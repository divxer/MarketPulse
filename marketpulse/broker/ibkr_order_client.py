"""IBKR TWS/Gateway adapter for Phase 7b order pilot.

This is the ONLY module in the codebase allowed to import ``ibapi``.
Architecture guard `tests/architecture/test_phase7b_order_boundary.py`
enforces this.

T7a scope: pure helpers (no threading, no callbacks, no client state).
T7b adds the ``IbkrOrderClient`` class with ``EClient``/``EWrapper`` machinery,
``threading.Event`` synchronization, and bounded waits per L74.
"""

from __future__ import annotations

import contextlib
import copy
import math
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.wrapper import EWrapper

from marketpulse.broker.order_types import (
    BrokerOrderObservation,
    BrokerOrderRequest,
    CancelResult,
    OrderAccountMismatchError,
    OrderCallbackTimeoutError,
    OrderConnectionError,
    PlaceResult,
    StatusResult,
)

# --- Sensitive-key redaction list -----------------------------------------

_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "token",
        "password",
        "secret",
        "session_id",
        "session",
        "auth",
        "cred",
        "credentials",
        "api_key",
        "apikey",
    }
)

_REDACTED = "[redacted]"

# IBKR uses sys.float_info.max as an "unset" sentinel on many fields. Anything
# above this threshold is treated as None (the field was never populated).
_UNSET_THRESHOLD = 1e300

# Plain JSON-serializable scalar types we pass through untouched.
_SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool, type(None))


# --- _decimal_or_none -----------------------------------------------------


def _decimal_or_none(value: object) -> Decimal | None:
    """Normalize an IBKR-callback value to a ``Decimal`` or ``None``.

    IBKR callbacks routinely pass ``""``, ``"NaN"``, ``inf``, or the sentinel
    ``1.7976931348623157E308`` (``sys.float_info.max``) for "unset" numeric
    fields. We collapse all of those to ``None`` so the rest of the stack can
    treat absent values uniformly.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        # bool is a subclass of int — treat True/False as numeric 1/0.
        return Decimal(int(value))
    if isinstance(value, Decimal):
        if value.is_nan() or value.is_infinite():
            return None
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.lower() == "nan":
            return None
        try:
            as_float = float(stripped)
        except (ValueError, TypeError):
            return None
        if math.isnan(as_float) or math.isinf(as_float):
            return None
        if abs(as_float) > _UNSET_THRESHOLD:
            return None
        try:
            return Decimal(stripped)
        except (InvalidOperation, ValueError):
            return None
    if isinstance(value, (int, float)):
        as_float = float(value)
        if math.isnan(as_float) or math.isinf(as_float):
            return None
        if abs(as_float) > _UNSET_THRESHOLD:
            return None
        # Prefer round-tripping through str(value) so "12.34" stays exact.
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    return None


# --- _sanitize_raw --------------------------------------------------------


def _sanitize_value(value: Any) -> Any:
    """Recursively sanitize an arbitrary value for ``broker_order_event.raw``.

    - dicts: descend, redacting sensitive keys (case-insensitive)
    - lists/tuples: descend, sanitize each element
    - scalars (str/int/float/bool/None): pass through
    - anything else (objects, datetimes, ...): ``repr()`` so the value is JSON-
      serializable without leaking ibapi objects
    """

    if isinstance(value, Mapping):
        return _sanitize_raw(dict(value))
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(v) for v in value]
    if isinstance(value, _SCALAR_TYPES):
        return value
    return repr(value)


def _sanitize_raw(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-copy + redact ``raw`` for safe persistence on ``broker_order_event``.

    L53: ``broker_order_event.raw`` must be sanitized JSON only — never raw
    ibapi objects, credentials, or tokens. We:

    1. Deep-copy so callers can't accidentally observe redaction in their
       in-memory copy.
    2. Lowercase each key and redact if it matches ``_SENSITIVE_KEYS``.
    3. Recurse into nested dicts and lists.
    4. ``repr()`` any non-scalar value so the output is JSON-friendly.
    """

    # Deep copy first so nested dicts/lists are independent from the source.
    source = copy.deepcopy(dict(raw))
    out: dict[str, Any] = {}
    for key, value in source.items():
        if isinstance(key, str) and key.lower() in _SENSITIVE_KEYS:
            out[key] = _REDACTED
            continue
        out[key] = _sanitize_value(value)
    return out


# --- _map_order_status_event ---------------------------------------------


# IBKR statuses that mean "order rejected by exchange / inactive at broker".
_REJECTED_STATUSES: frozenset[str] = frozenset({"Inactive"})
# IBKR statuses that mean "order cancelled".
_CANCELLED_STATUSES: frozenset[str] = frozenset({"Cancelled", "ApiCancelled"})


def _map_order_status_event(
    observed_at: datetime,
    broker_order_id: str | None,
    status: str,
    filled: object,
    remaining: object,
    avg_fill_price: object,
    perm_id: str | None,
    raw: Mapping[str, Any],
) -> BrokerOrderObservation:
    """Translate an ``orderStatus`` callback into a ``BrokerOrderObservation``.

    L47: ``broker_status`` preserves the raw IBKR string so downstream readers
    can distinguish e.g. ``PreSubmitted`` from ``Submitted`` even though both
    map to ``event_type='order_status_seen'``.

    L51: unknown statuses fall through to ``order_status_seen`` (with the raw
    status preserved) rather than inventing a new schema state.
    """

    filled_qty = _decimal_or_none(filled)
    remaining_qty = _decimal_or_none(remaining)
    avg_price = _decimal_or_none(avg_fill_price)

    event_type: str
    if status == "Filled" and filled_qty is not None and filled_qty > 0:
        event_type = "filled"
    elif status in _CANCELLED_STATUSES:
        event_type = "cancelled"
    elif status in _REJECTED_STATUSES:
        event_type = "rejected"
    else:
        event_type = "order_status_seen"

    return BrokerOrderObservation(
        event_type=event_type,  # type: ignore[arg-type]
        broker_order_id=broker_order_id,
        broker_perm_id=perm_id,
        broker_status=status,
        filled_quantity=filled_qty,
        remaining_quantity=remaining_qty,
        avg_fill_price=avg_price,
        raw=_sanitize_raw(raw),
    )


# --- _build_contract -----------------------------------------------------


def _build_contract(symbol: str, asset_class: str) -> Contract:
    """Build the ``ibapi`` ``Contract`` for a 7b paper STK order.

    7b MVP supports STK only (L from order_types). Any other asset class
    raises ``ValueError`` — futures/options/etc. are out of scope.
    """

    if asset_class != "STK":
        raise ValueError(
            f"7b MVP supports asset_class=STK only; got {asset_class!r}"
        )
    contract = Contract()
    contract.symbol = symbol
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    return contract


# --- _build_order --------------------------------------------------------


def _build_order(
    side: str,
    quantity: Decimal,
    order_type: str,
    limit_price: Decimal | None,
    transmit: bool,
    order_ref: str,
) -> Order:
    """Build the ``ibapi`` ``Order`` for a 7b paper LMT order.

    7b MVP supports LMT only. ``tif`` is hard-coded to ``DAY``.
    """

    if order_type != "LMT":
        raise ValueError(
            f"7b MVP supports order_type=LMT only; got {order_type!r}"
        )
    order = Order()
    order.action = side
    order.totalQuantity = quantity
    order.orderType = "LMT"
    order.lmtPrice = limit_price
    order.tif = "DAY"
    order.transmit = transmit
    order.orderRef = order_ref
    return order


# --- _IbkrOrderApp -------------------------------------------------------
#
# Module-private callback target. This is the ONLY place where ``ibapi`` types
# are exposed; it never leaves the adapter (L37). ``IbkrOrderClient`` owns the
# instance for the duration of a single command and disposes of it before
# returning a DTO result.


class _IbkrOrderApp(EWrapper, EClient):
    """Callback target for one ``IbkrOrderClient`` command.

    All synchronization uses ``threading.Event`` (L74); no callback uses
    ``time.sleep``. The reader thread (``EClient.run``) populates fields and
    triggers events; the main thread drains them after each bounded ``wait``.
    """

    def __init__(self) -> None:
        EClient.__init__(self, self)
        self.next_valid_id_event = threading.Event()
        self.next_valid_id: int | None = None
        self.managed_accounts_event = threading.Event()
        self.managed_accounts: tuple[str, ...] = ()
        self.observation_event = threading.Event()
        self.observations: list[BrokerOrderObservation] = []
        self.errors: list[dict[str, Any]] = []
        self.broker_order_id: str | None = None
        self.broker_perm_id: str | None = None

    # --- ibapi EWrapper callbacks ----------------------------------------
    def nextValidId(self, orderId: int) -> None:  # noqa: N802 — ibapi name
        self.next_valid_id = int(orderId)
        self.next_valid_id_event.set()

    def managedAccounts(self, accountsList: str) -> None:  # noqa: N802
        self.managed_accounts = tuple(
            a.strip() for a in accountsList.split(",") if a.strip()
        )
        self.managed_accounts_event.set()

    def orderStatus(  # noqa: N802
        self,
        orderId,
        status,
        filled,
        remaining,
        avgFillPrice,
        permId,
        parentId=0,
        lastFillPrice=0,
        clientId=0,
        whyHeld="",
        mktCapPrice=0,
    ) -> None:
        obs = _map_order_status_event(
            observed_at=datetime.now(UTC),
            broker_order_id=str(orderId),
            status=str(status),
            filled=filled,
            remaining=remaining,
            avg_fill_price=avgFillPrice,
            perm_id=str(permId) if permId else None,
            raw={
                "orderId": orderId,
                "status": status,
                "filled": str(filled),
                "remaining": str(remaining),
                "avgFillPrice": str(avgFillPrice),
                "permId": permId,
                "parentId": parentId,
                "whyHeld": whyHeld,
            },
        )
        self.observations.append(obs)
        self.broker_order_id = str(orderId)
        if permId:
            self.broker_perm_id = str(permId)
        self.observation_event.set()

    def openOrder(  # noqa: N802
        self, orderId, contract, order, orderState
    ) -> None:
        self.observations.append(
            BrokerOrderObservation(
                event_type="open_order_seen",
                broker_order_id=str(orderId),
                broker_status=getattr(orderState, "status", None),
                raw=_sanitize_raw(
                    {
                        "orderId": orderId,
                        "status": getattr(orderState, "status", None),
                    }
                ),
            )
        )
        self.observation_event.set()

    def error(  # noqa: N802
        self, reqId, errorCode, errorString, *args, **kwargs
    ) -> None:
        self.errors.append(
            {
                "reqId": reqId,
                "errorCode": errorCode,
                "errorString": errorString,
            }
        )
        # Don't set observation_event — main thread decides fatality.


# --- IbkrOrderClient -----------------------------------------------------


class IbkrOrderClient:
    """Public broker order adapter for the Phase 7b paper pilot.

    Exposes ONLY the three Protocol methods (L35): ``place_lmt_order``,
    ``fetch_order_status``, ``cancel_order``. Connection + reader thread are
    started fresh per command and torn down before return, so no ibapi state
    is shared with orchestration (L37). All waits are bounded with
    ``threading.Event`` deadlines (L74); ``OrderCallbackTimeoutError``
    distinguishes pre- vs post-``placeOrder`` to drive the service-side
    status decision (L69).
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        client_id: int,
        account_id: str,
        connect_timeout_seconds: int = 10,
        next_valid_id_timeout_seconds: int = 10,
        observation_timeout_seconds: int = 15,
        app_factory: Any = None,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._account_id = account_id
        self._connect_timeout = connect_timeout_seconds
        self._next_valid_id_timeout = next_valid_id_timeout_seconds
        self._observation_timeout = observation_timeout_seconds
        self._app_factory = app_factory or _IbkrOrderApp

    # --- private helpers --------------------------------------------------

    def _connect_and_validate(self) -> tuple[_IbkrOrderApp, threading.Thread]:
        """Connect, start reader, wait for ``managedAccounts``, validate account.

        Returns ``(app, reader)`` — the caller MUST pass ``reader`` to
        ``_safe_disconnect`` so the reader thread is joined on tear-down (no
        leaked daemon threads in long-running processes).

        Raises ``OrderConnectionError`` for any connect-time failure or if the
        ``managedAccounts`` callback never arrives; raises
        ``OrderAccountMismatchError`` if the connected accounts don't include
        ``self._account_id`` (L40 safety — never proceed to placeOrder against
        an account the broker hasn't confirmed it manages for this session).
        """

        app = self._app_factory()
        try:
            app.connect(self._host, self._port, self._client_id)
        except Exception as exc:
            raise OrderConnectionError(
                f"failed to connect to {self._host}:{self._port}: {exc}"
            ) from exc

        reader = threading.Thread(
            target=app.run, daemon=True, name="ibkr-order-reader"
        )
        reader.start()

        if not app.managed_accounts_event.wait(self._connect_timeout):
            self._safe_disconnect(app, reader)
            raise OrderConnectionError(
                "managedAccounts callback did not arrive before timeout"
            )

        if self._account_id not in app.managed_accounts:
            self._safe_disconnect(app, reader)
            raise OrderAccountMismatchError(
                f"requested account {self._account_id!r} not in managed "
                f"accounts {app.managed_accounts!r}"
            )
        return app, reader

    @staticmethod
    def _safe_disconnect(
        app: _IbkrOrderApp, reader: threading.Thread | None = None
    ) -> None:
        with contextlib.suppress(Exception):
            app.disconnect()
        if reader is not None:
            reader.join(timeout=2.0)

    # --- BrokerOrderClient Protocol --------------------------------------

    def place_lmt_order(
        self,
        request: BrokerOrderRequest,
        *,
        intent_id: int,
        order_ref: str,
    ) -> PlaceResult:
        app, reader = self._connect_and_validate()
        try:
            # Step 1: get a fresh order id.
            app.reqIds(-1)
            if not app.next_valid_id_event.wait(self._next_valid_id_timeout):
                raise OrderCallbackTimeoutError(
                    "nextValidId callback did not arrive before timeout",
                    placeorder_called=False,
                )
            assert app.next_valid_id is not None
            order_id = app.next_valid_id

            contract = _build_contract(request.symbol, request.asset_class)
            order = _build_order(
                side=request.side,
                quantity=request.quantity,
                order_type=request.order_type,
                limit_price=request.limit_price,
                transmit=request.transmit,
                order_ref=order_ref,
            )

            # Reset before issuing the place so the wait below only sees
            # callbacks caused by this placeOrder.
            app.observation_event.clear()
            app.placeOrder(order_id, contract, order)

            if not request.transmit:
                # L41: staged_to_tws — IBKR may never callback for a staged
                # order, so synthesize the observation and short-circuit.
                app.observations.append(
                    BrokerOrderObservation(
                        event_type="staged_to_tws",
                        broker_order_id=str(order_id),
                        broker_status="Staged",
                        raw={
                            "order_id": order_id,
                            "transmit": False,
                            "order_ref": order_ref,
                        },
                        message="staged in TWS, not submitted to broker",
                    )
                )
                app.broker_order_id = str(order_id)
                return PlaceResult(
                    placeorder_called=True,
                    broker_order_id=app.broker_order_id,
                    broker_perm_id=app.broker_perm_id,
                    managed_accounts=app.managed_accounts,
                    observations=tuple(app.observations),
                )

            # transmit=True: wait for the first orderStatus/openOrder callback.
            if not app.observation_event.wait(self._observation_timeout):
                raise OrderCallbackTimeoutError(
                    "orderStatus callback did not arrive before timeout",
                    placeorder_called=True,
                )

            # L41: synthesize submitted_to_broker if absent so the service
            # always sees the canonical "we asked TWS to send it" marker.
            has_submitted = any(
                o.event_type == "submitted_to_broker" for o in app.observations
            )
            if not has_submitted:
                app.observations.insert(
                    0,
                    BrokerOrderObservation(
                        event_type="submitted_to_broker",
                        broker_order_id=str(order_id),
                        raw={
                            "order_id": order_id,
                            "transmit": True,
                            "order_ref": order_ref,
                        },
                        message="placeOrder returned",
                    ),
                )
            if app.broker_order_id is None:
                app.broker_order_id = str(order_id)

            return PlaceResult(
                placeorder_called=True,
                broker_order_id=app.broker_order_id,
                broker_perm_id=app.broker_perm_id,
                managed_accounts=app.managed_accounts,
                observations=tuple(app.observations),
            )
        finally:
            self._safe_disconnect(app, reader)

    def fetch_order_status(
        self,
        *,
        broker_order_id: str,
        account_id: str,
    ) -> StatusResult:
        if account_id != self._account_id:
            raise OrderAccountMismatchError(
                f"requested account {account_id!r} != client account "
                f"{self._account_id!r}"
            )
        app, reader = self._connect_and_validate()
        try:
            app.observation_event.clear()
            # L62: current-session-only visibility — ask for currently-open
            # orders. If the broker has no state for this order in this
            # session, observations stays empty and ``success`` is still True.
            app.reqOpenOrders()
            app.observation_event.wait(self._observation_timeout)
            matching = tuple(
                o for o in app.observations if o.broker_order_id == broker_order_id
            )
            return StatusResult(
                success=True,
                managed_accounts=app.managed_accounts,
                observations=matching,
            )
        finally:
            self._safe_disconnect(app, reader)

    def cancel_order(
        self,
        *,
        broker_order_id: str,
        account_id: str,
        was_transmitted: bool,
    ) -> CancelResult:
        if account_id != self._account_id:
            raise OrderAccountMismatchError(
                f"requested account {account_id!r} != client account "
                f"{self._account_id!r}"
            )
        app, reader = self._connect_and_validate()
        try:
            if not was_transmitted:
                # L63: staged-cancelled — never reached the broker.
                obs = BrokerOrderObservation(
                    event_type="staged_cancelled",
                    broker_order_id=broker_order_id,
                    raw={
                        "broker_order_id": broker_order_id,
                        "was_transmitted": False,
                    },
                    message="staged order cancelled locally",
                )
                return CancelResult(
                    success=True,
                    managed_accounts=app.managed_accounts,
                    observations=(obs,),
                )

            app.observation_event.clear()
            try:
                cancel_arg = int(broker_order_id)
            except (TypeError, ValueError) as exc:
                raise OrderAccountMismatchError(
                    f"broker_order_id {broker_order_id!r} is not numeric"
                ) from exc

            # ibapi's cancelOrder may take 1 or 2 positional args depending
            # on version; the fake test app accepts variadic.
            try:
                app.cancelOrder(cancel_arg, "")
            except TypeError:
                app.cancelOrder(cancel_arg)

            request_obs = BrokerOrderObservation(
                event_type="broker_cancel_requested",
                broker_order_id=broker_order_id,
                raw={
                    "broker_order_id": broker_order_id,
                    "was_transmitted": True,
                },
                message="cancelOrder called",
            )
            if not app.observation_event.wait(self._observation_timeout):
                raise OrderCallbackTimeoutError(
                    "cancel orderStatus callback did not arrive before timeout",
                    placeorder_called=False,
                )
            broker_observations = tuple(
                o for o in app.observations if o.broker_order_id == broker_order_id
            )
            return CancelResult(
                success=True,
                managed_accounts=app.managed_accounts,
                observations=(request_obs,) + broker_observations,
            )
        finally:
            self._safe_disconnect(app, reader)
