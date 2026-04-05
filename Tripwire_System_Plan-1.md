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
│  STAGE 2: Change Significance                                   │
│  Was the change meaningful?                                     │
│  Three-channel detector (OR-logic):                             │
│    • Simhash Hamming distance                                   │
│    • Jaccard index on tri-grams                                 │
│    • Significance fingerprint (defined terms, numbers, dates)   │
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
│  Four signals, normalised and fused:                            │
│    • TF-IDF cosine similarity           (weight: 0.30)          │
│    • spaCy NER overlap                  (weight: 0.25)          │
│    • RAKE-driven BM25                   (weight: 0.30)          │
│    • Source importance metadata          (weight: 0.15)          │
│  Scored against diff (0.7 weight) + full new version (0.3)      │
│  Fast-pass overrides for exceptional single-signal strength     │
│  ── moderate cost, runs only on sources that passed Stage 2 ──  │
└──────────────┬──────────────────────────────────────────────────┘
               │ fused score exceeds threshold (default 0.35)
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 5: Semantic Matching — Bi-Encoder                        │
│  Which IPFR pages are most likely affected? (coarse pass)       │
│    • Chunk the incoming change document                         │
│    • Compute cosine similarity against IPFR content chunks      │
│      using all-MiniLM-L6-v2                                    │
│  Proceed if:                                                    │
│    • Any single chunk scores ≥ 0.75, OR                         │
│    • 3+ chunks from the same IPFR page score ≥ 0.45            │
└──────────────┬──────────────────────────────────────────────────┘
               │ candidate IPFR pages identified
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 6: Semantic Matching — Cross-Encoder                     │
│  Which IPFR pages are most likely affected? (precise pass)      │
│    • Score candidate pairs with ms-marco-MiniLM-L-6-v2          │
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
| **Webpage** | Scrape and normalise with inscriptis, then apply three-channel detector | Produce `.diff` file from old vs new snapshot | Diff (0.7 weight) + full new page (0.3 weight) |
| **Federal Register of Legislation** | Skipped — FRL publishes structured change information | Retrieve the change explainer document | Change explainer document only (no weighting split) |
| **RSS Feed** | Skipped — RSS items are inherently new content | Extract new items since last check | New items only (no weighting split) |

### 2.3 Observation Mode

On initial deployment, the pipeline runs in **observation mode**. All stages execute and all scores are logged, but no alerts are triggered and no emails are sent. This mode serves two purposes: it allows threshold calibration based on real score distributions, and it validates that each stage is producing sensible outputs before the system begins generating notifications.

Observation mode is controlled by a single boolean in the configuration file. When observation mode is active, the pipeline runs end-to-end, records everything to the JSONL log, and exits after Stage 6 (skipping LLM calls and email notifications to save cost). A summary report of score distributions is generated instead, to support manual threshold review.

During the initial calibration period (4–8 weeks recommended), the operator should also manually alter markdown snapshots of 10–15 influencer sources to test the sensitivity of each gate. This provides controlled ground-truth data for setting thresholds before real-world changes are available.

---

## 3. Stage Specifications

### 3.1 Stage 1 — Metadata Probe

**Purpose:** Determine whether a source has changed at all since the last check, using the cheapest possible signals. Sources that haven't changed are immediately skipped.

**Frequency:** Each source has a configured check frequency (daily, weekly, fortnightly, monthly, or quarterly) defined in the influencer source registry CSV. The pipeline runs every 24 hours (after the IPFR corpus ingestion run has completed) but only probes each source when its scheduled check is due.

**Probe signals (check any available, source-dependent):**

- HTTP `ETag` or `Last-Modified` header comparison against stored values
- Content-Length header comparison
- Version identifier (if the source publishes one, e.g. FRL compilation numbers)
- RSS feed: presence of items with publication dates newer than the last-checked timestamp

**Decision rule:** If any probe signal indicates a change, proceed to Stage 2. If no signals are available (e.g. the server doesn't return useful headers), always proceed to Stage 2 — the cost of an unnecessary scrape is low.

**Outputs logged:** Run ID, source ID, source URL, timestamp, probe signals collected, decision (changed / unchanged / unknown).

### 3.2 Stage 2 — Change Significance

**Purpose:** Determine whether a detected change is meaningful or merely cosmetic (e.g. a timestamp update, a CSS class rename, a whitespace change). This stage is only applied to **webpages**. FRL sources and RSS feeds bypass this stage because their change information is already structured.

**Prerequisite:** For webpages, the new page is scraped and normalised into plaintext using **inscriptis** before any comparison. The previous snapshot is loaded from storage.

**Three-channel detector (OR-logic — any one channel flagging a change is sufficient to proceed):**

**Channel 1: Simhash Hamming Distance.** Compute the simhash of the old and new normalised text. Calculate the Hamming distance between the two hashes. If the distance exceeds the configured minimum (default: 3 bits), flag as changed. Simhash captures global document-level similarity and is sensitive to distributed changes across the document.

**Channel 2: Jaccard Index on Tri-grams.** Extract the set of character-level or word-level tri-grams from the old and new text. Compute the Jaccard index (`|intersection| / |union|`). If the Jaccard index drops below `1.0 - threshold` (default threshold: 0.05, meaning Jaccard drops below 0.95), flag as changed. Jaccard on n-grams captures local phrase-level overlap and is sensitive to insertions, deletions, and substitutions of specific phrases.

**Channel 3: Significance Fingerprint.** Using spaCy and regex, extract the following from both old and new versions:

- Defined terms (capitalised terms that appear to be legal definitions)
- Numerical values (dollar amounts, time periods, percentages, section numbers)
- Dates (commencement dates, deadline dates, amendment dates)
- Cross-references (references to other Acts, sections, or regulations)
- Modal verbs in legal context ("may", "must", "shall", "should")

Compare the extracted sets. If any element has been added, removed, or changed, flag as changed. This channel exists specifically to catch small but legally significant changes (e.g. "may" → "must", "12 months" → "6 months") that barely move distributional metrics.

**Decision rule:** If any one of the three channels flags a change, proceed to Stage 3. If none flag a change, log the result and stop processing this source for this run.

**Outputs logged:** Run ID, source ID, simhash Hamming distance, Jaccard index, significance fingerprint details (what changed), decision.

### 3.3 Stage 3 — Diff Generation

**Purpose:** Produce a precise representation of what changed, formatted appropriately for the source type.

**Webpage sources:** Generate a unified `.diff` file comparing the previous normalised snapshot against the new normalised snapshot. Store the diff file in the run's working directory. Update the stored snapshot to the new version. Retain the previous 6 versions of the snapshot for audit purposes.

**FRL sources:** Retrieve the change explainer document associated with the new compilation or amendment. FRL publishes these as companion documents to legislative changes. If the explainer document is unavailable, fall back to treating the FRL source like a webpage (diff the legislation text directly) and log a warning.

**RSS sources:** Extract all items with publication dates newer than the last-checked timestamp. These items are the "diff" — they represent new information that did not exist at the time of the last check. There is no "old vs new" comparison because RSS feeds are append-only.

**Outputs logged:** Run ID, source ID, source type, diff file path (or explainer document path, or extracted RSS items), diff size in characters.

### 3.4 Stage 4 — Relevance Scoring

**Purpose:** Determine whether the detected change is potentially relevant to the IPFR corpus as a whole, before attempting to identify which specific IPFR pages are affected.

**Input composition (source-type dependent):**

- Webpages: Score against the `.diff` file (weight 0.7) and the full new version of the page (weight 0.3). The primary signal is "is what changed relevant?" while the secondary signal captures context (e.g. a diff that says "Section 12 amended" gains meaning from knowing it's Section 12 of the Trade Marks Act).
- FRL: Score against the change explainer document only. No weighting split.
- RSS: Score against the extracted new items only. No weighting split.

**Four scoring signals:**

**Signal 1: TF-IDF Cosine Similarity (weight 0.30).** Compute TF-IDF vectors for the input document and for the IPFR corpus (using precomputed TF-IDF vectors stored in the IPFR SQLite database). Calculate cosine similarity. This captures broad topical relevance — whether the change document discusses similar subjects to the IPFR content. The score is already on [0, 1].

**Signal 2: spaCy NER Overlap (weight 0.25).** Extract named entities from the input document using spaCy. Compare against the precomputed entity inventories stored per IPFR page. Compute the Jaccard coefficient (`|intersection| / |union|`) of the entity sets. This captures whether the same legislation, section numbers, organisations, or defined terms appear in both the change and the IPFR content. The Jaccard coefficient is already on [0, 1].

**Signal 3: RAKE-driven BM25 (weight 0.30).** Run RAKE keyword extraction on the input document to identify key phrases. Use these as query terms against a BM25 index built from the IPFR corpus (using precomputed keyphrases). This captures keyword-level relevance with term frequency weighting. BM25 scores are unbounded; normalise by dividing by the 95th percentile of all BM25 scores observed during the observation period, capping at 1.0.

**Signal 4: Source Importance (weight 0.15).** Look up the importance ranking (a float on [0, 1]) from the influencer source registry CSV. This is a manually assigned prior reflecting how significant the source is to IPFR content. For example, the Trade Marks Act might be rated 1.0 while a peripheral WIPO news feed might be rated 0.3. This signal should influence the decision but not dominate it.

**Fusion:**

```
relevance_score = (0.30 × tfidf) + (0.25 × ner_overlap) + (0.30 × bm25_norm) + (0.15 × importance)
```

All four component scores are logged separately alongside the fused score, enabling retroactive testing of alternative weight combinations against feedback data.

**Fast-pass overrides:** If NER overlap ≥ 0.80, proceed regardless of the fused score. If source importance = 1.0, proceed regardless of the fused score. Fast-pass rules encode domain knowledge that cannot be captured in a linear weighting.

**Decision rule:** If the fused relevance score exceeds the threshold (default: 0.35), or any fast-pass condition is met, proceed to Stage 5.

**Outputs logged:** Run ID, source ID, all four component scores (raw and normalised), weights used, fused score, threshold, fast-pass triggered (boolean), decision.

### 3.5 Stage 5 — Semantic Matching: Bi-Encoder

**Purpose:** Identify which specific IPFR pages are most likely affected by the change. This is a coarse-grained semantic pass using a bi-encoder to efficiently compare against all IPFR content chunks.

**Process:**

1. Chunk the incoming change document (webpage diff, FRL explainer, or RSS items) using the same chunking strategy applied during IPFR ingestion, so that chunk sizes are comparable.
2. Encode each chunk using the **all-MiniLM-L6-v2** bi-encoder model.
3. Compute cosine similarity between each change-document chunk and every precomputed IPFR content chunk embedding stored in the SQLite database.
4. For each IPFR page, record the highest single-chunk cosine score and the count of chunks exceeding the low-medium threshold.

**Decision rule:** An IPFR page becomes a candidate if either:

- Any single chunk scores ≥ 0.75 (a section of the IPFR page is clearly about the same subject as a section of the change), OR
- 3 or more chunks from the same IPFR page score ≥ 0.45 (the change is broadly related to multiple sections of the page, suggesting topical relevance even without a single strong match).

**Outputs logged:** Run ID, source ID, per-IPFR-page results (max chunk score, count of chunks above low-medium threshold, list of chunk IDs and their scores), candidate pages identified.

### 3.6 Stage 6 — Semantic Matching: Cross-Encoder

**Purpose:** Refine the candidate list from Stage 5 using a more precise (but more expensive) cross-encoder, then integrate lexical and graph-based signals for a final ranking.

**Process:**

1. For each candidate IPFR page from Stage 5, take the top-scoring chunk pairs (change chunk, IPFR chunk) and score them with the **ms-marco-MiniLM-L-6-v2** cross-encoder. The cross-encoder sees both texts simultaneously and produces a more accurate relevance judgment than the bi-encoder's independent encoding.
2. Rerank the candidates by combining three signals:
   - Cross-encoder score (semantic precision)
   - Lexical relevance scores from Stage 4 (keyword and entity-level match)
   - Pre-computed quasi-graph relationships (structural and conceptual connections between IPFR pages)

**Graph propagation:** After direct scoring, propagate alerts through the quasi-graph. If a change triggers a confirmed alert for IPFR Page A, and Page A has a graph edge to Page B with weight *w*:

- The propagated signal for Page B = original score × *w* × decay_per_hop (default: 0.25)
- Propagation continues up to max_hops (default: 3)
- If the decayed signal falls below propagation_threshold (default: 0.05), propagation stops on that path

With decay_per_hop = 0.25, the effective signal is:

- After 1 hop: 25% of the original (meaningful, will propagate if original signal was strong)
- After 2 hops: 6.25% of the original (marginal, only survives for very strong originals)
- After 3 hops: 1.5625% of the original (below the 0.05 floor, suppressed in practice)

This configuration makes multi-hop propagation technically available but practically limited to single-hop unless the original signal is exceptionally strong. This is the intended behaviour at launch; the decay rate and hop limit can be adjusted as operational experience accumulates.

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

- **Cached system prompt.** A carefully authored prompt that instructs the model to: act as an IP content accuracy reviewer, produce structured JSON output conforming to a defined schema, explicitly state "no amendment needed" when uncertain rather than speculating, and avoid hallucinating legal references.
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
  "ipfr_page_id": "B1012",
  "amendment_needed": true,
  "confidence": "high",
  "affected_sections": ["Section 3.2: Registering a Trade Mark"],
  "suggested_changes": [
    "Update the processing timeframe from '12 months' to '6 months' to reflect the amended s.44 of the Trade Marks Act 1995."
  ],
  "reasoning": "The Trade Marks Amendment Act 2026 reduced the examination period from 12 to 6 months. This directly affects the guidance in Section 3.2 of B1012, which currently states '12 months'.",
  "sources_considered": [
    {
      "source_id": "FRL-TMA-1995",
      "relevance_score": 0.82,
      "contribution": "Primary trigger — amendment to s.44"
    }
  ]
}
```

Every LLM response is validated against this schema. If validation fails:

- Retry the LLM call once with the same inputs.
- If the second call also fails validation, log the raw output and skip this IPFR page for this run. Record the failure in the health log.

**Outputs logged:** Run ID, IPFR page ID, LLM response (raw and validated), schema validation result, retry count, processing time.

### 3.9 Stage 9 — Notification

**Purpose:** Send one consolidated email per run to the content owner, summarising all amendment suggestions from Stage 8.

**Email delivery:** GitHub Actions with Python `smtplib` and a Gmail app password stored as a repository secret.

**Email structure:**

- Subject line includes the run date and the number of IPFR pages flagged.
- Body contains one section per flagged IPFR page, including:
  - The IPFR page title and ID
  - The amendment suggestion(s) from the LLM
  - The confidence level
  - The source(s) that triggered the alert, with links
  - A summary of the scoring evidence (fused relevance score, bi-encoder max, cross-encoder score)
- A "no alerts" email is not sent. If no pages are flagged, no email is generated (but the run is still logged).

**Feedback mechanism:** At the bottom of each page section in the email, four mailto links are provided:

1. **Useful** — the alert was accurate and the suggestion was helpful.
2. **Not a significant trigger event** — the change was real but not important enough to warrant an alert.
3. **Noteworthy trigger event but incorrect amendment** — the change was important, but the suggested amendment was wrong.
4. **Noteworthy trigger event but content influenced was incorrect** — the change was important, but the wrong IPFR page was flagged.

Each mailto link generates a pre-formatted reply containing the run ID, IPFR page ID, and trigger source(s). All options include space for a free-text comment. Replies are sent to a monitored mailbox.

**Feedback ingestion:** A lightweight GitHub Actions workflow (or a script within the pipeline) parses incoming feedback replies and appends them to the feedback JSONL log, tagged with run ID, IPFR page ID, trigger source, feedback category, and any comment. Over time, this log enables empirical threshold calibration by correlating score profiles with human judgments.

---

## 4. IP First Response Ingestion Pipeline

The IPFR ingestion pipeline creates and maintains the SQLite database that serves as the single source of truth for the content corpus that Tripwire monitors for potential impact. This pipeline runs before the main Tripwire pipeline and must complete before Tripwire begins its daily run.

### 4.1 Ingestion Steps

**Step 1: Sitemap Extraction.** A web scraper reads the IP First Response sitemap and populates the IPFR sitemap CSV with page URLs, titles, IPFR content identifiers (letter + four digits, e.g. B1000), links to local markdown snapshots, a "last modified" date column, and a "last checked" date column.

**Step 2: Page Scraping and Normalisation.** Each page on the IPFR sitemap is scraped and normalised into markdown using **markdownify**. Markdownify is used here (rather than inscriptis, which is used for influencer sources) because the structural markers it preserves — heading hierarchy, list formatting, link references — are valuable for downstream chunking, entity extraction, and human review of stored snapshots.

**Step 3: Change Detection (after initial run).** Every 24 hours, the pipeline checks whether the "Last modification date" for each IPFR page has changed. If yes, the page is re-scraped and continues through the full ingestion process. If no, the page is skipped.

**Step 4: Enrichment and Loading.** Each new or updated page is processed through the enrichment pipeline and loaded into the SQLite database. The enrichment process produces the following precomputed assets:

| Asset | Method | Purpose in Tripwire |
|---|---|---|
| Version hash | SHA-256 of normalised markdown | Fast change detection (hash comparison instead of full content comparison) |
| Document-level embedding | all-MiniLM-L6-v2 | Quasi-graph construction (embedding neighbours) |
| Document chunks | Recursive text splitting respecting heading boundaries | Unit of comparison for bi-encoder and cross-encoder stages |
| Chunk-level embeddings | all-MiniLM-L6-v2 | Bi-encoder cosine similarity in Stage 5 |
| Named entity inventory | spaCy NER extraction | NER overlap scoring in Stage 4 |
| Keyphrase extraction | RAKE | BM25 scoring in Stage 4 |
| TF-IDF vector | scikit-learn TfidfVectorizer fitted on full IPFR corpus | TF-IDF cosine similarity in Stage 4 |
| Section-level metadata | Heading hierarchy and section boundaries | LLM assessment context (which sections are affected) |
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
│   ├── stage2_change_detection.py    # Simhash, Jaccard, significance fingerprint
│   ├── stage3_diff.py                # Diff generation, FRL explainer retrieval, RSS extraction
│   ├── stage4_relevance.py           # TF-IDF, NER overlap, BM25, source importance, fusion
│   ├── stage5_biencoder.py           # Bi-encoder chunking and cosine similarity
│   ├── stage6_crossencoder.py        # Cross-encoder scoring, reranking, graph propagation
│   ├── stage7_aggregation.py         # Trigger grouping per IPFR page
│   ├── stage8_llm.py                 # LLM call, system prompt, JSON schema validation
│   ├── stage9_notification.py        # Email composition and sending
│   │
│   ├── scraper.py                    # Web scraping with inscriptis normalisation
│   ├── validation.py                 # Content validation after scraping
│   ├── health.py                     # Run health check and alert generation
│   └── observability.py              # Score distribution summary and reporting
│
├── ingestion/
│   ├── ingest.py                     # IPFR ingestion pipeline orchestration
│   ├── sitemap.py                    # Sitemap CSV construction
│   ├── scrape_ipfr.py                # IPFR page scraping with markdownify normalisation
│   ├── enrich.py                     # Embedding, NER, RAKE, TF-IDF, chunking
│   ├── graph.py                      # Quasi-graph edge computation
│   └── db.py                         # SQLite read/write operations (WAL mode)
│
├── data/
│   ├── logs/
│   │   ├── runs/                     # JSONL log files (one per run, warm storage)
│   │   ├── cold_storage.sqlite       # Archived log entries older than 30 days
│   │   ├── feedback.jsonl            # Content owner feedback log
│   │   └── deferred_triggers.jsonl   # Triggers queued due to LLM failure
│   │
│   ├── influencer_sources/
│   │   ├── source_registry.csv       # Target URLs, headings, IDs, importance, frequency
│   │   └── snapshots/                # Most recent snapshot of each influencer source
│   │
│   └── ipfr_corpus/
│       ├── ipfr.sqlite               # IPFR content, embeddings, NER, vectors, graph edges
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
| spaCy model | Fails to load | Skip NER overlap scoring; re-normalise remaining 3 signal weights to sum to 1.0 |
| Bi-encoder model | Fails to load | Skip semantic stages; send alert based on lexical scores alone with a reduced-confidence flag |
| LLM API | Timeout / rate limit | Retry 3×; if still failing, store triggers in deferred_triggers.jsonl for next run |
| Email sending (SMTP) | Connection failure | Retry 3×; if still failing, write email content to local file and send health alert |
| SQLite database | Locked / corrupted | Abort run, send immediate health alert |

### 6.5 Deferred Triggers

When the LLM API is unavailable, triggers that have passed all prior stages are written to `data/logs/deferred_triggers.jsonl`. At the start of the next run, before processing new sources, the pipeline checks for deferred triggers and processes them through Stages 8–9 first. Deferred triggers are timestamped; triggers older than 7 days are discarded (the next full run will regenerate them if the change is still relevant).

### 6.6 Health Alerting

After every run, the pipeline computes summary health statistics and evaluates alert conditions:

- **Error rate > 30%** in a single run → send health alert email.
- **Same source fails 3 consecutive runs** → send health alert email identifying the source.
- **Pipeline fails to complete within timeout** (default: 30 minutes via GitHub Actions `timeout-minutes`) → GitHub Actions failure notification.
- **LLM produces malformed output ≥ 2 times in a run** → send health alert email.

Health alerts are sent to a separate email address (the system operator) and are distinct from content-owner notification emails.

### 6.7 Observability

The JSONL log is the primary observability layer. A weekly summary script reads the last 30 days of logs and produces a report containing:

- **Reliability table:** For each source — total runs, successful runs, error count, last error date, current consecutive-success or consecutive-failure streak.
- **Score distributions:** For each scoring stage — min, 25th percentile, median, 75th percentile, max across all runs. Used to assess whether thresholds are in reasonable ranges.
- **Alert volume:** Number of alerts generated per week, trending over time.
- **Feedback summary:** Of alerts with feedback received, the proportion rated "useful" vs each non-useful category. This is the system's precision metric.

---

## 7. Configuration

All tuneable weights, thresholds, model identifiers, and behavioural parameters are exposed in a single YAML file at the repository root: `tripwire_config.yaml`. This file is version-controlled in Git so that every parameter change is tracked as a commit.

The configuration file is loaded and validated at the start of every pipeline run. Validation checks include: relevance weights sum to 1.0, all thresholds are within valid ranges, model names are recognised, and required file paths exist. If validation fails, the pipeline exits with a clear error message before processing any sources.

A full snapshot of the active configuration is included in the JSONL log entry for each run, ensuring that historical runs can always be interpreted in the context of the parameters that were in effect at the time.

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
  deferred_trigger_file: "data/logs/deferred_triggers.jsonl"

# --- Stage 2: Change detection ---
change_detection:
  simhash_hamming_distance_min: 3
  jaccard_trigram_change_threshold: 0.05
  significance_fingerprint: true

# --- Stage 4: Relevance scoring ---
relevance_scoring:
  weights:
    tfidf_cosine: 0.30
    ner_overlap: 0.25
    bm25: 0.30
    source_importance: 0.15
  threshold: 0.35
  diff_vs_full_weight:
    diff: 0.70
    full_page: 0.30
  fast_pass:
    ner_overlap_min: 0.80
    source_importance_min: 1.0

# --- Stages 5–6: Semantic scoring ---
semantic_scoring:
  biencoder:
    model: "all-MiniLM-L6-v2"
    high_threshold: 0.75
    low_medium_threshold: 0.45
    low_medium_min_chunks: 3
  crossencoder:
    model: "ms-marco-MiniLM-L-6-v2"
    threshold: 0.60

# --- Stage 6: Graph propagation ---
graph:
  enabled: true
  max_hops: 3
  decay_per_hop: 0.25
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
  warm_log_retention_days: 30
  content_versions_retained: 6
  sqlite_wal_mode: true

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
  influencer_tool: "inscriptis"
  ipfr_ingestion_tool: "markdownify"
```

---

## 8. JSONL Log Schema

Every run produces one JSONL file containing one entry per source processed. Each entry is a self-contained JSON object that captures the full lifecycle of that source through the pipeline for that run.

```json
{
  "run_id": "2026-04-05-001",
  "config_snapshot": { "...": "full config at time of run" },
  "source_id": "FRL-TMA-1995",
  "source_url": "https://www.legislation.gov.au/...",
  "source_type": "frl",
  "timestamp": "2026-04-05T02:14:33Z",
  "stage_reached": "llm_assessment",
  "outcome": "completed",
  "error": null,
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
      "scores": {
        "tfidf_cosine": 0.52,
        "ner_overlap": 0.41,
        "bm25_normalised": 0.63,
        "source_importance": 0.70
      },
      "weights": { "tfidf": 0.30, "ner": 0.25, "bm25": 0.30, "importance": 0.15 },
      "fused_score": 0.514,
      "threshold": 0.35,
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
      "amendment_needed": true,
      "confidence": "high",
      "schema_valid": true,
      "retries": 0
    }
  },
  "triggered_pages": ["B1012", "C2003"],
  "duration_seconds": 8.7
}
```

Entries where the source did not change are much smaller:

```json
{
  "run_id": "2026-04-05-001",
  "source_id": "WIPO-RSS-NEWS",
  "source_url": "https://www.wipo.int/...",
  "source_type": "rss",
  "timestamp": "2026-04-05T02:14:01Z",
  "stage_reached": "metadata_probe",
  "outcome": "no_change",
  "error": null,
  "stages": {
    "metadata_probe": {
      "changed": false,
      "signals": { "newest_item_date": "2026-04-03T00:00:00Z", "last_checked": "2026-04-04T00:00:00Z" }
    }
  },
  "triggered_pages": [],
  "duration_seconds": 0.4
}
```

Error entries:

```json
{
  "run_id": "2026-04-05-001",
  "source_id": "IPA-WEBSITE-TM",
  "source_url": "https://www.ipaustralia.gov.au/...",
  "source_type": "webpage",
  "timestamp": "2026-04-05T02:15:12Z",
  "stage_reached": "scrape",
  "outcome": "error",
  "error": {
    "type": "RetryableError",
    "message": "HTTP 503 after 3 retries",
    "http_status": 503,
    "retries_attempted": 3
  },
  "stages": {},
  "triggered_pages": [],
  "duration_seconds": 14.2
}
```

JSONL is used rather than a single structured file because it accommodates evolving schemas as pipeline stages are added or removed — each entry is self-contained and does not depend on a global schema definition.

---

## 9. SQLite Schema — IPFR Corpus

The IPFR SQLite database uses WAL (Write-Ahead Logging) mode to allow concurrent reads during writes. The ingestion pipeline writes to this database; the Tripwire pipeline reads from it.

```sql
-- Pages table: one row per IPFR page
CREATE TABLE pages (
    page_id         TEXT PRIMARY KEY,     -- e.g. "B1012"
    url             TEXT NOT NULL,
    title           TEXT NOT NULL,
    markdown_content TEXT NOT NULL,
    version_hash    TEXT NOT NULL,         -- SHA-256 of normalised markdown
    last_modified   TEXT,                  -- ISO 8601 date from IPFR sitemap
    last_checked    TEXT,                  -- ISO 8601 date of last ingestion check
    last_ingested   TEXT,                  -- ISO 8601 date of last full ingestion
    doc_embedding   BLOB                  -- document-level embedding (all-MiniLM-L6-v2)
);

-- Chunks table: one row per chunk of each page
CREATE TABLE chunks (
    chunk_id        TEXT PRIMARY KEY,     -- e.g. "B1012-chunk-003"
    page_id         TEXT NOT NULL REFERENCES pages(page_id),
    chunk_text      TEXT NOT NULL,
    chunk_index     INTEGER NOT NULL,     -- positional index within the page
    section_heading TEXT,                  -- nearest heading above this chunk
    chunk_embedding BLOB NOT NULL         -- chunk-level embedding (all-MiniLM-L6-v2)
);

-- Entities table: named entities extracted per page
CREATE TABLE entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id         TEXT NOT NULL REFERENCES pages(page_id),
    entity_text     TEXT NOT NULL,
    entity_type     TEXT NOT NULL,         -- e.g. "LEGISLATION", "ORG", "SECTION", "DATE"
    UNIQUE(page_id, entity_text, entity_type)
);

-- Keyphrases table: RAKE-extracted keyphrases per page
CREATE TABLE keyphrases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id         TEXT NOT NULL REFERENCES pages(page_id),
    keyphrase       TEXT NOT NULL,
    score           REAL NOT NULL          -- RAKE score
);

-- TF-IDF vectors: stored as serialised arrays per page
CREATE TABLE tfidf_vectors (
    page_id         TEXT PRIMARY KEY REFERENCES pages(page_id),
    vector          BLOB NOT NULL,         -- serialised sparse vector
    vocabulary_hash TEXT NOT NULL           -- hash of the vocabulary used; invalidated on re-fit
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
    char_start      INTEGER NOT NULL,      -- character offset in markdown_content
    char_end        INTEGER NOT NULL
);
```

---

## 10. Phased Implementation Plan

### Phase 1 — Foundation (Weeks 1–4)

| # | Task | Depends On |
|---|---|---|
| 1.1 | Create repository structure and `tripwire_config.yaml` | — |
| 1.2 | Implement `config.py` (load, validate, snapshot) | 1.1 |
| 1.3 | Implement `errors.py` and `retry.py` (error classes, exponential backoff) | — |
| 1.4 | Define and create the IPFR SQLite schema (`db.py`) | — |
| 1.5 | Build IPFR ingestion pipeline: sitemap → scrape → normalise to markdown | 1.4 |
| 1.6 | Build IPFR enrichment: chunking, embeddings, NER, RAKE, TF-IDF, section metadata | 1.5 |
| 1.7 | Build quasi-graph edge computation (embedding neighbours + entity overlap) | 1.6 |
| 1.8 | Implement JSONL logging framework with run-level schema | 1.2 |
| 1.9 | Build the influencer source registry CSV and metadata probe (Stage 1) | 1.2 |
| 1.10 | Set up GitHub Actions workflow for IPFR ingestion (daily) | 1.5–1.7 |

### Phase 2 — Change Detection (Weeks 5–8)

| # | Task | Depends On |
|---|---|---|
| 2.1 | Implement web scraping with inscriptis normalisation and content validation | 1.3 |
| 2.2 | Implement Stage 2: simhash, Jaccard, significance fingerprint | 2.1 |
| 2.3 | Implement Stage 3: diff generation, FRL explainer retrieval, RSS extraction | 2.2 |
| 2.4 | Implement snapshot storage and 6-version retention | 2.1 |
| 2.5 | Create 10–15 manually altered snapshots for threshold testing | 2.2 |
| 2.6 | Run in observation mode; collect score distributions; refine Stage 2 thresholds | 2.2–2.4 |

### Phase 3 — Relevance and Semantic Scoring (Weeks 9–12)

| # | Task | Depends On |
|---|---|---|
| 3.1 | Implement Stage 4: TF-IDF, NER overlap, BM25, source importance, signal fusion | 1.6, 2.3 |
| 3.2 | Implement fast-pass override logic | 3.1 |
| 3.3 | Implement BM25 score normalisation (95th percentile from observation data) | 3.1 |
| 3.4 | Implement Stage 5: bi-encoder chunking and cosine similarity | 1.6 |
| 3.5 | Implement Stage 6: cross-encoder scoring, reranking, graph propagation | 1.7, 3.4 |
| 3.6 | Continue observation mode; extend to Stages 4–6; refine all thresholds | 3.1–3.5 |

### Phase 4 — LLM Assessment and Notification (Weeks 13–16)

| # | Task | Depends On |
|---|---|---|
| 4.1 | Define LLM output JSON schema and validation logic | — |
| 4.2 | Author the LLM system prompt | — |
| 4.3 | Implement Stage 7: trigger aggregation per IPFR page | 3.5 |
| 4.4 | Implement Stage 8: LLM call with schema validation and retry | 4.1–4.3 |
| 4.5 | Implement deferred trigger mechanism for LLM failures | 4.4 |
| 4.6 | Implement Stage 9: consolidated email with feedback mailto links | 4.4 |
| 4.7 | Implement feedback ingestion (parse replies → feedback.jsonl) | 4.6 |
| 4.8 | Set up main Tripwire GitHub Actions workflow (daily, after ingestion) | All |
| 4.9 | Disable observation mode; begin live operation | All |

### Phase 5 — Hardening and Iteration (Ongoing)

| # | Task | Depends On |
|---|---|---|
| 5.1 | Implement health alerting (error rate, consecutive failures, timeout) | 4.8 |
| 5.2 | Implement weekly observability summary report | 1.8 |
| 5.3 | Implement warm-to-cold JSONL → SQLite migration (30-day rolling) | 1.8 |
| 5.4 | Calibrate thresholds using accumulated feedback data | 4.7, 4.9 |
| 5.5 | Evaluate alternative relevance weights via grid search against feedback log | 5.4 |
| 5.6 | Enable internal-link graph edges when link extraction is ready | 1.7 |
| 5.7 | Evaluate positional/proximity BM25 extensions if standard BM25 proves insufficient | 3.1 |
| 5.8 | Write operational runbooks: failure response, adding sources, adjusting thresholds | All |
