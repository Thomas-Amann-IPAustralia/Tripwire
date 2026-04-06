# CLAUDE.md — Tripwire

## What This Repo Is

Tripwire is being rebuilt from a prototype into a nine-stage autonomous IP monitoring pipeline. The authoritative design document is `260406_d_Tripwire_System_Plan.md` in the repo root. That plan defines all stages, schemas, configuration, and directory structure. Follow it precisely.

## Key Design Decisions

- **9 stages**, not 5. See Section 2 of the plan.
- **SQLite** (`ipfr.sqlite`) replaces the flat JSON embeddings file. Schema is in Section 9.
- **`tripwire_config.yaml`** is the single configuration file (Section 7). No env vars for config.
- **`source_registry.csv`** replaces `sources.json`.
- **Separate IPFR ingestion pipeline** (Section 4) populates the SQLite corpus.
- **Email notification** (Stage 9) replaces the review queue CSV.
- **Fail-closed**: uncertain → escalate, never silently drop signals.

## Existing Code

The repo contains a working prototype. Some modules are worth adapting:
- **`stage0_detect.py` / `stage1_fetch.py`** — metadata probing and web scraping logic works. Adapt into the plan's Stage 1 and scraping components.
- **`config.py`** — current threshold values are tuned. Carry forward values the plan doesn't explicitly replace.
- **`stage3_score.py`** — shows how `Semantic_Embeddings_Output.json` is loaded. The embedding model (`text-embedding-3-small`) and cosine similarity approach carry forward into the SQLite-backed system.

Anything in the prototype that conflicts with the plan should be discarded.

## Repository Structure

Follow Section 5 of the plan exactly. All source code goes in `src/`, ingestion code in `ingestion/`, data in `data/`, tests in `tests/`.

## Commands

All commands run from the repo root. Python 3.11+.

## Constraints

- No async unless the plan specifies it.
- No web frameworks or databases other than SQLite.
- Keep modules focused — one file per responsibility as the plan defines.
- Tests use pytest with tmp_path and monkeypatch. No network calls in tests.
