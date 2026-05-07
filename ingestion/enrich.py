"""
ingestion/enrich.py

Step 4 of the IPFR ingestion pipeline: enrich a scraped IPFR page with
precomputed assets consumed by the Tripwire main pipeline.

Enrichment produces (per Section 4.1 of the system plan):
  - Document-level embedding  (BAAI/bge-base-en-v1.5)
  - Section-aware content chunks
  - Chunk-level embeddings    (BAAI/bge-base-en-v1.5)
  - Named entity inventory    (spaCy NER)
  - Keyphrase extraction      (YAKE)
  - Section-level metadata    (from trafilatura XML / heading boundaries)

All model loading is lazy: models are imported and initialised on first use
so that tests can run without heavy ML dependencies installed.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Chunk configuration
# ---------------------------------------------------------------------------

# Defaults are sized for BAAI/bge-base-en-v1.5 (512-token ceiling).  English
# prose averages roughly 4 characters per token, so 1400 chars ≈ 350 tokens —
# well under the model's context limit while giving each embedding ~3–4× the
# semantic payload of the previous 512-char setting.  Chunking still runs in
# character space for speed and deterministic test behaviour; tokeniser-backed
# accounting can be enabled via `ingestion.chunking.units: "tokens"`.
_DEFAULT_CHUNK_SIZE = 1400      # ≈ 350 tokens
_DEFAULT_CHUNK_OVERLAP = 200    # ≈ 50 tokens
_DEFAULT_BOUNDARY_LOOKBACK = 160  # max chars to walk back for a clean break
_DEFAULT_CHUNK_MIN_SIZE = 200   # tail chunks smaller than this merge into the predecessor
_DEFAULT_CHARS_PER_TOKEN = 4    # char→token ratio when units == "tokens"

# YAKE extraction rate: one keyphrase per N words of text.
_YAKE_KEYPHRASES_PER_80_WORDS = 1
_YAKE_MIN_KEYPHRASES = 5
_YAKE_MAX_KEYPHRASES = 15
_YAKE_LANGUAGE = "en"
_YAKE_MAX_NGRAM_SIZE = 3
_YAKE_DEDUPLICATION_THRESHOLD = 0.7

# Default post-extraction filters.
_DEFAULT_KEYPHRASE_MIN_LENGTH = 3

# Minimal English stopword set used to drop single-word keyphrases that slip
# through YAKE's own filtering. Deliberately conservative — single-word phrases
# that are meaningful domain terms (e.g. "copyright") are not in this set.
_KEYPHRASE_STOPWORDS: frozenset[str] = frozenset({
    "the", "and", "for", "with", "you", "your", "this", "that", "from", "will",
    "have", "has", "are", "was", "were", "any", "all", "can", "may", "not",
    "but", "our", "its", "who", "how", "what", "when", "where", "which", "also",
    "more", "most", "some", "such", "other", "their", "there", "these", "those",
    "about", "into", "over", "than", "then", "them", "been", "being", "would",
    "should", "could",
})

# spaCy entity types retained in the DB. Others (CARDINAL, ORDINAL, QUANTITY,
# TIME, WORK_OF_ART, LANGUAGE, FAC, PRODUCT) are dropped as low-signal for
# Tripwire's downstream use.
_DEFAULT_ALLOWED_ENTITY_TYPES: frozenset[str] = frozenset({
    "ORG", "PERSON", "GPE", "LOC", "DATE", "MONEY",
    "PERCENT", "LAW", "NORP", "EVENT",
})

_DEFAULT_ENTITY_MIN_LENGTH = 3

# Regex patterns used to flag scraping-artefact entity strings.
_URLISH_RE = re.compile(r"https?://|://|www\.|\.(?:gov|com|org|net|edu)\b",
                        re.IGNORECASE)
_LETTERS_THEN_DIGITS_RE = re.compile(r"[A-Za-z]\d{2,}")  # e.g. "review33"
_SENTENCE_END_RE = re.compile(r"[.!?][\"')\]]?\s")


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def enrich_page(
    page_id: str,
    content: str,
    sections: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Enrich a single IPFR page and return all precomputed assets.

    Parameters
    ----------
    page_id:
        The IPFR content identifier (e.g. "B1012").
    content:
        Normalised plain-text content of the page.
    sections:
        Section metadata list from scrape_ipfr.extract_sections.
    config:
        Validated configuration dict (used for model names and YAKE params).

    Returns
    -------
    dict with keys:
        doc_embedding  — bytes (serialised numpy float32 array)
        chunks         — list of chunk dicts
        entities       — list of entity dicts
        keyphrases     — list of keyphrase dicts
        sections       — list of section dicts (same as input, for DB write)
    """
    from src.config import get

    biencoder_model = get(config, "semantic_scoring", "biencoder", "model",
                          default="BAAI/bge-base-en-v1.5")
    yake_params = dict(get(config, "relevance_scoring", "yake", default={}) or {})
    enrichment_cfg = get(config, "ingestion", "enrichment", default={}) or {}
    chunking_cfg = get(config, "ingestion", "chunking", default={}) or {}

    yake_params.setdefault(
        "dedup_threshold",
        enrichment_cfg.get("yake_dedup_threshold", _YAKE_DEDUPLICATION_THRESHOLD),
    )
    yake_params["blocklist"] = enrichment_cfg.get("keyphrase_blocklist", []) or []
    yake_params["min_length"] = int(
        enrichment_cfg.get("keyphrase_min_length", _DEFAULT_KEYPHRASE_MIN_LENGTH)
    )

    chunk_size = int(chunking_cfg.get("chunk_size", _DEFAULT_CHUNK_SIZE))
    chunk_overlap = int(chunking_cfg.get("chunk_overlap", _DEFAULT_CHUNK_OVERLAP))
    boundary_lookback = int(chunking_cfg.get("boundary_lookback", _DEFAULT_BOUNDARY_LOOKBACK))
    chunk_min_size = int(chunking_cfg.get("chunk_min_size", _DEFAULT_CHUNK_MIN_SIZE))
    units = str(chunking_cfg.get("units", "chars")).lower()
    if units == "tokens":
        chars_per_token = int(chunking_cfg.get("chars_per_token", _DEFAULT_CHARS_PER_TOKEN))
        chunk_size *= chars_per_token
        chunk_overlap *= chars_per_token
        boundary_lookback *= chars_per_token
        chunk_min_size *= chars_per_token

    allowed_entity_types = frozenset(
        enrichment_cfg.get("entity_allowed_types") or _DEFAULT_ALLOWED_ENTITY_TYPES
    )
    entity_min_length = int(
        enrichment_cfg.get("entity_min_length", _DEFAULT_ENTITY_MIN_LENGTH)
    )
    entity_alias_map = _build_alias_map(enrichment_cfg.get("entity_aliases"))

    # 1. Section-aware chunking (with tail coalescing + heading dedup).
    chunks_text = chunk_content(
        content,
        sections,
        chunk_size,
        chunk_overlap,
        boundary_lookback,
        chunk_min_size=chunk_min_size,
    )

    # 2. Compute embeddings.
    doc_embedding = compute_embedding(content, biencoder_model)
    chunk_records = _build_chunk_records(page_id, chunks_text, biencoder_model, sections)

    # 3. Named entity recognition.
    entities = extract_entities(
        content,
        allowed_types=allowed_entity_types,
        min_length=entity_min_length,
        alias_map=entity_alias_map,
    )

    # 4. Keyphrase extraction.
    keyphrases = extract_keyphrases(content, yake_params)

    return {
        "doc_embedding": doc_embedding,
        "chunks": chunk_records,
        "entities": entities,
        "keyphrases": keyphrases,
        "sections": sections,
    }


# ---------------------------------------------------------------------------
# Section-aware chunking
# ---------------------------------------------------------------------------


def chunk_content(
    content: str,
    sections: list[dict[str, Any]],
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    overlap: int = _DEFAULT_CHUNK_OVERLAP,
    boundary_lookback: int = _DEFAULT_BOUNDARY_LOOKBACK,
    chunk_min_size: int = _DEFAULT_CHUNK_MIN_SIZE,
) -> list[dict[str, Any]]:
    """Split *content* into overlapping chunks, respecting section boundaries.

    If *sections* is empty, falls back to fixed-size character chunking with
    overlap. Chunk boundaries prefer paragraph > sentence > word breaks within
    *boundary_lookback* characters of the target end, so chunks rarely start or
    end mid-word.

    Trailing chunks shorter than *chunk_min_size* are merged into the preceding
    chunk from the same section, preventing the fragmented "tail" chunks that
    arise when a section segment's length is just over a chunk boundary.

    If a chunk's plain text begins with its section heading, the heading is
    stripped from the chunk body — it is already carried in the
    ``section_heading`` column and the duplication wastes embedding capacity.

    Returns a list of dicts with keys:
        text           — chunk plain text
        chunk_index    — positional index (0-based)
        section_heading — nearest heading above this chunk (or None)
    """
    if sections:
        chunks = _section_aware_chunks(content, sections, chunk_size, overlap, boundary_lookback)
    else:
        chunks = _fixed_size_chunks(content, chunk_size, overlap, boundary_lookback)
    chunks = _coalesce_tail_chunks(chunks, chunk_min_size, chunk_size)
    chunks = _strip_leading_heading(chunks)
    return chunks


def _section_aware_chunks(
    content: str,
    sections: list[dict[str, Any]],
    chunk_size: int,
    overlap: int,
    boundary_lookback: int,
) -> list[dict[str, Any]]:
    """Produce chunks that don't cross heading boundaries where possible."""
    # Build boundary list: start positions of each section.
    boundaries = sorted({s["char_start"] for s in sections} | {0, len(content)})

    # Map each boundary start to the nearest heading.
    heading_at: dict[int, str] = {}
    for s in sections:
        heading_at[s["char_start"]] = s["heading_text"]

    chunks: list[dict[str, Any]] = []
    idx = 0

    for i in range(len(boundaries) - 1):
        seg_start = boundaries[i]
        seg_end = boundaries[i + 1]
        segment = content[seg_start:seg_end]
        heading = heading_at.get(seg_start)

        # If segment fits in one chunk, emit it directly.
        if len(segment) <= chunk_size:
            if segment.strip():
                chunks.append({
                    "text": segment.strip(),
                    "chunk_index": idx,
                    "section_heading": heading,
                })
                idx += 1
        else:
            # Sub-chunk with overlap.
            for sub in _fixed_size_chunks(segment, chunk_size, overlap, boundary_lookback):
                sub["section_heading"] = heading
                sub["chunk_index"] = idx
                chunks.append(sub)
                idx += 1

    return chunks


def _coalesce_tail_chunks(
    chunks: list[dict[str, Any]],
    min_size: int,
    max_size: int,
) -> list[dict[str, Any]]:
    """Merge undersized tail chunks into their predecessor when it's safe to do so.

    A chunk is merged if:
      * it is shorter than *min_size*, AND
      * the predecessor shares its ``section_heading`` (so we don't cross section
        boundaries), AND
      * the combined length stays within 1.5 × *max_size* (keeps embeddings below
        the bi-encoder's token ceiling even with the char-to-token estimate).

    Chunk indices are re-numbered after coalescing so the sequence is dense.
    """
    if not chunks:
        return chunks

    merged: list[dict[str, Any]] = []
    ceiling = int(max_size * 1.5)

    for chunk in chunks:
        if (
            merged
            and len(chunk["text"]) < min_size
            and merged[-1].get("section_heading") == chunk.get("section_heading")
            and len(merged[-1]["text"]) + 1 + len(chunk["text"]) <= ceiling
        ):
            merged[-1]["text"] = (merged[-1]["text"].rstrip() + "\n" + chunk["text"].lstrip()).strip()
            continue
        merged.append(dict(chunk))

    for new_idx, chunk in enumerate(merged):
        chunk["chunk_index"] = new_idx

    return merged


def _strip_leading_heading(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove a chunk's section heading from the start of its text, if present.

    Trafilatura renders headings into the plain-text stream, so section-aware
    chunks often begin with their own heading (already captured in
    ``section_heading``).  Dropping the duplicate reclaims embedding capacity
    without losing context.
    """
    for chunk in chunks:
        heading = chunk.get("section_heading")
        if not heading:
            continue
        heading = heading.strip()
        if not heading:
            continue
        stripped = chunk["text"].lstrip()
        if stripped.startswith(heading):
            remainder = stripped[len(heading):].lstrip(" \t\n\r-–—:.")
            if remainder:
                chunk["text"] = remainder
    return chunks


def _fixed_size_chunks(
    text: str,
    chunk_size: int,
    overlap: int,
    boundary_lookback: int = _DEFAULT_BOUNDARY_LOOKBACK,
) -> list[dict[str, Any]]:
    """Split *text* into fixed-size character chunks with *overlap*.

    The splitter walks back up to *boundary_lookback* characters from each
    target end position to find a paragraph, sentence, or word break so that
    chunks don't start or end mid-word. The overlap start is similarly snapped
    forward to the next word boundary.
    """
    if not text.strip():
        return []

    n = len(text)
    lookback = max(0, min(boundary_lookback, chunk_size // 2))

    chunks: list[dict[str, Any]] = []
    start = 0
    idx = 0
    while start < n:
        target_end = min(start + chunk_size, n)

        if target_end < n:
            boundary = _find_boundary_backwards(text, start, target_end, lookback)
            end = boundary if boundary > start else target_end
        else:
            end = target_end

        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append({
                "text": chunk_text,
                "chunk_index": idx,
                "section_heading": None,
            })
            idx += 1

        if end >= n:
            break

        next_start = max(start + 1, end - overlap)
        next_start = _advance_to_word_start(text, next_start)
        if next_start <= start:  # guarantee forward progress
            next_start = end
        start = next_start

    return chunks


def _find_boundary_backwards(
    text: str, start: int, end: int, max_lookback: int,
) -> int:
    """Return a boundary index in (start, end] that falls on a clean break.

    Preference order: paragraph break > sentence end > whitespace.
    Returns *end* when no boundary is found within the lookback window.
    """
    if max_lookback <= 0:
        return end
    lookback_start = max(start + 1, end - max_lookback)
    window = text[lookback_start:end]

    para = window.rfind("\n\n")
    if para != -1:
        return lookback_start + para + 2

    last_sent = -1
    for match in _SENTENCE_END_RE.finditer(window):
        last_sent = match.end()
    if last_sent != -1:
        return lookback_start + last_sent

    pos = max(window.rfind(" "), window.rfind("\n"), window.rfind("\t"))
    if pos != -1:
        return lookback_start + pos + 1

    return end


def _advance_to_word_start(text: str, pos: int) -> int:
    """Advance *pos* forward to the nearest word-start character.

    If *pos* lands mid-word, skip to the end of that word; then skip any
    non-alphanumeric characters (whitespace, punctuation) so the returned
    position sits on the first letter/digit of a new word.
    """
    n = len(text)
    if pos <= 0 or pos >= n:
        return pos
    if text[pos - 1].isalnum() and text[pos].isalnum():
        while pos < n and text[pos].isalnum():
            pos += 1
    while pos < n and not text[pos].isalnum():
        pos += 1
    return pos


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


_biencoder_cache: dict[str, Any] = {}


def compute_embedding(text: str, model_name: str) -> bytes | None:
    """Encode *text* with the bi-encoder and return raw bytes (float32 array).

    Returns None if the model cannot be loaded (e.g. in tests without GPU/deps).
    The returned bytes can be stored in SQLite as a BLOB and reconstructed
    with: ``numpy.frombuffer(blob, dtype=numpy.float32)``
    """
    model = _load_biencoder(model_name)
    if model is None:
        return None

    try:
        import numpy as np
        embedding = model.encode(text, normalize_embeddings=True)
        return np.array(embedding, dtype=np.float32).tobytes()
    except Exception as exc:
        logger.warning("Embedding failed for text (len=%d): %s", len(text), exc)
        return None


def _load_biencoder(model_name: str) -> Any:
    """Lazily load and cache the bi-encoder model."""
    if model_name in _biencoder_cache:
        return _biencoder_cache[model_name]

    try:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading bi-encoder model: %s", model_name)
        model = SentenceTransformer(model_name)
        _biencoder_cache[model_name] = model
        return model
    except ImportError:
        logger.warning(
            "sentence-transformers not installed. Embeddings will be skipped. "
            "Install with: pip install sentence-transformers"
        )
        _biencoder_cache[model_name] = None
        return None
    except Exception as exc:
        logger.error("Failed to load bi-encoder model %s: %s", model_name, exc)
        _biencoder_cache[model_name] = None
        return None


def _build_chunk_records(
    page_id: str,
    chunks_text: list[dict[str, Any]],
    model_name: str,
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Encode each chunk and build the full chunk record for DB insertion."""
    records = []
    for chunk in chunks_text:
        chunk_id = f"{page_id}-chunk-{chunk['chunk_index']:03d}"
        embedding = compute_embedding(chunk["text"], model_name)
        if embedding is None:
            # Still store the chunk — embedding may be computed later.
            embedding = bytes(0)  # empty placeholder
        records.append({
            "chunk_id": chunk_id,
            "page_id": page_id,
            "chunk_text": chunk["text"],
            "chunk_index": chunk["chunk_index"],
            "section_heading": chunk.get("section_heading"),
            "chunk_embedding": embedding,
        })
    return records


# ---------------------------------------------------------------------------
# Named Entity Recognition
# ---------------------------------------------------------------------------


_spacy_model_cache: dict[str, Any] = {}


def extract_entities(
    text: str,
    allowed_types: frozenset[str] | set[str] | None = None,
    min_length: int = _DEFAULT_ENTITY_MIN_LENGTH,
    alias_map: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Extract named entities from *text* using spaCy.

    en_core_web_sm emits the standard OntoNotes label set; *allowed_types*
    restricts the persisted subset (default: ORG, PERSON, GPE, LOC, DATE,
    MONEY, PERCENT, LAW, NORP, EVENT). CARDINAL, ORDINAL, QUANTITY, TIME,
    WORK_OF_ART, FAC, LANGUAGE, and PRODUCT are dropped as low-signal.

    Post-extraction:
      * Trailing possessive ``'s`` / ``’s`` is stripped.
      * Surface forms are canonicalised via *alias_map* (keys are matched
        case-insensitively; the mapped value is the persisted form).
      * Case-folded duplicates collapse to the first-seen surface form.
      * URL fragments and letters-followed-by-digits scraping artefacts are
        dropped.

    Returns a list of dicts with keys: entity_text, entity_type.
    If spaCy is unavailable, returns an empty list and logs a warning.
    """
    nlp = _load_spacy()
    if nlp is None:
        return []

    allowed = frozenset(allowed_types) if allowed_types else _DEFAULT_ALLOWED_ENTITY_TYPES
    alias_map = alias_map or {}

    try:
        doc = nlp(text[:100_000])  # spaCy has a practical limit; truncate for safety
    except Exception as exc:
        logger.warning("NER extraction failed: %s", exc)
        return []

    seen: dict[tuple[str, str], str] = {}
    ordered: list[tuple[str, str]] = []
    for ent in doc.ents:
        entity_text = canonicalise_entity(ent.text, alias_map)
        entity_type = ent.label_
        if not _is_valid_entity(entity_text, entity_type, allowed, min_length):
            continue
        key = (entity_text.casefold(), entity_type)
        if key in seen:
            continue
        seen[key] = entity_text
        ordered.append(key)

    return [
        {"entity_text": seen[key], "entity_type": key[1]}
        for key in ordered
    ]


# Trailing possessive endings stripped before dedup / alias lookup.
_POSSESSIVE_SUFFIXES = ("'s", "\u2019s", "'S", "\u2019S")


def canonicalise_entity(text: str, alias_map: dict[str, str] | None = None) -> str:
    """Normalise an entity string for dedup and alias resolution.

    Strips whitespace and trailing possessive forms, collapses internal runs of
    whitespace, and applies *alias_map* (case-insensitive keys).
    """
    cleaned = " ".join(text.split()).strip()
    for suffix in _POSSESSIVE_SUFFIXES:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].rstrip()
            break
    if alias_map:
        mapped = alias_map.get(cleaned.casefold())
        if mapped:
            cleaned = mapped
    return cleaned


def _build_alias_map(raw: Any) -> dict[str, str]:
    """Normalise config entity aliases into a case-insensitive lookup table.

    Accepted forms:
        {"IPTA": "Institute of Patent and Trade Mark Attorneys Australia"}
        [{"canonical": "X", "aliases": ["x", "X Co"]}, ...]
    """
    if not raw:
        return {}
    lookup: dict[str, str] = {}
    if isinstance(raw, dict):
        for alias, canonical in raw.items():
            if isinstance(alias, str) and isinstance(canonical, str):
                lookup[alias.strip().casefold()] = canonical.strip()
    elif isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            canonical = entry.get("canonical")
            if not isinstance(canonical, str) or not canonical.strip():
                continue
            canonical = canonical.strip()
            lookup[canonical.casefold()] = canonical
            for alias in entry.get("aliases", []) or []:
                if isinstance(alias, str) and alias.strip():
                    lookup[alias.strip().casefold()] = canonical
    return lookup


def _is_valid_entity(
    text: str, entity_type: str, allowed: frozenset[str], min_length: int,
) -> bool:
    """Return True if the entity passes post-NER quality filters."""
    if entity_type not in allowed:
        return False
    if len(text) < min_length:
        return False
    if text.isnumeric():
        return False
    if _URLISH_RE.search(text):
        return False
    if _LETTERS_THEN_DIGITS_RE.search(text):
        return False
    return True


def _load_spacy() -> Any:
    if "model" in _spacy_model_cache:
        return _spacy_model_cache["model"]

    try:
        import spacy
        try:
            nlp = spacy.load("en_core_web_sm")
        except OSError:
            logger.warning(
                "spaCy model 'en_core_web_sm' not found. "
                "Install with: python -m spacy download en_core_web_sm"
            )
            _spacy_model_cache["model"] = None
            return None
        _spacy_model_cache["model"] = nlp
        return nlp
    except ImportError:
        logger.warning(
            "spaCy not installed. NER will be skipped. "
            "Install with: pip install spacy"
        )
        _spacy_model_cache["model"] = None
        return None


# ---------------------------------------------------------------------------
# Keyphrase extraction (YAKE)
# ---------------------------------------------------------------------------


def extract_keyphrases(text: str, yake_params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Extract keyphrases from *text* using YAKE and apply quality filters.

    The number of keyphrases extracted scales with text length:
      n = max(min_keyphrases, min(max_keyphrases, words // 80))

    After YAKE runs, phrases are dropped if they are shorter than *min_length*,
    consist of a single stopword, are purely numeric, or appear in *blocklist*
    (case-insensitive).

    Supported *yake_params* keys:
        keyphrases_per_80_words, min_keyphrases, max_keyphrases
        dedup_threshold   — YAKE dedupLim (default 0.7)
        min_length        — drop phrases shorter than this (default 3)
        blocklist         — iterable of phrases to drop (site chrome, etc.)

    Returns a list of dicts with keys: keyphrase, score.
    Lower YAKE scores indicate higher relevance.
    """
    params = yake_params or {}
    per_80 = int(params.get("keyphrases_per_80_words", _YAKE_KEYPHRASES_PER_80_WORDS))
    min_kp = int(params.get("min_keyphrases", _YAKE_MIN_KEYPHRASES))
    max_kp = int(params.get("max_keyphrases", _YAKE_MAX_KEYPHRASES))
    dedup_threshold = float(params.get("dedup_threshold", _YAKE_DEDUPLICATION_THRESHOLD))
    min_length = int(params.get("min_length", _DEFAULT_KEYPHRASE_MIN_LENGTH))
    blocklist = frozenset(p.strip().lower() for p in (params.get("blocklist") or []) if p)

    word_count = len(text.split())
    # Pull extra candidates so the filter has room to discard noise without
    # starving downstream BM25.
    n = max(min_kp, min(max_kp, word_count // 80 * per_80))
    pool_size = max(n * 3, min_kp * 2)

    try:
        import yake as yake_lib
        kw_extractor = yake_lib.KeywordExtractor(
            lan=_YAKE_LANGUAGE,
            n=_YAKE_MAX_NGRAM_SIZE,
            dedupLim=dedup_threshold,
            top=pool_size,
        )
        keywords = kw_extractor.extract_keywords(text)
    except ImportError:
        logger.warning(
            "yake not installed. Keyphrase extraction will be skipped. "
            "Install with: pip install yake"
        )
        return []
    except Exception as exc:
        logger.warning("Keyphrase extraction failed: %s", exc)
        return []

    kept: list[dict[str, Any]] = []
    for kw, score in keywords:
        if _is_valid_keyphrase(kw, min_length, blocklist):
            kept.append({"keyphrase": kw, "score": score})
        if len(kept) >= n:
            break
    return kept


def _is_valid_keyphrase(
    phrase: str, min_length: int, blocklist: frozenset[str],
) -> bool:
    """Return True if the phrase passes post-YAKE quality filters."""
    phrase = phrase.strip()
    if len(phrase) < min_length:
        return False
    if phrase.lower() in blocklist:
        return False
    tokens = phrase.split()
    if not tokens:
        return False
    # Single-token phrases: drop if they are stopwords or purely numeric.
    if len(tokens) == 1:
        lower = tokens[0].lower()
        if lower in _KEYPHRASE_STOPWORDS:
            return False
        if lower.isnumeric():
            return False
    return True
