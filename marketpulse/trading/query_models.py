"""Read-side query models for the Phase 6f paper-trading operations UI.

This module is an inspection-plane consumer of paper_* state. It never writes
paper tables and never changes execution semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Generic, Literal, TypeVar

from sqlalchemy.orm import Session

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


def load_paper_trading_dashboard(
    db: Session,
    *,
    now: datetime | None = None,
) -> PaperTradingDashboard:
    del db
    generated_at = now or datetime.now(UTC)
    generated_at_label = f"Generated at {generated_at.astimezone(NY):%H:%M NY}"
    window = OperationalWindow(
        started_at=None,
        source_event_type=None,
        label="No paper tick has completed yet",
    )
    health = HealthSummary(
        cash_balance=Decimal("0"),
        realized_pnl_today=Decimal("0"),
        open_positions_count=0,
        latest_tick_status=None,
        kill_switch_state="OFF",
        kill_switch_reason=None,
    )
    critical_events = section_ok([], "No operational events in current cycle")
    positions = section_ok([], "No open paper positions")
    order_lifecycles = section_ok([], "No order lifecycle activity in current cycle")
    audit_timeline = section_ok(
        AuditTimeline(rows=[], routine_hidden_count=0),
        "No operational events in current cycle",
    )
    return PaperTradingDashboard(
        generated_at=generated_at,
        generated_at_label=generated_at_label,
        current_operational_window=window,
        system_status="Healthy",
        health=health,
        critical_events=critical_events,
        positions=positions,
        order_lifecycles=order_lifecycles,
        audit_timeline=audit_timeline,
    )
