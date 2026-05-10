from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from marketpulse.ai.service import AiService
from marketpulse.data.types import Bar, Fundamentals, NewsItem, Quote


class FakeAi:
    def __init__(self, response: str = "## Fundamentals\nx\n## Technicals\ny\n## Risks\nz") -> None:
        self.response = response
        self.calls = 0

    def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        return self.response


class FakeData:
    def get_quote(self, ticker: str) -> Quote:
        return Quote(
            ticker=ticker,
            price=100,
            change_pct=1,
            volume=1,
            avg_volume_20d=1,
            fetched_at=datetime.now(UTC),
        )

    def get_history(self, ticker: str, period: str = "60d") -> list[Bar]:
        return [Bar(date=date(2026, 5, 7), open=1, high=2, low=0.5, close=1.5, volume=100)]

    def get_news(self, ticker: str, limit: int = 10) -> list[NewsItem]:
        return [
            NewsItem(
                ticker=ticker,
                headline="x",
                url="u",
                published_at=datetime.now(UTC),
                source="s",
            )
        ]

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        return Fundamentals(
            ticker=ticker,
            market_cap=1,
            pe_ratio=10,
            eps=1,
            sector="t",
            industry="i",
        )


def test_analyze_writes_cache(db_session: Session) -> None:
    ai = FakeAi()
    svc = AiService(db_session, ai_client=ai, data=FakeData(), model="m1", ttl_hours=24)
    res = svc.analyze("NVDA")
    assert res.ticker == "NVDA"
    assert "Fundamentals" in res.response_markdown
    assert ai.calls == 1
    res2 = svc.analyze("NVDA")
    assert res2.cached is True
    assert ai.calls == 1  # cache hit


def test_analyze_invalidates_on_prompt_version_change(
    db_session: Session, monkeypatch
) -> None:
    ai = FakeAi()
    svc = AiService(db_session, ai_client=ai, data=FakeData(), model="m1", ttl_hours=24)
    svc.analyze("NVDA")
    monkeypatch.setattr("marketpulse.ai.prompts.ANALYSIS_PROMPT_VERSION", "analysis-v2")
    svc.analyze("NVDA")
    assert ai.calls == 2


def test_daily_commentary_passthrough(db_session: Session) -> None:
    ai = FakeAi(response="Markets were calm.")
    svc = AiService(db_session, ai_client=ai, data=FakeData(), model="m1", ttl_hours=24)
    text = svc.daily_commentary(
        market_summary={"spy": 0.8},
        watchlist_perf=[{"ticker": "AAPL", "change_pct": 1}],
    )
    assert text == "Markets were calm."
