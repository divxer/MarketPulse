"""Shared helpers for valuing positions.

Used by both the /holdings web route (to render the page) and the recap
service (to include holdings P&L in the daily AI commentary).
"""

from typing import Any, Protocol

from marketpulse.data.types import Quote
from marketpulse.db.models import Holding
from marketpulse.logging import get_logger

log = get_logger(__name__)


class _DataLike(Protocol):
    def get_quote(self, ticker: str) -> Quote: ...


def enrich_holdings(
    holdings: list[Holding], data: _DataLike
) -> list[dict[str, Any]]:
    """Attach live quote + computed P&L to each holding. Live fetch failures
    are tolerated — the row still renders with cost-basis info."""
    rows: list[dict[str, Any]] = []
    for h in holdings:
        cost_basis = h.quantity * h.avg_cost
        row: dict[str, Any] = {
            "id": h.id,
            "ticker": h.ticker,
            "quantity": h.quantity,
            "avg_cost": h.avg_cost,
            "notes": h.notes,
            "cost_basis": cost_basis,
            "current_price": None,
            "market_value": None,
            "pl_dollars": None,
            "pl_pct": None,
            "stale": False,
        }
        try:
            q = data.get_quote(h.ticker)
            row["current_price"] = q.price
            row["market_value"] = h.quantity * q.price
            row["pl_dollars"] = row["market_value"] - cost_basis
            row["pl_pct"] = (q.price - h.avg_cost) / h.avg_cost * 100 if h.avg_cost else 0
            row["stale"] = q.stale
        except Exception as exc:
            log.warning("holding_quote_failed", ticker=h.ticker, error=str(exc))
        rows.append(row)
    return rows


def compute_totals(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Aggregate cost basis, market value, and unrealized P&L across all rows."""
    cost = sum(r["cost_basis"] for r in rows)
    mv = sum(r["market_value"] for r in rows if r["market_value"] is not None)
    pl = mv - cost if cost > 0 else 0
    pl_pct = pl / cost * 100 if cost > 0 else 0
    return {"cost": cost, "market_value": mv, "pl_dollars": pl, "pl_pct": pl_pct}
