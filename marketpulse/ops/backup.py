"""SQLite online backup with manifest + 7-day retention.

Charter top-3 priority #1, PR1 (DB safety floor). See
docs/superpowers/specs/2026-05-28-db-backup-design.md for the locked
design contract.

Key invariants:
- `sqlite3.Connection.backup()` is the ONLY copy mechanism (no shell, no
  file copy) — guarantees online consistency.
- `BackupResult.status` is the SINGLE field PR2's metrics endpoint reads
  to know whether the most recent backup is usable.
- A failed backup does NOT trigger pruning — older successful backups
  stay reachable even on a bad day.
- Manifest write is atomic via tempfile + os.replace.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from marketpulse.logging import get_logger

log = get_logger(__name__)

BACKUP_FILENAME_PREFIX = "marketpulse-"
BACKUP_FILENAME_SUFFIX = ".db"
MANIFEST_FILENAME = "latest.json"
# Tightened from `marketpulse-*.db` so manual recovery files (e.g.
# `marketpulse-preupgrade.db`) survive retention even if dropped into the
# backups dir. Production filenames are always `marketpulse-YYYY-MM-DD.db`.
_BACKUP_GLOB_PATTERN = f"{BACKUP_FILENAME_PREFIX}????-??-??{BACKUP_FILENAME_SUFFIX}"


@dataclass(frozen=True)
class BackupResult:
    status: Literal["ok", "failed"]
    timestamp: str  # ISO-8601 UTC string
    source: str
    destination: str | None
    size_bytes: int | None
    integrity_check: Literal["ok", "failed", "not_run"]
    duration_ms: int
    error: str | None


def run_backup(*, source: Path, backups_dir: Path) -> BackupResult:
    """Take an online SQLite snapshot of `source` into `backups_dir`.

    Returns a BackupResult capturing the outcome — never raises. Caller
    (the scheduler entrypoint) writes the manifest and decides whether to
    prune.

    Failure modes covered by status="failed":
    - source path does not exist
    - source path is not a valid SQLite file
    - destination write fails (disk full, permission)
    - integrity check returns anything other than "ok"
    """
    started = time.monotonic()
    now = datetime.now(UTC)
    timestamp_str = now.isoformat()
    source_str = str(source)

    # Pre-flight: source must exist and be readable.
    if not source.exists():
        return BackupResult(
            status="failed",
            timestamp=timestamp_str,
            source=source_str,
            destination=None,
            size_bytes=None,
            integrity_check="not_run",
            duration_ms=int((time.monotonic() - started) * 1000),
            error=f"FileNotFoundError: source {source_str} does not exist",
        )

    backups_dir.mkdir(parents=True, exist_ok=True)
    dest_name = f"{BACKUP_FILENAME_PREFIX}{now.strftime('%Y-%m-%d')}{BACKUP_FILENAME_SUFFIX}"
    dest_path = backups_dir / dest_name

    # Same-day re-run: remove existing snapshot so backup() writes a fresh page set.
    # The sqlite3.backup API overwrites pages but does not shrink the
    # destination if the source is smaller — safer to start from a clean
    # file each invocation.
    if dest_path.exists():
        try:
            dest_path.unlink()
        except OSError as exc:
            return BackupResult(
                status="failed",
                timestamp=timestamp_str,
                source=source_str,
                destination=str(dest_path),
                size_bytes=None,
                integrity_check="not_run",
                duration_ms=int((time.monotonic() - started) * 1000),
                error=f"{type(exc).__name__}: pre-clean destination failed: {exc}",
            )

    src_conn: sqlite3.Connection | None = None
    dst_conn: sqlite3.Connection | None = None
    try:
        # Open source read-only. `Connection.backup()` does not need write
        # access, and the URI form prevents the backup process from ever
        # accidentally mutating the production DB (defensive against future
        # code drift).
        src_conn = sqlite3.connect(f"file:{source_str}?mode=ro", uri=True)
        dst_conn = sqlite3.connect(str(dest_path))
        src_conn.backup(dst_conn)
        # Integrity-check the FRESHLY WRITTEN destination, not the source.
        row = dst_conn.execute("PRAGMA integrity_check").fetchone()
        integrity = "ok" if row and row[0] == "ok" else "failed"
        if integrity != "ok":
            err_msg = f"integrity_check returned {row[0] if row else '<empty>'}"
            return BackupResult(
                status="failed",
                timestamp=timestamp_str,
                source=source_str,
                destination=str(dest_path),
                size_bytes=dest_path.stat().st_size if dest_path.exists() else None,
                integrity_check="failed",
                duration_ms=int((time.monotonic() - started) * 1000),
                error=err_msg,
            )
        return BackupResult(
            status="ok",
            timestamp=timestamp_str,
            source=source_str,
            destination=str(dest_path),
            size_bytes=dest_path.stat().st_size,
            integrity_check="ok",
            duration_ms=int((time.monotonic() - started) * 1000),
            error=None,
        )
    except Exception as exc:  # noqa: BLE001 — any failure must surface as status="failed"
        return BackupResult(
            status="failed",
            timestamp=timestamp_str,
            source=source_str,
            destination=str(dest_path) if dest_path.exists() else None,
            size_bytes=dest_path.stat().st_size if dest_path.exists() else None,
            integrity_check="not_run",
            duration_ms=int((time.monotonic() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if dst_conn is not None:
            with suppress(Exception):
                dst_conn.close()
        if src_conn is not None:
            with suppress(Exception):
                src_conn.close()


def prune_old_backups(
    *, backups_dir: Path, keep_days: int = 7, now: datetime | None = None,
) -> list[Path]:
    """Delete backup files older than `keep_days` days by mtime.

    Returns the list of deleted paths. `now` is injectable for tests.
    """
    if now is None:
        now = datetime.now(UTC)
    cutoff = now - timedelta(days=keep_days)
    pruned: list[Path] = []
    if not backups_dir.exists():
        return pruned
    for path in backups_dir.glob(_BACKUP_GLOB_PATTERN):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        if mtime < cutoff:
            try:
                path.unlink()
                pruned.append(path)
            except OSError as exc:
                log.warning(
                    "db_backup_prune_failed", path=str(path), error=str(exc),
                )
    return pruned


def write_manifest(*, manifest_path: Path, result: BackupResult) -> None:
    """Write the result as JSON atomically (tempfile + os.replace).

    A manifest write failure is logged but does NOT raise — the scheduler
    must not die because of a manifest issue.
    """
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(result), indent=2, sort_keys=True)
    tmp_fd: int | None = None
    tmp_path: str | None = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=".latest.", suffix=".json.tmp",
            dir=str(manifest_path.parent),
        )
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            tmp_fd = None  # ownership transferred to fdopen
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, manifest_path)
        tmp_path = None  # successfully renamed; no cleanup needed
    except Exception as exc:  # noqa: BLE001 — manifest must not crash scheduler
        log.warning(
            "db_backup_manifest_write_failed",
            path=str(manifest_path), error=str(exc),
        )
    finally:
        if tmp_fd is not None:
            with suppress(OSError):
                os.close(tmp_fd)
        if tmp_path is not None and os.path.exists(tmp_path):
            with suppress(OSError):
                os.unlink(tmp_path)
