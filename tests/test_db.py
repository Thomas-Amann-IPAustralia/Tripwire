"""
tests/test_db.py

Tests for ingestion/db.py — SQLite schema creation and CRUD operations.
No network calls; uses tmp_path fixture for isolated databases.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from ingestion.db import (
    init_db,
    get_connection,
    upsert_page,
    get_page,
    get_all_pages,
    get_page_version_hash,
    replace_chunks,
    get_chunks_for_page,
    get_all_chunks,
    replace_entities,
    get_entities_for_page,
    replace_keyphrases,
    get_keyphrases_for_page,
    upsert_graph_edge,
    get_edges_for_page,
    get_all_edges,
    clear_graph_edges,
    replace_sections,
    get_sections_for_page,
    log_pipeline_run,
    get_pipeline_runs,
    store_deferred_trigger,
    get_pending_deferred_triggers,
    mark_deferred_trigger_processed,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.sqlite"


@pytest.fixture
def conn(db_path):
    connection = init_db(db_path, wal_mode=True)
    yield connection
    connection.close()


def _sample_page(page_id="B1012"):
    return {
        "page_id": page_id,
        "url": f"https://example.com/{page_id}",
        "title": f"Test Page {page_id}",
        "content": "This is the content of the page about trade marks and patents.",
        "version_hash": "abc123def456",
        "last_modified": "2026-04-01",
        "last_checked": "2026-04-05",
        "last_ingested": "2026-04-05",
        "doc_embedding": None,
    }


# ---------------------------------------------------------------------------
# Schema and connection tests
# ---------------------------------------------------------------------------


def test_init_creates_all_tables(db_path):
    conn = init_db(db_path)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row[0] for row in cursor.fetchall()}
    expected = {
        "pages", "chunks", "entities", "keyphrases",
        "graph_edges", "sections", "pipeline_runs", "deferred_triggers",
    }
    assert expected.issubset(tables)
    conn.close()


def test_init_is_idempotent(db_path):
    """Calling init_db twice should not raise (IF NOT EXISTS guards)."""
    conn1 = init_db(db_path)
    conn1.close()
    conn2 = init_db(db_path)
    conn2.close()


def test_wal_mode_enabled(db_path):
    conn = init_db(db_path, wal_mode=True)
    row = conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"
    conn.close()


def test_get_connection_context_manager(db_path):
    with get_connection(db_path) as conn:
        pages = get_all_pages(conn)
    assert pages == []


def test_foreign_keys_enforced(conn):
    """Inserting a chunk with a nonexistent page_id should fail."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO chunks (chunk_id, page_id, chunk_text, chunk_index, chunk_embedding) "
            "VALUES ('c1', 'NONEXISTENT', 'text', 0, X'')"
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Page operations
# ---------------------------------------------------------------------------


def test_upsert_and_get_page(conn):
    page = _sample_page("B1012")
    upsert_page(conn, page)
    conn.commit()
    fetched = get_page(conn, "B1012")
    assert fetched is not None
    assert fetched["page_id"] == "B1012"
    assert fetched["title"] == "Test Page B1012"
    assert fetched["version_hash"] == "abc123def456"


def test_upsert_page_updates_existing(conn):
    page = _sample_page("B1012")
    upsert_page(conn, page)
    page["title"] = "Updated Title"
    page["version_hash"] = "newhash"
    upsert_page(conn, page)
    conn.commit()
    fetched = get_page(conn, "B1012")
    assert fetched["title"] == "Updated Title"
    assert fetched["version_hash"] == "newhash"


def test_get_page_nonexistent_returns_none(conn):
    assert get_page(conn, "ZZZZ") is None


def test_get_all_pages_empty(conn):
    assert get_all_pages(conn) == []


def test_get_all_pages_multiple(conn):
    for pid in ["A0001", "B1012", "C2003"]:
        upsert_page(conn, _sample_page(pid))
    conn.commit()
    pages = get_all_pages(conn)
    assert len(pages) == 3
    assert [p["page_id"] for p in pages] == ["A0001", "B1012", "C2003"]


def test_get_page_version_hash(conn):
    upsert_page(conn, _sample_page("B1012"))
    conn.commit()
    h = get_page_version_hash(conn, "B1012")
    assert h == "abc123def456"


def test_get_page_version_hash_missing(conn):
    assert get_page_version_hash(conn, "ZZZZ") is None


# ---------------------------------------------------------------------------
# Chunk operations
# ---------------------------------------------------------------------------


def _sample_chunks(page_id, n=3):
    return [
        {
            "chunk_id": f"{page_id}-chunk-{i:03d}",
            "page_id": page_id,
            "chunk_text": f"Chunk {i} text for {page_id}",
            "chunk_index": i,
            "section_heading": f"Section {i}" if i > 0 else None,
            "chunk_embedding": bytes(4),  # 1 float32 placeholder
        }
        for i in range(n)
    ]


def test_replace_and_get_chunks(conn):
    upsert_page(conn, _sample_page("B1012"))
    chunks = _sample_chunks("B1012", n=3)
    replace_chunks(conn, "B1012", chunks)
    conn.commit()
    fetched = get_chunks_for_page(conn, "B1012")
    assert len(fetched) == 3
    assert fetched[0]["chunk_index"] == 0
    assert fetched[1]["section_heading"] == "Section 1"


def test_replace_chunks_removes_old(conn):
    upsert_page(conn, _sample_page("B1012"))
    replace_chunks(conn, "B1012", _sample_chunks("B1012", n=5))
    replace_chunks(conn, "B1012", _sample_chunks("B1012", n=2))
    conn.commit()
    assert len(get_chunks_for_page(conn, "B1012")) == 2


def test_get_all_chunks(conn):
    for pid in ["A0001", "B1012"]:
        upsert_page(conn, _sample_page(pid))
        replace_chunks(conn, pid, _sample_chunks(pid, n=2))
    conn.commit()
    all_chunks = get_all_chunks(conn)
    assert len(all_chunks) == 4


# ---------------------------------------------------------------------------
# Entity operations
# ---------------------------------------------------------------------------


def _sample_entities():
    return [
        {"entity_text": "Trade Marks Act 1995", "entity_type": "LAW"},
        {"entity_text": "IP Australia", "entity_type": "ORG"},
        {"entity_text": "12 months", "entity_type": "DATE"},
    ]


def test_replace_and_get_entities(conn):
    upsert_page(conn, _sample_page("B1012"))
    replace_entities(conn, "B1012", _sample_entities())
    conn.commit()
    entities = get_entities_for_page(conn, "B1012")
    assert len(entities) == 3
    texts = {e["entity_text"] for e in entities}
    assert "Trade Marks Act 1995" in texts


def test_replace_entities_removes_old(conn):
    upsert_page(conn, _sample_page("B1012"))
    replace_entities(conn, "B1012", _sample_entities())
    replace_entities(conn, "B1012", [{"entity_text": "Only One", "entity_type": "ORG"}])
    conn.commit()
    assert len(get_entities_for_page(conn, "B1012")) == 1


def test_entity_uniqueness_constraint(conn):
    """Duplicate entity (page_id, entity_text, entity_type) is silently ignored."""
    upsert_page(conn, _sample_page("B1012"))
    dupes = _sample_entities() + _sample_entities()
    replace_entities(conn, "B1012", dupes)
    conn.commit()
    assert len(get_entities_for_page(conn, "B1012")) == 3


# ---------------------------------------------------------------------------
# Keyphrase operations
# ---------------------------------------------------------------------------


def _sample_keyphrases():
    return [
        {"keyphrase": "trade mark registration", "score": 0.05},
        {"keyphrase": "IP Australia", "score": 0.08},
        {"keyphrase": "examination period", "score": 0.12},
    ]


def test_replace_and_get_keyphrases(conn):
    upsert_page(conn, _sample_page("B1012"))
    replace_keyphrases(conn, "B1012", _sample_keyphrases())
    conn.commit()
    kps = get_keyphrases_for_page(conn, "B1012")
    assert len(kps) == 3
    phrases = [k["keyphrase"] for k in kps]
    assert "trade mark registration" in phrases


def test_replace_keyphrases_removes_old(conn):
    upsert_page(conn, _sample_page("B1012"))
    replace_keyphrases(conn, "B1012", _sample_keyphrases())
    replace_keyphrases(conn, "B1012", [{"keyphrase": "single phrase", "score": 0.1}])
    conn.commit()
    assert len(get_keyphrases_for_page(conn, "B1012")) == 1


# ---------------------------------------------------------------------------
# Graph edge operations
# ---------------------------------------------------------------------------


def test_upsert_and_get_graph_edge(conn):
    for pid in ["A0001", "B1012"]:
        upsert_page(conn, _sample_page(pid))
    upsert_graph_edge(conn, "A0001", "B1012", "embedding_similarity", 0.72)
    conn.commit()
    edges = get_edges_for_page(conn, "A0001")
    assert len(edges) == 1
    assert edges[0]["target_page_id"] == "B1012"
    assert edges[0]["weight"] == pytest.approx(0.72)


def test_upsert_graph_edge_takes_max_weight(conn):
    for pid in ["A0001", "B1012"]:
        upsert_page(conn, _sample_page(pid))
    upsert_graph_edge(conn, "A0001", "B1012", "embedding_similarity", 0.5)
    upsert_graph_edge(conn, "A0001", "B1012", "embedding_similarity", 0.9)
    conn.commit()
    edges = get_edges_for_page(conn, "A0001")
    assert edges[0]["weight"] == pytest.approx(0.9)


def test_upsert_graph_edge_lower_weight_not_overwritten(conn):
    for pid in ["A0001", "B1012"]:
        upsert_page(conn, _sample_page(pid))
    upsert_graph_edge(conn, "A0001", "B1012", "embedding_similarity", 0.9)
    upsert_graph_edge(conn, "A0001", "B1012", "embedding_similarity", 0.3)
    conn.commit()
    edges = get_edges_for_page(conn, "A0001")
    assert edges[0]["weight"] == pytest.approx(0.9)


def test_clear_graph_edges_by_type(conn):
    for pid in ["A0001", "B1012", "C2003"]:
        upsert_page(conn, _sample_page(pid))
    upsert_graph_edge(conn, "A0001", "B1012", "embedding_similarity", 0.7)
    upsert_graph_edge(conn, "A0001", "C2003", "entity_overlap", 0.5)
    conn.commit()
    clear_graph_edges(conn, edge_type="embedding_similarity")
    conn.commit()
    all_edges = get_all_edges(conn)
    assert len(all_edges) == 1
    assert all_edges[0]["edge_type"] == "entity_overlap"


def test_clear_all_graph_edges(conn):
    for pid in ["A0001", "B1012"]:
        upsert_page(conn, _sample_page(pid))
    upsert_graph_edge(conn, "A0001", "B1012", "embedding_similarity", 0.7)
    conn.commit()
    clear_graph_edges(conn)
    conn.commit()
    assert get_all_edges(conn) == []


# ---------------------------------------------------------------------------
# Section operations
# ---------------------------------------------------------------------------


def _sample_sections():
    return [
        {"heading_text": "Introduction", "heading_level": 1, "char_start": 0, "char_end": 120},
        {"heading_text": "Requirements", "heading_level": 2, "char_start": 120, "char_end": 400},
        {"heading_text": "Fees", "heading_level": 2, "char_start": 400, "char_end": 600},
    ]


def test_replace_and_get_sections(conn):
    upsert_page(conn, _sample_page("B1012"))
    replace_sections(conn, "B1012", _sample_sections())
    conn.commit()
    sections = get_sections_for_page(conn, "B1012")
    assert len(sections) == 3
    assert sections[0]["heading_text"] == "Introduction"
    assert sections[0]["heading_level"] == 1


def test_replace_sections_removes_old(conn):
    upsert_page(conn, _sample_page("B1012"))
    replace_sections(conn, "B1012", _sample_sections())
    replace_sections(conn, "B1012", [
        {"heading_text": "Only", "heading_level": 1, "char_start": 0, "char_end": 50}
    ])
    conn.commit()
    assert len(get_sections_for_page(conn, "B1012")) == 1


# ---------------------------------------------------------------------------
# Pipeline run logging
# ---------------------------------------------------------------------------


def _sample_run_entry(run_id="2026-04-05-001", source_id="frl_trademarks"):
    return {
        "run_id": run_id,
        "source_id": source_id,
        "source_url": "https://example.com/legislation",
        "source_type": "frl",
        "timestamp": "2026-04-05T01:00:00Z",
        "stage_reached": "stage1_metadata",
        "outcome": "no_change",
        "details": {"stages": {"metadata_probe": {"changed": False}}},
    }


def test_log_pipeline_run_and_retrieve(conn):
    entry = _sample_run_entry()
    row_id = log_pipeline_run(conn, entry)
    conn.commit()
    assert isinstance(row_id, int) and row_id > 0
    rows = get_pipeline_runs(conn, "2026-04-05-001")
    assert len(rows) == 1
    assert rows[0]["source_id"] == "frl_trademarks"
    assert rows[0]["outcome"] == "no_change"


def test_log_pipeline_run_details_stored_as_json(conn):
    entry = _sample_run_entry()
    log_pipeline_run(conn, entry)
    conn.commit()
    rows = get_pipeline_runs(conn, "2026-04-05-001")
    details = json.loads(rows[0]["details"])
    assert details["stages"]["metadata_probe"]["changed"] is False


def test_log_pipeline_run_with_error(conn):
    entry = _sample_run_entry()
    entry["outcome"] = "error"
    entry["error_type"] = "RetryableError"
    entry["error_message"] = "HTTP 503 fetching resource"
    log_pipeline_run(conn, entry)
    conn.commit()
    rows = get_pipeline_runs(conn, "2026-04-05-001")
    assert rows[0]["error_type"] == "RetryableError"


def test_log_pipeline_run_triggered_pages_list(conn):
    entry = _sample_run_entry()
    entry["triggered_pages"] = ["B1012", "C2003"]
    entry["outcome"] = "completed"
    log_pipeline_run(conn, entry)
    conn.commit()
    rows = get_pipeline_runs(conn, "2026-04-05-001")
    triggered = json.loads(rows[0]["triggered_pages"])
    assert triggered == ["B1012", "C2003"]


def test_multiple_runs_stored(conn):
    for i in range(5):
        entry = _sample_run_entry(run_id="2026-04-05-001", source_id=f"source_{i}")
        log_pipeline_run(conn, entry)
    conn.commit()
    rows = get_pipeline_runs(conn, "2026-04-05-001")
    assert len(rows) == 5


# ---------------------------------------------------------------------------
# Deferred trigger operations
# ---------------------------------------------------------------------------


def _sample_trigger(run_id="2026-04-05-001"):
    return {
        "run_id": run_id,
        "source_id": "frl_trademarks",
        "ipfr_page_id": "B1012",
        "trigger_data": {"rrf_score": 0.04, "diff_text": "The period changed from 12 to 6 months."},
        "created_at": "2026-04-05T01:30:00Z",
    }


def test_store_and_retrieve_deferred_trigger(conn):
    trigger_id = store_deferred_trigger(conn, _sample_trigger())
    conn.commit()
    assert isinstance(trigger_id, int) and trigger_id > 0
    pending = get_pending_deferred_triggers(conn, max_age_days=365)
    assert len(pending) == 1
    assert pending[0]["source_id"] == "frl_trademarks"


def test_mark_deferred_trigger_processed(conn):
    trigger_id = store_deferred_trigger(conn, _sample_trigger())
    conn.commit()
    mark_deferred_trigger_processed(conn, trigger_id)
    conn.commit()
    pending = get_pending_deferred_triggers(conn, max_age_days=365)
    assert len(pending) == 0


def test_deferred_trigger_data_stored_as_json(conn):
    store_deferred_trigger(conn, _sample_trigger())
    conn.commit()
    pending = get_pending_deferred_triggers(conn, max_age_days=365)
    data = json.loads(pending[0]["trigger_data"])
    assert data["rrf_score"] == pytest.approx(0.04)
