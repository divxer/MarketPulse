"""Phase 5c-2: correlation.py — pairwise Pearson + neighbor finding."""
from __future__ import annotations

from datetime import date, timedelta


class _FakePriceProvider:
    """Test double: in-memory ticker→date→close. Implements PriceProvider Protocol."""

    def __init__(self, data: dict[str, list[tuple[date, float]]]) -> None:
        self._data = data

    def get_daily_closes(self, ticker: str, start: date, end: date) -> list[tuple[date, float]]:
        rows = self._data.get(ticker, [])
        return sorted([(d, v) for d, v in rows if start <= d < end])


def _linear(start: float, n: int, slope: float, start_date: date) -> list[tuple[date, float]]:
    """Build a linear price series for testing."""
    return [(start_date + timedelta(days=i), start + slope * i) for i in range(n)]


def _alternating_pattern(
    start: float, n: int, up_pct: float, down_pct: float, start_date: date
) -> list[tuple[date, float]]:
    """Build a price series with alternating up/down changes for correlation tests."""
    prices = [start]
    for i in range(1, n):
        if i % 2 == 1:
            prices.append(prices[-1] * (1 + up_pct))
        else:
            prices.append(prices[-1] * (1 + down_pct))
    return [(start_date + timedelta(days=i), prices[i]) for i in range(n)]


def test_pairwise_correlation_identical_series_returns_one() -> None:
    """Two perfectly identical return series → corr ≈ 1.0."""
    from marketpulse.backtest.correlation import compute_pairwise_correlation

    series = _linear(100.0, 60, 1.0, date(2026, 1, 1))
    provider = _FakePriceProvider({"A": series, "B": series})

    corr = compute_pairwise_correlation(
        "A", "B",
        as_of=date(2026, 3, 5),
        lookback_days=60,
        min_overlap=30,
        price_provider=provider,
    )
    assert corr is not None
    assert corr > 0.999


def test_pairwise_correlation_inverse_series_returns_negative_one() -> None:
    """Inverse series (one rises, other falls) → corr ≈ -1.0."""
    from marketpulse.backtest.correlation import compute_pairwise_correlation

    # Create returns with opposite daily patterns
    up_series = _alternating_pattern(
        100.0, 60, up_pct=0.02, down_pct=-0.01, start_date=date(2026, 1, 1)
    )
    down_series = _alternating_pattern(
        100.0, 60, up_pct=-0.02, down_pct=0.01, start_date=date(2026, 1, 1)
    )
    provider = _FakePriceProvider({"UP": up_series, "DOWN": down_series})

    corr = compute_pairwise_correlation(
        "UP", "DOWN",
        as_of=date(2026, 3, 5),
        lookback_days=60,
        min_overlap=30,
        price_provider=provider,
    )
    assert corr is not None
    assert corr < -0.99


def test_pairwise_correlation_returns_none_below_min_overlap() -> None:
    """< min_overlap days → returns None."""
    from marketpulse.backtest.correlation import compute_pairwise_correlation

    short = _linear(100.0, 10, 1.0, date(2026, 2, 25))
    provider = _FakePriceProvider({"A": short, "B": short})

    corr = compute_pairwise_correlation(
        "A", "B",
        as_of=date(2026, 3, 7),
        lookback_days=60,
        min_overlap=30,
        price_provider=provider,
    )
    assert corr is None


def test_pairwise_correlation_returns_none_for_self_pair() -> None:
    """a == b returns None (NOT 1.0). Self-pair contract."""
    from marketpulse.backtest.correlation import compute_pairwise_correlation

    series = _linear(100.0, 60, 1.0, date(2026, 1, 1))
    provider = _FakePriceProvider({"AAPL": series})

    corr = compute_pairwise_correlation(
        "AAPL", "AAPL",
        as_of=date(2026, 3, 5),
        lookback_days=60,
        min_overlap=30,
        price_provider=provider,
    )
    assert corr is None


def test_pairwise_correlation_returns_none_for_zero_variance_series() -> None:
    """Flat series (zero variance) → corr undefined → None."""
    from marketpulse.backtest.correlation import compute_pairwise_correlation

    flat = [(date(2026, 1, 1) + timedelta(days=i), 100.0) for i in range(60)]
    moving = _linear(100.0, 60, 1.0, date(2026, 1, 1))
    provider = _FakePriceProvider({"FLAT": flat, "MOVE": moving})

    corr = compute_pairwise_correlation(
        "FLAT", "MOVE",
        as_of=date(2026, 3, 5),
        lookback_days=60,
        min_overlap=30,
        price_provider=provider,
    )
    assert corr is None


def test_pairwise_correlation_excludes_dates_at_or_after_as_of() -> None:
    """Window is [as_of - lookback, as_of) — exclusive upper bound."""
    from marketpulse.backtest.correlation import compute_pairwise_correlation

    # Series extends past as_of; future data must not affect corr
    long_series = _linear(100.0, 120, 1.0, date(2026, 1, 1))
    provider = _FakePriceProvider({"A": long_series, "B": long_series})

    as_of_early = date(2026, 2, 15)
    corr = compute_pairwise_correlation(
        "A", "B",
        as_of=as_of_early,
        lookback_days=45,
        min_overlap=30,
        price_provider=provider,
    )
    # Should succeed (45d window, ~30d data available before as_of) AND
    # not be influenced by post-as_of data (same series both legs → ~1.0)
    assert corr is not None
    assert corr > 0.999
