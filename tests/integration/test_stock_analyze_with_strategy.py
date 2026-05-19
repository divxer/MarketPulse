"""End-to-end: /stock analyze() does router → deep → records event with strategy."""
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

from marketpulse.ai.service import AiService
from marketpulse.data.types import Bar, Fundamentals, Quote
from marketpulse.db.models import AiAnalysis, EvaluationEvent
from marketpulse.evaluation.constants import AIVerdict

_DEEP_RESPONSE_BULLISH = (
    "## 突破质量\n\n突破有效\n\n"
    "VERDICTS_JSON: {\"ticker\": \"AAPL\", \"verdict\": \"bullish\", \"rationale\": \"x\"}"
)


def _build_service(db_session, *, router_response, deep_response):
    fake_ai = MagicMock()
    fake_ai.complete.side_effect = [router_response, deep_response]
    fake_data = MagicMock()
    fake_data.get_quote.return_value = Quote(
        ticker="AAPL", price=180.0, change_pct=1.0, volume=10_000,
        avg_volume_20d=8_500, fetched_at=datetime.now(UTC), stale=False,
    )
    fake_data.get_fundamentals.return_value = Fundamentals(
        ticker="AAPL", market_cap=3e12, pe_ratio=25.0, eps=7.0,
        sector="Tech", industry="Hardware",
    )
    base_date = date(2026, 3, 1)
    fake_data.get_history.return_value = [
        Bar(
            date=base_date.fromordinal(base_date.toordinal() + i),
            open=180, high=181, low=179,
            close=180.0 + i * 0.1, volume=10_000,
        )
        for i in range(60)
    ]
    fake_data.get_news.return_value = []
    return AiService(
        session=db_session, ai_client=fake_ai, data=fake_data,
        model="claude-sonnet-4-6", ttl_hours=24,
        model_analyze="claude-sonnet-4-6",
        model_router="claude-haiku-4-5",
    )


def test_analyze_records_event_with_strategy_and_version(db_session):
    svc = _build_service(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "momentum_breakout", "reason": "x"}',
        deep_response=_DEEP_RESPONSE_BULLISH,
    )
    svc.analyze("AAPL")
    events = db_session.query(EvaluationEvent).all()
    assert len(events) == 1
    e = events[0]
    assert e.payload["strategy"] == "momentum_breakout"
    assert e.payload["strategy_version"] == "v1"
    assert e.payload["prompt_version"] == "analysis-v4"
    assert e.subtype == AIVerdict.BULLISH


def test_analyze_cache_hit_returns_existing_no_new_event(db_session):
    """Second call same ticker within 24h: cache hits, no new event."""
    svc = _build_service(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "momentum_breakout", "reason": "x"}',
        deep_response=_DEEP_RESPONSE_BULLISH,
    )
    svc.analyze("AAPL")
    # Any further LLM call should NOT run (cache hits skip both router AND deep)
    svc.ai.complete.side_effect = AssertionError("cache should serve second call")
    svc.analyze("AAPL")
    assert db_session.query(EvaluationEvent).count() == 1


def test_analyze_router_fallback_to_general_records_general_strategy(db_session):
    """Router parse fails → general fallback → deep analysis with general.yaml."""
    svc = _build_service(
        db_session,
        router_response="garbage no marker",
        deep_response=_DEEP_RESPONSE_BULLISH,
    )
    svc.analyze("AAPL")
    events = db_session.query(EvaluationEvent).all()
    assert len(events) == 1
    assert events[0].payload["strategy"] == "general"


def test_analyze_stores_strategy_columns_on_ai_analyses(db_session):
    """AiAnalysis row should populate strategy + strategy_version columns."""
    svc = _build_service(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "fundamental_value", "reason": "x"}',
        deep_response=_DEEP_RESPONSE_BULLISH,
    )
    svc.analyze("AAPL")
    rows = db_session.query(AiAnalysis).all()
    assert len(rows) == 1
    assert rows[0].strategy == "fundamental_value"
    assert rows[0].strategy_version == "v1"
    assert rows[0].prompt_version == "analysis-v4"


def test_analyze_different_strategies_cache_independently(db_session):
    """Same ticker, two different days (two different router picks) → 2 cache rows."""
    svc = _build_service(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "momentum_breakout", "reason": "x"}',
        deep_response=_DEEP_RESPONSE_BULLISH,
    )
    svc.analyze("AAPL")
    # Clear module-level router cache to simulate "next day"
    from marketpulse.ai.service import _router_cache_clear
    _router_cache_clear()
    # New router response picks a different strategy
    svc.ai.complete.side_effect = [
        'ROUTER_JSON: {"strategy": "oversold_reversal", "reason": "y"}',
        _DEEP_RESPONSE_BULLISH,
    ]
    svc.analyze("AAPL")
    rows = db_session.query(AiAnalysis).order_by(AiAnalysis.id).all()
    assert len(rows) == 2
    assert {r.strategy for r in rows} == {"momentum_breakout", "oversold_reversal"}
