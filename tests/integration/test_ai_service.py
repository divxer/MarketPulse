from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from marketpulse.ai.service import AiService
from marketpulse.data.types import Bar, Fundamentals, NewsItem, Quote


class FakeAi:
    def __init__(self, response: str = "## Fundamentals\nx\n## Technicals\ny\n## Risks\nz") -> None:
        self.response = response
        self.calls = 0

    def complete(self, *, system: str, user: str, model: str | None = None) -> str:
        self.calls += 1
        self.last_model = model
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


def test_analyze_invalidates_on_model_change(db_session: Session) -> None:
    ai = FakeAi()
    svc1 = AiService(db_session, ai_client=ai, data=FakeData(), model="m1", ttl_hours=24)
    svc1.analyze("NVDA")
    # New service with a different model — cache row from m1 must not satisfy m2.
    svc2 = AiService(db_session, ai_client=ai, data=FakeData(), model="m2", ttl_hours=24)
    res = svc2.analyze("NVDA")
    assert ai.calls == 2
    assert res.cached is False


def test_analyze_uses_model_analyze_when_set(db_session: Session) -> None:
    """When model_analyze is provided, /stock deep analyze uses it instead of model."""
    ai = FakeAi()
    svc = AiService(
        db_session, ai_client=ai, data=FakeData(),
        model="cheap-model", ttl_hours=24, model_analyze="premium-model",
    )
    res = svc.analyze("NVDA")
    assert ai.last_model == "premium-model"
    assert res.model == "premium-model"


def test_analyze_falls_back_to_model_when_analyze_unset(db_session: Session) -> None:
    """When model_analyze is None or empty, analyze() uses model."""
    ai = FakeAi()
    svc = AiService(
        db_session, ai_client=ai, data=FakeData(),
        model="default-model", ttl_hours=24, model_analyze=None,
    )
    res = svc.analyze("NVDA")
    assert ai.last_model == "default-model"
    assert res.model == "default-model"


def test_daily_commentary_uses_base_model_not_analyze(db_session: Session) -> None:
    """Recap/commentary should NOT use the premium model — that's analyze-only."""
    ai = FakeAi(response="点评")
    svc = AiService(
        db_session, ai_client=ai, data=FakeData(),
        model="cheap-model", ttl_hours=24, model_analyze="premium-model",
    )
    svc.daily_commentary(market_summary={}, watchlist_perf=[])
    # daily_commentary doesn't pass model explicitly → falls back to client's default
    assert ai.last_model is None


def test_analyze_persists_full_input_snapshot(db_session: Session) -> None:
    import json as _json

    from sqlalchemy import select

    from marketpulse.db.models import AiAnalysis

    svc = AiService(
        db_session, ai_client=FakeAi(), data=FakeData(), model="m1", ttl_hours=24,
    )
    svc.analyze("NVDA")
    row = db_session.execute(select(AiAnalysis)).scalars().first()
    assert row is not None
    snap = _json.loads(row.input_data_json)
    assert snap["ticker"] == "NVDA"
    assert snap["quote"]["price"] == 100
    assert snap["quote"]["change_pct"] == 1
    assert snap["fundamentals"]["pe_ratio"] == 10
    assert snap["fundamentals"]["sector"] == "t"
    assert snap["bars"]["count"] == 1
    assert len(snap["news"]) == 1
    assert snap["news"][0]["headline"] == "x"


def test_daily_commentary_passthrough(db_session: Session) -> None:
    ai = FakeAi(response="Markets were calm.")
    svc = AiService(db_session, ai_client=ai, data=FakeData(), model="m1", ttl_hours=24)
    text = svc.daily_commentary(
        market_summary={"spy": 0.8},
        watchlist_perf=[{"ticker": "AAPL", "change_pct": 1}],
    )
    assert text == "Markets were calm."
