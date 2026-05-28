# DB Backup Safety Floor Design

**Date:** 2026-05-28
**Status:** Approved (lightweight spec — direct implementation, no writing-plans phase)
**Charter alignment:** Top-3 priority #1 — Operational floor (unlock condition #5)

## Problem

The single canonical store `/data/marketpulse.db` (Synology NAS bind mount `/volume1/docker/marketpulse/data/marketpulse.db`) has no backup. Container OOM kill, sqlite corruption, accidental file delete, or volume issue = total loss of trade history + AI evaluations + paper trading state + broker reconciliation snapshots. Charter mandates DB backup before any future live-trading unlock.

## Scope

A single APScheduler job that:
1. Writes a consistent snapshot of `marketpulse.db` to a dated file in `/data/backups/`
2. Verifies the snapshot's SQLite integrity
3. Updates `/data/backups/latest.json` manifest
4. Prunes backups older than 7 days

PR1 ships exactly this. No endpoint, no UI, no report — those belong to PR2.

## Out of scope

- `/lab/charter-metrics` endpoint (PR2)
- Weekly charter_review markdown report (PR2)
- Off-NAS replication (S3, rsync, Tailscale to dev machine) — explicitly deferred; same-volume backup defends against 99% of expected failure modes (process death, sqlite corruption, accidental delete)
- Backup encryption — single-user LAN-only deployment
- Prometheus / Grafana / external metrics export — charter explicitly rejects this layer
- Restore tooling — manifest gives operator everything needed to restore manually; CLI restore is YAGNI

## Backup destination

- Source: `/data/marketpulse.db`
- Backups directory: `/data/backups/` (created if missing, mode 0o755)
- Snapshot filename: `marketpulse-YYYY-MM-DD.db` (one per day; same-day re-run overwrites the day's file via SQLite's native backup)
- Manifest: `/data/backups/latest.json` (single file, atomically replaced each run via tmp + rename)

## Backup method

Use Python's `sqlite3.Connection.backup()` API. This is the canonical online backup primitive — it copies pages from the live DB to the destination DB while the live DB continues serving readers/writers. NO `sqlite3` CLI subprocess, NO file copy.

```python
src = sqlite3.connect(SRC_PATH)
dst = sqlite3.connect(DST_PATH)
src.backup(dst)  # online consistent snapshot
dst.close()
src.close()
```

## Integrity check

After writing, open the destination read-only and run:

```python
result = dst.execute("PRAGMA integrity_check").fetchone()[0]
# expect "ok"; any other string = corrupted snapshot
```

## Schedule

- APScheduler `CronTrigger(hour=9, minute=0, timezone="UTC")`
- 09:00 UTC = 04:00–05:00 NY (DST-dependent) = pre-market low-traffic window
- `misfire_grace_time=None` + `coalesce=True` — consistent with other daily critical jobs (paper_trading_tick, outcome_computation, flex_sync). A missed run during a deploy gets caught up on next start.
- Mon-Sun (no `day_of_week` filter) — DB writes happen daily including weekends (audit events from container ops, scheduled jobs)

## Manifest schema (lock — consumed by PR2)

### Success

```json
{
  "status": "ok",
  "timestamp": "2026-05-28T09:00:00.123456Z",
  "source": "/data/marketpulse.db",
  "destination": "/data/backups/marketpulse-2026-05-28.db",
  "size_bytes": 12345678,
  "integrity_check": "ok",
  "duration_ms": 432,
  "error": null
}
```

### Failure

```json
{
  "status": "failed",
  "timestamp": "2026-05-28T09:00:00.987654Z",
  "source": "/data/marketpulse.db",
  "destination": null,
  "size_bytes": null,
  "integrity_check": "not_run",
  "duration_ms": 85,
  "error": "<class name>: <message>"
}
```

Top-level `status` covers ALL failure modes — source open fail, destination write fail, disk full, integrity_check returned not-ok, even manifest write fail (logged separately). PR2 only needs to read `status` to know whether the most recent backup is usable.

## Retention

After successful backup, delete files matching `/data/backups/marketpulse-*.db` whose `mtime` is older than 7 days. Implementation:

```python
cutoff = datetime.now(UTC) - timedelta(days=7)
for path in BACKUPS_DIR.glob("marketpulse-*.db"):
    if datetime.fromtimestamp(path.stat().st_mtime, tz=UTC) < cutoff:
        path.unlink()
        log.info("db_backup_pruned", path=str(path))
```

A failed backup does NOT trigger pruning — we keep older successful backups available even if today's run failed.

## Failure behavior

- Any exception caught inside `run_backup()` → write a `status="failed"` manifest with the error string, log a WARNING, do NOT re-raise. The scheduler must not crash because of a backup failure.
- If the manifest write itself fails (disk full at exactly the moment we try to update `latest.json`), log a CRITICAL and let the scheduler thread swallow the exception — the next day's run will retry.
- If the destination disk is full → `OSError` → manifest failure → operator notice via `/lab/charter-metrics` (PR2). We do NOT delete old backups to "make room" — old data is more valuable than new data in this scenario.

## Module layout

```
marketpulse/ops/
├── __init__.py
└── backup.py             # ~100 LOC

marketpulse/scheduler/jobs.py
                          # +1 new entry: run_db_backup
```

`backup.py` exports:

```python
@dataclass(frozen=True)
class BackupResult:
    status: Literal["ok", "failed"]
    timestamp: datetime
    source: str
    destination: str | None
    size_bytes: int | None
    integrity_check: Literal["ok", "failed", "not_run"]
    duration_ms: int
    error: str | None

def run_backup(
    *, source: Path, backups_dir: Path,
) -> BackupResult: ...

def prune_old_backups(
    *, backups_dir: Path, keep_days: int = 7,
) -> list[Path]: ...

def write_manifest(
    *, manifest_path: Path, result: BackupResult,
) -> None: ...
```

Scheduler glue (in `scheduler/jobs.py`):

```python
def run_db_backup() -> None:
    settings = get_settings()
    src = Path(settings.database_url.replace("sqlite:///", ""))
    backups_dir = src.parent / "backups"
    result = run_backup(source=src, backups_dir=backups_dir)
    write_manifest(manifest_path=backups_dir / "latest.json", result=result)
    if result.status == "ok":
        pruned = prune_old_backups(backups_dir=backups_dir)
        log.info("db_backup_done", destination=result.destination, size=result.size_bytes, pruned=len(pruned))
    else:
        log.warning("db_backup_failed", error=result.error)
```

## Tests

- `# Layer: pure` `test_run_backup_writes_consistent_snapshot` — synthetic source DB with 3 known rows, backup, reopen destination, assert rows match.
- `# Layer: pure` `test_run_backup_integrity_check_passes_on_fresh_db` — verify "ok".
- `# Layer: pure` `test_run_backup_integrity_check_fails_on_corrupted_dest` — pre-write garbage bytes to destination path, backup recreates cleanly; or pre-write a sqlite file with deliberately invalid pages, integrity_check fails, manifest reflects failure.
- `# Layer: pure` `test_run_backup_handles_missing_source` — source path nonexistent → `status="failed"`, descriptive error.
- `# Layer: pure` `test_prune_old_backups_keeps_recent_only` — create 10 files with mtime spread across 14 days; prune; assert 7 remain.
- `# Layer: pure` `test_write_manifest_atomic` — write twice with different content; reading mid-write would not see truncated file (use tmp + os.replace).
- `# Layer: stateful` `test_scheduler_registers_db_backup_job` — `build_scheduler()` has `db_backup` job, `misfire_grace_time=None`, `coalesce=True`.

Total: ~7 tests.

## Verification (post-deploy)

After deploy, manually trigger once:

```bash
docker exec marketpulse python -c "from marketpulse.scheduler.jobs import run_db_backup; run_db_backup()"
docker exec marketpulse cat /data/backups/latest.json
docker exec marketpulse ls -lh /data/backups/
```

Expected:
- `latest.json` shows `status="ok"`, `integrity_check="ok"`
- `marketpulse-YYYY-MM-DD.db` exists, size > 0
- File size roughly matches source DB size (within 10%)

## Acceptance criteria

PR1 is shippable when:
1. `run_db_backup` registered in scheduler
2. Manual invocation produces a valid `.db` snapshot + matching manifest
3. Restored snapshot opens cleanly in `sqlite3` and `PRAGMA integrity_check` returns "ok"
4. All 7 tests pass
5. `uv run pytest` full suite passes
6. `uv run ruff check` clean

## Charter cross-reference

This PR satisfies:
- Charter top-3 priority #1 partial (DB backup half — observability half is PR2)
- Operational unlock condition #5 ("DB backup strategy in place")
- Phase 5e § 10 implicit concern about state durability

This PR does NOT satisfy:
- North-star metric measurement (PR2)
- Diagnostic metrics endpoint (PR2)
- Off-NAS DR (future hardening, not on critical path)
