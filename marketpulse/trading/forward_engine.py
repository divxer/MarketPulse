"""ForwardExecutionEngine — the ONLY Phase 6 ExecutionEngine implementation.

Per spec § 6 + locks ix, xxvii, xxx, xxiv, 6a-L2, 6a-L3, 6a-L4."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from marketpulse.trading.audit_json import normalize_for_json
from marketpulse.trading.clock import Clock
from marketpulse.trading.idempotency import compute_idempotency_key
from marketpulse.trading.kill_switch import KillSwitchState
from marketpulse.trading.repository import Repository
from marketpulse.trading.risk_gate import RiskGate
from marketpulse.trading.types import (
    AuditEventType,
    InvariantError,
    OrderId,
    OrderRejected,
    OrderRequest,
    PlaceOrderResult,
    TickError,
    TickResult,
)

EXECUTION_ENGINE_VERSION = "phase6a-v1"
VERSION = EXECUTION_ENGINE_VERSION  # back-compat alias for any introspection


def _dump(order_request: OrderRequest) -> dict:
    """Lock 6b-L17: delegate to the shared audit-JSON normalizer.
    Kept as a thin wrapper for back-compat and to make grep-ability of
    audit-writing sites obvious. New audit code should call
    `normalize_for_json` directly."""
    return normalize_for_json(order_request)


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
                        # Phase 6b: composite extensions (lock 6b-L6 — no
                        # new audit event type; reuse ORDER_REJECTED with
                        # extended context). list() so JSON column accepts.
                        "failed_gates": list(risk_result.failed_gates),
                        "per_gate": list(risk_result.context.get("per_gate", [])),
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
        """Idempotent cancel. PLACED → CANCELLED + audit. Terminal states
        (ENTRY_FILLED, CANCELLED) are no-op."""
        order = self._repo.find_paper_order_by_id(int(order_id))
        if order is None:
            raise ValueError(f"unknown order_id={order_id}")
        if order.status in ("ENTRY_FILLED", "CANCELLED"):
            return  # idempotent no-op
        with self._repo.transaction():
            self._repo.update_paper_order_status(
                order_id=order.id,
                new_status="CANCELLED",
                cancelled_at=self._clock.now(),
                cancel_reason="manual_cancel",
            )
            self._repo.write_audit_event(
                event_type=AuditEventType.ORDER_CANCELLED,
                order_id=order.id,
                strategy=order.strategy,
                reason="manual_cancel",
                context={"prior_status": "PLACED"},
                timestamp=self._clock.now(),
            )

    def tick(self, *, as_of: date) -> TickResult:
        """Per-row transactional. Each entry / exit gets its own
        Repository.transaction(); InvariantError → audit + continue
        (6a-L4)."""
        entries = 0
        exits = 0
        errors: list[TickError] = []

        # Phase A: entries
        for order in self._repo.find_orders_for_entry(as_of=as_of):
            try:
                self._materialize_entry(order, fill_date=as_of)
                entries += 1
            except InvariantError as e:
                err = TickError(
                    phase="entry_materialization",
                    order_id=order.id,
                    position_id=None,
                    error=str(e),
                )
                errors.append(err)
                with self._repo.transaction():
                    self._repo.write_audit_event(
                        event_type=AuditEventType.ENGINE_INVARIANT_ERROR,
                        order_id=order.id,
                        strategy=order.strategy,
                        reason="invariant_error",
                        context={
                            "phase": err.phase,
                            "order_id": err.order_id,
                            "error": err.error,
                            "as_of": as_of.isoformat(),
                        },
                        timestamp=self._clock.now(),
                    )

        # Phase B: exits
        for position in self._repo.find_positions_for_exit(as_of=as_of):
            try:
                self._materialize_exit(position, exit_date=as_of)
                exits += 1
            except InvariantError as e:
                err = TickError(
                    phase="exit_materialization",
                    order_id=position.order_id,
                    position_id=position.id,
                    error=str(e),
                )
                errors.append(err)
                with self._repo.transaction():
                    self._repo.write_audit_event(
                        event_type=AuditEventType.ENGINE_INVARIANT_ERROR,
                        order_id=position.order_id,
                        strategy=position.strategy,
                        reason="invariant_error",
                        context={
                            "phase": err.phase,
                            "position_id": err.position_id,
                            "order_id": err.order_id,
                            "error": err.error,
                            "as_of": as_of.isoformat(),
                        },
                        timestamp=self._clock.now(),
                    )

        return TickResult(
            as_of=as_of,
            entries_materialized=entries,
            exits_materialized=exits,
            errors=tuple(errors),
        )

    def _materialize_entry(self, order, *, fill_date: date) -> None:
        fill_time = self._clock.now()
        fill_price = order.event_price
        cash_outflow = fill_price * Decimal(order.quantity)

        with self._repo.transaction():
            position = self._repo.insert_paper_position(
                order_id=order.id, strategy=order.strategy,
                ticker=order.ticker, quantity=order.quantity,
                entry_price=fill_price, entry_date=fill_date,
                horizon_date=order.horizon_date, opened_at=fill_time,
            )
            fill = self._repo.insert_paper_fill(
                order_id=order.id, position_id=position.id,
                side="ENTRY", price=fill_price, quantity=order.quantity,
                filled_at=fill_time, cash_delta=-cash_outflow,
                realized_pnl=None,
            )
            self._repo.update_paper_position_entry_fill(
                position_id=position.id, entry_fill_id=fill.id,
            )
            self._repo.insert_cash_ledger_entry_for_fill(
                timestamp=fill_time, delta=-cash_outflow,
                reason="ENTRY_FILL", fill_id=fill.id,
            )
            self._repo.update_paper_order_status(
                order_id=order.id, new_status="ENTRY_FILLED",
                filled_at=fill_time,
            )
            self._repo.write_audit_event(
                event_type=AuditEventType.ORDER_ENTRY_FILLED,
                order_id=order.id, strategy=order.strategy, reason="",
                context={
                    "position_id": position.id,
                    "fill_price": str(fill_price),
                    "cash_balance_after": str(self._repo.cash_balance()),
                },
                timestamp=fill_time,
            )

    def _materialize_exit(self, position, *, exit_date: date) -> None:
        exit_time = self._clock.now()
        order = self._repo.find_paper_order_by_id(position.order_id)
        if order.horizon_price is None:
            raise InvariantError(
                f"order {order.id} has no horizon_price; "
                "ForwardExecutionEngine cannot exit without it (lock xii)"
            )
        exit_price = order.horizon_price
        cash_inflow = exit_price * Decimal(position.quantity)
        realized_pnl = (
            (exit_price - position.entry_price) * Decimal(position.quantity)
        )

        with self._repo.transaction():
            fill = self._repo.insert_paper_fill(
                order_id=position.order_id, position_id=position.id,
                side="EXIT", price=exit_price, quantity=position.quantity,
                filled_at=exit_time, cash_delta=cash_inflow,
                realized_pnl=realized_pnl,
            )
            self._repo.update_paper_position_exit(
                position_id=position.id, exit_fill_id=fill.id,
                exit_price=exit_price, realized_pnl=realized_pnl,
                closed_at=exit_time,
            )
            self._repo.insert_cash_ledger_entry_for_fill(
                timestamp=exit_time, delta=cash_inflow,
                reason="EXIT_FILL", fill_id=fill.id,
            )
            self._repo.write_audit_event(
                event_type=AuditEventType.POSITION_CLOSED,
                order_id=position.order_id, strategy=position.strategy,
                reason="",
                context={
                    "position_id": position.id,
                    "exit_price": str(exit_price),
                    "realized_pnl": str(realized_pnl),
                    "cash_balance_after": str(self._repo.cash_balance()),
                },
                timestamp=exit_time,
            )
