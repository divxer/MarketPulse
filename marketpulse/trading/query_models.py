"""Read-side query models for the Phase 6f paper-trading operations UI.

This module is an inspection-plane consumer of paper_* state. It never writes
paper tables and never changes execution semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import Integer, cast, desc, func, select
from sqlalchemy.orm import Session

from marketpulse.config import get_settings
from marketpulse.db.models import (
    PaperAuditEvent,
    PaperCashLedger,
    PaperFill,
    PaperOrder,
    PaperPosition,
)
from marketpulse.trading.calendar import NY
from marketpulse.trading.clock import WallClock

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

@dataclass(frozen=True)
class SectionResult[T]:
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


def section_ok[T](data: T, empty_message: str | None = None) -> SectionResult[T]:
    return SectionResult(status="ok", data=data, empty_message=empty_message)


def section_error[T](error_title: str, degraded_reason: str) -> SectionResult[T]:
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


def _safe_section(error_title: str, loader):
    try:
        return loader()
    except Exception as exc:
        return section_error(error_title, type(exc).__name__)


def _context_int(context: dict, key: str) -> int | None:
    value = context.get(key)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _ticker_from(row: PaperAuditEvent) -> str | None:
    value = (row.context or {}).get("ticker")
    return str(value) if value is not None else None


def _is_daily_loss_or_failed_gate(row: PaperAuditEvent) -> bool:
    gates = (row.context or {}).get("failed_gates")
    return isinstance(gates, list) and bool(gates)


def _position_id(row: PaperAuditEvent) -> int | None:
    return _context_int(row.context or {}, "position_id")


@dataclass(frozen=True)
class ProjectionContext:
    rows: list[PaperAuditEvent]
    recovered_positions: set[int]
    suppressed_price_unavailable_positions: set[int]


def _historical_price_unavailable_before_closes(
    db: Session,
    close_rows: list[PaperAuditEvent],
) -> set[int]:
    if not close_rows:
        return set()
    close_ts_by_position = {
        pid: row.timestamp
        for row in close_rows
        if (pid := _position_id(row)) is not None
    }
    if not close_ts_by_position:
        return set()

    position_id_expr = func.json_extract(PaperAuditEvent.context, "$.position_id")
    pu_rows = db.execute(
        select(position_id_expr, PaperAuditEvent.timestamp)
        .where(PaperAuditEvent.event_type == "PRICE_UNAVAILABLE")
        .where(position_id_expr.in_(sorted(close_ts_by_position)))
        .where(PaperAuditEvent.timestamp < max(close_ts_by_position.values())),
    ).all()
    return {
        int(position_id)
        for position_id, pu_timestamp in pu_rows
        if position_id is not None
        and pu_timestamp
        < close_ts_by_position.get(
            int(position_id),
            datetime.min.replace(tzinfo=UTC),
        )
    }


def _recovered_positions(
    db: Session,
    rows: list[PaperAuditEvent],
) -> set[int]:
    seen_pu: set[int] = set()
    recovered: set[int] = set()
    for row in rows:
        pid = _position_id(row)
        if pid is None:
            continue
        if row.event_type == "PRICE_UNAVAILABLE":
            seen_pu.add(pid)
        elif row.event_type == "POSITION_CLOSED" and pid in seen_pu:
            recovered.add(pid)

    historical_close_rows = [
        row
        for row in rows
        if row.event_type == "POSITION_CLOSED"
        and (pid := _position_id(row)) is not None
        and pid not in recovered
    ]
    recovered.update(_historical_price_unavailable_before_closes(db, historical_close_rows))
    return recovered


def _build_projection_context(
    db: Session,
    rows: list[PaperAuditEvent],
) -> ProjectionContext:
    recovered = _recovered_positions(db, rows)
    return ProjectionContext(
        rows=rows,
        recovered_positions=recovered,
        suppressed_price_unavailable_positions=recovered,
    )


def _severity_for(
    row: PaperAuditEvent,
    recovered_positions: set[int],
) -> Literal["critical", "warning", "recovery", "routine"]:
    if row.event_type == "ENGINE_INVARIANT_ERROR":
        return "critical"
    if row.event_type in {
        "SCHEDULER_GAP_DETECTED",
        "KILL_SWITCH_FLIPPED",
        "KILL_SWITCH_CYCLE_SKIPPED",
        "TICK_REPROCESSED_COMPLETED",
    }:
        return "warning"
    if row.event_type == "PRICE_UNAVAILABLE":
        return "warning"
    if row.event_type == "ORDER_REJECTED" and _is_daily_loss_or_failed_gate(row):
        return "warning"
    if row.event_type == "POSITION_CLOSED" and _position_id(row) in recovered_positions:
        return "recovery"
    return "routine"


def _load_critical_events_section(
    db: Session,
    window: OperationalWindow,
    projection: ProjectionContext,
) -> SectionResult[list[OperationalEvent]]:
    del db, window
    events: list[OperationalEvent] = []
    for row in projection.rows:
        pid = _position_id(row)
        severity = _severity_for(row, projection.recovered_positions)
        if (
            row.event_type == "PRICE_UNAVAILABLE"
            and pid in projection.suppressed_price_unavailable_positions
        ):
            continue
        if severity not in {"critical", "warning", "recovery"}:
            continue
        title = row.event_type.replace("_", " ").title()
        if row.event_type == "PRICE_UNAVAILABLE":
            attempt = (row.context or {}).get("attempt_count", "?")
            title = f"Price unavailable ({attempt})"
        if row.event_type == "POSITION_CLOSED" and severity == "recovery":
            title = "Position recovered"
        events.append(
            OperationalEvent(
                audit_id=row.id,
                timestamp=row.timestamp,
                event_type=row.event_type,
                severity=severity,
                title=title,
                detail=row.reason or "",
                ticker=_ticker_from(row),
                strategy=row.strategy,
            ),
        )
    return section_ok(events, "No operational events in current cycle")


def _latest_pu_attempts(
    db: Session,
    *,
    position_ids: set[int],
    rows: list[PaperAuditEvent],
) -> dict[int, int]:
    attempts: dict[int, int] = {}
    if position_ids:
        position_id_expr = func.json_extract(PaperAuditEvent.context, "$.position_id")
        attempt_expr = cast(
            func.json_extract(PaperAuditEvent.context, "$.attempt_count"),
            Integer,
        )
        historical = db.execute(
            select(
                position_id_expr.label("position_id"),
                func.max(attempt_expr).label("max_attempt"),
            )
            .where(PaperAuditEvent.event_type == "PRICE_UNAVAILABLE")
            .where(position_id_expr.in_(sorted(position_ids)))
            .group_by(position_id_expr),
        ).all()
        attempts.update(
            {
                int(row.position_id): int(row.max_attempt)
                for row in historical
                if row.position_id is not None and row.max_attempt is not None
            },
        )
    for row in rows:
        if row.event_type != "PRICE_UNAVAILABLE":
            continue
        pid = _position_id(row)
        attempt = _context_int(row.context or {}, "attempt_count")
        if pid is not None and attempt is not None:
            attempts[pid] = max(attempts.get(pid, 0), attempt)
    return attempts


def _exit_status(
    position: PaperPosition,
    *,
    today: object,
    attempts: dict[int, int],
) -> tuple[OperationalExitStatus, str]:
    if position.status == "CLOSED":
        return "CLOSED", "Closed"
    attempt = attempts.get(position.id, 0)
    if attempt >= 3:
        return "STUCK_3_PLUS", "Stuck 3+"
    if attempt == 2:
        return "PRICE_UNAVAILABLE_2", "Price unavailable 2/3"
    if attempt == 1:
        return "PRICE_UNAVAILABLE_1", "Price unavailable 1/3"
    if position.horizon_date <= today:
        return "EXIT_PENDING", "Exit pending"
    return "ON_SCHEDULE", "On schedule"


def _load_positions_section(
    db: Session,
    window: OperationalWindow,
    today: object,
    rows: list[PaperAuditEvent],
) -> SectionResult[list[PositionRow]]:
    del window
    recovered_ids = sorted(_recovered_positions(db, rows))
    open_positions = list(
        db.execute(
            select(PaperPosition)
            .where(PaperPosition.status == "OPEN")
            .order_by(PaperPosition.id),
        ).scalars().all(),
    )
    recovered_positions: list[PaperPosition] = []
    if recovered_ids:
        recovered_positions = list(
            db.execute(
                select(PaperPosition)
                .where(PaperPosition.id.in_(recovered_ids))
                .where(PaperPosition.status == "CLOSED")
                .order_by(PaperPosition.id),
            ).scalars().all(),
        )

    positions = [*open_positions, *recovered_positions]
    attempts = _latest_pu_attempts(
        db,
        position_ids={position.id for position in positions},
        rows=rows,
    )
    out: list[PositionRow] = []
    seen: set[int] = set()
    for position in positions:
        if position.id in seen:
            continue
        seen.add(position.id)
        exit_status, label = _exit_status(position, today=today, attempts=attempts)
        out.append(
            PositionRow(
                position_id=position.id,
                order_id=position.order_id,
                ticker=position.ticker,
                strategy=position.strategy,
                quantity=position.quantity,
                entry_price=position.entry_price,
                entry_date=position.entry_date,
                horizon_date=position.horizon_date,
                canonical_status=position.status,
                operational_exit_status=exit_status,
                exit_health_label=label,
                realized_pnl=position.realized_pnl,
            ),
        )
    return section_ok(out, "No open paper positions")


def _load_order_lifecycles_section(
    db: Session,
    window: OperationalWindow,
    rows: list[PaperAuditEvent],
) -> SectionResult[list[OrderLifecycleRow]]:
    del window
    order_ids = sorted({row.order_id for row in rows if row.order_id is not None})
    if not order_ids:
        return section_ok([], "No order lifecycle activity in current cycle")

    orders = list(
        db.execute(
            select(PaperOrder)
            .where(PaperOrder.id.in_(order_ids))
            .order_by(PaperOrder.id),
        ).scalars().all(),
    )
    positions = list(
        db.execute(
            select(PaperPosition).where(PaperPosition.order_id.in_(order_ids)),
        ).scalars().all(),
    )
    fills = list(
        db.execute(
            select(PaperFill).where(PaperFill.order_id.in_(order_ids)),
        ).scalars().all(),
    )
    positions_by_order = {position.order_id: position for position in positions}
    fills_by_order_side = {(fill.order_id, fill.side): fill for fill in fills}
    latest_reason_by_order: dict[int, str] = {}
    for row in rows:
        if row.order_id is not None:
            latest_reason_by_order[row.order_id] = row.reason or row.event_type

    out: list[OrderLifecycleRow] = []
    for order in orders:
        position = positions_by_order.get(order.id)
        entry = fills_by_order_side.get((order.id, "ENTRY"))
        exit_fill = fills_by_order_side.get((order.id, "EXIT"))
        out.append(
            OrderLifecycleRow(
                order_id=order.id,
                ticker=order.ticker,
                strategy=order.strategy,
                quantity=order.quantity,
                order_status=order.status,
                placed_at=order.placed_at,
                entry_price=entry.price if entry is not None else None,
                entry_time=entry.filled_at if entry is not None else order.filled_at,
                exit_price=exit_fill.price if exit_fill is not None else None,
                exit_time=(
                    exit_fill.filled_at
                    if exit_fill is not None
                    else (position.closed_at if position else None)
                ),
                realized_pnl=(
                    exit_fill.realized_pnl
                    if exit_fill is not None
                    else (position.realized_pnl if position else None)
                ),
                latest_audit_reason=latest_reason_by_order.get(order.id),
            ),
        )
    return section_ok(out, "No order lifecycle activity in current cycle")


def _load_audit_timeline_section(
    db: Session,
    window: OperationalWindow,
    projection: ProjectionContext,
) -> SectionResult[AuditTimeline]:
    del db, window
    timeline_rows: list[AuditTimelineRow] = []
    hidden_count = 0
    for row in projection.rows:
        if row.event_type == "TICK_COMPLETED":
            continue
        severity = _severity_for(row, projection.recovered_positions)
        routine = severity == "routine"
        if routine:
            hidden_count += 1
        timeline_rows.append(
            AuditTimelineRow(
                audit_id=row.id,
                timestamp=row.timestamp,
                event_type=row.event_type,
                reason=row.reason or "",
                order_id=row.order_id,
                ticker=_ticker_from(row),
                strategy=row.strategy,
                severity=severity,
                routine=routine,
            ),
        )
    return section_ok(
        AuditTimeline(rows=timeline_rows, routine_hidden_count=hidden_count),
        "No operational events in current cycle",
    )


def load_paper_trading_dashboard(
    db: Session,
    *,
    now: datetime | None = None,
) -> PaperTradingDashboard:
    generated_at = now or WallClock().now()
    generated_at_label = f"Generated at {generated_at.astimezone(NY):%H:%M NY}"
    window = _load_operational_window(db)
    rows = _window_rows(db, window)
    projection = _build_projection_context(db, rows)
    health = _load_health_summary(db, generated_at=generated_at, rows=rows)
    today = generated_at.astimezone(NY).date()
    critical_events = _safe_section(
        "Unable to load Critical Events",
        lambda: _load_critical_events_section(db, window, projection),
    )
    positions = _safe_section(
        "Unable to load Positions",
        lambda: _load_positions_section(db, window, today, rows),
    )
    order_lifecycles = _safe_section(
        "Unable to load Orders & Fills",
        lambda: _load_order_lifecycles_section(db, window, rows),
    )
    audit_timeline = _safe_section(
        "Unable to load Audit Timeline",
        lambda: _load_audit_timeline_section(db, window, projection),
    )
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
