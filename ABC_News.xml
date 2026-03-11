import json
import csv
import requests
import datetime
import os
import sys
import logging
import re
import difflib
from typing import List, Dict, Optional, Tuple

# --- Optional Web/Doc imports ---
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.by import By
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium_stealth import stealth
except Exception:
    webdriver = None
    ChromeService = None
    WebDriverWait = None
    EC = None
    By = None
    ChromeDriverManager = None
    stealth = None

from bs4 import BeautifulSoup
from markdownify import markdownify as md
import docx

# --- Optional OpenAI import ---
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# --- OpenAI client (lazy init; avoids missing bearer header in CI) ---
_client = None

def get_openai_client():
    """Lazily create and cache an OpenAI client.

    Ensures OPENAI_API_KEY is read at the moment the client is needed.
    """
    global _client

    if OpenAI is None:
        raise RuntimeError("OpenAI SDK not available. Ensure 'openai' is installed.")

    if _client is None:
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
        # Explicit is fine and avoids any ambiguity.
        _client = OpenAI(api_key=api_key)

    return _client

from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
import pandas as pd  # kept if used elsewhere / future compatibility

# --- Configuration ---
AUDIT_LOG = 'audit_log.csv'
SOURCES_FILE = 'sources.json'
OUTPUT_DIR = 'content_archive'
DIFF_DIR = 'diff_archive'
HANDOVER_DIR = 'handover_packets'
SEMANTIC_EMBEDDINGS_FILE = 'Semantic_Embeddings_Output.json'


# --- IPFR content archive for LLM verification (prototype) ---
# NOTE: Prototype only. We resolve UDIDs to content files by filename patterns within IPFR_CONTENT_ARCHIVE_DIR.
# In production, prefer an explicit UDID->file map generated during the IPFR export pipeline.
IPFR_CONTENT_ARCHIVE_DIR = os.environ.get("IPFR_CONTENT_ARCHIVE_DIR", "ipfr_content_archive")
LLM_VERIFY_DIR = os.environ.get("LLM_VERIFY_DIR", "llm_verification_results")

# Prototype behaviour: only load the top N candidates to keep prompts small.
# NOTE: This will need to be changed later to load the specific sections needed, not whole pages,
# and to support explicit per-candidate section targets.
TOP_N_VERIFICATION_CANDIDATES = int(os.environ.get("TOP_N_VERIFICATION_CANDIDATES", "3"))

# LLM verification execution
# Prototype requirement: always run verification after handover packets are generated.
# (If OPENAI_API_KEY is missing, verification will fail closed to overall_decision="uncertain".)
LLM_MODEL = os.environ.get("TRIPWIRE_LLM_MODEL", "gpt-4.1-mini")
TAGS_TO_EXCLUDE = ['nav', 'footer', 'header', 'script', 'style', 'aside', '.noprint', '#sidebar', 'iframe']

# Semantic scoring config
SEMANTIC_MODEL = 'text-embedding-3-small'
OPENAI_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip()
# NOTE: OpenAI client is created lazily via get_openai_client() to avoid missing auth headers in CI.
client = None

# Candidate / packet policy
CANDIDATE_MIN_SCORE = 0.35  # all page candidates >= this are "relevant" and must be handed over (across batches) if handover triggers
MEDIUM_PRIMARY_HANDOVER_THRESHOLD = 0.45
LOW_PRIMARY_HANDOVER_THRESHOLD = 0.50

# No top-k chunk cap: keep every chunk match >= threshold for evidence aggregation
HUNK_CHUNK_MIN_SIMILARITY = CANDIDATE_MIN_SCORE

# Packet/display controls (do not truncate threshold-passing candidates overall; batching handles overflow)
MAX_CANDIDATES_PER_PACKET = 50
MAX_RELEVANT_CHUNK_IDS_PER_CANDIDATE = 50
PER_HUNK_SUMMARY_LIMIT = 8

# Page aggregation bonuses
PAGE_HUNK_COVERAGE_BONUS = 0.04
MAX_PAGE_COVERAGE_BONUS = 0.12
PAGE_CHUNK_DENSITY_BONUS = 0.01
MAX_PAGE_DENSITY_BONUS = 0.06

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Tripwire")

_semantic_cache = None


# ---------------------------
# Audit / stage 0 helpers
# ---------------------------

def get_last_version_id(source_name: str) -> Optional[str]:
    """
    Retrieves the most recent successful Version_ID for a given source from the audit log.
    """
    if not os.path.exists(AUDIT_LOG):
        return None
    try:
        with open(AUDIT_LOG, mode='r', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        for row in reversed(rows):
            if row.get('Source_Name') == source_name and row.get('Status') == 'Success':
                return row.get('Version_ID')
    except Exception:
        return None
    return None



# --- Audit log schema (human-friendly, prototype) ---

AUDIT_HEADERS = [
    'Timestamp', 'Source_Name', 'Priority', 'Status', 'Change_Detected',
    'Version_ID', 'Diff_File', 'Similarity_Score', 'Power_Words',
    'Matched_UDID', 'Matched_Chunk_ID', 'Outcome', 'Reason',

    # Human-friendly AI verification linkage
    'AI Verification Run',
    'AI Verification Time',
    'AI Model Used',
    'AI Decision',
    'AI Confidence',
    'AI Change Summary',
    'AI Verification File',
    'Human Review Needed',

    # Monitoring / performance
    'Similarity Predicted Pages',
    'AI Verified Impact Pages',
    'AI vs Similarity Overlap Score',
    'AI vs Similarity Precision',
    'AI vs Similarity Recall',
    'Overlap Details'
]


def ensure_audit_log_headers() -> None:
    """Ensures audit_log.csv exists and contains all required headers.

    If the file exists with fewer columns, it is rewritten in-place with the new headers appended,
    preserving existing data.
    """
    if not os.path.exists(AUDIT_LOG):
        with open(AUDIT_LOG, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=AUDIT_HEADERS)
            writer.writeheader()
        return

    with open(AUDIT_LOG, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        existing_headers = reader.fieldnames or []
        rows = list(reader)

    if existing_headers == AUDIT_HEADERS:
        return

    # Build upgraded rows with new columns defaulting to blank
    upgraded = []
    for r in rows:
        nr = {h: '' for h in AUDIT_HEADERS}
        for k, v in (r or {}).items():
            if k in nr:
                nr[k] = v
        upgraded.append(nr)

    with open(AUDIT_LOG, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=AUDIT_HEADERS)
        writer.writeheader()
        writer.writerows(upgraded)


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


def append_audit_row(row: dict) -> None:
    ensure_audit_log_headers()
    safe = {h: '' for h in AUDIT_HEADERS}
    for k, v in (row or {}).items():
        if k in safe:
            safe[k] = v
    with open(AUDIT_LOG, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=AUDIT_HEADERS)
        writer.writerow(safe)


def update_audit_row_by_key(source_name: str, version_id: str, diff_file: str, updates: dict) -> bool:
    """Updates the *most recent* audit row matching (Source_Name, Version_ID, Diff_File)."""
    ensure_audit_log_headers()
    with open(AUDIT_LOG, mode='r', encoding='utf-8') as f:
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
        if k in AUDIT_HEADERS:
            rows[idx][k] = v

    with open(AUDIT_LOG, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=AUDIT_HEADERS)
        writer.writeheader()
        writer.writerows(rows)

    return True


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
    }
    append_audit_row(row)


def fetch_stage0_metadata(session, source) -> Optional[str]:
    """
    Performs a lightweight check to get the latest metadata ID without downloading full content.
    """
    stype = source.get('type')
    try:
        if stype == "Legislation_OData":
            params = {"$filter": f"titleid eq '{source['title_id']}'", "$orderby": "start desc", "$top": "1"}
            resp = session.get(source['base_url'], params=params, timeout=20)
            resp.raise_for_status()
            return resp.json().get('value', [{}])[0].get('registerId')
        elif stype in ["RSS", "WebPage"]:
            resp = session.head(source['url'], timeout=15)
            return resp.headers.get('ETag') or resp.headers.get('Content-Length')
    except Exception:
        return None
    return None


# ---------------------------
# Fetch / normalize helpers
# ---------------------------

def initialize_driver():
    """
    Initializes a headless Chrome driver with stealth settings to bypass anti-bot detection.
    """
    if webdriver is None:
        raise RuntimeError("Selenium/webdriver dependencies not available in this environment.")
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=chrome_options)
    if stealth:
        stealth(driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32", fix_hairline=True)
    return driver


def clean_html_content(html: str) -> str:
    """
    Strips non-essential HTML tags (nav, footer, etc.) and removes dynamic timestamps.
    """
    soup = BeautifulSoup(html, 'html.parser')
    body = soup.body
    if not body:
        return ""
    for selector in TAGS_TO_EXCLUDE:
        for tag in body.select(selector):
            tag.decompose()
    text = str(body)
    text = re.sub(r'Generated on:? \d{1,2}/\d{1,2}/\d{4}.*', '', text, flags=re.IGNORECASE)
    return text


def fetch_webpage_content(driver, url):
    """
    Uses Selenium to fetch a webpage, wait for rendering, and convert to Markdown.
    """
    driver.get(url)
    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
    cleaned_html = clean_html_content(driver.page_source)
    return md(cleaned_html, heading_style="ATX")


def sanitize_rss(xml_content):
    """
    Normalizes RSS XML by stripping transient channel-level dates and sorting items by GUID.
    """
    soup = BeautifulSoup(xml_content, 'xml')
    for tag in ['lastBuildDate', 'pubDate', 'generator']:
        t = soup.find(tag)
        if t and t.parent and t.parent.name == 'channel':
            t.decompose()
    items = soup.find_all('item')
    items.sort(key=lambda x: x.find('guid').text if x.find('guid') else (x.find('link').text if x.find('link') else ''))
    channel = soup.find('channel')
    if channel:
        for item in soup.find_all('item'):
            item.extract()
        for item in items:
            channel.append(item)
    return soup.prettify()


def fetch_legislation_metadata(session, source):
    """
    Fetches latest document metadata from the Federal Legislation OData API.
    """
    params = {"$filter": f"titleid eq '{source['title_id']}'", "$orderby": "start desc", "$top": "1"}
    resp = session.get(source['base_url'], params=params, timeout=30)
    resp.raise_for_status()
    val = resp.json().get('value', [])
    if not val:
        return None, None
    meta = val[0]
    return meta.get('registerId'), meta


def _extract_docx_text(docx_path: str) -> str:
    d = docx.Document(docx_path)
    lines = []
    for para in d.paragraphs:
        t = (para.text or '').strip()
        if t:
            lines.append(t)
    return "\n\n".join(lines)


def download_legislation_content(session, base_url, meta):
    candidate_urls = []
    for k in ['download', 'downloadUrl', 'Download', 'DownloadUrl', 'url', 'Url']:
        v = meta.get(k)
        if isinstance(v, str) and v.startswith('http'):
            candidate_urls.append(v)

    for k in ['documents', 'Documents', 'files', 'Files']:
        docs = meta.get(k)
        if isinstance(docs, list):
            for item in docs:
                if isinstance(item, dict):
                    for kk in ['downloadUrl', 'url', 'href']:
                        v = item.get(kk)
                        if isinstance(v, str) and v.startswith('http'):
                            candidate_urls.append(v)

    for url in candidate_urls:
        try:
            r = session.get(url, timeout=60)
            r.raise_for_status()
            ctype = (r.headers.get('Content-Type') or '').lower()

            if 'word' in ctype or url.lower().endswith('.docx'):
                tmp = os.path.join(OUTPUT_DIR, "_tmp_legislation_download.docx")
                with open(tmp, 'wb') as f:
                    f.write(r.content)
                text = _extract_docx_text(tmp)
                try:
                    os.remove(tmp)
                except Exception:
                    pass
                return text

            if 'html' in ctype:
                return md(clean_html_content(r.text), heading_style="ATX")

            try:
                return r.text
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"Legislation download candidate failed {url}: {e}")

    return json.dumps(meta, indent=2, sort_keys=True, ensure_ascii=False)


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
    path = os.path.join(OUTPUT_DIR, filename)
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
    path = os.path.join(DIFF_DIR, filename)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(diff_content)
    return filename


# ---------------------------
# Stage 3 helpers
# ---------------------------

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


def detect_power_words(text):
    tiers = {
        'strong': [
            r'\bmust\b', r'\bshall\b', r'\bpenalt(?:y|ies)\b', r'\bfines?\b',
            r'\$\d+(?:,\d+)*', r'\bprohibited\b', r'\bmandatory\b', r'\brequired\b',
            r'\bobligation\b', r'archives\s+act\s+1983'
        ],
        'moderate': [
            r'\bwithin\b', r'\bdeadline\b', r'\bdeadlines\b', r'\bnotice\b',
            r'\bservice\b', r'\bevidence\b', r'\bdeclaration\b'
        ],
        'weak': [
            r'\bmay\b', r'\d+\s*days?\b'
        ]
    }
    text_l = (text or '').lower()
    by_tier = {'strong': [], 'moderate': [], 'weak': []}
    for tier, patterns in tiers.items():
        for pat in patterns:
            matches = re.findall(pat, text_l, re.IGNORECASE)
            for m in matches:
                if m not in by_tier[tier]:
                    by_tier[tier].append(m)

    found = by_tier['strong'] + by_tier['moderate'] + by_tier['weak']
    strong_count = len(by_tier['strong'])
    moderate_count = len(by_tier['moderate'])
    weak_count = len(by_tier['weak'])
    weak_only = weak_count > 0 and strong_count == 0 and moderate_count == 0
    raw_score = min(0.35, strong_count * 0.08 + moderate_count * 0.04 + weak_count * 0.02)

    return {
        'found': found,
        'power_words_found': found,
        'by_tier': by_tier,
        'strong_count': strong_count,
        'moderate_count': moderate_count,
        'weak_count': weak_count,
        'count': len(found),
        'weak_only': weak_only,
        'score': raw_score
    }


def calculate_final_score(page_base_similarity, power_word_analysis):
    """
    Returns an adjusted similarity score (similarity + uplift), capped at 1.0.
    """
    if isinstance(power_word_analysis, dict):
        boost = float(power_word_analysis.get('score', 0.0))
        weak_only = bool(power_word_analysis.get('weak_only', False))
        strong_count = int(power_word_analysis.get('strong_count', 0))

        # Avoid boosting very-low similarity matches purely on weak words.
        if weak_only and float(page_base_similarity) < 0.20:
            boost = 0.0
        elif strong_count == 0 and float(page_base_similarity) < 0.10:
            boost = min(boost, 0.02)
    else:
        boost = float(power_word_analysis or 0.0)

    return min(1.0, float(page_base_similarity) + boost)


def get_primary_handover_threshold_for_priority(priority: str) -> Optional[float]:
    p = (priority or '').strip().lower()
    if p == 'high':
        return None  # bypass primary-score gate for high-priority sources
    if p == 'medium':
        return MEDIUM_PRIMARY_HANDOVER_THRESHOLD
    return LOW_PRIMARY_HANDOVER_THRESHOLD


def should_generate_handover(primary_score: float,
                             candidate_count: int,
                             source_priority: str) -> Tuple[bool, str, Optional[float]]:
    """
    Handover policy:
      - High-priority sources: hand over if any threshold-passing candidates exist.
      - Medium/Low: require primary score to pass a priority-specific threshold.
    """
    if candidate_count <= 0:
        return False, "No candidates passed candidate_min_score", get_primary_handover_threshold_for_priority(source_priority)

    threshold = get_primary_handover_threshold_for_priority(source_priority)
    p = (source_priority or '').strip().lower()

    if p == 'high':
        return True, "High priority source: handover triggered when threshold-passing candidates exist", None

    if threshold is None:
        return True, "No primary handover threshold configured", None

    ok = float(primary_score) >= float(threshold)
    return ok, (
        f"Primary score {primary_score:.3f} {'>=' if ok else '<'} "
        f"{p or 'default'} threshold {threshold:.3f}"
    ), threshold


def _load_semantic_embeddings(mock_semantic_data=None):
    global _semantic_cache
    if mock_semantic_data:
        vectors = np.array(mock_semantic_data['embeddings'])
        udids = mock_semantic_data['udids']
        chunk_texts = mock_semantic_data.get('chunk_texts', [''] * len(udids))
        chunks_raw = mock_semantic_data.get('chunks_raw')
        if chunks_raw is None:
            chunks_raw = []
            for i, udid in enumerate(udids):
                chunks_raw.append({
                    'UDID': udid,
                    'Chunk_ID': f"{udid}-C{i+1:02d}",
                    'Chunk_Text': chunk_texts[i] if i < len(udids) else '',
                    'Headline_Alt': ''
                })
        return vectors, udids, chunk_texts, chunks_raw

    if _semantic_cache is None:
        if not os.path.exists(SEMANTIC_EMBEDDINGS_FILE):
            raise FileNotFoundError(f"Semantic embeddings file not found: {SEMANTIC_EMBEDDINGS_FILE}")
        with open(SEMANTIC_EMBEDDINGS_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)

        vectors = []
        for item in raw:
            emb = item.get('Chunk_Embedding')
            if isinstance(emb, str):
                emb = json.loads(emb)
            vectors.append(emb)

        _semantic_cache = {
            'vectors': np.array(vectors),
            'udids': [item.get('UDID', 'N/A') for item in raw],
            'chunk_texts': [item.get('Chunk_Text', '') for item in raw],
            'chunks_raw': raw
        }
        logger.info(f"Loaded {len(raw)} semantic chunks from {SEMANTIC_EMBEDDINGS_FILE}")

    return _semantic_cache['vectors'], _semantic_cache['udids'], _semantic_cache['chunk_texts'], _semantic_cache['chunks_raw']


def _embed_texts(texts: List[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, 1536))
    client = get_openai_client()
    response = client.embeddings.create(input=texts, model=SEMANTIC_MODEL)
    return np.array([d.embedding for d in response.data])


def _priority_to_source_weight(priority: str) -> float:
    p = (priority or '').strip().lower()
    if p == 'high':
        return 1.0
    if p == 'medium':
        return 0.6
    return 0.3


def _is_administrative_noise(text: str) -> bool:
    """Returns True if the text is purely administrative (Page X, Dates, etc)."""
    t = text.strip().lower()
    if len(t) < 5:
        return True
    if re.match(r'^page \d+( of \d+)?$', t):
        return True
    if re.match(r'^\d{1,2} [a-z]+ \d{4}$', t):
        return True
    return False


def calculate_similarity(diff_path, source_priority='Low', mock_semantic_data=None):
    """
    Recall-first candidate retrieval with Administrative Noise filtering.
    """
    change = extract_change_content(diff_path)
    diff_hunks = change.get('hunks', [])

    if not diff_hunks:
        return {
            'status': 'no_content',
            'change_text': '',
            'change_hunks': [],
            'power_words': detect_power_words(''),
            'page_base_similarity': 0.0,
            'page_final_score': 0.0,
            'candidate_min_score': CANDIDATE_MIN_SCORE,
            'threshold_passing_candidates': [],
            'impacted_pages': [],
            'candidate_count': 0,
            'multi_impact_likely': False,
            'should_handover': False,
            'handover_decision_reason': "No substantive hunks parsed",
            'primary_handover_threshold_used': get_primary_handover_threshold_for_priority(source_priority),
        }

    overall_power = detect_power_words(change.get('change_context', ''))

    substantive_hunks = []
    for h in diff_hunks:
        ctx = h.get('change_context', '')
        if _is_administrative_noise(ctx):
            h['is_noise'] = True
            h['power_words'] = {'found': [], 'chunk_similarity': 0.0, 'strong_count': 0, 'power_words_found': []}
            continue

        h['is_noise'] = False
        h['power_words'] = detect_power_words(ctx)
        substantive_hunks.append(h)

    if not substantive_hunks:
        return {
            'status': 'success',
            'change_text': change.get('change_context', ''),
            'change_hunks': [
                {
                    'hunk_index': h['hunk_index'],
                    'hunk_header': h.get('header', ''),
                    'hunk_text': h.get('change_context', ''),
                    'is_noise': True,
                    # Structured fields for downstream packet formatting:
                    'removed': h.get('removed_lines', []),
                    'added': h.get('added_lines', [])
                }
                for h in diff_hunks
            ],
            'power_words': overall_power,
            'page_base_similarity': 0.0,
            'page_final_score': 0.0,
            'candidate_count': 0,
            'should_handover': False,
            'handover_decision_reason': "All changes identified as administrative noise",
            'threshold_passing_candidates': [],
            'impacted_pages': []
        }

    try:
        hunk_vectors = _embed_texts([h['change_context'] for h in substantive_hunks])
    except Exception as e:
        return {
            'status': 'error',
            'message': str(e),
            'change_text': change.get('change_context', ''),
            'change_hunks': [],
            'power_words': overall_power,
            'impacted_pages': [],
            'threshold_passing_candidates': [],
            'candidate_count': 0,
            'multi_impact_likely': False,
            'should_handover': False,
            'handover_decision_reason': f"Embedding error: {e}"
        }

    try:
        corpus_vectors, udids, chunk_texts, chunks_raw = _load_semantic_embeddings(
            mock_semantic_data=mock_semantic_data
        )
        _ = chunk_texts
    except Exception as e:
        return {
            'status': 'missing_embeddings' if isinstance(e, FileNotFoundError) else 'similarity_error',
            'message': str(e),
            'change_text': change.get('change_context', ''),
            'change_hunks': [],
            'power_words': overall_power,
            'impacted_pages': [],
            'threshold_passing_candidates': [],
            'candidate_count': 0,
            'multi_impact_likely': False,
            'should_handover': False,
            'handover_decision_reason': str(e)
        }

    similarity_matrix = cosine_similarity(hunk_vectors, corpus_vectors)

    hunk_matches = []
    page_evidence: Dict[str, dict] = {}

    for hunk_row_idx, hunk in enumerate(substantive_hunks):
        chunk_similarities = similarity_matrix[hunk_row_idx]
        passing_chunk_indices = np.where(chunk_similarities >= HUNK_CHUNK_MIN_SIMILARITY)[0].tolist()

        diagnostic_indices = passing_chunk_indices.copy()
        if not diagnostic_indices and chunk_similarities.size > 0:
            diagnostic_indices = [int(np.argmax(chunk_similarities))]

        chunk_match_summaries = []

        for chunk_idx in diagnostic_indices:
            chunk_similarity = float(chunk_similarities[chunk_idx])
            chunk_meta = chunks_raw[chunk_idx] if chunks_raw and chunk_idx < len(chunks_raw) else {}
            page_udid = (udids[chunk_idx] if chunk_idx < len(udids) else chunk_meta.get('UDID')) or 'N/A'
            chunk_id = chunk_meta.get('Chunk_ID') or f"{page_udid}-UNK-{chunk_idx}"
            headline = chunk_meta.get('Headline_Alt') or chunk_meta.get('Page_Title') or ''

            passes = chunk_similarity >= HUNK_CHUNK_MIN_SIMILARITY
            chunk_match_summaries.append({
                'udid': page_udid,
                'chunk_id': chunk_id,
                'chunk_similarity': chunk_similarity,
                'headline_alt': headline,
                'passes_chunk_threshold': passes
            })

            if not passes:
                continue

            page_rec = page_evidence.setdefault(page_udid, {
                'udid': page_udid,
                'chunk_hits': 0,
                'matched_hunks': set(),
                'chunk_id_set': set(),
                'chunk_ids': [],
                'best_chunk_id': chunk_id,
                'best_headline': headline,
                'page_base_similarity': 0.0,
            })

            page_rec['chunk_hits'] += 1
            page_rec['matched_hunks'].add(hunk['hunk_index'])

            if chunk_id not in page_rec['chunk_id_set']:
                page_rec['chunk_id_set'].add(chunk_id)
                page_rec['chunk_ids'].append(chunk_id)

            if chunk_similarity > page_rec['page_base_similarity']:
                page_rec['page_base_similarity'] = chunk_similarity
                page_rec['best_chunk_id'] = chunk_id
                page_rec['best_headline'] = headline

        hunk_matches.append({
            'hunk_index': hunk['hunk_index'],
            'hunk_header': hunk.get('header', ''),
            'change_text': hunk.get('change_context', ''),
            'power_words_found': hunk.get('power_words', {}).get('power_words_found', []),
            'top_chunks': sorted(chunk_match_summaries, key=lambda x: x['chunk_similarity'], reverse=True)[:5]
        })

    impacted_pages = []
    for page_udid, page_rec in page_evidence.items():
        distinct_hunks = len(page_rec['matched_hunks'])
        coverage_bonus = min(
            MAX_PAGE_COVERAGE_BONUS,
            max(0, distinct_hunks - 1) * PAGE_HUNK_COVERAGE_BONUS
        )
        density_bonus = min(
            MAX_PAGE_DENSITY_BONUS,
            max(0, page_rec['chunk_hits'] - 1) * PAGE_CHUNK_DENSITY_BONUS
        )

        power_adjusted = calculate_final_score(page_rec['page_base_similarity'], overall_power)
        power_uplift = max(0.0, power_adjusted - page_rec['page_base_similarity'])

        final_score = min(
            1.0,
            page_rec['page_base_similarity'] + coverage_bonus + density_bonus + power_uplift
        )

        impacted_pages.append({
            'udid': page_udid,
            'aggregated_page_base_similarity': float(page_rec['page_base_similarity']),
            'page_final_score': float(final_score),
            'chunk_hits': page_rec['chunk_hits'],
            'distinct_hunk_hits': distinct_hunks,
            'matched_hunk_indices': sorted(page_rec['matched_hunks']),
            'relevant_chunk_ids': page_rec['chunk_ids'][:MAX_RELEVANT_CHUNK_IDS_PER_CANDIDATE],
            'best_chunk_id': page_rec['best_chunk_id'],
            'best_headline': page_rec['best_headline'],
            'coverage_bonus': float(coverage_bonus),
            'density_bonus': float(density_bonus),
            'power_uplift': float(power_uplift),
        })

    impacted_pages.sort(key=lambda p: (p['page_final_score'], p['distinct_hunk_hits']), reverse=True)

    for rank, p in enumerate(impacted_pages, start=1):
        p['candidate_rank'] = rank

    threshold_passing_candidates = [
        p for p in impacted_pages if p['page_final_score'] >= CANDIDATE_MIN_SCORE
    ]

    primary = impacted_pages[0] if impacted_pages else None
    primary_page_final_score = float(primary['page_final_score']) if primary else 0.0
    candidate_count = len(threshold_passing_candidates)

    should_handover, handover_reason, primary_threshold_used = should_generate_handover(
        primary_score=primary_page_final_score,
        candidate_count=candidate_count,
        source_priority=source_priority
    )

    # NOTE: Preserve structured hunk info (removed/added arrays) for packet formatting.
    # This includes ALL hunks (noise + substantive), aligned with existing UI needs.
    change_hunks_structured = []
    for h in diff_hunks:
        change_hunks_structured.append({
            'hunk_index': h['hunk_index'],
            'hunk_header': h.get('header', ''),
            'hunk_text': h.get('change_context', ''),
            'is_noise': h.get('is_noise', False),
            'power_words_found': h.get('power_words', {}).get('power_words_found', []),
            'removed': h.get('removed_lines', []),
            'added': h.get('added_lines', []),
        })

    return {
        'status': 'success',
        'change_text': change.get('change_context', ''),
        'change_hunks': change_hunks_structured,
        'power_words': overall_power,
        'page_base_similarity': float(primary['aggregated_page_base_similarity']) if primary else 0.0,
        'page_final_score': primary_page_final_score,
        'primary_udid': primary['udid'] if primary else None,
        'primary_chunk_id': primary.get('best_chunk_id') if primary else None,
        'primary_headline': primary.get('best_headline') if primary else None,
        'hunk_matches': hunk_matches,
        'threshold_passing_candidates': threshold_passing_candidates,
        'impacted_pages': impacted_pages,
        'candidate_count': candidate_count,
        'multi_impact_likely': candidate_count > 1,
        'should_handover': should_handover,
        'handover_decision_reason': handover_reason,
        'filter_reason': None if should_handover else handover_reason,
        'primary_handover_threshold_used': primary_threshold_used,
        'candidate_min_score': CANDIDATE_MIN_SCORE,
        'hunk_chunk_min_similarity': HUNK_CHUNK_MIN_SIMILARITY
    }


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

    os.makedirs(HANDOVER_DIR, exist_ok=True)

    batch_size = max(1, int(MAX_CANDIDATES_PER_PACKET))
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
        filepath = os.path.join(HANDOVER_DIR, f"{packet_id}.json")

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
                "relevant_chunk_ids": (c.get("relevant_chunk_ids") or [])[:MAX_RELEVANT_CHUNK_IDS_PER_CANDIDATE],
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

        logger.info(f"LLM handover packet written: {os.path.basename(filepath)}")
        paths.append(filepath)

    return paths



# ---------------------------
# Stage 4 helpers (prototype): LLM verification (single-call, two-pass prompt)
# ---------------------------

from pathlib import Path  # local import to keep this file drop-in compatible


def _resolve_ipfr_markdown_path(udid: str, prefer_test_files: bool = True) -> Optional[str]:
    """Resolve an IPFR markdown file for a UDID.

    Prototype support:
    - prefers *_test.md fixtures when present (for GitHub-based eval runs)
    - falls back to non-test files for normal operation
    """
    if not udid:
        return None
    root = Path(IPFR_CONTENT_ARCHIVE_DIR)

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
    root = Path(IPFR_CONTENT_ARCHIVE_DIR)

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

def _read_text_file(path: str, max_chars: int = 40_000) -> str:
    if not path or not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        s = f.read()
    if max_chars and len(s) > max_chars:
        return s[:max_chars] + "\\n\\n[TRUNCATED]\\n"
    return s


def _build_llm_pass1_prompt(packet: dict, candidate: dict, window: dict) -> str:
    """Pass 1: Verify whether the external change materially impacts the candidate page.

    We intentionally provide a local evidence window centred on the target chunk
    to keep cost low while still giving the model nearby context.
    """
    packet_id = packet.get("packet_id", "")
    src = (packet.get("source_change_details", {}) or {}).get("source", {}) or {}
    source_name = src.get("name", "")
    priority = src.get("monitoring_priority", "")

    # Include only the hunks this candidate was matched to (if provided), else include all hunks.
    hunks = (packet.get("source_change_details", {}) or {}).get("hunks", []) or []
    matched = set(candidate.get("matched_hunk_indices") or [])
    if matched:
        hunks = [h for h in hunks if (h.get("hunk_id") in matched) or (h.get("hunk_index") in matched)]
    # Render hunks compactly.
    hunk_text_parts = []
    for h in hunks:
        removed = "\n".join([f"- {ln}" for ln in (h.get("removed") or [])])
        added = "\n".join([f"+ {ln}" for ln in (h.get("added") or [])])
        header = h.get("location_header") or h.get("hunk_header") or f"hunk {h.get('hunk_id', '')}"
        hunk_text_parts.append(f"{header}\n{removed}\n{added}".strip())
    diff_block = "\n\n".join(hunk_text_parts).strip()

    before = (window.get("before") or "").strip()
    current = (window.get("current") or "").strip()
    after = (window.get("after") or "").strip()

    return f"""System Role: You are a Technical Content Auditor for the IP First Response (IPFR) platform.

Your job is to verify whether the external source update below materially impacts the IPFR content for the candidate page.

External source context:
- Source: {source_name}
- Priority: {priority}
- Packet: {packet_id}

External change (unified diff hunks):
{diff_block}

Candidate IPFR page:
- UDID: {candidate.get('udid')}
- Target chunk_id: {candidate.get('best_chunk_id')}

IPFR local evidence window contains (from the actual page markdown):
[Before] – the preceding section of the page (if any)
[Current] – the target section that may require updating
[After] – the following section of the page (if any).

Treat [Current] as the only section that can trigger an update decision, and use [Before] and [After] for context if needed. 
[Before] and [After] must not be marked as impacted unless the change clearly alters the meaning of [Current].

[Before]
{before}

[Current]
{current}

[After]
{after}

Decision rules:
- Return "impact" if the external change updates, contradicts, or invalidates the meaning of the IPFR content in the evidence window.
- Return "no_impact" if the change is unrelated to the evidence window, or does not change the meaning relevant to the IPFR content.
- Return "uncertain" only if you cannot make a decision from the evidence provided.
- If the external change uses different wording but clearly changes a legal threshold/definition that the IPFR content relies on, treat that as "impact".

Return JSON ONLY with this schema:
{{
  "decision": "impact|no_impact|uncertain",
  "udid": "{candidate.get('udid')}",
  "chunk_id": "{candidate.get('best_chunk_id')}",
  "confidence": "high|medium|low",
  "reason": "brief explanation grounded in the evidence window",
  "evidence_quote": "a short quote (<=25 words) from the evidence window that supports your decision"
}}
"""


def _build_llm_pass2_prompt(
    packet: dict,
    candidate: dict,
    pass1_result: dict,
    chunk_index: List[dict],
    stage3_relevant_chunk_ids: Optional[List[str]] = None
) -> str:
    """Pass 2: confirm which Stage 3 suggested chunks actually need a human update (and allow additional chunks).

    We provide the LLM with:
      - Confirmed impact summary from Pass 1
      - A compact chunk index for the whole page (chunk_id + snippet)
      - Stage 3 'relevant_chunk_ids' (retrieval-suggested candidates) so the model doesn't start from scratch

    Output is used to seed Stage 5 (future) human/LLM authoring.
    """
    packet_id = packet.get("packet_id", "")
    src = (packet.get("source_change_details", {}) or {}).get("source", {}) or {}
    source_name = src.get("name", "")
    priority = src.get("monitoring_priority", "")

    hunks = (packet.get("source_change_details", {}) or {}).get("hunks", []) or []
    # Compact diff summary for scoping
    diff_lines: List[str] = []
    for h in hunks:
        for ln in (h.get("removed") or []):
            diff_lines.append(f"- {ln}")
        for ln in (h.get("added") or []):
            diff_lines.append(f"+ {ln}")
    diff_summary = "\n".join(diff_lines[:120])

    if stage3_relevant_chunk_ids is None:
        stage3_relevant_chunk_ids = candidate.get("relevant_chunk_ids") or []
    stage3_relevant_chunk_ids = [str(x) for x in stage3_relevant_chunk_ids][:80]

    # Include the compact index (id + snippet) for all chunks on this page.
    index_json = json.dumps(chunk_index, ensure_ascii=False)

    # Pass 1 summary (ground truth for Pass 2 scope)
    pass1_json = json.dumps(pass1_result, ensure_ascii=False)

    return f"""System Role: You are a Technical Content Auditor for the IP First Response (IPFR) platform.

Context:
- Packet: {packet_id}
- Source: {source_name}
- Priority: {priority}
- Candidate Page UDID: {candidate.get('udid','')}
- Candidate Best Chunk: {candidate.get('best_chunk_id','')}

You have already CONFIRMED there is a material impact for this candidate page (Pass 1 result below).

Your task now is to help a human update the page by confirming which specific chunks need review.

Inputs:
1) Confirmed impact (Pass 1):
{pass1_json}

2) Source change (compact diff):
{diff_summary}

3) Chunk index for the candidate page (chunk_id + snippet; snippets may be truncated):
{index_json}

4) Stage 3 retrieval-suggested chunk IDs (NOT confirmed; candidates only):
{json.dumps(stage3_relevant_chunk_ids, ensure_ascii=False)}

Instructions:
- Do NOT assume that Stage 3 suggested chunks need updates. They are only candidates.
- For each Stage 3 suggested chunk_id, decide if it likely requires a HUMAN update due to the confirmed impact.
- You may also nominate additional chunks not in the Stage 3 list, but ONLY if you can point to a snippet in the chunk index that shows why.
- Keep reasoning brief and evidence-based.
- Evidence quotes must be <= 25 words, copied from the chunk snippet where possible.

Return JSON ONLY in this shape:

{{
  "confirmed_update_chunk_ids": ["chunk_id", ...],
  "rejected_stage3_chunk_ids": ["chunk_id", ...],
  "additional_chunks_to_review": [
    {{
      "chunk_id": "...",
      "reason": "short explanation",
      "evidence_quote": "<=25 words from the snippet"
    }}
  ],
  "notes": "optional short notes for the human editor"
}}

"""


def _build_llm_verification_prompt(packet: dict, candidates_with_content: List[dict]) -> str:
    """Deprecated (kept for backwards compatibility).

    Stage 4 is now implemented as a two-pass per-candidate workflow:
    - Pass 1: verify impact using a narrow evidence window
    - Pass 2: scope additional chunks to review using a compact chunk index

    This function returns a minimal summary only.
    """
    packet_id = packet.get("packet_id", "")
    cand_list = [c.get("udid") for c in candidates_with_content if isinstance(c, dict)]
    return f"Tripwire Stage 4 uses two-pass verification. Packet {packet_id}. Candidates: {cand_list}"


def _call_llm_json(prompt: str) -> dict:
    """Call the LLM and parse a JSON response.

    Fail-closed behaviour:
    - If the client is unavailable or response is not parseable JSON, return an 'uncertain' decision.
    - Includes BOTH keys ('decision' and 'overall_decision') for compatibility with Pass 1/2 and legacy callers.
    """
    fallback = {
        "decision": "uncertain",
        "overall_decision": "uncertain",
        "confidence": "low",
        "reason": "LLM call failed or output was not valid JSON."
    }
    try:
        client = get_openai_client()
    except Exception as e:
        fallback["reason"] = f"LLM client unavailable: {e}"
        return fallback

    try:
        resp = client.responses.create(
            model=LLM_MODEL,
            input=prompt
        )
        txt = getattr(resp, "output_text", None)
        if not txt:
            return fallback

        # Try strict JSON first
        try:
            return json.loads(txt)
        except Exception:
            # Try to extract the first JSON object in the response
            m = re.search(r"\{.*\}", txt, flags=re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    return fallback
            return fallback
    except Exception as e:
        fallback["reason"] = f"LLM exception: {e}"
        return fallback

def verify_handover_packet_with_llm(packet_path: str, prefer_test_files: bool = True) -> Optional[str]:
    """Verify one handover packet using a two-pass LLM workflow (prototype).

    Prototype mode: loads TOP_N_VERIFICATION_CANDIDATES candidates from the packet.
    Note: when productionising, we will switch to verifying EVERY candidate.
    """
    if not packet_path or not os.path.exists(packet_path):
        return None

    with open(packet_path, "r", encoding="utf-8") as f:
        packet = json.load(f)

    targets = packet.get("llm_verification_targets", []) or []
    if not targets:
        return None

    # Select top N candidates (prototype)
    top_targets = [t for t in targets if isinstance(t, dict) and t.get("udid")]
    top_targets.sort(key=lambda x: (x.get("candidate_rank") is None, x.get("candidate_rank", 9999)))
    top_targets = top_targets[:max(1, TOP_N_VERIFICATION_CANDIDATES)]

    per_candidate_results = []
    impacted_pages = []
    any_uncertain = False
    missing_any = False

    for t in top_targets:
        udid = t.get("udid")
        resolved = resolve_ipfr_content_files(udid, prefer_test_files=prefer_test_files)
        if resolved.get("missing"):
            missing_any = True

        md_text = _read_text_file(resolved.get("markdown_path")) or ""
        chunks = parse_markdown_chunks(md_text)
        window = extract_chunk_window(chunks, t.get("best_chunk_id") or "")

        # ---- PASS 1 ----
        prompt1 = _build_llm_pass1_prompt(packet, t, window)
        pass1 = _call_llm_json(prompt1)

        # Normalise expected fields
        decision = (pass1.get("decision") or "uncertain").strip().lower()
        confidence = (pass1.get("confidence") or "").strip().lower()
        reason = (pass1.get("reason") or "").strip()

        if decision not in ("impact", "no_impact", "uncertain"):
            decision = "uncertain"

        if decision == "uncertain":
            any_uncertain = True

        pass2 = None
        review_ids: List[str] = []

        if decision == "impact":
            # ---- PASS 2 (scope review chunks) ----
            chunk_index = build_chunk_index(chunks)
            stage3_ids = t.get("relevant_chunk_ids") or []
            prompt2 = _build_llm_pass2_prompt(packet, t, pass1, chunk_index, stage3_relevant_chunk_ids=stage3_ids)
            pass2 = _call_llm_json(prompt2)

            # Backwards/forwards compatible extraction:
            # - New schema: confirmed_update_chunk_ids + additional_chunks_to_review
            # - Legacy schema: suggested_review_chunk_ids
            review_ids: List[str] = []

            legacy = pass2.get("suggested_review_chunk_ids")
            if isinstance(legacy, list) and legacy:
                review_ids = [str(x) for x in legacy]
            else:
                confirmed = pass2.get("confirmed_update_chunk_ids") or []
                if isinstance(confirmed, list):
                    review_ids.extend([str(x) for x in confirmed])

                additional = pass2.get("additional_chunks_to_review") or []
                if isinstance(additional, list):
                    for item in additional:
                        if isinstance(item, dict) and item.get("chunk_id"):
                            review_ids.append(str(item["chunk_id"]))

            # de-dup while preserving order
            seen = set()
            review_ids = [x for x in review_ids if not (x in seen or seen.add(x))]

            # Ensure verified chunk present
            verified_cid = (pass1.get("chunk_id") or t.get("best_chunk_id") or "").strip()
            if verified_cid and verified_cid not in review_ids:
                review_ids.insert(0, verified_cid)

            impacted_pages.append({
                "udid": udid,
                "chunk_id": (pass1.get("chunk_id") or t.get("best_chunk_id")),
                "confidence": confidence or "medium",
                "reason": reason or "External change impacts IPFR guidance.",
                "suggested_review_chunk_ids": review_ids
            })

        per_candidate_results.append({
            "udid": udid,
            "candidate_rank": t.get("candidate_rank"),
            "best_chunk_id": t.get("best_chunk_id"),
            "matched_hunk_indices": t.get("matched_hunk_indices", []),
            "resolved_files": resolved,
            "chunk_count": len(chunks),
            "evidence_window": window,
            "pass1_result": pass1,
            "pass2_result": pass2,
        })

    # ---- Aggregate overall decision ----
    # Confirmed impacts take precedence over unrelated missing candidates.
    if impacted_pages:
        overall_decision = "impact"
    elif missing_any:
        overall_decision = "uncertain"
    elif any_uncertain:
        overall_decision = "uncertain"
    else:
        overall_decision = "no_impact"

    llm_result = {
        "overall_decision": overall_decision,
        "confidence": ("high" if impacted_pages else ("low" if any_uncertain else "high")),
        "impacted_pages": impacted_pages,
        "note": "Prototype: verified top N candidates only. Production will verify every candidate."
    }

    os.makedirs(LLM_VERIFY_DIR, exist_ok=True)
    out_path = os.path.join(LLM_VERIFY_DIR, f"verification_{packet.get('packet_id','packet')}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "packet_id": packet.get("packet_id"),
            "verified_at": datetime.datetime.now().isoformat(),
            "top_n_candidates_loaded": TOP_N_VERIFICATION_CANDIDATES,
            "llm_result": llm_result,
            "per_candidate": per_candidate_results
        }, f, indent=2, ensure_ascii=False)

    return out_path



def run_llm_verification_for_packets(
    handover_paths: List[str],
    prefer_test_files: bool = True,
    top_n_candidates: Optional[int] = None
) -> List[str]:
    """
    Runs LLM verification for each packet (prototype) and links results back into audit_log.csv.

    Args:
        handover_paths: List of handover packet JSON paths.
        prefer_test_files: If True, verification will prefer *_test.md fixtures when resolving IPFR archive pages.
        top_n_candidates: Optional override for TOP_N_VERIFICATION_CANDIDATES (used to limit candidates passed to LLM).
    Returns:
        List of verification result JSON paths created.
    """
    global TOP_N_VERIFICATION_CANDIDATES

    if top_n_candidates is not None:
        try:
            TOP_N_VERIFICATION_CANDIDATES = int(top_n_candidates)
        except Exception:
            logger.warning(f"Ignoring invalid top_n_candidates={top_n_candidates!r}")

    if not handover_paths:
        return []

    results: List[str] = []

    for packet_path in handover_paths:
        try:
            if not packet_path or not os.path.exists(packet_path):
                continue

            with open(packet_path, "r", encoding="utf-8") as f:
                packet = json.load(f)

            src = (packet.get("source_change_details", {}) or {}).get("source", {}) or {}
            source_name = src.get("name", "") or ""
            priority = src.get("monitoring_priority", "") or ""
            version_id = (packet.get("source_change_details", {}) or {}).get("version_id", "") or ""
            diff_file = (packet.get("source_change_details", {}) or {}).get("diff_file", "") or ""
            packet_id = packet.get("packet_id", "") or ""

            # Verify via single LLM call (two-pass internal method)
            result_path = verify_handover_packet_with_llm(
                packet_path,
                prefer_test_files=prefer_test_files
            )
            if not result_path:
                continue

            verified_at = _now_iso()

            with open(result_path, "r", encoding="utf-8") as rf:
                verification_doc = json.load(rf)
            llm_res = (verification_doc.get("llm_result") or {})

            overall_decision = (llm_res.get("overall_decision") or "uncertain")
            confidence = (llm_res.get("confidence") or "")

            impacted = llm_res.get("impacted_pages") or []
            verified_udids = []
            verified_sections = []
            for it in impacted:
                if isinstance(it, dict):
                    u = it.get("udid")
                    if u:
                        verified_udids.append(u)
                    sid = it.get("section_identifier")
                    if sid:
                        verified_sections.append(f"{it.get('udid','')}: {sid}".strip())

            # Predicted set is ALL packet candidates (not just top N loaded) for monitoring retrieval performance.
            predicted_udids = []
            for t in (packet.get("llm_verification_targets") or []):
                if isinstance(t, dict) and t.get("udid"):
                    predicted_udids.append(t.get("udid"))

            metrics = _compute_overlap_metrics(predicted_udids, verified_udids)

            ai_decision_human = _decision_to_human(overall_decision)
            ai_conf_human = _confidence_to_human(confidence)

            # Make a short summary that is readable in the audit log.
            change_summary = (llm_res.get("external_change_summary") or "").strip()
            if not change_summary:
                change_summary = "AI verification completed."

            human_review = "Yes" if ai_decision_human in ("Impact Confirmed", "Uncertain", "Error") else "No"

            updates = {
                "AI Verification Run": "Yes",
                "AI Verification Time": verified_at,
                "AI Model Used": LLM_MODEL,
                "AI Decision": ai_decision_human,
                "AI Confidence": ai_conf_human,
                "AI Change Summary": change_summary[:500],
                "AI Verification File": result_path,
                "Human Review Needed": human_review,

                "AI Verified Impact Pages": _list_to_semicolon(metrics["ver_set"]),
                "AI vs Similarity Overlap Score": f"{metrics['overlap']:.3f}" if metrics.get("overlap") is not None else "",
                "AI vs Similarity Precision": (f"{metrics['precision']:.3f}" if metrics.get("precision") is not None else "n/a"),
                "AI vs Similarity Recall": (f"{metrics['recall']:.3f}" if metrics.get("recall") is not None else "n/a"),
                "Overlap Details": metrics.get("details") or "",
            }

            # Update the most recent matching audit row for this change event.
            updated = update_audit_row_by_key(
                source_name=source_name,
                version_id=version_id,
                diff_file=diff_file,
                updates=updates
            )
            if not updated:
                # If not found, append a minimal linking row rather than losing the verification.
                append_audit_row({
                    "Timestamp": verified_at,
                    "Source_Name": source_name,
                    "Priority": priority,
                    "Status": "Success",
                    "Change_Detected": "Yes",
                    "Version_ID": version_id,
                    "Diff_File": diff_file,
                    "Outcome": "verification_only",
                    "Reason": f"Audit row not found for packet {packet_id}. Linked verification appended.",
                    **updates
                })

            results.append(result_path)

        except Exception as e:
            logger.error(f"LLM verification failed for {packet_path}: {e}")

    return results


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




# ---------------------------
# Main loop
# ---------------------------

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(DIFF_DIR, exist_ok=True)

    if not os.path.exists(SOURCES_FILE):
        raise FileNotFoundError(f"Missing {SOURCES_FILE}")

    with open(SOURCES_FILE, 'r', encoding='utf-8') as f:
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
        out_path = os.path.join(OUTPUT_DIR, out_name)

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
                    diff_path = os.path.join(DIFF_DIR, diff_file)

                    analysis = calculate_similarity(diff_path, source_priority=priority)

                    analysis = calculate_similarity(diff_path, source_priority=priority)

                    # Stage 3 → write similarity scoring + routing decision into audit_log.csv (no llm_handover_log.csv)
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
                        # Similarity stage failed; still record the change event.
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

    # Stage 4 (prototype): always verify packets via a single LLM call
    if handover_paths:
        logger.info(
            f"Running LLM verification on {len(handover_paths)} handover packet(s) "
            f"(top N candidates per packet = {TOP_N_VERIFICATION_CANDIDATES})."
        )
        verification_paths = run_llm_verification_for_packets(handover_paths)
        if verification_paths:
            logger.info(f"Wrote {len(verification_paths)} LLM verification result file(s) to {LLM_VERIFY_DIR}.")

    write_github_summary(handover_paths)
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == '--test-stage3':
        if len(sys.argv) < 3:
            logger.error("Usage: python tripwire.py --test-stage3 <path_to_diff_file>")
            sys.exit(1)
        diff_file = sys.argv[2]
        if not os.path.exists(diff_file):
            logger.error(f"Diff file not found: {diff_file}")
            sys.exit(1)

        result = calculate_similarity(diff_file, source_priority='High')
        print(json.dumps({
            'status': result.get('status'),
            'primary_udid': result.get('primary_udid'),
            'primary_score': result.get('page_final_score'),
            'candidate_count': result.get('candidate_count'),
            'multi_impact_likely': result.get('multi_impact_likely'),
            'should_handover': result.get('should_handover'),
            'handover_decision_reason': result.get('handover_decision_reason')
        }, indent=2))
        sys.exit(0)

    main()
