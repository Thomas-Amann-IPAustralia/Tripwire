# Tripwire — Architectural & Implementation Review Backlog

**Date:** 2 July 2026
**Scope:** Full repository — main pipeline (`src/`), ingestion (`ingestion/`), CI/CD workflows, data layer, dashboard (`dashboard/`), tests, and documentation.
**Method:** Line-level review of all pipeline and ingestion modules, all four GitHub Actions workflows, spot-verification of the dashboard server/client, interrogation of the live SQLite database (`pipeline_runs`, `llm_assessments`, `pages`), and a local run of the test suite.

This document supersedes the dashboard-focused `Backlog.md` for prioritisation purposes (see TW-29 — most of its P0 items have since been fixed and it is now stale).

---

## 1. Review summary

### What is working well

- **The staged-funnel architecture is sound.** Cheap signals (HEAD probes, hashes) gate expensive ones (embeddings, cross-encoder, LLM). The design plan is unusually thorough and the code follows it closely in structure.
- **Error taxonomy** (`RetryableError` / `PermanentError`), per-source isolation in the orchestrator, and the deferred-trigger table are good resilience patterns.
- **Test breadth is genuine** — 573 tests, all pure-unit with no network access, covering every stage module. (Four currently fail; see TW-11.)
- **Observation mode, health alerting, and the `pipeline_runs` audit log** give the system real operational introspection that most prototypes lack.
- The dashboard team has been actively fixing the P0 items from the previous backlog (config path, health summary shape, filter wiring, `dbGuard` 503 — all verified fixed in the current tree).

### The headline concerns

Live data tells a clear story. Since going live (`observation_mode: false`):

| Signal | Value | Implication |
|---|---|---|
| Notification recipient in config | `content-owner@example.gov.au` (placeholder) | **Alert emails are going to a non-existent address** (TW-01) |
| LLM verdicts | 509 / 516 = 98.6% `NO_CHANGE` | Upstream thresholds pass almost pure noise to the LLM (TW-14) |
| `CHANGE_REQUIRED` verdicts since May | 7 | All 7 were likely never delivered to a human (TW-01) |
| Sources erroring **every single run** | 27–29 of 156 (~18%) | Persistent scrape failures, never triaged (TW-05, TW-06) |
| `pipeline_runs` error outcomes | 1,810 of 9,752 (1,609 retryable) | The retry module exists but **is never called** (TW-04) |
| Failing tests on the default branch | 4 of 573 | No CI test gate exists (TW-11) |
| Sitemap `last_modified` column | empty for all 140 rows | Ingestion change detection is dead code; the whole corpus is re-scraped and re-embedded daily instead (TW-12), and **new IPFR pages are never discovered** (TW-13) |

The system's stated core principle — *"fail-closed: uncertain → escalate, never silently drop signals"* — is violated at four separate points (TW-02, TW-03, TW-04, TW-07). Combined with the placeholder recipient address, the probability that a real regulatory change would currently reach a content owner is low. **Restoring end-to-end alert integrity is the urgent theme of this backlog.**

### Scoring model

- **Urgency (0–10):** 10 = the system is failing its mission today; 0 = cosmetic.
- **Difficulty (0–10):** 10 = multi-week redesign; 0 = one-line change.
- **Suggested order:** work top-down within Theme A first; elsewhere, pick high-urgency/low-difficulty items first (`U − D/2` is a reasonable tiebreak).

---

## 2. Backlog index

| ID | Task | Urgency | Difficulty |
|---|---|---:|---:|
| **A — Alert-path integrity** | | | |
| TW-01 | Replace placeholder notification emails; verify SMTP delivery end-to-end | 10 | 1 |
| TW-02 | Stop losing detected changes when Stages 4–6 fail mid-source | 8 | 4 |
| TW-03 | Defer (not drop) triggers on LLM schema-validation failure | 6 | 3 |
| TW-04 | Wire the retry layer into scraping and probing | 7 | 3 |
| TW-05 | Retry transient LLM errors in-run before deferring to next day | 6 | 2 |
| TW-06 | Triage the ~27 permanently failing sources | 8 | 5 |
| TW-07 | Re-send undelivered notifications (SMTP fallback follow-up) | 5 | 3 |
| TW-08 | De-duplicate / throttle health alerts; verify health email delivery | 5 | 3 |
| **B — Scraping robustness** | | | |
| TW-09 | Fall back to Selenium on HTTP 403/blocked status codes, not just connection errors | 8 | 3 |
| TW-10 | Restore the RSS channel — 65 of 67 RSS diffs ever produced are empty | 7 | 3 |
| TW-11′ *(see D)* | | | |
| **C — Corpus & ingestion correctness** | | | |
| TW-12 | Fix `_needs_ingestion`: dead change-detection logic causes daily full re-ingest | 5 | 3 |
| TW-13 | Refresh the sitemap on every ingestion run (discover new / retire removed pages) | 7 | 3 |
| TW-14 | Calibrate Stage 4/6 thresholds using the 516 accumulated LLM verdicts as labels | 7 | 6 |
| TW-15 | Exclude `stub`/`duplicate` pages from Stages 4, 6 and 8 | 5 | 2 |
| TW-16 | Align Stage-5 change chunking with ingestion chunking config | 5 | 3 |
| **D — CI/CD & operations** | | | |
| TW-11 | Add a CI test workflow; fix the 4 failing tests | 6 | 2 |
| TW-17 | Prune daily data releases; decide a sustainable state-storage strategy | 6 | 6 |
| TW-18 | Fix silent git-push failures and cross-workflow race windows | 5 | 4 |
| TW-19 | Pin dependencies (lockfile) and add automated update PRs | 6 | 3 |
| TW-20 | Schedule the weekly observability report (it currently never runs) | 5 | 2 |
| TW-21 | Verify and harden the feedback-ingestion loop end-to-end | 6 | 4 |
| **E — Performance & resource use** | | | |
| TW-22 | Fix the broken bi-encoder memory strategy (two model copies; per-source reloads) | 4 | 3 |
| TW-23 | Cache the spaCy model; build the BM25 index once per run | 3 | 2 |
| **F — Security** | | | |
| TW-24 | Harden the Stage-8 prompt against injection from scraped content | 5 | 5 |
| TW-25 | Escape all interpolated values in notification HTML | 4 | 2 |
| TW-26 | Quote/validate `workflow_dispatch` inputs in workflow shell steps | 3 | 1 |
| **G — Architecture & code health** | | | |
| TW-27 | Split `stage3_diff.py` (1,590 lines, four responsibilities) | 3 | 5 |
| TW-28 | Consolidate duplicated helpers (`_distribution`, bi-encoder loaders, chunkers) | 2 | 3 |
| **H — Documentation & governance** | | | |
| TW-29 | Re-triage the stale `Backlog.md`; close fixed items; merge remainder here | 4 | 2 |
| TW-30 | Fix doc drift (CLAUDE.md schema/timeouts/structure; brief vs API) | 4 | 2 |
| TW-31 | Define the config-governance path for dashboard "Adjust" writes | 5 | 5 |
| **I — Signal quality (longer-horizon)** | | | |
| TW-32 | Investigate uniform 0.85 LLM confidence; make confidence informative | 3 | 4 |
| TW-33 | Existing plan §5 deferred tasks (5.3–5.6) — unblocked earlier by TW-14 | 3 | 6 |
| **J — Quasi-graph (investigated 2 Jul; see §4 addendum)** | | | |
| TW-34 | Close the graph "side door": rejected neighbours reach the LLM ungated (45% of all LLM calls) | 7 | 4 |
| TW-35 | Pass correct (own) scores for graph-propagated triggers; fix `graph_propagated_to` bookkeeping | 5 | 3 |
| TW-36 | Implement internal-link edges (requires capturing hrefs at scrape time) and re-weight edge types | 6 | 6 |
| TW-37 | Retire or re-parameterise the designed boost mechanism (mathematically inert as configured) | 4 | 3 |
| **K — Cross-component contract breaks (second-pass sweep, 2 Jul)** | | | |
| TW-38 | Dashboard queries 12+ `details` JSON keys the pipeline never writes — verdicts/scores are null in all 9,752 rows | 7 | 5 |
| TW-39 | FRL fallback notices ("no ES retrievable") are silently dismissed as NO_CHANGE — force human review | 6 | 2 |

---

## 3. Detailed tasks

### Theme A — Alert-path integrity (the fail-closed gaps)

#### TW-01 — Replace placeholder notification emails; verify SMTP delivery end-to-end
**Urgency 10 · Difficulty 1**

`tripwire_config.yaml:93-94` ships `content_owner_email: "content-owner@example.gov.au"` and `health_alert_email: "admin@example.gov.au"` while `observation_mode: false`. Stage 9 (`src/stage9_notification.py:170`) reads the recipient straight from config with no environment override. The database shows 7 `CHANGE_REQUIRED` verdicts since 7 May 2026 — each should have produced an email, and each almost certainly bounced or was black-holed.

**Done when:** real recipient addresses are configured (or injected via secret if addresses are sensitive); a manual `workflow_dispatch` run confirms receipt of a test notification and a test health alert; and a checklist item is added to the go-live runbook so this cannot recur. Consider having Stage 9 refuse to run (hard error) when the recipient domain is `example.gov.au`.

#### TW-02 — Stop losing detected changes when Stages 4–6 fail mid-source
**Urgency 8 · Difficulty 4**

`src/pipeline.py:548-552` persists the new content baseline (`previous_text`/`previous_hash`) immediately after Stage 3 — *before* Stages 4–6 run. If relevance scoring, the bi-encoder, or the cross-encoder then throws (model download failure, OOM, SQLite error), the source is logged as `error`, but the next run compares against the *already-updated* baseline: hash matches, decision `no_change`, and the detected change is silently gone forever. This is a textbook fail-open path in a system whose design principle is fail-closed. The deferred-trigger mechanism only covers Stage-8 failures.

**Done when:** either (a) the baseline is persisted only after the source completes Stage 6 (with the Stage-3 write moved or made provisional), or (b) a `pending_change` marker (diff + old baseline) is written to state/DB on Stage-4-to-6 failure and replayed at the start of the next run, mirroring `deferred_triggers`. A regression test simulates a Stage-4 exception and asserts the change is re-detected on the following run.

#### TW-03 — Defer (not drop) triggers on LLM schema-validation failure
**Urgency 6 · Difficulty 3**

`src/stage8_llm.py:683-699`: when the LLM returns malformed JSON twice, the assessment is marked `schema_valid=False`, counted in `failed_count`… and the trigger bundle is discarded. It is not written to `deferred_triggers`, not surfaced in the notification email, and appears only as an aggregate count in a health alert (sent to the placeholder address — see TW-01). The module docstring and plan §6.5 both imply the bundle should be preserved.

**Done when:** a twice-failed bundle is written to `deferred_triggers` (or listed in the email's "requires human review" section with the raw response attached); a test asserts no bundle is ever dropped without a durable record. Note: the health alert text (`health.py:274-275`) currently *claims* affected bundles "have been written to the deferred_triggers table for retry" — false today; correct the text alongside the behaviour.

#### TW-04 — Wire the retry layer into scraping and probing
**Urgency 7 · Difficulty 3**

`src/retry.py` (with config keys `pipeline.max_retries`, `retry_base_delay_seconds`) is imported by **nothing** outside its own tests. `scrape_and_normalise`, `probe_source`, and the FRL API calls all raise `RetryableError` — which then falls through to the orchestrator's catch-all and marks the source errored on the first transient failure. The database records 1,609 `RetryableError` outcomes that, by design, should have been retried. This directly inflates the ~18% standing error rate (TW-06).

**Done when:** `retry_call` (config-driven) wraps the scrape/probe call sites in `pipeline.py._process_source`; the per-run error count for known-flaky sources measurably drops; retries are visible in the run log (`retries` field in `details`).

#### TW-05 — Retry transient LLM errors in-run before deferring to next day
**Urgency 6 · Difficulty 2**

`src/stage8_llm.py:601-625`: a single `RetryableError` (one 429 rate-limit, one timeout) immediately defers the bundle — meaning the alert is delayed a full 24 hours. The docstring says "when RetryableError **exhausted**", but there is no exhaustion: `max_retries` is never consulted here. A rate-limit blip on a batch of bundles defers all of them.

**Done when:** the OpenAI call is retried with exponential backoff (`pipeline.max_retries` / `retry_base_delay_seconds`) before `_write_deferred_trigger` is used as the last resort; a test with a client failing twice then succeeding asserts the assessment completes in-run.

#### TW-06 — Triage the ~27 permanently failing sources
**Urgency 8 · Difficulty 5**

27–29 of 156 sources have errored on **every run** (62–70 consecutive failures each): `federal_court_*` (10+ sources), `auda_*`, `aph_ip_committee`, `intellectual_property_how_to_import`, etc. Nobody is receiving the consecutive-failure health alerts (TW-01), so this has sat unaddressed for two months. These sources contribute zero monitoring coverage while consuming Selenium time every run. Root causes will be a mix of TW-09 (403s never reach Selenium fallback), missing `force_selenium` flags, genuinely blocked GHA IPs (needs `SCRAPER_PROXY_URL`), and dead URLs.

**Done when:** each failing source has a diagnosis recorded in the registry `notes` column and is either (a) fixed via flags/proxy/URL correction, (b) replaced with an alternative source, or (c) consciously retired with sign-off. Standing per-run error count drops below the 30% health threshold *per source category*, and a runbook section documents the triage procedure.

#### TW-07 — Re-send undelivered notifications (SMTP fallback follow-up)
**Urgency 5 · Difficulty 3**

When all SMTP retries fail, Stage 9 writes the email body to `data/logs/email_fallback_{run_id}.txt` (`stage9_notification.py:738-745`) on the ephemeral runner. It surfaces only as a CI artifact; no subsequent run re-attempts delivery. An SMTP outage on the wrong day means a real alert exists only inside a zip file nobody opens.

**Done when:** undelivered notifications are queued durably (e.g. a `deferred_notifications` table or committing the fallback file) and re-sent at the start of the next run, mirroring the deferred-trigger pattern; the GitHub job summary flags any undelivered alert loudly.

#### TW-08 — De-duplicate / throttle health alerts; verify health email delivery
**Urgency 5 · Difficulty 3**

With ~27 sources above the consecutive-failure threshold, `src/health.py` fires alerts every single run — once the recipient address is fixed (TW-01), the content owner will receive an identical multi-alert email daily, which trains people to ignore it. Alert on *state transitions* (source newly failing, source recovered) rather than steady state, or batch a weekly digest for chronic failures.

**Done when:** repeated identical alerts are suppressed (state tracked in `state.json` or DB); a test covers "no re-alert on unchanged failure set"; delivery to the real health address is confirmed.

---

### Theme B — Scraping robustness

#### TW-09 — Fall back to Selenium on blocked HTTP status codes
**Urgency 8 · Difficulty 3**

`src/scraper.py:335-353`: `_fetch_with_requests` returns `None` (→ Selenium fallback) only on connection *exceptions*. A non-200 status raises immediately — and WAFs overwhelmingly block with **403**, which `http_error()` classifies as `PermanentError`. Result: any source whose WAF 403s plain `requests` never gets the Selenium/stealth/proxy attempt that exists precisely for that scenario. This is very likely a top contributor to the standing failure set (TW-06).

**Done when:** 403/406/429/503 (and any response carrying a block signature) trigger the Selenium fallback chain before any error is raised; unit tests cover the 403→Selenium path; the failing-source count drops.

#### TW-10 — Restore the RSS channel (65 of 67 diffs ever produced are empty)
**Urgency 7 · Difficulty 3** *(urgency raised from 4 after empirical verification, 2 Jul)*

RSS monitoring is effectively non-functional and always has been. Of 67 RSS Stage-3 diffs in the database, **65 are empty and 64 carry fetch warnings**; across the 5 registered feeds (Federal Court ×2, WIPO ×2, EUIPO), only `wipo_arbitration_mediation_center_news` has ever yielded items — twice, in late April/early May. Root cause: feeds are fetched up to three times per run (Stage-1 probe, `scrape_and_normalise` bookkeeping fetch, Stage-3 `_generate_rss_diff` re-fetch), and the Stage-3 fetch (`stage3_diff.py:1243-1250`) — the only one whose output feeds the funnel — uses plain `requests` with no `force_selenium`/proxy support, so WAF-protected feeds pass Stages 1–2 via Selenium and then produce an empty diff. The RSS state file also grows without bound (GUID history never pruned).

**Done when:** the raw XML fetched once (with the full fallback chain) is passed down to Stage 3 instead of re-fetched; each of the 5 feeds demonstrably produces items on a forced run (or is retired per TW-06 triage); GUID history is capped; tests cover the blocked-feed path.

---

### Theme C — Corpus & ingestion correctness

#### TW-12 — Fix `_needs_ingestion`: dead change-detection logic
**Urgency 5 · Difficulty 3**

`ingestion/ingest.py:546-556` decides re-ingestion using `row.get("last_ingested")` — but the sitemap CSV schema (`ingestion/sitemap.py`) has **no `last_ingested` column**, so the check is always true and every page is re-scraped, re-chunked, re-embedded and re-NER'd daily (confirmed: all 139 pages have `last_ingested = 2026-07-01`). The intended `last_modified > last_ingested` comparison can also never fire because `last_modified` is empty on every row. The current behaviour keeps the corpus fresh *by accident* and at maximum cost (daily full Selenium crawl + full embedding recompute — the reason the workflow needs a 60-minute timeout).

**Done when:** the ingestion decision is hash-based: scrape cheaply, compare `version_hash`, and only re-enrich (embed/NER/YAKE) on genuine content change — or, if daily full re-ingest is the deliberate choice, delete the dead logic and document the decision. Either way the sitemap schema and the code must agree.

#### TW-13 — Refresh the sitemap on every ingestion run
**Urgency 7 · Difficulty 3**

`_load_or_bootstrap_sitemap` (`ingestion/ingest.py:225-251`) fetches `sitemap.xml` **only when the CSV is empty**. Since bootstrap, the page registry has been frozen at 140 rows: pages added to IP First Response are never ingested (changes affecting them are invisible to the whole pipeline), and removed pages are never retired (they linger as scrape errors or stale corpus content). For a system whose entire purpose is keeping IPFR accurate, corpus completeness is foundational.

**Done when:** every ingestion run re-fetches the sitemap, merges new URLs (preserving existing metadata via the already-written `build_sitemap_from_urls`), and marks pages absent from the sitemap as `status='removed'` (excluded from matching); the job summary reports added/removed counts.

#### TW-14 — Calibrate Stage 4/6 thresholds using accumulated LLM verdicts
**Urgency 7 · Difficulty 6**

509 of 516 LLM assessments (98.6%) returned `NO_CHANGE`. The cross-encoder threshold (0.60) and Stage-4/5 gates are passing almost pure noise to Stage 8 — the LLM is functioning as the real filter, which inverts the funnel's cost design and buries the 7 real hits in noise. Plan task 5.3 defers calibration until human feedback accumulates, but there are already 516 LLM-labelled (bundle → verdict) samples with full per-stage scores in `pipeline_runs.details` and `llm_assessments` — enough to calibrate now, using LLM verdicts as pseudo-labels (validated later against human feedback).

**Done when:** an offline analysis script (e.g. `scripts/calibrate_thresholds.py`) joins historical stage scores to verdicts, sweeps `crossencoder.threshold`, `biencoder` thresholds and `min_score_threshold`, and reports the precision/recall trade-off; new thresholds are proposed as a config PR with the analysis attached. Target: cut daily LLM bundle volume by ≥70% while retaining all historical `CHANGE_REQUIRED` cases.

#### TW-15 — Exclude `stub`/`duplicate` pages from matching and assessment
**Urgency 5 · Difficulty 2**

The `pages` table carries `status` (`active`/`stub`/`duplicate` — currently 131/7/1), but Stage 4 (`stage4_relevance.py:276-281`), Stage 6 (`stage6_crossencoder.py:362-373`) and Stage 8 (`stage8_llm.py:707-716`) all query without a status filter. Stubs and duplicates can absorb candidate slots (top-N is fixed at 5), receive cross-encoder confirmation, and be flagged in alerts — pointing the content owner at a page that says "coming soon" or duplicates another. Ingestion Phase 6 already excludes them from the graph; the main pipeline should be consistent.

**Done when:** all three loaders filter `status = 'active'`; a test seeds a stub page and asserts it never appears in candidates or bundles.

#### TW-16 — Align Stage-5 change chunking with ingestion chunking
**Urgency 5 · Difficulty 3**

`stage5_biencoder.py:38-40` hardcodes 512-char chunks / 64 overlap with a comment claiming it "mirrors the ingestion pipeline defaults" — but ingestion uses 1,400-char chunks / 200 overlap from `ingestion.chunking` config. Change chunks are therefore ~3× smaller than the corpus chunks they're compared against, which systematically shifts cosine scores and makes the calibrated thresholds (TW-14) less stable. Stage 5 also ignores the config section entirely.

**Done when:** Stage 5 reads `ingestion.chunking` (or a shared chunking helper is extracted — see TW-28) so both sides of the similarity use the same granularity; thresholds re-checked after the change (sequence with TW-14).

---

### Theme D — CI/CD & operations

#### TW-11 — Add a CI test workflow; fix the 4 failing tests
**Urgency 6 · Difficulty 2**

No workflow runs `pytest` — tests execute only when a developer remembers. Four currently fail on the default branch: `test_load_valid_config_from_repo_root` asserts `observation_mode is True` (stale since go-live), and three `TestProbeFrlFallbacks` tests have under-specified `MagicMock` sessions (`resp.status_code >= 400` explodes comparing a mock to an int). Failures that sit unnoticed erode the value of an otherwise strong suite.

**Done when:** a `test.yml` workflow runs the suite on push/PR (lightweight deps only — the suite needs no torch/network); the 4 tests are fixed; branch protection (or at minimum convention) requires green tests before merge.

#### TW-17 — Prune daily data releases; decide a sustainable state-storage strategy
**Urgency 6 · Difficulty 6**

Every daily pipeline run creates a **new GitHub Release** (`data-YYYYMMDD-HHMMSS`) carrying the full 50 MB SQLite plus snapshots tarball, and never deletes old ones (~365 releases/year, tens of GB). Separately, the 50 MB SQLite binary is committed to git up to twice daily (pack already 43 MiB after ~2 months; binary deltas compress poorly). This is the "state in git" architecture reaching its limits. Short term: prune. Medium term: decide the storage architecture deliberately.

**Done when:** (short term) the release step deletes releases older than N days, or reuses a single rolling `data-latest` tag with `--clobber`; (medium term) an ADR chooses between rolling-release-only state (drop SQLite from git history), Git LFS, or an object store — with repo-size growth projections. Ensure the Render dashboard's `GITHUB_RELEASE_TAG` contract is updated in the same change.

#### TW-18 — Fix silent git-push failures and cross-workflow race windows
**Urgency 5 · Difficulty 4**

Three workflows plus the in-pipeline `_git_commit_snapshots()` (`pipeline.py:935-982`) all push to the same branch under *different* concurrency groups, so races are possible (e.g. feedback ingestion at 06:00 vs a long pipeline run). Failure handling makes this worse: the pipeline's internal push failure is a log-warning; the workflow "safety net" step ends with `git push … || true`. A rejected push means snapshots/state silently fail to persist → the next run re-detects the same changes → duplicate LLM calls and duplicate alerts, with nothing flagged.

**Done when:** pushes use a shared retry-with-rebase helper (`git pull --rebase && git push`, N attempts); `|| true` is removed so persistence failure fails the job visibly; either the double-commit (Python + workflow) is collapsed into one place, or the workflow step is explicitly documented as the only writer. Consider a single shared concurrency group for all state-writing workflows.

#### TW-19 — Pin dependencies and add automated update PRs
**Urgency 6 · Difficulty 3**

Both requirements files use floor constraints only (`sentence-transformers>=3.0`, `openai>=1.30`, …). Every nightly run installs whatever is newest — non-reproducible builds, exposure to breaking releases (this already forced the `blinker<1.7` pin), and an open supply-chain surface, which matters in a government context. `webdriver_manager` additionally downloads chromedriver from the network at runtime.

**Done when:** a compiled lockfile (`pip-tools`/`uv`) pins exact versions used by CI; Dependabot/Renovate raises weekly update PRs gated by the CI suite (TW-11); model weights remain cache-pinned as now.

#### TW-20 — Schedule the weekly observability report
**Urgency 5 · Difficulty 2**

`src/observability.py` (498 lines, tested) generates the weekly score-distribution report required by plan §8 and claimed as delivered in the HLD — but no workflow, cron, or documentation invokes it. It has effectively never run. Score-distribution visibility is also the prerequisite for sane threshold work (TW-14).

**Done when:** a weekly workflow runs `python -m src.observability --days 30`, commits the report under `data/logs/` (or posts it to the job summary), and the HLD/runbooks reference it accurately.

#### TW-21 — Verify and harden the feedback-ingestion loop end-to-end
**Urgency 6 · Difficulty 4**

`data/logs/feedback.jsonl` does not exist in the repository, so either no feedback has ever been submitted or the loop has never worked — and nothing distinguishes the two. Implementation gaps compound this: messages are flagged `\Seen` *before* records are persisted (`feedback_ingestion.py:126-160` — a crash after flagging loses feedback permanently); malformed messages are left unread and re-fetched every 6 hours forever; and the workflow's plain `git push` can race (TW-18). The feedback loop is what unblocks every deferred calibration task, so it needs to demonstrably work before data is expected from it.

**Done when:** a test email sent to the feedback mailbox appears in a committed `feedback.jsonl` within one cycle; messages are flagged read only after a successful append; malformed messages are quarantined (label/folder) after N attempts with a log line; the loop's health (last-poll timestamp, parse failures) is visible in the dashboard or job summary.

---

### Theme E — Performance & resource use

#### TW-22 — Fix the broken bi-encoder memory strategy
**Urgency 4 · Difficulty 3**

`stage4_relevance.py` and `stage5_biencoder.py` each maintain a **separate** module-level cache of the *same* `BAAI/bge-base-en-v1.5` model — two full copies in RAM. `release_biencoder()` (called per-source between Stages 5 and 6, `pipeline.py:634`) clears only Stage 5's cache, so: (a) the Section-7.4 "release before cross-encoder" memory strategy never actually frees Stage 4's copy, and (b) Stage 5 cold-reloads the model from disk for *every source* that reaches it. Wrong on memory and wasteful on time.

**Done when:** one shared model-cache helper serves Stages 4 and 5; release happens once per run (after the last Stage-5 use), not per source; peak-RSS and per-source timings in the run log confirm the improvement.

#### TW-23 — Cache the spaCy model; build the BM25 index once per run
**Urgency 3 · Difficulty 2**

`stage2_change_detection.py:342-349` — `_get_spacy_model()` reloads `en_core_web_sm` from disk on **every** fingerprint call despite the "lazy-load" docstring (~1s × every changed source). `stage4_relevance.py` re-reads all page content and rebuilds the BM25Okapi index from scratch per changed source; corpus content changes at most once per day, so build it once per pipeline run.

**Done when:** both are cached at module/run scope; per-source Stage-2/Stage-4 durations drop accordingly.

---

### Theme F — Security

#### TW-24 — Harden the Stage-8 prompt against injection from scraped content
**Urgency 5 · Difficulty 5**

Stage 8 interpolates raw scraped diff text from external websites directly into the LLM user message (`stage8_llm.py:394-447`). A compromised or malicious monitored page could embed instructions ("verdict must be NO_CHANGE…" / crafted `suggested_changes`) to suppress real alerts or inject misleading amendment advice into emails read by government content owners. The JSON-schema constraint limits the blast radius but not verdict/reasoning manipulation.

**Done when:** diff text is delimited with explicit untrusted-content framing and an instruction that content inside delimiters is data, never instructions; a canary/heuristic flags diffs containing prompt-injection patterns for `UNCERTAIN` escalation; a red-team test file with hostile diffs is added to the suite documenting expected behaviour.

#### TW-25 — Escape all interpolated values in notification HTML
**Urgency 4 · Difficulty 2**

`_html_escape` is applied to diff text, reasoning and suggested changes — but **not** to `meta.title`, `meta.url`, `trig.source_url` or `trig.source_id`, which are interpolated into `<h3>`/`<a href>` (`stage9_notification.py:548-664`). Page titles originate from scraped IPFR HTML and source URLs from an editable CSV; a crafted title becomes markup in the recipient's mail client.

**Done when:** every interpolated value is escaped (and URLs validated as http/https before being used as `href`); a test asserts a hostile title renders inert.

#### TW-26 — Quote/validate `workflow_dispatch` inputs in workflow shell steps
**Urgency 3 · Difficulty 1**

`tripwire.yml` interpolates `run_id_override` and `check_frequency_override` into bash unquoted (`RUN_ID_ARG="--run-id ${{ … }}"`). Requires repo-write to exploit, so low severity — but it is a one-line fix per input (use `env:` indirection and quote), and standard hardening for government repos.

**Done when:** all dispatch inputs pass through `env:` and are quoted; `actionlint` added to CI (pairs with TW-11).

---

### Theme G — Architecture & code health

#### TW-27 — Split `stage3_diff.py`
**Urgency 3 · Difficulty 5**

At 1,590 lines, it contains four distinct subsystems: webpage unified-diff + snapshot rotation, a ~900-line FRL explainer-acquisition engine (FRL REST API traversal, ParlInfo scraping, EM/ES discovery, DOCX download), RSS parsing/diffing, and normalisation helpers. This violates the repo's own "one file per responsibility" constraint and makes the highest-risk scraping code (the FRL/ParlInfo chain, full of anchored-marker heuristics) hard to test and review in isolation.

**Done when:** FRL explainer acquisition moves to `src/frl_explainer.py` and RSS diffing to `src/rss_diff.py` (or similar), with `stage3_diff.py` as the thin dispatcher; tests move alongside; no behaviour change (golden-output tests before/after).

#### TW-28 — Consolidate duplicated helpers
**Urgency 2 · Difficulty 3**

`_distribution()` is copy-pasted in three modules (stages 4, 5, 6); bi-encoder loading logic exists in stages 4, 5 and `ingestion/enrich.py`; chunking exists in Stage 5 and `ingestion/enrich.py` (already divergent — see TW-16); `normalise_text`/`_strip_html_basic` exist in both `src/scraper.py` and `ingestion/scrape_ipfr.py`. Divergence between copies has already caused one real defect (TW-16).

**Done when:** shared helpers live in one module (e.g. `src/common/`), imported by both pipelines; duplicated definitions deleted.

---

### Theme H — Documentation & governance

#### TW-29 — Re-triage the stale `Backlog.md`
**Urgency 4 · Difficulty 2**

Spot-verification shows most of its P0 items are already fixed in the current tree (config path + validator schema, health-summary shape + Topbar wiring, filter param names, `timestamp ?? run_at` fallbacks, `dbGuard` 503, snapshot `previous_text` fallback). Keeping a backlog that claims shipped features are broken misdirects effort and undermines trust in the document.

**Done when:** every `Backlog.md` item is re-verified against the code, fixed items are marked closed with the fixing commit, and surviving items are migrated into this scored backlog (mostly Theme I / dashboard feature work).

#### TW-30 — Fix documentation drift
**Urgency 4 · Difficulty 2**

Confirmed drift: CLAUDE.md documents a `page_chunks` table (actual: `chunks`) and omits `llm_assessments` entirely; states a 30-minute pipeline timeout (actual: 60 in `tripwire.yml`); its repository-structure section omits `dashboard/`, `render.yaml`, `publish-dashboard-data-release.yml`, `Backlog.md` and `data/LLM Reports/`; the dashboard brief documents `/api/sources/:id/snapshot` vs the implemented `/api/snapshots/:id`. Stray artefacts (`data/LLM Reports/Test`, `docs/Reference-Code/fetch_em_summary (1).py`) should be removed.

**Done when:** CLAUDE.md, the HLD and the brief match the code (a single pass); stray files deleted; a "docs updated?" line item added to the PR checklist.

#### TW-31 — Define the config-governance path for dashboard "Adjust" writes
**Urgency 5 · Difficulty 5**

`POST /api/config` writes YAML to the Render instance's local disk. The pipeline reads config from *git*; the dashboard re-syncs its copy from releases on redeploy. So an operator "saving" thresholds in the dashboard changes nothing in the actual pipeline and their edit is silently overwritten on the next deploy — worse than read-only, because it looks like it worked. Threshold changes are exactly the parameter changes the config header says must be tracked as commits.

**Done when:** either the dashboard's save path commits to git (server-side PR via a scoped token, with audit trail of who/when/what), or Adjust becomes explicitly read-only with a "propose change" flow that opens a pre-filled PR; the silent-loss path is eliminated.

---

### Theme I — Signal quality (longer horizon)

#### TW-32 — Make LLM confidence informative
**Urgency 3 · Difficulty 4**

All 7 historical `CHANGE_REQUIRED` verdicts carry confidence **exactly 0.85**, strongly suggesting the model echoes the prompt's "confidence ≥ 0.70" anchor rather than expressing calibrated belief. Downstream calibration plots (planned in the dashboard) will be meaningless against a constant. Investigate prompt changes (qualitative bands mapped server-side), comparing verdict stability across temperature/self-consistency, or dropping the field from decision logic until it carries signal.

#### TW-33 — Plan §5 deferred tasks (5.3–5.6)
**Urgency 3 · Difficulty 6**

Tracked in CLAUDE.md and TODOs: human-feedback threshold calibration (5.3), weight grid search (5.4), internal-link graph edges (5.5), BM25 proximity extensions (5.6). Note that TW-14 partially unblocks 5.3/5.4 *now* using LLM pseudo-labels; 5.5 requires link extraction in `ingestion/graph.py`; keep 5.6 parked until evidence shows lexical scoring is the weak signal.

---

### Theme J — Quasi-graph findings and tasks (added 2 July 2026)

*Investigation prompted by the hypothesis that graph propagation may be harming filtration quality and that same-site internal links — a higher-quality relatedness signal — are not captured. Both claims were tested against the code and the live database. Verdict: the internal-links claim is fully correct; the "harming filtration" claim is half correct — the graph is silently **doubling LLM volume through an unintended path**, but that same path produced 3 of the 7 CHANGE_REQUIRED alerts ever raised, so it cannot simply be deleted. Full analysis in §4.*

#### TW-34 — Close the graph "side door" (rejected neighbours reach the LLM ungated)
**Urgency 7 · Difficulty 4**

Stage 6's bookkeeping (`stage6_crossencoder.py:235-240`) appends **any** page that accumulated a ≥0.05 boost to the `graph_propagated_to` list of every confirmed neighbour — regardless of whether that page passed, or even approached, the 0.60 threshold. Stage 7 (`stage7_aggregation.py:234-273`) then builds a full trigger bundle for each such page. Measured effect: **231 of 516 LLM assessments (45%) were for pages the cross-encoder rejected**, entering through this path. The Stage-6 gate is effectively bypassed for the entire embedding-neighbourhood of every confirmed page. However, 3 of the 7 historical `CHANGE_REQUIRED` verdicts came from exactly these pages — the side door is also functioning as an accidental recall net over a (likely miscalibrated, see TW-14) cross-encoder threshold.

**Done when:** the expansion is either (a) removed, with recall recovered deliberately via TW-14 threshold calibration, or (b) retained as an explicit, documented "neighbour audit" with its own gate (e.g. only neighbours whose *own* boosted `final_score` clears the threshold, or a per-bundle cap) — decided using the calibration data, since the CE threshold and this expansion trade off against each other. Either way, the number of LLM calls per confirmed page becomes bounded and intentional.

#### TW-35 — Correct scores for propagated triggers; fix `graph_propagated_to` bookkeeping
**Urgency 5 · Difficulty 3**

Two defects in the same path: (1) Stage 7 gives a propagated page's `TriggerSource` the **source page's** cross-encoder scores (the code comments concede the propagated score "we don't have directly here") — so the LLM receives inflated relevance evidence for pages that scored below threshold; (2) the Stage-6 marking loop tags a neighbour on every confirmed page that has *any* edge to it, whether or not that page actually contributed the boost, producing misleading provenance in logs and bundles.

**Done when:** propagated triggers carry their own (boost/final) scores; `graph_propagated_to` reflects actual contribution above the floor; the Stage-8 prompt's "indirect signal" note keys off accurate data.

#### TW-36 — Implement internal-link edges; re-weight edge types
**Urgency 6 · Difficulty 6**

Confirmed: internal links are not captured anywhere. `graph.py:69-72` logs "not yet implemented" even if enabled; config has `internal_links.enabled: false`; and — the deeper blocker — both scrapers call `trafilatura.extract()` without `include_links`, so hrefs are destroyed before snapshots are written. Plain-text snapshots cannot be mined for links retroactively. The hypothesis that editorial links are the higher-quality signal is well-founded: they are human-curated, *directed*, sparse, and orthogonal to embedding similarity — whereas 73% of current edges (655/897) are embedding-similarity edges (mean weight 0.82) that **re-encode the same signal the bi-encoder and cross-encoder already measured**, so propagation along them double-counts evidence rather than adding independent support. This is consistent with the live data: side-door pages convert to `CHANGE_REQUIRED` at roughly the same low rate as directly confirmed ones (~1.3% vs ~1.4%) — the graph currently widens recall without adding precision.

**Done when:** ingestion captures in-domain `<a href>` targets from the main content region at scrape time (e.g. `trafilatura.extract(..., include_links=True)` or parsing the raw HTML before extraction), stores them (new `page_links` table or equivalent), and Phase 6 builds directed `internal_links` edges; `internal_links.enabled` flips to true; embedding-similarity edge weight is reviewed (down-weighted or dropped) once link edges exist, since the two now compete as propagation carriers. Plan task 5.5 is satisfied by this work.

#### TW-37 — Retire or re-parameterise the designed boost mechanism
**Urgency 4 · Difficulty 3**

The *intended* propagation mechanism — boosts lifting pages over the 0.60 threshold, or standalone graph-only pages — has fired **zero times in 148 Stage-6 completions**, and parameter analysis shows it mathematically almost cannot fire: max per-edge boost potential is 0.088 at a perfect seed score (median 0.055), only 76/897 edges clear the 0.05 floor at a realistic seed of 0.65, no realistic combination bridges a 0.10 gap, and second-hop signals (~0.004) always die at the floor — making `max_hops: 3` illusory. As configured, the designed mechanism is dead weight while the accidental side door (TW-34) does all the work.

**Done when:** after TW-34/TW-36 land, the additive-boost parameters are either re-derived so the mechanism has a plausible firing range (validated against historical scores), or the boost path is removed and the graph's role is redefined as bundle-context/neighbour-audit only. The decision and evidence are recorded in the config comments or an ADR.

### Theme K — Cross-component contract breaks (second-pass sweep, 2 July 2026)

*Found by systematically validating cross-component data contracts against the live database — the same method that exposed the graph side-door. See §4.2 for the sweep summary.*

#### TW-38 — Dashboard queries `details` JSON keys the pipeline never writes
**Urgency 7 · Difficulty 5**

The dashboard server extracts at least 12 JSON paths from `pipeline_runs.details` that the pipeline has never produced. Verified against all 9,752 rows: **zero** contain `$.stages.llm_assessment.*` (verdict/confidence/reasoning/suggested_changes/schema_valid), `$.stages.diff.diff_text`, `$.stages.biencoder.candidate_pages`, `$.stages.crossencoder.scored_pages`, `$.stages.relevance.rrf_score`, or `$.graph_propagated`. The pipeline actually writes only summary counts (`biencoder: {candidates_in, candidates_out}`, `crossencoder: {candidates_in, confirmed, graph_propagated}`, etc.), and Stage-8 results go solely to the `llm_assessments` table (which only `llm-reports.js` reads correctly).

Blast radius: the runs API (`runs.js:26-41`) returns null verdict/confidence/reasoning/scores for every row — so the Triggered Events table, EventDrawer score panels and the Observe section run on nulls; `pages.js:147,212-213` and `graph.js:21` count `CHANGE_REQUIRED` per page as permanently zero; `health.js:54,88-93` LLM metrics are permanently zero. A compounding gap: `triggered_pages` only records directly-confirmed pages, so even after a join to `llm_assessments`, per-page alert counts would miss the 45% of assessments (including 3 of 7 CHANGE_REQUIRED) that arrived via the graph side door (TW-34).

**Done when:** one side of the contract is fixed and the other verified against it — either (a) the pipeline writes the richer per-source detail the dashboard expects (per-page scored lists + an `llm_assessment` summary keyed back to sources), or (b) the dashboard derives verdicts by joining `llm_assessments` and reads the summary keys that actually exist. An integration test loads a real `details` row and asserts every JSON path the server queries is present. The imagined-schema keys are deleted from whichever side loses.

#### TW-39 — FRL fallback notices are silently dismissed — force human review
**Urgency 6 · Difficulty 2**

When a legislative compilation changes but no Explanatory Statement can be retrieved, Stage 3 substitutes a ~15-word placeholder ("Compilation updated for X. No Explanatory Statement could be retrieved automatically") whose stated purpose is "so downstream stages can still flag the change for manual review" (`stage3_diff.py:281-337`). In practice there is no manual-review path: verified 4 occurrences (Trade Marks Regulations, two AAO sources, Australian Border Force Act 2015 on 25 June); each placeholder sailed through Stages 4–6 (triggering 5 pages — further evidence the semantic gates barely discriminate, see TW-14), and the LLM — reasonably, given a contentless diff — returned `NO_CHANGE`. Net effect: **known legislative changes with unretrievable detail are silently dismissed**, another fail-closed violation.

**Done when:** `diff_type == "compilation_change"` bypasses LLM adjudication and is routed directly to the notification email's "requires human review" section (with the FRL register link), regardless of scores; a test asserts a fallback notice always reaches the email.

## 4. Addendum — quasi-graph investigation (2 July 2026)

**Question 1: is graph propagation harming filtration quality?**

Partially, yes — but not through the mechanism one would guess, and removing it naively would hurt.

- The **designed** mechanism is inert. Boosts are `score × weight × 0.45 / out_degree`; with median out-degree 6 the median per-edge boost potential is 0.055 *at a perfect seed score of 1.0*. No page has ever been lifted over the 0.60 threshold by a boost, and no graph-only page has ever been added (0 occurrences in 148 Stage-6 completions). Multi-hop propagation has never survived the 0.05 floor.
- The **accidental** mechanism dominates. A bookkeeping loop in Stage 6 plus bundle expansion in Stage 7 sends every ≥0.05-boosted *neighbour of a confirmed page* to the LLM with no score gate and with the confirmed page's (higher) scores attached. Verified per-run: e.g. run `2026-06-21-367` had 4 confirmed (source, page) pairs but 18 pages assessed; all 14 extras are graph-neighbours of the 4 confirmed pages. Across all history this path accounts for **45% of every LLM call ever made** (231/516).
- **But it caught things.** 3 of the 7 `CHANGE_REQUIRED` verdicts ever produced (`X2A3EB` on 7 May; `XD2102`, `X3B183` on 18 June) came from side-door pages the cross-encoder had rejected. The leak is functioning as a crude recall net over a threshold that passes 98.6% noise anyway (TW-14). Conversion rates are near-identical on both paths (~1.3–1.4%), i.e. the graph widens recall without adding precision.

Net assessment: the graph as implemented degrades the *filtration* function (the funnel leaks around its final gate, doubling LLM volume with correlated evidence) while accidentally providing recall the cross-encoder threshold should be providing. Fix the gate and the threshold together (TW-34 + TW-14), don't just delete the graph.

**Question 2: are same-site internal links not captured, despite being the higher-quality signal?**

Correct on both counts. Internal-link edges are unimplemented at every level (config disabled, `graph.py` warning-only, plan task 5.5 open), and the raw material is destroyed before storage — both scrapers extract plain text without link preservation, so the corpus snapshots contain no hrefs. Meanwhile 73% of existing edges are embedding-similarity edges, which are circular as a propagation signal: they re-encode the same semantic-similarity measurement Stages 4–6 already made. Editorial links are directed, sparse, human-curated, and orthogonal to the encoders — precisely the "if this page changes, its linked siblings may need review" relation the quasi-graph was designed to model. TW-36 covers implementation; note it requires an ingestion-side change (link capture at scrape time), not just a graph-builder change.

### §4.2 — Second-pass contract sweep (2 July 2026)

The graph side-door was found by cross-validating live data against code rather than reading code alone. A follow-up sweep applied the same method to every remaining cross-component boundary (pipeline→DB→dashboard, stage→stage payloads, alert text→actual behaviour) and to the modules the first pass had only skimmed (`health.py` internals, `observability.py`, the FRL explainer chain, RSS Stage-3). New confirmed findings, all empirically verified:

1. **Dashboard/pipeline `details` schema break (TW-38).** 12+ JSON paths queried by the dashboard exist in 0 of 9,752 `pipeline_runs` rows. Every verdict, confidence, reasoning and per-page score surfaced through the runs/pages/graph/health routes is null or zero, and always has been. The dashboard was built against an imagined schema; only `llm-reports.js` reads the real table.
2. **FRL fallback dismissal (TW-39).** All 4 historical "compilation changed, no ES retrievable" placeholders triggered 5 IPFR pages each and were adjudicated `NO_CHANGE` — the promised manual-review routing does not exist. (The 25 June Australian Border Force Act compilation change was dismissed this way.)
3. **RSS channel non-functional (TW-10, urgency raised 4→7).** 65 of 67 RSS diffs ever produced are empty; only one of five feeds has ever yielded an item.
4. **Health alert text contradicts behaviour (folded into TW-03).** The malformed-LLM alert tells the operator bundles were deferred for retry; they are dropped.
5. Minor, noted for TW-14/TW-16: unified-diff markup (`+`/`-`/`@@`/header lines) is passed unstripped into YAKE, BM25 and the encoders, adding token noise to every webpage-source score.

Checked and found sound in the same sweep: snapshot rotation logic; run-ID uniqueness (no collisions in 70 runs); the frequency gate (probe volume matches registry cadences — ~32 of 156 sources scraped daily); health streak queries (run rows are logged before evaluation); feedback IMAP parsing against the mailto format; observability report generation (code is fine — it is simply never scheduled, TW-20).

---

## 5. Suggested first fortnight

1. **TW-01** (hours) — until the emails land somewhere real, everything else is academic.
2. **TW-11** (half-day) — green, enforced tests protect all subsequent fixes.
3. **TW-09 → TW-04 → TW-06** — restore scraping coverage; the three interact, so sequence them.
4. **TW-02, TW-05, TW-03** — close the remaining fail-closed gaps.
5. **TW-13** (new-page discovery) and **TW-17 short-term pruning** — stop the silent corpus gap and the release pile-up.
6. Start **TW-14** as a background analysis task; it shapes most later tuning work.
