"""
src/pipeline.py

Main pipeline orchestrator — Stages 1–9 (Section 2.1, Phase 4 task 4.8)

Execution order per run:
  0. Load config and validate. Open SQLite connection.
  1. Check for pending deferred triggers → process through Stages 8–9 first.
  2. Load source registry; determine which sources are due for a check.
  3. For each due source, run Stages 1–6 independently.
     Each source is wrapped in try/except so a failure on one never blocks
     the others (Section 6.3 stage-level error isolation).
  4. Stage 7: aggregate all confirmed (source, page) pairs into TriggerBundles.
  5. Stage 8: LLM assessment (skipped in observation mode).
  6. Stage 9: send notification email (skipped in observation mode).
  7. Commit updated snapshots and database back to Git (Section 7.2).
  8. Write GitHub Actions Job Summary.

Observation mode (Section 2.3): when pipeline.observation_mode is true,
Stages 8 and 9 are skipped. The pipeline logs score distributions and exits
after Stage 7, saving LLM cost during the calibration period.

Usage
-----
From the repository root:

    python -m src.pipeline [--config tripwire_config.yaml] [--run-id 2026-04-06-001]
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run the full pipeline.  Returns 0 on success, non-zero on fatal error."""
    import argparse

    parser = argparse.ArgumentParser(description="Run the Tripwire monitoring pipeline.")
    parser.add_argument(
        "--config",
        default="tripwire_config.yaml",
        help="Path to tripwire_config.yaml (default: %(default)s)",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Override the automatically generated run ID.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument(
        "--check-frequency",
        default=None,
        metavar="FREQ",
        help=(
            "Override per-source check frequency for this run. "
            "Accepts: daily, weekly, fortnightly, monthly, quarterly, or 'all' "
            "to force every source to run regardless of last-check date."
        ),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Suppress verbose INFO from third-party ML and HTTP libraries.
    for _lib in ("httpx", "sentence_transformers", "huggingface_hub", "transformers"):
        logging.getLogger(_lib).setLevel(logging.WARNING)

    # Disable transformers weight-loading progress bars.
    try:
        import transformers.utils.logging as _hf_logging
        _hf_logging.disable_progress_bar()
    except Exception:
        pass

    run_id = args.run_id or _generate_run_id()
    t_start = time.monotonic()

    try:
        exit_code = _run_pipeline(args.config, run_id, args.check_frequency)
    except Exception as exc:
        logger.critical("Pipeline aborted with unhandled exception: %s", exc, exc_info=True)
        exit_code = 1
    finally:
        elapsed = time.monotonic() - t_start
        logger.info("Pipeline finished in %.1f s (run_id=%s)", elapsed, run_id)

    return exit_code


def _generate_run_id() -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    github_run_number = os.environ.get("GITHUB_RUN_NUMBER", "")
    suffix = github_run_number if github_run_number else datetime.now(timezone.utc).strftime("%H%M")
    return f"{today}-{suffix}"


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------


def _run_pipeline(config_path: str, run_id: str, check_frequency_override: str | None = None) -> int:
    from src.config import load_config, snapshot_config, get as cfg_get
    from src.stage1_metadata import load_source_registry, probe_source, is_due_for_check
    from src.stage7_aggregation import aggregate_triggers, SourceTriggerRecord
    from src.stage8_llm import (
        assess_bundles,
        load_pending_deferred_triggers,
        mark_deferred_trigger_processed,
    )
    from src.stage9_notification import (
        send_notification,
        PageMeta,
        RejectedCandidate,
    )

    # ------------------------------------------------------------------
    # 1. Load and validate config.
    # ------------------------------------------------------------------
    config = load_config(config_path)
    config_snapshot = snapshot_config(config)
    observation_mode: bool = cfg_get(config, "pipeline", "observation_mode", default=True)

    logger.info("Run ID: %s  |  Observation mode: %s", run_id, observation_mode)

    # ------------------------------------------------------------------
    # 2. Open SQLite database (create with empty schema if not present).
    # ------------------------------------------------------------------
    from ingestion.db import init_db

    config_dir = Path(config_path).parent if Path(config_path).is_absolute() else Path.cwd()
    db_path = config_dir / cfg_get(config, "paths", "sqlite_db", default="data/ipfr_corpus/ipfr.sqlite")
    if not db_path.exists():
        logger.warning(
            "SQLite database not found at %s — initialising empty database. "
            "Run the IPFR ingestion pipeline to populate the corpus.",
            db_path,
        )

    try:
        wal_mode: bool = cfg_get(config, "storage", "sqlite_wal_mode", default=True)
        conn = init_db(db_path, wal_mode=wal_mode)
    except sqlite3.Error as exc:
        logger.critical("Cannot open SQLite database %s: %s", db_path, exc)
        return 1

    # ------------------------------------------------------------------
    # 3. Backfill any JSON report files that predate the llm_assessments table.
    # ------------------------------------------------------------------
    _backfill_llm_assessments_from_files(conn, config_dir)

    # ------------------------------------------------------------------
    # 4. Process any pending deferred triggers first (Section 6.5).
    # ------------------------------------------------------------------
    deferred_max_age = int(cfg_get(config, "pipeline", "deferred_trigger_max_age_days", default=7))
    deferred_records = load_pending_deferred_triggers(conn, deferred_max_age)
    if deferred_records and not observation_mode:
        logger.info("Processing %d deferred trigger(s) from previous run(s).", len(deferred_records))
        _process_deferred_triggers(deferred_records, conn, config, run_id)

    # ------------------------------------------------------------------
    # 4. Load source registry.
    # ------------------------------------------------------------------
    registry_path = config_dir / cfg_get(
        config, "paths", "source_registry_csv",
        default="data/influencer_sources/source_registry.csv"
    )
    try:
        sources = load_source_registry(registry_path)
    except FileNotFoundError:
        logger.error("Source registry not found: %s", registry_path)
        conn.close()
        return 1

    snapshot_dir = config_dir / cfg_get(
        config, "paths", "influencer_snapshots_dir",
        default="data/influencer_sources/snapshots"
    )
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 5. Process each source through Stages 1–6.
    # ------------------------------------------------------------------
    import requests

    # Read the optional proxy URL.  When set, a Selenium fetch that is
    # bot-blocked will be retried through selenium-wire using this proxy.
    proxy_url: str | None = os.environ.get("SCRAPER_PROXY_URL") or None
    if proxy_url:
        _proxy_display = proxy_url.split("@")[-1] if "@" in proxy_url else proxy_url
        logger.info("Proxy configured for blocked-Selenium fallback: %s", _proxy_display)

    session = requests.Session()
    session.headers["User-Agent"] = (
        "TripwireBot/1.0 (+https://github.com/thomas-amann-ipaustralia/tripwire)"
    )

    source_records: list = []
    rejected_candidates: list = []
    run_log_rows: list[dict] = []

    for source in sources:
        source_id = source["source_id"]
        source_type = source.get("source_type", "webpage").lower()
        source_url = source["url"]
        source_importance = float(source.get("importance", 0.5))

        t_source = time.monotonic()
        log_entry: dict[str, Any] = {
            "run_id": run_id,
            "source_id": source_id,
            "source_url": source_url,
            "source_type": source_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stage_reached": "stage1",
            "outcome": "completed",
            "error_type": None,
            "error_message": None,
            "triggered_pages": None,
            "details": {"config_snapshot": json.loads(config_snapshot), "stages": {}},
        }

        try:
            _process_source(
                source=source,
                source_id=source_id,
                source_type=source_type,
                source_url=source_url,
                source_importance=source_importance,
                session=session,
                conn=conn,
                config=config,
                snapshot_dir=snapshot_dir,
                run_id=run_id,
                source_records=source_records,
                rejected_candidates=rejected_candidates,
                log_entry=log_entry,
                check_frequency_override=check_frequency_override,
                proxy_url=proxy_url,
            )
        except Exception as exc:
            logger.error(
                "Unhandled error processing source %s: %s", source_id, exc, exc_info=True
            )
            log_entry["outcome"] = "error"
            log_entry["error_type"] = type(exc).__name__
            log_entry["error_message"] = str(exc)
            _record_source_failure(snapshot_dir, source_id, exc)

        log_entry["duration_seconds"] = time.monotonic() - t_source
        run_log_rows.append(log_entry)

    # ------------------------------------------------------------------
    # 6. Stage 7 — Trigger Aggregation.
    # ------------------------------------------------------------------
    logger.info("--- Stage 7: Trigger Aggregation ---")
    aggregation_result = aggregate_triggers(source_records, config)
    bundles = aggregation_result.bundles
    bundles_by_page = {b.ipfr_page_id: b for b in bundles}

    logger.info(
        "Stage 7 complete: %d bundle(s) across %d trigger(s).",
        len(bundles),
        aggregation_result.total_triggers,
    )

    if observation_mode:
        logger.info(
            "Observation mode: pipeline complete after Stage 7. "
            "Score distributions logged. Skipping LLM and email."
        )
        _write_observation_summary(aggregation_result, run_log_rows, run_id)
        _log_run_entries(conn, run_log_rows)
        # Health alerting still runs in observation mode (error rate / consecutive failures).
        from src.health import evaluate_and_alert
        evaluate_and_alert(
            run_id=run_id,
            run_log_rows=run_log_rows,
            llm_failed_count=0,
            crossencoder_truncation_pairs=_collect_truncation_pairs(run_log_rows),
            conn=conn,
            config=config,
            snapshot_dir=snapshot_dir,
        )
        conn.close()
        _write_github_summary(run_id, observation_mode=True, bundles=bundles, assessments=[])
        return 0

    # ------------------------------------------------------------------
    # 7. Stage 8 — LLM Assessment.
    # ------------------------------------------------------------------
    logger.info("--- Stage 8: LLM Assessment ---")
    llm_result = assess_bundles(
        bundles=bundles,
        conn=conn,
        config=config,
        run_id=run_id,
    )
    assessments = llm_result.assessments
    _save_llm_reports(assessments, run_id)
    _write_llm_assessments(conn, assessments, run_id)

    # ------------------------------------------------------------------
    # 8. Stage 9 — Notification.
    # ------------------------------------------------------------------
    logger.info("--- Stage 9: Email Notification ---")
    page_meta_by_id = _load_page_meta(conn, list(bundles_by_page.keys()))
    run_date = datetime.now(timezone.utc).strftime("%-d %B %Y")

    notification_result = send_notification(
        assessments=assessments,
        bundles_by_page=bundles_by_page,
        page_meta_by_id=page_meta_by_id,
        rejected_candidates=rejected_candidates,
        run_id=run_id,
        run_date=run_date,
        config=config,
    )

    # ------------------------------------------------------------------
    # 9. Log all run entries to SQLite.
    # ------------------------------------------------------------------
    _log_run_entries(conn, run_log_rows)

    # ------------------------------------------------------------------
    # 9a. Health alerting (Section 6.6, Phase 5 task 5.1).
    # ------------------------------------------------------------------
    from src.health import evaluate_and_alert

    crossencoder_truncation_pairs = _collect_truncation_pairs(run_log_rows)
    evaluate_and_alert(
        run_id=run_id,
        run_log_rows=run_log_rows,
        llm_failed_count=llm_result.failed_count,
        crossencoder_truncation_pairs=crossencoder_truncation_pairs,
        conn=conn,
        config=config,
        snapshot_dir=snapshot_dir,
    )

    conn.close()

    # ------------------------------------------------------------------
    # 10. Commit snapshots and LLM reports back to Git (Section 7.2).
    # ------------------------------------------------------------------
    _git_commit_snapshots(snapshot_dir, run_id, config)

    # ------------------------------------------------------------------
    # 11. Write GitHub Actions Job Summary.
    # ------------------------------------------------------------------
    _write_github_summary(
        run_id=run_id,
        observation_mode=False,
        bundles=bundles,
        assessments=assessments,
        notification_result=notification_result,
    )

    return 0


# ---------------------------------------------------------------------------
# Per-source processing (Stages 1–6)
# ---------------------------------------------------------------------------


def _process_source(
    source: dict[str, Any],
    source_id: str,
    source_type: str,
    source_url: str,
    source_importance: float,
    session: Any,
    conn: sqlite3.Connection,
    config: dict[str, Any],
    snapshot_dir: Path,
    run_id: str,
    source_records: list,
    rejected_candidates: list,
    log_entry: dict[str, Any],
    check_frequency_override: str | None = None,
    proxy_url: str | None = None,
) -> None:
    """Run Stages 1–6 for a single source."""
    from src.stage1_metadata import probe_source, is_due_for_check
    from src.stage2_change_detection import detect_change
    from src.stage3_diff import generate_diff
    from src.stage4_relevance import score_relevance
    from src.stage5_biencoder import score_biencoder, release_biencoder
    from src.stage6_crossencoder import score_crossencoder
    from src.stage9_notification import RejectedCandidate
    from src.stage7_aggregation import SourceTriggerRecord

    stages = log_entry["details"]["stages"]

    logger.info(">>> Source %s (%s): starting check", source_id, source_type)

    # ---- Stage 1: Metadata Probe ----------------------------------------
    logger.info("--- Stage 1: Metadata Probe ---")
    log_entry["stage_reached"] = "stage1"
    source_state = _load_source_state(snapshot_dir, source_id)
    stored_signals = source_state.get("probe_signals")
    last_checked = source_state.get("last_checked")

    if not is_due_for_check(source, last_checked, check_frequency_override):
        logger.info("Source %s: SKIP — not yet due for check", source_id)
        log_entry["outcome"] = "no_change"
        stages["metadata_probe"] = {"decision": "not_due"}
        return

    probe = probe_source(source, stored_signals, session)
    stages["metadata_probe"] = probe.to_dict()
    # Build the prospective post-probe state in memory only.  It is only
    # persisted once we know the downstream scrape/validation will not fail,
    # otherwise a failed run leaves `last_checked` and `probe_signals` updated
    # against a baseline that was never captured — the frequency gate then
    # skips retries and the probe falsely reports "unchanged" on the next run.
    source_state = {
        **source_state,
        "probe_signals": probe.signals,
        "last_checked": datetime.now(timezone.utc).isoformat(),
    }

    logger.info("Source %s: Stage 1 probe=%s", source_id, probe.decision)
    if not probe.should_proceed:
        # Even when Stage 1 signals no change, we must scrape once to
        # establish a content baseline.  Without one, future changes will
        # produce a "first_run" diff with no before/after context.  This
        # situation arises when a prior scrape attempt failed before a
        # baseline was ever captured.
        if source_state.get("previous_text") is None:
            logger.info(
                "Source %s: Stage 1 says %s but no content baseline found — "
                "proceeding to establish initial snapshot",
                source_id,
                probe.decision,
            )
            # Fall through to scraping.
        else:
            logger.info(
                "Source %s: FILTERED after Stage 1 — %s, no change detected",
                source_id,
                probe.decision,
            )
            log_entry["outcome"] = "no_change"
            _save_source_state_success(snapshot_dir, source_id, source_state)
            return

    # ---- Scrape / fetch new content -------------------------------------
    log_entry["stage_reached"] = "scrape"
    from src.scraper import scrape_and_normalise
    new_text = scrape_and_normalise(
        source_url, source_type, session,
        force_selenium=source.get("force_selenium", False),
        proxy_url=proxy_url,
    )
    previous_text = source_state.get("previous_text")
    previous_hash = source_state.get("previous_hash")

    # ---- Content Validation (non-RSS sources only) ----------------------
    # RSS sources are exempt: scrape_and_normalise returns raw XML for RSS,
    # and Stage 3 re-fetches/parses the feed independently.  Applying length
    # or CAPTCHA checks to raw XML produces false positives.
    if source_type != "rss":
        from src.validation import validate_scraped_content
        validate_scraped_content(
            new_text,
            source_url,
            previous_length=len(previous_text) if previous_text else None,
        )

    # ---- Stage 2: Change Detection (webpages only) ----------------------
    logger.info("--- Stage 2: Change Detection ---")
    log_entry["stage_reached"] = "stage2"
    fingerprint_enabled = config.get("change_detection", {}).get(
        "significance_fingerprint", True
    )
    change_result = detect_change(
        source_id=source_id,
        source_type=source_type,
        new_text=new_text,
        previous_text=previous_text,
        previous_hash=previous_hash,
        fingerprint_enabled=fingerprint_enabled,
    )
    stages["change_detection"] = change_result.to_dict()

    if not change_result.should_proceed:
        logger.info("Source %s: FILTERED after Stage 2 — %s", source_id, change_result.decision)
        log_entry["outcome"] = "no_change"
        _save_source_state_success(snapshot_dir, source_id, {
            **source_state,
            "previous_hash": _sha256(new_text),
            "previous_text": new_text,
        })
        return

    significance = change_result.significance

    # ---- Stage 3: Diff Generation ---------------------------------------
    logger.info("--- Stage 3: Diff Generation ---")
    log_entry["stage_reached"] = "stage3"
    versions_retained = int(
        config.get("storage", {}).get("content_versions_retained", 6)
    )
    diff_result = generate_diff(
        source=source,
        new_text=new_text,
        previous_text=previous_text,
        diff_lines=change_result.diff_lines,
        snapshot_dir=snapshot_dir,
        versions_retained=versions_retained,
        run_id=run_id,
        session=session,
    )
    stages["diff"] = diff_result.to_dict()
    normalised_diff = diff_result.normalised_diff
    if diff_result.diff_path:
        logger.info(
            "Source %s: Stage 3 — %s saved → %s",
            source_id, diff_result.diff_type, diff_result.diff_path,
        )

    _save_source_state_success(snapshot_dir, source_id, {
        **source_state,
        "previous_hash": _sha256(new_text),
        "previous_text": new_text,
    })

    # ---- Stage 4: Relevance Scoring -------------------------------------
    logger.info("--- Stage 4: Relevance Scoring ---")
    log_entry["stage_reached"] = "stage4"
    relevance_result = score_relevance(
        diff_text=normalised_diff,
        source_importance=source_importance,
        conn=conn,
        config=config,
        ner_entities=list(change_result.fingerprint.get("entities", [])),
    )
    stages["relevance"] = {
        "fast_pass_triggered": relevance_result.fast_pass_triggered,
        "candidates": len(relevance_result.candidates),
        "top_candidates": [
            {"page_id": p.page_id, "final_score": p.final_score}
            for p in relevance_result.candidates[:5]
        ],
    }

    logger.info(
        "Source %s: Stage 4 — %d candidate(s)%s",
        source_id,
        len(relevance_result.candidates),
        " [fast-pass]" if relevance_result.fast_pass_triggered else "",
    )
    if not relevance_result.candidates:
        logger.info("Source %s: FILTERED after Stage 4 — no relevant IPFR pages found", source_id)
        log_entry["outcome"] = "no_change"
        return

    stage4_page_scores: dict[str, dict] = {
        p.page_id: {
            "final_score": p.final_score,
            "rrf_score": p.rrf_score,
            "bm25_rank": p.bm25_rank,
            "semantic_rank": p.semantic_rank,
        }
        for p in relevance_result.all_pages
    }

    # ---- Stage 5: Bi-Encoder --------------------------------------------
    logger.info("--- Stage 5: Bi-Encoder ---")
    log_entry["stage_reached"] = "stage5"
    candidate_ids = [p.page_id for p in relevance_result.candidates]
    biencoder_result = score_biencoder(
        change_text=normalised_diff,
        candidate_page_ids=candidate_ids,
        conn=conn,
        config=config,
    )
    stages["biencoder"] = {
        "candidates_in": len(candidate_ids),
        "candidates_out": len(biencoder_result.candidate_pages),
    }

    logger.info(
        "Source %s: Stage 5 — %d/%d candidates passed bi-encoder",
        source_id, len(biencoder_result.candidate_pages), len(candidate_ids),
    )
    if not biencoder_result.candidate_pages:
        logger.info("Source %s: FILTERED after Stage 5 — no pages passed bi-encoder threshold", source_id)
        for page_id in candidate_ids:
            rejected_candidates.append(RejectedCandidate(
                source_id=source_id,
                source_url=source_url,
                ipfr_page_id=page_id,
                rejection_stage="biencoder",
            ))
        log_entry["outcome"] = "no_change"
        return

    stage5_page_scores: dict[str, dict] = {
        p.page_id: {
            "max_chunk_score": p.max_chunk_score,
            "chunks_above_low_medium": p.chunks_above_low_medium,
        }
        for p in biencoder_result.candidate_pages
    }

    # Release bi-encoder before loading cross-encoder (Section 7.4).
    release_biencoder()

    # ---- Stage 6: Cross-Encoder -----------------------------------------
    logger.info("--- Stage 6: Cross-Encoder ---")
    log_entry["stage_reached"] = "stage6"
    stage5_candidate_ids = [p.page_id for p in biencoder_result.candidate_pages]
    ce_result = score_crossencoder(
        candidate_page_ids=stage5_candidate_ids,
        change_text=normalised_diff,
        conn=conn,
        config=config,
        stage4_scores={p.page_id: p.final_score for p in relevance_result.all_pages},
    )
    stages["crossencoder"] = {
        "candidates_in": len(stage5_candidate_ids),
        "confirmed": len(ce_result.confirmed_pages),
        "graph_propagated": len(ce_result.graph_propagated_pages),
    }

    confirmed_ids = {p.page_id for p in ce_result.confirmed_pages}
    for p in ce_result.all_scored:
        if p.decision != "proceed":
            rejected_candidates.append(RejectedCandidate(
                source_id=source_id,
                source_url=source_url,
                ipfr_page_id=p.page_id,
                rejection_stage="crossencoder",
                crossencoder_score=p.crossencoder_score,
                reranked_score=p.reranked_score,
            ))

    logger.info(
        "Source %s: Stage 6 — %d/%d confirmed by cross-encoder%s",
        source_id,
        len(ce_result.confirmed_pages),
        len(stage5_candidate_ids),
        f" (+{len(ce_result.graph_propagated_pages)} graph-propagated)" if ce_result.graph_propagated_pages else "",
    )
    if not ce_result.confirmed_pages:
        logger.info("Source %s: FILTERED after Stage 6 — no pages confirmed by cross-encoder", source_id)
        log_entry["outcome"] = "no_change"
        return

    log_entry["stage_reached"] = "stage6_complete"
    log_entry["triggered_pages"] = json.dumps(list(confirmed_ids))

    confirmed_dicts = [
        {
            "page_id": p.page_id,
            "crossencoder_score": p.crossencoder_score,
            "reranked_score": p.reranked_score,
            "final_score": p.final_score,
            "decision": p.decision,
            "graph_propagated_to": p.graph_propagated_to,
        }
        for p in ce_result.confirmed_pages
    ]

    source_records.append(SourceTriggerRecord(
        source_id=source_id,
        source_url=source_url,
        source_importance=source_importance,
        source_type=source_type,
        diff_text=normalised_diff,
        significance=significance,
        stage4_scores=stage4_page_scores,
        stage5_scores=stage5_page_scores,
        stage6_confirmed=confirmed_dicts,
    ))
    logger.info(
        "Source %s: TRIGGER — %d confirmed IPFR page(s): %s",
        source_id, len(confirmed_ids), sorted(confirmed_ids),
    )


# ---------------------------------------------------------------------------
# Deferred trigger processing (Section 6.5)
# ---------------------------------------------------------------------------


def _process_deferred_triggers(
    deferred_records: list[dict[str, Any]],
    conn: sqlite3.Connection,
    config: dict[str, Any],
    run_id: str,
) -> None:
    from src.stage7_aggregation import TriggerBundle, TriggerSource
    from src.stage8_llm import assess_bundles, mark_deferred_trigger_processed
    from src.stage9_notification import send_notification, PageMeta, RejectedCandidate

    bundles: list[TriggerBundle] = []
    for rec in deferred_records:
        td = rec["trigger_data"]
        bundle = TriggerBundle(ipfr_page_id=td["ipfr_page_id"])
        for t in td.get("triggers", []):
            bundle.triggers.append(TriggerSource(
                source_id=t.get("source_id", ""),
                source_url=t.get("source_url", ""),
                source_importance=float(t.get("source_importance", 0.5)),
                source_type=t.get("source_type", "webpage"),
                diff_text=t.get("diff_text", ""),
                significance=t.get("significance", "standard"),
                stage4_final_score=float(t.get("stage4_final_score", 0.0)),
                stage4_rrf_score=0.0,
                stage4_bm25_rank=0,
                stage4_semantic_rank=0,
                biencoder_max_chunk_score=float(t.get("biencoder_max_chunk_score", 0.0)),
                biencoder_chunks_above_threshold=0,
                crossencoder_score=0.0,
                crossencoder_reranked_score=0.0,
                crossencoder_final_score=float(t.get("crossencoder_final_score", 0.0)),
                graph_propagated=bool(t.get("graph_propagated", False)),
            ))
        bundles.append(bundle)

    if not bundles:
        return

    llm_result = assess_bundles(
        bundles=bundles,
        conn=conn,
        config=config,
        run_id=f"{run_id}-deferred",
    )

    if llm_result.assessments:
        page_meta = _load_page_meta(conn, [b.ipfr_page_id for b in bundles])
        run_date = datetime.now(timezone.utc).strftime("%-d %B %Y")
        send_notification(
            assessments=llm_result.assessments,
            bundles_by_page={b.ipfr_page_id: b for b in bundles},
            page_meta_by_id=page_meta,
            rejected_candidates=[],
            run_id=f"{run_id}-deferred",
            run_date=run_date,
            config=config,
        )

    for rec in deferred_records:
        mark_deferred_trigger_processed(conn, rec["id"])


# ---------------------------------------------------------------------------
# SQLite logging
# ---------------------------------------------------------------------------


def _log_run_entries(
    conn: sqlite3.Connection, run_log_rows: list[dict[str, Any]]
) -> None:
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id          TEXT NOT NULL,
                source_id       TEXT NOT NULL,
                source_url      TEXT NOT NULL,
                source_type     TEXT NOT NULL,
                timestamp       TEXT NOT NULL,
                stage_reached   TEXT NOT NULL,
                outcome         TEXT NOT NULL,
                error_type      TEXT,
                error_message   TEXT,
                triggered_pages TEXT,
                duration_seconds REAL,
                details         TEXT NOT NULL
            )
        """)
        conn.executemany(
            """
            INSERT INTO pipeline_runs
                (run_id, source_id, source_url, source_type, timestamp,
                 stage_reached, outcome, error_type, error_message,
                 triggered_pages, duration_seconds, details)
            VALUES
                (:run_id, :source_id, :source_url, :source_type, :timestamp,
                 :stage_reached, :outcome, :error_type, :error_message,
                 :triggered_pages, :duration_seconds, :details)
            """,
            [
                {**row, "details": json.dumps(row.get("details", {}))}
                for row in run_log_rows
            ],
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.error("Failed to write pipeline_runs log: %s", exc)


# ---------------------------------------------------------------------------
# Page metadata loader (for Stage 9)
# ---------------------------------------------------------------------------


def _load_page_meta(
    conn: sqlite3.Connection, page_ids: list[str]
) -> dict[str, Any]:
    from src.stage9_notification import PageMeta

    if not page_ids:
        return {}

    placeholders = ",".join("?" * len(page_ids))
    rows = conn.execute(
        f"SELECT page_id, title, url FROM pages WHERE page_id IN ({placeholders})",
        page_ids,
    ).fetchall()
    return {row[0]: PageMeta(page_id=row[0], title=row[1], url=row[2]) for row in rows}


# ---------------------------------------------------------------------------
# Source state persistence
# ---------------------------------------------------------------------------


def _source_state_path(snapshot_dir: Path, source_id: str) -> Path:
    return snapshot_dir / source_id / "state.json"


def _load_source_state(snapshot_dir: Path, source_id: str) -> dict[str, Any]:
    path = _source_state_path(snapshot_dir, source_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_source_state(
    snapshot_dir: Path, source_id: str, state: dict[str, Any]
) -> None:
    path = _source_state_path(snapshot_dir, source_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _save_source_state_success(
    snapshot_dir: Path, source_id: str, state: dict[str, Any]
) -> None:
    """Persist state on a successful run.

    Clears any prior failure markers and resets the consecutive-failure
    counter so the health alert resets as soon as the source recovers.
    """
    clean = {k: v for k, v in state.items()
             if k not in ("last_error", "last_error_at", "consecutive_failures")}
    _save_source_state(snapshot_dir, source_id, clean)


def _record_source_failure(
    snapshot_dir: Path, source_id: str, exc: BaseException
) -> None:
    """Record a per-source failure without poisoning the probe baseline.

    Loads the existing state from disk and writes back only the failure
    metadata.  ``last_checked`` and ``probe_signals`` are intentionally
    preserved as they were before this run so the frequency gate does not
    skip retries and Stage 1 still compares against the last known-good
    baseline.
    """
    try:
        state = _load_source_state(snapshot_dir, source_id)
        state["last_error"] = f"{type(exc).__name__}: {exc}"
        state["last_error_at"] = datetime.now(timezone.utc).isoformat()
        state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
        _save_source_state(snapshot_dir, source_id, state)
    except Exception as record_exc:
        logger.warning(
            "Could not record failure metadata for %s: %s", source_id, record_exc
        )


# ---------------------------------------------------------------------------
# Observation mode summary
# ---------------------------------------------------------------------------


def _write_observation_summary(
    aggregation_result: Any, run_log_rows: list[dict], run_id: str
) -> None:
    summary_path = Path("data/logs") / f"observation_summary_{run_id}.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "run_id": run_id,
        "observation_mode": True,
        "aggregation": aggregation_result.observation_data,
        "sources_processed": len(run_log_rows),
        "sources_with_triggers": sum(
            1 for r in run_log_rows if r.get("triggered_pages")
        ),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.info("Observation summary written to %s", summary_path)


# ---------------------------------------------------------------------------
# Git persistence (Section 7.2)
# ---------------------------------------------------------------------------


def _git_commit_snapshots(
    snapshot_dir: Path,
    run_id: str,
    config: dict[str, Any],
) -> None:
    git_cfg = config.get("storage", {}).get("git_persistence", {})
    if not git_cfg.get("enabled", True):
        return
    if not git_cfg.get("commit_snapshots", True):
        return

    author = git_cfg.get(
        "commit_author",
        "github-actions[bot] <github-actions[bot]@users.noreply.github.com>",
    )
    try:
        name, email_part = author.split(" <", 1)
        email_val = email_part.rstrip(">")
    except ValueError:
        name = "github-actions[bot]"
        email_val = "github-actions[bot]@users.noreply.github.com"

    try:
        subprocess.run(["git", "config", "user.name", name], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", email_val], check=True, capture_output=True)
        subprocess.run(["git", "add", str(snapshot_dir)], check=True, capture_output=True)
        llm_reports_dir = Path("data/LLM Reports")
        if llm_reports_dir.exists():
            subprocess.run(["git", "add", str(llm_reports_dir)], check=True, capture_output=True)
        diff_result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if diff_result.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m",
                 f"chore: update influencer snapshots and LLM reports [run {run_id}]"],
                check=True,
                capture_output=True,
            )
            subprocess.run(["git", "push", "origin", "HEAD"], check=True, capture_output=True)
            logger.info(
                "Snapshots and LLM reports committed and pushed (run %s).", run_id,
            )
        else:
            logger.info("No snapshot or report changes to commit — nothing to push.")
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "Git commit/push failed: %s",
            exc.stderr.decode() if exc.stderr else exc,
        )


# ---------------------------------------------------------------------------
# GitHub Actions Job Summary (Section 8.2)
# ---------------------------------------------------------------------------


def _write_github_summary(
    run_id: str,
    observation_mode: bool,
    bundles: list,
    assessments: list,
    notification_result: Any = None,
) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    lines: list[str] = [f"# Tripwire Run Summary — {run_id}", ""]

    if observation_mode:
        lines.append("**Mode:** Observation (LLM and email skipped)")
        lines.append(f"**Trigger bundles:** {len(bundles)}")
    else:
        change_required = [a for a in assessments if a.verdict == "CHANGE_REQUIRED"]
        uncertain = [a for a in assessments if a.verdict == "UNCERTAIN"]
        no_change = [a for a in assessments if a.verdict == "NO_CHANGE"]

        lines += [
            "**Mode:** Live",
            f"**Trigger bundles assessed:** {len(bundles)}",
            f"**CHANGE_REQUIRED:** {len(change_required)}",
            f"**UNCERTAIN:** {len(uncertain)}",
            f"**NO_CHANGE:** {len(no_change)}",
        ]

        if notification_result:
            sent = "Yes" if notification_result.sent else "No"
            lines.append(f"**Email sent:** {sent}")
            if notification_result.fallback_file:
                lines.append(f"**Email fallback file:** `{notification_result.fallback_file}`")

        if change_required:
            lines += ["", "## Amendment Required", ""]
            for a in change_required:
                lines.append(
                    f"- **{a.ipfr_page_id}** (confidence={a.confidence:.0%}): "
                    f"{a.reasoning[:120]}…"
                )

    try:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError as exc:
        logger.warning("Could not write GitHub step summary: %s", exc)


# ---------------------------------------------------------------------------
# LLM report persistence
# ---------------------------------------------------------------------------


def _backfill_llm_assessments_from_files(
    conn: sqlite3.Connection, config_dir: Path
) -> None:
    """Import JSON report files into llm_assessments for any records not already present.

    This handles reports generated before the llm_assessments table was added to the
    schema. Each file is keyed by run_id + ipfr_page_id; duplicates are skipped.
    """
    reports_dir = config_dir / "data" / "LLM Reports"
    if not reports_dir.exists():
        return

    # Build a set of (run_id, ipfr_page_id) pairs already in the table.
    try:
        existing = {
            (row[0], row[1])
            for row in conn.execute(
                "SELECT run_id, ipfr_page_id FROM llm_assessments"
            ).fetchall()
        }
    except sqlite3.Error as exc:
        logger.warning("Cannot read llm_assessments for backfill check: %s", exc)
        return

    rows_to_insert: list[tuple] = []
    for fpath in sorted(reports_dir.glob("*.json")):
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        key = (data.get("run_id", ""), data.get("ipfr_page_id", ""))
        if not key[0] or not key[1] or key in existing:
            continue

        rows_to_insert.append((
            key[0],
            key[1],
            data.get("verdict", ""),
            float(data.get("confidence", 0.0)),
            data.get("reasoning", ""),
            json.dumps(data.get("suggested_changes", [])),
            data.get("model", ""),
            data.get("prompt_tokens"),
            data.get("completion_tokens"),
            data.get("total_tokens"),
            data.get("processing_time_seconds"),
            data.get("retries"),
            1 if data.get("schema_valid", True) else 0,
            data.get("generated_at", datetime.now(timezone.utc).isoformat()),
        ))
        existing.add(key)

    if not rows_to_insert:
        return

    try:
        conn.executemany(
            """
            INSERT INTO llm_assessments
                (run_id, ipfr_page_id, verdict, confidence, reasoning,
                 suggested_changes, model, prompt_tokens, completion_tokens,
                 total_tokens, processing_time_seconds, retries, schema_valid,
                 generated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows_to_insert,
        )
        conn.commit()
        logger.info(
            "Backfilled %d LLM assessment(s) from JSON files into SQLite.",
            len(rows_to_insert),
        )
    except sqlite3.Error as exc:
        logger.error("Failed to backfill llm_assessments from files: %s", exc)


def _save_llm_reports(assessments: list, run_id: str) -> None:
    """Write each LLM assessment to data/LLM Reports/ as a JSON file."""
    if not assessments:
        return
    reports_dir = Path("data/LLM Reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    for assessment in assessments:
        safe_page = assessment.ipfr_page_id.replace("/", "_").replace(" ", "_")
        filename = f"{run_id}_{safe_page}.json"
        report = {
            "run_id": run_id,
            "ipfr_page_id": assessment.ipfr_page_id,
            "verdict": assessment.verdict,
            "confidence": assessment.confidence,
            "reasoning": assessment.reasoning,
            "suggested_changes": assessment.suggested_changes,
            "model": assessment.model,
            "prompt_tokens": assessment.prompt_tokens,
            "completion_tokens": assessment.completion_tokens,
            "total_tokens": assessment.total_tokens,
            "processing_time_seconds": assessment.processing_time_seconds,
            "retries": assessment.retries,
            "schema_valid": assessment.schema_valid,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        (reports_dir / filename).write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info("Saved %d LLM report(s) to %s", len(assessments), reports_dir)


def _write_llm_assessments(
    conn: sqlite3.Connection, assessments: list, run_id: str
) -> None:
    """Persist LLM assessments to the llm_assessments SQLite table."""
    if not assessments:
        return
    try:
        conn.executemany(
            """
            INSERT INTO llm_assessments
                (run_id, ipfr_page_id, verdict, confidence, reasoning,
                 suggested_changes, model, prompt_tokens, completion_tokens,
                 total_tokens, processing_time_seconds, retries, schema_valid,
                 generated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    run_id,
                    a.ipfr_page_id,
                    a.verdict,
                    a.confidence,
                    a.reasoning,
                    json.dumps(a.suggested_changes),
                    a.model,
                    a.prompt_tokens,
                    a.completion_tokens,
                    a.total_tokens,
                    a.processing_time_seconds,
                    a.retries,
                    1 if a.schema_valid else 0,
                    datetime.now(timezone.utc).isoformat(),
                )
                for a in assessments
            ],
        )
        conn.commit()
        logger.info("Wrote %d LLM assessment(s) to SQLite.", len(assessments))
    except sqlite3.Error as exc:
        logger.error("Failed to write llm_assessments: %s", exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _collect_truncation_pairs(run_log_rows: list[dict]) -> list[tuple[str, str]]:
    """Return (source_id, page_id) pairs where cross-encoder truncation was logged."""
    pairs: list[tuple[str, str]] = []
    for row in run_log_rows:
        source_id = row.get("source_id", "")
        stages = row.get("details", {}).get("stages", {})
        crossencoder = stages.get("crossencoder", {})
        for entry in crossencoder.get("truncation_warnings", []):
            page_id = entry if isinstance(entry, str) else entry.get("page_id", "")
            if page_id:
                pairs.append((source_id, page_id))
    return pairs


if __name__ == "__main__":
    sys.exit(main())
