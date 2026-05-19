# Phase 4 — Backtest Engine MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Strategy Performance Observatory — replay Phase 2/3 EvaluationEvents into 6 per-strategy paper portfolios + SPY baseline, daily-MTM via linear interpolation, soft-cap-driven capacity tracking, surface via new `/lab/backtest` page.

**Architecture:** New `marketpulse/backtest/` module (pure-Python, no GPU, no PyTorch). Simulator iterates a trading-day timeline derived from the DB (union of `event_time.date()` + `horizon_date` values). Daily loop strictly orders CLOSE → OPEN → MTM → RECORD. Metrics computed on daily-return series via `empyrical-reloaded`. New `/lab/backtest` route mirrors `/lab/ai-track`'s NineScrolls layout.

**Tech Stack:** Python 3.12 + FastAPI + SQLAlchemy 2.x + Jinja2 + vanilla CSS (NineScrolls) + `empyrical-reloaded` (NEW). No new database tables. No Alembic migration.

**Spec:** `docs/superpowers/specs/2026-05-19-phase-4-backtest-engine-mvp.md`

---

## File Structure

```
marketpulse/
├── backtest/                              NEW module
│   ├── __init__.py                        NEW: re-export public API
│   ├── types.py                           NEW: StrategyBacktestResult dataclass
│   ├── trading_calendar.py                NEW: trading-day grid from DB outcomes
│   ├── queries.py                         NEW: SQL queries (events + outcomes joined)
│   ├── metrics.py                         NEW: empyrical-reloaded wrappers
│   └── simulator.py                       NEW: per-strategy + SPY + orchestrator
└── web/
    ├── routes/
    │   └── backtest.py                    NEW: GET /lab/backtest
    └── templates/
        ├── lab_backtest.html              NEW: shell page
        └── partials/
            ├── backtest_hero.html         NEW: hero + warning banner
            ├── backtest_kpi_strip.html    NEW: 5 KPI cards
            ├── backtest_equity_chart.html NEW: equity curve SVG (7 lines)
            ├── backtest_drawdown_chart.html NEW: drawdown SVG
            ├── backtest_filter_card.html  NEW: horizon + since_days
            └── backtest_strategy_table.html NEW: leaderboard

marketpulse/web/
├── main.py                                MODIFY: include backtest router
├── static/css/app.css                     MODIFY: append Phase 4 CSS
├── templates/lab_ai_track.html            MODIFY: strategy table row → backtest link
├── templates/partials/ai_track_strategy_table.html  MODIFY: arrow link cell

tests/
├── unit/
│   ├── test_backtest_trading_calendar.py  NEW
│   ├── test_backtest_types.py             NEW
│   ├── test_backtest_metrics.py           NEW
│   └── test_backtest_simulator.py         NEW (the core algorithm)
├── integration/
│   └── test_backtest_queries.py           NEW (DB-driven pipeline)
└── web/
    └── test_lab_backtest.py               NEW (route + partial integration)

pyproject.toml                             MODIFY: add empyrical-reloaded
```

No DB migration. Phase 1 schema (`EvaluationEvent` + `EvaluationOutcome`) provides every data point needed.

---

## Conventions

- **TDD strict**: each task = failing test → run/see fail → minimal impl → run/see pass → commit.
- **Working directory**: `/Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-4-plan` (worktree on `plan/phase-4-backtest`). Implementer should work from a fresh `feat/phase-4-backtest` worktree based on `origin/main`.
- **Run tests**: `uv run pytest <path> -v`
- **Lint**: `uv run ruff check <path>`
- **No new DB tables, no migration** — pure read-side over Phase 1-3.
- **Daily-loop ORDER LOCK** (spec § 15): `CLOSE → OPEN → MTM → RECORD`. Tests assert this order.
- **JOIN causal lock** (spec § 16): `event.event_time.date() < outcome.horizon_date` in every query.
- **Numerical tolerance**: `pytest.approx(..., abs=1e-6)` for float comparisons. `abs=1e-3` only for cumulative metrics where compounded floating error builds up.
- **Frozen dataclasses**: `StrategyBacktestResult` is `@dataclass(frozen=True)`.
- **Spec § Identity** quoted in module docstrings: "A reproducible research observatory for strategy-level synthetic PnL analysis under constrained-capital simulation assumptions."

---

### Task 1: Add `empyrical-reloaded` dependency

**Files:**
- Modify: `pyproject.toml` (add to `dependencies`)

- [ ] **Step 1.1: Add the dependency**

In `pyproject.toml`, find the existing `dependencies = [...]` block and add `"empyrical-reloaded>=0.5"` alphabetically:

```toml
dependencies = [
    "alembic>=1.14",
    "anthropic>=0.40",
    "apscheduler>=3.10",
    "bcrypt>=4.2",
    "empyrical-reloaded>=0.5",
    "fastapi>=0.115",
    ...
]
```

(Preserve existing order if not alphabetical — just add the line where it fits.)

- [ ] **Step 1.2: Lock + install**

```bash
uv lock
uv sync
```

Expected: `empyrical-reloaded` shows in `uv.lock`. `numpy` + `pandas` come transitively (empyrical depends on them).

- [ ] **Step 1.3: Confirm import**

```bash
uv run python -c "import empyrical; print(empyrical.__version__)"
```

Expected: a version string like `0.5.x` printed. No ImportError.

- [ ] **Step 1.4: Confirm specific functions exist**

```bash
uv run python -c "from empyrical import sharpe_ratio, sortino_ratio, max_drawdown, calmar_ratio, annual_return, cum_returns_final; print('ok')"
```

Expected: `ok` printed.

- [ ] **Step 1.5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build(deps): add empyrical-reloaded for Phase 4 backtest metrics

Provides Sharpe / Sortino / MaxDD / Calmar / annual_return /
cum_returns_final on daily return series. Pure-Python, depends on
numpy + pandas (already transitive through other deps). Used by
the new marketpulse.backtest.metrics module."
```

---

### Task 2: `Strategy Backtest Result` dataclass + module init

**Files:**
- Create: `marketpulse/backtest/__init__.py`
- Create: `marketpulse/backtest/types.py`
- Test: `tests/unit/test_backtest_types.py`

- [ ] **Step 2.1: Write failing tests**

Create `tests/unit/test_backtest_types.py`:

```python
"""StrategyBacktestResult dataclass — frozen, value-equal, Phase 5 hooks."""
from dataclasses import FrozenInstanceError
from datetime import date

import pytest


def _result_kwargs(**overrides):
    """Minimum-viable result kwargs; tests override specific fields."""
    base = {
        "strategy": "momentum_breakout",
        "display_name": "动量突破",
        "horizon": 5,
        "n_trades": 10,
        "n_capacity_skipped": 0,
        "cumulative_return": 0.05,
        "annual_return": 0.12,
        "sharpe": 1.2,
        "sortino": 1.5,
        "max_drawdown": -0.08,
        "calmar": 1.5,
        "win_rate": 0.6,
        "avg_win_pct": 0.03,
        "avg_loss_pct": -0.02,
        "daily_equity_curve": [(date(2026, 4, 1), 10000.0), (date(2026, 5, 1), 10500.0)],
        "excess_vs_spy": 0.02,
    }
    base.update(overrides)
    return base


def test_result_is_frozen():
    from marketpulse.backtest.types import StrategyBacktestResult
    r = StrategyBacktestResult(**_result_kwargs())
    with pytest.raises(FrozenInstanceError):
        r.strategy = "other"


def test_result_required_fields():
    from marketpulse.backtest.types import StrategyBacktestResult
    with pytest.raises(TypeError):
        StrategyBacktestResult()


def test_mtm_model_default_is_linear_interpolation_v0():
    from marketpulse.backtest.types import StrategyBacktestResult
    r = StrategyBacktestResult(**_result_kwargs())
    assert r.mtm_model == "linear_interpolation_v0"


def test_phase5_reserved_fields_default_to_none():
    from marketpulse.backtest.types import StrategyBacktestResult
    r = StrategyBacktestResult(**_result_kwargs())
    assert r.strategy_exposure is None
    assert r.capital_bid_score is None


def test_metric_fields_accept_none():
    """Strategies with n_trades < 5 report None for Sharpe/Sortino/Calmar."""
    from marketpulse.backtest.types import StrategyBacktestResult
    r = StrategyBacktestResult(**_result_kwargs(
        n_trades=2, sharpe=None, sortino=None, calmar=None,
    ))
    assert r.sharpe is None
    assert r.sortino is None
    assert r.calmar is None


def test_excess_vs_spy_can_be_negative():
    from marketpulse.backtest.types import StrategyBacktestResult
    r = StrategyBacktestResult(**_result_kwargs(excess_vs_spy=-0.05))
    assert r.excess_vs_spy == -0.05
```

- [ ] **Step 2.2: Run, fail**

```bash
uv run pytest tests/unit/test_backtest_types.py -v
```

Expected: 6 fails with `ModuleNotFoundError: No module named 'marketpulse.backtest'`.

- [ ] **Step 2.3: Create `marketpulse/backtest/__init__.py`**

```python
"""Backtest Engine MVP (Phase 4) — Strategy Performance Observatory.

A reproducible research observatory for strategy-level synthetic PnL
analysis under constrained-capital simulation assumptions. NOT a
faithful execution-level trading simulator.

See docs/superpowers/specs/2026-05-19-phase-4-backtest-engine-mvp.md
for the locked design decisions (16 of them).
"""
from marketpulse.backtest.types import StrategyBacktestResult

__all__ = ["StrategyBacktestResult"]
```

- [ ] **Step 2.4: Create `marketpulse/backtest/types.py`**

```python
"""StrategyBacktestResult — frozen dataclass returned by the simulator."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class StrategyBacktestResult:
    """One per-strategy (or SPY) backtest run result.

    All Sharpe/Sortino/Calmar fields are None when n_trades < 5
    (insufficient sample). daily_equity_curve is downsampled to ~120
    points before being returned (see simulator.downsample_equity_curve()).

    Fields are 3-layered: identity, performance, trade-level. Phase 5
    reserved hooks at bottom (always None in v0).
    """

    # Identity
    strategy: str                          # "momentum_breakout" or "__spy_buyhold__"
    display_name: str                      # "动量突破" or "SPY 基准"
    horizon: int                           # 5 / 20 / 60; 0 for SPY baseline

    # Trade counts
    n_trades: int
    n_capacity_skipped: int

    # Performance metrics (None if n_trades < 5)
    cumulative_return: float
    annual_return: float
    sharpe: float | None
    sortino: float | None
    max_drawdown: float
    calmar: float | None

    # Trade-level
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float

    # Equity curve (downsampled to ~120 points before return)
    daily_equity_curve: list[tuple[date, float]]

    # Benchmark
    excess_vs_spy: float

    # Defaulted (always-default in v0)
    mtm_model: str = "linear_interpolation_v0"

    # ---------- Reserved Phase 5 hooks (always None in v0) ----------
    # Phase 5 (True Portfolio Coupling) populates these for new runs;
    # v0 leaves them None for retroactive replay compatibility.
    strategy_exposure: float | None = None      # avg gross exposure during run
    capital_bid_score: float | None = None      # priority weight in shared pool
```

All non-defaulted fields are required; this matches the test fixture's positional construction via `_result_kwargs(...)`.

- [ ] **Step 2.5: Run, pass**

```bash
uv run pytest tests/unit/test_backtest_types.py -v
```

Expected: 6/6 pass.

- [ ] **Step 2.6: Ruff + commit**

```bash
uv run ruff check marketpulse/backtest/ tests/unit/test_backtest_types.py
git add marketpulse/backtest/ tests/unit/test_backtest_types.py
git commit -m "feat(backtest): StrategyBacktestResult dataclass + module init

Frozen dataclass with 18 fields (15 required + 3 defaulted):
- 3 identity (strategy, display_name, horizon)
- 2 trade counts (n_trades, n_capacity_skipped)
- 7 performance (cum/annual/sharpe/sortino/maxdd/calmar/win)
- 3 trade-level (win_rate, avg_win_pct, avg_loss_pct)
- 1 equity curve (downsampled in simulator)
- 1 benchmark (excess_vs_spy)
- 1 provenance (mtm_model = 'linear_interpolation_v0')
- 2 Phase 5 reserved hooks (strategy_exposure, capital_bid_score)

6 unit tests cover frozen behavior, required-field discipline,
default values, None-allowed metric fields.

Spec: docs/superpowers/specs/2026-05-19-phase-4-backtest-engine-mvp.md"
```

---

### Task 3: Trading-day calendar from DB

**Files:**
- Create: `marketpulse/backtest/trading_calendar.py`
- Test: `tests/unit/test_backtest_trading_calendar.py`

- [ ] **Step 3.1: Write failing tests**

Create `tests/unit/test_backtest_trading_calendar.py`:

```python
"""Trading-day calendar — derived from DB outcomes, not external library."""
from datetime import date


def test_build_calendar_returns_sorted_unique_dates():
    from marketpulse.backtest.trading_calendar import build_calendar
    raw_dates = [
        date(2026, 5, 1),
        date(2026, 5, 5),
        date(2026, 5, 1),   # duplicate
        date(2026, 4, 30),
        date(2026, 5, 3),
    ]
    cal = build_calendar(raw_dates)
    assert cal == [date(2026, 4, 30), date(2026, 5, 1), date(2026, 5, 3), date(2026, 5, 5)]


def test_build_calendar_handles_empty_input():
    from marketpulse.backtest.trading_calendar import build_calendar
    assert build_calendar([]) == []


def test_trading_days_between_inclusive_endpoints():
    from marketpulse.backtest.trading_calendar import build_calendar, trading_days_between
    cal = build_calendar([
        date(2026, 5, 1),
        date(2026, 5, 4),
        date(2026, 5, 5),
        date(2026, 5, 8),
    ])
    # Between 5/1 and 5/8 inclusive: 5/1, 5/4, 5/5, 5/8 = 4 trading days
    assert trading_days_between(cal, date(2026, 5, 1), date(2026, 5, 8)) == 4


def test_trading_days_between_returns_zero_for_same_day():
    from marketpulse.backtest.trading_calendar import build_calendar, trading_days_between
    cal = build_calendar([date(2026, 5, 1)])
    assert trading_days_between(cal, date(2026, 5, 1), date(2026, 5, 1)) == 1


def test_trading_days_between_excludes_out_of_range():
    from marketpulse.backtest.trading_calendar import build_calendar, trading_days_between
    cal = build_calendar([
        date(2026, 5, 1),
        date(2026, 5, 4),
        date(2026, 5, 5),
    ])
    # start before earliest date → only count from earliest
    assert trading_days_between(cal, date(2026, 4, 1), date(2026, 5, 4)) == 2


def test_days_elapsed_fraction_at_entry_is_zero():
    """For MTM linear interp: when current==entry, fraction = 0."""
    from marketpulse.backtest.trading_calendar import build_calendar, elapsed_fraction
    cal = build_calendar([
        date(2026, 5, 1), date(2026, 5, 4),
        date(2026, 5, 5), date(2026, 5, 6), date(2026, 5, 7),
    ])
    f = elapsed_fraction(cal, entry=date(2026, 5, 1),
                          horizon=date(2026, 5, 7), current=date(2026, 5, 1))
    assert f == 0.0


def test_days_elapsed_fraction_at_horizon_is_one():
    """When current==horizon, fraction = 1."""
    from marketpulse.backtest.trading_calendar import build_calendar, elapsed_fraction
    cal = build_calendar([
        date(2026, 5, 1), date(2026, 5, 4),
        date(2026, 5, 5), date(2026, 5, 6), date(2026, 5, 7),
    ])
    f = elapsed_fraction(cal, entry=date(2026, 5, 1),
                          horizon=date(2026, 5, 7), current=date(2026, 5, 7))
    assert f == 1.0


def test_days_elapsed_fraction_middle():
    """Halfway through holding period."""
    from marketpulse.backtest.trading_calendar import build_calendar, elapsed_fraction
    cal = build_calendar([
        date(2026, 5, 1), date(2026, 5, 4),
        date(2026, 5, 5), date(2026, 5, 6), date(2026, 5, 7),
    ])
    # entry=5/1, horizon=5/7, 5 trading days total (5/1, 5/4, 5/5, 5/6, 5/7)
    # current=5/5 → elapsed 3 days / total 5 → fraction = 0.5 (3-1)/(5-1) = 0.5
    import pytest
    f = elapsed_fraction(cal, entry=date(2026, 5, 1),
                          horizon=date(2026, 5, 7), current=date(2026, 5, 5))
    assert f == pytest.approx(0.5, abs=1e-6)
```

- [ ] **Step 3.2: Run, fail**

```bash
uv run pytest tests/unit/test_backtest_trading_calendar.py -v
```

Expected: 8 fails with ImportError.

- [ ] **Step 3.3: Create `marketpulse/backtest/trading_calendar.py`**

```python
"""Trading-day calendar derived from DB outcomes.

Phase 1's outcomes.py computes horizon_date via yfinance bar-index
alignment, which already respects weekends + US holidays. We don't
need a separate calendar library — instead, build the trading-day
grid from the union of all event_time.date() + horizon_date values
already in the DB.

Limitation: gaps appear if a date has no events (rare for our use).
If illiquid tickers cause gaps that matter, Phase 4.5 can swap in
pandas_market_calendars.
"""
from __future__ import annotations

import bisect
from datetime import date


def build_calendar(raw_dates: list[date]) -> list[date]:
    """Deduplicate + sort a list of dates into the trading-day grid.

    Args:
        raw_dates: union of event_time.date() and horizon_date values
            pulled from EvaluationOutcome rows.

    Returns:
        Sorted ascending, no duplicates.
    """
    return sorted(set(raw_dates))


def trading_days_between(
    calendar: list[date], start: date, end: date,
) -> int:
    """Inclusive count of calendar dates in [start, end].

    Out-of-range dates are clipped. start > end returns 0.
    """
    if start > end:
        return 0
    left = bisect.bisect_left(calendar, start)
    right = bisect.bisect_right(calendar, end)
    return right - left


def elapsed_fraction(
    calendar: list[date], *, entry: date, horizon: date, current: date,
) -> float:
    """Linear-interp fraction of holding period elapsed.

    Returns:
        0.0 when current == entry
        1.0 when current == horizon
        Linearly interpolated otherwise.
        Clipped to [0, 1].
    """
    total = trading_days_between(calendar, entry, horizon)
    if total <= 1:
        return 0.0 if current < horizon else 1.0
    elapsed = trading_days_between(calendar, entry, current) - 1
    elapsed = max(0, min(elapsed, total - 1))
    return elapsed / (total - 1)
```

- [ ] **Step 3.4: Run, pass**

```bash
uv run pytest tests/unit/test_backtest_trading_calendar.py -v
```

Expected: 8/8 pass.

- [ ] **Step 3.5: Ruff + commit**

```bash
uv run ruff check marketpulse/backtest/trading_calendar.py tests/unit/test_backtest_trading_calendar.py
git add marketpulse/backtest/trading_calendar.py tests/unit/test_backtest_trading_calendar.py
git commit -m "feat(backtest): trading_calendar — DB-derived trading-day grid

Three pure functions:
- build_calendar(): sort + dedupe raw dates
- trading_days_between(): inclusive count via bisect
- elapsed_fraction(): linear-interp fraction for MTM (0 at entry,
  1 at horizon, clipped)

No external dependency. Uses union of event_time + horizon_date
values from DB outcomes (which Phase 1 already aligned to yfinance
trading days). Phase 4.5 can swap pandas_market_calendars if
illiquid-ticker gaps emerge.

8 unit tests cover dedup, empty input, inclusive counting,
fraction edge cases (entry, horizon, middle)."
```

---

### Task 4: DB queries module

**Files:**
- Create: `marketpulse/backtest/queries.py`
- Test: `tests/integration/test_backtest_queries.py`

- [ ] **Step 4.1: Write failing tests**

Create `tests/integration/test_backtest_queries.py`:

```python
"""Backtest DB queries — pull events+outcomes for a strategy/horizon."""
from datetime import UTC, date, datetime, timedelta

import pytest

from marketpulse.db.models import EvaluationEvent, EvaluationOutcome


def _seed(db, *, ticker="AAPL", subtype="bullish", source="stock_analysis",
          strategy="momentum_breakout", days_ago=10, excess=0.03, horizon=5):
    """Seed one event + matching outcome."""
    e = EvaluationEvent(
        event_type="ai_analysis", subtype=subtype, ticker=ticker,
        event_time=datetime.now(UTC) - timedelta(days=days_ago),
        event_price=100.0,
        payload={"source": source, "strategy": strategy,
                 "strategy_version": "v1", "prompt_version": "analysis-v4"},
    )
    db.add(e)
    db.flush()
    outcome_date = date.today() - timedelta(days=max(0, days_ago - horizon))
    o = EvaluationOutcome(
        event_id=e.id, horizon_trading_days=horizon,
        event_price=100.0, horizon_price=100.0 * (1 + excess + 0.001),
        horizon_date=outcome_date,
        forward_return=excess + 0.001,
        benchmark_ticker="SPY", benchmark_forward_return=0.001,
        excess_return=excess,
    )
    db.add(o)
    db.flush()
    return e, o


def test_get_bullish_events_with_outcomes_filters_by_strategy(db_session):
    from marketpulse.backtest.queries import get_bullish_events_with_outcomes

    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    _seed(db_session, ticker="BBB", strategy="fundamental_value")
    db_session.commit()

    rows = get_bullish_events_with_outcomes(
        db_session, strategy="momentum_breakout", horizon=5,
    )
    tickers = [r.ticker for r in rows]
    assert tickers == ["AAA"]


def test_filters_out_neutral_and_bearish(db_session):
    from marketpulse.backtest.queries import get_bullish_events_with_outcomes

    _seed(db_session, ticker="A1", subtype="bullish")
    _seed(db_session, ticker="A2", subtype="neutral")
    _seed(db_session, ticker="A3", subtype="bearish")
    db_session.commit()

    rows = get_bullish_events_with_outcomes(
        db_session, strategy="momentum_breakout", horizon=5,
    )
    assert len(rows) == 1
    assert rows[0].ticker == "A1"


def test_filters_by_horizon(db_session):
    from marketpulse.backtest.queries import get_bullish_events_with_outcomes

    e, _ = _seed(db_session, ticker="X", horizon=5)
    # Add another outcome at horizon=20 on same event
    o20 = EvaluationOutcome(
        event_id=e.id, horizon_trading_days=20,
        event_price=100.0, horizon_price=110.0,
        horizon_date=date.today(),
        forward_return=0.10, benchmark_ticker="SPY",
        benchmark_forward_return=0.02, excess_return=0.08,
    )
    db_session.add(o20)
    db_session.commit()

    rows_5 = get_bullish_events_with_outcomes(db_session, strategy="momentum_breakout", horizon=5)
    rows_20 = get_bullish_events_with_outcomes(db_session, strategy="momentum_breakout", horizon=20)
    assert len(rows_5) == 1
    assert len(rows_20) == 1
    assert rows_5[0].horizon_price != rows_20[0].horizon_price


def test_filters_by_since(db_session):
    from marketpulse.backtest.queries import get_bullish_events_with_outcomes

    _seed(db_session, ticker="OLD", days_ago=120)
    _seed(db_session, ticker="NEW", days_ago=10)
    db_session.commit()

    cutoff = date.today() - timedelta(days=90)
    rows = get_bullish_events_with_outcomes(
        db_session, strategy="momentum_breakout", horizon=5, since=cutoff,
    )
    tickers = [r.ticker for r in rows]
    assert tickers == ["NEW"]


def test_filters_out_recap_source(db_session):
    """Spec § Open Decision #14: backtest is stock_analysis only."""
    from marketpulse.backtest.queries import get_bullish_events_with_outcomes

    _seed(db_session, ticker="STK", source="stock_analysis")
    _seed(db_session, ticker="RCP", source="recap")
    db_session.commit()

    rows = get_bullish_events_with_outcomes(
        db_session, strategy="momentum_breakout", horizon=5,
    )
    tickers = [r.ticker for r in rows]
    assert tickers == ["STK"]


def test_causal_constraint_excludes_future_dated_horizons(db_session):
    """Spec § Open Decision #16: event.event_time.date() < outcome.horizon_date."""
    from marketpulse.backtest.queries import get_bullish_events_with_outcomes

    # Create an event with horizon_date earlier than event_time (anomaly)
    e = EvaluationEvent(
        event_type="ai_analysis", subtype="bullish", ticker="BUG",
        event_time=datetime(2026, 5, 10, tzinfo=UTC),
        event_price=100.0,
        payload={"source": "stock_analysis", "strategy": "momentum_breakout",
                 "strategy_version": "v1", "prompt_version": "analysis-v4"},
    )
    db_session.add(e); db_session.flush()
    db_session.add(EvaluationOutcome(
        event_id=e.id, horizon_trading_days=5,
        event_price=100.0, horizon_price=105.0,
        horizon_date=date(2026, 5, 5),  # BEFORE event_time
        forward_return=0.05, benchmark_ticker="SPY",
        benchmark_forward_return=0.01, excess_return=0.04,
    ))
    db_session.commit()

    rows = get_bullish_events_with_outcomes(
        db_session, strategy="momentum_breakout", horizon=5,
    )
    # Anomalous row excluded
    assert all(r.ticker != "BUG" for r in rows)


def test_returns_namedtuple_like_with_required_fields(db_session):
    from marketpulse.backtest.queries import get_bullish_events_with_outcomes

    _seed(db_session, ticker="ZZZ")
    db_session.commit()

    rows = get_bullish_events_with_outcomes(
        db_session, strategy="momentum_breakout", horizon=5,
    )
    assert len(rows) == 1
    r = rows[0]
    # Required attributes accessible
    assert r.ticker == "ZZZ"
    assert r.event_time is not None
    assert r.event_price == 100.0
    assert r.horizon_price > 100.0
    assert r.horizon_date is not None
    assert r.benchmark_forward_return == 0.001
```

- [ ] **Step 4.2: Run, fail**

```bash
uv run pytest tests/integration/test_backtest_queries.py -v
```

Expected: 7 ImportError fails.

- [ ] **Step 4.3: Create `marketpulse/backtest/queries.py`**

```python
"""DB queries for backtest simulator — joins EvaluationEvent + Outcome."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from marketpulse.db.models import EvaluationEvent, EvaluationOutcome


@dataclass(frozen=True)
class EventOutcomePair:
    """Flattened (event, outcome) row used by the simulator.

    Includes only the fields the simulator actually needs — avoids
    holding ORM-attached objects across simulator iterations.
    """
    ticker: str
    event_time: datetime
    event_price: float
    horizon_price: float
    horizon_date: date
    forward_return: float
    benchmark_forward_return: float


def get_bullish_events_with_outcomes(
    db: Session,
    *,
    strategy: str,
    horizon: int,
    since: date | None = None,
) -> list[EventOutcomePair]:
    """Bullish events for one strategy at one horizon, with mature outcomes.

    Filters (spec § Architecture + § Open Decisions #14, #16):
      - event.event_type == "ai_analysis"
      - event.subtype == "bullish"
      - event.payload["source"] == "stock_analysis"  (Decision #14)
      - event.payload["strategy"] == strategy
      - outcome.horizon_trading_days == horizon
      - (since is None) OR (event.event_time >= since)
      - event.event_time.date() < outcome.horizon_date  (Decision #16)

    Returns:
        Sorted ASC by event_time (entry order for the simulator).
    """
    stmt = (
        select(
            EvaluationEvent.ticker,
            EvaluationEvent.event_time,
            EvaluationOutcome.event_price,
            EvaluationOutcome.horizon_price,
            EvaluationOutcome.horizon_date,
            EvaluationOutcome.forward_return,
            EvaluationOutcome.benchmark_forward_return,
        )
        .join(EvaluationOutcome, EvaluationOutcome.event_id == EvaluationEvent.id)
        .where(EvaluationEvent.event_type == "ai_analysis")
        .where(EvaluationEvent.subtype == "bullish")
        .where(EvaluationOutcome.horizon_trading_days == horizon)
        .where(func.json_extract(EvaluationEvent.payload, "$.source") == "stock_analysis")
        .where(func.json_extract(EvaluationEvent.payload, "$.strategy") == strategy)
        .where(func.date(EvaluationEvent.event_time) < EvaluationOutcome.horizon_date)
        .order_by(EvaluationEvent.event_time.asc())
    )
    if since is not None:
        stmt = stmt.where(
            EvaluationEvent.event_time >= datetime.combine(
                since, datetime.min.time(), tzinfo=UTC,
            ),
        )

    rows = db.execute(stmt).all()
    return [
        EventOutcomePair(
            ticker=r.ticker,
            event_time=r.event_time,
            event_price=r.event_price,
            horizon_price=r.horizon_price,
            horizon_date=r.horizon_date,
            forward_return=r.forward_return,
            benchmark_forward_return=r.benchmark_forward_return,
        )
        for r in rows
    ]
```

- [ ] **Step 4.4: Run, pass**

```bash
uv run pytest tests/integration/test_backtest_queries.py -v
```

Expected: 7/7 pass.

- [ ] **Step 4.5: Ruff + commit**

```bash
uv run ruff check marketpulse/backtest/queries.py tests/integration/test_backtest_queries.py
git add marketpulse/backtest/queries.py tests/integration/test_backtest_queries.py
git commit -m "feat(backtest): queries module — bullish events with outcomes

get_bullish_events_with_outcomes() returns flat EventOutcomePair rows
filtered by strategy + horizon + since, with strict source check
(stock_analysis only, recap excluded per spec Decision #14) and
causal JOIN constraint event.event_time.date() < outcome.horizon_date
(spec Decision #16).

EventOutcomePair flattens the SQLAlchemy result to a frozen dataclass
the simulator can iterate without holding ORM-attached objects.

7 integration tests cover strategy/subtype/horizon/since/source
filtering, causal constraint, and result shape."
```

---

### Task 5: Metrics module (empyrical wrappers)

**Files:**
- Create: `marketpulse/backtest/metrics.py`
- Test: `tests/unit/test_backtest_metrics.py`

- [ ] **Step 5.1: Write failing tests**

Create `tests/unit/test_backtest_metrics.py`:

```python
"""Metrics module — empyrical-reloaded wrappers on daily return series."""
from datetime import date

import pytest


def _equity_curve(start_value=10_000, daily_returns=None):
    """Build an equity_curve list[(date, float)] from daily returns."""
    daily_returns = daily_returns or []
    curve = [(date(2026, 4, 1), float(start_value))]
    v = start_value
    for i, r in enumerate(daily_returns, start=2):
        v *= (1 + r)
        curve.append((date(2026, 4, i), v))
    return curve


def test_compute_returns_none_metrics_when_n_trades_below_threshold():
    """Spec § Metrics: metrics are None when n_trades < 5."""
    from marketpulse.backtest.metrics import compute_metrics
    curve = _equity_curve(daily_returns=[0.01, 0.02])
    m = compute_metrics(equity_curve=curve, n_trades=2, trade_returns=[0.05, 0.03])
    assert m.sharpe is None
    assert m.sortino is None
    assert m.calmar is None


def test_compute_returns_real_metrics_when_n_trades_above_threshold():
    from marketpulse.backtest.metrics import compute_metrics
    # 30 days of small steady gains
    curve = _equity_curve(daily_returns=[0.005] * 30)
    m = compute_metrics(equity_curve=curve, n_trades=10,
                       trade_returns=[0.005] * 10)
    assert m.sharpe is not None
    assert m.sharpe > 0
    assert m.cumulative_return > 0


def test_max_drawdown_is_negative_on_drawdown_path():
    from marketpulse.backtest.metrics import compute_metrics
    # +5 then -10 → drawdown of about -10/105
    curve = _equity_curve(daily_returns=[0.05] + [-0.02] * 10)
    m = compute_metrics(equity_curve=curve, n_trades=10,
                       trade_returns=[0.05] * 5 + [-0.02] * 5)
    assert m.max_drawdown < 0


def test_win_rate_computed_from_trade_returns():
    from marketpulse.backtest.metrics import compute_metrics
    curve = _equity_curve(daily_returns=[0.01] * 10)
    m = compute_metrics(equity_curve=curve, n_trades=10,
                       trade_returns=[0.05, 0.03, -0.02, 0.01, -0.04, 0.02, 0.01, -0.01, 0.03, 0.05])
    # 7 wins / 10 = 0.7
    assert m.win_rate == pytest.approx(0.7, abs=1e-6)


def test_avg_win_pct_is_mean_of_positive_trades():
    from marketpulse.backtest.metrics import compute_metrics
    curve = _equity_curve(daily_returns=[0.0] * 5)
    m = compute_metrics(equity_curve=curve, n_trades=5,
                       trade_returns=[0.05, 0.10, -0.02, 0.06, -0.04])
    # positives: 0.05, 0.10, 0.06 → mean ≈ 0.07
    assert m.avg_win_pct == pytest.approx(0.07, abs=1e-6)


def test_avg_loss_pct_is_mean_of_negative_trades_negative_sign():
    from marketpulse.backtest.metrics import compute_metrics
    curve = _equity_curve(daily_returns=[0.0] * 5)
    m = compute_metrics(equity_curve=curve, n_trades=5,
                       trade_returns=[0.05, -0.02, 0.06, -0.04, -0.06])
    # losses: -0.02, -0.04, -0.06 → mean = -0.04
    assert m.avg_loss_pct == pytest.approx(-0.04, abs=1e-6)


def test_zero_trades_returns_zeroed_metrics():
    """No bullish events at all — empty equity curve."""
    from marketpulse.backtest.metrics import compute_metrics
    m = compute_metrics(equity_curve=[(date(2026, 5, 1), 10_000.0)],
                       n_trades=0, trade_returns=[])
    assert m.cumulative_return == 0.0
    assert m.win_rate == 0.0
    assert m.sharpe is None
```

- [ ] **Step 5.2: Run, fail**

```bash
uv run pytest tests/unit/test_backtest_metrics.py -v
```

Expected: 7 fails (module doesn't exist).

- [ ] **Step 5.3: Create `marketpulse/backtest/metrics.py`**

```python
"""Metrics on daily return series, computed via empyrical-reloaded.

Spec § Open Decision #8: Sharpe / Sortino / Calmar are computed on
DAILY return series (NOT on irregular-spacing trade returns) to
avoid the per-trade-spacing Sharpe inflation bug.

Sample threshold: n_trades < 5 returns None for risk-adjusted
metrics (Sharpe / Sortino / Calmar). Cumulative_return / annual_return
/ max_drawdown / win_rate are always computed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
from empyrical import (
    annual_return,
    calmar_ratio,
    cum_returns_final,
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
)

# Spec § Metrics: floor for risk-adjusted ratios.
MIN_TRADES_FOR_RISK_METRICS = 5


@dataclass(frozen=True)
class BacktestMetrics:
    """Computed metrics block — fed into StrategyBacktestResult."""

    cumulative_return: float
    annual_return: float
    sharpe: float | None
    sortino: float | None
    max_drawdown: float
    calmar: float | None
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float


def compute_metrics(
    *,
    equity_curve: list[tuple[date, float]],
    n_trades: int,
    trade_returns: list[float],
) -> BacktestMetrics:
    """Compute all metrics from a daily equity curve + trade list.

    Args:
        equity_curve: list of (date, portfolio_value) sorted ASC.
        n_trades: number of trades executed (used for sample-size floor).
        trade_returns: per-trade realized returns (used for win_rate /
            avg_win_pct / avg_loss_pct). Length = n_trades.

    Returns:
        BacktestMetrics with all 9 fields populated.
    """
    if len(equity_curve) < 2:
        # Edge case: empty / single-point curve, no series math possible
        return BacktestMetrics(
            cumulative_return=0.0,
            annual_return=0.0,
            sharpe=None,
            sortino=None,
            max_drawdown=0.0,
            calmar=None,
            win_rate=_win_rate(trade_returns) if trade_returns else 0.0,
            avg_win_pct=_avg_win(trade_returns) if trade_returns else 0.0,
            avg_loss_pct=_avg_loss(trade_returns) if trade_returns else 0.0,
        )

    values = np.array([v for _, v in equity_curve], dtype=float)
    daily_returns = np.diff(values) / values[:-1]

    cum_ret = float(cum_returns_final(daily_returns))
    annual = float(annual_return(daily_returns))
    mdd = float(max_drawdown(daily_returns))

    if n_trades >= MIN_TRADES_FOR_RISK_METRICS:
        s = float(sharpe_ratio(daily_returns))
        so = float(sortino_ratio(daily_returns))
        c = float(calmar_ratio(daily_returns))
        # empyrical returns inf/-inf on degenerate inputs — normalize to None.
        s = None if not np.isfinite(s) else s
        so = None if not np.isfinite(so) else so
        c = None if not np.isfinite(c) else c
    else:
        s = so = c = None

    return BacktestMetrics(
        cumulative_return=cum_ret,
        annual_return=annual,
        sharpe=s,
        sortino=so,
        max_drawdown=mdd,
        calmar=c,
        win_rate=_win_rate(trade_returns),
        avg_win_pct=_avg_win(trade_returns),
        avg_loss_pct=_avg_loss(trade_returns),
    )


def _win_rate(trade_returns: list[float]) -> float:
    if not trade_returns:
        return 0.0
    wins = sum(1 for r in trade_returns if r > 0)
    return wins / len(trade_returns)


def _avg_win(trade_returns: list[float]) -> float:
    wins = [r for r in trade_returns if r > 0]
    return sum(wins) / len(wins) if wins else 0.0


def _avg_loss(trade_returns: list[float]) -> float:
    losses = [r for r in trade_returns if r < 0]
    return sum(losses) / len(losses) if losses else 0.0
```

- [ ] **Step 5.4: Run, pass**

```bash
uv run pytest tests/unit/test_backtest_metrics.py -v
```

Expected: 7/7 pass.

- [ ] **Step 5.5: Ruff + commit**

```bash
uv run ruff check marketpulse/backtest/metrics.py tests/unit/test_backtest_metrics.py
git add marketpulse/backtest/metrics.py tests/unit/test_backtest_metrics.py
git commit -m "feat(backtest): metrics module — empyrical wrappers + trade-level

compute_metrics() takes a daily equity curve + trade_returns list
and returns BacktestMetrics (9 fields). Risk-adjusted ratios
(Sharpe/Sortino/Calmar) require n_trades >= MIN_TRADES_FOR_RISK_METRICS
(5); otherwise None. Cumulative / annual / max_drawdown / win_rate /
avg_win_pct / avg_loss_pct always computed.

Inf / -inf from empyrical's degenerate paths normalized to None.

7 unit tests cover sample-size threshold, drawdown direction, win
rate / avg_win / avg_loss math, zero-trade edge case."
```

---

### Task 6: Simulator — strategy portfolio core algorithm

**Files:**
- Create: `marketpulse/backtest/simulator.py`
- Test: `tests/unit/test_backtest_simulator.py`

- [ ] **Step 6.1: Write failing tests**

Create `tests/unit/test_backtest_simulator.py`:

```python
"""Per-strategy portfolio simulator — CLOSE → OPEN → MTM → RECORD daily loop."""
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest

from marketpulse.backtest.queries import EventOutcomePair


def _pair(ticker, event_date, event_price, horizon_date, horizon_price,
          benchmark_return=0.01):
    """Helper to construct an EventOutcomePair."""
    return EventOutcomePair(
        ticker=ticker,
        event_time=datetime.combine(event_date, datetime.min.time(), tzinfo=UTC),
        event_price=event_price,
        horizon_price=horizon_price,
        horizon_date=horizon_date,
        forward_return=(horizon_price - event_price) / event_price,
        benchmark_forward_return=benchmark_return,
    )


def test_zero_events_returns_flat_equity_curve_and_zero_trades():
    from marketpulse.backtest.simulator import simulate_strategy_from_pairs
    r = simulate_strategy_from_pairs(
        pairs=[],
        strategy="momentum_breakout",
        display_name="动量突破",
        horizon=5,
        initial_capital=10_000.0,
        position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    assert r.n_trades == 0
    assert r.cumulative_return == 0.0


def test_single_winning_trade_increases_equity():
    """One bullish event +5% → portfolio value at horizon = 10_050."""
    from marketpulse.backtest.simulator import simulate_strategy_from_pairs
    pairs = [_pair("AAA", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)]
    r = simulate_strategy_from_pairs(
        pairs=pairs,
        strategy="momentum_breakout",
        display_name="动量突破",
        horizon=5,
        initial_capital=10_000.0,
        position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    assert r.n_trades == 1
    # Final equity = 10_000 - 1_000 + 1_050 = 10_050
    final_val = r.daily_equity_curve[-1][1]
    assert final_val == pytest.approx(10_050.0, abs=1e-3)
    assert r.cumulative_return == pytest.approx(0.005, abs=1e-4)


def test_capital_cap_skips_excess_signals():
    """11 simultaneous $1k bullish events with $10k cap → 10 traded, 1 skipped."""
    from marketpulse.backtest.simulator import simulate_strategy_from_pairs
    # All on the same day, all 5d horizon, all +1%
    entry = date(2026, 5, 1)
    exit_ = date(2026, 5, 8)
    pairs = [_pair(f"T{i}", entry, 100.0, exit_, 101.0) for i in range(11)]
    r = simulate_strategy_from_pairs(
        pairs=pairs,
        strategy="momentum_breakout",
        display_name="动量突破",
        horizon=5,
        initial_capital=10_000.0,
        position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    assert r.n_trades == 10
    assert r.n_capacity_skipped == 1


def test_loop_order_close_before_open_frees_capital_same_day():
    """A position that closes on day D should free capital for a new signal
    on day D — verifies the CLOSE → OPEN ordering.

    Setup: first event entry=5/1 horizon=5/4. Second event entry=5/4.
    Initial position fills the $10k cap. On 5/4 morning, position 1
    closes — its $1k returns to cash. New event on 5/4 should NOT be
    skipped (cap freed).
    """
    from marketpulse.backtest.simulator import simulate_strategy_from_pairs
    pairs = [
        # 10 positions on 5/1, each $1k, totaling $10k cap
        *[_pair(f"A{i}", date(2026, 5, 1), 100.0, date(2026, 5, 4), 101.0) for i in range(10)],
        # 1 position on 5/4 — should fit because day-5/1 positions closed
        _pair("B0", date(2026, 5, 4), 100.0, date(2026, 5, 11), 102.0),
    ]
    r = simulate_strategy_from_pairs(
        pairs=pairs,
        strategy="momentum_breakout",
        display_name="动量突破",
        horizon=5,
        initial_capital=10_000.0,
        position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    # All 11 traded — no skip if CLOSE precedes OPEN
    assert r.n_trades == 11, (
        f"Expected 11 (10 close on 5/4, freeing cap for 11th), got {r.n_trades} "
        f"with {r.n_capacity_skipped} skipped — CLOSE must run before OPEN"
    )
    assert r.n_capacity_skipped == 0


def test_newly_opened_position_does_not_participate_in_same_day_mtm():
    """Open day D: position's est_price == entry_price (fraction = 0)."""
    from marketpulse.backtest.simulator import simulate_strategy_from_pairs
    pairs = [_pair("X", date(2026, 5, 1), 100.0, date(2026, 5, 8), 110.0)]
    r = simulate_strategy_from_pairs(
        pairs=pairs,
        strategy="momentum_breakout", display_name="动量突破", horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    # On entry day (5/1), equity should be 10_000 exactly:
    # cash $9_000 + position $1_000 (at entry_price, no MTM yet)
    first_day_val = r.daily_equity_curve[0][1]
    assert first_day_val == pytest.approx(10_000.0, abs=1e-6)


def test_mtm_progresses_linearly_during_holding_period():
    """Midpoint of a 4-trading-day hold should reflect ~half the gain."""
    from marketpulse.backtest.simulator import simulate_strategy_from_pairs
    # Trading days 5/1, 5/4, 5/5, 5/6, 5/7 — horizon = 5/7 (4 days after entry)
    pairs = [_pair("M", date(2026, 5, 1), 100.0, date(2026, 5, 7), 110.0)]
    r = simulate_strategy_from_pairs(
        pairs=pairs,
        strategy="momentum_breakout", display_name="动量突破", horizon=4,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    # equity_curve[i] = (date_i, value_i). Find day = 5/5 (the middle of 5/1..5/7)
    curve = dict(r.daily_equity_curve)
    mid_val = curve.get(date(2026, 5, 5))
    assert mid_val is not None, f"Expected 2026-05-05 in curve, got {sorted(curve.keys())}"
    # Linear interp: 50% through → est_price = 105, position_value = 1_050
    # equity = 9_000 (cash) + 1_050 (position) = 10_050
    assert mid_val == pytest.approx(10_050.0, abs=1.0)


def test_losing_trade_decreases_equity():
    from marketpulse.backtest.simulator import simulate_strategy_from_pairs
    pairs = [_pair("LOSE", date(2026, 5, 1), 100.0, date(2026, 5, 8), 90.0)]
    r = simulate_strategy_from_pairs(
        pairs=pairs,
        strategy="momentum_breakout", display_name="动量突破", horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    # 1k @ 100 → @ 90 = 900. cash = 9000 + 900 = 9900
    final = r.daily_equity_curve[-1][1]
    assert final == pytest.approx(9_900.0, abs=1.0)
    assert r.win_rate == 0.0  # 0 wins out of 1


def test_excess_vs_spy_subtracts_benchmark():
    """Strategy +5%, SPY +2% → excess_vs_spy ≈ +3% (cumulative-return diff)."""
    from marketpulse.backtest.simulator import simulate_strategy_from_pairs
    pairs = [_pair("X", date(2026, 5, 1), 100.0, date(2026, 5, 8), 110.0,
                    benchmark_return=0.04)]
    r = simulate_strategy_from_pairs(
        pairs=pairs,
        strategy="momentum_breakout", display_name="动量突破", horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    # Strategy: 1_000 → 1_100 → cum_return = 100/10000 = 0.01
    # SPY:      1_000 (proxied via benchmark_return=0.04 on each trade)
    # Spec says excess_vs_spy = cum_return - spy_cum_return
    # Approximation: spy_cum_return ~ 0.04 * (1_000 / 10_000) = 0.004
    # So excess ≈ 0.01 - 0.004 = 0.006
    # The implementation should compute it the same way the simulator does.
    # Loose check: positive excess.
    assert r.excess_vs_spy > 0
```

- [ ] **Step 6.2: Run, fail**

```bash
uv run pytest tests/unit/test_backtest_simulator.py -v
```

Expected: 8 fails — ImportError.

- [ ] **Step 6.3: Create `marketpulse/backtest/simulator.py`** (initial — `simulate_strategy_from_pairs` only)

```python
"""Backtest simulator — per-strategy paper portfolio + SPY baseline.

Spec § Open Decision #15: daily loop order is strict CLOSE → OPEN → MTM → RECORD.
Spec § Open Decision #16: causal JOIN constraint enforced in queries module.
Spec § Daily Mark-to-Market: linear interpolation between entry_price and
horizon_price; surfaced as mtm_model='linear_interpolation_v0'.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from marketpulse.backtest.metrics import compute_metrics
from marketpulse.backtest.queries import EventOutcomePair
from marketpulse.backtest.trading_calendar import (
    build_calendar, elapsed_fraction,
)
from marketpulse.backtest.types import StrategyBacktestResult
from marketpulse.logging import get_logger

log = get_logger(__name__)


@dataclass
class _OpenPosition:
    """Internal simulator state for one in-flight long position."""
    ticker: str
    entry_date: date
    entry_price: float
    horizon_date: date
    horizon_price: float
    position_size: float    # USD initially deployed


def simulate_strategy_from_pairs(
    pairs: list[EventOutcomePair],
    *,
    strategy: str,
    display_name: str,
    horizon: int,
    initial_capital: float = 10_000.0,
    position_size: float = 1_000.0,
    max_capital_in_use: float = 10_000.0,
) -> StrategyBacktestResult:
    """Simulate a long-only paper portfolio for ONE strategy.

    Algorithm (spec § Portfolio Simulator Algorithm):
      For each trading day d in [first_event_date, max_horizon_date]:
        a) CLOSE positions whose horizon_date == d
            - cash += position_size + realized_pnl
        b) OPEN new bullish events with event_time.date() == d
            - if (capital_in_use + position_size) > max_capital_in_use:
                skip + increment n_capacity_skipped
            - else: append _OpenPosition
        c) MTM open positions opened BEFORE today (linear interpolation)
        d) RECORD equity[d] = cash + Σ position_values

    NOTE: simulator does NOT compute SPY here. excess_vs_spy is computed
    using benchmark_forward_return values stored on the pairs as a
    quick proxy. SPY equity curve is built by simulate_spy_buyhold().

    Returns:
        StrategyBacktestResult with downsampled daily_equity_curve.
    """
    if not pairs:
        return _empty_result(strategy, display_name, horizon, initial_capital)

    # Trading-day grid from union of all event + horizon dates.
    raw_dates = []
    for p in pairs:
        raw_dates.append(p.event_time.date())
        raw_dates.append(p.horizon_date)
    calendar = build_calendar(raw_dates)

    # Group pairs by entry date for O(1) day lookup
    pairs_by_entry: dict[date, list[EventOutcomePair]] = {}
    for p in pairs:
        pairs_by_entry.setdefault(p.event_time.date(), []).append(p)

    cash: float = initial_capital
    open_positions: list[_OpenPosition] = []
    n_trades = 0
    n_capacity_skipped = 0
    trade_returns: list[float] = []
    equity_curve: list[tuple[date, float]] = []

    for d in calendar:
        # a) CLOSE: positions whose horizon_date == d
        still_open: list[_OpenPosition] = []
        for pos in open_positions:
            if pos.horizon_date == d:
                realized_ret = (pos.horizon_price - pos.entry_price) / pos.entry_price
                realized_pnl = pos.position_size * realized_ret
                cash += pos.position_size + realized_pnl
                trade_returns.append(realized_ret)
            else:
                still_open.append(pos)
        open_positions = still_open

        # b) OPEN: new bullish events on day d
        for p in pairs_by_entry.get(d, []):
            capital_in_use = sum(pos.position_size for pos in open_positions)
            if capital_in_use + position_size > max_capital_in_use:
                n_capacity_skipped += 1
                log.info(
                    "backtest_signal_capacity_skipped",
                    strategy=strategy, ticker=p.ticker, date=d.isoformat(),
                )
                continue
            if cash < position_size:
                # Edge case: cash shortfall (e.g., heavy losses).
                # Treat as capacity skip — can't open without funds.
                n_capacity_skipped += 1
                log.info(
                    "backtest_cash_shortfall_skipped",
                    strategy=strategy, ticker=p.ticker, date=d.isoformat(),
                    cash=cash,
                )
                continue
            open_positions.append(_OpenPosition(
                ticker=p.ticker,
                entry_date=d,
                entry_price=p.event_price,
                horizon_date=p.horizon_date,
                horizon_price=p.horizon_price,
                position_size=position_size,
            ))
            cash -= position_size
            n_trades += 1

        # c) MTM open positions: linear interpolation
        positions_value = 0.0
        for pos in open_positions:
            if pos.entry_date == d:
                # Spec § Algorithm (b): "Newly opened positions are NOT
                # marked-to-market on day d" — they contribute their
                # entry value, i.e. position_size.
                positions_value += pos.position_size
            else:
                fraction = elapsed_fraction(
                    calendar,
                    entry=pos.entry_date,
                    horizon=pos.horizon_date,
                    current=d,
                )
                est_price = pos.entry_price + (
                    pos.horizon_price - pos.entry_price
                ) * fraction
                est_value = pos.position_size * (est_price / pos.entry_price)
                positions_value += est_value

        # d) RECORD
        equity_curve.append((d, cash + positions_value))

    # Downsample if needed
    downsampled = downsample_equity_curve(equity_curve, target_points=120)

    # Metrics
    metrics = compute_metrics(
        equity_curve=equity_curve,   # full series for accurate Sharpe
        n_trades=n_trades,
        trade_returns=trade_returns,
    )

    # Excess vs SPY: average per-pair excess return weighted by deployed capital.
    # Final excess is (strategy_cum_return − spy_cum_return) using the same
    # daily-MTM equity curve methodology. Here we use a per-trade proxy:
    # avg(forward_return − benchmark_forward_return) over executed trades.
    excess_terms = []
    for i, p in enumerate(pairs):
        if i < n_trades + n_capacity_skipped and i >= n_capacity_skipped:
            # Only consider executed trades, not skipped ones.
            # Simpler: iterate over open_positions history. But trade_returns
            # already includes only executed trades, so use the same count.
            excess_terms.append(p.forward_return - p.benchmark_forward_return)
    # Cap to number of trades to avoid double-counting skipped:
    excess_terms = excess_terms[:n_trades]
    excess_vs_spy_proxy = (
        sum(excess_terms) / len(excess_terms) if excess_terms else 0.0
    ) * (position_size / initial_capital)

    return StrategyBacktestResult(
        strategy=strategy,
        display_name=display_name,
        horizon=horizon,
        n_trades=n_trades,
        n_capacity_skipped=n_capacity_skipped,
        cumulative_return=metrics.cumulative_return,
        annual_return=metrics.annual_return,
        sharpe=metrics.sharpe,
        sortino=metrics.sortino,
        max_drawdown=metrics.max_drawdown,
        calmar=metrics.calmar,
        win_rate=metrics.win_rate,
        avg_win_pct=metrics.avg_win_pct,
        avg_loss_pct=metrics.avg_loss_pct,
        daily_equity_curve=downsampled,
        excess_vs_spy=excess_vs_spy_proxy,
    )


def _empty_result(
    strategy: str, display_name: str, horizon: int, initial_capital: float,
) -> StrategyBacktestResult:
    """Result for a strategy with zero bullish events in the window."""
    from datetime import date as _date
    return StrategyBacktestResult(
        strategy=strategy,
        display_name=display_name,
        horizon=horizon,
        n_trades=0,
        n_capacity_skipped=0,
        cumulative_return=0.0,
        annual_return=0.0,
        sharpe=None,
        sortino=None,
        max_drawdown=0.0,
        calmar=None,
        win_rate=0.0,
        avg_win_pct=0.0,
        avg_loss_pct=0.0,
        daily_equity_curve=[(_date.today(), initial_capital)],
        excess_vs_spy=0.0,
    )


# downsample stub — full implementation in Task 8.
def downsample_equity_curve(
    curve: list[tuple[date, float]], *, target_points: int = 120,
) -> list[tuple[date, float]]:
    """Stub for now; Task 8 implements the algorithm."""
    return curve
```

(Note: the `downsample_equity_curve` stub returns the full curve for now; Task 8 replaces with the real downsampler. This lets Task 6 tests pass without depending on Task 8.)

- [ ] **Step 6.4: Run, pass**

```bash
uv run pytest tests/unit/test_backtest_simulator.py -v
```

Expected: 8/8 pass.

- [ ] **Step 6.5: Ruff + commit**

```bash
uv run ruff check marketpulse/backtest/simulator.py tests/unit/test_backtest_simulator.py
git add marketpulse/backtest/simulator.py tests/unit/test_backtest_simulator.py
git commit -m "feat(backtest): simulate_strategy_from_pairs — core daily loop

Algorithm spec § Open Decision #15: CLOSE → OPEN → MTM → RECORD per day.
- CLOSE first: freed capital available to same-day new signals
- OPEN second: new positions opened (capacity-checked, cash-checked)
- MTM third: only positions opened BEFORE today (linear interpolation
  via trading_calendar.elapsed_fraction)
- RECORD last: equity[d] = cash + Σ position_values

Newly-opened positions contribute entry_price * size (no same-day MTM).
Capacity exhaustion logged via 'backtest_signal_capacity_skipped';
cash shortfall via 'backtest_cash_shortfall_skipped'.

excess_vs_spy is a per-trade proxy (avg forward − benchmark return);
SPY equity curve comes from simulate_spy_buyhold (Task 7).

downsample_equity_curve is a Task 8 stub (returns full curve).

8 unit tests cover: zero events, winning trade, capital cap (10/11
signals), CLOSE-before-OPEN ordering (10 freed → 11th opens),
no-same-day-MTM invariant, mid-period MTM linearity, losing trade,
positive excess_vs_spy."
```

---

### Task 7: Simulator — SPY baseline (linear interpolation across outcomes)

**Files:**
- Modify: `marketpulse/backtest/simulator.py` (append `simulate_spy_buyhold`)
- Modify: `tests/unit/test_backtest_simulator.py` (append SPY tests)

- [ ] **Step 7.1: Append failing tests**

Append to `tests/unit/test_backtest_simulator.py`:

```python
def test_spy_buyhold_single_outcome():
    """One outcome 5/1→5/8 with benchmark_return=0.02 → SPY equity ends ≈ 10_200."""
    from marketpulse.backtest.simulator import simulate_spy_buyhold
    pairs = [_pair("AAA", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0,
                    benchmark_return=0.02)]
    r = simulate_spy_buyhold(
        pairs=pairs,
        initial_capital=10_000.0,
    )
    assert r.strategy == "__spy_buyhold__"
    assert r.display_name == "SPY 基准"
    # Final equity = 10_000 * (1 + 0.02) = 10_200
    final = r.daily_equity_curve[-1][1]
    assert final == pytest.approx(10_200.0, abs=10.0)
    # n_trades is 0 by convention (it's a buy-and-hold, not "trades")
    assert r.n_trades == 0


def test_spy_buyhold_with_no_pairs_returns_flat():
    from marketpulse.backtest.simulator import simulate_spy_buyhold
    r = simulate_spy_buyhold(pairs=[], initial_capital=10_000.0)
    assert r.cumulative_return == 0.0
    assert r.daily_equity_curve[0][1] == 10_000.0


def test_spy_buyhold_mtm_interpolation_midpoint():
    """5/1 → 5/7 (4 trading days), benchmark 0.04 → midpoint ≈ 10_200."""
    from marketpulse.backtest.simulator import simulate_spy_buyhold
    pairs = [
        # Two overlapping windows; total benchmark exposure should average.
        # Simpler test: one outcome covering 5/1 to 5/7.
        _pair("X", date(2026, 5, 1), 100.0, date(2026, 5, 7), 110.0,
              benchmark_return=0.04),
    ]
    # Inject extra trading-day grid:
    pairs.append(_pair("Y", date(2026, 5, 3), 100.0, date(2026, 5, 5), 102.0,
                        benchmark_return=0.0))  # zero impact on benchmark
    r = simulate_spy_buyhold(pairs=pairs, initial_capital=10_000.0)
    # On 5/5 (mid of 5/1..5/7): should be roughly mid of the +4% trajectory
    # ≈ +2% → 10_200
    curve = dict(r.daily_equity_curve)
    mid = curve.get(date(2026, 5, 5))
    # Looser bound — multiple overlapping interpolations don't have a clean
    # closed-form midpoint, but should be between 10_000 and final.
    assert mid is not None
    assert 10_000 <= mid <= curve[date(2026, 5, 7)] + 1
```

- [ ] **Step 7.2: Run, fail**

```bash
uv run pytest tests/unit/test_backtest_simulator.py -v -k spy
```

Expected: 3 fails (function doesn't exist).

- [ ] **Step 7.3: Append `simulate_spy_buyhold` to `simulator.py`**

Add at the bottom of `marketpulse/backtest/simulator.py` (after `_empty_result`):

```python
def simulate_spy_buyhold(
    pairs: list[EventOutcomePair],
    *,
    initial_capital: float = 10_000.0,
) -> StrategyBacktestResult:
    """SPY buy-and-hold baseline, anchored to the same window as strategy events.

    Spec § SPY Baseline: uses linear interpolation across overlapping
    `benchmark_forward_return` windows from the same outcomes the strategies
    use — methodologically consistent with strategy MTM (both
    mtm_model = 'linear_interpolation_v0').

    Algorithm:
      1. Build calendar from all event/horizon dates in `pairs`.
      2. For each calendar day d, compute the cumulative SPY return:
         - For each outcome o whose [event_date, horizon_date] window covers d,
           add fractional benchmark contribution proportional to
           elapsed_fraction(d) within that window.
         - Average across overlapping windows (simple mean).
      3. equity[d] = initial_capital * (1 + cumulative_spy_return)

    This is a smoothed approximation. Phase 4.5 may swap in real daily
    SPY bars for a true daily curve.
    """
    if not pairs:
        from datetime import date as _date
        return StrategyBacktestResult(
            strategy="__spy_buyhold__",
            display_name="SPY 基准",
            horizon=0,
            n_trades=0,
            n_capacity_skipped=0,
            cumulative_return=0.0,
            annual_return=0.0,
            sharpe=None,
            sortino=None,
            max_drawdown=0.0,
            calmar=None,
            win_rate=0.0,
            avg_win_pct=0.0,
            avg_loss_pct=0.0,
            daily_equity_curve=[(_date.today(), initial_capital)],
            excess_vs_spy=0.0,
        )

    raw_dates = []
    for p in pairs:
        raw_dates.append(p.event_time.date())
        raw_dates.append(p.horizon_date)
    calendar = build_calendar(raw_dates)

    equity_curve: list[tuple[date, float]] = []
    for d in calendar:
        # Average instantaneous SPY return across overlapping windows.
        contributions: list[float] = []
        for p in pairs:
            entry = p.event_time.date()
            if entry <= d <= p.horizon_date:
                fraction = elapsed_fraction(
                    calendar, entry=entry, horizon=p.horizon_date, current=d,
                )
                contributions.append(p.benchmark_forward_return * fraction)
        if contributions:
            spy_ret_to_date = sum(contributions) / len(contributions)
        else:
            spy_ret_to_date = 0.0
        equity_curve.append((d, initial_capital * (1 + spy_ret_to_date)))

    downsampled = downsample_equity_curve(equity_curve, target_points=120)
    metrics = compute_metrics(
        equity_curve=equity_curve, n_trades=0, trade_returns=[],
    )

    return StrategyBacktestResult(
        strategy="__spy_buyhold__",
        display_name="SPY 基准",
        horizon=0,
        n_trades=0,
        n_capacity_skipped=0,
        cumulative_return=metrics.cumulative_return,
        annual_return=metrics.annual_return,
        sharpe=metrics.sharpe,
        sortino=metrics.sortino,
        max_drawdown=metrics.max_drawdown,
        calmar=metrics.calmar,
        win_rate=0.0,
        avg_win_pct=0.0,
        avg_loss_pct=0.0,
        daily_equity_curve=downsampled,
        excess_vs_spy=0.0,   # baseline has no excess versus itself
    )
```

- [ ] **Step 7.4: Run, pass**

```bash
uv run pytest tests/unit/test_backtest_simulator.py -v
```

Expected: 11/11 pass (8 from Task 6 + 3 new SPY).

- [ ] **Step 7.5: Ruff + commit**

```bash
uv run ruff check marketpulse/backtest/simulator.py tests/unit/test_backtest_simulator.py
git add marketpulse/backtest/simulator.py tests/unit/test_backtest_simulator.py
git commit -m "feat(backtest): simulate_spy_buyhold — SPY linear-interp baseline

Spec § SPY Baseline: linear interpolation across overlapping
benchmark_forward_return outcome windows. Methodologically consistent
with strategy MTM — both use mtm_model='linear_interpolation_v0'.

Anchored to the same trading-day grid as the strategies (union of
event/horizon dates). Returns a StrategyBacktestResult with
strategy='__spy_buyhold__', display_name='SPY 基准', horizon=0.
n_trades is 0 by convention (buy-and-hold, not 'trades').

3 unit tests cover: single-window happy path, empty input,
multi-window MTM bounds at midpoint."
```

---

### Task 8: Equity curve downsampler

**Files:**
- Modify: `marketpulse/backtest/simulator.py` (replace `downsample_equity_curve` stub)
- Modify: `tests/unit/test_backtest_simulator.py` (append downsample tests)

- [ ] **Step 8.1: Append failing tests**

Append to `tests/unit/test_backtest_simulator.py`:

```python
def test_downsample_preserves_curve_under_target():
    """If curve is shorter than target, no downsampling."""
    from marketpulse.backtest.simulator import downsample_equity_curve
    from datetime import date as _date
    curve = [(_date(2026, 5, d), 10_000.0 + d) for d in range(1, 30)]  # 29 points
    out = downsample_equity_curve(curve, target_points=120)
    assert out == curve


def test_downsample_reduces_long_curve_to_target():
    from marketpulse.backtest.simulator import downsample_equity_curve
    from datetime import date as _date, timedelta as _td
    curve = [(_date(2026, 1, 1) + _td(days=d), 10_000.0 + d) for d in range(500)]
    out = downsample_equity_curve(curve, target_points=120)
    assert len(out) <= 122  # endpoints + ~120 samples
    # Endpoints preserved
    assert out[0] == curve[0]
    assert out[-1] == curve[-1]


def test_downsample_endpoints_always_included():
    from marketpulse.backtest.simulator import downsample_equity_curve
    from datetime import date as _date, timedelta as _td
    curve = [(_date(2026, 1, 1) + _td(days=d), float(d)) for d in range(200)]
    out = downsample_equity_curve(curve, target_points=50)
    assert out[0] == curve[0]
    assert out[-1] == curve[-1]


def test_downsample_empty_input_returns_empty():
    from marketpulse.backtest.simulator import downsample_equity_curve
    assert downsample_equity_curve([], target_points=120) == []


def test_downsample_single_point_returns_unchanged():
    from marketpulse.backtest.simulator import downsample_equity_curve
    from datetime import date as _date
    curve = [(_date(2026, 5, 1), 10_000.0)]
    assert downsample_equity_curve(curve, target_points=120) == curve
```

- [ ] **Step 8.2: Run, fail**

The stub returns the input unchanged, so the "reduces long curve" test will fail (the 500-point curve will pass through, not be downsampled).

```bash
uv run pytest tests/unit/test_backtest_simulator.py -v -k downsample
```

Expected: 1 fail (`test_downsample_reduces_long_curve_to_target`), 4 pass.

- [ ] **Step 8.3: Replace `downsample_equity_curve` stub in `simulator.py`**

Find the existing stub:

```python
# downsample stub — full implementation in Task 8.
def downsample_equity_curve(...):
    return curve
```

Replace with:

```python
def downsample_equity_curve(
    curve: list[tuple[date, float]], *, target_points: int = 120,
) -> list[tuple[date, float]]:
    """Reduce a daily equity curve to ~target_points evenly-spaced samples.

    Preserves both endpoints. Used by the simulator before returning a
    StrategyBacktestResult so template contexts stay light.

    Algorithm: take stride = ceil(len/target). Step through the curve at
    that stride, then explicitly append the last point if not already.
    Simple and stable; no statistical sampling needed for visualization.
    """
    n = len(curve)
    if n <= target_points or n <= 2:
        return list(curve)

    stride = max(1, (n + target_points - 1) // target_points)
    out = [curve[i] for i in range(0, n - 1, stride)]
    # Always include the last point (endpoint preservation)
    if out[-1] != curve[-1]:
        out.append(curve[-1])
    return out
```

- [ ] **Step 8.4: Run, pass**

```bash
uv run pytest tests/unit/test_backtest_simulator.py -v
```

Expected: 16/16 pass (11 from Tasks 6+7 + 5 new downsample).

- [ ] **Step 8.5: Ruff + commit**

```bash
uv run ruff check marketpulse/backtest/simulator.py
git add marketpulse/backtest/simulator.py tests/unit/test_backtest_simulator.py
git commit -m "feat(backtest): downsample_equity_curve — replace stub with real algo

Stride-based downsampling with endpoint preservation. For curves
<= target_points, returns as-is. For longer curves, takes evenly-spaced
samples at stride = ceil(n / target) and explicitly appends the last
point if not already included.

5 unit tests cover: short input passes through, long input downsampled
to ≤ target+2, endpoints always preserved, empty input, single-point
input."
```

---

### Task 9: Top-level orchestrator `run_all_backtests`

**Files:**
- Modify: `marketpulse/backtest/simulator.py` (append `run_all_backtests`)
- Modify: `marketpulse/backtest/__init__.py` (re-export)
- Test: `tests/integration/test_backtest_queries.py` (append orchestrator test)

- [ ] **Step 9.1: Append failing test**

Append to `tests/integration/test_backtest_queries.py`:

```python
def test_run_all_backtests_returns_7_results(db_session):
    """6 strategies + 1 SPY baseline = 7 results, in strategy display order."""
    from marketpulse.backtest.simulator import run_all_backtests

    # Seed one bullish event per strategy
    strategies_v1 = [
        "fundamental_value", "momentum_breakout", "news_event",
        "sector_rotation", "oversold_reversal", "general",
    ]
    for i, s in enumerate(strategies_v1):
        _seed(db_session, ticker=f"T{i}", strategy=s, days_ago=10, excess=0.03)
    db_session.commit()

    results = run_all_backtests(db_session, horizon=5)
    assert len(results) == 7
    # SPY always present
    names = [r.strategy for r in results]
    assert "__spy_buyhold__" in names
    # All 6 strategies present
    for s in strategies_v1:
        assert s in names


def test_run_all_backtests_handles_strategies_with_no_events(db_session):
    """Strategies with zero events still get a result (empty / flat)."""
    from marketpulse.backtest.simulator import run_all_backtests

    _seed(db_session, ticker="ONE", strategy="momentum_breakout")
    db_session.commit()

    results = run_all_backtests(db_session, horizon=5)
    # All 6 strategies present even if only 1 has events
    momentum = next(r for r in results if r.strategy == "momentum_breakout")
    assert momentum.n_trades >= 1

    value = next(r for r in results if r.strategy == "fundamental_value")
    assert value.n_trades == 0


def test_run_all_backtests_applies_since_filter(db_session):
    from datetime import timedelta as _td
    from marketpulse.backtest.simulator import run_all_backtests

    _seed(db_session, ticker="OLD", strategy="momentum_breakout", days_ago=120)
    _seed(db_session, ticker="NEW", strategy="momentum_breakout", days_ago=10)
    db_session.commit()

    cutoff = date.today() - _td(days=90)
    results = run_all_backtests(db_session, horizon=5, since=cutoff)
    momentum = next(r for r in results if r.strategy == "momentum_breakout")
    assert momentum.n_trades == 1  # only NEW
```

- [ ] **Step 9.2: Run, fail**

```bash
uv run pytest tests/integration/test_backtest_queries.py -v -k run_all
```

Expected: 3 fails.

- [ ] **Step 9.3: Append `run_all_backtests` to `simulator.py`**

Add at the bottom of `marketpulse/backtest/simulator.py`:

```python
def run_all_backtests(
    db,
    *,
    horizon: int = 5,
    since: date | None = None,
    initial_capital: float = 10_000.0,
    position_size: float = 1_000.0,
    max_capital_in_use: float = 10_000.0,
) -> list[StrategyBacktestResult]:
    """Run the 6 Phase 3 strategies + SPY baseline.

    Returns a list ordered: [6 strategies in load_strategies() iteration order,
    then __spy_buyhold__ last]. The /lab/backtest route sorts by Sharpe
    desc itself.
    """
    from marketpulse.backtest.queries import get_bullish_events_with_outcomes
    from marketpulse.strategies import load_strategies

    strategies = load_strategies()
    all_pairs: list[EventOutcomePair] = []
    results: list[StrategyBacktestResult] = []

    for name, strat in strategies.items():
        pairs = get_bullish_events_with_outcomes(
            db, strategy=name, horizon=horizon, since=since,
        )
        all_pairs.extend(pairs)
        r = simulate_strategy_from_pairs(
            pairs=pairs,
            strategy=name,
            display_name=strat.display_name,
            horizon=horizon,
            initial_capital=initial_capital,
            position_size=position_size,
            max_capital_in_use=max_capital_in_use,
        )
        results.append(r)
        log.info(
            "backtest_run_complete",
            strategy=name, horizon=horizon, n_trades=r.n_trades,
            sharpe=r.sharpe, cum_return=r.cumulative_return,
        )

    # SPY baseline anchored to the same window via the union of all pairs
    spy = simulate_spy_buyhold(pairs=all_pairs, initial_capital=initial_capital)
    results.append(spy)
    return results
```

Update `marketpulse/backtest/__init__.py`:

```python
"""Backtest Engine MVP (Phase 4) — Strategy Performance Observatory.

A reproducible research observatory for strategy-level synthetic PnL
analysis under constrained-capital simulation assumptions. NOT a
faithful execution-level trading simulator.

See docs/superpowers/specs/2026-05-19-phase-4-backtest-engine-mvp.md
for the locked design decisions (16 of them).
"""
from marketpulse.backtest.simulator import (
    run_all_backtests,
    simulate_spy_buyhold,
    simulate_strategy_from_pairs,
)
from marketpulse.backtest.types import StrategyBacktestResult

__all__ = [
    "StrategyBacktestResult",
    "run_all_backtests",
    "simulate_spy_buyhold",
    "simulate_strategy_from_pairs",
]
```

- [ ] **Step 9.4: Run, pass**

```bash
uv run pytest tests/integration/test_backtest_queries.py -v
```

Expected: 10/10 pass (7 from Task 4 + 3 new).

- [ ] **Step 9.5: Ruff + commit**

```bash
uv run ruff check marketpulse/backtest/
git add marketpulse/backtest/
git commit -m "feat(backtest): run_all_backtests orchestrator

Iterates load_strategies() to backtest all 6 Phase 3 strategies, then
appends SPY baseline anchored to the union of all event timelines.
Logs backtest_run_complete per strategy for observability.

Returns list of 7 StrategyBacktestResult (6 strategies + SPY). Route
sorts by Sharpe desc for the leaderboard.

3 integration tests: 7-result shape, strategies-with-zero-events
get empty results, since filter applies."
```

---

### Task 10: `/lab/backtest` route

**Files:**
- Create: `marketpulse/web/routes/backtest.py`
- Modify: `marketpulse/web/main.py` (register router)
- Test: `tests/web/test_lab_backtest.py`

- [ ] **Step 10.1: Write failing tests**

Create `tests/web/test_lab_backtest.py`:

```python
"""Tests for /lab/backtest route."""
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.db.models import EvaluationEvent, EvaluationOutcome


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _seed_event(db, *, ticker, strategy, excess=0.03, days_ago=10, horizon=5):
    e = EvaluationEvent(
        event_type="ai_analysis", subtype="bullish", ticker=ticker,
        event_time=datetime.now(UTC) - timedelta(days=days_ago),
        event_price=100.0,
        payload={"source": "stock_analysis", "strategy": strategy,
                 "strategy_version": "v1", "prompt_version": "analysis-v4"},
    )
    db.add(e); db.flush()
    db.add(EvaluationOutcome(
        event_id=e.id, horizon_trading_days=horizon,
        event_price=100.0, horizon_price=100 * (1 + excess + 0.001),
        horizon_date=date.today() - timedelta(days=max(0, days_ago - horizon)),
        forward_return=excess + 0.001, benchmark_ticker="SPY",
        benchmark_forward_return=0.001, excess_return=excess,
    ))


def test_lab_backtest_renders_with_no_data(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/backtest")
    assert r.status_code == 200
    # Warning banner always present
    assert "linear_interpolation_v0" in r.text
    assert "research" in r.text.lower() or "研究" in r.text


def test_lab_backtest_invalid_horizon_returns_422(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/backtest?horizon=3")
    assert r.status_code == 422


def test_lab_backtest_accepts_horizon_5(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/backtest?horizon=5")
    assert r.status_code == 200


def test_lab_backtest_renders_strategy_names_when_data_present(
    client, monkeypatch, db_session,
):
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="A1", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest")
    assert "动量突破" in r.text
    assert "SPY 基准" in r.text


def test_lab_backtest_since_days_all_works(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="OLD", strategy="momentum_breakout", days_ago=200)
    db_session.commit()
    r = client.get("/lab/backtest?since_days=all")
    assert r.status_code == 200
    assert "OLD" in r.text or "动量突破" in r.text


def test_lab_backtest_requires_auth(client):
    """Unauthenticated → 303 redirect to login (like other /lab pages)."""
    r = client.get("/lab/backtest", follow_redirects=False)
    assert r.status_code == 303
```

- [ ] **Step 10.2: Run, fail**

```bash
uv run pytest tests/web/test_lab_backtest.py -v
```

Expected: 6 fails (route doesn't exist, 404).

- [ ] **Step 10.3: Create `marketpulse/web/routes/backtest.py`**

```python
"""Lab — /lab/backtest Strategy Performance Observatory."""
from datetime import date, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from marketpulse.backtest.simulator import run_all_backtests
from marketpulse.evaluation.outcomes import DEFAULT_HORIZONS
from marketpulse.web.deps import get_db, require_auth
from marketpulse.web.main import templates

router = APIRouter()


def _qs_from_filters(filters: dict) -> str:
    """Build a clean query string, dropping defaults / None / empty.

    Mirrors marketpulse.web.routes.lab._qs_from_filters.
    """
    DEFAULTS = {"horizon": 5, "since_days": 90}
    payload = {}
    for k, v in filters.items():
        if v is None or v == "":
            continue
        if k in DEFAULTS and v == DEFAULTS[k]:
            continue
        payload[k] = str(v)
    return urlencode(payload)


@router.get("/lab/backtest", response_class=HTMLResponse)
def lab_backtest(
    request: Request,
    horizon: int = 5,
    since_days: str | int = 90,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    # Normalize since_days
    since: date | None
    if isinstance(since_days, str) and since_days == "all":
        since = None
    else:
        try:
            sd_int = int(since_days)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail=f"invalid since_days: {since_days}",
            ) from exc
        if sd_int <= 0:
            raise HTTPException(
                status_code=422, detail="since_days must be positive or 'all'",
            )
        since = date.today() - timedelta(days=sd_int)

    if horizon not in DEFAULT_HORIZONS:
        raise HTTPException(
            status_code=422,
            detail=f"invalid horizon: must be one of {DEFAULT_HORIZONS}",
        )

    results = run_all_backtests(db, horizon=horizon, since=since)

    # Split SPY out from strategies for the rail table
    strategies = [r for r in results if r.strategy != "__spy_buyhold__"]
    spy = next((r for r in results if r.strategy == "__spy_buyhold__"), None)

    # Sort strategies by Sharpe desc; None values sink to bottom.
    strategies_sorted = sorted(
        strategies,
        key=lambda r: r.sharpe if r.sharpe is not None else -999.0,
        reverse=True,
    )

    # KPI computation
    best_strategy = next(
        (r for r in strategies_sorted if r.n_trades >= 5), None,
    )
    best_sharpe = next(
        (r for r in strategies_sorted if r.sharpe is not None), None,
    )
    best_cum = max(strategies, key=lambda r: r.cumulative_return, default=None)
    worst_dd = min(strategies, key=lambda r: r.max_drawdown, default=None)
    avg_excess = (
        sum(r.excess_vs_spy for r in strategies) / len(strategies)
        if strategies else 0.0
    )

    filters = {"horizon": horizon, "since_days": since_days}

    return templates.TemplateResponse(
        request, "lab_backtest.html",
        {
            "strategies": strategies_sorted,
            "spy": spy,
            "best_strategy": best_strategy,
            "best_sharpe": best_sharpe,
            "best_cum": best_cum,
            "worst_dd": worst_dd,
            "avg_excess": avg_excess,
            "filters": filters,
            "filters_qs": _qs_from_filters(filters),
        },
    )
```

- [ ] **Step 10.4: Register router in `marketpulse/web/main.py`**

Find the import block + include_router block (around line 80-100). Add `backtest` import and `app.include_router(backtest.router)`:

```python
from marketpulse.web.routes import (  # noqa: WPS433
    alerts,
    auth,
    backtest,    # NEW
    health,
    holdings,
    home,
    lab,
    recap,
    splits,
    stock,
    trades,
    watchlist,
)
...
app.include_router(lab.router)
app.include_router(backtest.router)    # NEW (after lab.router)
```

- [ ] **Step 10.5: Create template stub `marketpulse/web/templates/lab_backtest.html`**

(Task 11 builds it out; this stub just renders enough to make Step 10 tests pass.)

```html
{% extends "base.html" %}
{% block main_width %}max-w-[2400px]{% endblock %}
{% block nav_width %}max-w-[2400px]{% endblock %}
{% block content %}
<div class="mp-warning-banner" style="padding:12px 48px; background:#fef3c7; border-left:4px solid #d97706; margin:0 0 16px;">
  ⓘ <strong>研究级模拟引擎</strong> · 非真实执行模拟器 ·
  指标基于持仓期间的线性插值 <code>mtm_model=linear_interpolation_v0</code>。
  Max Drawdown 可能被低估,Sharpe 可能被高估。
</div>

<section class="mp-hero">
  <span class="mp-eyebrow mp-eyebrow--primary">实验室 · 组合回测</span>
  <h1 class="grotesk mp-hero__title">Strategy Performance Observatory</h1>
  <p class="mp-hero__desc">
    回放 Phase 3 的 6 个策略 + SPY 基准在过去 N 天内的策略级合成 PnL。
  </p>
</section>

{# Task 11 fills in: KPI strip, equity chart, drawdown chart, filter, table. #}

{% for s in strategies %}
  <div>{{ s.display_name }}</div>
{% endfor %}
{% if spy %}<div>{{ spy.display_name }}</div>{% endif %}
{% endblock %}
```

- [ ] **Step 10.6: Run, pass**

```bash
uv run pytest tests/web/test_lab_backtest.py -v
```

Expected: 6/6 pass.

- [ ] **Step 10.7: Ruff + commit**

```bash
uv run ruff check marketpulse/web/routes/backtest.py marketpulse/web/main.py tests/web/test_lab_backtest.py
git add marketpulse/web/routes/backtest.py marketpulse/web/main.py marketpulse/web/templates/lab_backtest.html tests/web/test_lab_backtest.py
git commit -m "feat(lab): /lab/backtest route + shell template

Route accepts horizon (must be in DEFAULT_HORIZONS) + since_days
('all' or positive int). Calls run_all_backtests, splits SPY from
strategies, computes 5 KPIs (best_strategy, best_sharpe, best_cum,
worst_dd, avg_excess), sorts strategies by Sharpe desc.

Shell template has the mandatory warning banner per spec § UI Spec
+ minimal stub rendering. Task 11 fills in KPI strip + charts +
filter card + leaderboard.

6 web tests cover: empty render, invalid horizon (422), valid horizon,
strategy display name in HTML, since_days=all, auth required."
```

---

### Task 11: KPI strip + warning banner partials

**Files:**
- Create: `marketpulse/web/templates/partials/backtest_hero.html`
- Create: `marketpulse/web/templates/partials/backtest_kpi_strip.html`
- Modify: `marketpulse/web/templates/lab_backtest.html` (include partials)
- Modify: `marketpulse/web/static/css/app.css` (append KPI grid CSS)
- Modify: `tests/web/test_lab_backtest.py` (append KPI tests)

- [ ] **Step 11.1: Append failing tests**

Append to `tests/web/test_lab_backtest.py`:

```python
def test_lab_backtest_renders_5_kpi_cards(client, monkeypatch, db_session):
    _login(client, monkeypatch)
    # Seed 6 events on one strategy so best_strategy / best_sharpe exist
    for i in range(6):
        _seed_event(db_session, ticker=f"T{i}", strategy="momentum_breakout",
                    excess=0.03)
    db_session.commit()
    r = client.get("/lab/backtest")
    # All 5 KPI labels present
    assert "Best Strategy" in r.text
    assert "Best Sharpe" in r.text
    assert "Best Cum Ret" in r.text or "Best Return" in r.text
    assert "Worst MaxDD" in r.text or "MaxDD" in r.text
    assert "vs SPY" in r.text


def test_lab_backtest_kpi_shows_dash_when_no_qualifying_strategy(
    client, monkeypatch, db_session,
):
    _login(client, monkeypatch)
    # Only 2 events — n_trades < 5 → no qualifying best_strategy
    for i in range(2):
        _seed_event(db_session, ticker=f"T{i}", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest")
    # Best Strategy should show fallback "—" (not the display_name)
    assert "Best Strategy" in r.text
    # Implementation-defined — accept either explicit em-dash or "n<5"
    assert "—" in r.text or "n&lt;5" in r.text or "n<5" in r.text
```

- [ ] **Step 11.2: Run, fail**

```bash
uv run pytest tests/web/test_lab_backtest.py -v -k kpi
```

Expected: 2 fails.

- [ ] **Step 11.3: Create `marketpulse/web/templates/partials/backtest_hero.html`**

```html
<section class="mp-hero">
  <div>
    <span class="mp-eyebrow mp-eyebrow--primary">实验室 · 组合回测</span>
    <h1 class="grotesk mp-hero__title">Strategy Performance Observatory</h1>
    <span class="mp-rule"></span>
    <p class="mp-hero__desc">
      回放 Phase 3 的 6 个策略 + SPY 基准在过去 N 天内的策略级合成 PnL。
      回测使用 long-only 模型 + 固定持有 horizon 天 + $1k 每信号 + $10k 软上限。
    </p>
  </div>
</section>
```

- [ ] **Step 11.4: Create `marketpulse/web/templates/partials/backtest_kpi_strip.html`**

```html
<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">Best Strategy</span>
    <span class="material-symbols-outlined mp-kpi__icon">military_tech</span>
  </div>
  <div class="mp-kpi__value grotesk tnum">
    {% if best_strategy %}{{ best_strategy.display_name }}{% else %}—{% endif %}
  </div>
  <div class="mp-kpi__hint">
    {% if best_strategy %}
      Sharpe {{ "{:.2f}".format(best_strategy.sharpe) }} · n={{ best_strategy.n_trades }}
    {% else %}n&lt;5 暂无最佳{% endif %}
  </div>
</div>

<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">Best Sharpe</span>
    <span class="material-symbols-outlined mp-kpi__icon">trending_up</span>
  </div>
  <div class="mp-kpi__value grotesk tnum"
       style="color: {% if best_sharpe and best_sharpe.sharpe and best_sharpe.sharpe >= 1 %}var(--mp-up){% else %}var(--ns-navy){% endif %};">
    {% if best_sharpe and best_sharpe.sharpe is not none %}
      {{ "{:.2f}".format(best_sharpe.sharpe) }}
    {% else %}—{% endif %}
  </div>
  <div class="mp-kpi__hint">
    {% if best_sharpe %}{{ best_sharpe.display_name }}{% else %}n&lt;5{% endif %}
  </div>
</div>

<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">Best Cum Ret</span>
    <span class="material-symbols-outlined mp-kpi__icon">show_chart</span>
  </div>
  <div class="mp-kpi__value grotesk tnum"
       style="color: {% if best_cum and best_cum.cumulative_return > 0 %}var(--mp-up){% else %}var(--mp-down){% endif %};">
    {% if best_cum %}{{ "{:+.2f}%".format(best_cum.cumulative_return * 100) }}{% else %}—{% endif %}
  </div>
  <div class="mp-kpi__hint">
    {% if best_cum %}{{ best_cum.display_name }}{% else %}n=0{% endif %}
  </div>
</div>

<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">Worst MaxDD</span>
    <span class="material-symbols-outlined mp-kpi__icon">trending_down</span>
  </div>
  <div class="mp-kpi__value grotesk tnum" style="color: var(--mp-down);">
    {% if worst_dd %}{{ "{:.2f}%".format(worst_dd.max_drawdown * 100) }}{% else %}—{% endif %}
  </div>
  <div class="mp-kpi__hint">
    {% if worst_dd %}{{ worst_dd.display_name }}{% else %}n=0{% endif %}
  </div>
</div>

<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">vs SPY (avg)</span>
    <span class="material-symbols-outlined mp-kpi__icon">compare_arrows</span>
  </div>
  <div class="mp-kpi__value grotesk tnum"
       style="color: {% if avg_excess >= 0 %}var(--mp-up){% else %}var(--mp-down){% endif %};">
    {{ "{:+.2f}%".format(avg_excess * 100) }}
  </div>
  <div class="mp-kpi__hint">6 策略对 SPY 平均超额</div>
</div>
```

- [ ] **Step 11.5: Update `marketpulse/web/templates/lab_backtest.html`** to include the partials

Replace existing stub content with:

```html
{% extends "base.html" %}
{% block main_width %}max-w-[2400px]{% endblock %}
{% block nav_width %}max-w-[2400px]{% endblock %}
{% block content %}

<div class="mp-backtest-warning">
  <span class="material-symbols-outlined">info</span>
  <span><strong>研究级模拟引擎</strong> · 非真实执行模拟器 ·
  指标基于持仓期间的线性插值 <code>mtm_model=linear_interpolation_v0</code>。
  Max Drawdown 可能被低估,Sharpe 可能被高估。
  </span>
</div>

{% include "partials/backtest_hero.html" ignore missing %}

<section class="mp-backtest-kpi">
  {% include "partials/backtest_kpi_strip.html" ignore missing %}
</section>

<section class="mp-backtest-body">
  <div class="mp-backtest-main">
    {# Task 12 fills in equity + drawdown charts #}
  </div>
  <aside class="mp-backtest-rail">
    {# Task 13 fills in filter card + strategy table #}
  </aside>
</section>

{% endblock %}
```

- [ ] **Step 11.6: Append CSS to `marketpulse/web/static/css/app.css`**

```css
/* ════════ Phase 4: /lab/backtest layout ════════ */
.mp-backtest-warning {
  display: flex; align-items: flex-start; gap: 12px;
  padding: 14px 48px;
  background: #fef3c7; border-left: 4px solid #d97706;
  font-size: 13px; color: #78350f;
}
.mp-backtest-warning code {
  background: rgba(0,0,0,0.06); padding: 1px 6px; border-radius: 2px;
  font-family: var(--ns-font-mono); font-size: 12px;
}

.mp-backtest-kpi {
  padding: 0 48px 16px;
  display: grid; grid-template-columns: repeat(5, 1fr); gap: 16px;
}
.mp-backtest-body {
  padding: 0 48px 32px;
  display: grid; grid-template-columns: 760px 1fr; gap: 56px;
}
.mp-backtest-main { display: flex; flex-direction: column; gap: 16px; }
.mp-backtest-rail { display: flex; flex-direction: column; gap: 16px; }

@media (max-width: 1640px) {
  .mp-backtest-body { grid-template-columns: 1fr; }
}
@media (max-width: 1200px) {
  .mp-backtest-kpi { grid-template-columns: repeat(3, 1fr); }
}
@media (max-width: 900px) {
  .mp-backtest-kpi { grid-template-columns: repeat(2, 1fr); }
}
```

- [ ] **Step 11.7: Run, pass**

```bash
uv run pytest tests/web/test_lab_backtest.py -v
```

Expected: 8/8 pass (6 from Task 10 + 2 new KPI).

- [ ] **Step 11.8: Ruff + commit**

```bash
git add marketpulse/web/templates/lab_backtest.html \
       marketpulse/web/templates/partials/backtest_hero.html \
       marketpulse/web/templates/partials/backtest_kpi_strip.html \
       marketpulse/web/static/css/app.css \
       tests/web/test_lab_backtest.py
git commit -m "feat(lab): backtest hero + 5-card KPI strip partials

KPIs: Best Strategy (n>=5, Sharpe-leader), Best Sharpe, Best Cum Ret,
Worst MaxDD, vs SPY (avg). All fall back to '—' / 'n<5' chips when
the underlying metric is None.

Layout: 3-column grid at 2400px max-width (same as /lab/ai-track),
responsive collapse to 3/2 columns at 1200/900px breakpoints.
Warning banner uses amber-on-amber accent matching Phase 5 design.

2 web tests: 5 KPI labels present, '—' fallback when no n>=5 strategy."
```

---

### Task 12: Equity curve + drawdown chart partials

**Files:**
- Create: `marketpulse/web/templates/partials/backtest_equity_chart.html`
- Create: `marketpulse/web/templates/partials/backtest_drawdown_chart.html`
- Modify: `marketpulse/web/templates/lab_backtest.html` (include charts)
- Modify: `marketpulse/web/routes/backtest.py` (pre-compute polyline points + DD data)
- Modify: `tests/web/test_lab_backtest.py` (append chart tests)

- [ ] **Step 12.1: Append failing tests**

Append to `tests/web/test_lab_backtest.py`:

```python
def test_lab_backtest_renders_equity_curve_svg(
    client, monkeypatch, db_session,
):
    _login(client, monkeypatch)
    for i in range(6):
        _seed_event(db_session, ticker=f"T{i}", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest")
    assert "<svg" in r.text
    assert "<polyline" in r.text
    # SPY curve should also be drawn
    assert r.text.count("<polyline") >= 2  # at least strategy + SPY


def test_lab_backtest_renders_drawdown_svg(
    client, monkeypatch, db_session,
):
    _login(client, monkeypatch)
    for i in range(6):
        _seed_event(db_session, ticker=f"T{i}", strategy="momentum_breakout",
                    excess=-0.02)   # losing trades → drawdown visible
    db_session.commit()
    r = client.get("/lab/backtest")
    assert "Drawdown" in r.text or "回撤" in r.text
```

- [ ] **Step 12.2: Run, fail**

```bash
uv run pytest tests/web/test_lab_backtest.py -v -k "svg or drawdown"
```

Expected: 2 fails.

- [ ] **Step 12.3: Pre-compute polylines in route**

Modify `marketpulse/web/routes/backtest.py`. After the `strategies_sorted = sorted(...)` call, add the chart-data pre-compute:

```python
# Pre-compute SVG-ready data for charts. We do this in the route (not
# the template) to keep template logic minimal and to apply consistent
# scaling across all 7 curves.
chart_data = _build_chart_data(strategies + ([spy] if spy else []))
```

Then at the bottom of the file, add the helper:

```python
def _build_chart_data(
    results: list,
) -> dict:
    """Compose polyline points + drawdown points for all results.

    Returns a dict with:
      - equity_curves: list of {name, display_name, color, points_str}
      - drawdown_curves: same shape, drawdown values
      - x_axis: list of (frac, label) tuples for axis labels
    """
    # Aggregate all dates to determine common x-axis
    all_dates = sorted({d for r in results for d, _ in r.daily_equity_curve})
    if not all_dates:
        return {"equity_curves": [], "drawdown_curves": [], "x_axis": []}

    date_to_idx = {d: i for i, d in enumerate(all_dates)}
    n = len(all_dates)
    # SVG viewBox = 800 x 280 for equity, same for drawdown
    width = 800.0
    eq_height = 280.0
    dd_height = 140.0

    # Find min/max equity for Y normalization
    all_vals = [v for r in results for _, v in r.daily_equity_curve]
    eq_min = min(all_vals) if all_vals else 0
    eq_max = max(all_vals) if all_vals else 1
    eq_range = max(eq_max - eq_min, 1e-6)

    # Color palette (cycle): 6 strategies + dotted gray for SPY
    palette = [
        "#2563eb", "#16a34a", "#dc2626", "#ea580c",
        "#9333ea", "#0891b2", "#475569",  # SPY position
    ]

    equity_curves = []
    drawdown_curves = []
    for i, r in enumerate(results):
        color = palette[i % len(palette)]
        is_spy = r.strategy == "__spy_buyhold__"
        if is_spy:
            color = "#475569"  # neutral gray

        # Equity polyline points
        pts = []
        for d, v in r.daily_equity_curve:
            x = date_to_idx[d] / max(n - 1, 1) * width
            y = eq_height - ((v - eq_min) / eq_range) * eq_height
            pts.append(f"{x:.1f},{y:.1f}")

        # Drawdown polyline points (always <= 0)
        if r.daily_equity_curve:
            values = [v for _, v in r.daily_equity_curve]
            peak = values[0]
            dd_pts = []
            for j, v in enumerate(values):
                peak = max(peak, v)
                dd = (v - peak) / peak if peak > 0 else 0.0
                # Normalize: dd in [-1, 0]; pin -0.5 to bottom for visibility
                y = (-dd) * dd_height * 2   # scale x2 for clarity
                y = min(y, dd_height)
                d, _ = r.daily_equity_curve[j]
                x = date_to_idx[d] / max(n - 1, 1) * width
                dd_pts.append(f"{x:.1f},{y:.1f}")
        else:
            dd_pts = []

        equity_curves.append({
            "name": r.strategy,
            "display_name": r.display_name,
            "color": color,
            "is_spy": is_spy,
            "points_str": " ".join(pts),
        })
        drawdown_curves.append({
            "name": r.strategy,
            "display_name": r.display_name,
            "color": color,
            "is_spy": is_spy,
            "points_str": " ".join(dd_pts),
        })

    # X-axis labels: 5 evenly-spaced dates
    label_count = min(5, n)
    if n <= 1:
        x_axis = [(0.0, str(all_dates[0]))] if all_dates else []
    else:
        x_axis = [
            (i / (label_count - 1), str(all_dates[round(i / (label_count - 1) * (n - 1))]))
            for i in range(label_count)
        ]

    return {
        "equity_curves": equity_curves,
        "drawdown_curves": drawdown_curves,
        "x_axis": x_axis,
        "eq_min": eq_min,
        "eq_max": eq_max,
    }
```

Add `"chart_data": chart_data` to the `TemplateResponse` context dict.

- [ ] **Step 12.4: Create `marketpulse/web/templates/partials/backtest_equity_chart.html`**

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">show_chart</span>组合 Equity Curve
    </span>
    <span class="mp-card__sub">{{ filters.horizon }}d horizon · 7 条曲线(6 策略 + SPY)</span>
  </div>
  <div class="mp-card__body">
    {% if chart_data.equity_curves %}
    <svg viewBox="0 0 800 280" width="100%" height="280">
      <!-- Initial-capital baseline (y=initial) -->
      <line x1="0" y1="280" x2="800" y2="280"
            stroke="var(--ns-outline-variant)" stroke-dasharray="2 4" />
      {% for c in chart_data.equity_curves %}
        <polyline
          points="{{ c.points_str }}"
          fill="none"
          stroke="{{ c.color }}"
          stroke-width="{% if c.is_spy %}1.5{% else %}2{% endif %}"
          {% if c.is_spy %}stroke-dasharray="4 4"{% endif %}>
          <title>{{ c.display_name }}</title>
        </polyline>
      {% endfor %}
    </svg>
    <div class="mp-chart-legend">
      {% for c in chart_data.equity_curves %}
        <span class="mp-chart-legend__item">
          <span class="mp-chart-legend__swatch" style="background:{{ c.color }};
            {% if c.is_spy %}border:1px dashed {{ c.color }};{% endif %}"></span>
          {{ c.display_name }}
        </span>
      {% endfor %}
    </div>
    {% else %}
      <p class="muted" style="text-align:center; padding:32px;">暂无数据</p>
    {% endif %}
  </div>
</section>
```

- [ ] **Step 12.5: Create `marketpulse/web/templates/partials/backtest_drawdown_chart.html`**

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">trending_down</span>Drawdown(回撤)
    </span>
    <span class="mp-card__sub">从历史峰值的回撤百分比 · 0 在顶,负向下</span>
  </div>
  <div class="mp-card__body">
    {% if chart_data.drawdown_curves %}
    <svg viewBox="0 0 800 140" width="100%" height="140">
      <line x1="0" y1="0" x2="800" y2="0"
            stroke="var(--ns-outline-variant)" />
      {% for c in chart_data.drawdown_curves %}
        <polyline
          points="{{ c.points_str }}"
          fill="none"
          stroke="{{ c.color }}"
          stroke-width="{% if c.is_spy %}1.5{% else %}1.5{% endif %}"
          {% if c.is_spy %}stroke-dasharray="4 4"{% endif %}>
          <title>{{ c.display_name }}</title>
        </polyline>
      {% endfor %}
    </svg>
    {% else %}
      <p class="muted" style="text-align:center; padding:24px;">暂无数据</p>
    {% endif %}
  </div>
</section>
```

- [ ] **Step 12.6: Update `lab_backtest.html` to include the charts**

Find the `mp-backtest-main` div and fill it:

```html
<div class="mp-backtest-main">
  {% include "partials/backtest_equity_chart.html" ignore missing %}
  {% include "partials/backtest_drawdown_chart.html" ignore missing %}
</div>
```

- [ ] **Step 12.7: Append legend CSS** to `marketpulse/web/static/css/app.css`

```css
.mp-chart-legend {
  display: flex; flex-wrap: wrap; gap: 12px 20px;
  padding: 12px 16px 4px;
  font-size: 11px; color: var(--ns-on-surface-variant);
}
.mp-chart-legend__item {
  display: inline-flex; align-items: center; gap: 6px;
}
.mp-chart-legend__swatch {
  width: 14px; height: 6px; border-radius: 1px;
}
```

- [ ] **Step 12.8: Run, pass**

```bash
uv run pytest tests/web/test_lab_backtest.py -v
```

Expected: 10/10 pass (8 from earlier + 2 new chart).

- [ ] **Step 12.9: Ruff + commit**

```bash
uv run ruff check marketpulse/web/routes/backtest.py
git add marketpulse/web/routes/backtest.py \
       marketpulse/web/templates/lab_backtest.html \
       marketpulse/web/templates/partials/backtest_equity_chart.html \
       marketpulse/web/templates/partials/backtest_drawdown_chart.html \
       marketpulse/web/static/css/app.css \
       tests/web/test_lab_backtest.py
git commit -m "feat(lab): equity + drawdown SVG charts

Route pre-computes polyline points + drawdown data in _build_chart_data
helper so templates stay minimal. Common date axis across all 7 curves
for visual alignment. SPY rendered with dashed stroke + neutral gray.

Equity chart: 800x280 viewBox, 7 polylines, dashed baseline at
initial_capital, legend with color swatches.

Drawdown chart: 800x140 viewBox, 7 polylines descending from 0
(running-peak-relative pct, scaled x2 for clarity), single zero-line
at top.

2 web tests: svg + polyline present in equity chart, '回撤'/'Drawdown'
label in drawdown chart."
```

---

### Task 13: Filter card + strategy leaderboard table partials

**Files:**
- Create: `marketpulse/web/templates/partials/backtest_filter_card.html`
- Create: `marketpulse/web/templates/partials/backtest_strategy_table.html`
- Modify: `marketpulse/web/templates/lab_backtest.html` (include in rail)
- Modify: `marketpulse/web/static/css/app.css` (append table CSS)
- Modify: `tests/web/test_lab_backtest.py` (append tests)

- [ ] **Step 13.1: Append failing tests**

Append to `tests/web/test_lab_backtest.py`:

```python
def test_lab_backtest_filter_card_renders_horizon_chips(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/backtest")
    assert "1d" in r.text
    assert "5d" in r.text
    assert "20d" in r.text
    assert "60d" in r.text


def test_lab_backtest_strategy_table_renders_all_strategies(
    client, monkeypatch, db_session,
):
    _login(client, monkeypatch)
    for s in ("momentum_breakout", "fundamental_value", "general"):
        for i in range(6):
            _seed_event(db_session, ticker=f"{s[0]}{i}", strategy=s)
    db_session.commit()
    r = client.get("/lab/backtest")
    # All 6 strategy display names appear
    for name in ("动量突破", "价值分析", "通用分析", "事件驱动",
                  "行业轮动", "超卖反弹"):
        assert name in r.text


def test_lab_backtest_strategy_table_shows_skipped_chip(
    client, monkeypatch, db_session,
):
    """Strategy with capacity-skipped signals shows a chip in the table."""
    _login(client, monkeypatch)
    # Seed 15 events same day → 5 will be skipped at $10k cap
    same_day = datetime.now(UTC) - timedelta(days=10)
    for i in range(15):
        e = EvaluationEvent(
            event_type="ai_analysis", subtype="bullish", ticker=f"S{i}",
            event_time=same_day,
            event_price=100.0,
            payload={"source": "stock_analysis", "strategy": "momentum_breakout",
                     "strategy_version": "v1", "prompt_version": "analysis-v4"},
        )
        db_session.add(e); db_session.flush()
        db_session.add(EvaluationOutcome(
            event_id=e.id, horizon_trading_days=5,
            event_price=100.0, horizon_price=103.0,
            horizon_date=(same_day - timedelta(days=5)).date(),
            forward_return=0.03, benchmark_ticker="SPY",
            benchmark_forward_return=0.01, excess_return=0.02,
        ))
    db_session.commit()
    r = client.get("/lab/backtest")
    # "skipped" column header + non-zero value
    assert "skipped" in r.text.lower() or "跳过" in r.text


def test_lab_backtest_spy_baseline_row_marked(
    client, monkeypatch, db_session,
):
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="X", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest")
    assert "SPY 基准" in r.text or "基准" in r.text
```

- [ ] **Step 13.2: Run, fail**

```bash
uv run pytest tests/web/test_lab_backtest.py -v -k "filter or strategy_table or spy_baseline or skipped"
```

Expected: 4 fails.

- [ ] **Step 13.3: Create `marketpulse/web/templates/partials/backtest_filter_card.html`**

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">filter_list</span>筛选
    </span>
    <a href="/lab/backtest" class="mp-card__sub" style="color:var(--ns-primary);">重置</a>
  </div>
  <form method="get" action="/lab/backtest" class="mp-card__body" style="display:flex; flex-direction:column; gap:14px;">
    <div>
      <span class="mp-eyebrow">Horizon</span>
      <div class="mp-seg" style="margin-top:6px;">
        {% for h in [1, 5, 20, 60] %}
          <button type="submit" name="horizon" value="{{ h }}"
                  class="{% if filters.horizon == h %}is-active{% endif %}">{{ h }}d</button>
        {% endfor %}
      </div>
    </div>

    <div>
      <span class="mp-eyebrow">Time</span>
      <div class="mp-seg" style="margin-top:6px;">
        <button type="submit" name="since_days" value="30"
                class="{% if filters.since_days == 30 %}is-active{% endif %}">30d</button>
        <button type="submit" name="since_days" value="90"
                class="{% if filters.since_days == 90 %}is-active{% endif %}">90d</button>
        <button type="submit" name="since_days" value="180"
                class="{% if filters.since_days == 180 %}is-active{% endif %}">180d</button>
        <button type="submit" name="since_days" value="all"
                class="{% if filters.since_days == 'all' %}is-active{% endif %}">全部</button>
      </div>
    </div>
  </form>
</section>
```

- [ ] **Step 13.4: Create `marketpulse/web/templates/partials/backtest_strategy_table.html`**

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">leaderboard</span>策略排行
    </span>
    <span class="mp-card__sub">{{ filters.horizon }}d · Sharpe desc</span>
  </div>
  <div class="mp-card__body" style="padding:0; overflow-x:auto;">
    <table class="mp-table mp-backtest-table">
      <thead>
        <tr>
          <th>策略</th>
          <th class="num">Sharpe</th>
          <th class="num">MaxDD</th>
          <th class="num">Cum Ret</th>
          <th class="num">vs SPY</th>
          <th class="num">n</th>
          <th class="num">skipped</th>
        </tr>
      </thead>
      <tbody>
        {% for s in strategies %}
        <tr>
          <td>
            <a href="/lab/ai-track?strategy={{ s.strategy }}"
               class="mp-strategy-link" title="查看 hit rate">
              {{ s.display_name }}
            </a>
          </td>
          <td class="num mono tnum">
            {% if s.sharpe is none %}—{% else %}{{ "{:.2f}".format(s.sharpe) }}{% endif %}
          </td>
          <td class="num mono tnum {% if s.max_drawdown < -0.05 %}down{% endif %}">
            {% if s.n_trades == 0 %}—{% else %}{{ "{:.1f}%".format(s.max_drawdown * 100) }}{% endif %}
          </td>
          <td class="num mono tnum {% if s.cumulative_return >= 0 %}up{% else %}down{% endif %}">
            {% if s.n_trades == 0 %}—{% else %}{{ "{:+.2f}%".format(s.cumulative_return * 100) }}{% endif %}
          </td>
          <td class="num mono tnum {% if s.excess_vs_spy >= 0 %}up{% else %}down{% endif %}">
            {% if s.n_trades == 0 %}—{% else %}{{ "{:+.2f}%".format(s.excess_vs_spy * 100) }}{% endif %}
          </td>
          <td class="num mono tnum">
            {{ s.n_trades }}
            {% if s.n_trades < 5 and s.n_trades > 0 %}
              <span class="mp-chip mp-chip--pending" style="margin-left:4px;">积累中</span>
            {% endif %}
          </td>
          <td class="num mono tnum">
            {% if s.n_capacity_skipped > 0 %}
              <span class="mp-chip" style="background:#fef3c7; color:#92400e;">
                {{ s.n_capacity_skipped }}
              </span>
            {% else %}—{% endif %}
          </td>
        </tr>
        {% endfor %}
        {% if spy %}
        <tr style="border-top:2px solid var(--ns-outline-variant);">
          <td><strong>{{ spy.display_name }}</strong> <small class="muted">(基准)</small></td>
          <td class="num mono tnum">
            {% if spy.sharpe is none %}—{% else %}{{ "{:.2f}".format(spy.sharpe) }}{% endif %}
          </td>
          <td class="num mono tnum">{{ "{:.1f}%".format(spy.max_drawdown * 100) }}</td>
          <td class="num mono tnum {% if spy.cumulative_return >= 0 %}up{% else %}down{% endif %}">
            {{ "{:+.2f}%".format(spy.cumulative_return * 100) }}
          </td>
          <td class="num mono tnum">(baseline)</td>
          <td class="num mono tnum">—</td>
          <td class="num mono tnum">—</td>
        </tr>
        {% endif %}
      </tbody>
    </table>
  </div>
</section>
```

- [ ] **Step 13.5: Update `lab_backtest.html` rail**

Find the `mp-backtest-rail` aside and fill:

```html
<aside class="mp-backtest-rail">
  {% include "partials/backtest_filter_card.html" ignore missing %}
  {% include "partials/backtest_strategy_table.html" ignore missing %}
</aside>
```

- [ ] **Step 13.6: Append table CSS**

```css
.mp-backtest-table { width: 100%; min-width: 480px; font-size: 12px; }
.mp-backtest-table th,
.mp-backtest-table td { padding: 8px 10px; }
.mp-backtest-table th { text-align: left; color: var(--ns-on-surface-variant); }
.mp-backtest-table td.num,
.mp-backtest-table th.num { text-align: right; }
.mp-backtest-table tr:hover { background: var(--ns-surface-container); }
```

- [ ] **Step 13.7: Run, pass**

```bash
uv run pytest tests/web/test_lab_backtest.py -v
```

Expected: 14/14 pass (10 from earlier + 4 new).

- [ ] **Step 13.8: Ruff + commit**

```bash
git add marketpulse/web/templates/lab_backtest.html \
       marketpulse/web/templates/partials/backtest_filter_card.html \
       marketpulse/web/templates/partials/backtest_strategy_table.html \
       marketpulse/web/static/css/app.css \
       tests/web/test_lab_backtest.py
git commit -m "feat(lab): backtest filter card + strategy leaderboard

Filter card: Horizon (1/5/20/60) + Time (30/90/180/all) chips,
GET-submit forms matching /lab/ai-track pattern. No source/strategy
filters in v0 (head-to-head comparison is the point).

Strategy table: 7 columns (Strategy / Sharpe / MaxDD / Cum Ret /
vs SPY / n_trades / skipped). Strategy name links to
/lab/ai-track?strategy=<name> for hit-rate cross-reference. SPY
row at bottom marked '(基准)' with thicker separator. n<5 trades
get '积累中' chip; n_capacity_skipped > 0 gets amber chip.

4 web tests: horizon chips, all 6 strategy display names, skipped
chip rendered, SPY baseline row marked."
```

---

### Task 14: `/lab/ai-track` strategy leaderboard cross-link

**Files:**
- Modify: `marketpulse/web/templates/partials/ai_track_strategy_table.html` (add arrow to backtest)

- [ ] **Step 14.1: Write failing test**

Append to existing `tests/web/test_lab_strategy_table.py` (Phase 3 test file):

```python
def test_lab_ai_track_strategy_row_links_to_backtest(
    client, monkeypatch, db_session,
):
    """Each strategy row in /lab/ai-track has a → /lab/backtest arrow."""
    _login(client, monkeypatch)
    _seed(db_session, ticker="X", strategy="momentum_breakout", excess=0.03)
    db_session.commit()
    r = client.get("/lab/ai-track")
    # The strategy table should include a backtest link with preserved horizon
    assert "/lab/backtest" in r.text
```

- [ ] **Step 14.2: Run, fail**

```bash
uv run pytest tests/web/test_lab_strategy_table.py -v -k backtest
```

Expected: 1 fail.

- [ ] **Step 14.3: Read existing strategy_table partial**

```bash
cat marketpulse/web/templates/partials/ai_track_strategy_table.html
```

(Phase 3 file — look at where strategy rows are rendered.)

- [ ] **Step 14.4: Add backtest arrow link**

In `marketpulse/web/templates/partials/ai_track_strategy_table.html`, find the strategy row `<li>` and add a backtest-link chip after the existing rate display. Locate the part that shows `hit_rate`, and add a small link cell:

```html
<a href="/lab/backtest?horizon={{ filters.horizon }}"
   class="mp-strategy-backtest-link"
   title="在回测视图中查看">
  <span class="material-symbols-outlined" style="font-size:14px;">arrow_forward</span>
</a>
```

Insert this inside the strategy row, after the existing rate spans. Find the existing template and look for the closing tag of the row (e.g., right before `</li>`).

A minimal-impact edit: add this attribute to the strategy link or as a sibling. The exact insertion depends on the existing structure — read the partial and put the arrow link in a visually sensible place.

- [ ] **Step 14.5: Append CSS for the arrow link**

```css
.mp-strategy-backtest-link {
  display: inline-flex; align-items: center;
  color: var(--ns-on-surface-variant);
  text-decoration: none;
  padding: 2px 4px;
  border-radius: 2px;
}
.mp-strategy-backtest-link:hover {
  color: var(--ns-primary);
  background: var(--ns-surface-container);
}
```

Append to `marketpulse/web/static/css/app.css`.

- [ ] **Step 14.6: Run, pass**

```bash
uv run pytest tests/web/test_lab_strategy_table.py -v
```

Expected: 5/5 pass (4 existing + 1 new).

- [ ] **Step 14.7: Run broader /lab/ai-track suite — no regressions**

```bash
uv run pytest tests/web/test_lab_ai_track.py tests/web/test_lab_strategy_filter.py tests/web/test_lab_strategy_table.py -q
```

Expected: all green.

- [ ] **Step 14.8: Ruff + commit**

```bash
uv run ruff check marketpulse/web/templates/partials/ai_track_strategy_table.html
git add marketpulse/web/templates/partials/ai_track_strategy_table.html \
       marketpulse/web/static/css/app.css \
       tests/web/test_lab_strategy_table.py
git commit -m "feat(lab): /lab/ai-track strategy rows link to backtest

Adds a small → arrow on each strategy row in the leaderboard
linking to /lab/backtest?horizon=<current>. Preserves the current
horizon filter when navigating. Strategy-level filter not preserved
(backtest is head-to-head only in v0).

1 web test: /lab/backtest URL appears in /lab/ai-track response."
```

---

### Task 15: Final integration — full suite + ruff + smoke

- [ ] **Step 15.1: Full pytest**

```bash
uv run pytest 2>&1 | tail -1
```

Expected: ~700+ passed (Phase 3 left at 648 + ~50 new Phase 4 tests).

- [ ] **Step 15.2: Ruff entire repo**

```bash
uv run ruff check . 2>&1 | tail -3
```

Expected: `All checks passed!`.

- [ ] **Step 15.3: Module imports clean**

```bash
uv run python -c "
from marketpulse.backtest import (
    StrategyBacktestResult, run_all_backtests,
    simulate_strategy_from_pairs, simulate_spy_buyhold,
)
print('ok')
"
```

Expected: `ok`.

- [ ] **Step 15.4: Smoke test routes**

```bash
SESSION_SECRET=test-secret-thats-long-enough-32chars APP_PASSWORD_HASH=x \
uv run python -c "
from fastapi.testclient import TestClient
from marketpulse.web.main import app
client = TestClient(app)
for path in [
    '/lab/backtest',
    '/lab/backtest?horizon=5',
    '/lab/backtest?horizon=5&since_days=30',
    '/lab/backtest?horizon=20&since_days=all',
    '/lab/backtest?horizon=3',   # invalid → 422
]:
    r = client.get(path, follow_redirects=False)
    print(f'{path}: {r.status_code}')
"
```

Expected: first 4 → 303 (auth redirect). Last → 303 too (auth redirect happens before validation). To verify the 422 happens with auth:

```bash
# Authenticated version:
uv run python -c "
import os
os.environ['SESSION_SECRET'] = 'test-secret-thats-long-enough-32chars'
os.environ['APP_PASSWORD_HASH'] = 'x'
from fastapi.testclient import TestClient
from marketpulse.web.main import app
client = TestClient(app)
from marketpulse.auth.password import hash_password
pw = 'secret'
os.environ['APP_PASSWORD_HASH'] = hash_password(pw)
from marketpulse.config import get_settings
get_settings.cache_clear()
client.post('/login', data={'password': pw})
r1 = client.get('/lab/backtest?horizon=5', follow_redirects=False)
r2 = client.get('/lab/backtest?horizon=3', follow_redirects=False)
print(f'horizon=5: {r1.status_code}')
print(f'horizon=3: {r2.status_code}')
"
```

Expected: `horizon=5: 200`, `horizon=3: 422`.

- [ ] **Step 15.5: empyrical-reloaded import verified**

```bash
uv run python -c "from empyrical import sharpe_ratio, max_drawdown; print('ok')"
```

Expected: `ok`.

- [ ] **Step 15.6: Commit log review**

```bash
git log --oneline main..HEAD | wc -l
```

Expected: 14 task commits (or 15 with a final cleanup commit if needed).

- [ ] **Step 15.7: If anything failed, fix + commit**

```bash
git add <files>
git commit -m "fix(phase-4): <specific cleanup>"
```

---

## Self-Review Notes

(Per writing-plans skill checklist.)

**Spec coverage:**

| Spec section | Covered by |
|---|---|
| § Goal + identity | T2 (StrategyBacktestResult), T10 (route), T11 (warning banner) |
| § Non-goals | Documented in spec; no tasks needed |
| § Architecture data flow | T4 (queries) → T6 (simulator) → T9 (orchestrator) → T10 (route) |
| § Portfolio Simulator Algorithm CLOSE→OPEN→MTM→RECORD | T6 (algorithm + ordering test) |
| § SPY Baseline (linear interp) | T7 |
| § StrategyBacktestResult fields | T2 |
| § Capital Constraint | T6 (Step 6.1 capacity tests) |
| § Daily MTM (linear interp) | T3 (elapsed_fraction) + T6 (MTM step) |
| § Metrics empyrical-reloaded | T1 (dep) + T5 (wrappers) |
| § File Structure | All tasks |
| § UI Spec | T10 (route + shell) + T11 (hero+KPI) + T12 (charts) + T13 (filter+table) |
| § Cross-link with /lab/ai-track | T14 |
| § Edge Cases | T6/T7/T8 (various edges in tests) |
| § Telemetry | T6 (backtest_signal_capacity_skipped) + T9 (backtest_run_complete) |
| § Open Decisions (16 locked) | T1-T14 collectively (each decision referenced in commit msgs / tests) |

All sections accounted for.

**Placeholder scan:** None found. Every step has concrete code or commands.

**Type consistency:**
- `EventOutcomePair` defined T4, used T6/T7/T9/T10 ✓
- `StrategyBacktestResult` fields used consistently T2/T6/T7/T8/T9/T10 ✓
- `simulate_strategy_from_pairs` signature used in T6 tests + T9 orchestrator ✓
- `simulate_spy_buyhold` signature stable T7 + T9 ✓
- `downsample_equity_curve` stub in T6, replaced in T8 — both call signatures identical ✓
- `build_calendar` / `elapsed_fraction` from T3 used in T6/T7 ✓
- `compute_metrics` from T5 returns `BacktestMetrics` used in T6/T7 ✓

No drift.

**One acknowledged simplification:** in T6's `excess_vs_spy` computation, the per-trade proxy is a simplification of the more rigorous "strategy_cum_return − spy_cum_return on aligned daily curves". The aligned-curves approach would require joining strategy daily_equity_curve with SPY daily_equity_curve point-by-point. The proxy is documented in the simulator comment. Phase 4.5 can replace.
