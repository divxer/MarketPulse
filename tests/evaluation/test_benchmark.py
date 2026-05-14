from dataclasses import dataclass
from datetime import date
from unittest.mock import MagicMock

from marketpulse.evaluation.benchmark import (
    BENCHMARK_TICKER,
    benchmark_forward_return,
)


@dataclass
class FakeBar:
    date: date
    close: float = 0


def _mock_data(bars: list[FakeBar]) -> MagicMock:
    m = MagicMock()
    m.get_history.return_value = bars
    return m


def test_default_benchmark_is_spy():
    assert BENCHMARK_TICKER == "SPY"


def test_benchmark_forward_return_computes_spy_return():
    bars = [
        FakeBar(date=date(2026, 1, 1), close=400.0),
        FakeBar(date=date(2026, 1, 2), close=402.0),
        FakeBar(date=date(2026, 1, 3), close=404.0),
    ]
    m = _mock_data(bars)
    r = benchmark_forward_return(
        date(2026, 1, 1), horizon_trading_days=2, data=m,
    )
    assert r is not None
    assert abs(r - (404.0 - 400.0) / 400.0) < 1e-9
    # Verify it queried SPY specifically
    m.get_history.assert_called_with("SPY", period="1y")


def test_benchmark_returns_none_when_data_unavailable():
    m = _mock_data([])
    r = benchmark_forward_return(
        date(2026, 1, 1), horizon_trading_days=2, data=m,
    )
    assert r is None
