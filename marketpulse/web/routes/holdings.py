from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
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
    div_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """Delete a dividend row. Used for cleanup / re-imports."""
    from marketpulse.db.models import Dividend
    div = db.query(Dividend).filter(Dividend.id == div_id).one_or_none()
    if not div:
        raise HTTPException(status_code=404, detail="dividend not found")
    db.delete(div)
    db.commit()
    return HTMLResponse("")


@router.post("/holdings/risk-analysis", response_class=HTMLResponse)
def holdings_risk_analysis(
    request: Request,
    db: Session = Depends(get_db),
    data: DataService = Depends(get_data_service),
    ai: AiService = Depends(get_ai_service),
    _: None = Depends(require_auth),
):
    """On-demand AI portfolio risk analysis. Returns rendered Markdown HTML."""
    holdings = db.query(Holding).order_by(Holding.sort_order, Holding.id).all()
    if not holdings:
        return templates.TemplateResponse(
            request, "partials/risk_analysis.html",
            {"markdown": "暂无持仓,无需风险分析。", "error": None},
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
        result = ai.portfolio_risk(
            holdings=holdings_payload,
            totals=totals,
            allocation=allocation,
            realized_pl=realized,
            trading_stats=stats,
        )
    except Exception as exc:  # noqa: BLE001 — surface any failure as recoverable UI error
        log.warning("portfolio_risk_failed", error=str(exc))
        return templates.TemplateResponse(
            request, "partials/risk_analysis.html",
            {"markdown": None, "error": str(exc)},
        )
    return templates.TemplateResponse(
        request, "partials/risk_analysis.html",
        {"markdown": result, "error": None},
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
