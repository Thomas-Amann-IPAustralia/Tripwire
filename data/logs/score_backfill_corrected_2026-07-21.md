# Stage 6 Cross-Encoder Recalibration — Corrected Scores

**Generated:** 2026-07-21 15:20 UTC  
**Method:** analytic reconstruction from the existing `score_backfill` table.  
**Labelled pairs:** 21 CHANGE_REQUIRED, 519 NO_CHANGE (N=540).

> The double-sigmoid bug (fixed in `stage6_crossencoder._score_pair`) was an **exact invertible transform**: the stored cross-encoder score equals `sigmoid(p)` where `p` is the value the fixed code now returns. Corrected scores are recovered as `p = logit(stored)` and the reranked/final blends recomputed with the recovered per-row lexical term (`lexical = (reranked − 0.8·ce)/0.2`, verified in [0,1] for all rows). This isolates the fix from corpus drift; the CI `backfill_scores.yml` job now runs the fixed code and will produce native corrected scores to confirm.

## Separability (AUC: P[score(CHANGE_REQUIRED) > score(NO_CHANGE)])

| Score | Buggy | Corrected |
|-------|------:|----------:|
| Raw cross-encoder | 0.670 | 0.670 (identical — logit is monotonic) |
| Final (blend + graph) | 0.585 | 0.620 |
| Bi-encoder max-chunk (reference) | — | 0.834 |

The fix lifts the **final-score** AUC (0.585 → 0.620) by letting the cross-encoder — not the near-random lexical term — drive the blend. The raw cross-encoder's own AUC (0.670) is unchanged by the fix (a monotonic transform cannot re-rank), and remains well below the bi-encoder's 0.834.

## Corrected score distributions

| Series | Min | p25 | Median | p75 | Max |
|--------|----:|----:|-------:|----:|----:|
| Raw CE — CHANGE_REQUIRED | 0.680 | 0.829 | 0.857 | 0.888 | 0.924 |
| Raw CE — NO_CHANGE | 0.222 | 0.771 | 0.823 | 0.858 | 0.936 |
| Final — CHANGE_REQUIRED | 0.616 | 0.714 | 0.775 | 0.886 | 0.967 |
| Final — NO_CHANGE | 0.178 | 0.671 | 0.735 | 0.815 | 0.989 |

## Threshold sweep — corrected FINAL score (the config `threshold` gates on this)

| Threshold | TP kept | FP kept | Recall | LLM calls | Precision | FP dropped |
|----------:|--------:|--------:|-------:|----------:|----------:|-----------:|
| 0.500 | 21 | 511 | 100.0% | 532 | 3.9% | 2% |
| 0.550 | 21 | 496 | 100.0% | 517 | 4.1% | 4% |
| 0.600 ⬅ current | 21 | 478 | 100.0% | 499 | 4.2% | 8% |
| 0.616 ⬅ lowest TP | 21 | 463 | 100.0% | 484 | 4.3% | 11% |
| 0.630 | 20 | 448 | 95.2% | 468 | 4.3% | 14% |
| 0.650 | 19 | 423 | 90.5% | 442 | 4.3% | 18% |
| 0.680 | 17 | 371 | 81.0% | 388 | 4.4% | 29% |
| 0.700 | 16 | 326 | 76.2% | 342 | 4.7% | 37% |

## Stacked with the Stage-5 bi-encoder floor (0.63, already shipped)

After the bi-encoder floor: **21 TP, 390 FP** remain. Adding a corrected cross-encoder final floor on top:

| CE final floor | TP kept | FP kept | Recall | Incremental FP dropped vs bi-floor only |
|---------------:|--------:|--------:|-------:|----------------------------------------:|
| 0.000 | 21 | 390 | 100.0% | 0 |
| 0.550 | 21 | 384 | 100.0% | 6 |
| 0.600 | 21 | 375 | 100.0% | 15 |
| 0.616 | 21 | 368 | 100.0% | 22 |
| 0.650 | 19 | 344 | 90.5% | 46 |

## Calibration conclusion

1. **Keep `threshold: 0.60`.** On the corrected scale every true positive scores ≥ 0.616, so 0.60 keeps **100% recall** while now rejecting **41 of 519 false positives (8%)** at zero recall cost. Pre-fix, the same 0.60 rejected **none** (§ audit). So the fix alone — with no threshold change — converts Stage 6 from a no-op into a gate that does real work.
2. **Do not raise it.** The lowest true positive sits at 0.616; 0.616 is the zero-margin ceiling and 0.63 already drops a true positive (recall 95.2%). With only 21 positives, keep the margin.
3. **Stage 6 now adds modest, non-redundant value on top of the Stage-5 floor.** The two gates catch overlapping errors — the bi-encoder floor already removes 129 of 519 false positives, and 26 of the 41 that a 0.60 CE gate would drop are among them — but the cross-encoder still contributes **~15 unique false-positive rejections (≈4% of the post-bi-floor pool) at 100% recall**, rising to ~22 at 0.616. This is the answer the fix unlocked: Stage 6 was *broken*, not merely redundant — and now that it is fixed, it earns its place as a genuine (if secondary) second gate, bounded by the cross-encoder's modest intrinsic separation (AUC 0.670 vs the bi-encoder's 0.834).
4. **The lexical blend hurts.** Blending 0.2·lexical (AUC 0.523, ~random) drags the raw CE signal (0.670) down to 0.620 in the final score. A future improvement is to reduce `_RERANK_WEIGHT_LEXICAL` or gate on the raw CE score directly — but that is a separate change to validate, not part of this fix.

_All figures reconstructed from `data/ipfr_corpus/ipfr.sqlite` `score_backfill`; no production rows modified._
