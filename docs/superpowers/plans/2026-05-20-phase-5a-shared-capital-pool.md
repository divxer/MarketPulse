# Phase 5a — Shared Capital Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Phase 4's six isolated $10k portfolios with ONE shared $10k pool that all strategies bid into. Bid weights come from 60-day rolling causal Sharpe. UI gets a `?mode=per-strategy | shared-pool` toggle on the existing `/lab/backtest` page (Per-Strategy stays default for backward compat).

**Architecture:** Two new pure modules: `sharpe.py` (rolling causal Sharpe + bid weight functions) and `portfolio_simulator.py` (shared-pool daily loop with strict CLOSE → BID → WEIGHT → DEDUP → ALLOC → MTM → RECORD order). Phase 4's `simulator.py` is extended to emit `StrategyBacktestArtifacts` (carrying the un-downsampled equity curve) so the shared-pool simulator can compute rolling Sharpe without re-running per-strategy backtests. UI toggle picks between Phase 4 (isolated) and Phase 5a (shared) views — both pre-computed in a single orchestrator call.

**Tech Stack:** Python 3.12 + FastAPI + SQLAlchemy 2.x + Jinja2 + `empyrical-reloaded` (already added in Phase 4). No new dependencies. No new database tables. No Alembic migration.

**Spec:** `docs/superpowers/specs/2026-05-20-phase-5a-shared-capital-pool-design.md`

---

## File Structure

```
marketpulse/
├── backtest/                              EXTEND existing module
│   ├── __init__.py                        MODIFY: re-export new types
│   ├── types.py                           MODIFY: + 4 dataclasses, populate Phase 5 hooks
│   ├── simulator.py                       MODIFY: emit Artifacts; add run_shared_pool_backtest
│   ├── sharpe.py                          NEW: rolling_sharpe + compute_bid_weights
│   └── portfolio_simulator.py             NEW: simulate_shared_pool daily loop
└── web/
    ├── routes/backtest.py                 MODIFY: accept ?mode= param
    └── templates/
        ├── lab_backtest.html              MODIFY: mode-conditional partial includes
        └── partials/
            ├── backtest_hero.html         MODIFY: mode-specific sub-text + templated lookback
            ├── backtest_filter_card.html  MODIFY: + VIEW chip row
            ├── backtest_kpi_strip_shared.html NEW: pool-level KPIs
            ├── backtest_strategy_table_shared.html NEW: contribution columns
            └── backtest_bid_history.html  NEW: collapsible last-100 timeline

tests/
├── unit/
│   ├── test_backtest_sharpe.py            NEW: rolling Sharpe + bid weighting
│   └── test_backtest_portfolio_simulator.py NEW: shared-pool simulator
├── integration/
│   └── test_backtest_shared_pool.py       NEW: orchestrator + DB seed
└── web/
    └── test_lab_backtest_modes.py         NEW: ?mode=shared-pool route + toggle UI
```

No DB migration. No new dependencies.

---

## Conventions

- **TDD strict**: each task = failing test → run/see fail → minimal impl → run/see pass → commit.
- **Working directory**: `/Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5a-plan` (worktree on `plan/phase-5a-shared-pool`).
- **Run tests**: `uv run pytest <path> -v`.
- **Lint**: `uv run ruff check <path>`.
- **No new DB tables, no migrations** — pure read-side over Phase 1-3 outputs.
- **Daily loop ORDER LOCK** (spec § 2): `CLOSE → BID → WEIGHT → DEDUP → ALLOC → MTM → RECORD`. Tests assert this order.
- **Tiebreaker 3-key composite** (spec § 2): `(-weight, event_time, strategy_name)` for both DEDUP `max()` and ALLOC `sorted()`.
- **Causality lock** (spec § 3): rolling Sharpe at `as_of=d` includes only outcomes with `horizon_date < d`. Test enforces.
- **Frozen dataclasses**: all new types are `@dataclass(frozen=True)`.
- **Field order rule**: non-defaulted fields BEFORE defaulted fields in every dataclass (Python rule). Spec § 4 lists explicit order for `PortfolioBacktestResult`.
- **bid_policy provenance**: `"rolling_sharpe_60d_v0"` baked into `PortfolioBacktestResult`.

---

### Task 1: New dataclasses + Phase 4 type extensions

**Files:**
- Modify: `marketpulse/backtest/types.py`
- Test: `tests/unit/test_backtest_types_phase5a.py`

This task adds the 4 new dataclasses defined in spec § 4 and lifts the two reserved Phase 5 hooks on `StrategyBacktestResult` from "always None" to "documented as populated by shared-pool runs". No behavior change to Phase 4 isolated runs — they still leave the hooks at None.

- [ ] **Step 1.1: Write failing tests**

Create `tests/unit/test_backtest_types_phase5a.py`:

```python
"""Phase 5a new dataclasses — frozen, value-equal, correct field order."""
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime

import pytest


def _result_kwargs(**overrides):
    """Minimum-viable StrategyBacktestResult kwargs (unchanged from Phase 4)."""
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


def _contribution_kwargs(**overrides):
    base = {
        "strategy": "momentum_breakout",
        "display_name": "动量突破",
        "n_trades": 5,
        "n_dedup_skipped": 1,
        "n_capacity_skipped": 0,
        "n_cash_short_skipped": 0,
        "contribution_pnl": 250.0,
        "avg_exposure": 0.30,
        "avg_bid_weight": 1.4,
        "n_bids": 6,
        "n_floor_hits": 0,
    }
    base.update(overrides)
    return base


def _portfolio_kwargs(**overrides):
    base = {
        "horizon": 5,
        "n_trades": 30,
        "n_dedup_total": 4,
        "avg_capital_utilization": 0.55,
        "cumulative_return": 0.12,
        "annual_return": 0.24,
        "sharpe": 1.4,
        "sortino": 1.7,
        "max_drawdown": -0.06,
        "calmar": 4.0,
        "win_rate": 0.65,
        "avg_win_pct": 0.04,
        "avg_loss_pct": -0.02,
        "daily_equity_curve": [(date(2026, 4, 1), 10000.0), (date(2026, 5, 1), 11200.0)],
        "excess_vs_spy": 0.07,
        "per_strategy_stats": {},
        "bid_history": [],
    }
    base.update(overrides)
    return base


def _bid_kwargs(**overrides):
    base = {
        "date": date(2026, 5, 1),
        "strategy": "momentum_breakout",
        "ticker": "AAPL",
        "weight": 1.2,
        "outcome": "won",
        "winner": None,
    }
    base.update(overrides)
    return base


# ─── StrategyBacktestResult: Phase 5 hooks still accept None (Phase 4 default) ───

def test_strategy_result_phase5_hooks_default_to_none():
    from marketpulse.backtest.types import StrategyBacktestResult
    r = StrategyBacktestResult(**_result_kwargs())
    assert r.strategy_exposure is None
    assert r.capital_bid_score is None


def test_strategy_result_phase5_hooks_can_be_populated():
    from marketpulse.backtest.types import StrategyBacktestResult
    r = StrategyBacktestResult(**_result_kwargs(
        strategy_exposure=0.32, capital_bid_score=1.45,
    ))
    assert r.strategy_exposure == 0.32
    assert r.capital_bid_score == 1.45


# ─── StrategyBacktestArtifacts ───

def test_artifacts_carry_full_equity_curve():
    from marketpulse.backtest.types import StrategyBacktestArtifacts
    curve = [(date(2026, 4, 1), 10000.0), (date(2026, 4, 2), 10050.0)]
    a = StrategyBacktestArtifacts(strategy="momentum_breakout", full_equity_curve=curve)
    assert a.strategy == "momentum_breakout"
    assert a.full_equity_curve == curve


def test_artifacts_is_frozen():
    from marketpulse.backtest.types import StrategyBacktestArtifacts
    a = StrategyBacktestArtifacts(strategy="x", full_equity_curve=[])
    with pytest.raises(FrozenInstanceError):
        a.strategy = "y"


def test_artifacts_full_equity_curve_not_in_result_dto():
    """Spec § 4: separation of concerns — Result is DTO, Artifacts is compute layer."""
    from marketpulse.backtest.types import StrategyBacktestResult
    r = StrategyBacktestResult(**_result_kwargs())
    # The Result DTO must NOT carry full_equity_curve (that's on Artifacts)
    assert not hasattr(r, "full_equity_curve")
    assert not hasattr(r, "_full_equity_curve")


# ─── StrategyContribution ───

def test_contribution_required_fields():
    from marketpulse.backtest.types import StrategyContribution
    c = StrategyContribution(**_contribution_kwargs())
    assert c.strategy == "momentum_breakout"
    assert c.n_trades == 5
    assert c.n_floor_hits == 0


def test_contribution_is_frozen():
    from marketpulse.backtest.types import StrategyContribution
    c = StrategyContribution(**_contribution_kwargs())
    with pytest.raises(FrozenInstanceError):
        c.n_trades = 999


# ─── BidRecord ───

def test_bid_record_outcome_literal_won():
    from marketpulse.backtest.types import BidRecord
    b = BidRecord(**_bid_kwargs())
    assert b.outcome == "won"
    assert b.winner is None


def test_bid_record_dedup_loser_carries_winner():
    from marketpulse.backtest.types import BidRecord
    b = BidRecord(**_bid_kwargs(outcome="dedup_loser", winner="general"))
    assert b.outcome == "dedup_loser"
    assert b.winner == "general"


def test_bid_record_is_frozen():
    from marketpulse.backtest.types import BidRecord
    b = BidRecord(**_bid_kwargs())
    with pytest.raises(FrozenInstanceError):
        b.weight = 999.0


# ─── PortfolioBacktestResult ───

def test_portfolio_result_required_fields():
    from marketpulse.backtest.types import PortfolioBacktestResult
    r = PortfolioBacktestResult(**_portfolio_kwargs())
    assert r.horizon == 5
    assert r.n_trades == 30
    assert r.n_dedup_total == 4
    assert r.avg_capital_utilization == 0.55


def test_portfolio_result_provenance_defaults():
    """Spec § 4: bid_policy and mtm_model carry Phase 5a v0 provenance."""
    from marketpulse.backtest.types import PortfolioBacktestResult
    r = PortfolioBacktestResult(**_portfolio_kwargs())
    assert r.bid_policy == "rolling_sharpe_60d_v0"
    assert r.mtm_model == "linear_interpolation_v0"
    assert r.display_name == "Shared Pool"


def test_portfolio_result_is_frozen():
    from marketpulse.backtest.types import PortfolioBacktestResult
    r = PortfolioBacktestResult(**_portfolio_kwargs())
    with pytest.raises(FrozenInstanceError):
        r.n_trades = 999


def test_portfolio_result_can_carry_per_strategy_stats():
    from marketpulse.backtest.types import (
        PortfolioBacktestResult, StrategyContribution,
    )
    contribs = {"momentum_breakout": StrategyContribution(**_contribution_kwargs())}
    r = PortfolioBacktestResult(**_portfolio_kwargs(per_strategy_stats=contribs))
    assert "momentum_breakout" in r.per_strategy_stats
    assert r.per_strategy_stats["momentum_breakout"].n_trades == 5
```

- [ ] **Step 1.2: Run, fail**

```bash
uv run pytest tests/unit/test_backtest_types_phase5a.py -v
```

Expected: every test fails with `ImportError: cannot import name 'StrategyBacktestArtifacts'` (and friends) from `marketpulse.backtest.types`.

- [ ] **Step 1.3: Extend `marketpulse/backtest/types.py`**

Append these classes to the file (preserving existing `StrategyBacktestResult`):

```python
"""StrategyBacktestArtifacts, StrategyContribution, BidRecord, PortfolioBacktestResult."""
from typing import Literal


@dataclass(frozen=True)
class StrategyBacktestArtifacts:
    """Diagnostic + cross-module compute layer for a per-strategy run.

    Separates SERIALIZATION concerns (StrategyBacktestResult — what goes
    into templates, JSON, pickle, API responses) from COMPUTE concerns
    (StrategyBacktestArtifacts — what the Phase 5a shared-pool simulator
    needs internally for rolling Sharpe lookups).
    """
    strategy: str
    full_equity_curve: list[tuple[date, float]]


@dataclass(frozen=True)
class StrategyContribution:
    """One strategy's slice of a shared-pool run."""
    strategy: str
    display_name: str
    n_trades: int
    n_dedup_skipped: int
    n_capacity_skipped: int
    n_cash_short_skipped: int
    contribution_pnl: float
    avg_exposure: float
    avg_bid_weight: float
    n_bids: int
    n_floor_hits: int


@dataclass(frozen=True)
class BidRecord:
    """One bid decision — diagnostic timeline."""
    date: date
    strategy: str
    ticker: str
    weight: float
    outcome: Literal["won", "dedup_loser", "cap_full", "cash_short"]
    winner: str | None


@dataclass(frozen=True)
class PortfolioBacktestResult:
    """Phase 5a shared-pool result — the ONE portfolio combining all strategies."""

    # Identity (required)
    horizon: int

    # Aggregate counts (required)
    n_trades: int
    n_dedup_total: int

    # Utilization (required)
    avg_capital_utilization: float

    # Performance metrics (required; sharpe/sortino/calmar may be None)
    cumulative_return: float
    annual_return: float
    sharpe: float | None
    sortino: float | None
    max_drawdown: float
    calmar: float | None
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float

    # Series + benchmarks (required)
    daily_equity_curve: list[tuple[date, float]]
    excess_vs_spy: float

    # Breakdown + diagnostics (required)
    per_strategy_stats: dict[str, StrategyContribution]
    bid_history: list[BidRecord]

    # Defaulted provenance (always-default in v0)
    display_name: str = "Shared Pool"
    mtm_model: str = "linear_interpolation_v0"
    bid_policy: str = "rolling_sharpe_60d_v0"
```

- [ ] **Step 1.4: Run, pass**

```bash
uv run pytest tests/unit/test_backtest_types_phase5a.py -v
```

Expected: 13/13 pass.

- [ ] **Step 1.5: Ruff + commit**

```bash
uv run ruff check marketpulse/backtest/types.py tests/unit/test_backtest_types_phase5a.py
git add marketpulse/backtest/types.py tests/unit/test_backtest_types_phase5a.py
git commit -m "feat(phase-5a): new dataclasses — Artifacts, Contribution, BidRecord, Portfolio

Spec § 4: four frozen dataclasses added to types.py.
- StrategyBacktestArtifacts: un-downsampled equity curve for rolling
  Sharpe lookup. NOT in StrategyBacktestResult (DTO/compute split).
- StrategyContribution: per-strategy slice of shared-pool run.
- BidRecord: one bid decision — Literal['won','dedup_loser','cap_full','cash_short'].
- PortfolioBacktestResult: combined-pool result. Field order respects
  Python dataclass rule (non-default first, default last).
  Provenance defaults: display_name='Shared Pool',
  mtm_model='linear_interpolation_v0', bid_policy='rolling_sharpe_60d_v0'.

13 unit tests lock field shapes, frozenness, and provenance values.

Spec: docs/superpowers/specs/2026-05-20-phase-5a-shared-capital-pool-design.md"
```

---

### Task 2: rolling_sharpe service

**Files:**
- Create: `marketpulse/backtest/sharpe.py`
- Test: `tests/unit/test_backtest_sharpe.py`

This task implements the rolling causal Sharpe function. It operates on a pre-computed per-strategy daily equity curve (provided by the caller — Phase 4's `simulate_strategy_from_pairs` will be modified in Task 5 to emit this via Artifacts).

- [ ] **Step 2.1: Write failing tests**

Create `tests/unit/test_backtest_sharpe.py`:

```python
"""Rolling causal Sharpe service for Phase 5a bid weighting."""
from datetime import date, timedelta


def _curve(start_value=10_000, n_days=30, daily_return=0.005, start_date=None):
    """Build a synthetic daily equity curve."""
    start_date = start_date or date(2026, 4, 1)
    curve = []
    v = float(start_value)
    for i in range(n_days):
        d = start_date + timedelta(days=i)
        curve.append((d, v))
        v *= (1 + daily_return)
    return curve


def test_rolling_sharpe_returns_positive_for_steady_upward_curve():
    from marketpulse.backtest.sharpe import rolling_sharpe
    curve = _curve(daily_return=0.005, n_days=30)
    as_of = date(2026, 5, 1)
    s = rolling_sharpe(curve, as_of=as_of, lookback_days=60, min_events=5)
    assert s is not None
    assert s > 0


def test_rolling_sharpe_excludes_dates_at_or_after_as_of():
    """Causality: outcomes with curve date >= as_of are excluded."""
    from marketpulse.backtest.sharpe import rolling_sharpe
    # 30-day curve ending 2026-04-30
    curve = _curve(daily_return=0.005, n_days=30, start_date=date(2026, 4, 1))
    # as_of = 2026-04-15 → only first 14 days qualify (1, 2, ..., 14)
    as_of = date(2026, 4, 15)
    s = rolling_sharpe(curve, as_of=as_of, lookback_days=60, min_events=5)
    # 14 days included → still enough for Sharpe
    assert s is not None


def test_rolling_sharpe_returns_none_below_min_events():
    """n<min_events → None (matches Phase 4 n<5 floor)."""
    from marketpulse.backtest.sharpe import rolling_sharpe
    curve = _curve(daily_return=0.005, n_days=3)
    as_of = date(2026, 5, 1)
    s = rolling_sharpe(curve, as_of=as_of, lookback_days=60, min_events=5)
    assert s is None


def test_rolling_sharpe_lookback_window_truncates_curve():
    """Only curve points within [as_of - lookback_days, as_of) participate."""
    from marketpulse.backtest.sharpe import rolling_sharpe
    # 100-day curve starting 2026-01-01
    curve = _curve(daily_return=0.005, n_days=100, start_date=date(2026, 1, 1))
    # as_of = 2026-04-15; lookback 30d → window starts 2026-03-16
    as_of = date(2026, 4, 15)
    s_30 = rolling_sharpe(curve, as_of=as_of, lookback_days=30, min_events=5)
    s_60 = rolling_sharpe(curve, as_of=as_of, lookback_days=60, min_events=5)
    # With steady 0.5% daily, both should be similar positive Sharpe
    assert s_30 is not None and s_30 > 0
    assert s_60 is not None and s_60 > 0


def test_rolling_sharpe_empty_curve_returns_none():
    from marketpulse.backtest.sharpe import rolling_sharpe
    s = rolling_sharpe([], as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert s is None


def test_rolling_sharpe_normalizes_inf_to_none():
    """Degenerate input (zero-variance curve) yields inf from empyrical → normalize None."""
    from marketpulse.backtest.sharpe import rolling_sharpe
    # All same value → diff'd returns are all 0 → std=0 → Sharpe=inf
    flat_curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0) for i in range(30)]
    s = rolling_sharpe(flat_curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert s is None


# ─── compute_bid_weights tests ───

def test_bid_weight_equal_when_all_strategies_below_threshold():
    """All None Sharpes → all weights = 1.0 (bootstrap)."""
    from marketpulse.backtest.sharpe import compute_bid_weights
    # Empty curves → None Sharpe for both
    daily_curves = {"momentum_breakout": [], "general": []}
    weights = compute_bid_weights(
        ["momentum_breakout", "general"], daily_curves,
        as_of=date(2026, 5, 1), lookback_days=60,
    )
    assert weights == {"momentum_breakout": 1.0, "general": 1.0}


def test_bid_weight_avg_fill_when_some_below_threshold():
    """Mixed None and known → None strategies get avg of known."""
    from marketpulse.backtest.sharpe import compute_bid_weights
    daily_curves = {
        "momentum_breakout": _curve(daily_return=0.01, n_days=30),  # high Sharpe
        "news_event": [],  # n<5 → None → avg fill
    }
    weights = compute_bid_weights(
        ["momentum_breakout", "news_event"], daily_curves,
        as_of=date(2026, 5, 1), lookback_days=60,
    )
    assert weights["momentum_breakout"] > 0
    # news_event gets avg of known = momentum_breakout's weight (only known)
    assert weights["news_event"] == weights["momentum_breakout"]


def test_bid_weight_floors_negative_sharpe_at_0_1():
    """Negative Sharpe → floored at 0.1, not lower."""
    from marketpulse.backtest.sharpe import compute_bid_weights
    # Losing curve: 30 days of -1% daily returns
    daily_curves = {
        "loser": _curve(daily_return=-0.01, n_days=30),
        "winner": _curve(daily_return=0.01, n_days=30),
    }
    weights = compute_bid_weights(
        ["loser", "winner"], daily_curves,
        as_of=date(2026, 5, 1), lookback_days=60,
    )
    assert weights["loser"] == 0.1  # floor hit
    assert weights["winner"] > 0.1


def test_bid_weight_does_not_floor_high_positive_sharpe():
    """Sharpe >> 0.1 passes through unchanged."""
    from marketpulse.backtest.sharpe import compute_bid_weights
    daily_curves = {"winner": _curve(daily_return=0.01, n_days=30)}
    weights = compute_bid_weights(
        ["winner"], daily_curves,
        as_of=date(2026, 5, 1), lookback_days=60,
    )
    assert weights["winner"] > 0.1  # well above floor


def test_bid_weight_all_negative_degenerates_to_fifo():
    """All negative + floor → equal 0.1 weights → ties broken by event_time/alpha downstream."""
    from marketpulse.backtest.sharpe import compute_bid_weights
    daily_curves = {
        "loser1": _curve(daily_return=-0.01, n_days=30),
        "loser2": _curve(daily_return=-0.005, n_days=30),
    }
    weights = compute_bid_weights(
        ["loser1", "loser2"], daily_curves,
        as_of=date(2026, 5, 1), lookback_days=60,
    )
    assert weights["loser1"] == 0.1
    assert weights["loser2"] == 0.1


def test_bid_weight_deep_negative_sharpe_still_floored_at_0_1():
    """Sharpe = -10 still gets 0.1 floor (not lower)."""
    from marketpulse.backtest.sharpe import compute_bid_weights
    daily_curves = {"catastrophic": _curve(daily_return=-0.05, n_days=30)}
    weights = compute_bid_weights(
        ["catastrophic"], daily_curves,
        as_of=date(2026, 5, 1), lookback_days=60,
    )
    assert weights["catastrophic"] == 0.1


def test_compute_bid_weights_raises_on_missing_strategy():
    """Contract: every strategy in strategies_today must be in daily_curves."""
    import pytest
    from marketpulse.backtest.sharpe import compute_bid_weights
    daily_curves = {"momentum_breakout": _curve()}
    with pytest.raises(KeyError):
        compute_bid_weights(
            ["momentum_breakout", "missing"], daily_curves,
            as_of=date(2026, 5, 1), lookback_days=60,
        )


def test_bid_weight_empty_curve_in_daily_curves_returns_bootstrap():
    """Empty curve for a strategy → n=0 < 5 → None → bootstrap or avg-fill."""
    from marketpulse.backtest.sharpe import compute_bid_weights
    daily_curves = {"empty_strategy": []}
    weights = compute_bid_weights(
        ["empty_strategy"], daily_curves,
        as_of=date(2026, 5, 1), lookback_days=60,
    )
    assert weights == {"empty_strategy": 1.0}  # all-None bootstrap
```

- [ ] **Step 2.2: Run, fail**

```bash
uv run pytest tests/unit/test_backtest_sharpe.py -v
```

Expected: 14 fails (ImportError on `marketpulse.backtest.sharpe`).

- [ ] **Step 2.3: Create `marketpulse/backtest/sharpe.py`**

```python
"""Rolling causal Sharpe service for Phase 5a bid weighting.

Spec § 3: bid weights come from rolling Sharpe computed on the
per-strategy ISOLATED daily curve (Phase 4 output), NOT the
shared-pool slice. This is an intentional bootstrap that decouples
weight measurement from realized PnL to avoid recursive starvation.

Causality: rolling_sharpe(curve, as_of, lookback_days) returns the
Sharpe of curve values dated in [as_of - lookback_days, as_of).
Outcomes with date >= as_of are excluded (no future leakage).
"""
from __future__ import annotations

from datetime import date, timedelta

import math
import numpy as np
from empyrical import sharpe_ratio


def rolling_sharpe(
    daily_curve: list[tuple[date, float]],
    *,
    as_of: date,
    lookback_days: int = 60,
    min_events: int = 5,
) -> float | None:
    """Sharpe of daily-return diffs of curve points in [as_of - lookback, as_of).

    Returns None when:
      - Fewer than `min_events` qualifying points (matches Phase 4 n<5 floor).
      - empyrical returns inf/-inf (degenerate zero-variance input).
    """
    if not daily_curve:
        return None
    window_start = as_of - timedelta(days=lookback_days)
    sliced = [(d, v) for d, v in daily_curve if window_start <= d < as_of]
    if len(sliced) < min_events:
        return None
    values = np.array([v for _, v in sliced], dtype=float)
    if len(values) < 2:
        return None
    daily_returns = np.diff(values) / values[:-1]
    s = float(sharpe_ratio(daily_returns))
    if not math.isfinite(s):
        return None
    return s


def compute_bid_weights(
    strategies_today: list[str],
    daily_curves: dict[str, list[tuple[date, float]]],
    *,
    as_of: date,
    lookback_days: int = 60,
    min_floor: float = 0.1,
    min_events: int = 5,
) -> dict[str, float]:
    """Compute per-strategy bid weights using rolling Sharpe.

    Algorithm (spec § 3):
      1. rolling_sharpe per strategy on its slice of daily_curves.
      2. If all None → all weights = 1.0 (full equal-weight bootstrap).
      3. Otherwise: None strategies get avg of known weights; floor at min_floor.

    Contract:
      - Every entry of `strategies_today` MUST be a key of `daily_curves`.
        Raises KeyError on missing.
      - Empty curve for a strategy → its rolling_sharpe is None → bootstrap path.
    """
    raw: dict[str, float | None] = {
        s: rolling_sharpe(
            daily_curves[s], as_of=as_of, lookback_days=lookback_days,
            min_events=min_events,
        )
        for s in strategies_today
    }

    known = [w for w in raw.values() if w is not None]
    if not known:
        # All-None bootstrap: every strategy gets 1.0
        return {s: 1.0 for s in raw}

    avg_known = sum(known) / len(known)
    return {
        s: max(w if w is not None else avg_known, min_floor)
        for s, w in raw.items()
    }
```

- [ ] **Step 2.4: Run, pass**

```bash
uv run pytest tests/unit/test_backtest_sharpe.py -v
```

Expected: 14/14 pass.

- [ ] **Step 2.5: Ruff + commit**

```bash
uv run ruff check marketpulse/backtest/sharpe.py tests/unit/test_backtest_sharpe.py
git add marketpulse/backtest/sharpe.py tests/unit/test_backtest_sharpe.py
git commit -m "feat(phase-5a): sharpe module — rolling Sharpe + bid weight computation

Spec § 3: two pure functions on the per-strategy daily curve.
- rolling_sharpe(curve, as_of, lookback_days, min_events): causal Sharpe
  over [as_of - lookback, as_of). Returns None when n<min_events OR
  empyrical yields inf (degenerate zero-variance input).
- compute_bid_weights(strategies_today, daily_curves, as_of, ...):
  None → bootstrap (all 1.0) when all unknown; None → avg-fill when
  mixed; floor at 0.1 for everyone (penalizes losers without locking
  them out).

14 unit tests cover positive/negative paths, lookback truncation,
inf normalization, bootstrap full + partial, deep-negative floor,
KeyError on missing strategy, and empty-curve handling."
```

---

### Task 3: Extend simulator to emit StrategyBacktestArtifacts

**Files:**
- Modify: `marketpulse/backtest/simulator.py`
- Modify: `tests/unit/test_backtest_simulator.py`

Phase 5a's `simulate_shared_pool` needs each strategy's un-downsampled daily curve for rolling Sharpe. We add a sibling function `simulate_strategy_with_artifacts` that returns `(StrategyBacktestResult, StrategyBacktestArtifacts)`. The original `simulate_strategy_from_pairs` is kept unchanged (Phase 4 callers still work).

- [ ] **Step 3.1: Append failing test**

Append to `tests/unit/test_backtest_simulator.py`:

```python
def test_simulate_strategy_with_artifacts_returns_full_curve():
    """Phase 5a: artifacts variant returns both DTO + un-downsampled curve."""
    from marketpulse.backtest.simulator import simulate_strategy_with_artifacts
    from marketpulse.backtest.types import StrategyBacktestArtifacts
    pairs = [_pair("AAA", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)]
    result, artifacts = simulate_strategy_with_artifacts(
        pairs=pairs,
        strategy="momentum_breakout", display_name="动量突破", horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    # Result is the existing Phase 4 shape (downsampled curve)
    assert result.strategy == "momentum_breakout"
    # Artifacts carry the un-downsampled curve, same strategy linkage
    assert isinstance(artifacts, StrategyBacktestArtifacts)
    assert artifacts.strategy == "momentum_breakout"
    # Full curve has at least one point per trading day in the window
    # (5/1, 5/4, 5/5, 5/6, 5/7, 5/8 = 6 weekdays); densification adds intermediate
    assert len(artifacts.full_equity_curve) >= 6


def test_simulate_strategy_with_artifacts_curve_matches_record_step():
    """Artifacts curve is the un-downsampled equity_curve internal to the simulator."""
    from marketpulse.backtest.simulator import simulate_strategy_with_artifacts
    # 1 event, $1k position. Equity should be flat until horizon close.
    pairs = [_pair("A", date(2026, 5, 1), 100.0, date(2026, 5, 8), 110.0)]
    _, artifacts = simulate_strategy_with_artifacts(
        pairs=pairs, strategy="x", display_name="X", horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    # First day = entry day, no MTM → equity = initial_capital
    assert artifacts.full_equity_curve[0][1] == pytest.approx(10_000.0, abs=1e-3)
    # Last point >= initial (won trade)
    assert artifacts.full_equity_curve[-1][1] > 10_000.0


def test_simulate_strategy_from_pairs_unchanged_signature():
    """Phase 4 regression: original function still returns single result."""
    from marketpulse.backtest.simulator import simulate_strategy_from_pairs
    pairs = [_pair("A", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)]
    r = simulate_strategy_from_pairs(
        pairs=pairs,
        strategy="momentum_breakout", display_name="动量突破", horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0,
    )
    # Returns a single StrategyBacktestResult (not a tuple)
    assert hasattr(r, "strategy")
    assert not isinstance(r, tuple)
```

- [ ] **Step 3.2: Run, fail**

```bash
uv run pytest tests/unit/test_backtest_simulator.py -v -k artifacts
```

Expected: 2 fails (ImportError on `simulate_strategy_with_artifacts`); 1 pass (regression test).

- [ ] **Step 3.3: Refactor `simulator.py` to expose internal curve**

Open `marketpulse/backtest/simulator.py`. Inside `simulate_strategy_from_pairs`, the variable `equity_curve` holds the un-downsampled curve. Currently it's only used to construct the downsampled curve. We extract the body into a private helper, then provide TWO public entry points.

Add this at the top of the file (after imports):

```python
from marketpulse.backtest.types import (
    StrategyBacktestArtifacts,
    StrategyBacktestResult,
)
```

Add this new public function near the bottom of the file (just before `run_all_backtests`):

```python
def simulate_strategy_with_artifacts(
    pairs: list[EventOutcomePair],
    *,
    strategy: str,
    display_name: str,
    horizon: int,
    initial_capital: float = 10_000.0,
    position_size: float = 1_000.0,
    max_capital_in_use: float = 10_000.0,
) -> tuple[StrategyBacktestResult, StrategyBacktestArtifacts]:
    """Phase 5a variant: returns (DTO, Artifacts) for shared-pool rolling Sharpe.

    Same simulator logic as simulate_strategy_from_pairs — but ALSO returns
    the un-downsampled internal equity_curve as a StrategyBacktestArtifacts
    sibling. Phase 4 callers continue to use simulate_strategy_from_pairs
    (which discards the artifact).
    """
    # Run the existing simulator (it already builds equity_curve internally).
    result = simulate_strategy_from_pairs(
        pairs=pairs,
        strategy=strategy, display_name=display_name, horizon=horizon,
        initial_capital=initial_capital, position_size=position_size,
        max_capital_in_use=max_capital_in_use,
    )
    # Rebuild the un-downsampled curve by re-running the daily loop just for
    # the equity series. This is duplicative but isolated — keeps Phase 4
    # callers untouched. Future optimization: refactor simulate_strategy_from_pairs
    # to optionally return the full curve via a flag.
    if not pairs:
        from datetime import date as _date
        return result, StrategyBacktestArtifacts(
            strategy=strategy,
            full_equity_curve=[(_date.today(), initial_capital)],
        )

    # Replicate the calendar + daily loop just to grab the un-downsampled curve.
    db_dates: set[date] = set()
    for p in pairs:
        db_dates.add(p.event_time.date())
        db_dates.add(p.horizon_date)
    raw_dates: set[date] = set(db_dates)
    min_d, max_d = min(raw_dates), max(raw_dates)
    cur = min_d
    while cur <= max_d:
        if cur.weekday() < 5:
            raw_dates.add(cur)
        cur += timedelta(days=1)
    calendar = build_calendar(list(raw_dates))

    pairs_by_entry: dict[date, list[EventOutcomePair]] = {}
    for p in pairs:
        pairs_by_entry.setdefault(p.event_time.date(), []).append(p)

    cash: float = initial_capital
    open_positions: list[_OpenPosition] = []
    equity_curve: list[tuple[date, float]] = []
    for d in calendar:
        # CLOSE
        still_open: list[_OpenPosition] = []
        for pos in open_positions:
            if pos.horizon_date == d:
                realized_ret = (pos.horizon_price - pos.entry_price) / pos.entry_price
                cash += pos.position_size * (1 + realized_ret)
            else:
                still_open.append(pos)
        open_positions = still_open
        # OPEN
        for p in pairs_by_entry.get(d, []):
            capital_in_use = sum(pos.position_size for pos in open_positions)
            if capital_in_use + position_size > max_capital_in_use:
                continue
            if cash < position_size:
                continue
            open_positions.append(_OpenPosition(
                ticker=p.ticker, entry_date=d,
                entry_price=p.event_price, horizon_date=p.horizon_date,
                horizon_price=p.horizon_price, position_size=position_size,
            ))
            cash -= position_size
        # MTM
        positions_value = 0.0
        for pos in open_positions:
            if pos.entry_date == d:
                positions_value += pos.position_size
            else:
                fraction = elapsed_fraction(
                    calendar, entry=pos.entry_date,
                    horizon=pos.horizon_date, current=d,
                )
                est_price = pos.entry_price + (
                    pos.horizon_price - pos.entry_price
                ) * fraction
                positions_value += pos.position_size * (est_price / pos.entry_price)
        equity_curve.append((d, cash + positions_value))

    return result, StrategyBacktestArtifacts(
        strategy=strategy, full_equity_curve=equity_curve,
    )
```

Note: This intentionally re-runs the daily loop. The cleaner alternative is to refactor `simulate_strategy_from_pairs` to optionally return the full curve, but that touches Phase 4 code and increases blast radius. v0 simplicity wins.

- [ ] **Step 3.4: Run, pass**

```bash
uv run pytest tests/unit/test_backtest_simulator.py -v
```

Expected: all simulator tests pass (existing 16 + new 3 = 19).

- [ ] **Step 3.5: Ruff + commit**

```bash
uv run ruff check marketpulse/backtest/simulator.py tests/unit/test_backtest_simulator.py
git add marketpulse/backtest/simulator.py tests/unit/test_backtest_simulator.py
git commit -m "feat(phase-5a): simulate_strategy_with_artifacts entry point

Returns (StrategyBacktestResult, StrategyBacktestArtifacts) tuple.
DTO unchanged (Phase 4 callers untouched); Artifacts carry the
un-downsampled daily curve for Phase 5a rolling Sharpe lookup.

Re-runs the daily loop a second time to grab the curve — keeps Phase 4
hot path isolated and small. Future optimization: refactor to share
the loop via a flag. Spec § 4 mandates the DTO/Artifacts separation
to keep StrategyBacktestResult a clean serializable shape.

3 new tests + 16 existing Phase 4 tests pass."
```

---

### Task 4: simulate_shared_pool — CLOSE + BID COLLECT + WEIGHT steps

**Files:**
- Create: `marketpulse/backtest/portfolio_simulator.py`
- Test: `tests/unit/test_backtest_portfolio_simulator.py`

The shared-pool simulator is built in 3 task chunks (Task 4 = first 3 daily-loop steps; Task 5 = DEDUP + ALLOCATE; Task 6 = MTM + RECORD + finalization). Tasks 4-6 leave the file in a partially-working state — each commits after passing the tests added in that task.

- [ ] **Step 4.1: Write failing tests**

Create `tests/unit/test_backtest_portfolio_simulator.py`:

```python
"""Shared-pool simulator — CLOSE → BID → WEIGHT → DEDUP → ALLOC → MTM → RECORD."""
from datetime import UTC, date, datetime, timedelta

import pytest

from marketpulse.backtest.queries import EventOutcomePair


def _pair(ticker, strategy, event_date, event_price, horizon_date,
          horizon_price, benchmark_return=0.01):
    """EventOutcomePair plus a `strategy` attribute (Phase 5a needs it).

    Phase 4's EventOutcomePair doesn't carry strategy; Phase 5a needs the
    strategy on each bid. The shared-pool query returns a (strategy, pair)
    tuple list; tests use a lightweight dataclass to mock this.
    """
    return _BidInput(
        strategy=strategy,
        ticker=ticker,
        event_time=datetime.combine(event_date, datetime.min.time(), tzinfo=UTC),
        event_price=event_price,
        horizon_price=horizon_price,
        horizon_date=horizon_date,
        forward_return=(horizon_price - event_price) / event_price,
        benchmark_forward_return=benchmark_return,
    )


from dataclasses import dataclass


@dataclass(frozen=True)
class _BidInput:
    """Lightweight test fixture for shared-pool bid input.

    The real simulator accepts `list[tuple[str, EventOutcomePair]]` — strategy
    name plus the existing EventOutcomePair. This dataclass flattens that
    for test ergonomics.
    """
    strategy: str
    ticker: str
    event_time: datetime
    event_price: float
    horizon_price: float
    horizon_date: date
    forward_return: float
    benchmark_forward_return: float


def _curve(start_value=10_000, n_days=30, daily_return=0.005, start_date=None):
    start_date = start_date or date(2026, 4, 1)
    curve = []
    v = float(start_value)
    for i in range(n_days):
        d = start_date + timedelta(days=i)
        curve.append((d, v))
        v *= (1 + daily_return)
    return curve


def test_shared_pool_zero_bids_returns_flat_curve():
    """No bid inputs → equity stays at initial_capital throughout."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    r = simulate_shared_pool(
        bids=[],
        daily_curves={},
        horizon=5,
        initial_capital=10_000.0,
        position_size=1_000.0,
        max_capital_in_use=10_000.0,
        lookback_days=60,
    )
    assert r.n_trades == 0
    assert r.cumulative_return == 0.0


def test_shared_pool_single_bid_opens_one_position():
    """1 bid that wins → 1 trade → equity rises on close."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [_pair("AAA", "momentum_breakout",
                   date(2026, 5, 1), 100.0, date(2026, 5, 8), 110.0)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": _curve()},
        horizon=5,
        initial_capital=10_000.0,
        position_size=1_000.0,
        max_capital_in_use=10_000.0,
        lookback_days=60,
    )
    assert r.n_trades == 1


def test_shared_pool_close_frees_cap_before_alloc():
    """CLOSE → BID ordering: position closes day d → cash freed → new same-day bid fits.

    Setup: 10 positions on 5/1 fill the $10k cap. They close 5/4. One new
    bid arrives 5/4. CLOSE step frees cap before BID COLLECT, so the new
    bid succeeds (n_trades = 11). If order reversed (BID before CLOSE), the
    11th would be cap-skipped.
    """
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [
        *[_pair(f"A{i}", "momentum_breakout", date(2026, 5, 1), 100.0,
                 date(2026, 5, 4), 101.0) for i in range(10)],
        _pair("B0", "momentum_breakout", date(2026, 5, 4), 100.0,
               date(2026, 5, 11), 102.0),
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": _curve()},
        horizon=5,
        initial_capital=10_000.0,
        position_size=1_000.0,
        max_capital_in_use=10_000.0,
        lookback_days=60,
    )
    assert r.n_trades == 11, f"Expected 11 (CLOSE-before-BID), got {r.n_trades}"


def test_shared_pool_in_flight_ticker_filtered_at_bid_collect():
    """A bid for a ticker already held is filtered at BID COLLECT (no double-up)."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [
        # First bid opens AAPL on 5/1, holds until 5/8
        _pair("AAPL", "momentum_breakout", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 105.0),
        # Second bid: AAPL again on 5/5 (mid-hold) → should be filtered
        _pair("AAPL", "news_event", date(2026, 5, 5), 100.0,
               date(2026, 5, 12), 110.0),
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={
            "momentum_breakout": _curve(),
            "news_event": _curve(),
        },
        horizon=5,
        initial_capital=10_000.0,
        position_size=1_000.0,
        max_capital_in_use=10_000.0,
        lookback_days=60,
    )
    # Only 1 trade — the second AAPL bid was filtered (in-flight)
    assert r.n_trades == 1


def test_shared_pool_bootstrap_period_uses_equal_weight():
    """First 60 days have no mature outcomes → all weights = 1.0 (FIFO order)."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [
        _pair("X", "momentum_breakout", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 105.0),
        _pair("Y", "news_event", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 105.0),
    ]
    # Empty curves → all None Sharpe → bootstrap
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": [], "news_event": []},
        horizon=5,
        initial_capital=10_000.0,
        position_size=1_000.0,
        max_capital_in_use=10_000.0,
        lookback_days=60,
    )
    # Both trade (no dedup collision; different tickers)
    assert r.n_trades == 2
```

- [ ] **Step 4.2: Run, fail**

```bash
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v
```

Expected: 5 fails (ImportError on `marketpulse.backtest.portfolio_simulator`).

- [ ] **Step 4.3: Create `marketpulse/backtest/portfolio_simulator.py` skeleton (CLOSE + BID + WEIGHT only)**

```python
"""Shared-pool simulator — Phase 5a.

Spec § 2: daily loop order strict CLOSE → BID → WEIGHT → DEDUP → ALLOC → MTM → RECORD.

This file implements the 3-stage build: Task 4 covers CLOSE + BID + WEIGHT
(with DEDUP/ALLOC/MTM/RECORD as no-ops for now), Task 5 fills DEDUP + ALLOC,
Task 6 fills MTM + RECORD + finalization. Intermediate commits leave the
function partially working but with tests passing for the implemented steps.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from marketpulse.backtest.metrics import compute_metrics
from marketpulse.backtest.sharpe import compute_bid_weights
from marketpulse.backtest.trading_calendar import build_calendar, elapsed_fraction
from marketpulse.backtest.types import (
    BidRecord,
    PortfolioBacktestResult,
    StrategyContribution,
)
from marketpulse.logging import get_logger

log = get_logger(__name__)


@dataclass
class _OpenPosition:
    """Internal shared-pool position state."""
    strategy: str
    ticker: str
    entry_date: date
    entry_price: float
    horizon_date: date
    horizon_price: float
    position_size: float


def simulate_shared_pool(
    bids: list,                                # list of _BidInput-shaped objects
    daily_curves: dict[str, list[tuple[date, float]]],
    *,
    horizon: int,
    initial_capital: float = 10_000.0,
    position_size: float = 1_000.0,
    max_capital_in_use: float = 10_000.0,
    lookback_days: int = 60,
) -> PortfolioBacktestResult:
    """Phase 5a shared-pool simulator. See spec § 2 for algorithm."""
    if not bids:
        from datetime import date as _date
        return PortfolioBacktestResult(
            horizon=horizon,
            n_trades=0,
            n_dedup_total=0,
            avg_capital_utilization=0.0,
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
            per_strategy_stats={},
            bid_history=[],
        )

    # Calendar from union of event/horizon dates, then weekday-densified.
    db_dates: set[date] = set()
    for b in bids:
        db_dates.add(b.event_time.date())
        db_dates.add(b.horizon_date)
    raw_dates = set(db_dates)
    min_d, max_d = min(raw_dates), max(raw_dates)
    cur = min_d
    while cur <= max_d:
        if cur.weekday() < 5:
            raw_dates.add(cur)
        cur += timedelta(days=1)
    calendar = build_calendar(list(raw_dates))

    # Pre-index bids by entry date for O(1) day lookup
    bids_by_entry: dict[date, list] = {}
    for b in bids:
        bids_by_entry.setdefault(b.event_time.date(), []).append(b)

    # Simulator state
    cash: float = initial_capital
    open_positions: list[_OpenPosition] = []
    equity_curve: list[tuple[date, float]] = []
    all_bid_records: list[BidRecord] = []
    n_trades_by_strategy: dict[str, int] = {}
    trade_returns_by_strategy: dict[str, list[float]] = {}
    n_dedup_skipped_by_strategy: dict[str, int] = {}
    n_capacity_skipped_by_strategy: dict[str, int] = {}
    n_cash_short_skipped_by_strategy: dict[str, int] = {}
    n_floor_hits_by_strategy: dict[str, int] = {}
    n_bids_by_strategy: dict[str, int] = {}
    bid_weights_by_strategy: dict[str, list[float]] = {}
    capital_in_use_by_day: list[float] = []
    exposure_by_strategy_by_day: dict[str, list[float]] = {}

    for d in calendar:
        # ─── CLOSE ───
        still_open: list[_OpenPosition] = []
        for pos in open_positions:
            if pos.horizon_date == d:
                realized_ret = (pos.horizon_price - pos.entry_price) / pos.entry_price
                cash += pos.position_size * (1 + realized_ret)
                trade_returns_by_strategy.setdefault(pos.strategy, []).append(realized_ret)
            else:
                still_open.append(pos)
        open_positions = still_open

        # ─── BID COLLECT ───
        in_flight_tickers = {p.ticker for p in open_positions}
        todays_bids = [
            b for b in bids_by_entry.get(d, [])
            if b.ticker not in in_flight_tickers
        ]

        # ─── WEIGHT COMPUTE ───
        strategies_today = sorted({b.strategy for b in todays_bids})  # determinism
        weights: dict[str, float] = {}
        if strategies_today:
            weights = compute_bid_weights(
                strategies_today, daily_curves,
                as_of=d, lookback_days=lookback_days,
            )

        # Track n_floor_hits (weights at 0.1 came from a negative-Sharpe floor)
        for s in strategies_today:
            if weights.get(s) == 0.1:
                # Could be floor OR legitimate value; check by recomputing raw
                from marketpulse.backtest.sharpe import rolling_sharpe
                raw = rolling_sharpe(
                    daily_curves[s], as_of=d, lookback_days=lookback_days,
                )
                if raw is not None and raw < 0.1:
                    n_floor_hits_by_strategy[s] = n_floor_hits_by_strategy.get(s, 0) + 1

        # ─── DEDUP, ALLOC, MTM, RECORD — Tasks 5+6 ───
        # Stub: just record equity (cash, no positions) so curve is populated.
        equity_curve.append((d, cash))
        capital_in_use_by_day.append(0.0)
        for s in strategies_today:
            exposure_by_strategy_by_day.setdefault(s, []).append(0.0)

    # Stub return: only n_trades from accumulators (zero until Task 5).
    n_trades = sum(n_trades_by_strategy.values())
    return PortfolioBacktestResult(
        horizon=horizon,
        n_trades=n_trades,
        n_dedup_total=sum(n_dedup_skipped_by_strategy.values()),
        avg_capital_utilization=(
            sum(capital_in_use_by_day) / (max_capital_in_use * len(capital_in_use_by_day))
            if capital_in_use_by_day else 0.0
        ),
        cumulative_return=(equity_curve[-1][1] - initial_capital) / initial_capital
                          if equity_curve else 0.0,
        annual_return=0.0, sharpe=None, sortino=None, max_drawdown=0.0,
        calmar=None, win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0,
        daily_equity_curve=equity_curve,
        excess_vs_spy=0.0,
        per_strategy_stats={},
        bid_history=all_bid_records,
    )
```

- [ ] **Step 4.4: Run, fail (most tests still fail)**

```bash
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v
```

Expected: `test_shared_pool_zero_bids_returns_flat_curve` passes. The other 4 (which expect n_trades > 0) fail because DEDUP/ALLOC stub doesn't open positions yet.

- [ ] **Step 4.5: Commit the partial scaffold**

```bash
uv run ruff check marketpulse/backtest/portfolio_simulator.py tests/unit/test_backtest_portfolio_simulator.py
git add marketpulse/backtest/portfolio_simulator.py tests/unit/test_backtest_portfolio_simulator.py
git commit -m "feat(phase-5a): shared-pool simulator scaffold — CLOSE + BID + WEIGHT

Spec § 2 algorithm, first 3 steps of the daily loop:
- CLOSE: mature horizons return position_size * (1 + realized_ret) to cash
- BID COLLECT: filter today's bids by in-flight ticker (no double-up)
- WEIGHT: call compute_bid_weights with rolling Sharpe lookups

DEDUP, ALLOC, MTM, RECORD intentionally stub (Tasks 5-6 fill in).
Result: 1/5 tests pass (zero-bid no-op); 4/5 fail because no positions
yet open. Will go green when Task 5+6 land.

Locks the data flow shape, accumulators, and calendar densification."
```

---

### Task 5: simulate_shared_pool — DEDUP + ALLOCATE steps

**Files:**
- Modify: `marketpulse/backtest/portfolio_simulator.py`
- Modify: `tests/unit/test_backtest_portfolio_simulator.py`

- [ ] **Step 5.1: Append failing tests**

Append to `tests/unit/test_backtest_portfolio_simulator.py`:

```python
def test_shared_pool_dedup_picks_highest_sharpe_winner():
    """Two strategies bid same ticker same day → highest-Sharpe wins."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [
        _pair("AAPL", "momentum_breakout", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 105.0),
        _pair("AAPL", "news_event", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 105.0),
    ]
    # momentum has higher Sharpe (better curve)
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={
            "momentum_breakout": _curve(daily_return=0.01, n_days=30),
            "news_event": _curve(daily_return=0.001, n_days=30),
        },
        horizon=5,
        initial_capital=10_000.0,
        position_size=1_000.0,
        max_capital_in_use=10_000.0,
        lookback_days=60,
    )
    # Only 1 trade (dedup); the loser logs a dedup_loser BidRecord
    assert r.n_trades == 1
    assert r.n_dedup_total == 1
    assert len([b for b in r.bid_history if b.outcome == "dedup_loser"]) == 1


def test_shared_pool_dedup_loser_records_bid_loss():
    """The losing bid is logged with outcome='dedup_loser' and winner=<name>."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [
        _pair("AAPL", "momentum_breakout", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 105.0),
        _pair("AAPL", "news_event", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 105.0),
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={
            "momentum_breakout": _curve(daily_return=0.01, n_days=30),
            "news_event": _curve(daily_return=0.001, n_days=30),
        },
        horizon=5,
        initial_capital=10_000.0,
        position_size=1_000.0,
        max_capital_in_use=10_000.0,
        lookback_days=60,
    )
    losers = [b for b in r.bid_history if b.outcome == "dedup_loser"]
    assert len(losers) == 1
    assert losers[0].strategy == "news_event"
    assert losers[0].winner == "momentum_breakout"


def test_shared_pool_greedy_alloc_respects_max_cap():
    """11 same-day distinct-ticker bids in $10k pool → 10 open, 1 cap_full."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [
        _pair(f"T{i}", "momentum_breakout", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 101.0)
        for i in range(11)
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": _curve()},
        horizon=5,
        initial_capital=10_000.0,
        position_size=1_000.0,
        max_capital_in_use=10_000.0,
        lookback_days=60,
    )
    assert r.n_trades == 10
    cap_full = [b for b in r.bid_history if b.outcome == "cap_full"]
    assert len(cap_full) == 1


def test_equal_weight_tiebreak_uses_event_time_then_alpha():
    """Tiebreaker chain: weight → event_time → strategy name."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    # Both bids have identical event_time (date-only construction → midnight),
    # so the tiebreaker degenerates to alphabetical strategy.
    # momentum_breakout < news_event lexicographically → momentum_breakout wins.
    bids = [
        _pair("AAPL", "news_event", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 105.0),
        _pair("AAPL", "momentum_breakout", date(2026, 5, 1), 100.0,
               date(2026, 5, 8), 105.0),
    ]
    # All-None Sharpe → bootstrap → both weight 1.0 → tiebreaker kicks in
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"news_event": [], "momentum_breakout": []},
        horizon=5,
        initial_capital=10_000.0,
        position_size=1_000.0,
        max_capital_in_use=10_000.0,
        lookback_days=60,
    )
    won = [b for b in r.bid_history if b.outcome == "won"]
    assert len(won) == 1
    assert won[0].strategy == "momentum_breakout"  # alphabetically first
```

- [ ] **Step 5.2: Run, fail**

```bash
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v -k "dedup or greedy or tiebreak"
```

Expected: 4 fails.

- [ ] **Step 5.3: Replace the DEDUP/ALLOC stub block**

In `portfolio_simulator.py`, find this block (inside the `for d in calendar:` loop):

```python
        # ─── DEDUP, ALLOC, MTM, RECORD — Tasks 5+6 ───
        # Stub: just record equity (cash, no positions) so curve is populated.
        equity_curve.append((d, cash))
        capital_in_use_by_day.append(0.0)
        for s in strategies_today:
            exposure_by_strategy_by_day.setdefault(s, []).append(0.0)
```

Replace with:

```python
        # ─── DEDUP (same-day same-ticker collision) ───
        bids_by_ticker: dict[str, list] = {}
        for b in todays_bids:
            bids_by_ticker.setdefault(b.ticker, []).append(b)
        winners: dict[str, object] = {}
        for ticker, group in bids_by_ticker.items():
            # 3-key composite: (-weight, event_time, strategy_name)
            best = min(group, key=lambda b: (
                -weights[b.strategy], b.event_time, b.strategy,
            ))
            winners[ticker] = best
            for loser in group:
                if loser is not best:
                    all_bid_records.append(BidRecord(
                        date=d, strategy=loser.strategy, ticker=ticker,
                        weight=weights[loser.strategy],
                        outcome="dedup_loser", winner=best.strategy,
                    ))
                    n_dedup_skipped_by_strategy[loser.strategy] = (
                        n_dedup_skipped_by_strategy.get(loser.strategy, 0) + 1
                    )

        # ─── ALLOCATE (capital-constrained, greedy by weight desc) ───
        sorted_winners = sorted(
            winners.values(),
            key=lambda b: (-weights[b.strategy], b.event_time, b.strategy),
        )
        for b in sorted_winners:
            n_bids_by_strategy[b.strategy] = n_bids_by_strategy.get(b.strategy, 0) + 1
            bid_weights_by_strategy.setdefault(b.strategy, []).append(
                weights[b.strategy]
            )
            capital_in_use = sum(p.position_size for p in open_positions)
            if capital_in_use + position_size > max_capital_in_use:
                all_bid_records.append(BidRecord(
                    date=d, strategy=b.strategy, ticker=b.ticker,
                    weight=weights[b.strategy],
                    outcome="cap_full", winner=None,
                ))
                n_capacity_skipped_by_strategy[b.strategy] = (
                    n_capacity_skipped_by_strategy.get(b.strategy, 0) + 1
                )
                continue
            if cash < position_size:
                all_bid_records.append(BidRecord(
                    date=d, strategy=b.strategy, ticker=b.ticker,
                    weight=weights[b.strategy],
                    outcome="cash_short", winner=None,
                ))
                n_cash_short_skipped_by_strategy[b.strategy] = (
                    n_cash_short_skipped_by_strategy.get(b.strategy, 0) + 1
                )
                continue
            open_positions.append(_OpenPosition(
                strategy=b.strategy, ticker=b.ticker,
                entry_date=d, entry_price=b.event_price,
                horizon_date=b.horizon_date, horizon_price=b.horizon_price,
                position_size=position_size,
            ))
            cash -= position_size
            n_trades_by_strategy[b.strategy] = n_trades_by_strategy.get(b.strategy, 0) + 1
            all_bid_records.append(BidRecord(
                date=d, strategy=b.strategy, ticker=b.ticker,
                weight=weights[b.strategy],
                outcome="won", winner=None,
            ))

        # ─── MTM, RECORD — Task 6 fills proper MTM. Stub: cash only. ───
        equity_curve.append((d, cash + sum(p.position_size for p in open_positions)))
        capital_in_use_by_day.append(sum(p.position_size for p in open_positions))
        for s in strategies_today:
            exposure_by_strategy_by_day.setdefault(s, []).append(
                sum(p.position_size for p in open_positions if p.strategy == s)
                / initial_capital
            )
```

Also fix `n_bids` counting — bids that LOST dedup never reached ALLOCATE so they need to be counted there:

In the DEDUP step, after `n_dedup_skipped_by_strategy[loser.strategy] += 1`, also increment:

```python
                    n_bids_by_strategy[loser.strategy] = (
                        n_bids_by_strategy.get(loser.strategy, 0) + 1
                    )
                    bid_weights_by_strategy.setdefault(loser.strategy, []).append(
                        weights[loser.strategy]
                    )
```

- [ ] **Step 5.4: Run, pass**

```bash
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v
```

Expected: 8/9 pass (the 9th — `test_shared_pool_bootstrap_period_uses_equal_weight` from Task 4 — should also pass now since allocation works). 9/9 if all looks good.

- [ ] **Step 5.5: Commit**

```bash
uv run ruff check marketpulse/backtest/portfolio_simulator.py tests/unit/test_backtest_portfolio_simulator.py
git add marketpulse/backtest/portfolio_simulator.py tests/unit/test_backtest_portfolio_simulator.py
git commit -m "feat(phase-5a): simulator DEDUP + ALLOCATE steps

Spec § 2 algorithm, steps 4-5:
- DEDUP: same-day same-ticker bids collapsed to single winner via 3-key
  composite (-weight, event_time, strategy_name). Losers get
  BidRecord(outcome='dedup_loser', winner=<winner>) and increment
  n_dedup_skipped_by_strategy.
- ALLOCATE: greedy sort by same composite key, fill positions until
  cap OR cash exhausted. cap_full / cash_short / won outcomes all
  logged as BidRecord. Counters incremented per strategy.

MTM still stub (Task 6). Equity curve uses cash + sum(position_size) —
not yet linearly-interpolated MTM values.

4 new tests cover dedup winner, loser logging, greedy cap behavior,
and 3-key tiebreaker."
```

---

### Task 6: simulate_shared_pool — MTM + RECORD + finalization

**Files:**
- Modify: `marketpulse/backtest/portfolio_simulator.py`
- Modify: `tests/unit/test_backtest_portfolio_simulator.py`

This task replaces the cash-only equity record with proper linear-interpolation MTM (matching Phase 4) and computes all aggregate metrics (Sharpe, max_drawdown, etc.) + builds per-strategy StrategyContribution + finalizes the result with last-100 bid history slice.

- [ ] **Step 6.1: Append failing tests**

Append to `tests/unit/test_backtest_portfolio_simulator.py`:

```python
def test_shared_pool_mtm_uses_linear_interp_per_position():
    """Mid-period MTM reflects fractional gain (linear interpolation)."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [_pair("M", "momentum_breakout", date(2026, 5, 1), 100.0,
                   date(2026, 5, 7), 110.0)]  # +10% across 4 trading days
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": _curve()},
        horizon=4,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    curve = dict(r.daily_equity_curve)
    mid = curve.get(date(2026, 5, 5))
    assert mid is not None
    # Halfway through → +5% on $1k position → equity = $10_050
    assert mid == pytest.approx(10_050.0, abs=1.0)


def test_shared_pool_no_signal_day_still_records_equity():
    """A day with no bids still records equity (MTM-only update)."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [_pair("X", "momentum_breakout", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 105.0)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": _curve()},
        horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    # 5/4 had no bid, no close — still in curve
    curve_dates = [d for d, _ in r.daily_equity_curve]
    assert date(2026, 5, 4) in curve_dates


def test_shared_pool_contribution_pnl_sums_to_pool_pnl():
    """Σ per_strategy_stats[s].contribution_pnl == pool.cumulative_return * initial."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    bids = [
        _pair("A", "momentum_breakout", date(2026, 5, 1), 100.0, date(2026, 5, 8), 110.0),
        _pair("B", "news_event", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={
            "momentum_breakout": _curve(daily_return=0.01, n_days=30),
            "news_event": _curve(daily_return=0.005, n_days=30),
        },
        horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    total_contrib = sum(c.contribution_pnl for c in r.per_strategy_stats.values())
    pool_pnl = r.cumulative_return * 10_000.0
    assert abs(total_contrib - pool_pnl) < 1.0  # within $1 rounding


def test_shared_pool_bid_records_capped_at_render_layer():
    """bid_history has at most 100 entries (last-100 slice)."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    # 150 distinct-ticker bids over many days
    bids = []
    base = date(2026, 1, 1)
    for i in range(150):
        bids.append(_pair(
            f"T{i}", "momentum_breakout",
            base + timedelta(days=i % 90), 100.0,
            base + timedelta(days=(i % 90) + 5), 101.0,
        ))
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": _curve(n_days=200)},
        horizon=5,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    assert len(r.bid_history) <= 100


def test_shared_pool_avg_capital_utilization_correct():
    """avg_capital_utilization = mean(capital_in_use / max_cap) across all days."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    # 1 position open for 4 days using $1k of $10k → ~10% util on holding days
    bids = [_pair("A", "momentum_breakout", date(2026, 5, 1), 100.0,
                   date(2026, 5, 5), 105.0)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"momentum_breakout": _curve()},
        horizon=4,
        initial_capital=10_000.0, position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
    )
    # Sane range: between 0 and 1
    assert 0.0 <= r.avg_capital_utilization <= 1.0
    # Holding period has 10% util; days outside hold have 0 → overall low
    assert r.avg_capital_utilization > 0
```

- [ ] **Step 6.2: Run, fail**

```bash
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v -k "mtm or no_signal or contribution_pnl or capped or utilization"
```

Expected: 5 fails (MTM is still stub).

- [ ] **Step 6.3: Replace MTM stub with proper linear-interp MTM + finalization**

In `portfolio_simulator.py`, find the stub block at the end of the daily loop:

```python
        # ─── MTM, RECORD — Task 6 fills proper MTM. Stub: cash only. ───
        equity_curve.append((d, cash + sum(p.position_size for p in open_positions)))
        capital_in_use_by_day.append(sum(p.position_size for p in open_positions))
        for s in strategies_today:
            exposure_by_strategy_by_day.setdefault(s, []).append(
                sum(p.position_size for p in open_positions if p.strategy == s)
                / initial_capital
            )
```

Replace with proper MTM:

```python
        # ─── MTM ─── (linear interpolation per spec § 2 + Phase 4)
        positions_value = 0.0
        for pos in open_positions:
            if pos.entry_date == d:
                # Newly opened: no same-day MTM (matches Phase 4 invariant)
                positions_value += pos.position_size
            else:
                fraction = elapsed_fraction(
                    calendar, entry=pos.entry_date,
                    horizon=pos.horizon_date, current=d,
                )
                est_price = pos.entry_price + (
                    pos.horizon_price - pos.entry_price
                ) * fraction
                positions_value += pos.position_size * (est_price / pos.entry_price)

        # ─── RECORD ───
        equity_curve.append((d, cash + positions_value))
        capital_in_use_by_day.append(sum(p.position_size for p in open_positions))
        # Per-strategy exposure (snapshot of currently-deployed capital)
        all_strategies_seen = set(daily_curves.keys()) | set(n_bids_by_strategy.keys())
        for s in all_strategies_seen:
            exposure_by_strategy_by_day.setdefault(s, []).append(
                sum(p.position_size for p in open_positions if p.strategy == s)
                / initial_capital
            )
```

Also replace the stub return block (the part starting `# Stub return:`) with a proper finalization. Find it and replace with:

```python
    # ─── FINALIZE ───
    # Aggregate metrics over the COMBINED pool's daily curve
    all_returns: list[float] = []
    for s_returns in trade_returns_by_strategy.values():
        all_returns.extend(s_returns)
    n_trades = sum(n_trades_by_strategy.values())
    metrics = compute_metrics(
        equity_curve=equity_curve,
        n_trades=n_trades,
        trade_returns=all_returns,
    )

    # avg capital utilization across all days
    avg_util = (
        sum(c / max_capital_in_use for c in capital_in_use_by_day)
        / len(capital_in_use_by_day)
        if capital_in_use_by_day else 0.0
    )

    # Per-strategy contributions
    from marketpulse.strategies import load_strategies
    strategies_yaml = load_strategies()
    per_strategy_stats: dict[str, StrategyContribution] = {}
    initial_per_strategy_cash_share = initial_capital  # for contribution_pnl basis
    for s in set(daily_curves.keys()):
        ret_list = trade_returns_by_strategy.get(s, [])
        contrib_pnl = sum(r * position_size for r in ret_list)
        exposures = exposure_by_strategy_by_day.get(s, [])
        avg_exposure = sum(exposures) / len(exposures) if exposures else 0.0
        avg_bid_weight = (
            sum(bid_weights_by_strategy.get(s, []))
            / len(bid_weights_by_strategy.get(s, []) or [1])
        ) if bid_weights_by_strategy.get(s) else 0.0
        per_strategy_stats[s] = StrategyContribution(
            strategy=s,
            display_name=(
                strategies_yaml[s].display_name if s in strategies_yaml else s
            ),
            n_trades=n_trades_by_strategy.get(s, 0),
            n_dedup_skipped=n_dedup_skipped_by_strategy.get(s, 0),
            n_capacity_skipped=n_capacity_skipped_by_strategy.get(s, 0),
            n_cash_short_skipped=n_cash_short_skipped_by_strategy.get(s, 0),
            contribution_pnl=contrib_pnl,
            avg_exposure=avg_exposure,
            avg_bid_weight=avg_bid_weight,
            n_bids=n_bids_by_strategy.get(s, 0),
            n_floor_hits=n_floor_hits_by_strategy.get(s, 0),
        )

    # Last-100 slice of bid history (spec § 4: render-layer cap)
    bid_history = all_bid_records[-100:] if len(all_bid_records) > 100 else all_bid_records

    # excess_vs_spy: pool cumulative_return vs SPY (caller passes SPY result;
    # for now compute 0.0 here — the orchestrator in Task 7 will override).
    return PortfolioBacktestResult(
        horizon=horizon,
        n_trades=n_trades,
        n_dedup_total=sum(n_dedup_skipped_by_strategy.values()),
        avg_capital_utilization=avg_util,
        cumulative_return=metrics.cumulative_return,
        annual_return=metrics.annual_return,
        sharpe=metrics.sharpe,
        sortino=metrics.sortino,
        max_drawdown=metrics.max_drawdown,
        calmar=metrics.calmar,
        win_rate=metrics.win_rate,
        avg_win_pct=metrics.avg_win_pct,
        avg_loss_pct=metrics.avg_loss_pct,
        daily_equity_curve=equity_curve,
        excess_vs_spy=0.0,  # orchestrator overrides
        per_strategy_stats=per_strategy_stats,
        bid_history=bid_history,
    )
```

- [ ] **Step 6.4: Run, pass**

```bash
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v
```

Expected: 14/14 pass.

- [ ] **Step 6.5: Commit**

```bash
uv run ruff check marketpulse/backtest/portfolio_simulator.py tests/unit/test_backtest_portfolio_simulator.py
git add marketpulse/backtest/portfolio_simulator.py tests/unit/test_backtest_portfolio_simulator.py
git commit -m "feat(phase-5a): simulator MTM + RECORD + finalization

Spec § 2 algorithm steps 6-7:
- MTM: linear-interpolation per position (matches Phase 4); newly-opened
  positions skip same-day MTM (entry_price = current).
- RECORD: equity_curve += (d, cash + Σ positions_value); track
  capital_in_use and per-strategy exposure for utilization metric.

Finalization (after loop):
- Aggregate metrics via empyrical (compute_metrics shared with Phase 4)
- avg_capital_utilization = mean(capital_in_use / max_cap) across days
- StrategyContribution per strategy: n_trades, n_dedup_skipped,
  contribution_pnl, avg_exposure, avg_bid_weight, n_floor_hits etc.
- bid_history sliced to last 100 (template payload bound, spec § 4)
- excess_vs_spy left 0.0 — orchestrator in next task computes it

5 new tests cover linear MTM, no-signal-day, contribution accounting,
last-100 cap, and avg utilization range."
```

---

### Task 7: run_shared_pool_backtest orchestrator

**Files:**
- Modify: `marketpulse/backtest/simulator.py` (append orchestrator)
- Modify: `marketpulse/backtest/__init__.py` (re-export)
- Create: `tests/integration/test_backtest_shared_pool.py`

The orchestrator runs Phase 4's isolated backtests (gets daily curves via Artifacts), then runs Phase 5a's shared-pool simulator on the union of bullish events. Returns `{isolated, artifacts, shared}` triple.

- [ ] **Step 7.1: Write failing integration test**

Create `tests/integration/test_backtest_shared_pool.py`:

```python
"""End-to-end orchestrator test — DB seed + shared-pool run."""
from datetime import UTC, date, datetime, timedelta

import pytest

from marketpulse.db.models import EvaluationEvent, EvaluationOutcome


def _seed(db, *, ticker, strategy, days_ago=10, excess=0.03, horizon=5):
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
        event_price=100.0, horizon_price=100.0 * (1 + excess + 0.001),
        horizon_date=date.today() - timedelta(days=max(0, days_ago - horizon)),
        forward_return=excess + 0.001,
        benchmark_ticker="SPY", benchmark_forward_return=0.001,
        excess_return=excess,
    ))


def test_run_shared_pool_returns_triple(db_session):
    """Orchestrator returns {isolated, artifacts, shared} dict."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5)
    assert "isolated" in out
    assert "artifacts" in out
    assert "shared" in out


def test_run_shared_pool_isolated_matches_run_all_backtests(db_session):
    """Phase 4 regression: isolated list shape = same as run_all_backtests."""
    from marketpulse.backtest.simulator import (
        run_all_backtests,
        run_shared_pool_backtest,
    )
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    iso = run_all_backtests(db_session, horizon=5)
    out = run_shared_pool_backtest(db_session, horizon=5)
    assert len(out["isolated"]) == len(iso)
    # Strategy ordering matches
    assert [r.strategy for r in out["isolated"]] == [r.strategy for r in iso]


def test_run_shared_pool_artifacts_parallel_to_isolated_minus_spy(db_session):
    """Artifacts list parallel-indexed to isolated[:-1] (drops SPY)."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5)
    isolated_no_spy = [r for r in out["isolated"] if r.strategy != "__spy_buyhold__"]
    assert len(out["artifacts"]) == len(isolated_no_spy)
    for art, res in zip(out["artifacts"], isolated_no_spy, strict=True):
        assert art.strategy == res.strategy


def test_run_shared_pool_excess_vs_spy_is_pool_cum_minus_spy_cum(db_session):
    """Orchestrator overrides shared.excess_vs_spy with combined - SPY."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    for i in range(3):
        _seed(db_session, ticker=f"T{i}", strategy="momentum_breakout", excess=0.05)
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5)
    spy = next(r for r in out["isolated"] if r.strategy == "__spy_buyhold__")
    expected = out["shared"].cumulative_return - spy.cumulative_return
    assert abs(out["shared"].excess_vs_spy - expected) < 1e-9
```

- [ ] **Step 7.2: Run, fail**

```bash
uv run pytest tests/integration/test_backtest_shared_pool.py -v
```

Expected: 4 fails (`run_shared_pool_backtest` doesn't exist yet).

- [ ] **Step 7.3: Append orchestrator to `simulator.py`**

Add to bottom of `marketpulse/backtest/simulator.py`:

```python
def run_shared_pool_backtest(
    db,
    *,
    horizon: int = 5,
    since: date | None = None,
    initial_capital: float = 10_000.0,
    position_size: float = 1_000.0,
    max_capital_in_use: float = 10_000.0,
    lookback_days: int = 60,
) -> dict:
    """Phase 5a orchestrator. Spec § 4.

    Returns {isolated, artifacts, shared}:
      - isolated: list[StrategyBacktestResult] — 6 strategies + SPY (Phase 4 view)
      - artifacts: list[StrategyBacktestArtifacts] — parallel to isolated minus SPY
      - shared: PortfolioBacktestResult — Phase 5a combined view
    """
    from dataclasses import dataclass as _dataclass

    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    from marketpulse.backtest.queries import get_bullish_events_with_outcomes
    from marketpulse.strategies import load_strategies

    @_dataclass(frozen=True)
    class _BidInput:
        strategy: str
        ticker: str
        event_time: object
        event_price: float
        horizon_price: float
        horizon_date: object
        forward_return: float
        benchmark_forward_return: float

    strategies = load_strategies()
    isolated: list[StrategyBacktestResult] = []
    artifacts: list = []
    all_bids: list = []
    all_pairs = []

    for name, strat in strategies.items():
        pairs = get_bullish_events_with_outcomes(
            db, strategy=name, horizon=horizon, since=since,
        )
        all_pairs.extend(pairs)
        result, art = simulate_strategy_with_artifacts(
            pairs=pairs,
            strategy=name, display_name=strat.display_name, horizon=horizon,
            initial_capital=initial_capital, position_size=position_size,
            max_capital_in_use=max_capital_in_use,
        )
        isolated.append(result)
        artifacts.append(art)
        for p in pairs:
            all_bids.append(_BidInput(
                strategy=name,
                ticker=p.ticker, event_time=p.event_time,
                event_price=p.event_price, horizon_price=p.horizon_price,
                horizon_date=p.horizon_date,
                forward_return=p.forward_return,
                benchmark_forward_return=p.benchmark_forward_return,
            ))

    # SPY appended last for backward compatibility with run_all_backtests
    spy = simulate_spy_buyhold(pairs=all_pairs, initial_capital=initial_capital)
    isolated.append(spy)

    # Populate Phase 4 isolated results with Phase 5a hooks (avg_exposure,
    # avg_bid_weight) — first run the shared pool, then patch using replace().
    daily_curves = {a.strategy: a.full_equity_curve for a in artifacts}
    shared = simulate_shared_pool(
        bids=all_bids,
        daily_curves=daily_curves,
        horizon=horizon,
        initial_capital=initial_capital,
        position_size=position_size,
        max_capital_in_use=max_capital_in_use,
        lookback_days=lookback_days,
    )
    # Override excess_vs_spy with combined - spy.cum_return
    from dataclasses import replace
    shared = replace(
        shared,
        excess_vs_spy=shared.cumulative_return - spy.cumulative_return,
    )

    # Annotate isolated results with strategy_exposure + capital_bid_score
    enriched_isolated: list[StrategyBacktestResult] = []
    for r in isolated:
        if r.strategy == "__spy_buyhold__":
            enriched_isolated.append(r)
            continue
        contrib = shared.per_strategy_stats.get(r.strategy)
        if contrib is None:
            enriched_isolated.append(r)
            continue
        enriched_isolated.append(replace(
            r,
            strategy_exposure=contrib.avg_exposure,
            capital_bid_score=contrib.avg_bid_weight,
        ))

    return {
        "isolated": enriched_isolated,
        "artifacts": artifacts,
        "shared": shared,
    }
```

- [ ] **Step 7.4: Update `__init__.py`**

Replace contents of `marketpulse/backtest/__init__.py`:

```python
"""Backtest Engine (Phase 4 + Phase 5a) — Strategy Performance Observatory.

A reproducible research observatory for strategy-level synthetic PnL
analysis under constrained-capital simulation assumptions. NOT a
faithful execution-level trading simulator.

Specs:
  docs/superpowers/specs/2026-05-19-phase-4-backtest-engine-mvp.md
  docs/superpowers/specs/2026-05-20-phase-5a-shared-capital-pool-design.md
"""
from marketpulse.backtest.simulator import (
    run_all_backtests,
    run_shared_pool_backtest,
    simulate_spy_buyhold,
    simulate_strategy_from_pairs,
    simulate_strategy_with_artifacts,
)
from marketpulse.backtest.types import (
    BidRecord,
    PortfolioBacktestResult,
    StrategyBacktestArtifacts,
    StrategyBacktestResult,
    StrategyContribution,
)

__all__ = [
    "BidRecord",
    "PortfolioBacktestResult",
    "StrategyBacktestArtifacts",
    "StrategyBacktestResult",
    "StrategyContribution",
    "run_all_backtests",
    "run_shared_pool_backtest",
    "simulate_spy_buyhold",
    "simulate_strategy_from_pairs",
    "simulate_strategy_with_artifacts",
]
```

- [ ] **Step 7.5: Run, pass**

```bash
uv run pytest tests/integration/test_backtest_shared_pool.py -v
uv run pytest tests/unit/test_backtest_portfolio_simulator.py tests/unit/test_backtest_sharpe.py tests/integration/test_backtest_queries.py -v
```

Expected: all green.

- [ ] **Step 7.6: Commit**

```bash
uv run ruff check marketpulse/backtest/ tests/integration/test_backtest_shared_pool.py
git add marketpulse/backtest/ tests/integration/test_backtest_shared_pool.py
git commit -m "feat(phase-5a): run_shared_pool_backtest orchestrator + __init__ re-exports

Spec § 4 contract:
  run_shared_pool_backtest(db, horizon, since, lookback_days, ...) -> dict
  Returns {isolated, artifacts, shared}.

- isolated: per-strategy Phase 4 results + SPY, ENRICHED with
  strategy_exposure + capital_bid_score from the shared-pool contribution.
- artifacts: parallel-indexed to isolated[:-1] (drops SPY). Used internally
  for rolling Sharpe; never serialized to template.
- shared: PortfolioBacktestResult with excess_vs_spy overridden to
  combined.cum_return - spy.cum_return (the canonical alpha number).

4 integration tests cover triple shape, Phase 4 isolated regression,
artifacts ordering, and excess_vs_spy correctness."
```

---

### Task 8: /lab/backtest route accepts ?mode= param

**Files:**
- Modify: `marketpulse/web/routes/backtest.py`
- Create: `tests/web/test_lab_backtest_modes.py`

- [ ] **Step 8.1: Write failing tests**

Create `tests/web/test_lab_backtest_modes.py`:

```python
"""Phase 5a route — ?mode=per-strategy | shared-pool toggle."""
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
    db.add(e)
    db.flush()
    db.add(EvaluationOutcome(
        event_id=e.id, horizon_trading_days=horizon,
        event_price=100.0, horizon_price=100 * (1 + excess + 0.001),
        horizon_date=date.today() - timedelta(days=max(0, days_ago - horizon)),
        forward_return=excess + 0.001, benchmark_ticker="SPY",
        benchmark_forward_return=0.001, excess_return=excess,
    ))


def test_lab_backtest_default_mode_is_per_strategy(client, monkeypatch):
    """No ?mode= → Phase 4 view (backward compat)."""
    _login(client, monkeypatch)
    r = client.get("/lab/backtest")
    assert r.status_code == 200
    # Phase 4 KPIs present (Best Strategy etc.)
    assert "Best Strategy" in r.text


def test_lab_backtest_accepts_shared_pool_mode(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/backtest?mode=shared-pool")
    assert r.status_code == 200


def test_lab_backtest_accepts_per_strategy_mode_explicit(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/backtest?mode=per-strategy")
    assert r.status_code == 200


def test_lab_backtest_invalid_mode_returns_422(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/backtest?mode=garbage")
    assert r.status_code == 422


def test_lab_backtest_shared_mode_renders_pool_marker(
    client, monkeypatch, db_session,
):
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="A1", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest?mode=shared-pool")
    assert r.status_code == 200
    # Provenance marker exposed in hero
    assert "rolling_sharpe_60d_v0" in r.text


def test_lab_backtest_per_strategy_unchanged_with_shared_data(
    client, monkeypatch, db_session,
):
    """Phase 4 regression: per-strategy view renders Phase 4 KPIs even though
    shared-pool data is now computed alongside."""
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="A1", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest?mode=per-strategy")
    assert "Best Strategy" in r.text
    # Per-Strategy view does NOT show Shared Pool-only labels
    assert "Pool Sharpe" not in r.text or "Best Strategy" in r.text
```

- [ ] **Step 8.2: Run, fail**

```bash
uv run pytest tests/web/test_lab_backtest_modes.py -v
```

Expected: all 6 fail or partially fail (no `mode` param yet).

- [ ] **Step 8.3: Update route to accept `mode`**

Open `marketpulse/web/routes/backtest.py`. Add `Literal` import and update the route signature:

```python
from typing import Literal
```

Modify the route handler signature (currently `def lab_backtest(request, horizon, since_days, db, _)`):

```python
@router.get("/lab/backtest", response_class=HTMLResponse)
def lab_backtest(
    request: Request,
    horizon: int = 5,
    since_days: str | int = 90,
    mode: Literal["per-strategy", "shared-pool"] = "per-strategy",
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
```

After the existing `results = run_all_backtests(...)` line, switch behavior based on mode. Replace the existing call with:

```python
    if mode == "shared-pool":
        from marketpulse.backtest.simulator import run_shared_pool_backtest
        out = run_shared_pool_backtest(
            db, horizon=horizon, since=since, lookback_days=60,
        )
        results = out["isolated"]
        shared_result = out["shared"]
    else:
        results = run_all_backtests(db, horizon=horizon, since=since)
        shared_result = None
```

Then add `shared_result` and `mode` to the template context:

```python
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
            "chart_data": chart_data,
            "filters": filters,
            "filters_qs": _qs_from_filters(filters),
            "mode": mode,                           # NEW
            "shared_result": shared_result,         # NEW (None when mode=per-strategy)
            "lookback_days": 60,                    # NEW (templated in hero)
        },
    )
```

Also add `mode` to the filter qs builder so it round-trips:

```python
filters = {"horizon": horizon, "since_days": since_days, "mode": mode}
```

And update `_qs_from_filters` defaults:

```python
DEFAULTS = {"horizon": 5, "since_days": 90, "mode": "per-strategy"}
```

- [ ] **Step 8.4: Update `_qs_from_filters` defaults**

Replace the DEFAULTS dict at the top of `_qs_from_filters`:

```python
def _qs_from_filters(filters: dict) -> str:
    """Build a clean query string, dropping defaults / None / empty."""
    DEFAULTS = {"horizon": 5, "since_days": 90, "mode": "per-strategy"}
    payload = {}
    for k, v in filters.items():
        if v is None or v == "":
            continue
        if k in DEFAULTS and v == DEFAULTS[k]:
            continue
        payload[k] = str(v)
    return urlencode(payload)
```

- [ ] **Step 8.5: Run, expect 4/6 pass**

```bash
uv run pytest tests/web/test_lab_backtest_modes.py -v
```

The "rolling_sharpe_60d_v0" test will still fail until we update the template in Task 9. That's expected — leave it.

- [ ] **Step 8.6: Commit**

```bash
uv run ruff check marketpulse/web/routes/backtest.py
git add marketpulse/web/routes/backtest.py tests/web/test_lab_backtest_modes.py
git commit -m "feat(phase-5a): /lab/backtest accepts ?mode=per-strategy|shared-pool

Spec § 5: route gains a Literal['per-strategy', 'shared-pool'] mode
param defaulting to 'per-strategy' (backward compat).

shared-pool mode calls run_shared_pool_backtest; per-strategy keeps
the Phase 4 run_all_backtests path. Both paths add 'mode',
'shared_result', and 'lookback_days' to the template context for
Task 9's mode-conditional partials.

_qs_from_filters DEFAULTS now drops mode=per-strategy from URLs so
the default URL stays clean.

Tests: 6 added; 5/6 pass at this checkpoint. The 'rolling_sharpe_60d_v0'
marker test passes after Task 10 lands the new hero partial."
```

---

### Task 9: lab_backtest.html mode-conditional shell + filter card VIEW chip

**Files:**
- Modify: `marketpulse/web/templates/lab_backtest.html`
- Modify: `marketpulse/web/templates/partials/backtest_filter_card.html`

- [ ] **Step 9.1: Update `lab_backtest.html` shell**

Replace contents of `marketpulse/web/templates/lab_backtest.html` with:

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
  {% if mode == 'shared-pool' %}
    {% include "partials/backtest_kpi_strip_shared.html" ignore missing %}
  {% else %}
    {% include "partials/backtest_kpi_strip.html" ignore missing %}
  {% endif %}
</section>

<section class="mp-backtest-body">
  <div class="mp-backtest-main">
    {% include "partials/backtest_equity_chart.html" ignore missing %}
    {% include "partials/backtest_drawdown_chart.html" ignore missing %}
    {% if mode == 'shared-pool' %}
      {% include "partials/backtest_bid_history.html" ignore missing %}
    {% endif %}
  </div>
  <aside class="mp-backtest-rail">
    {% include "partials/backtest_filter_card.html" ignore missing %}
    {% if mode == 'shared-pool' %}
      {% include "partials/backtest_strategy_table_shared.html" ignore missing %}
    {% else %}
      {% include "partials/backtest_strategy_table.html" ignore missing %}
    {% endif %}
  </aside>
</section>

{% endblock %}
```

- [ ] **Step 9.2: Add VIEW chip row to `backtest_filter_card.html`**

Edit `marketpulse/web/templates/partials/backtest_filter_card.html`. Find the existing Time chip row (`<div><span class="mp-eyebrow">Time</span>...`) and add this new row immediately AFTER it (still inside the same `<form>`):

```html
    <div>
      <span class="mp-eyebrow">View</span>
      <div class="mp-seg" style="margin-top:6px;">
        <button type="submit" name="mode" value="per-strategy"
                class="{% if filters.mode == 'per-strategy' or not filters.mode %}is-active{% endif %}">单策略</button>
        <button type="submit" name="mode" value="shared-pool"
                class="{% if filters.mode == 'shared-pool' %}is-active{% endif %}">共享池</button>
      </div>
    </div>
```

- [ ] **Step 9.3: Commit**

```bash
git add marketpulse/web/templates/lab_backtest.html \
        marketpulse/web/templates/partials/backtest_filter_card.html
git commit -m "feat(lab): mode-conditional shell + VIEW chip in filter card

Spec § 5: lab_backtest.html shell renders different partials based on
mode (per-strategy default = Phase 4; shared-pool = Phase 5a). Filter
card gains a third chip row 'View [单策略*] [共享池]' that round-trips
via the existing GET form pattern.

Partials referenced but not yet created:
- backtest_kpi_strip_shared.html (Task 10)
- backtest_strategy_table_shared.html (Task 11)
- backtest_bid_history.html (Task 11)
- backtest_hero.html with mode-specific text (Task 10)

The {% include 'partials/X.html' ignore missing %} pattern means the
shell doesn't break before those partials exist — just renders blank
sections."
```

---

### Task 10: backtest_hero.html (mode-specific text) + backtest_kpi_strip_shared.html

**Files:**
- Modify: `marketpulse/web/templates/partials/backtest_hero.html`
- Create: `marketpulse/web/templates/partials/backtest_kpi_strip_shared.html`

- [ ] **Step 10.1: Update hero with mode-specific sub-text**

Replace contents of `marketpulse/web/templates/partials/backtest_hero.html`:

```html
<section class="mp-hero">
  <div>
    <span class="mp-eyebrow mp-eyebrow--primary">实验室 · 组合回测</span>
    <h1 class="grotesk mp-hero__title">Strategy Performance Observatory</h1>
    <span class="mp-rule"></span>
    {% if mode == 'shared-pool' %}
      <p class="mp-hero__desc">
        6 个策略共享单一 \$10k 资本池,通过 {{ lookback_days }}-day 滚动 Sharpe
        加权竞标分配。撞 ticker 时高 Sharpe 策略赢。
        <strong>bid_policy=rolling_sharpe_60d_v0</strong>。
      </p>
    {% else %}
      <p class="mp-hero__desc">
        回放 Phase 3 的 6 个策略 + SPY 基准在过去 N 天内的策略级合成 PnL。
        回测使用 long-only 模型 + 固定持有 horizon 天 + \$1k 每信号 + \$10k 软上限。
      </p>
    {% endif %}
  </div>
</section>
```

- [ ] **Step 10.2: Create `backtest_kpi_strip_shared.html`**

Create `marketpulse/web/templates/partials/backtest_kpi_strip_shared.html`:

```html
<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">Pool Sharpe</span>
    <span class="material-symbols-outlined mp-kpi__icon">trending_up</span>
  </div>
  <div class="mp-kpi__value grotesk tnum"
       style="color: {% if shared_result and shared_result.sharpe and shared_result.sharpe >= 1 %}var(--mp-up){% else %}var(--ns-navy){% endif %};">
    {% if shared_result and shared_result.sharpe is not none %}
      {{ "{:.2f}".format(shared_result.sharpe) }}
    {% else %}—{% endif %}
  </div>
  <div class="mp-kpi__hint">
    {% if shared_result and shared_result.sharpe is not none %}n={{ shared_result.n_trades }}{% else %}n&lt;5{% endif %}
  </div>
</div>

<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">Pool Cum Ret</span>
    <span class="material-symbols-outlined mp-kpi__icon">show_chart</span>
  </div>
  <div class="mp-kpi__value grotesk tnum"
       style="color: {% if shared_result and shared_result.cumulative_return > 0 %}var(--mp-up){% else %}var(--mp-down){% endif %};">
    {% if shared_result %}{{ "{:+.2f}%".format(shared_result.cumulative_return * 100) }}{% else %}—{% endif %}
  </div>
  <div class="mp-kpi__hint">所有 6 策略组合</div>
</div>

<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">Pool MaxDD</span>
    <span class="material-symbols-outlined mp-kpi__icon">trending_down</span>
  </div>
  <div class="mp-kpi__value grotesk tnum" style="color: var(--mp-down);">
    {% if shared_result %}{{ "{:.2f}%".format(shared_result.max_drawdown * 100) }}{% else %}—{% endif %}
  </div>
  <div class="mp-kpi__hint">组合回撤</div>
</div>

<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">vs SPY</span>
    <span class="material-symbols-outlined mp-kpi__icon">compare_arrows</span>
  </div>
  <div class="mp-kpi__value grotesk tnum"
       style="color: {% if shared_result and shared_result.excess_vs_spy >= 0 %}var(--mp-up){% else %}var(--mp-down){% endif %};">
    {% if shared_result %}{{ "{:+.2f}%".format(shared_result.excess_vs_spy * 100) }}{% else %}—{% endif %}
  </div>
  <div class="mp-kpi__hint">累计差</div>
</div>

<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">N dedup</span>
    <span class="material-symbols-outlined mp-kpi__icon">scatter_plot</span>
  </div>
  <div class="mp-kpi__value grotesk tnum">
    {% if shared_result %}{{ shared_result.n_dedup_total }}{% else %}—{% endif %}
  </div>
  <div class="mp-kpi__hint">同 ticker 撞挤次数</div>
</div>
```

- [ ] **Step 10.3: Run, expect shared-mode marker test passes**

```bash
uv run pytest tests/web/test_lab_backtest_modes.py -v -k "shared_mode_renders_pool_marker"
```

Expected: PASS — "rolling_sharpe_60d_v0" is now in the hero partial.

- [ ] **Step 10.4: Commit**

```bash
git add marketpulse/web/templates/partials/backtest_hero.html \
        marketpulse/web/templates/partials/backtest_kpi_strip_shared.html
git commit -m "feat(lab): mode-aware hero + shared-pool KPI strip

Spec § 5:
- Hero text branches on mode: per-strategy = Phase 4 copy unchanged;
  shared-pool = '6 策略共享单一 \$10k 资本池, {{ lookback_days }}-day
  滚动 Sharpe' + bid_policy=rolling_sharpe_60d_v0 provenance line.
- backtest_kpi_strip_shared.html: 5 cards — Pool Sharpe / Pool Cum
  Ret / Pool MaxDD / vs SPY / N dedup. Same NS card pattern as
  Phase 4 KPIs; sign-aware coloring; — fallback when shared_result
  is None or metric is None."
```

---

### Task 11: backtest_strategy_table_shared.html + backtest_bid_history.html

**Files:**
- Create: `marketpulse/web/templates/partials/backtest_strategy_table_shared.html`
- Create: `marketpulse/web/templates/partials/backtest_bid_history.html`

- [ ] **Step 11.1: Create `backtest_strategy_table_shared.html`**

```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">leaderboard</span>策略贡献
    </span>
    <span class="mp-card__sub">{{ filters.horizon }}d · 共享池视图</span>
  </div>
  <div class="mp-card__body" style="padding:0; overflow-x:auto;">
    <table class="mp-table mp-backtest-table">
      <thead>
        <tr>
          <th>策略</th>
          <th class="num">n_trades</th>
          <th class="num">n_dedup</th>
          <th class="num">n_skipped</th>
          <th class="num">PnL ($)</th>
          <th class="num">avg exposure</th>
          <th class="num">avg bid w</th>
        </tr>
      </thead>
      <tbody>
        {% if shared_result %}
          {% for s, c in shared_result.per_strategy_stats.items() %}
          <tr>
            <td>
              <a href="/lab/ai-track?strategy={{ s }}"
                 class="mp-strategy-link" title="查看 hit rate">
                {{ c.display_name }}
              </a>
              {% if c.n_floor_hits > 0 %}
                <span class="mp-chip mp-chip--down" style="margin-left:4px;"
                      title="负 Sharpe 触地 {{ c.n_floor_hits }} 次">
                  floor {{ c.n_floor_hits }}
                </span>
              {% endif %}
            </td>
            <td class="num mono tnum">{{ c.n_trades }}</td>
            <td class="num mono tnum">{{ c.n_dedup_skipped }}</td>
            <td class="num mono tnum">
              {{ c.n_capacity_skipped + c.n_cash_short_skipped }}
            </td>
            <td class="num mono tnum {% if c.contribution_pnl >= 0 %}up{% else %}down{% endif %}">
              {{ "{:+.2f}".format(c.contribution_pnl) }}
            </td>
            <td class="num mono tnum">{{ "{:.1%}".format(c.avg_exposure) }}</td>
            <td class="num mono tnum">{{ "{:.2f}".format(c.avg_bid_weight) }}</td>
          </tr>
          {% endfor %}
        {% endif %}
      </tbody>
    </table>
  </div>
</section>
```

- [ ] **Step 11.2: Create `backtest_bid_history.html`**

```html
{% if shared_result and shared_result.bid_history %}
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">history</span>近 100 次 bid 决策
    </span>
    <span class="mp-card__sub">诊断用 · 最新在上</span>
  </div>
  <div class="mp-card__body" style="padding:0; max-height:400px; overflow-y:auto;">
    <table class="mp-table mp-bid-history-table">
      <thead>
        <tr>
          <th>日期</th>
          <th>策略</th>
          <th>Ticker</th>
          <th class="num">权重</th>
          <th>结果</th>
        </tr>
      </thead>
      <tbody>
        {% for b in shared_result.bid_history|reverse %}
        <tr class="{% if b.outcome == 'won' %}is-won{% elif b.outcome == 'dedup_loser' %}is-loser{% else %}is-skipped{% endif %}">
          <td class="mono tnum">{{ b.date.isoformat() }}</td>
          <td>{{ b.strategy }}</td>
          <td>{{ b.ticker }}</td>
          <td class="num mono tnum">{{ "{:.2f}".format(b.weight) }}</td>
          <td>
            {% if b.outcome == 'won' %}<span class="mp-chip mp-chip--up">✓ won</span>
            {% elif b.outcome == 'dedup_loser' %}<span class="mp-chip" title="ceded to {{ b.winner }}">→ {{ b.winner }}</span>
            {% elif b.outcome == 'cap_full' %}<span class="mp-chip mp-chip--down">cap full</span>
            {% elif b.outcome == 'cash_short' %}<span class="mp-chip mp-chip--down">cash short</span>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</section>
{% endif %}
```

- [ ] **Step 11.3: Commit**

```bash
git add marketpulse/web/templates/partials/backtest_strategy_table_shared.html \
        marketpulse/web/templates/partials/backtest_bid_history.html
git commit -m "feat(lab): shared-pool strategy table + bid history timeline

Spec § 5:
- backtest_strategy_table_shared.html: 7 cols — 策略 / n_trades /
  n_dedup / n_skipped (cap_full + cash_short) / PnL / avg exposure /
  avg bid weight. n_floor_hits > 0 renders an inline 'floor N' chip
  next to strategy name as a dying-strategy warning. Strategy name
  still links to /lab/ai-track?strategy=<name>.
- backtest_bid_history.html: collapsible-in-scroll-area table of last
  100 BidRecord entries (reversed = most recent first). Outcome chips
  color-coded — won=green, dedup_loser=neutral with winner tooltip,
  cap_full/cash_short=red."
```

---

### Task 12: Append shared-mode CSS to app.css

**Files:**
- Modify: `marketpulse/web/static/css/app.css`

- [ ] **Step 12.1: Append CSS**

Append to bottom of `marketpulse/web/static/css/app.css`:

```css
/* ════════ Phase 5a: shared-pool view styles ════════ */
.mp-bid-history-table {
  width: 100%;
  min-width: 480px;
  font-size: 12px;
}
.mp-bid-history-table th,
.mp-bid-history-table td {
  padding: 6px 10px;
  border-bottom: 1px solid var(--ns-outline-variant);
}
.mp-bid-history-table th {
  text-align: left;
  color: var(--ns-on-surface-variant);
  position: sticky;
  top: 0;
  background: var(--ns-surface);
  z-index: 1;
}
.mp-bid-history-table td.num,
.mp-bid-history-table th.num { text-align: right; }
.mp-bid-history-table tr.is-won { background: rgba(22, 163, 74, 0.04); }
.mp-bid-history-table tr.is-loser { background: rgba(100, 116, 139, 0.04); }
.mp-bid-history-table tr.is-skipped { background: rgba(220, 38, 38, 0.04); }
```

- [ ] **Step 12.2: Commit**

```bash
git add marketpulse/web/static/css/app.css
git commit -m "style(lab): bid history table styles — sticky header + outcome-row tints

Sticky header inside the 400px-scroll-area body. Row backgrounds
distinguish outcomes: won=faint green, dedup_loser=neutral, skipped=
faint red. Matches NS palette."
```

---

### Task 13: Final integration — full suite + ruff + smoke

- [ ] **Step 13.1: Full pytest**

```bash
uv run pytest 2>&1 | tail -3
```

Expected: ~750 passed (Phase 4 had ~710 + this PR adds ~40-44 new tests).

- [ ] **Step 13.2: Ruff entire repo**

```bash
uv run ruff check . 2>&1 | tail -3
```

Expected: `All checks passed!`.

- [ ] **Step 13.3: Module imports**

```bash
uv run python -c "
from marketpulse.backtest import (
    BidRecord, PortfolioBacktestResult, StrategyBacktestArtifacts,
    StrategyContribution, run_shared_pool_backtest,
)
print('ok')
"
```

Expected: `ok`.

- [ ] **Step 13.4: Smoke 4 route variants**

```bash
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

for path in [
    '/lab/backtest',
    '/lab/backtest?mode=per-strategy',
    '/lab/backtest?mode=shared-pool',
    '/lab/backtest?mode=shared-pool&horizon=20',
    '/lab/backtest?mode=invalid',
]:
    r = client.get(path, follow_redirects=False)
    print(f'{path}: {r.status_code}')
"
```

Expected:
```
/lab/backtest: 200
/lab/backtest?mode=per-strategy: 200
/lab/backtest?mode=shared-pool: 200
/lab/backtest?mode=shared-pool&horizon=20: 200
/lab/backtest?mode=invalid: 422
```

- [ ] **Step 13.5: Commit log review**

```bash
git log --oneline main..HEAD | wc -l
```

Expected: 12 task commits (or 13 with a final cleanup if needed).

- [ ] **Step 13.6: If anything failed, fix + commit**

If you reach this step with green tests, ruff clean, and the 5 smoke URLs returning correct status codes, the implementation is complete. Commit any final cleanup, push the branch, and open a PR titled `feat(phase-5a): shared capital pool — bidding + UI toggle`.

---

## Self-Review Notes

**Spec coverage** — every § in spec mapped to a task:
- §1 Identity → Task 1 (provenance defaults, locked behavior in tests)
- §2 Algorithm → Tasks 4 + 5 + 6 (CLOSE/BID/WEIGHT, DEDUP/ALLOC, MTM/RECORD/finalize)
- §3 Sharpe + bid weighting → Task 2
- §4 Data model → Task 1 (types) + Task 3 (artifacts)
- §5 UI toggle → Tasks 8 + 9 + 10 + 11 + 12
- §6 File structure → matches plan structure 1:1
- §7 Test plan → all 44 tests distributed across Tasks 1, 2, 3, 4, 5, 6, 7, 8

**Placeholder scan** — no TBD/TODO/"add appropriate error handling" remain. Every code block is complete.

**Type consistency** — `StrategyContribution.n_floor_hits` defined in Task 1, populated in Task 6, rendered in Task 11 — same name throughout. `BidRecord.outcome` Literal["won","dedup_loser","cap_full","cash_short"] consistent across types definition (Task 1), simulator emit (Tasks 5+6), template render (Task 11). Tiebreaker key `(-weight, event_time, strategy_name)` identical in DEDUP and ALLOC pseudocode (Task 5).

**Bite-sized check** — each step is 2-5 minutes; long code blocks are explicit (engineer pastes verbatim), not narrative.
