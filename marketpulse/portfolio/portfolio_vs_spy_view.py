# Layer: pure
"""PR4 — pure presenter for /lab/portfolio-vs-spy.

Maps a list[NavSnapshot] (PR3a source of truth) into a frozen view-model with
precomputed SVG polyline strings. No DB, no FastAPI, no Jinja, no auth, no clock
(L1). All Decimal->string formatting lives here (L13).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from marketpulse.portfolio.north_star import (  # noqa: F401  # used in later PR4 tasks
    NORTH_STAR_WINDOW,
    NavSnapshot,
)

VIEWBOX_W = 800
VIEWBOX_H = 280
MAX_CHART_POINTS = 180
VALUE_NA = "N/A"


@dataclass(frozen=True)
class ChartData:
    portfolio_points: str        # SVG polyline "x,y x,y …"
    spy_points: str
    excess_points: str
    zero_y: float                # y-coord of the excess 0-reference line
    index_lo: Decimal            # shared index y-axis bound (label)
    index_hi: Decimal
    excess_lo: Decimal
    excess_hi: Decimal
    viewbox_w: int
    viewbox_h: int


@dataclass(frozen=True)
class PortfolioVsSpyView:
    has_data: bool
    chartable: bool
    hero_excess_return: Decimal | None
    hero_excess_return_label: str
    badge: Literal["PRELIMINARY", "SUFFICIENT"]
    show_insufficiency_banner: bool
    portfolio_index_latest: Decimal | None
    portfolio_index_label: str
    spy_index_latest: Decimal | None
    spy_index_label: str
    coverage_observed: int
    coverage_required: int
    coverage_label: str
    is_sufficient: bool
    first_date: date | None
    last_date: date | None
    chart_start_date: date | None
    dropped_prefix_count: int
    excluded_nonprefix_count: int
    chart: ChartData | None


def _fmt_excess_label(value: Decimal | None) -> str:
    """0.032 -> '+3.2%'; -0.014 -> '-1.4%'; 0 -> '+0.0%'; None -> 'N/A'."""
    if value is None:
        return VALUE_NA
    pct = (Decimal(value) * Decimal("100")).quantize(Decimal("0.1"))
    return f"+{pct}%" if pct >= 0 else f"{pct}%"


def _fmt_index_label(value: Decimal | None) -> str:
    """1.0413 -> '1.041'; None -> 'N/A'."""
    if value is None:
        return VALUE_NA
    return f"{Decimal(value).quantize(Decimal('0.001'))}"
