from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from marketpulse.ai.service import AiService
from marketpulse.data.service import DataService
from marketpulse.db.models import Holding, Trade, WatchlistItem
from marketpulse.logging import get_logger
from marketpulse.recap.signals import (
    bollinger_series,
    ema,
    macd,
    rsi_series,
    scan_signal_markers,
    sma,
)
from marketpulse.web.deps import (
    get_ai_service,
    get_data_service,
    get_db,
    require_auth,
)
from marketpulse.web.main import templates

router = APIRouter()
log = get_logger(__name__)

_VALID_PERIODS = {"30d", "60d", "6m", "1y"}
_PERIOD_DAYS = {"30d": 30, "60d": 60, "6m": 180, "1y": 365}


@router.get("/stock/{ticker}", response_class=HTMLResponse)
def stock_page(
    request: Request,
    ticker: str,
    data: DataService = Depends(get_data_service),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    ticker = ticker.upper()
    try:
        quote = data.get_quote(ticker)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # History and news degrade gracefully — page renders without them on failure.
    try:
        bars = data.get_history(ticker, period="60d")
    except Exception as exc:
        log.warning("stock_page_history_failed", ticker=ticker, error=str(exc))
        bars = []
    try:
        news = data.get_news(ticker, limit=5)
    except Exception as exc:
        log.warning("stock_page_news_failed", ticker=ticker, error=str(exc))
        news = []

    holding = db.query(Holding).filter(Holding.ticker == ticker).one_or_none()
    in_watchlist = db.query(WatchlistItem).filter(
        WatchlistItem.ticker == ticker,
    ).one_or_none() is not None
    # Sort by coalesce(executed_at, created_at) so trades with NULL
    # executed_at (entered before PR #8's form fix) appear in their proper
    # chronological position by insert time, not at the bottom of the list.
    recent_trades = (
        db.query(Trade)
        .filter(Trade.ticker == ticker)
        .order_by(func.coalesce(Trade.executed_at, Trade.created_at).desc())
        .limit(5)
        .all()
    )
    return templates.TemplateResponse(
        request, "stock.html",
        {
            "ticker": ticker,
            "quote": quote,
            "bars": bars,
            "news": news,
            "holding": holding,
            "in_watchlist": in_watchlist,
            "recent_trades": recent_trades,
        },
    )


@router.get("/stock/{ticker}/chart-data")
def stock_chart_data(
    ticker: str,
    period: str = Query("60d"),
    data: DataService = Depends(get_data_service),
    _: None = Depends(require_auth),
):
    if period not in _VALID_PERIODS:
        raise HTTPException(
            status_code=422,
            detail=f"period must be one of {sorted(_VALID_PERIODS)}",
        )
    ticker = ticker.upper()
    # Always fetch 1y so we have SMA200 headroom regardless of visible period.
    try:
        all_bars = data.get_history(ticker, period="1y")
    except Exception as exc:
        log.warning("chart_data_history_failed", ticker=ticker, error=str(exc))
        all_bars = []

    cutoff = date.today() - timedelta(days=_PERIOD_DAYS[period])

    if not all_bars:
        empty: list = []
        payload = {
            "bars": empty, "ema12": empty, "ema26": empty,
            "sma50": empty, "sma200": empty,
            "bb_upper": empty, "bb_middle": empty, "bb_lower": empty,
            "rsi": empty,
            "macd": {"line": empty, "signal": empty, "histogram": empty},
            "signal_markers": empty,
        }
        return JSONResponse(payload, headers={"Cache-Control": "private, max-age=300"})

    closes = [b.close for b in all_bars]

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200)
    bb_upper, bb_middle, bb_lower = bollinger_series(closes)
    rsi = rsi_series(closes)
    macd_line, macd_signal, macd_hist = macd(closes)
    markers = scan_signal_markers(all_bars)

    def series_after(bars, series):
        out = []
        for b, v in zip(bars, series, strict=True):
            if b.date < cutoff:
                continue
            out.append({"time": b.date.isoformat(), "value": v})
        return out

    visible_bars = [b for b in all_bars if b.date >= cutoff]
    payload = {
        "bars": [
            {"time": b.date.isoformat(), "open": b.open, "high": b.high,
             "low": b.low, "close": b.close, "volume": b.volume}
            for b in visible_bars
        ],
        "ema12": series_after(all_bars, ema12),
        "ema26": series_after(all_bars, ema26),
        "sma50": series_after(all_bars, sma50),
        "sma200": series_after(all_bars, sma200),
        "bb_upper": series_after(all_bars, bb_upper),
        "bb_middle": series_after(all_bars, bb_middle),
        "bb_lower": series_after(all_bars, bb_lower),
        "rsi": series_after(all_bars, rsi),
        "macd": {
            "line": series_after(all_bars, macd_line),
            "signal": series_after(all_bars, macd_signal),
            "histogram": series_after(all_bars, macd_hist),
        },
        "signal_markers": [m for m in markers if m["time"] >= cutoff.isoformat()],
    }
    return JSONResponse(payload, headers={"Cache-Control": "private, max-age=300"})


@router.post("/stock/{ticker}/analyze", response_class=HTMLResponse)
def stock_analyze(
    request: Request,
    ticker: str,
    ai: AiService = Depends(get_ai_service),
    _: None = Depends(require_auth),
):
    ticker = ticker.upper()
    try:
        result = ai.analyze(ticker)
    except Exception as exc:  # noqa: BLE001 — surface any failure as recoverable UI error
        log.warning("analyze_failed", ticker=ticker, error=str(exc))
        return templates.TemplateResponse(
            request, "partials/analysis_error.html",
            {"ticker": ticker, "error": str(exc)},
            status_code=200,
        )
    return templates.TemplateResponse(
        request, "partials/analysis_block.html",
        {"ticker": ticker, "result": result},
    )
