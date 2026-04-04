import json
import os
import re
from typing import Dict, List, Optional, Tuple

from . import config
from .config import logger
from .utils import canonical_chunk_id, format_relevant_diff_text, _now_iso, _list_to_semicolon
from .audit import update_audit_row_by_key, append_audit_row
from .llm_client import _call_llm_json
from .ipfr_content import resolve_ipfr_content_files, parse_markdown_chunks, _read_text_file
from .stage4_verify import _extract_confirmed_updates, _extract_additional_chunks_to_review


def _build_stage5_prompt(source_name: str,
                         diff_file: str,
                         udid: str,
                         chunk_id: str,
                         current_chunk_text: str,
                         relevant_hunks: List[dict]) -> str:
    hunk_text_parts = []
    for h in relevant_hunks or []:
        removed = "\n".join([f"- {ln}" for ln in (h.get("removed") or [])])
        added = "\n".join([f"+ {ln}" for ln in (h.get("added") or [])])
        header = h.get("location_header") or h.get("hunk_header") or f"hunk {h.get('hunk_id', '')}"
        hunk_text_parts.append(f"{header}\n{removed}\n{added}".strip())
    diff_block = "\n\n".join(hunk_text_parts).strip()

    return f"""System Role: You are a Technical Content Editor for the IP First Response (IPFR) platform.

Your task is to suggest the minimum necessary content update for one confirmed IPFR chunk.

Context:
- Source: {source_name}
- Diff file: {diff_file}
- UDID: {udid}
- Chunk ID: {chunk_id}

Confirmed external change:
{diff_block}

Current IPFR chunk text:
{current_chunk_text}

Instructions:
- Work on this chunk only.
- Preserve the heading and overall structure where possible.
- Make the minimum necessary change to keep the chunk accurate.
- Do not rewrite unrelated text.
- If the chunk is already accurate, set update_required to false and return the current text unchanged.
- Return full replacement text for the entire chunk.
- Keep the response concise and grounded in the source change.

Return JSON ONLY with this schema:
{{
  "udid": "{udid}",
  "chunk_id": "{chunk_id}",
  "update_required": true,
  "reason": "brief explanation",
  "proposed_replacement_text": "full replacement text for this chunk"
}}
"""


def _stage5_status_from_counts(success_count: int, failure_count: int, total_confirmed_chunks: int) -> str:
    if total_confirmed_chunks <= 0:
        return "No Update Required"
    if success_count > 0 and failure_count == 0:
        return "Suggestion Generated"
    if success_count > 0 and failure_count > 0:
        return "Partial Suggestion Generated"
    return "LLM Error"


def run_llm_update_suggestions_for_verification_files(
    verification_paths: List[str],
    prefer_test_files: bool = True
) -> List[str]:
    """Generate Stage 5 update suggestions from Stage 4 verification outputs.

    Aggregation unit:
    - one diff (Source_Name + Version_ID + Diff_File) -> one JSON suggestion artifact

    Prototype scope:
    - generate drafts ONLY for Pass 2 confirmed_update_chunk_ids
    - include additional_chunks_to_review as review-only metadata
    - do not patch markdown automatically
    """
    if not verification_paths:
        return []

    grouped: Dict[Tuple[str, str, str], dict] = {}

    for verification_path in verification_paths:
        try:
            if not verification_path or not os.path.exists(verification_path):
                continue

            with open(verification_path, "r", encoding="utf-8") as f:
                verification_doc = json.load(f)

            packet_id = verification_doc.get("packet_id") or ""
            if not packet_id:
                continue

            packet_path = os.path.join(config.HANDOVER_DIR, f"{packet_id}.json")
            if not os.path.exists(packet_path):
                logger.warning(f"Stage 5 skipped packet resolution for {verification_path}: packet not found.")
                continue

            with open(packet_path, "r", encoding="utf-8") as pf:
                packet = json.load(pf)

            src = (packet.get("source_change_details", {}) or {}).get("source", {}) or {}
            source_name = src.get("name", "") or ""
            version_id = (packet.get("source_change_details", {}) or {}).get("version_id", "") or ""
            diff_file = (packet.get("source_change_details", {}) or {}).get("diff_file", "") or ""
            packet_hunks = (packet.get("source_change_details", {}) or {}).get("hunks", []) or []

            group_key = (source_name, version_id, diff_file)
            group = grouped.setdefault(group_key, {
                "source_name": source_name,
                "version_id": version_id,
                "diff_file": diff_file,
                "pages": {},
            })

            for candidate in verification_doc.get("per_candidate", []) or []:
                if not isinstance(candidate, dict):
                    continue
                pass1 = candidate.get("pass1_result") or {}
                decision = str(pass1.get("decision") or "").strip().lower()
                if decision != "impact":
                    continue

                udid = str(candidate.get("udid") or "").strip()
                if not udid:
                    continue

                best_chunk_id = str(candidate.get("best_chunk_id") or pass1.get("chunk_id") or "").strip()
                pass2 = candidate.get("pass2_result") or {}

                confirmed_updates = _extract_confirmed_updates(
                    pass2,
                    fallback_chunk_id=best_chunk_id
                )
                additional_review = _extract_additional_chunks_to_review(pass2)

                candidate_level_hunks = set(candidate.get("matched_hunk_indices") or [])

                def _select_hunks_for_ids(hunk_ids):
                    hunk_ids = set(hunk_ids or [])
                    selected = [
                        h for h in packet_hunks
                        if (
                            (h.get("hunk_id") in hunk_ids)
                            or (h.get("hunk_index") in hunk_ids)
                        )
                    ]
                    if selected:
                        return selected

                    fallback = [
                        h for h in packet_hunks
                        if not candidate_level_hunks
                        or (h.get("hunk_id") in candidate_level_hunks or h.get("hunk_index") in candidate_level_hunks)
                    ]
                    return fallback or packet_hunks

                page = group["pages"].setdefault(udid, {
                    "udid": udid,
                    "confirmed_chunks": {},
                    "additional_chunks_to_review": {},
                })

                for item in confirmed_updates:
                    cid = str(item.get("chunk_id") or "").strip()
                    if not cid:
                        continue

                    relevant_hunks = _select_hunks_for_ids(item.get("matched_hunk_ids") or [])

                    rec = page["confirmed_chunks"].setdefault(cid, {
                        "chunk_id": cid,
                        "relevant_hunks": [],
                        "reason": "",
                        "evidence_quote": "",
                    })

                    if not rec["relevant_hunks"]:
                        rec["relevant_hunks"] = relevant_hunks
                    if item.get("reason") and not rec.get("reason"):
                        rec["reason"] = item.get("reason")
                    if item.get("evidence_quote") and not rec.get("evidence_quote"):
                        rec["evidence_quote"] = item.get("evidence_quote")

                for item in additional_review:
                    cid = str(item.get("chunk_id") or "").strip()
                    if not cid:
                        continue

                    review_item = dict(item)
                    review_item["relevant_hunks"] = _select_hunks_for_ids(item.get("matched_hunk_ids") or [])
                    page["additional_chunks_to_review"].setdefault(cid, review_item)

        except Exception as e:
            logger.error(f"Stage 5 aggregation failed for {verification_path}: {e}")

    if not grouped:
        return []

    os.makedirs(config.UPDATE_SUGGESTIONS_DIR, exist_ok=True)
    written_paths: List[str] = []

    for group_key, group in grouped.items():
        source_name, version_id, diff_file = group_key
        generated_at = _now_iso()
        safe_source = re.sub(r'[^A-Za-z0-9._-]+', '_', source_name or 'source').strip('_')[:80] or 'source'
        safe_version = re.sub(r'[^A-Za-z0-9._-]+', '_', version_id or 'version').strip('_')[:40] or 'version'
        out_name = f"update_{safe_source}_{safe_version}_{os.path.basename(diff_file) or 'diff'}.json"
        out_path = os.path.join(config.UPDATE_SUGGESTIONS_DIR, out_name)

        pages_out = []
        suggested_pairs: List[str] = []
        success_count = 0
        failure_count = 0
        total_confirmed_chunks = 0

        for udid, page in sorted((group.get("pages") or {}).items()):
            resolved = resolve_ipfr_content_files(udid, prefer_test_files=prefer_test_files)
            md_text = _read_text_file(resolved.get("markdown_path")) or ""
            chunks = parse_markdown_chunks(md_text)
            chunk_map = {
                canonical_chunk_id(c.get("chunk_id")): {
                    "chunk_id": c.get("chunk_id"),
                    "text": (c.get("text") or "").strip(),
                }
                for c in chunks
                if c.get("chunk_id")
            }

            confirmed_update_suggestions = []
            additional_review_out = sorted(page.get("additional_chunks_to_review", {}).values(), key=lambda x: x.get("chunk_id", ""))

            for chunk_id, chunk_meta in sorted((page.get("confirmed_chunks") or {}).items()):
                total_confirmed_chunks += 1
                canonical_id = canonical_chunk_id(chunk_id)
                resolved_chunk = chunk_map.get(canonical_id) or {}
                current_chunk_text = str(resolved_chunk.get("text") or "").strip()
                relevant_diff_text = format_relevant_diff_text(chunk_meta.get("relevant_hunks") or [])
                if not current_chunk_text:
                    failure_count += 1
                    confirmed_update_suggestions.append({
                        "chunk_id": chunk_id,
                        "resolved_markdown_chunk_id": str(resolved_chunk.get("chunk_id") or ""),
                        "update_required": False,
                        "reason": "Could not resolve authoritative markdown chunk text for this chunk_id.",
                        "relevant_diff_text": relevant_diff_text,
                        "proposed_replacement_text": "",
                        "status": "unresolved_chunk"
                    })
                    continue

                prompt = _build_stage5_prompt(
                    source_name=source_name,
                    diff_file=diff_file,
                    udid=udid,
                    chunk_id=chunk_id,
                    current_chunk_text=current_chunk_text,
                    relevant_hunks=chunk_meta.get("relevant_hunks") or [],
                )
                result = _call_llm_json(prompt, model=config.STAGE5_LLM_MODEL, fallback={
                    "update_required": False,
                    "reason": "LLM call failed or output was not valid JSON.",
                    "proposed_replacement_text": ""
                })
                update_required = bool(result.get("update_required", True))
                reason = str(result.get("reason") or "").strip()
                replacement = str(result.get("proposed_replacement_text") or "").strip()

                if not replacement:
                    replacement = current_chunk_text

                confirmed_update_suggestions.append({
                    "chunk_id": chunk_id,
                    "resolved_markdown_chunk_id": str(resolved_chunk.get("chunk_id") or chunk_id),
                    "update_required": update_required,
                    "reason": reason,
                    "relevant_diff_text": relevant_diff_text,
                    "proposed_replacement_text": replacement,
                    "status": "suggested" if reason else "suggested",
                })
                suggested_pairs.append(f"{udid}:{chunk_id}")
                success_count += 1

            pages_out.append({
                "udid": udid,
                "confirmed_update_suggestions": confirmed_update_suggestions,
                "additional_chunks_to_review": additional_review_out,
            })

        status = _stage5_status_from_counts(success_count, failure_count, total_confirmed_chunks)
        review_notes = "Drafts generated for confirmed chunks only; additional review chunks not drafted."
        if failure_count > 0 and success_count == 0:
            review_notes = "No drafts generated successfully; review unresolved chunks and Stage 5 logs."
        elif failure_count > 0:
            review_notes = "Some drafts generated successfully; some confirmed chunks could not be resolved from markdown."
        elif total_confirmed_chunks <= 0:
            review_notes = "No confirmed update chunks were available for Stage 5 drafting."

        payload = {
            "source_name": source_name,
            "version_id": version_id,
            "diff_file": diff_file,
            "generated_at": generated_at,
            "model_used": config.STAGE5_LLM_MODEL,
            "status": status,
            "pages": pages_out,
        }

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        updates = {
            "AI Update Suggestion Run": "Yes",
            "AI Update Suggestion Time": generated_at,
            "AI Update Suggestion Status": status,
            "AI Update Suggested Chunks": _list_to_semicolon(suggested_pairs),
            "AI Update Suggestions File": out_path,
            "AI Update Review Notes": review_notes,
        }
        updated = update_audit_row_by_key(source_name=source_name, version_id=version_id, diff_file=diff_file, updates=updates)
        if not updated:
            append_audit_row({
                "Timestamp": generated_at,
                "Source_Name": source_name,
                "Status": "Success",
                "Change_Detected": "Yes",
                "Version_ID": version_id,
                "Diff_File": diff_file,
                "Outcome": "update_suggestion_only",
                "Reason": "Audit row not found for Stage 5 update suggestion. Linked suggestion appended.",
                **updates,
            })

        written_paths.append(out_path)

    return written_paths
