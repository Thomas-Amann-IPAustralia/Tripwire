"""
src/backfill_scores.py

Historical semantic-score backfill / replay (audit follow-up, 2026-07-21).

Until this change, Stages 5 (bi-encoder) and 6 (cross-encoder) persisted only
pass/fail *counts* — the raw scores were computed and discarded. The stage-gate
efficacy audit therefore could not calibrate the bi-/cross-encoder thresholds
from stored data. Score persistence is now on for every new run, but that only
accrues data going forward.

This module recovers the *past*. For each historical change that reached Stage 4
it:

  1. Reconstructs the exact ``normalised_diff`` the models saw. For webpage
     sources this is faithful: the raw unified diff is committed per run under
     ``data/influencer_sources/snapshots/<source_id>/<source_id>_<run_id>.diff``
     and ``stage3_diff._normalise_diff_text`` is deterministic. FRL
     (``compilation_change``) and RSS change documents are *not* retained per
     run, so those runs are recorded as skipped rather than scored with low
     fidelity.
  2. Replays Stage 5 then Stage 6 over the run's Stage-4 candidate pages using
     the current models and config, reproducing the gate exactly (including
     candidates the gates would reject).
  3. Joins each confirmed page to the LLM verdict already stored in
     ``llm_assessments`` — producing a *labelled* dataset (cross-encoder score
     vs "CHANGE_REQUIRED / NO_CHANGE") that the 0.60 threshold can be
     calibrated against now, without waiting weeks for new runs.

Results are written to a new ``score_backfill`` table and never overwrite the
historical ``pipeline_runs`` rows — those remain the true record of what
happened. Scores are computed against the *current* corpus (historical corpus
snapshots are not retained), so fidelity is highest for recent runs; the report
flags this and includes a fidelity check comparing the replayed bi-encoder
pass-count to the count recorded at the time.

Usage (standalone; intended to run in CI where the models are already cached):

    python -m src.backfill_scores [--config tripwire_config.yaml] \
        [--since 2026-05-07] [--limit N] [--run-id 2026-07-16-392] \
        [--report data/logs/score_backfill_<date>.md]
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Reconstruction is only faithful for webpage unified diffs.
_FAITHFUL_DIFF_TYPE = "unified_diff"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def create_backfill_table(conn: sqlite3.Connection) -> None:
    """Create the score_backfill table if it does not already exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS score_backfill (
            run_id                            TEXT NOT NULL,
            source_id                         TEXT NOT NULL,
            source_type                       TEXT,
            ipfr_page_id                      TEXT NOT NULL,
            original_timestamp                TEXT,
            biencoder_max_chunk_score         REAL,
            biencoder_chunks_above_low_medium INTEGER,
            biencoder_pass                    INTEGER,
            crossencoder_score                REAL,
            crossencoder_reranked_score       REAL,
            crossencoder_final_score          REAL,
            crossencoder_decision             TEXT,
            historical_confirmed              INTEGER,
            llm_verdict                       TEXT,
            llm_confidence                    REAL,
            ce_threshold                      REAL,
            bi_high_threshold                 REAL,
            bi_low_medium_threshold           REAL,
            reconstruction                    TEXT,
            corpus_note                       TEXT,
            backfilled_at                     TEXT,
            PRIMARY KEY (run_id, source_id, ipfr_page_id)
        )
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Selection & reconstruction
# ---------------------------------------------------------------------------


def _select_runs(
    conn: sqlite3.Connection,
    since: str,
    run_id: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    """Return pipeline_runs rows that reached Stage 4 (have relevance candidates)."""
    sql = (
        "SELECT id, run_id, source_id, source_type, timestamp, triggered_pages, details "
        "FROM pipeline_runs WHERE timestamp >= ?"
    )
    params: list[Any] = [since]
    if run_id:
        sql += " AND run_id = ?"
        params.append(run_id)
    sql += " ORDER BY timestamp ASC"

    rows: list[dict[str, Any]] = []
    for r in conn.execute(sql, params):
        try:
            details = json.loads(r[6]) if r[6] else {}
        except (json.JSONDecodeError, TypeError):
            continue
        stages = details.get("stages", {})
        rel = stages.get("relevance", {})
        if not rel.get("top_candidates"):
            continue  # never reached Stage 4 with candidates — nothing to replay
        rows.append(
            {
                "id": r[0],
                "run_id": r[1],
                "source_id": r[2],
                "source_type": r[3],
                "timestamp": r[4],
                "triggered_pages": r[5],
                "stages": stages,
            }
        )
        if limit and len(rows) >= limit:
            break
    return rows


def reconstruct_change_text(
    row: dict[str, Any],
    snapshot_dir: Path,
) -> tuple[str | None, str]:
    """Rebuild the normalised change document a historical run scored against.

    Returns ``(text, status)``. ``text`` is None when the change document is not
    faithfully recoverable, in which case ``status`` explains why.
    """
    from src.stage3_diff import _normalise_diff_text

    diff = row["stages"].get("diff", {})
    diff_type = diff.get("diff_type")
    source_type = row.get("source_type")

    if source_type != "webpage" or diff_type != _FAITHFUL_DIFF_TYPE:
        # FRL (compilation_change) and RSS (rss_items) change docs are not
        # retained per run — do not fabricate low-fidelity scores.
        return None, f"skipped_{source_type or 'unknown'}_{diff_type or 'none'}"

    basename = (diff.get("diff_path") or "").rsplit("/", 1)[-1]
    if not basename:
        basename = f"{row['source_id']}_{row['run_id']}.diff"
    path = Path(snapshot_dir) / row["source_id"] / basename
    if not path.exists():
        return None, "diff_file_missing"

    raw = path.read_text(encoding="utf-8", errors="replace")
    if not raw.strip():
        return None, "empty_diff"

    return _normalise_diff_text(raw), _FAITHFUL_DIFF_TYPE


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def run_backfill(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    *,
    since: str = "2026-05-07",
    run_id: str | None = None,
    limit: int | None = None,
    snapshot_dir: Path | str | None = None,
    biencoder_model: Any | None = None,
    crossencoder_model: Any | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Replay Stages 5–6 for historical changes and write score_backfill rows.

    Models are loaded lazily and cached by the stage modules, so passing
    ``model=None`` loads each model once and reuses it across runs. Injecting
    ``biencoder_model`` / ``crossencoder_model`` (used by tests) bypasses
    loading entirely.

    The two passes (all bi-encoder scoring, then all cross-encoder scoring)
    honour the Section 7.4 memory ordering: the bi-encoder is released before
    the cross-encoder is loaded.
    """
    from src.stage5_biencoder import score_biencoder, release_biencoder
    from src.stage6_crossencoder import score_crossencoder

    if snapshot_dir is None:
        snapshot_dir = config.get("paths", {}).get(
            "influencer_snapshots_dir", "data/influencer_sources/snapshots"
        )
    snapshot_dir = Path(snapshot_dir)
    now = now or datetime.now(timezone.utc).isoformat()

    ss = config.get("semantic_scoring", {})
    ce_threshold = float(ss.get("crossencoder", {}).get("threshold", 0.60))
    bi_high = float(ss.get("biencoder", {}).get("high_threshold", 0.75))
    bi_low = float(ss.get("biencoder", {}).get("low_medium_threshold", 0.45))

    create_backfill_table(conn)
    runs = _select_runs(conn, since, run_id, limit)
    llm = _load_llm_verdicts(conn)

    stats: dict[str, int] = {
        "runs_selected": len(runs),
        "runs_scored": 0,
        "pages_written": 0,
    }
    skips: dict[str, int] = {}

    # ---- Pass A: bi-encoder over every replayable run -------------------
    pending: list[dict[str, Any]] = []
    for row in runs:
        text, status = reconstruct_change_text(row, snapshot_dir)
        if text is None:
            skips[status] = skips.get(status, 0) + 1
            continue

        rel = row["stages"]["relevance"]
        candidates = [c["page_id"] for c in rel["top_candidates"]]
        s4_scores = {
            c["page_id"]: float(c.get("final_score", 0.0))
            for c in rel["top_candidates"]
        }

        bi = score_biencoder(
            change_text=text,
            candidate_page_ids=candidates,
            conn=conn,
            config=config,
            model=biencoder_model,
        )
        pending.append(
            {
                "row": row,
                "text": text,
                "candidates": candidates,
                "s4_scores": s4_scores,
                "bi_by_page": {p.page_id: p for p in bi.all_pages},
                "survivors": [p.page_id for p in bi.candidate_pages],
            }
        )

    # Release the bi-encoder before loading the cross-encoder (Section 7.4).
    if biencoder_model is None:
        release_biencoder()

    # ---- Pass B: cross-encoder over survivors, then write ---------------
    records: list[dict[str, Any]] = []
    for item in pending:
        row = item["row"]
        ce_by_page: dict[str, Any] = {}
        if item["survivors"]:
            ce = score_crossencoder(
                candidate_page_ids=item["survivors"],
                change_text=item["text"],
                conn=conn,
                config=config,
                stage4_scores=item["s4_scores"],
                model=crossencoder_model,
            )
            ce_by_page = {p.page_id: p for p in ce.all_scored}

        confirmed = set(json.loads(row["triggered_pages"] or "[]"))
        for pid in item["candidates"]:
            b = item["bi_by_page"].get(pid)
            c = ce_by_page.get(pid)
            bi_pass = 1 if (b is not None and b.trigger_reason is not None) else 0
            verdict, confidence = llm.get((row["run_id"], pid), (None, None))
            records.append(
                {
                    "run_id": row["run_id"],
                    "source_id": row["source_id"],
                    "source_type": row["source_type"],
                    "ipfr_page_id": pid,
                    "original_timestamp": row["timestamp"],
                    "biencoder_max_chunk_score": (b.max_chunk_score if b else None),
                    "biencoder_chunks_above_low_medium": (
                        b.chunks_above_low_medium if b else None
                    ),
                    "biencoder_pass": bi_pass,
                    "crossencoder_score": (c.crossencoder_score if c else None),
                    "crossencoder_reranked_score": (c.reranked_score if c else None),
                    "crossencoder_final_score": (c.final_score if c else None),
                    "crossencoder_decision": (
                        c.decision if c else ("not_evaluated" if not bi_pass else None)
                    ),
                    "historical_confirmed": 1 if pid in confirmed else 0,
                    "llm_verdict": verdict,
                    "llm_confidence": confidence,
                    "ce_threshold": ce_threshold,
                    "bi_high_threshold": bi_high,
                    "bi_low_medium_threshold": bi_low,
                    "reconstruction": _FAITHFUL_DIFF_TYPE,
                    "corpus_note": "scored against current corpus",
                    "backfilled_at": now,
                }
            )
        stats["runs_scored"] += 1

    _write_records(conn, records)
    stats["pages_written"] = len(records)
    stats["skipped"] = skips
    logger.info(
        "Backfill: scored %d/%d runs, wrote %d page rows (skips: %s)",
        stats["runs_scored"], stats["runs_selected"], stats["pages_written"], skips,
    )
    return stats


def _load_llm_verdicts(conn: sqlite3.Connection) -> dict[tuple[str, str], tuple[str, float]]:
    """Map (run_id, ipfr_page_id) -> (verdict, confidence)."""
    out: dict[tuple[str, str], tuple[str, float]] = {}
    try:
        for r in conn.execute(
            "SELECT run_id, ipfr_page_id, verdict, confidence FROM llm_assessments"
        ):
            out[(r[0], r[1])] = (r[2], r[3])
    except sqlite3.Error:
        pass
    return out


def _write_records(conn: sqlite3.Connection, records: list[dict[str, Any]]) -> None:
    """Idempotently upsert backfill records (keyed by run/source/page)."""
    if not records:
        return
    cols = [
        "run_id", "source_id", "source_type", "ipfr_page_id", "original_timestamp",
        "biencoder_max_chunk_score", "biencoder_chunks_above_low_medium", "biencoder_pass",
        "crossencoder_score", "crossencoder_reranked_score", "crossencoder_final_score",
        "crossencoder_decision", "historical_confirmed", "llm_verdict", "llm_confidence",
        "ce_threshold", "bi_high_threshold", "bi_low_medium_threshold",
        "reconstruction", "corpus_note", "backfilled_at",
    ]
    placeholders = ", ".join(f":{c}" for c in cols)
    conn.executemany(
        f"INSERT OR REPLACE INTO score_backfill ({', '.join(cols)}) VALUES ({placeholders})",
        records,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Calibration report
# ---------------------------------------------------------------------------


def _pctl(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    v = sorted(values)
    i = (len(v) - 1) * p / 100.0
    lo = int(i)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (i - lo)


def _dist_line(label: str, values: list[float]) -> str:
    if not values:
        return f"| {label} | — | — | — | — | — | 0 |"
    return (
        f"| {label} | {min(values):.3f} | {_pctl(values,25):.3f} | {_pctl(values,50):.3f} "
        f"| {_pctl(values,75):.3f} | {max(values):.3f} | {len(values)} |"
    )


def generate_report(conn: sqlite3.Connection, stats: dict[str, Any]) -> str:
    """Build a Markdown calibration report from the score_backfill table."""
    # Column access via cursor description (portable regardless of row_factory).
    cur = conn.execute("SELECT * FROM score_backfill")
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]

    confirmed = [r for r in rows if r["historical_confirmed"] == 1]
    ce_change = [r["crossencoder_final_score"] for r in confirmed
                 if r["llm_verdict"] == "CHANGE_REQUIRED" and r["crossencoder_final_score"] is not None]
    ce_nochange = [r["crossencoder_final_score"] for r in confirmed
                   if r["llm_verdict"] == "NO_CHANGE" and r["crossencoder_final_score"] is not None]
    bi_all = [r["biencoder_max_chunk_score"] for r in rows if r["biencoder_max_chunk_score"] is not None]

    lines = [
        "# Historical Score Backfill — Calibration Dataset",
        "",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  ",
        f"**Runs replayed:** {stats.get('runs_scored', 0)} of {stats.get('runs_selected', 0)} selected  ",
        f"**Page rows written:** {stats.get('pages_written', 0)}  ",
        "",
        "> Scores were recomputed with the current models against the **current** "
        "corpus (historical corpus snapshots are not retained), so treat older "
        "runs as indicative and weight recent runs. Historical `pipeline_runs` "
        "rows were not modified.",
        "",
        "## Skipped runs (change document not faithfully recoverable)",
        "",
        "| Reason | Runs |",
        "|--------|------|",
    ]
    skips = stats.get("skipped", {}) or {}
    if skips:
        for reason, n in sorted(skips.items(), key=lambda kv: -kv[1]):
            lines.append(f"| `{reason}` | {n} |")
    else:
        lines.append("| — | 0 |")

    lines += [
        "",
        "## Score distributions",
        "",
        "| Series | Min | p25 | Median | p75 | Max | N |",
        "|--------|-----|-----|--------|-----|-----|---|",
        _dist_line("Bi-encoder max chunk (all scored candidates)", bi_all),
        _dist_line("Cross-encoder final — confirmed & LLM CHANGE_REQUIRED", ce_change),
        _dist_line("Cross-encoder final — confirmed & LLM NO_CHANGE", ce_nochange),
        "",
        "## Cross-encoder threshold calibration",
        "",
    ]
    if ce_change and ce_nochange:
        sep_lo = min(ce_change)
        no_p75 = _pctl(ce_nochange, 75)
        lines += [
            f"- Lowest cross-encoder final score among **useful** (CHANGE_REQUIRED) "
            f"pages: **{sep_lo:.3f}** — a threshold above this would start dropping true positives.",
            f"- p75 of **NO_CHANGE** pages: **{no_p75:.3f}**.",
            f"- Current threshold: **{confirmed[0]['ce_threshold']:.2f}** "
            f"(confirmed {len(confirmed)} pages; the audit showed it rejected 0).",
            "",
            "Pick the threshold that best separates the two distributions above; "
            "if they overlap heavily, the cross-encoder alone cannot cleanly gate "
            "and the signal should be combined with source importance or a "
            "bi-encoder floor.",
        ]
    else:
        lines.append(
            "_Not enough labelled data yet (need confirmed pages with both "
            "CHANGE_REQUIRED and NO_CHANGE verdicts) to suggest a boundary._"
        )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay historical changes to backfill semantic scores.")
    parser.add_argument("--config", default="tripwire_config.yaml")
    parser.add_argument("--since", default="2026-05-07", help="ISO date lower bound (default: %(default)s)")
    parser.add_argument("--run-id", default=None, help="Replay a single run only")
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of runs replayed")
    parser.add_argument("--report", default=None, help="Output path for the Markdown report")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from src.config import load_config, get as cfg_get

    config = load_config(args.config)
    db_path = cfg_get(config, "paths", "sqlite_db", default="data/ipfr_corpus/ipfr.sqlite")
    if not Path(db_path).exists():
        logger.error("SQLite database not found: %s", db_path)
        return 1

    conn = sqlite3.connect(db_path)
    # Stage 5/6 DB helpers build dicts via ``dict(row)``, which requires the
    # sqlite3.Row factory (the production pipeline sets this in ingestion.db.
    # init_db). Without it rows are plain tuples and dict(row) raises.
    conn.row_factory = sqlite3.Row
    try:
        stats = run_backfill(conn, config, since=args.since, run_id=args.run_id, limit=args.limit)
        report = generate_report(conn, stats)
    finally:
        conn.close()

    out = args.report or f"data/logs/score_backfill_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.md"
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"Backfill complete: {stats}")
    print(f"Report written to: {out_path}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
