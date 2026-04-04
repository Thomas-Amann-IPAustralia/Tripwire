import json
import os
import datetime
from typing import List

from . import config
from .config import logger


def write_current_run_manifest(
    handover_paths: List[str],
    verification_paths: List[str],
    suggestion_paths: List[str],
) -> None:
    """Write an ephemeral manifest of files generated in this run.

    Used by the GitHub Actions workflow to upload only current-run artifacts
    rather than the entire historical folder contents.
    The manifest is excluded from git via .gitignore.
    """
    manifest = {
        "run_timestamp": datetime.datetime.now().isoformat(),
        "handover_packets": handover_paths or [],
        "verification_results": verification_paths or [],
        "update_suggestions": suggestion_paths or [],
    }
    with open(config.CURRENT_RUN_MANIFEST, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
    logger.info(f"Wrote current run manifest to {config.CURRENT_RUN_MANIFEST} "
                f"({len(handover_paths or [])} handover, "
                f"{len(verification_paths or [])} verification, "
                f"{len(suggestion_paths or [])} suggestion file(s)).")


def write_github_summary(handover_paths: List[str]):
    """
    Writes a markdown summary of this run's handover packets to the GitHub Actions
    job summary (GITHUB_STEP_SUMMARY). If unavailable, prints to stdout.

    Updated to match revised packet schema.
    """
    summary_file = os.environ.get('GITHUB_STEP_SUMMARY')
    lines = ["## Tripwire run summary\n"]

    if not handover_paths:
        lines.append("No handover packets generated this run.\n")
    else:
        lines.append(f"**{len(handover_paths)} handover packet(s) generated this run.**\n")
        lines.append("| Packet Priority | Primary Score | Source | Primary UDID | Diff file | Batch | Candidates |")
        lines.append("|---|---:|---|---|---|---|---:|")

        for p in handover_paths:
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    packet = json.load(f)

                prio = packet.get('packet_priority', '')
                audit = packet.get('audit_summary', {}) or {}
                score = audit.get('primary_page_final_score')
                src = (packet.get('source_change_details', {}) or {}).get('source', {}).get('name', '')
                udid = audit.get('primary_target_udid', '')
                diff_file = (packet.get('source_change_details', {}) or {}).get('diff_file', '')
                batching = audit.get('batching', {}) or {}
                batch = f"{batching.get('candidate_batch_index','?')}/{batching.get('candidate_batch_count','?')}"
                count = batching.get('candidates_in_this_packet', '')

                score_fmt = f"{float(score):.3f}" if score is not None else ""
                lines.append(f"| {prio} | {score_fmt} | {src} | {udid} | {diff_file} | {batch} | {count} |")

            except Exception as e:
                lines.append(f"| Error | | | | {os.path.basename(p)} | | ({e}) |")

    output = "\n".join(lines) + "\n"
    if summary_file:
        with open(summary_file, 'a', encoding='utf-8') as f:
            f.write(output)
        logger.info(f"Wrote GitHub Actions summary to {summary_file}")
    else:
        print(output)
