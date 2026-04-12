"""
ingestion/scrape_ipfr.py

Step 2 of the IPFR ingestion pipeline: scrape each IPFR page and normalise
it into plain text using trafilatura.

Text extraction strategy (per Section 4.1 and 3.3 of the system plan):
  - Primary:  trafilatura plain-text output (boilerplate stripped automatically)
  - Section boundaries: trafilatura XML output parsed for <head> tags,
    then discarded — only the plain-text is persisted.
  - DOCX:  DOCX → Mammoth → HTML → trafilatura (preserves semantic structure)

Content validation (per Section 6.2):
  - Minimum length: 200 chars
  - CAPTCHA / bot-detection phrases
  - Structural marker check (optional per-source markers)
  - Dramatic size change detection (< 30% or > 300% of previous length)

Network calls are wrapped with RetryableError / PermanentError so the retry
layer in src/retry.py handles transient failures automatically.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

# Minimum acceptable content length (characters).
_MIN_CONTENT_LENGTH = 200

# Block-detection signatures checked against raw HTML (case-insensitive).
# Kept in sync with src/scraper._BLOCK_SIGNATURES.
_CAPTCHA_PHRASES = [
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

# Size-change bounds: flag if new content is outside [30%, 300%] of old.
_SIZE_CHANGE_MIN_RATIO = 0.30
_SIZE_CHANGE_MAX_RATIO = 3.00


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def scrape_page(url: str, session: Any) -> tuple[str, list[dict[str, Any]]]:
    """Fetch and normalise a single IPFR page.

    Parameters
    ----------
    url:
        The HTTPS URL to fetch.
    session:
        A requests.Session (or compatible) object.  Must be provided by the
        caller so that connection pooling and retry wrappers are applied at
        the call site.

    Returns
    -------
    (plain_text, sections)
        plain_text — normalised plain text (trafilatura output).
        sections   — list of section dicts with keys:
                     heading_text, heading_level, char_start, char_end.

    Raises
    ------
    src.errors.RetryableError
        On HTTP 5xx / connection timeouts.
    src.errors.PermanentError
        On HTTP 4xx (except 429), CAPTCHA detected, or content too short.
    """
    from src.errors import RetryableError, PermanentError, http_error, captcha_error, content_too_short_error

    try:
        resp = session.get(url, timeout=30)
    except Exception as exc:
        raise RetryableError(f"Connection error fetching {url}: {exc}") from exc

    if resp.status_code != 200:
        raise http_error(resp.status_code, url)

    html = resp.text
    plain_text = extract_plain_text(html)
    sections = extract_sections(html)

    # Content validation
    _validate_captcha(plain_text, url)
    _validate_length(plain_text, url)

    return plain_text, sections


def extract_plain_text(html: str) -> str:
    """Convert HTML to normalised plain text using trafilatura.

    Falls back to a simple strip if trafilatura is unavailable (tests).
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


def extract_sections(html: str) -> list[dict[str, Any]]:
    """Extract section headings and their character offsets from HTML.

    Uses trafilatura's XML output to find <head> tags, then maps them to
    character positions in the plain-text output.  Returns an empty list
    if trafilatura is unavailable or produces no headings.
    """
    try:
        import trafilatura
        xml_output = trafilatura.extract(
            html,
            output_format="xml",
            include_comments=False,
        )
        if not xml_output:
            return []
        return _parse_sections_from_xml(xml_output)
    except ImportError:
        return []
    except Exception as exc:
        logger.warning("Section extraction failed: %s", exc)
        return []


def extract_plain_text_from_docx(docx_bytes: bytes) -> tuple[str, list[dict[str, Any]]]:
    """Extract plain text from a DOCX file via Mammoth → HTML → trafilatura.

    Parameters
    ----------
    docx_bytes:
        Raw bytes of the DOCX file.

    Returns
    -------
    (plain_text, sections)
    """
    try:
        import io
        import mammoth
        result = mammoth.convert_to_html(io.BytesIO(docx_bytes))
        html = result.value
        return extract_plain_text(html), extract_sections(html)
    except ImportError:
        raise RuntimeError("mammoth is required to process DOCX files: pip install mammoth")


def compute_version_hash(text: str) -> str:
    """Return the SHA-256 hex digest of the normalised plain text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalise_text(text: str) -> str:
    """Apply canonical normalisation to plain text.

    Per Section 3.3 of the system plan:
    - Decode HTML entities (already handled by trafilatura).
    - Collapse whitespace runs including non-breaking spaces.
    - Normalise Unicode to NFC.
    - Does NOT lowercase (NER/YAKE need case information).
    - Does NOT strip punctuation (YAKE uses sentence boundaries).
    """
    # Replace non-breaking spaces and other whitespace variants.
    text = text.replace("\xa0", " ")
    # Normalise Unicode to NFC.
    text = unicodedata.normalize("NFC", text)
    # Collapse multiple consecutive whitespace characters (preserve newlines as single).
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def validate_content(
    content: str,
    url: str,
    previous_length: int | None = None,
    structural_markers: list[str] | None = None,
) -> list[str]:
    """Run all content validation checks and return a list of warning strings.

    Does not raise — callers decide what to do with warnings.  The CAPTCHA
    and minimum-length checks use the error helpers from src.errors.
    """
    warnings: list[str] = []

    if len(content) < _MIN_CONTENT_LENGTH:
        warnings.append(f"Content too short: {len(content)} chars (minimum {_MIN_CONTENT_LENGTH}).")

    lower = content.lower()
    for phrase in _CAPTCHA_PHRASES:
        if phrase in lower:
            warnings.append(f"Possible CAPTCHA/bot-detection phrase found: '{phrase}'.")
            break

    if structural_markers:
        if not any(marker in content for marker in structural_markers):
            warnings.append(
                f"None of the expected structural markers found: {structural_markers}"
            )

    if previous_length is not None and previous_length > 0:
        ratio = len(content) / previous_length
        if ratio < _SIZE_CHANGE_MIN_RATIO:
            warnings.append(
                f"Dramatic content shrinkage: {previous_length} → {len(content)} chars "
                f"({ratio:.0%} of previous)."
            )
        elif ratio > _SIZE_CHANGE_MAX_RATIO:
            warnings.append(
                f"Dramatic content growth: {previous_length} → {len(content)} chars "
                f"({ratio:.0%} of previous)."
            )

    return warnings


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _validate_captcha(content: str, url: str) -> None:
    from src.errors import captcha_error
    lower = content.lower()
    for phrase in _CAPTCHA_PHRASES:
        if phrase in lower:
            raise captcha_error(url)


def _validate_length(content: str, url: str) -> None:
    from src.errors import content_too_short_error
    if len(content) < _MIN_CONTENT_LENGTH:
        raise content_too_short_error(url, len(content), _MIN_CONTENT_LENGTH)


def _strip_html_basic(html: str) -> str:
    """Minimal HTML tag stripping used as a fallback when trafilatura is unavailable."""
    # Remove script/style blocks.
    html = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", "", html,
                  flags=re.DOTALL | re.IGNORECASE)
    # Remove remaining tags.
    return re.sub(r"<[^>]+>", " ", html)


def _parse_sections_from_xml(xml_text: str) -> list[dict[str, Any]]:
    """Parse trafilatura XML output to extract heading hierarchy.

    Trafilatura's XML uses <head rend="hN"> tags for headings.  We extract
    heading text, level, and approximate character offsets into the plain text
    (computed by accumulating text content up to each heading).
    """
    import re as _re
    sections: list[dict[str, Any]] = []
    # Extract all text-bearing elements in order to estimate char offsets.
    elements = _re.findall(
        r'<(head|p|list)[^>]*>(.*?)</(head|p|list)>',
        xml_text,
        flags=_re.DOTALL,
    )

    char_pos = 0
    for tag, content, _ in elements:
        # Strip nested tags from content to get plain text.
        text = _re.sub(r"<[^>]+>", "", content).strip()

        if tag == "head":
            # Try to extract heading level from rend="hN" attribute.
            level_match = _re.search(r'rend="h(\d)"', xml_text[
                xml_text.find(f"<head"): xml_text.find(f"<head") + 50
            ])
            level = int(level_match.group(1)) if level_match else 2
            sections.append(
                {
                    "heading_text": text,
                    "heading_level": level,
                    "char_start": char_pos,
                    "char_end": char_pos + len(text),
                }
            )

        char_pos += len(text) + 1  # +1 for newline separator

    return sections
