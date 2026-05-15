# Phase 5c — `/trades` 页 NineScrolls Variant A 重做

> 设计来源：`docs/design/mockups/page-trades.jsx`
> 上一阶段：Phase 5b-2（`/stock/{ticker}` Variant A）已落地
> 目标分支：`feat/phase-5c-trades-page`

## Goal

把 `/trades` 页从 Tailwind utility-class 老布局重做成 NineScrolls Variant A 设计语言，并补齐设计稿规定的 KPI strip / 月度柱图 / 按代码 P&L 排行 / 服务端分页 / 日期区间筛选 / CSV 导出 6 项数据能力。

## Non-Goals

- 移动端 (<768px) 优化 — 设计稿 desktop-first
- `/holdings`、`/recap` 重做 (Phase 5d / 5e)
- `/trades/import` wizard 页本身的重做 (本阶段仅在右栏放一个 dropzone 入口跳过去)
- "上次导入" 元信息显示 (需要新表，Phase 5c.1 单独做)
- AI insights on trades ("你胜率比上月高 X%")
- 计算结果与 `Trade.realized_pl` 列的一致性核验 (matcher 只读、不写回)

## Architecture

四层叠加，自下而上：

```
┌─────────────────────────────────────────────────────────────────┐
│ 模板层 (Jinja2 + HTMX)                                          │
│   trades.html (hero + KPI + filter card + main grid)            │
│   partials/trades_table.html (10 列 mp-table + 分页 footer)      │
│   partials/trades_kpi_strip.html                                │
│   partials/trades_filter_card.html                              │
│   partials/trades_monthly_pl_card.html                          │
│   partials/trades_dropzone_card.html                            │
│   partials/trades_by_ticker_card.html                           │
├─────────────────────────────────────────────────────────────────┤
│ 路由层 (FastAPI)                                                │
│   GET  /trades              扩展 ?page/limit/from/to/q          │
│   GET  /trades/export.csv   新增                                │
│   (POST/PUT/DELETE /trades 不动，partial 输出风格更新)             │
├─────────────────────────────────────────────────────────────────┤
│ 聚合层 (marketpulse/holdings/service.py + 扩展)                  │
│   total_realized_pl(...,from_date,to_date)                      │
│   trading_stats(...,from_date,to_date)                          │
│   monthly_realized_pl(*, months=15)                             │
│   trade_count_this_month(session) [新]                          │
│   realized_pl_by_ticker(session) [新]                           │
│   avg_hold_days(session,*,from_date,to_date) [新,内部用 fifo]    │
├─────────────────────────────────────────────────────────────────┤
│ FIFO 模块 (marketpulse/holdings/fifo.py) [新]                    │
│   @dataclass LotMatch                                           │
│   match_lots_fifo(session) → list[LotMatch]                     │
└─────────────────────────────────────────────────────────────────┘
```

数据库层不动 (沿用现有 `Trade` / `StockSplit` / `Dividend` 表)。

## Tech Stack

- 后端：FastAPI + SQLAlchemy 2.x (existing)
- 模板：Jinja2 (existing)
- 前端交互：HTMX (existing) + 极少量 vanilla JS (现有表单 type-aware 逻辑保留)
- 样式：vanilla CSS via `marketpulse/web/static/css/app.css` (existing) + 新增 `mp-trades-*`、`mp-kpi`、`mp-hero`、`mp-dropzone`、`mp-monthly-bars`、`mp-ticker-row`
- 图标：Material Symbols Outlined (existing)
- 字体：Space Grotesk / Inter / Roboto Mono (existing tokens)
- CSV：标准库 `csv` + FastAPI `StreamingResponse`

## 决策记录

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| 1 | `/trades/import` 处置 | 保留独立 wizard 页，右栏卡片只是入口 | 保留预览/重复检测安全性 |
| 2 | 分页 | 服务端 `?page=N&limit=50` | 与设计稿一致；性能可控 |
| 3 | 平均持仓天数算法 | 完整 FIFO lot matcher | 长期 holdings 也要用，写一次复用 |
| 4 | 日期筛选范围 | 流水 + KPI 联动；右栏(月度柱图/排行)永远 all-time | 局部表现 vs 宏观参考分离 |
| 5 | CSV 导出范围 | 当前 view (跟随过滤)；Robinhood-format 兼容 | "所见即所得"；导出可回 import 做迁移 |

## 路由 / 端点

### `GET /trades`

已存在。扩展 query 参数：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `page` | int | 1 | 1-based |
| `limit` | int | 50 | clamp 到 [10, 200] |
| `from` | YYYY-MM-DD | None | 包含。匹配 `Trade.executed_at`、`StockSplit.ex_date`、`Dividend.ex_date` |
| `to` | YYYY-MM-DD | None | 包含。同上 |
| `q` | str | None | 代码搜索，case-insensitive；保留 `ticker` 作为别名 (旧链接兼容) |
| `event_type` | trade\|split\|dividend\|None | None | 现有 |

参数校验：
- 非法日期格式 → 422
- `from > to` → 422
- `page <= 0` 或 `limit <= 0` → 422
- `page` 超出 max_page → clamp 到 max_page 并返回该页 (不报错)

返回 `templates/trades.html` 渲染，context dict 完整结构见 §"路由 context"。

### `GET /trades/export.csv` [新]

继承 `/trades` 的所有过滤参数 (但 ignore `page` / `limit`，导全部匹配)。

Headers:
```
Content-Type: text/csv; charset=utf-8
Content-Disposition: attachment; filename="trades-YYYY-MM-DD.csv"
```

输出 Robinhood-format 列 (兼容现有 `/trades/import` 解析器)：

```
Activity Date,Process Date,Settle Date,Instrument,Description,Trans Code,Quantity,Price,Amount
```

`Trans Code` 映射：
- `Trade.action == "buy"` → `Buy`
- `Trade.action == "sell"` → `Sell`
- `Dividend` → `CDIV`
- `StockSplit` → 跳过 (Robinhood CSV 没有拆股；自动从 yfinance 同步)

使用 `StreamingResponse` + generator，避免大量交易时一次性 buffer。

### `POST /trades` / `PUT /trades/{id}` / `DELETE /trades/{id}`

现有，签名不动。partial 输出 `partials/trades_table.html` 必须用新风格。

### `GET /trades/import` / `POST /trades/import` / `POST /trades/import/confirm`

现有 wizard 完全不动。本阶段不重做这个页面。

## 路由 context

```python
{
  "events": [...],                # 当前页 events (一页 50)，按时间倒序
  "page": int,
  "total_pages": int,
  "total_count": int,
  "filters": {
    "from": "YYYY-MM-DD" | None,
    "to":   "YYYY-MM-DD" | None,
    "q":    str | None,
    "event_type": "trade" | "split" | "dividend" | None,
  },
  "kpi": {
    "total_trades": int,           # 当前过滤范围内
    "ytd_realized": float,         # 当前过滤范围内的 realized_pl
    "ytd_label": str,              # "YTD" or "2026-03-01 → 2026-05-15"
    "win_rate_pct": float,         # 当前过滤范围
    "wins": int,                   # 同上
    "losses": int,                 # 同上
    "avg_hold_days": float | None, # 同上；无数据时 None
    "this_month": {                # 永远当前自然月，不受筛选
      "total": int,
      "buys": int,
      "sells": int,
      "dividends": int,
    },
  },
  "right_rail": {
    "monthly_pl": [{"month": "YYYY-MM", "pl": float, "trade_count": int}, ...],  # 15 月
    "by_ticker": [{"ticker": str, "realized_pl": float, "pct": float}, ...],     # top 8
  },
}
```

## 聚合层

### `marketpulse/holdings/fifo.py` [新]

```python
from dataclasses import dataclass
from datetime import datetime
from sqlalchemy.orm import Session
from marketpulse.holdings.models import Trade

@dataclass(frozen=True)
class LotMatch:
    ticker: str
    buy_executed_at: datetime
    sell_executed_at: datetime
    quantity: float           # 这一段配对的数量
    hold_days: int            # int(sell - buy 的天数)，向下取整
    realized_pl: float        # (sell_price - buy_price) * quantity

def match_lots_fifo(session: Session) -> list[LotMatch]:
    """对每个 ticker 按 executed_at 升序遍历 trades；
    buy → push open-lot (qty, price, executed_at)；
    sell → 按 FIFO 从 open-lots 头部消费 qty，每消一段产生 LotMatch。

    Sell 超过 open quantity 时丢弃溢出部分 (与现有 trades_service 行为一致；
    不报错，因为现有数据可能有手动调整)。
    Cross-ticker 互不影响。
    Splits / Dividends 不参与 lot matching。
    """
    ...
```

### `marketpulse/holdings/service.py` 扩展

```python
# 现有签名扩展
def total_realized_pl(
    session: Session,
    *,
    ticker: str | None = None,
    from_date: date | None = None,   # 包含
    to_date:   date | None = None,   # 包含
) -> float: ...

def trading_stats(
    session: Session,
    *,
    ticker: str | None = None,
    from_date: date | None = None,
    to_date:   date | None = None,
) -> dict[str, Any]: ...

def monthly_realized_pl(
    session: Session,
    *,
    months: int = 15,                # 现有签名加默认参数
) -> list[dict[str, Any]]:
    """末尾 months 个月，缺月补 {pl: 0, trade_count: 0}。"""
    ...

# 新增
def trade_count_this_month(session: Session) -> dict[str, int]:
    """{"total": N, "buys": N, "sells": N, "dividends": N}
    当前自然月 (UTC)，跟筛选无关。"""
    ...

def realized_pl_by_ticker(
    session: Session,
    *,
    top_n: int = 8,
) -> list[dict[str, Any]]:
    """[{ticker, realized_pl, pct}, ...] 按 abs(realized_pl) 降序 top_n。
    pct = realized_pl / cost_basis_of_sold_lots * 100。
    cost_basis_of_sold_lots 用 LotMatch 累加 (qty * buy_price)。"""
    ...

def avg_hold_days(
    session: Session,
    *,
    from_date: date | None = None,
    to_date:   date | None = None,
) -> float | None:
    """对 sell_executed_at 落在窗口内的 LotMatch 算 hold_days 平均。
    无数据返回 None。"""
    ...
```

## 模板架构

### 文件清单

```
marketpulse/web/templates/
├── trades.html                          重写 (整页布局 + script 引入)
└── partials/
    ├── trades_table.html                重写 (10 列 mp-table + 分页 footer)
    ├── trades_kpi_strip.html            新
    ├── trades_filter_card.html          新
    ├── trades_monthly_pl_card.html      新
    ├── trades_dropzone_card.html        新
    ├── trades_by_ticker_card.html       新
    └── trades_form_script.html          新 (现有 type-aware 表单 JS 抽出来)
```

### `trades.html` 整体结构

```html
{% extends "base.html" %}
{% block content %}

<section class="mp-hero">
  <div>
    <span class="mp-eyebrow mp-eyebrow--primary">交易记录</span>
    <h1 class="grotesk mp-hero__title">Trade Ledger</h1>
    <span class="mp-rule"></span>
    <p class="mp-hero__desc">买卖、拆股、分红的完整流水。所有持仓与已实现盈亏均由此推算。</p>
  </div>
  <div class="mp-hero__actions">
    <a href="/trades/import" class="mp-btn mp-btn--ghost mp-btn--lg">
      <span class="material-symbols-outlined">upload_file</span>导入 Robinhood CSV
    </a>
    <a href="/trades/export.csv?{{ filters_qs }}" class="mp-btn mp-btn--ghost mp-btn--lg">
      <span class="material-symbols-outlined">download</span>导出 CSV
    </a>
  </div>
</section>

<section class="mp-trades-kpi">
  {% include "partials/trades_kpi_strip.html" %}
</section>

<section class="mp-trades-filter">
  {% include "partials/trades_filter_card.html" %}
</section>

<section class="mp-trades-main">
  <div id="trades-container">
    {% include "partials/trades_table.html" %}
  </div>
  <aside class="mp-trades-rail">
    {% include "partials/trades_monthly_pl_card.html" %}
    {% include "partials/trades_dropzone_card.html" %}
    {% include "partials/trades_by_ticker_card.html" %}
  </aside>
</section>

{% include "partials/trades_form_script.html" %}
{% endblock %}
```

`filters_qs` 由 Jinja 过滤器 `urlencode` 生成，跳过 `None` 值。

### `partials/trades_kpi_strip.html`

5 张 `mp-card mp-kpi`，每张：

```html
<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">{{ label }}</span>
    <span class="material-symbols-outlined mp-kpi__icon">{{ icon }}</span>
  </div>
  <div class="mp-kpi__value grotesk tnum"
       {% if value_color %}style="color: {{ value_color }};"{% endif %}>{{ value }}</div>
  <div class="mp-kpi__hint">{{ hint }}</div>
</div>
```

5 张内容：

| label | value | hint | icon | value_color |
|---|---|---|---|---|
| `总笔数` | `kpi.total_trades` | `kpi.ytd_label`(显示日期范围标签) | `receipt_long` | navy |
| `已实现盈亏 · {{ kpi.ytd_label }}` | `"%+,.2f"\|format(kpi.ytd_realized)` | `"%d 胜 / %d 负"\|format(kpi.wins, kpi.losses)` | `payments` | up/down |
| `胜率` | `"%.1f%%"\|format(kpi.win_rate_pct)` | `"%d 胜 / %d 负"\|format(kpi.wins, kpi.losses)` | `trending_up` | navy |
| `平均持仓天数` | `kpi.avg_hold_days(int) + "d"` or `—` | `"基于 FIFO 配对"` | `schedule` | navy |
| `本月新笔数` | `kpi.this_month.total` | `"%d 买 · %d 卖 · %d 分红"\|format(...)` | `event_available` | navy |

### `partials/trades_filter_card.html`

```html
<div class="mp-card" style="padding: 18px;">
  <!-- 第一行: filter + 搜索 + 区间 -->
  <form method="get" action="/trades" class="mp-trades-filter__row">
    <span class="mp-eyebrow mp-eyebrow--primary">筛选</span>

    <!-- event_type chips (传 form submit) -->
    <div class="mp-filter-chips">
      <button name="event_type" value=""        class="mp-chip {% if not filters.event_type %}mp-chip--active{% endif %}">全部 <span class="mp-chip__count">{{ counts.all }}</span></button>
      <button name="event_type" value="trade"   class="mp-chip {% if filters.event_type=='trade' %}mp-chip--active{% endif %}">买卖 <span class="mp-chip__count">{{ counts.trade }}</span></button>
      <button name="event_type" value="split"   class="mp-chip {% if filters.event_type=='split' %}mp-chip--active{% endif %}">拆股 <span class="mp-chip__count">{{ counts.split }}</span></button>
      <button name="event_type" value="dividend" class="mp-chip {% if filters.event_type=='dividend' %}mp-chip--active{% endif %}">分红 <span class="mp-chip__count">{{ counts.dividend }}</span></button>
    </div>

    <span class="mp-divider-v"></span>

    <label class="mp-eyebrow">代码</label>
    <input name="q" value="{{ filters.q or '' }}" placeholder="AAPL · NVDA …" class="mp-input mp-input--mono" style="width:180px;" />

    <label class="mp-eyebrow">区间</label>
    <input name="from" type="date" value="{{ filters['from'] or '' }}" class="mp-input mp-input--mono" />
    <span class="mp-divider-arrow">→</span>
    <input name="to"   type="date" value="{{ filters.to or '' }}" class="mp-input mp-input--mono" />

    <button type="submit" class="mp-btn mp-btn--ghost mp-btn--sm">应用</button>
  </form>

  <hr class="mp-hr" style="margin: 16px -18px;" />

  <!-- 第二行: 添加表单 (现有 logic 保留) -->
  <form id="event-form" hx-post="/trades" hx-target="#trades-container" hx-swap="innerHTML"
        hx-on::after-request="if(event.detail.successful) exitEditMode();"
        class="mp-trades-add">
    <div class="mp-trades-add__kind">
      <span class="mp-eyebrow mp-eyebrow--primary">添加记录</span>
      <div class="mp-seg">
        <button type="button" data-kind="buy"      onclick="onEventKindChange('buy')"      class="is-active">买入</button>
        <button type="button" data-kind="sell"     onclick="onEventKindChange('sell')">卖出</button>
        <button type="button" data-kind="split"    onclick="onEventKindChange('split')">拆股</button>
        <button type="button" data-kind="dividend" onclick="onEventKindChange('dividend')">分红</button>
      </div>
      <input type="hidden" name="event_kind" id="event-kind" value="buy" />
    </div>

    <!-- type-aware fields 同现有逻辑,样式改 mp-input -->
    ...
    <button id="submit-btn" type="submit" class="mp-btn mp-btn--primary">
      <span class="material-symbols-outlined">add</span>记录
    </button>
  </form>
</div>
```

`counts` 字典在 route 里算：4 个 count 各跑一个 SELECT COUNT，区间过滤生效。

### `partials/trades_table.html`

```html
<table class="mp-table mp-table--trades">
  <thead>
    <tr>
      <th class="col-time">时间</th>
      <th class="col-ticker">代码</th>
      <th class="col-type">类型</th>
      <th class="col-qty">数量</th>
      <th class="col-price">价格</th>
      <th class="col-total">总额</th>
      <th class="col-fees">手续费</th>
      <th class="col-pl">已实现盈亏</th>
      <th class="col-plpct">盈亏 %</th>
      <th class="col-notes">备注</th>
      <th class="col-actions"></th>
    </tr>
  </thead>
  <tbody>
    {% for e in events %}
      {% if e.kind == "trade" %}
        <tr id="trade-row-{{ e.obj.id }}">
          <td class="col-time"><time data-utc="{{ ... }}">{{ ... }}</time></td>
          <td class="col-ticker"><a href="/stock/{{ e.obj.ticker }}" class="mp-ticker-link">{{ e.obj.ticker }}</a></td>
          <td class="col-type">
            {% if e.obj.action == "buy" %}<span class="mp-chip mp-chip--periwinkle">买入</span>
            {% else %}<span class="mp-chip mp-chip--down">卖出</span>{% endif %}
          </td>
          <td class="col-qty mono tnum">{{ "%g"|format(e.obj.quantity) }}</td>
          <td class="col-price mono tnum">${{ "%.2f"|format(e.obj.price) }}</td>
          <td class="col-total mono tnum">${{ "%.2f"|format(e.obj.quantity * e.obj.price) }}</td>
          <td class="col-fees mono tnum">{{ "$%.2f"|format(e.obj.fees) if e.obj.fees else "—" }}</td>
          <td class="col-pl mono tnum {% if e.obj.realized_pl is not none and e.obj.realized_pl >= 0 %}up{% elif e.obj.realized_pl is not none %}down{% endif %}">
            {% if e.obj.realized_pl is not none %}{{ "%+.2f"|format(e.obj.realized_pl) }}{% else %}—{% endif %}
          </td>
          <td class="col-plpct mono tnum">
            {% if e.obj.realized_pl is not none %}
              {{ "%+.2f%%"|format(e.obj.realized_pl / (e.obj.quantity * e.obj.price) * 100) }}
            {% else %}—{% endif %}
          </td>
          <td class="col-notes">{{ e.obj.notes or "" }}</td>
          <td class="col-actions">
            <button class="mp-icon-btn" onclick='loadTradeIntoForm({{ ... }})'>
              <span class="material-symbols-outlined">edit</span>
            </button>
            <button class="mp-icon-btn" hx-delete="/trades/{{ e.obj.id }}" hx-target="#trade-row-{{ e.obj.id }}" hx-swap="outerHTML" hx-confirm="...">
              <span class="material-symbols-outlined">delete_outline</span>
            </button>
          </td>
        </tr>
      {% elif e.kind == "split" %}
        <!-- split: bg 加 rgba(141,82,231,0.04)，紫色 chip "拆股"，合并 6 列 colspan 显示 "1 → ratio 拆股" -->
      {% elif e.kind == "dividend" %}
        <!-- dividend: bg 加 rgba(14,138,95,0.04)，绿色 chip "分红" (mp-chip--up)，总额上色 -->
      {% endif %}
    {% endfor %}
    {% if not events %}
      <tr><td colspan="11" class="mp-empty-row">暂无记录。在上方表单中添加第一条。</td></tr>
    {% endif %}
  </tbody>
</table>

<!-- 分页 footer -->
<div class="mp-table-footer">
  <span class="mp-table-footer__count">显示 {{ (page-1)*limit + 1 }} – {{ ((page-1)*limit + events|length) }} · 总 {{ total_count }} 条</span>
  <div class="mp-table-footer__pager">
    {% set base_qs = filters_qs_no_page %}
    {% if page > 1 %}
      <a class="mp-btn mp-btn--ghost mp-btn--sm" hx-get="/trades?{{ base_qs }}&page={{ page-1 }}" hx-target="#trades-container" hx-push-url="true">‹ 上一页</a>
    {% endif %}
    {# 显示 page-2..page+2,边界 clamp #}
    {% for p in pager_window %}
      <a class="mp-btn mp-btn--{% if p == page %}navy{% else %}ghost{% endif %} mp-btn--sm"
         hx-get="/trades?{{ base_qs }}&page={{ p }}" hx-target="#trades-container" hx-push-url="true">{{ p }}</a>
    {% endfor %}
    {% if page < total_pages %}
      <a class="mp-btn mp-btn--ghost mp-btn--sm" hx-get="/trades?{{ base_qs }}&page={{ page+1 }}" hx-target="#trades-container" hx-push-url="true">下一页 ›</a>
    {% endif %}
  </div>
</div>
```

`pager_window` 由 route 计算：`max(1, page-2)..min(total_pages, page+2)`。

### `partials/trades_monthly_pl_card.html`

15 个月柱图，每柱百分比高度 `abs(pl) / max_abs * 100`，正绿负红。

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">insights</span>月度已实现盈亏
    </span>
    <span class="mp-card__sub">15 个月 · 累计 <span class="up mono">{{ "%+,.0f"|format(monthly_total) }}</span></span>
  </div>
  <div class="mp-card__body">
    <div class="mp-monthly-bars">
      {% set max_abs = monthly_pl | map(attribute='pl') | map('abs') | max %}
      {% for m in monthly_pl %}
        {% set h = (m.pl|abs / max_abs * 100) if max_abs else 0 %}
        <div class="mp-monthly-bar" title="{{ m.month }}: {{ '%+,.0f'|format(m.pl) }}">
          <div class="mp-monthly-bar__bar" style="height: {{ h }}%; background: {% if m.pl >= 0 %}var(--mp-up){% else %}var(--mp-down){% endif %};"></div>
          <div class="mp-monthly-bar__label">{{ m.month[5:] }}</div>
        </div>
      {% endfor %}
    </div>
    <hr class="mp-hr" />
    <div class="mp-monthly-footer">
      <span>最佳月 · <span class="up mono">{{ "%+,.0f"|format(best_pl) }}</span> ({{ best_month }})</span>
      <span>最差月 · <span class="down mono">{{ "%+,.0f"|format(worst_pl) }}</span> ({{ worst_month }})</span>
    </div>
  </div>
</section>
```

### `partials/trades_dropzone_card.html`

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title"><span class="material-symbols-outlined">upload_file</span>从 Robinhood 导入</span>
  </div>
  <div class="mp-card__body">
    <form action="/trades/import" method="post" enctype="multipart/form-data" id="dropzone-form">
      <label class="mp-dropzone" id="dropzone">
        <input type="file" name="file" accept=".csv" class="mp-dropzone__file" />
        <span class="material-symbols-outlined mp-dropzone__icon">cloud_upload</span>
        <div class="mp-dropzone__title">拖入 Robinhood CSV</div>
        <div class="mp-dropzone__sub">或 <span class="mp-link">点击选择文件</span></div>
      </label>
    </form>
  </div>
</section>
```

JS：drag-over 加 `.is-dragover`；drop 时把 file 塞进 hidden input，自动 submit。**降级**：`<input type="file">` 本身可以点击触发，不依赖 JS。

### `partials/trades_by_ticker_card.html`

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title"><span class="material-symbols-outlined">leaderboard</span>按代码 · 已实现盈亏</span>
    <span class="mp-card__sub">all-time 累计</span>
  </div>
  <ul class="mp-ticker-list">
    {% set max_abs = by_ticker | map(attribute='realized_pl') | map('abs') | max %}
    {% for r in by_ticker %}
      <li class="mp-ticker-row">
        <span class="grotesk mp-ticker-row__symbol">{{ r.ticker }}</span>
        <div class="mp-ticker-row__bar">
          <div style="width: {{ (r.realized_pl|abs / max_abs * 100) if max_abs else 0 }}%; background: {% if r.realized_pl >= 0 %}var(--mp-up){% else %}var(--mp-down){% endif %};"></div>
        </div>
        <span class="mono tnum {% if r.realized_pl >= 0 %}up{% else %}down{% endif %}">{{ "%+,.0f"|format(r.realized_pl) }}</span>
        <span class="mono tnum {% if r.realized_pl >= 0 %}up{% else %}down{% endif %}">{{ "%+.1f%%"|format(r.pct) }}</span>
      </li>
    {% endfor %}
  </ul>
</section>
```

## CSS 新增

(全部追加到 `marketpulse/web/static/css/app.css`)

```css
/* ════════ Hero (用于 trades / holdings / recap) ════════ */
.mp-hero            { display:flex; align-items:flex-end; justify-content:space-between;
                      padding:32px 48px 24px; }
.mp-hero__title     { font:700 48px/1 var(--ns-font-headline); letter-spacing:-0.04em;
                      color:var(--ns-navy); margin:6px 0 0; }
.mp-hero__desc      { font-size:14px; color:var(--ns-on-surface-variant);
                      margin:12px 0 0; max-width:640px; }
.mp-hero__actions   { display:flex; gap:8px; }

/* ════════ /trades layout ════════ */
.mp-trades-kpi      { padding:0 48px 16px; display:grid;
                      grid-template-columns:repeat(5,1fr); gap:16px; }
.mp-trades-filter   { padding:8px 48px 16px; }
.mp-trades-main     { padding:0 48px 32px; display:grid;
                      grid-template-columns: minmax(0,1fr) 440px; gap:16px; }
.mp-trades-rail     { display:flex; flex-direction:column; gap:16px; }

@media (max-width: 1440px) {
  .mp-trades-main   { grid-template-columns: 1fr; }
}
@media (max-width: 900px) {
  .mp-trades-kpi    { grid-template-columns: repeat(2, 1fr); }
  .mp-hero          { flex-direction:column; align-items:flex-start; gap:16px; }
}

/* ════════ KPI card ════════ */
.mp-kpi             { padding:18px 20px; }
.mp-kpi__head       { display:flex; justify-content:space-between; align-items:flex-start; }
.mp-kpi__icon       { font-size:18px; color:var(--ns-outline-variant); }
.mp-kpi__value      { font:700 30px/1.1 var(--ns-font-headline); letter-spacing:-0.02em;
                      color:var(--ns-navy); margin-top:6px; }
.mp-kpi__hint       { font-size:11.5px; color:var(--ns-on-surface-variant); margin-top:4px; }

/* ════════ Filter card 内部 ════════ */
.mp-trades-filter__row { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.mp-filter-chips       { display:flex; gap:4px; }
.mp-chip__count        { margin-left:4px; opacity:0.7; }
.mp-divider-v          { width:1px; height:24px; background:var(--ns-outline-variant); }
.mp-divider-arrow      { color:var(--ns-slate-400); }
.mp-input              { height:30px; padding:0 12px; border:1px solid var(--ns-outline-variant);
                         border-radius:2px; font-size:12px; font-family:var(--ns-font-body); }
.mp-input--mono        { font-family:var(--ns-font-mono); }
.mp-trades-add         { display:flex; align-items:flex-end; gap:12px; flex-wrap:wrap; }
.mp-trades-add__kind   { display:flex; flex-direction:column; gap:4px; }

/* ════════ Trades table ════════ */
.mp-table--trades th        { font:600 10px/1 var(--ns-font-headline);
                              letter-spacing:0.08em; text-transform:uppercase;
                              color:var(--ns-on-surface-variant); padding:10px 12px;
                              border-bottom:1px solid var(--ns-outline-variant);
                              white-space:nowrap; }
.mp-table--trades td        { padding:12px; font-size:13px;
                              border-bottom:1px solid var(--ns-outline-variant); }
.mp-table--trades tbody tr:hover { background: var(--ns-surface-container-low); }
.mp-table--trades .col-time  { width:140px; color:var(--ns-on-surface-variant); }
.mp-table--trades .col-ticker{ width:90px; }
.mp-table--trades .col-type  { width:90px; }
.mp-table--trades .col-qty,
.mp-table--trades .col-price,
.mp-table--trades .col-total,
.mp-table--trades .col-fees,
.mp-table--trades .col-pl,
.mp-table--trades .col-plpct { text-align:right; }
.mp-table--trades .col-actions { width:80px; text-align:right; }
.mp-table--trades .up        { color:var(--mp-up); font-weight:600; }
.mp-table--trades .down      { color:var(--mp-down); font-weight:600; }
.mp-table--trades .muted     { color:var(--ns-on-surface-variant); }
.mp-table--trades tr.split   { background:rgba(141,82,231,0.04); }
.mp-table--trades tr.dividend{ background:rgba(14,138,95,0.04); }

.mp-empty-row               { text-align:center; padding:32px; color:var(--ns-on-surface-variant); }

.mp-ticker-link             { color:var(--ns-navy); font-weight:700;
                              text-decoration:none; font-family:var(--ns-font-headline); }
.mp-icon-btn                { background:transparent; border:0; cursor:pointer;
                              color:var(--ns-slate-400); padding:4px; }
.mp-icon-btn:hover          { color:var(--ns-navy); }

.mp-table-footer            { display:flex; justify-content:space-between; align-items:center;
                              padding:12px 18px;
                              background:var(--ns-surface-container-low);
                              border-top:1px solid var(--ns-outline-variant); }
.mp-table-footer__count     { font-size:12px; color:var(--ns-on-surface-variant); }
.mp-table-footer__pager     { display:flex; gap:4px; }

/* ════════ Drop zone ════════ */
.mp-dropzone                { display:block; border:1px dashed var(--ns-outline-variant);
                              border-radius:2px; padding:24px 16px; text-align:center;
                              background:var(--ns-surface-container-low); cursor:pointer;
                              transition: background 200ms, border-color 200ms; }
.mp-dropzone.is-dragover    { background:var(--ns-primary-container);
                              border-color:var(--ns-primary); }
.mp-dropzone__file          { position:absolute; opacity:0; pointer-events:none;
                              width:1px; height:1px; }
.mp-dropzone__icon          { font-size:36px; color:var(--ns-primary); }
.mp-dropzone__title         { font-size:14px; font-weight:600; color:var(--ns-navy); margin-top:6px; }
.mp-dropzone__sub           { font-size:11.5px; color:var(--ns-on-surface-variant); margin-top:4px; }
.mp-link                    { color:var(--ns-primary); text-decoration:underline; cursor:pointer; }

/* ════════ Monthly bar chart ════════ */
.mp-monthly-bars            { display:flex; gap:4px; align-items:flex-end; height:140px; }
.mp-monthly-bar             { flex:1; display:flex; flex-direction:column;
                              justify-content:flex-end; height:100%; }
.mp-monthly-bar__bar        { border-radius:2px 2px 0 0; min-height:2px; margin-bottom:4px; }
.mp-monthly-bar__label      { font:9px/1 var(--ns-font-mono); color:var(--ns-slate-400);
                              text-align:center; letter-spacing:0.02em; }
.mp-monthly-footer          { display:flex; justify-content:space-between;
                              font-size:11px; color:var(--ns-on-surface-variant); }

/* ════════ By-ticker leaderboard ════════ */
.mp-ticker-list             { list-style:none; margin:0; padding:10px 16px 18px; }
.mp-ticker-row              { display:grid;
                              grid-template-columns: 50px 1fr 90px 64px;
                              gap:10px; align-items:center; padding:7px 0; }
.mp-ticker-row__symbol      { font-weight:700; font-size:13px; color:var(--ns-navy); }
.mp-ticker-row__bar         { height:8px; background:var(--ns-surface-container);
                              border-radius:2px; position:relative; overflow:hidden; }
.mp-ticker-row__bar > div   { position:absolute; left:0; top:0; bottom:0; }
```

## HTMX 交互

| 触发 | hx-* | 目标 |
|------|------|------|
| 翻页 | `hx-get="/trades?page=N&..."` `hx-push-url="true"` | `#trades-container` |
| 类型 chip 切换 | filter form submit (普通 GET，**非** HTMX) | 整页刷新 |
| 区间日期切换 | filter form submit | 整页刷新 |
| 代码搜索 | filter form submit | 整页刷新 |
| 添加交易 | 现有 `hx-post="/trades"` | `#trades-container` |
| 编辑/删除 | 现有 | 表格行 |
| Dropzone 上传 | 普通 form POST | 跳转到 `/trades/import?preview=...` |

**HTMX 只管表格内的"快"操作**；任何改变 KPI / 右栏的过滤，整页刷新。简化心智模型。

## 错误处理

| 场景 | 处理 |
|------|------|
| `?page=999` 超界 | clamp 到 max_page，返回该页（可能为空） |
| `?from=invalid` | 422 |
| `?from > to` | 422 |
| `?limit=0` 或 `?limit=10000` | clamp 到 [10, 200] |
| dropzone 上传非 CSV | 现有 wizard 已处理，本阶段不重做 |
| FIFO matcher 数据不一致 (sell 超过 open qty) | 丢弃溢出部分，不报错 (与 trades_service 现有行为一致) |
| 无交易 | KPI 全部 0、avg_hold_days = "—"、右栏 monthly_pl 全 0、by_ticker 空数组 → 显示空 state |

## 测试

```
tests/holdings/test_fifo.py                     新
  - test_simple_buy_sell_full_close             一买一全卖
  - test_partial_sell_keeps_open_lot            买 10 卖 4，剩 6
  - test_multi_buys_one_sell_fifo_order         买 10 买 20 一次卖 15 → 第一段 10 配第一买，第二段 5 配第二买
  - test_cross_ticker_isolated                  AAPL buy / NVDA sell 不污染对方
  - test_sell_exceeds_open_quantity_drops       sell 50 但 open 30 → 只配 30，溢出丢弃，不报错
  - test_hold_days_calculation                  买 1/1 卖 6/30 → 180 days
  - test_excludes_splits_and_dividends          只匹配 Trade，splits/dividends 不参与

tests/holdings/test_aggregations.py             新
  - test_total_realized_pl_with_date_window     from/to 边界 inclusive
  - test_trading_stats_with_date_window         win_rate 不计 None
  - test_monthly_realized_pl_15_months          缺月补 0
  - test_monthly_realized_pl_default_months     默认 15
  - test_realized_pl_by_ticker_orders_by_abs    +1000 在 -2000 之后
  - test_realized_pl_by_ticker_top_n            top 8 截断
  - test_realized_pl_by_ticker_pct_calc         pct = pl / cost_basis * 100
  - test_avg_hold_days_basic                    多 LotMatch 算平均
  - test_avg_hold_days_no_data_returns_none     空 → None
  - test_avg_hold_days_window                   只统计 sell 日落在窗口内的
  - test_trade_count_this_month_classifies      买/卖/分红正确分类

tests/web/test_trades_page.py                   扩展
  # 现有 (保留)
  - 表单提交 / 编辑 / 删除 / event_type 过滤 / hash check / tz_offset
  # 新增
  - test_page_2_pagination                      ?page=2 偏移正确
  - test_pagination_clamps_to_max               ?page=999 不报错
  - test_filter_date_range                      from/to 生效
  - test_filter_q_case_insensitive              ?q=aapl 命中 AAPL
  - test_legacy_ticker_query_alias              ?ticker=AAPL 等价 ?q=AAPL
  - test_invalid_date_returns_422               ?from=garbage
  - test_from_greater_than_to_returns_422
  - test_kpi_strip_renders_5_cards              .mp-kpi 计数 == 5
  - test_kpi_ytd_value_present                  YTD realized 出现在 HTML
  - test_kpi_avg_hold_days_dash_when_empty
  - test_monthly_chart_has_15_bars              .mp-monthly-bar 计数 == 15
  - test_by_ticker_renders_top_8
  - test_dropzone_form_action_import            <form action="/trades/import">
  - test_hero_export_link_carries_filters       /trades/export.csv?event_type=trade&...
  - test_visual_anchors                         .mp-hero / .mp-trades-kpi / .mp-trades-main

tests/web/test_trades_export.py                 新
  - test_export_csv_content_type
  - test_export_csv_attachment_filename         trades-YYYY-MM-DD.csv
  - test_export_csv_robinhood_header_row        包含 "Activity Date" 等列
  - test_export_csv_filters_apply               ?event_type=trade 只导买卖
  - test_export_csv_skips_splits                splits 不在输出里
  - test_export_csv_round_trip                  导出 → 重 import → 行数一致

tests/holdings/test_export_csv.py               新
  - test_export_format_matches_import_parser    输出能被 marketpulse.holdings.import_robinhood 解析
```

## 风险 / 兼容性

| 风险 | 缓解 |
|---|---|
| FIFO matcher 与 `Trade.realized_pl` 列不一致 | matcher 只读、不写回；avg_hold_days 用 matcher，total_realized_pl 用现有列；两套独立 |
| YTD 标签在用户选其它区间时误导 | route 算 `ytd_label`：默认 "YTD"，有 from/to 时 `"{from} → {to}"` |
| 旧链接 `?ticker=AAPL` | 路由保留 `ticker` 作为 `q` 别名 |
| 大量 trades 导致 export CSV 内存爆 | `StreamingResponse` + generator |
| dropzone 上传 + HTMX 复杂性 | dropzone 用普通 form POST，不走 HTMX |
| Phase 5b 测试可能依赖 `.text-blue-600` 等旧 class | 重新跑 test_stock + test_holdings + test_recap (Phase 5b 已用 mp-card 锚点，影响小) |

## Out of Scope (本阶段不做)

- `ImportRun` 表 + "上次导入" 信息显示
- 移动端 < 768px 优化
- `/holdings`、`/recap` 重做
- 按月分组的列表视图 (设计稿用柱图)
- AI insights on trades
- 表格列拖动排序 / 自定义列
- Real-time 更新 (websocket / SSE)
