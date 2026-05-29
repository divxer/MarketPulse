# Layer: test
"""PR3a — snapshot_runner tests."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from marketpulse.db.models import (
    PaperCashLedger,
    PaperFill,
    PaperOrder,
    PaperPosition,
    PriceCacheEntry,
)
from marketpulse.portfolio.snapshot_repo import get_snapshot
from marketpulse.portfolio.snapshot_runner import (
    NoCashLedgerForDate,
    run_nav_snapshot,
)


def _seed_cash(session, balance: str, ts: datetime, reason: str = "INITIAL_DEPOSIT"):
    row = PaperCashLedger(
        timestamp=ts,
        delta=Decimal(balance),
        reason=reason,
        fill_id=None,
        balance_after=Decimal(balance),
    )
    session.add(row)
    session.flush()


def _seed_price(session, ticker: str, d: date, close: float):
    session.add(PriceCacheEntry(
        ticker=ticker, date=d, open=close, high=close, low=close,
        close=close, volume=1, fetched_at=datetime(d.year, d.month, d.day, tzinfo=UTC),
    ))


def _seed_position(session, *, ticker: str, qty: int, opened: datetime, closed: datetime | None = None):
    order = PaperOrder(
        idempotency_key=f"{ticker}-{opened.isoformat()}",
        allocation_run_id=f"test-run-{opened.date().isoformat()}",
        strategy="general",
        ticker=ticker,
        quantity=qty,
        event_time=opened,
        allocation_date=opened.date(),
        horizon_date=opened.date() + timedelta(days=7),
        placed_at=opened,
        filled_at=opened,
        cancelled_at=None,
        cancel_reason=None,
        event_price=Decimal("100"),
        horizon_price=None,
        status="ENTRY_FILLED" if closed is None else "EXIT_FILLED",
        strategy_version="v1",
        allocator_version="v1",
        execution_engine_version="v1",
        weight=Decimal("1"),
    )
    session.add(order)
    session.flush()
    pos = PaperPosition(
        order_id=order.id,
        entry_fill_id=None, exit_fill_id=None,
        strategy="general", ticker=ticker, quantity=qty,
        entry_price=Decimal("100"), entry_date=opened.date(),
        horizon_date=opened.date() + timedelta(days=7),
        status="OPEN" if closed is None else "CLOSED",
        opened_at=opened, closed_at=closed,
        exit_price=None if closed is None else Decimal("105"),
        realized_pnl=None if closed is None else Decimal("5"),
    )
    session.add(pos)
    session.flush()
    return pos


def test_run_nav_snapshot_empty_cash_ledger_raises(db_session):
    """L18: no cash ledger row → NoCashLedgerForDate raised."""
    with pytest.raises(NoCashLedgerForDate):
        run_nav_snapshot(db_session, trading_date=date(2026, 5, 28))


def test_run_nav_snapshot_first_run_self_anchors(db_session):
    """Fresh DB, 1 priced position; row created with self-anchor on portfolio."""
    _seed_cash(db_session, "100000", datetime(2026, 5, 28, 13, 0, tzinfo=UTC))
    _seed_position(db_session, ticker="AAPL", qty=10,
                   opened=datetime(2026, 5, 28, 14, 0, tzinfo=UTC))
    _seed_price(db_session, "AAPL", date(2026, 5, 28), 200.0)
    db_session.commit()

    snap = run_nav_snapshot(db_session, trading_date=date(2026, 5, 28))
    db_session.commit()

    assert snap.cash_balance == Decimal("100000")
    assert snap.holdings_mtm == Decimal("2000")
    assert snap.portfolio_nav == Decimal("102000")
    assert snap.anchor_portfolio_nav == Decimal("102000")  # self-anchored
    assert snap.portfolio_index == Decimal("1")
    assert snap.spy_close is None  # no SPY in price_cache
    assert snap.spy_index is None
    assert snap.excess_return is None

    persisted = get_snapshot(db_session, date(2026, 5, 28))
    assert persisted is not None
    assert persisted.portfolio_nav == Decimal("102000")
