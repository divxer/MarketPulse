"""Verify router stage emits the structlog events the spec requires."""
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

from structlog.testing import capture_logs

from marketpulse.ai.service import AiService
from marketpulse.data.types import Bar, Fundamentals, Quote


def _build_service(db_session, router_response: str):
    fake_ai = MagicMock()
    fake_ai.complete.side_effect = [
        router_response,
        '## Body\n\nVERDICTS_JSON: {"ticker":"AAPL","verdict":"neutral","rationale":"x"}',
    ]
    fake_data = MagicMock()
    fake_data.get_quote.return_value = Quote(
        ticker="AAPL",
        price=180.0,
        change_pct=1.0,
        volume=10000,
        avg_volume_20d=8500,
        fetched_at=datetime.now(UTC),
        stale=False,
    )
    fake_data.get_fundamentals.return_value = Fundamentals(
        ticker="AAPL",
        market_cap=3e12,
        pe_ratio=25.0,
        eps=7.0,
        sector="Technology",
        industry="Consumer Electronics",
    )
    base_date = date(2026, 3, 1)
    fake_data.get_history.return_value = [
        Bar(
            date=base_date.fromordinal(base_date.toordinal() + i),
            open=180,
            high=181,
            low=179,
            close=180.0 + i * 0.1,
            volume=10000,
        )
        for i in range(60)
    ]
    fake_data.get_news.return_value = []
    return AiService(
        session=db_session,
        ai_client=fake_ai,
        data=fake_data,
        model="claude-sonnet-4-6",
        ttl_hours=24,
        model_analyze="claude-sonnet-4-6",
        model_router="claude-haiku-4-5",
    )


def test_router_picked_emits_event_with_strategy_field(db_session):
    """Successful router pick emits structlog 'router_picked' with strategy + ticker + reason."""
    svc = _build_service(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "momentum_breakout", "reason": "突破新高"}',
    )
    with capture_logs() as captured:
        svc._route_strategy("AAPL")
    picks = [e for e in captured if e.get("event") == "router_picked"]
    assert len(picks) == 1
    assert picks[0]["strategy"] == "momentum_breakout"
    assert picks[0]["ticker"] == "AAPL"
    assert picks[0]["reason"] == "突破新高"


def test_router_fallback_emits_warning_with_reason(db_session):
    """Fallback emits 'router_fallback' (warning) with reason field and log_level='warning'."""
    svc = _build_service(db_session, router_response="garbage no marker")
    with capture_logs() as captured:
        svc._route_strategy("AAPL")
    fallbacks = [e for e in captured if e.get("event") == "router_fallback"]
    assert len(fallbacks) == 1
    assert fallbacks[0]["ticker"] == "AAPL"
    assert "parse_or_invalid" in fallbacks[0].get("reason", "")
    assert fallbacks[0]["log_level"] == "warning"
