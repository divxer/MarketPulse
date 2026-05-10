from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from marketpulse.db.models import DailyRecap
from marketpulse.recap.service import RecapService
from marketpulse.web.deps import get_db, get_recap_service, require_auth
from marketpulse.web.main import templates

router = APIRouter()


@router.get("/recaps", response_class=HTMLResponse)
def recap_list(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    rows = db.query(DailyRecap).order_by(DailyRecap.recap_date.desc()).limit(60).all()
    return templates.TemplateResponse(request, "recaps.html", {"rows": rows})


@router.get("/recap/{recap_date}", response_class=HTMLResponse)
def recap_detail(
    request: Request,
    recap_date: date,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    row = db.query(DailyRecap).filter(DailyRecap.recap_date == recap_date).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return templates.TemplateResponse(request, "recap.html", {"row": row})


@router.post("/recap/{recap_date}/retry")
def recap_retry(
    recap_date: date,
    svc: RecapService = Depends(get_recap_service),
    _: None = Depends(require_auth),
):
    svc.generate(recap_date)
    return RedirectResponse(url=f"/recap/{recap_date}", status_code=303)
