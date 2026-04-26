"""
src/scraper.py

Web scraping with trafilatura normalisation for the Tripwire influencer
source pipeline (Stage 2 prerequisite).

Responsibilities:
  - Fetch a URL and extract normalised plain text via trafilatura.
  - Support HTML, RSS, FRL, and DOCX sources.
  - Expose the normalise_text helper used across multiple stages.
  - Raise RetryableError / PermanentError so the retry layer handles
    transient vs permanent failures consistently.

Fetch strategy:
  1. Requests-based fetch (fast, low overhead).
  2. If a bot-detection block signature is found in the raw response HTML,
     or if force_selenium=True on the source, fall back to a Selenium
     ChromeDriver with selenium-stealth patches and randomised scroll
     simulation.
  3. Selenium uses a fresh driver per fetch — no session state, cookies,
     or history carries over between sources.

This module is the influencer-source counterpart of ingestion/scrape_ipfr.py
(which handles IPFR corpus pages).  The extraction logic is identical; the
difference is caller context and snapshot management (handled in stage3_diff.py).
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import time
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Block-detection signatures checked against raw HTML (case-insensitive).
# A 200 OK response carrying any of these strings is treated the same as a
# network error and triggers the Selenium fallback.
#
# This list is a minimum baseline — extend with site-specific strings
# observed during operation.
_BLOCK_SIGNATURES: list[str] = [
    # Cloudflare challenges
    "just a moment",
    "ddos protection by cloudflare",
    "checking if the site connection is secure",
    "verifying you are human",
    # Generic JS-gate / CAPTCHA pages
    "enable javascript and cookies to continue",
    "please enable javascript",
    "enable cookies",
    # Access control
    "access denied",
    "checking your browser",
    # Legacy CAPTCHA indicators
    "captcha",
    "verify you are human",
    "robot check",
    # Transport / network error pages served as HTML
    "this site can't be reached",
    "err_http2_protocol_error",
]


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def scrape_and_normalise(
    url: str,
    source_type: str,
    session: Any,
    force_selenium: bool = False,
) -> str:
    """Fetch a URL and return normalised plain text.

    Routing by source_type:
      - ``"docx"``        → requests download → mammoth → trafilatura
      - anything else     → HTML fetch (requests first, Selenium fallback)

    Parameters
    ----------
    url:
        The URL to fetch.
    source_type:
        Source type string from the source registry (e.g. ``"webpage"``,
        ``"frl"``, ``"rss"``, ``"docx"``).
    session:
        A ``requests.Session`` (or compatible) object.
    force_selenium:
        If ``True``, skip the requests-based attempt and go straight to
        Selenium.  Useful for targets that reliably block GitHub Actions
        runner IPs on direct connection.

    Returns
    -------
    str
        Normalised plain text.

    Raises
    ------
    src.errors.RetryableError
        On transient network failures when all fetch attempts fail.
    src.errors.PermanentError
        On HTTP 4xx responses, or when a block page is returned by both
        the requests path and the Selenium fallback.
    """
    if source_type == "docx":
        return _scrape_docx(url, session)

    if source_type == "rss":
        return _fetch_raw_rss(url, session, force_selenium=force_selenium)

    # --- HTML-based fetch ---
    html: str | None = None

    if not force_selenium:
        html = _fetch_with_requests(url, session)

    if html is None or _has_block_signature(html):
        if html is not None:
            logger.info(
                "Block signature detected in requests response for %s; "
                "falling back to Selenium.",
                url,
            )
        html = _fetch_with_selenium(url)

    if html is None:
        from src.errors import RetryableError
        raise RetryableError(f"All fetch attempts failed for {url}")

    if _has_block_signature(html):
        from src.errors import captcha_error
        raise captcha_error(url)

    return extract_plain_text(html)


def scrape_url(url: str, session: Any) -> str:
    """Fetch a URL and return normalised plain text (requests-only path).

    Retained for backward compatibility.  Prefer ``scrape_and_normalise()``
    for new call sites.

    Raises
    ------
    src.errors.RetryableError
        On HTTP 5xx responses or connection timeouts.
    src.errors.PermanentError
        On HTTP 4xx (except 429), or bot-detection block detected.
    """
    from src.errors import RetryableError, http_error, captcha_error

    try:
        resp = session.get(url, timeout=30)
    except Exception as exc:
        raise RetryableError(f"Connection error fetching {url}: {exc}") from exc

    if resp.status_code != 200:
        raise http_error(resp.status_code, url)

    if _has_block_signature(resp.text):
        raise captcha_error(url)

    return extract_plain_text(resp.text)


def extract_plain_text(html: str) -> str:
    """Convert HTML to normalised plain text using trafilatura.

    Falls back to a minimal HTML-strip when trafilatura is unavailable
    (e.g. in lightweight test environments).

    Parameters
    ----------
    html:
        Raw HTML string.

    Returns
    -------
    str
        Normalised plain text.
    """
    try:
        import trafilatura
        result = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
        )
        if result:
            return normalise_text(result)
    except ImportError:
        logger.warning("trafilatura not installed — falling back to basic HTML strip.")
    except Exception as exc:
        logger.warning("trafilatura extraction failed: %s — falling back.", exc)

    return normalise_text(_strip_html_basic(html))


def extract_plain_text_from_docx(docx_bytes: bytes) -> str:
    """Extract plain text from a DOCX file via Mammoth → HTML → trafilatura.

    Parameters
    ----------
    docx_bytes:
        Raw bytes of the DOCX file.

    Returns
    -------
    str
        Normalised plain text.
    """
    try:
        import io
        import mammoth
        result = mammoth.convert_to_html(io.BytesIO(docx_bytes))
        return extract_plain_text(result.value)
    except ImportError:
        raise RuntimeError("mammoth is required to process DOCX files: pip install mammoth")


def compute_sha256(text: str) -> str:
    """Return the SHA-256 hex digest of the normalised plain text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalise_text(text: str) -> str:
    """Apply canonical normalisation to plain text.

    Operations (per Section 3.3 of the system plan):
    - Replace non-breaking spaces (U+00A0) and tab characters with regular space.
    - Normalise Unicode to NFC.
    - Collapse multiple consecutive spaces/tabs on a single line.
    - Collapse 3+ consecutive blank lines to 2.
    - Does NOT lowercase (NER and YAKE need case information).
    - Does NOT strip punctuation (YAKE uses sentence boundaries).

    Parameters
    ----------
    text:
        Raw text string.

    Returns
    -------
    str
        Normalised plain text.
    """
    # Replace non-breaking spaces.
    text = text.replace("\xa0", " ")
    # Normalise Unicode to NFC.
    text = unicodedata.normalize("NFC", text)
    # Collapse runs of spaces/tabs within lines.
    text = re.sub(r"[ \t]+", " ", text)
    # Collapse excessive blank lines.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Private helpers — block detection
# ---------------------------------------------------------------------------


def _has_block_signature(html: str) -> bool:
    """Return True if *html* contains any known bot-detection or block string.

    The check is case-insensitive and runs against the raw HTML so it catches
    block pages that trafilatura might fail to extract any content from.
    """
    lower = html.lower()
    return any(sig in lower for sig in _BLOCK_SIGNATURES)


# ---------------------------------------------------------------------------
# Private helpers — requests-based fetch
# ---------------------------------------------------------------------------


def _fetch_with_requests(url: str, session: Any) -> str | None:
    """Attempt a simple requests-based GET.

    Returns the raw HTML on success, or ``None`` on connection failure
    (so the caller can fall back to Selenium).  HTTP error status codes
    are still raised as ``TripwireError`` exceptions.
    """
    from src.errors import http_error

    try:
        resp = session.get(url, timeout=30)
    except Exception as exc:
        logger.warning("Requests fetch failed for %s: %s", url, exc)
        return None

    if resp.status_code != 200:
        raise http_error(resp.status_code, url)

    return resp.text


# ---------------------------------------------------------------------------
# Private helpers — Selenium-based fetch
# ---------------------------------------------------------------------------


def build_selenium_driver():
    """Create a fresh, stealth-patched Chrome WebDriver.

    Chrome flags applied:

    ``--headless=new``
        Newer headless mode; more fingerprint-consistent than the legacy mode.
    ``--disable-blink-features=AutomationControlled``
        Suppresses ``navigator.webdriver = true``, the most commonly checked
        automation signal.
    ``excludeSwitches: ["enable-automation"]`` / ``useAutomationExtension: False``
        Removes the Chrome infobar and associated automation markers.
    ``--window-size=1920,1080``
        Headless Chrome defaults to a small viewport; many fingerprinting
        scripts flag non-standard dimensions.
    ``--lang=en-US``
        Sets a plausible ``Accept-Language`` value.
    ``--no-sandbox`` / ``--disable-dev-shm-usage``
        Required in GitHub Actions runner containers.

    ``selenium-stealth`` is applied on top to patch the JS layer
    (``navigator.plugins``, ``navigator.languages``, WebGL vendor strings,
    etc.) that the Chrome flags alone cannot reach.

    Returns
    -------
    selenium.webdriver.Chrome
        A freshly initialised WebDriver.  The caller MUST call ``driver.quit()``
        in a ``finally`` block.

    Raises
    ------
    src.errors.PermanentError
        If the ``selenium`` package is missing, Chrome/chromedriver cannot be
        launched, or ``selenium-stealth`` is not importable.  All three are
        environment-configuration problems that will not resolve on retry, so
        they surface as ``PermanentError`` rather than silent warnings.
    """
    from src.errors import PermanentError

    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError as exc:
        raise PermanentError(
            f"Selenium is required but not installed: {exc}. "
            "Install with: pip install selenium>=4.10"
        ) from exc

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=en-US")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    try:
        driver = webdriver.Chrome(options=options)
    except Exception as exc:
        raise PermanentError(
            f"Chrome WebDriver failed to launch: {exc}. "
            "Ensure google-chrome-stable is installed and selenium>=4.10 is "
            "available (Selenium Manager auto-downloads a compatible chromedriver)."
        ) from exc

    try:
        from selenium_stealth import stealth
    except ImportError as exc:
        try:
            driver.quit()
        except Exception:
            pass
        raise PermanentError(
            f"selenium-stealth is required for stealth patches but is not installed: {exc}. "
            "Install with: pip install selenium-stealth"
        ) from exc

    stealth(
        driver,
        languages=["en-US", "en"],
        vendor="Google Inc.",
        platform="Win32",
        webgl_vendor="Intel Inc.",
        renderer="Intel Iris OpenGL Engine",
        fix_hairline=True,
    )

    return driver


def _fetch_with_selenium(url: str) -> str | None:
    """Fetch a URL using a fresh Selenium Chrome driver with stealth patches.

    Behaviour:
    - Creates a new driver (no carried-over session state or cookies).
    - Waits up to 15 s for the page body to be present in the DOM.
    - Performs a two-stage randomised scroll (25% → 50% of page height)
      to trigger lazy-loaded content and avoid fixed-timing fingerprints.
    - Quits the driver in a ``finally`` block regardless of outcome.

    Parameters
    ----------
    url:
        The URL to fetch.

    Returns
    -------
    str | None
        Raw HTML of the rendered page, or ``None`` if the driver could not
        be initialised (e.g. Chrome not installed) or an exception occurred.
    """
    # build_selenium_driver raises PermanentError on environment-level
    # problems (missing Chrome, missing selenium-stealth).  Let those
    # propagate so the pipeline records them against the source rather than
    # silently falling through to a generic "all fetch attempts failed".
    driver = build_selenium_driver()

    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        driver.get(url)

        # Wait for the page body to be present (up to 15 s).
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        # Two-stage randomised scroll: triggers lazy-loaded content and
        # makes timing look less robotic.  Fixed delays are themselves a
        # fingerprint, hence the uniform random ranges.
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 4);")
        time.sleep(random.uniform(2, 4))
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
        time.sleep(random.uniform(1, 3))

        return driver.page_source

    except Exception as exc:
        logger.warning("Selenium fetch failed for %s: %s", url, exc)
        return None
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def fetch_with_waf_polling(
    url: str,
    *,
    must_disappear: str = "Azure WAF",
    poll_interval_s: float = 0.5,
    timeout_s: float = 10.0,
    page_load_timeout_s: int = 30,
    min_length: int = 500,
) -> str | None:
    """Fetch a URL via stealth Chrome and poll until ``must_disappear`` is gone.

    Designed for sites whose WAF (Web Application Firewall) serves an interim
    JS challenge page that resolves itself within a few seconds once the
    browser executes the challenge JavaScript.  parlinfo.aph.gov.au sits
    behind an Azure WAF JS Challenge that returns a page containing the
    string "Azure WAF" until the challenge completes; once it does, the page
    is replaced with the real content.  Polling beats a fixed sleep because
    challenge resolution time varies (typically 0.5–3 s).

    Parameters
    ----------
    url:
        URL to fetch.
    must_disappear:
        Substring whose absence from ``page_source`` signals the WAF
        challenge has cleared.
    poll_interval_s:
        Seconds between polls.
    timeout_s:
        Maximum total time to wait for ``must_disappear`` to vanish.
    page_load_timeout_s:
        Selenium ``set_page_load_timeout`` value applied before navigation.
    min_length:
        Reject the page if ``page_source`` is shorter than this; almost
        always indicates the challenge never resolved.

    Returns
    -------
    str | None
        Final ``page_source`` if the WAF cleared and the page is at least
        ``min_length`` chars; ``None`` otherwise.
    """
    driver = build_selenium_driver()
    try:
        driver.set_page_load_timeout(page_load_timeout_s)
        logger.debug("WAF fetch: navigating to %s", url[:120])
        driver.get(url)

        max_polls = max(1, int(timeout_s / poll_interval_s))
        polls_done = 0
        for _ in range(max_polls):
            time.sleep(poll_interval_s)
            polls_done += 1
            if must_disappear not in driver.page_source:
                break

        html = driver.page_source
        cleared = must_disappear not in html
        logger.debug(
            "WAF fetch: polls=%d cleared=%s length=%d",
            polls_done, cleared, len(html or ""),
        )
        if cleared and len(html) >= min_length:
            return html
        return None

    except Exception as exc:
        logger.warning("WAF fetch failed for %s: %s", url, exc)
        return None
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def fetch_raw_with_selenium(url: str, *, timeout_seconds: int = 60) -> str | None:
    """Fetch a URL and return the raw response body as text.

    Used for non-HTML resources (e.g. XML sitemaps) where Chrome's built-in
    viewer would otherwise wrap the payload in view-source markup.

    Strategy (mirrors the working approach from check_sitemap.py):
    1. Navigate to the URL so the WAF can run its JS challenge and set session
       cookies.  Starting from ``about:blank`` and issuing a bare ``fetch()``
       fails because no WAF session tokens exist in that context.
    2. Wait for the page body to appear, giving the WAF challenge time to
       complete.
    3. Issue a synchronous XHR *within the established browser context* so the
       WAF session cookies are included.  Synchronous XHR is used (not async
       ``fetch()``) because it reliably returns the raw response text before
       Chrome can render it into its shadow-DOM XML viewer.

    Parameters
    ----------
    url:
        The URL to fetch.
    timeout_seconds:
        Maximum seconds to wait for the page body after navigation.

    Returns
    -------
    str | None
        Raw response body, or ``None`` if the driver could not start or the
        fetch raised an exception.
    """
    # build_selenium_driver raises PermanentError on environment-level
    # problems; let it propagate so callers can record the real cause.
    driver = build_selenium_driver()

    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        # Step 1 + 2: navigate to establish WAF session, wait for challenge.
        driver.get(url)
        WebDriverWait(driver, timeout_seconds).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        # Step 3: synchronous XHR in the authenticated browser context.
        raw_text = driver.execute_script(
            """
            var xhr = new XMLHttpRequest();
            xhr.open('GET', arguments[0], false);
            xhr.send(null);
            return xhr.responseText;
            """,
            url,
        )

        if not raw_text:
            logger.warning("Selenium raw fetch returned empty response for %s", url)
            return None

        return raw_text

    except Exception as exc:
        logger.warning("Selenium raw fetch failed for %s: %s", url, exc)
        return None
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Private helpers — RSS
# ---------------------------------------------------------------------------


def _fetch_raw_rss(url: str, session: Any, *, force_selenium: bool = False) -> str:
    """Fetch an RSS feed and return the raw XML text.

    Stage 3 (_generate_rss_diff) re-fetches and parses the XML itself; this
    call exists only so that pipeline.py has a non-empty new_text for
    source-state bookkeeping.  Trafilatura is intentionally bypassed — it is
    an HTML extractor and produces garbled output on XML feeds.

    Fetch strategy mirrors the HTML path:
      1. If ``force_selenium`` is False, try plain ``requests``.
      2. If ``requests`` fails with a connection error or the response body
         contains a block-page signature, fall back to
         ``fetch_raw_with_selenium`` which runs a synchronous XHR inside an
         authenticated browser context (required for WAF-protected feeds such
         as fedcourt.gov.au).
    """
    from src.errors import RetryableError, http_error

    if not force_selenium:
        try:
            resp = session.get(url, timeout=20)
        except Exception as exc:
            logger.warning(
                "Requests fetch failed for RSS %s: %s — falling back to Selenium.",
                url, exc,
            )
        else:
            if resp.status_code != 200:
                raise http_error(resp.status_code, url)
            if not _has_block_signature(resp.text):
                return resp.text
            logger.info(
                "Block signature detected in RSS response for %s — falling back to Selenium.",
                url,
            )

    raw = fetch_raw_with_selenium(url)
    if raw is None:
        raise RetryableError(f"All fetch attempts failed for RSS {url}")
    if _has_block_signature(raw):
        from src.errors import captcha_error
        raise captcha_error(url)
    return raw


# ---------------------------------------------------------------------------
# Private helpers — DOCX
# ---------------------------------------------------------------------------


def _scrape_docx(url: str, session: Any) -> str:
    """Download a DOCX file via requests and extract its plain text."""
    from src.errors import RetryableError, http_error

    try:
        resp = session.get(url, timeout=60)
    except Exception as exc:
        raise RetryableError(f"Connection error downloading DOCX {url}: {exc}") from exc

    if resp.status_code != 200:
        raise http_error(resp.status_code, url)

    return extract_plain_text_from_docx(resp.content)


# ---------------------------------------------------------------------------
# Private helpers — HTML stripping fallback
# ---------------------------------------------------------------------------


def _strip_html_basic(html: str) -> str:
    """Minimal HTML tag stripping fallback when trafilatura is unavailable."""
    # Remove script/style blocks completely.
    html = re.sub(
        r"<(script|style)[^>]*>.*?</(script|style)>",
        "",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Replace block-level tags with newlines.
    html = re.sub(
        r"</(p|div|h[1-6]|li|br|tr)>",
        "\n",
        html,
        flags=re.IGNORECASE,
    )
    # Strip all remaining tags.
    return re.sub(r"<[^>]+>", " ", html)
