# Phase 5d — `/holdings` 页 NineScrolls 重做

> 设计来源：`docs/design/mockups/page-holdings.jsx`
> 上一阶段：Phase 5c (`/trades`) 已落地
> 目标分支：`feat/phase-5d-holdings-page`

## Goal

把 `/holdings` 页从 194 行老 Tailwind 重做成 NineScrolls 设计语言，并补齐设计稿规定的 hero / 5-KPI / 3-card row / 14 列表格 / 月度柱图 / AI 风险分析 6 项数据/视觉能力。

## Non-Goals

- 移动端 < 768px 适配
- 表格列头排序交互（决策点 4：B 只做导出）
- 表格筛选抽屉
- 板块手工 override（决策点 2：纯 yfinance 自动）
- AI 风险分析持久化缓存（决策点 3：B 每次后台跑；缓存留给 5d.1）
- Holdings vs S&P/Nasdaq 比较曲线
- Real-time 推送 / WebSocket
- `/recap` 重做（Phase 5e）

## Architecture

四层叠加：

```
┌─────────────────────────────────────────────────────────────────┐
│ 模板层 (Jinja2 + HTMX)                                          │
│   holdings.html (hero + KPI + 3-card row + table + bottom)      │
│   8 个新 partial + 1 个重写 (holdings_table.html)               │
├─────────────────────────────────────────────────────────────────┤
│ 路由层 (FastAPI)                                                │
│   GET  /holdings              扩展 context dict                 │
│   GET  /holdings/export.csv   新增                              │
│   GET  /holdings/risk-analysis 改 POST→GET (HTMX hx-trigger)    │
├─────────────────────────────────────────────────────────────────┤
│ 聚合层 (service.py + 扩展)                                       │
│   today_portfolio_change(rows) [新]                             │
│   contributors_ranked(rows, top_n=5) [新]                       │
│   sector_breakdown(rows) [新]                                   │
│   enrich_holdings 加 sector/today_change_pct/sparkline 字段     │
├─────────────────────────────────────────────────────────────────┤
│ 板块模块 (marketpulse/holdings/sector.py) [新]                   │
│   get_sector(ticker) — yfinance .info['sector'] + 24h cache     │
│   backfill_holding_sectors(session) — lazy fill NULL columns    │
└─────────────────────────────────────────────────────────────────┘
```

DB migration：`Holding.sector TEXT NULL`。

## Tech Stack

- 后端：FastAPI + SQLAlchemy 2.x (existing)
- 模板：Jinja2 (existing)
- 前端：HTMX (existing)
- 样式：vanilla CSS via `app.css`，复用 `mp-card` / `mp-kpi` / `mp-eyebrow` / `mp-hero` 等 5b/5c 已建立的 token
- 图标：Material Symbols Outlined
- 字体：Space Grotesk / Inter / Roboto Mono
- 第三方：`yfinance` (existing 依赖)，无新增 lib

## 决策记录

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| 1 | 范围 | A · 完整 5d | 跟 5c 同节奏；后端 80% 已有 |
| 2 | Sector 数据 | A · yfinance `.info['sector']` 自动 | 免维护；fallback "未分类" |
| 3 | AI 风险触发 | B · HTMX `hx-trigger="load"` 后台 | 不阻塞页面；不需新表 |
| 4 | 表格按钮 | B · 只做导出 | 排序/筛选 over-engineering（10 行表） |

## 路由 / 端点

### `GET /holdings`

已存在，扩展 context dict（无新 query 参数）。

**返回 context：**

```python
{
  # 现有 (保留)
  "rows": rows,                      # enriched holdings, 含新字段
  "ranked_rows": ranked_rows,        # 按 pl_impact 排序
  "totals": totals,                  # {cost, market_value}
  "realized_pl": float,
  "total_dividends": float,
  "monthly_pl": list,                # 现有 (no months arg, all-time)
  "monthly_dividends": list,
  "allocation": list,                # 现有, by ticker
  "trade_stats": trading_stats(db),

  # 新增
  "kpi": {
    "today_change": {
      "dollars": float,              # 净美元变化
      "pct": float,                  # 加权 %
      "up_count": int, "down_count": int,
    },
    "ytd_realized": float,           # Jan 1 到 today 已实现 P&L
  },
  "contributors": list[dict],        # top 5 P&L impact, mix pos+neg
  "sectors": list[dict],             # by sector aggregation
}
```

**行内每个 row 的新字段：**

```python
row["sector"] = h.sector or "未分类"
row["today_change_pct"] = quote.change_pct or None    # quote 已有
row["sparkline"] = [bar.close, ...]                   # last 30d closes
```

HX-Request header 不参与（页面没 HTMX 局部刷）。

### `GET /holdings/export.csv` [新]

无 query 参数（导出全部 holdings，没有过滤需要）。

Headers:
```
Content-Type: text/csv; charset=utf-8
Content-Disposition: attachment; filename="holdings-YYYY-MM-DD.csv"
```

输出列：
```
ticker,name,sector,quantity,avg_cost,current_price,market_value,cost_basis,unrealized_pl,unrealized_pl_pct,dividends_received
```

使用 `StreamingResponse` + generator (5c 同模式)。

### `GET /holdings/risk-analysis` [改造]

**注：** 老路由是 `POST /holdings/risk-analysis`（form-driven，触发后返回 markdown HTML）。本阶段改成 **GET**（无副作用、可被 HTMX `hx-trigger="load"` 调用）。

老 POST 路由如果有其他 caller（搜全代码库），保留 alias 直到下个 phase。

```python
@router.get("/holdings/risk-analysis", response_class=HTMLResponse)
def holdings_risk_analysis_get(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """HTMX endpoint: returns the AI risk analysis card as outerHTML
    replacement. Called by hx-trigger='load' on the placeholder."""
    # Existing AI analysis logic (call Anthropic with portfolio context)
    # Render `partials/holdings_risk_card.html` with markdown → HTML
```

**错误处理：** Anthropic 调用失败 → 返回友好 fallback card（"AI 服务暂时不可用,请稍后重试" + 重试按钮），HTTP 200（不让 HTMX 因为 4xx/5xx 留空）。

### `POST /dividends` / `GET /dividends` / `DELETE /dividends/{id}` / `DELETE /holdings/{id}`

现有，不动。

## 聚合层

### `marketpulse/holdings/sector.py` [新]

```python
"""yfinance sector lookup with 24h in-memory cache + DB persistence."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Process-level cache: ticker → (sector_or_None, fetched_at)
_cache: dict[str, tuple[str | None, datetime]] = {}
_TTL = timedelta(hours=24)


def get_sector(ticker: str) -> str | None:
    """Lookup sector from yfinance .info['sector'], cached 24h.

    Returns None when fetch fails or sector key is missing.
    Caller decides whether to fall back to '未分类'.
    """
    now = datetime.now(UTC)
    cached = _cache.get(ticker)
    if cached and (now - cached[1]) < _TTL:
        return cached[0]
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        sector = info.get("sector") or None
    except Exception:
        sector = None
    _cache[ticker] = (sector, now)
    return sector


def backfill_holding_sectors(session: Session) -> int:
    """Fill Holding.sector for rows where it's NULL. Idempotent.

    Returns count of rows newly filled. Safe to call on every
    /holdings render — only touches NULL rows.
    """
    from marketpulse.db.models import Holding
    holdings = session.query(Holding).filter(Holding.sector.is_(None)).all()
    n = 0
    for h in holdings:
        sec = get_sector(h.ticker)
        if sec:
            h.sector = sec
            n += 1
    if n > 0:
        session.commit()
    return n
```

### `marketpulse/holdings/service.py` 扩展

#### `enrich_holdings()` 加 3 字段

```python
def enrich_holdings(
    holdings: list[Holding],
    data: DataService,
) -> list[dict[str, Any]]:
    """... existing logic ...

    Phase 5d additions to each row:
    - sector: h.sector or "未分类"
    - today_change_pct: quote.change_pct or None
    - sparkline: list[float] last 30 daily closes, [] on fetch fail
    """
    rows = []
    for h in holdings:
        quote = data.get_quote(h.ticker)
        market_value = h.quantity * quote.price
        cost_basis = h.quantity * h.avg_cost
        row = {
            "ticker": h.ticker,
            "name": getattr(quote, "name", None) or h.ticker,
            "sector": h.sector or "未分类",
            "quantity": h.quantity,
            "avg_cost": h.avg_cost,
            "current_price": quote.price,
            "today_change_pct": getattr(quote, "change_pct", None),
            "market_value": market_value,
            "cost_basis": cost_basis,
            "pl_dollars": market_value - cost_basis,
            "pl_pct": ((market_value - cost_basis) / cost_basis * 100) if cost_basis else 0.0,
            "sparkline": _fetch_sparkline(data, h.ticker),
        }
        rows.append(row)
    return rows


def _fetch_sparkline(data: DataService, ticker: str) -> list[float]:
    """Return last 30 daily closes; [] on fetch failure."""
    try:
        bars = data.get_history(ticker, period="30d")
        return [b.close for b in bars[-30:]]
    except Exception:
        return []
```

#### 新增 `today_portfolio_change`

```python
def today_portfolio_change(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate today's portfolio change.

    Returns:
      dollars: sum of (market_value * today_change_pct/100) for rows
               with non-None today_change_pct
      pct: weighted by market_value
      up_count: rows with today_change_pct > 0
      down_count: rows with today_change_pct < 0

    Rows with today_change_pct=None are excluded from up/down/dollars
    but counted in totals (so % stays accurate over included rows).
    """
    eligible = [r for r in rows if r.get("today_change_pct") is not None]
    if not eligible:
        return {"dollars": 0.0, "pct": 0.0, "up_count": 0, "down_count": 0}

    dollars = sum(r["market_value"] * r["today_change_pct"] / 100 for r in eligible)
    total_mv = sum(r["market_value"] for r in eligible)
    pct = (dollars / total_mv * 100) if total_mv else 0.0
    up_count = sum(1 for r in eligible if r["today_change_pct"] > 0)
    down_count = sum(1 for r in eligible if r["today_change_pct"] < 0)
    return {
        "dollars": dollars,
        "pct": pct,
        "up_count": up_count,
        "down_count": down_count,
    }
```

#### 新增 `contributors_ranked`

```python
def contributors_ranked(
    rows: list[dict[str, Any]],
    *,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """Top N rows by |pl_dollars| (positive + negative contributors).

    Reuses sort_by_pl_impact ordering (already places top gains and
    top losses at the top), then slices first top_n.
    """
    ranked = sort_by_pl_impact(rows)
    return ranked[:top_n]
```

#### 新增 `sector_breakdown`

```python
def sector_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group rows by sector.

    Returns: [{sector, market_value, pct, holding_count}, ...]
    sorted by market_value desc. '未分类' falls naturally to its own bucket.
    """
    from collections import defaultdict
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"market_value": 0.0, "holding_count": 0},
    )
    for r in rows:
        s = r["sector"]
        buckets[s]["market_value"] += r["market_value"]
        buckets[s]["holding_count"] += 1
    total = sum(b["market_value"] for b in buckets.values())
    out = [
        {
            "sector": sector,
            "market_value": v["market_value"],
            "pct": (v["market_value"] / total * 100) if total else 0.0,
            "holding_count": v["holding_count"],
        }
        for sector, v in buckets.items()
    ]
    out.sort(key=lambda x: x["market_value"], reverse=True)
    return out
```

## DB Migration

```python
# alembic/versions/2026_05_15_add_holding_sector.py
"""add Holding.sector column

Revision ID: <auto>
Revises: <previous>
Create Date: 2026-05-15
"""
from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column("holding", sa.Column("sector", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("holding", "sector")
```

`Holding.sector` 默认 NULL — `backfill_holding_sectors` 在 `/holdings` 渲染时填。

## 模板架构

### 文件清单

```
marketpulse/web/templates/
├── holdings.html                              重写
└── partials/
    ├── holdings_hero.html                     新
    ├── holdings_donut.html                    新 (嵌在 hero 右栏)
    ├── holdings_kpi_strip.html                新
    ├── holdings_allocation_card.html          新
    ├── holdings_sector_card.html              新
    ├── holdings_contributors_card.html        新
    ├── holdings_table.html                    重写 (14 列)
    ├── holdings_monthly_card.html             新 (复用 5c 月度柱图样式)
    └── holdings_risk_card.html                新 (AI 分析卡)
```

### `holdings.html` 整体

```html
{% extends "base.html" %}
{% block main_width %}max-w-[2400px]{% endblock %}
{% block nav_width %}max-w-[2400px]{% endblock %}
{% block content %}

{% include "partials/holdings_hero.html" %}

<section class="mp-holdings-kpi">
  {% include "partials/holdings_kpi_strip.html" %}
</section>

<section class="mp-holdings-row3">
  {% include "partials/holdings_allocation_card.html" %}
  {% include "partials/holdings_sector_card.html" %}
  {% include "partials/holdings_contributors_card.html" %}
</section>

<section class="mp-holdings-table">
  <div id="holdings-container">
    {% include "partials/holdings_table.html" %}
  </div>
</section>

<section class="mp-holdings-bottom">
  {% include "partials/holdings_monthly_card.html" %}
  <div id="holdings-risk-card"
       hx-get="/holdings/risk-analysis"
       hx-trigger="load"
       hx-swap="outerHTML">
    <section class="mp-card">
      <div class="mp-card__head">
        <span class="mp-card__title">
          <span class="material-symbols-outlined">auto_awesome</span>AI 风险分析
        </span>
      </div>
      <div class="mp-card__body mp-risk-loading">
        <span class="muted">正在分析…</span>
      </div>
    </section>
  </div>
</section>

{% endblock %}
```

### `partials/holdings_hero.html`

```html
<section class="mp-holdings-hero">
  <div class="mp-holdings-hero__main">
    <span class="mp-eyebrow mp-eyebrow--primary">投资组合</span>
    <h1 class="grotesk mp-holdings-hero__title">Holdings · Portfolio Overview</h1>
    <span class="mp-rule"></span>
    <div class="mp-holdings-hero__stats">
      <div>
        <span class="mp-eyebrow">总市值 · USD</span>
        <div class="mp-holdings-hero__mv-value mono tnum">
          {{ "{:,.0f}".format(totals.market_value) }}
        </div>
      </div>
      <div>
        <span class="mp-eyebrow">未实现盈亏</span>
        {% set pl = totals.market_value - totals.cost %}
        {% set pl_pct = (pl / totals.cost * 100) if totals.cost else 0 %}
        <div class="mp-holdings-hero__pl-value grotesk tnum {% if pl >= 0 %}up{% else %}down{% endif %}">
          {{ "{:+,.0f}".format(pl) }}
        </div>
        <div class="mono tnum {% if pl >= 0 %}up{% else %}down{% endif %}" style="font-size:14px; font-weight:600; margin-top:2px;">
          {{ "{:+.2f}%".format(pl_pct) }}
        </div>
      </div>
      <div>
        <span class="mp-eyebrow">今日</span>
        {% set tc = kpi.today_change %}
        <div class="grotesk tnum {% if tc.dollars >= 0 %}up{% else %}down{% endif %}"
             style="font-size:32px; font-weight:700; letter-spacing:-0.02em; line-height:1.05;">
          {{ "{:+,.0f}".format(tc.dollars) }}
        </div>
        <div class="mono tnum {% if tc.dollars >= 0 %}up{% else %}down{% endif %}"
             style="font-size:14px; font-weight:600; margin-top:2px;">
          {{ "{:+.2f}%".format(tc.pct) }} · {{ tc.up_count }} 涨 {{ tc.down_count }} 跌
        </div>
      </div>
    </div>
  </div>
  <aside class="mp-holdings-hero__donut">
    {% include "partials/holdings_donut.html" %}
  </aside>
</section>
```

### `partials/holdings_donut.html`

```html
{% set total = allocation | sum(attribute='value') %}
{% set palette = ['#0066cc', '#022448', '#0e8a5f', '#c0392b', '#9b59b6', '#16a085', '#c0570c', '#4d94ff'] %}
<div class="mp-donut">
  <svg viewBox="0 0 100 100" width="160" height="160">
    {% set ns = namespace(offset=0) %}
    {% for slice in allocation[:8] %}
      {% set pct = (slice.value / total * 100) if total else 0 %}
      {% set dasharray = pct * 2.513 %}
      <circle cx="50" cy="50" r="40" fill="none"
              stroke="{{ palette[loop.index0 % palette|length] }}" stroke-width="14"
              stroke-dasharray="{{ "%.2f"|format(dasharray) }} 251.3"
              stroke-dashoffset="{{ "%.2f"|format(-ns.offset * 2.513) }}"
              transform="rotate(-90 50 50)" />
      {% set ns.offset = ns.offset + pct %}
    {% endfor %}
  </svg>
  <div class="mp-donut__legend">
    <span class="mp-eyebrow mp-eyebrow--primary">主要构成</span>
    {% for slice in allocation[:5] %}
      <div class="mp-donut__legend-row">
        <span class="mp-donut__legend-swatch" style="background:{{ palette[loop.index0 % palette|length] }};"></span>
        <span class="grotesk" style="font-weight:700; font-size:12px; color:var(--ns-navy); flex:1;">{{ slice.ticker }}</span>
        <span class="mono tnum muted" style="font-size:12px;">{{ "%.1f%%"|format((slice.value / total * 100) if total else 0) }}</span>
      </div>
    {% endfor %}
  </div>
</div>
```

### `partials/holdings_kpi_strip.html`

5 卡片：
| label | value | hint | icon | color |
|---|---|---|---|---|
| 总成本 · 含手续费 | `{:,.0f}\|format(totals.cost)` | `{} 笔交易累计 \| trade_stats.total_trades` | `payments` | navy |
| 市值 | `{:,.0f}\|format(totals.market_value)` | 实时 | `account_balance_wallet` | navy |
| 未实现盈亏 | `{:+,.0f}\|format(pl)` | `{:+.2f}% \| pl_pct` | `trending_up` | up/down |
| 已实现盈亏 · YTD | `{:+,.0f}\|format(kpi.ytd_realized)` | `胜率 {:.1f}% \| trade_stats.win_rate_pct` (or — when None) | `payments` | up/down |
| 累计分红 | `{:+,.0f}\|format(total_dividends)` | 含本月 (TBD: 本月分红) | `redeem` | up |

格式：用 `"{:+,.0f}".format(...)` 新式格式化（5c 已确立该模式）。

### `partials/holdings_table.html`

14 列：

```html
<table class="mp-table mp-table--holdings">
  <thead>
    <tr>
      <th class="col-ticker">代码</th>
      <th class="col-name">名称</th>
      <th class="col-sector">板块</th>
      <th class="col-qty">数量</th>
      <th class="col-avg">均价</th>
      <th class="col-price">现价</th>
      <th class="col-today">今日%</th>
      <th class="col-cost">总成本</th>
      <th class="col-mv">市值</th>
      <th class="col-pl">未实现盈亏</th>
      <th class="col-plpct">盈亏 %</th>
      <th class="col-spark">30日</th>
      <th class="col-alloc">占组合</th>
      <th class="col-actions"></th>
    </tr>
  </thead>
  <tbody>
    {% for r in rows %}
      <tr id="holding-row-{{ r.ticker }}">
        <td class="col-ticker"><a href="/stock/{{ r.ticker }}" class="mp-ticker-link">{{ r.ticker }}</a></td>
        <td class="col-name muted">{{ r.name }}</td>
        <td class="col-sector"><span class="mp-chip">{{ r.sector }}</span></td>
        <td class="col-qty mono tnum">{{ "%g"|format(r.quantity) }}</td>
        <td class="col-avg mono tnum">${{ "%.2f"|format(r.avg_cost) }}</td>
        <td class="col-price mono tnum">${{ "%.2f"|format(r.current_price) }}</td>
        <td class="col-today mono tnum {% if r.today_change_pct is not none and r.today_change_pct >= 0 %}up{% elif r.today_change_pct is not none %}down{% endif %}">
          {% if r.today_change_pct is not none %}{{ "{:+.2f}%".format(r.today_change_pct) }}{% else %}—{% endif %}
        </td>
        <td class="col-cost mono tnum">${{ "{:,.2f}".format(r.cost_basis) }}</td>
        <td class="col-mv mono tnum">${{ "{:,.2f}".format(r.market_value) }}</td>
        <td class="col-pl mono tnum {% if r.pl_dollars >= 0 %}up{% else %}down{% endif %}">{{ "{:+,.2f}".format(r.pl_dollars) }}</td>
        <td class="col-plpct mono tnum {% if r.pl_dollars >= 0 %}up{% else %}down{% endif %}">{{ "{:+.2f}%".format(r.pl_pct) }}</td>
        <td class="col-spark">
          {% if r.sparkline and r.sparkline|length >= 2 %}
            <svg class="mp-holdings__spark" width="64" height="22" viewBox="0 0 64 22" preserveAspectRatio="none">
              <polyline points="{{ r.sparkline | sparkpoints(64, 22) }}"
                        fill="none"
                        stroke="{% if r.pl_dollars >= 0 %}var(--mp-up){% else %}var(--mp-down){% endif %}"
                        stroke-width="1.5" />
            </svg>
          {% else %}<span class="muted">—</span>{% endif %}
        </td>
        <td class="col-alloc">
          {% set allo_pct = (r.market_value / totals.market_value * 100) if totals.market_value else 0 %}
          <div class="mp-holdings__allo-bar">
            <div style="width: {{ allo_pct }}%;"></div>
          </div>
          <span class="mono tnum muted" style="font-size:11px; margin-left:8px;">{{ "%.1f%%"|format(allo_pct) }}</span>
        </td>
        <td class="col-actions">
          <button class="mp-icon-btn"
                  hx-delete="/holdings/{{ r.ticker }}"
                  hx-confirm="删除 {{ r.ticker }} 的所有交易和持仓?">
            <span class="material-symbols-outlined">delete_outline</span>
          </button>
        </td>
      </tr>
    {% endfor %}
    {% if not rows %}
      <tr><td colspan="14" class="mp-empty-row">暂无持仓。先在 <a href="/trades">/trades</a> 添加交易。</td></tr>
    {% endif %}
  </tbody>
  {% if rows %}
  <tfoot>
    <tr class="mp-table__totals">
      <td colspan="7"><span class="grotesk" style="font-weight:700; font-size:12px; letter-spacing:0.04em; color:var(--ns-navy);">合计 · {{ rows|length }} 个标的</span></td>
      <td class="mono tnum" style="font-weight:700; color:var(--ns-navy);">${{ "{:,.0f}".format(totals.cost) }}</td>
      <td class="mono tnum" style="font-weight:700; color:var(--ns-navy);">${{ "{:,.0f}".format(totals.market_value) }}</td>
      {% set pl = totals.market_value - totals.cost %}
      {% set pl_pct = (pl / totals.cost * 100) if totals.cost else 0 %}
      <td class="mono tnum {% if pl >= 0 %}up{% else %}down{% endif %}" style="font-weight:700;">{{ "{:+,.0f}".format(pl) }}</td>
      <td class="mono tnum {% if pl >= 0 %}up{% else %}down{% endif %}" style="font-weight:700;">{{ "{:+.2f}%".format(pl_pct) }}</td>
      <td colspan="3"></td>
    </tr>
  </tfoot>
  {% endif %}
</table>
```

### `partials/holdings_allocation_card.html`

按 ticker 列表 + bar：

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">donut_small</span>持仓分布 · 按代码
    </span>
  </div>
  <ul class="mp-allocation-list">
    {% set max_val = allocation | map(attribute='value') | max if allocation else 0 %}
    {% for r in allocation %}
      <li class="mp-allocation-row">
        <span class="grotesk" style="font-weight:700; font-size:13px; color:var(--ns-navy); width:60px;">{{ r.ticker }}</span>
        <div class="mp-allocation-bar">
          <div style="width: {{ (r.value / max_val * 100) if max_val else 0 }}%; background:var(--ns-navy);"></div>
        </div>
        <span class="mono tnum muted" style="font-size:11.5px; margin-left:auto;">
          {{ "%.1f%%"|format(r.pct) }} · ${{ "{:,.0f}".format(r.value) }}
        </span>
      </li>
    {% endfor %}
  </ul>
</section>
```

### `partials/holdings_sector_card.html`

类似 allocation card，但用 `sectors` 数据。

### `partials/holdings_contributors_card.html`

类似 leaderboard，用 `contributors` 数据；每行显示 ticker + 简化的 P&L 美元数 + 占组合 bar。

### `partials/holdings_monthly_card.html`

复用 5c 的月度柱图样式（`mp-monthly-bars`），数据用 `monthly_pl`。

### `partials/holdings_risk_card.html`

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">auto_awesome</span>AI 风险分析
    </span>
    <span class="mp-card__sub">{{ generated_at | default('刚刚生成') }}</span>
  </div>
  <div class="mp-card__body mp-prose">
    {{ analysis_markdown | render_markdown | safe }}
  </div>
</section>
```

`render_markdown` 是现有 Jinja filter（Phase 5b 已有）。

## CSS 新增 (`app.css` 追加)

```css
/* ════════ Phase 5d: /holdings layout ════════ */
.mp-holdings-hero        { padding:32px 48px 24px;
                           display:grid; grid-template-columns:1fr 360px; gap:48px;
                           align-items:flex-start; }
.mp-holdings-hero__title { font:700 48px/1 var(--ns-font-headline);
                           letter-spacing:-0.04em; color:var(--ns-navy); margin:6px 0 0; }
.mp-holdings-hero__stats { display:flex; align-items:flex-end; gap:48px; margin-top:28px; }
.mp-holdings-hero__mv-value { font:600 60px/1 var(--ns-font-mono); letter-spacing:-0.04em;
                              color:var(--ns-navy); }
.mp-holdings-hero__pl-value { font:700 32px/1.05 var(--ns-font-headline);
                              letter-spacing:-0.02em; }

.mp-holdings-kpi         { padding:0 48px 16px;
                           display:grid; grid-template-columns:repeat(5,1fr); gap:16px; }
.mp-holdings-row3        { padding:16px 48px 16px;
                           display:grid; grid-template-columns:1.4fr 1fr 1.4fr; gap:16px; }
.mp-holdings-table       { padding:0 48px 16px; }
.mp-holdings-bottom      { padding:0 48px 32px;
                           display:grid; grid-template-columns:1fr 1fr; gap:16px; }

@media (max-width: 1600px) {
  .mp-holdings-row3      { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 1440px) {
  .mp-holdings-hero      { grid-template-columns: 1fr; }
  .mp-holdings-hero__donut { max-width: 360px; }
  .mp-holdings-row3      { grid-template-columns: 1fr; }
  .mp-holdings-bottom    { grid-template-columns: 1fr; }
}
@media (max-width: 900px) {
  .mp-holdings-kpi       { grid-template-columns: repeat(2, 1fr); }
}

/* ════════ Donut ════════ */
.mp-donut                { display:flex; align-items:center; gap:20px; }
.mp-donut__legend        { display:flex; flex-direction:column; gap:6px; flex:1; }
.mp-donut__legend-row    { display:flex; align-items:center; gap:8px; font-size:12px; }
.mp-donut__legend-swatch { width:10px; height:10px; border-radius:2px; }

/* ════════ Holdings table ════════ */
#holdings-container      { overflow-x: auto; }
.mp-table--holdings      { min-width: 1400px; width:100%; border-collapse:collapse; }
.mp-table--holdings th   { font:600 10px/1 var(--ns-font-headline);
                           letter-spacing:0.08em; text-transform:uppercase;
                           color:var(--ns-on-surface-variant); padding:10px 12px;
                           border-bottom:1px solid var(--ns-outline-variant);
                           white-space:nowrap; text-align:left; }
.mp-table--holdings td   { padding:12px; font-size:13px;
                           border-bottom:1px solid var(--ns-outline-variant);
                           vertical-align:middle; }
.mp-table--holdings tbody tr:hover { background: var(--ns-surface-container-low); }
.mp-table--holdings .col-qty,
.mp-table--holdings .col-avg,
.mp-table--holdings .col-price,
.mp-table--holdings .col-today,
.mp-table--holdings .col-cost,
.mp-table--holdings .col-mv,
.mp-table--holdings .col-pl,
.mp-table--holdings .col-plpct { text-align:right; }
.mp-table--holdings .up   { color:var(--mp-up); font-weight:600; }
.mp-table--holdings .down { color:var(--mp-down); font-weight:600; }
.mp-table--holdings .muted{ color:var(--ns-on-surface-variant); }
.mp-table--holdings tfoot td { background:var(--ns-surface-container-low);
                               border-top:2px solid var(--ns-outline-variant); }

.mp-holdings__spark      { display:block; }
.mp-holdings__allo-bar   { display:inline-block; width:80px; height:8px;
                           background:var(--ns-surface-container); border-radius:2px;
                           position:relative; overflow:hidden; vertical-align:middle; }
.mp-holdings__allo-bar > div { position:absolute; left:0; top:0; bottom:0; }

/* ════════ Allocation / Sector / Contributor lists ════════ */
.mp-allocation-list      { list-style:none; margin:0; padding:10px 16px 18px; }
.mp-allocation-row       { display:flex; align-items:center; gap:10px; padding:7px 0; }
.mp-allocation-bar       { flex:1; height:8px; background:var(--ns-surface-container);
                           border-radius:2px; position:relative; overflow:hidden; }
.mp-allocation-bar > div { position:absolute; left:0; top:0; bottom:0; }

/* ════════ Risk card loading state ════════ */
.mp-risk-loading         { min-height:200px; display:flex;
                           align-items:center; justify-content:center; }
.mp-prose                { font-size:14px; line-height:1.7; color:var(--ns-on-surface);
                           padding:20px; }
.mp-prose h2, .mp-prose h3 { font-family:var(--ns-font-headline);
                              color:var(--ns-navy); margin-top:16px; }
.mp-prose p              { margin:8px 0; }
.mp-prose ul             { padding-left:24px; }
```

## HTMX 交互

| 触发 | hx-* | 目标 |
|------|------|------|
| AI 风险分析 | `hx-get="/holdings/risk-analysis"` `hx-trigger="load"` `hx-swap="outerHTML"` | `#holdings-risk-card` |
| 删除分红 | 现有 (Phase 5c 已让 dividends_delete 返回 trades_table partial) | n/a 跳 /trades |
| 删除 holding | 现有 `hx-delete="/holdings/{ticker}"` | `holding-row-{ticker}` (outerHTML) |
| 导出 CSV | 普通 `<a href="/holdings/export.csv">`，非 HTMX | 下载 |

## 错误处理

| 场景 | 处理 |
|------|------|
| yfinance sector 失败 | `get_sector` 返回 None → "未分类" |
| `quote.change_pct` 缺失 | 行显示 "—" |
| 30 日 sparkline 数据少于 2 点 | 模板 `{% if r.sparkline\|length >= 2 %}` 跳过 SVG |
| AI 风险分析 Anthropic 失败 | route 返回 fallback card（"暂时不可用"），HTTP 200 |
| sector backfill 慢（首次） | 用户首次访问慢一点（≤ 3s），之后 24h cache hit |
| 空 holdings | 表格"暂无持仓"empty state；KPI 全 0；右栏卡片显空状态 |
| `Holding.sector` migration 失败 | 数据无破坏（NULL-able 字段） |

## 测试

```
tests/holdings/test_sector.py             新
  - test_get_sector_returns_yfinance       mock yf.Ticker → "Technology"
  - test_get_sector_returns_none_on_fail   yf.Ticker 抛错 → None
  - test_get_sector_caches_24h             连调 2 次只触发 1 次 fetch
  - test_get_sector_cache_expires          时间快进 25h → 重新 fetch
  - test_backfill_only_fills_null          NULL → 填; 已有 → 不动
  - test_backfill_returns_count
  - test_backfill_idempotent

tests/holdings/test_aggregations.py       扩展
  - test_today_portfolio_change_up_down    9 涨 1 跌 → up_count=9 down_count=1
  - test_today_portfolio_change_dollars    sum(mv * pct/100) 正确
  - test_today_portfolio_change_empty      全 None → 全 0
  - test_today_portfolio_change_pct_weighted_by_mv  按市值加权
  - test_contributors_ranked_top_n         5+ → 5
  - test_contributors_ranked_includes_neg  pos + neg 都参与
  - test_sector_breakdown_groups           2 sector → 2 行
  - test_sector_breakdown_unclassified     None → "未分类"
  - test_sector_breakdown_pct_sums_to_100  浮点容差

tests/web/test_holdings.py                扩展
  # 现有 (保留)
  # 新增视觉锚点
  - test_holdings_hero_renders             h1 + mp-rule + "Holdings · Portfolio Overview"
  - test_holdings_kpi_5_cards              .mp-kpi__value == 5
  - test_holdings_row3_3_cards
  - test_holdings_table_14_columns         <th> >= 14
  - test_holdings_donut_renders            <svg> + circle >= 1
  - test_holdings_sector_column_falls_back row.sector=None → "未分类"
  - test_holdings_table_sparkline_column   <polyline> per row
  - test_holdings_table_allocation_bar     .mp-holdings__allo-bar count == rows
  - test_holdings_risk_card_placeholder    hx-get="/holdings/risk-analysis" + 正在分析
  - test_holdings_tfoot_totals             合计行存在且数字正确

tests/web/test_holdings_risk.py           新
  - test_risk_analysis_get_returns_card    GET → <section class="mp-card">
  - test_risk_analysis_renders_markdown    h2/p tags from markdown
  - test_risk_analysis_handles_anthropic_error  Anthropic raises → fallback card 200

tests/web/test_holdings_export.py         新
  - test_export_csv_content_type
  - test_export_csv_filename               holdings-YYYY-MM-DD.csv
  - test_export_csv_header_row             ticker,name,sector,quantity,...
  - test_export_csv_n_data_rows            N holdings → N+1 rows (含 header)
  - test_export_csv_empty_holdings         空 → only header row
```

## 风险 / 兼容性

| 风险 | 缓解 |
|---|---|
| yfinance sector 对 ADR/ETF/加密币缺失 | fallback "未分类"；不影响表格其他列 |
| 30d sparkline 拉数据慢 | Tencent 兜底；可在 5d.1 升级预聚合 |
| AI hx-trigger=load 每次刷页烧 token | 决策点 3 已知；5d.1 升级缓存 |
| 删除 holding 后页面状态 | 现有 `/holdings/{id}` DELETE 行为；需确认在新模板下不破。如有问题，类似 5c 抽 `_build_holdings_ctx` |
| `Holding.sector` migration | NULL-able 字段，零数据风险 |
| 与 `monthly_realized_pl(months=None)` 兼容 | 5c 改 default 为 None；现有 `/holdings` 调 `monthly_realized_pl(db)` 行为不变 (return all months) |
| 老 POST `/holdings/risk-analysis` caller | 搜全代码库；若有，保留 POST 别名指向同一处理函数 |

## Out of Scope (本阶段不做)

- 表格列头排序交互
- 表格筛选抽屉
- 板块手工 override / 编辑
- AI 风险分析持久化缓存 (5d.1)
- Holdings vs S&P/Nasdaq 比较曲线
- 移动端 < 768px 适配
- 多账户切换
- Real-time WebSocket 推送
