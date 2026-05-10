from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from marketpulse.ai.service import AiService
from marketpulse.data.service import DataService
from marketpulse.logging import get_logger
from marketpulse.web.deps import get_ai_service, get_data_service, require_auth
from marketpulse.web.main import templates

router = APIRouter()
log = get_logger(__name__)


@router.get("/stock/{ticker}", response_class=HTMLResponse)
def stock_page(
    request: Request,
    ticker: str,
    data: DataService = Depends(get_data_service),
    _: None = Depends(require_auth),
):
    ticker = ticker.upper()
    try:
        quote = data.get_quote(ticker)
        bars = data.get_history(ticker, period="60d")
        news = data.get_news(ticker, limit=5)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request, "stock.html",
        {"ticker": ticker, "quote": quote, "bars": bars, "news": news},
    )


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
