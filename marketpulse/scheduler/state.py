"""Lightweight state persistence for scheduler jobs.

Stores the latest run summary as JSON in the existing `app_settings` key-value
table (no new migration). Used by `/health/scheduler` to expose:
- when the last detect_corporate_actions run happened
- per-ticker source (tencent / yfinance / none) and counts of splits/dividends added
- any error encountered per ticker
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from marketpulse.db.models import AppSetting

_LAST_RUN_KEY = "scheduler.detect_corporate_actions.last_run"


def record_run_summary(session: Session, summary: dict[str, Any]) -> None:
    """Persist the most recent run summary. Overwrites the previous row.
    Commits within. Dates / datetimes are coerced via str() in JSON.
    """
    payload = json.dumps(summary, default=str)
    row = (
        session.query(AppSetting)
        .filter(AppSetting.key == _LAST_RUN_KEY)
        .one_or_none()
    )
    if row:
        row.value = payload
    else:
        session.add(AppSetting(key=_LAST_RUN_KEY, value=payload))
    session.commit()


def get_last_run_summary(session: Session) -> dict[str, Any] | None:
    """Return the most recent run summary, or None if no run has ever finished."""
    row = (
        session.query(AppSetting)
        .filter(AppSetting.key == _LAST_RUN_KEY)
        .one_or_none()
    )
    if not row:
        return None
    return json.loads(row.value)
