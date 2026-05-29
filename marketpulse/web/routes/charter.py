# Layer: web
"""GET /lab/charter-metrics — v1 operational contract endpoint.

PR2 of Charter top-3 priority #1. See spec:
docs/superpowers/specs/2026-05-28-pr2-charter-metrics-design.md

PR3a fills north_star + diagnostics from paper_nav_snapshot.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session

from marketpulse.config import get_settings
from marketpulse.ops.charter_metrics import build_charter_metrics
from marketpulse.web.deps import get_db, require_auth

router = APIRouter()

_NON_SQLITE_REASON = (
    "sqlite database_url required for backup manifest discovery"
)


@router.get("/lab/charter-metrics")
def lab_charter_metrics(
    _: None = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return the v1 charter-metrics contract.

    HTTP 200 on every outcome — failed/missing backups are data, not errors.
    """
    settings = get_settings()
    parsed = make_url(settings.database_url)
    now = datetime.now(UTC)

    if not parsed.drivername.startswith("sqlite") or not parsed.database:
        return build_charter_metrics(
            manifest_path=Path("/dev/null"),
            now=now,
            backup_unavailable_reason=_NON_SQLITE_REASON,
            session=db,
        )

    manifest_path = (
        Path(parsed.database).resolve().parent / "backups" / "latest.json"
    )
    return build_charter_metrics(
        manifest_path=manifest_path,
        now=now,
        session=db,
    )
