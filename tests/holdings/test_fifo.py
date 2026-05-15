"""FIFO lot matching: pair buys and sells in time order, per-ticker."""
from datetime import UTC, datetime

import pytest

from marketpulse.db.models import Trade


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=UTC)


def _trade(session, ticker, action, qty, price, when) -> Trade:
    t = Trade(
        ticker=ticker,
        action=action,
        quantity=qty,
        price=price,
        fees=0.0,
        executed_at=when,
        realized_pl=None if action == "buy" else 0.0,  # placeholder; FIFO never writes this
    )
    session.add(t)
    session.commit()
    return t


def test_simple_buy_sell_full_close(db_session):
    """One buy of 10 @ $100, one sell of 10 @ $120 → one LotMatch, PL=+200."""
    from marketpulse.holdings.fifo import match_lots_fifo

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell", 10, 120.0, _dt(2026, 7, 1))
    matches = match_lots_fifo(db_session)
    assert len(matches) == 1
    m = matches[0]
    assert m.ticker == "AAPL"
    assert m.quantity == 10
    assert m.realized_pl == pytest.approx(200.0)
    assert m.hold_days == 181  # Jan 1 → Jul 1 = 181 days


def test_partial_sell_keeps_open_lot(db_session):
    """Buy 10, sell 4 → one LotMatch qty=4; remaining 6 unmatched."""
    from marketpulse.holdings.fifo import match_lots_fifo

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell",  4, 120.0, _dt(2026, 4, 1))
    matches = match_lots_fifo(db_session)
    assert len(matches) == 1
    assert matches[0].quantity == 4
    assert matches[0].realized_pl == pytest.approx(80.0)


def test_multi_buys_one_sell_fifo_order(db_session):
    """Buy 10 @ $100, buy 20 @ $110, sell 15 @ $130 →
    2 LotMatches: 10 from first lot (PL=300), 5 from second (PL=100).
    """
    from marketpulse.holdings.fifo import match_lots_fifo

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "buy",  20, 110.0, _dt(2026, 2, 1))
    _trade(db_session, "AAPL", "sell", 15, 130.0, _dt(2026, 6, 1))
    matches = match_lots_fifo(db_session)
    assert len(matches) == 2
    assert matches[0].quantity == 10
    assert matches[0].realized_pl == pytest.approx(300.0)
    assert matches[1].quantity == 5
    assert matches[1].realized_pl == pytest.approx(100.0)


def test_cross_ticker_isolated(db_session):
    """AAPL buy and NVDA sell never produce a cross-ticker match."""
    from marketpulse.holdings.fifo import match_lots_fifo

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "NVDA", "sell",  5, 200.0, _dt(2026, 6, 1))
    matches = match_lots_fifo(db_session)
    # NVDA sell has no matching open lot (no prior NVDA buy) → 0 matches.
    assert matches == []


def test_sell_exceeds_open_quantity_drops(db_session):
    """Buy 30, sell 50 → only 30 matched; overflow silently dropped."""
    from marketpulse.holdings.fifo import match_lots_fifo

    _trade(db_session, "AAPL", "buy",  30, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell", 50, 120.0, _dt(2026, 6, 1))
    matches = match_lots_fifo(db_session)
    assert len(matches) == 1
    assert matches[0].quantity == 30  # not 50


def test_hold_days_calculation(db_session):
    """Buy 2026-01-01, sell 2026-06-30 → 180 days exactly."""
    from marketpulse.holdings.fifo import match_lots_fifo

    _trade(db_session, "AAPL", "buy",  1, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell", 1, 120.0, _dt(2026, 6, 30))
    matches = match_lots_fifo(db_session)
    assert matches[0].hold_days == 180


def test_buy_after_sell_is_independent_lot(db_session):
    """Buy 10, sell 10 (clean close), buy 10, sell 5 →
    2 LotMatches: original (10), new (5). Times must reflect 2nd buy.
    """
    from marketpulse.holdings.fifo import match_lots_fifo

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell", 10, 120.0, _dt(2026, 3, 1))
    _trade(db_session, "AAPL", "buy",  10, 150.0, _dt(2026, 5, 1))
    _trade(db_session, "AAPL", "sell",  5, 160.0, _dt(2026, 7, 1))
    matches = match_lots_fifo(db_session)
    assert len(matches) == 2
    assert matches[0].quantity == 10  # first round
    assert matches[1].quantity == 5
    assert matches[1].buy_executed_at == _dt(2026, 5, 1)  # 2nd buy
    assert matches[1].sell_executed_at == _dt(2026, 7, 1)


def test_excludes_splits_and_dividends(db_session):
    """Only Trade rows participate; Splits/Dividends ignored."""
    from datetime import date

    from marketpulse.db.models import Dividend, StockSplit
    from marketpulse.holdings.fifo import match_lots_fifo

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell", 10, 120.0, _dt(2026, 6, 1))
    db_session.add(StockSplit(ticker="AAPL", ex_date=date(2026, 3, 1),
                              ratio=2.0, source="manual"))
    db_session.add(Dividend(ticker="AAPL", ex_date=date(2026, 4, 1),
                            amount_per_share=0.25, total_amount=2.50,
                            source="manual"))
    db_session.commit()

    matches = match_lots_fifo(db_session)
    assert len(matches) == 1  # only the buy/sell pair


def test_executed_at_none_falls_back_to_created_at(db_session):
    """When executed_at is None, ordering and hold_days use created_at.

    Both buy and sell have executed_at=None so nulls_last places them after
    all dated rows; within that group id-order (insertion order) keeps buy
    before sell, and hold_days is derived from their backdated created_at.
    """
    from marketpulse.holdings.fifo import match_lots_fifo

    # Insert buy then sell, both with executed_at=None.
    t_buy = Trade(
        ticker="AAPL", action="buy",
        quantity=10, price=100.0, fees=0.0,
        executed_at=None, realized_pl=None,
    )
    t_sell = Trade(
        ticker="AAPL", action="sell",
        quantity=10, price=120.0, fees=0.0,
        executed_at=None, realized_pl=0.0,
    )
    db_session.add_all([t_buy, t_sell])
    db_session.commit()

    # Backdate created_at to a deterministic spread (100 days apart).
    t_buy.created_at = _dt(2026, 1, 1)
    t_sell.created_at = _dt(2026, 4, 11)
    db_session.commit()

    matches = match_lots_fifo(db_session)
    assert len(matches) == 1
    assert matches[0].buy_executed_at == _dt(2026, 1, 1)
    assert matches[0].hold_days == 100


def test_empty_table_returns_empty_list(db_session):
    """No trades at all → empty list (not exception)."""
    from marketpulse.holdings.fifo import match_lots_fifo
    assert match_lots_fifo(db_session) == []


def test_fractional_multi_lot_sell_drains_cleanly(db_session):
    """Three 0.1-share buys + one 0.3-share sell → all lots consumed,
    no ghost lot left to corrupt the next match.
    """
    from marketpulse.holdings.fifo import match_lots_fifo

    _trade(db_session, "AAPL", "buy",  0.1, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "buy",  0.1, 100.0, _dt(2026, 1, 2))
    _trade(db_session, "AAPL", "buy",  0.1, 100.0, _dt(2026, 1, 3))
    _trade(db_session, "AAPL", "sell", 0.3, 120.0, _dt(2026, 6, 1))
    # Add a follow-up trade that would expose any ghost lot.
    _trade(db_session, "AAPL", "buy",  10.0, 200.0, _dt(2026, 7, 1))
    _trade(db_session, "AAPL", "sell", 5.0,  220.0, _dt(2026, 8, 1))

    matches = match_lots_fifo(db_session)
    # 3 fractional matches + 1 from the 10@200 → 5@220 = 4 total.
    # If ghost lot persisted, it would match part of the 5@220 sell
    # at price=100 instead of 200, inflating realized_pl.
    assert len(matches) == 4
    # The 5@220 match should be entirely at buy_price=200, not contaminated.
    fifth_match = matches[-1]  # last by sell_executed_at
    assert fifth_match.buy_price == 200.0
    assert fifth_match.realized_pl == pytest.approx(100.0)  # (220-200)*5
