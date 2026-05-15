"""FIFO lot matching: pair buy and sell trades chronologically per ticker.

Used by aggregation layer to compute:
- avg_hold_days (time between matched buy and sell)
- realized_pl_by_ticker (sum of matched lot PL per ticker)
- per-lot cost basis (for pct calculation)

Read-only: never writes back to Trade.realized_pl. Existing Trade.realized_pl
column is filled by trades_service on sell-row creation; this matcher is an
independent view on the same data.

Note: LotMatch.realized_pl is GROSS — buy/sell spread only, no fees.
Trade.realized_pl (filled by trades_service) is NET (includes fees).
The two values diverge for any portfolio with non-zero fees. Consumers
that need fee-accurate per-ticker totals should sum Trade.realized_pl
directly, not fold over LotMatch.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from marketpulse.db.models import Trade


@dataclass(frozen=True)
class LotMatch:
    ticker: str
    buy_executed_at: datetime
    sell_executed_at: datetime
    quantity: float
    hold_days: int
    realized_pl: float  # gross PL only — does NOT deduct Trade.fees (use Trade.realized_pl for net)
    buy_price: float  # for cost basis aggregation


def match_lots_fifo(session: Session) -> list[LotMatch]:
    """Walk all trades in chronological order, per ticker; pair sells with
    open buy lots in FIFO order. Returns list of LotMatch sorted by
    sell_executed_at (i.e., when the match was realized).

    Trades with executed_at=None fall back to created_at for ordering.
    Sells with executed_at=None (using created_at fallback) appear after all
    sells with explicit executed_at.
    Sells exceeding total open quantity have the overflow silently dropped
    (matches Trade.realized_pl behavior in trades_service).
    """
    # Tiebreak by id (insertion order, monotone) for trades sharing the same
    # executed_at — more reliable than created_at for sub-second commits.
    trades = (
        session.query(Trade)
        .order_by(Trade.executed_at.asc().nulls_last(), Trade.id.asc())
        .all()
    )

    open_lots: dict[str, deque[dict]] = defaultdict(deque)
    matches: list[LotMatch] = []

    for t in trades:
        when = t.executed_at or t.created_at
        if when is None:
            continue
        if t.action == "buy":
            open_lots[t.ticker].append({
                "qty": t.quantity,
                "price": t.price,
                "when": when,
            })
        elif t.action == "sell":
            remaining = t.quantity
            lots = open_lots[t.ticker]
            while remaining > 0 and lots:
                head = lots[0]
                take = min(remaining, head["qty"])
                pl = (t.price - head["price"]) * take
                hold = (when - head["when"]).days
                matches.append(LotMatch(
                    ticker=t.ticker,
                    buy_executed_at=head["when"],
                    sell_executed_at=when,
                    quantity=take,
                    hold_days=hold,
                    realized_pl=pl,
                    buy_price=head["price"],
                ))
                head["qty"] -= take
                remaining -= take
                # Float-imprecision tolerance: a fractional multi-lot sell can leave
                # qty at ~2.78e-17 instead of exactly 0. Without this, the residual
                # "ghost lot" stays in the queue and corrupts the next sell match.
                if math.isclose(head["qty"], 0.0, abs_tol=1e-9):
                    lots.popleft()
            # Overflow (remaining > 0) is silently dropped.

    return matches
