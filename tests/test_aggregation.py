"""
tests/test_aggregation.py

Tests for src/stage7_aggregation.py — trigger grouping per IPFR page.
"""

from __future__ import annotations

import pytest

from src.stage7_aggregation import (
    AggregationResult,
    SourceTriggerRecord,
    TriggerBundle,
    TriggerSource,
    aggregate_triggers,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_ce_page(
    page_id: str,
    final_score: float = 0.75,
    crossencoder_score: float = 0.72,
    reranked_score: float = 0.74,
    decision: str = "proceed",
    graph_propagated_to: list[str] | None = None,
) -> dict:
    return {
        "page_id": page_id,
        "crossencoder_score": crossencoder_score,
        "reranked_score": reranked_score,
        "final_score": final_score,
        "decision": decision,
        "graph_propagated_to": graph_propagated_to or [],
    }


_DEFAULT_STAGE4 = {
    "B1012": {"final_score": 0.041, "rrf_score": 0.048, "bm25_rank": 1, "semantic_rank": 2}
}
_DEFAULT_STAGE5 = {
    "B1012": {"max_chunk_score": 0.81, "chunks_above_low_medium": 5}
}


def _make_record(
    source_id: str = "SRC001",
    source_url: str = "https://example.com/src001",
    source_importance: float = 0.8,
    source_type: str = "webpage",
    diff_text: str = "The fee increased from $100 to $150.",
    significance: str = "high",
    confirmed_pages: list[dict] | None = None,
    stage4_scores: dict | None = None,
    stage5_scores: dict | None = None,
) -> SourceTriggerRecord:
    if confirmed_pages is None:
        confirmed_pages = [_make_ce_page("B1012")]
    if stage4_scores is None:
        stage4_scores = _DEFAULT_STAGE4
    if stage5_scores is None:
        stage5_scores = _DEFAULT_STAGE5
    return SourceTriggerRecord(
        source_id=source_id,
        source_url=source_url,
        source_importance=source_importance,
        source_type=source_type,
        diff_text=diff_text,
        significance=significance,
        stage4_scores=stage4_scores,
        stage5_scores=stage5_scores,
        stage6_confirmed=confirmed_pages,
    )


# ---------------------------------------------------------------------------
# Basic grouping
# ---------------------------------------------------------------------------


def test_single_source_single_page():
    record = _make_record()
    result = aggregate_triggers([record])

    assert isinstance(result, AggregationResult)
    assert len(result.bundles) == 1
    bundle = result.bundles[0]
    assert bundle.ipfr_page_id == "B1012"
    assert bundle.trigger_count == 1
    assert result.total_triggers == 1


def test_two_sources_same_page():
    r1 = _make_record(source_id="SRC001", diff_text="Change A")
    r2 = _make_record(source_id="SRC002", diff_text="Change B",
                      confirmed_pages=[_make_ce_page("B1012", final_score=0.68)])
    result = aggregate_triggers([r1, r2])

    assert len(result.bundles) == 1
    bundle = result.bundles[0]
    assert bundle.ipfr_page_id == "B1012"
    assert bundle.trigger_count == 2
    assert set(bundle.source_ids) == {"SRC001", "SRC002"}
    assert result.total_triggers == 2


def test_two_sources_different_pages():
    r1 = _make_record(source_id="SRC001", confirmed_pages=[_make_ce_page("B1012")])
    r2 = _make_record(
        source_id="SRC002",
        confirmed_pages=[_make_ce_page("C2003")],
        stage4_scores={"C2003": {"final_score": 0.05, "rrf_score": 0.06, "bm25_rank": 3, "semantic_rank": 4}},
        stage5_scores={"C2003": {"max_chunk_score": 0.77, "chunks_above_low_medium": 2}},
    )
    result = aggregate_triggers([r1, r2])

    assert len(result.bundles) == 2
    page_ids = {b.ipfr_page_id for b in result.bundles}
    assert page_ids == {"B1012", "C2003"}
    assert result.total_triggers == 2


def test_one_source_two_pages():
    record = _make_record(
        confirmed_pages=[_make_ce_page("B1012"), _make_ce_page("B1013")],
        stage4_scores={
            "B1012": {"final_score": 0.041, "rrf_score": 0.048, "bm25_rank": 1, "semantic_rank": 2},
            "B1013": {"final_score": 0.035, "rrf_score": 0.040, "bm25_rank": 2, "semantic_rank": 3},
        },
        stage5_scores={
            "B1012": {"max_chunk_score": 0.81, "chunks_above_low_medium": 5},
            "B1013": {"max_chunk_score": 0.76, "chunks_above_low_medium": 3},
        },
    )
    result = aggregate_triggers([record])

    assert len(result.bundles) == 2
    assert result.total_triggers == 2


def test_empty_source_list():
    result = aggregate_triggers([])
    assert result.bundles == []
    assert result.total_triggers == 0


def test_source_with_no_confirmed_pages():
    record = _make_record(confirmed_pages=[])
    result = aggregate_triggers([record])
    assert result.bundles == []
    assert result.total_triggers == 0


def test_rejected_pages_excluded():
    """Pages with decision != 'proceed' must not appear in bundles."""
    record = _make_record(
        confirmed_pages=[
            _make_ce_page("B1012", decision="proceed"),
            _make_ce_page("B1013", decision="rejected"),
        ]
    )
    result = aggregate_triggers([record])
    assert len(result.bundles) == 1
    assert result.bundles[0].ipfr_page_id == "B1012"


# ---------------------------------------------------------------------------
# Graph propagation
# ---------------------------------------------------------------------------


def test_graph_propagated_pages_added():
    """Pages in graph_propagated_to must appear as separate bundles."""
    record = _make_record(
        confirmed_pages=[_make_ce_page("B1012", graph_propagated_to=["C2003"])],
    )
    result = aggregate_triggers([record])

    page_ids = {b.ipfr_page_id for b in result.bundles}
    assert "B1012" in page_ids
    assert "C2003" in page_ids


def test_graph_propagated_trigger_marked():
    """Triggers added via graph propagation must have graph_propagated=True."""
    record = _make_record(
        confirmed_pages=[_make_ce_page("B1012", graph_propagated_to=["C2003"])],
    )
    result = aggregate_triggers([record])

    propagated_bundle = next(b for b in result.bundles if b.ipfr_page_id == "C2003")
    assert propagated_bundle.trigger_count == 1
    trigger = propagated_bundle.triggers[0]
    assert trigger.graph_propagated is True


def test_direct_trigger_not_marked_propagated():
    record = _make_record(
        confirmed_pages=[_make_ce_page("B1012")],
    )
    result = aggregate_triggers([record])
    trigger = result.bundles[0].triggers[0]
    assert trigger.graph_propagated is False


# ---------------------------------------------------------------------------
# Score fields
# ---------------------------------------------------------------------------


def test_trigger_scores_populated():
    record = _make_record(
        stage4_scores={"B1012": {"final_score": 0.041, "rrf_score": 0.048, "bm25_rank": 1, "semantic_rank": 2}},
        stage5_scores={"B1012": {"max_chunk_score": 0.81, "chunks_above_low_medium": 5}},
        confirmed_pages=[_make_ce_page("B1012", crossencoder_score=0.72, final_score=0.75)],
    )
    result = aggregate_triggers([record])
    trig = result.bundles[0].triggers[0]

    assert trig.stage4_final_score == pytest.approx(0.041)
    assert trig.stage4_rrf_score == pytest.approx(0.048)
    assert trig.stage4_bm25_rank == 1
    assert trig.stage4_semantic_rank == 2
    assert trig.biencoder_max_chunk_score == pytest.approx(0.81)
    assert trig.biencoder_chunks_above_threshold == 5
    assert trig.crossencoder_score == pytest.approx(0.72)
    assert trig.crossencoder_final_score == pytest.approx(0.75)


def test_missing_stage4_scores_default_zero():
    record = _make_record(stage4_scores={})
    result = aggregate_triggers([record])
    trig = result.bundles[0].triggers[0]
    assert trig.stage4_final_score == 0.0


def test_missing_stage5_scores_default_zero():
    record = _make_record(stage5_scores={})
    result = aggregate_triggers([record])
    trig = result.bundles[0].triggers[0]
    assert trig.biencoder_max_chunk_score == 0.0


# ---------------------------------------------------------------------------
# TriggerBundle properties
# ---------------------------------------------------------------------------


def test_bundle_max_crossencoder_score():
    r1 = _make_record(source_id="S1", confirmed_pages=[_make_ce_page("B1012", final_score=0.70)])
    r2 = _make_record(source_id="S2", confirmed_pages=[_make_ce_page("B1012", final_score=0.85)])
    result = aggregate_triggers([r1, r2])

    bundle = result.bundles[0]
    assert bundle.max_crossencoder_score == pytest.approx(0.85)


def test_bundle_source_ids():
    r1 = _make_record(source_id="S1")
    r2 = _make_record(source_id="S2")
    result = aggregate_triggers([r1, r2])

    # Both sources map to the same page (B1012), so one bundle with 2 triggers.
    bundle = result.bundles[0]
    assert set(bundle.source_ids) == {"S1", "S2"}


def test_bundle_score_summary_keys():
    result = aggregate_triggers([_make_record()])
    summary = result.bundles[0].score_summary()
    assert "trigger_count" in summary
    assert "source_ids" in summary
    assert "max_crossencoder_final" in summary
    assert "max_stage4_final" in summary
    assert "max_biencoder_chunk" in summary


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


def test_bundles_sorted_by_crossencoder_score_descending():
    r1 = _make_record(
        source_id="S1",
        confirmed_pages=[_make_ce_page("B1012", final_score=0.60)],
    )
    r2 = _make_record(
        source_id="S2",
        confirmed_pages=[_make_ce_page("C2003", final_score=0.90)],
        stage4_scores={"C2003": {"final_score": 0.05, "rrf_score": 0.06, "bm25_rank": 2, "semantic_rank": 3}},
        stage5_scores={"C2003": {"max_chunk_score": 0.77, "chunks_above_low_medium": 2}},
    )
    result = aggregate_triggers([r1, r2])

    assert result.bundles[0].ipfr_page_id == "C2003"
    assert result.bundles[1].ipfr_page_id == "B1012"


# ---------------------------------------------------------------------------
# Observation data
# ---------------------------------------------------------------------------


def test_observation_data_populated():
    result = aggregate_triggers([_make_record()])
    obs = result.observation_data
    assert obs["pages_aggregated"] == 1
    assert obs["total_triggers"] == 1
    assert len(obs["per_page"]) == 1
    assert obs["per_page"][0]["ipfr_page_id"] == "B1012"


def test_observation_data_empty():
    result = aggregate_triggers([])
    assert result.observation_data["pages_aggregated"] == 0
    assert result.observation_data["total_triggers"] == 0
