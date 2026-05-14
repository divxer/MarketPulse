# Evaluation Framework — Phase 1 of PRINCIPLES Trilogy

**Status:** Approved (revised after user review 2026-05-13)
**Author:** harvey
**Date:** 2026-05-13

## Goal

Build the shared evaluation infrastructure used by Phase 2 (AI hit-rate, PRINCIPLES #2) and Phase 3 (Signal win-rate, PRINCIPLES #3). No user-visible UI changes; everything is data-layer + nightly background job. By the time Phase 2/3 ship their UI badges, weeks of outcome data are already accumulated.

## Why this is a separate phase

The two PRINCIPLES projects share three identical pieces:

1. **An event table** — when did X happen for ticker Y? (X = AI verdict or Signal marker)
2. **An outcome table** — what was the forward return at horizon N days?
3. **A nightly job** — for events whose horizon end is now in the past, compute outcomes

If Phases 2 and 3 each build these independently we'd write the outcome-computation code twice and have two inconsistent schemas. Building once means Phases 2/3 become "add a hook" rather than "build a system."

## Architecture

### Schema

Two SQLAlchemy models added to `marketpulse/db/models.py`:

```python
class EvaluationEvent(Base):
    """A point-in-time event we want to evaluate later.

    event_type partitions the table:
      - "ai_analysis": payload has Claude's verdict + input snapshot
                       (quote, indicators, news heads, holdings ctx)
      - "signal_marker": payload has the signal type and the bar at trigger

    subtype is the FINE-GRAINED type within event_type:
      - For ai_analysis: "bullish" | "neutral" | "bearish"
      - For signal_marker: must match SignalType enum below
                           (e.g. "ema_golden_cross")

    All subtype values come from `marketpulse.evaluation.constants` —
    NOT free-form strings. This avoids typo-driven inconsistencies and
    makes future migrations to enums clean. (Phase 1 keeps them as
    String for SQLite compatibility, but constants gate writes.)

    `event_price` is the close price at event_time, denormalized from
    payload into an indexed column for fast "events where price was X"
    queries without JSON parsing. Hot path: Phase 2/3 dashboards.

    Outcomes are computed lazily by the nightly job and stored in
    EvaluationOutcome (one row per (event, horizon_days) pair).
    """
    __tablename__ = "evaluation_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    subtype: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_price: Mapped[float] = mapped_column(Float, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC),
    )

    outcomes: Mapped[list["EvaluationOutcome"]] = relationship(
        back_populates="event", cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_event_lookup", "event_type", "subtype", "ticker", "event_time"),
    )


class EvaluationOutcome(Base):
    """Forward-return measurement at a given horizon for an event.

    Inserted by the nightly job once `event_time + horizon_days` is in
    the past AND the necessary bars are available from yfinance.
    """
    __tablename__ = "evaluation_outcome"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_event.id"), nullable=False, index=True,
    )
    horizon_trading_days: Mapped[int] = mapped_column(Integer, nullable=False)
    event_price: Mapped[float] = mapped_column(Float, nullable=False)
    horizon_price: Mapped[float] = mapped_column(Float, nullable=False)
    horizon_date: Mapped[date] = mapped_column(Date, nullable=False)
    forward_return: Mapped[float] = mapped_column(Float, nullable=False)
    benchmark_ticker: Mapped[str] = mapped_column(String(16), default="SPY")
    benchmark_forward_return: Mapped[float] = mapped_column(Float, nullable=False)
    excess_return: Mapped[float] = mapped_column(Float, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC),
    )

    event: Mapped["EvaluationEvent"] = relationship(back_populates="outcomes")

    __table_args__ = (
        UniqueConstraint("event_id", "horizon_trading_days",
                         name="uq_event_horizon"),
    )
```

**Horizons:** 1, 5, 20, 60 trading days (per-outcome row). Stored as count of trading days; converted via bar-index lookup in the data source, not calendar days (avoids weekend/holiday confusion).

**Why these four horizons:**

| Horizon | Use case |
|---|---|
| 1 day | Very short signals — RSI intraday peaks, "Claude bullish tomorrow" verdicts |
| 5 days | Short-term — RSI extreme reversals, breakout follow-through |
| 20 days | Medium-term — EMA cross plays out, BB-squeeze breakouts mature |
| 60 days | Long-term — AI long-thesis verdicts, trend continuation |

**Why not 120 days:** beyond 60 days, single-event signal attribution gets washed out by macro events. If you need long-horizon analysis, the right tool is portfolio attribution, not single-event evaluation. Can be added later if a specific use case emerges (no schema change needed — just add `120` to the horizons list).

**Why not 0.5 day (intraday):** we don't store intraday bars; entire framework is daily-close. Adding intraday is a separate, much larger project (data layer change).

### Modules

```
marketpulse/evaluation/
├── __init__.py         # public re-exports
├── constants.py        # AI_VERDICT_* and SIGNAL_TYPE_* — subtype taxonomy
├── events.py           # record_event() — single insertion API
├── outcomes.py         # compute_outcomes_for_pending_events()
├── forward_return.py   # forward_return_at_horizon(ticker, event_date, N)
└── benchmark.py        # benchmark constant + helper

# Tests
tests/evaluation/
├── test_constants.py
├── test_events.py
├── test_forward_return.py
└── test_outcomes.py
```

#### `constants.py`

```python
# Standardized subtype taxonomy. record_event() validates against this.

class AIVerdict:
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"

    @classmethod
    def all(cls) -> set[str]:
        return {cls.BULLISH, cls.NEUTRAL, cls.BEARISH}


# Mirror of marketpulse.recap.signals' marker types.
class SignalType:
    EMA_GOLDEN_CROSS = "ema_golden_cross"
    EMA_DEATH_CROSS = "ema_death_cross"
    RSI_OVERBOUGHT = "rsi_overbought"
    RSI_OVERSOLD = "rsi_oversold"
    BOLLINGER_UPPER = "bollinger_upper"
    BOLLINGER_LOWER = "bollinger_lower"

    @classmethod
    def all(cls) -> set[str]:
        return {cls.EMA_GOLDEN_CROSS, cls.EMA_DEATH_CROSS,
                cls.RSI_OVERBOUGHT, cls.RSI_OVERSOLD,
                cls.BOLLINGER_UPPER, cls.BOLLINGER_LOWER}


class EventType:
    AI_ANALYSIS = "ai_analysis"
    SIGNAL_MARKER = "signal_marker"

    SUBTYPES = {
        AI_ANALYSIS: AIVerdict.all,
        SIGNAL_MARKER: SignalType.all,
    }
```

`record_event()` validates `subtype in EventType.SUBTYPES[event_type]()`. Wrong subtype → ValueError. Catches typos at write time.

#### `events.py`

```python
def record_event(
    *,
    event_type: str,           # EventType.AI_ANALYSIS | EventType.SIGNAL_MARKER
    subtype: str,              # AIVerdict.* | SignalType.*
    ticker: str,
    event_time: datetime,      # tz-aware UTC
    event_price: float,        # close at event_time (or current quote)
    payload: dict,
    db: Session,
) -> EvaluationEvent:
    """Record a point-in-time event. No outcome computed here.

    Validates:
      - event_type ∈ EventType.{AI_ANALYSIS, SIGNAL_MARKER}
      - subtype ∈ EventType.SUBTYPES[event_type]()
      - event_time is tz-aware
      - ticker normalized to UPPER
      - event_price > 0

    Returns the inserted EvaluationEvent (with id assigned by DB).
    Caller is responsible for the session commit/rollback boundary —
    we just session.add and session.flush (so id is available).

    Raises ValueError on validation failure.
    """
```

Idempotency: we DO allow duplicates. If a signal fires twice for the same ticker/time (which our dedup logic prevents but other code paths may not), we get two rows. That's correct — different code paths can independently care about the same event.

#### `forward_return.py`

```python
def forward_return_at_horizon(
    ticker: str,
    event_date: date,
    horizon_trading_days: int,
    data: DataService,
) -> ForwardReturnResult | None:
    """Compute forward return from event_date to event_date + N trading days.

    Returns None if:
      - Bars not available (network/quota issue)
      - event_date is in the future (shouldn't happen, but defensive)
      - horizon end is still in the future (not enough bars yet)
      - event_date itself is not a trading day AND no bar within 2 trading
        days exists (shouldn't happen for traded tickers, but defensive)

    Returns ForwardReturnResult on success:
      - event_price: close on event_date (or first trading day after)
      - horizon_price: close on event_date + N trading days
      - horizon_date: the calendar date of the horizon bar
      - forward_return: (horizon - event) / event
    """


@dataclass
class ForwardReturnResult:
    event_price: float
    horizon_price: float
    horizon_date: date
    forward_return: float
```

Implementation note: use `DataService.get_history(ticker, period='5y')` to get a wide bar series, then locate event_date by bisect, and step N bars forward.

#### `outcomes.py`

```python
def compute_outcomes_for_pending_events(
    db: Session,
    data: DataService,
    horizons: list[int] = [1, 5, 20, 60],
    max_events: int = 500,
) -> ComputeOutcomeReport:
    """For each EvaluationEvent without a matching EvaluationOutcome row at
    any of the requested horizons, compute the outcome and insert.

    Skip events where the horizon end is still in the future.

    Idempotent: safe to run multiple times per day. The UNIQUE constraint
    on (event_id, horizon_trading_days) prevents duplicate outcome rows.

    Returns a report counting inserted / skipped / failed events.
    """


@dataclass
class ComputeOutcomeReport:
    events_examined: int
    outcomes_inserted: int
    skipped_horizon_in_future: int
    skipped_data_unavailable: int
    failed: int
```

#### `benchmark.py`

```python
BENCHMARK_TICKER = "SPY"

def benchmark_forward_return(
    event_date: date,
    horizon_trading_days: int,
    data: DataService,
) -> float | None:
    """SPY forward return over the same horizon — used to compute excess return.

    Cached: SPY history is fetched once and reused across events on the
    same horizon. Cache invalidates on next nightly run.
    """
```

### Scheduler integration

Add a new job to `marketpulse/scheduler/jobs.py`:

```python
def run_outcome_computation() -> None:
    """Daily job: compute outcomes for pending events.

    Runs at 02:00 UTC (after US market close 21:00 UTC, before Beijing
    user's morning 10:00 China time). US market close → 5 hour buffer
    for yfinance to settle before we query.

    Trade-off accepted: a Beijing user looking before 10:00 China time
    won't see yesterday's outcomes yet. For "fresh now" outcomes we
    expose `/admin/compute-outcomes` (Phase 2 follow-up).
    """
    from marketpulse.evaluation.outcomes import compute_outcomes_for_pending_events

    with session_scope() as db:
        data = DataService(...)  # construct same way as recap
        report = compute_outcomes_for_pending_events(db, data)
        log.info(
            "outcome_computation_done",
            events_examined=report.events_examined,
            outcomes_inserted=report.outcomes_inserted,
            skipped_horizon_in_future=report.skipped_horizon_in_future,
            skipped_data_unavailable=report.skipped_data_unavailable,
            failed=report.failed,
            per_failure_details=report.failure_log,  # detailed list,
                                                      # see logging note below
        )
```

**Granular logging:** every skipped/failed event includes ticker, horizon, and reason. The `ComputeOutcomeReport` carries a `failure_log: list[dict]` with `{event_id, ticker, horizon, reason}` for the operator to triage delisted tickers etc.

Register the job in `build_scheduler()`:

```python
scheduler.add_job(
    run_outcome_computation,
    CronTrigger(hour=2, minute=0, timezone="UTC"),
    id="outcome_computation",
    replace_existing=True,
)
```

### Migration

Alembic migration to create both tables. Standard pattern, no data migration needed.

## Data Flow

```
Phase 2/3 not yet wired in this phase — but the contract is:

  [event happens (e.g. AI analysis, signal marker)]
                ↓
   record_event(event_type, subtype, ticker, event_time, payload, db)
                ↓
   inserted into evaluation_event table

  [nightly 02:00 UTC]
                ↓
   compute_outcomes_for_pending_events(db, data)
                ↓
   for each pending event:
     if event_time + horizon * 1.5 calendar days < today:
       forward_ret = forward_return_at_horizon(ticker, event_date, horizon)
       bench_ret = benchmark_forward_return(event_date, horizon)
       if both not None:
         INSERT INTO evaluation_outcome (event_id, horizon, ...)
                ↓
   outcomes available for read

  [Phase 2/3 UI query]
                ↓
   SELECT win_rate, avg_return, n_samples
   FROM evaluation_outcome JOIN evaluation_event
   WHERE event_type/subtype/ticker/horizon = ...
                ↓
   render badge on chart / stock page
```

## Edge Cases

| Case | Handling |
|---|---|
| Event time is on a weekend or holiday | `forward_return_at_horizon` finds first trading bar >= event_date. forward_return computed from that bar. |
| Ticker delisted between event_time and horizon | data unavailable → skipped_data_unavailable, outcome not inserted. Will retry indefinitely (no harm); operator can manually delete the event if known stale. |
| Stock split between event_time and horizon | yfinance returns adjusted close — forward_return correctly reflects total return including split. |
| Dividend in window | same — adjusted close handles it. |
| Benchmark SPY data fetch fails | Outcome not inserted that night; retried next night. |
| Many events backlogged (first run after deploy) | `max_events=500` per run. Subsequent runs catch up. |
| Same event has outcomes for some horizons but not others | UNIQUE constraint is on (event, horizon), so we can insert the missing horizon's outcome later without conflict. |
| Event_time is in the future (clock drift?) | forward_return returns None → skipped_data_unavailable. Defensive. |

## Tests

`tests/evaluation/test_constants.py`:
- `EventType.SUBTYPES[AI_ANALYSIS]()` returns exactly the 3 AIVerdict values
- `EventType.SUBTYPES[SIGNAL_MARKER]()` returns exactly the 6 SignalType values
- Each constant string matches what `signals.py` actually emits (regression: keeps Phase 3 hook in sync with this taxonomy)

`tests/evaluation/test_forward_return.py`:
- Returns correct value for a known historical date pair
- Returns None when horizon is in the future
- Returns None when ticker has no bars
- Handles event on a weekend (skips to next Monday)
- Handles event on a Friday with horizon=1 (next bar is Monday)
- Adjusts for splits (via yfinance adjusted close)
- **Cross-year boundary**: event in late Dec, horizon spanning new year holidays
- **Cross-Thanksgiving / Christmas**: horizon spans market closures, bar index still works

`tests/evaluation/test_events.py`:
- record_event inserts a row with all fields
- payload roundtrips through JSON cleanly
- **Validation**: invalid event_type → ValueError
- **Validation**: subtype not in taxonomy → ValueError
- **Validation**: ticker normalized to upper
- **Validation**: event_price ≤ 0 → ValueError
- **Validation**: naive datetime → ValueError
- Can insert two events with same (ticker, event_time) — deduplication is caller's responsibility
- **Multiple events same ticker same day**: 5 events with same ticker/date but different subtypes all insert (no UNIQUE conflict on event table)

`tests/evaluation/test_outcomes.py`:
- compute_outcomes_for_pending_events inserts when horizon end is past
- Skips events with horizon end still in future
- Skips already-computed outcomes (idempotency)
- Returns accurate report counts
- Excess return is computed correctly vs benchmark
- **Failure log details**: ticker + horizon + reason recorded per failure
- **Mixed horizons**: same event gets outcomes for horizons 1 and 5 first run, 20 and 60 later run (partial completion)
- **Benchmark cache**: SPY fetched once per run regardless of how many events touch it

`tests/scheduler/test_outcome_job.py`:
- Job registered in build_scheduler()
- Job triggers compute_outcomes_for_pending_events

## File Manifest

**New:**
- `marketpulse/evaluation/__init__.py`
- `marketpulse/evaluation/events.py`
- `marketpulse/evaluation/forward_return.py`
- `marketpulse/evaluation/outcomes.py`
- `marketpulse/evaluation/benchmark.py`
- `tests/evaluation/test_events.py`
- `tests/evaluation/test_forward_return.py`
- `tests/evaluation/test_outcomes.py`
- `tests/scheduler/test_outcome_job.py`
- `alembic/versions/<hash>_evaluation_event_outcome.py`

**Modified:**
- `marketpulse/db/models.py` — add EvaluationEvent and EvaluationOutcome
- `marketpulse/scheduler/jobs.py` — add `run_outcome_computation` + register

**Unchanged:**
- All web routes, templates, chart code, trade/holding/recap logic
- Phase 2 (AI hit-rate) and Phase 3 (Signal win-rate) hooks come in their own PRs

## Risk

**Low.** Pure additive — no existing functionality touched. The scheduler job runs at 02:00 UTC and only inserts new rows; it doesn't modify any existing data. If something goes wrong, the worst case is "outcomes don't compute" — Phases 2/3 would show "N=0" until fixed.

## Out of Scope

- UI surfacing (Phases 2 and 3)
- Hooks into ai/service.py and signals.py (Phases 2 and 3)
- **Multi-benchmark** — SPY only for now. When/if A-share or HK stocks
  enter the picture, add `benchmark_ticker` as an optional override per
  event_type or per ticker (e.g. mapping table `ticker_market → benchmark`).
  Schema migration is non-breaking: `evaluation_outcome.benchmark_ticker`
  is already a column.
- **PostgreSQL/JSONB** — SQLite + JSON is fine for single-user current
  scale. When/if we migrate to PostgreSQL, payload becomes JSONB with
  GIN index for arbitrary key lookups. The denormalized `event_price`
  + 4-column index in this phase keeps SQLite query path fast for the
  hot Phase 2/3 queries.
- Tax/fee-adjusted returns (use raw adjusted close)
- Drawdown / max-loss metrics (only point-in-time forward return)
- Confidence intervals on win rate (frequentist proportion is enough at this stage)
- Per-user evaluation (this is a single-user app)
- **Intraday horizons** — entire framework is daily-close. Adding intraday
  requires data layer changes (separate larger project).

## Future Extensions Notes

These are explicitly **not done in Phase 1** but the design accommodates them without schema breakage:

### Promoting payload fields to indexed columns

Pattern when a payload field becomes a hot query path (e.g., `payload["rsi_value"]` queried often in Phase 3 analytics):

1. Add typed column via Alembic migration (`rsi_value: Mapped[float | None]`)
2. One-time data migration: extract from existing payload rows
3. Update `record_event()` to populate the new column from payload
4. Add index if needed

Same pattern already used for `event_price`. Documents this as the **canonical migration recipe** rather than redesigning every time.

### Multi-benchmark mapping table

When/if A-share or HK stocks enter scope, instead of hardcoding `BENCHMARK_TICKER = "SPY"`:

```python
# Future: marketpulse/evaluation/benchmark_map.py
class BenchmarkMap(Base):
    __tablename__ = "benchmark_map"
    ticker: Mapped[str] = mapped_column(primary_key=True)  # or pattern
    benchmark_ticker: Mapped[str]
    rule_priority: Mapped[int]  # specific ticker > pattern > default

# Example rows:
#   ticker='AAPL',  benchmark='SPY',  priority=10
#   ticker='600*',  benchmark='000300.SH', priority=5  (CSI 300 for A-share)
#   ticker='*',     benchmark='SPY',  priority=0       (default)
```

`benchmark_forward_return()` consults the map (with `lru_cache` for hot lookups). Outcome row's `benchmark_ticker` column already accommodates this — no schema change to `evaluation_outcome` needed.

### Manual refresh endpoint (Phase 2/3 hook)

Phase 2 should add `POST /admin/compute-outcomes` (auth required, single-user) so Beijing-timezone user wanting "fresh now" outcomes can trigger compute mid-day without waiting for 02:00 UTC cron. UI nicety: small refresh button next to the AI-hit-rate badge that calls this endpoint. Documented here so Phase 2 spec doesn't re-derive the need.

## Principles Compliance

Per `docs/PRINCIPLES.md`:

- **#1 Measure, don't auto-modify**: Outcomes are *computed and stored*. No automatic prompt tuning, no automatic signal weight adjustment. Phases 2/3 UI shows numbers, user decides what to do. ✓
- **#2 AI verdicts must be auditable**: This phase builds the infrastructure to capture AI input + output. Phase 2 hooks it in. ✓
- **#3 Signals must declare their signal-to-noise**: This phase builds the outcome computation. Phase 3 uses it to render win rates. ✓
- **#5 Determinism**: Outcomes are pure functions of (event, bars). Same inputs → same outputs. ✓

## Implementation Order (this phase only)

1. DB models + Alembic migration
2. `forward_return.py` + tests (the hardest math, isolate first)
3. `benchmark.py` + tests (cache layer for SPY)
4. `events.py` + tests (trivial insertion API)
5. `outcomes.py` + tests (the orchestration glue)
6. Scheduler job + test
7. Integration test end-to-end

Estimated: **5-7 days** (1 work-week).
