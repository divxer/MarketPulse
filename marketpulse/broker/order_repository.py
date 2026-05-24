"""Persistence helpers for Phase 7b broker order provenance.

Mirrors ``marketpulse/broker/repository.py`` (7a) but for the order-intent /
order-event tables introduced in T2. All operations go through a caller-owned
SQLAlchemy ``Session`` — this module never opens its own engine.

State machine (see plan L17/L18/L52):

    created → sent     (place: broker call attempted)
    created → rejected (CLI validation / safety rejection before broker call)
    created → failed   (connection / nextValidId fail before broker call)
    sent    → completed
    sent    → rejected
    sent    → failed
    * terminal states (completed, rejected, failed) have no outgoing edges

L66 idempotency UNIQUE: ``(account_id, action, local_idempotency_key)`` is a DB
constraint. ``create_intent`` translates the integrity violation into
``OrderDuplicateError`` so callers can map it to a clean CLI error.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import get_args

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marketpulse.broker.order_types import (
    EventType,
    IntentStatus,
    OrderDuplicateError,
    OrderStateTransitionError,
)
from marketpulse.db.models import BrokerOrderEvent, BrokerOrderIntent

# Allowed `event_type` literals (mirrors the CHECK on broker_order_event).
_VALID_EVENT_TYPES: frozenset[str] = frozenset(get_args(EventType))

# Allowed `status` literals (mirrors the CHECK on broker_order_intent).
_VALID_STATUSES: frozenset[str] = frozenset(get_args(IntentStatus))

# Status state machine — keys are source states, values are reachable targets.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"sent", "rejected", "failed"}),
    "sent": frozenset({"completed", "rejected", "failed"}),
    "completed": frozenset(),
    "rejected": frozenset(),
    "failed": frozenset(),
}

# Idempotency UNIQUE constraint name (see db/models.py:uq_broker_order_intent_idem).
_IDEM_CONSTRAINT = "uq_broker_order_intent_idem"


def create_intent(
    session: Session,
    *,
    action: str,
    broker: str,
    broker_environment: str,
    account_id: str,
    local_idempotency_key: str,
    context: dict,
    symbol: str | None = None,
    asset_class: str | None = None,
    side: str | None = None,
    quantity: Decimal | None = None,
    order_type: str | None = None,
    limit_price: Decimal | None = None,
    transmit: bool | None = None,
    parent_intent_id: int | None = None,
    operator_source: str = "cli",
    created_at: datetime | None = None,
) -> BrokerOrderIntent:
    """Insert a new intent row with status='created' and return the ORM object.

    Raises ``OrderDuplicateError`` when the
    ``(account_id, action, local_idempotency_key)`` UNIQUE constraint trips
    (L66). Any other ``IntegrityError`` is re-raised unchanged.
    """

    intent = BrokerOrderIntent(
        created_at=created_at or datetime.now(UTC),
        operator_source=operator_source,
        action=action,
        broker=broker,
        broker_environment=broker_environment,
        account_id=account_id,
        symbol=symbol,
        asset_class=asset_class,
        side=side,
        quantity=quantity,
        order_type=order_type,
        limit_price=limit_price,
        transmit=transmit,
        local_idempotency_key=local_idempotency_key,
        parent_intent_id=parent_intent_id,
        broker_order_id=None,
        broker_perm_id=None,
        status="created",
        context=context,
    )
    session.add(intent)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        if _is_idem_violation(exc):
            raise OrderDuplicateError(
                "duplicate place intent: "
                f"(account_id={account_id!r}, action={action!r}, "
                f"local_idempotency_key={local_idempotency_key!r}) already exists"
            ) from exc
        raise
    return intent


def _is_idem_violation(exc: IntegrityError) -> bool:
    """Best-effort detection of the idempotency UNIQUE violation.

    SQLAlchemy surfaces UNIQUE failures with the constraint name embedded in
    the driver message on both SQLite and Postgres; we look for either the
    constraint name or the case-insensitive word ``unique``. If we cannot tell,
    we conservatively return False so the original IntegrityError bubbles up.
    """

    msg = str(getattr(exc, "orig", exc) or exc)
    if _IDEM_CONSTRAINT in msg:
        return True
    lower = msg.lower()
    if "unique" not in lower:
        return False
    # Constraint-name probe failed but we still see "unique" — check that the
    # idempotency columns are named in the violation message, otherwise we
    # might be misclassifying some other UNIQUE on the table.
    return "local_idempotency_key" in lower or "idem" in lower


def append_event(
    session: Session,
    *,
    intent_id: int,
    event_type: str,
    event_source: str,
    observed_at: datetime | None = None,
    broker_order_id: str | None = None,
    broker_perm_id: str | None = None,
    broker_status: str | None = None,
    filled_quantity: Decimal | None = None,
    remaining_quantity: Decimal | None = None,
    avg_fill_price: Decimal | None = None,
    message: str | None = None,
    raw: dict | None = None,
) -> BrokerOrderEvent:
    """Append one immutable event row for ``intent_id``.

    Validates ``event_type`` against the 14-element whitelist before insert so
    the caller gets a precise ``ValueError`` instead of a generic CHECK
    failure. Raises ``LookupError`` if no intent has the given id.
    """

    if event_type not in _VALID_EVENT_TYPES:
        raise ValueError(
            f"event_type {event_type!r} is not one of {sorted(_VALID_EVENT_TYPES)}"
        )
    if session.get(BrokerOrderIntent, intent_id) is None:
        raise LookupError(f"broker_order_intent not found: {intent_id}")
    event = BrokerOrderEvent(
        intent_id=intent_id,
        observed_at=observed_at or datetime.now(UTC),
        event_type=event_type,
        event_source=event_source,
        broker_order_id=broker_order_id,
        broker_perm_id=broker_perm_id,
        broker_status=broker_status,
        filled_quantity=filled_quantity,
        remaining_quantity=remaining_quantity,
        avg_fill_price=avg_fill_price,
        message=message,
        raw=raw or {},
    )
    session.add(event)
    session.flush()
    return event


def transition_status(
    session: Session,
    *,
    intent_id: int,
    new_status: str,
) -> None:
    """Move ``intent.status`` to ``new_status`` per the state machine.

    Raises ``OrderStateTransitionError`` on any disallowed transition (including
    unknown target statuses and any move out of a terminal state).
    """

    if new_status not in _VALID_STATUSES:
        raise OrderStateTransitionError(
            f"new_status {new_status!r} is not a valid IntentStatus"
        )
    intent = session.get(BrokerOrderIntent, intent_id)
    if intent is None:
        raise LookupError(f"broker_order_intent not found: {intent_id}")
    allowed = _ALLOWED_TRANSITIONS.get(intent.status, frozenset())
    if new_status not in allowed:
        raise OrderStateTransitionError(
            f"disallowed transition: {intent.status!r} → {new_status!r} "
            f"(intent_id={intent_id})"
        )
    intent.status = new_status
    session.flush()


def set_broker_ids(
    session: Session,
    *,
    intent_id: int,
    broker_order_id: str | None = None,
    broker_perm_id: str | None = None,
) -> None:
    """Persist broker-assigned IDs on an intent (L43).

    Either or both fields may be supplied. ``None`` is treated as
    "do not touch" — to clear a column explicitly the caller should write SQL.
    """

    intent = session.get(BrokerOrderIntent, intent_id)
    if intent is None:
        raise LookupError(f"broker_order_intent not found: {intent_id}")
    if broker_order_id is not None:
        intent.broker_order_id = broker_order_id
    if broker_perm_id is not None:
        intent.broker_perm_id = broker_perm_id
    session.flush()


def get_intent_by_id(session: Session, intent_id: int) -> BrokerOrderIntent:
    """Return the intent with ``intent_id``; raise ``LookupError`` if missing."""

    intent = session.get(BrokerOrderIntent, intent_id)
    if intent is None:
        raise LookupError(f"broker_order_intent not found: {intent_id}")
    return intent


def get_intent_by_idempotency_key(
    session: Session,
    *,
    account_id: str,
    action: str,
    local_idempotency_key: str,
) -> BrokerOrderIntent | None:
    """Look up an intent by its idempotency triple; return ``None`` if absent."""

    stmt = select(BrokerOrderIntent).where(
        BrokerOrderIntent.account_id == account_id,
        BrokerOrderIntent.action == action,
        BrokerOrderIntent.local_idempotency_key == local_idempotency_key,
    )
    return session.execute(stmt).scalar_one_or_none()


def list_events_for_intent(
    session: Session,
    intent_id: int,
) -> list[BrokerOrderEvent]:
    """Return all events for ``intent_id`` ordered by ``observed_at``, then ``id``."""

    stmt = (
        select(BrokerOrderEvent)
        .where(BrokerOrderEvent.intent_id == intent_id)
        .order_by(BrokerOrderEvent.observed_at, BrokerOrderEvent.id)
    )
    return list(session.execute(stmt).scalars().all())
