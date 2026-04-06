# Tripwire

Autonomous IP monitoring pipeline that detects substantive changes in authoritative Intellectual Property sources — Australian legislation, WIPO feeds, government agency webpages — and determines whether those changes require amendments to content published on the IP First Response (IPFR) website.

---

## Architecture

Tripwire is a nine-stage filter-funnel pipeline. Each stage acts as a gate; only changes that pass one stage proceed to the next. Expensive operations (semantic scoring, LLM calls) are reserved for the small fraction of changes that survive cheaper upstream checks.

```
Stage 1  Metadata Probe       — Has the source changed at all? (ETag, version ID, content length)
Stage 2  Change Detection     — Was the change meaningful? (SHA-256, word diff, significance tagger)
Stage 3  Diff Generation      — What exactly is different? (diff file / FRL explainer / RSS items)
Stage 4  Relevance Scoring    — Is this change relevant to IPFR? (YAKE-BM25 + bi-encoder RRF)
Stage 5  Bi-Encoder           — Which IPFR pages might be affected? (coarse semantic pass)
Stage 6  Cross-Encoder        — Which IPFR pages are affected? (precise re-ranking + graph propagation)
Stage 7  Trigger Aggregation  — Group all triggers per IPFR page for this run
Stage 8  LLM Assessment       — Should the page be amended? (one LLM call per IPFR page)
Stage 9  Notification         — Consolidated email to content owner with feedback links
```

Three source types are handled differently through the pipeline:

| Source Type | Stage 2 | Stage 3 |
|-------------|---------|---------|
| Webpage | Scrape → SHA-256 hash, word diff, significance tagger | `.diff` file (old vs new snapshot) |
| Federal Register of Legislation | Skipped | FRL change explainer document |
| RSS Feed | Skipped | New items since last check |

---

## Repository Structure

```
Tripwire/
├── src/
│   ├── pipeline.py              # Main orchestrator (Stages 1–9)
│   ├── config.py                # Config loading and validation
│   ├── errors.py                # Error hierarchy (RetryableError, PermanentError)
│   ├── retry.py                 # Exponential-backoff retry decorator
│   ├── scraper.py               # Web scraping with trafilatura
│   ├── validation.py            # Content validation (CAPTCHA, size, markers)
│   ├── stage1_metadata.py       # Stage 1: metadata probe
│   ├── stage2_change_detection.py # Stage 2: hash, diff, significance
│   ├── stage3_diff.py           # Stage 3: diff generation
│   ├── stage4_relevance.py      # Stage 4: YAKE-BM25 + bi-encoder RRF
│   ├── stage5_biencoder.py      # Stage 5: bi-encoder chunking
│   ├── stage6_crossencoder.py   # Stage 6: cross-encoder reranking + graph
│   ├── stage7_aggregation.py    # Stage 7: trigger aggregation
│   ├── stage8_llm.py            # Stage 8: LLM assessment
│   ├── stage9_notification.py   # Stage 9: email notification
│   ├── feedback_ingestion.py    # Gmail IMAP polling for feedback replies
│   ├── health.py                # Health alerting (error rate, consecutive failures)
│   └── observability.py         # Weekly score distribution report
├── ingestion/                   # IPFR corpus ingestion pipeline
├── data/
│   ├── ipfr_corpus/
│   │   └── ipfr.sqlite          # SQLite database (IPFR pages + pipeline run log)
│   ├── influencer_sources/
│   │   ├── source_registry.csv  # All monitored sources
│   │   └── snapshots/           # Per-source state and content snapshots
│   └── logs/                    # Observation summaries, feedback, health alerts
├── tests/                       # pytest test suite
├── docs/                        # Operational runbooks
│   ├── runbook-failure-response.md
│   ├── runbook-add-source.md
│   └── runbook-adjust-thresholds.md
├── tripwire_config.yaml         # Single configuration file (all thresholds and parameters)
└── requirements.txt
```

---

## Setup

**Requirements:** Python 3.11+, a Gmail account with an App Password (for SMTP and IMAP), and an OpenAI API key.

```bash
# 1. Clone the repository
git clone https://github.com/thomas-amann-ipaustralia/tripwire.git
cd tripwire

# 2. Create and activate a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies (CPU-only PyTorch for GitHub Actions compatibility)
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

# 4. Configure the pipeline
cp tripwire_config.yaml.example tripwire_config.yaml   # if not already present
# Edit tripwire_config.yaml — set emails, adjust thresholds as needed

# 5. Set required environment variables (or store as GitHub Actions secrets)
export OPENAI_API_KEY=sk-...
export SMTP_PASSWORD=...        # Gmail App Password for sending notifications
export SMTP_USER=sender@gmail.com
export IMAP_PASSWORD=...        # Gmail App Password for reading feedback replies
export IMAP_USER=feedback@gmail.com
```

### Hugging Face model downloads

On first run, the pipeline downloads two models (~1 GB total):

- `BAAI/bge-base-en-v1.5` (~400 MB) — bi-encoder (Stage 5)
- `gte-reranker-modernbert-base` (~600 MB) — cross-encoder (Stage 6)

Models are cached in `~/.cache/huggingface/`. GitHub Actions caches this directory
between runs via `actions/cache@v4` to avoid re-downloading on every run.

---

## Configuration

All tuneable parameters live in `tripwire_config.yaml`. Key sections:

```yaml
pipeline:
  observation_mode: true    # true = log everything, trigger nothing (use during calibration)
  llm_model: "gpt-4o"

relevance_scoring:
  top_n_candidates: 5       # Maximum candidates forwarded from Stage 4 to Stage 5

semantic_scoring:
  biencoder:
    high_threshold: 0.75    # Any chunk ≥ this passes Stage 5
  crossencoder:
    threshold: 0.60         # Final reranked score needed to reach Stage 7

notifications:
  content_owner_email: "content-owner@example.gov.au"
  health_alert_email: "admin@example.gov.au"
  health_alert_conditions:
    error_rate_threshold: 0.30
    consecutive_failures_threshold: 3
```

See [docs/runbook-adjust-thresholds.md](docs/runbook-adjust-thresholds.md) for a full
calibration guide.

---

## Running the Pipeline

All commands run from the repository root.

```bash
# Full pipeline run (Stages 1–9)
python -m src.pipeline

# With explicit config path and debug logging
python -m src.pipeline --config tripwire_config.yaml --log-level DEBUG

# Override the auto-generated run ID
python -m src.pipeline --run-id 2026-04-06-manual

# Generate the weekly observability report
python -m src.observability --days 30

# Run feedback ingestion (poll Gmail for feedback replies)
python -m src.feedback_ingestion
```

---

## Observation Mode

On initial deployment, run in **observation mode** (`pipeline.observation_mode: true`).
In this mode:

- Stages 1–7 run fully and all scores are logged to SQLite.
- Stage 8 (LLM) and Stage 9 (email) are skipped (no cost, no alerts).
- Per-run summaries are written to `data/logs/observation_summary_<run_id>.json`.

After 4–8 weeks, generate the observability report and calibrate thresholds before
disabling observation mode. See [docs/runbook-adjust-thresholds.md](docs/runbook-adjust-thresholds.md).

---

## Testing

```bash
# Run the full test suite
pytest tests/

# Run a specific test file
pytest tests/test_llm_assessment.py -v

# Run without network access (all tests are offline by design)
pytest tests/ --no-header -q
```

Tests use `pytest` with `tmp_path` and `monkeypatch`. No network calls are made.

---

## CI/CD

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `tripwire.yml` | Daily cron 02:00 UTC + manual dispatch | Full pipeline run |
| `feedback_ingestion.yml` | Every 6 hours | Poll Gmail for feedback replies |

The pipeline runs at 02:00 UTC (after the IPFR corpus ingestion workflow at 01:00 UTC).
A 30-minute `timeout-minutes` budget provides ~23 minutes of headroom on a typical run.

After each run, updated snapshots and the SQLite database are committed back to the
repository (`data/influencer_sources/snapshots/` and `data/ipfr_corpus/ipfr.sqlite`).

GitHub Actions secrets required:

| Secret | Purpose |
|--------|---------|
| `OPENAI_API_KEY` | LLM calls (Stage 8) |
| `SMTP_PASSWORD` | Gmail App Password for sending notification emails |
| `SMTP_USER` | Gmail address for sending |
| `IMAP_PASSWORD` | Gmail App Password for reading feedback replies |
| `IMAP_USER` | Gmail address for reading |

---

## Adding Sources

See [docs/runbook-add-source.md](docs/runbook-add-source.md).

---

## Responding to Failures

See [docs/runbook-failure-response.md](docs/runbook-failure-response.md).

---

## SQLite Schema

The pipeline writes to `data/ipfr_corpus/ipfr.sqlite`. Key tables:

| Table | Purpose |
|-------|---------|
| `pages` | IPFR page metadata and content (populated by ingestion pipeline) |
| `page_chunks` | Pre-chunked IPFR content with bi-encoder embeddings |
| `pipeline_runs` | Per-source log entry for every run (used by health and observability) |
| `deferred_triggers` | Trigger bundles queued for LLM retry after API failures |

---

## Design Principles

- **Fail-closed**: uncertain → escalate, never silently drop signals.
- **Single config file**: all thresholds and parameters in `tripwire_config.yaml`, version-controlled.
- **No async**: synchronous Python throughout (one source at a time, predictable resource use).
- **No web frameworks**: SQLite is the only database; standard library + well-audited packages only.
- **Modular**: one file per stage. Fork the repo, replace sources and corpus, adjust config — done.
