# PR2 — `/lab/charter-metrics` Skeleton Design

> Charter top-3 priority #1, second half. See `docs/CHARTER.md` § "Top 3 priorities" and
> `docs/superpowers/specs/2026-05-28-db-backup-design.md` (PR1, shipped as PR #128).

## Purpose

Freeze the **operational contract** for charter-mandated observability — not the metric semantics.
PR2 ships one JSON endpoint that exposes backup health (from the manifest written by PR #128)
and reserves the API shape for `north_star` and `diagnostics` sections that PR3+ will fill in.

This is intentionally **machine-readable only**. No HTML, no chart, no HTMX. The point is to give
PR3 (weekly `charter_review` report), future alerts, and future UI a single stable contract to
consume, before the underlying metric semantics are pinned down.

## Non-goals (deferred to PR3+)

- `north_star` semantics & computation (`paper_portfolio_excess_return_vs_spy_90d`)
- `diagnostics` semantics (`tick_success_rate`, `order_rejection_rate`, `ai_verdict_hit_rate_h5`, etc.)
- Weekly `charter_review` markdown report into `/recaps`
- HTML/UI surface for `/lab/charter-metrics`
- Alert hooks (e.g. paging when `is_stale=true`)
- Off-NAS replication, PITR, or any backup-strategy change

## Architecture & Boundaries

**Pure layer:** `marketpulse/ops/charter_metrics.py`
- No DB. No HTTP. Only `manifest_path.read_text()` for I/O.
- Single entry: `build_charter_metrics(*, manifest_path, now, backup_unavailable_reason=None) -> dict`.
- `now` is injectable. Module never reads the system clock directly.
- Never raises. All failure modes produce a well-formed contract dict with `status="missing"`
  and a descriptive `error`.

**Web layer:** `marketpulse/web/routes/charter.py`
- One route: `GET /lab/charter-metrics`.
- `Depends(require_auth)` consistent with other `/lab/*` routes.
- Parses `settings.database_url` via `sqlalchemy.engine.url.make_url`; resolves manifest path as
  `<dbpath>.parent / "backups" / "latest.json"`.
- **SQLite detection uses `drivername.startswith("sqlite")`** so both `sqlite` and `sqlite+pysqlite`
  drivers are treated as SQLite.
- **Path resolution invariant:** `database_url` must resolve to the same absolute filesystem path
  used by PR1's `run_db_backup` job. Both call sites use `Path(parsed.database).resolve()`, so
  relative URLs are resolved against the **same process working directory** (the FastAPI app and
  the APScheduler job share the same container CWD). If a deployment ever splits these into
  different working directories, PR2 will look in the wrong place; pin both to absolute paths in
  that case.
- Non-SQLite URL → calls `build_charter_metrics(..., backup_unavailable_reason="sqlite database_url required for backup manifest discovery")`.
- Returns the dict; FastAPI auto-serializes to `application/json`.
- HTTP 200 on **every** outcome (ok / failed / missing). This is observability, not liveness.

**Wired:** `app.include_router(charter.router)` in `marketpulse/web/main.py` alongside existing lab routers.

## Data Contract v1 (locked)

```json
{
  "schema_version": 1,
  "timestamp": "2026-05-28T14:00:00.000000+00:00",
  "operational_floor": {
    "backup": {
      "status": "ok",
      "is_stale": false,
      "stale_after_hours": 25,
      "last_backup_at": "2026-05-28T09:00:00+00:00",
      "source": "/data/marketpulse.db",
      "destination": "/data/backups/marketpulse-2026-05-28.db",
      "size_bytes": 1359872,
      "integrity_check": "ok",
      "duration_ms": 134,
      "error": null
    }
  },
  "north_star": {
    "status": "not_implemented"
  },
  "diagnostics": {
    "status": "not_implemented"
  }
}
```

### Locked rules

- `schema_version: 1` — bumped only on breaking change. PR3 may **add** fields without bumping.
- `timestamp` is the request-handling UTC time (injected `now` in tests), not the manifest's timestamp.
- `status` ∈ `{"ok", "failed", "missing"}` — orthogonal to `is_stale`.
- `is_stale` is `true` when manifest is missing OR `(now - last_backup_at) > stale_after_hours`.
- `stale_after_hours: 25` exposed as a constant so consumers don't re-derive the threshold.
- `north_star.status` and `diagnostics.status` are the literal string `"not_implemented"` — placeholder marker reserved for PR3.
- Raw manifest fields are preserved when available, but normalized into the v1 contract shape.
  Derived fields (`is_stale`, `stale_after_hours`) are added by `charter_metrics.py`.

### Failure-mode normalization

| Manifest state                            | `status`    | `last_backup_at` | `is_stale` | `error`                                           |
|-------------------------------------------|-------------|------------------|------------|---------------------------------------------------|
| File missing                              | `"missing"` | `null`           | `true`     | `"manifest file not found"`                       |
| File unreadable (permission, dir, etc.)   | `"missing"` | `null`           | `true`     | `"manifest unreadable: <exception>"`              |
| JSON invalid                              | `"missing"` | `null`           | `true`     | `"manifest json invalid: <exception>"`            |
| Manifest missing required key             | `"missing"` | `null`           | `true`     | `"manifest malformed: missing key '<name>'"`      |
| Manifest `timestamp` value is not ISO-8601 | `"missing"` | `null`           | `true`     | `"manifest malformed: invalid timestamp"`         |
| Manifest `status="ok"`, age ≤ 25h         | `"ok"`      | from manifest    | `false`    | `null`                                            |
| Manifest `status="ok"`, age > 25h         | `"ok"`      | from manifest    | `true`     | `null`                                            |
| Manifest `status="failed"`, age ≤ 25h     | `"failed"`  | from manifest    | `false`    | passed through from manifest                      |
| Manifest `status="failed"`, age > 25h     | `"failed"`  | from manifest    | `true`     | passed through from manifest                      |
| `backup_unavailable_reason` kwarg passed  | `"missing"` | `null`           | `true`     | the reason string (manifest is NOT read)          |

Required keys checked for "malformed" detection: `timestamp`, `status`, `integrity_check`, `duration_ms`.
Optional keys (`source`, `destination`, `size_bytes`, `error`) default to `null` if absent.
`timestamp` is additionally validated as parseable; the implementation accepts both
`"2026-05-28T09:00:00+00:00"` and `"2026-05-28T09:00:00Z"` by normalizing a trailing `Z` to
`+00:00` before calling `datetime.fromisoformat`. A non-parseable value triggers the
`"manifest malformed: invalid timestamp"` failure mode. PR1 currently writes `+00:00` form, but
the `Z` shim is kept for forward compatibility with external tooling that might rewrite the
manifest.

## Module Layout & Interfaces

### `marketpulse/ops/charter_metrics.py`

```python
# Layer: pure
"""Charter metrics contract — v1 skeleton (operational floor only)."""

SCHEMA_VERSION = 1
STALE_AFTER_HOURS = 25

def build_charter_metrics(
    *,
    manifest_path: Path,
    now: datetime,
    backup_unavailable_reason: str | None = None,
) -> dict[str, Any]:
    """Build the v1 contract dict. Never raises."""

def _read_manifest(manifest_path: Path) -> tuple[dict | None, str | None]:
    """Returns (parsed_dict, error_message). Both may be None on success."""

def _build_backup_section(
    manifest: dict | None,
    now: datetime,
    explicit_error: str | None,
) -> dict[str, Any]:
    """Normalize manifest into v1 contract shape + derive is_stale."""
```

### `marketpulse/web/routes/charter.py`

```python
# Layer: web
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.engine.url import make_url

from marketpulse.config import get_settings
from marketpulse.ops.charter_metrics import build_charter_metrics
from marketpulse.web.deps import require_auth

router = APIRouter()

_NON_SQLITE_REASON = "sqlite database_url required for backup manifest discovery"


@router.get("/lab/charter-metrics")
def lab_charter_metrics(
    _: None = Depends(require_auth),
) -> dict[str, Any]:
    settings = get_settings()
    parsed = make_url(settings.database_url)
    if not parsed.drivername.startswith("sqlite") or not parsed.database:
        return build_charter_metrics(
            manifest_path=Path("/dev/null"),
            now=datetime.now(UTC),
            backup_unavailable_reason=_NON_SQLITE_REASON,
        )
    manifest_path = Path(parsed.database).resolve().parent / "backups" / "latest.json"
    return build_charter_metrics(
        manifest_path=manifest_path,
        now=datetime.now(UTC),
    )
```

## Testing

### Unit tests — `tests/ops/test_charter_metrics.py`

| Test                                       | Asserts                                                                       |
|--------------------------------------------|-------------------------------------------------------------------------------|
| `test_missing_manifest`                    | `status="missing"`, `is_stale=true`, `error="manifest file not found"`        |
| `test_unreadable_manifest`                 | path is a directory → `status="missing"`, error contains `"unreadable"`       |
| `test_json_invalid`                        | manifest body is `{not json}` → `status="missing"`, error contains `"json invalid"` |
| `test_malformed_missing_key`               | manifest missing `timestamp` → `status="missing"`, error contains `"malformed: missing key"` |
| `test_malformed_missing_duration_ms`       | manifest missing `duration_ms` → `status="missing"`, error contains `"malformed: missing key 'duration_ms'"` |
| `test_malformed_invalid_timestamp`         | manifest `timestamp="bad-date"` → `status="missing"`, error contains `"invalid timestamp"` |
| `test_ok_recent`                           | valid manifest, `now = manifest_time + 1h` → `status="ok"`, `is_stale=false`  |
| `test_ok_stale`                            | valid manifest, `now = manifest_time + 26h` → `status="ok"`, `is_stale=true`  |
| `test_failed_recent`                       | manifest `status="failed"`, recent → outer `status="failed"`, `is_stale=false`, error passed through |
| `test_failed_stale`                        | manifest `status="failed"`, >25h old → `status="failed"`, `is_stale=true`     |
| `test_backup_unavailable_reason`           | reason kwarg set → `status="missing"`, error=reason, `is_stale=true`, manifest NOT read |
| `test_schema_v1_lock`                      | top-level required keys present (subset check, PR3-expandable); `operational_floor.backup` keys exact-match the locked set |
| `test_north_star_diagnostics_placeholders` | both sections present with `status="not_implemented"`                         |
| `test_timestamp_uses_injected_now`         | response `timestamp == injected_now.isoformat()`                              |

### Route tests — `tests/web/test_charter_route.py`

| Test                                        | Asserts                                                                  |
|---------------------------------------------|--------------------------------------------------------------------------|
| `test_endpoint_returns_200_with_no_backup_dir` | fresh DB, no `/data/backups` → 200 + `status="missing"`              |
| `test_endpoint_returns_200_when_backup_failed` | seed failed manifest → 200 + `status="failed"`                       |
| `test_endpoint_requires_auth`               | unauthenticated → 401/redirect (consistent with other `/lab/*`)         |
| `test_endpoint_non_sqlite_url`              | monkeypatch settings to postgres URL → `status="missing"`, error mentions sqlite |
| `test_endpoint_content_type_json`           | `application/json` content type, body parses as JSON                    |

### Integration smoke (post-deploy, manual)

```bash
curl -sf -b cookies.txt http://localhost:8088/lab/charter-metrics \
  | jq '.operational_floor.backup.status'
# expect: "ok"
```

## Acceptance Criteria

1. `ruff check .` clean.
2. Full `pytest` suite green.
3. `tests/ops/test_charter_metrics.py` covers all 14 unit cases above.
4. `tests/web/test_charter_route.py` covers all 5 route cases above.
5. Manual smoke: endpoint returns HTTP 200 with `operational_floor.backup.status="ok"`
   on the running container after the next 09:00 UTC cron fire (or after a manual `run_db_backup()`).
6. Spec and code never reference HTML, HTMX, or a Jinja template for this route.

### Schema lock policy

`test_schema_v1_lock` uses a **subset check** at the top level (asserts `schema_version`,
`timestamp`, `operational_floor`, `north_star`, `diagnostics` are all present) and an **exact-set
check** on `operational_floor.backup` keys. This lets PR3 add new top-level sections (e.g.
`audit`, `slo`) without breaking the test, while still preventing accidental drift of the
backup contract.

## Forward Compatibility Notes

- PR3 weekly `charter_review` consumes the **same** contract — it reads `latest.json` indirectly via
  this endpoint (or via the same `build_charter_metrics` helper). No duplicate staleness logic.
- PR3 will replace `north_star.status` and `diagnostics.status` placeholders with real fields. Adding
  fields is non-breaking and does **not** bump `schema_version`.
- If a future change makes the response shape backwards-incompatible (e.g. renaming `backup` →
  `db_backup`), bump to `schema_version: 2` and keep v1 supported for one release window.
