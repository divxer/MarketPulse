from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from marketpulse.data.service import DataService
from marketpulse.data.types import Bar, Fundamentals, IndexQuote, MarketOverview, NewsItem, Quote


class FakeYF:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_quote = False

    def fetch_quote(self, ticker: str) -> Quote:
        self.calls.append(("quote", ticker))
        if self.fail_quote:
            raise RuntimeError("yfinance is down")
        return Quote(
            ticker=ticker, price=100.0, change_pct=1.0, volume=1000,
            avg_volume_20d=900, fetched_at=datetime.now(UTC),
        )

    def fetch_history(self, ticker: str, period: str = "60d") -> list[Bar]:
        self.calls.append(("history", ticker))
        today = date.today()
        return [
            Bar(date=today - timedelta(days=1), open=1, high=2, low=0.5, close=1.5, volume=100),
            Bar(date=today, open=1.5, high=2, low=1, close=1.8, volume=120),
        ]

    def fetch_news(self, ticker: str, limit: int = 10) -> list[NewsItem]:
        self.calls.append(("news", ticker))
        return [NewsItem(
            ticker=ticker, headline="x", url="https://a.com",
            published_at=datetime.now(UTC), source="s",
        )]

    def fetch_fundamentals(self, ticker: str) -> Fundamentals:
        self.calls.append(("fund", ticker))
        return Fundamentals(ticker=ticker, market_cap=1.0, pe_ratio=10.0,
                            eps=1.0, sector="Tech", industry="SW")

    def fetch_market_overview(self) -> MarketOverview:
        self.calls.append(("market", ""))

        def q(s: str) -> IndexQuote:
            return IndexQuote(symbol=s, price=100, change_pct=0.5)

        return MarketOverview(spy=q("SPY"), qqq=q("QQQ"), dia=q("DIA"),
                              vix=q("^VIX"), fetched_at=datetime.now(UTC))


def test_quote_passes_through(db_session: Session) -> None:
    yf = FakeYF()
    svc = DataService(db_session, yf)
    q = svc.get_quote("AAPL")
    assert q.ticker == "AAPL"
    assert q.price == 100.0
    assert not q.stale


def test_quote_falls_back_to_cache_on_failure(db_session: Session) -> None:
    yf = FakeYF()
    svc = DataService(db_session, yf)
    svc.get_history("AAPL", period="60d")  # populates cache
    yf.fail_quote = True
    q = svc.get_quote("AAPL")
    assert q.stale is True
    assert q.price > 0


def test_history_uses_cache_when_complete(db_session: Session) -> None:
    yf = FakeYF()
    svc = DataService(db_session, yf)
    svc.get_history("AAPL", period="60d")
    yf.calls.clear()
    bars = svc.get_history("AAPL", period="60d")
    assert ("history", "AAPL") not in yf.calls  # second call hits cache
    assert len(bars) == 2


def test_news_caches_and_returns(db_session: Session) -> None:
    yf = FakeYF()
    svc = DataService(db_session, yf)
    items = svc.get_news("AAPL", limit=5)
    assert len(items) == 1
    items2 = svc.get_news("AAPL", limit=5)
    # second call still hits yfinance (we always refresh news), but dedups
    assert len(items2) == 1


def test_market_overview(db_session: Session) -> None:
    svc = DataService(db_session, FakeYF())
    m = svc.get_market_overview()
    assert m.spy.symbol == "SPY"

