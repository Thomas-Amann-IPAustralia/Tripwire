# Tripwire Stage-Gate Efficacy Audit

**Author:** Automated pipeline audit
**Date:** 2026-07-21
**Data window:** 2026-05-07 → 2026-07-20 (runs `2026-05-07-317` … `2026-07-20-396`)
**Primary sources:** `pipeline_runs` (11,388 source-runs across 73 pipeline runs), `llm_assessments` (786 rows), `graph_edges`, `source_registry.csv`, and the per-run `config_snapshot` embedded in `pipeline_runs.details`.

---

## 1. Executive summary

The pipeline is running and producing alerts, but the **stage gates are not doing the job they were designed for**. Precision is enforced almost entirely by the Stage 8 LLM, not by the cheap upstream gates that are supposed to protect it.

Headline findings, all evidenced below:

1. **Stage 6 (cross-encoder) is a no-op gate.** It confirmed **611 of 611** candidate pages (100%) and **rejected zero** in **131 of 131** runs. The `threshold: 0.60` setting has never once excluded a candidate.
2. **Stage 4 (relevance) is not a gate either.** `min_score_threshold` is `null`, so it always forwards its top-5 candidates. Its fused RRF scores are compressed into a 0.023–0.044 band and carry almost no discriminating signal.
3. **Stage 5 (bi-encoder) is the only semantic gate that actually filters** — it removed 84 of 215 source-runs (39%). Everything downstream of Stage 5 is pass-through.
4. **All precision has been offloaded to the LLM, which rejects 96.9% of what it receives.** Of 786 LLM calls, only **24 (3.1%)** returned `CHANGE_REQUIRED`. 12 of 23 scored runs produced **zero** useful alerts.
5. **17.2% of the LLM's rejections were triggered by scrape/error artefacts** — degraded or error-page content that passed validation, produced a spurious diff, and propagated all the way to the LLM.
6. **Scrape reliability is poor:** 2,070 fetch errors = **58% of all due source-checks**. **17 sources fail on 100% of attempts** and produce nothing but wasted budget.
7. **A calibration blocker exists:** the bi-encoder and cross-encoder raw scores are **never persisted**. `pipeline_runs` stores only pass/fail counts, and `observability.py`'s Stage 5/6 score-distribution table is therefore permanently empty. **We cannot pick correct thresholds for Stages 5–6 from the data we currently keep.**

The net effect: the system works as a "LLM-decides-everything" pipeline. It is functional but architecturally fragile and inefficient — 786 LLM invocations and 131 full bi-/cross-encoder passes to surface 24 real signals.

---

## 2. The end-to-end funnel (2026-05-07 → 2026-07-20)

Every source-run is logged exactly once at the stage it stopped. The counts below reconcile exactly to the 11,388 total.

| Step | Count | Notes |
|------|------:|-------|
| Source-runs logged | 11,388 | 73 runs × 156 sources |
| — Not due (schedule skip) | 7,845 | 68.9% — never checked this run |
| **Due checks** | **3,543** | |
| — Scrape/fetch **error** | 2,070 | **58.4% of due checks**, 18.2% of all source-runs |
| — Clean probe, unchanged | 1,258 | 746 `unchanged` + 512 Stage-2 `no_change` |
| **Reached Stage 4 (relevance)** | **215** | 129 webpage `significant` + 86 RSS/FRL (bypass Stage 2) |
| **Passed Stage 5 (bi-encoder)** | **131** | 84 filtered out (39%) — *the only real gate* |
| Candidate pages into Stage 6 | 611 | ~4.7 pages per surviving source-run |
| **Confirmed by Stage 6 (cross-encoder)** | **611** | **100% — zero rejected** |
| Source-level triggers (`stage6_complete`) | 131 | |
| **LLM calls (Stage 8)** | **786** | Stage 7 *expanded* 611 → 786 (+29%) |
| **`CHANGE_REQUIRED` verdicts** | **24** | **3.1% precision** |
| `NO_CHANGE` verdicts | 762 | 96.9% |

**Reading the funnel:** the three cheap/mid gates (Stages 1–2 scheduling/hash, Stage 4) discard the bulk of traffic on volume and "did-anything-change" logic. But once a change is real, the *semantic relevance* gates that are supposed to decide "does this change matter to an IPFR page" (Stages 4 and 6) let everything through, and Stage 5 is the sole filter. The LLM then absorbs the entire false-positive load.

---

## 3. Stage-by-stage findings

### Stage 1 — Metadata probe & scrape reliability

Decision distribution across all 11,388 source-runs:

| `metadata_probe.decision` | Count |
|---------------------------|------:|
| `not_due` (scheduler) | 7,845 |
| `unknown` (fetch failed) | 2,298 |
| `unchanged` | 746 |
| `changed` | 499 |

**Reliability is the biggest single source of waste.** Of the 3,543 due checks, 2,070 (58%) ended in a scrape error (`outcome='error'`), across 32 distinct sources. Error types: 1,886 `RetryableError`, 184 `PermanentError`.

**17 sources failed on 100% of ≥10 attempts** (73/73 each in most cases):
`federal_court_*` (7 sources), `wipo_lex_australia`, `wipo_arbitration_mediation_center_news`, `aph_ip_committee`, `au_dispute_resolution_policy_audrp`, `auda_consultations_page`, `intellectual_property_general_federal_law`, `intellectual_property_how_to_import`, `how_to_use_the_au_whois_tool`, `report_intellectual_property_infringement_etsy`, `shein_copyright_notice`.

These are dead weight: they consume the retry budget, generate `RetryableError` noise, and never contribute a signal. The dominant error message is `All fetch attempts failed …` on `*.gov.au` and `wipo.int` hosts — consistent with WAF IP-blocking of the GitHub Actions runner. `SCRAPER_PROXY_URL` is unset, so the proxy fallback in `scraping.proxy.fallback_on_block` never activates.

### Stage 2 — Change detection & significance fingerprint

Of 129 webpage changes that reached Stage 2's significance tagger:

| `significance` | Count | Share |
|----------------|------:|------:|
| `high` | 109 | 84% |
| `standard` | 20 | 16% |

**The significance fingerprint does not discriminate**, and it is not used as a gate anyway. `stage2_change_detection.py` tags a change `high` if *any* fingerprint category matches, and the `defined_terms` pattern (`[A-Z][a-zA-Z]+(?: [A-Z][a-zA-Z]+){1,5}`) matches essentially any capitalised phrase — so almost every real change becomes `high`. Both `high` and `standard` proceed identically (`should_proceed` is true for both). This tag is advisory metadata that adds cost without changing behaviour.

### Stage 4 — Relevance (YAKE-BM25 + bi-encoder RRF)

Reached by 215 source-runs. Three problems, all evidenced:

**(a) It is not a gate.** `relevance_scoring.min_score_threshold` is `null`, so selection is "top-5 always." All 215 forwarded exactly 5 candidates; none were filtered on score.

**(b) The RRF score has almost no dynamic range.** Top-candidate `final_score` distribution (N=215):

| min | p25 | median | p75 | p95 | max |
|----:|----:|-------:|----:|----:|----:|
| 0.0235 | 0.0295 | 0.0295 | 0.0404 | 0.0433 | 0.0443 |

This matches the formula exactly: `final_score = RRF × (floor + (1-floor)·importance)`. With `rrf_k=60`, `w_sem=2.0`, a rank-1 page maxes at `3/61 ≈ 0.0492`; the `source_importance_floor=0.5` multiplier (range 0.5–0.9 given max importance 0.8) then compresses it to 0.0246–0.0443. The **median is pinned at 0.0295**, the value produced by low-content/failed RSS candidates — i.e. the "typical" score is a floor artefact, not a signal.

**(c) The fast-pass is dead config.** `fast_pass_triggered` was **False in all 215 runs**. The condition is `source_importance ≥ 1.0`, but the **maximum importance in `source_registry.csv` is 0.8** (distribution: 0.8×28, 0.7×7, 0.6×36, 0.5×2, 0.4×4, 0.2×67, 0.0×12). No source can ever satisfy it.

**(d) The importance floor flattens the importance signal.** `source_importance_floor=0.5` maps importance 0.2→0.6× and 0.8→0.9× — a 4× importance spread collapses to a 1.5× score spread. 83 of 156 sources (53%) sit below the 0.5 floor and are all boosted to the same effective band.

### Stage 5 — Bi-encoder (the only working gate)

215 in → **131 passed, 84 filtered (39%)**. Pass behaviour is nearly binary:

| `candidates_out` | Runs |
|-----------------:|-----:|
| 0 | 84 |
| 3 | 7 |
| 4 | 30 |
| 5 | 94 |

When a change document is broadly on-topic, 4–5 of the 5 candidates clear; when off-topic, 0 clear. This is the gate carrying the whole semantic load. It is doing real work, but its coarse "all-or-nothing" behaviour suggests the decision is dominated by whole-document topicality rather than per-page precision — the `high_threshold: 0.75` / `low_medium_threshold: 0.45` split cannot be verified because the scores are not persisted (see §4).

### Stage 6 — Cross-encoder (inert)

This is the most important finding. **In 131 of 131 runs the cross-encoder confirmed every candidate it received: 611 of 611 pages, 0 rejected.** `graph_propagated` was 0 in every run.

The `threshold: 0.60` has never excluded anything. The reranked score (`0.8·CE + 0.2·lexical`) apparently sits above 0.60 for every Stage-5 survivor. Whether this is because the threshold is too low for this reranker's output range, or because Stage 5 already removes anything the cross-encoder would reject, **cannot be determined from persisted data** — but either way, the gate is contributing **zero filtering** while consuming the most expensive model pass in the pipeline before the LLM.

The graph is populated (965 edges: 655 embedding-similarity, 242 entity-overlap, 68 internal-link), yet propagation produced no confirmed pages. With `decay_per_hop=0.45` and a `propagation_threshold=0.05` floor, a propagated signal cannot realistically reach the 0.60 confirmation bar, so graph propagation is inert as a *trigger* mechanism.

### Stage 7 — Aggregation (expanding, not consolidating)

Stage 7 exists to **group** multiple source-triggers for the same IPFR page into a single LLM call. In practice it did the opposite: **611 confirmed source→page pairs became 786 LLM calls (+175, +29%).** The expansion comes from `stage7_aggregation.py` adding `graph_propagated_to` neighbours as *additional* trigger entries/bundles. Given the 96.9% downstream rejection rate, these ~175 extra calls are almost entirely wasted.

### Stage 8 — LLM assessment (absorbing all the noise)

| Metric | Value |
|--------|------:|
| Total calls | 786 |
| `CHANGE_REQUIRED` | 24 (3.1%) |
| `NO_CHANGE` | 762 (96.9%) |
| Input tokens | 3,271,775 |
| Output tokens | 114,537 |
| Approx spend (`gpt-4.1-mini` @ $0.40 / $1.60 per 1M) | ~$1.49 |
| Spend on `NO_CHANGE` | ~$1.42 (95%) |
| Retries | 0 across all 786 |

Two structural problems:

- **17.2% of `NO_CHANGE` verdicts (131 calls) cite technical/error content** in their reasoning (e.g. "technical issue with the … website (connection closed error)"). Degraded scrapes are passing `validation.py`, producing a large spurious diff against the last good snapshot, and riding the pass-through gates into the LLM.
- **LLM confidence does not separate the verdicts** and cannot be used as a secondary filter: `NO_CHANGE` mean confidence 0.867 vs `CHANGE_REQUIRED` 0.850 (confidence is effectively quantised at 0.85/0.95).

The precision problem is **stable, not transient**: per-run precision was 0% in 12 of 23 scored runs and never exceeded 10.7%.

**Where the real signals come from.** The 24 `CHANGE_REQUIRED` hits (20 distinct IPFR pages) clustered on a few high-value sources: WIPO ADR / arbitration fees, the Trans-Tasman IP Attorneys Board (TTIPAB) complaints pages, Amazon Brand Registry, and IP Australia hearings. Meanwhile the four IP Australia manuals (`tm_manual`, `patent_manual`, `pbr_manual`, `design_manual`) generated **197 of 611 confirmed pages (32%)** but almost no `CHANGE_REQUIRED`. Trigger volume and signal value are badly correlated.

---

## 4. The instrumentation gap (blocks Stage 5–6 calibration)

`pipeline.py` persists only **counts** for the semantic stages:

- `details.stages.biencoder` → `{candidates_in, candidates_out}`
- `details.stages.crossencoder` → `{candidates_in, confirmed, graph_propagated}`
- `pipeline_runs.triggered_pages` → a bare list of page-ID strings.

The per-page `biencoder_max_chunk_score` and `crossencoder_final_score` are computed (they exist on the in-memory `SourceTriggerRecord`) but are **thrown away** before the DB write. Consequently `observability.py._section_score_distributions` — which looks for those fields inside `triggered_pages` dicts — finds nothing and renders the Stage 5/6 rows as empty (N=0) on every report.

**This is why no correct threshold can be recommended for Stages 5 and 6 yet.** We can prove the gates are mis-set (Stage 6 rejects nothing), but we cannot see the score distribution needed to choose the right cut-point. Fixing this is prerequisite #1 for the deferred calibration tasks (5.3/5.4).

---

## 5. Recommendations

Each recommendation is tagged with its evidence and a confidence level. They are split into **Tier 1 — ship now** (airtight evidence, safe direction) and **Tier 2 — instrument then calibrate** (the change is known to be needed, but the exact value requires data we are not yet keeping).

### Tier 1 — ship-now config changes

| # | Setting | Current | Proposed | Evidence | Expected effect |
|---|---------|---------|----------|----------|-----------------|
| 1 | **Persist semantic scores** — add `biencoder_max_chunk_score` and `crossencoder_final_score` to `triggered_pages` (and log rejected-candidate scores). Add `pipeline.persist_stage_scores: true`. | scores discarded | persist per page | §4: observability Stage 5/6 table is always empty; scores exist in memory but are dropped | Unlocks Stage 5/6 calibration (Tasks 5.3/5.4); makes the weekly report meaningful |
| 2 | `relevance_scoring.fast_pass.source_importance_min` | `1.0` | `null` (explicitly disabled) *or* `0.8` if guaranteed top-source alerts are wanted | §3 Stage 4(c): max importance in registry is 0.8; fast-pass fired 0/215 times — the value is unreachable and misleading | Removes dead/misleading config; forces an explicit decision on fast-pass |
| 3 | `source_registry.csv` — quarantine the 17 always-failing sources (set `check_frequency: monthly` or disable) | mixed | quarantine / disable | §3 Stage 1: 17 sources at 100% failure on ≥10 attempts; 2,070 errors = 58% of due checks | Cuts the fetch-error rate roughly in half; frees retry budget |
| 4 | `SCRAPER_PROXY_URL` (env/secret) + keep `scraping.proxy.fallback_on_block: true` | unset | set a residential proxy | §3 Stage 1: dominant error is WAF blocking of GHA IPs on `*.gov.au`/`wipo.int`; fallback never activates while unset | Recovers a large share of `RetryableError` sources without editing code |
| 5 | `source_importance_floor` | `0.5` | `0.3` | §3 Stage 4(d): 0.5 floor compresses a 4× importance spread to 1.5× and boosts 53% of sources into one band | Restores importance discrimination ahead of enabling a Stage-4 score gate (rec #7) |

### Tier 2 — instrument, observe ~2–4 weeks, then calibrate

| # | Setting | Current | Direction | Evidence | Why not a number yet |
|---|---------|---------|-----------|----------|----------------------|
| 6 | `semantic_scoring.crossencoder.threshold` | `0.60` | **raise** | §3 Stage 6: 611/611 confirmed, 0 rejected in 131/131 runs — the gate filters nothing | The 24 true positives passed the *same* gate; raising blindly risks dropping them. Persist CE scores (rec #1), then set the threshold at the point that best separates `CHANGE_REQUIRED` from `NO_CHANGE`. |
| 7 | `relevance_scoring.min_score_threshold` | `null` | **set a floor** (start ≈ `0.030`, then calibrate) | §3 Stage 4(a/b): top-5 always forwarded; failed-RSS candidates sit at the ~0.0295 median floor | Interim `0.030` would drop degenerate/empty candidates; the precise cut needs the RRF-vs-verdict comparison in Task 5.4 |
| 8 | `semantic_scoring.biencoder.{high_threshold, low_medium_threshold, low_medium_min_chunks}` | `0.75 / 0.45 / 3` | review after instrumenting | §3 Stage 5: all-or-nothing pass pattern (0 or 4–5) suggests coarse behaviour | Cannot see max-chunk-score distribution until rec #1 lands |
| 9 | `graph.*` propagation into Stage 7 | enabled | **gate or disable pending value** | §3 Stage 6/7: `graph_propagated=0` at Stage 6, yet Stage 7 expands 611→786 calls via `graph_propagated_to` | Confirm the expansion mechanism with persisted data, then either fix Stage 7 dedup or set `graph.enabled: false` for triggering until it demonstrably adds true positives |

### Tier 2 — code + config (reduces the 17% error-artefact load)

| # | Change | Evidence | Effect |
|---|--------|----------|--------|
| 10 | Move `validation.py` thresholds into config (`scraping.validation.min_content_length`, `size_change_min_ratio`, `size_change_max_ratio`) and **tighten the size band** from `[0.30, 3.00]` toward e.g. `[0.60, 1.75]`; add common WAF/error phrases to the CAPTCHA/blocklist | §3 Stage 8: 17.2% of `NO_CHANGE` (131 calls) were error-page artefacts that passed validation and produced spurious diffs | Stops degraded scrapes from becoming "significant changes"; removes a large slice of wasted LLM calls |
| 11 | Treat Stage-2 `significance` as a real signal (e.g. `standard`-only changes from low-importance sources deprioritised) **or** document it as advisory | §3 Stage 2: 84% of changes tagged `high`; tag never alters flow | Either makes the tag useful or removes a misleading cost |

### Re-running observation mode

Once rec #1 (score persistence) is in place, run **`pipeline.observation_mode: true` for 2–4 weeks**. Stages 1–7 will run and log the now-persisted score distributions with **no LLM cost**, giving the clean dataset needed to set the Stage 4/5/6 thresholds in Tier 2 on evidence rather than guesswork. This is exactly the calibration window the deferred Tasks 5.3/5.4 assume.

---

## 6. Prioritised roadmap

1. **Reliability first (recs #3, #4).** Half of all due checks fail; fix the input before tuning the gates. *Config/registry only.*
2. **Instrument (rec #1).** Persist bi-/cross-encoder scores so the gates become observable. *Small code change.*
3. **Stop error artefacts (rec #10).** Tighten validation to cut the 17% error-driven LLM calls.
4. **Clean up dead config (recs #2, #5, #11).** Fast-pass, importance floor, significance tag.
5. **Observe & calibrate (recs #6, #7, #8, #9).** Run observation mode, then set the semantic thresholds on real distributions.

---

## 7. Appendix — ready-to-apply Tier-1 `tripwire_config.yaml` diff

```yaml
# --- Stage 4: Relevance scoring ---
relevance_scoring:
  min_score_threshold: null          # Tier-2: set ~0.030 after instrumenting (rec #7)
  source_importance_floor: 0.3       # was 0.5 — restore importance discrimination (rec #5)
  fast_pass:
    source_importance_min: null      # was 1.0 — unreachable (max registry importance is 0.8); disable explicitly (rec #2)

# --- Instrumentation (rec #1) ---
pipeline:
  persist_stage_scores: true         # write biencoder_max_chunk_score / crossencoder_final_score per triggered page

# --- Scraping validation (rec #10, requires validation.py to read these) ---
scraping:
  validation:
    min_content_length: 200
    size_change_min_ratio: 0.60      # was hard-coded 0.30 — tighten to reject error pages
    size_change_max_ratio: 1.75      # was hard-coded 3.00
```

> `SCRAPER_PROXY_URL` (rec #4) is a GitHub Actions secret / environment variable, not part of `tripwire_config.yaml`. The 17 always-failing sources (rec #3) are quarantined in `data/influencer_sources/source_registry.csv`.

### Evidence provenance

All figures were computed directly from `data/ipfr_corpus/ipfr.sqlite` over the window 2026-05-07 → 2026-07-20: `pipeline_runs` (funnel, stage decisions, scrape errors, per-stage counts), `llm_assessments` (verdicts, tokens, confidence, reasoning), `graph_edges` (edge population), and `source_registry.csv` (importance distribution). No numbers in this report are estimated except the LLM dollar figure, which uses published `gpt-4.1-mini` per-token pricing.
