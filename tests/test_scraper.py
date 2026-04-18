"""
tests/test_scraper.py

Tests for src/scraper — block detection, scrape_and_normalise routing,
Selenium fallback, and normalisation.  No real network calls or browser
processes are started.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.scraper import (
    _has_block_signature,
    _fetch_with_requests,
    extract_plain_text,
    normalise_text,
    scrape_and_normalise,
    scrape_url,
)
from src.errors import PermanentError, RetryableError


# ---------------------------------------------------------------------------
# _has_block_signature
# ---------------------------------------------------------------------------


def test_block_signature_cloudflare_just_a_moment():
    html = "<html><body><h1>Just a moment...</h1></body></html>"
    assert _has_block_signature(html) is True


def test_block_signature_cloudflare_ddos():
    assert _has_block_signature("DDoS protection by Cloudflare") is True


def test_block_signature_access_denied():
    assert _has_block_signature("<title>Access Denied</title>") is True


def test_block_signature_enable_js():
    assert _has_block_signature("Enable JavaScript and cookies to continue") is True


def test_block_signature_verifying_human():
    assert _has_block_signature("Verifying you are human. This may take a few seconds.") is True


def test_block_signature_site_cant_be_reached():
    assert _has_block_signature("This site can't be reached") is True


def test_block_signature_case_insensitive():
    # All checks should be case-insensitive.
    assert _has_block_signature("JUST A MOMENT") is True
    assert _has_block_signature("Access Denied") is True


def test_block_signature_clean_page():
    html = "<html><body><p>Welcome to the IP Australia website.</p></body></html>"
    assert _has_block_signature(html) is False


def test_block_signature_empty_string():
    assert _has_block_signature("") is False


# ---------------------------------------------------------------------------
# _fetch_with_requests
# ---------------------------------------------------------------------------


def _make_session(status_code: int, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    session = MagicMock()
    session.get.return_value = resp
    return session


def test_fetch_with_requests_returns_html_on_200():
    session = _make_session(200, "<html>content</html>")
    result = _fetch_with_requests("https://example.com", session)
    assert result == "<html>content</html>"


def test_fetch_with_requests_raises_on_404():
    session = _make_session(404)
    with pytest.raises(PermanentError):
        _fetch_with_requests("https://example.com", session)


def test_fetch_with_requests_raises_on_500():
    session = _make_session(500)
    with pytest.raises(RetryableError):
        _fetch_with_requests("https://example.com", session)


def test_fetch_with_requests_returns_none_on_connection_error():
    session = MagicMock()
    session.get.side_effect = ConnectionError("timed out")
    result = _fetch_with_requests("https://example.com", session)
    assert result is None


# ---------------------------------------------------------------------------
# scrape_url (backward-compat path)
# ---------------------------------------------------------------------------


def test_scrape_url_returns_plain_text():
    html = "<html><body><p>Hello world.</p></body></html>"
    session = _make_session(200, html)
    text = scrape_url("https://example.com", session)
    assert "Hello world" in text


def test_scrape_url_raises_captcha_on_block_page():
    html = "<html><body>Just a moment...</body></html>"
    session = _make_session(200, html)
    with pytest.raises(PermanentError):
        scrape_url("https://example.com", session)


def test_scrape_url_raises_on_http_error():
    session = _make_session(403)
    with pytest.raises(PermanentError):
        scrape_url("https://example.com", session)


# ---------------------------------------------------------------------------
# scrape_and_normalise — requests happy path
# ---------------------------------------------------------------------------

CLEAN_HTML = "<html><body><p>Australian IP legislation update.</p></body></html>"


def test_scrape_and_normalise_requests_success():
    session = _make_session(200, CLEAN_HTML)
    result = scrape_and_normalise("https://example.com", "webpage", session)
    assert isinstance(result, str)
    assert len(result) > 0


def test_scrape_and_normalise_frl_source_type():
    session = _make_session(200, CLEAN_HTML)
    result = scrape_and_normalise("https://legislation.gov.au/x", "frl", session)
    assert isinstance(result, str)


def test_scrape_and_normalise_rss_source_type():
    session = _make_session(200, CLEAN_HTML)
    result = scrape_and_normalise("https://example.com/feed", "rss", session)
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# scrape_and_normalise — Selenium fallback when block signature detected
# ---------------------------------------------------------------------------


def test_scrape_and_normalise_falls_back_to_selenium_on_block():
    """Requests returns a Cloudflare page; Selenium returns real content."""
    block_html = "<html><body>Just a moment...</body></html>"
    clean_html = "<html><body><p>Real content here.</p></body></html>"
    session = _make_session(200, block_html)

    with patch("src.scraper._fetch_with_selenium", return_value=clean_html) as mock_sel:
        result = scrape_and_normalise("https://example.com", "webpage", session)

    mock_sel.assert_called_once_with("https://example.com")
    assert "Real content" in result


def test_scrape_and_normalise_force_selenium_skips_requests():
    """force_selenium=True bypasses the requests attempt entirely."""
    session = MagicMock()
    clean_html = "<html><body><p>Selenium-only content.</p></body></html>"

    with patch("src.scraper._fetch_with_selenium", return_value=clean_html) as mock_sel:
        result = scrape_and_normalise(
            "https://example.com", "webpage", session, force_selenium=True
        )

    session.get.assert_not_called()
    mock_sel.assert_called_once_with("https://example.com")
    assert "Selenium-only content" in result


def test_scrape_and_normalise_raises_retryable_when_both_fail():
    """Both requests and Selenium return None → RetryableError."""
    session = MagicMock()
    session.get.side_effect = ConnectionError("timeout")

    with patch("src.scraper._fetch_with_selenium", return_value=None):
        with pytest.raises(RetryableError):
            scrape_and_normalise("https://example.com", "webpage", session)


def test_scrape_and_normalise_raises_captcha_when_selenium_also_blocked():
    """Selenium returns another block page → PermanentError (captcha)."""
    block_html = "<html><body>Access denied</body></html>"
    session = _make_session(200, block_html)

    with patch("src.scraper._fetch_with_selenium", return_value=block_html):
        with pytest.raises(PermanentError):
            scrape_and_normalise("https://example.com", "webpage", session)


# ---------------------------------------------------------------------------
# scrape_and_normalise — DOCX path
# ---------------------------------------------------------------------------


def test_scrape_and_normalise_docx_calls_mammoth(monkeypatch):
    docx_bytes = b"fake docx bytes"
    session = _make_session(200)
    session.get.return_value.content = docx_bytes

    with patch("src.scraper.extract_plain_text_from_docx", return_value="Extracted text") as mock_docx:
        result = scrape_and_normalise("https://example.com/doc.docx", "docx", session)

    mock_docx.assert_called_once_with(docx_bytes)
    assert result == "Extracted text"


def test_scrape_and_normalise_docx_raises_on_404():
    session = _make_session(404)
    with pytest.raises(PermanentError):
        scrape_and_normalise("https://example.com/doc.docx", "docx", session)


# ---------------------------------------------------------------------------
# normalise_text
# ---------------------------------------------------------------------------


def test_normalise_text_collapses_spaces():
    assert normalise_text("foo   bar") == "foo bar"


def test_normalise_text_replaces_nbsp():
    assert normalise_text("foo\xa0bar") == "foo bar"


def test_normalise_text_collapses_blank_lines():
    assert normalise_text("a\n\n\n\nb") == "a\n\nb"


def test_normalise_text_strips_leading_trailing():
    assert normalise_text("  hello  ") == "hello"


def test_normalise_text_preserves_case():
    assert normalise_text("IP Australia") == "IP Australia"


# ---------------------------------------------------------------------------
# _fetch_with_selenium — driver failure path
# ---------------------------------------------------------------------------


def test_fetch_with_selenium_returns_none_when_driver_fails():
    """If driver initialisation raises, _fetch_with_selenium returns None."""
    with patch("src.scraper.build_selenium_driver", side_effect=Exception("Chrome not found")):
        from src.scraper import _fetch_with_selenium
        result = _fetch_with_selenium("https://example.com")
    assert result is None


def test_fetch_with_selenium_returns_none_on_page_load_error():
    """If driver.get() raises, _fetch_with_selenium returns None and quits driver."""
    mock_driver = MagicMock()
    mock_driver.get.side_effect = Exception("page load timeout")

    with patch("src.scraper.build_selenium_driver", return_value=mock_driver):
        from src.scraper import _fetch_with_selenium
        result = _fetch_with_selenium("https://example.com")

    assert result is None
    mock_driver.quit.assert_called_once()


# ---------------------------------------------------------------------------
# fetch_raw_with_selenium — WAF-aware XHR approach
# ---------------------------------------------------------------------------


def test_fetch_raw_with_selenium_returns_none_when_driver_fails():
    """If driver initialisation raises, fetch_raw_with_selenium returns None."""
    with patch("src.scraper.build_selenium_driver", side_effect=Exception("Chrome not found")):
        from src.scraper import fetch_raw_with_selenium
        result = fetch_raw_with_selenium("https://example.com/sitemap.xml")
    assert result is None


def test_fetch_raw_with_selenium_navigates_then_uses_xhr():
    """Navigates to URL first (WAF session), then fetches via synchronous XHR."""
    import sys

    mock_driver = MagicMock()
    mock_driver.execute_script.return_value = (
        "<urlset><url><loc>https://example.com/</loc></url></urlset>"
    )

    # Selenium may not be installed in the test environment; mock the local imports
    # that happen inside fetch_raw_with_selenium so the function body can run.
    selenium_mocks = {
        "selenium": MagicMock(),
        "selenium.webdriver": MagicMock(),
        "selenium.webdriver.common": MagicMock(),
        "selenium.webdriver.common.by": MagicMock(),
        "selenium.webdriver.support": MagicMock(),
        "selenium.webdriver.support.ui": MagicMock(),
        "selenium.webdriver.support.expected_conditions": MagicMock(),
    }

    with patch("src.scraper.build_selenium_driver", return_value=mock_driver):
        with patch.dict(sys.modules, selenium_mocks):
            from src.scraper import fetch_raw_with_selenium
            result = fetch_raw_with_selenium("https://example.com/sitemap.xml")

    # Must navigate to the URL (not about:blank) to establish WAF session.
    mock_driver.get.assert_called_once_with("https://example.com/sitemap.xml")
    assert result is not None
    assert "<urlset>" in result
    mock_driver.quit.assert_called_once()


def test_fetch_raw_with_selenium_returns_none_on_empty_xhr_response():
    """If XHR returns empty string, returns None (not empty string)."""
    mock_driver = MagicMock()
    mock_driver.execute_script.return_value = ""

    with patch("src.scraper.build_selenium_driver", return_value=mock_driver):
        from src.scraper import fetch_raw_with_selenium
        result = fetch_raw_with_selenium("https://example.com/sitemap.xml")

    assert result is None
    mock_driver.quit.assert_called_once()


def test_fetch_raw_with_selenium_returns_none_on_get_exception():
    """If driver.get() raises, fetch_raw_with_selenium returns None and quits."""
    mock_driver = MagicMock()
    mock_driver.get.side_effect = Exception("navigation timeout")

    with patch("src.scraper.build_selenium_driver", return_value=mock_driver):
        from src.scraper import fetch_raw_with_selenium
        result = fetch_raw_with_selenium("https://example.com/sitemap.xml")

    assert result is None
    mock_driver.quit.assert_called_once()
