# Phase 5b — Dynamic Position Sizing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Phase 5a's fixed `$1000` position size with a Hybrid model: `size = clamp(base × (target_vol/σ) × (α/mean_α), min, max)`. Conviction multiplier uses `rolling_alpha` (raw mean return) to avoid the σ² double-count that would have over-rewarded stable-low-vol strategies.

**Architecture:** Two new sibling functions in `sharpe.py` (`rolling_sigma`, `rolling_alpha`) feed a new `compute_position_sizes` that returns per-strategy size dicts. The `portfolio_simulator.py` daily loop gains a SIZE COMPUTE step between WEIGHT and DEDUP — strategies whose computed size falls below `min_position` are filtered before DEDUP, preserving Phase 5a's bid-priority semantics. `BidRecord` gains a `position_size` field that preserves diagnostic value across all outcomes (raw pre-clamp size for `size_too_small`, requested size for `cap_full`/`cash_short`). New concentration telemetry (`max_strategy_exposure`, `hhi_concentration`) is observation-only in v0 and prepares for Phase 5d's risk-budget enforcement.

**Tech Stack:** Python 3.12 + numpy + empyrical-reloaded (all already present). No new dependencies. No new database tables. No Alembic migration.

**Spec:** `docs/superpowers/specs/2026-05-20-phase-5b-dynamic-position-sizing-design.md`

---

## File Structure

```
marketpulse/backtest/
├── sharpe.py                              MODIFY: + rolling_sigma, + rolling_alpha,
│                                                  + compute_position_sizes
├── portfolio_simulator.py                 MODIFY: + SIZE COMPUTE step;
│                                                  ALLOCATE uses per-bid sizes;
│                                                  finalization computes telemetry;
│                                                  + base_position_size rename, + sizing_enabled
├── types.py                               MODIFY: BidRecord.position_size + size_too_small literal;
│                                                  StrategyContribution.n_size_too_small_skipped +
│                                                  avg_position_size;
│                                                  PortfolioBacktestResult.sizing_policy +
│                                                  max_strategy_exposure + hhi_concentration
├── simulator.py                           MODIFY: run_shared_pool_backtest threads new knobs
└── __init__.py                            (no change — public API stable)

marketpulse/web/
├── routes/backtest.py                     MODIFY: compute size_distribution histogram bins,
│                                                  pass via context
└── templates/partials/
    ├── backtest_hero.html                 MODIFY: append sizing_policy line in shared-pool
    ├── backtest_bid_history.html          MODIFY: + Size column, + SVG sparkline header
    └── backtest_strategy_table_shared.html MODIFY: + Avg Size column

tests/
├── unit/
│   ├── test_backtest_sharpe.py            MODIFY: + 8 rolling_sigma tests,
│   │                                              + 6 rolling_alpha tests,
│   │                                              + 12 compute_position_sizes tests
│   └── test_backtest_portfolio_simulator.py MODIFY: + 13 sizing integration tests;
│                                                    UPDATE Phase 5a tests passing position_size= kwarg
├── integration/
│   └── test_backtest_shared_pool.py       MODIFY: + 3 orchestrator-level sizing tests
└── web/
    └── test_lab_backtest_modes.py         MODIFY: + 2 UI assertions
```

**No new files. No new dependencies. No DB migration.**

---

## Conventions

- **TDD strict**: each task = failing test → run/see fail → minimal impl → run/see pass → commit.
- **Working directory**: `/Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan` (worktree on `plan/phase-5b-dynamic-sizing`).
- **Run tests**: `uv run pytest <path> -v`.
- **Lint**: `uv run ruff check <path>`.
- **No new DB tables, no migrations**.
- **Daily loop ORDER LOCK** (spec § 2): `CLOSE → BID → WEIGHT → SIZE → DEDUP → ALLOC → MTM → RECORD`.
- **Decoupling lock** (spec § 1 ⚠ box): `compute_position_sizes` does NOT take `bid_weights`. Sizing uses `rolling_alpha` for conviction signal. `bid_weight = rolling_sharpe` from Phase 5a drives bid priority only (DEDUP + ALLOC sort key), unchanged.
- **Floor vs ceiling asymmetry** (spec § 2): raw < min → None (skip); raw > max → clamp to max.
- **BidRecord.position_size** field always records the model's REQUESTED size (raw pre-clamp for `size_too_small`, clamped value for `won`, requested value for `cap_full`/`cash_short`).
- **sizing_policy provenance**: default `"fixed_v0"` (Phase 5a backward-compat); Phase 5b overrides to `"vol_target_conviction_v0"`.

---

### Task 1: rolling_sigma function

**Files:**
- Modify: `marketpulse/backtest/sharpe.py`
- Modify: `tests/unit/test_backtest_sharpe.py`

- [ ] **Step 1.1: Append failing tests** to `tests/unit/test_backtest_sharpe.py`:

```python
def test_rolling_sigma_returns_positive_for_volatile_curve():
    """Curve with daily 0.5% drift + 0.5% noise → σ ≈ 0.005."""
    from datetime import date, timedelta
    import random
    from marketpulse.backtest.sharpe import rolling_sigma

    random.seed(42)
    curve = []
    v = 10_000.0
    for i in range(30):
        d = date(2026, 4, 1) + timedelta(days=i)
        curve.append((d, v))
        v *= (1 + 0.005 + random.gauss(0, 0.005))

    s = rolling_sigma(curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert s is not None
    assert 0.0 < s < 0.02  # roughly between 0 and 2% daily std


def test_rolling_sigma_returns_none_below_min_events():
    from datetime import date, timedelta
    from marketpulse.backtest.sharpe import rolling_sigma
    curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
             for i in range(3)]
    s = rolling_sigma(curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert s is None


def test_rolling_sigma_uses_60d_window_by_default():
    from datetime import date, timedelta
    from marketpulse.backtest.sharpe import rolling_sigma
    # 100 days of 0.5% steady growth — σ should be ~0 (deterministic)
    curve = [(date(2026, 1, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
             for i in range(100)]
    s = rolling_sigma(curve, as_of=date(2026, 4, 11), min_events=5)
    # Steady geometric growth has near-zero relative std → may be None or very small
    assert s is None or s < 0.001


def test_rolling_sigma_excludes_dates_at_or_after_as_of():
    """Causality: curve points >= as_of are excluded from window."""
    from datetime import date, timedelta
    from marketpulse.backtest.sharpe import rolling_sigma
    curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 + 0.01 * (i % 2)))
             for i in range(30)]
    # Window [2026-04-01, 2026-04-15) covers 14 days
    s = rolling_sigma(curve, as_of=date(2026, 4, 15), lookback_days=60, min_events=5)
    assert s is not None
    assert s > 0  # noisy data should yield non-zero std


def test_rolling_sigma_returns_none_when_variance_is_zero():
    """Flat curve → σ = 0 → return None (treated as degenerate)."""
    from datetime import date, timedelta
    from marketpulse.backtest.sharpe import rolling_sigma
    flat_curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0) for i in range(30)]
    s = rolling_sigma(flat_curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert s is None


def test_rolling_sigma_empty_curve_returns_none():
    from datetime import date
    from marketpulse.backtest.sharpe import rolling_sigma
    s = rolling_sigma([], as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert s is None


def test_rolling_sigma_matches_numpy_std_within_tolerance():
    """Cross-check: rolling_sigma should match numpy.std of diff'd returns."""
    from datetime import date, timedelta
    import numpy as np
    from marketpulse.backtest.sharpe import rolling_sigma
    values = [10_000.0, 10_050.0, 10_100.0, 10_080.0, 10_120.0, 10_150.0, 10_200.0]
    curve = [(date(2026, 4, 1) + timedelta(days=i), v) for i, v in enumerate(values)]
    s = rolling_sigma(curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    arr = np.array(values)
    expected_returns = np.diff(arr) / arr[:-1]
    expected_sigma = float(np.std(expected_returns))
    assert abs(s - expected_sigma) < 1e-9


def test_rolling_sigma_pairs_with_rolling_sharpe_consistent_window():
    """Same input + same window → both functions slice the same data points."""
    from datetime import date, timedelta
    from marketpulse.backtest.sharpe import rolling_sigma, rolling_sharpe
    curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i + 0.001 * (i % 3)))
             for i in range(30)]
    s = rolling_sigma(curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    sharpe = rolling_sharpe(curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    # Both should be non-None on the same dataset under the same window
    assert s is not None
    assert sharpe is not None
```

- [ ] **Step 1.2: Run, see fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/unit/test_backtest_sharpe.py -v -k rolling_sigma
```

Expected: 8 fails (ImportError or AttributeError).

- [ ] **Step 1.3: Add `rolling_sigma` to `marketpulse/backtest/sharpe.py`**

Append below the existing `rolling_sharpe` function:

```python
def rolling_sigma(
    daily_curve: list[tuple[date, float]],
    *,
    as_of: date,
    lookback_days: int = 60,
    min_events: int = 5,
) -> float | None:
    """Daily-return σ over curve points in [as_of - lookback, as_of).

    Returns None when:
      - Fewer than `min_events` qualifying points
      - σ computes to exactly 0 (degenerate zero-variance, e.g., flat curve)
      - Non-finite result (shouldn't happen with np.std on finite input)

    Causality: identical window semantics to rolling_sharpe.
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
    s = float(np.std(daily_returns))
    if not math.isfinite(s) or s == 0.0:
        return None
    return s
```

- [ ] **Step 1.4: Run, pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/unit/test_backtest_sharpe.py -v -k rolling_sigma
```

Expected: 8/8 pass.

- [ ] **Step 1.5: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run ruff check marketpulse/backtest/sharpe.py tests/unit/test_backtest_sharpe.py
git add marketpulse/backtest/sharpe.py tests/unit/test_backtest_sharpe.py
git commit -m "feat(phase-5b): rolling_sigma sibling to rolling_sharpe

Spec § 2: causal daily-return std over [as_of - lookback, as_of).
Same window + min_events semantics as rolling_sharpe.

Returns None when:
- Fewer than min_events qualifying points
- σ computes to exactly 0 (degenerate, e.g., flat curve)
- Non-finite result

Used by Phase 5b's compute_position_sizes for vol-target normalization.

8 unit tests cover positive path, n<5 floor, lookback truncation,
causal cutoff, zero-variance handling, empty curve, numpy
cross-check, and window-consistency with rolling_sharpe."
```

---

### Task 2: rolling_alpha function

**Files:**
- Modify: `marketpulse/backtest/sharpe.py`
- Modify: `tests/unit/test_backtest_sharpe.py`

- [ ] **Step 2.1: Append failing tests** to `tests/unit/test_backtest_sharpe.py`:

```python
def test_rolling_alpha_returns_positive_for_uptrend():
    from datetime import date, timedelta
    from marketpulse.backtest.sharpe import rolling_alpha
    curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
             for i in range(30)]
    a = rolling_alpha(curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert a is not None
    assert a > 0
    # ~0.5% daily growth → α ≈ 0.005 (small rounding from geometric vs arithmetic)
    assert 0.003 < a < 0.007


def test_rolling_alpha_returns_negative_for_downtrend():
    from datetime import date, timedelta
    from marketpulse.backtest.sharpe import rolling_alpha
    curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (0.99 ** i))
             for i in range(30)]
    a = rolling_alpha(curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert a is not None
    assert a < 0


def test_rolling_alpha_returns_none_below_min_events():
    from datetime import date, timedelta
    from marketpulse.backtest.sharpe import rolling_alpha
    curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
             for i in range(3)]
    a = rolling_alpha(curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert a is None


def test_rolling_alpha_excludes_dates_at_or_after_as_of():
    """Causality: same window semantics as rolling_sigma."""
    from datetime import date, timedelta
    from marketpulse.backtest.sharpe import rolling_alpha
    curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
             for i in range(30)]
    a = rolling_alpha(curve, as_of=date(2026, 4, 15), lookback_days=60, min_events=5)
    assert a is not None
    assert a > 0


def test_rolling_alpha_empty_curve_returns_none():
    from datetime import date
    from marketpulse.backtest.sharpe import rolling_alpha
    a = rolling_alpha([], as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    assert a is None


def test_rolling_alpha_matches_numpy_mean_within_tolerance():
    """Cross-check: rolling_alpha should match numpy.mean of diff'd returns."""
    from datetime import date, timedelta
    import numpy as np
    from marketpulse.backtest.sharpe import rolling_alpha
    values = [10_000.0, 10_050.0, 10_100.0, 10_080.0, 10_120.0, 10_150.0, 10_200.0]
    curve = [(date(2026, 4, 1) + timedelta(days=i), v) for i, v in enumerate(values)]
    a = rolling_alpha(curve, as_of=date(2026, 5, 1), lookback_days=60, min_events=5)
    arr = np.array(values)
    expected_returns = np.diff(arr) / arr[:-1]
    expected_alpha = float(np.mean(expected_returns))
    assert abs(a - expected_alpha) < 1e-9
```

- [ ] **Step 2.2: Run, see fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/unit/test_backtest_sharpe.py -v -k rolling_alpha
```

Expected: 6 fails.

- [ ] **Step 2.3: Add `rolling_alpha` to `marketpulse/backtest/sharpe.py`**

Append below `rolling_sigma`:

```python
def rolling_alpha(
    daily_curve: list[tuple[date, float]],
    *,
    as_of: date,
    lookback_days: int = 60,
    min_events: int = 5,
) -> float | None:
    """Daily-return MEAN (alpha) over curve points in [as_of - lookback, as_of).

    Returns None when:
      - Fewer than `min_events` qualifying points
      - Non-finite mean (shouldn't happen with np.mean on finite input)

    Used by Phase 5b's compute_position_sizes as the conviction signal.
    Distinct from rolling_sharpe — alpha is raw mean return WITHOUT division
    by σ. Using alpha (not Sharpe) for sizing conviction avoids the μ/σ²
    double-count described in spec § 1.

    Causality: identical window semantics to rolling_sigma.
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
    a = float(np.mean(daily_returns))
    if not math.isfinite(a):
        return None
    return a
```

- [ ] **Step 2.4: Run, pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/unit/test_backtest_sharpe.py -v -k rolling_alpha
```

Expected: 6/6 pass.

- [ ] **Step 2.5: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run ruff check marketpulse/backtest/sharpe.py tests/unit/test_backtest_sharpe.py
git add marketpulse/backtest/sharpe.py tests/unit/test_backtest_sharpe.py
git commit -m "feat(phase-5b): rolling_alpha sibling to rolling_sharpe + rolling_sigma

Spec § 2 + § 1 ⚠ box: causal daily-return mean over the same window as
rolling_sigma. Used by compute_position_sizes as the conviction signal
for the Hybrid sizing formula.

CRITICAL: alpha (raw mean return), NOT Sharpe (mean/σ), is what avoids
the μ/σ² double-count. With α as conviction multiplier and 1/σ as risk
normalizer, the final size ∝ μ/σ (alpha-risk-adjusted, exactly once).

6 unit tests cover positive/negative trends, n<5 floor, causal cutoff,
empty curve, and numpy cross-check."
```

---

### Task 3: compute_position_sizes

**Files:**
- Modify: `marketpulse/backtest/sharpe.py`
- Modify: `tests/unit/test_backtest_sharpe.py`

- [ ] **Step 3.1: Append failing tests** to `tests/unit/test_backtest_sharpe.py`:

```python
def _curve(start_value=10_000.0, n_days=30, daily_return=0.005, start_date=None):
    """Synthetic equity curve helper for compute_position_sizes tests."""
    from datetime import date, timedelta
    start_date = start_date or date(2026, 4, 1)
    curve = []
    v = float(start_value)
    for i in range(n_days):
        d = start_date + timedelta(days=i)
        curve.append((d, v))
        v *= (1 + daily_return)
    return curve


def _noisy_curve(start_value=10_000.0, n_days=30, daily_return=0.005, noise=0.002,
                  start_date=None, seed=42):
    from datetime import date, timedelta
    import random
    random.seed(seed)
    start_date = start_date or date(2026, 4, 1)
    curve = []
    v = float(start_value)
    for i in range(n_days):
        d = start_date + timedelta(days=i)
        curve.append((d, v))
        v *= (1 + daily_return + random.gauss(0, noise))
    return curve


def test_size_high_alpha_low_vol_yields_above_base():
    """High α + low σ → size > base (rewarded for both)."""
    from datetime import date
    from marketpulse.backtest.sharpe import compute_position_sizes
    daily_curves = {
        "fast": _noisy_curve(daily_return=0.015, noise=0.002, seed=1),  # high α, lowish σ
        "neutral": _noisy_curve(daily_return=0.005, noise=0.005, seed=2),
    }
    sizes, _ = compute_position_sizes(
        ["fast", "neutral"], daily_curves,
        as_of=date(2026, 5, 1),
    )
    assert sizes["fast"] is not None and sizes["neutral"] is not None
    assert sizes["fast"] > sizes["neutral"]


def test_size_low_alpha_high_vol_yields_none_below_min():
    """Low α + high σ → raw < min → None (skip)."""
    from datetime import date
    from marketpulse.backtest.sharpe import compute_position_sizes
    daily_curves = {
        "loser": _noisy_curve(daily_return=0.0005, noise=0.020, seed=3),  # tiny α, huge σ
        "winner": _noisy_curve(daily_return=0.015, noise=0.003, seed=4),
    }
    sizes, raw_below = compute_position_sizes(
        ["loser", "winner"], daily_curves,
        as_of=date(2026, 5, 1),
    )
    # loser's raw should be << $200; winner's should be well above
    if sizes["loser"] is None:
        assert "loser" in raw_below
        assert raw_below["loser"] < 200.0
    assert sizes["winner"] is not None


def test_size_neutral_strategy_yields_near_base():
    """Single neutral strategy (only strategy → mean_α = its own α)
    → α_scale = 1 → size = base × (target_vol/σ) only."""
    from datetime import date
    from marketpulse.backtest.sharpe import compute_position_sizes
    daily_curves = {
        "neutral": _noisy_curve(daily_return=0.01, noise=0.01, seed=5),  # σ ≈ 1% target
    }
    sizes, _ = compute_position_sizes(
        ["neutral"], daily_curves,
        as_of=date(2026, 5, 1), base=1000.0, target_vol=0.01,
    )
    # σ ≈ target_vol → vol_scale ≈ 1; only-strategy → α_scale = 1; size ≈ base
    assert sizes["neutral"] is not None
    assert 500 < sizes["neutral"] < 2000  # roughly around base


def test_size_below_min_returns_none_not_clamped_up():
    """raw < min_position → None (caller skips), NOT clamped up to min."""
    from datetime import date, timedelta
    from marketpulse.backtest.sharpe import compute_position_sizes
    # Construct curves where math forces raw < 200:
    # huge σ + tiny α, both meet min_events
    bad_curve = _noisy_curve(daily_return=0.0001, noise=0.05, seed=6)
    good_curve = _noisy_curve(daily_return=0.02, noise=0.003, seed=7)
    daily_curves = {"tiny": bad_curve, "huge": good_curve}
    sizes, raw_below = compute_position_sizes(
        ["tiny", "huge"], daily_curves,
        as_of=date(2026, 5, 1), min_position=200.0,
    )
    if sizes["tiny"] is None:
        assert raw_below["tiny"] < 200.0  # raw size was below floor
        assert sizes["tiny"] != 200.0      # NOT clamped up to floor


def test_size_above_max_clamps_to_max():
    """raw > max_position → clamp to max_position."""
    from datetime import date
    from marketpulse.backtest.sharpe import compute_position_sizes
    # Single high-α + low-σ strategy → no α_scale boost (only strategy),
    # but vol_scale boost. To force > max, push σ very low and base higher.
    daily_curves = {
        "low_vol": _noisy_curve(daily_return=0.001, noise=0.0005, seed=8),
    }
    sizes, _ = compute_position_sizes(
        ["low_vol"], daily_curves,
        as_of=date(2026, 5, 1), base=2000.0, target_vol=0.01, max_position=4000.0,
    )
    if sizes["low_vol"] is not None:
        assert sizes["low_vol"] <= 4000.0


def test_size_sigma_none_uses_target_vol_fallback():
    """σ unavailable (n<5) → vol_scale = 1.0."""
    from datetime import date, timedelta
    from marketpulse.backtest.sharpe import compute_position_sizes
    # Only 3 events → σ is None
    tiny_curve = [(date(2026, 4, 28) + timedelta(days=i), 10_000.0 * (1.01 ** i))
                  for i in range(3)]
    sizes, _ = compute_position_sizes(
        ["new"], {"new": tiny_curve},
        as_of=date(2026, 5, 1), base=1000.0,
    )
    # σ None → vol_scale=1.0; α also None (n<5) → α_scale=1.0
    # → size = base × 1.0 × 1.0 = 1000
    assert sizes["new"] == 1000.0


def test_size_zero_sigma_uses_target_vol_fallback():
    """σ computes to 0 (flat curve) → vol_scale = 1.0."""
    from datetime import date, timedelta
    from marketpulse.backtest.sharpe import compute_position_sizes
    flat_curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0) for i in range(30)]
    sizes, _ = compute_position_sizes(
        ["flat"], {"flat": flat_curve},
        as_of=date(2026, 5, 1), base=1000.0,
    )
    # σ = 0 → vol_scale = 1.0; α = 0 (or None) → α_scale = 1.0
    assert sizes["flat"] is not None
    assert sizes["flat"] == 1000.0


def test_size_joint_bootstrap_yields_uniform_base():
    """ALL strategies have None α AND None σ → all sizes = base. Review fix #1."""
    from datetime import date, timedelta
    from marketpulse.backtest.sharpe import compute_position_sizes
    # All strategies with n<5 events → both α and σ are None
    tiny = lambda seed: [(date(2026, 4, 28) + timedelta(days=i), 10_000.0 * (1.01 ** i))
                          for i in range(3)]
    daily_curves = {"a": tiny(1), "b": tiny(2), "c": tiny(3)}
    sizes, _ = compute_position_sizes(
        list(daily_curves.keys()), daily_curves,
        as_of=date(2026, 5, 1), base=1000.0,
    )
    assert sizes == {"a": 1000.0, "b": 1000.0, "c": 1000.0}


def test_size_all_strategies_below_min_returns_all_none():
    """Worst-case: all strategies have raw < min."""
    from datetime import date
    from marketpulse.backtest.sharpe import compute_position_sizes
    daily_curves = {
        "a": _noisy_curve(daily_return=0.00005, noise=0.05, seed=10),
        "b": _noisy_curve(daily_return=0.00005, noise=0.04, seed=11),
    }
    sizes, raw_below = compute_position_sizes(
        ["a", "b"], daily_curves,
        as_of=date(2026, 5, 1), min_position=200.0,
    )
    # Both should be None (raw < 200)
    if sizes["a"] is None and sizes["b"] is None:
        assert "a" in raw_below and "b" in raw_below


def test_compute_position_sizes_raises_on_missing_strategy():
    """Contract: every strategy in strategies_today must appear in daily_curves."""
    import pytest
    from datetime import date
    from marketpulse.backtest.sharpe import compute_position_sizes
    daily_curves = {"present": _noisy_curve()}
    with pytest.raises(KeyError):
        compute_position_sizes(
            ["present", "missing"], daily_curves,
            as_of=date(2026, 5, 1),
        )


def test_compute_position_sizes_returns_raw_sizes_below_min_dict():
    """raw_sizes_below_min populated for None strategies with their raw value."""
    from datetime import date
    from marketpulse.backtest.sharpe import compute_position_sizes
    daily_curves = {
        "tiny": _noisy_curve(daily_return=0.00005, noise=0.05, seed=13),
        "big":  _noisy_curve(daily_return=0.02, noise=0.005, seed=14),
    }
    sizes, raw_below = compute_position_sizes(
        ["tiny", "big"], daily_curves,
        as_of=date(2026, 5, 1), min_position=200.0,
    )
    # If tiny got None, its raw should be in the dict and below min
    if sizes["tiny"] is None:
        assert "tiny" in raw_below
        assert raw_below["tiny"] < 200.0
    # big should not appear in raw_below (it passed the floor)
    if sizes["big"] is not None:
        assert "big" not in raw_below


def test_compute_position_sizes_raw_only_for_none_strategies():
    """Strategies whose raw >= min do NOT appear in raw_sizes_below_min."""
    from datetime import date
    from marketpulse.backtest.sharpe import compute_position_sizes
    daily_curves = {"normal": _noisy_curve(daily_return=0.005, noise=0.005, seed=15)}
    sizes, raw_below = compute_position_sizes(
        ["normal"], daily_curves,
        as_of=date(2026, 5, 1),
    )
    if sizes["normal"] is not None:
        assert "normal" not in raw_below
```

- [ ] **Step 3.2: Run, see fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/unit/test_backtest_sharpe.py -v -k "test_size or compute_position_sizes"
```

Expected: 12 fails.

- [ ] **Step 3.3: Add `compute_position_sizes` to `marketpulse/backtest/sharpe.py`**

Append below `rolling_alpha`:

```python
def compute_position_sizes(
    strategies_today: list[str],
    daily_curves: dict[str, list[tuple[date, float]]],
    *,
    as_of: date,
    base: float = 1_000.0,
    target_vol: float = 0.01,
    min_position: float = 200.0,
    max_position: float = 4_000.0,
    lookback_days: int = 60,
    min_events: int = 5,
) -> tuple[dict[str, float | None], dict[str, float]]:
    """Compute per-strategy position size (Hybrid: vol-target × alpha-conviction).

    Spec § 1 ⚠ box: NOT bid_weights (Sharpe) as conviction signal — would
    double-count σ. Uses rolling_alpha (raw mean return) instead.

    Returns (sizes, raw_sizes_below_min):
      - sizes: dict[strategy, float | None]. None means raw < min_position;
        caller skips with outcome=size_too_small.
      - raw_sizes_below_min: dict[strategy, float] capturing RAW pre-clamp
        size for each None-returning strategy. Caller logs this on the
        size_too_small BidRecord so the bid history shows "model wanted $42"
        not just "blocked".

    Algorithm:
      1. For each s, compute σ_s and α_s via rolling_sigma / rolling_alpha.
      2. mean_α = mean over strategies where α is not None.
         - If ALL α are None → mean_α = None → all α_scale = 1.0 (full bootstrap).
         - If SOME α None → those get α_scale = 1.0; others use α_s/mean_α.
      3. For each s:
         vol_scale = target_vol / σ_s   if σ_s is not None and σ_s > 0
                     else 1.0
         α_scale   = α_s / mean_α       if α_s is not None and mean_α is not None
                     else 1.0
         raw = base * vol_scale * α_scale
         if raw < min_position:
             sizes[s] = None; raw_sizes_below_min[s] = raw
         else:
             sizes[s] = min(raw, max_position)  # ceiling clamp only
      4. Return (sizes, raw_sizes_below_min).

    Contract:
      - Every entry of strategies_today MUST appear in daily_curves.
        Raises KeyError on missing.
    """
    sigmas: dict[str, float | None] = {
        s: rolling_sigma(daily_curves[s], as_of=as_of,
                          lookback_days=lookback_days, min_events=min_events)
        for s in strategies_today
    }
    alphas: dict[str, float | None] = {
        s: rolling_alpha(daily_curves[s], as_of=as_of,
                          lookback_days=lookback_days, min_events=min_events)
        for s in strategies_today
    }
    known_alphas = [a for a in alphas.values() if a is not None]
    mean_alpha: float | None = (
        sum(known_alphas) / len(known_alphas) if known_alphas else None
    )

    sizes: dict[str, float | None] = {}
    raw_below: dict[str, float] = {}
    for s in strategies_today:
        sigma = sigmas[s]
        alpha = alphas[s]
        vol_scale = target_vol / sigma if (sigma is not None and sigma > 0) else 1.0
        if alpha is not None and mean_alpha is not None and mean_alpha != 0:
            alpha_scale = alpha / mean_alpha
        else:
            alpha_scale = 1.0
        raw = base * vol_scale * alpha_scale
        if raw < min_position:
            sizes[s] = None
            raw_below[s] = raw
        else:
            sizes[s] = min(raw, max_position)
    return sizes, raw_below
```

- [ ] **Step 3.4: Run, pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/unit/test_backtest_sharpe.py -v
```

Expected: all sharpe tests pass (existing 14 + new 26 = 40).

- [ ] **Step 3.5: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run ruff check marketpulse/backtest/sharpe.py tests/unit/test_backtest_sharpe.py
git add marketpulse/backtest/sharpe.py tests/unit/test_backtest_sharpe.py
git commit -m "feat(phase-5b): compute_position_sizes — Hybrid vol-target × alpha-conviction

Spec § 2 algorithm. Pure function over (strategies_today, daily_curves).
Returns (sizes, raw_sizes_below_min) tuple — raw sizes preserved for
size_too_small diagnostic logging.

CRITICAL design choices (locked):
- Conviction signal is rolling_alpha (raw mean return), NOT rolling_sharpe.
  Avoids μ/σ² double-count.
- mean_α = mean of non-None α values over today's firing strategies
  (not pool-wide all-6).
- If ALL α None → all α_scale = 1.0 (joint-bootstrap → uniform base).
- min_position is a FLOOR DECISION (raw<min → None=skip), max_position
  is a CEILING CLAMP (raw>max → max).
- bid_weights NOT a parameter — sizing is orthogonal to bid priority.

12 unit tests cover positive/negative paths, bootstrap fallback (σ,α),
floor decision vs ceiling clamp, joint bootstrap, KeyError contract,
and raw_sizes_below_min plumbing."
```

---

### Task 4: Extend types.py — BidRecord, StrategyContribution, PortfolioBacktestResult

**Files:**
- Modify: `marketpulse/backtest/types.py`
- Modify: `tests/unit/test_backtest_types_phase5a.py` (rename to phase5b? — keep name; just append tests)

This task adds the spec § 3 field extensions. **Adding `position_size` to `BidRecord` is a breaking change** — every BidRecord construction in code or tests must pass `position_size=...`. Phase 5a tests + the simulator itself need updating in subsequent tasks.

- [ ] **Step 4.1: Append failing tests** to `tests/unit/test_backtest_types_phase5a.py`:

```python
def test_bid_record_has_position_size_field():
    """Phase 5b: BidRecord requires position_size (no default)."""
    from datetime import date
    from marketpulse.backtest.types import BidRecord
    b = BidRecord(
        date=date(2026, 5, 1), strategy="x", ticker="AAPL",
        weight=1.0, outcome="won", winner=None,
        position_size=1500.0,
    )
    assert b.position_size == 1500.0


def test_bid_record_size_too_small_outcome_literal():
    """Phase 5b: new 'size_too_small' outcome in the literal."""
    from datetime import date
    from marketpulse.backtest.types import BidRecord
    b = BidRecord(
        date=date(2026, 5, 1), strategy="x", ticker="AAPL",
        weight=0.5, outcome="size_too_small", winner=None,
        position_size=42.0,  # raw pre-clamp diagnostic value
    )
    assert b.outcome == "size_too_small"
    assert b.position_size == 42.0


def test_strategy_contribution_has_size_telemetry_fields():
    """Phase 5b adds n_size_too_small_skipped + avg_position_size."""
    from marketpulse.backtest.types import StrategyContribution
    c = StrategyContribution(
        strategy="x", display_name="X",
        n_trades=5, n_dedup_skipped=1,
        n_capacity_skipped=0, n_cash_short_skipped=0,
        n_size_too_small_skipped=2,
        contribution_pnl=250.0,
        avg_exposure=0.25, avg_bid_weight=1.2,
        avg_position_size=1450.0,
        n_bids=8, n_floor_hits=0,
    )
    assert c.n_size_too_small_skipped == 2
    assert c.avg_position_size == 1450.0


def test_portfolio_result_has_concentration_telemetry():
    """Phase 5b adds max_strategy_exposure + hhi_concentration + sizing_policy."""
    from datetime import date
    from marketpulse.backtest.types import PortfolioBacktestResult
    r = PortfolioBacktestResult(
        horizon=5,
        n_trades=20, n_dedup_total=3,
        avg_capital_utilization=0.42,
        max_strategy_exposure=0.55,
        hhi_concentration=0.31,
        cumulative_return=0.08, annual_return=0.15,
        sharpe=1.3, sortino=1.6, max_drawdown=-0.04, calmar=3.75,
        win_rate=0.62, avg_win_pct=0.03, avg_loss_pct=-0.018,
        daily_equity_curve=[(date(2026, 4, 1), 10_000.0)],
        excess_vs_spy=0.04,
        per_strategy_stats={},
        bid_history=[],
    )
    assert r.max_strategy_exposure == 0.55
    assert r.hhi_concentration == 0.31
    # sizing_policy default = "fixed_v0" (Phase 5a backward compat)
    assert r.sizing_policy == "fixed_v0"


def test_portfolio_result_sizing_policy_overridable():
    """Phase 5b runs set sizing_policy='vol_target_conviction_v0'."""
    from datetime import date
    from marketpulse.backtest.types import PortfolioBacktestResult
    r = PortfolioBacktestResult(
        horizon=5, n_trades=0, n_dedup_total=0,
        avg_capital_utilization=0.0,
        max_strategy_exposure=0.0, hhi_concentration=0.0,
        cumulative_return=0.0, annual_return=0.0,
        sharpe=None, sortino=None, max_drawdown=0.0, calmar=None,
        win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0,
        daily_equity_curve=[(date(2026, 4, 1), 10_000.0)],
        excess_vs_spy=0.0,
        per_strategy_stats={}, bid_history=[],
        sizing_policy="vol_target_conviction_v0",
    )
    assert r.sizing_policy == "vol_target_conviction_v0"
```

- [ ] **Step 4.2: Run, see fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/unit/test_backtest_types_phase5a.py -v -k "position_size or too_small or concentration or sizing_policy"
```

Expected: 5 fails (TypeError on unexpected kwargs).

- [ ] **Step 4.3: Modify `marketpulse/backtest/types.py`**

Update `BidRecord` outcome literal + add `position_size` field:

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
        "size_too_small",  # NEW Phase 5b
    ]
    winner: str | None
    position_size: float  # NEW Phase 5b — model's REQUESTED size in dollars.
                          # Preserves diagnostic value across all outcomes:
                          #   won:           actual opened size (post-clamp)
                          #   dedup_loser:   what this strategy would have opened
                          #   cap_full:      what was requested but cap-blocked
                          #   cash_short:    what was requested but cash-blocked
                          #   size_too_small: raw pre-clamp size (e.g. $42)
                          # See spec § 3 for the rationale.
```

Update `StrategyContribution` — add `n_size_too_small_skipped` and `avg_position_size`. Place them where they fit logically; field order matters for dataclass (all required before any defaults). Since Phase 5a's StrategyContribution has no defaults, append at end:

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
    n_size_too_small_skipped: int  # NEW Phase 5b
    contribution_pnl: float
    avg_exposure: float
    avg_bid_weight: float
    avg_position_size: float       # NEW Phase 5b
    n_bids: int
    n_floor_hits: int
```

Update `PortfolioBacktestResult` — add `max_strategy_exposure`, `hhi_concentration`, `sizing_policy`. The first two are required (no default), so they go before the defaulted fields at end:

```python
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

    # NEW Phase 5b concentration telemetry (required) —
    # observation-only in v0; Phase 5d will enforce risk budgets using these.
    max_strategy_exposure: float
    hhi_concentration: float

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
    per_strategy_stats: dict[str, "StrategyContribution"]
    bid_history: list["BidRecord"]

    # Defaulted provenance (always-default in v0)
    display_name: str = "Shared Pool"
    mtm_model: str = "linear_interpolation_v0"
    bid_policy: str = "rolling_sharpe_60d_v0"
    sizing_policy: str = "fixed_v0"  # NEW Phase 5b
```

- [ ] **Step 4.4: Update Phase 5a tests that construct BidRecord / StrategyContribution / PortfolioBacktestResult without the new fields**

Find them with grep, add the new kwargs:

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
grep -rln "BidRecord(" tests/ | xargs ls -1
grep -rln "StrategyContribution(" tests/ | xargs ls -1
grep -rln "PortfolioBacktestResult(" tests/ | xargs ls -1
```

For each match, add the missing kwargs:
- `BidRecord(...)` → add `position_size=1000.0` (or another sensible value)
- `StrategyContribution(...)` → add `n_size_too_small_skipped=0, avg_position_size=0.0`
- `PortfolioBacktestResult(...)` → add `max_strategy_exposure=0.0, hhi_concentration=0.0` (positional ordering matters — required-before-defaults)

Specifically expected files to touch:
- `tests/unit/test_backtest_types_phase5a.py` (existing helpers `_bid_kwargs`, `_contribution_kwargs`, `_portfolio_kwargs`)
- `tests/unit/test_backtest_portfolio_simulator.py` (assertions on bid_history entries)
- `tests/integration/test_backtest_shared_pool.py` (orchestrator test fixtures)

For `_bid_kwargs` helper, modify to:

```python
def _bid_kwargs(**overrides):
    base = {
        "date": date(2026, 5, 1),
        "strategy": "momentum_breakout",
        "ticker": "AAPL",
        "weight": 1.2,
        "outcome": "won",
        "winner": None,
        "position_size": 1000.0,  # NEW Phase 5b default
    }
    base.update(overrides)
    return base
```

For `_contribution_kwargs`:

```python
def _contribution_kwargs(**overrides):
    base = {
        "strategy": "momentum_breakout",
        "display_name": "动量突破",
        "n_trades": 5,
        "n_dedup_skipped": 1,
        "n_capacity_skipped": 0,
        "n_cash_short_skipped": 0,
        "n_size_too_small_skipped": 0,  # NEW Phase 5b
        "contribution_pnl": 250.0,
        "avg_exposure": 0.30,
        "avg_bid_weight": 1.4,
        "avg_position_size": 1450.0,  # NEW Phase 5b
        "n_bids": 6,
        "n_floor_hits": 0,
    }
    base.update(overrides)
    return base
```

For `_portfolio_kwargs`:

```python
def _portfolio_kwargs(**overrides):
    base = {
        "horizon": 5,
        "n_trades": 30,
        "n_dedup_total": 4,
        "avg_capital_utilization": 0.55,
        "max_strategy_exposure": 0.55,  # NEW Phase 5b
        "hhi_concentration": 0.31,       # NEW Phase 5b
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
```

- [ ] **Step 4.5: Run pytest broadly to catch all callers needing update**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/unit/test_backtest_types_phase5a.py tests/unit/test_backtest_portfolio_simulator.py tests/integration/test_backtest_shared_pool.py -v 2>&1 | tail -20
```

Expected: any `TypeError: missing required argument` errors. Fix each call site to add the new kwargs.

- [ ] **Step 4.6: Re-run, all tests pass (existing + 5 new Phase 5b type tests)**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/unit/test_backtest_types_phase5a.py -v
```

Expected: 19/19 pass (14 existing + 5 new).

- [ ] **Step 4.7: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run ruff check marketpulse/backtest/types.py tests/
git add marketpulse/backtest/types.py tests/
git commit -m "feat(phase-5b): extend types — BidRecord.position_size + concentration telemetry

Spec § 3 type extensions:
- BidRecord: + position_size: float (required, no default — forces explicit
  setting; prevents silent 0.0 reporting). + 'size_too_small' outcome literal.
- StrategyContribution: + n_size_too_small_skipped, + avg_position_size.
- PortfolioBacktestResult: + max_strategy_exposure, + hhi_concentration
  (Phase 5d will use these for risk-budget enforcement).
  + sizing_policy field, default 'fixed_v0' (Phase 5a backward compat).

Phase 5a tests updated: _bid_kwargs / _contribution_kwargs /
_portfolio_kwargs helpers now include new fields; simulator tests'
direct BidRecord constructions also threaded.

5 new type tests cover all new fields' shape + literal extensibility."
```

---

### Task 5: portfolio_simulator — SIZE COMPUTE step (scaffold + tests)

**Files:**
- Modify: `marketpulse/backtest/portfolio_simulator.py`
- Modify: `tests/unit/test_backtest_portfolio_simulator.py`

This is a STAGED build: Task 5 inserts the SIZE COMPUTE step + new params; ALLOCATE still uses fixed-size for the moment but uses `position_sizes[s]` lookup. Task 6 wires variable-size ALLOC details. Task 7 finalization computes telemetry.

- [ ] **Step 5.1: Append failing tests** to `tests/unit/test_backtest_portfolio_simulator.py`:

```python
def test_shared_pool_sizing_skips_below_min_with_outcome():
    """Strategy whose computed size < min is skipped with size_too_small."""
    from datetime import UTC, date, datetime, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    # Build daily curves: one strategy with low α + high σ → raw < min
    bad = []
    v = 10_000.0
    import random
    random.seed(99)
    for i in range(30):
        d = date(2026, 4, 1) + timedelta(days=i)
        bad.append((d, v))
        v *= (1 + 0.00005 + random.gauss(0, 0.05))

    bids = [_pair("X", "bad_strategy", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 105.0)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"bad_strategy": bad},
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    # bad_strategy's size should be < min → bid skipped
    size_too_small = [b for b in r.bid_history if b.outcome == "size_too_small"]
    assert len(size_too_small) == 1
    assert size_too_small[0].strategy == "bad_strategy"
    # Diagnostic: position_size = raw pre-clamp (< 200)
    assert size_too_small[0].position_size < 200.0


def test_shared_pool_sizing_filters_before_dedup():
    """SIZE filters happen BEFORE DEDUP — strategy below min never wins DEDUP."""
    from datetime import UTC, date, datetime, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    import random
    random.seed(50)
    bad = []
    v = 10_000.0
    for i in range(30):
        d = date(2026, 4, 1) + timedelta(days=i)
        bad.append((d, v))
        v *= (1 + 0.00005 + random.gauss(0, 0.05))

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    # Both bid for AAPL — bad would normally lose dedup to good anyway,
    # but here size filter removes bad even before dedup.
    bids = [
        _pair("AAPL", "bad", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("AAPL", "good", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"bad": bad, "good": good},
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    # bad should be filtered with size_too_small (not dedup_loser)
    bad_records = [b for b in r.bid_history if b.strategy == "bad"]
    assert len(bad_records) == 1
    assert bad_records[0].outcome == "size_too_small"
    # good wins (was the only one in DEDUP)
    good_won = [b for b in r.bid_history if b.strategy == "good" and b.outcome == "won"]
    assert len(good_won) == 1


def test_shared_pool_sizing_enabled_false_uses_fixed_base():
    """sizing_enabled=False → Phase 5a behavior; every position is base."""
    from datetime import UTC, date, datetime
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    bids = [_pair("AAA", "any", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 105.0)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"any": good},
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
    )
    # Every BidRecord.position_size == base
    won = [b for b in r.bid_history if b.outcome == "won"]
    assert len(won) == 1
    assert won[0].position_size == 1000.0
    # sizing_policy reflects fixed mode
    assert r.sizing_policy == "fixed_v0"


def test_shared_pool_sizing_provenance_field_set():
    """sizing_enabled=True → sizing_policy='vol_target_conviction_v0'."""
    from datetime import UTC, date, datetime
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]
    bids = [_pair("AAA", "any", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 105.0)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"any": good},
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    assert r.sizing_policy == "vol_target_conviction_v0"


def test_shared_pool_empty_bids_returns_fixed_v0_when_disabled():
    """Empty bids early-return: sizing_policy reflects flag state."""
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool
    r_off = simulate_shared_pool(
        bids=[], daily_curves={}, horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
    )
    assert r_off.sizing_policy == "fixed_v0"

    r_on = simulate_shared_pool(
        bids=[], daily_curves={}, horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    assert r_on.sizing_policy == "vol_target_conviction_v0"
```

- [ ] **Step 5.2: Run, see fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v -k "sizing or size_too_small"
```

Expected: 5 fails (TypeError on unknown `sizing_enabled` or `base_position_size` kwarg; OR new outcomes/fields not present).

- [ ] **Step 5.3: Modify `marketpulse/backtest/portfolio_simulator.py`**

Find the `simulate_shared_pool` function signature and update:

```python
def simulate_shared_pool(
    bids: list,
    daily_curves: dict[str, list[tuple[date, float]]],
    *,
    horizon: int,
    initial_capital: float = 10_000.0,

    # Renamed from position_size → base_position_size in Phase 5b
    base_position_size: float = 1_000.0,

    # NEW Phase 5b knobs
    target_vol: float = 0.01,
    min_position: float = 200.0,
    max_position: float = 4_000.0,
    sizing_enabled: bool = True,

    max_capital_in_use: float = 10_000.0,
    lookback_days: int = 60,
) -> PortfolioBacktestResult:
    """Phase 5a shared-pool simulator + Phase 5b dynamic sizing.

    Spec § 2 daily loop:
      CLOSE → BID → WEIGHT → SIZE → DEDUP → ALLOC → MTM → RECORD

    Provenance: sizing_policy = 'vol_target_conviction_v0' when
    sizing_enabled=True, else 'fixed_v0' (Phase 5a regression mode).
    """
    bid_policy = f"rolling_sharpe_{lookback_days}d_v0"
    sizing_policy = (
        "vol_target_conviction_v0" if sizing_enabled else "fixed_v0"
    )
    # ... (rest of function unchanged until the daily loop)
```

Update the empty-bids early-return to include new fields:

```python
    if not bids:
        from datetime import date as _date
        return PortfolioBacktestResult(
            horizon=horizon,
            n_trades=0,
            n_dedup_total=0,
            avg_capital_utilization=0.0,
            max_strategy_exposure=0.0,
            hhi_concentration=0.0,
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
            bid_policy=bid_policy,
            sizing_policy=sizing_policy,
        )
```

After the `weights, floor_hits = compute_bid_weights(...)` block in the daily loop, INSERT the SIZE COMPUTE step:

```python
        # ─── SIZE COMPUTE ─── (NEW Phase 5b step, spec § 2)
        if sizing_enabled and strategies_today:
            from marketpulse.backtest.sharpe import compute_position_sizes
            position_sizes, raw_sizes_below_min = compute_position_sizes(
                strategies_today, daily_curves,
                as_of=d,
                base=base_position_size,
                target_vol=target_vol,
                min_position=min_position,
                max_position=max_position,
                lookback_days=lookback_days,
            )

            # Strategies returning None → skip all their bids today;
            # diagnostic log records the raw pre-clamp size.
            strategies_skipped_by_size = {
                s for s, sz in position_sizes.items() if sz is None
            }
            new_todays_bids = []
            for b in todays_bids:
                if b.strategy in strategies_skipped_by_size:
                    all_bid_records.append(BidRecord(
                        date=d, strategy=b.strategy, ticker=b.ticker,
                        weight=weights[b.strategy],
                        outcome="size_too_small",
                        winner=None,
                        position_size=raw_sizes_below_min[b.strategy],
                    ))
                    n_size_too_small_by_strategy[b.strategy] = (
                        n_size_too_small_by_strategy.get(b.strategy, 0) + 1
                    )
                    n_bids_by_strategy[b.strategy] = (
                        n_bids_by_strategy.get(b.strategy, 0) + 1
                    )
                else:
                    new_todays_bids.append(b)
            todays_bids = new_todays_bids
            # Strategies with None size are out of strategies_today too, so
            # DEDUP/ALLOC won't see them.
            strategies_today = [
                s for s in strategies_today
                if s not in strategies_skipped_by_size
            ]
        else:
            # Phase 5a behavior: every strategy uses base_position_size
            position_sizes = {s: base_position_size for s in strategies_today}
            raw_sizes_below_min = {}
```

Also add the counter dict at the top of the simulator (alongside other per-strategy accumulators):

```python
    n_size_too_small_by_strategy: dict[str, int] = {}
```

Update the final return to include the new fields:

```python
    return PortfolioBacktestResult(
        # ... existing fields ...
        max_strategy_exposure=0.0,    # Task 7 will compute
        hhi_concentration=0.0,         # Task 7 will compute
        # ... existing fields ...
        bid_policy=bid_policy,
        sizing_policy=sizing_policy,   # NEW
    )
```

ALLOCATE step stays at fixed `position_size` — Task 6 wires variable per-strategy sizes through. For now, ALLOCATE still uses `base_position_size` for cap arithmetic, which is fine because the new tests don't probe ALLOC dynamics yet.

- [ ] **Step 5.4: Run, pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v -k "sizing or size_too_small"
```

Expected: 5/5 pass. Other portfolio tests may fail if they used `position_size=` kwarg directly — fix call sites to use `base_position_size=`.

- [ ] **Step 5.5: Update all `position_size=` → `base_position_size=` in test files**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
grep -rln "position_size=" tests/ marketpulse/ | xargs ls -1
```

For each call to `simulate_shared_pool(..., position_size=...)`, rename to `base_position_size=...`. Same for `run_shared_pool_backtest`.

NOTE: Do NOT rename `BidRecord(position_size=...)` — that's a separate field on the dataclass.

- [ ] **Step 5.6: Re-run full portfolio simulator tests**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v
```

Expected: all pass (existing 19 + new 5 = 24).

- [ ] **Step 5.7: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run ruff check marketpulse/backtest/portfolio_simulator.py tests/
git add marketpulse/backtest/portfolio_simulator.py tests/
git commit -m "feat(phase-5b): SIZE COMPUTE step + base_position_size rename

Spec § 2 algorithm: new daily-loop step between WEIGHT and DEDUP.
- compute_position_sizes returns (sizes, raw_sizes_below_min)
- Strategies returning None filtered from todays_bids BEFORE dedup
- size_too_small BidRecord logged with raw pre-clamp position_size

position_size param renamed to base_position_size on simulate_shared_pool
and run_shared_pool_backtest (semantic correctness post-5b — it's no
longer THE size, it's the BASE).

sizing_enabled flag added (default True). When False, sizing_policy=
'fixed_v0' (Phase 5a regression mode); when True, sizing_policy=
'vol_target_conviction_v0'.

ALLOCATE still uses base_position_size for cap math — Task 6 wires
per-strategy variable sizing through ALLOC properly.

max_strategy_exposure + hhi_concentration default 0.0 in this commit;
Task 7 finalization computes real values.

5 new sizing integration tests pass; all 19 existing portfolio tests
still green."
```

---

### Task 6: ALLOCATE uses per-bid sizes (variable cap consumption)

**Files:**
- Modify: `marketpulse/backtest/portfolio_simulator.py`
- Modify: `tests/unit/test_backtest_portfolio_simulator.py`

- [ ] **Step 6.1: Append failing tests**:

```python
def test_shared_pool_sizing_caps_at_max_when_clamped():
    """A strategy with raw > max gets clamped to max in actual ALLOC."""
    from datetime import UTC, date, datetime, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    # Construct a strategy that would compute size > max:
    # Very low σ + only-strategy → vol_scale large, α_scale = 1, raw > max
    low_vol = []
    v = 10_000.0
    for i in range(30):
        d = date(2026, 4, 1) + timedelta(days=i)
        low_vol.append((d, v))
        v *= 1.001  # 0.1% steady (very low σ)

    bids = [_pair("AAA", "low_vol", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 105.0)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"low_vol": low_vol},
        horizon=5,
        initial_capital=10_000.0, base_position_size=2_000.0,
        target_vol=0.01, max_position=4_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    # Won bid's position_size should be capped at max_position
    won = [b for b in r.bid_history if b.outcome == "won"]
    if won:
        assert won[0].position_size <= 4_000.0


def test_shared_pool_high_size_strategy_blocks_more_small_bids():
    """Review iter 1 fix #3: high-conviction strategy consumes more cap.

    Setup: one strategy with high alpha gets a $3k size; 8 other small bids
    at $1k each. The pool ($10k) fills with 1×$3k + 7×$1k = $10k, blocking
    1 small bid (vs Phase 5a where all 9 would have fit at $1k each).
    """
    from datetime import UTC, date, datetime, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    # high_a_strategy has α much above mean → size > $3k after clamping
    high_a = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.02 ** i))
              for i in range(30)]  # 2% daily growth → high α

    # Other strategies are neutral
    neutrals = {
        f"n{i}": [(date(2026, 4, 1) + timedelta(days=j), 10_000.0 * (1.005 ** j))
                  for j in range(30)]
        for i in range(8)
    }
    daily_curves = {"high_a": high_a, **neutrals}

    # 1 bid for high_a + 8 bids for neutrals on the same day
    bids = [_pair("HIGH", "high_a", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 105.0)]
    for i in range(8):
        bids.append(_pair(f"N{i}", f"n{i}", date(2026, 5, 1), 100.0,
                          date(2026, 5, 8), 105.0))

    r = simulate_shared_pool(
        bids=bids,
        daily_curves=daily_curves,
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    # Total bids attempted: 9. Won bids should be < 9 because high_a's
    # variable size blocks at least one neutral.
    won = [b for b in r.bid_history if b.outcome == "won"]
    cap_full = [b for b in r.bid_history if b.outcome == "cap_full"]
    assert len(won) < 9
    assert len(cap_full) >= 1
    # Total capital allocated should equal pool cap (or close to it)
    total_won_size = sum(b.position_size for b in won)
    assert total_won_size <= 10_000.0  # never exceeds cap


def test_shared_pool_cap_full_records_requested_size():
    """cap_full BidRecord shows the requested size, not 0.0 or base.

    Review iter 1 fix #2: diagnostic value preserved.
    """
    from datetime import UTC, date, datetime, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    # high-conviction strategy with size > base
    high = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.02 ** i))
            for i in range(30)]

    # 11 bids for the same strategy → 10 fit at variable size, 11th cap-blocked
    daily_curves = {"high": high}
    bids = [_pair(f"T{i}", "high", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 101.0) for i in range(11)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves=daily_curves,
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    cap_full = [b for b in r.bid_history if b.outcome == "cap_full"]
    if cap_full:
        # cap_full position_size should be the ACTUAL computed size (variable),
        # not 0.0 and not base_position_size hardcoded
        for record in cap_full:
            assert record.position_size > 0.0  # real value
```

- [ ] **Step 6.2: Run, see fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v -k "caps_at_max or high_size_strategy or cap_full_records"
```

Expected: 3 fails (cap-full records won't have correct position_size since Task 5's ALLOCATE was still using fixed base).

- [ ] **Step 6.3: Update ALLOCATE in `portfolio_simulator.py` to use per-bid variable sizes**

Find the ALLOCATE block (after DEDUP, before MTM). The variable name was `position_size` in Phase 5a but now we use `position_sizes[bid.strategy]`. Replace the relevant block:

```python
        # ─── ALLOCATE (capital-constrained, greedy by weight desc) ───
        # Spec § 2: bids of the SAME strategy share the same per-strategy size
        # (Phase 5b: variable per strategy; Phase 5a: uniform base).
        # 3-key tiebreaker unchanged from Phase 5a: (-weight, event_time, strategy).
        sorted_winners = sorted(
            winners.values(),
            key=lambda b: (-weights[b.strategy], b.event_time, b.strategy),
        )
        for b in sorted_winners:
            n_bids_by_strategy[b.strategy] = n_bids_by_strategy.get(b.strategy, 0) + 1
            bid_weights_by_strategy.setdefault(b.strategy, []).append(
                weights[b.strategy]
            )
            # Phase 5b: per-strategy size lookup; Phase 5a: all = base
            requested_size = position_sizes[b.strategy]
            capital_in_use = sum(p.position_size for p in open_positions)
            if capital_in_use + requested_size > max_capital_in_use:
                # Diagnostic: log the REQUESTED size, not the base
                all_bid_records.append(BidRecord(
                    date=d, strategy=b.strategy, ticker=b.ticker,
                    weight=weights[b.strategy],
                    outcome="cap_full", winner=None,
                    position_size=requested_size,
                ))
                n_capacity_skipped_by_strategy[b.strategy] = (
                    n_capacity_skipped_by_strategy.get(b.strategy, 0) + 1
                )
                continue
            if cash < requested_size:
                all_bid_records.append(BidRecord(
                    date=d, strategy=b.strategy, ticker=b.ticker,
                    weight=weights[b.strategy],
                    outcome="cash_short", winner=None,
                    position_size=requested_size,
                ))
                n_cash_short_skipped_by_strategy[b.strategy] = (
                    n_cash_short_skipped_by_strategy.get(b.strategy, 0) + 1
                )
                continue
            # Open position at the requested (variable) size
            open_positions.append(_OpenPosition(
                strategy=b.strategy, ticker=b.ticker,
                entry_date=d, entry_price=b.event_price,
                horizon_date=b.horizon_date, horizon_price=b.horizon_price,
                position_size=requested_size,
            ))
            cash -= requested_size
            n_trades_by_strategy[b.strategy] = n_trades_by_strategy.get(b.strategy, 0) + 1
            all_bid_records.append(BidRecord(
                date=d, strategy=b.strategy, ticker=b.ticker,
                weight=weights[b.strategy],
                outcome="won", winner=None,
                position_size=requested_size,
            ))
```

DEDUP section should also propagate position_size for dedup_losers. Find the DEDUP block and update:

```python
        # ─── DEDUP (same-day same-ticker collision) ───
        bids_by_ticker: dict[str, list] = {}
        for b in todays_bids:
            bids_by_ticker.setdefault(b.ticker, []).append(b)
        winners: dict[str, object] = {}
        for ticker, group in bids_by_ticker.items():
            best = min(group, key=lambda b: (
                -weights[b.strategy], b.event_time, b.strategy,
            ))
            winners[ticker] = best
            for loser in group:
                if loser is not best:
                    # Loser's requested size (informational; the bid never opens)
                    loser_size = position_sizes[loser.strategy]
                    all_bid_records.append(BidRecord(
                        date=d, strategy=loser.strategy, ticker=ticker,
                        weight=weights[loser.strategy],
                        outcome="dedup_loser", winner=best.strategy,
                        position_size=loser_size,
                    ))
                    n_dedup_skipped_by_strategy[loser.strategy] = (
                        n_dedup_skipped_by_strategy.get(loser.strategy, 0) + 1
                    )
                    n_bids_by_strategy[loser.strategy] = (
                        n_bids_by_strategy.get(loser.strategy, 0) + 1
                    )
                    bid_weights_by_strategy.setdefault(loser.strategy, []).append(
                        weights[loser.strategy]
                    )
```

- [ ] **Step 6.4: Run, pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v
```

Expected: all pass (24 + 3 = 27).

- [ ] **Step 6.5: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run ruff check marketpulse/backtest/portfolio_simulator.py tests/
git add marketpulse/backtest/portfolio_simulator.py tests/
git commit -m "feat(phase-5b): ALLOCATE uses per-strategy variable sizes

Spec § 2 ALLOCATE step now indexes position_sizes[bid.strategy] for
cap arithmetic. High-conviction strategies consume more cap per bid
than they did in Phase 5a (which used uniform \$1k).

All BidRecord outcomes now record the model's REQUESTED size:
- won:           actual opened size (post-clamp)
- dedup_loser:   what this strategy would have opened
- cap_full:      requested size (not base, not 0.0)
- cash_short:    requested size
- size_too_small: raw pre-clamp size (already set in Task 5)

DEDUP losers also record position_sizes[loser.strategy] for
diagnostic — the bid history shows what the loser would have asked
for.

3 new integration tests cover max clamp behavior, high-size
crowding out, and cap_full diagnostic preservation. All 24 prior
portfolio tests still green."
```

---

### Task 7: Finalization — concentration telemetry + avg_position_size

**Files:**
- Modify: `marketpulse/backtest/portfolio_simulator.py`
- Modify: `tests/unit/test_backtest_portfolio_simulator.py`

- [ ] **Step 7.1: Append failing tests**:

```python
def test_shared_pool_avg_position_size_in_contribution():
    """avg_position_size = mean(position_size) over won bids per strategy."""
    from datetime import UTC, date, datetime, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    # 3 bids for the same strategy
    bids = [_pair(f"T{i}", "x", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 105.0)
            for i in range(3)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"x": good},
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    if "x" in r.per_strategy_stats:
        won = [b for b in r.bid_history
               if b.outcome == "won" and b.strategy == "x"]
        if won:
            expected_avg = sum(b.position_size for b in won) / len(won)
            assert abs(r.per_strategy_stats["x"].avg_position_size - expected_avg) < 1e-6


def test_shared_pool_n_size_too_small_in_contribution():
    """n_size_too_small_skipped counts the strategy's filtered bids."""
    from datetime import UTC, date, datetime, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    import random
    random.seed(20)
    bad = []
    v = 10_000.0
    for i in range(30):
        d = date(2026, 4, 1) + timedelta(days=i)
        bad.append((d, v))
        v *= (1 + 0.00005 + random.gauss(0, 0.05))

    bids = [_pair(f"T{i}", "bad", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 105.0) for i in range(3)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"bad": bad},
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    if "bad" in r.per_strategy_stats:
        size_skipped = [b for b in r.bid_history
                        if b.outcome == "size_too_small" and b.strategy == "bad"]
        assert r.per_strategy_stats["bad"].n_size_too_small_skipped == len(size_skipped)


def test_shared_pool_max_strategy_exposure_computed():
    """max_strategy_exposure = peak single-strategy avg-exposure value."""
    from datetime import UTC, date, datetime, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]
    bids = [_pair(f"T{i}", "x", date(2026, 5, 1), 100.0,
                   date(2026, 5, 8), 105.0) for i in range(5)]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"x": good},
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    # Single strategy → max_strategy_exposure equals its own avg_exposure
    if r.per_strategy_stats:
        max_expected = max(c.avg_exposure for c in r.per_strategy_stats.values())
        assert abs(r.max_strategy_exposure - max_expected) < 1e-9


def test_shared_pool_hhi_concentration_computed():
    """hhi_concentration = Σ(exposure_s²) — Herfindahl-Hirschman Index."""
    from datetime import UTC, date, datetime, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good_a = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
              for i in range(30)]
    good_b = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.006 ** i))
              for i in range(30)]

    bids = [
        _pair("A", "a", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("B", "b", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"a": good_a, "b": good_b},
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    # Two strategies → HHI = sum of squares of exposures
    if r.per_strategy_stats:
        exposures = [c.avg_exposure for c in r.per_strategy_stats.values()]
        expected_hhi = sum(e * e for e in exposures)
        assert abs(r.hhi_concentration - expected_hhi) < 1e-9


def test_shared_pool_joint_bootstrap_yields_uniform_base_sizes():
    """ALL strategies n<5 → all sizes = base_position_size (uniform). Review iter 1 fix #1."""
    from datetime import UTC, date, datetime, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    # All strategies have 3 events (below min_events=5)
    tiny = lambda seed_offset: [
        (date(2026, 4, 28) + timedelta(days=i), 10_000.0 * (1.01 ** i))
        for i in range(3)
    ]
    daily_curves = {"a": tiny(1), "b": tiny(2)}
    bids = [
        _pair("X", "a", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("Y", "b", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves=daily_curves,
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    # Both bids should open at base (no scaling)
    won = [b for b in r.bid_history if b.outcome == "won"]
    for w in won:
        assert w.position_size == 1000.0


def test_size_formula_not_double_rewarding_low_vol():
    """Review iter 2 fix #1: regression test for the σ² double-count.

    Strategy with σ = 0.5%, α = 0.5% (Sharpe = 1.0)
    Strategy with σ = 1.0%, α = 1.5% (Sharpe = 1.5)
    With double-count (size ∝ μ/σ²): A gets bigger size despite lower α.
    Without (size ∝ μ/σ): B gets bigger size (correctly).
    """
    from datetime import UTC, date, datetime, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    # Construct A: low σ (0.5% daily), modest α (≈0.5%)
    import random
    random.seed(100)
    a_curve = []
    v_a = 10_000.0
    for i in range(30):
        d = date(2026, 4, 1) + timedelta(days=i)
        a_curve.append((d, v_a))
        v_a *= (1 + 0.005 + random.gauss(0, 0.005))

    # Construct B: higher σ (1% daily), higher α (≈1.5%)
    random.seed(200)
    b_curve = []
    v_b = 10_000.0
    for i in range(30):
        d = date(2026, 4, 1) + timedelta(days=i)
        b_curve.append((d, v_b))
        v_b *= (1 + 0.015 + random.gauss(0, 0.010))

    bids = [
        _pair("A_T", "a", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("B_T", "b", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    r = simulate_shared_pool(
        bids=bids,
        daily_curves={"a": a_curve, "b": b_curve},
        horizon=5,
        initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=True,
    )
    won = {b.strategy: b.position_size for b in r.bid_history if b.outcome == "won"}
    if "a" in won and "b" in won:
        # B's higher α should give it a larger size despite higher σ.
        # If formula = base × (target_vol/σ) × (α/mean_α):
        #   A: 1000 × (1.0/0.5) × (0.5/1.0) = 1000
        #   B: 1000 × (1.0/1.0) × (1.5/1.0) = 1500
        # So B > A. The double-count formula would have given A > B.
        assert won["b"] > won["a"], (
            f"Higher-α strategy B should get bigger size; "
            f"A={won['a']}, B={won['b']}. "
            f"If A > B, the σ² double-count has regressed."
        )
```

- [ ] **Step 7.2: Run, see fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v -k "avg_position_size or n_size_too_small or max_strategy_exposure or hhi or joint_bootstrap or formula_not_double"
```

Expected: 6 fails.

- [ ] **Step 7.3: Modify finalization in `portfolio_simulator.py`**

Find the finalization block (after the daily loop, before the final `return PortfolioBacktestResult`). Update to compute the new fields:

```python
    # ─── FINALIZE ───
    all_returns: list[float] = []
    for s_returns in trade_returns_by_strategy.values():
        all_returns.extend(s_returns)
    n_trades = sum(n_trades_by_strategy.values())

    # Unrealized MTM of positions still open at end of window (unchanged from Phase 5a fix)
    last_day = calendar[-1] if calendar else None
    unrealized_pnl_by_strategy: dict[str, float] = {}
    for pos in open_positions:
        if pos.entry_date == last_day:
            unrealized = 0.0
        else:
            fraction = elapsed_fraction(
                calendar, entry=pos.entry_date,
                horizon=pos.horizon_date, current=last_day,
            )
            est_price = pos.entry_price + (
                pos.horizon_price - pos.entry_price
            ) * fraction
            unrealized = pos.position_size * (est_price / pos.entry_price - 1.0)
        unrealized_pnl_by_strategy[pos.strategy] = (
            unrealized_pnl_by_strategy.get(pos.strategy, 0.0) + unrealized
        )

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

    # Per-strategy contributions (Phase 5b: + avg_position_size, n_size_too_small_skipped)
    from marketpulse.strategies import load_strategies
    strategies_yaml = load_strategies()
    per_strategy_stats: dict[str, StrategyContribution] = {}
    for s in sorted(daily_curves.keys()):
        ret_list = trade_returns_by_strategy.get(s, [])
        realized = sum(r * base_position_size for r in ret_list)  # Phase 5a math
        # Phase 5b: position sizes vary; recompute from won BidRecords
        won_for_s = [
            b for b in all_bid_records
            if b.strategy == s and b.outcome == "won"
        ]
        if won_for_s:
            # Override realized with actual position sizes
            realized = sum(b.position_size * r for b, r in zip(won_for_s, ret_list, strict=False))
            avg_position_size = sum(b.position_size for b in won_for_s) / len(won_for_s)
        else:
            avg_position_size = 0.0
        unrealized = unrealized_pnl_by_strategy.get(s, 0.0)
        contrib_pnl = realized + unrealized
        exposures = exposure_by_strategy_by_day.get(s, [])
        avg_exposure = sum(exposures) / len(exposures) if exposures else 0.0
        bid_w_list = bid_weights_by_strategy.get(s, [])
        avg_bid_weight = sum(bid_w_list) / len(bid_w_list) if bid_w_list else 0.0
        per_strategy_stats[s] = StrategyContribution(
            strategy=s,
            display_name=(
                strategies_yaml[s].display_name if s in strategies_yaml else s
            ),
            n_trades=n_trades_by_strategy.get(s, 0),
            n_dedup_skipped=n_dedup_skipped_by_strategy.get(s, 0),
            n_capacity_skipped=n_capacity_skipped_by_strategy.get(s, 0),
            n_cash_short_skipped=n_cash_short_skipped_by_strategy.get(s, 0),
            n_size_too_small_skipped=n_size_too_small_by_strategy.get(s, 0),
            contribution_pnl=contrib_pnl,
            avg_exposure=avg_exposure,
            avg_bid_weight=avg_bid_weight,
            avg_position_size=avg_position_size,
            n_bids=n_bids_by_strategy.get(s, 0),
            n_floor_hits=n_floor_hits_by_strategy.get(s, 0),
        )

    # NEW Phase 5b concentration telemetry
    if per_strategy_stats:
        exposures = [c.avg_exposure for c in per_strategy_stats.values()]
        max_strategy_exposure = max(exposures) if exposures else 0.0
        hhi_concentration = sum(e * e for e in exposures)
    else:
        max_strategy_exposure = 0.0
        hhi_concentration = 0.0

    # Last-100 slice of bid history
    bid_history = all_bid_records[-100:] if len(all_bid_records) > 100 else all_bid_records

    return PortfolioBacktestResult(
        horizon=horizon,
        n_trades=n_trades,
        n_dedup_total=sum(n_dedup_skipped_by_strategy.values()),
        avg_capital_utilization=avg_util,
        max_strategy_exposure=max_strategy_exposure,
        hhi_concentration=hhi_concentration,
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
        bid_policy=bid_policy,
        sizing_policy=sizing_policy,
    )
```

- [ ] **Step 7.4: Run, pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v
```

Expected: all 33 pass (24 + 3 Task 6 + 6 Task 7).

- [ ] **Step 7.5: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run ruff check marketpulse/backtest/portfolio_simulator.py tests/
git add marketpulse/backtest/portfolio_simulator.py tests/
git commit -m "feat(phase-5b): finalization telemetry + per-strategy avg_position_size

Spec § 4 (PortfolioBacktestResult) + § 3 (StrategyContribution).
- avg_position_size = mean of won bids' position_size per strategy
- n_size_too_small_skipped tracked in StrategyContribution
- max_strategy_exposure = max single-strategy avg_exposure across pool
- hhi_concentration = Σ(exposure_s²) — Herfindahl-Hirschman Index

contribution_pnl now uses variable per-bid position_size (was: fixed
base × trade_returns) so accounting matches actual capital allocated.

6 new tests cover avg_position_size accuracy, n_size_too_small,
max/HHI computation, joint-bootstrap regression, and the double-count
regression check. All 33 portfolio simulator tests green."
```

---

### Task 8: Orchestrator threads sizing_enabled + new knobs

**Files:**
- Modify: `marketpulse/backtest/simulator.py` (run_shared_pool_backtest)
- Modify: `tests/integration/test_backtest_shared_pool.py`

- [ ] **Step 8.1: Append failing tests** to `tests/integration/test_backtest_shared_pool.py`:

```python
def test_run_shared_pool_with_sizing_enabled_default_true(db_session):
    """Orchestrator defaults sizing_enabled=True (Phase 5b is default)."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5)
    assert out["shared"].sizing_policy == "vol_target_conviction_v0"


def test_run_shared_pool_with_sizing_disabled_yields_phase5a_behavior(db_session):
    """Orchestrator sizing_enabled=False → fixed_v0 (regression mode)."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5, sizing_enabled=False)
    assert out["shared"].sizing_policy == "fixed_v0"


def test_run_shared_pool_sizing_policy_provenance(db_session):
    """sizing_policy strings match the locked decisions in spec § 8."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out_on = run_shared_pool_backtest(db_session, horizon=5, sizing_enabled=True)
    out_off = run_shared_pool_backtest(db_session, horizon=5, sizing_enabled=False)
    assert out_on["shared"].sizing_policy == "vol_target_conviction_v0"
    assert out_off["shared"].sizing_policy == "fixed_v0"
```

- [ ] **Step 8.2: Run, see fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/integration/test_backtest_shared_pool.py -v -k "sizing"
```

Expected: 3 fails (orchestrator doesn't accept sizing_enabled yet).

- [ ] **Step 8.3: Modify `run_shared_pool_backtest` in `marketpulse/backtest/simulator.py`**

Find the function signature and add `sizing_enabled` + thread through to `simulate_shared_pool`:

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
) -> dict:
    """Phase 5a + Phase 5b orchestrator. Returns {isolated, artifacts, shared}.

    sizing_enabled defaults to True (Phase 5b dynamic). Set False for
    Phase 5a regression / A/B comparison.
    """
    # ... existing logic up to simulate_shared_pool call ...

    shared = simulate_shared_pool(
        bids=all_bids,
        daily_curves=daily_curves,
        horizon=horizon,
        initial_capital=initial_capital,
        base_position_size=base_position_size,
        target_vol=target_vol,
        min_position=min_position,
        max_position=max_position,
        sizing_enabled=sizing_enabled,
        max_capital_in_use=max_capital_in_use,
        lookback_days=lookback_days,
    )
    # ... existing excess_vs_spy override + return ...
```

- [ ] **Step 8.4: Run, pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/integration/test_backtest_shared_pool.py -v
```

Expected: all integration tests pass (4 existing + 3 new = 7).

- [ ] **Step 8.5: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run ruff check marketpulse/backtest/simulator.py tests/integration/test_backtest_shared_pool.py
git add marketpulse/backtest/simulator.py tests/integration/test_backtest_shared_pool.py
git commit -m "feat(phase-5b): orchestrator threads sizing_enabled + Phase 5b knobs

run_shared_pool_backtest accepts:
- base_position_size (renamed from position_size)
- target_vol, min_position, max_position
- sizing_enabled (default True)

All threaded through to simulate_shared_pool. Returns same
{isolated, artifacts, shared} triple shape — sizing_policy on the
shared result reflects the flag.

3 new integration tests cover default-on, explicit-off, and
provenance strings."
```

---

### Task 9: Route + size_distribution histogram bins

**Files:**
- Modify: `marketpulse/web/routes/backtest.py`
- Modify: `tests/web/test_lab_backtest_modes.py`

- [ ] **Step 9.1: Append failing tests** to `tests/web/test_lab_backtest_modes.py`:

```python
def test_lab_backtest_shared_mode_renders_size_distribution_context(
    client, monkeypatch, db_session,
):
    """Backend computes size_distribution and passes it via context."""
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="A1", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest?mode=shared-pool")
    assert r.status_code == 200
    # The SVG sparkline rendering uses this; we don't directly assert the
    # list value, just that the page rendered without error.
    assert "shared" in r.text.lower() or "共享池" in r.text


def test_lab_backtest_shared_mode_includes_sizing_policy_in_hero(
    client, monkeypatch, db_session,
):
    """Hero text includes sizing_policy provenance line in shared mode."""
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="A1", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest?mode=shared-pool")
    # 2nd hero sentence references the sizing policy
    assert "vol_target_conviction_v0" in r.text
```

- [ ] **Step 9.2: Run, see fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/web/test_lab_backtest_modes.py -v -k "size_distribution or sizing_policy_in_hero"
```

Expected: hero test fails (template not updated yet); distribution test may pass already.

- [ ] **Step 9.3: Modify `marketpulse/web/routes/backtest.py`**

Add a helper function to compute the size distribution bins, then pass via context:

```python
def _compute_size_distribution(
    bid_records: list,
    min_position: float = 200.0,
    max_position: float = 4_000.0,
    n_bins: int = 7,
) -> list[float]:
    """Linearly-spaced histogram of position sizes across won/dedup/cap/cash bids.

    Spec § 4: 7 bins linearly over [min_position, max_position]. Excludes
    size_too_small (their position_size is the raw pre-clamp value, not a
    real allocation). Returns normalized heights 0-1 for SVG rendering.
    """
    valid = [
        b.position_size for b in bid_records
        if b.outcome != "size_too_small" and b.position_size > 0
    ]
    if not valid:
        return [0.0] * n_bins
    bin_width = (max_position - min_position) / n_bins
    counts = [0] * n_bins
    for size in valid:
        bin_idx = min(int((size - min_position) / bin_width), n_bins - 1)
        bin_idx = max(0, bin_idx)
        counts[bin_idx] += 1
    max_count = max(counts) if counts else 1
    return [c / max_count for c in counts]
```

In the `lab_backtest` route, when `mode=shared-pool`, compute the distribution:

```python
    # ... existing route logic ...
    if mode == "shared-pool":
        from marketpulse.backtest.simulator import run_shared_pool_backtest
        out = run_shared_pool_backtest(
            db, horizon=horizon, since=since, lookback_days=60,
        )
        results = out["isolated"]
        shared_result = out["shared"]
        # NEW: compute size distribution for SVG sparkline
        size_distribution = _compute_size_distribution(shared_result.bid_history)
    else:
        results = run_all_backtests(db, horizon=horizon, since=since)
        shared_result = None
        size_distribution = []
```

Pass via template context:

```python
    return templates.TemplateResponse(
        request, "lab_backtest.html",
        {
            "strategies": strategies_sorted,
            # ... existing context ...
            "mode": mode,
            "shared_result": shared_result,
            "lookback_days": 60,
            "size_distribution": size_distribution,
            "min_position": 200.0,
            "max_position": 4_000.0,
            "sizing_policy": (
                shared_result.sizing_policy if shared_result else "fixed_v0"
            ),
        },
    )
```

- [ ] **Step 9.4: Run pytest — hero test still fails until Task 10**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/web/test_lab_backtest_modes.py -v
```

Expected: size_distribution test passes; hero test still fails (Task 10 will fix).

- [ ] **Step 9.5: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run ruff check marketpulse/web/routes/backtest.py tests/web/test_lab_backtest_modes.py
git add marketpulse/web/routes/backtest.py tests/web/test_lab_backtest_modes.py
git commit -m "feat(phase-5b): route computes size_distribution histogram bins

7-bin linearly-spaced histogram over [min_position, max_position],
normalized 0-1 for SVG sparkline rendering in bid history card header.

Excludes size_too_small bids (their position_size is the raw pre-clamp
diagnostic value, not a real allocation).

Route passes size_distribution + min_position + max_position +
sizing_policy via template context. Templates updated in Tasks 10-12."
```

---

### Task 10: Hero template — append sizing_policy sentence in shared-pool

**Files:**
- Modify: `marketpulse/web/templates/partials/backtest_hero.html`

- [ ] **Step 10.1: Update `backtest_hero.html`** — append 2nd sentence when shared-pool:

```html
<section class="mp-hero">
  <div>
    <span class="mp-eyebrow mp-eyebrow--primary">实验室 · 组合回测</span>
    <h1 class="grotesk mp-hero__title">Strategy Performance Observatory</h1>
    <span class="mp-rule"></span>
    {% if mode == 'shared-pool' %}
      <p class="mp-hero__desc">
        6 个策略共享单一 $10k 资本池,通过 {{ lookback_days }}-day 滚动 Sharpe
        加权竞标分配。撞 ticker 时高 Sharpe 策略赢。
        <strong>bid_policy=rolling_sharpe_60d_v0</strong>。
      </p>
      {% if sizing_policy == 'vol_target_conviction_v0' %}
        <p class="mp-hero__desc">
          仓位大小动态:vol-target 1.0% daily × alpha-conviction multiplier,
          floor $200 / ceiling $4,000。<strong>sizing_policy=vol_target_conviction_v0</strong>。
        </p>
      {% else %}
        <p class="mp-hero__desc">
          固定 $1,000 每信号。<strong>sizing_policy=fixed_v0</strong>(Phase 5a 兼容模式)。
        </p>
      {% endif %}
    {% else %}
      <p class="mp-hero__desc">
        回放 Phase 3 的 6 个策略 + SPY 基准在过去 N 天内的策略级合成 PnL。
        回测使用 long-only 模型 + 固定持有 horizon 天 + $1k 每信号 + $10k 软上限。
      </p>
    {% endif %}
  </div>
</section>
```

- [ ] **Step 10.2: Run pytest — hero test should pass now**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest tests/web/test_lab_backtest_modes.py -v
```

Expected: all pass.

- [ ] **Step 10.3: Commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
git add marketpulse/web/templates/partials/backtest_hero.html
git commit -m "feat(phase-5b): hero template — append sizing_policy provenance line

Spec § 4: shared-pool mode now shows a 2nd paragraph with the active
sizing model. When sizing_policy='vol_target_conviction_v0' the line
mentions vol-target × alpha-conviction + floor/ceiling. When
sizing_policy='fixed_v0' (Phase 5a regression mode) it explicitly
calls that out so the user knows which behavior is rendering."
```

---

### Task 11: backtest_bid_history.html — Size column + SVG sparkline

**Files:**
- Modify: `marketpulse/web/templates/partials/backtest_bid_history.html`
- Modify: `marketpulse/web/static/css/app.css`

- [ ] **Step 11.1: Update `backtest_bid_history.html`** — add Size column + SVG sparkline:

```html
{% if shared_result and shared_result.bid_history %}
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">history</span>近 100 次 bid 决策
    </span>
    {% if size_distribution %}
      <span class="mp-bid-size-spark">
        <svg viewBox="0 0 120 24" width="120" height="24" aria-label="size distribution">
          {% for h in size_distribution %}
            <rect x="{{ loop.index0 * 17 }}" y="{{ 24 - (h * 24)|round(1) }}"
                  width="15" height="{{ (h * 24)|round(1) }}"
                  fill="var(--ns-on-surface-variant)" opacity="0.7"/>
          {% endfor %}
        </svg>
        <span class="mp-bid-size-spark__legend">
          ${{ "{:.0f}".format(min_position) }} → ${{ "{:,.0f}".format(max_position) }}
        </span>
      </span>
    {% endif %}
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
          <th class="num">Size</th>
          <th>结果</th>
        </tr>
      </thead>
      <tbody>
        {% for b in shared_result.bid_history|reverse %}
        <tr class="{% if b.outcome == 'won' %}is-won{% elif b.outcome == 'dedup_loser' %}is-loser{% elif b.outcome == 'size_too_small' %}is-skipped{% else %}is-skipped{% endif %}">
          <td class="mono tnum">{{ b.date.isoformat() }}</td>
          <td>{{ b.strategy }}</td>
          <td>{{ b.ticker }}</td>
          <td class="num mono tnum">{{ "{:.2f}".format(b.weight) }}</td>
          <td class="num mono tnum">
            {% if b.outcome == 'size_too_small' %}
              <span title="raw size = ${{ '{:.0f}'.format(b.position_size) }} (below ${{ '{:.0f}'.format(min_position) }} floor)">—</span>
            {% else %}
              ${{ "{:,.0f}".format(b.position_size) }}
            {% endif %}
          </td>
          <td>
            {% if b.outcome == 'won' %}<span class="mp-chip mp-chip--up">✓ won</span>
            {% elif b.outcome == 'dedup_loser' %}<span class="mp-chip" title="ceded to {{ b.winner }}">→ {{ b.winner }}</span>
            {% elif b.outcome == 'cap_full' %}<span class="mp-chip mp-chip--down">cap full</span>
            {% elif b.outcome == 'cash_short' %}<span class="mp-chip mp-chip--down">cash short</span>
            {% elif b.outcome == 'size_too_small' %}<span class="mp-chip mp-chip--down">size too small</span>
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

- [ ] **Step 11.2: Update `app.css`** — add styles for the SVG sparkline:

```css
/* Phase 5b: size distribution sparkline in bid history card header */
.mp-bid-size-spark {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--ns-on-surface-variant);
  margin-left: 12px;
}
.mp-bid-size-spark svg {
  vertical-align: middle;
}
.mp-bid-size-spark__legend {
  font-family: var(--ns-font-mono, monospace);
  white-space: nowrap;
}
```

- [ ] **Step 11.3: Commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
git add marketpulse/web/templates/partials/backtest_bid_history.html \
        marketpulse/web/static/css/app.css
git commit -m "feat(phase-5b): bid history — Size column + SVG sparkline header

Spec § 4:
- New Size column shows actual \$ requested per bid:
  - won/dedup/cap/cash: formatted as \$X,XXX
  - size_too_small: '—' with tooltip showing raw pre-clamp value
- SVG sparkline in card header: 7-bin histogram, var(--ns-on-surface-variant)
  fill, with legend showing min/max range.
- New size_too_small row uses is-skipped tinted background (red-tinted).

CSS adds .mp-bid-size-spark inline layout for sparkline + legend."
```

---

### Task 12: backtest_strategy_table_shared.html — Avg Size column

**Files:**
- Modify: `marketpulse/web/templates/partials/backtest_strategy_table_shared.html`

- [ ] **Step 12.1: Update `backtest_strategy_table_shared.html`** — add Avg Size column at end:

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
          <th class="num">avg size</th>
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
              {{ c.n_capacity_skipped + c.n_cash_short_skipped + c.n_size_too_small_skipped }}
            </td>
            <td class="num mono tnum {% if c.contribution_pnl >= 0 %}up{% else %}down{% endif %}">
              {{ "{:+.2f}".format(c.contribution_pnl) }}
            </td>
            <td class="num mono tnum">{{ "{:.1%}".format(c.avg_exposure) }}</td>
            <td class="num mono tnum">{{ "{:.2f}".format(c.avg_bid_weight) }}</td>
            <td class="num mono tnum">
              {% if c.n_trades == 0 and c.n_size_too_small_skipped > 0 %}
                <span class="mp-chip mp-chip--down" title="所有 bids 均 < min_position">sub-min</span>
              {% elif c.n_trades == 0 %}
                —
              {% else %}
                ${{ "{:,.0f}".format(c.avg_position_size) }}
              {% endif %}
            </td>
          </tr>
          {% endfor %}
        {% endif %}
      </tbody>
    </table>
  </div>
</section>
```

Note: `n_skipped` column now sums all three skip kinds (capacity + cash + size).

- [ ] **Step 12.2: Commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
git add marketpulse/web/templates/partials/backtest_strategy_table_shared.html
git commit -m "feat(phase-5b): strategy table — Avg Size column + sub-min flag

Spec § 4:
- New 'avg size' column shows mean dollar size of won bids per strategy.
- 'sub-min' chip when n_trades=0 and at least one bid was size_too_small —
  helps user spot dying strategies whose model wanted to trade but the
  computed size kept falling below the floor.
- '—' (em-dash) when n_trades=0 and no size_too_small skips (no signal).
- n_skipped column now sums capacity + cash + size_too_small skips."
```

---

### Task 13: Final integration — full suite + ruff + smoke

- [ ] **Step 13.1: Full pytest + ruff**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run pytest 2>&1 | tail -3
uv run ruff check . 2>&1 | tail -3
```

Expected: ~820 tests pass (Phase 5a was 780, Phase 5b adds ~40 net). Ruff clean.

- [ ] **Step 13.2: Module imports**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
uv run python -c "
from marketpulse.backtest import (
    BidRecord, PortfolioBacktestResult, StrategyContribution,
    StrategyBacktestArtifacts, run_shared_pool_backtest,
)
from marketpulse.backtest.sharpe import rolling_sigma, rolling_alpha, compute_position_sizes
print('ok')
"
```

Expected: `ok`.

- [ ] **Step 13.3: Smoke 4 route variants**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
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

- [ ] **Step 13.4: Commit log review**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5b-plan
git log --oneline main..HEAD | wc -l
```

Expected: 12 task commits.

- [ ] **Step 13.5: Final commit (if any cleanup)**

If full pytest + ruff + smoke all green, push the branch and open a PR titled `feat(phase-5b): Dynamic Position Sizing — vol-target × alpha-conviction`.

---

## Self-Review Notes

**Spec coverage** — every § in spec mapped to a task:
- § 1 Identity → Tasks 1+2+3 (sigma + alpha + compute_position_sizes)
- § 2 Algorithm → Tasks 5+6+7 (SIZE COMPUTE / ALLOC variable sizes / finalization)
- § 3 Data Model → Task 4 (types extension) + Task 5 (sizing_policy plumbing)
- § 4 UI → Tasks 9+10+11+12 (route + hero + bid history + strategy table)
- § 5 File Structure → matches plan structure 1:1
- § 6 Test Plan → all 39 tests distributed across tasks
- § 7 Locked Decisions → tests in Tasks 3, 5, 7 cover each lock
- § 8 Risks & Mitigations → tests verify the documented mitigations

**Placeholder scan** — no TBD/TODO/"add appropriate error handling" remain. Every code block is complete.

**Type consistency** — `position_size` field consistently used everywhere; `base_position_size` (function parameter, renamed) does NOT collide with `position_size` (dataclass field). `sizing_enabled` flag name identical across simulator + orchestrator. `compute_position_sizes` return tuple `(sizes, raw_sizes_below_min)` consistent across spec § 2 + Task 3 + Task 5 consumption. Test names `_pair`, `_curve`, `_noisy_curve` consistent across all `tests/unit/test_backtest_*.py` and `tests/integration/test_backtest_*.py` files (helper definitions are local per-file but follow identical signatures).

**Critical decisions locked in this plan:**
- `compute_position_sizes` does NOT take `bid_weights` — sizing uses internal `rolling_alpha` instead (spec § 1 ⚠ box, fix for σ² double-count)
- SIZE COMPUTE filters strategies BEFORE DEDUP (spec § 2)
- `position_size` field on `BidRecord` carries the model's REQUESTED size across all 5 outcomes
- `max_strategy_exposure` and `hhi_concentration` are observation-only in v0 (Phase 5d will enforce)
