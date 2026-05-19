"""Lab — research/evaluation dashboards."""
from datetime import date, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from marketpulse.evaluation import scoring
from marketpulse.evaluation.outcomes import DEFAULT_HORIZONS
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

    ticker_u = ticker.upper() if ticker else None
    common = dict(horizon=horizon, source=source, since=since)

    overall = scoring.compute_hit_rate(
        db, ticker=ticker_u, subtype=verdict, **common,
    )
    trend = scoring.get_hit_rate_trend(
        db, ticker=ticker_u, subtype=verdict,
        window_days=since_int or 90, rolling=30, **common,
    )
    per_ticker = scoring.get_per_ticker_hit_rates(
        db, subtype=verdict, **common,
    )
    recent = scoring.get_recent_events_with_outcomes(
        db, ticker=ticker_u, subtype=verdict, limit=20, **common,
    )

    best = next(
        (t for t in per_ticker if t.n_total >= 5),
        None,
    )

    filters = {
        "ticker": ticker, "horizon": horizon,
        "source": source, "verdict": verdict, "since_days": since_days,
    }

    return templates.TemplateResponse(request, "lab_ai_track.html", {
        "overall": overall,
        "trend": trend,
        "per_ticker": per_ticker,
        "recent": recent,
        "best": best,
        "filters": filters,
        "filters_qs": _qs_from_filters(filters),
        "filters_qs_no_ticker": _qs_from_filters({**filters, "ticker": None}),
    })
