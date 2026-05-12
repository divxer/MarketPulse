import contextlib
from datetime import UTC, date, datetime

import yfinance as yf
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from marketpulse.data.types import Bar, Fundamentals, IndexQuote, MarketOverview, NewsItem, Quote
from marketpulse.logging import get_logger

log = get_logger(__name__)

_YF_PERIOD_MAP = {
    "30d": "1mo",
    "60d": "3mo",
    "6m": "6mo",
    "1y": "1y",
}


def _is_transient(exc: BaseException) -> bool:
    """Network errors AND yfinance rate-limit messages are worth retrying.
    Real programmer errors (ValueError on missing tickers) are not."""
    if isinstance(exc, (OSError, RuntimeError, TimeoutError)):
        return True
    msg = str(exc).lower()
    return any(s in msg for s in ("rate limit", "too many requests", "429"))


# Use longer backoff on rate-limit (Yahoo returns 429 when hammered).
_retry = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, max=30),
    retry=retry_if_exception(_is_transient),
)


class YFinanceClient:
    """Thin wrapper around yfinance — the only module that imports yfinance.

    Replace via constructor injection in tests.
    """

    @_retry
    def fetch_quote(self, ticker: str) -> Quote:
        t = yf.Ticker(ticker)
        info = t.fast_info
        hist = t.history(period="21d", interval="1d")
        if hist.empty:
            raise ValueError(f"no data for {ticker}")
        last_close = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else last_close
        change_pct = (last_close - prev_close) / prev_close * 100 if prev_close else 0.0
        avg_vol = int(hist["Volume"].tail(20).mean()) if len(hist) >= 5 else 0
        return Quote(
            ticker=ticker,
            price=float(info.last_price or last_close),
            change_pct=change_pct,
            volume=int(hist["Volume"].iloc[-1]),
            avg_volume_20d=avg_vol,
            fetched_at=datetime.now(UTC),
        )

    @_retry
    def fetch_history(self, ticker: str, period: str = "60d") -> list[Bar]:
        yf_period = _YF_PERIOD_MAP.get(period, "3mo")
        hist = yf.Ticker(ticker).history(period=yf_period, interval="1d")
        bars: list[Bar] = []
        for idx, row in hist.iterrows():
            bars.append(
                Bar(
                    date=idx.date() if hasattr(idx, "date") else idx,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=int(row["Volume"]),
                )
            )
        return bars

    @_retry
    def fetch_news(self, ticker: str, limit: int = 10) -> list[NewsItem]:
        items = yf.Ticker(ticker).news or []
        out: list[NewsItem] = []
        for item in items[:limit]:
            # yfinance returns either the legacy flat shape (title/link/publisher/
            # providerPublishTime) or a newer nested shape under `content`. Probe
            # both so we degrade gracefully if the upstream schema flips again.
            content = item.get("content") or {}
            headline = content.get("title") or item.get("title") or ""
            summary = content.get("summary") or item.get("summary")
            url = (
                (content.get("canonicalUrl") or {}).get("url")
                or (content.get("clickThroughUrl") or {}).get("url")
                or item.get("link")
                or ""
            )
            source = (
                (content.get("provider") or {}).get("displayName")
                or item.get("publisher")
                or "unknown"
            )
            published = datetime.now(UTC)
            pub_iso = content.get("pubDate") or content.get("displayTime")
            ts = item.get("providerPublishTime")
            if pub_iso:
                with contextlib.suppress(ValueError):
                    published = datetime.fromisoformat(pub_iso.replace("Z", "+00:00"))
            elif ts:
                published = datetime.fromtimestamp(ts, tz=UTC)
            if not headline and not url:
                continue  # drop items with no useful content
            out.append(
                NewsItem(
                    ticker=ticker,
                    headline=headline,
                    url=url,
                    published_at=published,
                    source=source,
                    summary=summary,
                )
            )
        return out

    @_retry
    def fetch_splits(self, ticker: str) -> list[tuple[date, float]]:
        """Return historical splits for a ticker as (ex_date, ratio) pairs.

        ratio = new_shares / old_shares (forward 1:2 = 2.0, reverse 5:1 = 0.2).
        Returns an empty list if yfinance has no split history. Network and
        rate-limit errors propagate through `_retry` and surface to the caller.
        """
        s = yf.Ticker(ticker).splits
        if s is None or s.empty:
            return []
        out: list[tuple[date, float]] = []
        for ts, ratio in s.items():
            try:
                d = ts.date()
            except AttributeError:
                # Defensive: yfinance has historically returned naive datetimes.
                d = datetime.fromisoformat(str(ts)).date()
            out.append((d, float(ratio)))
        return out

    @_retry
    def fetch_fundamentals(self, ticker: str) -> Fundamentals:
        info = yf.Ticker(ticker).info or {}
        return Fundamentals(
            ticker=ticker,
            market_cap=info.get("marketCap"),
            pe_ratio=info.get("trailingPE"),
            eps=info.get("trailingEps"),
            sector=info.get("sector"),
            industry=info.get("industry"),
        )

    def fetch_market_overview(self) -> MarketOverview:
        symbols = ("SPY", "QQQ", "DIA", "^VIX")
        quotes: dict[str, IndexQuote] = {}
        for sym in symbols:
            q = self.fetch_quote(sym if sym != "^VIX" else "^VIX")
            key = sym.lstrip("^").lower()
            quotes[key] = IndexQuote(symbol=sym, price=q.price, change_pct=q.change_pct)
        return MarketOverview(
            spy=quotes["spy"],
            qqq=quotes["qqq"],
            dia=quotes["dia"],
            vix=quotes["vix"],
            fetched_at=datetime.now(UTC),
        )
