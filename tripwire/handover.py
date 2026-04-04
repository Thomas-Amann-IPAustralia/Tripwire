import json
import os
import re
from typing import List

from . import config


def _derive_packet_priority(priority: str, primary_score: float, power_count: int) -> str:
    p = (priority or '').strip().lower()
    if p == 'high':
        if primary_score >= 0.70 or power_count >= 5:
            return 'Critical'
        return 'High'
    if primary_score >= 0.75 or power_count >= 5:
        return 'Critical'
    if primary_score >= 0.60 or power_count >= 3:
        return 'High'
    return 'Medium'


def _clean_diff_text_line(s: str) -> str:
    """
    Defensive clean-up in case upstream diff text fragments carry leftover markers.
    For packet output we want clean human-readable lines.
    """
    if s is None:
        return ""
    t = str(s).strip()

    # remove common accidental prefixes
    # "+ - something" or "- - something"
    t = re.sub(r'^[\+\-]\s*-\s*', '', t)

    # remove leading '+'/'-' if present
    t = re.sub(r'^[\+\-]\s*', '', t)

    return t.strip()


def generate_handover_packets(source_name: str,
                             priority: str,
                             version_id: str,
                             diff_file: str,
                             analysis: dict,
                             timestamp: str) -> List[str]:
    """
    Generates one or more JSON handover packets in the revised, low-noise schema:
      - audit_summary
      - source_change_details
      - llm_verification_targets

    Packeting:
      - All threshold-passing candidates (score >= CANDIDATE_MIN_SCORE) are included.
      - Candidates are batched into MAX_CANDIDATES_PER_PACKET to control prompt size.
    """
    if not analysis or analysis.get('status') != 'success':
        return []

    all_candidates = list(analysis.get('threshold_passing_candidates') or [])
    if not all_candidates:
        return []

    os.makedirs(config.HANDOVER_DIR, exist_ok=True)

    batch_size = max(1, int(config.MAX_CANDIDATES_PER_PACKET))
    batches = [all_candidates[i:i + batch_size] for i in range(0, len(all_candidates), batch_size)]
    batch_count = len(batches)

    power = analysis.get('power_words', {}) or {}
    power_count = int(power.get('count', 0))
    primary_page_final_score = float(analysis.get('page_final_score') or 0.0)
    packet_priority = _derive_packet_priority(priority, primary_page_final_score, power_count)

    primary_udid = analysis.get('primary_udid') or 'unknown'
    safe_ts = timestamp.replace(':', '').replace('.', '').replace('-', '')[:14]

    # For audit/debug: keep variable names (and keep numeric thresholds in reason string already).
    p = (priority or '').strip().lower()
    threshold_name = None
    if p == 'medium':
        threshold_name = "MEDIUM_PRIMARY_HANDOVER_THRESHOLD"
    elif p == 'low':
        threshold_name = "LOW_PRIMARY_HANDOVER_THRESHOLD"
    elif p == 'high':
        threshold_name = None  # bypass

    # Primary candidate explanation (across *all* candidates, not only this batch)
    primary_candidate = all_candidates[0] if all_candidates else None
    primary_explanation = None
    if isinstance(primary_candidate, dict):
        primary_explanation = {
            "best_chunk_id": primary_candidate.get("best_chunk_id"),
            "matched_hunks": primary_candidate.get("matched_hunk_indices", []),
            "retrieval_reason": "Highest page_final_score among threshold-passing candidates"
        }

    paths: List[str] = []

    for idx, batch in enumerate(batches, start=1):
        packet_id = f"handover_{safe_ts}_{primary_udid}_batch_{idx:02d}_of_{batch_count:02d}"
        filepath = os.path.join(config.HANDOVER_DIR, f"{packet_id}.json")

        impacted_page_list = [c.get("udid") for c in batch if isinstance(c, dict) and c.get("udid")]

        # Convert analysis hunks to template shape
        hunks_out = []
        for h in (analysis.get("change_hunks") or []):
            if not isinstance(h, dict):
                continue
            hunks_out.append({
                "hunk_id": h.get("hunk_index"),
                "location_header": h.get("hunk_header", ""),
                "pre_context": [],
                "removed": [_clean_diff_text_line(x) for x in (h.get("removed") or []) if _clean_diff_text_line(x)],
                "added": [_clean_diff_text_line(x) for x in (h.get("added") or []) if _clean_diff_text_line(x)],
                "post_context": []
            })

        # Build llm verification targets (reference-first, include evidence_resolution)
        targets = []
        for c in batch:
            if not isinstance(c, dict):
                continue
            targets.append({
                "candidate_rank": c.get("candidate_rank"),
                "udid": c.get("udid"),
                "page_name": c.get("best_headline") or c.get("udid") or "",
                "page_final_score": c.get("page_final_score"),
                "best_chunk_id": c.get("best_chunk_id"),
                "matched_hunk_indices": c.get("matched_hunk_indices", []),
                "relevant_chunk_ids": (c.get("relevant_chunk_ids") or [])[:config.MAX_RELEVANT_CHUNK_IDS_PER_CANDIDATE],
                "evidence_resolution": {
                    "requires_resolution": True,
                    "resolve_from": [
                        "Semantic_Embeddings_Output.json",
                        "ipfr_markdown_archive"
                    ],
                    "fail_closed_if_missing": True
                }
            })

        packet = {
            "packet_id": packet_id,
            "packet_priority": packet_priority,

            "audit_summary": {
                "generated_at": timestamp,
                "primary_target_udid": primary_udid,

                "primary_page_final_score": primary_page_final_score,

                "primary_candidate_explanation": primary_explanation or {
                    "best_chunk_id": analysis.get("primary_chunk_id"),
                    "matched_hunks": [],
                    "retrieval_reason": "Primary candidate explanation unavailable"
                },

                "routing_decision": {
                    "decision_logic": analysis.get("handover_decision_reason") or "",
                    "candidate_min_score": "CANDIDATE_MIN_SCORE",
                    "primary_handover_threshold_used": threshold_name
                },

                "batching": {
                    "total_impacted_pages_across_batches": len(all_candidates),
                    "candidates_in_this_packet": len(batch),
                    "candidate_batch_index": idx,
                    "candidate_batch_count": batch_count,
                    "candidate_selection_policy": "all_threshold_passing_candidates"
                },

                "impacted_page_list": impacted_page_list
            },

            "source_change_details": {
                "source": {
                    "name": source_name,
                    "monitoring_priority": priority
                },
                "diff_file": diff_file,
                "timestamp_from_audit_log": timestamp,
                "version_id": version_id or "N/A",
                "power_words_found": power.get("power_words_found", power.get("found", [])),
                "hunks": hunks_out
            },

            "llm_verification_targets": targets,

            "llm_consumer_instructions": {
                "placeholder": "LLM consumer instructions will be defined in a later stage."
            }
        }

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(packet, f, indent=2, ensure_ascii=False)

        config.logger.info(f"LLM handover packet written: {os.path.basename(filepath)}")
        paths.append(filepath)

    return paths
