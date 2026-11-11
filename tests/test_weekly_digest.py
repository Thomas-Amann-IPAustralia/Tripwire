"""
tests/test_weekly_digest.py

Tests for src/weekly_digest.py — queueing, scheduling (incl. daylight saving),
composition and fail-closed sending. No real SMTP connections are made.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.config import ConfigError, _validate_notifications
from src.health import HealthAlert
from src.stage9_notification import PageMeta, RejectedCandidate
from src.weekly_digest import (
    compose_digest,
    enqueue_run,
    ensure_tables,
    in_send_window,
    is_due,
    run_stats,
    send_weekly_digest,
)
from tests.test_notification import _make_assessment, _make_bundle


def _config(**notif) -> dict:
    return {
        "pipeline": {"observation_mode": False},
        "notifications": {
            "content_owner_email": "owner@example.gov.au",
            "health_alert_email": "admin@example.gov.au",
            "cadence": "weekly",
            "weekly_digest": {"day": "monday", "time": "09:30",
                              "timezone": "Australia/Sydney", "lookback_days": 7},
            **notif,
        },
    }


@pytest.fixture()
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.sqlite"))
    ensure_tables(c)
    yield c
    c.close()


def _queue(conn, run_id="2026-11-10-509", verdict=None, health=None, rejected=None):
    assessments = [_make_assessment(verdict=verdict)] if verdict else []
    enqueue_run(
        conn, run_id=run_id, run_date="10 November 2026",
        assessments=assessments,
        bundles_by_page={"B1012": _make_bundle()},
        page_meta_by_id={"B1012": PageMeta("B1012", "Trade mark examination", "https://x/b1012")},
        rejected_candidates=rejected or [],
        health_alerts=health or [],
        stats={"sources_total": 10, "sources_checked": 4, "changes_detected": 1, "errors": 2},
    )


# Monday 9 Nov 2026 09:30 AEDT == Sunday 8 Nov 22:30 UTC.
AEDT_SLOT_1 = datetime(2026, 11, 8, 22, 30, tzinfo=timezone.utc)   # Mon 09:30 AEDT
AEDT_SLOT_2 = datetime(2026, 11, 8, 23, 30, tzinfo=timezone.utc)   # Mon 10:30 AEDT
AEST_SLOT_1 = datetime(2026, 6, 14, 22, 30, tzinfo=timezone.utc)   # Mon 08:30 AEST
AEST_SLOT_2 = datetime(2026, 6, 14, 23, 30, tzinfo=timezone.utc)   # Mon 09:30 AEST


class TestSchedule:
    def test_aedt_first_slot_due(self, conn):
        assert is_due(conn, _config(), AEDT_SLOT_1)[0]

    def test_aest_first_slot_too_early_second_due(self, conn):
        due, reason = is_due(conn, _config(), AEST_SLOT_1)
        assert not due and "before" in reason
        assert is_due(conn, _config(), AEST_SLOT_2)[0]

    def test_not_monday(self):
        due, reason = in_send_window(_config(), datetime(2026, 11, 10, 22, 30, tzinfo=timezone.utc))
        assert not due and "Wednesday" in reason

    def test_only_once_per_week(self, conn):
        _queue(conn)
        smtp = MagicMock()
        assert send_weekly_digest(conn, _config(), now=AEDT_SLOT_1, smtp_client=smtp)["sent"]
        second = send_weekly_digest(conn, _config(), now=AEDT_SLOT_2, smtp_client=smtp)
        assert not second["sent"] and "already sent" in second["reason"]
        assert smtp.sendmail.call_count == 1


class TestCompose:
    def test_no_noteworthy_changes(self, conn):
        _queue(conn, verdict="NO_CHANGE")
        res = send_weekly_digest(conn, _config(), now=AEDT_SLOT_1, dry_run=True)
        assert "no noteworthy changes identified" in res["subject"]
        assert "No noteworthy changes identified" in res["body_text"]
        assert "judged no change needed" in res["body_text"]
        assert res["flagged_count"] == 0

    def test_empty_week_still_sends(self, conn):
        smtp = MagicMock()
        res = send_weekly_digest(conn, _config(), now=AEDT_SLOT_1, smtp_client=smtp)
        assert res["sent"] and "no noteworthy changes" in res["subject"]
        smtp.sendmail.assert_called_once()

    def test_flagged_items_from_several_runs(self, conn):
        _queue(conn, run_id="2026-11-04-503", verdict="CHANGE_REQUIRED")
        _queue(conn, run_id="2026-11-06-505", verdict="UNCERTAIN",
               rejected=[RejectedCandidate("src1", "https://s", "B2000", "crossencoder", 0.1, 0.2)])
        res = send_weekly_digest(conn, _config(), now=AEDT_SLOT_1, dry_run=True)
        assert "2 IPFR pages flagged" in res["subject"]
        text = res["body_text"]
        assert "AMENDMENT REQUIRED" in text and "ITEMS REQUIRING HUMAN REVIEW" in text
        assert "CANDIDATES REJECTED AT DEEP ANALYSIS" in text
        assert "Update period to 6 months." in text           # full suggested change
        # Feedback links carry the run each item came from.
        assert "2026-11-04-503%20%E2%80%94%20B1012" in text
        assert "2026-11-06-505%20%E2%80%94%20B1012" in text
        assert "Amendment required" in res["body_html"]
        assert "Needs human review" in res["body_html"]

    def test_health_warnings_from_latest_run(self, conn):
        _queue(conn, run_id="r1", health=[HealthAlert("consecutive_failures", "warning", "old", "")])
        _queue(conn, run_id="r2", health=[HealthAlert("consecutive_failures", "warning",
                                                      "Source `afp` has failed 20 runs", "")])
        res = send_weekly_digest(conn, _config(), now=AEDT_SLOT_1, dry_run=True)
        assert "SOURCE HEALTH (1 warning(s) at the latest run, r2)" in res["body_text"]
        assert "Source `afp` has failed 20 runs" in res["body_text"]
        assert "  • old" not in res["body_text"]

    def test_activity_totals(self):
        payloads = [{"run_id": "a", "stats": {"sources_checked": 4, "changes_detected": 1,
                                              "errors": 2}, "assessed_count": 3,
                     "no_change_count": 3}] * 2
        _, text, _, _ = compose_digest(payloads, datetime(2026, 11, 3).date(),
                                       datetime(2026, 11, 9).date(), "fb@x")
        assert "Pipeline runs: 2" in text and "Source checks performed: 8" in text
        assert "IPFR pages assessed by the LLM: 6 (6 judged no change needed)" in text
        assert "Period: 3–9 November 2026" in text


class TestSending:
    def test_marks_queue_and_logs(self, conn):
        _queue(conn)
        smtp = MagicMock()
        res = send_weekly_digest(conn, _config(), now=AEDT_SLOT_1, smtp_client=smtp)
        assert res["sent"]
        args = smtp.sendmail.call_args[0]
        assert args[1] == ["owner@example.gov.au", "admin@example.gov.au"]
        assert conn.execute("SELECT COUNT(*) FROM notification_queue WHERE digest_id IS NULL"
                            ).fetchone()[0] == 0
        assert conn.execute("SELECT week_key FROM digest_log").fetchone()[0] == "2026-11-09"

    def test_smtp_failure_leaves_queue_unsent(self, conn, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _queue(conn)
        smtp = MagicMock()
        smtp.sendmail.side_effect = OSError("network down")
        res = send_weekly_digest(conn, _config(), now=AEDT_SLOT_1, smtp_client=smtp)
        assert not res["sent"] and res["fallback_file"]
        assert conn.execute("SELECT COUNT(*) FROM notification_queue WHERE digest_id IS NULL"
                            ).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM digest_log").fetchone()[0] == 0
        # The later Sunday slot retries.
        smtp.sendmail.side_effect = None
        assert send_weekly_digest(conn, _config(), now=AEDT_SLOT_2, smtp_client=smtp)["sent"]

    def test_already_sent_runs_not_repeated(self, conn):
        _queue(conn, run_id="week1", verdict="CHANGE_REQUIRED")
        smtp = MagicMock()
        send_weekly_digest(conn, _config(), now=AEDT_SLOT_1, smtp_client=smtp)
        _queue(conn, run_id="week2", verdict="NO_CHANGE")
        res = send_weekly_digest(conn, _config(), now=AEDT_SLOT_1.replace(day=15),
                                 smtp_client=smtp)
        assert res["sent"] and res["run_count"] == 1
        assert "no noteworthy changes" in res["subject"]

    def test_observation_mode_sends_nothing(self, conn):
        cfg = _config()
        cfg["pipeline"]["observation_mode"] = True
        smtp = MagicMock()
        assert not send_weekly_digest(conn, cfg, now=AEDT_SLOT_1, smtp_client=smtp)["sent"]
        smtp.sendmail.assert_not_called()


class TestHelpers:
    def test_run_stats(self):
        rows = [
            {"stage_reached": "stage1", "outcome": "no_change",
             "details": {"stages": {"metadata_probe": {"decision": "not_due"}}}},
            {"stage_reached": "stage4", "outcome": "no_change", "details": {"stages": {}}},
            {"stage_reached": "scrape", "outcome": "error", "details": {"stages": {}}},
        ]
        assert run_stats(rows) == {"sources_total": 3, "sources_checked": 2,
                                   "changes_detected": 1, "errors": 1}

    @pytest.mark.parametrize("bad", [
        {"cadence": "daily"},
        {"weekly_digest": {"day": "funday"}},
        {"weekly_digest": {"time": "9.30"}},
        {"weekly_digest": {"timezone": "Mars/Base"}},
    ])
    def test_config_validation(self, bad):
        n = {**_config()["notifications"], **bad}
        with pytest.raises(ConfigError):
            _validate_notifications(n)

    def test_health_send_email_false_does_not_email(self, conn, tmp_path, monkeypatch):
        import src.health as health
        monkeypatch.chdir(tmp_path)
        sent = MagicMock()
        monkeypatch.setattr(health, "_send_health_alert", sent)
        rows = [{"source_id": "s", "outcome": "error", "stage_reached": "scrape",
                 "details": {"stages": {}}}]
        res = health.evaluate_and_alert("r1", rows, 0, [], conn, _config(), send_email=False)
        assert res.alerts and not sent.called
        assert (tmp_path / "data/logs/health_alert_r1.txt").exists()
