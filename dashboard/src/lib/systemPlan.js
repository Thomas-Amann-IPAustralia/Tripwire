// ── Inline node helpers ───────────────────────────────────────────────────────
const t = (v) => ({ type: 'text', v });
const c = (v) => ({ type: 'code', v, isConfigParam: false });
const cfg = (v, k) => ({ type: 'code', v, isConfigParam: true, configKey: k });
const b = (v) => ({ type: 'strong', v });

// paragraph: string → { type:'p', text } | InlineNode[] → { type:'p', nodes }
const p = (nodes, anchor = null) =>
  typeof nodes === 'string'
    ? { type: 'p', text: nodes, anchor }
    : { type: 'p', nodes, anchor };

const blk  = (lang, text, anchor = null) => ({ type: 'codeblock', language: lang, code: text, anchor });
const tbl  = (headers, rows, anchor = null) => ({ type: 'table', headers, rows, anchor });
const ul   = (items, anchor = null) => ({ type: 'list', ordered: false, items, anchor });
const ol   = (items, anchor = null) => ({ type: 'list', ordered: true, items, anchor });

// ─────────────────────────────────────────────────────────────────────────────

export const systemPlan = [

  // ── §1 Purpose and Scope ─────────────────────────────────────────────────
  {
    id: 'purpose',
    heading: '1. Purpose and Scope',
    level: 1,
    stageRef: null,
    anchor: null,
    content: [
      p('Tripwire is an autonomous monitoring system that tracks substantive changes in authoritative Intellectual Property (IP) sources — such as Australian legislation hosted on the Federal Register of Legislation (FRL), WIPO feeds, and government agency webpages — to detect updates that may require amendments to content published on the IP First Response (IPFR) website.'),
      p('The system answers a chain of five questions, each more expensive to compute than the last:'),
      ol([
        'Did the target information change?',
        'Was the change meaningful?',
        'What exactly is different?',
        'Is the change potentially relevant to IPFR content?',
        'Which IPFR pages are most likely affected, and what should be done about it?',
      ]),
      p('Each question acts as a gate. Only changes that pass one gate proceed to the next. This filter-funnel architecture ensures that expensive operations (semantic scoring, LLM calls) are reserved for the small fraction of changes that survive cheaper upstream checks.'),
      p('The system is modular. A user can fork the repository, replace the "influencer" sources and the "influenced" corpus, adjust the configuration file, and have a working change-monitoring pipeline for a different domain.'),
    ],
  },

  // ── §2 Architecture Overview ─────────────────────────────────────────────
  {
    id: 'architecture',
    heading: '2. Architecture Overview',
    level: 1,
    stageRef: null,
    anchor: null,
    content: [],
  },
  {
    id: 'pipeline-stages',
    heading: '2.1 Pipeline Stages',
    level: 2,
    stageRef: null,
    anchor: null,
    content: [
      p('The pipeline executes as a scheduled GitHub Actions workflow. Each run processes every influencer source that is due for checking (based on its configured frequency), passes changes through a sequence of gates, and produces a consolidated email report to the content owner.'),
      { type: 'pipeline-diagram' },
    ],
  },
  {
    id: 'source-type-routing',
    heading: '2.2 Source-Type Routing',
    level: 2,
    stageRef: null,
    anchor: null,
    content: [
      p('Three categories of influencer source pass through the pipeline differently:'),
      tbl(
        ['Source Type', 'Stage 2 — Change Significance', 'Stage 3 — Diff Generation', 'Stage 4 — Relevance Scoring Input'],
        [
          ['Webpage',
            'Scrape and normalise with trafilatura, then apply three-pass change detection (SHA-256 hash, word-level diff, significance tagger)',
            'Produce .diff file from old vs new snapshot',
            'Normalised diff scored via BM25 and bi-encoder similarity'],
          ['Federal Register of Legislation',
            'Skipped — FRL publishes structured change information',
            'Retrieve the change explainer document',
            'Change explainer scored via BM25 and bi-encoder similarity'],
          ['RSS Feed',
            'Skipped — RSS items are inherently new content',
            'Extract new items since last check',
            'New items scored via BM25 and bi-encoder similarity'],
        ]
      ),
    ],
  },
  {
    id: 'observation-mode',
    heading: '2.3 Observation Mode',
    level: 2,
    stageRef: null,
    anchor: 'doc-observation-mode',
    content: [
      p([
        t('On initial deployment, the pipeline runs in '),
        b('observation mode'),
        t('. All stages execute and all scores are logged, but no alerts are triggered and no emails are sent. This mode serves two purposes: it allows threshold calibration based on real score distributions, and it validates that each stage is producing sensible outputs before the system begins generating notifications.'),
      ]),
      p([
        t('Observation mode is controlled by a single boolean — '),
        cfg('pipeline.observation_mode', 'pipeline.observation_mode'),
        t(' — in the configuration file. When active, the pipeline runs end-to-end, records everything to the '),
        c('pipeline_runs'),
        t(' table, and exits after Stage 6 (skipping LLM calls and email notifications to save cost). A summary report of score distributions is generated instead, to support manual threshold review.'),
      ]),
      p('During the initial calibration period (4–8 weeks recommended), the operator should also manually alter markdown snapshots of 10–15 influencer sources to test the sensitivity of each gate. This provides controlled ground-truth data for setting thresholds before real-world changes are available.'),
    ],
  },

  // ── §3 Stage Specifications ──────────────────────────────────────────────
  {
    id: 'stage-specifications',
    heading: '3. Stage Specifications',
    level: 1,
    stageRef: null,
    anchor: null,
    content: [],
  },

  // Stage 1
  {
    id: 'stage-1',
    heading: '3.1 Stage 1 — Metadata Probe',
    level: 2,
    stageRef: 1,
    anchor: null,
    content: [
      p([b('Purpose:'), t(' Determine whether a source has changed at all since the last check, using the cheapest possible signals. Sources that have not changed are immediately skipped.')]),
      p([
        b('Frequency:'), t(' Each source has a configured check frequency (daily, weekly, fortnightly, monthly, or quarterly) defined in the influencer source registry CSV. The pipeline runs every 24 hours (after the IPFR corpus ingestion run) but only probes each source when its scheduled check is due.'),
      ], 'doc-run-frequency'),
      p('Probe signals (checked for any available, source-dependent):'),
      ul([
        'HTTP `ETag` or `Last-Modified` header comparison against stored values',
        'Content-Length header comparison',
        'Version identifier (FRL sources: `registerId` of the latest compiled version, via `api.prod.legislation.gov.au`)',
        'RSS feed: presence of items with publication dates newer than the last-checked timestamp',
      ]),
      p([b('Decision rule:'), t(' If any probe signal indicates a change, proceed to Stage 2. If no signals are available (e.g. the server does not return useful headers), always proceed to Stage 2 — the cost of an unnecessary scrape is low.')]),
      p([b('Outputs logged:'), t(' Run ID, source ID, source URL, timestamp, probe signals collected, decision (changed / unchanged / unknown).')]),
    ],
  },

  // Stage 2
  {
    id: 'stage-2',
    heading: '3.2 Stage 2 — Change Detection',
    level: 2,
    stageRef: 2,
    anchor: null,
    content: [
      p([b('Purpose:'), t(' Determine whether a detected change is meaningful or merely cosmetic (e.g. a timestamp update, CSS class rename, whitespace change). This stage is only applied to '), b('webpages'), t('. FRL sources and RSS feeds bypass this stage because their change information is already structured.')]),
      p([b('Prerequisite:'), t(' For webpages, the new page is scraped and normalised into plaintext using '), b('trafilatura'), t(' before any comparison. The previous snapshot is loaded from storage.')]),
      p([b('Pass 1 — SHA-256 Content Hash.'), t(' Compute the SHA-256 hash of the normalised plain text and compare against the stored hash. If the hashes match, the content has not changed — skip immediately. This is the fast-path exact check.')]),
      p([b('Pass 2 — Word-Level Diff.'), t(' Generate a word-level diff (using Python `difflib.unified_diff` on the normalised text) to identify specifically what changed. If the diff is empty after normalisation (whitespace-only change), log as "cosmetic change" and stop processing.')]),
      p([
        b('Pass 3 — Significance Fingerprint (Tagger).'), t(' Using spaCy and regex, extract from the changed lines: defined terms, numerical values (dollar amounts, time periods, percentages, section numbers), dates, cross-references, and modal verbs in legal context. This step is enabled via '),
        cfg('change_detection.significance_fingerprint', 'change_detection.significance_fingerprint'),
        t('. Tag the change as '),
        c('significance: high'),
        t(' (fingerprint matched) or '),
        c('significance: standard'),
        t(' (real content changed but no fingerprint match). Both tags proceed to Stage 3.'),
      ], 'doc-significance-fingerprint'),
      p([b('Decision rule:'), t(' If the SHA-256 hash differs (Pass 1) and the diff is non-empty after normalisation (Pass 2), the change proceeds to Stage 3 regardless of the significance tag. The only things stopped at Stage 2 are identical content (hash match) and whitespace-only diffs.')]),
    ],
  },

  // Stage 3
  {
    id: 'stage-3',
    heading: '3.3 Stage 3 — Diff Generation',
    level: 2,
    stageRef: 3,
    anchor: null,
    content: [
      p([b('Purpose:'), t(' Produce a precise representation of what changed, formatted appropriately for the source type.')]),
      p([b('Webpage sources:'), t(' Generate a unified .diff file comparing the previous normalised snapshot against the new normalised snapshot. Store the diff file in the run\'s working directory. Update the stored snapshot to the new version. Retain the previous 6 versions of the snapshot for audit purposes.')]),
      p([b('FRL sources:'), t(' Retrieve the Explanatory Statement (ES) Word document for the latest compiled version using the official FRL REST API. A metadata check is performed first to confirm the document exists. If `type=\'ES\'` returns HTTP 404, `type=\'SupplementaryES\'` is tried as a fallback. The binary DOCX is extracted to plain text via mammoth → trafilatura.')]),
      blk('text',
`GET https://api.prod.legislation.gov.au/v1/documents/find(titleid='{titleId}',
    asatspecification='Latest',type='ES',format='Word',
    uniqueTypeNumber=0,volumeNumber=0,rectificationVersionNumber=0)`),
      p([b('RSS sources:'), t(' Persist feed state as a keyed JSON structure mapping each item\'s `guid` (or `link` as fallback) to the full item payload. On each run, fetch the current feed, compare keys against the stored snapshot to find new GUIDs, and compare payloads for existing GUIDs to detect mutations.')]),
      p([b('Diff normalisation (all source types):'), t(' The raw diff or extracted content is normalised into canonical plain-text before Stage 4: HTML entities decoded, whitespace collapsed, Unicode normalised to NFC. Normalisation does '), b('not'), t(' lowercase text (NER and YAKE depend on case) and does '), b('not'), t(' strip punctuation (YAKE uses it for sentence boundary detection).')]),
    ],
  },

  // Stage 4
  {
    id: 'stage-4',
    heading: '3.4 Stage 4 — Relevance Scoring',
    level: 2,
    stageRef: 4,
    anchor: null,
    content: [
      p([b('Purpose:'), t(' Determine whether the detected change is potentially relevant to the IPFR corpus, and identify which IPFR pages are the strongest candidates for impact, before attempting expensive semantic matching.')]),
      p([
        b('Signal 1 — BM25 (keyword relevance).'), t(' Run YAKE keyword extraction on the normalised diff at a rate of '),
        cfg('relevance_scoring.yake.keyphrases_per_80_words', 'relevance_scoring.yake.keyphrases_per_80_words'),
        t(' keyphrase per 80 words, with a minimum of '),
        cfg('relevance_scoring.yake.min_keyphrases', 'relevance_scoring.yake.min_keyphrases'),
        t(' and maximum of '),
        cfg('relevance_scoring.yake.max_keyphrases', 'relevance_scoring.yake.max_keyphrases'),
        t('. For diffs shorter than '),
        cfg('relevance_scoring.yake.short_diff_word_threshold', 'relevance_scoring.yake.short_diff_word_threshold'),
        t(' words, supplement YAKE output with NER entities from Stage 2. Use the keyphrases as query terms against a BM25 index built from the IPFR corpus.'),
      ], 'doc-yake'),
      p([
        b('Signal 2 — Bi-encoder Semantic Similarity.'), t(' Encode the normalised diff using the bi-encoder ('),
        cfg('semantic_scoring.biencoder.model', 'semantic_scoring.biencoder.model'),
        t('), compute cosine similarity against each IPFR page\'s precomputed document-level embedding. This captures semantic relevance even when terminology differs.'),
      ]),
      p([
        b('Fusion via weighted RRF.'), t(' For each change event, rank all IPFR pages independently by each signal, then compute RRF score using '),
        cfg('relevance_scoring.rrf_k', 'relevance_scoring.rrf_k'),
        t(' (k = 60), '),
        cfg('relevance_scoring.rrf_weight_bm25', 'relevance_scoring.rrf_weight_bm25'),
        t(' (w_bm25 = 1.0), and '),
        cfg('relevance_scoring.rrf_weight_semantic', 'relevance_scoring.rrf_weight_semantic'),
        t(' (w_semantic = 2.0). The higher semantic weight reflects that semantic similarity is the more important signal.'),
      ], 'doc-rrf-weights'),
      blk('text',
`RRF_score(page) = w_bm25 / (k + rank_bm25) + w_semantic / (k + rank_semantic)`, 'doc-rrf-k'),
      p([
        b('Source importance multiplier.'), t(' Applied after fusion: '),
        c('final_score = RRF_score × (0.5 + 0.5 × source_importance)'),
        t('. The '),
        cfg('relevance_scoring.source_importance_floor', 'relevance_scoring.source_importance_floor'),
        t(' (0.5) ensures low-importance sources can still trigger alerts on strong content matches.'),
      ], 'doc-importance-floor'),
      p([
        b('Candidate selection.'), t(' Take the top '),
        cfg('relevance_scoring.top_n_candidates', 'relevance_scoring.top_n_candidates'),
        t(' IPFR pages by final score (default N = 5), plus any additional page whose final score exceeds '),
        cfg('relevance_scoring.min_score_threshold', 'relevance_scoring.min_score_threshold'),
        t(' (set during the observation period), plus any page where a fast-pass condition is met. This ensures a routine day surfaces ~5 candidates, but a major legislative change that genuinely affects 20 pages lets all 20 through.'),
      ], 'doc-top-n'),
      p([
        b('Fast-pass overrides.'), t(' Sources with importance ≥ '),
        cfg('relevance_scoring.fast_pass.source_importance_min', 'relevance_scoring.fast_pass.source_importance_min'),
        t(' bypass the candidate selection threshold entirely and proceed directly to Stage 5.'),
      ], 'doc-fast-pass'),
    ],
  },

  // Stage 5
  {
    id: 'stage-5',
    heading: '3.5 Stage 5 — Semantic Matching: Bi-Encoder',
    level: 2,
    stageRef: 5,
    anchor: 'doc-biencoder',
    content: [
      p([b('Purpose:'), t(' Identify which specific IPFR pages are most likely affected by the change. This is a coarse-grained semantic pass using a bi-encoder to efficiently compare against all IPFR content chunks.')]),
      p('Process:'),
      ol([
        'Chunk the incoming change document using the same chunking strategy applied during IPFR ingestion, so that chunk sizes are comparable.',
        'Encode each chunk using the BAAI/bge-base-en-v1.5 bi-encoder model.',
        'Compute cosine similarity between each change-document chunk and every precomputed IPFR content chunk embedding stored in SQLite.',
        'For each IPFR page, record the highest single-chunk cosine score and the count of chunks exceeding the low-medium threshold.',
      ]),
      p([
        b('Decision rule.'), t(' An IPFR page becomes a candidate if either: any single chunk scores ≥ '),
        cfg('semantic_scoring.biencoder.high_threshold', 'semantic_scoring.biencoder.high_threshold'),
        t(' (a section of the IPFR page clearly discusses the same subject), or ≥ '),
        cfg('semantic_scoring.biencoder.low_medium_min_chunks', 'semantic_scoring.biencoder.low_medium_min_chunks'),
        t(' chunks from the same IPFR page score ≥ '),
        cfg('semantic_scoring.biencoder.low_medium_threshold', 'semantic_scoring.biencoder.low_medium_threshold'),
        t(' (the change is broadly related to multiple sections of the page).'),
      ], 'doc-biencoder-thresholds'),
      p([b('Lazy loading.'), t(' The bi-encoder model (~400 MB) is not loaded until the first source reaches Stage 5, saving ~25 s and ~400 MB RAM on runs where no source reaches this stage. It is released before the cross-encoder is loaded.')]),
    ],
  },

  // Stage 6
  {
    id: 'stage-6',
    heading: '3.6 Stage 6 — Semantic Matching: Cross-Encoder',
    level: 2,
    stageRef: 6,
    anchor: 'doc-crossencoder',
    content: [
      p([b('Purpose:'), t(' Refine the candidate list from Stage 5 using a more precise (but more expensive) cross-encoder, then integrate lexical and graph-based signals for a final ranking.')]),
      p([
        b('Cross-encoder scoring.'), t(' For each candidate IPFR page, score the full IPFR page content against the full normalised change document using the '),
        cfg('semantic_scoring.crossencoder.model', 'semantic_scoring.crossencoder.model'),
        t(' cross-encoder (~150M parameters, '),
        cfg('semantic_scoring.crossencoder.max_context_tokens', 'semantic_scoring.crossencoder.max_context_tokens'),
        t('-token context window). The cross-encoder sees both complete texts simultaneously and produces a more accurate relevance judgment than chunk-level comparison. Before every call, input tokens are counted; if the combined total exceeds the context window, a truncation warning is logged.'),
      ], 'doc-crossencoder-context'),
      p('Reranking combines three signals:'),
      ul([
        'Cross-encoder score (semantic precision)',
        'Lexical relevance scores from Stage 4 (keyword and entity-level match)',
        'Pre-computed quasi-graph relationships (structural and conceptual connections between IPFR pages)',
      ]),
      p([
        b('Graph propagation.'), t(' After direct scoring, propagate alerts through the quasi-graph. If a change triggers a confirmed alert for IPFR Page A, and Page A has a graph edge to Page B with weight w: propagated signal = original score × w × '),
        cfg('graph.decay_per_hop', 'graph.decay_per_hop'),
        t(' / out_degree(source_node). Propagation continues up to '),
        cfg('graph.max_hops', 'graph.max_hops'),
        t(' hops; stops when the decayed signal falls below '),
        cfg('graph.propagation_threshold', 'graph.propagation_threshold'),
        t('. Graph-neighbour signals only '),
        b('boost'),
        t(' scores; they never reduce them.'),
      ], 'doc-graph'),
      p([
        t('With '),
        cfg('graph.decay_per_hop', 'graph.decay_per_hop'),
        t(' = 0.45, the effective signal at 1 hop is 45%, at 2 hops is ~20%, and at 3 hops is ~9% — above the 0.05 floor for strong original signals, matching the intended behaviour for legislative dependency chains.'),
      ], 'doc-graph-hops'),
      p([
        b('Decision rule.'), t(' IPFR pages whose final reranked score (including any graph-propagated signal) exceeds '),
        cfg('semantic_scoring.crossencoder.threshold', 'semantic_scoring.crossencoder.threshold'),
        t(' proceed to trigger aggregation. Pages below this threshold are logged but not actioned.'),
      ], 'doc-crossencoder-threshold'),
    ],
  },

  // Stage 7
  {
    id: 'stage-7',
    heading: '3.7 Stage 7 — Trigger Aggregation',
    level: 2,
    stageRef: 7,
    anchor: null,
    content: [
      p([b('Purpose:'), t(' Before making LLM calls, group all triggers that exceeded thresholds for the same IPFR page within the current run window. This prevents the content owner receiving multiple separate notifications about the same page, and allows the LLM to reason about the combined effect of several upstream changes on a single IPFR page.')]),
      p('Process:'),
      ol([
        'Collect all (source, IPFR page) pairs that survived Stage 6 in this run.',
        'Group by IPFR page ID.',
        'For each IPFR page, assemble a trigger bundle containing: all relevant diffs, corresponding source metadata, and all scores from Stages 4–6.',
        'Pass each trigger bundle to Stage 8 as a single unit.',
      ]),
    ],
  },

  // Stage 8
  {
    id: 'stage-8',
    heading: '3.8 Stage 8 — LLM Assessment',
    level: 2,
    stageRef: 8,
    anchor: null,
    content: [
      p([b('Purpose:'), t(' For each IPFR page with grouped triggers, make a single LLM call to determine whether the page should be amended, and if so, produce specific, actionable suggestions.')]),
      p([
        b('LLM call configuration.'), t(' Model: '),
        cfg('pipeline.llm_model', 'pipeline.llm_model'),
        t(' (default: gpt-4o). Temperature: '),
        cfg('pipeline.llm_temperature', 'pipeline.llm_temperature'),
        t(' (default: 0.2, to reduce output variance). Maximum tokens: sufficient for the defined JSON schema (default: 1000).'),
      ], 'doc-llm-model'),
      p([b('LLM inputs per call:')]),
      ul([
        'Cached system prompt (instructs model to act as IP content accuracy reviewer, produce structured JSON, treat UNCERTAIN as expected output)',
        'All relevant diffs for this IPFR page (webpage diff, FRL explainer, or RSS extract)',
        'Full IPFR page content (loaded from SQLite)',
        'Bi-encoder cosine scores per chunk pair',
        'Relevance scores (lexical, semantic, reranked) for each trigger',
      ]),
      p([b('Output JSON schema (validated before processing):')]),
      blk('json',
`{
  "verdict": "CHANGE_REQUIRED",
  "confidence": 0.85,
  "reasoning": "The Trade Marks Amendment Act 2026 reduced the examination period...",
  "suggested_changes": [
    "Update the processing timeframe from '12 months' to '6 months'..."
  ]
}`),
      p([
        t('The '),
        c('verdict'),
        t(' field must be one of '),
        c('CHANGE_REQUIRED'),
        t(', '),
        c('NO_CHANGE'),
        t(', or '),
        c('UNCERTAIN'),
        t('. The '),
        c('confidence'),
        t(' field is a float on [0.0, 1.0]. '),
        c('suggested_changes'),
        t(' is populated only for '),
        c('CHANGE_REQUIRED'),
        t(' verdicts. Every response is validated against this schema; if validation fails, the call is retried once before the source is skipped for this run.'),
      ], 'doc-llm-temperature'),
    ],
  },

  // Stage 9
  {
    id: 'stage-9',
    heading: '3.9 Stage 9 — Notification',
    level: 2,
    stageRef: 9,
    anchor: 'doc-notifications',
    content: [
      p([b('Purpose:'), t(' Send one consolidated email per run to the content owner, summarising all amendment suggestions from Stage 8.')]),
      p([b('Email delivery:'), t(' GitHub Actions with Python `smtplib` and a Gmail app password stored as a repository secret.')]),
      p('Email structure:'),
      ul([
        'Subject line includes the run date and the number of IPFR pages flagged.',
        'One section per CHANGE_REQUIRED IPFR page: identifier, title, triggering source(s), normalised diff text, LLM reasoning, full suggested_changes text, and scoring evidence.',
        'A separate "Items requiring human review" section lists all UNCERTAIN verdicts with reasoning and scoring evidence.',
        'A separate "Candidates rejected at deep analysis" section lists items that passed upstream gates but were rejected at the cross-encoder or LLM stage — for calibration purposes.',
        'No "no alerts" email is sent. If no pages are flagged, the run is logged but no email is generated.',
      ]),
      p([b('Feedback mechanism.'), t(' At the bottom of each page section, four mailto links are provided:')]),
      ol([
        'Useful — the alert was accurate and the suggestion was helpful.',
        'Not a significant trigger event — the change was real but not important enough to warrant an alert.',
        'Noteworthy trigger event but incorrect amendment — the change was important but the suggested amendment was wrong.',
        'Noteworthy trigger event but content influenced was incorrect — the change was important but the wrong IPFR page was flagged.',
      ]),
      p([
        b('Feedback ingestion.'), t(' Replies are collected via a dedicated Gmail account and processed by a scheduled workflow ('),
        c('feedback_ingestion.yml'),
        t('). Parsed entries are appended to '),
        c('data/logs/feedback.jsonl'),
        t(', enabling empirical threshold calibration by correlating score profiles with human judgments.'),
      ]),
    ],
  },

  // ── §4 IPFR Ingestion Pipeline ───────────────────────────────────────────
  {
    id: 'ingestion',
    heading: '4. IPFR Ingestion Pipeline',
    level: 1,
    stageRef: null,
    anchor: null,
    content: [
      p('The IPFR ingestion pipeline creates and maintains the SQLite database that serves as the single source of truth for the content corpus that Tripwire monitors for potential impact. This pipeline runs before the main Tripwire pipeline and must complete before Tripwire begins its daily run.'),
    ],
  },
  {
    id: 'ingestion-steps',
    heading: '4.1 Ingestion Steps',
    level: 2,
    stageRef: null,
    anchor: null,
    content: [
      p([b('Step 1 — Sitemap Extraction.'), t(' A web scraper reads the IP First Response sitemap and populates '),
        c('data/ipfr_corpus/sitemap.csv'),
        t(' with page URLs, titles, IPFR content identifiers (letter + four digits, e.g. B1000), links to local markdown snapshots, and last-modified / last-checked date columns.')]),
      p([b('Step 2 — Page Scraping and Normalisation.'), t(' Each page on the IPFR sitemap is scraped and normalised into plain text using '), b('trafilatura'), t('. Trafilatura\'s built-in boilerplate removal strips navigation menus, footers, and sidebars automatically. The plain text output is the single canonical representation stored in the '),
        c('pages.content'),
        t(' column and used for all similarity calculations.')]),
      p([b('Step 3 — Change Detection (after initial run).'), t(' Every 24 hours, the pipeline checks whether the "Last modification date" for each IPFR page has changed. If yes, the page is re-scraped and continues through the full ingestion process. If no, the page is skipped.')]),
      p([b('Step 4 — Enrichment and Loading.')]),
      tbl(
        ['Asset', 'Method', 'Purpose in Tripwire'],
        [
          ['Version hash', 'SHA-256 of normalised plain text', 'Fast change detection'],
          ['Document-level embedding', 'BAAI/bge-base-en-v1.5', 'Quasi-graph construction'],
          ['Document chunks', 'Section-aware splitting from trafilatura XML headings', 'Bi-encoder and cross-encoder comparison'],
          ['Chunk-level embeddings', 'BAAI/bge-base-en-v1.5', 'Bi-encoder cosine similarity in Stage 5'],
          ['Named entity inventory', 'spaCy NER extraction', 'Stage 2 significance fingerprint'],
          ['Keyphrase extraction', 'YAKE', 'BM25 scoring in Stage 4'],
          ['Section-level metadata', 'Heading hierarchy from trafilatura XML', 'LLM assessment context'],
          ['Quasi-graph edges', 'See §4.2', 'Graph propagation in Stage 6'],
        ]
      ),
    ],
  },
  {
    id: 'graph-construction',
    heading: '4.2 Quasi-Graph Construction',
    level: 2,
    stageRef: null,
    anchor: 'doc-graph-edges',
    content: [
      p('The quasi-graph captures relationships between IPFR pages so that alerts can propagate from directly-affected pages to related pages. Edges are derived from three sources (each configurable independently):'),
      p([
        b('Embedding neighbours (semantic).'), t(' Compute cosine similarity between all pairs of document-level embeddings. For each page, retain edges to the top-'),
        cfg('graph.edge_types.embedding_similarity.top_k', 'graph.edge_types.embedding_similarity.top_k'),
        t(' most similar pages above a minimum similarity threshold of '),
        cfg('graph.edge_types.embedding_similarity.min_similarity', 'graph.edge_types.embedding_similarity.min_similarity'),
        t('. Edge weight equals the cosine similarity score.'),
      ]),
      p([
        b('Entity overlap (conceptual).'), t(' Compute the Jaccard coefficient of named entity sets between all pairs of pages. Retain edges where the Jaccard coefficient exceeds '),
        cfg('graph.edge_types.entity_overlap.min_jaccard', 'graph.edge_types.entity_overlap.min_jaccard'),
        t('. Edge weight equals the Jaccard coefficient scaled by '),
        cfg('graph.edge_types.entity_overlap.weight', 'graph.edge_types.entity_overlap.weight'),
        t('.'),
      ]),
      p([
        b('Internal links (structural — deferred).'), t(' Extract hyperlinks from each IPFR page that point to other IPFR pages. Currently disabled ('),
        cfg('graph.edge_types.internal_links.enabled', 'graph.edge_types.internal_links.enabled'),
        t(' = false) pending link extraction implementation.'),
      ]),
      p('Where multiple edge sources produce edges between the same pair of pages, the edges are combined by taking the maximum weight across all sources.'),
    ],
  },

  // ── §5 Repository Structure ───────────────────────────────────────────────
  {
    id: 'repo-structure',
    heading: '5. Repository Structure',
    level: 1,
    stageRef: null,
    anchor: null,
    content: [
      blk('text',
`tripwire/
├── tripwire_config.yaml              # All tuneable parameters
├── README.md
├── .gitattributes
├── .github/
│   └── workflows/
│       ├── tripwire.yml              # Main pipeline workflow (daily)
│       ├── ipfr_ingestion.yml        # IPFR corpus ingestion (daily)
│       └── feedback_ingestion.yml    # Feedback email parsing (6-hourly)
├── src/
│   ├── config.py                     # Load and validate tripwire_config.yaml
│   ├── pipeline.py                   # Main pipeline orchestration
│   ├── retry.py                      # Retry with exponential backoff
│   ├── errors.py                     # TripwireError, RetryableError, PermanentError
│   ├── stage1_metadata.py            # Metadata probe logic
│   ├── stage2_change_detection.py    # SHA-256, word-level diff, significance fingerprint
│   ├── stage3_diff.py                # Diff generation, FRL explainer retrieval, RSS extraction
│   ├── stage4_relevance.py           # BM25, bi-encoder similarity, RRF fusion
│   ├── stage5_biencoder.py           # Bi-encoder chunking and cosine similarity
│   ├── stage6_crossencoder.py        # Cross-encoder scoring, reranking, graph propagation
│   ├── stage7_aggregation.py         # Trigger grouping per IPFR page
│   ├── stage8_llm.py                 # LLM call, system prompt, JSON schema validation
│   ├── stage9_notification.py        # Email composition and sending
│   ├── scraper.py                    # Web scraping with trafilatura normalisation
│   ├── validation.py                 # Content validation after scraping
│   ├── health.py                     # Run health check and alert generation
│   └── observability.py              # Score distribution summary and reporting
├── ingestion/
│   ├── ingest.py                     # IPFR ingestion pipeline orchestration
│   ├── sitemap.py                    # Sitemap CSV construction
│   ├── scrape_ipfr.py                # IPFR page scraping
│   ├── enrich.py                     # Embedding, NER, YAKE, chunking
│   ├── graph.py                      # Quasi-graph edge computation
│   └── db.py                         # SQLite read/write (WAL mode)
├── data/
│   ├── logs/feedback.jsonl           # Content owner feedback log
│   ├── influencer_sources/
│   │   ├── source_registry.csv       # Target URLs, IDs, importance, frequency
│   │   └── snapshots/                # Per-source content snapshots
│   └── ipfr_corpus/
│       ├── ipfr.sqlite               # IPFR content, embeddings, NER, vectors, graph edges
│       ├── snapshots/                # Markdown snapshots of every IPFR page
│       └── sitemap.csv               # IPFR sitemap with URLs, snapshot links, dates
└── tests/                            # pytest test suite (18 files)`),
    ],
  },

  // ── §6 Error Handling ────────────────────────────────────────────────────
  {
    id: 'error-handling',
    heading: '6. Error Handling, Retries, and Observability',
    level: 1,
    stageRef: null,
    anchor: null,
    content: [],
  },
  {
    id: 'error-classification',
    heading: '6.1 Error Classification',
    level: 2,
    stageRef: null,
    anchor: null,
    content: [
      p([b('Retryable errors'), t(' are transient failures where a subsequent attempt may succeed: HTTP 5xx responses, connection timeouts, DNS resolution failures, LLM API rate limits, and SMTP connection failures. Retried with exponential backoff (base delay '),
        cfg('pipeline.retry_base_delay_seconds', 'pipeline.retry_base_delay_seconds'),
        t(' seconds, max '),
        cfg('pipeline.max_retries', 'pipeline.max_retries'),
        t(' retries, with jitter).'),
      ], 'doc-retries'),
      p([b('Permanent errors'), t(' are failures where retrying will not help: HTTP 404, HTTP 403, content validation failures (CAPTCHA detected, content too short, dramatic size change), and repeated LLM schema validation failures. Permanent errors are logged and the source is skipped for this run.')], 'doc-retry-backoff'),
    ],
  },
  {
    id: 'content-validation',
    heading: '6.2 Content Validation',
    level: 2,
    stageRef: null,
    anchor: null,
    content: [
      p('After every web scrape, the returned content is validated before being accepted:'),
      ul([
        'Minimum length check: content shorter than 200 characters is rejected as likely an error page or empty response.',
        'CAPTCHA / bot detection: scanned for common bot-detection phrases; if found, treated as a permanent error.',
        'Structural marker check: each source can define expected structural markers in the source registry.',
        'Dramatic size change detection: if the new content length is less than 30% or more than 300% of the previous snapshot, the content is flagged as suspicious.',
      ]),
    ],
  },
  {
    id: 'error-isolation',
    heading: '6.3 Stage-Level Error Isolation',
    level: 2,
    stageRef: null,
    anchor: null,
    content: [
      p('Each source is processed independently within a `try/except/finally` block. A failure on Source A never prevents processing of Sources B through N. Every source always produces a log entry regardless of outcome — recording either a successful result with scores or an error with the stage at which failure occurred, the error type, and the error message.'),
    ],
  },
  {
    id: 'graceful-degradation',
    heading: '6.4 Graceful Degradation',
    level: 2,
    stageRef: null,
    anchor: null,
    content: [
      p('When a component is unavailable, the pipeline degrades rather than halts:'),
      tbl(
        ['Component', 'Failure Mode', 'Degradation'],
        [
          ['Individual source scrape', 'Timeout / 5xx', 'Retry 3×, skip source, log error'],
          ['FRL explainer document', 'Unavailable', 'Fall back to diffing the legislation text directly'],
          ['spaCy model', 'Fails to load', 'Skip significance fingerprint; tag all changes as standard'],
          ['Bi-encoder model', 'Fails to load', 'Skip semantic stages; alert on lexical scores alone with reduced-confidence flag'],
          ['LLM API', 'Timeout / rate limit', 'Retry 3×; store triggers in deferred_triggers table for next run'],
          ['Email sending (SMTP)', 'Connection failure', 'Retry 3×; write email to local file and send health alert'],
          ['SQLite database', 'Locked / corrupted', 'Abort run, send immediate health alert'],
        ]
      ),
    ],
  },
  {
    id: 'deferred-triggers',
    heading: '6.5 Deferred Triggers',
    level: 2,
    stageRef: null,
    anchor: 'doc-deferred-triggers',
    content: [
      p([
        t('When the LLM API is unavailable, triggers that have passed all prior stages are written to the '),
        c('deferred_triggers'),
        t(' table in the SQLite database. At the start of the next run, before processing new sources, the pipeline checks for unprocessed deferred triggers and processes them through Stages 8–9 first. Deferred triggers are timestamped; triggers older than '),
        cfg('pipeline.deferred_trigger_max_age_days', 'pipeline.deferred_trigger_max_age_days'),
        t(' days are discarded (the next full run will regenerate them if the change is still relevant).'),
      ]),
    ],
  },
  {
    id: 'health-alerting',
    heading: '6.6 Health Alerting',
    level: 2,
    stageRef: null,
    anchor: 'doc-health-alerts',
    content: [
      p('After every run, the pipeline evaluates alert conditions:'),
      ul([
        ['Error rate > ', cfg('notifications.health_alert_conditions.error_rate_threshold', 'notifications.health_alert_conditions.error_rate_threshold'), ' in a single run → send health alert email.'],
        ['Same source fails ', cfg('notifications.health_alert_conditions.consecutive_failures_threshold', 'notifications.health_alert_conditions.consecutive_failures_threshold'), ' consecutive runs → send health alert email identifying the source.'],
        ['Pipeline fails to complete within ', cfg('notifications.health_alert_conditions.pipeline_timeout_minutes', 'notifications.health_alert_conditions.pipeline_timeout_minutes'), ' minutes (GitHub Actions `timeout-minutes`) → GitHub Actions failure notification.'],
        'LLM produces malformed output ≥ 2 times in a run → send health alert email.',
        'Cross-encoder truncation occurs ≥ 3 times in a run → send health alert identifying affected source/page pairs.',
      ], 'doc-timeout'),
      p([b('Health alerts'), t(' are sent to a separate email address (the system operator) and are distinct from content-owner notification emails.')]),
    ],
  },
  {
    id: 'observability',
    heading: '6.7 Observability',
    level: 2,
    stageRef: null,
    anchor: null,
    content: [
      p([
        t('The '),
        c('pipeline_runs'),
        t(' table in the SQLite database is the primary observability layer. A weekly summary script queries the last 30 days of run data and produces a report containing:'),
      ]),
      ul([
        'Reliability table: for each source — total runs, successful runs, error count, last error date, current streak.',
        'Score distributions: for each scoring stage — min, 25th percentile, median, 75th percentile, max across all runs.',
        'Alert volume: number of alerts generated per week, trending over time.',
        'Feedback summary: of alerts with feedback received, the proportion rated "useful" vs each non-useful category.',
      ]),
    ],
  },

  // ── §7 Configuration ─────────────────────────────────────────────────────
  {
    id: 'configuration',
    heading: '7. Configuration',
    level: 1,
    stageRef: null,
    anchor: null,
    content: [
      p([
        t('All tuneable weights, thresholds, model identifiers, and behavioural parameters are exposed in a single YAML file at the repository root: '),
        c('tripwire_config.yaml'),
        t('. This file is version-controlled in Git so that every parameter change is tracked as a commit.'),
      ]),
      p([
        t('The configuration file is loaded and validated at the start of every pipeline run. Validation checks include: relevance weights sum to 1.0, all thresholds are within valid ranges, model names are recognised, and required file paths exist. A full snapshot of the active configuration is included in the '),
        c('details'),
        t(' JSON column of the '),
        c('pipeline_runs'),
        t(' table for each run.'),
      ]),
      blk('yaml',
`# ==============================================================
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
  min_score_threshold: null          # set during observation period
  source_importance_floor: 0.5
  fast_pass:
    source_importance_min: 1.0
  yake:
    keyphrases_per_80_words: 1
    min_keyphrases: 5
    max_keyphrases: 15
    short_diff_word_threshold: 50

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
    commit_snapshots: true
    commit_database: true
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
  tool: "trafilatura"`),
    ],
  },
  {
    id: 'cicd',
    heading: '7.1 CI/CD Configuration',
    level: 2,
    stageRef: null,
    anchor: null,
    content: [
      p([b('CPU-only PyTorch.'), t(' GitHub Actions runners are CPU-only. PyTorch must be installed from the CPU-only index to avoid downloading ~1.5–2 GB of unused CUDA libraries:')]),
      blk('text', 'pip install torch --index-url https://download.pytorch.org/whl/cpu'),
      p('This provides identical inference performance at ~200 MB, saving disk space and reducing workflow setup time by 30–60 seconds.'),
      p([b('Model caching.'), t(' Cache the `~/.cache/huggingface/` directory using `actions/cache@v4` to avoid re-downloading models (BAAI/bge-base-en-v1.5 ~400 MB, gte-reranker-modernbert-base ~600 MB) on every run.')]),
      tbl(
        ['Workflow', 'Trigger', 'Timeout', 'Purpose'],
        [
          ['ipfr_ingestion.yml', 'Daily cron 01:00 UTC + workflow_dispatch', '60 min', 'Refresh IPFR corpus in SQLite'],
          ['tripwire.yml', 'Daily cron 02:00 UTC + workflow_dispatch', '30 min', 'Full pipeline run (Stages 1–9)'],
          ['feedback_ingestion.yml', 'Every 6 hours + workflow_dispatch', '10 min', 'Poll Gmail for feedback replies'],
        ]
      ),
    ],
  },
  {
    id: 'persistent-storage',
    heading: '7.2 Persistent Storage',
    level: 2,
    stageRef: null,
    anchor: 'doc-snapshots',
    content: [
      p('GitHub Actions runners are ephemeral: the workspace is wiped at the end of every job. Any file that is not explicitly persisted is lost. `actions/cache` is not a reliable persistence mechanism for data that must survive across runs — cache entries can be evicted at any time without notice.'),
      p([
        b('Persistence strategy: commit to the repository.'), t(' Both categories of state that must survive between runs are committed back to the repository at the end of each workflow run. Influencer snapshots ('),
        c('data/influencer_sources/snapshots/'),
        t(') are plain text — human-readable, diff-friendly. The IPFR SQLite database ('),
        c('data/ipfr_corpus/ipfr.sqlite'),
        t(') is a binary file tracked directly in Git; migrate to Git LFS if it exceeds ~50 MB.'),
      ]),
      blk('yaml',
`- name: Commit updated snapshots and database
  run: |
    git config user.name  "github-actions[bot]"
    git config user.email "github-actions[bot]@users.noreply.github.com"
    git add data/influencer_sources/snapshots/
    git add data/ipfr_corpus/ipfr.sqlite
    git diff --cached --quiet || git commit -m "chore: update snapshots [run \${RUN_ID}]"
    git push
  env:
    RUN_ID: \${{ github.run_id }}`, 'doc-git-persistence'),
      p([
        t('The '),
        c('.gitattributes'),
        t(' file marks the SQLite file as binary so that Git does not attempt text diffs or line-ending normalisation. Each workflow performs a '),
        c('git pull --rebase'),
        t(' as its first step to ensure it starts from the latest committed state, preventing conflicts between the ingestion and Tripwire workflows.'),
      ]),
      p([
        b('Snapshot retention.'), t(' The '),
        cfg('storage.content_versions_retained', 'storage.content_versions_retained'),
        t(' setting controls how many previous snapshot versions are retained per influencer source (default: 6).'),
      ]),
    ],
  },
  {
    id: 'feedback-ingestion',
    heading: '7.3 Feedback Ingestion — Gmail IMAP Polling',
    level: 2,
    stageRef: null,
    anchor: null,
    content: [
      p([
        t('The Stage 9 notification email includes four mailto feedback links. When the content owner clicks a link, their email client opens a pre-formatted reply containing the run ID, IPFR page ID, source ID, and feedback category. A scheduled workflow ('),
        c('feedback_ingestion.yml'),
        t(') polls a dedicated Gmail mailbox for these replies, parses them, and appends them to '),
        c('data/logs/feedback.jsonl'),
        t('.'),
      ]),
      p([
        b('Mailto template.'), t(' The subject line is prefixed with '),
        c('[TRIPWIRE]'),
        t(' so the IMAP query can filter precisely for Tripwire replies: '),
        c('Subject: [TRIPWIRE] Feedback — {run_id} — {page_id}'),
        t('.'),
      ]),
      p('One-time setup: create a dedicated Gmail account, enable IMAP access, and generate a Gmail App Password stored as a GitHub Actions secret named `FEEDBACK_GMAIL_APP_PASSWORD`.'),
      blk('python',
`import imaplib, email, os

mail = imaplib.IMAP4_SSL("imap.gmail.com")
mail.login("tripwire-feedback@gmail.com", os.environ["FEEDBACK_GMAIL_APP_PASSWORD"])
mail.select("inbox")
_, ids = mail.search(None, 'UNSEEN SUBJECT "[TRIPWIRE]"')`),
      p('The workflow runs on a 6-hour schedule and can be triggered manually via `workflow_dispatch`. Volume comfortably fits within Gmail\'s free limits (expected: tens of feedback emails per month).'),
    ],
  },
  {
    id: 'lazy-model-loading',
    heading: '7.4 Lazy Model Loading',
    level: 2,
    stageRef: null,
    anchor: null,
    content: [
      p('The two inference models are large and slow to load on a CPU-only runner. If both models are loaded unconditionally at workflow start, every run pays ~60 s and ~1 GB of RAM — even on the majority of runs where no source reaches Stage 5. Lazy loading defers each model until the moment it is actually needed.'),
      tbl(
        ['Model', 'Size', 'Approx. load time (CPU)'],
        [
          ['BAAI/bge-base-en-v1.5 (bi-encoder)', '~400 MB', '~25 s'],
          ['gte-reranker-modernbert-base (cross-encoder)', '~600 MB', '~35 s'],
        ]
      ),
      p('Loading strategy: Stages 1–4 run with no models loaded. If any candidate reaches Stage 5, the bi-encoder is loaded. If any candidate survives Stage 5, the bi-encoder is released and the cross-encoder is loaded. This keeps peak RAM at ~600 MB (the larger model alone) rather than ~1 GB (both simultaneously).'),
      tbl(
        ['Scenario', 'Without lazy loading', 'With lazy loading', 'Saving'],
        [
          ['No changes detected (most days)', 'Load both: ~60 s', 'Load neither: 0 s', '~60 s'],
          ['Changes reach Stage 5 only', 'Load both: ~60 s', 'Load bi-encoder only: ~25 s', '~35 s'],
          ['Full pipeline executes', 'Load both: ~60 s', 'Sequential load: ~60 s', '0 s'],
        ]
      ),
    ],
  },

  // ── §8 Pipeline Run Logging ───────────────────────────────────────────────
  {
    id: 'run-logging',
    heading: '8. Pipeline Run Logging',
    level: 1,
    stageRef: null,
    anchor: null,
    content: [
      p([
        t('Pipeline run data is logged directly to a '),
        c('pipeline_runs'),
        t(' table in the IPFR SQLite database, with a '),
        c('details'),
        t(' JSON column for per-stage structured data. SQLite\'s JSON1 extension ('),
        c('json_extract()'),
        t(') enables querying into the structured details. Deferred triggers are stored in a '),
        c('deferred_triggers'),
        t(' table in the same database.'),
      ]),
    ],
  },
  {
    id: 'logging-tables',
    heading: '8.1 SQLite Logging Tables',
    level: 2,
    stageRef: null,
    anchor: null,
    content: [
      blk('sql',
`-- Pipeline run log: one row per source per run
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
    trigger_data    TEXT NOT NULL,          -- JSON with scores and diffs
    created_at      TEXT NOT NULL,          -- ISO 8601
    processed       INTEGER DEFAULT 0       -- 0 = pending, 1 = processed
);`),
    ],
  },
  {
    id: 'job-summary',
    heading: '8.2 GitHub Actions Job Summary',
    level: 2,
    stageRef: null,
    anchor: null,
    content: [
      p([
        t('At the end of each run, a formatted markdown summary is written to '),
        c('$GITHUB_STEP_SUMMARY'),
        t('. This renders directly in the GitHub Actions UI and is visible to anyone with repository access. The summary includes: sources checked, changes detected, scores, verdicts, and any errors.'),
      ]),
      p([
        t('For full raw detail, the current run\'s log entry is published as a '),
        b('GitHub Actions workflow artifact'),
        t(' using '),
        c('actions/upload-artifact@v4'),
        t(' (retained for 90 days by default).'),
      ]),
    ],
  },
  {
    id: 'log-example',
    heading: '8.3 Example Log Entry',
    level: 2,
    stageRef: null,
    anchor: null,
    content: [
      p([
        t('The '),
        c('details'),
        t(' JSON column contains structured per-stage data. A full entry for a source that triggers an alert:'),
      ]),
      blk('json',
`{
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
    "diff": { "type": "explainer_document", "size_chars": 3420 },
    "relevance": {
      "bm25_rank": 1, "semantic_rank": 2, "rrf_score": 0.048,
      "source_importance": 0.70, "final_score": 0.041,
      "fast_pass_triggered": false, "decision": "proceed"
    },
    "biencoder": {
      "candidate_pages": [{
        "ipfr_page_id": "B1012", "max_chunk_score": 0.81,
        "chunks_above_low_medium": 5, "trigger_reason": "single_chunk_high"
      }]
    },
    "crossencoder": {
      "scored_pages": [{
        "ipfr_page_id": "B1012", "crossencoder_score": 0.74,
        "reranked_score": 0.78, "graph_propagated_to": ["C2003"],
        "decision": "proceed"
      }]
    },
    "llm_assessment": {
      "ipfr_page_id": "B1012", "model": "gpt-4o",
      "verdict": "CHANGE_REQUIRED", "confidence": 0.85,
      "schema_valid": true, "retries": 0,
      "prompt_tokens": 1842, "completion_tokens": 214, "total_tokens": 2056
    }
  }
}`),
    ],
  },

  // ── §9 SQLite Schema ─────────────────────────────────────────────────────
  {
    id: 'sqlite-schema',
    heading: '9. SQLite Schema — IPFR Corpus',
    level: 1,
    stageRef: null,
    anchor: 'doc-sqlite',
    content: [
      p([
        t('The IPFR SQLite database uses WAL (Write-Ahead Logging) mode ('),
        cfg('storage.sqlite_wal_mode', 'storage.sqlite_wal_mode'),
        t(') to allow concurrent reads during writes. The ingestion pipeline writes to this database; the Tripwire pipeline reads from it.'),
      ]),
      tbl(
        ['Table', 'Purpose'],
        [
          ['pages', 'One row per IPFR page: URL, title, normalised content, version hash, dates, document-level embedding'],
          ['page_chunks', 'Pre-chunked IPFR content with BGE chunk embeddings'],
          ['entities', 'Named entities (ORG, PERSON, GPE, LAW, etc.) per page'],
          ['keyphrases', 'YAKE keyphrases with IDF weights per page'],
          ['graph_edges', 'Quasi-graph edges (embedding similarity, entity overlap)'],
          ['sections', 'Section headings and offsets per page'],
          ['pipeline_runs', 'Per-source log entry for every run'],
          ['deferred_triggers', 'Trigger bundles queued for LLM retry after API failures'],
        ]
      ),
      blk('sql',
`-- Pages table: one row per IPFR page
CREATE TABLE pages (
    page_id         TEXT PRIMARY KEY,     -- e.g. "B1012"
    url             TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    version_hash    TEXT NOT NULL,        -- SHA-256 of normalised plain text
    last_modified   TEXT,                 -- ISO 8601 date from IPFR sitemap
    last_checked    TEXT,                 -- ISO 8601 date of last ingestion check
    last_ingested   TEXT,                 -- ISO 8601 date of last full ingestion
    doc_embedding   BLOB                  -- BAAI/bge-base-en-v1.5 embedding
);

-- Chunks table: one row per chunk of each page
CREATE TABLE page_chunks (
    chunk_id        TEXT PRIMARY KEY,     -- e.g. "B1012-chunk-003"
    page_id         TEXT NOT NULL REFERENCES pages(page_id),
    chunk_text      TEXT NOT NULL,
    chunk_index     INTEGER NOT NULL,
    section_heading TEXT,
    chunk_embedding BLOB NOT NULL
);

-- Entities table: named entities extracted per page
CREATE TABLE entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id         TEXT NOT NULL REFERENCES pages(page_id),
    entity_text     TEXT NOT NULL,
    entity_type     TEXT NOT NULL,        -- "LEGISLATION", "ORG", "SECTION", "DATE"
    UNIQUE(page_id, entity_text, entity_type)
);

-- Keyphrases table: YAKE-extracted keyphrases per page
CREATE TABLE keyphrases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id         TEXT NOT NULL REFERENCES pages(page_id),
    keyphrase       TEXT NOT NULL,
    score           REAL NOT NULL
);

-- Graph edges: quasi-graph relationships between IPFR pages
CREATE TABLE graph_edges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_page_id  TEXT NOT NULL REFERENCES pages(page_id),
    target_page_id  TEXT NOT NULL REFERENCES pages(page_id),
    edge_type       TEXT NOT NULL,        -- "embedding_similarity", "entity_overlap", "internal_link"
    weight          REAL NOT NULL,
    UNIQUE(source_page_id, target_page_id, edge_type)
);

-- Section metadata: heading hierarchy per page
CREATE TABLE sections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id         TEXT NOT NULL REFERENCES pages(page_id),
    heading_text    TEXT NOT NULL,
    heading_level   INTEGER NOT NULL,     -- 1 = H1, 2 = H2, etc.
    char_start      INTEGER NOT NULL,
    char_end        INTEGER NOT NULL
);`),
    ],
  },

  // ── §10 Phased Implementation Plan ───────────────────────────────────────
  {
    id: 'implementation-plan',
    heading: '10. Phased Implementation Plan',
    level: 1,
    stageRef: null,
    anchor: null,
    content: [],
  },
  {
    id: 'phase-1',
    heading: 'Phase 1 — Foundation',
    level: 2,
    stageRef: null,
    anchor: null,
    content: [
      tbl(
        ['#', 'Task', 'Depends On'],
        [
          ['1.1', 'Create repository structure and tripwire_config.yaml', '—'],
          ['1.2', 'Implement config.py (load, validate, snapshot)', '1.1'],
          ['1.3', 'Implement errors.py and retry.py (error classes, exponential backoff)', '—'],
          ['1.4', 'Define and create the IPFR SQLite schema (db.py)', '—'],
          ['1.5', 'Build IPFR ingestion pipeline: sitemap → scrape → normalise', '1.4'],
          ['1.6', 'Build IPFR enrichment: chunking, embeddings, NER, YAKE, section metadata', '1.5'],
          ['1.7', 'Build quasi-graph edge computation (embedding neighbours + entity overlap)', '1.6'],
          ['1.8', 'Implement SQLite pipeline run logging and GitHub Actions Job Summary', '1.2, 1.4'],
          ['1.9', 'Build the influencer source registry CSV and metadata probe (Stage 1)', '1.2'],
          ['1.10', 'Set up GitHub Actions workflow for IPFR ingestion (daily)', '1.5–1.7'],
        ]
      ),
    ],
  },
  {
    id: 'phase-2',
    heading: 'Phase 2 — Change Detection',
    level: 2,
    stageRef: null,
    anchor: null,
    content: [
      tbl(
        ['#', 'Task', 'Depends On'],
        [
          ['2.1', 'Implement web scraping with trafilatura normalisation and content validation', '1.3'],
          ['2.2', 'Implement Stage 2: SHA-256 hash check, word-level diff, significance fingerprint tagger', '2.1'],
          ['2.3', 'Implement Stage 3: diff generation, FRL explainer retrieval, RSS extraction', '2.2'],
          ['2.4', 'Implement snapshot storage, 6-version retention, and end-of-run Git commit/push', '2.1'],
          ['2.5', 'Create 10–15 manually altered snapshots for threshold testing', '2.2'],
          ['2.6', 'Run in observation mode; collect score distributions; refine Stage 2 thresholds', '2.2–2.4'],
        ]
      ),
    ],
  },
  {
    id: 'phase-3',
    heading: 'Phase 3 — Relevance and Semantic Scoring',
    level: 2,
    stageRef: null,
    anchor: null,
    content: [
      tbl(
        ['#', 'Task', 'Depends On'],
        [
          ['3.1', 'Implement Stage 4: YAKE-driven BM25, bi-encoder semantic similarity, weighted RRF fusion, source importance multiplier', '1.6, 2.3'],
          ['3.2', 'Implement fast-pass override logic (source importance = 1.0)', '3.1'],
          ['3.3', 'Implement Stage 5: bi-encoder chunking and cosine similarity', '1.6'],
          ['3.4', 'Implement Stage 6: cross-encoder scoring, reranking, graph propagation', '1.7, 3.3'],
          ['3.5', 'Continue observation mode; extend to Stages 4–6; refine all thresholds', '3.1–3.4'],
        ]
      ),
    ],
  },
  {
    id: 'phase-4',
    heading: 'Phase 4 — LLM Assessment and Notification',
    level: 2,
    stageRef: null,
    anchor: null,
    content: [
      tbl(
        ['#', 'Task', 'Depends On'],
        [
          ['4.1', 'Define LLM output JSON schema and validation logic', '—'],
          ['4.2', 'Author the LLM system prompt', '—'],
          ['4.3', 'Implement Stage 7: trigger aggregation per IPFR page', '3.4'],
          ['4.4', 'Implement Stage 8: LLM call with schema validation and retry', '4.1–4.3'],
          ['4.5', 'Implement deferred trigger mechanism for LLM failures', '4.4'],
          ['4.6', 'Implement Stage 9: consolidated email with feedback mailto links', '4.4'],
          ['4.7', 'Implement feedback ingestion (parse replies → feedback.jsonl)', '4.6'],
          ['4.8', 'Set up main Tripwire GitHub Actions workflow (daily, after ingestion)', 'All'],
          ['4.9', 'Disable observation mode; begin live operation', 'All'],
        ]
      ),
    ],
  },
  {
    id: 'phase-5',
    heading: 'Phase 5 — Hardening and Iteration',
    level: 2,
    stageRef: null,
    anchor: null,
    content: [
      tbl(
        ['#', 'Task', 'Depends On'],
        [
          ['5.1', 'Implement health alerting (error rate, consecutive failures, timeout)', '4.8'],
          ['5.2', 'Implement weekly observability summary report', '1.8'],
          ['5.3', 'Calibrate thresholds using accumulated feedback data', '4.7, 4.9'],
          ['5.4', 'Evaluate alternative relevance weights via grid search against feedback log', '5.3'],
          ['5.5', 'Enable internal-link graph edges when link extraction is ready', '1.7'],
          ['5.6', 'Evaluate positional/proximity BM25 extensions if standard BM25 proves insufficient', '3.1'],
          ['5.7', 'Write operational runbooks: failure response, adding sources, adjusting thresholds', 'All'],
        ]
      ),
    ],
  },
];
