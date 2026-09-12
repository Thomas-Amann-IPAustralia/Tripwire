"""
tests/test_ingestion_dedup.py

Tests for ingestion/dedup.py — exact-duplicate collapsing, near-duplicate
cosine matching, and the cross-document keyphrase IDF sweep.
"""

from __future__ import annotations

import numpy as np
import pytest

from ingestion import db, dedup


@pytest.fixture
def conn(tmp_path):
    connection = db.init_db(tmp_path / "dedup.sqlite")
    yield connection
    connection.close()


def _embedding(vec: list[float]) -> bytes:
    return np.array(vec, dtype=np.float32).tobytes()


def _seed_page(conn, page_id, *, content="body", version_hash="h1",
               doc_embedding=None, last_ingested="2026-04-10", status="active"):
    db.upsert_page(conn, {
        "page_id": page_id,
        "url": f"https://example/{page_id}",
        "title": page_id,
        "content": content,
        "version_hash": version_hash,
        "last_modified": "2026-04-01",
        "last_checked": "2026-04-10",
        "last_ingested": last_ingested,
        "doc_embedding": doc_embedding,
        "status": status,
    })


# ---------------------------------------------------------------------------
# Exact-hash duplicate collapsing
# ---------------------------------------------------------------------------


def test_mark_duplicates_collapses_pages_sharing_version_hash(conn):
    _seed_page(conn, "A", version_hash="abc", last_ingested="2026-04-01")
    _seed_page(conn, "B", version_hash="abc", last_ingested="2026-04-05")
    _seed_page(conn, "C", version_hash="xyz")

    result = dedup.mark_duplicates(conn)
    assert result["exact"] == 1

    rows = {r["page_id"]: r for r in db.get_all_pages(conn)}
    assert rows["A"]["status"] == "active"        # earliest last_ingested wins
    assert rows["B"]["status"] == "duplicate"
    assert rows["B"]["duplicate_of"] == "A"
    assert rows["C"]["status"] == "active"


def test_mark_duplicates_is_idempotent(conn):
    _seed_page(conn, "A", version_hash="abc", last_ingested="2026-04-01")
    _seed_page(conn, "B", version_hash="abc", last_ingested="2026-04-05")

    first = dedup.mark_duplicates(conn)
    second = dedup.mark_duplicates(conn)
    assert first["exact"] == 1
    # Second run resets then re-applies.
    assert second["reset"] >= 1
    rows = {r["page_id"]: r for r in db.get_all_pages(conn)}
    assert rows["B"]["status"] == "duplicate"


def test_mark_duplicates_skips_stubs(conn):
    _seed_page(conn, "A", version_hash="abc", status="stub")
    _seed_page(conn, "B", version_hash="abc")
    dedup.mark_duplicates(conn)
    rows = {r["page_id"]: r for r in db.get_all_pages(conn)}
    assert rows["A"]["status"] == "stub"
    assert rows["B"]["status"] == "active"


# ---------------------------------------------------------------------------
# Near-duplicate (cosine) detection
# ---------------------------------------------------------------------------


def test_near_duplicate_collapsing_with_high_cosine_similarity(conn):
    # Two near-parallel unit vectors (cos ≈ 1) + one orthogonal outlier.
    e1 = _embedding([1.0, 0.0, 0.0])
    e2 = _embedding([0.999, 0.0447, 0.0])   # cos ≈ 0.999
    e3 = _embedding([0.0, 1.0, 0.0])
    _seed_page(conn, "A", version_hash="h1", content="a long body" * 20, doc_embedding=e1)
    _seed_page(conn, "B", version_hash="h2", content="shorter", doc_embedding=e2)
    _seed_page(conn, "C", version_hash="h3", content="outlier", doc_embedding=e3)

    result = dedup.mark_duplicates(conn, near_duplicate_threshold=0.98)
    assert result["near"] >= 1

    rows = {r["page_id"]: r for r in db.get_all_pages(conn)}
    # A (longer) should be canonical; B should be the duplicate.
    assert rows["A"]["status"] == "active"
    assert rows["B"]["status"] == "duplicate"
    assert rows["B"]["duplicate_of"] == "A"
    assert rows["C"]["status"] == "active"


def test_near_duplicate_threshold_is_respected(conn):
    e1 = _embedding([1.0, 0.0])
    e2 = _embedding([0.8, 0.6])  # cos ≈ 0.8
    _seed_page(conn, "A", version_hash="h1", content="aaaa", doc_embedding=e1)
    _seed_page(conn, "B", version_hash="h2", content="bbbb", doc_embedding=e2)

    result = dedup.mark_duplicates(conn, near_duplicate_threshold=0.98)
    assert result["near"] == 0


# ---------------------------------------------------------------------------
# Global keyphrase IDF sweep
# ---------------------------------------------------------------------------


def test_filter_global_keyphrases_drops_pervasive_phrases(conn):
    for i in range(6):
        pid = f"P{i}"
        _seed_page(conn, pid, version_hash=f"h{i}")
        db.replace_keyphrases(conn, pid, [
            {"keyphrase": "Response website", "score": 0.01},
            {"keyphrase": f"unique phrase {i}", "score": 0.05},
        ])

    dropped = dedup.filter_global_keyphrases(conn, df_threshold=0.7, min_pages=5)
    assert dropped == 6  # one row per page for "Response website"

    remaining = conn.execute(
        "SELECT DISTINCT keyphrase FROM keyphrases ORDER BY keyphrase"
    ).fetchall()
    phrases = {r["keyphrase"] for r in remaining}
    assert "Response website" not in phrases
    assert "unique phrase 0" in phrases


def test_filter_global_keyphrases_skips_when_corpus_too_small(conn):
    _seed_page(conn, "A", version_hash="h1")
    db.replace_keyphrases(conn, "A", [{"keyphrase": "kp", "score": 0.1}])

    dropped = dedup.filter_global_keyphrases(conn, df_threshold=0.7, min_pages=5)
    assert dropped == 0
    # keyphrase survives.
    rows = db.get_keyphrases_for_page(conn, "A")
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Schema migrations (status / duplicate_of columns get added to legacy DBs)
# ---------------------------------------------------------------------------


def test_schema_migration_adds_new_columns_to_legacy_pages_table(tmp_path):
    import sqlite3

    legacy_path = tmp_path / "legacy.sqlite"
    legacy_conn = sqlite3.connect(legacy_path)
    legacy_conn.executescript(
        """
        CREATE TABLE pages (
            page_id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            version_hash TEXT NOT NULL,
            last_modified TEXT,
            last_checked TEXT,
            last_ingested TEXT,
            doc_embedding BLOB
        );
        INSERT INTO pages VALUES
            ('A', 'http://x', 'Page A', 'content', 'hash', NULL, NULL, NULL, NULL);
        """
    )
    legacy_conn.commit()
    legacy_conn.close()

    conn = db.init_db(legacy_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(pages)").fetchall()}
    assert "status" in cols
    assert "duplicate_of" in cols

    row = conn.execute("SELECT status, duplicate_of FROM pages WHERE page_id = 'A'").fetchone()
    assert row["status"] == "active"
    assert row["duplicate_of"] is None
    conn.close()
