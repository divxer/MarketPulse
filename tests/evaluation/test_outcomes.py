from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from marketpulse.db import base as db_base
from marketpulse.db.models import EvaluationOutcome
from marketpulse.evaluation.constants import EventType, SignalType
from marketpulse.evaluation.events import record_event
from marketpulse.evaluation.outcomes import (
    compute_outcomes_for_pending_events,
)


@dataclass
class FakeBar:
    date: date
    close: float = 0


@pytest.fixture
def db(tmp_path):
    """Isolated per-test SQLite DB — prevents data bleeding between tests."""
    from marketpulse.db.base import Base
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    db_base.init_engine(db_url)
    Base.metadata.create_all(db_base.get_engine())
    s = next(db_base.session_scope())
    yield s
    s.close()
    db_base.reset_engine()


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

    stock_bars = [
        FakeBar(date=past.date() + timedelta(days=i), close=100 + i) for i in range(0, 20)
    ]
    spy_bars = [
        FakeBar(date=past.date() + timedelta(days=i), close=400) for i in range(0, 20)
    ]
    data = _mock_data_with_bars(stock_bars, spy_bars)

    # First run: 1 inserted
    report1 = compute_outcomes_for_pending_events(db, data, horizons=[5])
    assert report1.outcomes_inserted == 1

    # Second run: 0 inserted, 1 already computed
    report2 = compute_outcomes_for_pending_events(db, data, horizons=[5])
    assert report2.outcomes_inserted == 0
    assert report2.skipped_already_computed == 1


def test_partial_completion_mixed_horizons(db):
    """Same event: horizon=1 computes (2d past), horizon=60 doesn't (future)."""
    two_days_ago = datetime.now(UTC) - timedelta(days=2)
    record_event(
        event_type=EventType.SIGNAL_MARKER, subtype=SignalType.EMA_GOLDEN_CROSS,
        ticker="TST", event_time=two_days_ago, event_price=100.0, payload={}, db=db,
    )
    db.commit()

    stock_bars = [
        FakeBar(date=two_days_ago.date() + timedelta(days=i), close=100 + i)
        for i in range(0, 5)
    ]
    spy_bars = [
        FakeBar(date=two_days_ago.date() + timedelta(days=i), close=400)
        for i in range(0, 5)
    ]
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


def test_naive_event_time_from_sqlite_does_not_shift_event_date(db):
    """Regression: SQLite + DateTime(timezone=True) returns naive datetimes
    on read-back. A naive datetime's .astimezone(UTC) treats it as LOCAL
    time, which on non-UTC dev machines (e.g. PDT) shifts the date by 1
    and silently lookups bars at the wrong index.

    This test simulates the bug by directly inserting an event row with a
    naive datetime (mimicking what SQLite gives us back after commit on
    machines where SQLAlchemy doesn't restore tzinfo), then verifying that
    compute_outcomes_for_pending_events still computes the outcome at the
    correct bar index instead of shifting by 1.
    """
    past = datetime.now(UTC) - timedelta(days=30)
    record_event(
        event_type=EventType.SIGNAL_MARKER, subtype=SignalType.EMA_GOLDEN_CROSS,
        ticker="TST", event_time=past, event_price=100.0, payload={}, db=db,
    )
    db.commit()

    # Force the round-trip behavior: clear identity map and re-query so the
    # row comes back with whatever tzinfo state SQLite returned. On the
    # affected dev machines this drops tzinfo entirely.
    db.expire_all()

    # Bars exactly span [past.date(), past.date()+5]. Off-by-1 date shift
    # would land horizon_idx at 6 (out of range) → no outcome inserted.
    stock_bars = [
        FakeBar(date=past.date() + timedelta(days=i), close=100.0 + i * 2)
        for i in range(6)
    ]
    spy_bars = [
        FakeBar(date=past.date() + timedelta(days=i), close=400 + i * 1.6)
        for i in range(6)
    ]
    data = _mock_data_with_bars(stock_bars, spy_bars)

    compute_outcomes_for_pending_events(db, data, horizons=[5])

    # If the tz fix is in place, exactly one outcome lands at the right
    # bar (close=110 → forward_return=0.10). If the bug regresses, this
    # raises NoResultFound.
    outcome = db.query(EvaluationOutcome).one()
    assert abs(outcome.forward_return - 0.10) < 1e-9


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
