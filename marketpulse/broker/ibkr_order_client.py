"""IBKR TWS/Gateway adapter for Phase 7b order pilot.

This is the ONLY module in the codebase allowed to import ``ibapi``.
Architecture guard `tests/architecture/test_phase7b_order_boundary.py`
enforces this.

T7a scope: pure helpers (no threading, no callbacks, no client state).
T7b adds the ``IbkrOrderClient`` class with ``EClient``/``EWrapper`` machinery,
``threading.Event`` synchronization, and bounded waits per L74.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ibapi.contract import Contract
from ibapi.order import Order

from marketpulse.broker.order_types import BrokerOrderObservation

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
