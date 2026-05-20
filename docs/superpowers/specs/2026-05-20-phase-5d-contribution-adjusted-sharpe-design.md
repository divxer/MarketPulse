# Phase 5d — Contribution-Adjusted Sharpe Design

**Status:** Locked
**Author:** harvey
**Date:** 2026-05-20
**Phase position:** After Phase 5c (Sector & Correlation Caps); before Phase 6 (Live Trading)

---

## 1. Goal

Phase 5a locked `bid_weight = rolling_sharpe(isolated_curve, 60d)` as the shared-pool bid signal. That measures each strategy's **standalone** Sharpe on its Phase 4 isolated `$10k` curve — orthogonal to how the strategy contributes to the **pool's** Sharpe. Two strategies with identical isolated Sharpe but different correlation with the rest of the pool should bid differently; today they don't.

Phase 5d introduces a soft **contribution-adjusted** bid weight: the same `rolling_sharpe` value, multiplied by a clipped function of the strategy's correlation with the pool excluding itself (leave-one-out). The multiplier mildly penalizes high overlap and (modestly) rewards negative correlation. The goal is to reward diversification without replacing alpha.

### Why not the alternatives

| Candidate | Why rejected |
|---|---|
| Marginal Sharpe (Δ pool Sharpe with vs without strategy) | O(N) extra simulations per day; DEDUP ordering becomes unstable (a strategy's marginal Sharpe depends on which other strategies got allocated); time leakage if the pool window includes future data |
| Beta-adjusted Sharpe (`Sharpe / (1 + |β_SPY|)`) | Measures market exposure, not contribution. Overlaps with Phase 5c sector cap intent. Adds no new signal |
| Information Ratio (`excess_vs_pool / tracking_error`) | Rewards "different from pool", not "helpful for pool". A losing strategy that is anti-correlated has high IR but should not be amplified |
| Per-trade attribution score | Requires historical trade reconstruction; properly belongs in a Phase 7 attribution subsystem |
| `Sharpe × (1 - λρ)` with leave-one-out denominator (chosen) | Reuses Phase 5c correlation machinery, no extra simulations, mathematically interpretable, fail-safe when pool is small |

---

## 2. Locked Decisions

| # | Decision | Status |
|---|----------|--------|
| 1 | **Formula**: `adjusted_bid_weight = rolling_sharpe_60d × clip(1 − λ·ρ, 0.5, 1.2)` (asymmetric clip) | LOCKED |
| 2 | **λ default**: `0.5`. Hot-tuneable via `run_shared_pool_backtest(contribution_lambda=...)`. Not a YAML config in v0 | LOCKED |
| 3 | **Window**: 60d rolling, identical to Phase 5a `rolling_sharpe` | LOCKED |
| 4 | **Cold-start gate**: `min_overlap = 30d`. Below threshold → `multiplier = 1.0` (failsafe-open). Between 30 and 60 days, the actual overlap is used (transparent via `effective_corr_window` telemetry) | LOCKED |
| 5 | **Default**: `contribution_enabled = False`. Observation-only first — telemetry is always computed; the toggle only swaps which weight drives DEDUP/ALLOC. Mirror of the Phase 5c review lesson | LOCKED |
| 6 | **Correlation denominator**: leave-one-out via **subtraction** — `pool_minus_A_return[d] = pool_return[d] − A_contribution_return[d]`. No N-fold simulator replay | LOCKED |
| 7 | **`strategy_contribution_return[s][d]` definition**: `realized_pnl_today[s] / pool_equity[d−1]`. Per-day pool decomposition, NOT the Phase 4 isolated curve | LOCKED |
| 8 | **Effective point**: full adjusted weight throughout. DEDUP and ALLOC both read the same `weights[s]` dict, which equals `weights_adjusted` when enabled, `weights_raw` when disabled. Phase 5a regression bit-equivalent via `contribution_enabled=False` | LOCKED |
| 9 | **New BidRecord fields**: `raw_bid_weight`, `pool_corr`, `contribution_multiplier`, `adjusted_bid_weight`, `effective_corr_window`, `pool_corr_excludes_self`, `rewarded_for_negative_corr`, `would_change_rank` (8 fields, all defaulted for fixture backward-compat) | LOCKED |
| 10 | **New PortfolioBacktestResult fields**: `contribution_enabled`, `contribution_policy`, `contribution_lambda` (3 fields, all defaulted) | LOCKED |
| 11 | **New StrategyContribution fields**: `avg_pool_corr`, `n_would_change_rank` (2 fields, observation-period KPIs) | LOCKED |
| 12 | **Scope**: only `bid_weight`. Phase 5b sizing `α_scale` untouched. 5b-3 per-strategy YAML sizing override remains deferred — it is sizing, not bidding | LOCKED |
| 13 | **No new DB tables, no Alembic migration, no new dependencies** | LOCKED |

### Derived locks

- **`bid_policy` string upgrade**: `contribution_enabled=False` → `"rolling_sharpe_60d_v0"` (unchanged from 5a); `contribution_enabled=True` → `"contribution_adjusted_sharpe_60d_v0"`. The hero template's existing `<strong>bid_policy=...</strong>` placeholder reflects this automatically — no new hero paragraph is needed
- **DEDUP 3-key tiebreak**: still `(-weight, event_time, strategy_name)`. When enabled, `weight` is `adjusted_bid_weight`; when disabled, `weight` is `raw_bid_weight`. The tuple structure is unchanged — only the first key's source value swaps
- **`rewarded_for_negative_corr` precondition**: `pool_corr is not None AND pool_corr < 0 AND multiplier > 1.0`. The combination is non-trivial because clip[0.5, 1.2] caps the reward even when ρ is strongly negative
- **`would_change_rank` per-bid**: True iff the strategy's rank in `sorted(strategies_today, key=-weights_raw[s])` differs from its rank in `sorted(strategies_today, key=-weights_adjusted[s])`. Computed in the WEIGHT step, populated on every BidRecord that strategy emits today. Same value across the strategy's bids on that day (a strategy's rank is a per-day per-strategy property, not per-bid)
- **Observation-only telemetry is always recorded** regardless of `contribution_enabled`. The toggle only swaps `weights = weights_raw` vs `weights = weights_adjusted` for DEDUP/ALLOC. **All 8 BidRecord 5d fields including `would_change_rank` are populated either way** — both rankings are always computable (we always run `pool_corr_excluding_self` and `compute_adjusted_bid_weight` for every strategy in WEIGHT). `would_change_rank=True` when disabled means "if you flipped contribution_enabled to True, this bid's rank in DEDUP/ALLOC would change". This is the killer observation-mode KPI — the user audits `would_change_rank` and `n_would_change_rank` before deciding to enable

### Out of scope (explicit deferrals)

- Per-strategy YAML override of λ, clip bounds, or window — `λ` is a single global value in v0
- Marginal Sharpe / counterfactual simulation — Phase 7+
- Per-trade attribution — separate subsystem
- Beta-adjusted variant — overlaps with 5c sector cap, no new signal
- Auto-tuning λ from observation-period statistics — Phase 7 strategy evolution
- Regime-aware ρ threshold (drawdown vs steady-state) — Phase 7 regime detection
- 3-state `contribution_mode` (off / observe / enforce) — collapsed into `bool` + always-on telemetry. The `bool=False` default IS "observe" semantics

---

## 3. Architecture

```
marketpulse/backtest/
├── contribution.py                        NEW: pool_corr_excluding_self +
│                                                compute_adjusted_bid_weight + helpers
├── sharpe.py                              UNCHANGED — Phase 5a rolling_sharpe,
│                                                       Phase 5b rolling_sigma/alpha untouched
├── portfolio_simulator.py                 MODIFY: maintain daily_strategy_contribution_returns +
│                                                  daily_pool_returns; WEIGHT step
│                                                  computes adjusted; finalization adds 5d telemetry
├── types.py                               MODIFY: BidRecord + 8 fields;
│                                                  StrategyContribution + 2 fields;
│                                                  PortfolioBacktestResult + 3 fields
├── simulator.py                           MODIFY: run_shared_pool_backtest threads
│                                                  contribution_enabled + contribution_lambda
└── __init__.py                            (no change — public API stable)

marketpulse/web/
├── routes/backtest.py                     MODIFY: pass contribution_enabled +
│                                                  contribution_lambda via template context
└── templates/partials/
    ├── backtest_hero.html                 MODIFY: inline λ modifier in para 1
    ├── backtest_bid_history.html          MODIFY: tooltip + 2 inline chip icons on weight cell
    └── backtest_strategy_table_shared.html MODIFY: + 2 columns (avg pool ρ, rank Δ)

tests/
├── unit/
│   ├── test_backtest_contribution.py      NEW: ~12 tests
│   └── test_backtest_portfolio_simulator.py MODIFY: + ~8 integration tests
├── integration/
│   └── test_backtest_shared_pool.py       MODIFY: + 3 orchestrator tests
└── web/
    └── test_lab_backtest_modes.py         MODIFY: + 2 UI assertions
```

### Module isolation (`contribution.py`)

Three public pure functions:

```python
def daily_contribution_return(
    strategy_pnl_today: float,
    pool_equity_prev_day: float,
) -> float:
    """Per-day contribution return decomposition.

    Returns strategy_pnl_today / pool_equity_prev_day. Returns 0.0 when
    pool_equity_prev_day is zero or negative (avoids ZeroDivisionError;
    cold-start safe).
    """


def pool_corr_excluding_self(
    strategy_contribution_returns: list[tuple[date, float]],
    daily_pool_returns: list[tuple[date, float]],
    *,
    as_of: date,
    lookback_days: int = 60,
    min_overlap: int = 30,
) -> tuple[float | None, int]:
    """Pearson correlation between strategy's contribution returns and
    (pool_total − strategy_contribution), over [as_of − lookback, as_of).

    Returns (corr, effective_window).
      - corr = None and effective_window = overlap_count when overlap < min_overlap
      - corr = None when either series has zero variance
      - effective_window = clamp(actual_overlap, 0, lookback_days)

    Leave-one-out implementation: pool_minus_self[d] = pool_total[d] −
    strategy_contribution[d]. Caller passes both series aligned by date;
    function intersects to overlapping dates.
    """


def compute_adjusted_bid_weight(
    raw_sharpe: float | None,
    pool_corr: float | None,
    *,
    lam: float = 0.5,
    clip_min: float = 0.5,
    clip_max: float = 1.2,
) -> tuple[float | None, float, bool]:
    """Apply contribution-adjusted multiplier to a raw bid weight.

    Returns (adjusted_weight, multiplier, rewarded_for_negative_corr).

      adjusted = raw_sharpe × multiplier
      multiplier = clip(1 − lam × pool_corr, clip_min, clip_max)
                   when pool_corr is not None and raw_sharpe > 0; else 1.0
      rewarded = (pool_corr is not None) and (pool_corr < 0) and (multiplier > 1.0)

    Skips the adjustment (multiplier=1.0, adjusted=raw_sharpe) when:
      - raw_sharpe is None (Phase 5a n<5 floor)
      - raw_sharpe <= 0 (negative-Sharpe strategy; let Phase 5a floor handle it)
      - pool_corr is None (cold-start)
    """
```

Imports outside `contribution.py`:

- `portfolio_simulator.py` imports both `pool_corr_excluding_self` and `compute_adjusted_bid_weight` at module top (no in-loop imports — Phase 5c review lesson)
- Nothing else imports `contribution.py` directly

### Simulator changes (`portfolio_simulator.py`)

New per-strategy/day accumulators (alongside existing `n_*_by_strategy` dicts at the top of `simulate_shared_pool`):

```python
daily_strategy_contribution_returns: dict[str, list[tuple[date, float]]] = {}
daily_pool_returns: list[tuple[date, float]] = []
pool_corr_by_strategy: dict[str, list[float | None]] = {}
n_would_change_rank_by_strategy: dict[str, int] = {}
```

`daily_strategy_contribution_returns` is the day-level re-bin of Phase 5b's `trade_realized_pnl_by_strategy` accumulator plus MTM contribution. The Σ contribution_pnl == pool_pnl invariant established in Phase 5b T6 carries through by construction.

### Type extensions (`types.py`)

Detailed in § 4.

### Orchestrator (`simulator.py`)

`run_shared_pool_backtest` gains two new kwargs:

```python
contribution_enabled: bool = False
contribution_lambda: float = 0.5
```

Threaded through to `simulate_shared_pool` unchanged.

### Web layer

- `routes/backtest.py`: forwards `contribution_enabled` + `contribution_lambda` via template context aliases (already-established Phase 5c pattern)
- `backtest_hero.html`: inline modifier in paragraph 1, conditional on `contribution_enabled`
- `backtest_bid_history.html`: weight column gains tooltip + 2 chip icons
- `backtest_strategy_table_shared.html`: 2 new columns after existing `avg bid w`

---

## 4. Type Extensions

### `BidRecord` — 8 new defaulted fields

```python
@dataclass(frozen=True)
class BidRecord:
    # ... existing fields unchanged ...
    date: date
    strategy: str
    ticker: str
    weight: float                              # adjusted when enabled, else raw; drives DEDUP/ALLOC
    outcome: Literal[
        "won", "dedup_loser", "cap_full", "cash_short",
        "size_too_small", "sector_cap_full", "correlation_cap_full",
    ]
    winner: str | None
    position_size: float
    blocked_by_sector: str | None = None
    blocked_by_correlation_with: tuple[tuple[str, float], ...] = ()

    # NEW Phase 5d (all defaulted for backward-compat with existing fixtures)
    raw_bid_weight: float | None = None        # Phase 5a rolling_sharpe value
    pool_corr: float | None = None             # Pearson against pool_minus_self
    contribution_multiplier: float = 1.0       # clip(1 − λρ, 0.5, 1.2)
    adjusted_bid_weight: float | None = None   # raw × multiplier
    effective_corr_window: int = 0             # actual days used (≤ 60)
    pool_corr_excludes_self: bool = True       # always True for v0 — future-flag for non-LOO variants
    rewarded_for_negative_corr: bool = False   # ρ < 0 AND multiplier > 1
    would_change_rank: bool = False            # rank-delta indicator; populated only when enabled
```

### `StrategyContribution` — 2 new fields

```python
@dataclass(frozen=True)
class StrategyContribution:
    # ... existing fields ...
    n_bids: int
    n_floor_hits: int

    # NEW Phase 5d
    avg_pool_corr: float | None = None         # time-average of pool_corr across the strategy's bids
    n_would_change_rank: int = 0               # count of bids where rank changed when enabled
```

### `PortfolioBacktestResult` — 3 new defaulted fields

```python
@dataclass(frozen=True)
class PortfolioBacktestResult:
    # ... existing required fields unchanged ...
    # ... existing defaulted block (Phase 5a/5b/5c provenance) ...
    bid_policy: str = "rolling_sharpe_60d_v0"
    sizing_policy: str = "fixed_v0"
    sector_cap_policy: str = "uniform_40pct_v0"
    correlation_cap_policy: str = "neighbor_sum_rho06_40pct_v0"
    sector_caps_enabled: bool = True
    correlation_caps_enabled: bool = True
    risk_policy: str = "cap40_corr06_enforced_v0"

    # NEW Phase 5d provenance
    contribution_enabled: bool = False
    contribution_policy: str = "contribution_adjusted_sharpe_60d_v0"  # composite tag
    contribution_lambda: float = 0.5
```

### Field semantics

- **`weight`**: still the DEDUP/ALLOC driver. Equals `adjusted_bid_weight` when enabled, `raw_bid_weight` when disabled
- **`raw_bid_weight`**: ALWAYS populated. Makes A/B analysis trivial — read both columns
- **`adjusted_bid_weight`**: `float | None`. Only `None` when `raw_bid_weight is None` (Phase 5a n<5 floor — there is nothing to adjust). When `raw_sharpe ≤ 0` or `pool_corr is None`, the multiplier short-circuits to `1.0` and `adjusted_bid_weight = raw_bid_weight` (a real float, NOT None). When disabled, `adjusted_bid_weight == raw_bid_weight × 1.0` (still a real float when raw is real)
- **`pool_corr`**: `None` when cold-start (overlap < `min_overlap=30`) OR when either return series has zero variance
- **`contribution_multiplier`**: `1.0` when cold-start, when `pool_corr is None`, or when `contribution_enabled=False`. Strictly within `[clip_min, clip_max] = [0.5, 1.2]` otherwise
- **`effective_corr_window`**: actual days of overlap used. **Always returns the actual count**, even when below `min_overlap` (e.g., 12 means "we had 12 overlapping days but didn't compute corr because < 30"). Capped at `lookback_days=60`. Telemetry-only — does not affect math. Zero only when there is literally no overlap (strategy never traded in the window)
- **`pool_corr_excludes_self`**: `True` in v0. The field exists as a forward-compat flag for hypothetical future variants where the denominator changes (e.g., a full-pool option for A/B)
- **`rewarded_for_negative_corr`**: derived field, but persisted for tooltip rendering and audit. Computed at WEIGHT step, copied to every BidRecord that strategy emits today
- **`would_change_rank`**: per-strategy per-day flag (same value across all of that strategy's bids that day). Computed at WEIGHT step. `False` when `contribution_enabled=False`
- **`avg_pool_corr`**: time-average over the strategy's per-day `pool_corr` values, ignoring `None`s. `None` when the strategy never had enough overlap during the run
- **`n_would_change_rank`**: integer count of *bids* (not days) where `would_change_rank=True`. Multiple bids per day inflate the count; that is the intended semantic for "how often did adjustment matter at decision time"

### Schema stability when `contribution_enabled=False`

- `contribution_policy` and `contribution_lambda` always populated
- `contribution_enabled=False` → `bid_policy="rolling_sharpe_60d_v0"`, all BidRecord 5d telemetry still computed (so downstream dashboards can compare)
- `would_change_rank` and `n_would_change_rank` are always `False`/`0` when disabled (rank delta requires two weight orderings; we only compute one)

---

## 5. Daily Loop Integration

The Phase 5b daily loop ORDER LOCK remains: `CLOSE → BID → WEIGHT → SIZE → DEDUP → ALLOC → MTM → RECORD`. Phase 5d injects per-day strategy contribution decomposition between CLOSE and BID, and replaces the WEIGHT-step body. SIZE (5b), DEDUP, ALLOC, sector + correlation cap checks (5c), MTM, RECORD are untouched.

### Top-of-function additions

```python
# Phase 5d accumulators
daily_strategy_contribution_returns: dict[str, list[tuple[date, float]]] = {}
daily_pool_returns: list[tuple[date, float]] = []
pool_corr_by_strategy: dict[str, list[float | None]] = {}
# n_would_change_rank counted from all_bid_records at finalization (see § 5 Finalization)

# NEW Phase 5d per-day accumulators populated during CLOSE + RECORD
# (Phase 5b's trade_realized_pnl_by_strategy is run-wide-flat; Phase 5d
# needs per-day per-strategy buckets to drive the LOO subtraction.)
realized_pnl_today_by_strategy: dict[str, float] = {}   # reset each day in CLOSE
mtm_prev_by_strategy: dict[str, float] = {}             # snapshot before RECORD's MTM loop

# Provenance — preserves lookback_days in the string (Phase 5a pattern)
bid_policy = (
    f"contribution_adjusted_sharpe_{lookback_days}d_v0" if contribution_enabled
    else f"rolling_sharpe_{lookback_days}d_v0"
)
contribution_policy = f"contribution_adjusted_sharpe_{lookback_days}d_v0"
```

### Per-day CLOSE → contribution decomposition

**Required new accumulator plumbing** (Phase 5b's `trade_realized_pnl_by_strategy` is run-wide-flat; Phase 5d adds the per-day per-strategy buckets the LOO math consumes):

1. **In the CLOSE step**, initialize `realized_pnl_today_by_strategy: dict[str, float] = {}` at day start. As each position closes (horizon hit), credit its realized PnL (`size × (horizon_price − entry_price) / entry_price`) to its strategy's bucket. Existing CLOSE-step code attributes the same value to `trade_realized_pnl_by_strategy`; the new dict is a parallel per-day accumulator
2. **Just before the RECORD step's per-position MTM loop**, snapshot the current per-strategy unrealized MTM: `mtm_prev_by_strategy = {s: sum(p.position_size * (p.entry_price + ... fraction × ...) ... for p in open_positions if p.strategy == s) for s in all_known_strategies}`. After the MTM loop runs (positions' marks update), recompute `mtm_today_by_strategy` and derive `mtm_delta_today = mtm_today − mtm_prev`
3. After CLOSE + ALLOC + MTM, aggregate today's per-strategy PnL into contribution returns:

```python
pool_equity_prev = equity_curve[-1][1] if equity_curve else initial_capital

for s in all_known_strategies:
    pnl_today_s = (
        realized_pnl_today_by_strategy.get(s, 0.0)        # from CLOSE step
        + (mtm_today_by_strategy.get(s, 0.0)
           - mtm_prev_by_strategy.get(s, 0.0))             # MTM delta from RECORD step
    )
    contrib_ret = (
        pnl_today_s / pool_equity_prev if pool_equity_prev > 0.0 else 0.0
    )
    daily_strategy_contribution_returns.setdefault(s, []).append((d, contrib_ret))

pool_ret_today = sum(
    daily_strategy_contribution_returns[s][-1][1]
    for s in all_known_strategies
    if daily_strategy_contribution_returns.get(s)
)
daily_pool_returns.append((d, pool_ret_today))
```

The Σ contribution_pnl == pool_pnl invariant (Phase 5b T6 lock) is preserved by construction: each position's realized PnL is attributed exactly once (in CLOSE) and each open position's MTM delta is attributed exactly once (in RECORD). The day-level decomposition sums to the actual pool return.

### WEIGHT step replacement

```python
weights_raw, floor_hits = compute_bid_weights(
    daily_curves, as_of=d, lookback_days=lookback_days,
)

bid_weight_metadata: dict[str, BidWeightMetadata] = {}  # @dataclass(frozen=True) in contribution.py
# Use dataclasses.replace(meta, would_change_rank=True) to flip the per-day flag
# (matches surrounding @dataclass(frozen=True) BidRecord/StrategyContribution convention)

for s in strategies_today:
    raw = weights_raw[s]
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
    bid_weight_metadata[s] = BidWeightMetadata(
        raw=raw, pool_corr=pool_corr,
        multiplier=multiplier, adjusted=adjusted,
        effective_window=eff_window,
        rewarded_for_negative_corr=rewarded,
        would_change_rank=False,  # filled below if enabled
    )
    pool_corr_by_strategy.setdefault(s, []).append(pool_corr)

# Always compute both rankings so would_change_rank is meaningful telemetry
# regardless of whether contribution_enabled is True. This is the
# killer observation-mode KPI: "how often would Phase 5d have changed
# this bid's rank if enabled?"
sorted_raw = sorted(
    strategies_today,
    key=lambda s: (-(weights_raw[s] or 0.0), s),
)
sorted_adj = sorted(
    strategies_today,
    key=lambda s: (-(bid_weight_metadata[s].adjusted or 0.0), s),
)
rank_raw = {s: i for i, s in enumerate(sorted_raw)}
rank_adj = {s: i for i, s in enumerate(sorted_adj)}

for s in strategies_today:
    if rank_raw[s] != rank_adj[s]:
        # Flip the per-day flag on the metadata. n_would_change_rank
        # is counted at finalization by scanning all_bid_records,
        # which gives a per-BID count (matches § 4 field semantics).
        bid_weight_metadata[s] = dataclasses.replace(
            bid_weight_metadata[s], would_change_rank=True,
        )

# The toggle only chooses WHICH weight drives DEDUP/ALLOC.
if contribution_enabled:
    weights = {s: bid_weight_metadata[s].adjusted for s in strategies_today}
else:
    weights = weights_raw
```

The `weights` dict produced here is read unchanged by Phase 5b SIZE, Phase 5a DEDUP, and Phase 5c ALLOC + cap checks.

### BidRecord construction sites

Every BidRecord constructor inside the daily loop reads from `bid_weight_metadata[b.strategy]` and copies all 8 Phase 5d fields. Five existing sites (`won`, `dedup_loser`, `cap_full`, `cash_short`, `size_too_small`) plus two Phase 5c sites (`sector_cap_full`, `correlation_cap_full`) — 7 sites total. The metadata is per-strategy per-day (constant for all of a strategy's bids that day), so the copy is mechanical.

### Finalization

```python
# Phase 5d per-strategy aggregates — counted from all_bid_records to give
# per-BID counts (not per-day). A strategy that has 5 bids on a day where
# its rank flipped contributes 5 to the counter, not 1.
n_would_change_rank_by_strategy: dict[str, int] = {}
for b in all_bid_records:
    if b.would_change_rank:
        n_would_change_rank_by_strategy[b.strategy] = (
            n_would_change_rank_by_strategy.get(b.strategy, 0) + 1
        )

per_strategy_stats[s] = StrategyContribution(
    # ... existing fields ...
    avg_pool_corr=(
        sum(c for c in pool_corr_by_strategy[s] if c is not None)
        / max(1, sum(1 for c in pool_corr_by_strategy[s] if c is not None))
        if any(c is not None for c in pool_corr_by_strategy[s])
        else None
    ),
    n_would_change_rank=n_would_change_rank_by_strategy.get(s, 0),
)

return PortfolioBacktestResult(
    # ... existing fields ...
    contribution_enabled=contribution_enabled,
    contribution_policy=contribution_policy,
    contribution_lambda=contribution_lambda,
    bid_policy=bid_policy,  # already determined at top of function
)
```

### Invariants

- **Phase 5a regression bit-equivalence** when `contribution_enabled=False`:
  - `weights == weights_raw`
  - All BidRecord 5d telemetry populated with neutral defaults (`multiplier=1.0`, `pool_corr=None`, `would_change_rank=False`)
  - DEDUP/ALLOC sort key tuple is `(-weights_raw[s], event_time, strategy_name)` — identical to Phase 5a
- **Phase 5b sizing decoupling**: `compute_position_sizes` reads `rolling_sigma`/`rolling_alpha`, not bid_weight. Phase 5d does not touch sizing path
- **Phase 5c caps decoupling**: ALLOC cap checks read `weights[b.strategy]` (which is now potentially adjusted) but the cap math is unchanged
- **Σ contribution_pnl == pool_pnl** (Phase 5b T6 lock): the `daily_strategy_contribution_returns` accumulator is a day-level re-bin of `trade_realized_pnl_by_strategy` plus MTM increment. Equivalence by construction; covered by a new regression test

---

## 6. UI Surfacing

### Hero — inline λ modifier in paragraph 1

Phase 5c left the hero with three paragraphs (bid policy, sizing policy, cap policy). Phase 5d does **not** add a fourth paragraph; instead it modifies paragraph 1 with a short inline phrase when contribution is enabled:

```html
<p class="mp-hero__desc">
  6 个策略共享单一 $10k 资本池,通过 {{ lookback_days }}-day 滚动 Sharpe
  加权竞标分配
  {% if shared_result.contribution_enabled %}
    × <strong>1−{{ "{:.1f}".format(shared_result.contribution_lambda) }}ρ</strong> 贡献调整(clip [0.5, 1.2])
  {% endif %}
  。撞 ticker 时高 Sharpe 策略赢。
  <strong>bid_policy={{ shared_result.bid_policy }}</strong>。
</p>
```

When disabled, this paragraph is byte-identical to the pre-5d version (the conditional outputs nothing). When enabled, one extra phrase appears mid-sentence.

### Bid history — weight column tooltip + 2 chip icons

The existing "权重" column in `backtest_bid_history.html` continues to display `b.weight` (the driver). Phase 5d enhances that cell with:

1. A tooltip exposing `raw`, `ρ`, and `multiplier` (with cold-start text when ρ is None)
2. An optional `↗` chip when `rewarded_for_negative_corr=True`
3. An optional `⇅` chip when `would_change_rank=True`

```html
<td class="num mono tnum"
    title="{% if b.contribution_multiplier != 1.0 %}raw={{ '%.2f'|format(b.raw_bid_weight) }} · ρ={{ '%.2f'|format(b.pool_corr) if b.pool_corr is not none else 'n<30 cold-start' }} · ×{{ '%.2f'|format(b.contribution_multiplier) }}{% else %}raw weight (no adjustment){% endif %}">
  {{ "{:.2f}".format(b.weight) }}
  {% if b.rewarded_for_negative_corr %}<span class="mp-chip mp-chip--up" title="hedge boost (ρ<0)">↗</span>{% endif %}
  {% if b.would_change_rank %}<span class="mp-chip" title="adjusted weight 改变了排名">⇅</span>{% endif %}
</td>
```

Reuses existing `.mp-chip` and `.mp-chip--up` styles from Phase 5b/5c. No new CSS.

### Strategy table — `avg pool ρ` + `rank Δ` columns

After Phase 5c the columns are:
`策略 | n_trades | n_dedup | n_skipped | PnL ($) | avg exposure | avg bid w | avg size`

Phase 5d inserts two new columns after `avg bid w`, before `avg size`:

```html
<th class="num">avg pool ρ</th>
<th class="num">rank Δ</th>
```

```html
<td class="num mono tnum">
  {% if c.avg_pool_corr is none %}—{% else %}{{ "{:+.2f}".format(c.avg_pool_corr) }}{% endif %}
</td>
<td class="num mono tnum"
    title="bids 因 adjusted 改变排名的次数">
  {% if c.n_would_change_rank > 0 %}{{ c.n_would_change_rank }}{% else %}—{% endif %}
</td>
```

The `avg pool ρ` column makes overlap with the pool legible at a glance; the `rank Δ` column quantifies how often Phase 5d *would* have changed allocation decisions during the observation period.

### KPI strip — unchanged

The five Phase 5a KPIs (Pool Sharpe / Cum Ret / MaxDD / vs SPY / N dedup) remain. Phase 5d telemetry lives in the hero modifier, bid history tooltip, and strategy table — no new top-level KPI cards.

### No new partial files, no new CSS

Phase 5d touches three existing partials; no `backtest_*.html` is created. CSS reuses `.mp-chip`, `.mp-chip--up`, `.mono`, `.tnum`.

---

## 7. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| **Cold-start period (first ~30 trading days) ρ is undefined** | `min_overlap=30` hard gate. Below threshold: `pool_corr=None`, `multiplier=1.0`, `effective_corr_window` reflects actual count. UI tooltip says "n<30 cold-start" explicitly |
| **30–60 day partial overlap inflates noise** | Transparent via `effective_corr_window` field. UI and audit show the actual sample size on every bid. Observation-only default means user can decide when ρ stabilizes before enforcing |
| **leave-one-out subtraction floating-point error** | `pool_minus_A_returns = pool_total − strategy_A_contribution` is one subtraction per day. IEEE 754 error magnitude < 1e-12, well below Pearson's effective precision (~0.01). Regression test verifies subtraction-based corr matches an independent recomputation that excludes strategy A from a full sim re-run, within 1e-9 |
| **Single-strategy scenarios — `pool_minus_A` is all zeros** | Either return series with `std=0` returns `corr=None` per the zero-variance guard in `pool_corr_excluding_self`. Multiplier falls back to 1.0 |
| **A strategy with no trades yet — `A_contribution_return` is all zeros** | Same zero-variance guard applies. Cold-start path also triggers via overlap shortage |
| **`raw_sharpe` is None (Phase 5a n<5 floor)** | `compute_adjusted_bid_weight` short-circuits to `(None, 1.0, False)`. Phase 5a floor handling in DEDUP/ALLOC remains the source of truth for negative-Sharpe strategies |
| **`raw_sharpe <= 0`** | Multiplier defaults to 1.0 (we don't amplify or attenuate negative Sharpe; Phase 5a floor decides whether the bid lands) |
| **`bid_policy` string change breaks dashboards that hardcode `"rolling_sharpe_60d_v0"`** | Phase 5b/5c already established multi-string provenance (`sizing_policy`, `sector_cap_policy`, `risk_policy`). A hardcoded equality check on `bid_policy` would already be a maintenance bug. Dashboards should parse the policy string or read the boolean toggle |
| **Phase 5a 3-key tiebreak first key swaps source** | DEDUP/ALLOC sort key tuple structure is unchanged. The first key's *value* swaps based on `contribution_enabled`. Documented in § 2 derived locks. New regression test confirms DEDUP winner can flip between modes |
| **`would_change_rank` ambiguity** | Defined as: a strategy's rank in `sorted(strategies_today, key=-weights_raw[s])` differs from its rank in `sorted(strategies_today, key=-weights_adjusted[s])`. Same value for all bids by the strategy that day. Test coverage includes a 3-strategy pool where ranks (0,1,2) → (2,0,1) and asserts all three bid records carry the flag |
| **`avg_pool_corr` denominator zero when all ρ are None** | Returns `None`. UI displays `—`. Tested |
| **Observation-period analysis says "enable" but enabling regresses live performance** | Inherent to observation→enforcement transitions. Mitigation: A/B by running two backtests with `contribution_enabled=True/False` and comparing aggregate KPIs. Lab UI does NOT include a built-in A/B card; users do this comparison externally |
| **Self-bias if `pool_corr` included A** | Avoided by design (leave-one-out). The `pool_corr_excludes_self=True` flag is persisted as a forward-compat marker — if future versions add a "full pool" variant, the flag distinguishes data sources |
| **Historical self-bias** (LOO ≠ counterfactual A-less pool) | LOO subtraction `pool_minus_A_return[d] = pool_total[d] − A_contribution[d]` is exact for the **day-level decomposition** but NOT identical to "what the pool would have realized without A". Without A competing for capacity, ALLOC outcomes for B/C/D on day d would differ. The ρ measured by Phase 5d is therefore "co-movement of A's daily contribution with the rest of the pool's actual realized contribution", NOT "ρ vs counterfactual A-less pool". This is signal degradation, not a correctness bug — the metric is still useful and interpretable, just not the strict marginal-contribution measure. Documented in Appendix A |
| **Pool equity negative (future leverage)** | Phase 4 forbids negative pool equity (long-only $10k pool). If future leverage allows it, `pool_equity_prev_day < 0` → `contrib_ret` sign flips. The current guard returns 0.0 only when `pool_equity_prev_day == 0`. Forward-compat: when leverage lands, decide whether ρ semantics still apply with negative equity (probably renormalize to abs(equity)) |
| **`contribution.py` module-level state** | None. All three functions are pure. No process-level cache like Phase 5c's `_SECTOR_CACHE`. No `_reset_caches_for_testing` helper needed. Verified in module isolation tests |
| **Asymmetric clip `[0.5, 1.2]` is a deliberate risk-aversion bias** | Max penalty −50%, max reward +20%. Intentional: "diversification incentive" not "optimal Sharpe composition". If a future iteration wants neutral clip `[0.5, 1.5]`, document the change in `contribution_policy` provenance string version (`_v1`). v0 ships the conservative form because hedge boosts on small samples are riskier than overlap penalties |
| **`λ=0.5` operates on raw correlation scale** | Not z-scored, not rank-transformed. Every 0.1 ρ → 5% weight change. With typical ρ in `[-0.3, +0.7]` for the current 6-strategy universe, the system spends most time in the penalty regime (`ρ > 0` → multiplier < 1). This is intentional and matches the "diversification penalty first, hedge reward second" intent. Cross-sectional ρ normalization is a Phase 5e candidate (see Appendix B) |
| **`would_change_rank` is decision sensitivity, NOT performance** | A high `n_would_change_rank` count means "the engine would pick differently if enabled", NOT "performance would improve". Dashboards must label this column carefully — "rank Δ" or "decision change count", never "Phase 5d benefit count". Misinterpretation risk is real and addressed via deliberate UI labeling (§ 6) |
| **Indicator-game risk** — auto-tuning λ on observation-period `n_would_change_rank` could over-fit | Phase 5d does NOT auto-tune. λ is a fixed kwarg. Any future auto-tuning (Phase 7 territory) must use a held-out metric like out-of-sample pool Sharpe, never the rank-change count itself |

### Migration & Reproducibility

```python
# Bit-equivalent to pre-Phase-5d behavior
result = run_shared_pool_backtest(
    db, horizon=5,
    contribution_enabled=False,  # default
)
# bid_policy = "rolling_sharpe_60d_v0"
# All BidRecord 5d fields populated with neutral defaults

# Phase 5d enabled
result = run_shared_pool_backtest(
    db, horizon=5,
    contribution_enabled=True,
    contribution_lambda=0.5,  # override for A/B
)
# bid_policy = "contribution_adjusted_sharpe_60d_v0"
```

The `contribution_policy` provenance string is always populated; `contribution_enabled` is the runtime semantic switch.

### Consistency with prior phases

| Prior phase lock | Phase 5d behavior |
|---|---|
| Phase 4 per-strategy mode (isolated $10k pools) | Unaffected. Phase 5d only applies to shared-pool mode |
| Phase 5a `bid_policy="rolling_sharpe_60d_v0"` | Conditionally upgraded to `"contribution_adjusted_sharpe_60d_v0"` when enabled |
| Phase 5b `sizing_policy`, `sizing_enabled` | Unchanged. Sizing reads raw `rolling_sigma`/`rolling_alpha`, decoupled from bid_weight |
| Phase 5b `position_size` across 5 outcomes | Unchanged. Phase 5d adds 8 new BidRecord fields, does not touch `position_size` |
| Phase 5c `sector_caps`, `correlation_caps`, `risk_policy` | Unchanged. ALLOC cap checks read `weights[b.strategy]` (now potentially adjusted) but the cap math itself is untouched |
| Phase 5b daily loop ORDER lock | Preserved. Phase 5d changes all happen inside the WEIGHT step body and the per-day contribution-decomposition that runs between CLOSE and BID |
| Phase 5b Σ contribution_pnl == pool_pnl invariant | By construction. `daily_strategy_contribution_returns` is a day-level re-bin of the existing 5b trade-level accumulator |

### Backward-compat audit (Phase 5c Group B pattern)

Default `contribution_enabled=False` + f-string `bid_policy` preserves the Phase 5a string format. Existing tests in our own codebase that hardcode the policy string must continue to pass:

- `tests/unit/test_backtest_types_phase5a.py:198` — asserts `bid_policy == "rolling_sharpe_60d_v0"` on a default-constructed `PortfolioBacktestResult`. **Passes**: dataclass default `bid_policy = "rolling_sharpe_60d_v0"` is literal-assigned (not computed), and Phase 5d does not change the dataclass default
- `tests/unit/test_backtest_portfolio_simulator.py:373` — asserts `r90.bid_policy == "rolling_sharpe_90d_v0"` when `lookback_days=90`. **Passes**: the f-string at simulator runtime substitutes the actual `lookback_days` parameter; Phase 5d preserves this f-string for both branches (`f"rolling_sharpe_{lookback_days}d_v0"` and `f"contribution_adjusted_sharpe_{lookback_days}d_v0"`)

Other risks:
- Hard-coded `BidRecord(..., weight=X)` followed by `assert b.weight == X` — these still work; `weight` is set from the metadata at construction time and equals `raw_bid_weight` when disabled
- Pre-built `BidRecord` fixtures in tests that don't pass the new fields — all 8 new fields default, so kwargs are optional
- Pre-built `PortfolioBacktestResult` / `StrategyContribution` fixtures — all 5 new fields (3 + 2) default, so kwargs are optional

The plan adds one regression test verifying both hardcoded `bid_policy` assertions pass with the Phase 5d f-string substitution.

### Toggling `contribution_enabled` mid-test

`contribution.py` exposes three pure functions with no module-level state. `simulate_shared_pool` is functional (takes all knobs as kwargs, returns a result). Two sequential calls with different `contribution_enabled` values produce independent results with no leak. No fixture cleanup required.

---

## 8. Required Test Scenarios

Each scenario must land as at least one test in the plan. Pattern requirement: assert precondition first, then assert outcome. No `if X: assert Y` vacuous patterns.

### `tests/unit/test_backtest_contribution.py` (new, ~12 tests)

1. `test_daily_contribution_return_basic` — `pnl=100, equity_prev=10000 → 0.01`
2. `test_daily_contribution_return_zero_equity_prev` — `pnl=100, equity_prev=0 → 0.0` (no ZeroDivisionError)
3. `test_pool_corr_excluding_self_perfectly_correlated` — A_returns identical to (pool − A) returns → ρ ≈ 1.0
4. `test_pool_corr_excluding_self_anti_correlated` — A_returns = −(pool − A) returns → ρ ≈ −1.0
5. `test_pool_corr_excluding_self_cold_start` — fewer than 30 overlapping days → `(None, overlap_count)`
6. `test_pool_corr_excluding_self_partial_overlap` — 45 overlapping days → ρ computed on 45-day window, `effective_window=45`
7. `test_pool_corr_excluding_self_zero_variance_strategy` — A_returns all zero → `(None, overlap)`
8. `test_pool_corr_excluding_self_zero_variance_pool_minus_self` — pool_minus_A all zero → `(None, overlap)`
9. `test_compute_adjusted_bid_weight_negative_corr_rewarded` — `raw=1.0, ρ=−0.8, λ=0.5` → `adjusted=1.2 (clipped), multiplier=1.2, rewarded=True`
10. `test_compute_adjusted_bid_weight_positive_corr_penalized` — `raw=1.0, ρ=0.8, λ=0.5` → `adjusted=0.6, multiplier=0.6, rewarded=False`
11. `test_compute_adjusted_bid_weight_extreme_clip_max` — `raw=1.0, ρ=−1.0, λ=1.0` → `multiplier=1.2` (capped, NOT 2.0)
12. `test_compute_adjusted_bid_weight_negative_sharpe_unchanged` — `raw=−0.5, ρ=0.5` → `(−0.5, 1.0, False)` (no adjustment to losers)
13. `test_compute_adjusted_bid_weight_none_sharpe_unchanged` — `raw=None, ρ=0.5` → `(None, 1.0, False)`
14. `test_compute_adjusted_bid_weight_zero_sharpe_boundary` — `raw=0.0, ρ=−0.5` → `(0.0, 1.0, False)`. Zero is not "positive"; treat same as negative — no adjustment
15. `test_pool_corr_excluding_self_empty_intersection` — `as_of` before any strategy contribution exists → `(None, 0)`. Pure cold-start with zero overlap
16. `test_pool_corr_excluding_self_returns_actual_count_below_threshold` — 15 overlap days, `min_overlap=30` → `(None, 15)` not `(None, 0)`. The actual count is informative telemetry

### `tests/unit/test_backtest_portfolio_simulator.py` (~8 new tests)

1. `test_phase5d_disabled_yields_phase5a_weights` — `contribution_enabled=False` → all BidRecord `weight == raw_bid_weight`, `would_change_rank=False`
2. `test_phase5d_enabled_changes_dedup_winner` — two strategies bid same ticker; A has higher raw_sharpe but high ρ; B has lower raw_sharpe but negative ρ. Without enabled: A wins. With enabled: B wins. Assert outcome explicitly
3. `test_phase5d_enabled_changes_alloc_order` — pool full only fits 2 of 3 winners. With enabled, the order changes so that the kept 2 are different
4. `test_phase5d_subtraction_matches_independent_recomputation` — synthetic 4-strategy pool. Compute `pool_corr_excluding_self` for one strategy via subtraction. Independently rebuild a 3-strategy pool excluding that strategy, recompute ρ on the rebuilt pool returns. Assert both ρ values within 1e-9
5. `test_phase5d_cold_start_neutral_multiplier` — pool runs for only 10 days. All BidRecords have `multiplier=1.0`, `pool_corr=None`, `effective_corr_window<=10`
6. `test_phase5d_avg_pool_corr_in_contribution` — strategy with mixed bids (some cold-start, some warm). `avg_pool_corr` is the mean of non-None values
7. `test_phase5d_would_change_rank_count_per_bid` — strategy A has 5 bid-days, on 3 of them rank flips. Each day produces exactly 1 BidRecord. Assert `n_would_change_rank == 3`. If a day produces 2 BidRecords (multiple tickers), both carry the same flag and both count, so `n_would_change_rank == 6` in that variant. Test the per-bid counting explicitly with one variant of each
8. `test_phase5d_pool_pnl_invariant_preserved` — synthetic 3-strategy pool. Assert `sum(StrategyContribution.contribution_pnl) == pool_final_equity − initial_capital` within 0.01
9. `test_phase5d_metadata_copied_to_sector_cap_full_bidrecord` — verify a `sector_cap_full` outcome BidRecord (Phase 5c site) carries all 8 Phase 5d fields populated. Easy to miss in mechanical copy
10. `test_phase5d_metadata_copied_to_correlation_cap_full_bidrecord` — same for `correlation_cap_full` site
11. `test_phase5d_would_change_rank_populated_when_disabled` — `contribution_enabled=False` but pool has run long enough for ρ to be defined. Assert at least one BidRecord has `would_change_rank=True` when raw and adjusted rankings differ. This is the killer observation-mode test
12. `test_phase5d_lookback_days_threaded_to_bid_policy` — run with `lookback_days=90, contribution_enabled=True`. Assert `result.bid_policy == "contribution_adjusted_sharpe_90d_v0"`. Mirror of the existing Phase 5a `r90` test

### `tests/integration/test_backtest_shared_pool.py` (+3)

1. `test_run_shared_pool_default_contribution_disabled` — default kwargs → `result.contribution_enabled is False`, `result.bid_policy == "rolling_sharpe_60d_v0"`
2. `test_run_shared_pool_contribution_enabled_provenance` — pass `contribution_enabled=True, contribution_lambda=0.7` → `result.contribution_enabled is True`, `result.bid_policy == "contribution_adjusted_sharpe_60d_v0"`, `result.contribution_lambda == 0.7`
3. `test_run_shared_pool_avg_pool_corr_populated` — non-trivial backtest with shared-pool mode → at least one `StrategyContribution.avg_pool_corr is not None`

### `tests/web/test_lab_backtest_modes.py` (+2)

1. `test_lab_backtest_shared_mode_contribution_off_default` — GET `/lab/backtest?mode=shared-pool` → response contains `bid_policy=rolling_sharpe_60d_v0`. No `1−0.5ρ` phrase
2. `test_lab_backtest_shared_mode_contribution_enabled_renders_inline_modifier` — toggle on via test override → response contains `1−0.5ρ` phrase and `bid_policy=contribution_adjusted_sharpe_60d_v0`

---

## Appendix A: Glossary

- **Bid weight** — the per-strategy scalar that drives DEDUP tiebreaking and ALLOC greedy ordering. Phase 5a sourced it from `rolling_sharpe`; Phase 5d optionally multiplies it by a contribution-adjustment factor
- **Raw bid weight** — `rolling_sharpe_60d` value, unchanged from Phase 5a. Always populated in BidRecord
- **Adjusted bid weight** — `raw_bid_weight × multiplier`. When `contribution_enabled=True`, this is the value `weights[s]` takes
- **Multiplier** — `clip(1 − λ × ρ, 0.5, 1.2)`. Strictly within `[0.5, 1.2]` when ρ is defined; `1.0` otherwise (cold-start, zero variance, raw_sharpe None, etc.)
- **Leave-one-out (LOO)** — when computing strategy A's correlation with the pool, exclude A's own contribution from the pool denominator. Implementation in v0: `pool_minus_A_return[d] = pool_total_return[d] − A_contribution_return[d]`. **Important distinction**: this is NOT identical to "what the pool would have returned if A had never traded" (a counterfactual A-less pool). Without A's competition for capacity, ALLOC outcomes for other strategies on day d would have differed (B might have won the slot A took, with a different position size). The LOO subtraction gives an exact decomposition of the realized pool, not a counterfactual reconstruction. The ρ value therefore measures "co-movement of A's daily contribution with the other strategies' realized contribution given that A was competing", which is a useful and interpretable signal but not the strict marginal-contribution measure. For Phase 7+ true marginal Sharpe, one would need O(N) extra sims
- **ρ semantic boundary (formal)** — **`pool_corr` measures realized co-movement under competitive allocation constraints, NOT independent return correlation.** Equivalent ways to read it: "equilibrium decomposition correlation" (not causal), "co-realization correlation under competitive allocation" (not marginal), "structural decision-sensitivity input" (not portfolio-quality predictor). This is what we have; it is not what some readers may mistake it for. Documented here so dashboards / future model authors don't over-interpret
- **`would_change_rank` semantic boundary** — measures **decision sensitivity** (would the engine pick differently?), NOT performance attribution (would the pool do better?). It is the strongest signal Phase 5d produces because it directly answers "does enabling Phase 5d change what we'd actually trade?", but it overloads three concerns: ranking sensitivity, allocation sensitivity, correlation interaction effect. Phase 5e candidate is to decompose into `rank_delta_due_to_weight`, `allocation_delta_flag`, `post_cap_delta_flag`. For v0 keep the single flag — concrete decomposition lands when prod data shows we need it
- **Contribution return** — `strategy_pnl_today / pool_equity_prev_day`. Per-day pool decomposition. Sums to `pool_return` by construction
- **Cold-start** — overlap of pool history with strategy contribution history is less than `min_overlap=30` days. Triggers `pool_corr=None, multiplier=1.0`
- **Observation-only** — `contribution_enabled=False`. All Phase 5d telemetry is computed and persisted to BidRecord and StrategyContribution; only the `weights[s]` driver is unchanged from raw. Lets the user audit `would_change_rank` and `avg_pool_corr` before flipping the switch

## Appendix B: Future evolution (not Phase 5d work)

- **Per-strategy YAML override** — λ_per_strategy, clip_per_strategy. Same deferred category as 5b-3
- **Auto-tune λ** from observation-period rank-delta statistics — Phase 7 strategy evolution. Must use out-of-sample pool Sharpe as the target metric, NEVER `n_would_change_rank` itself (that would be optimizing for the indicator, not the underlying goal)
- **Regime-aware ρ threshold** — drawdown vs steady-state — Phase 7 regime detection
- **Counterfactual marginal Sharpe** as a third weight variant — Phase 7+, requires O(N) extra sims
- **3-state mode** (`off / observe / enforce`) — collapsed in v0 to `bool + always-on telemetry`. If users want a "telemetry-off-for-perf" mode in future, add it as `bool contribution_telemetry_enabled` orthogonal to `contribution_enabled`
- **Cross-sectional ρ normalization** (Phase 5e candidate) — current `λ × ρ` uses raw correlation scale, which means the penalty/reward depends on the absolute ρ value. Two upgrades:
  - **Z-score**: `ρ̃_s = (ρ_s − μ_ρ) / σ_ρ` across all firing strategies today. Makes the multiplier scale-invariant relative to the day's correlation distribution
  - **Rank transform**: `ρ̃_s = percentile_rank(ρ_s among today's ρ values)`. Robust to outliers, monotonic, no Gaussian assumption
  Either upgrade requires the cross-sectional context (all today's ρ values before applying the multiplier). Forward-compatible: would land as a new `contribution_normalization: Literal["raw", "zscore", "rank"] = "raw"` kwarg
- **`would_change_rank` decomposition** (Phase 5e candidate) — single flag currently overloads ranking sensitivity, allocation sensitivity, and correlation interaction. Future decomposition into three orthogonal flags:
  - `rank_delta_due_to_weight: bool` — did the WEIGHT-step ranking change?
  - `allocation_delta_flag: bool` — did the bid actually land differently in ALLOC (vs being a no-op rank shuffle that didn't change winners)?
  - `post_cap_delta_flag: bool` — did sector / correlation caps redirect the allocation outcome?
- **System stability KPI** (Phase 5e candidate) — `instability_index = n_strategies_with_rank_flip_today / n_strategies_today`. Per-day aggregate of decision sensitivity. Lab UI could chart this over time to surface "how often Phase 5d is structurally active" without requiring per-bid drilling
