"""Regression tests for the C1+C2+I1-I3+M1 fixes from PR #57 review."""
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

from marketpulse.ai.service import _ROUTER_CACHE, AiService
from marketpulse.data.types import Bar, Fundamentals, Quote


def _bars():
    base = date(2026, 3, 1)
    return [
        Bar(
            date=base.fromordinal(base.toordinal() + i),
            open=180, high=181, low=179,
            close=180.0 + i * 0.1, volume=10000,
        )
        for i in range(60)
    ]


def _svc(db_session, *, router_response, model_router="claude-haiku-4-5"):
    fake_ai = MagicMock()
    fake_ai.complete.side_effect = [
        router_response,
        "VERDICTS_JSON: {\"ticker\":\"AAPL\",\"verdict\":\"neutral\",\"rationale\":\"x\"}",
    ]
    fake_data = MagicMock()
    fake_data.get_quote.return_value = Quote(
        ticker="AAPL", price=180.0, change_pct=1.0, volume=10000,
        avg_volume_20d=8500, fetched_at=datetime.now(UTC), stale=False,
    )
    fake_data.get_fundamentals.return_value = Fundamentals(
        ticker="AAPL", market_cap=3e12, pe_ratio=25.0, eps=7.0,
        sector="Tech", industry="Hardware",
    )
    fake_data.get_history.return_value = _bars()
    fake_data.get_news.return_value = []
    return AiService(
        session=db_session, ai_client=fake_ai, data=fake_data,
        model="claude-sonnet-4-6", ttl_hours=24,
        model_analyze="claude-sonnet-4-6",
        model_router=model_router,
    )


# ---------- C1: ai_model_router env wiring ----------

def test_settings_exposes_ai_model_router_with_haiku_default():
    """C1: production builds must default to a cheap router model."""
    from marketpulse.config import Settings
    s = Settings()
    assert s.ai_model_router.startswith("claude-haiku"), (
        f"AI_MODEL_ROUTER default should be a Haiku-class model, got {s.ai_model_router!r}"
    )


def test_get_ai_service_passes_model_router(monkeypatch):
    """C1: deps.get_ai_service must thread the setting into AiService."""
    import os
    monkeypatch.setenv("AI_MODEL_ROUTER", "claude-haiku-test")
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    from marketpulse.db.base import Base, get_engine, init_engine, reset_engine
    init_engine("sqlite:///:memory:")
    Base.metadata.create_all(get_engine())
    try:
        from marketpulse.web.deps import get_ai_service, get_data_service, get_db
        # Resolve dependencies manually
        db = next(get_db())
        data = get_data_service(db)
        svc = get_ai_service(db, data)
        assert svc.model_router == "claude-haiku-test"
    finally:
        reset_engine()
        get_settings.cache_clear()
        if "AI_MODEL_ROUTER" in os.environ:
            del os.environ["AI_MODEL_ROUTER"]


# ---------- C2: module-level router cache survives per-request instances ----------

def test_router_cache_survives_across_aiservice_instances(db_session):
    """C2: a second AiService instance must hit the cache from the first call."""
    svc1 = _svc(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "momentum_breakout", "reason": "first"}',
    )
    name1, _ = svc1._route_strategy("AAPL")
    assert name1 == "momentum_breakout"

    # New instance — simulates next HTTP request via FastAPI Depends
    svc2 = _svc(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "fundamental_value", "reason": "different"}',
    )
    # Even though svc2.ai would return a different strategy, the cache hit
    # short-circuits — the LLM is NOT called.
    svc2.ai.complete.side_effect = AssertionError(
        "module-level router cache should serve the second instance"
    )
    name2, _ = svc2._route_strategy("AAPL")
    assert name2 == "momentum_breakout"  # cached value from svc1


# ---------- I1: ticker case normalized ----------

def test_route_strategy_normalizes_ticker_case(db_session):
    """I1: 'aapl' and 'AAPL' must share a router cache slot."""
    svc = _svc(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "general", "reason": "x"}',
    )
    svc._route_strategy("aapl")
    svc.ai.complete.side_effect = AssertionError(
        "case-normalized cache should serve upper-case ticker"
    )
    name, _ = svc._route_strategy("AAPL")
    assert name == "general"

    # Confirm the cache key is uppercase
    keys = list(_ROUTER_CACHE.keys())
    assert all(k[0] == k[0].upper() for k in keys)


# ---------- I3: market_cap None → "N/A" not "$0.00e+00" ----------

def test_render_router_prompt_renders_none_market_cap_as_na():
    from marketpulse.strategies import load_strategies
    from marketpulse.strategies.router import render_router_prompt

    ctx = {
        "ticker": "X", "price": 10.0, "change_pct": 0.0,
        "market_cap": None,  # ← key case
        "sector": "Unknown / Unknown",
        "trend_summary": "数据不足",
        "volume_ratio_20d": 1.0,
        "rsi_14": None,
        "sector_rs_20d_vs_spy": None,
        "news_count_7d": 0,
    }
    prompt = render_router_prompt(strategies=load_strategies(), context=ctx)
    assert "N/A" in prompt
    assert "$0.00e+00" not in prompt
    assert "0.0e+00" not in prompt
