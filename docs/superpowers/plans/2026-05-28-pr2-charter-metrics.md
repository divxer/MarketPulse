# PR2 — `/lab/charter-metrics` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a JSON `GET /lab/charter-metrics` endpoint that exposes backup health from PR #128's manifest under a v1 schema, with reserved placeholders for `north_star` and `diagnostics` sections that PR3 will fill in.

**Architecture:** One pure module (`ops/charter_metrics.py`) builds the contract dict; one thin web route (`routes/charter.py`) resolves the manifest path from `settings.database_url` and returns the dict (FastAPI auto-serializes to JSON). HTTP 200 on every outcome — failed/missing backups are data, not errors.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy `make_url`, pytest, `datetime.fromisoformat`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-28-pr2-charter-metrics-design.md` (commit `cadae13`).

---

## File Structure

| Path | Layer | Responsibility |
|---|---|---|
| `marketpulse/ops/charter_metrics.py` (new) | pure | Build v1 contract dict; never raise |
| `marketpulse/web/routes/charter.py` (new) | web | Resolve manifest path; call builder; return dict |
| `marketpulse/web/main.py` (modify) | web | Import + `include_router(charter.router)` |
| `tests/ops/test_charter_metrics.py` (new) | test | 14 unit tests covering all failure modes |
| `tests/web/test_charter_route.py` (new) | test | 5 route-level tests |

---

## Task 1: Module scaffold + constants + happy-path "ok recent"

**Files:**
- Create: `marketpulse/ops/charter_metrics.py`
- Create: `tests/ops/test_charter_metrics.py`

- [ ] **Step 1: Write the failing happy-path test**

Create `tests/ops/test_charter_metrics.py`:

```python
# Layer: pure
"""Charter metrics v1 contract — PR2 (Charter top-3 #1, second half).

See docs/superpowers/specs/2026-05-28-pr2-charter-metrics-design.md.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from marketpulse.ops.charter_metrics import (
    SCHEMA_VERSION,
    STALE_AFTER_HOURS,
    build_charter_metrics,
)


def _write_manifest(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _ok_manifest(*, when: datetime) -> dict:
    return {
        "status": "ok",
        "timestamp": when.isoformat(),
        "source": "/data/marketpulse.db",
        "destination": "/data/backups/marketpulse-2026-05-28.db",
        "size_bytes": 1359872,
        "integrity_check": "ok",
        "duration_ms": 134,
        "error": None,
    }


def test_constants_locked():
    assert SCHEMA_VERSION == 1
    assert STALE_AFTER_HOURS == 25


def test_ok_recent(tmp_path):
    manifest_time = datetime(2026, 5, 28, 9, 0, 0, tzinfo=UTC)
    now = manifest_time + timedelta(hours=1)
    manifest_path = tmp_path / "latest.json"
    _write_manifest(manifest_path, _ok_manifest(when=manifest_time))

    result = build_charter_metrics(manifest_path=manifest_path, now=now)

    backup = result["operational_floor"]["backup"]
    assert backup["status"] == "ok"
    assert backup["is_stale"] is False
    assert backup["stale_after_hours"] == 25
    assert backup["last_backup_at"] == manifest_time.isoformat()
    assert backup["source"] == "/data/marketpulse.db"
    assert backup["destination"] == "/data/backups/marketpulse-2026-05-28.db"
    assert backup["size_bytes"] == 1359872
    assert backup["integrity_check"] == "ok"
    assert backup["duration_ms"] == 134
    assert backup["error"] is None
    assert result["timestamp"] == now.isoformat()
    assert result["schema_version"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ops/test_charter_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketpulse.ops.charter_metrics'`

- [ ] **Step 3: Create the module with minimum implementation**

Create `marketpulse/ops/charter_metrics.py`:

```python
# Layer: pure
"""Charter metrics v1 contract — PR2 of Charter top-3 priority #1.

See docs/superpowers/specs/2026-05-28-pr2-charter-metrics-design.md.

Pure module. No DB. No network. Only reads `manifest_path`. Never raises —
every failure mode normalizes into a well-formed v1 contract dict so the
endpoint and PR3's weekly report can both consume the same shape.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
STALE_AFTER_HOURS = 25

_REQUIRED_MANIFEST_KEYS: tuple[str, ...] = (
    "timestamp", "status", "integrity_check", "duration_ms",
)
_OPTIONAL_MANIFEST_KEYS: tuple[str, ...] = (
    "source", "destination", "size_bytes", "error",
)


def build_charter_metrics(
    *,
    manifest_path: Path,
    now: datetime,
    backup_unavailable_reason: str | None = None,
) -> dict[str, Any]:
    """Build the v1 charter-metrics contract dict. Never raises."""
    backup = _build_backup_section(
        manifest_path=manifest_path,
        now=now,
        backup_unavailable_reason=backup_unavailable_reason,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "timestamp": now.isoformat(),
        "operational_floor": {"backup": backup},
        "north_star": {"status": "not_implemented"},
        "diagnostics": {"status": "not_implemented"},
    }


def _build_backup_section(
    *,
    manifest_path: Path,
    now: datetime,
    backup_unavailable_reason: str | None,
) -> dict[str, Any]:
    if backup_unavailable_reason is not None:
        return _missing_backup(error=backup_unavailable_reason)

    manifest, read_error = _read_manifest(manifest_path)
    if read_error is not None:
        return _missing_backup(error=read_error)

    # manifest is a dict at this point
    last_backup_at = _parse_timestamp(manifest["timestamp"])
    is_stale = (now - last_backup_at) > timedelta(hours=STALE_AFTER_HOURS)
    return {
        "status": manifest["status"],
        "is_stale": is_stale,
        "stale_after_hours": STALE_AFTER_HOURS,
        "last_backup_at": manifest["timestamp"],
        "source": manifest.get("source"),
        "destination": manifest.get("destination"),
        "size_bytes": manifest.get("size_bytes"),
        "integrity_check": manifest["integrity_check"],
        "duration_ms": manifest["duration_ms"],
        "error": manifest.get("error"),
    }


def _missing_backup(*, error: str) -> dict[str, Any]:
    return {
        "status": "missing",
        "is_stale": True,
        "stale_after_hours": STALE_AFTER_HOURS,
        "last_backup_at": None,
        "source": None,
        "destination": None,
        "size_bytes": None,
        "integrity_check": None,
        "duration_ms": None,
        "error": error,
    }


def _read_manifest(manifest_path: Path) -> tuple[dict | None, str | None]:
    """Return (parsed_dict, error). Both None means success; on failure
    parsed_dict is None and error is a human-readable string."""
    if not manifest_path.exists():
        return None, "manifest file not found"
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"manifest unreadable: {type(exc).__name__}: {exc}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"manifest json invalid: {exc}"
    if not isinstance(parsed, dict):
        return None, "manifest json invalid: root is not an object"
    for key in _REQUIRED_MANIFEST_KEYS:
        if key not in parsed:
            return None, f"manifest malformed: missing key '{key}'"
    # timestamp parse-validation
    try:
        _parse_timestamp(parsed["timestamp"])
    except ValueError:
        return None, "manifest malformed: invalid timestamp"
    return parsed, None


def _parse_timestamp(value: str) -> datetime:
    """Parse ISO-8601 timestamp, normalizing trailing 'Z' to '+00:00'."""
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized)
```

Also create `marketpulse/ops/__init__.py` is already present from PR #128, no change needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ops/test_charter_metrics.py -v`
Expected: PASS — 2 tests (`test_constants_locked`, `test_ok_recent`)

- [ ] **Step 5: Commit**

```bash
git add marketpulse/ops/charter_metrics.py tests/ops/test_charter_metrics.py
git commit -m "feat(ops): charter_metrics v1 contract skeleton (ok-recent path)"
```

---

## Task 2: Missing-manifest failure modes

**Files:**
- Modify: `tests/ops/test_charter_metrics.py`

- [ ] **Step 1: Add failing tests for the four missing/unreadable/invalid/malformed modes**

Append to `tests/ops/test_charter_metrics.py`:

```python
def test_missing_manifest(tmp_path):
    manifest_path = tmp_path / "nope.json"
    now = datetime(2026, 5, 28, 14, 0, 0, tzinfo=UTC)

    result = build_charter_metrics(manifest_path=manifest_path, now=now)

    backup = result["operational_floor"]["backup"]
    assert backup["status"] == "missing"
    assert backup["is_stale"] is True
    assert backup["last_backup_at"] is None
    assert backup["error"] == "manifest file not found"


def test_unreadable_manifest(tmp_path):
    # A directory at the manifest path is "unreadable" as a file.
    manifest_path = tmp_path / "latest.json"
    manifest_path.mkdir()
    now = datetime(2026, 5, 28, 14, 0, 0, tzinfo=UTC)

    result = build_charter_metrics(manifest_path=manifest_path, now=now)

    backup = result["operational_floor"]["backup"]
    assert backup["status"] == "missing"
    assert backup["is_stale"] is True
    assert "unreadable" in backup["error"]


def test_json_invalid(tmp_path):
    manifest_path = tmp_path / "latest.json"
    manifest_path.write_text("{not valid json", encoding="utf-8")
    now = datetime(2026, 5, 28, 14, 0, 0, tzinfo=UTC)

    result = build_charter_metrics(manifest_path=manifest_path, now=now)

    backup = result["operational_floor"]["backup"]
    assert backup["status"] == "missing"
    assert "json invalid" in backup["error"]


def test_malformed_missing_key(tmp_path):
    # Missing required key `timestamp`
    payload = {
        "status": "ok", "integrity_check": "ok", "duration_ms": 100,
    }
    manifest_path = tmp_path / "latest.json"
    _write_manifest(manifest_path, payload)
    now = datetime(2026, 5, 28, 14, 0, 0, tzinfo=UTC)

    result = build_charter_metrics(manifest_path=manifest_path, now=now)

    backup = result["operational_floor"]["backup"]
    assert backup["status"] == "missing"
    assert "malformed: missing key 'timestamp'" in backup["error"]


def test_malformed_missing_duration_ms(tmp_path):
    payload = {
        "status": "ok",
        "timestamp": "2026-05-28T09:00:00+00:00",
        "integrity_check": "ok",
    }
    manifest_path = tmp_path / "latest.json"
    _write_manifest(manifest_path, payload)
    now = datetime(2026, 5, 28, 14, 0, 0, tzinfo=UTC)

    result = build_charter_metrics(manifest_path=manifest_path, now=now)

    backup = result["operational_floor"]["backup"]
    assert backup["status"] == "missing"
    assert "missing key 'duration_ms'" in backup["error"]
```

- [ ] **Step 2: Run tests to verify they pass (already covered by Task 1's implementation)**

Run: `pytest tests/ops/test_charter_metrics.py -v`
Expected: PASS — all 7 tests so far

If any fail, debug the implementation before continuing.

- [ ] **Step 3: Commit**

```bash
git add tests/ops/test_charter_metrics.py
git commit -m "test(charter_metrics): missing/unreadable/json-invalid/malformed-key modes"
```

---

## Task 3: Invalid-timestamp failure mode + Z-suffix support

**Files:**
- Modify: `tests/ops/test_charter_metrics.py`

- [ ] **Step 1: Add failing tests for timestamp edge cases**

Append to `tests/ops/test_charter_metrics.py`:

```python
def test_malformed_invalid_timestamp(tmp_path):
    payload = {
        "status": "ok",
        "timestamp": "bad-date",
        "integrity_check": "ok",
        "duration_ms": 100,
    }
    manifest_path = tmp_path / "latest.json"
    _write_manifest(manifest_path, payload)
    now = datetime(2026, 5, 28, 14, 0, 0, tzinfo=UTC)

    result = build_charter_metrics(manifest_path=manifest_path, now=now)

    backup = result["operational_floor"]["backup"]
    assert backup["status"] == "missing"
    assert "invalid timestamp" in backup["error"]


def test_z_suffix_timestamp_accepted(tmp_path):
    # External tooling may rewrite timestamp with trailing 'Z'.
    payload = {
        "status": "ok",
        "timestamp": "2026-05-28T09:00:00Z",
        "integrity_check": "ok",
        "duration_ms": 100,
    }
    manifest_path = tmp_path / "latest.json"
    _write_manifest(manifest_path, payload)
    now = datetime(2026, 5, 28, 10, 0, 0, tzinfo=UTC)

    result = build_charter_metrics(manifest_path=manifest_path, now=now)

    backup = result["operational_floor"]["backup"]
    assert backup["status"] == "ok"
    assert backup["is_stale"] is False
    assert backup["last_backup_at"] == "2026-05-28T09:00:00Z"  # preserved verbatim
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/ops/test_charter_metrics.py -v -k "timestamp"`
Expected: PASS — both new tests pass (Task 1's `_parse_timestamp` already handles `Z`)

- [ ] **Step 3: Commit**

```bash
git add tests/ops/test_charter_metrics.py
git commit -m "test(charter_metrics): invalid-timestamp + Z-suffix compatibility"
```

---

## Task 4: `is_stale` derivation (ok stale, failed recent, failed stale)

**Files:**
- Modify: `tests/ops/test_charter_metrics.py`

- [ ] **Step 1: Add failing tests for is_stale + failed-status pass-through**

Append to `tests/ops/test_charter_metrics.py`:

```python
def test_ok_stale(tmp_path):
    manifest_time = datetime(2026, 5, 27, 9, 0, 0, tzinfo=UTC)
    now = manifest_time + timedelta(hours=26)  # > 25h threshold
    manifest_path = tmp_path / "latest.json"
    _write_manifest(manifest_path, _ok_manifest(when=manifest_time))

    result = build_charter_metrics(manifest_path=manifest_path, now=now)

    backup = result["operational_floor"]["backup"]
    assert backup["status"] == "ok"
    assert backup["is_stale"] is True


def test_failed_recent(tmp_path):
    manifest_time = datetime(2026, 5, 28, 9, 0, 0, tzinfo=UTC)
    now = manifest_time + timedelta(hours=1)
    failed_payload = {
        "status": "failed",
        "timestamp": manifest_time.isoformat(),
        "source": "/data/marketpulse.db",
        "destination": None,
        "size_bytes": None,
        "integrity_check": "not_run",
        "duration_ms": 42,
        "error": "OSError: disk full",
    }
    manifest_path = tmp_path / "latest.json"
    _write_manifest(manifest_path, failed_payload)

    result = build_charter_metrics(manifest_path=manifest_path, now=now)

    backup = result["operational_floor"]["backup"]
    assert backup["status"] == "failed"
    assert backup["is_stale"] is False
    assert backup["error"] == "OSError: disk full"
    assert backup["integrity_check"] == "not_run"


def test_failed_stale(tmp_path):
    manifest_time = datetime(2026, 5, 26, 9, 0, 0, tzinfo=UTC)
    now = manifest_time + timedelta(hours=48)
    failed_payload = {
        "status": "failed",
        "timestamp": manifest_time.isoformat(),
        "source": "/data/marketpulse.db",
        "destination": None,
        "size_bytes": None,
        "integrity_check": "not_run",
        "duration_ms": 5,
        "error": "permission denied",
    }
    manifest_path = tmp_path / "latest.json"
    _write_manifest(manifest_path, failed_payload)

    result = build_charter_metrics(manifest_path=manifest_path, now=now)

    backup = result["operational_floor"]["backup"]
    assert backup["status"] == "failed"
    assert backup["is_stale"] is True
    assert backup["error"] == "permission denied"
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/ops/test_charter_metrics.py -v`
Expected: PASS — all 12 tests so far

- [ ] **Step 3: Commit**

```bash
git add tests/ops/test_charter_metrics.py
git commit -m "test(charter_metrics): is_stale + failed-status pass-through"
```

---

## Task 5: `backup_unavailable_reason` kwarg + schema lock + placeholders + injected-now

**Files:**
- Modify: `tests/ops/test_charter_metrics.py`

- [ ] **Step 1: Add the remaining 4 unit tests**

Append to `tests/ops/test_charter_metrics.py`:

```python
def test_backup_unavailable_reason(tmp_path):
    # Even if manifest exists, reason kwarg short-circuits the read.
    manifest_path = tmp_path / "latest.json"
    _write_manifest(manifest_path, _ok_manifest(
        when=datetime(2026, 5, 28, 9, 0, 0, tzinfo=UTC),
    ))
    now = datetime(2026, 5, 28, 14, 0, 0, tzinfo=UTC)

    result = build_charter_metrics(
        manifest_path=manifest_path,
        now=now,
        backup_unavailable_reason="sqlite database_url required for backup manifest discovery",
    )

    backup = result["operational_floor"]["backup"]
    assert backup["status"] == "missing"
    assert backup["is_stale"] is True
    assert backup["error"] == (
        "sqlite database_url required for backup manifest discovery"
    )
    # Confirm manifest fields were NOT read (would have been "ok").
    assert backup["last_backup_at"] is None


def test_schema_v1_lock(tmp_path):
    """Top-level: required-subset (PR3-expandable).
    operational_floor.backup: exact-set (locked)."""
    manifest_time = datetime(2026, 5, 28, 9, 0, 0, tzinfo=UTC)
    now = manifest_time + timedelta(hours=1)
    manifest_path = tmp_path / "latest.json"
    _write_manifest(manifest_path, _ok_manifest(when=manifest_time))

    result = build_charter_metrics(manifest_path=manifest_path, now=now)

    required_top_level = {
        "schema_version", "timestamp",
        "operational_floor", "north_star", "diagnostics",
    }
    assert required_top_level.issubset(result.keys())

    expected_backup_keys = {
        "status", "is_stale", "stale_after_hours",
        "last_backup_at", "source", "destination", "size_bytes",
        "integrity_check", "duration_ms", "error",
    }
    assert set(result["operational_floor"]["backup"].keys()) == expected_backup_keys


def test_north_star_diagnostics_placeholders(tmp_path):
    manifest_path = tmp_path / "missing.json"
    now = datetime(2026, 5, 28, 14, 0, 0, tzinfo=UTC)

    result = build_charter_metrics(manifest_path=manifest_path, now=now)

    assert result["north_star"] == {"status": "not_implemented"}
    assert result["diagnostics"] == {"status": "not_implemented"}


def test_timestamp_uses_injected_now(tmp_path):
    manifest_path = tmp_path / "missing.json"
    now = datetime(2026, 5, 28, 14, 30, 45, 123456, tzinfo=UTC)

    result = build_charter_metrics(manifest_path=manifest_path, now=now)

    assert result["timestamp"] == now.isoformat()
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/ops/test_charter_metrics.py -v`
Expected: PASS — all 16 tests (2 constants + 14 spec cases)

- [ ] **Step 3: Commit**

```bash
git add tests/ops/test_charter_metrics.py
git commit -m "test(charter_metrics): kwarg shortcut + schema lock + placeholders + now injection"
```

---

## Task 6: Web route file

**Files:**
- Create: `marketpulse/web/routes/charter.py`

- [ ] **Step 1: Write the failing route smoke test (will be expanded in Task 7)**

Create `tests/web/test_charter_route.py`:

```python
"""PR2 — /lab/charter-metrics route tests."""
from __future__ import annotations

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password


def _login(client: TestClient, monkeypatch) -> None:
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def test_endpoint_requires_auth(client: TestClient):
    r = client.get("/lab/charter-metrics")
    # Unauthenticated → 401 (FastAPI default for HTTPException)
    assert r.status_code == 401


def test_endpoint_returns_200_with_no_backup_dir(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/charter-metrics")
    assert r.status_code == 200
    body = r.json()
    backup = body["operational_floor"]["backup"]
    # Fresh test DB has no /data/backups dir → status=missing
    assert backup["status"] == "missing"
    assert backup["is_stale"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/web/test_charter_route.py -v`
Expected: FAIL — 404 Not Found (route doesn't exist yet)

- [ ] **Step 3: Create the route module**

Create `marketpulse/web/routes/charter.py`:

```python
# Layer: web
"""GET /lab/charter-metrics — v1 operational contract endpoint.

PR2 of Charter top-3 priority #1. See spec:
docs/superpowers/specs/2026-05-28-pr2-charter-metrics-design.md
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.engine.url import make_url

from marketpulse.config import get_settings
from marketpulse.ops.charter_metrics import build_charter_metrics
from marketpulse.web.deps import require_auth

router = APIRouter()

_NON_SQLITE_REASON = (
    "sqlite database_url required for backup manifest discovery"
)


@router.get("/lab/charter-metrics")
def lab_charter_metrics(
    _: None = Depends(require_auth),
) -> dict[str, Any]:
    """Return the v1 charter-metrics contract.

    HTTP 200 on every outcome — failed/missing backups are data, not errors.
    """
    settings = get_settings()
    parsed = make_url(settings.database_url)
    now = datetime.now(UTC)

    # SQLite drivers may be "sqlite" or "sqlite+pysqlite"; treat both as SQLite.
    if not parsed.drivername.startswith("sqlite") or not parsed.database:
        return build_charter_metrics(
            manifest_path=Path("/dev/null"),  # ignored when reason is set
            now=now,
            backup_unavailable_reason=_NON_SQLITE_REASON,
        )

    # PR1 invariant: backups live at <dbpath>.parent/backups/latest.json.
    # Both the APScheduler job and this route share container CWD, so
    # `Path(parsed.database).resolve()` produces the same absolute path.
    manifest_path = (
        Path(parsed.database).resolve().parent / "backups" / "latest.json"
    )
    return build_charter_metrics(manifest_path=manifest_path, now=now)
```

- [ ] **Step 4: Wire the router in `marketpulse/web/main.py`**

Edit `marketpulse/web/main.py`. In the import block (currently lines 137–152), add `charter` in alphabetical order:

```python
    from marketpulse.web.routes import (  # noqa: WPS433
        alerts,
        auth,
        backtest,
        broker,
        charter,
        health,
        holdings,
        home,
        lab,
        recap,
        reconcile,
        splits,
        stock,
        trades,
        watchlist,
    )
```

Then add the `include_router` call alongside the other `/lab/*` routers (after `app.include_router(reconcile.router)`):

```python
    app.include_router(reconcile.router)
    app.include_router(charter.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/web/test_charter_route.py -v`
Expected: PASS — both tests pass

- [ ] **Step 6: Commit**

```bash
git add marketpulse/web/routes/charter.py marketpulse/web/main.py tests/web/test_charter_route.py
git commit -m "feat(web): /lab/charter-metrics JSON endpoint (PR2)"
```

---

## Task 7: Remaining 3 route tests (failed seed, non-sqlite, content-type)

**Files:**
- Modify: `tests/web/test_charter_route.py`

- [ ] **Step 1: Add 3 failing tests**

Append to `tests/web/test_charter_route.py`:

```python
import json
from pathlib import Path

import pytest


def _seed_failed_manifest(db_path: Path) -> Path:
    """Write a failed-status manifest at <dbpath>.parent/backups/latest.json."""
    backups_dir = db_path.parent / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "failed",
        "timestamp": "2026-05-28T09:00:00+00:00",
        "source": str(db_path),
        "destination": None,
        "size_bytes": None,
        "integrity_check": "not_run",
        "duration_ms": 42,
        "error": "OSError: disk full",
    }
    manifest_path = backups_dir / "latest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    return manifest_path


def test_endpoint_returns_200_when_backup_failed(
    client: TestClient, monkeypatch, db_url: str,
):
    _login(client, monkeypatch)
    # db_url is sqlite:///<tmp>/test.db — derive the path.
    db_path = Path(db_url.replace("sqlite:///", "", 1))
    _seed_failed_manifest(db_path)

    r = client.get("/lab/charter-metrics")
    assert r.status_code == 200
    backup = r.json()["operational_floor"]["backup"]
    assert backup["status"] == "failed"
    assert backup["error"] == "OSError: disk full"


def test_endpoint_non_sqlite_url(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    # Force the settings to look non-SQLite for this request.
    from marketpulse.config import get_settings
    real_settings = get_settings()

    class _StubSettings:
        database_url = "postgresql://user:pw@localhost:5432/mp"
        # Forward any other attribute reads to the real settings.
        def __getattr__(self, name):
            return getattr(real_settings, name)

    monkeypatch.setattr(
        "marketpulse.web.routes.charter.get_settings",
        lambda: _StubSettings(),
    )

    r = client.get("/lab/charter-metrics")
    assert r.status_code == 200
    backup = r.json()["operational_floor"]["backup"]
    assert backup["status"] == "missing"
    assert "sqlite" in backup["error"].lower()


def test_endpoint_content_type_json(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/charter-metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    # And the body is parseable JSON
    body = r.json()
    assert body["schema_version"] == 1
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/web/test_charter_route.py -v`
Expected: PASS — all 5 route tests

- [ ] **Step 3: Commit**

```bash
git add tests/web/test_charter_route.py
git commit -m "test(charter_route): failed-seed + non-sqlite + content-type"
```

---

## Task 8: Final integration — full suite + ruff + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Run full pytest suite**

Run: `pytest -x`
Expected: PASS — all tests including pre-existing ones (no regressions)

If any pre-existing test fails, debug before continuing. Likely cause: a fixture or app-wide import broke when wiring `charter.router`.

- [ ] **Step 2: Run ruff**

Run: `ruff check .`
Expected: clean (no errors).

If errors appear in the new files, fix them. Common: import ordering, line length, unused imports.

- [ ] **Step 3: Local smoke test (optional pre-deploy verification)**

Start the app locally if convenient:

```bash
uvicorn marketpulse.web.main:create_app --factory --reload --port 8000
```

In another shell:

```bash
# Login (substitute the dev password from .env or DEFAULT_APP_PASSWORD)
curl -s -c /tmp/mp-cookies.txt -X POST http://localhost:8000/login \
  -d "password=dev"

# Hit the endpoint
curl -s -b /tmp/mp-cookies.txt http://localhost:8000/lab/charter-metrics \
  | jq '.operational_floor.backup'
```

Expected: a JSON object with `status` in `{ok, failed, missing}`. On a dev box with no `/data/backups/` it should be `"missing"` — that confirms the missing-manifest path works.

- [ ] **Step 4: Open the PR**

```bash
git push -u origin HEAD
gh pr create --title "feat(charter-metrics): /lab/charter-metrics JSON endpoint (PR2)" \
  --body "$(cat <<'EOF'
## Summary
- Adds `marketpulse/ops/charter_metrics.py` — pure module that builds the v1 charter-metrics contract dict from PR #128's backup manifest. Never raises.
- Adds `GET /lab/charter-metrics` (auth-gated, JSON-only) returning backup health + reserved `north_star`/`diagnostics` placeholders.
- HTTP 200 on every outcome — failed/missing backups are observability data, not server errors.

Charter top-3 priority #1, second half. Spec: `docs/superpowers/specs/2026-05-28-pr2-charter-metrics-design.md`.

## Test Plan
- [ ] `pytest tests/ops/test_charter_metrics.py -v` — 14 unit cases (missing/unreadable/json-invalid/malformed/timestamp/ok/failed/stale/kwarg/schema-lock/placeholders/now-injection)
- [ ] `pytest tests/web/test_charter_route.py -v` — 5 route cases (auth, missing, failed, non-sqlite, content-type)
- [ ] `pytest -x` — full suite green, no regressions
- [ ] `ruff check .` — clean
- [ ] Post-deploy smoke: `curl -b cookies.txt http://localhost:8088/lab/charter-metrics | jq '.operational_floor.backup.status'` → `"ok"` (after the next 09:00 UTC cron fire, or after manual `run_db_backup()`)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Post-deploy verification (after PR merged + container restart)**

On the NAS:

```bash
ssh divxer@192.168.50.29
echo Latex@2022 | sudo -S -p '' /usr/local/bin/docker exec marketpulse \
  curl -sf -b /tmp/cookies.txt http://localhost:8000/lab/charter-metrics \
  | jq '.operational_floor.backup.status'
```

Or trigger via the host with login first. Expected: `"ok"` after the cron has fired at least once.

---

## Spec → Plan Coverage Map

| Spec requirement | Implemented in |
|---|---|
| Pure `ops/charter_metrics.py`, never raises | Task 1 |
| `SCHEMA_VERSION=1`, `STALE_AFTER_HOURS=25` constants | Task 1 |
| Happy path (status=ok, recent) | Task 1 |
| Failure: file missing | Task 2 |
| Failure: unreadable | Task 2 |
| Failure: JSON invalid | Task 2 |
| Failure: missing key `timestamp` | Task 2 |
| Failure: missing key `duration_ms` | Task 2 |
| Failure: invalid timestamp | Task 3 |
| Z-suffix compatibility | Task 3 |
| `is_stale` true when age > 25h | Task 4 |
| `status="failed"` pass-through + `is_stale` orthogonal | Task 4 |
| `backup_unavailable_reason` kwarg short-circuits read | Task 5 |
| Schema v1 lock (top-level subset, backup exact-set) | Task 5 |
| `north_star` / `diagnostics` `"not_implemented"` placeholders | Task 5 |
| `timestamp` uses injected `now` | Task 5 |
| Web route, JSON-only, `Depends(require_auth)` | Task 6 |
| `drivername.startswith("sqlite")` | Task 6 |
| Path-resolution invariant (shared container CWD) | Task 6 (route code + comment) |
| HTTP 200 on every outcome | Task 6 + Task 7 |
| Non-SQLite URL → explicit reason | Task 7 |
| `application/json` content type | Task 7 |
| Full suite + ruff acceptance | Task 8 |
| Manual smoke post-deploy | Task 8 |
