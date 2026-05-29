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


def _seed_position(
    session, *, ticker: str, qty: int, opened: datetime,
    closed: datetime | None = None,
):
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
        status="ENTRY_FILLED",
        strategy_version="v1",
        allocator_version="v1",
        execution_engine_version="v1",
        weight=Decimal("1"),
    )
    session.add(order)
    session.flush()
    # Create position first (entry_fill_id/exit_fill_id populated after fills).
    pos = PaperPosition(
        order_id=order.id,
        entry_fill_id=None, exit_fill_id=None,
        strategy="general", ticker=ticker, quantity=qty,
        entry_price=Decimal("100"), entry_date=opened.date(),
        horizon_date=opened.date() + timedelta(days=7),
        status="OPEN",
        opened_at=opened, closed_at=None,
        exit_price=None,
        realized_pnl=None,
    )
    session.add(pos)
    session.flush()
    # Entry fill — always required.
    entry_fill = PaperFill(
        order_id=order.id, position_id=pos.id, side="ENTRY",
        price=Decimal("100"), quantity=qty, filled_at=opened,
        cash_delta=-Decimal("100") * qty, realized_pnl=None,
    )
    session.add(entry_fill)
    session.flush()
    pos.entry_fill_id = entry_fill.id
    if closed is not None:
        exit_fill = PaperFill(
            order_id=order.id, position_id=pos.id, side="EXIT",
            price=Decimal("105"), quantity=qty, filled_at=closed,
            cash_delta=Decimal("105") * qty,
            realized_pnl=Decimal("5") * qty,
        )
        session.add(exit_fill)
        session.flush()
        pos.exit_fill_id = exit_fill.id
        pos.status = "CLOSED"
        pos.closed_at = closed
        pos.exit_price = Decimal("105")
        pos.realized_pnl = Decimal("5") * qty
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


def test_run_nav_snapshot_historical_open_positions(db_session):
    """L7: time-predicate reconstruction. Position opened day-2 closed day-4;
    rebuild for day-3 includes it, rebuild for day-5 excludes it."""
    _seed_cash(db_session, "100000", datetime(2026, 5, 26, 13, 0, tzinfo=UTC))
    _seed_position(
        db_session, ticker="AAPL", qty=10,
        opened=datetime(2026, 5, 27, 14, 0, tzinfo=UTC),
        closed=datetime(2026, 5, 29, 14, 0, tzinfo=UTC),
    )
    _seed_price(db_session, "AAPL", date(2026, 5, 28), 200.0)
    _seed_price(db_session, "AAPL", date(2026, 5, 30), 200.0)
    db_session.commit()

    snap_d3 = run_nav_snapshot(db_session, trading_date=date(2026, 5, 28))
    db_session.commit()
    assert snap_d3.holdings_mtm == Decimal("2000")  # position still open

    snap_d5 = run_nav_snapshot(db_session, trading_date=date(2026, 5, 30))
    db_session.commit()
    assert snap_d5.holdings_mtm == Decimal("0")  # position already closed


def test_run_nav_snapshot_idempotent_pk_conflict(db_session):
    _seed_cash(db_session, "100000", datetime(2026, 5, 28, 13, 0, tzinfo=UTC))
    db_session.commit()

    snap1 = run_nav_snapshot(db_session, trading_date=date(2026, 5, 28))
    db_session.commit()
    snap2 = run_nav_snapshot(db_session, trading_date=date(2026, 5, 28))
    assert snap2.trading_date == snap1.trading_date
    assert snap2.portfolio_nav == snap1.portfolio_nav


def test_run_nav_snapshot_spy_anchor_late_establishment(db_session):
    """L16: SPY anchor establishes on first SPY-available snapshot."""
    _seed_cash(db_session, "100000", datetime(2026, 5, 26, 13, 0, tzinfo=UTC))
    db_session.commit()

    # Day 1: no SPY in cache → no SPY anchor
    snap_d1 = run_nav_snapshot(db_session, trading_date=date(2026, 5, 26))
    db_session.commit()
    assert snap_d1.anchor_spy_close is None
    assert snap_d1.spy_index is None
    assert snap_d1.excess_return is None

    # Day 2: SPY shows up
    _seed_price(db_session, "SPY", date(2026, 5, 27), 500.0)
    db_session.commit()
    snap_d2 = run_nav_snapshot(db_session, trading_date=date(2026, 5, 27))
    db_session.commit()
    assert snap_d2.anchor_spy_close == Decimal("500")
    assert snap_d2.spy_close == Decimal("500")
    # spy_index = 500/500 = 1
    assert snap_d2.spy_index == Decimal("1")

    # Day 3: SPY moves; anchor stays at 500
    _seed_price(db_session, "SPY", date(2026, 5, 28), 510.0)
    db_session.commit()
    snap_d3 = run_nav_snapshot(db_session, trading_date=date(2026, 5, 28))
    db_session.commit()
    assert snap_d3.anchor_spy_close == Decimal("500")
    assert snap_d3.spy_index == Decimal("510") / Decimal("500")

    # Day 1 row remains frozen with null benchmark side.
    persisted_d1 = get_snapshot(db_session, date(2026, 5, 26))
    assert persisted_d1.anchor_spy_close is None
    assert persisted_d1.spy_index is None


def test_run_nav_snapshot_partial_pricing(db_session):
    """3 positions, 1 unpriced → unpriced_count=1, MTM reflects only priced."""
    _seed_cash(db_session, "100000", datetime(2026, 5, 28, 13, 0, tzinfo=UTC))
    for ticker in ("AAPL", "GOOGL", "XYZ"):
        _seed_position(
            db_session, ticker=ticker, qty=5,
            opened=datetime(2026, 5, 28, 14, 0, tzinfo=UTC),
        )
    _seed_price(db_session, "AAPL", date(2026, 5, 28), 100.0)
    _seed_price(db_session, "GOOGL", date(2026, 5, 28), 200.0)
    # XYZ intentionally absent.
    db_session.commit()

    snap = run_nav_snapshot(db_session, trading_date=date(2026, 5, 28))
    assert snap.holdings_mtm == Decimal("1500")  # 5*100 + 5*200
    assert snap.unpriced_positions_count == 1
    assert snap.unpriced_tickers == ("XYZ",)


def test_run_nav_snapshot_no_network(db_session, monkeypatch):
    """L5: snapshot runner does NOT touch yfinance. Even if any yfinance
    import is monkeypatched to raise, the snapshot still succeeds."""
    import marketpulse.data.yfinance_client as yf_mod

    def boom(*a, **kw):  # noqa: ANN001
        raise RuntimeError("yfinance must not be called from snapshot path")

    monkeypatch.setattr(yf_mod.YFinanceClient, "__init__", boom, raising=False)

    _seed_cash(db_session, "100000", datetime(2026, 5, 28, 13, 0, tzinfo=UTC))
    db_session.commit()
    snap = run_nav_snapshot(db_session, trading_date=date(2026, 5, 28))
    assert snap.portfolio_nav == Decimal("100000")


def test_run_nav_snapshot_repo_error_propagates(db_session, monkeypatch):
    """L4: non-PK persistence errors are NOT swallowed by the runner."""
    _seed_cash(db_session, "100000", datetime(2026, 5, 28, 13, 0, tzinfo=UTC))
    db_session.commit()

    def boom(session, snapshot):  # noqa: ANN001
        raise RuntimeError("disk full")

    monkeypatch.setattr(
        "marketpulse.portfolio.snapshot_runner.insert_snapshot", boom,
    )
    with pytest.raises(RuntimeError, match="disk full"):
        run_nav_snapshot(db_session, trading_date=date(2026, 5, 28))
