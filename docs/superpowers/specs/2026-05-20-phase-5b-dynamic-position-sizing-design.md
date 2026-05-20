# Phase 5b — Dynamic Position Sizing Design

**Status:** Approved (brainstormed 2026-05-20, 4 user questions answered, 8 decisions locked)
**Predecessors:**
- Phase 4 Backtest Engine MVP (`2026-05-19-phase-4-backtest-engine-mvp.md`)
- Phase 5a Shared Capital Pool (`2026-05-20-phase-5a-shared-capital-pool-design.md`)

**Successors:** Phase 5c (cross-strategy sector / correlation caps), Phase 5d (contribution-adjusted Sharpe), Phase 6 (live trading), Phase 7 (strategy evolution)

---

## 1. Identity

Phase 5b makes `position_size` variable, replacing Phase 5a's fixed `$1000` per signal. The model is **Hybrid: vol-target × conviction multiplier** — each position is first risk-normalized via inverse-volatility scaling, then scaled by the strategy's bid weight relative to the pool mean.

```
size_i = clamp(
    base × (target_vol / σ_i) × (bid_weight_i / mean_bid_weight),
    min = min_position_size,
    max = max_position_size,
)
```

Default constants (locked in § 7):
- `base = $1,000` (matches Phase 5a fixed default → neutral strategy gets same as before)
- `target_vol = 1.0% daily` (~16% annualized; moderate)
- `min_position = $200` (below this, the position is *skipped*, not floored — to avoid clamping noise into the portfolio)
- `max_position = $4,000` (40% of pool — concentration cap)

`σ_i` = rolling 60d std of strategy `i`'s daily-return diffs on its Phase 4 isolated equity curve. Same lookback + n<5 None-gate as Phase 5a's `rolling_sharpe`. Cold-start fallback: when σ is None or zero, `vol_scale = 1.0` — sizing reduces to pure conviction multiplier. This mirrors Phase 5a's equal-weight bootstrap philosophy.

`bid_weight_i` comes directly from Phase 5a's `compute_bid_weights`. `mean_bid_weight = mean(bid_weights.values())`. Both are guaranteed positive (Phase 5a floors at 0.1), so no divide-by-zero.

### What this means in practice

Three example outcomes assuming `mean_bid_weight = 1.2`:

| Scenario | bid_weight | σ | vol_scale | conv_scale | raw | clamped |
|---|---|---|---|---|---|---|
| Strong + low-vol | 2.0 | 0.5% | 2.0 | 1.67 | $3,333 | **$3,333** |
| Neutral | 1.2 | 1.0% | 1.0 | 1.0 | $1,000 | **$1,000** |
| Weak + high-vol | 0.1 (floor) | 2.0% | 0.5 | 0.083 | $42 | **None (size_too_small)** |
| Strong + bootstrap (σ=None) | 2.5 | None | 1.0 | 2.08 | $2,083 | **$2,083** |

The fourth row is critical: during the cold-start period (first 60 days when no strategy has 5+ mature outcomes), `vol_scale` is uniformly 1.0 across all strategies, so position size becomes a pure function of `bid_weight`. This intentionally simplifies the model during the period when statistical estimates are unreliable.

### Out of scope (explicit deferrals)

- **Per-strategy sizing override** — each strategy declaring its own model in YAML (e.g., momentum uses Kelly, mean-reversion uses fixed-fractional). Phase 5c-ish.
- **Per-ticker sizing variation** — different sizes for AAPL vs QUBT within the same strategy. Requires per-ticker σ, which we don't have at this data scale.
- **Kelly criterion** — `size = pool × (p × win - (1-p) × loss) / win²`. Mathematically optimal but extremely unstable with MarketPulse's current sample size (8 events repo-wide).
- **Drawdown-adjusted target_vol** — shrinking target_vol during drawdown, expanding during steady up periods.
- **A/B URL toggle** — `?sizing=off` to compare Phase 5a vs 5b side-by-side on the UI. Defer to Phase 5b.1 if data warrants.

### Why coupled with Phase 5a's bid weights

`mean_bid_weight` is the normalizer that keeps the formula scale-invariant: if every strategy has the same weight (e.g., all-1.0 during bootstrap), every `conv_scale = 1.0` and sizing collapses to pure vol-targeting. If one strategy is dominant (Sharpe = 3 vs others = 0.1), it gets up to ~10x conviction multiplier — capped by `max_position = $4000`.

The same `bid_weight` already determines (a) bid order priority within DEDUP and ALLOC; this is **deliberate reuse** — bid weight is the system's single source of truth for "how much we trust this strategy right now."

---

## 2. Core Algorithm

The Phase 5a daily loop adds ONE new step between WEIGHT and DEDUP:

```
CLOSE → BID COLLECT → WEIGHT → SIZE COMPUTE → DEDUP → ALLOC → MTM → RECORD
```

**Why SIZE COMPUTE before DEDUP:** if a strategy's sized position falls below `min_position`, ALL of its bids today must be filtered out *before* DEDUP runs. Otherwise a low-confidence strategy could win DEDUP against a higher-confidence one only to fail SIZE in ALLOC — wasting both the high-confidence bid and the cap-slot semantically.

### New function — `rolling_sigma` (sharpe.py)

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
      - Fewer than min_events qualifying points
      - σ computes to exactly 0 (degenerate zero-variance, e.g., flat curve)

    Causality: identical window semantics to rolling_sharpe. Outcomes at
    or after `as_of` are excluded.
    """
```

Implementation parallels `rolling_sharpe`: slice the curve to `[as_of - lookback, as_of)`, compute `np.diff(values) / values[:-1]`, return `float(np.std(daily_returns))` if finite and > 0, else None.

### New function — `compute_position_sizes` (sharpe.py)

```python
def compute_position_sizes(
    strategies_today: list[str],
    daily_curves: dict[str, list[tuple[date, float]]],
    bid_weights: dict[str, float],
    *,
    as_of: date,
    base: float = 1_000.0,
    target_vol: float = 0.01,
    min_position: float = 200.0,
    max_position: float = 4_000.0,
    lookback_days: int = 60,
    min_events: int = 5,
) -> dict[str, float | None]:
    """Compute per-strategy position size (Hybrid: vol-target × conviction).

    Algorithm (spec § 2):
      1. mean_w = mean(bid_weights.values()) — never zero (Phase 5a floors at 0.1)
      2. For each s in strategies_today:
         σ = rolling_sigma(daily_curves[s], as_of=as_of, lookback_days=...)
         vol_scale = target_vol / σ if (σ is not None and σ > 0) else 1.0
         conv_scale = bid_weights[s] / mean_w
         raw = base * vol_scale * conv_scale
         if raw < min_position:
             result[s] = None  # caller filters → outcome=size_too_small
         else:
             result[s] = min(raw, max_position)  # clamp at top, never at bottom
      3. Return dict

    Contract:
      - Every entry of strategies_today MUST appear in bid_weights AND
        daily_curves. Raises KeyError on missing.
      - Returns None for any strategy whose RAW size (pre-clamp) falls
        below min_position. Caller treats None as "skip all of this
        strategy's bids today with outcome=size_too_small".
      - max_position is a CEILING clamp (raw > max → max). min_position
        is a FLOOR DECISION (raw < min → None), not a clamp.
    """
```

**Floor vs ceiling asymmetry** is deliberate: clamping a $42 computed size up to $200 would inject sizing-floor noise into low-conviction trades the model said shouldn't fire. Better to skip them entirely.

### Updated simulator algorithm

```python
for d in trading_calendar:
    # CLOSE — unchanged
    ...

    # BID COLLECT — unchanged
    todays_bids = [b for b in events_on_day(d) if b.ticker not in in_flight_tickers]

    # WEIGHT — unchanged
    weights, floor_hits = compute_bid_weights(strategies_today, daily_curves, ...)

    # ─── SIZE COMPUTE ─── (NEW Phase 5b step)
    if sizing_enabled:
        position_sizes = compute_position_sizes(
            strategies_today, daily_curves, weights,
            as_of=d, base=base_position_size, target_vol=target_vol,
            min_position=min_position, max_position=max_position,
            lookback_days=lookback_days,
        )
        # Strategies returning None → skip all their bids today
        strategies_skipped_by_size = {s for s, sz in position_sizes.items() if sz is None}
        for b in [b for b in todays_bids if b.strategy in strategies_skipped_by_size]:
            all_bid_records.append(BidRecord(
                date=d, strategy=b.strategy, ticker=b.ticker,
                weight=weights[b.strategy], outcome="size_too_small",
                winner=None, position_size=0.0,
            ))
            n_size_too_small_by_strategy[b.strategy] = ... + 1
            n_bids_by_strategy[b.strategy] = ... + 1
        todays_bids = [b for b in todays_bids if b.strategy not in strategies_skipped_by_size]
    else:
        # Phase 5a behavior — every active strategy gets base_position_size
        position_sizes = {s: base_position_size for s in strategies_today}

    # DEDUP — unchanged (3-key tiebreaker)
    ...

    # ALLOCATE — uses position_sizes[b.strategy] instead of fixed position_size
    for b in sorted_winners:
        size_for_this_bid = position_sizes[b.strategy]
        if capital_in_use + size_for_this_bid > max_capital_in_use:
            outcome = "cap_full"  # logged with actual requested size
            ...
        elif cash < size_for_this_bid:
            outcome = "cash_short"
            ...
        else:
            # open position at size_for_this_bid
            ...

    # MTM, RECORD — unchanged
    ...
```

### Spec-locked invariants

1. **Order strict:** `CLOSE → BID → WEIGHT → SIZE → DEDUP → ALLOC → MTM → RECORD`. Test enforces.
2. **Size before dedup:** `size_too_small` strategies filtered BEFORE DEDUP considers them. Test enforces.
3. **Floor decision vs ceiling clamp:** below-min → None (skip), above-max → clamp. Test enforces both directions.
4. **Bootstrap fallback:** `σ = None` (n<5) and `σ = 0` both yield `vol_scale = 1.0`. No divide-by-zero anywhere.

### Edge cases (explicit)

- **All strategies' computed sizes are None** → empty `todays_bids` after filter → DEDUP/ALLOC have nothing to do → equity recorded as MTM-only. Same as Phase 5a's no-signal day.
- **mean_bid_weight = 0** → impossible because Phase 5a floors all weights at 0.1.
- **bid_weight is exactly 0.1 (floor hit) AND σ is huge** → conv_scale tiny AND vol_scale tiny → raw < min → None → skipped. Strategy that is BOTH unconvincing AND noisy gets blocked. Intentional.
- **σ is huge but conviction is huge too** → vol_scale × conv_scale could land anywhere; clamp at max handles the top.
- **`sizing_enabled=False`** → Phase 5a behavior restored. Used by orchestrator for regression testing and potentially A/B comparison in future UI work.

---

## 3. Data Model

### `BidRecord` — extends with `position_size` + new outcome

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
        "size_too_small",  # NEW in Phase 5b
    ]
    winner: str | None
    position_size: float  # NEW — actual $ requested. 0.0 for size_too_small (no real size)
```

**Migration:** `position_size` is required (no default). Existing Phase 5a tests that constructed `BidRecord` directly need a `position_size=1000.0` kwarg added. This is intentional — making the field optional would let Phase 5b code silently report 0.0 for forgotten sets.

### `StrategyContribution` — extends with size telemetry

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
    n_size_too_small_skipped: int  # NEW
    contribution_pnl: float
    avg_exposure: float
    avg_bid_weight: float
    avg_position_size: float       # NEW — avg $ of won bids (0.0 if n_trades == 0)
    n_bids: int
    n_floor_hits: int
```

### `PortfolioBacktestResult` — adds sizing_policy provenance

```python
@dataclass(frozen=True)
class PortfolioBacktestResult:
    # ... all existing required fields unchanged ...

    # Defaulted provenance (always-default in v0)
    display_name: str = "Shared Pool"
    mtm_model: str = "linear_interpolation_v0"
    bid_policy: str = "rolling_sharpe_60d_v0"
    sizing_policy: str = "fixed_v0"  # NEW
```

**Why default `"fixed_v0"` not None:** Phase 5a runs implicitly used a sizing model (fixed $1000). The new field makes that retroactively explicit. Phase 5b runs override to `"vol_target_conviction_v0"`. Future Phase 5b.1 (e.g., changed defaults) would bump to `"vol_target_conviction_v1"`.

### `simulate_shared_pool` — signature additions

```python
def simulate_shared_pool(
    bids: list,
    daily_curves: dict[str, list[tuple[date, float]]],
    *,
    horizon: int,
    initial_capital: float = 10_000.0,

    # Renamed: position_size → base_position_size
    base_position_size: float = 1_000.0,

    # NEW Phase 5b knobs
    target_vol: float = 0.01,
    min_position: float = 200.0,
    max_position: float = 4_000.0,
    sizing_enabled: bool = True,

    max_capital_in_use: float = 10_000.0,
    lookback_days: int = 60,
) -> PortfolioBacktestResult:
```

The `position_size` → `base_position_size` rename is a **breaking API change** to the existing function. Phase 5a tests passing `position_size=1_000.0` need updating to `base_position_size=1_000.0`. Worth it because the name is semantically wrong post-5b (it's no longer THE size, it's the BASE).

### Orchestrator — `run_shared_pool_backtest`

```python
def run_shared_pool_backtest(
    db,
    *,
    horizon: int = 5,
    since: date | None = None,
    sizing_enabled: bool = True,  # NEW — default ON
    # ... existing knobs threaded through ...
) -> dict:
```

Returns same `{isolated, artifacts, shared}` triple. Phase 5b only adds 1 new knob (`sizing_enabled`).

### Route — `/lab/backtest`

No new URL params in v0. Shared-pool mode automatically uses dynamic sizing. Future `?sizing=off` for A/B is deferred.

---

## 4. UI Surfacing

### Bid history table — adds `Size` column

Existing 5 columns: 日期 / 策略 / Ticker / 权重 / 结果.
New: 6 columns with `Size` inserted before 结果:

```
日期        策略          Ticker  权重    Size      结果
5/19       通用分析        GOOGL   0.85    $1,420    ✓ won
5/19       动量突破        GOOGL   0.71    $850      → 通用分析 (dedup)
5/18       价值分析        AAPL    1.20    —         ✗ size too small
5/18       事件驱动        QUBT    0.95    $3,200    ✗ cap full
```

- Size format: `$:,.0f` (no decimals — $1,420 not $1,420.00)
- `size_too_small` outcome → renders `—` (no real size)
- Outcome chip `size too small` styled `mp-chip mp-chip--down` (red-tinted, matches `cap full` / `cash short`)

### Strategy contribution table — adds `Avg Size` column

Existing 7 cols. New: 8 cols with `Avg Size` at end:

```
策略          n_trades  n_dedup  n_skipped  PnL ($)   Avg Exposure  Avg Bid W  Avg Size
通用分析        12        2        3          +$245.30  18.2%         1.45       $1,650
动量突破        4         3        1          -$78.20   8.5%          0.85       $920
价值分析        0         0        5          $0.00     0.0%          0.20       sub-min
```

- `Avg Size` = mean of `position_size` over won bids; `sub-min` if 0 won bids and any `size_too_small` skips (informational tag, not a real value)
- `n_skipped` now includes `n_size_too_small_skipped` in the sum (broader interpretation of "couldn't trade")

### Hero text — Phase 5b adds a 2nd sentence (shared-pool only)

Current shared-pool hero (Phase 5a):
> 6 个策略共享单一 \$10k 资本池,通过 {{ lookback_days }}-day 滚动 Sharpe 加权竞标分配。撞 ticker 时高 Sharpe 策略赢。**bid_policy=rolling_sharpe_60d_v0**。

Phase 5b appends:
> 仓位大小动态:vol-target 1.0% daily × conviction multiplier,floor \$200 / ceiling \$4,000。**sizing_policy=vol_target_conviction_v0**。

When `sizing_enabled=False` (regression mode), only the Phase 5a sentence renders + provenance line says `sizing_policy=fixed_v0`.

### KPI strip — no changes

The 5 Phase 5a cards stay (Pool Sharpe / Pool Cum Ret / Pool MaxDD / vs SPY / N dedup). Adding a 6th "Avg Position $" was considered and rejected — that's strategy-table-level granularity, not pool-summary.

### Size distribution SVG sparkline

In the bid history card header, an inline 120×24px SVG histogram showing the distribution of position sizes across the rendered (last-100) bids:

```
近 100 次 bid 决策                  [▁▂▅█▆▃▂] $200 → $4k · n=47
诊断用 · 最新在上
```

7 bins log-spaced over `[min_position, max_position]`. Excluded: `size_too_small` bids (no real size). Backend pre-computes bin heights normalized 0-1 → passed as `size_distribution: list[float]` (length 7) in template context.

Implementation in `backtest_bid_history.html`:

```jinja
<svg class="mp-bid-size-spark" viewBox="0 0 120 24" width="120" height="24">
  {% for h in size_distribution %}
    <rect x="{{ loop.index0 * 17 }}" y="{{ 24 - h * 24 }}"
          width="15" height="{{ h * 24 }}"
          fill="var(--ns-on-surface-variant)" opacity="0.7"/>
  {% endfor %}
</svg>
<span class="mp-bid-size-spark__legend">
  ${{ "{:.0f}".format(min_position) }} → ${{ "{:,.0f}".format(max_position) }}
  · n={{ shared_result.bid_history|length }}
</span>
```

Helps spot pathological clustering (all bids at max → vol-scaling is broken; all bids at min → conviction is too uniform).

---

## 5. File Structure

```
marketpulse/backtest/
├── sharpe.py                              MODIFY: + rolling_sigma, + compute_position_sizes
├── portfolio_simulator.py                 MODIFY: + SIZE COMPUTE step; BidRecord uses per-bid size;
│                                                  base_position_size renamed; sizing_enabled flag
├── types.py                               MODIFY: BidRecord.position_size + size_too_small literal;
│                                                  StrategyContribution.n_size_too_small_skipped +
│                                                  avg_position_size;
│                                                  PortfolioBacktestResult.sizing_policy
├── simulator.py                           MODIFY: run_shared_pool_backtest threads new knobs
└── __init__.py                            (unchanged — public API stable)

marketpulse/web/
├── routes/backtest.py                     MODIFY: compute size_distribution, pass via context
└── templates/partials/
    ├── backtest_hero.html                 MODIFY: append sizing_policy line in shared-pool
    ├── backtest_bid_history.html          MODIFY: + Size column, + SVG sparkline
    └── backtest_strategy_table_shared.html MODIFY: + Avg Size column

tests/
├── unit/
│   ├── test_backtest_sharpe.py            MODIFY: + 8 rolling_sigma tests,
│   │                                              + 10 compute_position_sizes tests
│   └── test_backtest_portfolio_simulator.py MODIFY: + 8 sizing integration tests;
│                                                    UPDATE: existing tests passing position_size= kwarg
├── integration/
│   └── test_backtest_shared_pool.py       MODIFY: + 3 orchestrator-level sizing tests
└── web/
    └── test_lab_backtest_modes.py         MODIFY: + 2 UI assertions
```

**No new files. No new dependencies. No DB migration.** Pure extension of Phase 5a infrastructure.

---

## 6. Test Plan

Approximately 31 new tests + ~5 Phase 5a test updates for the `position_size` BidRecord kwarg and `base_position_size` rename.

### `rolling_sigma` (8 tests)

```
test_rolling_sigma_returns_positive_for_volatile_curve
test_rolling_sigma_returns_none_below_min_events
test_rolling_sigma_uses_60d_window_by_default
test_rolling_sigma_excludes_dates_at_or_after_as_of
test_rolling_sigma_returns_none_when_variance_is_zero
test_rolling_sigma_empty_curve_returns_none
test_rolling_sigma_matches_numpy_std_within_tolerance
test_rolling_sigma_pairs_with_rolling_sharpe_consistent_window
```

### `compute_position_sizes` (10 tests)

```
test_size_high_conviction_low_vol_yields_max
test_size_low_conviction_high_vol_yields_none_below_min
test_size_neutral_strategy_yields_base
test_size_below_min_returns_none_not_clamped_up
test_size_above_max_clamps_to_max
test_size_sigma_none_uses_target_vol_fallback
test_size_zero_sigma_uses_target_vol_fallback
test_size_negative_bid_weight_via_floor_still_normalizes
test_size_all_strategies_below_min_returns_all_none
test_compute_position_sizes_raises_on_missing_bid_weight
```

### Simulator integration (8 tests)

```
test_shared_pool_sizing_skips_below_min_with_outcome
test_shared_pool_sizing_filters_before_dedup
test_shared_pool_sizing_caps_at_max_when_clamped
test_shared_pool_sizing_enabled_false_uses_fixed_base
test_shared_pool_sizing_high_conviction_gets_bigger_position
test_shared_pool_sizing_provenance_field_set
test_shared_pool_avg_position_size_in_contribution
test_shared_pool_n_size_too_small_in_contribution
```

### Orchestrator + UI (5 tests)

```
test_run_shared_pool_with_sizing_enabled_default_true
test_run_shared_pool_with_sizing_disabled_yields_phase5a_behavior
test_run_shared_pool_sizing_policy_provenance
test_lab_backtest_shared_mode_renders_size_column
test_lab_backtest_shared_mode_renders_size_sparkline
```

### Coverage target

≥ 90% on new code paths in `sharpe.py` and the SIZE COMPUTE block in `portfolio_simulator.py`. Existing Phase 5a coverage remains.

---

## 7. Locked Decisions

8 decisions, locked during the 2026-05-20 brainstorming session:

| # | Decision | Status |
|---|----------|--------|
| 1 | **Scope**: 5b-1 (algorithm) + 5b-2 (UI). 5b-3 per-strategy YAML override deferred. | LOCKED |
| 2 | **Sizing model**: Hybrid — `base × (target_vol/σ) × (bid_weight/mean_bid_weight)` | LOCKED |
| 3 | **σ source**: rolling 60d causal on Phase 4 isolated daily curve (same window as bid_weight) | LOCKED |
| 4 | **σ bootstrap**: σ None or 0 → `vol_scale = 1.0` (pure conviction-driven) | LOCKED |
| 5 | **Defaults**: base=\$1000 / target_vol=1.0% daily / min=\$200 / max=\$4000 | LOCKED |
| 6 | **Min vs max behavior**: min is a FLOOR DECISION (raw<min → None=skip), max is a CEILING CLAMP (raw>max → max) | LOCKED |
| 7 | **size_too_small outcome**: new BidRecord literal; SIZE filters before DEDUP | LOCKED |
| 8 | **sizing_policy provenance**: new field on PortfolioBacktestResult; default `"fixed_v0"` (Phase 5a backward-compat), Phase 5b overrides to `"vol_target_conviction_v0"` | LOCKED |

### Derived locks (not user-decided but spec-locked)

- **Daily loop order**: `CLOSE → BID → WEIGHT → SIZE → DEDUP → ALLOC → MTM → RECORD` (strict, test-enforced)
- **`position_size` field on BidRecord is required** (no default) to force explicit setting
- **`position_size` → `base_position_size` rename** on `simulate_shared_pool` and orchestrator (breaking API change, justified by semantic correctness)
- **Bid history card header** carries the 120×24 SVG sparkline

### Out of scope (explicit non-goals)

- Per-strategy sizing override in YAML
- Per-ticker sizing variation
- Kelly criterion
- Dynamic `target_vol` adjustment (drawdown-aware)
- `?sizing=off` URL param for UI A/B comparison

---

## 8. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| **Cold-start period (first 60d) loses vol-scaling** | Intentional — `vol_scale = 1.0` fallback prevents amplifying noise. Sizing collapses to pure conviction. Documented in hero text. |
| **All bids cluster at `max_position`** | SVG sparkline in bid history exposes the clustering. If observed, lower `max_position` or raise `target_vol`. Phase 5b.1 could auto-tune. |
| **All bids fall below `min_position`** | n_size_too_small_skipped tracked per strategy; the strategy_table flags `sub-min` for any strategy where 100% of bids skip. User sees the dying strategy fast. |
| **Floor vs ceiling asymmetry confuses users** | Hero copy says "floor \$200 / ceiling \$4,000" without explaining the asymmetry — the asymmetry is intentional but invisible UX. Future doc page can elaborate. |
| **Performance: extra `rolling_sigma` call per strategy per day** | Cheap — same data already in memory from `rolling_sharpe`; could share an inner helper if profiling shows it matters. v0 doesn't optimize. |
| **Phase 5a tests need updating** | ~5 tests construct `BidRecord` directly without `position_size`. Implementation plan handles in the same task that adds the field. No silent regressions. |

---

## 9. Implementation Hand-off

Per superpowers brainstorming → writing-plans flow:

1. **User reviews this spec.** Request changes inline or approve.
2. **`writing-plans` skill** invoked to produce a task-by-task TDD implementation plan (estimated ~12-14 tasks).
3. **`subagent-driven-development` skill** executes the plan (Phase 5a precedent worked well).
4. **Final code review** by the `code-reviewer` agent against this spec.

---

## Appendix A — Spec Coverage Map

Every clause in § 1 (Identity) maps to a section:

| § 1 promise | Implemented in |
|---|---|
| Hybrid sizing formula | § 2 (rolling_sigma + compute_position_sizes) |
| Vol-target × conviction multiplier | § 2 algorithm description |
| Defaults base/target_vol/min/max | § 7 decision #5 |
| σ from rolling 60d causal | § 2 rolling_sigma contract |
| Bootstrap fallback (σ unavailable) | § 7 decision #4 |
| Floor decision vs ceiling clamp asymmetry | § 7 decision #6 |
| Phase 5a backward compat | § 3 (sizing_enabled flag) + § 7 decision #8 |
| size_too_small outcome propagation | § 3 (BidRecord literal) + § 4 (UI rendering) |
| SVG sparkline diagnostic | § 4 |
| Provenance via sizing_policy field | § 3 (PortfolioBacktestResult) |

---

**Spec author:** Claude (brainstorming session 2026-05-20, ~25 min, 4 user-locked decisions + 4 derived locks)
