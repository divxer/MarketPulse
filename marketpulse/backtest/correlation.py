"""Phase 5c-2: pairwise correlation + neighbor finding.

Spec § 5: Pearson correlation of daily returns on price_cache data, with a
60d causal window matching Phase 5b's rolling_sigma. Self-pair short-circuits
to None. Cold-start (< min_overlap) returns None (fail-safe-open at the
neighbor level).
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Protocol

import numpy as np


class PriceProvider(Protocol):
    """Read-only price interface consumed by correlation calculations.

    Implementations:
      - Production: a yfinance-backed price_cache wrapper
      - Tests: an in-memory dict-backed fake (see test_backtest_correlation.py)
    """

    def get_daily_closes(
        self,
        ticker: str,
        start: date,
        end: date,
    ) -> list[tuple[date, float]]:
        """Return (date, close) tuples for ticker, dates in [start, end). Sorted ascending."""
        ...


def compute_pairwise_correlation(
    ticker_a: str,
    ticker_b: str,
    *,
    as_of: date,
    lookback_days: int = 60,
    min_overlap: int = 30,
    price_provider: PriceProvider,
) -> float | None:
    """Pearson correlation of daily returns over [as_of - lookback_days, as_of).

    Returns None when:
      - ticker_a == ticker_b (self-pair short-circuit)
      - Either ticker missing data in the window
      - Overlapping days < min_overlap
      - Computed corr is NaN (zero variance in either series)

    Contract:
      - Window: [as_of - lookback_days, as_of) — exclusive upper bound
      - Data source: PriceProvider.get_daily_closes (raw OHLC close)
      - Self-pair: returns None (NOT 1.0) — caller never wants a position
        to be its own neighbor.
    """
    if ticker_a == ticker_b:
        return None

    window_start = as_of - timedelta(days=lookback_days)
    a_series = price_provider.get_daily_closes(ticker_a, window_start, as_of)
    b_series = price_provider.get_daily_closes(ticker_b, window_start, as_of)

    a_by_date = {d: v for d, v in a_series}
    b_by_date = {d: v for d, v in b_series}
    overlap_dates = sorted(set(a_by_date) & set(b_by_date))
    if len(overlap_dates) < min_overlap:
        return None

    a_prices = np.array([a_by_date[d] for d in overlap_dates], dtype=float)
    b_prices = np.array([b_by_date[d] for d in overlap_dates], dtype=float)

    if len(a_prices) < 2:
        return None

    a_returns = np.diff(a_prices) / a_prices[:-1]
    b_returns = np.diff(b_prices) / b_prices[:-1]

    if a_returns.std() == 0.0 or b_returns.std() == 0.0:
        return None

    corr = float(np.corrcoef(a_returns, b_returns)[0, 1])
    if not math.isfinite(corr):
        return None
    return corr
