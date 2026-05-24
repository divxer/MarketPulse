"""Phase 7b order command DTOs.

Compare ``marketpulse/broker/types.py`` which holds **broker truth snapshot
DTOs** (positions, cash, executions sourced from the Phase 7a Flex Web
Service read-only sync). This module holds **order command DTOs** (place /
status / cancel intents and the immutable observation events emitted while
those intents are being driven against TWS/IB Gateway).

Phase 7b is a manual, paper-account-only pilot — none of the types here are
permitted to touch any ``paper_*`` table, and no field on ``BrokerOrderRequest``
encodes anything other than a paper STK LMT order (see ``__post_init__``).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

# --- Type aliases ----------------------------------------------------------

OrderAction = Literal["place", "cancel", "status_check"]
OrderSide = Literal["BUY", "SELL"]
OrderType = Literal["LMT"]
AssetClass = Literal["STK"]
IntentStatus = Literal["created", "sent", "completed", "rejected", "failed"]
EventType = Literal[
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
]
EventSource = Literal["adapter_callback", "service_safety", "cli_validation", "timeout"]
BrokerEnvironment = Literal["paper", "live", "unknown"]

# Legacy aliases that mirror the names used in Phase 7a-Flex helpers so the
# rest of the 7b stack can opt into either spelling.
BrokerOrderAction = OrderAction
BrokerOrderIntentStatus = IntentStatus
BrokerOrderEventSource = EventSource
BrokerOrderEventType = EventType
OrderAccountEnvironment = BrokerEnvironment


# --- Account safety regex (L26/L30) ---------------------------------------

# Mirrors ``marketpulse/broker/types.py:classify_broker_environment_from_account_id``.
# The Flex sync widened the paper regex to ``DU[A-Z]*\d+`` (e.g. ``DUE411848``);
# 7b reuses the same shape so a Flex-approved paper account is always a 7b-
# approved paper account.
_PAPER_RE = re.compile(r"^DU[A-Z]*\d+$")
_LIVE_RE = re.compile(r"^U\d+$")


def classify_order_account(account_id: str | None) -> BrokerEnvironment:
    """Return the safety environment a broker account ID maps to.

    L26/L30: only strings matching ``^DU[A-Z]*\\d+$`` are treated as paper.
    Anything else (empty, ``None``, ``U<digits>``, free-form) is rejected by
    the order pilot — ``classify_order_account`` is the single regex that
    governs that safety gate.
    """

    if not account_id:
        return "unknown"
    if _PAPER_RE.match(account_id):
        return "paper"
    if _LIVE_RE.match(account_id):
        return "live"
    return "unknown"


# --- orderRef builder (L67) -----------------------------------------------

_ORDER_REF_MAX_LEN = 32


def build_order_ref(*, intent_id: int, local_idempotency_key: str) -> str:
    """Build the deterministic ``orderRef`` sent to TWS for a 7b intent.

    Shape: ``MP-7B-{intent_id}-{short_key}`` where ``short_key`` is the first
    8 hex characters of ``sha256(local_idempotency_key)``. The full ref must
    be at most 32 characters; if the rendered string exceeds the limit (for
    example because ``intent_id`` is improbably large) we raise rather than
    silently truncating, because the broker-side ``orderRef`` is the only
    cross-system handle we have for reconciling.
    """

    if not local_idempotency_key or not re.search(r"[A-Za-z0-9]", local_idempotency_key):
        raise ValueError(
            "local_idempotency_key must contain at least one alphanumeric character"
        )
    short_key = hashlib.sha256(local_idempotency_key.encode("utf-8")).hexdigest()[:8]
    ref = f"MP-7B-{intent_id}-{short_key}"
    if len(ref) > _ORDER_REF_MAX_LEN:
        raise ValueError(f"orderRef exceeds {_ORDER_REF_MAX_LEN} characters: {ref}")
    return ref


# --- Request DTO -----------------------------------------------------------


@dataclass(frozen=True)
class BrokerOrderRequest:
    """Immutable description of one place-order command from an operator.

    7b MVP scope: STK + LMT + positive quantity + positive limit price only.
    Validation runs in ``__post_init__`` so an invalid request cannot exist.
    """

    account_id: str
    symbol: str
    asset_class: str
    side: OrderSide
    quantity: Decimal
    order_type: str
    limit_price: Decimal | None
    transmit: bool
    local_idempotency_key: str

    def __post_init__(self) -> None:
        if self.asset_class != "STK":
            raise ValueError("7b MVP supports asset_class=STK only")
        if self.order_type != "LMT":
            raise ValueError("7b MVP supports order_type=LMT only")
        if self.limit_price is None:
            raise ValueError("limit_price is required for 7b LMT orders")
        if self.limit_price <= 0:
            raise ValueError("limit_price must be positive")
        if self.quantity is None or self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if not self.account_id:
            raise ValueError("account_id is required")
        if not self.symbol:
            raise ValueError("symbol is required")
        if not self.local_idempotency_key:
            raise ValueError("local_idempotency_key is required")


# --- Observation + result DTOs --------------------------------------------


@dataclass(frozen=True)
class BrokerOrderObservation:
    """One immutable event captured while driving a broker intent."""

    event_type: EventType
    event_source: EventSource
    observed_at: datetime
    broker_order_id: str | None = None
    broker_perm_id: str | None = None
    broker_status: str | None = None
    filled_quantity: Decimal | None = None
    remaining_quantity: Decimal | None = None
    avg_fill_price: Decimal | None = None
    message: str | None = None
    raw: dict | None = None


@dataclass(frozen=True)
class PlaceOrderResult:
    broker_order_id: str
    order_ref: str
    observations: tuple[BrokerOrderObservation, ...]


@dataclass(frozen=True)
class OrderStatusResult:
    observations: tuple[BrokerOrderObservation, ...]


@dataclass(frozen=True)
class CancelOrderResult:
    observations: tuple[BrokerOrderObservation, ...]


# --- Error hierarchy -------------------------------------------------------


class OrderError(Exception):
    """Base class for all Phase 7b order pilot errors."""


class OrderSafetyError(OrderError):
    """Order pilot refused before broker mutation for safety/config reasons."""


class OrderAccountMismatchError(OrderSafetyError):
    """The requested account_id does not match the configured paper account."""


class OrderDuplicateError(OrderSafetyError):
    """A place intent with the same (account, action, key) already exists."""


class OrderConnectionError(OrderError):
    """TWS/Gateway connection or session validation failed."""


class OrderBrokerCallError(OrderError):
    """The broker rejected or errored on a call we successfully delivered."""


class OrderCallbackTimeoutError(OrderError):
    """Expected broker callback did not arrive before the bounded timeout."""


# Phase-7a-style aliases for backwards-compatible imports.
BrokerOrderSafetyError = OrderSafetyError
BrokerOrderConnectionError = OrderConnectionError
BrokerOrderTimeoutError = OrderCallbackTimeoutError
