# Tripwire — System Plan

## 1. Purpose and Scope

Tripwire is an autonomous monitoring system that tracks substantive changes in authoritative Intellectual Property (IP) sources — such as Australian legislation hosted on the Federal Register of Legislation (FRL), WIPO feeds, and government agency webpages — to detect updates that may require amendments to content published on the IP First Response (IPFR) website.

The system answers a chain of five questions, each more expensive to compute than the last:

1. Did the target information change?
2. Was the change meaningful?
3. What exactly is different?
4. Is the change potentially relevant to IPFR content?
5. Which IPFR pages are most likely affected, and what should be done about it?

Each question acts as a gate. Only changes that pass one gate proceed to the next. This filter-funnel architecture ensures that expensive operations (semantic scoring, LLM calls) are reserved for the small fraction of changes that survive cheaper upstream checks.

The system is modular. A user can fork the repository, replace the "influencer" sources and the "influenced" corpus, adjust the configuration file, and have a working change-monitoring pipeline for a different domain.

---

## 2. Architecture Overview

### 2.1 Pipeline Stages

The pipeline executes as a scheduled GitHub Actions workflow. Each run processes every influencer source that is due for checking (based on its configured frequency), passes changes through a sequence of gates, and produces a consolidated email report to the content owner.

```
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 1: Metadata Probe                                        │
│  Has the source changed at all?                                 │
│  (Version ID, Last-Updated header, content length)              │
│  ── cheap, runs on every source every cycle ──                  │
└──────────────┬──────────────────────────────────────────────────┘
               │ changed
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 2: Change Detection                                      │
│  Was the change meaningful?                                     │
│  Three-pass system:                                             │
│    • SHA-256 content hash (exact match = skip)                  │
│    • Word-level diff (empty after normalisation = cosmetic)     │
│    • Significance fingerprint tagger (high / standard)          │
│  ── moderate cost, runs only on sources that passed Stage 1 ──  │
│                                                                 │
│  * NOT applied to FRL sources (they publish change explainers)  │
│  ** Webpages are scraped and normalised before this stage       │
└──────────────┬──────────────────────────────────────────────────┘
               │ meaningful change detected
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 3: Diff Generation                                       │
│  What exactly is different?                                     │
│  Source-type routing:                                            │
│    • Webpages → .diff file (old snapshot vs new snapshot)        │
│    • FRL → retrieve the change explainer document               │
│    • RSS → extract only the new items                           │
└──────────────┬──────────────────────────────────────────────────┘
               │ diff / change document produced
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 4: Relevance Scoring                                     │
│  Is this change potentially relevant to IPFR content?           │
│  Two signals fused via weighted Reciprocal Rank Fusion:         │
│    • YAKE-driven BM25          (RRF weight: 1.0)                │
│    • Bi-encoder cosine sim     (RRF weight: 2.0)                │
│  Source importance multiplier applied after fusion               │
│  Fast-pass override for source importance = 1.0                 │
│  ── moderate cost, runs only on sources that passed Stage 2 ──  │
└──────────────┬──────────────────────────────────────────────────┘
               │ top-N candidates OR score threshold exceeded
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 5: Semantic Matching — Bi-Encoder                        │
│  Which IPFR pages are most likely affected? (coarse pass)       │
│    • Chunk the incoming change document                         │
│    • Compute cosine similarity against IPFR content chunks      │
│      using BAAI/bge-base-en-v1.5                                    │
│  Proceed if:                                                    │
│    • Any single chunk scores ≥ 0.75, OR                         │
│    • 3+ chunks from the same IPFR page score ≥ 0.45            │
└──────────────┬──────────────────────────────────────────────────┘
               │ candidate IPFR pages identified
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 6: Semantic Matching — Cross-Encoder                     │
│  Which IPFR pages are most likely affected? (precise pass)      │
│    • Score full IPFR page vs full change document with          │
│      gte-reranker-modernbert-base (8,192-token context)        │
│    • Rerank semantic findings with lexical results and           │
│      pre-computed quasi-graph relationships                     │
│    • Propagate alerts to graph-connected IPFR pages             │
│      (decay 0.25 per hop, max 3 hops, floor 0.05)              │
└──────────────┬──────────────────────────────────────────────────┘
               │ confirmed IPFR pages with ranked scores
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 7: Trigger Aggregation                                   │
│  Group all triggers per IPFR page within this run.              │
│  Batch all relevant diffs for each IPFR page into a single      │
│  unit for LLM assessment.                                       │
└──────────────┬──────────────────────────────────────────────────┘
               │ grouped trigger sets
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 8: LLM Assessment                                        │
│  Should the IPFR page be amended? If so, why?                   │
│  One discrete LLM call per IPFR page (not per trigger).         │
│  Inputs:                                                        │
│    • Cached system prompt                                       │
│    • All relevant diffs for this page                           │
│    • The IPFR page content                                      │
│    • Bi-encoder cosine scores per chunk                         │
│    • Relevance scores (lexical, semantic, reranked)             │
│  Output: structured JSON against a validated schema             │
└──────────────┬──────────────────────────────────────────────────┘
               │ amendment suggestions (or "no amendment needed")
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 9: Notification                                          │
│  One consolidated email per run to the content owner.           │
│  Includes structured feedback mechanism (mailto links).         │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Source-Type Routing

Three categories of influencer source pass through the pipeline differently:

| Source Type | Stage 2 (Change Significance) | Stage 3 (Diff Generation) | Stage 4 (Relevance Scoring Input) |
|---|---|---|---|
| **Webpage** | Scrape and normalise with trafilatura, then apply three-pass change detection (SHA-256 hash, word-level diff, significance tagger) | Produce `.diff` file from old vs new snapshot | Normalised diff scored via BM25 and bi-encoder similarity |
| **Federal Register of Legislation** | Skipped — FRL publishes structured change information | Retrieve the change explainer document | Change explainer scored via BM25 and bi-encoder similarity |
| **RSS Feed** | Skipped — RSS items are inherently new content | Extract new items since last check | New items scored via BM25 and bi-encoder similarity |

### 2.3 Observation Mode

On initial deployment, the pipeline runs in **observation mode**. All stages execute and all scores are logged, but no alerts are triggered and no emails are sent. This mode serves two purposes: it allows threshold calibration based on real score distributions, and it validates that each stage is producing sensible outputs before the system begins generating notifications.

Observation mode is controlled by a single boolean in the configuration file. When observation mode is active, the pipeline runs end-to-end, records everything to the pipeline_runs table, and exits after Stage 6 (skipping LLM calls and email notifications to save cost). A summary report of score distributions is generated instead, to support manual threshold review.

During the initial calibration period (4–8 weeks recommended), the operator should also manually alter markdown snapshots of 10–15 influencer sources to test the sensitivity of each gate. This provides controlled ground-truth data for setting thresholds before real-world changes are available.

---

## 3. Stage Specifications

### 3.1 Stage 1 — Metadata Probe

**Purpose:** Determine whether a source has changed at all since the last check, using the cheapest possible signals. Sources that haven't changed are immediately skipped.

**Frequency:** Each source has a configured check frequency (daily, weekly, fortnightly, monthly, or quarterly) defined in the influencer source registry CSV. The pipeline runs every 24 hours (after the IPFR corpus ingestion run has completed) but only probes each source when its scheduled check is due.

**Probe signals (check any available, source-dependent):**

- HTTP `ETag` or `Last-Modified` header comparison against stored values
- Content-Length header comparison
- Version identifier (FRL sources: `registerId` of the latest compiled version, obtained via `GET /v1/Versions/Find(titleId='{titleId}',asAtSpecification='Latest')` on `api.prod.legislation.gov.au`)
- RSS feed: presence of items with publication dates newer than the last-checked timestamp

**Decision rule:** If any probe signal indicates a change, proceed to Stage 2. If no signals are available (e.g. the server doesn't return useful headers), always proceed to Stage 2 — the cost of an unnecessary scrape is low.

**Outputs logged:** Run ID, source ID, source URL, timestamp, probe signals collected, decision (changed / unchanged / unknown).

### 3.2 Stage 2 — Change Detection

**Purpose:** Determine whether a detected change is meaningful or merely cosmetic (e.g. a timestamp update, a CSS class rename, a whitespace change). This stage is only applied to **webpages**. FRL sources and RSS feeds bypass this stage because their change information is already structured.

**Prerequisite:** For webpages, the new page is scraped and normalised into plaintext using **trafilatura** before any comparison. The previous snapshot is loaded from storage.

**Three-pass system:**

**Pass 1: SHA-256 Content Hash.** Compute the SHA-256 hash of the normalised plain text. Compare against the stored hash from the previous snapshot. If the hashes match, the content has not changed — skip immediately and stop processing this source for this run. This is the fast-path check: exact rather than approximate.

**Pass 2: Word-Level Diff.** Generate a word-level or sentence-level diff (using Python `difflib.unified_diff` on the normalised text) to identify specifically what changed. This produces interpretable output showing the exact insertions, deletions, and modifications. If the diff is empty after normalisation (whitespace-only change), log as "cosmetic change" and stop processing this source for this run.

**Pass 3: Significance Fingerprint (Tagger).** Using spaCy and regex, extract the following from the *changed lines only* (from the diff in Pass 2):

- Defined terms (capitalised terms that appear to be legal definitions)
- Numerical values (dollar amounts, time periods, percentages, section numbers)
- Dates (commencement dates, deadline dates, amendment dates)
- Cross-references (references to other Acts, sections, or regulations)
- Modal verbs in legal context ("may", "must", "shall", "should")

Tag the change as either `significance: high` (fingerprint matched — defined terms, numbers, dates, cross-references, or modal verbs were modified) or `significance: standard` (real content changed but no fingerprint match). **Both tags proceed to Stage 3.** The significance tag travels with the change through the pipeline and is available to the LLM in Stage 8 as additional context (e.g., "this change modified a monetary threshold"). The fingerprint adds value as a signal but does not have veto power over genuine content changes.

**Decision rule:** If the SHA-256 hash differs (Pass 1) and the diff is non-empty after normalisation (Pass 2), the change proceeds to Stage 3 regardless of the significance tag. The only things stopped at Stage 2 are identical content (hash match) and whitespace-only diffs.

**Outputs logged:** Run ID, source ID, SHA-256 hash match (boolean), diff size (lines changed), significance tag (high / standard), significance fingerprint details (what changed, if any), decision.

### 3.3 Stage 3 — Diff Generation

**Purpose:** Produce a precise representation of what changed, formatted appropriately for the source type.

**Webpage sources:** Generate a unified `.diff` file comparing the previous normalised snapshot against the new normalised snapshot. Store the diff file in the run's working directory. Update the stored snapshot to the new version. Retain the previous 6 versions of the snapshot for audit purposes. The current snapshot (and any version files still within the retention window) are committed to the repository at the end of each run — see Section 7.2 for the Git persistence mechanism.

**FRL sources:** Retrieve the Explanatory Statement (ES) Word document for the latest compiled version using the official FRL REST API documents endpoint:
```
GET https://api.prod.legislation.gov.au/v1/documents/find(titleid='{titleId}',asatspecification='Latest',type='ES',format='Word',uniqueTypeNumber=0,volumeNumber=0,rectificationVersionNumber=0)
```
A metadata check (`Accept: application/json`) is performed first to confirm the document exists before downloading the binary. If `type='ES'` returns HTTP 404, `type='SupplementaryES'` is tried as a fallback. The binary DOCX is extracted to plain text via mammoth → trafilatura. If neither ES document type is available, fall back to treating the FRL source like a webpage (diff the legislation text directly) and log a warning.

**RSS sources:** Persist the feed state as a keyed JSON structure mapping each item's `guid` (or `link` as fallback if no GUID is present) to the full item payload: title, description, pubDate, link, and any `content:encoded` extension. On each run, fetch the current feed, compare keys against the stored snapshot to find new GUIDs, and compare payloads for existing GUIDs to detect mutations. The diff for a new item is its full content; for a mutated item it is the field-level delta. Snapshots are stored per feed URL with a stable filename derived from a hash of the URL.

Note: some feeds recycle GUIDs when content is updated (a spec violation but common). A secondary check on `pubDate` or a content hash alongside GUID comparison catches this case.

**Diff normalisation (applied to all source types before Stage 4):** The raw diff or extracted content is normalised into a canonical plain-text string before being consumed by downstream stages. Normalisation performs the following operations:

- Decode HTML entities.
- Collapse whitespace runs, including `\xa0` non-breaking spaces.
- Strip residual formatting artefacts from upstream processing.
- Normalize Unicode to NFC.
- For RSS items: concatenate title + description + `content:encoded` into a single text block with a fixed delimiter.

Normalisation does **not** lowercase text (NER and YAKE both depend on case information) and does **not** strip punctuation (YAKE uses it for sentence boundary detection). The normalised output is the canonical "changed text" consumed by all stages from Stage 4 onward.

**Text extraction layer:** Webpages are converted to plain text using **trafilatura**, which performs boilerplate removal (navigation menus, footers, sidebars) and extracts the main content as clean plain text. Trafilatura's built-in boilerplate removal replaces any manual stripping logic. DOCX sources are processed via a two-step pathway: `DOCX → Mammoth → HTML → trafilatura → plain text`. Mammoth preserves DOCX semantic structure (headings, tables, bold runs) as clean HTML before trafilatura converts it to plain text.

**Outputs logged:** Run ID, source ID, source type, diff file path (or explainer document path, or extracted RSS items), diff size in characters, normalised diff size in characters.

### 3.4 Stage 4 — Relevance Scoring

**Purpose:** Determine whether the detected change is potentially relevant to the IPFR corpus, and identify which IPFR pages are the strongest candidates for impact, before attempting expensive semantic matching.

**Two signals fused via weighted Reciprocal Rank Fusion (RRF):**

**Signal 1: BM25 (keyword relevance).** Run YAKE keyword extraction on the normalised diff to identify key phrases. Extract at a rate of 1 keyphrase per 80 words of diff text, with a minimum of 5 keyphrases and a maximum of 15. For diffs shorter than 50 words, supplement YAKE output with any NER entities extracted at the Stage 2 significance fingerprint step to ensure sufficient query terms. For RSS sources, apply YAKE per new item individually (since each item is a separate news unit), then merge and deduplicate the results for BM25 query construction — do not concatenate all new items and run YAKE once. Use the extracted keyphrases as query terms against a BM25 index built from the IPFR corpus (full pages, not chunks). BM25 produces a ranking of all IPFR pages by keyword relevance.

**Signal 2: Bi-encoder Semantic Similarity (meaning-level match).** Encode the normalised diff using the bi-encoder (BAAI/bge-base-en-v1.5), compute cosine similarity against each IPFR page's precomputed document-level embedding. This is a single vector dot product per page using embeddings already computed during ingestion. This signal captures semantic relevance even when terminology differs. Cosine similarity produces a ranking of all IPFR pages by semantic relevance.

**Fusion via weighted RRF.** For each change event, rank all IPFR pages independently by each signal. Compute:

```
RRF_score(page) = w_bm25/(k + rank_bm25) + w_semantic/(k + rank_semantic)
```

where `k = 60` (configurable), `w_bm25 = 1.0`, and `w_semantic = 2.0`. The higher semantic weight reflects that semantic similarity is the more important signal — it captures meaning-level relevance even when exact terminology differs, which is the primary matching challenge for this system.

**Source importance multiplier.** Applied after fusion (source importance is a per-source constant, not a per-page score, so it cannot produce a ranking across pages):

```
final_score = RRF_score × (0.5 + 0.5 × source_importance)
```

A source with importance 1.0 gets the full RRF score; importance 0.0 gets 50% of it. The 0.5 floor ensures low-importance sources can still trigger alerts on strong content matches.

**Candidate selection.** A **top-N OR score threshold** approach replaces the fixed threshold:

- Take the top-N IPFR pages by final score (default N = 5), **plus**
- Any additional page whose final score exceeds a minimum score threshold (configurable, calibrated during the observation period — a reasonable starting point is 50–60% of the score the #1-ranked page received in a typical run), **plus**
- Any page where a fast-pass condition is met.

This ensures a routine day surfaces ~5 candidates, but a major legislative change that genuinely affects 20 pages lets all 20 through.

**Fast-pass overrides:** Source importance = 1.0 bypasses the candidate selection threshold entirely and proceeds directly to Stage 5. If experience shows that exact entity matches (e.g., a diff mentioning a specific Act that appears in an IPFR page) are being missed by the two-signal fusion, NER overlap can be reintroduced as a fast-pass override rather than a full fusion signal.

**Outputs logged:** Run ID, source ID, per-IPFR-page BM25 rank and score, per-IPFR-page bi-encoder cosine similarity, RRF scores, source importance multiplier, final scores, top-N cutoff, fast-pass triggered (boolean), candidate pages identified.

### 3.5 Stage 5 — Semantic Matching: Bi-Encoder

**Purpose:** Identify which specific IPFR pages are most likely affected by the change. This is a coarse-grained semantic pass using a bi-encoder to efficiently compare against all IPFR content chunks.

**Process:**

1. Chunk the incoming change document (webpage diff, FRL explainer, or RSS items) using the same chunking strategy applied during IPFR ingestion, so that chunk sizes are comparable.
2. Encode each chunk using the **BAAI/bge-base-en-v1.5** bi-encoder model.
3. Compute cosine similarity between each change-document chunk and every precomputed IPFR content chunk embedding stored in the SQLite database.
4. For each IPFR page, record the highest single-chunk cosine score and the count of chunks exceeding the low-medium threshold.

**Decision rule:** An IPFR page becomes a candidate if either:

- Any single chunk scores ≥ 0.75 (a section of the IPFR page is clearly about the same subject as a section of the change), OR
- 3 or more chunks from the same IPFR page score ≥ 0.45 (the change is broadly related to multiple sections of the page, suggesting topical relevance even without a single strong match).

**Outputs logged:** Run ID, source ID, per-IPFR-page results (max chunk score, count of chunks above low-medium threshold, list of chunk IDs and their scores), candidate pages identified.

### 3.6 Stage 6 — Semantic Matching: Cross-Encoder

**Purpose:** Refine the candidate list from Stage 5 using a more precise (but more expensive) cross-encoder, then integrate lexical and graph-based signals for a final ranking.

**Process:**

1. For each candidate IPFR page from Stage 5, score the full IPFR page content against the full normalised change document using the **gte-reranker-modernbert-base** cross-encoder (~150M parameters, 8,192-token context window). The cross-encoder sees both complete texts simultaneously and produces a more accurate relevance judgment than chunk-level comparison. The 8,192-token context window accommodates full IPFR pages (typically 1,000–5,000 words) without truncation. **Truncation warning:** before every cross-encoder call, count the combined input tokens. If the total exceeds the model's context window, log a warning entry containing the source ID, IPFR page ID, combined token count, and the number of tokens that will be truncated.
2. Rerank the candidates by combining three signals:
   - Cross-encoder score (semantic precision)
   - Lexical relevance scores from Stage 4 (keyword and entity-level match)
   - Pre-computed quasi-graph relationships (structural and conceptual connections between IPFR pages)

**Graph propagation:** After direct scoring, propagate alerts through the quasi-graph. If a change triggers a confirmed alert for IPFR Page A, and Page A has a graph edge to Page B with weight *w*:

- The propagated signal for Page B = original score × *w* × decay_per_hop (default: 0.45) / out_degree(source_node)
- **Degree normalisation:** when propagating from a node, divide the outgoing signal by the node's out-degree (number of outgoing edges). This prevents high-degree hub nodes from absorbing activation and pushing relevant nodes below the alert threshold.
- Propagation continues up to max_hops (default: 3)
- If the decayed signal falls below propagation_threshold (default: 0.05), propagation stops on that path
- Graph-neighbour signals only **boost** scores; they never reduce them. A neighbour delta is applied as `score += max(0, neighbour_delta)`.

With decay_per_hop = 0.45, the effective signal is:

- After 1 hop: 45% of the original (meaningful, will reliably propagate)
- After 2 hops: 20.25% of the original (reliably functional for moderate-to-strong original signals)
- After 3 hops: 9.11% of the original (above the 0.05 floor, viable for strong original signals)

This configuration makes 2-hop propagation reliably functional and 3-hop propagation viable for strong original signals, matching the intended behaviour for legislative dependency chains (e.g. legislation → regulation → policy → procedure).

**Decision rule:** IPFR pages whose final reranked score (including any graph-propagated signal) exceeds the cross-encoder threshold (default: 0.60) proceed to trigger aggregation. Pages below this threshold are logged but not actioned.

**Outputs logged:** Run ID, source ID, per-candidate cross-encoder scores, reranked scores, graph-propagated pages and their decayed scores, final candidate list.

### 3.7 Stage 7 — Trigger Aggregation

**Purpose:** Before making LLM calls, group all triggers that exceeded thresholds for the same IPFR page within the current run window. This prevents the content owner receiving multiple separate notifications about the same page, and allows the LLM to reason about the combined effect of several upstream changes on a single IPFR page.

**Process:**

1. Collect all (source, IPFR page) pairs that survived Stage 6 in this run.
2. Group by IPFR page ID.
3. For each IPFR page, assemble a trigger bundle containing:
   - All relevant diffs (webpage diffs, FRL explainer documents, RSS extracts)
   - The corresponding source metadata (source ID, source URL, importance ranking)
   - All scores from Stages 4–6 for each trigger
4. Pass each trigger bundle to Stage 8 as a single unit.

**Outputs logged:** Run ID, per-IPFR-page trigger bundles (source IDs, score summaries), count of triggers per page.

### 3.8 Stage 8 — LLM Assessment

**Purpose:** For each IPFR page with grouped triggers, make a single LLM call to determine whether the page should be amended, and if so, produce specific, actionable suggestions.

**LLM call inputs:**

- **Cached system prompt.** A carefully authored prompt that instructs the model to: act as an IP content accuracy reviewer, produce structured JSON output conforming to a defined schema, and treat `UNCERTAIN` as a responsible and expected output rather than a failure mode. The prompt explicitly states: "If the relevance of the external change to the IPFR page is genuinely ambiguous — for example, because the change is tangentially related or the implications are unclear — output UNCERTAIN with your reasoning. Do not infer a change recommendation unless you are confident the IPFR page requires update." This framing prevents the model from resolving uncertainty by arbitrarily picking a side. The model is also instructed to avoid hallucinating legal references.
- **All relevant diffs** for this IPFR page: the webpage diff (for webpages), the explainer document (for FRL), or the extracted new content (for RSS).
- **The full IPFR page content** (loaded from the SQLite database).
- **Bi-encoder cosine scores** for each chunk pair, providing the model with a quantitative signal about which sections are most relevant.
- **Relevance scores** (lexical, semantic, and reranked) for each trigger, so the model can weigh the strength of the evidence.

**LLM call configuration:**

- Model: as specified in configuration (default: configurable)
- Temperature: low (default: 0.2) to reduce output variance
- Maximum tokens: sufficient for the defined JSON schema (default: 1000)

**Output JSON schema (validated before processing):**

```json
{
  "verdict": "CHANGE_REQUIRED",
  "confidence": 0.85,
  "reasoning": "The Trade Marks Amendment Act 2026 reduced the examination period from 12 to 6 months. This directly affects the guidance in Section 3.2 of B1012, which currently states '12 months'.",
  "suggested_changes": [
    "Update the processing timeframe from '12 months' to '6 months' to reflect the amended s.44 of the Trade Marks Act 1995."
  ]
}
```

The `verdict` field must be one of `CHANGE_REQUIRED`, `NO_CHANGE`, or `UNCERTAIN`. The `confidence` field is a float on [0.0, 1.0]. The `reasoning` field is populated for all three verdicts. The `suggested_changes` field is populated only for `CHANGE_REQUIRED` verdicts.

Every LLM response is validated against this schema. If validation fails:

- Retry the LLM call once with the same inputs.
- If the second call also fails validation, log the raw output and skip this IPFR page for this run. Record the failure in the health log.

**Outputs logged:** Run ID, IPFR page ID, stage identifier, model name, LLM response (raw and validated), schema validation result, retry count, processing time, prompt tokens, completion tokens, total tokens.

### 3.9 Stage 9 — Notification

**Purpose:** Send one consolidated email per run to the content owner, summarising all amendment suggestions from Stage 8.

**Email delivery:** GitHub Actions with Python `smtplib` and a Gmail app password stored as a repository secret.

**Email structure:**

- Subject line includes the run date and the number of IPFR pages flagged.
- Body contains one section per `CHANGE_REQUIRED` IPFR page, including:
  - The IPFR page identifier and title
  - The source(s) that triggered the alert, with links
  - The normalised diff text that was evaluated
  - The LLM's reasoning
  - The full text of each `suggested_changes` entry from the LLM response (not a summary)
  - A summary of the scoring evidence (fused relevance score, bi-encoder max, cross-encoder score)
- A separate **"Items requiring human review"** section lists all `UNCERTAIN` verdicts. Each entry includes the IPFR page identifier, the source, the LLM's reasoning for uncertainty, and the scoring evidence. These are editorial signals requiring human judgment, not recommended changes.
- A separate **"Candidates rejected at deep analysis"** section lists any items that passed BM25 and exceeded the bi-encoder threshold but were subsequently rejected at the cross-encoder or LLM stage. Each entry includes the source, the candidate IPFR page, and the stage at which rejection occurred. This section supports system calibration by surfacing potential false positives in upstream stages or over-conservative downstream filters.
- A "no alerts" email is not sent. If no pages are flagged, no email is generated (but the run is still logged).

**Feedback mechanism:** At the bottom of each page section in the email, four mailto links are provided:

1. **Useful** — the alert was accurate and the suggestion was helpful.
2. **Not a significant trigger event** — the change was real but not important enough to warrant an alert.
3. **Noteworthy trigger event but incorrect amendment** — the change was important, but the suggested amendment was wrong.
4. **Noteworthy trigger event but content influenced was incorrect** — the change was important, but the wrong IPFR page was flagged.

Each mailto link generates a pre-formatted reply containing the run ID, IPFR page ID, and trigger source(s). All options include space for a free-text comment. Replies are sent to a monitored mailbox.

**Feedback ingestion:** Feedback replies are collected via a dedicated Gmail account and processed by a scheduled GitHub Actions workflow (`feedback_ingestion.yml`). See Section 7.3 for the full setup and implementation details.

---

## 4. IP First Response Ingestion Pipeline

The IPFR ingestion pipeline creates and maintains the SQLite database that serves as the single source of truth for the content corpus that Tripwire monitors for potential impact. This pipeline runs before the main Tripwire pipeline and must complete before Tripwire begins its daily run.

### 4.1 Ingestion Steps

**Step 1: Sitemap Extraction.** A web scraper reads the IP First Response sitemap and populates the IPFR sitemap CSV with page URLs, titles, IPFR content identifiers (letter + four digits, e.g. B1000), links to local markdown snapshots, a "last modified" date column, and a "last checked" date column.

**Step 2: Page Scraping and Normalisation.** Each page on the IPFR sitemap is scraped and normalised into plain text using **trafilatura**. Trafilatura's built-in boilerplate removal strips navigation menus, footers, and sidebars automatically. The **plain text output** is the single canonical representation stored in the database (`content` column) and used for all similarity calculations. During the enrichment step only, trafilatura's **XML output** (which preserves `<head>`, `<p>`, `<list>` structural tags) is used transiently to determine section boundaries — `<head>` tags are parsed to identify heading positions, and the plain text is split at those boundaries to produce section-aware chunks. The XML is then discarded; only the plain text is persisted.

**Step 3: Change Detection (after initial run).** Every 24 hours, the pipeline checks whether the "Last modification date" for each IPFR page has changed. If yes, the page is re-scraped and continues through the full ingestion process. If no, the page is skipped.

**Step 4: Enrichment and Loading.** Each new or updated page is processed through the enrichment pipeline and loaded into the SQLite database. The enrichment process produces the following precomputed assets:

| Asset | Method | Purpose in Tripwire |
|---|---|---|
| Version hash | SHA-256 of normalised plain text | Fast change detection (hash comparison instead of full content comparison) |
| Document-level embedding | BAAI/bge-base-en-v1.5 | Quasi-graph construction (embedding neighbours) |
| Document chunks | Section-aware splitting using heading boundaries from trafilatura XML output | Unit of comparison for bi-encoder and cross-encoder stages |
| Chunk-level embeddings | BAAI/bge-base-en-v1.5 | Bi-encoder cosine similarity in Stage 5 |
| Named entity inventory | spaCy NER extraction | Stage 2 significance fingerprint and potential future fast-pass overrides |
| Keyphrase extraction | YAKE | BM25 scoring in Stage 4 |
| Section-level metadata | Heading hierarchy from trafilatura XML output and section boundaries | LLM assessment context (which sections are affected) |
| Quasi-graph edges | See Section 4.2 | Graph propagation in Stage 6 |

### 4.2 Quasi-Graph Construction

The quasi-graph captures relationships between IPFR pages so that alerts can propagate from directly-affected pages to related pages. Edges are derived from three sources (each configurable independently):

**Embedding neighbours (semantic).** Compute cosine similarity between all pairs of document-level embeddings. For each page, retain edges to the top-K most similar pages (default: 5) above a minimum similarity threshold (default: 0.40). Edge weight equals the cosine similarity score.

**Entity overlap (conceptual).** Compute the Jaccard coefficient of named entity sets between all pairs of pages. Retain edges where the Jaccard coefficient exceeds a minimum threshold (default: 0.30). Edge weight equals the Jaccard coefficient × a configurable scaling factor (default: 0.8).

**Internal links (structural — deferred).** Extract hyperlinks from each IPFR page that point to other IPFR pages. Each link creates a directed edge from the linking page to the linked page. Edge weight is a configurable constant (default: 0.6). This source is deferred until link extraction is implemented and is disabled in the initial configuration.

Where multiple edge sources produce edges between the same pair of pages, the edges are combined by taking the maximum weight across all sources.

---

## 5. Repository Structure

```
tripwire/
├── tripwire_config.yaml              # All tuneable parameters (see Section 7)
├── README.md
├── .gitattributes                    # Marks ipfr.sqlite as binary; LFS config if needed
├── .github/
│   └── workflows/
│       ├── tripwire.yml              # Main pipeline workflow (daily)
│       ├── ipfr_ingestion.yml        # IPFR corpus ingestion (daily, runs before tripwire)
│       └── feedback_ingestion.yml    # Feedback email parsing (triggered or scheduled)
│
├── src/
│   ├── config.py                     # Load and validate tripwire_config.yaml
│   ├── pipeline.py                   # Main pipeline orchestration (stage-by-stage)
│   ├── retry.py                      # Retry with exponential backoff, error classification
│   ├── errors.py                     # TripwireError, RetryableError, PermanentError
│   │
│   ├── stage1_metadata.py            # Metadata probe logic
│   ├── stage2_change_detection.py    # SHA-256 hash, word-level diff, significance fingerprint 
│   ├── stage3_diff.py                # Diff generation, FRL explainer retrieval, RSS extraction
│   ├── stage4_relevance.py           # BM25, bi-encoder similarity, RRF fusion, source importance
│   ├── stage5_biencoder.py           # Bi-encoder chunking and cosine similarity
│   ├── stage6_crossencoder.py        # Cross-encoder scoring, reranking, graph propagation
│   ├── stage7_aggregation.py         # Trigger grouping per IPFR page
│   ├── stage8_llm.py                 # LLM call, system prompt, JSON schema validation
│   ├── stage9_notification.py        # Email composition and sending
│   │
│   ├── scraper.py                    # Web scraping with trafilatura normalisation
│   ├── validation.py                 # Content validation after scraping
│   ├── health.py                     # Run health check and alert generation
│   └── observability.py              # Score distribution summary and reporting
│
├── ingestion/
│   ├── ingest.py                     # IPFR ingestion pipeline orchestration
│   ├── sitemap.py                    # Sitemap CSV construction
│   ├── scrape_ipfr.py                # IPFR page scraping with trafilatura normalisation
│   ├── enrich.py                     # Embedding, NER, YAKE, chunking
│   ├── graph.py                      # Quasi-graph edge computation
│   └── db.py                         # SQLite read/write operations (WAL mode)
│
├── data/
│   ├── logs/
│   │   └── feedback.jsonl            # Content owner feedback log
│   │
│   ├── influencer_sources/
│   │   ├── source_registry.csv       # Target URLs, headings, IDs, importance, frequency
│   │   └── snapshots/                # Current + retained snapshots of each influencer source (Git-tracked; committed after each run)
│   │
│   └── ipfr_corpus/
│       ├── ipfr.sqlite               # IPFR content, embeddings, NER, vectors, graph edges (Git-tracked binary; committed after each ingestion run)
│       ├── snapshots/                # Markdown snapshots of every IPFR page
│       ├── sitemap.csv               # IPFR sitemap with URLs, snapshot links, dates
│       └── precompute/               # Scripts/outputs for enrichment that feeds ipfr.sqlite
│
└── tests/
    ├── test_change_detection.py
    ├── test_relevance_scoring.py
    ├── test_semantic_matching.py
    ├── test_config_validation.py
    └── fixtures/                     # Sample pages, diffs, and expected outputs
```

---

## 6. Error Handling, Retries, and Observability

### 6.1 Error Classification

All errors are classified into two categories that determine how the pipeline responds:

**Retryable errors** are transient failures where a subsequent attempt may succeed. These include HTTP 5xx responses, connection timeouts, DNS resolution failures, LLM API rate limits, and SMTP connection failures. Retryable errors are retried with exponential backoff (base delay 2 seconds, max 3 retries, with jitter).

**Permanent errors** are failures where retrying will not help. These include HTTP 404 (page not found), HTTP 403 (access denied), content validation failures (CAPTCHA detected, content too short, dramatic size change), and repeated LLM schema validation failures (two consecutive calls returned invalid JSON). Permanent errors are logged and the source is skipped for this run.

### 6.2 Content Validation

After every web scrape, the returned content is validated before being accepted:

- **Minimum length check.** Content shorter than 200 characters is rejected as likely an error page or empty response.
- **CAPTCHA / bot detection.** The content is scanned for common bot-detection phrases ("captcha", "verify you are human", "robot check", "access denied", "please enable javascript"). If found, the scrape is treated as a permanent error.
- **Structural marker check.** Each source can define expected structural markers in the source registry (e.g. legislation pages should contain "Act" or "Regulation"). If none of the expected markers are present, the content is flagged for review.
- **Dramatic size change detection.** If the new content length is less than 30% or more than 300% of the previous snapshot's length, the content is flagged as suspicious rather than accepted as a legitimate change.

### 6.3 Stage-Level Error Isolation

Each source is processed independently within a `try/except/finally` block. A failure on Source A never prevents processing of Sources B through N. Every source always produces a log entry regardless of outcome — recording either a successful result with scores or an error with the stage at which failure occurred, the error type, and the error message.

### 6.4 Graceful Degradation

When a component is unavailable, the pipeline degrades rather than halts:

| Component | Failure Mode | Degradation |
|---|---|---|
| Individual source scrape | Timeout / 5xx | Retry 3×, skip source, log error |
| FRL explainer document | Unavailable | Fall back to diffing the legislation text directly |
| spaCy model | Fails to load | Skip significance fingerprint tagging in Stage 2; tag all changes as `significance: standard` |
| Bi-encoder model | Fails to load | Skip semantic stages; send alert based on lexical scores alone with a reduced-confidence flag |
| LLM API | Timeout / rate limit | Retry 3×; if still failing, store triggers in deferred_triggers.jsonl for next run |
| Email sending (SMTP) | Connection failure | Retry 3×; if still failing, write email content to local file and send health alert |
| SQLite database | Locked / corrupted | Abort run, send immediate health alert |

### 6.5 Deferred Triggers

When the LLM API is unavailable, triggers that have passed all prior stages are written to the `deferred_triggers` table in the SQLite database. At the start of the next run, before processing new sources, the pipeline checks for unprocessed deferred triggers and processes them through Stages 8–9 first. Deferred triggers are timestamped; triggers older than 7 days are discarded (the next full run will regenerate them if the change is still relevant).

### 6.6 Health Alerting

After every run, the pipeline computes summary health statistics and evaluates alert conditions:

- **Error rate > 30%** in a single run → send health alert email.
- **Same source fails 3 consecutive runs** → send health alert email identifying the source.
- **Pipeline fails to complete within timeout** (default: 30 minutes via GitHub Actions `timeout-minutes`) → GitHub Actions failure notification.
- **LLM produces malformed output ≥ 2 times in a run** → send health alert email.
- **Cross-encoder truncation occurs ≥ 3 times in a run** → send health alert email identifying the affected source/page pairs.

Health alerts are sent to a separate email address (the system operator) and are distinct from content-owner notification emails.

### 6.7 Observability

The `pipeline_runs` table in the SQLite database is the primary observability layer. A weekly summary script queries the last 30 days of run data and produces a report containing:

- **Reliability table:** For each source — total runs, successful runs, error count, last error date, current consecutive-success or consecutive-failure streak.
- **Score distributions:** For each scoring stage — min, 25th percentile, median, 75th percentile, max across all runs. Used to assess whether thresholds are in reasonable ranges.
- **Alert volume:** Number of alerts generated per week, trending over time.
- **Feedback summary:** Of alerts with feedback received, the proportion rated "useful" vs each non-useful category. This is the system's precision metric.

---

## 7. Configuration

All tuneable weights, thresholds, model identifiers, and behavioural parameters are exposed in a single YAML file at the repository root: `tripwire_config.yaml`. This file is version-controlled in Git so that every parameter change is tracked as a commit.

The configuration file is loaded and validated at the start of every pipeline run. Validation checks include: relevance weights sum to 1.0, all thresholds are within valid ranges, model names are recognised, and required file paths exist. If validation fails, the pipeline exits with a clear error message before processing any sources.

A full snapshot of the active configuration is included in the `details` JSON column of the `pipeline_runs` table for each run, ensuring that historical runs can always be interpreted in the context of the parameters that were in effect at the time.

```yaml
# ==============================================================
# tripwire_config.yaml
# ==============================================================

# --- Pipeline behaviour ---
pipeline:
  observation_mode: true             # true = log everything, trigger nothing
  run_frequency_hours: 24
  max_retries: 3
  retry_base_delay_seconds: 2.0
  llm_temperature: 0.2
  llm_model: "gpt-4o"
  deferred_trigger_max_age_days: 7

# --- Stage 2: Change detection ---
change_detection:
  significance_fingerprint: true

# --- Stage 4: Relevance scoring ---
relevance_scoring:
  rrf_k: 60
  rrf_weight_bm25: 1.0
  rrf_weight_semantic: 2.0
  top_n_candidates: 5
  min_score_threshold: null            # set during observation period
  source_importance_floor: 0.5
  fast_pass:
    source_importance_min: 1.0
  yake:
    keyphrases_per_80_words: 1
    min_keyphrases: 5
    max_keyphrases: 15
    short_diff_word_threshold: 50   # supplement with NER entities below this length

# --- Stages 5–6: Semantic scoring ---
semantic_scoring:
  biencoder:
    model: "BAAI/bge-base-en-v1.5"
    high_threshold: 0.75
    low_medium_threshold: 0.45
    low_medium_min_chunks: 3
  crossencoder:
    model: "gte-reranker-modernbert-base"
    threshold: 0.60
    max_context_tokens: 8192

# --- Stage 6: Graph propagation ---
graph:
  enabled: true
  max_hops: 3
  decay_per_hop: 0.45
  propagation_threshold: 0.05
  edge_types:
    embedding_similarity:
      enabled: true
      weight: 1.0
      top_k: 5
      min_similarity: 0.40
    entity_overlap:
      enabled: true
      weight: 0.8
      min_jaccard: 0.30
    internal_links:
      enabled: false
      weight: 0.6

# --- Storage ---
storage:
  content_versions_retained: 6
  sqlite_wal_mode: true
  git_persistence:
    enabled: true
    commit_snapshots: true           # commit data/influencer_sources/snapshots/ after each run
    commit_database: true            # commit data/ipfr_corpus/ipfr.sqlite after each ingestion run
    commit_author: "github-actions[bot] <github-actions[bot]@users.noreply.github.com>"

# --- Notifications ---
notifications:
  content_owner_email: "content-owner@example.gov.au"
  health_alert_email: "admin@example.gov.au"
  health_alert_conditions:
    error_rate_threshold: 0.30
    consecutive_failures_threshold: 3
    pipeline_timeout_minutes: 30

# --- Normalisation ---
normalisation:
  tool: "trafilatura"
```

---

## 7.1 CI/CD Configuration

**CPU-only PyTorch.** GitHub Actions runners are CPU-only. PyTorch must be installed from the CPU-only index to avoid downloading ~1.5–2 GB of unused CUDA libraries:

```
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

This provides identical inference performance at ~200 MB, saving disk space and reducing workflow setup time by 30–60 seconds.

**Model caching.** Cache the `~/.cache/huggingface/` directory using `actions/cache@v4` to avoid re-downloading models (BAAI/bge-base-en-v1.5 ~400 MB, gte-reranker-modernbert-base ~600 MB) on every run.

**Future optimisation.** ONNX quantised inference is an option for 2–4× CPU speedup if runtime becomes a constraint.

---

## 7.2 Persistent Storage

GitHub Actions runners are ephemeral: the workspace is wiped at the end of every job. Any file that is not explicitly persisted is lost. `actions/cache` (used for Hugging Face model weights) is not a reliable persistence mechanism for data that must survive across runs — cache entries can be evicted at any time without notice.

**Persistence strategy: commit to the repository.**

Both categories of state that must survive between runs are committed back to the repository at the end of each workflow run:

| Asset | Path | Why Git is suitable |
|---|---|---|
| Influencer snapshots | `data/influencer_sources/snapshots/` | Plain text (trafilatura output) — human-readable, diff-friendly, small per-file |
| IPFR SQLite database | `data/ipfr_corpus/ipfr.sqlite` | Binary, but small enough for direct Git tracking; upgrade to Git LFS if it exceeds ~50 MB |

**Commit step (added to both workflows).** After all processing is complete, each workflow runs a post-processing step:

```yaml
- name: Commit updated snapshots and database
  run: |
    git config user.name  "github-actions[bot]"
    git config user.email "github-actions[bot]@users.noreply.github.com"
    git add data/influencer_sources/snapshots/
    git add data/ipfr_corpus/ipfr.sqlite
    git diff --cached --quiet || git commit -m "chore: update snapshots and database [run ${RUN_ID}]"
    git push
  env:
    RUN_ID: ${{ github.run_id }}
```

The `git diff --cached --quiet || git commit` guard ensures no empty commits are created on runs where nothing changed.

**`.gitattributes` configuration.** The `.gitattributes` file at the repository root marks the SQLite file as binary so that Git does not attempt text diffs or line-ending normalisation on it:

```
data/ipfr_corpus/ipfr.sqlite  binary
```

If the database grows beyond ~50 MB, migrate to Git LFS by replacing the `binary` attribute with `filter=lfs diff=lfs merge=lfs -text`. No other changes are required — the commit step is identical.

**Read-before-write.** Each workflow performs a `git pull --rebase` as its first step to ensure it starts from the latest committed state, preventing conflicts between the ingestion and Tripwire workflows:

```yaml
- name: Pull latest committed state
  run: git pull --rebase origin main
```

**What `actions/cache` is still used for.** Model weight caching remains unchanged — Hugging Face model files (~1 GB combined) are cached under `~/.cache/huggingface/` using `actions/cache@v4`. Model weights do not need to survive run failures; a cache miss simply triggers a fresh download.

---

## 7.3 Feedback Ingestion — Gmail IMAP Polling

### Overview

The Stage 9 notification email includes four mailto feedback links. When the content owner clicks a link, their email client opens a pre-formatted reply containing the run ID, IPFR page ID, source ID, and feedback category as structured text. A scheduled GitHub Actions workflow (`feedback_ingestion.yml`) polls a dedicated Gmail mailbox for these replies, parses them, and appends them to `data/logs/feedback.jsonl`. Over time, this log enables empirical threshold calibration by correlating score profiles with human judgments.

### One-time setup

1. Create a dedicated Gmail account (e.g. `tripwire-feedback@gmail.com`). This address serves as both the `Reply-To` address on notification emails and the mailbox the workflow monitors.
2. Enable IMAP access in Gmail Settings → See all settings → Forwarding and POP/IMAP.
3. Generate a Gmail App Password (Settings → Security → 2-Step Verification → App Passwords). Store it as a GitHub Actions repository secret named `FEEDBACK_GMAIL_APP_PASSWORD`.

### Mailto template constraint

The pre-formatted reply body already contains all required fields (`run_id`, `page_id`, `source_id`, feedback category). One additional constraint is applied to the mailto template: prefix the subject line with a fixed tag — `[TRIPWIRE]`. This lets the IMAP query filter precisely for Tripwire replies without touching other mail:

```
Subject: [TRIPWIRE] Feedback — {run_id} — {page_id}
```

### Parsing logic

The workflow uses only Python standard library modules — no extra dependencies:

```python
import imaplib, email, os

mail = imaplib.IMAP4_SSL("imap.gmail.com")
mail.login("tripwire-feedback@gmail.com", os.environ["FEEDBACK_GMAIL_APP_PASSWORD"])
mail.select("inbox")
_, ids = mail.search(None, 'UNSEEN SUBJECT "[TRIPWIRE]"')
```

For each unread `[TRIPWIRE]`-tagged message, the workflow:

1. Parses the subject and body to extract `run_id`, `page_id`, `source_id`, and feedback category.
2. Appends a record to `data/logs/feedback.jsonl` tagged with those fields plus any free-text comment and an ingestion timestamp.
3. Marks the message as read.
4. Commits and pushes the updated `feedback.jsonl` to the repository.

If parsing fails for a message (malformed reply or missing fields), the message is left unread and a warning is logged — it will be retried on the next cycle.

### Workflow schedule

`feedback_ingestion.yml` runs on a 6-hour schedule (cron: `0 */6 * * *`) and can also be triggered manually via `workflow_dispatch`. The 6-hour cadence keeps the feedback log reasonably current without requiring real-time infrastructure.

```yaml
on:
  schedule:
    - cron: "0 */6 * * *"
  workflow_dispatch:
```

### Why this approach

- No external services, DNS configuration, webhooks, or OAuth dance — just a secret and standard-library Python.
- The Gmail mailbox doubles as a human-readable audit trail: unread count is a passive health signal, and replies are searchable by a human operator.
- Volume comfortably fits within Gmail's free limits (expected: tens of feedback emails per month).
- **Migration path:** when volume or reliability requirements grow, replace the IMAP poller with a Mailgun inbound-parse webhook that fires a `repository_dispatch` event. The parsing logic and `feedback.jsonl` schema are unchanged — only the trigger mechanism is swapped.

### What this unblocks for Phase 5

All four feedback categories (useful / not significant / wrong amendment / wrong page) are captured with run ID, page ID, and source ID — sufficient to compute precision metrics and drive the threshold calibration grid search described in tasks 5.3 and 5.4.

---

## 7.4 Lazy Model Loading

### Motivation

The two inference models required by the pipeline are large and slow to load on a CPU-only runner:

| Model | Size | Approximate load time (CPU) |
|---|---|---|
| BAAI/bge-base-en-v1.5 (bi-encoder) | ~400 MB | ~25 s |
| gte-reranker-modernbert-base (cross-encoder) | ~600 MB | ~35 s |

If both models are loaded unconditionally at workflow start, every run pays ~60 s and ~1 GB of RAM — even on the majority of runs where no source reaches Stage 5. Lazy loading defers each model until the moment it is actually needed.

### Loading strategy

```
Workflow start
  ↓
Stages 1–4   [no models loaded]
  ↓
Any candidate reaches Stage 5?
  ├── No  → skip to Stage 7; neither model ever loaded
  └── Yes → load bi-encoder → run Stage 5
               ↓
            Any candidate survives Stage 5?
              ├── No  → release bi-encoder; cross-encoder never loaded
              └── Yes → release bi-encoder → load cross-encoder → run Stage 6
```

The bi-encoder is released from memory before the cross-encoder is loaded. This keeps peak RAM at ~600 MB (the larger model alone) rather than ~1 GB (both simultaneously), providing comfortable headroom within the runner's 7 GB limit.

### Practical savings

| Scenario | Without lazy loading | With lazy loading | Saving |
|---|---|---|---|
| No changes detected (most days) | Load both models: ~60 s | Load neither: 0 s | ~60 s |
| Changes reach Stage 5 only | Load both models: ~60 s | Load bi-encoder only: ~25 s | ~35 s |
| Full pipeline executes | Load both models: ~60 s | Sequential load: ~60 s | 0 s |

On a typical no-change day, lazy loading reduces total run time by ~14% and eliminates all model-related RAM usage.

### Runtime budget

For reference, the full back-of-envelope runtime on a standard GitHub Actions runner (2-core CPU, ~7 GB RAM), assuming ~25 sources pass Stage 1, ~10 pass Stage 2, and ~5 reach Stage 5:

| Phase | Operation | Estimate |
|---|---|---|
| Workflow setup | Python env + pip (cached) | ~90 s |
| Cache restore | Hugging Face weights (~1 GB) | ~45 s |
| Stage 1 | HTTP HEAD × 80 sources @ ~0.5 s each | ~40 s |
| Stage 2 | Trafilatura scrape × 25 @ ~3 s + spaCy fingerprint | ~80 s |
| Stage 3 | Diff generation (in-memory) | ~5 s |
| Stage 4 | YAKE + BM25 + bi-encoder encode × 10 diffs | ~15 s |
| Bi-encoder load (lazy) | BAAI/bge-base-en-v1.5 (~400 MB, CPU) | ~25 s |
| Stage 5 | Encode ~50 chunks × 0.5 s, cosine vs precomputed embeddings | ~30 s |
| Cross-encoder load (lazy) | gte-reranker-modernbert-base (~600 MB, CPU) | ~35 s |
| Stage 6 | 5 candidates × ~4 s cross-encoder inference | ~20 s |
| Stage 8 | 2–3 LLM API calls @ ~10 s each | ~25 s |
| Stage 9 + git commit | Email + push snapshots | ~20 s |
| **Total (full pipeline)** | | **~430 s ≈ 7 minutes** |

The 30-minute `timeout-minutes` budget (see Section 6.6) provides ~23 minutes of headroom on a typical run. Even a worst-case busy day lands around 18–20 minutes — comfortably within budget. The dominant cost is not compute but model loading (~60 s combined on a full run) and HTTP I/O during Stages 1–2.

### Relationship to model caching

Lazy loading and model weight caching (Section 7.1) are complementary. `actions/cache` ensures model weights are already on disk at the start of the workflow — lazy loading controls when those weights are read into memory during pipeline execution.

---

## 8. Pipeline Run Logging

Pipeline run data is logged directly to a `pipeline_runs` table in the existing IPFR SQLite database, with a `details` JSON column for per-stage structured data. SQLite's JSON1 extension (`json_extract()`) enables querying into the structured details. Deferred triggers are stored in a `deferred_triggers` table in the same database.

### 8.1 SQLite Logging Tables

```sql
-- Pipeline run log: one row per source per run
CREATE TABLE pipeline_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,          -- e.g. "2026-04-05-001"
    source_id       TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    source_type     TEXT NOT NULL,          -- "webpage", "frl", "rss"
    timestamp       TEXT NOT NULL,          -- ISO 8601
    stage_reached   TEXT NOT NULL,
    outcome         TEXT NOT NULL,          -- "completed", "no_change", "error"
    error_type      TEXT,
    error_message   TEXT,
    triggered_pages TEXT,                   -- JSON array of page IDs
    duration_seconds REAL,
    details         TEXT NOT NULL           -- JSON object with full per-stage data
);

-- Deferred triggers: stored when LLM API is unavailable
CREATE TABLE deferred_triggers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    ipfr_page_id    TEXT NOT NULL,
    trigger_data    TEXT NOT NULL,          -- JSON object with scores and diffs
    created_at      TEXT NOT NULL,          -- ISO 8601
    processed       INTEGER DEFAULT 0       -- 0 = pending, 1 = processed
);
```

### 8.2 GitHub Actions Job Summary

At the end of each run, a formatted markdown summary is written to `$GITHUB_STEP_SUMMARY`. This renders directly in the GitHub Actions UI and is visible to anyone with repository access. The summary includes: sources checked, changes detected, scores, verdicts, and any errors.

For full raw detail, the current run's log entry is published as a **GitHub Actions workflow artifact** using `actions/upload-artifact@v4` (retained for 90 days by default).

### 8.3 Example Log Entry (details column)

The `details` JSON column contains the same structured per-stage data previously logged as JSONL. A full entry for a source that triggers an alert:

```json
{
  "config_snapshot": { "...": "full config at time of run" },
  "stages": {
    "metadata_probe": {
      "changed": true,
      "signals": { "content_length_changed": true, "etag_changed": false }
    },
    "change_detection": {
      "skipped": true,
      "reason": "FRL source — change detection not applied"
    },
    "diff": {
      "type": "explainer_document",
      "size_chars": 3420
    },
    "relevance": {
      "bm25_rank": 1,
      "semantic_rank": 2,
      "rrf_score": 0.048,
      "source_importance": 0.70,
      "final_score": 0.041,
      "fast_pass_triggered": false,
      "decision": "proceed"
    },
    "biencoder": {
      "candidate_pages": [
        {
          "ipfr_page_id": "B1012",
          "max_chunk_score": 0.81,
          "chunks_above_low_medium": 5,
          "trigger_reason": "single_chunk_high"
        }
      ]
    },
    "crossencoder": {
      "scored_pages": [
        {
          "ipfr_page_id": "B1012",
          "crossencoder_score": 0.74,
          "reranked_score": 0.78,
          "graph_propagated_to": ["C2003"],
          "decision": "proceed"
        }
      ]
    },
    "llm_assessment": {
      "ipfr_page_id": "B1012",
      "stage_id": "stage8_llm",
      "model": "gpt-4o",
      "verdict": "CHANGE_REQUIRED",
      "confidence": 0.85,
      "schema_valid": true,
      "retries": 0,
      "prompt_tokens": 1842,
      "completion_tokens": 214,
      "total_tokens": 2056,
      "timestamp": "2026-04-05T02:14:45Z"
    }
  }
}
```

Feedback continues to be logged in `feedback.jsonl` (appended by an external process parsing email replies).

---

## 9. SQLite Schema — IPFR Corpus

The IPFR SQLite database uses WAL (Write-Ahead Logging) mode to allow concurrent reads during writes. The ingestion pipeline writes to this database; the Tripwire pipeline reads from it.

```sql
-- Pages table: one row per IPFR page
CREATE TABLE pages (
    page_id         TEXT PRIMARY KEY,     -- e.g. "B1012"
    url             TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    version_hash    TEXT NOT NULL,         -- SHA-256 of normalised plain text
    last_modified   TEXT,                  -- ISO 8601 date from IPFR sitemap
    last_checked    TEXT,                  -- ISO 8601 date of last ingestion check
    last_ingested   TEXT,                  -- ISO 8601 date of last full ingestion
    doc_embedding   BLOB                  -- document-level embedding (BAAI/bge-base-en-v1.5)
);

-- Chunks table: one row per chunk of each page
CREATE TABLE chunks (
    chunk_id        TEXT PRIMARY KEY,     -- e.g. "B1012-chunk-003"
    page_id         TEXT NOT NULL REFERENCES pages(page_id),
    chunk_text      TEXT NOT NULL,
    chunk_index     INTEGER NOT NULL,     -- positional index within the page
    section_heading TEXT,                  -- nearest heading above this chunk
    chunk_embedding BLOB NOT NULL         -- chunk-level embedding (BAAI/bge-base-en-v1.5)
);

-- Entities table: named entities extracted per page
CREATE TABLE entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id         TEXT NOT NULL REFERENCES pages(page_id),
    entity_text     TEXT NOT NULL,
    entity_type     TEXT NOT NULL,         -- e.g. "LEGISLATION", "ORG", "SECTION", "DATE"
    UNIQUE(page_id, entity_text, entity_type)
);

-- Keyphrases table: YAKE-extracted keyphrases per page
CREATE TABLE keyphrases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id         TEXT NOT NULL REFERENCES pages(page_id),
    keyphrase       TEXT NOT NULL,
    score           REAL NOT NULL          -- YAKE score
);

-- Graph edges: quasi-graph relationships between IPFR pages
CREATE TABLE graph_edges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_page_id  TEXT NOT NULL REFERENCES pages(page_id),
    target_page_id  TEXT NOT NULL REFERENCES pages(page_id),
    edge_type       TEXT NOT NULL,         -- "embedding_similarity", "entity_overlap", "internal_link"
    weight          REAL NOT NULL,
    UNIQUE(source_page_id, target_page_id, edge_type)
);

-- Section metadata: heading hierarchy per page
CREATE TABLE sections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id         TEXT NOT NULL REFERENCES pages(page_id),
    heading_text    TEXT NOT NULL,
    heading_level   INTEGER NOT NULL,      -- 1 = H1, 2 = H2, etc.
    char_start      INTEGER NOT NULL,      -- character offset in content
    char_end        INTEGER NOT NULL
);
```

---

## 10. Phased Implementation Plan

### Phase 1 — Foundation 

| # | Task | Depends On |
|---|---|---|
| 1.1 | Create repository structure and `tripwire_config.yaml` | — |
| 1.2 | Implement `config.py` (load, validate, snapshot) | 1.1 |
| 1.3 | Implement `errors.py` and `retry.py` (error classes, exponential backoff) | — |
| 1.4 | Define and create the IPFR SQLite schema (`db.py`) | — |
| 1.5 | Build IPFR ingestion pipeline: sitemap → scrape → normalise to markdown | 1.4 |
| 1.6 | Build IPFR enrichment: chunking, embeddings, NER, YAKE, section metadata | 1.5 |
| 1.7 | Build quasi-graph edge computation (embedding neighbours + entity overlap) | 1.6 |
| 1.8 | Implement SQLite pipeline run logging and GitHub Actions Job Summary | 1.2, 1.4 |
| 1.9 | Build the influencer source registry CSV and metadata probe (Stage 1) | 1.2 |
| 1.10 | Set up GitHub Actions workflow for IPFR ingestion (daily) | 1.5–1.7 |

### Phase 2 — Change Detection 

| # | Task | Depends On |
|---|---|---|
| 2.1 | Implement web scraping with trafilatura normalisation and content validation | 1.3 |
| 2.2 | Implement Stage 2: SHA-256 hash check, word-level diff, significance fingerprint tagger | 2.1 |
| 2.3 | Implement Stage 3: diff generation, FRL explainer retrieval, RSS extraction | 2.2 |
| 2.4 | Implement snapshot storage, 6-version retention, and end-of-run Git commit/push (see Section 7.2) | 2.1 |
| 2.5 | Create 10–15 manually altered snapshots for threshold testing | 2.2 |
| 2.6 | Run in observation mode; collect score distributions; refine Stage 2 thresholds | 2.2–2.4 |

### Phase 3 — Relevance and Semantic Scoring 

| # | Task | Depends On |
|---|---|---|
| 3.1 | Implement Stage 4: YAKE-driven BM25, bi-encoder semantic similarity, weighted RRF fusion, source importance multiplier | 1.6, 2.3 |
| 3.2 | Implement fast-pass override logic (source importance = 1.0) | 3.1 |
| 3.3 | Implement Stage 5: bi-encoder chunking and cosine similarity | 1.6 |
| 3.4 | Implement Stage 6: cross-encoder scoring, reranking, graph propagation | 1.7, 3.3 |
| 3.5 | Continue observation mode; extend to Stages 4–6; refine all thresholds | 3.1–3.4 |

### Phase 4 — LLM Assessment and Notification 

| # | Task | Depends On |
|---|---|---|
| 4.1 | Define LLM output JSON schema and validation logic | — |
| 4.2 | Author the LLM system prompt | — |
| 4.3 | Implement Stage 7: trigger aggregation per IPFR page | 3.4 |
| 4.4 | Implement Stage 8: LLM call with schema validation and retry | 4.1–4.3 |
| 4.5 | Implement deferred trigger mechanism for LLM failures | 4.4 |
| 4.6 | Implement Stage 9: consolidated email with feedback mailto links | 4.4 |
| 4.7 | Implement feedback ingestion (parse replies → feedback.jsonl) | 4.6 |
| 4.8 | Set up main Tripwire GitHub Actions workflow (daily, after ingestion) | All |
| 4.9 | Disable observation mode; begin live operation | All |

### Phase 5 — Hardening and Iteration 

| # | Task | Depends On |
|---|---|---|
| 5.1 | Implement health alerting (error rate, consecutive failures, timeout) | 4.8 |
| 5.2 | Implement weekly observability summary report | 1.8 |
| 5.3 | Calibrate thresholds using accumulated feedback data | 4.7, 4.9 |
| 5.4 | Evaluate alternative relevance weights via grid search against feedback log | 5.3 |
| 5.5 | Enable internal-link graph edges when link extraction is ready | 1.7 |
| 5.6 | Evaluate positional/proximity BM25 extensions if standard BM25 proves insufficient | 3.1 |
| 5.7 | Write operational runbooks: failure response, adding sources, adjusting thresholds | All |
