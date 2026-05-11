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
    def __init__(self) -> None:
        self.calls = 0
        self.last_holdings: list[dict] | None = None
        self.last_holdings_totals: dict | None = None

    def daily_commentary(
        self, *, market_summary, watchlist_perf,
        holdings_overview=None, holdings_totals=None,
    ) -> str:
        self.calls += 1
        self.last_holdings = holdings_overview
        self.last_holdings_totals = holdings_totals
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


def test_failed_rerun_clears_stale_success_data(db_session: Session) -> None:
    """If a previous run succeeded and the rerun fails, stale JSON must not persist."""
    db_session.add(WatchlistItem(ticker="AAPL"))
    db_session.commit()
    good = FakeData()
    svc = RecapService(db_session, data=good, ai=FakeAi())
    first = svc.generate(date(2026, 5, 8))
    assert first.generation_status == "success"
    assert first.watchlist_performance_json is not None

    bad = FakeData()
    bad.fail_quote_for = set()  # noop; we'll fail at market overview instead

    class BadData(FakeData):
        def get_market_overview(self):
            raise RuntimeError("market is down")

    svc2 = RecapService(db_session, data=BadData(), ai=FakeAi())
    second = svc2.generate(date(2026, 5, 8))
    assert second.generation_status == "failed"
    assert second.watchlist_performance_json is None
    assert second.market_summary_json is None
    assert second.ai_commentary_text is None


def test_empty_watchlist_skips_ai_commentary(db_session: Session) -> None:
    """No tickers and no holdings → no AI call (saves tokens). Placeholder text."""
    ai = FakeAi()
    svc = RecapService(db_session, data=FakeData(), ai=ai)
    result = svc.generate(date(2026, 5, 8))
    assert result.generation_status == "success"
    assert ai.calls == 0
    assert result.ai_commentary_text is not None


def test_recap_includes_holdings_overview(db_session: Session) -> None:
    """Recap with holdings persists overview + totals and passes them to AI."""
    from marketpulse.db.models import Holding

    db_session.add(WatchlistItem(ticker="AAPL"))
    # NVDA: 10 shares @ $80, current $100 → +$200 (+25%)
    db_session.add(Holding(ticker="NVDA", quantity=10, avg_cost=80))
    db_session.commit()

    ai = FakeAi()
    svc = RecapService(db_session, data=FakeData(), ai=ai)
    result = svc.generate(date(2026, 5, 8))

    assert result.generation_status == "success"
    assert ai.last_holdings is not None
    assert ai.last_holdings[0]["ticker"] == "NVDA"
    assert ai.last_holdings[0]["pl_dollars"] == 200
    assert ai.last_holdings[0]["pl_pct"] == 25
    assert ai.last_holdings_totals["pl_dollars"] == 200

    overview = json.loads(result.holdings_overview_json)
    assert overview[0]["ticker"] == "NVDA"
    totals = json.loads(result.holdings_totals_json)
    assert totals["pl_dollars"] == 200


def test_recap_with_only_holdings_no_watchlist(db_session: Session) -> None:
    """Holdings alone should still trigger AI commentary."""
    from marketpulse.db.models import Holding
    db_session.add(Holding(ticker="MSFT", quantity=5, avg_cost=200))
    db_session.commit()
    ai = FakeAi()
    svc = RecapService(db_session, data=FakeData(), ai=ai)
    result = svc.generate(date(2026, 5, 8))
    assert result.generation_status == "success"
    assert ai.calls == 1  # AI called because holdings exist
    assert ai.last_holdings is not None
    assert ai.last_holdings[0]["ticker"] == "MSFT"


def test_recap_no_holdings_no_watchlist_skips_ai(db_session: Session) -> None:
    """Neither watchlist nor holdings → no AI call."""
    ai = FakeAi()
    svc = RecapService(db_session, data=FakeData(), ai=ai)
    result = svc.generate(date(2026, 5, 8))
    assert ai.calls == 0
    assert result.holdings_overview_json is None
    assert result.holdings_totals_json is None
    assert "自选股清单和持仓均为空" in result.ai_commentary_text
