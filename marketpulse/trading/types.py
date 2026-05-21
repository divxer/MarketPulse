"""Phase 6a shared vocabulary. Frozen dataclasses, enums, exceptions.

Nothing here imports from other marketpulse.trading.* modules — types is
the bottom of the dependency tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, NewType

# === ID newtypes ===

OrderId = NewType("OrderId", int)
AllocationRunId = NewType("AllocationRunId", str)


# === Status / side enums (as Literal aliases, not Enum classes — keeps
# DB CHECK constraints simple) ===

OrderStatus = Literal["PLACED", "ENTRY_FILLED", "CANCELLED"]
PositionStatus = Literal["OPEN", "CLOSED"]
FillSide = Literal["ENTRY", "EXIT"]


# === Audit event types — 12 in 6a (6b/6g extend) ===

class AuditEventType(StrEnum):
    ORDER_PLACED = "ORDER_PLACED"
    ORDER_PLACED_DUPLICATE = "ORDER_PLACED_DUPLICATE"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_ENTRY_FILLED = "ORDER_ENTRY_FILLED"
    POSITION_CLOSED = "POSITION_CLOSED"
    KILL_SWITCH_FLIPPED = "KILL_SWITCH_FLIPPED"
    KILL_SWITCH_CYCLE_SKIPPED = "KILL_SWITCH_CYCLE_SKIPPED"
    TICK_COMPLETED = "TICK_COMPLETED"
    TICK_REPROCESSED_COMPLETED = "TICK_REPROCESSED_COMPLETED"
    SCHEDULER_GAP_DETECTED = "SCHEDULER_GAP_DETECTED"
    ENGINE_INVARIANT_ERROR = "ENGINE_INVARIANT_ERROR"


# === Exceptions ===

class OrderRejected(Exception):
    """Raised by ExecutionEngine.place_order when an order is rejected
    (kill switch, risk gate, etc.). Lock ix: raised ONLY after the
    ORDER_REJECTED audit row commits."""


class InvariantError(Exception):
    """Raised when a runtime invariant is violated. Caught by
    ExecutionEngine.tick to record a TickError and continue with the
    remaining rows. Phase 6 considers it a bug; Phase 7 may relax."""


# === Boundary objects ===

@dataclass(frozen=True)
class OrderRequest:
    """RiskGates produce, ExecutionEngine consumes. The constructor is
    the float → Decimal quantization site (lock xxii)."""

    strategy: str
    ticker: str
    quantity: int                       # signed; positive only in Phase 6
    event_time: datetime                # UTC, tz-aware (lock xxix)
    allocation_date: date               # NY trading day
    event_price: Decimal                # quantized
    horizon_date: date
    horizon_price: Decimal | None
    allocation_run_id: AllocationRunId

    # Versioning for replay determinism (lock xxviii)
    strategy_version: str
    allocator_version: str
    execution_engine_version: str

    # Phase 5 allocation provenance (lock x)
    weight: float
    raw_bid_weight: float | None
    pool_corr: float | None
    contribution_multiplier: float
    adjusted_bid_weight: float | None
    effective_corr_window: int
    rewarded_for_negative_corr: bool
    would_change_rank: bool
    size_clamped_by_override: bool


@dataclass(frozen=True)
class TickError:
    """Structured invariant-error record (6a-L4)."""
    phase: Literal["entry_materialization", "exit_materialization"]
    order_id: int | None
    position_id: int | None
    error: str


@dataclass(frozen=True)
class TickResult:
    """ExecutionEngine.tick() return shape."""
    as_of: date
    entries_materialized: int
    exits_materialized: int
    errors: tuple[TickError, ...]


@dataclass(frozen=True)
class PlaceOrderResult:
    """ExecutionEngine.place_order() return shape (6a-L2). The flags
    eliminate the TOCTOU race that a caller-side pre-check would
    introduce."""
    order_id: OrderId
    created: bool
    duplicate: bool
