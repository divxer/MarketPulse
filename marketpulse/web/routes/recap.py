import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from marketpulse.ai.prompts import COMMENTARY_PROMPT_VERSION
from marketpulse.config import get_settings
from marketpulse.db.models import DailyRecap
from marketpulse.recap.service import RecapService
from marketpulse.web.deps import get_db, get_recap_service, require_auth
from marketpulse.web.main import templates

router = APIRouter()


def _safe_json_parse(text: str | None, default):
    """Try to parse JSON; return `default` on failure or None input."""
    if not text:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def _normalize_market_snap(raw: dict | list | None) -> list[dict]:
    """Reshape stored market_summary_json into template-friendly list.

    Stored shape: {"spy": pct, "qqq": pct, "dia": pct, "vix": price,
                   "vix_change_pct": pct (optional, added in Phase 5e)}

    For VIX, "down is good" — direction uses vix_change_pct (which is
    stored separately, since the v[vix] value is the index level itself).
    For SPY/QQQ/DIA the stored value IS the change_pct.
    """
    if not raw:
        return []
    if isinstance(raw, list):
        return raw

    out = []
    INDICES = [
        ("spy", "标普 500"),
        ("qqq", "纳指 100"),
        ("dia", "道指"),
        ("vix", "VIX 恐慌指数"),
    ]
    vix_pct = raw.get("vix_change_pct")  # may be None for legacy recaps

    for key, label in INDICES:
        v = raw.get(key)
        if v is None:
            continue
        is_vix = (key == "vix")
        # Direction: VIX uses change_pct (down = good); others use stored value (= pct already)
        if is_vix:
            up = (vix_pct <= 0) if vix_pct is not None else None
            pct_display = f"{vix_pct:+.2f}%" if vix_pct is not None else None
        else:
            up = (v >= 0)
            pct_display = f"{v:+.2f}%"
        out.append({
            "label": label,
            "value": f"{v:.2f}",
            "pct": pct_display,
            "up": up if up is not None else False,
        })
    return out


@router.get("/recaps", response_class=HTMLResponse)
def recap_list(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    rows = (
        db.query(DailyRecap)
        .order_by(DailyRecap.recap_date.desc())
        .limit(60)
        .all()
    )
    enriched = []
    for r in rows:
        totals = _safe_json_parse(r.holdings_totals_json, {})
        enriched.append({
            "recap_date": r.recap_date,
            "generation_status": r.generation_status,
            "generated_at": r.generated_at,
            "summary": (r.ai_commentary_text or "")[:200],
            # compute_totals returns {cost, market_value, pl_dollars, pl_pct}
            "today_pl_dollars": totals.get("pl_dollars"),
            "today_pl_pct": totals.get("pl_pct"),
        })
    return templates.TemplateResponse(request, "recaps.html", {"rows": enriched})


@router.get("/recap/{recap_date}", response_class=HTMLResponse)
def recap_detail(
    request: Request,
    recap_date: date,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    row = (
        db.query(DailyRecap)
        .filter(DailyRecap.recap_date == recap_date)
        .one_or_none()
    )
    if not row:
        raise HTTPException(status_code=404, detail="not found")

    prev_recaps = (
        db.query(DailyRecap)
        .filter(DailyRecap.recap_date < recap_date)
        .order_by(DailyRecap.recap_date.desc())
        .limit(6)
        .all()
    )

    settings = get_settings()

    return templates.TemplateResponse(
        request,
        "recap.html",
        {
            "row": row,
            "recap_date": recap_date,
            "commentary_md": row.ai_commentary_text or "",
            "market_snap": _normalize_market_snap(
                _safe_json_parse(row.market_summary_json, {})
            ),
            "portfolio_today": _safe_json_parse(row.holdings_totals_json, {}),
            "watchlist_perf": _safe_json_parse(row.watchlist_performance_json, []),
            "key_events": _safe_json_parse(row.key_events_json, []),
            "prev_recaps": prev_recaps,
            "model_version": f"{COMMENTARY_PROMPT_VERSION} · {settings.ai_model}",
        },
    )


@router.post("/recap/{recap_date}/retry")
def recap_retry(
    recap_date: date,
    svc: RecapService = Depends(get_recap_service),
    _: None = Depends(require_auth),
):
    svc.generate(recap_date)
    return RedirectResponse(url=f"/recap/{recap_date}", status_code=303)
