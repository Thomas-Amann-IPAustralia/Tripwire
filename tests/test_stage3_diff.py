"""
tests/test_stage3_diff.py

Tests for src/stage3_diff — FRL explainer retrieval, diff generation routing,
snapshot rotation, and normalisation.  No real network calls or filesystem
side-effects outside of tmp_path.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from src.stage3_diff import (
    _discover_amending_via_affect_api,
    _discover_em_url,
    _download_es_via_web,
    _extract_act_id_from_markdown,
    _extract_amending_instruments,
    _extract_between_anchored_markers,
    _extract_bill_id,
    _extract_frl_title_id,
    _fetch_act_bill_summary,
    _fetch_em_outline,
    _fetch_frl_explainer,
    _fetch_frl_version_with_reasons,
    _fetch_regulation_explainer,
    _FRL_API_BASE,
    _FRL_ES_TYPES,
    _FRL_STOP_HEADINGS,
    _FRL_WEB_BASE,
    _get_asmade_date,
    _PARLINFO_MIN_WORDS,
    _normalise_diff_text,
    _truncate_at_es_stop_heading,
    generate_diff,
    load_previous_snapshot,
)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_AMENDING_REGULATION_TITLE_ID = "F2024L01299"
_AMENDING_ACT_TITLE_ID = "C2026A00001"

_VERSION_WITH_REGULATION_REASON = {
    "registerId": "F2026C00009",
    "compilationNumber": "53",
    "reasons": [
        {
            "affect": "Amend",
            "affectedByTitle": {
                "titleId": _AMENDING_REGULATION_TITLE_ID,
                "seriesType": "SLI",
                "name": "Trade Marks Amendment Regulations 2024",
            },
        }
    ],
}

_VERSION_WITH_ACT_REASON = {
    "registerId": "C2026C00071",
    "compilationNumber": "9",
    "reasons": [
        {
            "affect": "Amend",
            "affectedByTitle": {
                "titleId": _AMENDING_ACT_TITLE_ID,
                "seriesType": "Act",
                "name": "Combatting Antisemitism Act 2026",
            },
        }
    ],
}

_VERSION_NO_REASONS = {
    "registerId": "F2026C00001",
    "compilationNumber": "1",
    "reasons": [],
}

_TITLES_RESPONSE_WITH_BILL_URI = {
    "id": _AMENDING_ACT_TITLE_ID,
    "originatingBillUri": (
        "https://parlinfo.aph.gov.au/parlInfo/search/display/display.w3p;"
        "query=Id%3A%22legislation%2Fbillhome%2Fr7421\""
    ),
}

_TITLES_RESPONSE_NO_BILL_URI = {
    "id": _AMENDING_ACT_TITLE_ID,
    "originatingBillUri": None,
}


def _make_source(
    source_id: str = "frl_trademarks",
    url: str = "https://www.legislation.gov.au/F1996B00084/latest/text",
) -> dict:
    return {"source_id": source_id, "url": url, "source_type": "frl"}


def _make_source_act(
    source_id: str = "frl_abf_act",
    url: str = "https://www.legislation.gov.au/C2015A00040/latest/text",
) -> dict:
    return {"source_id": source_id, "url": url, "source_type": "frl"}


def _json_resp(body: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


def _docx_response(
    content: bytes = b"fake-docx-bytes",
    *,
    status_code: int = 200,
    content_type: str = "application/octet-stream",
) -> MagicMock:
    """Build a session.get response carrying a binary DOCX (or 404)."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"Content-Type": content_type}
    resp.content = content
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


def _json_metadata_response(body: dict | None = None) -> MagicMock:
    """A 200 response carrying JSON metadata (no file) — treated as a miss."""
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"Content-Type": "application/json"}
    resp.content = b'{"type":"ES"}'
    resp.json.return_value = body or {"type": "ES", "format": "Word"}
    resp.raise_for_status.return_value = None
    return resp


def _html_response(body: bytes = b"<html>error</html>") -> MagicMock:
    """A 200 HTML response (used to test the web-fallback HTML rejection)."""
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"Content-Type": "text/html; charset=utf-8"}
    resp.content = body
    resp.raise_for_status.return_value = None
    return resp


def _session(*responses) -> MagicMock:
    """Build a session whose successive .get() calls return *responses* in order."""
    s = MagicMock()
    s.get.side_effect = list(responses)
    return s


# ---------------------------------------------------------------------------
# _extract_frl_title_id
# ---------------------------------------------------------------------------


class TestExtractFrlTitleId:
    def test_extracts_from_latest_text_url(self):
        assert _extract_frl_title_id(
            "https://www.legislation.gov.au/F1996B00084/latest/text"
        ) == "F1996B00084"

    def test_extracts_from_asmade_text_url(self):
        assert _extract_frl_title_id(
            "https://www.legislation.gov.au/C2015A00040/asmade/text"
        ) == "C2015A00040"

    def test_extracts_from_series_url(self):
        assert _extract_frl_title_id(
            "https://www.legislation.gov.au/Series/C2004A00913"
        ) == "C2004A00913"

    def test_returns_none_for_empty_url(self):
        assert _extract_frl_title_id("") is None


# ---------------------------------------------------------------------------
# _truncate_at_es_stop_heading
# ---------------------------------------------------------------------------


class TestTruncateAtEsStopHeading:
    def test_truncates_at_attachment_a(self):
        text = "Intro text.\n\nMore content here.\n\nAttachment A\nDetail that should be excluded."
        result = _truncate_at_es_stop_heading(text)
        assert "Intro text." in result
        assert "More content here." in result
        assert "Detail that should be excluded." not in result
        assert "Attachment A" not in result

    def test_truncates_at_schedule_1(self):
        text = "Preamble.\n\nSchedule 1\nAmendments..."
        result = _truncate_at_es_stop_heading(text)
        assert "Preamble." in result
        assert "Amendments" not in result

    def test_truncates_at_notes_on_sections(self):
        text = "Overview of changes.\n\nNotes on sections\nSection 3: ..."
        result = _truncate_at_es_stop_heading(text)
        assert "Overview of changes." in result
        assert "Section 3" not in result

    def test_case_insensitive_match(self):
        text = "Content.\n\nATTACHMENT A\nExcluded."
        result = _truncate_at_es_stop_heading(text)
        assert "Content." in result
        assert "Excluded." not in result

    def test_returns_full_text_when_no_stop_heading(self):
        text = "This document has no stop headings at all. Just content."
        assert _truncate_at_es_stop_heading(text) == text

    def test_does_not_truncate_on_long_line_with_phrase(self):
        """A body-text line >100 chars containing 'Attachment A' should not trigger truncation."""
        long_line = "The amendment inserts a reference to Attachment A of the principal instrument, which sets out the detailed technical specifications for the process."
        text = f"Intro.\n\n{long_line}\n\nSchedule 1\nExcluded."
        result = _truncate_at_es_stop_heading(text)
        # Long line should NOT trigger truncation; "Schedule 1" on its own line should.
        assert long_line in result
        assert "Excluded." not in result

    def test_handles_heading_with_suffix(self):
        """Headings like 'Attachment A – Background' should still trigger truncation."""
        text = "Content.\n\nAttachment A – Background\nExcluded."
        result = _truncate_at_es_stop_heading(text)
        assert "Content." in result
        assert "Excluded." not in result


# ---------------------------------------------------------------------------
# _extract_amending_instruments
# ---------------------------------------------------------------------------


class TestExtractAmendingInstruments:
    def test_extracts_regulation_amending_instrument(self):
        instruments = _extract_amending_instruments(_VERSION_WITH_REGULATION_REASON)
        assert len(instruments) == 1
        assert instruments[0]["title_id"] == _AMENDING_REGULATION_TITLE_ID
        assert instruments[0]["series_type"] == "SLI"

    def test_extracts_act_amending_instrument(self):
        instruments = _extract_amending_instruments(_VERSION_WITH_ACT_REASON)
        assert len(instruments) == 1
        assert instruments[0]["title_id"] == _AMENDING_ACT_TITLE_ID
        assert instruments[0]["series_type"] == "Act"

    def test_returns_empty_for_no_reasons(self):
        assert _extract_amending_instruments(_VERSION_NO_REASONS) == []

    def test_ignores_non_amend_reasons(self):
        version = {
            "reasons": [
                {"affect": "AsMade", "affectedByTitle": {"titleId": "X2024A00001", "seriesType": "Act"}},
                {"affect": "ChangeDate", "affectedByTitle": {"titleId": "Y2024A00001", "seriesType": "Act"}},
            ]
        }
        assert _extract_amending_instruments(version) == []

    def test_handles_multiple_amend_reasons(self):
        version = {
            "reasons": [
                {
                    "affect": "Amend",
                    "affectedByTitle": {"titleId": "F2024L00001", "seriesType": "SLI"},
                },
                {
                    "affect": "Amend",
                    "affectedByTitle": {"titleId": "F2024L00002", "seriesType": "SR"},
                },
            ]
        }
        instruments = _extract_amending_instruments(version)
        assert len(instruments) == 2
        assert instruments[0]["title_id"] == "F2024L00001"
        assert instruments[1]["title_id"] == "F2024L00002"

    def test_falls_back_to_amendedByTitle_field(self):
        """If affectedByTitle is absent, amendedByTitle should be used."""
        version = {
            "reasons": [
                {
                    "affect": "Amend",
                    "amendedByTitle": {"titleId": "F2024L00099", "seriesType": "SLI"},
                }
            ]
        }
        instruments = _extract_amending_instruments(version)
        assert len(instruments) == 1
        assert instruments[0]["title_id"] == "F2024L00099"

    def test_missing_series_type_returns_empty_string(self):
        version = {
            "reasons": [
                {
                    "affect": "Amend",
                    "affectedByTitle": {"titleId": "F2024L00001"},
                }
            ]
        }
        instruments = _extract_amending_instruments(version)
        assert instruments[0]["series_type"] == ""

    def test_layer1_register_id_is_act(self):
        """Layer 1: registerId matching the Act series pattern is recognised."""
        version = {"registerId": "C2023A00074", "reasons": []}
        instruments = _extract_amending_instruments(version)
        assert len(instruments) == 1
        assert instruments[0]["title_id"] == "C2023A00074"
        assert instruments[0]["series_type"] == "Act"

    def test_layer1_register_id_non_act_pattern_ignored(self):
        """Compilation registerIds (e.g. C2026C00071) don't match Layer 1."""
        version = {"registerId": "C2026C00071", "reasons": []}
        assert _extract_amending_instruments(version) == []

    def test_layer2_markdown_fallback(self):
        """When both structured fields are absent, scan the markdown text."""
        version = {
            "reasons": [
                {
                    "affect": "Amend",
                    "markdown": "Amended by C2023A00074 on its commencement date.",
                }
            ]
        }
        instruments = _extract_amending_instruments(version)
        assert len(instruments) == 1
        assert instruments[0]["title_id"] == "C2023A00074"

    def test_layer2_independent_field_check(self):
        """Both affectedByTitle AND amendedByTitle are kept when both populated
        with distinct titleIds."""
        version = {
            "reasons": [
                {
                    "affect": "Amend",
                    "affectedByTitle": {"titleId": "C2024A00010", "seriesType": "Act"},
                    "amendedByTitle": {"titleId": "C2024A00011", "seriesType": "Act"},
                }
            ]
        }
        instruments = _extract_amending_instruments(version)
        assert {i["title_id"] for i in instruments} == {"C2024A00010", "C2024A00011"}

    def test_deduplication_across_layers(self):
        """Same titleId in registerId and reasons must appear only once."""
        version = {
            "registerId": "C2023A00074",
            "reasons": [
                {
                    "affect": "Amend",
                    "affectedByTitle": {"titleId": "C2023A00074", "seriesType": "Act"},
                }
            ],
        }
        instruments = _extract_amending_instruments(version)
        assert len(instruments) == 1


# ---------------------------------------------------------------------------
# _extract_act_id_from_markdown
# ---------------------------------------------------------------------------


class TestExtractActIdFromMarkdown:
    def test_returns_first_act_id(self):
        assert _extract_act_id_from_markdown(
            "Amended by C2023A00074 and C2024A00010"
        ) == "C2023A00074"

    def test_returns_none_when_no_act_id(self):
        assert _extract_act_id_from_markdown("No id here") is None
        assert _extract_act_id_from_markdown("") is None
        assert _extract_act_id_from_markdown(None) is None

    def test_ignores_non_act_patterns(self):
        # F2024L01299 is a regulation series id, not an Act id.
        assert _extract_act_id_from_markdown("Reference: F2024L01299") is None


# ---------------------------------------------------------------------------
# _discover_amending_via_affect_api
# ---------------------------------------------------------------------------


class TestDiscoverAmendingViaAffectApi:
    def test_affects_search_success(self):
        body = {
            "value": [
                {
                    "amendedByTitle": {"titleId": "C2024A00010", "seriesType": "Act"},
                    "effectiveDate": "2024-06-01",
                }
            ]
        }
        s = _session(_json_resp(body))
        result = _discover_amending_via_affect_api(
            "C2015A00040", s, compilation_start_date="2024-06-01",
        )
        assert len(result) == 1
        assert result[0]["title_id"] == "C2024A00010"
        # First call should hit _AffectsSearch.
        first_url = s.get.call_args_list[0][0][0]
        assert "_AffectsSearch" in first_url
        assert "C2015A00040" in first_url

    def test_falls_back_to_affect_endpoint_on_404(self):
        body = {
            "value": [
                {"amendedByTitleId": "C2024A00099"}
            ]
        }
        s = _session(_json_resp({}, status_code=404), _json_resp(body))
        result = _discover_amending_via_affect_api(
            "C2015A00040", s, compilation_start_date=None,
        )
        assert len(result) == 1
        assert result[0]["title_id"] == "C2024A00099"
        second_url = s.get.call_args_list[1][0][0]
        assert "/v1/Affect?" in second_url

    def test_filters_by_compilation_date(self):
        body = {
            "value": [
                {"amendedByTitle": {"titleId": "C2024A00010"}, "effectiveDate": "2023-01-01"},
                {"amendedByTitle": {"titleId": "C2024A00099"}, "effectiveDate": "2024-06-01"},
            ]
        }
        s = _session(_json_resp(body))
        result = _discover_amending_via_affect_api(
            "C2015A00040", s, compilation_start_date="2024-06-01",
        )
        ids = {i["title_id"] for i in result}
        assert ids == {"C2024A00099"}

    def test_returns_empty_when_both_endpoints_404(self):
        s = _session(_json_resp({}, status_code=404), _json_resp({}, status_code=404))
        assert _discover_amending_via_affect_api("C2015A00040", s) == []

    def test_returns_empty_on_network_error(self):
        s = MagicMock()
        s.get.side_effect = Exception("boom")
        assert _discover_amending_via_affect_api("C2015A00040", s) == []


# ---------------------------------------------------------------------------
# _discover_em_url and _fetch_em_outline
# ---------------------------------------------------------------------------


class TestDiscoverEmUrl:
    def test_finds_em_link(self):
        url = (
            "https://parlinfo.aph.gov.au/parlInfo/search/display/display.w3p;"
            "query=Id%3A%22legislation%2Fems%2Fr7421_ems_uuid%22"
        )
        html = f'<a href="{url}">EM</a>'
        assert _discover_em_url(html) == url

    def test_returns_none_when_no_em_link(self):
        assert _discover_em_url("<html><body>no em</body></html>") is None
        assert _discover_em_url("") is None

    def test_strips_trailing_quotes(self):
        url = (
            "https://parlinfo.aph.gov.au/parlInfo/search/display/display.w3p;"
            "query=Id%3A%22legislation%2Fems%2Frabc%22"
        )
        # The regex stops at quote/whitespace/angle bracket; an extra rstrip
        # guard removes any trailing quote that snuck in.
        result = _discover_em_url(f'href="{url}"')
        assert result is not None
        assert not result.endswith('"')


class TestFetchEmOutline:
    def test_returns_outline_text(self):
        em_text = _em_plain("Outline body text describing the bill.")
        with patch(
            "src.stage3_diff._fetch_parlinfo_text",
            return_value=("<html/>", em_text),
        ):
            text = _fetch_em_outline("https://parlinfo.example/em", MagicMock())
        assert text is not None
        assert "Outline body text" in text

    def test_returns_none_when_fetch_fails(self):
        with patch(
            "src.stage3_diff._fetch_parlinfo_text",
            return_value=("", ""),
        ):
            assert _fetch_em_outline("https://parlinfo.example/em", MagicMock()) is None

    def test_returns_none_when_markers_missing(self):
        with patch(
            "src.stage3_diff._fetch_parlinfo_text",
            return_value=("<html/>", "Some plain text without the markers."),
        ):
            assert _fetch_em_outline("https://parlinfo.example/em", MagicMock()) is None


# ---------------------------------------------------------------------------
# _fetch_frl_version_with_reasons — API call structure
# ---------------------------------------------------------------------------


class TestFetchFrlVersionWithReasons:
    def test_calls_find_function_endpoint(self):
        # Versions/Find returns a single Version object directly (not wrapped).
        s = _session(_json_resp(_VERSION_WITH_REGULATION_REASON))
        _fetch_frl_version_with_reasons("F1996B00084", s)
        url_called = s.get.call_args[0][0]
        assert "api.prod.legislation.gov.au/v1/Versions/Find(" in url_called
        assert "titleId='F1996B00084'" in url_called
        assert "asAtSpecification='Latest'" in url_called

    def test_does_not_use_list_endpoint_query_params(self):
        """Regression: the live API returns 400 for the list endpoint with
        $filter+$expand; we must call the function endpoint instead."""
        s = _session(_json_resp(_VERSION_WITH_REGULATION_REASON))
        _fetch_frl_version_with_reasons("F1996B00084", s)
        params = s.get.call_args[1].get("params", {}) or {}
        assert "$filter" not in params
        assert "$expand" not in params

    def test_sends_accept_json_header(self):
        s = _session(_json_resp(_VERSION_WITH_REGULATION_REASON))
        _fetch_frl_version_with_reasons("F1996B00084", s)
        headers = s.get.call_args[1].get("headers", {})
        assert headers.get("Accept") == "application/json"

    def test_returns_version_dict(self):
        s = _session(_json_resp(_VERSION_WITH_REGULATION_REASON))
        result = _fetch_frl_version_with_reasons("F1996B00084", s)
        assert result["registerId"] == "F2026C00009"
        assert "reasons" in result

    def test_raises_when_empty_body(self):
        s = _session(_json_resp({}))
        with pytest.raises(ValueError, match="No latest version found"):
            _fetch_frl_version_with_reasons("F1996B00084", s)

    def test_raises_on_http_error(self):
        s = _session(_json_resp({}, status_code=503))
        with pytest.raises(Exception):
            _fetch_frl_version_with_reasons("F1996B00084", s)


# ---------------------------------------------------------------------------
# _extract_bill_id
# ---------------------------------------------------------------------------


class TestExtractBillId:
    def test_extracts_bill_id_from_encoded_uri(self):
        uri = (
            "https://parlinfo.aph.gov.au/parlInfo/search/display/display.w3p;"
            "query=Id%3A%22legislation%2Fbillhome%2Fr7421\""
        )
        assert _extract_bill_id(uri) == "r7421"

    def test_extracts_bill_id_from_decoded_uri(self):
        uri = 'https://parlinfo.aph.gov.au/parlInfo/search/display/display.w3p;query=Id:"legislation/billhome/r7137"'
        assert _extract_bill_id(uri) == "r7137"

    def test_returns_none_when_no_billhome(self):
        assert _extract_bill_id("https://parlinfo.aph.gov.au/something/else") is None

    def test_returns_none_for_empty_string(self):
        assert _extract_bill_id("") is None


# ---------------------------------------------------------------------------
# _extract_between_anchored_markers (replaces the old _scrape_text_between)
# ---------------------------------------------------------------------------


class TestExtractBetweenAnchoredMarkers:
    def test_extracts_between_short_heading_lines(self):
        text = "Intro\nSummary\nThis is the summary content.\nProgress of bill\nRest."
        result = _extract_between_anchored_markers(
            text, [r"summary"], [r"progress\s+of\s+bill"],
        )
        assert "This is the summary content." in result
        assert "Intro" not in result
        assert "Rest." not in result

    def test_case_insensitive_headings(self):
        text = "SUMMARY\nContent here.\nPROGRESS OF BILL\nAfter."
        result = _extract_between_anchored_markers(
            text, [r"summary"], [r"progress\s+of\s+bill"],
        )
        assert "Content here." in result

    def test_returns_empty_when_start_marker_missing(self):
        assert _extract_between_anchored_markers(
            "Some text", [r"summary"], [r"contents"],
        ) == ""

    def test_returns_rest_when_end_marker_missing(self):
        text = "Summary\nBody line one.\nBody line two."
        result = _extract_between_anchored_markers(
            text, [r"summary"], [r"contents"],
        )
        assert "Body line one." in result
        assert "Body line two." in result

    def test_inline_mention_does_not_match(self):
        """A long body line containing 'Summary' must NOT count as the heading."""
        long_body = (
            "This paragraph mentions Summary in the middle of the sentence "
            "but is far too long to be a section heading line — the 80-char "
            "guard rejects it."
        )
        text = f"{long_body}\nKey points\nThe real key points.\nContents\nTOC"
        result = _extract_between_anchored_markers(
            text, [r"key\s+points"], [r"contents"],
        )
        assert "real key points" in result
        # The long body line shouldn't have been treated as a Summary heading
        # so no content from it leaks into a different match.

    def test_alternate_start_patterns(self):
        text = "Outline\nThe outline body.\nFinancial Impact\nMoney."
        result = _extract_between_anchored_markers(
            text,
            [r"general\s+outline", r"outline"],
            [r"financial\s+impact(?:\s+statement)?"],
        )
        assert "outline body" in result
        assert "Money." not in result


# ---------------------------------------------------------------------------
# _fetch_regulation_explainer — single-call flow with AsMade + web fallback
# ---------------------------------------------------------------------------


class TestFetchRegulationExplainer:
    def test_uses_asmade_in_endpoint(self):
        s = _session(_docx_response())
        with patch("src.scraper.extract_plain_text_from_docx", return_value="ES text"):
            _fetch_regulation_explainer(_AMENDING_REGULATION_TITLE_ID, s)
        first_url = s.get.call_args_list[0][0][0]
        assert _AMENDING_REGULATION_TITLE_ID in first_url
        assert "asatspecification='AsMade'" in first_url
        assert "asatspecification='Latest'" not in first_url

    def test_returns_extracted_text_on_first_call(self):
        s = _session(_docx_response())
        with patch("src.scraper.extract_plain_text_from_docx", return_value="Plain ES text."):
            text, err = _fetch_regulation_explainer(_AMENDING_REGULATION_TITLE_ID, s)
        assert err is None
        assert text == "Plain ES text."
        # Single call: the metadata probe is gone.
        assert s.get.call_count == 1

    def test_truncates_at_attachment_a(self):
        es_content = "Purpose of the instrument.\n\nAttachment A\nDetail not wanted."
        s = _session(_docx_response())
        with patch("src.scraper.extract_plain_text_from_docx", return_value=es_content):
            text, err = _fetch_regulation_explainer(_AMENDING_REGULATION_TITLE_ID, s)
        assert err is None
        assert "Purpose of the instrument." in text
        assert "Detail not wanted." not in text

    def test_falls_back_to_supplementary_es_on_404(self):
        s = _session(
            _docx_response(status_code=404),  # ES: 404
            _docx_response(),                  # SupplementaryES: success
        )
        with patch("src.scraper.extract_plain_text_from_docx", return_value="Supplementary text"):
            text, err = _fetch_regulation_explainer(_AMENDING_REGULATION_TITLE_ID, s)
        assert err is None
        assert text == "Supplementary text"
        supp_url = s.get.call_args_list[1][0][0]
        assert "SupplementaryES" in supp_url

    def test_treats_json_content_type_as_miss(self):
        """200 + Content-Type: application/json is metadata-only and must not be
        treated as a successful binary download."""
        s = _session(
            _json_metadata_response(),         # ES → JSON metadata, not a file
            _docx_response(),                  # SupplementaryES → real DOCX
        )
        with patch("src.scraper.extract_plain_text_from_docx", return_value="Supp text"):
            text, err = _fetch_regulation_explainer(_AMENDING_REGULATION_TITLE_ID, s)
        assert err is None
        assert text == "Supp text"

    def test_falls_back_to_web_url_when_api_404s(self):
        version_resp = _json_resp({"start": "2024-10-14T00:00:00"})
        s = _session(
            _docx_response(status_code=404),   # ES API 404
            _docx_response(status_code=404),   # SupplementaryES API 404
            version_resp,                       # AsMade-date lookup
            _docx_response(content=b"web-docx-bytes"),  # web URL ES success
        )
        with patch("src.scraper.extract_plain_text_from_docx", return_value="Web ES text"):
            text, err = _fetch_regulation_explainer(_AMENDING_REGULATION_TITLE_ID, s)
        assert err is None
        assert text == "Web ES text"
        # Last call should be the public web URL with the as-made date.
        web_url = s.get.call_args_list[-1][0][0]
        assert _FRL_WEB_BASE in web_url
        assert "/asmade/2024-10-14/es/original/word" in web_url

    def test_web_fallback_rejects_html_error_pages(self):
        """A 200 response with HTML content < 50 KB must be rejected as an error
        page rather than written as an ES."""
        version_resp = _json_resp({"start": "2024-10-14T00:00:00"})
        s = _session(
            _docx_response(status_code=404),
            _docx_response(status_code=404),
            version_resp,
            _html_response(b"<html>404 page</html>"),  # ES web URL: HTML
            _html_response(b"<html>404 page</html>"),  # SupplementaryES: HTML
        )
        text, err = _fetch_regulation_explainer(_AMENDING_REGULATION_TITLE_ID, s)
        assert text is None
        assert err is not None

    def test_returns_error_when_api_404_and_no_asmade_date(self):
        """If we cannot resolve the AsMade date the web fallback is skipped."""
        s = _session(
            _docx_response(status_code=404),
            _docx_response(status_code=404),
            _json_resp({}, status_code=503),   # AsMade-date lookup fails
        )
        text, err = _fetch_regulation_explainer(_AMENDING_REGULATION_TITLE_ID, s)
        assert text is None
        assert err is not None
        assert _AMENDING_REGULATION_TITLE_ID in err

    def test_returns_error_on_empty_extracted_text(self):
        s = _session(_docx_response())
        with patch("src.scraper.extract_plain_text_from_docx", return_value=""):
            text, err = _fetch_regulation_explainer(_AMENDING_REGULATION_TITLE_ID, s)
        # Empty extraction is recorded as an error string; it does not raise.
        assert text is None
        assert err is not None


# ---------------------------------------------------------------------------
# _get_asmade_date
# ---------------------------------------------------------------------------


class TestGetAsmadeDate:
    def test_extracts_yyyy_mm_dd_from_start(self):
        s = _session(_json_resp({"start": "2024-10-14T00:00:00"}))
        assert _get_asmade_date("F2024L01299", s) == "2024-10-14"

    def test_returns_none_when_start_missing(self):
        s = _session(_json_resp({}))
        assert _get_asmade_date("F2024L01299", s) is None

    def test_returns_none_on_http_error(self):
        s = _session(_json_resp({}, status_code=503))
        assert _get_asmade_date("F2024L01299", s) is None

    def test_returns_none_on_network_exception(self):
        s = MagicMock()
        s.get.side_effect = Exception("boom")
        assert _get_asmade_date("F2024L01299", s) is None


# ---------------------------------------------------------------------------
# _download_es_via_web
# ---------------------------------------------------------------------------


class TestDownloadEsViaWeb:
    def test_returns_bytes_on_es_success(self):
        s = _session(_docx_response(content=b"web-bytes"))
        content, err = _download_es_via_web("F2024L01299", "2024-10-14", s)
        assert err is None
        assert content == b"web-bytes"
        url = s.get.call_args_list[0][0][0]
        assert "/asmade/2024-10-14/es/original/word" in url

    def test_falls_back_to_supplementaryes_when_es_404(self):
        s = _session(
            _docx_response(status_code=404),                # ES → 404
            _docx_response(content=b"supp-bytes"),          # SupplementaryES → success
        )
        content, err = _download_es_via_web("F2024L01299", "2024-10-14", s)
        assert err is None
        assert content == b"supp-bytes"
        supp_url = s.get.call_args_list[1][0][0]
        assert "/supplementaryes/original/word" in supp_url

    def test_rejects_html_error_pages(self):
        s = _session(_html_response(b"<html>err</html>"), _html_response(b"<html>err</html>"))
        content, err = _download_es_via_web("F2024L01299", "2024-10-14", s)
        assert content is None
        assert err is not None

    def test_returns_none_when_both_404(self):
        s = _session(
            _docx_response(status_code=404),
            _docx_response(status_code=404),
        )
        content, err = _download_es_via_web("F2024L01299", "2024-10-14", s)
        assert content is None
        assert err is not None


# ---------------------------------------------------------------------------
# _fetch_act_bill_summary
# ---------------------------------------------------------------------------


def _bill_home_plain(summary_text: str) -> str:
    """Plain-text rendering of a bill home page with a Summary section."""
    return (
        "Bill home\n"
        "Summary\n"
        f"{summary_text}\n"
        "Progress of bill\n"
        "Introduced in Senate..."
    )


def _bills_digest_plain(keypoints_text: str) -> str:
    return (
        "Bills Digest header\n"
        "Key Points\n"
        f"{keypoints_text}\n"
        "Contents\n"
        "Table of contents..."
    )


def _em_plain(outline_text: str) -> str:
    return (
        "Explanatory Memorandum\n"
        "General Outline\n"
        f"{outline_text}\n"
        "Financial Impact\n"
        "Nil financial impact."
    )


def _bill_home_html_with_em(em_url: str = "https://parlinfo.aph.gov.au/parlInfo/search/display/display.w3p;query=Id%3A%22legislation%2Fems%2Fr7421_ems_0ce6b86e-1206-484c-a5be-deadbeef%22") -> str:
    return f'<html><body><a href="{em_url}">Explanatory Memorandum</a></body></html>'


class TestFetchActBillSummary:
    """Four-tier waterfall: Bills Digest → long Summary → EM → short Summary."""

    def test_returns_error_when_no_originating_bill_uri(self):
        s = _session(_json_resp(_TITLES_RESPONSE_NO_BILL_URI))
        text, err = _fetch_act_bill_summary(_AMENDING_ACT_TITLE_ID, s)
        assert text is None
        assert err is not None
        assert _AMENDING_ACT_TITLE_ID in err

    def test_returns_error_on_titles_api_failure(self):
        s = _session(_json_resp({}, status_code=503))
        text, err = _fetch_act_bill_summary(_AMENDING_ACT_TITLE_ID, s)
        assert text is None
        assert err is not None

    def test_tier1_bills_digest_wins(self):
        """When the Bills Digest has Key Points, it takes priority over Summary."""
        digest_text = " ".join(["digestword"] * 150)
        long_summary = " ".join(["summword"] * 150)
        # First call: Bills Digest URL → digest plain text
        # (No further fetches expected because tier 1 wins.)
        with patch(
            "src.stage3_diff._fetch_parlinfo_text",
            return_value=("<html/>", _bills_digest_plain(digest_text)),
        ) as mock_fetch:
            text, err = _fetch_act_bill_summary(
                _AMENDING_ACT_TITLE_ID,
                MagicMock(),
                prefetched_title=_TITLES_RESPONSE_WITH_BILL_URI,
            )
        assert err is None
        assert "digestword" in text
        # Tier 1 wins; only the digest should have been fetched.
        assert mock_fetch.call_count == 1
        digest_url = mock_fetch.call_args_list[0][0][0]
        assert "r7421" in digest_url
        assert "BillId_Phrase" in digest_url
        assert "billsdgs" in digest_url

    def test_tier2_long_summary_when_no_digest(self):
        long_summary = " ".join(["sumword"] * 150)
        # Tier 1 returns ("","") (digest fetch failed); Tier 2 returns bill home page.
        with patch(
            "src.stage3_diff._fetch_parlinfo_text",
            side_effect=[
                ("", ""),                                        # Bills Digest miss
                ("<html/>", _bill_home_plain(long_summary)),      # bill home OK
            ],
        ):
            text, err = _fetch_act_bill_summary(
                _AMENDING_ACT_TITLE_ID,
                MagicMock(),
                prefetched_title=_TITLES_RESPONSE_WITH_BILL_URI,
            )
        assert err is None
        assert text is not None
        assert len(text.split()) >= _PARLINFO_MIN_WORDS
        assert "sumword" in text

    def test_tier3_em_when_summary_short(self):
        """EM kicks in when Bills Digest is absent and Summary is too short."""
        short_summary = " ".join(["sumword"] * 20)
        outline_text = " ".join(["outlineword"] * 80)
        em_url = (
            "https://parlinfo.aph.gov.au/parlInfo/search/display/display.w3p;"
            "query=Id%3A%22legislation%2Fems%2Fr7421_ems_uuid%22"
        )
        bill_home_html = _bill_home_html_with_em(em_url)
        with patch(
            "src.stage3_diff._fetch_parlinfo_text",
            side_effect=[
                ("", ""),                                          # Tier 1: digest miss
                (bill_home_html, _bill_home_plain(short_summary)),  # Tier 2: short summary
                ("<html/>", _em_plain(outline_text)),               # Tier 3: EM page
            ],
        ) as mock_fetch:
            text, err = _fetch_act_bill_summary(
                _AMENDING_ACT_TITLE_ID,
                MagicMock(),
                prefetched_title=_TITLES_RESPONSE_WITH_BILL_URI,
            )
        assert err is None
        assert text is not None
        assert "outlineword" in text
        # The third fetch should target the EM URL.
        em_call_url = mock_fetch.call_args_list[2][0][0]
        assert "legislation%2Fems%2F" in em_call_url

    def test_tier4_short_summary_fallback(self):
        """When digest, EM, and long Summary all fail, return the short Summary."""
        short_summary = " ".join(["sumword"] * 20)
        # Bill home HTML has no EM link → tier 3 cannot run.
        bill_home_html = "<html><body>no em here</body></html>"
        with patch(
            "src.stage3_diff._fetch_parlinfo_text",
            side_effect=[
                ("", ""),                                          # Tier 1: digest miss
                (bill_home_html, _bill_home_plain(short_summary)),  # Tier 2/4: short summary
            ],
        ):
            text, err = _fetch_act_bill_summary(
                _AMENDING_ACT_TITLE_ID,
                MagicMock(),
                prefetched_title=_TITLES_RESPONSE_WITH_BILL_URI,
            )
        assert err is None
        assert text is not None
        assert "sumword" in text
        # Tier 4 returns the short summary even though it falls below the threshold.
        assert len(text.split()) < _PARLINFO_MIN_WORDS

    def test_returns_error_when_all_tiers_fail(self):
        """Bill home fetch failure with no digest = unrecoverable failure."""
        with patch(
            "src.stage3_diff._fetch_parlinfo_text",
            side_effect=[
                ("", ""),  # Tier 1: digest miss
                ("", ""),  # Tier 2-4: bill home miss
            ],
        ):
            text, err = _fetch_act_bill_summary(
                _AMENDING_ACT_TITLE_ID,
                MagicMock(),
                prefetched_title=_TITLES_RESPONSE_WITH_BILL_URI,
            )
        assert text is None
        assert err is not None

    def test_prefetched_title_skips_titles_api(self):
        """Passing prefetched_title must avoid a fresh /v1/Titles call."""
        digest_text = " ".join(["digestword"] * 150)
        s = MagicMock()
        s.get.side_effect = AssertionError(
            "session.get must not be called when prefetched_title is supplied"
        )
        with patch(
            "src.stage3_diff._fetch_parlinfo_text",
            return_value=("<html/>", _bills_digest_plain(digest_text)),
        ):
            text, err = _fetch_act_bill_summary(
                _AMENDING_ACT_TITLE_ID,
                s,
                prefetched_title=_TITLES_RESPONSE_WITH_BILL_URI,
            )
        assert err is None
        assert text is not None


# ---------------------------------------------------------------------------
# _fetch_frl_explainer — full integration with new flow
# ---------------------------------------------------------------------------


class TestFetchFrlExplainer:
    def test_regulation_path_uses_version_api_then_es(self):
        """Full regulation path: Version API → find amending instrument → fetch ES."""
        es_text = "This instrument amends the Trade Marks Regulations."
        with (
            patch(
                "src.stage3_diff._fetch_frl_version_with_reasons",
                return_value=_VERSION_WITH_REGULATION_REASON,
            ),
            patch(
                "src.stage3_diff._fetch_regulation_explainer",
                return_value=(es_text, None),
            ) as mock_reg,
        ):
            s = MagicMock()
            text, err = _fetch_frl_explainer(_make_source(), s)

        assert err is None
        assert text == es_text
        mock_reg.assert_called_once_with(_AMENDING_REGULATION_TITLE_ID, s)

    def test_act_path_uses_version_api_then_bill_summary(self):
        """Full Act path: Version API → find amending Act → fetch bill summary."""
        bill_text = "The bill makes changes to the ABF Act."
        with (
            patch(
                "src.stage3_diff._fetch_frl_version_with_reasons",
                return_value=_VERSION_WITH_ACT_REASON,
            ),
            patch(
                "src.stage3_diff._fetch_act_bill_summary",
                return_value=(bill_text, None),
            ) as mock_act,
        ):
            s = MagicMock()
            text, err = _fetch_frl_explainer(_make_source_act(), s)

        assert err is None
        assert text == bill_text
        mock_act.assert_called_once_with(
            _AMENDING_ACT_TITLE_ID, s, prefetched_title=None
        )

    def test_returns_error_when_no_amending_instruments(self):
        with (
            patch(
                "src.stage3_diff._fetch_frl_version_with_reasons",
                return_value=_VERSION_NO_REASONS,
            ),
            patch(
                "src.stage3_diff._discover_amending_via_affect_api",
                return_value=[],
            ),
        ):
            text, err = _fetch_frl_explainer(_make_source(), MagicMock())
        assert text is None
        assert err is not None
        assert "F1996B00084" in err

    def test_affect_api_fallback_runs_when_reasons_empty(self):
        """When the version reasons array is empty, the Affect API is consulted."""
        version_no_reasons = {
            "registerId": "F2026C00001",
            "compilationNumber": "1",
            "start": "2024-06-01T00:00:00",
            "reasons": [],
        }
        amending = [{"title_id": "F2024L00099", "series_type": "SLI"}]
        with (
            patch(
                "src.stage3_diff._fetch_frl_version_with_reasons",
                return_value=version_no_reasons,
            ),
            patch(
                "src.stage3_diff._discover_amending_via_affect_api",
                return_value=amending,
            ) as mock_affect,
            patch(
                "src.stage3_diff._fetch_regulation_explainer",
                return_value=("Recovered text.", None),
            ),
        ):
            text, err = _fetch_frl_explainer(_make_source(), MagicMock())
        assert err is None
        assert text == "Recovered text."
        # The Affect API helper must have been called with the principal title
        # id and the compilation start date.
        _, kwargs = mock_affect.call_args
        assert kwargs.get("compilation_start_date") == "2024-06-01"

    def test_returns_error_when_versions_api_fails(self):
        with patch(
            "src.stage3_diff._fetch_frl_version_with_reasons",
            side_effect=Exception("API unreachable"),
        ):
            text, err = _fetch_frl_explainer(_make_source(), MagicMock())
        assert text is None
        assert err is not None
        assert "F1996B00084" in err

    def test_returns_error_for_unextractable_title_id(self):
        source = {"source_id": "bad", "url": "https://example.com/", "source_type": "frl"}
        text, err = _fetch_frl_explainer(source, MagicMock())
        assert text is None
        assert err is not None

    def test_concatenates_multiple_amending_instruments(self):
        version_multi = {
            "reasons": [
                {
                    "affect": "Amend",
                    "affectedByTitle": {"titleId": "F2024L00001", "seriesType": "SLI"},
                },
                {
                    "affect": "Amend",
                    "affectedByTitle": {"titleId": "F2024L00002", "seriesType": "SLI"},
                },
            ]
        }
        with (
            patch(
                "src.stage3_diff._fetch_frl_version_with_reasons",
                return_value=version_multi,
            ),
            patch(
                "src.stage3_diff._fetch_regulation_explainer",
                side_effect=[
                    ("Text from instrument 1.", None),
                    ("Text from instrument 2.", None),
                ],
            ),
        ):
            text, err = _fetch_frl_explainer(_make_source(), MagicMock())

        assert err is None
        assert "Text from instrument 1." in text
        assert "Text from instrument 2." in text
        assert "---" in text

    def test_missing_series_type_routes_via_titles_api_to_regulation(self):
        """Empty seriesType triggers a Titles API lookup; SLI routes to regulation."""
        version = {
            "reasons": [
                {
                    "affect": "Amend",
                    "affectedByTitle": {"titleId": "F2024L00099", "seriesType": ""},
                }
            ]
        }
        with (
            patch(
                "src.stage3_diff._fetch_frl_version_with_reasons",
                return_value=version,
            ),
            patch(
                "src.stage3_diff._fetch_frl_title",
                return_value={"id": "F2024L00099", "seriesType": "SLI"},
            ) as mock_title,
            patch(
                "src.stage3_diff._fetch_regulation_explainer",
                return_value=("Regulation text.", None),
            ) as mock_reg,
            patch("src.stage3_diff._fetch_act_bill_summary") as mock_act,
        ):
            text, err = _fetch_frl_explainer(_make_source(), MagicMock())

        mock_title.assert_called_once()
        mock_reg.assert_called_once()
        mock_act.assert_not_called()
        assert text == "Regulation text."

    def test_missing_series_type_routes_via_titles_api_to_act(self):
        """Empty seriesType resolved to 'Act' via Titles API routes to the Act path."""
        version = {
            "reasons": [
                {
                    "affect": "Amend",
                    "affectedByTitle": {"titleId": "C2099A00001", "seriesType": None},
                }
            ]
        }
        title_payload = {
            "id": "C2099A00001",
            "seriesType": "Act",
            "originatingBillUri": (
                "https://parlinfo.aph.gov.au/parlInfo/search/display/display.w3p;"
                "query=Id%3A%22legislation%2Fbillhome%2Fr9999\""
            ),
        }
        with (
            patch(
                "src.stage3_diff._fetch_frl_version_with_reasons",
                return_value=version,
            ),
            patch(
                "src.stage3_diff._fetch_frl_title",
                return_value=title_payload,
            ) as mock_title,
            patch(
                "src.stage3_diff._fetch_act_bill_summary",
                return_value=("Bill summary text.", None),
            ) as mock_act,
            patch("src.stage3_diff._fetch_regulation_explainer") as mock_reg,
        ):
            text, err = _fetch_frl_explainer(_make_source(), MagicMock())

        mock_title.assert_called_once()
        mock_reg.assert_not_called()
        mock_act.assert_called_once()
        # The prefetched Title must be forwarded so _fetch_act_bill_summary
        # does not re-call the Titles API.
        _, kwargs = mock_act.call_args
        assert kwargs.get("prefetched_title") is title_payload
        assert text == "Bill summary text."

    def test_titles_api_failure_defaults_to_regulation_path(self):
        """If Titles API fallback raises, routing defaults to the regulation path."""
        version = {
            "reasons": [
                {
                    "affect": "Amend",
                    "affectedByTitle": {"titleId": "F2024L00099", "seriesType": ""},
                }
            ]
        }
        with (
            patch(
                "src.stage3_diff._fetch_frl_version_with_reasons",
                return_value=version,
            ),
            patch(
                "src.stage3_diff._fetch_frl_title",
                side_effect=Exception("Titles API down"),
            ),
            patch(
                "src.stage3_diff._fetch_regulation_explainer",
                return_value=("Regulation text.", None),
            ) as mock_reg,
            patch("src.stage3_diff._fetch_act_bill_summary") as mock_act,
        ):
            text, err = _fetch_frl_explainer(_make_source(), MagicMock())

        mock_reg.assert_called_once()
        mock_act.assert_not_called()
        assert text == "Regulation text."

    def test_regulation_path_emits_debug_logs(self, caplog):
        """DEBUG logs must surface routing and endpoint detail for FRL regulations."""
        with (
            patch(
                "src.stage3_diff._fetch_frl_version_with_reasons",
                return_value=_VERSION_WITH_REGULATION_REASON,
            ),
            patch(
                "src.stage3_diff._fetch_regulation_explainer",
                return_value=("ES text.", None),
            ),
            caplog.at_level(logging.DEBUG, logger="src.stage3_diff"),
        ):
            _fetch_frl_explainer(_make_source(), MagicMock())

        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "regulation ES path" in messages
        assert _AMENDING_REGULATION_TITLE_ID in messages

    def test_populated_series_type_does_not_trigger_titles_api(self):
        """When seriesType is already populated on the reason, skip the Titles lookup."""
        with (
            patch(
                "src.stage3_diff._fetch_frl_version_with_reasons",
                return_value=_VERSION_WITH_ACT_REASON,
            ),
            patch("src.stage3_diff._fetch_frl_title") as mock_title,
            patch(
                "src.stage3_diff._fetch_act_bill_summary",
                return_value=("Bill summary.", None),
            ) as mock_act,
        ):
            _fetch_frl_explainer(_make_source_act(), MagicMock())

        mock_title.assert_not_called()
        mock_act.assert_called_once()
        # No prefetched_title is passed because _fetch_frl_explainer did not need one.
        _, kwargs = mock_act.call_args
        assert kwargs.get("prefetched_title") is None


# ---------------------------------------------------------------------------
# generate_diff — FRL routing (integration-level, uses tmp_path)
# ---------------------------------------------------------------------------


class TestGenerateFrlDiff:
    def test_diff_type_is_explainer_when_es_available(self, tmp_path):
        es_text = "Explanatory Statement text here."
        with (
            patch(
                "src.stage3_diff._fetch_frl_explainer",
                return_value=(es_text, None),
            ),
        ):
            result = generate_diff(
                _make_source(),
                new_text="",
                previous_text=None,
                diff_lines=[],
                snapshot_dir=tmp_path,
                session=MagicMock(),
            )

        assert result.diff_type == "explainer"
        assert result.source_type == "frl"
        assert "Explanatory Statement" in result.normalised_diff
        assert result.diff_path is not None
        assert "_explainer_" in result.diff_path

    def test_diff_falls_back_to_unified_diff_when_no_explainer(self, tmp_path):
        with (
            patch(
                "src.stage3_diff._fetch_frl_explainer",
                return_value=(None, "No explainer found."),
            ),
        ):
            result = generate_diff(
                _make_source(),
                new_text="new legislation text",
                previous_text="old legislation text",
                diff_lines=[
                    "--- old\n", "+++ new\n",
                    "-old legislation text\n", "+new legislation text\n",
                ],
                snapshot_dir=tmp_path,
                session=MagicMock(),
            )

        assert result.diff_type == "unified_diff_fallback"
        assert result.source_type == "frl"
        assert len(result.warnings) > 0

    def test_explainer_file_written_to_snapshot_dir(self, tmp_path):
        es_text = "ES content here."
        with patch(
            "src.stage3_diff._fetch_frl_explainer",
            return_value=(es_text, None),
        ):
            result = generate_diff(
                _make_source(),
                new_text="",
                previous_text=None,
                diff_lines=[],
                snapshot_dir=tmp_path,
                session=MagicMock(),
            )

        explainer_path = Path(result.diff_path)
        assert explainer_path.exists()
        assert explainer_path.read_text(encoding="utf-8") == es_text


# ---------------------------------------------------------------------------
# _normalise_diff_text
# ---------------------------------------------------------------------------


class TestNormaliseDiffText:
    def test_decodes_html_entities(self):
        result = _normalise_diff_text("&amp; &lt;trade mark&gt;")
        assert "&amp;" not in result
        assert "& <trade mark>" in result

    def test_collapses_whitespace(self):
        result = _normalise_diff_text("foo   bar\t\tbaz")
        assert "foo bar baz" in result

    def test_returns_non_empty_string(self):
        assert _normalise_diff_text("some text") != ""


# ---------------------------------------------------------------------------
# load_previous_snapshot
# ---------------------------------------------------------------------------


class TestLoadPreviousSnapshot:
    def test_returns_none_when_no_snapshot(self, tmp_path):
        result = load_previous_snapshot("missing_source", snapshot_dir=tmp_path)
        assert result is None

    def test_returns_content_when_snapshot_exists(self, tmp_path):
        snap_dir = tmp_path / "frl_trademarks"
        snap_dir.mkdir()
        (snap_dir / "frl_trademarks.txt").write_text("previous content", encoding="utf-8")

        result = load_previous_snapshot("frl_trademarks", snapshot_dir=tmp_path)
        assert result == "previous content"
