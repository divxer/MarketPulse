"""Forward-return computation at a given horizon for a ticker."""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date

from marketpulse.data.service import DataService
from marketpulse.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ForwardReturnResult:
    """Result of a forward-return computation."""
    event_price: float
    horizon_price: float
    horizon_date: date
    forward_return: float  # (horizon_price - event_price) / event_price


def forward_return_at_horizon(
    ticker: str,
    event_date: date,
    horizon_trading_days: int,
    data: DataService,
) -> ForwardReturnResult | None:
    """Compute forward return from event_date to event_date + N trading days.

    Returns None if:
      - Bars not available (network / quota / delisted ticker)
      - event_date is in the future
      - horizon end is still in the future (not enough bars yet)
      - Insufficient bars between event_date and horizon

    Uses the ticker's actual trading-day index, so weekends and holidays
    don't shift the horizon — N "trading days later" means N bars after
    event_date.
    """
    if event_date > date.today():
        return None

    try:
        # Fetch enough history to span any reasonable horizon (60 trading days
        # ~ 90 calendar days; we use 1y for a comfortable margin).
        bars = data.get_history(ticker, period="1y")
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "forward_return_fetch_failed",
            ticker=ticker, event_date=str(event_date), error=str(exc),
        )
        return None

    if not bars:
        return None

    # Find the bar at event_date or the first trading day after.
    bar_dates = [b.date for b in bars]
    idx = bisect.bisect_left(bar_dates, event_date)
    if idx >= len(bars):
        return None
    event_bar = bars[idx]

    horizon_idx = idx + horizon_trading_days
    if horizon_idx >= len(bars):
        return None  # horizon still in the future
    horizon_bar = bars[horizon_idx]

    event_price = event_bar.close
    horizon_price = horizon_bar.close
    if event_price == 0:
        return None  # defensive — division would explode

    return ForwardReturnResult(
        event_price=event_price,
        horizon_price=horizon_price,
        horizon_date=horizon_bar.date,
        forward_return=(horizon_price - event_price) / event_price,
    )
