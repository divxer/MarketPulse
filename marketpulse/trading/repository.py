"""Phase 6a single-writer surface (lock iii).

The ONLY module allowed to INSERT/UPDATE paper_* tables. Execution-path
reads also live here (find_by_id, find_by_key, etc.). UI/observability
reads will get their own query_models.py later (deferred to 6f/6g).

This module never imports kill_switch, forward_engine, daily_cycle, or
bid_aggregator (layered dependency rule)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from marketpulse.db.models import (
    PaperAuditEvent,
    PaperCashLedger,
    PaperFill,
    PaperOrder,
    PaperPosition,
)
from marketpulse.trading.types import AuditEventType, InvariantError, OrderRequest

# Allowed status transitions (6a-L6)
_ALLOWED_ORDER_TRANSITIONS = {
    ("PLACED", "ENTRY_FILLED"),
    ("PLACED", "CANCELLED"),
}
_ALLOWED_POSITION_TRANSITIONS = {
    ("OPEN", "CLOSED"),
}


class Repository:
    def __init__(self, *, session: Session) -> None:
        self._session = session

    @contextmanager
    def transaction(self):
        """Wraps a unit of work. Commits on success, rolls back on exception."""
        try:
            yield self._session
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    # === Audit writers (lock v + xiii append-only) ===

    def write_audit_event(
        self,
        *,
        event_type: AuditEventType,
        order_id: int | None,
        strategy: str | None,
        reason: str,
        context: dict,
        timestamp: datetime,
    ) -> PaperAuditEvent:
        row = PaperAuditEvent(
            timestamp=timestamp,
            event_type=event_type.value,
            order_id=order_id,
            strategy=strategy,
            reason=reason,
            context=context,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def write_duplicate_audit_once(
        self,
        *,
        idempotency_key: str,
        order_id: int,
        strategy: str,
        tick_date: date,
        context: dict,
        timestamp: datetime,
    ) -> None:
        """6a-L5: at most one ORDER_PLACED_DUPLICATE per (idempotency_key,
        tick_date)."""
        existing = self._session.execute(
            select(PaperAuditEvent).where(
                PaperAuditEvent.event_type
                == AuditEventType.ORDER_PLACED_DUPLICATE.value,
                PaperAuditEvent.context["idempotency_key"].as_string()
                == idempotency_key,
                PaperAuditEvent.context["tick_date"].as_string()
                == tick_date.isoformat(),
            )
        ).scalars().first()
        if existing is not None:
            return
        ctx = {
            **context,
            "idempotency_key": idempotency_key,
            "tick_date": tick_date.isoformat(),
        }
        self.write_audit_event(
            event_type=AuditEventType.ORDER_PLACED_DUPLICATE,
            order_id=order_id,
            strategy=strategy,
            reason="idempotent_replay",
            context=ctx,
            timestamp=timestamp,
        )

    def write_gap_audit_once(
        self,
        *,
        last_tick: date,
        resume_date: date,
        missed_business_days: int,
        timestamp: datetime,
    ) -> None:
        """Dedup per (last_tick, resume_date)."""
        existing = self._session.execute(
            select(PaperAuditEvent).where(
                PaperAuditEvent.event_type
                == AuditEventType.SCHEDULER_GAP_DETECTED.value,
                PaperAuditEvent.context["last_processed_tick_date"].as_string()
                == last_tick.isoformat(),
                PaperAuditEvent.context["resume_date"].as_string()
                == resume_date.isoformat(),
            )
        ).scalars().first()
        if existing is not None:
            return
        self.write_audit_event(
            event_type=AuditEventType.SCHEDULER_GAP_DETECTED,
            order_id=None,
            strategy=None,
            reason="forward_only_skip",
            context={
                "last_processed_tick_date": last_tick.isoformat(),
                "resume_date": resume_date.isoformat(),
                "missed_business_days": missed_business_days,
                "mode": "forward_only_skip",
            },
            timestamp=timestamp,
        )

    def write_tick_completed_once(
        self,
        *,
        tick_date: date,
        context: dict,
        timestamp: datetime,
    ) -> None:
        """6a-L5 / 6a-L8 decision table:
            no prior row → append TICK_COMPLETED
            prior=completed → no-op (terminal)
            prior=completed_with_errors + new=completed_with_errors → no-op
            prior=completed_with_errors + new=completed → append TICK_REPROCESSED_COMPLETED
        """
        new_status = context["status"]
        prior = self._session.execute(
            select(PaperAuditEvent).where(
                PaperAuditEvent.event_type == AuditEventType.TICK_COMPLETED.value,
                PaperAuditEvent.context["tick_date"].as_string()
                == tick_date.isoformat(),
            ).order_by(PaperAuditEvent.id)
        ).scalars().first()

        if prior is None:
            self.write_audit_event(
                event_type=AuditEventType.TICK_COMPLETED,
                order_id=None,
                strategy=None,
                reason="",
                context=context,
                timestamp=timestamp,
            )
            return

        prior_status = prior.context.get("status")
        if prior_status == "completed":
            return  # terminal
        if (
            prior_status == "completed_with_errors"
            and new_status == "completed_with_errors"
        ):
            return  # same state
        if prior_status == "completed_with_errors" and new_status == "completed":
            # Spread caller context FIRST so the explicit recovery-marker
            # keys below always win — caller's context cannot accidentally
            # shadow tick_date / prior_status / new_status /
            # prior_tick_completed_id even if a future caller adds such keys.
            self.write_audit_event(
                event_type=AuditEventType.TICK_REPROCESSED_COMPLETED,
                order_id=None,
                strategy=None,
                reason="recovered_from_errors",
                context={
                    **context,
                    "tick_date": context["tick_date"],
                    "prior_status": prior_status,
                    "new_status": new_status,
                    "prior_tick_completed_id": prior.id,
                },
                timestamp=timestamp,
            )
            return
        # Other combinations: no-op (defensive).

    # === last_processed_tick_date (6a-L5 + 6a-L8) ===

    def last_processed_tick_date(self) -> date | None:
        """Reads max tick_date from TICK_COMPLETED OR KILL_SWITCH_CYCLE_SKIPPED
        rows. Does NOT include TICK_REPROCESSED_COMPLETED."""
        row = self._session.execute(
            select(PaperAuditEvent).where(
                PaperAuditEvent.event_type.in_([
                    AuditEventType.TICK_COMPLETED.value,
                    AuditEventType.KILL_SWITCH_CYCLE_SKIPPED.value,
                ])
            ).order_by(desc(PaperAuditEvent.id))
        ).scalars().first()
        if row is None:
            return None
        return date.fromisoformat(row.context["tick_date"])

    def latest_tick_status(
        self, tick_date: date,
    ) -> Literal[
        "completed", "completed_with_errors", "reprocessed_completed",
        "kill_switch_skipped",
    ] | None:
        """For 6g/UI badge rendering."""
        row = self._session.execute(
            select(PaperAuditEvent).where(
                PaperAuditEvent.event_type.in_([
                    AuditEventType.TICK_COMPLETED.value,
                    AuditEventType.TICK_REPROCESSED_COMPLETED.value,
                    AuditEventType.KILL_SWITCH_CYCLE_SKIPPED.value,
                ]),
                PaperAuditEvent.context["tick_date"].as_string()
                == tick_date.isoformat(),
            ).order_by(desc(PaperAuditEvent.id))
        ).scalars().first()
        if row is None:
            return None
        if row.event_type == AuditEventType.TICK_REPROCESSED_COMPLETED.value:
            return "reprocessed_completed"
        if row.event_type == AuditEventType.KILL_SWITCH_CYCLE_SKIPPED.value:
            return "kill_switch_skipped"
        # TICK_COMPLETED
        s = row.context.get("status")
        return "completed" if s == "completed" else "completed_with_errors"

    # === paper_order CRUD + status transitions (6a-L6) ===

    def insert_paper_order(
        self,
        *,
        order_request: OrderRequest,
        idempotency_key: str,
        placed_at: datetime,
    ) -> PaperOrder:
        row = PaperOrder(
            idempotency_key=idempotency_key,
            allocation_run_id=order_request.allocation_run_id,
            strategy=order_request.strategy,
            ticker=order_request.ticker,
            quantity=order_request.quantity,
            event_time=order_request.event_time,
            allocation_date=order_request.allocation_date,
            horizon_date=order_request.horizon_date,
            placed_at=placed_at,
            event_price=order_request.event_price,
            horizon_price=order_request.horizon_price,
            status="PLACED",
            strategy_version=order_request.strategy_version,
            allocator_version=order_request.allocator_version,
            execution_engine_version=order_request.execution_engine_version,
            weight=order_request.weight,
            raw_bid_weight=order_request.raw_bid_weight,
            pool_corr=order_request.pool_corr,
            contribution_multiplier=order_request.contribution_multiplier,
            adjusted_bid_weight=order_request.adjusted_bid_weight,
            effective_corr_window=order_request.effective_corr_window,
            rewarded_for_negative_corr=order_request.rewarded_for_negative_corr,
            would_change_rank=order_request.would_change_rank,
            size_clamped_by_override=order_request.size_clamped_by_override,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def find_paper_order_by_id(self, order_id: int) -> PaperOrder | None:
        return self._session.get(PaperOrder, order_id)

    def find_paper_order_by_idempotency_key(self, key: str) -> PaperOrder | None:
        return self._session.execute(
            select(PaperOrder).where(PaperOrder.idempotency_key == key)
        ).scalars().first()

    def update_paper_order_status(
        self,
        *,
        order_id: int,
        new_status: str,
        filled_at: datetime | None = None,
        cancelled_at: datetime | None = None,
        cancel_reason: str | None = None,
    ) -> PaperOrder:
        order = self.find_paper_order_by_id(order_id)
        if order is None:
            raise InvariantError(f"unknown order_id={order_id}")
        if (order.status, new_status) not in _ALLOWED_ORDER_TRANSITIONS:
            raise InvariantError(
                f"illegal status transition {order.status!r} → "
                f"{new_status!r} on order {order_id}"
            )
        order.status = new_status
        if filled_at is not None:
            order.filled_at = filled_at
        if cancelled_at is not None:
            order.cancelled_at = cancelled_at
        if cancel_reason is not None:
            order.cancel_reason = cancel_reason
        self._session.flush()
        return order

    # === paper_position / paper_fill / paper_cash_ledger writers ===

    def insert_paper_position(
        self,
        *,
        order_id: int,
        strategy: str,
        ticker: str,
        quantity: int,
        entry_price: Decimal,
        entry_date: date,
        horizon_date: date,
        opened_at: datetime,
    ) -> PaperPosition:
        row = PaperPosition(
            order_id=order_id,
            entry_fill_id=None,
            exit_fill_id=None,
            strategy=strategy,
            ticker=ticker,
            quantity=quantity,
            entry_price=entry_price,
            entry_date=entry_date,
            horizon_date=horizon_date,
            status="OPEN",
            opened_at=opened_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def update_paper_position_entry_fill(
        self, *, position_id: int, entry_fill_id: int,
    ) -> None:
        pos = self._session.get(PaperPosition, position_id)
        if pos is None:
            raise InvariantError(f"unknown position_id={position_id}")
        pos.entry_fill_id = entry_fill_id
        self._session.flush()

    def update_paper_position_exit(
        self,
        *,
        position_id: int,
        exit_fill_id: int,
        exit_price: Decimal,
        realized_pnl: Decimal,
        closed_at: datetime,
    ) -> None:
        pos = self._session.get(PaperPosition, position_id)
        if pos is None:
            raise InvariantError(f"unknown position_id={position_id}")
        if (pos.status, "CLOSED") not in _ALLOWED_POSITION_TRANSITIONS:
            raise InvariantError(
                f"illegal position transition {pos.status!r} → CLOSED"
            )
        pos.exit_fill_id = exit_fill_id
        pos.exit_price = exit_price
        pos.realized_pnl = realized_pnl
        pos.closed_at = closed_at
        pos.status = "CLOSED"
        self._session.flush()

    def insert_paper_fill(
        self,
        *,
        order_id: int,
        position_id: int,
        side: str,
        price: Decimal,
        quantity: int,
        filled_at: datetime,
        cash_delta: Decimal,
        realized_pnl: Decimal | None,
    ) -> PaperFill:
        row = PaperFill(
            order_id=order_id,
            position_id=position_id,
            side=side,
            price=price,
            quantity=quantity,
            filled_at=filled_at,
            cash_delta=cash_delta,
            realized_pnl=realized_pnl,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def insert_cash_ledger_entry_for_fill(
        self,
        *,
        timestamp: datetime,
        delta: Decimal,
        reason: str,
        fill_id: int | None,
    ) -> PaperCashLedger:
        """Repository computes balance_after inside the transaction (round-3
        lock). Engine never juggles balance arithmetic."""
        latest = self._session.execute(
            select(PaperCashLedger).order_by(desc(PaperCashLedger.id))
        ).scalars().first()
        prior_balance = (
            latest.balance_after if latest is not None else Decimal("0")
        )
        new_balance = prior_balance + delta
        row = PaperCashLedger(
            timestamp=timestamp,
            delta=delta,
            reason=reason,
            fill_id=fill_id,
            balance_after=new_balance,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def cash_balance(self) -> Decimal:
        """Lock xxi: latest balance_after by monotonic id."""
        latest = self._session.execute(
            select(PaperCashLedger).order_by(desc(PaperCashLedger.id))
        ).scalars().first()
        return latest.balance_after if latest is not None else Decimal("0")

    def ensure_initial_deposit(
        self, *, amount: Decimal, timestamp: datetime,
    ) -> None:
        """Idempotent. Called at app startup. Uses self.transaction() — no
        naked commit (round-3 fix)."""
        with self.transaction():
            count = self._session.execute(
                select(func.count(PaperCashLedger.id))
            ).scalar()
            if count == 0:
                self._session.add(PaperCashLedger(
                    timestamp=timestamp,
                    delta=amount,
                    reason="INITIAL_DEPOSIT",
                    fill_id=None,
                    balance_after=amount,
                ))

    # === Engine-facing queries ===

    def find_orders_for_entry(self, *, as_of: date) -> list[PaperOrder]:
        """tick() Phase A query: PLACED orders with allocation_date <= as_of."""
        return list(self._session.execute(
            select(PaperOrder)
            .where(PaperOrder.status == "PLACED")
            .where(PaperOrder.allocation_date <= as_of)
            .order_by(PaperOrder.id)
        ).scalars().all())

    def find_positions_for_exit(self, *, as_of: date) -> list[PaperPosition]:
        """tick() Phase B query: OPEN positions with horizon_date <= as_of."""
        return list(self._session.execute(
            select(PaperPosition)
            .where(PaperPosition.status == "OPEN")
            .where(PaperPosition.horizon_date <= as_of)
            .order_by(PaperPosition.id)
        ).scalars().all())

    def open_positions_snapshot(self) -> list[PaperPosition]:
        return list(self._session.execute(
            select(PaperPosition).where(PaperPosition.status == "OPEN")
            .order_by(PaperPosition.id)
        ).scalars().all())

    def count_positions_status(self, status: str) -> int:
        return self._session.execute(
            select(func.count(PaperPosition.id))
            .where(PaperPosition.status == status)
        ).scalar() or 0

    # === Kill switch DB-state read (6a-2.4 rule #3) ===

    def latest_kill_switch_state(self) -> bool:
        """Returns True iff the latest KILL_SWITCH_FLIPPED audit row's
        context.to_state is True. Returns False if never flipped.

        Rule #3: KillSwitchState reads via this helper instead of touching
        the session directly."""
        row = self._session.execute(
            select(PaperAuditEvent).where(
                PaperAuditEvent.event_type
                == AuditEventType.KILL_SWITCH_FLIPPED.value,
            ).order_by(desc(PaperAuditEvent.id))
        ).scalars().first()
        if row is None:
            return False
        return bool(row.context.get("to_state", False))
