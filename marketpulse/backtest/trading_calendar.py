"""Trading-day calendar derived from DB outcomes.

Phase 1's outcomes.py computes horizon_date via yfinance bar-index
alignment, which already respects weekends + US holidays. We don't
need a separate calendar library — instead, build the trading-day
grid from the union of all event_time.date() + horizon_date values
already in the DB.

Limitation: gaps appear if a date has no events (rare for our use).
If illiquid tickers cause gaps that matter, Phase 4.5 can swap in
pandas_market_calendars.
"""
from __future__ import annotations

import bisect
from datetime import date


def build_calendar(raw_dates: list[date]) -> list[date]:
    """Deduplicate + sort a list of dates into the trading-day grid.

    Args:
        raw_dates: union of event_time.date() and horizon_date values
            pulled from EvaluationOutcome rows.

    Returns:
        Sorted ascending, no duplicates.
    """
    return sorted(set(raw_dates))


def trading_days_between(
    calendar: list[date], start: date, end: date,
) -> int:
    """Inclusive count of calendar dates in [start, end].

    Out-of-range dates are clipped. start > end returns 0.
    """
    if start > end:
        return 0
    left = bisect.bisect_left(calendar, start)
    right = bisect.bisect_right(calendar, end)
    return right - left


def elapsed_fraction(
    calendar: list[date], *, entry: date, horizon: date, current: date,
) -> float:
    """Linear-interp fraction of holding period elapsed.

    Returns:
        0.0 when current == entry
        1.0 when current == horizon
        Linearly interpolated otherwise.
        Clipped to [0, 1].
    """
    total = trading_days_between(calendar, entry, horizon)
    if total <= 1:
        return 0.0 if current < horizon else 1.0
    elapsed = trading_days_between(calendar, entry, current) - 1
    elapsed = max(0, min(elapsed, total - 1))
    return elapsed / (total - 1)
