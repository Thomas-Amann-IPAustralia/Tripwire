import json
import csv
import requests
import datetime
import os
import sys
import logging
import re
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
TAGS_TO_EXCLUDE = ['nav', 'footer', 'header', 'script', 'style', 'aside', '.noprint', '#sidebar', 'iframe']

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Tripwire")

# --- Stage 0: Helpers ---

def get_last_version_id(source_name: str) -> Optional[str]:
    if not os.path.exists(AUDIT_LOG): return None
    try:
        with open(AUDIT_LOG, mode='r', encoding='utf-8') as f:
            reader = list(csv.DictReader(f))
            for row in reversed(reader):
                if row['Source_Name'] == source_name and row['Status'] == 'Success':
                    return row['Version_ID']
    except Exception: return None
    return None

def log_to_audit(name, priority, status, change_detected, version_id):
    file_exists = os.path.exists(AUDIT_LOG)
    with open(AUDIT_LOG, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Timestamp', 'Source_Name', 'Priority', 'Status', 'Change_Detected', 'Version_ID'])
        writer.writerow([datetime.datetime.now().isoformat(), name, priority, status, change_detected, version_id])

def fetch_stage0_metadata(session, source) -> Optional[str]:
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

# --- Stage 1: Extraction & Normalization ---

def initialize_driver():
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=chrome_options)
    stealth(driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32", fix_hairline=True)
    return driver

def clean_html_content(html):
    soup = BeautifulSoup(html, 'html.parser')
    body = soup.body
    if not body: return ""
    for selector in TAGS_TO_EXCLUDE:
        for tag in body.select(selector): tag.decompose()
    text = str(body)
    text = re.sub(r'Generated on:? \d{1,2}/\d{1,2}/\d{4}.*', '', text, flags=re.IGNORECASE)
    return text

def fetch_webpage_content(driver, url):
    driver.get(url)
    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
    cleaned_html = clean_html_content(driver.page_source)
    return md(cleaned_html, heading_style="ATX")

def fetch_legislation_metadata(session, source):
    params = {"$filter": f"titleid eq '{source['title_id']}'", "$orderby": "start desc", "$top": "1"}
    resp = session.get(source['base_url'], params=params, timeout=30)
    docs = resp.json().get('value', [])
    return (docs[0].get('registerId'), docs[0]) if docs else (None, None)

def download_legislation_content(session, base_url, doc_meta):
    def q(val): return f"'{val}'"
    reg_id = doc_meta.get('registerId')
    download_url = f"{base_url}/find(registerId={q(reg_id)},type={q(doc_meta.get('type'))},format='Word',uniqueTypeNumber={int(doc_meta.get('uniqueTypeNumber') or 0)},volumeNumber={int(doc_meta.get('volumeNumber') or 0)},rectificationVersionNumber={int(doc_meta.get('rectificationVersionNumber') or 0)})"
    resp = session.get(download_url, stream=True, timeout=90)
    return resp.content if resp.status_code == 200 else None

def save_to_archive(filename, content, is_binary=False):
    if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)
    
    if filename.endswith('.docx') or (is_binary and filename.endswith('.md')):
        from io import BytesIO
        doc = docx.Document(BytesIO(content))
        text_content = "\n\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        filename = filename.replace('.docx', '.md')
        mode, encoding, final_content = 'w', 'utf-8', text_content
    else:
        mode, encoding = ('wb', None) if isinstance(content, bytes) else ('w', 'utf-8')
        final_content = content

    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, mode, encoding=encoding) as f:
        f.write(final_content)
    return filepath

def sanitize_rss(xml_content):
    soup = BeautifulSoup(xml_content, 'xml')
    for tag in ['lastBuildDate', 'pubDate', 'generator']:
        t = soup.find(tag)
        if t and t.parent.name == 'channel': t.decompose()
    items = soup.find_all('item')
    items.sort(key=lambda x: x.find('guid').text if x.find('guid') else (x.find('link').text if x.find('link') else ''))
    channel = soup.find('channel')
    for item in soup.find_all('item'): item.extract()
    for item in items: channel.append(item)
    return soup.prettify()

# --- Main Logic ---

def main():
    if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)
    with open(SOURCES_FILE, 'r') as f:
        sources = json.load(f)

    session = requests.Session()
    driver = None
    logger.info(f"--- Tripwire Run: {datetime.datetime.now()} ---")

    for source in sources:
        name = source['name']
        stype = source['type']
        priority = source.get('priority', 'Low')
        out_name = source['output_filename']
        out_path = os.path.join(OUTPUT_DIR, out_name.replace('.docx', '.md') if stype == "Legislation_OData" else out_name)
        
        old_id = get_last_version_id(name)
        current_id = fetch_stage0_metadata(session, source)
        file_exists = os.path.exists(out_path)
        
        # SELF-HEALING LOGIC
        repopulate_only = False
        if old_id and current_id and old_id == current_id:
            if file_exists:
                logger.info(f"Stage 0: No change and file exists for {name}. Skipping.")
                continue
            else:
                logger.warning(f"Stage 0: Version matches but file missing for {name}. Repopulating archive.")
                repopulate_only = True

        try:
            change_val = "No" if repopulate_only else "Yes"
            
            if stype == "Legislation_OData":
                ver_id, meta = fetch_legislation_metadata(session, source)
                content = download_legislation_content(session, source['base_url'], meta)
                if content:
                    save_to_archive(out_name, content, is_binary=True)
                    log_to_audit(name, priority, "Success", change_val, ver_id)

            elif stype == "RSS":
                resp = session.get(source['url'], timeout=15)
                clean_xml = sanitize_rss(resp.content)
                save_to_archive(out_name, clean_xml)
                log_to_audit(name, priority, "Success", change_val, current_id)

            elif stype == "WebPage":
                if not driver: driver = initialize_driver()
                markdown = fetch_webpage_content(driver, source['url'])
                if markdown:
                    save_to_archive(out_name, markdown)
                    log_to_audit(name, priority, "Success", change_val, current_id)

        except Exception as e:
            logger.error(f"Failed {name}: {e}")
            log_to_audit(name, priority, "Exception", "N/A", current_id)

    if driver: driver.quit()

if __name__ == "__main__":
    main()
