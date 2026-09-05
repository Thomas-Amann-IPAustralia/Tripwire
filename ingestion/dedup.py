"""
ingestion/dedup.py

Post-ingestion cleanup passes that depend on the full corpus being loaded:

  * :func:`mark_duplicates` — collapses exact (version_hash) and near-duplicate
    (doc-embedding cosine similarity) pages by setting their ``status`` to
    ``'duplicate'`` and pointing ``duplicate_of`` at the canonical page.
    Duplicates are then excluded from graph construction and retrieval (see
    :func:`ingestion.db.get_active_pages`).

  * :func:`filter_global_keyphrases` — drops keyphrases that appear on more
    than *df_threshold* of active pages.  This catches boilerplate-driven
    keyphrases ("Response website", "holders navigate") that survive
    per-page YAKE extraction but would otherwise distort Stage 4 BM25 scoring.

Both sweeps are idempotent and safe to re-run at the end of every ingestion
cycle.  They respect the modularity constraint: a fork that replaces the
"influenced" corpus inherits the same cleanup without code changes.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from typing import Any

from ingestion import db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------


def mark_duplicates(
    conn: sqlite3.Connection,
    *,
    near_duplicate_threshold: float = 0.98,
) -> dict[str, int]:
    """Mark exact and near-duplicate pages, pointing them at their canonical.

    Exact duplicates: pages sharing a ``version_hash`` — the earliest
    ``last_ingested`` wins (lexicographic order on the ISO date, ties broken by
    ``page_id``).

    Near-duplicates: pairs of active pages whose doc embeddings have cosine
    similarity ≥ *near_duplicate_threshold*.  The page with the shorter
    content (or smaller page_id on ties) becomes the duplicate.

    Any page previously flagged as a duplicate whose canonical is no longer
    the closest match is **reset** to ``'active'`` before re-evaluation, so
    this function is safe to re-run each cycle.

    Returns a dict with counts of pages marked per class:
        {"exact": N, "near": M, "reset": K}
    """
    reset = _reset_duplicates(conn)
    exact = _mark_exact_duplicates(conn)
    near = _mark_near_duplicates(conn, threshold=near_duplicate_threshold)
    logger.info(
        "Dedup sweep: reset=%d exact=%d near=%d (threshold=%.3f)",
        reset, exact, near, near_duplicate_threshold,
    )
    return {"exact": exact, "near": near, "reset": reset}


def _reset_duplicates(conn: sqlite3.Connection) -> int:
    """Reset pages previously flagged as duplicates so we can re-evaluate."""
    cursor = conn.execute(
        "UPDATE pages SET status = 'active', duplicate_of = NULL "
        "WHERE status = 'duplicate'"
    )
    return cursor.rowcount or 0


def _mark_exact_duplicates(conn: sqlite3.Connection) -> int:
    """Collapse pages sharing a version_hash into a single canonical row."""
    rows = conn.execute(
        """
        SELECT page_id, version_hash, last_ingested
        FROM pages
        WHERE status != 'stub' AND version_hash IS NOT NULL AND version_hash != ''
        ORDER BY page_id
        """
    ).fetchall()

    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row["version_hash"]].append(
            (row["page_id"], row["last_ingested"] or "")
        )

    count = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        # Canonical = earliest last_ingested, ties broken by page_id.
        members.sort(key=lambda m: (m[1] or "9999-99-99", m[0]))
        canonical_id = members[0][0]
        for dup_id, _ in members[1:]:
            db.set_page_status(
                conn, dup_id, status="duplicate", duplicate_of=canonical_id,
            )
            count += 1
    return count


def _mark_near_duplicates(
    conn: sqlite3.Connection, threshold: float,
) -> int:
    """Use doc-embedding cosine similarity to catch non-exact duplicates."""
    try:
        import numpy as np
    except ImportError:
        logger.warning("numpy not installed — skipping near-duplicate detection.")
        return 0

    rows = conn.execute(
        """
        SELECT page_id, content, doc_embedding
        FROM pages
        WHERE status = 'active' AND doc_embedding IS NOT NULL
          AND LENGTH(doc_embedding) > 0
        ORDER BY page_id
        """
    ).fetchall()

    if len(rows) < 2:
        return 0

    page_ids = [r["page_id"] for r in rows]
    content_lengths = {r["page_id"]: len(r["content"] or "") for r in rows}

    try:
        embeddings = np.stack([
            np.frombuffer(r["doc_embedding"], dtype=np.float32) for r in rows
        ])
    except ValueError as exc:
        logger.warning("Near-duplicate embedding stack failed: %s", exc)
        return 0

    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    embeddings = embeddings / norms
    sim_matrix = embeddings @ embeddings.T

    count = 0
    duplicate_of: dict[str, str] = {}
    n = len(page_ids)
    for i in range(n):
        pid_i = page_ids[i]
        if pid_i in duplicate_of:
            continue
        for j in range(i + 1, n):
            pid_j = page_ids[j]
            if pid_j in duplicate_of:
                continue
            if float(sim_matrix[i, j]) < threshold:
                continue
            # Canonical = longer content; ties broken by page_id.
            len_i, len_j = content_lengths[pid_i], content_lengths[pid_j]
            if (len_j, pid_j) > (len_i, pid_i):
                canonical, dup = pid_j, pid_i
            else:
                canonical, dup = pid_i, pid_j
            duplicate_of[dup] = canonical

    for dup, canonical in duplicate_of.items():
        # Resolve canonical transitively in case of chains.
        final = canonical
        while final in duplicate_of:
            final = duplicate_of[final]
        if final == dup:
            continue
        db.set_page_status(conn, dup, status="duplicate", duplicate_of=final)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Cross-document keyphrase IDF filter
# ---------------------------------------------------------------------------


def filter_global_keyphrases(
    conn: sqlite3.Connection,
    *,
    df_threshold: float = 0.7,
    min_pages: int = 5,
) -> int:
    """Drop keyphrases whose document frequency exceeds *df_threshold*.

    A keyphrase that appears on more than *df_threshold* of the active pages
    is almost certainly boilerplate-driven ("Response website", "holders
    navigate", etc.) and hurts Stage 4 BM25 signal-to-noise.  This sweep runs
    once per ingestion cycle.  Requires at least *min_pages* active pages —
    below that, cross-document repetition isn't a meaningful signal and the
    sweep is skipped.

    Returns the number of keyphrase rows deleted.
    """
    active_pages = conn.execute(
        "SELECT COUNT(*) AS n FROM pages WHERE status = 'active'"
    ).fetchone()["n"]

    if active_pages < min_pages:
        logger.info(
            "Keyphrase IDF sweep skipped: %d active pages < min_pages=%d.",
            active_pages, min_pages,
        )
        return 0

    cutoff = max(2, int(df_threshold * active_pages))
    rows = conn.execute(
        """
        SELECT k.keyphrase, COUNT(DISTINCT k.page_id) AS df
        FROM keyphrases k
        JOIN pages p ON p.page_id = k.page_id
        WHERE p.status = 'active'
        GROUP BY k.keyphrase
        HAVING df >= ?
        """,
        (cutoff,),
    ).fetchall()

    if not rows:
        return 0

    deleted = 0
    for row in rows:
        cursor = conn.execute(
            "DELETE FROM keyphrases WHERE keyphrase = ?", (row["keyphrase"],)
        )
        deleted += cursor.rowcount or 0
        logger.info(
            "Dropping keyphrase %r (df=%d/%d)",
            row["keyphrase"], row["df"], active_pages,
        )
    return deleted


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_corpus_contents(conn: sqlite3.Connection) -> list[str]:
    """Load plain-text content of all non-stub pages.  Used for boilerplate detection."""
    rows = conn.execute(
        "SELECT content FROM pages WHERE status != 'stub' AND content IS NOT NULL"
    ).fetchall()
    return [r["content"] for r in rows if r["content"]]
