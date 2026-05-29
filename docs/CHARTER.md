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

- **Phase 8a ML feature snapshots — STATUS: PARKED (decided 2026-05-29).** The spec is technically sound (correct cache key, sane feature catalog, no training, no ta-lib dependency, no allocator change, simple rollback) — reviewed standalone it would approve. It is parked on **timing**, not design: the charter's ship-condition (a paired v4-vs-v5 diagnostic proving measurable improvement in `ai_verdict_hit_rate_h5`) is unsatisfiable today, and shipping without it would repeat operational gap #3.
  - Blocking facts at decision time: **13 resolved h5 outcomes** total (h1: 53), 35 analyses, 9 tickers — far below any statistical power; the 8a spec itself defers the A/B diagnostic to 8b (§9); and 8a enriches `analyze()` (D-pillar advisor), not the allocator that actually produces the north-star.
  - **Unpark when ALL hold:** (1) ≥ 30 resolved h5 outcomes available **per A/B arm**; (2) ≥ 30 valid north-star trading days accumulated (`paper_nav_snapshot`); (3) a shadow-mode A/B design (v4 vs v5 run simultaneously on the same ticker-day, both recorded) is approved. At unpark, ship **8a + 8b together** as one shadow experiment so a single run answers "do ML features add value?" with real data — not 8a-then-8b sequentially.
  - Until then the bottleneck is **data generation, not features** (see task #57 / priority re-order below).
- **Phase 8b ML predictions** — gated on the 8a+8b shadow experiment (above) showing statistically meaningful improvement in `ai_verdict_hit_rate_h5`. If no meaningful improvement after sufficient sample accumulation, 8b is abandoned and 8a stays parked/frozen. "Statistically meaningful" and "sufficient sample" are experiment-plan decisions made at unpark.
- **6e ShadowPoolOptimizer** — gated on the north-star metric stagnating near zero. If `paper_portfolio_excess_return_vs_spy_90d` stays in [-2%, +2%] over a sustained period, the optimizer becomes the next priority (the heuristic-to-optimal residual is then a candidate for non-trivial alpha). The exact "sustained period" is a re-evaluation parameter, not charter-locked.

### Future research-platform options — PARKED (evaluated 2026-05-29)

**Status:** Parked — not under consideration. Not a permanent rejection; "not now, show me the trigger."

**Options reviewed:** Qlib (MS, ML/factor research), QuantConnect Lean (full backtest+live platform), Backtrader (Python backtest).

**Verdict: No adoption.** Build-vs-buy is the wrong axis right now — the binding constraint is **evidence of edge, not research infrastructure**. Specifics:
- The "save 6–12 months" framing is a greenfield claim; this system is 7 phases deep (paper engine, evaluation framework, IBKR sync, dashboard all exist). The marginal move would be *integration/migration*, which **adds** time.
- Qlib solves factor discovery / alpha mining — a capability whose *absence is not the current bottleneck*. Adding it now is complexity on an unvalidated system (the Charter anti-pattern).
- Backtrader would *replace* the working, tested Phase 4/5 engine (re-platforming) and is effectively unmaintained since ~2021 — a downgrade.
- Lean migration: re-platforming working code; rejected.
- The advice "don't rewrite backtest/metrics/indicators from scratch" is already satisfied (Phase 4/5 uses `empyrical`); it is not a reason to add anything.

**Reconsideration trigger — ALL must hold:**
1. North-star has ≥ 30 valid trading days (`paper_nav_snapshot`).
2. The measurement loop is stable and trusted.
3. The existing strategy set + allocator are validated against SPY.
4. Performance sits in the Charter stagnation band ([-2%, +2%], per 6e) — i.e. a real research bottleneck, not an infra gap.
5. A concrete research bottleneck has been named.

Until then: P0/P1 unchanged; **no migration, integration, or evaluation work is authorized.** Anyone re-proposing Qlib / Lean / Backtrader can be answered with: *evaluated 2026-05-29, verdict no adoption, trigger required.*

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

**Data-trust precondition (added 2026-05-29):** the excess-return unlock condition (#1) must NOT be evaluated from NAV snapshots unless **price-freshness telemetry is available and shows acceptable staleness**. Since #138 the snapshot marks positions/SPY to the *last available close* (`<= trading_date`) to survive the ~1-day `price_cache` lag — a correct EOD-NAV convention, but it means a NAV can be "complete" while priced from stale closes. Without the P2 freshness telemetry (below), a stale-but-complete NAV could silently flatter `excess_return`. P2 must ship and be reviewed before #1 is trusted for the unlock decision.

Until these hold, `MP_IBKR_ALLOW_LIVE` stays `false` regardless of broker-account configuration. Phase 7b's DU* whitelist remains in effect as defense-in-depth.

---

## Priorities

### Original 30-day Top 3 — all resolved (2026-05-29)

1. **DB backup + Charter-mandated SLI dashboard** — ✅ DONE. PR1 (`sqlite3 .backup` + 7-day rotation), PR2 (`/lab/charter-metrics` JSON), PR3b (weekly `charter_review` markdown into `/data/recaps/charter/`).
2. **`/lab/portfolio-vs-spy` route + north-star metric** — ✅ DONE. PR3a (NAV snapshot semantic layer) + PR4 (visualization). Hardened post-ship: #133 (snapshots now persist), #135 (degenerate rows can't poison the inception anchor).
3. **Decide Phase 8a under this charter** — ✅ DECIDED: **PARKED** (see "What is conditional" above). Not shippable under the charter's A/B requirement with 13 h5 outcomes.

### Re-ordered priorities (post-2026-05-29 — development philosophy: measure → find bottleneck → prove a change addresses it → only then add features)

The bottleneck is **data**, not features. Ranked:

- **P0 — Let the north-star run.** The snapshot pipeline was only fixed today (#133/#135); `paper_nav_snapshot` has ~0 valid days. Accumulate ≥ 30 trading days of valid snapshots. This is the prerequisite for *everything* — the charter's core question ("does it beat SPY?") is currently **Unknown** because coverage ≈ 0%.
- **P1 — Task #57: drive `/stock` analyses to populate evaluation data.** At ~1 analysis/day, 30/60/120 outcomes take 1/2/4+ months. Auto-analyzing a wider watchlist nightly raises throughput an order of magnitude (13 → hundreds of outcomes), which is the only thing that makes a future 8a/8b A/B statistically possible.
- **P2 — 8a + 8b shadow experiment** — only after P0 + P1 satisfy the unpark trigger. Run v4 and v5 prompts simultaneously and answer the ML-features question with real data instead of guessing about RSI/MACD.

Anything else in the backlog (chart fixes, old branches, optimizer brainstorm, etc.) waits.

### NAV snapshot follow-ups (after #138 — deferred, not on the active P0/P1/P2 plane)

#138 made the snapshot *correct* (mark-to-last-available-close, surviving the ~1-day `price_cache` lag). Two deferred items remain, distinct from the priorities above:

- **F1 — Move the NAV snapshot to a post-close job.** Today it piggybacks the *in-session* paper tick (which must run during market hours to place orders); aligning it with EOD-NAV semantics means **decoupling it into its own ~17:00 ET job**, not just a cron change. Not required for correctness after #138 — a timing improvement only.
- **F2 — Price-freshness telemetry (trust/decision-readiness requirement).** Capture per snapshot: `price_asof_date`, `spy_asof_date`, `max_price_age_days`, `stale_price_count`; surface a "⚠ marked to previous close (N-day lag)" banner on the north-star / charter pages. **Gating:** before the north-star is used for the live-trading unlock decision, F2 must be present and reviewed (see the data-trust precondition under "Operational unlock conditions"). F2 is a *trust* requirement, not just observability — a stale-but-"complete" NAV could flatter `excess_return`.

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

- `docs/superpowers/specs/2026-05-28-phase-8a-ml-feature-snapshots-design.md` — drafted 2026-05-28; **PARKED 2026-05-29** (see "What is conditional"). Not implemented; not part of the as-built baseline. Unpark trigger is charter-locked above.

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
