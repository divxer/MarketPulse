"""Integration: AiService router stage — LLM call + per-day cache."""
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

from marketpulse.ai.service import AiService
from marketpulse.data.types import Bar, Fundamentals, Quote


def _build_service(db_session, *, router_response: str = ""):
    fake_ai = MagicMock()
    # complete() can be called for both router (cheap model) and deep analysis.
    # First call = router; second call = deep analysis (deep stage isn't tested
    # here, so just return a valid VERDICTS_JSON deep response if it fires).
    _deep = (
        '## Body\n\nVERDICTS_JSON: {"ticker":"AAPL","verdict":"neutral","rationale":"x"}'
    )
    fake_ai.complete.side_effect = [router_response, _deep]
    fake_data = MagicMock()
    fake_data.get_quote.return_value = Quote(
        ticker="AAPL", price=180.0, change_pct=1.0, volume=10000,
        avg_volume_20d=8500, fetched_at=datetime.now(UTC), stale=False,
    )
    fake_data.get_fundamentals.return_value = Fundamentals(
        ticker="AAPL", market_cap=3e12, pe_ratio=25.0, eps=7.0,
        sector="Technology", industry="Consumer Electronics",
    )
    base_date = date(2026, 3, 1)
    fake_data.get_history.return_value = [
        Bar(
            date=base_date.fromordinal(base_date.toordinal() + i),
            open=180, high=181, low=179,
            close=180.0 + i * 0.1, volume=10000,
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


def test_route_strategy_returns_router_pick(db_session):
    svc = _build_service(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "momentum_breakout", "reason": "突破新高"}',
    )
    name, reason = svc._route_strategy("AAPL")
    assert name == "momentum_breakout"
    assert reason == "突破新高"


def test_route_strategy_falls_back_to_general_on_parse_failure(db_session):
    svc = _build_service(db_session, router_response="garbage no marker")
    name, reason = svc._route_strategy("AAPL")
    assert name == "general"


def test_route_strategy_falls_back_when_router_picks_invalid_name(db_session):
    svc = _build_service(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "bogus", "reason": "x"}',
    )
    name, _ = svc._route_strategy("AAPL")
    assert name == "general"


def test_route_strategy_uses_daily_cache(db_session):
    """Second call same ticker same day → no LLM call."""
    svc = _build_service(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "momentum_breakout", "reason": "x"}',
    )
    svc._route_strategy("AAPL")
    # Replace side_effect with an exception so second router call would fail
    svc.ai.complete.side_effect = AssertionError("router should not run again same day")
    name, _ = svc._route_strategy("AAPL")
    # If cache works, no AssertionError raised
    assert name == "momentum_breakout"


def test_route_strategy_uses_router_model_not_analyze_model(db_session):
    """The router LLM call uses model_router, not model_analyze."""
    svc = _build_service(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "general", "reason": "x"}',
    )
    svc._route_strategy("AAPL")
    # First call to complete() should have been with model=claude-haiku-4-5
    call = svc.ai.complete.call_args_list[0]
    assert call.kwargs["model"] == "claude-haiku-4-5"
