# Phase 2 — AI Hit-Rate Evaluation

> **Trilogy context**: Phase 1 (eval infrastructure) ✅ shipped. Phase 2 (this) wires Claude AI verdicts into the eval system. Phase 3 (signal hit rate) reuses everything Phase 2 builds.
> **Target branch**: `feat/phase-2-ai-hit-rate`
> **Spec date**: 2026-05-18

## Goal

Wire Claude's existing AI predictions (stock deep analysis + recap commentary) into Phase 1's evaluation framework. Each AI verdict becomes an `EvaluationEvent`; the existing nightly job auto-computes forward returns. Surface hit-rate stats via (a) a small accuracy badge on `/stock/{ticker}` AI card and (b) a new `/lab/ai-track` dashboard.

After this phase, MarketPulse displays "Claude AAPL 5d 准确率 64% (8/12)" instead of letting the user blindly trust AI output.

## Non-Goals

- Historical backfill of past `AiAnalysis` rows (decision 5: clean start; data accrues over 2-3 weeks)
- Phase 3 signal hit-rate UI (namespace `/lab/signal-track` reserved but not built)
- AI confidence/strength field (payload extensible — defer to Phase 2.1)
- Prompt A/B comparison dashboard (filter scaffolding in place; specific UI deferred)
- Holdings risk-analysis as verdict source (decision 1: risk = narrative, not directional verdict)
- Multi-model comparison (Kronos / others — Phase 4)

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│ AI Prompt Layer (marketpulse/ai/prompts.py)                        │
│   ANALYSIS_PROMPT_VERSION:  analysis-v2-zh  →  analysis-v3-zh-verdict
│   COMMENTARY_PROMPT_VERSION: v4-zh-markdown → v5-zh-verdicts        │
│   Both emit VERDICTS_JSON marker after main content                 │
├────────────────────────────────────────────────────────────────────┤
│ Service Layer                                                      │
│   AiService.analyze() — parses VERDICTS_JSON, calls record_event   │
│   RecapService.generate() — parses VERDICTS_JSON, records N events │
│   marketpulse/evaluation/scoring.py [NEW]                          │
│     compute_hit_rate(...)                                          │
│     get_per_ticker_hit_rates(...)                                  │
│     get_hit_rate_trend(...)                                        │
│     get_recent_events_with_outcomes(...)                           │
├────────────────────────────────────────────────────────────────────┤
│ Phase 1 Infrastructure (already shipped)                           │
│   record_event() — validates + inserts EvaluationEvent             │
│   compute_outcomes job — nightly forward_return + excess vs SPY    │
├────────────────────────────────────────────────────────────────────┤
│ UI Layer                                                            │
│   /stock/{ticker}: AI card head badge (hit rate or "积累中")        │
│   /lab/ai-track [NEW]: dashboard with 4 KPI + trend + table + filter│
└────────────────────────────────────────────────────────────────────┘
```

No DB migration needed. Schema is Phase 1's; `event_type="ai_analysis"`, `subtype` ∈ {bullish, neutral, bearish}, `payload.source` ∈ {stock_analysis, recap}.

## Tech Stack

- Backend: FastAPI + SQLAlchemy 2.x (existing)
- AI: Anthropic Claude Sonnet 4.6 (existing)
- Frontend: Jinja2 + HTMX + vanilla CSS, NineScrolls design system
- Existing dependencies — no new packages
- Tests: pytest (~35 new tests)

## 决策记录

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| 1 | Hook 范围 | A + B (stock + recap; skip holdings risk) | risk 是风险叙事不是方向 verdict |
| 2 | Verdict 提取 | A · AI 显式输出 VERDICTS_JSON | 复用 Phase 5e KEY_EVENTS_JSON 模式 |
| 3 | 打分定义 | 超额方向 (excess return); 主显 5d | 衡量相对强弱,避大盘普涨带来的虚高 |
| 4 | UI 位置 | A · /stock 角标 + C · /lab/ai-track | 高频入口 + 量化研究宏观视图 |
| 5 | 历史数据 | 不回填 | 数据洁净比"立即可看"重要 |
| 6 | Dashboard 形态 | D · 分层 (总数 + 趋势 + ticker 表 + filters) | 一眼可读 + 可下钻 |

## AI Prompt 改造

### `marketpulse/ai/prompts.py`

#### Stock 深度分析 — v3

```python
ANALYSIS_PROMPT_VERSION = "analysis-v3-zh-verdict"  # 从 v2 升

_ANALYSIS_SYSTEM = (
    "你是一名股票研究分析师。请用中文输出一份简明的 markdown 报告,"
    "包含三个部分: ## 基本面、## 技术面、## 风险。只使用所提供的数据,"
    "不要编造数字,不要给出买入或卖出建议。股票代码、行业名称等专有名词"
    "可保留英文原文。\n\n"
    "在 markdown 报告之后必须**单独一行**输出 verdict JSON,严格遵守此 schema:\n\n"
    "VERDICTS_JSON: {\"ticker\": \"AAPL\", \"verdict\": \"bullish\", "
    "\"rationale\": \"一句话说明依据\"}\n\n"
    "verdict 取值: bullish | neutral | bearish。\n"
    "- bullish: 数据显示中短期相对大盘有正向超额 (技术面 + 基本面综合)\n"
    "- bearish: 数据显示中短期相对大盘负向超额风险\n"
    "- neutral: 无明确方向倾向 (数据混合 / 噪声大)\n\n"
    "客观,基于数据,不要因为缺数据而强行选边。"
)
```

#### Recap commentary — v5

```python
COMMENTARY_PROMPT_VERSION = "commentary-v5-zh-verdicts"  # 从 v4 升

# (_COMMENTARY_SYSTEM 在 v4 基础上加 VERDICTS_JSON 要求)
_COMMENTARY_SYSTEM = (
    ... # v4 现有内容 (## 大盘 / ## 板块与个股 / ## 持仓与启示)
    "KEY_EVENTS_JSON: [...]\n\n"
    "在 KEY_EVENTS_JSON 之后**再单独一行**输出 VERDICTS_JSON (可选):\n\n"
    "VERDICTS_JSON: [\n"
    "  {\"ticker\": \"AAPL\", \"verdict\": \"bullish\", \"rationale\": \"...\"},\n"
    "  {\"ticker\": \"NVDA\", \"verdict\": \"bearish\", \"rationale\": \"...\"}\n"
    "]\n\n"
    "只对今日复盘里你有**明确方向判断**的 ticker 输出 verdict。"
    "不必每个自选股都给(避免强行表态)。数组可以为空 []。"
)
```

### `marketpulse/recap/service.py:_parse_ai_output`

扩展返回三元组:

```python
def _parse_ai_output(raw: str) -> tuple[str, str | None, str | None]:
    """Returns (commentary_md, key_events_json, verdicts_json).

    Both KEY_EVENTS_JSON 和 VERDICTS_JSON markers are optional.
    Uses rfind for each to tolerate AI quoting marker in prose.
    """
    commentary = raw
    verdicts_json = _extract_marker(commentary, "VERDICTS_JSON:")
    if verdicts_json is not None:
        commentary = commentary[:commentary.rfind("VERDICTS_JSON:")].rstrip()
    key_events_json = _extract_marker(commentary, "KEY_EVENTS_JSON:")
    if key_events_json is not None:
        commentary = commentary[:commentary.rfind("KEY_EVENTS_JSON:")].rstrip()
    return commentary, key_events_json, verdicts_json
```

`_extract_marker` 处理 marker 后段 JSON 解析 + 验证 (list/dict 区分).

### Stock 分析路径 `marketpulse/ai/service.py:analyze`

新增 helper:

```python
def _parse_analyze_output(raw: str) -> tuple[str, dict | None]:
    """Returns (analysis_md, verdict_dict | None).

    VERDICTS_JSON value is a single object (one ticker analyzed).
    Returns None on missing marker or malformed JSON.
    """
```

## Event 记录

### 单 ticker 分析 (stock 路径)

```python
# In AiService.analyze() after AI call returns response_markdown
analysis_md, verdict = _parse_analyze_output(response_markdown)
if verdict is not None:
    try:
        record_event(
            event_type="ai_analysis",
            subtype=verdict["verdict"],         # bullish/neutral/bearish
            ticker=verdict["ticker"],
            event_time=datetime.now(UTC),
            event_price=quote.price,            # quote already fetched
            payload={
                "rationale": verdict.get("rationale", ""),
                "prompt_version": prompts.ANALYSIS_PROMPT_VERSION,
                "source": "stock_analysis",
                "model": self.model_analyze,
            },
            db=self.session,
        )
        self.session.commit()
    except ValueError as exc:
        log.warning("ai_verdict_invalid", error=str(exc), verdict=verdict)
    except Exception as exc:
        log.warning("record_event_failed", error=str(exc))
```

**Critical**: cache hit path does NOT call `record_event` (same prediction within 24h must not double-count).

### Recap 路径

```python
# In RecapService.generate() after parse
commentary_md, events_json, verdicts_json = _parse_ai_output(raw)
recap.ai_commentary_text = commentary_md
recap.key_events_json = events_json
# verdicts NOT stored on DailyRecap — they go straight to EvaluationEvent
if verdicts_json:
    try:
        verdicts = json.loads(verdicts_json)
        if isinstance(verdicts, list):
            for v in verdicts:
                if not isinstance(v, dict):
                    continue
                ticker = v.get("ticker", "").strip().upper()
                verdict = v.get("verdict", "")
                if not ticker or verdict not in AIVerdict.all():
                    continue
                try:
                    # Fetch quote at recap generation time
                    quote = self.data.get_quote(ticker)
                    record_event(
                        event_type="ai_analysis",
                        subtype=verdict,
                        ticker=ticker,
                        event_time=datetime.now(UTC),
                        event_price=quote.price,
                        payload={
                            "rationale": v.get("rationale", ""),
                            "prompt_version": prompts.COMMENTARY_PROMPT_VERSION,
                            "source": "recap",
                            "recap_date": target.isoformat(),
                            "model": self.ai_model,
                        },
                        db=self.session,
                    )
                except Exception as exc:
                    log.warning("recap_verdict_skipped",
                                ticker=ticker, error=str(exc))
            self.session.commit()
    except json.JSONDecodeError as exc:
        log.warning("verdicts_json_malformed", error=str(exc))
```

### Recap retry 重复处理

When user clicks "重新生成", existing recap is overwritten in `daily_recaps`, but **EvaluationEvents from the prior generation remain in the DB**. To avoid double-counting:

**Strategy**: Add `payload.recap_date` (we already do). Before recording new verdicts for a recap_date, delete any existing events where `event_type="ai_analysis"` AND `payload->>'source' = 'recap'` AND `payload->>'recap_date' = target_date`. PostgreSQL JSON operators work; SQLite JSON1 also supports this.

(SQLite path: `json_extract(payload, '$.recap_date') = '2026-05-18'`)

```python
# In RecapService.generate() before recording verdicts
db.query(EvaluationEvent).filter(
    EvaluationEvent.event_type == "ai_analysis",
    func.json_extract(EvaluationEvent.payload, "$.source") == "recap",
    func.json_extract(EvaluationEvent.payload, "$.recap_date") == target.isoformat(),
).delete(synchronize_session=False)
```

This implies losing the old verdict events (and their outcomes if any). Trade-off: cleaner data over historical preservation. Acceptable because retry usually happens within minutes of original generation, outcomes haven't accrued.

## Scoring 模块

### `marketpulse/evaluation/scoring.py` [NEW]

```python
"""Hit-rate queries over EvaluationEvent + EvaluationOutcome.

All functions are pure read; do not mutate state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketpulse.db.models import EvaluationEvent, EvaluationOutcome


NEUTRAL_THRESHOLD = 0.01   # |excess_return| < 1% counts as "no meaningful move"


@dataclass(frozen=True)
class HitRateStats:
    n_total: int                   # events with outcome at the queried horizon
    n_hits: int                    # events that hit per scoring rule
    n_bullish: int                 # subset breakdown
    n_bearish: int
    n_neutral: int
    n_bullish_hits: int
    n_bearish_hits: int
    n_neutral_hits: int
    hit_rate: float | None         # None if n_total == 0
    avg_excess_return: float       # signed by verdict direction; 0 if n_total == 0
    as_of: datetime


def compute_hit_rate(
    db: Session,
    *,
    event_type: str = "ai_analysis",
    subtype: str | None = None,
    ticker: str | None = None,
    horizon: int = 5,
    source: str | None = None,
    since: date | None = None,
) -> HitRateStats:
    """Core hit-rate query.

    Scoring rules:
      bullish + excess_return > NEUTRAL_THRESHOLD  → hit
      bearish + excess_return < -NEUTRAL_THRESHOLD → hit
      neutral + |excess_return| <= NEUTRAL_THRESHOLD → hit

    Events without outcome at this horizon are excluded from both n_total
    and n_hits (they're not "wrong" — just not measured yet).
    """
    ...

@dataclass(frozen=True)
class TickerHitRate:
    ticker: str
    n_total: int
    n_hits: int
    hit_rate: float | None
    avg_excess_return: float

def get_per_ticker_hit_rates(
    db: Session,
    *,
    horizon: int = 5,
    since: date | None = None,
) -> list[TickerHitRate]:
    """Returns per-ticker rollup, sorted by hit_rate desc.
    Tickers with n_total == 0 are excluded.
    Tickers with n_total < 5 keep their stats but caller (UI) decorates."""

@dataclass(frozen=True)
class DailyHitRate:
    day: date
    n_total: int
    hit_rate: float | None

def get_hit_rate_trend(
    db: Session,
    *,
    horizon: int = 5,
    window_days: int = 90,
    rolling: int = 30,    # 30-day rolling window
) -> list[DailyHitRate]:
    """Returns daily rolling hit rate over the past window_days.
    Each entry is the hit rate computed over events whose event_time
    falls in the rolling-day window ending on that day."""

@dataclass(frozen=True)
class EventOutcome:
    event_id: int
    event_time: datetime
    ticker: str
    verdict: str
    source: str            # from payload
    rationale: str         # from payload
    horizon: int
    forward_return: float
    excess_return: float
    hit: bool

def get_recent_events_with_outcomes(
    db: Session,
    *,
    horizon: int = 5,
    limit: int = 20,
) -> list[EventOutcome]:
    """Latest events with outcomes at this horizon, newest first."""
```

## 路由 / 端点

### `GET /stock/{ticker}` — 扩展 context

Existing route already provides AI analysis card. Add to context:

```python
from datetime import date, timedelta
from marketpulse.evaluation import scoring

since_90d = date.today() - timedelta(days=90)
stats = scoring.compute_hit_rate(
    db,
    event_type="ai_analysis",
    ticker=ticker_upper,
    horizon=5,
    since=since_90d,
)
ctx["ai_hit_rate"] = stats.hit_rate          # float | None
ctx["ai_n_hits"] = stats.n_hits
ctx["ai_n_total"] = stats.n_total            # used to decide pending/data state
ctx["ai_badge_color"] = _ai_badge_color(stats)  # "good"/"neutral"/"bad"/"pending"
```

Helper:
```python
def _ai_badge_color(stats: HitRateStats) -> str | None:
    if stats.n_total == 0:
        return None                    # no badge at all
    if stats.n_total < 5:
        return "pending"
    if stats.hit_rate is None:
        return "pending"
    if stats.hit_rate >= 0.60:
        return "good"
    if stats.hit_rate >= 0.40:
        return "neutral"
    return "bad"
```

### `GET /lab/ai-track` [NEW]

```python
@router.get("/lab/ai-track", response_class=HTMLResponse)
def lab_ai_track(
    request: Request,
    ticker: str | None = None,
    horizon: int = 5,
    source: str | None = None,    # "stock_analysis" | "recap" | None
    verdict: str | None = None,   # "bullish" | "neutral" | "bearish" | None
    since_days: int = 90,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    since = date.today() - timedelta(days=since_days)

    overall = scoring.compute_hit_rate(
        db, horizon=horizon, ticker=ticker.upper() if ticker else None,
        source=source, subtype=verdict, since=since,
    )
    trend = scoring.get_hit_rate_trend(
        db, horizon=horizon, window_days=since_days, rolling=30,
    )
    per_ticker = scoring.get_per_ticker_hit_rates(
        db, horizon=horizon, since=since,
    )
    recent = scoring.get_recent_events_with_outcomes(
        db, horizon=horizon, limit=20,
    )

    return templates.TemplateResponse(request, "lab_ai_track.html", {
        "overall": overall,
        "trend": trend,
        "per_ticker": per_ticker,
        "recent": recent,
        "filters": {
            "ticker": ticker, "horizon": horizon,
            "source": source, "verdict": verdict, "since_days": since_days,
        },
        "filters_qs": _qs_from_filters(...),
    })
```

Filter form on the page submits as plain GET (no HTMX); URL stays canonical.

## 模板架构

### 文件清单

```
marketpulse/web/templates/
├── lab_ai_track.html                          NEW
└── partials/
    ├── ai_track_hero.html                     NEW
    ├── ai_track_kpi_strip.html                NEW (4 cards)
    ├── ai_track_trend_chart.html              NEW (SVG)
    ├── ai_track_recent_events_table.html      NEW
    ├── ai_track_ticker_table.html             NEW
    └── ai_track_filter_card.html              NEW

marketpulse/web/templates/partials/
└── stock_ai_card.html                         MODIFY (add badge)
```

### `/stock/{ticker}` AI card badge

In existing `partials/stock_ai_card.html` (or wherever the AI analysis card is):

```html
<div class="mp-card__head">
  <span class="mp-card__title">
    <span class="material-symbols-outlined">auto_awesome</span>AI 分析
  </span>
  {% if ai_badge_color %}
    {% if ai_badge_color == "pending" %}
      <span class="mp-ai-badge mp-ai-badge--pending"
            title="N={{ ai_n_total }} 个 verdict, 5d 还在积累">
        积累中
        <small>({{ ai_n_total }})</small>
      </span>
    {% else %}
      <a href="/lab/ai-track?ticker={{ ticker }}"
         class="mp-ai-badge mp-ai-badge--{{ ai_badge_color }}"
         title="过去 90 天 {{ ticker }} 的 5d horizon hit rate">
        <span class="material-symbols-outlined">military_tech</span>
        {{ "{:.0f}%".format(ai_hit_rate * 100) }}
        <small>({{ ai_n_hits }}/{{ ai_n_total }})</small>
      </a>
    {% endif %}
  {% endif %}
</div>
```

### Lab 页面整体

```html
{% extends "base.html" %}
{% block main_width %}max-w-[2400px]{% endblock %}
{% block nav_width %}max-w-[2400px]{% endblock %}
{% block content %}

{% include "partials/ai_track_hero.html" %}

<section class="mp-ai-track-kpi">
  {% include "partials/ai_track_kpi_strip.html" %}
</section>

{% if overall.n_total == 0 %}
  <section class="mp-empty-placeholder">
    <div class="mp-card" style="padding:64px; text-align:center;">
      <p style="font-size:16px; color:var(--ns-on-surface-variant);">
        AI 评估数据积累中,需要至少 7 个交易日才有可读数据。
      </p>
      <a href="/recaps" class="mp-btn mp-btn--ghost" style="margin-top:16px;">
        浏览历史复盘
      </a>
    </div>
  </section>
{% else %}
  <section class="mp-ai-track-body">
    <div class="mp-ai-track-main">
      {% include "partials/ai_track_trend_chart.html" %}
      {% include "partials/ai_track_recent_events_table.html" %}
    </div>
    <aside class="mp-ai-track-rail">
      {% include "partials/ai_track_filter_card.html" %}
      {% include "partials/ai_track_ticker_table.html" %}
    </aside>
  </section>
{% endif %}

{% endblock %}
```

Body grid: `760px 1fr` (same as `/recap` after PR #47).

### KPI strip · 4 cards

| label | value | hint |
|---|---|---|
| 总 verdicts (90d) | `{{ overall.n_total }}` | `已评分,其中 {{ n_bullish }} 看涨 / {{ n_bearish }} 看跌 / {{ n_neutral }} 中性` |
| 5d Hit Rate | `{{ "{:.0f}%".format(overall.hit_rate * 100) }}` color-coded | `{{ n_hits }}/{{ n_total }} 命中` |
| Avg Excess | `{{ "{:+.2f}%".format(overall.avg_excess_return * 100) }}` color-coded | `对 SPY 超额收益均值` |
| Best Ticker | `{{ best.ticker }} {{ "{:+.0f}%".format(best.avg_excess_return * 100) }}` | `5d horizon · n={{ best.n_total }}` |

### Trend chart (SVG polyline)

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">show_chart</span>30 日滚动 Hit Rate
    </span>
    <span class="mp-card__sub">{{ trend|length }} 个数据点</span>
  </div>
  <div class="mp-card__body">
    {% if trend|length >= 2 %}
      <svg viewBox="0 0 600 200" width="100%" height="200">
        {# X axis: trend index, Y axis: hit_rate% inverted #}
        <polyline
          points="{% for d in trend %}{{ loop.index0 * (600 / (trend|length - 1)) }},{{ 200 - (d.hit_rate or 0) * 200 }} {% endfor %}"
          fill="none" stroke="var(--ns-primary)" stroke-width="2" />
        {# 50% baseline #}
        <line x1="0" y1="100" x2="600" y2="100"
              stroke="var(--ns-outline-variant)" stroke-dasharray="4 4" />
      </svg>
    {% else %}
      <p class="muted" style="text-align:center; padding:32px;">趋势数据不足</p>
    {% endif %}
  </div>
</section>
```

### Recent events table (10 cols)

时间 / Ticker / Source / Verdict / 价 / 5d 后价 / fwd% / excess% / 命中? / Rationale (truncate)

### Filter card

mp-card with 4 sections:
- Horizon: 1/3/5/10/20 radio chips
- Source: stock_analysis / recap / 全部
- Verdict: bullish / neutral / bearish / 全部
- Time: 30/90/180/all

Form submits GET. Reset URL on "重置" button.

### Ticker table (rail card)

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">leaderboard</span>按 Ticker
    </span>
    <span class="mp-card__sub">5d hit rate desc</span>
  </div>
  <ul class="mp-ai-track-ticker-list">
    {% for t in per_ticker %}
      <li>
        <a href="?ticker={{ t.ticker }}&horizon=5">{{ t.ticker }}</a>
        {% if t.n_total < 5 %}
          <span class="mp-chip mp-chip--pending">积累中 ({{ t.n_total }})</span>
        {% else %}
          <span class="mono tnum">{{ "{:.0f}%".format(t.hit_rate * 100) }}</span>
          <small>{{ t.n_hits }}/{{ t.n_total }}</small>
        {% endif %}
      </li>
    {% endfor %}
    {% if not per_ticker %}<li class="muted">暂无数据</li>{% endif %}
  </ul>
</section>
```

## CSS 新增 (`marketpulse/web/static/css/app.css`)

```css
/* ════════ Phase 2: AI hit-rate badge (/stock) ════════ */
.mp-ai-badge {
  display: inline-flex; align-items: center; gap: 4px;
  height: 22px; padding: 0 8px;
  font: 600 11px/1 var(--ns-font-mono);
  border-radius: 2px; text-decoration: none;
  transition: filter 200ms;
}
.mp-ai-badge .material-symbols-outlined { font-size: 14px; }
.mp-ai-badge small { font-size: 10px; opacity: 0.7; margin-left: 2px; }
.mp-ai-badge:hover { filter: brightness(0.95); }
.mp-ai-badge--good     { background: #d1fae5; color: #065f46; }
.mp-ai-badge--neutral  { background: var(--ns-surface-container); color: var(--ns-on-surface-variant); }
.mp-ai-badge--bad      { background: #fee2e2; color: #991b1b; }
.mp-ai-badge--pending  { background: #fef3c7; color: #92400e; }

/* ════════ Phase 2: /lab/ai-track layout ════════ */
.mp-ai-track-kpi {
  padding: 0 48px 16px;
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px;
}
.mp-ai-track-body {
  padding: 0 48px 32px;
  display: grid; grid-template-columns: 760px 1fr; gap: 56px;
}
.mp-ai-track-main { display: flex; flex-direction: column; gap: 16px; }
.mp-ai-track-rail { display: flex; flex-direction: column; gap: 16px; }

@media (max-width: 1640px) {
  .mp-ai-track-body { grid-template-columns: 1fr; }
}
@media (max-width: 900px) {
  .mp-ai-track-kpi { grid-template-columns: repeat(2, 1fr); }
}

/* Lab ticker list */
.mp-ai-track-ticker-list { list-style: none; margin: 0; padding: 10px 16px 18px; }
.mp-ai-track-ticker-list li {
  display: grid; grid-template-columns: 60px 1fr auto;
  gap: 10px; align-items: center; padding: 6px 0;
  border-bottom: 1px solid var(--ns-outline-variant);
}
.mp-ai-track-ticker-list li:last-child { border-bottom: 0; }
```

## 错误处理

| 场景 | 处理 |
|------|------|
| AI 输出无 `VERDICTS_JSON:` | `_extract_marker` 返回 None; 不调 record_event; 不影响 commentary/analysis 显示 |
| `VERDICTS_JSON: <malformed>` | 同上 (silent fallback) |
| verdict 不在 enum ("moon"/"strong_bull") | record_event 抛 ValueError → catch + log warning + skip 这一条 |
| recap retry 删除旧 events | 用 json_extract 过滤 source='recap' AND recap_date=target; outcomes ON DELETE CASCADE 跟着删 |
| record_event DB 错误 | catch + log warning, 不阻塞主流程 |
| compute_hit_rate 查询慢 | Phase 1 已建复合索引 (event_type, subtype, ticker, event_time); 应 < 50ms |
| `/lab/ai-track` 无 event | 整页 placeholder; "数据积累中" 引导文案 |
| Trend 数据点 < 2 | 趋势图卡片显 "数据不足" |
| 某个 ticker N < 5 | UI 角标 / Lab 列表 chip pending; 但 stats 仍计算 (不参与判定颜色) |
| EvaluationOutcome 缺 SPY 对照 | Phase 1 已处理 — 该 event 在该 horizon 上无 outcome; compute_hit_rate 自动排除 |

## 测试

```
tests/unit/test_analysis_prompt_parsing.py          NEW (5 tests)
  - test_parse_with_valid_verdicts_object
  - test_parse_without_verdicts_marker_returns_none
  - test_parse_malformed_verdicts_json_returns_none
  - test_parse_verdicts_object_missing_required_fields
  - test_parse_verdicts_with_invalid_verdict_value

tests/unit/test_recap_prompt_parsing.py             EXTEND (3 tests)
  - test_parse_returns_three_tuple_when_both_markers_present
  - test_parse_verdicts_only_no_key_events
  - test_parse_marker_order_independent (KEY_EVENTS before/after VERDICTS)

tests/unit/test_evaluation_scoring.py               NEW (11 tests)
  - test_compute_hit_rate_bullish_excess_positive_is_hit
  - test_compute_hit_rate_bearish_excess_negative_is_hit
  - test_compute_hit_rate_neutral_within_threshold_is_hit
  - test_compute_hit_rate_excludes_events_without_outcome
  - test_compute_hit_rate_filters_by_ticker
  - test_compute_hit_rate_filters_by_horizon
  - test_compute_hit_rate_filters_by_source_in_payload
  - test_compute_hit_rate_filters_by_since_date
  - test_compute_hit_rate_returns_none_hit_rate_when_n_zero
  - test_get_per_ticker_hit_rates_orders_by_hit_rate_desc
  - test_get_hit_rate_trend_30day_rolling_window

tests/integration/test_stock_analyze_records_event.py  NEW (5 tests)
  - test_first_analyze_records_event_with_verdict
  - test_cached_analyze_does_not_record_duplicate_event
  - test_cache_miss_after_ttl_records_new_event
  - test_invalid_ai_output_no_verdict_recorded
  - test_analyze_with_invalid_verdict_value_skips_event

tests/integration/test_recap_records_events.py      NEW (5 tests)
  - test_recap_with_3_verdicts_records_3_events
  - test_recap_without_verdicts_marker_no_events
  - test_recap_retry_deletes_old_events_for_same_date
  - test_recap_with_mixed_valid_invalid_verdicts_skips_invalid
  - test_recap_records_event_per_unique_ticker

tests/web/test_stock_ai_badge.py                    NEW (5 tests)
  - test_stock_page_renders_badge_when_data_present
  - test_stock_page_no_badge_when_n_total_zero
  - test_stock_page_pending_badge_when_n_below_5
  - test_stock_page_good_badge_color_when_hit_rate_above_60
  - test_stock_page_badge_links_to_lab_ai_track_with_ticker

tests/web/test_lab_ai_track.py                      NEW (6 tests)
  - test_lab_renders_placeholder_when_no_data
  - test_lab_renders_4_kpi_strip_when_data_present
  - test_lab_filter_horizon_changes_url_via_get
  - test_lab_filter_ticker_via_query_param
  - test_lab_ticker_table_pending_chip_when_n_below_5
  - test_lab_trend_chart_renders_svg_polyline_with_enough_data
```

Total: ~40 new tests.

## 风险 / 兼容性

| 风险 | 缓解 |
|---|---|
| Claude 不稳定输出 VERDICTS_JSON (偶尔漏) | 设计接受这种漏失 — 数据少几条不影响趋势可靠性 |
| Recap retry 重复事件污染 | retry 前 delete 该 recap_date 对应的 source='recap' 事件 |
| Hit rate 阈值 60/40 太粗 | YAGNI for now; Phase 2.1 加 Wilson score interval |
| AI 总输出 BULLISH 偏差 | Dashboard 显示 n_bullish/n_bearish/n_neutral 分布,用户能看到偏差 |
| `payload` JSON 查询性能 | Phase 1 索引已含 event_type/subtype/ticker/event_time;90 天 ~几千行,SQLite json_extract 足够快 |
| 多 ticker recap 一次写 5-10 个 event | DB 写量微小,无性能问题 |
| Prompt 升级 (v3/v5) 破坏旧 Recap | v4 / v2 recap 仍可读 (旧 markdown 不含 VERDICTS_JSON,parser silent fallback)。`AiAnalysis` 缓存继续生效 |
| Cache hit 不 record event 导致频繁访问的 ticker 数据稀疏 | 缓存 TTL=24h, 每天会生成 1 个 event;1 周 = 5-7 个 event,足够入门 |

## Out of Scope

- Confidence/strength 字段 (Phase 2.1)
- Wilson score interval / 统计显著性 (Phase 2.1)
- Prompt A/B 对比 (Phase 2.1)
- 单条 event 详细审计页 (Phase 2.1)
- AI 自我反思 (Phase 4+ 见到自己历史 hit rate 后调整)
- Phase 3 signal hit rate UI
- 多模型对比 (Phase 4)
- 移动端 < 768px 优化
- 历史回填
