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
    if price <= 0:
        raise TradeError("price must be positive")
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


def total_realized_pl(session: Session, *, ticker: str | None = None) -> float:
    """Sum of realized P&L across all sell trades (optionally filtered by ticker)."""
    q = session.query(Trade).filter(Trade.realized_pl.isnot(None))
    if ticker:
        q = q.filter(Trade.ticker == ticker.upper())
    return sum(t.realized_pl for t in q.all())
