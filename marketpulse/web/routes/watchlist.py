import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from marketpulse.db.models import WatchlistItem
from marketpulse.web.deps import get_db, require_auth
from marketpulse.web.main import templates
from marketpulse.web.watchlist_view import build_watchlist_view

router = APIRouter()

_TICKER_RE = re.compile(r"^[A-Z\^][A-Z0-9.\-]{0,9}$")


@router.get("/watchlist", response_class=HTMLResponse)
def watchlist_page(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    view = build_watchlist_view(db)
    return templates.TemplateResponse(
        request, "watchlist.html", {"view": view, "add_result": None})


def _parse_tickers(raw: str) -> list[str]:
    parts = raw.replace(",", "\n").split("\n")
    seen, out = set(), []
    for p in parts:
        t = p.strip().upper()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


@router.post("/watchlist", response_class=HTMLResponse)
def watchlist_add(
    request: Request,
    tickers: str = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    existing = {t for (t,) in db.query(WatchlistItem.ticker).all()}
    added, already, invalid = [], [], []
    for t in _parse_tickers(tickers):
        if not _TICKER_RE.match(t):
            invalid.append(t)
        elif t in existing:
            already.append(t)
        else:
            db.add(WatchlistItem(ticker=t))
            existing.add(t)
            added.append(t)
    db.commit()
    parts = [f"added {len(added)}"]
    if already:
        parts.append(f"{len(already)} already present")
    if invalid:
        parts.append(f"{len(invalid)} invalid: {', '.join(invalid)}")
    view = build_watchlist_view(db)
    return templates.TemplateResponse(
        request, "partials/watchlist_grid.html",
        {"view": view, "add_result": " · ".join(parts)})


@router.delete("/watchlist/{item_id}", response_class=HTMLResponse)
def watchlist_delete(
    request: Request,
    item_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    item = db.get(WatchlistItem, item_id)
    if item is not None:
        db.delete(item)
        db.commit()
    view = build_watchlist_view(db)
    return templates.TemplateResponse(
        request, "partials/watchlist_grid.html",
        {"view": view, "add_result": None})
