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


def _normalize_reason(raw: str | None) -> str:
    """L19: NULL or empty → '(no reason)'. Anything else passes through."""
    if raw is None or raw == "":
        return NO_REASON
    return raw


def _top_reasons(
    session: Session, *, event_types: tuple[str, ...],
    window_start: datetime, window_end: datetime, limit: int = 3,
) -> tuple[ReasonCount, ...]:
    """SELECT reason, COUNT(*) GROUP BY reason; normalize empties to
    "(no reason)" then re-aggregate; ORDER BY count DESC, reason ASC LIMIT."""
    rows = session.execute(
        select(PaperAuditEvent.reason, func.count(PaperAuditEvent.id))
        .where(and_(
            PaperAuditEvent.event_type.in_(event_types),
            PaperAuditEvent.timestamp >= window_start,
            PaperAuditEvent.timestamp <= window_end,
        ))
        .group_by(PaperAuditEvent.reason),
    ).all()
    bucket: dict[str, int] = {}
    for raw_reason, n in rows:
        key = _normalize_reason(raw_reason)
        bucket[key] = bucket.get(key, 0) + int(n)
    ordered = sorted(bucket.items(), key=lambda kv: (-kv[1], kv[0]))
    return tuple(ReasonCount(reason=r, count=c) for r, c in ordered[:limit])


def _count_audit(
    session: Session, *, event_type: str,
    window_start: datetime, window_end: datetime,
) -> int:
    return int(session.scalar(
        select(func.count(PaperAuditEvent.id))
        .where(and_(
            PaperAuditEvent.event_type == event_type,
            PaperAuditEvent.timestamp >= window_start,
            PaperAuditEvent.timestamp <= window_end,
        )),
    ) or 0)


def _build_tick_success_rate(
    session: Session, week: WeekWindow,
) -> DiagnosticWeek:
    start, end = _eod_window(week)
    completed = _count_audit(session, event_type="TICK_COMPLETED",
                              window_start=start, window_end=end)
    errored = _count_audit(session, event_type="ENGINE_INVARIANT_ERROR",
                            window_start=start, window_end=end)
    total = completed + errored
    if total == 0:
        return _empty_diagnostic()
    value = Decimal(completed) / Decimal(total)
    return DiagnosticWeek(
        value=value, observations=total,
        top_reasons=_top_reasons(
            session, event_types=("ENGINE_INVARIANT_ERROR",),
            window_start=start, window_end=end,
        ),
    )


def _build_order_rejection_rate(
    session: Session, week: WeekWindow,
) -> DiagnosticWeek:
    start, end = _eod_window(week)
    placed = _count_audit(session, event_type="ORDER_PLACED",
                           window_start=start, window_end=end)
    rejected = _count_audit(session, event_type="ORDER_REJECTED",
                             window_start=start, window_end=end)
    total = placed + rejected
    if total == 0:
        return _empty_diagnostic()
    value = Decimal(rejected) / Decimal(total)
    return DiagnosticWeek(
        value=value, observations=total,
        top_reasons=_top_reasons(
            session, event_types=("ORDER_REJECTED",),
            window_start=start, window_end=end,
        ),
    )


def _build_paper_trade_count(
    session: Session, week: WeekWindow,
) -> DiagnosticWeek:
    """L5: paper_fill side='ENTRY' AND position_id IS NOT NULL.
    L7: observations = trading days observed in week.
    L22: zero observations → value=None. Otherwise the integer fill count,
    including 0 when the week had trading days but no entry fills."""
    obs = _trading_days_observed(session, week)
    if obs == 0:
        return _empty_diagnostic()
    start, end = _eod_window(week)
    count = int(session.scalar(
        select(func.count(PaperFill.id))
        .where(and_(
            PaperFill.side == "ENTRY",
            PaperFill.position_id.is_not(None),
            PaperFill.filled_at >= start,
            PaperFill.filled_at <= end,
        )),
    ) or 0)
    return DiagnosticWeek(
        value=count, observations=obs, top_reasons=(),
    )


def _build_engine_invariant_errors(
    session: Session, week: WeekWindow,
) -> DiagnosticWeek:
    """L6: observations = TICK_COMPLETED + ENGINE_INVARIANT_ERROR.
    L22: if total = 0 (no tick activity at all this week), value=None.
    If total>0 and errors=0, value=0 (truthful: ticks ran, none broke)."""
    start, end = _eod_window(week)
    completed = _count_audit(session, event_type="TICK_COMPLETED",
                              window_start=start, window_end=end)
    errored = _count_audit(session, event_type="ENGINE_INVARIANT_ERROR",
                            window_start=start, window_end=end)
    total = completed + errored
    if total == 0:
        return _empty_diagnostic()
    return DiagnosticWeek(
        value=errored, observations=total,
        top_reasons=_top_reasons(
            session, event_types=("ENGINE_INVARIANT_ERROR",),
            window_start=start, window_end=end,
        ),
    )


def _build_diagnostics(session: Session, week: WeekWindow) -> DiagnosticsWeek:
    return DiagnosticsWeek(
        tick_success_rate=_build_tick_success_rate(session, week),
        order_rejection_rate=_build_order_rejection_rate(session, week),
        paper_trade_count=_build_paper_trade_count(session, week),
        engine_invariant_errors=_build_engine_invariant_errors(session, week),
    )


def _operational_floor(backup: dict | None) -> OperationalFloor:
    """Map PR2's normalized backup section (charter_metrics.build_backup_section)
    onto OperationalFloor.

    L14: a 'missing' status (also None / unknown status) means no usable
    manifest → manifest_available=False with missing + stale + Nones. Only
    'ok'/'failed' are real manifests; for those we read PR2's computed
    is_stale and last_backup_at (PR2 owns the single staleness definition)."""
    status = backup.get("status") if backup else None
    if status not in {"ok", "failed"}:
        return OperationalFloor(
            backup_status="missing", backup_is_stale=True,
            backup_last_at=None, backup_error=None,
            manifest_available=False,
        )
    return OperationalFloor(
        backup_status=status,
        backup_is_stale=bool(backup.get("is_stale", True)),
        backup_last_at=backup.get("last_backup_at"),
        backup_error=backup.get("error"),
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
    backup_section: dict | None,
    generated_at: datetime,
) -> CharterReviewPayload:
    """Build the payload. `backup_section` is PR2's normalized backup dict
    (charter_metrics.build_backup_section), or None when unavailable."""
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
        diagnostics_this=_build_diagnostics(session, this_window),
        diagnostics_prior=_build_diagnostics(session, prior_window),
        operational_floor=_operational_floor(backup_section),
        appendix_snapshot=_appendix_snapshot(session, this_window),
    )
