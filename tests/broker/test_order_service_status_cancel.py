# Layer: stateful
"""Tests for Phase 7b ``order_service.fetch_status`` and ``cancel_order``.

Covers spec locks for the status/cancel child-intent provenance flow:

* L15 — status/cancel limited to locally known ``broker_order_intent`` rows.
* L21 — cancel requires explicit ``--confirm-cancel`` (service-level fail-closed).
* L42 — status/cancel resolve broker identity ONLY from the local place intent.
* L44 — if a place intent has no broker_order_id (failed place), the status/cancel
  child intent is still created for provenance but transitions to ``rejected``
  via a ``safety_rejected`` event.
* L45/L46 — every status/cancel call creates a child intent whose
  ``parent_intent_id`` references the place intent.
* L62 — status only observes order state in the current TWS session.
* L63 — ``transmit=False`` place → ``staged_cancelled`` (not ``cancelled``).
* L69 — callback timeout semantics: status/cancel child intent stays ``sent``
  with an ``error``/``callback_timeout`` event.
* L70 — status/cancel use *generated* idempotency keys (status-N-xxxx /
  cancel-N-xxxx), never operator-supplied keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from marketpulse.broker.order_types import (
    BrokerOrderObservation,
    BrokerOrderRequest,
    CancelResult,
    OrderAccountMismatchError,
    OrderBrokerCallError,
    OrderCallbackTimeoutError,
    OrderConnectionError,
    OrderSafetyError,
    PlaceResult,
    StatusResult,
)
from marketpulse.db.base import Base
from marketpulse.db.models import BrokerOrderEvent, BrokerOrderIntent

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _StatusCall:
    broker_order_id: str
    account_id: str


@dataclass
class _CancelCall:
    broker_order_id: str
    account_id: str
    was_transmitted: bool


class FakeStatusCancelClient:
    """Programmable fake broker client for status/cancel.

    Place flow uses ``place_script`` (PlaceResult or Exception).
    Status uses ``status_script`` (StatusResult or Exception).
    Cancel uses ``cancel_script`` (CancelResult or Exception).
    """

    def __init__(
        self,
        *,
        place_script: PlaceResult | Exception | None = None,
        status_script: StatusResult | Exception | None = None,
        cancel_script: CancelResult | Exception | None = None,
    ) -> None:
        self._place_script = place_script
        self._status_script = status_script
        self._cancel_script = cancel_script
        self.place_calls: list[BrokerOrderRequest] = []
        self.status_calls: list[_StatusCall] = []
        self.cancel_calls: list[_CancelCall] = []

    def place_lmt_order(
        self,
        request: BrokerOrderRequest,
        *,
        intent_id: int,
        order_ref: str,
    ) -> PlaceResult:
        self.place_calls.append(request)
        if self._place_script is None:
            raise RuntimeError("no place_script configured")
        if isinstance(self._place_script, Exception):
            raise self._place_script
        return self._place_script

    def fetch_order_status(
        self,
        *,
        broker_order_id: str,
        account_id: str,
    ) -> StatusResult:
        self.status_calls.append(
            _StatusCall(broker_order_id=broker_order_id, account_id=account_id)
        )
        if self._status_script is None:
            raise RuntimeError("no status_script configured")
        if isinstance(self._status_script, Exception):
            raise self._status_script
        return self._status_script

    def cancel_order(
        self,
        *,
        broker_order_id: str,
        account_id: str,
        was_transmitted: bool,
    ) -> CancelResult:
        self.cancel_calls.append(
            _CancelCall(
                broker_order_id=broker_order_id,
                account_id=account_id,
                was_transmitted=was_transmitted,
            )
        )
        if self._cancel_script is None:
            raise RuntimeError("no cancel_script configured")
        if isinstance(self._cancel_script, Exception):
            raise self._cancel_script
        return self._cancel_script


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _request(
    *,
    account_id: str = "DU123456",
    key: str = "key-1",
    transmit: bool = False,
) -> BrokerOrderRequest:
    return BrokerOrderRequest(
        account_id=account_id,
        symbol="AAPL",
        asset_class="STK",
        side="BUY",
        quantity=Decimal("1"),
        order_type="LMT",
        limit_price=Decimal("1.00"),
        transmit=transmit,
        local_idempotency_key=key,
    )


def _seed_place_intent(
    session: Session,
    *,
    client: FakeStatusCancelClient,
    transmit: bool = False,
    key: str = "place-1",
    broker_order_id: str | None = "1001",
) -> BrokerOrderIntent:
    """Drive a place_order through the service to seed a parent intent."""

    from marketpulse.broker.order_service import place_order

    client._place_script = PlaceResult(
        placeorder_called=True,
        broker_order_id=broker_order_id,
        broker_perm_id=None,
        managed_accounts=("DU123456",),
        observations=(
            BrokerOrderObservation(
                event_type="next_valid_id_received",
                broker_order_id=broker_order_id,
            ),
            BrokerOrderObservation(
                event_type="staged_to_tws" if not transmit else "submitted_to_broker",
                broker_order_id=broker_order_id,
            ),
        ),
    )
    result = place_order(
        session,
        client=client,
        request=_request(transmit=transmit, key=key),
        confirm_transmit=transmit,
    )
    session.commit()
    intent = session.get(BrokerOrderIntent, result.intent.id)
    assert intent is not None
    return intent


def _seed_place_intent_no_broker_id(
    session: Session,
    *,
    transmit: bool = False,
    key: str = "place-noid",
) -> BrokerOrderIntent:
    """Seed a place intent without a broker_order_id (simulating a failed place).

    Insert directly via repository to avoid driving a full failure path.
    """

    from marketpulse.broker.order_repository import create_intent

    intent = create_intent(
        session,
        action="place",
        broker="IBKR",
        broker_environment="paper",
        account_id="DU123456",
        local_idempotency_key=key,
        context={"transmit": transmit},
        symbol="AAPL",
        asset_class="STK",
        side="BUY",
        quantity=Decimal("1"),
        order_type="LMT",
        limit_price=Decimal("1.00"),
        transmit=transmit,
    )
    session.commit()
    return intent


# ---------------------------------------------------------------------------
# fetch_status
# ---------------------------------------------------------------------------


def test_status_happy_path_creates_child_intent_and_records_observations():
    from marketpulse.broker.order_service import fetch_status

    session = _session()
    client = FakeStatusCancelClient()
    place = _seed_place_intent(session, client=client)

    client._status_script = StatusResult(
        success=True,
        managed_accounts=("DU123456",),
        observations=(
            BrokerOrderObservation(
                event_type="order_status_seen",
                broker_order_id="1001",
                broker_status="PreSubmitted",
                filled_quantity=Decimal("0"),
                raw={"status": "PreSubmitted"},
            ),
        ),
    )

    result = fetch_status(session, client=client, intent_id=place.id)
    session.commit()

    assert result.status == "completed"
    assert len(client.status_calls) == 1
    call = client.status_calls[0]
    assert call.broker_order_id == "1001"
    assert call.account_id == "DU123456"

    # Child intent: action=status_check, parent_intent_id=place.id, generated key.
    child = session.get(BrokerOrderIntent, result.intent.id)
    assert child is not None
    assert child.action == "status_check"
    assert child.parent_intent_id == place.id
    assert child.local_idempotency_key.startswith("status-")
    assert str(place.id) in child.local_idempotency_key
    assert child.status == "completed"

    # Events on child only — the place intent's events should not be mutated.
    events = list(
        session.scalars(
            select(BrokerOrderEvent).where(BrokerOrderEvent.intent_id == child.id)
        ).all()
    )
    assert [e.event_type for e in events] == ["order_status_seen"]


def test_status_with_missing_broker_order_id_records_safety_rejected():
    """L44: place intent without broker_order_id → child created + safety_rejected."""

    from marketpulse.broker.order_service import fetch_status

    session = _session()
    place = _seed_place_intent_no_broker_id(session)
    client = FakeStatusCancelClient()

    result = fetch_status(session, client=client, intent_id=place.id)
    session.commit()

    assert result.status == "rejected"
    assert client.status_calls == []  # broker not called

    child = session.get(BrokerOrderIntent, result.intent.id)
    assert child is not None
    assert child.action == "status_check"
    assert child.parent_intent_id == place.id
    assert child.status == "rejected"

    events = list(
        session.scalars(
            select(BrokerOrderEvent).where(BrokerOrderEvent.intent_id == child.id)
        ).all()
    )
    assert len(events) == 1
    assert events[0].event_type == "safety_rejected"
    assert events[0].event_source == "service_safety"


def test_status_account_mismatch_marks_child_failed():
    from marketpulse.broker.order_service import fetch_status

    session = _session()
    client = FakeStatusCancelClient()
    place = _seed_place_intent(session, client=client)
    client._status_script = OrderAccountMismatchError(
        "managedAccounts does not include DU123456"
    )

    result = fetch_status(session, client=client, intent_id=place.id)
    session.commit()

    assert result.status == "failed"
    child = session.get(BrokerOrderIntent, result.intent.id)
    assert child is not None
    assert child.status == "failed"
    events = list(
        session.scalars(
            select(BrokerOrderEvent).where(BrokerOrderEvent.intent_id == child.id)
        ).all()
    )
    assert [e.event_type for e in events] == ["account_mismatch"]


def test_status_callback_timeout_keeps_child_sent():
    """L69: callback timeout for status → child stays ``sent`` with error event."""

    from marketpulse.broker.order_service import fetch_status

    session = _session()
    client = FakeStatusCancelClient()
    place = _seed_place_intent(session, client=client)
    client._status_script = OrderCallbackTimeoutError(
        "openOrder callback never arrived", placeorder_called=True
    )

    result = fetch_status(session, client=client, intent_id=place.id)
    session.commit()

    assert result.status == "sent"
    child = session.get(BrokerOrderIntent, result.intent.id)
    assert child is not None
    assert child.status == "sent"
    events = list(
        session.scalars(
            select(BrokerOrderEvent).where(BrokerOrderEvent.intent_id == child.id)
        ).all()
    )
    assert [e.event_type for e in events] == ["error"]
    assert "callback_timeout" in (events[0].message or "").lower()


def test_status_rejects_non_place_intent():
    """fetch_status of a non-place intent is a programmer error → OrderSafetyError."""

    from marketpulse.broker.order_service import fetch_status

    session = _session()
    client = FakeStatusCancelClient()
    place = _seed_place_intent(session, client=client)

    # Create a status_check child manually, then try to fetch_status on it.
    from marketpulse.broker.order_repository import create_intent

    not_a_place = create_intent(
        session,
        action="status_check",
        broker="IBKR",
        broker_environment="paper",
        account_id="DU123456",
        local_idempotency_key="status-fake",
        context={},
        parent_intent_id=place.id,
    )
    session.commit()

    with pytest.raises(OrderSafetyError):
        fetch_status(session, client=client, intent_id=not_a_place.id)


def test_status_of_terminal_place_intent_still_calls_adapter():
    """Operator may want a forensic status check on a completed/rejected place."""

    from marketpulse.broker.order_service import fetch_status

    session = _session()
    client = FakeStatusCancelClient()
    place = _seed_place_intent(session, client=client)
    # The seeded place is now in status=completed; status should still proceed.
    assert place.status == "completed"

    client._status_script = StatusResult(
        success=True,
        managed_accounts=("DU123456",),
        observations=(
            BrokerOrderObservation(
                event_type="order_status_seen",
                broker_order_id="1001",
                broker_status="Filled",
            ),
        ),
    )

    result = fetch_status(session, client=client, intent_id=place.id)
    session.commit()

    assert result.status == "completed"
    assert len(client.status_calls) == 1


# ---------------------------------------------------------------------------
# cancel_order
# ---------------------------------------------------------------------------


def test_cancel_requires_confirm_flag_before_any_provenance():
    """L21: cancel without confirm_cancel=True raises BEFORE creating a child intent."""

    from marketpulse.broker.order_service import cancel_order

    session = _session()
    client = FakeStatusCancelClient()
    place = _seed_place_intent(session, client=client)

    intent_count_before = len(
        list(session.scalars(select(BrokerOrderIntent)).all())
    )

    with pytest.raises(OrderSafetyError):
        cancel_order(session, client=client, intent_id=place.id)

    # No new intent and no broker call.
    assert client.cancel_calls == []
    intent_count_after = len(
        list(session.scalars(select(BrokerOrderIntent)).all())
    )
    assert intent_count_after == intent_count_before


def test_cancel_transmit_true_records_broker_cancel_observations():
    from marketpulse.broker.order_service import cancel_order

    session = _session()
    client = FakeStatusCancelClient()
    place = _seed_place_intent(session, client=client, transmit=True, key="tx-1")

    client._cancel_script = CancelResult(
        success=True,
        managed_accounts=("DU123456",),
        observations=(
            BrokerOrderObservation(
                event_type="broker_cancel_requested",
                broker_order_id="1001",
            ),
            BrokerOrderObservation(
                event_type="cancelled",
                broker_order_id="1001",
                broker_status="Cancelled",
            ),
        ),
    )

    result = cancel_order(
        session, client=client, intent_id=place.id, confirm_cancel=True
    )
    session.commit()

    assert result.status == "completed"
    assert len(client.cancel_calls) == 1
    call = client.cancel_calls[0]
    assert call.broker_order_id == "1001"
    assert call.account_id == "DU123456"
    assert call.was_transmitted is True

    child = session.get(BrokerOrderIntent, result.intent.id)
    assert child is not None
    assert child.action == "cancel"
    assert child.parent_intent_id == place.id
    assert child.local_idempotency_key.startswith("cancel-")
    assert str(place.id) in child.local_idempotency_key
    assert child.status == "completed"

    events = list(
        session.scalars(
            select(BrokerOrderEvent).where(BrokerOrderEvent.intent_id == child.id)
        ).all()
    )
    assert [e.event_type for e in events] == ["broker_cancel_requested", "cancelled"]


def test_cancel_transmit_false_emits_staged_cancelled_only():
    """L63: cancel of a staged (transmit=False) place produces ``staged_cancelled``."""

    from marketpulse.broker.order_service import cancel_order

    session = _session()
    client = FakeStatusCancelClient()
    place = _seed_place_intent(session, client=client, transmit=False, key="staged-1")

    client._cancel_script = CancelResult(
        success=True,
        managed_accounts=("DU123456",),
        observations=(
            BrokerOrderObservation(
                event_type="staged_cancelled",
                broker_order_id="1001",
                message="staged order removed from TWS without broker submission",
            ),
        ),
    )

    result = cancel_order(
        session, client=client, intent_id=place.id, confirm_cancel=True
    )
    session.commit()

    assert result.status == "completed"
    call = client.cancel_calls[0]
    assert call.was_transmitted is False

    child = session.get(BrokerOrderIntent, result.intent.id)
    assert child is not None
    events = list(
        session.scalars(
            select(BrokerOrderEvent).where(BrokerOrderEvent.intent_id == child.id)
        ).all()
    )
    types = [e.event_type for e in events]
    assert types == ["staged_cancelled"]
    assert "broker_cancel_requested" not in types
    assert "cancelled" not in types


def test_cancel_with_missing_broker_order_id_records_safety_rejected():
    from marketpulse.broker.order_service import cancel_order

    session = _session()
    place = _seed_place_intent_no_broker_id(session)
    client = FakeStatusCancelClient()

    result = cancel_order(
        session, client=client, intent_id=place.id, confirm_cancel=True
    )
    session.commit()

    assert result.status == "rejected"
    assert client.cancel_calls == []

    child = session.get(BrokerOrderIntent, result.intent.id)
    assert child is not None
    assert child.action == "cancel"
    assert child.parent_intent_id == place.id
    assert child.status == "rejected"
    events = list(
        session.scalars(
            select(BrokerOrderEvent).where(BrokerOrderEvent.intent_id == child.id)
        ).all()
    )
    assert [e.event_type for e in events] == ["safety_rejected"]


def test_cancel_account_mismatch_marks_child_failed():
    from marketpulse.broker.order_service import cancel_order

    session = _session()
    client = FakeStatusCancelClient()
    place = _seed_place_intent(session, client=client)
    client._cancel_script = OrderAccountMismatchError(
        "managedAccounts does not include DU123456"
    )

    result = cancel_order(
        session, client=client, intent_id=place.id, confirm_cancel=True
    )
    session.commit()

    assert result.status == "failed"
    child = session.get(BrokerOrderIntent, result.intent.id)
    assert child is not None
    assert child.status == "failed"
    events = list(
        session.scalars(
            select(BrokerOrderEvent).where(BrokerOrderEvent.intent_id == child.id)
        ).all()
    )
    assert [e.event_type for e in events] == ["account_mismatch"]


def test_cancel_connection_failure_marks_child_failed():
    from marketpulse.broker.order_service import cancel_order

    session = _session()
    client = FakeStatusCancelClient()
    place = _seed_place_intent(session, client=client)
    client._cancel_script = OrderConnectionError("could not connect to 127.0.0.1:7497")

    result = cancel_order(
        session, client=client, intent_id=place.id, confirm_cancel=True
    )
    session.commit()

    assert result.status == "failed"
    child = session.get(BrokerOrderIntent, result.intent.id)
    assert child is not None
    assert child.status == "failed"
    events = list(
        session.scalars(
            select(BrokerOrderEvent).where(BrokerOrderEvent.intent_id == child.id)
        ).all()
    )
    assert [e.event_type for e in events] == ["connection_failed"]


def test_cancel_callback_timeout_keeps_child_sent():
    from marketpulse.broker.order_service import cancel_order

    session = _session()
    client = FakeStatusCancelClient()
    place = _seed_place_intent(session, client=client)
    client._cancel_script = OrderCallbackTimeoutError(
        "cancel callback never arrived", placeorder_called=True
    )

    result = cancel_order(
        session, client=client, intent_id=place.id, confirm_cancel=True
    )
    session.commit()

    assert result.status == "sent"
    child = session.get(BrokerOrderIntent, result.intent.id)
    assert child is not None
    assert child.status == "sent"
    events = list(
        session.scalars(
            select(BrokerOrderEvent).where(BrokerOrderEvent.intent_id == child.id)
        ).all()
    )
    assert [e.event_type for e in events] == ["error"]
    assert "callback_timeout" in (events[0].message or "").lower()


def test_cancel_broker_call_error_marks_child_failed():
    from marketpulse.broker.order_service import cancel_order

    session = _session()
    client = FakeStatusCancelClient()
    place = _seed_place_intent(session, client=client)
    client._cancel_script = OrderBrokerCallError(
        "TWS error 202: order cancellation rejected"
    )

    result = cancel_order(
        session, client=client, intent_id=place.id, confirm_cancel=True
    )
    session.commit()

    assert result.status == "failed"
    child = session.get(BrokerOrderIntent, result.intent.id)
    assert child is not None
    assert child.status == "failed"
    events = list(
        session.scalars(
            select(BrokerOrderEvent).where(BrokerOrderEvent.intent_id == child.id)
        ).all()
    )
    assert [e.event_type for e in events] == ["error"]
    assert "202" in (events[0].message or "")


def test_cancel_rejects_non_place_intent():
    from marketpulse.broker.order_service import cancel_order

    session = _session()
    client = FakeStatusCancelClient()
    place = _seed_place_intent(session, client=client)

    from marketpulse.broker.order_repository import create_intent

    not_a_place = create_intent(
        session,
        action="cancel",
        broker="IBKR",
        broker_environment="paper",
        account_id="DU123456",
        local_idempotency_key="cancel-fake",
        context={},
        parent_intent_id=place.id,
    )
    session.commit()

    with pytest.raises(OrderSafetyError):
        cancel_order(
            session, client=client, intent_id=not_a_place.id, confirm_cancel=True
        )


def test_status_missing_intent_raises_lookup_error():
    from marketpulse.broker.order_service import fetch_status

    session = _session()
    client = FakeStatusCancelClient()
    with pytest.raises(LookupError):
        fetch_status(session, client=client, intent_id=9999)


def test_cancel_missing_intent_raises_lookup_error():
    from marketpulse.broker.order_service import cancel_order

    session = _session()
    client = FakeStatusCancelClient()
    with pytest.raises(LookupError):
        cancel_order(session, client=client, intent_id=9999, confirm_cancel=True)
