import re
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
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
def trades_page(  # noqa: PLR0912, PLR0913, PLR0915
    request: Request,
    page: int = 1,
    limit: int = 50,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    q: str | None = None,
    ticker: str | None = None,
    event_type: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """Phase 5c: paginated + filtered + KPI-decorated trade ledger.

    Query params:
      page, limit  — 1-based pagination (limit clamped to [10,200])
      from, to     — inclusive YYYY-MM-DD window on event date
      q            — ticker prefix search (case-insensitive)
      ticker       — exact ticker match (legacy alias, kept for old links)
      event_type   — trade | split | dividend | None
    """
    from datetime import date as _date
    from urllib.parse import urlencode

    from marketpulse.holdings.service import (
        avg_hold_days,
        monthly_realized_pl,
        realized_pl_by_ticker,
        trade_count_this_month,
        trading_stats,
    )

    # -- parse & validate --
    def _parse_d(s: str | None, name: str):
        if not s:
            return None
        try:
            return _date.fromisoformat(s)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"invalid {name}: {s}") from e

    from_date = _parse_d(from_, "from")
    to_date = _parse_d(to, "to")
    if from_date and to_date and from_date > to_date:
        raise HTTPException(status_code=422, detail="from must be <= to")
    if page <= 0 or limit <= 0:
        raise HTTPException(status_code=422, detail="page and limit must be positive")
    limit = max(10, min(200, limit))

    # Treat q="" as None.
    q = q.strip() if q else None
    if q == "":
        q = None
    q_upper = q.upper() if q else None
    ticker_upper = ticker.upper() if ticker else None

    # -- fetch events with filters --
    events: list[dict] = []
    _EOD = time(23, 59, 59, tzinfo=UTC)

    if event_type in (None, "trade"):
        tq = db.query(Trade)
        if q_upper:
            tq = tq.filter(Trade.ticker.ilike(f"{q_upper}%"))
        if ticker_upper:
            tq = tq.filter(Trade.ticker == ticker_upper)
        for t in tq.all():
            when = t.executed_at or t.created_at
            d = when.date() if when else None
            if from_date and (d is None or d < from_date):
                continue
            if to_date and (d is None or d > to_date):
                continue
            events.append({"kind": "trade", "when": when, "obj": t})

    if event_type in (None, "split"):
        sq = db.query(StockSplit)
        if q_upper:
            sq = sq.filter(StockSplit.ticker.ilike(f"{q_upper}%"))
        if ticker_upper:
            sq = sq.filter(StockSplit.ticker == ticker_upper)
        for s in sq.all():
            if from_date and s.ex_date < from_date:
                continue
            if to_date and s.ex_date > to_date:
                continue
            events.append({
                "kind": "split",
                "when": datetime.combine(s.ex_date, _EOD),
                "obj": s,
            })

    if event_type in (None, "dividend"):
        dq = db.query(Dividend)
        if q_upper:
            dq = dq.filter(Dividend.ticker.ilike(f"{q_upper}%"))
        if ticker_upper:
            dq = dq.filter(Dividend.ticker == ticker_upper)
        for dv in dq.all():
            if from_date and dv.ex_date < from_date:
                continue
            if to_date and dv.ex_date > to_date:
                continue
            events.append({
                "kind": "dividend",
                "when": datetime.combine(dv.ex_date, _EOD),
                "obj": dv,
            })

    events.sort(key=lambda e: e["when"], reverse=True)

    # -- pagination --
    total_count = len(events)
    total_pages = max(1, (total_count + limit - 1) // limit)
    page = min(max(1, page), total_pages)  # clamp
    start = (page - 1) * limit
    page_events = events[start:start + limit]
    pager_window = list(range(max(1, page - 2), min(total_pages, page + 2) + 1))

    # -- counts (4 chip totals; ignore event_type, keep other filters) --
    def _count(kind: str) -> int:
        if kind == "trade":
            base = db.query(Trade)
            col = Trade.ticker
        elif kind == "split":
            base = db.query(StockSplit)
            col = StockSplit.ticker
        else:
            base = db.query(Dividend)
            col = Dividend.ticker
        if q_upper:
            base = base.filter(col.ilike(f"{q_upper}%"))
        if ticker_upper:
            base = base.filter(col == ticker_upper)
        rows = base.all()
        n = 0
        for r in rows:
            if kind == "trade":
                when = r.executed_at or r.created_at
                d = when.date() if when else None
            else:
                d = r.ex_date
            if d is None:
                continue
            if from_date and d < from_date:
                continue
            if to_date and d > to_date:
                continue
            n += 1
        return n

    counts = {
        "trade": _count("trade"),
        "split": _count("split"),
        "dividend": _count("dividend"),
    }
    counts["all"] = counts["trade"] + counts["split"] + counts["dividend"]

    # -- KPI strip (filter-aware except this_month) --
    today = _date.today()
    kpi_from = from_date or _date(today.year, 1, 1)
    kpi_to = to_date or today
    kpi_label = (
        "YTD" if (from_date is None and to_date is None)
        else f"{kpi_from.isoformat()} → {kpi_to.isoformat()}"
    )
    stats = trading_stats(db, ticker=ticker_upper, from_date=kpi_from, to_date=kpi_to)
    ytd_realized = total_realized_pl(
        db, ticker=ticker_upper, from_date=kpi_from, to_date=kpi_to,
    )
    avg_hd = avg_hold_days(db, from_date=kpi_from, to_date=kpi_to)

    kpi = {
        "total_trades": stats["total_trades"],
        "ytd_realized": ytd_realized,
        "ytd_label": kpi_label,
        "win_rate_pct": stats["win_rate_pct"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "avg_hold_days": avg_hd,
        "this_month": trade_count_this_month(db),
    }

    # -- right rail (always all-time per spec decision 4) --
    monthly = monthly_realized_pl(db, months=15)
    by_ticker_rows = realized_pl_by_ticker(db, top_n=8)

    # -- filters query string --
    filters_dict = {
        "from": from_, "to": to, "q": q, "event_type": event_type,
    }
    filters_qs = urlencode({k: v for k, v in filters_dict.items() if v})
    filters_qs_no_event_type = urlencode(
        {k: v for k, v in filters_dict.items() if v and k != "event_type"},
    )

    ctx = {
        "events": page_events,
        "page": page, "limit": limit,
        "total_pages": total_pages, "total_count": total_count,
        "pager_window": pager_window,
        "filters": {
            "from": from_ or None, "to": to or None,
            "q": q, "event_type": event_type,
        },
        "filters_qs": filters_qs,
        "filters_qs_no_event_type": filters_qs_no_event_type,
        "counts": counts,
        "kpi": kpi,
        "monthly_pl": monthly,
        "by_ticker": by_ticker_rows,
        # legacy keys (some callers may still expect)
        "filter_ticker": ticker_upper,
        "filter_event_type": event_type,
        "realized_pl_total": ytd_realized,
    }

    # HX-Request → return only the table partial
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request, "partials/trades_table.html", ctx,
        )
    return templates.TemplateResponse(request, "trades.html", ctx)


@router.get("/trades/export.csv")
def trades_export_csv(
    request: Request,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    q: str | None = None,
    ticker: str | None = None,
    event_type: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """Streaming Robinhood-format CSV; inherits /trades filters except page/limit."""
    from datetime import date as _date

    from fastapi.responses import StreamingResponse

    def _parse_d(s, name):
        if not s:
            return None
        try:
            return _date.fromisoformat(s)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"invalid {name}: {s}") from e

    from_date = _parse_d(from_, "from")
    to_date = _parse_d(to, "to")
    if from_date and to_date and from_date > to_date:
        raise HTTPException(status_code=422, detail="from must be <= to")
    q = (q or "").strip() or None
    q_upper = q.upper() if q else None
    ticker_upper = ticker.upper() if ticker else None

    HEADER = [
        "Activity Date", "Process Date", "Settle Date",
        "Instrument", "Description", "Trans Code",
        "Quantity", "Price", "Amount",
    ]

    def _gen():
        # Header row.
        yield ",".join(HEADER) + "\n"

        # Trades.
        if event_type in (None, "trade"):
            tq = db.query(Trade)
            if q_upper:
                tq = tq.filter(Trade.ticker.ilike(f"{q_upper}%"))
            if ticker_upper:
                tq = tq.filter(Trade.ticker == ticker_upper)
            for t in tq.order_by(Trade.executed_at.desc().nullslast(), Trade.id.desc()).all():
                when = t.executed_at or t.created_at
                d = when.date() if when else None
                if not _date_in_window_or_all(d, from_date, to_date):
                    continue
                date_s = _format_date_us(d) if d else ""
                amt = t.quantity * t.price
                amt_s = f"(${amt:.2f})" if t.action == "buy" else f"${amt:.2f}"
                yield (
                    f"{date_s},{date_s},{date_s},{t.ticker},,"
                    f"{'Buy' if t.action == 'buy' else 'Sell'},"
                    f"{t.quantity:g},${t.price:.2f},{amt_s}\n"
                )

        # Dividends (Trans Code = CDIV).
        if event_type in (None, "dividend"):
            dq = db.query(Dividend)
            if q_upper:
                dq = dq.filter(Dividend.ticker.ilike(f"{q_upper}%"))
            if ticker_upper:
                dq = dq.filter(Dividend.ticker == ticker_upper)
            for dv in dq.order_by(Dividend.ex_date.desc()).all():
                if not _date_in_window_or_all(dv.ex_date, from_date, to_date):
                    continue
                date_s = _format_date_us(dv.ex_date)
                yield (
                    f"{date_s},{date_s},{date_s},{dv.ticker},Dividend,"
                    f"CDIV,,,${dv.total_amount:.2f}\n"
                )

        # Splits intentionally skipped — Robinhood CSV has no split code.

    filename = f"trades-{datetime.now(UTC).date().isoformat()}.csv"
    return StreamingResponse(
        _gen(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _date_in_window_or_all(d, from_date, to_date):
    """Return True if d is within the optional [from_date, to_date] window.
    None d → False (excluded). No window args → True (included)."""
    if d is None:
        return False
    if from_date and d < from_date:
        return False
    return not (to_date and d > to_date)


def _format_date_us(d):
    """Format a date as M/D/YYYY (Robinhood CSV format, no zero-padding)."""
    return f"{d.month}/{d.day}/{d.year}"


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
    _add_total = len(events)
    _add_limit = 50
    _add_page = 1
    _add_total_pages = max(1, (_add_total + _add_limit - 1) // _add_limit)
    events = events[:_add_limit]

    return templates.TemplateResponse(
        request,
        "partials/trades_table.html",
        {
            "events": events,
            "page": _add_page,
            "limit": _add_limit,
            "total_count": _add_total,
            "total_pages": _add_total_pages,
            "pager_window": list(range(1, min(_add_total_pages, 5) + 1)),
            "filters_qs": "",
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
    _upd_total = len(events)
    _upd_limit = 50
    _upd_page = 1
    _upd_total_pages = max(1, (_upd_total + _upd_limit - 1) // _upd_limit)
    events = events[:_upd_limit]

    return templates.TemplateResponse(
        request,
        "partials/trades_table.html",
        {
            "events": events,
            "page": _upd_page,
            "limit": _upd_limit,
            "total_count": _upd_total,
            "total_pages": _upd_total_pages,
            "pager_window": list(range(1, min(_upd_total_pages, 5) + 1)),
            "filters_qs": "",
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
