import json
from typing import Any

from marketpulse.data.types import Bar, Fundamentals, NewsItem, Quote

ANALYSIS_PROMPT_VERSION = "analysis-v1"
COMMENTARY_PROMPT_VERSION = "commentary-v1"

_ANALYSIS_SYSTEM = (
    "You are an equity research analyst. Produce a concise markdown report with three sections: "
    "## Fundamentals, ## Technicals, ## Risks. Use only the data provided. "
    "Do not invent figures. Do not give buy/sell recommendations."
)

_COMMENTARY_SYSTEM = (
    "You are a market recap writer. In one paragraph (3-5 sentences), summarize today's market "
    "for an investor watching this watchlist. Be factual, calm, and specific."
)


def render_analysis_prompt(
    *, quote: Quote, fundamentals: Fundamentals, news: list[NewsItem], bars: list[Bar]
) -> str:
    payload: dict[str, Any] = {
        "ticker": quote.ticker,
        "current": {
            "price": quote.price,
            "change_pct": round(quote.change_pct, 2),
            "volume": quote.volume,
            "avg_volume_20d": quote.avg_volume_20d,
        },
        "fundamentals": {
            "market_cap": fundamentals.market_cap,
            "pe_ratio": fundamentals.pe_ratio,
            "eps": fundamentals.eps,
            "sector": fundamentals.sector,
            "industry": fundamentals.industry,
        },
        "recent_bars": [
            {"date": b.date.isoformat(), "close": b.close, "volume": b.volume}
            for b in bars[-30:]
        ],
        "news": [
            {"headline": n.headline, "source": n.source,
             "published": n.published_at.isoformat(), "summary": n.summary}
            for n in news[:10]
        ],
    }
    return f"{_ANALYSIS_SYSTEM}\n\nDATA:\n{json.dumps(payload, indent=2)}"


def render_commentary_prompt(
    *, market_summary: dict[str, Any], watchlist_perf: list[dict[str, Any]]
) -> str:
    payload = {"market": market_summary, "watchlist": watchlist_perf}
    return f"{_COMMENTARY_SYSTEM}\n\nDATA:\n{json.dumps(payload, indent=2)}"
