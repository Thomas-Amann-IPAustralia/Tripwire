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
                 outcome=None, reason=None):
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
        outcome (str, optional): 'handover' or 'filtered'.
        reason (str, optional): Explanation of the outcome.
    """
    file_exists = os.path.exists(AUDIT_LOG)
    headers = [
        'Timestamp', 'Source_Name', 'Priority', 'Status', 'Change_Detected',
        'Version_ID', 'Diff_File', 'Similarity_Score', 'Power_Words',
        'Matched_UDID', 'Outcome', 'Reason'
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

def extract_change_content(diff_file_path):
    """
    Parses a diff file to extract added and removed content lines.
    Strips diff metadata (+++, ---, @@) and returns both additions and removals.
    
    Args:
        diff_file_path (str): Path to the .diff file.
    Returns:
        dict: Contains 'added', 'removed', and 'change_context' strings.
    """
    additions = []
    removals = []

    with open(diff_file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.rstrip()
            # Extract additions (lines starting with +, but not +++)
            if line.startswith('+') and not line.startswith('+++'):
                additions.append(line[1:].strip())
            # Extract removals (lines starting with -, but not ---)
            elif line.startswith('-') and not line.startswith('---'):
                removals.append(line[1:].strip())

    # Combine removals and additions as "change context"
    change_context = ' '.join(removals + additions)
    return {'added': ' '.join(additions), 'removed': ' '.join(removals), 'change_context': change_context}

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
        r'\$\d+(?:,\d+)*',  # Matches dollar amounts with commas like $150,000
        r'\d+\s*days?\b',  # Time periods like "30 days"
        r'Archives\s+Act\s+1983', # Handles whitespace better for Act reference
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
    
    # Calculate power word score (capped at 1.0)
    # Each power word adds 0.15, maximum 1.0
    power_score = min(1.0, len(found_words) * 0.15)
    
    return {
        'found': found_words,
        'count': len(found_words),
        'score': power_score
    }

def calculate_final_score(base_similarity, power_word_score):
    """
    Combines semantic similarity with power word boost to get final relevance score.
    
    Weighting: 90% semantic similarity, 10% power words
    
    Args:
        base_similarity (float): Cosine similarity score (0.7-1).
        power_word_score (float): Power word score (0.7-1).
    Returns:
        float: Final weighted score (0.7-1).
    """
    return (base_similarity * 0.90) + (power_word_score * 0.10)

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
    Phase 2 implementation: Converts diff to embedding, detects power words, 
    and calculates relevance score against website content.
    
    Can work with mock data for testing or real data from Tom's spreadsheet.
    
    Args:
        diff_path (str): Path to the diff file.
        mock_semantic_data (dict, optional): Mock data for testing with keys:
            - 'udids': list of UDID strings
            - 'embeddings': numpy array of shape (n, 1536)
            - 'chunk_texts': list of chunk text strings
    Returns:
        dict: Contains status, scores, matches, and power word analysis.
    """
    # Step 1: Extract change content
    change = extract_change_content(diff_path)
    
    if not change['change_context']:
        logger.warning(f"No substantive content extracted from {diff_path}")
        return {
            'status': 'no_content',
            'change_text': '',
            'final_score': 0.0,
            'should_handover': False
        }
    
    logger.info(f"Change context preview: {change['change_context'][:200]}...")
    
    # Step 2: Detect power words
    power_analysis = detect_power_words(change['change_context'])
    logger.info(f"Power words found: {power_analysis['count']} - {power_analysis['found']}")
    logger.info(f"Power word score: {power_analysis['score']:.2f}")
    
    # Step 3: Generate embedding
    try:
        response = client.embeddings.create(
            input=[change['change_context']],
            model=SEMANTIC_MODEL
        )
        # Vector dimension is now 1536
        diff_vector = np.array(response.data[0].embedding).reshape(1, -1)
        logger.info(f"Generated OpenAI embedding (1536d)")
    except Exception as e:
        logger.error(f"API Error: {e}")
        return {'status': 'error', 'final_score': 0.0, 'base_similarity': 0.0, 'should_handover': False}
    
    # Step 4: Load semantic embeddings from JSON (Phase 3)
    global _semantic_cache
    if mock_semantic_data:
        # TESTING MODE: Use provided mock data
        logger.info("Using mock semantic data for testing")
        website_vectors = mock_semantic_data['embeddings']
        udids = mock_semantic_data['udids']
        chunk_texts = mock_semantic_data.get('chunk_texts', [''] * len(udids))
        chunks_raw = None
    else:
        if _semantic_cache is None:
            if not os.path.exists(SEMANTIC_EMBEDDINGS_FILE):
                logger.error(f"Semantic embeddings file not found: {SEMANTIC_EMBEDDINGS_FILE}")
                return {'status': 'missing_embeddings', 'final_score': 0.0, 'should_handover': False}
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
    
    # Step 5: Calculate cosine similarities
    try:
        similarities = cosine_similarity(diff_vector, website_vectors)[0]
        logger.info(f"Calculated similarities for {len(similarities)} chunks")
    except Exception as e:
        logger.error(f"Failed to calculate similarities: {e}")
        return {
            'status': 'similarity_error',
            'change_text': change['change_context'],
            'power_words': power_analysis,
            'final_score': 0.0,
            'should_handover': False
        }
    
    # Step 6: Find best match
    best_match_idx = np.argmax(similarities)
    base_similarity = similarities[best_match_idx]
    matched_udid = udids[best_match_idx]
    matched_text = chunk_texts[best_match_idx]
    
    logger.info(f"Best match: {matched_udid} with similarity {base_similarity:.3f}")
    logger.info(f"Matched chunk preview: {matched_text[:100]}...")
    
    # Step 7: Calculate final score with power word boost
    final_score = calculate_final_score(base_similarity, power_analysis['score'])
    should_handover = should_generate_handover(final_score)
    
    logger.info(f"Base similarity: {base_similarity:.3f}")
    logger.info(f"Final score (with power words): {final_score:.3f}")
    logger.info(f"Threshold: {SIMILARITY_THRESHOLD}")
    logger.info(f"Should generate handover: {should_handover}")
    
    # Step 8: Return comprehensive results
    return {
        'status': 'success',
        'change_text': change['change_context'],
        'diff_vector_shape': diff_vector.shape,
        'power_words': power_analysis,
        'base_similarity': float(base_similarity),
        'final_score': float(final_score),
        'matched_udid': matched_udid,
        'matched_text': matched_text,
        'matched_chunk_raw': chunks_raw[best_match_idx] if chunks_raw else None,
        'threshold': SIMILARITY_THRESHOLD,
        'should_handover': should_handover,
        'filter_reason': None if should_handover else f"Below threshold: {final_score:.3f} < {SIMILARITY_THRESHOLD}"
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

    Args:
        source_name (str): Name of the monitored source.
        priority (str): Source priority level (High/Medium/Low).
        diff_file (str): Filename of the associated diff.
        analysis (dict): The result dict from calculate_similarity().
        timestamp (str): ISO timestamp from the audit log entry.
    Returns:
        str: Path to the written handover packet file.
    """
    os.makedirs(HANDOVER_DIR, exist_ok=True)

    chunk = analysis.get('matched_chunk_raw') or {}
    power = analysis.get('power_words', {})
    final_score = analysis.get('final_score', 0.0)

    # Derive packet priority from score and power word count
    pw_count = power.get('count', 0)
    if final_score >= 0.75 or pw_count >= 5:
        packet_priority = 'Critical'
    elif final_score >= 0.60 or pw_count >= 3:
        packet_priority = 'High'
    else:
        packet_priority = 'Medium'

    safe_ts = timestamp.replace(':', '').replace('.', '')[:15]
    udid = analysis.get('matched_udid', 'unknown')
    filename = f"handover_{safe_ts}_{udid}.json"
    filepath = os.path.join(HANDOVER_DIR, filename)

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
            'power_word_score': power.get('score', 0.0)
        },
        'change': {
            'hunk': analysis.get('change_text', ''),
            'preview': (analysis.get('change_text', '') or '')[:200]
        },
        'matched_chunk': {
            'udid': udid,
            'chunk_id': chunk.get('Chunk_ID', 'N/A'),
            'headline_alt': chunk.get('Headline_Alt', 'N/A'),
            'chunk_text': chunk.get('Chunk_Text', analysis.get('matched_text', '')),
            'chunk_context_prepend': chunk.get('Chunk_Context_Prepend', ''),
            'token_count': chunk.get('Chunk_Token_Count', 'N/A')
        },
        'review_context': {
            'why_flagged': (
                f"Similarity score of {final_score:.2%} exceeds threshold of "
                f"{analysis.get('threshold', SIMILARITY_THRESHOLD):.2%}"
            ),
            'power_words_note': (
                f"Found {pw_count} enforcement-related term(s): "
                f"{', '.join(power.get('found', []))}"
                if pw_count else "No enforcement terms detected"
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
                        s3_outcome = None
                        s3_reason = analysis.get('filter_reason') or analysis.get('message') or analysis['status']

                        if analysis.get('should_handover'):
                            ts = datetime.datetime.now().isoformat()
                            packet_path = generate_handover_packet(name, priority, diff_file, analysis, ts)
                            handover_paths.append(packet_path)
                            s3_outcome = 'handover'
                            s3_reason = f"Score {s3_score:.3f} >= threshold {SIMILARITY_THRESHOLD}"
                        elif analysis['status'] == 'success':
                            s3_outcome = 'filtered'

                        log_to_audit(name, priority, "Success", "Yes", current_id, diff_file,
                                     similarity_score=s3_score, power_words=s3_words,
                                     matched_udid=s3_udid, outcome=s3_outcome, reason=s3_reason)
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
            logger.info(f"Example: python tripwire.py --test-stage3 {DIFF_DIR}/20260208_064622_ABC_News_World.diff")
            sys.exit(1)
        
        diff_file = sys.argv[2]
        
        if not os.path.exists(diff_file):
            logger.error(f"Diff file not found: {diff_file}")
            sys.exit(1)
        
        # Run Stage 3 analysis (will show "awaiting_phase3" without mock data)
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
        
        if result['status'] == 'awaiting_phase3':
            logger.info("\n✓ Phase 2 complete! Power word detection and scoring working.")
            logger.info("Next: Create test fixtures and run pytest to validate logic.")
            logger.info("Phase 3: Load Tom's spreadsheet and generate handover packets.")
        elif result['status'] == 'success':
            logger.info("\n✓ All systems working! Ready for production.")
        else:
            logger.error("\n✗ Test failed - check errors above")
        
        sys.exit(0)
    
    # Normal Stage 2 operation
    main()
