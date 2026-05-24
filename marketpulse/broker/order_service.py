"""Phase 7b manual IBKR paper order pilot orchestration — place flow.

This module owns the *synchronous* command flow that turns one
``BrokerOrderRequest`` into a persisted ``broker_order_intent`` row plus the
``broker_order_event`` trail produced while the request was driven against
TWS/IB Gateway. All ``ibapi`` threading lives inside the adapter
(``ibkr_order_client.py``, T7b); this service is pure orchestration over a
caller-owned ``Session`` and the ``BrokerOrderClient`` Protocol.

Spec locks honored here:

* L17 — the intent row is created **before** the broker is called, so a
  failed broker call always leaves provenance.
* L26/L30 — only ``classify_order_account == "paper"`` (``DU[A-Z]*\\d+``)
  accounts are admitted; everything else turns into a ``safety_rejected``
  event and a terminal ``rejected`` intent.
* L31/L48 — account-mismatch / connection / safety failures emit a typed
  event (``account_mismatch``, ``connection_failed``, ``safety_rejected``).
* L38 — duplicate ``(account_id, action, local_idempotency_key)`` triples
  are rejected via an explicit pre-check **before** any broker call.
* L41 — the service does not synthesize ``staged_to_tws`` /
  ``submitted_to_broker`` events; it copies whatever the adapter observed
  into ``broker_order_event``. The adapter is responsible for emitting the
  right pair for ``transmit=False`` vs. ``transmit=True``.
* L52 — every terminal failure path that has an intent appends ≥1 event.
* L69 — ``OrderCallbackTimeoutError.placeorder_called`` decides whether the
  intent is parked at ``sent`` (placeOrder was called) or marched to
  ``failed`` (placeOrder never reached the broker).
* L74/L75 — sync flow; statuses are exactly ``{created, sent, completed,
  rejected, failed}``.

T5 (status/cancel) and T6 (CLI) will extend this module.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from marketpulse.broker.order_client import BrokerOrderClient
from marketpulse.broker.order_repository import (
    append_event,
    create_intent,
    get_intent_by_id,
    get_intent_by_idempotency_key,
    set_broker_ids,
    transition_status,
)
from marketpulse.broker.order_types import (
    BrokerOrderObservation,
    BrokerOrderRequest,
    EventSource,
    EventType,
    IntentStatus,
    OrderAccountMismatchError,
    OrderBrokerCallError,
    OrderCallbackTimeoutError,
    OrderConnectionError,
    OrderDuplicateError,
    OrderSafetyError,
    PlaceResult,
    build_order_ref,
    classify_order_account,
)
from marketpulse.db.models import BrokerOrderEvent, BrokerOrderIntent

_ACTION_PLACE = "place"
_ACTION_STATUS = "status_check"
_ACTION_CANCEL = "cancel"
_BROKER = "IBKR"


@dataclass(frozen=True)
class PlaceCommandResult:
    """Service-level summary of one place-order command.

    ``status`` is the service's view of the final intent state, which may be
    ``"sent"`` when placeOrder was invoked but the callback timed out (L69).
    ``events`` lists the events the service appended during this call, in
    insertion order — callers (CLI, future smoke runner) can render them
    without re-querying.
    """

    intent: BrokerOrderIntent
    events: tuple[BrokerOrderEvent, ...]
    status: IntentStatus


def _now() -> datetime:
    return datetime.now(UTC)


def _request_context_snapshot(request: BrokerOrderRequest) -> dict[str, Any]:
    """Return a JSON-safe snapshot of the request for ``intent.context``.

    ``Decimal`` is stringified so ``JSON`` columns serialize cleanly across
    SQLite and Postgres without precision drift.
    """

    return {
        "account_id": request.account_id,
        "symbol": request.symbol,
        "asset_class": request.asset_class,
        "side": request.side,
        "quantity": str(request.quantity),
        "order_type": request.order_type,
        "limit_price": str(request.limit_price) if request.limit_price is not None else None,
        "transmit": request.transmit,
        "local_idempotency_key": request.local_idempotency_key,
    }


def _record_event(
    session: Session,
    *,
    intent_id: int,
    event_type: EventType,
    event_source: EventSource,
    observed_at: datetime | None = None,
    observation: BrokerOrderObservation | None = None,
    message: str | None = None,
) -> BrokerOrderEvent:
    """Append an event to ``broker_order_event``.

    If ``observation`` is provided its broker_*/filled/remaining/avg fields are
    copied verbatim. The service-level ``event_type``/``event_source``
    override anything carried by the observation so callers cannot accidentally
    persist a mismatched source label.
    """

    raw: dict[str, Any] = dict(observation.raw) if observation and observation.raw else {}
    return append_event(
        session,
        intent_id=intent_id,
        event_type=event_type,
        event_source=event_source,
        observed_at=observed_at or _now(),
        broker_order_id=observation.broker_order_id if observation else None,
        broker_perm_id=observation.broker_perm_id if observation else None,
        broker_status=observation.broker_status if observation else None,
        filled_quantity=observation.filled_quantity if observation else None,
        remaining_quantity=observation.remaining_quantity if observation else None,
        avg_fill_price=observation.avg_fill_price if observation else None,
        message=message if message is not None else (observation.message if observation else None),
        raw=raw,
    )


def place_order(
    session: Session,
    *,
    client: BrokerOrderClient,
    request: BrokerOrderRequest,
    confirm_transmit: bool = False,
) -> PlaceCommandResult:
    """Execute the 7b place-order command flow.

    Algorithm:

    1. Account safety (L26/L30): non-paper accounts → ``safety_rejected``
       event, intent transitioned ``rejected``. No broker call.
    2. Idempotency pre-check (L38): if an intent already exists for
       ``(account_id, "place", local_idempotency_key)``, raise
       ``OrderDuplicateError`` before creating a new intent or touching the
       broker.
    3. Create intent (L17), then call
       ``client.place_lmt_order(request, intent_id=..., order_ref=...)``.
    4. Translate the adapter outcome into events + a terminal transition:

       * ``OrderAccountMismatchError`` → ``account_mismatch`` (adapter_callback) → ``failed``
       * ``OrderConnectionError`` → ``connection_failed`` (adapter_callback) → ``failed``
       * ``OrderCallbackTimeoutError(placeorder_called=False)`` →
         ``error`` (timeout) with ``next_valid_id``/``callback_timeout`` → ``failed``
       * ``OrderCallbackTimeoutError(placeorder_called=True)`` →
         ``error`` (timeout, ``callback_timeout`` message) → status stays
         ``sent`` (L69)
       * ``OrderBrokerCallError`` → ``rejected`` (adapter_callback) → ``rejected``
       * success: persist broker IDs, copy each ``observation`` into an
         ``adapter_callback`` event, transition to ``completed``.

    ``confirm_transmit`` is reserved for the CLI; the service trusts that the
    caller already gated ``request.transmit=True`` behind operator
    confirmation. The flag is accepted so the CLI in T6 can pass it through
    without a signature change.
    """

    del confirm_transmit  # reserved for CLI gating; service trusts the caller (L17 commentary).

    now = _now()
    env = classify_order_account(request.account_id)

    # --- (1) Account safety gate (L26/L30) --------------------------------
    if env != "paper":
        # Intent must still exist so the safety refusal leaves a record
        # (L17 + L48). We persist with ``broker_environment="paper"`` to
        # satisfy the CHECK constraint — the rejection event itself is the
        # canonical record that the request was *not* honored.
        message = (
            f"refusing non-paper account for 7b order pilot: "
            f"account_id={request.account_id!r} classified as {env!r}"
        )
        intent = create_intent(
            session,
            action=_ACTION_PLACE,
            broker=_BROKER,
            broker_environment="paper",
            account_id=request.account_id,
            local_idempotency_key=request.local_idempotency_key,
            context={**_request_context_snapshot(request), "refusal": message},
            symbol=request.symbol,
            asset_class=request.asset_class,
            side=request.side,
            quantity=request.quantity,
            order_type=request.order_type,
            limit_price=request.limit_price,
            transmit=request.transmit,
            created_at=now,
        )
        event = _record_event(
            session,
            intent_id=intent.id,
            event_type="safety_rejected",
            event_source="service_safety",
            observed_at=now,
            message=message,
        )
        transition_status(session, intent_id=intent.id, new_status="rejected")
        return PlaceCommandResult(intent=intent, events=(event,), status="rejected")

    # --- (2) Idempotency pre-check (L38) ----------------------------------
    existing = get_intent_by_idempotency_key(
        session,
        account_id=request.account_id,
        action=_ACTION_PLACE,
        local_idempotency_key=request.local_idempotency_key,
    )
    if existing is not None:
        raise OrderDuplicateError(
            "duplicate place intent: "
            f"(account_id={request.account_id!r}, action='place', "
            f"local_idempotency_key={request.local_idempotency_key!r}) already exists "
            f"(existing intent_id={existing.id}, status={existing.status!r})"
        )

    # --- (3) Create intent BEFORE broker call (L17) -----------------------
    intent = create_intent(
        session,
        action=_ACTION_PLACE,
        broker=_BROKER,
        broker_environment="paper",
        account_id=request.account_id,
        local_idempotency_key=request.local_idempotency_key,
        context=_request_context_snapshot(request),
        symbol=request.symbol,
        asset_class=request.asset_class,
        side=request.side,
        quantity=request.quantity,
        order_type=request.order_type,
        limit_price=request.limit_price,
        transmit=request.transmit,
        created_at=now,
    )

    order_ref = build_order_ref(
        intent_id=intent.id,
        local_idempotency_key=request.local_idempotency_key,
    )

    # --- (4) Broker call --------------------------------------------------
    try:
        result = client.place_lmt_order(
            request,
            intent_id=intent.id,
            order_ref=order_ref,
        )
    except OrderAccountMismatchError as exc:
        event = _record_event(
            session,
            intent_id=intent.id,
            event_type="account_mismatch",
            event_source="adapter_callback",
            message=str(exc) or "managedAccounts mismatch",
        )
        transition_status(session, intent_id=intent.id, new_status="failed")
        return PlaceCommandResult(intent=intent, events=(event,), status="failed")
    except OrderConnectionError as exc:
        event = _record_event(
            session,
            intent_id=intent.id,
            event_type="connection_failed",
            event_source="adapter_callback",
            message=str(exc) or "TWS/Gateway connection failed",
        )
        transition_status(session, intent_id=intent.id, new_status="failed")
        return PlaceCommandResult(intent=intent, events=(event,), status="failed")
    except OrderCallbackTimeoutError as exc:
        message = str(exc) or "broker callback timed out"
        if exc.placeorder_called:
            # L69: placeOrder was invoked, callback never returned. The order
            # may exist at the broker — we cannot prove otherwise — so park
            # the intent at ``sent`` and let the operator reconcile via T5.
            message = f"callback_timeout: {message}"
            event = _record_event(
                session,
                intent_id=intent.id,
                event_type="error",
                event_source="timeout",
                message=message,
            )
            transition_status(session, intent_id=intent.id, new_status="sent")
            return PlaceCommandResult(intent=intent, events=(event,), status="sent")
        # placeOrder never ran (e.g. nextValidId timeout) — safe to mark
        # the intent ``failed`` because no broker mutation occurred.
        message = f"callback_timeout (next_valid_id): {message}"
        event = _record_event(
            session,
            intent_id=intent.id,
            event_type="error",
            event_source="timeout",
            message=message,
        )
        transition_status(session, intent_id=intent.id, new_status="failed")
        return PlaceCommandResult(intent=intent, events=(event,), status="failed")
    except OrderBrokerCallError as exc:
        event = _record_event(
            session,
            intent_id=intent.id,
            event_type="rejected",
            event_source="adapter_callback",
            message=str(exc) or "broker rejected order",
        )
        transition_status(session, intent_id=intent.id, new_status="rejected")
        return PlaceCommandResult(intent=intent, events=(event,), status="rejected")

    # --- (5) Success path -------------------------------------------------
    return _process_success(session, intent=intent, result=result)


def _process_success(
    session: Session,
    *,
    intent: BrokerOrderIntent,
    result: PlaceResult,
) -> PlaceCommandResult:
    """Persist broker IDs, fan out adapter observations, and complete."""

    if result.broker_order_id is not None or result.broker_perm_id is not None:
        set_broker_ids(
            session,
            intent_id=intent.id,
            broker_order_id=result.broker_order_id,
            broker_perm_id=result.broker_perm_id,
        )

    events: list[BrokerOrderEvent] = []
    for observation in result.observations:
        events.append(
            _record_event(
                session,
                intent_id=intent.id,
                event_type=observation.event_type,
                event_source="adapter_callback",
                observation=observation,
            )
        )

    # Move through sent → completed. If the result reports no placeOrder call
    # at all (defensive: adapter sometimes returns early on staging-only
    # paths) we still treat the absence of an explicit failure as success
    # because the per-observation events above will tell the operator what
    # happened.
    transition_status(session, intent_id=intent.id, new_status="sent")
    transition_status(session, intent_id=intent.id, new_status="completed")
    return PlaceCommandResult(intent=intent, events=tuple(events), status="completed")


# ---------------------------------------------------------------------------
# Status / Cancel — child-intent provenance flows (T5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatusCommandResult:
    """Service-level summary of one fetch-status command (L46).

    ``intent`` is the *child* ``status_check`` intent (the parent place intent
    is reachable via ``intent.parent_intent_id``).
    """

    intent: BrokerOrderIntent
    events: tuple[BrokerOrderEvent, ...]
    status: IntentStatus


@dataclass(frozen=True)
class CancelCommandResult:
    """Service-level summary of one cancel-order command (L45).

    ``intent`` is the *child* ``cancel`` intent.
    """

    intent: BrokerOrderIntent
    events: tuple[BrokerOrderEvent, ...]
    status: IntentStatus


def _generate_child_key(*, prefix: str, parent_intent_id: int) -> str:
    """Build a generated idempotency key for a status/cancel child intent (L70).

    Shape: ``{prefix}-{parent_intent_id}-{8 hex chars}``. Mirrors the
    ``MP-7B-{id}-{short_key}`` style of ``build_order_ref`` so logs read
    consistently.
    """

    suffix = secrets.token_hex(4)  # 8 hex chars
    return f"{prefix}-{parent_intent_id}-{suffix}"


def _validate_place_parent(intent: BrokerOrderIntent) -> None:
    if intent.action != _ACTION_PLACE:
        raise OrderSafetyError(
            f"intent {intent.id} has action {intent.action!r}; "
            "fetch_status/cancel_order require a place intent (L42)"
        )


def fetch_status(
    session: Session,
    *,
    client: BrokerOrderClient,
    intent_id: int,
) -> StatusCommandResult:
    """Drive a status-check command for a known place intent (L46/L62).

    Algorithm:

    1. Load the parent place intent. ``LookupError`` propagates if missing
       (L15: only locally known intents are eligible).
    2. Refuse if ``intent.action != "place"`` (L42).
    3. Create a child ``status_check`` intent with a *generated* idempotency
       key (L70) and ``parent_intent_id=intent_id`` (L46).
    4. If the parent lacks ``broker_order_id`` (place failed before broker
       assigned an id, L44): append ``safety_rejected`` to the child, transition
       to ``rejected``, return.
    5. Otherwise call ``client.fetch_order_status`` and translate exceptions /
       observations into events on the child, ending in
       ``completed`` / ``failed`` / ``sent`` (callback timeout, L69).
    """

    place = get_intent_by_id(session, intent_id)
    _validate_place_parent(place)

    now = _now()
    child_key = _generate_child_key(prefix="status", parent_intent_id=intent_id)
    child_context: dict[str, Any] = {
        "parent_intent_id": intent_id,
        "parent_broker_order_id": place.broker_order_id,
        "parent_account_id": place.account_id,
    }
    child = create_intent(
        session,
        action=_ACTION_STATUS,
        broker=_BROKER,
        broker_environment=place.broker_environment,
        account_id=place.account_id,
        local_idempotency_key=child_key,
        context=child_context,
        parent_intent_id=intent_id,
        created_at=now,
    )

    # L44 — parent never received a broker_order_id; status is not eligible.
    if not place.broker_order_id:
        message = (
            f"parent intent {intent_id} lacks broker_order_id; "
            "not eligible for status (L44)"
        )
        event = _record_event(
            session,
            intent_id=child.id,
            event_type="safety_rejected",
            event_source="service_safety",
            observed_at=now,
            message=message,
        )
        transition_status(session, intent_id=child.id, new_status="rejected")
        return StatusCommandResult(intent=child, events=(event,), status="rejected")

    # Broker call.
    try:
        result = client.fetch_order_status(
            broker_order_id=place.broker_order_id,
            account_id=place.account_id,
        )
    except OrderAccountMismatchError as exc:
        event = _record_event(
            session,
            intent_id=child.id,
            event_type="account_mismatch",
            event_source="adapter_callback",
            message=str(exc) or "managedAccounts mismatch",
        )
        transition_status(session, intent_id=child.id, new_status="failed")
        return StatusCommandResult(intent=child, events=(event,), status="failed")
    except OrderConnectionError as exc:
        event = _record_event(
            session,
            intent_id=child.id,
            event_type="connection_failed",
            event_source="adapter_callback",
            message=str(exc) or "TWS/Gateway connection failed",
        )
        transition_status(session, intent_id=child.id, new_status="failed")
        return StatusCommandResult(intent=child, events=(event,), status="failed")
    except OrderCallbackTimeoutError as exc:
        message = f"callback_timeout: {exc!s}" if str(exc) else "callback_timeout"
        event = _record_event(
            session,
            intent_id=child.id,
            event_type="error",
            event_source="timeout",
            message=message,
        )
        # L69: callback never arrived — we can't prove the broker side, so park
        # the child at ``sent`` for forensic visibility.
        transition_status(session, intent_id=child.id, new_status="sent")
        return StatusCommandResult(intent=child, events=(event,), status="sent")
    except OrderBrokerCallError as exc:
        event = _record_event(
            session,
            intent_id=child.id,
            event_type="error",
            event_source="adapter_callback",
            message=str(exc) or "broker error during status fetch",
        )
        transition_status(session, intent_id=child.id, new_status="failed")
        return StatusCommandResult(intent=child, events=(event,), status="failed")

    return _process_status_or_cancel_success(
        session,
        child=child,
        observations=result.observations,
        kind="status",
    )


def cancel_order(
    session: Session,
    *,
    client: BrokerOrderClient,
    intent_id: int,
    confirm_cancel: bool = False,
) -> CancelCommandResult:
    """Drive a cancel-order command for a known place intent (L45/L63).

    Fail-closed gate: without ``confirm_cancel=True`` we raise BEFORE creating
    a child intent (L21). This keeps a stray CLI call from leaving spurious
    provenance.

    Algorithm:

    1. Refuse if ``confirm_cancel`` is False (L21).
    2. Load parent place intent. ``LookupError`` propagates if missing.
    3. Refuse if parent action != "place" (L42).
    4. Create child ``cancel`` intent with generated key (L70).
    5. If parent lacks ``broker_order_id``: ``safety_rejected`` + ``rejected``.
    6. Otherwise call ``client.cancel_order(..., was_transmitted=place.transmit)``.
       The adapter chooses ``staged_cancelled`` (transmit=False, L63) vs
       ``broker_cancel_requested`` + ``cancelled`` (transmit=True).
    7. Translate exceptions/observations into events on the child.
    """

    if not confirm_cancel:
        raise OrderSafetyError(
            "cancel_order requires confirm_cancel=True (L21); refusing "
            "before creating any provenance"
        )

    place = get_intent_by_id(session, intent_id)
    _validate_place_parent(place)

    now = _now()
    child_key = _generate_child_key(prefix="cancel", parent_intent_id=intent_id)
    child_context: dict[str, Any] = {
        "parent_intent_id": intent_id,
        "parent_broker_order_id": place.broker_order_id,
        "parent_account_id": place.account_id,
        "parent_transmit": bool(place.transmit),
    }
    child = create_intent(
        session,
        action=_ACTION_CANCEL,
        broker=_BROKER,
        broker_environment=place.broker_environment,
        account_id=place.account_id,
        local_idempotency_key=child_key,
        context=child_context,
        parent_intent_id=intent_id,
        created_at=now,
    )

    if not place.broker_order_id:
        message = (
            f"parent intent {intent_id} lacks broker_order_id; "
            "not eligible for cancel (L44)"
        )
        event = _record_event(
            session,
            intent_id=child.id,
            event_type="safety_rejected",
            event_source="service_safety",
            observed_at=now,
            message=message,
        )
        transition_status(session, intent_id=child.id, new_status="rejected")
        return CancelCommandResult(intent=child, events=(event,), status="rejected")

    try:
        result = client.cancel_order(
            broker_order_id=place.broker_order_id,
            account_id=place.account_id,
            was_transmitted=bool(place.transmit),
        )
    except OrderAccountMismatchError as exc:
        event = _record_event(
            session,
            intent_id=child.id,
            event_type="account_mismatch",
            event_source="adapter_callback",
            message=str(exc) or "managedAccounts mismatch",
        )
        transition_status(session, intent_id=child.id, new_status="failed")
        return CancelCommandResult(intent=child, events=(event,), status="failed")
    except OrderConnectionError as exc:
        event = _record_event(
            session,
            intent_id=child.id,
            event_type="connection_failed",
            event_source="adapter_callback",
            message=str(exc) or "TWS/Gateway connection failed",
        )
        transition_status(session, intent_id=child.id, new_status="failed")
        return CancelCommandResult(intent=child, events=(event,), status="failed")
    except OrderCallbackTimeoutError as exc:
        message = f"callback_timeout: {exc!s}" if str(exc) else "callback_timeout"
        event = _record_event(
            session,
            intent_id=child.id,
            event_type="error",
            event_source="timeout",
            message=message,
        )
        transition_status(session, intent_id=child.id, new_status="sent")
        return CancelCommandResult(intent=child, events=(event,), status="sent")
    except OrderBrokerCallError as exc:
        event = _record_event(
            session,
            intent_id=child.id,
            event_type="error",
            event_source="adapter_callback",
            message=str(exc) or "broker error during cancel",
        )
        transition_status(session, intent_id=child.id, new_status="failed")
        return CancelCommandResult(intent=child, events=(event,), status="failed")

    return _process_status_or_cancel_success(
        session,
        child=child,
        observations=result.observations,
        kind="cancel",
    )


def _process_status_or_cancel_success(
    session: Session,
    *,
    child: BrokerOrderIntent,
    observations: tuple[BrokerOrderObservation, ...],
    kind: str,
) -> StatusCommandResult | CancelCommandResult:
    """Persist observations on a status/cancel child and mark it completed."""

    events: list[BrokerOrderEvent] = []
    for observation in observations:
        events.append(
            _record_event(
                session,
                intent_id=child.id,
                event_type=observation.event_type,
                event_source="adapter_callback",
                observation=observation,
            )
        )
    transition_status(session, intent_id=child.id, new_status="sent")
    transition_status(session, intent_id=child.id, new_status="completed")
    if kind == "status":
        return StatusCommandResult(
            intent=child, events=tuple(events), status="completed"
        )
    return CancelCommandResult(intent=child, events=tuple(events), status="completed")
