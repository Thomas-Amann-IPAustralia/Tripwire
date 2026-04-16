"""
tests/test_stage1_metadata.py

Tests for src/stage1_metadata — FRL probe logic, signal comparison,
frequency checks, and source registry loading.  No real network calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.stage1_metadata import (
    _compare_signals,
    _extract_frl_title_id,
    _probe_frl,
    is_due_for_check,
    probe_source,
    ProbeResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source(source_id: str = "frl_trademarks",
                 url: str = "https://www.legislation.gov.au/Series/C2004A00913",
                 source_type: str = "frl") -> dict:
    return {
        "source_id": source_id,
        "url": url,
        "source_type": source_type,
        "importance": 1.0,
        "check_frequency": "weekly",
    }


def _make_session(status_code: int = 200, json_body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    session = MagicMock()
    session.get.return_value = resp
    return session


_LATEST_VERSION = {
    "registerId": "F2024C00123",
    "compilationNumber": "42",
    "start": "2024-03-01",
    "titleId": "C2004A00913",
    "isCurrent": True,
}


# ---------------------------------------------------------------------------
# _probe_frl — happy paths
# ---------------------------------------------------------------------------


class TestProbeFrl:
    def test_first_run_returns_unknown(self):
        """No stored signals on first run → 'unknown' (proceed to Stage 2)."""
        session = _make_session(json_body=_LATEST_VERSION)
        result = _probe_frl(_make_source(), stored=None, session=session)

        assert result.decision == "unknown"
        assert result.signals["register_id"] == "F2024C00123"
        assert result.signals["compilation_number"] == "42"
        assert result.signals["start"] == "2024-03-01"

    def test_unchanged_when_register_id_matches(self):
        """Stored registerId matches new → 'unchanged'."""
        stored = {
            "register_id": "F2024C00123",
            "compilation_number": "42",
            "start": "2024-03-01",
        }
        session = _make_session(json_body=_LATEST_VERSION)
        result = _probe_frl(_make_source(), stored=stored, session=session)

        assert result.decision == "unchanged"

    def test_changed_when_register_id_differs(self):
        """New registerId differs from stored → 'changed'."""
        stored = {
            "register_id": "F2023C00099",
            "compilation_number": "41",
            "start": "2023-11-01",
        }
        session = _make_session(json_body=_LATEST_VERSION)
        result = _probe_frl(_make_source(), stored=stored, session=session)

        assert result.decision == "changed"

    def test_correct_api_endpoint_called(self):
        """Verifies the correct FRL API URL and Accept header are used."""
        session = _make_session(json_body=_LATEST_VERSION)
        _probe_frl(_make_source(), stored=None, session=session)

        call_args = session.get.call_args
        url_called = call_args[0][0]
        headers_called = call_args[1].get("headers", {})

        assert "api.prod.legislation.gov.au" in url_called
        assert "C2004A00913" in url_called
        assert "Latest" in url_called
        assert headers_called.get("Accept") == "application/json"

    def test_title_id_extracted_from_series_url(self):
        """titleId is correctly parsed from the legacy /Series/<id> URL."""
        url = "https://www.legislation.gov.au/Series/C2004A00652"
        session = _make_session(json_body={**_LATEST_VERSION, "titleId": "C2004A00652"})
        _probe_frl(_make_source(url=url), stored=None, session=session)

        url_called = session.get.call_args[0][0]
        assert "C2004A00652" in url_called

    def test_title_id_extracted_from_current_url(self):
        """titleId is correctly parsed from the current /<id>/latest/text URL."""
        url = "https://www.legislation.gov.au/C2004A04969/latest/text"
        session = _make_session(json_body={**_LATEST_VERSION, "titleId": "C2004A04969"})
        _probe_frl(_make_source(url=url), stored=None, session=session)

        url_called = session.get.call_args[0][0]
        assert "C2004A04969" in url_called
        # The legacy bug sent 'text' as the titleId; guard against regression.
        assert "titleId='text'" not in url_called

    def test_title_id_extracted_from_asmade_url(self):
        """titleId is correctly parsed from the /<id>/asmade/text URL variant."""
        url = "https://www.legislation.gov.au/C2021A00013/asmade/text"
        session = _make_session(json_body={**_LATEST_VERSION, "titleId": "C2021A00013"})
        _probe_frl(_make_source(url=url), stored=None, session=session)

        url_called = session.get.call_args[0][0]
        assert "C2021A00013" in url_called


class TestExtractFrlTitleId:
    def test_current_url_format(self):
        assert _extract_frl_title_id(
            "https://www.legislation.gov.au/C2004A04969/latest/text"
        ) == "C2004A04969"

    def test_asmade_url_format(self):
        assert _extract_frl_title_id(
            "https://www.legislation.gov.au/C2021A00013/asmade/text"
        ) == "C2021A00013"

    def test_f_prefixed_id(self):
        assert _extract_frl_title_id(
            "https://www.legislation.gov.au/F1996B00084/latest/text"
        ) == "F1996B00084"

    def test_legacy_series_format(self):
        assert _extract_frl_title_id(
            "https://www.legislation.gov.au/Series/C2004A00913"
        ) == "C2004A00913"

    def test_empty_path_returns_none(self):
        assert _extract_frl_title_id("https://www.legislation.gov.au/") is None


# ---------------------------------------------------------------------------
# _probe_frl — fallback paths
# ---------------------------------------------------------------------------


class TestProbeFrlFallbacks:
    def test_falls_back_to_head_on_api_error(self):
        """If the FRL API raises an exception, fall back to HTTP HEAD probe."""
        session = MagicMock()
        session.get.side_effect = [
            Exception("connection refused"),   # FRL API call fails
            _head_response(),                  # HEAD probe succeeds
        ]
        result = _probe_frl(_make_source(), stored=None, session=session)

        # Two calls: first the FRL API, then the HEAD fallback.
        assert session.get.call_count == 2
        # Decision comes from _probe_webpage; no stored signals → unknown.
        assert result.decision in ("unknown", "unchanged", "changed")

    def test_falls_back_to_head_on_http_error(self):
        """If the FRL API returns HTTP 5xx, fall back to HEAD probe."""
        frl_resp = MagicMock()
        frl_resp.status_code = 503
        frl_resp.raise_for_status.side_effect = Exception("503 Service Unavailable")

        head_resp = _head_response()
        session = MagicMock()
        session.get.side_effect = [frl_resp, head_resp]

        result = _probe_frl(_make_source(), stored=None, session=session)
        assert session.get.call_count == 2

    def test_falls_back_when_register_id_missing(self):
        """If the API response has no registerId, fall back to HEAD probe."""
        session = MagicMock()
        session.get.side_effect = [
            _json_response({}),   # FRL API: no registerId
            _head_response(),     # HEAD fallback
        ]
        result = _probe_frl(_make_source(), stored=None, session=session)
        assert session.get.call_count == 2


# ---------------------------------------------------------------------------
# _compare_signals
# ---------------------------------------------------------------------------


class TestCompareSignals:
    def test_no_new_signals_returns_unknown(self):
        assert _compare_signals({}, {"register_id": "X"}) == "unknown"

    def test_no_stored_signals_returns_unknown(self):
        assert _compare_signals({"register_id": "X"}, {}) == "unknown"

    def test_matching_signals_returns_unchanged(self):
        s = {"register_id": "F2024C00123", "compilation_number": "5"}
        assert _compare_signals(s, s) == "unchanged"

    def test_changed_signal_returns_changed(self):
        new = {"register_id": "F2024C00456"}
        old = {"register_id": "F2024C00123"}
        assert _compare_signals(new, old) == "changed"

    def test_no_common_keys_returns_unknown(self):
        assert _compare_signals({"a": "1"}, {"b": "2"}) == "unknown"


# ---------------------------------------------------------------------------
# is_due_for_check
# ---------------------------------------------------------------------------


class TestIsDueForCheck:
    def test_never_checked_is_due(self):
        source = {"check_frequency": "weekly"}
        assert is_due_for_check(source, None) is True

    def test_checked_today_not_due(self):
        from datetime import datetime, timezone
        today = datetime.now(tz=timezone.utc).date().isoformat()
        source = {"check_frequency": "weekly"}
        assert is_due_for_check(source, today) is False

    def test_checked_8_days_ago_is_due_weekly(self):
        from datetime import datetime, timedelta, timezone
        eight_days_ago = (
            datetime.now(tz=timezone.utc).date() - timedelta(days=8)
        ).isoformat()
        source = {"check_frequency": "weekly"}
        assert is_due_for_check(source, eight_days_ago) is True

    def test_invalid_last_checked_is_due(self):
        source = {"check_frequency": "weekly"}
        assert is_due_for_check(source, "not-a-date") is True


# ---------------------------------------------------------------------------
# probe_source — routing
# ---------------------------------------------------------------------------


class TestProbeSource:
    def test_frl_source_routes_to_frl_probe(self):
        source = _make_source(source_type="frl")
        session = _make_session(json_body=_LATEST_VERSION)

        result = probe_source(source, stored_signals=None, session=session)

        url_called = session.get.call_args[0][0]
        assert "api.prod.legislation.gov.au" in url_called

    def test_exception_in_probe_returns_unknown(self):
        source = _make_source(source_type="frl")
        session = MagicMock()
        # Both FRL and HEAD fallback fail.
        session.get.side_effect = Exception("total failure")

        result = probe_source(source, stored_signals=None, session=session)
        assert result.decision == "unknown"
        assert result.error is not None


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _json_response(body: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


def _head_response(status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"ETag": '"abc123"', "Content-Length": "50000"}
    resp.raise_for_status.return_value = None
    return resp
