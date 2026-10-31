# Runbook: Adjusting Thresholds and Configuration

This runbook explains how to tune the pipeline's scoring thresholds and
behavioural parameters in `tripwire_config.yaml`.

All configuration changes should be committed to Git so that every parameter
change is tracked alongside the run data that results from it.

---

## 1. When to Adjust Thresholds

Adjust thresholds when the feedback data (from `data/logs/feedback.jsonl`) or
the weekly observability report (`data/logs/observability_<date>.md`) shows:

| Observation | Suggested action |
|-------------|-----------------|
| Too many `not_significant` feedback responses | Raise Stage 4 or Stage 6 thresholds to filter more aggressively |
| Too many `wrong_page` responses | Review Stage 5/6 bi-encoder and cross-encoder thresholds |
| Missing real changes (escaping the pipeline) | Lower thresholds or review source importance values |
| LLM `NO_CHANGE` rate very high | Raise Stage 6 threshold; only very high-confidence matches should reach Stage 8 |
| LLM `UNCERTAIN` rate very high | Review Stage 8 system prompt; improve evidence quality fed to the LLM |

See also: Phase 5, task 5.3 (threshold calibration) which provides a
systematic grid-search approach once sufficient feedback data is accumulated.

---

## 2. Key Threshold Parameters

All parameters live in `tripwire_config.yaml`. Edit the file and commit the
change to take effect on the next pipeline run.

### 2.1 Stage 4 — Relevance Scoring

```yaml
relevance_scoring:
  rrf_k: 60                          # RRF smoothing constant (higher = flatter rankings)
  rrf_weight_bm25: 1.0               # Weight for BM25 signal in RRF fusion
  rrf_weight_semantic: 2.0           # Weight for bi-encoder signal in RRF fusion
  top_n_candidates: 5                # Maximum candidates forwarded to Stage 5
  min_score_threshold: null          # Optional floor score; null = use top_n only
  source_importance_floor: 0.5       # Sources below this importance skip the fast-pass
  fast_pass:
    source_importance_min: 1.0       # Sources at or above this importance always proceed
```

**Increasing `top_n_candidates`** → more IPFR pages reach Stages 5–6 (more recall, more cost).
**Decreasing `top_n_candidates`** → fewer pages proceed (less cost, lower recall).
**Setting `min_score_threshold`** → discard low-scoring candidates even if within top N.

### 2.2 Stage 5 — Bi-Encoder

```yaml
semantic_scoring:
  biencoder:
    model: "BAAI/bge-base-en-v1.5"
    high_threshold: 0.75             # Any single chunk ≥ this → proceed
    low_medium_threshold: 0.45       # Multiple chunks ≥ this → proceed
    low_medium_min_chunks: 3         # How many chunks needed at the low threshold
```

**Lowering `high_threshold`** → more pages advance to Stage 6 (better recall on subtle matches).
**Raising `high_threshold`** → stricter gate (reduce false positives, risk missing real changes).

To find the right value, look at the `Stage 5 — Bi-encoder (max chunk cosine)` distribution in
the observability report. The threshold should sit just below the natural gap between
"real change" scores and "noise" scores.

### 2.3 Stage 6 — Cross-Encoder

```yaml
semantic_scoring:
  crossencoder:
    model: "gte-reranker-modernbert-base"
    threshold: 0.60                  # Final reranked score needed to proceed to Stage 7
    max_context_tokens: 8192
```

**Threshold 0.60** is the most impactful single parameter. Lower it to catch more changes;
raise it to reduce noise at Stage 8.

Check the `Stage 6 — Cross-encoder (reranked)` distribution in the observability report.
The threshold should sit between the "noise" mass (typically < 0.40) and the "signal"
mass (typically > 0.65).

### 2.4 Graph Propagation

```yaml
link_graph:
  max_hops: 3
  decay_per_hop: 0.45
  propagation_threshold: 0.05
```

**`decay_per_hop`** — how much score decays as alerts propagate to neighbouring IPFR pages.
Reduce this (e.g. to 0.3) to tighten graph propagation if too many indirect pages are being
flagged.

### 2.5 Health Alert Thresholds

```yaml
notifications:
  health_alert_conditions:
    error_rate_threshold: 0.30       # Alert if > 30% of sources error in a run
    consecutive_failures_threshold: 3 # Alert if same source fails 3 runs in a row
```

---

## 3. Observation Mode

During threshold calibration, enable observation mode to run the full pipeline
without sending emails or incurring LLM costs:

```yaml
pipeline:
  observation_mode: true
```

With observation mode active:
- Stages 1–7 run fully and scores are logged.
- Stage 8 (LLM) and Stage 9 (email) are skipped.
- Per-run summaries are written to `data/logs/observation_summary_<run_id>.json`.

Use the observability report to review score distributions, then disable
observation mode once thresholds are set:

```yaml
pipeline:
  observation_mode: false
```

---

## 4. Calibration Workflow

The recommended calibration workflow (Phase 5, task 5.3):

1. Run in observation mode for 4–8 weeks to accumulate score data.
2. Generate the observability report:

   ```bash
   python -m src.observability --config tripwire_config.yaml --days 60
   ```

3. Review the `Score Distributions` section. Identify natural gaps in the score
   distributions for each stage.
4. Set thresholds at the low end of the "signal" mass (conservative start).
5. Disable observation mode and run live for 2–4 weeks.
6. Review feedback via the `Feedback Summary` section. If precision (% `useful`)
   is low, raise thresholds. If important changes are being missed, lower them.
7. Repeat as needed.

---

## 5. Changing Source Importance Weights

Source importance weights in `source_registry.csv` multiply the Stage 4 relevance
score for all candidates sourced from that source.

```csv
source_id,name,url,source_type,importance,...
ipa_trademarks,IP Australia Trade Marks,https://...,webpage,1.0,...
abc_news_world,ABC News World,https://...,rss,0.3,...
```

**`importance = 1.0`** — triggers the fast-pass override in Stage 4 (source always
proceeds to Stage 5, regardless of RRF score). Use only for authoritative legislative
sources where every change is worth examining.

**`importance = 0.5`** — default mid-weight for secondary references.

**`importance = 0.3`** — low-signal sources; must score significantly above the
relevance threshold to proceed.

---

## 6. LLM Model and Cost Control

```yaml
pipeline:
  llm_model: "gpt-4o"
  llm_temperature: 0.2
```

To reduce cost while maintaining quality:
- Switch to `gpt-4o-mini` for a lower-cost alternative (test quality before switching in production).
- Lower Stage 6 threshold to ensure only high-confidence candidates reach Stage 8.
- Enable observation mode temporarily to audit score distributions and remove noisy sources.

Each Stage 8 LLM call costs approximately $0.002–$0.005 (gpt-4o, typical bundle size).
With 5–10 bundles per run and daily runs, monthly cost is approximately $0.30–$1.50.

---

## 7. Committing Configuration Changes

Always commit `tripwire_config.yaml` changes with a descriptive message:

```bash
git add tripwire_config.yaml
git commit -m "config: raise Stage 6 threshold 0.60 → 0.65 (reduce false positives)"
git push
```

The active configuration is snapshotted into the `pipeline_runs` table on every run,
so historical runs can always be interpreted in the context of the parameters that
were in effect at the time.
