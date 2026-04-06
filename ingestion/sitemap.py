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
