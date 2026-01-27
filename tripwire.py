import sqlite3
import json
import hashlib
import requests
import datetime
import os
import sys

# --- Configuration ---
DB_FILE = 'tripwire.sqlite'
SOURCES_FILE = 'sources.json'
HISTORY_LIMIT = 10  # Rolling history: keep only last 10 entries per source

def init_db():
    """
    Initializes the local SQLite database.
    We track 'version_id' specifically to handle the Legislation API's unique dates.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT,
            version_id TEXT,      -- For RSS, this is the hash. For API, this is the 'Start Date'.
            content_hash TEXT,    -- SHA256 of the actual file/content.
            timestamp DATETIME,   -- When we checked.
            priority TEXT,
            details TEXT
        )
    ''')
    conn.commit()
    return conn

def get_hash(content_bytes):
    """Returns SHA256 hash of bytes."""
    return hashlib.sha256(content_bytes).hexdigest()

def prune_history(cursor, source_name):
    """
    Maintains hygiene: Deletes records older than the 10 most recent 
    for the specific source.
    """
    cursor.execute('''
        DELETE FROM history
        WHERE source_name = ? AND id NOT IN (
            SELECT id FROM history
            WHERE source_name = ?
            ORDER BY timestamp DESC
            LIMIT ?
        )
    ''', (source_name, source_name, HISTORY_LIMIT))

def fetch_legislation_metadata(source):
    """
    Step 1 of Legislation Logic: DISCOVERY.
    We don't download the file yet. We ask the API:
    "What is the Start Date of the very latest document (Epub/Word) for this Title ID?"
    """
    base_url = source['base_url']
    title_id = source['title_id']
    doc_format = source['format'] # e.g., 'Epub' or 'Word'

    # OData Query: Filter by TitleID and Format, Order by Start Date (newest first), take top 1.
    params = {
        "$filter": f"titleid eq '{title_id}' and format eq '{doc_format}'",
        "$orderby": "start desc",
        "$top": "1"
    }
    
    headers = {'User-Agent': 'TripwireBot/1.0', 'Accept': 'application/json'}
    
    print(f"  -> Discovery: Querying metadata for {title_id} ({doc_format})...")
    resp = requests.get(base_url, params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    
    data = resp.json()
    
    # Validation: Ensure we actually got a result
    if not data.get('value'):
        print(f"  [!] Warning: No documents found for {title_id}")
        return None, None

    latest_meta = data['value'][0]
    
    # We use the 'start' field (date) as the unique Version ID.
    version_identifier = latest_meta.get('start') 
    
    return version_identifier, latest_meta

def download_legislation_content(base_url, meta):
    """
    Step 2 of Legislation Logic: DOWNLOAD.
    Constructs the specific OData Entity Key URL to retrieve the binary file.
    """
    # Extract the exact keys required by the API schema
    keys = {
        'titleid': meta['titleId'],
        'start': meta['start'],
        'retrospectivestart': meta['retrospectiveStart'],
        'rectificationversionnumber': meta['rectificationVersionNumber'],
        'type': meta['type'],
        'uniquetypenumber': meta['uniqueTypeNumber'],
        'volumenumber': meta['volumeNumber'],
        'format': meta['format']
    }
    
    # Construct the complex OData Key string.
    # Note: Strings get quotes (e.g. 'Epub'), Numbers do not. 
    path_segment = (
        f"titleid='{keys['titleid']}',"
        f"start={keys['start']},"
        f"retrospectivestart={keys['retrospectivestart']},"
        f"rectificationversionnumber={keys['rectificationversionnumber']},"
        f"type='{keys['type']}',"
        f"uniqueTypeNumber={keys['uniquetypenumber']},"
        f"volumeNumber={keys['volumenumber']},"
        f"format='{keys['format']}'"
    )
    
    # Append /$value to get the file content (binary), not the JSON metadata.
    download_url = f"{base_url}({path_segment})/$value"
    
    print(f"  -> Downloading content from: .../documents({keys['start']}...)")
    headers = {'User-Agent': 'TripwireBot/1.0'}
    
    file_resp = requests.get(download_url, headers=headers, timeout=60)
    file_resp.raise_for_status()
    
    return file_resp.content

def main():
    # 1. Load Sources
    if not os.path.exists(SOURCES_FILE):
        print(f"Error: {SOURCES_FILE} not found.")
        sys.exit(1)

    with open(SOURCES_FILE, 'r') as f:
        sources = json.load(f)

    conn = init_db()
    cursor = conn.cursor()
    
    updates_found = False

    print(f"--- Tripwire Run: {datetime.datetime.now()} ---")

    for source in sources:
        name = source.get('name')
        stype = source.get('type')
        priority = source.get('priority', 'Low')
        
        try:
            print(f"Checking {name}...")
            
            content_bytes = None
            version_id = None
            details_str = ""

            # === STRATEGY A: OData Legislation (Complex) ===
            if stype == "Legislation_OData":
                # Step 1: Check Metadata (Cheap call)
                version_id, meta = fetch_legislation_metadata(source)
                
                if not version_id:
                    continue # Skip if no data found
                
                # Check DB: Do we already have this 'start' date recorded?
                cursor.execute('SELECT id FROM history WHERE source_name = ? AND version_id = ?', (name, version_id))
                if cursor.fetchone():
                    print("  No change (Version ID match).")
                    continue 
                
                # Step 2: Download File (Expensive call) - Only happens if Version ID is new
                print(f"  [!] NEW VERSION DETECTED ({version_id}). Downloading...")
                content_bytes = download_legislation_content(source['base_url'], meta)
                details_str = f"Legislation Update ({source.get('format', 'File')}). Start Date: {version_id}"

            # === STRATEGY B: RSS / Standard API (Simple) ===
            elif stype == "RSS" or stype == "API":
                resp = requests.get(source['url'], timeout=15)
                resp.raise_for_status()
                content_bytes = resp.content
                
                # For RSS, we don't have a 'version number', so we use the hash as the ID
                version_id = get_hash(content_bytes) 
                
                cursor.execute('SELECT id FROM history WHERE source_name = ? AND version_id = ?', (name, version_id))
                if cursor.fetchone():
                     print("  No change.")
                     continue
                
                print(f"  [!] CHANGE DETECTED.")
                details_str = "RSS/API Update"

            # === COMMON: Save to DB ===
            new_hash = get_hash(content_bytes)
            timestamp = datetime.datetime.now().isoformat()
            
            # Store metadata. 
            cursor.execute('''
                INSERT INTO history (source_name, version_id, content_hash, timestamp, priority, details)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (name, version_id, new_hash, timestamp, priority, details_str))
            
            # Clean up old records
            prune_history(cursor, name)
            updates_found = True

        except Exception as e:
            print(f"  [x] Error checking {name}: {e}")

    conn.commit()
    conn.close()

    if updates_found:
        print("--- Updates completed ---")
    else:
        print("--- No updates found ---")

if __name__ == "__main__":
    main()
