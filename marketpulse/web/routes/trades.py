import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from marketpulse.db.models import Trade
from marketpulse.holdings.trades import TradeError, record_trade, total_realized_pl
from marketpulse.logging import get_logger
from marketpulse.web.deps import get_db, require_auth
from marketpulse.web.main import templates

router = APIRouter()
log = get_logger(__name__)

_TICKER_RE = re.compile(r"^[A-Z\^][A-Z0-9.\-]{0,15}$")


@router.get("/trades", response_class=HTMLResponse)
def trades_page(
    request: Request,
    ticker: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    q = db.query(Trade).order_by(Trade.created_at.desc())
    if ticker:
        q = q.filter(Trade.ticker == ticker.upper())
    trades = q.limit(200).all()
    return templates.TemplateResponse(
        request,
        "trades.html",
        {
            "trades": trades,
            "filter_ticker": ticker.upper() if ticker else None,
            "realized_pl_total": total_realized_pl(db, ticker=ticker),
        },
    )


@router.post("/trades", response_class=HTMLResponse)
def trades_add(
    request: Request,
    ticker: str = Form(...),
    action: str = Form(...),
    quantity: float = Form(...),
    price: float = Form(...),
    fees: float = Form(0.0),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    normalized = ticker.strip().upper()
    if not _TICKER_RE.match(normalized):
        raise HTTPException(status_code=422, detail="invalid ticker")
    try:
        record_trade(
            db,
            ticker=normalized,
            action=action,
            quantity=quantity,
            price=price,
            fees=fees,
            notes=notes or None,
        )
    except TradeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Re-render the full trades table so totals + new row both refresh.
    trades = (
        db.query(Trade)
        .filter(Trade.ticker == normalized)
        .order_by(Trade.created_at.desc())
        .limit(200)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "partials/trades_table.html",
        {
            "trades": db.query(Trade).order_by(Trade.created_at.desc()).limit(200).all(),
            "realized_pl_total": total_realized_pl(db),
            "filter_ticker": None,
            "_just_added": trades[0] if trades else None,
        },
    )
