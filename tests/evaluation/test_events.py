from datetime import UTC, datetime

import pytest

from marketpulse.db import base as db_base
from marketpulse.db.models import EvaluationEvent
from marketpulse.evaluation.constants import (
    AIVerdict,
    EventType,
    SignalType,
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
