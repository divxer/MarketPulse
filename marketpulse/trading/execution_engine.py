"""ExecutionEngine Protocol — Phase 6 command-only contract.

Phase 6 ships ForwardExecutionEngine.
Phase 7 will add BrokerExecutionEngine.
Stretch 6d may add RealtimeExecutionEngine.

All implementations are structural (Protocol). Downstream code never
knows which one is running (lock vi)."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from marketpulse.trading.types import (
    OrderId,
    OrderRequest,
    PlaceOrderResult,
    TickResult,
)


class ExecutionEngine(Protocol):
    """Command-only Protocol. Reads of canonical state happen via DB
    query helpers in repository.py (execution-path) or future
    query_models.py (UI/observability — deferred to 6f/6g)."""

    def place_order(self, *, order_request: OrderRequest) -> PlaceOrderResult: ...

    def cancel_order(self, *, order_id: OrderId) -> None: ...

    def tick(self, *, as_of: date) -> TickResult: ...
