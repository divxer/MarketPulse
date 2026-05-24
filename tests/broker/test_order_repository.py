# Layer: stateful
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from marketpulse.broker.order_types import (
    OrderDuplicateError,
    OrderStateTransitionError,
)
from marketpulse.db.base import Base
from marketpulse.db.models import BrokerOrderEvent, BrokerOrderIntent


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    # SQLite needs FK enforcement enabled per-connection for FK errors to surface.
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):  # pragma: no cover - tiny shim
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    return Session(engine)


def _count(session: Session, model) -> int:
    return int(session.execute(select(func.count(model.id))).scalar() or 0)


def _create_place_intent(
    session: Session,
    *,
    key: str = "key-1",
    account_id: str = "DU123456",
):
    from marketpulse.broker.order_repository import create_intent

    return create_intent(
        session,
        action="place",
        broker="IBKR",
        broker_environment="paper",
        account_id=account_id,
        local_idempotency_key=key,
        context={"cli": "place"},
        symbol="AAPL",
        asset_class="STK",
        side="BUY",
        quantity=Decimal("1"),
        order_type="LMT",
        limit_price=Decimal("1.00"),
        transmit=False,
    )


def test_create_intent_writes_row_and_defaults_status_to_created():
    session = _session()
    intent = _create_place_intent(session)
    session.commit()

    assert intent.id is not None
    assert intent.status == "created"
    assert intent.action == "place"
    assert intent.account_id == "DU123456"
    assert intent.local_idempotency_key == "key-1"
    assert intent.operator_source == "cli"
    assert intent.created_at is not None


def test_create_intent_duplicate_key_raises_order_duplicate_error():
    session = _session()
    _create_place_intent(session, key="dup")
    session.commit()
    with pytest.raises(OrderDuplicateError):
        _create_place_intent(session, key="dup")
    session.rollback()


def test_append_event_writes_row_with_fk_to_intent():
    from marketpulse.broker.order_repository import append_event

    session = _session()
    intent = _create_place_intent(session)
    session.commit()

    event = append_event(
        session,
        intent_id=intent.id,
        event_type="staged_to_tws",
        event_source="adapter_callback",
        observed_at=datetime(2026, 5, 24, 12, tzinfo=UTC),
        broker_order_id="1001",
        broker_status="PreSubmitted",
        raw={"status": "PreSubmitted"},
    )
    session.commit()

    assert event.id is not None
    assert event.intent_id == intent.id
    assert _count(session, BrokerOrderEvent) == 1


def test_append_event_raises_when_intent_id_missing():
    from marketpulse.broker.order_repository import append_event

    session = _session()
    with pytest.raises(LookupError):
        append_event(
            session,
            intent_id=99999,
            event_type="error",
            event_source="service_safety",
        )


@pytest.mark.parametrize(
    "event_type",
    [
        "safety_rejected",
        "connection_failed",
        "account_mismatch",
        "next_valid_id_received",
        "staged_to_tws",
        "submitted_to_broker",
        "open_order_seen",
        "order_status_seen",
        "broker_cancel_requested",
        "staged_cancelled",
        "cancelled",
        "filled",
        "rejected",
        "error",
    ],
)
def test_append_event_accepts_all_valid_event_types(event_type):
    from marketpulse.broker.order_repository import append_event

    session = _session()
    intent = _create_place_intent(session, key=f"k-{event_type}")
    session.commit()
    event = append_event(
        session,
        intent_id=intent.id,
        event_type=event_type,
        event_source="adapter_callback",
    )
    session.commit()
    assert event.event_type == event_type


def test_append_event_rejects_unknown_event_type():
    from marketpulse.broker.order_repository import append_event

    session = _session()
    intent = _create_place_intent(session)
    session.commit()
    with pytest.raises(ValueError):
        append_event(
            session,
            intent_id=intent.id,
            event_type="not_a_real_type",
            event_source="adapter_callback",
        )


def test_transition_status_allows_created_to_sent_then_completed():
    from marketpulse.broker.order_repository import transition_status

    session = _session()
    intent = _create_place_intent(session)
    session.commit()

    transition_status(session, intent_id=intent.id, new_status="sent")
    session.commit()
    assert session.get(BrokerOrderIntent, intent.id).status == "sent"

    transition_status(session, intent_id=intent.id, new_status="completed")
    session.commit()
    assert session.get(BrokerOrderIntent, intent.id).status == "completed"


@pytest.mark.parametrize("new_status", ["rejected", "failed"])
def test_transition_status_allows_created_to_terminal_failure(new_status):
    from marketpulse.broker.order_repository import transition_status

    session = _session()
    intent = _create_place_intent(session, key=f"k-{new_status}")
    session.commit()
    transition_status(session, intent_id=intent.id, new_status=new_status)
    session.commit()
    assert session.get(BrokerOrderIntent, intent.id).status == new_status


def test_transition_status_rejects_disallowed_transition():
    from marketpulse.broker.order_repository import transition_status

    session = _session()
    intent = _create_place_intent(session)
    session.commit()
    transition_status(session, intent_id=intent.id, new_status="sent")
    transition_status(session, intent_id=intent.id, new_status="completed")
    session.commit()

    with pytest.raises(OrderStateTransitionError):
        transition_status(session, intent_id=intent.id, new_status="sent")


def test_transition_status_rejects_invalid_target_status():
    from marketpulse.broker.order_repository import transition_status

    session = _session()
    intent = _create_place_intent(session)
    session.commit()
    with pytest.raises(OrderStateTransitionError):
        transition_status(session, intent_id=intent.id, new_status="bogus")


def test_transition_status_no_exit_from_terminal_state():
    from marketpulse.broker.order_repository import transition_status

    session = _session()
    intent = _create_place_intent(session)
    session.commit()
    transition_status(session, intent_id=intent.id, new_status="rejected")
    with pytest.raises(OrderStateTransitionError):
        transition_status(session, intent_id=intent.id, new_status="sent")


def test_set_broker_ids_updates_intent():
    from marketpulse.broker.order_repository import set_broker_ids

    session = _session()
    intent = _create_place_intent(session)
    session.commit()

    set_broker_ids(session, intent_id=intent.id, broker_order_id="1001", broker_perm_id="P9")
    session.commit()
    saved = session.get(BrokerOrderIntent, intent.id)
    assert saved.broker_order_id == "1001"
    assert saved.broker_perm_id == "P9"

    # Partial update preserves the other field.
    set_broker_ids(session, intent_id=intent.id, broker_perm_id="P10")
    session.commit()
    saved = session.get(BrokerOrderIntent, intent.id)
    assert saved.broker_order_id == "1001"
    assert saved.broker_perm_id == "P10"


def test_set_broker_ids_raises_on_unknown_intent():
    from marketpulse.broker.order_repository import set_broker_ids

    session = _session()
    with pytest.raises(LookupError):
        set_broker_ids(session, intent_id=42, broker_order_id="x")


def test_get_intent_by_id_returns_or_raises():
    from marketpulse.broker.order_repository import get_intent_by_id

    session = _session()
    intent = _create_place_intent(session)
    session.commit()

    assert get_intent_by_id(session, intent.id).id == intent.id
    with pytest.raises(LookupError):
        get_intent_by_id(session, 999999)


def test_get_intent_by_idempotency_key_returns_match_or_none():
    from marketpulse.broker.order_repository import get_intent_by_idempotency_key

    session = _session()
    intent = _create_place_intent(session, key="lookup-1")
    session.commit()

    found = get_intent_by_idempotency_key(
        session,
        account_id="DU123456",
        action="place",
        local_idempotency_key="lookup-1",
    )
    assert found is not None and found.id == intent.id

    missing = get_intent_by_idempotency_key(
        session,
        account_id="DU123456",
        action="place",
        local_idempotency_key="not-there",
    )
    assert missing is None


def test_list_events_for_intent_orders_by_observed_at_then_id():
    from marketpulse.broker.order_repository import append_event, list_events_for_intent

    session = _session()
    intent = _create_place_intent(session)
    session.commit()

    t1 = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    t2 = datetime(2026, 5, 24, 12, 5, tzinfo=UTC)

    # Insert in non-chronological order; two share the same observed_at.
    e_late = append_event(
        session, intent_id=intent.id, event_type="order_status_seen",
        event_source="adapter_callback", observed_at=t2,
    )
    e_early_a = append_event(
        session, intent_id=intent.id, event_type="staged_to_tws",
        event_source="adapter_callback", observed_at=t1,
    )
    e_early_b = append_event(
        session, intent_id=intent.id, event_type="submitted_to_broker",
        event_source="adapter_callback", observed_at=t1,
    )
    session.commit()

    events = list_events_for_intent(session, intent.id)
    assert [e.id for e in events] == [e_early_a.id, e_early_b.id, e_late.id]
