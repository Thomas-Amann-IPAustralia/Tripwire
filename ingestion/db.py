"""
ingestion/db.py

SQLite database setup and read/write operations for the IPFR corpus.

Uses WAL (Write-Ahead Logging) mode to allow concurrent reads during writes.
The ingestion pipeline writes; the Tripwire main pipeline reads.

Schema (Section 9 of the system plan):
  pages          — one row per IPFR page
  chunks         — one row per content chunk (section-aware)
  entities       — named entities per page
  keyphrases     — YAKE-extracted keyphrases per page
  graph_edges    — quasi-graph edges between pages
  sections       — heading hierarchy / section metadata per page
  pipeline_runs  — one row per source per pipeline run (Section 8)
  deferred_triggers — triggers stored when LLM API is unavailable
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Sequence


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Pages table: one row per IPFR page
CREATE TABLE IF NOT EXISTS pages (
    page_id         TEXT PRIMARY KEY,
    url             TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    version_hash    TEXT NOT NULL,
    last_modified   TEXT,
    last_checked    TEXT,
    last_ingested   TEXT,
    doc_embedding   BLOB,
    status          TEXT NOT NULL DEFAULT 'active',
    duplicate_of    TEXT
);

-- Chunks table: one row per chunk of each page
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        TEXT PRIMARY KEY,
    page_id         TEXT NOT NULL REFERENCES pages(page_id),
    chunk_text      TEXT NOT NULL,
    chunk_index     INTEGER NOT NULL,
    section_heading TEXT,
    chunk_embedding BLOB NOT NULL
);

-- Entities table: named entities extracted per page
CREATE TABLE IF NOT EXISTS entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id         TEXT NOT NULL REFERENCES pages(page_id),
    entity_text     TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    UNIQUE(page_id, entity_text, entity_type)
);

-- Keyphrases table: YAKE-extracted keyphrases per page
CREATE TABLE IF NOT EXISTS keyphrases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id         TEXT NOT NULL REFERENCES pages(page_id),
    keyphrase       TEXT NOT NULL,
    score           REAL NOT NULL
);

-- Graph edges: quasi-graph relationships between IPFR pages
CREATE TABLE IF NOT EXISTS graph_edges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_page_id  TEXT NOT NULL REFERENCES pages(page_id),
    target_page_id  TEXT NOT NULL REFERENCES pages(page_id),
    edge_type       TEXT NOT NULL,
    weight          REAL NOT NULL,
    UNIQUE(source_page_id, target_page_id, edge_type)
);

-- Section metadata: heading hierarchy per page
CREATE TABLE IF NOT EXISTS sections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id         TEXT NOT NULL REFERENCES pages(page_id),
    heading_text    TEXT NOT NULL,
    heading_level   INTEGER NOT NULL,
    char_start      INTEGER NOT NULL,
    char_end        INTEGER NOT NULL
);

-- Pipeline run log: one row per source per run
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL,
    source_id        TEXT NOT NULL,
    source_url       TEXT NOT NULL,
    source_type      TEXT NOT NULL,
    timestamp        TEXT NOT NULL,
    stage_reached    TEXT NOT NULL,
    outcome          TEXT NOT NULL,
    error_type       TEXT,
    error_message    TEXT,
    triggered_pages  TEXT,
    duration_seconds REAL,
    details          TEXT NOT NULL
);

-- Deferred triggers: stored when LLM API is unavailable
CREATE TABLE IF NOT EXISTS deferred_triggers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    ipfr_page_id TEXT NOT NULL,
    trigger_data TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    processed    INTEGER DEFAULT 0
);

-- LLM assessments: one row per assessed IPFR page per pipeline run
CREATE TABLE IF NOT EXISTS llm_assessments (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL,
    ipfr_page_id            TEXT NOT NULL,
    verdict                 TEXT NOT NULL,
    confidence              REAL NOT NULL,
    reasoning               TEXT NOT NULL,
    suggested_changes       TEXT NOT NULL,
    model                   TEXT NOT NULL,
    prompt_tokens           INTEGER,
    completion_tokens       INTEGER,
    total_tokens            INTEGER,
    processing_time_seconds REAL,
    retries                 INTEGER,
    schema_valid            INTEGER,
    generated_at            TEXT NOT NULL
);

-- Ingestion run audit log: one row per page per ingestion run
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            TEXT NOT NULL,
    page_id           TEXT,
    url               TEXT NOT NULL,
    timestamp         TEXT NOT NULL,
    outcome           TEXT NOT NULL,
    status            TEXT,
    error_type        TEXT,
    error_message     TEXT,
    chunk_count       INTEGER,
    section_count     INTEGER,
    entity_count      INTEGER,
    keyphrase_count   INTEGER,
    content_length    INTEGER,
    boilerplate_bytes_stripped INTEGER,
    duplicate_of      TEXT,
    warnings          TEXT,
    duration_seconds  REAL
);

-- Indices for common query patterns
CREATE INDEX IF NOT EXISTS idx_chunks_page_id ON chunks(page_id);
CREATE INDEX IF NOT EXISTS idx_entities_page_id ON entities(page_id);
CREATE INDEX IF NOT EXISTS idx_keyphrases_page_id ON keyphrases(page_id);
CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges(source_page_id);
CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_page_id);
CREATE INDEX IF NOT EXISTS idx_sections_page_id ON sections(page_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_run_id ON pipeline_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_source_id ON pipeline_runs(source_id);
CREATE INDEX IF NOT EXISTS idx_deferred_triggers_processed ON deferred_triggers(processed);
CREATE INDEX IF NOT EXISTS idx_llm_assessments_run_id ON llm_assessments(run_id);
CREATE INDEX IF NOT EXISTS idx_llm_assessments_page_id ON llm_assessments(ipfr_page_id);
CREATE INDEX IF NOT EXISTS idx_llm_assessments_verdict ON llm_assessments(verdict);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_run_id ON ingestion_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_page_id ON ingestion_runs(page_id);
"""


# Indices that depend on columns added via migration run after _apply_migrations.
_POST_MIGRATION_INDICES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_pages_status ON pages(status)",
    "CREATE INDEX IF NOT EXISTS idx_pages_duplicate_of ON pages(duplicate_of)",
]

# Columns added after the initial schema; applied via ALTER TABLE for existing DBs.
_PAGES_MIGRATIONS: list[tuple[str, str]] = [
    ("status", "ALTER TABLE pages ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"),
    ("duplicate_of", "ALTER TABLE pages ADD COLUMN duplicate_of TEXT"),
]


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


def init_db(db_path: str | Path, wal_mode: bool = True) -> sqlite3.Connection:
    """Open (or create) the database and apply the full schema.

    Parameters
    ----------
    db_path:
        Path to the SQLite file. Created if it does not exist.
    wal_mode:
        Enable WAL journal mode. Should always be True in production.

    Returns
    -------
    sqlite3.Connection
        An open connection with row_factory set to sqlite3.Row and
        foreign-key enforcement enabled.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row

    if wal_mode:
        conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")

    conn.executescript(_SCHEMA_SQL)
    _apply_migrations(conn)
    conn.commit()
    return conn


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial schema to existing databases."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(pages)").fetchall()}
    for col_name, ddl in _PAGES_MIGRATIONS:
        if col_name not in existing:
            conn.execute(ddl)
    for ddl in _POST_MIGRATION_INDICES:
        conn.execute(ddl)


@contextmanager
def get_connection(db_path: str | Path, wal_mode: bool = True) -> Generator[sqlite3.Connection, None, None]:
    """Context manager that yields an open, initialised connection and closes it on exit."""
    conn = init_db(db_path, wal_mode=wal_mode)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Page operations
# ---------------------------------------------------------------------------


def upsert_page(conn: sqlite3.Connection, page: dict[str, Any]) -> None:
    """Insert or replace a page record.

    Required keys: page_id, url, title, content, version_hash.
    Optional keys: last_modified, last_checked, last_ingested, doc_embedding,
                   status, duplicate_of.
    """
    conn.execute(
        """
        INSERT INTO pages
            (page_id, url, title, content, version_hash,
             last_modified, last_checked, last_ingested, doc_embedding,
             status, duplicate_of)
        VALUES
            (:page_id, :url, :title, :content, :version_hash,
             :last_modified, :last_checked, :last_ingested, :doc_embedding,
             :status, :duplicate_of)
        ON CONFLICT(page_id) DO UPDATE SET
            url           = excluded.url,
            title         = excluded.title,
            content       = excluded.content,
            version_hash  = excluded.version_hash,
            last_modified = excluded.last_modified,
            last_checked  = excluded.last_checked,
            last_ingested = excluded.last_ingested,
            doc_embedding = excluded.doc_embedding,
            status        = excluded.status,
            duplicate_of  = excluded.duplicate_of
        """,
        {
            "page_id": page["page_id"],
            "url": page["url"],
            "title": page["title"],
            "content": page["content"],
            "version_hash": page["version_hash"],
            "last_modified": page.get("last_modified"),
            "last_checked": page.get("last_checked"),
            "last_ingested": page.get("last_ingested"),
            "doc_embedding": page.get("doc_embedding"),
            "status": page.get("status", "active"),
            "duplicate_of": page.get("duplicate_of"),
        },
    )


def set_page_status(
    conn: sqlite3.Connection,
    page_id: str,
    *,
    status: str,
    duplicate_of: str | None = None,
) -> None:
    """Update the status/duplicate_of markers for an existing page."""
    conn.execute(
        "UPDATE pages SET status = ?, duplicate_of = ? WHERE page_id = ?",
        (status, duplicate_of, page_id),
    )


def get_active_pages(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return pages marked as active (excludes stubs and duplicates)."""
    return conn.execute(
        "SELECT * FROM pages WHERE status = 'active' ORDER BY page_id"
    ).fetchall()


def get_page(conn: sqlite3.Connection, page_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM pages WHERE page_id = ?", (page_id,)).fetchone()


def get_all_pages(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM pages ORDER BY page_id").fetchall()


def get_page_version_hash(conn: sqlite3.Connection, page_id: str) -> str | None:
    row = conn.execute(
        "SELECT version_hash FROM pages WHERE page_id = ?", (page_id,)
    ).fetchone()
    return row["version_hash"] if row else None


# ---------------------------------------------------------------------------
# Chunk operations
# ---------------------------------------------------------------------------


def replace_chunks(conn: sqlite3.Connection, page_id: str, chunks: list[dict[str, Any]]) -> None:
    """Delete all existing chunks for *page_id* and insert the new set."""
    conn.execute("DELETE FROM chunks WHERE page_id = ?", (page_id,))
    conn.executemany(
        """
        INSERT INTO chunks
            (chunk_id, page_id, chunk_text, chunk_index, section_heading, chunk_embedding)
        VALUES
            (:chunk_id, :page_id, :chunk_text, :chunk_index, :section_heading, :chunk_embedding)
        """,
        [
            {
                "chunk_id": c["chunk_id"],
                "page_id": page_id,
                "chunk_text": c["chunk_text"],
                "chunk_index": c["chunk_index"],
                "section_heading": c.get("section_heading"),
                "chunk_embedding": c["chunk_embedding"],
            }
            for c in chunks
        ],
    )


def get_chunks_for_page(conn: sqlite3.Connection, page_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM chunks WHERE page_id = ? ORDER BY chunk_index", (page_id,)
    ).fetchall()


def get_all_chunks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM chunks ORDER BY page_id, chunk_index").fetchall()


# ---------------------------------------------------------------------------
# Entity operations
# ---------------------------------------------------------------------------


def replace_entities(conn: sqlite3.Connection, page_id: str, entities: list[dict[str, str]]) -> None:
    """Delete all existing entities for *page_id* and insert the new set."""
    conn.execute("DELETE FROM entities WHERE page_id = ?", (page_id,))
    conn.executemany(
        """
        INSERT OR IGNORE INTO entities (page_id, entity_text, entity_type)
        VALUES (:page_id, :entity_text, :entity_type)
        """,
        [{"page_id": page_id, "entity_text": e["entity_text"],
          "entity_type": e["entity_type"]} for e in entities],
    )


def get_entities_for_page(conn: sqlite3.Connection, page_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM entities WHERE page_id = ? ORDER BY entity_type, entity_text",
        (page_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Keyphrase operations
# ---------------------------------------------------------------------------


def replace_keyphrases(conn: sqlite3.Connection, page_id: str, keyphrases: list[dict[str, Any]]) -> None:
    """Delete all existing keyphrases for *page_id* and insert the new set."""
    conn.execute("DELETE FROM keyphrases WHERE page_id = ?", (page_id,))
    conn.executemany(
        """
        INSERT INTO keyphrases (page_id, keyphrase, score)
        VALUES (:page_id, :keyphrase, :score)
        """,
        [{"page_id": page_id, "keyphrase": kp["keyphrase"],
          "score": kp["score"]} for kp in keyphrases],
    )


def get_keyphrases_for_page(conn: sqlite3.Connection, page_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM keyphrases WHERE page_id = ? ORDER BY score", (page_id,)
    ).fetchall()


# ---------------------------------------------------------------------------
# Graph edge operations
# ---------------------------------------------------------------------------


def upsert_graph_edge(conn: sqlite3.Connection, source: str, target: str,
                      edge_type: str, weight: float) -> None:
    conn.execute(
        """
        INSERT INTO graph_edges (source_page_id, target_page_id, edge_type, weight)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source_page_id, target_page_id, edge_type) DO UPDATE SET
            weight = MAX(weight, excluded.weight)
        """,
        (source, target, edge_type, weight),
    )


def get_edges_for_page(conn: sqlite3.Connection, page_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM graph_edges WHERE source_page_id = ?", (page_id,)
    ).fetchall()


def get_all_edges(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM graph_edges").fetchall()


def clear_graph_edges(conn: sqlite3.Connection, edge_type: str | None = None) -> None:
    """Clear graph edges, optionally filtered by edge_type."""
    if edge_type is not None:
        conn.execute("DELETE FROM graph_edges WHERE edge_type = ?", (edge_type,))
    else:
        conn.execute("DELETE FROM graph_edges")


# ---------------------------------------------------------------------------
# Section operations
# ---------------------------------------------------------------------------


def replace_sections(conn: sqlite3.Connection, page_id: str, sections: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM sections WHERE page_id = ?", (page_id,))
    conn.executemany(
        """
        INSERT INTO sections (page_id, heading_text, heading_level, char_start, char_end)
        VALUES (:page_id, :heading_text, :heading_level, :char_start, :char_end)
        """,
        [{"page_id": page_id, **s} for s in sections],
    )


def get_sections_for_page(conn: sqlite3.Connection, page_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sections WHERE page_id = ? ORDER BY char_start", (page_id,)
    ).fetchall()


# ---------------------------------------------------------------------------
# Pipeline run logging (Section 8)
# ---------------------------------------------------------------------------


def log_pipeline_run(conn: sqlite3.Connection, entry: dict[str, Any]) -> int:
    """Insert a pipeline run log row.

    Required keys: run_id, source_id, source_url, source_type, timestamp,
                   stage_reached, outcome, details.
    Optional keys: error_type, error_message, triggered_pages, duration_seconds.

    Returns the row id of the inserted record.
    """
    details = entry.get("details", {})
    if isinstance(details, dict):
        details_str = json.dumps(details)
    else:
        details_str = str(details)

    triggered = entry.get("triggered_pages")
    if isinstance(triggered, list):
        triggered_str = json.dumps(triggered)
    else:
        triggered_str = triggered

    cursor = conn.execute(
        """
        INSERT INTO pipeline_runs
            (run_id, source_id, source_url, source_type, timestamp,
             stage_reached, outcome, error_type, error_message,
             triggered_pages, duration_seconds, details)
        VALUES
            (:run_id, :source_id, :source_url, :source_type, :timestamp,
             :stage_reached, :outcome, :error_type, :error_message,
             :triggered_pages, :duration_seconds, :details)
        """,
        {
            "run_id": entry["run_id"],
            "source_id": entry["source_id"],
            "source_url": entry["source_url"],
            "source_type": entry["source_type"],
            "timestamp": entry["timestamp"],
            "stage_reached": entry["stage_reached"],
            "outcome": entry["outcome"],
            "error_type": entry.get("error_type"),
            "error_message": entry.get("error_message"),
            "triggered_pages": triggered_str,
            "duration_seconds": entry.get("duration_seconds"),
            "details": details_str,
        },
    )
    return cursor.lastrowid


def get_pipeline_runs(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM pipeline_runs WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()


# ---------------------------------------------------------------------------
# Deferred trigger operations
# ---------------------------------------------------------------------------


def store_deferred_trigger(conn: sqlite3.Connection, entry: dict[str, Any]) -> int:
    """Store a trigger that could not be processed due to LLM unavailability."""
    trigger_data = entry.get("trigger_data", {})
    if isinstance(trigger_data, dict):
        trigger_data_str = json.dumps(trigger_data)
    else:
        trigger_data_str = str(trigger_data)

    cursor = conn.execute(
        """
        INSERT INTO deferred_triggers
            (run_id, source_id, ipfr_page_id, trigger_data, created_at, processed)
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (entry["run_id"], entry["source_id"], entry["ipfr_page_id"],
         trigger_data_str, entry["created_at"]),
    )
    return cursor.lastrowid


def get_pending_deferred_triggers(conn: sqlite3.Connection,
                                   max_age_days: int = 7) -> list[sqlite3.Row]:
    """Return unprocessed deferred triggers not older than *max_age_days*."""
    return conn.execute(
        """
        SELECT * FROM deferred_triggers
        WHERE processed = 0
          AND julianday('now') - julianday(created_at) <= ?
        ORDER BY created_at
        """,
        (max_age_days,),
    ).fetchall()


def mark_deferred_trigger_processed(conn: sqlite3.Connection, trigger_id: int) -> None:
    conn.execute(
        "UPDATE deferred_triggers SET processed = 1 WHERE id = ?", (trigger_id,)
    )


# ---------------------------------------------------------------------------
# Ingestion audit log
# ---------------------------------------------------------------------------


def log_ingestion_run(conn: sqlite3.Connection, entry: dict[str, Any]) -> int:
    """Insert an ingestion-run audit row.

    Required keys: run_id, url, timestamp, outcome.
    Optional keys: page_id, status, error_type, error_message, chunk_count,
                   section_count, entity_count, keyphrase_count, content_length,
                   boilerplate_bytes_stripped, duplicate_of, warnings (list/str),
                   duration_seconds.
    """
    warnings = entry.get("warnings")
    if isinstance(warnings, (list, dict)):
        warnings_str = json.dumps(warnings)
    else:
        warnings_str = warnings

    cursor = conn.execute(
        """
        INSERT INTO ingestion_runs
            (run_id, page_id, url, timestamp, outcome, status,
             error_type, error_message, chunk_count, section_count,
             entity_count, keyphrase_count, content_length,
             boilerplate_bytes_stripped, duplicate_of, warnings, duration_seconds)
        VALUES
            (:run_id, :page_id, :url, :timestamp, :outcome, :status,
             :error_type, :error_message, :chunk_count, :section_count,
             :entity_count, :keyphrase_count, :content_length,
             :boilerplate_bytes_stripped, :duplicate_of, :warnings, :duration_seconds)
        """,
        {
            "run_id": entry["run_id"],
            "page_id": entry.get("page_id"),
            "url": entry["url"],
            "timestamp": entry["timestamp"],
            "outcome": entry["outcome"],
            "status": entry.get("status"),
            "error_type": entry.get("error_type"),
            "error_message": entry.get("error_message"),
            "chunk_count": entry.get("chunk_count"),
            "section_count": entry.get("section_count"),
            "entity_count": entry.get("entity_count"),
            "keyphrase_count": entry.get("keyphrase_count"),
            "content_length": entry.get("content_length"),
            "boilerplate_bytes_stripped": entry.get("boilerplate_bytes_stripped"),
            "duplicate_of": entry.get("duplicate_of"),
            "warnings": warnings_str,
            "duration_seconds": entry.get("duration_seconds"),
        },
    )
    return cursor.lastrowid


def get_ingestion_runs(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ingestion_runs WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
