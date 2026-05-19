from datetime import UTC, datetime

from marketpulse.ai.prompts import (
    ANALYSIS_PROMPT_VERSION,
    COMMENTARY_PROMPT_VERSION,
    render_commentary_prompt,
    render_strategy_analysis_prompt,
)
from marketpulse.data.types import Bar, Fundamentals, NewsItem, Quote
from marketpulse.strategies import load_strategies


def test_render_analysis_prompt_contains_data() -> None:
    quote = Quote(ticker="NVDA", price=900, change_pct=2.5, volume=1, avg_volume_20d=1,
                  fetched_at=datetime.now(UTC))
    fund = Fundamentals(ticker="NVDA", market_cap=1e12, pe_ratio=70, eps=12, sector="Tech",
                        industry="Semis")
    news = [NewsItem(ticker="NVDA", headline="Big news", url="u",
                     published_at=datetime.now(UTC), source="x")]
    bars = [Bar(date=datetime(2026, 5, 7).date(), open=1, high=2, low=0.5, close=1.5, volume=100)]
    general = load_strategies()["general"]
    out = render_strategy_analysis_prompt(
        strategy=general, quote=quote, fundamentals=fund, news=news, bars=bars,
    )
    assert "NVDA" in out
    assert "Big news" in out
    assert ANALYSIS_PROMPT_VERSION  # non-empty


def test_render_commentary_prompt_with_recap_data() -> None:
    out = render_commentary_prompt(
        market_summary={"spy": 0.8, "qqq": 1.2, "vix": 14},
        watchlist_perf=[{"ticker": "AAPL", "change_pct": 1.2}],
    )
    assert "AAPL" in out
    assert COMMENTARY_PROMPT_VERSION
