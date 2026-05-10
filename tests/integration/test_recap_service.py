import json
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from marketpulse.data.types import Bar, IndexQuote, MarketOverview, NewsItem, Quote
from marketpulse.db.models import DailyRecap, WatchlistItem
from marketpulse.recap.service import RecapService


class FakeData:
    def __init__(self) -> None:
        self.fail_quote_for: set[str] = set()

    def get_market_overview(self) -> MarketOverview:
        def q(s: str) -> IndexQuote:
            return IndexQuote(symbol=s, price=100, change_pct=0.5)
        return MarketOverview(spy=q("SPY"), qqq=q("QQQ"), dia=q("DIA"),
                              vix=q("^VIX"), fetched_at=datetime.now(UTC))

    def get_quote(self, ticker: str) -> Quote:
        if ticker in self.fail_quote_for:
            raise RuntimeError("boom")
        return Quote(ticker=ticker, price=100, change_pct=1.0, volume=1_000_000,
                     avg_volume_20d=1_000_000, fetched_at=datetime.now(UTC))

    def get_history(self, ticker: str, period: str = "60d") -> list[Bar]:
        return [Bar(date=date(2026, 5, 7), open=1, high=2, low=0.5, close=1.5, volume=100)] * 25

    def get_news(self, ticker: str, limit: int = 10) -> list[NewsItem]:
        return [NewsItem(ticker=ticker, headline=f"{ticker} news", url=f"https://x/{ticker}",
                         published_at=datetime.now(UTC), source="s")]


class FakeAi:
    def daily_commentary(self, *, market_summary, watchlist_perf) -> str:
        return "All good."


def test_generate_recap_success(db_session: Session) -> None:
    db_session.add_all([WatchlistItem(ticker="AAPL"), WatchlistItem(ticker="NVDA")])
    db_session.commit()
    svc = RecapService(db_session, data=FakeData(), ai=FakeAi())
    result = svc.generate(date(2026, 5, 8))
    assert result.generation_status == "success"
    assert result.ai_commentary_text == "All good."
    perf = json.loads(result.watchlist_performance_json)
    assert {p["ticker"] for p in perf} == {"AAPL", "NVDA"}


def test_generate_recap_idempotent(db_session: Session) -> None:
    db_session.add(WatchlistItem(ticker="AAPL"))
    db_session.commit()
    svc = RecapService(db_session, data=FakeData(), ai=FakeAi())
    svc.generate(date(2026, 5, 8))
    svc.generate(date(2026, 5, 8))
    assert db_session.query(DailyRecap).count() == 1


def test_partial_failure_marked_per_ticker(db_session: Session) -> None:
    db_session.add_all([WatchlistItem(ticker="AAPL"), WatchlistItem(ticker="BAD")])
    db_session.commit()
    fake = FakeData()
    fake.fail_quote_for = {"BAD"}
    svc = RecapService(db_session, data=fake, ai=FakeAi())
    result = svc.generate(date(2026, 5, 8))
    perf = json.loads(result.watchlist_performance_json)
    bad = next(p for p in perf if p["ticker"] == "BAD")
    assert bad["error"] is not None
    assert result.generation_status == "success"


def test_complete_failure_when_market_data_unavailable(db_session: Session) -> None:
    class BadData(FakeData):
        def get_market_overview(self):
            raise RuntimeError("market is down")

    db_session.add(WatchlistItem(ticker="AAPL"))
    db_session.commit()
    svc = RecapService(db_session, data=BadData(), ai=FakeAi())
    result = svc.generate(date(2026, 5, 8))
    assert result.generation_status == "failed"
    assert "market is down" in (result.error_message or "")
