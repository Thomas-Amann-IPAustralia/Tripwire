"""Tests for src/recover_db.py (merging run history from other database copies)."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from ingestion.db import init_db
from src.backfill_scores import create_backfill_table
from src.recover_db import main, merge_database, recovered_run_ids


def _db(path: Path) -> sqlite3.Connection:
    conn = init_db(path, wal_mode=False)
    create_backfill_table(conn)
    conn.commit()
    return conn


def _run(conn, run_id, source_id="S1", ts=None, details="{}"):
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, source_id, source_url, source_type, timestamp, "
        "stage_reached, outcome, details) VALUES (?,?,?,?,?,?,?,?)",
        (run_id, source_id, f"https://example.gov.au/{source_id}", "webpage",
         ts or f"{run_id}T02:00:00", "stage_2", "completed", details),
    )


def _assessment(conn, run_id, page="P1"):
    conn.execute(
        "INSERT INTO llm_assessments (run_id, ipfr_page_id, verdict, confidence, reasoning, "
        "suggested_changes, model, generated_at) VALUES (?,?,?,?,?,?,?,?)",
        (run_id, page, "CHANGE_REQUIRED", 0.9, "r", "[]", "m", f"{run_id}T02:05:00"),
    )


def _deferred(conn, run_id, processed=0):
    conn.execute(
        "INSERT INTO deferred_triggers (run_id, source_id, ipfr_page_id, trigger_data, created_at, processed) "
        "VALUES (?,?,?,?,?,?)",
        (run_id, "S1", "P1", "{}", f"{run_id}T02:06:00", processed),
    )


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _scenario(tmp_path):
    """Base DB plus two 'release' copies that each diverged from it by one day."""
    base = _db(tmp_path / "base.sqlite")
    _run(base, "2026-09-22-460")
    _run(base, "2026-09-22-460", source_id="S2")
    _assessment(base, "2026-09-22-460")
    base.commit()
    base.close()

    releases = []
    for day, run_id in ((23, "2026-09-23-461"), (24, "2026-09-24-462")):
        path = tmp_path / f"release-{day}.sqlite"
        shutil.copy(tmp_path / "base.sqlite", path)
        conn = sqlite3.connect(path)
        _run(conn, run_id)
        _run(conn, run_id, source_id="S2", details='{"big": "payload"}')
        _assessment(conn, run_id)
        _deferred(conn, run_id)
        conn.commit()
        conn.close()
        releases.append(path)
    return tmp_path / "base.sqlite", releases


def test_merges_rows_lost_from_each_release(tmp_path):
    base_path, releases = _scenario(tmp_path)
    target = sqlite3.connect(base_path)

    first = merge_database(target, releases[0])
    second = merge_database(target, releases[1])

    assert first["pipeline_runs"] == 2 and second["pipeline_runs"] == 2
    assert first["llm_assessments"] == 1 and second["llm_assessments"] == 1
    assert _count(target, "pipeline_runs") == 6
    assert {r[0] for r in target.execute("SELECT DISTINCT run_id FROM pipeline_runs")} == {
        "2026-09-22-460", "2026-09-23-461", "2026-09-24-462"}
    # Full row content survives, not just keys.
    assert target.execute(
        "SELECT details FROM pipeline_runs WHERE run_id='2026-09-24-462' AND source_id='S2'"
    ).fetchone()[0] == '{"big": "payload"}'
    # Surrogate ids were reassigned without collisions.
    ids = [r[0] for r in target.execute("SELECT id FROM pipeline_runs")]
    assert len(ids) == len(set(ids))


def test_merge_is_idempotent(tmp_path):
    base_path, releases = _scenario(tmp_path)
    target = sqlite3.connect(base_path)
    for r in releases:
        merge_database(target, r)
    snapshot = _count(target, "pipeline_runs"), _count(target, "llm_assessments")

    for r in releases:
        counts = merge_database(target, r)
        assert all(v == 0 for v in counts.values())
    assert (_count(target, "pipeline_runs"), _count(target, "llm_assessments")) == snapshot


def test_deferred_triggers_restored_as_pending(tmp_path):
    base_path, releases = _scenario(tmp_path)
    target = sqlite3.connect(base_path)
    for r in releases:
        merge_database(target, r)
    assert target.execute("SELECT COUNT(*) FROM deferred_triggers WHERE processed = 0").fetchone()[0] == 2


def test_corpus_tables_are_not_touched(tmp_path):
    base_path, releases = _scenario(tmp_path)
    conn = sqlite3.connect(releases[0])
    conn.execute("INSERT INTO pages (page_id, url, title, content, version_hash) "
                 "VALUES ('X1', 'https://example', 'Stale', 'c', 'h')")
    conn.commit()
    conn.close()

    target = sqlite3.connect(base_path)
    merge_database(target, releases[0])
    assert _count(target, "pages") == 0


def test_older_schema_copy_missing_columns_and_tables(tmp_path):
    target = _db(tmp_path / "target.sqlite")
    old = sqlite3.connect(tmp_path / "old.sqlite")
    old.execute("CREATE TABLE pipeline_runs (id INTEGER PRIMARY KEY, run_id TEXT, source_id TEXT, timestamp TEXT)")
    old.execute("INSERT INTO pipeline_runs (run_id, source_id, timestamp) VALUES ('2026-05-01-1', 'S1', 't')")
    old.commit()
    old.close()

    counts = merge_database(target, tmp_path / "old.sqlite")
    assert counts == {"pipeline_runs": 1}
    # Missing NOT NULL column filled neutrally; missing nullable column left NULL.
    details, error = target.execute("SELECT details, error_message FROM pipeline_runs").fetchone()
    assert details == "" and error is None


def test_no_helper_indexes_left_behind(tmp_path):
    base_path, releases = _scenario(tmp_path)
    target = sqlite3.connect(base_path)
    merge_database(target, releases[0])
    names = [r[0] for r in target.execute("SELECT name FROM sqlite_master WHERE type='index'")]
    assert not any(n.startswith("_recover_") for n in names)


def test_recovered_run_ids_preview(tmp_path):
    base_path, releases = _scenario(tmp_path)
    target = sqlite3.connect(base_path)
    assert recovered_run_ids(target, releases[1]) == ["2026-09-24-462"]


def test_cli_writes_summary_and_survives_bad_source(tmp_path):
    base_path, releases = _scenario(tmp_path)
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"not a database" * 100)
    summary = tmp_path / "summary.md"

    rc = main(["--into", str(base_path), "--from", str(releases[0]), "--from", str(bad),
               "--summary", str(summary)])

    assert rc == 0
    text = summary.read_text()
    assert "2026-09-23-461" in text
    assert "error" in text
    assert _count(sqlite3.connect(base_path), "pipeline_runs") == 4
