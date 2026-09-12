"""
src/stage2_change_detection.py

Stage 2 of the Tripwire pipeline: Change Detection (Section 3.2).

Purpose: determine whether a change detected in Stage 1 is *meaningful* or
merely cosmetic (timestamp update, CSS rename, whitespace-only change).

This stage applies only to **webpage** sources.  FRL and RSS sources bypass
it because their change information is already structured.

Three-pass system
-----------------
Pass 1 — SHA-256 Content Hash
    Compare the SHA-256 hash of the new normalised text against the stored
    hash.  If they match the content has not changed — stop immediately.

Pass 2 — Word-Level Diff
    Generate a unified diff (difflib.unified_diff) of the previous snapshot
    versus the new snapshot.  If the diff is empty after normalisation the
    change is cosmetic — log and stop.

Pass 3 — Significance Fingerprint Tagger
    Extract from the *changed lines only*:
      • Defined terms (capitalised multi-word terms)
      • Numerical values (dollar amounts, percentages, section numbers)
      • Dates (commencement, deadline, amendment)
      • Cross-references (references to Acts, sections, regulations)
      • Modal verbs in legal context (may, must, shall, should)
    Tag the change as ``significance: high`` or ``significance: standard``.
    Both tags proceed to Stage 3.

Decision rule: stop only if hash matches (Pass 1) or diff is empty after
normalisation (Pass 2).  The significance tag is advisory metadata.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ChangeDetectionResult:
    """Result of Stage 2 change detection for a single source."""

    source_id: str
    decision: str  # "no_change" | "cosmetic" | "significant" | "skipped"
    hash_matched: bool = False
    diff_lines: list[str] = field(default_factory=list)
    diff_size: int = 0          # number of changed lines
    significance: str = "standard"  # "high" | "standard"
    fingerprint: dict[str, list[str]] = field(default_factory=dict)
    skipped_reason: str = ""

    @property
    def should_proceed(self) -> bool:
        """Return True if Stage 3 should process this source."""
        return self.decision in ("significant", "standard", "skipped")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "decision": self.decision,
            "hash_matched": self.hash_matched,
            "diff_size": self.diff_size,
            "significance": self.significance,
            "fingerprint": self.fingerprint,
            "skipped_reason": self.skipped_reason,
        }


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def detect_change(
    source_id: str,
    source_type: str,
    new_text: str,
    previous_text: str | None,
    previous_hash: str | None,
    *,
    fingerprint_enabled: bool = True,
) -> ChangeDetectionResult:
    """Run Stage 2 change detection for a single source.

    Parameters
    ----------
    source_id:
        Identifier from the source registry.
    source_type:
        ``"webpage"`` | ``"frl"`` | ``"rss"``.  FRL and RSS bypass this stage.
    new_text:
        Normalised plain text of the current scrape.
    previous_text:
        Normalised plain text of the previous snapshot, or None on first run.
    previous_hash:
        SHA-256 hash of the previous snapshot, or None on first run.
    fingerprint_enabled:
        Whether to run Pass 3 (controlled by
        ``change_detection.significance_fingerprint`` in config).

    Returns
    -------
    ChangeDetectionResult
    """
    # FRL and RSS sources bypass change detection entirely.
    if source_type in ("frl", "rss"):
        return ChangeDetectionResult(
            source_id=source_id,
            decision="skipped",
            skipped_reason=f"{source_type.upper()} source — change detection not applied",
        )

    # First run — no previous snapshot to compare against.
    if previous_text is None or previous_hash is None:
        logger.info("Stage 2 [%s]: first run, no previous snapshot.", source_id)
        # Treat as a new significant item so Stage 3 stores the initial snapshot.
        return ChangeDetectionResult(
            source_id=source_id,
            decision="significant",
            hash_matched=False,
            diff_size=0,
            significance="standard",
            skipped_reason="first_run",
        )

    # ------------------------------------------------------------------
    # Pass 1: SHA-256 hash comparison.
    # ------------------------------------------------------------------
    from src.scraper import compute_sha256
    new_hash = compute_sha256(new_text)

    if new_hash == previous_hash:
        logger.info("Stage 2 [%s]: hash match — no change.", source_id)
        return ChangeDetectionResult(
            source_id=source_id,
            decision="no_change",
            hash_matched=True,
        )

    # ------------------------------------------------------------------
    # Pass 2: Word-level (line-level) unified diff.
    # ------------------------------------------------------------------
    diff_lines = _compute_diff(previous_text, new_text)

    # Extract only the changed lines (lines starting with + or -,
    # excluding the diff header lines starting with +++ or ---).
    changed_lines = [
        ln for ln in diff_lines
        if (ln.startswith("+") or ln.startswith("-"))
        and not ln.startswith("+++")
        and not ln.startswith("---")
    ]

    if not changed_lines:
        logger.info("Stage 2 [%s]: diff empty after normalisation — cosmetic.", source_id)
        return ChangeDetectionResult(
            source_id=source_id,
            decision="cosmetic",
            hash_matched=False,
            diff_lines=diff_lines,
            diff_size=0,
        )

    # ------------------------------------------------------------------
    # Pass 3: Significance fingerprint (optional).
    # ------------------------------------------------------------------
    significance = "standard"
    fingerprint: dict[str, list[str]] = {}

    if fingerprint_enabled:
        changed_text = "\n".join(
            ln[1:] for ln in changed_lines  # strip leading +/-
        )
        fingerprint = _extract_fingerprint(changed_text)
        if any(fingerprint.values()):
            significance = "high"

    logger.info(
        "Stage 2 [%s]: change detected — diff_size=%d, significance=%s.",
        source_id,
        len(changed_lines),
        significance,
    )

    # Both "high" and "standard" proceed to Stage 3.
    return ChangeDetectionResult(
        source_id=source_id,
        decision="significant",
        hash_matched=False,
        diff_lines=diff_lines,
        diff_size=len(changed_lines),
        significance=significance,
        fingerprint=fingerprint,
    )


# ---------------------------------------------------------------------------
# Diff helpers
# ---------------------------------------------------------------------------


def _compute_diff(old_text: str, new_text: str) -> list[str]:
    """Generate a unified diff between old and new normalised text.

    Operates line-by-line (trafilatura produces line-separated paragraphs).
    """
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile="previous",
        tofile="current",
        lineterm="",
    ))
    return diff


def compute_diff(old_text: str, new_text: str) -> list[str]:
    """Public wrapper around _compute_diff for use by stage3_diff."""
    return _compute_diff(old_text, new_text)


# ---------------------------------------------------------------------------
# Significance fingerprint
# ---------------------------------------------------------------------------


# Patterns for each fingerprint category.
_PATTERN_DEFINED_TERMS = re.compile(
    r"\b([A-Z][a-zA-Z]+(?: [A-Z][a-zA-Z]+){1,5})\b"
)
_PATTERN_NUMERICAL = re.compile(
    r"""
    (?:
        \$[\d,]+(?:\.\d+)?          # dollar amounts
        | \d+(?:\.\d+)?%            # percentages
        | \b\d{1,3}(?:,\d{3})+\b   # large numbers with commas
        | \bsection\s+\d+[\w.]*     # section references
        | \bs\.\s*\d+[\w.]*         # s.44 style
        | \b\d+\s*(?:days?|months?|years?|weeks?)\b  # time periods
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)
_PATTERN_DATES = re.compile(
    r"""
    (?:
        \b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|
                       September|October|November|December)\s+\d{4}\b
        | \b(?:January|February|March|April|May|June|July|August|
               September|October|November|December)\s+\d{4}\b
        | \b\d{4}-\d{2}-\d{2}\b     # ISO date
        | \bcommences?\b | \bcommencement\b | \bin force\b
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)
_PATTERN_CROSS_REFS = re.compile(
    r"""
    (?:
        \b(?:the\s+)?[A-Z][A-Za-z\s]+(?:Act|Regulation|Regulations|
            Rules|Code|Ordinance|Determination)\s+\d{4}\b
        | \bAct\s+No\b
        | \bsee\s+(?:also\s+)?section\b
        | \bpursuant\s+to\b
        | \bas\s+amended\s+by\b
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)
_PATTERN_MODAL_VERBS = re.compile(
    r"\b(must|shall|may\s+not|must\s+not|should|ought\s+to)\b",
    re.IGNORECASE,
)


def _extract_fingerprint(changed_text: str) -> dict[str, list[str]]:
    """Extract significance signals from the changed lines.

    Parameters
    ----------
    changed_text:
        The text of the changed lines (+ and - prefixes stripped).

    Returns
    -------
    dict mapping category → list of matched strings.
    Empty lists indicate no match for that category.
    """
    # Try spaCy first (richer NER for defined terms).
    entities: list[str] = _extract_entities_spacy(changed_text)

    defined_terms: list[str] = entities if entities else _extract_defined_terms_regex(changed_text)
    numerical = _unique_matches(_PATTERN_NUMERICAL, changed_text)
    dates = _unique_matches(_PATTERN_DATES, changed_text)
    cross_refs = _unique_matches(_PATTERN_CROSS_REFS, changed_text)
    modal_verbs = _unique_matches(_PATTERN_MODAL_VERBS, changed_text)

    return {
        "defined_terms": defined_terms,
        "numerical": numerical,
        "dates": dates,
        "cross_references": cross_refs,
        "modal_verbs": modal_verbs,
    }


def _extract_entities_spacy(text: str) -> list[str]:
    """Use spaCy NER to extract named entities from text.

    Returns an empty list if spaCy is unavailable (graceful degradation per
    Section 6.4 — skip significance fingerprint tagging, tag as 'standard').
    """
    try:
        import spacy
        _nlp = _get_spacy_model()
        if _nlp is None:
            return []
        doc = _nlp(text[:10_000])  # cap to avoid slow processing on large diffs
        return [ent.text for ent in doc.ents if ent.label_ in (
            "ORG", "LAW", "GPE", "DATE", "MONEY", "PERCENT", "CARDINAL", "ORDINAL"
        )]
    except Exception:
        return []


def _get_spacy_model():
    """Lazy-load the spaCy model; return None if unavailable."""
    try:
        import spacy
        return spacy.load("en_core_web_sm")
    except Exception:
        logger.debug("spaCy model unavailable — using regex-only fingerprint.")
        return None


def _extract_defined_terms_regex(text: str) -> list[str]:
    """Regex fallback: extract capitalised multi-word terms."""
    return list(dict.fromkeys(m.group(1) for m in _PATTERN_DEFINED_TERMS.finditer(text)))


def _unique_matches(pattern: re.Pattern, text: str) -> list[str]:
    """Return deduplicated list of all pattern matches in text."""
    return list(dict.fromkeys(m.group(0) for m in pattern.finditer(text)))
