import csv
import os
from typing import Optional

from . import config
from .utils import _now_iso, _list_to_semicolon


def get_last_version_id(source_name: str) -> Optional[str]:
    """
    Retrieves the most recent successful Version_ID for a given source from the audit log.
    """
    if not os.path.exists(config.AUDIT_LOG):
        return None
    try:
        with open(config.AUDIT_LOG, mode='r', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        for row in reversed(rows):
            if row.get('Source_Name') == source_name and row.get('Status') == 'Success':
                return row.get('Version_ID')
    except Exception:
        return None
    return None


def ensure_audit_log_headers() -> None:
    """Ensures audit_log.csv exists and contains all required headers.

    If the file exists with fewer columns, it is rewritten in-place with the new headers appended,
    preserving existing data.
    """
    if not os.path.exists(config.AUDIT_LOG):
        with open(config.AUDIT_LOG, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=config.AUDIT_HEADERS)
            writer.writeheader()
        return

    with open(config.AUDIT_LOG, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        existing_headers = reader.fieldnames or []
        rows = list(reader)

    if existing_headers == config.AUDIT_HEADERS:
        return

    # Build upgraded rows with new columns defaulting to blank
    upgraded = []
    for r in rows:
        nr = {h: '' for h in config.AUDIT_HEADERS}
        for k, v in (r or {}).items():
            if k in nr:
                nr[k] = v
        upgraded.append(nr)

    with open(config.AUDIT_LOG, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=config.AUDIT_HEADERS)
        writer.writeheader()
        writer.writerows(upgraded)


def append_audit_row(row: dict) -> None:
    ensure_audit_log_headers()
    safe = {h: '' for h in config.AUDIT_HEADERS}
    for k, v in (row or {}).items():
        if k in safe:
            safe[k] = v
    with open(config.AUDIT_LOG, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=config.AUDIT_HEADERS)
        writer.writerow(safe)


def update_audit_row_by_key(source_name: str, version_id: str, diff_file: str, updates: dict) -> bool:
    """Updates the *most recent* audit row matching (Source_Name, Version_ID, Diff_File)."""
    ensure_audit_log_headers()
    with open(config.AUDIT_LOG, mode='r', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    idx = None
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        if (r.get('Source_Name') == (source_name or '') and
            r.get('Version_ID') == (version_id or '') and
            r.get('Diff_File') == (diff_file or '')):
            idx = i
            break

    if idx is None:
        return False

    for k, v in (updates or {}).items():
        if k in config.AUDIT_HEADERS:
            rows[idx][k] = v

    with open(config.AUDIT_LOG, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=config.AUDIT_HEADERS)
        writer.writeheader()
        writer.writerows(rows)

    return True


def log_stage3_to_audit(source_name: str,
                        priority: str,
                        status: str,
                        change_detected: str,
                        version_id: str,
                        diff_file: str,
                        analysis: dict,
                        outcome: str,
                        reason: str) -> None:
    """Writes the Stage 3 similarity + handover decision into audit_log.csv (prototype).

    NOTE: matched_udid / matched_chunk_id are stored as *candidate lists* (not only the primary).
    """
    power = (analysis or {}).get('power_words', {}) or {}
    candidates = (analysis or {}).get('threshold_passing_candidates', []) or []

    candidate_udids = []
    candidate_pairs = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        u = c.get('udid')
        b = c.get('best_chunk_id')
        if u:
            candidate_udids.append(u)
            if b:
                candidate_pairs.append(f"{u}:{b}")
            else:
                candidate_pairs.append(f"{u}:")

    # Keep existing schema fields populated for compatibility
    row = {
        'Timestamp': _now_iso(),
        'Source_Name': source_name or '',
        'Priority': priority or '',
        'Status': status or '',
        'Change_Detected': change_detected or '',
        'Version_ID': version_id or '',
        'Diff_File': diff_file or '',
        'Similarity_Score': f"{float((analysis or {}).get('page_final_score') or 0.0):.4f}" if (analysis or {}).get('status') == 'success' else '',
        'Power_Words': _list_to_semicolon(power.get('power_words_found', power.get('found', []))),
        'Matched_UDID': _list_to_semicolon(candidate_udids),
        'Matched_Chunk_ID': _list_to_semicolon(candidate_pairs),
        'Outcome': outcome or '',
        'Reason': reason or '',

        # AI columns default
        'AI Verification Run': 'No',
        'Human Review Needed': 'No',
        'Similarity Predicted Pages': _list_to_semicolon(candidate_udids),
        'AI Update Suggestion Run': 'No',
    }
    append_audit_row(row)


def log_to_audit(name, priority, status, change_detected, version_id, diff_file=None,
                 similarity_score=None, power_words=None, matched_udid=None,
                 matched_chunk_id=None, outcome=None, reason=None):
    """Backwards-compatible audit append.

    For Stage 3 similarity/LLM-routing, prefer log_stage3_to_audit(...).
    """
    row = {
        'Timestamp': _now_iso(),
        'Source_Name': name or '',
        'Priority': priority or '',
        'Status': status or '',
        'Change_Detected': change_detected or '',
        'Version_ID': version_id or '',
        'Diff_File': diff_file or '',
        'Similarity_Score': f"{float(similarity_score):.4f}" if similarity_score is not None else '',
        'Power_Words': _list_to_semicolon(power_words) if isinstance(power_words, list) else (power_words or ''),
        'Matched_UDID': matched_udid or '',
        'Matched_Chunk_ID': matched_chunk_id or '',
        'Outcome': outcome or '',
        'Reason': reason or '',
        'AI Verification Run': 'No',
        'Human Review Needed': 'No',
        'AI Update Suggestion Run': 'No',
    }
    append_audit_row(row)
