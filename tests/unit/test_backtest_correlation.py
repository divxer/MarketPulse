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


def test_find_correlation_neighbors_returns_only_above_threshold() -> None:
    """Only tickers with corr >= threshold appear in neighbors."""
    from marketpulse.backtest.correlation import find_correlation_neighbors

    base = _linear(100.0, 60, 1.0, date(2026, 1, 1))
    weak = [(d, base[i][1] + 5.0 * (i % 7)) for i, (d, _) in enumerate(base)]
    flat = [(d, 100.0 + 0.05 * (i % 3)) for i, (d, _) in enumerate(base)]
    provider = _FakePriceProvider({
        "CAND": base,
        "STRONG": base,  # ρ ≈ 1.0
        "WEAK": weak,    # ρ moderate
        "FLAT": flat,    # near zero correlation
    })

    neighbors, _diag = find_correlation_neighbors(
        "CAND",
        ["STRONG", "WEAK", "FLAT"],
        as_of=date(2026, 3, 5),
        threshold=0.6,
        lookback_days=60,
        price_provider=provider,
    )
    assert "STRONG" in neighbors
    assert "FLAT" not in neighbors


def test_find_correlation_neighbors_filters_self_from_input() -> None:
    """Candidate ticker in open_positions list is filtered before pairing."""
    from marketpulse.backtest.correlation import find_correlation_neighbors

    series = _linear(100.0, 60, 1.0, date(2026, 1, 1))
    provider = _FakePriceProvider({"AAPL": series, "GOOGL": series})

    neighbors, _diag = find_correlation_neighbors(
        "AAPL",
        ["AAPL", "GOOGL"],  # candidate appears in input
        as_of=date(2026, 3, 5),
        threshold=0.6,
        lookback_days=60,
        price_provider=provider,
    )
    # AAPL must NOT appear in neighbors (self-filtered)
    assert "AAPL" not in neighbors
    # GOOGL identical series → corr ~1.0 → IS a neighbor
    assert "GOOGL" in neighbors


def test_find_correlation_neighbors_diagnostics_sorted_desc() -> None:
    """Diagnostics tuple is sorted by corr value descending (highest first)."""
    from marketpulse.backtest.correlation import find_correlation_neighbors

    # Build series with known correlations relative to CAND
    base = _linear(100.0, 60, 1.0, date(2026, 1, 1))
    medium = [(d, v + 2.0 * (i % 9)) for i, (d, v) in enumerate(base)]
    low = [(d, v + 8.0 * (i % 5)) for i, (d, v) in enumerate(base)]
    provider = _FakePriceProvider({
        "CAND": base,
        "HIGH": base,
        "MED": medium,
        "LOW": low,
    })

    _neighbors, diagnostics = find_correlation_neighbors(
        "CAND",
        ["HIGH", "MED", "LOW"],
        as_of=date(2026, 3, 5),
        threshold=0.0,  # capture everything for sort assertion
        lookback_days=60,
        min_overlap=30,
        price_provider=provider,
    )
    # diagnostics is tuple of (ticker, corr) sorted by corr desc
    corrs = [c for _t, c in diagnostics]
    assert corrs == sorted(corrs, reverse=True)
    # HIGH should be first (corr ~1.0)
    assert diagnostics[0][0] == "HIGH"


def test_find_correlation_neighbors_cold_start_returns_empty() -> None:
    """When all corrs are None (insufficient overlap), returns empty list + empty tuple."""
    from marketpulse.backtest.correlation import find_correlation_neighbors

    # Only 10 days of data; min_overlap=30 forces None
    short_a = _linear(100.0, 10, 1.0, date(2026, 2, 25))
    short_b = _linear(100.0, 10, 1.0, date(2026, 2, 25))
    provider = _FakePriceProvider({"A": short_a, "B": short_b})

    neighbors, diagnostics = find_correlation_neighbors(
        "A",
        ["B"],
        as_of=date(2026, 3, 7),
        threshold=0.6,
        lookback_days=60,
        min_overlap=30,
        price_provider=provider,
    )
    assert neighbors == []
    assert diagnostics == ()
