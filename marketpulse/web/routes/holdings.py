from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from marketpulse.ai.service import AiService
from marketpulse.data.service import DataService
from marketpulse.db.models import Holding
from marketpulse.holdings.dividends import (
    DividendError,
    monthly_dividends,
    per_ticker_dividends,
    record_dividend,
    total_dividends,
)
from marketpulse.holdings.service import (
    allocation_breakdown,
    compute_totals,
    enrich_holdings,
    monthly_realized_pl,
    sort_by_pl_impact,
    trading_stats,
)
from marketpulse.holdings.trades import total_realized_pl
from marketpulse.logging import get_logger
from marketpulse.web.deps import (
    get_ai_service,
    get_data_service,
    get_db,
    require_auth,
)
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
    totals = compute_totals(rows)
    realized = total_realized_pl(db)
    dividends_by_ticker = per_ticker_dividends(db)
    # Attach per-ticker dividend total to each enriched row so the table can
    # show it inline without a second query loop.
    for r in rows:
        r["dividends_received"] = dividends_by_ticker.get(r["ticker"], 0.0)
    return templates.TemplateResponse(
        request,
        "holdings.html",
        {
            "rows": rows,
            "ranked_rows": sort_by_pl_impact(rows),
            "totals": totals,
            "realized_pl": realized,
            "total_dividends": total_dividends(db),
            "allocation": allocation_breakdown(rows),
            "monthly_pl": monthly_realized_pl(db),
            "monthly_dividends": monthly_dividends(db),
            "trade_stats": trading_stats(db),
        },
    )


@router.post("/dividends", response_class=HTMLResponse)
def dividends_create(
    request: Request,
    ticker: str = Form(...),
    ex_date: str = Form(...),
    amount_per_share: float = Form(...),
    total_amount: float = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """Record a cash dividend. Used by the import script and (future) a UI form."""
    try:
        ex_dt = datetime.strptime(ex_date.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid ex_date: {exc}") from exc
    try:
        d = record_dividend(
            db,
            ticker=ticker,
            ex_date=ex_dt,
            amount_per_share=amount_per_share,
            total_amount=total_amount,
            notes=notes or None,
        )
    except DividendError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse({
        "id": d.id, "ticker": d.ticker, "ex_date": d.ex_date.isoformat(),
        "total_amount": d.total_amount,
    })


@router.get("/dividends")
def dividends_list(
    ticker: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """List all dividends, optionally filtered by ticker. Useful for the
    import script to check what's already recorded before reposting."""
    from marketpulse.db.models import Dividend
    q = db.query(Dividend).order_by(Dividend.ex_date.desc())
    if ticker:
        q = q.filter(Dividend.ticker == ticker.upper())
    rows = q.all()
    return JSONResponse([
        {"id": d.id, "ticker": d.ticker, "ex_date": d.ex_date.isoformat(),
         "amount_per_share": d.amount_per_share, "total_amount": d.total_amount}
        for d in rows
    ])


@router.delete("/dividends/{div_id}", response_class=HTMLResponse)
def dividends_delete(
    request: Request,
    div_id: int,
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
    """Delete a dividend row. Re-renders /trades table partial."""
    from marketpulse.db.models import Dividend
    div = db.query(Dividend).filter(Dividend.id == div_id).one_or_none()
    if not div:
        raise HTTPException(status_code=404, detail="dividend not found")
    db.delete(div)
    db.commit()

    from marketpulse.web.routes.trades import _build_trades_ctx, _parse_date_or_422
    fd = _parse_date_or_422(from_, "from")
    td = _parse_date_or_422(to, "to")
    q_clean = (q.strip() if q else None) or None
    ctx = _build_trades_ctx(
        db, page=page, limit=limit,
        from_date=fd, to_date=td,
        q=q_clean, ticker_alias=ticker, event_type=event_type,
    )
    return templates.TemplateResponse(request, "partials/trades_table.html", ctx)


@router.get("/holdings/risk-analysis", response_class=HTMLResponse)
def holdings_risk_analysis(
    request: Request,
    db: Session = Depends(get_db),
    data: DataService = Depends(get_data_service),
    ai: AiService = Depends(get_ai_service),
    _: None = Depends(require_auth),
):
    """HTMX endpoint: AI risk analysis card.

    Called by hx-trigger='load' on the placeholder in /holdings.
    Always returns 200 — even on Anthropic failure, renders a fallback
    card so HTMX swaps cleanly.
    """
    holdings = db.query(Holding).order_by(Holding.sort_order, Holding.id).all()
    if not holdings:
        return templates.TemplateResponse(
            request, "partials/holdings_risk_card.html",
            {"analysis_markdown": "暂无持仓,无需风险分析。", "generated_at": None},
        )

    rows = enrich_holdings(holdings, data)
    totals = compute_totals(rows)
    allocation = allocation_breakdown(rows)
    realized = total_realized_pl(db)
    stats = trading_stats(db)

    # Strip the non-JSON-serializable bits + heavy fields the AI doesn't need
    holdings_payload = [
        {k: r[k] for k in ("ticker", "quantity", "avg_cost", "current_price",
                            "market_value", "pl_dollars", "pl_pct") if k in r}
        for r in rows
    ]
    try:
        analysis_markdown = ai.portfolio_risk(
            holdings=holdings_payload,
            totals=totals,
            allocation=allocation,
            realized_pl=realized,
            trading_stats=stats,
        )
    except Exception as exc:  # noqa: BLE001 — surface any failure as recoverable UI error
        log.warning("portfolio_risk_failed", error=str(exc))
        analysis_markdown = "**AI 服务暂时不可用,请稍后重试。**"

    return templates.TemplateResponse(
        request, "partials/holdings_risk_card.html",
        {"analysis_markdown": analysis_markdown, "generated_at": None},
    )


@router.get("/holdings/export.csv")
def holdings_export_csv(
    db: Session = Depends(get_db),
    data: DataService = Depends(get_data_service),
    _: None = Depends(require_auth),
):
    """Streaming CSV export of current holdings.

    Format columns: ticker, name, sector, quantity, avg_cost,
    current_price, market_value, cost_basis, unrealized_pl,
    unrealized_pl_pct, dividends_received

    Uses StreamingResponse to avoid buffering large portfolios in memory.
    """
    from datetime import UTC, datetime

    from fastapi.responses import StreamingResponse

    HEADER = [
        "ticker", "name", "sector", "quantity", "avg_cost",
        "current_price", "market_value", "cost_basis",
        "unrealized_pl", "unrealized_pl_pct", "dividends_received",
    ]

    def _gen():
        yield ",".join(HEADER) + "\n"
        holdings = db.query(Holding).order_by(Holding.sort_order, Holding.id).all()
        if not holdings:
            return
        rows = enrich_holdings(holdings, data)
        divs_by_ticker = per_ticker_dividends(db)
        for r in rows:
            divs = divs_by_ticker.get(r["ticker"], 0.0)
            current_price = r.get("current_price")
            market_value = r.get("market_value")
            pl_dollars = r.get("pl_dollars") or 0
            pl_pct = r.get("pl_pct") or 0
            yield (
                f'{r["ticker"]},'
                f'{r["ticker"]},'  # name = ticker placeholder (Quote has no name field)
                f'{r["sector"]},'
                f'{r["quantity"]:g},'
                f'{r["avg_cost"]:.4f},'
                f'{current_price if current_price is not None else ""},'
                f'{market_value if market_value is not None else ""},'
                f'{r["cost_basis"]:.2f},'
                f'{pl_dollars:.2f},'
                f'{pl_pct:.4f},'
                f'{divs:.2f}\n'
            )

    filename = f"holdings-{datetime.now(UTC).date().isoformat()}.csv"
    return StreamingResponse(
        _gen(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
