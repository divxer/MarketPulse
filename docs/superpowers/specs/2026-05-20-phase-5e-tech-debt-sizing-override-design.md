# Phase 5e — Tech Debt + Per-Strategy Sizing Override Design

**Status:** Brainstorm complete · ready for plan
**Author:** brainstorm 2026-05-20
**Spec-format:** locked-decision design doc (no TBDs)

---

## 1 — Goal

Tech-debt sprint that strengthens Phase 5d's foundation (refactor + tests), ships the deferred 5b-3 per-strategy YAML sizing override on the cleaner ground, AND introduces invariant-grade allocation observability that instruments the heuristic-to-optimal residual gap. Four threads, one PR:

1. **Refactor** — `portfolio_simulator.py` 853 → ~770 LOC via targeted extractions.
2. **Test hardening** — close the tautological-test gap noted across 5d code reviews.
3. **5b-3 sizing override** — optional `sizing:` block per strategy YAML, with strict validation.
4. **Allocation observability** — `effective_allocation` + `rank_drift_from_signal` on `StrategyContribution`, always-on, default-on, deterministic-invariant-grade. Instruments the three named system-evolution debts (§ 10): ordering instability, signal-execution mismatch, no unified objective.

No new features beyond the sizing override + observability metrics. **One new module** (`marketpulse/backtest/policy.py`, ~30 LOC, declarative-only — see § 2 lock #7 for rationale). No DB migration. No new dependencies.

---

## 2 — Locked decisions

1. **Sequencing**: single plan, refactor first → test hardening → sizing override → final integration.
2. **Refactor depth**: medium — extract `_phase5d_kwargs` helper to `contribution.py`, extract per-day decomposition block to `_decompose_day_contributions` helper, promote `min_overlap=30` to module constant, drop `BidRecord.pool_corr_excludes_self` field AND introduce `POOL_CORR_MODE` module constant (see lock #7).
3. **Override knobs**: all three sizing knobs (`base_position_size`, `min_position`, `max_position`) overridable per strategy YAML. `target_vol` stays global.
4. **YAML schema**: optional `sizing:` block, partial overrides allowed. Strategies without the block inherit global defaults. Existing 6 YAMLs continue working unmodified (zero migration required).
5. **Validation**: strict at load time. Loader merges overrides with globals, then validates `min ≤ base ≤ max`, all values `> 0`. ConfigError on violation, message includes strategy name + offending values.
6. **Apply scope**: overrides apply to BOTH `sizing_enabled=True` (vol-target × conviction × clip) AND `sizing_enabled=False` (fixed mode uses overridden base).
7. **`pool_corr_excludes_self` → `POOL_CORR_MODE` constant in policy layer**: the per-BidRecord field is dropped (always-True noise), AND the variant-discriminator semantic is preserved as a module-level constant. Critically, the constant lives in a NEW dedicated policy-layer module `marketpulse/backtest/policy.py` (NOT in `contribution.py`). Rationale: `contribution.py` is signal-math (data plane); `POOL_CORR_MODE` is system policy (control plane). Putting policy constants in a signal module would be architectural drift — the kind of seed that grows into "signal modules silently dictating system behavior" over multiple phases.

    Three constants colocate in `policy.py` because they share the same control-plane category:
    ```python
    # marketpulse/backtest/policy.py
    from typing import Literal

    MIN_OVERLAP_DAYS: int = 30
    POOL_CORR_MODE: Literal["LOO_ONLY_v0"] = "LOO_ONLY_v0"
    OBSERVABILITY_MODE: Literal["v1"] = "v1"
    ```

    Consumers import from `policy`, not from `contribution` or `portfolio_simulator`. This is the ONE new module Phase 5e allows itself — the "no new modules" guard from § 1 is relaxed for this single ~30-line policy file because the alternative is architectural drift accumulating across phases.
8. **UI surfacing**: bid history `size` column tooltip shows "$<size> (custom limits: $<min>/$<max>)" when the bid's strategy has overrides. No new BidRecord field; pass `strategies_with_sizing_overrides: set[str]` as separate template context.
9. **Test fixture**: one shared `phase5d_warm_pool` pytest fixture (90-day calendar, 2 anti-correlated strategies, bids every other day for 60 days). Lives in `tests/conftest.py` or dedicated fixture module. Smoke-tested itself.
10. **Strategy dataclass extension**: 3 optional fields (`base_position_size`, `min_position`, `max_position` — each `float | None = None`). Existing fields unchanged.
11. **Override map shape**: `dict[str, tuple[float | None, float | None, float | None]]` — strategy → (base, min, max). None at any position means "inherit global".
12. **Execution contract — sizing override is a post-processing clamp, NOT an alternative sizing engine.** Phrased explicitly so future readers cannot misread the override as a parallel signal pathway:
    - Global sizing (Phase 5b `compute_position_sizes`: vol-target × alpha-conviction) is the **authoritative** size producer. The per-strategy `(base, min, max)` overrides act ONLY as the **final clamp envelope** on that authoritative output.
    - Override values do NOT enter the signal layer. They do NOT affect rolling Sharpe, bid weights, pool correlation, contribution adjustment, or any pre-allocation computation.
    - The override is a **post-hoc clamp** applied after the vol-target × conviction math (or after the fixed-mode constant). Single clip operation, no second sizing engine, no double-clipping.
    - This is a hard architectural invariant. Any future change that lets overrides feed back into signal-layer computations would constitute a Phase boundary violation and requires a fresh spec.
13. **Test taxonomy — invariant tests vs behavioral tests are explicitly tagged.** Every new test in Phase 5e carries a `# Layer: invariant` or `# Layer: behavioral` comment at its docstring header (see § 6). Invariant tests assert structural properties that must hold regardless of synthetic-market dynamics (Σ contribution_returns == pool_return, no NaN, monotonic position-size clipping, override never relaxes global ceiling). Behavioral tests assert dynamics-dependent properties (rank flips occur, correlation has expected sign, avg_pool_corr is non-None given sufficient warm-up). This taxonomy prevents test drift in 5f+ where fixture dynamics evolve.

    **Taxonomy is DESCRIPTIVE, not prescriptive.** The tag is added AFTER the author has decided what failure the test is detecting; the tag describes what was already true about the test's logic. Authors must NOT shape a test to "fit" a category — e.g., weakening a behavioral assertion to fit the invariant slot, or adding a fixture-dependent precondition to fit the behavioral slot. If a test resists clean categorization, that is a SIGNAL that the test is doing two things and should be split, not a license to relax the tag rules. Code reviewers verify: "given this test's actual logic, is the tag accurate?" — not "is the tag present?"
14. **Allocation observability — default-ON, no gating flag.** The two new metrics (`effective_allocation`, `rank_drift_from_signal`) are computed at finalization on EVERY backtest run. No `observability_enabled` flag. Rationale: gating would reintroduce the same "silent missing telemetry" failure mode that the warm-pool fixture in Thread B was created to prevent. If display-level filtering is ever needed, add a UI-only / API-only switch — never a computation gate. **Stats are either present and complete, or the run did not happen.**
15. **Allocation metrics are INVARIANT-grade telemetry, not stochastic.** `effective_allocation` and `rank_drift_from_signal` are deterministic functions of (final per-strategy capital, bid sequence). Given fixed inputs, the metrics return identical outputs every run. Their tests therefore live in the invariant taxonomy (lock #13). The ONLY behavioral aspect is whether a given fixture *triggers* a non-zero drift — that fixture-shape question is isolated as a separate behavioral guard test, never conflated with the metric's correctness. This separation prevents the metric from drifting into the same ambiguity class that bit `n_would_change_rank` in Phase 5d.
16. **Allocation metrics are part of the CORE BACKTEST CONTRACT, not diagnostics.** Because lock #14 guarantees `effective_allocation` and `rank_drift_from_signal` are always populated, downstream consumers MAY assume their presence unconditionally:
    - Phase 6's optimizer (or any future allocation-tier consumer) may read these fields without a `hasattr` check, a feature-flag check, or a None-fallback.
    - The `StrategyContribution` dataclass schema treats these fields as load-bearing, on the same tier as `contribution_pnl` and `avg_exposure`.
    - Any future spec that proposes gating, removing, or conditionally populating these fields constitutes a backward-incompatible contract change requiring a fresh design doc.
    - Equivalent restatement: there is no "diagnostics mode" for Phase 5e. The metrics are not opt-in observability for engineers; they are state the system carries because the system needs them to evolve.
17. **`OBSERVABILITY_MODE` version anchor in policy layer.** Colocates with `POOL_CORR_MODE` and `MIN_OVERLAP_DAYS` in `marketpulse/backtest/policy.py` (see lock #7). Referenced once near the metric computation site as a provenance comment (e.g., `# Provenance: OBSERVABILITY_MODE == "v1" — spec § 2 lock #17`). The constant exists ONLY as a version anchor for future metric-schema evolution; NOTHING branches on it at runtime. A future v2 would bump the constant and add new fields to `StrategyContribution` without removing v1 fields (additive evolution). v0 / null state is not legal — there is no "before observability."
18. **Explicit clamp pipeline order — locked, no implicit ordering.** Phase 5e introduces a third independent clip system (`sizing override`) alongside the two from 5c (`sector_cap`, `correlation_cap`). The ORDER in which these clips compose is now an explicit architectural commitment, NOT an emergent property of code layout. Per-day-loop sequence:

    ```
    1. SIGNAL    — compute raw_sharpe, contribution multiplier, adjusted_weight, rank
                   (signal layer; NO read of sizing overrides, NO read of caps)
    2. SIZE      — compute_position_sizes:
                   a. raw_size = vol_target × alpha_conviction × eff_base
                   b. clamp to (eff_min, eff_max)   ← sizing override applied here
                   (execution-layer clamp #1: per-strategy envelope)
    3. DEDUP     — when multiple strategies bid same ticker, highest weight wins
                   (no clipping — pure selection)
    4. ALLOC     — sequential per-bid:
                   a. sector_cap check                  ← execution-layer clamp #2
                   b. correlation_cap check             ← execution-layer clamp #3
                   c. capacity (cash + max_capital)     ← execution-layer clamp #4
                   d. record outcome (won / *_full / cash_short / size_too_small)
    5. RECORD    — equity_curve, daily exposures, BidRecord with outcome
    ```

    **Invariants:**
    - Each clip operates on the output of the previous step. No clip reads forward.
    - Per-strategy override clamp (step 2b) MUST execute BEFORE pool-level caps (4a, 4b). Rationale: per-strategy envelope is a property of the strategy itself; pool-level caps are properties of the aggregate state. Swapping the order would mean a strategy's "preferred max" could be reduced by a pool cap to a value the strategy itself never intended.
    - Cap order within ALLOC (sector → correlation → capacity) is the Phase 5c lock; 5e does NOT change it.
    - Any future clip (e.g., Phase 6's pool-level VaR cap) must declare its position in this pipeline as part of its spec.

    **Failure mode this lock prevents:** "size was clamped — why?" debugging where the answer depends on undocumented evaluation order. With the explicit pipeline, the answer is always "look at which clamp fired first" with a single source of truth.

---

## 3 — Architecture

### 3.1 Four-thread execution

```
Thread A (Refactor, lowest risk)
  ├── A1: MIN_OVERLAP_DAYS constant
  ├── A2: phase5d_kwargs_from_metadata public helper
  ├── A3: Replace inline closure at 7 BidRecord sites
  ├── A4: Extract _decompose_day_contributions helper
  └── A5: Drop pool_corr_excludes_self field + add POOL_CORR_MODE constant

Thread B (Test hardening)
  ├── B6: phase5d_warm_pool fixture + self-smoke test
  ├── B7: 2 cross-validation tests (avg_pool_corr, n_would_change_rank)
  └── B8: Tighten 2 weak web assertions

Thread C (Sizing override feature)
  ├── C9:  Strategy dataclass + 3 optional fields
  ├── C10: YAML loader sizing block + strict validation
  ├── C11: 6 loader tests (no block, partial, full, 3× invalid)
  ├── C12: compute_position_sizes accepts override map
  ├── C13: 3 sharpe.py tests (full, partial, no-override regression)
  ├── C14: Orchestrator threads override map
  └── C15: bid history tooltip when strategy has overrides

Thread D (Allocation observability — default-on, invariant-grade, core contract)
  ├── D16: effective_allocation + rank_drift_from_signal fields on StrategyContribution
  ├── D17: OBSERVABILITY_MODE = "v1" constant (lock #17)
  ├── D18: Finalization populates both fields from bid_history + avg_bid_weight
  ├── D19: Strategy table UI — 2 new columns (eff. alloc, rank Δ vs signal)
  └── D20: 4 metric tests (3 invariant + 1 fixture-shape behavioral guard)

Final
  └── 21: Full suite + ruff + module-import smoke + route smoke
```

### 3.2 Refactor mechanics

**`_phase5d_kwargs` extraction (A2 + A3):**

Current state: defined as a closure inside the daily loop of `simulate_shared_pool` (lines ~346-372 of `portfolio_simulator.py`). Closure captures `bid_weight_metadata` via default-arg.

Target: pure function in `contribution.py`:

```python
def phase5d_kwargs_from_metadata(
    metadata: BidWeightMetadata | None,
    strategy: str,
) -> dict[str, object]:
    """Build the 8 Phase 5d BidRecord telemetry kwargs from metadata.

    When metadata is None, returns safe defaults matching BidRecord
    dataclass defaults (cold-start / strategy-skipped-WEIGHT case).
    """
    if metadata is None:
        return {
            "raw_bid_weight": None,
            "pool_corr": None,
            "contribution_multiplier": 1.0,
            "adjusted_bid_weight": None,
            "effective_corr_window": 0,
            "rewarded_for_negative_corr": False,
            "would_change_rank": False,
        }
    return {
        "raw_bid_weight": metadata.raw,
        "pool_corr": metadata.pool_corr,
        "contribution_multiplier": metadata.multiplier,
        "adjusted_bid_weight": metadata.adjusted,
        "effective_corr_window": metadata.effective_window,
        "rewarded_for_negative_corr": metadata.rewarded_for_negative_corr,
        "would_change_rank": metadata.would_change_rank,
    }
```

Note: removes `pool_corr_excludes_self` from both branches (A5).

Call site pattern in simulator:
```python
meta = bid_weight_metadata.get(b.strategy)
all_bid_records.append(BidRecord(
    ..., **phase5d_kwargs_from_metadata(meta, b.strategy),
))
```

**Decomposition extraction (A4):**

Current state: ~50 lines inline after MTM, before RECORD (lines ~614-637 of `portfolio_simulator.py`).

Target: private helper in `portfolio_simulator.py` (NOT in `contribution.py` — it mutates simulator-local accumulators):

```python
def _decompose_day_contributions(
    *,
    today: date,
    open_positions: list[_OpenPosition],
    realized_pnl_today_by_strategy: dict[str, float],
    mtm_prev_by_strategy: dict[str, float],
    mtm_today_by_strategy: dict[str, float],
    equity_curve: list[tuple[date, float]],
    initial_capital: float,
    daily_strategy_contribution_returns: dict[str, list[tuple[date, float]]],
    daily_pool_returns: list[tuple[date, float]],
) -> None:
    """Append per-strategy contribution returns + pool return for `today`.

    Pure side-effect helper: mutates daily_strategy_contribution_returns
    and daily_pool_returns in place. Returns None to make the mutation
    explicit at the call site.

    Invariant: Σ daily_strategy_contribution_returns[s][-1] == daily_pool_returns[-1]
    by construction (shared denominator).
    """
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
        daily_strategy_contribution_returns.setdefault(s, []).append((today, contrib_ret))
    pool_ret_today = sum(
        daily_strategy_contribution_returns[s][-1][1]
        for s in all_known_strategies
        if daily_strategy_contribution_returns.get(s)
    )
    daily_pool_returns.append((today, pool_ret_today))
```

**`POOL_CORR_MODE` constant (A5):**

The `pool_corr_excludes_self: bool = True` field on `BidRecord` is removed (always-True noise; no consumer reads it). The variant-discriminator semantic moves to a module-level `Literal` constant in `contribution.py`:

```python
# marketpulse/backtest/contribution.py
from typing import Literal

POOL_CORR_MODE: Literal["LOO_ONLY_v0"] = "LOO_ONLY_v0"
"""Discriminator for the pool-correlation computation variant.

v0 ships LOO (leave-one-out via subtraction) as the only mode.
A future non-LOO variant (e.g., counterfactual A-less simulation,
or pool-conditional correlation) would version-bump this constant
to e.g. 'LOO_OR_CF_v1' and `pool_corr_excluding_self` would dispatch
on the mode. v0 hardcodes LOO; the constant exists to anchor the
extension surface, not to be branched on yet.
"""
```

Referenced once in `portfolio_simulator.py` as a provenance assertion comment alongside the `pool_corr_excluding_self` call:

```python
# WEIGHT block in portfolio_simulator.py
# Provenance: POOL_CORR_MODE == "LOO_ONLY_v0" — spec § 2 lock #7
pool_corr, eff_window = pool_corr_excluding_self(...)
```

The constant does NOT appear on any dataclass or wire format. Future v1 introductions add a discriminator field then; v0 stays clean.

### 3.3 Sizing override mechanics

**YAML schema (optional `sizing:` block):**

```yaml
# marketpulse/strategies/sector_rotation.yaml
key: sector_rotation
display_name: 板块轮动
# ... existing fields ...
sizing:                       # optional block
  base_position_size: 500     # all 3 fields optional within the block
  min_position: 200
  max_position: 2000
```

Missing block → strategy uses globals.
Partial block (e.g., only `min_position`) → other 2 inherit globals.

**Loader validation** (`marketpulse/strategies/loader.py`):

```python
def _validate_sizing(
    name: str,
    sizing: dict | None,
    globals_: tuple[float, float, float],  # (base, min, max)
) -> tuple[float | None, float | None, float | None]:
    """Parse + validate optional sizing block; return (base, min, max) overrides
    or (None, None, None) if no block. Raises ConfigError on invalid input."""
    if sizing is None:
        return (None, None, None)
    base = sizing.get("base_position_size")
    mn = sizing.get("min_position")
    mx = sizing.get("max_position")
    for fld, val in (("base_position_size", base), ("min_position", mn), ("max_position", mx)):
        if val is not None and val <= 0:
            raise ConfigError(f"Strategy '{name}': sizing.{fld} must be > 0 (got {val})")
    # Merge with globals to validate consistency
    g_base, g_min, g_max = globals_
    eff_base = base if base is not None else g_base
    eff_min = mn if mn is not None else g_min
    eff_max = mx if mx is not None else g_max
    if not (eff_min <= eff_base <= eff_max):
        raise ConfigError(
            f"Strategy '{name}': sizing invariant violated — "
            f"need min ({eff_min}) <= base ({eff_base}) <= max ({eff_max})"
        )
    return (base, mn, mx)
```

**Strategy dataclass extension** (`marketpulse/strategies/_types.py` or equivalent):

```python
@dataclass(frozen=True)
class Strategy:
    # ... existing fields unchanged ...
    base_position_size: float | None = None  # NEW Phase 5e
    min_position: float | None = None        # NEW Phase 5e
    max_position: float | None = None        # NEW Phase 5e
```

**`compute_position_sizes` extension** (`marketpulse/backtest/sharpe.py`):

```python
def compute_position_sizes(
    strategies: list[str],
    daily_curves: dict[str, list[tuple[date, float]]],
    *,
    as_of: date,
    lookback_days: int,
    target_vol: float,
    base_position_size: float,
    min_position: float,
    max_position: float,
    per_strategy_overrides: dict[str, tuple[float | None, float | None, float | None]] | None = None,
) -> dict[str, float]:
    """For each strategy in `strategies`, returns the effective position size
    in dollars.

    Execution-contract invariant (spec § 2 lock #12):
    Per-strategy overrides are a POST-PROCESSING CLAMP applied ONLY at the
    final clip step. They do NOT enter the vol-target × alpha-conviction
    signal computation — `eff_base` parameterizes only the conviction
    baseline, NOT the signal numerator. Equivalently: the override values
    influence the CLAMP ENVELOPE for the size, never the SIGNAL that
    determines where within that envelope the size lands. This is a hard
    architectural boundary; do not loosen it without a fresh spec.
    """
    overrides = per_strategy_overrides or {}
    sizes: dict[str, float] = {}
    for s in strategies:
        ov_base, ov_min, ov_max = overrides.get(s, (None, None, None))
        eff_base = ov_base if ov_base is not None else base_position_size
        eff_min = ov_min if ov_min is not None else min_position
        eff_max = ov_max if ov_max is not None else max_position
        # ... existing vol-target × alpha-conviction × clip math, using
        # eff_base / eff_min / eff_max in place of the global args ...
```

**Orchestrator threading** (`marketpulse/backtest/simulator.py`):

```python
# In run_shared_pool_backtest, after loading strategies:
overrides = {
    s.key: (s.base_position_size, s.min_position, s.max_position)
    for s in strategies.values()
    if (s.base_position_size is not None
        or s.min_position is not None
        or s.max_position is not None)
}
shared_result = simulate_shared_pool(
    ...,  # existing kwargs
    per_strategy_overrides=overrides,
)
```

`simulate_shared_pool` accepts and forwards `per_strategy_overrides` to `compute_position_sizes`. SIZE fixed-mode also reads the override map (fallback to global `base_position_size`).

**UI tooltip** (`marketpulse/web/templates/partials/backtest_bid_history.html`):

```html
{# strategies_with_sizing_overrides is a set[str] passed from the route #}
<td class="num mono tnum" title="{% if b.strategy in strategies_with_sizing_overrides %}${{ '{:.0f}'.format(b.position_size) }} (custom limits — see strategy config){% endif %}">
  ${{ "{:.0f}".format(b.position_size) }}
</td>
```

Route surfaces `strategies_with_sizing_overrides` from the loaded strategies dict (computed alongside the override map).

### 3.4 Architectural boundary — signal vs execution

This diagram captures the spec § 2 lock #12 invariant as a layer map. Every Phase 5e change must respect this boundary:

```
┌─────────────────────────────────────────────────────────────────┐
│                       SIGNAL LAYER                              │
│  rolling Sharpe · pool_corr_excluding_self · contribution       │
│  multiplier · compute_adjusted_bid_weight · rank ordering       │
│                                                                 │
│  Inputs: market data, equity curves, daily pool returns         │
│  Outputs: weights (raw + adjusted), BidWeightMetadata           │
│                                                                 │
│  ❌ NO read of per-strategy sizing overrides                    │
│  ❌ NO read of (base, min, max) per-strategy values             │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         │  weights, metadata
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                  EXECUTION LAYER                                │
│  SIZE step (compute_position_sizes):                            │
│    1. vol-target × alpha-conviction math (signal-driven)        │
│    2. CLAMP envelope = (eff_min, eff_max) — POST-PROCESSING     │
│       └── per-strategy override is consulted HERE and ONLY HERE │
│                                                                 │
│  DEDUP · ALLOC (sector cap · correlation cap · capacity cap)    │
│                                                                 │
│  Inputs: weights, metadata, per_strategy_overrides              │
│  Outputs: BidRecords (with outcome), positions                  │
└─────────────────────────────────────────────────────────────────┘
```

**Invariant check for code review:**
- If a Phase 5e change reads `per_strategy_overrides` (or `Strategy.min_position` etc.) ABOVE the SIZE step's final clip, the change is wrong. Reject.
- If a Phase 5e change uses an override value as a signal-layer input (e.g., scaling a rolling Sharpe by a strategy-specific size factor), the change is wrong. Reject.
- The override map is read in exactly two locations: `compute_position_sizes` (clip envelope) and the route layer's `strategies_with_sizing_overrides` set (UI surfacing only).

### 3.5 Allocation observability mechanics

Phase 5e introduces TWO observation-only metrics on `StrategyContribution`, populated at finalization on every run (no gating flag — spec § 2 lock #14). They measure the gap between **signal-layer ranking** (where the strategy bid) and **execution-layer outcome** (where capital actually landed), which is the load-bearing observable for the three system-evolution debts named in § 10 (ordering instability, signal-execution mismatch, no unified objective).

**StrategyContribution extension** (`marketpulse/backtest/types.py`):

```python
@dataclass(frozen=True)
class StrategyContribution:
    # ... existing fields unchanged ...
    avg_pool_corr: float | None = None        # Phase 5d
    n_would_change_rank: int = 0              # Phase 5d

    # NEW Phase 5e Thread D — always populated, invariant-grade
    effective_allocation: float = 0.0          # share of total won capital, [0.0, 1.0]
    rank_drift_from_signal: int = 0            # rank(avg_bid_weight) − rank(effective_allocation)
```

**Effective allocation:**

$$E_s = \frac{\sum_{b \in \text{bid\_history},\, b.\text{strategy}=s,\, b.\text{outcome}=\text{won}} b.\text{position\_size}}{\sum_j \sum_{b'} b'.\text{position\_size}}$$

Implementation:
```python
total_won_capital = sum(
    b.position_size for b in all_bid_records if b.outcome == "won"
)
effective_allocation_by_strategy: dict[str, float] = {}
for s in sorted(daily_curves.keys()):
    won_size = sum(
        b.position_size for b in all_bid_records
        if b.strategy == s and b.outcome == "won"
    )
    effective_allocation_by_strategy[s] = (
        won_size / total_won_capital if total_won_capital > 0 else 0.0
    )
```

Range: `[0.0, 1.0]`. Sum across all strategies = 1.0 when any capital was allocated, else all zero.

**Rank drift from signal:**

$$D_s = \mathrm{rank}_\text{desc}(\overline{w}_s) - \mathrm{rank}_\text{desc}(E_s)$$

Where `rank_desc` is the descending-order rank (highest value → rank 0). `avg_bid_weight` is the existing Phase 5a field on `StrategyContribution` (mean of `b.weight` over all bids for the strategy).

Implementation:
```python
# Strategies with no bids get sentinel rank == len(strategies) (lowest)
strategies_sorted_by_weight = sorted(
    per_strategy_stats.keys(),
    key=lambda s: -per_strategy_stats[s].avg_bid_weight,
)
strategies_sorted_by_capital = sorted(
    per_strategy_stats.keys(),
    key=lambda s: -effective_allocation_by_strategy[s],
)
rank_by_weight = {s: i for i, s in enumerate(strategies_sorted_by_weight)}
rank_by_capital = {s: i for i, s in enumerate(strategies_sorted_by_capital)}
rank_drift_by_strategy: dict[str, int] = {
    s: rank_by_weight[s] - rank_by_capital[s]
    for s in per_strategy_stats
}
```

Range: `[−(N−1), +(N−1)]` where N is the number of strategies.
- `D_s = 0`: strategy lands at the rank its signal predicted.
- `D_s > 0`: signal said "rank higher" than capital outcome (strategy under-allocated relative to bid). Typical cause: caps fired (sector, correlation, size_too_small, cash_short).
- `D_s < 0`: capital outcome was HIGHER than signal rank predicted. Typical cause: higher-ranked strategies were blocked by caps, leaving capital for this one.

**Determinism (lock #15):** both metrics are pure functions of `all_bid_records` (the finalized BidRecord list) and `per_strategy_stats[s].avg_bid_weight`. Given identical bid history, the metrics return identical values every run. The behavioral aspect — *whether* a given fixture produces non-zero drift — is isolated as a separate fixture-shape guard test (D20.3 in § 6.4).

**UI surfacing** (`backtest_strategy_table_shared.html`):

Two new columns inserted between existing `rank Δ` (Phase 5d) and `avg size`:

```html
<th class="num">eff. alloc</th>
<th class="num" title="bid rank − capital rank: + means under-allocated vs signal">rank Δ vs signal</th>
```

Cells:
```html
<td class="num mono tnum">{{ "{:.1%}".format(c.effective_allocation) }}</td>
<td class="num mono tnum">
  {% if c.rank_drift_from_signal == 0 %}—{% else %}{{ "{:+d}".format(c.rank_drift_from_signal) }}{% endif %}
</td>
```

**No new BidRecord fields.** Both metrics are per-strategy aggregates only.

**No backward-compat issue.** Default values (`0.0`, `0`) mean a pre-Phase-5e `StrategyContribution` constructed without these fields would land in the "no drift" state — semantically correct for legacy reconstruction, though no production code constructs `StrategyContribution` outside the simulator anyway. Note that per spec § 2 lock #16, the simulator MUST populate these fields on every run; defaults exist only for fixture / test construction.

**`OBSERVABILITY_MODE` constant** (spec § 2 lock #17):

```python
# marketpulse/backtest/contribution.py (or marketpulse/backtest/observability.py)
from typing import Literal

OBSERVABILITY_MODE: Literal["v1"] = "v1"
"""Version anchor for the allocation-observability schema.

v1 = effective_allocation + rank_drift_from_signal on StrategyContribution.

Future v2 would add additive fields (e.g., per-day allocation history,
constraint-binding indicators) without removing v1 fields. There is no
v0 or null state; the metrics are part of the core backtest contract
from Phase 5e onward. Lock #16 + #17.
"""
```

Referenced once in `portfolio_simulator.py`'s finalization block as a provenance comment alongside the metric computation:

```python
# Provenance: OBSERVABILITY_MODE == "v1" — spec § 2 lock #17.
# These metrics are core contract, not diagnostics (lock #16).
effective_allocation_by_strategy = {...}
rank_drift_by_strategy = {...}
```

**Downstream contract guarantee (lock #16):** Phase 6's optimizer and any future allocation-tier consumer may read `StrategyContribution.effective_allocation` and `.rank_drift_from_signal` unconditionally — no `hasattr` checks, no feature-flag checks, no None-fallbacks. The simulator's invariant test in `test_phase5e_effective_allocation_sums_to_one_or_zero` (D20.1) enforces this contract on every commit.

---

## 4 — Data flow

```
YAML file (strategies/*.yaml)
  └── loader._validate_sizing(name, sizing_block, globals)
       └── (base|None, min|None, max|None)
            └── Strategy dataclass fields

run_shared_pool_backtest(db, ..., contribution_enabled=False)
  ├── strategies = load_strategies()
  ├── overrides = {key: (base, min, max) for s in strategies if any override}
  └── simulate_shared_pool(
        ...,
        per_strategy_overrides=overrides,
      )
       │
       └── SIZE step:
           compute_position_sizes(
             strategies_today, daily_curves,
             ...,
             per_strategy_overrides=overrides,
           )
            └── per strategy s:
                eff_base = overrides[s][0] or base_position_size
                eff_min  = overrides[s][1] or min_position
                eff_max  = overrides[s][2] or max_position
                # ... clip-and-multiply as Phase 5b ...

Route layer:
  context["strategies_with_sizing_overrides"] = {s.key for s in strategies if any override}
  └── bid_history.html: tooltip per <td> when b.strategy in that set
```

---

## 5 — Error handling

| Failure mode                                | Behavior                                                   |
|---------------------------------------------|------------------------------------------------------------|
| YAML has `sizing:` with negative value      | `ConfigError("Strategy 'X': sizing.min_position must be > 0 (got -100)")` at load |
| YAML has `sizing:` with `min > max`         | `ConfigError("Strategy 'X': sizing invariant violated — need min (5000) <= base (1000) <= max (4000)")` |
| YAML has `sizing:` with `base > max`        | Same `ConfigError` form, different values                 |
| YAML omits `sizing:` block                  | Strategy uses global defaults; no error                   |
| YAML has empty `sizing:` block (`sizing: {}`) | Treated same as omitted; all 3 inherit globals          |
| Strategy in pool with no YAML entry         | Falls through to globals (no `ConfigError` — unknown strategies use defaults) |
| Override map passed to `compute_position_sizes` for strategy with no entry | Treated as `(None, None, None)` — full inheritance |
| Refactor: pre-Phase 5d code reads `BidRecord.pool_corr_excludes_self` | None exists — field is removed, mypy/pytest would catch  |

**`ConfigError`**: standard Python `ValueError` subclass already defined in the strategies module (used by Phase 3 for YAML loader). No new exception type.

---

## 6 — Testing strategy

### 6.0 Test taxonomy (locked — spec § 2 lock #13)

Every new test added in Phase 5e MUST carry one of these tags as the first line of its docstring:

- **`# Layer: invariant`** — asserts a structural property that holds regardless of synthetic-market dynamics. Examples: `Σ contribution_returns == pool_return`, no NaN/Inf, `result >= eff_min`, override never relaxes global ceiling. These tests fail ONLY when the implementation breaks; they survive fixture rewrites.
- **`# Layer: behavioral`** — asserts a dynamics-dependent property: rank flips occur, `pool_corr` has expected sign, `avg_pool_corr` is non-None given sufficient warm-up. These tests depend on the synthetic-market fixture being well-shaped; they can become vacuous if the fixture changes.

**Why this matters:** behavioral tests that masquerade as invariant tests are the failure mode that produced the tautological-test gap in Phase 5d (warm-up didn't trigger → "pass" assertions were vacuous). By tagging explicitly, future engineers can audit: "is this test telling me about my implementation, or about my fixture?"

**Heuristic check:** if a test passes when the production code is reverted to a no-op (e.g., always return None, always return 0), it's vacuous. Invariant tests should FAIL on a no-op; behavioral tests may pass (depending on the no-op's interaction with the fixture). Author each test with this in mind.

### 6.1 Thread A (Refactor) — verified by existing 915 tests

All Thread A tests are **invariant**: they assert pure-function I/O contracts that don't depend on market dynamics.

- A1: 1 new test asserting `MIN_OVERLAP_DAYS == 30`. `# Layer: invariant`
- A2: 2 new tests for `phase5d_kwargs_from_metadata`: None branch returns safe defaults dict; Some branch returns unpacked metadata. `# Layer: invariant`
- A3: No new tests; existing 915 must pass.
- A4: 1 new test asserting `_decompose_day_contributions` produces invariant `Σ contribution_returns == pool_return` for a synthetic 3-strategy day. `# Layer: invariant`
- A5: 1 modified test (the `pool_corr_excludes_self` assertion in `test_backtest_types_phase5a.py` is deleted); 1 new test asserts `POOL_CORR_MODE == "LOO_ONLY_v0"`. `# Layer: invariant`

### 6.2 Thread B (Test hardening) — load-bearing fixture

Thread B introduces a mix of invariant and behavioral tests. The taxonomy split is critical here because Thread B's whole purpose is to close 5d's tautology gap — so we explicitly mark which assertions survive fixture rewrites and which depend on fixture shape.

**Fixture self-smoke (B6):** `# Layer: behavioral` — the fixture's job IS to produce specific dynamics.

```python
def test_warm_pool_fixture_produces_non_none_pool_corr(phase5d_warm_pool):
    """# Layer: behavioral
    The fixture itself must produce non-None pool_corr on >= 1 bid.
    If this test fails, the behavioral assertions in B7 are vacuous —
    the same trap that bit us in 5d Task 8.
    """
    bids_with_corr = [
        b for b in phase5d_warm_pool["shared"].bid_history
        if b.pool_corr is not None
    ]
    assert len(bids_with_corr) > 0, (
        "Warm-pool fixture did not warm up pool_corr — fix the fixture"
    )
```

**Cross-validation tests (B7):**

```python
def test_phase5e_avg_pool_corr_matches_bid_history_mean(phase5d_warm_pool):
    """# Layer: invariant
    The aggregate `c.avg_pool_corr` is, by construction, the mean of all
    non-None `b.pool_corr` for bids of that strategy. This holds for ANY
    fixture — vacuous-fixture-safe because the equality is verified per
    strategy by reconstructing the expected mean from the same data.
    """
    r = phase5d_warm_pool["shared"]
    for s, c in r.per_strategy_stats.items():
        bid_corrs = [b.pool_corr for b in r.bid_history
                     if b.strategy == s and b.pool_corr is not None]
        if not bid_corrs:
            assert c.avg_pool_corr is None
            continue
        expected = sum(bid_corrs) / len(bid_corrs)
        assert c.avg_pool_corr is not None
        assert abs(c.avg_pool_corr - expected) < 1e-9


def test_phase5e_n_would_change_rank_aggregate_consistency(phase5d_warm_pool):
    """# Layer: invariant
    Per-strategy `n_would_change_rank` summed across strategies MUST equal
    the total count of `would_change_rank=True` BidRecords. This holds for
    ANY fixture (including empty) — pure aggregation consistency.
    """
    r = phase5d_warm_pool["shared"]
    total_flips = sum(1 for b in r.bid_history if b.would_change_rank)
    aggregate = sum(c.n_would_change_rank for c in r.per_strategy_stats.values())
    assert total_flips == aggregate


def test_phase5e_warm_pool_produces_at_least_one_rank_flip(phase5d_warm_pool):
    """# Layer: behavioral
    The warm-pool fixture is engineered to produce ≥1 rank flip via
    anti-correlated curves. If this test fails, the fixture has drifted
    and the rank-flip code path is no longer exercised — fix the fixture.
    """
    r = phase5d_warm_pool["shared"]
    total_flips = sum(1 for b in r.bid_history if b.would_change_rank)
    assert total_flips > 0, "Fixture too tame — no rank flips produced"
```

**Why the split matters:** the B7 cross-validation tests (aggregation consistency, mean reconstruction) are now PURE invariants — they would catch a regression where finalization drops bids, miscounts flags, or uses the wrong mean formula, regardless of fixture dynamics. The "at least one rank flip occurs" assertion is a BEHAVIORAL guard on the fixture itself, isolated as a separate test so it can fail independently without obscuring the invariant signal.

**Tightened web assertions (B8):** `# Layer: invariant` — assert literal template-rendering contract.

```python
# OLD: assert "1−0.5ρ" not in r.text or "1−" not in r.text  # tolerant
assert "贡献调整" not in r.text  # Phase 5d marker absent when disabled

# OLD: assert "avg pool ρ" in r.text or "pool ρ" in r.text or "avg pool" in r.text
assert "avg pool ρ" in r.text and "rank Δ" in r.text  # both columns
```

### 6.3 Thread C (Sizing override) — 13 new tests

All Thread C tests are **invariant** unless explicitly marked otherwise. They assert pure-function I/O contracts, validation behavior, and the post-processing-clamp invariant.

**YAML loader (C11, 6 tests):** all `# Layer: invariant`

1. No `sizing:` block → strategy fields all None.
2. Partial: only `base_position_size` → other 2 None.
3. Full override → all 3 set; merged invariant satisfied.
4. Invalid: `min > max` → ConfigError with both values in message.
5. Invalid: `base > max` → ConfigError.
6. Invalid: negative `min_position` → ConfigError with field name + value.

**`compute_position_sizes` (C13, 3 tests):** all `# Layer: invariant`

1. Full override applied: passes 3 overridden values; result clipped to overridden bounds. **Additionally asserts the lock #12 boundary:** the signal-layer inputs (sigma, alpha) are NOT scaled by the override; identical signal inputs with different overrides produce sizes that differ ONLY in the clip envelope.
2. Partial override (only `min`): asserts overridden min, inherited base + max.
3. No override = bit-equivalent Phase 5b: assert result identical to a baseline call without `per_strategy_overrides` for ALL 6 production strategies on a fixed input.

**Integration (C14, 1 test):** `# Layer: invariant`

1. One strategy has `sizing:` block in YAML; run `run_shared_pool_backtest`; assert that strategy's `BidRecord.position_size` always satisfies `eff_min ≤ size ≤ eff_max` (clip envelope respected). This is a pure post-condition that holds independent of dynamics.

**UI (C15, 1 test):** `# Layer: invariant`

1. After backtest with one strategy having overrides, assert `strategies_with_sizing_overrides` set is in the template context AND the bid history HTML contains the tooltip text for at least one row.

### 6.4 Thread D (Allocation observability) — 5 new tests

All metric-correctness tests are **invariant** (deterministic functions of bid_history — see lock #15). The fixture-shape guard is **behavioral**.

**Metric correctness (D20.1, D20.2):**

```python
def test_phase5e_effective_allocation_sums_to_one_or_zero(phase5d_warm_pool):
    """# Layer: invariant
    Σ effective_allocation == 1.0 when ANY capital was allocated; else == 0.0.
    Pure aggregation identity — holds for ANY fixture (including empty).
    """
    r = phase5d_warm_pool["shared"]
    total = sum(c.effective_allocation for c in r.per_strategy_stats.values())
    won_capital = sum(
        b.position_size for b in r.bid_history if b.outcome == "won"
    )
    if won_capital > 0:
        assert abs(total - 1.0) < 1e-9
    else:
        assert total == 0.0


def test_phase5e_rank_drift_sum_to_zero(phase5d_warm_pool):
    """# Layer: invariant
    Σ rank_drift_from_signal == 0 by construction (any permutation of N
    distinct ranks has zero net drift). Pure structural property.

    Note: ties in avg_bid_weight or effective_allocation may produce
    non-unique ranks; this test uses dense-rank semantics consistent
    with the simulator's sort, so the equality still holds.
    """
    r = phase5d_warm_pool["shared"]
    drifts = [c.rank_drift_from_signal for c in r.per_strategy_stats.values()]
    assert sum(drifts) == 0
```

**Fixture-shape guard (D20.3):**

```python
def test_phase5e_warm_pool_produces_at_least_one_nonzero_drift(phase5d_warm_pool):
    """# Layer: behavioral
    The warm-pool fixture is engineered to fire at least one cap (sector,
    correlation, or cash-short) so |rank_drift_from_signal| > 0 for at
    least one strategy. If this test fails, the fixture has drifted and
    the rank-drift code path is no longer exercised — fix the fixture.

    Pairs with the invariant tests above: those verify the metric IS
    correct; this verifies the fixture actually USES the metric's
    non-trivial range.
    """
    r = phase5d_warm_pool["shared"]
    nonzero = [
        c.rank_drift_from_signal for c in r.per_strategy_stats.values()
        if c.rank_drift_from_signal != 0
    ]
    assert len(nonzero) > 0, (
        "Fixture too uniform — no rank drift produced. "
        "Caps must fire for this metric's path to execute."
    )
```

**Why this split is load-bearing:** the invariant tests (D20.1, D20.2) catch any regression in the metric implementation — wrong formula, wrong rank ordering, off-by-one in the sum. The behavioral guard (D20.3) catches fixture drift. If a future change makes the fixture too tame, only D20.3 fails — D20.1 and D20.2 still validate the production code on whatever data the fixture produces, including degenerate cases. This is exactly the property that Phase 5d's `n_would_change_rank` tests lacked.

**Constant anchor (D17):**

```python
def test_phase5e_observability_mode_v1():
    """# Layer: invariant
    Anchors the OBSERVABILITY_MODE constant. Lock #17 forbids null/v0
    states; this test ensures any future schema-bump (v2) is a conscious
    edit that updates this assertion, not an accidental rename.
    """
    from marketpulse.backtest.contribution import OBSERVABILITY_MODE
    assert OBSERVABILITY_MODE == "v1"
```

**Lock #16 contract test (D20.4):**

```python
def test_phase5e_observability_fields_present_on_every_strategy_contribution(
    phase5d_warm_pool,
):
    """# Layer: invariant
    Lock #16 contract: effective_allocation and rank_drift_from_signal
    MUST be present on every StrategyContribution returned by the
    simulator. Downstream consumers (Phase 6 optimizer) rely on this.

    This test fails if any code path returns a StrategyContribution
    without the Phase 5e fields populated (would manifest as a default
    0.0 / 0 leak from the dataclass default, which is acceptable
    semantically but would indicate the simulator path didn't compute
    the metric — a contract violation).

    Verified by: every per_strategy_stats entry has BOTH fields, AND
    at least one has effective_allocation > 0 (proving the simulator
    actually computed them rather than just returning defaults).
    """
    r = phase5d_warm_pool["shared"]
    assert len(r.per_strategy_stats) > 0
    for s, c in r.per_strategy_stats.items():
        # Fields are present (hasattr would catch removal)
        assert hasattr(c, "effective_allocation")
        assert hasattr(c, "rank_drift_from_signal")
        # Types are correct
        assert isinstance(c.effective_allocation, float)
        assert isinstance(c.rank_drift_from_signal, int)
        # Range is sane
        assert 0.0 <= c.effective_allocation <= 1.0
    # Simulator actually computed something (not just defaults)
    total = sum(c.effective_allocation for c in r.per_strategy_stats.values())
    assert total > 0.0, "Simulator returned default zeros — contract violated"
```

**Final integration (16):**

Full suite green; ~935 tests total (915 + 20 new).

---

## 7 — Migration & reproducibility

- **Existing YAMLs (5b/5c/5d shipped):** unmodified. Zero migration. Backtests with the current 6 strategies produce bit-equivalent results before and after 5e ships.
- **New strategies with `sizing:` blocks:** opt-in. Add the block when needed; don't add it when not.
- **`pool_corr_excludes_self` removal:** the field was added in Phase 5d (1 release ago) and only ever set to True. Removing it does not affect any persisted state (BidRecord is in-memory). Test that referenced it is updated.
- **Refactor:** pure code movement, no algorithm change. 915 existing tests act as the regression net. Any test failure = bug in extraction.

---

## 8 — Required test scenarios

| #  | Scenario                                                                          | Task | Layer       |
|----|-----------------------------------------------------------------------------------|------|-------------|
| 1  | `MIN_OVERLAP_DAYS` constant anchored at 30                                        | A1   | invariant   |
| 2  | `phase5d_kwargs_from_metadata(None, s)` returns safe defaults                     | A2   | invariant   |
| 3  | `phase5d_kwargs_from_metadata(meta, s)` unpacks all 7 fields                      | A2   | invariant   |
| 4  | `_decompose_day_contributions` invariant on 3 synthetic strategies                | A4   | invariant   |
| 5  | `BidRecord.pool_corr_excludes_self` field gone (compile-time)                     | A5   | invariant   |
| 5b | `POOL_CORR_MODE == "LOO_ONLY_v0"` constant anchored                               | A5   | invariant   |
| 6  | Warm-pool fixture produces ≥1 bid with non-None `pool_corr`                       | B6   | behavioral  |
| 7  | `avg_pool_corr` matches mean of bid_history non-None values (per strategy)        | B7   | invariant   |
| 8  | `n_would_change_rank` per-strategy sum == total bid_history flag count            | B7   | invariant   |
| 8b | Warm-pool fixture produces ≥1 rank flip                                           | B7   | behavioral  |
| 9  | Hero default-off: `"贡献调整" not in r.text`                                       | B8   | invariant   |
| 10 | Strategy table: `"avg pool ρ"` AND `"rank Δ"` both present                        | B8   | invariant   |
| 11 | No `sizing:` block → Strategy fields all None                                     | C11  | invariant   |
| 12 | Partial `sizing:` (only base) → other 2 None                                      | C11  | invariant   |
| 13 | Full `sizing:` valid → all 3 set                                                  | C11  | invariant   |
| 14 | `sizing.min > sizing.max` → ConfigError with both values                          | C11  | invariant   |
| 15 | `sizing.base > sizing.max` → ConfigError                                          | C11  | invariant   |
| 16 | Negative `sizing.min_position` → ConfigError with field + value                   | C11  | invariant   |
| 17 | `compute_position_sizes` honors full override + lock #12 boundary check           | C13  | invariant   |
| 18 | `compute_position_sizes` honors partial override                                  | C13  | invariant   |
| 19 | `compute_position_sizes` no-override = bit-equivalent Phase 5b (all 6 strategies) | C13  | invariant   |
| 20 | Orchestrator integration: `eff_min ≤ BidRecord.position_size ≤ eff_max`            | C14  | invariant   |
| 21 | UI: `strategies_with_sizing_overrides` set populated when YAML has block          | C15  | invariant   |
| 22 | UI: bid history tooltip contains "custom limits" text for overridden strategy bids| C15  | invariant   |
| 23 | Σ `effective_allocation` == 1.0 (when capital allocated) or 0.0 (when none)       | D20  | invariant   |
| 24 | Σ `rank_drift_from_signal` == 0 (permutation identity)                            | D20  | invariant   |
| 25 | Warm-pool fixture produces ≥1 strategy with non-zero `rank_drift_from_signal`     | D20  | behavioral  |
| 26 | `OBSERVABILITY_MODE == "v1"` constant anchored                                     | D17  | invariant   |
| 27 | Lock #16 contract — `effective_allocation` field present on EVERY `StrategyContribution` returned from simulator (no `getattr` / None fallback ever needed) | D20  | invariant |

**Counts:** 25 invariant tests + 3 behavioral tests (fixture-shape guards) = 28 new tests. Invariant tests dominate (~89%) because Phase 5e's core deliverables are structural contracts and deterministic telemetry, not new dynamics.

---

## 9 — Out of scope (deferred to 5f or later)

- Heavy refactor (caps.py extraction, sizing.py extraction, WEIGHT block extraction). Medium refactor only.
- `target_vol` per-strategy override. Stays global.
- YAML migration of existing 6 strategy files. No existing YAMLs change.
- Removal of `max_neighbor_exposure = 0.0` placeholder from Phase 5c. Stays as v0 placeholder.
- New BidRecord field for "has sizing override". Tooltip uses separate template context.
- Heavy refactor (extract `caps.py`, `sizing.py`, `_compute_weights_with_metadata` helper, BidRecordAssembler layer). Medium refactor only in 5e.
- Live trading. (Phase 6.)
- Strategy evolution. (Phase 7.)
- **`effective_weight_trace` per-BidRecord debug field — deliberately deferred.** Future debugging will benefit from a per-bid trace recording the value at each stage of the pipeline (lock #18). Sketch of the future structure:
    ```python
    @dataclass(frozen=True)
    class EffectiveWeightTrace:
        raw_sharpe: float | None           # signal stage 1
        contribution_multiplier: float     # signal stage 2 (Phase 5d)
        adjusted_weight: float | None      # signal output
        raw_size_dollars: float            # execution stage 2a
        size_after_override_clamp: float   # execution stage 2b
        size_after_sector_cap: float       # execution stage 4a
        size_after_correlation_cap: float  # execution stage 4b
        final_position_size: float         # post-capacity, recorded
        clamps_that_fired: tuple[Literal["override", "sector", "correlation", "capacity"], ...]
    ```
    Why deferred: this is a 12-13 field expansion of `BidRecord` (already 22 fields post-5d). Phase 5e is constraint-defining, not trace-instrumenting. Add to a future spec when Phase 6's optimizer needs to attribute clamps to specific constraints — likely Phase 6 itself or a dedicated debug-instrumentation phase.

---

## 10 — System-evolution status (forward-warning)

**Phase 5e marks the last "local-constraint" phase before Phase 6.**

The Phase 5a-5e arc has been adding constraints and telemetry to a fundamentally **per-strategy independent** computation model:
- 5a: each strategy bids independently, dedup picks one winner per ticker.
- 5b: each strategy's size derives independently from its own sigma + alpha.
- 5c: caps apply post-sizing as filters (sector, correlation neighbors).
- 5d: contribution-adjusted Sharpe touches WEIGHT but the LOO subtraction is still per-strategy decomposition of an already-realized pool.
- 5e: per-strategy overrides clamp the size envelope, still independently.

**Phase 6 will introduce coupling** in at least three ways that escape the per-strategy frame:

1. **Live-trading execution constraints** (slippage, market hours, fill probability) couple all simultaneous orders.
2. **Pool-level risk budgets** (e.g., portfolio VaR, max correlated-cluster exposure beyond the v0 neighbor sum) require global optimization, not per-strategy filtering.
3. **Real-time bid arbitration** (vs. backtest's batch dedup) introduces ordering dependencies that the current "rank by weight, allocate in order" model cannot express.

This means Phase 6 will likely require a **fresh architectural layer** (a `pool_optimizer.py` or similar) sitting between ALLOC and the execution boundary, with its own contract spec. The 5e refactor preparation (extracted `_decompose_day_contributions`, `_phase5d_kwargs_from_metadata`, `MIN_OVERLAP_DAYS` / `POOL_CORR_MODE` constants) deliberately keeps the simulator's per-day loop legible so Phase 6's combinatorial layer can plug in cleanly.

### Three named hidden debts (instrumented by Thread D)

The Phase 5a-5e architecture stacks heuristics in sequential greedy order (DEDUP → SECTOR_CAP → CORRELATION_CAP → CAPACITY_CAP → ALLOC). This stack produces emergent behavior that no single component is responsible for. Three specific debts will compound across the stack as 5f and 6 add more constraints. Naming them explicitly so the rank-drift telemetry has a clear job:

**Debt 1 — Ordering instability:**
DEDUP/CAP/ALLOC are sequential greedy operations. The order in which they execute determines which strategy wins when constraints bind. Reordering `sector_cap_full` and `correlation_cap_full` checks would produce different `BidRecord.outcome` distributions for the same bid set. There is no spec lock guaranteeing the order is optimal — only that it is deterministic. The system has no metric that detects when reordering would have produced a different allocation. **Phase 6 implication:** real-time bid arrival (not batch) makes ordering nondeterministic; current heuristics cannot adapt.
**5e instrumentation:** `rank_drift_from_signal != 0` is a necessary (not sufficient) signal that the heuristic ordering is influencing outcomes beyond what the signal prescribed.

**Debt 2 — Signal-execution mismatch:**
Phase 5d's `pool_corr_excluding_self` measures correlation in the **signal space** (pre-cap per-day contribution returns). Phase 5e's overrides clamp in the **execution space** (post-allocation capital). These two spaces diverge whenever caps fire. A strategy can have a strongly negative `pool_corr` (signal says: this is a hedge, boost it) AND large rank drift (execution says: caps blocked the boost). The pool's realized risk profile is therefore NOT what the contribution-adjusted Sharpe optimization predicted.
**5e instrumentation:** comparing `rank_drift_from_signal` against `rewarded_for_negative_corr` reveals strategies that the signal layer wanted to boost but the execution layer suppressed.

**Debt 3 — No unified objective function:**
The system optimizes a stack of partial objectives:
- 5a: per-strategy rolling Sharpe (signal)
- 5d: pool-correlation penalty (signal modifier)
- 5c: sector + correlation caps (execution constraint)
- 5e: per-strategy clip envelope (execution constraint)

There is no scalar objective $J$ that the entire stack maximizes. The system is **rule-following, not objective-driven**. Phase 6's optimization layer will require defining $J$ — likely a constrained optimization over $\sum_s x_s w_s$ subject to all current heuristics expressed as linear constraints. Until that exists, the current stack's behavior cannot be proved optimal relative to any criterion.
**5e instrumentation:** `effective_allocation` is the canonical observable for "what the system actually optimized," distinct from the bid-weight signal. The gap between them is the heuristic-to-optimal residual that Phase 6 will need to close (or accept).

### Three additional named risks (interpretability watch-list)

These are NOT debts (no compounding component obligation) but interpretability hazards that grow as the heuristic stack deepens. Naming them so future readers can recognize the smell before it becomes a bug:

**Risk A — Semantic layer stacking.**
After 5e the system has four weight semantics in sequence: `raw_sharpe → adjusted_sharpe (contribution multiplier) → weights (DEDUP input) → position_size (after clamp)`. Plus three policy constants: `POOL_CORR_MODE`, `MIN_OVERLAP_DAYS`, `OBSERVABILITY_MODE`. Plus the override envelope. Each layer is independently correct, but debugging an unexpected outcome requires reasoning across all of them: "Did the signal change? Did the contribution multiplier change? Did the clamp fire? Did a cap block it?" The `effective_weight_trace` field deferred in § 9 is the eventual remediation; until then, `rank_drift_from_signal` is the proxy that flags when the layers are doing meaningfully different work.

**Risk B — LOO correlation misinterpretation.**
`pool_corr_excluding_self` is mathematically `corr(A, pool − A)`, not `corr(A, counterfactual pool without A)`. The first is a self-influenced residual correlation — the strategy contributes to the pool aggregate, then is subtracted out, producing a statistically stable but semantically subtle quantity. The spec's Appendix A (Phase 5d) names this boundary; lock #7's `POOL_CORR_MODE` constant documents it. The remaining hazard is human: future engineers may write user-facing copy ("this strategy is uncorrelated with the rest") that overpromises causal independence. Recommended mitigation: when adding any UI surface that displays pool_corr, route the copy through a glossary that links to Appendix A.

**Risk C — Multi-clamp interaction with implicit dependencies.**
Phase 5e adds the sizing-override clamp on top of Phase 5c's sector + correlation caps. The pipeline order is now locked (lock #18), but the SEMANTIC interaction is non-trivial: a strategy might set `max_position=$500` to express "I am a small-bet strategy," and then a sector cap might further reduce that to $300. The user-facing question "why did strategy X get $300 instead of its $500 maximum?" has TWO valid answers (override said max=$500 AND sector cap reduced it further). Without per-bid trace (Risk A's deferred feature), the answer requires reconstructing the pipeline manually. Recommended near-term mitigation: extend the `BidRecord.outcome` Literal to include a `"override_clamp_below_signal"` value when the override was the binding constraint (separate from `sector_cap_full` which already exists). DEFERRED to 5f or later — adds a Literal value to a frozen dataclass.

**Architectural drift acknowledged but deferred:**

- `phase5d_kwargs_from_metadata` lives in `contribution.py` (signal module) but is technically presentation-layer (BidRecord serialization). This is mild drift accepted in 5e to avoid spawning a second new module (the policy.py module from lock #7 is the one new module 5e allows). Phase 6 should formalize a `BidRecordAssembler` layer if telemetry threading grows further.
- `_decompose_day_contributions` stays in `portfolio_simulator.py` because it mutates simulator-local accumulators. Pure-function extraction would require returning ~3 large structures per day, complicating the hot path. Acceptable for 5e.

**Bottom line:** Phase 5e is **system stabilization and constraint layering**, NOT feature expansion. It is the last natural step before Phase 6's combinatorial optimization complexity. Architectural decisions in 5e (especially the lock #12 signal-vs-execution boundary) are designed to survive that transition.
