# Layer: db
"""PR3b — DB aggregator for the weekly charter review.

L3: reads paper_nav_snapshot, paper_audit_event, paper_fill only.
Never reads paper_position or paper_cash_ledger.
Manifest is INPUT — never read from filesystem here.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from marketpulse.db.models import (
    PaperAuditEvent,
    PaperFill,
    PaperNavSnapshot,
)
from marketpulse.ops.charter_review_types import (
    CharterReviewPayload,
    DiagnosticsWeek,
    DiagnosticWeek,
    NorthStarWeek,
    OperationalFloor,
    ReasonCount,
    SnapshotAppendix,
    WeekWindow,
)

NO_REASON = "(no reason)"   # L19


def _week_window(week_ending: date) -> WeekWindow:
    """L2: Sunday week_end → Monday week_start (6 days back).
    trading_days_observed is filled later."""
    week_start = week_ending - timedelta(days=6)
    return WeekWindow(
        week_start=week_start, week_end=week_ending, trading_days_observed=0,
    )


def _eod_window(week: WeekWindow) -> tuple[datetime, datetime]:
    start = datetime.combine(week.week_start, time.min, tzinfo=UTC)
    end = datetime.combine(week.week_end, time.max, tzinfo=UTC)
    return start, end


def _trading_days_observed(session: Session, week: WeekWindow) -> int:
    return int(session.scalar(
        select(func.count(PaperNavSnapshot.trading_date))
        .where(and_(
            PaperNavSnapshot.trading_date >= week.week_start,
            PaperNavSnapshot.trading_date <= week.week_end,
        )),
    ) or 0)


def _build_north_star_for_week(
    session: Session, week: WeekWindow,
) -> NorthStarWeek:
    days = _trading_days_observed(session, week)
    week_with_days = WeekWindow(
        week_start=week.week_start, week_end=week.week_end,
        trading_days_observed=days,
    )
    if days == 0:
        return NorthStarWeek(
            week=week_with_days,
            first_snapshot_date=None, last_snapshot_date=None,
            excess_return_end=None, portfolio_index_end=None,
            spy_index_end=None, coverage_ratio_end=None,
            is_sufficient_end=False,
        )
    rows = session.scalars(
        select(PaperNavSnapshot)
        .where(and_(
            PaperNavSnapshot.trading_date >= week.week_start,
            PaperNavSnapshot.trading_date <= week.week_end,
        ))
        .order_by(PaperNavSnapshot.trading_date.asc()),
    ).all()
    first = rows[0]
    last = rows[-1]
    return NorthStarWeek(
        week=week_with_days,
        first_snapshot_date=first.trading_date,
        last_snapshot_date=last.trading_date,
        excess_return_end=last.excess_return,
        portfolio_index_end=last.portfolio_index,
        spy_index_end=last.spy_index,
        coverage_ratio_end=last.coverage_ratio,
        is_sufficient_end=last.is_sufficient,
    )


def _empty_diagnostic() -> DiagnosticWeek:
    return DiagnosticWeek(value=None, observations=0, top_reasons=())


def _empty_diagnostics() -> DiagnosticsWeek:
    return DiagnosticsWeek(
        tick_success_rate=_empty_diagnostic(),
        order_rejection_rate=_empty_diagnostic(),
        paper_trade_count=_empty_diagnostic(),
        engine_invariant_errors=_empty_diagnostic(),
    )


def _operational_floor(manifest: dict | None) -> OperationalFloor:
    """L14: None or malformed manifest → missing + stale + Nones."""
    if not manifest:
        return OperationalFloor(
            backup_status="missing", backup_is_stale=True,
            backup_last_at=None, backup_error=None,
            manifest_available=False,
        )
    raw_status = manifest.get("status")
    if raw_status not in {"ok", "failed", "missing"}:
        return OperationalFloor(
            backup_status="missing", backup_is_stale=True,
            backup_last_at=None, backup_error=None,
            manifest_available=False,
        )
    return OperationalFloor(
        backup_status=raw_status,
        backup_is_stale=bool(manifest.get("is_stale", True)),
        backup_last_at=manifest.get("last_backup_at"),
        backup_error=manifest.get("error"),
        manifest_available=True,
    )


def _appendix_snapshot(session: Session, week: WeekWindow) -> SnapshotAppendix:
    """L15: latest snapshot in week. All None when none exist."""
    row = session.scalars(
        select(PaperNavSnapshot)
        .where(and_(
            PaperNavSnapshot.trading_date >= week.week_start,
            PaperNavSnapshot.trading_date <= week.week_end,
        ))
        .order_by(PaperNavSnapshot.trading_date.desc())
        .limit(1),
    ).first()
    if row is None:
        return SnapshotAppendix(
            trading_date=None, cash_balance=None, holdings_mtm=None,
            portfolio_nav=None, unpriced_positions_count=0,
            unpriced_tickers=(),
        )
    tickers_raw = row.unpriced_tickers
    tickers = tuple(tickers_raw.split(",")) if tickers_raw else ()
    return SnapshotAppendix(
        trading_date=row.trading_date,
        cash_balance=row.cash_balance,
        holdings_mtm=row.holdings_mtm,
        portfolio_nav=row.portfolio_nav,
        unpriced_positions_count=row.unpriced_positions_count,
        unpriced_tickers=tickers,
    )


def build_payload(
    *,
    session: Session,
    week_ending: date,
    backup_manifest: dict | None,
    generated_at: datetime,
) -> CharterReviewPayload:
    """Build the payload. Diagnostics are stubbed empty in Task 5;
    populated in Task 6."""
    this_window = _week_window(week_ending)
    prior_window = _week_window(week_ending - timedelta(days=7))
    return CharterReviewPayload(
        generated_at=generated_at,
        week_ending=week_ending,
        this_week=WeekWindow(
            week_start=this_window.week_start,
            week_end=this_window.week_end,
            trading_days_observed=_trading_days_observed(session, this_window),
        ),
        prior_week=WeekWindow(
            week_start=prior_window.week_start,
            week_end=prior_window.week_end,
            trading_days_observed=_trading_days_observed(session, prior_window),
        ),
        north_star_this=_build_north_star_for_week(session, this_window),
        north_star_prior=_build_north_star_for_week(session, prior_window),
        diagnostics_this=_empty_diagnostics(),       # populated in Task 6
        diagnostics_prior=_empty_diagnostics(),       # populated in Task 6
        operational_floor=_operational_floor(backup_manifest),
        appendix_snapshot=_appendix_snapshot(session, this_window),
    )
