import datetime
import re
import unicodedata
from typing import List, Optional


def canonical_chunk_id(value: Optional[str]) -> str:
    """Return a tolerant canonical form for chunk_id comparisons.

    Minimum Stage 5 fix:
    - normalise unicode
    - replace non-breaking spaces with normal spaces
    - trim outer whitespace
    - collapse internal whitespace
    - compare case-insensitively
    """
    if value is None:
        return ""
    s = unicodedata.normalize("NFKC", str(value))
    s = s.replace("\u00A0", " ").replace("\u200B", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s.lower()


def format_relevant_diff_text(relevant_hunks: List[dict]) -> str:
    """Flatten matched diff hunks into a reviewer-friendly text block."""
    blocks: List[str] = []
    for idx, h in enumerate(relevant_hunks or [], start=1):
        removed = "\n".join([f"- {ln}" for ln in (h.get("removed") or []) if str(ln).strip()])
        added = "\n".join([f"+ {ln}" for ln in (h.get("added") or []) if str(ln).strip()])
        header = (h.get("location_header") or h.get("hunk_header") or f"hunk {h.get('hunk_id', idx)}").strip()
        body = "\n".join([part for part in [removed, added] if part.strip()]).strip()
        blocks.append(f"[{header}]\n{body}" if body else f"[{header}]")
    return "\n\n".join([b for b in blocks if b.strip()]).strip()


def _now_iso() -> str:
    return datetime.datetime.now().isoformat()


def _list_to_semicolon(values) -> str:
    vals = []
    for v in (values or []):
        if v is None:
            continue
        s = str(v).strip()
        if s:
            vals.append(s)
    return ';'.join(vals)


def _decision_to_human(decision: str) -> str:
    d = (decision or '').strip().lower()
    if d == 'impact':
        return 'Impact Confirmed'
    if d == 'no_impact':
        return 'No Impact'
    if d == 'uncertain':
        return 'Uncertain'
    return 'Error'


def _confidence_to_human(confidence: str) -> str:
    c = (confidence or '').strip().lower()
    if c in ('high', 'medium', 'low'):
        return c.capitalize()
    return ''


def _compute_overlap_metrics(predicted_udids: List[str], verified_udids: List[str]) -> dict:
    pred = set([u for u in (predicted_udids or []) if u])
    ver = set([u for u in (verified_udids or []) if u])

    inter = pred.intersection(ver)
    union = pred.union(ver)

    # Jaccard overlap
    overlap = (len(inter) / len(union)) if union else 1.0

    # Precision/Recall (handle empty denominators as "n/a" for monitoring clarity)
    precision = (len(inter) / len(pred)) if pred else None
    recall = (len(inter) / len(ver)) if ver else None

    details = f"intersection={len(inter)}; predicted={len(pred)}; verified={len(ver)}"

    return {
        "overlap": overlap,
        "precision": precision,
        "recall": recall,
        "details": details,
        "pred_set": sorted(pred),
        "ver_set": sorted(ver),
        "inter_set": sorted(inter),
    }
