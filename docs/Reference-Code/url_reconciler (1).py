"""
url_reconciler.py — MinHash-based URL change detection for check_sitemap.py

Distinguishes between:
  - Genuinely new pages (new UDID needed)
  - URL renames / redirects (existing row should be updated in-place)
  - URL renames with light content revision (still a rename, Jaccard >= threshold)

Design constraints:
  - Fully deterministic (no LLM calls, no embeddings)
  - Uses only scraped .md files already on disk — no extra network calls for existing pages
  - Falls back gracefully if .md file not found (new page that hasn't been scraped yet)
  - Fits into check_sitemap.py's existing CSV-driven reconciliation loop

Dependencies (add to requirements.txt):
  datasketch>=1.6.4
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from datasketch import MinHash

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuneable constants
# ---------------------------------------------------------------------------

#: Jaccard similarity at or above which we treat a URL as a rename of an
#: existing page rather than a genuinely new page.  0.5 gives a comfortable
#: margin: lightly-revised gov pages typically score 0.6–0.9; unrelated pages
#: typically score < 0.15.  Raise towards 0.7 if you want stricter matching.
JACCARD_THRESHOLD: float = 0.5

#: k-shingle size (characters).  4–5 works well for English prose; smaller
#: values are more sensitive to small edits, larger values are more selective.
SHINGLE_SIZE: int = 4

#: Number of hash permutations for MinHash.  128 gives ~3% estimation error,
#: which is more than adequate for a 0.5 threshold.
NUM_PERM: int = 128


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _shingle(text: str, k: int = SHINGLE_SIZE) -> set[str]:
    """Return the set of character k-shingles from *text*."""
    text = re.sub(r"\s+", " ", text.lower().strip())
    return {text[i : i + k] for i in range(max(1, len(text) - k + 1))}


def _minhash(text: str) -> MinHash:
    """Build a MinHash signature from *text*."""
    m = MinHash(num_perm=NUM_PERM)
    for shingle in _shingle(text):
        m.update(shingle.encode("utf-8"))
    return m


def jaccard_from_text(text_a: str, text_b: str) -> float:
    """Return the estimated Jaccard similarity between two text strings."""
    if not text_a or not text_b:
        return 0.0
    return _minhash(text_a).jaccard(_minhash(text_b))


# ---------------------------------------------------------------------------
# Signature store — built once per check_sitemap.py run
# ---------------------------------------------------------------------------

class ExistingPageSignatures:
    """
    Lazily builds MinHash signatures for every row already in the CSV,
    loading from the corresponding .md file in IPFR-Webpages/ where available.

    Usage
    -----
    sigs = ExistingPageSignatures(md_dir=Path("IPFR-Webpages"))
    sigs.load_from_csv_rows(rows)          # rows = list[dict] from csv.DictReader

    result = sigs.find_best_match(new_page_text)
    if result and result.jaccard >= JACCARD_THRESHOLD:
        # URL rename — update result.udid row in CSV
    else:
        # Genuinely new page — append new A-prefix row
    """

    def __init__(self, md_dir: Path) -> None:
        self.md_dir = md_dir
        # udid -> MinHash
        self._sigs: dict[str, MinHash] = {}
        # udid -> canonical_url (for logging)
        self._urls: dict[str, str] = {}

    def load_from_csv_rows(self, rows: list[dict]) -> None:
        """
        Build signatures for all existing CSV rows.

        Looks for a .md file whose name starts with ``{UDID}_`` in *md_dir*.
        Rows whose .md file cannot be found are silently skipped (they will
        not participate in rename detection for this run — safe fallback).
        """
        loaded = 0
        skipped = 0
        for row in rows:
            udid = row.get("UDID", "").strip()
            url = row.get("Canonical-url", "").strip()
            if not udid or not url:
                continue

            md_text = self._load_md(udid)
            if md_text is None:
                skipped += 1
                continue

            self._sigs[udid] = _minhash(md_text)
            self._urls[udid] = url
            loaded += 1

        logger.info(
            "ExistingPageSignatures: loaded %d signatures, skipped %d (no .md file)",
            loaded,
            skipped,
        )

    def _load_md(self, udid: str) -> Optional[str]:
        """Return the text of the first .md file whose name starts with ``{udid}_``."""
        pattern = f"{udid}_*.md"
        matches = list(self.md_dir.glob(pattern))
        if not matches:
            return None
        try:
            return matches[0].read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("Could not read %s: %s", matches[0], exc)
            return None

    # ------------------------------------------------------------------

    class MatchResult:
        __slots__ = ("udid", "old_url", "jaccard")

        def __init__(self, udid: str, old_url: str, jaccard: float) -> None:
            self.udid = udid
            self.old_url = old_url
            self.jaccard = jaccard

        def __repr__(self) -> str:
            return (
                f"MatchResult(udid={self.udid!r}, "
                f"old_url={self.old_url!r}, jaccard={self.jaccard:.3f})"
            )

    def find_best_match(self, new_page_text: str) -> Optional["ExistingPageSignatures.MatchResult"]:
        """
        Return the best-matching existing page for *new_page_text*, or None
        if the signature store is empty.

        The caller is responsible for applying the threshold:

            result = sigs.find_best_match(text)
            if result and result.jaccard >= JACCARD_THRESHOLD:
                # rename
        """
        if not self._sigs or not new_page_text:
            return None

        new_sig = _minhash(new_page_text)
        best_udid = None
        best_score = -1.0

        for udid, sig in self._sigs.items():
            score = new_sig.jaccard(sig)
            if score > best_score:
                best_score = score
                best_udid = udid

        if best_udid is None:
            return None

        return self.MatchResult(
            udid=best_udid,
            old_url=self._urls[best_udid],
            jaccard=best_score,
        )


# ---------------------------------------------------------------------------
# Public reconciliation function — drop-in for check_sitemap.py
# ---------------------------------------------------------------------------

def reconcile_new_url(
    new_url: str,
    new_page_text: str,
    existing_signatures: ExistingPageSignatures,
    *,
    threshold: float = JACCARD_THRESHOLD,
) -> dict:
    """
    Decide whether *new_url* is a rename of an existing page or a genuinely
    new page, using MinHash Jaccard similarity against scraped .md content.

    Parameters
    ----------
    new_url:
        The URL discovered in the sitemap that is not yet in the CSV.
    new_page_text:
        The scraped (Markdown) text of that URL.  Pass an empty string if the
        page hasn't been fetched yet — the function will conservatively treat
        it as a new page.
    existing_signatures:
        Pre-built ExistingPageSignatures instance for the current CSV.
    threshold:
        Jaccard score at or above which the URL is treated as a rename.
        Defaults to module-level JACCARD_THRESHOLD (0.5).

    Returns
    -------
    dict with keys:
        ``verdict``   — ``"rename"`` or ``"new_page"``
        ``udid``      — matched UDID if rename, else None
        ``old_url``   — matched old URL if rename, else None
        ``jaccard``   — similarity score (0.0 if no match or no text)
        ``new_url``   — echoed back for convenience
    """
    result = existing_signatures.find_best_match(new_page_text)

    if result is not None and result.jaccard >= threshold:
        logger.info(
            "URL rename detected  |  %.3f Jaccard  |  %s  →  %s  (UDID: %s)",
            result.jaccard,
            result.old_url,
            new_url,
            result.udid,
        )
        return {
            "verdict": "rename",
            "udid": result.udid,
            "old_url": result.old_url,
            "jaccard": result.jaccard,
            "new_url": new_url,
        }

    jaccard_score = result.jaccard if result else 0.0
    logger.info(
        "New page detected  |  %.3f Jaccard (best match)  |  %s",
        jaccard_score,
        new_url,
    )
    return {
        "verdict": "new_page",
        "udid": None,
        "old_url": None,
        "jaccard": jaccard_score,
        "new_url": new_url,
    }
