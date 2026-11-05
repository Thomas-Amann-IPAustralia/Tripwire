"""
src/stage7_aggregation.py

Stage 7 — Trigger Aggregation (Section 3.7)

Purpose: before making LLM calls, group all triggers that exceeded thresholds
for the same IPFR page within the current run window.  This prevents the
content owner receiving multiple separate notifications about the same page
and allows the LLM to reason about the combined effect of several upstream
changes on a single IPFR page.

Process:
  1. Collect all (source, IPFR page) pairs that survived Stage 6 in this run.
  2. Group by IPFR page ID.
  3. For each IPFR page, assemble a TriggerBundle containing:
       - All relevant diffs (webpage diffs, FRL explainers, RSS extracts)
       - Source metadata (source ID, source URL, importance ranking)
       - All scores from Stages 4–6 for each trigger
  4. Pass each TriggerBundle to Stage 8 as a single unit.

Outputs logged: run ID, per-IPFR-page trigger bundles (source IDs, score
summaries), count of triggers per page.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class TriggerSource:
    """One upstream trigger event for an IPFR page."""

    source_id: str
    source_url: str
    source_importance: float
    source_type: str
    """'webpage' | 'frl' | 'rss'"""
    diff_text: str
    """Normalised diff/change text from Stage 3."""
    significance: str
    """'high' | 'standard' — significance tag from Stage 2."""
    stage4_final_score: float
    """Final Stage 4 RRF × importance score for this IPFR page."""
    stage4_rrf_score: float
    stage4_bm25_rank: int
    stage4_semantic_rank: int
    biencoder_max_chunk_score: float
    """Highest single-chunk cosine score from Stage 5."""
    biencoder_chunks_above_threshold: int
    """Count of chunks exceeding the low-medium threshold in Stage 5."""
    crossencoder_score: float
    """Raw cross-encoder score from Stage 6."""
    crossencoder_reranked_score: float
    """Blended reranked score from Stage 6."""
    crossencoder_final_score: float
    """Final score after graph propagation (if any)."""
    graph_propagated: bool = False
    """True if this page reached the bundle via graph propagation."""


@dataclass
class TriggerBundle:
    """All triggers for a single IPFR page in the current run."""

    ipfr_page_id: str
    triggers: list[TriggerSource] = field(default_factory=list)

    @property
    def trigger_count(self) -> int:
        return len(self.triggers)

    @property
    def max_crossencoder_score(self) -> float:
        if not self.triggers:
            return 0.0
        return max(t.crossencoder_final_score for t in self.triggers)

    @property
    def source_ids(self) -> list[str]:
        return [t.source_id for t in self.triggers]

    def score_summary(self) -> dict[str, Any]:
        """Compact score summary for logging."""
        return {
            "trigger_count": self.trigger_count,
            "source_ids": self.source_ids,
            "max_crossencoder_final": self.max_crossencoder_score,
            "max_stage4_final": max(
                (t.stage4_final_score for t in self.triggers), default=0.0
            ),
            "max_biencoder_chunk": max(
                (t.biencoder_max_chunk_score for t in self.triggers), default=0.0
            ),
        }


@dataclass
class AggregationResult:
    """Output of Stage 7."""

    bundles: list[TriggerBundle]
    """One TriggerBundle per IPFR page that has at least one trigger."""
    total_triggers: int
    """Sum of all trigger counts across all bundles."""
    observation_data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Input record (provided by the pipeline orchestrator per source run)
# ---------------------------------------------------------------------------


@dataclass
class SourceTriggerRecord:
    """A single source's output from Stages 1–6, ready for aggregation."""

    source_id: str
    source_url: str
    source_importance: float
    source_type: str
    diff_text: str
    significance: str

    # Stage 4 data — per-page scores, keyed by page_id
    stage4_scores: dict[str, dict[str, Any]]
    """
    Mapping page_id → {
        'final_score': float,
        'rrf_score': float,
        'bm25_rank': int,
        'semantic_rank': int,
    }
    """

    # Stage 5 data — per-page bi-encoder results, keyed by page_id
    stage5_scores: dict[str, dict[str, Any]]
    """
    Mapping page_id → {
        'max_chunk_score': float,
        'chunks_above_low_medium': int,
    }
    """

    # Stage 6 confirmed pages (list of CrossEncoderPageResult-compatible dicts)
    stage6_confirmed: list[dict[str, Any]]
    """
    Each dict contains at minimum:
      page_id, crossencoder_score, reranked_score, final_score,
      decision, graph_propagated_to (list[str])
    """


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def aggregate_triggers(
    source_records: list[SourceTriggerRecord],
    config: dict[str, Any] | None = None,
) -> AggregationResult:
    """Group all confirmed triggers by IPFR page ID.

    Parameters
    ----------
    source_records:
        One record per source that produced at least one confirmed Stage 6
        result.  Sources that produced no confirmed pages should be omitted
        (or included — they will be silently ignored).
    config:
        Validated pipeline configuration.  Currently unused but accepted for
        forward-compatibility with observation mode logging.

    Returns
    -------
    AggregationResult
    """
    # page_id → TriggerBundle
    bundles: dict[str, TriggerBundle] = {}

    for rec in source_records:
        for ce_page in rec.stage6_confirmed:
            page_id: str = ce_page["page_id"]
            decision: str = ce_page.get("decision", "proceed")
            if decision != "proceed":
                continue

            if page_id not in bundles:
                bundles[page_id] = TriggerBundle(ipfr_page_id=page_id)

            # Collect scores for this page from each prior stage.
            s4 = rec.stage4_scores.get(page_id, {})
            s5 = rec.stage5_scores.get(page_id, {})

            trigger = TriggerSource(
                source_id=rec.source_id,
                source_url=rec.source_url,
                source_importance=rec.source_importance,
                source_type=rec.source_type,
                diff_text=rec.diff_text,
                significance=rec.significance,
                stage4_final_score=float(s4.get("final_score", 0.0)),
                stage4_rrf_score=float(s4.get("rrf_score", 0.0)),
                stage4_bm25_rank=int(s4.get("bm25_rank", 0)),
                stage4_semantic_rank=int(s4.get("semantic_rank", 0)),
                biencoder_max_chunk_score=float(
                    s5.get("max_chunk_score", 0.0)
                ),
                biencoder_chunks_above_threshold=int(
                    s5.get("chunks_above_low_medium", 0)
                ),
                crossencoder_score=float(ce_page.get("crossencoder_score", 0.0)),
                crossencoder_reranked_score=float(
                    ce_page.get("reranked_score", 0.0)
                ),
                crossencoder_final_score=float(ce_page.get("final_score", 0.0)),
                graph_propagated=False,
            )
            bundles[page_id].triggers.append(trigger)

            # Also add graph-propagated pages as separate trigger entries
            # with graph_propagated=True.  These use the propagated score
            # rather than the direct cross-encoder score.
            for prop_page_id in ce_page.get("graph_propagated_to", []):
                if prop_page_id not in bundles:
                    bundles[prop_page_id] = TriggerBundle(
                        ipfr_page_id=prop_page_id
                    )
                # Propagated entries share the same source/diff but carry
                # their own (lower) propagated final score, which we don't
                # have directly here.  We record the cross-encoder final score
                # as-is; the pipeline may later filter these based on
                # observation data.  Mark graph_propagated=True so Stage 8
                # can treat them with appropriate confidence.
                prop_trigger = TriggerSource(
                    source_id=rec.source_id,
                    source_url=rec.source_url,
                    source_importance=rec.source_importance,
                    source_type=rec.source_type,
                    diff_text=rec.diff_text,
                    significance=rec.significance,
                    stage4_final_score=float(s4.get("final_score", 0.0)),
                    stage4_rrf_score=float(s4.get("rrf_score", 0.0)),
                    stage4_bm25_rank=int(s4.get("bm25_rank", 0)),
                    stage4_semantic_rank=int(s4.get("semantic_rank", 0)),
                    biencoder_max_chunk_score=float(
                        s5.get("max_chunk_score", 0.0)
                    ),
                    biencoder_chunks_above_threshold=int(
                        s5.get("chunks_above_low_medium", 0)
                    ),
                    crossencoder_score=float(
                        ce_page.get("crossencoder_score", 0.0)
                    ),
                    crossencoder_reranked_score=float(
                        ce_page.get("reranked_score", 0.0)
                    ),
                    crossencoder_final_score=float(
                        ce_page.get("final_score", 0.0)
                    ),
                    graph_propagated=True,
                )
                bundles[prop_page_id].triggers.append(prop_trigger)

    sorted_bundles = sorted(
        bundles.values(),
        key=lambda b: b.max_crossencoder_score,
        reverse=True,
    )

    total_triggers = sum(b.trigger_count for b in sorted_bundles)

    observation_data: dict[str, Any] = {
        "pages_aggregated": len(sorted_bundles),
        "total_triggers": total_triggers,
        "per_page": [
            {
                "ipfr_page_id": b.ipfr_page_id,
                **b.score_summary(),
            }
            for b in sorted_bundles
        ],
    }

    logger.info(
        "Stage 7: aggregated %d trigger(s) across %d IPFR page(s)",
        total_triggers,
        len(sorted_bundles),
    )

    return AggregationResult(
        bundles=sorted_bundles,
        total_triggers=total_triggers,
        observation_data=observation_data,
    )
