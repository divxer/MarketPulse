from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from marketpulse.data.service import DataService
from marketpulse.db.models import Holding
from marketpulse.holdings.service import compute_totals, enrich_holdings
from marketpulse.holdings.trades import total_realized_pl
from marketpulse.logging import get_logger
from marketpulse.web.deps import get_data_service, get_db, require_auth
from marketpulse.web.main import templates

router = APIRouter()
log = get_logger(__name__)


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
        request,
        "holdings.html",
        {
            "rows": rows,
            "totals": compute_totals(rows),
            "realized_pl": total_realized_pl(db),
        },
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
