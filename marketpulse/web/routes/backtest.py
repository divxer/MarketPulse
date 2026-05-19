"""Lab — /lab/backtest Strategy Performance Observatory."""
from datetime import date, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from marketpulse.backtest.simulator import run_all_backtests
from marketpulse.evaluation.outcomes import DEFAULT_HORIZONS
from marketpulse.web.deps import get_db, require_auth
from marketpulse.web.main import templates

router = APIRouter()


def _qs_from_filters(filters: dict) -> str:
    """Build a clean query string, dropping defaults / None / empty."""
    DEFAULTS = {"horizon": 5, "since_days": 90}
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

    results = run_all_backtests(db, horizon=horizon, since=since)

    strategies = [r for r in results if r.strategy != "__spy_buyhold__"]
    spy = next((r for r in results if r.strategy == "__spy_buyhold__"), None)

    strategies_sorted = sorted(
        strategies,
        key=lambda r: r.sharpe if r.sharpe is not None else -999.0,
        reverse=True,
    )

    best_strategy = next(
        (r for r in strategies_sorted if r.n_trades >= 5), None,
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

    filters = {"horizon": horizon, "since_days": since_days}

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
            "filters": filters,
            "filters_qs": _qs_from_filters(filters),
        },
    )
