# CLAUDE.md — Tripwire

## What This Repo Is

Tripwire is a nine-stage autonomous IP monitoring pipeline. The authoritative design document is `260406_d_Tripwire_System_Plan.md` in the repo root. That plan defines all stages, schemas, configuration, and directory structure. Follow it precisely.

All nine phases are complete and the full pipeline runs end-to-end.

## Key Design Decisions

- **9 stages**, not 5. See Section 2 of the plan.
- **SQLite** (`ipfr.sqlite`) replaces the flat JSON embeddings file. Schema is in Section 9.
- **`tripwire_config.yaml`** is the single configuration file (Section 7). No env vars for config.
- **`source_registry.csv`** replaces `sources.json`.
- **Separate IPFR ingestion pipeline** (`ingestion/`) populates the SQLite corpus; runs daily at 01:00 UTC before the main pipeline.
- **Email notification** (Stage 9) replaces the review queue CSV.
- **Fail-closed**: uncertain → escalate, never silently drop signals.
- **Observation mode**: when `pipeline.observation_mode: true`, Stages 1–7 run fully and scores are logged, but Stage 8 (LLM) and Stage 9 (email) are **completely skipped** — no cost, no alerts. Use for 4–8 weeks of calibration before going live.
- **Deferred triggers**: if the LLM API fails after all retries, the trigger bundle is stored in the `deferred_triggers` table and retried at the **start of the next run**, before any new sources are processed.

## Repository Structure

```
src/
  pipeline.py                — Main orchestrator (Stages 1–9)
  config.py                  — Config loading and validation
  errors.py                  — Error hierarchy (RetryableError, PermanentError)
  retry.py                   — Exponential-backoff retry decorator
  scraper.py                 — Web scraping (trafilatura + Selenium fallback)
  validation.py              — Content validation (length, CAPTCHA, size ratio)
  stage1_metadata.py         — Stage 1: metadata probe
  stage2_change_detection.py — Stage 2: hash / diff / significance
  stage3_diff.py             — Stage 3: diff generation
  stage4_relevance.py        — Stage 4: YAKE-BM25 + bi-encoder RRF
  stage5_biencoder.py        — Stage 5: bi-encoder chunking
  stage6_crossencoder.py     — Stage 6: cross-encoder reranking + graph
  stage7_aggregation.py      — Stage 7: trigger aggregation
  stage8_llm.py              — Stage 8: LLM assessment
  stage9_notification.py     — Stage 9: email notification
  feedback_ingestion.py      — Gmail IMAP polling for feedback replies
  health.py                  — Health alerting
  observability.py           — Weekly score distribution report
ingestion/
  ingest.py                  — Ingestion orchestrator (Phases 0–6)
  db.py                      — SQLite schema and I/O helpers
  scrape_ipfr.py             — IPFR page scraping (trafilatura + DOCX support)
  enrich.py                  — Chunking, embeddings (BGE), NER (spaCy), YAKE
  dedup.py                   — Exact and near-duplicate marking; IDF filtering
  sitemap.py                 — IPFR sitemap discovery and page registry
  graph.py                   — Quasi-graph edge construction (Phase 6)
data/
  ipfr_corpus/
    ipfr.sqlite              — SQLite database (8 tables; see schema below)
    sitemap.csv              — IPFR page registry (populated by ingestion)
    snapshots/               — Per-page IPFR content snapshots
  influencer_sources/
    source_registry.csv      — All monitored sources
    snapshots/               — Per-source state and content
  logs/                      — Observation summaries, health alerts, feedback.jsonl
tests/                       — pytest test suite (18 files)
docs/                        — Operational runbooks
tripwire_config.yaml         — All parameters and thresholds
requirements.txt             — Main pipeline dependencies
requirements-ingestion.txt   — Ingestion pipeline dependencies
```

## Commands

All commands run from the repo root. Python 3.11+.

```bash
# Full pipeline run
python -m src.pipeline

# With debug logging
python -m src.pipeline --log-level DEBUG

# Force-check all sources regardless of schedule
python -m src.pipeline --check-frequency all

# IPFR corpus ingestion
python -m ingestion.ingest

# Force re-ingest all pages
python -m ingestion.ingest --force-all

# Weekly observability report
python -m src.observability --days 30

# Feedback ingestion (poll Gmail)
python -m src.feedback_ingestion

# Run all tests
pytest tests/ -v
```

## Environment Variables

These are set as GitHub Actions secrets and sourced locally as needed.
They are **not** in `tripwire_config.yaml`.

| Variable | Purpose | Used by |
|----------|---------|---------|
| `OPENAI_API_KEY` | LLM calls (Stage 8) | `tripwire.yml` |
| `SMTP_USER` | Gmail address for sending notifications | `tripwire.yml` |
| `SMTP_PASSWORD` | Gmail App Password for sending notifications | `tripwire.yml` |
| `FEEDBACK_EMAIL` | Reply-To address embedded in notification mailto links | `tripwire.yml` |
| `FEEDBACK_GMAIL_USER` | Gmail address for reading feedback replies | `feedback_ingestion.yml` |
| `FEEDBACK_GMAIL_APP_PASSWORD` | Gmail App Password for reading feedback replies | `feedback_ingestion.yml` |

## Testing

Tests use pytest with `tmp_path` and `monkeypatch`. No network calls are made in tests.

```bash
pytest tests/ -v
```

Test files follow the pattern `tests/test_<module>.py`.

## CI/CD

| Workflow | Trigger | Timeout | Purpose |
|----------|---------|---------|---------|
| `ipfr_ingestion.yml` | Daily cron 01:00 UTC + `workflow_dispatch` (`force_all` flag) | 60 min | Refresh IPFR corpus in SQLite |
| `tripwire.yml` | Daily cron 02:00 UTC + `workflow_dispatch` | 30 min | Full pipeline run (Stages 1–9) |
| `feedback_ingestion.yml` | Every 6 hours + `workflow_dispatch` | 10 min | Poll Gmail for feedback replies |

All three workflows commit updated state back to the repository after running (snapshots, SQLite, `sitemap.csv`, `feedback.jsonl`). Each uses a concurrency group to prevent parallel writes to SQLite.

The ingestion workflow runs at 01:00 UTC so the corpus is up to date before the main pipeline starts at 02:00 UTC.

## SQLite Schema (`data/ipfr_corpus/ipfr.sqlite`)

| Table | Purpose |
|-------|---------|
| `pages` | IPFR page metadata, content, doc embedding, status (`active`/`stub`/`duplicate`) |
| `page_chunks` | Pre-chunked IPFR content with BGE chunk embeddings |
| `entities` | Named entities (ORG, PERSON, GPE, LAW, etc.) per page |
| `keyphrases` | YAKE keyphrases with IDF weights per page |
| `graph_edges` | Quasi-graph edges (embedding similarity, entity overlap) |
| `sections` | Section headings and offsets per page |
| `pipeline_runs` | Per-source log entry for every run (used by health and observability) |
| `deferred_triggers` | Trigger bundles queued for LLM retry after API failures |

## Deferred Tasks (do not implement without live data)

These tasks from the plan require accumulated feedback data or live run history:

- **5.3** — Threshold calibration using feedback data (`src/stage4_relevance.py`, `src/stage6_crossencoder.py`)
- **5.4** — Grid search over relevance weights (`src/stage4_relevance.py`)
- **5.5** — Enable internal-link graph edges (`src/stage6_crossencoder.py`, `link_graph.enabled`)
- **5.6** — BM25 positional/proximity extensions (`src/stage4_relevance.py`)

TODO comments referencing these tasks are present in the relevant source files.

## Constraints

- No async unless the plan specifies it.
- No web frameworks or databases other than SQLite.
- Keep modules focused — one file per responsibility as the plan defines.
- Tests use pytest with tmp_path and monkeypatch. No network calls in tests.
- PyTorch must be installed from the CPU-only index for GitHub Actions compatibility:
  `pip install torch --index-url https://download.pytorch.org/whl/cpu`
