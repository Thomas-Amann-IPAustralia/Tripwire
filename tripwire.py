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
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Tripwire")

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
    """Keeps only the last N entries per source to prevent JSON bloat."""
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

    # Semantic Tagging for LLM Clarity
    for tag in page_body.find_all(['em', 'i']):
        tag.insert_before(' __SEMANTIC_ITALIC_START__ ')
        tag.insert_after(' __SEMANTIC_ITALIC_END__ ')
        tag.unwrap()
    for tag in page_body.find_all(['strong', 'b']):
        tag.insert_before(' __SEMANTIC_BOLD_START__ ')
        tag.insert_after(' __SEMANTIC_BOLD_END__ ')
        tag.unwrap()
    for i in range(1, 7):
        for tag in page_body.find_all(f'h{i}'):
            tag.insert_before(f' __SEMANTIC_H{i}_START__ ')
            tag.insert_after(f' __SEMANTIC_H{i}_END__ ')
            tag.unwrap()
            
    # Cleanup DOM
    for tag_selector in TAGS_TO_EXCLUDE:
        for tag in page_body.select(tag_selector):
            tag.decompose()
    
    text_content = str(page_body)

    # --- NEW: Noise Reduction Regex ---
    # Removes dynamic timestamps that trigger false positives
    text_content = re.sub(r'Generated on:? \d{1,2}/\d{1,2}/\d{4}.*', '', text_content, flags=re.IGNORECASE)
    text_content = re.sub(r'Last updated:? \d{1,2}:\d{2}.*', '', text_content, flags=re.IGNORECASE)
    text_content = re.sub(r'\d{1,2}:\d{2}:\d{2} [AP]M', '', text_content) # Simple time masking

    return text_content

def fetch_webpage_content(driver, url, max_retries=2):
    if url.lower().endswith('.pdf'):
        logger.warning(f"  [!] Skipping PDF link in scraper: {url}")
        return None

    for attempt in range(max_retries + 1):
        try:
            driver.get(url)
            WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
            
            # Human-like scroll
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

# --- Legislation API Logic ---

def fetch_legislation_metadata(source):
    base_url = source['base_url']
    title_id = source['title_id']
    doc_format = source.get('format', 'Word') # Default to Word if unspecified
    
    params = {
        "$filter": f"titleid eq '{title_id}' and format eq '{doc_format}'",
        "$orderby": "start desc",
        "$top": "1"
    }
    headers = {'User-Agent': 'TripwireBot/1.0', 'Accept': 'application/json'}
    try:
        resp = requests.get(base_url, params=params, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if not data.get('value'): return None, None
        latest_meta = data['value'][0]
        # Use registerId as version_id to avoid downloading duplicate content
        return latest_meta.get('registerId'), latest_meta
    except Exception as e:
        logger.error(f"  [x] Legislation API error: {e}")
        return None, None

def download_legislation_content(base_url, meta):
    try:
        keys = {k: meta[k] for k in ['titleId', 'start', 'retrospectiveStart', 'rectificationVersionNumber', 'type', 'uniqueTypeNumber', 'volumeNumber', 'format']}
        path_segment = f"titleid='{keys['titleId']}',start='{keys['start']}',retrospectivestart='{keys['retrospectiveStart']}',rectificationversionnumber={keys['rectificationVersionNumber']},type='{keys['type']}',uniqueTypeNumber={keys['uniqueTypeNumber']},volumeNumber={keys['volumeNumber']},format='{keys['format']}'"
        download_url = f"{base_url}({path_segment})/$value"
        
        file_resp = requests.get(download_url, headers={'User-Agent': 'TripwireBot/1.0'}, timeout=60)
        file_resp.raise_for_status()
        return file_resp.content
    except Exception as e:
        logger.error(f"  [x] Legislation download error: {e}")
        return None

# --- Main Execution ---

def main():
    if not os.path.exists(SOURCES_FILE):
        logger.critical(f"Error: {SOURCES_FILE} not found.")
        sys.exit(1)

    with open(SOURCES_FILE, 'r') as f:
        sources = json.load(f)

    history = load_history()
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
            # 1. Handle Legislation
            if stype == "Legislation_OData":
                ver_id, meta = fetch_legislation_metadata(source)
                if not ver_id: continue
                
                # Check history by Version ID (more efficient than hash for APIs)
                if not any(h['source_name'] == name and h['version_id'] == ver_id for h in history):
                    logger.info(f"  [!] NEW LEGISLATION VERSION ({ver_id}).")
                    content_to_save = download_legislation_content(source['base_url'], meta)
                    if content_to_save:
                        version_id = ver_id
                        details_str = f"Legislation Update ({source.get('format')})"
                        is_new = True

            # 2. Handle Web Pages
            elif stype == "WebPage":
                if not driver: continue
                markdown_content = fetch_webpage_content(driver, source['url'])
                
                if markdown_content:
                    current_hash = get_hash(markdown_content)
                    # For webpages, Version ID is the Hash
                    if not any(h['source_name'] == name and h['version_id'] == current_hash for h in history):
                        logger.info(f"  [!] WEBPAGE CHANGE DETECTED.")
                        content_to_save = markdown_content
                        version_id = current_hash
                        details_str = "Web Scrape Update"
                        is_new = True

            # 3. Handle RSS/API
            elif stype in ["RSS", "API"]:
                resp = requests.get(source['url'], timeout=15)
                if resp.status_code == 200:
                    current_hash = get_hash(resp.content)
                    if not any(h['source_name'] == name and h['version_id'] == current_hash for h in history):
                        logger.info(f"  [!] RSS/API CHANGE DETECTED.")
                        content_to_save = resp.content
                        version_id = current_hash
                        details_str = "RSS/API Update"
                        is_new = True

            # --- Save & Update History ---
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
            else:
                logger.info("  No change.")

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
