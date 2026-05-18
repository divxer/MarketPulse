import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

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

    Service dumps flat dict {"spy": pct, "qqq": pct, "dia": pct, "vix": price}.
    Template expects [{label, value, pct, up}, ...].

    For VIX, "down is good" → up=(pct <= 0). For others, up=(pct >= 0).
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
    for key, label in INDICES:
        v = raw.get(key)
        if v is None:
            continue
        is_vix = key == "vix"
        out.append({
            "label": label,
            "value": f"{v:.2f}",
            "pct": None if is_vix else f"{v:+.2f}%",
            "up": (v <= 0) if is_vix else (v >= 0),
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
            "model_version": f"commentary-v4-zh-markdown · {settings.ai_model}",
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
