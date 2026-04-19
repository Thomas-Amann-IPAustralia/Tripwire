"""
ingestion/ingest.py

IPFR ingestion pipeline orchestration (Section 4 of the system plan).

The ingestion cycle runs in phases so that corpus-wide quality improvements
(boilerplate detection, duplicate marking, keyphrase IDF filtering) can see
the full set of newly-scraped pages before they're committed as retrieval
candidates:

  Phase 0 — load sitemap (bootstrap if empty).
  Phase 1 — scrape + normalise every page that needs re-ingestion.
            Persist snapshots to disk and remember raw plain text in memory.
  Phase 2 — detect boilerplate across the scraped batch merged with the
            existing DB corpus; build a dynamic block set.
  Phase 3 — strip boilerplate, enrich (chunk/embed/NER/YAKE), upsert, and
            log one audit row per page.  Stub pages are flagged and skipped
            past enrichment.
  Phase 4 — mark exact + near-duplicate pages via cosine similarity.
  Phase 5 — drop keyphrases that appear on too large a fraction of the active
            corpus (cross-document IDF filter).
  Phase 6 — rebuild the quasi-graph (stubs and duplicates excluded).

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

from src.config import load_config, get  # noqa: E402
from ingestion import db, dedup, enrich, graph, scrape_ipfr  # noqa: E402
from ingestion import sitemap as sitemap_mod  # noqa: E402

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
        Summary statistics for the run: total pages, ingested, skipped, errors,
        stubs, duplicates.
    """
    run_id = _make_run_id()
    run_start = time.monotonic()

    _db_path = Path(db_path or get(config, "paths", "sqlite_db", default="data/ipfr_corpus/ipfr.sqlite"))
    sitemap_csv = Path(get(config, "paths", "sitemap_csv", default="data/ipfr_corpus/sitemap.csv"))
    snapshots_dir = Path(get(config, "paths", "ipfr_snapshots_dir", default="data/ipfr_corpus/snapshots"))
    wal_mode = get(config, "storage", "sqlite_wal_mode", default=True)

    ingestion_cfg = get(config, "ingestion", default={}) or {}
    boilerplate_cfg = ingestion_cfg.get("boilerplate", {}) or {}
    stub_cfg = ingestion_cfg.get("stub_detection", {}) or {}
    dedup_cfg = ingestion_cfg.get("dedup", {}) or {}
    keyphrase_idf_cfg = ingestion_cfg.get("keyphrase_idf", {}) or {}

    logger.info("Ingestion run %s starting. DB: %s", run_id, _db_path)

    conn = db.init_db(_db_path, wal_mode=wal_mode)

    import requests
    session = requests.Session()
    session.headers.update({"User-Agent": sitemap_mod.BROWSER_USER_AGENT})

    existing_rows = _load_or_bootstrap_sitemap(
        sitemap_csv, snapshots_dir, session, config,
    )

    force_selenium = get(config, "ingestion", "force_selenium", default=False)

    stats: dict[str, Any] = {
        "run_id": run_id,
        "total": len(existing_rows),
        "ingested": 0,
        "skipped": 0,
        "errors": 0,
        "stubs": 0,
        "duplicates": {"exact": 0, "near": 0, "reset": 0},
        "boilerplate_lines_detected": 0,
        "keyphrases_dropped": 0,
        "pages": [],
    }
    updated_rows: list[dict[str, str]] = []

    # --- Phase 1: scrape all due pages into memory ---
    scrape_results: list[dict[str, Any]] = []
    for row in existing_rows:
        page_id = row.get("page_id", "")
        url = row.get("url", "")
        if not url:
            continue
        result = _scrape_one(
            row=row,
            conn=conn,
            session=session,
            snapshots_dir=snapshots_dir,
            force=force_all,
            force_selenium=force_selenium,
            run_id=run_id,
        )
        scrape_results.append(result)

    # --- Phase 2: detect boilerplate across the new batch + existing corpus ---
    frequent_lines = _detect_boilerplate(conn, scrape_results, boilerplate_cfg)
    stats["boilerplate_lines_detected"] = len(frequent_lines)
    if frequent_lines:
        logger.info("Detected %d repeating boilerplate lines.", len(frequent_lines))

    blocklist = boilerplate_cfg.get("blocklist", []) or []

    # --- Phase 3: strip boilerplate, enrich, upsert, and audit-log each page ---
    for result in scrape_results:
        processed = _enrich_and_persist(
            scrape_result=result,
            conn=conn,
            config=config,
            blocklist=blocklist,
            frequent_lines=frequent_lines,
            stub_cfg=stub_cfg,
            run_id=run_id,
        )

        updated_rows.append(processed["updated_row"])
        outcome = processed["outcome"]
        if outcome == "ingested":
            stats["ingested"] += 1
        elif outcome == "skipped":
            stats["skipped"] += 1
        elif outcome == "stub":
            stats["stubs"] += 1
        else:
            stats["errors"] += 1
        stats["pages"].append(
            {"page_id": processed.get("page_id"), "outcome": outcome,
             "error": processed.get("error")}
        )

    sitemap_mod.save_sitemap(updated_rows, sitemap_csv)

    # --- Phase 4: duplicate detection ---
    if dedup_cfg.get("enabled", True):
        threshold = float(dedup_cfg.get("near_duplicate_threshold", 0.98))
        stats["duplicates"] = dedup.mark_duplicates(
            conn, near_duplicate_threshold=threshold,
        )

    # --- Phase 5: keyphrase IDF filter ---
    if keyphrase_idf_cfg.get("enabled", True):
        stats["keyphrases_dropped"] = dedup.filter_global_keyphrases(
            conn,
            df_threshold=float(keyphrase_idf_cfg.get("df_threshold", 0.7)),
            min_pages=int(keyphrase_idf_cfg.get("min_pages", 5)),
        )

    # --- Phase 6: rebuild graph (excludes stubs / duplicates) ---
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
        "Ingestion run %s complete. Ingested=%d Skipped=%d Stubs=%d Errors=%d "
        "Duplicates(exact=%d near=%d) Keyphrases dropped=%d in %.1f s",
        run_id,
        stats["ingested"], stats["skipped"], stats["stubs"], stats["errors"],
        stats["duplicates"]["exact"], stats["duplicates"]["near"],
        stats["keyphrases_dropped"], stats["duration_seconds"],
    )

    _write_job_summary(stats)
    return stats


# ---------------------------------------------------------------------------
# Sitemap loading / bootstrap
# ---------------------------------------------------------------------------


def _load_or_bootstrap_sitemap(
    sitemap_csv: Path,
    snapshots_dir: Path,
    session: Any,
    config: dict[str, Any],
) -> list[dict[str, str]]:
    existing_rows = sitemap_mod.load_sitemap(sitemap_csv)
    logger.info("Sitemap: %d existing rows", len(existing_rows))
    if existing_rows:
        return existing_rows

    sitemap_url = get(config, "ingestion", "sitemap_url", default=None)
    if not sitemap_url:
        logger.warning("ingestion.sitemap_url not configured; cannot bootstrap sitemap")
        return []

    logger.info("No existing sitemap — bootstrapping from %s", sitemap_url)
    try:
        xml_text = sitemap_mod.fetch_sitemap_xml(sitemap_url, session)
        urls = sitemap_mod.parse_sitemap_xml(xml_text)
        existing_rows = sitemap_mod.build_sitemap_from_urls(urls, [], snapshots_dir)
        logger.info("Bootstrap discovered %d URLs", len(existing_rows))
        sitemap_mod.save_sitemap(existing_rows, sitemap_csv)
    except Exception as exc:
        logger.error("Sitemap bootstrap failed: %s", exc)
        existing_rows = []
    return existing_rows


# ---------------------------------------------------------------------------
# Phase 1: scrape
# ---------------------------------------------------------------------------


def _scrape_one(
    row: dict[str, str],
    conn: Any,
    session: Any,
    snapshots_dir: Path,
    force: bool,
    force_selenium: bool,
    run_id: str,
) -> dict[str, Any]:
    """Scrape a single page (or record a skip/error) and return an in-memory result."""
    page_id = row.get("page_id", "")
    url = row.get("url", "")
    today = sitemap_mod.current_utc_date()
    start = time.monotonic()

    stored_hash = db.get_page_version_hash(conn, page_id) if page_id else None
    if not force and not _needs_ingestion(row, stored_hash):
        return {
            "row": row,
            "page_id": page_id,
            "url": url,
            "today": today,
            "raw_text": None,
            "sections": [],
            "title": "",
            "outcome": "skipped",
            "error": None,
            "started_at": start,
        }

    try:
        plain_text, sections, scraped_title = scrape_ipfr.scrape_page(
            url, session, force_selenium=force_selenium,
        )
        snapshot_path = Path(row.get("snapshot_path", "") or
                             str(snapshots_dir / f"{page_id or 'unknown'}.md"))
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(plain_text, encoding="utf-8")

        return {
            "row": row,
            "page_id": page_id,
            "url": url,
            "today": today,
            "raw_text": plain_text,
            "sections": sections,
            "title": scraped_title or row.get("title", "") or "",
            "outcome": "scraped",
            "error": None,
            "started_at": start,
        }
    except Exception as exc:
        logger.error("Failed to scrape page %s (%s): %s", page_id, url, exc)
        return {
            "row": row,
            "page_id": page_id,
            "url": url,
            "today": today,
            "raw_text": None,
            "sections": [],
            "title": row.get("title", "") or "",
            "outcome": "error",
            "error": str(exc),
            "error_type": type(exc).__name__,
            "started_at": start,
        }


# ---------------------------------------------------------------------------
# Phase 2: boilerplate detection
# ---------------------------------------------------------------------------


def _detect_boilerplate(
    conn: Any,
    scrape_results: list[dict[str, Any]],
    boilerplate_cfg: dict[str, Any],
) -> set[str]:
    if not boilerplate_cfg.get("repetition_detection_enabled", True):
        return set()

    threshold = float(boilerplate_cfg.get("frequency_threshold", 0.7))
    min_documents = int(boilerplate_cfg.get("min_documents", 5))

    documents: list[str] = []
    for result in scrape_results:
        if result.get("raw_text"):
            documents.append(result["raw_text"])

    # Include existing corpus so the detector has enough signal on partial runs.
    documents.extend(dedup.load_corpus_contents(conn))

    return scrape_ipfr.detect_frequent_lines(
        documents,
        frequency_threshold=threshold,
        min_documents=min_documents,
    )


# ---------------------------------------------------------------------------
# Phase 3: strip + enrich + upsert + audit
# ---------------------------------------------------------------------------


def _enrich_and_persist(
    scrape_result: dict[str, Any],
    conn: Any,
    config: dict[str, Any],
    blocklist: list[str],
    frequent_lines: set[str],
    stub_cfg: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    row = scrape_result["row"]
    page_id = scrape_result["page_id"]
    url = scrape_result["url"]
    today = scrape_result["today"]
    outcome = scrape_result["outcome"]
    started_at = scrape_result["started_at"]
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    # Scraper failed — log and short-circuit.
    if outcome == "error":
        db.log_ingestion_run(conn, {
            "run_id": run_id,
            "page_id": page_id or None,
            "url": url,
            "timestamp": now_iso,
            "outcome": "error",
            "error_type": scrape_result.get("error_type"),
            "error_message": scrape_result.get("error"),
            "duration_seconds": round(time.monotonic() - started_at, 3),
        })
        return {
            "updated_row": sitemap_mod.update_row(row, last_checked=today),
            "outcome": "error",
            "page_id": page_id,
            "error": scrape_result.get("error"),
        }

    # Skipped due to no change — emit a minimal audit row.
    if outcome == "skipped":
        db.log_ingestion_run(conn, {
            "run_id": run_id,
            "page_id": page_id or None,
            "url": url,
            "timestamp": now_iso,
            "outcome": "skipped",
            "duration_seconds": round(time.monotonic() - started_at, 3),
        })
        return {
            "updated_row": sitemap_mod.update_row(row, last_checked=today),
            "outcome": "skipped",
            "page_id": page_id,
        }

    # --- Strip boilerplate + realign sections ---
    raw_text = scrape_result["raw_text"]
    sections = scrape_result["sections"]
    title = scrape_result["title"]

    stripped, adjusted_sections, bytes_stripped = scrape_ipfr.strip_boilerplate(
        raw_text,
        sections,
        blocklist=blocklist,
        frequent_lines=frequent_lines,
    )

    # --- Stub detection ---
    stub_min_length = int(stub_cfg.get("min_content_length", 500))
    stub_phrases = stub_cfg.get("phrases", []) or []
    if scrape_ipfr.is_stub_page(
        stripped, min_length=stub_min_length, stub_phrases=stub_phrases,
    ):
        version_hash = scrape_ipfr.compute_version_hash(stripped)
        db.upsert_page(conn, {
            "page_id": page_id,
            "url": url,
            "title": title,
            "content": stripped,
            "version_hash": version_hash,
            "last_modified": row.get("last_modified"),
            "last_checked": today,
            "last_ingested": today,
            "doc_embedding": None,
            "status": "stub",
            "duplicate_of": None,
        })
        # Stub pages don't get chunks/entities/keyphrases.
        db.replace_chunks(conn, page_id, [])
        db.replace_entities(conn, page_id, [])
        db.replace_keyphrases(conn, page_id, [])
        db.replace_sections(conn, page_id, [])
        db.log_ingestion_run(conn, {
            "run_id": run_id,
            "page_id": page_id,
            "url": url,
            "timestamp": now_iso,
            "outcome": "stub",
            "status": "stub",
            "content_length": len(stripped),
            "boilerplate_bytes_stripped": bytes_stripped,
            "duration_seconds": round(time.monotonic() - started_at, 3),
        })
        return {
            "updated_row": sitemap_mod.update_row(
                row, last_checked=today, last_modified=row.get("last_modified", ""),
                title=title or None,
            ),
            "outcome": "stub",
            "page_id": page_id,
        }

    # --- Enrich + upsert ---
    try:
        enriched = enrich.enrich_page(
            page_id=page_id,
            content=stripped,
            sections=adjusted_sections,
            config=config,
        )
    except Exception as exc:
        logger.error("Enrichment failed for %s: %s", page_id, exc)
        db.log_ingestion_run(conn, {
            "run_id": run_id,
            "page_id": page_id,
            "url": url,
            "timestamp": now_iso,
            "outcome": "error",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "content_length": len(stripped),
            "boilerplate_bytes_stripped": bytes_stripped,
            "duration_seconds": round(time.monotonic() - started_at, 3),
        })
        return {
            "updated_row": sitemap_mod.update_row(row, last_checked=today),
            "outcome": "error",
            "page_id": page_id,
            "error": str(exc),
        }

    version_hash = scrape_ipfr.compute_version_hash(stripped)
    db.upsert_page(conn, {
        "page_id": page_id,
        "url": url,
        "title": title,
        "content": stripped,
        "version_hash": version_hash,
        "last_modified": row.get("last_modified"),
        "last_checked": today,
        "last_ingested": today,
        "doc_embedding": enriched.get("doc_embedding"),
        "status": "active",
        "duplicate_of": None,
    })
    db.replace_chunks(conn, page_id, enriched["chunks"])
    db.replace_entities(conn, page_id, enriched["entities"])
    db.replace_keyphrases(conn, page_id, enriched["keyphrases"])
    db.replace_sections(conn, page_id, enriched["sections"])

    db.log_ingestion_run(conn, {
        "run_id": run_id,
        "page_id": page_id,
        "url": url,
        "timestamp": now_iso,
        "outcome": "ingested",
        "status": "active",
        "chunk_count": len(enriched["chunks"]),
        "section_count": len(enriched["sections"]),
        "entity_count": len(enriched["entities"]),
        "keyphrase_count": len(enriched["keyphrases"]),
        "content_length": len(stripped),
        "boilerplate_bytes_stripped": bytes_stripped,
        "duration_seconds": round(time.monotonic() - started_at, 3),
    })

    return {
        "updated_row": sitemap_mod.update_row(
            row, last_checked=today, last_modified=row.get("last_modified", ""),
            title=title or None,
        ),
        "outcome": "ingested",
        "page_id": page_id,
    }


def _needs_ingestion(row: dict[str, str], stored_hash: str | None) -> bool:
    """Return True if the page requires re-ingestion."""
    if not row.get("last_ingested"):
        return True
    if stored_hash is None:
        return True
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
    dup = stats.get("duplicates", {})
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
        f"| Stubs | {stats['stubs']} |",
        f"| Errors | {stats['errors']} |",
        f"| Boilerplate lines detected | {stats.get('boilerplate_lines_detected', 0)} |",
        f"| Exact duplicates | {dup.get('exact', 0)} |",
        f"| Near-duplicates | {dup.get('near', 0)} |",
        f"| Keyphrase rows dropped (IDF) | {stats.get('keyphrases_dropped', 0)} |",
    ]
    if errors:
        lines += ["", "### Errors", ""]
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
    print(json.dumps(result, indent=2, default=str))
