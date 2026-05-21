"""ForwardExecutionEngine — the ONLY Phase 6 ExecutionEngine implementation.

Per spec § 6 + locks ix, xxvii, xxx, xxiv, 6a-L2, 6a-L3, 6a-L4."""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal

from marketpulse.trading.clock import Clock
from marketpulse.trading.idempotency import compute_idempotency_key
from marketpulse.trading.kill_switch import KillSwitchState
from marketpulse.trading.repository import Repository
from marketpulse.trading.risk_gate import RiskGate
from marketpulse.trading.types import (
    AuditEventType,
    OrderId,
    OrderRejected,
    OrderRequest,
    PlaceOrderResult,
    TickResult,
)

VERSION = "v0"


def _dump(order_request: OrderRequest) -> dict:
    d = dataclasses.asdict(order_request)
    # Make Decimal / datetime / date JSON-friendly
    for k, v in list(d.items()):
        if isinstance(v, Decimal):
            d[k] = str(v)
        elif hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d


class ForwardExecutionEngine:
    VERSION = VERSION

    def __init__(
        self,
        *,
        repository: Repository,
        clock: Clock,
        kill_switch: KillSwitchState,
        risk_gate: RiskGate,
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._kill_switch = kill_switch
        self._risk_gate = risk_gate

    def place_order(self, *, order_request: OrderRequest) -> PlaceOrderResult:
        # Step 1: compute idempotency key (pure)
        key = compute_idempotency_key(order_request)

        # Step 2: idempotency hit (lock xxx / 6a-L5)
        existing = self._repo.find_paper_order_by_idempotency_key(key)
        if existing is not None:
            with self._repo.transaction():
                self._repo.write_duplicate_audit_once(
                    idempotency_key=key,
                    order_id=existing.id,
                    strategy=order_request.strategy,
                    tick_date=order_request.allocation_date,
                    context={
                        "allocation_run_id": order_request.allocation_run_id,
                    },
                    timestamp=self._clock.now(),
                )
            return PlaceOrderResult(
                order_id=OrderId(existing.id),
                created=False,
                duplicate=True,
            )

        # Step 3: kill switch (audit BEFORE raise — lock ix)
        if self._kill_switch.is_active():
            with self._repo.transaction():
                self._repo.write_audit_event(
                    event_type=AuditEventType.ORDER_REJECTED,
                    order_id=None,
                    strategy=order_request.strategy,
                    reason="kill_switch_active",
                    context={"order_request": _dump(order_request)},
                    timestamp=self._clock.now(),
                )
            raise OrderRejected("kill_switch_active")

        # Step 4: risk gate — fail-closed exception path (6a-L3)
        try:
            risk_result = self._risk_gate.check_pre_trade(
                order_request=order_request,
            )
        except Exception as e:
            with self._repo.transaction():
                self._repo.write_audit_event(
                    event_type=AuditEventType.ORDER_REJECTED,
                    order_id=None,
                    strategy=order_request.strategy,
                    reason="risk_gate_error",
                    context={
                        "order_request": _dump(order_request),
                        "error_type": type(e).__name__,
                        "error": str(e),
                    },
                    timestamp=self._clock.now(),
                )
            raise OrderRejected("risk_gate_error") from e

        if not risk_result.approved:
            with self._repo.transaction():
                self._repo.write_audit_event(
                    event_type=AuditEventType.ORDER_REJECTED,
                    order_id=None,
                    strategy=order_request.strategy,
                    reason=risk_result.reason,
                    context={
                        "order_request": _dump(order_request),
                        "gate": risk_result.gate_name,
                    },
                    timestamp=self._clock.now(),
                )
            raise OrderRejected(risk_result.reason)

        # Step 5: accepted — atomic INSERT order + ORDER_PLACED audit (6a-L2)
        with self._repo.transaction():
            order = self._repo.insert_paper_order(
                order_request=order_request,
                idempotency_key=key,
                placed_at=self._clock.now(),
            )
            self._repo.write_audit_event(
                event_type=AuditEventType.ORDER_PLACED,
                order_id=order.id,
                strategy=order_request.strategy,
                reason="",
                context={
                    "idempotency_key": key,
                    "allocation_run_id": order_request.allocation_run_id,
                },
                timestamp=self._clock.now(),
            )

        return PlaceOrderResult(
            order_id=OrderId(order.id),
            created=True,
            duplicate=False,
        )

    def cancel_order(self, *, order_id: OrderId) -> None:
        """Filled in Task 6a-2.6."""
        raise NotImplementedError

    def tick(self, *, as_of: date) -> TickResult:
        """Filled in Task 6a-2.7."""
        raise NotImplementedError
