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
_ALLOWED_MANIFEST_STATUSES: frozenset[str] = frozenset({"ok", "failed"})


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

    # Defensive: _read_manifest already validated these, but the contract
    # promise is "never raises", so re-guard at the boundary.
    try:
        last_backup_at = _parse_timestamp(str(manifest["timestamp"]))
    except (ValueError, TypeError):
        return _missing_backup(error="manifest malformed: invalid timestamp")
    if last_backup_at.tzinfo is None or last_backup_at.utcoffset() is None:
        return _missing_backup(error="manifest malformed: timestamp missing timezone")
    try:
        is_stale = (now - last_backup_at) > timedelta(hours=STALE_AFTER_HOURS)
    except TypeError:
        return _missing_backup(error="manifest malformed: timestamp missing timezone")
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
    # status enum validation — PR2 is the contract boundary; reject unknown values.
    if parsed["status"] not in _ALLOWED_MANIFEST_STATUSES:
        return None, "manifest malformed: invalid status"
    # timestamp parse-validation (accepts Z suffix).
    try:
        dt = _parse_timestamp(str(parsed["timestamp"]))
    except (ValueError, TypeError):
        return None, "manifest malformed: invalid timestamp"
    if dt.tzinfo is None or dt.utcoffset() is None:
        return None, "manifest malformed: timestamp missing timezone"
    return parsed, None


def _parse_timestamp(value: str) -> datetime:
    """Parse ISO-8601 timestamp, normalizing trailing 'Z' to '+00:00'."""
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized)
