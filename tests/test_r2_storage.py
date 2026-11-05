"""Tests for src/r2_storage.py using an in-memory fake of the S3 API (no network)."""

from __future__ import annotations

import hashlib
import io
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from src import r2_storage
from src.r2_storage import R2StorageError, endpoint_url, prune_backups, pull, push, r2_settings

NOW = datetime(2026, 11, 5, 3, 0, tzinfo=timezone.utc)


def _err(code: str, op: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


class _Body:
    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)

    def iter_chunks(self, chunk_size: int = 1024):
        while chunk := self._buf.read(chunk_size):
            yield chunk


class FakeR2:
    """Minimal S3 stand-in honouring IfMatch / IfNoneMatch like R2 does."""

    def __init__(self):
        self.objects: dict[str, dict] = {}
        self.clock = NOW

    def _store(self, key: str, data: bytes, when: datetime | None = None):
        etag = '"' + hashlib.md5(data).hexdigest() + '"'
        self.objects[key] = {"data": data, "etag": etag, "modified": when or self.clock}
        return etag

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _err("NoSuchKey", "GetObject")
        o = self.objects[Key]
        return {"Body": _Body(o["data"]), "ETag": o["etag"], "ContentLength": len(o["data"])}

    def put_object(self, Bucket, Key, Body, IfMatch=None, IfNoneMatch=None, **_):
        data = Body.read() if hasattr(Body, "read") else Body
        current = self.objects.get(Key)
        if IfNoneMatch == "*" and current is not None:
            raise _err("PreconditionFailed", "PutObject")
        if IfMatch is not None and (current is None or current["etag"] != IfMatch):
            raise _err("PreconditionFailed", "PutObject")
        return {"ETag": self._store(Key, data)}

    def copy_object(self, Bucket, Key, CopySource):
        self._store(Key, self.objects[CopySource["Key"]]["data"])

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        contents = [{"Key": k, "LastModified": o["modified"]}
                    for k, o in sorted(self.objects.items()) if k.startswith(Prefix)]
        return {"Contents": contents, "IsTruncated": False}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


def _settings(**overrides):
    cfg = {"storage": {"r2": {"enabled": True, "bucket": "tripwire", **overrides}}}
    return r2_settings(cfg)


def _make_db(path: Path, rows: int = 1) -> bytes:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS t (v INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    conn.commit()
    conn.close()
    return path.read_bytes()


def _rows(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    finally:
        conn.close()


# --- endpoint / settings -----------------------------------------------------


def test_endpoint_url_handles_jurisdiction():
    assert endpoint_url("abc") == "https://abc.r2.cloudflarestorage.com"
    assert endpoint_url("abc", "eu") == "https://abc.eu.r2.cloudflarestorage.com"
    assert endpoint_url("abc", " EU ") == "https://abc.eu.r2.cloudflarestorage.com"


def test_settings_defaults():
    s = r2_settings({"storage": {}})
    assert s["enabled"] is False
    assert s["database_key"] == "ipfr_corpus/ipfr.sqlite"
    assert s["backup_min_keep"] == 14


def test_make_client_requires_credentials(monkeypatch):
    for var in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(R2StorageError, match="R2_ACCOUNT_ID"):
        r2_storage.make_client(_settings())


# --- pull ----------------------------------------------------------------------


def test_pull_downloads_and_records_etag(tmp_path):
    fake = FakeR2()
    etag = fake._store("ipfr_corpus/ipfr.sqlite", _make_db(tmp_path / "src.sqlite", rows=3))
    db = tmp_path / "data" / "ipfr.sqlite"

    assert pull(fake, _settings(), db) == etag
    assert _rows(db) == 3
    assert (tmp_path / "data" / "ipfr.sqlite.r2-etag").read_text() == etag


def test_pull_missing_object_fails_closed_and_keeps_local_copy(tmp_path):
    db = tmp_path / "ipfr.sqlite"
    original = _make_db(db, rows=2)
    with pytest.raises(R2StorageError, match="No database"):
        pull(FakeR2(), _settings(), db)
    assert db.read_bytes() == original


def test_pull_rejects_corrupt_download(tmp_path):
    fake = FakeR2()
    fake._store("ipfr_corpus/ipfr.sqlite", b"this is not a sqlite database" * 200)
    db = tmp_path / "ipfr.sqlite"
    with pytest.raises(Exception):
        pull(fake, _settings(), db)
    assert not db.exists()
    assert not (tmp_path / "ipfr.sqlite.download").exists()


def test_pull_removes_stale_wal_files(tmp_path):
    fake = FakeR2()
    fake._store("ipfr_corpus/ipfr.sqlite", _make_db(tmp_path / "src.sqlite"))
    db = tmp_path / "ipfr.sqlite"
    (tmp_path / "ipfr.sqlite-wal").write_bytes(b"stale")
    pull(fake, _settings(), db)
    assert not (tmp_path / "ipfr.sqlite-wal").exists()


# --- push ----------------------------------------------------------------------


def test_round_trip_push_after_pull(tmp_path):
    fake = FakeR2()
    fake._store("ipfr_corpus/ipfr.sqlite", _make_db(tmp_path / "src.sqlite", rows=1))
    db = tmp_path / "ipfr.sqlite"
    pull(fake, _settings(), db)

    _make_db(db, rows=4)  # the job appends rows
    push(fake, _settings(), db, label="run-42", now=NOW)

    assert fake.objects["ipfr_corpus/ipfr.sqlite"]["data"] == db.read_bytes()
    backups = [k for k in fake.objects if k.startswith("backups/ipfr_corpus/")]
    assert backups == ["backups/ipfr_corpus/ipfr-20261105T030000Z-run-42.sqlite"]
    # The sidecar now holds the new ETag, so a second push in the same job works.
    push(fake, _settings(), db, now=NOW + timedelta(minutes=1))


def test_push_without_pull_is_refused(tmp_path):
    db = tmp_path / "ipfr.sqlite"
    _make_db(db)
    with pytest.raises(R2StorageError, match="run `pull` before `push`"):
        push(FakeR2(), _settings(), db)


def test_push_refuses_to_overwrite_concurrent_write_and_parks_copy(tmp_path):
    fake = FakeR2()
    fake._store("ipfr_corpus/ipfr.sqlite", _make_db(tmp_path / "src.sqlite", rows=1))
    db = tmp_path / "ipfr.sqlite"
    pull(fake, _settings(), db)

    # Another job replaces the database after we pulled.
    other = fake._store("ipfr_corpus/ipfr.sqlite", _make_db(tmp_path / "other.sqlite", rows=9))
    _make_db(db, rows=2)

    with pytest.raises(R2StorageError, match="changed since it was pulled"):
        push(fake, _settings(), db, label="run-7", now=NOW)

    assert fake.objects["ipfr_corpus/ipfr.sqlite"]["etag"] == other  # untouched
    parked = fake.objects["conflicts/ipfr_corpus/ipfr-20261105T030000Z-run-7.sqlite"]["data"]
    assert parked == db.read_bytes()  # nothing lost


def test_push_create_seeds_empty_bucket_only(tmp_path):
    fake = FakeR2()
    db = tmp_path / "ipfr.sqlite"
    _make_db(db)
    push(fake, _settings(), db, create=True, now=NOW)
    assert "ipfr_corpus/ipfr.sqlite" in fake.objects

    with pytest.raises(R2StorageError, match="already exists"):
        push(fake, _settings(), db, create=True, now=NOW)


def test_push_checkpoints_wal_before_upload(tmp_path):
    fake = FakeR2()
    db = tmp_path / "ipfr.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (v INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(50)])
    conn.commit()  # left open: rows live in the -wal file

    push(fake, _settings(), db, create=True, now=NOW)
    conn.close()

    restored = tmp_path / "restored.sqlite"
    restored.write_bytes(fake.objects["ipfr_corpus/ipfr.sqlite"]["data"])
    assert _rows(restored) == 50


# --- backup pruning ------------------------------------------------------------


def test_prune_backups_respects_retention_and_minimum(tmp_path):
    fake = FakeR2()
    for days_old in range(0, 120, 5):  # 24 backups, every 5 days
        fake._store(f"backups/ipfr_corpus/ipfr-{days_old:03d}.sqlite", b"x",
                    when=NOW - timedelta(days=days_old))
    fake._store("ipfr_corpus/ipfr.sqlite", b"live", when=NOW - timedelta(days=400))

    deleted = prune_backups(fake, _settings(backup_retention_days=90, backup_min_keep=14), now=NOW)

    # 90 days back keeps ages 0..90 (19 objects) which is above the minimum of 14.
    assert sorted(deleted) == [f"backups/ipfr_corpus/ipfr-{d:03d}.sqlite" for d in (95, 100, 105, 110, 115)]
    assert "ipfr_corpus/ipfr.sqlite" in fake.objects  # never touches the live object


def test_prune_backups_keeps_minimum_even_if_old(tmp_path):
    fake = FakeR2()
    for i in range(5):
        fake._store(f"backups/ipfr_corpus/old-{i}.sqlite", b"x", when=NOW - timedelta(days=300 + i))
    assert prune_backups(fake, _settings(backup_retention_days=90, backup_min_keep=3), now=NOW) == [
        "backups/ipfr_corpus/old-3.sqlite", "backups/ipfr_corpus/old-4.sqlite"]


# --- CLI -------------------------------------------------------------------------


def test_cli_refuses_when_disabled(tmp_path, monkeypatch):
    import yaml

    cfg = yaml.safe_load((Path(__file__).parent.parent / "tripwire_config.yaml").read_text())
    cfg["storage"]["r2"]["enabled"] = False
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump(cfg))
    assert r2_storage.main(["pull", "--config", str(path), "--db", str(tmp_path / "x.sqlite")]) == 2
