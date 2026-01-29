import json
import hashlib
import requests
import datetime
import os
import sys
import time
import random
import logging
import re
from typing import List, Dict, Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- Web Scrape Imports ---
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from selenium_stealth import stealth
from bs4 import BeautifulSoup
from markdownify import markdownify as md

# --- Configuration ---
HISTORY_FILE = 'tripwire_history.json'
SOURCES_FILE = 'sources.json'
OUTPUT_DIR = 'content_archive'
HISTORY_LIMIT = 10 

# --- Scrape Config ---
TAGS_TO_EXCLUDE = ['nav', 'footer', 'header', 'script', 'style', 'aside', '.noprint', '#sidebar', 'iframe']
BLOCK_PAGE_SIGNATURES = [
    "access denied", "enable javascript", "checking if the site connection is secure",
    "just a moment...", "verifying you are human", "ddos protection by", "site can’t be reached"
]

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("Tripwire")

# --- Robust Session Factory ---
def get_robust_session():
    """Creates a requests session with retry logic for stability."""
    session = requests.Session()
    retry_strategy = Retry(
        total=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    return session

# --- JSON History Management ---

def load_history() -> List[Dict]:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"History file corrupt or empty: {e}. Starting fresh.")
        return []

def save_history(history: List[Dict]):
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save history: {e}")

def prune_history(history: List[Dict]) -> List[Dict]:
    new_history = []
    source_names = set(entry['source_name'] for entry in history)
    for name in source_names:
        entries = [e for e in history if e['source_name'] == name]
        entries.sort(key=lambda x: x['timestamp'])
        new_history.extend(entries[-HISTORY_LIMIT:])
    return new_history

def get_hash(content):
    if isinstance(content, str):
        content = content.encode('utf-8')
    return hashlib.sha256(content).hexdigest()

def save_to_archive(filename, content):
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
    filepath = os.path.join(OUTPUT_DIR, filename)
    mode = 'wb' if isinstance(content, bytes) else 'w'
    encoding = None if isinstance(content, bytes) else 'utf-8'
    with open(filepath, mode, encoding=encoding) as f:
        f.write(content)
    logger.info(f"  -> Saved to: {filepath}")
    return filename

# --- Selenium / Scraping Functions ---

def initialize_driver():
    logger.info("  -> Initializing Selenium Driver...")
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')
    
    try:
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        stealth(driver,
                languages=["en-US", "en"],
                vendor="Google Inc.",
                platform="Win32",
                webgl_vendor="Intel Inc.",
                renderer="Intel Iris OpenGL Engine",
                fix_hairline=True,
        )
        return driver
    except Exception as e:
        logger.error(f"  [x] Failed to initialize WebDriver: {e}")
        return None

def clean_html_content(html):
    soup = BeautifulSoup(html, 'html.parser')
    page_body = soup.body
    if not page_body:
        return ""

    for tag_selector in TAGS_TO_EXCLUDE:
        for tag in page_body.select(tag_selector):
            tag.decompose()
    
    text_content = str(page_body)
    # Noise Reduction
    text_content = re.sub(r'Generated on:? \d{1,2}/\d{1,2}/\d{4}.*', '', text_content, flags=re.IGNORECASE)
    text_content = re.sub(r'Last updated:? \d{1,2}:\d{2}.*', '', text_content, flags=re.IGNORECASE)
    return text_content

def fetch_webpage_content(driver, url, max_retries=2):
    if url.lower().endswith('.pdf'):
        logger.warning(f"  [!] Skipping PDF link: {url}")
        return None

    for attempt in range(max_retries + 1):
        try:
            driver.get(url)
            WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
            
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
            time.sleep(random.uniform(1.0, 2.0))

            html_content = driver.page_source
            if any(sig in html_content.lower() for sig in BLOCK_PAGE_SIGNATURES):
                logger.warning(f"  [x] Block page detected at {url}.")
                return None

            cleaned_html = clean_html_content(html_content)
            if not cleaned_html:
                return None

            return md(cleaned_html, heading_style="ATX")
        except Exception as e:
            logger.warning(f"  [x] Attempt {attempt+1} failed for {url}: {e}")
            time.sleep(2)
    return None

# --- Legislation API Logic (Optimized) ---

def fetch_legislation_metadata(session, source):
    """
    Fetches metadata with PERFORMANCE OPTIMIZATION.
    - Reverts to 'titleid' (lowercase) to fix 400 Bad Request.
    - Adds '$select' to fetch ONLY essential columns, preventing timeouts.
    """
    base_url = source['base_url']
    target_title_id = source['title_id']
    target_format = source.get('format', 'Word') 
    
    # PERFORMANCE KEY: '$select' restricts the query to light metadata fields only.
    # This prevents the server from loading heavy blobs for every row.
    params = {
        "$filter": f"titleid eq '{target_title_id}'", 
        "$orderby": "start desc",
        "$top": "20",
        "$select": "registerId,format,start,type,uniqueTypeNumber,volumeNumber,rectificationVersionNumber"
    }
    headers = {'Accept': 'application/json'}
    
    try:
        resp = session.get(base_url, params=params, headers=headers, timeout=45)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"  [x] Metadata request failed: {e}")
        return None, None
    
    documents = data.get('value', [])
    if not documents:
        return None, None

    # Client-Side Selection: Find latest Word doc
    selected_doc = None
    documents.sort(key=lambda x: x.get('start', ''), reverse=True)
    
    for doc in documents:
        if doc.get('format') == target_format:
            selected_doc = doc
            break
            
    if not selected_doc:
        selected_doc = documents[0]
        logger.warning(f"  [!] Desired format '{target_format}' not found. Falling back to '{selected_doc.get('format')}'.")

    return selected_doc.get('registerId'), selected_doc

def download_legislation_content(session, doc_meta):
    """
    Downloads content using the 'Canonical URI' from __metadata.
    """
    try:
        # Use the API's own self-link if available (Most reliable method)
        if '__metadata' in doc_meta and 'uri' in doc_meta['__metadata']:
            base_uri = doc_meta['__metadata']['uri']
            download_url = f"{base_uri}/$value"
            logger.info(f"  -> Downloading from Canonical URI: {download_url}")
        else:
            # Fallback for when __metadata is excluded by $select or missing
            # Reconstruct the URI manually using the proven fields
            def q(val): return f"'{val}'"
            reg_id = doc_meta.get('registerId')
            d_type = doc_meta.get('type')
            fmt = doc_meta.get('format')
            uniq_num = int(doc_meta.get('uniqueTypeNumber') or 0)
            vol_num = int(doc_meta.get('volumeNumber') or 0)
            rect_ver = int(doc_meta.get('rectificationVersionNumber') or 0)
            
            segment = f"registerId={q(reg_id)},type={q(d_type)},format={q(fmt)},uniqueTypeNumber={uniq_num},volumeNumber={vol_num},rectificationVersionNumber={rect_ver}"
            download_url = f"https://api.prod.legislation.gov.au/v1/documents/find({segment})/$value"
            logger.info(f"  -> Downloading from Constructed URI: {download_url}")

        response = session.get(download_url, headers={"Accept": "*/*"}, stream=True, timeout=90)
        response.raise_for_status()
        return response.content

    except Exception as e:
        logger.error(f"  [x] Download failed: {e}")
        return None

# --- Main Execution ---

def main():
    if not os.path.exists(SOURCES_FILE):
        logger.critical(f"Error: {SOURCES_FILE} not found.")
        sys.exit(1)

    with open(SOURCES_FILE, 'r') as f:
        sources = json.load(f)

    history = load_history()
    session = get_robust_session()
    
    # Check if we need the browser
    has_web_sources = any(s.get('type') == 'WebPage' for s in sources)
    driver = initialize_driver() if has_web_sources else None

    updates_found = False
    logger.info(f"--- Tripwire Run: {datetime.datetime.now()} ---")

    for source in sources:
        name = source.get('name')
        stype = source.get('type')
        priority = source.get('priority', 'Low')
        output_filename = source.get('output_filename', f"{name.replace(' ', '_')}.dat")
        
        logger.info(f"Checking {name} ({stype})...")
        
        content_to_save = None
        version_id = None
        details_str = ""
        is_new = False
        
        try:
            # 1. LEGISLATION
            if stype == "Legislation_OData":
                ver_id, meta = fetch_legislation_metadata(session, source)
                if not ver_id: continue
                
                # Check history
                if not any(h['source_name'] == name and h['version_id'] == ver_id for h in history):
                    logger.info(f"  [!] NEW LEGISLATION VERSION ({ver_id}).")
                    content_to_save = download_legislation_content(session, meta)
                    if content_to_save:
                        version_id = ver_id
                        details_str = f"Legislation Update ({meta.get('format')})"
                        is_new = True
                else:
                    logger.info("  No change (Version Match).")

            # 2. WEB PAGE
            elif stype == "WebPage":
                if not driver: 
                    logger.warning("  [!] Web driver required but not initialized.")
                    continue
                
                markdown_content = fetch_webpage_content(driver, source['url'])
                if markdown_content:
                    current_hash = get_hash(markdown_content)
                    if not any(h['source_name'] == name and h['version_id'] == current_hash for h in history):
                        logger.info(f"  [!] WEBPAGE CHANGE DETECTED.")
                        content_to_save = markdown_content
                        version_id = current_hash
                        details_str = "Web Scrape Update"
                        is_new = True
                    else:
                        logger.info("  No change.")

            # 3. RSS / API
            elif stype in ["RSS", "API"]:
                resp = session.get(source['url'], timeout=15)
                if resp.status_code == 200:
                    current_hash = get_hash(resp.content)
                    if not any(h['source_name'] == name and h['version_id'] == current_hash for h in history):
                        logger.info(f"  [!] RSS/API CHANGE DETECTED.")
                        content_to_save = resp.content
                        version_id = current_hash
                        details_str = "RSS/API Update"
                        is_new = True
                    else:
                        logger.info("  No change.")

            # --- SAVE & UPDATE ---
            if is_new and content_to_save:
                saved_file = save_to_archive(output_filename, content_to_save)
                new_entry = {
                    "source_name": name,
                    "version_id": version_id,
                    "content_hash": get_hash(content_to_save),
                    "timestamp": datetime.datetime.now().isoformat(),
                    "priority": priority,
                    "details": details_str,
                    "file": saved_file
                }
                history.append(new_entry)
                updates_found = True

        except Exception as e:
            logger.error(f"  [x] Unexpected error for {name}: {e}")

    if driver:
        driver.quit()

    if updates_found:
        history = prune_history(history)
        save_history(history)
        logger.info("--- Updates completed and saved ---")
    else:
        logger.info("--- No updates found ---")

if __name__ == "__main__":
    main()
