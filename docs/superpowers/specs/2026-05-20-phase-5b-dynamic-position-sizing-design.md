# Phase 5b — Dynamic Position Sizing Design

**Status:** Approved (brainstormed 2026-05-20, 4 user questions answered, 8 decisions locked)
**Predecessors:**
- Phase 4 Backtest Engine MVP (`2026-05-19-phase-4-backtest-engine-mvp.md`)
- Phase 5a Shared Capital Pool (`2026-05-20-phase-5a-shared-capital-pool-design.md`)

**Successors:** Phase 5c (cross-strategy sector / correlation caps), Phase 5d (contribution-adjusted Sharpe), Phase 6 (live trading), Phase 7 (strategy evolution)

---

## 1. Identity

Phase 5b makes `position_size` variable, replacing Phase 5a's fixed `$1000` per signal. The model is **Hybrid: vol-target × alpha-conviction multiplier** — each position is first risk-normalized via inverse-volatility scaling, then scaled by the strategy's **raw mean return** (alpha) relative to the pool mean.

```
size_i = clamp(
    base × (target_vol / σ_i) × (α_i / mean_α),
    min = min_position_size,
    max = max_position_size,
)
```

> **⚠ Important — why α NOT bid_weight as the conviction multiplier.**
>
> Phase 5a's `bid_weight = rolling_sharpe = μ/σ`. If sizing also used Sharpe
> as the conviction signal, the formula would collapse to:
> ```
> size ∝ (1/σ) × (μ/σ) = μ/σ²
> ```
> Low volatility would be rewarded **twice** — once in vol-targeting (1/σ),
> once embedded inside Sharpe (μ/σ). The system would systematically over-
> allocate to stable-low-vol strategies and starve momentum/breakout/event-
> driven strategies whose alpha justifies their volatility.
>
> Using `rolling_alpha = μ` (raw mean return) as the conviction signal
> breaks the chain: `size ∝ (1/σ) × μ = μ/σ` (alpha-risk-adjusted) —
> volatility appears exactly once. The bid_weight = Sharpe still drives
> bid **priority** in Phase 5a (who-trades-first when capital is scarce),
> but it does NOT drive bid **size**. Two orthogonal signals, two distinct
> purposes.

Default constants (locked in § 7):
- `base = $1,000` (matches Phase 5a fixed default → neutral strategy gets same as before)
- `target_vol = 1.0% daily` (~16% annualized; moderate)
- `min_position = $200` (below this, the position is *skipped*, not floored — to avoid clamping noise into the portfolio)
- `max_position = $4,000` (40% of pool — concentration cap)

`σ_i` and `α_i` are BOTH computed from strategy `i`'s Phase 4 isolated daily equity curve via two new functions in `sharpe.py`:
- `rolling_sigma(curve, as_of=d, lookback_days=60)` — std of daily-return diffs (same window + n<5 None-gate as Phase 5a's `rolling_sharpe`)
- `rolling_alpha(curve, as_of=d, lookback_days=60)` — mean of daily-return diffs (same window + n<5 None-gate)

When `σ_i` is None or zero, `vol_scale = 1.0`. When `α_i` is None, the strategy's `α_scale = mean_known_alpha / mean_α` (avg-fill bootstrap, matches Phase 5a's `compute_bid_weights` pattern for unknown signals). When **all** α are None across firing strategies, all `α_scale = 1.0` (full bootstrap).

`mean_α = mean(α_i for s in strategies_today if α_i is not None)`, computed over today's firing strategies only (not pool-wide all-6). If every strategy is None, `mean_α` is undefined and full-bootstrap fires.

Note: bid_weight (= Phase 5a's rolling_sharpe) appears in this spec only for **priority** semantics — it determines who-trades-first in DEDUP and ALLOC (unchanged from Phase 5a). It is **NOT** a multiplier in the size formula.

**Joint-bootstrap behavior — accurate matrix.** Two independent signals (σ and α), each with their own None-gate and bootstrap fallback:

| State | σ status | α status | vol_scale | α_scale | Effective size |
|---|---|---|---|---|---|
| **True cold-start** (very early, <5 outcomes per strategy) | All None | All None | 1.0 | 1.0 (full bootstrap) | **= base ($1000)** uniformly |
| **α-only bootstrap** (no strategy mature on alpha; some have σ already) | Some real, some None | All None | Real or 1.0 | 1.0 (full bootstrap) | base × (target_vol/σ) — pure vol-target |
| **σ-only bootstrap** (some strategies validated by alpha; σ still maturing) | All None | Mixed real + avg-fill | 1.0 | Real or avg-fill | base × (α/mean_α) — pure alpha-conviction |
| **Mixed maturity** (most realistic post-day-60) | Mixed real + None | Mixed real + None | Real or 1.0 | Real or avg-fill | Full Hybrid math |
| **Steady state** (all mature) | All real | All real | Real | Real | Full Hybrid math |

The first row is the critical edge case: during true cold-start, **every** position size = `$1000 = base`. Both bootstraps active simultaneously → uniform allocation. Only once *either* σ *or* α has real signal does sizing differentiate. This is intentional: when statistical estimates are unreliable on both axes, the model defaults to uniform allocation rather than amplifying noise.

### What this means in practice

Four example outcomes assuming `mean_α = 1.0% daily` across firing strategies:

| Scenario | α (μ) | σ | vol_scale | α_scale | raw | clamped |
|---|---|---|---|---|---|---|
| Strong + low-vol | 1.0% | 0.5% | 2.0 | 1.0 | $2,000 | **$2,000** |
| High-α + neutral-vol | 1.5% | 1.0% | 1.0 | 1.5 | $1,500 | **$1,500** |
| Weak + high-vol | 0.2% | 2.0% | 0.5 | 0.2 | $100 | **None (size_too_small)** |
| Strong + bootstrap (σ=None) | 1.5% | None | 1.0 | 1.5 | $1,500 | **$1,500** |

**Compare with the OLD double-counted formula** (using Sharpe as conviction):
- Strong+low-vol would have gotten $3,333 (1.67× neutral) — now $2,000 (only 1.33×)
- High-α+neutral-vol would have gotten $1,250 — now $1,500 (correctly rewarded for actual return)
- The new formula dampens the over-concentration in low-vol strategies. Returns alpha its rightful weight in the multiplier.

The fourth row is critical: during the cold-start period (first 60 days when no strategy has 5+ mature outcomes), `vol_scale` is uniformly 1.0 across all strategies, so position size becomes a pure function of `α / mean_α`. Strategies still differentiate based on raw return signal during cold-start; sizing reduces to a one-dimensional decision (alpha-only) rather than uniform.

### Out of scope (explicit deferrals)

- **Per-strategy sizing override** — each strategy declaring its own model in YAML (e.g., momentum uses Kelly, mean-reversion uses fixed-fractional). Phase 5c-ish.
- **Per-ticker sizing variation** — different sizes for AAPL vs QUBT within the same strategy. Requires per-ticker σ, which we don't have at this data scale.
- **Kelly criterion** — `size = pool × (p × win - (1-p) × loss) / win²`. Mathematically optimal but extremely unstable with MarketPulse's current sample size (8 events repo-wide).
- **Drawdown-adjusted target_vol** — shrinking target_vol during drawdown, expanding during steady up periods.
- **A/B URL toggle** — `?sizing=off` to compare Phase 5a vs 5b side-by-side on the UI. Defer to Phase 5b.1 if data warrants.

### Why orthogonal to Phase 5a's bid weights

Phase 5a's `bid_weight = rolling_sharpe = μ/σ` drives **bid PRIORITY** — who-gets-the-trade-first in DEDUP and the greedy ALLOC sort. Phase 5b's `α_scale = α/mean_α` drives **bid SIZE** — how-much-cap each trade consumes. Two orthogonal signals for two orthogonal concerns:

| Signal | Source | Purpose |
|---|---|---|
| `bid_weight = rolling_sharpe` | Phase 5a, unchanged | **Priority**: bid ordering in DEDUP (3-key tiebreak) and ALLOC (greedy sort) |
| `α_scale = rolling_alpha / mean_α` | Phase 5b, new | **Size**: conviction multiplier in sizing formula |
| `vol_scale = target_vol / σ` | Phase 5b, new | **Size**: risk normalizer in sizing formula |

This separation is **the key fix from review iteration 1's double-count finding** — see the ⚠ box at the top of this section. The system now has 3 independent dimensions:
1. **Priority** (Sharpe): determines who trades in scarce-capital scenarios
2. **Risk normalization** (1/σ): equalizes risk across positions
3. **Alpha attribution** (α): rewards strategies for raw return regardless of volatility

Volatility appears in exactly one place (vol_scale denominator). Sharpe appears in exactly one place (priority). The previous over-rewarding of stable-low-vol strategies is broken.

---

## 2. Core Algorithm

The Phase 5a daily loop adds ONE new step between WEIGHT and DEDUP:

```
CLOSE → BID COLLECT → WEIGHT → SIZE COMPUTE → DEDUP → ALLOC → MTM → RECORD
```

**Why SIZE COMPUTE before DEDUP:** if a strategy's sized position falls below `min_position`, ALL of its bids today must be filtered out *before* DEDUP runs. Otherwise a low-confidence strategy could win DEDUP against a higher-confidence one only to fail SIZE in ALLOC — wasting both the high-confidence bid and the cap-slot semantically.

### Two new functions in `sharpe.py`

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

    Causality: identical window semantics to rolling_sharpe.
    """


def rolling_alpha(
    daily_curve: list[tuple[date, float]],
    *,
    as_of: date,
    lookback_days: int = 60,
    min_events: int = 5,
) -> float | None:
    """Daily-return mean (alpha) over curve points in [as_of - lookback, as_of).

    Returns None when fewer than min_events qualifying points.

    Causality: identical window semantics to rolling_sigma.
    """
```

Implementations parallel `rolling_sharpe`: slice the curve, compute `np.diff(values) / values[:-1]`, then `np.std(...)` for sigma or `np.mean(...)` for alpha. Return None on insufficient samples or non-finite results.

### `compute_position_sizes` (sharpe.py)

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

    Returns (sizes, raw_sizes_below_min):
      - sizes: dict[strategy, float | None]. None means raw was below
        min_position → caller skips with outcome=size_too_small.
      - raw_sizes_below_min: dict[strategy, float] capturing the RAW
        pre-clamp computed size for each None-returning strategy. Used by
        caller to log diagnostic position_size on size_too_small BidRecords
        (so the bid history shows "model wanted $42" not just "blocked").

    Algorithm (spec § 2):
      1. For each s, compute:
         σ_s = rolling_sigma(daily_curves[s], as_of=as_of, ...)
         α_s = rolling_alpha(daily_curves[s], as_of=as_of, ...)
      2. mean_α = mean of α values over strategies where α is not None.
         If ALL α are None → use base for everyone (true cold-start).
         If SOME α are None → those strategies get α_scale = 1.0
         (avg-fill bootstrap, equivalent to using mean_known_α as
         the substitute). Others use α_s / mean_α.
      3. For each s:
         vol_scale = target_vol / σ_s if (σ_s is not None and σ_s > 0) else 1.0
         α_scale = (α_s / mean_α) if (α_s is not None and mean_α is not None) else 1.0
         raw = base * vol_scale * α_scale
         if raw < min_position:
             sizes[s] = None
             raw_sizes_below_min[s] = raw  # preserve for diagnostic log
         else:
             sizes[s] = min(raw, max_position)  # clamp at top, never at bottom
      4. Return (sizes, raw_sizes_below_min)

    NOTE — bid_weights is NOT a parameter here. Phase 5a's bid_weight
    (rolling_sharpe) drives bid PRIORITY in the simulator (DEDUP +
    ALLOC sort key). It does NOT drive position size. See § 1 ⚠ box
    for why (avoids μ/σ² double-count).

    Contract:
      - Every entry of strategies_today MUST appear in daily_curves.
        Raises KeyError on missing.
      - max_position is a CEILING clamp (raw > max → max). min_position
        is a FLOOR DECISION (raw < min → None), not a clamp.
      - raw_sizes_below_min only contains strategies where sizes[s] is None.
        Strategies whose raw exceeds min do NOT appear in raw_sizes_below_min.
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
        # compute_position_sizes uses rolling_sigma + rolling_alpha internally.
        # Does NOT take bid_weights — sizing is independent of bid priority.
        # Returns BOTH the final-size dict AND the raw pre-clamp size for
        # size_too_small strategies (for diagnostic logging).
        position_sizes, raw_sizes_below_min = compute_position_sizes(
            strategies_today, daily_curves,
            as_of=d, base=base_position_size, target_vol=target_vol,
            min_position=min_position, max_position=max_position,
            lookback_days=lookback_days,
        )
        # Strategies returning None → skip all their bids today, but log the
        # raw computed size so diagnostics show WHY the bid was below floor.
        strategies_skipped_by_size = {s for s, sz in position_sizes.items() if sz is None}
        for b in [b for b in todays_bids if b.strategy in strategies_skipped_by_size]:
            all_bid_records.append(BidRecord(
                date=d, strategy=b.strategy, ticker=b.ticker,
                weight=weights[b.strategy], outcome="size_too_small",
                winner=None,
                position_size=raw_sizes_below_min[b.strategy],  # e.g., $42
            ))
            n_size_too_small_by_strategy[b.strategy] = ... + 1
            n_bids_by_strategy[b.strategy] = ... + 1
        todays_bids = [b for b in todays_bids if b.strategy not in strategies_skipped_by_size]
    else:
        # Phase 5a behavior — every active strategy gets base_position_size
        position_sizes = {s: base_position_size for s in strategies_today}
        raw_sizes_below_min = {}

    # DEDUP — unchanged (3-key tiebreaker)
    ...

    # ALLOCATE — uses position_sizes[b.strategy] instead of fixed position_size
    # IMPORTANT — semantic change from Phase 5a:
    #   In Phase 5a, every bid consumed $1k uniformly. ALLOC was greedy by
    #   weight; cap exhaustion was bid-count-proportional.
    #   In Phase 5b, bid consumption is STRATEGY-dependent. A strategy with
    #   size=$3k blocks 3x more cap-budget than a $1k strategy per bid.
    #   Sort key (-weight, event_time, strategy) is unchanged, but the
    #   downstream cap-fill dynamics shift: high-conviction strategies
    #   can crowd out more bids than they did in Phase 5a.
    #   Example: momentum_breakout with 3 bids @ $3k each consumes $9k of
    #   the $10k pool, leaving only $1k cap for the rest of the day's bids.
    #   In Phase 5a same scenario consumed only $3k.
    for b in sorted_winners:
        size_for_this_bid = position_sizes[b.strategy]
        if capital_in_use + size_for_this_bid > max_capital_in_use:
            # cap_full: log the REQUESTED size (the model wanted this much
            # but cap was full), not 0.0 or the base.
            all_bid_records.append(BidRecord(
                ..., outcome="cap_full", position_size=size_for_this_bid,
            ))
            ...
        elif cash < size_for_this_bid:
            # cash_short: same — log requested size, not 0.0
            all_bid_records.append(BidRecord(
                ..., outcome="cash_short", position_size=size_for_this_bid,
            ))
            ...
        else:
            # open position at size_for_this_bid; BidRecord logs same value
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
    position_size: float  # NEW — model's REQUESTED size in dollars. Preserves
                          # diagnostic value across all outcomes:
                          #   won:           actual opened size (post-clamp)
                          #   dedup_loser:   what this strategy would have opened
                          #   cap_full:      what was requested but cap-blocked
                          #   cash_short:    what was requested but cash-blocked
                          #   size_too_small: raw computed size BEFORE the < min check
                          #                   (e.g., $42 — the value tells the user
                          #                   why the bid was below the floor)
                          # Never 0.0 except for accidentally-misconstructed records.
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

    # NEW Phase 5b concentration telemetry (required) —
    # observation-only in v0; Phase 5d will enforce risk budgets using these.
    max_strategy_exposure: float  # peak single-strategy exposure across all
                                  # days, expressed as fraction of pool.
                                  # E.g. 0.45 = peak day, one strategy used
                                  # 45% of the pool. Flags concentration.
    hhi_concentration: float      # Herfindahl-Hirschman Index of avg-exposures
                                  # across strategies. Σ(exposure_s²) over
                                  # strategies. Range: 1/N (perfectly even)
                                  # to 1.0 (one strategy owns all). High HHI
                                  # = winner-take-all warning sign.

    # Defaulted provenance (always-default in v0)
    display_name: str = "Shared Pool"
    mtm_model: str = "linear_interpolation_v0"
    bid_policy: str = "rolling_sharpe_60d_v0"
    sizing_policy: str = "fixed_v0"  # NEW
```

**Why both metrics:**
- `max_strategy_exposure` captures peak concentration (one bad day's worst strategy)
- `hhi_concentration` captures **distributional** concentration (is one strategy *consistently* dominating?)
- Together they give early warning before Phase 5d's risk-budget logic ships

Both are **observation-only** in Phase 5b. They are computed but not used to constrain allocation. Phase 5d will read them to enforce caps. Their values inform whether Phase 5d's defaults need adjustment.

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

7 bins **linearly spaced** over `[min_position, max_position]` — review iter 2 fix. For `[$200, $4000]` range (20× span), linear is more intuitive than log; log only adds value for spans of 100× or more. Bin edges: `$200, $743, $1286, $1829, $2371, $2914, $3457, $4000`. Excluded: `size_too_small` bids (no real size). Backend pre-computes bin heights normalized 0-1 → passed as `size_distribution: list[float]` (length 7) in template context.

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

### `compute_position_sizes` (12 tests)

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
test_compute_position_sizes_returns_raw_sizes_below_min_dict      # Review fix #2
test_compute_position_sizes_raw_only_for_none_strategies          # Review fix #2
```

### Simulator integration (13 tests)

```
test_shared_pool_sizing_skips_below_min_with_outcome
test_shared_pool_sizing_filters_before_dedup
test_shared_pool_sizing_caps_at_max_when_clamped
test_shared_pool_sizing_enabled_false_uses_fixed_base
test_shared_pool_sizing_high_alpha_gets_bigger_position             # was: high_conviction
test_shared_pool_sizing_provenance_field_set
test_shared_pool_avg_position_size_in_contribution
test_shared_pool_n_size_too_small_in_contribution
test_shared_pool_high_size_strategy_blocks_more_small_bids          # Review iter 1 fix #3
test_shared_pool_joint_bootstrap_yields_uniform_base_sizes          # Review iter 1 fix #1
test_size_formula_not_double_rewarding_low_vol                      # Review iter 2 fix #1
test_shared_pool_max_strategy_exposure_computed                     # Review iter 2 fix #3 telemetry
test_shared_pool_hhi_concentration_computed                         # Review iter 2 fix #3 telemetry
```

### Concentration regression test (1 test, new section)

```
test_single_strategy_monopolizes_pool_under_high_conviction         # Review iter 2 — locks current
                                                                     # winner-take-all behavior so
                                                                     # Phase 5d's risk-budget logic
                                                                     # can later detect when it kicks in
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
| 2 | **Sizing model**: Hybrid — `base × (target_vol/σ) × (α/mean_α)`. NOT `(bid_weight/mean_bid_weight)` — that would double-count σ via Sharpe (see § 1 ⚠ box, review iter 2 fix). | LOCKED |
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
| **Cold-start tighter than Phase 5a (review fix #1 finding)** | When σ=None AND bid_weight=0.1 (floor), raw size = $1000 × 1.0 × (0.1/1.2) = $83 → skipped via size_too_small. Phase 5a let these bids open at $1k. Phase 5b's cold-start period will see fewer trades overall — quieter warm-up. Test `test_shared_pool_joint_bootstrap_yields_uniform_base_sizes` locks the uniform-base case. |
| **ALLOC dynamics change unfaithful to Phase 5a (review fix #3 finding)** | Bids of high-conviction strategies consume more pool per bid → crowd out more downstream bids than in Phase 5a. Test `test_shared_pool_high_size_strategy_blocks_more_small_bids` locks the new behavior. Documented in § 2 ALLOCATE pseudocode as a "semantic change from Phase 5a." Acceptable — it's what dynamic sizing means. |

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

**Review iteration 1 (2026-05-20):** /review identified 3 Important + 7 Minor items. The 3 Important applied in this commit:
- **#1 Joint-bootstrap clarity** — § 1 now includes a 3-row table showing the (σ status × bid_weight status) → effective behavior matrix. Cold-start = uniform $1000 base everywhere, NOT pure-vol-targeting or pure-conviction.
- **#2 BidRecord diagnostic value** — `position_size` field semantics now preserve the model's REQUESTED size across all outcomes (won/dedup_loser/cap_full/cash_short/size_too_small). For size_too_small the value is the raw pre-clamp size (e.g. $42), enabling diagnostic "model wanted $X but floor was $Y". `compute_position_sizes` return signature changed to tuple `(sizes, raw_sizes_below_min)` to plumb the raw values.
- **#3 ALLOC dynamics change documented** — § 2 ALLOCATE pseudocode now flags the semantic shift from Phase 5a (uniform $1k consumption) to Phase 5b (variable, conviction-proportional). Test `test_shared_pool_high_size_strategy_blocks_more_small_bids` locks the new behavior. § 8 Risks adds the cold-start-tighter caveat.

Test count: 31 → 35 (+4 review-fix tests, +2 fix #2 contract tests). Spec line count: 570 → ~620. 8 locked decisions unchanged.

**Review iteration 2 (2026-05-20):** Second quant review identified 3 Important + 7 Minor items. The 3 Important applied in this commit:

- **#1 Double-count σ in formula** — `size ∝ μ/σ²` was the implied math under `(target_vol/σ) × (Sharpe/mean_Sharpe)`. Stable-low-vol strategies got rewarded twice. **Replaced**: conviction multiplier now uses `rolling_alpha / mean_α` (raw mean return) instead of `bid_weight / mean_bid_weight` (Sharpe). Now `size ∝ μ/σ` (alpha-risk-adjusted, no double-count). Phase 5a's `bid_weight = rolling_sharpe` stays — drives bid **priority** only, not bid **size**. New `rolling_alpha` function added to sharpe.py. Decision #2 wording updated. Worked-examples table in § 1 regenerated with new ratios. New test `test_size_formula_not_double_rewarding_low_vol` locks the property.

- **#2 Joint-bootstrap matrix accuracy** — original 3-row table was incomplete; cold-start can be partial (σ-bootstrapping while α has signal, or vice versa). Expanded to a 5-row matrix covering all (σ_status × α_status) combinations. True cold-start (both None) = uniform base. The α-only and σ-only bootstrap paths each correctly degrade to a single-dimension allocation rather than uniform.

- **#3 Winner-take-all telemetry** — added `max_strategy_exposure` and `hhi_concentration` to PortfolioBacktestResult. Both observation-only in v0. Phase 5d will use them for risk-budget enforcement. Phase 5b's job is to MAKE THE WARNING VISIBLE before damage compounds. New tests `test_shared_pool_max_strategy_exposure_computed` + `test_shared_pool_hhi_concentration_computed`. Plus the locking test `test_single_strategy_monopolizes_pool_under_high_conviction` documenting current behavior.

Other review-2 Minor items addressed inline:
- SVG sparkline bins now **linear** (not log) — span is only 20×, log adds visual noise
- `compute_position_sizes` no longer takes `bid_weights` parameter (was: dependency leak; now: independent signal computation via rolling_alpha inside)

Test count: 35 → 39 (+4 review-2 fix tests). Spec line count: ~635 → ~720. Locked decision #2 wording updated to reflect new formula. All 8 locked decisions otherwise unchanged.
