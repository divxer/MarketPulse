# Layer: ops
"""Charter metrics v1 contract — PR2 of Charter top-3 priority #1.

See docs/superpowers/specs/2026-05-28-pr2-charter-metrics-design.md.

Pure module. No DB. No network. Only reads `manifest_path`. Never raises —
every failure mode normalizes into a well-formed v1 contract dict so the
endpoint and PR3's weekly report can both consume the same shape.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_
from sqlalchemy import func as _func
from sqlalchemy import select as _select
from sqlalchemy.orm import Session

from marketpulse.portfolio.snapshot_repo import (
    get_latest_snapshot,
    get_recent_snapshot_dates,
)

SCHEMA_VERSION = 1
STALE_AFTER_HOURS = 25
NORTH_STAR_METRIC = "paper_portfolio_excess_return_vs_spy_90d"
NORTH_STAR_REQUIRED = 90
DIAGNOSTICS_REQUIRED = 30

_REQUIRED_MANIFEST_KEYS: tuple[str, ...] = (
    "timestamp", "status", "integrity_check", "duration_ms",
)
_ALLOWED_MANIFEST_STATUSES: frozenset[str] = frozenset({"ok", "failed"})


def _empty_north_star(*, error: str) -> dict[str, Any]:
    return {
        "metric": NORTH_STAR_METRIC,
        "as_of_trading_date": None,
        "value": None,
        "portfolio_index": None,
        "spy_index": None,
        "trading_days_observed": 0,
        "trading_days_required": NORTH_STAR_REQUIRED,
        "coverage_ratio": 0,
        "is_sufficient": False,
        "window_start": None,
        "window_end": None,
        "data_quality": {
            "unpriced_positions_count": 0,
            "unpriced_tickers": [],
            "is_complete": True,
        },
        "error": error,
    }


def _to_float(value):  # Decimal | None → float | None  # noqa: ANN001
    return None if value is None else float(value)


def build_north_star_section(
    session: Session | None, *, now,  # noqa: ARG001 (now reserved for future use)
) -> dict[str, Any]:
    """L17: ratios/returns/index → float; money fields are NOT exposed.
    Empty snapshot table → no_snapshots_yet fallback. session=None →
    db_session_unavailable fallback (L10).

    This is a DB-backed builder (NOT pure) — see L9. Reads the latest
    snapshot row and renders the contract dict; never recomputes
    semantics."""
    if session is None:
        return _empty_north_star(error="db_session_unavailable")

    latest = get_latest_snapshot(session)
    if latest is None:
        return _empty_north_star(error="no_snapshots_yet")

    recent_dates = get_recent_snapshot_dates(session, limit=NORTH_STAR_REQUIRED)
    window_start = recent_dates[0] if recent_dates else None

    return {
        "metric": NORTH_STAR_METRIC,
        "as_of_trading_date": latest.trading_date.isoformat(),
        "value": _to_float(latest.excess_return),
        "portfolio_index": _to_float(latest.portfolio_index),
        "spy_index": _to_float(latest.spy_index),
        "trading_days_observed": latest.trading_days_observed,
        "trading_days_required": NORTH_STAR_REQUIRED,
        "coverage_ratio": _to_float(latest.coverage_ratio),
        "is_sufficient": latest.is_sufficient,
        "window_start": window_start.isoformat() if window_start else None,
        "window_end": latest.trading_date.isoformat(),
        "data_quality": {
            "unpriced_positions_count": latest.unpriced_positions_count,
            "unpriced_tickers": list(latest.unpriced_tickers),
            "is_complete": latest.unpriced_positions_count == 0,
        },
        "error": None,
    }


def _empty_diagnostic() -> dict[str, Any]:
    return {
        "value": None,
        "observations": 0,
        "required_observations": DIAGNOSTICS_REQUIRED,
        "coverage_ratio": 0,
        "is_sufficient": False,
    }


def _empty_diagnostics() -> dict[str, Any]:
    return {
        "tick_success_rate_30d": _empty_diagnostic(),
        "order_rejection_rate_30d": _empty_diagnostic(),
        "paper_trade_count_30d": _empty_diagnostic(),
    }


def build_diagnostics_section(
    session: Session | None, *, now,  # noqa: ARG001
) -> dict[str, Any]:
    """L11: window = last 30 snapshot trading_dates (or all if fewer).
    L17: ratios → float. DB-backed builder (NOT pure)."""
    if session is None:
        return _empty_diagnostics()

    recent = get_recent_snapshot_dates(session, limit=DIAGNOSTICS_REQUIRED)
    if not recent:
        return _empty_diagnostics()

    from datetime import datetime as _dt
    from datetime import time as _time
    window_start_eod = _dt.combine(recent[0], _time.min, tzinfo=UTC)
    window_end_eod = _dt.combine(recent[-1], _time.max, tzinfo=UTC)
    snapshot_count = len(recent)
    coverage_ratio = min(snapshot_count / DIAGNOSTICS_REQUIRED, 1.0)
    is_sufficient = snapshot_count >= DIAGNOSTICS_REQUIRED

    from marketpulse.db.models import (
        PaperAuditEvent as _Audit,
    )
    from marketpulse.db.models import (
        PaperFill as _Fill,
    )

    # 1. tick_success_rate_30d
    tick_completed = session.scalar(
        _select(_func.count(_Audit.id)).where(
            and_(
                _Audit.event_type == "TICK_COMPLETED",
                _Audit.timestamp >= window_start_eod,
                _Audit.timestamp <= window_end_eod,
            ),
        ),
    ) or 0
    engine_error = session.scalar(
        _select(_func.count(_Audit.id)).where(
            and_(
                _Audit.event_type == "ENGINE_INVARIANT_ERROR",
                _Audit.timestamp >= window_start_eod,
                _Audit.timestamp <= window_end_eod,
            ),
        ),
    ) or 0
    tick_total = tick_completed + engine_error
    tick_dict = _empty_diagnostic()
    if tick_total > 0:
        tick_dict["value"] = tick_completed / tick_total
        tick_dict["observations"] = tick_total
        tick_dict["coverage_ratio"] = min(tick_total / DIAGNOSTICS_REQUIRED, 1.0)
        tick_dict["is_sufficient"] = tick_total >= DIAGNOSTICS_REQUIRED

    # 2. order_rejection_rate_30d (L12: PLACED + REJECTED mutually exclusive)
    placed = session.scalar(
        _select(_func.count(_Audit.id)).where(
            and_(
                _Audit.event_type == "ORDER_PLACED",
                _Audit.timestamp >= window_start_eod,
                _Audit.timestamp <= window_end_eod,
            ),
        ),
    ) or 0
    rejected = session.scalar(
        _select(_func.count(_Audit.id)).where(
            and_(
                _Audit.event_type == "ORDER_REJECTED",
                _Audit.timestamp >= window_start_eod,
                _Audit.timestamp <= window_end_eod,
            ),
        ),
    ) or 0
    decisions = placed + rejected
    rej_dict = _empty_diagnostic()
    if decisions > 0:
        rej_dict["value"] = rejected / decisions
        rej_dict["observations"] = decisions
        rej_dict["coverage_ratio"] = min(decisions / DIAGNOSTICS_REQUIRED, 1.0)
        rej_dict["is_sufficient"] = decisions >= DIAGNOSTICS_REQUIRED

    # 3. paper_trade_count_30d (L13: paper_fill ENTRY rows)
    trade_count = session.scalar(
        _select(_func.count(_Fill.id)).where(
            and_(
                _Fill.side == "ENTRY",
                _Fill.position_id.is_not(None),
                _Fill.filled_at >= window_start_eod,
                _Fill.filled_at <= window_end_eod,
            ),
        ),
    ) or 0
    trade_dict = {
        "value": int(trade_count),
        "observations": snapshot_count,
        "required_observations": DIAGNOSTICS_REQUIRED,
        "coverage_ratio": coverage_ratio,
        "is_sufficient": is_sufficient,
    }

    return {
        "tick_success_rate_30d": tick_dict,
        "order_rejection_rate_30d": rej_dict,
        "paper_trade_count_30d": trade_dict,
    }


def build_charter_metrics(
    *,
    manifest_path: Path,
    now: datetime,
    backup_unavailable_reason: str | None = None,
    session: Session | None = None,
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
        "north_star": build_north_star_section(session, now=now),
        "diagnostics": build_diagnostics_section(session, now=now),
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
