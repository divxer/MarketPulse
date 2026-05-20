"""Lab — /lab/backtest Strategy Performance Observatory."""
from datetime import date, timedelta
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from marketpulse.backtest.simulator import run_all_backtests
from marketpulse.evaluation.outcomes import DEFAULT_HORIZONS
from marketpulse.web.deps import get_db, require_auth
from marketpulse.web.main import templates

router = APIRouter()


def _compute_size_distribution(
    bid_records: list,
    min_position: float = 200.0,
    max_position: float = 4_000.0,
    n_bins: int = 7,
) -> list[float]:
    """Linearly-spaced histogram of position sizes across won/dedup/cap/cash bids.

    Spec § 4: 7 bins linearly over [min_position, max_position]. Excludes
    size_too_small (their position_size is the raw pre-clamp value, not a
    real allocation). Returns normalized heights 0-1 for SVG rendering.
    """
    valid = [
        b.position_size for b in bid_records
        if b.outcome != "size_too_small" and b.position_size > 0
    ]
    if not valid:
        return [0.0] * n_bins
    bin_width = (max_position - min_position) / n_bins
    counts = [0] * n_bins
    for size in valid:
        bin_idx = min(int((size - min_position) / bin_width), n_bins - 1)
        bin_idx = max(0, bin_idx)
        counts[bin_idx] += 1
    max_count = max(counts) if counts else 1
    return [c / max_count for c in counts]


def _qs_from_filters(filters: dict) -> str:
    """Build a clean query string, dropping defaults / None / empty."""
    DEFAULTS = {"horizon": 5, "since_days": 90, "mode": "per-strategy"}
    payload = {}
    for k, v in filters.items():
        if v is None or v == "":
            continue
        if k in DEFAULTS and v == DEFAULTS[k]:
            continue
        payload[k] = str(v)
    return urlencode(payload)


@router.get("/lab/backtest", response_class=HTMLResponse)
def lab_backtest(
    request: Request,
    horizon: int = 5,
    since_days: str | int = 90,
    mode: Literal["per-strategy", "shared-pool"] = "per-strategy",
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    since: date | None
    if isinstance(since_days, str) and since_days == "all":
        since = None
    else:
        try:
            sd_int = int(since_days)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail=f"invalid since_days: {since_days}",
            ) from exc
        if sd_int <= 0:
            raise HTTPException(
                status_code=422, detail="since_days must be positive or 'all'",
            )
        since = date.today() - timedelta(days=sd_int)

    if horizon not in DEFAULT_HORIZONS:
        raise HTTPException(
            status_code=422,
            detail=f"invalid horizon: must be one of {DEFAULT_HORIZONS}",
        )

    if mode == "shared-pool":
        from marketpulse.backtest.simulator import run_shared_pool_backtest
        out = run_shared_pool_backtest(
            db, horizon=horizon, since=since, lookback_days=60,
        )
        results = out["isolated"]
        shared_result = out["shared"]
        # Compute size distribution histogram from bid history
        size_distribution = _compute_size_distribution(
            shared_result.bid_history,
            min_position=200.0,
            max_position=4_000.0,
            n_bins=7,
        )
    else:
        results = run_all_backtests(db, horizon=horizon, since=since)
        shared_result = None
        size_distribution = None

    strategies = [r for r in results if r.strategy != "__spy_buyhold__"]
    spy = next((r for r in results if r.strategy == "__spy_buyhold__"), None)

    strategies_sorted = sorted(
        strategies,
        key=lambda r: r.sharpe if r.sharpe is not None else -999.0,
        reverse=True,
    )

    # Best Strategy must have n>=5 AND a real Sharpe — without both,
    # the KPI hint (which formats Sharpe to 2 decimals) can't render.
    best_strategy = next(
        (r for r in strategies_sorted
         if r.n_trades >= 5 and r.sharpe is not None),
        None,
    )
    best_sharpe = next(
        (r for r in strategies_sorted if r.sharpe is not None), None,
    )
    best_cum = max(strategies, key=lambda r: r.cumulative_return, default=None)
    worst_dd = min(strategies, key=lambda r: r.max_drawdown, default=None)
    avg_excess = (
        sum(r.excess_vs_spy for r in strategies) / len(strategies)
        if strategies else 0.0
    )

    chart_data = _build_chart_data(strategies + ([spy] if spy else []))

    filters = {"horizon": horizon, "since_days": since_days, "mode": mode}

    return templates.TemplateResponse(
        request, "lab_backtest.html",
        {
            "strategies": strategies_sorted,
            "spy": spy,
            "best_strategy": best_strategy,
            "best_sharpe": best_sharpe,
            "best_cum": best_cum,
            "worst_dd": worst_dd,
            "avg_excess": avg_excess,
            "chart_data": chart_data,
            "filters": filters,
            "filters_qs": _qs_from_filters(filters),
            "mode": mode,
            "shared_result": shared_result,
            "lookback_days": 60,
            "size_distribution": size_distribution,
            "min_position": 200.0,
            "max_position": 4_000.0,
            "sizing_policy": shared_result.sizing_policy if shared_result else None,
        },
    )


def _build_chart_data(
    results: list,
) -> dict:
    """Compose polyline points + drawdown points for all results.

    Returns a dict with:
      - equity_curves: list of {name, display_name, color, points_str, is_spy}
      - drawdown_curves: same shape, drawdown values
      - x_axis: list of (frac, label) tuples for axis labels
    """
    all_dates = sorted({d for r in results for d, _ in r.daily_equity_curve})
    if not all_dates:
        return {"equity_curves": [], "drawdown_curves": [], "x_axis": []}

    date_to_idx = {d: i for i, d in enumerate(all_dates)}
    n = len(all_dates)
    width = 800.0
    eq_height = 280.0
    dd_height = 140.0

    all_vals = [v for r in results for _, v in r.daily_equity_curve]
    eq_min = min(all_vals) if all_vals else 0
    eq_max = max(all_vals) if all_vals else 1
    eq_range = max(eq_max - eq_min, 1e-6)

    palette = [
        "#2563eb", "#16a34a", "#dc2626", "#ea580c",
        "#9333ea", "#0891b2", "#475569",
    ]

    equity_curves = []
    drawdown_curves = []
    for i, r in enumerate(results):
        color = palette[i % len(palette)]
        is_spy = r.strategy == "__spy_buyhold__"
        if is_spy:
            color = "#475569"

        pts = []
        for d, v in r.daily_equity_curve:
            x = date_to_idx[d] / max(n - 1, 1) * width
            y = eq_height - ((v - eq_min) / eq_range) * eq_height
            pts.append(f"{x:.1f},{y:.1f}")

        if r.daily_equity_curve:
            values = [v for _, v in r.daily_equity_curve]
            peak = values[0]
            dd_pts = []
            for j, v in enumerate(values):
                peak = max(peak, v)
                dd = (v - peak) / peak if peak > 0 else 0.0
                y = (-dd) * dd_height * 2
                y = min(y, dd_height)
                d_j, _ = r.daily_equity_curve[j]
                x = date_to_idx[d_j] / max(n - 1, 1) * width
                dd_pts.append(f"{x:.1f},{y:.1f}")
        else:
            dd_pts = []

        equity_curves.append({
            "name": r.strategy,
            "display_name": r.display_name,
            "color": color,
            "is_spy": is_spy,
            "points_str": " ".join(pts),
        })
        drawdown_curves.append({
            "name": r.strategy,
            "display_name": r.display_name,
            "color": color,
            "is_spy": is_spy,
            "points_str": " ".join(dd_pts),
        })

    label_count = min(5, n)
    if n <= 1:
        x_axis = [(0.0, str(all_dates[0]))] if all_dates else []
    else:
        x_axis = [
            (i / (label_count - 1), str(all_dates[round(i / (label_count - 1) * (n - 1))]))
            for i in range(label_count)
        ]

    return {
        "equity_curves": equity_curves,
        "drawdown_curves": drawdown_curves,
        "x_axis": x_axis,
        "eq_min": eq_min,
        "eq_max": eq_max,
    }
