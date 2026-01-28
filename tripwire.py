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

def prune_history(history):
    """
    Keeps only the last N entries per source.
    Assumes the list is roughly chronological (appended).
    """
    new_history = []
    # Get unique source names
    source_names = set(entry['source_name'] for entry in history)
    
    for name in source_names:
        # Filter entries for this source
        source_entries = [e for e in history if e['source_name'] == name]
        # Sort by timestamp just in case (descending)
        source_entries.sort(key=lambda x: x['timestamp'])
        # Keep only the last N
        kept_entries = source_entries[-HISTORY_LIMIT:]
        new_history.extend(kept_entries)
    
    return new_history

def get_hash(content_bytes):
    """Generates SHA256 hash of content."""
    if content_bytes is None:
        return "NO_CONTENT"
    return hashlib.sha256(content_bytes).hexdigest()

def fetch_legislation_metadata(session, source):
    """
    Fetches metadata from FRL API using the logic that successfully finds
    Word/PDF formats and their specific metadata.
    """
    base_url = source['base_url']
    target_title_id = source['title_id'] 
    
    # Query for the TitleID (Series ID).
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

    # --- INTELLIGENT SELECTION ---
    # Group docs by 'start' date first
    docs_by_date = {}
    for doc in documents:
        start_date = doc.get('start')
        if start_date not in docs_by_date:
            docs_by_date[start_date] = []
        docs_by_date[start_date].append(doc)
    
    # Sort dates descending (latest first)
    sorted_dates = sorted(docs_by_date.keys(), reverse=True)
    
    selected_doc = None
    
    # Try to find Word or PDF in the latest version(s)
    for date in sorted_dates:
        version_docs = docs_by_date[date]
        
        # Priority 1: Word
        for doc in version_docs:
            if doc.get('format') == 'Word':
                selected_doc = doc
                break
        if selected_doc: break
        
        # Priority 2: Pdf
        for doc in version_docs:
            if doc.get('format') == 'Pdf':
                selected_doc = doc
                break
        if selected_doc: break
        
        # Priority 3: Epub
        for doc in version_docs:
            if doc.get('format') == 'Epub':
                selected_doc = doc
                break
        if selected_doc: break

    if not selected_doc:
        logger.warning("  [!] No usable format found. Defaulting to first record.")
        selected_doc = documents[0]

    version_identifier = selected_doc.get('registerId') 
    
    logger.info(f"  -> Found version: {version_identifier} | Format: {selected_doc.get('format')} | Date: {selected_doc.get('start')}")
    return version_identifier, selected_doc

def download_legislation_content(session, doc_meta):
    """
    Downloads the binary content using the OData 'find' composite key.
    """
    # Helper to safely quote strings
    def q(val): return f"'{val}'"
    
    try:
        # Extract keys EXACTLY as they appear in the metadata object
        reg_id = doc_meta.get('registerId')
        d_type = doc_meta.get('type')
        fmt = doc_meta.get('format') 
        
        # Numbers must be unquoted integers
        uniq_num = int(doc_meta.get('uniqueTypeNumber') or 0)
        vol_num = int(doc_meta.get('volumeNumber') or 0)
        rect_ver = int(doc_meta.get('rectificationVersionNumber') or 0)

        # Construct the URL based on Source [162]
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

    # Load existing history (JSON)
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

            # --- LEGISLATION (API) CHECK ---
            if stype == "Legislation_OData":
                found_ver_id, meta = fetch_legislation_metadata(session, source)
                
                if not found_ver_id:
                    continue 
                
                # Create a unique key for history checking
                # We combine RegisterID + Format
                db_ver_key = f"{found_ver_id}_{meta.get('format')}"
                
                # Check if this specific version key exists in history
                if any(h['source_name'] == name and h['version_id'] == db_ver_key for h in history):
                    logger.info("  No change (Version Match).")
                    continue 
                
                logger.info(f"  [!] NEW VERSION DETECTED ({found_ver_id}). Downloading...")
                content_bytes = download_legislation_content(session, meta)
                
                if content_bytes is None:
                    logger.error("  [x] Skipping update due to download failure.")
                    continue

                version_id = db_ver_key
                current_hash = get_hash(content_bytes)
                details_str = f"Legislation Update. Format: {meta.get('format')}. Date: {meta.get('start')}"

            # --- RSS / GENERIC CHECK ---
            elif stype == "RSS" or stype == "API":
                resp = session.get(source['url'], timeout=15)
                resp.raise_for_status()
                content_bytes = resp.content
                
                current_hash = get_hash(content_bytes)
                version_id = current_hash 
                
                # Check if this hash exists in history
                if any(h['source_name'] == name and h['version_id'] == version_id for h in history):
                     logger.info("  No change.")
                     continue
                
                logger.info(f"  [!] CHANGE DETECTED.")
                details_str = "RSS/API Update"

            # --- SAVE UPDATE ---
            timestamp = datetime.datetime.now().isoformat()
            
            new_entry = {
                "source_name": name,
                "version_id": version_id,
                "content_hash": current_hash,
                "timestamp": timestamp,
                "priority": priority,
                "details": details_str
            }
            
            history.append(new_entry)
            updates_found = True

        except Exception as e:
            logger.error(f"  [x] Error checking {name}: {e}")

    # Prune and Save
    if updates_found:
        history = prune_history(history)
        save_history(history)
        logger.info("--- Updates completed and saved to JSON ---")
    else:
        logger.info("--- No updates found ---")

if __name__ == "__main__":
    main()
