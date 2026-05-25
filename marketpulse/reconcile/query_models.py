"""Phase 7c - DB-backed dashboard assembly."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from marketpulse.config import get_settings
from marketpulse.db.models import BrokerPositionSnapshot, BrokerSyncRun, PaperPosition
from marketpulse.reconcile.diffing import reconcile_positions
from marketpulse.reconcile.types import DiffRow, DiffType, ReconciliationDashboard, Severity

_STALE_THRESHOLD = timedelta(hours=24)
_RED_MISMATCH_COUNT_THRESHOLD = 3


def _empty_dashboard(
    *,
    no_broker_data: bool = False,
    account_ambiguous: bool = False,
    paper_open_count: int = 0,
    recent_failed: tuple[str, ...] = (),
) -> ReconciliationDashboard:
    return ReconciliationDashboard(
        rows=(),
        severity=Severity.GRAY,
        broker_account_id=None,
        broker_completed_at=None,
        broker_reference_code=None,
        broker_is_stale=False,
        no_broker_data=no_broker_data,
        account_ambiguous=account_ambiguous,
        paper_open_position_count=paper_open_count,
        recent_failed_run_descriptions=recent_failed,
        matched_count=0,
        missing_in_broker_count=0,
        missing_in_paper_count=0,
        quantity_mismatch_count=0,
        side_mismatch_count=0,
    )


def _pick_account(db: Session) -> tuple[str | None, bool]:
    settings = get_settings()
    configured = (settings.ibkr_account_id or "").strip()
    if configured:
        return configured, False

    distinct_accounts = db.execute(
        select(distinct(BrokerSyncRun.account_id)).where(BrokerSyncRun.status == "completed")
    ).scalars().all()
    accounts = [account for account in distinct_accounts if account]
    if not accounts:
        return None, False
    if len(accounts) > 1:
        return None, True
    return accounts[0], False


def _recent_failed_descriptions(db: Session, limit: int = 3) -> tuple[str, ...]:
    rows = db.execute(
        select(BrokerSyncRun)
        .where(BrokerSyncRun.status == "failed")
        .order_by(BrokerSyncRun.started_at.desc())
        .limit(limit)
    ).scalars().all()
    descriptions: list[str] = []
    for row in rows:
        label = row.error_type or "failed"
        if row.error_message:
            message = row.error_message[:80].replace("\n", " ")
            label = f"{label} - {message}"
        descriptions.append(f"#{row.id} {row.started_at.strftime('%Y-%m-%d %H:%M')} {label}")
    return tuple(descriptions)


def _paper_map(db: Session) -> tuple[dict[str, Decimal], int]:
    rows = db.execute(
        select(PaperPosition).where(PaperPosition.exit_fill_id.is_(None))
    ).scalars().all()
    paper: dict[str, Decimal] = {}
    for row in rows:
        key = (row.ticker or "").upper().strip()
        if key:
            paper[key] = paper.get(key, Decimal(0)) + Decimal(row.quantity)
    return paper, len(rows)


def _broker_map(db: Session, run_id: int) -> dict[str, Decimal]:
    rows = db.execute(
        select(BrokerPositionSnapshot).where(BrokerPositionSnapshot.sync_run_id == run_id)
    ).scalars().all()
    broker: dict[str, Decimal] = {}
    for row in rows:
        key = (row.symbol or "").upper().strip()
        if key:
            broker[key] = broker.get(key, Decimal(0)) + row.quantity
    return broker


def compute_hero_severity(
    rows: list[DiffRow],
    *,
    no_broker_data: bool = False,
    account_ambiguous: bool = False,
) -> Severity:
    if no_broker_data or account_ambiguous:
        return Severity.GRAY
    non_matched = [row for row in rows if row.diff_type != DiffType.MATCHED]
    if not non_matched:
        return Severity.GREEN
    if any(row.is_red for row in non_matched):
        return Severity.RED
    if len(non_matched) >= _RED_MISMATCH_COUNT_THRESHOLD:
        return Severity.RED
    return Severity.YELLOW


def load_reconciliation_dashboard(
    db: Session,
    *,
    now: datetime | None = None,
) -> ReconciliationDashboard:
    """Build the full read-only reconciliation dashboard payload."""
    clock_now = now if now is not None else datetime.now(UTC)
    account_id, account_ambiguous = _pick_account(db)

    if account_ambiguous:
        _, paper_count = _paper_map(db)
        return _empty_dashboard(account_ambiguous=True, paper_open_count=paper_count)

    if account_id is None:
        _, paper_count = _paper_map(db)
        return _empty_dashboard(
            no_broker_data=True,
            paper_open_count=paper_count,
            recent_failed=_recent_failed_descriptions(db),
        )

    latest_run = db.execute(
        select(BrokerSyncRun)
        .where(BrokerSyncRun.status == "completed")
        .where(BrokerSyncRun.account_id == account_id)
        .order_by(BrokerSyncRun.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if latest_run is None:
        _, paper_count = _paper_map(db)
        return _empty_dashboard(
            no_broker_data=True,
            paper_open_count=paper_count,
            recent_failed=_recent_failed_descriptions(db),
        )

    paper_map, paper_count = _paper_map(db)
    broker_map = _broker_map(db, latest_run.id)
    rows = reconcile_positions(paper_map, broker_map)
    counts = {diff_type: 0 for diff_type in DiffType}
    for row in rows:
        counts[row.diff_type] += 1

    broker_completed_at = latest_run.completed_at
    broker_is_stale = (
        broker_completed_at is not None
        and clock_now - broker_completed_at > _STALE_THRESHOLD
    )
    context = latest_run.context or {}
    reference_code = context.get("reference_code") if isinstance(context, dict) else None

    return ReconciliationDashboard(
        rows=tuple(rows),
        severity=compute_hero_severity(rows),
        broker_account_id=latest_run.account_id,
        broker_completed_at=broker_completed_at,
        broker_reference_code=reference_code if isinstance(reference_code, str) else None,
        broker_is_stale=broker_is_stale,
        no_broker_data=False,
        account_ambiguous=False,
        paper_open_position_count=paper_count,
        recent_failed_run_descriptions=(),
        matched_count=counts[DiffType.MATCHED],
        missing_in_broker_count=counts[DiffType.MISSING_IN_BROKER],
        missing_in_paper_count=counts[DiffType.MISSING_IN_PAPER],
        quantity_mismatch_count=counts[DiffType.QUANTITY_MISMATCH],
        side_mismatch_count=counts[DiffType.SIDE_MISMATCH],
    )
