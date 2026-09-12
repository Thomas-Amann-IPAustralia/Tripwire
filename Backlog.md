# Tripwire Backlog

A living, prioritised punch-list for the Tripwire dashboard and supporting
pipeline, derived from an end-to-end audit of `dashboard/`, the system plan
(`260406_d_Tripwire_System_Plan.md`), the dashboard brief
(`tripwire_dashboard_brief_v3_0.md`), and the runtime data layout under
`data/`.

The list is organised by priority band, then by theme. Each item carries a
**stable ID** (e.g. `BUG-007`, `FEAT-014`) so future iterations can reference
it from commits, PR titles, and issue trackers. Where possible the entry
points to the file and line where the work needs to happen.

---

## Priority bands

| Band | Meaning | Action stance |
|---|---|---|
| **P0 — Broken** | A feature that is shipped to users but does not function correctly. The user sees the affordance, clicks/uses it, and gets the wrong result (or nothing). | Fix before doing anything else. These are credibility-eroding. |
| **P1 — Misleading or incomplete** | Data is rendered but is wrong, stale, or hides important context. Or a workflow is missing an obvious step that forces users into the CSV/YAML. | Schedule into the next 1–2 iterations. |
| **P2 — Quality, polish, transparency** | Real improvements to insight, navigation, and trust — not a bug fix, but the difference between "dashboard exists" and "team relies on it". | Pick from this band when P0/P1 work is quiet. |
| **P3 — Speculative / blocked** | Larger ideas, or work waiting on accumulated production data (per the plan's §5 "deferred tasks"). | Park until pre-conditions are met. |

> **Reading note:** every item ends with a one-line **Definition of Done** so
> the work is small enough to ship in a single PR.

---

## P0 — Broken

These items are user-visible failures of features that already appear to
"exist" in the UI. They should be the next thing anyone touches.

### BUG-001 — `VIEW SNAPSHOT` opens an empty overlay (the reported bug)

The "view snapshot" affordance in the Sources expanded row appears to do
nothing because **three independent failures stack on top of each other**:

1. **In production, the snapshot files are never delivered to the server.**
   `dashboard/server/syncData.js:12-17` only downloads `ipfr.sqlite`,
   `tripwire_config.yaml`, `source_registry.csv` and `feedback.jsonl` from
   the GitHub Release. The 150+ per-source snapshot directories under
   `data/influencer_sources/snapshots/<source_id>/<source_id>.txt` are not
   in `ASSETS`, so on Render they simply do not exist. The snapshot route
   then reads nothing from disk and returns an envelope full of `null`s.
2. **The "previous snapshot" path is wrong.** `dashboard/server/routes/snapshots.js:40`
   looks for `<source_id>.v1.txt`, which Stage 3 only writes when a
   *webpage* source has actually changed (`src/stage3_diff.py:240-244`). For
   sources that have not yet changed (the majority on a young deployment),
   the previous version lives in `state.json["previous_text"]` instead, so
   `previous_snapshot_text` is always `null` and the diff is empty.
3. **The button can still appear "enabled" with no usable content.** In
   `dashboard/src/sections/Sources.jsx:175` the disabled check is
   `!snapshotData`, but `snapshotData` is the response *envelope*
   (`{data: {…}}`), which is truthy even when every inner field is `null`.
   So the button reads `VIEW SNAPSHOT ↗`, the overlay opens, and the user
   sees no snapshot text, no previous version, no diff, and no matching
   IPFR chunks.

**Definition of done:** (a) `syncData.js` packages and downloads a
`snapshots.tar.gz` (or rsync from the pipeline workflow per
`dashboard/DEPLOY.md` §3.2); (b) `snapshots.js` falls back to
`state.json["previous_text"]` when `<source_id>.v1.txt` is missing;
(c) the button is disabled when the resolved `snapshot_text` is null and
shows `NO SNAPSHOT YET` rather than letting the user click into an empty
modal.

### BUG-002 — `Topbar` status pill, stage dots, and "last run" are always blank

`dashboard/src/components/Topbar.jsx:95-97` reads `health?.last_run_at`,
`health?.pipeline_status`, and `health?.stages_completed`. None of those
keys exist on the response from `/api/health/summary`, which actually
returns `{ data: { last_run: { timestamp, outcome, ... }, error_rate_30d,
... } }` (`dashboard/server/routes/health.js:65-74`). Net effect: the pill
permanently reads `IDLE`, no stage dots ever light, and the relative time
is hidden. The most prominent piece of state in the entire dashboard is
silently fake.

**Definition of done:** unwrap `health?.data`, read `last_run.timestamp`,
derive `pipeline_status` from `last_run.outcome` (or from a per-run join
against `pipeline_runs.outcome` aggregated over `run_id`), and surface a
real `stages_completed` value (max `stage_reached` across the latest
`run_id`).

### BUG-003 — `ADJUST` cannot read or save the live config

Two compounding issues mean Adjust is essentially read-only and reading
the wrong thing:

1. **Wrong path.** `dashboard/server/db.js:10` resolves
   `CONFIG_PATH = path.join(REPO_ROOT, 'data', 'tripwire_config.yaml')`,
   but the file lives at `tripwire_config.yaml` in the repo root. The
   dashboard brief itself spells out the correct path
   (`tripwire_dashboard_brief_v3_0.md:134`). `GET /api/config` therefore
   returns `{}` and every Adjust control renders against `undefined`.
2. **Validation rejects the real schema.** `dashboard/server/routes/config.js:8`
   demands top-level keys `relevance`, `biencoder`, `crossencoder`, but the
   committed config uses `relevance_scoring` and the nested
   `semantic_scoring.biencoder` / `semantic_scoring.crossencoder` (see
   `tripwire_config.yaml:34-59`). Even after the path fix, `POST /api/config`
   will 400 on every save.

**Definition of done:** point `CONFIG_PATH` at the repo root file, replace
`REQUIRED_FIELDS` with the actual schema groups (`pipeline`,
`change_detection`, `relevance_scoring`, `semantic_scoring`, `graph`,
`storage`, `notifications`), and round-trip a `GET → edit → POST → GET`
locally with the test config to prove it sticks.

### BUG-004 — Global FilterBar doesn't filter

`dashboard/src/hooks/useData.js:19-30` constructs query parameters named
`sources`, `stageMin`, `verdicts`, and `datePreset`. The Express handler
in `dashboard/server/routes/runs.js:86` reads `source_id`, `stage_reached_min`,
`verdict` (singular) and `from`/`to` (no preset). Result: every chip in
the FilterBar is a no-op. The 30-day default that the user sees is
*coincidental* — there's no `from` cutoff so the API just returns the
last 1000 rows, which on a fresh install happens to look about right.

**Definition of done:** in `filtersToSearch`, (a) translate `datePreset`
into concrete `from`/`to` ISO timestamps; (b) rename `sources` →
`source_id` (sending repeated values, and updating `runs.js` to handle
`IN (?)`); (c) rename `stageMin` → `stage_reached_min`; (d) handle
multi-`verdict` values via `IN (?)`. Add an integration test that asserts
a filter change actually changes the SQL params.

### BUG-005 — `CalendarHeatmap` and `TimelineSwimLane` always show empty

Both charts key on `run.run_at`, but the runs API returns `timestamp`
(`dashboard/server/routes/runs.js:27`). The same bug is baked into
`dashboard/src/lib/dataUtils.js:41` (`aggregateByDay`). Net effect: the
calendar is a flat grey grid, the swim-lane is a blank canvas, and the
"alerts per day" sparkline never lights.

References: `dashboard/src/visualisations/CalendarHeatmap.jsx:29`,
`dashboard/src/visualisations/TimelineSwimLane.jsx:38,79,283,296,441`.

**Definition of done:** read `run.timestamp ?? run.run_at` in all four
visualisations and `aggregateByDay`. Add a snapshot test that confirms
non-zero day counts when fed real `pipeline_runs` rows.

### BUG-006 — Pipeline funnel always shows 0 for Stages 7, 8, 9

The Python pipeline writes `stage_reached` values of `stage1`, `scrape`,
`stage2`, `stage3`, `stage4`, `stage5`, `stage6`, `stage6_complete`
(`src/pipeline.py:417,467,491,519,549,589,631,670`) — and *nothing
beyond*. Stages 7 (Aggregation), 8 (LLM) and 9 (Notification) operate on
*bundles*, not per-source rows. The dashboard's `STAGE_REACHED_CASE`
(`dashboard/server/routes/runs.js:7-19`) doesn't even list the strings
`stage7/8/9` because they don't exist. Yet the FilterBar offers S1–S9
chips, the Funnel always renders nine bars, and the rightmost three are
permanently empty.

**Definition of done:** decide and document the contract — either (a) the
funnel renders Stages 1–6 from per-source rows and Stages 7–9 from a
separate run-level aggregation (e.g. count `pipeline_runs` whose `details`
JSON contains `stages.aggregation`, `stages.llm_assessment`, and
`stages.notification` payloads), or (b) collapse the UI to show only the
six stages that are actually instrumented and label 7–9 as "run-level
stages — see Health". The brief expects (a).

### BUG-007 — `dbGuard` returns HTTP 200 on database failure

`dashboard/server/db.js:38-44` responds with `200 { data: [], error: 'database_not_found' }`
when the SQLite file cannot be opened. React Query treats this as a
successful response, so every list silently renders as empty rather than
surfacing an error. This makes a misconfigured `DATA_ROOT` indistinguishable
from a freshly-installed system with no data.

**Definition of done:** return `503` with the same payload, and surface
`error.message` in a single `<ErrorBanner>` component at the top of every
section.

### BUG-008 — Snapshot diff is set-based, not a real diff

`dashboard/server/routes/snapshots.js:8-24` builds added/removed line
*sets*, not an LCS-based diff. As a result, repeated lines (legal
preambles, navigation chrome, table rows) are silently collapsed and the
diff highlights misalign with reality. The pipeline already produces a
real unified diff per change (`data/influencer_sources/snapshots/<src>/<src>_<run>.diff`)
— the dashboard should serve that file rather than recomputing a worse
version.

**Definition of done:** if a `<src>_*.diff` file exists for the latest
run, parse and serve it; otherwise fall back to `diff-match-patch` (already
the de facto Node baseline) so we get word-level diffs instead of set
membership.

---

## P1 — Misleading or incomplete

Things that *appear* to work but show wrong data, hide important context,
or force the user to drop into the CSV/YAML to do their job.

### BUG-009 — "Changed" column in Health Run Log is mis-labelled

`dashboard/src/sections/Health.jsx:487` displays `r.sources_completed`
under the column header `Changed`. `sources_completed` actually counts
sources whose `outcome = 'completed'` — i.e. that progressed through
Stage 6 and would have triggered an LLM call. A "changed" source is one
where Stage 2 detected change. Two completely different concepts with
very different counts.

**Definition of done:** add `sources_changed` to the `/api/health/runs`
aggregation (count of rows whose `details.stages.change_detection.changed = true`)
and rename the column appropriately. Or, if "completed" is the right
metric, rename the column in the UI.

### BUG-010 — Expanded Health row hides per-source detail

`dashboard/src/sections/Health.jsx:409-411` shows literal `—` placeholders
in the Changed/Errored/Alerts columns of the expanded sub-table. The data
is in `pipeline_runs.details` already; it's just not extracted server-side
or rendered.

**Definition of done:** populate those columns with `details.stages.change_detection.changed`,
`outcome === 'error'`, and `verdict === 'CHANGE_REQUIRED'` respectively.

### BUG-011 — Adjust Sections reference config keys that the file uses but the validator forbids

The Adjust accordion has correct keys (`relevance_scoring.rrf_k`, etc.),
but `dashboard/server/routes/config.js:8` uses a different schema
contract. Even after BUG-003 is fixed, every save will require touching
both files in lock-step. Centralise the schema definition (one JSON Schema
or Zod schema) and import it in both places.

**Definition of done:** create `dashboard/server/configSchema.js`,
import in `routes/config.js` for validation, and emit a `/api/config/schema`
endpoint that the Adjust UI can consume to render controls dynamically
(eliminating the parallel definition in `Adjust.jsx`).

### BUG-012 — Topbar version label is hardcoded

`Topbar.jsx:127` reads `v2.0 · TW-DASHBOARD` as a literal. There is no
single version of truth that gets bumped by builds.

**Definition of done:** read from `dashboard/package.json` at build time
via a Vite `define` (`__APP_VERSION__`) and render that.

### BUG-013 — Source registry CSV is the only place to edit / delete a source

`dashboard/server/routes/sources.js` exposes only `GET` and `POST`. There
is no `DELETE`, no row-level `PUT`. The Sources view has only `+ ADD
SOURCE`. To disable a source, the user must SSH into Render or open the
CSV in Git.

**Definition of done:** add `DELETE /api/sources/:source_id`, an `enabled`
column on each row (true/false), and an inline toggle/eyebrow menu in the
Sources table.

### BUG-014 — No way to trigger a re-check from the UI

The pipeline accepts `--check-frequency all` (`CLAUDE.md` "Commands"
section) and a `workflow_dispatch` trigger on `tripwire.yml`. The
dashboard never wires this up. Operators have to leave the dashboard, go
to GitHub Actions, and click "Run workflow".

**Definition of done:** in the Sources expanded row, add a `RE-CHECK NOW`
button that POSTs to a new endpoint (`POST /api/sources/:id/recheck`)
which calls the GitHub REST API to dispatch the workflow with that source
ID as an input. Requires a `GITHUB_TOKEN` server-side secret.

### BUG-015 — Add Source form does no de-duplication or URL validation

`dashboard/src/sections/Sources.jsx:214-220` only validates non-empty
fields. Submitting an existing `source_id` silently overwrites the row
(`routes/sources.js:147`), and a malformed URL is accepted without
checking that it's reachable.

**Definition of done:** server-side, return `409 Conflict` on duplicate
`source_id` unless an `?overwrite=true` query param is set; client-side,
warn before overwriting and HEAD-probe the URL to surface obvious 404s
before saving.

### BUG-016 — Feedback ingestion link in EventDrawer has no acknowledgement of state

The four feedback chips (`EventDrawer.jsx:302-378`) `POST` to
`/api/feedback/submit`, which appends a JSON line to `feedback.jsonl`.
That file is owned by GitHub Actions (it's git-committed by
`feedback_ingestion.yml`). Two issues: (a) writes from the dashboard are
not committed back to the repo, so on the next `rsync` they will be lost;
(b) the user has no way to see prior feedback for the same run, so they
can submit conflicting categories without warning.

**Definition of done:** either persist feedback through the same git
mechanism (commit + push from server) or treat dashboard feedback as
write-through to a separate `dashboard_feedback.jsonl` that gets merged in
the next `feedback_ingestion.yml` run. Surface prior feedback inline.

### BUG-017 — Doc drift: brief says `/api/sources/:id/snapshot`, code uses `/api/snapshots/:id`

`tripwire_dashboard_brief_v3_0.md:474` documents one URL; the actual route
is mounted at `dashboard/server/index.js:37` and serves a different shape.
Pick one and update the other.

**Definition of done:** update the brief to match the implementation (the
implementation is reasonable as-is) and add a one-line "API contract"
section to `dashboard/DEPLOY.md` so future readers don't go to the brief
first.

### BUG-018 — `CLAUDE.md` says the chunk table is `page_chunks`; the table is `chunks`

`CLAUDE.md` SQLite Schema table lists `page_chunks`, but
`ingestion/db.py:53` creates `CREATE TABLE chunks`, and every dashboard
query hits `FROM chunks`. Doc drift.

**Definition of done:** correct `CLAUDE.md`. (Don't rename the table —
it's reasonable, and migrating live data is not worth it.)

### BUG-019 — `last_changed` aggregation uses the wrong predicate

`dashboard/server/routes/sources.js:50` computes `last_changed` as
`MAX(timestamp WHERE outcome='completed')`. "Completed" means the source
was processed past Stage 6, not that the source actually changed. A user
glancing at "Last Changed" sees a misleading recency.

**Definition of done:** redefine the SQL as `MAX(timestamp WHERE
json_extract(details,'$.stages.change_detection.changed')=1)` and rename
the response key for clarity.

---

## P2 — Quality, polish, transparency

The list of things that turn the dashboard from "it shows numbers" into
"the team trusts and uses it". Order within this band is suggestive —
pick whichever the team finds most painful.

### Insight & transparency

#### FEAT-001 — Single-run diagnostic replay

This is the highest-leverage missing feature. Pick any `run_id` and step
through what each stage *did*, with the actual artefacts: scraped HTML
size, hash diff, significance flags, YAKE keyphrases, BM25 ranks, RRF
scores, biencoder candidate list with chunk hits, crossencoder rankings,
graph propagation hops, the **actual LLM prompt that was sent**, the raw
LLM reply, the parsed verdict, and the final email body. Today you have
to read `pipeline_runs.details` JSON in a SQL client.

**DoD:** new `/document` sub-route `/replay/:runId` with a vertical
stepper UI; collapsible per-stage panels; render the LLM prompt with
syntax highlighting; show token / cost telemetry inline.

#### FEAT-002 — LLM cost & latency telemetry

The plan logs `stages.llm_assessment.usage` (tokens) but the dashboard
ignores it. Roll up tokens × model price into a `$ this week` and
`$ projected monthly` figure on the Health view, with a per-source
breakdown for the Sources view.

**DoD:** add `total_tokens`, `cost_usd`, and `mean_latency_ms` cards to
the Health StatusStrip; add a per-source $ column in Sources.

#### FEAT-003 — Confidence calibration plot ("does our 80% mean 80%?")

In Observe, replace or augment `PrecisionTracker` with a reliability
diagram: bin LLM `confidence` into deciles, plot empirical "useful" rate
from feedback against the bin midpoint. A perfectly calibrated model
follows y = x.

**DoD:** new `<CalibrationDiagram>` visualisation; minimum 30 feedback
records before rendering, otherwise show "needs N more data points".

#### FEAT-004 — Stage-4 / Stage-6 score distributions with live threshold lines

The `ThresholdSimulator` is a step in this direction but doesn't show the
distribution of *actual* scores. Render histograms of `rrf_score` (Stage
4) and `crossencoder_score` (Stage 6) over the filtered period, with
draggable threshold lines that show how many alerts would survive at the
new value. This makes Section 5.3 of the plan ("threshold calibration
using feedback data") an interactive task instead of a future memo.

**DoD:** two new histograms in Adjust, below the section accordion;
dragging the line updates a "would-be alerts: N" counter live.

#### FEAT-005 — Trigger lineage Sankey

For any CHANGE_REQUIRED verdict, show a Sankey from
`source → diff bucket (S2 significance) → top relevance candidate →
crossencoder confirmed page → LLM verdict`. A single picture explains
"why this fired".

**DoD:** new visualisation in EventDrawer; reuse existing
`crossencoder.scored_pages` array.

#### FEAT-006 — Source contribution & trust matrix

In the Sources view, add a sortable column for "alerts contributed (90d)"
and "feedback-validated precision". A source that fires often but is
mostly marked `not_significant` is a tuning candidate — surface that.

**DoD:** join `pipeline_runs` and `feedback.jsonl` server-side; expose
`alerts_30d`, `useful_rate` per source.

#### FEAT-007 — Diff-overlap-with-IPFR heatmap

When Stage 5/6 ran, the bi-encoder produced per-chunk hit scores. Render
those as a small 2D heatmap on the SnapshotOverlay right pane: rows are
diff segments (or the whole new-text), columns are top-K IPFR pages,
cells are max chunk score. Lets the operator see at a glance "which
parts of the change matter to which page".

**DoD:** depends on BUG-001 fix; reuse `details.stages.biencoder.candidate_pages[].top_chunk_scores`.

#### FEAT-008 — LLM reasoning surfaced in the Triggered Events table

`reasoning` and `suggested_changes` are returned by the runs API
(`runs.js:28-29`) but only consumed by the EventDrawer. Adding a
truncated reasoning column (with click-to-expand) to
`TriggeredEventsTable.jsx` would make scanning the day's alerts much
faster.

**DoD:** add an inline expandable cell.

### Workflow & UX

#### FEAT-009 — Global error toast + connectivity indicator

When any `/api/*` call fails, show a single transient toast (top-right,
under the topbar). Colour the topbar status pill red on consecutive
failures so the user knows to refresh, instead of seeing stale data
without warning.

**DoD:** wrap React Query in a global `onError` handler; ToastProvider
context.

#### FEAT-010 — Refresh-in-progress affordance

The Topbar `REFRESH` button silently invalidates queries. Add a spinner
and a `Last refreshed Xs ago` line.

**DoD:** local state on the button + visible timestamp.

#### FEAT-011 — Persist filter state to URL

Today the filters live in React state and reset on page reload. Mirror
them to query params on `HashRouter` so a link to a specific filter view
is shareable with the team.

**DoD:** `useFilters` reads/writes `?from=&to=&sources=…` on the hash.

#### FEAT-012 — Snapshot diff: version dropdown

After BUG-001/BUG-008 are fixed, expose all retained versions
(`<src>.txt`, `<src>.v1.txt`, … up to `storage.content_versions_retained`)
in the SnapshotOverlay header so the user can compare current ↔ any
prior version, not just current ↔ immediate previous.

**DoD:** scan the snapshot directory server-side, return the list of
versions; client renders a `<select>`.

#### FEAT-013 — Inline "open in DB" affordance for power users

Almost every visualisation eventually leaves the user wanting to write a
SQL query. A read-only SQL console (e.g. via
[`sql.js`](https://sql.js.org)) on the Document section, scoped to the
existing 8 tables, would let analysts answer questions the dashboard
doesn't anticipate.

**DoD:** load `ipfr.sqlite` in WebAssembly read-only; UI is a simple
textarea + results table; warn if query touches more than 100k rows.

#### FEAT-014 — Bulk source import / export

`POST /api/sources` is one row at a time. Add a CSV upload button next to
`+ ADD SOURCE` that POSTs the CSV directly. Mirror with a `Download CSV`
that returns the current registry.

**DoD:** new `POST /api/sources/import` (multipart) and
`GET /api/sources/export.csv`.

#### FEAT-015 — Source cards: "next scheduled check"

The cadence barcode is cute but doesn't tell the user *when* the next
check will fire. Compute it from `last_checked + frequency_to_seconds(check_frequency)`
and render alongside the barcode.

**DoD:** server returns `next_check_at`; client shows it in the source
row.

### Observability & docs

#### FEAT-016 — Audit log of config changes

Every `POST /api/config` is currently invisible. Either commit the
resulting YAML to git via `git_persistence` (the config already has a
`commit_author` slot), or write a separate `data/logs/config_audit.jsonl`
keyed by who-when-what.

**DoD:** new `config_audit.jsonl` with `{ts, user, diff_lines}`; view in
the Adjust section under a collapsible "history" panel.

#### FEAT-017 — Pipeline graph visualisation in `Document`

`PipelineDiagram.jsx` already exists. Make the nodes clickable: clicking
"Stage 4" jumps the document body to §4 of the plan AND highlights any
relevant Adjust controls. Closes a real workflow loop ("I don't know what
this threshold does → click → read → adjust").

**DoD:** wire `onClick` on each diagram node to dispatch the existing
`tripwire:navigate-doc` event.

#### FEAT-018 — Inline keyphrase / entity inspector for IPFR pages

`/api/pages/:page_id` already returns top-10 keyphrases and entities. The
ContentMap and KnowledgeGraph could surface those on hover; today the
hover only shows the title.

**DoD:** add hover-card with chips for top entities and keyphrases.

#### FEAT-019 — "Why was this not triggered?" inverse view

For sources that had a change but did *not* trigger an alert, expose the
chain of rejection: which threshold did it fail, by how much. Sits inside
the EventDrawer for runs whose verdict is `NO_CHANGE`.

**DoD:** read `rejected_candidates` (already in the trigger record per
`src/pipeline.py:646-657`) and render a small reasons-table.

---

## P3 — Speculative / blocked

Not for this iteration. Listed so they aren't lost.

### FEAT-020 — Threshold auto-calibration from feedback (plan §5.3)

Already a TODO in `src/stage4_relevance.py:40` and `src/stage6_crossencoder.py:46`.
Blocked on 4–8 weeks of accumulated feedback. When unblocked, surface
the recommended new thresholds in the Adjust UI as a banner.

### FEAT-021 — Internal-link graph edges (plan §5.5)

Toggle exists in the Adjust UI but is `disabled: true`. Requires
`ingestion/graph.py` to extract internal links during ingestion. Once
implemented, simply remove the disabled flag.

### FEAT-022 — BM25 positional/proximity extensions (plan §5.6)

Likewise blocked until we have evidence that lexical scoring under-performs
on long diffs.

### FEAT-023 — Mobile / tablet layout

Currently desktop-first with hard-coded widths (e.g. `width: 640px`
EventDrawer, `width: 240px` document sidebar). Real mobile use seems
unlikely for this internal tool, but a responsive pass would make the
dashboard usable on a 13" laptop in landscape mode.

### FEAT-024 — Multi-tenant / per-user feedback attribution

The pipeline assumes a single content owner. If the team grows beyond
3–5 people, sign feedback with the user's email so disagreements are
visible.

### FEAT-025 — Replace LIKE-on-`triggered_pages` with a proper join table

`dashboard/server/routes/pages.js:147,217` and
`dashboard/server/routes/graph.js:21` use
`triggered_pages LIKE '%' || page_id || '%'` to count alerts per page.
Cheap today, broken when a page_id is a substring of another or when the
table grows past 100k rows. Long-term, normalise into a
`pipeline_run_triggered_pages(run_id, page_id)` table populated at write
time.

---

## Cross-cutting principles

These aren't tasks — they're tests we should apply when picking from this
backlog.

1. **Fail loudly, not quietly.** Every silent empty-state in the current
   dashboard hides a fixable bug. Replace `data: []` with proper error
   surfaces.
2. **Single source of schema truth.** Today the Adjust UI defines
   parameters in JS, the Express server validates a different schema, and
   the Python pipeline is the actual consumer. Pick one and derive the
   other two.
3. **Don't add cleverness around broken plumbing.** Most of the P0 bugs
   exist because someone built a polished UI on top of a hook/route pair
   that wasn't really wired up. Wire it up first.
4. **Every visualisation should answer one specific operator question.**
   If it doesn't, delete it or merge it with one that does.

---

## Quick triage for the next iteration

If picking up this backlog cold, do the P0 list in this order — each
takes <2 hours and unblocks user trust:

1. **BUG-002** (Topbar) — single-component fix, instantly visible win.
2. **BUG-003** (config path + validator) — unblocks the entire Adjust section.
3. **BUG-005** (`run_at` → `timestamp`) — restores two visualisations with a
   single-line change in four files.
4. **BUG-004** (filter param names + datePreset) — small, surgical, makes the
   global filter actually do something.
5. **BUG-001** (snapshots) — most user-visible; depends on a sync change so
   slightly bigger.
6. **BUG-007** (dbGuard 503) — ten-line fix that will start exposing other
   silent failures.
7. **BUG-006** (Stage 7–9 funnel) — needs a brief design conversation
   first; do last in this batch.
