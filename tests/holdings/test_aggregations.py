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


def test_monthly_realized_pl_default_returns_all_months_no_fill(db_session):
    """Default months=None: matches existing behavior used by /holdings."""
    from marketpulse.holdings.service import monthly_realized_pl

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell",  5, 120.0, _dt(2026, 3, 15), pl=100.0)
    _trade(db_session, "AAPL", "sell",  3, 90.0,  _dt(2026, 7, 1),  pl=-30.0)

    rows = monthly_realized_pl(db_session)
    months = [r["month"] for r in rows]
    # Only the 2 months with sells; no Feb/Apr/May/Jun padding.
    assert months == ["2026-03", "2026-07"]


def test_monthly_realized_pl_with_months_fills_gaps(db_session):
    """months=15: trailing 15 calendar months (incl. current), missing → 0."""
    import datetime as dt

    from marketpulse.holdings.service import monthly_realized_pl

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell",  5, 120.0, _dt(2026, 3, 15), pl=100.0)

    rows = monthly_realized_pl(db_session, months=15)
    assert len(rows) == 15
    # Sorted ascending; last entry = current month.
    today = dt.date.today()
    assert rows[-1]["month"] == f"{today.year:04d}-{today.month:02d}"
    # Empty months → pl == 0, trade_count == 0
    march = next(r for r in rows if r["month"] == "2026-03")
    assert march["pl"] == pytest.approx(100.0)
    other = [r for r in rows if r["month"] != "2026-03"]
    for r in other:
        assert r["pl"] == 0.0
        assert r["trade_count"] == 0


def test_trade_count_this_month_classifies(db_session, monkeypatch):
    """Counts BUY/SELL/dividend in the current calendar month (UTC)."""
    from datetime import date

    import marketpulse.holdings.service as svc
    from marketpulse.db.models import Dividend
    from marketpulse.holdings.service import trade_count_this_month

    # Freeze "today" via a tiny shim — the function reads date.today() in svc.
    class _FakeDate(date):
        @classmethod
        def today(cls): return date(2026, 5, 15)
    monkeypatch.setattr(svc, "date", _FakeDate)

    # In-month
    _trade(db_session, "AAPL", "buy",  1, 100, _dt(2026, 5, 3))
    _trade(db_session, "AAPL", "buy",  1, 100, _dt(2026, 5, 10))
    _trade(db_session, "AAPL", "sell", 1, 120, _dt(2026, 5, 12), pl=20.0)
    db_session.add(Dividend(ticker="AAPL", ex_date=date(2026, 5, 8),
                            amount_per_share=0.25, total_amount=0.25))
    # Out of month
    _trade(db_session, "AAPL", "buy", 1, 100, _dt(2026, 4, 30))
    _trade(db_session, "AAPL", "buy", 1, 100, _dt(2026, 6, 1))
    db_session.commit()

    counts = trade_count_this_month(db_session)
    assert counts == {"total": 4, "buys": 2, "sells": 1, "dividends": 1}


def test_trade_count_this_month_empty(db_session):
    from marketpulse.holdings.service import trade_count_this_month
    assert trade_count_this_month(db_session) == {
        "total": 0, "buys": 0, "sells": 0, "dividends": 0,
    }


def test_realized_pl_by_ticker_orders_by_abs(db_session):
    """A -2000 loss ranks above a +1000 gain in 'biggest movers' view."""
    from marketpulse.holdings.service import realized_pl_by_ticker

    _trade(db_session, "AAPL", "buy",  10, 100, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell", 10, 200, _dt(2026, 6, 1), pl=+1000.0)
    _trade(db_session, "NVDA", "buy",  10, 300, _dt(2026, 1, 1))
    _trade(db_session, "NVDA", "sell", 10, 100, _dt(2026, 6, 1), pl=-2000.0)

    rows = realized_pl_by_ticker(db_session)
    assert [r["ticker"] for r in rows] == ["NVDA", "AAPL"]
    assert rows[0]["realized_pl"] == pytest.approx(-2000.0)
    assert rows[1]["realized_pl"] == pytest.approx(+1000.0)


def test_realized_pl_by_ticker_top_n(db_session):
    """top_n=2 with 3 tickers → only top 2 by abs(pl)."""
    from marketpulse.holdings.service import realized_pl_by_ticker

    # Use distinct buy/sell prices per ticker so FIFO computes genuinely
    # different P&Ls. NVDA=+400, AAPL=+100, TSLA=+20.
    for sym, buy, sell in [("AAPL", 10, 20), ("NVDA", 10, 50), ("TSLA", 10, 12)]:
        _trade(db_session, sym, "buy",  10, buy,  _dt(2026, 1, 1))
        _trade(db_session, sym, "sell", 10, sell, _dt(2026, 6, 1))

    rows = realized_pl_by_ticker(db_session, top_n=2)
    assert len(rows) == 2
    # NVDA has the largest abs(PL), AAPL second; TSLA must be excluded.
    assert [r["ticker"] for r in rows] == ["NVDA", "AAPL"]


def test_realized_pl_by_ticker_pct_uses_lot_cost_basis(db_session):
    """pct = realized_pl / sum(qty*buy_price for matched lots) * 100."""
    from marketpulse.holdings.service import realized_pl_by_ticker

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell", 10, 120.0, _dt(2026, 6, 1), pl=200.0)
    rows = realized_pl_by_ticker(db_session)
    # cost basis of sold lots = 10 * 100 = 1000; pct = 200/1000 * 100 = 20%
    # NOTE: this uses LotMatch.realized_pl (gross, no fees), so the displayed
    # pct may diverge from Trade.realized_pl-based numbers for portfolios
    # with non-zero fees. This is intentional and documented in fifo.py.
    assert rows[0]["pct"] == pytest.approx(20.0)


def test_realized_pl_by_ticker_empty(db_session):
    from marketpulse.holdings.service import realized_pl_by_ticker
    assert realized_pl_by_ticker(db_session) == []


def test_avg_hold_days_basic(db_session):
    """Two matches: 100 days and 200 days → avg 150."""
    from marketpulse.holdings.service import avg_hold_days

    _trade(db_session, "AAPL", "buy",  1, 100, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell", 1, 120, _dt(2026, 4, 11), pl=20.0)  # 100d
    _trade(db_session, "NVDA", "buy",  1, 100, _dt(2026, 1, 1))
    _trade(db_session, "NVDA", "sell", 1, 120, _dt(2026, 7, 20), pl=20.0)  # 200d

    assert avg_hold_days(db_session) == pytest.approx(150.0)


def test_avg_hold_days_no_data_returns_none(db_session):
    from marketpulse.holdings.service import avg_hold_days
    assert avg_hold_days(db_session) is None

    _trade(db_session, "AAPL", "buy", 1, 100, _dt(2026, 1, 1))
    # No sells → no matches → None
    assert avg_hold_days(db_session) is None


def test_avg_hold_days_window_filters_by_sell_date(db_session):
    """Only LotMatches whose sell_executed_at falls in window count."""
    from datetime import date

    from marketpulse.holdings.service import avg_hold_days

    _trade(db_session, "AAPL", "buy",  1, 100, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell", 1, 120, _dt(2026, 3, 1), pl=20.0)  # 59d
    _trade(db_session, "NVDA", "buy",  1, 100, _dt(2026, 1, 1))
    _trade(db_session, "NVDA", "sell", 1, 120, _dt(2026, 9, 1), pl=20.0)  # 243d

    # Both → avg of 59 and 243 = 151
    assert avg_hold_days(db_session) == pytest.approx(151.0)
    # Q1 only → 59
    val = avg_hold_days(
        db_session,
        from_date=date(2026, 1, 1), to_date=date(2026, 3, 31),
    )
    assert val == pytest.approx(59.0)


def test_enrich_holdings_adds_sector_today_change_sparkline(db_session):
    """enrich_holdings preserves existing fields and adds 3 new ones."""
    from datetime import date
    from unittest.mock import MagicMock

    from marketpulse.data.types import Bar, Quote
    from marketpulse.db.models import Holding
    from marketpulse.holdings.service import enrich_holdings

    h = Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0, sort_order=0,
                sector="Technology")
    db_session.add(h)
    db_session.commit()

    fake_data = MagicMock()
    fake_data.get_quote.return_value = Quote(
        ticker="AAPL", price=150.0, change_pct=1.5, volume=1000,
        avg_volume_20d=2000, fetched_at=_dt(2026, 5, 15), stale=False,
    )
    fake_data.get_history.return_value = [
        Bar(date=date(2026, 5, d), open=140.0, high=151.0, low=139.0,
            close=140.0 + d, volume=1000)
        for d in range(1, 16)
    ]

    rows = enrich_holdings([h], fake_data)
    assert len(rows) == 1
    r = rows[0]
    # Existing fields still present
    assert r["ticker"] == "AAPL"
    assert r["market_value"] == pytest.approx(1500.0)
    # New Phase 5d fields
    assert r["sector"] == "Technology"
    assert r["today_change_pct"] == pytest.approx(1.5)
    assert r["sparkline"] == [141.0, 142.0, 143.0, 144.0, 145.0,
                              146.0, 147.0, 148.0, 149.0, 150.0,
                              151.0, 152.0, 153.0, 154.0, 155.0]


def test_enrich_holdings_null_sector_falls_back_unclassified(db_session):
    from unittest.mock import MagicMock

    from marketpulse.data.types import Quote
    from marketpulse.db.models import Holding
    from marketpulse.holdings.service import enrich_holdings

    h = Holding(ticker="AAPL", quantity=10.0, avg_cost=100.0, sort_order=0,
                sector=None)
    db_session.add(h)
    db_session.commit()

    fake_data = MagicMock()
    fake_data.get_quote.return_value = Quote(
        ticker="AAPL", price=150.0, change_pct=0.5, volume=1000,
        avg_volume_20d=2000, fetched_at=_dt(2026, 5, 15), stale=False,
    )
    fake_data.get_history.return_value = []

    rows = enrich_holdings([h], fake_data)
    assert rows[0]["sector"] == "未分类"
    assert rows[0]["sparkline"] == []


def test_enrich_holdings_quote_failure_today_change_none(db_session):
    """Pre-existing tolerance: quote fetch fails → today_change_pct=None."""
    from unittest.mock import MagicMock

    from marketpulse.db.models import Holding
    from marketpulse.holdings.service import enrich_holdings

    h = Holding(ticker="ZZZ", quantity=10.0, avg_cost=100.0, sort_order=0)
    db_session.add(h)
    db_session.commit()

    fake_data = MagicMock()
    fake_data.get_quote.side_effect = RuntimeError("yfinance down")
    fake_data.get_history.side_effect = RuntimeError("yfinance down")

    rows = enrich_holdings([h], fake_data)
    assert rows[0]["today_change_pct"] is None
    assert rows[0]["sparkline"] == []


def test_today_portfolio_change_up_down_counts():
    from marketpulse.holdings.service import today_portfolio_change
    rows = [
        {"market_value": 1000.0, "today_change_pct": 2.0},
        {"market_value": 500.0, "today_change_pct": -1.0},
        {"market_value": 200.0, "today_change_pct": 0.5},
    ]
    result = today_portfolio_change(rows)
    assert result["up_count"] == 2
    assert result["down_count"] == 1


def test_today_portfolio_change_dollars_sum():
    from marketpulse.holdings.service import today_portfolio_change
    rows = [
        {"market_value": 1000.0, "today_change_pct": 2.0},
        {"market_value": 500.0, "today_change_pct": -1.0},
    ]
    result = today_portfolio_change(rows)
    assert result["dollars"] == pytest.approx(15.0)


def test_today_portfolio_change_pct_weighted_by_mv():
    from marketpulse.holdings.service import today_portfolio_change
    rows = [
        {"market_value": 1000.0, "today_change_pct": 1.0},
        {"market_value": 100.0, "today_change_pct": 10.0},
    ]
    result = today_portfolio_change(rows)
    assert result["pct"] == pytest.approx(1.818, rel=1e-2)


def test_today_portfolio_change_excludes_none_pct():
    from marketpulse.holdings.service import today_portfolio_change
    rows = [
        {"market_value": 1000.0, "today_change_pct": None},
        {"market_value": 500.0, "today_change_pct": 2.0},
    ]
    result = today_portfolio_change(rows)
    assert result["up_count"] == 1
    assert result["down_count"] == 0
    assert result["dollars"] == pytest.approx(10.0)


def test_today_portfolio_change_empty_returns_zero():
    from marketpulse.holdings.service import today_portfolio_change
    assert today_portfolio_change([]) == {
        "dollars": 0.0, "pct": 0.0, "up_count": 0, "down_count": 0,
    }


def test_today_portfolio_change_all_none_returns_zero():
    from marketpulse.holdings.service import today_portfolio_change
    rows = [{"market_value": 1000.0, "today_change_pct": None}]
    assert today_portfolio_change(rows) == {
        "dollars": 0.0, "pct": 0.0, "up_count": 0, "down_count": 0,
    }


def test_contributors_ranked_top_n_slice():
    from marketpulse.holdings.service import contributors_ranked
    rows = [
        {"ticker": "A", "pl_dollars": +1000.0, "market_value": 5000.0},
        {"ticker": "B", "pl_dollars": -2000.0, "market_value": 3000.0},
        {"ticker": "C", "pl_dollars": +500.0, "market_value": 2000.0},
        {"ticker": "D", "pl_dollars": +100.0, "market_value": 1000.0},
        {"ticker": "E", "pl_dollars": -50.0, "market_value": 500.0},
        {"ticker": "F", "pl_dollars": +10.0, "market_value": 100.0},
    ]
    result = contributors_ranked(rows, top_n=3)
    assert len(result) == 3
    assert [r["ticker"] for r in result] == ["B", "A", "C"]


def test_contributors_ranked_default_top_n_5():
    from marketpulse.holdings.service import contributors_ranked
    rows = [{"ticker": str(i), "pl_dollars": float(i),
             "market_value": 100.0} for i in range(10)]
    result = contributors_ranked(rows)
    assert len(result) == 5


def test_contributors_ranked_fewer_than_top_n():
    from marketpulse.holdings.service import contributors_ranked
    rows = [
        {"ticker": "A", "pl_dollars": +1000.0, "market_value": 5000.0},
        {"ticker": "B", "pl_dollars": -500.0, "market_value": 3000.0},
    ]
    result = contributors_ranked(rows, top_n=10)
    assert len(result) == 2


def test_contributors_ranked_empty():
    from marketpulse.holdings.service import contributors_ranked
    assert contributors_ranked([]) == []
