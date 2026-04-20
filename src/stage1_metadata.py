"""
src/stage1_metadata.py

Stage 1 of the Tripwire pipeline: Metadata Probe (Section 3.1).

Purpose: determine whether a source has changed at all since the last check
using the cheapest possible signals.  Sources that haven't changed are
immediately skipped — no scraping, no diff, no scoring.

Probe signals (checked in order of cheapness):
  1. HTTP ETag or Last-Modified header comparison
  2. Content-Length header comparison
  3. Version identifier (FRL API: registerId of latest compiled version)
  4. RSS feed: presence of items newer than the last-checked timestamp

Decision rule: if ANY signal indicates a change (or no signals are available),
proceed to Stage 2.  Only skip if signals are present AND all indicate no change.

Source registry: data/influencer_sources/source_registry.csv
Columns: source_id, url, title, source_type, importance, check_frequency,
         notes, force_selenium.

All network calls are wrapped with RetryableError / PermanentError so the
retry layer in src/retry.py handles transient failures.
"""

from __future__ import annotations

import csv
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

_REGISTRY_FIELDNAMES = [
    "source_id",
    "url",
    "title",
    "source_type",
    "importance",
    "check_frequency",
    "notes",
    "force_selenium",
]

# Check frequencies mapped to days.
_FREQUENCY_DAYS: dict[str, int] = {
    "daily": 1,
    "weekly": 7,
    "fortnightly": 14,
    "monthly": 30,
    "quarterly": 91,
}


def load_source_registry(csv_path: str | Path) -> list[dict[str, Any]]:
    """Load the influencer source registry CSV.

    Returns a list of source dicts with typed fields:
      importance     — float
      force_selenium — bool
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Source registry not found: {path}")

    sources: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            row = dict(row)
            # Parse importance as float.
            try:
                row["importance"] = float(row.get("importance", 0.5))
            except (ValueError, TypeError):
                row["importance"] = 0.5
            # Parse force_selenium as bool (accepts "true"/"false", case-insensitive).
            row["force_selenium"] = str(row.get("force_selenium", "false")).strip().lower() == "true"
            sources.append(row)

    return sources


def save_source_registry(sources: list[dict[str, Any]], csv_path: str | Path) -> None:
    """Persist the source registry to CSV (preserves all columns)."""
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Serialise typed fields back to strings.
    rows = []
    for s in sources:
        row = dict(s)
        row["importance"] = str(row.get("importance", 0.5))
        rows.append(row)

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_REGISTRY_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def is_due_for_check(
    source: dict[str, Any],
    last_checked: str | None,
    frequency_override: str | None = None,
) -> bool:
    """Return True if the source is due for a check based on its frequency.

    Parameters
    ----------
    source:
        Source registry row.
    last_checked:
        ISO 8601 date of the last check, or None if never checked.
    frequency_override:
        If provided, replaces the per-source CSV frequency. Pass ``"all"`` to
        force every source to run regardless of last_checked.
    """
    if frequency_override and frequency_override.lower() == "all":
        return True

    if not last_checked:
        return True

    frequency = (frequency_override or source.get("check_frequency", "weekly")).lower()
    interval_days = _FREQUENCY_DAYS.get(frequency, 7)

    try:
        last_dt = datetime.fromisoformat(last_checked).date()
    except ValueError:
        return True

    today = datetime.now(tz=timezone.utc).date()
    return (today - last_dt).days >= interval_days


# ---------------------------------------------------------------------------
# Metadata probe
# ---------------------------------------------------------------------------


class ProbeResult:
    """Result of a Stage 1 metadata probe for a single source."""

    def __init__(
        self,
        source_id: str,
        url: str,
        decision: str,
        signals: dict[str, Any],
        error: str | None = None,
    ) -> None:
        self.source_id = source_id
        self.url = url
        # decision: "changed" | "unchanged" | "unknown" | "error" | "not_due"
        self.decision = decision
        self.signals = signals
        self.error = error

    @property
    def should_proceed(self) -> bool:
        """Return True if Stage 2 should be run for this source."""
        return self.decision in ("changed", "unknown")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "url": self.url,
            "decision": self.decision,
            "signals": self.signals,
            "error": self.error,
        }


def probe_source(
    source: dict[str, Any],
    stored_signals: dict[str, Any] | None,
    session: Any,
) -> ProbeResult:
    """Run Stage 1 metadata probe for a single source.

    Parameters
    ----------
    source:
        Row from the source registry.
    stored_signals:
        Previously stored probe signals for this source (from last run),
        or None if this is the first run.
    session:
        A requests.Session (or compatible) object.

    Returns
    -------
    ProbeResult
        Contains the probe decision and collected signals.
    """
    source_id = source["source_id"]
    url = source["url"]
    source_type = source.get("source_type", "webpage").lower()

    try:
        if source_type == "frl":
            result = _probe_frl(source, stored_signals, session)
        elif source_type == "rss":
            result = _probe_rss(source, stored_signals, session)
        else:
            result = _probe_webpage(source, stored_signals, session)
        return result
    except Exception as exc:
        logger.error("Stage 1 [%s]: probe failed — %s", source_id, exc)
        return ProbeResult(
            source_id=source_id,
            url=url,
            decision="unknown",  # fail-open: unknown → proceed to Stage 2
            signals={},
            error=str(exc),
        )


def _probe_webpage(
    source: dict[str, Any],
    stored: dict[str, Any] | None,
    session: Any,
) -> ProbeResult:
    """Probe a generic webpage via HTTP HEAD."""
    from src.errors import RetryableError, http_error
    source_id = source["source_id"]
    url = source["url"]

    try:
        resp = session.head(url, timeout=15, allow_redirects=True)
    except Exception as exc:
        raise RetryableError(f"HEAD request failed for {url}: {exc}") from exc

    if resp.status_code >= 400:
        from src.errors import http_error
        raise http_error(resp.status_code, url)

    new_signals: dict[str, Any] = {
        "etag": resp.headers.get("ETag"),
        "last_modified": resp.headers.get("Last-Modified"),
        "content_length": resp.headers.get("Content-Length"),
    }
    new_signals = {k: v for k, v in new_signals.items() if v is not None}

    decision = _compare_signals(new_signals, stored or {})
    return ProbeResult(source_id=source_id, url=url, decision=decision, signals=new_signals)


# Base URL of the official Federal Register of Legislation REST API.
_FRL_API_BASE = "https://api.prod.legislation.gov.au"

# Matches an FRL titleId such as C2004A04969, F1996B00084, F2024L01179, C2025Q00003.
_FRL_TITLE_ID_RE = re.compile(r"^[A-Z]\d{4}[A-Z]\w+$")


def _extract_frl_title_id(url: str) -> str | None:
    """Extract the FRL titleId from a legislation.gov.au URL.

    Handles both the current URL convention (titleId as the first path
    segment after the host, followed by a version specifier such as
    ``latest`` or ``asmade`` and a format suffix) and the legacy
    ``/Series/<titleId>`` form.

    Returns ``None`` if no plausible titleId is found.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    segments = [s for s in parsed.path.split("/") if s]
    if not segments:
        return None

    # Legacy form: /Series/<titleId>
    if segments[0].lower() == "series" and len(segments) >= 2:
        return segments[1]

    # Current form: the first segment is the titleId when it matches the
    # expected FRL identifier shape; otherwise scan for the first match.
    for seg in segments:
        if _FRL_TITLE_ID_RE.match(seg):
            return seg

    # Fall back to the first segment for URLs we don't recognise.
    return segments[0]


def _probe_frl(
    source: dict[str, Any],
    stored: dict[str, Any] | None,
    session: Any,
) -> ProbeResult:
    """Probe an FRL (Federal Register of Legislation) source.

    Uses the official FRL REST API to retrieve the latest compiled Version for
    the title without downloading the full legislation text.

    The change signal is the ``registerId`` of the latest compiled version —
    a stable, semantically meaningful identifier that changes only when a new
    compilation is registered (e.g. ``F2024C00123``).

    API reference:
        GET /v1/Versions/Find(titleId='{titleId}',asAtSpecification='Latest')
        Base URL: https://api.prod.legislation.gov.au
        Auth: none required for public read

    Source URLs in the registry follow the current FRL convention, where the
    titleId is the first path segment after the host, e.g.:
        https://www.legislation.gov.au/C2004A04969/latest/text  →  C2004A04969
        https://www.legislation.gov.au/F1996B00084/latest/text  →  F1996B00084
        https://www.legislation.gov.au/C2021A00013/asmade/text  →  C2021A00013

    The legacy ``/Series/<titleId>`` form is also tolerated for backward
    compatibility.
    """
    source_id = source["source_id"]
    url = source["url"]

    title_id = _extract_frl_title_id(url)
    if not title_id:
        logger.warning(
            "Stage 1 [%s]: FRL URL has no extractable titleId — FALLBACK to HTTP HEAD probe",
            source_id,
        )
        return _probe_webpage(source, stored, session)

    endpoint = (
        f"{_FRL_API_BASE}/v1/Versions/Find("
        f"titleId='{title_id}',asAtSpecification='Latest')"
    )

    try:
        resp = session.get(
            endpoint,
            headers={"Accept": "application/json"},
            timeout=20,
        )
        resp.raise_for_status()
        version = resp.json()
        register_id = version.get("registerId", "")
        if not register_id:
            logger.warning(
                "Stage 1 [%s]: FRL API returned no registerId — FALLBACK to HTTP HEAD probe",
                source_id,
            )
            return _probe_webpage(source, stored, session)

        new_signals: dict[str, Any] = {
            "register_id": register_id,
            "compilation_number": version.get("compilationNumber", ""),
            "start": version.get("start", ""),
        }
    except Exception as exc:
        logger.warning(
            "Stage 1 [%s]: FRL API unreachable — FALLBACK to HTTP HEAD probe: %s",
            source_id, exc,
        )
        return _probe_webpage(source, stored, session)

    decision = _compare_signals(new_signals, stored or {})
    return ProbeResult(source_id=source_id, url=url, decision=decision, signals=new_signals)


def _probe_rss(
    source: dict[str, Any],
    stored: dict[str, Any] | None,
    session: Any,
) -> ProbeResult:
    """Probe an RSS feed for new items since the last check."""
    from src.errors import RetryableError
    source_id = source["source_id"]
    url = source["url"]

    try:
        resp = session.get(url, timeout=20)
        resp.raise_for_status()
    except Exception as exc:
        raise RetryableError(f"RSS fetch failed for {url}: {exc}") from exc

    item_ids = _extract_rss_item_ids(resp.text)
    latest_id = item_ids[0] if item_ids else None
    item_count = len(item_ids)

    new_signals: dict[str, Any] = {
        "latest_item_id": latest_id,
        "item_count": item_count,
    }

    stored_signals = stored or {}
    if not stored_signals:
        # First run — no stored baseline.
        decision = "unknown"
    elif new_signals.get("latest_item_id") != stored_signals.get("latest_item_id"):
        decision = "changed"
    elif new_signals.get("item_count") != stored_signals.get("item_count"):
        decision = "changed"
    else:
        decision = "unchanged"

    return ProbeResult(source_id=source_id, url=url, decision=decision, signals=new_signals)


def _compare_signals(
    new: dict[str, Any],
    stored: dict[str, Any],
) -> str:
    """Compare new probe signals against stored signals.

    Returns "changed", "unchanged", or "unknown".

    Decision logic:
    - No new signals available → "unknown" (proceed to Stage 2).
    - No stored signals → "unknown" (first run, no baseline to compare).
    - Any signal changed → "changed".
    - All signals match → "unchanged".
    """
    if not new:
        return "unknown"
    if not stored:
        return "unknown"

    # Check each signal that appears in both dicts.
    common_keys = set(new.keys()) & set(stored.keys())
    if not common_keys:
        return "unknown"

    for key in common_keys:
        if new[key] != stored[key]:
            return "changed"

    return "unchanged"


def _extract_rss_item_ids(xml_text: str) -> list[str]:
    """Extract item GUIDs (or links as fallback) from RSS XML text."""
    guids = re.findall(r"<guid[^>]*>\s*([^\s<]+)\s*</guid>", xml_text, re.IGNORECASE)
    if guids:
        return guids
    # Fallback: use <link> elements from <item> blocks.
    links = re.findall(r"<item[^>]*>.*?<link>\s*([^\s<]+)\s*</link>", xml_text,
                       re.DOTALL | re.IGNORECASE)
    return links


# ---------------------------------------------------------------------------
# Convenience: probe all due sources
# ---------------------------------------------------------------------------


def probe_all_due_sources(
    sources: list[dict[str, Any]],
    stored_signals_by_id: dict[str, dict[str, Any]],
    last_checked_by_id: dict[str, str],
    session: Any,
) -> list[ProbeResult]:
    """Probe all sources that are due for a check.

    Parameters
    ----------
    sources:
        Full source registry list.
    stored_signals_by_id:
        Dict mapping source_id → previously stored probe signals.
    last_checked_by_id:
        Dict mapping source_id → ISO 8601 date of last check.
    session:
        Requests session.

    Returns
    -------
    list[ProbeResult]
        Results for every source that was probed (due sources only).
        Sources not yet due have decision="not_due".
    """
    results: list[ProbeResult] = []

    for source in sources:
        sid = source["source_id"]
        url = source["url"]
        last_checked = last_checked_by_id.get(sid)

        if not is_due_for_check(source, last_checked):
            results.append(ProbeResult(
                source_id=sid,
                url=url,
                decision="not_due",
                signals={},
            ))
            continue

        stored = stored_signals_by_id.get(sid)
        result = probe_source(source, stored, session)
        results.append(result)
        logger.info(
            "Probe %s (%s): %s", sid, source.get("source_type", "?"), result.decision
        )

    return results
