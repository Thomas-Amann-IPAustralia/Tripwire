"""
src/r2_storage.py

Persist ``ipfr.sqlite`` in a Cloudflare R2 bucket instead of the git repository.

Why: GitHub rejects any file over 100 MB. Once the database crossed that limit
(September 2026) every push that included it was refused, and because the
pipeline's safety-net push ended in ``|| true`` the failure was silent. Each
run then started from the last database that *had* been pushed, so its run
log, LLM assessments and deferred triggers were discarded. R2 has no practical
file size limit, so the database now lives there and only the small text
artefacts (snapshots, LLM reports, sitemap, feedback log) stay in git.

Workflows call this module around every job that writes to the database:

    python -m src.r2_storage pull   # before the job; fails if R2 is unreachable
    python -m src.r2_storage push --label "$GITHUB_RUN_ID"   # after the job

Concurrency safety
------------------
``pull`` records the object's ETag in a sidecar file (``<db>.r2-etag``).
``push`` uploads with ``If-Match: <etag>``, so if anything else replaced the
database in the meantime the upload is refused instead of silently
overwriting the other job's work. When that happens the local database is
saved under ``conflicts/`` in the bucket (never dropped) and the command exits
non-zero so the workflow fails visibly. This complements, rather than
replaces, the shared GitHub Actions concurrency group.

Backups
-------
Every successful push also stores a dated server-side copy under
``storage.r2.backup_prefix``. Copies older than ``backup_retention_days`` are
pruned, but the newest ``backup_min_keep`` copies are always kept.

Credentials
-----------
Supplied as environment variables (GitHub Actions secrets), never in
``tripwire_config.yaml``: ``R2_ACCOUNT_ID``, ``R2_ACCESS_KEY_ID``,
``R2_SECRET_ACCESS_KEY``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).parent.parent
_ETAG_SUFFIX = ".r2-etag"
_ENV_VARS = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")


class R2StorageError(Exception):
    """Raised when the database cannot be safely pulled from or pushed to R2."""


# ---------------------------------------------------------------------------
# Settings and client
# ---------------------------------------------------------------------------


def r2_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Return ``storage.r2`` with defaults applied."""
    r2 = dict((config.get("storage") or {}).get("r2") or {})
    r2.setdefault("enabled", False)
    r2.setdefault("bucket", "")
    r2.setdefault("jurisdiction", "")
    r2.setdefault("database_key", "ipfr_corpus/ipfr.sqlite")
    r2.setdefault("backup_prefix", "backups/ipfr_corpus/")
    r2.setdefault("conflict_prefix", "conflicts/ipfr_corpus/")
    r2.setdefault("backup_retention_days", 90)
    r2.setdefault("backup_min_keep", 14)
    return r2


def endpoint_url(account_id: str, jurisdiction: str = "") -> str:
    """Build the S3 endpoint for an R2 account.

    Buckets created in a jurisdiction (e.g. ``eu``) are only reachable through
    that jurisdiction's endpoint; the default endpoint reports NoSuchBucket.
    """
    j = (jurisdiction or "").strip().lower()
    host = f"{account_id}.{j}.r2.cloudflarestorage.com" if j else f"{account_id}.r2.cloudflarestorage.com"
    return f"https://{host}"


def make_client(settings: dict[str, Any]):
    """Create a boto3 S3 client for R2 from environment credentials."""
    missing = [v for v in _ENV_VARS if not os.environ.get(v)]
    if missing:
        raise R2StorageError(
            "Missing R2 credentials in the environment: " + ", ".join(missing)
            + ". Add them as GitHub Actions secrets and pass them to this step."
        )
    import boto3  # imported lazily so the rest of the pipeline never needs it
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url(os.environ["R2_ACCOUNT_ID"], settings.get("jurisdiction", "")),
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(retries={"max_attempts": 5, "mode": "standard"}),
    )


def _error_code(exc: Exception) -> str:
    return str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))


def _is_not_found(exc: Exception) -> bool:
    return _error_code(exc) in {"404", "NoSuchKey", "NotFound"}


def _is_precondition_failed(exc: Exception) -> bool:
    return _error_code(exc) in {"412", "PreconditionFailed"}


# ---------------------------------------------------------------------------
# Local database helpers
# ---------------------------------------------------------------------------


def etag_path(db_path: Path) -> Path:
    return db_path.with_name(db_path.name + _ETAG_SUFFIX)


def prepare_for_upload(db_path: Path) -> None:
    """Fold any WAL content into the main file and verify integrity.

    The pipeline runs SQLite in WAL mode; without a checkpoint the newest
    writes would sit in ``-wal`` and never reach R2.
    """
    if not db_path.exists():
        raise R2StorageError(f"Database not found: {db_path}")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        result = conn.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        conn.close()
    if result != "ok":
        raise R2StorageError(f"SQLite quick_check failed for {db_path}: {result}")


def _verify_download(db_path: Path) -> None:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        result = conn.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        conn.close()
    if result != "ok":
        raise R2StorageError(f"Downloaded database failed quick_check: {result}")


# ---------------------------------------------------------------------------
# Pull / push
# ---------------------------------------------------------------------------


def pull(client, settings: dict[str, Any], db_path: Path) -> str:
    """Download the database from R2, replacing the local copy.

    Fails closed: if the object is missing or unreadable the local file is
    left untouched and R2StorageError is raised, so a job never runs against
    a stale or empty database. Returns the object's ETag.
    """
    bucket, key = settings["bucket"], settings["database_key"]
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
    except Exception as exc:  # botocore ClientError and network errors
        if _is_not_found(exc):
            raise R2StorageError(
                f"No database at r2://{bucket}/{key}. Seed it first with "
                "`python -m src.r2_storage push --create` or the "
                "'Recover database into R2' workflow."
            ) from exc
        raise R2StorageError(f"Could not download r2://{bucket}/{key}: {exc}") from exc

    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(db_path.name + ".download")
    with open(tmp, "wb") as fh:
        for chunk in obj["Body"].iter_chunks(chunk_size=8 * 1024 * 1024):
            fh.write(chunk)

    expected = obj.get("ContentLength")
    if expected is not None and tmp.stat().st_size != expected:
        tmp.unlink(missing_ok=True)
        raise R2StorageError(
            f"Truncated download: got {tmp.stat().st_size} bytes, expected {expected}."
        )
    try:
        _verify_download(tmp)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    # Stale WAL/SHM files from a previous local copy would corrupt the new one.
    for suffix in ("-wal", "-shm"):
        db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
    os.replace(tmp, db_path)

    etag = obj["ETag"]
    etag_path(db_path).write_text(etag, encoding="utf-8")
    logger.info("Pulled r2://%s/%s (%d bytes, ETag %s) -> %s",
                bucket, key, db_path.stat().st_size, etag, db_path)
    return etag


def push(
    client,
    settings: dict[str, Any],
    db_path: Path,
    label: str = "",
    create: bool = False,
    now: datetime | None = None,
) -> str:
    """Upload the database to R2 with optimistic locking, then back it up.

    ``create=True`` is only for seeding an empty bucket: the upload succeeds
    only if no database exists yet. Otherwise the ETag recorded by ``pull``
    must still match the object in R2. Returns the new ETag.
    """
    bucket, key = settings["bucket"], settings["database_key"]
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    suffix = f"-{_safe(label)}" if label else ""

    prepare_for_upload(db_path)

    put_kwargs: dict[str, Any] = {"Bucket": bucket, "Key": key, "ContentType": "application/vnd.sqlite3"}
    if create:
        put_kwargs["IfNoneMatch"] = "*"
    else:
        ep = etag_path(db_path)
        if not ep.exists():
            raise R2StorageError(
                f"No ETag recorded for {db_path}; run `pull` before `push` "
                "(or `push --create` to seed an empty bucket)."
            )
        put_kwargs["IfMatch"] = ep.read_text(encoding="utf-8").strip()

    try:
        with open(db_path, "rb") as fh:
            resp = client.put_object(Body=fh, **put_kwargs)
    except Exception as exc:
        if _is_precondition_failed(exc):
            conflict_key = f"{settings['conflict_prefix']}ipfr-{stamp}{suffix}.sqlite"
            with open(db_path, "rb") as fh:
                client.put_object(Bucket=bucket, Key=conflict_key, Body=fh)
            reason = (
                "a database already exists" if create
                else "the database in R2 changed since it was pulled (another job wrote to it)"
            )
            raise R2StorageError(
                f"Refused to overwrite r2://{bucket}/{key}: {reason}. "
                f"This job's database was saved to r2://{bucket}/{conflict_key} "
                "so its rows can be merged with `python -m src.recover_db`."
            ) from exc
        raise R2StorageError(f"Could not upload r2://{bucket}/{key}: {exc}") from exc

    etag = resp["ETag"]
    etag_path(db_path).write_text(etag, encoding="utf-8")
    logger.info("Pushed %s -> r2://%s/%s (%d bytes, ETag %s)",
                db_path, bucket, key, db_path.stat().st_size, etag)

    backup_key = f"{settings['backup_prefix']}ipfr-{stamp}{suffix}.sqlite"
    client.copy_object(Bucket=bucket, Key=backup_key, CopySource={"Bucket": bucket, "Key": key})
    logger.info("Backed up to r2://%s/%s", bucket, backup_key)
    prune_backups(client, settings, now=now)
    return etag


def prune_backups(client, settings: dict[str, Any], now: datetime | None = None) -> list[str]:
    """Delete backups older than the retention window, keeping the newest N."""
    bucket, prefix = settings["bucket"], settings["backup_prefix"]
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=int(settings["backup_retention_days"]))
    keep = int(settings["backup_min_keep"])

    objects: list[dict[str, Any]] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = client.list_objects_v2(**kwargs)
        objects.extend(page.get("Contents", []))
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")

    objects.sort(key=lambda o: o["LastModified"], reverse=True)
    doomed = [o["Key"] for o in objects[keep:] if o["LastModified"] < cutoff]
    for k in doomed:
        client.delete_object(Bucket=bucket, Key=k)
    if doomed:
        logger.info("Pruned %d backup(s) older than %s.", len(doomed), cutoff.date())
    return doomed


def _safe(label: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in label)[:80]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync ipfr.sqlite with Cloudflare R2.")
    parser.add_argument("command", choices=["pull", "push"])
    parser.add_argument("--config", default=str(_REPO_ROOT / "tripwire_config.yaml"))
    parser.add_argument("--db", default=None, help="Local database path (default: paths.sqlite_db)")
    parser.add_argument("--label", default="", help="Tag for backup object names, e.g. the run ID")
    parser.add_argument("--create", action="store_true",
                        help="push only: seed an empty bucket (fails if a database already exists)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from src.config import load_config

    config = load_config(args.config)
    settings = r2_settings(config)
    if not settings["enabled"]:
        logger.error("storage.r2.enabled is false; refusing to %s.", args.command)
        return 2
    if not settings["bucket"]:
        logger.error("storage.r2.bucket is not set.")
        return 2

    config_dir = Path(args.config).resolve().parent
    db_path = Path(args.db) if args.db else config_dir / config.get("paths", {}).get(
        "sqlite_db", "data/ipfr_corpus/ipfr.sqlite")

    try:
        client = make_client(settings)
        if args.command == "pull":
            pull(client, settings, db_path)
        else:
            push(client, settings, db_path, label=args.label, create=args.create)
    except R2StorageError as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
