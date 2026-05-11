from datetime import UTC, datetime

from marketpulse.data.hybrid_client import HybridClient
from marketpulse.data.types import (
    Bar,
    Fundamentals,
    IndexQuote,
    MarketOverview,
    NewsItem,
    Quote,
)


def _q(ticker: str, price: float) -> Quote:
    return Quote(
        ticker=ticker, price=price, change_pct=0,
        volume=1, avg_volume_20d=1, fetched_at=datetime.now(UTC),
    )


class _FakeTencent:
    def __init__(self, fail: bool = False, history_fail: bool = False) -> None:
        self.fail = fail
        self.history_fail = history_fail
        self.calls = 0
        self.history_calls = 0

    def fetch_quote(self, ticker: str) -> Quote:
        self.calls += 1
        if self.fail:
            raise ValueError("tencent unavailable")
        return _q(ticker, 100.0)

    def fetch_history(self, ticker: str, period: str = "60d") -> list[Bar]:
        self.history_calls += 1
        if self.history_fail:
            raise ValueError("tencent kline unavailable")
        return [Bar(
            date=datetime.now(UTC).date(),
            open=1, high=2, low=0.5, close=1.5, volume=100,
        )]


class _FakeYF:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_quote(self, ticker: str) -> Quote:
        self.calls += 1
        return _q(ticker, 200.0)

    def fetch_history(self, ticker: str, period: str = "60d") -> list[Bar]:
        return []

    def fetch_news(self, ticker: str, limit: int = 10) -> list[NewsItem]:
        return []

    def fetch_fundamentals(self, ticker: str) -> Fundamentals:
        return Fundamentals(
            ticker=ticker, market_cap=None, pe_ratio=None,
            eps=None, sector=None, industry=None,
        )

    def fetch_market_overview(self) -> MarketOverview:
        def idx(s: str) -> IndexQuote:
            return IndexQuote(symbol=s, price=100, change_pct=0)
        return MarketOverview(
            spy=idx("SPY"), qqq=idx("QQQ"), dia=idx("DIA"),
            vix=idx("^VIX"), fetched_at=datetime.now(UTC),
        )


def test_prefer_tencent_uses_tencent() -> None:
    tencent = _FakeTencent()
    yf = _FakeYF()
    client = HybridClient(yf, tencent=tencent, prefer_tencent=True)
    q = client.fetch_quote("AAPL")
    assert q.price == 100.0
    assert tencent.calls == 1
    assert yf.calls == 0


def test_tencent_failure_falls_back_to_yfinance() -> None:
    tencent = _FakeTencent(fail=True)
    yf = _FakeYF()
    client = HybridClient(yf, tencent=tencent, prefer_tencent=True)
    q = client.fetch_quote("AAPL")
    assert q.price == 200.0
    assert tencent.calls == 1
    assert yf.calls == 1


def test_no_tencent_means_yfinance_only() -> None:
    yf = _FakeYF()
    client = HybridClient(yf, tencent=None)
    q = client.fetch_quote("AAPL")
    assert q.price == 200.0
    assert yf.calls == 1


def test_news_fundamentals_delegate_to_yfinance() -> None:
    yf = _FakeYF()
    client = HybridClient(yf, tencent=_FakeTencent())
    client.fetch_news("AAPL")
    client.fetch_fundamentals("AAPL")
    client.fetch_market_overview()
    # Just verifying these calls succeed and route to yfinance (no Tencent fallback)


def test_history_prefers_tencent() -> None:
    tencent = _FakeTencent()
    yf = _FakeYF()
    client = HybridClient(yf, tencent=tencent, prefer_tencent=True)
    bars = client.fetch_history("AAPL")
    assert len(bars) == 1
    assert tencent.history_calls == 1


def test_history_falls_back_to_yfinance_on_tencent_failure() -> None:
    tencent = _FakeTencent(history_fail=True)
    yf = _FakeYF()
    client = HybridClient(yf, tencent=tencent, prefer_tencent=True)
    bars = client.fetch_history("AAPL")
    assert bars == []  # _FakeYF returns empty
    assert tencent.history_calls == 1
