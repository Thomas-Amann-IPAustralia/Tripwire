# Runbook: database storage in Cloudflare R2

`data/ipfr_corpus/ipfr.sqlite` is stored in the Cloudflare R2 bucket `tripwire` (EU jurisdiction), not in git. Everything else the pipeline persists (influencer snapshots, LLM reports, `sitemap.csv`, IPFR page snapshots, `feedback.jsonl`) is still committed to the repository.

## Why

GitHub rejects any file over 100 MB. The database passed that size in September 2026 (about 85 MB of it is the `pipeline_runs` run log). From 22 September every push that included it was refused:

- the ingestion workflow failed every day;
- the pipeline's push ended in `|| true`, so it reported success while discarding the database;
- each run started from the last database that had been pushed, so run logs, LLM assessments and deferred triggers written after 22 September were lost.

The rows were recovered from the full database copies that the pipeline uploaded to a GitHub Release after every run (see "Recovering lost history" below).

## How it works

| Step | Command | Behaviour |
|---|---|---|
| Before a job | `python -m src.r2_storage pull` | Downloads the database, checks integrity and records its ETag in `ipfr.sqlite.r2-etag`. Fails if the object is missing or unreadable, so a job never runs against a stale database. |
| After a job | `python -m src.r2_storage push --label <run>` | Checkpoints WAL, checks integrity, then uploads with `If-Match: <etag>`. Saves a dated copy under `backups/ipfr_corpus/` and prunes copies older than 90 days (always keeping the newest 14). |

All jobs that write the database (`tripwire.yml`, `ipfr_ingestion.yml`, `backfill_scores.yml`, `recover_db.yml`) share the `tripwire-db` concurrency group, so they run one at a time. The `If-Match` check is a second safeguard. If the database in R2 changed after a job pulled it, the push is refused; the job's copy is saved under `conflicts/ipfr_corpus/` and the job fails.

Settings live in `tripwire_config.yaml` under `storage.r2`. Credentials are GitHub Actions secrets:

| Secret | Value |
|---|---|
| `R2_ACCOUNT_ID` | Cloudflare account ID |
| `R2_ACCESS_KEY_ID` | R2 API token access key (Object Read and Write on `tripwire`) |
| `R2_SECRET_ACCESS_KEY` | R2 API token secret |

The bucket was created in the EU jurisdiction, so it is only reachable at `https://<account>.eu.r2.cloudflarestorage.com` (`storage.r2.jurisdiction: "eu"`). The default endpoint reports `NoSuchBucket`.

## Working locally

```bash
export R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=...
python -m src.r2_storage pull
```

Only `push` from a local machine if you intend to replace the live database. The `If-Match` check will refuse if a scheduled job has written since your pull.

## Restoring from a backup

1. List the backups in the Cloudflare dashboard (bucket `tripwire`, prefix `backups/ipfr_corpus/`). Each backup is named with the time it was made and the run that made it.
2. Download the one you want to `data/ipfr_corpus/ipfr.sqlite`.
3. Run `python -m src.r2_storage pull --db /tmp/current.sqlite` to record the live ETag, then copy `/tmp/current.sqlite.r2-etag` to `data/ipfr_corpus/ipfr.sqlite.r2-etag`.
4. Run `python -m src.r2_storage push --label restore`.

## Handling a refused push (conflict)

The job log names the parked object, e.g. `conflicts/ipfr_corpus/ipfr-20261105T081500Z-pipeline-123.sqlite`. Download it, then merge its rows into the live database:

```bash
python -m src.r2_storage pull
python -m src.recover_db --into data/ipfr_corpus/ipfr.sqlite --from parked.sqlite
python -m src.r2_storage push --label merge-conflict
```

## Recovering lost history

Run the **Recover database into R2** workflow (`recover_db.yml`). It pulls the database from R2, merges history rows from every `data-*` release created on or after the `since` input, and, unless `dry_run` is ticked, pushes the result back. It only adds rows missing from the target (matched on natural keys), so re-running it is safe. Run it first as a dry run and check the summary.

Corpus tables (`pages`, `chunks` and so on) are never merged; the next ingestion run refreshes them from the live site. Restored deferred triggers keep their original `processed` flag, and the pipeline discards any older than `pipeline.deferred_trigger_max_age_days` on its next run.
