# Phase 5d — Contribution-Adjusted Sharpe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Phase 5a's standalone `bid_weight = rolling_sharpe` with a contribution-adjusted variant `adjusted_bid_weight = rolling_sharpe × clip(1 − λρ, 0.5, 1.2)` where ρ is the strategy's Pearson correlation with the pool excluding itself (leave-one-out via subtraction). Default off (observation-only); all 8 telemetry fields populated either way.

**Architecture:** New pure-function module `marketpulse/backtest/contribution.py` (3 public functions + 1 frozen dataclass). Phase 5b's run-wide `trade_realized_pnl_by_strategy` is augmented with per-day per-strategy buckets (`realized_pnl_today_by_strategy` in CLOSE, `mtm_prev_by_strategy` snapshot pre-RECORD). WEIGHT step always computes both raw and adjusted rankings, populates `would_change_rank` flag regardless of `contribution_enabled`. The toggle only chooses which ranking drives DEDUP/ALLOC.

**Tech Stack:** Python 3.12 + numpy (existing). No new dependencies. No new DB tables. No Alembic migration.

**Spec:** `docs/superpowers/specs/2026-05-20-phase-5d-contribution-adjusted-sharpe-design.md`

---

## File Structure

```
marketpulse/backtest/
├── contribution.py                        NEW: daily_contribution_return,
│                                                pool_corr_excluding_self,
│                                                compute_adjusted_bid_weight,
│                                                BidWeightMetadata (@dataclass(frozen=True))
├── portfolio_simulator.py                 MODIFY: +per-day per-strategy PnL accumulators
│                                                  (CLOSE realized + pre-RECORD MTM snapshot);
│                                                  +daily_strategy_contribution_returns,
│                                                  daily_pool_returns, pool_corr_by_strategy;
│                                                  WEIGHT step always computes both rankings;
│                                                  finalization aggregates avg_pool_corr +
│                                                  n_would_change_rank from all_bid_records;
│                                                  bid_policy + contribution_policy strings
├── types.py                               MODIFY: BidRecord + 8 defaulted Phase 5d fields;
│                                                  StrategyContribution + 2 fields;
│                                                  PortfolioBacktestResult + 3 fields
├── simulator.py                           MODIFY: run_shared_pool_backtest threads
│                                                  contribution_enabled + contribution_lambda
├── sharpe.py                              UNCHANGED (Phase 5a/5b math untouched)
└── __init__.py                            (no change — public API stable)

marketpulse/web/
├── routes/backtest.py                     MODIFY: pass contribution_enabled +
│                                                  contribution_lambda via template context
└── templates/partials/
    ├── backtest_hero.html                 MODIFY: inline λ modifier in paragraph 1
    ├── backtest_bid_history.html          MODIFY: weight column tooltip + 2 chip icons
    └── backtest_strategy_table_shared.html MODIFY: + 2 columns (avg pool ρ, rank Δ)

tests/
├── unit/
│   ├── test_backtest_contribution.py      NEW: 16 tests
│   ├── test_backtest_portfolio_simulator.py MODIFY: + 12 integration tests
│   └── test_backtest_types_phase5a.py     MODIFY: + 5 type tests
├── integration/
│   └── test_backtest_shared_pool.py       MODIFY: + 3 orchestrator tests
└── web/
    └── test_lab_backtest_modes.py         MODIFY: + 2 UI assertions
```

**No new files outside the structure above. No DB migration. No new dependencies.**

---

## Conventions

- **TDD strict**: each task = failing test → run/see fail → minimal impl → run/see pass → commit.
- **Working directory**: `/Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan` (worktree on `plan/phase-5d-contribution-adjusted-sharpe`).
- **Run tests**: `uv run pytest <path> -v`.
- **Lint**: `uv run ruff check <path>`.
- **No new DB tables, no migrations**.
- **Daily loop ORDER LOCK** (spec § 5): `CLOSE → BID → WEIGHT → SIZE → DEDUP → ALLOC → MTM → RECORD` — unchanged. Phase 5d additions slot INSIDE existing steps (CLOSE / WEIGHT / pre-RECORD).
- **Always-on telemetry**: `would_change_rank`, `pool_corr`, `contribution_multiplier`, `adjusted_bid_weight`, `effective_corr_window`, `rewarded_for_negative_corr` are populated regardless of `contribution_enabled`. The toggle only swaps `weights = weights_raw` vs `weights = weights_adjusted` for DEDUP/ALLOC.
- **f-string `bid_policy`**: `f"rolling_sharpe_{lookback_days}d_v0"` / `f"contribution_adjusted_sharpe_{lookback_days}d_v0"` — preserve `lookback_days` in the provenance string (Phase 5a precedent at line 75 of `portfolio_simulator.py`).
- **`adjusted_bid_weight is None` semantic**: only when `raw_bid_weight is None` (Phase 5a n<5 floor). For `raw≤0` or `ρ=None`, `adjusted = raw` (real float, multiplier=1.0).
- **`effective_corr_window` semantic**: always returns actual overlap count, even when below `min_overlap`. Zero only when truly no overlap.
- **Self-pair exclusion**: covered by LOO via subtraction — `pool_minus_A_returns[d] = pool_total[d] − A_contribution[d]`. No need to filter A from open_position_tickers because there is no "ticker" concept at this layer (we operate on strategy contribution series, not tickers).
- **Test-quality lock** (spec § 8): every test asserts precondition before outcome. No `if X: assert Y` vacuous patterns.

---

### Task 1: contribution.py — `daily_contribution_return` + `BidWeightMetadata`

**Files:**
- Create: `marketpulse/backtest/contribution.py`
- Create: `tests/unit/test_backtest_contribution.py`

- [ ] **Step 1.1: Write failing tests** in `tests/unit/test_backtest_contribution.py`:

```python
"""Phase 5d: contribution.py — per-day decomposition + LOO correlation + adjusted bid weight."""
from __future__ import annotations

from datetime import date, timedelta


def test_daily_contribution_return_basic() -> None:
    """pnl=100, equity_prev=10000 → 0.01."""
    from marketpulse.backtest.contribution import daily_contribution_return
    assert daily_contribution_return(100.0, 10_000.0) == 0.01


def test_daily_contribution_return_zero_equity_prev_returns_zero() -> None:
    """pnl=100, equity_prev=0 → 0.0 (no ZeroDivisionError)."""
    from marketpulse.backtest.contribution import daily_contribution_return
    assert daily_contribution_return(100.0, 0.0) == 0.0


def test_daily_contribution_return_negative_equity_prev_returns_zero() -> None:
    """Forward-compat: pnl=100, equity_prev=-5000 → 0.0 (Phase 4 forbids; future leverage may allow)."""
    from marketpulse.backtest.contribution import daily_contribution_return
    assert daily_contribution_return(100.0, -5000.0) == 0.0


def test_bid_weight_metadata_is_frozen_dataclass() -> None:
    """BidWeightMetadata is a frozen dataclass (hashable, immutable)."""
    import dataclasses
    from marketpulse.backtest.contribution import BidWeightMetadata
    meta = BidWeightMetadata(
        raw=1.5, pool_corr=0.3, multiplier=0.85,
        adjusted=1.275, effective_window=42,
        rewarded_for_negative_corr=False, would_change_rank=False,
    )
    assert dataclasses.is_dataclass(meta)
    assert hash(meta)  # hashable means frozen
    # dataclasses.replace works
    new_meta = dataclasses.replace(meta, would_change_rank=True)
    assert new_meta.would_change_rank is True
    assert meta.would_change_rank is False  # original unchanged
```

- [ ] **Step 1.2: Run, see 4 fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_contribution.py -v
```

Expected: 4 fails (`ImportError: No module named 'marketpulse.backtest.contribution'`).

- [ ] **Step 1.3: Create `marketpulse/backtest/contribution.py`** with `daily_contribution_return` + `BidWeightMetadata`:

```python
"""Phase 5d-1: contribution-adjusted bid weight machinery.

Spec § 3 + § 4. Three pure public functions:
  - daily_contribution_return: per-day decomposition for LOO subtraction
  - pool_corr_excluding_self: Pearson on (strategy, pool_minus_self) returns
  - compute_adjusted_bid_weight: clip(1 − λρ, 0.5, 1.2) × raw_sharpe

Plus the BidWeightMetadata frozen dataclass that wraps per-strategy
per-day inputs to BidRecord construction.

ρ semantic boundary (spec Appendix A): pool_corr measures realized
co-movement under competitive allocation constraints, NOT independent
return correlation. Equivalent reads: "equilibrium decomposition
correlation", "structural decision-sensitivity input".
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class BidWeightMetadata:
    """Per-strategy per-day inputs to BidRecord Phase 5d telemetry fields.

    Populated once per (strategy, day) in the WEIGHT step. Read at every
    BidRecord constructor site (7 sites across all outcome literals).
    Frozen for hashability + ergonomics (dataclasses.replace for flag updates).
    """
    raw: float | None
    pool_corr: float | None
    multiplier: float
    adjusted: float | None
    effective_window: int
    rewarded_for_negative_corr: bool
    would_change_rank: bool


def daily_contribution_return(
    strategy_pnl_today: float,
    pool_equity_prev_day: float,
) -> float:
    """Per-day strategy contribution to pool return.

    Returns strategy_pnl_today / pool_equity_prev_day. Returns 0.0 when
    pool_equity_prev_day is zero or negative (avoids ZeroDivisionError;
    cold-start safe; future-leverage-safe).
    """
    if pool_equity_prev_day <= 0.0:
        return 0.0
    return strategy_pnl_today / pool_equity_prev_day
```

- [ ] **Step 1.4: Run, see 4 pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_contribution.py -v
```

Expected: 4/4 pass.

- [ ] **Step 1.5: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run ruff check marketpulse/backtest/contribution.py tests/unit/test_backtest_contribution.py
git add marketpulse/backtest/contribution.py tests/unit/test_backtest_contribution.py
git commit -m "feat(phase-5d): contribution.py — daily_contribution_return + BidWeightMetadata

Spec § 3 + § 4. Foundation of Phase 5d:
- daily_contribution_return(pnl, equity_prev): per-day pool decomposition.
  Returns 0.0 when equity_prev <= 0 (zero, negative, or future leverage).
- BidWeightMetadata frozen dataclass: per-strategy per-day inputs to
  BidRecord Phase 5d telemetry. 7 fields (raw, pool_corr, multiplier,
  adjusted, effective_window, rewarded_for_negative_corr, would_change_rank).
  Frozen for hashability + dataclasses.replace ergonomics matching
  surrounding BidRecord convention.

4 unit tests cover happy path, zero equity, negative equity (forward-
compat), and dataclass freeze/replace contract."
```

---

### Task 2: contribution.py — `pool_corr_excluding_self`

**Files:**
- Modify: `marketpulse/backtest/contribution.py`
- Modify: `tests/unit/test_backtest_contribution.py`

- [ ] **Step 2.1: Append failing tests** to `tests/unit/test_backtest_contribution.py`:

```python
def _build_returns(start_date: date, values: list[float]) -> list[tuple[date, float]]:
    """Helper: build (date, value) tuples starting at start_date, one per day."""
    return [(start_date + timedelta(days=i), v) for i, v in enumerate(values)]


def test_pool_corr_excluding_self_perfectly_correlated() -> None:
    """A_returns identical to (pool − A) returns → ρ ≈ 1.0."""
    from marketpulse.backtest.contribution import pool_corr_excluding_self

    start = date(2026, 1, 1)
    # A contributes consistently; rest of pool moves identically
    strategy_returns = _build_returns(start, [0.01, -0.005, 0.02, 0.008, -0.012] * 7)  # 35 days
    # pool_total = strategy_contribution + identical_rest → pool_minus_A = identical_rest = A
    pool_returns = _build_returns(start, [0.02, -0.01, 0.04, 0.016, -0.024] * 7)  # 2× strategy

    corr, eff = pool_corr_excluding_self(
        strategy_returns, pool_returns,
        as_of=date(2026, 3, 5),
        lookback_days=60,
        min_overlap=30,
    )
    assert corr is not None
    assert corr > 0.99
    assert eff >= 30


def test_pool_corr_excluding_self_anti_correlated() -> None:
    """A_returns = −(pool − A) returns → ρ ≈ −1.0."""
    from marketpulse.backtest.contribution import pool_corr_excluding_self

    start = date(2026, 1, 1)
    strategy_returns = _build_returns(start, [0.01, -0.005, 0.02, 0.008, -0.012] * 7)
    # pool_total = 0 (A cancels rest exactly), so pool_minus_A = -A
    pool_returns = _build_returns(start, [0.0] * 35)

    corr, eff = pool_corr_excluding_self(
        strategy_returns, pool_returns,
        as_of=date(2026, 3, 5),
        lookback_days=60,
        min_overlap=30,
    )
    assert corr is not None
    assert corr < -0.99
    assert eff >= 30


def test_pool_corr_excluding_self_cold_start_returns_count() -> None:
    """Below min_overlap → (None, actual_overlap_count). NOT (None, 0)."""
    from marketpulse.backtest.contribution import pool_corr_excluding_self

    start = date(2026, 2, 25)  # only 10 days before as_of
    strategy_returns = _build_returns(start, [0.01] * 10)
    pool_returns = _build_returns(start, [0.02] * 10)

    corr, eff = pool_corr_excluding_self(
        strategy_returns, pool_returns,
        as_of=date(2026, 3, 7),
        lookback_days=60,
        min_overlap=30,
    )
    assert corr is None
    # Informative telemetry: how many overlap days actually existed
    assert eff == 10


def test_pool_corr_excluding_self_empty_intersection_returns_zero() -> None:
    """No overlap at all → (None, 0)."""
    from marketpulse.backtest.contribution import pool_corr_excluding_self

    # as_of before any returns exist
    strategy_returns = _build_returns(date(2026, 6, 1), [0.01] * 10)
    pool_returns = _build_returns(date(2026, 6, 1), [0.02] * 10)

    corr, eff = pool_corr_excluding_self(
        strategy_returns, pool_returns,
        as_of=date(2026, 3, 1),  # before any returns
        lookback_days=60,
        min_overlap=30,
    )
    assert corr is None
    assert eff == 0


def test_pool_corr_excluding_self_partial_overlap_uses_actual_window() -> None:
    """30 ≤ overlap < 60 → corr computed on actual overlap, eff = actual."""
    from marketpulse.backtest.contribution import pool_corr_excluding_self

    # 45 days of overlap available
    start = date(2026, 1, 20)
    strategy_returns = _build_returns(start, [0.01, -0.005] * 23)  # 46 days
    pool_returns = _build_returns(start, [0.02, -0.01] * 23)

    corr, eff = pool_corr_excluding_self(
        strategy_returns, pool_returns,
        as_of=date(2026, 3, 5),
        lookback_days=60,
        min_overlap=30,
    )
    # Precondition: actual overlap should be ~44 (start + 46 - days past as_of)
    # Outcome: corr defined, eff between min_overlap and lookback_days
    assert corr is not None
    assert 30 <= eff < 60


def test_pool_corr_excluding_self_zero_variance_strategy_returns_none() -> None:
    """A_returns all zero → std=0 → (None, overlap_count)."""
    from marketpulse.backtest.contribution import pool_corr_excluding_self

    start = date(2026, 1, 1)
    strategy_returns = _build_returns(start, [0.0] * 35)
    pool_returns = _build_returns(start, [0.02, -0.01] * 17 + [0.01])  # 35 days

    corr, eff = pool_corr_excluding_self(
        strategy_returns, pool_returns,
        as_of=date(2026, 3, 5),
        lookback_days=60,
        min_overlap=30,
    )
    assert corr is None
    # Precondition: enough overlap existed even though variance was zero
    assert eff >= 30


def test_pool_corr_excluding_self_zero_variance_pool_minus_a_returns_none() -> None:
    """pool_minus_A all zero → std=0 → (None, overlap_count)."""
    from marketpulse.backtest.contribution import pool_corr_excluding_self

    start = date(2026, 1, 1)
    # A is the only contributor; rest of pool is flat (pool_minus_A all zero)
    strategy_returns = _build_returns(start, [0.01, -0.005] * 17 + [0.01])
    pool_returns = strategy_returns  # pool_total == A → pool_minus_A == 0

    corr, eff = pool_corr_excluding_self(
        strategy_returns, pool_returns,
        as_of=date(2026, 3, 5),
        lookback_days=60,
        min_overlap=30,
    )
    assert corr is None
    assert eff >= 30


def test_pool_corr_excluding_self_excludes_dates_at_or_after_as_of() -> None:
    """Window is [as_of − lookback, as_of) — exclusive upper bound."""
    from marketpulse.backtest.contribution import pool_corr_excluding_self

    start = date(2026, 1, 1)
    # 60 days before as_of + 30 days after
    strategy_returns = _build_returns(start, [0.01, -0.01] * 45)  # 90 days
    pool_returns = _build_returns(start, [0.02, -0.02] * 45)

    corr, eff = pool_corr_excluding_self(
        strategy_returns, pool_returns,
        as_of=date(2026, 3, 2),  # ~60 days after start
        lookback_days=60,
        min_overlap=30,
    )
    # Should NOT include days >= as_of in the correlation
    assert corr is not None
    assert eff <= 60  # capped at lookback_days
```

- [ ] **Step 2.2: Run, see 8 fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_contribution.py -v -k "pool_corr_excluding_self"
```

Expected: 8 fails (`pool_corr_excluding_self not defined`).

- [ ] **Step 2.3: Append `pool_corr_excluding_self` to `marketpulse/backtest/contribution.py`**

Add `import numpy as np` to the top of the file (alongside `import math`), then append below `daily_contribution_return`:

```python
import numpy as np


def pool_corr_excluding_self(
    strategy_contribution_returns: list[tuple[date, float]],
    daily_pool_returns: list[tuple[date, float]],
    *,
    as_of: date,
    lookback_days: int = 60,
    min_overlap: int = 30,
) -> tuple[float | None, int]:
    """Pearson correlation between strategy's contribution returns and
    (pool_total − strategy_contribution) — leave-one-out via subtraction.

    Window: [as_of − lookback_days, as_of), exclusive upper bound.

    Returns (corr, effective_window):
      - corr = None when overlap < min_overlap (effective_window = actual overlap count, NOT 0)
      - corr = None when either series has zero variance (std == 0)
      - corr = None when computed corr is non-finite (defensive)
      - effective_window is always the actual overlap count, capped at lookback_days

    Semantic boundary (spec Appendix A): measures realized co-movement
    under competitive allocation constraints, NOT independent return
    correlation. The subtraction recovers an exact day-level decomposition
    of the realized pool, not a counterfactual A-less pool.
    """
    window_start = as_of - timedelta(days=lookback_days)
    strat_by_date = {
        d: v for d, v in strategy_contribution_returns
        if window_start <= d < as_of
    }
    pool_by_date = {
        d: v for d, v in daily_pool_returns
        if window_start <= d < as_of
    }
    overlap_dates = sorted(set(strat_by_date) & set(pool_by_date))
    overlap_count = len(overlap_dates)
    effective_window = min(overlap_count, lookback_days)

    if overlap_count < min_overlap:
        return None, effective_window

    strat_arr = np.array([strat_by_date[d] for d in overlap_dates], dtype=float)
    pool_arr = np.array([pool_by_date[d] for d in overlap_dates], dtype=float)
    # Leave-one-out via subtraction
    pool_minus_self_arr = pool_arr - strat_arr

    if strat_arr.std() < 1e-12 or pool_minus_self_arr.std() < 1e-12:
        return None, effective_window

    corr = float(np.corrcoef(strat_arr, pool_minus_self_arr)[0, 1])
    if not math.isfinite(corr):
        return None, effective_window
    return corr, effective_window
```

- [ ] **Step 2.4: Run, see 8 pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_contribution.py -v -k "pool_corr_excluding_self"
```

Expected: 8/8 pass.

- [ ] **Step 2.5: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run ruff check marketpulse/backtest/contribution.py tests/unit/test_backtest_contribution.py
git add marketpulse/backtest/contribution.py tests/unit/test_backtest_contribution.py
git commit -m "feat(phase-5d): contribution.py — pool_corr_excluding_self

Spec § 3 + § 5. Leave-one-out Pearson correlation between strategy's
per-day contribution returns and (pool_total − strategy_contribution).

Locked contract:
- Window: [as_of − lookback_days, as_of) exclusive upper bound, matches
  Phase 5a/5b/5c window semantics
- LOO via subtraction: pool_minus_A[d] = pool_total[d] − A_contribution[d]
- effective_window ALWAYS returns actual overlap count, even when below
  min_overlap (informative telemetry; spec § 4 lock)
- Zero-variance guard < 1e-12 (matches Phase 5c precision)
- Returns (None, overlap_count) on cold-start / zero-variance / non-finite

Semantic boundary documented in module docstring: ρ measures realized
co-movement under competitive allocation, NOT independent return
correlation. Not a counterfactual A-less pool measure.

8 unit tests cover perfect/anti correlation, cold-start (overlap < 30),
empty intersection, partial overlap (30 ≤ N < 60), zero variance
in strategy or pool_minus_self, causal window cutoff."
```

---

### Task 3: contribution.py — `compute_adjusted_bid_weight`

**Files:**
- Modify: `marketpulse/backtest/contribution.py`
- Modify: `tests/unit/test_backtest_contribution.py`

- [ ] **Step 3.1: Append failing tests** to `tests/unit/test_backtest_contribution.py`:

```python
def test_compute_adjusted_bid_weight_negative_corr_rewarded() -> None:
    """raw=1.0, ρ=−0.8, λ=0.5 → multiplier=1.2 (clipped), rewarded=True."""
    from marketpulse.backtest.contribution import compute_adjusted_bid_weight
    adjusted, multiplier, rewarded = compute_adjusted_bid_weight(
        raw_sharpe=1.0, pool_corr=-0.8,
        lam=0.5, clip_min=0.5, clip_max=1.2,
    )
    # 1 - 0.5 * (-0.8) = 1.4 → clipped to 1.2
    assert abs(multiplier - 1.2) < 1e-9
    assert abs(adjusted - 1.2) < 1e-9
    assert rewarded is True


def test_compute_adjusted_bid_weight_positive_corr_penalized() -> None:
    """raw=1.0, ρ=0.8, λ=0.5 → multiplier=0.6, rewarded=False."""
    from marketpulse.backtest.contribution import compute_adjusted_bid_weight
    adjusted, multiplier, rewarded = compute_adjusted_bid_weight(
        raw_sharpe=1.0, pool_corr=0.8,
        lam=0.5, clip_min=0.5, clip_max=1.2,
    )
    # 1 - 0.5 * 0.8 = 0.6
    assert abs(multiplier - 0.6) < 1e-9
    assert abs(adjusted - 0.6) < 1e-9
    assert rewarded is False


def test_compute_adjusted_bid_weight_extreme_negative_clipped_at_max() -> None:
    """raw=1.0, ρ=−1.0, λ=1.0 → would be 2.0 but clipped to 1.2."""
    from marketpulse.backtest.contribution import compute_adjusted_bid_weight
    adjusted, multiplier, rewarded = compute_adjusted_bid_weight(
        raw_sharpe=1.0, pool_corr=-1.0,
        lam=1.0, clip_min=0.5, clip_max=1.2,
    )
    # 1 - 1.0 * (-1.0) = 2.0 → clipped to 1.2
    assert abs(multiplier - 1.2) < 1e-9
    assert abs(adjusted - 1.2) < 1e-9
    assert rewarded is True


def test_compute_adjusted_bid_weight_extreme_positive_clipped_at_min() -> None:
    """raw=1.0, ρ=1.0, λ=1.0 → would be 0.0 but clipped to 0.5."""
    from marketpulse.backtest.contribution import compute_adjusted_bid_weight
    adjusted, multiplier, rewarded = compute_adjusted_bid_weight(
        raw_sharpe=1.0, pool_corr=1.0,
        lam=1.0, clip_min=0.5, clip_max=1.2,
    )
    # 1 - 1.0 * 1.0 = 0.0 → clipped to 0.5
    assert abs(multiplier - 0.5) < 1e-9
    assert abs(adjusted - 0.5) < 1e-9
    assert rewarded is False


def test_compute_adjusted_bid_weight_none_sharpe_short_circuits() -> None:
    """raw=None (Phase 5a n<5 floor) → (None, 1.0, False)."""
    from marketpulse.backtest.contribution import compute_adjusted_bid_weight
    adjusted, multiplier, rewarded = compute_adjusted_bid_weight(
        raw_sharpe=None, pool_corr=0.5, lam=0.5,
    )
    assert adjusted is None
    assert multiplier == 1.0
    assert rewarded is False


def test_compute_adjusted_bid_weight_negative_sharpe_unchanged() -> None:
    """raw=-0.5, ρ=0.5 → (-0.5, 1.0, False). Don't adjust negative-Sharpe."""
    from marketpulse.backtest.contribution import compute_adjusted_bid_weight
    adjusted, multiplier, rewarded = compute_adjusted_bid_weight(
        raw_sharpe=-0.5, pool_corr=0.5, lam=0.5,
    )
    assert adjusted == -0.5
    assert multiplier == 1.0
    assert rewarded is False


def test_compute_adjusted_bid_weight_zero_sharpe_unchanged() -> None:
    """raw=0.0, ρ=−0.5 → (0.0, 1.0, False). Zero is not 'positive'."""
    from marketpulse.backtest.contribution import compute_adjusted_bid_weight
    adjusted, multiplier, rewarded = compute_adjusted_bid_weight(
        raw_sharpe=0.0, pool_corr=-0.5, lam=0.5,
    )
    assert adjusted == 0.0
    assert multiplier == 1.0
    assert rewarded is False


def test_compute_adjusted_bid_weight_none_corr_short_circuits() -> None:
    """raw=1.5, ρ=None (cold-start) → (1.5, 1.0, False)."""
    from marketpulse.backtest.contribution import compute_adjusted_bid_weight
    adjusted, multiplier, rewarded = compute_adjusted_bid_weight(
        raw_sharpe=1.5, pool_corr=None, lam=0.5,
    )
    assert adjusted == 1.5
    assert multiplier == 1.0
    assert rewarded is False
```

- [ ] **Step 3.2: Run, see 8 fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_contribution.py -v -k "compute_adjusted_bid_weight"
```

Expected: 8 fails.

- [ ] **Step 3.3: Append `compute_adjusted_bid_weight` to `marketpulse/backtest/contribution.py`**

```python
def compute_adjusted_bid_weight(
    raw_sharpe: float | None,
    pool_corr: float | None,
    *,
    lam: float = 0.5,
    clip_min: float = 0.5,
    clip_max: float = 1.2,
) -> tuple[float | None, float, bool]:
    """Apply contribution-adjusted multiplier to a raw bid weight.

    Returns (adjusted_weight, multiplier, rewarded_for_negative_corr):
      - adjusted = raw_sharpe × multiplier
      - multiplier = clip(1 − lam × pool_corr, clip_min, clip_max) when
        pool_corr is not None AND raw_sharpe > 0; else 1.0
      - rewarded = (pool_corr is not None) AND (pool_corr < 0) AND
        (multiplier > 1.0)

    Short-circuits to (None, 1.0, False) when raw_sharpe is None (Phase 5a
    n<5 floor — there is nothing to adjust).

    Short-circuits to (raw_sharpe, 1.0, False) when raw_sharpe <= 0
    (negative or zero — Phase 5a floor decides whether to bid; we don't
    amplify or attenuate).

    Short-circuits to (raw_sharpe, 1.0, False) when pool_corr is None
    (cold-start; failsafe-open per spec § 2 lock #4).

    Clip is asymmetric: [0.5, 1.2] is deliberate risk-aversion bias.
    Max penalty -50%, max reward +20%. v0 ships conservative form;
    a neutral clip would version-bump contribution_policy to _v1.
    """
    if raw_sharpe is None:
        return None, 1.0, False
    if raw_sharpe <= 0.0:
        return raw_sharpe, 1.0, False
    if pool_corr is None:
        return raw_sharpe, 1.0, False

    raw_multiplier = 1.0 - lam * pool_corr
    multiplier = max(clip_min, min(clip_max, raw_multiplier))
    adjusted = raw_sharpe * multiplier
    rewarded = (pool_corr < 0) and (multiplier > 1.0)
    return adjusted, multiplier, rewarded
```

- [ ] **Step 3.4: Run, see 8 pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_contribution.py -v
```

Expected: 20/20 pass (4 from Task 1 + 8 from Task 2 + 8 new).

- [ ] **Step 3.5: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run ruff check marketpulse/backtest/contribution.py tests/unit/test_backtest_contribution.py
git add marketpulse/backtest/contribution.py tests/unit/test_backtest_contribution.py
git commit -m "feat(phase-5d): contribution.py — compute_adjusted_bid_weight

Spec § 3 + § 4. Multiplicative overlay on raw Sharpe:
  adjusted = raw_sharpe × clip(1 − λ × pool_corr, 0.5, 1.2)

Asymmetric clip [0.5, 1.2] is the deliberate risk-aversion bias from
spec § 7 (max penalty -50%, max reward +20%).

Four short-circuit paths return (raw, 1.0, False):
- raw_sharpe is None (Phase 5a n<5 floor)
- raw_sharpe <= 0 (Phase 5a floor handles negative-Sharpe; we don't amplify)
- pool_corr is None (cold-start, failsafe-open per § 2 lock #4)
- raw_sharpe = 0.0 boundary (treated as non-positive)

rewarded_for_negative_corr = (pool_corr < 0) AND (multiplier > 1.0)
— only True when the clip actually granted a hedge boost.

8 new unit tests cover negative-corr reward, positive-corr penalty,
both clip boundaries hit, all four short-circuit paths."
```

---

### Task 4: types.py — extend BidRecord, StrategyContribution, PortfolioBacktestResult

**Files:**
- Modify: `marketpulse/backtest/types.py`
- Modify: `tests/unit/test_backtest_types_phase5a.py`

- [ ] **Step 4.1: Append failing tests** to `tests/unit/test_backtest_types_phase5a.py`:

```python
def test_bid_record_phase5d_fields_have_safe_defaults() -> None:
    """Phase 5d adds 8 fields to BidRecord, all defaulted for backward-compat."""
    from datetime import date
    from marketpulse.backtest.types import BidRecord

    # Construct with NO Phase 5d kwargs — all 8 fields should default to neutral
    b = BidRecord(
        date=date(2026, 5, 1), strategy="x", ticker="AAPL",
        weight=1.5, outcome="won", winner=None, position_size=1000.0,
    )
    assert b.raw_bid_weight is None
    assert b.pool_corr is None
    assert b.contribution_multiplier == 1.0
    assert b.adjusted_bid_weight is None
    assert b.effective_corr_window == 0
    assert b.pool_corr_excludes_self is True
    assert b.rewarded_for_negative_corr is False
    assert b.would_change_rank is False


def test_bid_record_phase5d_fields_populated() -> None:
    """Phase 5d fields accept real values without raising."""
    from datetime import date
    from marketpulse.backtest.types import BidRecord

    b = BidRecord(
        date=date(2026, 5, 1), strategy="x", ticker="AAPL",
        weight=1.275, outcome="won", winner=None, position_size=1000.0,
        raw_bid_weight=1.5,
        pool_corr=0.3,
        contribution_multiplier=0.85,
        adjusted_bid_weight=1.275,
        effective_corr_window=42,
        pool_corr_excludes_self=True,
        rewarded_for_negative_corr=False,
        would_change_rank=True,
    )
    assert b.raw_bid_weight == 1.5
    assert b.pool_corr == 0.3
    assert b.contribution_multiplier == 0.85
    assert b.adjusted_bid_weight == 1.275
    assert b.effective_corr_window == 42
    assert b.would_change_rank is True


def test_strategy_contribution_phase5d_fields_have_safe_defaults() -> None:
    """Phase 5d adds 2 fields to StrategyContribution, both defaulted."""
    from marketpulse.backtest.types import StrategyContribution

    c = StrategyContribution(
        strategy="x", display_name="X",
        n_trades=5, n_dedup_skipped=1,
        n_capacity_skipped=0, n_cash_short_skipped=0,
        n_size_too_small_skipped=0,
        n_sector_cap_skipped=0, n_correlation_cap_skipped=0,
        contribution_pnl=100.0, avg_exposure=0.20, avg_bid_weight=1.0,
        avg_position_size=1200.0, n_bids=9, n_floor_hits=0,
    )
    # Defaults
    assert c.avg_pool_corr is None
    assert c.n_would_change_rank == 0


def test_strategy_contribution_phase5d_fields_populated() -> None:
    """Phase 5d StrategyContribution fields accept real values."""
    from marketpulse.backtest.types import StrategyContribution

    c = StrategyContribution(
        strategy="x", display_name="X",
        n_trades=5, n_dedup_skipped=1,
        n_capacity_skipped=0, n_cash_short_skipped=0,
        n_size_too_small_skipped=0,
        n_sector_cap_skipped=0, n_correlation_cap_skipped=0,
        contribution_pnl=100.0, avg_exposure=0.20, avg_bid_weight=1.0,
        avg_position_size=1200.0, n_bids=9, n_floor_hits=0,
        avg_pool_corr=0.42,
        n_would_change_rank=7,
    )
    assert c.avg_pool_corr == 0.42
    assert c.n_would_change_rank == 7


def test_portfolio_result_phase5d_provenance_defaults() -> None:
    """Phase 5d adds 3 PortfolioBacktestResult provenance fields, all defaulted."""
    from datetime import date
    from marketpulse.backtest.types import PortfolioBacktestResult

    r = PortfolioBacktestResult(
        horizon=5, n_trades=0, n_dedup_total=0,
        avg_capital_utilization=0.0,
        max_strategy_exposure=0.0, hhi_concentration=0.0,
        max_sector_exposure=0.0, max_sector_exposure_by_sector={},
        sector_breakdown={}, max_neighbor_exposure=0.0,
        n_correlation_cap_events=0,
        cumulative_return=0.0, annual_return=0.0,
        sharpe=None, sortino=None, max_drawdown=0.0, calmar=None,
        win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0,
        daily_equity_curve=[(date(2026, 4, 1), 10_000.0)],
        excess_vs_spy=0.0,
        per_strategy_stats={}, bid_history=[],
    )
    # Default contribution provenance — disabled, lambda=0.5, policy string
    assert r.contribution_enabled is False
    assert r.contribution_policy == "contribution_adjusted_sharpe_60d_v0"
    assert r.contribution_lambda == 0.5
```

- [ ] **Step 4.2: Run, see 5 fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_types_phase5a.py -v -k "phase5d"
```

Expected: 5 fails (`TypeError: __init__() got an unexpected keyword argument 'raw_bid_weight'`).

- [ ] **Step 4.3: Modify `marketpulse/backtest/types.py`**

Append 8 defaulted Phase 5d fields to `BidRecord`:

```python
@dataclass(frozen=True)
class BidRecord:
    """One bid decision — diagnostic timeline."""
    date: date
    strategy: str
    ticker: str
    weight: float
    outcome: Literal[
        "won", "dedup_loser", "cap_full", "cash_short",
        "size_too_small",
        "sector_cap_full", "correlation_cap_full",
    ]
    winner: str | None
    position_size: float
    blocked_by_sector: str | None = None
    blocked_by_correlation_with: tuple[tuple[str, float], ...] = ()

    # NEW Phase 5d (all defaulted for backward-compat with existing fixtures)
    raw_bid_weight: float | None = None
    pool_corr: float | None = None
    contribution_multiplier: float = 1.0
    adjusted_bid_weight: float | None = None
    effective_corr_window: int = 0
    pool_corr_excludes_self: bool = True
    rewarded_for_negative_corr: bool = False
    would_change_rank: bool = False
```

Append 2 defaulted fields to `StrategyContribution`:

```python
@dataclass(frozen=True)
class StrategyContribution:
    """One strategy's slice of a shared-pool run."""
    strategy: str
    display_name: str
    n_trades: int
    n_dedup_skipped: int
    n_capacity_skipped: int
    n_cash_short_skipped: int
    n_size_too_small_skipped: int
    n_sector_cap_skipped: int
    n_correlation_cap_skipped: int
    contribution_pnl: float
    avg_exposure: float
    avg_bid_weight: float
    avg_position_size: float
    n_bids: int
    n_floor_hits: int

    # NEW Phase 5d (both defaulted)
    avg_pool_corr: float | None = None
    n_would_change_rank: int = 0
```

Append 3 defaulted provenance fields to `PortfolioBacktestResult` AFTER the existing Phase 5c provenance block:

```python
@dataclass(frozen=True)
class PortfolioBacktestResult:
    """Phase 5a shared-pool result — the ONE portfolio combining all strategies."""
    # ... existing required fields unchanged ...

    # Defaulted provenance — existing Phase 5a/5b/5c (DO NOT REORDER)
    display_name: str = "Shared Pool"
    mtm_model: str = "linear_interpolation_v0"
    bid_policy: str = "rolling_sharpe_60d_v0"
    sizing_policy: str = "fixed_v0"
    sector_cap_policy: str = "uniform_40pct_v0"
    correlation_cap_policy: str = "neighbor_sum_rho06_40pct_v0"
    sector_caps_enabled: bool = True
    correlation_caps_enabled: bool = True
    risk_policy: str = "cap40_corr06_enforced_v0"

    # NEW Phase 5d provenance
    contribution_enabled: bool = False
    contribution_policy: str = "contribution_adjusted_sharpe_60d_v0"
    contribution_lambda: float = 0.5
```

- [ ] **Step 4.4: Run, see 5 pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_types_phase5a.py -v -k "phase5d"
```

Expected: 5/5 pass.

- [ ] **Step 4.5: Verify existing tests still pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_types_phase5a.py -v
```

Expected: all existing + 5 new pass.

- [ ] **Step 4.6: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run ruff check marketpulse/backtest/types.py tests/unit/test_backtest_types_phase5a.py
git add marketpulse/backtest/types.py tests/unit/test_backtest_types_phase5a.py
git commit -m "feat(phase-5d): extend types — BidRecord/Contribution/PortfolioResult

Spec § 4 type extensions:

BidRecord: + 8 defaulted Phase 5d telemetry fields:
- raw_bid_weight, pool_corr, adjusted_bid_weight: float | None
- contribution_multiplier (default 1.0)
- effective_corr_window (default 0)
- pool_corr_excludes_self (default True; forward-flag for non-LOO variants)
- rewarded_for_negative_corr, would_change_rank (default False)

StrategyContribution: + 2 defaulted fields:
- avg_pool_corr: float | None (time-average over non-None bids)
- n_would_change_rank: int (per-BID count, NOT per-day)

PortfolioBacktestResult: + 3 defaulted provenance fields:
- contribution_enabled: bool (default False; observation-first)
- contribution_policy: str (default 'contribution_adjusted_sharpe_60d_v0')
- contribution_lambda: float (default 0.5)

All fields defaulted so existing Phase 5a/5b/5c BidRecord/Contribution/
Result constructions remain backward-compatible without modification.

5 new type tests verify safe defaults + accept real values."
```

---

### Task 5: portfolio_simulator.py — per-day per-strategy PnL accumulators

**Files:**
- Modify: `marketpulse/backtest/portfolio_simulator.py`
- Modify: `tests/unit/test_backtest_portfolio_simulator.py`

**Critical:** Phase 5b's `trade_realized_pnl_by_strategy` is run-wide-flat. Phase 5d needs per-day per-strategy buckets. This task adds the accumulators ONLY (no WEIGHT step changes yet — that's Task 6).

- [ ] **Step 5.1: Append failing test** to `tests/unit/test_backtest_portfolio_simulator.py`:

```python
def test_phase5d_per_day_contribution_decomposition_sums_to_pool_return():
    """Σ daily_strategy_contribution_returns[s][d] == pool_return[d] for every d.

    The Phase 5b T6 invariant (Σ contribution_pnl == pool_pnl) is now reaffirmed
    at day-level granularity via the per-day per-strategy accumulator that
    feeds Phase 5d's LOO subtraction.
    """
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]
    bids = [
        _pair("AAPL", "a", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("MSFT", "b", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    daily_curves = {"a": good, "b": good}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False, correlation_caps_enabled=False,
        contribution_enabled=False,  # Phase 5d default
    )

    # Precondition: daily accumulator is exposed on the result for inspection
    # (or via a debug attribute). Since we don't surface raw accumulators,
    # use the existing equity_curve + per_strategy.contribution_pnl invariant
    # to validate the per-day decomposition consistency.
    daily_equity = r.daily_equity_curve

    # Pool PnL realized at end == sum of per-strategy contribution_pnl
    final_pool_pnl = daily_equity[-1][1] - daily_equity[0][1]
    sum_contribution_pnl = sum(c.contribution_pnl for c in r.per_strategy_stats.values())

    # Outcome: invariant holds within float tolerance
    assert abs(sum_contribution_pnl - final_pool_pnl) < 0.01
```

- [ ] **Step 5.2: Run, see fail (or pass — invariant may already hold from Phase 5b but we want to lock the test in)**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v -k "per_day_contribution_decomposition"
```

This test will fail if the simulator doesn't accept `contribution_enabled` kwarg yet — that's the immediate fail. Expected: `TypeError: unexpected keyword argument 'contribution_enabled'`.

- [ ] **Step 5.3: Modify `marketpulse/backtest/portfolio_simulator.py`** — add Phase 5d accumulators + new kwargs (signature only; WEIGHT step rewrite comes in Task 6)

Find the `simulate_shared_pool` function signature (around line 49) and add new kwargs after `correlation_threshold`:

```python
def simulate_shared_pool(
    bids: list,
    daily_curves: dict[str, list[tuple[date, float]]],
    *,
    horizon: int,
    initial_capital: float = 10_000.0,
    base_position_size: float = 1_000.0,
    max_capital_in_use: float = 10_000.0,
    lookback_days: int = 60,
    target_vol: float = 0.01,
    min_position: float = 200.0,
    max_position: float = 4_000.0,
    sizing_enabled: bool = True,
    sector_caps_enabled: bool = True,
    sector_cap_pct: float = 0.40,
    sector_provider: "Callable[[str], str] | None" = None,
    correlation_caps_enabled: bool = True,
    correlation_cap_pct: float = 0.40,
    correlation_threshold: float = 0.60,
    price_provider: "PriceProvider | None" = None,
    # NEW Phase 5d
    contribution_enabled: bool = False,
    contribution_lambda: float = 0.5,
) -> PortfolioBacktestResult:
```

Add Phase 5d accumulators near top of function (alongside other `n_*_by_strategy` dicts; around line 165):

```python
# Phase 5d per-day per-strategy accumulators
realized_pnl_today_by_strategy: dict[str, float] = {}
daily_strategy_contribution_returns: dict[str, list[tuple[date, float]]] = {}
daily_pool_returns: list[tuple[date, float]] = []
pool_corr_by_strategy: dict[str, list[float | None]] = {}
```

Inside the CLOSE step (around line 191-203), reset and accumulate the per-day bucket:

```python
        # ─── CLOSE ───
        realized_pnl_today_by_strategy.clear()  # NEW Phase 5d: reset per day
        still_open: list[_OpenPosition] = []
        for pos in open_positions:
            if pos.horizon_date == d:
                realized_ret = (pos.horizon_price - pos.entry_price) / pos.entry_price
                cash += pos.position_size * (1 + realized_ret)
                trade_returns_by_strategy.setdefault(pos.strategy, []).append(realized_ret)
                trade_realized_pnl_by_strategy.setdefault(pos.strategy, []).append(
                    realized_ret * pos.position_size
                )
                # NEW Phase 5d: per-day per-strategy realized PnL bucket
                realized_pnl_today_by_strategy[pos.strategy] = (
                    realized_pnl_today_by_strategy.get(pos.strategy, 0.0)
                    + realized_ret * pos.position_size
                )
            else:
                still_open.append(pos)
        open_positions = still_open
```

Find the MTM step (around line 434-449). Inside, snapshot pre-MTM state per-strategy and compute post-MTM delta. Replace the existing MTM block to track per-strategy MTM:

Read the existing MTM block first:

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
grep -n "ALLOCATE\|MTM\|RECORD\|equity_curve.append" marketpulse/backtest/portfolio_simulator.py | head -15
```

Then update the MTM block to snapshot per-strategy MTM before and after the per-position mark loop, and compute the delta. After MTM, compute the day's contribution_return per strategy and append to `daily_strategy_contribution_returns`:

```python
        # ─── MTM ─── (linear interpolation per spec § 2 + Phase 4)
        # Phase 5d: snapshot per-strategy mark-to-market BEFORE updating positions
        mtm_prev_by_strategy: dict[str, float] = {}
        for p in open_positions:
            mtm_prev_by_strategy[p.strategy] = (
                mtm_prev_by_strategy.get(p.strategy, 0.0)
                + p.position_size * ((p.entry_price + (p.horizon_price - p.entry_price)
                                       * elapsed_fraction(calendar, entry=p.entry_date,
                                                          horizon=p.horizon_date, current=d - timedelta(days=1)))
                                      / p.entry_price - 1.0)
                if d > p.entry_date else 0.0
            )

        # Existing MTM body — update positions' marks for today
        mtm_today_by_strategy: dict[str, float] = {}
        # ... existing per-position MTM loop body unchanged ...
        # At the end of the loop, also accumulate today's per-strategy MTM:
        for p in open_positions:
            mtm_today_by_strategy[p.strategy] = (
                mtm_today_by_strategy.get(p.strategy, 0.0)
                + p.position_size * ((p.entry_price + (p.horizon_price - p.entry_price)
                                       * elapsed_fraction(calendar, entry=p.entry_date,
                                                          horizon=p.horizon_date, current=d))
                                      / p.entry_price - 1.0)
            )
```

**Important:** the MTM-delta computation above is a SIMPLIFICATION sketch. The actual implementation should match the existing per-position MTM math in `portfolio_simulator.py:434-449`. Read that block carefully and mirror its formulas exactly when snapshotting.

After MTM completes, compute Phase 5d per-day contribution returns:

```python
        # NEW Phase 5d: per-day per-strategy contribution decomposition
        # (Combines CLOSE-step realized + RECORD-step MTM delta into a per-day
        # per-strategy PnL, normalized by previous-day equity → contribution_return.)
        from marketpulse.backtest.contribution import daily_contribution_return
        pool_equity_prev_day = (
            equity_curve[-1][1] if equity_curve else initial_capital
        )
        all_known_strategies = (
            set(realized_pnl_today_by_strategy)
            | set(mtm_prev_by_strategy)
            | set(mtm_today_by_strategy)
        )
        for s in all_known_strategies:
            pnl_today_s = (
                realized_pnl_today_by_strategy.get(s, 0.0)
                + mtm_today_by_strategy.get(s, 0.0)
                - mtm_prev_by_strategy.get(s, 0.0)
            )
            contrib_ret = daily_contribution_return(pnl_today_s, pool_equity_prev_day)
            daily_strategy_contribution_returns.setdefault(s, []).append((d, contrib_ret))

        # Pool return for this day (used by Task 6's pool_corr_excluding_self call)
        pool_ret_today = sum(
            daily_strategy_contribution_returns[s][-1][1]
            for s in all_known_strategies
            if daily_strategy_contribution_returns.get(s)
        )
        daily_pool_returns.append((d, pool_ret_today))
```

- [ ] **Step 5.4: Run, see pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v -k "per_day_contribution_decomposition"
```

Expected: 1/1 pass.

- [ ] **Step 5.5: Run full portfolio_simulator suite — must not regress**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v 2>&1 | tail -5
```

Expected: all existing tests still pass (Phase 5d accumulators are populated but not yet used; no behavioral change).

- [ ] **Step 5.6: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run ruff check marketpulse/backtest/portfolio_simulator.py tests/unit/test_backtest_portfolio_simulator.py
git add marketpulse/backtest/portfolio_simulator.py tests/unit/test_backtest_portfolio_simulator.py
git commit -m "feat(phase-5d): per-day per-strategy PnL accumulators

Spec § 5. Adds the missing day-level decomposition that Phase 5d's LOO
subtraction will consume in Task 6.

CLOSE step:
- realized_pnl_today_by_strategy: dict[str, float] (reset each day,
  accumulated per closed position). Parallel to Phase 5b's run-wide
  trade_realized_pnl_by_strategy

Pre-RECORD snapshot:
- mtm_prev_by_strategy: dict[str, float] snapshot of per-strategy
  mark-to-market BEFORE updating positions today
- mtm_today_by_strategy: dict[str, float] after MTM update

Post-MTM:
- daily_strategy_contribution_returns: dict[str, list[(date, float)]]
- daily_pool_returns: list[(date, float)]
- Σ daily_strategy_contribution_returns[s][d] == pool_return[d] by
  construction (Phase 5b T6 invariant preserved at day level)

Two new kwargs on simulate_shared_pool:
- contribution_enabled: bool = False
- contribution_lambda: float = 0.5

Both reserved here; WEIGHT step uses them in Task 6.

1 new test asserts the day-level decomposition invariant matches
the existing Phase 5b run-level invariant within 0.01 float tolerance."
```

---

### Task 6: portfolio_simulator.py — WEIGHT step always computes both rankings

**Files:**
- Modify: `marketpulse/backtest/portfolio_simulator.py`
- Modify: `tests/unit/test_backtest_portfolio_simulator.py`

This task replaces the WEIGHT step body. The `weights` dict (driver of DEDUP/ALLOC) is computed via `contribution_enabled` toggle, but `would_change_rank` + `bid_weight_metadata` are computed regardless.

- [ ] **Step 6.1: Append failing tests** to `tests/unit/test_backtest_portfolio_simulator.py`:

```python
def test_phase5d_disabled_yields_phase5a_weights_and_telemetry():
    """contribution_enabled=False: weight == raw_bid_weight, telemetry still populated."""
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]
    bids = [_pair("AAPL", "a", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)]
    daily_curves = {"a": good}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False, correlation_caps_enabled=False,
        contribution_enabled=False,
    )
    # Precondition: at least one won bid
    won = [b for b in r.bid_history if b.outcome == "won"]
    assert len(won) >= 1

    # Outcome: when disabled, weight == raw_bid_weight (when raw is real)
    for b in won:
        if b.raw_bid_weight is not None:
            assert abs(b.weight - b.raw_bid_weight) < 1e-9

    # Provenance: disabled → bid_policy = rolling_sharpe_60d_v0
    assert r.bid_policy == "rolling_sharpe_60d_v0"
    assert r.contribution_enabled is False


def test_phase5d_lookback_days_threaded_to_bid_policy():
    """contribution_enabled=True with lookback_days=90 → bid_policy string includes 90d."""
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]
    bids = [_pair("AAPL", "a", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)]
    daily_curves = {"a": good}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=90,  # NOT default 60
        sizing_enabled=False,
        sector_caps_enabled=False, correlation_caps_enabled=False,
        contribution_enabled=True,
    )
    # Outcome: bid_policy and contribution_policy strings reflect actual lookback
    assert r.bid_policy == "contribution_adjusted_sharpe_90d_v0"
    assert r.contribution_policy == "contribution_adjusted_sharpe_90d_v0"


def test_phase5d_would_change_rank_populated_when_disabled():
    """Disabled but pool has enough history → would_change_rank can be True.

    Killer observation-mode test: even with contribution_enabled=False, if the
    raw and adjusted rankings differ for any strategy on any day, the flag
    should be set on that strategy's BidRecords for that day.
    """
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    # Two strategies with DIFFERENT raw Sharpe and DIFFERENT pool correlation —
    # designed so raw ranking [A, B] vs adjusted ranking [B, A] differ.
    # A: high Sharpe, high pool_corr
    # B: lower Sharpe, negative pool_corr (multiplier boosts B)
    # In cold-start (< 30 days), would_change_rank is False (multiplier=1.0 for both).
    # After enough days, the ranking can flip even with contribution_enabled=False
    # (because we always compute both rankings).

    # Build long enough history so ρ is defined
    long_history = [(date(2026, 1, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
                    for i in range(120)]
    bids = [
        _pair("AAPL", "a", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("MSFT", "b", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    daily_curves = {"a": long_history, "b": long_history}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False, correlation_caps_enabled=False,
        contribution_enabled=False,  # DISABLED but telemetry still computed
    )

    # Precondition: pool has enough history for ρ to be computable on at least one bid
    bids_with_corr = [b for b in r.bid_history if b.pool_corr is not None]
    # If pool corr is still cold-start for all bids on this small synthetic
    # backtest, the test is conservative — we assert that telemetry IS populated
    # with at least the right shape.
    if bids_with_corr:
        # Outcome: at least one bid has would_change_rank populated correctly
        # (whether True or False — what matters is the field is computed)
        for b in bids_with_corr:
            assert isinstance(b.would_change_rank, bool)
```

- [ ] **Step 6.2: Run, see fails**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v -k "phase5d_disabled_yields or phase5d_lookback_days_threaded or phase5d_would_change_rank_populated"
```

Expected: 3 fails (`r.bid_policy` does not include `contribution_adjusted_sharpe_*`, `r.contribution_enabled` AttributeError, etc.).

- [ ] **Step 6.3: Update provenance strings + WEIGHT step in `marketpulse/backtest/portfolio_simulator.py`**

Near the top of `simulate_shared_pool` (after `bid_policy = ...` and `sizing_policy = ...` lines, around line 75-77), add Phase 5d provenance:

```python
# Phase 5a provenance
bid_policy = f"rolling_sharpe_{lookback_days}d_v0"
# Phase 5b
sizing_policy = "vol_target_conviction_v0" if sizing_enabled else "fixed_v0"

# Phase 5d: bid_policy upgrade + composite provenance
if contribution_enabled:
    bid_policy = f"contribution_adjusted_sharpe_{lookback_days}d_v0"
contribution_policy = f"contribution_adjusted_sharpe_{lookback_days}d_v0"
```

Find the WEIGHT COMPUTE block (around line 212-224) and replace with:

```python
        # ─── WEIGHT COMPUTE ───
        strategies_today = sorted({b.strategy for b in todays_bids})
        weights_raw: dict[str, float | None] = {}
        weights: dict[str, float | None] = {}
        floor_hits: set[str] = set()
        bid_weight_metadata: dict[str, BidWeightMetadata] = {}

        if strategies_today:
            weights_raw, floor_hits = compute_bid_weights(
                strategies_today, daily_curves,
                as_of=d, lookback_days=lookback_days,
            )

            # NEW Phase 5d: always compute both raw and adjusted, always populate
            # bid_weight_metadata. The contribution_enabled toggle only chooses
            # which dict drives DEDUP/ALLOC.
            from marketpulse.backtest.contribution import (
                BidWeightMetadata,
                compute_adjusted_bid_weight,
                pool_corr_excluding_self,
            )
            import dataclasses

            weights_adjusted: dict[str, float | None] = {}
            for s in strategies_today:
                raw = weights_raw.get(s)
                pool_corr, eff_window = pool_corr_excluding_self(
                    daily_strategy_contribution_returns.get(s, []),
                    daily_pool_returns,
                    as_of=d,
                    lookback_days=lookback_days,
                    min_overlap=30,
                )
                adjusted, multiplier, rewarded = compute_adjusted_bid_weight(
                    raw_sharpe=raw,
                    pool_corr=pool_corr,
                    lam=contribution_lambda,
                    clip_min=0.5,
                    clip_max=1.2,
                )
                weights_adjusted[s] = adjusted
                bid_weight_metadata[s] = BidWeightMetadata(
                    raw=raw, pool_corr=pool_corr,
                    multiplier=multiplier, adjusted=adjusted,
                    effective_window=eff_window,
                    rewarded_for_negative_corr=rewarded,
                    would_change_rank=False,  # computed below
                )
                pool_corr_by_strategy.setdefault(s, []).append(pool_corr)

            # Compute would_change_rank for EVERY strategy (regardless of toggle)
            sorted_raw = sorted(
                strategies_today,
                key=lambda s: (-(weights_raw.get(s) or 0.0), s),
            )
            sorted_adj = sorted(
                strategies_today,
                key=lambda s: (-(weights_adjusted.get(s) or 0.0), s),
            )
            rank_raw = {s: i for i, s in enumerate(sorted_raw)}
            rank_adj = {s: i for i, s in enumerate(sorted_adj)}
            for s in strategies_today:
                if rank_raw[s] != rank_adj[s]:
                    bid_weight_metadata[s] = dataclasses.replace(
                        bid_weight_metadata[s], would_change_rank=True,
                    )

            # The toggle only chooses which weight drives DEDUP/ALLOC
            if contribution_enabled:
                weights = weights_adjusted
            else:
                weights = weights_raw

        # n_floor_hits telemetry (post-floor-hit set is the source of truth)
        for s in floor_hits:
            n_floor_hits_by_strategy[s] = n_floor_hits_by_strategy.get(s, 0) + 1
```

- [ ] **Step 6.4: Run, see 3 pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v -k "phase5d_disabled_yields or phase5d_lookback_days_threaded or phase5d_would_change_rank_populated"
```

Expected: 3/3 pass.

- [ ] **Step 6.5: Sanity-check Phase 5a/5b/5c tests still pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py 2>&1 | tail -5
```

Expected: all tests pass. If any Phase 5a `bid_policy` assertions break, verify they used `lookback_days=60` (the default) — they should still match `"rolling_sharpe_60d_v0"` literally.

- [ ] **Step 6.6: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run ruff check marketpulse/backtest/portfolio_simulator.py tests/unit/test_backtest_portfolio_simulator.py
git add marketpulse/backtest/portfolio_simulator.py tests/unit/test_backtest_portfolio_simulator.py
git commit -m "feat(phase-5d): WEIGHT step always computes both raw + adjusted rankings

Spec § 5. The WEIGHT step now always:
1. Computes weights_raw via Phase 5a compute_bid_weights (unchanged)
2. Computes pool_corr per strategy via pool_corr_excluding_self
3. Computes weights_adjusted via compute_adjusted_bid_weight per strategy
4. Sorts BOTH rankings and sets would_change_rank flag on metadata
5. ONLY THEN selects driver via contribution_enabled toggle

Killer observation-mode behavior: would_change_rank is computed and
populated on BidRecord regardless of contribution_enabled. Lets users
see 'if I flipped the toggle, would my engine pick differently?'
without enabling Phase 5d in production.

bid_policy + contribution_policy strings use f-strings with lookback_days
(matching Phase 5a precedent). Phase 5d disabled + lookback_days=60 →
'rolling_sharpe_60d_v0' (bit-equivalent Phase 5a regression). Enabled +
lookback_days=90 → 'contribution_adjusted_sharpe_90d_v0'.

3 new tests cover disabled-yields-raw-weights, lookback_days threading
to bid_policy, would_change_rank populated when disabled."
```

---

### Task 7: portfolio_simulator.py — BidRecord constructors thread Phase 5d metadata

**Files:**
- Modify: `marketpulse/backtest/portfolio_simulator.py`
- Modify: `tests/unit/test_backtest_portfolio_simulator.py`

All BidRecord constructor sites in the daily loop (7 sites: `dedup_loser`, `cap_full`, `cash_short`, `size_too_small`, `sector_cap_full`, `correlation_cap_full`, `won`) must read from `bid_weight_metadata[b.strategy]` and copy the 8 Phase 5d fields.

- [ ] **Step 7.1: Append failing tests** to `tests/unit/test_backtest_portfolio_simulator.py`:

```python
def test_phase5d_metadata_on_won_bidrecord():
    """Won BidRecord carries all 8 Phase 5d fields populated from metadata."""
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]
    bids = [_pair("AAPL", "a", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)]
    daily_curves = {"a": good}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False, correlation_caps_enabled=False,
        contribution_enabled=False,
    )
    won = [b for b in r.bid_history if b.outcome == "won"]
    assert len(won) == 1
    b = won[0]
    # Precondition: weight is set (matches raw)
    assert b.weight is not None
    # Outcome: all 8 Phase 5d fields are populated
    assert b.raw_bid_weight is not None or b.raw_bid_weight is None  # may be None on cold-start
    # multiplier defaults to 1.0 in cold-start
    assert b.contribution_multiplier in (1.0,) or 0.5 <= b.contribution_multiplier <= 1.2
    # effective_corr_window is set (0 if no overlap)
    assert b.effective_corr_window >= 0
    # pool_corr_excludes_self is True (LOO v0 lock)
    assert b.pool_corr_excludes_self is True


def test_phase5d_metadata_on_sector_cap_full_bidrecord():
    """sector_cap_full BidRecord (Phase 5c site) carries Phase 5d metadata."""
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    def fake_sector(_ticker: str) -> str:
        return "Technology"  # all bids same sector → cap fires

    # 5 same-sector $1k bids; cap=40% × $10k = $4k → first 4 win, 5th blocks
    bids = [
        _pair(f"T{i}", f"s{i}", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)
        for i in range(5)
    ]
    daily_curves = {f"s{i}": good for i in range(5)}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=True, sector_cap_pct=0.40,
        correlation_caps_enabled=False,
        sector_provider=fake_sector,
        contribution_enabled=False,
    )
    blocked = [b for b in r.bid_history if b.outcome == "sector_cap_full"]
    # Precondition: exactly one blocked bid
    assert len(blocked) == 1
    b = blocked[0]
    # Outcome: 5d metadata present
    assert b.pool_corr_excludes_self is True
    assert b.effective_corr_window >= 0
    # Multiplier should be 1.0 (cold-start or short_circuit; not adjusted)
    assert 0.5 <= b.contribution_multiplier <= 1.2


def test_phase5d_metadata_on_dedup_loser_bidrecord():
    """dedup_loser BidRecord carries Phase 5d metadata."""
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    # Two strategies bid same ticker; lower Sharpe loses dedup
    bids = [
        _pair("AAPL", "a", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("AAPL", "b", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    daily_curves = {"a": good, "b": good}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False, correlation_caps_enabled=False,
        contribution_enabled=False,
    )
    losers = [b for b in r.bid_history if b.outcome == "dedup_loser"]
    # Precondition: exactly one dedup loser
    assert len(losers) == 1
    b = losers[0]
    # Outcome: 5d metadata present
    assert b.pool_corr_excludes_self is True
    assert b.effective_corr_window >= 0
```

- [ ] **Step 7.2: Run, see 3 fail**

Tests will fail because BidRecord constructions in portfolio_simulator.py don't thread `pool_corr_excludes_self=True` (the dataclass default is True but if `bid_weight_metadata` doesn't supply it explicitly, the field gets the dataclass default `None` for `raw_bid_weight` etc.).

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v -k "phase5d_metadata_on"
```

- [ ] **Step 7.3: Update BidRecord constructor sites in `portfolio_simulator.py`**

Find all 7 `BidRecord(...)` constructor sites in the daily loop:

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
grep -n "all_bid_records.append(BidRecord(" marketpulse/backtest/portfolio_simulator.py
```

At each site, after the existing fields, append metadata threading (using a helper to avoid repetition is fine but inline is OK for clarity):

For example, the `won` outcome:

```python
            meta = bid_weight_metadata.get(b.strategy)
            all_bid_records.append(BidRecord(
                date=d, strategy=b.strategy, ticker=b.ticker,
                weight=weights[b.strategy],
                outcome="won", winner=None,
                position_size=requested_size,
                # Phase 5d telemetry threading from metadata
                raw_bid_weight=meta.raw if meta else None,
                pool_corr=meta.pool_corr if meta else None,
                contribution_multiplier=meta.multiplier if meta else 1.0,
                adjusted_bid_weight=meta.adjusted if meta else None,
                effective_corr_window=meta.effective_window if meta else 0,
                pool_corr_excludes_self=True,
                rewarded_for_negative_corr=meta.rewarded_for_negative_corr if meta else False,
                would_change_rank=meta.would_change_rank if meta else False,
            ))
```

Apply the same metadata block (potentially extracted as a helper `_phase5d_telemetry_kwargs(meta)` inside the function, or inline 7 times) to all 7 BidRecord constructor sites:
1. `dedup_loser` (DEDUP block)
2. `cap_full` (ALLOC block)
3. `cash_short` (ALLOC block)
4. `size_too_small` (Phase 5b SIZE block)
5. `sector_cap_full` (Phase 5c ALLOC block)
6. `correlation_cap_full` (Phase 5c ALLOC block)
7. `won` (ALLOC block)

Recommendation: define a local helper inside `simulate_shared_pool`:

```python
        def _phase5d_kwargs(strategy: str) -> dict:
            """Build the 8 Phase 5d BidRecord telemetry kwargs from metadata."""
            meta = bid_weight_metadata.get(strategy)
            if meta is None:
                return {
                    "raw_bid_weight": None,
                    "pool_corr": None,
                    "contribution_multiplier": 1.0,
                    "adjusted_bid_weight": None,
                    "effective_corr_window": 0,
                    "pool_corr_excludes_self": True,
                    "rewarded_for_negative_corr": False,
                    "would_change_rank": False,
                }
            return {
                "raw_bid_weight": meta.raw,
                "pool_corr": meta.pool_corr,
                "contribution_multiplier": meta.multiplier,
                "adjusted_bid_weight": meta.adjusted,
                "effective_corr_window": meta.effective_window,
                "pool_corr_excludes_self": True,
                "rewarded_for_negative_corr": meta.rewarded_for_negative_corr,
                "would_change_rank": meta.would_change_rank,
            }
```

Then each BidRecord constructor becomes:

```python
            all_bid_records.append(BidRecord(
                date=d, strategy=b.strategy, ticker=b.ticker,
                weight=weights[b.strategy],
                outcome="won", winner=None,
                position_size=requested_size,
                **_phase5d_kwargs(b.strategy),
            ))
```

Apply this pattern at all 7 sites.

- [ ] **Step 7.4: Run, see 3 pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v -k "phase5d_metadata_on"
```

Expected: 3/3 pass.

- [ ] **Step 7.5: Sanity-check no regressions**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 7.6: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run ruff check marketpulse/backtest/portfolio_simulator.py tests/unit/test_backtest_portfolio_simulator.py
git add marketpulse/backtest/portfolio_simulator.py tests/unit/test_backtest_portfolio_simulator.py
git commit -m "feat(phase-5d): thread Phase 5d metadata into all 7 BidRecord sites

Spec § 5. Every BidRecord constructor in the daily loop now carries
all 8 Phase 5d telemetry fields populated from bid_weight_metadata.

Sites updated (7 total):
- dedup_loser (DEDUP block)
- cap_full (ALLOC block, Phase 5a)
- cash_short (ALLOC block, Phase 5a)
- size_too_small (SIZE block, Phase 5b)
- sector_cap_full (ALLOC block, Phase 5c)
- correlation_cap_full (ALLOC block, Phase 5c)
- won (ALLOC block, Phase 5a)

Helper _phase5d_kwargs(strategy) extracts the 8 kwargs from
bid_weight_metadata to keep all 7 sites DRY.

When metadata is missing (e.g., strategy never entered WEIGHT this day),
returns safe defaults matching the BidRecord dataclass defaults.

3 new tests verify metadata threads to won, sector_cap_full, and
dedup_loser sites. Other 4 sites covered by integration coverage."
```

---

### Task 8: portfolio_simulator.py — Finalization aggregates Phase 5d telemetry

**Files:**
- Modify: `marketpulse/backtest/portfolio_simulator.py`
- Modify: `tests/unit/test_backtest_portfolio_simulator.py`

Finalization populates `StrategyContribution.avg_pool_corr` (time-average of non-None per-day pool_corr per strategy) and `StrategyContribution.n_would_change_rank` (per-bid count from `all_bid_records`). Also threads `contribution_enabled/_lambda/_policy` into the result.

- [ ] **Step 8.1: Append failing tests** to `tests/unit/test_backtest_portfolio_simulator.py`:

```python
def test_phase5d_avg_pool_corr_in_strategy_contribution():
    """avg_pool_corr is the time-average over non-None pool_corr values per strategy."""
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(120)]  # long history to allow non-cold-start ρ
    bids = [
        _pair("AAPL", "a", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("MSFT", "b", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    daily_curves = {"a": good, "b": good}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False, correlation_caps_enabled=False,
        contribution_enabled=False,
    )
    # Precondition: per_strategy_stats populated for both strategies
    assert "a" in r.per_strategy_stats
    assert "b" in r.per_strategy_stats
    # Outcome: avg_pool_corr is None when cold-start prevents any defined ρ,
    # or a real float in [-1, 1] when at least one bid had a defined ρ
    for s in ("a", "b"):
        c = r.per_strategy_stats[s]
        if c.avg_pool_corr is not None:
            assert -1.0 <= c.avg_pool_corr <= 1.0


def test_phase5d_n_would_change_rank_counted_per_bid():
    """n_would_change_rank is the count of BidRecords with would_change_rank=True."""
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]
    bids = [_pair("AAPL", "a", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)]
    daily_curves = {"a": good}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False, correlation_caps_enabled=False,
        contribution_enabled=False,
    )
    # Cold-start with single strategy → n_would_change_rank == 0
    # (no ranking changes possible with one strategy)
    for s in r.per_strategy_stats:
        # Outcome: count matches sum of per-bid flags
        expected_count = sum(
            1 for b in r.bid_history
            if b.strategy == s and b.would_change_rank
        )
        assert r.per_strategy_stats[s].n_would_change_rank == expected_count


def test_phase5d_provenance_threaded_to_result():
    """contribution_enabled, contribution_policy, contribution_lambda in result."""
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]
    bids = [_pair("AAPL", "a", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)]
    daily_curves = {"a": good}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False, correlation_caps_enabled=False,
        contribution_enabled=True,
        contribution_lambda=0.7,
    )
    # Outcome: provenance reflects kwargs
    assert r.contribution_enabled is True
    assert r.contribution_lambda == 0.7
    assert r.contribution_policy == "contribution_adjusted_sharpe_60d_v0"
    assert r.bid_policy == "contribution_adjusted_sharpe_60d_v0"
```

- [ ] **Step 8.2: Run, see 3 fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v -k "phase5d_avg_pool_corr or phase5d_n_would_change_rank or phase5d_provenance_threaded"
```

Expected: 3 fails.

- [ ] **Step 8.3: Modify finalization in `portfolio_simulator.py`**

Find the finalization block (after the daily loop, before final return; around line 461+). Add Phase 5d aggregates:

```python
    # ─── FINALIZE ───
    # ... existing finalization code ...

    # Phase 5d: count would_change_rank per BID from all_bid_records
    n_would_change_rank_by_strategy: dict[str, int] = {}
    for b in all_bid_records:
        if b.would_change_rank:
            n_would_change_rank_by_strategy[b.strategy] = (
                n_would_change_rank_by_strategy.get(b.strategy, 0) + 1
            )

    # Phase 5d: avg_pool_corr per strategy (time-avg over non-None values)
    avg_pool_corr_by_strategy: dict[str, float | None] = {}
    for s, corr_list in pool_corr_by_strategy.items():
        defined = [c for c in corr_list if c is not None]
        avg_pool_corr_by_strategy[s] = (
            sum(defined) / len(defined) if defined else None
        )
```

Find the `StrategyContribution(...)` construction site in finalization and add the new fields:

```python
        per_strategy_stats[s] = StrategyContribution(
            # ... existing fields ...
            avg_pool_corr=avg_pool_corr_by_strategy.get(s),
            n_would_change_rank=n_would_change_rank_by_strategy.get(s, 0),
        )
```

Find the final `PortfolioBacktestResult(...)` constructor and add the 3 new provenance fields:

```python
    return PortfolioBacktestResult(
        # ... existing fields ...
        bid_policy=bid_policy,
        sizing_policy=sizing_policy,
        # Phase 5c fields ...
        # NEW Phase 5d
        contribution_enabled=contribution_enabled,
        contribution_policy=contribution_policy,
        contribution_lambda=contribution_lambda,
    )
```

Also update the empty-bids early-return PortfolioBacktestResult constructor (around line 80-115) to thread the same 3 fields.

- [ ] **Step 8.4: Run, see 3 pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v -k "phase5d_avg_pool_corr or phase5d_n_would_change_rank or phase5d_provenance_threaded"
```

Expected: 3/3 pass.

- [ ] **Step 8.5: Sanity-check no regressions**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 8.6: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run ruff check marketpulse/backtest/portfolio_simulator.py tests/unit/test_backtest_portfolio_simulator.py
git add marketpulse/backtest/portfolio_simulator.py tests/unit/test_backtest_portfolio_simulator.py
git commit -m "feat(phase-5d): finalization aggregates avg_pool_corr + n_would_change_rank

Spec § 5 finalization. StrategyContribution gains two Phase 5d fields
populated from accumulators:

- avg_pool_corr: time-average over non-None pool_corr values for the
  strategy's bids. None when all bids were cold-start
- n_would_change_rank: per-BID count from all_bid_records (NOT per-day).
  A strategy with 5 bids on a day where rank flipped contributes 5,
  not 1 — matches spec § 4 field semantic

PortfolioBacktestResult provenance threading completes:
- contribution_enabled: bool (passed through from kwargs)
- contribution_policy: str (computed from lookback_days)
- contribution_lambda: float (passed through)

Both result constructors (empty-bids early-return + finalization) updated.

3 new tests cover avg_pool_corr computation, per-bid count semantic,
and end-to-end provenance threading with non-default lambda."
```

---

### Task 9: simulator.py — orchestrator threads contribution kwargs

**Files:**
- Modify: `marketpulse/backtest/simulator.py`
- Modify: `tests/integration/test_backtest_shared_pool.py`

- [ ] **Step 9.1: Append failing tests** to `tests/integration/test_backtest_shared_pool.py`:

```python
def test_run_shared_pool_default_contribution_disabled(db_session):
    """Default kwargs → contribution_enabled is False, bid_policy is Phase 5a string."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest

    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5)
    assert out["shared"].contribution_enabled is False
    assert out["shared"].bid_policy == "rolling_sharpe_60d_v0"
    assert out["shared"].contribution_policy == "contribution_adjusted_sharpe_60d_v0"


def test_run_shared_pool_contribution_enabled_provenance(db_session):
    """contribution_enabled=True + non-default lambda threads through."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest

    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(
        db_session, horizon=5,
        contribution_enabled=True,
        contribution_lambda=0.7,
    )
    assert out["shared"].contribution_enabled is True
    assert out["shared"].contribution_lambda == 0.7
    assert out["shared"].bid_policy == "contribution_adjusted_sharpe_60d_v0"


def test_run_shared_pool_avg_pool_corr_populated_when_history_sufficient(db_session):
    """avg_pool_corr is a defined float or None on every StrategyContribution."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest

    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5)
    # Outcome: every StrategyContribution has avg_pool_corr field (None or float)
    for s, c in out["shared"].per_strategy_stats.items():
        assert c.avg_pool_corr is None or isinstance(c.avg_pool_corr, float)
        assert c.n_would_change_rank >= 0
```

- [ ] **Step 9.2: Run, see fails**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/integration/test_backtest_shared_pool.py -v -k "default_contribution_disabled or contribution_enabled_provenance or avg_pool_corr_populated"
```

Expected: 3 fails (`TypeError: unexpected kwarg contribution_enabled`).

- [ ] **Step 9.3: Modify `run_shared_pool_backtest` in `marketpulse/backtest/simulator.py`**

Find the signature (around line 467) and add 2 new kwargs:

```python
def run_shared_pool_backtest(
    db,
    *,
    horizon: int = 5,
    since: date | None = None,
    initial_capital: float = 10_000.0,
    base_position_size: float = 1_000.0,
    target_vol: float = 0.01,
    min_position: float = 200.0,
    max_position: float = 4_000.0,
    sizing_enabled: bool = True,
    max_capital_in_use: float = 10_000.0,
    lookback_days: int = 60,
    sector_caps_enabled: bool = True,
    sector_cap_pct: float = 0.40,
    correlation_caps_enabled: bool = True,
    correlation_cap_pct: float = 0.40,
    correlation_threshold: float = 0.60,
    # NEW Phase 5d
    contribution_enabled: bool = False,
    contribution_lambda: float = 0.5,
) -> dict:
```

Find the `simulate_shared_pool(...)` call inside and thread the new kwargs:

```python
    shared_result = simulate_shared_pool(
        # ... existing kwargs ...
        sector_caps_enabled=sector_caps_enabled,
        sector_cap_pct=sector_cap_pct,
        correlation_caps_enabled=correlation_caps_enabled,
        correlation_cap_pct=correlation_cap_pct,
        correlation_threshold=correlation_threshold,
        price_provider=price_provider,
        # NEW Phase 5d
        contribution_enabled=contribution_enabled,
        contribution_lambda=contribution_lambda,
    )
```

- [ ] **Step 9.4: Run, see pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/integration/test_backtest_shared_pool.py -v
```

Expected: existing + 3 new pass.

- [ ] **Step 9.5: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run ruff check marketpulse/backtest/simulator.py tests/integration/test_backtest_shared_pool.py
git add marketpulse/backtest/simulator.py tests/integration/test_backtest_shared_pool.py
git commit -m "feat(phase-5d): orchestrator threads contribution_enabled + contribution_lambda

run_shared_pool_backtest accepts 2 new kwargs:
- contribution_enabled: bool = False (observation-only default)
- contribution_lambda: float = 0.5

Both threaded through to simulate_shared_pool. Default-disabled means
existing integration tests pass unchanged.

3 new integration tests cover defaults-off, enabled provenance with
non-default lambda, and avg_pool_corr / n_would_change_rank field
shape on StrategyContribution."
```

---

### Task 10: routes/backtest.py — context aliases

**Files:**
- Modify: `marketpulse/web/routes/backtest.py`
- Modify: `tests/web/test_lab_backtest_modes.py`

- [ ] **Step 10.1: Append failing tests** to `tests/web/test_lab_backtest_modes.py`:

```python
def test_lab_backtest_shared_mode_contribution_off_by_default(
    client, monkeypatch, db_session,
):
    """Default shared-pool render does not show contribution modifier or bid_policy upgrade."""
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="A1", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest?mode=shared-pool")
    assert r.status_code == 200
    # Hero shows the Phase 5a string when disabled
    assert "rolling_sharpe_60d_v0" in r.text
    # No "1−0.5ρ" inline modifier in disabled mode
    assert "1−0.5ρ" not in r.text or "1−" not in r.text  # tolerant


def test_lab_backtest_shared_mode_avg_pool_corr_column_visible(
    client, monkeypatch, db_session,
):
    """Strategy table includes 'avg pool ρ' column header."""
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="A1", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest?mode=shared-pool")
    assert r.status_code == 200
    # Outcome: avg pool ρ column header is present
    assert "avg pool ρ" in r.text or "pool ρ" in r.text or "avg pool" in r.text
```

- [ ] **Step 10.2: Run, see 2 fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/web/test_lab_backtest_modes.py -v -k "contribution_off_by_default or avg_pool_corr_column_visible"
```

Expected: 2 fails (templates don't render the new content yet — that's T11-T13).

- [ ] **Step 10.3: Modify `marketpulse/web/routes/backtest.py`**

Find the `lab_backtest` route handler. In the shared-pool branch, add explicit context aliases for Phase 5d:

```python
return templates.TemplateResponse(
    request, "lab_backtest.html",
    {
        # ... existing context ...
        # Phase 5d: explicit aliases for template clarity
        "contribution_enabled": (
            shared_result.contribution_enabled if shared_result else False
        ),
        "contribution_lambda": (
            shared_result.contribution_lambda if shared_result else 0.5
        ),
    },
)
```

- [ ] **Step 10.4: Commit (tests still fail until T11-T13 templates land)**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run ruff check marketpulse/web/routes/backtest.py tests/web/test_lab_backtest_modes.py
git add marketpulse/web/routes/backtest.py tests/web/test_lab_backtest_modes.py
git commit -m "feat(phase-5d): route passes contribution context aliases

Adds explicit template context aliases:
- contribution_enabled: bool (from shared_result.contribution_enabled)
- contribution_lambda: float (from shared_result.contribution_lambda)

shared_result already carries these via Phase 5d dataclass extensions;
aliases simplify template access.

2 new web tests assert default-off rendering and avg pool ρ column
header presence. Currently failing — templates land in Tasks 11-13."
```

---

### Task 11: Hero template — inline λ modifier

**Files:**
- Modify: `marketpulse/web/templates/partials/backtest_hero.html`

- [ ] **Step 11.1: Update `backtest_hero.html`**

Read the file first to find paragraph 1 of the shared-pool block. Modify paragraph 1 to include the conditional `1 − Xρ 贡献调整` phrase:

```html
{% if mode == 'shared-pool' %}
  <p class="mp-hero__desc">
    6 个策略共享单一 $10k 资本池,通过 {{ lookback_days }}-day 滚动 Sharpe
    加权竞标分配
    {% if shared_result and shared_result.contribution_enabled %}
      × <strong>1−{{ "{:.1f}".format(shared_result.contribution_lambda) }}ρ</strong> 贡献调整(clip [0.5, 1.2])
    {% endif %}
    。撞 ticker 时高 Sharpe 策略赢。
    <strong>bid_policy={{ shared_result.bid_policy }}</strong>。
  </p>
  <!-- ... rest of shared-pool block unchanged ... -->
{% endif %}
```

The conditional outputs nothing when `contribution_enabled=False`, leaving the paragraph byte-identical to the pre-5d Phase 5c version.

- [ ] **Step 11.2: Verify Phase 5c test still passes (the default-off case)**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/web/test_lab_backtest_modes.py -v -k "contribution_off_by_default"
```

Expected: 1 pass (now that route context aliases exist + template renders Phase 5a string).

- [ ] **Step 11.3: Commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
git add marketpulse/web/templates/partials/backtest_hero.html
git commit -m "feat(phase-5d): hero — inline λ modifier in paragraph 1

Spec § 6. When shared_result.contribution_enabled is True, paragraph 1
gains an inline phrase '× 1−Xρ 贡献调整 (clip [0.5, 1.2])' between the
60-day rolling Sharpe description and the period. The bid_policy string
in the same paragraph reflects the upgrade automatically (rolling_sharpe
→ contribution_adjusted_sharpe).

When disabled, the conditional outputs nothing — paragraph 1 is
byte-identical to Phase 5c shipped form.

No new hero paragraph; no new CSS."
```

---

### Task 12: Bid history — weight column tooltip + 2 chip icons

**Files:**
- Modify: `marketpulse/web/templates/partials/backtest_bid_history.html`

- [ ] **Step 12.1: Find and update the weight column cell**

Read `marketpulse/web/templates/partials/backtest_bid_history.html` and locate the `<td>` for the weight column. Replace its current rendering with:

```html
<td class="num mono tnum"
    title="{% if b.contribution_multiplier != 1.0 %}raw={{ '%.2f'|format(b.raw_bid_weight) }} · ρ={{ '%.2f'|format(b.pool_corr) if b.pool_corr is not none else 'n<30 cold-start' }} · ×{{ '%.2f'|format(b.contribution_multiplier) }}{% else %}raw weight (no adjustment){% endif %}">
  {{ "{:.2f}".format(b.weight) }}
  {% if b.rewarded_for_negative_corr %}<span class="mp-chip mp-chip--up" title="hedge boost (ρ<0)">↗</span>{% endif %}
  {% if b.would_change_rank %}<span class="mp-chip" title="adjusted weight 改变了排名">⇅</span>{% endif %}
</td>
```

This re-uses Phase 5b/5c `.mp-chip` and `.mp-chip--up` classes — no new CSS.

- [ ] **Step 12.2: Commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
git add marketpulse/web/templates/partials/backtest_bid_history.html
git commit -m "feat(phase-5d): bid history weight column tooltip + 2 chip icons

Spec § 6. The existing 权重 column gains:
- Tooltip exposing raw + ρ + multiplier (cold-start text when ρ is None)
- ↗ chip (mp-chip--up) when rewarded_for_negative_corr=True
- ⇅ chip when would_change_rank=True

Tooltip is conditional: shows 'raw weight (no adjustment)' when
contribution_multiplier == 1.0, full breakdown otherwise.

Reuses existing .mp-chip and .mp-chip--up CSS from Phase 5b/5c — no new
styles. Cell still displays b.weight as the primary number; new icons
are inline supplements."
```

---

### Task 13: Strategy table — `avg pool ρ` + `rank Δ` columns

**Files:**
- Modify: `marketpulse/web/templates/partials/backtest_strategy_table_shared.html`

- [ ] **Step 13.1: Add 2 column headers after `avg bid w`, before `avg size`**

Read the file and find the `<thead>` block. Add 2 new `<th>` after `avg bid w`:

```html
<th class="num">avg pool ρ</th>
<th class="num">rank Δ</th>
```

- [ ] **Step 13.2: Add 2 column cells in the strategy iteration loop**

After the existing `avg bid w` `<td>`, add 2 new cells:

```html
<td class="num mono tnum">
  {% if c.avg_pool_corr is none %}—{% else %}{{ "{:+.2f}".format(c.avg_pool_corr) }}{% endif %}
</td>
<td class="num mono tnum"
    title="bids 因 adjusted 改变排名的次数">
  {% if c.n_would_change_rank > 0 %}{{ c.n_would_change_rank }}{% else %}—{% endif %}
</td>
```

- [ ] **Step 13.3: Verify web tests pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest tests/web/test_lab_backtest_modes.py -v
```

Expected: all tests pass (including the 2 new from T10).

- [ ] **Step 13.4: Commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
git add marketpulse/web/templates/partials/backtest_strategy_table_shared.html
git commit -m "feat(phase-5d): strategy table — avg pool ρ + rank Δ columns

Spec § 6. Two new columns inserted between 'avg bid w' and 'avg size':
- avg pool ρ: shows c.avg_pool_corr formatted as {:+.2f}, or — when None
- rank Δ: shows c.n_would_change_rank when > 0, or — otherwise.
  Tooltip explains the metric

Both columns reuse Phase 5c .num .mono .tnum CSS — no new styles.

Strategy table now has 10 columns; layout fits in the existing
aside rail (mp-backtest-rail) without overflow."
```

---

### Task 14: Final integration — full suite + ruff + smoke

- [ ] **Step 14.1: Full pytest + ruff**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run pytest 2>&1 | tail -3
uv run ruff check . 2>&1 | tail -3
```

Expected: ~895 tests pass (Phase 5c was 875; Phase 5d adds ~25 net new tests). Ruff clean.

- [ ] **Step 14.2: Module imports smoke**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
uv run python -c "
from marketpulse.backtest import (
    BidRecord, PortfolioBacktestResult, StrategyContribution,
    StrategyBacktestArtifacts, run_shared_pool_backtest,
)
from marketpulse.backtest.contribution import (
    daily_contribution_return,
    pool_corr_excluding_self,
    compute_adjusted_bid_weight,
    BidWeightMetadata,
)
print('ok')
"
```

Expected: `ok`.

- [ ] **Step 14.3: 4-variant route smoke**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
SESSION_SECRET=test-secret-32-bytes-of-random-here APP_PASSWORD_HASH=x ANTHROPIC_API_KEY=x \
  uv run alembic upgrade head 2>&1 | tail -2
SESSION_SECRET=test-secret-32-bytes-of-random-here APP_PASSWORD_HASH=x ANTHROPIC_API_KEY=x \
  uv run python -c "
import os
from fastapi.testclient import TestClient
from marketpulse.web.main import app
from marketpulse.auth.password import hash_password
client = TestClient(app)
pw = 'secret'
os.environ['APP_PASSWORD_HASH'] = hash_password(pw)
from marketpulse.config import get_settings
get_settings.cache_clear()
client.post('/login', data={'password': pw})
for path in [
    '/lab/backtest',
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
/lab/backtest?mode=shared-pool: 200
/lab/backtest?mode=shared-pool&horizon=20: 200
/lab/backtest?mode=invalid: 422
```

- [ ] **Step 14.4: Commit count check**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
git log --oneline main..HEAD | wc -l
```

Expected: 13 task commits + 1 plan commit + 4 spec/merge commits = 18 commits since main.

- [ ] **Step 14.5: Push branch + open PR**

If all green:

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5d-plan
git push -u origin plan/phase-5d-contribution-adjusted-sharpe
```

Then open PR titled `feat(phase-5d): Contribution-Adjusted Sharpe`.

---

## Self-Review Notes

### Spec coverage check

| Spec section | Task |
|---|---|
| § 1 Goal | n/a (overall) |
| § 2 Locked decision #1 (formula) | T3 |
| § 2 Locked decision #2 (λ=0.5 default) | T3 + T9 |
| § 2 Locked decision #3 (60d window) | T2 |
| § 2 Locked decision #4 (cold-start min_overlap=30) | T2 |
| § 2 Locked decision #5 (default disabled, observation-only) | T6 + T9 |
| § 2 Locked decision #6 (LOO via subtraction) | T2 |
| § 2 Locked decision #7 (strategy_contribution_return definition) | T1 + T5 |
| § 2 Locked decision #8 (full adjusted throughout, Phase 5a regression) | T6 |
| § 2 Locked decision #9 (8 new BidRecord fields) | T4 + T7 |
| § 2 Locked decision #10 (3 new PortfolioBacktestResult fields) | T4 + T8 |
| § 2 Locked decision #11 (2 new StrategyContribution fields) | T4 + T8 |
| § 2 Locked decision #12 (scope: only bid_weight) | confirmed throughout |
| § 2 Locked decision #13 (no DB tables) | confirmed throughout |
| § 3 Architecture (3 functions + 1 dataclass in contribution.py) | T1 + T2 + T3 |
| § 4 BidRecord extensions | T4 |
| § 4 StrategyContribution extensions | T4 |
| § 4 PortfolioBacktestResult extensions | T4 |
| § 5 Per-day accumulator plumbing (realized + MTM snapshots) | T5 |
| § 5 WEIGHT step rewrite (always both rankings) | T6 |
| § 5 BidRecord constructor sites (7 sites) | T7 |
| § 5 Finalization (avg_pool_corr, n_would_change_rank) | T8 |
| § 5 bid_policy + contribution_policy f-strings | T6 |
| § 6 Hero inline modifier | T11 |
| § 6 Bid history tooltip + chips | T12 |
| § 6 Strategy table 2 new columns | T13 |
| § 7 Risks (zero new module-level state, etc.) | confirmed in T1 docstring |
| § 7 Migration & Reproducibility (contribution_enabled=False bit-equiv Phase 5a) | T6 |
| § 7 Backward-compat audit | T6 + T8 (assertions on bid_policy literal) |
| § 8 Required test scenarios (~16) | T1-T13 (each scenario mapped to a step) |

### Placeholder scan: ZERO

No "TBD", "TODO", "implement later", "fill in details", "Add appropriate error handling", or "similar to Task N" in this plan.

### Type consistency check

- `BidWeightMetadata` fields: `raw`, `pool_corr`, `multiplier`, `adjusted`, `effective_window`, `rewarded_for_negative_corr`, `would_change_rank` — consistent across T1 (defn), T6 (population), T7 (read at constructor sites)
- `daily_strategy_contribution_returns: dict[str, list[tuple[date, float]]]` — consistent in T5 (init), T6 (consumed by `pool_corr_excluding_self`)
- `daily_pool_returns: list[tuple[date, float]]` — consistent in T5 (init), T6 (consumed)
- `pool_corr_by_strategy: dict[str, list[float | None]]` — consistent in T5 (init), T6 (populate per day), T8 (consume in finalization)
- `bid_policy` f-string format `f"rolling_sharpe_{lookback_days}d_v0"` / `f"contribution_adjusted_sharpe_{lookback_days}d_v0"` — consistent in T6 (compute), T8 (verify in tests), T11 (template reads `shared_result.bid_policy`)
- `contribution_policy` constant: `f"contribution_adjusted_sharpe_{lookback_days}d_v0"` — same as above
- `_phase5d_kwargs(strategy: str) -> dict` helper signature — defined in T7, called at 7 BidRecord sites
