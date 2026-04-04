import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from . import config
from .utils import canonical_chunk_id


def _resolve_ipfr_markdown_path(udid: str, prefer_test_files: bool = True) -> Optional[str]:
    """Resolve an IPFR markdown file for a UDID.

    Prototype support:
    - prefers *_test.md fixtures when present (for GitHub-based eval runs)
    - falls back to non-test files for normal operation
    """
    if not udid:
        return None
    root = Path(config.IPFR_CONTENT_ARCHIVE_DIR)

    patterns = []
    if prefer_test_files:
        patterns.extend([
            f"{udid} - *_test.md",
            f"{udid} - *test.md",
        ])
    patterns.append(f"{udid} - *.md")

    for pat in patterns:
        matches = sorted(root.glob(pat))
        if matches:
            return str(matches[0])
    return None


def _resolve_ipfr_jsonld_path(udid: str, prefer_test_files: bool = True) -> Optional[str]:
    """Resolve an IPFR JSON-LD file for a UDID.

    Prototype support:
    - prefers *_test.json fixtures when present
    - falls back to non-test files
    """
    if not udid:
        return None
    root = Path(config.IPFR_CONTENT_ARCHIVE_DIR)

    patterns = []
    if prefer_test_files:
        patterns.extend([
            f"{udid}_*_test.json",
            f"{udid}_*test.json",
        ])
    patterns.append(f"{udid}_*.json")

    for pat in patterns:
        matches = sorted(root.glob(pat))
        if matches:
            return str(matches[0])
    return None


def resolve_ipfr_content_files(udid: str, prefer_test_files: bool = True) -> dict:
    """Resolves the prototype IPFR content files for a UDID."""
    missing = []
    md_path = _resolve_ipfr_markdown_path(udid, prefer_test_files=prefer_test_files)
    js_path = _resolve_ipfr_jsonld_path(udid, prefer_test_files=prefer_test_files)

    if not md_path:
        missing.append("markdown")
    if not js_path:
        missing.append("jsonld")

    return {
        "udid": udid,
        "markdown_path": md_path,
        "jsonld_path": js_path,
        "missing": missing
    }

def parse_markdown_chunks(markdown_text: str) -> List[Dict[str, str]]:
    """Parse markdown into an ordered list of chunks using <!-- chunk_id: ... --> markers.

    Prototype behaviour:
    - Strip YAML frontmatter before parsing chunk markers so page metadata is not
      silently treated as chunk content.
    - If no chunk markers exist after frontmatter removal, return a single
      FULL_PAGE chunk.
    """
    if markdown_text is None:
        markdown_text = ""

    # Remove leading YAML frontmatter (--- ... ---) so page metadata like UDID,
    # URL, and title do not become implicit prelude content for Stage 4.
    markdown_text = re.sub(
        r"^---\s*\n.*?\n---\s*(?:\n|$)",
        "",
        markdown_text,
        count=1,
        flags=re.DOTALL,
    )

    pattern = r"<!--\s*chunk_id\s*:\s*([^>]+?)\s*-->"  # capture chunk id
    parts = re.split(pattern, markdown_text)

    # parts: [prelude, chunk_id1, chunk_text1, chunk_id2, chunk_text2, ...]
    if len(parts) < 3:
        return [{"chunk_id": "FULL_PAGE", "text": markdown_text.strip()}]

    chunks: List[Dict[str, str]] = []
    for i in range(1, len(parts), 2):
        chunk_id = (parts[i] or "").strip()
        text = (parts[i + 1] if i + 1 < len(parts) else "") or ""
        chunks.append({"chunk_id": chunk_id, "text": text.strip()})

    return chunks


def extract_chunk_window(
    chunks: List[Dict[str, str]],
    target_chunk_id: str,
    fallback_max_chars: int = 3000,
    side_max_chars: int = 800
) -> Dict[str, str]:
    """Return a markdown-sourced local evidence window for a target chunk id.

    Source of truth:
    - The markdown archive remains authoritative.
    - best_chunk_id is used only as a locator into the markdown chunk markers.

    Behaviour:
    - If the exact chunk_id is found, return the previous chunk as "before"
      (if any), the matched chunk as "current", and the next chunk as "after"
      (if any).
    - If the page has no chunk markers (FULL_PAGE), return the page text, truncated.
    - If the chunk_id is missing, fall back to concatenated page text, truncated.
    """
    if not chunks:
        return {"before": "", "current": "", "after": ""}

    def _clip(text: str, limit: int) -> str:
        text = (text or "").strip()
        if limit and len(text) > limit:
            return text[:limit].rstrip() + "\n\n[TRUNCATED]"
        return text

    # FULL_PAGE fallback when the markdown has no explicit chunk markers.
    if len(chunks) == 1 and chunks[0].get("chunk_id") == "FULL_PAGE":
        return {
            "before": "",
            "current": _clip(chunks[0].get("text", ""), fallback_max_chars),
            "after": ""
        }

    for i, c in enumerate(chunks):
        if c.get("chunk_id") == target_chunk_id:
            before_text = chunks[i - 1].get("text", "") if i > 0 else ""
            current_text = c.get("text", "")
            after_text = chunks[i + 1].get("text", "") if i + 1 < len(chunks) else ""
            return {
                "before": _clip(before_text, side_max_chars),
                "current": _clip(current_text, fallback_max_chars),
                "after": _clip(after_text, side_max_chars)
            }

    # Exact chunk id not found: fall back deterministically.
    joined = "\n\n".join([c.get("text", "") for c in chunks]).strip()
    return {"before": "", "current": _clip(joined, fallback_max_chars), "after": ""}

def build_chunk_index(chunks: List[Dict[str, str]], max_snippet_chars: int = 260) -> List[Dict[str, str]]:
    """Build a compact index of all chunks on a page for Pass 2 scoping.

    We avoid sending full page text; we send chunk_id + a short snippet.
    """
    index: List[Dict[str, str]] = []
    for c in chunks:
        cid = c.get("chunk_id", "")
        txt = (c.get("text", "") or "").strip()
        snippet = txt[:max_snippet_chars]
        index.append({"chunk_id": cid, "snippet": snippet})
    return index


def _normalise_chunk_id_list(chunk_ids: Optional[List[str]]) -> List[str]:
    """Return deduplicated chunk ids preserving original order."""
    out: List[str] = []
    seen = set()
    for raw in chunk_ids or []:
        cid = str(raw or "").strip()
        canon = canonical_chunk_id(cid)
        if not cid or not canon or canon in seen:
            continue
        out.append(cid)
        seen.add(canon)
    return out


def _read_text_file(path: str, max_chars: int = 40_000) -> str:
    if not path or not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        s = f.read()
    if max_chars and len(s) > max_chars:
        return s[:max_chars] + "\\n\\n[TRUNCATED]\\n"
    return s
