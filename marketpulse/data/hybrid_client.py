"""Combines TencentClient (fast quotes, China-friendly) with YFinanceClient
(history / news / fundamentals / market overview).

Configured via QUOTE_SOURCE env var:
  - "auto"     (default): try Tencent first for fetch_quote, fall back to
                          yfinance on any failure.
  - "tencent": Tencent only for quotes (yfinance still used for history etc.)
  - "yfinance": Skip Tencent entirely (legacy behavior).

Non-quote methods (fetch_history, fetch_news, fetch_fundamentals,
fetch_market_overview) always delegate to yfinance — Tencent's free API
doesn't expose them.
"""

from typing import Protocol

from marketpulse.data.types import Bar, Fundamentals, MarketOverview, NewsItem, Quote
from marketpulse.logging import get_logger

log = get_logger(__name__)


class _TencentLike(Protocol):
    def fetch_quote(self, ticker: str) -> Quote: ...
    def fetch_history(self, ticker: str, period: str = ...) -> list[Bar]: ...


class _YFLike(Protocol):
    def fetch_quote(self, ticker: str) -> Quote: ...
    def fetch_history(self, ticker: str, period: str = ...) -> list[Bar]: ...
    def fetch_news(self, ticker: str, limit: int = ...) -> list[NewsItem]: ...
    def fetch_fundamentals(self, ticker: str) -> Fundamentals: ...
    def fetch_market_overview(self) -> MarketOverview: ...


class HybridClient:
    def __init__(
        self,
        yf: _YFLike,
        *,
        tencent: _TencentLike | None = None,
        prefer_tencent: bool = True,
    ) -> None:
        self.yf = yf
        self.tencent = tencent
        self.prefer_tencent = prefer_tencent

    def fetch_quote(self, ticker: str) -> Quote:
        if self.tencent and self.prefer_tencent:
            try:
                return self.tencent.fetch_quote(ticker)
            except Exception as exc:
                log.info(
                    "tencent_quote_failed_falling_back",
                    ticker=ticker, error=str(exc),
                )
        return self.yf.fetch_quote(ticker)

    def fetch_history(self, ticker: str, period: str = "60d") -> list[Bar]:
        if self.tencent and self.prefer_tencent:
            try:
                return self.tencent.fetch_history(ticker, period=period)
            except Exception as exc:
                log.info(
                    "tencent_history_failed_falling_back",
                    ticker=ticker, error=str(exc),
                )
        return self.yf.fetch_history(ticker, period=period)

    def fetch_news(self, ticker: str, limit: int = 10) -> list[NewsItem]:
        return self.yf.fetch_news(ticker, limit=limit)

    def fetch_fundamentals(self, ticker: str) -> Fundamentals:
        return self.yf.fetch_fundamentals(ticker)

    def fetch_market_overview(self) -> MarketOverview:
        return self.yf.fetch_market_overview()
