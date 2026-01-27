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
HISTORY_LIMIT = 10 

def init_db():
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
    return hashlib.sha256(content_bytes).hexdigest()

def prune_history(cursor, source_name):
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
    base_url = source['base_url']
    title_id = source['title_id']
    doc_format = source['format']

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
    
    if not data.get('value'):
        print(f"  [!] Warning: No documents found for {title_id}")
        return None, None

    latest_meta = data['value'][0]
    version_identifier = latest_meta.get('start') 
    
    return version_identifier, latest_meta

def download_legislation_content(base_url, meta):
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
    
    # --- FIX APPLIED BELOW ---
    # Added single quotes around {keys['start']} and {keys['retrospectivestart']}
    path_segment = (
        f"titleid='{keys['titleid']}',"
        f"start='{keys['start']}'," 
        f"retrospectivestart='{keys['retrospectivestart']}',"
        f"rectificationversionnumber={keys['rectificationversionnumber']},"
        f"type='{keys['type']}',"
        f"uniqueTypeNumber={keys['uniquetypenumber']},"
        f"volumeNumber={keys['volumenumber']},"
        f"format='{keys['format']}'"
    )
    
    download_url = f"{base_url}({path_segment})/$value"
    
    print(f"  -> Downloading content from: .../documents(start='{keys['start']}'...)")
    headers = {'User-Agent': 'TripwireBot/1.0'}
    
    file_resp = requests.get(download_url, headers=headers, timeout=60)
    file_resp.raise_for_status()
    
    return file_resp.content

def main():
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

            if stype == "Legislation_OData":
                version_id, meta = fetch_legislation_metadata(source)
                
                if not version_id:
                    continue 
                
                cursor.execute('SELECT id FROM history WHERE source_name = ? AND version_id = ?', (name, version_id))
                if cursor.fetchone():
                    print("  No change (Version ID match).")
                    continue 
                
                print(f"  [!] NEW VERSION DETECTED ({version_id}). Downloading...")
                content_bytes = download_legislation_content(source['base_url'], meta)
                details_str = f"Legislation Update ({source.get('format', 'File')}). Start Date: {version_id}"

            elif stype == "RSS" or stype == "API":
                resp = requests.get(source['url'], timeout=15)
                resp.raise_for_status()
                content_bytes = resp.content
                version_id = get_hash(content_bytes) 
                
                cursor.execute('SELECT id FROM history WHERE source_name = ? AND version_id = ?', (name, version_id))
                if cursor.fetchone():
                     print("  No change.")
                     continue
                
                print(f"  [!] CHANGE DETECTED.")
                details_str = "RSS/API Update"

            new_hash = get_hash(content_bytes)
            timestamp = datetime.datetime.now().isoformat()
            
            cursor.execute('''
                INSERT INTO history (source_name, version_id, content_hash, timestamp, priority, details)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (name, version_id, new_hash, timestamp, priority, details_str))
            
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
