"""
tests/test_pipeline_baseline.py

Tests for the content-baseline guard in pipeline._process_source.

Scenario: Stage 1 saves probe signals on the first run, but the subsequent
scrape raises an exception.  The state file now has probe signals but no
``previous_text``.  On the next run, Stage 1 compares the cached signals
against the server's current signals, finds them equal, and would normally
return early — leaving the source permanently without a content baseline.

The fix: if Stage 1 says "unchanged" but ``previous_text`` is absent from the
stored state, the pipeline must proceed to scraping to establish a baseline.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source(
    source_id: str = "test_source",
    url: str = "https://example.com/page",
    source_type: str = "webpage",
    force_selenium: bool = False,
    importance: float = 0.5,
    check_frequency: str = "daily",
) -> dict:
    return {
        "source_id": source_id,
        "url": url,
        "source_type": source_type,
        "force_selenium": force_selenium,
        "importance": importance,
        "check_frequency": check_frequency,
        "notes": "",
    }


def _probe_unchanged(source_id: str, url: str = "https://example.com/page") -> object:
    """Return a ProbeResult that says 'unchanged'."""
    from src.stage1_metadata import ProbeResult
    return ProbeResult(
        source_id=source_id,
        url=url,
        decision="unchanged",
        signals={"etag": '"abc"', "content_length": "1234"},
    )


def _make_log_entry(source_id: str, url: str) -> dict:
    return {
        "run_id": "test",
        "source_id": source_id,
        "source_url": url,
        "source_type": "webpage",
        "timestamp": "2026-01-02T00:00:00+00:00",
        "stage_reached": "stage1",
        "outcome": "completed",
        "error_type": None,
        "error_message": None,
        "triggered_pages": None,
        "details": {"config_snapshot": {}, "stages": {}},
    }


def _make_state_file(snapshot_dir: Path, source_id: str, state: dict) -> None:
    source_dir = snapshot_dir / source_id
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")


# ---------------------------------------------------------------------------
# Stage 1 "unchanged" with no baseline — should proceed to scrape
# ---------------------------------------------------------------------------


class TestBaselineGuard:
    def test_stage1_unchanged_no_baseline_proceeds_to_scrape(self, tmp_path):
        """Stage 1 says unchanged, but previous_text is absent → must scrape."""
        from src.pipeline import _process_source

        source_id = "no_baseline_source"
        source = _make_source(source_id=source_id)

        # State has probe signals from a prior failed scrape, but no content.
        _make_state_file(
            tmp_path,
            source_id,
            {
                "probe_signals": {"etag": '"abc"', "content_length": "1234"},
                "last_checked": "2026-01-01T00:00:00+00:00",
            },
        )

        scraped_text = "This is the current page content."
        calls = []

        def fake_scrape(url, source_type, session, force_selenium=False):
            calls.append("scrape")
            return scraped_text

        from src.stage2_change_detection import ChangeDetectionResult
        no_change = ChangeDetectionResult(
            source_id=source_id, decision="no_change", hash_matched=True
        )

        with (
            patch("src.stage1_metadata.probe_source", return_value=_probe_unchanged(source_id)),
            patch("src.stage1_metadata.is_due_for_check", return_value=True),
            patch("src.scraper.scrape_and_normalise", side_effect=fake_scrape),
            patch("src.stage2_change_detection.detect_change", return_value=no_change),
            patch("src.pipeline._save_source_state"),
        ):
            _process_source(
                source=source,
                source_id=source_id,
                source_type="webpage",
                source_url=source["url"],
                source_importance=0.5,
                session=MagicMock(),
                conn=MagicMock(),
                config={},
                snapshot_dir=tmp_path,
                run_id="test-run",
                source_records=[],
                rejected_candidates=[],
                log_entry=_make_log_entry(source_id, source["url"]),
            )

        assert "scrape" in calls, (
            "Expected scrape to be called when Stage 1 says unchanged "
            "but no content baseline exists"
        )

    def test_stage1_unchanged_with_baseline_skips_scrape(self, tmp_path):
        """Stage 1 says unchanged and previous_text exists → should skip scraping."""
        from src.pipeline import _process_source

        source_id = "has_baseline_source"
        source = _make_source(source_id=source_id)

        # State has both probe signals AND previous content.
        _make_state_file(
            tmp_path,
            source_id,
            {
                "probe_signals": {"etag": '"abc"', "content_length": "1234"},
                "last_checked": "2026-01-01T00:00:00+00:00",
                "previous_text": "Existing baseline content.",
                "previous_hash": "abc123",
            },
        )

        calls = []

        def fake_scrape(url, source_type, session, force_selenium=False):
            calls.append("scrape")
            return "content"

        log_entry = _make_log_entry(source_id, source["url"])

        with (
            patch("src.stage1_metadata.probe_source", return_value=_probe_unchanged(source_id)),
            patch("src.stage1_metadata.is_due_for_check", return_value=True),
            patch("src.scraper.scrape_and_normalise", side_effect=fake_scrape),
            patch("src.pipeline._save_source_state"),
        ):
            _process_source(
                source=source,
                source_id=source_id,
                source_type="webpage",
                source_url=source["url"],
                source_importance=0.5,
                session=MagicMock(),
                conn=MagicMock(),
                config={},
                snapshot_dir=tmp_path,
                run_id="test-run",
                source_records=[],
                rejected_candidates=[],
                log_entry=log_entry,
            )

        assert "scrape" not in calls, (
            "Expected scraping to be skipped when Stage 1 says unchanged "
            "and a content baseline already exists"
        )
        assert log_entry["outcome"] == "no_change"

    def test_baseline_saved_after_forced_scrape(self, tmp_path):
        """previous_text is written to state after a forced baseline scrape."""
        from src.pipeline import _process_source

        source_id = "save_baseline_source"
        source = _make_source(source_id=source_id)

        # No content baseline.
        _make_state_file(
            tmp_path,
            source_id,
            {
                "probe_signals": {"etag": '"abc"'},
                "last_checked": "2026-01-01T00:00:00+00:00",
            },
        )

        scraped_text = "Fresh page content for baseline."
        saved_states: list[dict] = []

        def capture_save(snapshot_dir, sid, state):
            saved_states.append(state)

        from src.stage2_change_detection import ChangeDetectionResult
        no_change = ChangeDetectionResult(
            source_id=source_id, decision="no_change", hash_matched=True
        )

        with (
            patch("src.stage1_metadata.probe_source", return_value=_probe_unchanged(source_id)),
            patch("src.stage1_metadata.is_due_for_check", return_value=True),
            patch("src.scraper.scrape_and_normalise", return_value=scraped_text),
            patch("src.stage2_change_detection.detect_change", return_value=no_change),
            patch("src.pipeline._save_source_state", side_effect=capture_save),
        ):
            _process_source(
                source=source,
                source_id=source_id,
                source_type="webpage",
                source_url=source["url"],
                source_importance=0.5,
                session=MagicMock(),
                conn=MagicMock(),
                config={},
                snapshot_dir=tmp_path,
                run_id="test-run",
                source_records=[],
                rejected_candidates=[],
                log_entry=_make_log_entry(source_id, source["url"]),
            )

        assert saved_states, "Expected _save_source_state to be called"
        final_state = saved_states[-1]
        assert final_state.get("previous_text") == scraped_text, (
            "Expected previous_text to be saved after forced baseline scrape"
        )
