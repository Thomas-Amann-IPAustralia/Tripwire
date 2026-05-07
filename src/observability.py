"""
src/observability.py

Weekly observability summary report (Section 6.7, Phase 5 task 5.2).

Queries the last 30 days of ``pipeline_runs`` data from SQLite and produces
a Markdown report containing:

  - **Reliability table**: per source — total runs, successful runs, error
    count, last error date, current streak (consecutive successes or failures).
  - **Score distributions**: for each scoring stage — min, p25, median, p75,
    max across all runs in the window.
  - **Alert volume**: number of flagged pages per week over the reporting
    window.
  - **Feedback summary**: proportion of alerts rated "useful" vs non-useful
    categories (if feedback.jsonl exists).

Usage (standalone, run weekly):

    python -m src.observability [--config tripwire_config.yaml] [--days 30] [--output report.md]

The report is written to ``data/logs/observability_<date>.md`` by default.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCORE_FIELDS = {
    "stage4_final_score": "Stage 4 — Relevance (fused RRF)",
    "biencoder_max_chunk_score": "Stage 5 — Bi-encoder (max chunk cosine)",
    "crossencoder_final_score": "Stage 6 — Cross-encoder (reranked)",
}

# Feedback categories (must match stage9_notification.py).
_FEEDBACK_CATEGORIES = ["useful", "not_significant", "wrong_amendment", "wrong_page"]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_report(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    days: int = 30,
    feedback_path: Path | None = None,
) -> str:
    """Query the database and return a Markdown observability report.

    Parameters
    ----------
    conn:
        Open SQLite connection pointing to ``ipfr.sqlite``.
    config:
        Loaded ``tripwire_config.yaml`` dict (used for output path config).
    days:
        How many days back to include in the report window (default: 30).
    feedback_path:
        Path to ``data/logs/feedback.jsonl``. If ``None``, defaults to the
        path derived from config.

    Returns
    -------
    str
        Full Markdown report text.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_str = cutoff.isoformat()

    rows = _fetch_runs(conn, cutoff_str)
    if not rows:
        return (
            f"# Tripwire Observability Report\n\n"
            f"_No pipeline runs recorded in the last {days} days._\n"
        )

    sections: list[str] = [
        f"# Tripwire Observability Report",
        f"",
        f"**Report window:** last {days} days  ",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  ",
        f"**Runs in window:** {_count_distinct_run_ids(rows)}  ",
        f"**Sources monitored:** {len({r['source_id'] for r in rows})}",
        f"",
    ]

    sections += _section_reliability(rows)
    sections += _section_score_distributions(rows)
    sections += _section_alert_volume(rows)

    if feedback_path is None:
        feedback_path = _default_feedback_path(config)
    sections += _section_feedback(feedback_path, cutoff_str)

    return "\n".join(sections)


def write_report(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    output_path: Path | None = None,
    days: int = 30,
    feedback_path: Path | None = None,
) -> Path:
    """Generate and write the report to disk.

    Returns the path of the written file.
    """
    report_text = generate_report(conn, config, days=days, feedback_path=feedback_path)

    if output_path is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        output_path = Path("data/logs") / f"observability_{date_str}.md"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_text, encoding="utf-8")
    logger.info("Observability report written to %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Database queries
# ---------------------------------------------------------------------------


def _fetch_runs(
    conn: sqlite3.Connection,
    cutoff_str: str,
) -> list[dict[str, Any]]:
    """Return pipeline_runs rows newer than cutoff_str."""
    try:
        rows = conn.execute(
            """
            SELECT run_id, source_id, source_url, timestamp,
                   stage_reached, outcome, error_type, error_message,
                   triggered_pages, details
            FROM pipeline_runs
            WHERE timestamp >= ?
            ORDER BY timestamp ASC
            """,
            (cutoff_str,),
        ).fetchall()
    except sqlite3.Error as exc:
        logger.error("Failed to query pipeline_runs: %s", exc)
        return []

    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            details = json.loads(row[9]) if row[9] else {}
        except json.JSONDecodeError:
            details = {}
        result.append({
            "run_id": row[0],
            "source_id": row[1],
            "source_url": row[2],
            "timestamp": row[3],
            "stage_reached": row[4],
            "outcome": row[5],
            "error_type": row[6],
            "error_message": row[7],
            "triggered_pages": row[8],
            "details": details,
        })
    return result


def _count_distinct_run_ids(rows: list[dict]) -> int:
    return len({r["run_id"] for r in rows})


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _section_reliability(rows: list[dict]) -> list[str]:
    """Build the per-source reliability table."""
    source_stats: dict[str, dict] = {}

    for row in rows:
        sid = row["source_id"]
        if sid not in source_stats:
            source_stats[sid] = {
                "total": 0,
                "success": 0,
                "error": 0,
                "last_error_date": None,
                "outcomes": [],
            }
        stats = source_stats[sid]
        stats["total"] += 1
        if row["outcome"] == "error":
            stats["error"] += 1
            stats["last_error_date"] = row["timestamp"][:10]
        else:
            stats["success"] += 1
        stats["outcomes"].append(row["outcome"])  # chronological

    lines = [
        "## Source Reliability",
        "",
        "| Source | Total Runs | Successes | Errors | Last Error | Current Streak |",
        "|--------|-----------|-----------|--------|------------|----------------|",
    ]

    for sid, stats in sorted(source_stats.items()):
        streak = _compute_streak(stats["outcomes"])
        last_err = stats["last_error_date"] or "—"
        lines.append(
            f"| `{sid}` | {stats['total']} | {stats['success']} "
            f"| {stats['error']} | {last_err} | {streak} |"
        )

    lines.append("")
    return lines


def _compute_streak(outcomes: list[str]) -> str:
    """Return a human-readable current streak from a chronological outcomes list."""
    if not outcomes:
        return "—"

    last = outcomes[-1]
    streak_type = "success" if last != "error" else "failure"
    count = 0
    for outcome in reversed(outcomes):
        if (outcome != "error") == (streak_type == "success"):
            count += 1
        else:
            break

    return f"{count}× {streak_type}"


def _section_score_distributions(rows: list[dict]) -> list[str]:
    """Build the score distributions table for Stages 4–6."""
    # Collect score values from details.stages sub-dicts.
    score_buckets: dict[str, list[float]] = {k: [] for k in _SCORE_FIELDS}

    for row in rows:
        details = row.get("details", {})
        stages = details.get("stages", {})

        # Stage 4 relevance
        rel = stages.get("relevance", {})
        for cand in rel.get("top_candidates", []):
            score = cand.get("final_score")
            if score is not None:
                score_buckets["stage4_final_score"].append(float(score))

        # Stage 5/6 scores stored on triggered_pages (flat field)
        triggered_raw = row.get("triggered_pages")
        if triggered_raw:
            try:
                pages = json.loads(triggered_raw) if isinstance(triggered_raw, str) else triggered_raw
                if isinstance(pages, list):
                    for page in pages:
                        if isinstance(page, dict):
                            for field_name in ("biencoder_max_chunk_score", "crossencoder_final_score"):
                                val = page.get(field_name)
                                if val is not None:
                                    score_buckets[field_name].append(float(val))
            except (json.JSONDecodeError, TypeError):
                pass

    lines: list[str] = [
        "## Score Distributions",
        "",
        "Aggregated across all runs in the reporting window. "
        "Use these to assess whether thresholds are set appropriately.",
        "",
        "| Stage | Min | p25 | Median | p75 | Max | N |",
        "|-------|-----|-----|--------|-----|-----|---|",
    ]

    for field_name, label in _SCORE_FIELDS.items():
        values = sorted(score_buckets[field_name])
        if not values:
            lines.append(f"| {label} | — | — | — | — | — | 0 |")
            continue
        n = len(values)
        lines.append(
            f"| {label} "
            f"| {_pct(values, 0):.3f} "
            f"| {_pct(values, 25):.3f} "
            f"| {_pct(values, 50):.3f} "
            f"| {_pct(values, 75):.3f} "
            f"| {_pct(values, 100):.3f} "
            f"| {n} |"
        )

    lines.append("")
    return lines


def _section_alert_volume(rows: list[dict]) -> list[str]:
    """Build the weekly alert volume summary."""
    # Count distinct run_ids that had triggered_pages (i.e. produced alerts).
    week_alerts: dict[str, int] = {}   # ISO week → alert count

    for row in rows:
        triggered_raw = row.get("triggered_pages")
        if not triggered_raw:
            continue
        try:
            dt = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        iso_week = dt.strftime("%G-W%V")  # ISO week like "2026-W14"
        week_alerts[iso_week] = week_alerts.get(iso_week, 0) + 1

    lines = [
        "## Alert Volume",
        "",
        "Number of source→page trigger events per ISO week.",
        "",
        "| Week | Trigger Events |",
        "|------|---------------|",
    ]
    if not week_alerts:
        lines.append("| — | 0 |")
    else:
        for week in sorted(week_alerts):
            lines.append(f"| {week} | {week_alerts[week]} |")

    lines.append("")
    return lines


def _section_feedback(
    feedback_path: Path | None,
    cutoff_str: str,
) -> list[str]:
    """Build the feedback summary section."""
    lines = ["## Feedback Summary", ""]

    if feedback_path is None or not feedback_path.exists():
        lines += [
            "_No feedback data found. "
            "Feedback is written to `data/logs/feedback.jsonl` by `src/feedback_ingestion.py`._",
            "",
        ]
        return lines

    records = _load_feedback(feedback_path, cutoff_str)
    if not records:
        lines += ["_No feedback records in the reporting window._", ""]
        return lines

    totals: dict[str, int] = {cat: 0 for cat in _FEEDBACK_CATEGORIES}
    for rec in records:
        cat = rec.get("category", "")
        if cat in totals:
            totals[cat] += 1

    total = sum(totals.values())
    lines += [
        f"**Total feedback records in window:** {total}",
        "",
        "| Category | Count | % of Total |",
        "|----------|-------|------------|",
    ]
    for cat in _FEEDBACK_CATEGORIES:
        count = totals[cat]
        pct = count / total * 100 if total > 0 else 0.0
        lines.append(f"| `{cat}` | {count} | {pct:.1f}% |")

    precision = totals.get("useful", 0) / total * 100 if total > 0 else 0.0
    lines += [
        "",
        f"**System precision (useful / total feedback): {precision:.1f}%**",
        "> Precision target: aim for ≥ 60% once the system has been running for 4+ weeks.",
        "",
    ]
    return lines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pct(sorted_values: list[float], percentile: float) -> float:
    """Return the given percentile of a sorted list using linear interpolation."""
    if not sorted_values:
        return 0.0
    if percentile <= 0:
        return sorted_values[0]
    if percentile >= 100:
        return sorted_values[-1]
    n = len(sorted_values)
    idx = (percentile / 100.0) * (n - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    frac = idx - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def _load_feedback(path: Path, cutoff_str: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("timestamp", "")
            if ts >= cutoff_str[:10]:
                records.append(rec)
    except OSError as exc:
        logger.warning("Could not read feedback file %s: %s", path, exc)
    return records


def _default_feedback_path(config: dict[str, Any]) -> Path:
    log_dir = config.get("paths", {}).get("logs_dir", "data/logs")
    return Path(log_dir) / "feedback.jsonl"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate the weekly Tripwire observability report."
    )
    parser.add_argument(
        "--config",
        default="tripwire_config.yaml",
        help="Path to tripwire_config.yaml (default: %(default)s)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days to include in the reporting window (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path for the Markdown report (default: data/logs/observability_<date>.md)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from src.config import load_config, get as cfg_get

    config = load_config(args.config)
    config_dir = Path(args.config).parent if Path(args.config).is_absolute() else Path.cwd()
    db_path = config_dir / cfg_get(
        config, "paths", "sqlite_db", default="data/ipfr_corpus/ipfr.sqlite"
    )

    if not db_path.exists():
        logger.error("SQLite database not found: %s", db_path)
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        output_path = Path(args.output) if args.output else None
        written = write_report(conn, config, output_path=output_path, days=args.days)
        print(f"Report written to: {written}")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
