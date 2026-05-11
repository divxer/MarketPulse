import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from marketpulse.data.service import DataService
from marketpulse.db.models import Holding
from marketpulse.holdings.service import compute_totals, enrich_holdings
from marketpulse.logging import get_logger
from marketpulse.web.deps import get_data_service, get_db, require_auth
from marketpulse.web.main import templates

router = APIRouter()
log = get_logger(__name__)

_TICKER_RE = re.compile(r"^[A-Z\^][A-Z0-9.\-]{0,15}$")


@router.get("/holdings", response_class=HTMLResponse)
def holdings_page(
    request: Request,
    db: Session = Depends(get_db),
    data: DataService = Depends(get_data_service),
    _: None = Depends(require_auth),
):
    holdings = db.query(Holding).order_by(Holding.sort_order, Holding.id).all()
    rows = enrich_holdings(holdings, data)
    return templates.TemplateResponse(
        request, "holdings.html", {"rows": rows, "totals": compute_totals(rows)},
    )


@router.post("/holdings", response_class=HTMLResponse)
def holdings_add(
    request: Request,
    ticker: str = Form(...),
    quantity: float = Form(...),
    avg_cost: float = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    data: DataService = Depends(get_data_service),
    _: None = Depends(require_auth),
):
    normalized = ticker.strip().upper()
    if not _TICKER_RE.match(normalized):
        raise HTTPException(status_code=422, detail="invalid ticker")
    if quantity <= 0 or avg_cost <= 0:
        raise HTTPException(status_code=422, detail="quantity and avg_cost must be positive")
    existing = db.query(Holding).filter(Holding.ticker == normalized).one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"{normalized} already held — edit or delete the existing row",
        )
    h = Holding(
        ticker=normalized,
        quantity=quantity,
        avg_cost=avg_cost,
        notes=notes or None,
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    row = enrich_holdings([h], data)[0]
    return templates.TemplateResponse(
        request, "partials/holding_row.html", {"row": row},
    )


@router.post("/holdings/{item_id}/update", response_class=HTMLResponse)
def holdings_update(
    request: Request,
    item_id: int,
    quantity: float = Form(...),
    avg_cost: float = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    data: DataService = Depends(get_data_service),
    _: None = Depends(require_auth),
):
    if quantity <= 0 or avg_cost <= 0:
        raise HTTPException(status_code=422, detail="quantity and avg_cost must be positive")
    h = db.query(Holding).filter(Holding.id == item_id).one_or_none()
    if not h:
        raise HTTPException(status_code=404, detail="not found")
    h.quantity = quantity
    h.avg_cost = avg_cost
    h.notes = notes or None
    db.commit()
    db.refresh(h)
    row = enrich_holdings([h], data)[0]
    return templates.TemplateResponse(
        request, "partials/holding_row.html", {"row": row},
    )


@router.delete("/holdings/{item_id}", response_class=HTMLResponse)
def holdings_delete(
    item_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    db.query(Holding).filter(Holding.id == item_id).delete()
    db.commit()
    return HTMLResponse("")
