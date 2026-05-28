# Layer: pure
"""DB backup safety floor (Charter top-3 priority #1, PR1).

See docs/superpowers/specs/2026-05-28-db-backup-design.md for the design.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from marketpulse.ops.backup import (
    BACKUP_FILENAME_PREFIX,
    BACKUP_FILENAME_SUFFIX,
    BackupResult,
    prune_old_backups,
    run_backup,
    write_manifest,
)


def _make_source(tmp_path: Path, rows: list[tuple[int, str]]) -> Path:
    """Build a small SQLite DB with N rows in table 'kv'."""
    src = tmp_path / "source.db"
    conn = sqlite3.connect(str(src))
    try:
        conn.execute("CREATE TABLE kv(id INTEGER PRIMARY KEY, v TEXT)")
        conn.executemany("INSERT INTO kv VALUES (?, ?)", rows)
        conn.commit()
    finally:
        conn.close()
    return src


def test_run_backup_writes_consistent_snapshot(tmp_path):
    rows = [(1, "alpha"), (2, "beta"), (3, "gamma")]
    src = _make_source(tmp_path, rows)
    backups_dir = tmp_path / "backups"

    result = run_backup(source=src, backups_dir=backups_dir)

    assert result.status == "ok"
    assert result.integrity_check == "ok"
    assert result.error is None
    assert result.destination is not None
    dest = Path(result.destination)
    assert dest.exists()
    assert dest.parent == backups_dir
    assert dest.name.startswith(BACKUP_FILENAME_PREFIX)
    assert dest.name.endswith(BACKUP_FILENAME_SUFFIX)
    assert result.size_bytes is not None and result.size_bytes > 0

    # Reopen backup and confirm rows survived.
    conn = sqlite3.connect(str(dest))
    try:
        got = sorted(conn.execute("SELECT id, v FROM kv").fetchall())
    finally:
        conn.close()
    assert got == rows


def test_run_backup_handles_missing_source(tmp_path):
    src = tmp_path / "does_not_exist.db"
    backups_dir = tmp_path / "backups"

    result = run_backup(source=src, backups_dir=backups_dir)

    assert result.status == "failed"
    assert result.destination is None
    assert result.size_bytes is None
    assert result.integrity_check == "not_run"
    assert "FileNotFoundError" in (result.error or "")


def test_run_backup_handles_corrupted_source(tmp_path):
    """Source is a real file but not a valid SQLite DB."""
    src = tmp_path / "corrupted.db"
    src.write_bytes(b"this is not a sqlite database")
    backups_dir = tmp_path / "backups"

    result = run_backup(source=src, backups_dir=backups_dir)

    assert result.status == "failed"
    # Either backup() raises (DatabaseError) or integrity_check returns
    # not-ok — both surface as status=failed with non-empty error.
    assert result.error
    assert result.integrity_check in {"failed", "not_run"}


def test_run_backup_overwrites_same_day(tmp_path):
    """A second backup the same calendar day overwrites the first file
    (no orphan day-named files accumulate)."""
    src = _make_source(tmp_path, [(1, "first")])
    backups_dir = tmp_path / "backups"

    r1 = run_backup(source=src, backups_dir=backups_dir)
    assert r1.status == "ok"
    first_dest = Path(r1.destination)
    assert first_dest.exists()

    # Modify source between runs.
    conn = sqlite3.connect(str(src))
    try:
        conn.execute("INSERT INTO kv VALUES (2, 'second')")
        conn.commit()
    finally:
        conn.close()

    r2 = run_backup(source=src, backups_dir=backups_dir)
    assert r2.status == "ok"
    second_dest = Path(r2.destination)
    assert second_dest == first_dest  # same-day filename
    # Only one file in the backups dir matching pattern.
    matches = list(backups_dir.glob(
        f"{BACKUP_FILENAME_PREFIX}*{BACKUP_FILENAME_SUFFIX}"),
    )
    assert len(matches) == 1

    # Newer backup has 2 rows.
    conn = sqlite3.connect(str(second_dest))
    try:
        got = sorted(conn.execute("SELECT id, v FROM kv").fetchall())
    finally:
        conn.close()
    assert got == [(1, "first"), (2, "second")]


def test_prune_old_backups_keeps_recent_only(tmp_path):
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    now = datetime(2026, 5, 28, 9, 0, 0, tzinfo=UTC)
    # Production filename format: marketpulse-YYYY-MM-DD.db. Use real
    # dated names so the tightened glob (marketpulse-????-??-??.db) matches.
    for offset_days in range(14):
        file_date = (now - timedelta(days=offset_days)).date()
        p = backups_dir / (
            f"{BACKUP_FILENAME_PREFIX}{file_date.isoformat()}"
            f"{BACKUP_FILENAME_SUFFIX}"
        )
        p.write_bytes(b"x")
        target_time = (now - timedelta(days=offset_days)).timestamp()
        os.utime(p, (target_time, target_time))

    pruned = prune_old_backups(backups_dir=backups_dir, keep_days=7, now=now)

    # Files with mtime older than 7 days from `now` get deleted.
    # offset_days 0..7 are within 7 days (inclusive cutoff), 8..13 deleted.
    remaining = sorted(
        p.name for p in backups_dir.glob(
            f"{BACKUP_FILENAME_PREFIX}*{BACKUP_FILENAME_SUFFIX}",
        )
    )
    assert len(remaining) == 8  # offsets 0..7 = 8 files
    assert len(pruned) == 6     # offsets 8..13 = 6 files


def test_prune_old_backups_ignores_non_date_named_files(tmp_path):
    """The tightened glob `marketpulse-????-??-??.db` only deletes
    date-formatted snapshots. A manual recovery file dropped in the
    backups dir (e.g. `marketpulse-preupgrade.db`) survives retention.
    """
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    now = datetime(2026, 5, 28, 9, 0, 0, tzinfo=UTC)
    # Manual recovery file, ancient mtime.
    manual = backups_dir / f"{BACKUP_FILENAME_PREFIX}preupgrade{BACKUP_FILENAME_SUFFIX}"
    manual.write_bytes(b"recovery")
    ancient = (now - timedelta(days=30)).timestamp()
    os.utime(manual, (ancient, ancient))
    # Real dated backup, also ancient.
    dated = backups_dir / f"{BACKUP_FILENAME_PREFIX}2026-04-28{BACKUP_FILENAME_SUFFIX}"
    dated.write_bytes(b"old daily")
    os.utime(dated, (ancient, ancient))

    pruned = prune_old_backups(backups_dir=backups_dir, keep_days=7, now=now)

    assert dated not in [p for p in backups_dir.iterdir()]  # deleted
    assert manual.exists()  # survived
    assert pruned == [dated]


def test_prune_old_backups_no_dir_is_noop(tmp_path):
    # Pruning a nonexistent dir returns [] without crashing.
    pruned = prune_old_backups(
        backups_dir=tmp_path / "no_such_dir", keep_days=7,
    )
    assert pruned == []


def test_write_manifest_is_atomic(tmp_path):
    """Manifest write produces a complete file (no truncated state)
    even if a second writer races on the same path."""
    manifest_path = tmp_path / "latest.json"
    result_ok = BackupResult(
        status="ok",
        timestamp="2026-05-28T09:00:00.000000+00:00",
        source="/data/marketpulse.db",
        destination="/data/backups/marketpulse-2026-05-28.db",
        size_bytes=1024,
        integrity_check="ok",
        duration_ms=42,
        error=None,
    )
    write_manifest(manifest_path=manifest_path, result=result_ok)
    assert manifest_path.exists()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["status"] == "ok"
    assert payload["destination"] == result_ok.destination
    assert payload["error"] is None

    # Overwrite with a failure manifest — atomic replace should leave the
    # file in the new state, never a partial.
    result_fail = BackupResult(
        status="failed",
        timestamp="2026-05-28T09:00:00.500000+00:00",
        source="/data/marketpulse.db",
        destination=None,
        size_bytes=None,
        integrity_check="not_run",
        duration_ms=85,
        error="OSError: disk full",
    )
    write_manifest(manifest_path=manifest_path, result=result_fail)
    payload2 = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload2["status"] == "failed"
    assert payload2["destination"] is None
    assert "disk full" in payload2["error"]

    # No tempfile orphans linger.
    leftovers = sorted(manifest_path.parent.glob(".latest.*.json.tmp"))
    assert leftovers == []


def test_manifest_schema_includes_all_locked_fields(tmp_path):
    """Lock against accidental schema regressions. Phase 8a-equivalent
    locks for PR2's metrics endpoint must keep these fields stable."""
    src = _make_source(tmp_path, [(1, "x")])
    backups_dir = tmp_path / "backups"
    result = run_backup(source=src, backups_dir=backups_dir)
    write_manifest(
        manifest_path=backups_dir / "latest.json", result=result,
    )
    payload = json.loads(
        (backups_dir / "latest.json").read_text(encoding="utf-8"),
    )
    expected_keys = {
        "status", "timestamp", "source", "destination", "size_bytes",
        "integrity_check", "duration_ms", "error",
    }
    assert set(payload.keys()) == expected_keys


def test_backup_result_duration_is_measured(tmp_path):
    """Sanity: duration_ms is a non-negative int, even on trivial DBs."""
    src = _make_source(tmp_path, [(1, "x")])
    backups_dir = tmp_path / "backups"
    t0 = time.monotonic()
    result = run_backup(source=src, backups_dir=backups_dir)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    assert isinstance(result.duration_ms, int)
    assert 0 <= result.duration_ms <= elapsed_ms + 100  # slack
