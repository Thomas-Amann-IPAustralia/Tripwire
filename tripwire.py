import json
import csv
import hashlib
import requests
import datetime
import os
import sys
import time
import random
import logging
import re
import subprocess
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
AUDIT_LOG = 'audit_log.csv' # NEW: Replaces HISTORY_FILE 
SOURCES_FILE = 'sources.json'
OUTPUT_DIR = 'content_archive'

# --- Scrape Config  ---
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

# --- Stage 0: Audit & Metadata Helpers  ---

def get_last_version_id(source_name: str) -> Optional[str]:
    """Reads the Audit CSV to find the last known Version_ID."""
    if not os.path.exists(AUDIT_LOG):
        return None
    try:
        with open(AUDIT_LOG, mode='r', encoding='utf-8') as f:
            reader = list(csv.DictReader(f))
            for row in reversed(reader):
                if row['Source_Name'] == source_name and row['Status'] == 'Success':
                    return row['Version_ID']
    except Exception:
        return None
    return None

def log_to_audit(name, priority, status, change_detected, version_id):
    """Universal CSV logger for auditability."""
    file_exists = os.path.exists(AUDIT_LOG)
    with open(AUDIT_LOG, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Timestamp', 'Source_Name', 'Priority', 'Status', 'Change_Detected', 'Version_ID'])
        writer.writerow([datetime.datetime.now().isoformat(), name, priority, status, change_detected, version_id])

def fetch_stage0_metadata(session, source) -> Optional[str]:
    """Lightweight metadata fetch to determine if Stage 1 is needed."""
    stype = source.get('type')
    try:
        if stype == "Legislation_OData":
            params = {"$filter": f"titleid eq '{source['title_id']}'", "$orderby": "start desc", "$top": "1"}
            resp = session.get(source['base_url'], params=params, timeout=20)
            return resp.json().get('value', [{}])[0].get('registerId')
        elif stype in ["RSS", "WebPage"]:
            resp = session.head(source['url'], timeout=15)
            # Use ETag or Content-Length as a unique identifier
            return resp.headers.get('ETag') or resp.headers.get('Content-Length')
    except Exception:
        return None
    return None

# --- Stage 1: Extraction & Normalization  ---

def get_robust_session():
    session = requests.Session()
    retry_strategy = Retry(total=5, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retry_strategy))
    return session

def initialize_driver():
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    try:
        driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=chrome_options)
        stealth(driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32", fix_hairline=True)
        return driver
    except Exception as e:
        logger.error(f"Failed to initialize WebDriver: {e}")
        return None

def clean_html_content(html):
    soup = BeautifulSoup(html, 'html.parser')
    body = soup.body
    if not body: return ""
    for selector in TAGS_TO_EXCLUDE:
        for tag in body.select(selector): tag.decompose()
    text = str(body)
    text = re.sub(r'Generated on:? \d{1,2}/\d{1,2}/\d{4}.*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'Last updated:? \d{1,2}:\d{2}.*', '', text, flags=re.IGNORECASE)
    return text

def fetch_webpage_content(driver, url):
    try:
        driver.get(url)
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
        cleaned = clean_html_content(driver.page_source)
        return md(cleaned, heading_style="ATX")
    except Exception:
        return None

def fetch_legislation_metadata(session, source):
    params = {"$filter": f"titleid eq '{source['title_id']}'", "$orderby": "start desc", "$top": "1"}
    resp = session.get(source['base_url'], params=params, timeout=30)
    docs = resp.json().get('value', [])
    return (docs[0].get('registerId'), docs[0]) if docs else (None, None)

def download_legislation_content(session, doc_meta):
    def q(val): return f"'{val}'"
    reg_id = doc_meta.get('registerId')
    download_url = f"https://api.prod.legislation.gov.au/v1/documents/find(registerId={q(reg_id)},type={q(doc_meta.get('type'))},format={q(doc_meta.get('format'))},uniqueTypeNumber={int(doc_meta.get('uniqueTypeNumber') or 0)},volumeNumber={int(doc_meta.get('volumeNumber') or 0)},rectificationVersionNumber={int(doc_meta.get('rectificationVersionNumber') or 0)})"
    resp = session.get(download_url, stream=True, timeout=90)
    return resp.content

def save_to_archive(filename, content):
    if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)
    filepath = os.path.join(OUTPUT_DIR, filename)
    mode, encoding = ('wb', None) if isinstance(content, bytes) else ('w', 'utf-8')
    with open(filepath, mode, encoding=encoding) as f: f.write(content)
    return filepath

# --- Main Execution ---

def main():
    if not os.path.exists(SOURCES_FILE):
        logger.critical(f"Error: {SOURCES_FILE} not found.")
        sys.exit(1)

    with open(SOURCES_FILE, 'r') as f:
        sources = json.load(f)

    session = get_robust_session()
    driver = None
    logger.info(f"--- Tripwire Run: {datetime.datetime.now()} ---")

    for source in sources:
        name = source.get('name')
        stype = source.get('type')
        priority = source.get('priority', 'Low')
        output_filename = source.get('output_filename')
        
        # --- STAGE 0: THE SENTRY ---
        old_id = get_last_version_id(name)
        current_id = fetch_stage0_metadata(session, source)
        
        # LOG NO CHANGE: Ensure "No Change" is recorded and skip to next
        if old_id and current_id and old_id == current_id:
            logger.info(f"Stage 0: No change for {name}.")
            log_to_audit(name, priority, "Success", "No", current_id)
            continue

        # --- STAGE 1: EXTRACTION ---
        logger.info(f"Stage 1: Processing {name}...")
        try:
            if stype == "Legislation_OData":
                ver_id, meta = fetch_legislation_metadata(session, source)
                if ver_id:
                    content = download_legislation_content(session, meta)
                    if content:
                        save_to_archive(output_filename, content)
                        log_to_audit(name, priority, "Success", "Yes", ver_id)
                    else:
                        log_to_audit(name, priority, "Error: Download Failed", "N/A", ver_id)
                else:
                    log_to_audit(name, priority, "Error: Metadata Failed", "N/A", current_id)
            
            elif stype == "WebPage":
                if not driver: driver = initialize_driver()
                markdown = fetch_webpage_content(driver, source['url'])
                if markdown:
                    save_to_archive(output_filename, markdown)
                    log_to_audit(name, priority, "Success", "Yes", current_id)
                else:
                    log_to_audit(name, priority, "Error: Scrape Failed", "N/A", current_id)

            elif stype == "RSS":
                resp = session.get(source['url'], timeout=15)
                if resp.status_code == 200:
                    save_to_archive(output_filename, resp.content)
                    log_to_audit(name, priority, "Success", "Yes", current_id)
                else:
                    log_to_audit(name, priority, f"Error: {resp.status_code}", "N/A", current_id)

        except Exception as e:
            logger.error(f"Unexpected error for {name}: {e}")
            log_to_audit(name, priority, "Exception", "N/A", current_id)

    if driver: driver.quit()

if __name__ == "__main__": main()
