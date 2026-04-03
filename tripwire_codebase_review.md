# Tripwire Codebase Review

**Prepared for:** Tom  
**Date:** 23 March 2026  
**Scope:** Full repo audit — every file, its role, and a refactoring blueprint

---

## 1. Executive Summary

Tripwire is a **5-stage pipeline** that monitors authoritative IP sources (legislation, WIPO feeds, web pages) for changes that may affect IP First Response (IPFR) content. When a meaningful change is detected, the system generates structured evidence packets, runs LLM verification to confirm impact, drafts content update suggestions, and queues them for human review.

The entire pipeline logic lives in a single **~2,400-line monolith** (`tripwire.py`). Every other file in the repo is either configuration, test fixtures, CI workflows, or output artifacts. The refactoring opportunity is significant and well-scoped.

---

## 2. File-by-File Inventory

### 2.1 Core Application Code

#### `tripwire.py` — The Monolith (~2,400 lines)

This is the entire application. It contains **all five pipeline stages**, plus shared utilities, audit logging, LLM integration, and the CLI entrypoint. Here's what's packed in:

| Responsibility | Approx. Lines | Key Functions |
|---|---|---|
| **Configuration & constants** | ~60 | Global vars: paths, thresholds, model names, scoring bonuses |
| **OpenAI client management** | ~25 | `get_openai_client()` — lazy singleton with env-var auth |
| **Audit logging** | ~180 | `append_audit_row()`, `update_audit_row_by_key()`, `ensure_audit_log_headers()`, `log_stage3_to_audit()`, `log_to_audit()` — CSV-based ledger with a 27-column schema |
| **Stage 0 — Version detection** | ~30 | `fetch_stage0_metadata()` — lightweight HEAD/OData probes |
| **Stage 1 — Fetch & normalise** | ~120 | `initialize_driver()`, `clean_html_content()`, `fetch_webpage_content()`, `sanitize_rss()`, `fetch_legislation_metadata()`, `download_legislation_content()`, `_extract_docx_text()` |
| **Stage 2 — Diff generation** | ~30 | `get_diff()`, `save_diff_record()`, `save_to_archive()` |
| **Stage 3 — Semantic scoring** | ~350 | `parse_diff_hunks()`, `extract_change_content()`, `detect_power_words()`, `calculate_similarity()`, `calculate_final_score()`, `should_generate_handover()`, `_load_semantic_embeddings()`, `_embed_texts()`, `_is_administrative_noise()` |
| **Handover packet generation** | ~150 | `generate_handover_packets()`, `_derive_packet_priority()`, `_clean_diff_text_line()` |
| **Stage 4 — LLM verification** | ~600 | Two-pass architecture: `_build_llm_pass1_prompt()`, `_build_llm_pass2_prompt()`, `verify_handover_packet_with_llm()`, `run_llm_verification_for_packets()`, plus helpers: `parse_markdown_chunks()`, `extract_chunk_window()`, `build_chunk_index()`, `_select_pass1_target_chunk_id()`, `_score_chunk_for_pass1()`, `_build_chunk_verification_targets()`, `_format_diff_block_for_candidate()`, `_call_llm_json()` |
| **Stage 4 — Result extraction** | ~180 | `_extract_confirmed_updates()`, `_extract_confirmed_update_chunk_ids()`, `_extract_additional_chunks_to_review()`, `_extract_pass1_confirmed_chunk_ids()`, `summarise_verification_file()`, `summarise_verification_files()` |
| **IPFR content resolution** | ~80 | `resolve_ipfr_content_files()`, `_resolve_ipfr_markdown_path()`, `_resolve_ipfr_jsonld_path()`, `_read_text_file()` |
| **Stage 5 — Update suggestions** | ~280 | `_build_stage5_prompt()`, `_call_llm_json_with_model()`, `run_llm_update_suggestions_for_verification_files()`, `_stage5_status_from_counts()` |
| **Review queue** | ~80 | `build_update_review_queue_rows_from_payload()`, `write_update_review_queue_csv_from_suggestion_files()`, `format_relevant_diff_text()` |
| **Utilities** | ~50 | `canonical_chunk_id()`, `_list_to_semicolon()`, `_now_iso()`, `_decision_to_human()`, `_confidence_to_human()`, `_compute_overlap_metrics()`, `_normalise_chunk_id_list()`, `_priority_to_source_weight()` |
| **Manifest & GitHub summary** | ~60 | `write_current_run_manifest()`, `write_github_summary()` |
| **Main loop & CLI** | ~120 | `main()` — orchestrates everything; CLI `--test-stage3` mode |

**Key architectural patterns in the monolith:**

- **Global mutable state**: `_semantic_cache`, `_client`, `TOP_N_VERIFICATION_CANDIDATES` are module-level globals mutated at runtime.
- **Two LLM call functions**: `_call_llm_json()` (uses module-level `LLM_MODEL`) and `_call_llm_json_with_model()` (accepts model param). These are near-duplicates.
- **Mixed concerns**: Audit logging is tangled into every stage. The `main()` loop handles fetching, diffing, scoring, handover, verification, and suggestions in one function.
- **Prototype markers**: Many `# NOTE: Prototype` comments indicate temporary patterns (e.g., glob-based file resolution, top-N candidate limits).

---

#### `evaluate_tripwire_llm.py` (~340 lines) — End-to-End Evaluation Suite

Runs a controlled evaluation of the full pipeline using hardcoded test data. This is **not** a pytest suite — it's a standalone script designed for the `llm-eval.yml` GitHub Action.

**What it does:**

1. **Builds a synthetic multi-hunk diff** (`_build_multihunk_diff_text()`) from 3 hardcoded hunks that target pages `101-1` and `101-2`.
2. **Runs Stage 3** (`calculate_similarity`) against the synthetic diff.
3. **Generates handover packets** and runs **Stage 4 verification** + **Stage 5 suggestions**.
4. **Computes retrieval and verification metrics**: precision/recall at page level and chunk level.
5. **Runs a noise filtering evaluation** (`run_noise_filter_eval()`) that tests whether HTML layout noise (nav changes, date stamps) is correctly filtered vs. substantive content changes.
6. **Reports an end state** classification: `RETRIEVAL MISS`, `LLM VERIFICATION MISS`, `PASS 2 FALLBACK`, `UPDATE SUGGESTION GENERATED`, etc.

**Key constants:**
- `EXPECTED_IMPACTED_UDIDS = {"101-1", "101-2"}` — ground truth for the synthetic test
- `HTML_NOISE_OLD`, `HTML_NOISE_ONLY_NEW`, `HTML_MIXED_NEW` — HTML fixtures for noise filtering tests

**Dependencies on tripwire.py:** Heavy — calls `calculate_similarity`, `generate_handover_packets`, `run_llm_verification_for_packets`, `run_llm_update_suggestions_for_verification_files`, `summarise_verification_files`, `build_update_review_queue_rows_from_payload`, `write_update_review_queue_csv_from_suggestion_files`, `clean_html_content`, `parse_diff_hunks`, `_is_administrative_noise`, `canonical_chunk_id`.

---

#### `generate_mock_data.py` (~50 lines) — Mock Embeddings Generator

Creates synthetic semantic embeddings for 4 mock IPFR content chunks using the OpenAI `text-embedding-3-small` model. Saves them as a pickle file to `test_fixtures/mock_semantic_data.pkl`.

**Used by:** `test_stage3.py` (indirectly — the test suite patches `_embed_texts` and uses its own mock embeddings inline rather than loading the pickle).

**Note:** This script requires a live OpenAI API key at generation time. The pickle output is consumed downstream.

---

### 2.2 Test Suites

#### `test_stage3.py` (~130 lines) — Stage 3 Unit Tests (pytest)

Tests the semantic scoring pipeline with mocked embeddings:

| Test | What It Validates |
|---|---|
| `test_parse_diff_hunks_logic` | Diff parser returns hunks with `added_lines` key |
| `test_power_words_detection` | Power word detector finds `strong_count > 0` and `score > 0.10` for legal terms |
| `test_calculate_similarity_structure` | Full pipeline returns `status: success` with expected keys |
| `test_generate_handover_packets_data_integrity` | Packet JSON contains correct verification targets |
| `test_noise_suppression_logic` | Administrative noise (page numbers) produces low scores |
| `test_handover_batching_limit` | 5 candidates with `MAX_CANDIDATES_PER_PACKET=2` produces 3 packets |

**Mocking strategy:** Patches `tripwire._embed_texts` and `tripwire.SEMANTIC_EMBEDDINGS_FILE` to avoid OpenAI calls.

---

#### `test_stage5.py` (~230 lines) — Stage 5 Unit Tests (pytest)

Tests the update suggestion generation pipeline:

| Test | What It Validates |
|---|---|
| `test_stage5_one_confirmed_chunk_on_one_page` | Single confirmed chunk → `Suggestion Generated` status, correct audit row |
| `test_stage5_multiple_confirmed_chunks_on_one_page` | Two confirmed chunks on same page → both drafted |
| `test_stage5_confirmed_chunks_across_multiple_pages_from_one_diff` | One diff affecting two pages → single suggestion file with both pages |
| `test_stage5_additional_review_chunks_present_but_not_drafted` | Additional review chunks appear in output but are NOT sent to LLM for drafting |
| `test_stage5_unresolved_chunk_id_produces_partial_suggestion_generated` | Missing chunk in markdown → `Partial Suggestion Generated` with `unresolved_chunk` status |

**Mocking strategy:** Uses `monkeypatch` to replace `resolve_ipfr_content_files`, `_read_text_file`, and `_call_llm_json_with_model`. Creates temporary markdown files and handover packets via helper functions.

**Interesting pattern:** Imports `tripwire.py` via `importlib.util.spec_from_file_location` rather than a standard import — likely to avoid module-level side effects.

---

### 2.3 Configuration & Data Files

#### `sources.json` — Monitored Source Registry

Defines 6 monitored sources:

| Source | Type | Priority |
|---|---|---|
| WIPO Arbitration and Mediation Center | RSS | Medium |
| WIPO Press Room | RSS | Low |
| Trade Marks Act 1995 | Legislation_OData | High |
| Patents Regulations 1991 | Legislation_OData | High |
| Thomas Amann Website | WebPage | Medium |
| IP Australia - What are trade marks? | WebPage | Medium |

Each entry has: `name`, `type`, `url`/`base_url`, `priority`, `output_filename`, and optionally `title_id` (for legislation) and `format`.

---

#### `requirements.txt` — Python Dependencies

```
requests, selenium, selenium-stealth, beautifulsoup4, markdownify,
webdriver-manager, lxml, python-docx, scikit-learn, numpy, pandas,
openpyxl, pytest, openai
```

**Notes:**
- `selenium` + `selenium-stealth` + `webdriver-manager` are only needed for Stage 1 web page fetching.
- `python-docx` is only for legislation `.docx` extraction.
- `openpyxl` and `pandas` appear unused in the current codebase (pandas is imported but only referenced in a comment about "future compatibility").

---

#### `Semantic_Embeddings_Output.json` — (Referenced but not in provided files)

The semantic embeddings corpus. Each entry contains `UDID`, `Chunk_ID`, `Chunk_Text`, `Chunk_Embedding`, and optional metadata (`Headline_Alt`, `Page_Title`). This is the retrieval index for Stage 3.

---

#### `.gitignore` (as `gitignore` in the provided files)

Excludes `current_run_manifest.json` and `staging/` — ephemeral CI artifacts.

---

### 2.4 CI / GitHub Actions Workflows

#### `.github/workflows/tripwire.yml` — Main Pipeline (Scheduled)

- **Trigger:** Cron `0 20 * * 0-4` (6am AEST weekdays) + manual dispatch
- **What it does:** Full pipeline run — Stage 0 through Stage 5
- **Key steps:** Checkout → Python setup → Chrome setup (for Selenium) → Install deps → Run `tripwire.py` → Git commit/push any changes → Stage artifacts via manifest → Upload handover packets, verification results, and update suggestions as GitHub Artifacts
- **Permissions:** `contents: write` (commits changes back to repo)

#### `.github/workflows/llm-eval.yml` — Evaluation Suite

- **Trigger:** Manual dispatch only
- **What it does:** Runs `evaluate_tripwire_llm.py`
- **Uploads:** handover_packets, llm_verification_results, llm_update_suggestions, update_review_queue.csv

#### `.github/workflows/test-stage3.yml` — Stage 3 Tests

- **Trigger:** Manual dispatch
- **What it does:** Generates mock semantic data → runs `pytest test_stage3.py` → optionally runs a manual test on a real diff fixture
- **The manual test** is a Python inline script that imports tripwire functions directly

#### `.github/workflows/test_stage5.yml` — Stage 5 Tests

- **Trigger:** Push/PR to `tripwire_stage5.py` or `test_stage5.py`, plus manual dispatch
- **What it does:** `pytest -v test_stage5.py`
- **Note:** References `tripwire_stage5.py` in path triggers but the actual test imports from `tripwire.py` — this looks like a leftover from when Stage 5 was a separate file.

---

### 2.5 IPFR Content Archive (Prototype Fixtures)

#### `IPFR_content_archive/readme.txt`

Documents the chunk marker and section anchor conventions added to IPFR content files for Tripwire's LLM verification stage.

#### `IPFR_content_archive/101-1 - How to avoid infringing others' intellectual property_test.md`

Full IPFR page content with:
- YAML frontmatter (`udid: 101-1`, `ipfr_url`, `title`)
- `<!-- chunk_id: 101-1_01 -->` markers for chunk identification
- `<!-- section_id: section-1-avoiding-ip-infringement -->` anchors for LLM navigation
- Actual IP guidance content across 5 chunks

#### `IPFR_content_archive/101-1_how-to-avoid-infringing-others-intellectual-property_test.json`

JSON-LD structured data for page 101-1 using Schema.org vocabulary (`GovernmentOrganization`, `WebPage`, `WebPageElement`, `GovernmentService`). Contains metadata, section identifiers, and full text for each section.

#### `IPFR_content_archive/101-2 - Design infringement_test.md`

Same pattern as 101-1 but for the design infringement page. 6 chunks covering: FAQ (what is design infringement, what do registered designs protect, how does infringement happen), examples, what is NOT infringement, feedback. Includes footnote references to the *Designs Act 2003*.

#### `IPFR_content_archive/101-2_design-infringement_test.json`

JSON-LD for page 101-2. Includes `FAQPage` schema with 3 Q&A pairs, `Legislation` references, and section elements.

---

### 2.6 Test Fixtures (Diff Files)

All in `test_fixtures/diffs/`:

| File | Purpose | Characteristics |
|---|---|---|
| `high_relevance_trademark.diff` | Tests high-relevance trade mark content | Modifies Section 120 infringement definition; adds penalty clause with `$150,000`, `must`, `30 days` |
| `multi_impact_three_hunks.diff` | Tests multi-hunk multi-topic diffs | 3 hunks: trade mark enforcement, patent filing, design registration |
| `noise_only.diff` | Tests noise suppression | Only changes `lastBuildDate` in an RSS feed |
| `power_words_heavy.diff` | Tests power word detection | Dense with `must`, `shall`, `penalty`, `$5,000`, `mandatory`, `Archives Act 1983` |
| `stage3_trigger_multi_hunks.diff` | Realistic multi-hunk diff for manual testing | 5+ hunks modifying a "receiving a letter of demand" page with substantive + noise changes, includes `penalty`, `prohibited`, `cease`, `mandatory`, `criminal offence` |
| `unrelated_content.diff` | Tests irrelevant content filtering | Sports scores and weather — zero IP relevance |

---

### 2.7 Output Artifacts (Committed to Repo)

#### `audit_log.csv`

The persistent ledger. 27 columns tracking every pipeline run:
- Stage 0-3 metadata (timestamp, source, version, diff file, similarity score, power words, matched UDIDs)
- Stage 4 AI verification linkage (decision, confidence, model, verification file, overlap metrics)
- Stage 5 update suggestion linkage (status, suggested chunks, suggestion file)

The log shows real production runs from Feb 5 – Mar 17 2026, with sources being checked every ~6 hours. Notable patterns:
- ABC News triggers frequent diffs but they're all filtered (noise or below threshold)
- WIPO Press Room has triggered actual handover packets (scores 0.515 and 0.582)
- IP Australia trade marks page triggers occasional diffs
- Most verification results show `no_impact` because IPFR content files are missing for most UDIDs (markdown_path: null)

#### `handover_packets/*.json` (3 files)

Real handover packets from WIPO Press Room changes:

1. **Albania joins Riyadh Design Law Treaty** (Mar 15) — 22 candidate pages, single batch
2. **WIPO AI Infrastructure Interchange** (Mar 17) — 59 candidates, batch 1 of 2 (50 candidates)
3. **Same event** — batch 2 of 2 (9 remaining candidates)

Each packet contains: `audit_summary` (scoring, routing, batching), `source_change_details` (parsed hunks), `llm_verification_targets` (per-candidate with `evidence_resolution` metadata).

#### `llm_verification_results/*.json` (3 files)

Stage 4 verification outputs. All 3 show `overall_decision: "uncertain"` because most candidates resolve to empty evidence windows (IPFR markdown files missing for those UDIDs). The one candidate with content (101-2) correctly returns `no_impact` — Albania joining a treaty doesn't affect the design infringement guidance.

---

## 3. Data Flow Summary

```
sources.json
    │
    ▼
Stage 0: Probe metadata (ETag / registerId)
    │ change detected?
    ▼
Stage 1: Fetch & normalise → content_archive/
    │
    ▼
Stage 2: Unified diff vs. archive → diff_archive/*.diff
    │ substantive changes?
    ▼
Stage 3: Embed hunks → cosine similarity vs. Semantic_Embeddings_Output.json
         → score pages → apply bonuses → threshold check
    │ passes handover threshold?
    ▼
Handover packets → handover_packets/*.json
    │
    ▼
Stage 4 Pass 1: Load IPFR markdown → extract chunk window → LLM page impact check
    │ impact confirmed?
    ▼
Stage 4 Pass 2: Per-chunk verification targets → LLM chunk scoping
    │
    ▼
llm_verification_results/*.json
    │
    ▼
Stage 5: For each confirmed chunk → load markdown chunk text → LLM draft replacement
    │
    ▼
llm_update_suggestions/*.json → update_review_queue.csv
    │
    ▼
audit_log.csv (updated at each stage)
```

---

## 4. Key Design Decisions & Prototype Limitations

### Things that work well

1. **Recall-first philosophy** — the threshold hierarchy (High: any ≥ 0.35, Medium: primary ≥ 0.45, Low: primary ≥ 0.50) is a sound approach for a monitoring system where misses are expensive.

2. **Administrative noise filtering** — `_is_administrative_noise()` catches page numbers and date-only lines, preventing false triggers from RSS feed churn.

3. **Two-pass verification** — Pass 1 (page-level impact) → Pass 2 (chunk scoping) is a good pattern for minimising token spend while maintaining recall.

4. **Fail-closed/fail-open patterns** — Pass 2 failure promotes Pass 1 chunks to `additional_chunks_to_review` rather than silently dropping them.

5. **Audit log as single source of truth** — every decision point writes to the ledger, making the system auditable.

### Prototype limitations flagged in code

- **Glob-based IPFR file resolution** — `_resolve_ipfr_markdown_path()` uses filename pattern matching (`{udid} - *_test.md`). Production needs an explicit UDID→file map.
- **Top-N candidate loading** — `TOP_N_VERIFICATION_CANDIDATES = 3` means most candidates are never verified. The code has notes about switching to per-candidate verification.
- **Missing IPFR content** — The verification results show most UDIDs resolve to empty evidence windows because only 2 test markdown files exist (`101-1`, `101-2`). This is the biggest gap.
- **No chunk-level embedding retrieval** — Stage 3 retrieves all chunks for a page, but Stage 4 loads the full markdown and re-parses it. There's no direct chunk→embedding→text lookup.

### Potential issues I noticed

1. **Unused imports/deps:** `pandas` is imported but appears unused. `openpyxl` is in requirements but not imported.

2. **Duplicate LLM call functions:** `_call_llm_json()` and `_call_llm_json_with_model()` are near-identical. The first hardcodes `LLM_MODEL`, the second takes a parameter.

3. **Global state mutation:** `TOP_N_VERIFICATION_CANDIDATES` is mutated via `run_llm_verification_for_packets(top_n_candidates=...)`. This is a side-effect that persists across calls.

4. **`test_stage5.yml` path trigger mismatch:** Triggers on changes to `tripwire_stage5.py` but that file doesn't exist — Stage 5 was merged into `tripwire.py`.

5. **Semantic cache never invalidated:** `_semantic_cache` is a module-level dict that's only populated once. If the embeddings file changes during a run, stale data persists.

6. **`client = None` on line ~120:** A module-level `client` variable is set to None alongside the lazy `get_openai_client()` function — this shadowed variable is never used but could cause confusion.

---

## 5. Refactoring Blueprint: Breaking Up the Monolith

Here's a proposed module structure that preserves the existing logic while separating concerns. Each module maps cleanly to the responsibilities already present in `tripwire.py`.

### Proposed Package Structure

```
tripwire/
├── __init__.py              # Public API re-exports
├── __main__.py              # CLI entrypoint (replaces `if __name__ == "__main__"`)
├── config.py                # All constants, thresholds, paths, env vars
├── audit.py                 # Audit log schema, headers, read/write/update
├── utils.py                 # canonical_chunk_id, _list_to_semicolon, _now_iso, etc.
├── llm_client.py            # OpenAI client singleton + _call_llm_json (unified)
├── stage0_detect.py         # fetch_stage0_metadata, get_last_version_id
├── stage1_fetch.py          # All fetching: web, RSS, legislation, docx extraction
├── stage2_diff.py           # get_diff, save_diff_record, save_to_archive
├── stage3_score.py          # parse_diff_hunks, detect_power_words, calculate_similarity,
│                            # _embed_texts, _load_semantic_embeddings, _is_administrative_noise,
│                            # should_generate_handover, calculate_final_score
├── handover.py              # generate_handover_packets, _derive_packet_priority
├── ipfr_content.py          # resolve_ipfr_content_files, parse_markdown_chunks,
│                            # extract_chunk_window, build_chunk_index, _read_text_file
├── stage4_verify.py         # Pass 1 + Pass 2 prompts, verify_handover_packet_with_llm,
│                            # run_llm_verification_for_packets, all extraction helpers
├── stage5_suggest.py        # _build_stage5_prompt, run_llm_update_suggestions_for_verification_files
├── review_queue.py          # build_update_review_queue_rows_from_payload,
│                            # write_update_review_queue_csv_from_suggestion_files
├── manifest.py              # write_current_run_manifest, write_github_summary
└── pipeline.py              # main() orchestration loop (imports from all modules)
```

### Module Dependency Graph

```
config.py          ← imported by everything
    │
utils.py           ← imported by most modules
    │
audit.py           ← imported by stage modules + pipeline
    │
llm_client.py      ← imported by stage3 (embeddings), stage4, stage5
    │
┌───────────────────────────────────────────────┐
│  stage0 → stage1 → stage2 → stage3 → handover │
│                                        │       │
│              ipfr_content ←────── stage4       │
│                                    │           │
│                              stage5            │
│                                │               │
│                          review_queue          │
└───────────────────────────────────────────────┘
    │
pipeline.py / __main__.py  (orchestrates the above)
```

### Migration Strategy

**Phase 1 — Extract config and utilities (low risk, high clarity)**

Pull out `config.py` (all constants/thresholds), `utils.py` (pure functions), and `audit.py` (CSV operations). These have zero functional dependencies on pipeline logic.

**Phase 2 — Extract content resolution**

Pull `ipfr_content.py` — the markdown parsing, chunk windowing, and file resolution functions. These are self-contained and already called from multiple places.

**Phase 3 — Extract stages bottom-up**

Start with `stage0_detect.py` and `stage1_fetch.py` (fewest internal dependencies), then `stage2_diff.py`, then `stage3_score.py`. Each stage naturally depends on the ones before it.

**Phase 4 — Extract LLM-dependent stages**

Pull `llm_client.py` (unified LLM call function), then `stage4_verify.py` and `stage5_suggest.py`. These are the most complex but also the most self-contained once config/utils/content are extracted.

**Phase 5 — Wire up pipeline.py**

Replace `main()` with imports from all modules. The orchestration logic is ~120 lines and reads like a script once the stages are extracted.

### What NOT to Change During Refactoring

- **Audit log format** — the 27-column CSV schema is a contract with existing data. Don't change column names.
- **Handover packet JSON schema** — downstream consumers (LLM prompts, eval suite) depend on the exact field names.
- **Function signatures** — keep the public API identical so `evaluate_tripwire_llm.py` and tests continue to work.
- **`__init__.py` re-exports** — make sure `import tripwire; tripwire.calculate_similarity(...)` still works.

---

## 6. Quick Reference: Key Functions by Stage

### Stage 0 — Version Detection
| Function | Purpose |
|---|---|
| `fetch_stage0_metadata(session, source)` | HEAD request or OData probe to get current version ID |
| `get_last_version_id(source_name)` | Reads audit log for most recent successful version |

### Stage 1 — Fetch & Normalise
| Function | Purpose |
|---|---|
| `initialize_driver()` | Headless Chrome with stealth settings |
| `clean_html_content(html)` | Strips nav/footer/scripts, removes date stamps |
| `fetch_webpage_content(driver, url)` | Selenium render → clean → markdownify |
| `sanitize_rss(xml_content)` | Strips transient dates, sorts items by GUID |
| `fetch_legislation_metadata(session, source)` | OData API call for latest registerId |
| `download_legislation_content(session, base_url, meta)` | Downloads .docx/.html, extracts text |

### Stage 2 — Diff Generation
| Function | Purpose |
|---|---|
| `get_diff(old_path, new_content)` | Unified diff against archive |
| `save_diff_record(source_name, diff_content)` | Writes timestamped .diff file |
| `save_to_archive(filename, content)` | Writes normalised content to archive |

### Stage 3 — Semantic Scoring
| Function | Purpose |
|---|---|
| `parse_diff_hunks(diff_file_path)` | Splits unified diff into hunk objects |
| `detect_power_words(text)` | Tiered regex matching (strong/moderate/weak) |
| `calculate_similarity(diff_path, ...)` | **Main Stage 3 entry point** — the big function |
| `_embed_texts(texts)` | OpenAI embedding API call |
| `_load_semantic_embeddings(mock_data)` | Loads corpus from JSON (with caching) |
| `_is_administrative_noise(text)` | Page numbers, standalone dates → True |
| `should_generate_handover(...)` | Priority-aware threshold check |
| `calculate_final_score(base, power)` | Similarity + power word uplift |

### Handover Generation
| Function | Purpose |
|---|---|
| `generate_handover_packets(...)` | Builds JSON packets, handles batching |
| `_derive_packet_priority(...)` | Score + power words → Critical/High/Medium |

### Stage 4 — LLM Verification
| Function | Purpose |
|---|---|
| `verify_handover_packet_with_llm(packet_path, ...)` | **Main Stage 4 entry point** — two-pass workflow |
| `run_llm_verification_for_packets(paths, ...)` | Iterates packets, links audit log |
| `_build_llm_pass1_prompt(...)` | Page-level impact verification prompt |
| `_build_llm_pass2_prompt(...)` | Chunk-level scoping prompt |
| `_select_pass1_target_chunk_id(...)` | Lexical scorer picks best chunk for Pass 1 |
| `_score_chunk_for_pass1(...)` | Token overlap + phrase hit scoring |
| `_build_chunk_verification_targets(...)` | Builds compact per-chunk targets for Pass 2 |
| `_call_llm_json(prompt)` | LLM call with JSON parsing + fail-closed fallback |
| `parse_markdown_chunks(markdown_text)` | Splits markdown by `<!-- chunk_id: ... -->` markers |
| `extract_chunk_window(chunks, target_id, ...)` | Returns before/current/after window |
| `summarise_verification_files(paths)` | Aggregates verification results across files |

### Stage 5 — Update Suggestions
| Function | Purpose |
|---|---|
| `run_llm_update_suggestions_for_verification_files(paths, ...)` | **Main Stage 5 entry point** |
| `_build_stage5_prompt(...)` | Per-chunk content editor prompt |
| `_call_llm_json_with_model(prompt, model)` | LLM call (parameterised model) |

### Review Queue
| Function | Purpose |
|---|---|
| `build_update_review_queue_rows_from_payload(payload, ...)` | Flattens suggestion JSON → CSV rows |
| `write_update_review_queue_csv_from_suggestion_files(paths, ...)` | Writes consolidated CSV |
| `format_relevant_diff_text(hunks)` | Renders hunks as human-readable diff text |

---

## 7. Testing Coverage Assessment

| Area | Coverage | Notes |
|---|---|---|
| Stage 0 | None | No unit tests |
| Stage 1 | None | Would need mocked Selenium/requests |
| Stage 2 | None | Trivial functions, low risk |
| Stage 3 | Good | `test_stage3.py` covers parsing, scoring, noise, batching |
| Stage 4 | Indirect only | Tested via `evaluate_tripwire_llm.py` (integration), no unit tests for prompt building or result extraction |
| Stage 5 | Good | `test_stage5.py` covers happy path, multi-page, partial failures, additional review |
| Audit logging | Indirect | Tested as side-effect of Stage 5 tests |
| Content resolution | None | `parse_markdown_chunks`, `extract_chunk_window` have no dedicated tests |

**Recommendation:** When refactoring into modules, add unit tests for `parse_markdown_chunks`, `extract_chunk_window`, `_score_chunk_for_pass1`, and `_extract_confirmed_updates` — these are pure functions with complex logic that currently rely on end-to-end testing only.

---

## 8. Summary of Immediate Action Items

1. **Fix the `test_stage5.yml` path trigger** — change `tripwire_stage5.py` to `tripwire.py` (or update after refactoring).

2. **Remove unused imports** — `pandas` import, `openpyxl` from requirements, the shadowed `client = None` global.

3. **Unify LLM call functions** — merge `_call_llm_json` and `_call_llm_json_with_model` into one function with a default model parameter.

4. **Begin Phase 1 refactoring** — extract `config.py`, `utils.py`, `audit.py`. These are zero-risk moves that immediately improve readability.

5. **Add the missing IPFR content files** — the biggest operational gap is that Stage 4 verification finds empty evidence windows for 90%+ of candidates because the markdown archive only has 2 pages.
