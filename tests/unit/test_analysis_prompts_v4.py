"""Tests for v4 base_system + render_strategy_analysis_prompt."""


def test_analysis_prompt_version_is_v4():
    from marketpulse.ai.prompts import ANALYSIS_PROMPT_VERSION
    assert ANALYSIS_PROMPT_VERSION == "analysis-v4"


def test_base_analysis_system_contains_verdict_taxonomy():
    """base_system must define VERDICTS_JSON output schema + verdict values."""
    from marketpulse.ai.prompts import _BASE_ANALYSIS_SYSTEM
    assert "VERDICTS_JSON" in _BASE_ANALYSIS_SYSTEM
    assert "bullish" in _BASE_ANALYSIS_SYSTEM
    assert "neutral" in _BASE_ANALYSIS_SYSTEM
    assert "bearish" in _BASE_ANALYSIS_SYSTEM


def test_base_analysis_system_strips_three_section_structure():
    """base_system MUST NOT prescribe 基本面/技术面/风险 — strategies define their own."""
    from marketpulse.ai.prompts import _BASE_ANALYSIS_SYSTEM
    # The fixed three-section structure goes away
    assert "包含三个部分" not in _BASE_ANALYSIS_SYSTEM


def test_render_strategy_analysis_prompt_includes_strategy_instructions():
    """The rendered system message = base_system + strategy.instructions."""
    from datetime import UTC, date, datetime

    from marketpulse.ai.prompts import render_strategy_analysis_prompt
    from marketpulse.data.types import Bar, Fundamentals, NewsItem, Quote
    from marketpulse.strategies.types import Strategy

    strat = Strategy(
        name="momentum_breakout",
        display_name="动量突破",
        version="v1",
        description="x",
        applies_when="x",
        expected_horizons=[5, 20],
        instructions="STRATEGY_MARKER_BREAKOUT_ANALYSIS_BODY",
    )
    quote = Quote(
        ticker="AAPL", price=180.0, change_pct=1.0, volume=1000,
        avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False,
    )
    fundamentals = Fundamentals(
        ticker="AAPL", market_cap=3e12, pe_ratio=25.0, eps=7.0,
        sector="Tech", industry="Hardware",
    )
    bars = [
        Bar(date=date(2026, 5, d), open=180, high=181, low=179,
            close=180.0 + d * 0.1, volume=1000)
        for d in range(1, 16)
    ]
    news: list[NewsItem] = []

    rendered = render_strategy_analysis_prompt(
        strategy=strat, quote=quote, fundamentals=fundamentals,
        news=news, bars=bars,
    )
    # base_system + strategy.instructions both in there
    assert "VERDICTS_JSON" in rendered
    assert "STRATEGY_MARKER_BREAKOUT_ANALYSIS_BODY" in rendered


def test_render_with_general_strategy_works():
    """general.yaml is the fallback — render should not require any special handling."""
    from datetime import UTC, date, datetime

    from marketpulse.ai.prompts import render_strategy_analysis_prompt
    from marketpulse.data.types import Bar, Fundamentals, Quote
    from marketpulse.strategies import load_strategies

    general = load_strategies()["general"]
    quote = Quote(
        ticker="AAPL", price=180.0, change_pct=1.0, volume=1000,
        avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False,
    )
    fundamentals = Fundamentals(
        ticker="AAPL", market_cap=3e12, pe_ratio=25.0, eps=7.0,
        sector="Tech", industry="Hardware",
    )
    bars = [
        Bar(date=date(2026, 5, d), open=180, high=181, low=179,
            close=180.0 + d * 0.1, volume=1000)
        for d in range(1, 16)
    ]
    rendered = render_strategy_analysis_prompt(
        strategy=general, quote=quote, fundamentals=fundamentals,
        news=[], bars=bars,
    )
    assert "通用分析" in rendered or "基本面" in rendered  # general's content
    assert "VERDICTS_JSON" in rendered
