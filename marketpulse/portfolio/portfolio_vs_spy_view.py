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

from marketpulse.portfolio.north_star import NORTH_STAR_WINDOW, NavSnapshot

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
    if pct == 0:
        pct = abs(pct)  # normalize negative zero so tiny negatives don't show "+-0.0%"
    return f"+{pct}%" if pct >= 0 else f"{pct}%"


def _fmt_index_label(value: Decimal | None) -> str:
    """1.0413 -> '1.041'; None -> 'N/A'."""
    if value is None:
        return VALUE_NA
    return f"{Decimal(value).quantize(Decimal('0.001'))}"


def _is_complete(s: NavSnapshot) -> bool:
    return (
        s.portfolio_index is not None
        and s.spy_index is not None
        and s.excess_return is not None
    )


def _compute_chart_run(
    series: list[NavSnapshot],
) -> tuple[list[NavSnapshot], int, int]:
    """Return (chart_run, dropped_prefix_count, excluded_nonprefix_count).

    chart_run is the CONTIGUOUS chart run of all-three-non-null snapshots starting
    at the first complete row (L2, L15) — NOT necessarily a suffix. It STOPS at the
    first later incomplete row (a mid/tail gap) rather than connecting across it.
      - dropped_prefix_count = index of first complete row (true leading prefix).
        If no complete row exists, every row is prefix -> len(series).
      - excluded_nonprefix_count = rows after `start` dropped because a gap appeared
        (should be 0 under the PR3a lazy-anchor invariant).
    """
    start = next((i for i, s in enumerate(series) if _is_complete(s)), None)
    if start is None:
        return [], len(series), 0
    tail = series[start:]
    run: list[NavSnapshot] = []
    for s in tail:
        if _is_complete(s):
            run.append(s)
        else:
            break
    return run, start, len(tail) - len(run)


def _downsample(
    rows: list[NavSnapshot], max_points: int = MAX_CHART_POINTS,
) -> list[NavSnapshot]:
    """Deterministic stride-sample to <= max_points, ALWAYS preserving the first
    and last rows (L5). No-op when len(rows) <= max_points.

    NOTE: round()+set dedup means the result caps at <= max_points but is not
    guaranteed to be EXACTLY max_points (adjacent indices may collide). This
    satisfies L5 (<=180); exact-180 is not required for trend rendering.
    """
    n = len(rows)
    if n <= max_points:
        return list(rows)
    # Evenly spaced indices across [0, n-1]; i=0 -> 0, i=max-1 -> n-1.
    idxs = sorted({
        round(i * (n - 1) / (max_points - 1)) for i in range(max_points)
    })
    return [rows[i] for i in idxs]


def _scale_y(value: Decimal, lo: Decimal, hi: Decimal, height: int) -> float:
    """Map value in [lo, hi] to SVG y (grows downward -> invert). Guards lo==hi."""
    if hi == lo:
        return height / 2
    frac = (Decimal(value) - lo) / (hi - lo)
    return height - float(frac) * height


def _build_chart_data(chart_run: list[NavSnapshot]) -> ChartData:
    """Compose SVG polyline strings. Caller guarantees len(chart_run) >= 2 (L2).

    L3: portfolio and SPY share one [lo, hi] index scale.
    L4: the excess scale always contains 0 so the 0-reference line is on-canvas.
    L5: x is computed AFTER downsampling, against the plotted count.
    """
    plotted = _downsample(chart_run)
    n = len(plotted)
    # chart_run only contains all-three-non-null rows (L2); narrow for the type
    # checker so the Decimal arithmetic below sees no `| None`.
    for s in plotted:
        assert s.portfolio_index is not None
        assert s.spy_index is not None
        assert s.excess_return is not None
    port_vals = [s.portfolio_index for s in plotted]
    spy_vals = [s.spy_index for s in plotted]
    exc_vals = [s.excess_return for s in plotted]

    # Shared index scale (L3).
    lo = min(min(port_vals), min(spy_vals))
    hi = max(max(port_vals), max(spy_vals))

    # Excess scale that always contains 0 (L4).
    elo = min(Decimal("0"), min(exc_vals))
    ehi = max(Decimal("0"), max(exc_vals))
    if ehi == elo:  # all excess == 0 -> degenerate; widen symmetrically.
        elo, ehi = Decimal("-0.0001"), Decimal("0.0001")

    def x_at(i: int) -> float:
        return i / (n - 1) * VIEWBOX_W

    def points(vals: list[Decimal], lo_: Decimal, hi_: Decimal) -> str:
        return " ".join(
            f"{x_at(i):.1f},{_scale_y(v, lo_, hi_, VIEWBOX_H):.1f}"
            for i, v in enumerate(vals)
        )

    zero_y = _scale_y(Decimal("0"), elo, ehi, VIEWBOX_H)

    return ChartData(
        portfolio_points=points(port_vals, lo, hi),
        spy_points=points(spy_vals, lo, hi),
        excess_points=points(exc_vals, elo, ehi),
        zero_y=zero_y,
        index_lo=lo, index_hi=hi,
        excess_lo=elo, excess_hi=ehi,
        viewbox_w=VIEWBOX_W, viewbox_h=VIEWBOX_H,
    )


def build_portfolio_vs_spy_view(series: list[NavSnapshot]) -> PortfolioVsSpyView:
    """Map the snapshot series into the view-model. Pure (L1)."""
    if not series:
        return PortfolioVsSpyView(
            has_data=False, chartable=False,
            hero_excess_return=None, hero_excess_return_label=VALUE_NA,
            badge="PRELIMINARY", show_insufficiency_banner=False,
            portfolio_index_latest=None, portfolio_index_label=VALUE_NA,
            spy_index_latest=None, spy_index_label=VALUE_NA,
            coverage_observed=0, coverage_required=NORTH_STAR_WINDOW,
            coverage_label=f"0 / {NORTH_STAR_WINDOW}", is_sufficient=False,
            first_date=None, last_date=None, chart_start_date=None,
            dropped_prefix_count=0, excluded_nonprefix_count=0, chart=None,
        )

    latest = series[-1]
    chart_run, dropped, excluded = _compute_chart_run(series)
    chartable = len(chart_run) >= 2
    chart = _build_chart_data(chart_run) if chartable else None

    return PortfolioVsSpyView(
        has_data=True,
        chartable=chartable,
        hero_excess_return=latest.excess_return,
        hero_excess_return_label=_fmt_excess_label(latest.excess_return),
        badge="SUFFICIENT" if latest.is_sufficient else "PRELIMINARY",
        show_insufficiency_banner=not latest.is_sufficient,
        portfolio_index_latest=latest.portfolio_index,
        portfolio_index_label=_fmt_index_label(latest.portfolio_index),
        spy_index_latest=latest.spy_index,
        spy_index_label=_fmt_index_label(latest.spy_index),
        coverage_observed=latest.trading_days_observed,
        coverage_required=NORTH_STAR_WINDOW,
        coverage_label=f"{latest.trading_days_observed} / {NORTH_STAR_WINDOW}",
        is_sufficient=latest.is_sufficient,
        first_date=series[0].trading_date,
        last_date=latest.trading_date,
        chart_start_date=chart_run[0].trading_date if chart_run else None,
        dropped_prefix_count=dropped,
        excluded_nonprefix_count=excluded,
        chart=chart,
    )
