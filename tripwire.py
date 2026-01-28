import json
import hashlib
import requests
import datetime
import os
import sys
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- Configuration ---
HISTORY_FILE = 'tripwire_history.json'
SOURCES_FILE = 'sources.json'
DOWNLOAD_DIR = 'content_archive'  # Folder to save raw documents
HISTORY_LIMIT = 10 

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("Tripwire")

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

def load_history():
    """Loads history from JSON file."""
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        logger.warning("History file is corrupt or empty. Starting fresh.")
        return []

def save_history(history):
    """Saves history to JSON file."""
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save history: {e}")

def save_raw_file(filename, content_bytes):
    """Saves binary content to the archive directory."""
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)
    
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    try:
        with open(filepath, 'wb') as f:
            f.write(content_bytes)
        logger.info(f"  -> Saved raw file to: {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"  [x] Failed to save raw file {filename}: {e}")
        return None

def prune_history(history):
    """Keeps only the last N entries per source."""
    new_history = []
    source_names = set(entry['source_name'] for entry in history)
    
    for name in source_names:
        source_entries = [e for e in history if e['source_name'] == name]
        source_entries.sort(key=lambda x: x['timestamp'])
        kept_entries = source_entries[-HISTORY_LIMIT:]
        new_history.extend(kept_entries)
    
    return new_history

def get_hash(content_bytes):
    """Generates SHA256 hash of content."""
    if content_bytes is None:
        return "NO_CONTENT"
    return hashlib.sha256(content_bytes).hexdigest()

def fetch_legislation_metadata(session, source):
    """Fetches metadata using Intelligent Selection (Word > Pdf > Epub)."""
    base_url = source['base_url']
    target_title_id = source['title_id'] 
    
    params = {
        "$filter": f"titleid eq '{target_title_id}'", 
        "$orderby": "start desc",
        "$top": "40"
    }
    
    headers = {'Accept': 'application/json'}
    
    logger.info(f"  -> Discovery: Querying metadata for {target_title_id}...")
    
    try:
        resp = session.get(base_url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"  [x] Metadata request failed: {e}")
        return None, None
    
    documents = data.get('value', [])
    if not documents:
        logger.warning(f"  [!] No documents found for {target_title_id}")
        return None, None

    # Group docs by 'start' date
    docs_by_date = {}
    for doc in documents:
        start_date = doc.get('start')
        if start_date not in docs_by_date:
            docs_by_date[start_date] = []
        docs_by_date[start_date].append(doc)
    
    sorted_dates = sorted(docs_by_date.keys(), reverse=True)
    selected_doc = None
    
    # Priority: Word > Pdf > Epub
    for date in sorted_dates:
        version_docs = docs_by_date[date]
        for doc in version_docs:
            if doc.get('format') == 'Word':
                selected_doc = doc; break
        if selected_doc: break
        
        for doc in version_docs:
            if doc.get('format') == 'Pdf':
                selected_doc = doc; break
        if selected_doc: break
        
        for doc in version_docs:
            if doc.get('format') == 'Epub':
                selected_doc = doc; break
        if selected_doc: break

    if not selected_doc:
        logger.warning("  [!] No usable format found. Defaulting to first record.")
        selected_doc = documents[0]

    version_identifier = selected_doc.get('registerId') 
    logger.info(f"  -> Found version: {version_identifier} | Format: {selected_doc.get('format')} | Date: {selected_doc.get('start')}")
    return version_identifier, selected_doc

def download_legislation_content(session, doc_meta):
    """Downloads binary content using the OData composite key."""
    def q(val): return f"'{val}'"
    
    try:
        reg_id = doc_meta.get('registerId')
        d_type = doc_meta.get('type')
        fmt = doc_meta.get('format')
        uniq_num = int(doc_meta.get('uniqueTypeNumber') or 0)
        vol_num = int(doc_meta.get('volumeNumber') or 0)
        rect_ver = int(doc_meta.get('rectificationVersionNumber') or 0)

        segment = (
            f"registerId={q(reg_id)},"
            f"type={q(d_type)},"
            f"format={q(fmt)},"
            f"uniqueTypeNumber={uniq_num},"
            f"volumeNumber={vol_num},"
            f"rectificationVersionNumber={rect_ver}"
        )
        
        download_url = f"https://api.prod.legislation.gov.au/v1/documents/find({segment})"
        logger.info(f"  -> Downloading from: {download_url}")
        
        response = session.get(download_url, headers={"Accept": "*/*"}, stream=True, timeout=90)
        response.raise_for_status()
        return response.content

    except Exception as e:
        logger.error(f"  [x] Download failed: {e}")
        return None

def main():
    if not os.path.exists(SOURCES_FILE):
        logger.critical(f"Error: {SOURCES_FILE} not found.")
        sys.exit(1)

    with open(SOURCES_FILE, 'r') as f:
        sources = json.load(f)

    history = load_history()
    session = get_robust_session()
    
    updates_found = False

    logger.info(f"--- Tripwire Run: {datetime.datetime.now()} ---")

    for source in sources:
        name = source.get('name')
        stype = source.get('type')
        priority = source.get('priority', 'Low')
        
        try:
            logger.info(f"Checking {name}...")
            
            content_bytes = None
            version_id = None
            details_str = ""
            current_hash = None
            saved_filename = None

            # --- LEGISLATION (API) CHECK ---
            if stype == "Legislation_OData":
                found_ver_id, meta = fetch_legislation_metadata(session, source)
                
                if not found_ver_id:
                    continue 
                
                db_ver_key = f"{found_ver_id}_{meta.get('format')}"
                
                if any(h['source_name'] == name and h['version_id'] == db_ver_key for h in history):
                    logger.info("  No change (Version Match).")
                    continue 
                
                logger.info(f"  [!] NEW VERSION DETECTED ({found_ver_id}). Downloading...")
                content_bytes = download_legislation_content(session, meta)
                
                if content_bytes is None:
                    logger.error("  [x] Skipping update due to download failure.")
                    continue

                # Determine Extension & Save
                ext = meta.get('extension') # Explicit extension in metadata
                if not ext:
                    fmt_lower = meta.get('format', '').lower()
                    if 'word' in fmt_lower: ext = '.docx'
                    elif 'pdf' in fmt_lower: ext = '.pdf'
                    elif 'epub' in fmt_lower: ext =
