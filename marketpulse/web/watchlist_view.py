# Layer: web
"""Watchlist AI-Universe presenter — cache-only, ZERO network.

Assembles display-ready WatchlistCard view-models from DB + local caches only:
price_cache (price/sparkline), latest EvaluationEvent subtype (verdict),
holdings/paper-position sets (status), and the on-disk sector cache + YAML
overrides (sector grouping). Never calls a quote client, yfinance, or the
network get_sector — enforced by tests/architecture/test_watchlist_zero_network.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WatchlistCard:
    ticker: str
    price_display: str
    change_display: str
    change_class: str
    sparkline: list[float]
    sector: str
    verdict_class: str
    verdict_label: str
    status_label: str
    status_class: str
    spark_stroke: str = "var(--mp-up)"
    item_id: int | None = None
    active: bool = False


@dataclass(frozen=True)
class SectorGroup:
    name: str
    count: int
    cards: list[WatchlistCard]


@dataclass(frozen=True)
class Coverage:
    total: int
    sectors: int
    holdings: int
    paper: int
    universe_only: int


def _empty_coverage() -> "Coverage":
    return Coverage(0, 0, 0, 0, 0)


@dataclass(frozen=True)
class WatchlistView:
    groups: list[SectorGroup] = field(default_factory=list)
    coverage: Coverage = field(default_factory=_empty_coverage)


UNCATEGORIZED = "Uncategorized"

_VERDICT = {
    "bullish": ("mp-ai-badge--good", "Bullish"),
    "bearish": ("mp-ai-badge--bad", "Bearish"),
    "neutral": ("mp-ai-badge--neutral", "Neutral"),
}


def _fmt_price(close: float | None) -> str:
    return f"${close:,.2f}" if close is not None else "—"


def _fmt_change(latest: float | None, prior: float | None) -> tuple[str, str]:
    if latest is None or prior is None or prior == 0:
        return "—", ""
    pct = (latest - prior) / prior * 100.0
    cls = "mp-watchlist__chg--up" if pct >= 0 else "mp-watchlist__chg--down"
    return f"{'+' if pct >= 0 else ''}{pct:.2f}%", cls


def _verdict_fields(subtype: str | None) -> tuple[str, str]:
    return _VERDICT.get(subtype or "", ("mp-ai-badge--pending", "Pending"))


def _status_fields(
    ticker: str, holdings: set[str], paper: set[str],
) -> tuple[str, str]:
    if ticker in holdings:
        return "Holding", "mp-chip--success"
    if ticker in paper:
        return "Paper Position", "mp-chip--warn"
    return "Universe Only", "mp-chip--muted"


from sqlalchemy import func, select

from marketpulse.backtest.sector import load_sector_cache, load_sector_overrides
from marketpulse.db.models import (
    EvaluationEvent, Holding, PaperPosition, PriceCacheEntry, WatchlistItem,
)

_SPARK_N = 30


def _price_blocks(session, tickers: list[str]) -> dict[str, dict]:
    """Per ticker: latest close, prior close, last-N closes (ascending)."""
    if not tickers:
        return {}
    rows = session.execute(
        select(PriceCacheEntry.ticker, PriceCacheEntry.date, PriceCacheEntry.close)
        .where(PriceCacheEntry.ticker.in_(tickers))
        .order_by(PriceCacheEntry.ticker, PriceCacheEntry.date.asc())
    ).all()
    closes: dict[str, list[float]] = {}
    for tkr, _d, close in rows:
        closes.setdefault(tkr, []).append(float(close))
    out: dict[str, dict] = {}
    for tkr, series in closes.items():
        out[tkr] = {
            "latest": series[-1],
            "prior": series[-2] if len(series) >= 2 else None,
            # Contract: [] when <2 points (sparkpoints needs >=2 to draw a line).
            "spark": series[-_SPARK_N:] if len(series) >= 2 else [],
        }
    return out


def _latest_verdicts(session, tickers: list[str]) -> dict[str, str]:
    """Latest EvaluationEvent(ai_analysis) subtype per ticker. Deterministic
    tie-break on equal event_time via id DESC (row_number window — SQLite 3.25+)."""
    if not tickers:
        return {}
    rn = func.row_number().over(
        partition_by=EvaluationEvent.ticker,
        order_by=(EvaluationEvent.event_time.desc(), EvaluationEvent.id.desc()),
    ).label("rn")
    sub = (
        select(EvaluationEvent.ticker, EvaluationEvent.subtype, rn)
        .where(EvaluationEvent.event_type == "ai_analysis")
        .where(EvaluationEvent.ticker.in_(tickers))
        .subquery()
    )
    rows = session.execute(
        select(sub.c.ticker, sub.c.subtype).where(sub.c.rn == 1)
    ).all()
    return {t: st for t, st in rows}


def _status_sets(session) -> tuple[set[str], set[str]]:
    holdings = {t for (t,) in session.execute(select(Holding.ticker)).all()}
    paper = {t for (t,) in session.execute(
        select(PaperPosition.ticker).where(PaperPosition.status == "OPEN")
    ).all()}
    return holdings, paper


def _sector_map(tickers: list[str], holdings_sector: dict[str, str]) -> dict[str, str]:
    """Cache-only sector (L5): holdings.sector first, then on-disk cache + YAML
    overrides. NO network. Uncached -> UNCATEGORIZED."""
    cache = load_sector_cache()
    overrides = load_sector_overrides()
    out: dict[str, str] = {}
    for t in tickers:
        out[t] = (holdings_sector.get(t) or overrides.get(t)
                  or cache.get(t) or UNCATEGORIZED)
    return out


def _card(ticker, block, subtype, sector, holdings, paper, item_id=None, active=False):
    latest = block["latest"] if block else None
    prior = block["prior"] if block else None
    change_display, change_class = _fmt_change(latest, prior)
    v_class, v_label = _verdict_fields(subtype)
    s_label, s_class = _status_fields(ticker, holdings, paper)
    spark_stroke = ("var(--mp-down)" if change_class == "mp-watchlist__chg--down"
                    else "var(--mp-up)")
    return WatchlistCard(
        ticker=ticker,
        price_display=_fmt_price(latest),
        change_display=change_display,
        change_class=change_class,
        sparkline=block["spark"] if block else [],
        sector=sector,
        verdict_class=v_class,
        verdict_label=v_label,
        status_label=s_label,
        status_class=s_class,
        spark_stroke=spark_stroke,
        item_id=item_id,
        active=active,
    )


def build_watchlist_view(session) -> WatchlistView:
    id_map = {
        t: i for (i, t) in session.execute(
            select(WatchlistItem.id, WatchlistItem.ticker)
        ).all()
    }
    tickers = sorted(id_map)
    blocks = _price_blocks(session, tickers)
    verdicts = _latest_verdicts(session, tickers)
    holdings, paper = _status_sets(session)
    holdings_sector = {
        t: s for (t, s) in session.execute(
            select(Holding.ticker, Holding.sector)
        ).all() if s
    }
    sectors = _sector_map(tickers, holdings_sector)

    cards = [
        _card(t, blocks.get(t), verdicts.get(t), sectors[t], holdings, paper,
              item_id=id_map[t])
        for t in tickers
    ]
    by_sector: dict[str, list[WatchlistCard]] = {}
    for c in cards:
        by_sector.setdefault(c.sector, []).append(c)

    # Order: count DESC, then name ASC; Uncategorized always last (L12).
    def _key(name: str) -> tuple:
        is_uncat = name == UNCATEGORIZED
        return (is_uncat, -len(by_sector[name]), name)

    groups = [
        SectorGroup(name=n, count=len(by_sector[n]),
                    cards=sorted(by_sector[n], key=lambda c: c.ticker))
        for n in sorted(by_sector, key=_key)
    ]
    coverage = Coverage(
        total=len(tickers),
        sectors=len(by_sector),       # includes the Uncategorized group when present
        holdings=sum(1 for c in cards if c.status_label == "Holding"),
        paper=sum(1 for c in cards if c.status_label == "Paper Position"),
        universe_only=sum(1 for c in cards if c.status_label == "Universe Only"),
    )
    return WatchlistView(groups=groups, coverage=coverage)
