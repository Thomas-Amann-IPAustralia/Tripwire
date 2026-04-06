"""
src/stage6_crossencoder.py

Stage 6 — Semantic Matching: Cross-Encoder (Section 3.6)

Purpose: refine the candidate list from Stage 5 using a precise cross-encoder
and then integrate lexical and graph-based signals for a final ranking.

Process:
  1. For each candidate IPFR page from Stage 5, score the full page content
     against the full normalised change document using:
       gte-reranker-modernbert-base (8,192-token context)
     Log a warning before every call where combined tokens exceed the limit.
  2. Rerank by combining three signals:
       - Cross-encoder score (primary — semantic precision)
       - Lexical relevance from Stage 4 (secondary — keyword match)
       - Pre-computed quasi-graph edges (tertiary — structural relationships)
  3. Propagate alerts through the quasi-graph:
       propagated_score = source_score × edge_weight × decay_per_hop
                          / out_degree(source_node)
       Propagation is additive-only; neighbours are never demoted.
       Max hops: 3.  Floor: 0.05.

Decision rule: pages whose final reranked score (including any graph-
propagated signal) >= cross-encoder threshold (default: 0.60) proceed to
Stage 7.

Observation mode: cross-encoder score distributions are captured in
observation_data.

Cross-encoder loading is lazy.  The caller (pipeline orchestrator) must
release the bi-encoder before loading the cross-encoder (see Section 7.4).
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Lazy model cache.
_crossencoder_cache: dict[str, Any] = {}

# Reranking blend weights (cross-encoder is primary).
_RERANK_WEIGHT_CE = 0.80
_RERANK_WEIGHT_LEXICAL = 0.20


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class CrossEncoderPageResult:
    """Per-candidate result from Stage 6."""

    page_id: str
    crossencoder_score: float
    """Raw cross-encoder score after sigmoid normalisation [0, 1]."""
    reranked_score: float
    """Combined score: cross-encoder + lexical blend."""
    final_score: float
    """reranked_score + any additive graph propagation boost."""
    decision: str
    """'proceed' if final_score >= threshold, else 'rejected'."""
    graph_propagated_to: list[str] = field(default_factory=list)
    """Page IDs that received a propagated signal from this page."""
    truncation_warning: bool = False
    """True if the input was longer than max_context_tokens."""


@dataclass
class CrossEncoderResult:
    """Output of Stage 6."""

    confirmed_pages: list[CrossEncoderPageResult]
    """Pages that passed the cross-encoder threshold."""
    all_scored: list[CrossEncoderPageResult]
    """All candidates that were scored (sorted by final_score desc)."""
    graph_propagated_pages: list[CrossEncoderPageResult]
    """Pages added solely through graph propagation (not direct candidates)."""
    observation_data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_crossencoder(
    candidate_page_ids: list[str],
    change_text: str,
    conn: sqlite3.Connection,
    config: dict[str, Any],
    stage4_scores: dict[str, float] | None = None,
    model: Any | None = None,
) -> CrossEncoderResult:
    """Run Stage 6 cross-encoder scoring and graph propagation.

    Parameters
    ----------
    candidate_page_ids:
        IPFR page IDs that survived Stage 5.
    change_text:
        Full normalised change document from Stage 3.
    conn:
        Open SQLite connection to the IPFR corpus database.
    config:
        Validated configuration dict.
    stage4_scores:
        Mapping of page_id → final_score from Stage 4 (used for reranking).
        If None, only cross-encoder scores are used.
    model:
        Pre-loaded CrossEncoder model, or None to load lazily.

    Returns
    -------
    CrossEncoderResult
    """
    ce_cfg = config.get("semantic_scoring", {}).get("crossencoder", {})
    model_name: str = ce_cfg.get("model", "gte-reranker-modernbert-base")
    threshold: float = float(ce_cfg.get("threshold", 0.60))
    max_context_tokens: int = int(ce_cfg.get("max_context_tokens", 8192))

    graph_cfg = config.get("graph", {})
    graph_enabled: bool = bool(graph_cfg.get("enabled", True))
    max_hops: int = int(graph_cfg.get("max_hops", 3))
    decay_per_hop: float = float(graph_cfg.get("decay_per_hop", 0.45))
    propagation_threshold: float = float(graph_cfg.get("propagation_threshold", 0.05))

    stage4_scores = stage4_scores or {}

    if not candidate_page_ids:
        return CrossEncoderResult(
            confirmed_pages=[],
            all_scored=[],
            graph_propagated_pages=[],
            observation_data={"warning": "no_candidates"},
        )

    # ---- Load page contents ---------------------------------------------
    page_contents = _load_page_contents(conn, candidate_page_ids)
    if not page_contents:
        logger.warning("Stage 6: Could not load any candidate page contents.")
        return CrossEncoderResult(
            confirmed_pages=[],
            all_scored=[],
            graph_propagated_pages=[],
            observation_data={"warning": "page_load_failed"},
        )

    # ---- Load cross-encoder model ---------------------------------------
    encoder = model if model is not None else _load_crossencoder(model_name)

    # Normalise Stage 4 lexical scores to [0, 1] for blending.
    lexical_normalised = _normalise_scores(stage4_scores)

    # ---- Score each candidate page --------------------------------------
    scored_results: list[CrossEncoderPageResult] = []

    for page_id in candidate_page_ids:
        page_content = page_contents.get(page_id)
        if page_content is None:
            logger.warning("Stage 6: No content for page %s — skipping.", page_id)
            continue

        # Token budget check.
        combined_token_estimate = _estimate_tokens(page_content) + _estimate_tokens(
            change_text
        )
        truncation_warning = combined_token_estimate > max_context_tokens
        if truncation_warning:
            logger.warning(
                "Stage 6: Truncation warning — page=%s, combined_tokens=%d > %d",
                page_id, combined_token_estimate, max_context_tokens,
            )

        # Cross-encoder scoring.
        ce_score = _score_pair(page_content, change_text, encoder)

        # Rerank: blend cross-encoder with lexical signal.
        lexical = lexical_normalised.get(page_id, 0.0)
        reranked = _RERANK_WEIGHT_CE * ce_score + _RERANK_WEIGHT_LEXICAL * lexical

        scored_results.append(
            CrossEncoderPageResult(
                page_id=page_id,
                crossencoder_score=ce_score,
                reranked_score=reranked,
                final_score=reranked,  # graph boost applied below
                decision="pending",
                truncation_warning=truncation_warning,
            )
        )

    # ---- Graph propagation ----------------------------------------------
    graph_extra: dict[str, float] = {}
    confirmed_set: set[str] = {
        r.page_id for r in scored_results if r.reranked_score >= threshold
    }

    if graph_enabled and scored_results:
        graph_edges = _load_graph_edges(conn)
        # Propagate from every scored page (not just confirmed) to be safe,
        # but the signal only has meaningful effect from strong scores.
        seed_scores = {r.page_id: r.reranked_score for r in scored_results}
        graph_extra = _propagate_graph(
            seed_scores, graph_edges, max_hops, decay_per_hop, propagation_threshold
        )

        # Apply additive graph boost.
        scored_by_id = {r.page_id: r for r in scored_results}
        for page_id, boost in graph_extra.items():
            if page_id in scored_by_id:
                scored_by_id[page_id].final_score = (
                    scored_by_id[page_id].reranked_score + max(0.0, boost)
                )
            # Track propagation on source pages.
            for result in scored_results:
                edges = graph_edges.get(result.page_id, [])
                if any(e["target_page_id"] == page_id for e in edges):
                    if page_id not in result.graph_propagated_to:
                        result.graph_propagated_to.append(page_id)

    # ---- Apply decision -------------------------------------------------
    for result in scored_results:
        result.decision = "proceed" if result.final_score >= threshold else "rejected"

    scored_results.sort(key=lambda r: r.final_score, reverse=True)

    # ---- Graph-only pages (propagated but not directly scored) ----------
    graph_propagated_pages: list[CrossEncoderPageResult] = []
    direct_ids = {r.page_id for r in scored_results}

    if graph_enabled:
        for page_id, boost in graph_extra.items():
            if page_id in direct_ids:
                continue
            final = boost
            if final >= threshold:
                graph_propagated_pages.append(
                    CrossEncoderPageResult(
                        page_id=page_id,
                        crossencoder_score=0.0,
                        reranked_score=0.0,
                        final_score=final,
                        decision="proceed",
                        graph_propagated_to=[],
                    )
                )

    graph_propagated_pages.sort(key=lambda r: r.final_score, reverse=True)

    confirmed_pages = [
        r for r in scored_results if r.decision == "proceed"
    ] + graph_propagated_pages

    observation_data = _build_observation_data(
        scored_results, graph_propagated_pages, threshold
    )

    return CrossEncoderResult(
        confirmed_pages=confirmed_pages,
        all_scored=scored_results,
        graph_propagated_pages=graph_propagated_pages,
        observation_data=observation_data,
    )


# ---------------------------------------------------------------------------
# Cross-encoder loading and scoring
# ---------------------------------------------------------------------------


def _load_crossencoder(model_name: str) -> Any:
    """Lazily load and cache the cross-encoder model."""
    if model_name in _crossencoder_cache:
        return _crossencoder_cache[model_name]

    try:
        from sentence_transformers import CrossEncoder

        logger.info("Stage 6: Loading cross-encoder model: %s", model_name)
        m = CrossEncoder(model_name, max_length=8192)
        _crossencoder_cache[model_name] = m
        return m
    except ImportError:
        logger.warning(
            "Stage 6: sentence-transformers not installed. "
            "Install with: pip install sentence-transformers"
        )
        _crossencoder_cache[model_name] = None
        return None
    except Exception as exc:
        logger.error("Stage 6: Failed to load cross-encoder %s: %s", model_name, exc)
        _crossencoder_cache[model_name] = None
        return None


def _sigmoid(x: float) -> float:
    """Sigmoid function to normalise raw logits to [0, 1]."""
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def _score_pair(page_content: str, change_text: str, model: Any) -> float:
    """Score a (page, change) pair with the cross-encoder.

    Returns a probability in [0, 1] (sigmoid of the raw logit).
    Returns 0.5 (uncertain) if the model is unavailable.
    """
    if model is None:
        return 0.5

    try:
        raw = model.predict([[change_text, page_content]])
        # CrossEncoder.predict returns an array; take the first element.
        if hasattr(raw, "__len__"):
            raw = float(raw[0])
        else:
            raw = float(raw)
        return _sigmoid(raw)
    except Exception as exc:
        logger.warning("Stage 6: Cross-encoder prediction failed: %s", exc)
        return 0.5


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    """Rough token count estimate: ~4 characters per token on average."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _load_page_contents(
    conn: sqlite3.Connection, page_ids: list[str]
) -> dict[str, str]:
    """Load full page content for a list of page IDs."""
    if not page_ids:
        return {}
    placeholders = ",".join("?" for _ in page_ids)
    rows = conn.execute(
        f"SELECT page_id, content FROM pages WHERE page_id IN ({placeholders})",
        page_ids,
    ).fetchall()
    return {row["page_id"]: row["content"] for row in rows}


def _load_graph_edges(
    conn: sqlite3.Connection,
) -> dict[str, list[dict[str, Any]]]:
    """Load all graph edges, grouped by source_page_id."""
    rows = conn.execute(
        "SELECT source_page_id, target_page_id, edge_type, weight FROM graph_edges"
    ).fetchall()
    edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        edges[row["source_page_id"]].append(dict(row))
    return dict(edges)


# ---------------------------------------------------------------------------
# Graph propagation
# ---------------------------------------------------------------------------


def _propagate_graph(
    seed_scores: dict[str, float],
    graph_edges: dict[str, list[dict[str, Any]]],
    max_hops: int,
    decay_per_hop: float,
    propagation_threshold: float,
) -> dict[str, float]:
    """Propagate relevance signals through the quasi-graph.

    For each confirmed page, propagate to graph neighbours:
      signal = source_score × edge_weight × decay_per_hop / out_degree(source)

    Rules:
      - Propagation is additive only (never reduces an existing score).
      - Stops when the decayed signal < propagation_threshold.
      - Max depth: max_hops.
      - Out-degree normalisation prevents hub pages from dominating.

    Returns a dict of page_id → accumulated propagated boost (delta scores
    added on top of direct scores).
    """
    accumulated: dict[str, float] = defaultdict(float)

    # BFS / iterative propagation across hops.
    # frontier: list of (page_id, score_at_this_hop)
    frontier: list[tuple[str, float]] = [
        (page_id, score) for page_id, score in seed_scores.items()
    ]

    for _hop in range(max_hops):
        next_frontier: list[tuple[str, float]] = []

        for source_id, source_score in frontier:
            outgoing = graph_edges.get(source_id, [])
            if not outgoing:
                continue

            out_degree = len(outgoing)

            for edge in outgoing:
                target_id = edge["target_page_id"]
                edge_weight = float(edge["weight"])

                signal = source_score * edge_weight * decay_per_hop / out_degree

                if signal < propagation_threshold:
                    continue

                accumulated[target_id] += signal
                next_frontier.append((target_id, signal))

        if not next_frontier:
            break
        frontier = next_frontier

    return dict(accumulated)


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


def _normalise_scores(scores: dict[str, float]) -> dict[str, float]:
    """Min-max normalise a dict of scores to [0, 1].

    If all scores are equal, returns 0.0 for every entry (avoids divide-by-zero).
    """
    if not scores:
        return {}
    min_s = min(scores.values())
    max_s = max(scores.values())
    rng = max_s - min_s
    if rng == 0.0:
        return {k: 0.0 for k in scores}
    return {k: (v - min_s) / rng for k, v in scores.items()}


# ---------------------------------------------------------------------------
# Observation mode data
# ---------------------------------------------------------------------------


def _build_observation_data(
    scored_results: list[CrossEncoderPageResult],
    graph_propagated_pages: list[CrossEncoderPageResult],
    threshold: float,
) -> dict[str, Any]:
    """Collect score distributions for observation mode logging."""
    ce_scores = [r.crossencoder_score for r in scored_results]
    reranked_scores = [r.reranked_score for r in scored_results]
    final_scores = [r.final_score for r in scored_results]
    confirmed_count = sum(1 for r in scored_results if r.decision == "proceed")

    return {
        "stage": "stage6_crossencoder",
        "candidates_scored": len(scored_results),
        "confirmed_count": confirmed_count,
        "graph_propagated_count": len(graph_propagated_pages),
        "threshold": threshold,
        "distributions": {
            "crossencoder_score": _distribution(ce_scores),
            "reranked_score": _distribution(reranked_scores),
            "final_score": _distribution(final_scores),
        },
        "scored_pages": [
            {
                "page_id": r.page_id,
                "crossencoder_score": round(r.crossencoder_score, 6),
                "reranked_score": round(r.reranked_score, 6),
                "final_score": round(r.final_score, 6),
                "decision": r.decision,
                "graph_propagated_to": r.graph_propagated_to,
                "truncation_warning": r.truncation_warning,
            }
            for r in scored_results
        ],
    }


def _distribution(values: list[float]) -> dict[str, float]:
    """Return basic descriptive statistics."""
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
