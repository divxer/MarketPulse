"""Lab — research/evaluation dashboards."""
from datetime import date, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from marketpulse.evaluation import scoring
from marketpulse.evaluation.outcomes import DEFAULT_HORIZONS
from marketpulse.strategies import load_strategies
from marketpulse.web.deps import get_db, require_auth
from marketpulse.web.main import templates

router = APIRouter()


def _qs_from_filters(filters: dict) -> str:
    """Build a URL-encoded query string from filters dict, dropping None /
    defaults / empty strings."""
    DEFAULTS = {"horizon": 5, "since_days": 90}
    payload = {}
    for k, v in filters.items():
        if v is None or v == "":
            continue
        if k in DEFAULTS and v == DEFAULTS[k]:
            continue
        payload[k] = str(v)
    return urlencode(payload)


@router.get("/lab/ai-track", response_class=HTMLResponse)
def lab_ai_track(
    request: Request,
    ticker: str | None = None,
    horizon: int = 5,
    source: str | None = None,
    strategy: str | None = None,
    verdict: str | None = None,
    since_days: str | int = 90,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    # Normalize since_days
    since: date | None
    if isinstance(since_days, str) and since_days == "all":
        since = None
        since_int = None
    else:
        try:
            sd_int = int(since_days)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail=f"invalid since_days: {since_days}",
            ) from exc
        if sd_int <= 0:
            raise HTTPException(status_code=422, detail="since_days must be positive or 'all'")
        since = date.today() - timedelta(days=sd_int)
        since_int = sd_int

    # Validate horizon
    if horizon not in DEFAULT_HORIZONS:
        raise HTTPException(
            status_code=422,
            detail=f"invalid horizon: must be one of {DEFAULT_HORIZONS}",
        )

    # When user filters by recap (or anything other than stock_analysis), strategy
    # is meaningless — recap events have no strategy field.
    if source and source != "stock_analysis":
        strategy = None

    ticker_u = ticker.upper() if ticker else None
    common = dict(horizon=horizon, source=source, since=since)

    overall = scoring.compute_hit_rate(
        db, ticker=ticker_u, subtype=verdict, strategy=strategy, **common,
    )
    trend = scoring.get_hit_rate_trend(
        db, ticker=ticker_u, subtype=verdict,
        window_days=since_int or 90, rolling=30, strategy=strategy, **common,
    )
    per_ticker = scoring.get_per_ticker_hit_rates(
        db, subtype=verdict, strategy=strategy, **common,
    )
    recent = scoring.get_recent_events_with_outcomes(
        db, ticker=ticker_u, subtype=verdict, limit=20, strategy=strategy, **common,
    )

    best = next(
        (t for t in per_ticker if t.n_total >= 5),
        None,
    )

    filters = {
        "ticker": ticker, "horizon": horizon,
        "source": source, "strategy": strategy,
        "verdict": verdict, "since_days": since_days,
    }

    # Pre-compute per-strategy aggregations for the new strategy leaderboard.
    # Always computed regardless of strategy filter, so the UI shows all
    # strategies' performance.
    strategy_lib = load_strategies()
    per_strategy: list[dict] = []
    for name in strategy_lib:
        s_stats = scoring.compute_hit_rate(
            db, ticker=None, subtype=None, source="stock_analysis",
            strategy=name, horizon=horizon, since=since,
        )
        if s_stats.n_total > 0:
            per_strategy.append({
                "name": name,
                "display_name": strategy_lib[name].display_name,
                "expected_horizons": strategy_lib[name].expected_horizons,
                "n_total": s_stats.n_total,
                "n_hits": s_stats.n_hits,
                "hit_rate": s_stats.hit_rate,
                "avg_excess_return": s_stats.avg_excess_return,
            })
    per_strategy.sort(
        key=lambda x: x["hit_rate"] if x["hit_rate"] is not None else -1,
        reverse=True,
    )

    # Best strategy (n>=5) for the KPI strip in T13
    best_strategy = next(
        (s for s in per_strategy if s["n_total"] >= 5),
        None,
    )

    return templates.TemplateResponse(request, "lab_ai_track.html", {
        "overall": overall,
        "trend": trend,
        "per_ticker": per_ticker,
        "recent": recent,
        "best": best,
        "best_strategy": best_strategy,
        "per_strategy": per_strategy,
        "strategy_library": list(strategy_lib.values()),
        "filters": filters,
        "filters_qs": _qs_from_filters(filters),
        "filters_qs_no_ticker": _qs_from_filters({**filters, "ticker": None}),
        "filters_qs_no_strategy": _qs_from_filters({**filters, "strategy": None}),
    })
