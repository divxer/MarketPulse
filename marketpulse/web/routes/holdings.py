import re
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from marketpulse.data.service import DataService
from marketpulse.db.models import Holding
from marketpulse.logging import get_logger
from marketpulse.web.deps import get_data_service, get_db, require_auth
from marketpulse.web.main import templates

router = APIRouter()
log = get_logger(__name__)

_TICKER_RE = re.compile(r"^[A-Z\^][A-Z0-9.\-]{0,15}$")


def _enrich_holdings(holdings: list[Holding], data: DataService) -> list[dict[str, Any]]:
    """Attach live quote + computed P&L to each holding. Live fetch failures
    are tolerated — the row still renders with cost-basis info."""
    rows: list[dict[str, Any]] = []
    for h in holdings:
        cost_basis = h.quantity * h.avg_cost
        row: dict[str, Any] = {
            "id": h.id,
            "ticker": h.ticker,
            "quantity": h.quantity,
            "avg_cost": h.avg_cost,
            "notes": h.notes,
            "cost_basis": cost_basis,
            "current_price": None,
            "market_value": None,
            "pl_dollars": None,
            "pl_pct": None,
            "stale": False,
        }
        try:
            q = data.get_quote(h.ticker)
            row["current_price"] = q.price
            row["market_value"] = h.quantity * q.price
            row["pl_dollars"] = row["market_value"] - cost_basis
            row["pl_pct"] = (q.price - h.avg_cost) / h.avg_cost * 100 if h.avg_cost else 0
            row["stale"] = q.stale
        except Exception as exc:
            log.warning("holding_quote_failed", ticker=h.ticker, error=str(exc))
        rows.append(row)
    return rows


def _totals(rows: list[dict[str, Any]]) -> dict[str, float]:
    cost = sum(r["cost_basis"] for r in rows)
    mv = sum(r["market_value"] for r in rows if r["market_value"] is not None)
    pl = mv - cost if cost > 0 else 0
    pl_pct = pl / cost * 100 if cost > 0 else 0
    return {"cost": cost, "market_value": mv, "pl_dollars": pl, "pl_pct": pl_pct}


@router.get("/holdings", response_class=HTMLResponse)
def holdings_page(
    request: Request,
    db: Session = Depends(get_db),
    data: DataService = Depends(get_data_service),
    _: None = Depends(require_auth),
):
    holdings = db.query(Holding).order_by(Holding.sort_order, Holding.id).all()
    rows = _enrich_holdings(holdings, data)
    return templates.TemplateResponse(
        request, "holdings.html", {"rows": rows, "totals": _totals(rows)},
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
    row = _enrich_holdings([h], data)[0]
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
    row = _enrich_holdings([h], data)[0]
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
