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

_DEFAULT_CHUNK_SIZE = 512       # maximum characters per chunk
_DEFAULT_CHUNK_OVERLAP = 64     # overlap between consecutive chunks
_DEFAULT_BOUNDARY_LOOKBACK = 80  # max chars to walk back looking for a word/sentence boundary

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

    allowed_entity_types = frozenset(
        enrichment_cfg.get("entity_allowed_types") or _DEFAULT_ALLOWED_ENTITY_TYPES
    )
    entity_min_length = int(
        enrichment_cfg.get("entity_min_length", _DEFAULT_ENTITY_MIN_LENGTH)
    )

    # 1. Section-aware chunking.
    chunks_text = chunk_content(content, sections, chunk_size, chunk_overlap, boundary_lookback)

    # 2. Compute embeddings.
    doc_embedding = compute_embedding(content, biencoder_model)
    chunk_records = _build_chunk_records(page_id, chunks_text, biencoder_model, sections)

    # 3. Named entity recognition.
    entities = extract_entities(
        content,
        allowed_types=allowed_entity_types,
        min_length=entity_min_length,
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
) -> list[dict[str, Any]]:
    """Split *content* into overlapping chunks, respecting section boundaries.

    If *sections* is empty, falls back to fixed-size character chunking with
    overlap. Chunk boundaries prefer paragraph > sentence > word breaks within
    *boundary_lookback* characters of the target end, so chunks rarely start or
    end mid-word.

    Returns a list of dicts with keys:
        text           — chunk plain text
        chunk_index    — positional index (0-based)
        section_heading — nearest heading above this chunk (or None)
    """
    if sections:
        return _section_aware_chunks(content, sections, chunk_size, overlap, boundary_lookback)
    return _fixed_size_chunks(content, chunk_size, overlap, boundary_lookback)


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
) -> list[dict[str, str]]:
    """Extract named entities from *text* using spaCy.

    en_core_web_sm emits the standard OntoNotes label set; *allowed_types*
    restricts the persisted subset (default: ORG, PERSON, GPE, LOC, DATE,
    MONEY, PERCENT, LAW, NORP, EVENT). CARDINAL, ORDINAL, QUANTITY, TIME,
    WORK_OF_ART, FAC, LANGUAGE, and PRODUCT are dropped as low-signal.

    Entities are further filtered to remove: strings shorter than
    *min_length*, URL fragments, and letters-followed-by-digits scraping
    artefacts (e.g. "review33"). Duplicates are removed by (text, type).

    Returns a list of dicts with keys: entity_text, entity_type.
    If spaCy is unavailable, returns an empty list and logs a warning.
    """
    nlp = _load_spacy()
    if nlp is None:
        return []

    allowed = frozenset(allowed_types) if allowed_types else _DEFAULT_ALLOWED_ENTITY_TYPES

    try:
        doc = nlp(text[:100_000])  # spaCy has a practical limit; truncate for safety
        seen: set[tuple[str, str]] = set()
        entities: list[dict[str, str]] = []
        for ent in doc.ents:
            entity_text = ent.text.strip()
            entity_type = ent.label_
            if not _is_valid_entity(entity_text, entity_type, allowed, min_length):
                continue
            key = (entity_text, entity_type)
            if key in seen:
                continue
            seen.add(key)
            entities.append({
                "entity_text": entity_text,
                "entity_type": entity_type,
            })
        return entities
    except Exception as exc:
        logger.warning("NER extraction failed: %s", exc)
        return []


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
