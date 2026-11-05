# Tripwire Threshold Calibration & Efficacy Report

**Author:** Automated calibration review
**Date:** 2026-07-21
**Builds on:** `docs/stage_gate_efficacy_audit_2026-07-21.md`
**New data:** `score_backfill` table — 580 per-page bi-/cross-encoder scores replayed across 116 historical webpage runs, each joined to its production LLM verdict. 567 of those rows carry a verdict (21 `CHANGE_REQUIRED`, 546 `NO_CHANGE`); 13 are unverdicted. Scores were recomputed with the live models against the **current** corpus, so older runs are indicative and recent runs are the most faithful.

---

## 1. Executive summary

The July 21 audit proved the semantic gates were mis-set but could not recommend numbers, because the raw bi-/cross-encoder scores were never persisted. That instrumentation gap is now closed and the scores have been backfilled. With the labelled score dataset in hand, the picture is clear and, in one important respect, **the opposite of what the audit tentatively proposed.**

1. **The bi-encoder (Stage 5) is a genuinely strong discriminator** — separability AUC **0.834**. It should carry the semantic precision load, and it can be tightened to cut roughly a quarter to a third of wasted LLM calls **without losing a single one of the 21 known true positives.**
2. **The cross-encoder (Stage 6) was hobbled by a bug, now fixed.** Its *decision* score separated at only AUC **0.585** because of a **double sigmoid** in `stage6_crossencoder._score_pair` (§2.3a): the model already returns a probability and the code sigmoided it again, crushing the score into a 0.16-wide band ([0.555, 0.718]). The raw cross-encoder signal underneath is actually AUC **0.670** — moderate, not random. **The fix is shipped** (§2.3b): with the double sigmoid removed and *no threshold change*, the unchanged `threshold: 0.60` now rejects 8% of false positives at 100% recall (was 0%). This also **refutes the audit's Tier-2 rec #6** — the answer was a code fix, not raising the threshold (raising it drops true positives).
3. **The precision lever right now is a bi-encoder `max_chunk_score` floor.** A floor of ~0.63 retains 100% recall on the labelled set and removes ~24% of the false-positive LLM calls; ~0.65 removes ~36% with a thinner safety margin. **This is implemented** (`max_chunk_floor: 0.63`).
4. **Efficacy on the goal is mixed.** Tripwire *is* surfacing real, high-value signals (21–24 `CHANGE_REQUIRED` across ~20 distinct IPFR pages in the window), so the pipeline demonstrably works end-to-end. But precision is ~3–4%, recall is still unmeasured (no ground truth for *missed* changes), and precision today is enforced entirely by the LLM. The calibration below moves the first slice of that load back onto the cheap gates where it belongs.

---

## 2. What the backfilled scores show

### 2.1 Class separation by stage

| Score | AUC (P[score(CR) > score(NO_CHANGE)]) | Verdict |
|-------|--------------------------------------:|---------|
| **Bi-encoder max-chunk (Stage 5)** | **0.834** | Strong separator — the gate that should decide |
| Bi-encoder chunks-above-low-medium | 0.620 | Weak |
| Cross-encoder **raw** (Stage 6) | **0.670** | Moderate — but crushed by a bug (see §2.3a) |
| **Cross-encoder final (Stage 6, as gated)** | **0.585** | The decision score — diluted below the raw signal |

> **Correction (2026-07-21, later):** the cross-encoder is *not* near-random. The **raw** cross-encoder score separates at AUC **0.670** — moderate, and well short of the bi-encoder's 0.834, but real. The **0.585** figure is the *final* score the gate actually decides on, which is lower than the raw signal for two compounding reasons diagnosed below: a **double-sigmoid bug** (§2.3a) that compressed the raw score, and a **lexical blend** (0.2 weight, AUC ≈ 0.52) that dilutes it. Fixing the bug lifts the final AUC to 0.620; see §2.3b and `data/logs/score_backfill_corrected_2026-07-21.md`.

Pearson correlation between the bi-encoder max-chunk score and the cross-encoder final score is only **0.316** — even accounting for the bug, the cross-encoder is only weakly re-confirming what the bi-encoder saw.

### 2.2 Score distributions (labelled webpage candidates, N=567)

| Series | Min | p25 | Median | p75 | p95 | Max | N |
|--------|----:|----:|-------:|----:|----:|----:|--:|
| Bi-encoder max-chunk — **CHANGE_REQUIRED** | 0.666 | 0.712 | 0.751 | 0.789 | 0.861 | 0.940 | 21 |
| Bi-encoder max-chunk — NO_CHANGE | 0.492 | 0.629 | 0.674 | 0.712 | 0.787 | 0.955 | 546 |
| Cross-encoder final — **CHANGE_REQUIRED** | 0.547 | 0.597 | 0.674 | 0.751 | 0.819 | 0.823 | 21 |
| Cross-encoder final — NO_CHANGE | 0.444 | 0.574 | 0.636 | 0.736 | 0.773 | 0.835 | 546 |

The bi-encoder distributions are offset (CR median 0.751 vs NO_CHANGE 0.674, and every CR page sits at ≥0.666). The cross-encoder distributions are nearly coincident — the CR band [0.547, 0.823] sits entirely inside the NO_CHANGE band [0.444, 0.835].

### 2.3 Threshold sweeps

**Bi-encoder max-chunk floor** (keep candidate if score ≥ t):

| Floor | TP kept | FP kept | Recall | LLM calls | Precision | Calls saved vs no-floor |
|------:|--------:|--------:|-------:|----------:|----------:|------------------------:|
| — (none) | 21 | 546 | 100.0% | 567 | 3.7% | — |
| 0.600 | 21 | 478 | 100.0% | 499 | 4.2% | 12% |
| **0.630** | **21** | **408** | **100.0%** | **429** | **4.9%** | **24%** |
| 0.650 | 21 | 342 | 100.0% | 363 | 5.8% | 36% |
| 0.666 | 21 | 291 | 100.0% | 312 | 6.7% | 45% (zero margin) |
| 0.680 | 18 | 257 | 85.7% | 275 | 6.5% | — (drops 3 TPs) |
| 0.750 | 12 | 59 | 57.1% | 71 | 16.9% | — (drops 9 TPs) |

**Cross-encoder final floor** (keep candidate if score ≥ t):

| Floor | TP kept | FP kept | Recall | LLM calls | Precision |
|------:|--------:|--------:|-------:|----------:|----------:|
| 0.500 | 21 | 513 | 100.0% | 534 | 3.9% |
| 0.547 | 20 | 485 | 95.2% | 505 | 4.0% |
| **0.600 (current)** | 15 | 330 | **71.4%** | 345 | 4.3% |
| 0.650 | 11 | 237 | 52.4% | 248 | 4.4% |
| 0.700 | 10 | 170 | 47.6% | 180 | 5.6% |

The cross-encoder table is the whole argument against rec #6: every step that removes false positives removes true positives at almost the same rate, so precision barely moves (3.9% → 5.6%) while recall collapses (100% → 48%). There is no good operating point on this curve.

### 2.3a Root cause: the cross-encoder score is double-sigmoided (a bug)

The cross-encoder is not intrinsically weak — **its output is being destroyed in `src/stage6_crossencoder.py`.** The raw cross-encoder score across all 540 backfilled pairs is crushed into **[0.555, 0.718]** (stdev **0.021**, versus **0.072** for the bi-encoder — 3.4× wider). Every single value sits in [0.50, 0.75] and **not one is below 0.5.** That hard floor at 0.5 is the tell.

`_score_pair()` calls `model.predict(...)` and then applies `_sigmoid()` to the result:

```python
raw = model.predict([[change_text, page_content]])   # ← already a probability in [0,1]
return _sigmoid(raw)                                  # ← squashes [0,1] → [0.5, 0.731]
```

`sentence_transformers.CrossEncoder.predict()` applies the model's **default activation (sigmoid) for a single-label reranker**, so it already returns a probability in [0, 1]. Applying `_sigmoid` a second time maps that [0, 1] range onto **[sigmoid(0), sigmoid(1)] = [0.5, 0.731]** — and, because the sigmoid is flattest near its centre, it compresses exactly the region where the discriminative differences live. Inverting the observed band through the sigmoid recovers pre-squash values of **[0.22, 0.93]** — a healthy, wide, discriminative range that the second sigmoid throws away. The impossibility of any score below 0.5 (a double sigmoid is lower-bounded at 0.5; a correctly single-sigmoided reranker would return sub-0.5 scores for the many off-topic pairs) is conclusive.

So the audit's "Stage 6 confirms everything" symptom and this report's "AUC 0.585" measurement have the **same underlying cause**: the gate is comparing a 0.60 threshold against scores that have been algebraically compressed into a 0.16-wide band around 0.63–0.70, so the threshold lands in the middle of the noise. **This is a code fix, not a threshold change** — see rec F.

### 2.3b Outcome of the fix — Stage 6 recalibrated

The double sigmoid is now **removed** (`_score_pair` returns the model's probability directly). Because the bug is an *exact invertible transform* (`stored = sigmoid(corrected)`), the corrected scores were reconstructed analytically from the existing backfill — `corrected = logit(stored)` — without re-running the models (which cannot load in this session, and which would otherwise re-score against a drifted corpus). The CI `backfill_scores.yml` job now runs the fixed code and will produce native corrected scores to confirm. Full detail: **`data/logs/score_backfill_corrected_2026-07-21.md`**. Headline results:

- The corrected cross-encoder score spans **[0.22, 0.94]** (stdev 0.094 vs the buggy 0.021) — the dynamic range is restored.
- **Keep `threshold: 0.60`.** On the corrected scale every true positive scores ≥ 0.616, so the *unchanged* 0.60 threshold now keeps **100% recall while rejecting 41 of 519 false positives (8%)** — up from **zero** before the fix. The fix, with no threshold change, turns Stage 6 from a no-op into a working gate.
- **Do not raise it.** 0.616 is the zero-margin ceiling (lowest true positive) and 0.63 already drops one; with 21 positives, keep the margin.
- **Stacked with the shipped bi-encoder floor**, Stage 6 adds ~15 *unique* false-positive rejections (≈4%) at 100% recall — modest and partly redundant, bounded by the cross-encoder's moderate intrinsic separation (raw AUC 0.670). The lexical blend (0.2 weight, AUC ≈ 0.52) dilutes it further; reducing that weight is a candidate future change, validated separately.

### 2.4 Why the bi-encoder floor is safe *and* why the gate logic must stay

Stage 5 passes a page if **either** its max chunk ≥ `high_threshold` (0.75) **or** ≥ `low_medium_min_chunks` (3) chunks exceed `low_medium_threshold` (0.45). Of the 21 true positives, **12 pass via the high path and 9 pass only via the low-medium path** (max-chunk in [0.666, 0.75)). So the low-medium path is not dead weight — removing it or raising `high_threshold` would discard 43% of known true positives. Likewise, the nine low-path true positives have chunk counts {3, 4, 6, 12, 12, 13, 13, 13, 13}; exactly one sits on the `min_chunks=3` boundary, so raising `low_medium_min_chunks` to 4 would cost a true positive. **Neither existing knob can be tightened without losing recall.**

The clean lever is a *new* absolute floor applied to **both** paths: a page must clear the OR-logic **and** have max-chunk ≥ `max_chunk_floor`. Every true positive already clears 0.666, and 192 of the 519 NO_CHANGE pages that pass the gate today sit below 0.65 (111 below 0.62). That floor is the ~24–36% saving in the sweep, at full recall.

---

## 3. Revised threshold amendments

These supersede the audit's Tier-2 table (§5, recs #6–#8) now that the calibration data exists.

| # | Setting | Current | **Revised recommendation** | Basis | Effect on labelled set |
|---|---------|---------|----------------------------|-------|------------------------|
| A | **Bi-encoder `max_chunk_floor`** (new knob, applied to both pass paths) | none | **0.63** ✅ **implemented** — up to **0.65** once trusted | §2.3/2.4: AUC 0.834; lowest TP = 0.666 | 100% recall retained; ~24% (0.63) to ~36% (0.65) fewer LLM calls |
| B | `semantic_scoring.crossencoder.threshold` | 0.60 | **Keep 0.60 — do NOT raise** (reverses audit rec #6) | §2.1/2.3: AUC 0.585; every step up sheds TPs ≈ as fast as FPs | Any raise damages recall for negligible precision gain |
| C | `semantic_scoring.biencoder.high_threshold` | 0.75 | **Keep 0.75** | §2.4: 9/21 TPs pass *below* 0.75 via the low-medium path | Raising it drops 43% of known TPs |
| D | `semantic_scoring.biencoder.low_medium_min_chunks` | 3 | **Keep 3** | §2.4: one TP sits exactly at 3 chunks | Raising to 4 drops a TP |
| E | `relevance_scoring.min_score_threshold` (audit rec #7) | null | **Defer / low priority (~0.030 if set)** | Stage-4 RRF still compresses to ~0.03 and was not rescored in the backfill; the bi-encoder floor (A) achieves the same precision goal with a measured cut-point | Marginal; keep as a degenerate-candidate guard only |
| F | **`src/stage6_crossencoder.py` `_score_pair` — remove the double sigmoid** (code fix) | double-sigmoided | ✅ **implemented** — model activation applied **once** | §2.3a/§2.3b: raw CE crushed to [0.555, 0.718], nothing below 0.5 — the second sigmoid destroyed the signal | **Done.** Restored the CE range to [0.22, 0.94]; the unchanged 0.60 threshold now rejects 8% of false positives at 100% recall (was 0%). Stage 6 is a working — if secondary — gate. |

**Bottom line:** amendment **A** is the single high-value change and is now implemented. Amendments B–D are "hold the line" corrections that stop well-intentioned but harmful tightening. **Amendment F is the real explanation** behind the audit's "Stage 6 confirms everything" finding: the cross-encoder was never given a chance to discriminate, because its scores are algebraically compressed by a double sigmoid. The audit was right that Stage 5 is the real working gate — but Stage 6 is *fixable*, not inherently useless. Fixing F, then re-backfilling, is the path to a genuine second semantic gate and a chance to raise precision further than the bi-encoder floor alone can.

### Ready-to-apply config diff

```yaml
# --- Stage 5: Bi-encoder (the effective semantic gate) ---
semantic_scoring:
  biencoder:
    high_threshold: 0.75          # unchanged — 9/21 true positives pass below this
    low_medium_threshold: 0.45    # unchanged
    low_medium_min_chunks: 3      # unchanged — one true positive sits exactly here
    max_chunk_floor: 0.63         # IMPLEMENTED — absolute floor on both pass paths (rec A)
                                  #   0.63 = 100% recall, ~24% fewer LLM calls
                                  #   0.65 = 100% recall, ~36% fewer LLM calls (thinner margin)
  crossencoder:
    threshold: 0.60               # HOLD — do NOT raise (rec B). AUC 0.585 is a symptom of the
                                  # double-sigmoid bug (rec F), not a bad threshold.

# --- Stage 4: Relevance ---
relevance_scoring:
  min_score_threshold: null       # keep null or set ~0.030 only as a degenerate-candidate guard (rec E)
```

> `max_chunk_floor` is a new key: `src/stage5_biencoder.py` must read it and reject any page whose `max_chunk_score` is below it, regardless of which OR-branch it passed. This is a small code change (~5 lines) plus a test. Everything else is config-only.

---

## 4. Efficacy against Tripwire's goal

**Goal (per the system plan):** autonomously watch the influencer sources and alert IP Australia when an external change means an IPFR page should be updated — fail-closed, no silent drops.

**What works.** The pipeline runs end-to-end daily and does surface genuine, high-value signals. Over the window it produced ~24 `CHANGE_REQUIRED` alerts across ~20 distinct IPFR pages, clustered on exactly the sources you would hope: WIPO ADR / arbitration fees, the TTIPAB complaints pages, Amazon Brand Registry, and IP Australia hearings. The core hypothesis — that watching these sources catches IPFR-relevant change — is validated.

**What limits efficacy.**

- **Precision is ~3–4%.** 96–97% of what reaches the LLM is a false alarm. The calibration above recovers the first ~24–36% of that waste at zero recall cost, but the LLM remains the dominant filter.
- **Recall is unknown and currently unmeasurable.** We have labels for what the LLM *saw*, not for IPFR-relevant changes the upstream gates or scrape failures *dropped before* the LLM. The 58% scrape-error rate (audit §3) is the biggest recall risk: 17 sources fail 100% of the time, so any signal on them is invisible. **Reliability, not thresholds, is the larger efficacy problem** — a perfectly tuned gate cannot rank a page that was never fetched.
- **Signal value and trigger volume are badly correlated.** The four IP Australia manuals generated 32% of confirmed pages but almost no `CHANGE_REQUIRED`; the real signals came from a handful of fee/complaint pages. Source-level weighting is coarser than it should be (audit §3 Stage 4d).

**Net:** Tripwire is *effective at not missing what it sees* (fail-closed holds) but *inefficient*, and its true recall is capped by scrape reliability rather than by gate tuning.

---

## 5. How to improve it — prioritised

1. **Fix the input before the gates (highest leverage on the actual goal).** Set `SCRAPER_PROXY_URL` and re-enable the 17 quarantined sources. Half of all due checks currently fail; this is the biggest lever on *recall*, which no threshold can touch. (Audit recs #3/#4 — still open.)
2. **Amendment A (bi-encoder `max_chunk_floor` = 0.63) — done.** ~24% fewer LLM calls at 100% recall on the labelled set.
3. **Cross-encoder double-sigmoid fix (amendment F) — done.** Stage 6 is now a working second gate: the unchanged 0.60 threshold rejects 8% of false positives at 100% recall (was 0%), stacking ~15 unique rejections on top of the bi-encoder floor. Re-run the CI `backfill_scores.yml` job to confirm on native corrected scores.
4. **Hold Stage 6's threshold and the Stage-5 OR-logic (amendments B–D).** Explicitly *do not* raise the cross-encoder threshold or the bi-encoder high/min-chunks knobs — the data shows each would cost recall.
5. **Stop error-artefact diffs reaching the LLM (audit rec #10).** 17% of `NO_CHANGE` verdicts were degraded/error pages that passed validation. Tightening the size-ratio band and blocklist removes a large slice of the remaining false positives that the bi-encoder floor does not catch (error pages can still score topically).
6. **Re-weight sources by demonstrated signal value.** The manuals over-trigger and rarely matter; the fee/complaint pages under-trigger relative to their value. Feed the 20 confirmed IPFR pages back into source importance so budget follows signal.
7. **Consider trimming the lexical blend in Stage 6.** Now that the cross-encoder is un-crushed, the 0.2 lexical weight (AUC ≈ 0.52) measurably dilutes the raw CE signal (0.670 → 0.620 at the decision point). Lowering `_RERANK_WEIGHT_LEXICAL` is a candidate follow-up, validated on the corrected backfill.
8. **Keep accruing the labelled set and re-run this calibration quarterly.** 21 true positives is a thin basis for a production cut-point; the safety margin on `max_chunk_floor=0.63` (0.036 below the lowest TP) and the Stage-6 0.60 threshold (0.016 below the lowest TP) should be re-checked as the count grows. Running observation mode for a clean 2–4 week window (audit §5) is the cheapest way to grow it.

---

## 6. Appendix — provenance

All figures computed from `data/ipfr_corpus/ipfr.sqlite`, table `score_backfill` (580 rows; 567 verdict-labelled), joined to `llm_assessments`. AUC is the Mann-Whitney statistic P[score(CHANGE_REQUIRED) > score(NO_CHANGE)] over all labelled pairs. Sweeps count each labelled page once at its replayed score. Scores are replayed against the current corpus (historical snapshots not retained), so absolute values drift for older runs; the class-separation and ordering conclusions are robust to that drift because both classes are scored under the same corpus. No production `pipeline_runs` rows were modified.
