# CLAUDE.md — Tripwire

## What This Repo Is

Tripwire is a nine-stage autonomous IP monitoring pipeline. The authoritative design document is `260406_d_Tripwire_System_Plan.md` in the repo root. That plan defines all stages, schemas, configuration, and directory structure. Follow it precisely.

All nine phases are now complete and the full pipeline runs end-to-end.

## Key Design Decisions

- **9 stages**, not 5. See Section 2 of the plan.
- **SQLite** (`ipfr.sqlite`) replaces the flat JSON embeddings file. Schema is in Section 9.
- **`tripwire_config.yaml`** is the single configuration file (Section 7). No env vars for config.
- **`source_registry.csv`** replaces `sources.json`.
- **Separate IPFR ingestion pipeline** (Section 4) populates the SQLite corpus.
- **Email notification** (Stage 9) replaces the review queue CSV.
- **Fail-closed**: uncertain → escalate, never silently drop signals.

## Repository Structure

```
src/
  pipeline.py              — Main orchestrator (Stages 1–9)
  config.py                — Config loading and validation
  errors.py                — Error hierarchy
  retry.py                 — Exponential-backoff retry decorator
  scraper.py               — Web scraping (trafilatura)
  validation.py            — Content validation
  stage1_metadata.py       — Stage 1: metadata probe
  stage2_change_detection.py — Stage 2: hash / diff / significance
  stage3_diff.py           — Stage 3: diff generation
  stage4_relevance.py      — Stage 4: YAKE-BM25 + bi-encoder RRF
  stage5_biencoder.py      — Stage 5: bi-encoder chunking
  stage6_crossencoder.py   — Stage 6: cross-encoder reranking + graph
  stage7_aggregation.py    — Stage 7: trigger aggregation
  stage8_llm.py            — Stage 8: LLM assessment
  stage9_notification.py   — Stage 9: email notification
  feedback_ingestion.py    — Gmail IMAP polling for feedback replies
  health.py                — Health alerting (Phase 5, task 5.1)
  observability.py         — Weekly score distribution report (Phase 5, task 5.2)
ingestion/                 — IPFR corpus ingestion pipeline
data/
  ipfr_corpus/ipfr.sqlite  — SQLite database
  influencer_sources/
    source_registry.csv    — All monitored sources
    snapshots/             — Per-source state and content
  logs/                    — Observation summaries, health alerts, feedback
tests/                     — pytest test suite
docs/                      — Operational runbooks
tripwire_config.yaml       — All parameters and thresholds
requirements.txt
```

## Commands

All commands run from the repo root. Python 3.11+.

```bash
# Full pipeline run
python -m src.pipeline

# With debug logging
python -m src.pipeline --log-level DEBUG

# Weekly observability report
python -m src.observability --days 30

# Feedback ingestion (poll Gmail)
python -m src.feedback_ingestion

# Run all tests
pytest tests/
```

## Environment Variables

These are set as GitHub Actions secrets and sourced locally as needed.
They are **not** in `tripwire_config.yaml`.

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | LLM calls (Stage 8) |
| `SMTP_PASSWORD` | Gmail App Password for sending notification emails |
| `SMTP_USER` | Gmail address for sending |
| `IMAP_PASSWORD` | Gmail App Password for reading feedback replies |
| `IMAP_USER` | Gmail address for reading |

## Testing

Tests use pytest with `tmp_path` and `monkeypatch`. No network calls are made in tests.

```bash
pytest tests/ -v
```

Test files follow the pattern `tests/test_<module>.py`.

## CI/CD

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `tripwire.yml` | Daily cron 02:00 UTC + `workflow_dispatch` | Full pipeline run |
| `feedback_ingestion.yml` | Every 6 hours + `workflow_dispatch` | Gmail feedback polling |

Both workflows commit updated state (snapshots, SQLite) back to the repository after running.
The pipeline workflow has `timeout-minutes: 30` per Section 6.6.

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
