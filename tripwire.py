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
from sentence_transformers import SentenceTransformer
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
SEMANTIC_MODEL = 'intfloat/e5-base-v2'
TOM_SPREADSHEET = '260120_SQLiteStructure.xlsx'  # Tom's pre-vectorised website content
SEMANTIC_SHEET = 'Semantic'  # Sheet containing chunk embeddings
INFLUENCES_SHEET = 'Influences'  # Sheet containing source-to-UDID relationships
SIMILARITY_THRESHOLD = 0.70  # Initial threshold, tune based on testing

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

def log_to_audit(name, priority, status, change_detected, version_id, diff_file=None):
    """
    Appends a new entry to the CSV audit log.
    
    Args:
        name (str): Source name.
        priority (str): Source priority level.
        status (str): Outcome status (Success/Exception).
        change_detected (str): Yes/No/Initial/Healed.
        version_id (str): Metadata ID from the source.
        diff_file (str): Filename of the generated diff hunk, if any.
    """
    file_exists = os.path.exists(AUDIT_LOG)
    headers = ['Timestamp', 'Source_Name', 'Priority', 'Status', 'Change_Detected', 'Version_ID', 'Diff_File']
    with open(AUDIT_LOG, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(headers)
        writer.writerow([datetime.datetime.now().isoformat(), name, priority, status, change_detected, version_id, diff_file or "N/A"])

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
    
    try:
        with open(diff_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.rstrip()
                # Extract additions (lines starting with +, but not +++)
                if line.startswith('+') and not line.startswith('+++'):
                    additions.append(line[1:].strip())
                # Extract removals (lines starting with -, but not ---)
                elif line.startswith('-') and not line.startswith('---'):
                    removals.append(line[1:].strip())
    except Exception as e:
        logger.error(f"Failed to parse diff file {diff_file_path}: {e}")
        return {'added': '', 'removed': '', 'change_context': ''}
    
    # Combine removals and additions as "change context"
    change_context = ' '.join(removals + additions)
    
    return {
        'added': ' '.join(additions),
        'removed': ' '.join(removals),
        'change_context': change_context
    }

def calculate_similarity(diff_path):
    """
    Thin implementation: Converts diff content to embedding vector and prepares for comparison.
    This is Phase 1 - just proves the concept works.
    
    Args:
        diff_path (str): Path to the diff file.
    Returns:
        dict: Contains change_text, diff_vector shape, and readiness status.
    """
    # Extract change content
    change = extract_change_content(diff_path)
    
    if not change['change_context']:
        logger.warning(f"No substantive content extracted from {diff_path}")
        return {
            'status': 'no_content',
            'change_text': '',
            'diff_vector': None
        }
    
    logger.info(f"Change context preview: {change['change_context'][:200]}...")
    
    # Initialize model (in production, this should be done once globally)
    try:
        model = SentenceTransformer(SEMANTIC_MODEL)
        logger.info(f"Loaded model: {SEMANTIC_MODEL}")
    except Exception as e:
        logger.error(f"Failed to load semantic model: {e}")
        return {
            'status': 'model_error',
            'change_text': change['change_context'],
            'diff_vector': None
        }
    
    # Generate embedding
    try:
        diff_vector = model.encode([change['change_context']])
        logger.info(f"Generated embedding vector with shape: {diff_vector.shape}")
    except Exception as e:
        logger.error(f"Failed to generate embedding: {e}")
        return {
            'status': 'embedding_error',
            'change_text': change['change_context'],
            'diff_vector': None
        }
    
    # TODO Phase 2: Load TOM_SPREADSHEET and read SEMANTIC_SHEET for chunk embeddings
    # TODO Phase 2: Load INFLUENCES_SHEET for source relationship boosting
    # TODO Phase 3: Add power word detection
    # TODO Phase 3: Add handover packet generation
    
    return {
        'status': 'ready',
        'change_text': change['change_context'],
        'diff_vector': diff_vector,
        'vector_shape': diff_vector.shape,
        'added_lines': len(change['added'].split()),
        'removed_lines': len(change['removed'].split())
    }

# --- Main Loop ---

def main():
    if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)
    if not os.path.exists(DIFF_DIR): os.makedirs(DIFF_DIR)
    with open(SOURCES_FILE, 'r') as f: sources = json.load(f)

    session = requests.Session()
    driver = None
    logger.info(f"--- Tripwire Stage 2 (Modular & Documented) Run: {datetime.datetime.now()} ---")

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
                        log_to_audit(name, priority, "Success", "Yes", current_id, diff_file)
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

if __name__ == "__main__":
    # Check if running Stage 3 test mode
    if len(sys.argv) > 1 and sys.argv[1] == '--test-stage3':
        logger.info("=== Running Stage 3 Thin Implementation Test ===")
        
        # Check if diff file argument provided
        if len(sys.argv) < 3:
            logger.error("Usage: python tripwire.py --test-stage3 <path_to_diff_file>")
            logger.info(f"Example: python tripwire.py --test-stage3 {DIFF_DIR}/20260208_064622_ABC_News_World.diff")
            sys.exit(1)
        
        diff_file = sys.argv[2]
        
        if not os.path.exists(diff_file):
            logger.error(f"Diff file not found: {diff_file}")
            sys.exit(1)
        
        # Run Stage 3 analysis
        result = calculate_similarity(diff_file)
        
        logger.info("\n=== Stage 3 Test Results ===")
        logger.info(f"Status: {result['status']}")
        logger.info(f"Change text length: {len(result.get('change_text', ''))} characters")
        
        if result.get('diff_vector') is not None:
            logger.info(f"Vector shape: {result['vector_shape']}")
            logger.info(f"Added word count: {result['added_lines']}")
            logger.info(f"Removed word count: {result['removed_lines']}")
            logger.info("\n✓ Stage 3 thin implementation working correctly!")
            logger.info("Next steps:")
            logger.info("1. Create test fixtures (test_fixtures/)")
            logger.info("2. Write test_stage3.py")
            logger.info("3. Implement full similarity comparison with semantic.csv")
        else:
            logger.error("✗ Stage 3 failed - check errors above")
        
        sys.exit(0)
    
    # Normal Stage 2 operation
    main()
