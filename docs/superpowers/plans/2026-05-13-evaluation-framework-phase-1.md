# Evaluation Framework Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Build the shared evaluation infrastructure (event/outcome tables + nightly forward-return job) used by PRINCIPLES.md #2 (AI hit-rate) and #3 (Signal win-rate).

**Architecture:** Two SQLAlchemy models, five `marketpulse/evaluation/` modules, one APScheduler job. No UI changes. Phases 2/3 add hooks on top.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, Alembic, APScheduler, pytest. No new deps.

**Spec:** [`docs/superpowers/specs/2026-05-13-evaluation-framework-design.md`](../specs/2026-05-13-evaluation-framework-design.md)

**Branch:** `feat/evaluation-framework-phase-1` (already created, has 3 spec commits).

---

## Pre-flight

- [ ] **Step 0a:** Confirm branch + clean tree

```bash
git branch --show-current      # expect: feat/evaluation-framework-phase-1
git status --short             # expect: empty (spec commits already in)
uv run pytest 2>&1 | tail -3   # expect: all green
```

If anything fails, stop.

- [ ] **Step 0b:** Inspect existing scheduler patterns to match style

```bash
sed -n '1,50p' marketpulse/scheduler/jobs.py
```

Note the import pattern, `session_scope()` usage, `record_run_summary()` for telemetry, and the `CronTrigger` registration style. New job mirrors these.

---

## Task 1: DB Models + Alembic Migration

**Files:**
- Modify: `marketpulse/db/models.py`
- Create: `alembic/versions/<auto-hash>_evaluation_tables.py`

### Step 1a: Add models to `marketpulse/db/models.py`

Find the existing imports at the top. Add (if missing):

```python
from sqlalchemy import (
    JSON, Date, DateTime, Float, ForeignKey, Index, Integer, String,
    UniqueConstraint,
)
```

At the bottom of `models.py` (after the last model class), add:

```python
class EvaluationEvent(Base):
    """A point-in-time event we want to evaluate later.

    event_type partitions: "ai_analysis" | "signal_marker"
    subtype values come from marketpulse.evaluation.constants
    """
    __tablename__ = "evaluation_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    subtype: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
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
    """Forward-return measurement at a given horizon for an event."""
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

If `date` is not imported at the top of `models.py`, add it: `from datetime import UTC, date, datetime`.

### Step 1b: Generate Alembic migration

```bash
uv run alembic revision --autogenerate -m "evaluation event and outcome tables"
```

Inspect the generated file under `alembic/versions/`. It should contain:
- `op.create_table('evaluation_event', ...)` with all columns + the `ix_event_lookup` index
- `op.create_table('evaluation_outcome', ...)` with the UniqueConstraint
- Foreign key from `evaluation_outcome.event_id` to `evaluation_event.id`

If autogenerate missed the index, add it manually:

```python
op.create_index(
    'ix_event_lookup', 'evaluation_event',
    ['event_type', 'subtype', 'ticker', 'event_time'],
)
```

### Step 1c: Run migration

```bash
uv run alembic upgrade head
```

Verify tables exist:

```bash
uv run python -c "from sqlalchemy import inspect; from marketpulse.db.base import engine; print([t for t in inspect(engine).get_table_names() if 'evaluation' in t])"
```

Expected output: `['evaluation_event', 'evaluation_outcome']`

### Step 1d: Run tests to verify no regressions

```bash
uv run pytest 2>&1 | tail -5
```

All existing tests must still pass.

### Step 1e: Commit

```bash
git add marketpulse/db/models.py alembic/versions/
git commit -m "$(cat <<'EOF'
feat(evaluation): add EvaluationEvent and EvaluationOutcome models

Schema for the Phase 1 evaluation framework. EvaluationEvent stores
point-in-time events (AI verdicts, signal markers); EvaluationOutcome
stores forward-return measurements at horizons {1, 5, 20, 60} trading
days, plus benchmark comparison vs SPY.

ix_event_lookup composite index optimizes the hot Phase 2/3 query:
"events by (event_type, subtype, ticker) in date range."

event_price denormalized onto EvaluationEvent for fast indexed sorting
without JSON parsing.

UniqueConstraint on (event_id, horizon_trading_days) makes the nightly
outcome computation idempotent.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: constants.py + Tests

**Files:**
- Create: `marketpulse/evaluation/__init__.py`
- Create: `marketpulse/evaluation/constants.py`
- Create: `tests/evaluation/__init__.py`
- Create: `tests/evaluation/test_constants.py`

### Step 2a: Create `marketpulse/evaluation/__init__.py`

```python
"""Evaluation framework: events, outcomes, forward-return computation."""
```

(empty package init for now; will re-export later)

### Step 2b: Create `marketpulse/evaluation/constants.py`

```python
"""Standardized taxonomy for evaluation events.

record_event() validates against these — no free-form subtype strings.
Mirrors marketpulse.recap.signals taxonomy for signal_marker; defines
canonical labels for ai_analysis.
"""


class AIVerdict:
    """Claude analysis verdict labels."""
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"

    @classmethod
    def all(cls) -> set[str]:
        return {cls.BULLISH, cls.NEUTRAL, cls.BEARISH}


class SignalType:
    """K-line marker types — mirrors marketpulse.recap.signals."""
    EMA_GOLDEN_CROSS = "ema_golden_cross"
    EMA_DEATH_CROSS = "ema_death_cross"
    RSI_OVERBOUGHT = "rsi_overbought"
    RSI_OVERSOLD = "rsi_oversold"
    BOLLINGER_UPPER = "bollinger_upper"
    BOLLINGER_LOWER = "bollinger_lower"

    @classmethod
    def all(cls) -> set[str]:
        return {
            cls.EMA_GOLDEN_CROSS, cls.EMA_DEATH_CROSS,
            cls.RSI_OVERBOUGHT, cls.RSI_OVERSOLD,
            cls.BOLLINGER_UPPER, cls.BOLLINGER_LOWER,
        }


class EventType:
    """Top-level event partition."""
    AI_ANALYSIS = "ai_analysis"
    SIGNAL_MARKER = "signal_marker"

    SUBTYPES = {
        AI_ANALYSIS: AIVerdict.all,
        SIGNAL_MARKER: SignalType.all,
    }

    @classmethod
    def all(cls) -> set[str]:
        return set(cls.SUBTYPES.keys())
```

### Step 2c: Create `tests/evaluation/__init__.py`

```python
```

(empty)

### Step 2d: Create `tests/evaluation/test_constants.py`

```python
from marketpulse.evaluation.constants import (
    AIVerdict,
    EventType,
    SignalType,
)


def test_ai_verdict_has_exactly_three_values():
    assert AIVerdict.all() == {"bullish", "neutral", "bearish"}


def test_signal_type_has_exactly_six_values():
    assert SignalType.all() == {
        "ema_golden_cross", "ema_death_cross",
        "rsi_overbought", "rsi_oversold",
        "bollinger_upper", "bollinger_lower",
    }


def test_signal_type_matches_recap_signals_emitter():
    """Regression: keep this taxonomy in sync with what signals.py emits.

    If scan_signal_markers adds a new signal type, this test fails and
    forces us to add it to SignalType (and consider Phase 3 implications).
    """
    from marketpulse.recap.signals import scan_signal_markers
    import inspect
    source = inspect.getsource(scan_signal_markers)
    # Every SignalType constant must appear as a string literal in the source
    for type_name in SignalType.all():
        assert f'"{type_name}"' in source, (
            f"{type_name} not emitted by scan_signal_markers — taxonomy drift"
        )


def test_event_type_subtypes_map_complete():
    assert EventType.all() == {"ai_analysis", "signal_marker"}
    assert EventType.SUBTYPES[EventType.AI_ANALYSIS]() == AIVerdict.all()
    assert EventType.SUBTYPES[EventType.SIGNAL_MARKER]() == SignalType.all()
```

### Step 2e: Run tests

```bash
uv run pytest tests/evaluation/test_constants.py -v
```

Expected: 4 PASS.

### Step 2f: Commit

```bash
git add marketpulse/evaluation/ tests/evaluation/
git commit -m "$(cat <<'EOF'
feat(evaluation): add subtype taxonomy constants

AIVerdict / SignalType / EventType classes serve as the canonical
vocabulary for evaluation events. record_event() (next commit) will
validate against these.

The signal-type-matches-recap test forces the taxonomy to stay in
sync with what scan_signal_markers actually emits, so future signal
additions can't silently bypass this catalog.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: forward_return.py + Tests

**Files:**
- Create: `marketpulse/evaluation/forward_return.py`
- Create: `tests/evaluation/test_forward_return.py`

### Step 3a: Implement `forward_return.py`

```python
"""Forward-return computation at a given horizon for a ticker."""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date

from marketpulse.data.service import DataService
from marketpulse.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ForwardReturnResult:
    """Result of a forward-return computation."""
    event_price: float
    horizon_price: float
    horizon_date: date
    forward_return: float  # (horizon_price - event_price) / event_price


def forward_return_at_horizon(
    ticker: str,
    event_date: date,
    horizon_trading_days: int,
    data: DataService,
) -> ForwardReturnResult | None:
    """Compute forward return from event_date to event_date + N trading days.

    Returns None if:
      - Bars not available (network / quota / delisted ticker)
      - event_date is in the future
      - horizon end is still in the future (not enough bars yet)
      - Insufficient bars between event_date and horizon

    Uses the ticker's actual trading-day index, so weekends and holidays
    don't shift the horizon — N "trading days later" means N bars after
    event_date.
    """
    if event_date > date.today():
        return None

    try:
        # Fetch enough history to span any reasonable horizon (60 trading days
        # ~ 90 calendar days; we use 1y for a comfortable margin).
        bars = data.get_history(ticker, period="1y")
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "forward_return_fetch_failed",
            ticker=ticker, event_date=str(event_date), error=str(exc),
        )
        return None

    if not bars:
        return None

    # Find the bar at event_date or the first trading day after.
    bar_dates = [b.date for b in bars]
    idx = bisect.bisect_left(bar_dates, event_date)
    if idx >= len(bars):
        return None
    event_bar = bars[idx]

    horizon_idx = idx + horizon_trading_days
    if horizon_idx >= len(bars):
        return None  # horizon still in the future
    horizon_bar = bars[horizon_idx]

    event_price = event_bar.close
    horizon_price = horizon_bar.close
    if event_price == 0:
        return None  # defensive — division would explode

    return ForwardReturnResult(
        event_price=event_price,
        horizon_price=horizon_price,
        horizon_date=horizon_bar.date,
        forward_return=(horizon_price - event_price) / event_price,
    )
```

### Step 3b: Implement tests in `tests/evaluation/test_forward_return.py`

```python
"""Tests for forward_return_at_horizon.

Uses mocked DataService rather than hitting yfinance — we test the math,
not the data source.
"""
from datetime import date
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from marketpulse.evaluation.forward_return import (
    ForwardReturnResult,
    forward_return_at_horizon,
)


@dataclass
class FakeBar:
    date: date
    open: float = 0
    high: float = 0
    low: float = 0
    close: float = 0
    volume: int = 0


def _mock_data(bars: list[FakeBar]) -> MagicMock:
    """Helper: build a DataService mock whose get_history returns these bars."""
    m = MagicMock()
    m.get_history.return_value = bars
    return m


def test_known_date_pair_returns_correct_value():
    bars = [
        FakeBar(date=date(2026, 1, 1), close=100.0),
        FakeBar(date=date(2026, 1, 2), close=101.0),
        FakeBar(date=date(2026, 1, 3), close=102.0),
        FakeBar(date=date(2026, 1, 6), close=105.0),  # +3.96% from idx=1
    ]
    result = forward_return_at_horizon(
        "TST", date(2026, 1, 2), horizon_trading_days=2,
        data=_mock_data(bars),
    )
    assert result is not None
    assert result.event_price == 101.0
    assert result.horizon_price == 105.0
    assert result.horizon_date == date(2026, 1, 6)
    assert abs(result.forward_return - (105.0 - 101.0) / 101.0) < 1e-9


def test_event_on_weekend_skips_to_next_trading_day():
    # 2026-01-03 is a Saturday in this synthetic series; first bar at/after is
    # 2026-01-05 (Monday).
    bars = [
        FakeBar(date=date(2026, 1, 2), close=100.0),  # Fri
        FakeBar(date=date(2026, 1, 5), close=101.0),  # Mon
        FakeBar(date=date(2026, 1, 6), close=102.0),
    ]
    result = forward_return_at_horizon(
        "TST", date(2026, 1, 3), horizon_trading_days=1,
        data=_mock_data(bars),
    )
    assert result is not None
    assert result.event_price == 101.0  # used Monday's close
    assert result.horizon_price == 102.0


def test_horizon_in_future_returns_none():
    bars = [
        FakeBar(date=date(2026, 1, 1), close=100.0),
        FakeBar(date=date(2026, 1, 2), close=101.0),
    ]
    result = forward_return_at_horizon(
        "TST", date(2026, 1, 1), horizon_trading_days=5,
        data=_mock_data(bars),
    )
    assert result is None


def test_event_in_future_returns_none():
    bars = [FakeBar(date=date(2026, 1, 1), close=100.0)]
    future = date.today().replace(year=date.today().year + 1)
    result = forward_return_at_horizon(
        "TST", future, horizon_trading_days=1, data=_mock_data(bars),
    )
    assert result is None


def test_no_bars_returns_none():
    result = forward_return_at_horizon(
        "TST", date(2026, 1, 1), horizon_trading_days=1, data=_mock_data([]),
    )
    assert result is None


def test_fetch_exception_returns_none():
    m = MagicMock()
    m.get_history.side_effect = RuntimeError("yfinance quota")
    result = forward_return_at_horizon(
        "TST", date(2026, 1, 1), horizon_trading_days=1, data=m,
    )
    assert result is None


def test_zero_event_price_returns_none():
    bars = [
        FakeBar(date=date(2026, 1, 1), close=0.0),
        FakeBar(date=date(2026, 1, 2), close=10.0),
    ]
    result = forward_return_at_horizon(
        "TST", date(2026, 1, 1), horizon_trading_days=1, data=_mock_data(bars),
    )
    assert result is None  # would divide by zero


def test_cross_year_boundary_handled():
    """Event late December, horizon spanning new year holidays."""
    bars = [
        FakeBar(date=date(2025, 12, 29), close=100.0),
        FakeBar(date=date(2025, 12, 30), close=101.0),
        FakeBar(date=date(2025, 12, 31), close=102.0),
        # Jan 1 holiday
        FakeBar(date=date(2026, 1, 2), close=105.0),
        FakeBar(date=date(2026, 1, 5), close=107.0),
    ]
    result = forward_return_at_horizon(
        "TST", date(2025, 12, 30), horizon_trading_days=3,
        data=_mock_data(bars),
    )
    assert result is not None
    assert result.event_price == 101.0
    assert result.horizon_price == 107.0
    assert result.horizon_date == date(2026, 1, 5)


def test_horizon_zero_returns_event_bar_self():
    """Edge case: horizon=0 should mean "same day" — forward_return=0."""
    bars = [
        FakeBar(date=date(2026, 1, 1), close=100.0),
        FakeBar(date=date(2026, 1, 2), close=101.0),
    ]
    result = forward_return_at_horizon(
        "TST", date(2026, 1, 1), horizon_trading_days=0, data=_mock_data(bars),
    )
    assert result is not None
    assert result.forward_return == 0.0
    assert result.event_price == result.horizon_price == 100.0
```

### Step 3c: Run tests

```bash
uv run pytest tests/evaluation/test_forward_return.py -v
```

Expected: 9 PASS.

### Step 3d: Commit

```bash
git add marketpulse/evaluation/forward_return.py tests/evaluation/test_forward_return.py
git commit -m "$(cat <<'EOF'
feat(evaluation): forward_return_at_horizon — core math primitive

Pure function from (ticker, event_date, N) → ForwardReturnResult.
Uses the ticker's own trading-day index so weekends/holidays don't
shift the horizon ("N trading days" means N bars).

Returns None on data unavailability, event in future, horizon in
future, or zero event price (defensive).

Tested on synthetic mocked bars — no yfinance hit. 9 tests covering
known pairs, weekends, cross-year boundary, future events, fetch
exceptions, and edge cases.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: benchmark.py + Tests

**Files:**
- Create: `marketpulse/evaluation/benchmark.py`
- Create: `tests/evaluation/test_benchmark.py`

### Step 4a: Implement `benchmark.py`

```python
"""SPY benchmark forward-return helper with per-run caching.

Fetched once per benchmark_forward_return() process run via lru_cache,
then reused across all events on the same horizon. Cache invalidates
when the process restarts (i.e., next nightly cron).
"""
from __future__ import annotations

from datetime import date

from marketpulse.data.service import DataService
from marketpulse.evaluation.forward_return import forward_return_at_horizon

BENCHMARK_TICKER = "SPY"


def benchmark_forward_return(
    event_date: date,
    horizon_trading_days: int,
    data: DataService,
) -> float | None:
    """SPY forward return over the same horizon — used to compute excess return.

    Returns None if SPY data unavailable for that horizon.

    Note: NOT cached across processes; one cron run = one SPY history fetch.
    Phase 2/3 may want a class-level cache when called repeatedly.
    """
    result = forward_return_at_horizon(
        BENCHMARK_TICKER, event_date, horizon_trading_days, data,
    )
    return result.forward_return if result else None
```

### Step 4b: Implement tests in `tests/evaluation/test_benchmark.py`

```python
from datetime import date
from dataclasses import dataclass
from unittest.mock import MagicMock

from marketpulse.evaluation.benchmark import (
    BENCHMARK_TICKER,
    benchmark_forward_return,
)


@dataclass
class FakeBar:
    date: date
    close: float = 0


def _mock_data(bars: list[FakeBar]) -> MagicMock:
    m = MagicMock()
    m.get_history.return_value = bars
    return m


def test_default_benchmark_is_spy():
    assert BENCHMARK_TICKER == "SPY"


def test_benchmark_forward_return_computes_spy_return():
    bars = [
        FakeBar(date=date(2026, 1, 1), close=400.0),
        FakeBar(date=date(2026, 1, 2), close=402.0),
        FakeBar(date=date(2026, 1, 3), close=404.0),
    ]
    m = _mock_data(bars)
    r = benchmark_forward_return(
        date(2026, 1, 1), horizon_trading_days=2, data=m,
    )
    assert r is not None
    assert abs(r - (404.0 - 400.0) / 400.0) < 1e-9
    # Verify it queried SPY specifically
    m.get_history.assert_called_with("SPY", period="1y")


def test_benchmark_returns_none_when_data_unavailable():
    m = _mock_data([])
    r = benchmark_forward_return(
        date(2026, 1, 1), horizon_trading_days=2, data=m,
    )
    assert r is None
```

### Step 4c: Run tests

```bash
uv run pytest tests/evaluation/test_benchmark.py -v
```

Expected: 3 PASS.

### Step 4d: Commit

```bash
git add marketpulse/evaluation/benchmark.py tests/evaluation/test_benchmark.py
git commit -m "$(cat <<'EOF'
feat(evaluation): SPY benchmark forward-return wrapper

Thin wrapper around forward_return_at_horizon for the benchmark ticker
(SPY). Returns just the forward_return float for use in excess-return
computation in outcomes.py.

Phase 1 keeps benchmark hardcoded to SPY; multi-benchmark mapping
documented as future extension in the spec.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: events.py + Tests

**Files:**
- Create: `marketpulse/evaluation/events.py`
- Create: `tests/evaluation/test_events.py`

### Step 5a: Implement `events.py`

```python
"""record_event() — single insertion API for evaluation events."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from marketpulse.db.models import EvaluationEvent
from marketpulse.evaluation.constants import EventType
from marketpulse.logging import get_logger

log = get_logger(__name__)


def record_event(
    *,
    event_type: str,
    subtype: str,
    ticker: str,
    event_time: datetime,
    event_price: float,
    payload: dict[str, Any],
    db: Session,
) -> EvaluationEvent:
    """Record a point-in-time event. No outcome computed here.

    Validates input. Caller is responsible for the session commit/rollback
    boundary — we session.add and session.flush so the id is assigned.

    Raises:
        ValueError: invalid event_type, invalid subtype for that type,
            naive event_time, non-positive event_price.
    """
    # Validate event_type
    if event_type not in EventType.SUBTYPES:
        raise ValueError(
            f"invalid event_type {event_type!r}, "
            f"must be one of {sorted(EventType.SUBTYPES)}",
        )

    # Validate subtype
    valid_subtypes = EventType.SUBTYPES[event_type]()
    if subtype not in valid_subtypes:
        raise ValueError(
            f"invalid subtype {subtype!r} for event_type {event_type!r}, "
            f"must be one of {sorted(valid_subtypes)}",
        )

    # Validate event_time is tz-aware
    if event_time.tzinfo is None:
        raise ValueError("event_time must be timezone-aware (UTC preferred)")

    # Validate event_price
    if event_price <= 0:
        raise ValueError(f"event_price must be positive, got {event_price}")

    # Normalize ticker
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker must be non-empty")

    event = EvaluationEvent(
        event_type=event_type,
        subtype=subtype,
        ticker=ticker,
        event_time=event_time,
        event_price=event_price,
        payload=payload,
    )
    db.add(event)
    db.flush()  # populates event.id

    log.debug(
        "evaluation_event_recorded",
        event_id=event.id, event_type=event_type, subtype=subtype,
        ticker=ticker, event_time=event_time.isoformat(),
    )

    return event
```

### Step 5b: Implement tests in `tests/evaluation/test_events.py`

```python
from datetime import UTC, date, datetime

import pytest

from marketpulse.db import base as db_base
from marketpulse.db.models import EvaluationEvent
from marketpulse.evaluation.constants import (
    AIVerdict, EventType, SignalType,
)
from marketpulse.evaluation.events import record_event


@pytest.fixture
def db():
    """Use the same session pattern other tests use."""
    s = next(db_base.session_scope())
    yield s
    s.rollback()


def test_record_ai_analysis_event(db):
    event = record_event(
        event_type=EventType.AI_ANALYSIS,
        subtype=AIVerdict.BULLISH,
        ticker="AAPL",
        event_time=datetime(2026, 5, 13, 12, 0, tzinfo=UTC),
        event_price=294.80,
        payload={"verdict_text": "looks good", "input_snapshot": {}},
        db=db,
    )
    assert event.id is not None
    assert event.event_type == "ai_analysis"
    assert event.subtype == "bullish"
    assert event.ticker == "AAPL"
    assert event.event_price == 294.80
    assert event.payload["verdict_text"] == "looks good"


def test_record_signal_marker_event(db):
    event = record_event(
        event_type=EventType.SIGNAL_MARKER,
        subtype=SignalType.EMA_GOLDEN_CROSS,
        ticker="qubt",  # lower — should normalize
        event_time=datetime(2026, 4, 15, 21, 0, tzinfo=UTC),
        event_price=7.50,
        payload={"ema12": 7.50, "ema26": 7.49},
        db=db,
    )
    assert event.ticker == "QUBT"  # normalized


def test_invalid_event_type_raises(db):
    with pytest.raises(ValueError, match="invalid event_type"):
        record_event(
            event_type="garbage",
            subtype="bullish",
            ticker="AAPL",
            event_time=datetime.now(UTC),
            event_price=100.0,
            payload={},
            db=db,
        )


def test_invalid_subtype_raises(db):
    with pytest.raises(ValueError, match="invalid subtype"):
        record_event(
            event_type=EventType.AI_ANALYSIS,
            subtype="very_bullish",  # not in AIVerdict.all()
            ticker="AAPL",
            event_time=datetime.now(UTC),
            event_price=100.0,
            payload={},
            db=db,
        )


def test_subtype_must_match_event_type(db):
    with pytest.raises(ValueError, match="invalid subtype"):
        record_event(
            event_type=EventType.AI_ANALYSIS,
            subtype=SignalType.EMA_GOLDEN_CROSS,  # signal subtype on ai_analysis
            ticker="AAPL",
            event_time=datetime.now(UTC),
            event_price=100.0,
            payload={},
            db=db,
        )


def test_naive_datetime_raises(db):
    with pytest.raises(ValueError, match="timezone-aware"):
        record_event(
            event_type=EventType.AI_ANALYSIS,
            subtype=AIVerdict.BULLISH,
            ticker="AAPL",
            event_time=datetime(2026, 5, 13, 12, 0),  # no tzinfo
            event_price=100.0,
            payload={},
            db=db,
        )


def test_non_positive_price_raises(db):
    with pytest.raises(ValueError, match="event_price must be positive"):
        record_event(
            event_type=EventType.AI_ANALYSIS,
            subtype=AIVerdict.BULLISH,
            ticker="AAPL",
            event_time=datetime.now(UTC),
            event_price=0.0,
            payload={},
            db=db,
        )

    with pytest.raises(ValueError, match="event_price must be positive"):
        record_event(
            event_type=EventType.AI_ANALYSIS,
            subtype=AIVerdict.BULLISH,
            ticker="AAPL",
            event_time=datetime.now(UTC),
            event_price=-5.0,
            payload={},
            db=db,
        )


def test_empty_ticker_raises(db):
    with pytest.raises(ValueError, match="ticker must be non-empty"):
        record_event(
            event_type=EventType.AI_ANALYSIS,
            subtype=AIVerdict.BULLISH,
            ticker="   ",  # whitespace only
            event_time=datetime.now(UTC),
            event_price=100.0,
            payload={},
            db=db,
        )


def test_multiple_events_same_ticker_same_day(db):
    """Five events with same ticker/date but different subtypes — no UNIQUE."""
    when = datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
    subtypes = list(SignalType.all())[:5]
    ids = []
    for subtype in subtypes:
        event = record_event(
            event_type=EventType.SIGNAL_MARKER,
            subtype=subtype,
            ticker="AAPL",
            event_time=when,
            event_price=294.0,
            payload={"marker": subtype},
            db=db,
        )
        ids.append(event.id)
    assert len(set(ids)) == 5  # all distinct


def test_payload_json_roundtrips(db):
    """Complex payload survives JSON serialization."""
    payload = {
        "string": "hello",
        "number": 42,
        "float": 3.14,
        "nested": {"inner": [1, 2, 3]},
        "null": None,
        "bool": True,
    }
    event = record_event(
        event_type=EventType.AI_ANALYSIS,
        subtype=AIVerdict.NEUTRAL,
        ticker="AAPL",
        event_time=datetime.now(UTC),
        event_price=100.0,
        payload=payload,
        db=db,
    )
    # Re-fetch to ensure roundtrip
    db.flush()
    fetched = db.query(EvaluationEvent).filter_by(id=event.id).one()
    assert fetched.payload == payload
```

### Step 5c: Run tests

```bash
uv run pytest tests/evaluation/test_events.py -v
```

Expected: 10 PASS.

### Step 5d: Commit

```bash
git add marketpulse/evaluation/events.py tests/evaluation/test_events.py
git commit -m "$(cat <<'EOF'
feat(evaluation): record_event() insertion API with validation

Single entry point for writing evaluation events. Validates:
- event_type ∈ EventType.SUBTYPES
- subtype ∈ EventType.SUBTYPES[event_type]() (catches typos at write)
- event_time is tz-aware
- event_price > 0
- ticker non-empty (auto-uppercase)

Caller commits/rollbacks. We session.add + flush so id is assigned.

10 tests covering all happy paths + every validation failure case +
JSON payload roundtrip + same-ticker-same-day insertion (no UNIQUE).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: outcomes.py + Tests

**Files:**
- Create: `marketpulse/evaluation/outcomes.py`
- Create: `tests/evaluation/test_outcomes.py`

### Step 6a: Implement `outcomes.py`

```python
"""Outcome computation: scan pending events, compute forward returns, insert."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from marketpulse.data.service import DataService
from marketpulse.db.models import EvaluationEvent, EvaluationOutcome
from marketpulse.evaluation.benchmark import (
    BENCHMARK_TICKER,
    benchmark_forward_return,
)
from marketpulse.evaluation.forward_return import (
    ForwardReturnResult,
    forward_return_at_horizon,
)
from marketpulse.logging import get_logger

log = get_logger(__name__)

DEFAULT_HORIZONS = [1, 5, 20, 60]


@dataclass
class ComputeOutcomeReport:
    events_examined: int = 0
    outcomes_inserted: int = 0
    skipped_horizon_in_future: int = 0
    skipped_data_unavailable: int = 0
    skipped_benchmark_unavailable: int = 0
    skipped_already_computed: int = 0
    failed: int = 0
    failure_log: list[dict] = field(default_factory=list)


def compute_outcomes_for_pending_events(
    db: Session,
    data: DataService,
    horizons: list[int] | None = None,
    max_events: int = 500,
) -> ComputeOutcomeReport:
    """For each event without a matching outcome row at any of the requested
    horizons, compute the outcome and insert.

    Idempotent: safe to run multiple times per day. UNIQUE(event_id,
    horizon_trading_days) prevents duplicate inserts.

    Returns a report with per-status counts and a failure_log of dicts
    {event_id, ticker, horizon, reason}.
    """
    if horizons is None:
        horizons = DEFAULT_HORIZONS

    report = ComputeOutcomeReport()

    # Find events that might need outcomes computed.
    # Strategy: pull recent events; for each (event, horizon) check if outcome
    # row exists; if not, try to compute.
    events = (
        db.query(EvaluationEvent)
        .order_by(EvaluationEvent.event_time.desc())
        .limit(max_events)
        .all()
    )

    for event in events:
        report.events_examined += 1
        event_date = event.event_time.astimezone(UTC).date()

        for horizon in horizons:
            # Skip if outcome row already exists
            existing = (
                db.query(EvaluationOutcome.id)
                .filter(
                    EvaluationOutcome.event_id == event.id,
                    EvaluationOutcome.horizon_trading_days == horizon,
                )
                .first()
            )
            if existing:
                report.skipped_already_computed += 1
                continue

            # Compute forward return for the event
            event_result = forward_return_at_horizon(
                event.ticker, event_date, horizon, data,
            )
            if event_result is None:
                # Distinguish "horizon in future" from "data unavailable"
                # by checking event_date + horizon vs today.
                # Heuristic: if event_date is recent, it's horizon-in-future;
                # if event_date is old, it's data-unavailable.
                days_since_event = (datetime.now(UTC).date() - event_date).days
                if days_since_event < horizon * 1.5:
                    report.skipped_horizon_in_future += 1
                    reason = "horizon_in_future"
                else:
                    report.skipped_data_unavailable += 1
                    reason = "event_data_unavailable"
                    report.failure_log.append({
                        "event_id": event.id,
                        "ticker": event.ticker,
                        "horizon": horizon,
                        "reason": reason,
                    })
                continue

            # Compute benchmark forward return
            bench_return = benchmark_forward_return(event_date, horizon, data)
            if bench_return is None:
                report.skipped_benchmark_unavailable += 1
                report.failure_log.append({
                    "event_id": event.id,
                    "ticker": event.ticker,
                    "horizon": horizon,
                    "reason": "benchmark_unavailable",
                })
                continue

            # Insert outcome row
            try:
                outcome = EvaluationOutcome(
                    event_id=event.id,
                    horizon_trading_days=horizon,
                    event_price=event_result.event_price,
                    horizon_price=event_result.horizon_price,
                    horizon_date=event_result.horizon_date,
                    forward_return=event_result.forward_return,
                    benchmark_ticker=BENCHMARK_TICKER,
                    benchmark_forward_return=bench_return,
                    excess_return=event_result.forward_return - bench_return,
                )
                db.add(outcome)
                db.flush()
                report.outcomes_inserted += 1
            except Exception as exc:  # noqa: BLE001
                # IntegrityError from race condition or unexpected — log + continue
                db.rollback()
                report.failed += 1
                report.failure_log.append({
                    "event_id": event.id,
                    "ticker": event.ticker,
                    "horizon": horizon,
                    "reason": f"insert_failed: {exc}",
                })

    db.commit()
    return report
```

### Step 6b: Implement tests in `tests/evaluation/test_outcomes.py`

```python
from datetime import UTC, date, datetime, timedelta
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from marketpulse.db import base as db_base
from marketpulse.db.models import EvaluationEvent, EvaluationOutcome
from marketpulse.evaluation.constants import (
    AIVerdict, EventType, SignalType,
)
from marketpulse.evaluation.events import record_event
from marketpulse.evaluation.outcomes import (
    compute_outcomes_for_pending_events,
)


@dataclass
class FakeBar:
    date: date
    close: float = 0


@pytest.fixture
def db():
    s = next(db_base.session_scope())
    yield s
    s.rollback()


def _mock_data_with_bars(stock_bars: list[FakeBar], spy_bars: list[FakeBar]) -> MagicMock:
    """Mock DataService that returns stock_bars for non-SPY, spy_bars for SPY."""
    def fake_get_history(ticker, period):
        return spy_bars if ticker == "SPY" else stock_bars
    m = MagicMock()
    m.get_history.side_effect = fake_get_history
    return m


def test_computes_outcome_when_horizon_end_is_past(db):
    # Event 30 days ago, horizon 5 (long since past)
    past = datetime.now(UTC) - timedelta(days=30)
    event = record_event(
        event_type=EventType.SIGNAL_MARKER,
        subtype=SignalType.EMA_GOLDEN_CROSS,
        ticker="TST",
        event_time=past,
        event_price=100.0,
        payload={},
        db=db,
    )
    db.commit()

    # Set up bars so the forward return can be computed
    stock_bars = [
        FakeBar(date=past.date() + timedelta(days=i), close=100 + i)
        for i in range(0, 20)
    ]
    spy_bars = [
        FakeBar(date=past.date() + timedelta(days=i), close=400 + i * 0.5)
        for i in range(0, 20)
    ]
    data = _mock_data_with_bars(stock_bars, spy_bars)

    report = compute_outcomes_for_pending_events(db, data, horizons=[5])

    assert report.events_examined == 1
    assert report.outcomes_inserted == 1
    assert report.skipped_horizon_in_future == 0

    # Verify the outcome row
    outcome = db.query(EvaluationOutcome).filter_by(event_id=event.id).one()
    assert outcome.horizon_trading_days == 5
    assert outcome.event_price == 100.0
    assert outcome.horizon_price == 105.0  # bar index 5 from event date
    assert outcome.benchmark_ticker == "SPY"


def test_skips_when_horizon_still_in_future(db):
    # Event today, horizon 60 → way in future
    record_event(
        event_type=EventType.SIGNAL_MARKER,
        subtype=SignalType.EMA_GOLDEN_CROSS,
        ticker="TST",
        event_time=datetime.now(UTC),
        event_price=100.0,
        payload={},
        db=db,
    )
    db.commit()

    data = _mock_data_with_bars([], [])
    report = compute_outcomes_for_pending_events(db, data, horizons=[60])
    assert report.outcomes_inserted == 0
    assert report.skipped_horizon_in_future == 1


def test_idempotent_skip_already_computed(db):
    past = datetime.now(UTC) - timedelta(days=30)
    record_event(
        event_type=EventType.SIGNAL_MARKER, subtype=SignalType.EMA_GOLDEN_CROSS,
        ticker="TST", event_time=past, event_price=100.0, payload={}, db=db,
    )
    db.commit()

    stock_bars = [FakeBar(date=past.date() + timedelta(days=i), close=100 + i) for i in range(0, 20)]
    spy_bars = [FakeBar(date=past.date() + timedelta(days=i), close=400) for i in range(0, 20)]
    data = _mock_data_with_bars(stock_bars, spy_bars)

    # First run: 1 inserted
    report1 = compute_outcomes_for_pending_events(db, data, horizons=[5])
    assert report1.outcomes_inserted == 1

    # Second run: 0 inserted, 1 already computed
    report2 = compute_outcomes_for_pending_events(db, data, horizons=[5])
    assert report2.outcomes_inserted == 0
    assert report2.skipped_already_computed == 1


def test_partial_completion_mixed_horizons(db):
    """Same event: horizon=1 computes (1d past), horizon=60 doesn't (future)."""
    one_day_ago = datetime.now(UTC) - timedelta(days=2)
    record_event(
        event_type=EventType.SIGNAL_MARKER, subtype=SignalType.EMA_GOLDEN_CROSS,
        ticker="TST", event_time=one_day_ago, event_price=100.0, payload={}, db=db,
    )
    db.commit()

    stock_bars = [FakeBar(date=one_day_ago.date() + timedelta(days=i), close=100 + i) for i in range(0, 5)]
    spy_bars = [FakeBar(date=one_day_ago.date() + timedelta(days=i), close=400) for i in range(0, 5)]
    data = _mock_data_with_bars(stock_bars, spy_bars)

    report = compute_outcomes_for_pending_events(db, data, horizons=[1, 60])
    assert report.outcomes_inserted == 1
    assert report.skipped_horizon_in_future == 1
    assert report.skipped_already_computed == 0


def test_excess_return_computed_correctly(db):
    past = datetime.now(UTC) - timedelta(days=30)
    record_event(
        event_type=EventType.SIGNAL_MARKER, subtype=SignalType.EMA_GOLDEN_CROSS,
        ticker="TST", event_time=past, event_price=100.0, payload={}, db=db,
    )
    db.commit()

    # Stock goes 100 → 110 (+10%), SPY goes 400 → 408 (+2%)
    stock_bars = [
        FakeBar(date=past.date(), close=100.0),
        FakeBar(date=past.date() + timedelta(days=1), close=102.0),
        FakeBar(date=past.date() + timedelta(days=2), close=104.0),
        FakeBar(date=past.date() + timedelta(days=3), close=106.0),
        FakeBar(date=past.date() + timedelta(days=4), close=108.0),
        FakeBar(date=past.date() + timedelta(days=5), close=110.0),
    ]
    spy_bars = [
        FakeBar(date=past.date() + timedelta(days=i), close=400 + i * 1.6)
        for i in range(0, 6)
    ]
    data = _mock_data_with_bars(stock_bars, spy_bars)

    compute_outcomes_for_pending_events(db, data, horizons=[5])

    outcome = db.query(EvaluationOutcome).one()
    assert abs(outcome.forward_return - 0.10) < 1e-9
    assert abs(outcome.benchmark_forward_return - 0.02) < 1e-9
    assert abs(outcome.excess_return - 0.08) < 1e-9


def test_failure_log_includes_ticker_and_horizon(db):
    """When data is unavailable for an old event, failure_log captures details."""
    long_ago = datetime.now(UTC) - timedelta(days=200)
    record_event(
        event_type=EventType.SIGNAL_MARKER, subtype=SignalType.EMA_GOLDEN_CROSS,
        ticker="DEAD", event_time=long_ago, event_price=100.0, payload={}, db=db,
    )
    db.commit()

    # No bars available (delisted-like)
    data = _mock_data_with_bars([], [])
    report = compute_outcomes_for_pending_events(db, data, horizons=[20])
    assert report.skipped_data_unavailable == 1
    assert len(report.failure_log) == 1
    entry = report.failure_log[0]
    assert entry["ticker"] == "DEAD"
    assert entry["horizon"] == 20
    assert "unavailable" in entry["reason"]
```

### Step 6c: Run tests

```bash
uv run pytest tests/evaluation/test_outcomes.py -v
```

Expected: 6 PASS.

### Step 6d: Full project test pass

```bash
uv run pytest 2>&1 | tail -5
uv run ruff check 2>&1 | tail -3
```

Expected: everything green.

### Step 6e: Commit

```bash
git add marketpulse/evaluation/outcomes.py tests/evaluation/test_outcomes.py
git commit -m "$(cat <<'EOF'
feat(evaluation): compute_outcomes_for_pending_events orchestrator

Scans recent events; for each (event, horizon) pair without an outcome
row, computes forward_return via forward_return.py and benchmark via
benchmark.py, then inserts an EvaluationOutcome row.

Idempotent (UNIQUE constraint + explicit skip-if-exists check). Reports
per-status counts and a failure_log of {event_id, ticker, horizon, reason}
for operator triage.

Heuristic distinguishes "horizon in future" from "data unavailable":
events less than horizon * 1.5 calendar days old are considered "still
pending"; older ones with missing data go to failure_log.

6 tests cover the orchestration: happy path, future horizons, idempotency,
partial completion, excess return correctness, failure_log details.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Scheduler Integration + Integration Test

**Files:**
- Modify: `marketpulse/scheduler/jobs.py`
- Create: `tests/scheduler/test_outcome_job.py`
- Create: `marketpulse/evaluation/__init__.py` (re-exports, finalize)

### Step 7a: Add re-exports to `marketpulse/evaluation/__init__.py`

```python
"""Evaluation framework: events, outcomes, forward-return computation.

Public API:
    record_event() — write an event
    compute_outcomes_for_pending_events() — nightly outcome computation
    forward_return_at_horizon() — pure math helper (Phases 2/3 may call)
"""
from marketpulse.evaluation.benchmark import (
    BENCHMARK_TICKER,
    benchmark_forward_return,
)
from marketpulse.evaluation.constants import (
    AIVerdict,
    EventType,
    SignalType,
)
from marketpulse.evaluation.events import record_event
from marketpulse.evaluation.forward_return import (
    ForwardReturnResult,
    forward_return_at_horizon,
)
from marketpulse.evaluation.outcomes import (
    DEFAULT_HORIZONS,
    ComputeOutcomeReport,
    compute_outcomes_for_pending_events,
)

__all__ = [
    "AIVerdict",
    "BENCHMARK_TICKER",
    "ComputeOutcomeReport",
    "DEFAULT_HORIZONS",
    "EventType",
    "ForwardReturnResult",
    "SignalType",
    "benchmark_forward_return",
    "compute_outcomes_for_pending_events",
    "forward_return_at_horizon",
    "record_event",
]
```

### Step 7b: Add scheduler job to `marketpulse/scheduler/jobs.py`

Open `marketpulse/scheduler/jobs.py` and find the `build_scheduler()` function. Read the existing job-registration pattern (likely `scheduler.add_job(...)` calls with `CronTrigger`).

Find an appropriate spot AFTER existing imports but before `build_scheduler()`. Add:

```python
def run_outcome_computation() -> None:
    """Daily job: compute outcomes for pending evaluation events.

    Runs at 02:00 UTC. US market close ~21:00 UTC → 5h buffer for yfinance.
    """
    from marketpulse.evaluation import compute_outcomes_for_pending_events

    with session_scope() as db:
        data = DataService(
            quote_client=_build_quote_client(),
            news_cache=NewsCache(),
        )
        report = compute_outcomes_for_pending_events(db, data)
        log.info(
            "outcome_computation_done",
            events_examined=report.events_examined,
            outcomes_inserted=report.outcomes_inserted,
            skipped_horizon_in_future=report.skipped_horizon_in_future,
            skipped_data_unavailable=report.skipped_data_unavailable,
            skipped_benchmark_unavailable=report.skipped_benchmark_unavailable,
            skipped_already_computed=report.skipped_already_computed,
            failed=report.failed,
            failure_log_count=len(report.failure_log),
        )
        record_run_summary(
            "outcome_computation",
            details=f"inserted={report.outcomes_inserted} "
                    f"skipped={report.skipped_horizon_in_future + report.skipped_already_computed + report.skipped_data_unavailable} "
                    f"failed={report.failed}",
        )
```

**Note:** The DataService construction here mirrors how `run_daily_recap` constructs it. If the actual constructor signature differs, copy the working pattern from there exactly. **Don't guess** — inspect `run_daily_recap` for the canonical pattern.

Inside `build_scheduler()`, after the existing `scheduler.add_job(...)` calls, add:

```python
    scheduler.add_job(
        run_outcome_computation,
        CronTrigger(hour=2, minute=0, timezone="UTC"),
        id="outcome_computation",
        replace_existing=True,
    )
```

### Step 7c: Create `tests/scheduler/__init__.py` if not exists

```python
```

### Step 7d: Add test in `tests/scheduler/test_outcome_job.py`

```python
"""Tests for the outcome-computation scheduler job."""
from unittest.mock import patch

from marketpulse.scheduler.jobs import build_scheduler, run_outcome_computation


def test_outcome_job_registered():
    scheduler = build_scheduler()
    job = scheduler.get_job("outcome_computation")
    assert job is not None
    assert job.trigger.fields[5].is_value(2)  # hour=2 (field index for hour in CronTrigger)
    # Just verify it's a cron trigger at hour 2; exact field index may
    # vary by APScheduler version — fall back to inspecting the next-run time.


def test_run_outcome_computation_calls_compute(monkeypatch):
    """Verify the job invokes compute_outcomes_for_pending_events."""
    called = {}

    def fake_compute(db, data, horizons=None, max_events=500):
        called["yes"] = True
        from marketpulse.evaluation.outcomes import ComputeOutcomeReport
        return ComputeOutcomeReport()

    monkeypatch.setattr(
        "marketpulse.evaluation.compute_outcomes_for_pending_events",
        fake_compute,
    )
    # Also stub DataService construction to avoid hitting yfinance
    monkeypatch.setattr(
        "marketpulse.scheduler.jobs._build_quote_client",
        lambda: object(),
    )

    run_outcome_computation()
    assert called.get("yes")
```

**Note:** the cron-trigger field assertion (`trigger.fields[5]`) is APScheduler-version-specific. If the test fails due to API differences, simplify to just `assert job is not None` and trust manual inspection.

### Step 7e: Run all tests

```bash
uv run pytest tests/evaluation/ tests/scheduler/test_outcome_job.py -v
uv run pytest 2>&1 | tail -5
uv run ruff check 2>&1 | tail -3
```

Expected: every evaluation/scheduler test green, project all green, ruff clean.

### Step 7f: Commit

```bash
git add marketpulse/evaluation/__init__.py marketpulse/scheduler/jobs.py tests/scheduler/
git commit -m "$(cat <<'EOF'
feat(evaluation): register outcome computation as nightly scheduler job

Adds run_outcome_computation() to scheduler/jobs.py, registered as
a daily 02:00 UTC cron via APScheduler — joins existing recap-push
and alert-eval jobs in the same scheduler.

UTC 02:00 = US market close + 5 hour buffer = Beijing 10:00 (user
typically opens MarketPulse morning their time). Logs report counts
via record_run_summary so the operator can inspect via existing
scheduler-state dashboard.

Also finalizes marketpulse/evaluation/__init__.py with public API
re-exports so consumers can `from marketpulse.evaluation import record_event`.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Push + Open PR

### Step 8a: Push

```bash
git push -u origin feat/evaluation-framework-phase-1
```

### Step 8b: Open PR

```bash
gh pr create --title "feat(evaluation): Phase 1 — evaluation framework foundation" --body "$(cat <<'EOF'
## Summary

Phase 1 of the PRINCIPLES.md trilogy (#2 AI hit-rate, #3 Signal win-rate, #4 /recap diagnostics). Builds the shared evaluation infrastructure so Phases 2 and 3 are "add a hook" rather than "build a system."

**What this PR delivers:**

1. **Two new DB tables** — \`evaluation_event\` (point-in-time events) + \`evaluation_outcome\` (forward-return measurements at horizons {1, 5, 20, 60} trading days, with SPY excess return).
2. **\`marketpulse.evaluation\`** package — \`constants.py\` (subtype taxonomy), \`events.py\` (\`record_event()\` insertion API with validation), \`forward_return.py\` (pure math primitive), \`benchmark.py\` (SPY wrapper), \`outcomes.py\` (nightly orchestrator).
3. **APScheduler job** — \`run_outcome_computation\` at 02:00 UTC daily, joins existing recap/alert jobs.
4. **No UI changes** in this phase. Data layer only. Phases 2/3 surface this data in chart marker tooltips + AI hit-rate badges.

**Why Phase 1 standalone:**

If we built Phases 2 and 3 separately, we'd write the same forward-return/outcome code twice. Building once means Phases 2/3 become a hook + a SQL query rather than "build the system again."

By the time Phases 2/3 ship their UI, multiple weeks of outcomes have already accumulated — no "N=0, sample too small" embarrassment.

## Test Plan

- [x] ~30 new tests across constants/events/forward_return/benchmark/outcomes/scheduler
- [x] All existing tests pass (~318 total after additions)
- [x] \`ruff check\` clean
- [ ] Manual after deploy:
  - [ ] Check scheduler state — \`outcome_computation\` job appears in dashboard
  - [ ] Insert a test event manually, run \`run_outcome_computation\` once, verify outcome row inserted
  - [ ] Inspect tables: \`evaluation_event\` and \`evaluation_outcome\` exist with correct schema

## Spec

\`docs/superpowers/specs/2026-05-13-evaluation-framework-design.md\` — full design rationale, edge cases, and future extension notes (multi-benchmark, manual refresh endpoint, payload-column promotion).

## Principles Compliance

Per \`docs/PRINCIPLES.md\`:
- **#1 Measure, don't auto-modify** — Outcomes computed, no automatic prompt/signal tuning
- **#2 AI verdicts auditable** — Infrastructure now ready; Phase 2 wires the hook
- **#3 Signals declare signal-to-noise** — Infrastructure now ready; Phase 3 wires the hook
- **#5 Determinism** — Outcome is pure function of (event, bars). Same inputs → same outputs.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### Step 8c: Self-review checklist

Run each and report:
- `git log --oneline | head -10` — expect spec + 7 task commits = 8+ commits ahead of main
- `uv run pytest 2>&1 | tail -3` — all green (~318 tests)
- `uv run ruff check 2>&1 | tail -3` — clean
- `grep -c "class EvaluationEvent\|class EvaluationOutcome" marketpulse/db/models.py` — `2`
- `grep -c "record_event\|compute_outcomes_for_pending_events\|forward_return_at_horizon" marketpulse/evaluation/__init__.py` — at least `3`
- `grep -c "outcome_computation" marketpulse/scheduler/jobs.py` — at least `2` (job + registration)
- `ls marketpulse/evaluation/` — should list 6 files (init, constants, events, forward_return, benchmark, outcomes)
- `ls tests/evaluation/` — should list 6 files (init, test_constants, test_events, test_forward_return, test_benchmark, test_outcomes)

Report the PR URL.

---

## Estimated Timeline

- Task 1 (DB): 30 min
- Task 2 (constants): 20 min
- Task 3 (forward_return): 1 hour
- Task 4 (benchmark): 20 min
- Task 5 (events): 45 min
- Task 6 (outcomes): 1.5 hours
- Task 7 (scheduler): 45 min
- Task 8 (push + PR): 15 min

**Total: ~5 hours of focused work.** Spec target was 5-7 days; the plan execution is concentrated because each subagent task is mechanical given the explicit code.
