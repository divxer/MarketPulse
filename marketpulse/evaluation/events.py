"""record_event() — single insertion API for evaluation events."""
from __future__ import annotations

from datetime import datetime
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
