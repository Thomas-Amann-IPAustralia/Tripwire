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


def scrape_page(
    url: str,
    session: Any,
    *,
    force_selenium: bool = False,
) -> tuple[str, list[dict[str, Any]], str]:
    """Fetch and normalise a single IPFR page.

    Strategy:

    1. Try a ``requests`` GET with a 30 s timeout (skipped when
       *force_selenium* is True).
    2. If that fails (connection error, non-200, or the response body contains
       a known bot-detection signature), fall back to a fresh Selenium Chrome
       driver via ``src.scraper._fetch_with_selenium``.  This is the same
       two-tier approach used by the influencer-source scraper and keeps the
       IPFR ingestion resilient to IP-based or JS-gate blocks on GitHub
       Actions runners.

    Parameters
    ----------
    url:
        The HTTPS URL to fetch.
    session:
        A ``requests.Session`` (or compatible) object.  Must be provided by
        the caller so that connection pooling and retry wrappers are applied
        at the call site.
    force_selenium:
        If True, skip the requests-based attempt and go straight to Selenium.
        Use when the target host reliably blocks direct connections from the
        environment running this code (e.g. GitHub Actions runner IPs).

    Returns
    -------
    (plain_text, sections, title)
        plain_text — normalised plain text (trafilatura output).
        sections   — list of section dicts with keys:
                     heading_text, heading_level, char_start, char_end.
        title      — page title from trafilatura metadata, or "" if not found.

    Raises
    ------
    src.errors.RetryableError
        On HTTP 5xx / connection timeouts when both requests and Selenium fail.
    src.errors.PermanentError
        On HTTP 4xx (except 429), CAPTCHA detected, or content too short.
    """
    html = _fetch_page_html(url, session, force_selenium=force_selenium)

    plain_text = extract_plain_text(html)
    sections = extract_sections(html, plain_text=plain_text)
    title = extract_title(html)

    # Content validation
    _validate_captcha(plain_text, url)
    _validate_length(plain_text, url)

    return plain_text, sections, title


def _fetch_page_html(url: str, session: Any, force_selenium: bool = False) -> str:
    """Return raw HTML for *url*, using Selenium as a fallback.

    Raises
    ------
    src.errors.RetryableError
        If both requests and Selenium fail to return a non-blocked page.
    src.errors.PermanentError
        On HTTP 4xx or when a block page is returned by Selenium as well.
    """
    from src.errors import RetryableError, http_error, captcha_error

    html: str | None = None
    requests_err: str | None = None

    if not force_selenium:
        try:
            resp = session.get(url, timeout=30)
        except Exception as exc:
            requests_err = str(exc)
            logger.warning("Requests fetch failed for %s: %s", url, exc)
        else:
            if resp.status_code == 200:
                html = resp.text
            elif resp.status_code in (429,) or resp.status_code >= 500:
                # Transient — try Selenium before giving up.
                requests_err = f"HTTP {resp.status_code}"
                logger.warning(
                    "Transient HTTP %s for %s; will try Selenium fallback.",
                    resp.status_code,
                    url,
                )
            else:
                # 4xx (non-429) — permanent.
                raise http_error(resp.status_code, url)

    if html is not None and _looks_like_block_page(html):
        logger.info(
            "Block signature detected in requests response for %s; "
            "falling back to Selenium.",
            url,
        )
        html = None

    if html is None:
        try:
            from src.scraper import _fetch_with_selenium
        except ImportError as exc:
            raise RetryableError(
                f"Page fetch failed for {url} "
                f"(requests: {requests_err}; Selenium unavailable: {exc})"
            ) from exc

        selenium_html = _fetch_with_selenium(url)
        if selenium_html is None:
            raise RetryableError(
                f"Page fetch failed for {url} via both requests "
                f"({requests_err or 'blocked'}) and Selenium."
            )

        if _looks_like_block_page(selenium_html):
            raise captcha_error(url)

        html = selenium_html

    return html


def _looks_like_block_page(text: str) -> bool:
    """Return True if *text* contains a known bot-detection block signature."""
    try:
        from src.scraper import _has_block_signature
        return _has_block_signature(text)
    except ImportError:
        lower = text.lower()
        return any(phrase in lower for phrase in _CAPTCHA_PHRASES)


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


def extract_sections(
    html: str,
    plain_text: str | None = None,
) -> list[dict[str, Any]]:
    """Extract section headings and their character offsets from HTML.

    Strategy:
      1. Parse trafilatura's XML output with ``lxml`` to pull ``<head>`` nodes
         (robust against attribute ordering, nested inline markup, and self-
         closing tags that the previous regex approach missed).
      2. If that produces no headings — or if trafilatura/lxml is unavailable —
         fall back to a heuristic scan over *plain_text*: short standalone lines
         that look like titles ("What is it?", "See also", "Who's involved?")
         are promoted to level-2 headings.

    Char offsets are computed by locating each heading string inside the plain
    text produced by :func:`extract_plain_text`.  Pass *plain_text* explicitly
    when available so the offsets align with the content actually stored in the
    database.
    """
    if plain_text is None:
        plain_text = extract_plain_text(html)

    try:
        import trafilatura
        xml_output = trafilatura.extract(
            html,
            output_format="xml",
            include_comments=False,
        )
    except ImportError:
        xml_output = None
    except Exception as exc:
        logger.warning("Section XML extraction failed: %s", exc)
        xml_output = None

    sections: list[dict[str, Any]] = []
    if xml_output:
        try:
            sections = _parse_sections_from_xml_lxml(xml_output, plain_text)
        except ImportError:
            sections = _parse_sections_from_xml(xml_output)
        except Exception as exc:
            logger.warning("lxml section parse failed: %s — using regex fallback.", exc)
            sections = _parse_sections_from_xml(xml_output)

    if not sections:
        sections = _heuristic_sections(plain_text)
    return sections


def extract_plain_text_from_docx(docx_bytes: bytes) -> tuple[str, list[dict[str, Any]], str]:
    """Extract plain text from a DOCX file via Mammoth → HTML → trafilatura.

    Parameters
    ----------
    docx_bytes:
        Raw bytes of the DOCX file.

    Returns
    -------
    (plain_text, sections, title)
    """
    try:
        import io
        import mammoth
        result = mammoth.convert_to_html(io.BytesIO(docx_bytes))
        html = result.value
        return extract_plain_text(html), extract_sections(html), extract_title(html)
    except ImportError:
        raise RuntimeError("mammoth is required to process DOCX files: pip install mammoth")


def extract_title(html: str) -> str:
    """Return the page title from trafilatura metadata, falling back to <title>.

    Returns an empty string if no title is recoverable.
    """
    try:
        import trafilatura
        meta = trafilatura.extract_metadata(html)
        if meta is not None:
            title = getattr(meta, "title", None)
            if title:
                return normalise_text(title)
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("trafilatura title extraction failed: %s", exc)

    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if match:
        raw = re.sub(r"<[^>]+>", "", match.group(1))
        return normalise_text(raw)
    return ""


def compute_version_hash(text: str) -> str:
    """Return the SHA-256 hex digest of the normalised plain text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Boilerplate stripping
# ---------------------------------------------------------------------------


def detect_frequent_lines(
    documents: list[str],
    *,
    frequency_threshold: float = 0.7,
    min_documents: int = 3,
    min_line_length: int = 3,
) -> set[str]:
    """Return lines that appear on more than *frequency_threshold* of documents.

    Used to auto-detect site chrome (navigation, disclaimers) that leaks
    through trafilatura.  A line is considered boilerplate when it occurs
    verbatim (trimmed, case-sensitive) on at least *frequency_threshold* × N
    distinct documents, where N is the input corpus size.  If fewer than
    *min_documents* are supplied the detector bails out — we need a corpus
    large enough for statistical repetition to be meaningful.

    The returned set is case-sensitive stripped line content.
    """
    n = len(documents)
    if n < min_documents:
        return set()

    counts: dict[str, int] = {}
    for doc in documents:
        if not doc:
            continue
        # Dedup within a single document so a repeated line counts once.
        seen: set[str] = set()
        for raw_line in doc.split("\n"):
            line = raw_line.strip()
            if len(line) < min_line_length:
                continue
            if line in seen:
                continue
            seen.add(line)
            counts[line] = counts.get(line, 0) + 1

    cutoff = max(2, int(frequency_threshold * n))
    return {line for line, count in counts.items() if count >= cutoff}


def strip_boilerplate(
    content: str,
    sections: list[dict[str, Any]] | None = None,
    *,
    blocklist: list[str] | set[str] | None = None,
    frequent_lines: set[str] | None = None,
) -> tuple[str, list[dict[str, Any]], int]:
    """Drop lines that match *blocklist* or *frequent_lines* and re-offset sections.

    Parameters
    ----------
    content:
        Plain text produced by :func:`extract_plain_text`.
    sections:
        Section dicts from :func:`extract_sections`.  Char offsets are
        recomputed against the stripped content; headings that no longer appear
        in the stripped text (because they were part of the removed boilerplate)
        are discarded.
    blocklist:
        User-supplied phrases to drop.  Matched case-insensitively against
        stripped lines.  A blocklist phrase that appears *inside* a longer
        line is also stripped — useful for inline chrome like ``Skip to main
        content``.
    frequent_lines:
        Auto-detected repeated lines (from :func:`detect_frequent_lines`).
        Matched case-sensitively against stripped lines (exact equality only —
        we never strip substrings here to avoid eating legitimate content).

    Returns
    -------
    (stripped_content, adjusted_sections, bytes_stripped)
    """
    blocklist_lc = {b.strip().lower() for b in (blocklist or []) if b and b.strip()}
    frequent = set(frequent_lines or [])

    kept_lines: list[str] = []
    for raw_line in content.split("\n"):
        stripped = raw_line.strip()
        if not stripped:
            kept_lines.append(raw_line)
            continue
        if stripped in frequent:
            continue
        if stripped.lower() in blocklist_lc:
            continue
        # Inline blocklist phrases (leave frequent-line detection to exact match only).
        cleaned = raw_line
        for phrase in blocklist_lc:
            if phrase and phrase in cleaned.lower():
                pattern = re.compile(re.escape(phrase), re.IGNORECASE)
                cleaned = pattern.sub("", cleaned)
        cleaned = cleaned.strip()
        if cleaned:
            kept_lines.append(cleaned)

    stripped_content = "\n".join(kept_lines)
    stripped_content = re.sub(r"\n{3,}", "\n\n", stripped_content).strip()
    bytes_stripped = max(0, len(content) - len(stripped_content))

    adjusted_sections = _reindex_sections(stripped_content, sections or [])
    return stripped_content, adjusted_sections, bytes_stripped


def _reindex_sections(
    content: str, sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Re-anchor section char offsets against *content* by searching for heading_text."""
    adjusted: list[dict[str, Any]] = []
    cursor = 0
    for section in sections:
        heading = section.get("heading_text", "")
        if not heading:
            continue
        idx = content.find(heading, cursor)
        if idx < 0:
            idx = content.find(heading)
        if idx < 0:
            continue
        cursor = idx + len(heading)
        adjusted.append({
            "heading_text": heading,
            "heading_level": section.get("heading_level", 2),
            "char_start": idx,
            "char_end": idx + len(heading),
        })
    return adjusted


def is_stub_page(
    content: str,
    *,
    min_length: int = 500,
    stub_phrases: list[str] | set[str] | None = None,
) -> bool:
    """Return True if *content* looks like a placeholder / stub page.

    A page is a stub when its length falls below *min_length* OR it contains
    any of the configured *stub_phrases* (case-insensitive substring match)
    such as "This page is coming soon" or "Coming April 2026".
    """
    if not content or len(content) < min_length:
        return True
    lower = content.lower()
    for phrase in stub_phrases or []:
        if phrase and phrase.lower() in lower:
            return True
    return False


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


def _parse_sections_from_xml_lxml(
    xml_text: str, plain_text: str,
) -> list[dict[str, Any]]:
    """Extract heading hierarchy via lxml + offset-lookup in *plain_text*.

    Trafilatura's XML uses ``<head rend="hN">`` for headings and may nest
    inline markup (``<hi>``, ``<ref>``) that the regex-based parser misses.
    ``lxml`` gives us the full text content of each heading regardless of the
    inline children, and we then locate that text inside the caller's plain
    text to get accurate character offsets.  Headings that don't appear in the
    plain text (rare edge case, typically when trafilatura emits them but the
    text extractor dropped them) are skipped.
    """
    from lxml import etree  # type: ignore

    # Trafilatura emits ``<doc>`` as the root; wrap defensively to tolerate
    # documents that already include or omit an XML prolog.
    try:
        root = etree.fromstring(xml_text.encode("utf-8"))
    except etree.XMLSyntaxError as exc:
        logger.debug("lxml parse error, retrying wrapped: %s", exc)
        wrapped = f"<doc>{xml_text}</doc>".encode("utf-8")
        root = etree.fromstring(wrapped)

    sections: list[dict[str, Any]] = []
    seen_spans: list[tuple[int, int]] = []
    search_cursor = 0

    for node in root.iter("head"):
        heading_text = " ".join((node.xpath("string(.)") or "").split()).strip()
        if not heading_text:
            continue

        rend = node.get("rend", "")
        match = re.match(r"h(\d)", rend or "")
        level = int(match.group(1)) if match else 2

        # Find the heading in the plain text, starting from the last known
        # match so repeated headings within a document map to the correct span.
        idx = plain_text.find(heading_text, search_cursor)
        if idx < 0 and search_cursor > 0:
            idx = plain_text.find(heading_text)
        if idx < 0:
            continue
        span = (idx, idx + len(heading_text))
        if span in seen_spans:
            continue
        seen_spans.append(span)
        search_cursor = span[1]

        sections.append({
            "heading_text": heading_text,
            "heading_level": level,
            "char_start": span[0],
            "char_end": span[1],
        })

    sections.sort(key=lambda s: s["char_start"])
    return sections


def _parse_sections_from_xml(xml_text: str) -> list[dict[str, Any]]:
    """Legacy regex-based parser, retained as a fallback when lxml is absent."""
    sections: list[dict[str, Any]] = []
    # Extract all text-bearing elements in order to estimate char offsets.
    elements = re.findall(
        r'<(head|p|list)\b([^>]*)>(.*?)</\1>',
        xml_text,
        flags=re.DOTALL,
    )

    char_pos = 0
    for tag, attrs, content in elements:
        # Strip nested tags from content to get plain text.
        text = re.sub(r"<[^>]+>", "", content).strip()

        if tag == "head":
            level_match = re.search(r'rend="h(\d)"', attrs)
            level = int(level_match.group(1)) if level_match else 2
            sections.append({
                "heading_text": text,
                "heading_level": level,
                "char_start": char_pos,
                "char_end": char_pos + len(text),
            })

        char_pos += len(text) + 1  # +1 for newline separator

    return sections


# Regex for heuristic heading detection: short standalone lines that end in
# question mark, colon, or look like a title (Title Case, no terminal period).
_HEADING_MAX_CHARS = 80
_TITLE_CASE_RE = re.compile(r"^(?:[A-Z][\w’'-]*(?:\s+|$)){1,8}[?!:]?$")
_QUESTION_OR_COLON_RE = re.compile(r".{3,%d}[?:]$" % _HEADING_MAX_CHARS)


def _heuristic_sections(plain_text: str) -> list[dict[str, Any]]:
    """Fall back to heuristic heading detection when trafilatura XML is empty.

    A line is promoted to a level-2 heading when ALL of the following hold:
      * length between 3 and ``_HEADING_MAX_CHARS`` characters,
      * does not end in a period (periods indicate prose),
      * either ends in ``?`` or ``:``, OR is Title Case and has no terminal
        punctuation, AND
      * the following line is non-empty (headings precede content).
    """
    sections: list[dict[str, Any]] = []
    if not plain_text:
        return sections

    lines = plain_text.split("\n")
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line) + 1  # +1 for the newline

    for i, line in enumerate(lines):
        candidate = line.strip()
        if not (3 <= len(candidate) <= _HEADING_MAX_CHARS):
            continue
        if candidate.endswith("."):
            continue
        # Require a content line below.
        next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if not next_line:
            continue
        if _QUESTION_OR_COLON_RE.match(candidate) or _TITLE_CASE_RE.match(candidate):
            start = offsets[i] + line.index(candidate) if candidate in line else offsets[i]
            sections.append({
                "heading_text": candidate,
                "heading_level": 2,
                "char_start": start,
                "char_end": start + len(candidate),
            })
    return sections
