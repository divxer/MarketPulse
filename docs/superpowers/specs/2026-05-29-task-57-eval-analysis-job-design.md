# Task #57 — Nightly Evaluation-Analysis Job — Design

**Date:** 2026-05-29
**Charter priority:** P1 (make AI-verdict hit-rate statistically meaningful)
**Status:** Design approved (section-by-section); ready for implementation planning.

---

## Problem

`/lab/ai-track` is technically correct, but its hit-rate is computed over only
~13 h5 outcomes — far too few to be statistically meaningful. The bottleneck is
**data volume**, not the metric. AI verdicts are only recorded when a human opens
`/stock/<ticker>` and triggers `analyze()` (the sole caller is
`routes/stock.py:346`). To grow evaluation data from ~13 outcomes to hundreds,
the system must generate verdicts automatically, every trading day, over a
meaningful universe — **without** affecting the allocator and **without**
amplifying cost or noise.

This job is **evaluation-only**: it writes `AiAnalysis` + `EvaluationEvent`
(which `outcomes.py` later scores at horizons `[1, 5, 20, 60]`) and nothing else.
It never launches the allocator, creates orders, or mutates the watchlist.

## Goal

A nightly, post-close, independent scheduler job that analyzes
**Watchlist ∪ current open paper holdings** once per trading day, capped by a
configurable per-day fresh-LLM-call budget, recording a coverage summary readable
at `/health/scheduler`.

## Non-Goals

- No allocator / order / watchlist side effects (hard invariant, CI-enforced).
- No new horizons. `DEFAULT_HORIZONS = [1, 5, 20, 60]` is already computed; the
  page shows h1/h5. Unchanged.
- No backfill of historical verdicts. Forward-only accumulation.
- Not piggybacked on the recap job (recap runs mid-session at 16:30/17:00 UTC).

---

## Locked Decisions (from brainstorming)

| # | Decision |
|---|----------|
| Universe | `watchlist ∪ current open paper positions`, deduped. Normalize: uppercase, strip, dedupe, **sort ASC** (deterministic skip set when cap hits). |
| Open-holdings definition | Reuse `PaperPositionRepository.open_positions_snapshot()` (`status == "OPEN"`) — the existing canonical "current open" helper. Avoids a status-vs-`exit_fill_id` semantic split. `status == "OPEN"` is correct here because this is a *current* universe, not the historical reconstruction path. |
| Cadence | Once/day, **Mon–Fri 21:00 UTC** (~17:00 ET EDT), post-close, independent job. Each verdict's as-of = that day's completed close (consistent with the north-star's EOD basis). |
| Cost cap | `AI_EVAL_MAX_CALLS_PER_DAY = max fresh LLM analyses` (default 60, configurable). Counts **fresh calls only**. |
| Eval-only | Writes `AiAnalysis` + `EvaluationEvent` only. Never allocator/orders/watchlist. Double-enforced: (a) only side-effecting call is `ai.analyze()`; (b) architecture-guard test. |
| Horizons | Unchanged `[1, 5, 20, 60]`; page shows h1/h5. |
| Enablement | `AI_EVAL_ENABLED = false` by default. Enable = env flip + restart. |

### Cap semantics (locked)

```
cached=True  → cache_hits += 1, does NOT consume cap
cached=False → analyzed_fresh += 1, consumes cap
error        → errors += 1, does NOT consume cap, session.rollback()
```

- Cap reached → stop loop, `skipped_cap = remaining tickers`, `cap_hit = True`,
  WARN, return success.
- `max_calls <= 0` → analyze none, `skipped_cap = universe_size`, `cap_hit = True`,
  WARN, return success (a config error can never trigger an unbounded run).
- Same-day re-run (all cached) → `fresh=0, cache_hits=N, skipped_cap=0,
  cap_hit=False` — the correct, near-zero-cost behavior.

`cap = successful fresh LLM call budget`. Cache hits are nearly free; errors are
not successful calls. Neither consumes the budget.

---

## Architecture

```
marketpulse/ai/eval_analysis.py            (NEW — testable service/orchestration layer)
   build_eval_universe(session) -> list[str]
   @dataclass(frozen=True) EvalAnalysisSummary
   run_eval_analysis(session, *, ai, universe, max_calls) -> EvalAnalysisSummary

marketpulse/scheduler/eval_state.py        (NEW — mirrors scheduler/state.py)
   record_eval_run_summary(session, summary: dict) -> None     # own commit
   get_eval_last_run_summary(session) -> dict | None

marketpulse/scheduler/jobs.py              (MODIFY)
   run_eval_analysis_job()                  # composition root + gate
   register_jobs(...)                       # add cron 21:00 UTC mon-fri

marketpulse/config.py                      (MODIFY)
   ai_eval_enabled / ai_eval_max_calls_per_day / ai_eval_hour / ai_eval_minute

tests/ai/test_eval_analysis.py             (NEW)
tests/scheduler/test_eval_state.py         (NEW)
tests/scheduler/test_eval_analysis_job.py  (NEW)
tests/architecture/test_eval_only_invariant.py (NEW)
```

`run_eval_analysis` takes the prebuilt `universe` and a live `AiService`, so it is
fully unit-testable with a fake AI and a spy session — no network, no real LLM.
It is **not** pure: it drives `ai.analyze()`, which writes `AiAnalysis` +
`EvaluationEvent` and commits internally.

### Data model / dataclass

```python
@dataclass(frozen=True)
class EvalAnalysisSummary:
    run_date: date
    universe_size: int
    analyzed_fresh: int
    cache_hits: int
    skipped_cap: int
    errors: int
    cap_hit: bool

    @property
    def processed(self) -> int:
        return self.analyzed_fresh + self.cache_hits + self.errors

    def as_dict(self, *, status: str) -> dict:
        # status ∈ {"ok", "disabled", "failed"}; processed included for /health.
        ...
```

**Invariant (asserted in tests):**
`processed == analyzed_fresh + cache_hits + errors` and
`processed + skipped_cap == universe_size`.

### Core loop

```python
def run_eval_analysis(session, *, ai, universe, max_calls, run_date) -> EvalAnalysisSummary:
    fresh = cache_hits = errors = 0
    cap_hit = False
    processed_count = 0
    skipped = 0
    for ticker in universe:
        if fresh >= max_calls:                    # cap counts FRESH calls only
            cap_hit = True
            skipped = len(universe) - processed_count
            log.warning("ai_eval_daily_cap_hit",
                        fresh=fresh, universe_size=len(universe),
                        skipped=skipped, run_date=str(run_date))
            break
        try:
            result = ai.analyze(ticker)           # commits AiAnalysis + EvaluationEvent
            if result.cached:
                cache_hits += 1
            else:
                fresh += 1
        except Exception as exc:                  # per-ticker isolation
            session.rollback()                    # analyze() commits internally;
                                                  # clean partial state before next
            errors += 1
            log.warning("ai_eval_ticker_failed", ticker=ticker, error=str(exc))
        processed_count += 1
    return EvalAnalysisSummary(run_date, len(universe), fresh, cache_hits,
                               skipped, errors, cap_hit)
```

Notes:
- `skipped` is initialized to 0 and only reassigned on the cap-break; a loop that
  runs to completion leaves `skipped = 0`. No `for/else` needed.
- `max_calls <= 0`: the `fresh >= max_calls` guard fires on the first iteration
  (`0 >= 0`), so nothing is analyzed, `skipped = universe_size`, `cap_hit = True`.
  Single code path, no special-case branch.
- `run_date` is passed in by the job (`date.today()`); tests inject a fixed date.

### Universe builder

```python
def build_eval_universe(session) -> list[str]:
    watch = session.query(WatchlistItem.ticker).all()
    holdings = PaperPositionRepository(session).open_positions_snapshot()
    raw = [w[0] for w in watch] + [p.ticker for p in holdings]
    normalized = {t.strip().upper() for t in raw if t and t.strip()}
    return sorted(normalized)                     # dedupe + ASC
```

Reads only: `WatchlistItem` (model) and the open-positions helper. No mutation.

### Composition root (jobs.py)

```python
def run_eval_analysis_job() -> None:
    settings = get_settings()
    run_date = date.today()
    gen = session_scope()                          # generator helper (not a CM)
    db = next(gen)
    summary = None
    status = "ok"
    try:
        if not settings.ai_eval_enabled:
            status = "disabled"
            summary = EvalAnalysisSummary(run_date, 0, 0, 0, 0, 0, False)
            log.info("ai_eval_disabled")
            return
        data = DataService(db, _build_quote_client(),
                           news_ttl_days=settings.news_cache_ttl_days)
        ai = AiService(db, ai_client=AnthropicClient(), data=data,
                       model=settings.ai_model, ttl_hours=settings.ai_cache_ttl_hours,
                       model_analyze=settings.ai_model_analyze or None,
                       model_router=settings.ai_model_router or None)
        universe = build_eval_universe(db)
        summary = run_eval_analysis(db, ai=ai, universe=universe,
                                    max_calls=settings.ai_eval_max_calls_per_day,
                                    run_date=run_date)
        log.info("ai_eval_done", **summary.as_dict(status="ok"))
    except Exception as exc:                        # job-boundary failure
        status = "failed"
        log.warning("ai_eval_job_failed", error=str(exc))
        # Best-effort failed-summary; db may be unusable.
        try:
            record_eval_run_summary(
                db, EvalAnalysisSummary(run_date, 0, 0, 0, 0, 0, False)
                    .as_dict(status="failed", error=str(exc)))
        except Exception:
            pass
        return
    finally:
        if summary is not None and status in ("ok", "disabled"):
            try:
                record_eval_run_summary(db, summary.as_dict(status=status))
            except Exception as exc:
                log.warning("ai_eval_summary_persist_failed", error=str(exc))
        gen.close()                                 # runs the generator's finally → db.close()
```

- **Disabled** still records a `status="disabled"` summary so `/health/scheduler`
  can explain "last run was disabled," not just "job exists."
- **Job-boundary failure** (e.g. `build_eval_universe` raises, AiService
  construction fails) → record `status="failed"` + error, WARN, **no raise**. The
  scheduler never crashes.
- **Session won't even open** (`next(gen)` itself raises): physically cannot write
  an `AppSetting`. Behavior is **log warning only, no persisted failed summary** —
  a physical limit, not a design failure. (`gen.close()` is still attempted.)

### Scheduler registration (unconditional + body gate)

```python
sched.add_job(
    run_eval_analysis_job,
    id="ai_eval_analysis",
    trigger=CronTrigger(hour=settings.ai_eval_hour, minute=settings.ai_eval_minute,
                        day_of_week="mon-fri", timezone="UTC"),
)
```

Registered unconditionally; the `AI_EVAL_ENABLED` gate lives in the body so the
job is visible in `/health/scheduler` even when disabled, and enabling is a pure
env flip + restart with no code-path divergence.

### Config (pydantic Settings)

```python
ai_eval_enabled: bool = Field(False, alias="AI_EVAL_ENABLED")
ai_eval_max_calls_per_day: int = Field(60, alias="AI_EVAL_MAX_CALLS_PER_DAY", ge=0)
ai_eval_hour: int = Field(21, alias="AI_EVAL_HOUR", ge=0, le=23)      # UTC
ai_eval_minute: int = Field(0, alias="AI_EVAL_MINUTE", ge=0, le=59)
```

### Summary persistence (scheduler/eval_state.py)

Mirrors `scheduler/state.py`. `AppSetting` key
`scheduler.ai_eval_analysis.last_run`, JSON payload:

```json
{
  "status": "ok",
  "run_date": "2026-05-29",
  "universe_size": 35,
  "analyzed_fresh": 35,
  "cache_hits": 0,
  "skipped_cap": 0,
  "errors": 0,
  "cap_hit": false,
  "processed": 35,
  "ts": "2026-05-29T21:00:13+00:00"
}
```

`status` ∈ `{"ok", "disabled", "failed"}`. `failed` payloads also carry `"error"`.

---

## Eval-only invariant (CI-enforced)

The only side-effecting call in `eval_analysis.py` is `ai.analyze()`. An
architecture-guard test asserts the module imports **nothing** from the
order/allocation/execution/watchlist-mutation layers:

**Forbidden imports** (substring match on import statements in
`marketpulse/ai/eval_analysis.py`):

```
marketpulse.trading.execution_engine
marketpulse.trading.forward_engine
marketpulse.trading.daily_cycle
marketpulse.trading.bid_aggregator
marketpulse.backtest.allocation        # allocate_for_day kernel
marketpulse.broker.order_service       # order placement
marketpulse.web.routes.watchlist       # watchlist mutation (add/delete)
```

**Allowed reads:** `marketpulse.trading.repository` (open-positions helper) and
`marketpulse.db.models.WatchlistItem`. The guard forbids placing orders,
allocating capital, and mutating the watchlist — it does not forbid reading
holdings. Mirrors the existing P6b forward-invariant grep test.

---

## Testing strategy

### Core `run_eval_analysis` (unit; `FakeAi` returns `AnalysisResult(cached=…)` or raises; spy session)

- all-fresh under cap → `fresh=N, cache_hits=0, skipped_cap=0, cap_hit=False`.
- same-day re-run (all cached) → `fresh=0, cache_hits=N, skipped_cap=0,
  cap_hit=False` (cache hits don't consume cap).
- cap hit mid-loop → `fresh==max_calls, skipped_cap==remaining, cap_hit=True`.
- cache hits don't consume cap → universe ≫ cap but mostly cached → all processed,
  `fresh ≤ cap`.
- `max_calls<=0` → `fresh=0, skipped_cap=universe_size, cap_hit=True`, WARN.
- per-ticker raise → `errors+=1`, **`session.rollback()` asserted called**, loop
  continues, error doesn't consume cap.
- **mixed-path invariant** — `fresh=2, cache_hit=1, errors=1, skipped=3` over a
  `universe_size=7`: assert `processed == 4` and `processed + skipped_cap == 7`.
- empty universe → all-zero, no error.

### Universe `build_eval_universe` (db_session)

- watchlist ∪ open-holdings deduped / uppercased / stripped / sorted-ASC.
- ticker in both watchlist and holdings appears once.
- `status != "OPEN"` positions excluded.
- empty → `[]`.

### Job `run_eval_analysis_job` (integration; monkeypatched seams)

- disabled (`AI_EVAL_ENABLED=false`) → persists `status="disabled"`; `analyze`
  never called.
- happy path → persists `status="ok"` + counts.
- `build_eval_universe` raises → persists `status="failed"` + error; **no
  exception propagates**.
- session-open failure (`next(gen)` raises) → **log warning only, no persisted
  summary** (physical limit; explicitly asserted that no `AppSetting` write is
  attempted / no crash).

### Summary persistence (`tests/scheduler/test_eval_state.py`, mirror test_state.py)

- `record_eval_run_summary` upsert: insert then overwrite.
- `get_eval_last_run_summary` round-trips dict; `None` when never run.

### Architecture guard (`tests/architecture/test_eval_only_invariant.py`)

- assert none of the forbidden modules appear in `eval_analysis.py` imports.

### Scheduler registration

- `register_jobs` adds `id="ai_eval_analysis"` with
  `CronTrigger(hour=21, minute=0, day_of_week="mon-fri", timezone="UTC")`.

### Config

- defaults: `AI_EVAL_ENABLED=false`, `AI_EVAL_MAX_CALLS_PER_DAY=60`,
  `AI_EVAL_HOUR=21`, `AI_EVAL_MINUTE=0`.

---

## Rollout

1. Merge with `AI_EVAL_ENABLED=false` (no behavior change in prod).
2. Enable on the NAS by setting `AI_EVAL_ENABLED=true` (+ optional
   `AI_EVAL_MAX_CALLS_PER_DAY`) and restarting the container.
3. Watch `/health/scheduler` for the first `status="ok"` summary; confirm
   `analyzed_fresh ≈ universe_size`, `errors=0`.
4. After ~5 sessions, confirm `/lab/ai-track` h1/h5 outcome counts grow materially
   (tens → hundreds over weeks), making the hit-rate statistically meaningful.

## Cost note

At default cap 60 fresh sonnet analyses/day × ~21 trading days ≈ 1,260
analyses/month worst case (universe permitting). Cache hits within a day are free.
The cap is the cost ceiling; the universe (watchlist ∪ holdings, typically
~30–50) is the practical driver.
