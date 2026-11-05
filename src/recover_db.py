"""
src/recover_db.py

Merge run history from other copies of ``ipfr.sqlite`` into a target database.

Background: from 22 September 2026 the database exceeded GitHub's 100 MB file
limit, so every push that included it was rejected. Each daily run therefore
started from the last database that had been pushed and its new rows were
lost. They were not lost everywhere: after every pipeline run the workflow
uploaded that runner's complete database to a GitHub Release
(``data-YYYYMMDD-HHMMSS``). This tool folds those copies back together.

It is also how a database parked under ``conflicts/`` by ``src.r2_storage``
(after a refused push) is merged back in.

What is merged
--------------
Only the append-only history tables. A row is copied when no row with the
same natural key already exists in the target, so re-running the merge, or
merging overlapping copies, never duplicates anything:

    pipeline_runs      (run_id, source_id, timestamp)
    llm_assessments    (run_id, ipfr_page_id, generated_at)
    ingestion_runs     (run_id, page_id, timestamp)
    deferred_triggers  (run_id, source_id, ipfr_page_id, created_at)
    score_backfill     (run_id, source_id, ipfr_page_id)

Surrogate ``id`` columns are reassigned by the target. Columns missing from
an older copy are left NULL. Corpus tables (pages, chunks, entities, ...) are
never touched: the target's corpus wins and the next ingestion run refreshes
it from the live site.

Deferred triggers are restored with their original ``processed`` flag. The
pipeline already discards pending triggers older than
``pipeline.deferred_trigger_max_age_days`` at the start of a run, so restoring
old ones cannot cause stale alerts; recent ones are retried as designed.

Usage:
    python -m src.recover_db --into data/ipfr_corpus/ipfr.sqlite \\
        --from release-a.sqlite --from release-b.sqlite \\
        [--label data-20261104-085118] [--summary recovery.md]
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# table -> natural key columns
MERGE_TABLES: dict[str, tuple[str, ...]] = {
    "pipeline_runs": ("run_id", "source_id", "timestamp"),
    "llm_assessments": ("run_id", "ipfr_page_id", "generated_at"),
    "ingestion_runs": ("run_id", "page_id", "timestamp"),
    "deferred_triggers": ("run_id", "source_id", "ipfr_page_id", "created_at"),
    "score_backfill": ("run_id", "source_id", "ipfr_page_id"),
}

_SURROGATE = "id"


def _columns(conn: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA "{schema}".table_info("{table}")')]


def _required_columns(conn: sqlite3.Connection, table: str) -> list[tuple[str, str, int, object]]:
    """(name, type, notnull, default) for every column of main.<table>."""
    return [(r[1], r[2] or "", r[3], r[4]) for r in conn.execute(f'PRAGMA main.table_info("{table}")')]


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def merge_database(target: sqlite3.Connection, source_path: str | Path) -> dict[str, int]:
    """Copy missing history rows from *source_path* into *target*.

    Returns the number of rows inserted per table. The source is only ever
    read from.
    """
    target.execute("ATTACH DATABASE ? AS src", (str(Path(source_path).resolve()),))
    inserted: dict[str, int] = {}
    try:
        for table, key in MERGE_TABLES.items():
            dst_cols = _columns(target, "main", table)
            src_cols = _columns(target, "src", table)
            if not dst_cols or not src_cols:
                continue
            if any(k not in dst_cols or k not in src_cols for k in key):
                logger.warning("Skipping %s: key columns %s missing in one copy.", table, key)
                continue

            has_surrogate = _SURROGATE in dst_cols and _SURROGATE in src_cols
            cols = [c for c in dst_cols if c in src_cols and c != _SURROGATE]
            select = ["s." + _q(c) for c in cols]
            # An older copy may lack a column the target declares NOT NULL
            # without a default; fill it neutrally rather than fail the merge.
            for name, ctype, notnull, default in _required_columns(target, table):
                if name not in src_cols and name != _SURROGATE and default is None and notnull:
                    cols.append(name)
                    select.append("0" if any(t in ctype.upper() for t in ("INT", "REAL", "NUM")) else "''")
            col_sql = ", ".join(_q(c) for c in cols)
            match = " AND ".join(f"m.{_q(k)} IS s.{_q(k)}" for k in key)
            order = f"s.{_q(_SURROGATE)}" if has_surrogate else "s.rowid"

            # Helper index so the NOT EXISTS probe stays fast on large tables.
            idx = f"_recover_{table}_key"
            target.execute(
                f"CREATE INDEX IF NOT EXISTS main.{_q(idx)} ON {_q(table)} ({', '.join(_q(k) for k in key)})"
            )
            before = target.total_changes
            target.execute(
                f"INSERT INTO main.{_q(table)} ({col_sql}) "
                f"SELECT {', '.join(select)} FROM src.{_q(table)} s "
                f"WHERE NOT EXISTS (SELECT 1 FROM main.{_q(table)} m WHERE {match}) "
                f"ORDER BY {order}"
            )
            inserted[table] = target.total_changes - before
            target.execute(f"DROP INDEX IF EXISTS main.{_q(idx)}")
        target.commit()
    except Exception:
        target.rollback()
        raise
    finally:
        target.execute("DETACH DATABASE src")
    return inserted


def recovered_run_ids(target: sqlite3.Connection, source_path: str | Path) -> list[str]:
    """Run IDs present in *source_path*'s pipeline_runs but not in *target* (preview)."""
    target.execute("ATTACH DATABASE ? AS src", (str(Path(source_path).resolve()),))
    try:
        if not _columns(target, "src", "pipeline_runs"):
            return []
        rows = target.execute(
            "SELECT DISTINCT run_id FROM src.pipeline_runs "
            "WHERE run_id NOT IN (SELECT DISTINCT run_id FROM main.pipeline_runs WHERE run_id IS NOT NULL) "
            "ORDER BY run_id"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        target.execute("DETACH DATABASE src")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge run history from other ipfr.sqlite copies.")
    parser.add_argument("--into", required=True, help="Target database (modified in place)")
    parser.add_argument("--from", dest="sources", action="append", required=True,
                        help="Source database; repeat for several, oldest first")
    parser.add_argument("--label", default="", help="Label for the summary line (e.g. release tag)")
    parser.add_argument("--summary", default=None, help="Append a markdown table row per source here")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    target_path = Path(args.into)
    if not target_path.exists():
        logger.error("Target database not found: %s", target_path)
        return 1

    conn = sqlite3.connect(str(target_path))
    try:
        for src in args.sources:
            label = args.label or Path(src).name
            try:
                new_runs = recovered_run_ids(conn, src)
                counts = merge_database(conn, src)
            except sqlite3.DatabaseError as exc:
                logger.error("Could not merge %s: %s", src, exc)
                if args.summary:
                    with open(args.summary, "a", encoding="utf-8") as fh:
                        fh.write(f"| {label} | error: {exc} | | | | | |\n")
                continue
            logger.info("%s: +%s (new runs: %s)", label, counts, ", ".join(new_runs) or "none")
            if args.summary:
                with open(args.summary, "a", encoding="utf-8") as fh:
                    fh.write(
                        f"| {label} | {', '.join(new_runs) or '-'} | "
                        + " | ".join(str(counts.get(t, 0)) for t in MERGE_TABLES)
                        + " |\n"
                    )
    finally:
        conn.close()
    return 0


def summary_header() -> str:
    """Markdown header matching the rows written by ``--summary``."""
    cols = ["Source", "Recovered run IDs", *MERGE_TABLES]
    return "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"


if __name__ == "__main__":
    sys.exit(main())
