# Phase 4 — Backtest Engine MVP

> **Research-grade simulation engine — NOT a faithful execution-level trading simulator.**
> This system projects per-strategy paper portfolios from AI verdict events. It does not model real execution, market impact, slippage, fees, or true cross-strategy capital competition. Treat outputs as **strategy behavior observations**, not realized trading results.

> **Status:** Spec ready for plan
> **Branch:** `spec/phase-4-backtest`
> **Depends on:** Phase 1 (eval infra) + Phase 2 (verdict + outcomes) + Phase 3 (strategy YAML) — all merged in main

## Goal

Replay historical `EvaluationEvent` rows tagged with each of the 6 Phase 3 strategies, simulating "long-only paper portfolio with $1k per bullish signal, hold for horizon, daily mark-to-market" for each strategy independently. Add SPY buy-and-hold as a 7th baseline.

Output: 7 daily equity curves + per-strategy Sharpe / MaxDD / Cumulative Return / Win Rate, surfaced in a new `/lab/backtest` dashboard.

**System identity** (one-line):
> A reproducible research observatory for strategy-level synthetic PnL analysis under constrained-capital simulation assumptions.

**Primary value:** answers "which Phase 3 strategy would have made money if I followed its bullish verdicts?" — the natural "show me the $$" companion to Phase 2's hit-rate dashboard.

**Secondary value:** lays the foundation for Phase 5+ work (true portfolio coupling, regime conditioning, slippage modeling) by providing daily equity curves and explicit capital-constraint metrics.

## Non-goals (out of scope for v0)

| Out of scope | Why deferred | Future phase |
|---|---|---|
| Long-short trading | Personal investors rarely short; doubles complexity | Phase 5+ |
| Cross-strategy shared capital pool | Requires designing allocator + correlation handling | Phase 5 (True Portfolio Coupling) |
| Real daily bar mark-to-market | Linear interpolation is a known approximation, sufficient for v0 | Phase 4.5 |
| Strategy correlation penalty | Needs daily_equity_curve output first, then design weight | Phase 5 |
| Random-entry baseline | SPY-only baseline is enough for v0 first pass | Phase 5 |
| Slippage / commissions | Personal broker 0-commission is the realistic baseline | Phase 5 |
| Walk-forward validation | Premature with current data volume (Phase 3 just deployed) | Phase 5 |
| Regime conditioning (trend vs choppy) | Needs regime detector first | Phase 6 |
| Strategy DSL + genetic search | Separate research track | Phase 6 |
| Live paper trading via Alpaca/IBKR | Operational complexity | Phase 7 |
| Per-strategy visibility toggle in UI | Not needed for 7 lines, defer | Phase 4.5 |
| QuantStats HTML tear sheet export | Convenience feature | Phase 5 |

## Architecture

```
EvaluationEvent (Phase 2/3 schema, already populated)
   ├─ ticker, event_time, event_price
   ├─ subtype ∈ {bullish, neutral, bearish}
   └─ payload.strategy ∈ {fundamental_value, momentum_breakout, news_event,
                          sector_rotation, oversold_reversal, general}

EvaluationOutcome (Phase 1 nightly job, populated as events mature)
   ├─ horizon_trading_days, horizon_price, horizon_date
   ├─ forward_return
   └─ benchmark_forward_return (SPY return over same horizon)

         ↓ JOIN, filter by strategy + horizon + bullish
         ↓
   Portfolio Simulator (NEW: marketpulse/backtest/simulator.py)
   ├─ For each strategy → independent daily equity loop
   │    OPEN: new bullish events that day (if capital available)
   │    CLOSE: positions whose horizon_date == today
   │    MTM (key): linear interpolation between entry_price and horizon_price
   │    RECORD: daily equity = cash + Σ position_value
   ├─ Capital constraint: max_capital_in_use = $10k (soft cap)
   │    → over-capacity signals skipped, counted in n_capacity_skipped
   └─ SPY buy-and-hold from first_event_time to last_horizon_date
         ↓
   Metrics (NEW: marketpulse/backtest/metrics.py + empyrical-reloaded)
   ├─ Sharpe / Sortino / Calmar (on DAILY return series)
   ├─ Max Drawdown / Cumulative / Annual return
   ├─ Win Rate / Avg Win % / Avg Loss %
   └─ Excess vs SPY (cum_return − spy_cum_return)
         ↓
   /lab/backtest (NEW route + 5 partials)
   ├─ Warning banner: "research-grade · linear MTM approximation"
   ├─ 5 KPI cards
   ├─ Equity curve SVG (7 lines: 6 strategies + SPY)
   ├─ Drawdown curve SVG
   ├─ Strategy leaderboard with Sharpe / MaxDD / Cum Ret / vs SPY / n_trades / skipped
   └─ Filter card (horizon / since_days)
```

No new database tables. No new migration. v0 is pure read-side computation over existing Phase 1-3 data.

## Portfolio Simulator Algorithm

```python
def simulate_strategy_portfolio(
    db: Session,
    *,
    strategy: str,
    horizon: int = 5,                          # trading days
    since: date | None = None,                 # filter event_time >= since
    initial_capital: float = 10_000.0,
    position_size: float = 1_000.0,            # USD per bullish signal
    max_capital_in_use: float = 10_000.0,      # soft cap per strategy
) -> StrategyBacktestResult:
    """
    For each (event, outcome) pair where:
      - event.subtype == "bullish"
      - event.payload["strategy"] == strategy
      - outcome.horizon_trading_days == horizon
      - (since is None) OR (event.event_time >= since)
      - outcome.horizon_price is not None
      - event.event_time.date() < outcome.horizon_date  # causal — defends
        against any DB anomaly where a horizon would resolve before the
        event was recorded. Should always be true by Phase 1 construction;
        we still assert it explicitly to make the temporal invariant
        unbreakable.

    Build a daily equity curve from min(event_time) to max(horizon_date).
    For each trading day d, in this ORDER (matters — avoids subtle
    same-day look-ahead bias):
      a) CLOSE: open positions whose horizon_date == d
         - realized PnL = position_size * (horizon_price / entry_price - 1)
         - cash += position_size + realized_pnl
         - This MUST happen before OPEN so freed capital is available to
           new same-day signals AND new positions don't participate in
           today's MTM (which would be a same-day forward bias).
      b) OPEN: new bullish events with event_time.date() == d
         - if (capital_in_use + position_size) > max_capital_in_use:
             skip, increment n_capacity_skipped
         - else: open position(ticker, entry_price=event_price,
                               entry_date=d, horizon_date=outcome.horizon_date)
         - Newly opened positions are NOT marked-to-market on day d (their
           est_position_value equals position_size at entry by definition).
      c) MTM open positions opened BEFORE today (linear interpolation):
         est_price(d) = entry_price + (horizon_price - entry_price)
                        * trading_days_elapsed / total_horizon_days
         est_position_value = position_size * (est_price / entry_price)
      d) RECORD: equity[d] = cash + Σ position_values
                          (where freshly-opened positions contribute their
                          entry value, older positions contribute their
                          MTM-interpolated value)

    Compute metrics on the daily return series:
      daily_returns = equity.pct_change()
      sharpe = empyrical.sharpe_ratio(daily_returns, annualization=252)
      ...
    """
```

### Critical assumptions

1. **Linear interpolation MTM** — captures intra-horizon drawdown direction but smooths real volatility. Surfaced via `mtm_model = "linear_interpolation_v0"` in result + UI.
2. **Same-ticker positions stack** — 4 simultaneous AAPL longs from 4 consecutive bullish events all hold concurrently. v0 simplification; Phase 5 may add `max_concurrent_per_ticker` constraint.
3. **No fees / no slippage** — personal broker 0-commission baseline.
4. **No cash interest** — cash sits at 0% yield.
5. **Trading-day timeline** — calendar days skip weekends + US market holidays. Approach: iterate the **union of distinct dates** appearing in `event_time.date()` + `horizon_date` across all relevant outcomes. This implicitly respects the trading-day grid because Phase 1's `forward_return.py` already computes horizons via the ticker's yfinance bar index (which skips non-trading days). No new calendar dependency required. (If gaps emerge between events on illiquid tickers, plan can switch to `pandas_market_calendars` — note Phase 1 does NOT use it; that "consistency" rationale is removed from this spec.)

## SPY Baseline

```python
def simulate_spy_buyhold(
    db: Session,
    *,
    first_event_time: datetime,    # align with strategy events
    last_horizon_date: date,       # align with last horizon close
    initial_capital: float = 10_000.0,
) -> StrategyBacktestResult:
    """
    "Buy SPY at first_event_time, hold to last_horizon_date" baseline.

    Data source: EvaluationOutcome.benchmark_forward_return is the SPY return
    over each event's horizon (Phase 1 already populated this). We build the
    SPY equity curve by **linear interpolation on each outcome's horizon
    window**, using the SAME `mtm_model = "linear_interpolation_v0"`
    convention as strategies. This keeps SPY and strategies methodologically
    consistent — both are smoothed approximations of the true daily path.

    Algorithm:
      1. Collect distinct (event_time, horizon_date, benchmark_forward_return)
         tuples from all EvaluationOutcomes in the time window.
      2. For each trading day d from first_event_time to last_horizon_date:
         a. Find SPY's effective return-to-date by interpolating across the
            overlapping outcome windows.
         b. equity[d] = initial_capital * (1 + cumulative_spy_return_to_d)
      3. NOTE: this is an APPROXIMATION. Real daily SPY bars from yfinance
         would be more accurate (Phase 4.5 upgrade).

    Why anchored to first_event_time: avoids lookback bias. If the strategy's
    events span 2025-12-01 → 2026-05-15, SPY runs that same window — NOT a
    hardcoded "from start of Phase 2 history" which would unfairly compare
    different time slices.

    IMPORTANT semantic note: this makes the SPY baseline a **window-aligned**
    benchmark, NOT a full-period universal benchmark. Two backtest runs over
    different time windows produce two different SPY equity curves — they are
    NOT directly comparable as cross-run "vs SPY" deltas. Within a single
    backtest run, all 6 strategies share the same SPY window so cross-strategy
    comparison stays valid. Cross-run comparison requires care; document in
    UI tooltips.
    """
```

Result is rendered as the 7th line (dotted, neutral gray) on the equity curve, and shown in the leaderboard table marked as **baseline** (not ranked).

**Phase 4.5 upgrade path:** swap the interpolation logic for real SPY daily bars (fetched via `DataService.get_history("SPY", ...)`) — only the helper changes, the public `simulate_spy_buyhold()` signature stays.

## `StrategyBacktestResult`

```python
@dataclass(frozen=True)
class StrategyBacktestResult:
    # Identity
    strategy: str                          # "momentum_breakout" or "__spy_buyhold__"
    display_name: str                      # "动量突破" or "SPY 基准"
    horizon: int                           # 5 / 20 / etc; 0 for SPY baseline
    mtm_model: str = "linear_interpolation_v0"   # provenance / disclaimer

    # Trade counts
    n_trades: int                          # bullish events traded
    n_capacity_skipped: int                # signals dropped due to capital cap
    # NOTE: "n_pending" (events whose horizon hasn't matured) is NOT a field
    # of this result. The simulator filters on outcome-present rows only.
    # The /lab UI shows pending-events count separately via the existing
    # Phase 2 EvaluationEvent counter (no need to duplicate in backtest).

    # Performance
    cumulative_return: float               # final_equity / initial_capital − 1
    annual_return: float                   # annualized via 252 trading days
    sharpe: float | None                   # None if n_trades < 5
    sortino: float | None
    max_drawdown: float                    # most negative DD on daily curve
    calmar: float | None                   # annual_return / |max_drawdown|

    # Trade-level
    win_rate: float                        # winning trades / n_trades
    avg_win_pct: float
    avg_loss_pct: float

    # Equity curve for plotting — DOWNSAMPLED to ~120 points before being
    # returned. For windows < 120 trading days, this is the raw daily series;
    # for longer windows, evenly-spaced samples (keeping endpoints + key
    # turning points). Routes pass this directly to templates — no further
    # truncation needed.
    daily_equity_curve: list[tuple[date, float]]

    # Benchmark comparison
    excess_vs_spy: float                   # cumulative_return - spy_cumulative_return

    # ---------- Reserved for Phase 5 (always None in v0) ----------
    # These nullable fields let Phase 5 (True Portfolio Coupling) replay v0
    # data without schema migration. v0 populates None; Phase 5 will compute
    # values for new runs.
    strategy_exposure: float | None = None         # avg gross exposure during run
    capital_bid_score: float | None = None         # priority weight when competing
                                                    # for shared capital pool
                                                    # (was capital_request_signal —
                                                    # renamed: "signal" overloaded
                                                    # with trading signal)
```

## Capital Constraint

**Terminology:** this is a **hard cap with skip-based enforcement** — not a "soft cap". v0 has no rotation, no priority queue, no overflow buffer. The earlier "soft cap" phrasing was misleading.

- `max_capital_in_use = $10_000` (hardcoded v0)
- **Unit:** sum of `position_size` over **open positions** (not their MTM est_position_value). So if 10 positions are open each at $1,000 deployed-capital, `capital_in_use = $10,000` regardless of whether they've appreciated to $1500 each.
  - Rationale: this is "how much cash I committed", not "how much risk I'm carrying". Risk-based caps come in Phase 5.
- If a new bullish event arrives and `capital_in_use + position_size > max`, the signal is **dropped (hard rejection)**:
  - logged via structlog `log.info("backtest_signal_capacity_skipped", strategy=..., ticker=..., date=...)`
  - counted in `n_capacity_skipped` on the result
- v0: NO rotation / NO priority queue / NO reservation. First-come-first-served by event_time order.
- Phase 5 will introduce: signal strength prioritization, rotation (close oldest position to make room for stronger new signal), or true shared-capital allocator. Those mechanics may justify a "soft cap" naming there — but v0 is hard.

**Why this matters:** without a cap, high-signal-density strategies (e.g., a momentum strategy that fires daily) would appear to dwarf low-density strategies (e.g., fundamental_value that fires weekly) purely because of trade frequency, not alpha quality.

## Daily Mark-to-Market: linear interpolation

For an open position with entry on day `d_open` and horizon end on day `d_horizon`:

```
trading_days_elapsed = count_trading_days(d_open, d_current)
total_horizon_days = count_trading_days(d_open, d_horizon)
fraction = trading_days_elapsed / total_horizon_days     # 0 to 1

est_price(d_current) = entry_price + (horizon_price - entry_price) * fraction
est_position_value = position_size * (est_price / entry_price)
```

### Known limitations (documented in UI)

| Limitation | Effect on metrics |
|---|---|
| Real price path is volatile; we draw a straight line | **MaxDD is underestimated** — intra-horizon dips are invisible |
| Volatility on the daily series is smoothed | **Sharpe is overestimated** — denominator (std of daily returns) compressed |
| Convex risk exposure invisible | Trend / breakout strategies' tail risk looks too benign |

These are **expected** for a linear MTM model and **must be surfaced in the UI**. Phase 4.5 can swap in real daily bars from yfinance to fix this without changing the simulator architecture (only the `est_price()` function).

## Metrics (empyrical-reloaded)

| Metric | Formula | Notes |
|---|---|---|
| Sharpe | `mean(daily_returns) / std(daily_returns) * sqrt(252)` | empyrical default, risk-free=0 |
| Sortino | downside-only stdev | empyrical default |
| Max Drawdown | `min(equity / cumulative_max(equity) - 1)` | on the daily series |
| Calmar | `annual_return / abs(max_drawdown)` | None if MaxDD = 0 |
| Cumulative Return | `equity[-1] / equity[0] - 1` | over full window |
| Annual Return | `(1 + cum_ret) ** (252 / n_trading_days) - 1` | |
| Win Rate | winning trades / n_trades | trade-level, not daily |
| Avg Win % | mean of positive trade returns | |
| Avg Loss % | mean of negative trade returns | |

Metrics with `n_trades < 5` return `None` (rendered as `—` in UI) to avoid noisy outputs in early days.

## File Structure

```
marketpulse/
├── backtest/                          NEW module
│   ├── __init__.py                    re-export StrategyBacktestResult + run_all_backtests
│   ├── types.py                       StrategyBacktestResult dataclass
│   ├── simulator.py                   simulate_strategy_portfolio + simulate_spy_buyhold
│   ├── metrics.py                     empyrical wrappers + format helpers
│   ├── queries.py                     DB queries (events, outcomes, SPY prices)
│   └── trading_calendar.py            trading-day arithmetic (count_trading_days)
└── web/
    ├── routes/
    │   └── backtest.py                NEW: /lab/backtest GET route
    └── templates/
        ├── lab_backtest.html          NEW page shell
        └── partials/
            ├── backtest_hero.html              NEW
            ├── backtest_kpi_strip.html         NEW (5 cards)
            ├── backtest_equity_chart.html      NEW (SVG 7 lines)
            ├── backtest_drawdown_chart.html    NEW (SVG)
            └── backtest_strategy_table.html    NEW (leaderboard)

tests/
├── unit/
│   ├── test_backtest_simulator.py     simulator math (entry/exit/MTM/cap)
│   ├── test_backtest_metrics.py       empyrical wrappers
│   └── test_trading_calendar.py       trading-day arithmetic
├── integration/
│   └── test_backtest_queries.py       full DB → result pipeline
└── web/
    └── test_lab_backtest.py           route + partial integration
```

New external dependency: `empyrical-reloaded` (pure Python, ~5 functions used).

No new database tables, no Alembic migration.

## UI Spec: `/lab/backtest`

### Page layout (3-column at 2400px max-width, same as `/lab/ai-track`)

```
┌─ Warning banner ─────────────────────────────────────────────┐
│ ⓘ Research-grade simulation. Linear-interpolation MTM        │
│   (mtm_model=linear_interpolation_v0). MaxDD may underestimate│
│   real risk; Sharpe may overestimate.                         │
├──────────────────────────────────────────────────────────────┤
│ HERO: 实验室 · 组合回测 + intro paragraph                     │
├──────────────────────────────────────────────────────────────┤
│ KPI STRIP (5 cards):                                          │
│ [Best Strategy] [Best Sharpe] [Best Cum Ret]                 │
│ [Worst MaxDD] [vs SPY (avg excess)]                          │
├─────────────────────────────────┬────────────────────────────┤
│ MAIN (760px)                    │ RAIL (1fr)                 │
│ ┌─ Equity Curve SVG ─────────┐  │ ┌─ Filter Card ─────────┐ │
│ │ 6 strategies + SPY dotted  │  │ │ Horizon: 1/5/20/60    │ │
│ │ Hover tooltip per day      │  │ │ Since: 30/90/180/all  │ │
│ └────────────────────────────┘  │ └───────────────────────┘ │
│ ┌─ Drawdown SVG ─────────────┐  │ ┌─ Strategy Table ──────┐ │
│ │ Stacked or split DD lines  │  │ │ Sharpe MaxDD CumRet   │ │
│ └────────────────────────────┘  │ │ vsSPY n_trades        │ │
│                                  │ │ skipped               │ │
│                                  │ └───────────────────────┘ │
└─────────────────────────────────┴────────────────────────────┘
```

### Strategy leaderboard (rail table)

| Strategy | Sharpe | MaxDD | Cum Ret | vs SPY | n_trades | skipped |
|---|---|---|---|---|---|---|
| momentum_breakout | 1.42 | -8.3% | +12.4% | +4.2% | 47 | 3 |
| fundamental_value | 0.91 | -6.1% | +5.8% | -2.4% | 23 | 0 |
| **SPY 基准** | 0.82 | -7.5% | **+8.2%** | (baseline) | (—) | (—) |
| ... |

- Sort by Sharpe desc (SPY shown but unsorted, marked as baseline)
- n_trades < 5 → metrics show "—" + "积累中" chip
- skipped > 0 → chip ⚠ to show capacity hit
- Each row links to `/lab/ai-track?strategy=<name>` for hit-rate cross-reference

### Equity curve SVG

- viewBox: `0 0 800 280`
- 7 polylines: 6 strategies in distinct colors (NineScrolls palette) + SPY in `--ns-on-surface-variant` dotted
- Y-axis: portfolio value (10_000 = initial); log-scale toggle deferred to Phase 4.5
- X-axis: dates (sample sparsely, e.g., monthly labels)

### Drawdown SVG

- Same X-axis as equity
- 7 polylines descending from 0 (or 0% baseline at top)
- Highlight max drawdown period per strategy with annotation (Phase 4.5; v0 plain)

### Filter card

Same shape as `/lab/ai-track`:
- Horizon: chip group, 1d / 5d / 20d / 60d (default 5d)
- Since: 30d / 90d / 180d / all (default 90d)
- No Strategy filter (all 6 always shown)
- No Source filter (`stock_analysis` only — recap doesn't backtest)

URL: `/lab/backtest?horizon=5&since_days=90`

### Cross-link with `/lab/ai-track`

**v0:** plain navigation, no tab strip. Top of `/lab/backtest` shows a small back-link "← Hit Rate"; top of `/lab/ai-track` is **NOT modified** (avoids Phase 3 scope creep).

The page-to-page navigation is via the existing nav bar entries (Phase 3 already has `/lab/ai-track` in the nav). Plan adds `/lab/backtest` as another entry.

**Phase 4.5 candidate:** unified tab strip across both lab pages with shared filter state via query string. Defers because it requires modifying the Phase 3 page header.

## Edge Cases

| Scenario | Handling |
|---|---|
| Strategy has 0 bullish events in window | Equity curve flat at initial_capital; n_trades = 0; metrics = "—" |
| All events have unmatured outcomes | n_trades = 0; equity curve = [(today, initial_capital)]; UI shows "等待 outcome 成熟" hint (queries EvaluationEvent directly for the count, not via result) |
| Event with no SPY benchmark return in outcome | Use 0 as benchmark_return fallback; log warning |
| `since_days=all` with thousands of events | Computation should stay under 1s (pure Python loops over <10k events) |
| Strategy YAML deleted but events still in DB | Treat as orphan; render with raw strategy name, no display_name |
| `max_capital_in_use` reached on first event of a strategy | Skip + counter; not an error |
| Two events same ticker same day same strategy | Stack — both open as separate positions |
| Cache HIT does NOT record a new event | Phase 2 invariant preserved (not changed by Phase 4) |
| Concurrent web requests to `/lab/backtest` | Read-only, no shared state, no concern |

## Telemetry / Observability

| Signal | Where | Purpose |
|---|---|---|
| `backtest_signal_capacity_skipped` (info) | inside simulator, per skipped signal | Operator can grep to confirm capital cap is hitting |
| `backtest_run_complete` (info, with strategy + n_trades + sharpe) | end of `simulate_strategy_portfolio()` | Detect anomalous results (Sharpe > 5 or < -5 = bug suspicion) |
| `backtest_outcome_missing` (warning) | if joined outcome row has NULL horizon_price | Should never happen if Phase 1 nightly job ran |

No new dashboard required — counters flow through existing structlog.

## Open Decisions (locked for v0)

These are the brainstorming outcomes. Implementation should NOT revisit without spec amendment.

1. **Long-only model.** Bearish / neutral signals are ignored, NOT used to open shorts.
2. **6 independent portfolios + SPY baseline = 7 lines.** No shared capital pool, no cross-strategy correlation handling.
3. **Linear interpolation MTM.** Real daily bars deferred to Phase 4.5.
4. **Capital cap = $10,000 per strategy, hardcoded — HARD cap with skip-based enforcement.** Not a soft cap; over-budget signals are dropped. Phase 5 may introduce soft-cap mechanics with rotation.
5. **Position size = $1,000 fixed.** No volatility weighting, no signal-strength sizing.
6. **Same-ticker positions stack.** No `max_concurrent_per_ticker` constraint.
7. **SPY benchmark anchored to `first_event_time`** (window-aligned benchmark, NOT a full-period universal benchmark). Avoids lookback bias within a single backtest run. Cross-run "vs SPY" deltas need care since each run uses its own SPY window.
8. **Metrics computed on DAILY return series.** Not on trade returns (avoids irregular-spacing Sharpe bias).
9. **`mtm_model` field surfaced in API + UI.** Provenance / disclaimer.
10. **Reserved Phase 5 fields** (`strategy_exposure`, `capital_bid_score`) added as `None` defaults to enable retroactive replay without schema migration. (Field originally proposed as `capital_request_signal` — renamed to avoid "signal" overloading with "trading signal".)
11. **No Alembic migration.** Pure read-side over Phase 1-3 schema.
12. **Warning banner mandatory.** UI explicitly labels v0 as research-grade, not execution-level.
13. **Strategy filter NOT in v0 filter card.** All 6 strategies always shown (head-to-head is the point).
14. **Recap-sourced events excluded** — backtest is `source = "stock_analysis"` only (recap events are commentary, not actionable verdicts).
15. **Simulator daily loop ORDER: CLOSE → OPEN → MTM → RECORD.** Strict. CLOSE first so freed capital is available to same-day new signals; new positions do NOT participate in same-day MTM (would be forward bias).
16. **JOIN must include causal constraint** `event.event_time.date() < outcome.horizon_date`. Defends against any DB anomaly with reversed timestamps.

## Self-Review Notes

(Per brainstorming skill's spec self-review checklist.)

**Placeholder scan:** None. Every section has concrete values or explicit "deferred to Phase X" labels.

**Internal consistency:**
- File structure (§ File Structure) lists 4 backtest module files; algorithm (§ Portfolio Simulator) references all 4. ✓
- `StrategyBacktestResult` fields used consistently across § StrategyBacktestResult, § Metrics, § UI Spec, § Capital Constraint. ✓
- `mtm_model = "linear_interpolation_v0"` defined once (§ Daily MTM), referenced in § StrategyBacktestResult, § UI Spec warning, § Open Decisions #9. ✓
- 7 lines (6 strategies + SPY) used consistently throughout. ✓
- SPY anchoring rule defined once (§ SPY Baseline) and reinforced in § Open Decisions #7. ✓

**Scope check:** v0 is bounded — single page, 7 independent simulations, no shared state, no DB writes, ~1 week impl. Plan can be written from this. ✓

**Ambiguity check:**
- Capital constraint behavior (skip vs rotate) explicit (skip, FCFS by event_time) ✓
- Same-ticker stacking explicit (stack, no cap) ✓
- Metrics formula explicit (daily series for Sharpe, trade-level for win_rate) ✓
- Phase 5 reserved fields specified as nullable defaults so v0 implementations consistent ✓
- Cross-link with `/lab/ai-track` specified at tab + table-link granularity ✓

**One open ambiguity** (intentional, for plan to resolve):
- **Trading calendar source.** Default: derive the trading-day grid from the union of `event_time.date()` + `horizon_date` values already in the DB (Phase 1 computed these via yfinance bar-index, no separate calendar library). Plan can swap in `pandas_market_calendars` if gaps emerge on illiquid tickers; not required for v0.

## Implementation Pointers

- `marketpulse/evaluation/outcomes.py` — Phase 1 module that computes `horizon_date` from `event_time + horizon trading days`. Reuse the trading-calendar helper from here.
- `marketpulse/evaluation/scoring.py` — Phase 2 module with 4 read-only query functions. Mirror its pattern for `marketpulse/backtest/queries.py`.
- `marketpulse/strategies/loader.py` — Phase 3 module with cached `load_strategies()`. Used to resolve `display_name` for UI.
- `marketpulse/web/routes/lab.py` — Phase 3 route with `_qs_from_filters` helper. Mirror its pattern for `marketpulse/web/routes/backtest.py`.
- `marketpulse/db/models.py:EvaluationOutcome` — already has `benchmark_forward_return` (SPY) per outcome. Use to compute SPY equity curve aligned to strategy events.
- `marketpulse/web/templates/partials/ai_track_*.html` — Phase 3 partials. Mirror their structure for backtest partials.
