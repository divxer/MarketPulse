"""Shared helpers for valuing positions.

Used by both the /holdings web route (to render the page) and the recap
service (to include holdings P&L in the daily AI commentary).
"""

from collections import defaultdict
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from sqlalchemy.orm import Session

from marketpulse.data.types import Quote
from marketpulse.db.models import Dividend, Holding, Trade
from marketpulse.logging import get_logger

log = get_logger(__name__)


class _DataLike(Protocol):
    def get_quote(self, ticker: str) -> Quote: ...
    def get_history(self, ticker: str, period: str = "30d") -> list[Any]: ...


@dataclass(frozen=True)
class _HoldingSnapshot:
    id: int | None
    ticker: str
    quantity: float
    avg_cost: float
    notes: str | None
    sector: str | None


def _snapshot_holding(h: Holding) -> _HoldingSnapshot:
    return _HoldingSnapshot(
        id=h.id,
        ticker=h.ticker,
        quantity=h.quantity,
        avg_cost=h.avg_cost,
        notes=h.notes,
        sector=h.sector,
    )


def _fetch_sparkline(data: "_DataLike", ticker: str) -> list[float]:
    """Return last 30 daily closes; [] on fetch failure.

    Used by /holdings table 30-day sparkline column. Failures are
    silenced so a single bad ticker doesn't break the entire table.
    """
    try:
        bars = data.get_history(ticker, period="30d")
        return [b.close for b in bars[-30:]]
    except Exception:
        return []


def _enrich_one(h: _HoldingSnapshot, data: _DataLike) -> dict[str, Any]:
    """Build one enriched row. Tolerates quote/history fetch failures —
    the row still renders with cost-basis info. Extracted so the outer
    loop can fan out across a ThreadPoolExecutor.
    """
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
    quote = None
    try:
        q = data.get_quote(h.ticker)
        quote = q
        row["current_price"] = q.price
        row["market_value"] = h.quantity * q.price
        row["pl_dollars"] = row["market_value"] - cost_basis
        row["pl_pct"] = (q.price - h.avg_cost) / h.avg_cost * 100 if h.avg_cost else 0
        row["stale"] = q.stale
    except Exception as exc:
        log.warning("holding_quote_failed", ticker=h.ticker, error=str(exc))
    row["sector"] = h.sector or "未分类"
    row["today_change_pct"] = quote.change_pct if quote is not None else None
    row["sparkline"] = _fetch_sparkline(data, h.ticker)
    return row


# Tunable for tests that want to assert sequential ordering / disable
# parallelism. Production paths leave this alone.
ENRICH_MAX_WORKERS = 8


def enrich_holdings(
    holdings: list[Holding],
    data: _DataLike,
    *,
    data_factory: Callable[[], AbstractContextManager[_DataLike]] | None = None,
) -> list[dict[str, Any]]:
    """Attach live quote + computed P&L to each holding. Live fetch failures
    are tolerated — the row still renders with cost-basis info.

    Fans out per-ticker network I/O (get_quote + get_history) across a
    ThreadPoolExecutor: previously 2 sequential HTTPS calls × N holdings
    = O(N) wall time; now O(N / workers). For a 5-ticker portfolio with
    ~300ms per call this drops the route from ~3s to ~600ms on cold cache.
    Output order matches input order so allocation_breakdown sort stability
    is preserved.
    """
    if not holdings:
        return []
    snapshots = [_snapshot_holding(h) for h in holdings]
    workers = min(ENRICH_MAX_WORKERS, len(holdings))
    if data_factory is None:
        from marketpulse.data.service import DataService

        if isinstance(data, DataService):
            workers = 1
    if workers <= 1:
        if data_factory is None:
            return [_enrich_one(h, data) for h in snapshots]
        rows: list[dict[str, Any]] = []
        for h in snapshots:
            with data_factory() as worker_data:
                rows.append(_enrich_one(h, worker_data))
        return rows
    from concurrent.futures import ThreadPoolExecutor

    if data_factory is not None:
        def enrich_with_worker_data(h: _HoldingSnapshot) -> dict[str, Any]:
            with data_factory() as worker_data:
                return _enrich_one(h, worker_data)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(enrich_with_worker_data, snapshots))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda h: _enrich_one(h, data), snapshots))


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
    `from_date`/`to_date` is an inclusive window on each row's
    executed_at.date() (fallback to created_at). Both BUY and SELL
    rows are filtered — `total_trades` counts BUY+SELL within the
    window; `wins`/`losses` count only the SELL rows that fell in.

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


def trade_count_this_month(session: Session) -> dict[str, int]:
    """Activity count in the current calendar month (UTC).

    Returns {total, buys, sells, dividends}. Splits intentionally not
    counted — they're corporate actions, not user activity.
    Not affected by any filter (always current month).
    """
    today = date.today()
    y, m = today.year, today.month
    next_y, next_m = (y + 1, 1) if m == 12 else (y, m + 1)
    start = date(y, m, 1)
    end_excl = date(next_y, next_m, 1)

    buys = sells = 0
    for t in session.query(Trade).all():
        when = (t.executed_at.date() if t.executed_at
                else (t.created_at.date() if t.created_at else None))
        if when is None or when < start or when >= end_excl:
            continue
        if t.action == "buy":
            buys += 1
        elif t.action == "sell":
            sells += 1

    dividends = (
        session.query(Dividend)
        .filter(Dividend.ex_date >= start, Dividend.ex_date < end_excl)
        .count()
    )

    return {
        "total": buys + sells + dividends,
        "buys": buys, "sells": sells, "dividends": dividends,
    }


def realized_pl_by_ticker(
    session: Session,
    *,
    top_n: int = 8,
) -> list[dict[str, Any]]:
    """Per-ticker realized P&L leaderboard, sorted by abs(P&L) desc, top_n cap.

    Uses FIFO matcher (LotMatch) to compute both realized_pl and cost basis.
    Note: LotMatch.realized_pl is GROSS (excludes fees) — see fifo.py docstring.
    For fee-accurate per-ticker totals, sum Trade.realized_pl directly instead.

    Returns: [{ticker, realized_pl, pct}, ...]
      pct = realized_pl / cost_basis_of_sold_lots * 100
      Tickers with zero realized_pl are omitted.
    """
    from marketpulse.holdings.fifo import match_lots_fifo

    matches = match_lots_fifo(session)
    if not matches:
        return []

    by_ticker: dict[str, dict[str, float]] = defaultdict(
        lambda: {"realized_pl": 0.0, "cost_basis": 0.0},
    )
    for m in matches:
        by_ticker[m.ticker]["realized_pl"] += m.realized_pl
        by_ticker[m.ticker]["cost_basis"] += m.quantity * m.buy_price

    rows = [
        {
            "ticker": t,
            "realized_pl": v["realized_pl"],
            "pct": (v["realized_pl"] / v["cost_basis"] * 100) if v["cost_basis"] else 0.0,
        }
        for t, v in by_ticker.items()
        if v["realized_pl"] != 0.0
    ]
    rows.sort(key=lambda r: abs(r["realized_pl"]), reverse=True)
    return rows[:top_n]


def today_portfolio_change(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate today's portfolio change.

    Rows without today_change_pct (e.g., quote fetch failed) are excluded
    from up/down counts and the dollars sum. Percentage is weighted by
    market value across eligible rows only.

    Returns:
      dollars: sum of (market_value * today_change_pct/100) for eligible rows
      pct: weighted by market_value of eligible rows
      up_count: rows with today_change_pct > 0
      down_count: rows with today_change_pct < 0
    """
    eligible = [
        r for r in rows
        if r.get("today_change_pct") is not None and r.get("market_value") is not None
    ]
    if not eligible:
        return {"dollars": 0.0, "pct": 0.0, "up_count": 0, "down_count": 0}

    dollars = sum(r["market_value"] * r["today_change_pct"] / 100 for r in eligible)
    total_mv = sum(r["market_value"] for r in eligible)
    pct = (dollars / total_mv * 100) if total_mv else 0.0
    up_count = sum(1 for r in eligible if r["today_change_pct"] > 0)
    down_count = sum(1 for r in eligible if r["today_change_pct"] < 0)
    return {
        "dollars": dollars,
        "pct": pct,
        "up_count": up_count,
        "down_count": down_count,
    }


def sector_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group rows by sector.

    Returns: [{sector, market_value, pct, holding_count}, ...]
    sorted by market_value desc. '未分类' falls naturally to its own bucket.
    """
    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {"market_value": 0.0, "holding_count": 0},
    )
    for r in rows:
        s = r["sector"]
        buckets[s]["market_value"] += r["market_value"] or 0.0
        buckets[s]["holding_count"] += 1
    total = sum(b["market_value"] for b in buckets.values())
    out = [
        {
            "sector": sector,
            "market_value": v["market_value"],
            "pct": (v["market_value"] / total * 100) if total else 0.0,
            "holding_count": v["holding_count"],
        }
        for sector, v in buckets.items()
    ]
    out.sort(key=lambda x: x["market_value"], reverse=True)
    return out


def contributors_ranked(
    rows: list[dict[str, Any]],
    *,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """Top N rows by |pl_dollars| — the biggest movers in absolute terms.

    NOTE: 'biggest by |pl|' does NOT guarantee a mix of positive and
    negative. If a portfolio has 5 large winners and 1 small loser,
    all 5 returned rows will be winners — that's correct behavior
    (the question is 'who moved the needle most').
    """
    ranked = sort_by_pl_impact(rows)
    return ranked[:top_n]


def avg_hold_days(
    session: Session,
    *,
    from_date: "date | None" = None,
    to_date: "date | None" = None,
) -> float | None:
    """Average hold_days across FIFO LotMatches whose sell_executed_at.date()
    falls in the inclusive window. Returns None when no matches qualify.
    """
    from marketpulse.holdings.fifo import match_lots_fifo

    matches = match_lots_fifo(session)
    if from_date is not None:
        matches = [m for m in matches if m.sell_executed_at.date() >= from_date]
    if to_date is not None:
        matches = [m for m in matches if m.sell_executed_at.date() <= to_date]
    if not matches:
        return None
    return sum(m.hold_days for m in matches) / len(matches)
