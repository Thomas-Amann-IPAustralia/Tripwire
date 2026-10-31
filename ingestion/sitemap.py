"""
ingestion/sitemap.py

Step 1 of the IPFR ingestion pipeline: read the IP First Response sitemap
and populate (or update) the sitemap CSV.

The sitemap CSV columns (per Section 4.1 of the system plan):
  page_id        — IPFR content identifier, e.g. "B1012"
  url            — canonical URL of the page
  title          — page title
  snapshot_path  — relative path to the local markdown snapshot file
  last_modified  — "Last modification date" from the IPFR page (ISO 8601 date)
  last_checked   — date of the last ingestion check (ISO 8601 date, set by pipeline)

Sitemap discovery: the IPFR website publishes an XML sitemap at a known URL.
We parse that to get the full list of page URLs, then probe each page for its
title and last-modified date.

All network calls go through the src.retry module so transient failures are
retried automatically.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Browser-like User-Agent applied to ingestion requests.  The IPFR site rejects
# / slow-walks connections that identify themselves as non-browser clients, so
# we present a standard desktop Chrome string.  The real Tripwire identity is
# logged server-side by the GitHub Actions environment anyway.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Default timeout for the sitemap fetch.  The IPFR sitemap can take >30 s from
# GitHub Actions runners; keep it generous because this is a one-shot bootstrap.
_SITEMAP_DEFAULT_TIMEOUT = 60

# Column order for the CSV file.
_CSV_FIELDNAMES = [
    "page_id",
    "url",
    "title",
    "snapshot_path",
    "last_modified",
    "last_checked",
]

# Regex for IPFR page identifiers: letter + 4 digits (e.g. B1012, A0042).
_PAGE_ID_RE = re.compile(r"\b([A-Z]\d{4})\b")


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def load_sitemap(csv_path: str | Path) -> list[dict[str, str]]:
    """Load an existing sitemap CSV into a list of row dicts.

    Returns an empty list if the file does not exist.
    """
    path = Path(csv_path)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def save_sitemap(rows: list[dict[str, str]], csv_path: str | Path) -> None:
    """Write the sitemap to *csv_path*, creating parent directories as needed."""
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_sitemap_from_urls(
    urls: list[str],
    existing_rows: list[dict[str, str]],
    snapshots_dir: str | Path,
) -> list[dict[str, str]]:
    """Merge a freshly-discovered list of URLs with existing sitemap rows.

    For each URL:
    - Attempt to extract a page_id from the URL path.
    - Preserve existing metadata (last_modified, last_checked) if the URL is
      already in the sitemap.
    - New URLs are added with empty metadata.

    Parameters
    ----------
    urls:
        All page URLs discovered from the sitemap XML.
    existing_rows:
        The rows currently in the sitemap CSV (may be empty).
    snapshots_dir:
        Base directory for snapshot files; used to compute snapshot_path.

    Returns
    -------
    list[dict[str, str]]
        Updated sitemap rows, sorted by page_id then url.
    """
    existing_by_url: dict[str, dict[str, str]] = {r["url"]: r for r in existing_rows}
    snapshots_base = Path(snapshots_dir)

    merged: list[dict[str, str]] = []
    for url in urls:
        if url in existing_by_url:
            merged.append(existing_by_url[url])
            continue

        page_id = _extract_page_id(url)
        snapshot_path = _snapshot_path(page_id, url, snapshots_base)
        merged.append(
            {
                "page_id": page_id,
                "url": url,
                "title": "",
                "snapshot_path": str(snapshot_path),
                "last_modified": "",
                "last_checked": "",
            }
        )

    # Sort: known IDs first (by ID), unknown at the end (by URL).
    merged.sort(key=lambda r: (r["page_id"] == "", r["page_id"] or r["url"]))
    return merged


def update_row(
    row: dict[str, str],
    *,
    title: str | None = None,
    last_modified: str | None = None,
    last_checked: str | None = None,
) -> dict[str, str]:
    """Return an updated copy of *row* with the given fields replaced."""
    updated = dict(row)
    if title is not None:
        updated["title"] = title
    if last_modified is not None:
        updated["last_modified"] = last_modified
    if last_checked is not None:
        updated["last_checked"] = last_checked
    return updated


def fetch_sitemap_xml(
    url: str,
    session: Any,
    *,
    timeout: int = _SITEMAP_DEFAULT_TIMEOUT,
    force_selenium: bool = False,
) -> str:
    """Fetch the IPFR sitemap XML, falling back to Selenium on failure.

    Strategy (mirrors ``src.scraper.scrape_and_normalise``):

    1. Attempt a ``requests.Session.get`` with a browser-like User-Agent and
       generous timeout.
    2. If the request raises, returns non-200, or the response body trips a
       known bot-detection block signature, retry the fetch through a fresh
       Selenium Chrome driver using a JavaScript ``fetch()`` call — this
       returns the raw XML verbatim (Chrome's XML viewer would otherwise wrap
       it in view-source HTML).

    Parameters
    ----------
    url:
        Sitemap XML URL (e.g. ``https://.../sitemap.xml``).
    session:
        A ``requests.Session`` configured by the caller.  If it does not
        already advertise a browser User-Agent, one is applied for the
        duration of the call.
    timeout:
        Read timeout for the initial requests-based attempt.
    force_selenium:
        Skip the requests attempt entirely and go straight to Selenium.

    Returns
    -------
    str
        Raw sitemap XML document.

    Raises
    ------
    src.errors.RetryableError
        If both requests and Selenium fetches fail.
    src.errors.PermanentError
        If the response is a known block / CAPTCHA page after Selenium.
    """
    from src.errors import RetryableError, PermanentError, http_error, captcha_error

    xml_text: str | None = None
    requests_err: str | None = None

    if not force_selenium:
        try:
            resp = session.get(
                url,
                timeout=timeout,
                headers={"User-Agent": BROWSER_USER_AGENT, "Accept": "application/xml, text/xml, */*"},
            )
            if resp.status_code == 200:
                xml_text = resp.text
            else:
                # Non-200 — record the reason and try Selenium.
                requests_err = f"HTTP {resp.status_code}"
                logger.warning(
                    "Sitemap requests fetch returned %s for %s; will try Selenium fallback.",
                    resp.status_code,
                    url,
                )
        except Exception as exc:
            requests_err = str(exc)
            logger.warning(
                "Sitemap requests fetch failed for %s: %s; will try Selenium fallback.",
                url,
                exc,
            )

    if xml_text is not None and _looks_like_block_page(xml_text):
        logger.info(
            "Block signature detected in requests response for sitemap %s; "
            "falling back to Selenium.",
            url,
        )
        xml_text = None

    if xml_text is None:
        try:
            from src.scraper import fetch_raw_with_selenium
        except ImportError as exc:
            raise RetryableError(
                f"Sitemap fetch failed for {url} "
                f"(requests error: {requests_err}; Selenium unavailable: {exc})"
            ) from exc

        selenium_text = fetch_raw_with_selenium(url, timeout_seconds=timeout)
        if selenium_text is None:
            raise RetryableError(
                f"Sitemap fetch failed for {url} via both requests "
                f"({requests_err or 'blocked'}) and Selenium."
            )

        if _looks_like_block_page(selenium_text):
            raise captcha_error(url)

        xml_text = selenium_text

    return xml_text


def _looks_like_block_page(text: str) -> bool:
    """Return True if *text* contains a known bot-detection block signature."""
    try:
        from src.scraper import _has_block_signature
        return _has_block_signature(text)
    except ImportError:
        return False


def parse_sitemap_xml(xml_text: str) -> list[str]:
    """Extract page URLs from a sitemap XML document.

    Handles both standard sitemaps (``<loc>`` tags) and sitemap index files
    (``<sitemap>`` elements containing ``<loc>``).  Only HTTP/HTTPS URLs are
    returned.
    """
    # Simple regex-based extraction — avoids an xml.etree dependency on malformed docs.
    urls = re.findall(r"<loc>\s*(https?://[^\s<]+)\s*</loc>", xml_text, re.IGNORECASE)
    return [u.strip() for u in urls]


def current_utc_date() -> str:
    """Return today's date as an ISO 8601 date string (YYYY-MM-DD) in UTC."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_page_id(url: str) -> str:
    """Try to extract an IPFR page_id from the URL path; fall back to a hash."""
    parsed = urllib.parse.urlparse(url)
    path_part = parsed.path.rstrip("/").split("/")[-1]
    m = _PAGE_ID_RE.search(path_part.upper())
    if m:
        return m.group(1)
    # Fall back: stable 6-char hex derived from the URL.
    return "X" + hashlib.sha256(url.encode()).hexdigest()[:5].upper()


def _snapshot_path(page_id: str, url: str, base: Path) -> Path:
    """Compute the expected snapshot file path for a page."""
    safe_name = re.sub(r"[^\w\-]", "_", page_id or _extract_page_id(url))
    return base / f"{safe_name}.md"
