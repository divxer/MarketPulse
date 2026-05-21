"""Phase 6a single-writer surface (lock iii).

The ONLY module allowed to INSERT/UPDATE paper_* tables. Execution-path
reads also live here (find_by_id, find_by_key, etc.). UI/observability
reads will get their own query_models.py later (deferred to 6f/6g).

This module never imports kill_switch, forward_engine, daily_cycle, or
bid_aggregator (layered dependency rule)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from typing import Literal

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from marketpulse.db.models import (
    PaperAuditEvent,
)
from marketpulse.trading.types import AuditEventType


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
            self.write_audit_event(
                event_type=AuditEventType.TICK_REPROCESSED_COMPLETED,
                order_id=None,
                strategy=None,
                reason="recovered_from_errors",
                context={
                    "tick_date": context["tick_date"],
                    "prior_status": prior_status,
                    "new_status": new_status,
                    "prior_tick_completed_id": prior.id,
                    **context,
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
