"""
src/stage3_diff.py

Stage 3 of the Tripwire pipeline: Diff Generation (Section 3.3).

Purpose: produce a precise representation of what changed, formatted
appropriately for the source type.

Source-type routing
-------------------
Webpage    → unified .diff file (old snapshot vs new snapshot).
FRL        → retrieve the change explainer document from the FRL API;
             fall back to webpage diff if unavailable.
RSS        → extract new items (and detect mutated items) since last check.

Snapshot management
-------------------
For webpage and FRL sources, the current normalised text is written to a
snapshot file under ``data/influencer_sources/snapshots/<source_id>/``.
Up to ``content_versions_retained`` (default: 6) previous versions are kept
on disk; older versions are deleted.

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
        return _generate_frl_diff(
            source, new_text, previous_text, diff_lines,
            snap_base, versions_retained, run_id, session
        )
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
    new_text: str,
    previous_text: str | None,
    diff_lines: list[str],
    snap_base: Path,
    versions_retained: int,
    run_id: str,
    session: Any,
) -> DiffResult:
    """Retrieve FRL change explainer; fall back to webpage diff if unavailable."""
    source_id = source["source_id"]
    warnings: list[str] = []

    explainer_text = None
    if session is not None:
        explainer_text, err = _fetch_frl_explainer(source, session)
        if err:
            warnings.append(err)

    if explainer_text:
        normalised = _normalise_diff_text(explainer_text)
        snap_dir = snap_base / source_id
        snap_dir.mkdir(parents=True, exist_ok=True)
        _rotate_snapshots(snap_dir, source_id, versions_retained)
        (snap_dir / f"{source_id}.txt").write_text(new_text, encoding="utf-8")

        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        explainer_path = snap_dir / f"{source_id}_explainer_{ts}.txt"
        explainer_path.write_text(explainer_text, encoding="utf-8")
        logger.info(
            "Stage 3 [%s]: FRL explainer saved → %s (%d chars)",
            source_id, explainer_path, len(explainer_text),
        )

        return DiffResult(
            source_id=source_id,
            source_type="frl",
            diff_type="explainer",
            normalised_diff=normalised,
            diff_path=str(explainer_path),
            diff_size_chars=len(explainer_text),
            normalised_size_chars=len(normalised),
            warnings=warnings,
        )

    # Fallback: treat as webpage diff.
    warnings.append(
        f"FRL explainer unavailable for {source_id}; falling back to text diff."
    )
    logger.warning(
        "Stage 3 [%s]: FALLBACK — FRL explainer unavailable, using unified text diff",
        source_id,
    )
    result = _generate_webpage_diff(
        source, new_text, previous_text, diff_lines,
        snap_base, versions_retained, run_id
    )
    result.source_type = "frl"
    result.diff_type = "unified_diff_fallback"
    result.warnings = warnings
    return result


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
    """Fetch the latest compiled Version for *title_id*, expanding the Reasons array.

    API reference:
        GET /v1/Versions/Find(titleId='{titleId}',asAtSpecification='Latest')?$expand=Reasons
        Accept: application/json
        Base URL: https://api.prod.legislation.gov.au
        Auth: none required for public read.

    Raises on HTTP error or connection failure; caller is responsible for handling.
    """
    endpoint = (
        f"{_FRL_API_BASE}/v1/Versions/Find("
        f"titleId='{title_id}',asAtSpecification='Latest')?$expand=Reasons"
    )
    logger.debug("FRL Versions API: GET %s", endpoint)
    resp = session.get(endpoint, headers={"Accept": "application/json"}, timeout=20)
    logger.debug("FRL Versions API: status=%s", resp.status_code)
    resp.raise_for_status()
    return resp.json()


def _extract_amending_instruments(version_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract amending instrument info from a Version's reasons array.

    Returns a list of ``{"title_id": str, "series_type": str}`` dicts — one per
    ``affect="Amend"`` reason.  ``series_type`` is "Act", "SR", "SLI", or "" if
    the API did not return it.
    """
    instruments: list[dict[str, Any]] = []
    for reason in version_data.get("reasons", []):
        if reason.get("affect") != "Amend":
            continue
        # affectedByTitle = the title that affected (amended) the monitored source.
        # amendedByTitle is a secondary fallback field used in some API responses.
        for field in ("affectedByTitle", "amendedByTitle"):
            ref = reason.get(field) or {}
            amending_id = ref.get("titleId")
            if amending_id:
                instruments.append({
                    "title_id": amending_id,
                    "series_type": ref.get("seriesType", ""),
                })
                break
    return instruments


def _fetch_regulation_explainer(
    amending_title_id: str,
    session: Any,
) -> tuple[str | None, str | None]:
    """Fetch and truncate the ES DOCX for a regulation amending instrument.

    Tries ES then SupplementaryES.  Downloads the Word document via the FRL API,
    extracts plain text (mammoth → trafilatura), then truncates at the first
    standalone heading in _FRL_STOP_HEADINGS.

    Returns ``(text, error_message)``.  On success ``error_message`` is None.

    UI ↔ API mapping (auditable alignment):
        UI URL:
            https://www.legislation.gov.au/{amendingTitleId}/latest/text/explanatory-statement
        API equivalent:
            GET {_FRL_API_BASE}/v1/documents/find(
                titleid='{amendingTitleId}',
                asatspecification='Latest',    # UI "/latest/"
                type='ES',                     # UI ".../explanatory-statement"
                format='Word',                 # downloadable DOCX (mammoth-friendly)
                uniqueTypeNumber=0, volumeNumber=0, rectificationVersionNumber=0)

        Verified against docs/FRL-API/FRL_Instructions.json:
            type   ∈ {Primary, ES, SupportingMaterial, IncorporatedByReference, SupplementaryES}
            format ∈ {Word, Pdf, Epub, NameOnly}

    API call sequence:
        Step 1 — confirm document exists (metadata check):
            GET ...find(...)  with  Accept: application/json
            → Document metadata; HTTP 404 means that type is unavailable.

        Step 2 — download binary DOCX:
            GET ...find(...)  with Accept header omitted
            → binary DOCX content (application/octet-stream).
    """
    for doc_type in _FRL_ES_TYPES:
        endpoint = (
            f"{_FRL_API_BASE}/v1/documents/find("
            f"titleid='{amending_title_id}',"
            f"asatspecification='Latest',"
            f"type='{doc_type}',"
            f"format='Word',"
            f"uniqueTypeNumber=0,"
            f"volumeNumber=0,"
            f"rectificationVersionNumber=0)"
        )
        logger.debug(
            "FRL ES [%s]: attempting type=%s format=Word → %s",
            amending_title_id, doc_type, endpoint,
        )
        try:
            meta_resp = session.get(
                endpoint,
                headers={"Accept": "application/json"},
                timeout=20,
            )
            logger.debug(
                "FRL ES [%s]: metadata status=%s",
                amending_title_id, meta_resp.status_code,
            )
            if meta_resp.status_code == 404:
                continue  # This document type is unavailable; try next.
            meta_resp.raise_for_status()
        except Exception as exc:
            return None, (
                f"FRL ES metadata check failed for {amending_title_id} ({doc_type}): {exc}"
            )

        try:
            bin_resp = session.get(endpoint, timeout=60)
            bin_resp.raise_for_status()
        except Exception as exc:
            return None, f"FRL ES download failed for {amending_title_id}: {exc}"

        logger.debug(
            "FRL ES [%s]: downloaded type=%s bytes=%d",
            amending_title_id, doc_type, len(bin_resp.content or b""),
        )

        try:
            from src.scraper import extract_plain_text_from_docx
            text = extract_plain_text_from_docx(bin_resp.content)
            if text:
                return _truncate_at_es_stop_heading(text), None
            return None, f"FRL ES for {amending_title_id} yielded empty text after extraction."
        except Exception as exc:
            return None, f"FRL ES extraction failed for {amending_title_id}: {exc}"

    return None, f"No ES or SupplementaryES Word document found for {amending_title_id}."


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


def _scrape_text_between(text: str, start_marker: str, end_marker: str) -> str:
    """Extract the substring of *text* between *start_marker* and *end_marker*.

    Both markers are matched case-insensitively.  The markers themselves are
    excluded.  If *end_marker* is not found, everything after *start_marker* is
    returned.  Returns "" if *start_marker* is not found.
    """
    lower = text.lower()
    start_idx = lower.find(start_marker.lower())
    if start_idx == -1:
        return ""
    start_idx += len(start_marker)
    end_idx = lower.find(end_marker.lower(), start_idx)
    if end_idx != -1:
        return text[start_idx:end_idx].strip()
    return text[start_idx:].strip()


def _scrape_parlinfo_page(url: str, session: Any) -> str:
    """Fetch a parlinfo page and return normalised plain text.

    Attempts a requests-based GET first.  If the extracted text is too short
    (< 200 chars, indicating a JS-gated page), falls back to Selenium.
    """
    from src.scraper import extract_plain_text, _fetch_with_selenium
    try:
        resp = session.get(
            url,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if resp.status_code == 200:
            text = extract_plain_text(resp.text)
            if len(text) >= 200:
                return text
    except Exception:
        pass
    html = _fetch_with_selenium(url)
    if html:
        return extract_plain_text(html)
    return ""


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

    Steps:
    1. GET /v1/Titles('{amendingTitleId}') → originatingBillUri (FRL API).
       If *prefetched_title* is supplied (e.g. when the caller already fetched
       it for series-type routing), reuse it instead of re-calling the API.
    2. Scrape the parlinfo bill page: extract text between "Summary" and
       "Progress of bill".
    3. If the summary is < _PARLINFO_MIN_WORDS words, fall back to the Bills
       Digest: extract text between "Key points" and "Contents".

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

    # Scrape parlinfo bill home page; extract the Summary section.
    page_text = _scrape_parlinfo_page(bill_uri, session)
    summary_raw = _scrape_text_between(page_text, "Summary", "Progress of bill")
    summary = _normalise_diff_text(summary_raw) if summary_raw else ""
    summary_word_count = len(summary.split()) if summary else 0
    logger.debug(
        "FRL Act path [%s]: parlinfo summary words=%d (threshold=%d)",
        amending_title_id, summary_word_count, _PARLINFO_MIN_WORDS,
    )

    if summary and summary_word_count >= _PARLINFO_MIN_WORDS:
        return summary, None

    # Bills Digest fallback when summary is absent or too short.
    if bill_id:
        digest_url = (
            f"https://parlinfo.aph.gov.au/parlInfo/search/display/display.w3p;"
            f"query=BillId_Phrase%3A%22{bill_id}%22%20Dataset%3Abillsdgs;rec=0"
        )
        logger.debug(
            "FRL Act path [%s]: Bills Digest fallback URL=%s",
            amending_title_id, digest_url,
        )
        digest_raw = _scrape_parlinfo_page(digest_url, session)
        digest_text = _scrape_text_between(digest_raw, "Key points", "Contents")
        digest_text = _normalise_diff_text(digest_text) if digest_text else ""
        if digest_text:
            return digest_text, None

    if summary:
        return summary, None
    return None, f"No bill summary found for Act {amending_title_id} (bill ID: {bill_id})."


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
