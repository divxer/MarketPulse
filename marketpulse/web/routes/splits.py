from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from marketpulse.db.models import StockSplit
from marketpulse.holdings.splits import SplitError, delete_split, record_split
from marketpulse.holdings.trades import recompute_ticker
from marketpulse.logging import get_logger
from marketpulse.web.deps import get_db, require_auth

router = APIRouter()
log = get_logger(__name__)


def _serialize(s: StockSplit) -> dict:
    return {
        "id": s.id,
        "ticker": s.ticker,
        "ex_date": s.ex_date.isoformat(),
        "ratio": s.ratio,
        "source": s.source,
        "notes": s.notes,
    }


@router.post("/splits")
def splits_create(
    ticker: str = Form(...),
    ex_date: str = Form(...),
    ratio: float = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """Record a stock split, then recompute the affected ticker so the
    Holding row reflects the new share count and avg_cost immediately.
    """
    try:
        ex_dt = datetime.strptime(ex_date.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid ex_date: {exc}") from exc
    try:
        s = record_split(
            db, ticker=ticker, ex_date=ex_dt, ratio=ratio,
            source="manual", notes=notes or None,
        )
    except SplitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    recompute_ticker(db, s.ticker)
    return JSONResponse(_serialize(s))


@router.get("/splits")
def splits_list(
    ticker: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    q = db.query(StockSplit).order_by(StockSplit.ex_date.desc())
    if ticker:
        q = q.filter(StockSplit.ticker == ticker.upper())
    return JSONResponse([_serialize(s) for s in q.all()])


@router.delete("/splits/{split_id}", response_class=HTMLResponse)
def splits_delete(
    split_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    try:
        ticker = delete_split(db, split_id)
    except SplitError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    recompute_ticker(db, ticker)
    return HTMLResponse("")
