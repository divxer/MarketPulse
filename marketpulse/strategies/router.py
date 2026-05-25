"""Router stage: build LLM context + parse LLM output.

The router is a cheap LLM call (Haiku) that picks ONE strategy from the
loaded library based on a small structured ticker snapshot.

This module is pure: no DB, no LLM calls, no I/O. AiService wires it
to the actual LLM in Task 7.
"""
from __future__ import annotations

import json
import re
from typing import Any

from marketpulse.data.types import Bar, Fundamentals, Quote
from marketpulse.strategies.types import Strategy

_ROUTER_MARKER = "ROUTER_JSON:"

# Match a balanced-ish JSON object containing a "strategy" key. Tolerates
# the LLM wrapping the object in ```json ... ``` fences, prepending a
# ROUTER_JSON: marker, or appending free-form prose after the closing
# brace. We DON'T try to parse deeply nested JSON — the router prompt
# guarantees a flat {strategy, reason} shape.
_JSON_OBJECT_RE = re.compile(
    r"\{[^{}]*?\"strategy\"\s*:\s*\"[^\"]+\"[^{}]*?\}",
    re.DOTALL,
)


def build_router_context(
    *,
    quote: Quote,
    fundamentals: Fundamentals,
    bars: list[Bar],
    spy_bars: list[Bar],
    news_count_7d: int,
) -> dict[str, Any]:
    """Compose the structured snapshot the router LLM sees.

    All values are computed from the same fetched data Stage 2 (deep
    analysis) will reuse — no extra LLM-side computation.

    Returns a dict with 10 fields per spec § Router Design.
    """
    closes = [b.close for b in bars]
    spy_closes = [b.close for b in spy_bars]

    # 20-day rolling indicators
    ma20 = _mean(closes[-20:]) if len(closes) >= 20 else None
    ma50 = _mean(closes[-50:]) if len(closes) >= 50 else None
    high_60d = (max(closes[-60:]) if len(closes) >= 60 else max(closes)) if closes else quote.price
    pos_vs_60d_high_pct = ((quote.price - high_60d) / high_60d * 100.0) if high_60d else 0.0

    trend_summary = _trend_summary(closes, ma20, ma50, pos_vs_60d_high_pct)
    rsi_14 = _rsi(closes, period=14)
    volume_ratio = (quote.volume / quote.avg_volume_20d) if quote.avg_volume_20d else 0.0
    rs_20d = _relative_strength_20d(closes, spy_closes)

    return {
        "ticker": quote.ticker,
        "price": quote.price,
        "change_pct": quote.change_pct,
        "market_cap": fundamentals.market_cap,  # may be None when fundamentals are unknown
        "sector": f"{fundamentals.sector or '?'} / {fundamentals.industry or '?'}",
        "trend_summary": trend_summary,
        "volume_ratio_20d": round(volume_ratio, 2),
        "rsi_14": round(rsi_14, 1) if rsi_14 is not None else None,
        "sector_rs_20d_vs_spy": round(rs_20d * 100, 2) if rs_20d is not None else None,
        "news_count_7d": news_count_7d,
    }


def render_router_prompt(
    *, strategies: dict[str, Strategy], context: dict[str, Any],
) -> str:
    """Build the full router prompt: strategy menu + ticker snapshot + output schema."""
    menu_lines = [
        f"- {s.name}: {s.description}"
        for s in strategies.values()
    ]
    menu_block = "\n".join(menu_lines)

    mcap = context["market_cap"]
    mcap_str = f"${mcap:.2e}" if mcap else "N/A"
    rsi = context["rsi_14"]
    rsi_str = f"{rsi}" if rsi is not None else "N/A"
    rs = context["sector_rs_20d_vs_spy"]
    rs_str = f"{rs}%" if rs is not None else "N/A"

    snapshot = "\n".join([
        f"ticker: {context['ticker']}",
        f"price: ${context['price']:.2f} ({context['change_pct']:+.2f}%)",
        f"market_cap: {mcap_str}",
        f"sector: {context['sector']}",
        f"60d trend: {context['trend_summary']}",
        f"volume_ratio_20d: {context['volume_ratio_20d']} (今日量 / 20日均量)",
        f"rsi_14: {rsi_str}",
        f"sector_rs_20d_vs_spy: {rs_str}",
        f"news_count_7d: {context['news_count_7d']}",
    ])

    return (
        "你是分析策略路由器。根据下面这只股票的当前状态,"
        "从可选策略中选 1 个最合适的来做深度分析。\n\n"
        f"【可选策略】\n{menu_block}\n\n"
        f"【股票快照】\n{snapshot}\n\n"
        "输出 JSON,严格遵守 schema:\n"
        "ROUTER_JSON: {\"strategy\": \"<name>\", \"reason\": \"<一句话依据>\"}"
    )


def parse_router_output(
    raw: str, *, valid_names: set[str],
) -> dict[str, str] | None:
    """Extract the {strategy, reason} from router LLM output.

    Robust to common LLM output drift observed in production:
      - Markdown code fences (```json ... ```)
      - Prefix ``ROUTER_JSON:`` either bare or inside the fence
      - Trailing prose after the JSON object
      - Missing marker entirely (Haiku frequently skips it)

    Strategy: scan the response for the LAST JSON-object-looking blob
    that contains a "strategy" key (rfind semantics — prefer the final
    block in case the LLM repeated itself). Parse it as JSON. Validate
    the strategy field is in valid_names. Returns None on any failure;
    caller falls back to 'general'.
    """
    matches = _JSON_OBJECT_RE.findall(raw)
    if not matches:
        return None
    # Walk matches from last to first so we honor the original rfind
    # intent — the last JSON block in the response is the operative one
    # if the LLM dumped multiple drafts.
    for blob in reversed(matches):
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        strategy = parsed.get("strategy")
        if isinstance(strategy, str) and strategy in valid_names:
            return {
                "strategy": strategy,
                "reason": str(parsed.get("reason", "")),
            }
    return None


# ---------- internal indicators ----------

def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _rsi(closes: list[float], *, period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, period + 1):
        diff = closes[-i] - closes[-i - 1]
        if diff > 0:
            gains.append(diff)
        else:
            losses.append(-diff)
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _relative_strength_20d(closes: list[float], spy_closes: list[float]) -> float | None:
    if len(closes) < 21 or len(spy_closes) < 21:
        return None
    stock_ret = (closes[-1] - closes[-21]) / closes[-21] if closes[-21] else None
    spy_ret = (spy_closes[-1] - spy_closes[-21]) / spy_closes[-21] if spy_closes[-21] else None
    if stock_ret is None or spy_ret is None:
        return None
    return stock_ret - spy_ret


def _trend_summary(
    closes: list[float], ma20: float | None, ma50: float | None,
    pos_vs_60d_high_pct: float,
) -> str:
    if not closes or ma20 is None or ma50 is None:
        return "数据不足"
    direction = "上行" if closes[-1] > ma20 > ma50 else (
        "下行" if closes[-1] < ma20 < ma50 else "震荡"
    )
    return f"{direction}, 距 60d 高 {pos_vs_60d_high_pct:+.1f}%"
