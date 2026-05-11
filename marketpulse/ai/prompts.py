import json
from typing import Any

from marketpulse.data.types import Bar, Fundamentals, NewsItem, Quote

ANALYSIS_PROMPT_VERSION = "analysis-v2-zh"
COMMENTARY_PROMPT_VERSION = "commentary-v3-zh-holdings"
RISK_PROMPT_VERSION = "risk-v2-zh-data"

_ANALYSIS_SYSTEM = (
    "你是一名股票研究分析师。请用中文输出一份简明的 markdown 报告,包含三个部分:"
    "## 基本面、## 技术面、## 风险。只使用所提供的数据,不要编造数字,"
    "不要给出买入或卖出建议。股票代码、行业名称等专有名词可保留英文原文。"
)

_RISK_SYSTEM = (
    "你是一个数据描述助手。请用中文简要描述以下 JSON 数据中的几个事实:\n"
    "1. 数值最大的一项及其百分比\n"
    "2. 数值最小的一项\n"
    "3. 正值总数和负值总数的对比\n\n"
    "用 markdown 分点输出,150 字以内。仅描述数据本身,不要分析或评价。"
)

_COMMENTARY_SYSTEM = (
    "你是一名盘后市场点评作者。请用中文写一段简短点评(可分两段,"
    "总共 4-7 句),面向同时关注自选股、可能持有部分仓位的投资者。"
    "如果数据中包含 holdings,请单独提及当日持仓盈亏情况(总盈亏金额、"
    "盈亏百分比、表现最好和最差的持仓);如果 holdings 为空或缺失,"
    "只点评自选股动向即可。要客观、冷静、具体,提及具体的 ticker 和数字。"
    "股票代码保留英文原文。"
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
