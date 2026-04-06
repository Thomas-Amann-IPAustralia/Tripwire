## Summary

This PR introduces the complete IPFR corpus ingestion pipeline and supporting infrastructure for the Tripwire system. It includes database schema and CRUD operations, multi-stage ingestion workflow, configuration management, error handling, and retry logic.

## Key Changes

### Core Infrastructure
- **`src/config.py`**: Configuration loading, validation, and snapshotting from `tripwire_config.yaml`. Validates all pipeline parameters at startup with clear error messages.
- **`src/errors.py`**: Error hierarchy (`TripwireError`, `RetryableError`, `PermanentError`) with context tracking and convenience constructors for common failure modes.
- **`src/retry.py`**: Exponential backoff retry decorator with jitter. Retries only on `RetryableError`; `PermanentError` propagates immediately.

### Database Layer
- **`ingestion/db.py`**: SQLite schema (8 tables) and CRUD operations for pages, chunks, entities, keyphrases, graph edges, sections, pipeline runs, and deferred triggers. Supports WAL mode for concurrent read/write access.
- **`ingestion/sitemap.py`**: Step 1 of ingestion—loads/updates the IPFR sitemap CSV with page metadata (ID, URL, title, last-modified date).

### Ingestion Pipeline
- **`ingestion/scrape_ipfr.py`**: Step 2—fetches and normalizes IPFR pages using trafilatura. Validates content (minimum length, CAPTCHA detection, size-change bounds).
- **`ingestion/enrich.py`**: Step 4—enriches pages with precomputed assets: document/chunk embeddings (BAAI/bge-base-en-v1.5), section-aware chunking, named entity recognition (spaCy), and keyphrase extraction (YAKE).
- **`ingestion/graph.py`**: Computes quasi-graph edges between pages via embedding similarity, entity overlap, and (deferred) internal links.
- **`ingestion/ingest.py`**: Orchestrates the full ingestion cycle: loads sitemap, checks for changes, scrapes/enriches pages, upserts to database, rebuilds graph, and logs runs.

### Configuration & Workflows
- **`tripwire_config.yaml`**: Centralized configuration file with all tuneable parameters (retry settings, model names, scoring thresholds, graph parameters, storage options).
- **`.github/workflows/ipfr_ingestion.yml`**: GitHub Actions workflow that runs ingestion daily at 01:00 UTC with optional manual `force_all` trigger. Commits updated database and snapshots back to the repository.

### Testing
- **`tests/test_db.py`**: Comprehensive tests for database schema, connection management, and all CRUD operations (pages, chunks, entities, keyphrases, edges, sections, pipeline runs, deferred triggers).
- **`tests/test_config_validation.py`**: Tests for config loading, validation, and snapshot functionality.
- **`tests/test_retry.py`**: Tests for retry logic, backoff delays, and error classification.
- **`tests/test_errors.py`**: Tests for error hierarchy and convenience constructors.

### Supporting Files
- **`ingestion/__init__.py`**, **`src/__init__.py`**, **`tests/__init__.py`**: Package markers.
- **`requirements-ingestion.txt`**: Dependencies for ingestion (PyYAML, trafilatura, spaCy, YAKE, sentence-transformers, numpy).
- **`.gitattributes`**: Marks SQLite database as binary to prevent text diffs.
- **`data/influencer_sources/source_registry.csv`**: Sample source registry with FRL Trademarks Act as initial entry.

## Notable Implementation Details

- **Lazy model loading**: ML models (embeddings, NER, YAKE) are imported on first use so tests can run without heavy dependencies.
- **Section-aware chunking**: Content is split into overlapping chunks while respecting section boundaries extracted from trafilatura XML.
- **Concurrent read/write**: SQLite WAL mode allows the main Tripwire pipeline to read while ingestion writes.

https://claude.ai/code/session_01WiSS6XcoHwDcb5JcQw2a6e