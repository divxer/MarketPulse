# Phase 5e — `/recap` 页 NineScrolls 重做

> 设计来源：`docs/design/mockups/page-recap.jsx` (285 行)
> 上一阶段：Phase 5d (`/holdings`) 已落地 (PR #40)
> 目标分支：`feat/phase-5e-recap-page`

## Goal

把 `/recap/{date}` 从 30 行简陋 Tailwind 重做成 NineScrolls 编辑长文风格：64px h1 hero / 5 大盘 Snap / 760px 阅读栏 + 720px 数据 rail (4 张数据卡)。同时 `/recaps` 历史列表升级为 NS 网格。

## Non-Goals

- 分享/置顶/推送至订阅者 (3 个 hero 按钮仅装饰,点击 toast "暂未启用")
- 移动端 < 768px 适配
- 复盘自动定时生成 (现有 scheduler 不动)
- 主页 `recap_card.html` partial (不动,保持现状)
- AI commentary 输出格式向前兼容 (旧复盘是纯文本,新格式有 Markdown + structured key_events;模板按存在性 fallback)
- 关键事件外部数据源 (Phase 5e.1 真有需求时再加 yfinance calendar API)
- 多用户/订阅者系统 (整个应用是单用户)

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ 模板层 (Jinja2 + HTMX)                                          │
│   recap.html (hero + 5 snap + 2-col body)                       │
│   recaps.html (grid)                                            │
│   8 个 partial (hero / snap / article / 4 right cards / grid)   │
├─────────────────────────────────────────────────────────────────┤
│ 路由层 (FastAPI)                                                │
│   GET  /recap/{date}    现有,扩展 context (parse JSON + history)│
│   GET  /recaps          现有,扩展 (加 P&L 摘要)                  │
│   POST /recap/{date}/retry  现有,不动                            │
├─────────────────────────────────────────────────────────────────┤
│ AI prompt 层 (marketpulse/ai/prompts.py)                        │
│   render_commentary_prompt 升级 → 输出 Markdown + KEY_EVENTS    │
│   COMMENTARY_PROMPT_VERSION 从 "commentary-v3-zh-holdings"      │
│   升到 "commentary-v4-zh-markdown"                              │
├─────────────────────────────────────────────────────────────────┤
│ Recap service 层 (marketpulse/recap/service.py)                 │
│   generate() 解析 AI 输出:                                       │
│   - 提取 Markdown commentary (KEY_EVENTS 之前)                   │
│   - 提取 KEY_EVENTS_JSON: [...] 之后的 JSON                      │
│   - 失败时 fallback: 整段当 commentary, events=[]                │
└─────────────────────────────────────────────────────────────────┘
```

DB migration：`daily_recaps.key_events_json TEXT NULL`。

## Tech Stack

- 后端：FastAPI + SQLAlchemy 2.x (existing)
- 模板：Jinja2 (existing)
- 前端：HTMX (existing) + 极少 vanilla JS (toast 函数)
- 样式：vanilla CSS via `app.css`,复用 `mp-card` / `mp-eyebrow` / `mp-hero` / `mp-prose` (Phase 5d) / `mp-btn` / `mp-kpi`-like
- 图标：Material Symbols Outlined
- 字体：Space Grotesk / Inter / Roboto Mono
- AI：Anthropic Claude (existing)
- 无新依赖

## 决策记录

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| 1 | 范围 | A · 完整 5e | 跟 5c/5d 同节奏 |
| 2 | Hero 4 actions | C · 仅"重新生成"实做,其它 toast | 单用户场景下分享/置顶/推送无意义 |
| 3 | Key events | A · AI 输出 structured JSON | 复用现有 AI,零外部依赖 |
| 4 | 长文 rendering | A · Markdown + markdown filter | 与 holdings AI 风险卡一致;向前兼容 |
| 5 | /recaps 处理 | A · 也升级 NS 网格 + 右栏 PrevRecaps | 视觉一致性 |

## 路由 / 端点

### `GET /recap/{recap_date}`

已存在,扩展 context dict。

**新 context dict：**

```python
{
  "row": DailyRecap,                          # 现有 (保留)
  "recap_date": date,                         # 新 (从 path)
  "commentary_md": row.ai_commentary_text or "",  # 新 → markdown filter
  "market_snap": parsed_market_summary,       # 新
  "portfolio_today": parsed_holdings_totals,  # 新
  "watchlist_perf": parsed_watchlist_perf,    # 新
  "key_events": parsed_key_events,            # 新 (新列)
  "prev_recaps": [DailyRecap, ...],           # 新 (last 6, excl. current)
  "model_version": "commentary-v4-zh-markdown · claude-sonnet-4-5",
  "generated_at_local": row.generated_at,     # 模板用 JS 转本地时间
}
```

**JSON parsing 容错**：每个 `parsed_*` 字段都 try/except,失败时 fallback 到 None/[]。模板按存在性显示 placeholder。

### `GET /recaps`

已存在,扩展 context dict。

**新 context dict：**

```python
{
  "rows": [
    {
      "recap_date": date,
      "generation_status": str,
      "generated_at": datetime | None,
      "summary": str,            # commentary 首段截断 200 字符
      "today_pl_dollars": float | None,  # 从 holdings_totals_json parse
      "today_pl_pct": float | None,
    },
    ...
  ],
}
```

最多 60 行 (现有 limit 不动),按 `recap_date` desc。

### `POST /recap/{recap_date}/retry`

现有,**不动**。但 `/recap/{date}` 页面的"重新生成"按钮使用 HTMX：

```html
<button class="mp-btn mp-btn--ghost"
        hx-post="/recap/{{ recap_date }}/retry"
        hx-target="body" hx-swap="outerHTML"
        hx-confirm="重新生成 {{ recap_date }} 的复盘?">
  <span class="material-symbols-outlined">refresh</span>重新生成
</button>
```

(整页刷,复用现有 retry 路由的 303 redirect。)

## DB Migration

```python
# alembic/versions/<auto>_add_daily_recaps_key_events.py
"""add daily_recaps.key_events_json column

Revises: 6b48d3a5c80f  (Phase 5d holdings sector)
"""

def upgrade() -> None:
    op.add_column(
        "daily_recaps",
        sa.Column("key_events_json", sa.Text(), nullable=True),
    )

def downgrade() -> None:
    op.drop_column("daily_recaps", "key_events_json")
```

实施者运行 `uv run alembic revision -m "add daily_recaps key_events_json"` 生成 stub,然后填上面 body。

`DailyRecap` model 加一列：

```python
key_events_json: Mapped[str | None] = mapped_column(Text, nullable=True)
```

(放在 `error_message` 之前,保持 model 字段顺序符合 schema 演进时间。)

## AI Prompt 升级

### `marketpulse/ai/prompts.py`

```python
COMMENTARY_PROMPT_VERSION = "commentary-v4-zh-markdown"  # 从 v3 升

_COMMENTARY_SYSTEM = (
    "你是一名盘后市场点评作者,面向同时关注自选股、可能持有部分仓位的投资者。\n\n"
    "请用中文写一段盘后复盘,严格按以下格式输出:\n\n"
    "## 大盘\n"
    "[2-3 段 Markdown 段落,内嵌 inline code 标记数字如 `5,973.10`,关键 ticker 用粗体 **NVDA**,涨跌幅度可加颜色提示如 *(+0.24%)*]\n\n"
    "## 板块与个股\n"
    "[同上格式]\n\n"
    "## 持仓与启示 (若 holdings 非空才输出)\n"
    "[同上格式]\n\n"
    "---\n\n"
    "在 commentary 之后必须**单独一行**输出关键事件 JSON 数组,严格遵守此 schema:\n\n"
    "KEY_EVENTS_JSON: [\n"
    "  {\"time\": \"16:00 EDT\", \"title\": \"AVGO 与 AAPL 5 年定制芯片协议\", \"kind\": \"deal\"},\n"
    "  {\"time\": \"14:00 EDT\", \"title\": \"CPI 数据公布略低于预期\", \"kind\": \"econ\"}\n"
    "]\n\n"
    "kind 取值: deal | earnings | econ | merger | analyst | other\n"
    "请提供 3-5 条今日最关键事件。若数据中无明确事件,输出空数组 []。\n\n"
    "整体要客观、冷静、具体,提及具体的 ticker 和数字。股票代码保留英文原文。"
)
```

(原 `_COMMENTARY_SYSTEM` 整段替换。)

### `marketpulse/recap/service.py:generate()` 解析逻辑

```python
def _parse_ai_output(raw: str) -> tuple[str, str | None]:
    """Split AI output into (commentary_markdown, key_events_json).

    Looks for the `KEY_EVENTS_JSON:` marker. Everything before is the
    commentary (Markdown). Everything after (parsed as JSON) is events.

    Failures (no marker, malformed JSON) silently fall back to: entire
    raw output as commentary, events_json = None.
    """
    marker = "KEY_EVENTS_JSON:"
    if marker not in raw:
        return raw, None

    idx = raw.index(marker)
    commentary = raw[:idx].rstrip()
    events_part = raw[idx + len(marker):].strip()

    # Validate JSON (parse + reserialize to canonicalize)
    try:
        events = json.loads(events_part)
        if not isinstance(events, list):
            return commentary, None
        return commentary, json.dumps(events, ensure_ascii=False)
    except json.JSONDecodeError:
        return commentary, None
```

在 `generate()` 现有 `recap.ai_commentary_text = commentary` 那行之前调用：

```python
commentary_md, events_json = _parse_ai_output(commentary_raw)
recap.ai_commentary_text = commentary_md
recap.key_events_json = events_json
```

(其中 `commentary_raw` 是 AI 调用返回的原始字符串。)

## 路由 implementation

### `marketpulse/web/routes/recap.py`

```python
import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from marketpulse.db.models import DailyRecap
from marketpulse.recap.service import RecapService
from marketpulse.web.deps import get_db, get_recap_service, require_auth
from marketpulse.web.main import templates

router = APIRouter()


def _safe_json_parse(text: str | None, default):
    """Try to parse JSON; return `default` on failure or None input."""
    if not text:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


@router.get("/recaps", response_class=HTMLResponse)
def recap_list(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    rows = (
        db.query(DailyRecap)
        .order_by(DailyRecap.recap_date.desc())
        .limit(60)
        .all()
    )
    enriched = []
    for r in rows:
        totals = _safe_json_parse(r.holdings_totals_json, {})
        enriched.append({
            "recap_date": r.recap_date,
            "generation_status": r.generation_status,
            "generated_at": r.generated_at,
            "summary": (r.ai_commentary_text or "")[:200],
            "today_pl_dollars": totals.get("today_pl_dollars"),
            "today_pl_pct": totals.get("today_pl_pct"),
        })
    return templates.TemplateResponse(request, "recaps.html", {"rows": enriched})


@router.get("/recap/{recap_date}", response_class=HTMLResponse)
def recap_detail(
    request: Request,
    recap_date: date,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    row = (
        db.query(DailyRecap)
        .filter(DailyRecap.recap_date == recap_date)
        .one_or_none()
    )
    if not row:
        raise HTTPException(status_code=404, detail="not found")

    prev_recaps = (
        db.query(DailyRecap)
        .filter(DailyRecap.recap_date != recap_date)
        .order_by(DailyRecap.recap_date.desc())
        .limit(6)
        .all()
    )

    return templates.TemplateResponse(
        request, "recap.html",
        {
            "row": row,
            "recap_date": recap_date,
            "commentary_md": row.ai_commentary_text or "",
            "market_snap": _safe_json_parse(row.market_summary_json, []),
            "portfolio_today": _safe_json_parse(row.holdings_totals_json, {}),
            "watchlist_perf": _safe_json_parse(row.watchlist_performance_json, []),
            "key_events": _safe_json_parse(row.key_events_json, []),
            "prev_recaps": prev_recaps,
            "model_version": "commentary-v4-zh-markdown · claude-sonnet-4-5",
        },
    )


@router.post("/recap/{recap_date}/retry")  # existing, unchanged
def recap_retry(
    recap_date: date,
    svc: RecapService = Depends(get_recap_service),
    _: None = Depends(require_auth),
):
    svc.generate(recap_date)
    return RedirectResponse(url=f"/recap/{recap_date}", status_code=303)
```

## 模板架构

### 文件清单

```
marketpulse/web/templates/
├── recap.html                                重写
├── recaps.html                               重写
└── partials/
    ├── recap_hero.html                       新
    ├── recap_market_snap.html                新 (5 大盘 KPI 卡)
    ├── recap_article.html                    新 (Markdown 长文)
    ├── recap_portfolio_today_card.html       新
    ├── recap_watchlist_perf_card.html        新
    ├── recap_key_events_card.html            新
    ├── recap_prev_recaps_card.html           新
    └── recap_card.html                       保留 (dashboard 用,不动)
```

### `recap.html`

```html
{% extends "base.html" %}
{% block main_width %}max-w-[2400px]{% endblock %}
{% block nav_width %}max-w-[2400px]{% endblock %}
{% block content %}

{% include "partials/recap_hero.html" %}

<section class="mp-recap-snap">
  {% include "partials/recap_market_snap.html" %}
</section>

<section class="mp-recap-body">
  <article class="mp-recap-article">
    {% include "partials/recap_article.html" %}
  </article>
  <aside class="mp-recap-rail">
    {% include "partials/recap_portfolio_today_card.html" %}
    {% include "partials/recap_watchlist_perf_card.html" %}
    {% include "partials/recap_key_events_card.html" %}
    {% include "partials/recap_prev_recaps_card.html" %}
  </aside>
</section>

<script>
function recapToast(msg) {
  // Lightweight toast (no library): bottom-center temporary banner.
  const t = document.createElement('div');
  t.textContent = msg;
  t.style.cssText = "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);"
    + "background:rgba(2,36,72,0.92);color:white;padding:10px 18px;border-radius:2px;"
    + "font-size:13px;z-index:9999;font-family:var(--ns-font-body);";
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2200);
}
</script>
{% endblock %}
```

### `partials/recap_hero.html`

```html
<section class="mp-recap-hero">
  <div class="mp-recap-hero__main">
    <span class="mp-eyebrow mp-eyebrow--primary">盘后复盘 · 美股</span>
    <h1 class="grotesk mp-recap-hero__title">
      {{ recap_date.strftime('%Y · %-m 月 %-d 日') }}
    </h1>
    <span class="mp-rule"></span>
    <p class="mp-recap-hero__desc">
      由 Claude 在收盘后基于您的自选股、当日持仓和大盘数据自动生成。
      客观、冷静、具体,提及具体的 ticker 和数字。
    </p>
  </div>

  <div class="mp-recap-hero__meta">
    <div class="mp-recap-hero__status">
      {% if row.generation_status == "ok" %}
        <span class="mp-pulse"></span>
        已生成 ·
        <time data-utc="{{ row.generated_at.isoformat() if row.generated_at else '' }}">
          {{ row.generated_at.strftime('%H:%M') if row.generated_at else '' }}
        </time>
      {% elif row.generation_status == "pending" %}
        <span class="muted">生成中…</span>
      {% else %}
        <span class="down">生成失败</span>
      {% endif %}
      <span class="mp-recap-hero__model">{{ model_version }}</span>
    </div>
    <div class="mp-recap-hero__actions">
      <button class="mp-btn mp-btn--ghost"
              hx-post="/recap/{{ recap_date }}/retry"
              hx-target="body" hx-swap="outerHTML"
              hx-confirm="重新生成 {{ recap_date }} 的复盘?">
        <span class="material-symbols-outlined">refresh</span>重新生成
      </button>
      <button class="mp-btn mp-btn--ghost" onclick="recapToast('分享功能暂未启用')">
        <span class="material-symbols-outlined">share</span>分享
      </button>
      <button class="mp-btn mp-btn--ghost" onclick="recapToast('置顶功能暂未启用')">
        <span class="material-symbols-outlined">push_pin</span>置顶
      </button>
      <button class="mp-btn mp-btn--navy" onclick="recapToast('推送功能暂未启用')">
        <span class="material-symbols-outlined">notifications_active</span>推送至订阅者
      </button>
    </div>
  </div>
</section>
```

### `partials/recap_market_snap.html`

```html
{# 5 大盘指数 KPI 卡。market_snap 由 market_summary_json parse。
   Expected shape: [{label, value, pct, up: bool}, ...] 或 dict;
   实施者按 RecapService.generate 中的实际 json shape 适配 #}
{% for item in market_snap %}
<div class="mp-card mp-recap-snap__card">
  <span class="mp-eyebrow mp-eyebrow--primary">{{ item.label }}</span>
  <div class="mono tnum mp-recap-snap__value">{{ item.value }}</div>
  <div class="mono tnum mp-recap-snap__pct {% if item.up %}up{% else %}down{% endif %}">
    <span class="material-symbols-outlined">
      {% if item.up %}trending_up{% else %}trending_down{% endif %}
    </span>
    {{ item.pct }}
  </div>
</div>
{% endfor %}
{% if not market_snap %}
  <div class="muted" style="grid-column: 1 / -1; padding: 16px; text-align:center;">
    暂无大盘数据
  </div>
{% endif %}
```

注：实施者需先确认 `market_summary_json` 的实际数据 shape (查 `RecapService.generate` 中 `market_summary_json` 是如何 dump 的)。如果 shape 与上不符,调整模板 attribute 访问方式。

### `partials/recap_article.html`

```html
<header class="mp-recap-article__head">
  <span class="mp-eyebrow mp-eyebrow--primary">编辑分析 · AI</span>
  <h2 class="grotesk mp-recap-article__title">每日盘后</h2>
  <span class="mp-rule"></span>
</header>

<div class="mp-prose mp-recap-prose">
  {% if commentary_md %}
    {{ commentary_md | markdown }}
  {% else %}
    <p class="muted">AI commentary 暂未生成。</p>
  {% endif %}
</div>
```

### `partials/recap_portfolio_today_card.html`

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">account_balance_wallet</span>组合今日
    </span>
    {% set pl = portfolio_today.today_pl_dollars %}
    {% if pl is not none %}
      <span class="mono tnum {% if pl >= 0 %}up{% else %}down{% endif %}" style="font-weight:700;">
        {{ "{:+,.2f}".format(pl) }}
        {% if portfolio_today.today_pl_pct is not none %}
          · {{ "{:+.2f}%".format(portfolio_today.today_pl_pct) }}
        {% endif %}
      </span>
    {% endif %}
  </div>
  <div class="mp-card__body">
    {% if portfolio_today %}
      <dl class="mp-recap-stats">
        <div><dt>市值</dt><dd class="mono tnum">${{ "{:,.0f}".format(portfolio_today.market_value or 0) }}</dd></div>
        <div><dt>总成本</dt><dd class="mono tnum">${{ "{:,.0f}".format(portfolio_today.cost or 0) }}</dd></div>
        <div><dt>未实现盈亏</dt>
          {% set upl = (portfolio_today.market_value or 0) - (portfolio_today.cost or 0) %}
          <dd class="mono tnum {% if upl >= 0 %}up{% else %}down{% endif %}">
            {{ "{:+,.0f}".format(upl) }}
          </dd>
        </div>
      </dl>
    {% else %}
      <p class="muted" style="text-align:center; padding:16px;">暂无组合数据</p>
    {% endif %}
  </div>
</section>
```

### `partials/recap_watchlist_perf_card.html`

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">monitoring</span>自选股表现
    </span>
  </div>
  <ul class="mp-recap-perf-list">
    {% for w in watchlist_perf[:10] %}
      <li>
        <a href="/stock/{{ w.ticker }}" class="mp-ticker-link">{{ w.ticker }}</a>
        <span class="mono tnum">${{ "{:.2f}".format(w.price) if w.price else "—" }}</span>
        <span class="mono tnum {% if w.change_pct is not none and w.change_pct >= 0 %}up{% elif w.change_pct is not none %}down{% endif %}">
          {% if w.change_pct is not none %}{{ "{:+.2f}%".format(w.change_pct) }}{% else %}—{% endif %}
        </span>
      </li>
    {% endfor %}
    {% if not watchlist_perf %}
      <li class="muted" style="padding:16px; text-align:center;">暂无自选股</li>
    {% endif %}
  </ul>
</section>
```

### `partials/recap_key_events_card.html`

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">event_note</span>关键事件
    </span>
  </div>
  <ul class="mp-recap-events-list">
    {% for e in key_events %}
      <li class="mp-recap-events__item">
        <span class="mp-recap-events__time mono">{{ e.time or "" }}</span>
        <span class="mp-recap-events__title">{{ e.title }}</span>
        <span class="mp-chip mp-chip--{{ e.kind or 'other' }}">{{ e.kind or 'other' }}</span>
      </li>
    {% endfor %}
    {% if not key_events %}
      <li class="muted" style="padding:16px; text-align:center;">
        {% if row.key_events_json is none %}AI 整理中…{% else %}暂无关键事件{% endif %}
      </li>
    {% endif %}
  </ul>
</section>
```

### `partials/recap_prev_recaps_card.html`

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">history</span>历史复盘
    </span>
    <a href="/recaps" class="mp-card__sub" style="color:var(--ns-primary);">全部 →</a>
  </div>
  <ul class="mp-recap-prev-list">
    {% for p in prev_recaps %}
      <li>
        <a href="/recap/{{ p.recap_date }}" class="mp-recap-prev__date mono">
          {{ p.recap_date.strftime('%m-%d') }}
        </a>
        <span class="muted mp-recap-prev__excerpt">
          {{ (p.ai_commentary_text or "")[:60] }}…
        </span>
      </li>
    {% endfor %}
    {% if not prev_recaps %}
      <li class="muted" style="padding:16px; text-align:center;">暂无历史复盘</li>
    {% endif %}
  </ul>
</section>
```

### `recaps.html` (网格列表)

```html
{% extends "base.html" %}
{% block main_width %}max-w-[2400px]{% endblock %}
{% block nav_width %}max-w-[2400px]{% endblock %}
{% block content %}

<section class="mp-hero">
  <div>
    <span class="mp-eyebrow mp-eyebrow--primary">复盘档案</span>
    <h1 class="grotesk mp-hero__title">Recap History</h1>
    <span class="mp-rule"></span>
    <p class="mp-hero__desc">每日盘后由 AI 自动生成的市场点评。点击进入完整复盘。</p>
  </div>
</section>

<section class="mp-recaps-grid">
  {% for r in rows %}
    <a class="mp-card mp-recaps-card" href="/recap/{{ r.recap_date }}">
      <div class="mp-recaps-card__date grotesk">{{ r.recap_date.strftime('%m-%d') }}</div>
      <div class="muted" style="font-size:11px;">{{ r.recap_date.strftime('%Y') }}</div>
      {% if r.today_pl_dollars is not none %}
        <div class="mono tnum mp-recaps-card__pl {% if r.today_pl_dollars >= 0 %}up{% else %}down{% endif %}">
          {{ "{:+,.0f}".format(r.today_pl_dollars) }}
          {% if r.today_pl_pct is not none %}
            <small>{{ "{:+.2f}%".format(r.today_pl_pct) }}</small>
          {% endif %}
        </div>
      {% else %}
        <div class="muted" style="font-size:11px; margin-top:4px;">无盈亏数据</div>
      {% endif %}
      <p class="mp-recaps-card__summary muted">{{ r.summary or '无摘要' }}…</p>
      <span class="mp-recaps-card__status mp-chip mp-chip--{{ r.generation_status }}">
        {{ r.generation_status }}
      </span>
    </a>
  {% endfor %}
  {% if not rows %}
    <div class="muted" style="grid-column:1/-1; padding:32px; text-align:center;">
      暂无复盘记录
    </div>
  {% endif %}
</section>
{% endblock %}
```

## CSS 新增 (`app.css` 追加)

```css
/* ════════ Phase 5e: /recap layout ════════ */
.mp-recap-hero        { padding:40px 48px 24px;
                        display:flex; align-items:flex-end; justify-content:space-between;
                        gap:48px; border-bottom:1px solid var(--ns-outline-variant); }
.mp-recap-hero__title { font:700 64px/0.95 var(--ns-font-headline);
                        letter-spacing:-0.04em; color:var(--ns-navy); margin:8px 0 6px; }
.mp-recap-hero__desc  { font-size:16px; line-height:1.6; max-width:720px;
                        color:var(--ns-on-surface-variant); margin:16px 0 0; }
.mp-recap-hero__meta  { display:flex; flex-direction:column; align-items:flex-end; gap:10px; }
.mp-recap-hero__status { display:flex; gap:12px; align-items:center;
                         font-size:12px; color:var(--mp-up); font-weight:600; }
.mp-recap-hero__model { font:11.5px/1 var(--ns-font-mono); color:var(--ns-on-surface-variant); }
.mp-recap-hero__actions { display:flex; gap:6px; }

.mp-pulse             { width:8px; height:8px; border-radius:50%; background:var(--mp-up);
                        box-shadow:0 0 0 0 rgba(14,138,95,0.5);
                        animation:mp-pulse 2s infinite; }
@keyframes mp-pulse {
  0%   { box-shadow:0 0 0 0 rgba(14,138,95,0.5); }
  70%  { box-shadow:0 0 0 8px rgba(14,138,95,0); }
  100% { box-shadow:0 0 0 0 rgba(14,138,95,0); }
}

.mp-recap-snap        { padding:20px 48px 24px;
                        display:grid; grid-template-columns:repeat(5,1fr); gap:16px; }
.mp-recap-snap__card  { padding:16px 18px; }
.mp-recap-snap__value { font:600 26px/1 var(--ns-font-mono);
                        letter-spacing:-0.01em; color:var(--ns-navy); margin-top:4px; }
.mp-recap-snap__pct   { font:600 13px/1 var(--ns-font-mono); margin-top:2px;
                        display:flex; align-items:center; gap:4px; }

.mp-recap-body        { padding:0 48px 32px;
                        display:grid;
                        grid-template-columns: minmax(720px, 1.4fr) 720px;
                        gap:56px; }
.mp-recap-article     { max-width:760px; }
.mp-recap-article__head { margin-bottom:24px; }
.mp-recap-article__title { font:700 32px/1.1 var(--ns-font-headline);
                           letter-spacing:-0.03em; color:var(--ns-navy); margin:6px 0; }
.mp-recap-prose       { font-size:17px; line-height:1.85;
                        color:var(--ns-on-surface); }
.mp-recap-prose h2    { font:700 22px/1.2 var(--ns-font-headline);
                        letter-spacing:-0.02em; color:var(--ns-navy);
                        margin:32px 0 14px;
                        display:flex; align-items:center; gap:10px; }
.mp-recap-prose h3    { font:700 18px/1.2 var(--ns-font-headline);
                        color:var(--ns-navy); margin:24px 0 12px; }
.mp-recap-prose p     { margin:14px 0; }
.mp-recap-prose code  { background:var(--ns-surface-container-low);
                        padding:0 6px; font:600 14px var(--ns-font-mono); }
.mp-recap-prose strong { color:var(--ns-navy); }

.mp-recap-rail        { display:flex; flex-direction:column; gap:16px; }

@media (max-width: 1600px) {
  .mp-recap-body      { grid-template-columns: 1fr; }
}
@media (max-width: 900px) {
  .mp-recap-snap      { grid-template-columns: repeat(2, 1fr); }
  .mp-recap-hero      { flex-direction:column; align-items:flex-start; gap:24px; }
}

/* ════════ Phase 5e: Side rail cards ════════ */
.mp-recap-stats              { display:grid; grid-template-columns: 1fr 1fr 1fr; gap:16px;
                               padding:16px; margin:0; }
.mp-recap-stats > div        { display:flex; flex-direction:column; gap:4px; }
.mp-recap-stats dt           { font:600 10px/1 var(--ns-font-headline);
                               letter-spacing:0.08em; text-transform:uppercase;
                               color:var(--ns-on-surface-variant); }
.mp-recap-stats dd           { font:600 16px/1 var(--ns-font-mono); margin:0; }

.mp-recap-perf-list,
.mp-recap-events-list,
.mp-recap-prev-list          { list-style:none; margin:0; padding:8px 16px 14px; }
.mp-recap-perf-list li       { display:grid; grid-template-columns: 60px 1fr 80px;
                               gap:10px; align-items:center; padding:6px 0;
                               border-bottom:1px solid var(--ns-outline-variant); }
.mp-recap-perf-list li:last-child { border-bottom:0; }

.mp-recap-events__item       { display:grid; grid-template-columns: 80px 1fr auto;
                               gap:10px; align-items:center; padding:8px 0;
                               border-bottom:1px solid var(--ns-outline-variant); }
.mp-recap-events__item:last-child { border-bottom:0; }
.mp-recap-events__time       { font-size:11px; color:var(--ns-on-surface-variant); }
.mp-recap-events__title      { font-size:13px; color:var(--ns-navy); }

/* Event kind chips */
.mp-chip--deal      { background:#e0f0ff; color:#0066cc; }
.mp-chip--earnings  { background:#fef3c7; color:#92400e; }
.mp-chip--econ      { background:#ede9fe; color:#5e2cb4; }
.mp-chip--merger    { background:#fce7f3; color:#9d174d; }
.mp-chip--analyst   { background:#d1fae5; color:#065f46; }
.mp-chip--other     { background:var(--ns-surface-container); color:var(--ns-on-surface-variant); }

.mp-recap-prev-list li       { display:flex; gap:10px; padding:8px 0;
                               border-bottom:1px solid var(--ns-outline-variant);
                               align-items:flex-start; }
.mp-recap-prev-list li:last-child { border-bottom:0; }
.mp-recap-prev__date         { font-size:12px; font-weight:600;
                               color:var(--ns-navy); flex:0 0 50px; }
.mp-recap-prev__excerpt      { font-size:12px; line-height:1.4;
                               overflow:hidden; text-overflow:ellipsis; max-height:34px; }

/* ════════ Phase 5e: /recaps grid ════════ */
.mp-recaps-grid              { padding:0 48px 32px;
                               display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr));
                               gap:16px; }
.mp-recaps-card              { padding:18px 20px; text-decoration:none; color:inherit;
                               display:flex; flex-direction:column;
                               transition:box-shadow 200ms; }
.mp-recaps-card:hover        { box-shadow:var(--ns-shadow-hover); }
.mp-recaps-card__date        { font:700 28px/1 var(--ns-font-headline);
                               letter-spacing:-0.02em; color:var(--ns-navy); }
.mp-recaps-card__pl          { font:700 18px/1.1 var(--ns-font-mono); margin-top:10px; }
.mp-recaps-card__pl small    { font-size:11px; opacity:0.7; margin-left:4px; }
.mp-recaps-card__summary     { font-size:12px; line-height:1.5; margin:10px 0 0;
                               overflow:hidden; text-overflow:ellipsis;
                               display:-webkit-box; -webkit-line-clamp:3;
                               -webkit-box-orient:vertical; }
.mp-recaps-card__status      { align-self:flex-start; margin-top:auto; padding-top:10px; }
.mp-chip--ok                 { background:#d1fae5; color:#065f46; }
.mp-chip--pending            { background:#fef3c7; color:#92400e; }
.mp-chip--failed             { background:#fee2e2; color:#991b1b; }
```

## 测试

```
tests/recap/test_prompt_parsing.py             新
  - test_parse_with_valid_marker_and_json
  - test_parse_without_marker_returns_raw
  - test_parse_malformed_json_falls_back_to_none
  - test_parse_events_not_a_list_falls_back
  - test_parse_strips_trailing_whitespace_in_commentary

tests/recap/test_recap_service_generate.py     扩展
  - test_generate_saves_commentary_and_key_events_separately
  - test_generate_falls_back_when_ai_no_marker

tests/web/test_recap.py                        扩展
  # 现有
  # 新增视觉锚点 + 数据
  - test_recap_hero_renders_date_h1
  - test_recap_hero_4_action_buttons_present
  - test_recap_hero_toast_buttons_have_onclick
  - test_recap_market_snap_5_cards_when_data_present
  - test_recap_market_snap_empty_state_when_no_data
  - test_recap_article_renders_markdown_h2_h3
  - test_recap_portfolio_today_card_renders
  - test_recap_watchlist_perf_card_renders
  - test_recap_key_events_card_renders_chips
  - test_recap_key_events_card_empty_state
  - test_recap_prev_recaps_card_excludes_current_date
  - test_recap_404_unchanged
  - test_recap_retry_button_htmx_target_body

tests/web/test_recaps.py                       新
  - test_recaps_grid_renders_mp_recaps_card
  - test_recaps_grid_shows_pl_when_data_present
  - test_recaps_grid_handles_missing_pl
  - test_recaps_grid_status_chips_color_coded
  - test_recaps_grid_empty_state
```

## 错误处理

| 场景 | 处理 |
|------|------|
| AI 输出无 `KEY_EVENTS_JSON:` 标记 | 整段当 commentary,events=None |
| AI 输出 `KEY_EVENTS_JSON:` 但 JSON 解析失败 | commentary 保留,events=None |
| AI 输出 events 不是 list | 同上,events=None |
| `market_summary_json` JSON 解析失败 | snap 卡显空 state |
| `watchlist_performance_json` 失败 | 同上 |
| `holdings_totals_json` 失败 | portfolio_today 卡显空 |
| 当日 `DailyRecap` 不存在 | 404 (现有行为) |
| 重新生成 AI 调用失败 | retry route 现有错误处理保留;`generation_status=failed`,模板 hero 显红色 "生成失败" |

## 性能

- `/recap/{date}` 一次 query + 一次 `prev_recaps` query (limit 6) + 4 次 JSON parse。总耗时 < 50ms
- `/recaps` 一次 query (limit 60) + N 次 holdings_totals_json parse。< 100ms
- 无外部 API 调用 (AI 仅在 retry 路径)
- Markdown 渲染 commentary 文长 ~500-1000 字符,< 5ms

## 风险 / 兼容性

| 风险 | 缓解 |
|---|---|
| 旧复盘 (v3 prompt) 是纯文本,新模板按 Markdown 渲染 | `markdown` filter 对纯文本 fallback 为单段 `<p>`,视觉 OK |
| 旧复盘 `key_events_json IS NULL` | 模板 fallback "AI 整理中…" or "暂无关键事件" |
| AI v4 prompt 不守 schema | `_parse_ai_output` 容错,失败时 commentary 仍然保存,events=None |
| `KEY_EVENTS_JSON:` 标记在 commentary 文本内出现(极小概率) | 用 `raw.index(marker)` 取第一个出现位置,commentary 内 inline 提及的会被切掉 — 接受这一风险 |
| Migration 失败 | NULL-able 字段,零数据风险 |
| 老 POST `/recap/{date}/retry` caller | hero 按钮用 hx-post,与现有保持一致;无 schema 变化 |
| `market_summary_json` 实际 shape 与模板假设不符 | 实施 Task 1 前,先 grep `RecapService.generate` 中 `market_summary_json` dump 方式,确认 shape 后调整模板 |

## Out of Scope (本阶段不做)

- 分享/置顶/推送至订阅者实际功能 (仅装饰 + toast)
- 关键事件外部数据源 (yfinance/Tencent calendar API)
- 复盘自动定时生成 (现有 scheduler 不动)
- 主页 `recap_card.html` (不动)
- 移动端适配 < 768px
- 多用户/订阅者系统
