# Layer: orchestration
"""snapshot_runner — reads forward state, computes NavSnapshot, persists.

Called at the end of paper_trading_tick. L4: persistence errors (non-PK)
propagate; the scheduler catches them and logs. L18: empty cash ledger
raises NoCashLedgerForDate.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketpulse.db.models import (
    PaperCashLedger,
    PaperPosition,
    PriceCacheEntry,
)
from marketpulse.portfolio.north_star import (
    NORTH_STAR_WINDOW,
    NavSnapshot,
    OpenPosition,
    compute_nav_snapshot,
)
from marketpulse.portfolio.snapshot_repo import (
    SnapshotAlreadyExists,
    count_snapshots_in_window,
    get_earliest_eligible_snapshot,
    get_snapshot,
    insert_snapshot,
)

log = logging.getLogger(__name__)

_SPY_TICKER = "SPY"


class NoCashLedgerForDate(Exception):
    """L18: paper_cash_ledger has no row with timestamp <= EOD(trading_date)."""


def _eod_utc(trading_date: date) -> datetime:
    """End-of-day in UTC. Paper engine timestamps are TZ-aware UTC."""
    return datetime.combine(trading_date, time.max, tzinfo=UTC)


def _read_cash_balance(session: Session, trading_date: date) -> Decimal:
    eod = _eod_utc(trading_date)
    row = session.scalars(
        select(PaperCashLedger)
        .where(PaperCashLedger.timestamp <= eod)
        .order_by(PaperCashLedger.timestamp.desc())
        .limit(1),
    ).first()
    if row is None:
        raise NoCashLedgerForDate(
            f"no paper_cash_ledger row at or before EOD {trading_date}"
        )
    return row.balance_after


def _read_open_positions(
    session: Session, trading_date: date,
) -> list[OpenPosition]:
    """L7: historical-safe — time predicates only, never status='OPEN'."""
    eod = _eod_utc(trading_date)
    rows = session.scalars(
        select(PaperPosition).where(
            PaperPosition.opened_at <= eod,
            (PaperPosition.closed_at.is_(None))
            | (PaperPosition.closed_at > eod),
        ),
    ).all()
    return [
        OpenPosition(ticker=r.ticker, quantity=Decimal(r.quantity))
        for r in rows
    ]


def _read_price_lookup(session: Session, trading_date: date):
    """L5/L19: price_cache.close as-is; Float → Decimal at the boundary."""
    rows = session.scalars(
        select(PriceCacheEntry).where(PriceCacheEntry.date == trading_date),
    ).all()
    table = {r.ticker: Decimal(str(r.close)) for r in rows}

    def lookup(ticker: str) -> Decimal | None:
        return table.get(ticker)

    return lookup, table.get(_SPY_TICKER)


def run_nav_snapshot(
    session: Session, *, trading_date: date,
) -> NavSnapshot:
    """Read forward state, compute, persist. Returns the snapshot.

    Idempotent re-run: if a snapshot for `trading_date` already exists,
    log + return it WITHOUT recomputing (avoids wasted work AND prevents
    trading_days_observed drift from re-counting a finalized day).

    All non-PK persistence errors propagate (L4). The PK race path
    (concurrent writer) rolls back the half-formed add() and returns
    the row that actually won.
    """
    # Idempotency check FIRST — before any read/compute work.
    existing = get_snapshot(session, trading_date)
    if existing is not None:
        log.warning(
            "nav_snapshot_idempotent_rerun",
            extra={"tick_date": str(trading_date)},
        )
        return existing

    cash_balance = _read_cash_balance(session, trading_date)
    open_positions = _read_open_positions(session, trading_date)
    price_lookup, spy_close = _read_price_lookup(session, trading_date)

    # North-star anchor (PR3a + anchor-eligibility fix): anchor BOTH indices to
    # the earliest ELIGIBLE snapshot — fully priced (unpriced_positions_count==0)
    # AND benchmarked (spy_close present). Degenerate rows (pre-market manual
    # triggers, price_cache gaps, transient SPY/position-price absence) are
    # skipped so they never pollute the inception baseline; otherwise a
    # cash-only first row would inflate portfolio_index for the entire series.
    # A shared inception day also keeps excess_return coherent.
    eligible = get_earliest_eligible_snapshot(session)
    if eligible is not None:
        anchor_portfolio_nav = eligible.portfolio_nav
        anchor_spy_close = eligible.spy_close
    else:
        # No clean inception yet. Self-anchor THIS row for internal consistency
        # (index 1.0). If this row is itself ineligible (no SPY / unpriced
        # positions) it is NOT inherited — the first later eligible snapshot
        # becomes the true inception and re-anchors the series.
        # L6: preview omits unpriced positions (never `(price or 0)`), matching
        # the pure compute function, so the self-anchor can't include phantom
        # zero-price MTM.
        portfolio_nav_preview = cash_balance
        for pos in open_positions:
            price = price_lookup(pos.ticker)
            if price is not None:
                portfolio_nav_preview += pos.quantity * price
        anchor_portfolio_nav = portfolio_nav_preview
        anchor_spy_close = spy_close

    trading_days_observed = count_snapshots_in_window(
        session, window_end=trading_date, window_size=NORTH_STAR_WINDOW,
    ) + 1

    snapshot = compute_nav_snapshot(
        trading_date=trading_date,
        cash_balance=cash_balance,
        open_positions=open_positions,
        price_lookup=price_lookup,
        spy_close=spy_close,
        anchor_portfolio_nav=anchor_portfolio_nav,
        anchor_spy_close=anchor_spy_close,
        trading_days_observed=trading_days_observed,
    )

    try:
        insert_snapshot(session, snapshot)
    except SnapshotAlreadyExists:
        # True race: a concurrent writer landed the row between our
        # get_snapshot() at the top and our flush. Rollback the half-formed
        # add() so the caller's transaction stays clean, then return the
        # row that actually won.
        session.rollback()
        log.warning(
            "nav_snapshot_pk_conflict_race",
            extra={"tick_date": str(trading_date)},
        )
        winning = get_snapshot(session, trading_date)
        assert winning is not None  # PK conflict implies row exists
        return winning

    return snapshot
