import csv
import json
import os
from typing import List

from .utils import format_relevant_diff_text


def build_update_review_queue_rows_from_payload(payload: dict, suggestion_file: str = "") -> List[dict]:
    """Flatten a Stage 5 suggestion payload into one review row per suggested change."""
    rows: List[dict] = []
    source_name = str(payload.get("source_name") or "")
    version_id = str(payload.get("version_id") or "")
    diff_file = str(payload.get("diff_file") or "")
    generated_at = str(payload.get("generated_at") or "")
    model_used = str(payload.get("model_used") or "")
    overall_status = str(payload.get("status") or "")

    for page in payload.get("pages", []) or []:
        if not isinstance(page, dict):
            continue
        udid = str(page.get("udid") or "")

        for suggestion in page.get("confirmed_update_suggestions", []) or []:
            if not isinstance(suggestion, dict):
                continue
            rows.append({
                "Run Timestamp": generated_at,
                "Source Name": source_name,
                "Version ID": version_id,
                "Diff File": diff_file,
                "UDID": udid,
                "Chunk ID": str(suggestion.get("chunk_id") or ""),
                "Suggestion Status": str(suggestion.get("status") or overall_status),
                "Update Required": str(suggestion.get("update_required", "")),
                "Relevant Diff Text": str(suggestion.get("relevant_diff_text") or ""),
                "Reason For Change": str(suggestion.get("reason") or ""),
                "Suggested Text": str(suggestion.get("proposed_replacement_text") or ""),
                "Model Used": model_used,
                "Update Suggestion File": suggestion_file or "",
            })

        for review_item in page.get("additional_chunks_to_review", []) or []:
            if not isinstance(review_item, dict):
                continue
            rows.append({
                "Run Timestamp": generated_at,
                "Source Name": source_name,
                "Version ID": version_id,
                "Diff File": diff_file,
                "UDID": udid,
                "Chunk ID": str(review_item.get("chunk_id") or ""),
                "Suggestion Status": "review_only",
                "Update Required": "",
                "Relevant Diff Text": format_relevant_diff_text(review_item.get("relevant_hunks") or []),
                "Reason For Change": str(review_item.get("reason") or review_item.get("evidence_quote") or ""),
                "Suggested Text": "",
                "Model Used": model_used,
                "Update Suggestion File": suggestion_file or "",
            })
    return rows


def write_update_review_queue_csv_from_suggestion_files(suggestion_paths: List[str], output_path: str = "update_review_queue.csv") -> str:
    """Write a flat, human-readable review queue from Stage 5 suggestion files."""
    headers = [
        "Run Timestamp", "Source Name", "Version ID", "Diff File", "UDID", "Chunk ID",
        "Suggestion Status", "Update Required", "Relevant Diff Text", "Reason For Change",
        "Suggested Text", "Model Used", "Update Suggestion File"
    ]
    rows: List[dict] = []
    for suggestion_path in suggestion_paths or []:
        if not suggestion_path or not os.path.exists(suggestion_path):
            continue
        with open(suggestion_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        rows.extend(build_update_review_queue_rows_from_payload(payload, suggestion_file=suggestion_path))

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            safe = {h: row.get(h, "") for h in headers}
            writer.writerow(safe)
    return output_path
