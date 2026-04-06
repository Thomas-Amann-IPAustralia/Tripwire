# Runbook: Adding a New Influencer Source

This runbook describes how to add a new monitored source to Tripwire.

---

## 1. Overview

All monitored sources are defined in `data/influencer_sources/source_registry.csv`.
Adding a source involves three steps:

1. Add a row to `source_registry.csv`.
2. Create the initial snapshot directory.
3. Verify the source works in a test run.

No code changes are required for standard source types (`webpage`, `frl`, `rss`).

---

## 2. Source Registry Schema

`data/influencer_sources/source_registry.csv` has the following columns:

| Column | Type | Description |
|--------|------|-------------|
| `source_id` | string | Unique identifier, e.g. `ipa_trademarks_page`. No spaces; use underscores. |
| `name` | string | Human-readable name, e.g. `IP Australia — What are trade marks?` |
| `url` | string | Full URL to the resource. |
| `source_type` | string | One of: `webpage`, `frl`, `rss` |
| `importance` | float | Source importance weight: `0.0`–`1.0`. Set `1.0` for authoritative sources (fast-pass). |
| `check_frequency_hours` | int | How often to check this source (e.g. `24` = daily). |
| `enabled` | bool | `true` to monitor; `false` to pause without deleting. |
| `expected_markers` | string (optional) | Comma-separated substrings expected in the page content. Used for structural validation. |

### Source type routing

| `source_type` | Stage 2 applied? | Stage 3 mechanism |
|---------------|-----------------|-------------------|
| `webpage` | Yes (SHA-256 hash, word diff, significance tagger) | `.diff` vs previous snapshot |
| `frl` | No | Retrieve FRL change explainer document |
| `rss` | No | Extract new items since last check |

---

## 3. Step-by-Step: Adding a Webpage Source

### 3.1 Add to source_registry.csv

```csv
my_new_source,My New Source Name,https://example.gov.au/ip-page,webpage,0.8,24,true,"intellectual property,trade mark"
```

**Choosing `importance`:**
- `1.0` — authoritative legislative sources (fast-pass override; always proceed to scoring)
- `0.7`–`0.9` — high-quality government or official agency pages
- `0.5`–`0.6` — secondary references
- `0.3`–`0.4` — low-signal sources (blogs, news feeds)

### 3.2 Verify the URL

Before committing, manually check:

```bash
curl -I "https://example.gov.au/ip-page"
```

Confirm the URL returns HTTP 200 and is not behind a login or CAPTCHA.

### 3.3 Create the initial snapshot

The pipeline will create the snapshot directory automatically on the first run.
Optionally, pre-seed a snapshot to avoid a false-change alert on the first run:

```bash
mkdir -p data/influencer_sources/snapshots/my_new_source
# The first pipeline run will record the initial state.json automatically.
```

### 3.4 Test the source in isolation

Run the pipeline with `--log-level DEBUG` to see verbose output for the new source:

```bash
python -m src.pipeline --config tripwire_config.yaml --log-level DEBUG
```

Check the output for:
- `Stage 1: source my_new_source — no change detected` (expected on first run if the content matches a prior snapshot)
- Any `PermanentError` or `RetryableError` lines — these indicate the URL is unreachable or blocked.

### 3.5 Commit the registry change

```bash
git add data/influencer_sources/source_registry.csv
git commit -m "chore: add monitoring for my_new_source"
git push
```

---

## 4. Step-by-Step: Adding an RSS Feed Source

1. Add a row with `source_type = rss`.
2. Set `check_frequency_hours` to something appropriate (e.g. `6` for a news feed, `24` for an official press room).
3. Leave `expected_markers` empty — RSS feeds are validated by item count, not content markers.
4. The pipeline will extract new items since the last check (based on item GUIDs stored in `state.json`).

---

## 5. Step-by-Step: Adding a Federal Register of Legislation (FRL) Source

1. Set `source_type = frl`.
2. The `url` should be the canonical FRL series URL for the instrument.
3. The pipeline probes the FRL registerId to detect new compilations, then retrieves the change explainer document automatically.
4. No snapshot is stored — Stage 2 is skipped for FRL sources.

---

## 6. Disabling or Removing a Source

**To pause monitoring temporarily** (e.g. source is undergoing maintenance):

```csv
my_old_source,My Old Source,https://...,webpage,0.8,24,false,""
```

Set `enabled = false`. The pipeline will skip this source entirely.

**To remove a source permanently:**

1. Delete the row from `source_registry.csv`.
2. Optionally clean up the snapshot directory:

   ```bash
   rm -rf data/influencer_sources/snapshots/my_old_source
   ```

3. Commit both changes.

Historical `pipeline_runs` records for the removed source remain in SQLite for audit purposes.

---

## 7. Troubleshooting New Sources

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `PermanentError: HTTP 404` | Wrong URL | Correct the `url` in the registry |
| `PermanentError: CAPTCHA` | Site blocks bots | Remove from registry; consider an official RSS or API alternative |
| `PermanentError: Content too short` | Login page or redirect returned instead of content | Check URL and authentication requirements |
| `Dramatic size change` flagged | First-run snapshot mismatch | Wait for the second run; the alert will not repeat once the baseline is set |
| Source never shows as "changed" | `check_frequency_hours` too high | Reduce the frequency, or verify the site is actually updating |
