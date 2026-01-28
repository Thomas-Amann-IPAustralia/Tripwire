import sqlite3
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
DB_FILE = 'tripwire.sqlite'
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

def init_db():
    """Initializes the SQLite database."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT,
            version_id TEXT,
            content_hash TEXT,
            timestamp DATETIME,
            priority TEXT,
            details TEXT
        )
    ''')
    conn.commit()
    return conn

def get_hash(content_bytes):
    """Generates SHA256 hash of content."""
    if content_bytes is None:
        return "NO_CONTENT"
    return hashlib.sha256(content_bytes).hexdigest()

def prune_history(cursor, source_name):
    """Keeps only the last N entries for a source."""
    cursor.execute('''
        DELETE FROM history
        WHERE source_name = ? AND id NOT IN (
            SELECT id FROM history
            WHERE source_name = ?
            ORDER BY timestamp DESC
            LIMIT ?
        )
    ''', (source_name, source_name, HISTORY_LIMIT))

def fetch_legislation_metadata(session, source):
    """
    Fetches metadata from FRL API. 
    1. Searches for all documents matching the TitleID.
    2. Selects the specific METADATA ENTRY for the desired format (Word > Pdf).
    """
    base_url = source['base_url']
    target_title_id = source['title_id'] 
    
    # Query for the TitleID (Series ID).
    # We fetch more results (top 40) to ensure we get all formats for the latest versions.
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
    # We are looking for the latest "start" date, and within that date, the best format.
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
            [cite_start]if doc.get('format') == 'Word': [cite: 1]
                selected_doc = doc
                break
        if selected_doc: break
        
        # Priority 2: Pdf
        for doc in version_docs:
            if doc.get('format') == 'Pdf':
                selected_doc = doc
                break
        if selected_doc: break
        
        # Priority 3: Epub (if nothing else)
        for doc in version_docs:
            if doc.get('format') == 'Epub':
                selected_doc = doc
                break
        if selected_doc: break

    if not selected_doc:
        logger.warning("  [!] No usable format found. Defaulting to first record.")
        selected_doc = documents[0]

    # Use registerId from the selected specific format entry
    version_identifier = selected_doc.get('registerId') 
    
    logger.info(f"  -> Found version: {version_identifier} | Format: {selected_doc.get('format')} | Date: {selected_doc.get('start')}")
    return version_identifier, selected_doc

def download_legislation_content(session, doc_meta):
    """
    Downloads the binary content using the explicit OData 'find' composite key.
    See Source [162].
    """
    
    # Helper to safely quote strings
    def q(val): return f"'{val}'"
    
    try:
        # Extract keys EXACTLY as they appear in the metadata object
        reg_id = doc_meta.get('registerId')
        d_type = doc_meta.get('type')
        fmt = doc_meta.get('format') # This will be "Word", "Pdf", etc.
        
        # Numbers must be unquoted integers
        uniq_num = int(doc_meta.get('uniqueTypeNumber') or 0)
        vol_num = int(doc_meta.get('volumeNumber') or 0)
        rect_ver = int(doc_meta.get('rectificationVersionNumber') or 0)

        # Construct the URL based on Source [162]
        # GET /v1/documents/find(registerId='...',type='...',format='...', ...)
        segment = (
            f"registerId={q(reg_id)},"
            f"type={q(d_type)},"
            f"format={q(fmt)},"
            f"uniqueTypeNumber={uniq_num},"
            f"volumeNumber={vol_num},"
            f"rectificationVersionNumber={rect_ver}"
        )
        
        # [cite_start]Note: 'find' returns the raw file bytes by default [cite: 138]
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

    conn = init_db()
    cursor = conn.cursor()
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

            # --- LEGISLATION (API) CHECK ---
            if stype == "Legislation_OData":
                found_ver_id, meta = fetch_legislation_metadata(session, source)
                
                if not found_ver_id:
                    continue 
                
                # Check DB for existing RegisterID (and ensure we have the hash for it)
                # We use the RegisterID + Format as the unique key logic
                db_ver_key = f"{found_ver_id}_{meta.get('format')}"
                
                cursor.execute('SELECT id FROM history WHERE source_name = ? AND version_id = ?', (name, db_ver_key))
                if cursor.fetchone():
                    logger.info("  No change (Version Match).")
                    continue 
                
                logger.info(f"  [!] NEW VERSION DETECTED ({found_ver_id}). Downloading...")
                content_bytes = download_legislation_content(session, meta)
                
                if content_bytes is None:
                    logger.error("  [x] Skipping update due to download failure.")
                    continue

                version_id = db_ver_key
                details_str = f"Legislation Update. Format: {meta.get('format')}. Date: {meta.get('start')}"

            # --- RSS / GENERIC CHECK ---
            elif stype == "RSS" or stype == "API":
                resp = session.get(source['url'], timeout=15)
                resp.raise_for_status()
                content_bytes = resp.content
                
                # Use hash as version ID for RSS
                current_hash = get_hash(content_bytes)
                version_id = current_hash 
                
                cursor.execute('SELECT id FROM history WHERE source_name = ? AND version_id = ?', (name, version_id))
                if cursor.fetchone():
                     logger.info("  No change.")
                     continue
                
                logger.info(f"  [!] CHANGE DETECTED.")
                details_str = "RSS/API Update"

            # --- SAVE UPDATE ---
            new_hash = get_hash(content_bytes)
            timestamp = datetime.datetime.now().isoformat()
            
            cursor.execute('''
                INSERT INTO history (source_name, version_id, content_hash, timestamp, priority, details)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (name, version_id, new_hash, timestamp, priority, details_str))
            
            prune_history(cursor, name)
            updates_found = True

        except Exception as e:
            logger.error(f"  [x] Error checking {name}: {e}")

    conn.commit()
    conn.close()

    if updates_found:
        logger.info("--- Updates completed ---")
    else:
        logger.info("--- No updates found ---")

if __name__ == "__main__":
    main()
