"""Historical-snapshot helper for holdings.

`quantity_as_of(session, ticker, as_of)` walks the merged Trade + StockSplit
timeline up to end-of-`as_of` and returns the share count at that point. Used
by the dividend auto-detection job to compute `total_amount` correctly
without needing to call recompute_ticker (which mutates state) just to read
a historical qty.

Shares the `_walk_events` helper with `marketpulse.holdings.trades` so the
chronological ordering and split-anchor logic stays in one place.
"""
from datetime import date

from sqlalchemy.orm import Session

from marketpulse.holdings.trades import _walk_events


def quantity_as_of(session: Session, ticker: str, as_of: date) -> float:
    """Return share quantity held at end-of-day `as_of`, derived from all
    Trade and StockSplit events for `ticker` whose chronological time is
    <= end-of-`as_of`.

    Returns 0.0 if the ticker was never held or fully sold before as_of.
    Read-only — no DB writes, no recompute side-effect.
    """
    qty, _avg_cost, _processed = _walk_events(session, ticker, until=as_of)
    return qty
