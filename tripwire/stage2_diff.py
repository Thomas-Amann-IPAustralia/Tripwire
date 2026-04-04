import datetime
import difflib
import os
import re
from typing import List, Optional

from . import config


def get_diff(old_path, new_content) -> Optional[str]:
    """
    Performs a unified diff (-U10) between the archived file and the new content.
    """
    if not os.path.exists(old_path):
        return "Initial archive creation."
    with open(old_path, 'r', encoding='utf-8') as f:
        old_content = f.read()
    if old_content == new_content:
        return None
    diff_lines = difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=old_path,
        tofile='new_content',
        lineterm=''
    )
    diff_text = ''.join(diff_lines)
    return diff_text if diff_text.strip() else None


def save_to_archive(filename, content):
    path = os.path.join(config.OUTPUT_DIR, filename)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return path


def save_diff_record(source_name, diff_content):
    """
    Saves a diff hunk to the diff_archive directory with a timestamp.
    """
    safe_name = re.sub(r'[^A-Za-z0-9._-]+', '_', source_name)[:80].strip('_')
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{ts}_{safe_name}.diff"
    path = os.path.join(config.DIFF_DIR, filename)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(diff_content)
    return filename


def parse_diff_hunks(diff_file_path: str) -> List[dict]:
    """
    Parses a unified diff into hunk-level change objects so semantically distinct
    changes can be analysed independently (multi-impact detection).
    """
    hunks: List[dict] = []
    current = None
    with open(diff_file_path, 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.rstrip('\n')
            if line.startswith('@@'):
                if current:
                    current['change_context'] = ' '.join(current['removed_lines'] + current['added_lines']).strip()
                    hunks.append(current)
                current = {
                    'hunk_index': len(hunks) + 1,  # 1-based index
                    'header': line,
                    'added_lines': [],
                    'removed_lines': [],
                }
                continue
            if current is None:
                continue
            if line.startswith('+') and not line.startswith('+++'):
                current['added_lines'].append(line[1:].strip())
            elif line.startswith('-') and not line.startswith('---'):
                current['removed_lines'].append(line[1:].strip())

    if current:
        current['change_context'] = ' '.join(current['removed_lines'] + current['added_lines']).strip()
        hunks.append(current)

    return [h for h in hunks if h.get('change_context')]


def extract_change_content(diff_file_path):
    """
    Backwards-compatible change extractor. Parses hunks then flattens them.
    """
    hunks = parse_diff_hunks(diff_file_path)
    additions, removals = [], []
    for h in hunks:
        additions.extend(h.get('added_lines', []))
        removals.extend(h.get('removed_lines', []))
    return {
        'added': ' '.join(additions),
        'removed': ' '.join(removals),
        'change_context': ' '.join(removals + additions).strip(),
        'hunks': hunks
    }
