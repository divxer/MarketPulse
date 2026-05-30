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
