from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from marketpulse.ai.service import AiService
from marketpulse.data.service import DataService
from marketpulse.data.yfinance_client import YFinanceClient
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

_VALID_PERIODS = {"60d", "6m", "ytd", "1y", "5y", "all"}
_PERIOD_DAYS_FIXED = {"60d": 60, "6m": 180, "1y": 365, "5y": 1825}


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


# 320 calendar days ≈ 228 trading days (after weekends + holidays),
# comfortably above SMA200's 199-bar lookback requirement so the
# indicator is fully populated at every bar of every lazy chunk.
_LOOKBACK_DAYS = 320


@router.get("/stock/{ticker}/chart-data")
def stock_chart_data(
    ticker: str,
    period: str = Query("60d"),
    before: str | None = Query(None),
    count: int = Query(180, ge=1, le=400),
    data: DataService = Depends(get_data_service),
    _: None = Depends(require_auth),
):
    """Two modes:
    - ?period=...                  initial load (Tencent fast path, unchanged)
    - ?before=YYYY-MM-DD&count=N   lazy-load chunk via yfinance, padded with
                                   250-day indicator lookback then trimmed by
                                   date before returning. `before` wins if both
                                   are provided.
    """
    ticker = ticker.upper()

    if before is not None:
        return _chart_data_lazy(ticker, before, count)

    if period not in _VALID_PERIODS:
        raise HTTPException(
            status_code=422,
            detail=f"period must be one of {sorted(_VALID_PERIODS)}",
        )

    # Short periods (≤ 1y) use the fast Tencent path with a 1y cache. Long
    # periods (5y, All) fall through to yfinance which can return arbitrary
    # date ranges — slower but the only way to cover multi-year history.
    if period in {"5y", "all"}:
        from marketpulse.data.yfinance_client import YFinanceClient
        if period == "5y":
            start = date.today() - timedelta(days=_PERIOD_DAYS_FIXED["5y"])
        else:  # "all"
            start = date(1900, 1, 1)
        try:
            all_bars = YFinanceClient().fetch_history_range(
                ticker, start=start, end=date.today(),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "chart_data_long_period_failed", ticker=ticker,
                period=period, error=str(exc),
            )
            all_bars = []
        if not all_bars:
            return JSONResponse(
                _empty_payload(),
                headers={"Cache-Control": "private, max-age=300"},
            )
        return JSONResponse(
            _build_payload(all_bars, cutoff=all_bars[0].date),
            headers={"Cache-Control": "private, max-age=300"},
        )

    # Short periods: Tencent fast path.
    try:
        all_bars = data.get_history(ticker, period="1y")
    except Exception as exc:
        log.warning("chart_data_history_failed", ticker=ticker, error=str(exc))
        all_bars = []

    if period == "ytd":
        cutoff = date(date.today().year, 1, 1)
    else:
        cutoff = date.today() - timedelta(days=_PERIOD_DAYS_FIXED[period])

    if not all_bars:
        return JSONResponse(
            _empty_payload(), headers={"Cache-Control": "private, max-age=300"},
        )

    return JSONResponse(
        _build_payload(all_bars, cutoff=cutoff),
        headers={"Cache-Control": "private, max-age=300"},
    )


def _chart_data_lazy(ticker: str, before_str: str, count: int) -> JSONResponse:
    """Lazy-load path: fetch `count + 320` calendar days ending strictly before
    `before_str` via yfinance, compute indicators over the padded range, then
    trim by date so only the requested `count` window is returned.
    """
    try:
        before_date = date.fromisoformat(before_str)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"invalid before: {exc}",
        ) from exc

    # Pull (count + lookback) calendar days. Trading days are ~70% of calendar
    # days so this is a comfortable upper bound; the trim step below picks
    # exactly `count` trading days (or fewer if ticker history is short).
    fetch_start = before_date - timedelta(days=count + _LOOKBACK_DAYS)
    fetch_end = before_date - timedelta(days=1)  # exclusive of `before`

    try:
        all_bars = YFinanceClient().fetch_history_range(
            ticker, start=fetch_start, end=fetch_end,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "chart_data_lazy_failed", ticker=ticker,
            before=before_str, error=str(exc),
        )
        all_bars = []

    if not all_bars:
        return JSONResponse(_empty_payload())

    # The "window" is the last `count` of the padded bars.
    window_bars = all_bars[-count:]
    window_start = window_bars[0].date

    return JSONResponse(_build_payload(all_bars, cutoff=window_start))


def _empty_payload() -> dict:
    empty: list = []
    return {
        "bars": empty, "ema12": empty, "ema26": empty,
        "sma50": empty, "sma200": empty,
        "bb_upper": empty, "bb_lower": empty,
        "rsi": empty,
        "macd": {"line": empty, "signal": empty, "histogram": empty},
        "signal_markers": empty,
    }


def _build_payload(all_bars: list, cutoff: date) -> dict:
    """Compute indicators over `all_bars`, then trim every series to points
    whose date >= cutoff. Date-based trim (not array index) so leading-null
    indicators (SMA200 has 199 leading nulls) don't misalign when sliced."""
    closes = [b.close for b in all_bars]
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200)
    bb_upper, _bb_middle, bb_lower = bollinger_series(closes)
    rsi = rsi_series(closes)
    macd_line, macd_signal, macd_hist = macd(closes)
    markers = scan_signal_markers(all_bars)

    def series_after(bars, series):
        return [
            {"time": b.date.isoformat(), "value": v}
            for b, v in zip(bars, series, strict=True)
            if b.date >= cutoff
        ]

    visible_bars = [b for b in all_bars if b.date >= cutoff]
    return {
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
        "bb_lower": series_after(all_bars, bb_lower),
        "rsi": series_after(all_bars, rsi),
        "macd": {
            "line": series_after(all_bars, macd_line),
            "signal": series_after(all_bars, macd_signal),
            "histogram": series_after(all_bars, macd_hist),
        },
        "signal_markers": [m for m in markers if m["time"] >= cutoff.isoformat()],
    }


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
