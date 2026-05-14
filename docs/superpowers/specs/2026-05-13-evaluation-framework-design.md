# Evaluation Framework — Phase 1 of PRINCIPLES Trilogy

**Status:** Approved
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
      - "ai_analysis": payload has Claude's verdict (bullish/neutral/bearish)
                       and the input snapshot (quote, indicators, news heads)
      - "signal_marker": payload has the signal type (ema_golden_cross, etc.)
                         and the bar at trigger time

    Outcomes are computed lazily by the nightly job and stored in
    EvaluationOutcome (one row per (event, horizon_days) pair).
    """
    __tablename__ = "evaluation_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    subtype: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC),
    )

    outcomes: Mapped[list["EvaluationOutcome"]] = relationship(
        back_populates="event", cascade="all, delete-orphan",
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

**Horizons:** 5, 20, 60 trading days (per-outcome row). Stored as count of trading days; converted via bar-index lookup in the data source, not calendar days (avoids weekend/holiday confusion).

**Why three horizons:** different signals have different optimal evaluation windows. EMA cross may take 20 days to play out; RSI extremes resolve in 5 days; AI long-thesis takes 60 days. Storing all three lets later analysis pick the right one per signal type.

### Modules

```
marketpulse/evaluation/
├── __init__.py         # public re-exports
├── events.py           # record_event() — single insertion API
├── outcomes.py         # compute_outcomes_for_pending_events()
├── forward_return.py   # forward_return_at_horizon(ticker, event_date, N)
└── benchmark.py        # benchmark constant + helper

# Tests
tests/evaluation/
├── test_events.py
├── test_forward_return.py
└── test_outcomes.py
```

#### `events.py`

```python
def record_event(
    *,
    event_type: str,           # "ai_analysis" | "signal_marker"
    subtype: str,              # e.g. "bullish" | "ema_golden_cross"
    ticker: str,
    event_time: datetime,      # tz-aware UTC
    payload: dict,
    db: Session,
) -> EvaluationEvent:
    """Record a point-in-time event. No outcome computed here.

    Returns the inserted EvaluationEvent (with id assigned by DB).
    Caller is responsible for the session commit/rollback boundary —
    we just session.add and session.flush (so id is available).
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
    horizons: list[int] = [5, 20, 60],
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

    Runs at 02:00 UTC (after market close, before user's morning).
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
        )
```

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

`tests/evaluation/test_forward_return.py`:
- Returns correct value for a known historical date pair
- Returns None when horizon is in the future
- Returns None when ticker has no bars
- Handles event on a weekend (skips to next Monday)
- Adjusts for splits (via yfinance adjusted close)

`tests/evaluation/test_events.py`:
- record_event inserts a row with all fields
- payload roundtrips through JSON cleanly
- Can insert two events with same (ticker, event_time) — deduplication is caller's responsibility

`tests/evaluation/test_outcomes.py`:
- compute_outcomes_for_pending_events inserts when horizon end is past
- Skips events with horizon end still in future
- Skips already-computed outcomes (idempotency)
- Returns accurate report counts
- Excess return is computed correctly vs benchmark

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
- Multi-benchmark (only SPY for now; A-share would need Hang Seng or similar later)
- Tax/fee-adjusted returns (use raw adjusted close)
- Drawdown / max-loss metrics (only point-in-time forward return)
- Confidence intervals on win rate (frequentist proportion is enough at this stage)
- Per-user evaluation (this is a single-user app)

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
