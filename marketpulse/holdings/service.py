"""Shared helpers for valuing positions.

Used by both the /holdings web route (to render the page) and the recap
service (to include holdings P&L in the daily AI commentary).
"""

from collections import defaultdict
from datetime import date
from typing import Any, Protocol

from sqlalchemy.orm import Session

from marketpulse.data.types import Quote
from marketpulse.db.models import Holding, Trade
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


def allocation_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-position share of total market value, sorted descending.

    Each entry: {ticker, market_value, pct, color}.
    Tickers with no market value (quote failed) are skipped.
    """
    valued = [r for r in rows if r.get("market_value") is not None]
    total = sum(r["market_value"] for r in valued)
    if total <= 0:
        return []
    # Stable color cycle — same ticker gets the same hue across page loads.
    palette = [
        "#475569",  # slate-600
        "#16a34a",  # green-600
        "#a855f7",  # purple-500
        "#3b82f6",  # blue-500
        "#f59e0b",  # amber-500
        "#ec4899",  # pink-500
        "#0ea5e9",  # sky-500
        "#84cc16",  # lime-500
    ]
    sorted_rows = sorted(valued, key=lambda r: r["market_value"], reverse=True)
    return [
        {
            "ticker": r["ticker"],
            "market_value": r["market_value"],
            "pct": r["market_value"] / total * 100,
            "color": palette[i % len(palette)],
        }
        for i, r in enumerate(sorted_rows)
    ]


def sort_by_pl_impact(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort enriched rows by absolute unrealized P&L (biggest mover first).

    Rows with no P&L (quote failed) sink to the bottom.
    """
    def key(r: dict[str, Any]) -> tuple[int, float]:
        pl = r.get("pl_dollars")
        if pl is None:
            return (1, 0.0)  # nulls last
        return (0, -abs(pl))
    return sorted(rows, key=key)


def monthly_realized_pl(
    session: Session,
    *,
    months: int | None = None,
) -> list[dict[str, Any]]:
    """Aggregate realized P&L from sell trades grouped by (year, month).

    months=None (default): return all months that have realized P&L,
        chronologically; gaps omitted. Preserves existing /holdings behavior.
    months=N: return the trailing N calendar months (including current);
        missing months padded with {pl: 0, trade_count: 0}.
    """
    sells = session.query(Trade).filter(Trade.realized_pl.isnot(None)).all()
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"pl": 0.0, "trade_count": 0},
    )
    for t in sells:
        when: date | None = (
            t.executed_at.date() if t.executed_at
            else (t.created_at.date() if t.created_at else None)
        )
        if when is None:
            continue
        key = f"{when.year:04d}-{when.month:02d}"
        buckets[key]["pl"] += t.realized_pl
        buckets[key]["trade_count"] += 1

    if months is None:
        return [
            {"month": m, "pl": v["pl"], "trade_count": v["trade_count"]}
            for m, v in sorted(buckets.items())
        ]

    # months=N: pad trailing N months including current.
    today = date.today()
    keys: list[str] = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    keys.reverse()
    return [
        {"month": k, "pl": buckets[k]["pl"], "trade_count": buckets[k]["trade_count"]}
        for k in keys
    ]


def trading_stats(
    session: Session,
    *,
    ticker: str | None = None,
    from_date: "date | None" = None,
    to_date: "date | None" = None,
) -> dict[str, Any]:
    """High-level stats across trades: count, win rate, total realized P&L.

    `ticker` filters to a single symbol (case-insensitive).
    `from_date`/`to_date` is an inclusive window on the SELL row's
    executed_at.date() (fallback to created_at).

    Returns:
      total_trades: BUY+SELL count within filter (NOT just sells)
      closed_positions: wins+losses
      wins / losses: per realized_pl sign
      win_rate_pct: float OR None when wins+losses == 0
      realized_pl: sum of realized_pl in window
    """
    q = session.query(Trade)
    if ticker:
        q = q.filter(Trade.ticker == ticker.upper())
    trades = q.all()

    # Single-pass filter (compute _row_date once per row)
    if from_date is not None or to_date is not None:
        def _row_date(t: Trade):
            d = t.executed_at or t.created_at
            return d.date() if d is not None else None

        def _keep(t: Trade) -> bool:
            d = _row_date(t)
            if d is None:
                return False
            if from_date is not None and d < from_date:
                return False
            return not (to_date is not None and d > to_date)

        trades = [t for t in trades if _keep(t)]

    total = len(trades)
    sells = [t for t in trades if t.realized_pl is not None]
    wins = sum(1 for t in sells if t.realized_pl > 0)
    losses = sum(1 for t in sells if t.realized_pl < 0)
    closed = wins + losses
    realized = sum(t.realized_pl for t in sells)
    return {
        "total_trades": total,
        "closed_positions": closed,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": (wins / closed * 100) if closed else None,
        "realized_pl": realized,
    }
