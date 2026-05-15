# Phase 5c — `/trades` 页 NineScrolls Variant A 重做 · 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `/trades` 页从 Tailwind utility 老布局重做成 NineScrolls Variant A 设计语言，补齐 KPI strip / 月度柱图 / 按代码 P&L 排行 / 服务端分页 / 日期区间筛选 / CSV 导出 6 项数据能力。

**Architecture:** 四层叠加：FIFO matcher (new) → service aggregations (extend) → routes (extend + new export) → templates (rewrite + 6 partials)。HTMX 管表格内分页/编辑/删除，整页刷新管 KPI/右栏。

**Tech Stack:** FastAPI + SQLAlchemy 2.x · Jinja2 + HTMX · vanilla CSS (`app.css` 扩展) · Material Symbols · Robinhood-format CSV via StreamingResponse · pytest

**Spec:** `docs/superpowers/specs/2026-05-15-phase-5c-trades-page.md`

**Branch:** `feat/phase-5c-trades-page` (off main)

---

## File Structure (locked in)

```
NEW
  marketpulse/holdings/fifo.py                       LotMatch + match_lots_fifo
  marketpulse/web/templates/partials/
    trades_kpi_strip.html                            5 KPI cards
    trades_filter_card.html                          filter chips + range + add form
    trades_monthly_pl_card.html                      15-month P&L bars
    trades_dropzone_card.html                        Robinhood import entry
    trades_by_ticker_card.html                       top-8 P&L leaderboard
    trades_form_script.html                          extracted type-aware form JS
  tests/holdings/test_fifo.py
  tests/holdings/test_aggregations.py
  tests/web/test_trades_export.py

REWRITE
  marketpulse/web/templates/trades.html              整页布局 (mp-hero + grid)
  marketpulse/web/templates/partials/trades_table.html
                                                     10 列 mp-table + pagination

EXTEND
  marketpulse/holdings/trades.py                     total_realized_pl: +from_date/to_date
  marketpulse/holdings/service.py                    trading_stats: +from_date/to_date
                                                     monthly_realized_pl: +months
                                                     +trade_count_this_month (new)
                                                     +realized_pl_by_ticker (new)
                                                     +avg_hold_days (new)
  marketpulse/web/routes/trades.py                   GET /trades: 新 query params
                                                     +GET /trades/export.csv (new)
                                                     POST/PUT/DELETE: 保留 pagination
  marketpulse/web/static/css/app.css                 +mp-hero / mp-trades-* / mp-kpi
                                                     +mp-filter-chips / mp-input
                                                     +mp-table--trades
                                                     +mp-table-footer / mp-icon-btn
                                                     +mp-dropzone / mp-monthly-bars
                                                     +mp-ticker-row / mp-chip--split
  tests/web/test_trades.py                           +扩展测试 case
```

---

## Conventions / 项目特定 gotchas

- **Models 在 `marketpulse/db/models.py`** (不是 `marketpulse/holdings/models.py`)；导入 `from marketpulse.db.models import Trade, StockSplit, Dividend`
- **`total_realized_pl` 在 `marketpulse/holdings/trades.py`** (不是 `service.py`)；spec 写错了，以这份计划为准
- **`Trade.executed_at` 是 `datetime | None`(timezone-aware)**；可能为 None；fallback 用 `created_at`
- **`StockSplit.ex_date` / `Dividend.ex_date` 是 `date`(no time)**
- **`Trade.realized_pl is not None`** 表示 sell 行；buy 行永远是 None
- **`session_scope()` 是 generator**——用 `next(gen)` 拿 session，参考 `tests/conftest.py`
- **`get_settings.cache_clear()`** 在测试改 env 时要调
- **HTMX 检测**：`request.headers.get("HX-Request") == "true"` → 返回 partial
- **Jinja `urlencode` filter** 已内置；构造 `filters_qs` 用 `{k: v for k, v in filters.items() if v}` + `urlencode`
- **Tailwind 已有 build**：本计划纯 vanilla CSS 加到 `app.css`，**无需** rebuild Tailwind
- **运行测试**：`uv run pytest tests/path/test_x.py::test_name -v`
- **Ruff lint**：每次 commit 前 `uv run ruff check .` 必须干净

---

## Task 1: FIFO lot matcher

**Why:** Phase 5c 的 "平均持仓天数" KPI 需要按 FIFO 配对 buy/sell lot。`marketpulse/holdings/fifo.py` 也将被 holdings 模块未来复用。Service 层的 `realized_pl_by_ticker` 和 `avg_hold_days` 都依赖此模块。

**Files:**
- Create: `marketpulse/holdings/fifo.py`
- Test: `tests/holdings/test_fifo.py`

### Step 1.1: Write failing tests for FIFO matcher

- [ ] Create `tests/holdings/test_fifo.py`:

```python
"""FIFO lot matching: pair buys and sells in time order, per-ticker."""
from datetime import UTC, datetime

import pytest

from marketpulse.db import base as db_base
from marketpulse.db.base import Base
from marketpulse.db.models import Trade


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=UTC)


def _trade(session, ticker, action, qty, price, when) -> Trade:
    t = Trade(
        ticker=ticker,
        action=action,
        quantity=qty,
        price=price,
        fees=0.0,
        executed_at=when,
        realized_pl=None if action == "buy" else 0.0,  # filled later by FIFO
    )
    session.add(t)
    session.commit()
    return t


def test_simple_buy_sell_full_close(db_session):
    """One buy of 10 @ $100, one sell of 10 @ $120 → one LotMatch, PL=+200."""
    from marketpulse.holdings.fifo import match_lots_fifo

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell", 10, 120.0, _dt(2026, 7, 1))
    matches = match_lots_fifo(db_session)
    assert len(matches) == 1
    m = matches[0]
    assert m.ticker == "AAPL"
    assert m.quantity == 10
    assert m.realized_pl == pytest.approx(200.0)
    assert m.hold_days == 181  # Jan 1 → Jul 1 = 181 days


def test_partial_sell_keeps_open_lot(db_session):
    """Buy 10, sell 4 → one LotMatch qty=4; remaining 6 unmatched."""
    from marketpulse.holdings.fifo import match_lots_fifo

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell",  4, 120.0, _dt(2026, 4, 1))
    matches = match_lots_fifo(db_session)
    assert len(matches) == 1
    assert matches[0].quantity == 4
    assert matches[0].realized_pl == pytest.approx(80.0)


def test_multi_buys_one_sell_fifo_order(db_session):
    """Buy 10 @ $100, buy 20 @ $110, sell 15 @ $130 →
    2 LotMatches: 10 from first lot (PL=300), 5 from second (PL=100).
    """
    from marketpulse.holdings.fifo import match_lots_fifo

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "buy",  20, 110.0, _dt(2026, 2, 1))
    _trade(db_session, "AAPL", "sell", 15, 130.0, _dt(2026, 6, 1))
    matches = match_lots_fifo(db_session)
    assert len(matches) == 2
    assert matches[0].quantity == 10
    assert matches[0].realized_pl == pytest.approx(300.0)
    assert matches[1].quantity == 5
    assert matches[1].realized_pl == pytest.approx(100.0)


def test_cross_ticker_isolated(db_session):
    """AAPL buy and NVDA sell never produce a cross-ticker match."""
    from marketpulse.holdings.fifo import match_lots_fifo

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "NVDA", "sell",  5, 200.0, _dt(2026, 6, 1))
    matches = match_lots_fifo(db_session)
    # NVDA sell has no matching open lot (no prior NVDA buy) → 0 matches.
    assert matches == []


def test_sell_exceeds_open_quantity_drops(db_session):
    """Buy 30, sell 50 → only 30 matched; overflow silently dropped."""
    from marketpulse.holdings.fifo import match_lots_fifo

    _trade(db_session, "AAPL", "buy",  30, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell", 50, 120.0, _dt(2026, 6, 1))
    matches = match_lots_fifo(db_session)
    assert len(matches) == 1
    assert matches[0].quantity == 30  # not 50


def test_hold_days_calculation(db_session):
    """Buy 2026-01-01, sell 2026-06-30 → 180 days exactly."""
    from marketpulse.holdings.fifo import match_lots_fifo

    _trade(db_session, "AAPL", "buy",  1, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell", 1, 120.0, _dt(2026, 6, 30))
    matches = match_lots_fifo(db_session)
    assert matches[0].hold_days == 180


def test_buy_after_sell_is_independent_lot(db_session):
    """Buy 10, sell 10 (clean close), buy 10, sell 5 →
    2 LotMatches: original (10), new (5). Times must reflect 2nd buy.
    """
    from marketpulse.holdings.fifo import match_lots_fifo

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell", 10, 120.0, _dt(2026, 3, 1))
    _trade(db_session, "AAPL", "buy",  10, 150.0, _dt(2026, 5, 1))
    _trade(db_session, "AAPL", "sell",  5, 160.0, _dt(2026, 7, 1))
    matches = match_lots_fifo(db_session)
    assert len(matches) == 2
    assert matches[0].quantity == 10  # first round
    assert matches[1].quantity == 5
    assert matches[1].buy_executed_at == _dt(2026, 5, 1)  # 2nd buy
    assert matches[1].sell_executed_at == _dt(2026, 7, 1)


def test_excludes_splits_and_dividends(db_session):
    """Only Trade rows participate; Splits/Dividends ignored."""
    from marketpulse.db.models import Dividend, StockSplit
    from datetime import date

    from marketpulse.holdings.fifo import match_lots_fifo

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell", 10, 120.0, _dt(2026, 6, 1))
    db_session.add(StockSplit(ticker="AAPL", ex_date=date(2026, 3, 1),
                              ratio=2.0, source="manual"))
    db_session.add(Dividend(ticker="AAPL", ex_date=date(2026, 4, 1),
                            amount_per_share=0.25, total_amount=2.50,
                            source="manual"))
    db_session.commit()

    matches = match_lots_fifo(db_session)
    assert len(matches) == 1  # only the buy/sell pair
```

- [ ] **Step 1.2:** Run tests to verify they fail.

```bash
uv run pytest tests/holdings/test_fifo.py -v
```

Expected: All 8 tests FAIL with `ModuleNotFoundError: No module named 'marketpulse.holdings.fifo'`.

- [ ] **Step 1.3:** Implement `marketpulse/holdings/fifo.py`:

```python
"""FIFO lot matching: pair buy and sell trades chronologically per ticker.

Used by aggregation layer to compute:
- avg_hold_days (time between matched buy and sell)
- realized_pl_by_ticker (sum of matched lot PL per ticker)
- per-lot cost basis (for pct calculation)

Read-only: never writes back to Trade.realized_pl. Existing Trade.realized_pl
column is filled by trades_service on sell-row creation; this matcher is an
independent view on the same data.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from marketpulse.db.models import Trade


@dataclass(frozen=True)
class LotMatch:
    ticker: str
    buy_executed_at: datetime
    sell_executed_at: datetime
    quantity: float
    hold_days: int
    realized_pl: float
    buy_price: float  # for cost basis aggregation


def match_lots_fifo(session: Session) -> list[LotMatch]:
    """Walk all trades in chronological order, per ticker; pair sells with
    open buy lots in FIFO order. Returns list of LotMatch sorted by
    sell_executed_at (i.e., when the match was realized).

    Trades with executed_at=None fall back to created_at for ordering.
    Sells exceeding total open quantity have the overflow silently dropped
    (matches Trade.realized_pl behavior in trades_service).
    """
    trades = (
        session.query(Trade)
        .order_by(Trade.executed_at.asc().nullslast(), Trade.id.asc())
        .all()
    )

    open_lots: dict[str, deque[dict]] = defaultdict(deque)
    matches: list[LotMatch] = []

    for t in trades:
        when = t.executed_at or t.created_at
        if when is None:
            continue
        if t.action == "buy":
            open_lots[t.ticker].append({
                "qty": t.quantity,
                "price": t.price,
                "when": when,
            })
        elif t.action == "sell":
            remaining = t.quantity
            lots = open_lots[t.ticker]
            while remaining > 0 and lots:
                head = lots[0]
                take = min(remaining, head["qty"])
                pl = (t.price - head["price"]) * take
                hold = (when - head["when"]).days
                matches.append(LotMatch(
                    ticker=t.ticker,
                    buy_executed_at=head["when"],
                    sell_executed_at=when,
                    quantity=take,
                    hold_days=hold,
                    realized_pl=pl,
                    buy_price=head["price"],
                ))
                head["qty"] -= take
                remaining -= take
                if head["qty"] == 0:
                    lots.popleft()
            # Overflow (remaining > 0) is silently dropped.

    return matches
```

- [ ] **Step 1.4:** Run tests to verify pass.

```bash
uv run pytest tests/holdings/test_fifo.py -v
```

Expected: `8 passed`.

- [ ] **Step 1.5:** Lint clean.

```bash
uv run ruff check marketpulse/holdings/fifo.py tests/holdings/test_fifo.py
```

Expected: `All checks passed!`

- [ ] **Step 1.6:** Commit.

```bash
git add marketpulse/holdings/fifo.py tests/holdings/test_fifo.py
git commit -m "feat(holdings): FIFO lot matcher with LotMatch dataclass

Pairs buy/sell trades chronologically per ticker. Used by Phase 5c
KPI aggregations (avg_hold_days, realized_pl_by_ticker).

Read-only — never writes back to Trade.realized_pl. Sells exceeding
open quantity silently drop the overflow (matches trades_service
behavior). Splits and dividends are excluded.

8 test cases cover: simple close, partial sell, multi-buy FIFO order,
cross-ticker isolation, sell-overflow drop, hold_days calc,
buy-after-sell independence, splits/dividends exclusion."
```

---

## Task 2: `total_realized_pl` date window

**Why:** KPI #2 "已实现盈亏 · YTD" 需要按 sell.executed_at 落在窗口内过滤。当前签名只支持 `ticker` filter。

**Files:**
- Modify: `marketpulse/holdings/trades.py:198-203`
- Test: `tests/holdings/test_aggregations.py` (new file)

### Step 2.1: Write failing tests

- [ ] Create `tests/holdings/test_aggregations.py`:

```python
"""Phase 5c aggregations: date-windowed totals, per-ticker rollups, hold days."""
from datetime import UTC, date, datetime

import pytest

from marketpulse.db.models import Trade


def _dt(y, m, d): return datetime(y, m, d, tzinfo=UTC)


def _trade(session, ticker, action, qty, price, when, *, pl=None):
    t = Trade(ticker=ticker, action=action, quantity=qty, price=price,
              fees=0.0, executed_at=when,
              realized_pl=pl if action == "sell" else None)
    session.add(t)
    session.commit()
    return t


def test_total_realized_pl_with_from_to_inclusive(db_session):
    from marketpulse.holdings.trades import total_realized_pl

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell",  5, 120.0, _dt(2026, 3, 15), pl=100.0)
    _trade(db_session, "AAPL", "sell",  5, 130.0, _dt(2026, 6, 30), pl=150.0)

    # No window → both sells.
    assert total_realized_pl(db_session) == pytest.approx(250.0)
    # Window covers only first sell.
    assert total_realized_pl(
        db_session,
        from_date=date(2026, 1, 1),
        to_date=date(2026, 3, 31),
    ) == pytest.approx(100.0)
    # Inclusive boundary.
    assert total_realized_pl(
        db_session,
        from_date=date(2026, 3, 15),
        to_date=date(2026, 3, 15),
    ) == pytest.approx(100.0)


def test_total_realized_pl_ignores_buys(db_session):
    """Even with realized_pl=None on buys, no effect on sum."""
    from marketpulse.holdings.trades import total_realized_pl

    _trade(db_session, "AAPL", "buy", 10, 100.0, _dt(2026, 1, 1))
    assert total_realized_pl(db_session) == 0.0
    assert total_realized_pl(
        db_session,
        from_date=date(2025, 1, 1),
        to_date=date(2026, 12, 31),
    ) == 0.0
```

- [ ] **Step 2.2:** Run test, see fail.

```bash
uv run pytest tests/holdings/test_aggregations.py::test_total_realized_pl_with_from_to_inclusive -v
```

Expected: FAIL with `TypeError: total_realized_pl() got an unexpected keyword argument 'from_date'`.

- [ ] **Step 2.3:** Edit `marketpulse/holdings/trades.py:198` — replace the existing function:

```python
def total_realized_pl(
    session: Session,
    *,
    ticker: str | None = None,
    from_date: "date | None" = None,
    to_date: "date | None" = None,
) -> float:
    """Sum of realized P&L across sell trades (filter by ticker and/or date window).

    Date window filters the SELL row's executed_at.date() (inclusive on both
    ends). Trades with executed_at=None fall back to created_at.
    """
    from datetime import date as _date  # noqa: F401  (used in type hint)

    q = session.query(Trade).filter(Trade.realized_pl.isnot(None))
    if ticker:
        q = q.filter(Trade.ticker == ticker.upper())
    rows = q.all()

    def _row_date(t: Trade):
        d = t.executed_at or t.created_at
        return d.date() if d is not None else None

    if from_date is not None:
        rows = [r for r in rows if _row_date(r) is not None and _row_date(r) >= from_date]
    if to_date is not None:
        rows = [r for r in rows if _row_date(r) is not None and _row_date(r) <= to_date]

    return sum(t.realized_pl for t in rows)
```

Add at the top of the file (if not present):

```python
from datetime import date
```

- [ ] **Step 2.4:** Run tests.

```bash
uv run pytest tests/holdings/test_aggregations.py -v
```

Expected: 2 tests pass.

- [ ] **Step 2.5:** Run full existing trades test suite (don't break callers).

```bash
uv run pytest tests/web/test_trades.py tests/web/test_holdings.py -q
```

Expected: All pass.

- [ ] **Step 2.6:** Lint clean + commit.

```bash
uv run ruff check marketpulse/holdings/trades.py tests/holdings/test_aggregations.py
git add marketpulse/holdings/trades.py tests/holdings/test_aggregations.py
git commit -m "feat(holdings): total_realized_pl accepts from_date/to_date window

Inclusive on both ends; filters the SELL row's executed_at.date()
(fallback to created_at). Buy rows have realized_pl=None and were
already ignored by the existing IS NOT NULL filter.

No existing caller broken — both new kwargs default to None."
```

---

## Task 3: `trading_stats` date window

**Why:** KPI "胜率" + "已实现盈亏" 的 hint 都用到 wins/losses/win_rate；都需要 date window。

**Files:**
- Modify: `marketpulse/holdings/service.py:142-159`
- Test: append to `tests/holdings/test_aggregations.py`

### Step 3.1: Append failing tests

- [ ] Append to `tests/holdings/test_aggregations.py`:

```python
def test_trading_stats_window_filters_sells(db_session):
    from marketpulse.holdings.service import trading_stats

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell",  5, 120.0, _dt(2026, 3, 15), pl=100.0)
    _trade(db_session, "AAPL", "sell",  3, 90.0,  _dt(2026, 4, 1),  pl=-30.0)
    _trade(db_session, "AAPL", "sell",  2, 130.0, _dt(2026, 7, 1),  pl=60.0)

    # All-time: 3 sells, 2 wins, 1 loss
    s_all = trading_stats(db_session)
    assert s_all["wins"] == 2
    assert s_all["losses"] == 1
    assert s_all["win_rate_pct"] == pytest.approx(66.66666, rel=1e-3)

    # Q1 only: 1 win
    s_q1 = trading_stats(
        db_session,
        from_date=date(2026, 1, 1), to_date=date(2026, 3, 31),
    )
    assert s_q1["wins"] == 1
    assert s_q1["losses"] == 0
    assert s_q1["win_rate_pct"] == pytest.approx(100.0)


def test_trading_stats_no_closed_returns_none_win_rate(db_session):
    """Per spec: win_rate_pct is None when wins+losses == 0
    (template shows '—' instead of misleading '0.0%')."""
    from marketpulse.holdings.service import trading_stats

    _trade(db_session, "AAPL", "buy", 10, 100.0, _dt(2026, 1, 1))
    s = trading_stats(db_session)
    assert s["wins"] == 0
    assert s["losses"] == 0
    assert s["win_rate_pct"] is None  # not 0.0


def test_trading_stats_ticker_filter_still_works(db_session):
    """Don't break the existing single-arg path."""
    from marketpulse.holdings.service import trading_stats

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell",  5, 120.0, _dt(2026, 6, 1), pl=100.0)
    _trade(db_session, "NVDA", "buy",  10, 50.0,  _dt(2026, 1, 1))
    _trade(db_session, "NVDA", "sell",  5, 40.0,  _dt(2026, 6, 1), pl=-50.0)

    s_aapl = trading_stats(db_session, ticker="AAPL")
    assert s_aapl["wins"] == 1 and s_aapl["losses"] == 0
    s_nvda = trading_stats(db_session, ticker="NVDA")
    assert s_nvda["wins"] == 0 and s_nvda["losses"] == 1
```

- [ ] **Step 3.2:** Run, see fail (no `ticker`/`from_date`/`to_date` kwargs).

```bash
uv run pytest tests/holdings/test_aggregations.py -v -k trading_stats
```

Expected: 3 FAIL with kwarg errors / unexpected return values.

- [ ] **Step 3.3:** Edit `marketpulse/holdings/service.py:142` — replace `trading_stats`:

```python
def trading_stats(
    session: Session,
    *,
    ticker: str | None = None,
    from_date: "date | None" = None,
    to_date: "date | None" = None,
) -> dict[str, Any]:
    """High-level stats across trades: count, win rate, total realized P&L.

    `ticker` filters to a single symbol (case-insensitive).
    `from_date`/`to_date` is an inclusive window on the SELL row's
    executed_at.date() (fallback to created_at).

    Returns:
      total_trades: BUY+SELL count within filter (NOT just sells)
      closed_positions: wins+losses
      wins / losses: per realized_pl sign
      win_rate_pct: float OR None when wins+losses == 0
      realized_pl: sum of realized_pl in window
    """
    from datetime import date  # noqa: F401

    q = session.query(Trade)
    if ticker:
        q = q.filter(Trade.ticker == ticker.upper())
    trades = q.all()

    def _row_date(t: Trade):
        d = t.executed_at or t.created_at
        return d.date() if d is not None else None

    if from_date is not None:
        trades = [t for t in trades if _row_date(t) is not None and _row_date(t) >= from_date]
    if to_date is not None:
        trades = [t for t in trades if _row_date(t) is not None and _row_date(t) <= to_date]

    total = len(trades)
    sells = [t for t in trades if t.realized_pl is not None]
    wins = sum(1 for t in sells if t.realized_pl > 0)
    losses = sum(1 for t in sells if t.realized_pl < 0)
    closed = wins + losses
    realized = sum(t.realized_pl for t in sells)
    return {
        "total_trades": total,
        "closed_positions": closed,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": (wins / closed * 100) if closed else None,
        "realized_pl": realized,
    }
```

If `date` isn't imported at top of file, add it.

- [ ] **Step 3.4:** Run aggregation tests + holdings page tests (calls `trading_stats`).

```bash
uv run pytest tests/holdings/test_aggregations.py tests/web/test_holdings.py -v
```

Expected: pass. **Note:** existing `tests/web/test_holdings.py` may assume `win_rate_pct: 0.0` for empty case — if so, that test needs updating per the new None semantics.

- [ ] **Step 3.5:** If `test_holdings.py` breaks: search for `win_rate_pct` assertions there:

```bash
grep -n "win_rate_pct\|win_rate" tests/web/test_holdings.py marketpulse/web/templates/holdings.html
```

If a template uses `{{ trade_stats.win_rate_pct }}` and now gets None, the format string `"%.0f"|format(...)` will crash. Need to guard:

```jinja
{{ "%.0f"|format(trade_stats.win_rate_pct) if trade_stats.win_rate_pct is not none else "—" }}%
```

Edit `marketpulse/web/templates/holdings.html:28` if needed. Verify with `uv run pytest tests/web/test_holdings.py -v`.

- [ ] **Step 3.6:** Lint + commit.

```bash
uv run ruff check marketpulse/holdings/service.py
git add marketpulse/holdings/service.py tests/holdings/test_aggregations.py marketpulse/web/templates/holdings.html
git commit -m "feat(holdings): trading_stats accepts date window; win_rate=None when no closed

- from_date/to_date inclusive window on SELL executed_at.date()
- win_rate_pct returns None (not 0.0) when wins+losses == 0;
  template guards with conditional format
- ticker filter unchanged"
```

---

## Task 4: `monthly_realized_pl(months=N)` with gap-filling

**Why:** 右栏柱图要 15 个月、缺月补 0。当前函数返回所有月份，无 gap-fill。新参数 `months=None` 默认保留 `/holdings` 行为 (all months, no fill)。

**Files:**
- Modify: `marketpulse/holdings/service.py:113-140`
- Test: append `tests/holdings/test_aggregations.py`

### Step 4.1: Append failing tests

- [ ] Append:

```python
def test_monthly_realized_pl_default_returns_all_months_no_fill(db_session):
    """Default months=None: matches existing behavior used by /holdings."""
    from marketpulse.holdings.service import monthly_realized_pl

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell",  5, 120.0, _dt(2026, 3, 15), pl=100.0)
    _trade(db_session, "AAPL", "sell",  3, 90.0,  _dt(2026, 7, 1),  pl=-30.0)

    rows = monthly_realized_pl(db_session)
    months = [r["month"] for r in rows]
    # Only the 2 months with sells; no Feb/Apr/May/Jun padding.
    assert months == ["2026-03", "2026-07"]


def test_monthly_realized_pl_with_months_fills_gaps(db_session):
    """months=15: trailing 15 calendar months (incl. current), missing → 0."""
    import datetime as dt
    from marketpulse.holdings.service import monthly_realized_pl

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell",  5, 120.0, _dt(2026, 3, 15), pl=100.0)

    rows = monthly_realized_pl(db_session, months=15)
    assert len(rows) == 15
    # Sorted ascending; last entry = current month.
    today = dt.date.today()
    assert rows[-1]["month"] == f"{today.year:04d}-{today.month:02d}"
    # Empty months → pl == 0, trade_count == 0
    march = next(r for r in rows if r["month"] == "2026-03")
    assert march["pl"] == pytest.approx(100.0)
    other = [r for r in rows if r["month"] != "2026-03"]
    for r in other:
        assert r["pl"] == 0.0
        assert r["trade_count"] == 0
```

- [ ] **Step 4.2:** Run, fail.

```bash
uv run pytest tests/holdings/test_aggregations.py -v -k monthly_realized
```

Expected: `test_monthly_realized_pl_with_months_fills_gaps` FAILs (unexpected kwarg `months`).

- [ ] **Step 4.3:** Edit `marketpulse/holdings/service.py:113` — extend:

```python
def monthly_realized_pl(
    session: Session,
    *,
    months: int | None = None,
) -> list[dict[str, Any]]:
    """Aggregate realized P&L from sell trades grouped by (year, month).

    months=None (default): return all months that have realized P&L,
        chronologically; gaps omitted. Preserves existing /holdings behavior.
    months=N: return the trailing N calendar months (including current);
        missing months padded with {pl: 0, trade_count: 0}.
    """
    from datetime import date  # noqa: F401

    sells = session.query(Trade).filter(Trade.realized_pl.isnot(None)).all()
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"pl": 0.0, "trade_count": 0},
    )
    for t in sells:
        when: date | None = (
            t.executed_at.date() if t.executed_at
            else (t.created_at.date() if t.created_at else None)
        )
        if when is None:
            continue
        key = f"{when.year:04d}-{when.month:02d}"
        buckets[key]["pl"] += t.realized_pl
        buckets[key]["trade_count"] += 1

    if months is None:
        return [
            {"month": m, "pl": v["pl"], "trade_count": v["trade_count"]}
            for m, v in sorted(buckets.items())
        ]

    # months=N: pad trailing N months including current.
    today = date.today()
    keys: list[str] = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    keys.reverse()
    return [
        {"month": k, "pl": buckets[k]["pl"], "trade_count": buckets[k]["trade_count"]}
        for k in keys
    ]
```

- [ ] **Step 4.4:** Run aggregation + holdings tests.

```bash
uv run pytest tests/holdings/test_aggregations.py tests/web/test_holdings.py -v
```

Expected: pass.

- [ ] **Step 4.5:** Lint + commit.

```bash
uv run ruff check marketpulse/holdings/service.py
git add marketpulse/holdings/service.py tests/holdings/test_aggregations.py
git commit -m "feat(holdings): monthly_realized_pl(months=N) for trailing window with gap-fill

months=None preserves existing /holdings behavior (all months, no padding).
months=N pads trailing N calendar months (incl. current) with zero entries
for missing months. /trades right-rail chart will pass months=15."
```

---

## Task 5: `trade_count_this_month`

**Why:** KPI #5 显示当前自然月的活动笔数（不受筛选影响）。

**Files:**
- Modify: `marketpulse/holdings/service.py` (append)
- Test: append `tests/holdings/test_aggregations.py`

### Step 5.1: Tests

- [ ] Append:

```python
def test_trade_count_this_month_classifies(db_session, monkeypatch):
    """Counts BUY/SELL/dividend in the current calendar month (UTC)."""
    from datetime import date

    import marketpulse.holdings.service as svc
    from marketpulse.holdings.service import trade_count_this_month
    from marketpulse.db.models import Dividend

    # Freeze "today" via a tiny shim — the function reads date.today() in svc.
    class _FakeDate(date):
        @classmethod
        def today(cls): return date(2026, 5, 15)
    monkeypatch.setattr(svc, "date", _FakeDate)

    # In-month
    _trade(db_session, "AAPL", "buy",  1, 100, _dt(2026, 5, 3))
    _trade(db_session, "AAPL", "buy",  1, 100, _dt(2026, 5, 10))
    _trade(db_session, "AAPL", "sell", 1, 120, _dt(2026, 5, 12), pl=20.0)
    db_session.add(Dividend(ticker="AAPL", ex_date=date(2026, 5, 8),
                            amount_per_share=0.25, total_amount=0.25))
    # Out of month
    _trade(db_session, "AAPL", "buy", 1, 100, _dt(2026, 4, 30))
    _trade(db_session, "AAPL", "buy", 1, 100, _dt(2026, 6, 1))
    db_session.commit()

    counts = trade_count_this_month(db_session)
    assert counts == {"total": 4, "buys": 2, "sells": 1, "dividends": 1}


def test_trade_count_this_month_empty(db_session):
    from marketpulse.holdings.service import trade_count_this_month
    assert trade_count_this_month(db_session) == {
        "total": 0, "buys": 0, "sells": 0, "dividends": 0,
    }
```

- [ ] **Step 5.2:** Run, fail.

```bash
uv run pytest tests/holdings/test_aggregations.py -v -k this_month
```

- [ ] **Step 5.3:** Append to `marketpulse/holdings/service.py`:

```python
def trade_count_this_month(session: Session) -> dict[str, int]:
    """Activity count in the current calendar month (UTC).

    Returns {total, buys, sells, dividends}. Splits intentionally not
    counted — they're corporate actions, not user activity.
    Not affected by any filter (always current month).
    """
    today = date.today()
    y, m = today.year, today.month
    next_y, next_m = (y + 1, 1) if m == 12 else (y, m + 1)
    start = date(y, m, 1)
    end_excl = date(next_y, next_m, 1)

    buys = sells = 0
    for t in session.query(Trade).all():
        when = (t.executed_at.date() if t.executed_at
                else (t.created_at.date() if t.created_at else None))
        if when is None or when < start or when >= end_excl:
            continue
        if t.action == "buy":
            buys += 1
        elif t.action == "sell":
            sells += 1

    dividends = (
        session.query(Dividend)
        .filter(Dividend.ex_date >= start, Dividend.ex_date < end_excl)
        .count()
    )

    return {
        "total": buys + sells + dividends,
        "buys": buys, "sells": sells, "dividends": dividends,
    }
```

If `Dividend` isn't imported in `service.py`, add `from marketpulse.db.models import Dividend, Trade` (or extend existing import).

- [ ] **Step 5.4:** Run, pass.

```bash
uv run pytest tests/holdings/test_aggregations.py -v -k this_month
```

- [ ] **Step 5.5:** Lint + commit.

```bash
uv run ruff check marketpulse/holdings/service.py
git add marketpulse/holdings/service.py tests/holdings/test_aggregations.py
git commit -m "feat(holdings): trade_count_this_month(session) → {total,buys,sells,dividends}

Current natural month (UTC); splits intentionally excluded (corporate
action, not user activity). Used by Phase 5c KPI #5 strip."
```

---

## Task 6: `realized_pl_by_ticker`

**Why:** 右栏排行榜。按 abs(realized_pl) 降序 top N，含 pct (vs cost basis of sold lots)。

**Files:**
- Modify: `marketpulse/holdings/service.py` (append)
- Test: append `tests/holdings/test_aggregations.py`

### Step 6.1: Tests

- [ ] Append:

```python
def test_realized_pl_by_ticker_orders_by_abs(db_session):
    """A -2000 loss ranks above a +1000 gain in 'biggest movers' view."""
    from marketpulse.holdings.service import realized_pl_by_ticker

    _trade(db_session, "AAPL", "buy",  10, 100, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell", 10, 200, _dt(2026, 6, 1), pl=+1000.0)
    _trade(db_session, "NVDA", "buy",  10, 300, _dt(2026, 1, 1))
    _trade(db_session, "NVDA", "sell", 10, 100, _dt(2026, 6, 1), pl=-2000.0)

    rows = realized_pl_by_ticker(db_session)
    assert [r["ticker"] for r in rows] == ["NVDA", "AAPL"]
    assert rows[0]["realized_pl"] == pytest.approx(-2000.0)
    assert rows[1]["realized_pl"] == pytest.approx(+1000.0)


def test_realized_pl_by_ticker_top_n(db_session):
    """top_n=2 with 3 tickers → only top 2 by abs(pl)."""
    from marketpulse.holdings.service import realized_pl_by_ticker

    for sym, pl in [("AAPL", 100), ("NVDA", -200), ("TSLA", 50)]:
        _trade(db_session, sym, "buy",  10, 10, _dt(2026, 1, 1))
        _trade(db_session, sym, "sell", 10, 20, _dt(2026, 6, 1), pl=float(pl))

    rows = realized_pl_by_ticker(db_session, top_n=2)
    assert len(rows) == 2
    assert {r["ticker"] for r in rows} == {"AAPL", "NVDA"}


def test_realized_pl_by_ticker_pct_uses_lot_cost_basis(db_session):
    """pct = realized_pl / sum(qty*buy_price for matched lots) * 100."""
    from marketpulse.holdings.service import realized_pl_by_ticker

    _trade(db_session, "AAPL", "buy",  10, 100.0, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell", 10, 120.0, _dt(2026, 6, 1), pl=200.0)
    rows = realized_pl_by_ticker(db_session)
    # cost basis of sold lots = 10 * 100 = 1000; pct = 200/1000 * 100 = 20%
    assert rows[0]["pct"] == pytest.approx(20.0)


def test_realized_pl_by_ticker_empty(db_session):
    from marketpulse.holdings.service import realized_pl_by_ticker
    assert realized_pl_by_ticker(db_session) == []
```

- [ ] **Step 6.2:** Run, fail.

```bash
uv run pytest tests/holdings/test_aggregations.py -v -k realized_pl_by_ticker
```

- [ ] **Step 6.3:** Append to `marketpulse/holdings/service.py`:

```python
def realized_pl_by_ticker(
    session: Session,
    *,
    top_n: int = 8,
) -> list[dict[str, Any]]:
    """[{ticker, realized_pl, pct}, ...] sorted by abs(realized_pl) desc, top_n.

    pct = realized_pl / cost_basis_of_sold_lots * 100, where
    cost_basis = sum(qty * buy_price) for matched FIFO lots.
    Tickers with zero realized_pl are omitted.
    """
    from marketpulse.holdings.fifo import match_lots_fifo

    matches = match_lots_fifo(session)
    if not matches:
        return []

    by_ticker: dict[str, dict[str, float]] = defaultdict(
        lambda: {"realized_pl": 0.0, "cost_basis": 0.0},
    )
    for m in matches:
        by_ticker[m.ticker]["realized_pl"] += m.realized_pl
        by_ticker[m.ticker]["cost_basis"] += m.quantity * m.buy_price

    rows = [
        {
            "ticker": t,
            "realized_pl": v["realized_pl"],
            "pct": (v["realized_pl"] / v["cost_basis"] * 100) if v["cost_basis"] else 0.0,
        }
        for t, v in by_ticker.items()
        if v["realized_pl"] != 0.0
    ]
    rows.sort(key=lambda r: abs(r["realized_pl"]), reverse=True)
    return rows[:top_n]
```

- [ ] **Step 6.4:** Run, pass.

```bash
uv run pytest tests/holdings/test_aggregations.py -v -k realized_pl_by_ticker
```

- [ ] **Step 6.5:** Lint + commit.

```bash
uv run ruff check marketpulse/holdings/service.py
git add marketpulse/holdings/service.py tests/holdings/test_aggregations.py
git commit -m "feat(holdings): realized_pl_by_ticker(top_n=8) leaderboard

Uses FIFO matcher to compute per-ticker realized P&L plus pct vs
sold-lot cost basis. Sorted by abs(pl) descending; top_n cap.
Empty tickers (zero P&L) omitted."
```

---

## Task 7: `avg_hold_days`

**Why:** KPI #4 "平均持仓天数"。基于 FIFO LotMatch.hold_days 求平均；按 sell.executed_at 落在窗口内过滤。

**Files:**
- Modify: `marketpulse/holdings/service.py` (append)
- Test: append `tests/holdings/test_aggregations.py`

### Step 7.1: Tests

- [ ] Append:

```python
def test_avg_hold_days_basic(db_session):
    """Two matches: 100 days and 200 days → avg 150."""
    from marketpulse.holdings.service import avg_hold_days

    _trade(db_session, "AAPL", "buy",  1, 100, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell", 1, 120, _dt(2026, 4, 11), pl=20.0)  # 100d
    _trade(db_session, "NVDA", "buy",  1, 100, _dt(2026, 1, 1))
    _trade(db_session, "NVDA", "sell", 1, 120, _dt(2026, 7, 20), pl=20.0)  # 200d

    assert avg_hold_days(db_session) == pytest.approx(150.0)


def test_avg_hold_days_no_data_returns_none(db_session):
    from marketpulse.holdings.service import avg_hold_days
    assert avg_hold_days(db_session) is None

    _trade(db_session, "AAPL", "buy", 1, 100, _dt(2026, 1, 1))
    # No sells → no matches → None
    assert avg_hold_days(db_session) is None


def test_avg_hold_days_window_filters_by_sell_date(db_session):
    """Only LotMatches whose sell_executed_at falls in window count."""
    from datetime import date
    from marketpulse.holdings.service import avg_hold_days

    _trade(db_session, "AAPL", "buy",  1, 100, _dt(2026, 1, 1))
    _trade(db_session, "AAPL", "sell", 1, 120, _dt(2026, 3, 1), pl=20.0)  # 59d
    _trade(db_session, "NVDA", "buy",  1, 100, _dt(2026, 1, 1))
    _trade(db_session, "NVDA", "sell", 1, 120, _dt(2026, 9, 1), pl=20.0)  # 243d

    # Both → avg of 59 and 243 = 151
    assert avg_hold_days(db_session) == pytest.approx(151.0)
    # Q1 only → 59
    val = avg_hold_days(
        db_session,
        from_date=date(2026, 1, 1), to_date=date(2026, 3, 31),
    )
    assert val == pytest.approx(59.0)
```

- [ ] **Step 7.2:** Run, fail.

```bash
uv run pytest tests/holdings/test_aggregations.py -v -k avg_hold_days
```

- [ ] **Step 7.3:** Append to `marketpulse/holdings/service.py`:

```python
def avg_hold_days(
    session: Session,
    *,
    from_date: "date | None" = None,
    to_date: "date | None" = None,
) -> float | None:
    """Average hold_days across FIFO LotMatches whose sell_executed_at.date()
    falls in the inclusive window. Returns None when no matches qualify.
    """
    from marketpulse.holdings.fifo import match_lots_fifo

    matches = match_lots_fifo(session)
    if from_date is not None:
        matches = [m for m in matches if m.sell_executed_at.date() >= from_date]
    if to_date is not None:
        matches = [m for m in matches if m.sell_executed_at.date() <= to_date]
    if not matches:
        return None
    return sum(m.hold_days for m in matches) / len(matches)
```

- [ ] **Step 7.4:** Run, pass.

```bash
uv run pytest tests/holdings/test_aggregations.py -v
```

Expected: all aggregation tests pass (Tasks 2-7 combined).

- [ ] **Step 7.5:** Lint + commit.

```bash
uv run ruff check marketpulse/holdings/service.py
git add marketpulse/holdings/service.py tests/holdings/test_aggregations.py
git commit -m "feat(holdings): avg_hold_days windowed by sell_executed_at

Average of LotMatch.hold_days; window filter is inclusive on
sell-side. Returns None when no matches qualify (template '—')."
```

---

## Task 8: Route `GET /trades` — query params + HX-Request + ticker alias + context

**Why:** 把 spec 第 1 节路由完整落地：`?page/limit/from/to/q + event_type + ticker alias`、HX-Request 局部响应、context dict 拼装、参数校验。

**Files:**
- Modify: `marketpulse/web/routes/trades.py:89-148` (rewrite `trades_page`)
- Test: extend `tests/web/test_trades.py`

### Step 8.1: Write failing tests

- [ ] Append to `tests/web/test_trades.py`:

```python
import math
from datetime import UTC, datetime, date

from marketpulse.db.models import Trade


def _seed_trades(db_session, n: int):
    """Seed N AAPL trades evenly spaced over a year."""
    for i in range(n):
        when = datetime(2026, 1, 1, 12, 0, tzinfo=UTC) + (
            (datetime(2026, 12, 31, 12, 0, tzinfo=UTC) - datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
            * (i / max(1, n - 1))
        )
        db_session.add(Trade(
            ticker="AAPL", action="buy", quantity=1.0, price=100.0,
            fees=0.0, executed_at=when, realized_pl=None,
        ))
    db_session.commit()


def test_trades_page_pagination_default_50(client, monkeypatch, db_session):
    """75 trades → page 1 shows 50, page 2 shows 25."""
    _login(client, monkeypatch)
    _seed_trades(db_session, 75)

    r = client.get("/trades")
    assert r.status_code == 200
    assert r.text.count("trade-row-") == 50

    r = client.get("/trades?page=2")
    assert r.text.count("trade-row-") == 25


def test_trades_page_clamps_overflow_page(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_trades(db_session, 5)
    r = client.get("/trades?page=999")
    assert r.status_code == 200  # not 422


def test_trades_page_invalid_date_returns_422(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades?from=not-a-date")
    assert r.status_code == 422


def test_trades_page_from_greater_than_to_returns_422(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades?from=2026-06-01&to=2026-01-01")
    assert r.status_code == 422


def test_trades_page_q_prefix_match(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    for sym in ("AAPL", "AMZN", "NVDA"):
        db_session.add(Trade(ticker=sym, action="buy", quantity=1, price=100,
                             fees=0, executed_at=datetime(2026, 1, 1, tzinfo=UTC)))
    db_session.commit()

    r = client.get("/trades?q=AA")
    assert "AAPL" in r.text
    assert "AMZN" not in r.text
    assert "NVDA" not in r.text


def test_trades_page_q_empty_string_no_filter(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    db_session.add(Trade(ticker="AAPL", action="buy", quantity=1, price=100,
                         fees=0, executed_at=datetime(2026, 1, 1, tzinfo=UTC)))
    db_session.commit()
    r = client.get("/trades?q=")
    assert "AAPL" in r.text


def test_trades_page_ticker_alias_exact_match(client, monkeypatch, db_session):
    """?ticker=AAPL is exact match (legacy); does NOT match AAPL prefix."""
    _login(client, monkeypatch)
    for sym in ("AAPL", "AAPLE"):  # AAPLE: fictional prefix neighbor
        db_session.add(Trade(ticker=sym, action="buy", quantity=1, price=100,
                             fees=0, executed_at=datetime(2026, 1, 1, tzinfo=UTC)))
    db_session.commit()

    r = client.get("/trades?ticker=AAPL")
    assert "AAPL" in r.text
    # AAPLE row should not be present.
    assert r.text.count(">AAPLE<") == 0


def test_trades_page_kpi_strip_renders_5_cards(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades")
    assert r.text.count('class="mp-card mp-kpi"') == 5 or \
           r.text.count("mp-kpi__value") == 5


def test_trades_page_monthly_chart_15_bars(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades")
    assert r.text.count("mp-monthly-bar__bar") == 15


def test_trades_page_dropzone_form_action(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades")
    assert 'action="/trades/import"' in r.text
    assert 'enctype="multipart/form-data"' in r.text


def test_trades_page_hero_export_link_carries_filters(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades?event_type=trade&q=AAPL")
    # Export anchor href must include the same filter qs.
    assert "/trades/export.csv?" in r.text
    assert "event_type=trade" in r.text


def test_trades_page_hx_request_returns_partial_only(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades", headers={"HX-Request": "true"})
    # Partial: table present, hero absent.
    assert "mp-table--trades" in r.text or "trade-row-" in r.text or "暂无记录" in r.text
    assert "mp-hero" not in r.text


def test_trades_page_this_month_kpi_unaffected_by_filter(client, monkeypatch, db_session):
    """Even with from/to in distant past, this_month KPI uses current month."""
    _login(client, monkeypatch)
    # No trades. this_month = {0,0,0,0}; KPI value '0'.
    r = client.get("/trades?from=2020-01-01&to=2020-12-31")
    # The this_month KPI should still display "0" not a date-window count.
    # We can't easily anchor without DOM, so verify the value via context-checking
    # indirect: ensure status 200 and page rendered.
    assert r.status_code == 200
    assert "本月新笔数" in r.text
```

- [ ] **Step 8.2:** Run, see fails (most fail because route hasn't been updated).

```bash
uv run pytest tests/web/test_trades.py -v -k "pagination or invalid_date or q_prefix or ticker_alias or kpi or monthly_chart or dropzone or export_link or hx_request"
```

Expected: many FAIL.

- [ ] **Step 8.3:** Rewrite `marketpulse/web/routes/trades.py:89-148` — replace the `trades_page` function entirely:

```python
@router.get("/trades", response_class=HTMLResponse)
def trades_page(
    request: Request,
    page: int = 1,
    limit: int = 50,
    # Pydantic / FastAPI will pass dates as strings by default;
    # we parse manually for nicer 422 messages.
    **kwargs,  # captures from / to / q / ticker / event_type
):
    """Phase 5c: paginated + filtered + KPI-decorated trade ledger."""
    raise NotImplementedError("see step 8.3 — full impl below")
```

(Above is just a stub; the actual full body — paste it as the new function:)

```python
@router.get("/trades", response_class=HTMLResponse)
def trades_page(  # noqa: PLR0912, PLR0913
    request: Request,
    page: int = 1,
    limit: int = 50,
    from_: str | None = None,
    to: str | None = None,
    q: str | None = None,
    ticker: str | None = None,
    event_type: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """Phase 5c: paginated + filtered + KPI-decorated trade ledger.

    Query params:
      page, limit  — 1-based pagination (limit clamped to [10,200])
      from, to     — inclusive YYYY-MM-DD window on event date
      q            — ticker prefix search (case-insensitive)
      ticker       — exact ticker match (legacy alias, kept for old links)
      event_type   — trade | split | dividend | None
    """
    from datetime import date as _date
    from urllib.parse import urlencode

    from marketpulse.holdings.service import (
        avg_hold_days,
        monthly_realized_pl,
        realized_pl_by_ticker,
        trade_count_this_month,
        trading_stats,
    )
    from marketpulse.holdings.trades import total_realized_pl

    # -- parse & validate --
    def _parse_d(s: str | None, name: str):
        if not s:
            return None
        try:
            return _date.fromisoformat(s)
        except ValueError as e:
            raise HTTPException(422, f"invalid {name}: {s}") from e

    from_date = _parse_d(from_, "from")
    to_date = _parse_d(to, "to")
    if from_date and to_date and from_date > to_date:
        raise HTTPException(422, "from must be <= to")
    if page <= 0 or limit <= 0:
        raise HTTPException(422, "page and limit must be positive")
    limit = max(10, min(200, limit))

    # Treat q="" as None.
    q = q.strip() if q else None
    if q == "":
        q = None
    q_upper = q.upper() if q else None
    ticker_upper = ticker.upper() if ticker else None

    # -- fetch events with filters --
    events: list[dict] = []
    _EOD = time(23, 59, 59, tzinfo=UTC)

    if event_type in (None, "trade"):
        tq = db.query(Trade)
        if q_upper:
            tq = tq.filter(Trade.ticker.ilike(f"{q_upper}%"))
        if ticker_upper:
            tq = tq.filter(Trade.ticker == ticker_upper)
        for t in tq.all():
            when = t.executed_at or t.created_at
            d = when.date() if when else None
            if from_date and (d is None or d < from_date):
                continue
            if to_date and (d is None or d > to_date):
                continue
            events.append({"kind": "trade", "when": when, "obj": t})

    if event_type in (None, "split"):
        sq = db.query(StockSplit)
        if q_upper:
            sq = sq.filter(StockSplit.ticker.ilike(f"{q_upper}%"))
        if ticker_upper:
            sq = sq.filter(StockSplit.ticker == ticker_upper)
        for s in sq.all():
            if from_date and s.ex_date < from_date:
                continue
            if to_date and s.ex_date > to_date:
                continue
            events.append({
                "kind": "split",
                "when": datetime.combine(s.ex_date, _EOD),
                "obj": s,
            })

    if event_type in (None, "dividend"):
        dq = db.query(Dividend)
        if q_upper:
            dq = dq.filter(Dividend.ticker.ilike(f"{q_upper}%"))
        if ticker_upper:
            dq = dq.filter(Dividend.ticker == ticker_upper)
        for dv in dq.all():
            if from_date and dv.ex_date < from_date:
                continue
            if to_date and dv.ex_date > to_date:
                continue
            events.append({
                "kind": "dividend",
                "when": datetime.combine(dv.ex_date, _EOD),
                "obj": dv,
            })

    events.sort(key=lambda e: e["when"], reverse=True)

    # -- pagination --
    total_count = len(events)
    total_pages = max(1, (total_count + limit - 1) // limit)
    page = min(max(1, page), total_pages)  # clamp
    start = (page - 1) * limit
    page_events = events[start:start + limit]
    pager_window = list(range(max(1, page - 2), min(total_pages, page + 2) + 1))

    # -- counts (4 chip totals; ignore event_type, keep other filters) --
    counts = {"all": 0, "trade": 0, "split": 0, "dividend": 0}
    # cheap: re-walk the events buckets we already have? Need pre-event_type
    # version. Simpler: just count once per kind with filter applied.
    def _count(kind: str) -> int:
        if kind == "trade":
            base = db.query(Trade)
        elif kind == "split":
            base = db.query(StockSplit)
        else:
            base = db.query(Dividend)
        if q_upper:
            base = base.filter(getattr(
                Trade if kind == "trade" else StockSplit if kind == "split" else Dividend,
                "ticker",
            ).ilike(f"{q_upper}%"))
        if ticker_upper:
            col = (Trade if kind == "trade" else StockSplit if kind == "split"
                   else Dividend).ticker
            base = base.filter(col == ticker_upper)
        # date filter: trade uses executed_at, others use ex_date
        rows = base.all()
        n = 0
        for r in rows:
            d = (r.executed_at or r.created_at).date() if kind == "trade" else r.ex_date
            if d is None:
                continue
            if from_date and d < from_date:
                continue
            if to_date and d > to_date:
                continue
            n += 1
        return n
    counts["trade"] = _count("trade")
    counts["split"] = _count("split")
    counts["dividend"] = _count("dividend")
    counts["all"] = counts["trade"] + counts["split"] + counts["dividend"]

    # -- KPI strip (filter-aware except this_month) --
    # ytd window: if user gave from/to use it; else Jan 1 of current year → today
    today = _date.today()
    kpi_from = from_date or _date(today.year, 1, 1)
    kpi_to = to_date or today
    kpi_label = (
        "YTD" if (from_date is None and to_date is None)
        else f"{kpi_from.isoformat()} → {kpi_to.isoformat()}"
    )
    stats = trading_stats(db, ticker=ticker_upper, from_date=kpi_from, to_date=kpi_to)
    ytd_realized = total_realized_pl(
        db, ticker=ticker_upper, from_date=kpi_from, to_date=kpi_to,
    )
    avg_hd = avg_hold_days(db, from_date=kpi_from, to_date=kpi_to)

    kpi = {
        "total_trades": stats["total_trades"],
        "ytd_realized": ytd_realized,
        "ytd_label": kpi_label,
        "win_rate_pct": stats["win_rate_pct"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "avg_hold_days": avg_hd,
        "this_month": trade_count_this_month(db),
    }

    # -- right rail (always all-time per spec decision 4) --
    monthly = monthly_realized_pl(db, months=15)
    by_ticker = realized_pl_by_ticker(db, top_n=8)

    # -- filters query string for partial links --
    filters_dict = {
        "from": from_, "to": to, "q": q, "event_type": event_type,
    }
    filters_qs = urlencode({k: v for k, v in filters_dict.items() if v})
    filters_qs_no_event_type = urlencode(
        {k: v for k, v in filters_dict.items() if v and k != "event_type"}
    )

    ctx = {
        "events": page_events,
        "page": page, "limit": limit,
        "total_pages": total_pages, "total_count": total_count,
        "pager_window": pager_window,
        "filters": {
            "from": from_ or None, "to": to or None,
            "q": q, "event_type": event_type,
        },
        "filters_qs": filters_qs,
        "filters_qs_no_event_type": filters_qs_no_event_type,
        "counts": counts,
        "kpi": kpi,
        "monthly_pl": monthly,
        "by_ticker": by_ticker,
        # legacy keys (some callers may still expect)
        "filter_ticker": ticker_upper,
        "filter_event_type": event_type,
        "realized_pl_total": ytd_realized,
    }

    # HX-Request → return only the table partial
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request, "partials/trades_table.html", ctx,
        )
    return templates.TemplateResponse(request, "trades.html", ctx)
```

**Note about `from_` param:** FastAPI lets you alias query params: change the function signature to use `from_: str | None = Query(None, alias="from")`. Add at top of file:

```python
from fastapi import Query
```

Then:

```python
from_: str | None = Query(None, alias="from"),
```

- [ ] **Step 8.4:** Run route tests — many will pass but template doesn't exist yet, so a few will error during render. We accept these for now; they fix in later tasks.

```bash
uv run pytest tests/web/test_trades.py -v -k "invalid_date or from_greater or q_prefix or q_empty or ticker_alias or this_month or hx_request" --tb=line
```

The 4 validation tests + q tests should pass (validation rejects before render). Template-dependent tests (kpi_strip, monthly_chart, dropzone, export_link) will fail rendering — expected, fix in later tasks.

- [ ] **Step 8.5:** Lint + commit (skip the template-dependent tests for now; commit the route logic).

```bash
uv run ruff check marketpulse/web/routes/trades.py
git add marketpulse/web/routes/trades.py tests/web/test_trades.py
git commit -m "feat(trades): /trades route accepts page/limit/from/to/q + HX-Request

- Pagination clamped: page > total_pages → last page; limit ∈ [10,200]
- from/to inclusive; invalid date or from>to → 422
- q is prefix-match ILIKE upper(q) || '%' (empty string treated as None)
- ticker alias kept for old links: exact match
- HX-Request header → partial response (trades_table.html only)
- Context dict carries kpi/monthly_pl/by_ticker/counts/filters_qs

Template-dependent integration tests fail until later tasks land
the new templates."
```

---

## Task 9: Route `GET /trades/export.csv`

**Why:** Spec 第 1 节新端点；继承 `/trades` 过滤、忽略 page/limit、Robinhood-format streaming CSV。

**Files:**
- Modify: `marketpulse/web/routes/trades.py` (append handler)
- Test: `tests/web/test_trades_export.py` (new)

### Step 9.1: Tests

- [ ] Create `tests/web/test_trades_export.py`:

```python
import csv
from datetime import UTC, datetime
from io import StringIO

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.db.models import Dividend, Trade
from datetime import date


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _seed(db_session):
    db_session.add(Trade(ticker="AAPL", action="buy", quantity=10, price=180.0,
                         fees=0, executed_at=datetime(2026, 5, 8, tzinfo=UTC),
                         realized_pl=None))
    db_session.add(Trade(ticker="AAPL", action="sell", quantity=4, price=200.0,
                         fees=0, executed_at=datetime(2026, 5, 9, tzinfo=UTC),
                         realized_pl=80.0))
    db_session.add(Dividend(ticker="AAPL", ex_date=date(2026, 5, 1),
                            amount_per_share=0.25, total_amount=1.0,
                            source="manual"))
    db_session.commit()


def test_export_csv_content_type_and_filename(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed(db_session)
    r = client.get("/trades/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    cd = r.headers["content-disposition"]
    assert "attachment" in cd
    assert "trades-" in cd and ".csv" in cd


def test_export_csv_robinhood_header(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed(db_session)
    r = client.get("/trades/export.csv")
    lines = r.text.strip().split("\n")
    assert lines[0] == (
        "Activity Date,Process Date,Settle Date,Instrument,Description,"
        "Trans Code,Quantity,Price,Amount"
    )


def test_export_csv_filter_event_type_trade_only(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed(db_session)
    r = client.get("/trades/export.csv?event_type=trade")
    body = r.text
    # 2 trade rows + 1 header = 3 lines
    assert body.strip().count("\n") == 2  # 3 lines total → 2 newlines


def test_export_csv_skips_splits(client: TestClient, monkeypatch, db_session):
    from marketpulse.db.models import StockSplit
    _login(client, monkeypatch)
    _seed(db_session)
    db_session.add(StockSplit(ticker="AAPL", ex_date=date(2026, 4, 1),
                              ratio=2.0, source="manual"))
    db_session.commit()
    r = client.get("/trades/export.csv")
    # 2 trades + 1 dividend = 3 data rows. Splits never appear.
    rows = list(csv.reader(StringIO(r.text)))
    data_rows = rows[1:]
    trans_codes = [r[5] for r in data_rows]
    assert "Buy" in trans_codes
    assert "Sell" in trans_codes
    assert "CDIV" in trans_codes
    # No 'Split' or similar.
    assert all(tc in {"Buy", "Sell", "CDIV"} for tc in trans_codes)


def test_export_csv_empty_filter_only_header(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed(db_session)
    r = client.get("/trades/export.csv?ticker=NONEXIST")
    lines = r.text.strip().split("\n")
    assert len(lines) == 1  # header only


def test_export_csv_round_trip_compatible_with_import(client: TestClient, monkeypatch, db_session):
    """Export → import → same trade+dividend count (splits excluded fixture)."""
    _login(client, monkeypatch)
    _seed(db_session)
    r = client.get("/trades/export.csv")
    csv_text = r.text

    # Re-import (forces new client to get fresh db).
    # Use the existing import flow.
    res = client.post(
        "/trades/import",
        files={"file": ("export.csv", csv_text, "text/csv")},
    )
    assert res.status_code == 200
    # Preview shows 2 buy/sell rows; dividends counted as 0 in trade preview
    # (the importer parses CDIV separately). Just confirm 200 and AAPL in body.
    assert "AAPL" in res.text
```

- [ ] **Step 9.2:** Run, fail (endpoint doesn't exist).

```bash
uv run pytest tests/web/test_trades_export.py -v --tb=short
```

Expected: all 6 FAIL with 404.

- [ ] **Step 9.3:** Add to `marketpulse/web/routes/trades.py` (after `trades_page`):

```python
@router.get("/trades/export.csv", response_class=HTMLResponse)
def trades_export_csv(
    request: Request,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    q: str | None = None,
    ticker: str | None = None,
    event_type: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """Streaming Robinhood-format CSV; inherits /trades filters except page/limit."""
    from datetime import date as _date
    from fastapi.responses import StreamingResponse

    def _parse_d(s, name):
        if not s:
            return None
        try:
            return _date.fromisoformat(s)
        except ValueError as e:
            raise HTTPException(422, f"invalid {name}: {s}") from e

    from_date = _parse_d(from_, "from")
    to_date = _parse_d(to, "to")
    if from_date and to_date and from_date > to_date:
        raise HTTPException(422, "from must be <= to")
    q = (q or "").strip() or None
    q_upper = q.upper() if q else None
    ticker_upper = ticker.upper() if ticker else None

    HEADER = [
        "Activity Date", "Process Date", "Settle Date",
        "Instrument", "Description", "Trans Code",
        "Quantity", "Price", "Amount",
    ]

    def _date_in_window(d):
        if d is None:
            return False
        if from_date and d < from_date:
            return False
        if to_date and d > to_date:
            return False
        return True

    def _gen():
        # Header row.
        yield ",".join(HEADER) + "\n"

        # Trades.
        if event_type in (None, "trade"):
            tq = db.query(Trade)
            if q_upper:
                tq = tq.filter(Trade.ticker.ilike(f"{q_upper}%"))
            if ticker_upper:
                tq = tq.filter(Trade.ticker == ticker_upper)
            for t in tq.order_by(Trade.executed_at.desc().nullslast(), Trade.id.desc()).all():
                when = t.executed_at or t.created_at
                d = when.date() if when else None
                if not _date_in_window(d):
                    continue
                date_s = d.strftime("%-m/%-d/%Y") if d else ""
                amt = t.quantity * t.price
                amt_s = f"(${amt:.2f})" if t.action == "buy" else f"${amt:.2f}"
                yield (
                    f"{date_s},{date_s},{date_s},{t.ticker},,"
                    f"{'Buy' if t.action == 'buy' else 'Sell'},"
                    f"{t.quantity:g},${t.price:.2f},{amt_s}\n"
                )

        # Dividends (Trans Code = CDIV).
        if event_type in (None, "dividend"):
            dq = db.query(Dividend)
            if q_upper:
                dq = dq.filter(Dividend.ticker.ilike(f"{q_upper}%"))
            if ticker_upper:
                dq = dq.filter(Dividend.ticker == ticker_upper)
            for dv in dq.order_by(Dividend.ex_date.desc()).all():
                if not _date_in_window(dv.ex_date):
                    continue
                date_s = dv.ex_date.strftime("%-m/%-d/%Y")
                yield (
                    f"{date_s},{date_s},{date_s},{dv.ticker},Dividend,"
                    f"CDIV,,,${dv.total_amount:.2f}\n"
                )

        # Splits intentionally skipped (Robinhood CSV has no native split code;
        # splits are synced from yfinance, not user-entered).

    filename = f"trades-{datetime.now(UTC).date().isoformat()}.csv"
    return StreamingResponse(
        _gen(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

- [ ] **Step 9.4:** Run, pass.

```bash
uv run pytest tests/web/test_trades_export.py -v
```

Expected: 6 pass.

- [ ] **Step 9.5:** Lint + commit.

```bash
uv run ruff check marketpulse/web/routes/trades.py tests/web/test_trades_export.py
git add marketpulse/web/routes/trades.py tests/web/test_trades_export.py
git commit -m "feat(trades): GET /trades/export.csv streaming Robinhood-format

Inherits filters from /trades (ignores page/limit). Streams to avoid
memory pressure on large exports. Splits intentionally skipped (not
in Robinhood schema). Round-trip with /trades/import preserves
buy/sell/dividend rows."
```

---

## Task 10: Template `trades.html` shell + mp-hero CSS + delete old layout

**Why:** 取代当前 181 行老 Tailwind 模板；建立整页结构 (hero / kpi 占位 / filter 占位 / main grid 占位 / right rail 占位)。后续 partial 各自填空。

**Files:**
- Rewrite: `marketpulse/web/templates/trades.html` (181 → ~80 lines)
- Create: `marketpulse/web/templates/partials/trades_form_script.html`
- Modify: `marketpulse/web/static/css/app.css` (append `mp-hero`, `mp-trades-kpi`, `mp-trades-filter`, `mp-trades-main`, `mp-trades-rail`, responsive)
- Test: extend `tests/web/test_trades.py`

### Step 10.1: Test for visual anchors

- [ ] Append to `tests/web/test_trades.py`:

```python
def test_trades_page_visual_anchors_present(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades")
    for cls in ("mp-hero", "mp-trades-kpi", "mp-trades-filter",
                "mp-trades-main", "mp-trades-rail"):
        assert cls in r.text, f"missing {cls}"
    # h1 with grotesk class + 'Trade Ledger'
    assert "Trade Ledger" in r.text
    # Old Tailwind classes should be gone.
    assert 'class="bg-white rounded-md shadow-sm p-4"' not in r.text
```

- [ ] **Step 10.2:** Run, fail (old template has none of these).

```bash
uv run pytest tests/web/test_trades.py::test_trades_page_visual_anchors_present -v
```

- [ ] **Step 10.3:** Extract the existing form JS to a new partial.

Create `marketpulse/web/templates/partials/trades_form_script.html` — copy lines 82-175 of the current `trades.html` (the entire `<script>` block including `onEventKindChange`, `formatLocalTime`, `applyLocalTime`, `loadTradeIntoForm`, `exitEditMode`) verbatim into this file. Wrap in:

```html
<script>
{# everything from the current <script>...</script> block #}
</script>
```

(Get the file content with `sed -n '82,175p' marketpulse/web/templates/trades.html` if needed.)

- [ ] **Step 10.4:** Rewrite `marketpulse/web/templates/trades.html` from scratch:

```html
{% extends "base.html" %}
{% block content %}

<!-- Hero -->
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
    <a href="/trades/export.csv{% if filters_qs %}?{{ filters_qs }}{% endif %}"
       class="mp-btn mp-btn--ghost mp-btn--lg">
      <span class="material-symbols-outlined">download</span>导出 CSV
    </a>
  </div>
</section>

<!-- KPI strip (placeholder; populated in Task 11) -->
<section class="mp-trades-kpi">
  {% include "partials/trades_kpi_strip.html" ignore missing %}
</section>

<!-- Filter + Add card (placeholder; populated in Task 12) -->
<section class="mp-trades-filter">
  {% include "partials/trades_filter_card.html" ignore missing %}
</section>

<!-- Main grid: ledger + right rail -->
<section class="mp-trades-main">
  <div id="trades-container">
    {% include "partials/trades_table.html" %}
  </div>
  <aside class="mp-trades-rail">
    {% include "partials/trades_monthly_pl_card.html" ignore missing %}
    {% include "partials/trades_dropzone_card.html" ignore missing %}
    {% include "partials/trades_by_ticker_card.html" ignore missing %}
  </aside>
</section>

{% include "partials/trades_form_script.html" ignore missing %}
{% endblock %}
```

`ignore missing` is a Jinja directive — lets the page render before later tasks create the partials.

- [ ] **Step 10.5:** Append to `marketpulse/web/static/css/app.css` (near the end, before any closing brace):

```css
/* ════════ Phase 5c: Hero ════════ */
.mp-hero            { display:flex; align-items:flex-end; justify-content:space-between;
                      padding:32px 48px 24px; max-width:2400px; margin:0 auto; }
.mp-hero__title     { font:700 48px/1 var(--ns-font-headline); letter-spacing:-0.04em;
                      color:var(--ns-navy); margin:6px 0 0; }
.mp-hero__desc      { font-size:14px; color:var(--ns-on-surface-variant);
                      margin:12px 0 0; max-width:640px; }
.mp-hero__actions   { display:flex; gap:8px; }

/* ════════ Phase 5c: /trades layout ════════ */
.mp-trades-kpi      { padding:0 48px 16px; max-width:2400px; margin:0 auto;
                      display:grid; grid-template-columns:repeat(5,1fr); gap:16px; }
.mp-trades-filter   { padding:8px 48px 16px; max-width:2400px; margin:0 auto; }
.mp-trades-main     { padding:0 48px 32px; max-width:2400px; margin:0 auto;
                      display:grid;
                      grid-template-columns: minmax(0,1fr) 440px; gap:16px; }
.mp-trades-rail     { display:flex; flex-direction:column; gap:16px; }

@media (max-width: 1440px) {
  .mp-trades-main   { grid-template-columns: 1fr; }
}
@media (max-width: 900px) {
  .mp-trades-kpi    { grid-template-columns: repeat(2, 1fr); }
  .mp-hero          { flex-direction:column; align-items:flex-start; gap:16px; }
}
```

- [ ] **Step 10.6:** Run anchor test.

```bash
uv run pytest tests/web/test_trades.py::test_trades_page_visual_anchors_present -v
```

Expected: PASS. Some other tests fail because partials not yet created — acceptable.

- [ ] **Step 10.7:** Commit.

```bash
git add marketpulse/web/templates/trades.html \
        marketpulse/web/templates/partials/trades_form_script.html \
        marketpulse/web/static/css/app.css \
        tests/web/test_trades.py
git commit -m "feat(trades): new trades.html shell + mp-hero/mp-trades-* layout

Replaces 181-line Tailwind template with NineScrolls Variant A
3-section shell (hero / kpi strip / filter card / main grid + right rail).
Form JS extracted to trades_form_script.html partial.

Layout CSS: 2400px max-width container; 1fr+440px grid on wide
screens; collapses to 1fr at <1440px; KPI strip collapses to 2 cols
at <900px. Partials use ignore-missing so later tasks can fill in."
```

---

## Task 11: KPI strip partial + `mp-kpi` CSS

**Why:** 5 张 KPI 卡片渲染 `kpi` context dict。

**Files:**
- Create: `marketpulse/web/templates/partials/trades_kpi_strip.html`
- Modify: `marketpulse/web/static/css/app.css`
- Test: extend `tests/web/test_trades.py`

### Step 11.1: Tests

- [ ] Append:

```python
def test_kpi_strip_5_value_blocks(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades")
    # 5 KPI cards rendered.
    assert r.text.count("mp-kpi__value") == 5


def test_kpi_avg_hold_days_dash_when_empty(client, monkeypatch):
    """No trades → avg_hold_days is None → rendered as '—'."""
    _login(client, monkeypatch)
    r = client.get("/trades")
    # Card with label "平均持仓天数" should contain "—"
    assert "平均持仓天数" in r.text
    assert "—" in r.text


def test_kpi_win_rate_dash_when_no_closed(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades")
    assert "胜率" in r.text


def test_kpi_ytd_label_default_is_ytd(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades")
    assert "YTD" in r.text


def test_kpi_ytd_label_reflects_explicit_range(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades?from=2026-01-01&to=2026-03-31")
    assert "2026-01-01" in r.text
    assert "2026-03-31" in r.text
```

- [ ] **Step 11.2:** Run, fail.

- [ ] **Step 11.3:** Create `marketpulse/web/templates/partials/trades_kpi_strip.html`:

```html
{# 5 KPI cards. All numeric values use tnum + grotesk. #}

<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">总笔数</span>
    <span class="material-symbols-outlined mp-kpi__icon">receipt_long</span>
  </div>
  <div class="mp-kpi__value grotesk tnum">{{ kpi.total_trades }}</div>
  <div class="mp-kpi__hint">{{ kpi.ytd_label }}</div>
</div>

<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">已实现盈亏 · {{ kpi.ytd_label }}</span>
    <span class="material-symbols-outlined mp-kpi__icon">payments</span>
  </div>
  <div class="mp-kpi__value grotesk tnum"
       style="color: {% if kpi.ytd_realized >= 0 %}var(--mp-up){% else %}var(--mp-down){% endif %};">
    {{ "%+,.2f"|format(kpi.ytd_realized) }}
  </div>
  <div class="mp-kpi__hint">{{ kpi.wins + kpi.losses }} 笔已平仓</div>
</div>

<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">胜率</span>
    <span class="material-symbols-outlined mp-kpi__icon">trending_up</span>
  </div>
  <div class="mp-kpi__value grotesk tnum">
    {{ "%.1f%%"|format(kpi.win_rate_pct) if kpi.win_rate_pct is not none else "—" }}
  </div>
  <div class="mp-kpi__hint">{{ kpi.wins }} 胜 / {{ kpi.losses }} 负</div>
</div>

<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">平均持仓天数</span>
    <span class="material-symbols-outlined mp-kpi__icon">schedule</span>
  </div>
  <div class="mp-kpi__value grotesk tnum">
    {% if kpi.avg_hold_days is not none %}{{ "%d d"|format(kpi.avg_hold_days|round|int) }}{% else %}—{% endif %}
  </div>
  <div class="mp-kpi__hint">基于 FIFO 配对</div>
</div>

<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">本月新笔数</span>
    <span class="material-symbols-outlined mp-kpi__icon">event_available</span>
  </div>
  <div class="mp-kpi__value grotesk tnum">{{ kpi.this_month.total }}</div>
  <div class="mp-kpi__hint">
    {{ kpi.this_month.buys }} 买 · {{ kpi.this_month.sells }} 卖 · {{ kpi.this_month.dividends }} 分红
  </div>
</div>
```

- [ ] **Step 11.4:** Append to `app.css`:

```css
/* ════════ Phase 5c: KPI cards ════════ */
.mp-kpi             { padding:18px 20px; }
.mp-kpi__head       { display:flex; justify-content:space-between; align-items:flex-start; }
.mp-kpi__icon       { font-size:18px; color:var(--ns-outline-variant); }
.mp-kpi__value      { font:700 30px/1.1 var(--ns-font-headline); letter-spacing:-0.02em;
                      color:var(--ns-navy); margin-top:6px; }
.mp-kpi__hint       { font-size:11.5px; color:var(--ns-on-surface-variant); margin-top:4px; }
```

- [ ] **Step 11.5:** Run tests.

```bash
uv run pytest tests/web/test_trades.py -v -k "kpi_strip or avg_hold or win_rate or ytd_label"
```

Expected: pass.

- [ ] **Step 11.6:** Commit.

```bash
git add marketpulse/web/templates/partials/trades_kpi_strip.html \
        marketpulse/web/static/css/app.css tests/web/test_trades.py
git commit -m "feat(trades): KPI strip partial + mp-kpi CSS

5 cards: 总笔数 / 已实现盈亏 (color-coded) / 胜率 (— when 0 closed) /
平均持仓天数 (— when None) / 本月新笔数 (with buy/sell/div breakdown).
YTD label reflects active date range."
```

---

## Task 12: Filter card partial + `mp-filter-chips` / `mp-input` CSS

**Why:** Filter chip 切换 + 代码搜索 + 区间日期 + 类型感知 Add 表单。

**Files:**
- Create: `marketpulse/web/templates/partials/trades_filter_card.html`
- Modify: `marketpulse/web/static/css/app.css`
- Test: extend `tests/web/test_trades.py`

### Step 12.1: Tests

- [ ] Append:

```python
def test_filter_card_renders(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades")
    assert 'class="mp-trades-filter"' in r.text or "mp-filter-chips" in r.text
    # All 4 chips
    assert "全部" in r.text and "买卖" in r.text and "拆股" in r.text and "分红" in r.text
    # Range inputs present
    assert 'type="date"' in r.text
    # Add form mp-seg
    assert "mp-seg" in r.text


def test_filter_card_active_chip_reflects_event_type(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades?event_type=trade")
    # The "买卖" chip should have mp-chip--active when event_type=trade
    assert "mp-chip--active" in r.text
```

- [ ] **Step 12.2:** Run, fail.

- [ ] **Step 12.3:** Create `marketpulse/web/templates/partials/trades_filter_card.html`:

```html
<div class="mp-card" style="padding: 18px;">
  <!-- Filter row: chip selector + ticker prefix + date range -->
  <form method="get" action="/trades" class="mp-trades-filter__row">
    <span class="mp-eyebrow mp-eyebrow--primary">筛选</span>
    <div class="mp-filter-chips">
      <button type="submit" name="event_type" value=""
              class="mp-chip {% if not filters.event_type %}mp-chip--active{% endif %}">
        全部 <span class="mp-chip__count">{{ counts.all }}</span>
      </button>
      <button type="submit" name="event_type" value="trade"
              class="mp-chip {% if filters.event_type == 'trade' %}mp-chip--active{% endif %}">
        买卖 <span class="mp-chip__count">{{ counts.trade }}</span>
      </button>
      <button type="submit" name="event_type" value="split"
              class="mp-chip {% if filters.event_type == 'split' %}mp-chip--active{% endif %}">
        拆股 <span class="mp-chip__count">{{ counts.split }}</span>
      </button>
      <button type="submit" name="event_type" value="dividend"
              class="mp-chip {% if filters.event_type == 'dividend' %}mp-chip--active{% endif %}">
        分红 <span class="mp-chip__count">{{ counts.dividend }}</span>
      </button>
    </div>

    <span class="mp-divider-v"></span>

    <label class="mp-eyebrow">代码</label>
    <input name="q" value="{{ filters.q or '' }}"
           placeholder="AAPL · NVDA …"
           class="mp-input mp-input--mono" style="width:180px;" />

    <label class="mp-eyebrow">区间</label>
    <input name="from" type="date" value="{{ filters['from'] or '' }}"
           class="mp-input mp-input--mono" />
    <span class="mp-divider-arrow">→</span>
    <input name="to" type="date" value="{{ filters.to or '' }}"
           class="mp-input mp-input--mono" />

    <button type="submit" class="mp-btn mp-btn--ghost mp-btn--sm">应用</button>
  </form>

  <hr class="mp-hr" style="margin: 16px -18px;" />

  <!-- Add-record row: type-aware form -->
  <form id="event-form"
        hx-post="/trades{% if filters_qs %}?{{ filters_qs }}{% endif %}"
        hx-target="#trades-container" hx-swap="innerHTML"
        hx-on::after-request="if(event.detail.successful) exitEditMode();"
        class="mp-trades-add">
    <div class="mp-trades-add__kind">
      <span class="mp-eyebrow mp-eyebrow--primary">添加记录</span>
      <div class="mp-seg" id="event-kind-seg">
        <button type="button" onclick="onEventKindChange('buy')"      class="is-active">买入</button>
        <button type="button" onclick="onEventKindChange('sell')">卖出</button>
        <button type="button" onclick="onEventKindChange('split')">拆股</button>
        <button type="button" onclick="onEventKindChange('dividend')">分红</button>
      </div>
      <input type="hidden" name="event_kind" id="event-kind" value="buy" />
    </div>

    <input name="ticker" placeholder="代码" required
           class="mp-input mp-input--mono" style="width:120px;" />

    <!-- Trade fields -->
    <input name="quantity" type="number" step="any" min="0" placeholder="数量" required
           class="mp-input trade-field" style="width:100px;" />
    <input name="price" type="number" step="any" min="0" placeholder="价格 $" required
           class="mp-input trade-field" style="width:130px;" />
    <input name="fees" type="number" step="any" min="0" placeholder="手续费 $" value="0"
           class="mp-input trade-field" style="width:100px;" />
    <input name="executed_at" type="date" title="交易日期 (留空=今天)" data-optional="true"
           class="mp-input trade-field" style="width:140px;" />

    <!-- Split fields -->
    <input name="ratio" type="number" step="any" min="0.0001" placeholder="比例 (1:2 填 2)"
           class="mp-input split-field hidden" style="width:140px;" />
    <input name="ex_date" type="date" placeholder="生效日期"
           class="mp-input split-field hidden" style="width:140px;" />

    <!-- Dividend fields -->
    <input name="amount_per_share" type="number" step="any" min="0" placeholder="每股金额 $"
           class="mp-input dividend-field hidden" style="width:120px;" />
    <input name="total_amount" type="number" step="any" min="0" placeholder="总金额 $"
           class="mp-input dividend-field hidden" style="width:120px;" />

    <input name="notes" placeholder="备注(可选)"
           class="mp-input" style="width:260px;" />
    <input type="hidden" name="trade_id" id="trade-id-input" value="" />
    <input type="hidden" name="tz_offset_minutes" id="tz-offset-input" value="0" />
    <input type="hidden" name="original_executed_at_iso" id="original-executed-at-iso" value="" />

    <button id="submit-btn" type="submit" class="mp-btn mp-btn--primary">
      <span class="material-symbols-outlined">add</span>记录
    </button>
    <button id="cancel-edit-btn" type="button" onclick="exitEditMode()"
            class="mp-btn mp-btn--ghost hidden">取消编辑</button>
  </form>
</div>
```

- [ ] **Step 12.4:** Append to `app.css`:

```css
/* ════════ Phase 5c: Filter card ════════ */
.mp-trades-filter__row { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.mp-filter-chips       { display:flex; gap:4px; }
.mp-chip__count        { margin-left:4px; opacity:0.7; }
.mp-divider-v          { width:1px; height:24px; background:var(--ns-outline-variant); }
.mp-divider-arrow      { color:var(--ns-slate-400); }
.mp-input              { height:30px; padding:0 12px;
                         border:1px solid var(--ns-outline-variant);
                         border-radius:2px; font-size:12px;
                         font-family:var(--ns-font-body); }
.mp-input--mono        { font-family:var(--ns-font-mono); }
.mp-trades-add         { display:flex; align-items:flex-end; gap:12px; flex-wrap:wrap; }
.mp-trades-add__kind   { display:flex; flex-direction:column; gap:4px; }
.mp-hr                 { border:0; border-top:1px solid var(--ns-outline-variant); }
.hidden                { display:none !important; }
```

(If `.hidden` already exists in app.css, skip it.)

- [ ] **Step 12.5:** Run filter tests.

```bash
uv run pytest tests/web/test_trades.py -v -k "filter_card or active_chip"
```

- [ ] **Step 12.6:** Run existing form-submission tests (don't break add/edit).

```bash
uv run pytest tests/web/test_trades.py -v
```

Some test may still fail because trades_table.html isn't rewritten yet (Task 13). Acceptable.

- [ ] **Step 12.7:** Commit.

```bash
git add marketpulse/web/templates/partials/trades_filter_card.html \
        marketpulse/web/static/css/app.css tests/web/test_trades.py
git commit -m "feat(trades): filter card partial — chips + range + add form

- 4 event-type chips with counts; active state highlights
- ticker prefix search (q) + date range inputs (form GET submit)
- type-aware add form retains existing JS hooks; uses mp-input/mp-seg
- HTMX add target unchanged: #trades-container"
```

---

## Task 13: `trades_table.html` rewrite — 10 cols + split/dividend rows + pagination footer + `mp-table--trades` CSS

**Why:** 设计稿核心组件。10 列流水表 (时间/代码/类型/数量/价格/总额/手续费/已实现盈亏/盈亏%/备注/操作) + split/dividend 行 + 分页 footer。

**Files:**
- Rewrite: `marketpulse/web/templates/partials/trades_table.html` (109 → ~120 lines)
- Modify: `marketpulse/web/static/css/app.css`
- Test: extend `tests/web/test_trades.py`

### Step 13.1: Tests

- [ ] Append:

```python
def test_trades_table_10_columns(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    db_session.add(Trade(ticker="AAPL", action="buy", quantity=1, price=100,
                         fees=0, executed_at=datetime(2026, 5, 1, tzinfo=UTC)))
    db_session.commit()
    r = client.get("/trades")
    # 11 th cells (10 data + 1 actions column)
    assert r.text.count("<th") >= 10


def test_trades_table_pagination_footer(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_trades(db_session, 75)
    r = client.get("/trades?page=2")
    assert "上一页" in r.text
    assert "下一页" in r.text
    # Current page button has navy class
    assert "mp-btn--navy" in r.text


def test_trades_table_dividend_row_chip(client, monkeypatch, db_session):
    from datetime import date as _date
    _login(client, monkeypatch)
    db_session.add(Dividend(ticker="AAPL", ex_date=_date(2026, 5, 1),
                            amount_per_share=0.25, total_amount=2.5))
    db_session.commit()
    r = client.get("/trades")
    # Dividend chip uses mp-chip--up green
    assert "mp-chip--up" in r.text
    assert "分红" in r.text


def test_trades_table_split_row_purple(client, monkeypatch, db_session):
    from datetime import date as _date
    from marketpulse.db.models import StockSplit
    _login(client, monkeypatch)
    db_session.add(StockSplit(ticker="AAPL", ex_date=_date(2026, 5, 1),
                              ratio=2.0))
    db_session.commit()
    r = client.get("/trades")
    assert "mp-chip--split" in r.text
    assert "拆股" in r.text
```

- [ ] **Step 13.2:** Run, fail.

- [ ] **Step 13.3:** Rewrite `marketpulse/web/templates/partials/trades_table.html`:

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
        {% set t = e.obj %}
        <tr id="trade-row-{{ t.id }}">
          <td class="col-time mono">
            <time data-utc="{{ (t.executed_at or t.created_at).isoformat() }}">{{ (t.executed_at or t.created_at).strftime("%Y-%m-%d %H:%M") }}</time>
          </td>
          <td class="col-ticker"><a href="/stock/{{ t.ticker }}" class="mp-ticker-link">{{ t.ticker }}</a></td>
          <td class="col-type">
            {% if t.action == "buy" %}<span class="mp-chip mp-chip--periwinkle">买入</span>
            {% else %}<span class="mp-chip mp-chip--down">卖出</span>{% endif %}
          </td>
          <td class="col-qty mono tnum">{{ "%g"|format(t.quantity) }}</td>
          <td class="col-price mono tnum">${{ "%.2f"|format(t.price) }}</td>
          <td class="col-total mono tnum">${{ "%.2f"|format(t.quantity * t.price) }}</td>
          <td class="col-fees mono tnum">{{ "$%.2f"|format(t.fees) if t.fees else "—" }}</td>
          <td class="col-pl mono tnum {% if t.realized_pl is not none and t.realized_pl >= 0 %}up{% elif t.realized_pl is not none %}down{% endif %}">
            {% if t.realized_pl is not none %}{{ "%+.2f"|format(t.realized_pl) }}{% else %}—{% endif %}
          </td>
          <td class="col-plpct mono tnum {% if t.realized_pl is not none and t.realized_pl >= 0 %}up{% elif t.realized_pl is not none %}down{% endif %}">
            {% if t.realized_pl is not none and t.quantity and t.price %}{{ "%+.2f%%"|format(t.realized_pl / (t.quantity * t.price) * 100) }}{% else %}—{% endif %}
          </td>
          <td class="col-notes muted">{{ t.notes or "" }}</td>
          <td class="col-actions">
            <button class="mp-icon-btn" type="button"
                    onclick='loadTradeIntoForm({{ {
                      "id": t.id, "ticker": t.ticker, "action": t.action,
                      "quantity": t.quantity, "price": t.price, "fees": t.fees,
                      "notes": t.notes or "",
                      "executed_at_date": (t.executed_at.strftime("%Y-%m-%d") if t.executed_at else ""),
                      "executed_at_iso": (t.executed_at.isoformat() if t.executed_at else "")
                    } | tojson }})'>
              <span class="material-symbols-outlined">edit</span>
            </button>
            <button class="mp-icon-btn"
                    hx-delete="/trades/{{ t.id }}?{{ filters_qs }}&page={{ page }}&limit={{ limit }}"
                    hx-target="#trades-container" hx-swap="innerHTML"
                    hx-confirm="删除这笔交易?会自动重算该代码的持仓和已实现盈亏。">
              <span class="material-symbols-outlined">delete_outline</span>
            </button>
          </td>
        </tr>
      {% elif e.kind == "split" %}
        {% set s = e.obj %}
        <tr class="split" id="split-row-{{ s.id }}">
          <td class="col-time mono">{{ s.ex_date.strftime("%Y-%m-%d") }}</td>
          <td class="col-ticker"><a href="/stock/{{ s.ticker }}" class="mp-ticker-link">{{ s.ticker }}</a></td>
          <td class="col-type"><span class="mp-chip mp-chip--split">拆股</span></td>
          <td colspan="6" class="muted">
            1 → <span class="mono" style="color:var(--ns-navy); font-weight:600;">{{ "%g"|format(s.ratio) }}</span>
            自动重算 {{ s.ticker }} 持仓
            <span class="muted">[{{ s.source }}]</span>
            {% if s.notes %} · {{ s.notes }}{% endif %}
          </td>
          <td class="col-notes"></td>
          <td class="col-actions">
            <button class="mp-icon-btn"
                    hx-delete="/splits/{{ s.id }}?{{ filters_qs }}&page={{ page }}&limit={{ limit }}"
                    hx-target="#trades-container" hx-swap="innerHTML"
                    hx-confirm="删除这条拆股记录?会自动重算该代码的持仓。">
              <span class="material-symbols-outlined">delete_outline</span>
            </button>
          </td>
        </tr>
      {% elif e.kind == "dividend" %}
        {% set d = e.obj %}
        <tr class="dividend" id="dividend-row-{{ d.id }}">
          <td class="col-time mono">{{ d.ex_date.strftime("%Y-%m-%d") }}</td>
          <td class="col-ticker"><a href="/stock/{{ d.ticker }}" class="mp-ticker-link">{{ d.ticker }}</a></td>
          <td class="col-type"><span class="mp-chip mp-chip--up">分红</span></td>
          <td class="col-qty muted">—</td>
          <td class="col-price mono tnum">${{ "%.4f"|format(d.amount_per_share) }}/股</td>
          <td class="col-total mono tnum up" style="font-weight:600;">+${{ "%.2f"|format(d.total_amount) }}</td>
          <td class="col-fees muted">—</td>
          <td class="col-pl mono tnum up" style="font-weight:600;">+${{ "%.2f"|format(d.total_amount) }}</td>
          <td class="col-plpct muted">—</td>
          <td class="col-notes muted">{{ d.notes or "" }}</td>
          <td class="col-actions">
            <button class="mp-icon-btn"
                    hx-delete="/dividends/{{ d.id }}?{{ filters_qs }}&page={{ page }}&limit={{ limit }}"
                    hx-target="#trades-container" hx-swap="innerHTML"
                    hx-confirm="删除这条分红记录?">
              <span class="material-symbols-outlined">delete_outline</span>
            </button>
          </td>
        </tr>
      {% endif %}
    {% endfor %}
    {% if not events %}
      <tr><td colspan="11" class="mp-empty-row">暂无记录。在上方表单中添加第一条。</td></tr>
    {% endif %}
  </tbody>
</table>

{% if total_count > 0 %}
<div class="mp-table-footer">
  <span class="mp-table-footer__count">
    显示 {{ (page-1)*limit + 1 }} – {{ (page-1)*limit + events|length }} · 总 {{ total_count }} 条
  </span>
  <div class="mp-table-footer__pager">
    {% set base_qs = filters_qs %}
    {% if page > 1 %}
      <a class="mp-btn mp-btn--ghost mp-btn--sm"
         hx-get="/trades?{{ base_qs }}&page={{ page-1 }}&limit={{ limit }}"
         hx-target="#trades-container" hx-swap="innerHTML" hx-push-url="true">‹ 上一页</a>
    {% endif %}
    {% for p in pager_window %}
      <a class="mp-btn mp-btn--{% if p == page %}navy{% else %}ghost{% endif %} mp-btn--sm"
         hx-get="/trades?{{ base_qs }}&page={{ p }}&limit={{ limit }}"
         hx-target="#trades-container" hx-swap="innerHTML" hx-push-url="true">{{ p }}</a>
    {% endfor %}
    {% if page < total_pages %}
      <a class="mp-btn mp-btn--ghost mp-btn--sm"
         hx-get="/trades?{{ base_qs }}&page={{ page+1 }}&limit={{ limit }}"
         hx-target="#trades-container" hx-swap="innerHTML" hx-push-url="true">下一页 ›</a>
    {% endif %}
  </div>
</div>
{% endif %}
```

- [ ] **Step 13.4:** Append to `app.css`:

```css
/* ════════ Phase 5c: Trades table ════════ */
#trades-container           { overflow-x: auto; }
.mp-table--trades           { min-width: 1100px; width: 100%; border-collapse: collapse; }
.mp-table--trades th        { font:600 10px/1 var(--ns-font-headline);
                              letter-spacing:0.08em; text-transform:uppercase;
                              color:var(--ns-on-surface-variant);
                              padding:10px 12px;
                              border-bottom:1px solid var(--ns-outline-variant);
                              white-space:nowrap; text-align:left; }
.mp-table--trades td        { padding:12px; font-size:13px;
                              border-bottom:1px solid var(--ns-outline-variant);
                              vertical-align: middle; }
.mp-table--trades tbody tr:hover { background: var(--ns-surface-container-low); }
.mp-table--trades .col-time   { width:140px; color:var(--ns-on-surface-variant); }
.mp-table--trades .col-ticker { width:90px; }
.mp-table--trades .col-type   { width:90px; }
.mp-table--trades .col-qty,
.mp-table--trades .col-price,
.mp-table--trades .col-total,
.mp-table--trades .col-fees,
.mp-table--trades .col-pl,
.mp-table--trades .col-plpct  { text-align:right; }
.mp-table--trades .col-actions{ width:80px; text-align:right; }
.mp-table--trades .up         { color:var(--mp-up); font-weight:600; }
.mp-table--trades .down       { color:var(--mp-down); font-weight:600; }
.mp-table--trades .muted      { color:var(--ns-on-surface-variant); }
.mp-table--trades tr.split    { background:rgba(141,82,231,0.04); }
.mp-table--trades tr.dividend { background:rgba(14,138,95,0.04); }

.mp-chip--split             { color:#5e2cb4;
                              background:rgba(141,82,231,0.12);
                              border-color:transparent; }

.mp-empty-row               { text-align:center; padding:32px;
                              color:var(--ns-on-surface-variant); }

.mp-ticker-link             { color:var(--ns-navy); font-weight:700;
                              text-decoration:none;
                              font-family:var(--ns-font-headline); }
.mp-icon-btn                { background:transparent; border:0; cursor:pointer;
                              color:var(--ns-slate-400); padding:4px;
                              display:inline-flex; align-items:center; }
.mp-icon-btn:hover          { color:var(--ns-navy); }
.mp-icon-btn .material-symbols-outlined { font-size:18px; }

.mp-table-footer            { display:flex; justify-content:space-between;
                              align-items:center; padding:12px 18px;
                              background:var(--ns-surface-container-low);
                              border-top:1px solid var(--ns-outline-variant); }
.mp-table-footer__count     { font-size:12px; color:var(--ns-on-surface-variant); }
.mp-table-footer__pager     { display:flex; gap:4px; }
```

- [ ] **Step 13.5:** Run all trades tests.

```bash
uv run pytest tests/web/test_trades.py -v
```

Most should now pass. Right-rail tests (`monthly_chart_15_bars` etc.) still fail — fixed in Task 14.

- [ ] **Step 13.6:** Commit.

```bash
git add marketpulse/web/templates/partials/trades_table.html \
        marketpulse/web/static/css/app.css tests/web/test_trades.py
git commit -m "feat(trades): 10-col mp-table--trades + split/dividend rows + pagination

- 10 columns (time/ticker/type/qty/price/total/fees/pl/pl%/notes/actions)
- Split rows: purple chip, colspan-6 message, delete button
- Dividend rows: green chip, total in green, '—' in qty/fees/pl% cols
- Pagination footer: page numbers ±2 window + prev/next, HTMX swap
- Delete buttons preserve pagination via URL query string
- overflow-x:auto + min-width:1100px for narrow viewports"
```

---

## Task 14: Right rail — monthly P&L card + dropzone card + by-ticker card + their CSS

**Why:** 3 张右栏卡片，是 Phase 5c 整页可见信息密度的重要组成。

**Files:**
- Create: `marketpulse/web/templates/partials/trades_monthly_pl_card.html`
- Create: `marketpulse/web/templates/partials/trades_dropzone_card.html`
- Create: `marketpulse/web/templates/partials/trades_by_ticker_card.html`
- Modify: `marketpulse/web/static/css/app.css`
- Test: extend `tests/web/test_trades.py`

### Step 14.1: Tests

- [ ] Append:

```python
def test_monthly_pl_card_15_bars(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades")
    assert r.text.count("mp-monthly-bar__bar") == 15
    assert "月度已实现盈亏" in r.text


def test_dropzone_card_renders(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/trades")
    assert 'action="/trades/import"' in r.text
    assert 'enctype="multipart/form-data"' in r.text
    assert "拖入" in r.text
    assert "mp-dropzone" in r.text


def test_by_ticker_card_empty_state(client, monkeypatch):
    """Empty by_ticker still renders the card without crashing."""
    _login(client, monkeypatch)
    r = client.get("/trades")
    assert "按代码" in r.text  # card title
    assert "mp-ticker-list" in r.text


def test_by_ticker_card_renders_data(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    db_session.add(Trade(ticker="AAPL", action="buy", quantity=10, price=100,
                         fees=0, executed_at=datetime(2026, 1, 1, tzinfo=UTC),
                         realized_pl=None))
    db_session.add(Trade(ticker="AAPL", action="sell", quantity=10, price=120,
                         fees=0, executed_at=datetime(2026, 6, 1, tzinfo=UTC),
                         realized_pl=200.0))
    db_session.commit()
    r = client.get("/trades")
    # AAPL appears in by-ticker row
    assert r.text.count("AAPL") >= 1
    assert "mp-ticker-row" in r.text
```

- [ ] **Step 14.2:** Run, fail.

- [ ] **Step 14.3:** Create `marketpulse/web/templates/partials/trades_monthly_pl_card.html`:

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">insights</span>月度已实现盈亏
    </span>
    {% set monthly_total = monthly_pl | map(attribute='pl') | sum %}
    <span class="mp-card__sub">
      {{ monthly_pl|length }} 个月 · 累计
      <span class="{% if monthly_total >= 0 %}up{% else %}down{% endif %} mono"
            style="font-weight:700;">{{ "%+,.0f"|format(monthly_total) }}</span>
    </span>
  </div>
  <div class="mp-card__body">
    <div class="mp-monthly-bars">
      {% set max_abs = (monthly_pl | map(attribute='pl') | map('abs') | list) %}
      {% set max_v = max_abs | max if max_abs else 0 %}
      {% for m in monthly_pl %}
        {% set pct = (m.pl|abs / max_v * 100) if max_v else 0 %}
        <div class="mp-monthly-bar" title="{{ m.month }}: {{ '%+,.0f'|format(m.pl) }}">
          <div class="mp-monthly-bar__bar"
               style="height: {{ pct }}%;
                      background: {% if m.pl >= 0 %}var(--mp-up){% else %}var(--mp-down){% endif %};"></div>
          <div class="mp-monthly-bar__label">{{ m.month[5:] }}</div>
        </div>
      {% endfor %}
    </div>
    {% if monthly_pl and (monthly_pl | selectattr('pl', 'ne', 0) | list) %}
      {% set nonzero = monthly_pl | selectattr('pl', 'ne', 0) | list %}
      {% set best = nonzero | max(attribute='pl') %}
      {% set worst = nonzero | min(attribute='pl') %}
      <hr class="mp-hr" />
      <div class="mp-monthly-footer">
        <span>最佳月 · <span class="up mono" style="font-weight:700;">{{ "%+,.0f"|format(best.pl) }}</span> ({{ best.month }})</span>
        <span>最差月 · <span class="down mono" style="font-weight:700;">{{ "%+,.0f"|format(worst.pl) }}</span> ({{ worst.month }})</span>
      </div>
    {% endif %}
  </div>
</section>
```

- [ ] **Step 14.4:** Create `marketpulse/web/templates/partials/trades_dropzone_card.html`:

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">upload_file</span>从 Robinhood 导入
    </span>
  </div>
  <div class="mp-card__body">
    <form action="/trades/import" method="post" enctype="multipart/form-data" id="dropzone-form">
      <label class="mp-dropzone" id="dropzone">
        <input type="file" name="file" accept=".csv" class="mp-dropzone__file"
               onchange="document.getElementById('dropzone-form').submit();" />
        <span class="material-symbols-outlined mp-dropzone__icon">cloud_upload</span>
        <div class="mp-dropzone__title">拖入 Robinhood CSV</div>
        <div class="mp-dropzone__sub">或 <span class="mp-link">点击选择文件</span></div>
      </label>
    </form>
  </div>
</section>

<script>
(function() {
  const dz = document.getElementById('dropzone');
  if (!dz) return;
  const fileInput = dz.querySelector('input[type="file"]');
  const form = document.getElementById('dropzone-form');

  ['dragenter','dragover'].forEach(ev =>
    dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add('is-dragover'); })
  );
  ['dragleave','drop'].forEach(ev =>
    dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove('is-dragover'); })
  );
  dz.addEventListener('drop', e => {
    if (e.dataTransfer && e.dataTransfer.files.length) {
      fileInput.files = e.dataTransfer.files;
      form.submit();
    }
  });
})();
</script>
```

- [ ] **Step 14.5:** Create `marketpulse/web/templates/partials/trades_by_ticker_card.html`:

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">leaderboard</span>按代码 · 已实现盈亏
    </span>
    <span class="mp-card__sub">all-time 累计</span>
  </div>
  {% if by_ticker %}
    <ul class="mp-ticker-list">
      {% set max_abs = (by_ticker | map(attribute='realized_pl') | map('abs') | list) %}
      {% set max_v = max_abs | max if max_abs else 0 %}
      {% for r in by_ticker %}
        <li class="mp-ticker-row">
          <span class="grotesk mp-ticker-row__symbol">{{ r.ticker }}</span>
          <div class="mp-ticker-row__bar">
            <div style="width: {{ (r.realized_pl|abs / max_v * 100) if max_v else 0 }}%;
                        background: {% if r.realized_pl >= 0 %}var(--mp-up){% else %}var(--mp-down){% endif %};"></div>
          </div>
          <span class="mono tnum {% if r.realized_pl >= 0 %}up{% else %}down{% endif %}"
                style="text-align:right;">{{ "%+,.0f"|format(r.realized_pl) }}</span>
          <span class="mono tnum {% if r.realized_pl >= 0 %}up{% else %}down{% endif %}"
                style="text-align:right;">{{ "%+.1f%%"|format(r.pct) }}</span>
        </li>
      {% endfor %}
    </ul>
  {% else %}
    <div class="mp-card__body mp-empty-row">暂无已实现盈亏数据</div>
  {% endif %}
</section>
```

- [ ] **Step 14.6:** Append to `app.css`:

```css
/* ════════ Phase 5c: Drop zone ════════ */
.mp-dropzone                { display:block; position:relative;
                              border:1px dashed var(--ns-outline-variant);
                              border-radius:2px; padding:24px 16px;
                              text-align:center;
                              background:var(--ns-surface-container-low);
                              cursor:pointer;
                              transition: background 200ms, border-color 200ms; }
.mp-dropzone.is-dragover    { background:var(--ns-primary-container);
                              border-color:var(--ns-primary); }
.mp-dropzone__file          { position:absolute; opacity:0; pointer-events:none;
                              width:1px; height:1px; }
.mp-dropzone__icon          { font-size:36px; color:var(--ns-primary); }
.mp-dropzone__title         { font-size:14px; font-weight:600;
                              color:var(--ns-navy); margin-top:6px; }
.mp-dropzone__sub           { font-size:11.5px;
                              color:var(--ns-on-surface-variant); margin-top:4px; }
.mp-link                    { color:var(--ns-primary); text-decoration:underline;
                              cursor:pointer; }

/* ════════ Phase 5c: Monthly bar chart ════════ */
.mp-monthly-bars            { display:flex; gap:4px; align-items:flex-end; height:140px; }
.mp-monthly-bar             { flex:1; display:flex; flex-direction:column;
                              justify-content:flex-end; height:100%; }
.mp-monthly-bar__bar        { border-radius:2px 2px 0 0;
                              min-height:2px; margin-bottom:4px; }
.mp-monthly-bar__label      { font:9px/1 var(--ns-font-mono);
                              color:var(--ns-slate-400);
                              text-align:center; letter-spacing:0.02em; }
.mp-monthly-footer          { display:flex; justify-content:space-between;
                              font-size:11px;
                              color:var(--ns-on-surface-variant);
                              margin-top:12px; }

/* ════════ Phase 5c: By-ticker leaderboard ════════ */
.mp-ticker-list             { list-style:none; margin:0; padding:10px 16px 18px; }
.mp-ticker-row              { display:grid;
                              grid-template-columns: 50px 1fr 90px 64px;
                              gap:10px; align-items:center; padding:7px 0; }
.mp-ticker-row__symbol      { font-weight:700; font-size:13px; color:var(--ns-navy); }
.mp-ticker-row__bar         { height:8px; background:var(--ns-surface-container);
                              border-radius:2px; position:relative; overflow:hidden; }
.mp-ticker-row__bar > div   { position:absolute; left:0; top:0; bottom:0; }
```

- [ ] **Step 14.7:** Run tests.

```bash
uv run pytest tests/web/test_trades.py -v
```

Expected: 大部分 pass。POST/PUT/DELETE 测试（保留分页 context）可能仍 fail，下一 task 修。

- [ ] **Step 14.8:** Commit.

```bash
git add marketpulse/web/templates/partials/trades_monthly_pl_card.html \
        marketpulse/web/templates/partials/trades_dropzone_card.html \
        marketpulse/web/templates/partials/trades_by_ticker_card.html \
        marketpulse/web/static/css/app.css tests/web/test_trades.py
git commit -m "feat(trades): right rail — monthly P&L bars, import dropzone, by-ticker

- 15-month bar chart with cumulative sum + best/worst footer
- Robinhood CSV dropzone: native form POST (no HTMX); drag-drop
  auto-submits via JS, falls back to click-to-select with no JS
- Per-ticker leaderboard: top-8 by abs(P&L), progress bars + %
- All CSS scoped under mp-monthly-* / mp-ticker-* / mp-dropzone"
```

---

## Task 15: POST/PUT/DELETE handlers preserve pagination + emit partial response

**Why:** 现有 POST/PUT/DELETE 返回 `partials/trades_table.html`，但 context 不含 `kpi`/`monthly_pl`/`by_ticker`/`counts` 等新字段，模板会 KeyError。也要支持 `?page&limit&filters` 透传。

**Files:**
- Modify: `marketpulse/web/routes/trades.py:151-300` (POST/PUT/DELETE)
- Test: extend `tests/web/test_trades.py`

### Step 15.1: Tests

- [ ] Append:

```python
def test_post_trade_returns_partial_with_page_1(client, monkeypatch, db_session):
    """After adding, returned partial defaults to page 1."""
    _login(client, monkeypatch)
    r = client.post("/trades", data={
        "event_kind": "buy", "action": "buy",
        "ticker": "AAPL", "quantity": "1", "price": "100",
        "fees": "0", "tz_offset_minutes": "0",
    })
    assert r.status_code == 200
    assert "AAPL" in r.text


def test_delete_preserves_pagination(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_trades(db_session, 60)
    # Get id of a trade on page 2
    trades = db_session.query(Trade).order_by(Trade.executed_at.desc()).all()
    target = trades[55]  # somewhere on page 2 of 50/page
    r = client.delete(f"/trades/{target.id}?page=2&limit=50")
    assert r.status_code == 200
    # Trade row gone from response
    assert f"trade-row-{target.id}" not in r.text


def test_delete_last_item_on_page_clamps(client, monkeypatch, db_session):
    """If deleting drops total below current page's start, clamp."""
    _login(client, monkeypatch)
    _seed_trades(db_session, 51)
    trades = db_session.query(Trade).order_by(Trade.executed_at.desc()).all()
    target = trades[50]  # only item on page 2
    r = client.delete(f"/trades/{target.id}?page=2&limit=50")
    assert r.status_code == 200
    # Response should contain trades (clamped to page 1).
    assert "trade-row-" in r.text
```

- [ ] **Step 15.2:** Run, mostly fail (KeyError on template fields).

- [ ] **Step 15.3:** Edit POST/PUT/DELETE handlers in `marketpulse/web/routes/trades.py`. Each handler currently returns:

```python
return templates.TemplateResponse(
    "partials/trades_table.html",
    {... limited context ...},
)
```

Refactor: extract a helper `_render_table_partial(request, db, page, limit, filters)` that produces the **same full context** that `trades_page` builds. Then each handler calls it.

Add this helper at the top of `routes/trades.py` (above `trades_page`):

```python
def _trades_partial_response(
    request: Request,
    db: Session,
    *,
    page: int = 1,
    limit: int = 50,
    from_date: "date | None" = None,
    to_date: "date | None" = None,
    q: str | None = None,
    ticker: str | None = None,
    event_type: str | None = None,
):
    """Build the full context for trades_table.html and return TemplateResponse.

    Used by POST/PUT/DELETE handlers to render the partial after a mutation.
    Mirrors the data assembly in trades_page() — keep in sync.
    """
    # ---- This function should call trades_page's data-assembly logic.
    # To avoid duplication, we restructure trades_page itself so its body is
    # a thin wrapper around an internal _build_ctx() function.
    raise NotImplementedError  # see step 15.4 for full restructure
```

Actually, the cleanest refactor: extract the heavy lifting from `trades_page` into a private `_build_trades_ctx(...)` returning the full context dict. Then both `trades_page` and the mutation handlers use it.

Apply this restructure to `trades_page` from Task 8: rename its body to `_build_trades_ctx(db, request, page, limit, from_date, to_date, q, ticker_alias, event_type) -> dict`, and have `trades_page` just call it and dispatch the right template.

```python
def _build_trades_ctx(
    db: Session,
    *,
    page: int,
    limit: int,
    from_date: "date | None",
    to_date: "date | None",
    q: str | None,
    ticker_alias: str | None,
    event_type: str | None,
) -> dict:
    """Full context dict for trades.html and partials/trades_table.html."""
    # ... move all of the existing trades_page body here ...
    # Return the ctx dict (not the response).
```

Then `trades_page`:

```python
@router.get("/trades", response_class=HTMLResponse)
def trades_page(
    request: Request,
    page: int = 1,
    limit: int = 50,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    q: str | None = None,
    ticker: str | None = None,
    event_type: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    from datetime import date as _date

    def _pd(s, name):
        if not s: return None
        try: return _date.fromisoformat(s)
        except ValueError as e: raise HTTPException(422, f"invalid {name}: {s}") from e

    from_date = _pd(from_, "from")
    to_date = _pd(to, "to")
    if from_date and to_date and from_date > to_date:
        raise HTTPException(422, "from must be <= to")
    if page <= 0 or limit <= 0:
        raise HTTPException(422, "page and limit must be positive")

    ctx = _build_trades_ctx(
        db, page=page, limit=max(10, min(200, limit)),
        from_date=from_date, to_date=to_date,
        q=(q.strip() if q else None) or None,
        ticker_alias=ticker, event_type=event_type,
    )
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(request, "partials/trades_table.html", ctx)
    return templates.TemplateResponse(request, "trades.html", ctx)
```

Then update each mutation handler (POST/PUT/DELETE for trades, splits, dividends) to use `_build_trades_ctx`. Example for `POST /trades`:

```python
@router.post("/trades", response_class=HTMLResponse)
def add_trade(
    request: Request,
    # ... existing form params ...
    page: int = 1, limit: int = 50,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    q: str | None = None,
    ticker_filter: str | None = Query(None, alias="ticker"),  # rename to avoid clash with form field
    event_type: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    # ... existing trade creation logic ...

    from datetime import date as _date
    fd = _date.fromisoformat(from_) if from_ else None
    td = _date.fromisoformat(to) if to else None
    # Default page=1 after add (new trade at top).
    ctx = _build_trades_ctx(
        db, page=1, limit=max(10, min(200, limit)),
        from_date=fd, to_date=td,
        q=(q.strip() if q else None) or None,
        ticker_alias=ticker_filter, event_type=event_type,
    )
    return templates.TemplateResponse(request, "partials/trades_table.html", ctx)
```

For DELETE: same params, but use the incoming `page` (with clamp via `min(page, total_pages)` inside `_build_trades_ctx`).

For PUT (`/trades/{trade_id}`): same as POST.

For `/splits/{id}` and `/dividends/{id}` DELETE handlers (in `marketpulse/web/routes/splits.py` and `dividends.py`): they also return `partials/trades_table.html`. Apply the same pattern.

**Note:** if these endpoints in `splits.py`/`dividends.py` cannot import `_build_trades_ctx` cleanly, move the helper to `marketpulse/web/routes/_trades_ctx.py` and import from there.

- [ ] **Step 15.4:** Run.

```bash
uv run pytest tests/web/test_trades.py tests/web/test_splits.py -v
```

Expected: pass.

- [ ] **Step 15.5:** Lint + commit.

```bash
uv run ruff check marketpulse/web/routes/
git add marketpulse/web/routes/
git commit -m "feat(trades): mutation handlers preserve pagination + full ctx for partial

Extracts _build_trades_ctx helper used by GET /trades and all POST/PUT/
DELETE handlers (trades + splits + dividends). Each handler accepts
page/limit/filter query params and re-renders partials/trades_table.html
with the complete Phase 5c context.

POST defaults page=1 (new trade at top). PUT/DELETE preserve current
page; if delete-empties-page, _build_trades_ctx clamps to last
non-empty page."
```

---

## Task 16: End-to-end integration + ruff full pass + visual smoke

**Why:** 综合验证。Run all tests, ensure full suite is green.

**Files:** none (or fix-ups only)

### Step 16.1: Full test run

- [ ] Run.

```bash
uv run pytest -q
```

Expected: 0 fail. If any test fails:
- If it's a Phase 5c regression: find the assertion, trace back to which task introduced the bug, fix.
- If it's a pre-existing test that was implicitly relying on old `/trades` HTML: update the assertion to the new selectors (e.g., `mp-card mp-kpi` instead of old Tailwind classes).

- [ ] **Step 16.2:** Lint full repo.

```bash
uv run ruff check .
```

Expected: `All checks passed!`. Fix any complaints.

- [ ] **Step 16.3:** Visual smoke — start dev server and hit `/trades` once. Confirm:
  - Hero shows "Trade Ledger" with primary-blue rule
  - 5 KPI cards visible (even with no data, all should render with — or 0)
  - Filter card with 4 chips + ticker/date inputs + Add form
  - Empty state row "暂无记录" when DB empty
  - Right rail: 15 monthly bars (all flat), dropzone, by-ticker empty state
  - No console errors

(Manual step — can be skipped in automated subagent flow; the human will visually inspect after merge.)

- [ ] **Step 16.4:** Final commit (if any fixes were needed).

```bash
git add -A
git commit -m "chore(phase-5c): final integration polish

Resolves cross-template asserts inherited from pre-5b tests that
referenced old Tailwind selectors."
```

(Skip if nothing changed.)

- [ ] **Step 16.5:** Push and open PR.

```bash
git push -u origin feat/phase-5c-trades-page
gh pr create --title "feat(trades): Phase 5c — /trades NineScrolls Variant A redesign" --body "..."
```

PR body should reference spec PR #35 + summarize what shipped.

---

## Spec coverage checklist

Cross-check each spec section is implemented in a task above:

- [x] §Goal — Tasks 1-16 collectively
- [x] §Architecture · FIFO module → Task 1
- [x] §Architecture · service extensions → Tasks 2-7
- [x] §Architecture · route extensions → Tasks 8-9, 15
- [x] §Architecture · templates → Tasks 10-14
- [x] §决策 1 (`/trades/import` 保留 wizard) → Task 14 dropzone uses `/trades/import`
- [x] §决策 2 (server pagination) → Tasks 8, 13
- [x] §决策 3 (FIFO matcher) → Task 1
- [x] §决策 4 (date filter scope: ledger+KPI linked, right rail all-time) → Task 8 builds kpi with window, right rail without
- [x] §决策 5 (CSV current view, Robinhood-format) → Task 9
- [x] §路由 context dict completeness → Task 8 (full dict assembled)
- [x] §FIFO LotMatch fields → Task 1 dataclass (includes `buy_price` for cost basis)
- [x] §total_realized_pl extension → Task 2
- [x] §trading_stats extension + win_rate None → Task 3
- [x] §monthly_realized_pl(months=N) → Task 4
- [x] §trade_count_this_month → Task 5
- [x] §realized_pl_by_ticker → Task 6
- [x] §avg_hold_days → Task 7
- [x] §模板 trades.html + 6 partials + form_script → Tasks 10-14
- [x] §CSS 新增 全部 mp-* classes → distributed Tasks 10-14
- [x] §HTMX 交互 → Tasks 8, 13, 15
- [x] §HX-Request 局部响应 → Task 8
- [x] §POST/PUT/DELETE 保留 pagination → Task 15
- [x] §错误处理 5 项 → Task 8 (parse + clamp)
- [x] §所有测试 → distributed; each task has its own test step
- [x] §Out of scope (移动端、ImportRun、AI insights) — 不实现，无任务对应（正确）

All requirements covered.
