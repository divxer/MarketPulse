# Layer: db
"""Task #57 — eval-analysis last-run summary persistence.

Mirrors scheduler/state.py: one JSON blob in the app_settings key-value table
(no migration). Read by /health/scheduler. The recorder stamps `ts` (UTC) so the
core summary stays clock-free and unit-testable.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from marketpulse.db.models import AppSetting

_LAST_RUN_KEY = "scheduler.ai_eval_analysis.last_run"


def record_eval_run_summary(session: Session, summary: dict[str, Any]) -> None:
    """Persist the latest run summary (overwrites prior). Stamps `ts` (UTC).
    Commits within. Dates/datetimes coerced via str() in JSON."""
    payload_dict = {**summary, "ts": datetime.now(UTC).isoformat()}
    payload = json.dumps(payload_dict, default=str)
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


def get_eval_last_run_summary(session: Session) -> dict[str, Any] | None:
    """Return the most recent run summary, or None if never run."""
    row = (
        session.query(AppSetting)
        .filter(AppSetting.key == _LAST_RUN_KEY)
        .one_or_none()
    )
    if not row:
        return None
    return json.loads(row.value)
