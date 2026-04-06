## Summary

This PR completes Phase 5 of the Tripwire system plan, implementing health alerting, observability reporting, and operational runbooks. The pipeline now monitors its own health, generates weekly observability reports, and provides comprehensive operational documentation.

## Key Changes

### New Modules

- **`src/health.py`** — Health alerting system that evaluates four conditions after every run:
  - Error rate > 30% in a single run
  - Same source failing N consecutive runs (default: 3)
  - LLM producing malformed output ≥ 2 times
  - Cross-encoder truncation occurring ≥ 3 times
  - Sends consolidated alert emails to `notifications.health_alert_email`

- **`src/observability.py`** — Weekly observability report generator that queries 30 days of pipeline run data and produces a Markdown report containing:
  - Per-source reliability table (total runs, successes, errors, current streak)
  - Score distributions for Stages 4–6 (min, p25, median, p75, max)
  - Weekly alert volume summary
  - Feedback summary (proportion of useful vs non-useful responses)

### Documentation

- **`docs/runbook-failure-response.md`** — Diagnostic guide for responding to pipeline failures, covering:
  - High error rate diagnosis and recovery
  - Consecutive source failure handling
  - LLM malformed output troubleshooting
  - Cross-encoder truncation response

- **`docs/runbook-adjust-thresholds.md`** — Threshold tuning guide explaining:
  - When to adjust thresholds based on feedback and observability data
  - Key threshold parameters (Stage 4 RRF, Stage 5 bi-encoder, Stage 6 cross-encoder)
  - Graph propagation decay tuning
  - Health alert threshold configuration

- **`docs/runbook-add-source.md`** — Source registry management guide covering:
  - Source registry schema and column definitions
  - Step-by-step instructions for adding webpage, FRL, and RSS sources
  - Importance weighting guidance
  - Testing and verification procedures

### Pipeline Integration

- **`src/pipeline.py`** — Updated to call `evaluate_and_alert()` after every run (both observation and production modes), collecting truncation pairs and LLM failure counts for health evaluation

- **`src/stage4_relevance.py` and `src/stage6_crossencoder.py`** — Added TODO comments referencing Phase 5 task 5.3 (threshold calibration) to be performed once 4–8 weeks of feedback data is available

### Configuration & Dependencies

- **`README.md`** — Completely rewritten to reflect the new nine-stage architecture, updated setup instructions, and configuration guidance
- **`CLAUDE.md`** — Updated to note that all nine phases are now complete and the full pipeline runs end-to-end
- **`requirements.txt`** — Reorganized with clear sections and added missing dependencies (pyyaml, trafilatura, spacy, yake, rank-bm25)

## Implementation Details

- Health alerts are sent via SMTP to a separate `health_alert_email` address (distinct from content-owner notifications)
- Observability reports query the `pipeline_runs` table and optional `feedback.jsonl` file
- All four health conditions are evaluated independently; multiple alerts can fire in a single run
- Runbooks provide both diagnostic SQL queries and step-by-step remediation procedures
- Configuration is centralized in `tripwire_config.yaml` with all thresholds documented and tunable

## Testing & Validation

The implementation includes:
- Health check functions with clear separation of concerns (error rate, consecutive failures, LLM malformed, truncation)
- Observability report generation with graceful handling of missing data
- Comprehensive runbook examples with SQL queries and decision trees

https://claude.ai/code/session_01DhQLqbAwiWkj3ri2mveyy7
