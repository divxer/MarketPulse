# Phase 5a — Shared Capital Pool Design

**Status:** Approved (brainstormed 2026-05-20, 5 user questions answered, 13 decisions locked)
**Predecessor:** Phase 4 Backtest Engine MVP (`2026-05-19-phase-4-backtest-engine-mvp.md`)
**Successors:** Phase 5b (dynamic position sizing), Phase 5c (cross-strategy exposure caps), Phase 5d (risk budgets), Phase 6 (live trading hookup), Phase 7 (strategy evolution)

---

## 1. Identity

Phase 5a is the **True Coupling foundation** — it replaces Phase 4's six isolated $10k portfolios with **ONE** $10k pool that all strategies bid into. The six strategies stop being parallel universes and become competing claimants on a single capital pool.

**This phase introduces endogenous competition between strategies.** Where Phase 4 measured strategies independently (an *observation system*), Phase 5a forces them to interact for a finite resource (a *competition system*). The shift is structural — every subsequent phase (5b sizing, 5c exposure, 5d feedback, 6 live, 7 evolution) operates inside this competitive substrate, not the isolated one.

This is the first phase where strategy quality (measured by Phase 2 hit-rate and Phase 4 Sharpe) **directly drives capital allocation**, closing the feedback loop:

```
Phase 2 hit rate → Phase 4 Sharpe → Phase 5a bid weight → real capital → P&L → Phase 2 hit rate
```

System identity statement (quoted in module docstrings):

> A Sharpe-weighted bidding mechanism that allocates a single shared capital pool across competing strategies, using rolling causal performance windows to determine bid priority. NOT a multi-account portfolio manager.

### What this means in practice

- Phase 4 today: 6 separate $10k pools, each strategy runs in isolation, no interaction.
- Phase 5a tomorrow: 1 shared $10k pool. On any given day, multiple strategies may signal, but only the highest-Sharpe ones get capital. If momentum_breakout has been outperforming over the last 60 days, it wins more bids. If oversold_reversal has been losing, its bids get out-prioritized.

### Out of scope (explicit deferrals)

- **Dynamic position sizing** (Phase 5b) — Phase 5a keeps the fixed `$1k` position size from Phase 4.
- **Cross-strategy sector / correlation caps** (Phase 5c) — Phase 5a only does per-ticker dedup.
- **Per-strategy risk budget ceilings** (Phase 5d) — e.g., momentum ≤ 40% of pool. Phase 5a has no per-strategy caps.
- **Negative-Sharpe lockout** — Phase 5a keeps negative strategies in the bidding game (floored at 0.1). They're penalized, not killed.
- **Bull/bear conflict resolution** — moot at v0 because Phase 4 is long-only (only bullish events trigger positions). Bearish events still record verdicts for hit-rate scoring.

---

## 2. Core Algorithm — Shared Pool Simulator

The daily loop continues Phase 4's `CLOSE → OPEN → MTM → RECORD` discipline, but `OPEN` expands into a three-step subroutine specific to shared bidding. The full order is:

```
CLOSE → BID COLLECT → WEIGHT COMPUTE → DEDUP → ALLOCATE → MTM → RECORD
```

This order is a **spec-locked invariant**. Tests enforce it directly. Reordering any step causes downstream bugs: dedup before weight gives wrong winners; allocate before dedup double-counts cap.

### Daily loop pseudocode

```python
for d in trading_calendar:

    # ─── CLOSE ─── (same as Phase 4)
    for pos in open_positions:
        if pos.horizon_date == d:
            realized_ret = (pos.horizon_price - pos.entry_price) / pos.entry_price
            cash += pos.position_size * (1 + realized_ret)
            trade_returns[pos.strategy].append(realized_ret)
            open_positions.remove(pos)

    # ─── BID COLLECT ───
    todays_bids = [
        (event, strategy)
        for event in events_on_day(d)
        if event.subtype == "bullish"
        and event.ticker not in {p.ticker for p in open_positions}
        # in-flight dedup: don't double up on a ticker already held
    ]

    # ─── WEIGHT COMPUTE ───
    strategies_today = {b.strategy for b in todays_bids}
    raw_weights = {
        s: rolling_sharpe(db, strategy=s, as_of=d, lookback_days=60)
        for s in strategies_today
    }

    if all(w is None for w in raw_weights.values()):
        weights = {s: 1.0 for s in strategies_today}  # bootstrap: full equal-weight
    else:
        known = [w for w in raw_weights.values() if w is not None]
        avg_known = sum(known) / len(known)
        weights = {
            s: max(raw_weights[s] if raw_weights[s] is not None else avg_known, 0.1)
            for s in strategies_today
        }

    # ─── DEDUP (same-day same-ticker collision) ───
    bids_by_ticker = group_by(todays_bids, key=lambda b: b.ticker)
    winners = {}
    for ticker, bids in bids_by_ticker.items():
        # Tiebreaker chain (deterministic across runs):
        #   1. Primary: highest bid weight  (rolling Sharpe-based)
        #   2. Secondary: earliest event_time  (whoever signaled first)
        #   3. Tertiary: alphabetical strategy name  (paranoia fallback —
        #      event_time has microsecond precision so collisions are rare)
        #
        # min(...) on (-weight, event_time, strategy_name):
        #   - negated weight makes "highest weight" sort first
        #   - event_time ASC means earliest signal wins ties
        #   - strategy_name ASC is the ultimate deterministic tiebreaker
        #
        # Semantic meaning: "the strategy that the data validates most
        # gets the trade. If two are equally validated, the one that
        # spoke first gets it (rewards early conviction)."
        best = min(bids, key=lambda b: (
            -weights[b.strategy], b.event_time, b.strategy,
        ))
        winners[ticker] = best
        for loser in bids:
            if loser != best:
                bid_records.append(BidRecord(
                    date=d, strategy=loser.strategy, ticker=ticker,
                    weight=weights[loser.strategy], outcome="dedup_loser",
                    winner=best.strategy,
                ))

    # ─── ALLOCATE (capital-constrained, greedy by weight desc) ───
    # Same 3-key tiebreaker as DEDUP: weight desc → event_time asc → strategy asc
    sorted_winners = sorted(
        winners.values(),
        key=lambda b: (-weights[b.strategy], b.event_time, b.strategy),
    )
    for bid in sorted_winners:
        capital_in_use = sum(p.position_size for p in open_positions)
        if capital_in_use + position_size > max_capital_in_use:
            bid_records.append(BidRecord(
                date=d, strategy=bid.strategy, ticker=bid.ticker,
                weight=weights[bid.strategy], outcome="cap_full", winner=None,
            ))
            continue
        if cash < position_size:
            bid_records.append(BidRecord(
                date=d, strategy=bid.strategy, ticker=bid.ticker,
                weight=weights[bid.strategy], outcome="cash_short", winner=None,
            ))
            continue
        open_positions.append(_OpenPosition(
            strategy=bid.strategy, ticker=bid.ticker,
            entry_date=d, entry_price=bid.event_price,
            horizon_date=bid.horizon_date, horizon_price=bid.horizon_price,
            position_size=position_size,
        ))
        cash -= position_size
        n_trades[bid.strategy] += 1
        bid_records.append(BidRecord(
            date=d, strategy=bid.strategy, ticker=bid.ticker,
            weight=weights[bid.strategy], outcome="won", winner=None,
        ))

    # ─── MTM ─── (same linear interpolation as Phase 4)
    positions_value = 0.0
    for pos in open_positions:
        if pos.entry_date == d:
            positions_value += pos.position_size  # no same-day MTM
        else:
            fraction = elapsed_fraction(calendar, entry=pos.entry_date,
                                        horizon=pos.horizon_date, current=d)
            est_price = pos.entry_price + (pos.horizon_price - pos.entry_price) * fraction
            positions_value += pos.position_size * (est_price / pos.entry_price)

    # ─── RECORD ───
    equity_curve.append((d, cash + positions_value))
    for s in all_strategies:
        deployed = sum(p.position_size for p in open_positions if p.strategy == s)
        per_strategy_exposure[s].append((d, deployed / initial_capital))
```

### Spec-locked invariants

1. **Order strict** — `CLOSE → BID → WEIGHT → DEDUP → ALLOC → MTM → RECORD`. Tests: `test_shared_pool_close_frees_cap_before_alloc`, `test_shared_pool_dedup_before_alloc`.

2. **Causal Sharpe** — rolling Sharpe uses `as_of=d`, but internally only includes outcomes whose `horizon_date < d`. No future leakage. Tests: `test_rolling_sharpe_excludes_future_outcomes`, `test_sharpe_does_not_peek_future`.

3. **Dedup before allocate** — ticker collisions resolved by weight comparison BEFORE the cap-constrained allocation loop runs. Reversing this would let losers consume cap they shouldn't.

4. **Equal-weight bootstrap** — when all strategies firing today have `None` Sharpe (n<5 mature outcomes in lookback), all get weight 1.0. Phase 5a degrades to FIFO during the cold-start period without deadlocking.

### Edge cases (explicit)

- **Same strategy, multiple tickers same day** → each bid is independent. Strategy can win multiple positions if their weights are high.
- **No signals on day d** → only `CLOSE + MTM + RECORD` run. Equity unchanged from open positions' MTM.
- **All bids dedup-lost or cap-skipped** → `ALLOC` input empty, equity drifts on MTM only.
- **Cash drops below `$1k` but cap not full** → distinct counter `n_cash_short_skipped` (vs cap-full `n_capacity_skipped`). Both surfaced in `StrategyContribution`.
- **In-flight ticker** (already held in another position) → bid filtered out in `BID COLLECT` step. Avoids doubling up.
- **Same-day round-trip allowed (v0 simplification)** → a position closes on day d (CLOSE step) and a NEW bid for the same ticker arrives on day d (BID COLLECT step). Because CLOSE precedes BID COLLECT in the strict order, the ticker is no longer in `open_positions` when bid collection runs, so the new bid passes the in-flight filter and may open. Real trading would require a 1-day cooldown to avoid wash-sale rules and over-trading. **Phase 5a accepts this as a v0 simplification.** Phase 5b/6 should add `min_holding_days_after_close` enforcement. Tracked as an explicit non-goal in §1.
- **Equal-weight tiebreaker** → 3-key composite, both DEDUP and ALLOC use it: `(-weight, event_time, strategy_name)`. Semantic order:
  1. **Primary — weight desc**: highest rolling Sharpe wins (the validated quality signal)
  2. **Secondary — event_time asc**: among equal-weight bids, the *earliest signal* wins (rewards prompt conviction; event_time has microsecond precision so practical collisions are rare)
  3. **Tertiary — strategy_name asc**: alphabetical fallback for the freak case where two events have identical event_time
  
  Deterministic across runs and Python versions. Test: `test_equal_weight_tiebreak_uses_event_time_then_alpha`.

---

## 3. Bid Weighting + Rolling Sharpe Service

### Rolling Sharpe contract

```python
def rolling_sharpe(
    db: Session,
    *,
    strategy: str,
    as_of: date,                    # the bidding day
    lookback_days: int = 60,
    min_events_for_sharpe: int = 5, # match Phase 4 n_trades floor
) -> float | None:
    """Sharpe of strategy's per-day equity-curve returns over a causal window.

    Returns None if fewer than min_events_for_sharpe mature outcomes have
    horizon_date in [as_of - lookback_days, as_of). Causal cutoff:
    outcomes that ARE mature but whose horizon_date >= as_of are excluded.
    """
```

### Implementation strategy — reuse Phase 4 isolated curves

Phase 5a does NOT rebuild Sharpe from scratch per bid call. Instead:

1. **Pre-compute** all six per-strategy isolated `StrategyBacktestResult` objects (Phase 4's existing `run_all_backtests` already does this).
2. **Expose** each strategy's full (un-downsampled) daily equity curve via a new `full_equity_curve` field on `StrategyBacktestResult`.
3. **At each bid step**, slice each strategy's daily curve to the lookback window and compute Sharpe via `empyrical.sharpe_ratio` on the diff'd values.

This means the Phase 5a "extra work" beyond Phase 4 is purely the bidding logic in the daily loop. Pre-computation cost is unchanged.

### Design rationale — why use Phase 4 ISOLATED curves (not shared-pool slices) for Sharpe

> **⚠ Intentional bootstrap — feedback inconsistency acknowledged**
>
> Bid weights in Phase 5a are derived from **isolated strategy performance**
> (each strategy's hypothetical $10k-alone curve), not from the strategy's
> realized **shared-pool contribution**. The "world that measures quality"
> and the "world that allocates capital" are deliberately decoupled in v0.
>
> This is **intentional**, not an oversight. Coupling them in v0 would
> create a recursive allocation loop: a strategy that got starved of capital
> last month would have low realized returns, which would lower its weight
> this month, which would starve it further — strategies that lose the
> early bidding race never recover.
>
> Phase 5d (deferred) will replace this with a **contribution-adjusted**
> rolling Sharpe that uses `StrategyContribution.avg_exposure`-weighted
> realized returns. By then we will have enough live data to choose a
> dampening factor that prevents recursive starvation while still letting
> bad strategies decay.

**v0 bootstrap (Phase 5a):**
- Bid weight source = Phase 4 isolated daily curve (intrinsic quality)
- Pros: stable, breaks the starvation feedback loop, easier to debug
- Cons: high-Sharpe strategies that happen to fire late stay starved
  even though their realized PnL would have been good

**Phase 5d planned source:**
- Bid weight source = exposure-adjusted realized daily returns from the
  shared pool (with anti-recursion dampening — exact form TBD)
- Pros: closes the feedback loop honestly
- Cons: requires careful regularization (out of v0 scope)

**Edge case:** Phase 4 isolated runs use the **same event set** as Phase 5a shared runs, just with each strategy getting its own $10k. The pairs going into both simulators are identical (same `get_bullish_events_with_outcomes` query). So the isolated daily curve is a clean signal of strategy quality at the event-attendance level.

**Locked behavior — do not change without a Phase 5d-scope spec.** A test enforces: bid weights at day d are computed using `isolated_results[s].full_equity_curve`, NEVER using the shared-pool curve's per-strategy slice. Test: `test_bid_weight_source_is_isolated_curve_not_shared_slice`.

### Bid weight computation function

```python
def compute_bid_weights(
    strategies_today: list[str],            # strategies with bids on day d
    daily_curves: dict[str, list],          # pre-computed isolated curves
    *,
    as_of: date,
    lookback_days: int = 60,
    min_floor: float = 0.1,                 # negative Sharpe floor
) -> dict[str, float]:
    """Compute per-strategy bid weights using rolling Sharpe.

    Algorithm (spec § 2):
      1. Slice each strategy's daily curve to [as_of - lookback_days, as_of)
      2. Compute Sharpe via empyrical on diff'd values; gate at n<5 = None
      3. If all None → all 1.0 (full equal-weight bootstrap)
      4. Otherwise: None strategies = mean of known; floor 0.1

    Contract:
      - All strategies in `strategies_today` MUST be keys of `daily_curves`.
        Missing-key behavior is undefined; the caller (orchestrator) is
        responsible for ensuring the dict is complete. Test
        `test_compute_bid_weights_raises_on_missing_strategy` locks this
        with an explicit KeyError assertion.
      - `daily_curves[s]` may be empty list (zero-event strategy in window)
        → that strategy's slice will also be empty → n=0 < 5 → None →
        bootstrap path applies.
      - `as_of` must be a real trading date in the calendar (no validation
        in this fn; rolling_sharpe handles cutoff math).
      - `min_floor` lower-bounds ALL weights including non-None ones.
        Spec-locked at 0.1; do not change without revisiting §3 floor design.
    """
```

### Floor design — why 0.1

Negative-Sharpe strategies are not locked out; they are penalized:

- Historically strong strategy: Sharpe = 2.0 → weight 2.0
- Historically weak strategy: Sharpe = -0.5 → weight 0.1 (floor)
- Ratio 20:1 → weak strategy rarely wins capital, but never receives a death sentence

**Reasoning:** A negative Sharpe could be a noise patch (20 trades, 8 losers, briefly underwater). Locking out permanently is overreaction. As the lookback window rolls forward, if the strategy has real alpha, Sharpe returns positive. Meanwhile high-Sharpe strategies dominate capital naturally.

### Edge case: all weights negative or None

When all firing strategies have Sharpe < 0 and `min_floor=0.1` applies uniformly, all weights become 0.1, and `sorted_winners` order degenerates to insertion order. Effectively FIFO during a bad regime — keeps the system running, no deadlock. Test: `test_bid_weight_all_negative_degenerates_to_fifo`.

---

## 4. Data Model

### No new database tables

Phase 5a stays read-side over Phase 1-3 outputs, same philosophy as Phase 4. No Alembic migration. All Phase 5a state is in-memory during the backtest run.

### Three new / extended dataclasses

```python
# marketpulse/backtest/types.py

@dataclass(frozen=True)
class StrategyBacktestResult:
    """Phase 4 isolated per-strategy result — a serialization-friendly DTO.

    Stays a clean serializable shape (no large internal-use arrays). The
    two reserved Phase 5 hooks (previously always None) are now populated
    when this result is part of a shared-pool run. Toggle-mode
    'Per-Strategy' view shows enriched isolated results — same shape,
    more populated fields.
    """
    # ... all existing Phase 4 fields unchanged ...

    # Phase 5 hooks — populated by shared-pool runs
    strategy_exposure: float | None = None   # avg deployed / initial in shared pool
    capital_bid_score: float | None = None   # avg rolling Sharpe weight used


@dataclass(frozen=True)
class StrategyBacktestArtifacts:
    """Diagnostic + cross-module compute layer for a per-strategy run.

    Separates SERIALIZATION concerns (StrategyBacktestResult — what goes
    into templates, JSON, pickle, API responses) from COMPUTE concerns
    (StrategyBacktestArtifacts — what the Phase 5a shared-pool simulator
    needs internally for rolling Sharpe lookups).

    Why split: the un-downsampled daily curve can be hundreds of rows.
    Embedding it in StrategyBacktestResult would bloat every template
    payload, every cached API response, and any future serialization
    use case. By living on a sibling Artifacts dataclass, the result
    stays a small clean DTO.

    Orchestrator returns BOTH for each strategy:
        run_all_backtests(...) -> list[StrategyBacktestResult]      # for templates
        run_all_backtests(..., return_artifacts=True) -> tuple[
            list[StrategyBacktestResult], list[StrategyBacktestArtifacts]
        ]                                                            # for Phase 5a

    Phase 4 callers never need artifacts. Phase 5a's orchestrator always
    requests them. The artifacts list is parallel-indexed to the results
    list (same order, same length).
    """
    strategy: str                                    # links to StrategyBacktestResult.strategy
    full_equity_curve: list[tuple[date, float]]      # un-downsampled, one row per trading day
    # Future Phase 5d/6 may add: full_drawdown_curve, daily_bids_attempted, etc.


@dataclass(frozen=True)
class PortfolioBacktestResult:
    """Phase 5a shared-pool result — the ONE portfolio combining all strategies.

    Field order: all non-defaulted fields FIRST (Python dataclass requirement);
    defaulted provenance fields LAST. Mirrors Phase 4's StrategyBacktestResult
    ordering — non-default-then-default — and avoids the
    'non-default argument follows default argument' import-time TypeError.
    """

    # Identity (required)
    horizon: int                          # 5 / 20 / 60

    # Aggregate counts (required)
    n_trades: int                         # positions opened across all strategies
    n_dedup_total: int                    # cross-strategy ticker collisions resolved

    # Utilization (required) — mean over all trading days of
    #   (capital_in_use_at_record_step / max_capital_in_use)
    # Critical interpretability metric:
    #   - low cum_return + high utilization = bad alpha
    #   - low cum_return + low utilization  = cap-starved (not bad alpha)
    # Computed in the RECORD step of the daily loop; averaged at end.
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
    daily_equity_curve: list[tuple[date, float]]   # downsampled to ~120 points
    excess_vs_spy: float                           # combined cum − spy.cum

    # Breakdown + diagnostics (required)
    per_strategy_stats: dict[str, "StrategyContribution"]
    bid_history: list["BidRecord"]        # capped at MAX_BID_RECORDS_RENDERED = 100

    # Defaulted provenance (always-default in v0; future versions vary)
    display_name: str = "Shared Pool"
    mtm_model: str = "linear_interpolation_v0"
    bid_policy: str = "rolling_sharpe_60d_v0"   # Phase 5a provenance


@dataclass(frozen=True)
class StrategyContribution:
    """One strategy's slice of a shared-pool run.

    Separate from StrategyBacktestResult because the per-strategy Sharpe
    / Sortino / max_drawdown make NO sense in the shared-pool context
    (the pool has those; the strategy contributes to them, but doesn't
    have its own).
    """
    strategy: str
    display_name: str
    n_trades: int                # bids this strategy won
    n_dedup_skipped: int         # lost ticker dedup to higher-Sharpe rival
    n_capacity_skipped: int      # pool cap was full at allocation time
    n_cash_short_skipped: int    # pool cash < position_size
    contribution_pnl: float      # $ this strategy's positions added to the pool
    avg_exposure: float          # avg capital fraction this strategy held
    avg_bid_weight: float        # avg rolling-Sharpe weight in its bids
    n_bids: int                  # total bid attempts (won + all skipped variants)
    n_floor_hits: int            # bids where rolling Sharpe < 0 → floored at 0.1.
                                 # Distinguishes "dying strategy" (high floor-hit
                                 # rate, weights consistently below threshold)
                                 # from "unlucky" (occasional floor hit, otherwise
                                 # competitive). Critical for diagnosing whether
                                 # to delist a strategy in future phases.


@dataclass(frozen=True)
class BidRecord:
    """One bid decision — diagnostic timeline."""
    date: date
    strategy: str
    ticker: str
    weight: float                # weight used (post-floor, post-bootstrap)
    outcome: Literal["won", "dedup_loser", "cap_full", "cash_short"]
    winner: str | None           # who won the ticker (only when outcome=dedup_loser)


# ───── BidRecord retention policy ─────
#
# Two-layer cap (spec-locked invariant):
#
# 1. Inside simulate_shared_pool() the simulator records EVERY bid attempt
#    into an internal `_all_bids: list[BidRecord]`. This is unbounded
#    during simulation — needed for unit tests that assert specific bid
#    outcomes deep in the timeline.
#
# 2. The orchestrator (run_shared_pool_backtest) slices the LAST 100
#    entries before constructing PortfolioBacktestResult.bid_history.
#    Last-N (not first-N) so the UI shows MOST RECENT decisions.
#
# Test: test_bid_records_capped_at_render_layer asserts that
# `len(result.bid_history) <= 100` AND that they are the last 100
# in chronological order (not the first 100).
#
# Memory bound: even 10k events × 6 strategies = 60k bids at ~80 bytes
# each = ~4.8 MB. Fine for one simulation; the slice prevents per-render
# template bloat.
```

### Orchestrator return contract

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
    """Run Phase 4 isolated backtests + Phase 5a shared-pool backtest.

    Returns:
        {
            'isolated':  list[StrategyBacktestResult],     # 6 strategies + SPY (DTO)
            'artifacts': list[StrategyBacktestArtifacts],  # 6 strategies (internal)
            'shared':    PortfolioBacktestResult,          # new Phase 5a
        }

    - `isolated`: serializable DTOs for the Per-Strategy toggle view.
    - `artifacts`: parallel-indexed (same order/length as isolated, minus SPY)
      with un-downsampled curves; consumed internally by the shared simulator
      for rolling Sharpe; never sent to templates.
    - `shared`: the Phase 5a portfolio result for the Shared Pool toggle view.

    Caller MAY memoize this entire dict per (horizon, since_days) tuple.
    No spec-locked freshness requirement — backtests over historical data
    are deterministic and stable until new EvaluationEvents land.
    """
```

### Provenance fields

Every Phase 5a result carries `bid_policy="rolling_sharpe_60d_v0"`. This lets future versions (Phase 5b/c) coexist:

- `rolling_sharpe_60d_v0` — current
- `rolling_sharpe_90d_v0` — if window changes
- `kelly_v0` — Phase 5b dynamic sizing
- `risk_parity_v0` — experimental

Same provenance pattern as Phase 4's `mtm_model="linear_interpolation_v0"`.

---

## 5. UI — Toggle Mode

### Toggle location: filter card, third chip row

```
┌─ 筛选 ──────────────────────────── 重置 ─┐
│  HORIZON   [1d] [5d*] [20d] [60d]        │
│  TIME      [30d] [90d*] [180d] [全部]    │
│  VIEW      [Per-Strategy*] [Shared Pool] │ ← NEW
└──────────────────────────────────────────┘
```

URL parameter: `?mode=per-strategy | shared-pool`. **Default: `per-strategy`** — preserves Phase 4 backward compatibility for bookmarks and the `/lab/ai-track` arrow link.

### Mode-conditional partial includes

`lab_backtest.html` shell:

```jinja
{% extends "base.html" %}
{% block content %}

{# Warning banner: same in both modes #}
<div class="mp-backtest-warning">ⓘ 研究级模拟引擎 ...</div>

{# Hero: mode-specific sub-text #}
{% include "partials/backtest_hero.html" %}

<section class="mp-backtest-kpi">
  {% if mode == 'shared-pool' %}
    {% include "partials/backtest_kpi_strip_shared.html" %}
  {% else %}
    {% include "partials/backtest_kpi_strip.html" %}
  {% endif %}
</section>

<section class="mp-backtest-body">
  <div class="mp-backtest-main">
    {% include "partials/backtest_equity_chart.html" %}
    {% include "partials/backtest_drawdown_chart.html" %}
    {% if mode == 'shared-pool' %}
      {% include "partials/backtest_bid_history.html" %}
    {% endif %}
  </div>
  <aside class="mp-backtest-rail">
    {% include "partials/backtest_filter_card.html" %}
    {% if mode == 'shared-pool' %}
      {% include "partials/backtest_strategy_table_shared.html" %}
    {% else %}
      {% include "partials/backtest_strategy_table.html" %}
    {% endif %}
  </aside>
</section>

{% endblock %}
```

### KPI strip differences

| Card | Per-Strategy (existing) | Shared Pool (new) |
|------|-------------------------|-------------------|
| 1 | Best Strategy (display_name + Sharpe) | **Pool Sharpe** (combined Sharpe) |
| 2 | Best Sharpe (numeric) | **Pool Cum Ret** |
| 3 | Best Cum Ret | **Pool MaxDD** |
| 4 | Worst MaxDD | **vs SPY** (cum diff) |
| 5 | vs SPY (avg) | **N dedup** (collision health indicator) |

### Strategy table differences

| Col | Per-Strategy (existing) | Shared Pool (new) |
|-----|-------------------------|-------------------|
| 1 | 策略 | 策略 |
| 2 | Sharpe | n_trades (bids won) |
| 3 | MaxDD | n_dedup (lost ticker collisions) |
| 4 | Cum Ret | n_skipped (cap full or cash short) |
| 5 | vs SPY | **contribution PnL** ($) |
| 6 | n | **avg exposure** (%) |
| 7 | skipped | **avg bid weight** |

The strategy display-name link continues to point to `/lab/ai-track?strategy=<name>` for hit-rate cross-reference in both modes.

### Hero text — mode-specific

**Per-Strategy mode** (unchanged):
> 回放 Phase 3 的 6 个策略 + SPY 基准在过去 N 天内的策略级合成 PnL。

**Shared Pool mode** (new):
> 6 个策略共享单一 \$10k 资本池,通过 {{ lookback_days }}-day 滚动 Sharpe 加权竞标分配。撞 ticker 时高 Sharpe 策略赢。**bid_policy={{ bid_policy }}**。

Template variables `lookback_days` and `bid_policy` pulled from the route's context so future Phase 5b changes (e.g., 90d window, kelly_v0 policy) update copy automatically without code edits to the partial.

### Bid history timeline (shared-pool mode only)

```
最近 100 次 bid 决策(诊断用)
─────────────────────────────────────────
5/19  GOOGL  通用分析    0.85  ✓ won
5/19  GOOGL  动量突破    0.71      → lost to 通用分析 (dedup)
5/18  AAPL   通用分析    0.85  ✓ won
5/18  QUBT   通用分析    0.85  ✗ cap full
5/15  NVDA   通用分析    0.85  ✗ cash short (cash=$892)
...
```

Default collapsed to 50 entries; "展开" button expands to 100. Hard-capped at 100 in route (the simulator may track more internally but the UI never sees them).

### Toggle behavior

- Switching modes triggers a GET form submit, same pattern as Horizon / Time chips.
- Horizon, since_days, and mode are three independent dimensions of the URL.
- All combinations bookmarkable.
- `/lab/ai-track` arrow link unchanged (continues to point at Per-Strategy mode).

---

## 6. File Structure

```
marketpulse/backtest/
├── types.py                              MODIFY: add PortfolioBacktestResult,
│                                                 StrategyContribution, BidRecord;
│                                                 extend StrategyBacktestResult with
│                                                 full_equity_curve field
├── sharpe.py                             NEW: rolling_sharpe() + compute_bid_weights()
├── portfolio_simulator.py                NEW: simulate_shared_pool() core loop
├── simulator.py                          MODIFY: populate full_equity_curve on
│                                                 StrategyBacktestResult;
│                                                 add run_shared_pool_backtest() entry
└── __init__.py                           MODIFY: re-export new types

marketpulse/web/
├── routes/backtest.py                    MODIFY: accept ?mode= param, return both
│                                                 isolated + shared results to template
└── templates/
    ├── lab_backtest.html                 MODIFY: mode-conditional partial includes
    └── partials/
        ├── backtest_filter_card.html     MODIFY: add VIEW segment chip row
        ├── backtest_hero.html            MODIFY: mode-specific sub-text
        ├── backtest_kpi_strip_shared.html NEW: pool-level KPI cards
        ├── backtest_strategy_table_shared.html NEW: contributions columns
        └── backtest_bid_history.html     NEW: collapsible timeline

tests/
├── unit/
│   ├── test_backtest_sharpe.py           NEW: rolling Sharpe + bid weighting
│   └── test_backtest_portfolio_simulator.py NEW: shared-pool main loop
├── integration/
│   └── test_backtest_shared_pool.py      NEW: orchestrator + DB seed
└── web/
    └── test_lab_backtest_modes.py        NEW: ?mode=shared-pool route + toggle UI
```

No DB migration. No new dependencies (empyrical-reloaded already added in Phase 4).

---

## 7. Test Plan

### Key invariants to lock (≈44 tests)

**Rolling Sharpe service:**
```
test_rolling_sharpe_excludes_future_outcomes
test_rolling_sharpe_returns_none_when_n_below_5
test_rolling_sharpe_uses_60d_window_by_default
test_rolling_sharpe_normalizes_inf_to_none
```

**Bid weight computation:**
```
test_bid_weight_equal_when_all_strategies_below_threshold
test_bid_weight_avg_fill_when_some_below_threshold
test_bid_weight_floors_negative_sharpe_at_0_1
test_bid_weight_does_not_floor_high_positive_sharpe
test_bid_weight_all_negative_degenerates_to_fifo
test_bid_weight_deep_negative_sharpe_still_floored_at_0_1     # -10 → 0.1
test_compute_bid_weights_raises_on_missing_strategy           # contract enforcement
test_bid_weight_empty_curve_in_daily_curves_returns_bootstrap # n=0 path
```

**Shared-pool simulator:**
```
test_shared_pool_close_frees_cap_before_alloc
test_shared_pool_dedup_picks_highest_sharpe_winner
test_shared_pool_dedup_loser_records_bid_loss
test_shared_pool_dedup_before_alloc
test_shared_pool_greedy_alloc_respects_max_cap
test_shared_pool_greedy_alloc_respects_cash_floor
test_shared_pool_mtm_uses_linear_interp_per_position
test_shared_pool_excess_vs_spy_is_pool_cum_minus_spy
test_shared_pool_no_signal_day_still_records_equity
test_shared_pool_bootstrap_period_uses_equal_weight
test_shared_pool_contribution_pnl_sums_to_pool_pnl
test_shared_pool_in_flight_ticker_filtered_at_bid_collect
test_shared_pool_sharpe_does_not_peek_future
test_shared_pool_same_day_reentry_after_close_allowed       # v0 simplification
test_equal_weight_tiebreak_uses_event_time_then_alpha       # 3-key composite
test_strategy_contribution_avg_exposure_correct             # day-avg math
test_bid_records_capped_at_render_layer                     # last-100 slice
test_simulator_internal_bid_history_unbounded               # raw store unbounded
test_phase4_isolated_results_unchanged_with_shared_run      # Phase 4 regression
test_n_dedup_total_equals_sum_of_per_strategy_n_dedup       # accounting integrity
test_avg_capital_utilization_matches_record_step_mean       # new field accuracy
test_n_floor_hits_increments_only_on_negative_sharpe        # telemetry accuracy
test_n_floor_hits_distinguishes_dying_from_unlucky          # diagnostic value
test_bid_weight_source_is_isolated_curve_not_shared_slice   # Phase 5d boundary enforcement
test_artifacts_full_equity_curve_not_in_result_dto          # DTO/artifact separation
```

**Route + UI:**
```
test_lab_backtest_route_accepts_mode_param
test_lab_backtest_default_mode_is_per_strategy        # backward-compat
test_lab_backtest_shared_mode_renders_pool_kpi
test_lab_backtest_shared_mode_renders_contribution_table
test_lab_backtest_shared_mode_renders_bid_history
test_lab_backtest_filter_card_renders_view_chips
test_lab_backtest_per_strategy_unchanged              # Phase 4 regression
```

### Coverage target

≥ 90% on new modules (`sharpe.py`, `portfolio_simulator.py`). The existing Phase 4 `simulator.py` only gains the `full_equity_curve` field which is exercised by every existing test via the simulator path.

---

## 8. Locked Decisions

13 decisions, locked during the 2026-05-20 brainstorming session:

| # | Decision | Status |
|---|----------|--------|
| 1 | **Scope**: Phase 5a only — Shared Pool + bid policy. NO dynamic sizing / risk budget / sector cap. | LOCKED |
| 2 | **Capital**: 1 pool, $10k initial, $1k fixed position, $10k hard cap. | LOCKED |
| 3 | **Bid policy**: Rolling Sharpe weighting with 60-day lookback. | LOCKED |
| 4 | **Bootstrap**: n<5 → equal-weight; negative Sharpe still participates (floored 0.1). | LOCKED |
| 5 | **Dedup**: Same-day same-ticker → 1 position to highest-weight strategy; losers logged but don't count as cap skip. | LOCKED |
| 6 | **Causality**: Sharpe lookback uses only outcomes mature by day-1 (no future leakage). Test-enforced. | LOCKED |
| 7 | **Daily loop order**: CLOSE → BID → WEIGHT → DEDUP → ALLOC → MTM → RECORD. Strict. | LOCKED |
| 8 | **mtm_model**: same `linear_interpolation_v0` as Phase 4. | LOCKED |
| 9 | **bid_policy field**: `"rolling_sharpe_60d_v0"` provenance for future v1/v2. | LOCKED |
| 10 | **UI**: Toggle on existing `/lab/backtest`, default `?mode=per-strategy`. | LOCKED |
| 11 | **Bid history cap**: render last 100 BidRecord entries. | LOCKED |
| 12 | **DB**: no new tables; no migrations. | LOCKED |
| 13 | **Pool vs isolated**: orchestrator MUST return both shapes from a single call (isolated list + shared result). Caller MAY memoize per (horizon, since_days) filter tuple — implementation choice, not spec-locked. Toggle picks display from already-computed data. | LOCKED |

---

## 9. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| **Sharpe inflation in lookback window** | Use daily-return Sharpe (not per-trade); enforce n<5 None gate; same methodology as Phase 4. |
| **Bootstrap period (first 60d) skews early results** | Documented in hero text. Cold-start behavior expected; tests lock equal-weight fallback. |
| **Bid policy starves slow-to-validate strategies** | Negative Sharpe floor (0.1) keeps them in the bidding game. They still occasionally win when high-Sharpe strategies aren't bidding. |
| **Performance: 6 isolated + 1 shared per page load** | Acceptable at MarketPulse's data volume (hundreds of events, sub-second). Will revisit at 100× scale. |
| **Phase 4 isolated results no longer "the answer"** | Both isolated AND shared computed every render. UI toggle. Per-Strategy is still default. |
| **Lookback window choice (60d) is arbitrary** | Provenance via `bid_policy` field; future runs can use `rolling_sharpe_90d_v0` etc. without breaking history. |
| **Deep-negative Sharpe (e.g., -5, -10) gets same floor as -0.1** | v0 accepts this — floor is constant at 0.1. Catastrophically-losing strategies still get *some* capital (rare, but non-zero). Phase 5b/c can introduce a soft-lockout threshold (e.g., Sharpe < -2.0 → weight 0) once we have enough live data to know what "catastrophic" means in our universe. Acceptable trade-off because n<5 None gate already shields against tiny-sample false negatives. |

---

## 10. Implementation Hand-off

Next steps (per superpowers brainstorming → writing-plans flow):

1. **User reviews this spec.** Request changes inline or approve.
2. **`writing-plans` skill** invoked to produce a task-by-task TDD implementation plan.
3. **`subagent-driven-development` skill** invoked to execute the plan (estimated ~15–18 tasks).
4. **Final review** by the `code-reviewer` agent against this spec.

---

## Appendix A — Spec Coverage Map

Every clause in §1 (Identity) maps to a section below:

| §1 promise | Implemented in |
|------------|-----------------|
| 1 shared $10k pool | §2 Algorithm + §4 Data model (`initial_capital=10000` in orchestrator) |
| 6 strategies bid | §2 BID COLLECT + WEIGHT COMPUTE |
| Phase 2/4 hit rate drives capital | §3 Rolling Sharpe service (uses Phase 4 isolated curves) |
| Same-day collision resolution | §2 DEDUP step (highest weight wins) |
| Rolling Sharpe lookback | §3 `rolling_sharpe()` contract, lookback_days=60 default |
| Causal — no future leakage | §3 (mature outcomes only) + §2 invariant 2 |
| Negative-Sharpe floored at 0.1 | §3 Floor design + §8 decision #4 |
| Toggle UI | §5 (existing `/lab/backtest`, ?mode= param) |
| Phase 4 backward-compat | §5 (default `?mode=per-strategy`) + §7 test `test_lab_backtest_per_strategy_unchanged` |

---

**Spec author:** Claude (brainstorming session 2026-05-20, ~30 min, 5 user-locked decisions + 8 derived locks)

**Review iteration 1 (2026-05-20):** /review identified 1 critical (dataclass field order
would raise TypeError at import), 3 important (bid-history dual-cap policy, underscore-
prefix vs cross-module access, same-day re-entry semantics), and 7 minor items (tiebreaker
determinism, deep-negative-Sharpe floor, parameter contract, design rationale, hero text
templating, missing tests, risks-table coverage). All addressed inline in commit
`1ab1705`.

**Review iteration 2 (2026-05-20):** Second quant review identified 2 MUST FIX
(intentional-bootstrap disclaimer missing on rolling Sharpe source; `full_equity_curve`
on serialized DTO pollutes downstream API/template payloads) + 4 STRONGLY RECOMMEND
(`avg_capital_utilization` field, `n_floor_hits` telemetry, event_time-primary tiebreaker
for trade-semantic alignment, allow caching in decision #13) + 1 prose addition
(endogenous-competition framing in §1). All addressed in this commit:

- §1: added "endogenous competition" paragraph framing Phase 5a as structural shift
- §2: tiebreaker upgraded from 2-key (weight, alpha) to 3-key (weight, event_time, alpha)
- §3: stronger "intentional bootstrap" warning block + Phase 5d planned-source pointer
- §4: split `StrategyBacktestArtifacts` out of `StrategyBacktestResult` to keep DTO clean
- §4: added `avg_capital_utilization` to `PortfolioBacktestResult`
- §4: added `n_floor_hits` to `StrategyContribution`
- §4: orchestrator return contract now exposes `{isolated, artifacts, shared}` triple
- §8 decision #13 wording softened: orchestrator MUST return both; caller MAY memoize
- §7: +5 new tests (now 43 total)

13 locked decisions unchanged. Spec now reads more like a research engine than a dashboard.

---

## 11. Implementation Deltas (post-shipping)

Phase 5a shipped as PR #69 on 2026-05-20. During implementation, four
**meaningful contract refinements** emerged from code-review iterations
that aren't captured above. Recording them here so future Phase 5b/5c
spec writers (and anyone reading this file as a contract) see the true
shipped behavior, not just the design-time intent.

Listed in order of contract impact, most user-visible first.

### Delta 1 — `compute_bid_weights` returns a 2-tuple

**As designed (§ 3):**
```python
def compute_bid_weights(...) -> dict[str, float]:
    """Returns dict[strategy_name, weight]."""
```

**As shipped:**
```python
def compute_bid_weights(...) -> tuple[dict[str, float], set[str]]:
    """Returns (weights, floor_hits) — floor_hits is the set of strategies
    whose raw Sharpe was below min_floor and got clipped up."""
```

**Why changed:** the original spec had the simulator re-running
`rolling_sharpe` to detect floor hits for the `n_floor_hits` telemetry.
That duplicated the magic constant `0.1`, miscounted strategies whose
avg-fill happened to equal 0.1, and miscounted real Sharpe=0.1 as a
floor hit. Cleaner: have the weight computation return the floor-hit
set directly. Caller iterates it for telemetry, no second call.

**Locked in PR #69 commit:** `ca3a7c7 fix(phase-5a): compute_bid_weights returns (weights, floor_hits) tuple`

**Tests:** `test_compute_bid_weights_returns_floor_hits_set`, `test_compute_bid_weights_bootstrap_returns_empty_floor_hits`

### Delta 2 — `bid_policy` reflects `lookback_days` dynamically

**As designed (§ 4, § 8 decision #9):**
> Every Phase 5a result carries `bid_policy="rolling_sharpe_60d_v0"`.

**As shipped:**
```python
bid_policy = f"rolling_sharpe_{lookback_days}d_v0"
```

The default 60d still yields `"rolling_sharpe_60d_v0"`. But callers
passing `lookback_days=90` now get `"rolling_sharpe_90d_v0"` in the
result, instead of the spec's hardcoded "60d" lie.

**Why changed:** code-review iteration 2 caught that the orchestrator
threads `lookback_days` through but the result's provenance string
never updated. Dashboards and logs would have misreported the lookback
window for any non-default run. Trivial fix; preserves the provenance
contract's value.

**Locked in PR #69 commit:** `8a1a67d fix(phase-5a): bid_policy reflects lookback_days + deterministic per_strategy_stats order`

**Tests:** `test_shared_pool_bid_policy_reflects_lookback_days`, `test_shared_pool_bid_policy_set_on_empty_bids_path`

### Delta 3 — `per_strategy_stats` iteration is deterministic (alphabetical)

**As designed (§ 4):** unspecified — `set(daily_curves.keys())`
iteration leaves dict insertion order to Python's random hash seed.

**As shipped:** `sorted(daily_curves.keys())`, so the dict's iteration
order is locked to alphabetical strategy name.

**Why changed:** templates iterate `per_strategy_stats.items()`. Set
iteration is hash-randomized → strategy table rows render in different
order across identical backtest runs, which looks like a bug to users
diffing snapshots. One-character fix (`set` → `sorted`), but worth
recording so Phase 5b doesn't accidentally re-introduce non-determinism
when adding new strategies.

**Locked in PR #69 commit:** `8a1a67d`

**Tests:** `test_shared_pool_per_strategy_stats_iteration_is_sorted`

### Delta 4 — `contribution_pnl` includes unrealized MTM at window end

**As designed (§ 4):** `contribution_pnl: float` — described as "$
this strategy's positions added to the pool." Implementation summed
only realized trade returns.

**As shipped:** `contribution_pnl = realized + unrealized_at_window_end`
where unrealized = linear-interpolated MTM of any positions still open
on the last calendar day (matching the RECORD step's per-day MTM).

**Why changed:** without including unrealized MTM, the invariant
`Σ contribution_pnl == pool_pnl` only holds when every position's
horizon falls inside the window. Phase 5a tests happened to satisfy
this by construction. Future Phase 5b/5c with arbitrary windows would
break the invariant silently. Spec didn't anticipate.

**Locked in PR #69 commit:** `8797b2a fix(phase-5a): contribution_pnl includes unrealized MTM of open positions`

**Tests:** `test_shared_pool_contribution_pnl_includes_unrealized_mtm`, `test_shared_pool_contribution_pnl_sums_to_pool_pnl`

### Delta 5 — `_simulate_strategy_daily` private helper extracted

**As designed (§ 6):** plan had `simulate_strategy_with_artifacts`
re-run the daily loop to grab the un-downsampled curve, as a "v0
trade-off to keep Phase 4 hot path isolated."

**As shipped:** code review iteration 2 caught this as a real
maintenance hazard. Extracted `_simulate_strategy_daily` private helper
that both `simulate_strategy_from_pairs` and
`simulate_strategy_with_artifacts` consume. Single source of truth for
the inner CLOSE/OPEN/MTM/RECORD loop.

**Why noted here:** this is an **internal** refactor (no API change)
so it doesn't belong in the §6 file structure or §4 data model. But
future readers tracing why both public entry points return matching
data should know it's by construction, not by accident.

**Locked in PR #69 commit:** `4ac799c refactor(backtest): extract daily loop into _simulate_strategy_daily helper`

### Decisions Still Locked

The 13 decisions in § 8 remain unchanged. Deltas 1–4 above refine HOW
those decisions are surfaced in the contract; none of them flip a
decision. Specifically:

- Decision #3 (60-day Sharpe lookback) — still default; just provenance string is now dynamic
- Decision #9 (bid_policy provenance) — string format refined; pattern unchanged
- Decision #4 (n<5 floor + bootstrap) — refactored detection, same behavior

If Phase 5b changes any of these, write a fresh spec there. Don't edit
this file's locked-decisions table.

---

**Phase 5a complete:** 17 commits on PR #69, 780 repo-wide tests pass,
shipped 2026-05-20.
