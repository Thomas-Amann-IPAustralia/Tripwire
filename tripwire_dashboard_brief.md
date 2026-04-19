# TRIPWIRE DASHBOARD
## Design & Engineering Brief for Claude Code

**Version:** 1.0  
**Author:** Thomas Amann, IPAVentures  
**Date:** April 2026  
**Deliverable:** A single-file React `.jsx` artifact — no separate CSS or JS files

---

## 1. PROJECT CONTEXT

Tripwire is an autonomous change-monitoring pipeline that watches external IP legislation and regulatory sources ("influencers") and determines whether changes to those sources require amendments to content on the IP First Response (IPFR) website ("the influenced corpus"). The system runs as a scheduled GitHub Actions workflow and produces SQLite-persisted run logs, embeddings, and a quasi-graph of relationships between IPFR pages.

The dashboard built from this brief serves one primary user: **a solo technical operator** who is also the content owner. The dashboard must therefore do two things simultaneously — be a powerful analytical instrument AND be pleasant to live with every working day.

---

## 2. AESTHETIC DIRECTION

### 2.1 Reference Images

Three reference images have been provided. Extract the following from each:

**Image 1 — IPAVentures & GenAI slide (dark, typographic):**
- Near-black background (`#0f0f0f` or `#111`)
- Large, heavy, slightly distressed sans-serif display type (reminiscent of grotesque newsprint)
- Warm off-white / bone text (`#e8e2d4`)
- Discrete colour dot motif — green, orange/red, yellow, blue, mid-grey, light-grey — used as categorical accent colours (map these to pipeline stages)
- Fine rule lines as structural dividers
- Bottom-anchored metadata strip in small caps

**Image 2 — Entrace Group layout (Swiss/editorial, constructivist):**
- Light grey ground (`#e8e4dc`)
- Brutalist-editorial grid with extreme typographic scale contrast
- Barcode / data-label aesthetics — alphanumeric codes, version strings, coordinate-like numbers
- Orange (`#F05A28`), blue (`#2B5EE8`), yellow accents
- Image placeholder boxes with crossing diagonals
- Dense information at the edges, breathing space in the middle

**Image 3 — Tesla/Electric Current poster (technical diagram, monochrome):**
- Mid-grey ground
- Thin-weight engineering diagram with labelled parts
- Restrained typographic hierarchy — small caps, generous tracking, light weight
- Coordinates and version numbers as decorative data elements

### 2.2 Synthesis: The Design Direction

**"Government Intelligence Terminal"** — imagine a bespoke internal tool built for a well-funded government analytical unit in 1997 and then restored by a Swiss designer in 2024. It should feel:

- **Serious without being sterile.** Dense with information, yet every element earns its place.
- **Editorial in its typography.** Not a SaaS dashboard. Something between a printed policy brief and a mission control terminal.
- **Colour-coded but not garish.** The six dot colours from Image 1 map to the nine pipeline stages. Used with restraint.

### 2.3 Colour Palette (CSS Variables)

```css
--bg-primary:    #111110;    /* near-black ground — Image 1 */
--bg-secondary:  #1a1a18;    /* panel backgrounds */
--bg-tertiary:   #242420;    /* input fields, card interiors */
--bg-accent:     #2e2e28;    /* hover states, highlights */

--text-primary:  #e8e2d4;    /* bone white — Image 1 */
--text-secondary: #9e9888;   /* muted labels */
--text-tertiary:  #5c5a52;   /* disabled / decorative */

--rule:          #2e2e28;    /* hairline dividers */
--rule-accent:   #4a4a40;    /* stronger dividers */

/* Stage / Category Accent Colours — from Image 1 dots */
--stage-1:   #3a6b3a;   /* deep green — Metadata Probe */
--stage-2:   #c94020;   /* rust orange — Change Detection */
--stage-3:   #d4a820;   /* amber — Diff Generation */
--stage-4:   #4a7ab5;   /* steel blue — Relevance Scoring */
--stage-5:   #4a4a40;   /* dark grey — Bi-Encoder */
--stage-6:   #7a7a70;   /* mid grey — Cross-Encoder */
--stage-7:   #3a6b3a;   /* (reuse green) — Aggregation */
--stage-8:   #c94020;   /* (reuse orange) — LLM Assessment */
--stage-9:   #d4a820;   /* (reuse amber) — Notification */

/* Semantic states */
--state-alert:    #c94020;   /* CHANGE_REQUIRED */
--state-warn:     #d4a820;   /* UNCERTAIN */
--state-ok:       #3a6b3a;   /* NO_CHANGE */
--state-error:    #8b1a1a;   /* pipeline error */
--state-inactive: #5c5a52;   /* no data / skipped */
```

### 2.4 Typography

Use Google Fonts, loaded in the artifact's `<head>` equivalent via `@import`:

- **Display / Headings:** `'Bebas Neue'` — heavy caps, consistent with Image 1's title weight
- **UI / Labels:** `'DM Mono'` — monospace, for all data values, codes, timestamps, scores
- **Body / Descriptions:** `'Lora'` — a serif for the document section and tooltips, giving a policy-brief quality contrast to the mono data elements

```css
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Mono:wght@300;400;500&family=Lora:ital,wght@0,400;0,500;1,400&display=swap');

--font-display: 'Bebas Neue', sans-serif;
--font-mono:    'DM Mono', monospace;
--font-body:    'Lora', serif;
```

### 2.5 Motifs & Details

- **Fine hairline rules** (`1px solid var(--rule)`) used extensively as structural separators
- **Alphanumeric labels** in the style of Image 2 — `TW-S1`, `TW-S4`, run IDs like `2026-04-05-001` — displayed in small DM Mono as decorative data identifiers
- **Barcode-style visual encoding** — narrow vertical bars representing source check cadences
- **Stage indicator dots** — 9 dots, coloured by `--stage-N`, used inline as categorical markers throughout
- **Coordinate-style numbers** (lat/long inspired) for vector embedding positions in the 3D view
- **Grain texture overlay** on panel backgrounds using an SVG noise filter for depth (subtle, `opacity: 0.03`)
- **No border-radius on structural containers.** Hard corners only. Rounded corners only for small pills/badges.

---

## 3. LAYOUT ARCHITECTURE

### 3.1 Top-Level Structure

The dashboard is a **single-page application** with a persistent left navigation rail and a main content area. No top navigation bar. The nav rail is narrow (56px collapsed, 200px expanded).

```
┌──────┬───────────────────────────────────────────────────────┐
│      │  TOPBAR: Dashboard identity + run status strip        │
│ NAV  ├───────────────────────────────────────────────────────┤
│ RAIL │                                                       │
│      │           MAIN CONTENT AREA                          │
│      │           (changes per selected section)             │
│      │                                                       │
└──────┴───────────────────────────────────────────────────────┘
```

**Navigation Rail Items** (icons + labels):

1. `OBSERVE` — Pipeline analytics (the main view)
2. `CORPUS` — Influenced corpus visualisation
3. `SOURCES` — Influencer source management
4. `ADJUST` — Configuration editor
5. `DOCUMENT` — System documentation
6. `HEALTH` — System health log

The active section is indicated by a left-side coloured bar using the section's accent colour. All nav labels are in Bebas Neue, tracked out at 0.15em.

### 3.2 Topbar

A 48px high strip. Left-anchored: logo mark (a small `◈` symbol) + `TRIPWIRE` in Bebas Neue at 18px + a version/build string (`v0.1 · TW-DASHBOARD`). Right-anchored: last run timestamp, pipeline status pill (RUNNING / IDLE / ERROR), and a small 9-dot stage indicator showing which stages completed in the most recent run.

### 3.3 Content Area Anatomy

Each section has:
- A **section header band** — full-width, 80px tall, containing the section title in Bebas Neue at 42px, a one-line description in DM Mono at 11px, and a contextual action area (filters, buttons) on the right
- **Content panels** below, arranged in a CSS Grid

---

## 4. SECTION SPECIFICATIONS

---

### 4.1 OBSERVE — Pipeline Analytics

This is the primary view. It answers: *what has happened, to what depth, and what was found?*

#### 4.1.1 Global Filter Bar

A persistent strip immediately below the section header, before all panels. Contains:

- **Date range picker** — preset chips: `7D · 30D · 90D · ALL` + a custom range calendar
- **Source filter** — multi-select dropdown populated from the source registry
- **Stage filter** — multi-select chip row showing all 9 stages with their colour dots; selecting stages filters visualisations to show only events that reached those stages
- **Verdict filter** — chips: `CHANGE_REQUIRED · UNCERTAIN · NO_CHANGE · ERROR`
- **Reset filters** link (DM Mono, 10px)

All panels below respond to these filters reactively.

#### 4.1.2 Panel Layout — OBSERVE

```
ROW 1: [FUNNEL SUMMARY — full width]

ROW 2: [TIMELINE — full width]

ROW 3: [SOURCE MATRIX — 60%] [STAGE BREAKDOWN — 40%]

ROW 4: [TRIGGERED EVENTS TABLE — full width]
```

---

#### Panel: FUNNEL SUMMARY

A horizontal funnel visualisation showing total events at each stage across the filtered date range.

**Visual spec:**
- 9 vertical columns, one per stage, labelled `S1` through `S9`
- Each column is a thin bar whose **height** encodes the count of events that reached or passed that stage
- Bars are coloured by `--stage-N`
- The "funnel" effect is achieved by decreasing height from left to right (S1 tallest, S9 shortest)
- Above each bar: the count in DM Mono 11px
- Below each bar: the stage short-name in Bebas Neue 10px
- Between bars: a thin arrow glyph `›` in `--text-tertiary`
- A horizontal dashed baseline rule
- Hover on any bar: show a tooltip with the stage full name, count, and percentage pass-through rate from the previous stage

**Interaction:** Clicking a stage bar adds that stage to the global Stage filter.

---

#### Panel: TIMELINE

A two-track timeline showing:
- **Track 1 (top):** For each influencer source, a horizontal swimlane showing check events as small vertical ticks coloured by the deepest stage reached (stage colour). Ticks cluster when multiple checks happen close together.
- **Track 2 (bottom):** A bar chart of daily alert volume (CHANGE_REQUIRED verdicts per day) overlaid with a line for total checks per day

**Visual spec:**
- X axis: time (date labels in DM Mono 10px)
- Y axis (Track 1): source names in DM Mono 9px, sorted by check frequency descending
- Swimlane height: 16px per source, with 4px gap
- The stage-coloured ticks are 2px wide, 12px tall
- Hover on a tick: show a popover with run ID, source, stage reached, verdict, and score
- Click on a tick: opens the TRIGGERED EVENTS TABLE filtered to that specific run

**Source swimlane depth indicator:** To the right of each swimlane, a small mini-funnel (9 dots, sized by proportion) showing the historical distribution of max stage reached for that source across all runs in the filtered period.

---

#### Panel: SOURCE MATRIX

A grid where:
- **Rows** = influencer sources (from source_registry.csv)
- **Columns** = pipeline stages (S1–S9)
- **Cells** = coloured by the most common outcome for that source × stage combination

Cell colour key:
- Solid stage colour: stage was reached and passed
- 50% opacity stage colour: stage was reached but failed/rejected
- `--bg-tertiary`: stage was never reached
- `--state-error`: error at this stage

Cell hover: tooltip showing exact counts (passed / failed / errored) for that source × stage combination.

Row hover: highlight the entire row. Click: filter the entire view to that source.

To the left of each row: a small coloured bar indicating source importance (0.0 → 1.0, encoded as bar height within the cell). Source name truncated to 24 chars.

---

#### Panel: STAGE BREAKDOWN

A set of small donut charts, one per stage (3×3 grid), each showing the outcome distribution for that stage (passed / rejected / errored / skipped) in the filtered date range. Stage number in Bebas Neue at 24px in the centre of each donut. Below each donut: total events in DM Mono 9px.

---

#### Panel: TRIGGERED EVENTS TABLE

A data table listing every event that reached Stage 8 (LLM Assessment) in the filtered period.

**Columns:**
1. Run ID (`DM Mono 10px`)
2. Source (truncated, with importance colour dot)
3. IPFR Page ID
4. Max Stage Reached (stage dot)
5. Verdict (coloured pill: `CHANGE_REQUIRED` in `--state-alert`, `UNCERTAIN` in `--state-warn`, `NO_CHANGE` in `--state-ok`)
6. Confidence (0.00–1.00, DM Mono)
7. Bi-Encoder Max Score
8. Cross-Encoder Score
9. Feedback (icon: ✓ useful / ✗ not / ? no feedback yet)

**Row interactions:**
- Click row: expand inline to show the full LLM reasoning text, suggested changes, and diff preview (as a collapsible code block with diff syntax colouring: `+` lines in faint green, `-` lines in faint red)
- The expanded row shows all scoring evidence in a small grid

**Table features:**
- Column-click sorting
- Row-level search (filter-as-you-type input above the table, searches across source, page ID, reasoning text)
- Paginated: 20 rows per page, page selector in DM Mono
- Export button: copies filtered data as TSV to clipboard

---

#### Deep Dive Layer — Event Detail Drawer

When a user clicks an event row in the Triggered Events Table, a **right-side drawer** slides in (640px wide, full height minus topbar). The drawer renders the full event narrative:

1. **Header:** Run ID · Source · IPFR Page ID · Timestamp — all in DM Mono 11px
2. **Stage Journey:** A horizontal sequence of 9 stage dots. Stages reached are solid; unreached are hollow. Stages where something notable happened (hash changed, fingerprint matched, fast-pass triggered) show a small superscript marker.
3. **Diff Preview:** The normalised diff text in a scrollable monospace block. Added lines (`+`) faintly highlighted green; removed lines (`-`) faintly highlighted red.
4. **Score Panel:** A mini table — Stage, Signal, Raw Score, Threshold, Pass/Fail. All in DM Mono.
5. **LLM Assessment:** Verdict pill, confidence bar, full reasoning text in Lora serif, suggested changes as a numbered list.
6. **Feedback Status:** Which feedback category was submitted (if any), with the comment text.

Close with Esc or by clicking the backdrop.

---

### 4.2 CORPUS — Influenced Corpus Visualisation

This section provides three interactive visual representations of the IPFR corpus and its relationship to influencer sources.

#### 4.2.1 Panel Layout

```
ROW 1: [VIEW SELECTOR TABS] [CORPUS STATS STRIP]

ROW 2: [PRIMARY VISUALISATION — full width, tall]

ROW 3: [SNAPSHOT OVERLAY PANEL — full width]
```

#### 4.2.2 View Selector Tabs

Three tabs, styled as large Bebas Neue text with an underline indicator:

- `3D EMBEDDING SPACE`
- `2D KNOWLEDGE GRAPH`
- `CONTENT MAP`

#### 4.2.3 Corpus Stats Strip

A one-row strip of key numbers in DM Mono:
`PAGES: 127 · CHUNKS: 2,841 · GRAPH EDGES: 634 · LAST INGESTED: 2026-04-19`

---

#### Tab: 3D EMBEDDING SPACE

A 3D scatter plot of document-level IPFR page embeddings, projected into 3D using PCA or UMAP (pre-computed coordinates stored in the mock data; the component renders them — it does not compute them at runtime).

**Visual spec:**
- Uses `Three.js` (available in the React artifact environment)
- Each IPFR page is a **sphere**, radius 4px, coloured by a topic cluster assignment (provide 6–8 mock clusters, each with a cluster colour drawn from the palette)
- The sphere glows (emissive material, low intensity) proportionally to the number of alerts it has received historically
- Labels: on hover, a floating text label shows the page ID and title
- Camera: orbits freely with mouse drag; scroll to zoom; right-click drag to pan
- Axes: three thin lines in `--text-tertiary` with coordinate labels at ±1.0 endpoints
- Grid: faint grid planes at z=0 and y=0 in `--rule`
- A subtle star-field background (small white dots randomly distributed, `opacity: 0.15`)

**Interaction:**
- Click a sphere: highlight it (increase emissive, show label pinned), populate the right-side detail panel with that page's metadata, top chunks, keyphrases, entity list, and graph neighbours
- Multi-select with Shift+click: draw a convex hull around selected spheres
- Filter by cluster: chips below the 3D view, one per cluster, toggle visibility
- Filter by alert history: a slider `0–N alerts` filters which spheres are visible
- Animate button: slowly rotates the entire scene (CSS/requestAnimationFrame orbit)

**Right-side detail panel (appears on sphere click, 280px wide):**
- Page ID in Bebas Neue 28px
- Title in Lora 14px
- Section count, chunk count, entity count in DM Mono 11px
- Top 5 keyphrases as small pills
- A mini 2D graph (SVG) showing this page's immediate graph neighbours (1 hop), with edge weights as line opacity
- Last modified date, last alert date, total alerts, alert verdict distribution (3 small coloured bars)

---

#### Tab: 2D KNOWLEDGE GRAPH

A force-directed 2D graph of the quasi-graph (IPFR pages as nodes, graph edges as links).

**Visual spec:**
- Uses `d3-force` (D3 is available in the artifact environment)
- **Nodes:** Circles, radius proportional to the page's degree (number of edges). Filled with the page's cluster colour (same clusters as 3D view). Labelled with page ID in DM Mono 9px.
- **Edges:** Lines coloured by edge type:
  - Embedding similarity: `--stage-4` (steel blue), opacity = edge weight
  - Entity overlap: `--stage-3` (amber), opacity = edge weight
  - Internal links: `--stage-1` (green), opacity = edge weight
- **Force simulation:** Repulsion between nodes, attraction along edges, gravity toward centre. Simulation runs on mount, then nodes can be dragged.
- **Alert pulse:** Nodes with active alerts (CHANGE_REQUIRED in the last 30 days) pulse with a slow ring animation (expanding circle, `--state-alert` colour)

**Interaction:**
- Hover node: highlight all edges from that node, dim all unconnected nodes
- Click node: pin detail panel (same panel as 3D view)
- Hover edge: show tooltip with edge type, weight, and the two page IDs
- Edge type filter: three toggle buttons (one per edge type) above the graph
- Min weight slider: removes edges below the threshold weight, simplifying the graph
- Search: type a page ID or keyword → matching node(s) pulse and camera pans to them

---

#### Tab: CONTENT MAP

A 2D treemap or icicle chart showing the IPFR corpus organised hierarchically:

- **Root:** The entire corpus
- **Level 1:** Topic clusters (same clusters as the 3D view)
- **Level 2:** Individual IPFR pages within each cluster
- **Cell fill:** Coloured by cluster, with brightness encoding alert recency (brighter = more recently alerted)
- **Cell size:** Proportional to chunk count (larger pages → larger cells)
- **Cell label:** Page ID + truncated title in DM Mono 9px, visible when cell is large enough

**Interaction:**
- Click a cluster: zoom into that cluster (treemap drill-down animation)
- Click a page: open detail panel
- Breadcrumb trail at the top: `CORPUS › CLUSTER 3 › B1012`
- Back button or click breadcrumb to zoom out
- Hover cell: tooltip showing page ID, title, chunk count, entity count, alert count

---

#### Panel: SNAPSHOT OVERLAY

Below the main visualisation, a full-width panel that overlays **influencer source snapshots** against **the IPFR corpus**.

**Purpose:** Let the operator see, at a glance, how much of each influencer source's current content is "covered" by the IPFR corpus — and which parts of the influencer are new or changed since the last check.

**Visual spec:**
- Left column (30%): Influencer source selector — a list of all sources from the registry, with their last-checked date and a health indicator dot
- Right column (70%): Two side-by-side text panels:
  - Left panel: Current influencer snapshot (normalised plain text), scrollable
  - Right panel: Most-similar IPFR page (determined by the highest bi-encoder score in the last run against this source)
  - Matching passages highlighted in both panels simultaneously — highlighted text in the influencer snapshot glows `--stage-4` (steel blue), the corresponding passage in the IPFR panel glows `--stage-1` (green)
  - A similarity score badge between the two panels: `SIMILARITY: 0.83` in DM Mono
  - If the influencer snapshot has changed since last check, the diff (additions/deletions) is visually superimposed: added text has a faint `--state-ok` green underline; deleted text has a faint `--state-alert` red strikethrough

---

### 4.3 SOURCES — Influencer Source Management

A registry view and source health panel.

#### 4.3.1 Panel Layout

```
ROW 1: [SOURCE REGISTRY TABLE — full width]

ROW 2: [SOURCE DETAIL (appears on row click) — full width expandable]
```

#### 4.3.2 Source Registry Table

A styled data table where each row is one source from `source_registry.csv`.

**Columns:**
1. Source ID (`TW-SRC-001`) — DM Mono, coloured dot for source type (blue=webpage, amber=FRL, green=RSS)
2. URL (truncated, with external link icon)
3. Importance (0.0–1.0, displayed as a thin horizontal fill bar)
4. Check Frequency (pill: `DAILY · WEEKLY · MONTHLY`)
5. Last Checked (relative: `3h ago` / `2d ago`)
6. Last Changed (relative)
7. Check Health (mini sparkline: last 30 checks shown as 30 tiny tick marks coloured by outcome)
8. Total Alerts triggered (count)

**Row click:** Expands inline (or opens the right drawer, design choice) to show:
- Full source metadata
- A mini-timeline of all check events for this source (past 90 days)
- A per-stage funnel for this source specifically
- The current snapshot text (scrollable, 200px max-height)
- The previous snapshot text alongside it (with diff highlighted)
- Button: `VIEW IN TIMELINE` (navigates to OBSERVE with this source pre-filtered)

**Add/Edit source:** A button `+ ADD SOURCE` opens a modal form with fields for all source registry columns. Each field has a small label and DM Mono input styling. Submit saves to the mock data store.

---

### 4.4 ADJUST — Configuration Editor

This section exposes all parameters from `tripwire_config.yaml` as editable form controls.

#### 4.4.1 Design Principle

The configuration editor must feel like a control panel, not a settings page. It uses the following conventions:
- All labels in DM Mono small caps, 10px, `--text-secondary`
- All values in DM Mono 13px, `--text-primary`
- Numeric inputs with steppers (+ / - buttons)
- Boolean toggles as custom switches (not browser checkboxes) — a small pill that slides left/right
- All changes are staged (highlighted in `--stage-3` amber) until the user clicks `APPLY CHANGES`
- A `RESET TO DEFAULTS` button in `--text-tertiary` (destructive, requires confirmation)
- A diff preview: when changes are staged, a sidebar shows a before/after YAML diff

#### 4.4.2 Info Tooltip System

Every parameter has a small `ⓘ` icon to its right (DM Mono, 9px, `--text-tertiary`, positioned as a superscript-style element).

**Tooltip spec:**
- Trigger: hover (or tap on mobile)
- Container: 240px wide panel, `--bg-secondary` background, `1px solid var(--rule-accent)` border
- Typography: Lora 12px for the description text
- Content: a one-to-two sentence plain-English explanation of what the parameter does and why it matters
- At the bottom of the tooltip: `Learn more ↗` — a small DM Mono 10px link that:
  - Navigates to the DOCUMENT section
  - Scrolls to and highlights the relevant subsection
  - A URL-style hash is appended to the internal route, e.g. `#config-rrf-k`
- Tooltip animation: fade in over 150ms, no bounce

#### 4.4.3 Config Sections and Parameters

Render the config in collapsible accordion sections. Each section has a Bebas Neue heading and a section-level info indicator showing count of changed parameters.

---

**Section: PIPELINE BEHAVIOUR**

| Parameter | Control Type | Info Text | Doc Anchor |
|---|---|---|---|
| `observation_mode` | Toggle | When ON, the pipeline runs all stages but sends no alerts and skips LLM calls. Use during initial calibration. | `#doc-observation-mode` |
| `run_frequency_hours` | Number input (step 1, min 1, max 168) | How often the pipeline runs, in hours. Default 24 (daily). | `#doc-run-frequency` |
| `max_retries` | Number input (step 1, min 0, max 5) | How many times a transient failure (HTTP 5xx, timeout) is retried with exponential backoff before the source is skipped. | `#doc-retries` |
| `retry_base_delay_seconds` | Number input (step 0.5, min 0.5, max 30) | The base delay in seconds for the first retry. Each subsequent retry doubles this value. | `#doc-retry-backoff` |
| `llm_temperature` | Slider (0.0–1.0, step 0.05) | Controls LLM output randomness. Lower values produce more deterministic, conservative responses. Default 0.2. | `#doc-llm-temperature` |
| `llm_model` | Text input | The model identifier passed to the LLM API. E.g. `gpt-4o`. | `#doc-llm-model` |
| `deferred_trigger_max_age_days` | Number input (step 1, min 1, max 30) | How long a deferred trigger (queued when the LLM API was unavailable) is retained before being discarded. | `#doc-deferred-triggers` |

---

**Section: CHANGE DETECTION (STAGE 2)**

| Parameter | Control Type | Info Text | Doc Anchor |
|---|---|---|---|
| `significance_fingerprint` | Toggle | When ON, Stage 2 applies an NLP tagger to classify changes as high or standard significance based on presence of defined terms, numbers, dates, and modal verbs. | `#doc-significance-fingerprint` |

---

**Section: RELEVANCE SCORING (STAGE 4)**

| Parameter | Control Type | Info Text | Doc Anchor |
|---|---|---|---|
| `rrf_k` | Number input (step 5, min 10, max 200) | Smoothing constant in the Reciprocal Rank Fusion formula. Higher values reduce the advantage of top-ranked items. Default 60. | `#doc-rrf-k` |
| `rrf_weight_bm25` | Number input (step 0.1, min 0.0, max 5.0) | Relative weight of the BM25 keyword-matching signal in fusion scoring. | `#doc-rrf-weights` |
| `rrf_weight_semantic` | Number input (step 0.1, min 0.0, max 5.0) | Relative weight of the bi-encoder semantic similarity signal in fusion scoring. Default 2.0 (higher than BM25 to prioritise meaning over keyword overlap). | `#doc-rrf-weights` |
| `top_n_candidates` | Number input (step 1, min 1, max 20) | Minimum number of IPFR pages to forward to semantic matching, regardless of score. | `#doc-top-n` |
| `min_score_threshold` | Number input or "AUTO" toggle | Minimum fused score for a page to be included beyond the top-N cutoff. Set to null during observation period. | `#doc-min-score-threshold` |
| `source_importance_floor` | Slider (0.0–1.0, step 0.05) | The minimum multiplier applied to any source's relevance score, regardless of its importance rating. Prevents low-importance sources from being completely suppressed. | `#doc-importance-floor` |
| `fast_pass_source_importance_min` | Slider (0.0–1.0, step 0.05) | Sources with importance ≥ this value bypass Stage 4 scoring and proceed directly to Stage 5. | `#doc-fast-pass` |
| `yake_keyphrases_per_80_words` | Number input (step 1, min 1, max 5) | Rate of keyphrase extraction from diffs. Higher values produce more query terms for BM25. | `#doc-yake` |
| `yake_min_keyphrases` | Number input (step 1, min 1, max 10) | Floor on the number of keyphrases extracted, regardless of diff length. | `#doc-yake` |
| `yake_max_keyphrases` | Number input (step 1, min 5, max 30) | Ceiling on the number of keyphrases extracted. | `#doc-yake` |
| `yake_short_diff_threshold` | Number input (step 5, min 10, max 200) | Diffs shorter than this word count are supplemented with NER entities to ensure sufficient BM25 query terms. | `#doc-yake` |

---

**Section: SEMANTIC SCORING (STAGES 5–6)**

| Parameter | Control Type | Info Text | Doc Anchor |
|---|---|---|---|
| `biencoder_model` | Text input (read-only, with edit toggle) | The Hugging Face model identifier for the bi-encoder. Changing this requires re-computing all IPFR embeddings. | `#doc-biencoder` |
| `biencoder_high_threshold` | Slider (0.0–1.0, step 0.01) | A single chunk scoring above this cosine similarity triggers the IPFR page as a candidate. | `#doc-biencoder-thresholds` |
| `biencoder_low_medium_threshold` | Slider (0.0–1.0, step 0.01) | The lower threshold used in the "3+ chunks" candidate trigger rule. | `#doc-biencoder-thresholds` |
| `biencoder_low_medium_min_chunks` | Number input (step 1, min 1, max 10) | Number of chunks that must exceed the low-medium threshold to trigger the IPFR page. | `#doc-biencoder-thresholds` |
| `crossencoder_model` | Text input (read-only, with edit toggle) | The Hugging Face model identifier for the cross-encoder reranker. | `#doc-crossencoder` |
| `crossencoder_threshold` | Slider (0.0–1.0, step 0.01) | Minimum cross-encoder score for a candidate IPFR page to proceed to LLM assessment. | `#doc-crossencoder-threshold` |
| `crossencoder_max_context_tokens` | Number input (step 512, min 512, max 16384) | Maximum combined token count passed to the cross-encoder. Inputs exceeding this are truncated with a warning. | `#doc-crossencoder-context` |

---

**Section: GRAPH PROPAGATION (STAGE 6)**

| Parameter | Control Type | Info Text | Doc Anchor |
|---|---|---|---|
| `graph_enabled` | Toggle | Enables alert propagation through the quasi-graph. When OFF, only directly-scored pages are alerted. | `#doc-graph` |
| `graph_max_hops` | Number input (step 1, min 1, max 5) | Maximum number of graph hops a propagated alert can travel from the directly-triggered page. | `#doc-graph-hops` |
| `graph_decay_per_hop` | Slider (0.0–1.0, step 0.01) | Fraction of signal strength retained at each hop. At 0.45: 1 hop = 45%, 2 hops = 20.25%, 3 hops = 9.11%. | `#doc-graph-decay` |
| `graph_propagation_threshold` | Slider (0.0–0.5, step 0.005) | Propagation stops on a path when the decayed signal falls below this floor. | `#doc-graph-threshold` |
| `edge_embedding_similarity_enabled` | Toggle | Enable/disable embedding-similarity edges in the quasi-graph. | `#doc-graph-edges` |
| `edge_embedding_similarity_weight` | Slider (0.0–1.0, step 0.05) | Scaling factor applied to embedding-similarity edge weights. | `#doc-graph-edges` |
| `edge_embedding_similarity_top_k` | Number input (step 1, min 1, max 20) | Each page retains edges to its top-K most similar neighbours. | `#doc-graph-edges` |
| `edge_embedding_similarity_min_similarity` | Slider (0.0–1.0, step 0.01) | Minimum cosine similarity for an embedding-similarity edge to be retained. | `#doc-graph-edges` |
| `edge_entity_overlap_enabled` | Toggle | Enable/disable entity-overlap edges. | `#doc-graph-edges` |
| `edge_entity_overlap_weight` | Slider (0.0–1.0, step 0.05) | Scaling factor applied to entity-overlap edge weights. | `#doc-graph-edges` |
| `edge_entity_overlap_min_jaccard` | Slider (0.0–1.0, step 0.01) | Minimum Jaccard coefficient for an entity-overlap edge to be retained. | `#doc-graph-edges` |
| `edge_internal_links_enabled` | Toggle (disabled / greyed with tooltip "Not yet implemented") | Enable/disable internal-link graph edges. Deferred pending link extraction implementation. | `#doc-graph-edges` |

---

**Section: STORAGE**

| Parameter | Control Type | Info Text | Doc Anchor |
|---|---|---|---|
| `content_versions_retained` | Number input (step 1, min 1, max 20) | How many previous snapshot versions of each influencer source are retained in the repository. | `#doc-snapshots` |
| `sqlite_wal_mode` | Toggle (read-only, always ON) | SQLite Write-Ahead Logging mode. Required for concurrent read/write access. Always enabled. | `#doc-sqlite` |
| `git_commit_snapshots` | Toggle | Commit influencer source snapshots to the repository after each run. | `#doc-git-persistence` |
| `git_commit_database` | Toggle | Commit the IPFR SQLite database to the repository after each ingestion run. | `#doc-git-persistence` |

---

**Section: NOTIFICATIONS**

| Parameter | Control Type | Info Text | Doc Anchor |
|---|---|---|---|
| `content_owner_email` | Text input (email type) | Email address that receives consolidated alert reports after each run. | `#doc-notifications` |
| `health_alert_email` | Text input (email type) | Email address that receives system health alerts (high error rate, consecutive failures). | `#doc-health-alerts` |
| `health_error_rate_threshold` | Slider (0.0–1.0, step 0.05) | If the fraction of sources that error in a single run exceeds this value, a health alert is sent. | `#doc-health-alerts` |
| `health_consecutive_failures_threshold` | Number input (step 1, min 1, max 10) | If the same source fails this many consecutive runs, a health alert is sent. | `#doc-health-alerts` |
| `pipeline_timeout_minutes` | Number input (step 5, min 10, max 120) | The GitHub Actions `timeout-minutes` value. Runs exceeding this duration are killed and flagged. | `#doc-timeout` |

---

#### 4.4.4 YAML Preview Panel

A collapsible panel at the bottom of the ADJUST section showing the current config as a YAML code block. Staged (unsaved) changes are highlighted with an amber left border on the changed lines. A `COPY YAML` button copies the full YAML to clipboard.

---

### 4.5 DOCUMENT — System Documentation

A full in-app documentation viewer rendering the Tripwire system plan as a navigable, well-typeset document.

#### 4.5.1 Layout

```
[SECTION SIDEBAR — 240px] [DOCUMENT BODY — remaining width]
```

**Sidebar:** A nested list of document sections (mirroring the system plan headings). Active section highlighted. Clicking a section smoothly scrolls the document body to that section. Sections are collapsible.

**Document body:**
- Rendered in Lora serif, 16px, `--text-primary`, line-height 1.75
- Headings in Bebas Neue (H1: 42px, H2: 28px, H3: 20px)
- Code blocks in DM Mono 12px, `--bg-tertiary` background, with syntax highlighting for YAML, SQL, Python, and JSON
- Tables: clean, hairline-bordered, header row in Bebas Neue 11px small caps
- Architecture diagrams from the system plan (the ASCII box diagrams) are rendered as styled SVG reproductions, not raw ASCII
- All 9 pipeline stage sections have a stage-colour left border
- Configuration parameter names appear as inline `code` spans that, when clicked, navigate to the ADJUST section and highlight the corresponding control

#### 4.5.2 Document Sections

Render the complete content of the Tripwire System Plan document in full, organised into the following top-level sections (matching the system plan numbering):

1. Purpose and Scope
2. Architecture Overview (with SVG reproduction of the pipeline diagram)
3. Stage Specifications (one sub-section per stage, S1–S9, each with its stage colour accent)
4. IP First Response Ingestion Pipeline
5. Repository Structure
6. Error Handling, Retries, and Observability
7. Configuration (with anchor links to every parameter in the ADJUST section)
8. CI/CD Configuration
9. Persistent Storage
10. Feedback Ingestion
11. Lazy Model Loading
12. Pipeline Run Logging
13. SQLite Schema
14. Phased Implementation Plan

#### 4.5.3 Search

A search input at the top of the sidebar searches across all document text and highlights matching passages in the document body. Results count displayed: `3 matches`.

#### 4.5.4 Anchor Links from ADJUST

When the user clicks `Learn more ↗` on an ADJUST tooltip, the DOCUMENT section opens and scrolls to the correct section. The target section pulses with a brief amber glow animation to confirm arrival.

---

### 4.6 HEALTH — System Health Log

A dedicated panel for monitoring pipeline reliability.

#### 4.6.1 Panel Layout

```
ROW 1: [HEALTH STATUS STRIP — full width]

ROW 2: [ERROR RATE CHART — 50%] [CONSECUTIVE FAILURES LIST — 50%]

ROW 3: [FULL RUN LOG TABLE — full width]
```

#### 4.6.2 Health Status Strip

Five stat cards in a row:
- **Last Run:** timestamp + duration + outcome pill
- **Error Rate (30d):** percentage + mini sparkline
- **Sources Monitored:** count
- **LLM Schema Failures (30d):** count
- **Cross-Encoder Truncations (30d):** count

Each card is a small bordered panel. The `Error Rate` card turns amber (`--state-warn`) if rate > 15%, red (`--state-alert`) if rate > 30%.

#### 4.6.3 Error Rate Chart

A time series line chart (using Recharts, available in the artifact) showing:
- Daily error rate as a filled area chart (faint `--state-alert` fill)
- The 30% threshold as a horizontal dashed line
- The 15% threshold as a second dashed line
- X axis: dates, DM Mono 9px
- Y axis: percentage, DM Mono 9px

#### 4.6.4 Consecutive Failures List

A list of sources that have failed 2 or more consecutive runs, sorted by failure streak descending. Each entry shows:
- Source ID and URL
- Current streak count (e.g. `3 consecutive failures`)
- Last error type and stage
- A `VIEW SOURCE` link (navigates to SOURCES section, source selected)

If no sources are failing: display `ALL SOURCES HEALTHY` in `--state-ok` green, Bebas Neue 18px.

#### 4.6.5 Full Run Log Table

A paginated table of all pipeline runs, one row per run (not per source).

**Columns:**
- Run ID
- Start Time
- Duration (seconds)
- Sources Checked
- Sources Changed
- Sources Errored
- Alerts Generated (count)
- Status (COMPLETED / PARTIAL / FAILED)

Row click: expand to show per-source breakdown for that run.

---

## 5. DATA MODEL (Mock Data)

Since this is a dashboard artifact without a real backend, all data is provided as mock data defined in the component's initial state. The mock data should be comprehensive enough to demonstrate all visualisation features.

### 5.1 Required Mock Data Arrays

```typescript
// Run log entries
interface RunLogEntry {
  run_id: string;           // "2026-04-05-001"
  timestamp: string;        // ISO 8601
  source_id: string;
  source_url: string;
  source_type: 'webpage' | 'frl' | 'rss';
  stage_reached: number;    // 1–9
  outcome: 'completed' | 'no_change' | 'error' | 'skipped';
  verdict?: 'CHANGE_REQUIRED' | 'UNCERTAIN' | 'NO_CHANGE';
  confidence?: number;
  biencoder_max?: number;
  crossencoder_score?: number;
  ipfr_page_id?: string;
  reasoning?: string;
  suggested_changes?: string[];
  diff_text?: string;
  feedback?: 'useful' | 'not_significant' | 'wrong_amendment' | 'wrong_page';
  error_type?: string;
  duration_seconds?: number;
  scores?: {
    bm25_rank?: number;
    semantic_rank?: number;
    rrf_score?: number;
    source_importance?: number;
    final_score?: number;
  };
}

// Source registry
interface SourceRegistryEntry {
  source_id: string;
  url: string;
  source_type: 'webpage' | 'frl' | 'rss';
  importance: number;       // 0.0–1.0
  frequency: 'daily' | 'weekly' | 'fortnightly' | 'monthly' | 'quarterly';
  last_checked: string;
  last_changed?: string;
  check_history: Array<{ timestamp: string; outcome: string; stage_reached: number }>;
}

// IPFR corpus pages
interface IPFRPage {
  page_id: string;          // "B1012"
  title: string;
  url: string;
  chunk_count: number;
  entity_count: number;
  cluster: number;          // 0–7
  embedding_3d: [number, number, number];  // pre-computed PCA coordinates
  embedding_2d: [number, number];          // pre-computed 2D coordinates
  keyphrases: string[];
  last_modified: string;
  last_alerted?: string;
  alert_count: number;
  graph_neighbours: Array<{ page_id: string; weight: number; edge_type: string }>;
}

// Active config
interface TripwireConfig { /* all parameters from Section 4.4 */ }
```

### 5.2 Mock Data Scale

Provide sufficient mock data to make all visualisations interesting:
- At least **80 sources** in the source registry (mix of webpage, FRL, RSS types)
- At least **120 IPFR corpus pages** (scattered across 7 clusters in 3D/2D embedding space)
- At least **500 run log entries** spanning 90 days (so timeline and charts have meaningful density)
- At least **30 triggered events** (Stage 8 completions) with a realistic distribution of verdicts
- At least **15 graph edges** between corpus pages

---

## 6. TECHNICAL IMPLEMENTATION REQUIREMENTS

### 6.1 Framework and Libraries

- **React** with hooks (`useState`, `useEffect`, `useRef`, `useMemo`, `useCallback`)
- **Recharts** for 2D charts (timeline bar chart, error rate area chart, donut charts)
- **D3** (`d3-force`, `d3-hierarchy`) for the knowledge graph and treemap
- **Three.js** (r128) for the 3D embedding space
- **No external component libraries** (no MUI, Chakra, etc.) — all components are custom
- All in a single `.jsx` file

### 6.2 State Management

Use React `useState` and `useContext` for a global app state containing:
- Active section
- Active filters (date range, source filter, stage filter, verdict filter)
- Selected entity (clicked source, page, run, etc.)
- Staged config changes
- Open drawers/modals

### 6.3 Performance Considerations

- The 3D Three.js scene must use `useRef` for the canvas and clean up the renderer on unmount
- D3 force simulation should pause when the graph tab is not visible
- Mock data computations (filtering, sorting) should use `useMemo` to avoid re-computation on every render
- The timeline swimlane with 80+ sources should use a virtualised list approach (render only visible rows) or limit to visible viewport

### 6.4 Animation

- Section transitions: a fast (200ms) fade + slight vertical slide when switching nav sections
- Drawer open/close: 250ms ease-out slide from right
- Tooltip reveal: 150ms fade
- 3D scene: continuous slow rotation when animate button is active
- Config change highlight: amber pulse (3s, then hold)
- Document anchor arrival: amber background glow on target section, 1.5s fade out
- Health alert card: a subtle pulse on the error-rate card when threshold is exceeded

### 6.5 Responsiveness

The dashboard is designed for a **1440px+ desktop viewport**. On smaller viewports:
- The nav rail collapses to icon-only (56px)
- The OBSERVE panels stack vertically
- The 3D view disables (shows a "viewport too small for 3D" message below 900px width)

---

## 7. VISUAL QUALITY CHECKLIST

Before considering the implementation complete, verify:

- [ ] The colour palette is strictly followed — no default browser blue links, no white backgrounds on any panel
- [ ] All numerical data is in DM Mono — no serif or sans-serif numbers anywhere in data displays
- [ ] All headings are in Bebas Neue — nothing in Inter or system sans-serif
- [ ] Hairline rules (`1px solid var(--rule)`) separate every logical group
- [ ] No border-radius on panels or tables (only on pills, badges, and tooltips, max 3px)
- [ ] The grain texture overlay is visible but subtle on panel backgrounds
- [ ] Stage colours are consistently applied across every panel that references stages
- [ ] All tooltips show the `Learn more ↗` link
- [ ] The config diff preview works and shows staged changes in amber
- [ ] The 3D view orbits on drag and responds to scroll-zoom
- [ ] The knowledge graph nodes can be dragged
- [ ] The DOCUMENT section renders all system plan content legibly with section sidebar navigation
- [ ] The snapshot overlay shows both text panels with highlighted matching passages
- [ ] The filter bar in OBSERVE drives all panels reactively

---

## 8. SECTION NAVIGATION REFERENCE

For the `Learn more ↗` links in ADJUST tooltips, the following anchor IDs must be present in the DOCUMENT section:

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

## 9. PROMPT TO CLAUDE CODE

When handing this brief to Claude Code, use the following prompt:

---

*"Build a single-file React JSX artifact — a dashboard called Tripwire — according to the attached design and engineering brief. The brief is comprehensive and precise; follow it closely.*

*Key implementation priorities in order:*

*1. Get the aesthetic exactly right first: the colour palette (near-black background, bone text, 6 accent colours), the three fonts (Bebas Neue for headings, DM Mono for all data, Lora for body/docs), the hairline rules, hard corners, grain texture.*

*2. Build all six navigation sections: OBSERVE, CORPUS, SOURCES, ADJUST, DOCUMENT, HEALTH.*

*3. Populate with realistic mock data (80+ sources, 120+ IPFR pages, 500+ run log entries).*

*4. Wire up the ADJUST section with full tooltip/Learn more system linking to DOCUMENT anchors.*

*5. Implement the three CORPUS visualisation tabs: Three.js 3D embedding scatter, D3 force-directed graph, and D3 treemap.*

*6. Ensure the global filter bar in OBSERVE drives all panels reactively.*

*The complete Tripwire System Plan document content must be rendered in full in the DOCUMENT section — do not summarise or abbreviate it.*

*The result should feel like a bespoke government intelligence terminal, not a SaaS dashboard."*

---

*End of Brief*
