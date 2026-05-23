"""Read-side query models for the Phase 6f paper-trading operations UI.

This module is an inspection-plane consumer of paper_* state. It never writes
paper tables and never changes execution semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Generic, Literal, TypeVar

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from marketpulse.config import get_settings
from marketpulse.db.models import (
    PaperAuditEvent,
    PaperCashLedger,
    PaperFill,
    PaperPosition,
)
from marketpulse.trading.calendar import NY

SystemStatus = Literal["Healthy", "Attention", "Degraded"]
SectionStatus = Literal["ok", "error"]
OperationalExitStatus = Literal[
    "CLOSED",
    "ON_SCHEDULE",
    "EXIT_PENDING",
    "PRICE_UNAVAILABLE_1",
    "PRICE_UNAVAILABLE_2",
    "STUCK_3_PLUS",
]

T = TypeVar("T")


@dataclass(frozen=True)
class SectionResult(Generic[T]):
    status: SectionStatus
    data: T | None
    empty_message: str | None = None
    error_title: str | None = None
    degraded_reason: str | None = None

    def __post_init__(self) -> None:
        if self.status == "ok" and self.data is None:
            raise ValueError("ok SectionResult requires non-None data")
        if self.status == "error" and self.data is not None:
            raise ValueError("error SectionResult requires data=None")


def section_ok(data: T, empty_message: str | None = None) -> SectionResult[T]:
    return SectionResult(status="ok", data=data, empty_message=empty_message)


def section_error(error_title: str, degraded_reason: str) -> SectionResult[T]:
    return SectionResult(
        status="error",
        data=None,
        error_title=error_title,
        degraded_reason=degraded_reason,
    )


@dataclass(frozen=True)
class OperationalWindow:
    started_at: datetime | None
    source_event_type: str | None
    label: str


@dataclass(frozen=True)
class HealthSummary:
    cash_balance: Decimal
    realized_pnl_today: Decimal
    open_positions_count: int
    latest_tick_status: str | None
    kill_switch_state: str
    kill_switch_reason: str | None


@dataclass(frozen=True)
class OperationalEvent:
    audit_id: int
    timestamp: datetime
    event_type: str
    severity: Literal["critical", "warning", "recovery"]
    title: str
    detail: str
    ticker: str | None = None
    strategy: str | None = None


@dataclass(frozen=True)
class PositionRow:
    position_id: int
    order_id: int
    ticker: str
    strategy: str
    quantity: int
    entry_price: Decimal
    entry_date: object
    horizon_date: object
    canonical_status: str
    operational_exit_status: OperationalExitStatus
    exit_health_label: str
    realized_pnl: Decimal | None


@dataclass(frozen=True)
class OrderLifecycleRow:
    order_id: int
    ticker: str
    strategy: str
    quantity: int
    order_status: str
    placed_at: datetime
    entry_price: Decimal | None
    entry_time: datetime | None
    exit_price: Decimal | None
    exit_time: datetime | None
    realized_pnl: Decimal | None
    latest_audit_reason: str | None


@dataclass(frozen=True)
class AuditTimelineRow:
    audit_id: int
    timestamp: datetime
    event_type: str
    reason: str
    order_id: int | None
    ticker: str | None
    strategy: str | None
    severity: Literal["critical", "warning", "recovery", "routine"]
    routine: bool


@dataclass(frozen=True)
class AuditTimeline:
    rows: list[AuditTimelineRow]
    routine_hidden_count: int
    show_routine: bool = False


@dataclass(frozen=True)
class PaperTradingDashboard:
    generated_at: datetime
    generated_at_label: str
    current_operational_window: OperationalWindow
    system_status: SystemStatus
    health: HealthSummary
    critical_events: SectionResult[list[OperationalEvent]]
    positions: SectionResult[list[PositionRow]]
    order_lifecycles: SectionResult[list[OrderLifecycleRow]]
    audit_timeline: SectionResult[AuditTimeline]


_COW_BOUNDARY_EVENTS = (
    "TICK_COMPLETED",
    "KILL_SWITCH_CYCLE_SKIPPED",
    "TICK_REPROCESSED_COMPLETED",
)


def _format_ny_label(started_at: datetime | None) -> str:
    if started_at is None:
        return "No paper tick has completed yet"
    return f"Operational Window · Started {started_at.astimezone(NY):%Y-%m-%d %H:%M NY}"


def _load_operational_window(db: Session) -> OperationalWindow:
    row = db.execute(
        select(PaperAuditEvent)
        .where(PaperAuditEvent.event_type.in_(_COW_BOUNDARY_EVENTS))
        .order_by(desc(PaperAuditEvent.timestamp), desc(PaperAuditEvent.id))
        .limit(1),
    ).scalars().first()
    if row is None:
        return OperationalWindow(None, None, "No paper tick has completed yet")
    return OperationalWindow(
        row.timestamp,
        row.event_type,
        _format_ny_label(row.timestamp),
    )


def _window_rows(db: Session, window: OperationalWindow) -> list[PaperAuditEvent]:
    if window.started_at is None:
        return []
    return list(
        db.execute(
            select(PaperAuditEvent)
            .where(PaperAuditEvent.timestamp >= window.started_at)
            .order_by(PaperAuditEvent.timestamp, PaperAuditEvent.id),
        ).scalars().all(),
    )


def _latest_tick_status_from_window(rows: list[PaperAuditEvent]) -> str | None:
    for row in reversed(rows):
        if row.event_type == "TICK_REPROCESSED_COMPLETED":
            return "reprocessed_completed"
        if row.event_type == "KILL_SWITCH_CYCLE_SKIPPED":
            return "kill_switch_skipped"
        if row.event_type == "TICK_COMPLETED":
            return (
                "completed"
                if (row.context or {}).get("status") == "completed"
                else "completed_with_errors"
            )
    return None


def _load_kill_switch(db: Session) -> tuple[str, str | None]:
    settings = get_settings()
    if settings.paper_kill_switch:
        return "ON", "env override"
    row = db.execute(
        select(PaperAuditEvent)
        .where(PaperAuditEvent.event_type == "KILL_SWITCH_FLIPPED")
        .order_by(desc(PaperAuditEvent.timestamp), desc(PaperAuditEvent.id))
        .limit(1),
    ).scalars().first()
    if row is None:
        return "OFF", None
    return (
        "ON" if bool((row.context or {}).get("to_state")) else "OFF",
        row.reason or "unknown",
    )


def _load_health_summary(
    db: Session,
    *,
    generated_at: datetime,
    rows: list[PaperAuditEvent],
) -> HealthSummary:
    latest_cash = db.execute(
        select(PaperCashLedger.balance_after).order_by(desc(PaperCashLedger.id)).limit(1),
    ).scalar()
    open_count = db.execute(
        select(func.count(PaperPosition.id)).where(PaperPosition.status == "OPEN"),
    ).scalar() or 0
    ny_date = generated_at.astimezone(NY).date()
    ny_start = datetime.combine(ny_date, datetime.min.time(), tzinfo=NY).astimezone(UTC)
    ny_end = datetime.combine(
        ny_date + timedelta(days=1),
        datetime.min.time(),
        tzinfo=NY,
    ).astimezone(UTC)
    realized = db.execute(
        select(func.coalesce(func.sum(PaperFill.realized_pnl), Decimal("0")))
        .where(PaperFill.side == "EXIT")
        .where(PaperFill.filled_at >= ny_start)
        .where(PaperFill.filled_at < ny_end),
    ).scalar() or Decimal("0")
    kill_state, kill_reason = _load_kill_switch(db)
    return HealthSummary(
        cash_balance=Decimal(latest_cash or 0),
        realized_pnl_today=Decimal(realized or 0),
        open_positions_count=int(open_count),
        latest_tick_status=_latest_tick_status_from_window(rows),
        kill_switch_state=kill_state,
        kill_switch_reason=kill_reason,
    )


def _has_attention_events(events: SectionResult[list[OperationalEvent]]) -> bool:
    if events.status == "error" or events.data is None:
        return False
    return any(event.severity in {"critical", "warning"} for event in events.data)


def _compute_system_status(
    *,
    health: HealthSummary,
    critical_events: SectionResult[list[OperationalEvent]],
    positions: SectionResult[list[PositionRow]],
    order_lifecycles: SectionResult[list[OrderLifecycleRow]],
    audit_timeline: SectionResult[AuditTimeline],
) -> SystemStatus:
    sections = (critical_events, positions, order_lifecycles, audit_timeline)
    if any(section.status == "error" for section in sections):
        return "Degraded"
    if health.kill_switch_state == "ON":
        return "Attention"
    if health.latest_tick_status in {
        "completed_with_errors",
        "kill_switch_skipped",
        "reprocessed_completed",
    }:
        return "Attention"
    if _has_attention_events(critical_events):
        return "Attention"
    return "Healthy"


def _load_critical_events_section(
    db: Session,
    window: OperationalWindow,
    rows: list[PaperAuditEvent],
) -> SectionResult[list[OperationalEvent]]:
    del db, window, rows
    return section_ok([], "No operational events in current cycle")


def _load_positions_section(
    db: Session,
    window: OperationalWindow,
    today: object,
    rows: list[PaperAuditEvent],
) -> SectionResult[list[PositionRow]]:
    del db, window, today, rows
    return section_ok([], "No open paper positions")


def _load_order_lifecycles_section(
    db: Session,
    window: OperationalWindow,
    rows: list[PaperAuditEvent],
) -> SectionResult[list[OrderLifecycleRow]]:
    del db, window, rows
    return section_ok([], "No order lifecycle activity in current cycle")


def _load_audit_timeline_section(
    db: Session,
    window: OperationalWindow,
    rows: list[PaperAuditEvent],
) -> SectionResult[AuditTimeline]:
    del db, window
    timeline_rows = [
        AuditTimelineRow(
            audit_id=row.id,
            timestamp=row.timestamp,
            event_type=row.event_type,
            reason=row.reason or "",
            order_id=row.order_id,
            ticker=(row.context or {}).get("ticker"),
            strategy=row.strategy,
            severity="warning" if row.event_type != "TICK_COMPLETED" else "routine",
            routine=row.event_type == "TICK_COMPLETED",
        )
        for row in rows
    ]
    return section_ok(
        AuditTimeline(rows=timeline_rows, routine_hidden_count=0),
        "No operational events in current cycle",
    )


def load_paper_trading_dashboard(
    db: Session,
    *,
    now: datetime | None = None,
) -> PaperTradingDashboard:
    generated_at = now or datetime.now(UTC)
    generated_at_label = f"Generated at {generated_at.astimezone(NY):%H:%M NY}"
    window = _load_operational_window(db)
    rows = _window_rows(db, window)
    health = _load_health_summary(db, generated_at=generated_at, rows=rows)
    today = generated_at.astimezone(NY).date()
    critical_events = _load_critical_events_section(db, window, rows)
    positions = _load_positions_section(db, window, today, rows)
    order_lifecycles = _load_order_lifecycles_section(db, window, rows)
    audit_timeline = _load_audit_timeline_section(db, window, rows)
    system_status = _compute_system_status(
        health=health,
        critical_events=critical_events,
        positions=positions,
        order_lifecycles=order_lifecycles,
        audit_timeline=audit_timeline,
    )
    return PaperTradingDashboard(
        generated_at=generated_at,
        generated_at_label=generated_at_label,
        current_operational_window=window,
        system_status=system_status,
        health=health,
        critical_events=critical_events,
        positions=positions,
        order_lifecycles=order_lifecycles,
        audit_timeline=audit_timeline,
    )
