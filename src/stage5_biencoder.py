"""
src/stage5_biencoder.py

Stage 5 — Semantic Matching: Bi-Encoder (Section 3.5)

Purpose: identify which IPFR pages are most likely affected by the change
using a coarse-grained bi-encoder pass.

Process:
  1. Chunk the incoming change document using the same strategy as ingestion.
  2. Encode each chunk with BAAI/bge-base-en-v1.5.
  3. Compute cosine similarity between each change chunk and every precomputed
     IPFR chunk embedding stored in the SQLite database.
  4. For each IPFR page, record the highest single-chunk cosine score and the
     count of chunks exceeding the low-medium threshold.

Decision rule — an IPFR page is a candidate if:
  • Any single chunk scores >= high_threshold (default: 0.75), OR
  • >= low_medium_min_chunks chunks score >= low_medium_threshold (default: 0.45)

Observation mode: distributions of max_chunk_score and chunk counts are
captured in observation_data for logging.

Model loading: the bi-encoder is loaded lazily.  The caller (pipeline
orchestrator) is responsible for releasing the model before loading the
cross-encoder (see Section 7.4 of the system plan).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Default chunking parameters — mirror the ingestion pipeline defaults.
_DEFAULT_CHUNK_SIZE = 512
_DEFAULT_CHUNK_OVERLAP = 64

# Lazy model cache.
_biencoder_cache: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ChunkScore:
    """Cosine similarity between one change chunk and one corpus chunk."""

    chunk_id: str
    """Corpus chunk ID (e.g. 'B1012-chunk-003')."""
    score: float


@dataclass
class PageBiEncoderResult:
    """Bi-encoder results aggregated per IPFR page."""

    page_id: str
    max_chunk_score: float
    """Highest cosine similarity across all chunk pairs."""
    chunks_above_low_medium: int
    """Count of corpus chunks scoring >= low_medium_threshold."""
    trigger_reason: str | None
    """'single_chunk_high' | 'multi_chunk_medium' | None (not a candidate)."""
    top_chunk_scores: list[ChunkScore] = field(default_factory=list)
    """Top-5 chunk scores for logging."""


@dataclass
class BiEncoderResult:
    """Output of Stage 5."""

    candidate_pages: list[PageBiEncoderResult]
    """Pages that satisfied the decision rule and proceed to Stage 6."""
    all_pages: list[PageBiEncoderResult]
    """All scored pages, sorted by max_chunk_score descending."""
    observation_data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_biencoder(
    change_text: str,
    conn: sqlite3.Connection,
    config: dict[str, Any],
    model: Any | None = None,
) -> BiEncoderResult:
    """Run Stage 5 bi-encoder scoring.

    Parameters
    ----------
    change_text:
        Normalised change document (diff, explainer, or RSS content) from
        Stage 3.
    conn:
        Open SQLite connection to the IPFR corpus database.
    config:
        Validated configuration dict.
    model:
        Pre-loaded SentenceTransformer model, or None to load lazily.

    Returns
    -------
    BiEncoderResult
    """
    from src.config import get as cfg_get

    bi_cfg = config.get("semantic_scoring", {}).get("biencoder", {})
    model_name: str = bi_cfg.get("model", "BAAI/bge-base-en-v1.5")
    high_threshold: float = float(bi_cfg.get("high_threshold", 0.75))
    low_medium_threshold: float = float(bi_cfg.get("low_medium_threshold", 0.45))
    low_medium_min_chunks: int = int(bi_cfg.get("low_medium_min_chunks", 3))

    # ---- 1. Chunk the change document -----------------------------------
    change_chunks = _chunk_text(change_text, _DEFAULT_CHUNK_SIZE, _DEFAULT_CHUNK_OVERLAP)
    if not change_chunks:
        logger.warning("Stage 5: Change text produced no chunks — empty content?")
        return BiEncoderResult(
            candidate_pages=[],
            all_pages=[],
            observation_data={"warning": "no_change_chunks"},
        )

    # ---- 2. Encode change chunks ----------------------------------------
    encoder = model if model is not None else _load_biencoder(model_name)
    change_embeddings = _encode_texts(change_chunks, encoder)

    if change_embeddings is None:
        logger.warning("Stage 5: Encoding failed — no candidates produced.")
        return BiEncoderResult(
            candidate_pages=[],
            all_pages=[],
            observation_data={"warning": "encoding_failed"},
        )

    # ---- 3. Load corpus chunks from DB ----------------------------------
    corpus_chunks = _load_corpus_chunks(conn)
    if not corpus_chunks:
        logger.warning("Stage 5: No corpus chunks found in database.")
        return BiEncoderResult(
            candidate_pages=[],
            all_pages=[],
            observation_data={"warning": "empty_corpus_chunks"},
        )

    # ---- 4. Compute similarity and aggregate per page -------------------
    page_results = _score_pages(
        change_embeddings, corpus_chunks, high_threshold, low_medium_threshold,
        low_medium_min_chunks,
    )

    # Sort all pages by max_chunk_score descending.
    page_results.sort(key=lambda r: r.max_chunk_score, reverse=True)

    candidates = [r for r in page_results if r.trigger_reason is not None]

    observation_data = _build_observation_data(page_results, change_chunks)

    return BiEncoderResult(
        candidate_pages=candidates,
        all_pages=page_results,
        observation_data=observation_data,
    )


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def _chunk_text(
    text: str,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    overlap: int = _DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Split text into fixed-size character chunks with overlap.

    Mirrors the fixed-size fallback path in ingestion/enrich.py so that
    change document chunk sizes are comparable to corpus chunk sizes.
    """
    if not text.strip():
        return []

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap
        if start >= len(text):
            break
    return chunks


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


def _encode_texts(texts: list[str], model: Any) -> Any:
    """Encode a list of texts and return a numpy float32 array.

    Returns None if encoding fails or model is unavailable.
    Each row is a normalised embedding vector.
    """
    if model is None:
        return None

    try:
        import numpy as np

        embeddings = model.encode(texts, normalize_embeddings=True, batch_size=32)
        return np.array(embeddings, dtype=np.float32)
    except Exception as exc:
        logger.error("Stage 5: Encoding %d texts failed: %s", len(texts), exc)
        return None


def _load_biencoder(model_name: str) -> Any:
    """Lazily load and cache the bi-encoder model."""
    if model_name in _biencoder_cache:
        return _biencoder_cache[model_name]

    try:
        from sentence_transformers import SentenceTransformer

        logger.info("Stage 5: Loading bi-encoder model: %s", model_name)
        m = SentenceTransformer(model_name)
        _biencoder_cache[model_name] = m
        return m
    except ImportError:
        logger.warning(
            "Stage 5: sentence-transformers not installed. "
            "Install with: pip install sentence-transformers"
        )
        _biencoder_cache[model_name] = None
        return None
    except Exception as exc:
        logger.error("Stage 5: Failed to load bi-encoder %s: %s", model_name, exc)
        _biencoder_cache[model_name] = None
        return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _load_corpus_chunks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Load all corpus chunks with their embeddings and page_ids."""
    rows = conn.execute(
        "SELECT chunk_id, page_id, chunk_embedding FROM chunks ORDER BY page_id, chunk_index"
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Similarity computation
# ---------------------------------------------------------------------------


def _score_pages(
    change_embeddings: Any,
    corpus_chunks: list[dict[str, Any]],
    high_threshold: float,
    low_medium_threshold: float,
    low_medium_min_chunks: int,
) -> list[PageBiEncoderResult]:
    """Score all IPFR pages by aggregating chunk-level cosine similarities.

    For each IPFR page:
      - max_chunk_score: the highest cosine score across all change-chunk ×
        corpus-chunk pairs for that page.
      - chunks_above_low_medium: count of corpus chunks scoring >=
        low_medium_threshold (at least once, from any change chunk).
    """
    try:
        import numpy as np
    except ImportError:
        logger.error("Stage 5: numpy not available — cannot compute similarity.")
        return []

    # Group corpus chunks by page_id and decode embeddings.
    page_chunk_embeddings: dict[str, list[tuple[str, Any]]] = {}
    for chunk in corpus_chunks:
        page_id = chunk["page_id"]
        emb_bytes = chunk.get("chunk_embedding")
        if not emb_bytes:
            continue
        try:
            emb = np.frombuffer(emb_bytes, dtype=np.float32)
        except Exception:
            continue
        page_chunk_embeddings.setdefault(page_id, []).append(
            (chunk["chunk_id"], emb)
        )

    results: list[PageBiEncoderResult] = []

    for page_id, chunk_list in page_chunk_embeddings.items():
        # Stack corpus chunk embeddings for this page into a matrix.
        chunk_ids = [cid for cid, _ in chunk_list]
        chunk_matrix = np.stack([emb for _, emb in chunk_list])  # (M, D)

        # Compute cosine similarity: change_embeddings (N, D) × chunk_matrix.T (D, M)
        # Result: (N, M) — each entry is sim(change_chunk_i, corpus_chunk_j)
        sim_matrix = np.dot(change_embeddings, chunk_matrix.T)  # (N, M)

        # Max similarity per corpus chunk (best matching change chunk).
        max_per_corpus_chunk = sim_matrix.max(axis=0)  # (M,)

        max_score = float(max_per_corpus_chunk.max())
        chunks_above = int((max_per_corpus_chunk >= low_medium_threshold).sum())

        # Determine trigger reason.
        trigger_reason: str | None = None
        if max_score >= high_threshold:
            trigger_reason = "single_chunk_high"
        elif chunks_above >= low_medium_min_chunks:
            trigger_reason = "multi_chunk_medium"

        # Top-5 chunk scores for logging.
        top_indices = max_per_corpus_chunk.argsort()[::-1][:5]
        top_chunks = [
            ChunkScore(chunk_id=chunk_ids[i], score=float(max_per_corpus_chunk[i]))
            for i in top_indices
        ]

        results.append(
            PageBiEncoderResult(
                page_id=page_id,
                max_chunk_score=max_score,
                chunks_above_low_medium=chunks_above,
                trigger_reason=trigger_reason,
                top_chunk_scores=top_chunks,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Observation mode data
# ---------------------------------------------------------------------------


def _build_observation_data(
    page_results: list[PageBiEncoderResult],
    change_chunks: list[str],
) -> dict[str, Any]:
    """Collect score distributions for observation mode logging."""
    if not page_results:
        return {
            "stage": "stage5_biencoder",
            "change_chunk_count": len(change_chunks),
            "page_count": 0,
        }

    max_scores = [r.max_chunk_score for r in page_results]
    chunk_counts = [r.chunks_above_low_medium for r in page_results]
    candidate_count = sum(1 for r in page_results if r.trigger_reason is not None)

    return {
        "stage": "stage5_biencoder",
        "change_chunk_count": len(change_chunks),
        "page_count": len(page_results),
        "candidate_count": candidate_count,
        "distributions": {
            "max_chunk_score": _distribution(max_scores),
            "chunks_above_low_medium": _distribution(
                [float(c) for c in chunk_counts]
            ),
        },
        "top_pages": [
            {
                "page_id": r.page_id,
                "max_chunk_score": round(r.max_chunk_score, 6),
                "chunks_above_low_medium": r.chunks_above_low_medium,
                "trigger_reason": r.trigger_reason,
            }
            for r in page_results[:10]
        ],
    }


def _distribution(values: list[float]) -> dict[str, float]:
    """Return basic descriptive statistics for a list of floats."""
    if not values:
        return {}
    sorted_vals = sorted(values)
    n = len(sorted_vals)

    def _percentile(p: float) -> float:
        idx = (n - 1) * p
        lo = int(idx)
        hi = lo + 1
        if hi >= n:
            return round(sorted_vals[lo], 6)
        return round(
            sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo), 6
        )

    return {
        "min": round(sorted_vals[0], 6),
        "p25": _percentile(0.25),
        "median": _percentile(0.50),
        "p75": _percentile(0.75),
        "max": round(sorted_vals[-1], 6),
    }
