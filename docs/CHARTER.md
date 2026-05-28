# MarketPulse Charter

**Date:** 2026-05-28
**Replaces:** the 4-pillar vision (A/B/C/D) in `docs/superpowers/specs/2026-05-10-marketpulse-design.md` § Roadmap Context
**Status:** Active

---

## Mission

**MarketPulse is a personal quantitative paper-trading laboratory.**

Phase 5/6/7's infrastructure is the main product. Recap + AI analysis (the original v1 C+D pillars) remain shipped utilities but are NOT the strategic focus anymore. The single question driving every future decision is:

> Does this system, running on paper money, beat SPY?

If yes, eventually it becomes a real-money trading system. If no, it gets simplified or shut down. Anything that does not measurably contribute to answering that question is not worked on.

---

## North-star metric

**`paper_portfolio_excess_return_vs_spy_90d`**

- Definition: trailing-90-trading-day cumulative total return of the live paper portfolio (cash + MTM positions) MINUS trailing-90-trading-day cumulative total return of SPY over the same window.
- Initial floor: ≥ 0% (the system at minimum does not lose to passive SPY over 90d)
- Target: ≥ 5% (cumulative over 90d — NOT annualized)
- Computed: from `paper_position` entry/exit prices + `paper_cash_ledger` + `price_cache[SPY]` close
- Cadence: recomputed daily after market close; surfaced on a new `/lab/portfolio-vs-spy` route (see priority #2 of the next 30 days)

### Why 90d and not 30d

- Trade frequency is low (daily tick, ~1 order/day at peak); 30d window is noise-dominated.
- 30d swings can be ±3–4% from a single bad/good entry, which is louder than the alpha signal.
- 90d gives the allocator + caps enough turnover for the structural choices (rolling Sharpe, sector caps, contribution adjustment) to express themselves.
- The cost is delayed feedback; this is acceptable because we are NOT optimizing the system in tight loops — we are validating its existence value.

### Why excess return and not Sharpe

- Sharpe needs ≥ 20 observations for stable estimation; we have 3 trades total.
- Excess return is direction-of-truth at any sample size.
- Sharpe and IR become secondary metrics once N ≥ 30 trades.

### Secondary diagnostic metrics (review weekly)

| Metric | Question it answers |
|---|---|
| `ai_verdict_hit_rate_h5_n_total` | Is AI verdict signal still positive? |
| `orders_placed_per_day_5d_avg` | Is the system actually trading? |
| `order_rejection_rate_5d` | Are gates blocking too much (alarm > 70%)? |
| `tick_success_rate_5d` | Is `paper_trading_tick` running cleanly? |
| `paper_position_count_open` | Concentration check |
| `sector_exposure_max_pct` | Diversification check |

---

## What is alive (under this charter)

| Track | Why it survives |
|---|---|
| **Phase 5 backtest engine** | Sandbox for strategy iteration before paper deploy |
| **Phase 6a–c paper trading** | The thing being measured |
| **Phase 6b risk gates** | Without gates, paper P&L is not credible — we'd just be testing whether luck > rules |
| **Phase 6g observability + recap push** | Operational visibility into north-star metric |
| **Phase 7a–c broker reconciliation** | Pre-requisite for eventual real-money transition; also a paper-truth integrity check |
| **Phase 5e telemetry (effective_allocation, rank_drift_from_signal)** | Gets a consumer for the first time — feeds weekly diagnostic review |
| **Strategy YAML system (Phase 3)** | Where new alphas land |
| **Daily recap (C-pillar)** | Surfaces AI commentary; remains useful research artifact |
| **AI analysis (D-pillar)** | Feeds AI verdict events into evaluation pipeline |

## What is deprecated (under this charter)

Marked archived; no new work, existing code may stay until next cleanup.

| Item | Source | Why deprecated |
|---|---|---|
| **Pillar A real-time alerts via WebSocket / streaming** | v1 2026-05-10 § Roadmap | Never built beyond 5-min cron; not on the path to north-star |
| **Pillar B intraday decision support** | v1 2026-05-10 § Roadmap | Daily tick is not intraday; would require new data layer |
| **Phase 6d RealtimeExecutionEngine** | 2026-05-21 umbrella § 2 (STRETCH) | Specced one year ago, no work, not on critical path |
| **Phase 6e ShadowPoolOptimizer** | 2026-05-21 umbrella § 2 (STRETCH) | Telemetry stays as forward-compat; optimizer itself is parked |
| **Phase 7d–7h reconciliation deepening** | informal roadmap | Will re-evaluate when paper data > 60 trades; currently 3 |
| **Real-money / live trading (`MP_IBKR_ALLOW_LIVE=true`)** | Phase 7b safety brake | Locked behind north-star ≥ 5% for 90 consecutive days |
| **Multi-user, mobile native apps, subscriptions** | v1 § Out of Scope | Unchanged |

## What is conditional (re-evaluate under charter)

- **Phase 8a ML feature snapshots** — spec drafted 2026-05-28 but NOT yet approved. Under this charter the question is: will 15 structured features in the analyze prompt measurably improve `ai_verdict_hit_rate_h5`? Decision: SHIP 8a only with a paired diagnostic comparison (verdicts under v4 vs v5 prompt on the same ticker-day, retrospectively or via shadow mode). The pass/fail thresholds and review window are EXPERIMENT PARAMETERS belonging in the 8a plan, NOT this charter.
- **Phase 8b ML predictions** — gated on 8a's diagnostic comparison showing statistically meaningful improvement. If Phase 8a shows no statistically meaningful improvement in `ai_verdict_hit_rate_h5` after sufficient sample accumulation, Phase 8b is abandoned and 8a is frozen. "Statistically meaningful" and "sufficient sample" are 8a-plan-level decisions.
- **6e ShadowPoolOptimizer** — gated on the north-star metric stagnating near zero. If `paper_portfolio_excess_return_vs_spy_90d` stays in [-2%, +2%] over a sustained period, the optimizer becomes the next priority (the heuristic-to-optimal residual is then a candidate for non-trivial alpha). The exact "sustained period" is a re-evaluation parameter, not charter-locked.

---

## Operational unlock conditions

### To exit "paper-only" and unlock live trading

ALL of the following must hold over the **rolling 90-trading-day window**. Each metric is the 90-trading-day-window measurement itself; no "consecutive days" stacking on top.

1. `paper_portfolio_excess_return_vs_spy_90d` ≥ +5% (the north-star itself, sustained)
2. `paper_max_drawdown_90d` ≤ 15%
3. `tick_success_rate_90d` ≥ 95%
4. `order_rejection_rate_90d` < 30%
5. **DB backup strategy in place** (currently NOT — see operational gaps below)
6. **Reconciliation pass rate** (Phase 7c paper vs broker diff) ≥ 99%

Until these hold, `MP_IBKR_ALLOW_LIVE` stays `false` regardless of broker-account configuration. Phase 7b's DU* whitelist remains in effect as defense-in-depth.

---

## Top 3 priorities for next 30 days

1. **DB backup + Charter-mandated SLI dashboard**(operational floor — cannot run a quant system without it)
   - Cron `sqlite3 .backup` to NAS sibling path, 7-day rotation
   - One new endpoint `/health/charter` exposing the 7 secondary diagnostic metrics
   - One scheduled weekly `charter_review` job that writes a markdown report into `/recaps`

2. **`/lab/portfolio-vs-spy` route + the north-star metric itself**(observability before optimization)
   - Compute rolling 90-trading-day cumulative total return on paper portfolio
   - Compare against SPY same-window cumulative total return
   - Surface as line chart + delta number
   - Updates daily after recap

3. **Decide Phase 8a (ML feature snapshots) under this charter**
   - Re-read the 2026-05-28 spec with the new question: "will this measurably improve `ai_verdict_hit_rate_h5`?"
   - If yes (or arguably yes), ship with a paired A/B diagnostic. The actual measurement threshold and review window are 8a-plan parameters, not charter-locked.
   - If no, archive the spec and pick a different lever

Anything else in the backlog (chart fixes, old branches, optimizer brainstorm, etc.) waits.

---

## Operational gaps that block the unlock conditions

These are not "nice to have" — they are unconditionally required before real-money trading is even considerable.

1. **No DB backup.** Single point of failure. Container OOM = full loss of trade history + AI evaluations + paper-trading state.
2. **No SLI/SLO instrumentation.** The system has silently produced zero orders for stretches (observed 2026-05-26 investigation). Need automatic alerts on `tick_success_rate < 90%`.
3. **No prompt-version A/B comparison.** Currently bump v3 → v4 → v5 with zero data on whether prompt changes improve verdicts.
4. **Secrets in plain `.env`.** `IBKR_FLEX_TOKEN`, `ANTHROPIC_API_KEY` not in a vault.
5. **No deployment versioning.** Cannot answer "what's on prod right now" without `git log`.
6. **`/data/marketpulse.db` is the only canonical store.** No replica, no PITR, no scheduled snapshot.

Items 1, 2, 6 are the hard floor — every other decision is conditional on these.

---

## Reference architecture (as-built, 2026-05-28)

The current code matches these specs. Treat them as the architectural baseline; this charter sets direction on top of them, it does not redesign them.

- `docs/superpowers/specs/2026-05-10-marketpulse-design.md` — v1 foundation
- `docs/superpowers/specs/2026-05-18-phase-3-strategy-yaml-design.md` — strategy YAML system
- `docs/superpowers/specs/2026-05-20-phase-4-backtest-engine-design.md` — backtest engine (Phase 4)
- `docs/superpowers/specs/2026-05-20-phase-5a-shared-capital-pool-design.md` — shared pool
- `docs/superpowers/specs/2026-05-20-phase-5b-dynamic-position-sizing-design.md` — position sizing
- `docs/superpowers/specs/2026-05-20-phase-5c-sector-correlation-caps-design.md` — sector + corr caps
- `docs/superpowers/specs/2026-05-20-phase-5d-contribution-adjusted-sharpe-design.md` — contribution Sharpe
- `docs/superpowers/specs/2026-05-20-phase-5e-tech-debt-sizing-override-design.md` — 5e tech-debt + telemetry
- `docs/superpowers/specs/2026-05-21-phase-6-umbrella-design.md` — Phase 6 umbrella
- `docs/superpowers/specs/2026-05-21-phase-6a-paper-trading-foundation-design.md` — paper trading foundation
- `docs/superpowers/specs/2026-05-21-phase-6b-risk-gates-design.md` — risk gates
- `docs/superpowers/specs/2026-05-22-phase-6b-plus-paper-pnl-realization-design.md` — P&L realization

Phase 7a–c specs / plans exist but are scattered across PR descriptions; consolidation into one Phase 7 umbrella is a backlog item, not urgent.

### Conditional / pending — not part of as-built baseline

- `docs/superpowers/specs/2026-05-28-phase-8a-ml-feature-snapshots-design.md` — drafted 2026-05-28, AWAITING decision under this charter. The architecture baseline above does NOT yet include this work.

---

## Success definition

MarketPulse succeeds if it can:
- run unattended for months without operator intervention,
- generate measurable paper alpha versus SPY over a rolling 90-trading-day window,
- and produce enough operational confidence (reconciliation pass rate, audit trail completeness, kill-switch correctness) to justify eventual limited-capital live trading.

Otherwise it should be simplified rather than expanded. Adding more strategies, more models, more optimizers does NOT fix a system that cannot consistently survive its own scheduler.

---

## Revision discipline

This charter is a living document. Update it when:
- A north-star metric threshold is met or persistently missed
- A "conditional" item is decided (8a / 8b / 6e)
- A "deprecated" item is reactivated (likely never; if it happens, document why)
- A new track is added (must justify why it serves the north-star)

Each revision keeps the old version visible via `git log docs/CHARTER.md`. The charter is never silently rewritten.

---

## Sign-off

Project owner approval required to commit this charter and enact its locks.
