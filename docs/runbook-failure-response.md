# Runbook: Responding to Pipeline Failures

This runbook covers how to diagnose and resolve failures in the Tripwire pipeline.

---

## 1. Where to Look First

| Signal | Location |
|--------|----------|
| GitHub Actions failure | Actions tab → `tripwire.yml` run |
| Health alert email | Inbox of `notifications.health_alert_email` |
| Health alert fallback | `data/logs/health_alert_<run_id>.txt` |
| Per-run log rows | `pipeline_runs` table in `data/ipfr_corpus/ipfr.sqlite` |
| Per-run observation summary | `data/logs/observability_<date>.md` |

---

## 2. Alert Conditions and Responses

### 2.1 High Error Rate (> 30% of sources failed in one run)

**Symptom:** Health alert email with subject `[Tripwire] WARNING — N health alert(s)`, condition `error_rate`.

**Diagnosis:**

```sql
-- Find all sources that errored in the most recent run
SELECT source_id, error_type, error_message
FROM pipeline_runs
WHERE run_id = '<run_id>' AND outcome = 'error'
ORDER BY source_id;
```

**Common causes:**

| Error type | Cause | Fix |
|------------|-------|-----|
| `RetryableError: HTTP 5xx` | Upstream server temporarily down | Wait for next scheduled run; no action needed if isolated |
| `PermanentError: HTTP 404` | Source URL has changed | Update `source_registry.csv` with new URL |
| `PermanentError: CAPTCHA` | Site has added bot protection | Switch to a different scrape method; consider removing the source |
| `ConnectionError` | Network issue on the Actions runner | Re-run the workflow manually |

**Recovery:**

- If the error was transient (e.g. temporary outage), re-run the workflow via `workflow_dispatch`.
- If a specific source is the culprit, update or disable it in `source_registry.csv` (see [runbook-add-source.md](runbook-add-source.md)).

---

### 2.2 Consecutive Source Failures (same source fails N runs in a row)

**Symptom:** Health alert condition `consecutive_failures`, naming a specific `source_id`.

**Diagnosis:**

```sql
-- Check recent history for the failing source
SELECT run_id, timestamp, outcome, error_type, error_message
FROM pipeline_runs
WHERE source_id = '<source_id>'
ORDER BY timestamp DESC
LIMIT 10;
```

**Steps:**

1. Visit the source URL manually to confirm it still exists and is reachable.
2. If the URL has moved: update the `url` column in `data/influencer_sources/source_registry.csv`.
3. If the site requires authentication or has blocked bots: update the source entry or set `enabled = false` to pause monitoring.
4. After updating, re-run the workflow to confirm the fix.

---

### 2.3 LLM Malformed Output (Stage 8 failures)

**Symptom:** Health alert condition `llm_malformed`.

**Diagnosis:**

Affected bundles are stored in the `deferred_triggers` table for retry next run:

```sql
SELECT id, run_id, source_id, ipfr_page_id, created_at
FROM deferred_triggers
WHERE processed = 0
ORDER BY created_at DESC;
```

Check the Actions log for the run (`Stage 8` section) for the raw LLM output that failed validation.

**Common causes:**

- The LLM API returned an error or empty response under load.
- The model produced non-JSON content (e.g. markdown fence wrapping the JSON).
- Token budget exceeded, causing truncated output.

**Steps:**

1. If the issue was transient (rate limit, API outage): deferred triggers will be retried automatically on the next run. No action needed.
2. If the issue is systematic (repeated across many runs): review the system prompt in `src/stage8_llm.py` (`SYSTEM_PROMPT`) and ensure `response_format: json_object` is specified in the API call.
3. If `max_tokens` is too low, increase it in `tripwire_config.yaml` under `pipeline.llm_model` options.

---

### 2.4 Cross-Encoder Truncation (Stage 6)

**Symptom:** Health alert condition `crossencoder_truncation`, listing affected source/page pairs.

**Cause:** The combined length of a change document and an IPFR page exceeded the model's 8,192-token context window. Scores for truncated pairs may be unreliable.

**Steps:**

1. Review the listed source/page pairs. If they are structurally large sources (e.g. full legislation Acts), consider:
   - Increasing chunking granularity in Stage 5 to reduce the document fed to Stage 6.
   - Reducing the diff size passed to cross-encoder by tightening Stage 3 filtering.
2. If the issue is widespread, reduce `semantic_scoring.crossencoder.max_context_tokens` in `tripwire_config.yaml` to a safe ceiling, so truncation is handled consistently.
3. Log the affected pairs for the threshold calibration review (Phase 5, task 5.3).

---

### 2.5 Pipeline Timeout (GitHub Actions)

**Symptom:** GitHub Actions job marked as failed with reason `The job running on runner ... has exceeded the maximum execution time`.

The pipeline is configured with `timeout-minutes: 30` in `.github/workflows/tripwire.yml`.

**Diagnosis:** Check which step was in progress when the timeout occurred (visible in the Actions log).

**Common causes and fixes:**

| Cause | Fix |
|-------|-----|
| Bi-encoder or cross-encoder model download slow | Ensure Hugging Face cache (`~/.cache/huggingface/`) is being cached by `actions/cache@v4` |
| Too many sources due for check | Stagger source frequencies in `source_registry.csv`; set `check_frequency_hours` higher for low-priority sources |
| LLM API very slow | Reduce `pipeline.llm_temperature` or switch to a faster model |

---

### 2.6 SQLite Database Locked or Corrupted

**Symptom:** Pipeline exits immediately with message `Cannot open SQLite database`.

**Steps:**

1. Check that no other process holds a lock on `ipfr.sqlite`:

   ```bash
   fuser data/ipfr_corpus/ipfr.sqlite
   ```

2. Enable WAL mode if not already set (`storage.sqlite_wal_mode: true` in config). WAL mode reduces lock contention significantly.

3. If the database is corrupted, restore from the most recent Git commit:

   ```bash
   git checkout HEAD -- data/ipfr_corpus/ipfr.sqlite
   ```

   The database is committed after every ingestion run, so at most one run's data will be lost.

---

## 3. Manual Re-runs

To re-run the pipeline manually after fixing a problem:

1. Go to the repository **Actions** tab.
2. Select the **Tripwire** workflow.
3. Click **Run workflow** → choose the branch → optionally set `run_id` and `log_level`.
4. Click **Run workflow**.

Alternatively, from the repository root:

```bash
python -m src.pipeline --config tripwire_config.yaml --log-level DEBUG
```

---

## 4. Checking Run History

```bash
# Open the SQLite database
sqlite3 data/ipfr_corpus/ipfr.sqlite

-- Most recent 10 runs
SELECT DISTINCT run_id, MIN(timestamp) AS started, COUNT(*) AS sources_processed
FROM pipeline_runs
GROUP BY run_id
ORDER BY started DESC
LIMIT 10;

-- Sources with consecutive errors
SELECT source_id, COUNT(*) AS errors
FROM pipeline_runs
WHERE outcome = 'error'
GROUP BY source_id
ORDER BY errors DESC;
```

---

## 5. Generating the Observability Report on Demand

```bash
python -m src.observability --config tripwire_config.yaml --days 30
```

The report is written to `data/logs/observability_<date>.md`.
