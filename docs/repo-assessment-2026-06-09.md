# Tripwire Repository Assessment — 9 June 2026

An in-depth review of the repository covering the core pipeline (`src/`), the
ingestion layer (`ingestion/`), tests, CI/CD, repo hygiene, the dashboard
(`dashboard/`), configuration, and documentation. Findings are consolidated and
prioritised. Items already tracked in `Backlog.md` are cross-referenced rather
than repeated; several Backlog P0 items were found to be **already fixed** in
the current code (see §8).

**Severity legend:** 🔴 High — fix soon; 🟡 Medium — schedule; 🟢 Low — polish.

---

## 1. Critical: process and infrastructure gaps

These are the highest-leverage improvements in the repo.

### 1.1 🔴 No CI runs the test suite
The repo has an ~8,500-line pytest suite (18 files), but none of the four
workflows in `.github/workflows/` ever runs `pytest`. There is no workflow
triggered on `push` or `pull_request` at all — code can land on `main` without
a single test executing.
**Fix:** add `.github/workflows/test.yml` running `pytest tests/ -v` (with the
CPU-only torch index per `CLAUDE.md`) on every push and PR. Add `pytest-cov`
for a coverage signal.

### 1.2 🔴 36 MB SQLite database committed daily is bloating git history
`ipfr_ingestion.yml` and `tripwire.yml` each commit `data/ipfr_corpus/ipfr.sqlite`
(36 MB) back to the repo daily. `.git` is already 66 MB and grows with every
run; clone times will degrade steadily and GitHub's repo-size limits will
eventually bite. `data/influencer_sources/snapshots/` (7 MB) and
`data/LLM Reports/` (1.2 MB, 570 JSON files) compound the problem.
**Fix options (in rough order of preference):**
- Publish the DB and snapshots as GitHub Release assets only (the
  `publish-dashboard-data-release.yml` machinery already exists and the
  dashboard already consumes release assets via `syncData.js`) and stop
  committing them to git; cache between runs with `actions/cache`.
- Or move `ipfr.sqlite` to Git LFS.
- Periodically squash/garbage-collect history once the daily commits stop.

### 1.3 🔴 No linting, formatting, or type-checking
There is no `ruff`/`black`/`mypy`/`pre-commit` configuration anywhere, and no
`pyproject.toml`. For a ~14,000-line Python codebase this is the cheapest
quality win available.
**Fix:** add `pyproject.toml` with `[tool.ruff]` (lint + format) and pytest
config; add a `.pre-commit-config.yaml`; run ruff in the new CI workflow.
`mypy` can follow once type hints are tightened (see §5.4).

### 1.4 🔴 Unpinned dependencies, no lockfile, no update automation
Both `requirements.txt` and `requirements-ingestion.txt` use only `>=`
constraints (e.g. `sentence-transformers>=3.0`, `openai>=1.30`). A transitive
release can silently break the nightly pipeline — exactly the failure mode
that already forced the ad-hoc `blinker>=1.4,<1.7` pin.
**Fix:** generate pinned lockfiles with `pip-compile` (pip-tools) and install
from those in CI; add `.github/dependabot.yml` for weekly update PRs (pip +
npm + github-actions ecosystems).

### 1.5 🟡 Race between workflows committing state back to the repo
`tripwire.yml`, `ipfr_ingestion.yml`, and `feedback_ingestion.yml` all commit
and push to `main`. Concurrency groups exist per-workflow, but not across
workflows, so two different workflows can race on push. `tripwire.yml`
swallows push failures with `|| true` (state silently lost); the ingestion
workflow has no fallback at all (job fails on conflict).
**Fix:** a shared concurrency group across the three state-writing workflows,
plus a pull-rebase-push retry loop instead of `|| true`. (Largely moot if 1.2
removes the commit-back pattern.)

---

## 2. Correctness risks — core pipeline (`src/`)

### 2.1 🔴 SQLite connection and run-log not protected by `try/finally`
`src/pipeline.py` closes its connection at three separate exit points
(`pipeline.py:196`, `:309`, `:365`). Any unhandled exception between open and
close leaks the connection **and skips `_log_run_entries()` and health
alerting entirely**, so the runs most in need of a health alert (crashes) are
the ones that never produce one.
**Fix:** wrap the body of `_run_pipeline()` in `try/finally` (close + best-effort
run-log flush in `finally`), or a context manager.

### 2.2 🔴 No `busy_timeout` on SQLite connections
The pipeline opens `ipfr.sqlite` without a busy timeout. If ingestion overruns
its 01:00 UTC window into the 02:00 pipeline run (the 60-minute ingestion
timeout makes this possible), the pipeline can fail immediately with
`database is locked`.
**Fix:** `PRAGMA busy_timeout = 30000` after connect, in both `src/` and
`ingestion/db.py`.

### 2.3 🟡 Prompt injection from scraped content into Stage 8
`src/stage8_llm.py:434-438` embeds scraped `diff_text` into the LLM prompt
inside a bare code fence. A monitored page (or a compromised one) could embed
"ignore previous instructions…" text. Fail-closed design limits blast radius
(worst case is a wrong verdict/notification text), but hardening is cheap.
**Fix:** delimit untrusted content explicitly (e.g. `<change_document>` tags),
strip/escape backtick fences inside `diff_text` so the fence can't be broken
out of, and add a system-prompt instruction that the delimited block is data,
never instructions.

### 2.4 🟡 Failed-twice LLM bundles are dropped instead of deferred
In `assess_bundles` (`src/stage8_llm.py`), a bundle whose response fails
schema validation after the retry is logged and skipped — it is **not**
written to `deferred_triggers`, so the signal is lost, violating the
fail-closed principle. (Deferral currently happens only for API failures.)
**Fix:** defer on persistent schema-validation failure too, with a
`failure_reason` column to distinguish the cases.

### 2.5 🟡 Email-address handling in Stage 9
`src/stage9_notification.py:170` reads `content_owner_email` straight from
config with no validation, and the module falls back to a hardcoded
`tripwire-feedback@gmail.com` when `FEEDBACK_EMAIL` is unset. Header injection
risk is low (config is repo-controlled), but a typo or unset env var fails
silently into the wrong mailbox.
**Fix:** validate addresses with `email.utils.parseaddr` at config-load time,
reject control characters and `*@example.*` placeholders, and make
`FEEDBACK_EMAIL` required rather than defaulted.

### 2.6 🟡 Selenium fallback swallows all exceptions
`src/scraper.py` (`_fetch_with_selenium`) catches bare `Exception` and returns
`None`, flattening timeouts, WebDriver crashes, and genuine page errors into
one indistinguishable outcome.
**Fix:** catch specific WebDriver/timeout exceptions, classify into
`RetryableError`/`PermanentError`, and log the exception type.

### 2.7 🟡 Unbounded SQL `IN (...)` placeholder lists
`src/pipeline.py:828-841` (`_load_page_meta`) builds `",".join("?" * len(ids))`.
Fine today, but it breaks at SQLite's bound-parameter limit if the candidate
set grows.
**Fix:** chunk ID lists into batches of ~500.

### 2.8 🟢 Smaller items
- `_log_run_entries` uses `executemany` without explicit transaction
  boundaries — wrap in one transaction.
- `git config user.name/email` in `pipeline.py:957-959` should use `--local`.
- CAPTCHA/block-page signature lists are duplicated between
  `src/validation.py` and `src/scraper.py` — extract to one module.
- Malformed JSON in a `deferred_triggers` row will crash deferred-trigger
  loading — wrap `json.loads` and quarantine bad rows.
- Network timeouts are hardcoded in several modules
  (`stage1_metadata.py`, `scraper.py`) — move to a `network:` config section.
- SMTP retry backoff has no jitter.

---

## 3. Correctness risks — ingestion layer (`ingestion/`)

### 3.1 🔴 Failed embeddings stored as empty bytes are silently skipped
`ingestion/enrich.py:501` stores `bytes(0)` as a placeholder when embedding
fails; `src/stage5_biencoder.py:303` then skips falsy blobs **without any
warning**. A transient model failure during ingestion silently degrades
retrieval quality for that page indefinitely.
**Fix:** add an `embedding_status` column (or at minimum log a warning and
count skipped chunks per run in `pipeline_runs`); have ingestion re-attempt
failed embeddings on the next run.

### 3.2 🔴 Duplicate-chain resolution can loop forever
`ingestion/dedup.py:175-176` walks `duplicate_of` chains with no cycle guard;
a cycle (A→B→A) hangs the nightly ingestion job until the workflow timeout.
**Fix:** track visited IDs, break and log a warning on a cycle.

### 3.3 🟡 Graph rebuild is O(n²) with per-edge INSERTs
`ingestion/graph.py` compares all page pairs for entity overlap and upserts
edges one at a time, fetching entities per page (N+1). Acceptable at the
current corpus size, but it will dominate ingestion time as the corpus grows.
**Fix:** load all entity sets in one query, batch edge writes with
`executemany`, and consider an inverted index (entity → pages) to skip pairs
with no shared entities.

### 3.4 🟡 Partial graph state committed on Phase 6 failure
`ingestion/ingest.py:196-203` catches graph-rebuild exceptions, logs, and then
commits unconditionally — leaving a half-rebuilt edge table.
**Fix:** run the rebuild in a transaction and roll back on failure.

### 3.5 🟡 Embedding blobs carry no model/dimension metadata
`pages.doc_embedding` and chunk embeddings are bare BLOBs. Changing the
embedding model would silently mix incompatible vectors.
**Fix:** store `embedding_model` and `dim` (per-table or in a metadata table)
and validate at load time.

### 3.6 🟢 Smaller items
- No index on `pages.url` despite URL-keyed merges in `sitemap.py`.
- Empty `page_id` falls through to `unknown.md` snapshots — validate early
  and raise `PermanentError`.
- NER truncates input at 100k chars with no log line.
- WAL checkpoint hygiene: run `PRAGMA wal_checkpoint(TRUNCATE)` before the
  workflow commits the DB file, so stale `-wal` content can't be lost.
- Keyphrase deletion in `dedup.py` is case-sensitive; canonicalise
  (lowercase) keyphrases at insert time.

---

## 4. Testing

### 4.1 🔴 Large coverage gaps in exactly the riskiest modules
Modules with **no dedicated test file**: `src/pipeline.py` (orchestrator),
`src/config.py`, `src/health.py`, `src/observability.py`,
`src/feedback_ingestion.py`, `src/validation.py`, and on the ingestion side
`ingest.py`, `graph.py`. (Several stages are covered indirectly — e.g.
`test_llm_assessment.py`, `test_notification.py`, `test_relevance_scoring.py` —
but the orchestrator and config validation have none.)
**Fix priority:** `pipeline.py` (observation-mode gating, deferred-trigger
re-processing, early-exit paths from §2.1) and `config.py` (validation
rejects/accepts) first.

### 4.2 🟡 No `tests/conftest.py`
DB/config/source fixtures are re-created per file (e.g.
`tests/test_llm_assessment.py`). Centralise into shared fixtures.

### 4.3 🟢 No pytest config
Add `[tool.pytest.ini_options]` (testpaths, addopts, timeout) to the new
`pyproject.toml`; add `pytest --cov` reporting in CI.

### 4.4 🟡 Dashboard is effectively untested
~157 lines of tests for the whole React app, zero tests for the Express
server (auth, routes, sync). Also `dashboard/vite.config.js` sets
`test.environment: 'node'`, which will break any DOM-dependent component
test — should be `jsdom`/`happy-dom`.

---

## 5. Maintainability

### 5.1 🟡 `src/stage3_diff.py` is 1,590 lines
It mixes webpage, FRL, ParlInfo, and RSS diff logic. Split into per-source-type
modules behind a dispatcher; this also makes the largest test file
(`test_stage3_diff.py`, 1,459 lines) decomposable.

### 5.2 🟡 `src/pipeline.py` is 1,218 lines
Stage orchestration, git commit-back, GitHub summary writing, and report
saving all live together. Extract the git/persistence helpers.

### 5.3 🟢 `docs/Reference-Code/` contains dead code with download-artifact names
Files like `fetch_em_summary (1).py`, `check_sitemap_patch (1).py` (~144 KB).
Delete, or move to `docs/archive/` with a README explaining provenance.

### 5.4 🟢 Type-hint gaps
Model objects passed as `Any` in stages 5/6; add type aliases or
`TYPE_CHECKING` imports so mypy becomes adoptable later.

### 5.5 🟢 Root-level clutter
`Backlog.md`, `Dashboard-Lookbook.html`, `tripwire_dashboard_brief_v3_0.md`,
`260406_d_Tripwire_System_Plan.md` (filename is hard to discover),
`.nojekyll`, `render.yaml`. Move docs into `docs/` and reference them from the
README. (Keep `260406_d_…` where CLAUDE.md says it is, or update CLAUDE.md in
the same change.)

---

## 6. Dashboard (server + client)

### 6.1 🔴 `sourceId` path handling in the snapshot route
`dashboard/server/routes/snapshots.js` joins the request parameter into a
filesystem path. `path.join` does not neutralise `..` segments, so a crafted
`sourceId` can escape `SNAPSHOTS_PATH`. Auth mitigates exposure, but this is a
one-line fix: validate `sourceId` against `^[A-Za-z0-9_-]+$` (or against the
source registry) and 404 otherwise. Same for any other ID-parameterised
file-serving route.

### 6.2 🟡 `execSync` tar extraction in `syncData.js`
`dashboard/server/syncData.js:159` shells out to `tar xzf` on a downloaded
release asset. The variables are server-controlled, but member paths inside
the tarball are not validated (classic tar-slip).
**Fix:** use the `tar` npm package with `strip` + path filtering instead of
`execSync`.

### 6.3 🟡 LLM reports and snapshots published as release assets
`publish-dashboard-data-release.yml` publishes the full LLM assessment JSONs
and source snapshots. If the GitHub repo is (or ever becomes) public, release
assets are public regardless of dashboard auth.
**Fix:** confirm the repo's visibility intent and document it; if public
visibility is ever needed, move dashboard data distribution to private
storage.

### 6.4 🟡 Dev-mode auth and CORS are wide open
`dashboard/server/auth.js` skips auth outside production when creds are
unset, and `index.js` uses `cors({ origin: true })` in development. Default
dev CORS to the Vite origin and log loudly when auth is disabled.

### 6.5 🟡 `/api/config` POST validation is shallow
`routes/config.js` checks top-level key presence only — a bad save can break
the next pipeline run. Mirror `src/config.py` validation rules (types,
ranges) server-side, and validate query params (`limit`, `offset`, dates) in
the read routes.

### 6.6 🟢 `render.yaml` uses `npm install`
Use `npm ci` for deterministic deploys; add a health-check path.

---

## 7. Documentation and configuration

### 7.1 🔴 CLAUDE.md schema/table documentation is stale (verified)
The live database tables are: `pages`, `chunks`, `entities`, `keyphrases`,
`graph_edges`, `sections`, `pipeline_runs`, `deferred_triggers`,
`ingestion_runs`, `llm_assessments`. CLAUDE.md documents 8 tables and calls
the chunk table `page_chunks` (this is Backlog BUG-018, still unfixed).
CLAUDE.md also doesn't mention the dashboard at all — a contributor reading
the onboarding doc won't know a Node/Express/React/Vite app ships in the same
repo, nor that `render.yaml` and `publish-dashboard-data-release.yml` exist.
**Fix:** update the schema table, add a Dashboard section (stack, layout,
`npm run dev`, deployment, data flow), and list the dashboard env vars
(`DASHBOARD_USER`, `DASHBOARD_PASS`, `DATA_ROOT`).

### 7.2 🟡 Placeholder emails in committed config
`tripwire_config.yaml` ships `content-owner@example.gov.au` /
`admin@example.gov.au`. Pair with §2.5: reject `example.*` addresses at
config-validation time so a fresh deployment fails loudly instead of mailing
nowhere.

### 7.3 🟢 Magic numbers in `tripwire_config.yaml`
Thresholds like `rrf_k: 60`, `propagation_threshold: 0.05` deserve one-line
comments referencing the relevant System Plan section, especially with
threshold calibration (deferred task 5.3) coming.

### 7.4 🟢 `source_registry.csv` schema is undocumented
Document required columns, valid `source_type` values, and validation rules
in `docs/` (e.g. a `runbook-add-source.md`), since the CSV is the only way to
add a source (Backlog BUG-013).

### 7.5 🟢 Note on `observation_mode: false`
Flagged by review as risky-by-default, but the system is live (355+ runs), so
the current value is intentional. No action beyond documenting in the config
comment that new deployments should start with `true`.

---

## 8. Backlog.md reconciliation (verified)

`Backlog.md` (BUG-001 … BUG-019 plus P1–P3 features) is the dashboard issue
tracker, but it has drifted from the code:

- **Already fixed in code, still listed as open:** BUG-001 (snapshots.tar.gz
  is now published and synced — `publish-dashboard-data-release.yml:74`,
  `syncData.js:136`), BUG-002 (Topbar now reads `health?.data?.last_run` —
  `Topbar.jsx:95`), BUG-003 (config path now correctly points at repo root —
  `db.js:10`).
- **Still open:** BUG-018 (CLAUDE.md `page_chunks` naming — see §7.1) and,
  unverified in this pass, most of BUG-004…BUG-017/019.

**Fix:** sweep Backlog.md against the current code, mark fixed items with the
fixing commit, and decide whether the backlog lives in this file or in GitHub
Issues (currently neither is authoritative).

---

## 9. Suggested execution order

| # | Item | Sections | Effort |
|---|------|----------|--------|
| 1 | CI workflow: pytest + ruff on push/PR | 1.1, 1.3 | Small |
| 2 | `try/finally` around pipeline connection + run-log flush | 2.1 | Small |
| 3 | `busy_timeout` pragmas (both layers) | 2.2 | Trivial |
| 4 | Dedup cycle guard; embedding-failure visibility | 3.2, 3.1 | Small |
| 5 | `sourceId` validation + tar-package extraction in dashboard server | 6.1, 6.2 | Small |
| 6 | Defer (don't drop) twice-failed LLM bundles | 2.4 | Small |
| 7 | Stop committing `ipfr.sqlite`/snapshots to git; release-asset or LFS strategy | 1.2, 1.5 | Medium |
| 8 | Pin dependencies (pip-tools) + dependabot | 1.4 | Small |
| 9 | Update CLAUDE.md (schema, dashboard section); reconcile Backlog.md | 7.1, 8 | Small |
| 10 | Tests for `pipeline.py` + `config.py`; `conftest.py` | 4.1, 4.2 | Medium |
| 11 | Prompt-injection hardening in Stage 8 | 2.3 | Small |
| 12 | Split `stage3_diff.py`; extract pipeline git helpers | 5.1, 5.2 | Medium |
| 13 | Remaining 🟢 items opportunistically | — | Ongoing |

---

*Generated from a four-track review (core pipeline, ingestion, tests/CI/hygiene,
dashboard/docs/config) with manual verification of all high-severity claims.
Line numbers reference the tree at commit `2154e5c`.*
