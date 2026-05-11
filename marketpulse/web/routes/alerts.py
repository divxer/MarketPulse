import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from marketpulse.alerts.engine import VALID_METRICS, VALID_OPS
from marketpulse.config import get_settings
from marketpulse.db.models import AlertRule
from marketpulse.web.deps import get_db, require_auth
from marketpulse.web.main import templates

router = APIRouter()

_TICKER_RE = re.compile(r"^[A-Z\^][A-Z0-9.\-]{0,15}$")


@router.get("/alerts", response_class=HTMLResponse)
def alerts_page(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    rules = db.query(AlertRule).order_by(AlertRule.enabled.desc(), AlertRule.id).all()
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "alerts.html",
        {
            "rules": rules,
            "notifier_kind": settings.notifier_kind,
            "metrics": VALID_METRICS,
            "ops": VALID_OPS,
        },
    )


@router.post("/alerts", response_class=HTMLResponse)
def alerts_add(
    request: Request,
    ticker: str = Form(...),
    metric: str = Form(...),
    op: str = Form(...),
    threshold: float = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    normalized = ticker.strip().upper()
    if not _TICKER_RE.match(normalized):
        raise HTTPException(status_code=422, detail="invalid ticker")
    if metric not in VALID_METRICS:
        raise HTTPException(status_code=422, detail=f"metric must be one of {VALID_METRICS}")
    if op not in VALID_OPS:
        raise HTTPException(status_code=422, detail=f"op must be one of {VALID_OPS}")
    rule = AlertRule(
        ticker=normalized, metric=metric, op=op,
        threshold=threshold, enabled=True, notes=notes or None,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return templates.TemplateResponse(
        request, "partials/alert_row.html", {"rule": rule},
    )


@router.post("/alerts/{rule_id}/toggle", response_class=HTMLResponse)
def alerts_toggle(
    request: Request,
    rule_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    rule = db.query(AlertRule).filter(AlertRule.id == rule_id).one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="not found")
    rule.enabled = not rule.enabled
    db.commit()
    db.refresh(rule)
    return templates.TemplateResponse(
        request, "partials/alert_row.html", {"rule": rule},
    )


@router.delete("/alerts/{rule_id}", response_class=HTMLResponse)
def alerts_delete(
    rule_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    db.query(AlertRule).filter(AlertRule.id == rule_id).delete()
    db.commit()
    return HTMLResponse("")
