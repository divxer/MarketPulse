from datetime import UTC, datetime

from marketpulse.db.models import Trade
from marketpulse.holdings.service import (
    allocation_breakdown,
    monthly_realized_pl,
    sort_by_pl_impact,
    trading_stats,
)


def _row(ticker: str, market_value: float | None, pl: float | None) -> dict:
    return {
        "ticker": ticker,
        "market_value": market_value,
        "pl_dollars": pl,
        "cost_basis": 100.0,
        "quantity": 1,
        "avg_cost": 100.0,
    }


def test_allocation_breakdown_sorts_and_calculates_pct() -> None:
    rows = [
        _row("AAA", 100, 0),
        _row("BBB", 300, 0),
        _row("CCC", 100, 0),
    ]
    out = allocation_breakdown(rows)
    assert [r["ticker"] for r in out] == ["BBB", "AAA", "CCC"]
    assert abs(out[0]["pct"] - 60.0) < 1e-9
    assert abs(out[1]["pct"] - 20.0) < 1e-9
    assert all("color" in r for r in out)


def test_allocation_skips_rows_without_market_value() -> None:
    rows = [_row("AAA", None, None), _row("BBB", 100, 0)]
    out = allocation_breakdown(rows)
    assert len(out) == 1
    assert out[0]["ticker"] == "BBB"


def test_allocation_empty_returns_empty() -> None:
    assert allocation_breakdown([]) == []
    assert allocation_breakdown([_row("X", None, None)]) == []


def test_sort_by_pl_impact_orders_by_abs_value() -> None:
    rows = [
        _row("SMALL_GAIN", 110, +10),
        _row("BIG_LOSS", 50, -50),
        _row("HUGE_GAIN", 200, +100),
        _row("NULL_PL", None, None),
    ]
    out = sort_by_pl_impact(rows)
    tickers = [r["ticker"] for r in out]
    assert tickers == ["HUGE_GAIN", "BIG_LOSS", "SMALL_GAIN", "NULL_PL"]


def test_monthly_realized_pl_groups_by_month(db_session) -> None:
    db_session.add_all([
        Trade(ticker="X", action="sell", quantity=10, price=10, realized_pl=50,
              executed_at=datetime(2026, 1, 5, tzinfo=UTC)),
        Trade(ticker="X", action="sell", quantity=10, price=10, realized_pl=-30,
              executed_at=datetime(2026, 1, 20, tzinfo=UTC)),
        Trade(ticker="Y", action="sell", quantity=5, price=5, realized_pl=100,
              executed_at=datetime(2026, 3, 1, tzinfo=UTC)),
    ])
    db_session.commit()
    rows = monthly_realized_pl(db_session)
    assert len(rows) == 2
    assert rows[0]["month"] == "2026-01"
    assert rows[0]["pl"] == 20  # 50 - 30
    assert rows[0]["trade_count"] == 2
    assert rows[1]["month"] == "2026-03"
    assert rows[1]["pl"] == 100


def test_monthly_realized_pl_ignores_buys(db_session) -> None:
    db_session.add(Trade(ticker="X", action="buy", quantity=10, price=10,
                         executed_at=datetime(2026, 1, 5, tzinfo=UTC)))
    db_session.commit()
    assert monthly_realized_pl(db_session) == []


def test_trading_stats(db_session) -> None:
    db_session.add_all([
        Trade(ticker="X", action="buy", quantity=10, price=10,
              executed_at=datetime(2026, 1, 1, tzinfo=UTC)),
        Trade(ticker="X", action="sell", quantity=5, price=15, realized_pl=25,
              executed_at=datetime(2026, 1, 2, tzinfo=UTC)),
        Trade(ticker="X", action="sell", quantity=5, price=5, realized_pl=-25,
              executed_at=datetime(2026, 1, 3, tzinfo=UTC)),
    ])
    db_session.commit()
    stats = trading_stats(db_session)
    assert stats["total_trades"] == 3
    assert stats["closed_positions"] == 2
    assert stats["wins"] == 1
    assert stats["losses"] == 1
    assert stats["win_rate_pct"] == 50.0
    assert stats["realized_pl"] == 0
