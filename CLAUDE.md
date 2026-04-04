# CLAUDE.md — Tripwire

This file gives Claude Code the orientation it needs to work safely and
productively in this repository. Read it before touching any code.

---

## What This Repo Is

Tripwire is a recall-first, fail-closed autonomous IP monitoring pipeline.
It watches authoritative sources (Australian legislation, WIPO feeds, web pages)
for changes that may affect IP First Response (IPFR) content, then uses LLM
verification to narrow confirmed impacts down to the specific chunks that need
updating.

The pipeline runs as a single Python package invocation. There is no web server,
no database, and no persistent daemon.

---

## Commands You Will Use

**Run the full pipeline** (from repo root):
```bash
python -m tripwire
```

**Test Stage 3 in isolation** against a specific diff file:
```bash
python -m tripwire --test-stage3 path/to/file.diff
```
Loads the embeddings corpus, scores one diff, prints JSON, and exits — no LLM
calls, no side effects on output directories.

**Run the unit test suite** (no API key or network required):
```bash
pytest test_stage3.py test_stage5.py
```

**Regenerate mock embeddings** for tests (requires `OPENAI_API_KEY` — it calls the real embeddings API):
```bash
python generate_mock_data.py
```

**Run end-to-end evaluation** (requires `OPENAI_API_KEY`):
```bash
python evaluate_tripwire_llm.py
```

All commands must be run from the **repo root** — all path constants in
`config.py` are relative.

---

## Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OPENAI_API_KEY` | Stages 4 & 5 only | none | LLM verification and update suggestions. Stages 0–3 run without it. If absent, Stage 4 fails closed to `overall_decision="uncertain"`. |
| `TRIPWIRE_LLM_MODEL` | No | `gpt-4.1-mini` | LLM model for Stage 4 verification. |
| `TRIPWIRE_STAGE5_LLM_MODEL` | No | same as above | LLM model for Stage 5 suggestions. |
| `IPFR_CONTENT_ARCHIVE_DIR` | No | `IPFR_content_archive` | Directory containing IPFR markdown pages for Stage 4. |
| `LLM_VERIFY_DIR` | No | `llm_verification_results` | Output directory for Stage 4 per-candidate logs. |
| `UPDATE_SUGGESTIONS_DIR` | No | `llm_update_suggestions` | Output directory for Stage 5 draft suggestions. |
| `TOP_N_VERIFICATION_CANDIDATES` | No | `3` | Prototype cap on candidates passed to the LLM per packet. |

---

## Critical Data File

`Semantic_Embeddings_Output.json` (~25 MB) must exist in the repo root.
Stage 3 will hard-abort if it is absent. This file is not committed to git.
Obtain it from the project data store and place it at:

```
./Semantic_Embeddings_Output.json
```

The embedding model used to generate it is `text-embedding-3-small` (OpenAI).
Do not regenerate with a different model without also regenerating all IPFR
chunk embeddings — they must come from the same model.

---

## Repository Layout

```
Tripwire/
├── tripwire/                        # Main package (17 modules)
│   ├── __main__.py                  # CLI entry point; routes --test-stage3 or main()
│   ├── pipeline.py                  # Top-level orchestrator; calls Stages 0–5
│   ├── config.py                    # All constants and env-var overrides
│   ├── stage0_detect.py             # ETag / registerId metadata probing
│   ├── stage1_fetch.py              # Download, clean, normalise content
│   ├── stage2_diff.py               # Unified diff generation vs. archive
│   ├── stage3_score.py              # Semantic embedding, scoring, routing
│   ├── stage4_verify.py             # Two-pass LLM verification (largest module)
│   ├── stage5_suggest.py            # LLM draft update suggestions
│   ├── handover.py                  # Structured JSON packet generation
│   ├── ipfr_content.py              # IPFR markdown parsing and chunk windowing
│   ├── llm_client.py                # OpenAI client wrapper
│   ├── audit.py                     # audit_log.csv read/write
│   ├── review_queue.py              # update_review_queue.csv generation
│   ├── manifest.py                  # Run manifest and GitHub Actions summary
│   ├── utils.py                     # Shared helpers (chunk IDs, overlap metrics)
│   └── __init__.py                  # Re-exports all public names (compat shim)
├── sources.json                     # Monitored source definitions
├── Semantic_Embeddings_Output.json  # ~25 MB embedding corpus (not committed)
├── IPFR_content_archive/            # IPFR markdown pages used by Stage 4
├── content_archive/                 # Normalised source snapshots (pipeline output)
├── diff_archive/                    # Unified diffs by timestamp (pipeline output)
├── handover_packets/                # Stage 3 → Stage 4 evidence packets
├── llm_verification_results/        # Stage 4 per-candidate logs
├── llm_update_suggestions/          # Stage 5 draft replacement text
├── audit_log.csv                    # Master append-only run ledger
├── update_review_queue.csv          # Flat human review queue (Stage 5 output)
├── test_stage3.py                   # Stage 3 unit tests
├── test_stage5.py                   # Stage 5 unit tests
├── test_fixtures/diffs/             # Sample diffs for tests
├── evaluate_tripwire_llm.py         # E2E evaluation utility
├── generate_mock_data.py            # Mock embedding generator for tests
└── requirements.txt                 # Python dependencies
```

---

## Architecture in One Paragraph

Stage 0 probes source metadata (ETag, registerId) to detect changes without
downloading. Stage 1 downloads and normalises changed sources into stable
text/markdown. Stage 2 diffs normalised content against the archive. Stage 3
embeds diff hunks, compares them against the precomputed IPFR chunk embedding
corpus, aggregates page-level scores with coverage/density/power-word bonuses,
and routes qualifying candidates into a handover packet. Stage 4 sends each
packet to the LLM in two passes: Pass 1 confirms page-level impact using a
narrow 3-chunk evidence window; Pass 2 scopes which chunks need updating.
Stage 5 generates draft replacement text per confirmed chunk. The review queue
CSV consolidates all Stage 5 output for human sign-off before changes are
applied to IPFR content.

---

## Scoring Thresholds

Change these only after running `evaluate_tripwire_llm.py` to measure the
impact on retrieval and verifier precision/recall.

| Constant | Value | Purpose |
|---|---|---|
| `CANDIDATE_MIN_SCORE` | `0.35` | Minimum score for a page to be included as a candidate |
| `HUNK_CHUNK_MIN_SIMILARITY` | `0.35` | Same value — minimum per-chunk similarity to count as a match |
| `MEDIUM_PRIMARY_HANDOVER_THRESHOLD` | `0.45` | Top page score required to trigger handover for Medium-priority sources |
| `LOW_PRIMARY_HANDOVER_THRESHOLD` | `0.50` | Top page score required for Low-priority sources |
| `PAGE_HUNK_COVERAGE_BONUS` | `+0.04` | Per unique hunk matched; capped at `MAX_PAGE_COVERAGE_BONUS` (0.12) |
| `PAGE_CHUNK_DENSITY_BONUS` | `+0.01` | Per additional chunk hit; capped at `MAX_PAGE_DENSITY_BONUS` (0.06) |

High-priority sources always hand over if any candidate scores ≥ 0.35.

---

## Sources Configuration

`sources.json` is a JSON array. Each entry has:

```json
{
  "name": "Human-readable label",
  "type": "Legislation_OData | WebPage | RSS",
  "url": "...",          // WebPage and RSS
  "base_url": "...",     // Legislation_OData
  "title_id": "...",     // Legislation_OData: legislation.gov.au title ID
  "format": "Word",      // Legislation_OData only
  "priority": "High | Medium | Low",
  "output_filename": "filename.ext"
}
```

Add sources here; `pipeline.py` loops over the array automatically. Priority
controls handover thresholds (High = maximum recall, Medium = balanced,
Low = efficiency-first).

---

## Prototype Markers and Production TODOs

The codebase contains explicit `# NOTE: Prototype` comments — do not remove
them without implementing the production alternative:

- **UDID → file resolution** (`ipfr_content.py`): Currently resolves UDIDs to
  markdown files by filename pattern matching. In production, use an explicit
  UDID→file map generated by the IPFR export pipeline.
- **`TOP_N_VERIFICATION_CANDIDATES`** (`config.py`): Caps LLM candidates at 3
  per packet to limit prompt size. In production, load only the specific section
  windows needed per candidate instead of whole pages.
- **Headless Chrome** (`stage1_fetch.py`): Uses `selenium-stealth` +
  `webdriver-manager`. Requires Chrome on the execution host. In production,
  consider a dedicated scraping service.
- **Flat CSV audit log** (`audit.py`): 27-column CSV schema. Intended future
  migration to SQLite for richer querying (schema was explored in the now-retired
  `Future_SQLite/` directory).

---

## Fail-Closed Behaviour

Tripwire escalates on uncertainty rather than silently dropping signals:

- **Missing `OPENAI_API_KEY`** → Stage 4 returns `overall_decision = "uncertain"`
  (not `"no_impact"`); flagged for human review.
- **Pass 2 failure or invalid JSON** → fallback promotes the Pass 1 probe chunk
  into `additional_chunks_to_review` so no signal is lost.
- **Missing `Semantic_Embeddings_Output.json`** → Stage 3 hard-aborts.

Do not catch-and-suppress errors in ways that would silently convert uncertain
signals into clean negatives.

---

## Package Structure Note

`tripwire/__init__.py` re-exports everything publicly so tests and tools can do
`import tripwire` and then call `tripwire.calculate_similarity(...)` or patch
`tripwire.config.SOME_CONSTANT`. The bottom of `__init__.py` contains a
backwards-compatibility shim (`_call_llm_json_with_model`).

Do not restructure into sub-packages without updating `__init__.py` and all
`monkeypatch.setattr` calls in the tests.

---

## Testing Notes

- Tests use `pytest` with `tmp_path` and `monkeypatch`. No network calls; no
  real API key needed.
- `test_stage3.py` uses mock embedding data (generated by `generate_mock_data.py`)
  and diff fixtures from `test_fixtures/diffs/`.
- `test_stage5.py` patches `tripwire.stage5_suggest._call_llm_json` to mock LLM
  responses.
- To override config constants in tests, use:
  ```python
  monkeypatch.setattr(tripwire.config, "CANDIDATE_MIN_SCORE", 0.5)
  ```
  This avoids side effects on real output directories.

---

## CI/CD

| Workflow | Trigger | Purpose |
|---|---|---|
| `tripwire.yml` | Cron (6am AEST weekdays) + manual dispatch | Full pipeline; commits diffs and uploads artifacts |
| `test_stage3.yml` | Pull request | Stage 3 unit tests; calls `generate_mock_data.py` first |
| `test_stage5.yml` | Pull request | Stage 5 unit tests |
| `llm-eval.yml` | Manual dispatch | End-to-end evaluation with precision/recall metrics |

Stages 0–3 are cost-free (no API calls). Stages 4 and 5 incur OpenAI costs
proportional to the number of candidates that clear the Stage 3 thresholds.

---

## Common Mistakes to Avoid

1. **Running from the wrong directory.** `python -m tripwire` must be run from
   the repo root — all paths in `config.py` are relative (e.g.
   `"content_archive"`, `"Semantic_Embeddings_Output.json"`).

2. **Assuming Stage 4 results with no API key.** The audit log will show
   `AI Decision = uncertain` for all candidates. This is correct behaviour,
   not a bug.

3. **Regenerating embeddings with a different model.** `Semantic_Embeddings_Output.json`
   was created with `text-embedding-3-small`. All IPFR chunk embeddings in IPFR
   content files must come from the same model. Mixing models silently corrupts
   similarity scores.

4. **Adding new config constants to `pipeline.py`.** All constants belong in
   `config.py` — this is the monkeypatching contract that tests depend on.

5. **Restructuring `__init__.py` re-exports without checking tests.** The shim
   at the bottom of `__init__.py` and the `monkeypatch.setattr(tripwire.config, ...)`
   calls in both test files depend on the current flat export structure.

6. **Stage 4 uses the OpenAI Responses API** (`client.responses.create()`), not
   `client.chat.completions.create()`. The model must support this endpoint.
   `gpt-4.1-mini` does; older models (e.g. `gpt-3.5-turbo`) do not.
