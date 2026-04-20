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

    # Rotate snapshots: rename current → versioned, write new current.
    _rotate_snapshots(snap_dir, source_id, versions_retained)
    current_snap = snap_dir / f"{source_id}.txt"
    current_snap.write_text(new_text, encoding="utf-8")

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
    logger.warning(warnings[-1])
    result = _generate_webpage_diff(
        source, new_text, previous_text, diff_lines,
        snap_base, versions_retained, run_id
    )
    result.source_type = "frl"
    result.diff_type = "unified_diff_fallback"
    result.warnings = warnings
    return result


def _fetch_frl_explainer(source: dict[str, Any], session: Any) -> tuple[str | None, str | None]:
    """Retrieve the FRL Explanatory Statement (ES) document as plain text.

    Queries the official FRL REST API documents endpoint for the latest
    compiled version of the title.  The ES document type (``ES``) is tried
    first; ``SupplementaryES`` is tried as a fallback for instruments that
    only have a supplementary explanatory statement.

    Downloads the Word (.docx) binary and extracts plain text via mammoth
    (through ``src.scraper.extract_plain_text_from_docx``).

    Returns ``(text, error_message)``.  On success ``error_message`` is None.

    API reference:
        Step 1 — confirm the document exists (metadata only):
            GET /v1/documents/find(titleid='{titleId}',asatspecification='Latest',
                type='ES',format='Word',uniqueTypeNumber=0,volumeNumber=0,
                rectificationVersionNumber=0)
            Accept: application/json  →  Document metadata; 404 means no ES.

        Step 2 — download binary DOCX (omit Accept header):
            Same URL without Accept: application/json  →  binary DOCX content.

        Base URL: https://api.prod.legislation.gov.au
        Auth: none required for public read.

    The titleId is extracted from the source URL using _extract_frl_title_id,
    which handles both the current form (/<titleId>/latest/text) and the legacy
    /Series/<titleId> form.
    """
    url = source.get("url", "")
    title_id = _extract_frl_title_id(url)
    if not title_id:
        return None, f"Could not extract FRL titleId from URL: {url!r}"

    def _doc_endpoint(doc_type: str) -> str:
        return (
            f"{_FRL_API_BASE}/v1/documents/find("
            f"titleid='{title_id}',"
            f"asatspecification='Latest',"
            f"type='{doc_type}',"
            f"format='Word',"
            f"uniqueTypeNumber=0,"
            f"volumeNumber=0,"
            f"rectificationVersionNumber=0)"
        )

    # Step 1: find the first available ES document type.
    chosen_type: str | None = None
    for doc_type in _FRL_ES_TYPES:
        try:
            meta_resp = session.get(
                _doc_endpoint(doc_type),
                headers={"Accept": "application/json"},
                timeout=20,
            )
            if meta_resp.status_code == 404:
                continue  # This type does not exist; try the next.
            meta_resp.raise_for_status()
            chosen_type = doc_type
            break
        except Exception as exc:
            return None, (
                f"FRL API metadata check failed for {title_id} ({doc_type}): {exc}"
            )

    if chosen_type is None:
        return None, (
            f"No ES or SupplementaryES Word document found for title {title_id}."
        )

    # Step 2: download the binary DOCX.
    try:
        bin_resp = session.get(_doc_endpoint(chosen_type), timeout=60)
        bin_resp.raise_for_status()
    except Exception as exc:
        return None, f"FRL ES binary download failed for {title_id}: {exc}"

    # Step 3: extract plain text from the DOCX via mammoth → trafilatura.
    try:
        from src.scraper import extract_plain_text_from_docx
        text = extract_plain_text_from_docx(bin_resp.content)
        if text:
            return text, None
        return None, f"FRL ES for {title_id} yielded empty text after extraction."
    except Exception as exc:
        return None, f"FRL ES text extraction failed for {title_id}: {exc}"


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
