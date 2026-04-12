"""
tests/test_stage3_diff.py

Tests for src/stage3_diff — FRL explainer retrieval, diff generation routing,
snapshot rotation, and normalisation.  No real network calls or filesystem
side-effects outside of tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from src.stage3_diff import (
    _fetch_frl_explainer,
    _FRL_API_BASE,
    _FRL_ES_TYPES,
    _normalise_diff_text,
    generate_diff,
    load_previous_snapshot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source(source_id: str = "frl_trademarks",
                 url: str = "https://www.legislation.gov.au/Series/C2004A00913") -> dict:
    return {"source_id": source_id, "url": url, "source_type": "frl"}


def _make_session_with_responses(*responses) -> MagicMock:
    """Return a session whose successive .get() calls return *responses."""
    session = MagicMock()
    session.get.side_effect = list(responses)
    return session


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


# ---------------------------------------------------------------------------
# _fetch_frl_explainer — API endpoint correctness
# ---------------------------------------------------------------------------


class TestFetchFrlExplainerEndpoints:
    def test_uses_correct_api_base_url(self):
        """Calls must go to api.prod.legislation.gov.au, not www.legislation.gov.au."""
        session = _make_session_with_responses(
            _meta_response(200),
            _binary_response(),
        )
        with patch("src.scraper.extract_plain_text_from_docx", return_value="ES text"):
            _fetch_frl_explainer(_make_source(), session)

        first_url = session.get.call_args_list[0][0][0]
        assert first_url.startswith(_FRL_API_BASE), (
            f"Expected API base {_FRL_API_BASE!r}, got {first_url!r}"
        )

    def test_metadata_request_uses_accept_json_header(self):
        """Metadata (existence check) request must send Accept: application/json."""
        session = _make_session_with_responses(
            _meta_response(200),
            _binary_response(),
        )
        with patch("src.scraper.extract_plain_text_from_docx", return_value="ES text"):
            _fetch_frl_explainer(_make_source(), session)

        meta_call = session.get.call_args_list[0]
        assert meta_call[1]["headers"]["Accept"] == "application/json"

    def test_binary_download_has_no_accept_json_header(self):
        """Binary download must NOT send Accept: application/json."""
        session = _make_session_with_responses(
            _meta_response(200),
            _binary_response(),
        )
        with patch("src.scraper.extract_plain_text_from_docx", return_value="ES text"):
            _fetch_frl_explainer(_make_source(), session)

        bin_call = session.get.call_args_list[1]
        headers = bin_call[1].get("headers", {})
        assert headers.get("Accept") != "application/json"

    def test_endpoint_contains_title_id(self):
        """The document endpoint must embed the titleId from the source URL."""
        url = "https://www.legislation.gov.au/Series/C2004A00652"
        session = _make_session_with_responses(
            _meta_response(200),
            _binary_response(),
        )
        with patch("src.scraper.extract_plain_text_from_docx", return_value="ES text"):
            _fetch_frl_explainer(_make_source(url=url), session)

        first_url = session.get.call_args_list[0][0][0]
        assert "C2004A00652" in first_url

    def test_endpoint_specifies_type_es(self):
        """The first attempt must use type='ES'."""
        session = _make_session_with_responses(
            _meta_response(200),
            _binary_response(),
        )
        with patch("src.scraper.extract_plain_text_from_docx", return_value="ES text"):
            _fetch_frl_explainer(_make_source(), session)

        first_url = session.get.call_args_list[0][0][0]
        assert "type='ES'" in first_url

    def test_endpoint_specifies_format_word(self):
        """The document endpoint must request Word format."""
        session = _make_session_with_responses(
            _meta_response(200),
            _binary_response(),
        )
        with patch("src.scraper.extract_plain_text_from_docx", return_value="ES text"):
            _fetch_frl_explainer(_make_source(), session)

        first_url = session.get.call_args_list[0][0][0]
        assert "format='Word'" in first_url

    def test_endpoint_specifies_asatspecification_latest(self):
        """The document endpoint must use asatspecification='Latest'."""
        session = _make_session_with_responses(
            _meta_response(200),
            _binary_response(),
        )
        with patch("src.scraper.extract_plain_text_from_docx", return_value="ES text"):
            _fetch_frl_explainer(_make_source(), session)

        first_url = session.get.call_args_list[0][0][0]
        assert "Latest" in first_url


# ---------------------------------------------------------------------------
# _fetch_frl_explainer — success path
# ---------------------------------------------------------------------------


class TestFetchFrlExplainerSuccess:
    def test_returns_extracted_text_on_success(self):
        session = _make_session_with_responses(
            _meta_response(200),   # ES metadata check
            _binary_response(),    # Binary DOCX download
        )
        with patch("src.scraper.extract_plain_text_from_docx",
                   return_value="This instrument amends the Trade Marks Regulations."):
            text, err = _fetch_frl_explainer(_make_source(), session)

        assert err is None
        assert text == "This instrument amends the Trade Marks Regulations."

    def test_uses_extract_plain_text_from_docx(self):
        """Must call extract_plain_text_from_docx with the binary content."""
        docx_bytes = b"real-docx-content"
        session = _make_session_with_responses(
            _meta_response(200),
            _binary_response(content=docx_bytes),
        )
        with patch("src.scraper.extract_plain_text_from_docx",
                   return_value="text") as mock_extract:
            _fetch_frl_explainer(_make_source(), session)

        mock_extract.assert_called_once_with(docx_bytes)


# ---------------------------------------------------------------------------
# _fetch_frl_explainer — SupplementaryES fallback
# ---------------------------------------------------------------------------


class TestFetchFrlExplainerSupplementaryFallback:
    def test_falls_back_to_supplementary_es_when_es_is_404(self):
        """If ES returns 404, SupplementaryES should be tried next."""
        session = _make_session_with_responses(
            _meta_response(404),   # ES: not found
            _meta_response(200),   # SupplementaryES: found
            _binary_response(),    # Binary download
        )
        with patch("src.scraper.extract_plain_text_from_docx", return_value="supp text"):
            text, err = _fetch_frl_explainer(_make_source(), session)

        assert err is None
        assert text == "supp text"

        # The second metadata call should use SupplementaryES.
        second_url = session.get.call_args_list[1][0][0]
        assert "SupplementaryES" in second_url

    def test_returns_error_when_both_types_are_404(self):
        """If both ES and SupplementaryES are 404, return (None, error_message)."""
        session = _make_session_with_responses(
            _meta_response(404),   # ES: not found
            _meta_response(404),   # SupplementaryES: not found
        )
        text, err = _fetch_frl_explainer(_make_source(), session)

        assert text is None
        assert err is not None
        assert "C2004A00913" in err


# ---------------------------------------------------------------------------
# _fetch_frl_explainer — error paths
# ---------------------------------------------------------------------------


class TestFetchFrlExplainerErrors:
    def test_returns_error_on_api_exception(self):
        session = MagicMock()
        session.get.side_effect = Exception("connection refused")

        text, err = _fetch_frl_explainer(_make_source(), session)

        assert text is None
        assert err is not None
        assert "C2004A00913" in err

    def test_returns_error_on_download_failure(self):
        """If metadata check passes but binary download fails, return error."""
        dl_resp = MagicMock()
        dl_resp.status_code = 503
        dl_resp.raise_for_status.side_effect = Exception("503 Service Unavailable")

        session = _make_session_with_responses(
            _meta_response(200),
            dl_resp,
        )
        text, err = _fetch_frl_explainer(_make_source(), session)

        assert text is None
        assert err is not None

    def test_returns_error_on_empty_extracted_text(self):
        """If mammoth returns empty string, return (None, error_message)."""
        session = _make_session_with_responses(
            _meta_response(200),
            _binary_response(),
        )
        with patch("src.scraper.extract_plain_text_from_docx", return_value=""):
            text, err = _fetch_frl_explainer(_make_source(), session)

        assert text is None
        assert err is not None

    def test_returns_error_on_extract_exception(self):
        """If extract_plain_text_from_docx raises, return (None, error_message)."""
        session = _make_session_with_responses(
            _meta_response(200),
            _binary_response(),
        )
        with patch("src.scraper.extract_plain_text_from_docx",
                   side_effect=RuntimeError("mammoth not installed")):
            text, err = _fetch_frl_explainer(_make_source(), session)

        assert text is None
        assert err is not None


# ---------------------------------------------------------------------------
# generate_diff — FRL routing (integration-level, uses tmp_path)
# ---------------------------------------------------------------------------


class TestGenerateFrlDiff:
    def test_diff_type_is_explainer_when_es_available(self, tmp_path):
        source = _make_source()
        session = _make_session_with_responses(
            _meta_response(200),
            _binary_response(),
        )
        with patch("src.scraper.extract_plain_text_from_docx",
                   return_value="Explanatory Statement text here."):
            result = generate_diff(
                source,
                new_text="",
                previous_text=None,
                diff_lines=[],
                snapshot_dir=tmp_path,
                session=session,
            )

        assert result.diff_type == "explainer"
        assert result.source_type == "frl"
        assert "Explanatory Statement" in result.normalised_diff
        assert result.diff_path is not None
        assert "_explainer_" in result.diff_path

    def test_diff_falls_back_to_unified_diff_when_no_es(self, tmp_path):
        """When the ES is unavailable, fall back to a webpage-style diff."""
        source = _make_source()
        # Both ES and SupplementaryES return 404.
        session = _make_session_with_responses(
            _meta_response(404),
            _meta_response(404),
        )
        result = generate_diff(
            source,
            new_text="new legislation text",
            previous_text="old legislation text",
            diff_lines=["--- old\n", "+++ new\n", "-old legislation text\n",
                        "+new legislation text\n"],
            snapshot_dir=tmp_path,
            session=session,
        )

        assert result.diff_type == "unified_diff_fallback"
        assert result.source_type == "frl"
        assert len(result.warnings) > 0

    def test_explainer_file_written_to_snapshot_dir(self, tmp_path):
        source = _make_source()
        session = _make_session_with_responses(
            _meta_response(200),
            _binary_response(),
        )
        with patch("src.scraper.extract_plain_text_from_docx",
                   return_value="ES content"):
            result = generate_diff(
                source,
                new_text="",
                previous_text=None,
                diff_lines=[],
                snapshot_dir=tmp_path,
                session=session,
            )

        explainer_path = Path(result.diff_path)
        assert explainer_path.exists()
        assert explainer_path.read_text(encoding="utf-8") == "ES content"


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
