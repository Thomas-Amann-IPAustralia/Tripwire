import json
import os
import datetime
import requests
from typing import List

from . import config
from .config import logger
from .audit import (
    get_last_version_id,
    ensure_audit_log_headers,
    log_to_audit,
    log_stage3_to_audit,
    append_audit_row,
)
from .stage0_detect import fetch_stage0_metadata
from .stage1_fetch import (
    initialize_driver,
    fetch_webpage_content,
    sanitize_rss,
    fetch_legislation_metadata,
    download_legislation_content,
)
from .stage2_diff import get_diff, save_to_archive, save_diff_record
from .stage3_score import calculate_similarity
from .handover import generate_handover_packets
from .stage4_verify import run_llm_verification_for_packets, summarise_verification_files
from .stage5_suggest import run_llm_update_suggestions_for_verification_files
from .review_queue import write_update_review_queue_csv_from_suggestion_files
from .manifest import write_current_run_manifest, write_github_summary


def main():
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    os.makedirs(config.DIFF_DIR, exist_ok=True)

    if not os.path.exists(config.SOURCES_FILE):
        raise FileNotFoundError(f"Missing {config.SOURCES_FILE}")

    with open(config.SOURCES_FILE, 'r', encoding='utf-8') as f:
        sources = json.load(f)

    session = requests.Session()
    driver = None
    handover_paths: List[str] = []

    logger.info(f"--- Tripwire Run: {datetime.datetime.now().isoformat()} ---")

    for source in sources:
        name = source['name']
        stype = source['type']
        priority = source.get('priority', 'Low')

        out_name = source['output_filename'].replace('.docx', '.md') if stype == "Legislation_OData" else source['output_filename']
        out_path = os.path.join(config.OUTPUT_DIR, out_name)

        old_id = get_last_version_id(name)
        current_id = fetch_stage0_metadata(session, source)
        file_exists = os.path.exists(out_path)

        repopulate_only = False
        if old_id and current_id and old_id == current_id:
            if file_exists:
                logger.info(f"No version change for {name}. Skipping.")
                continue
            logger.warning(f"Archive file missing for {name}; healing archive copy.")
            repopulate_only = True

        try:
            new_content = None
            if stype == "Legislation_OData":
                ver_id, meta = fetch_legislation_metadata(session, source)
                if meta:
                    current_id = ver_id
                    new_content = download_legislation_content(session, source['base_url'], meta)
            elif stype == "RSS":
                resp = session.get(source['url'], timeout=15)
                resp.raise_for_status()
                new_content = sanitize_rss(resp.content)
            elif stype == "WebPage":
                if driver is None:
                    driver = initialize_driver()
                new_content = fetch_webpage_content(driver, source['url'])
            else:
                raise ValueError(f"Unsupported source type: {stype}")

            if new_content is None:
                log_to_audit(name, priority, "Exception", "N/A", current_id, reason="No content fetched")
                continue

            diff_hunk = get_diff(out_path, new_content)

            if diff_hunk or not file_exists or repopulate_only:
                save_to_archive(out_name, new_content)

                if diff_hunk and diff_hunk != "Initial archive creation." and not repopulate_only:
                    diff_file = save_diff_record(name, diff_hunk)
                    diff_path = os.path.join(config.DIFF_DIR, diff_file)

                    analysis = calculate_similarity(diff_path, source_priority=priority)

                    if analysis.get('status') == 'success':
                        s3_outcome = 'filtered'
                        if analysis.get('should_handover'):
                            ts = datetime.datetime.now().isoformat()
                            new_packets = generate_handover_packets(
                                source_name=name,
                                priority=priority,
                                diff_file=diff_file,
                                analysis=analysis,
                                timestamp=ts,
                                version_id=current_id
                            )
                            handover_paths.extend(new_packets)
                            s3_outcome = 'handover'

                        s3_reason = analysis.get('handover_decision_reason') or analysis.get('filter_reason') or 'Stage 3 complete'
                        log_stage3_to_audit(
                            source_name=name,
                            priority=priority,
                            status="Success",
                            change_detected="Yes",
                            version_id=current_id or "",
                            diff_file=diff_file,
                            analysis=analysis,
                            outcome=s3_outcome,
                            reason=s3_reason
                        )
                    else:
                        log_to_audit(
                            name=name,
                            priority=priority,
                            status="Exception",
                            change_detected="Yes",
                            version_id=current_id or "",
                            diff_file=diff_file,
                            outcome="similarity_error",
                            reason=(analysis.get('message') or analysis.get('status') or 'Stage 3 failed')
                        )

                elif repopulate_only:
                    log_to_audit(name, priority, "Success", "Healed", current_id)
                else:
                    log_to_audit(name, priority, "Success", "Initial", current_id)
            else:
                log_to_audit(name, priority, "Success", "No", current_id)

        except Exception as e:
            logger.error(f"Failed {name}: {e}")
            log_to_audit(name, priority, "Exception", "N/A", current_id, reason=str(e))

    if driver:
        try:
            driver.quit()
        except Exception:
            pass

    # --- Stages 4 & 5 (LLM Verification and Suggestions) ---
    verification_paths: List[str] = []
    suggestion_paths: List[str] = []

    if handover_paths:
        logger.info(
            f"Running LLM verification on {len(handover_paths)} handover packet(s) "
            f"(top N candidates per packet = {config.TOP_N_VERIFICATION_CANDIDATES})."
        )
        verification_paths = run_llm_verification_for_packets(handover_paths)

        if verification_paths:
            logger.info(f"Wrote {len(verification_paths)} LLM verification result file(s) to {config.LLM_VERIFY_DIR}.")

            suggestion_paths = run_llm_update_suggestions_for_verification_files(verification_paths)

            if suggestion_paths:
                logger.info(f"Wrote {len(suggestion_paths)} Stage 5 update suggestion file(s) to {config.UPDATE_SUGGESTIONS_DIR}.")

                queue_file = "update_review_queue.csv"
                write_update_review_queue_csv_from_suggestion_files(suggestion_paths, output_path=queue_file)
                logger.info(f"Consolidated human review queue written to {queue_file}")

    write_current_run_manifest(handover_paths, verification_paths, suggestion_paths)
    write_github_summary(handover_paths)
