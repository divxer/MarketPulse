from datetime import UTC, date, datetime

import pytest

from marketpulse.holdings.quantity_history import quantity_as_of
from marketpulse.holdings.splits import record_split
from marketpulse.holdings.trades import record_trade


def test_qty_zero_when_never_held(db_session) -> None:
    assert quantity_as_of(db_session, "NEVER", date(2025, 1, 1)) == 0


def test_qty_after_single_buy(db_session) -> None:
    record_trade(db_session, ticker="X", action="buy", quantity=20, price=30,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    assert quantity_as_of(db_session, "X", date(2024, 1, 15)) == 20
    assert quantity_as_of(db_session, "X", date(2024, 1, 14)) == 0  # day before buy


def test_qty_after_buy_sell_sequence(db_session) -> None:
    record_trade(db_session, ticker="X", action="buy", quantity=20, price=30,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    record_trade(db_session, ticker="X", action="sell", quantity=8, price=40,
                 executed_at=datetime(2024, 6, 1, tzinfo=UTC))
    assert quantity_as_of(db_session, "X", date(2024, 1, 15)) == 20
    assert quantity_as_of(db_session, "X", date(2024, 5, 31)) == 20
    assert quantity_as_of(db_session, "X", date(2024, 6, 1)) == 12
    assert quantity_as_of(db_session, "X", date(2025, 1, 1)) == 12


def test_qty_after_split_doubles(db_session) -> None:
    """1:2 forward split doubles the snapshot starting on ex_date."""
    record_trade(db_session, ticker="X", action="buy", quantity=20, price=30,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    record_split(db_session, ticker="X", ex_date=date(2025, 6, 1), ratio=2.0)
    # Split is EOD-anchored; on ex_date the qty reflects the split.
    assert quantity_as_of(db_session, "X", date(2025, 5, 31)) == 20
    assert quantity_as_of(db_session, "X", date(2025, 6, 1)) == 40
    assert quantity_as_of(db_session, "X", date(2026, 1, 1)) == 40


def test_qty_full_sale_then_zero(db_session) -> None:
    """Selling 100% leaves qty == 0 after the sell date."""
    record_trade(db_session, ticker="X", action="buy", quantity=10, price=30,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    record_trade(db_session, ticker="X", action="sell", quantity=10, price=50,
                 executed_at=datetime(2024, 12, 1, tzinfo=UTC))
    assert quantity_as_of(db_session, "X", date(2024, 6, 1)) == 10
    assert quantity_as_of(db_session, "X", date(2024, 12, 1)) == 0
    assert quantity_as_of(db_session, "X", date(2025, 12, 1)) == 0


def test_qty_split_then_partial_sell(db_session) -> None:
    """Split first, then sell — snapshot reflects post-split qty minus sold."""
    record_trade(db_session, ticker="X", action="buy", quantity=20, price=60,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    record_split(db_session, ticker="X", ex_date=date(2024, 6, 1), ratio=2.0)
    record_trade(db_session, ticker="X", action="sell", quantity=15, price=35,
                 executed_at=datetime(2024, 9, 1, tzinfo=UTC))
    # Post-split: 40 shares. After selling 15: 25.
    assert quantity_as_of(db_session, "X", date(2024, 1, 15)) == 20
    assert quantity_as_of(db_session, "X", date(2024, 6, 1)) == 40
    assert quantity_as_of(db_session, "X", date(2024, 8, 31)) == 40
    assert quantity_as_of(db_session, "X", date(2024, 9, 1)) == 25


def test_qty_same_day_buy_counted(db_session) -> None:
    """A buy at 09:30 on as_of date IS included in the snapshot
    (same-day buy already settled by end-of-day)."""
    record_trade(db_session, ticker="X", action="buy", quantity=20, price=30,
                 executed_at=datetime(2024, 1, 15, 9, 30, tzinfo=UTC))
    assert quantity_as_of(db_session, "X", date(2024, 1, 15)) == 20
