"""
src/stage3_diff.py

Stage 3 of the Tripwire pipeline: Diff Generation (Section 3.3).

Purpose: produce a precise representation of what changed, formatted
appropriately for the source type.

Source-type routing
-------------------
Webpage    → unified .diff file (old snapshot vs new snapshot).
FRL        → retrieve the Explanatory Statement (regulations) or bill summary
             (Acts) from the FRL API / ParlInfo.  A compilation number change
             is inherently significant, so no .diff file is needed.  The
             explainer text is saved as the current snapshot and older
             explainers are rotated into versioned backlog files.  When no
             explainer can be retrieved a minimal compilation-change notice is
             returned instead.
RSS        → extract new items (and detect mutated items) since last check.

Snapshot management
-------------------
For webpage sources, the current normalised text is written to a snapshot file
under ``data/influencer_sources/snapshots/<source_id>/``.  For FRL sources the
most recent explainer is written to the same location and older versions are
kept as backlog (``<source_id>.v1.txt``, etc.).  Up to
``content_versions_retained`` (default: 6) previous versions are kept on disk;
older versions are deleted.

The snapshot directory is committed to Git at the end of each pipeline run
(Section 7.2 — handled by the pipeline orchestrator, not this module).

Diff normalisation
------------------
The raw diff or extracted content is normalised into a canonical plain-text
string before being returned for downstream stages (Section 3.3):
  - HTML entities decoded.
  - Whitespace collapsed (non-breaking spaces replaced with regular space).
  - Residual formatting artefacts stripped.
  - Unicode NFC.
  - Does NOT lowercase (NER/YAKE need case).
  - Does NOT strip punctuation (YAKE needs sentence boundaries).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default snapshot base directory (relative to repo root).
_DEFAULT_SNAPSHOT_DIR = Path("data/influencer_sources/snapshots")
_DEFAULT_VERSIONS_RETAINED = 6

# Official FRL REST API base URL and Explanatory Statement document types to try.
_FRL_API_BASE = "https://api.prod.legislation.gov.au"
# Public legislation.gov.au web base URL (used for the ES web download fallback).
_FRL_WEB_BASE = "https://www.legislation.gov.au"
# ES is tried first; SupplementaryES is the fallback for instruments that only
# have a supplementary explanatory statement rather than a standalone one.
_FRL_ES_TYPES = ("ES", "SupplementaryES")

# Standalone heading text that marks the end of the substantive ES content.
# Any line ≤100 chars (stripped) that contains one of these phrases triggers truncation.
_FRL_STOP_HEADINGS = ("Attachment A", "Schedule 1", "Notes on sections")
# Minimum word count for a parlinfo bill summary before falling back to Bills Digest.
_PARLINFO_MIN_WORDS = 100

# Matches an FRL titleId such as C2004A04969, F1996B00084, F2024L01179.
_FRL_TITLE_ID_RE = re.compile(r"^[A-Z]\d{4}[A-Z]\w+$")
# Matches an Act series titleId (e.g. C2023A00074).  Used to recognise an
# amending Act in registerId / markdown fields when the structured fields are
# missing.
_FRL_ACT_SERIES_RE = re.compile(r"^C\d{4}A\d+$")
_FRL_ACT_ID_IN_TEXT_RE = re.compile(r"\bC\d{4}A\d+\b")


def _extract_frl_title_id(url: str) -> str | None:
    """Extract the FRL titleId from a legislation.gov.au URL.

    Handles the current convention (``/<titleId>/latest/text``) and the legacy
    ``/Series/<titleId>`` form.  Returns None if no plausible titleId is found.
    """
    from urllib.parse import urlparse
    segments = [s for s in urlparse(url).path.split("/") if s]
    if not segments:
        return None
    if segments[0].lower() == "series" and len(segments) >= 2:
        return segments[1]
    for seg in segments:
        if _FRL_TITLE_ID_RE.match(seg):
            return seg
    return segments[0]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class DiffResult:
    """Result of Stage 3 diff generation for a single source."""

    def __init__(
        self,
        source_id: str,
        source_type: str,
        diff_type: str,             # "unified_diff" | "explainer" | "rss_items" | "first_run"
        normalised_diff: str,
        diff_path: str | None = None,
        diff_size_chars: int = 0,
        normalised_size_chars: int = 0,
        rss_new_items: list[dict[str, Any]] | None = None,
        rss_mutated_items: list[dict[str, Any]] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        self.source_id = source_id
        self.source_type = source_type
        self.diff_type = diff_type
        self.normalised_diff = normalised_diff
        self.diff_path = diff_path
        self.diff_size_chars = diff_size_chars
        self.normalised_size_chars = normalised_size_chars
        self.rss_new_items = rss_new_items or []
        self.rss_mutated_items = rss_mutated_items or []
        self.warnings = warnings or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "diff_type": self.diff_type,
            "diff_path": self.diff_path,
            "diff_size_chars": self.diff_size_chars,
            "normalised_size_chars": self.normalised_size_chars,
            "rss_new_item_count": len(self.rss_new_items),
            "rss_mutated_item_count": len(self.rss_mutated_items),
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def generate_diff(
    source: dict[str, Any],
    new_text: str,
    previous_text: str | None,
    diff_lines: list[str],
    *,
    snapshot_dir: Path | str | None = None,
    versions_retained: int = _DEFAULT_VERSIONS_RETAINED,
    run_id: str = "",
    session: Any = None,
) -> DiffResult:
    """Generate the Stage 3 diff for a single source.

    Parameters
    ----------
    source:
        Source registry row (must include ``source_id``, ``source_type``,
        and optionally ``url``).
    new_text:
        Normalised plain text of the current scrape (webpages) or ``""``
        for RSS (the RSS state dict is loaded from snapshot).
    previous_text:
        Normalised plain text of the previous snapshot, or None on first run.
    diff_lines:
        Pre-computed unified diff lines from Stage 2 (for webpages).
    snapshot_dir:
        Base directory for snapshots.  Defaults to
        ``data/influencer_sources/snapshots``.
    versions_retained:
        Number of old snapshot versions to keep (default: 6).
    run_id:
        Current pipeline run identifier (used in file naming).
    session:
        requests.Session for FRL explainer retrieval.

    Returns
    -------
    DiffResult
    """
    snap_base = Path(snapshot_dir) if snapshot_dir else _DEFAULT_SNAPSHOT_DIR
    source_id = source["source_id"]
    source_type = source.get("source_type", "webpage").lower()

    if source_type == "rss":
        return _generate_rss_diff(source, snap_base, versions_retained, session)
    elif source_type == "frl":
        return _generate_frl_diff(source, snap_base, versions_retained, session)
    else:
        return _generate_webpage_diff(
            source, new_text, previous_text, diff_lines,
            snap_base, versions_retained, run_id
        )


# ---------------------------------------------------------------------------
# Webpage diff
# ---------------------------------------------------------------------------


def _generate_webpage_diff(
    source: dict[str, Any],
    new_text: str,
    previous_text: str | None,
    diff_lines: list[str],
    snap_base: Path,
    versions_retained: int,
    run_id: str,
) -> DiffResult:
    """Generate a unified diff and update the snapshot for a webpage source."""
    source_id = source["source_id"]
    snap_dir = snap_base / source_id
    snap_dir.mkdir(parents=True, exist_ok=True)

    # Build the diff string.
    diff_text = "".join(diff_lines)
    normalised = _normalise_diff_text(diff_text)

    # Determine diff file path.
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    diff_filename = f"{source_id}_{ts}.diff" if run_id == "" else f"{source_id}_{run_id}.diff"
    diff_path = snap_dir / diff_filename

    # Write diff file (even if empty, for audit trail on first run).
    diff_path.write_text(diff_text, encoding="utf-8")
    logger.info("Stage 3 [%s]: diff saved → %s (%d chars)", source_id, diff_path, len(diff_text))

    # Rotate snapshots: rename current → versioned, write new current.
    _rotate_snapshots(snap_dir, source_id, versions_retained)
    current_snap = snap_dir / f"{source_id}.txt"
    current_snap.write_text(new_text, encoding="utf-8")
    logger.info("Stage 3 [%s]: snapshot saved → %s", source_id, current_snap)

    if previous_text is None:
        diff_type = "first_run"
    else:
        diff_type = "unified_diff"

    return DiffResult(
        source_id=source_id,
        source_type="webpage",
        diff_type=diff_type,
        normalised_diff=normalised,
        diff_path=str(diff_path),
        diff_size_chars=len(diff_text),
        normalised_size_chars=len(normalised),
    )


# ---------------------------------------------------------------------------
# FRL diff
# ---------------------------------------------------------------------------


def _generate_frl_diff(
    source: dict[str, Any],
    snap_base: Path,
    versions_retained: int,
    session: Any,
) -> DiffResult:
    """Retrieve FRL change explainer and save it as the versioned snapshot.

    A compilation number change is inherently significant — no .diff file is
    required.  The Explanatory Statement (for regulations) or bill summary (for
    Acts) is saved as ``<source_id>.txt``; the rotation system keeps a backlog
    of previous explainers as ``<source_id>.v1.txt``, ``<source_id>.v2.txt``,
    etc.

    When no explainer can be retrieved a minimal compilation-change notice is
    returned so downstream stages can still flag the change for manual review.
    """
    source_id = source["source_id"]
    warnings: list[str] = []
    snap_dir = snap_base / source_id
    snap_dir.mkdir(parents=True, exist_ok=True)

    explainer_text = None
    if session is not None:
        explainer_text, err = _fetch_frl_explainer(source, session)
        if err:
            warnings.append(err)
            logger.warning("Stage 3 [%s]: FRL explainer error: %s", source_id, err)

    if explainer_text:
        normalised = _normalise_diff_text(explainer_text)
        # Rotate previous explainers into versioned backlog, then write new one.
        _rotate_snapshots(snap_dir, source_id, versions_retained)
        snap_file = snap_dir / f"{source_id}.txt"
        snap_file.write_text(explainer_text, encoding="utf-8")
        logger.info(
            "Stage 3 [%s]: FRL explainer saved → %s (%d chars)",
            source_id, snap_file, len(explainer_text),
        )
        return DiffResult(
            source_id=source_id,
            source_type="frl",
            diff_type="explainer",
            normalised_diff=normalised,
            diff_path=str(snap_file),
            diff_size_chars=len(explainer_text),
            normalised_size_chars=len(normalised),
            warnings=warnings,
        )

    # No explainer retrieved, but the compilation change is still significant.
    # Return a minimal notice so downstream stages can flag it for manual review.
    warnings.append(
        f"FRL explainer unavailable for {source_id}; "
        "compilation change recorded with no detail."
    )
    logger.warning(
        "Stage 3 [%s]: FRL explainer unavailable — compilation change recorded with no detail",
        source_id,
    )
    fallback_text = (
        f"Compilation updated for {source_id}. "
        "No Explanatory Statement could be retrieved automatically."
    )
    normalised = _normalise_diff_text(fallback_text)
    return DiffResult(
        source_id=source_id,
        source_type="frl",
        diff_type="compilation_change",
        normalised_diff=normalised,
        diff_path=None,
        diff_size_chars=len(fallback_text),
        normalised_size_chars=len(normalised),
        warnings=warnings,
    )


def _truncate_at_es_stop_heading(text: str) -> str:
    """Truncate ES text at the first standalone heading matching a stop phrase.

    A "standalone heading" is a non-empty line whose stripped length is ≤100 chars
    and which contains one of _FRL_STOP_HEADINGS (case-insensitive).  The heading
    line itself is excluded; everything before it is returned stripped.

    If no matching heading is found the full text is returned unchanged.
    """
    lines = text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and len(stripped) <= 100:
            if any(phrase.lower() in stripped.lower() for phrase in _FRL_STOP_HEADINGS):
                return "\n".join(lines[:i]).strip()
    return text


def _fetch_frl_version_with_reasons(title_id: str, session: Any) -> dict[str, Any]:
    """Fetch the latest compiled Version for *title_id*, with its reasons.

    Calls the FRL function endpoint
    ``/v1/Versions/Find(titleId='{titleId}',asAtSpecification='Latest')``,
    which returns a single ``Version`` object whose schema already includes
    the ``reasons`` array (see docs/FRL-API/FRL_Instructions.json — the
    ``Version`` schema declares ``reasons`` as an array of ``ReasonForVersion``).
    No OData ``$expand`` or ``$filter`` is required, and the live API
    rejects the equivalent list-endpoint query (``/v1/Versions?$filter=…
    and isLatest eq true&$expand=Reasons``) with HTTP 400.  This matches
    the working approach used in ``docs/Reference-Code/download_es.py``.

    API reference:
        GET /v1/Versions/Find(titleId='{titleId}',asAtSpecification='Latest')
        Accept: application/json
        Base URL: https://api.prod.legislation.gov.au

    Raises ValueError if no version is returned (empty body).
    Raises on HTTP error or connection failure; caller is responsible for
    handling.
    """
    endpoint = (
        f"{_FRL_API_BASE}/v1/Versions/Find("
        f"titleId='{title_id}',"
        f"asAtSpecification='Latest')"
    )
    logger.debug("FRL Versions/Find: GET %s", endpoint)
    resp = session.get(
        endpoint,
        headers={"Accept": "application/json"},
        timeout=20,
    )
    logger.debug("FRL Versions/Find: status=%s", resp.status_code)
    resp.raise_for_status()
    data = resp.json()
    # Find() returns a single Version object directly (not an OData list).
    if not isinstance(data, dict) or not data:
        raise ValueError(f"No latest version found for titleId '{title_id}'")
    return data


def _extract_act_id_from_markdown(markdown: str) -> str | None:
    """Return the first Act series titleId mentioned in *markdown*, or None.

    The FRL API populates ``reason.markdown`` with a human-readable
    description of the amendment.  When the structured ``amendedByTitle`` /
    ``affectedByTitle`` fields are missing, the Act titleId is often present
    in the markdown text in canonical form (e.g. ``C2023A00074``).
    """
    if not markdown:
        return None
    m = _FRL_ACT_ID_IN_TEXT_RE.search(markdown)
    return m.group(0) if m else None


def _extract_amending_instruments(version_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract amending instrument info from a Version object.

    Discovery layers (run in order; results deduplicated by titleId):

    Layer 1 — registerId check:
        If ``version_data["registerId"]`` matches the Act series pattern
        ``C\\d{4}A\\d+``, treat it as an amending Act.  Sometimes the
        registerId IS the amending Act's titleId.

    Layer 2 — reasons array (with markdown scan):
        Walk ``version_data["reasons"]`` for ``affect == "Amend"``.  Both
        ``affectedByTitle.titleId`` AND ``amendedByTitle.titleId`` are
        checked independently because either can be empty when the other
        holds the correct id.  As a final within-reason fallback, scan
        ``reason["markdown"]`` for a canonical Act series titleId.

    Returns a list of ``{"title_id": str, "series_type": str}`` dicts.
    ``series_type`` is "Act", "SR", "SLI", or "" if the API did not return
    it (the caller resolves missing types via the Titles API).

    The Affect API fallback (Layer 3) is implemented separately in
    ``_discover_amending_via_affect_api`` because it requires HTTP and the
    principal title id; ``_fetch_frl_explainer`` invokes it when this
    function returns an empty list.
    """
    instruments: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(title_id: str | None, series_type: str = "") -> None:
        if not title_id or title_id in seen:
            return
        seen.add(title_id)
        instruments.append({"title_id": title_id, "series_type": series_type})

    # Layer 1: registerId check.
    register_id = version_data.get("registerId") or ""
    if isinstance(register_id, str) and _FRL_ACT_SERIES_RE.match(register_id):
        # registerId pattern alone tells us this is an Act series.
        _add(register_id, "Act")

    # Layer 2: reasons array.
    for reason in version_data.get("reasons", []):
        if reason.get("affect") != "Amend":
            continue

        # Both fields are checked independently — either can be empty when
        # the other holds the correct id (and both can be populated with
        # different ids in some responses).
        for field in ("affectedByTitle", "amendedByTitle"):
            ref = reason.get(field) or {}
            _add(ref.get("titleId"), ref.get("seriesType", "") or "")

        # Within-reason last resort: scan the markdown blob for a canonical
        # Act titleId.  Series type is unknown here, so leave it empty and
        # let the Titles API resolve it.
        if not (reason.get("affectedByTitle") or reason.get("amendedByTitle")):
            md_id = _extract_act_id_from_markdown(reason.get("markdown", ""))
            if md_id:
                _add(md_id, "")

    return instruments


def _discover_amending_via_affect_api(
    title_id: str,
    session: Any,
    *,
    compilation_start_date: str | None = None,
) -> list[dict[str, Any]]:
    """Last-resort discovery via the FRL Affects API.

    Runs only when the registerId / reasons-array layers yielded nothing.
    Tries ``/v1/_AffectsSearch`` first and falls back to ``/v1/Affect`` on
    404; both endpoints exist in the live API and either may be authoritative
    depending on the title.

    The ``$filter`` query string is built with ``urllib.parse.quote`` (not
    ``urlencode``) to preserve the literal ``$`` and the OData operators.

    If ``compilation_start_date`` is supplied (``YYYY-MM-DD``), results are
    narrowed to instruments whose effective date matches that day —
    necessary because the Affect API returns the title's full amendment
    history, not just the changes that triggered the latest compilation.

    Returns a list of ``{"title_id": str, "series_type": str}`` dicts.  Empty
    on any failure; callers use this as a best-effort fallback.
    """
    from urllib.parse import quote

    filter_expr = f"affectedTitleId eq '{title_id}'"
    encoded_filter = quote(filter_expr, safe="'")
    query = f"$filter={encoded_filter}&$top=50"
    endpoints = [
        f"{_FRL_API_BASE}/v1/_AffectsSearch?{query}",
        f"{_FRL_API_BASE}/v1/Affect?{query}",
    ]

    items: list[dict[str, Any]] = []
    for endpoint in endpoints:
        logger.debug("FRL Affects API [%s]: GET %s", title_id, endpoint)
        try:
            resp = session.get(
                endpoint,
                headers={"Accept": "application/json"},
                timeout=20,
            )
        except Exception as exc:
            logger.debug(
                "FRL Affects API [%s]: network error: %s", title_id, exc,
            )
            continue
        if resp.status_code == 404:
            continue
        if resp.status_code != 200:
            logger.debug(
                "FRL Affects API [%s]: status=%s", title_id, resp.status_code,
            )
            continue
        try:
            data = resp.json()
        except Exception as exc:
            logger.debug(
                "FRL Affects API [%s]: invalid JSON: %s", title_id, exc,
            )
            continue
        items = data.get("value", []) if isinstance(data, dict) else list(data)
        if items:
            break

    instruments: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        # Optional compilation-date filter — keep instruments whose
        # effective date matches the start of this compilation.  When the
        # date isn't provided, accept all results.
        if compilation_start_date:
            effective = (
                item.get("effectiveDate")
                or item.get("startDate")
                or item.get("commencementDate")
                or ""
            )
            if effective and not effective.startswith(compilation_start_date):
                continue

        # The amending instrument's id appears under one of these keys
        # depending on which endpoint served the response.
        ref = (
            item.get("amendedByTitle")
            or item.get("affectedByTitle")
            or {}
        )
        amending_id = (
            ref.get("titleId")
            or item.get("amendedByTitleId")
            or item.get("affectingTitleId")
        )
        if not amending_id or amending_id in seen:
            continue
        seen.add(amending_id)
        series_type = ref.get("seriesType", "") if isinstance(ref, dict) else ""
        instruments.append({"title_id": amending_id, "series_type": series_type})
    return instruments


def _get_asmade_date(amending_title_id: str, session: Any) -> str | None:
    """Fetch the as-made registration date for an amending instrument.

    Calls Versions/Find(titleId=..., asAtSpecification='AsMade') and returns
    the ``YYYY-MM-DD`` portion of the ``start`` field.  Returns None on
    failure; callers should treat the absence of a date as "skip the web
    fallback" rather than as a hard error.

    The website's direct download URL (``/{id}/asmade/{date}/es/original/word``)
    requires this date segment.
    """
    endpoint = (
        f"{_FRL_API_BASE}/v1/Versions/Find("
        f"titleId='{amending_title_id}',"
        f"asAtSpecification='AsMade')"
    )
    logger.debug("FRL AsMade date [%s]: GET %s", amending_title_id, endpoint)
    try:
        resp = session.get(endpoint, timeout=15)
        if resp.status_code != 200:
            logger.debug(
                "FRL AsMade date [%s]: status=%s",
                amending_title_id, resp.status_code,
            )
            return None
        data = resp.json()
    except Exception as exc:
        logger.debug(
            "FRL AsMade date [%s]: lookup failed: %s",
            amending_title_id, exc,
        )
        return None

    start = data.get("start", "") if isinstance(data, dict) else ""
    # 'start' format: "2024-10-14T00:00:00" — take the date prefix.
    date_part = start[:10] if start and len(start) >= 10 else None
    if date_part:
        logger.debug("FRL AsMade date [%s]: %s", amending_title_id, date_part)
    return date_part


def _download_es_via_web(
    amending_title_id: str,
    asmade_date: str,
    session: Any,
) -> tuple[bytes | None, str | None]:
    """Fallback: download the ES Word document directly from the public site.

    Mirrors what the website's Downloads tab serves and works even when the
    API ``documents/find()`` endpoint returns 404 (e.g. instruments whose ES
    was lodged as a direct file rather than through the standard API
    pathway).  Tries ES then SupplementaryES.

    Returns ``(bytes, None)`` on success or ``(None, error_message)`` on
    failure.  HTML responses < 50 KB are rejected as error pages
    masquerading as 200.
    """
    web_paths = [
        (doc_type,
         f"{_FRL_WEB_BASE}/{amending_title_id}/asmade/{asmade_date}/"
         f"{doc_type.lower()}/original/word")
        for doc_type in _FRL_ES_TYPES
    ]
    last_error: str | None = None

    for doc_type, web_url in web_paths:
        logger.debug(
            "FRL ES web fallback [%s]: GET %s",
            amending_title_id, web_url,
        )
        try:
            resp = session.get(web_url, timeout=60, allow_redirects=True)
        except Exception as exc:
            last_error = f"web fallback network error ({doc_type}): {exc}"
            logger.debug("FRL ES web fallback [%s]: %s", amending_title_id, last_error)
            continue

        if resp.status_code == 404:
            last_error = f"web fallback {doc_type}: 404"
            continue
        if resp.status_code != 200:
            last_error = f"web fallback {doc_type}: status {resp.status_code}"
            continue

        content_type = resp.headers.get("Content-Type", "") or ""
        # Reject HTML error pages masquerading as 200 OK.
        if "html" in content_type.lower() and len(resp.content or b"") < 50_000:
            last_error = f"web fallback {doc_type}: HTML error page"
            logger.debug(
                "FRL ES web fallback [%s]: %s", amending_title_id, last_error,
            )
            continue

        logger.debug(
            "FRL ES web fallback [%s]: %s success bytes=%d",
            amending_title_id, doc_type, len(resp.content or b""),
        )
        return resp.content, None

    return None, last_error or "web fallback exhausted"


def _fetch_regulation_explainer(
    amending_title_id: str,
    session: Any,
) -> tuple[str | None, str | None]:
    """Fetch and truncate the ES DOCX for a regulation amending instrument.

    Two-pass strategy (mirrors the working approach in
    ``docs/Reference-Code/download_es.py``):

    Pass 1 — FRL API documents endpoint:
        GET {_FRL_API_BASE}/v1/documents/find(
            titleid='{amendingTitleId}',
            asatspecification='AsMade',    # the instrument is fixed at AsMade;
                                           # 'Latest' returns 404 for many real
                                           # amending instruments.
            type='ES' | 'SupplementaryES',
            format='Word')

        A single GET is issued (no separate metadata probe).  A response is
        treated as a miss when ``status == 404`` OR when ``status == 200`` AND
        ``Content-Type`` contains "json" (metadata-only response, no file).

    Pass 2 — Public web URL fallback (only if all API attempts missed):
        Resolve the as-made date via ``Versions/Find(...,'AsMade')``, then
        GET https://www.legislation.gov.au/{id}/asmade/{date}/es/original/word
        (and the SupplementaryES variant).  Used because some instruments'
        ES documents are lodged as direct files served by the website but
        not exposed through the API ``documents/find()`` endpoint.

    On binary success the DOCX is parsed via mammoth → trafilatura
    (``extract_plain_text_from_docx``) and truncated at the first standalone
    heading in ``_FRL_STOP_HEADINGS``.

    Returns ``(text, error_message)``.  On success ``error_message`` is None.
    """
    from src.scraper import extract_plain_text_from_docx

    errors: list[str] = []

    # --- Pass 1: FRL API ---
    api_all_404 = True
    for doc_type in _FRL_ES_TYPES:
        endpoint = (
            f"{_FRL_API_BASE}/v1/documents/find("
            f"titleid='{amending_title_id}',"
            f"asatspecification='AsMade',"
            f"type='{doc_type}',"
            f"format='Word')"
        )
        logger.debug(
            "FRL ES API [%s]: type=%s → %s",
            amending_title_id, doc_type, endpoint,
        )
        try:
            resp = session.get(endpoint, timeout=60)
        except Exception as exc:
            api_all_404 = False
            errors.append(f"API {doc_type} network error: {exc}")
            continue

        if resp.status_code == 404:
            logger.debug(
                "FRL ES API [%s]: %s → 404", amending_title_id, doc_type,
            )
            continue

        if resp.status_code != 200:
            api_all_404 = False
            errors.append(f"API {doc_type}: status {resp.status_code}")
            continue

        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "json" in content_type:
            # Metadata-only response (no file).  Treat as a miss and continue.
            logger.debug(
                "FRL ES API [%s]: %s → 200 + JSON metadata (no file)",
                amending_title_id, doc_type,
            )
            continue

        try:
            text = extract_plain_text_from_docx(resp.content)
        except Exception as exc:
            return None, (
                f"FRL ES extraction failed for {amending_title_id} ({doc_type}): {exc}"
            )
        if text:
            return _truncate_at_es_stop_heading(text), None
        errors.append(f"API {doc_type}: empty text after extraction")

    # --- Pass 2: Web URL fallback (only when API is exhausted with misses) ---
    if api_all_404:
        asmade_date = _get_asmade_date(amending_title_id, session)
        if not asmade_date:
            errors.append("could not resolve AsMade date for web fallback")
        else:
            content, web_err = _download_es_via_web(
                amending_title_id, asmade_date, session,
            )
            if content:
                try:
                    text = extract_plain_text_from_docx(content)
                except Exception as exc:
                    return None, (
                        f"FRL ES extraction failed for {amending_title_id} (web): {exc}"
                    )
                if text:
                    return _truncate_at_es_stop_heading(text), None
                errors.append("web fallback: empty text after extraction")
            elif web_err:
                errors.append(web_err)

    detail = "; ".join(errors) if errors else "no ES located"
    return None, (
        f"No ES or SupplementaryES Word document found for {amending_title_id} ({detail})."
    )


def _extract_bill_id(originating_bill_uri: str) -> str | None:
    """Extract the parlinfo billId from an originatingBillUri.

    Example input (URL-encoded):
        https://parlinfo.aph.gov.au/parlInfo/search/display/display.w3p;
        query=Id%3A%22legislation%2Fbillhome%2Fr7421"
    Returns: "r7421"
    """
    from urllib.parse import unquote
    decoded = unquote(originating_bill_uri)
    m = re.search(r'billhome/([^"&\s]+)', decoded)
    return m.group(1) if m else None


# Anchored heading match: the line's stripped length must be ≤ this for it to
# qualify as a section heading rather than an inline mention.  Mirrors the
# 80-char heuristic in the reference implementation.
_PARLINFO_HEADING_MAX_CHARS = 80

# EM URLs on parlinfo are deeply nested; the reliable signal is the
# "legislation%2Fems%2F" segment plus a UUID assigned at upload time.  Cannot
# be predicted from the bill id, so we scrape it from the bill home page.
_EM_LINK_RE = re.compile(
    r'https?://parlinfo\.aph\.gov\.au/parlInfo/search/display/display\.w3p'
    r'[^\s"\'<>]*legislation%2Fems%2F[^\s"\'<>]+',
    re.IGNORECASE,
)


def _extract_between_anchored_markers(
    plain_text: str,
    start_patterns: list[str],
    end_patterns: list[str],
    *,
    max_heading_chars: int = _PARLINFO_HEADING_MAX_CHARS,
) -> str:
    """Extract content between two section-heading lines in *plain_text*.

    A line qualifies as a heading if its stripped length is ≤
    ``max_heading_chars`` and its full content matches one of the patterns
    (``re.fullmatch``, case-insensitive).  Once a start heading is found,
    every subsequent non-empty line is collected until an end heading is
    encountered (or end-of-text).  Each pattern is a regex.

    The 80-char cap excludes paragraphs that happen to mention "Summary"
    inline; only short standalone heading lines match.
    """
    if not plain_text:
        return ""

    start_re = [re.compile(p, re.IGNORECASE) for p in start_patterns]
    end_re = [re.compile(p, re.IGNORECASE) for p in end_patterns]

    lines = plain_text.splitlines()
    in_section = False
    chunks: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        is_short = bool(line) and len(line) <= max_heading_chars

        if not in_section:
            if is_short and any(rx.fullmatch(line) for rx in start_re):
                in_section = True
            continue

        # Inside the section: stop on an end-marker heading.
        if is_short and any(rx.fullmatch(line) for rx in end_re):
            break
        if line:
            chunks.append(line)

    return " ".join(chunks).strip()


def _fetch_parlinfo_text(url: str, session: Any) -> tuple[str, str]:
    """Fetch a parlinfo page (WAF-aware) and return ``(raw_html, plain_text)``.

    Uses ``fetch_with_waf_polling`` to drive a stealth Chrome that waits out
    the Azure WAF JS challenge.  Returns ``("", "")`` on failure.  Plain text
    is produced via the standard trafilatura pipeline (``extract_plain_text``)
    so the caller can run anchored marker extraction on it.

    The ``session`` parameter is unused but accepted to keep the signature
    consistent with sibling fetch helpers and to allow future plain-HTTP
    bypass paths (e.g. against non-WAF mirror servers in tests).
    """
    from src.scraper import fetch_with_waf_polling, extract_plain_text

    html = fetch_with_waf_polling(url) or ""
    if not html:
        return "", ""
    plain = extract_plain_text(html) if html else ""
    return html, plain


def _discover_em_url(bill_home_html: str) -> str | None:
    """Scan the bill home page HTML for an Explanatory Memorandum link.

    The EM URL contains a UUID assigned at upload time that is not derivable
    from any other field, so the bill home page is the only reliable source.
    Regex against the raw HTML is more resilient to DOM-structure variation
    than parsing.
    """
    if not bill_home_html:
        return None
    m = _EM_LINK_RE.search(bill_home_html)
    if not m:
        return None
    return m.group(0).rstrip("\"'")


def _fetch_em_outline(em_url: str, session: Any) -> str | None:
    """Fetch the Explanatory Memorandum and return its General Outline section.

    Extracts text between ``General Outline`` / ``Outline`` and
    ``Financial Impact`` / ``Financial Impact Statement`` (case-insensitive,
    line-anchored).  The heading varies by drafting convention; older EMs
    use "Outline", newer ones use "General Outline".

    Returns the extracted text on success, or ``None`` if the page cannot
    be retrieved or the markers are not found.
    """
    _, plain = _fetch_parlinfo_text(em_url, session)
    if not plain:
        return None
    text = _extract_between_anchored_markers(
        plain,
        start_patterns=[r"general\s+outline", r"outline"],
        end_patterns=[r"financial\s+impact(?:\s+statement)?"],
    )
    return text or None


def _fetch_frl_title(title_id: str, session: Any) -> dict[str, Any]:
    """Fetch the Title resource for *title_id* from the FRL API.

    API reference:
        GET /v1/Titles('{titleId}')
        Accept: application/json
        Base URL: https://api.prod.legislation.gov.au

    Returns the parsed JSON body.  Raises on HTTP/connection failure; caller
    is responsible for error handling.  The returned object contains (among
    other fields) ``seriesType`` (Act | SR | SLI, nullable) and, for Acts,
    ``originatingBillUri``.
    """
    endpoint = f"{_FRL_API_BASE}/v1/Titles('{title_id}')"
    logger.debug("FRL Titles API: GET %s", endpoint)
    resp = session.get(
        endpoint,
        headers={"Accept": "application/json"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def _fetch_act_bill_summary(
    amending_title_id: str,
    session: Any,
    prefetched_title: dict[str, Any] | None = None,
) -> tuple[str | None, str | None]:
    """Retrieve a plain-English summary for an amending Act via parlinfo.

    Four-tier content waterfall (mirrors ``docs/Reference-Code/fetch_em_summary``).
    Each parlinfo fetch goes through ``fetch_with_waf_polling`` so the Azure
    WAF JS challenge is waited out properly.

    Tier 1 — Bills Digest, "Key Points" → "Contents":
        Written by the Parliamentary Library for a non-specialist audience;
        the most readable source.  Tried first because when present it is
        the highest-quality summary.

    Tier 2 — Bill home Summary, ≥ ``_PARLINFO_MIN_WORDS`` words:
        Drafted summary on the bill home page, between "Summary" and
        "Progress of bill".  Used when the Bills Digest is absent.

    Tier 3 — Explanatory Memorandum, "General Outline" → "Financial Impact":
        EM URL is discovered from the bill home page.  More technical than
        the digest but always available; used when shorter sources fail.

    Tier 4 — Bill home Summary fallback (any length):
        Whatever was extracted in Tier 2, even if < 100 words.  Better than
        nothing.

    The bill home HTML and plain text are fetched once and reused across
    Tiers 2–4 to avoid a second WAF-bypass roundtrip.

    Returns ``(text, error_message)``.  On success ``error_message`` is None.
    """
    if prefetched_title is not None:
        title_data = prefetched_title
    else:
        try:
            title_data = _fetch_frl_title(amending_title_id, session)
        except Exception as exc:
            return None, f"FRL Titles API failed for {amending_title_id}: {exc}"

    bill_uri = title_data.get("originatingBillUri")
    if not bill_uri:
        return None, f"No originatingBillUri for Act {amending_title_id}."

    bill_id = _extract_bill_id(bill_uri)
    logger.debug(
        "FRL Act path [%s]: originatingBillUri=%s billId=%s",
        amending_title_id, bill_uri, bill_id,
    )

    errors: list[str] = []

    # --- Tier 1: Bills Digest — Key Points ---
    if bill_id:
        digest_url = (
            f"https://parlinfo.aph.gov.au/parlInfo/search/display/display.w3p;"
            f"query=BillId_Phrase%3A%22{bill_id}%22%20Dataset%3Abillsdgs;rec=0"
        )
        logger.debug(
            "FRL Act path [%s]: tier 1 Bills Digest URL=%s",
            amending_title_id, digest_url,
        )
        _, digest_plain = _fetch_parlinfo_text(digest_url, session)
        if digest_plain:
            digest_text = _extract_between_anchored_markers(
                digest_plain,
                start_patterns=[r"key\s+points"],
                end_patterns=[r"contents"],
            )
            digest_text = _normalise_diff_text(digest_text) if digest_text else ""
            if digest_text:
                logger.debug(
                    "FRL Act path [%s]: tier 1 success (%d words)",
                    amending_title_id, len(digest_text.split()),
                )
                return digest_text, None
            errors.append("tier 1 (Bills Digest): markers not found")
        else:
            errors.append("tier 1 (Bills Digest): page fetch failed")
    else:
        errors.append("tier 1 (Bills Digest): no bill id derivable from originatingBillUri")

    # --- Tiers 2–4 share the bill home page; fetch once. ---
    bill_home_html, bill_home_plain = _fetch_parlinfo_text(bill_uri, session)
    if not bill_home_plain:
        errors.append("bill home page fetch failed (tiers 2–4 unavailable)")
        return None, (
            f"No bill summary found for Act {amending_title_id} "
            f"(bill ID: {bill_id}): {'; '.join(errors)}"
        )

    summary_raw = _extract_between_anchored_markers(
        bill_home_plain,
        start_patterns=[r"summary"],
        end_patterns=[r"progress\s+of\s+bill"],
    )
    summary = _normalise_diff_text(summary_raw) if summary_raw else ""
    summary_word_count = len(summary.split()) if summary else 0
    logger.debug(
        "FRL Act path [%s]: tier 2 summary words=%d (threshold=%d)",
        amending_title_id, summary_word_count, _PARLINFO_MIN_WORDS,
    )

    # --- Tier 2: Summary ≥ MIN_WORDS ---
    if summary and summary_word_count >= _PARLINFO_MIN_WORDS:
        return summary, None

    # --- Tier 3: Explanatory Memorandum — General Outline ---
    em_url = _discover_em_url(bill_home_html)
    if em_url:
        logger.debug("FRL Act path [%s]: tier 3 EM URL=%s", amending_title_id, em_url)
        em_text = _fetch_em_outline(em_url, session)
        em_text = _normalise_diff_text(em_text) if em_text else ""
        if em_text:
            logger.debug(
                "FRL Act path [%s]: tier 3 success (%d words)",
                amending_title_id, len(em_text.split()),
            )
            return em_text, None
        errors.append("tier 3 (EM): markers not found or fetch failed")
    else:
        errors.append("tier 3 (EM): no EM link on bill home page")

    # --- Tier 4: Short Summary fallback ---
    if summary:
        logger.debug(
            "FRL Act path [%s]: tier 4 short-summary fallback (%d words)",
            amending_title_id, summary_word_count,
        )
        return summary, None

    return None, (
        f"No bill summary found for Act {amending_title_id} "
        f"(bill ID: {bill_id}): {'; '.join(errors)}"
    )


def _fetch_frl_explainer(source: dict[str, Any], session: Any) -> tuple[str | None, str | None]:
    """Retrieve the FRL change explainer for the latest compilation.

    Identifies the amending instrument(s) that caused the new compilation via
    the FRL Versions API reasons array, then retrieves the appropriate document:

    - Regulations (seriesType SR or SLI): fetches the amending instrument's
      Explanatory Statement DOCX via the FRL API; truncates at the first
      standalone heading in _FRL_STOP_HEADINGS.

    - Acts (seriesType Act): retrieves the amending Act's originatingBillUri
      from the FRL Titles API, then scrapes the parlinfo bill summary.  Falls
      back to the Bills Digest if the summary is < _PARLINFO_MIN_WORDS words.

    API-first: all metadata is retrieved via the FRL API.  Scraping is used
    only for parlinfo content (no FRL API equivalent exists).

    Series-type resolution: ``reasons[].affectedByTitle.seriesType`` is
    nullable in the FRL API; when it's absent, route via an authoritative
    ``/v1/Titles('{id}')`` lookup instead of silently defaulting to the
    regulation path (which 404s for Acts).  The fetched Title is reused in
    ``_fetch_act_bill_summary`` so we make at most one Titles call per
    instrument.  If the Titles lookup itself fails, we default to the
    regulation path to preserve historical behaviour for SR/SLI cases.

    Returns ``(text, error_message)``.  On success ``error_message`` is None.
    When multiple amending instruments are found their explainers are
    concatenated with a ``\\n\\n---\\n\\n`` separator.
    """
    url = source.get("url", "")
    title_id = _extract_frl_title_id(url)
    if not title_id:
        return None, f"Could not extract FRL titleId from URL: {url!r}"

    # Step 1: get the latest Version with its reasons to find amending instrument(s).
    try:
        version_data = _fetch_frl_version_with_reasons(title_id, session)
    except Exception as exc:
        return None, f"FRL Versions API failed for {title_id}: {exc}"

    amending_instruments = _extract_amending_instruments(version_data)

    if not amending_instruments:
        # Layer 3: Affect API last-resort discovery.  Only runs when the
        # registerId + reasons-array layers found nothing.
        compilation_start = version_data.get("start") or ""
        compilation_start_date = compilation_start[:10] if compilation_start else None
        amending_instruments = _discover_amending_via_affect_api(
            title_id, session, compilation_start_date=compilation_start_date,
        )
        if amending_instruments:
            logger.debug(
                "FRL [%s]: %d amending instrument(s) discovered via Affect API",
                title_id, len(amending_instruments),
            )

    if not amending_instruments:
        return None, (
            f"No amending instruments found in version reasons for {title_id}. "
            f"This may be the first (as-made) version with no amendment history."
        )

    # Step 2: fetch explainer for each amending instrument and concatenate.
    texts: list[str] = []
    errors: list[str] = []
    for instrument in amending_instruments:
        amending_id = instrument["title_id"]
        series_type = instrument["series_type"]
        prefetched_title: dict[str, Any] | None = None

        # If seriesType is missing from the reasons array, fetch the Title to
        # determine it authoritatively.  Reuse the fetched Title for the Act
        # path to avoid a second Titles API call.
        if not series_type:
            try:
                prefetched_title = _fetch_frl_title(amending_id, session)
                series_type = (prefetched_title.get("seriesType") or "").strip()
                logger.debug(
                    "FRL routing [%s]: seriesType resolved via Titles API → %r",
                    amending_id, series_type or "(still empty)",
                )
            except Exception as exc:
                errors.append(
                    f"Titles API fallback failed for {amending_id}: {exc}; "
                    f"defaulting to regulation ES path."
                )
                logger.debug(
                    "FRL routing [%s]: Titles API fallback failed (%s); "
                    "defaulting to regulation path", amending_id, exc,
                )

        if series_type == "Act":
            logger.debug("FRL routing [%s]: Act path", amending_id)
            text, err = _fetch_act_bill_summary(
                amending_id, session, prefetched_title=prefetched_title
            )
        else:
            # SR, SLI, or unknown → try regulation ES path.
            logger.debug(
                "FRL routing [%s]: regulation ES path (seriesType=%r)",
                amending_id, series_type,
            )
            text, err = _fetch_regulation_explainer(amending_id, session)

        if text:
            texts.append(text)
        if err:
            errors.append(err)

    if texts:
        return "\n\n---\n\n".join(texts), None
    return None, "; ".join(errors) if errors else "No explainer found."


# ---------------------------------------------------------------------------
# RSS diff
# ---------------------------------------------------------------------------


def _generate_rss_diff(
    source: dict[str, Any],
    snap_base: Path,
    versions_retained: int,
    session: Any,
) -> DiffResult:
    """Compare current RSS feed against stored snapshot; extract new/mutated items."""
    source_id = source["source_id"]
    url = source.get("url", "")
    snap_dir = snap_base / source_id
    snap_dir.mkdir(parents=True, exist_ok=True)

    # Stable filename derived from a hash of the URL.
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
    snap_file = snap_dir / f"rss_{url_hash}.json"

    # Load stored snapshot.
    stored_state: dict[str, Any] = {}
    if snap_file.exists():
        try:
            stored_state = json.loads(snap_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load RSS snapshot for %s: %s", source_id, exc)

    # Fetch current feed.
    current_items: dict[str, Any] = {}
    warnings: list[str] = []

    if session is not None:
        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
            current_items = _parse_rss_items(resp.text)
        except Exception as exc:
            warnings.append(f"RSS fetch failed for {source_id}: {exc}")
            logger.warning(warnings[-1])

    # Diff: find new GUIDs and mutated existing GUIDs.
    new_items: list[dict[str, Any]] = []
    mutated_items: list[dict[str, Any]] = []

    for guid, payload in current_items.items():
        if guid not in stored_state:
            new_items.append({"guid": guid, **payload})
        else:
            stored_payload = stored_state[guid]
            # Detect mutations: compare content hash.
            current_content_hash = _rss_item_hash(payload)
            stored_content_hash = _rss_item_hash(stored_payload)
            if current_content_hash != stored_content_hash:
                mutated_items.append({
                    "guid": guid,
                    "previous": stored_payload,
                    "current": payload,
                })

    # Build normalised diff text.
    normalised = _rss_to_normalised_text(new_items, mutated_items)

    # Update snapshot.
    # Merge current items into stored state (keeps full history of GUIDs seen).
    merged_state = {**stored_state, **current_items}
    snap_file.write_text(
        json.dumps(merged_state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return DiffResult(
        source_id=source_id,
        source_type="rss",
        diff_type="rss_items",
        normalised_diff=normalised,
        diff_path=str(snap_file),
        diff_size_chars=len(normalised),
        normalised_size_chars=len(normalised),
        rss_new_items=new_items,
        rss_mutated_items=mutated_items,
        warnings=warnings,
    )


def _parse_rss_items(xml_text: str) -> dict[str, dict[str, Any]]:
    """Parse RSS/Atom items into a dict keyed by GUID (or link as fallback).

    Payload fields: title, description, pubDate, link, content_encoded.
    """
    # Try to use feedparser if available; fall back to regex extraction.
    try:
        import feedparser
        feed = feedparser.parse(xml_text)
        items: dict[str, dict[str, Any]] = {}
        for entry in feed.entries:
            guid = (
                getattr(entry, "id", None)
                or getattr(entry, "link", None)
                or ""
            )
            if not guid:
                continue
            items[guid] = {
                "title": getattr(entry, "title", ""),
                "description": getattr(entry, "summary", ""),
                "pubDate": getattr(entry, "published", ""),
                "link": getattr(entry, "link", ""),
                "content_encoded": _get_rss_content_encoded(entry),
            }
        return items
    except ImportError:
        pass

    # Regex fallback.
    return _parse_rss_items_regex(xml_text)


def _get_rss_content_encoded(entry: Any) -> str:
    """Extract content:encoded from a feedparser entry."""
    try:
        content = entry.get("content", [])
        if content:
            return content[0].get("value", "")
    except Exception:
        pass
    return ""


def _parse_rss_items_regex(xml_text: str) -> dict[str, dict[str, Any]]:
    """Minimal regex-based RSS parser (fallback when feedparser unavailable)."""
    items: dict[str, dict[str, Any]] = {}

    item_blocks = re.findall(r"<item[^>]*>(.*?)</item>", xml_text, re.DOTALL | re.IGNORECASE)
    for block in item_blocks:
        guid = _extract_xml_tag(block, "guid") or _extract_xml_tag(block, "link") or ""
        if not guid:
            continue
        items[guid] = {
            "title": _extract_xml_tag(block, "title") or "",
            "description": _extract_xml_tag(block, "description") or "",
            "pubDate": _extract_xml_tag(block, "pubDate") or "",
            "link": _extract_xml_tag(block, "link") or "",
            "content_encoded": _extract_xml_tag(block, "content:encoded") or "",
        }

    return items


def _extract_xml_tag(text: str, tag: str) -> str | None:
    """Extract the text content of the first occurrence of an XML tag."""
    tag_escaped = re.escape(tag)
    m = re.search(
        rf"<{tag_escaped}[^>]*>(.*?)</{tag_escaped}>",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if m:
        # Strip CDATA wrappers.
        content = m.group(1).strip()
        content = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", content, flags=re.DOTALL)
        return content.strip()
    return None


def _rss_item_hash(payload: dict[str, Any]) -> str:
    """Compute a content hash for an RSS item payload."""
    combined = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(combined.encode()).hexdigest()


def _rss_to_normalised_text(
    new_items: list[dict[str, Any]],
    mutated_items: list[dict[str, Any]],
) -> str:
    """Build the canonical normalised text for RSS changes.

    Per Section 3.3: concatenate title + description + content:encoded
    with a fixed delimiter.
    """
    from src.scraper import normalise_text
    parts: list[str] = []
    delimiter = "\n---\n"

    for item in new_items:
        segments = [
            item.get("title", ""),
            item.get("description", ""),
            item.get("content_encoded", ""),
        ]
        block = delimiter.join(s for s in segments if s)
        if block:
            parts.append(f"[NEW ITEM]\n{block}")

    for item in mutated_items:
        current = item.get("current", {})
        segments = [
            current.get("title", ""),
            current.get("description", ""),
            current.get("content_encoded", ""),
        ]
        block = delimiter.join(s for s in segments if s)
        if block:
            parts.append(f"[MUTATED ITEM]\n{block}")

    combined = "\n\n".join(parts)
    return normalise_text(combined) if combined else ""


# ---------------------------------------------------------------------------
# Snapshot rotation
# ---------------------------------------------------------------------------


def _rotate_snapshots(snap_dir: Path, source_id: str, versions_retained: int) -> None:
    """Rotate versioned snapshot files, keeping at most ``versions_retained`` old copies.

    Current snapshot: ``<source_id>.txt``
    Versioned copies: ``<source_id>.v1.txt``, ``<source_id>.v2.txt``, …

    On each call:
    1. Shift v(N-1) → vN, …, v1 → v2.
    2. Rename current → v1.
    3. Delete any versions beyond ``versions_retained``.
    """
    current = snap_dir / f"{source_id}.txt"
    if not current.exists():
        return  # Nothing to rotate on first run.

    # Shift existing versioned files upward.
    for v in range(versions_retained, 0, -1):
        src = snap_dir / f"{source_id}.v{v}.txt"
        dst = snap_dir / f"{source_id}.v{v + 1}.txt"
        if src.exists():
            if v + 1 > versions_retained:
                src.unlink()  # Prune beyond retention limit.
            else:
                src.rename(dst)

    # Promote current → v1.
    current.rename(snap_dir / f"{source_id}.v1.txt")


def load_previous_snapshot(
    source_id: str,
    snapshot_dir: Path | str | None = None,
) -> str | None:
    """Load the current snapshot for a source, or None if none exists.

    Parameters
    ----------
    source_id:
        Source identifier.
    snapshot_dir:
        Base directory for snapshots.

    Returns
    -------
    str or None
        Contents of the snapshot file, or None if not found.
    """
    snap_base = Path(snapshot_dir) if snapshot_dir else _DEFAULT_SNAPSHOT_DIR
    snap_file = snap_base / source_id / f"{source_id}.txt"
    if snap_file.exists():
        return snap_file.read_text(encoding="utf-8")
    return None


def load_previous_hash(
    source_id: str,
    snapshot_dir: Path | str | None = None,
) -> str | None:
    """Return the SHA-256 hash of the current snapshot, or None if none exists."""
    text = load_previous_snapshot(source_id, snapshot_dir)
    if text is None:
        return None
    from src.scraper import compute_sha256
    return compute_sha256(text)


# ---------------------------------------------------------------------------
# Diff normalisation
# ---------------------------------------------------------------------------


def _normalise_diff_text(text: str) -> str:
    """Normalise raw diff or explainer text for downstream stages.

    Per Section 3.3: decode HTML entities, collapse whitespace, strip
    formatting artefacts, NFC Unicode.  Does not lowercase or strip punctuation.
    """
    from src.scraper import normalise_text
    import html as _html
    text = _html.unescape(text)
    return normalise_text(text)


# ---------------------------------------------------------------------------
# Git persistence (end-of-run commit)
# ---------------------------------------------------------------------------


def commit_snapshots(
    snapshot_dir: Path | str | None = None,
    run_id: str = "",
    author: str = "github-actions[bot] <github-actions[bot]@users.noreply.github.com>",
) -> bool:
    """Commit updated snapshot files to Git.

    This implements the end-of-run Git persistence described in Section 7.2.
    Called by the pipeline orchestrator after all sources have been processed.

    Parameters
    ----------
    snapshot_dir:
        Path to ``data/influencer_sources/snapshots/``.
    run_id:
        Current pipeline run identifier (included in commit message).
    author:
        Git author string.

    Returns
    -------
    bool
        True if a commit was made, False if there was nothing to commit
        or Git is unavailable.
    """
    snap_base = Path(snapshot_dir) if snapshot_dir else _DEFAULT_SNAPSHOT_DIR

    try:
        # Stage the snapshot directory.
        subprocess.run(
            ["git", "add", str(snap_base)],
            check=True,
            capture_output=True,
        )

        # Check if there is anything to commit.
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            capture_output=True,
        )
        if result.returncode == 0:
            logger.info("Git: no snapshot changes to commit.")
            return False

        msg = f"chore: update influencer snapshots [run {run_id}]" if run_id else \
              "chore: update influencer snapshots"

        subprocess.run(
            [
                "git", "-c", f"user.name=github-actions[bot]",
                "-c", f"user.email=github-actions[bot]@users.noreply.github.com",
                "commit", "--author", author, "-m", msg,
            ],
            check=True,
            capture_output=True,
        )
        logger.info("Git: committed snapshot updates (%s).", run_id)

        # Push.
        subprocess.run(
            ["git", "push"],
            check=True,
            capture_output=True,
        )
        logger.info("Git: pushed snapshot updates.")
        return True

    except FileNotFoundError:
        logger.debug("Git not available in this environment — skipping commit.")
        return False
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "Git commit/push failed: %s\nstdout: %s\nstderr: %s",
            exc,
            exc.stdout.decode(errors="replace") if exc.stdout else "",
            exc.stderr.decode(errors="replace") if exc.stderr else "",
        )
        return False
