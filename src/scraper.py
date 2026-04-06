"""
src/scraper.py

Web scraping with trafilatura normalisation for the Tripwire influencer
source pipeline (Stage 2 prerequisite).

Responsibilities:
  - Fetch a URL and extract normalised plain text via trafilatura.
  - Support HTML and DOCX sources.
  - Expose the normalise_text helper used across multiple stages.
  - Raise RetryableError / PermanentError so the retry layer handles
    transient vs permanent failures consistently.

This module is the influencer-source counterpart of ingestion/scrape_ipfr.py
(which handles IPFR corpus pages).  The extraction logic is identical; the
difference is caller context and snapshot management (handled in stage3_diff.py).
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Phrases that indicate a CAPTCHA or bot-detection page.
_CAPTCHA_PHRASES: list[str] = [
    "captcha",
    "verify you are human",
    "robot check",
    "access denied",
    "please enable javascript",
    "enable cookies",
    "checking your browser",
]


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def scrape_url(url: str, session: Any) -> str:
    """Fetch a URL and return normalised plain text.

    Parameters
    ----------
    url:
        The HTTPS URL to fetch.
    session:
        A requests.Session (or compatible) object.

    Returns
    -------
    str
        Normalised plain text (trafilatura output, NFC, whitespace collapsed).

    Raises
    ------
    src.errors.RetryableError
        On HTTP 5xx responses or connection timeouts.
    src.errors.PermanentError
        On HTTP 4xx (except 429), or CAPTCHA detected.
    """
    from src.errors import RetryableError, http_error, captcha_error

    try:
        resp = session.get(url, timeout=30)
    except Exception as exc:
        raise RetryableError(f"Connection error fetching {url}: {exc}") from exc

    if resp.status_code != 200:
        raise http_error(resp.status_code, url)

    plain_text = extract_plain_text(resp.text)

    # Check for CAPTCHA before returning.
    lower = plain_text.lower()
    for phrase in _CAPTCHA_PHRASES:
        if phrase in lower:
            raise captcha_error(url)

    return plain_text


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
# Private helpers
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
