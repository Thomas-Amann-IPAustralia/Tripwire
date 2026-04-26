"""
src/stage4_relevance.py

Stage 4 — Relevance Scoring (Section 3.4)

Purpose: determine whether a change is potentially relevant to the IPFR corpus
and identify candidate IPFR pages before expensive semantic matching.

Two signals fused via weighted Reciprocal Rank Fusion:
  Signal 1: YAKE-driven BM25 (keyword relevance)       RRF weight: 1.0
  Signal 2: Bi-encoder cosine similarity (semantic)    RRF weight: 2.0

Fusion formula:
  RRF_score(page) = w_bm25 / (k + rank_bm25) + w_semantic / (k + rank_semantic)

Source importance multiplier applied after fusion:
  final_score = RRF_score × (floor + (1 - floor) × source_importance)

Fast-pass override: source_importance >= 1.0 bypasses candidate selection
entirely — all corpus pages proceed to Stage 5.

Candidate selection: top-N OR any page above min_score_threshold, plus any
fast-pass triggered page set.

Observation mode: score distributions are captured in observation_data and
written to the pipeline_runs details column when pipeline.observation_mode
is true.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# TODO(5.3): Threshold calibration — once 4–8 weeks of feedback data has been
# accumulated in data/logs/feedback.jsonl, use the feedback labels to calibrate
# min_score_threshold and rrf_weight_* via the grid-search approach in task 5.4.
# See docs/runbook-adjust-thresholds.md for the manual procedure in the interim.

# TODO(5.4): Grid search over relevance weights — evaluate alternative
# rrf_weight_bm25 / rrf_weight_semantic combinations against the accumulated
# feedback log. Implement as an offline evaluation script (not in this module).

# TODO(5.6): BM25 positional/proximity extensions — if standard BM25 proves
# insufficient for distinguishing close-proximity keyword matches from
# scattered matches, evaluate BM25+ or BM25L variants, or a positional BM25
# implementation. Requires live data to assess marginal value before adding
# complexity here.

# ---------------------------------------------------------------------------
# Lazy bi-encoder cache (shared within this module's process lifetime)
# ---------------------------------------------------------------------------

_biencoder_cache: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PageRelevanceScore:
    """Per-IPFR-page relevance scores produced by Stage 4."""

    page_id: str
    bm25_score: float
    bm25_rank: int          # 1-based; 1 = highest BM25 score
    semantic_score: float
    semantic_rank: int      # 1-based; 1 = highest cosine similarity
    rrf_score: float
    final_score: float      # RRF × source importance multiplier


@dataclass
class RelevanceResult:
    """Output of Stage 4 relevance scoring."""

    candidates: list[PageRelevanceScore]
    """Pages selected for Stage 5 (top-N + threshold + fast-pass)."""

    all_pages: list[PageRelevanceScore]
    """All scored pages, sorted by final_score descending."""

    fast_pass_triggered: bool
    """True when source_importance >= fast_pass.source_importance_min."""

    keyphrases: list[str]
    """YAKE keyphrases used as BM25 query terms."""

    observation_data: dict[str, Any] = field(default_factory=dict)
    """Score distributions and metadata for observation mode logging."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_relevance(
    diff_text: str,
    conn: sqlite3.Connection,
    config: dict[str, Any],
    source_importance: float = 0.5,
    ner_entities: list[str] | None = None,
) -> RelevanceResult:
    """Score the relevance of a change diff against the IPFR corpus.

    Parameters
    ----------
    diff_text:
        Normalised diff text from Stage 3.
    conn:
        Open SQLite connection to the IPFR corpus database.
    config:
        Validated configuration dict (from src.config.load_config).
    source_importance:
        Per-source importance score in [0.0, 1.0].
    ner_entities:
        NER entity strings from Stage 2 significance fingerprint. Used to
        supplement YAKE output for short diffs (< short_diff_word_threshold).

    Returns
    -------
    RelevanceResult
    """
    from src.config import get as cfg_get

    rs_cfg = config.get("relevance_scoring", {})
    rrf_k = int(rs_cfg.get("rrf_k", 60))
    w_bm25 = float(rs_cfg.get("rrf_weight_bm25", 1.0))
    w_sem = float(rs_cfg.get("rrf_weight_semantic", 2.0))
    top_n = int(rs_cfg.get("top_n_candidates", 5))
    min_score_threshold = rs_cfg.get("min_score_threshold")
    importance_floor = float(rs_cfg.get("source_importance_floor", 0.5))
    fast_pass_min = float(
        cfg_get(config, "relevance_scoring", "fast_pass", "source_importance_min",
                default=1.0)
    )

    biencoder_model_name = cfg_get(
        config, "semantic_scoring", "biencoder", "model",
        default="BAAI/bge-base-en-v1.5",
    )

    # ---- Fast-pass check ------------------------------------------------
    fast_pass_triggered = source_importance >= fast_pass_min

    # ---- 1. YAKE keyphrase extraction -----------------------------------
    yake_cfg = rs_cfg.get("yake", {})
    keyphrases = _extract_keyphrases(diff_text, yake_cfg, ner_entities or [])

    # ---- 2. Load all IPFR pages from corpus DB --------------------------
    pages = _load_pages(conn)
    if not pages:
        logger.warning("Stage 4: No pages found in IPFR corpus — skipping.")
        return RelevanceResult(
            candidates=[],
            all_pages=[],
            fast_pass_triggered=fast_pass_triggered,
            keyphrases=keyphrases,
            observation_data={"warning": "empty_corpus"},
        )

    page_contents = [p["content"] for p in pages]

    # ---- 3. BM25 keyword scoring ----------------------------------------
    bm25_scores = _bm25_score(keyphrases, page_contents)

    # ---- 4. Bi-encoder semantic scoring ---------------------------------
    semantic_scores = _semantic_score(diff_text, pages, biencoder_model_name)

    # ---- 5. Weighted RRF fusion -----------------------------------------
    page_scores = _fuse_rrf(pages, bm25_scores, semantic_scores, rrf_k, w_bm25, w_sem)

    # ---- 6. Source importance multiplier --------------------------------
    multiplier = importance_floor + (1.0 - importance_floor) * source_importance
    for ps in page_scores:
        ps.final_score = ps.rrf_score * multiplier

    # Sort by final_score descending.
    page_scores.sort(key=lambda ps: ps.final_score, reverse=True)

    # ---- 7. Candidate selection -----------------------------------------
    candidates = _select_candidates(
        page_scores, top_n, min_score_threshold, fast_pass_triggered
    )

    # ---- 8. Observation mode data ---------------------------------------
    observation_data = _build_observation_data(
        page_scores, keyphrases, fast_pass_triggered, top_n, min_score_threshold
    )

    return RelevanceResult(
        candidates=candidates,
        all_pages=page_scores,
        fast_pass_triggered=fast_pass_triggered,
        keyphrases=keyphrases,
        observation_data=observation_data,
    )


# ---------------------------------------------------------------------------
# Keyphrase extraction
# ---------------------------------------------------------------------------


def _extract_keyphrases(
    diff_text: str,
    yake_cfg: dict[str, Any],
    ner_entities: list[str],
) -> list[str]:
    """Extract keyphrases from the diff using YAKE.

    For RSS sources the caller should pass individual item text rather than
    concatenated items (merging happens upstream — see Section 3.4 of the
    plan). This function is called once per diff unit.

    For short diffs (word count < short_diff_word_threshold) the NER entities
    from Stage 2 are merged in to ensure sufficient query coverage.
    """
    per_80 = int(yake_cfg.get("keyphrases_per_80_words", 1))
    min_kp = int(yake_cfg.get("min_keyphrases", 5))
    max_kp = int(yake_cfg.get("max_keyphrases", 15))
    short_threshold = int(yake_cfg.get("short_diff_word_threshold", 50))

    language = "en"
    max_ngram = 3
    dedup_lim = 0.9

    word_count = len(diff_text.split())
    n = max(min_kp, min(max_kp, (word_count // 80) * per_80))

    keyphrases: list[str] = []

    try:
        import yake as yake_lib

        kw_extractor = yake_lib.KeywordExtractor(
            lan=language,
            n=max_ngram,
            dedupLim=dedup_lim,
            top=n,
        )
        keywords = kw_extractor.extract_keywords(diff_text)
        keyphrases = [kw for kw, _score in keywords]
    except ImportError:
        logger.warning(
            "Stage 4: yake not installed — keyphrase extraction skipped. "
            "Install with: pip install yake"
        )
    except Exception as exc:
        logger.warning("Stage 4: YAKE extraction failed: %s", exc)

    # Supplement with NER entities for short diffs.
    if word_count < short_threshold and ner_entities:
        existing_lower = {kp.lower() for kp in keyphrases}
        for entity in ner_entities:
            if entity.lower() not in existing_lower:
                keyphrases.append(entity)
                existing_lower.add(entity.lower())

    return keyphrases


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _load_pages(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all pages from the corpus as plain dicts."""
    rows = conn.execute(
        "SELECT page_id, content, doc_embedding FROM pages ORDER BY page_id"
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# BM25 scoring
# ---------------------------------------------------------------------------


def _bm25_score(keyphrases: list[str], page_contents: list[str]) -> list[float]:
    """Score each page against the extracted keyphrases using BM25Okapi.

    Returns a list of raw BM25 scores, one per page, in the same order as
    *page_contents*.  Higher score = more keyword-relevant.
    """
    if not keyphrases or not page_contents:
        return [0.0] * len(page_contents)

    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        logger.warning(
            "Stage 4: rank-bm25 not installed — BM25 will return uniform scores. "
            "Install with: pip install rank-bm25"
        )
        return [0.0] * len(page_contents)

    tokenized_corpus = [_tokenize(c) for c in page_contents]
    # Guard: empty documents become a single placeholder token.
    tokenized_corpus = [toks if toks else ["__empty__"] for toks in tokenized_corpus]

    try:
        bm25 = BM25Okapi(tokenized_corpus)
    except Exception as exc:
        logger.warning("Stage 4: BM25 index construction failed: %s", exc)
        return [0.0] * len(page_contents)

    query_tokens: list[str] = []
    for kp in keyphrases:
        query_tokens.extend(_tokenize(kp))

    if not query_tokens:
        return [0.0] * len(page_contents)

    try:
        scores = bm25.get_scores(query_tokens)
        return [float(s) for s in scores]
    except Exception as exc:
        logger.warning("Stage 4: BM25 scoring failed: %s", exc)
        return [0.0] * len(page_contents)


def _tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokeniser for BM25."""
    return re.findall(r"\b[a-z0-9]+\b", text.lower())


# ---------------------------------------------------------------------------
# Bi-encoder semantic scoring
# ---------------------------------------------------------------------------


def _semantic_score(
    diff_text: str,
    pages: list[dict[str, Any]],
    model_name: str,
) -> list[float]:
    """Encode the diff and compute cosine similarity vs stored doc embeddings.

    Returns a list of cosine similarity scores [0.0, 1.0], one per page.
    Pages without a stored doc_embedding receive a score of 0.0.
    """
    model = _load_biencoder(model_name)

    if model is None:
        logger.warning(
            "Stage 4: Bi-encoder unavailable — semantic scores will be 0.0."
        )
        return [0.0] * len(pages)

    try:
        import numpy as np
    except ImportError:
        logger.warning("Stage 4: numpy not available — semantic scores will be 0.0.")
        return [0.0] * len(pages)

    try:
        diff_vec = model.encode(diff_text, normalize_embeddings=True, show_progress_bar=False)
        diff_vec = np.array(diff_vec, dtype=np.float32)
    except Exception as exc:
        logger.warning("Stage 4: Failed to encode diff text: %s", exc)
        return [0.0] * len(pages)

    scores: list[float] = []
    for page in pages:
        emb_bytes = page.get("doc_embedding")
        if not emb_bytes:
            scores.append(0.0)
            continue
        try:
            page_vec = np.frombuffer(emb_bytes, dtype=np.float32)
            sim = float(np.dot(diff_vec, page_vec))
            scores.append(max(0.0, sim))
        except Exception as exc:
            logger.warning(
                "Stage 4: Cosine similarity failed for page %s: %s",
                page.get("page_id"), exc,
            )
            scores.append(0.0)

    return scores


def _load_biencoder(model_name: str) -> Any:
    """Lazily load and cache the bi-encoder model."""
    if model_name in _biencoder_cache:
        return _biencoder_cache[model_name]

    try:
        from sentence_transformers import SentenceTransformer

        logger.info("Stage 4: Loading bi-encoder model: %s", model_name)
        model = SentenceTransformer(model_name)
        _biencoder_cache[model_name] = model
        return model
    except ImportError:
        logger.warning(
            "Stage 4: sentence-transformers not installed — semantic scoring "
            "disabled. Install with: pip install sentence-transformers"
        )
        _biencoder_cache[model_name] = None
        return None
    except Exception as exc:
        logger.error("Stage 4: Failed to load bi-encoder %s: %s", model_name, exc)
        _biencoder_cache[model_name] = None
        return None


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------


def _rank_scores(scores: list[float], higher_is_better: bool = True) -> list[int]:
    """Return 1-based rank for each score (1 = best).

    Ties receive the same rank (dense ranking).
    """
    indexed = sorted(range(len(scores)), key=lambda i: scores[i], reverse=higher_is_better)
    ranks = [0] * len(scores)
    current_rank = 1
    prev_score: float | None = None
    for pos, idx in enumerate(indexed):
        score = scores[idx]
        if prev_score is not None and score != prev_score:
            current_rank = pos + 1
        ranks[idx] = current_rank
        prev_score = score
    return ranks


def _fuse_rrf(
    pages: list[dict[str, Any]],
    bm25_scores: list[float],
    semantic_scores: list[float],
    rrf_k: int,
    w_bm25: float,
    w_sem: float,
) -> list[PageRelevanceScore]:
    """Compute weighted RRF scores for all pages."""
    bm25_ranks = _rank_scores(bm25_scores, higher_is_better=True)
    semantic_ranks = _rank_scores(semantic_scores, higher_is_better=True)

    results: list[PageRelevanceScore] = []
    for i, page in enumerate(pages):
        rrf = (
            w_bm25 / (rrf_k + bm25_ranks[i])
            + w_sem / (rrf_k + semantic_ranks[i])
        )
        results.append(
            PageRelevanceScore(
                page_id=page["page_id"],
                bm25_score=bm25_scores[i],
                bm25_rank=bm25_ranks[i],
                semantic_score=semantic_scores[i],
                semantic_rank=semantic_ranks[i],
                rrf_score=rrf,
                final_score=rrf,   # overwritten by importance multiplier
            )
        )
    return results


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------


def _select_candidates(
    page_scores: list[PageRelevanceScore],
    top_n: int,
    min_score_threshold: float | None,
    fast_pass_triggered: bool,
) -> list[PageRelevanceScore]:
    """Select pages that proceed to Stage 5.

    Fast-pass (source_importance >= 1.0): all pages are candidates.
    Normal mode:
      - top-N by final_score, PLUS
      - any page whose final_score >= min_score_threshold (if configured).
    """
    if fast_pass_triggered:
        return list(page_scores)

    selected: set[str] = set()
    candidates: list[PageRelevanceScore] = []

    # Always include top-N.
    for ps in page_scores[:top_n]:
        if ps.page_id not in selected:
            candidates.append(ps)
            selected.add(ps.page_id)

    # Include any page above the optional score threshold.
    if min_score_threshold is not None:
        for ps in page_scores[top_n:]:
            if ps.final_score >= min_score_threshold and ps.page_id not in selected:
                candidates.append(ps)
                selected.add(ps.page_id)

    # Keep candidates sorted by final_score descending.
    candidates.sort(key=lambda ps: ps.final_score, reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# Observation mode data
# ---------------------------------------------------------------------------


def _build_observation_data(
    page_scores: list[PageRelevanceScore],
    keyphrases: list[str],
    fast_pass_triggered: bool,
    top_n: int,
    min_score_threshold: float | None,
) -> dict[str, Any]:
    """Collect score distributions for observation mode logging."""
    if not page_scores:
        return {
            "stage": "stage4_relevance",
            "page_count": 0,
            "fast_pass_triggered": fast_pass_triggered,
            "keyphrases": keyphrases,
        }

    final_scores = [ps.final_score for ps in page_scores]
    rrf_scores = [ps.rrf_score for ps in page_scores]
    bm25_scores = [ps.bm25_score for ps in page_scores]
    sem_scores = [ps.semantic_score for ps in page_scores]

    return {
        "stage": "stage4_relevance",
        "page_count": len(page_scores),
        "fast_pass_triggered": fast_pass_triggered,
        "top_n": top_n,
        "min_score_threshold": min_score_threshold,
        "keyphrases": keyphrases,
        "distributions": {
            "final_score": _distribution(final_scores),
            "rrf_score": _distribution(rrf_scores),
            "bm25_score": _distribution(bm25_scores),
            "semantic_score": _distribution(sem_scores),
        },
        "top_pages": [
            {
                "page_id": ps.page_id,
                "final_score": round(ps.final_score, 6),
                "bm25_rank": ps.bm25_rank,
                "semantic_rank": ps.semantic_rank,
                "rrf_score": round(ps.rrf_score, 6),
            }
            for ps in page_scores[:10]
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
        return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo), 6)

    return {
        "min": round(sorted_vals[0], 6),
        "p25": _percentile(0.25),
        "median": _percentile(0.50),
        "p75": _percentile(0.75),
        "max": round(sorted_vals[-1], 6),
    }
