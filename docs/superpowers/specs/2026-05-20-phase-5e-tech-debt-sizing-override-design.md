# Phase 5e — Tech Debt + Per-Strategy Sizing Override Design

**Status:** Brainstorm complete · ready for plan
**Author:** brainstorm 2026-05-20
**Spec-format:** locked-decision design doc (no TBDs)

---

## 1 — Goal

Tech-debt sprint that strengthens Phase 5d's foundation (refactor + tests) and ships the deferred 5b-3 per-strategy YAML sizing override on the cleaner ground. Three threads, one PR:

1. **Refactor** — `portfolio_simulator.py` 853 → ~770 LOC via targeted extractions.
2. **Test hardening** — close the tautological-test gap noted across 5d code reviews.
3. **5b-3 sizing override** — optional `sizing:` block per strategy YAML, with strict validation.

No new features beyond the sizing override. No new modules. No DB migration. No new dependencies.

---

## 2 — Locked decisions

1. **Sequencing**: single plan, refactor first → test hardening → sizing override → final integration.
2. **Refactor depth**: medium — extract `_phase5d_kwargs` helper to `contribution.py`, extract per-day decomposition block to `_decompose_day_contributions` helper, promote `min_overlap=30` to module constant, drop `BidRecord.pool_corr_excludes_self` field.
3. **Override knobs**: all three sizing knobs (`base_position_size`, `min_position`, `max_position`) overridable per strategy YAML. `target_vol` stays global.
4. **YAML schema**: optional `sizing:` block, partial overrides allowed. Strategies without the block inherit global defaults. Existing 6 YAMLs continue working unmodified (zero migration required).
5. **Validation**: strict at load time. Loader merges overrides with globals, then validates `min ≤ base ≤ max`, all values `> 0`. ConfigError on violation, message includes strategy name + offending values.
6. **Apply scope**: overrides apply to BOTH `sizing_enabled=True` (vol-target × conviction × clip) AND `sizing_enabled=False` (fixed mode uses overridden base).
7. **`pool_corr_excludes_self` removal**: dropped as YAGNI. The forward-flag field is always True in v0, no consumer reads it. Removal is forward-compat for kwarg callers (only kwarg construction in production).
8. **UI surfacing**: bid history `size` column tooltip shows "$<size> (custom limits: $<min>/$<max>)" when the bid's strategy has overrides. No new BidRecord field; pass `strategies_with_sizing_overrides: set[str]` as separate template context.
9. **Test fixture**: one shared `phase5d_warm_pool` pytest fixture (90-day calendar, 2 anti-correlated strategies, bids every other day for 60 days). Lives in `tests/conftest.py` or dedicated fixture module. Smoke-tested itself.
10. **Strategy dataclass extension**: 3 optional fields (`base_position_size`, `min_position`, `max_position` — each `float | None = None`). Existing fields unchanged.
11. **Override map shape**: `dict[str, tuple[float | None, float | None, float | None]]` — strategy → (base, min, max). None at any position means "inherit global".

---

## 3 — Architecture

### 3.1 Three-thread execution

```
Thread A (Refactor, lowest risk)
  ├── A1: MIN_OVERLAP_DAYS constant
  ├── A2: phase5d_kwargs_from_metadata public helper
  ├── A3: Replace inline closure at 7 BidRecord sites
  ├── A4: Extract _decompose_day_contributions helper
  └── A5: Drop pool_corr_excludes_self field

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

Final
  └── 16: Full suite + ruff + module-import smoke + route smoke
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
    in dollars. Per-strategy overrides take precedence over globals."""
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

### 6.1 Thread A (Refactor) — verified by existing 915 tests

- A1: 1 new test asserting `MIN_OVERLAP_DAYS == 30` (anchors the constant).
- A2: 2 new tests for `phase5d_kwargs_from_metadata`: None branch returns safe defaults dict; Some branch returns unpacked metadata.
- A3: No new tests; existing 915 must pass.
- A4: 1 new test asserting `_decompose_day_contributions` produces invariant `Σ contribution_returns == pool_return` for a synthetic 3-strategy day.
- A5: 1 modified test (the `pool_corr_excludes_self` assertion in `test_backtest_types_phase5a.py` is deleted).

### 6.2 Thread B (Test hardening) — load-bearing fixture

**Fixture self-smoke (B6):**

```python
def test_warm_pool_fixture_produces_non_none_pool_corr(phase5d_warm_pool):
    """The fixture itself must produce non-None pool_corr on >= 1 bid.

    If this test fails, the cross-validation tests in B7 are vacuous —
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


def test_phase5e_n_would_change_rank_matches_bid_count(phase5d_warm_pool):
    r = phase5d_warm_pool["shared"]
    total_flips = sum(1 for b in r.bid_history if b.would_change_rank)
    aggregate = sum(c.n_would_change_rank for c in r.per_strategy_stats.values())
    assert total_flips == aggregate
    # Strong precondition: prove the rank-flip path executes at least once
    assert total_flips > 0, (
        "Warm-pool fixture did not produce any rank flip — fixture too tame"
    )
```

**Tightened web assertions (B8):**

```python
# OLD: assert "1−0.5ρ" not in r.text or "1−" not in r.text  # tolerant
assert "贡献调整" not in r.text  # Phase 5d marker absent when disabled

# OLD: assert "avg pool ρ" in r.text or "pool ρ" in r.text or "avg pool" in r.text
assert "avg pool ρ" in r.text and "rank Δ" in r.text  # both columns
```

### 6.3 Thread C (Sizing override) — 13 new tests

**YAML loader (C11, 6 tests):**

1. No `sizing:` block → strategy fields all None.
2. Partial: only `base_position_size` → other 2 None.
3. Full override → all 3 set; merged invariant satisfied.
4. Invalid: `min > max` → ConfigError with both values in message.
5. Invalid: `base > max` → ConfigError.
6. Invalid: negative `min_position` → ConfigError with field name + value.

**`compute_position_sizes` (C13, 3 tests):**

1. Full override applied: passes 3 overridden values; result clipped to overridden bounds.
2. Partial override (only `min`): asserts overridden min, inherited base + max.
3. No override = bit-equivalent Phase 5b: assert result identical to a baseline call without `per_strategy_overrides`.

**Integration (C14, 1 test):**

1. One strategy has `sizing:` block in YAML; run `run_shared_pool_backtest`; assert that strategy's `BidRecord.position_size` reflects the overridden values (e.g., never below custom `min_position`).

**UI (C15, 1 test):**

1. After backtest with one strategy having overrides, assert `strategies_with_sizing_overrides` set is in the template context AND the bid history HTML contains the tooltip text for at least one row.

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

| # | Scenario                                                          | Task |
|---|-------------------------------------------------------------------|------|
| 1 | `MIN_OVERLAP_DAYS` constant anchored at 30                        | A1   |
| 2 | `phase5d_kwargs_from_metadata(None, s)` returns safe defaults    | A2   |
| 3 | `phase5d_kwargs_from_metadata(meta, s)` unpacks all 7 fields     | A2   |
| 4 | `_decompose_day_contributions` invariant on 3 synthetic strategies | A4   |
| 5 | `BidRecord.pool_corr_excludes_self` field gone (compile-time)    | A5   |
| 6 | Warm-pool fixture produces ≥1 bid with non-None `pool_corr`      | B6   |
| 7 | `avg_pool_corr` matches mean of bid_history non-None values       | B7   |
| 8 | `n_would_change_rank` matches bid_history flag count AND > 0     | B7   |
| 9 | Hero default-off: `"贡献调整" not in r.text`                       | B8   |
| 10 | Strategy table: `"avg pool ρ"` AND `"rank Δ"` both present       | B8   |
| 11 | No `sizing:` block → Strategy fields all None                     | C11  |
| 12 | Partial `sizing:` (only base) → other 2 None                      | C11  |
| 13 | Full `sizing:` valid → all 3 set                                  | C11  |
| 14 | `sizing.min > sizing.max` → ConfigError with both values         | C11  |
| 15 | `sizing.base > sizing.max` → ConfigError                          | C11  |
| 16 | Negative `sizing.min_position` → ConfigError with field + value   | C11  |
| 17 | `compute_position_sizes` honors full override                     | C13  |
| 18 | `compute_position_sizes` honors partial override                  | C13  |
| 19 | `compute_position_sizes` no-override = bit-equivalent Phase 5b   | C13  |
| 20 | Orchestrator integration: overridden strategy's BidRecord size ≥ overridden min | C14 |
| 21 | UI: `strategies_with_sizing_overrides` set populated when YAML has block | C15 |
| 22 | UI: bid history tooltip contains "custom limits" text for overridden strategy bids | C15 |

---

## 9 — Out of scope (deferred to 5f or later)

- Heavy refactor (caps.py extraction, sizing.py extraction, WEIGHT block extraction). Medium refactor only.
- `target_vol` per-strategy override. Stays global.
- YAML migration of existing 6 strategy files. No existing YAMLs change.
- Removal of `max_neighbor_exposure = 0.0` placeholder from Phase 5c. Stays as v0 placeholder.
- New BidRecord field for "has sizing override". Tooltip uses separate template context.
- Live trading. (Phase 6.)
- Strategy evolution. (Phase 7.)
