import json
import csv
import requests
import datetime
import os
import sys
import logging
import re
import subprocess
from typing import List, Dict, Optional

# --- Web Scrape & Document Imports ---
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from selenium_stealth import stealth
from bs4 import BeautifulSoup
from markdownify import markdownify as md
import docx

# --- Stage 3: Semantic Analysis Imports ---
from openai import OpenAI
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
import pandas as pd

# --- Configuration ---
AUDIT_LOG = 'audit_log.csv' 
SOURCES_FILE = 'sources.json'
OUTPUT_DIR = 'content_archive'
DIFF_DIR = 'diff_archive'
TAGS_TO_EXCLUDE = ['nav', 'footer', 'header', 'script', 'style', 'aside', '.noprint', '#sidebar', 'iframe']

# --- Stage 3 Configuration ---
SEMANTIC_MODEL = 'text-embedding-3-small' 
SIMILARITY_THRESHOLD = 0.45  # Initial threshold, tune based on testing
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
client = OpenAI(api_key=OPENAI_KEY)

# Phase 3: Semantic Embeddings
SEMANTIC_EMBEDDINGS_FILE = 'Semantic_Embeddings_Output.json'
HANDOVER_DIR = 'handover_packets'
_semantic_cache = None  # Module-level cache so embeddings load once per run

# Phase B: Multi-impact retrieval & aggregation
TOP_K_CHUNKS_PER_HUNK = 12
MAX_IMPACT_PAGES_IN_PACKET = 10
MAX_TOP_CHUNKS_IN_PACKET = 25
MULTI_IMPACT_MIN_SCORE = max(0.35, SIMILARITY_THRESHOLD - 0.05)
PAGE_HUNK_COVERAGE_BONUS = 0.03   # per additional hunk matched by the same page
PAGE_CHUNK_DENSITY_BONUS = 0.01   # per additional supporting chunk matched by the same page
MAX_PAGE_COVERAGE_BONUS = 0.08
MAX_PAGE_DENSITY_BONUS = 0.05

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Tripwire")

# --- Stage 0: Helpers ---

def get_last_version_id(source_name: str) -> Optional[str]:
    """
    Retrieves the most recent successful Version_ID for a given source from the audit log.
    
    Args:
        source_name (str): The name of the source to lookup.
    Returns:
        Optional[str]: The last recorded Version_ID or None if not found.
    """
    if not os.path.exists(AUDIT_LOG): return None
    try:
        with open(AUDIT_LOG, mode='r', encoding='utf-8') as f:
            reader = list(csv.DictReader(f))
            for row in reversed(reader):
                if row['Source_Name'] == source_name and row['Status'] == 'Success':
                    return row['Version_ID']
    except Exception: return None
    return None

def log_to_audit(name, priority, status, change_detected, version_id, diff_file=None,
                 similarity_score=None, power_words=None, matched_udid=None,
                 matched_chunk_id=None, outcome=None, reason=None):
    """
    Appends a new entry to the CSV audit log.
    
    Stage 2 fields are always written. Stage 3 fields (similarity_score through reason)
    are only populated when semantic analysis has been performed on a diff.
    
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
            datetime.datetime.now().isoformat(), name, priority, status,
            change_detected, version_id, diff_file or "N/A",
            f"{similarity_score:.4f}" if similarity_score is not None else "N/A",
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
            return resp.json().get('value', [{}])[0].get('registerId')
        elif stype in ["RSS", "WebPage"]:
            resp = session.head(source['url'], timeout=15)
            return resp.headers.get('ETag') or resp.headers.get('Content-Length')
    except Exception: return None
    return None

# --- Extraction & Normalization Functions ---

def initialize_driver():
    """
    Initializes a headless Chrome driver with stealth settings to bypass anti-bot detection.
    
    Returns:
        webdriver.Chrome: Configured Selenium driver.
    """
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=chrome_options)
    stealth(driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32", fix_hairline=True)
    return driver

def clean_html_content(html):
    """
    Strips non-essential HTML tags (nav, footer, etc.) and removes dynamic timestamps.
    
    Args:
        html (str): Raw HTML string.
    Returns:
        str: Cleaned HTML string containing only the body substance.
    """
    soup = BeautifulSoup(html, 'html.parser')
    body = soup.body
    if not body: return ""
    for selector in TAGS_TO_EXCLUDE:
        for tag in body.select(selector): tag.decompose()
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
        if t and t.parent.name == 'channel': t.decompose()
    items = soup.find_all('item')
    items.sort(key=lambda x: x.find('guid').text if x.find('guid') else (x.find('link').text if x.find('link') else ''))
    channel = soup.find('channel')
    if channel:
        for item in soup.find_all('item'): item.extract()
        for item in items: channel.append(item)
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
    docs = resp.json().get('value', [])
    return (docs[0].get('registerId'), docs[0]) if docs else (None, None)

def download_legislation_content(session, base_url, doc_meta):
    """
    Downloads a Word document from OData API and converts it to Markdown.
    
    Args:
        session (requests.Session): Active HTTP session.
        base_url (str): API base URL.
        doc_meta (dict): Metadata for the specific document version.
    Returns:
        str: Normalized Markdown text extracted from the document.
    """
    from io import BytesIO
    def q(val): return f"'{val}'"
    reg_id = doc_meta.get('registerId')
    download_url = f"{base_url}/find(registerId={q(reg_id)},type={q(doc_meta.get('type'))},format='Word',uniqueTypeNumber={int(doc_meta.get('uniqueTypeNumber') or 0)},volumeNumber={int(doc_meta.get('volumeNumber') or 0)},rectificationVersionNumber={int(doc_meta.get('rectificationVersionNumber') or 0)})"
    resp = session.get(download_url, stream=True, timeout=90)
    if resp.status_code == 200:
        doc = docx.Document(BytesIO(resp.content))
        return "\n\n".join([p.text for p in doc.paragraphs if p.text.strip()])
    return None

# --- Stage 2 Logic ---

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
    temp_path = old_path + ".tmp"
    with open(temp_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    try:
        result = subprocess.run(['diff', '-U10', old_path, temp_path], capture_output=True, text=True)
        return result.stdout if result.stdout else None
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)

def save_to_archive(filename, content):
    """
    Saves content to the archive directory using UTF-8 encoding.
    
    Args:
        filename (str): Target filename.
        content (str): Content to save.
    Returns:
        str: Full path to the saved file.
    """
    if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    return filepath

def save_diff_record(name, diff_content):
    """
    Saves a diff hunk to the diff_archive directory with a timestamp.
    
    Args:
        name (str): Source name.
        diff_content (str): The raw diff text.
    Returns:
        str: The generated filename of the diff record.
    """
    if not os.path.exists(DIFF_DIR): os.makedirs(DIFF_DIR)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r'\W+', '_', name)
    filename = f"{timestamp}_{safe_name}.diff"
    filepath = os.path.join(DIFF_DIR, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(diff_content)
    return filename

# --- Stage 3: Semantic Analysis & Relevance Gate ---

def parse_diff_hunks(diff_file_path: str) -> List[Dict]:
    """
    Parses a unified diff into hunk-level change objects so semantically distinct
    changes can be analysed independently (Phase B multi-impact detection).
    """
    hunks: List[Dict] = []
    current = None

    def _finalise_hunk(h):
        if not h:
            return None
        added = ' '.join([x for x in h.get('added_lines', []) if x]).strip()
        removed = ' '.join([x for x in h.get('removed_lines', []) if x]).strip()
        change_context = ' '.join([x for x in [removed, added] if x]).strip()
        if not change_context:
            return None
        return {
            'hunk_index': h['hunk_index'],
            'header': h.get('header', ''),
            'added': added,
            'removed': removed,
            'change_context': change_context,
        }

    with open(diff_file_path, 'r', encoding='utf-8') as f:
        for raw_line in f:
            line = raw_line.rstrip('\n')

            if line.startswith('@@'):
                finalised = _finalise_hunk(current)
                if finalised:
                    hunks.append(finalised)
                current = {
                    'hunk_index': len(hunks) + 1,
                    'header': line,
                    'added_lines': [],
                    'removed_lines': []
                }
                continue

            if line.startswith('+++') or line.startswith('---'):
                continue

            if current is None and (line.startswith('+') or line.startswith('-')):
                current = {
                    'hunk_index': 1,
                    'header': 'NO_HUNK_HEADER',
                    'added_lines': [],
                    'removed_lines': []
                }

            if current is None:
                continue

            if line.startswith('+'):
                current['added_lines'].append(line[1:].strip())
            elif line.startswith('-'):
                current['removed_lines'].append(line[1:].strip())

    finalised = _finalise_hunk(current)
    if finalised:
        hunks.append(finalised)

    return hunks


def extract_change_content(diff_file_path):
    """
    Backwards-compatible change extractor. Phase B now parses hunks first and then
    flattens them into a single overall change context for logging/high-level signals.
    """
    hunks = parse_diff_hunks(diff_file_path)
    additions = [h['added'] for h in hunks if h.get('added')]
    removals = [h['removed'] for h in hunks if h.get('removed')]
    change_context = ' '.join([x for x in removals + additions if x]).strip()
    return {
        'added': ' '.join(additions).strip(),
        'removed': ' '.join(removals).strip(),
        'change_context': change_context,
        'hunks': hunks
    }


def _top_k_indices(values: np.ndarray, k: int) -> np.ndarray:
    """Returns indices of the top-k values in descending order."""
    if len(values) == 0:
        return np.array([], dtype=int)
    k = max(1, min(k, len(values)))
    if k == len(values):
        return np.argsort(values)[::-1]
    idx = np.argpartition(values, -k)[-k:]
    return idx[np.argsort(values[idx])[::-1]]


def _build_chunk_candidate(idx: int, similarity: float, hunk: Dict, hunk_power: Dict,
                           udids: List[str], chunk_texts: List[str], chunks_raw: Optional[List[Dict]]) -> Dict:
    raw = (chunks_raw[idx] if chunks_raw else {}) or {}
    base_similarity = float(similarity)
    final_score = float(calculate_final_score(base_similarity, hunk_power.get('score', 0.0)))
    return {
        'raw_index': int(idx),
        'udid': udids[idx],
        'chunk_id': raw.get('Chunk_ID', 'N/A') if raw else 'N/A',
        'headline_alt': raw.get('Headline_Alt', 'N/A') if raw else 'N/A',
        'chunk_text': chunk_texts[idx],
        'chunk_context_prepend': raw.get('Chunk_Context_Prepend', '') if raw else '',
        'token_count': raw.get('Chunk_Token_Count', 'N/A') if raw else 'N/A',
        'base_similarity': base_similarity,
        'final_score': final_score,
        'hunk_index': hunk['hunk_index'],
        'hunk_header': hunk.get('header', ''),
        'hunk_change_preview': hunk.get('change_context', '')[:180],
        'hunk_power_words': hunk_power.get('found', []),
        'hunk_power_word_score': float(hunk_power.get('score', 0.0)),
    }


def aggregate_page_impacts(top_chunks: List[Dict]) -> List[Dict]:
    """
    Aggregates chunk-level matches into page-level impact candidates (UDID-level).
    """
    pages: Dict[str, Dict] = {}

    for c in top_chunks:
        udid = c['udid']
        page = pages.get(udid)
        if page is None:
            page = {
                'udid': udid,
                'max_base_similarity': c['base_similarity'],
                'max_final_score': c['final_score'],
                'best_chunk': c,
                'chunk_hits': 0,
                'matched_hunks': set(),
                'supporting_chunks': []
            }
            pages[udid] = page

        page['chunk_hits'] += 1
        page['matched_hunks'].add(c['hunk_index'])
        page['supporting_chunks'].append(c)

        if c['base_similarity'] > page['max_base_similarity']:
            page['max_base_similarity'] = c['base_similarity']
        if c['final_score'] > page['max_final_score']:
            page['max_final_score'] = c['final_score']
        if c['final_score'] > page['best_chunk']['final_score']:
            page['best_chunk'] = c

    impacted_pages: List[Dict] = []
    for page in pages.values():
        distinct_hunks = len(page['matched_hunks'])
        chunk_hits = page['chunk_hits']

        coverage_bonus = min(MAX_PAGE_COVERAGE_BONUS, max(0, distinct_hunks - 1) * PAGE_HUNK_COVERAGE_BONUS)
        density_bonus = min(MAX_PAGE_DENSITY_BONUS, max(0, chunk_hits - 1) * PAGE_CHUNK_DENSITY_BONUS)

        aggregated_base = min(1.0, page['max_base_similarity'] + coverage_bonus + density_bonus)
        aggregated_final = min(1.0, page['max_final_score'] + coverage_bonus + density_bonus)

        supporting_chunks = sorted(
            page['supporting_chunks'],
            key=lambda x: (x['final_score'], x['base_similarity']),
            reverse=True
        )

        impacted_pages.append({
            'udid': page['udid'],
            'aggregated_base_similarity': float(aggregated_base),
            'aggregated_final_score': float(aggregated_final),
            'max_base_similarity': float(page['max_base_similarity']),
            'max_final_score': float(page['max_final_score']),
            'chunk_hits': int(chunk_hits),
            'distinct_hunk_hits': int(distinct_hunks),
            'coverage_bonus': float(coverage_bonus),
            'density_bonus': float(density_bonus),
            'best_chunk': page['best_chunk'],
            'supporting_chunks': supporting_chunks[:5],
            'matched_hunk_indices': sorted(page['matched_hunks']),
        })

    impacted_pages.sort(
        key=lambda p: (p['aggregated_final_score'], p['distinct_hunk_hits'], p['chunk_hits'], p['max_base_similarity']),
        reverse=True
    )
    for rank, page in enumerate(impacted_pages, start=1):
        page['rank'] = rank
    return impacted_pages

def detect_power_words(text):
    """
    Scans text for legal trigger words that indicate high-priority changes.
    
    Power words include: must, shall, may, penalty, fine, days deadlines, dollar amounts,
    and references to specific acts.
    
    Args:
        text (str): The text to scan.
    Returns:
        dict: Contains 'found' (list of matched words), 'count' (int), and 'score' (float 0-1).
    """
    power_patterns = [
        r'\bmust\b',
        r'\bshall\b',
        r'\bmay\b',
        r'\bpenalty\b',
        r'\bpenalties\b',
        r'\bfine\b',
        r'\bfines\b',
        r'\$\d+(?:,\d+)*',          # Dollar amounts with commas like $150,000
        r'\d+\s*days?\b',            # Time periods like "30 days"
        r'Archives\s+Act\s+1983',
        r'\bprohibited\b',
        r'\bmandatory\b',
        r'\brequired\b',
        r'\bobligation\b',
    ]
    
    found_words = []
    text_lower = text.lower()
    
    for pattern in power_patterns:
        matches = re.findall(pattern, text_lower, re.IGNORECASE)
        found_words.extend(matches)
    
    # Remove duplicates while preserving order
    found_words = list(dict.fromkeys(found_words))
    
    # Each power word adds 0.15, capped at 1.0
    power_score = min(1.0, len(found_words) * 0.15)
    
    return {
        'found': found_words,
        'count': len(found_words),
        'score': power_score
    }

def calculate_final_score(base_similarity, power_word_score):
    """
    Adds the power word boost directly on top of the base similarity score,
    capped at 1.0.

    This replaces the previous 90/10 weighted blend. The additive approach means
    the boost is always visible in the log and has a consistent, predictable effect
    regardless of the base score — a score of 0.40 with a boost of 0.10 will
    clearly show as 0.50, whereas the weighted formula would have shown 0.46.

    Args:
        base_similarity (float): Cosine similarity score (0-1).
        power_word_score (float): Additive boost from power words (0-1).
    Returns:
        float: Final score, capped at 1.0.
    """
    return min(1.0, base_similarity + power_word_score)

def should_generate_handover(final_score, threshold=SIMILARITY_THRESHOLD):
    """
    Determines if a change is relevant enough to generate a handover packet for Tom.
    
    Args:
        final_score (float): The final weighted relevance score.
        threshold (float): Minimum score required (default from config).
    Returns:
        bool: True if handover should be generated.
    """
    return final_score >= threshold

def calculate_similarity(diff_path, mock_semantic_data=None):
    """
    Phase B implementation: hunk-aware semantic matching with chunk-level retrieval
    and page-level (UDID) aggregation to detect likely multi-page impacts.
    """
    change = extract_change_content(diff_path)
    all_hunks = change.get('hunks', [])

    if not change['change_context'] or not all_hunks:
        logger.warning(f"No substantive content extracted from {diff_path}")
        return {
            'status': 'no_content',
            'change_text': '',
            'change_hunks': [],
            'final_score': 0.0,
            'should_handover': False
        }

    logger.info(f"Change context preview: {change['change_context'][:200]}...")
    logger.info(f"Parsed {len(all_hunks)} diff hunk(s) for semantic analysis")

    overall_power_analysis = detect_power_words(change['change_context'])
    logger.info(f"Power words found (overall): {overall_power_analysis['count']} - {overall_power_analysis['found']}")
    logger.info(f"Overall power word score: {overall_power_analysis['score']:.2f}")

    hunk_texts = []
    hunk_power_analyses = []
    for h in all_hunks:
        h_text = (h.get('change_context') or '').strip()
        hunk_texts.append(h_text)
        hunk_power_analyses.append(detect_power_words(h_text))

    try:
        response = client.embeddings.create(input=hunk_texts, model=SEMANTIC_MODEL)
        diff_vectors = np.array([row.embedding for row in response.data])
        if diff_vectors.ndim == 1:
            diff_vectors = diff_vectors.reshape(1, -1)
        logger.info(f"Generated {len(diff_vectors)} OpenAI embedding(s) for diff hunks ({diff_vectors.shape[-1]}d)")
    except Exception as e:
        logger.error(f"API Error: {e}")
        return {
            'status': 'error',
            'change_text': change['change_context'],
            'change_hunks': all_hunks,
            'power_words': overall_power_analysis,
            'final_score': 0.0,
            'base_similarity': 0.0,
            'should_handover': False
        }

    global _semantic_cache
    if mock_semantic_data:
        logger.info("Using mock semantic data for testing")
        website_vectors = mock_semantic_data['embeddings']
        udids = mock_semantic_data['udids']
        chunk_texts = mock_semantic_data.get('chunk_texts', [''] * len(udids))
        chunks_raw = mock_semantic_data.get('chunks_raw')
    else:
        if _semantic_cache is None:
            if not os.path.exists(SEMANTIC_EMBEDDINGS_FILE):
                logger.error(f"Semantic embeddings file not found: {SEMANTIC_EMBEDDINGS_FILE}")
                return {
                    'status': 'missing_embeddings',
                    'change_text': change['change_context'],
                    'change_hunks': all_hunks,
                    'power_words': overall_power_analysis,
                    'final_score': 0.0,
                    'should_handover': False
                }
            logger.info(f"Loading semantic embeddings from {SEMANTIC_EMBEDDINGS_FILE}...")
            with open(SEMANTIC_EMBEDDINGS_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            _semantic_cache = {
                'vectors': np.array([json.loads(item['Chunk_Embedding']) for item in raw]),
                'udids': [item['UDID'] for item in raw],
                'chunk_texts': [item['Chunk_Text'] for item in raw],
                'chunks_raw': raw
            }
            logger.info(f"Loaded {len(raw)} semantic chunks")
        website_vectors = _semantic_cache['vectors']
        udids = _semantic_cache['udids']
        chunk_texts = _semantic_cache['chunk_texts']
        chunks_raw = _semantic_cache['chunks_raw']

    try:
        similarity_matrix = cosine_similarity(diff_vectors, website_vectors)
        logger.info(f"Calculated similarity matrix: {similarity_matrix.shape[0]} hunks x {similarity_matrix.shape[1]} chunks")
    except Exception as e:
        logger.error(f"Failed to calculate similarities: {e}")
        return {
            'status': 'similarity_error',
            'change_text': change['change_context'],
            'change_hunks': all_hunks,
            'power_words': overall_power_analysis,
            'final_score': 0.0,
            'should_handover': False
        }

    top_chunks: List[Dict] = []
    hunk_matches: List[Dict] = []

    for h_idx, hunk in enumerate(all_hunks):
        row = similarity_matrix[h_idx]
        top_idx = _top_k_indices(row, TOP_K_CHUNKS_PER_HUNK)
        hunk_power = hunk_power_analyses[h_idx]
        hunk_candidates: List[Dict] = []

        for idx in top_idx:
            candidate = _build_chunk_candidate(
                idx=int(idx),
                similarity=float(row[int(idx)]),
                hunk=hunk,
                hunk_power=hunk_power,
                udids=udids,
                chunk_texts=chunk_texts,
                chunks_raw=chunks_raw
            )
            top_chunks.append(candidate)
            hunk_candidates.append(candidate)

        per_page_best: Dict[str, Dict] = {}
        for c in hunk_candidates:
            existing = per_page_best.get(c['udid'])
            if existing is None or c['final_score'] > existing['final_score']:
                per_page_best[c['udid']] = c

        top_pages_for_hunk = sorted(
            per_page_best.values(),
            key=lambda x: (x['final_score'], x['base_similarity']),
            reverse=True
        )[:5]

        hunk_matches.append({
            'hunk_index': hunk['hunk_index'],
            'header': hunk.get('header', ''),
            'change_text': hunk.get('change_context', ''),
            'change_preview': hunk.get('change_context', '')[:220],
            'added': hunk.get('added', ''),
            'removed': hunk.get('removed', ''),
            'power_words': hunk_power,
            'top_chunks': hunk_candidates[:5],
            'top_pages': [
                {
                    'udid': c['udid'],
                    'score': c['final_score'],
                    'base_similarity': c['base_similarity'],
                    'chunk_id': c['chunk_id'],
                    'headline_alt': c['headline_alt']
                } for c in top_pages_for_hunk
            ]
        })

    if not top_chunks:
        return {
            'status': 'no_candidates',
            'change_text': change['change_context'],
            'change_hunks': all_hunks,
            'power_words': overall_power_analysis,
            'final_score': 0.0,
            'should_handover': False
        }

    impacted_pages = aggregate_page_impacts(top_chunks)
    primary_page = impacted_pages[0] if impacted_pages else None
    primary_chunk = primary_page.get('best_chunk') if primary_page else None

    base_similarity = float(primary_page['aggregated_base_similarity']) if primary_page else 0.0
    final_score = float(primary_page['aggregated_final_score']) if primary_page else 0.0
    should_handover = should_generate_handover(final_score)

    impact_count = sum(1 for p in impacted_pages if p['aggregated_final_score'] >= MULTI_IMPACT_MIN_SCORE)
    multi_impact_likely = impact_count >= 2

    if primary_page:
        logger.info(
            f"Primary page match: {primary_page['udid']} with aggregated score {final_score:.3f} "
            f"(chunk hits={primary_page['chunk_hits']}, hunk hits={primary_page['distinct_hunk_hits']})"
        )
    if multi_impact_likely:
        logger.info(f"Multi-impact likely: {impact_count} page candidates >= {MULTI_IMPACT_MIN_SCORE:.2f}")

    logger.info(f"Threshold: {SIMILARITY_THRESHOLD} | Should generate handover: {should_handover}")

    top_chunks_sorted = sorted(
        top_chunks,
        key=lambda x: (x['final_score'], x['base_similarity']),
        reverse=True
    )

    filter_reason = None
    if not should_handover:
        filter_reason = f"Below threshold: {final_score:.3f} < {SIMILARITY_THRESHOLD}"
    elif multi_impact_likely:
        filter_reason = f"Multi-impact candidate: {impact_count} pages >= {MULTI_IMPACT_MIN_SCORE:.2f}"

    matched_chunk_raw = None
    if primary_chunk:
        if chunks_raw:
            matched_chunk_raw = chunks_raw[primary_chunk['raw_index']]
        else:
            matched_chunk_raw = {
                'Chunk_ID': primary_chunk.get('chunk_id'),
                'Chunk_Text': primary_chunk.get('chunk_text'),
                'Headline_Alt': primary_chunk.get('headline_alt', 'N/A'),
                'Chunk_Context_Prepend': primary_chunk.get('chunk_context_prepend', ''),
                'Chunk_Token_Count': primary_chunk.get('token_count', 'N/A')
            }

    return {
        'status': 'success',
        'change_text': change['change_context'],
        'change_hunks': all_hunks,
        'hunk_matches': hunk_matches,
        'diff_vector_shape': tuple(diff_vectors.shape),
        'power_words': overall_power_analysis,
        'base_similarity': base_similarity,
        'final_score': final_score,
        'matched_udid': primary_page['udid'] if primary_page else None,
        'matched_chunk_id': primary_chunk.get('chunk_id') if primary_chunk else None,
        'matched_text': primary_chunk.get('chunk_text') if primary_chunk else '',
        'matched_chunk_raw': matched_chunk_raw,
        'primary_match': {'page': primary_page, 'chunk': primary_chunk} if primary_page and primary_chunk else None,
        'impacted_pages': impacted_pages,
        'top_chunks': top_chunks_sorted[:MAX_TOP_CHUNKS_IN_PACKET],
        'impact_count': int(impact_count),
        'multi_impact_likely': bool(multi_impact_likely),
        'multi_impact_threshold': float(MULTI_IMPACT_MIN_SCORE),
        'threshold': SIMILARITY_THRESHOLD,
        'should_handover': should_handover,
        'filter_reason': filter_reason
    }

def write_github_summary(handover_paths: list):
    """
    Writes a markdown summary of this run's handover packets to the GitHub Actions
    job summary (GITHUB_STEP_SUMMARY). If that env variable isn't set (i.e. running
    locally), the summary is written to stdout instead.

    Args:
        handover_paths (list): Paths to handover packet JSON files generated this run.
    """
    summary_file = os.environ.get('GITHUB_STEP_SUMMARY')

    lines = ["## Tripwire run summary\n"]

    if not handover_paths:
        lines.append("No handover packets generated this run — all changes were below threshold or no changes were detected.\n")
    else:
        lines.append(f"**{len(handover_paths)} handover packet(s) generated this run.**\n")
        lines.append("| Priority | Score | Source | Matched UDID | Headline | Diff file |")
        lines.append("|----------|-------|--------|--------------|----------|-----------|")

        for path in handover_paths:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    p = json.load(f)
                priority   = p.get('packet_priority', 'N/A')
                score      = p.get('analysis', {}).get('similarity_score', 0)
                source     = p.get('source', {}).get('name', 'N/A')
                udid       = p.get('matched_chunk', {}).get('udid', 'N/A')
                headline   = p.get('matched_chunk', {}).get('headline_alt', 'N/A')
                diff_file  = p.get('source', {}).get('diff_file', 'N/A')
                lines.append(f"| **{priority}** | {score:.3f} | {source} | `{udid}` | {headline} | `{diff_file}` |")
            except Exception as e:
                lines.append(f"| — | — | Error reading packet: {e} | — | — | — |")

        lines.append("")
        lines.append("> Full JSON packets are available in the **handover_packets** artifact attached to this run.")

    summary = "\n".join(lines) + "\n"

    if summary_file:
        with open(summary_file, 'a', encoding='utf-8') as f:
            f.write(summary)
    else:
        print(summary)


def generate_handover_packet(source_name: str, priority: str, diff_file: str,
                             analysis: dict, timestamp: str) -> str:
    """
    Generates a JSON handover packet for Tom containing all context needed to review
    a flagged content change.

    Phase B extends the packet to include multi-page impact candidates and hunk-level
    evidence while retaining the legacy matched_chunk block for compatibility.
    """
    os.makedirs(HANDOVER_DIR, exist_ok=True)

    chunk = analysis.get('matched_chunk_raw') or {}
    power = analysis.get('power_words', {})
    final_score = analysis.get('final_score', 0.0)
    impacted_pages = analysis.get('impacted_pages', [])
    top_chunks = analysis.get('top_chunks', [])
    hunk_matches = analysis.get('hunk_matches', [])
    primary_udid = analysis.get('matched_udid', 'unknown')

    pw_count = power.get('count', 0)
    if final_score >= 0.75 or pw_count >= 5:
        packet_priority = 'Critical'
    elif final_score >= 0.60 or pw_count >= 3:
        packet_priority = 'High'
    else:
        packet_priority = 'Medium'

    safe_ts = re.sub(r'[^0-9T]', '', timestamp).replace('T', '_')
    safe_ts = safe_ts[:20] if safe_ts else datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    diff_stub = os.path.splitext(os.path.basename(diff_file))[0]
    diff_stub = re.sub(r'\W+', '_', diff_stub)[:40]
    filename = f"handover_{safe_ts}_{primary_udid}_{diff_stub}.json"
    filepath = os.path.join(HANDOVER_DIR, filename)

    packet_hunks = []
    for hm in hunk_matches:
        packet_hunks.append({
            'hunk_index': hm.get('hunk_index'),
            'header': hm.get('header', ''),
            'change_preview': hm.get('change_preview', ''),
            'power_words': hm.get('power_words', {}),
            'top_pages': hm.get('top_pages', []),
            'top_chunks': [
                {
                    'udid': c.get('udid'),
                    'chunk_id': c.get('chunk_id'),
                    'headline_alt': c.get('headline_alt'),
                    'base_similarity': c.get('base_similarity'),
                    'final_score': c.get('final_score')
                }
                for c in hm.get('top_chunks', [])[:5]
            ]
        })

    packet_impacted_pages = []
    for p in impacted_pages[:MAX_IMPACT_PAGES_IN_PACKET]:
        best = p.get('best_chunk', {}) or {}
        packet_impacted_pages.append({
            'rank': p.get('rank'),
            'udid': p.get('udid'),
            'aggregated_final_score': p.get('aggregated_final_score'),
            'aggregated_base_similarity': p.get('aggregated_base_similarity'),
            'chunk_hits': p.get('chunk_hits'),
            'distinct_hunk_hits': p.get('distinct_hunk_hits'),
            'coverage_bonus': p.get('coverage_bonus'),
            'density_bonus': p.get('density_bonus'),
            'matched_hunk_indices': p.get('matched_hunk_indices', []),
            'best_chunk': {
                'chunk_id': best.get('chunk_id'),
                'headline_alt': best.get('headline_alt'),
                'base_similarity': best.get('base_similarity'),
                'final_score': best.get('final_score'),
                'chunk_text': best.get('chunk_text', '')[:500]
            }
        })

    packet_top_chunks = []
    for c in top_chunks[:MAX_TOP_CHUNKS_IN_PACKET]:
        packet_top_chunks.append({
            'udid': c.get('udid'),
            'chunk_id': c.get('chunk_id'),
            'headline_alt': c.get('headline_alt'),
            'hunk_index': c.get('hunk_index'),
            'base_similarity': c.get('base_similarity'),
            'final_score': c.get('final_score'),
            'hunk_power_words': c.get('hunk_power_words', []),
            'hunk_power_word_score': c.get('hunk_power_word_score', 0.0),
            'chunk_text': c.get('chunk_text', '')[:500]
        })

    packet = {
        'packet_id': filename.replace('.json', ''),
        'generated_at': timestamp,
        'packet_priority': packet_priority,
        'source': {
            'name': source_name,
            'monitoring_priority': priority,
            'diff_file': diff_file,
            'diff_file_path': os.path.join(DIFF_DIR, diff_file)
        },
        'analysis': {
            'similarity_score': final_score,
            'base_similarity': analysis.get('base_similarity'),
            'threshold': analysis.get('threshold', SIMILARITY_THRESHOLD),
            'power_words_found': power.get('found', []),
            'power_word_count': pw_count,
            'power_word_score': power.get('score', 0.0),
            'multi_impact_likely': analysis.get('multi_impact_likely', False),
            'impact_count': analysis.get('impact_count', 0),
            'multi_impact_threshold': analysis.get('multi_impact_threshold', MULTI_IMPACT_MIN_SCORE)
        },
        'change': {
            'hunk': analysis.get('change_text', ''),
            'preview': (analysis.get('change_text', '') or '')[:200],
            'hunks': packet_hunks
        },
        'matched_chunk': {
            'udid': primary_udid,
            'chunk_id': chunk.get('Chunk_ID', 'N/A'),
            'headline_alt': chunk.get('Headline_Alt', 'N/A'),
            'chunk_text': chunk.get('Chunk_Text', analysis.get('matched_text', '')),
            'chunk_context_prepend': chunk.get('Chunk_Context_Prepend', ''),
            'token_count': chunk.get('Chunk_Token_Count', 'N/A')
        },
        'impacted_pages': packet_impacted_pages,
        'top_chunks': packet_top_chunks,
        'review_context': {
            'why_flagged': (
                f"Primary page aggregated similarity score of {final_score:.2%} exceeds threshold of "
                f"{analysis.get('threshold', SIMILARITY_THRESHOLD):.2%}"
            ),
            'power_words_note': (
                f"Found {pw_count} enforcement-related term(s): "
                f"{', '.join(power.get('found', []))}"
                if pw_count else "No enforcement terms detected"
            ),
            'multi_impact_note': (
                f"Multi-impact likely: {analysis.get('impact_count', 0)} page candidates "
                f"scored >= {analysis.get('multi_impact_threshold', MULTI_IMPACT_MIN_SCORE):.2f}"
                if analysis.get('multi_impact_likely') else
                "No strong evidence yet that multiple IPFR pages are impacted"
            )
        }
    }

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(packet, f, indent=2, ensure_ascii=False)

    logger.info(f"Handover packet written: {filename} [{packet_priority}]")
    return filepath


# --- Main Loop ---

def main():
    if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)
    if not os.path.exists(DIFF_DIR): os.makedirs(DIFF_DIR)
    with open(SOURCES_FILE, 'r') as f: sources = json.load(f)

    session = requests.Session()
    driver = None
    logger.info(f"--- Tripwire Stage 2 (Modular & Documented) Run: {datetime.datetime.now()} ---")
    handover_paths = []

    for source in sources:
        name, stype, priority = source['name'], source['type'], source.get('priority', 'Low')
        out_name = source['output_filename'].replace('.docx', '.md') if stype == "Legislation_OData" else source['output_filename']
        out_path = os.path.join(OUTPUT_DIR, out_name)
        
        old_id = get_last_version_id(name)
        current_id = fetch_stage0_metadata(session, source)
        file_exists = os.path.exists(out_path)

        # START: Self-Healing Logic
        # Check if we need to repopulate a missing file even if the Version ID matches
        repopulate_only = False
        if old_id and current_id and old_id == current_id:
            if file_exists:
                logger.info(f"No version change for {name}. Skipping.")
                continue
            else:
                logger.warning(f"File missing for {name}. Healing archive...")
                repopulate_only = True

        new_content = None
        try:
            # START: Extraction and Normalisation
            # Fetches raw content from the source and converts it to a standard Markdown/XML format
            if stype == "Legislation_OData":
                ver_id, meta = fetch_legislation_metadata(session, source)
                if meta:
                    new_content = download_legislation_content(session, source['base_url'], meta)
                    current_id = ver_id
            elif stype == "RSS":
                resp = session.get(source['url'], timeout=15)
                new_content = sanitize_rss(resp.content)
            elif stype == "WebPage":
                if not driver: driver = initialize_driver()
                new_content = fetch_webpage_content(driver, source['url'])

            if new_content:
                # START: Substantive Change Detection
                # Compares the new normalized content against the archived version
                diff_hunk = get_diff(out_path, new_content)
                
                # Determine if we should save (change detected, initial run, or self-healing)
                if diff_hunk or not file_exists or repopulate_only:
                    save_to_archive(out_name, new_content)
                    
                    if diff_hunk and diff_hunk != "Initial archive creation." and not repopulate_only:
                        diff_file = save_diff_record(name, diff_hunk)
                        diff_path = os.path.join(DIFF_DIR, diff_file)

                        # Stage 3: Semantic analysis and handover packet generation
                        analysis = calculate_similarity(diff_path)
                        s3_score = analysis.get('final_score') if analysis['status'] == 'success' else None
                        s3_words = analysis.get('power_words', {}).get('found') if analysis['status'] == 'success' else None
                        s3_udid = analysis.get('matched_udid') if analysis['status'] == 'success' else None
                        s3_chunk_id = analysis.get('matched_chunk_id') if analysis['status'] == 'success' else None
                        s3_outcome = None
                        s3_reason = analysis.get('filter_reason') or analysis.get('message') or analysis['status']

                        if analysis.get('should_handover'):
                            ts = datetime.datetime.now().isoformat()
                            packet_path = generate_handover_packet(name, priority, diff_file, analysis, ts)
                            handover_paths.append(packet_path)
                            s3_outcome = 'handover'
                            s3_reason = (
                                f"Score {s3_score:.3f} >= threshold {SIMILARITY_THRESHOLD}"
                                + (f"; multi-impact likely ({analysis.get('impact_count', 0)} pages >= "
                                   f"{analysis.get('multi_impact_threshold', MULTI_IMPACT_MIN_SCORE):.2f})"
                                   if analysis.get('multi_impact_likely') else "")
                            )
                        elif analysis['status'] == 'success':
                            s3_outcome = 'filtered'

                        log_to_audit(name, priority, "Success", "Yes", current_id, diff_file,
                                     similarity_score=s3_score, power_words=s3_words,
                                     matched_udid=s3_udid, matched_chunk_id=s3_chunk_id,
                                     outcome=s3_outcome, reason=s3_reason)
                    elif repopulate_only:
                        log_to_audit(name, priority, "Success", "Healed", current_id)
                    else:
                        log_to_audit(name, priority, "Success", "Initial", current_id)
                else:
                    log_to_audit(name, priority, "Success", "No", current_id)

        except Exception as e:
            logger.error(f"Failed {name}: {e}")
            log_to_audit(name, priority, "Exception", "N/A", current_id)

    if driver: driver.quit()
    write_github_summary(handover_paths)

if __name__ == "__main__":
    # Check if running Stage 3 test mode
    if len(sys.argv) > 1 and sys.argv[1] == '--test-stage3':
        logger.info("=== Running Stage 3 Phase 2 Test ===")
        
        # Check if diff file argument provided
        if len(sys.argv) < 3:
            logger.error("Usage: python tripwire.py --test-stage3 <path_to_diff_file>")
            logger.info(f"Example: python tripwire.py --test-stage3 {DIFF_DIR}/some_file.diff")
            sys.exit(1)
        
        diff_file = sys.argv[2]
        
        if not os.path.exists(diff_file):
            logger.error(f"Diff file not found: {diff_file}")
            sys.exit(1)
        
        # Run Stage 3 analysis
        result = calculate_similarity(diff_file)
        
        logger.info("\n=== Stage 3 Phase 2 Test Results ===")
        logger.info(f"Status: {result['status']}")
        logger.info(f"Change text length: {len(result.get('change_text', ''))} characters")
        
        if result.get('power_words'):
            pw = result['power_words']
            logger.info(f"Power words detected: {pw['count']}")
            logger.info(f"Power words: {pw['found']}")
            logger.info(f"Power word score: {pw['score']:.2f}")
        
        if result.get('final_score') is not None:
            logger.info(f"Base similarity: {result.get('base_similarity', 0):.3f}")
            logger.info(f"Final score: {result['final_score']:.3f}")
            logger.info(f"Should handover: {result.get('should_handover', False)}")
            if result.get('filter_reason'):
                logger.info(f"Filter reason: {result['filter_reason']}")
        
        if result['status'] == 'success':
            logger.info("\n✓ All systems working! Ready for production.")
        else:
            logger.error("\n✗ Test failed - check errors above")
        
        sys.exit(0)
    
    # Normal Stage 2 operation
    main()
