import re
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from marketpulse.db.models import Dividend, StockSplit, Trade
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


def _parse_executed_at(
    executed_at: str,
    tz_offset_minutes: int,
    original_iso: str = "",
) -> datetime:
    """Resolve form `executed_at` to a UTC datetime.

    Priority:
    1. Preserve-original: if `original_iso` is provided AND its user-local
       date (per tz_offset_minutes) equals the form's YYYY-MM-DD string,
       the trade is being edited without a date change — return the
       original full timestamp byte-for-byte. Sub-second precision intact.
    2. Blank → datetime.now(UTC).
    3. YYYY-MM-DD → combine with user's current local clock time → UTC.
    4. Full ISO 8601 → parse as-is; naive treated as UTC.

    `tz_offset_minutes` follows JS Date.getTimezoneOffset() convention:
    Beijing (UTC+8) → -480. Formula: utc_naive = local_naive + offset.
    """
    s = executed_at.strip()
    orig = original_iso.strip()

    # Priority 1: preserve-original
    if orig:
        try:
            orig_dt = datetime.fromisoformat(orig.replace("Z", "+00:00"))
            if orig_dt.tzinfo is None:
                orig_dt = orig_dt.replace(tzinfo=UTC)
            orig_local = orig_dt + timedelta(minutes=-tz_offset_minutes)
            if s and len(s) == 10 and orig_local.date().isoformat() == s:
                return orig_dt
        except ValueError:
            pass  # bad original_iso → fall through to normal parsing

    # Priority 2: blank
    if not s:
        return datetime.now(UTC)

    try:
        # Priority 3: YYYY-MM-DD
        if len(s) == 10:
            local_date = date.fromisoformat(s)
            now_utc_naive = datetime.now(UTC).replace(tzinfo=None)
            now_local_naive = now_utc_naive + timedelta(minutes=-tz_offset_minutes)
            local_dt_naive = datetime.combine(local_date, now_local_naive.time())
            return (
                local_dt_naive + timedelta(minutes=tz_offset_minutes)
            ).replace(tzinfo=UTC)
        # Priority 4: full ISO 8601
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"invalid executed_at: {exc}",
        ) from exc


@router.get("/trades", response_class=HTMLResponse)
def trades_page(
    request: Request,
    ticker: str | None = None,
    event_type: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """Unified timeline of Trade + StockSplit + Dividend events.

    `event_type` filter values: "trade" | "split" | "dividend" | None (all).
    """
    tnorm = ticker.upper() if ticker else None
    events: list[dict] = []

    if event_type in (None, "trade"):
        tq = db.query(Trade)
        if tnorm:
            tq = tq.filter(Trade.ticker == tnorm)
        for t in tq.all():
            when = t.executed_at or t.created_at
            events.append({"kind": "trade", "when": when, "obj": t})

    _EOD = time(23, 59, 59, tzinfo=UTC)
    if event_type in (None, "split"):
        sq = db.query(StockSplit)
        if tnorm:
            sq = sq.filter(StockSplit.ticker == tnorm)
        for s in sq.all():
            events.append({
                "kind": "split",
                "when": datetime.combine(s.ex_date, _EOD),
                "obj": s,
            })

    if event_type in (None, "dividend"):
        dq = db.query(Dividend)
        if tnorm:
            dq = dq.filter(Dividend.ticker == tnorm)
        for d in dq.all():
            events.append({
                "kind": "dividend",
                "when": datetime.combine(d.ex_date, _EOD),
                "obj": d,
            })

    # Newest first, capped at 200.
    events.sort(key=lambda e: e["when"], reverse=True)
    events = events[:200]

    return templates.TemplateResponse(
        request,
        "trades.html",
        {
            "events": events,
            "filter_ticker": tnorm,
            "filter_event_type": event_type,
            "realized_pl_total": total_realized_pl(db, ticker=tnorm),
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
    tz_offset_minutes: int = Form(0),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    normalized = ticker.strip().upper()
    if not _TICKER_RE.match(normalized):
        raise HTTPException(status_code=422, detail="invalid ticker")

    executed_at_dt = _parse_executed_at(executed_at, tz_offset_minutes)

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

    # Re-render the full timeline so the new row + totals refresh.
    events: list[dict] = []
    for t in db.query(Trade).all():
        when = t.executed_at or t.created_at
        events.append({"kind": "trade", "when": when, "obj": t})
    _EOD = time(23, 59, 59, tzinfo=UTC)
    for s in db.query(StockSplit).all():
        events.append({"kind": "split", "when": datetime.combine(s.ex_date, _EOD), "obj": s})
    for d in db.query(Dividend).all():
        events.append({"kind": "dividend", "when": datetime.combine(d.ex_date, _EOD), "obj": d})
    events.sort(key=lambda e: e["when"], reverse=True)
    events = events[:200]

    return templates.TemplateResponse(
        request,
        "partials/trades_table.html",
        {
            "events": events,
            "filter_ticker": None,
            "filter_event_type": None,
            "realized_pl_total": total_realized_pl(db),
        },
    )


@router.put("/trades/{trade_id}", response_class=HTMLResponse)
def trades_update(
    request: Request,
    trade_id: int,
    ticker: str = Form(...),
    action: str = Form(...),
    quantity: float = Form(...),
    price: float = Form(...),
    fees: float = Form(0.0),
    notes: str = Form(""),
    executed_at: str = Form(""),
    tz_offset_minutes: int = Form(0),
    original_executed_at_iso: str = Form(""),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """Edit an existing trade. Mutates the row in place, then runs
    recompute_ticker for the affected ticker(s) so Holding + realized_pl
    are rebuilt from the full event history.

    Validation mirrors trades_add (POST). If the ticker changes, both
    the old and new ticker are recomputed."""
    trade = db.query(Trade).filter(Trade.id == trade_id).one_or_none()
    if not trade:
        raise HTTPException(status_code=404, detail="trade not found")

    normalized = ticker.strip().upper()
    if not _TICKER_RE.match(normalized):
        raise HTTPException(status_code=422, detail="invalid ticker")

    action_norm = action.lower().strip()
    if action_norm not in ("buy", "sell"):
        raise HTTPException(
            status_code=422,
            detail=f"invalid action {action!r}, must be 'buy' or 'sell'",
        )
    if quantity <= 0:
        raise HTTPException(status_code=422, detail="quantity must be positive")
    if price < 0:
        raise HTTPException(status_code=422, detail="price cannot be negative")
    if fees < 0:
        raise HTTPException(status_code=422, detail="fees cannot be negative")

    executed_at_dt = _parse_executed_at(
        executed_at, tz_offset_minutes, original_executed_at_iso,
    )

    old_ticker = trade.ticker

    trade.ticker = normalized
    trade.action = action_norm
    trade.quantity = quantity
    trade.price = price
    trade.fees = fees
    trade.executed_at = executed_at_dt
    trade.notes = notes or None
    db.commit()

    # Recompute the old ticker first (so its Holding reflects the removal
    # of this trade), then the new ticker (to apply the trade there).
    # When ticker is unchanged, the second call is a no-op (same ticker).
    recompute_ticker(db, old_ticker)
    if normalized != old_ticker:
        recompute_ticker(db, normalized)

    # Re-render the timeline (same logic as trades_add).
    events: list[dict] = []
    for t in db.query(Trade).all():
        when = t.executed_at or t.created_at
        events.append({"kind": "trade", "when": when, "obj": t})
    _EOD = time(23, 59, 59, tzinfo=UTC)
    for sp in db.query(StockSplit).all():
        events.append({"kind": "split", "when": datetime.combine(sp.ex_date, _EOD), "obj": sp})
    for d in db.query(Dividend).all():
        events.append({"kind": "dividend", "when": datetime.combine(d.ex_date, _EOD), "obj": d})
    events.sort(key=lambda e: e["when"], reverse=True)
    events = events[:200]

    return templates.TemplateResponse(
        request,
        "partials/trades_table.html",
        {
            "events": events,
            "filter_ticker": None,
            "filter_event_type": None,
            "realized_pl_total": total_realized_pl(db),
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
