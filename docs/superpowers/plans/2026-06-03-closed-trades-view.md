# Closed Trades View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface closed paper-trade history (entry/exit/realized P&L + summary) as a new "Closed Trades" section at the bottom of `/lab/paper-trading`.

**Architecture:** Cache-only / zero-network presenter in the existing `marketpulse/trading/query_models.py` (3 frozen dataclasses + `_load_closed_trades_section`, wired into `PaperTradingDashboard` via the existing `SectionResult`/`_safe_section` pattern), rendered by a new block in `lab_paper_trading.html`. No new route, no DB migration.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x (`select`/`desc`), FastAPI + Jinja2, pytest. Tests: `uv run pytest`. Lint: `uv run ruff check`.

**Spec:** `docs/superpowers/specs/2026-06-03-closed-trades-view-design.md`

---

## File structure

- **Modify** `marketpulse/trading/query_models.py` — add `date` to the datetime import; 3 frozen dataclasses (`ClosedTradeRow`, `ClosedTradesSummary`, `ClosedTrades`); `_closed_trade_return_pct` helper; `_load_closed_trades_section`; `closed_trades` field on `PaperTradingDashboard`; wire into `load_paper_trading_dashboard` + `_shared_fetch_error_dashboard` (and any other `PaperTradingDashboard(` construction).
- **Modify** `marketpulse/web/templates/lab_paper_trading.html` — Closed Trades section at the bottom.
- **Modify** `tests/trading/test_query_models.py` — presenter tests (reuse existing `_paper_order` helper).
- **Modify** `tests/web/test_lab_paper_trading.py` — route render test.

Existing facts to rely on (verified):
- `SectionResult[T]` has `.status` (`"ok"`/`"error"`), `.data`, `.empty_message`, `.error_title`, `.degraded_reason`.
- `section_ok(data, empty_message)` and `section_error(error_title, degraded_reason)` exist.
- `_safe_section(error_title, loader)` wraps a loader and returns `section_error(error_title, type(exc).__name__)` on exception.
- `PaperPosition` columns: `ticker, strategy, quantity:int, entry_price:Decimal, entry_date:date, status, opened_at, closed_at:datetime|None (TZDateTime), exit_price:Decimal|None, realized_pnl:Decimal|None, order_id (FK, not null)`.
- Test helper `_paper_order(db_session, *, ticker=..., strategy=..., placed_at=...)` already exists in `tests/trading/test_query_models.py` and builds a valid `PaperOrder` (idempotency_key derived from ticker+placed_at).
- The presenter module top imports: `from datetime import UTC, datetime, timedelta` (NO `date`), `from decimal import Decimal`, `from sqlalchemy import Integer, cast, desc, func, select`, `from sqlalchemy.orm import Session`, and `PaperPosition` from `marketpulse.db.models`.

---

## Task 1: Presenter — dataclasses + `_load_closed_trades_section`

**Files:**
- Modify: `marketpulse/trading/query_models.py`
- Test: `tests/trading/test_query_models.py`

- [ ] **Step 1: Add `date` to the datetime import**

In `marketpulse/trading/query_models.py`, change:
```python
from datetime import UTC, datetime, timedelta
```
to:
```python
from datetime import UTC, date, datetime, timedelta
```

- [ ] **Step 2: Write the failing presenter tests**

Append to `tests/trading/test_query_models.py` (the `_paper_order` helper is already defined in this file). Add this local helper near the other helpers:

```python
def _closed(
    db_session,
    *,
    realized_pnl,
    closed_at,
    ticker="AAPL",
    strategy="general",
    entry_price=Decimal("100"),
    quantity=3,
    entry_date=date(2026, 5, 20),
):
    """Seed one CLOSED PaperPosition (with its parent order) with controllable
    entry_price / quantity / realized_pnl / closed_at."""
    from marketpulse.db.models import PaperPosition

    order = _paper_order(db_session, ticker=ticker, strategy=strategy, placed_at=closed_at)
    pos = PaperPosition(
        order_id=order.id,
        strategy=strategy,
        ticker=ticker,
        quantity=quantity,
        entry_price=entry_price,
        entry_date=entry_date,
        horizon_date=entry_date,
        status="CLOSED",
        opened_at=closed_at,
        closed_at=closed_at,
        exit_price=Decimal("110"),
        realized_pnl=realized_pnl,
    )
    db_session.add(pos)
    db_session.flush()
    return pos
```

Then the tests (note `Decimal`, `date`, `datetime`, `UTC` are already imported at the top of this test file; if not, add them — and ensure `import pytest` is present for `pytest.approx`):

```python
def test_closed_trades_orders_and_summary(db_session):
    from marketpulse.trading.query_models import _load_closed_trades_section

    # 2 winners + 1 loser, distinct closed_at. entry_price=100, qty=3 -> cost=300.
    _closed(db_session, ticker="AAA", realized_pnl=Decimal("30"),
            closed_at=datetime(2026, 6, 1, 21, 30, tzinfo=UTC), entry_date=date(2026, 5, 27))
    _closed(db_session, ticker="BBB", realized_pnl=Decimal("-15"),
            closed_at=datetime(2026, 6, 2, 21, 30, tzinfo=UTC), entry_date=date(2026, 5, 28))
    _closed(db_session, ticker="CCC", realized_pnl=Decimal("60"),
            closed_at=datetime(2026, 6, 3, 21, 30, tzinfo=UTC), entry_date=date(2026, 5, 29))
    db_session.commit()

    section = _load_closed_trades_section(db_session)
    assert section.status == "ok"
    ct = section.data
    # newest exit first
    assert [r.ticker for r in ct.rows] == ["CCC", "BBB", "AAA"]
    ccc = ct.rows[0]
    assert ccc.exit_date == date(2026, 6, 3)
    assert ccc.days_held == (date(2026, 6, 3) - date(2026, 5, 29)).days  # 5
    assert ccc.return_pct == pytest.approx(60.0 / 300.0)
    s = ct.summary
    assert s.total_count == 3
    assert s.realized_pnl_total == Decimal("75")  # 30 - 15 + 60
    assert s.win_rate == 2 / 3                     # 2 priced winners / 3 priced
    # pytest.approx — float addition is not associative, don't assert exact equality
    assert s.avg_return_pct == pytest.approx((0.1 - 0.05 + 0.2) / 3)
    assert ct.count_label == "Showing 3 closed trades"


def test_closed_trades_zero_cost_return_none(db_session):
    from marketpulse.trading.query_models import _load_closed_trades_section

    _closed(db_session, ticker="ZZZ", realized_pnl=Decimal("5"),
            closed_at=datetime(2026, 6, 1, 21, 30, tzinfo=UTC),
            entry_price=Decimal("0"))  # cost = 0 -> return_pct None
    db_session.commit()

    ct = _load_closed_trades_section(db_session).data
    assert ct.rows[0].return_pct is None
    # excluded from Avg Return (no other valid returns -> None)
    assert ct.summary.avg_return_pct is None
    # still priced (realized_pnl present) -> counts toward win_rate
    assert ct.summary.win_rate == 1.0


def test_closed_trades_winrate_excludes_unpriced(db_session):
    from marketpulse.trading.query_models import _load_closed_trades_section

    _closed(db_session, ticker="WIN1", realized_pnl=Decimal("10"),
            closed_at=datetime(2026, 6, 1, 21, 30, tzinfo=UTC))
    _closed(db_session, ticker="WIN2", realized_pnl=Decimal("20"),
            closed_at=datetime(2026, 6, 2, 21, 30, tzinfo=UTC))
    _closed(db_session, ticker="NONE", realized_pnl=None,
            closed_at=datetime(2026, 6, 3, 21, 30, tzinfo=UTC))
    db_session.commit()

    ct = _load_closed_trades_section(db_session).data
    assert ct.summary.total_count == 3            # all closed counted
    assert ct.summary.win_rate == 1.0             # 2 wins / 2 priced (None excluded)
    assert ct.summary.realized_pnl_total == Decimal("30")  # None treated as 0


def test_closed_trades_empty(db_session):
    from marketpulse.trading.query_models import _load_closed_trades_section

    section = _load_closed_trades_section(db_session)
    assert section.status == "ok"
    ct = section.data
    assert ct.rows == []
    assert ct.summary.total_count == 0
    assert ct.summary.win_rate is None
    assert ct.summary.avg_return_pct is None
    assert ct.count_label == "Showing 0 closed trades"
    assert section.empty_message == "No closed trades yet"


def test_closed_trades_cap_50(db_session):
    from marketpulse.trading.query_models import _load_closed_trades_section

    for i in range(55):
        _closed(db_session, ticker=f"T{i:02d}", realized_pnl=Decimal("1"),
                closed_at=datetime(2026, 6, 1, 0, i, tzinfo=UTC))
    db_session.commit()

    ct = _load_closed_trades_section(db_session).data
    assert len(ct.rows) == 50
    assert ct.summary.total_count == 55
    assert ct.count_label == "Showing latest 50 of 55 closed trades"
```

- [ ] **Step 3: Run the tests to confirm they fail**

Run: `uv run pytest tests/trading/test_query_models.py -k closed_trades -v`
Expected: FAIL — `cannot import name '_load_closed_trades_section'`.

- [ ] **Step 4: Implement the dataclasses + helper + loader**

In `marketpulse/trading/query_models.py`, add near the other frozen dataclasses (e.g. just before `PaperTradingDashboard`):

```python
@dataclass(frozen=True)
class ClosedTradeRow:
    exit_date: date | None
    ticker: str
    strategy: str
    quantity: int
    entry_price: Decimal
    exit_price: Decimal | None
    days_held: int | None
    realized_pnl: Decimal | None
    return_pct: float | None


@dataclass(frozen=True)
class ClosedTradesSummary:
    total_count: int
    realized_pnl_total: Decimal
    win_rate: float | None
    avg_return_pct: float | None


@dataclass(frozen=True)
class ClosedTrades:
    summary: ClosedTradesSummary
    rows: list[ClosedTradeRow]
    count_label: str


_CLOSED_TRADES_LIMIT = 50


def _closed_trade_return_pct(
    entry_price: Decimal | None,
    quantity: int,
    realized_pnl: Decimal | None,
) -> float | None:
    """Cost-basis return. None when realized_pnl/entry_price missing or cost <= 0."""
    if realized_pnl is None or entry_price is None:
        return None
    cost = entry_price * quantity
    if cost <= 0:
        return None
    return float(realized_pnl) / float(cost)


def _load_closed_trades_section(db: Session) -> SectionResult[ClosedTrades]:
    """All CLOSED paper positions, newest exit first. Summary computed over the
    full closed set; table rows capped at _CLOSED_TRADES_LIMIT. DB-only."""
    positions = list(
        db.execute(
            select(PaperPosition)
            .where(PaperPosition.status == "CLOSED")
            .order_by(desc(PaperPosition.closed_at), desc(PaperPosition.id)),
        ).scalars().all(),
    )

    rows: list[ClosedTradeRow] = []
    pnl_total = Decimal("0")
    priced = 0
    wins = 0
    returns: list[float] = []
    for p in positions:
        exit_date = p.closed_at.date() if p.closed_at is not None else None
        days_held = (
            (exit_date - p.entry_date).days
            if exit_date is not None and p.entry_date is not None
            else None
        )
        ret = _closed_trade_return_pct(p.entry_price, p.quantity, p.realized_pnl)
        if p.realized_pnl is not None:
            priced += 1
            pnl_total += p.realized_pnl
            if p.realized_pnl > 0:
                wins += 1
        if ret is not None:
            returns.append(ret)
        rows.append(
            ClosedTradeRow(
                exit_date=exit_date,
                ticker=p.ticker,
                strategy=p.strategy,
                quantity=p.quantity,
                entry_price=p.entry_price,
                exit_price=p.exit_price,
                days_held=days_held,
                realized_pnl=p.realized_pnl,
                return_pct=ret,
            ),
        )

    total = len(positions)
    summary = ClosedTradesSummary(
        total_count=total,
        realized_pnl_total=pnl_total,
        win_rate=(wins / priced) if priced > 0 else None,
        avg_return_pct=(sum(returns) / len(returns)) if returns else None,
    )
    if total > _CLOSED_TRADES_LIMIT:
        count_label = (
            f"Showing latest {_CLOSED_TRADES_LIMIT} of {total} closed trades"
        )
    else:
        count_label = f"Showing {total} closed trades"
    closed = ClosedTrades(
        summary=summary,
        rows=rows[:_CLOSED_TRADES_LIMIT],
        count_label=count_label,
    )
    return section_ok(closed, "No closed trades yet")
```

- [ ] **Step 5: Run the tests to confirm they pass**

Run: `uv run pytest tests/trading/test_query_models.py -k closed_trades -v`
Expected: 5 passed.

- [ ] **Step 6: Lint + commit**

Run: `uv run ruff check marketpulse/trading/query_models.py tests/trading/test_query_models.py`
Expected: All checks passed.
```bash
git add marketpulse/trading/query_models.py tests/trading/test_query_models.py
git commit -m "feat(paper): closed-trades presenter (_load_closed_trades_section + dataclasses)"
```

---

## Task 2: Wire `closed_trades` into the dashboard

**Files:**
- Modify: `marketpulse/trading/query_models.py`
- Test: `tests/trading/test_query_models.py`

- [ ] **Step 1: Write the failing wiring tests**

Append to `tests/trading/test_query_models.py`:

```python
def test_dashboard_exposes_closed_trades(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    _closed(db_session, ticker="AAA", realized_pnl=Decimal("30"),
            closed_at=datetime(2026, 6, 1, 21, 30, tzinfo=UTC))
    db_session.commit()

    dash = load_paper_trading_dashboard(db_session)
    assert dash.closed_trades.status == "ok"
    assert dash.closed_trades.data.summary.total_count == 1
    assert dash.closed_trades.data.rows[0].ticker == "AAA"


def test_dashboard_closed_trades_degrades(db_session, monkeypatch):
    import marketpulse.trading.query_models as qm

    def boom(_db):
        raise RuntimeError("db blew up")

    monkeypatch.setattr(qm, "_load_closed_trades_section", boom)
    dash = qm.load_paper_trading_dashboard(db_session)
    # whole page still constructs; the section is degraded
    assert dash.closed_trades.status == "error"
    assert dash.closed_trades.error_title == "Unable to load Closed Trades"
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/trading/test_query_models.py -k "closed_trades_degrades or exposes_closed_trades" -v`
Expected: FAIL — `PaperTradingDashboard` has no field `closed_trades` (AttributeError / TypeError).

- [ ] **Step 3: Add the field to the dataclass**

In `PaperTradingDashboard` (frozen dataclass), add after `audit_timeline`:
```python
    audit_timeline: SectionResult[AuditTimeline]
    closed_trades: SectionResult[ClosedTrades]
```

- [ ] **Step 4: Wire every `PaperTradingDashboard(` construction**

Run `grep -n "PaperTradingDashboard(" marketpulse/trading/query_models.py` and update **every** construction site to pass the new field. There are (at least) two:

(a) In `load_paper_trading_dashboard`, alongside the other `_safe_section` calls, add:
```python
    closed_trades = _safe_section(
        "Unable to load Closed Trades",
        lambda: _load_closed_trades_section(db),
    )
```
and pass `closed_trades=closed_trades` into the `PaperTradingDashboard(...)` return.

(b) In `_shared_fetch_error_dashboard`, add:
```python
    closed_trades = section_error("Unable to load Closed Trades", degraded_reason)
```
and pass `closed_trades=closed_trades` into its `PaperTradingDashboard(...)` return.

If grep finds additional construction sites (e.g. another fallback), add the field there too (use `section_error("Unable to load Closed Trades", <reason>)` for error/fallback paths).

- [ ] **Step 5: Run to confirm pass (+ no regression in the existing dashboard tests)**

Run: `uv run pytest tests/trading/test_query_models.py -v`
Expected: all pass (new wiring tests + existing dashboard tests).

- [ ] **Step 6: Lint + commit**

Run: `uv run ruff check marketpulse/trading/query_models.py tests/trading/test_query_models.py`
```bash
git add marketpulse/trading/query_models.py tests/trading/test_query_models.py
git commit -m "feat(paper): wire closed_trades section into PaperTradingDashboard"
```

---

## Task 3: Template — Closed Trades section + route test

**Files:**
- Modify: `marketpulse/web/templates/lab_paper_trading.html`
- Test: `tests/web/test_lab_paper_trading.py`

- [ ] **Step 1: Write the failing route tests**

Append to `tests/web/test_lab_paper_trading.py` (it already has a `_login(client, monkeypatch)` helper). Seed via a fresh Session against the test DB URL, the same pattern other web tests use:

```python
def test_paper_trading_renders_closed_trades(client, monkeypatch, db_url):
    from datetime import UTC, date, datetime
    from decimal import Decimal

    _login(client, monkeypatch)
    monkeypatch.setenv("DATABASE_URL", db_url)
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from marketpulse.db.models import PaperOrder, PaperPosition

    engine = create_engine(db_url)
    with Session(engine) as s:
        order = PaperOrder(
            idempotency_key="ct-key-1", allocation_run_id="run-1",
            strategy="general", ticker="ZQK", quantity=3,
            event_time=datetime(2026, 6, 2, 21, 30, tzinfo=UTC),
            allocation_date=date(2026, 5, 28), horizon_date=date(2026, 6, 2),
            placed_at=datetime(2026, 6, 2, 21, 30, tzinfo=UTC),
            event_price=Decimal("100"), status="EXIT_FILLED",
            strategy_version="v1", allocator_version="v1",
            execution_engine_version="v1", weight=1.0,
            contribution_multiplier=1.0, effective_corr_window=60,
            rewarded_for_negative_corr=False, would_change_rank=False,
            size_clamped_by_override=False,
        )
        s.add(order)
        s.flush()
        s.add(PaperPosition(
            order_id=order.id, strategy="general", ticker="ZQK", quantity=3,
            entry_price=Decimal("100"), entry_date=date(2026, 5, 28),
            horizon_date=date(2026, 6, 2), status="CLOSED",
            opened_at=datetime(2026, 6, 2, 21, 30, tzinfo=UTC),
            closed_at=datetime(2026, 6, 2, 21, 30, tzinfo=UTC),
            exit_price=Decimal("110"), realized_pnl=Decimal("30"),
        ))
        s.commit()

    r = client.get("/lab/paper-trading")
    assert r.status_code == 200
    body = r.text
    assert "Closed Trades" in body
    assert "ZQK" in body
    assert "Showing 1 closed trades" in body


def test_paper_trading_closed_trades_empty_state(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/paper-trading")
    assert r.status_code == 200
    assert "Closed Trades" in r.text
    assert "No closed trades yet" in r.text
```

(If `PaperOrder` requires fields not listed here, copy the full field set from the `_paper_order` helper in `tests/trading/test_query_models.py` — it constructs a valid order. Verify against `marketpulse/db/models.py` and add any missing non-null columns.)

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/web/test_lab_paper_trading.py -k closed_trades -v`
Expected: FAIL — "Closed Trades" / "No closed trades yet" not in the page.

- [ ] **Step 3: Add the template section**

In `marketpulse/web/templates/lab_paper_trading.html`, add **before the closing `</section>` of `mp-paper-ops`** (i.e. at the bottom, after the drill-down `mp-paper-drilldown` section), this block. It mirrors the existing `mp-card` / `mp-table` / degraded / empty patterns:

```html
  <section class="mp-paper-closed">
    {% set ct = dashboard.closed_trades %}
    <article class="mp-card">
      <div class="mp-card__head"><div class="mp-card__title">Closed Trades</div></div>
      <div class="mp-card__body">
        {% if ct.status == "error" %}
          <div class="mp-paper-degraded">
            <strong>{{ ct.error_title }}</strong>
            {% if ct.degraded_reason %}
              <div class="mp-paper-muted">{{ ct.degraded_reason }}</div>
            {% endif %}
          </div>
        {% elif ct.data and ct.data.rows %}
          <section class="mp-paper-kpis" aria-label="Closed Trades Summary">
            <article class="mp-card mp-paper-kpi">
              <div class="mp-card__body">
                <div class="mp-card__eyebrow">Realized P&amp;L</div>
                <div class="mp-paper-kpi__value">{{ ct.data.summary.realized_pnl_total }}</div>
              </div>
            </article>
            <article class="mp-card mp-paper-kpi">
              <div class="mp-card__body">
                <div class="mp-card__eyebrow">Closed Trades</div>
                <div class="mp-paper-kpi__value">{{ ct.data.summary.total_count }}</div>
              </div>
            </article>
            <article class="mp-card mp-paper-kpi">
              <div class="mp-card__body">
                <div class="mp-card__eyebrow">Win Rate</div>
                <div class="mp-paper-kpi__value">
                  {% if ct.data.summary.win_rate is not none %}{{ "%.0f%%"|format(ct.data.summary.win_rate * 100) }}{% else %}—{% endif %}
                </div>
              </div>
            </article>
            <article class="mp-card mp-paper-kpi">
              <div class="mp-card__body">
                <div class="mp-card__eyebrow">Avg Return</div>
                <div class="mp-paper-kpi__value">
                  {% if ct.data.summary.avg_return_pct is not none %}{{ "%+.1f%%"|format(ct.data.summary.avg_return_pct * 100) }}{% else %}—{% endif %}
                </div>
              </div>
            </article>
          </section>
          <div class="mp-paper-muted">{{ ct.data.count_label }}</div>
          <div class="mp-paper-table-wrap">
            <table class="mp-table mp-table--paper">
              <thead>
                <tr><th>Exit Date</th><th>Ticker</th><th>Strategy</th><th class="num">Qty</th><th class="num">Entry</th><th class="num">Exit</th><th class="num">Days Held</th><th class="num">P&amp;L</th><th class="num">Return</th></tr>
              </thead>
              <tbody>
              {% for row in ct.data.rows %}
                <tr>
                  <td>{{ row.exit_date if row.exit_date is not none else "—" }}</td>
                  <td><a href="/stock/{{ row.ticker }}" class="mp-ticker-link">{{ row.ticker }}</a></td>
                  <td>{{ row.strategy }}</td>
                  <td class="num">{{ row.quantity }}</td>
                  <td class="num">{{ row.entry_price }}</td>
                  <td class="num">{% if row.exit_price is not none %}{{ row.exit_price }}{% else %}—{% endif %}</td>
                  <td class="num">{% if row.days_held is not none %}{{ row.days_held }}{% else %}—{% endif %}</td>
                  <td class="num">{% if row.realized_pnl is not none %}{{ row.realized_pnl }}{% else %}—{% endif %}</td>
                  <td class="num">{% if row.return_pct is not none %}{{ "%+.1f%%"|format(row.return_pct * 100) }}{% else %}—{% endif %}</td>
                </tr>
              {% endfor %}
              </tbody>
            </table>
          </div>
        {% else %}
          <div class="mp-paper-empty">{{ ct.empty_message }}</div>
        {% endif %}
      </div>
    </article>
  </section>
```

Confirm placement: the new `<section class="mp-paper-closed">` goes inside the top-level `<section class="mp-paper-ops">` (after `mp-paper-drilldown`), before that section's closing tag / `{% endblock %}`. Read the end of the template first to place it correctly.

- [ ] **Step 4: Run to confirm pass**

Run: `uv run pytest tests/web/test_lab_paper_trading.py -k closed_trades -v`
Expected: 2 passed.

- [ ] **Step 5: Full file + lint + commit**

Run: `uv run pytest tests/web/test_lab_paper_trading.py -q` → all pass.
Run: `uv run ruff check tests/web/test_lab_paper_trading.py` → clean. (Templates aren't linted by ruff.)
```bash
git add marketpulse/web/templates/lab_paper_trading.html tests/web/test_lab_paper_trading.py
git commit -m "feat(paper): Closed Trades section on /lab/paper-trading"
```

---

## Task 4: Final integration

- [ ] **Step 1: Full suite**

Run: `uv run pytest -q`
Expected: all pass (note: a pre-existing unrelated failure should NOT appear — `test_charter_route` was fixed; if anything else fails, investigate before proceeding).

- [ ] **Step 2: Lint repo-wide**

Run: `uv run ruff check .`
Expected: All checks passed.

- [ ] **Step 3: Architecture guard (zero-network presenter)**

Run: `uv run pytest -q -k "architecture or zero_network or no_network"` (if such guards exist).
Expected: pass — `_load_closed_trades_section` adds no network imports (DB-only).

- [ ] **Step 4: Confirm clean tree**

Run: `git status` → only the intended files changed; nothing stray staged.
```

---

## Self-review notes (for the implementer)

- **win_rate denominator is the priced subset** (`realized_pnl is not None`), NOT `total_count` — see `test_closed_trades_winrate_excludes_unpriced`.
- **return_pct uses cost basis** `realized_pnl / (entry_price*quantity)`, `None` when cost ≤ 0 or fields missing — `None` rows are skipped in `avg_return_pct`.
- **days_held is `None` (renders "—")** when a date is missing, never `0`.
- **summary is over the full closed set; table rows are capped at 50** (newest exit first).
- **No new route, no migration, no new deps.**
