# Layer: stateful
"""Tests for Phase 7b ``order_service.place_order``.

These exercise the place flow's safety brakes, intent provenance, broker-call
ordering, and event recording against a ``FakeOrderClient``. Spec locks (see
``docs/superpowers/plans/2026-05-24-phase-7b-ibkr-paper-execution-pilot.md``):

* L17 — intent row exists before any broker call so failed calls leave evidence.
* L26/L30 — only ``DU<letters>*\\d+`` accounts pass the safety gate.
* L28/L31 — adapter validates ``managedAccounts`` includes the requested account
  before constructing/submitting an Order.
* L38 — duplicate ``(account_id, action, local_idempotency_key)`` rejected
  before the broker is touched.
* L41 — ``transmit=False`` emits ``staged_to_tws``; ``transmit=True`` emits
  ``submitted_to_broker``.
* L48/L52 — every terminal failure path that has an intent records at least one
  event.
* L69 — placeOrder-after callback timeout leaves the intent ``sent`` with an
  ``error`` event; pre-placeOrder callback timeout leaves it ``failed``.
* L74/L75 — service is sync; ``IntentStatus`` is ``{created, sent, completed,
  rejected, failed}``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from marketpulse.broker.order_types import (
    BrokerOrderObservation,
    BrokerOrderRequest,
    OrderAccountMismatchError,
    OrderBrokerCallError,
    OrderCallbackTimeoutError,
    OrderConnectionError,
    OrderDuplicateError,
    OrderSafetyError,
    PlaceResult,
)
from marketpulse.db.base import Base
from marketpulse.db.models import BrokerOrderEvent, BrokerOrderIntent

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _ClientCall:
    request: BrokerOrderRequest
    intent_id: int
    order_ref: str


class FakeOrderClient:
    """Programmable fake broker client.

    ``script`` is either a ``PlaceResult`` to return or an ``Exception`` to
    raise. ``calls`` records every ``place_lmt_order`` invocation so tests can
    assert call counts and the order_ref the service computed.
    """

    def __init__(self, script: PlaceResult | Exception) -> None:
        self._script = script
        self.calls: list[_ClientCall] = []

    def place_lmt_order(
        self,
        request: BrokerOrderRequest,
        *,
        intent_id: int,
        order_ref: str,
    ) -> PlaceResult:
        self.calls.append(_ClientCall(request=request, intent_id=intent_id, order_ref=order_ref))
        if isinstance(self._script, Exception):
            raise self._script
        return self._script


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _count(session: Session, model) -> int:
    return int(session.execute(select(func.count(model.id))).scalar() or 0)


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


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_happy_path_transmit_false_stages_to_tws_and_completes():
    from marketpulse.broker.order_service import place_order

    session = _session()
    client = FakeOrderClient(
        PlaceResult(
            placeorder_called=True,
            broker_order_id="1001",
            broker_perm_id=None,
            managed_accounts=("DU123456",),
            observations=(
                BrokerOrderObservation(
                    event_type="next_valid_id_received",
                    broker_order_id="1001",
                    raw={"next_valid_id": 1001},
                ),
                BrokerOrderObservation(
                    event_type="staged_to_tws",
                    broker_order_id="1001",
                    broker_status="PreSubmitted",
                    raw={"transmit": False},
                ),
            ),
        )
    )

    result = place_order(session, client=client, request=_request())
    session.commit()

    assert len(client.calls) == 1
    assert client.calls[0].order_ref.startswith("MP-7B-")
    assert result.status == "completed"
    intent = session.get(BrokerOrderIntent, result.intent.id)
    assert intent is not None
    assert intent.status == "completed"
    assert intent.broker_order_id == "1001"
    event_types = [row.event_type for row in session.scalars(select(BrokerOrderEvent)).all()]
    assert event_types == ["next_valid_id_received", "staged_to_tws"]
    # L41: transmit=False must NOT emit submitted_to_broker.
    assert "submitted_to_broker" not in event_types


def test_happy_path_transmit_true_records_submitted_and_persists_perm_id():
    from marketpulse.broker.order_service import place_order

    session = _session()
    client = FakeOrderClient(
        PlaceResult(
            placeorder_called=True,
            broker_order_id="1002",
            broker_perm_id="P-77",
            managed_accounts=("DU123456",),
            observations=(
                BrokerOrderObservation(event_type="next_valid_id_received", raw={"id": 1002}),
                BrokerOrderObservation(
                    event_type="submitted_to_broker",
                    broker_order_id="1002",
                    broker_status="Submitted",
                    raw={"transmit": True},
                ),
                BrokerOrderObservation(
                    event_type="order_status_seen",
                    broker_order_id="1002",
                    broker_perm_id="P-77",
                    broker_status="Submitted",
                    filled_quantity=Decimal("0"),
                    raw={"status": "Submitted"},
                ),
            ),
        )
    )

    result = place_order(
        session,
        client=client,
        request=_request(transmit=True, key="tx-1"),
        confirm_transmit=True,
    )
    session.commit()

    assert result.status == "completed"
    intent = session.get(BrokerOrderIntent, result.intent.id)
    assert intent is not None
    assert intent.broker_order_id == "1002"
    assert intent.broker_perm_id == "P-77"
    event_types = [row.event_type for row in session.scalars(select(BrokerOrderEvent)).all()]
    assert event_types == ["next_valid_id_received", "submitted_to_broker", "order_status_seen"]


# ---------------------------------------------------------------------------
# Safety / refusal paths
# ---------------------------------------------------------------------------


def test_non_paper_account_is_rejected_with_safety_event_and_no_broker_call():
    from marketpulse.broker.order_service import place_order

    session = _session()
    client = FakeOrderClient(
        PlaceResult(
            placeorder_called=False,
            broker_order_id=None,
            broker_perm_id=None,
            managed_accounts=(),
            observations=(),
        )
    )

    result = place_order(
        session,
        client=client,
        request=_request(account_id="U123456", key="live-1"),
    )
    session.commit()

    assert client.calls == []  # no broker call
    assert result.status == "rejected"
    intent = session.get(BrokerOrderIntent, result.intent.id)
    assert intent is not None
    assert intent.status == "rejected"
    event = session.scalars(select(BrokerOrderEvent)).one()
    assert event.event_type == "safety_rejected"
    assert event.event_source == "service_safety"


def test_account_mismatch_from_adapter_fails_intent_and_records_event():
    from marketpulse.broker.order_service import place_order

    session = _session()
    client = FakeOrderClient(
        OrderAccountMismatchError(
            "requested DU123456 not in managedAccounts={'DU999999'}"
        )
    )

    result = place_order(session, client=client, request=_request(key="mm-1"))
    session.commit()

    # Adapter was asked but raised before constructing/submitting any Order.
    assert len(client.calls) == 1
    assert result.status == "failed"
    intent = session.get(BrokerOrderIntent, result.intent.id)
    assert intent is not None
    assert intent.status == "failed"
    event = session.scalars(select(BrokerOrderEvent)).one()
    assert event.event_type == "account_mismatch"
    assert event.event_source == "adapter_callback"


def test_connection_failure_marks_intent_failed_with_connection_event():
    from marketpulse.broker.order_service import place_order

    session = _session()
    client = FakeOrderClient(OrderConnectionError("could not connect to 127.0.0.1:7497"))

    result = place_order(session, client=client, request=_request(key="conn-1"))
    session.commit()

    assert result.status == "failed"
    intent = session.get(BrokerOrderIntent, result.intent.id)
    assert intent is not None
    assert intent.status == "failed"
    event = session.scalars(select(BrokerOrderEvent)).one()
    assert event.event_type == "connection_failed"
    assert event.event_source == "adapter_callback"


def test_next_valid_id_timeout_fails_intent():
    from marketpulse.broker.order_service import place_order

    session = _session()
    client = FakeOrderClient(
        OrderCallbackTimeoutError(
            "next_valid_id callback never arrived",
            placeorder_called=False,
        )
    )

    result = place_order(session, client=client, request=_request(key="t-pre"))
    session.commit()

    assert result.status == "failed"
    intent = session.get(BrokerOrderIntent, result.intent.id)
    assert intent is not None
    assert intent.status == "failed"
    event = session.scalars(select(BrokerOrderEvent)).one()
    assert event.event_type == "error"
    msg = (event.message or "").lower()
    assert "callback_timeout" in msg or "next_valid_id" in msg


def test_place_callback_timeout_keeps_intent_sent():
    """L69: if placeOrder was called and the callback timed out, the intent
    stays ``sent`` (we asked TWS to act and cannot prove it didn't)."""

    from marketpulse.broker.order_service import place_order

    session = _session()
    client = FakeOrderClient(
        OrderCallbackTimeoutError(
            "openOrder callback never arrived",
            placeorder_called=True,
        )
    )

    result = place_order(session, client=client, request=_request(key="t-post"))
    session.commit()

    assert result.status == "sent"
    intent = session.get(BrokerOrderIntent, result.intent.id)
    assert intent is not None
    assert intent.status == "sent"
    event = session.scalars(select(BrokerOrderEvent)).one()
    assert event.event_type == "error"
    assert "callback_timeout" in (event.message or "").lower()


def test_broker_rejection_marks_intent_rejected():
    from marketpulse.broker.order_service import place_order

    session = _session()
    client = FakeOrderClient(OrderBrokerCallError("TWS error 201: order rejected"))

    result = place_order(session, client=client, request=_request(key="rej-1"))
    session.commit()

    assert result.status == "rejected"
    intent = session.get(BrokerOrderIntent, result.intent.id)
    assert intent is not None
    assert intent.status == "rejected"
    event = session.scalars(select(BrokerOrderEvent)).one()
    assert event.event_type == "rejected"
    assert "201" in (event.message or "")


# ---------------------------------------------------------------------------
# Service-level confirm_transmit gate (L20)
# ---------------------------------------------------------------------------


def test_transmit_true_without_confirm_transmit_raises_safety_error():
    """L20: place_order(transmit=True, confirm_transmit=False) must raise
    BEFORE any DB writes or broker calls — symmetric with cancel_order's
    confirm_cancel brake.
    """

    from marketpulse.broker.order_service import place_order

    session = _session()
    client = FakeOrderClient(
        PlaceResult(
            placeorder_called=False,
            broker_order_id=None,
            broker_perm_id=None,
            managed_accounts=(),
            observations=(),
        )
    )

    with pytest.raises(OrderSafetyError):
        place_order(
            session,
            client=client,
            request=_request(transmit=True, key="no-confirm"),
            confirm_transmit=False,
        )

    # Fail-closed: no intent, no events, no broker call.
    assert client.calls == []
    assert _count(session, BrokerOrderIntent) == 0
    assert _count(session, BrokerOrderEvent) == 0


def test_transmit_true_with_confirm_transmit_proceeds():
    """L20: confirm_transmit=True allows the transmit flow to proceed."""

    from marketpulse.broker.order_service import place_order

    session = _session()
    client = FakeOrderClient(
        PlaceResult(
            placeorder_called=True,
            broker_order_id="2002",
            broker_perm_id=None,
            managed_accounts=("DU123456",),
            observations=(
                BrokerOrderObservation(
                    event_type="submitted_to_broker",
                    broker_order_id="2002",
                    broker_status="Submitted",
                    raw={"transmit": True},
                ),
            ),
        )
    )

    result = place_order(
        session,
        client=client,
        request=_request(transmit=True, key="confirmed-tx"),
        confirm_transmit=True,
    )
    session.commit()

    assert result.status in {"completed", "sent"}
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Non-paper account broker_environment classification persistence
# ---------------------------------------------------------------------------


def test_live_account_rejection_persists_live_classification():
    """Non-paper rejection must persist the actual classification
    (``live``/``unknown``) — not coerce to ``paper`` — so audit queries
    can distinguish refusal reasons.
    """

    from marketpulse.broker.order_service import place_order

    session = _session()
    client = FakeOrderClient(
        PlaceResult(
            placeorder_called=False,
            broker_order_id=None,
            broker_perm_id=None,
            managed_accounts=(),
            observations=(),
        )
    )

    result = place_order(
        session,
        client=client,
        request=_request(account_id="U1234567", key="live-cls"),
    )
    session.commit()

    intent = session.get(BrokerOrderIntent, result.intent.id)
    assert intent is not None
    assert intent.broker_environment == "live"


def test_unknown_account_rejection_persists_unknown():
    from marketpulse.broker.order_service import place_order

    session = _session()
    client = FakeOrderClient(
        PlaceResult(
            placeorder_called=False,
            broker_order_id=None,
            broker_perm_id=None,
            managed_accounts=(),
            observations=(),
        )
    )

    result = place_order(
        session,
        client=client,
        request=_request(account_id="FOO123", key="unk-cls"),
    )
    session.commit()

    intent = session.get(BrokerOrderIntent, result.intent.id)
    assert intent is not None
    assert intent.broker_environment == "unknown"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_duplicate_idempotency_key_is_refused_before_broker_call():
    from marketpulse.broker.order_service import place_order

    session = _session()
    first_client = FakeOrderClient(
        PlaceResult(
            placeorder_called=True,
            broker_order_id="1001",
            broker_perm_id=None,
            managed_accounts=("DU123456",),
            observations=(
                BrokerOrderObservation(event_type="next_valid_id_received", raw={}),
                BrokerOrderObservation(event_type="staged_to_tws", raw={}),
            ),
        )
    )
    place_order(session, client=first_client, request=_request(key="dup"))
    session.commit()

    intent_count_before = _count(session, BrokerOrderIntent)
    event_count_before = _count(session, BrokerOrderEvent)

    second_client = FakeOrderClient(
        PlaceResult(
            placeorder_called=False,
            broker_order_id=None,
            broker_perm_id=None,
            managed_accounts=(),
            observations=(),
        )
    )
    with pytest.raises(OrderDuplicateError):
        place_order(session, client=second_client, request=_request(key="dup"))

    # No second intent row, no events on the existing row, no broker call.
    assert second_client.calls == []
    assert _count(session, BrokerOrderIntent) == intent_count_before
    assert _count(session, BrokerOrderEvent) == event_count_before
