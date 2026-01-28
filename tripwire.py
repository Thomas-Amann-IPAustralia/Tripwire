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
    1. Searches for documents by Register ID (title_id).
    2. Filters the results in Python to find the .docx version (or falls back).
    [cite_start][cite: 8] GET /v1/documents
    """
    base_url = source['base_url']
    target_title_id = source['title_id'] 
    
    # Query for the TitleID (Series ID). We fetch the top 20 to ensure we catch the format we want.
    params = {
        "$filter": f"titleid eq '{target_title_id}'", 
        "$orderby": "start desc",
        "$top": "20"
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

    # Filter for the desired format. We want the latest version that has a Word doc.
    selected_doc = None
    
    for doc in documents:
        fmt = doc.get('format', '').lower()
        if '.docx' in fmt or '.doc' in fmt or 'officedocument' in fmt:
             selected_doc = doc
             break
    
    # Fallback: If no .docx found, take the latest one regardless of format
    if not selected_doc:
        logger.warning("  [!] No .docx format found. Falling back to latest available format.")
        selected_doc = documents[0]

    version_identifier = selected_doc.get('registerId') 
    
    logger.info(f"  -> Found version: {version_identifier} ({selected_doc.get('format')}) dated {selected_doc.get('start')}")
    return version_identifier, selected_doc

def download_legislation_content(session, version_register_id):
    """
    Downloads the binary content using the simple Content endpoint.
    [cite_start][cite: 10] GET /v1/Content({key})
    """
    api_root = "https://api.prod.legislation.gov.au/v1" 
    download_url = f"{api_root}/Content('{version_register_id}')"
    
    logger.info(f"  -> Downloading content for {version_register_id}...")
    
    try:
        # Accept */* to prevent 406 Not Acceptable errors on binary files
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
                
                # Check DB for existing RegisterID
                cursor.execute('SELECT id FROM history WHERE source_name = ? AND version_id = ?', (name, found_ver_id))
                if cursor.fetchone():
                    logger.info("  No change (Version ID match).")
                    continue 
                
                logger.info(f"  [!] NEW VERSION DETECTED ({found_ver_id}). Downloading...")
                content_bytes = download_legislation_content(session, found_ver_id)
                
                if content_bytes is None:
                    logger.error("  [x] Skipping update due to download failure.")
                    continue

                version_id = found_ver_id
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
