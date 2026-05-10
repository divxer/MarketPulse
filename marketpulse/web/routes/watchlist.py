import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from marketpulse.db.models import WatchlistItem
from marketpulse.web.deps import get_db, require_auth
from marketpulse.web.main import templates

router = APIRouter()

_TICKER_RE = re.compile(r"^[A-Z\^][A-Z0-9.\-]{0,9}$")


@router.get("/watchlist", response_class=HTMLResponse)
def watchlist_page(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    items = db.query(WatchlistItem).order_by(WatchlistItem.sort_order, WatchlistItem.id).all()
    return templates.TemplateResponse(request, "watchlist.html", {"items": items})


@router.post("/watchlist", response_class=HTMLResponse)
def watchlist_add(
    request: Request,
    ticker: str = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    normalized = ticker.strip().upper()
    if not _TICKER_RE.match(normalized):
        raise HTTPException(status_code=422, detail="invalid ticker")
    existing = db.query(WatchlistItem).filter(WatchlistItem.ticker == normalized).one_or_none()
    if existing:
        item = existing
    else:
        item = WatchlistItem(ticker=normalized)
        db.add(item)
        db.commit()
        db.refresh(item)
    return templates.TemplateResponse(request, "partials/watchlist_row.html", {"item": item})


@router.delete("/watchlist/{item_id}", response_class=HTMLResponse)
def watchlist_delete(
    item_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    db.query(WatchlistItem).filter(WatchlistItem.id == item_id).delete()
    db.commit()
    return HTMLResponse("")
