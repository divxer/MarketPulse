"""Phase 5c aggregations: date-windowed totals, per-ticker rollups, hold days."""
from datetime import UTC, date, datetime

import pytest

from marketpulse.db.models import Trade


def _dt(y, m, d): return datetime(y, m, d, tzinfo=UTC)


def _trade(session, ticker, action, qty, price, when, *, pl=None):
    t = Trade(ticker=ticker, action=action, quantity=qty, price=price,
              fees=0.0, executed_at=when,
              realized_pl=pl if action == "sell" else None)
    session.add(t)
    session.commit()
    return t


def test_total_realized_pl_with_from_to_inclusive(db_session):
    from marketpulse.holdings.trades import total_realized_pl

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell",  5, 120.0, _dt(2026, 3, 15), pl=100.0)
    _trade(db_session, "AAPL", "sell",  5, 130.0, _dt(2026, 6, 30), pl=150.0)

    # No window → both sells.
    assert total_realized_pl(db_session) == pytest.approx(250.0)
    # Window covers only first sell.
    assert total_realized_pl(
        db_session,
        from_date=date(2026, 1, 1),
        to_date=date(2026, 3, 31),
    ) == pytest.approx(100.0)
    # Inclusive boundary.
    assert total_realized_pl(
        db_session,
        from_date=date(2026, 3, 15),
        to_date=date(2026, 3, 15),
    ) == pytest.approx(100.0)


def test_total_realized_pl_ignores_buys(db_session):
    """Even with realized_pl=None on buys, no effect on sum."""
    from marketpulse.holdings.trades import total_realized_pl

    _trade(db_session, "AAPL", "buy", 10, 100.0, _dt(2026, 1, 1))
    assert total_realized_pl(db_session) == 0.0
    assert total_realized_pl(
        db_session,
        from_date=date(2025, 1, 1),
        to_date=date(2026, 12, 31),
    ) == 0.0


def test_trading_stats_window_filters_sells(db_session):
    from marketpulse.holdings.service import trading_stats

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell",  5, 120.0, _dt(2026, 3, 15), pl=100.0)
    _trade(db_session, "AAPL", "sell",  3, 90.0,  _dt(2026, 4, 1),  pl=-30.0)
    _trade(db_session, "AAPL", "sell",  2, 130.0, _dt(2026, 7, 1),  pl=60.0)

    # All-time: 3 sells, 2 wins, 1 loss
    s_all = trading_stats(db_session)
    assert s_all["wins"] == 2
    assert s_all["losses"] == 1
    assert s_all["win_rate_pct"] == pytest.approx(66.66666, rel=1e-3)

    # Q1 only: 1 win
    s_q1 = trading_stats(
        db_session,
        from_date=date(2026, 1, 1), to_date=date(2026, 3, 31),
    )
    assert s_q1["wins"] == 1
    assert s_q1["losses"] == 0
    assert s_q1["win_rate_pct"] == pytest.approx(100.0)


def test_trading_stats_no_closed_returns_none_win_rate(db_session):
    """Per spec: win_rate_pct is None when wins+losses == 0
    (template shows '—' instead of misleading '0.0%')."""
    from marketpulse.holdings.service import trading_stats

    _trade(db_session, "AAPL", "buy", 10, 100.0, _dt(2026, 1, 1))
    s = trading_stats(db_session)
    assert s["wins"] == 0
    assert s["losses"] == 0
    assert s["win_rate_pct"] is None  # not 0.0


def test_trading_stats_ticker_filter_still_works(db_session):
    """Don't break the existing single-arg path."""
    from marketpulse.holdings.service import trading_stats

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell",  5, 120.0, _dt(2026, 6, 1), pl=100.0)
    _trade(db_session, "NVDA", "buy",  10, 50.0,  _dt(2026, 1, 1))
    _trade(db_session, "NVDA", "sell",  5, 40.0,  _dt(2026, 6, 1), pl=-50.0)

    s_aapl = trading_stats(db_session, ticker="AAPL")
    assert s_aapl["wins"] == 1 and s_aapl["losses"] == 0
    s_nvda = trading_stats(db_session, ticker="NVDA")
    assert s_nvda["wins"] == 0 and s_nvda["losses"] == 1
