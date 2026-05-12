"""Trade recording: buy/sell events that automatically update the Holdings table.

Buy: increases quantity, recomputes weighted-average cost basis (including fees).
Sell: decreases quantity, records realized P&L = (sell_price - avg_cost) * qty - fees.
      Holding row is deleted when quantity reaches zero.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from marketpulse.db.models import Holding, Trade


class TradeError(ValueError):
    """Raised on invalid trade input (bad action, oversell, non-positive values)."""


# Float comparison tolerance — quantities under this round to zero (holding deleted).
_EPSILON = 1e-9


def record_trade(
    session: Session,
    *,
    ticker: str,
    action: str,
    quantity: float,
    price: float,
    fees: float = 0.0,
    executed_at: datetime | None = None,
    notes: str | None = None,
) -> Trade:
    """Persist a trade and apply its effect to the Holding row for `ticker`.

    Returns the created Trade. Commits within. Raises TradeError on bad input
    (e.g. selling more than held).
    """
    action = action.lower().strip()
    if action not in ("buy", "sell"):
        raise TradeError(f"invalid action {action!r}, must be 'buy' or 'sell'")
    if quantity <= 0:
        raise TradeError("quantity must be positive")
    if price < 0:
        raise TradeError("price cannot be negative")
    # price == 0 is allowed: represents stock splits, share gifts, or
    # employer-granted shares where no cash changed hands.
    if fees < 0:
        raise TradeError("fees cannot be negative")

    ticker = ticker.strip().upper()
    holding = session.query(Holding).filter(Holding.ticker == ticker).one_or_none()

    trade = Trade(
        ticker=ticker,
        action=action,
        quantity=quantity,
        price=price,
        fees=fees,
        executed_at=executed_at,
        notes=notes or None,
    )

    if action == "buy":
        if holding:
            new_qty = holding.quantity + quantity
            # Weighted average; fees fold into cost basis.
            total_cost = (
                holding.quantity * holding.avg_cost + quantity * price + fees
            )
            holding.avg_cost = total_cost / new_qty
            holding.quantity = new_qty
        else:
            # First buy: avg_cost is price plus per-share fees.
            avg = price + (fees / quantity if quantity else 0)
            session.add(
                Holding(ticker=ticker, quantity=quantity, avg_cost=avg, notes=notes)
            )
    else:  # sell
        if not holding or holding.quantity + _EPSILON < quantity:
            held = holding.quantity if holding else 0
            raise TradeError(
                f"cannot sell {quantity} of {ticker}; only {held} held",
            )
        trade.realized_pl = (price - holding.avg_cost) * quantity - fees
        holding.quantity -= quantity
        if holding.quantity <= _EPSILON:
            session.delete(holding)
        # avg_cost stays the same on partial sell (remaining shares retain their cost basis)

    session.add(trade)
    session.commit()
    session.refresh(trade)
    return trade


def recompute_ticker(session: Session, ticker: str) -> None:
    """Rebuild Holding row + realized_pl values from the full Trade + StockSplit
    history for ticker.

    Walks both timelines merged in chronological order. Splits are anchored to
    end-of-day so any same-day trade sorts BEFORE the split takes effect, which
    matches real-world execution (the split is applied at market open of the
    next session, but ex_date is a date, not a datetime).

    Trade rows are never mutated — only `realized_pl` on sells is recomputed.
    """
    from datetime import UTC, datetime, time

    from marketpulse.db.models import StockSplit

    ticker = ticker.strip().upper()
    trades = (
        session.query(Trade)
        .filter(Trade.ticker == ticker)
        .order_by(Trade.executed_at.asc().nulls_last(), Trade.created_at.asc())
        .all()
    )
    splits = (
        session.query(StockSplit)
        .filter(StockSplit.ticker == ticker)
        .order_by(StockSplit.ex_date.asc())
        .all()
    )

    # Normalize event times to datetime so heterogeneous tuple comparison
    # never raises. Splits anchor at end-of-day (kind=1) so same-day trades
    # (kind=0) sort first.
    _EOD = time(23, 59, 59, tzinfo=UTC)

    def _trade_when(t: Trade) -> datetime:
        if t.executed_at:
            return t.executed_at
        return t.created_at

    events: list[tuple[datetime, int, str, object]] = []
    for t in trades:
        events.append((_trade_when(t), 0, "trade", t))
    for s in splits:
        events.append((datetime.combine(s.ex_date, _EOD), 1, "split", s))
    events.sort(key=lambda x: (x[0], x[1]))

    qty = 0.0
    avg_cost = 0.0
    for _when, _order, kind, evt in events:
        if kind == "trade":
            t = evt  # type: ignore[assignment]
            if t.action == "buy":
                new_qty = qty + t.quantity
                total_cost = qty * avg_cost + t.quantity * t.price + t.fees
                avg_cost = total_cost / new_qty if new_qty else 0
                qty = new_qty
                t.realized_pl = None
            else:  # sell
                t.realized_pl = (t.price - avg_cost) * t.quantity - t.fees
                qty -= t.quantity
                # avg_cost unchanged on partial sell
        else:  # split
            s = evt  # type: ignore[assignment]
            qty = qty * s.ratio
            # Inverse adjustment keeps total_cost invariant.
            if s.ratio:
                avg_cost = avg_cost / s.ratio

    holding = session.query(Holding).filter(Holding.ticker == ticker).one_or_none()
    if qty <= _EPSILON:
        if holding:
            session.delete(holding)
    elif holding:
        holding.quantity = qty
        holding.avg_cost = avg_cost
    else:
        session.add(Holding(ticker=ticker, quantity=qty, avg_cost=avg_cost))

    session.commit()


def total_realized_pl(session: Session, *, ticker: str | None = None) -> float:
    """Sum of realized P&L across all sell trades (optionally filtered by ticker)."""
    q = session.query(Trade).filter(Trade.realized_pl.isnot(None))
    if ticker:
        q = q.filter(Trade.ticker == ticker.upper())
    return sum(t.realized_pl for t in q.all())
