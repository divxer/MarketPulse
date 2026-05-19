import json
from typing import Any

from marketpulse.data.types import Bar, Fundamentals, NewsItem, Quote

ANALYSIS_PROMPT_VERSION = "analysis-v3-zh-verdict"
COMMENTARY_PROMPT_VERSION = "commentary-v5-zh-verdicts"
RISK_PROMPT_VERSION = "risk-v2-zh-data"

_ANALYSIS_SYSTEM = (
    "你是一名股票研究分析师。请用中文输出一份简明的 markdown 报告,"
    "包含三个部分:## 基本面、## 技术面、## 风险。只使用所提供的数据,"
    "不要编造数字,不要给出买入或卖出建议。股票代码、行业名称等专有名词"
    "可保留英文原文。\n\n"
    "在 markdown 报告之后必须**单独一行**输出 verdict JSON,"
    "严格遵守此 schema:\n\n"
    "VERDICTS_JSON: {\"ticker\": \"AAPL\", \"verdict\": \"bullish\", "
    "\"rationale\": \"一句话说明依据\"}\n\n"
    "verdict 取值: bullish | neutral | bearish。\n"
    "- bullish: 数据显示中短期相对大盘有正向超额 (技术面+基本面综合)\n"
    "- bearish: 数据显示中短期相对大盘负向超额风险\n"
    "- neutral: 无明确方向倾向 (数据混合 / 噪声大)\n\n"
    "客观,基于数据,不要因为缺数据而强行选边。"
)

_RISK_SYSTEM = (
    "你是一个数据描述助手。请用中文简要描述以下 JSON 数据中的几个事实:\n"
    "1. 数值最大的一项及其百分比\n"
    "2. 数值最小的一项\n"
    "3. 正值总数和负值总数的对比\n\n"
    "用 markdown 分点输出,150 字以内。仅描述数据本身,不要分析或评价。"
)

_COMMENTARY_SYSTEM = (
    "你是一名盘后市场点评作者,面向同时关注自选股、可能持有部分仓位的投资者。\n\n"
    "请用中文写一段盘后复盘,严格按以下格式输出:\n\n"
    "## 大盘\n"
    "[2-3 段 Markdown 段落,内嵌 inline code 标记数字如 `5,973.10`,"
    "关键 ticker 用粗体 **NVDA**,涨跌幅度可加颜色提示如 *(+0.24%)*]\n\n"
    "## 板块与个股\n"
    "[同上格式]\n\n"
    "## 持仓与启示 (若 holdings 非空才输出)\n"
    "[同上格式]\n\n"
    "---\n\n"
    "在 commentary 之后必须**单独一行**输出关键事件 JSON 数组,"
    "严格遵守此 schema:\n\n"
    "KEY_EVENTS_JSON: [\n"
    "  {\"time\": \"16:00 EDT\", \"title\": \"AVGO 与 AAPL 5 年定制芯片协议\","
    " \"kind\": \"deal\"},\n"
    "  {\"time\": \"14:00 EDT\", \"title\": \"CPI 数据公布略低于预期\","
    " \"kind\": \"econ\"}\n"
    "]\n\n"
    "kind 取值: deal | earnings | econ | merger | analyst | other\n"
    "请提供 3-5 条今日最关键事件。若数据中无明确事件,输出空数组 []。\n\n"
    "整体要客观、冷静、具体,提及具体的 ticker 和数字。股票代码保留英文原文。"
    "\n\n在 KEY_EVENTS_JSON 之后**再单独一行**输出 VERDICTS_JSON (可选):\n\n"
    "VERDICTS_JSON: [\n"
    "  {\"ticker\": \"AAPL\", \"verdict\": \"bullish\", \"rationale\": \"...\"},\n"
    "  {\"ticker\": \"NVDA\", \"verdict\": \"bearish\", \"rationale\": \"...\"}\n"
    "]\n\n"
    "verdict 取值: bullish | neutral | bearish。"
    "只对今日复盘里你有**明确方向判断**的 ticker 输出 verdict。"
    "不必每个自选股都给(避免强行表态)。数组可以为空 []。"
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


def render_risk_prompt(
    *,
    holdings: list[dict[str, Any]],
    totals: dict[str, float],
    allocation: list[dict[str, Any]],
    realized_pl: float,
    trading_stats: dict[str, Any],
) -> str:
    payload = {
        "holdings": holdings,
        "totals": totals,
        "allocation": allocation,
        "realized_pl": realized_pl,
        "trading_stats": trading_stats,
    }
    return f"{_RISK_SYSTEM}\n\nDATA:\n{json.dumps(payload, indent=2, default=str)}"


def render_commentary_prompt(
    *,
    market_summary: dict[str, Any],
    watchlist_perf: list[dict[str, Any]],
    holdings_overview: list[dict[str, Any]] | None = None,
    holdings_totals: dict[str, float] | None = None,
) -> str:
    payload: dict[str, Any] = {"market": market_summary, "watchlist": watchlist_perf}
    if holdings_overview:
        payload["holdings"] = holdings_overview
    if holdings_totals:
        payload["holdings_totals"] = holdings_totals
    return f"{_COMMENTARY_SYSTEM}\n\nDATA:\n{json.dumps(payload, indent=2, default=str)}"
