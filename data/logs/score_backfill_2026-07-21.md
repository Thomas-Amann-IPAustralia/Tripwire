# Historical Score Backfill — Calibration Dataset

**Generated:** 2026-07-21 03:05 UTC  
**Runs replayed:** 116 of 215 selected  
**Page rows written:** 580  

> Scores were recomputed with the current models against the **current** corpus (historical corpus snapshots are not retained), so treat older runs as indicative and weight recent runs. Historical `pipeline_runs` rows were not modified.

## Skipped runs (change document not faithfully recoverable)

| Reason | Runs |
|--------|------|
| `skipped_rss_rss_items` | 71 |
| `skipped_webpage_first_run` | 13 |
| `skipped_frl_explainer` | 12 |
| `skipped_frl_compilation_change` | 3 |

## Score distributions

| Series | Min | p25 | Median | p75 | Max | N |
|--------|-----|-----|--------|-----|-----|---|
| Bi-encoder max chunk (all scored candidates) | 0.492 | 0.629 | 0.675 | 0.714 | 0.955 | 580 |
| Cross-encoder final — confirmed & LLM CHANGE_REQUIRED | 0.547 | 0.597 | 0.674 | 0.751 | 0.823 | 21 |
| Cross-encoder final — confirmed & LLM NO_CHANGE | 0.444 | 0.574 | 0.636 | 0.736 | 0.835 | 518 |

## Cross-encoder threshold calibration

- Lowest cross-encoder final score among **useful** (CHANGE_REQUIRED) pages: **0.547** — a threshold above this would start dropping true positives.
- p75 of **NO_CHANGE** pages: **0.736**.
- Current threshold: **0.60** (confirmed 549 pages; the audit showed it rejected 0).

Pick the threshold that best separates the two distributions above; if they overlap heavily, the cross-encoder alone cannot cleanly gate and the signal should be combined with source importance or a bi-encoder floor.
