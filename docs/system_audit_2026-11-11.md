# Tripwire System Audit — health, alert quality and threshold tuning

**Date:** 2026-11-11
**Data:** the live `ipfr.sqlite` pulled from R2 (202 pipeline runs, 2026-04-28 → 2026-11-11; 28,356 source-runs; 1,828 distinct LLM assessments), the committed snapshots and diffs, and the current code and config.
**Builds on:** `docs/stage_gate_efficacy_audit_2026-07-21.md` and `docs/threshold_calibration_efficacy_2026-07-21.md`.
**Labelled dataset produced by this audit:** `data/logs/audit_labels_2026-11-11.csv` (376 change/page pairs with two independent model verdicts and replayed scores).

Nothing in the pipeline, config or database was changed by this audit.

> **Updated the same day:** §10 adds a dedicated false-negative hunt (1,146 further source/page reviews). It found more misses and **changes recommendations 8 and 9**: under a "misses are far worse than false alarms" priority, do not raise the bi-encoder floor and do not narrow to the top 3. Read §10 before acting on §5 or §9.

---

## 1. Verdict

Tripwire runs every day and has not missed a scheduled day since July, but it is **not yet fit for purpose as an alerting system**:

- **About 4 in 5 alerts are wrong.** Of 39 reviewed `CHANGE_REQUIRED` alerts, both independent reviewers rejected 31 (79%). Only 2 were upheld by both.
- **It missed real changes it had in hand.** The one IPFR page that names the retired IVDS system was scored the most relevant page in the corpus for the notice announcing IVDS's replacement, and the LLM still returned `NO_CHANGE`.
- **A quarter of the watch list is not being watched.** 38 of 156 sources are disabled, failing every check, or frozen on a stored error page, and 16 more are degraded. Both enabled RSS "feeds" are HTML pages that can never report a change.
- **Most LLM spend is avoidable.** 49% of LLM calls come from a graph-propagation path that bypasses the Stage 6 threshold and produced no valid alert.

One correction to the brief: the parameters were tuned once, on 2026-07-21 (a bi-encoder floor of 0.63 and a cross-encoder scoring fix). This audit is the first calibration with persisted scores and independently adjudicated labels.

---

## 2. How the review was done

| Step | What | Volume | Model | Cost |
|------|------|-------:|-------|-----:|
| Triage | Every distinct change event that reached Stage 4, judged against a catalogue of all IPFR pages: what kind of change is it, and which pages could it affect? | 389 events | `gemini-3.5-flash-lite` (batch) | ≈ $0.48 |
| Adjudication | Change/page pairs judged with the full page text: update required, review worthwhile, or no action | 374 pairs | `gpt-5.4-mini` (batch) | ≈ $0.47 |
| Second opinion | The same pairs, independently | 374 pairs | `gemini-3.5-flash` (batch) | ≈ $0.97 |
| Prompt experiment | A tightened Stage 8 prompt against the labelled set | 177 pairs × 2 models | `gpt-5.4-mini` (batch), `gpt-4.1-mini` (batch stalled at 176/177, re-run synchronously) | ≈ $0.51 |
| Replay | Stages 4 and 5 re-run locally over **all** pages for every event, not just the top five | 389 events | local BGE model | $0 |

Approximate spend: **Gemini $1.45, OpenAI $1.00** (token counts are exact; prices are list batch rates taken from third-party price trackers, using the higher figure where sources disagreed).

The adjudicated pairs cover every stage where a candidate can be dropped:

| Set | Pairs | What it tests |
|-----|------:|---------------|
| A | 65 | Everything that passed all filters: every `CHANGE_REQUIRED` alert, per contributing source |
| B | 54 | Pairs the triage model nominated independently of the pipeline |
| C | 95 | Random sample of pairs the Stage 8 LLM rejected (`NO_CHANGE`) |
| D | 75 | Random sample of pairs Stage 5 rejected, plus the single pair Stage 6 has rejected since July |
| E | 97 | Pairs Stage 4 never forwarded: the two best bi-encoder pages outside the Stage 4 top five, for every substantive change |

Key claims were then checked by hand against page text. Limits of this method are in §8.

---

## 3. What passed all the filters, and should it have?

40 distinct alerts (run + IPFR page) were raised across 14 runs. 39 could be re-assessed.

| Outcome | Alerts | Share |
|---------|-------:|------:|
| Both reviewers: update required | 1 | 3% |
| Both reviewers: review worthwhile | 1 | 3% |
| One reviewer only | 6 | 15% |
| **Both reviewers: no action** | **31** | **79%** |

**Upheld**

- `tm_manual` 2026-11-05 → *Remove a trade mark from the register* (`X39AC5`). The page tells readers to "Consider carefully Part 62 of the Trade Marks Manual"; revocation content was consolidated into a new Part 36.
- `domain_name_dispute_resolution` 2026-07-09 → *Challenge the use of international domain name* (`XE69D4`). WIPO added a one-month expedited UDRP service; the page gives two months as the normal duration.

**Why the other 31 were wrong** (hand-checked examples):

| Failure | Example | Evidence |
|---------|---------|----------|
| The LLM asserted page content that is not there | eBay VeRO email change → three pages "currently reference vero@ebay.com" | No IPFR page contains that address |
| Same | WIPO Wallet → two pages "list payment methods" (credit card, PayPal) | Neither page mentions PayPal or any payment method |
| Whitespace-only diff treated as a change | `ttiap_reasons_to_make_a_complaint` 2026-06-18 → 4 alerts suggesting new sections | Removed and added lines are identical once whitespace is collapsed |
| Scrape error treated as a change | `get_a_free_consultation` 2026-10-12 → "the IPTA website is unreachable; remove the hyperlink" | The "change" was a browser timeout page |
| "The source has more detail than the page" | Federal Court fee increase → 5 pages, incl. *What happens in an IP Australia hearing?* | None of the pages quote court filing fees |
| Marketing copy | Amazon seller cashback rates; Amazon patent-evaluation deposit → 3 alerts | The pages mention neither |
| Graph-propagated bundle | *What can I expect from a lawyer or attorney?* flagged 4 times | All 13 graph-only alert pairs rejected by both reviewers |

There is no memory between runs, so the same page is re-alerted for the same unresolved issue: 8 pages account for 23 of the 40 alerts.

**Two things worth fixing on IPFR today, found in passing**

- *Obtain legal advice on an accusation of infringement* (`X0F896`) says patent attorneys cost "$500-1,000 per hour". The Trans-Tasman IP Attorneys Board page says $400–700. This was alerted in July from a whitespace-only diff, then judged `NO_CHANGE` from the same input in October.
- *What happens in an IP Australia hearing?* (`X7E272`) says "Australian Review Tribunal (ART)". The body is the Administrative Review Tribunal.

---

## 4. What was filtered out, and should it have been?

| Stage that dropped it | Pairs reviewed | Judged to need action | Reading |
|-----------------------|---------------:|----------------------:|---------|
| Stage 5 bi-encoder (D) | 74 | 0 | The floor is safe and has room to rise |
| Stage 6 cross-encoder (D) | 1 | 0 | It has rejected 1 of 660 candidates since July — still not a gate |
| Stage 8 LLM, random sample (C) | 95 | 0 real misses¹ | Random rejections are sound; the misses are in the high-score tail below |
| Stage 4 top-five cut (E) | 97 | 1 update, 2 review² | **The main recall leak** |

¹ One flagged pair is the pre-existing attorney-fee discrepancy above, not a change.
² One reviewer only; the update was verified by hand.

**Confirmed misses**

1. **QP Portal replaces IVDS — still unfixed on IPFR.** *Choosing the right IP professional* (`XE324C`) tells readers to "Submit a detailed description … through the Interactive Variety Description System (IVDS)". It is the only IPFR page that mentions IVDS.
   - 2026-07-02, official notice "new Qualified Persons (QP) portal to replace the current … IVDS": `XE324C` reached the LLM (bi-encoder 0.809, the highest of all 132 pages) and the **LLM returned `NO_CHANGE`**. It flagged a different page that does not mention IVDS.
   - 2026-07-02, PBR manual "QP Portal … to replace IVDS": `XE324C` was the second-best bi-encoder page of 132 but **Stage 4 ranked it 61st**, so it was never a candidate.
2. **Part 62 → Part 36, caught two weeks late.** The Part 36 rows first appeared in the `tm_manual` diff on 2026-10-22; `X39AC5` reached the LLM (bi-encoder 0.832, rank 1) and got `NO_CHANGE`. It was only alerted when further rows appeared on 2026-11-05.
3. **WIPO expedited UDRP → *Dispute a domain name that infringes your trade mark*** (`X6F04B`): third-best bi-encoder page, Stage 4 rank 6, never a candidate. Minor.

**Changes that never reach the filters at all** are the larger gap — see §6.

---

## 5. Threshold tuning, with evidence

Positives are the 9 change/page pairs upheld in §3–§4 ("gold": 4 update, 4 review, 1 pre-existing discrepancy). Negatives are the 360 pairs both reviewers rejected. Scores are from the full-corpus replay; replayed scores match the persisted production scores (median absolute difference < 0.000001 for runs since 15 October).

### 5.1 Stage 5 separates well; Stage 4 does not

| Signal | Gold positives | Judged negatives |
|--------|---------------|------------------|
| Bi-encoder max-chunk score | min **0.733**, median 0.807 | median 0.676, 90th percentile 0.762 |
| Bi-encoder rank among all 132 pages | **always 1–3** | — |
| Stage 4 rank | 1, 1, 1, 1, 2, 2, 4, **6, 61** | — |

Bi-encoder AUC against judged negatives: **0.96**.

Stage 4 exists to shortlist five pages cheaply for Stage 5. But Stage 5 over all pages takes under a second per change, so the shortlist saves nothing and is where recall is lost. Stage 4's "semantic rank" compares whole-document embeddings and disagrees with the chunk-level score that actually works (`XE324C`: semantic rank 51, chunk rank 2).

### 5.2 Replace "Stage 4 top 5, then Stage 5 gate" with "top K by bi-encoder score, above a floor"

Replayed over all 389 change events:

| Selection rule | Gold kept | Candidates per event | Events with no candidate |
|----------------|----------:|---------------------:|-------------------------:|
| **Current** (Stage 4 top 5 → Stage 5 gate, floor 0.63) | **7 / 9** | 2.97 | 20% |
| Top 5 by bi-encoder, floor 0.63 | 9 / 9 | 4.06 | 12% |
| **Top 5, floor 0.68** (recommended) | **9 / 9** | **2.71** | 35% |
| Top 5, floor 0.70 | 9 / 9 | 2.25 | 43% |
| Top 3, floor 0.70 | 9 / 9 | 1.47 | 43% |
| Top 3, floor 0.72 | 9 / 9 | 1.18 | — |

Recommended: **top 5, floor 0.68**. It recovers both Stage 4 misses with slightly fewer candidates than today, and leaves a 0.05 margin under the lowest gold score (0.733). Top 3 / floor 0.70 halves volume at the same recall on this set, but nine positives is too few to justify a 0.03 margin yet.

### 5.3 Stage 5 floor, if Stage 4 is left as is

On the 660 candidates that passed Stage 5 since 22 July (persisted production scores):

| `max_chunk_floor` | Candidates kept | Production `CHANGE_REQUIRED` kept | Note |
|------------------:|----------------:|----------------------------------:|------|
| 0.63 (current) | 660 (100%) | 25 / 25 | |
| 0.65 | 584 (88%) | 25 / 25 | |
| 0.66 | 538 (82%) | 25 / 25 | |
| **0.68** | **440 (67%)** | 24 / 25 | The one lost is an alert both reviewers rejected |
| 0.70 | 348 (53%) | 22 / 25 | All three lost were rejected by both reviewers |
| 0.75 | 162 (25%) | 15 / 25 | Below the lowest gold score (0.733) — too high |

No gold positive scores below 0.733, and 0 of 74 sampled Stage 5 rejections needed action. **Raise `max_chunk_floor` from 0.63 to 0.68**: a third fewer LLM calls.

The other two Stage 5 knobs are dead and can be retired:

- `low_medium_threshold: 0.45` with `low_medium_min_chunks: 3` — the median candidate has **10** chunks above 0.45, so this path passes almost everything. 325 of the 411 Stage 5 rejections since July were made by the floor alone.
- `high_threshold: 0.75` — irrelevant once a floor applies to both paths.

### 5.4 Stage 6 cross-encoder

| Gate | Candidates kept (of 660) | Production `CHANGE_REQUIRED` kept |
|------|-------------------------:|----------------------------------:|
| Current: blended final score ≥ 0.60 | 659 | 25 / 25 |
| Raw cross-encoder ≥ 0.75 | 586 (89%) | 25 / 25 |
| Raw cross-encoder ≥ 0.78 | 515 (78%) | 25 / 25 |
| Raw cross-encoder ≥ 0.80 | 443 (67%) | 24 / 25 |
| Raw cross-encoder ≥ 0.85 | 218 (33%) | 9 / 25 |

The July fix did not make Stage 6 a gate. The decision score is `0.8 × cross-encoder + 0.2 × lexical + graph boost`; the boost adds up to 0.27 and pushes final scores above 1.0, so 0.60 is unreachable from below.

Only two gold positives have persisted cross-encoder scores (0.832 and 0.878), so I would not set a cut-point on this evidence. Either:

- gate on the **raw** score at a conservative **0.75** (11% fewer calls, no known positive within 0.08), or
- remove Stage 6. It is the slowest model in the run and currently changes nothing.

### 5.5 Graph propagation (`graph.*`)

| | LLM calls | Alerts raised | Alerts upheld by either reviewer |
|--|----------:|--------------:|---------------------------------:|
| Direct candidates | 940 | 34 | 8 |
| **Graph-propagated only** | **888 (49%)** | 6 | **0** |

Stage 7 creates an LLM bundle for every graph neighbour that receives any signal ≥ `propagation_threshold` (0.05). These never pass the Stage 6 threshold — they bypass it. Every embedding-similarity edge has weight ≥ 0.74 against a configured `min_similarity` of 0.40, because 97 of 130 pages open with the same disclaimer paragraph, which makes most pages look alike.

**Stop Stage 7 creating bundles for propagated pages** (or set `graph.enabled: false`). That halves LLM spend with no loss on this evidence.

### 5.6 Settings to leave alone

| Setting | Finding |
|---------|---------|
| `relevance_scoring.min_score_threshold`, `rrf_*`, `source_importance_floor` | Moot if Stage 4 stops being a gate (§5.2). If it stays, raise `top_n_candidates` from 5 to 25 (reaches 8 of 9 gold) rather than tuning weights. |
| `pipeline.llm_temperature: 0.2` | Not the problem; see §7. |
| `health_alert_conditions.error_rate_threshold: 0.30` | The due-check error rate has been 41–59% every month, so this has fired on every run. It will only mean something once §6 is fixed. |

---

## 6. System health

**Runs.** 195 of 198 days have a run (three missed days in July). Longest run 41 minutes against a 30-minute timeout; typical run 15 minutes. No deferred triggers, no LLM retries. Total LLM spend to date ≈ $3.50.

**Incident worth knowing about.** IPFR ingestion ran 22 times in September, once in October (all pages errored) and resumed on 4 November with the R2 move. The corpus was stale for about six weeks. The feedback workflow failed 36 of 38 runs from 29 October until the fix on 11 November. `llm_assessments` holds 462 duplicate rows (runs 462–498) left by the recovery merge; they inflate any count taken from that table.

### 6.1 Source coverage

| State | Sources | Detail |
|-------|--------:|--------|
| Healthy | 102 | |
| Disabled in July, still disabled | 17 | Federal Court (7), WIPO Lex, auDA, APH and others |
| **Stored baseline is an error page** | 17 | "This site can't be reached", Cloudflare origin error, 403, FRL maintenance banner |
| Failing ≥ 90% of checks over 60 days | 2 | |
| Failing 30–90% | 4 | All legislation |
| RSS source that cannot detect change | 2 | `wipo_news`, `euipo_enforcement_news` |
| Baseline under 1,000 characters | 12 | Eight WIPO treaty pages, two WIPO clause pages, Amazon, Shein |

**38 of 156 sources (24%) are not monitored at all** (the first three rows and the RSS row) and 16 more are degraded. The 23 enabled sources that are failing or frozen include ten at importance 0.6 and three at 0.7. Scrape errors are 42–52% of due checks every month since August, because failed sources are retried daily.

### 6.2 Defects behind those numbers

1. **Error pages are accepted as content, then lock the source.** `validation.py` passes any text over 200 characters without a CAPTCHA phrase, so a Chrome error page becomes the baseline. After that:
   - if the error repeats, the hash matches and the run logs `no_change` — a silent blind spot;
   - if the real page returns, it is more than 3× larger and is rejected as a "dramatic content size change", permanently. `wipo_guide_to_domain_name_dispute_resolution` (893 → 21,833 characters) and `asbfeo_about_us` have failed every check for at least 60 days this way.
2. **RSS failures are logged as "no change".** `euipo_enforcement_news` returns 403 on every fetch; the run records `no_change` at Stage 5 with an empty diff (190 runs). Its URL and `wipo_news`'s are HTML pages, not feeds: `wipo_news` parses to zero items every day and has reported "unchanged" 191 times. This breaks the fail-closed rule.
3. **Legislation sources can be blocked by a scrape they do not need.** Change detection for FRL uses the legislation API, but the page is scraped and size-validated first. `legislation.gov.au` renders anywhere from 230 to 137,000 characters, so the size check fails intermittently (90 errors across 8 Acts) and the amendment is never processed.
4. **Empty diffs run the full semantic stack.** 282 of the 693 source-runs that reached Stage 4 had an empty diff (197 failed RSS fetches, 85 first-run baselines).
5. **Whitespace-only diffs count as significant.** 53 of 303 webpage change events (17%) have no difference once whitespace is collapsed. They produced 206 LLM assessments and 6 alerts.
6. **Legislation explainers have encoding damage** (`â¢` for bullets) and the same explainer can re-trigger on consecutive checks (9 repeats).
7. **Health alerts have become noise.** The latest run queued 19 health warnings; the same sources have been failing for months.

### 6.3 What the change stream is made of

| Triage label | Events | Share |
|--------------|-------:|------:|
| Editorial / formatting / whitespace | 148 | 38% |
| News or listing rotation | 107 | 28% |
| Real change, unrelated to IP (mostly omnibus bills amending broad Acts) | 67 | 17% |
| **Substantive** | **49** | **13%** |
| Scrape artefact | 18 | 5% |

About one change in eight is substantive, and fewer than ten in six months affected an IPFR page. The filters are being asked to find a very rare event in a stream that is 87% noise, so cleaning the stream (§6.2 items 4 and 5) is worth more than any threshold.

---

## 7. Stage 8 prompt experiment

The production prompt shows the LLM a unified diff with context lines, does not require it to quote the page, and never yields `UNCERTAIN` (0 of 1,828; confidence is 0.85 on every alert). A tightened prompt was tested: only removed/added text is shown (whitespace-normalised), `CHANGE_REQUIRED` must quote the IPFR sentence and the source text verbatim, a middle `REVIEW_SUGGESTED` verdict exists, and marketing, news rotation and error pages are named as `NO_CHANGE`.

On the labelled set (9 gold, 167 judged negatives):

| Configuration | Gold flagged | Wrong `CHANGE_REQUIRED` | Wrong `REVIEW_SUGGESTED` |
|---------------|-------------:|------------------------:|-------------------------:|
| Production: `gpt-4.1-mini`, current prompt (historical verdicts) | 5 / 9 | 44 / 167 | — |
| `gpt-4.1-mini`, tightened prompt | 4 / 9 | **3 / 167** | 24 / 167 |
| `gpt-5.4-mini`, tightened prompt | 5 / 9 | **1 / 167** | 4 / 167 |

- **The prompt fixes the false alarms on either model**: hard false alarms fall from 44 to 3 with no model change. The whitespace-only and error-page inputs correctly return `NO_CHANGE`.
- **The model fixes the misses.** `gpt-5.4-mini` flagged both IVDS pairs as `CHANGE_REQUIRED`. `gpt-4.1-mini` again returned `NO_CHANGE` for the official IVDS notice, and over-used the middle verdict.
- Neither caught the first Part 36 appearance.
- The in-code quote check changed nothing here, because the prompt had already removed the unquotable claims; it is still worth keeping as a guard.

Caveats: the negatives over-represent past false alarms, so 44/167 is not a production base rate. `gpt-5.4-mini` was one of the two reviewers that defined the negatives, which favours it. Seven `gpt-4.1-mini` responses were cut off by the 800-token output limit used in the test and are counted as not flagged. Treat this as strong support for the direction, not a measured precision figure.

---

## 8. Limits of this audit

- **Nine positives.** Every threshold above keeps all nine, but nine cannot fix a cut-point to two decimal places. Margins were chosen accordingly.
- **Model reviewers, not content owners.** The two reviewers agreed on 97% of pairs but split on six alerts. No human feedback has ever been recorded (`feedback.jsonl` does not exist).
- **Recall is only measured on changes Tripwire saw.** Sets C–E sample what the stages dropped; nothing here measures changes on the 38 unmonitored sources.
- **Set E is selected by bi-encoder score**, which favours the bi-encoder in §5.1–5.2.
- **Scores were replayed against today's corpus**, so pre-September events are indicative.
- **Costs are estimates** from third-party price lists.

---

## 9. Recommendations, in order

**A. Stop missing and stop mis-alerting (code)**

| # | Change | Evidence |
|---|--------|----------|
| 1 | Stage 7: do not create LLM bundles for graph-propagated pages | §5.5 — 49% of calls, 0 valid alerts |
| 2 | Treat RSS fetch failure and zero-item feeds as errors; replace the two RSS URLs with real feeds or re-type them as webpages | §6.2 item 2 |
| 3 | Reject browser/CDN error pages in `validation.py`; let a source re-baseline after N consecutive size-check failures; purge the 17 poisoned baselines | §6.2 item 1 |
| 4 | Skip the page scrape and size check for FRL sources | §6.2 item 3 |
| 5 | Normalise whitespace before hashing and diffing; drop moved-but-identical lines; stop at Stage 3 when the diff is empty | §6.2 items 4–5 |
| 6 | Stage 8: show only changed text, require verbatim quotes and verify in code that the quote exists on the page, add a "review suggested" verdict; move to a stronger model (`gpt-5.4-mini` class) | §3, §7 — hard false alarms 44 → 3 from the prompt; both IVDS misses recovered by the model |
| 7 | Suppress repeat alerts for the same page and source until the page or the source text changes again | §3 — 8 pages = 23 alerts |

**B. Thresholds (config, after A1 and A5)**

| # | Setting | Now | Proposed | Evidence |
|---|---------|-----|----------|----------|
| 8 | Candidate selection | Stage 4 top 5 → Stage 5 | **Revised in §10:** top 10 by bi-encoder max-chunk over all pages, plus any page sharing a specific term with a changed line | §5.2, §10.3 |
| 9 | `biencoder.max_chunk_floor` | 0.63 | **Revised in §10:** keep 0.63 for webpages; never apply it to legislation sources | §10.2, §10.3 |
| 10 | `biencoder.high_threshold`, `low_medium_*` | 0.75 / 0.45 / 3 | Retire | §5.3 |
| 11 | `crossencoder.threshold` | 0.60 on blended score | 0.75 on raw score, or remove Stage 6 | §5.4 |
| 12 | `relevance_scoring.top_n_candidates` (only if #8 is not done) | 5 | 25 | §5.6 |

**C. Coverage and operations**

| # | Change | Evidence |
|---|--------|----------|
| 13 | Set `SCRAPER_PROXY_URL` (open since July) and re-enable the 17 disabled sources | §6.1 |
| 14 | Review the 12 thin baselines: the scraper captures only the introduction of eight WIPO treaty pages | §6.1 |
| 15 | Report health as state changes (newly failing, recovered) plus one coverage figure, not a daily list | §6.2 item 7 |
| 16 | Alert when ingestion or any workflow fails two days running | §6 incident |
| 17 | De-duplicate `llm_assessments` (462 rows) | §6 |
| 18 | Strip the shared disclaimer paragraph at ingestion (97 of 130 pages) and rebuild embeddings and graph | §5.5 |
| 19 | Fix FRL explainer encoding and repeat triggers | §6.2 item 6 |

**D. Content fixes for the IPFR team now** — `XE324C` (IVDS), `X39AC5` (Part 62), `X0F896` (attorney fees), `X7E272` (tribunal name), and consider `XE69D4` (expedited UDRP).

**Expected effect of A + B (as revised in §10):** LLM calls still fall by roughly 40–50% (graph bundles −49%; whitespace, empty and error diffs removed; the wider candidate net adds some back), every confirmed miss in §4 and §10 is recovered at the candidate stage, and the alert classes that made up 31 of 39 false alarms are closed. Re-run this audit's labelled set after the changes, and keep `audit_labels_2026-11-11.csv` as the regression set for tasks 5.3 and 5.4.

---

## 10. Addendum — dedicated false-negative hunt

Brief: spend the remaining budget looking only for things that were filtered out and should not have been, on the basis that false alarms are acceptable and misses are not.

### 10.1 Method

The first pass sampled each stage. This pass tried to cover everything Tripwire saw:

- Every changed line from every source over six months was de-duplicated (lines that only moved, whitespace changes and browser error text removed), leaving 2,801 units from 111 sources.
- Each unit was scored locally against every chunk of every IPFR page, and separately probed for exact matches of specific terms (amounts, acronyms, part and section numbers, emails, durations) that occur on eight or fewer IPFR pages.
- Any source/page pair with a line-level similarity ≥ 0.70 or a specific-term match was sent for review with the full page text: **1,146 pairs covering 50 sources and all 130 active pages**. The 480 strongest went to `gpt-5.4-mini`, the rest to `gemini-3.5-flash-lite` (both batch). The prompt told the reviewer that a miss is worse than a false alarm.
- All seven earlier confirmed positives were in the pool; both models flagged the firm ones.
- Stage 1 was tested separately: nine sources that are routinely skipped on HTTP headers alone were fetched live and compared with their stored baselines.
- Every flag was then checked by hand against the page text. Flags and my ruling on each are in `data/logs/audit_fn_hunt_2026-11-11.csv`.

Cost of this pass: OpenAI ≈ $0.77, Gemini ≈ $0.40. Running totals: **OpenAI ≈ $1.77, Gemini ≈ $1.85** of $2 each; I stopped there because the prices are estimates.

### 10.2 What it found

52 of 1,146 pairs were flagged. After checking: 5 confirmed, 8 worth a look, 39 rejected.

**New confirmed misses**

| # | What changed | IPFR page | Where it was lost |
|---|--------------|-----------|-------------------|
| 1 | Federal Circuit and Family Court fee for mediation by a court officer: $665 → **$690** on 1 July 2026 | *Resolve IP issues with the help of a third party* (`X5B5CC`) says "**$650** for each attendance" — already one increase behind | **Stage 4.** Bi-encoder rank 4 of 132 (0.754), Stage 4 semantic rank 70. Never a candidate on any of three fee-page changes, while four pages that quote no court fees were alerted from the same diff |
| 2 | **Trade Marks Act 1995 (1 Oct) and Patents Act 1990 (15 Oct) amended** by the Regulatory Reform Omnibus Act 2026 | See below | **Stage 5.** The "change text" was a one-paragraph bill summary ("Amends 26 Acts to simplify and streamline…"). Best page score 0.623 and 0.615, under the 0.63 floor. All candidates rejected; nothing reached the LLM or the digest |
| 3 | Eight legislation changes arrived with **no explainer at all** (API timeout or no bill summary), including the Trade Marks Regulations and Patents Regulations | — | **Stage 3.** The LLM was shown a ~110-character stub and returned `NO_CHANGE`. Six were explained on a later run; two never were: the ASIC Act amendment by the *Treasury Laws Amendment (Business Registries Stabilisation and Uplift) Act 2026* (IPFR has at least six pages on business names), and a Competition and Consumer Act change on 12 October |

On item 2: IP Australia's own article says the Act brings a PBR renewal-fee grace period and surrender process (from 18 September 2027), Nice Classification references that update automatically (from 18 March 2027), more flexible costs awards in trade mark oppositions, and closes an attorney-discipline loophole (both subject to further regulations). No IPFR statement is wrong today, but *Challenge the registration of a new trade mark* (`XC6CD2`, "the hearing officer has the power to award costs") and the PBR pages will need to follow it. The point is the mechanism: **an amendment to the two most important Acts on the watch list was dropped without a trace**, and raising the floor to 0.68 as §5.3 proposed would have made that more likely, not less.

**Earlier misses, now with the full timeline**

- **Part 62** (`X39AC5`): the Trade Marks Manual dropped "Part 62 Revocation of Registration" from its chapter list on **23 July** — the page ranked 14th by bi-encoder that day and was not a candidate. It then reached the LLM five times (30 July to 29 October) and an official notice on 22 October said the guidance had been consolidated; all returned `NO_CHANGE`. First alert: 5 November, fifteen weeks after the first signal.
- **IVDS** (`XE324C`): confirmed again from both the official notice and the PBR manual. Still not alerted.

**Worth a look** (not proven; listed because false alarms are acceptable)

- `XA1EBE` *What is it?* (patent re-examination, "costs $800") and `X611E8` (fees via "Schedule 8 of Patents Regulation 1991"): IP Australia updated its patent fee-payment instruments on 7 August 2026. The notice does not say an amount changed.
- `X02CB3`, `X76958`: the Copyright (International Protection) Regulations were amended to update which countries are protected; the pages describe that protection in general terms.
- `XD0E48`, `X2A3EB`: IP Australia added a note that Amazon Brand Registry is a private service it has no part in; Amazon removed its public infringement-report form (8 October).
- `X2A3EB`: says to file an eBay VeRO counter notice "via email"; eBay's address changed. Already alerted in July.
- `XBA31C`: WIPO now takes payment only through WIPO Pay; the page quotes WIPO arbitration costs.

**Rejected after checking** — 7 flags that IPTA had withdrawn its free consultation (the "removal" was a browser error page; I fetched the live page and the service is unchanged), 2 where the model quoted text that is not on the page, and 30 that were topical overlap only.

**Stage 1 is clean on the evidence available.** The nine header-skipped sources (four Shein pages share one ETag; two TTIPAB pages share one content length) all matched their baselines when fetched live. Two side findings: `ttiap_find_an_attorney` is the same URL as `how_to_engage_an_attorney` plus a `#fragment`, so it monitors nothing new; and `icann_whois_lookup`'s baseline is an error page.

### 10.3 Where confirmed misses were lost, and what that changes

| Stage | Confirmed misses (both passes) |
|-------|-------------------------------|
| Never fetched | Unknown — 38 sources (§6.1) |
| Stage 3: no explainer text | 8 legislation changes, 2 never explained |
| Stage 4: top-five shortlist | `XE324C` (PBR manual), `X5B5CC` (court fee, three times), `X39AC5` (23 July), `X6F04B` (minor) |
| Stage 5: 0.63 floor | Trade Marks Act and Patents Act amendments |
| Stage 6 | None |
| Stage 8: LLM said `NO_CHANGE` | `XE324C` (official notice), `X39AC5` (six times) |

Across both passes the confirmed positives rank 1, 1, 1, 1, 1, 2, 2, 2, 3, **4, 5 and 14** by bi-encoder score over all pages, with scores from 0.733 to 0.94. That is enough to revise three earlier recommendations:

| Earlier recommendation | Revised |
|------------------------|---------|
| Top 5 (or top 3) by bi-encoder | **Top 10**, floor unchanged. Top 3 would have lost the court fee; top 5 keeps it by one place. Roughly 4–5 candidates per change instead of 3 |
| Raise `max_chunk_floor` to 0.68 | **Leave it at 0.63.** Nothing confirmed sits below 0.73, but the saving is a few dollars a year and the downside is a miss |
| (new) | **Add an exact-term path:** any page that shares a rare specific term with a changed line becomes a candidate regardless of score. This alone catches IVDS and the 23 July Part 62 signal (rank 14) |
| (new) | **Legislation never passes through the semantic gates.** A detected change to an Act or Regulation at importance ≥ 0.7 goes into the digest as its own line — title, amending instrument, link — whether or not any page matches. This is what the disabled `fast_pass` setting was for |
| (new) | **No explainer = escalate, not "no change".** Report "legislation changed, detail unavailable" and retry the explainer on later runs |
| Stage 8 model and prompt (rec. 6) | Unchanged, and now the highest-value fix for misses: the two worst (IVDS, Part 62) reached the LLM as the top-ranked page and were waved through |

The false-alarm fixes in §9 part A are unaffected: none of them removes a real signal.

### 10.4 What this still cannot tell you

- **Changes on the 38 sources that are not being fetched.** There is no history to review. The useful next step is a one-off check of the *current* text of every source against the IPFR pages that cite it; this environment could reach every site I tried, so it is feasible, but it needs a fresh budget.
- **Detail behind headlines.** News and notice pages are captured as a list of titles. The omnibus reforms were visible only as "New reforms to streamline Australia's IP system"; the content was one click away. Following links from new items on the IP Australia news and official-notice pages would close this.
- **Thin captures.** Twelve sources store under 1,000 characters, so most of each page is invisible.
- The review was done by models and checked by me, not by a content owner; pairs below the 0.70 line-similarity cut with no exact-term match (about 7,700) were not reviewed.

**IPFR pages to fix now, updated:** `XE324C` (IVDS), `X39AC5` (Part 62), `X5B5CC` (court mediation fee $650 → $690), `X0F896` (attorney hourly rate), `X7E272` (tribunal name).
