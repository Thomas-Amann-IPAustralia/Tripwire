# TRIPWIRE DASHBOARD
## Design & Engineering Brief for Claude Code

**Version:** 3.0 — Team web access  
**Author:** Thomas Amann, IPAVentures  
**Date:** April 2026  
**Changelog from v2.1:**
- §1: updated audience from solo local operator to small content team; removed "local web application" framing
- §3.2: CORS updated — permissive localhost removed; origin locked to deployment URL via environment variable
- §3.5: start scripts updated for production deployment
- NEW §3.6: Deployment — Render free tier, persistent disk, environment variables
- NEW §3.7: Authentication — HTTP Basic Auth middleware in Express; all routes protected
- §6 (all routes): Basic Auth applies to all API endpoints; no public routes
- §12 (new): Deployment & Auth implementation notes — full code patterns for Basic Auth, Render config, data path handling, and database sync strategy
- §15: Claude Code prompt updated to reference §3.6, §3.7, and §12

---

## 1. PROJECT CONTEXT

Tripwire is an autonomous change-monitoring pipeline that watches external IP legislation and regulatory sources ("influencers") and determines whether changes to those sources require amendments to content on the IP First Response (IPFR) website ("the influenced corpus"). It runs as a scheduled GitHub Actions workflow and persists all outputs — run logs, page embeddings, chunk-level embeddings, entity inventories, keyphrases, graph edges, and LLM assessment results — into a SQLite database at `data/ipfr_corpus/ipfr.sqlite`.

The dashboard built from this brief serves **a small content management team** (typically 2–5 people) who need to monitor pipeline activity, review triggered alerts, and manage source configuration from their web browsers. It must be a powerful analytical instrument AND something pleasant to live with every day.

The dashboard is a **web application** — a React frontend (Vite + React) served by a lightweight **Express API layer** that reads from the SQLite database. It is deployed as a single Node.js service on **Render** (free tier), protected by **HTTP Basic Auth**. There is no mock data. All data is read at runtime from the real database, which will contain months of accumulated pipeline run history.

---

## 2. REPOSITORY STRUCTURE FOR THE DASHBOARD

The dashboard lives inside the existing `tripwire/` repository as a first-class subdirectory:

```
tripwire/
├── dashboard/
│   ├── package.json
│   ├── vite.config.js
│   ├── index.html
│   ├── src/
│   │   ├── main.jsx                  # React entry point
│   │   ├── App.jsx                   # Root component, router, global state
│   │   ├── styles/
│   │   │   └── globals.css           # CSS variables, reset, base typography
│   │   ├── sections/
│   │   │   ├── Observe.jsx
│   │   │   ├── Corpus.jsx
│   │   │   ├── Sources.jsx
│   │   │   ├── Adjust.jsx
│   │   │   ├── Document.jsx
│   │   │   └── Health.jsx
│   │   ├── components/
│   │   │   ├── NavRail.jsx
│   │   │   ├── Topbar.jsx
│   │   │   ├── FilterBar.jsx
│   │   │   ├── EventDrawer.jsx
│   │   │   ├── Tooltip.jsx
│   │   │   ├── ConfigControl.jsx
│   │   │   └── StageIndicator.jsx
│   │   ├── visualisations/
│   │   │   ├── FunnelSummary.jsx
│   │   │   ├── TimelineSwimLane.jsx
│   │   │   ├── SourceMatrix.jsx
│   │   │   ├── StageDonutGrid.jsx
│   │   │   ├── TriggeredEventsTable.jsx
│   │   │   ├── Embedding3D.jsx       # Three.js
│   │   │   ├── KnowledgeGraph.jsx    # D3 force
│   │   │   ├── ContentMap.jsx        # D3 treemap
│   │   │   ├── SnapshotOverlay.jsx
│   │   │   ├── BipartiteMap.jsx      # D3 bipartite
│   │   │   ├── PrecisionTracker.jsx
│   │   │   ├── CalendarHeatmap.jsx
│   │   │   └── ThresholdSimulator.jsx
│   │   ├── hooks/
│   │   │   ├── useData.js            # Fetch + cache layer
│   │   │   ├── useFilters.js
│   │   │   └── useConfig.js
│   │   └── lib/
│   │       ├── dataUtils.js          # Filtering, aggregation, stat helpers
│   │       └── systemPlan.js         # Full document content as structured data
│   └── server/
│       ├── index.js                  # Express server entry point
│       ├── db.js                     # Better-sqlite3 connection + helpers
│       ├── auth.js                   # HTTP Basic Auth middleware
│       └── routes/
│           ├── runs.js               # /api/runs, /api/runs/:id
│           ├── pages.js              # /api/pages, /api/pages/:id
│           ├── sources.js            # /api/sources (GET + POST)
│           ├── config.js             # GET /api/config, POST /api/config
│           ├── embeddings.js         # /api/embeddings (3D coords + metadata)
│           ├── graph.js              # /api/graph/nodes, /api/graph/edges
│           └── snapshots.js          # /api/snapshots/:sourceId
```

---

## 3. TECHNOLOGY STACK

### 3.1 Frontend

- **React 18** with hooks
- **Vite** for development and build
- **React Router v6** for section routing (hash-based, no server config needed)
- **Recharts** for 2D charts (area charts, bar charts, donut charts, line charts)
- **D3 v7** (`d3-force`, `d3-hierarchy`, `d3-scale`, `d3-zoom`) for knowledge graph, treemap, bipartite map, calendar heatmap
- **Three.js r128** for 3D embedding scatter
- **@tanstack/react-query** for data fetching, caching, and background refresh
- No external component libraries (no MUI, Chakra, Radix, etc.) — all components are custom

### 3.2 Backend (API Server)

- **Express 4** running on `process.env.PORT` (default `3001` for local dev)
- **better-sqlite3** for synchronous SQLite reads (no async complexity needed)
- **cors** middleware — in production, origin is locked to `process.env.DASHBOARD_ORIGIN`; in development, permissive localhost is allowed
- **js-yaml** for reading and writing `tripwire_config.yaml`
- **csv-parse** + **csv-stringify** for reading and writing `source_registry.csv` (required by `POST /api/sources`)
- **ml-pca** (`npm install ml-pca`) for server-side PCA on doc embeddings — replaces `numeric` which is unmaintained since 2016
- All routes are read-only except `POST /api/config` (which writes the YAML file) and `POST /api/sources` (which writes the CSV)
- **All routes are protected by HTTP Basic Auth** — see §3.7

### 3.3 Data Flow

The frontend fetches all data from `/api/...` via React Query. The Express server reads from the SQLite database using better-sqlite3 synchronous calls. Embedding coordinates for the 3D and 2D visualisations are pre-computed server-side on first request and cached in memory (PCA projection on the raw BLOB embeddings using `ml-pca`). Config reads/writes target `tripwire_config.yaml` directly. Source registry reads/writes target `data/influencer_sources/source_registry.csv` directly.

In production, the React app is served as static files by the same Express server (`--serve-build` flag), so the frontend makes API requests to the same origin — no cross-origin complexity.

### 3.4 Path Anchoring

The Express server resolves all data paths relative to the **repository root**. In local development, this is two directories above `dashboard/server/`. On Render, this is the directory where the repo is cloned. The `DATA_ROOT` environment variable overrides this when set:

```js
// dashboard/server/db.js
const REPO_ROOT = process.env.DATA_ROOT || path.join(__dirname, '..', '..');
const DB_PATH = path.join(REPO_ROOT, 'data/ipfr_corpus/ipfr.sqlite');
const CONFIG_PATH = path.join(REPO_ROOT, 'tripwire_config.yaml');
const REGISTRY_PATH = path.join(REPO_ROOT, 'data/influencer_sources/source_registry.csv');
const FEEDBACK_PATH = path.join(REPO_ROOT, 'data/logs/feedback.jsonl');
const SNAPSHOTS_PATH = path.join(REPO_ROOT, 'data/influencer_sources/snapshots');
```

On Render, `DATA_ROOT` is set to the persistent disk mount path — see §3.6.

### 3.5 Start Commands

```json
// dashboard/package.json
"scripts": {
  "dev:server": "node server/index.js",
  "dev:client": "vite",
  "dev": "concurrently \"npm run dev:server\" \"npm run dev:client\"",
  "build": "vite build",
  "start": "node server/index.js --serve-build"
}
```

When `--serve-build` is detected, the Express server serves the Vite `dist/` directory as static files with a catch-all SPA fallback route (see §12.1). The `npm start` command is what Render executes in production.

### 3.6 Deployment — Render Free Tier

The dashboard is deployed as a single **Render Web Service** (free tier). The same Express process serves both the API and the built React frontend.

**Service configuration:**

| Setting | Value |
|---|---|
| **Environment** | Node |
| **Build command** | `cd dashboard && npm install && npm run build` |
| **Start command** | `cd dashboard && npm start` |
| **Plan** | Free |
| **Persistent disk** | Attached at `/data` (1 GB, free tier) |

**Persistent disk:** The SQLite database, config YAML, source registry CSV, snapshots, and feedback log all live on the persistent disk at `/data`. Set `DATA_ROOT=/data` in the Render environment variables so the server resolves data paths to the disk mount.

**Database sync strategy:** The SQLite database is built and updated by GitHub Actions (the Tripwire pipeline). After each pipeline run, GitHub Actions uploads the database to the Render persistent disk via `rsync` over SSH using a Render deploy key stored as a GitHub Actions secret. See §12.3 for the GitHub Actions step pattern.

**Environment variables to set in the Render dashboard:**

| Variable | Example value | Purpose |
|---|---|---|
| `DASHBOARD_USER` | `tripwire` | Basic Auth username |
| `DASHBOARD_PASS` | `<strong-password>` | Basic Auth password |
| `DASHBOARD_ORIGIN` | `https://tripwire-dashboard.onrender.com` | CORS allowed origin |
| `DATA_ROOT` | `/data` | Persistent disk mount path |
| `NODE_ENV` | `production` | Disables dev-only behaviour |

**Free tier limitations:** Render free web services spin down after 15 minutes of inactivity and take ~30 seconds to cold-start on next request. This is acceptable for an internal tool used by a small team on a known schedule (typically after pipeline runs). The persistent disk is not affected by spin-down — data is retained between restarts.

**Custom domain (optional):** Render free tier supports custom domains via CNAME. Update `DASHBOARD_ORIGIN` when adding a custom domain.

### 3.7 Authentication — HTTP Basic Auth

All routes are protected by a single HTTP Basic Auth middleware. There are no public routes.

Basic Auth is the right choice here: it is natively supported by all browsers (no login page to build), trivially implemented in Express, and appropriate for a small internal team accessing an internal tool over HTTPS. Render provides HTTPS automatically.

**Implementation:**

```js
// dashboard/server/auth.js
export function basicAuth(req, res, next) {
  const user = process.env.DASHBOARD_USER;
  const pass = process.env.DASHBOARD_PASS;

  // In development (NODE_ENV !== 'production'), skip auth if credentials not set
  if (process.env.NODE_ENV !== 'production' && (!user || !pass)) {
    return next();
  }

  const authHeader = req.headers['authorization'] || '';
  const [scheme, encoded] = authHeader.split(' ');

  if (scheme !== 'Basic' || !encoded) {
    res.set('WWW-Authenticate', 'Basic realm="Tripwire Dashboard"');
    return res.status(401).send('Authentication required.');
  }

  const [incomingUser, incomingPass] = Buffer.from(encoded, 'base64')
    .toString('utf8')
    .split(':');

  if (incomingUser === user && incomingPass === pass) {
    return next();
  }

  res.set('WWW-Authenticate', 'Basic realm="Tripwire Dashboard"');
  return res.status(401).send('Invalid credentials.');
}
```

```js
// dashboard/server/index.js — apply before all routes
import { basicAuth } from './auth.js';

app.use(basicAuth);

// ... then register API routes and static file serving
```

**Sharing access:** To give a team member access, share the username and password directly. The browser will remember the credentials for the session. To revoke access for an individual, change `DASHBOARD_PASS` in the Render environment variables and redeploy — all sessions are invalidated.

**Future upgrade path:** If the team grows or role-based access is needed, the `auth.js` module is the only file that needs updating. A drop-in replacement using `passport.js` with a user list stored in environment variables (e.g. `DASHBOARD_USERS=alice:pass1,bob:pass2`) can be added without touching any routes.

---

## 4. AESTHETIC DIRECTION

### 4.1 Reference Images

Three reference images define the aesthetic. Extract the following from each:

**Image 1 — IPAVentures & GenAI slide (dark, typographic):**
- Near-black background (`#111110`)
- Large, heavy sans-serif display type — grotesque, slightly distressed
- Warm off-white / bone text (`#e8e2d4`)
- Discrete colour dot motif: green, orange/red, yellow, blue, mid-grey, light-grey (map to pipeline stages)
- Fine rule lines as structural dividers
- Bottom-anchored metadata strip in small caps

**Image 2 — Entrace Group layout (Swiss/editorial, constructivist):**
- Brutalist-editorial grid with extreme typographic scale contrast
- Barcode / data-label aesthetics — alphanumeric codes, version strings, coordinate-like numbers
- Orange, blue, yellow as accents against a neutral ground
- Dense information at the edges, breathing space in the middle

**Image 3 — Tesla/Electric Current poster (technical diagram, monochrome):**
- Thin-weight engineering diagram with labelled parts
- Restrained typographic hierarchy: small caps, generous tracking, light weight
- Coordinates and version numbers as decorative data elements

### 4.2 Synthesis: "Government Intelligence Terminal"

Imagine a bespoke internal tool built for a well-funded government analytical unit in 1997 and then restored by a Swiss designer in 2024.

- **Serious without being sterile.** Dense with information; every element earns its place.
- **Editorial in its typography.** Not a SaaS dashboard. Something between a printed policy brief and a mission control terminal.
- **Colour-coded but not garish.** The six dot colours from Image 1 map to pipeline stages and are used with restraint.

### 4.3 Colour Palette

```css
/* globals.css */
:root {
  --bg-primary:    #111110;
  --bg-secondary:  #1a1a18;
  --bg-tertiary:   #242420;
  --bg-accent:     #2e2e28;

  --text-primary:  #e8e2d4;
  --text-secondary: #9e9888;
  --text-tertiary:  #5c5a52;

  --rule:          #2e2e28;
  --rule-accent:   #4a4a40;

  /* Stage accent colours — from Image 1 dots.
     NOTE: The pipeline has 9 stages but only 6 distinct dot colours (by design).
     Stages 1 & 7 intentionally share green  (--stage-1 / --stage-7 are identical).
     Stages 2 & 8 intentionally share orange (--stage-2 / --stage-8 are identical).
     Stages 3 & 9 intentionally share amber  (--stage-3 / --stage-9 are identical).
     This is deliberate — do not "fix" it. The funnel visualisation distinguishes
     stages by position and label, not colour alone. */
  --stage-1:  #3a6b3a;   /* deep green  — Metadata Probe      */
  --stage-2:  #c94020;   /* rust orange — Change Detection    */
  --stage-3:  #d4a820;   /* amber       — Diff Generation     */
  --stage-4:  #4a7ab5;   /* steel blue  — Relevance Scoring   */
  --stage-5:  #4a4a40;   /* dark grey   — Bi-Encoder          */
  --stage-6:  #7a7a70;   /* mid grey    — Cross-Encoder       */
  --stage-7:  #3a6b3a;   /* green       — Aggregation         */
  --stage-8:  #c94020;   /* orange      — LLM Assessment      */
  --stage-9:  #d4a820;   /* amber       — Notification        */

  /* Semantic states */
  --state-alert:    #c94020;
  --state-warn:     #d4a820;
  --state-ok:       #3a6b3a;
  --state-error:    #8b1a1a;
  --state-inactive: #5c5a52;

  /* Typography */
  --font-display: 'Bebas Neue', sans-serif;
  --font-mono:    'DM Mono', monospace;
  --font-body:    'Lora', serif;
}
```

### 4.4 Typography

Load via Google Fonts in `index.html`:

```html
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Mono:wght@300;400;500&family=Lora:ital,wght@0,400;0,500;1,400&display=swap" rel="stylesheet">
```

- **`Bebas Neue`** — Display and all headings. Heavy caps. Used for section titles, panel headers, nav labels, stat numbers.
- **`DM Mono`** — All data values, codes, timestamps, scores, IDs, labels, inputs. Nothing with a numerical value should render in any other font.
- **`Lora`** — Document body text, tooltip descriptions, LLM reasoning text, diff previews. The serif gives a "policy brief" quality that contrasts with the terminal mono.

### 4.5 Motifs & Details

- **Hairline rules** (`1px solid var(--rule)`) as the primary structural separator between every logical group
- **Alphanumeric labels** — `TW-S1`, `TW-S4`, run IDs like `2026-04-05-001` — in DM Mono 9px as decorative data identifiers
- **Stage indicator dots** — 9 small circles coloured by `--stage-N`, used as inline categorical markers throughout
- **Coordinate-style numbers** for embedding positions (e.g. `+0.4821, −0.1234, +0.7782`) in DM Mono 9px
- **Grain texture overlay** using an SVG noise filter at `opacity: 0.025` on all panel backgrounds for depth
- **No border-radius on structural containers** — hard corners only. Pills and badges: max 2px radius.
- **Barcode-style encoding** — narrow vertical bars representing source check cadences in the Sources registry

---

## 5. LAYOUT ARCHITECTURE

### 5.1 Top-Level Structure

Single-page application. Persistent left nav rail (56px collapsed / 200px expanded, toggle via hamburger). Full-height main content area.

```
┌─────┬──────────────────────────────────────────────────────┐
│     │  TOPBAR (48px)                                       │
│ NAV ├──────────────────────────────────────────────────────┤
│     │                                                      │
│ R   │           MAIN CONTENT AREA                         │
│ A   │           scrollable, section-specific              │
│ I   │                                                      │
│ L   │                                                      │
└─────┴──────────────────────────────────────────────────────┘
```

### 5.2 Topbar

48px strip. Left: `◈ TRIPWIRE` in Bebas Neue 18px + version tag `v2.0 · TW-DASHBOARD` in DM Mono 10px `--text-tertiary`. Right: last run timestamp, pipeline status pill (`RUNNING` / `IDLE` / `ERROR`), and a mini 9-dot stage completion indicator for the most recent run.

### 5.3 Nav Rail

Nav items (icons + Bebas Neue labels, tracked 0.15em):
1. `OBSERVE`
2. `CORPUS`
3. `SOURCES`
4. `ADJUST`
5. `DOCUMENT`
6. `HEALTH`

Active item: left-side 3px coloured bar. Hovering expands the rail (if collapsed). All navigation is React Router routes: `/observe`, `/corpus`, `/sources`, `/adjust`, `/document`, `/health`.

### 5.4 Section Anatomy

Each section:
- **Header band** (80px): section title Bebas Neue 42px, one-line descriptor DM Mono 11px `--text-secondary`, contextual actions (filters / buttons) anchored right
- **Content area**: scrollable, CSS Grid layout, panels arranged per spec below

---

## 6. API SPECIFICATION

The Express server exposes the following endpoints. The frontend consumes all data from these endpoints via React Query.

**All endpoints require HTTP Basic Auth.** A 401 response is returned for any unauthenticated request.

### 6.1 Run Data

**`GET /api/runs`**
Query params: `from` (ISO date), `to` (ISO date), `source_id`, `stage_reached_min` (integer 1–9), `verdict`, `outcome`, `limit` (default 1000), `offset`.

**Implementation note — `stage_reached` mapping:** The `pipeline_runs.stage_reached` column stores **text values** (not integers). The server maps them to integers before filtering and returning:

| DB value | Integer | Meaning |
|---|---|---|
| `"stage1"` | 1 | Metadata probe complete |
| `"scrape"` | 1 | Between probe and change detection |
| `"stage2"` | 2 | Change detection complete |
| `"stage3"` | 3 | Diff generation complete |
| `"stage4"` | 4 | Relevance scoring complete |
| `"stage5"` | 5 | Bi-encoder complete |
| `"stage6"` | 6 | Cross-encoder running |
| `"stage6_complete"` | 6 | Cross-encoder complete |

The API response exposes `stage_reached` as an integer. The `stage_reached_min` query param filters `WHERE mapped_stage >= stage_reached_min`.

Returns array of run records. Each record extracts from `pipeline_runs.details` JSON the fields needed for the dashboard:

```json
{
  "id": 1,
  "run_id": "2026-04-05-001",
  "source_id": "TW-SRC-007",
  "source_url": "https://...",
  "source_type": "frl",
  "timestamp": "2026-04-05T02:10:00Z",
  "stage_reached": 8,
  "outcome": "completed",
  "triggered_pages": ["B1012", "C2003"],
  "duration_seconds": 14.2,
  "verdict": "CHANGE_REQUIRED",
  "confidence": 0.85,
  "ipfr_page_id": "B1012",
  "biencoder_max": 0.81,
  "crossencoder_score": 0.74,
  "reranked_score": 0.78,
  "reasoning": "...",
  "suggested_changes": ["..."],
  "diff_text": "...",
  "feedback": null,
  "significance": "high",
  "fast_pass_triggered": false,
  "graph_propagated": false,
  "scores": {
    "bm25_rank": 1,
    "semantic_rank": 2,
    "rrf_score": 0.048,
    "source_importance": 0.7,
    "final_score": 0.041
  }
}
```

**Implementation note:** The `verdict`, `confidence`, `reasoning`, `suggested_changes`, `diff_text`, `reasoning` and `scores` fields all live inside the `pipeline_runs.details` JSON column. The `biencoder_max` field requires navigating into a JSON array — see the corrected SQL in §11.3. If a given field is absent from the `details` blob (because it was not yet logged by that version of the pipeline), return `null` for that field — never fail the request.

**`GET /api/runs/:run_id`** — Full single run record including complete `details` JSON.

**`GET /api/runs/summary`** — Aggregate counts for the funnel: per-stage pass/fail/error counts across the filtered period.

**`GET /api/runs/feedback`** — Returns all records from `data/logs/feedback.jsonl` as a JSON array, merged with their corresponding run record by `run_id`.

### 6.2 Pages (IPFR Corpus)

**`GET /api/pages`** — All **active** IPFR pages from the `pages` table (`WHERE status = 'active'`). Stub pages (`status = 'stub'`) and duplicate pages (`status = 'duplicate'`) are excluded from all corpus visualisations. Returns: `page_id`, `url`, `title`, `chunk_count` (JOIN from chunks), `entity_count` (JOIN from entities), `last_modified`, `last_ingested`, `alert_count` (computed from pipeline_runs), `cluster` (pre-assigned server-side via KMeans on embeddings on first request, cached), `embedding_2d` and `embedding_3d` (PCA-projected server-side, cached).

**`GET /api/pages/:page_id`** — Full page detail including: all fields from `/api/pages`, the top 10 keyphrases (from `keyphrases` table ordered by score), the named entities (from `entities` table), the top 5 graph neighbours (from `graph_edges` table ordered by weight), all historical alerts for this page (from `pipeline_runs`).

### 6.3 Sources

**`GET /api/sources`** — All sources from `data/influencer_sources/source_registry.csv`, joined with aggregated stats computed from `pipeline_runs`: total checks, last checked, last changed, consecutive failures, per-stage outcome distribution, check history (last 90 days, one record per run for sparkline rendering).

**`POST /api/sources`** — Add or update a source in `source_registry.csv`. Accepts a JSON body matching the source record schema (see §11.5). Validates required fields and writes the updated CSV to disk. Returns `{ success: true }` or `{ error: "..." }`. Requires `csv-parse` and `csv-stringify` npm packages.

**`GET /api/sources/:source_id/snapshot`** — Returns the current snapshot text for this source from `data/influencer_sources/snapshots/<source_id>/<source_id>.txt`. Also returns the previous snapshot (`<source_id>.v1.txt`) if available, and the computed diff between them.

### 6.4 Graph

**`GET /api/graph/nodes`** — All entries from `/api/pages` (active only) reduced to graph node fields: `page_id`, `title`, `cluster`, `alert_count`, `degree` (computed from graph_edges), `embedding_2d`.

**`GET /api/graph/edges`** — All rows from `graph_edges` table: `source_page_id`, `target_page_id`, `edge_type`, `weight`.

### 6.5 Config

**`GET /api/config`** — Reads and parses `tripwire_config.yaml`, returns as JSON.

**`POST /api/config`** — Accepts a full config JSON body, validates it (required fields present, types correct), writes back to `tripwire_config.yaml`. Returns `{ success: true }` or `{ error: "..." }`.

### 6.6 Health

**`GET /api/health/summary`** — Returns: last run timestamp and outcome, 30-day error rate, total sources monitored, LLM schema failure count (30d) (from `pipeline_runs.details` where `stages.llm_assessment.schema_valid = false`), cross-encoder truncation count (30d) (from `pipeline_runs.details` where `stages.crossencoder.truncation_warnings` is non-empty), sources with consecutive failures ≥ 2 (array).

**`GET /api/health/runs`** — Paginated list of pipeline run summaries (one row per `run_id`, aggregated from per-source rows): start time, duration, sources checked, sources changed, sources errored, alerts generated, status.

**`GET /api/health/ingestion`** — Summary from the `ingestion_runs` table (populated by the IPFR ingestion pipeline): last ingestion run timestamp, pages ingested, pages skipped, stubs, errors, duplicates, boilerplate lines detected, keyphrases dropped. This supplements the pipeline health view with corpus freshness data.

---

## 7. SECTION SPECIFICATIONS

---

### 7.1 OBSERVE — Pipeline Analytics

The primary view. Answers: *what has happened, to what depth, and what was found?*

#### 7.1.1 Global Filter Bar

Persistent strip immediately below the section header. All panels below respond reactively. State managed in `useFilters` hook and propagated via React Context.

Controls:
- **Date range** — preset chips: `7D · 30D · 90D · ALL` + custom range datepicker
- **Source** — multi-select searchable dropdown (populated from `/api/sources`)
- **Stage reached** — chip row: S1 through S9, each with its colour dot; selecting a chip filters to runs that reached **at least** that stage
- **Verdict** — chips: `CHANGE_REQUIRED · UNCERTAIN · NO_CHANGE · ERROR`
- **Reset filters** link

#### 7.1.2 Panel Row 1 — Funnel Summary (full width)

Horizontal funnel. Nine vertical bars (one per stage), heights encoding total event count reaching that stage across the filtered period. Bars coloured `--stage-N`. Above each bar: count in DM Mono 11px. Below: stage short-name in Bebas Neue 10px. Between bars: `›` glyph in `--text-tertiary`. Hover tooltip: stage full name, count, pass-through rate from previous stage (percentage). Click a bar: adds that stage to the Stage filter.

**Note:** Because stages 1 & 7, 2 & 8, and 3 & 9 share the same CSS colour variable, position (left-to-right order) is the primary distinguishing signal in this chart. Pair each bar with its Bebas Neue stage label.

#### 7.1.3 Panel Row 2 — Calendar Heatmap (full width)

A GitHub-contribution-style 52-week calendar heatmap showing alert density per day (CHANGE_REQUIRED verdicts). Each day cell is a 12×12px square. Colour scale: `--bg-tertiary` (zero) → `--state-alert` (maximum). Month labels in DM Mono 9px above. Day-of-week labels left. Hover: tooltip showing exact date, alert count, and source names. Click: sets the global date filter to that day.

#### 7.1.4 Panel Row 3 — Timeline Swimlane (full width)

Two-track timeline:

**Track 1 — Source swimlanes:** One horizontal row per active source (sources with zero events in the period are hidden; toggle to show all). Each check event is a 2px-wide × 12px-tall vertical tick, coloured by deepest stage reached. Sources sorted by total events descending. Source name labels in DM Mono 9px at left, right-truncated at 28 chars.

**Virtualisation:** Only render ticks within the visible horizontal scroll viewport. Use a custom windowing approach (track the scroll position, compute visible time window, render only ticks within ±200px of viewport edge).

**Track 2 — Daily volume:** Bar chart showing daily alert volume overlaid with a line for total daily checks.

X axis: date labels, DM Mono 9px. Scroll horizontally for periods longer than 30 days. A zoom level toggle: `DAY · WEEK · MONTH` aggregation.

Hover a tick: popover with run ID, source, stage reached, verdict, score, and timestamp. Click: opens the Event Detail Drawer.

To the right of each swimlane row: a mini-funnel (9 stage dots, sized proportionally to stage pass rates for that source over the filtered period).

#### 7.1.5 Panel Row 4 — Source Matrix (60%) + Stage Breakdown (40%)

**Source Matrix:** Grid where rows = influencer sources, columns = pipeline stages S1–S9. Cell colour: solid stage colour (stage reached + passed), 50% opacity (reached + failed/rejected), `--bg-tertiary` (never reached), `--state-error` (error). Cell hover tooltip: exact counts (passed / failed / errored). Row hover: highlights row. Click: sets source filter. Left of each row: an importance bar (fill height = importance score 0.0–1.0, width = 8px, coloured `--stage-4`).

**Stage Donut Grid:** Nine small donut charts (3×3 grid). Each donut: outcome distribution (passed / rejected / errored / skipped) for that stage across the filtered period. Stage number in Bebas Neue 24px in the centre. Below: total events, DM Mono 9px.

#### 7.1.6 Panel Row 5 — Alert Precision Tracker (full width)

A time-series area chart (Recharts) showing rolling 30-day alert precision: `useful_alerts / total_alerts_with_feedback` as a line, plotted weekly. Separate shaded regions for each feedback category. A "no feedback" remainder shown as a lighter fill.

Data source: `/api/runs/feedback` merged with run records. If fewer than 5 feedback records exist, show a "Collecting feedback — precision tracking begins with 5+ rated alerts" placeholder.

#### 7.1.7 Panel Row 6 — Triggered Events Table (full width)

Paginated table of all events that reached Stage 8 (LLM Assessment) in the filtered period.

**Columns:**
1. Run ID (DM Mono 10px)
2. Timestamp (relative: `3h ago` / `2d ago`)
3. Source (type dot + truncated ID)
4. IPFR Page ID
5. Stage (dot)
6. Verdict (pill: CHANGE_REQUIRED `--state-alert`, UNCERTAIN `--state-warn`, NO_CHANGE `--state-ok`)
7. Confidence (0.00–1.00, DM Mono, coloured by value: low=red, mid=amber, high=green)
8. Bi-Enc Max
9. Cross-Enc
10. Graph Propagated (✓ or —)
11. Feedback (icon state)

**Table features:** Column-header click to sort. Filter-as-you-type search above the table. 20 rows per page. Export as TSV button.

**Row click:** Opens Event Detail Drawer (right-side panel, 640px wide, full height).

#### 7.1.8 Event Detail Drawer

Slides in from the right (250ms ease-out). Contains:

1. **Header strip:** Run ID · Source · IPFR Page · Timestamp in DM Mono 11px. Close button (Esc or ✕).
2. **Stage Journey:** 9 stage dots in a row. Reached stages: solid, coloured. Unreached: hollow. Special markers: `F` superscript if fast-pass triggered, `G` if graph-propagated, `H` if significance=high.
3. **Graph Propagation Trace** (visible only if `graph_propagated: true`): A small SVG showing the hop path — `Source → PageA (score) → PageB (decayed score, hop 1) → PageC (decayed score, hop 2)`. Edge weights shown as line opacity. Decay values in DM Mono 9px alongside each arrow.
4. **Score Panel:** A small table — Signal, Raw Score, Threshold, Pass/Fail — in DM Mono 11px.
5. **Diff Preview:** Normalised diff text in a scrollable monospace block (DM Mono 11px). Added lines (`+`) highlighted with faint `--state-ok` green; removed lines (`-`) faint `--state-alert` red. Max-height 300px, scrollable.
6. **LLM Assessment:** Verdict pill + confidence bar. Full `reasoning` text in Lora 13px. `suggested_changes` as a numbered list in Lora 13px.
7. **Feedback Status:** Submitted feedback category shown, with comment. If no feedback: four clickable feedback options (POST to `/api/feedback/submit` endpoint that appends to `feedback.jsonl`).

---

### 7.2 CORPUS — Influenced Corpus Visualisation

Answers: *what is the shape of the knowledge we're protecting, and how does it connect?*

#### 7.2.1 Panel Row 1 — View Selector + Corpus Stats

Four large Bebas Neue tab labels with underline indicator:
`3D EMBEDDING SPACE · 2D KNOWLEDGE GRAPH · CONTENT MAP · SOURCE-CORPUS MAP`

Stats strip: `PAGES: {n} · CHUNKS: {n} · GRAPH EDGES: {n} · CLUSTERS: {n} · LAST INGESTED: {date}` in DM Mono 11px.

The page count, chunk count, and graph edge count are all drawn from active pages only (`status = 'active'`).

#### 7.2.2 Tab: 3D EMBEDDING SPACE

A full-height Three.js canvas rendering document-level IPFR page embeddings projected into 3D via PCA (computed server-side and served from `/api/pages`).

**Visual spec:**
- Each IPFR page: a sphere, radius 5px
- Colour: cluster assignment (server-side KMeans, 7 clusters, one colour per cluster drawn from the palette)
- Emissive glow intensity proportional to `alert_count` (no alerts = base emissive; max alerts = strong glow)
- A subtle particle field background (300 small points, `--text-tertiary`, `opacity: 0.12`) for depth
- Axes: three lines in `--text-tertiary`, coordinate labels at ±1.0 endpoints in DM Mono 9px
- Grid planes: faint `--rule` at z=0

**Interaction:**
- Mouse drag: orbit camera (OrbitControls-equivalent implemented via manual mouse event listeners — note: `THREE.OrbitControls` is not available via CDN in r128)
- Scroll: zoom in/out
- Hover sphere: floating label (DM Mono 10px) showing page ID and title
- Click sphere: highlight (ring around sphere), populate right-side detail panel
- Shift+click: multi-select; draw a bounding box around selected group (convex hull requires `quickhull3d` npm package — add to §3.2 if implementing, otherwise scope as stretch goal and use axis-aligned bounding box)
- Cluster filter chips below the canvas (one chip per cluster, coloured, toggle visibility)
- Alert filter slider: `Min alerts: {N}` hides pages below threshold
- Animate toggle: slow continuous Y-axis orbit

**Right-side detail panel (280px):**
- Page ID in Bebas Neue 28px + title in Lora 14px
- 3D coordinates in DM Mono 9px
- Chunk count, entity count, degree (graph edges) in DM Mono 11px
- Top 5 keyphrases as small pills
- Alert history: count, last alerted date, verdict distribution
- Mini 1-hop graph (SVG, 180px)
- `VIEW IN GRAPH →` and `VIEW IN TABLE →` links

#### 7.2.3 Tab: 2D KNOWLEDGE GRAPH

D3 force-directed graph of the quasi-graph (all data from `/api/graph/nodes` and `/api/graph/edges`).

**Visual spec:**
- Nodes: circles, radius = 4 + (degree × 1.5), capped at 18px. Fill = cluster colour.
- Labels: page ID in DM Mono 8px (rendered for nodes above a minimum radius; hidden for small nodes, shown on hover)
- Edges coloured by type:
  - Embedding similarity: `--stage-4` (steel blue)
  - Entity overlap: `--stage-3` (amber)
  - Internal links: `--stage-1` (green)
  Edge opacity = edge weight
- Alert pulse: nodes with recent CHANGE_REQUIRED alerts (last 30d) emit a slow expanding ring in `--state-alert`
- Force config: `d3.forceManyBody()` repulsion –200, `d3.forceLink()` attraction, `d3.forceCenter()` gravity, `d3.forceCollide()` separation

**Interaction:**
- Drag nodes (pin on drag, unpin on double-click)
- Hover node: highlight all edges from that node, dim unconnected nodes, show tooltip
- Click node: populate detail panel + pin the node label
- Hover edge: tooltip showing edge type, weight, and both page IDs
- Edge type toggles: three pill buttons above the graph
- Min weight slider: hides edges below threshold
- Search input: type page ID or keyword, matching nodes pulse and pan to view
- Zoom + pan via `d3.zoom()`
- **Pause simulation when tab is not active** (call `.stop()` on the simulation; resume on tab activation). This is mandatory for performance.

#### 7.2.4 Tab: CONTENT MAP

D3 treemap (icicle or squarified layout, user can toggle between them).

- Root: entire corpus
- Level 1: clusters
- Level 2: IPFR pages within each cluster
- Cell fill: cluster colour, brightness encoding alert recency
- Cell size: proportional to chunk count
- Cell label: page ID + truncated title in DM Mono 8px

**Interaction:**
- Click cluster: zoom in (D3 treemap drill-down with smooth 350ms transition)
- Click page: open detail panel
- Breadcrumb: `CORPUS › CLUSTER 3 › B1012`
- Hover: tooltip with page ID, title, chunk count, entity count, alert count
- `SHOW UNCOVERED PAGES` toggle: overlays grey hatching on pages with `alert_count = 0`

#### 7.2.5 Tab: SOURCE-CORPUS MAP (Bipartite)

A D3 force-directed bipartite graph.

- Left column: influencer sources (circles, sized by importance score)
- Right column: IPFR pages (circles, sized by total alert count from sources)
- Edges: connect source → IPFR page for each historical CHANGE_REQUIRED alert
- Edge width encodes alert count; edge colour = source type colour
- Force layout: strong `forceX` positioning force keeps sources left and pages right — tune `forceX` strength empirically against real data (recommended starting range: 0.3–0.8)

**Interaction:**
- Hover source node: highlight all its target IPFR pages, dim others
- Hover IPFR page node: highlight all source nodes that have alerted it
- Click either node type: open detail panel
- Filter by source type using toggle buttons

#### 7.2.6 Snapshot Overlay Panel (below all tabs)

A persistent panel below the four tabs, visible regardless of active tab.

**Purpose:** Compare an influencer source's current snapshot against the most similar IPFR page to see coverage and divergence.

Left column (30%): Source selector list — all sources ordered by last-changed date. Each entry: source ID, source type dot, last checked, health indicator dot.

Right column (70%): Two side-by-side text panels (each ~330px wide, monospace, scrollable, max-height 400px):
- Left panel: current influencer snapshot (plain text from `data/influencer_sources/snapshots/<source_id>/<source_id>.txt`)
- Right panel: most similar IPFR page content (determined by highest bi-encoder score from the last run touching this source)

**Matching passage highlighting — implementation (resolved):** The chunk-level similarity scores needed for highlighting are already stored in `pipeline_runs.details`. No Python bi-encoder is required at dashboard runtime. The server-side implementation for `/api/sources/:source_id/snapshot` should:
1. Query the most recent `pipeline_runs` row for this `source_id` that has `stage_reached >= 5` (i.e. reached bi-encoder)
2. Extract `json_extract(details, '$.stages.biencoder.candidate_pages')` — this is a JSON array; parse it
3. Find the candidate page with the highest `max_chunk_score` — this is the "most similar IPFR page"
4. From the `top_chunk_scores` array within that candidate (stored as `[{chunk_id, score}, ...]`), extract the top chunk IDs
5. Look up those `chunk_id`s in the `chunks` table to retrieve `chunk_text`
6. Return both the source snapshot text, the IPFR page content, and the highlighted chunk texts

The response shape for `/api/sources/:source_id/snapshot`:
```json
{
  "source_id": "TW-SRC-007",
  "snapshot_text": "...",
  "previous_snapshot_text": "...",
  "diff": "...",
  "best_match_page_id": "B1012",
  "best_match_page_content": "...",
  "similarity_score": 0.83,
  "matching_passages": [
    { "source_text": "...", "ipfr_text": "...", "score": 0.81 }
  ]
}
```

**Diff overlay:** Added text underlined `--state-ok` green; deleted text struck through `--state-alert` red.

**Similarity badge** between the two panels: `SIMILARITY: 0.83` in DM Mono.

---

### 7.3 SOURCES — Influencer Source Management

#### 7.3.1 Source Registry Table

Rows = sources from `source_registry.csv`. The actual columns in the CSV are (confirmed from `ingestion/stage1_metadata.py`):

| Column | Type | Display in table |
|---|---|---|
| `source_id` | string | Column 1 — primary identifier |
| `url` | string | Column 2 — truncated with external link icon |
| `title` | string | Shown in expanded row |
| `source_type` | string | Type dot (blue=webpage, amber=frl, green=rss) |
| `importance` | float 0.0–1.0 | Column 3 — thin fill bar |
| `check_frequency` | string (daily/weekly/etc.) | Column 4 — pill |
| `notes` | string | Shown in expanded row |
| `force_selenium` | bool | Shown in expanded row (advanced) |

**Aggregated stats columns** (computed from `pipeline_runs`):
- Last Checked (relative)
- Last Changed (relative)
- Check Health (30-tick sparkline)
- Total Alerts (count)

Row click: expands inline to show full metadata, mini-timeline, per-stage funnel, snapshot text, previous snapshot diff.

**`+ ADD SOURCE` button** opens a modal form. `EDIT` appears on row hover for inline editing. All edits write back to `source_registry.csv` via `POST /api/sources`. The `force_selenium` field is an advanced option, collapsed by default.

---

### 7.4 ADJUST — Configuration Editor

Exposes all parameters from `tripwire_config.yaml` as typed interactive controls. Reads from `GET /api/config`, writes via `POST /api/config`.

#### 7.4.1 Design Conventions

- All labels: DM Mono small caps 10px `--text-secondary`
- All values: DM Mono 13px `--text-primary`
- Numeric inputs: custom steppers with `+`/`−` buttons
- Booleans: custom toggle pill (slides left/right, `--stage-1` fill when ON)
- Sliders: custom range input with DM Mono value readout
- All changes are **staged** (highlighted `--stage-3` amber left border) until `APPLY CHANGES` is clicked
- `APPLY CHANGES` button: posts to `/api/config`, shows success/error feedback
- `RESET TO DEFAULTS` button: restores defaults (requires confirmation modal)
- The `min_score_threshold` control deserves special handling: it can be either a number or `null`. Implement as a toggle that enables/disables the numeric input, writing `null` to the staged config when disabled.

#### 7.4.2 YAML Preview Panel

Collapsible panel at the bottom of the ADJUST section. Shows the current (and staged-but-unsaved) config as a YAML code block. Changed lines have an amber left border. `COPY YAML` button.

#### 7.4.3 Threshold Sensitivity Simulator

A dedicated panel within the ADJUST section, below the config controls.

**Layout:** Two side-by-side sub-panels:

**Left — Simulator Controls:**
- Threshold selector: choose one of `biencoder_high_threshold`, `biencoder_low_medium_threshold`, `crossencoder_threshold`
- Range slider for the selected threshold (full 0.0–1.0 range)
- Date range for the simulation (default: all historical data)

**Right — Simulator Output:**
- A bar chart (Recharts) with two grouped bars per threshold value across the range
- Below the chart: three stat cards — `Estimated Precision`, `Estimated Recall` (relative to current), `Δ Alert Volume`
- The current threshold value is marked with a vertical dashed line and label: `CURRENT`

#### 7.4.4 Info Tooltip System

Every parameter has a `ⓘ` in DM Mono 9px `--text-tertiary` to its right.

**Tooltip spec:**
- Trigger: hover (150ms delay)
- Container: 240px wide, `--bg-secondary` background, `1px solid var(--rule-accent)`, 3px border-radius
- Content: one or two sentences in Lora 12px
- Footer: `Learn more ↗` in DM Mono 10px — navigates to the DOCUMENT section, scrolls to and highlights the relevant anchor with a 1.5s amber glow
- Fade-in animation: 150ms

#### 7.4.5 Config Sections and Parameters

Rendered as collapsible Bebas Neue accordion sections. Each section shows a count of unsaved changes.

---

**Section: PIPELINE BEHAVIOUR**

| Parameter | Control | Info Text | Doc Anchor |
|---|---|---|---|
| `observation_mode` | Toggle | When ON, the pipeline runs all stages but skips LLM calls and sends no alerts. Use during the initial calibration period. | `#doc-observation-mode` |
| `run_frequency_hours` | Number (step 1, min 1, max 168) | How often the pipeline runs, in hours. Default 24. | `#doc-run-frequency` |
| `max_retries` | Number (step 1, min 0, max 5) | How many times a transient failure is retried before the source is skipped. | `#doc-retries` |
| `retry_base_delay_seconds` | Number (step 0.5, min 0.5, max 30) | The base delay for the first retry. Each subsequent retry doubles this value plus random jitter. | `#doc-retry-backoff` |
| `llm_temperature` | Slider (0.0–1.0, step 0.05) | Controls output randomness for the LLM assessment call. Default 0.2. | `#doc-llm-temperature` |
| `llm_model` | Text input | The model identifier passed to the LLM API, e.g. `gpt-4o`. | `#doc-llm-model` |
| `deferred_trigger_max_age_days` | Number (step 1, min 1, max 30) | How long a deferred trigger is held before being discarded. | `#doc-deferred-triggers` |

---

**Section: CHANGE DETECTION — STAGE 2**

| Parameter | Control | Info Text | Doc Anchor |
|---|---|---|---|
| `significance_fingerprint` | Toggle | When ON, Stage 2 uses spaCy and regex to classify changes as high or standard significance. | `#doc-significance-fingerprint` |

---

**Section: RELEVANCE SCORING — STAGE 4**

| Parameter | Control | Info Text | Doc Anchor |
|---|---|---|---|
| `rrf_k` | Number (step 5, min 10, max 200) | Smoothing constant in the Reciprocal Rank Fusion formula. Default 60. | `#doc-rrf-k` |
| `rrf_weight_bm25` | Number (step 0.1, min 0.0, max 5.0) | Weight of the BM25 keyword signal in RRF fusion. | `#doc-rrf-weights` |
| `rrf_weight_semantic` | Number (step 0.1, min 0.0, max 5.0) | Weight of the bi-encoder semantic similarity signal in RRF fusion. Default 2.0. | `#doc-rrf-weights` |
| `top_n_candidates` | Number (step 1, min 1, max 20) | Minimum number of IPFR pages forwarded to semantic matching. | `#doc-top-n` |
| `min_score_threshold` | Number or null (toggle to enable) | Floor score for inclusion beyond the top-N. Null during calibration. | `#doc-min-score-threshold` |
| `source_importance_floor` | Slider (0.0–1.0, step 0.05) | The minimum multiplier applied to any source, regardless of importance. | `#doc-importance-floor` |
| `fast_pass_source_importance_min` | Slider (0.0–1.0, step 0.05) | Sources at or above this importance bypass Stage 4 fusion. | `#doc-fast-pass` |
| `yake_keyphrases_per_80_words` | Number (step 1, min 1, max 5) | Rate of YAKE keyphrase extraction from diffs. | `#doc-yake` |
| `yake_min_keyphrases` | Number (step 1, min 1, max 10) | Minimum keyphrases extracted. | `#doc-yake` |
| `yake_max_keyphrases` | Number (step 1, min 5, max 30) | Maximum keyphrases extracted. | `#doc-yake` |
| `yake_short_diff_threshold` | Number (step 5, min 10, max 200) | Diffs shorter than this word count are supplemented with NER entities. | `#doc-yake` |

---

**Section: SEMANTIC SCORING — STAGES 5–6**

| Parameter | Control | Info Text | Doc Anchor |
|---|---|---|---|
| `biencoder_model` | Text input (read-only, toggle to unlock) | Hugging Face model ID for the bi-encoder. Changing this invalidates all stored embeddings and requires a full re-ingestion. | `#doc-biencoder` |
| `biencoder_high_threshold` | Slider (0.0–1.0, step 0.01) | A single chunk scoring above this cosine similarity triggers the IPFR page. | `#doc-biencoder-thresholds` |
| `biencoder_low_medium_threshold` | Slider (0.0–1.0, step 0.01) | The lower threshold used in the multi-chunk candidate trigger rule. | `#doc-biencoder-thresholds` |
| `biencoder_low_medium_min_chunks` | Number (step 1, min 1, max 10) | Number of chunks that must exceed the low-medium threshold to trigger. | `#doc-biencoder-thresholds` |
| `crossencoder_model` | Text input (read-only, toggle to unlock) | Hugging Face model ID for the cross-encoder reranker. | `#doc-crossencoder` |
| `crossencoder_threshold` | Slider (0.0–1.0, step 0.01) | Minimum cross-encoder score for a candidate to proceed to LLM assessment. | `#doc-crossencoder-threshold` |
| `crossencoder_max_context_tokens` | Number (step 512, min 512, max 16384) | Maximum combined token count passed to the cross-encoder. | `#doc-crossencoder-context` |

---

**Section: GRAPH PROPAGATION — STAGE 6**

| Parameter | Control | Info Text | Doc Anchor |
|---|---|---|---|
| `graph_enabled` | Toggle | Enables alert propagation through the quasi-graph. | `#doc-graph` |
| `graph_max_hops` | Number (step 1, min 1, max 5) | Maximum hops a propagated alert can travel. | `#doc-graph-hops` |
| `graph_decay_per_hop` | Slider (0.0–1.0, step 0.01) | Signal fraction retained at each hop. | `#doc-graph-decay` |
| `graph_propagation_threshold` | Slider (0.0–0.5, step 0.005) | Propagation stops when decayed signal falls below this floor. | `#doc-graph-threshold` |
| `edge_embedding_enabled` | Toggle | Enable/disable embedding-similarity edges in the graph. | `#doc-graph-edges` |
| `edge_embedding_weight` | Slider (0.0–1.0, step 0.05) | Scaling factor applied to embedding-similarity edge weights. | `#doc-graph-edges` |
| `edge_embedding_top_k` | Number (step 1, min 1, max 20) | Each page retains edges to its top-K most similar neighbours. | `#doc-graph-edges` |
| `edge_embedding_min_similarity` | Slider (0.0–1.0, step 0.01) | Minimum cosine similarity for an embedding-similarity edge to be retained. | `#doc-graph-edges` |
| `edge_entity_enabled` | Toggle | Enable/disable entity-overlap edges. | `#doc-graph-edges` |
| `edge_entity_weight` | Slider (0.0–1.0, step 0.05) | Scaling factor applied to entity-overlap edge weights. | `#doc-graph-edges` |
| `edge_entity_min_jaccard` | Slider (0.0–1.0, step 0.01) | Minimum Jaccard coefficient for an entity-overlap edge to be retained. | `#doc-graph-edges` |
| `edge_internal_links_enabled` | Toggle (disabled, greyed) | Internal-link edges. Deferred pending link extraction implementation. | `#doc-graph-edges` |

---

**Section: STORAGE**

| Parameter | Control | Info Text | Doc Anchor |
|---|---|---|---|
| `content_versions_retained` | Number (step 1, min 1, max 20) | Number of previous snapshot versions retained per influencer source. | `#doc-snapshots` |
| `sqlite_wal_mode` | Toggle (always ON, read-only) | SQLite Write-Ahead Logging. Required for concurrent access. Cannot be disabled. | `#doc-sqlite` |
| `git_commit_snapshots` | Toggle | Commit influencer snapshots to the repository after each run. | `#doc-git-persistence` |
| `git_commit_database` | Toggle | Commit the IPFR SQLite database after each ingestion run. | `#doc-git-persistence` |

---

**Section: NOTIFICATIONS**

| Parameter | Control | Info Text | Doc Anchor |
|---|---|---|---|
| `content_owner_email` | Email input | Receives consolidated alert reports after each run. | `#doc-notifications` |
| `health_alert_email` | Email input | Receives system health alerts. | `#doc-health-alerts` |
| `health_error_rate_threshold` | Slider (0.0–1.0, step 0.05) | If the error fraction in a single run exceeds this, a health alert is dispatched. | `#doc-health-alerts` |
| `health_consecutive_failures_threshold` | Number (step 1, min 1, max 10) | A health alert is sent if the same source fails this many consecutive runs. | `#doc-health-alerts` |
| `pipeline_timeout_minutes` | Number (step 5, min 10, max 120) | The GitHub Actions timeout-minutes budget. | `#doc-timeout` |

---

### 7.5 DOCUMENT — System Documentation

A full in-app documentation viewer. The complete text of the Tripwire System Plan is stored as structured data in `src/lib/systemPlan.js`. The DOCUMENT section renders this data into formatted HTML.

#### 7.5.1 Layout

```
[LEFT SIDEBAR — 240px, sticky]  [DOCUMENT BODY — remaining width, scrollable]
```

**Sidebar:** Nested list of sections mirroring the plan's heading hierarchy. Active section highlighted with `--stage-3` amber left border. Click to smooth-scroll. Sections are collapsible at H2 level.

**Document body:**
- Lora serif 16px, `--text-primary`, line-height 1.75
- H1: Bebas Neue 42px. H2: Bebas Neue 28px. H3: Bebas Neue 20px, `--text-secondary`.
- H2 sections have a 3px left border coloured by the relevant pipeline stage
- Code blocks: DM Mono 12px, `--bg-tertiary` background, basic keyword-based syntax highlighting (YAML, SQL, Python, JSON) — no external library
- Tables: hairline-bordered, thead row in Bebas Neue 11px small caps
- The ASCII pipeline diagram from the system plan is rendered as a **styled SVG** reproduction — not raw ASCII. The SVG faithfully replicates the box-and-arrow structure with stage colours, readable stage labels, and arrow connectors.
- Config parameter names appearing inline as `code` spans are rendered as clickable links that navigate to the ADJUST section and highlight the relevant control with an amber pulse

#### 7.5.2 Search

Search input at the top of the sidebar. Searches all section content text. Highlights matching passages. Shows match count: `{N} matches in {M} sections`.

#### 7.5.3 Anchor System

Every config parameter doc anchor (listed in §9 below) must correspond to a `data-anchor` attribute on the relevant section or paragraph.

---

### 7.6 HEALTH — System Health Log

#### 7.6.1 Health Status Strip (Panel Row 1)

Five stat cards (data from `/api/health/summary`):
- **Last Run:** timestamp + duration + outcome pill
- **Error Rate (30d):** percentage + mini 30-day sparkline
- **Sources Monitored:** count
- **LLM Schema Failures (30d):** count. Card highlights at ≥2.
- **Cross-Encoder Truncations (30d):** count. Card highlights at ≥3.

#### 7.6.2 Error Rate Chart (Panel Row 2, 50%)

Recharts area chart. Y axis: daily error rate. X axis: dates. Two horizontal dashed threshold lines (15% and 30%). Hover tooltips.

#### 7.6.3 Consecutive Failures List (Panel Row 2, 50%)

Sources with ≥2 consecutive failures. If no failures: `ALL SOURCES HEALTHY` in Bebas Neue 18px `--state-ok`.

#### 7.6.4 Full Run Log Table (Panel Row 3)

Paginated table, one row per pipeline run (aggregated from per-source rows by `run_id`). Columns: Run ID, Start Time, Duration, Sources Checked, Sources Changed, Sources Errored, Alerts Generated, Status pill. Row click: expands to show per-source breakdown for that run.

#### 7.6.5 Ingestion Health Strip (Panel Row 4)

Data from `/api/health/ingestion`. Shows the most recent IPFR corpus ingestion run: timestamp, pages ingested, pages skipped, stubs detected, duplicates found, keyphrases pruned. This panel tells the operator whether the corpus the pipeline scores against is fresh.

---

## 8. ADDITIONAL VISUALISATIONS

These are first-class features, not optional additions.

### 8.1 Threshold Sensitivity Simulator
Covered in §7.4.3.

### 8.2 Alert Precision Tracker
Covered in §7.1.6.

### 8.3 Source-Corpus Bipartite Map
Covered in §7.2.5.

### 8.4 Coverage Gap View
Covered in §7.2.4.

### 8.5 Graph Propagation Trace Viewer
Covered in §7.1.8.

### 8.6 Calendar Heatmap
Covered in §7.1.3.

---

## 9. SECTION NAVIGATION REFERENCE

| Anchor ID | Document Section |
|---|---|
| `#doc-observation-mode` | §2.3 Observation Mode |
| `#doc-run-frequency` | §7 Configuration — Pipeline behaviour |
| `#doc-retries` | §6.1 Error Classification |
| `#doc-retry-backoff` | §6.1 Error Classification |
| `#doc-llm-temperature` | §3.8 Stage 8 — LLM Assessment |
| `#doc-llm-model` | §3.8 Stage 8 — LLM Assessment |
| `#doc-deferred-triggers` | §6.5 Deferred Triggers |
| `#doc-significance-fingerprint` | §3.2 Stage 2 — Change Detection, Pass 3 |
| `#doc-rrf-k` | §3.4 Stage 4 — Relevance Scoring, Fusion |
| `#doc-rrf-weights` | §3.4 Stage 4 — Relevance Scoring, Fusion |
| `#doc-top-n` | §3.4 Stage 4 — Candidate Selection |
| `#doc-min-score-threshold` | §3.4 Stage 4 — Candidate Selection |
| `#doc-importance-floor` | §3.4 Stage 4 — Source Importance Multiplier |
| `#doc-fast-pass` | §3.4 Stage 4 — Fast-Pass Overrides |
| `#doc-yake` | §3.4 Stage 4 — Signal 1: BM25 |
| `#doc-biencoder` | §3.5 Stage 5 — Semantic Matching: Bi-Encoder |
| `#doc-biencoder-thresholds` | §3.5 Stage 5 — Decision Rule |
| `#doc-crossencoder` | §3.6 Stage 6 — Semantic Matching: Cross-Encoder |
| `#doc-crossencoder-threshold` | §3.6 Stage 6 — Decision Rule |
| `#doc-crossencoder-context` | §3.6 Stage 6 — Process, step 1 |
| `#doc-graph` | §3.6 Stage 6 — Graph Propagation |
| `#doc-graph-hops` | §3.6 Stage 6 — Graph Propagation |
| `#doc-graph-decay` | §3.6 Stage 6 — Graph Propagation |
| `#doc-graph-threshold` | §3.6 Stage 6 — Graph Propagation |
| `#doc-graph-edges` | §4.2 Quasi-Graph Construction |
| `#doc-snapshots` | §3.3 Stage 3 — Diff Generation (Webpage sources) |
| `#doc-sqlite` | §9 SQLite Schema |
| `#doc-git-persistence` | §7.2 Persistent Storage |
| `#doc-notifications` | §3.9 Stage 9 — Notification |
| `#doc-health-alerts` | §6.6 Health Alerting |
| `#doc-timeout` | §6.6 Health Alerting |

---

## 10. SQLITE SCHEMA (confirmed from `ingestion/db.py`)

This section contains the complete, confirmed schema from the live codebase. Use this as the authoritative reference for all Express route queries.

```sql
-- Pages table: one row per IPFR page
-- NOTE: Filter by status = 'active' in all dashboard queries.
--       'stub' = placeholder / too-short page (no embeddings, no chunks)
--       'duplicate' = near-duplicate of another page (duplicate_of points to canonical)
CREATE TABLE IF NOT EXISTS pages (
    page_id         TEXT PRIMARY KEY,     -- e.g. "B1012"
    url             TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    version_hash    TEXT NOT NULL,         -- SHA-256 of normalised plain text
    last_modified   TEXT,                  -- ISO 8601 date from IPFR sitemap
    last_checked    TEXT,                  -- ISO 8601 date of last ingestion check
    last_ingested   TEXT,                  -- ISO 8601 date of last full ingestion
    doc_embedding   BLOB,                  -- document-level embedding (BAAI/bge-base-en-v1.5, float32)
    status          TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'stub' | 'duplicate'
    duplicate_of    TEXT                   -- page_id of canonical page (if status = 'duplicate')
);

-- Chunks table: one row per chunk of each page
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        TEXT PRIMARY KEY,     -- e.g. "B1012-chunk-003"
    page_id         TEXT NOT NULL REFERENCES pages(page_id),
    chunk_text      TEXT NOT NULL,
    chunk_index     INTEGER NOT NULL,     -- positional index within the page
    section_heading TEXT,                  -- nearest heading above this chunk (may be NULL)
    chunk_embedding BLOB NOT NULL         -- chunk-level embedding (BAAI/bge-base-en-v1.5, float32)
);

-- Entities table: named entities extracted per page
CREATE TABLE IF NOT EXISTS entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id         TEXT NOT NULL REFERENCES pages(page_id),
    entity_text     TEXT NOT NULL,
    entity_type     TEXT NOT NULL,         -- e.g. "ORG", "LAW", "GPE", "DATE", "MONEY"
    UNIQUE(page_id, entity_text, entity_type)
);

-- Keyphrases table: YAKE-extracted keyphrases per page
CREATE TABLE IF NOT EXISTS keyphrases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id         TEXT NOT NULL REFERENCES pages(page_id),
    keyphrase       TEXT NOT NULL,
    score           REAL NOT NULL          -- YAKE score (lower = more relevant)
);

-- Graph edges: quasi-graph relationships between IPFR pages
CREATE TABLE IF NOT EXISTS graph_edges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_page_id  TEXT NOT NULL REFERENCES pages(page_id),
    target_page_id  TEXT NOT NULL REFERENCES pages(page_id),
    edge_type       TEXT NOT NULL,         -- "embedding_similarity" | "entity_overlap" | "internal_link"
    weight          REAL NOT NULL,
    UNIQUE(source_page_id, target_page_id, edge_type)
);

-- Section metadata: heading hierarchy per page
CREATE TABLE IF NOT EXISTS sections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id         TEXT NOT NULL REFERENCES pages(page_id),
    heading_text    TEXT NOT NULL,
    heading_level   INTEGER NOT NULL,      -- 1 = H1, 2 = H2, etc.
    char_start      INTEGER NOT NULL,      -- character offset in content
    char_end        INTEGER NOT NULL
);

-- Pipeline run log: one row per SOURCE per run
-- NOTE: stage_reached is a TEXT field, not an integer.
--       See §11.3 for the TEXT-to-integer mapping used by the dashboard API.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL,          -- e.g. "2026-04-05-001" or "2026-04-05-1814"
    source_id        TEXT NOT NULL,
    source_url       TEXT NOT NULL,
    source_type      TEXT NOT NULL,          -- "webpage" | "frl" | "rss"
    timestamp        TEXT NOT NULL,          -- ISO 8601
    stage_reached    TEXT NOT NULL,          -- TEXT: "stage1"|"stage2"|...|"stage6_complete"|"scrape"
    outcome          TEXT NOT NULL,          -- "completed" | "no_change" | "error"
    error_type       TEXT,
    error_message    TEXT,
    triggered_pages  TEXT,                   -- JSON array of page IDs, e.g. '["B1012","C2003"]'
    duration_seconds REAL,
    details          TEXT NOT NULL           -- JSON object with full per-stage data
);

-- Deferred triggers: stored when LLM API is unavailable
CREATE TABLE IF NOT EXISTS deferred_triggers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    ipfr_page_id    TEXT NOT NULL,
    trigger_data    TEXT NOT NULL,          -- JSON object with scores and diffs
    created_at      TEXT NOT NULL,          -- ISO 8601
    processed       INTEGER DEFAULT 0       -- 0 = pending, 1 = processed
);

-- Ingestion run audit log: one row per page per ingestion run
-- Populated by ingestion/ingest.py — useful for HEALTH section corpus freshness
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                    TEXT NOT NULL,
    page_id                   TEXT,          -- NULL for error rows where page_id unknown
    url                       TEXT NOT NULL,
    timestamp                 TEXT NOT NULL,
    outcome                   TEXT NOT NULL, -- "ingested" | "skipped" | "stub" | "error"
    status                    TEXT,          -- "active" | "stub" | "duplicate"
    error_type                TEXT,
    error_message             TEXT,
    chunk_count               INTEGER,
    section_count             INTEGER,
    entity_count              INTEGER,
    keyphrase_count           INTEGER,
    content_length            INTEGER,
    boilerplate_bytes_stripped INTEGER,
    duplicate_of              TEXT,
    warnings                  TEXT,          -- JSON array of warning strings
    duration_seconds          REAL
);

-- Indices for common query patterns
CREATE INDEX IF NOT EXISTS idx_chunks_page_id ON chunks(page_id);
CREATE INDEX IF NOT EXISTS idx_entities_page_id ON entities(page_id);
CREATE INDEX IF NOT EXISTS idx_keyphrases_page_id ON keyphrases(page_id);
CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges(source_page_id);
CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_page_id);
CREATE INDEX IF NOT EXISTS idx_sections_page_id ON sections(page_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_run_id ON pipeline_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_source_id ON pipeline_runs(source_id);
CREATE INDEX IF NOT EXISTS idx_deferred_triggers_processed ON deferred_triggers(processed);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_run_id ON ingestion_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_page_id ON ingestion_runs(page_id);
CREATE INDEX IF NOT EXISTS idx_pages_status ON pages(status);
CREATE INDEX IF NOT EXISTS idx_pages_duplicate_of ON pages(duplicate_of);
```

---

## 11. DATA NOTES FOR IMPLEMENTATION

### 11.1 Embedding Projection (Server-Side)

The `pages` table stores raw embedding BLOBs (`doc_embedding` column) from BAAI/bge-base-en-v1.5 (768 dimensions, stored as float32 bytes). The API server must:

1. On first call to `/api/pages`, deserialise all `doc_embedding` BLOBs into float32 arrays
2. Run PCA to project to 3D and 2D using **`ml-pca`** (npm: `ml-pca`) — not `numeric` (unmaintained since 2016)
3. Cache the projected coordinates in memory, invalidated when the database file's last-modified timestamp changes
4. Return `embedding_3d: [x, y, z]` and `embedding_2d: [x, y]` as normalised floats in [-1, 1]

```js
// dashboard/server/routes/pages.js
import { PCA } from 'ml-pca';

function projectEmbeddings(embeddings) {
  const pca = new PCA(embeddings);
  const proj3d = pca.predict(embeddings, { nComponents: 3 }).to2DArray();
  const proj2d = pca.predict(embeddings, { nComponents: 2 }).to2DArray();
  // normalise to [-1, 1] per axis
  return { proj3d, proj2d };
}
```

Only active pages (`status = 'active'`) with a non-null `doc_embedding` are projected. Stub and duplicate pages are excluded.

### 11.2 Cluster Assignment (Server-Side)

Run K-Means (k=7) on the 2D projected coordinates to assign cluster labels. Cache the result in memory alongside the PCA result. The `cluster` field on each page is this integer (0–6). Assign a deterministic cluster-to-colour mapping.

### 11.3 Details JSON Parsing

The `pipeline_runs.details` column contains a JSON object. The server uses SQLite's `json_extract()` to surface nested fields as top-level columns.

**Important:** The `stage_reached` column is TEXT. Map it to an integer using a CASE expression:

```sql
SELECT
  id, run_id, source_id, source_url, source_type, timestamp,
  CASE stage_reached
    WHEN 'stage1'         THEN 1
    WHEN 'scrape'         THEN 1
    WHEN 'stage2'         THEN 2
    WHEN 'stage3'         THEN 3
    WHEN 'stage4'         THEN 4
    WHEN 'stage5'         THEN 5
    WHEN 'stage6'         THEN 6
    WHEN 'stage6_complete' THEN 6
    ELSE 0
  END AS stage_reached,
  outcome, triggered_pages, duration_seconds,

  json_extract(details, '$.stages.llm_assessment.verdict')           AS verdict,
  json_extract(details, '$.stages.llm_assessment.confidence')        AS confidence,
  json_extract(details, '$.stages.llm_assessment.model')             AS llm_model,
  json_extract(details, '$.stages.llm_assessment.prompt_tokens')     AS prompt_tokens,
  json_extract(details, '$.stages.llm_assessment.completion_tokens') AS completion_tokens,

  -- biencoder_max is inside a JSON array; get max_chunk_score from first candidate
  -- (the array is sorted by max_chunk_score desc in stage5_biencoder.py)
  -- Use json_each() for robust multi-candidate handling in the full routes/runs.js
  json_extract(details, '$.stages.biencoder.candidate_pages[0].max_chunk_score') AS biencoder_max,

  json_extract(details, '$.stages.crossencoder.scored_pages[0].crossencoder_score') AS crossencoder_score,
  json_extract(details, '$.stages.crossencoder.scored_pages[0].reranked_score')     AS reranked_score,

  json_extract(details, '$.stages.relevance.rrf_score')             AS rrf_score,
  json_extract(details, '$.stages.relevance.source_importance')     AS source_importance,
  json_extract(details, '$.stages.relevance.fast_pass_triggered')   AS fast_pass_triggered,
  json_extract(details, '$.stages.change_detection.significance')   AS significance_tag,
  json_extract(details, '$.stages.llm_assessment.reasoning')        AS reasoning

FROM pipeline_runs
```

For `biencoder_candidates_json` and `crossencoder_scores_json` (used in the Event Detail Drawer), return the full JSON array string and parse it in the Express route:

```sql
json_extract(details, '$.stages.biencoder.candidate_pages')  AS biencoder_candidates_json,
json_extract(details, '$.stages.crossencoder.scored_pages')  AS crossencoder_scores_json
```

If any `json_extract()` path is absent, return `null`. The frontend must handle nulls gracefully — display `—` in tables, skip in charts.

### 11.4 Feedback Integration

`feedback.jsonl` is a newline-delimited JSON file at **`data/logs/feedback.jsonl`** relative to `DATA_ROOT`. The server reads this file on each `/api/runs/feedback` request (no caching needed given low volume). Each line has at minimum: `run_id`, `page_id`, `source_id`, `category` (`useful` / `not_significant` / `wrong_amendment` / `wrong_page`), `comment`, `ingested_at`.

The `POST /api/feedback/submit` endpoint appends a new JSON line to `feedback.jsonl`.

### 11.5 Source Registry CSV Schema

The `source_registry.csv` file at `data/influencer_sources/source_registry.csv` (relative to `DATA_ROOT`) has the following columns, confirmed from `src/stage1_metadata.py`:

| Column | Type | Notes |
|---|---|---|
| `source_id` | string | Unique identifier. No spaces; use underscores. |
| `url` | string | Full HTTPS URL to the resource. |
| `title` | string | Human-readable name for display. |
| `source_type` | string | `"webpage"` \| `"frl"` \| `"rss"` |
| `importance` | float | 0.0–1.0. Sources at 1.0 trigger fast-pass in Stage 4. |
| `check_frequency` | string | `"daily"` \| `"weekly"` \| `"fortnightly"` \| `"monthly"` \| `"quarterly"` |
| `notes` | string | Free text. May be empty. |
| `force_selenium` | bool | `"true"` or `"false"`. Forces Selenium fetch even if requests succeeds. |

The `POST /api/sources` endpoint must preserve all existing columns when writing updated records. It must not silently drop any column not in its own schema.

### 11.6 Snapshot Overlay — Chunk Matching Data Path

The snapshot overlay in §7.2.6 requires highlighting matching passages between a source snapshot and an IPFR page. This data is pre-computed and stored in `pipeline_runs.details` — no runtime ML inference is required.

The server-side query for `/api/sources/:source_id/snapshot`:

```sql
-- Step 1: find the most recent run for this source that reached the bi-encoder
SELECT id, run_id, details
FROM pipeline_runs
WHERE source_id = ?
  AND CASE stage_reached
        WHEN 'stage5' THEN 1
        WHEN 'stage6' THEN 1
        WHEN 'stage6_complete' THEN 1
        ELSE 0
      END = 1
ORDER BY timestamp DESC
LIMIT 1
```

Then in JavaScript:
```js
const biencoder = JSON.parse(run.details).stages?.biencoder;
const candidates = biencoder?.candidate_pages || [];
// Sort by max_chunk_score desc, take first
const bestPage = candidates.sort((a, b) => b.max_chunk_score - a.max_chunk_score)[0];
const topChunkIds = (bestPage?.top_chunk_scores || []).map(c => c.chunk_id);

// Step 2: look up chunk text for matching passages
// (use placeholders for the chunk IDs)
const chunkRows = db.prepare(
  `SELECT chunk_id, chunk_text FROM chunks WHERE chunk_id IN (${topChunkIds.map(() => '?').join(',')})`
).all(...topChunkIds);
```

The `top_chunk_scores` array in `PageBiEncoderResult` (from `stage5_biencoder.py`) stores the top-5 corpus chunk scores per page: `[{ chunk_id, score }]`. These chunk IDs reference the `chunks` table directly.

### 11.7 Express `--serve-build` Static File Serving

When `process.argv.includes('--serve-build')`, the Express server also serves the Vite build output as static files:

```js
// dashboard/server/index.js
import path from 'path';
import { fileURLToPath } from 'url';
import express from 'express';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();

// ... API routes registered first ...

if (process.argv.includes('--serve-build')) {
  const distDir = path.join(__dirname, '..', 'dist');
  app.use(express.static(distDir));
  // SPA fallback: serve index.html for any non-API route
  app.get('*', (req, res) => {
    res.sendFile(path.join(distDir, 'index.html'));
  });
}
```

---

## 12. DEPLOYMENT & AUTH IMPLEMENTATION NOTES

### 12.1 Full Express Server Entry Point Pattern

```js
// dashboard/server/index.js
import express from 'express';
import cors from 'cors';
import path from 'path';
import { fileURLToPath } from 'url';
import { basicAuth } from './auth.js';

// Routes
import runsRouter from './routes/runs.js';
import pagesRouter from './routes/pages.js';
import sourcesRouter from './routes/sources.js';
import configRouter from './routes/config.js';
import graphRouter from './routes/graph.js';
import snapshotsRouter from './routes/snapshots.js';
import healthRouter from './routes/health.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = process.env.PORT || 3001;

// CORS — permissive in dev, locked to DASHBOARD_ORIGIN in production
const corsOptions = process.env.NODE_ENV === 'production'
  ? { origin: process.env.DASHBOARD_ORIGIN }
  : { origin: true };
app.use(cors(corsOptions));

app.use(express.json());

// Auth — must come before all routes
app.use(basicAuth);

// API routes
app.use('/api/runs', runsRouter);
app.use('/api/pages', pagesRouter);
app.use('/api/sources', sourcesRouter);
app.use('/api/config', configRouter);
app.use('/api/graph', graphRouter);
app.use('/api/snapshots', snapshotsRouter);
app.use('/api/health', healthRouter);

// Static file serving for production build
if (process.argv.includes('--serve-build')) {
  const distDir = path.join(__dirname, '..', 'dist');
  app.use(express.static(distDir));
  app.get('*', (req, res) => {
    res.sendFile(path.join(distDir, 'index.html'));
  });
}

app.listen(PORT, () => {
  console.log(`Tripwire Dashboard server running on port ${PORT}`);
});
```

### 12.2 Render Deployment Steps

1. Push the `tripwire/` repository to GitHub (if not already there).
2. In the Render dashboard, create a new **Web Service** connected to the GitHub repository.
3. Set **Build Command** to `cd dashboard && npm install && npm run build`.
4. Set **Start Command** to `cd dashboard && npm start`.
5. Add a **Persistent Disk** at mount path `/data`, size 1 GB.
6. Set environment variables: `DASHBOARD_USER`, `DASHBOARD_PASS`, `DASHBOARD_ORIGIN`, `DATA_ROOT=/data`, `NODE_ENV=production`.
7. Deploy. Render provides a URL like `https://tripwire-dashboard.onrender.com`.
8. Set `DASHBOARD_ORIGIN` to that URL (update and redeploy once the URL is known).
9. Share the URL and credentials with team members.

**Render free tier note:** The service spins down after 15 minutes of inactivity. The first request after a spin-down takes ~30 seconds (cold start). This is fine for a small internal team. If cold starts become disruptive, the free tier can be upgraded to Render Starter ($7/month) which eliminates spin-down.

### 12.3 GitHub Actions — Database Sync to Render Persistent Disk

After each Tripwire pipeline run, GitHub Actions syncs the updated SQLite database and related data files to the Render persistent disk via `rsync` over SSH.

**Prerequisites:**
- Generate an SSH key pair. Add the private key as a GitHub Actions secret (`RENDER_SSH_KEY`).
- Add the public key to the Render persistent disk's SSH configuration (via Render's SSH access feature, or by using Render's deploy hooks + a sync script).

**GitHub Actions step:**

```yaml
# .github/workflows/tripwire.yml (append to existing pipeline workflow)
- name: Sync data to Render persistent disk
  env:
    RENDER_SSH_KEY: ${{ secrets.RENDER_SSH_KEY }}
    RENDER_SSH_HOST: ${{ secrets.RENDER_SSH_HOST }}   # Render SSH host for the service
    RENDER_SSH_USER: ${{ secrets.RENDER_SSH_USER }}   # Render SSH user
  run: |
    mkdir -p ~/.ssh
    echo "$RENDER_SSH_KEY" > ~/.ssh/render_key
    chmod 600 ~/.ssh/render_key
    rsync -avz --delete \
      -e "ssh -i ~/.ssh/render_key -o StrictHostKeyChecking=no" \
      data/ipfr_corpus/ipfr.sqlite \
      data/influencer_sources/ \
      data/logs/ \
      tripwire_config.yaml \
      ${RENDER_SSH_USER}@${RENDER_SSH_HOST}:/data/
```

**Alternative approach (simpler, no SSH key management):** If SSH access to the Render disk proves complex to configure, use Render's [Deploy Hook](https://render.com/docs/deploy-hooks) in combination with uploading the database as a GitHub release asset, and have the Express server download the latest release asset on startup if the local copy is stale. This trades simplicity of setup for a slightly longer cold-start. Implement whichever approach the team finds easier to maintain.

### 12.4 Vite Configuration for Production

In production, the React app is served by Express from the same origin, so API calls are relative (no `http://localhost:3001` prefix). Configure Vite's dev proxy and production base:

```js
// dashboard/vite.config.js
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:3001'  // dev only — proxies to Express
    }
  },
  base: '/'
});
```

In `useData.js` (the React Query fetch layer), all API calls should use relative URLs (`/api/runs`, not `http://localhost:3001/api/runs`). The Vite dev proxy handles local development; the production Express server handles deployment.

```js
// dashboard/src/hooks/useData.js
const API_BASE = '';  // relative — works in both dev (via Vite proxy) and prod (same origin)
```

### 12.5 Environment Variable Reference (Complete)

| Variable | Required in | Default | Purpose |
|---|---|---|---|
| `DASHBOARD_USER` | Production | — | Basic Auth username |
| `DASHBOARD_PASS` | Production | — | Basic Auth password |
| `DASHBOARD_ORIGIN` | Production | — | CORS allowed origin (full URL) |
| `DATA_ROOT` | Production | `../../` (from server/) | Absolute path to data directory |
| `NODE_ENV` | Production | `development` | Enables auth, locks CORS |
| `PORT` | Both | `3001` | Express listen port (Render sets this automatically) |

---

## 13. PERFORMANCE REQUIREMENTS

- **Timeline swimlane:** Render only ticks within the visible scroll viewport. Use `IntersectionObserver` or scroll event + manual windowing. Never render all 80 source rows × 500 ticks simultaneously.
- **3D Three.js:** `useRef` for the canvas, clean up renderer on unmount. Pause animation loop when section is not active (track active section in global state).
- **D3 force simulation:** Pause simulation (`.stop()`) when the Knowledge Graph tab is not visible. Resume on tab activation.
- **React Query caching:** Set `staleTime: 5 * 60 * 1000` (5 minutes) for all API calls. The database changes only once per day after a pipeline run. Background refetch can be triggered manually via a `REFRESH` button in the topbar.
- **`useMemo` throughout:** All filtered/sorted/aggregated data derivations in `useFilters.js` and `dataUtils.js` must be memoised.
- **PCA cache invalidation:** Cache the PCA + KMeans result in memory with the SQLite file's `mtime` as the key. Recompute only when the mtime changes (i.e. after a new ingestion run).
- **React error boundaries:** Wrap each visualisation component (`Embedding3D`, `KnowledgeGraph`, `ContentMap`, `BipartiteMap`) in an error boundary so a crash in one visualisation does not unmount the entire section.

---

## 14. ANIMATION SPECIFICATION

| Element | Animation | Duration | Easing |
|---|---|---|---|
| Section transition | Fade + 8px upward translate | 200ms | ease-out |
| Event Detail Drawer | Slide in from right | 250ms | ease-out |
| Tooltip reveal | Fade in | 150ms | ease |
| 3D scene (animate mode) | Continuous Y-axis orbit | ∞ | linear |
| Config change staging | Amber left border, fade in | 200ms | ease |
| Document anchor arrival | Background amber glow on target | 1.5s | ease-out |
| Health alert card | Slow pulse (opacity 1.0 → 0.7 → 1.0) | 2s | ease-in-out, infinite |
| Graph node alert pulse | Expanding ring, `--state-alert` | 2s | ease-out, infinite |
| Treemap drill-down | D3 transition, scale + opacity | 350ms | ease |

---

## 15. VISUAL QUALITY CHECKLIST

Before implementation is considered complete:

- [ ] Colour palette strictly applied — no white backgrounds, no default browser-blue links
- [ ] All numerical data rendered in DM Mono, not serif or system sans-serif
- [ ] All headings in Bebas Neue
- [ ] Hairline rules separate every logical group
- [ ] Hard corners on all structural panels; border-radius only on pills/badges (max 2px)
- [ ] Grain texture overlay visible but subtle (`opacity: 0.025`)
- [ ] Stage colours consistently applied across all panels referencing stages
- [ ] Stage funnel bars labelled by position/name (not colour alone) — stages 1&7, 2&8, 3&9 share colour
- [ ] All ADJUST tooltips include `Learn more ↗` links with correct anchors
- [ ] Config diff preview shows staged changes in amber
- [ ] YAML preview panel reflects staged changes in real-time
- [ ] `min_score_threshold` renders as toggle + numeric input (null = disabled)
- [ ] 3D view: orbit on drag, zoom on scroll, sphere glow encodes alert count
- [ ] Knowledge graph: nodes are draggable, edge types toggleable, simulation paused when tab inactive
- [ ] Treemap: drill-down works with breadcrumb, coverage gap toggle works
- [ ] Bipartite map: hover highlights correct connections
- [ ] Calendar heatmap: correct colour density, hover tooltips
- [ ] Threshold simulator: chart reacts to slider in real-time
- [ ] Precision tracker: renders correctly with and without feedback data
- [ ] Graph propagation trace: visible in Event Detail Drawer for propagated alerts
- [ ] Snapshot overlay: two-panel diff with matching passages highlighted
- [ ] Document section: SVG pipeline diagram rendered (not ASCII), all anchor targets present
- [ ] Config parameter inline-code links in Document navigate to ADJUST and pulse the control
- [ ] Filter bar in OBSERVE drives all panels reactively
- [ ] React Query caching with manual refresh trigger in topbar
- [ ] Mobile viewport: nav rail collapses to icons, OBSERVE panels stack, 3D view replaced by message
- [ ] Pages queries filter by `status = 'active'` — stubs and duplicates excluded from all corpus views
- [ ] Corpus stats show only active page counts
- [ ] Error boundaries wrap all Three.js and D3 visualisation components
- [ ] `/api/sources` POST endpoint writes to `source_registry.csv` (add source flow works end-to-end)
- [ ] `feedback.jsonl` path resolved from `DATA_ROOT`
- [ ] All API routes return 401 if `Authorization` header is absent or incorrect
- [ ] Basic Auth skipped in development when `DASHBOARD_USER`/`DASHBOARD_PASS` are not set
- [ ] Vite API proxy configured for local dev (`/api` → `localhost:3001`)
- [ ] All API calls in frontend use relative URLs (no hardcoded `localhost`)
- [ ] `DATA_ROOT` environment variable respected for all data file path resolution

---

## 16. PROMPT TO CLAUDE CODE

Hand Claude Code this brief along with the Tripwire System Plan document and use the following prompt:

---

*"Build the Tripwire Dashboard as specified in the attached design and engineering brief (v3.0). This is a Vite + React frontend backed by an Express API server, both living inside the `tripwire/dashboard/` directory. The app is deployed as a single Node.js service on Render; the same Express process serves both the API and the built React frontend. No mock data — all data comes from the real SQLite database and config file, resolved via the `DATA_ROOT` environment variable (see §3.4).*

*The brief specifies every component, API route, data shape, visual design decision, and interaction behaviour in full detail. Follow it precisely.*

*Build all files as specified in the repository structure (Section 2). Use the libraries listed in Section 3. Do not add libraries not listed. Do not use any external component library (no MUI, Chakra, etc.).*

*Priorities in order:*

*1. Establish the project structure, `package.json`, Express server with all routes, database connection layer, and the `auth.js` Basic Auth middleware (§3.7, §12.1) first. Apply `basicAuth` before all routes. Every API route must handle missing fields gracefully (null-safe). Note: `pipeline_runs.stage_reached` is a TEXT field — map it to integers using the CASE expression in §11.3 before returning it to the frontend.*

*2. Build the global shell: Vite config with API proxy for dev (§12.4), `index.html` (with Google Fonts), `globals.css` with all CSS variables, `App.jsx` with router and global state context, `NavRail.jsx`, `Topbar.jsx`. Wrap all visualisation-heavy section components in React error boundaries. All API calls in the frontend must use relative URLs.*

*3. Build each section in order: OBSERVE → CORPUS → SOURCES → ADJUST → DOCUMENT → HEALTH. For each section, implement the data fetching hook first, then the layout, then each panel.*

*4. Implement the CORPUS visualisation tabs (3D, Knowledge Graph, Content Map, Bipartite Map) — these are the most technically complex components. The 3D view uses Three.js r128. The graph, treemap, and bipartite map use D3 v7. Pause D3 simulations when their containing tab is not active.*

*5. Implement the server-side PCA and K-Means for embedding projection and clustering using `ml-pca` (not `numeric`). Cache the result with SQLite file mtime as the invalidation key. Query only `status = 'active'` pages for all corpus operations.*

*6. Wire up the ADJUST section with the full tooltip/Learn more system, the YAML preview panel, and the Threshold Sensitivity Simulator. The `min_score_threshold` parameter requires a toggle + nullable number control.*

*7. Implement `POST /api/sources` for the source registry using `csv-parse` and `csv-stringify`. Implement the snapshot overlay chunk-matching logic from §11.6 — the data is already in `pipeline_runs.details` and requires no runtime ML inference.*

*8. Render the full Tripwire System Plan document content in the DOCUMENT section. The pipeline architecture diagram must be rendered as an SVG, not ASCII. Every config parameter code span must be a clickable link navigating to ADJUST.*

*All data paths (SQLite DB, config YAML, feedback.jsonl, source registry CSV, snapshots directory) must be resolved from `process.env.DATA_ROOT` using the constants defined in `dashboard/server/db.js` (§3.4). See §12 for the complete deployment and auth implementation.*

*The result must feel like a bespoke government intelligence terminal — Bebas Neue headings, DM Mono data, Lora body text, near-black palette, hairline rules, hard corners, no rounded panels, subtle grain texture. Not a SaaS dashboard."*

---

*End of Brief — v3.0*
