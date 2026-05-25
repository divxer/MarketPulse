"""Phase 7a+ Broker Truth Viewer — read-only query model.

LOCK L2: this module reads ONLY the 4 Phase 7a snapshot tables. It must
NEVER touch broker_order_intent / broker_order_event (Phase 7b write
provenance) or paper_* (Phase 6 paper lifecycle). The architecture guard
test at tests/architecture/test_lab_broker_isolation.py enforces this.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketpulse.db.models import (
    BrokerAccountSnapshot,
    BrokerCashSnapshot,
    BrokerPositionSnapshot,
    BrokerSyncRun,
)


@dataclass(frozen=True)
class BrokerSyncRunSummary:
    id: int
    started_at: datetime
    completed_at: datetime | None
    status: str  # started/completed/failed
    broker_environment: str
    account_id: str | None
    error_type: str | None
    error_message: str | None
    reference_code: str | None


@dataclass(frozen=True)
class BrokerDashboard:
    """Snapshot of broker truth for the viewer page.

    snapshot_run: the SyncRunSummary that sourced the displayed snapshot
        data (may be older than latest_run when latest is failed).
    latest_run: the most recent run regardless of status (drives the
        "latest sync attempt" banner in Hero).
    snapshot_is_stale: True iff snapshot_run.id != latest_run.id (we're
        showing fallback data because latest failed).
    """

    latest_run: BrokerSyncRunSummary | None
    snapshot_run: BrokerSyncRunSummary | None
    snapshot_is_stale: bool
    account: BrokerAccountSnapshot | None
    cash_rows: tuple[BrokerCashSnapshot, ...]
    position_rows: tuple[BrokerPositionSnapshot, ...]
    recent_runs: tuple[BrokerSyncRunSummary, ...]


def _summary_from_run(run: BrokerSyncRun) -> BrokerSyncRunSummary:
    ctx = run.context or {}
    ref = ctx.get("reference_code") if isinstance(ctx, dict) else None
    return BrokerSyncRunSummary(
        id=run.id,
        started_at=run.started_at,
        completed_at=run.completed_at,
        status=run.status,
        broker_environment=run.broker_environment,
        account_id=run.account_id,
        error_type=run.error_type,
        error_message=run.error_message,
        reference_code=ref if isinstance(ref, str) else None,
    )


def load_broker_dashboard(
    db: Session, *, recent_limit: int = 10
) -> BrokerDashboard:
    """Build the full dashboard payload in one cohesive pass.

    Algorithm:
    1. Fetch latest broker_sync_run (any status). If none -> empty dashboard.
    2. If latest is completed -> snapshot_run = latest_run.
       Else -> snapshot_run = most recent completed run (may be None).
    3. Fetch account/cash/positions rows tied to snapshot_run.id (or empty
       tuples if snapshot_run is None).
    4. Fetch last ``recent_limit`` runs ordered by started_at desc.
    """
    latest_run_row = db.execute(
        select(BrokerSyncRun).order_by(BrokerSyncRun.started_at.desc()).limit(1)
    ).scalar_one_or_none()

    if latest_run_row is None:
        return BrokerDashboard(
            latest_run=None,
            snapshot_run=None,
            snapshot_is_stale=False,
            account=None,
            cash_rows=(),
            position_rows=(),
            recent_runs=(),
        )

    latest_summary = _summary_from_run(latest_run_row)

    if latest_run_row.status == "completed":
        snapshot_row = latest_run_row
    else:
        snapshot_row = db.execute(
            select(BrokerSyncRun)
            .where(BrokerSyncRun.status == "completed")
            .order_by(BrokerSyncRun.started_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    snapshot_summary = (
        _summary_from_run(snapshot_row) if snapshot_row is not None else None
    )

    account: BrokerAccountSnapshot | None = None
    cash_rows: tuple[BrokerCashSnapshot, ...] = ()
    position_rows: tuple[BrokerPositionSnapshot, ...] = ()
    if snapshot_row is not None:
        account = db.execute(
            select(BrokerAccountSnapshot)
            .where(BrokerAccountSnapshot.sync_run_id == snapshot_row.id)
            .limit(1)
        ).scalar_one_or_none()
        cash_rows = tuple(
            db.execute(
                select(BrokerCashSnapshot)
                .where(BrokerCashSnapshot.sync_run_id == snapshot_row.id)
                .order_by(BrokerCashSnapshot.cash_balance.desc())
            ).scalars()
        )
        position_rows = tuple(
            db.execute(
                select(BrokerPositionSnapshot)
                .where(BrokerPositionSnapshot.sync_run_id == snapshot_row.id)
                .order_by(BrokerPositionSnapshot.market_value.desc())
            ).scalars()
        )

    recent_rows = db.execute(
        select(BrokerSyncRun)
        .order_by(BrokerSyncRun.started_at.desc())
        .limit(recent_limit)
    ).scalars().all()
    recent_runs = tuple(_summary_from_run(r) for r in recent_rows)

    snapshot_is_stale = (
        snapshot_summary is not None
        and snapshot_summary.id != latest_summary.id
    )

    return BrokerDashboard(
        latest_run=latest_summary,
        snapshot_run=snapshot_summary,
        snapshot_is_stale=snapshot_is_stale,
        account=account,
        cash_rows=cash_rows,
        position_rows=position_rows,
        recent_runs=recent_runs,
    )
