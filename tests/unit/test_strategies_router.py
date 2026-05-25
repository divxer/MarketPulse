"""Tests for router prompt context builder + LLM output parser."""
from datetime import UTC, date, datetime

import pytest

from marketpulse.data.types import Bar, Fundamentals, Quote


def _quote(ticker="AAPL", price=180.0):
    return Quote(
        ticker=ticker, price=price, change_pct=1.2, volume=10_000,
        avg_volume_20d=8_500, fetched_at=datetime.now(UTC), stale=False,
    )


def _fundamentals(ticker="AAPL"):
    return Fundamentals(
        ticker=ticker, market_cap=2.8e12, pe_ratio=28.0, eps=6.5,
        sector="Technology", industry="Consumer Electronics",
    )


def _bars(close_seq=None):
    close_seq = close_seq or [180.0 + i * 0.1 for i in range(60)]
    # Use consecutive dates to make tests deterministic
    base_date = date(2026, 3, 1)
    return [
        Bar(
            date=base_date.fromordinal(base_date.toordinal() + i),
            open=180, high=181, low=179, close=c, volume=10_000,
        )
        for i, c in enumerate(close_seq)
    ]


def test_build_router_context_has_required_fields():
    from marketpulse.strategies.router import build_router_context

    ctx = build_router_context(
        quote=_quote(),
        fundamentals=_fundamentals(),
        bars=_bars(),
        spy_bars=_bars([500.0 + i * 0.05 for i in range(60)]),
        news_count_7d=2,
    )
    # All required fields from spec § Router Design
    assert ctx["ticker"] == "AAPL"
    assert "price" in ctx
    assert "change_pct" in ctx
    assert "market_cap" in ctx
    assert "sector" in ctx
    assert "trend_summary" in ctx           # MA20/50 direction + position
    assert "volume_ratio_20d" in ctx
    assert "rsi_14" in ctx
    assert "sector_rs_20d_vs_spy" in ctx
    assert ctx["news_count_7d"] == 2


def test_build_router_context_volume_ratio_correct():
    """volume_ratio_20d = today_volume / avg_volume_20d."""
    from marketpulse.strategies.router import build_router_context
    q = _quote()
    # quote.volume=10000, avg_volume_20d=8500 → ratio ≈ 1.176
    ctx = build_router_context(
        quote=q, fundamentals=_fundamentals(),
        bars=_bars(), spy_bars=_bars(),
        news_count_7d=0,
    )
    assert ctx["volume_ratio_20d"] == pytest.approx(10000 / 8500, abs=0.01)


def test_render_router_prompt_lists_all_6_strategies():
    from marketpulse.strategies import load_strategies
    from marketpulse.strategies.router import render_router_prompt

    strategies = load_strategies()
    ctx = {
        "ticker": "AAPL", "price": 180.0, "change_pct": 1.2,
        "market_cap": 2.8e12, "sector": "Technology",
        "trend_summary": "MA20 向上", "volume_ratio_20d": 1.2,
        "rsi_14": 62, "sector_rs_20d_vs_spy": 3.2, "news_count_7d": 2,
    }
    prompt = render_router_prompt(strategies=strategies, context=ctx)
    # All 6 strategy names appear in the prompt's options list
    for name in ["fundamental_value", "momentum_breakout", "news_event",
                 "sector_rotation", "oversold_reversal", "general"]:
        assert name in prompt
    # The context shows up
    assert "AAPL" in prompt
    assert "180.0" in prompt or "$180" in prompt
    assert "ROUTER_JSON" in prompt


def test_parse_router_output_valid():
    from marketpulse.strategies.router import parse_router_output
    raw = (
        "我会用动量突破策略,因为价格刚刚突破前期高点。\n\n"
        "ROUTER_JSON: {\"strategy\": \"momentum_breakout\", \"reason\": \"突破新高\"}"
    )
    result = parse_router_output(raw, valid_names={"momentum_breakout", "general"})
    assert result == {"strategy": "momentum_breakout", "reason": "突破新高"}


def test_parse_router_output_uses_rfind_when_marker_quoted_in_body():
    """If the LLM mentions ROUTER_JSON: in the body before the real one."""
    from marketpulse.strategies.router import parse_router_output
    raw = (
        "ROUTER_JSON: 这是输出格式说明。\n\n"
        "实际选择:动量突破\n\n"
        "ROUTER_JSON: {\"strategy\": \"momentum_breakout\", \"reason\": \"x\"}"
    )
    result = parse_router_output(raw, valid_names={"momentum_breakout"})
    assert result["strategy"] == "momentum_breakout"


def test_parse_router_output_no_marker_returns_none():
    from marketpulse.strategies.router import parse_router_output
    result = parse_router_output("no router json here.", valid_names={"general"})
    assert result is None


def test_parse_router_output_malformed_json_returns_none():
    from marketpulse.strategies.router import parse_router_output
    result = parse_router_output(
        "ROUTER_JSON: not-json-at-all",
        valid_names={"general"},
    )
    assert result is None


def test_parse_router_output_invalid_strategy_name_returns_none():
    from marketpulse.strategies.router import parse_router_output
    result = parse_router_output(
        'ROUTER_JSON: {"strategy": "bogus", "reason": "x"}',
        valid_names={"momentum_breakout", "general"},
    )
    assert result is None


def test_parse_router_output_missing_strategy_field_returns_none():
    from marketpulse.strategies.router import parse_router_output
    result = parse_router_output(
        'ROUTER_JSON: {"reason": "x"}',
        valid_names={"general"},
    )
    assert result is None


# --- Production-observed Haiku output shapes (post-PR #109) ---

def test_parse_router_output_markdown_fenced_json_no_marker():
    """Haiku often returns ```json {...} ``` without the ROUTER_JSON: marker."""
    from marketpulse.strategies.router import parse_router_output
    raw = (
        '```json\n'
        '{\n  "strategy": "news_event",\n  "reason": "7天20条新闻"\n}\n'
        '```\n\n**选择依据：** ...'
    )
    result = parse_router_output(
        raw, valid_names={"news_event", "general"},
    )
    assert result == {"strategy": "news_event", "reason": "7天20条新闻"}


def test_parse_router_output_marker_inside_fence_with_trailing_prose():
    """Haiku: marker + JSON inside ```json fence + trailing **说明** prose."""
    from marketpulse.strategies.router import parse_router_output
    raw = (
        '```json\nROUTER_JSON: {\n  "strategy": "momentum_breakout", '
        '"reason": "突破"\n}\n```\n\n**说明：** 价格突破...'
    )
    result = parse_router_output(
        raw, valid_names={"momentum_breakout"},
    )
    assert result is not None
    assert result["strategy"] == "momentum_breakout"


def test_parse_router_output_picks_last_valid_block():
    """If LLM dumps multiple drafts, take the LAST valid block."""
    from marketpulse.strategies.router import parse_router_output
    raw = (
        '{"strategy": "general", "reason": "first draft"}\n\n'
        'on reflection:\n'
        '{"strategy": "news_event", "reason": "final answer"}'
    )
    result = parse_router_output(
        raw, valid_names={"general", "news_event"},
    )
    assert result == {"strategy": "news_event", "reason": "final answer"}
