"""Protocol for the Phase 7b manual paper order pilot broker client.

This is the seam between ``order_service.py`` (pure orchestration over a
SQLAlchemy session) and ``ibkr_order_client.py`` (the only module that may
import ``ibapi``). Production wires in the real IBKR adapter; tests wire in
a fake that returns deterministic ``BrokerOrderObservation`` tuples.
"""

from __future__ import annotations

from typing import Protocol

from marketpulse.broker.order_types import (
    BrokerOrderRequest,
    CancelOrderResult,
    OrderStatusResult,
    PlaceResult,
)


class BrokerOrderClient(Protocol):
    """Read/write client surface for the 7b order pilot.

    Each method returns a result whose ``observations`` tuple is the
    chronological sequence of immutable events that should be appended to
    ``broker_order_event`` by the service layer.
    """

    def place_lmt_order(
        self,
        *,
        request: BrokerOrderRequest,
        intent_id: int,
        order_ref: str,
    ) -> PlaceResult: ...

    def fetch_order_status(
        self,
        *,
        account_id: str,
        broker_order_id: str,
    ) -> OrderStatusResult: ...

    def cancel_order(
        self,
        *,
        account_id: str,
        broker_order_id: str,
        staged: bool,
    ) -> CancelOrderResult: ...
