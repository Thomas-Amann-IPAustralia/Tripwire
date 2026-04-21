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
    _extract_bill_id,
    _extract_amending_instruments,
    _extract_frl_title_id,
    _fetch_act_bill_summary,
    _fetch_frl_explainer,
    _fetch_frl_version_with_reasons,
    _fetch_regulation_explainer,
    _FRL_API_BASE,
    _FRL_ES_TYPES,
    _FRL_STOP_HEADINGS,
    _PARLINFO_MIN_WORDS,
    _normalise_diff_text,
    _scrape_text_between,
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


def _meta_response(status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    resp.json.return_value = {"type": "ES", "format": "Word", "sizeInBytes": 12345}
    return resp


def _binary_response(content: bytes = b"fake-docx-bytes") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.content = content
    return resp


def _session(*responses) -> MagicMock:
    """Build a session whose successive .get() calls return *responses* in order."""
    s = MagicMock()
    s.get.side_effect = list(responses)
    return s


# Long enough parlinfo summary (> _PARLINFO_MIN_WORDS words)
_LONG_SUMMARY_TEXT = " ".join(["word"] * 120)
_SHORT_SUMMARY_TEXT = " ".join(["word"] * 50)

# Simulated parlinfo page wrapping a summary section
def _parlinfo_html(summary_text: str) -> str:
    return f"""
    <html><body>
    <h2>Summary</h2>
    <p>{summary_text}</p>
    <h2>Progress of bill</h2>
    <p>Introduced in Senate...</p>
    </body></html>
    """


def _bills_digest_html(keypoints_text: str) -> str:
    return f"""
    <html><body>
    <h2>Key points</h2>
    <p>{keypoints_text}</p>
    <h2>Contents</h2>
    <p>Table of contents...</p>
    </body></html>
    """


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


# ---------------------------------------------------------------------------
# _fetch_frl_version_with_reasons — API call structure
# ---------------------------------------------------------------------------


class TestFetchFrlVersionWithReasons:
    def test_calls_correct_endpoint(self):
        s = _session(_json_resp(_VERSION_WITH_REGULATION_REASON))
        _fetch_frl_version_with_reasons("F1996B00084", s)
        url_called = s.get.call_args[0][0]
        assert "api.prod.legislation.gov.au" in url_called
        assert "F1996B00084" in url_called
        assert "asAtSpecification='Latest'" in url_called
        assert "$expand=Reasons" in url_called

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
# _scrape_text_between
# ---------------------------------------------------------------------------


class TestScrapeTextBetween:
    def test_extracts_between_markers(self):
        text = "Intro\nSummary\nThis is the summary content.\nProgress of bill\nRest."
        result = _scrape_text_between(text, "Summary", "Progress of bill")
        assert "This is the summary content." in result
        assert "Intro" not in result
        assert "Rest." not in result

    def test_case_insensitive(self):
        text = "SUMMARY\nContent here.\nPROGRESS OF BILL\nAfter."
        result = _scrape_text_between(text, "Summary", "Progress of bill")
        assert "Content here." in result

    def test_returns_empty_when_start_not_found(self):
        assert _scrape_text_between("Some text", "Summary", "Progress of bill") == ""

    def test_returns_rest_when_end_not_found(self):
        text = "Summary\nContent without end marker."
        result = _scrape_text_between(text, "Summary", "Progress of bill")
        assert "Content without end marker." in result

    def test_extracts_key_points_to_contents(self):
        text = "Header\nKey points\nPoint one.\nPoint two.\nContents\nTable."
        result = _scrape_text_between(text, "Key points", "Contents")
        assert "Point one." in result
        assert "Table." not in result


# ---------------------------------------------------------------------------
# _fetch_regulation_explainer
# ---------------------------------------------------------------------------


class TestFetchRegulationExplainer:
    def test_uses_amending_title_id_in_endpoint(self):
        s = _session(_meta_response(200), _binary_response())
        with patch("src.scraper.extract_plain_text_from_docx", return_value="ES text"):
            _fetch_regulation_explainer(_AMENDING_REGULATION_TITLE_ID, s)
        first_url = s.get.call_args_list[0][0][0]
        assert _AMENDING_REGULATION_TITLE_ID in first_url

    def test_metadata_request_sends_accept_json(self):
        s = _session(_meta_response(200), _binary_response())
        with patch("src.scraper.extract_plain_text_from_docx", return_value="ES text"):
            _fetch_regulation_explainer(_AMENDING_REGULATION_TITLE_ID, s)
        meta_headers = s.get.call_args_list[0][1].get("headers", {})
        assert meta_headers.get("Accept") == "application/json"

    def test_binary_download_has_no_accept_json(self):
        s = _session(_meta_response(200), _binary_response())
        with patch("src.scraper.extract_plain_text_from_docx", return_value="ES text"):
            _fetch_regulation_explainer(_AMENDING_REGULATION_TITLE_ID, s)
        bin_headers = s.get.call_args_list[1][1].get("headers", {})
        assert bin_headers.get("Accept") != "application/json"

    def test_returns_extracted_text_on_success(self):
        s = _session(_meta_response(200), _binary_response())
        with patch("src.scraper.extract_plain_text_from_docx", return_value="Plain ES text."):
            text, err = _fetch_regulation_explainer(_AMENDING_REGULATION_TITLE_ID, s)
        assert err is None
        assert text == "Plain ES text."

    def test_truncates_at_attachment_a(self):
        es_content = "Purpose of the instrument.\n\nAttachment A\nDetail not wanted."
        s = _session(_meta_response(200), _binary_response())
        with patch("src.scraper.extract_plain_text_from_docx", return_value=es_content):
            text, err = _fetch_regulation_explainer(_AMENDING_REGULATION_TITLE_ID, s)
        assert err is None
        assert "Purpose of the instrument." in text
        assert "Detail not wanted." not in text

    def test_falls_back_to_supplementary_es(self):
        s = _session(
            _meta_response(404),   # ES: not found
            _meta_response(200),   # SupplementaryES: found
            _binary_response(),
        )
        with patch("src.scraper.extract_plain_text_from_docx", return_value="Supplementary text"):
            text, err = _fetch_regulation_explainer(_AMENDING_REGULATION_TITLE_ID, s)
        assert err is None
        assert text == "Supplementary text"
        supp_url = s.get.call_args_list[1][0][0]
        assert "SupplementaryES" in supp_url

    def test_returns_error_when_both_es_types_404(self):
        s = _session(_meta_response(404), _meta_response(404))
        text, err = _fetch_regulation_explainer(_AMENDING_REGULATION_TITLE_ID, s)
        assert text is None
        assert err is not None
        assert _AMENDING_REGULATION_TITLE_ID in err

    def test_returns_error_on_empty_extracted_text(self):
        s = _session(_meta_response(200), _binary_response())
        with patch("src.scraper.extract_plain_text_from_docx", return_value=""):
            text, err = _fetch_regulation_explainer(_AMENDING_REGULATION_TITLE_ID, s)
        assert text is None
        assert err is not None

    def test_returns_error_on_api_exception(self):
        s = MagicMock()
        s.get.side_effect = Exception("connection refused")
        text, err = _fetch_regulation_explainer(_AMENDING_REGULATION_TITLE_ID, s)
        assert text is None
        assert err is not None


# ---------------------------------------------------------------------------
# _fetch_act_bill_summary
# ---------------------------------------------------------------------------


class TestFetchActBillSummary:
    def _make_session_for_act(self, summary_text: str) -> MagicMock:
        """Session: Titles API → parlinfo page with summary → no digest."""
        titles_resp = _json_resp(_TITLES_RESPONSE_WITH_BILL_URI)
        parlinfo_resp = MagicMock()
        parlinfo_resp.status_code = 200
        parlinfo_resp.raise_for_status.return_value = None
        parlinfo_resp.text = _parlinfo_html(summary_text)
        s = MagicMock()
        s.get.side_effect = [titles_resp, parlinfo_resp]
        return s

    def test_calls_titles_api_with_amending_title_id(self):
        s = self._make_session_for_act(_LONG_SUMMARY_TEXT)
        with patch("src.stage3_diff._scrape_parlinfo_page", return_value=_parlinfo_html(_LONG_SUMMARY_TEXT)):
            _fetch_act_bill_summary(_AMENDING_ACT_TITLE_ID, s)
        titles_url = s.get.call_args_list[0][0][0]
        assert _AMENDING_ACT_TITLE_ID in titles_url
        assert "Titles" in titles_url

    def test_returns_summary_when_long_enough(self):
        with (
            patch("src.stage3_diff._scrape_parlinfo_page", return_value=_parlinfo_html(_LONG_SUMMARY_TEXT)),
        ):
            s = _session(_json_resp(_TITLES_RESPONSE_WITH_BILL_URI))
            text, err = _fetch_act_bill_summary(_AMENDING_ACT_TITLE_ID, s)
        assert err is None
        assert text is not None
        assert len(text.split()) >= _PARLINFO_MIN_WORDS

    def test_falls_back_to_bills_digest_when_summary_short(self):
        digest_content = " ".join(["digestword"] * 150)
        with (
            patch(
                "src.stage3_diff._scrape_parlinfo_page",
                side_effect=[
                    _parlinfo_html(_SHORT_SUMMARY_TEXT),   # first call: bill home page
                    _bills_digest_html(digest_content),    # second call: Bills Digest
                ],
            ),
        ):
            s = _session(_json_resp(_TITLES_RESPONSE_WITH_BILL_URI))
            text, err = _fetch_act_bill_summary(_AMENDING_ACT_TITLE_ID, s)
        assert err is None
        assert text is not None
        assert "digestword" in text

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

    def test_prefetched_title_skips_titles_api(self):
        """Passing prefetched_title must avoid a fresh /v1/Titles call."""
        with patch(
            "src.stage3_diff._scrape_parlinfo_page",
            return_value=_parlinfo_html(_LONG_SUMMARY_TEXT),
        ):
            s = MagicMock()
            s.get.side_effect = AssertionError(
                "session.get must not be called when prefetched_title is supplied"
            )
            text, err = _fetch_act_bill_summary(
                _AMENDING_ACT_TITLE_ID,
                s,
                prefetched_title=_TITLES_RESPONSE_WITH_BILL_URI,
            )
        assert err is None
        assert text is not None

    def test_bills_digest_url_contains_bill_id(self):
        digest_content = " ".join(["digestword"] * 150)
        digest_calls = []

        def mock_scrape(url, session):
            digest_calls.append(url)
            if "billhome" in url or "billId" not in url:
                return _parlinfo_html(_SHORT_SUMMARY_TEXT)
            return _bills_digest_html(digest_content)

        with patch("src.stage3_diff._scrape_parlinfo_page", side_effect=mock_scrape):
            s = _session(_json_resp(_TITLES_RESPONSE_WITH_BILL_URI))
            _fetch_act_bill_summary(_AMENDING_ACT_TITLE_ID, s)

        assert len(digest_calls) == 2
        digest_url = digest_calls[1]
        assert "r7421" in digest_url
        assert "BillId_Phrase" in digest_url
        assert "billsdgs" in digest_url


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
        with patch(
            "src.stage3_diff._fetch_frl_version_with_reasons",
            return_value=_VERSION_NO_REASONS,
        ):
            text, err = _fetch_frl_explainer(_make_source(), MagicMock())
        assert text is None
        assert err is not None
        assert "F1996B00084" in err

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
