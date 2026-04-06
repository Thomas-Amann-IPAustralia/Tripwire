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

# YAKE extraction rate: one keyphrase per N words of text.
_YAKE_KEYPHRASES_PER_80_WORDS = 1
_YAKE_MIN_KEYPHRASES = 5
_YAKE_MAX_KEYPHRASES = 15
_YAKE_LANGUAGE = "en"
_YAKE_MAX_NGRAM_SIZE = 3
_YAKE_DEDUPLICATION_THRESHOLD = 0.9


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
    yake_params = get(config, "relevance_scoring", "yake", default={})

    # 1. Section-aware chunking.
    chunks_text = chunk_content(content, sections)

    # 2. Compute embeddings.
    doc_embedding = compute_embedding(content, biencoder_model)
    chunk_records = _build_chunk_records(page_id, chunks_text, biencoder_model, sections)

    # 3. Named entity recognition.
    entities = extract_entities(content)

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
) -> list[dict[str, Any]]:
    """Split *content* into overlapping chunks, respecting section boundaries.

    If *sections* is empty, falls back to fixed-size character chunking with
    overlap.

    Returns a list of dicts with keys:
        text           — chunk plain text
        chunk_index    — positional index (0-based)
        section_heading — nearest heading above this chunk (or None)
    """
    if sections:
        return _section_aware_chunks(content, sections, chunk_size, overlap)
    return _fixed_size_chunks(content, chunk_size, overlap)


def _section_aware_chunks(
    content: str,
    sections: list[dict[str, Any]],
    chunk_size: int,
    overlap: int,
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
            for sub in _fixed_size_chunks(segment, chunk_size, overlap):
                sub["section_heading"] = heading
                sub["chunk_index"] = idx
                chunks.append(sub)
                idx += 1

    return chunks


def _fixed_size_chunks(
    text: str,
    chunk_size: int,
    overlap: int,
) -> list[dict[str, Any]]:
    """Split *text* into fixed-size character chunks with *overlap*."""
    if not text.strip():
        return []

    chunks = []
    start = 0
    idx = 0
    while start < len(text):
        end = start + chunk_size
        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append({
                "text": chunk_text,
                "chunk_index": idx,
                "section_heading": None,
            })
            idx += 1
        start = end - overlap
        if start >= len(text):
            break

    return chunks


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
            import numpy as np
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


def extract_entities(text: str) -> list[dict[str, str]]:
    """Extract named entities from *text* using spaCy.

    Entity types stored (superset of what the plan specifies):
      LEGISLATION, ORG, SECTION, DATE, MONEY, PERCENT, LAW, GPE, PERSON

    Returns a list of dicts with keys: entity_text, entity_type.
    If spaCy is unavailable, returns an empty list and logs a warning.
    """
    nlp = _load_spacy()
    if nlp is None:
        return []

    try:
        doc = nlp(text[:100_000])  # spaCy has a practical limit; truncate for safety
        seen: set[tuple[str, str]] = set()
        entities: list[dict[str, str]] = []
        for ent in doc.ents:
            key = (ent.text.strip(), ent.label_)
            if key not in seen and ent.text.strip():
                seen.add(key)
                entities.append({
                    "entity_text": ent.text.strip(),
                    "entity_type": ent.label_,
                })
        return entities
    except Exception as exc:
        logger.warning("NER extraction failed: %s", exc)
        return []


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
    """Extract keyphrases from *text* using YAKE.

    The number of keyphrases extracted scales with text length:
      n = max(min_keyphrases, min(max_keyphrases, words // 80))

    Returns a list of dicts with keys: keyphrase, score.
    Lower YAKE scores indicate higher relevance.
    """
    params = yake_params or {}
    per_80 = int(params.get("keyphrases_per_80_words", _YAKE_KEYPHRASES_PER_80_WORDS))
    min_kp = int(params.get("min_keyphrases", _YAKE_MIN_KEYPHRASES))
    max_kp = int(params.get("max_keyphrases", _YAKE_MAX_KEYPHRASES))

    word_count = len(text.split())
    n = max(min_kp, min(max_kp, word_count // 80 * per_80))

    try:
        import yake as yake_lib
        kw_extractor = yake_lib.KeywordExtractor(
            lan=_YAKE_LANGUAGE,
            n=_YAKE_MAX_NGRAM_SIZE,
            dedupLim=_YAKE_DEDUPLICATION_THRESHOLD,
            top=n,
        )
        keywords = kw_extractor.extract_keywords(text)
        return [{"keyphrase": kw, "score": score} for kw, score in keywords]
    except ImportError:
        logger.warning(
            "yake not installed. Keyphrase extraction will be skipped. "
            "Install with: pip install yake"
        )
        return []
    except Exception as exc:
        logger.warning("Keyphrase extraction failed: %s", exc)
        return []
