"""SPY benchmark forward-return helper with per-run caching.

Fetched once per benchmark_forward_return() process run via lru_cache,
then reused across all events on the same horizon. Cache invalidates
when the process restarts (i.e., next nightly cron).
"""
from __future__ import annotations

from datetime import date

from marketpulse.data.service import DataService
from marketpulse.evaluation.forward_return import forward_return_at_horizon

BENCHMARK_TICKER = "SPY"


def benchmark_forward_return(
    event_date: date,
    horizon_trading_days: int,
    data: DataService,
) -> float | None:
    """SPY forward return over the same horizon — used to compute excess return.

    Returns None if SPY data unavailable for that horizon.

    Note: NOT cached across processes; one cron run = one SPY history fetch.
    Phase 2/3 may want a class-level cache when called repeatedly.
    """
    result = forward_return_at_horizon(
        BENCHMARK_TICKER, event_date, horizon_trading_days, data,
    )
    return result.forward_return if result else None
