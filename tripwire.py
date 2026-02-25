
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
except Exception:
    OpenAI = None

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

TAGS_TO_EXCLUDE = ['nav', 'footer', 'header', 'script', 'style', 'aside', '.noprint', '#sidebar', 'iframe']

# Semantic scoring config
SEMANTIC_MODEL = 'text-embedding-3-small'
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
client = OpenAI(api_key=OPENAI_KEY) if (OpenAI and OPENAI_KEY) else None

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
    
    Args:
        source_name (str): The name of the source to lookup.
    Returns:
        Optional[str]: The last recorded Version_ID or None if not found.
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


def log_to_audit(name, priority, status, change_detected, version_id, diff_file=None,
                 similarity_score=None, power_words=None, matched_udid=None,
                 matched_chunk_id=None, outcome=None, reason=None):
    """
    Appends a new entry to the CSV audit log.
    
    Args:
        name (str): Source name.
        priority (str): Source priority level.
        status (str): Outcome status (Success/Exception).
        change_detected (str): Yes/No/Initial/Healed.
        version_id (str): Metadata ID from the source.
        diff_file (str): Filename of the generated diff hunk, if any.
        similarity_score (float, optional): Final semantic similarity score.
        power_words (list, optional): Power words detected in the diff.
        matched_udid (str, optional): Best-matching UDID from semantic analysis.
        matched_chunk_id (str, optional): Best-matching Chunk ID from semantic analysis.
        outcome (str, optional): 'handover' or 'filtered'.
        reason (str, optional): Explanation of the outcome.
    """
    file_exists = os.path.exists(AUDIT_LOG)
    headers = [
        'Timestamp', 'Source_Name', 'Priority', 'Status', 'Change_Detected',
        'Version_ID', 'Diff_File', 'Similarity_Score', 'Power_Words',
        'Matched_UDID', 'Matched_Chunk_ID', 'Outcome', 'Reason'
    ]
    with open(AUDIT_LOG, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(headers)
        writer.writerow([
            datetime.datetime.now().isoformat(),
            name,
            priority,
            status,
            change_detected,
            version_id or "N/A",
            diff_file or "N/A",
            f"{float(similarity_score):.4f}" if similarity_score is not None else "N/A",
            '; '.join(power_words) if power_words else "N/A",
            matched_udid or "N/A",
            matched_chunk_id or "N/A",
            outcome or "N/A",
            reason or "N/A"
        ])


def fetch_stage0_metadata(session, source) -> Optional[str]:
    """
    Performs a lightweight check to get the latest metadata ID without downloading full content.
    
    Args:
        session (requests.Session): Active HTTP session.
        source (dict): Source configuration dictionary.
    Returns:
        Optional[str]: RegisterId for legislation, ETag or Content-Length for others.
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
    
    Returns:
        webdriver.Chrome: Configured Selenium driver.
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
    
    Args:
        html (str): Raw HTML string.
    Returns:
        str: Cleaned HTML string containing only the body substance.
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
    
    Args:
        driver (webdriver.Chrome): Active Selenium driver.
        url (str): Target URL.
    Returns:
        str: Normalized Markdown content.
    """
    driver.get(url)
    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
    cleaned_html = clean_html_content(driver.page_source)
    return md(cleaned_html, heading_style="ATX")


def sanitize_rss(xml_content):
    """
    Normalizes RSS XML by stripping transient channel-level dates and sorting items by GUID.
    
    Args:
        xml_content (bytes/str): Raw RSS XML.
    Returns:
        str: Prettified, stable XML string.
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
    
    Args:
        session (requests.Session): Active HTTP session.
        source (dict): Source configuration.
    Returns:
        tuple: (registerId, full_meta_dict)
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
    
    Args:
        old_path (str): Path to the archived version.
        new_content (str): Newest normalized content.
    Returns:
        Optional[str]: The diff hunk if changes exist, otherwise None.
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
    
    Args:
        name (str): Source name.
        diff_content (str): The raw diff text.
    Returns:
        str: The generated filename of the diff record.
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
    Backwards-compatible change extractor. Phase B now parses hunks first and then
    flattens them into a single overall change context for logging/high-level signals.
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


def calculate_final_score(base_similarity, power_word_analysis):
    if isinstance(power_word_analysis, dict):
        boost = float(power_word_analysis.get('score', 0.0))
        weak_only = bool(power_word_analysis.get('weak_only', False))
        strong_count = int(power_word_analysis.get('strong_count', 0))
        if weak_only and float(base_similarity) < 0.20:
            boost = 0.0
        elif strong_count == 0 and float(base_similarity) < 0.10:
            boost = min(boost, 0.02)
    else:
        boost = float(power_word_analysis or 0.0)
    return min(1.0, float(base_similarity) + boost)


def get_primary_handover_threshold_for_priority(priority: str) -> Optional[float]:
    p = (priority or '').strip().lower()
    if p == 'high':
        return None  # bypass primary-score gate for high-priority sources
    if p == 'medium':
        return MEDIUM_PRIMARY_HANDOVER_THRESHOLD
    return LOW_PRIMARY_HANDOVER_THRESHOLD


def should_generate_handover(primary_score: float,
                             impact_count: int,
                             source_priority: str) -> Tuple[bool, str, Optional[float]]:
    """
    High-priority source: handover if any threshold-passing candidates exist.
    Medium/Low: require primary score to pass priority-specific threshold.
    """
    if impact_count <= 0:
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
                    'Chunk_Text': chunk_texts[i] if i < len(chunk_texts) else '',
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
    if client is None:
        raise RuntimeError("OpenAI client unavailable. Set OPENAI_API_KEY and install openai package.")
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
    if len(t) < 5: return True
    # Regex for "Page 1", "Page 2 of 10", etc.
    if re.match(r'^page \d+( of \d+)?$', t): return True
    # Regex for standard dates like "25 February 2026"
    if re.match(r'^\d{1,2} [a-z]+ \d{4}$', t): return True
    return False

def calculate_similarity(diff_path, source_priority='Low', mock_semantic_data=None):
    """
    Recall-first candidate retrieval with Administrative Noise filtering.
    """
    change = extract_change_content(diff_path)
    hunks = change.get('hunks', [])
    
    if not hunks:
        return {
            'status': 'no_content',
            'change_text': '',
            'change_hunks': [],
            'power_words': detect_power_words(''),
            'base_similarity': 0.0,
            'final_score': 0.0,
            'candidate_min_score': CANDIDATE_MIN_SCORE,
            'threshold_passing_candidates': [],
            'impacted_pages': [],
            'impact_count': 0,
            'multi_impact_likely': False,
            'should_handover': False,
            'handover_decision_reason': "No substantive hunks parsed",
            'primary_handover_threshold_used': get_primary_handover_threshold_for_priority(source_priority),
        }

    overall_power = detect_power_words(change.get('change_context', ''))
    
    # --- NOISE SUPPRESSION GATEKEEPER ---
    substantive_hunks = []
    for h in hunks:
        ctx = h.get('change_context', '')
        if _is_administrative_noise(ctx):
            h['is_noise'] = True
            h['power_words'] = {'found': [], 'score': 0.0, 'strong_count': 0, 'power_words_found': []}
            continue
        
        h['is_noise'] = False
        h['power_words'] = detect_power_words(ctx)
        substantive_hunks.append(h)

    # If everything was noise (e.g., only page numbers changed), return early with 0 score
    if not substantive_hunks:
        return {
            'status': 'success',
            'change_text': change.get('change_context', ''),
            'change_hunks': [{'hunk_index': h['hunk_index'], 'hunk_text': h.get('change_context', ''), 'is_noise': True} for h in hunks],
            'power_words': overall_power,
            'base_similarity': 0.0,
            'final_score': 0.0,
            'impact_count': 0,
            'should_handover': False,
            'handover_decision_reason': "All changes identified as administrative noise",
            'threshold_passing_candidates': [],
            'impacted_pages': []
        }

    # --- SEMANTIC PROCESSING ---
    try:
        # Only embed the substantive content to save cost and avoid false positives
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
            'impact_count': 0,
            'multi_impact_likely': False,
            'should_handover': False,
            'handover_decision_reason': f"Embedding error: {e}"
        }

    try:
        corpus_vectors, udids, chunk_texts, chunks_raw = _load_semantic_embeddings(mock_semantic_data=mock_semantic_data)
    except Exception as e:
        return {
            'status': 'missing_embeddings' if isinstance(e, FileNotFoundError) else 'similarity_error',
            'message': str(e),
            'change_text': change.get('change_context', ''),
            'change_hunks': [],
            'power_words': overall_power,
            'impacted_pages': [],
            'threshold_passing_candidates': [],
            'impact_count': 0,
            'multi_impact_likely': False,
            'should_handover': False,
            'handover_decision_reason': str(e)
        }

    similarity_matrix = cosine_similarity(hunk_vectors, corpus_vectors)

    hunk_matches = []
    page_acc: Dict[str, dict] = {}

    # Map similarities back to pages (using the idx from substantive_hunks)
    for s_idx, hunk in enumerate(substantive_hunks):
        scores = similarity_matrix[s_idx]
        passing_chunk_indices = np.where(scores >= HUNK_CHUNK_MIN_SIMILARITY)[0].tolist()

        diagnostic_indices = passing_chunk_indices.copy()
        if not diagnostic_indices and scores.size > 0:
            diagnostic_indices = [int(np.argmax(scores))]

        page_best_for_hunk: Dict[str, float] = {}
        hunk_chunk_summaries = []

        for ci in diagnostic_indices:
            score = float(scores[ci])
            raw = chunks_raw[ci] if chunks_raw and ci < len(chunks_raw) else {}
            udid = (udids[ci] if ci < len(udids) else raw.get('UDID')) or 'N/A'
            chunk_id = raw.get('Chunk_ID') or f"{udid}-UNK-{ci}"
            headline = raw.get('Headline_Alt') or raw.get('Page_Title') or ''

            passes = score >= HUNK_CHUNK_MIN_SIMILARITY
            hunk_chunk_summaries.append({
                'udid': udid, 'chunk_id': chunk_id, 'similarity': score,
                'headline_alt': headline, 'passes_chunk_threshold': passes
            })

            if not passes: continue

            page_best_for_hunk[udid] = max(page_best_for_hunk.get(udid, 0.0), score)

            rec = page_acc.setdefault(udid, {
                'udid': udid, 'chunk_hits': 0, 'matched_hunks': set(),
                'chunk_id_set': set(), 'chunk_ids': [], 'best_chunk_id': chunk_id,
                'best_chunk_similarity': score, 'best_headline': headline,
                'base_similarity': 0.0, 'avg_similarity_sum': 0.0,
            })

            rec['chunk_hits'] += 1
            rec['matched_hunks'].add(hunk['hunk_index'])
            if chunk_id not in rec['chunk_id_set']:
                rec['chunk_id_set'].add(chunk_id)
                rec['chunk_ids'].append(chunk_id)

            rec['avg_similarity_sum'] += score
            if score > rec['base_similarity']:
                rec['base_similarity'] = score
                rec['best_chunk_id'] = chunk_id
                rec['best_headline'] = headline

        hunk_matches.append({
            'hunk_index': hunk['hunk_index'],
            'hunk_header': hunk.get('header', ''),
            'change_text': hunk.get('change_context', ''),
            'power_words_found': hunk.get('power_words', {}).get('power_words_found', []),
            'top_chunks': sorted(hunk_chunk_summaries, key=lambda x: x['similarity'], reverse=True)[:5]
        })

    # --- SCORING & BONUSES ---
    impacted_pages = []
    for udid, rec in page_acc.items():
        distinct_hunks = len(rec['matched_hunks'])
        coverage_bonus = min(MAX_PAGE_COVERAGE_BONUS, max(0, distinct_hunks - 1) * PAGE_HUNK_COVERAGE_BONUS)
        density_bonus = min(MAX_PAGE_DENSITY_BONUS, max(0, rec['chunk_hits'] - 1) * PAGE_CHUNK_DENSITY_BONUS)

        power_adjusted = calculate_final_score(rec['base_similarity'], overall_power)
        power_uplift = max(0.0, power_adjusted - rec['base_similarity'])

        final_score = min(1.0, rec['base_similarity'] + coverage_bonus + density_bonus + power_uplift)

        impacted_pages.append({
            'udid': udid,
            'aggregated_base_similarity': float(rec['base_similarity']),
            'aggregated_final_score': float(final_score),
            'chunk_hits': rec['chunk_hits'],
            'distinct_hunk_hits': distinct_hunks,
            'matched_hunk_indices': sorted(rec['matched_hunks']),
            'relevant_chunk_ids': rec['chunk_ids'][:5],
            'best_chunk_id': rec['best_chunk_id'],
            'best_headline': rec['best_headline']
        })

    impacted_pages.sort(key=lambda p: (p['aggregated_final_score'], p['distinct_hunk_hits']), reverse=True)
    
    threshold_passing_candidates = [
        p for p in impacted_pages if p['aggregated_final_score'] >= CANDIDATE_MIN_SCORE
    ]

    primary = impacted_pages[0] if impacted_pages else None
    primary_score = float(primary['aggregated_final_score']) if primary else 0.0
    impact_count = len(threshold_passing_candidates)
    
    should_handover, handover_reason, primary_threshold_used = should_generate_handover(
        primary_score=primary_score,
        impact_count=impact_count,
        source_priority=source_priority
    )

    return {
        'status': 'success',
        'change_text': change.get('change_context', ''),
        # Every hunk, including noise, is preserved here for the UI
        'change_hunks': [
            {
                'hunk_index': h['hunk_index'],
                'hunk_header': h.get('header', ''),
                'hunk_text': h.get('change_context', ''),
                'is_noise': h.get('is_noise', False),
                'power_words_found': h.get('power_words', {}).get('power_words_found', [])
            }
            for h in hunks
        ],
        'power_words': overall_power,
        'base_similarity': float(primary['aggregated_base_similarity']) if primary else 0.0,
        'final_score': primary_score,
        'primary_udid': primary['udid'] if primary else None,
        'primary_chunk_id': primary.get('best_chunk_id') if primary else None,
        'primary_headline': primary.get('best_headline') if primary else None,
        
        # Diagnostics
        'hunk_matches': hunk_matches,
        'threshold_passing_candidates': threshold_passing_candidates,
        'impacted_pages': impacted_pages,
        'impact_count': impact_count,
        'multi_impact_likely': impact_count > 1,
        
        # Decisions
        'should_handover': should_handover,
        'handover_decision_reason': handover_reason,
        'filter_reason': None if should_handover else handover_reason,
        'primary_handover_threshold_used': primary_threshold_used,
        'candidate_min_score': CANDIDATE_MIN_SCORE,
        'hunk_chunk_min_similarity': HUNK_CHUNK_MIN_SIMILARITY
    }

# ---------------------------
# Packet generation (batched)
# ---------------------------

def _derive_packet_priority(priority: str, primary_score: float, power_count: int) -> str:
    # source priority can dominate urgency but preserve score influence
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


def generate_handover_packets(source_name: str, priority: str, diff_file: str,
                              analysis: dict, timestamp: str,
                              version_id: Optional[str] = None) -> List[str]:
    """
    Generates one or more JSON handover packets for LLM confirmation.
    All candidates with score >= CANDIDATE_MIN_SCORE are included across batches (no truncation).
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
    primary_score = float(analysis.get('final_score') or 0.0)
    packet_priority = _derive_packet_priority(priority, primary_score, power_count)
    primary_udid = analysis.get('primary_udid') or 'unknown'
    safe_ts = timestamp.replace(':', '').replace('.', '').replace('-', '')[:14]

    paths: List[str] = []

    for idx, batch in enumerate(batches, start=1):
        packet_id = f"handover_{safe_ts}_{primary_udid}_batch_{idx:02d}_of_{batch_count:02d}"
        filepath = os.path.join(HANDOVER_DIR, f"{packet_id}.json")

        packet = {
            'packet_id': packet_id,
            'generated_at': timestamp,
            'packet_priority': packet_priority,
            'source': {
                'name': source_name,
                'monitoring_priority': priority,
                'source_weight': _priority_to_source_weight(priority),
                'diff_file': diff_file,
                'diff_file_path': os.path.join(DIFF_DIR, diff_file),
                'timestamp_from_audit_log': timestamp,
                'version_id': version_id
            },
            'analysis': {
                'primary_similarity_score': primary_score,
                'primary_base_similarity': analysis.get('base_similarity'),
                'candidate_min_score': analysis.get('candidate_min_score', CANDIDATE_MIN_SCORE),
                'primary_handover_threshold_used': analysis.get('primary_handover_threshold_used'),
                'power_words_found': power.get('power_words_found', power.get('found', [])),
                'power_word_count': power_count,
                'power_word_score': power.get('score', 0.0),
                'impact_count': analysis.get('impact_count', 0),
                'multi_impact_likely': analysis.get('multi_impact_likely', False),
                'handover_decision_reason': analysis.get('handover_decision_reason')
            },
            'change': {
                'diff_file': diff_file,
                'preview': (analysis.get('change_text', '') or '')[:400],
                'hunks': analysis.get('change_hunks', [])
            },
            'llm_handover': {
                'purpose': 'JSON handover packet for the LLM to confirm whether the source change impacts candidate IPFR pages.',
                'retrieval_mode': 'high_recall_candidate_generation',
                'packeting_mode': 'candidate_batching_no_truncation_of_threshold_passing_candidates',
                'primary_udid': analysis.get('primary_udid'),
                'candidate_min_score': analysis.get('candidate_min_score', CANDIDATE_MIN_SCORE),
                'candidate_count_total': len(all_candidates),
                'candidate_count_in_packet': len(batch),
                'candidate_batch_index': idx,
                'candidate_batch_count': batch_count,
                'candidate_start_index': (idx - 1) * batch_size + 1,
                'candidate_end_index': (idx - 1) * batch_size + len(batch),
                'candidate_udids': [c.get('udid') for c in batch],
                'candidates': [
                    {
                        'udid': c.get('udid'),
                        'candidate_rank': c.get('candidate_rank'),
                        'candidate_score': c.get('aggregated_final_score'),
                        'base_similarity': c.get('aggregated_base_similarity'),
                        'matched_hunk_indices': c.get('matched_hunk_indices', []),
                        'relevant_chunk_ids': c.get('relevant_chunk_ids', []),
                        'best_chunk_id': c.get('best_chunk_id'),
                        'chunk_hit_count': c.get('chunk_hits'),
                        'distinct_hunk_hits': c.get('distinct_hunk_hits'),
                        'coverage_bonus': c.get('coverage_bonus'),
                        'density_bonus': c.get('density_bonus'),
                        'best_headline': c.get('best_headline')
                    }
                    for c in batch
                ],
                'hunk_matches': analysis.get('hunk_matches', [])
            }
        }

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(packet, f, indent=2, ensure_ascii=False)

        logger.info(f"LLM handover packet written: {os.path.basename(filepath)}")
        paths.append(filepath)

    return paths


def write_github_summary(handover_paths: List[str]):
    """
    Writes a markdown summary of this run's handover packets to the GitHub Actions
    job summary (GITHUB_STEP_SUMMARY). If unavailable, prints to stdout.
    """
    summary_file = os.environ.get('GITHUB_STEP_SUMMARY')
    lines = ["## Tripwire run summary\n"]

    if not handover_paths:
        lines.append("No handover packets generated this run.\n")
    else:
        lines.append(f"**{len(handover_paths)} handover packet(s) generated this run.**\n")
        lines.append("| Priority | Score | Source | Primary UDID | Diff file | Batch | Candidates |")
        lines.append("|---|---:|---|---|---|---|---:|")

        for p in handover_paths:
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    packet = json.load(f)
                prio = packet.get('packet_priority', '')
                score = packet.get('analysis', {}).get('primary_similarity_score')
                src = packet.get('source', {}).get('name', '')
                udid = packet.get('llm_handover', {}).get('primary_udid', '')
                diff_file = packet.get('source', {}).get('diff_file', '')
                lh = packet.get('llm_handover', {})
                batch = f"{lh.get('candidate_batch_index','?')}/{lh.get('candidate_batch_count','?')}"
                count = lh.get('candidate_count_in_packet', '')
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

LLM_HANDOVER_LOG = "llm_handover_log.csv"

def log_llm_handover_decision(source_name: str,
                              priority: str,
                              version_id: str,
                              diff_file: str,
                              analysis: dict,
                              packets_generated: int):
    """
    Records Stage 3 → LLM routing decisions.
    One row per semantic evaluation event.
    """

    file_exists = os.path.exists(LLM_HANDOVER_LOG)

    headers = [
        "Timestamp",
        "Source_Name",
        "Priority",
        "Version_ID",
        "Diff_File",
        "Analysis_Status",
        "Should_Handover",
        "Decision_Reason",
        "Final_Score",
        "Impact_Count",
        "Strong_Power_Words",
        "Power_Word_Score",
        "Candidate_Count",
        "Top_Candidate_UDIDs",
        "Packets_Generated"
    ]

    power = analysis.get("power_words", {}) or {}
    candidates = analysis.get("threshold_passing_candidates", []) or []

    row = [
        datetime.datetime.now().isoformat(),
        source_name,
        priority,
        version_id or "N/A",
        diff_file or "N/A",
        analysis.get("status"),
        analysis.get("should_handover"),
        analysis.get("handover_decision_reason"),
        analysis.get("final_score"),
        analysis.get("impact_count"),
        power.get("strong_count"),
        power.get("score"),
        len(candidates),
        ", ".join([c.get("udid", "N/A") for c in candidates[:10]]),
        packets_generated
    ]

    with open(LLM_HANDOVER_LOG, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow(headers)

        writer.writerow(row)


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

                    s3_success = analysis.get('status') == 'success'
                    s3_score = analysis.get('final_score') if s3_success else None
                    s3_words = analysis.get('power_words', {}).get('power_words_found') if s3_success else None
                    s3_udid = analysis.get('primary_udid') if s3_success else None
                    s3_chunk_id = analysis.get('primary_chunk_id') if s3_success else None
                    s3_outcome = None
                    s3_reason = analysis.get('handover_decision_reason') or analysis.get('message') or analysis.get('status')

                    if s3_success and analysis.get('should_handover'):
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
                        
                        log_llm_handover_decision(
                            source_name=name,
                            priority=priority,
                            version_id=current_id,
                            diff_file=diff_file,
                            analysis=analysis,
                            packets_generated=len(new_packets)
                        )
                    elif s3_success:
                        s3_outcome = 'filtered'

                        log_llm_handover_decision(
                            source_name=name,
                            priority=priority,
                            version_id=current_id,
                            diff_file=diff_file,
                            analysis=analysis,
                            packets_generated=0
                        )

                    log_to_audit(
                        name=name,
                        priority=priority,
                        status="Success",
                        change_detected="Yes",
                        version_id=current_id,
                        diff_file=diff_file,
                        similarity_score=s3_score,
                        power_words=s3_words,
                        matched_udid=s3_udid,
                        matched_chunk_id=s3_chunk_id,
                        outcome=s3_outcome,
                        reason=s3_reason
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
            'primary_score': result.get('final_score'),
            'impact_count': result.get('impact_count'),
            'multi_impact_likely': result.get('multi_impact_likely'),
            'should_handover': result.get('should_handover'),
            'handover_decision_reason': result.get('handover_decision_reason')
        }, indent=2))
        sys.exit(0)

    main()
