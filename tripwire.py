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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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
import docx # Required: pip install python-docx

# --- Configuration ---
AUDIT_LOG = 'audit_log.csv' 
SOURCES_FILE = 'sources.json'
OUTPUT_DIR = 'content_archive'
DIFF_DIR = 'diff_archive'
TAGS_TO_EXCLUDE = ['nav', 'footer', 'header', 'script', 'style', 'aside', '.noprint', '#sidebar', 'iframe']

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Tripwire")

# --- Stage 0: Helpers ---

def get_last_version_id(source_name: str) -> Optional[str]:
    """Retrieves the last successful Version_ID from the audit log."""
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
    """Logs results to CSV including Diff_File."""
    file_exists = os.path.exists(AUDIT_LOG)
    # Define columns explicitly to ensure headers match data
    headers = ['Timestamp', 'Source_Name', 'Priority', 'Status', 'Change_Detected', 'Version_ID', 'Diff_File']
    
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
            version_id, 
            diff_file if diff_file else "N/A"
        ])

def fetch_stage0_metadata(session, source) -> Optional[str]:
    """Quick check for ETag or RegisterID to see if a full fetch is needed."""
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

# --- Stage 1 & 2: Extraction, Normalization & Diffing ---

def initialize_driver():
    """Sets up a stealthy headless Chrome instance for web scraping."""
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=chrome_options)
    stealth(driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32", fix_hairline=True)
    return driver

def clean_html_content(html):
    """Removes boilerplate noise from HTML before conversion."""
    soup = BeautifulSoup(html, 'html.parser')
    body = soup.body
    if not body: return ""
    for selector in TAGS_TO_EXCLUDE:
        for tag in body.select(selector): tag.decompose()
    text = str(body)
    text = re.sub(r'Generated on:? \d{1,2}/\d{1,2}/\d{4}.*', '', text, flags=re.IGNORECASE)
    return text

def fetch_webpage_content(driver, url):
    """Scrapes a page and returns a normalized Markdown string."""
    driver.get(url)
    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
    cleaned_html = clean_html_content(driver.page_source)
    return md(cleaned_html, heading_style="ATX")

def fetch_legislation_metadata(session, source):
    """Gets latest metadata for Australian Legislation OData."""
    params = {"$filter": f"titleid eq '{source['title_id']}'", "$orderby": "start desc", "$top": "1"}
    resp = session.get(source['base_url'], params=params, timeout=30)
    docs = resp.json().get('value', [])
    return (docs[0].get('registerId'), docs[0]) if docs else (None, None)

def download_legislation_content(session, base_url, doc_meta):
    """Downloads document (Word format) from the API."""
    def q(val): return f"'{val}'"
    reg_id = doc_meta.get('registerId')
    download_url = f"{base_url}/find(registerId={q(reg_id)},type={q(doc_meta.get('type'))},format='Word',uniqueTypeNumber={int(doc_meta.get('uniqueTypeNumber') or 0)},volumeNumber={int(doc_meta.get('volumeNumber') or 0)},rectificationVersionNumber={int(doc_meta.get('rectificationVersionNumber') or 0)})"
    resp = session.get(download_url, stream=True, timeout=90)
    return resp.content if resp.status_code == 200 else None

def sanitize_rss(xml_content):
    """Stabilizes RSS XML by stripping channel dates and sorting items."""
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

def get_diff(old_path, new_content) -> Optional[str]:
    """Executes a unified diff with 10 lines of context (Stage 2)."""
    if not os.path.exists(old_path):
        return "Initial archive creation."

    temp_path = old_path + ".tmp"
    with open(temp_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    try:
        # captures the hunk for Phase 3 impact assessment
        result = subprocess.run(
            ['diff', '-U10', old_path, temp_path],
            capture_output=True,
            text=True
        )
        return result.stdout if result.stdout else None
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

def save_to_archive(filename, content):
    """Saves the normalized string content to the archive directory."""
    if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    return filepath

def save_diff_record(name, diff_content):
    """Saves the diff hunk to a permanent file for auditing."""
    if not os.path.exists(DIFF_DIR): os.makedirs(DIFF_DIR)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    # Clean name of any characters that might interfere with file paths or CSVs
    safe_name = re.sub(r'\W+', '_', name)
    filename = f"{timestamp}_{safe_name}.diff"
    filepath = os.path.join(DIFF_DIR, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(diff_content)
    return filename

# --- Main Logic ---

def main():
    if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)
    if not os.path.exists(DIFF_DIR): os.makedirs(DIFF_DIR)
    
    with open(SOURCES_FILE, 'r') as f:
        sources = json.load(f)

    session = requests.Session()
    driver = None
    logger.info(f"--- Tripwire Stage 2 Run: {datetime.datetime.now()} ---")

    for source in sources:
        name = source['name']
        stype = source['type']
        priority = source.get('priority', 'Low')
        out_name = source['output_filename']
        
        if stype == "Legislation_OData":
            out_name = out_name.replace('.docx', '.md')
            
        out_path = os.path.join(OUTPUT_DIR, out_name)
        current_id = fetch_stage0_metadata(session, source)
        
        new_content = None

        try:
            # 1. Extraction & Normalization
            if stype == "Legislation_OData":
                ver_id, meta = fetch_legislation_metadata(session, source)
                binary_content = download_legislation_content(session, source['base_url'], meta)
                if binary_content:
                    from io import BytesIO
                    doc = docx.Document(BytesIO(binary_content))
                    new_content = "\n\n".join([p.text for p in doc.paragraphs if p.text.strip()])
                    current_id = ver_id

            elif stype == "RSS":
                resp = session.get(source['url'], timeout=15)
                new_content = sanitize_rss(resp.content)

            elif stype == "WebPage":
                if not driver: driver = initialize_driver()
                new_content = fetch_webpage_content(driver, source['url'])

            # 2. Stage 2: Substantive Change Detection
            if new_content:
                diff_hunk = get_diff(out_path, new_content)
                
                if diff_hunk:
                    logger.info(f"Substantive change detected for {name}.")
                    # Record the hunk permanently
                    diff_file = save_diff_record(name, diff_hunk)
                    # Update the archive
                    save_to_archive(out_name, new_content)
                    # Log with the reference to the diff file
                    log_to_audit(name, priority, "Success", "Yes", current_id, diff_file)
                else:
                    logger.info(f"No substantive change for {name}.")
                    log_to_audit(name, priority, "Success", "No", current_id)

        except Exception as e:
            logger.error(f"Failed {name}: {e}")
            log_to_audit(name, priority, "Exception", "N/A", current_id)

    if driver: driver.quit()

if __name__ == "__main__":
    main()
