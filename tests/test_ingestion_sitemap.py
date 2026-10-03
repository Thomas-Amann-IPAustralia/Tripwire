"""
tests/test_ingestion_sitemap.py

Tests for ingestion/sitemap.py — URL extraction, bootstrap fetch with the
Selenium fallback, and the block-signature detection path.  No real network
calls or browser processes are started.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ingestion.sitemap import (
    BROWSER_USER_AGENT,
    build_sitemap_from_urls,
    fetch_sitemap_xml,
    parse_sitemap_xml,
)
from src.errors import PermanentError, RetryableError


SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://ipfirstresponse.ipaustralia.gov.au/B1012</loc></url>
  <url><loc>https://ipfirstresponse.ipaustralia.gov.au/A0042</loc></url>
</urlset>
"""


def _make_session(status_code: int, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    session = MagicMock()
    session.get.return_value = resp
    return session


# ---------------------------------------------------------------------------
# parse_sitemap_xml
# ---------------------------------------------------------------------------


def test_parse_sitemap_xml_extracts_loc_urls():
    urls = parse_sitemap_xml(SITEMAP_XML)
    assert urls == [
        "https://ipfirstresponse.ipaustralia.gov.au/B1012",
        "https://ipfirstresponse.ipaustralia.gov.au/A0042",
    ]


def test_parse_sitemap_xml_ignores_non_http_loc():
    xml = "<urlset><url><loc>ftp://example.com/a</loc></url></urlset>"
    assert parse_sitemap_xml(xml) == []


# ---------------------------------------------------------------------------
# fetch_sitemap_xml — requests happy path
# ---------------------------------------------------------------------------


def test_fetch_sitemap_xml_requests_success_sends_browser_ua():
    session = _make_session(200, SITEMAP_XML)

    result = fetch_sitemap_xml("https://example.com/sitemap.xml", session)

    assert result == SITEMAP_XML
    call_kwargs = session.get.call_args.kwargs
    assert call_kwargs["headers"]["User-Agent"] == BROWSER_USER_AGENT


# ---------------------------------------------------------------------------
# fetch_sitemap_xml — Selenium fallback paths
# ---------------------------------------------------------------------------


def test_fetch_sitemap_xml_falls_back_to_selenium_on_timeout():
    session = MagicMock()
    session.get.side_effect = TimeoutError("read timeout")

    with patch(
        "src.scraper.fetch_raw_with_selenium", return_value=SITEMAP_XML
    ) as mock_sel:
        result = fetch_sitemap_xml("https://example.com/sitemap.xml", session)

    assert result == SITEMAP_XML
    mock_sel.assert_called_once()


def test_fetch_sitemap_xml_falls_back_on_block_signature():
    block_body = "<html><body>Just a moment...</body></html>"
    session = _make_session(200, block_body)

    with patch(
        "src.scraper.fetch_raw_with_selenium", return_value=SITEMAP_XML
    ) as mock_sel:
        result = fetch_sitemap_xml("https://example.com/sitemap.xml", session)

    assert result == SITEMAP_XML
    mock_sel.assert_called_once()


def test_fetch_sitemap_xml_falls_back_on_non_200_status():
    session = _make_session(503)

    with patch(
        "src.scraper.fetch_raw_with_selenium", return_value=SITEMAP_XML
    ) as mock_sel:
        result = fetch_sitemap_xml("https://example.com/sitemap.xml", session)

    assert result == SITEMAP_XML
    mock_sel.assert_called_once()


def test_fetch_sitemap_xml_raises_retryable_when_both_fail():
    session = MagicMock()
    session.get.side_effect = ConnectionError("dns failure")

    with patch("src.scraper.fetch_raw_with_selenium", return_value=None):
        with pytest.raises(RetryableError):
            fetch_sitemap_xml("https://example.com/sitemap.xml", session)


def test_fetch_sitemap_xml_raises_permanent_when_selenium_also_blocked():
    session = MagicMock()
    session.get.side_effect = ConnectionError("dns failure")
    block_body = "<html><body>Access denied</body></html>"

    with patch("src.scraper.fetch_raw_with_selenium", return_value=block_body):
        with pytest.raises(PermanentError):
            fetch_sitemap_xml("https://example.com/sitemap.xml", session)


def test_fetch_sitemap_xml_force_selenium_skips_requests():
    session = MagicMock()

    with patch(
        "src.scraper.fetch_raw_with_selenium", return_value=SITEMAP_XML
    ) as mock_sel:
        result = fetch_sitemap_xml(
            "https://example.com/sitemap.xml", session, force_selenium=True
        )

    session.get.assert_not_called()
    mock_sel.assert_called_once()
    assert result == SITEMAP_XML


# ---------------------------------------------------------------------------
# build_sitemap_from_urls — ensure fresh bootstrap produces rows
# ---------------------------------------------------------------------------


def test_build_sitemap_from_urls_bootstraps_empty_state(tmp_path):
    urls = parse_sitemap_xml(SITEMAP_XML)
    rows = build_sitemap_from_urls(urls, [], tmp_path / "snapshots")

    assert len(rows) == 2
    page_ids = {r["page_id"] for r in rows}
    assert page_ids == {"B1012", "A0042"}
    for r in rows:
        assert r["url"].startswith("https://")
        assert r["snapshot_path"].endswith(".md")
        assert r["last_modified"] == ""
        assert r["last_checked"] == ""
