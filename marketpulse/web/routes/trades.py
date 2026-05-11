import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

# Sort key: real trade time (executed_at) when present, fallback to record time.
# Defined once so all trade-listing endpoints stay consistent.
def _trade_sort_key():
    return func.coalesce(Trade.executed_at, Trade.created_at).desc()

from marketpulse.db.models import Trade
from marketpulse.holdings.robinhood_import import (
    ParsedTrade,
    RobinhoodParseError,
    parse_robinhood_csv,
)
from marketpulse.holdings.trades import (
    TradeError,
    recompute_ticker,
    record_trade,
    total_realized_pl,
)
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
    q = db.query(Trade).order_by(_trade_sort_key())
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
    executed_at: str = Form(""),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    normalized = ticker.strip().upper()
    if not _TICKER_RE.match(normalized):
        raise HTTPException(status_code=422, detail="invalid ticker")

    executed_at_dt: datetime | None = None
    if executed_at.strip():
        try:
            # Accept YYYY-MM-DD or full ISO 8601. Naive dates are treated as UTC.
            s = executed_at.strip()
            if len(s) == 10:  # YYYY-MM-DD
                executed_at_dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)
            else:
                executed_at_dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                if executed_at_dt.tzinfo is None:
                    executed_at_dt = executed_at_dt.replace(tzinfo=UTC)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"invalid executed_at: {exc}") from exc

    try:
        record_trade(
            db,
            ticker=normalized,
            action=action,
            quantity=quantity,
            price=price,
            fees=fees,
            executed_at=executed_at_dt,
            notes=notes or None,
        )
    except TradeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Re-render the full trades table so totals + new row both refresh.
    trades = (
        db.query(Trade)
        .filter(Trade.ticker == normalized)
        .order_by(_trade_sort_key())
        .limit(200)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "partials/trades_table.html",
        {
            "trades": db.query(Trade).order_by(_trade_sort_key()).limit(200).all(),
            "realized_pl_total": total_realized_pl(db),
            "filter_ticker": None,
            "_just_added": trades[0] if trades else None,
        },
    )


@router.delete("/trades/{trade_id}", response_class=HTMLResponse)
def trades_delete(
    trade_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    trade = db.query(Trade).filter(Trade.id == trade_id).one_or_none()
    if not trade:
        raise HTTPException(status_code=404, detail="trade not found")
    ticker = trade.ticker
    db.delete(trade)
    db.commit()
    # Recompute Holding + realized_pl on remaining sells for this ticker.
    recompute_ticker(db, ticker)
    return HTMLResponse("")


def _is_duplicate(db: Session, t: ParsedTrade) -> bool:
    """A prior trade with same ticker/action/qty/price executed on the same UTC day."""
    day_start = t.executed_at.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start.replace(hour=23, minute=59, second=59, microsecond=999_999)
    q = (
        db.query(Trade)
        .filter(
            Trade.ticker == t.ticker,
            Trade.action == t.action,
            Trade.quantity == t.quantity,
            Trade.price == t.price,
            Trade.executed_at >= day_start,
            Trade.executed_at <= day_end,
        )
    )
    return db.query(q.exists()).scalar() is True


_MAX_CSV_BYTES = 2 * 1024 * 1024  # 2 MB — covers ~10 years of activity


def _parse_csv_text(text: str) -> list[ParsedTrade]:
    if len(text.encode("utf-8")) > _MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail="CSV 文件过大 (>2MB)")
    try:
        return parse_robinhood_csv(text)
    except RobinhoodParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/trades/import", response_class=HTMLResponse)
def trades_import_page(
    request: Request,
    _: None = Depends(require_auth),
):
    return templates.TemplateResponse(request, "trades_import.html", {})


@router.post("/trades/import", response_class=HTMLResponse)
def trades_import_preview(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    raw = file.file.read()
    text = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, bytes) else raw
    parsed = _parse_csv_text(text)
    new_trades = [t for t in parsed if not _is_duplicate(db, t)]
    skipped = len(parsed) - len(new_trades)
    return templates.TemplateResponse(
        request,
        "trades_import.html",
        {
            "preview": new_trades,
            "skipped": skipped,
            "total_parsed": len(parsed),
            "filename": file.filename,
            "csv_text": text,
        },
    )


@router.post("/trades/import/confirm", response_class=HTMLResponse)
def trades_import_confirm(
    request: Request,
    csv_text: str = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    parsed = _parse_csv_text(csv_text)
    imported = 0
    skipped = 0
    errors: list[str] = []
    for t in parsed:
        if _is_duplicate(db, t):
            skipped += 1
            continue
        try:
            record_trade(
                db,
                ticker=t.ticker,
                action=t.action,
                quantity=t.quantity,
                price=t.price,
                executed_at=t.executed_at,
                notes=f"Robinhood import (row {t.raw_row})",
            )
            imported += 1
        except TradeError as exc:
            errors.append(f"行 {t.raw_row} {t.action} {t.ticker}: {exc}")
            log.warning("import_skip", row=t.raw_row, ticker=t.ticker, error=str(exc))

    return templates.TemplateResponse(
        request,
        "trades_import.html",
        {
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
        },
    )
