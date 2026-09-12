"""
tests/test_llm_assessment.py

Tests for src/stage8_llm.py — LLM assessment, schema validation, retry logic,
and deferred trigger mechanism.  No real LLM calls are made.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.stage7_aggregation import TriggerBundle, TriggerSource
from src.stage8_llm import (
    LLMAssessment,
    LLMStageResult,
    assess_bundles,
    load_pending_deferred_triggers,
    mark_deferred_trigger_processed,
    validate_llm_response,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path):
    """In-memory SQLite database with the required tables."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE pages (
            page_id     TEXT PRIMARY KEY,
            url         TEXT NOT NULL,
            title       TEXT NOT NULL,
            content     TEXT NOT NULL,
            version_hash TEXT NOT NULL,
            last_modified TEXT,
            last_checked  TEXT,
            last_ingested TEXT,
            doc_embedding BLOB
        )
    """)
    conn.execute("""
        CREATE TABLE deferred_triggers (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       TEXT NOT NULL,
            source_id    TEXT NOT NULL,
            ipfr_page_id TEXT NOT NULL,
            trigger_data TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            processed    INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        INSERT INTO pages (page_id, url, title, content, version_hash)
        VALUES ('B1012', 'https://ipfr.example/b1012', 'Trade Marks Application',
                'The current examination period is 12 months under s.44.', 'abc123')
    """)
    conn.commit()
    return conn


@pytest.fixture()
def simple_config():
    return {
        "pipeline": {
            "observation_mode": False,
            "llm_model": "gpt-4o",
            "llm_temperature": 0.2,
            "max_retries": 3,
            "retry_base_delay_seconds": 0.01,
            "deferred_trigger_max_age_days": 7,
        }
    }


@pytest.fixture()
def observation_config():
    return {
        "pipeline": {
            "observation_mode": True,
            "llm_model": "gpt-4o",
            "llm_temperature": 0.2,
            "max_retries": 3,
            "retry_base_delay_seconds": 0.01,
            "deferred_trigger_max_age_days": 7,
        }
    }


def _make_bundle(page_id: str = "B1012") -> TriggerBundle:
    bundle = TriggerBundle(ipfr_page_id=page_id)
    bundle.triggers.append(
        TriggerSource(
            source_id="TMAA2026",
            source_url="https://www.legislation.gov.au/TMAA2026",
            source_importance=0.9,
            source_type="frl",
            diff_text="Section 44 — examination period reduced from 12 to 6 months.",
            significance="high",
            stage4_final_score=0.041,
            stage4_rrf_score=0.048,
            stage4_bm25_rank=1,
            stage4_semantic_rank=2,
            biencoder_max_chunk_score=0.81,
            biencoder_chunks_above_threshold=5,
            crossencoder_score=0.72,
            crossencoder_reranked_score=0.74,
            crossencoder_final_score=0.75,
        )
    )
    return bundle


def _mock_client(response_json: dict) -> MagicMock:
    client = MagicMock()
    completion = MagicMock()
    choice = MagicMock()
    choice.message.content = json.dumps(response_json)
    completion.choices = [choice]
    completion.usage.prompt_tokens = 500
    completion.usage.completion_tokens = 100
    completion.usage.total_tokens = 600
    client.chat.completions.create.return_value = completion
    return client


# ---------------------------------------------------------------------------
# validate_llm_response
# ---------------------------------------------------------------------------


def test_validate_change_required():
    raw = json.dumps({
        "verdict": "CHANGE_REQUIRED",
        "confidence": 0.85,
        "reasoning": "The period changed.",
        "suggested_changes": ["Update from 12 to 6 months."],
    })
    result = validate_llm_response(raw)
    assert result["verdict"] == "CHANGE_REQUIRED"
    assert result["confidence"] == pytest.approx(0.85)
    assert len(result["suggested_changes"]) == 1


def test_validate_no_change():
    raw = json.dumps({
        "verdict": "NO_CHANGE",
        "confidence": 0.90,
        "reasoning": "The change is about copyright, not trade marks.",
        "suggested_changes": [],
    })
    result = validate_llm_response(raw)
    assert result["verdict"] == "NO_CHANGE"
    assert result["suggested_changes"] == []


def test_validate_uncertain():
    raw = json.dumps({
        "verdict": "UNCERTAIN",
        "confidence": 0.50,
        "reasoning": "It is unclear whether this affects our content.",
        "suggested_changes": [],
    })
    result = validate_llm_response(raw)
    assert result["verdict"] == "UNCERTAIN"


def test_validate_strips_markdown_fence():
    raw = '```json\n{"verdict":"NO_CHANGE","confidence":0.9,"reasoning":"ok","suggested_changes":[]}\n```'
    result = validate_llm_response(raw)
    assert result["verdict"] == "NO_CHANGE"


def test_validate_invalid_verdict():
    raw = json.dumps({
        "verdict": "MAYBE",
        "confidence": 0.5,
        "reasoning": "who knows",
        "suggested_changes": [],
    })
    with pytest.raises(ValueError, match="verdict"):
        validate_llm_response(raw)


def test_validate_confidence_out_of_range():
    raw = json.dumps({
        "verdict": "NO_CHANGE",
        "confidence": 1.5,
        "reasoning": "Something",
        "suggested_changes": [],
    })
    with pytest.raises(ValueError, match="confidence"):
        validate_llm_response(raw)


def test_validate_empty_reasoning():
    raw = json.dumps({
        "verdict": "NO_CHANGE",
        "confidence": 0.8,
        "reasoning": "   ",
        "suggested_changes": [],
    })
    with pytest.raises(ValueError, match="reasoning"):
        validate_llm_response(raw)


def test_validate_change_required_empty_suggestions():
    raw = json.dumps({
        "verdict": "CHANGE_REQUIRED",
        "confidence": 0.8,
        "reasoning": "Something changed.",
        "suggested_changes": [],
    })
    with pytest.raises(ValueError, match="suggested_changes"):
        validate_llm_response(raw)


def test_validate_non_change_required_clears_suggestions():
    """If verdict != CHANGE_REQUIRED but suggested_changes is populated, coerce to []."""
    raw = json.dumps({
        "verdict": "NO_CHANGE",
        "confidence": 0.9,
        "reasoning": "No action needed.",
        "suggested_changes": ["Some spurious suggestion"],
    })
    result = validate_llm_response(raw)
    assert result["suggested_changes"] == []


def test_validate_not_json():
    with pytest.raises(ValueError, match="JSON"):
        validate_llm_response("This is not JSON at all.")


def test_validate_json_array():
    with pytest.raises(ValueError, match="JSON object"):
        validate_llm_response(json.dumps(["verdict", "NO_CHANGE"]))


def test_validate_integer_confidence():
    """Integer confidence values should be accepted and coerced to float."""
    raw = json.dumps({
        "verdict": "NO_CHANGE",
        "confidence": 1,
        "reasoning": "Nothing changed.",
        "suggested_changes": [],
    })
    result = validate_llm_response(raw)
    assert result["confidence"] == pytest.approx(1.0)
    assert isinstance(result["confidence"], float)


# ---------------------------------------------------------------------------
# assess_bundles — observation mode
# ---------------------------------------------------------------------------


def test_assess_bundles_observation_mode(db, observation_config):
    bundles = [_make_bundle()]
    result = assess_bundles(bundles, db, observation_config, "run-001")

    assert isinstance(result, LLMStageResult)
    assert result.assessments == []
    assert result.observation_data.get("skipped") is True


def test_assess_bundles_empty_bundles(db, simple_config):
    result = assess_bundles([], db, simple_config, "run-001")
    assert result.assessments == []
    assert result.deferred_count == 0
    assert result.failed_count == 0


# ---------------------------------------------------------------------------
# assess_bundles — successful call
# ---------------------------------------------------------------------------


def test_assess_bundles_change_required(db, simple_config):
    client = _mock_client({
        "verdict": "CHANGE_REQUIRED",
        "confidence": 0.85,
        "reasoning": "Examination period changed.",
        "suggested_changes": ["Update from 12 to 6 months."],
    })
    bundles = [_make_bundle("B1012")]
    result = assess_bundles(bundles, db, simple_config, "run-001", client=client)

    assert len(result.assessments) == 1
    a = result.assessments[0]
    assert a.verdict == "CHANGE_REQUIRED"
    assert a.confidence == pytest.approx(0.85)
    assert a.schema_valid is True
    assert a.retries == 0
    assert not a.deferred
    assert a.ipfr_page_id == "B1012"


def test_assess_bundles_no_change(db, simple_config):
    client = _mock_client({
        "verdict": "NO_CHANGE",
        "confidence": 0.90,
        "reasoning": "Unrelated change.",
        "suggested_changes": [],
    })
    result = assess_bundles([_make_bundle()], db, simple_config, "run-001", client=client)
    assert result.assessments[0].verdict == "NO_CHANGE"


def test_assess_bundles_token_counts(db, simple_config):
    client = _mock_client({
        "verdict": "NO_CHANGE",
        "confidence": 0.9,
        "reasoning": "OK",
        "suggested_changes": [],
    })
    result = assess_bundles([_make_bundle()], db, simple_config, "run-001", client=client)
    a = result.assessments[0]
    assert a.prompt_tokens == 500
    assert a.completion_tokens == 100
    assert a.total_tokens == 600


# ---------------------------------------------------------------------------
# assess_bundles — missing page
# ---------------------------------------------------------------------------


def test_assess_bundles_missing_page(db, simple_config):
    """Bundles for a page not in the DB should be counted as failures."""
    bundles = [_make_bundle("NONEXISTENT")]
    result = assess_bundles(bundles, db, simple_config, "run-001", client=MagicMock())
    assert len(result.assessments) == 0
    assert result.failed_count == 1


# ---------------------------------------------------------------------------
# assess_bundles — schema validation failure with retry
# ---------------------------------------------------------------------------


def test_assess_bundles_schema_failure_then_success(db, simple_config):
    """First call returns invalid JSON; second call (retry) returns valid JSON."""
    bad_response = MagicMock()
    bad_response.choices[0].message.content = '{"verdict": "DUNNO"}'
    bad_response.usage.prompt_tokens = 100
    bad_response.usage.completion_tokens = 20
    bad_response.usage.total_tokens = 120

    good_response = MagicMock()
    good_response.choices[0].message.content = json.dumps({
        "verdict": "NO_CHANGE",
        "confidence": 0.8,
        "reasoning": "Not relevant.",
        "suggested_changes": [],
    })
    good_response.usage.prompt_tokens = 100
    good_response.usage.completion_tokens = 30
    good_response.usage.total_tokens = 130

    client = MagicMock()
    client.chat.completions.create.side_effect = [bad_response, good_response]

    result = assess_bundles([_make_bundle()], db, simple_config, "run-001", client=client)
    a = result.assessments[0]
    assert a.verdict == "NO_CHANGE"
    assert a.retries == 1
    assert a.schema_valid is True


def test_assess_bundles_both_attempts_fail(db, simple_config):
    """Both attempts return invalid JSON — page should be counted as failed."""
    bad = MagicMock()
    bad.choices[0].message.content = "not json"
    bad.usage.prompt_tokens = 10
    bad.usage.completion_tokens = 5
    bad.usage.total_tokens = 15

    client = MagicMock()
    client.chat.completions.create.return_value = bad

    result = assess_bundles([_make_bundle()], db, simple_config, "run-001", client=client)
    assert len(result.assessments) == 0
    assert result.failed_count == 1


# ---------------------------------------------------------------------------
# assess_bundles — deferred trigger on RetryableError
# ---------------------------------------------------------------------------


def test_assess_bundles_deferred_on_api_failure(db, simple_config):
    """When the LLM API raises RetryableError, the trigger should be deferred."""
    from src.errors import RetryableError

    client = MagicMock()
    client.chat.completions.create.side_effect = RetryableError("rate limit")

    result = assess_bundles([_make_bundle()], db, simple_config, "run-001", client=client)
    assert result.deferred_count == 1
    assert len(result.assessments) == 0

    # Verify the deferred trigger was persisted.
    rows = db.execute(
        "SELECT ipfr_page_id, processed FROM deferred_triggers"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "B1012"
    assert rows[0][1] == 0


# ---------------------------------------------------------------------------
# Deferred trigger persistence helpers
# ---------------------------------------------------------------------------


def test_load_pending_deferred_triggers_empty(db):
    rows = load_pending_deferred_triggers(db)
    assert rows == []


def test_load_pending_deferred_triggers_returns_unprocessed(db):
    trigger_data = {"ipfr_page_id": "B1012", "triggers": []}
    db.execute(
        """
        INSERT INTO deferred_triggers (run_id, source_id, ipfr_page_id, trigger_data, created_at)
        VALUES ('run-001', 'SRC001', 'B1012', ?, ?)
        """,
        (json.dumps(trigger_data), datetime.now(timezone.utc).isoformat()),
    )
    db.commit()

    rows = load_pending_deferred_triggers(db)
    assert len(rows) == 1
    assert rows[0]["ipfr_page_id"] == "B1012"
    assert rows[0]["trigger_data"]["ipfr_page_id"] == "B1012"


def test_load_pending_deferred_triggers_excludes_processed(db):
    trigger_data = {"ipfr_page_id": "B1012", "triggers": []}
    db.execute(
        """
        INSERT INTO deferred_triggers (run_id, source_id, ipfr_page_id, trigger_data, created_at, processed)
        VALUES ('run-001', 'SRC001', 'B1012', ?, ?, 1)
        """,
        (json.dumps(trigger_data), datetime.now(timezone.utc).isoformat()),
    )
    db.commit()

    rows = load_pending_deferred_triggers(db)
    assert rows == []


def test_load_pending_deferred_triggers_discards_stale(db):
    """Triggers older than max_age_days should be marked processed and excluded."""
    trigger_data = {"ipfr_page_id": "B1012", "triggers": []}
    old_date = "2020-01-01T00:00:00+00:00"
    db.execute(
        """
        INSERT INTO deferred_triggers (run_id, source_id, ipfr_page_id, trigger_data, created_at)
        VALUES ('run-old', 'SRC001', 'B1012', ?, ?)
        """,
        (json.dumps(trigger_data), old_date),
    )
    db.commit()

    rows = load_pending_deferred_triggers(db, max_age_days=7)
    assert rows == []

    # Stale row should now be marked as processed.
    processed = db.execute(
        "SELECT processed FROM deferred_triggers WHERE run_id = 'run-old'"
    ).fetchone()
    assert processed[0] == 1


def test_mark_deferred_trigger_processed(db):
    trigger_data = {"ipfr_page_id": "B1012", "triggers": []}
    db.execute(
        """
        INSERT INTO deferred_triggers (run_id, source_id, ipfr_page_id, trigger_data, created_at)
        VALUES ('run-001', 'SRC001', 'B1012', ?, ?)
        """,
        (json.dumps(trigger_data), datetime.now(timezone.utc).isoformat()),
    )
    db.commit()
    row_id = db.execute("SELECT id FROM deferred_triggers").fetchone()[0]

    mark_deferred_trigger_processed(db, row_id)

    processed = db.execute(
        "SELECT processed FROM deferred_triggers WHERE id = ?", (row_id,)
    ).fetchone()
    assert processed[0] == 1


# ---------------------------------------------------------------------------
# observation_data
# ---------------------------------------------------------------------------


def test_verdict_distribution_in_observation_data(db, simple_config):
    responses = [
        {"verdict": "CHANGE_REQUIRED", "confidence": 0.85, "reasoning": "x",
         "suggested_changes": ["do this"]},
        {"verdict": "NO_CHANGE", "confidence": 0.9, "reasoning": "y",
         "suggested_changes": []},
        {"verdict": "UNCERTAIN", "confidence": 0.5, "reasoning": "z",
         "suggested_changes": []},
    ]
    # We need 3 pages in the DB.
    for pid in ["B1013", "B1014"]:
        db.execute(
            "INSERT INTO pages (page_id, url, title, content, version_hash) VALUES (?,?,?,?,?)",
            (pid, f"https://x/{pid}", pid, "some content", "hash"),
        )
    db.commit()

    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        resp = MagicMock()
        resp.choices[0].message.content = json.dumps(responses[call_count])
        resp.usage.prompt_tokens = 100
        resp.usage.completion_tokens = 30
        resp.usage.total_tokens = 130
        call_count += 1
        return resp

    client = MagicMock()
    client.chat.completions.create.side_effect = side_effect

    bundles = [_make_bundle("B1012"), _make_bundle("B1013"), _make_bundle("B1014")]
    result = assess_bundles(bundles, db, simple_config, "run-001", client=client)

    dist = result.observation_data.get("verdict_distribution", {})
    assert dist["CHANGE_REQUIRED"] == 1
    assert dist["NO_CHANGE"] == 1
    assert dist["UNCERTAIN"] == 1
