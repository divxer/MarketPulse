from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from sqlalchemy.orm import Session

from marketpulse.data.cache import NewsCache, PriceCache
from marketpulse.data.quote_cache import QUOTE_CACHE
from marketpulse.data.types import Bar, Fundamentals, MarketOverview, NewsItem, Quote
from marketpulse.logging import get_logger

log = get_logger(__name__)


class _YFLike(Protocol):
    def fetch_quote(self, ticker: str) -> Quote: ...
    def fetch_history(self, ticker: str, period: str = ...) -> list[Bar]: ...
    def fetch_news(self, ticker: str, limit: int = ...) -> list[NewsItem]: ...
    def fetch_fundamentals(self, ticker: str) -> Fundamentals: ...
    def fetch_market_overview(self) -> MarketOverview: ...


class DataService:
    def __init__(
        self, session: Session, yf_client: _YFLike, *, news_ttl_days: int = 7
    ) -> None:
        self.session = session
        self.yf = yf_client
        self.price_cache = PriceCache(session)
        self.news_cache = NewsCache(session, ttl_days=news_ttl_days)

    def get_quote(self, ticker: str) -> Quote:
        # Short-circuit if we have a fresh quote — saves a yfinance call
        # (and avoids Yahoo Finance rate-limiting on repeat page loads).
        cached = QUOTE_CACHE.get(ticker)
        if cached is not None:
            return cached
        try:
            q = self.yf.fetch_quote(ticker)
            QUOTE_CACHE.set(ticker, q)
            return q
        except Exception as exc:
            log.warning("quote_fallback_to_cache", ticker=ticker, error=str(exc))
            bars = self.price_cache.get_range(
                ticker, date.today() - timedelta(days=30), date.today()
            )
            if not bars:
                raise
            last = bars[-1]
            prev = bars[-2] if len(bars) > 1 else bars[-1]
            change_pct = (
                ((last.close - prev.close) / prev.close * 100) if prev.close else 0.0
            )
            stale_quote = Quote(
                ticker=ticker,
                price=last.close,
                change_pct=change_pct,
                volume=last.volume,
                avg_volume_20d=int(
                    sum(b.volume for b in bars[-20:]) / max(len(bars[-20:]), 1)
                ),
                fetched_at=datetime.now(UTC),
                stale=True,
            )
            # Cache the stale quote with a SHORTER TTL (60s) so we stop hammering
            # yfinance during an ongoing outage, but recover quickly when it heals.
            QUOTE_CACHE.set(ticker, stale_quote, ttl_seconds=60)
            return stale_quote

    def get_history(self, ticker: str, period: str = "60d") -> list[Bar]:
        days = int(period.rstrip("d")) if period.endswith("d") else 60
        end = date.today()
        start = end - timedelta(days=days)
        cached = self.price_cache.get_range(ticker, start, end)
        if cached and (end - cached[-1].date).days <= 1:
            return cached
        try:
            bars = self.yf.fetch_history(ticker, period=period)
            self.price_cache.upsert(ticker, bars)
            return bars
        except Exception as exc:
            # On fetch failure (rate limit, etc.), serve any cached bars we have
            # instead of crashing the page. Empty list is fine — UI hides charts.
            log.warning("history_fetch_failed", ticker=ticker, error=str(exc))
            return cached

    def get_news(self, ticker: str, limit: int = 10) -> list[NewsItem]:
        try:
            fresh = self.yf.fetch_news(ticker, limit=limit)
            self.news_cache.upsert(fresh)
        except Exception as exc:
            log.warning("news_fetch_failed", ticker=ticker, error=str(exc))
        return self.news_cache.recent(ticker, limit=limit)

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        return self.yf.fetch_fundamentals(ticker)

    def get_market_overview(self) -> MarketOverview:
        return self.yf.fetch_market_overview()
