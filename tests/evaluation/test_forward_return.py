"""Tests for forward_return_at_horizon.

Uses mocked DataService rather than hitting yfinance — we test the math,
not the data source.
"""
from dataclasses import dataclass
from datetime import date
from unittest.mock import MagicMock

from marketpulse.evaluation.forward_return import (
    forward_return_at_horizon,
)


@dataclass
class FakeBar:
    date: date
    open: float = 0
    high: float = 0
    low: float = 0
    close: float = 0
    volume: int = 0


def _mock_data(bars: list[FakeBar]) -> MagicMock:
    """Helper: build a DataService mock whose get_history returns these bars."""
    m = MagicMock()
    m.get_history.return_value = bars
    return m


def test_known_date_pair_returns_correct_value():
    bars = [
        FakeBar(date=date(2026, 1, 1), close=100.0),
        FakeBar(date=date(2026, 1, 2), close=101.0),
        FakeBar(date=date(2026, 1, 3), close=102.0),
        FakeBar(date=date(2026, 1, 6), close=105.0),  # +3.96% from idx=1
    ]
    result = forward_return_at_horizon(
        "TST", date(2026, 1, 2), horizon_trading_days=2,
        data=_mock_data(bars),
    )
    assert result is not None
    assert result.event_price == 101.0
    assert result.horizon_price == 105.0
    assert result.horizon_date == date(2026, 1, 6)
    assert abs(result.forward_return - (105.0 - 101.0) / 101.0) < 1e-9


def test_event_on_weekend_skips_to_next_trading_day():
    # 2026-01-03 is a Saturday in this synthetic series; first bar at/after is
    # 2026-01-05 (Monday).
    bars = [
        FakeBar(date=date(2026, 1, 2), close=100.0),  # Fri
        FakeBar(date=date(2026, 1, 5), close=101.0),  # Mon
        FakeBar(date=date(2026, 1, 6), close=102.0),
    ]
    result = forward_return_at_horizon(
        "TST", date(2026, 1, 3), horizon_trading_days=1,
        data=_mock_data(bars),
    )
    assert result is not None
    assert result.event_price == 101.0  # used Monday's close
    assert result.horizon_price == 102.0


def test_horizon_in_future_returns_none():
    bars = [
        FakeBar(date=date(2026, 1, 1), close=100.0),
        FakeBar(date=date(2026, 1, 2), close=101.0),
    ]
    result = forward_return_at_horizon(
        "TST", date(2026, 1, 1), horizon_trading_days=5,
        data=_mock_data(bars),
    )
    assert result is None


def test_event_in_future_returns_none():
    bars = [FakeBar(date=date(2026, 1, 1), close=100.0)]
    future = date.today().replace(year=date.today().year + 1)
    result = forward_return_at_horizon(
        "TST", future, horizon_trading_days=1, data=_mock_data(bars),
    )
    assert result is None


def test_no_bars_returns_none():
    result = forward_return_at_horizon(
        "TST", date(2026, 1, 1), horizon_trading_days=1, data=_mock_data([]),
    )
    assert result is None


def test_fetch_exception_returns_none():
    m = MagicMock()
    m.get_history.side_effect = RuntimeError("yfinance quota")
    result = forward_return_at_horizon(
        "TST", date(2026, 1, 1), horizon_trading_days=1, data=m,
    )
    assert result is None


def test_zero_event_price_returns_none():
    bars = [
        FakeBar(date=date(2026, 1, 1), close=0.0),
        FakeBar(date=date(2026, 1, 2), close=10.0),
    ]
    result = forward_return_at_horizon(
        "TST", date(2026, 1, 1), horizon_trading_days=1, data=_mock_data(bars),
    )
    assert result is None  # would divide by zero


def test_cross_year_boundary_handled():
    """Event late December, horizon spanning new year holidays."""
    bars = [
        FakeBar(date=date(2025, 12, 29), close=100.0),
        FakeBar(date=date(2025, 12, 30), close=101.0),
        FakeBar(date=date(2025, 12, 31), close=102.0),
        # Jan 1 holiday
        FakeBar(date=date(2026, 1, 2), close=105.0),
        FakeBar(date=date(2026, 1, 5), close=107.0),
    ]
    result = forward_return_at_horizon(
        "TST", date(2025, 12, 30), horizon_trading_days=3,
        data=_mock_data(bars),
    )
    assert result is not None
    assert result.event_price == 101.0
    assert result.horizon_price == 107.0
    assert result.horizon_date == date(2026, 1, 5)


def test_horizon_zero_returns_event_bar_self():
    """Edge case: horizon=0 should mean "same day" — forward_return=0."""
    bars = [
        FakeBar(date=date(2026, 1, 1), close=100.0),
        FakeBar(date=date(2026, 1, 2), close=101.0),
    ]
    result = forward_return_at_horizon(
        "TST", date(2026, 1, 1), horizon_trading_days=0, data=_mock_data(bars),
    )
    assert result is not None
    assert result.forward_return == 0.0
    assert result.event_price == result.horizon_price == 100.0
