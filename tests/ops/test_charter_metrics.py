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
