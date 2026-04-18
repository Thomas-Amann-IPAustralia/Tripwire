"""
ingestion/ingest.py

IPFR ingestion pipeline orchestration (Section 4 of the system plan).

Steps:
  1. Load sitemap CSV (or build from scratch if missing).
  2. For each page, check whether it needs re-ingestion (last_modified changed
     or never ingested).
  3. Scrape and normalise the page content.
  4. Run enrichment (chunking, embeddings, NER, YAKE, sections).
  5. Upsert all data into the SQLite database.
  6. Compute / refresh quasi-graph edges.
  7. Log the run and write a GitHub Actions Job Summary.

Run with:
  python -m ingestion.ingest [--config path/to/tripwire_config.yaml]
                             [--db path/to/ipfr.sqlite]
                             [--force-all]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure repo root is on sys.path when run as __main__.
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import load_config, get, snapshot_config
from ingestion import db, sitemap as sitemap_mod, scrape_ipfr, enrich, graph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_ingestion(
    config: dict[str, Any],
    *,
    db_path: str | Path | None = None,
    force_all: bool = False,
) -> dict[str, Any]:
    """Run a full IPFR ingestion cycle.

    Parameters
    ----------
    config:
        Validated configuration dict from src.config.load_config.
    db_path:
        Path to the SQLite file.  Defaults to the value in config.
    force_all:
        If True, re-ingest all pages regardless of last_modified date.

    Returns
    -------
    dict
        Summary statistics for the run: total pages, ingested, skipped, errors.
    """
    run_id = _make_run_id()
    run_start = time.monotonic()

    # Resolve paths.
    _db_path = Path(db_path or get(config, "paths", "sqlite_db", default="data/ipfr_corpus/ipfr.sqlite"))
    sitemap_csv = Path(get(config, "paths", "sitemap_csv", default="data/ipfr_corpus/sitemap.csv"))
    snapshots_dir = Path(get(config, "paths", "ipfr_snapshots_dir", default="data/ipfr_corpus/snapshots"))
    wal_mode = get(config, "storage", "sqlite_wal_mode", default=True)

    logger.info("Ingestion run %s starting. DB: %s", run_id, _db_path)

    # Open database.
    conn = db.init_db(_db_path, wal_mode=wal_mode)

    import requests
    session = requests.Session()
    session.headers.update({"User-Agent": "Tripwire/1.0 (IPFR ingestion)"})

    # Load sitemap; bootstrap from XML sitemap on first run if CSV is absent/empty.
    existing_rows = sitemap_mod.load_sitemap(sitemap_csv)
    logger.info("Sitemap: %d existing rows", len(existing_rows))
    if not existing_rows:
        sitemap_url = get(config, "ingestion", "sitemap_url", default=None)
        if sitemap_url:
            logger.info("No existing sitemap — bootstrapping from %s", sitemap_url)
            try:
                resp = session.get(sitemap_url, timeout=30)
                resp.raise_for_status()
                urls = sitemap_mod.parse_sitemap_xml(resp.text)
                existing_rows = sitemap_mod.build_sitemap_from_urls(
                    urls, [], snapshots_dir
                )
                logger.info("Bootstrap discovered %d URLs", len(existing_rows))
            except Exception as exc:
                logger.error("Sitemap bootstrap failed: %s", exc)
        else:
            logger.warning("ingestion.sitemap_url not configured; cannot bootstrap sitemap")

    stats = {
        "run_id": run_id,
        "total": len(existing_rows),
        "ingested": 0,
        "skipped": 0,
        "errors": 0,
        "pages": [],
    }

    updated_rows: list[dict[str, str]] = []

    for row in existing_rows:
        page_id = row.get("page_id", "")
        url = row.get("url", "")
        if not url:
            continue

        page_result = _process_page(
            row=row,
            conn=conn,
            session=session,
            config=config,
            snapshots_dir=snapshots_dir,
            force=force_all,
        )

        updated_rows.append(page_result["updated_row"])

        if page_result["outcome"] == "ingested":
            stats["ingested"] += 1
        elif page_result["outcome"] == "skipped":
            stats["skipped"] += 1
        else:
            stats["errors"] += 1

        stats["pages"].append(
            {"page_id": page_id, "outcome": page_result["outcome"],
             "error": page_result.get("error")}
        )

    # Save updated sitemap.
    sitemap_mod.save_sitemap(updated_rows, sitemap_csv)

    # Recompute graph edges after ingestion.
    if stats["ingested"] > 0:
        logger.info("Recomputing quasi-graph edges (%d pages ingested).", stats["ingested"])
        try:
            graph.rebuild_graph(conn, config)
        except Exception as exc:
            logger.error("Graph rebuild failed: %s", exc)

    conn.commit()
    conn.close()

    stats["duration_seconds"] = round(time.monotonic() - run_start, 2)
    logger.info(
        "Ingestion run %s complete. Ingested=%d, Skipped=%d, Errors=%d in %.1f s",
        run_id,
        stats["ingested"],
        stats["skipped"],
        stats["errors"],
        stats["duration_seconds"],
    )

    _write_job_summary(stats)
    return stats


# ---------------------------------------------------------------------------
# Per-page processing
# ---------------------------------------------------------------------------


def _process_page(
    row: dict[str, str],
    conn: Any,
    session: Any,
    config: dict[str, Any],
    snapshots_dir: Path,
    force: bool,
) -> dict[str, Any]:
    page_id = row.get("page_id", "")
    url = row.get("url", "")
    today = sitemap_mod.current_utc_date()

    # Check whether re-ingestion is needed.
    stored_hash = db.get_page_version_hash(conn, page_id) if page_id else None
    if not force and not _needs_ingestion(row, stored_hash):
        return {
            "updated_row": sitemap_mod.update_row(row, last_checked=today),
            "outcome": "skipped",
        }

    try:
        # Scrape and normalise.
        plain_text, sections = scrape_ipfr.scrape_page(url, session)
        version_hash = scrape_ipfr.compute_version_hash(plain_text)

        # Save snapshot to disk.
        snapshot_path = Path(row.get("snapshot_path", "") or
                             str(snapshots_dir / f"{page_id or 'unknown'}.md"))
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(plain_text, encoding="utf-8")

        # Enrich.
        enriched = enrich.enrich_page(
            page_id=page_id,
            content=plain_text,
            sections=sections,
            config=config,
        )

        # Upsert page record.
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        db.upsert_page(conn, {
            "page_id": page_id,
            "url": url,
            "title": row.get("title", ""),
            "content": plain_text,
            "version_hash": version_hash,
            "last_modified": row.get("last_modified"),
            "last_checked": today,
            "last_ingested": today,
            "doc_embedding": enriched.get("doc_embedding"),
        })

        # Upsert enrichment data.
        db.replace_chunks(conn, page_id, enriched["chunks"])
        db.replace_entities(conn, page_id, enriched["entities"])
        db.replace_keyphrases(conn, page_id, enriched["keyphrases"])
        db.replace_sections(conn, page_id, enriched["sections"])

        updated_row = sitemap_mod.update_row(
            row, last_checked=today,
            last_modified=row.get("last_modified", ""),
        )
        return {"updated_row": updated_row, "outcome": "ingested"}

    except Exception as exc:
        logger.error("Failed to ingest page %s (%s): %s", page_id, url, exc)
        return {
            "updated_row": sitemap_mod.update_row(row, last_checked=today),
            "outcome": "error",
            "error": str(exc),
        }


def _needs_ingestion(row: dict[str, str], stored_hash: str | None) -> bool:
    """Return True if the page requires re-ingestion."""
    # Never ingested before.
    if not row.get("last_ingested"):
        return True
    # stored_hash missing means it was never committed to the DB.
    if stored_hash is None:
        return True
    # last_modified date changed since last ingestion.
    last_mod = row.get("last_modified", "")
    last_ingested = row.get("last_ingested", "")
    if last_mod and last_ingested and last_mod > last_ingested:
        return True
    return False


# ---------------------------------------------------------------------------
# GitHub Actions Job Summary (Section 8.2)
# ---------------------------------------------------------------------------


def _write_job_summary(stats: dict[str, Any]) -> None:
    """Write a markdown summary to $GITHUB_STEP_SUMMARY if available."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    errors = [p for p in stats.get("pages", []) if p["outcome"] == "error"]
    lines = [
        "## IPFR Ingestion Run Summary",
        "",
        f"**Run ID:** `{stats['run_id']}`",
        f"**Duration:** {stats.get('duration_seconds', 0):.1f} s",
        "",
        "| Metric | Count |",
        "|---|---|",
        f"| Total pages | {stats['total']} |",
        f"| Ingested | {stats['ingested']} |",
        f"| Skipped (no change) | {stats['skipped']} |",
        f"| Errors | {stats['errors']} |",
    ]
    if errors:
        lines += [
            "",
            "### Errors",
            "",
        ]
        for e in errors:
            lines.append(f"- **{e['page_id']}**: {e.get('error', 'unknown error')}")

    with open(summary_path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Run ID
# ---------------------------------------------------------------------------


def _make_run_id() -> str:
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    suffix = uuid.uuid4().hex[:6]
    return f"{today}-{suffix}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the IPFR ingestion pipeline.")
    p.add_argument("--config", default=None, help="Path to tripwire_config.yaml")
    p.add_argument("--db", default=None, help="Path to ipfr.sqlite")
    p.add_argument("--force-all", action="store_true",
                   help="Re-ingest all pages regardless of change detection.")
    return p.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = _parse_args()
    cfg = load_config(args.config)
    result = run_ingestion(cfg, db_path=args.db, force_all=args.force_all)
    print(json.dumps(result, indent=2))
