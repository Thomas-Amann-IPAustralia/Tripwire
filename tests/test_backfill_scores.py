"""
tests/test_backfill_scores.py

Tests for the historical score backfill/replay (src/backfill_scores.py).

No network and no real models: the bi-/cross-encoder are injected as light
fakes, and a tiny in-memory SQLite corpus stands in for ipfr.sqlite. Diff
reconstruction is exercised against real files written under tmp_path.
"""

from __future__ import annotations

import json
import sqlite3

import numpy as np
import pytest

from src import backfill_scores as bf


# ---------------------------------------------------------------------------
# Fakes & fixtures
# ---------------------------------------------------------------------------


class _FakeBiEncoder:
    """Returns a fixed unit vector so cosine similarity is deterministic."""

    def __init__(self, vec):
        self._vec = np.asarray(vec, dtype=np.float32)

    def encode(self, texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False):
        return np.stack([self._vec for _ in texts])


class _FakeCrossEncoder:
    """Returns a fixed logit for every pair (sigmoid applied downstream)."""

    def __init__(self, logit):
        self._logit = logit

    def predict(self, pairs, **kwargs):
        return np.array([self._logit for _ in pairs], dtype=np.float32)


@pytest.fixture
def corpus_conn():
    """Minimal ipfr corpus: two pages, one chunk each, with embeddings."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row  # stage modules do dict(row); match production
    conn.execute("CREATE TABLE pages (page_id TEXT PRIMARY KEY, content TEXT, status TEXT)")
    conn.execute(
        "CREATE TABLE chunks (chunk_id TEXT, page_id TEXT, chunk_index INTEGER, chunk_embedding BLOB)"
    )
    conn.execute("CREATE TABLE graph_edges (source_page_id TEXT, target_page_id TEXT, edge_type TEXT, weight REAL)")
    conn.execute(
        "CREATE TABLE llm_assessments (run_id TEXT, ipfr_page_id TEXT, verdict TEXT, confidence REAL)"
    )
    conn.execute(
        "CREATE TABLE pipeline_runs (id INTEGER PRIMARY KEY, run_id TEXT, source_id TEXT, "
        "source_type TEXT, timestamp TEXT, triggered_pages TEXT, details TEXT)"
    )
    aligned = np.ones(4, dtype=np.float32)
    for pid in ("XAAA", "XBBB"):
        conn.execute("INSERT INTO pages VALUES (?,?,?)", (pid, f"content for {pid}", "active"))
        conn.execute(
            "INSERT INTO chunks VALUES (?,?,?,?)",
            (f"{pid}-c0", pid, 0, aligned.tobytes()),
        )
    conn.commit()
    return conn


def _config():
    return {
        "semantic_scoring": {
            "biencoder": {"high_threshold": 0.75, "low_medium_threshold": 0.45, "low_medium_min_chunks": 1},
            "crossencoder": {"threshold": 0.60, "max_context_tokens": 8192},
        },
        "graph": {"enabled": False},
        "paths": {},
    }


def _insert_run(conn, run_id, source_id, source_type, diff_type, diff_basename,
                candidates, triggered):
    details = {
        "stages": {
            "diff": {"diff_type": diff_type, "diff_path": f"/gha/{source_id}/{diff_basename}"},
            "relevance": {"top_candidates": [{"page_id": p, "final_score": 0.03} for p in candidates]},
        }
    }
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, source_id, source_type, timestamp, triggered_pages, details) "
        "VALUES (?,?,?,?,?,?)",
        (run_id, source_id, source_type, "2026-07-16T00:00:00+00:00",
         json.dumps(triggered), json.dumps(details)),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Reconstruction
# ---------------------------------------------------------------------------


def test_reconstruct_webpage_reads_and_normalises_diff(tmp_path):
    src_dir = tmp_path / "afp"
    src_dir.mkdir()
    (src_dir / "afp_2026-07-16-392.diff").write_text("--- previous\n+++ current\n+New   text  here\n")
    row = {
        "source_id": "afp", "run_id": "2026-07-16-392", "source_type": "webpage",
        "stages": {"diff": {"diff_type": "unified_diff", "diff_path": "/gha/afp/afp_2026-07-16-392.diff"}},
    }
    text, status = bf.reconstruct_change_text(row, tmp_path)
    assert status == "unified_diff"
    assert "New text here" in text  # whitespace collapsed by the normaliser


def test_reconstruct_skips_frl_and_rss(tmp_path):
    for stype, dtype in (("frl", "compilation_change"), ("rss", "rss_items")):
        row = {"source_id": "s", "run_id": "r", "source_type": stype,
               "stages": {"diff": {"diff_type": dtype, "diff_path": ""}}}
        text, status = bf.reconstruct_change_text(row, tmp_path)
        assert text is None
        assert status.startswith("skipped_")


def test_reconstruct_missing_and_empty(tmp_path):
    row = {"source_id": "s", "run_id": "r", "source_type": "webpage",
           "stages": {"diff": {"diff_type": "unified_diff", "diff_path": "/gha/s/s_r.diff"}}}
    text, status = bf.reconstruct_change_text(row, tmp_path)
    assert text is None and status == "diff_file_missing"

    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "s_r.diff").write_text("   \n")
    text, status = bf.reconstruct_change_text(row, tmp_path)
    assert text is None and status == "empty_diff"


# ---------------------------------------------------------------------------
# End-to-end replay
# ---------------------------------------------------------------------------


def test_run_backfill_writes_labelled_rows(corpus_conn, tmp_path):
    conn = corpus_conn
    (tmp_path / "afp").mkdir()
    (tmp_path / "afp" / "afp_2026-07-16-392.diff").write_text(
        "--- previous\n+++ current\n+Significant amendment to the Act.\n"
    )
    _insert_run(conn, "2026-07-16-392", "afp", "webpage", "unified_diff",
                "afp_2026-07-16-392.diff", candidates=["XAAA", "XBBB"], triggered=["XAAA"])
    # Known LLM verdict for the confirmed page.
    conn.execute("INSERT INTO llm_assessments VALUES (?,?,?,?)",
                 ("2026-07-16-392", "XAAA", "CHANGE_REQUIRED", 0.85))
    conn.commit()

    stats = bf.run_backfill(
        conn, _config(), since="2026-05-07", snapshot_dir=tmp_path,
        biencoder_model=_FakeBiEncoder([1, 0, 0, 0]),
        crossencoder_model=_FakeCrossEncoder(4.0),  # high logit -> ~1.0 after sigmoid
        now="2026-07-21T00:00:00+00:00",
    )
    assert stats["runs_scored"] == 1
    assert stats["pages_written"] == 2

    rows = {r[0]: r for r in conn.execute(
        "SELECT ipfr_page_id, biencoder_max_chunk_score, crossencoder_final_score, "
        "historical_confirmed, llm_verdict, ce_threshold FROM score_backfill")}
    assert set(rows) == {"XAAA", "XBBB"}
    # Confirmed page carries its known verdict and a real cross-encoder score.
    assert rows["XAAA"][3] == 1
    assert rows["XAAA"][4] == "CHANGE_REQUIRED"
    assert rows["XAAA"][2] is not None and rows["XAAA"][2] >= 0.60
    assert rows["XAAA"][5] == 0.60
    # Non-confirmed candidate is recorded but flagged not-confirmed.
    assert rows["XBBB"][3] == 0


def test_run_backfill_is_idempotent(corpus_conn, tmp_path):
    conn = corpus_conn
    (tmp_path / "afp").mkdir()
    (tmp_path / "afp" / "afp_2026-07-16-392.diff").write_text("--- previous\n+++ current\n+Change.\n")
    _insert_run(conn, "2026-07-16-392", "afp", "webpage", "unified_diff",
                "afp_2026-07-16-392.diff", candidates=["XAAA"], triggered=["XAAA"])
    kwargs = dict(since="2026-05-07", snapshot_dir=tmp_path,
                  biencoder_model=_FakeBiEncoder([1, 0, 0, 0]),
                  crossencoder_model=_FakeCrossEncoder(4.0))
    bf.run_backfill(conn, _config(), **kwargs)
    bf.run_backfill(conn, _config(), **kwargs)
    (n,) = conn.execute("SELECT COUNT(*) FROM score_backfill").fetchone()
    assert n == 1  # INSERT OR REPLACE, not duplicated


def test_run_backfill_records_skips(corpus_conn, tmp_path):
    conn = corpus_conn
    _insert_run(conn, "2026-06-01-999", "act", "frl", "compilation_change",
                "", candidates=["XAAA"], triggered=["XAAA"])
    stats = bf.run_backfill(
        conn, _config(), since="2026-05-07", snapshot_dir=tmp_path,
        biencoder_model=_FakeBiEncoder([1, 0, 0, 0]),
        crossencoder_model=_FakeCrossEncoder(4.0),
    )
    assert stats["runs_scored"] == 0
    assert stats["pages_written"] == 0
    assert any(k.startswith("skipped_frl") for k in stats["skipped"])


def test_report_renders_without_labels(corpus_conn, tmp_path):
    conn = corpus_conn
    bf.create_backfill_table(conn)
    report = bf.generate_report(conn, {"runs_scored": 0, "runs_selected": 0, "pages_written": 0, "skipped": {}})
    assert "Historical Score Backfill" in report
    assert "Not enough labelled data" in report
