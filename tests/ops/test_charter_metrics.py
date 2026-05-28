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


def test_malformed_naive_timestamp(tmp_path):
    """Naive timestamp (no tz) cannot be subtracted from aware `now`.
    PR2 rejects it as malformed at the contract boundary."""
    payload = {
        "status": "ok",
        "timestamp": "2026-05-28T09:00:00",  # no offset, no Z
        "integrity_check": "ok",
        "duration_ms": 100,
    }
    manifest_path = tmp_path / "latest.json"
    _write_manifest(manifest_path, payload)
    now = datetime(2026, 5, 28, 10, 0, 0, tzinfo=UTC)

    result = build_charter_metrics(manifest_path=manifest_path, now=now)

    backup = result["operational_floor"]["backup"]
    assert backup["status"] == "missing"
    assert "missing timezone" in backup["error"]


def test_malformed_invalid_status(tmp_path):
    """status enum is locked to {ok, failed}. Unknown values are malformed."""
    payload = {
        "status": "weird",
        "timestamp": "2026-05-28T09:00:00+00:00",
        "integrity_check": "ok",
        "duration_ms": 100,
    }
    manifest_path = tmp_path / "latest.json"
    _write_manifest(manifest_path, payload)
    now = datetime(2026, 5, 28, 10, 0, 0, tzinfo=UTC)

    result = build_charter_metrics(manifest_path=manifest_path, now=now)

    backup = result["operational_floor"]["backup"]
    assert backup["status"] == "missing"
    assert "invalid status" in backup["error"]


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
