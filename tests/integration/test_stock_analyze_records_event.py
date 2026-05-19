"""AiService.analyze() records EvaluationEvent on cache miss."""
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from marketpulse.ai.service import AiService
from marketpulse.db.models import AiAnalysis, EvaluationEvent
from marketpulse.evaluation.constants import AIVerdict, EventType


def _build_service(db_session, ai_response: str):
    """AiService with a fake AI client returning the given string."""
    from datetime import date

    from marketpulse.data.types import Bar, Fundamentals, Quote

    fake_ai = MagicMock()
    fake_ai.complete.return_value = ai_response

    fake_data = MagicMock()
    fake_data.get_quote.return_value = Quote(
        ticker="AAPL", price=180.0, change_pct=1.0, volume=1000,
        avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False,
    )
    fake_data.get_fundamentals.return_value = Fundamentals(
        ticker="AAPL", market_cap=3e12, pe_ratio=25.0, eps=7.0,
        sector="Technology", industry="Consumer Electronics",
    )
    fake_data.get_history.return_value = [
        Bar(date=date(2026, 5, d), open=180, high=181, low=179,
            close=180.0 + d * 0.1, volume=1000)
        for d in range(1, 16)
    ]
    fake_data.get_news.return_value = []

    return AiService(
        session=db_session,
        ai_client=fake_ai,
        data=fake_data,
        model="claude-sonnet-4-6",
        ttl_hours=24,
        model_analyze="claude-sonnet-4-6",
    )


_AI_OUTPUT_WITH_VERDICT = (
    "## 基本面\n\n苹果财务稳健。\n\n"
    "## 技术面\n\nRSI 60。\n\n"
    "## 风险\n\nAI 资本开支。\n\n"
    "VERDICTS_JSON: {\"ticker\": \"AAPL\", \"verdict\": \"bullish\", \"rationale\": \"x\"}"
)


def test_first_analyze_records_event_with_verdict(db_session):
    svc = _build_service(db_session, _AI_OUTPUT_WITH_VERDICT)
    svc.analyze("AAPL")
    events = db_session.query(EvaluationEvent).all()
    assert len(events) == 1
    e = events[0]
    assert e.event_type == EventType.AI_ANALYSIS
    assert e.subtype == AIVerdict.BULLISH
    assert e.ticker == "AAPL"
    assert e.event_price == pytest.approx(180.0)
    assert e.payload["source"] == "stock_analysis"
    assert e.payload["prompt_version"].startswith("analysis-v4")


def test_cached_analyze_does_not_record_duplicate_event(db_session):
    """Second call within TTL returns cached AiAnalysis; no new event."""
    svc = _build_service(db_session, _AI_OUTPUT_WITH_VERDICT)
    svc.analyze("AAPL")  # cache miss → 1 event
    svc.analyze("AAPL")  # cache hit → no new event
    events = db_session.query(EvaluationEvent).all()
    assert len(events) == 1


def test_invalid_ai_output_no_verdict_recorded(db_session):
    """AI response lacking VERDICTS_JSON: still caches AiAnalysis, no event."""
    raw = "## 基本面\n\n没有 verdicts 标记。"
    svc = _build_service(db_session, raw)
    svc.analyze("AAPL")
    assert db_session.query(AiAnalysis).count() == 1
    assert db_session.query(EvaluationEvent).count() == 0


def test_analyze_with_invalid_verdict_value_skips_event(db_session):
    """AI returns verdict='moon' (not in enum) → no event recorded."""
    raw = (
        "## 基本面\n\n正文。\n\n"
        "VERDICTS_JSON: {\"ticker\": \"AAPL\", \"verdict\": \"moon\", \"rationale\": \"x\"}"
    )
    svc = _build_service(db_session, raw)
    svc.analyze("AAPL")
    assert db_session.query(AiAnalysis).count() == 1   # still cached
    assert db_session.query(EvaluationEvent).count() == 0


def test_cache_miss_after_ttl_records_new_event(db_session):
    """Force cache expiry → next call records another event."""
    svc = _build_service(db_session, _AI_OUTPUT_WITH_VERDICT)
    svc.analyze("AAPL")
    # Force the cached row to look expired
    cached = db_session.query(AiAnalysis).filter_by(ticker="AAPL").one()
    cached.expires_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.commit()
    svc.analyze("AAPL")
    assert db_session.query(EvaluationEvent).count() == 2
