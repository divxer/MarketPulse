# Phase 6f Paper Trading Operations UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `/lab/paper-trading`, a read-only operations dashboard that lets an authenticated operator inspect current paper-trading health, critical events, positions, orders/fills, and audit signals without querying the database or raw logs.

**Architecture:** Add a dedicated read-side query model in `marketpulse/trading/query_models.py`; the FastAPI route calls `load_paper_trading_dashboard(...)` and renders shaped DTOs only. The UI is fail-soft by section, cycle-scoped by Current Operational Window (COW), and strictly inspection-only with no trading mutations or control-plane actions.

**Tech Stack:** Python 3.12, FastAPI, Jinja2 templates, SQLAlchemy ORM, pytest, ruff, Alembic.

---

## Spec Source

Use the approved spec as the source of truth:

- `docs/superpowers/specs/2026-05-23-phase-6f-paper-trading-ui-design.md`

Implementation locks to preserve:

- `/lab/paper-trading` is read-only and auth-protected via existing lab/admin auth.
- Query model owns COW, overlay statuses, fail-soft section results, empty states, and system status.
- Route/template never derive lifecycle, stuck status, recovery, COW, health, or empty/degraded state.
- COW rows use `timestamp >= started_at`.
- Fresh DB with no tick is `Healthy` unless a query failure occurs.
- Routine audit rows are already loaded and revealed client-side; no second server query.
- Batch-load paper tables; no per-row query loops.
- No new trading state, no mutation endpoint, no replay/force-close/retry/kill-switch controls.
- `marketpulse.trading.calendar` publicly exposes `NY`; use that canonical
  timezone instead of defining another `ZoneInfo`.
- `TICK_COMPLETED` may be the COW boundary, but routine completed-tick rows do
  not count toward `routine_hidden_count`.
- Recovery and stuck overlays consider historical `PRICE_UNAVAILABLE` rows for
  the affected positions, not only rows inside the current COW.
- `load_paper_trading_dashboard(db, *, now=None)` accepts an optional clock
  value for deterministic tests.

## File Structure

- Create `marketpulse/trading/query_models.py`
  - Frozen DTOs.
  - `section_ok(...)` and `section_error(...)` invariant helpers.
  - COW loader, health summary loader, critical/audit projection helpers, positions overlay, order lifecycle rows, and top-level `load_paper_trading_dashboard(...)`.

- Modify `marketpulse/web/routes/lab.py`
  - Add authenticated `GET /lab/paper-trading`.
  - Route calls only `load_paper_trading_dashboard(db)`.
  - No POST route.

- Modify `marketpulse/web/templates/base.html`
  - Add nav link `纸上交易` for `/lab/paper-trading`.

- Create `marketpulse/web/templates/lab_paper_trading.html`
  - Compact Ops Console layout.
  - Health summary, critical events, positions, secondary client-side tabs for
    orders/fills and audit timeline.
  - Section-level degraded cards and explicit empty states.
  - Client-side routine reveal only.

- Modify `marketpulse/web/static/css/app.css`
  - Add Phase 6f classes near existing `/lab/backtest` and `/holdings` styles.
  - Reuse `.mp-card`, `.mp-table`, `.mp-chip`, and KPI typography.

- Create `tests/trading/test_query_models.py`
  - Pure query model and section invariant tests.

- Create `tests/web/test_lab_paper_trading.py`
  - Auth, route, template, degraded, empty, read-only/no-controls tests.

- Modify `tests/architecture/test_repository_boundary.py`
  - Add guard that `marketpulse/trading/query_models.py` remains read-only: no `.add`, `.commit`, `.merge`, `.delete`, and no `execute(insert/update/delete(...))`.
  - Existing guard already covers most of this; add explicit regression message for the new read-side file if needed during implementation.

---

### Task 1: Query Model DTOs and Section Invariants

**Files:**
- Create: `marketpulse/trading/query_models.py`
- Test: `tests/trading/test_query_models.py`

- [ ] **Step 1: Write failing tests for section helper invariants and fresh DB baseline**

Add this to `tests/trading/test_query_models.py`:

```python
"""Tests for Phase 6f paper-trading read-side query models."""

from __future__ import annotations

import pytest


def test_section_ok_requires_non_none_data():
    from marketpulse.trading.query_models import section_ok

    with pytest.raises(ValueError, match="ok SectionResult requires non-None data"):
        section_ok(None)


def test_section_error_requires_none_data():
    from marketpulse.trading.query_models import SectionResult

    with pytest.raises(ValueError, match="error SectionResult requires data=None"):
        SectionResult(status="error", data=[])


def test_section_error_sets_degraded_reason():
    from marketpulse.trading.query_models import section_error

    result = section_error("Unable to load Positions", "positions query failed")

    assert result.status == "error"
    assert result.data is None
    assert result.error_title == "Unable to load Positions"
    assert result.degraded_reason == "positions query failed"


def test_fresh_db_dashboard_is_healthy_empty_state(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    dashboard = load_paper_trading_dashboard(db_session)

    assert dashboard.system_status == "Healthy"
    assert dashboard.current_operational_window.started_at is None
    assert dashboard.current_operational_window.source_event_type is None
    assert dashboard.current_operational_window.label == "No paper tick has completed yet"
    assert dashboard.critical_events.status == "ok"
    assert dashboard.critical_events.data == []
    assert dashboard.critical_events.empty_message == "No operational events in current cycle"
    assert dashboard.positions.status == "ok"
    assert dashboard.positions.data == []
    assert dashboard.positions.empty_message == "No open paper positions"
    assert dashboard.order_lifecycles.status == "ok"
    assert dashboard.order_lifecycles.data == []
    assert dashboard.order_lifecycles.empty_message == "No order lifecycle activity in current cycle"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/trading/test_query_models.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'marketpulse.trading.query_models'`.

- [ ] **Step 3: Implement DTOs, helpers, and fresh DB top-level skeleton**

Create `marketpulse/trading/query_models.py` with:

```python
"""Read-side query models for the Phase 6f paper-trading operations UI.

This module is an inspection-plane consumer of paper_* state. It never writes
paper tables and never changes execution semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
pytest tests/trading/test_query_models.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/query_models.py tests/trading/test_query_models.py
git commit -m "feat(phase-6f): add paper dashboard query model DTOs"
```

---

### Task 2: Current Operational Window, Health Summary, and System Status

**Files:**
- Modify: `marketpulse/trading/query_models.py`
- Test: `tests/trading/test_query_models.py`

- [ ] **Step 1: Add failing tests for COW boundary, `>= started_at`, fresh DB health, kill switch, and degraded priority**

Append to `tests/trading/test_query_models.py`:

```python
from datetime import UTC, datetime, timedelta
from decimal import Decimal


def _audit(db_session, *, event_type, ts, reason="", context=None, order_id=None, strategy=None):
    from marketpulse.db.models import PaperAuditEvent

    row = PaperAuditEvent(
        timestamp=ts,
        event_type=event_type,
        order_id=order_id,
        strategy=strategy,
        reason=reason,
        context=context or {},
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_cow_uses_latest_boundary_and_includes_boundary_event(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    old = datetime(2026, 5, 22, 21, 30, tzinfo=UTC)
    new = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    _audit(db_session, event_type="TICK_COMPLETED", ts=old, context={"tick_date": "2026-05-22", "status": "completed"})
    boundary = _audit(
        db_session,
        event_type="TICK_REPROCESSED_COMPLETED",
        ts=new,
        reason="recovered_from_errors",
        context={"tick_date": "2026-05-23", "status": "completed", "prior_status": "completed_with_errors", "new_status": "completed"},
    )
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)

    assert dashboard.current_operational_window.started_at == new
    assert dashboard.current_operational_window.source_event_type == "TICK_REPROCESSED_COMPLETED"
    assert dashboard.system_status == "Attention"
    assert [row.audit_id for row in dashboard.audit_timeline.data.rows] == [boundary.id]


def test_completed_tick_without_warnings_is_healthy(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    ts = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    _audit(db_session, event_type="TICK_COMPLETED", ts=ts, context={"tick_date": "2026-05-23", "status": "completed"})
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)

    assert dashboard.system_status == "Healthy"
    assert dashboard.health.latest_tick_status == "completed"


def test_completed_with_errors_tick_is_attention(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    ts = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    _audit(db_session, event_type="TICK_COMPLETED", ts=ts, context={"tick_date": "2026-05-23", "status": "completed_with_errors"})
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)

    assert dashboard.system_status == "Attention"
    assert dashboard.health.latest_tick_status == "completed_with_errors"


def test_kill_switch_on_from_latest_flip_is_attention(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    ts = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    _audit(db_session, event_type="TICK_COMPLETED", ts=ts, context={"tick_date": "2026-05-23", "status": "completed"})
    _audit(
        db_session,
        event_type="KILL_SWITCH_FLIPPED",
        ts=datetime(2026, 5, 23, 21, 31, tzinfo=UTC),
        reason="manual_ui",
        context={"from_state": False, "to_state": True, "actor": "test"},
    )
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)

    assert dashboard.system_status == "Attention"
    assert dashboard.health.kill_switch_state == "ON"
    assert dashboard.health.kill_switch_reason == "manual_ui"


def test_env_kill_switch_override_is_reported(monkeypatch, db_session):
    from marketpulse.config import get_settings
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    monkeypatch.setenv("MP_PAPER_KILL_SWITCH", "true")
    get_settings.cache_clear()

    dashboard = load_paper_trading_dashboard(db_session)

    assert dashboard.health.kill_switch_state == "ON"
    assert dashboard.health.kill_switch_reason == "env override"
    assert dashboard.system_status == "Attention"

    get_settings.cache_clear()


def test_generated_at_label_uses_injected_now_and_ny_timezone(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    dashboard = load_paper_trading_dashboard(
        db_session,
        now=datetime(2026, 5, 23, 21, 34, tzinfo=UTC),
    )

    assert dashboard.generated_at == datetime(2026, 5, 23, 21, 34, tzinfo=UTC)
    assert dashboard.generated_at_label == "Generated at 17:34 NY"


def test_section_error_has_degraded_priority(db_session, monkeypatch):
    import marketpulse.trading.query_models as qm

    monkeypatch.setattr(
        qm,
        "_load_positions_section",
        lambda db, window, today, rows: qm.section_error("Unable to load Positions", "positions query failed"),
    )

    dashboard = qm.load_paper_trading_dashboard(db_session)

    assert dashboard.system_status == "Degraded"
    assert dashboard.positions.error_title == "Unable to load Positions"
    assert dashboard.positions.degraded_reason == "positions query failed"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/trading/test_query_models.py -q
```

Expected: failures for COW loader, kill switch source, status computation, and monkeypatched loader hook.

- [ ] **Step 3: Implement COW, health, and system-status loaders**

Modify `marketpulse/trading/query_models.py`:

```python
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, func, select

from marketpulse.config import get_settings
from marketpulse.db.models import PaperAuditEvent, PaperCashLedger, PaperFill, PaperPosition
```

Add constants and helpers:

```python
_COW_BOUNDARY_EVENTS = (
    "TICK_COMPLETED",
    "KILL_SWITCH_CYCLE_SKIPPED",
    "TICK_REPROCESSED_COMPLETED",
)


def _ny_now_date(generated_at: datetime) -> object:
    return generated_at.astimezone(NY).date()


def _format_ny_label(started_at: datetime | None) -> str:
    if started_at is None:
        return "No paper tick has completed yet"
    return f"Operational Window · Started {started_at.astimezone(NY):%Y-%m-%d %H:%M NY}"


def _load_operational_window(db: Session) -> OperationalWindow:
    row = db.execute(
        select(PaperAuditEvent)
        .where(PaperAuditEvent.event_type.in_(_COW_BOUNDARY_EVENTS))
        .order_by(desc(PaperAuditEvent.timestamp), desc(PaperAuditEvent.id))
        .limit(1)
    ).scalars().first()
    if row is None:
        return OperationalWindow(None, None, "No paper tick has completed yet")
    return OperationalWindow(row.timestamp, row.event_type, _format_ny_label(row.timestamp))


def _window_rows(db: Session, window: OperationalWindow) -> list[PaperAuditEvent]:
    if window.started_at is None:
        return []
    return list(
        db.execute(
            select(PaperAuditEvent)
            .where(PaperAuditEvent.timestamp >= window.started_at)
            .order_by(PaperAuditEvent.timestamp, PaperAuditEvent.id)
        ).scalars().all()
    )


def _latest_tick_status_from_window(rows: list[PaperAuditEvent]) -> str | None:
    for row in reversed(rows):
        if row.event_type == "TICK_REPROCESSED_COMPLETED":
            return "reprocessed_completed"
        if row.event_type == "KILL_SWITCH_CYCLE_SKIPPED":
            return "kill_switch_skipped"
        if row.event_type == "TICK_COMPLETED":
            return "completed" if (row.context or {}).get("status") == "completed" else "completed_with_errors"
    return None


def _load_kill_switch(db: Session) -> tuple[str, str | None]:
    settings = get_settings()
    if settings.paper_kill_switch:
        return "ON", "env override"
    row = db.execute(
        select(PaperAuditEvent)
        .where(PaperAuditEvent.event_type == "KILL_SWITCH_FLIPPED")
        .order_by(desc(PaperAuditEvent.timestamp), desc(PaperAuditEvent.id))
        .limit(1)
    ).scalars().first()
    if row is None:
        return "OFF", None
    return ("ON" if bool((row.context or {}).get("to_state")) else "OFF"), (row.reason or "unknown")


def _load_health_summary(
    db: Session,
    *,
    generated_at: datetime,
    rows: list[PaperAuditEvent],
) -> HealthSummary:
    latest_cash = db.execute(
        select(PaperCashLedger.balance_after).order_by(desc(PaperCashLedger.id)).limit(1)
    ).scalar()
    open_count = db.execute(
        select(func.count(PaperPosition.id)).where(PaperPosition.status == "OPEN")
    ).scalar() or 0
    ny_date = generated_at.astimezone(NY).date()
    ny_start = datetime.combine(ny_date, datetime.min.time(), tzinfo=NY).astimezone(UTC)
    ny_end = datetime.combine(ny_date + timedelta(days=1), datetime.min.time(), tzinfo=NY).astimezone(UTC)
    realized = db.execute(
        select(func.coalesce(func.sum(PaperFill.realized_pnl), Decimal("0")))
        .where(PaperFill.side == "EXIT")
        .where(PaperFill.filled_at >= ny_start)
        .where(PaperFill.filled_at < ny_end)
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
```

Then implement status and loader composition:

```python
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
    if health.latest_tick_status in {"completed_with_errors", "kill_switch_skipped", "reprocessed_completed"}:
        return "Attention"
    if _has_attention_events(critical_events):
        return "Attention"
    return "Healthy"
```

Replace `load_paper_trading_dashboard(...)` body so it calls:

```python
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
```

Include `generated_at_label=generated_at_label` when constructing
`PaperTradingDashboard`.

For this task, add temporary section loaders that return empty `section_ok(...)`; later tasks replace them:

```python
def _load_critical_events_section(db: Session, window: OperationalWindow, rows: list[PaperAuditEvent]) -> SectionResult[list[OperationalEvent]]:
    del db, window, rows
    return section_ok([], "No operational events in current cycle")


def _load_positions_section(db: Session, window: OperationalWindow, today: object, rows: list[PaperAuditEvent]) -> SectionResult[list[PositionRow]]:
    del db, window, today, rows
    return section_ok([], "No open paper positions")


def _load_order_lifecycles_section(db: Session, window: OperationalWindow, rows: list[PaperAuditEvent]) -> SectionResult[list[OrderLifecycleRow]]:
    del db, window, rows
    return section_ok([], "No order lifecycle activity in current cycle")


def _load_audit_timeline_section(db: Session, window: OperationalWindow, rows: list[PaperAuditEvent]) -> SectionResult[AuditTimeline]:
    del db, window, rows
    return section_ok(AuditTimeline(rows=[], routine_hidden_count=0), "No operational events in current cycle")
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
pytest tests/trading/test_query_models.py -q
```

Expected: all Task 1 and Task 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/query_models.py tests/trading/test_query_models.py
git commit -m "feat(phase-6f): compute paper operational window and health"
```

---

### Task 3: Critical Events and Audit Timeline Projection

**Files:**
- Modify: `marketpulse/trading/query_models.py`
- Test: `tests/trading/test_query_models.py`

- [ ] **Step 1: Add failing tests for critical selection, recovery collapse, routine hidden count, and additive routine rows**

Append to `tests/trading/test_query_models.py`:

```python
def test_price_unavailable_three_plus_is_attention_and_visible(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    start = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    _audit(db_session, event_type="TICK_COMPLETED", ts=start, context={"tick_date": "2026-05-23", "status": "completed"})
    pu = _audit(
        db_session,
        event_type="PRICE_UNAVAILABLE",
        ts=datetime(2026, 5, 23, 21, 31, tzinfo=UTC),
        reason="no_price",
        context={"position_id": 7, "ticker": "AAPL", "attempt_count": 3},
        order_id=11,
        strategy="momentum_breakout",
    )
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)

    assert dashboard.system_status == "Attention"
    assert [event.audit_id for event in dashboard.critical_events.data] == [pu.id]
    assert dashboard.critical_events.data[0].severity == "warning"
    assert any(row.audit_id == pu.id for row in dashboard.audit_timeline.data.rows)


def test_position_closed_recovery_collapses_prior_price_unavailable(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    start = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    _audit(db_session, event_type="TICK_COMPLETED", ts=start, context={"tick_date": "2026-05-23", "status": "completed"})
    _audit(db_session, event_type="PRICE_UNAVAILABLE", ts=datetime(2026, 5, 23, 21, 31, tzinfo=UTC), reason="no_price", context={"position_id": 7, "ticker": "AAPL", "attempt_count": 3})
    recovered = _audit(db_session, event_type="POSITION_CLOSED", ts=datetime(2026, 5, 23, 21, 32, tzinfo=UTC), reason="closed", context={"position_id": 7, "ticker": "AAPL", "retry_count": 3})
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)

    assert dashboard.system_status == "Healthy"
    assert [event.event_type for event in dashboard.critical_events.data] == ["POSITION_CLOSED"]
    assert dashboard.critical_events.data[0].severity == "recovery"
    assert dashboard.critical_events.data[0].audit_id == recovered.id
    assert [row.event_type for row in dashboard.audit_timeline.data.rows] == [
        "PRICE_UNAVAILABLE",
        "POSITION_CLOSED",
    ]


def test_position_closed_recovery_uses_historical_price_unavailable(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    _audit(db_session, event_type="PRICE_UNAVAILABLE", ts=datetime(2026, 5, 22, 21, 31, tzinfo=UTC), reason="no_price", context={"position_id": 7, "ticker": "AAPL", "attempt_count": 3})
    start = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    _audit(db_session, event_type="TICK_COMPLETED", ts=start, context={"tick_date": "2026-05-23", "status": "completed"})
    recovered = _audit(db_session, event_type="POSITION_CLOSED", ts=datetime(2026, 5, 23, 21, 32, tzinfo=UTC), reason="closed", context={"position_id": 7, "ticker": "AAPL"})
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)

    assert [event.event_type for event in dashboard.critical_events.data] == ["POSITION_CLOSED"]
    assert dashboard.critical_events.data[0].severity == "recovery"
    assert dashboard.critical_events.data[0].audit_id == recovered.id


def test_audit_timeline_hides_routine_rows_but_loads_them_for_client_reveal(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    start = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    _audit(db_session, event_type="TICK_COMPLETED", ts=start, context={"tick_date": "2026-05-23", "status": "completed"})
    placed = _audit(db_session, event_type="ORDER_PLACED", ts=datetime(2026, 5, 23, 21, 31, tzinfo=UTC), reason="", context={"ticker": "AAPL"})
    rejected = _audit(db_session, event_type="ORDER_REJECTED", ts=datetime(2026, 5, 23, 21, 32, tzinfo=UTC), reason="risk_gate_failed", context={"failed_gates": ["daily_loss"], "ticker": "MSFT"})
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)
    timeline = dashboard.audit_timeline.data

    assert timeline.routine_hidden_count == 1
    assert {row.audit_id for row in timeline.rows} == {placed.id, rejected.id}
    assert [row.routine for row in timeline.rows if row.audit_id == placed.id] == [True]
    assert [row.routine for row in timeline.rows if row.audit_id == rejected.id] == [False]
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/trading/test_query_models.py -q
```

Expected: failures because projection sections still return empty rows.

- [ ] **Step 3: Implement projection helpers**

Add constants:

```python
_ROUTINE_EVENTS = {
    "ORDER_PLACED",
    "ORDER_PLACED_DUPLICATE",
    "ORDER_ENTRY_FILLED",
    "TICK_COMPLETED",
}
_ALWAYS_RELEVANT_EVENTS = {
    "ENGINE_INVARIANT_ERROR",
    "SCHEDULER_GAP_DETECTED",
    "TICK_REPROCESSED_COMPLETED",
    "KILL_SWITCH_FLIPPED",
    "KILL_SWITCH_CYCLE_SKIPPED",
}
```

Add helpers:

```python
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
    context = row.context or {}
    value = context.get("ticker")
    return str(value) if value is not None else None


def _is_daily_loss_or_failed_gate(row: PaperAuditEvent) -> bool:
    context = row.context or {}
    gates = context.get("failed_gates")
    return isinstance(gates, list) and bool(gates)


def _position_id(row: PaperAuditEvent) -> int | None:
    return _context_int(row.context or {}, "position_id")


def _severity_for(row: PaperAuditEvent, recovered_positions: set[int]) -> Literal["critical", "warning", "recovery", "routine"]:
    if row.event_type in {"ENGINE_INVARIANT_ERROR", "SCHEDULER_GAP_DETECTED", "KILL_SWITCH_FLIPPED", "KILL_SWITCH_CYCLE_SKIPPED", "TICK_REPROCESSED_COMPLETED"}:
        return "critical" if row.event_type == "ENGINE_INVARIANT_ERROR" else "warning"
    if row.event_type == "PRICE_UNAVAILABLE":
        return "warning"
    if row.event_type == "ORDER_REJECTED" and _is_daily_loss_or_failed_gate(row):
        return "warning"
    if row.event_type == "POSITION_CLOSED" and _position_id(row) in recovered_positions:
        return "recovery"
    return "routine"


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
    position_id_expr = func.json_extract(PaperAuditEvent.context, "$.position_id")
    close_ts_by_position = {
        pid: row.timestamp
        for row in close_rows
        if (pid := _position_id(row)) is not None
    }
    if not close_ts_by_position:
        return set()
    pu_rows = db.execute(
        select(position_id_expr, PaperAuditEvent.timestamp)
        .where(PaperAuditEvent.event_type == "PRICE_UNAVAILABLE")
        .where(position_id_expr.in_(sorted(close_ts_by_position)))
        .where(PaperAuditEvent.timestamp < max(close_ts_by_position.values()))
    ).all()
    return {
        int(position_id)
        for position_id, pu_timestamp in pu_rows
        if position_id is not None
        and pu_timestamp < close_ts_by_position.get(int(position_id), datetime.min.replace(tzinfo=UTC))
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
```

Update the top-level loader after `rows = _window_rows(db, window)`:

```python
projection = _build_projection_context(db, rows)
critical_events = _load_critical_events_section(db, window, projection)
audit_timeline = _load_audit_timeline_section(db, window, projection)
```

Leave Positions and Orders/Fills on `rows` at this point. Positions computes
latest PU attempts once after it has the loaded position IDs.

Replace `_load_critical_events_section(...)`:

```python
def _load_critical_events_section(
    db: Session,
    window: OperationalWindow,
    projection: ProjectionContext,
) -> SectionResult[list[OperationalEvent]]:
    del db, window
    recovered = projection.recovered_positions
    suppressed_pu = projection.suppressed_price_unavailable_positions
    events: list[OperationalEvent] = []
    for row in projection.rows:
        pid = _position_id(row)
        severity = _severity_for(row, recovered)
        if row.event_type == "PRICE_UNAVAILABLE" and pid in suppressed_pu:
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
            )
        )
    return section_ok(events, "No operational events in current cycle")
```

Critical Events collapses/suppresses `PRICE_UNAVAILABLE` when the position is
recovered. Audit Timeline preserves the underlying non-routine rows for
debugging context.

Replace `_load_audit_timeline_section(...)`:

```python
def _load_audit_timeline_section(
    db: Session,
    window: OperationalWindow,
    projection: ProjectionContext,
) -> SectionResult[AuditTimeline]:
    del db, window
    recovered = projection.recovered_positions
    timeline_rows: list[AuditTimelineRow] = []
    hidden_count = 0
    for row in projection.rows:
        if row.event_type == "TICK_COMPLETED":
            continue
        severity = _severity_for(row, recovered)
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
            )
        )
    return section_ok(
        AuditTimeline(rows=timeline_rows, routine_hidden_count=hidden_count),
        "No operational events in current cycle",
    )
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
pytest tests/trading/test_query_models.py -q
```

Expected: all query-model tests pass.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/query_models.py tests/trading/test_query_models.py
git commit -m "feat(phase-6f): project paper operational events"
```

---

### Task 4: Positions Overlay and Order Lifecycle Rows

**Files:**
- Modify: `marketpulse/trading/query_models.py`
- Test: `tests/trading/test_query_models.py`

- [ ] **Step 1: Add failing tests for position exit health and order lifecycle aggregation**

Append to `tests/trading/test_query_models.py`:

```python
from datetime import date


def _paper_order(db_session, *, ticker="AAPL", strategy="momentum_breakout", status="ENTRY_FILLED", placed_at=None, filled_at=None):
    from decimal import Decimal
    from marketpulse.db.models import PaperOrder

    ts = placed_at or datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    row = PaperOrder(
        idempotency_key=f"key-{ticker}-{ts.timestamp()}",
        allocation_run_id="run-1",
        strategy=strategy,
        ticker=ticker,
        quantity=3,
        event_time=ts,
        allocation_date=date(2026, 5, 23),
        horizon_date=date(2026, 5, 23),
        placed_at=ts,
        filled_at=filled_at,
        cancelled_at=None,
        cancel_reason=None,
        event_price=Decimal("100"),
        horizon_price=None,
        status=status,
        strategy_version="v1",
        allocator_version="v1",
        execution_engine_version="v1",
        weight=1.0,
        raw_bid_weight=None,
        pool_corr=None,
        contribution_multiplier=1.0,
        adjusted_bid_weight=None,
        effective_corr_window=60,
        rewarded_for_negative_corr=False,
        would_change_rank=False,
        size_clamped_by_override=False,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _position(db_session, order, *, status="OPEN", horizon_date=date(2026, 5, 23), realized_pnl=None, closed_at=None):
    from decimal import Decimal
    from marketpulse.db.models import PaperPosition

    row = PaperPosition(
        order_id=order.id,
        entry_fill_id=None,
        exit_fill_id=None,
        strategy=order.strategy,
        ticker=order.ticker,
        quantity=order.quantity,
        entry_price=Decimal("100"),
        entry_date=date(2026, 5, 20),
        horizon_date=horizon_date,
        status=status,
        opened_at=order.placed_at,
        closed_at=closed_at,
        exit_price=Decimal("110") if status == "CLOSED" else None,
        realized_pnl=realized_pnl,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _fill(db_session, *, order, position, side, price, filled_at, realized_pnl=None):
    from decimal import Decimal
    from marketpulse.db.models import PaperFill

    row = PaperFill(
        order_id=order.id,
        position_id=position.id,
        side=side,
        price=Decimal(price),
        quantity=order.quantity,
        filled_at=filled_at,
        cash_delta=Decimal("-300") if side == "ENTRY" else Decimal("330"),
        realized_pnl=realized_pnl,
    )
    db_session.add(row)
    db_session.flush()
    if side == "ENTRY":
        position.entry_fill_id = row.id
    else:
        position.exit_fill_id = row.id
    db_session.flush()
    return row


def test_positions_overlay_exit_health_attempts(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    start = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    _audit(db_session, event_type="TICK_COMPLETED", ts=start, context={"tick_date": "2026-05-23", "status": "completed"})
    order = _paper_order(db_session)
    position = _position(db_session, order, horizon_date=date(2026, 5, 23))
    _audit(db_session, event_type="PRICE_UNAVAILABLE", ts=datetime(2026, 5, 23, 21, 31, tzinfo=UTC), context={"position_id": position.id, "attempt_count": 2, "ticker": "AAPL"})
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)

    assert len(dashboard.positions.data) == 1
    row = dashboard.positions.data[0]
    assert row.canonical_status == "OPEN"
    assert row.operational_exit_status == "PRICE_UNAVAILABLE_2"
    assert row.exit_health_label == "Price unavailable 2/3"


def test_positions_overlay_stuck_three_plus(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    start = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    _audit(db_session, event_type="TICK_COMPLETED", ts=start, context={"tick_date": "2026-05-23", "status": "completed"})
    order = _paper_order(db_session)
    position = _position(db_session, order, horizon_date=date(2026, 5, 23))
    _audit(db_session, event_type="PRICE_UNAVAILABLE", ts=datetime(2026, 5, 23, 21, 31, tzinfo=UTC), context={"position_id": position.id, "attempt_count": 3, "ticker": "AAPL"})
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)

    assert dashboard.positions.data[0].operational_exit_status == "STUCK_3_PLUS"
    assert dashboard.positions.data[0].exit_health_label == "Stuck 3+"


def test_positions_overlay_uses_historical_price_unavailable_attempts(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    order = _paper_order(db_session)
    position = _position(db_session, order, horizon_date=date(2026, 5, 23))
    _audit(db_session, event_type="PRICE_UNAVAILABLE", ts=datetime(2026, 5, 22, 21, 31, tzinfo=UTC), context={"position_id": position.id, "attempt_count": 3, "ticker": "AAPL"})
    _audit(db_session, event_type="TICK_COMPLETED", ts=datetime(2026, 5, 23, 21, 30, tzinfo=UTC), context={"tick_date": "2026-05-23", "status": "completed"})
    db_session.commit()

    dashboard = load_paper_trading_dashboard(
        db_session,
        now=datetime(2026, 5, 23, 22, 0, tzinfo=UTC),
    )

    assert dashboard.positions.data[0].operational_exit_status == "STUCK_3_PLUS"
    assert dashboard.positions.data[0].exit_health_label == "Stuck 3+"


def test_order_lifecycle_joins_entry_exit_fills_and_latest_cow_audit(db_session):
    from decimal import Decimal
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    start = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    _audit(db_session, event_type="TICK_COMPLETED", ts=start, context={"tick_date": "2026-05-23", "status": "completed"})
    order = _paper_order(db_session, filled_at=datetime(2026, 5, 23, 21, 31, tzinfo=UTC))
    position = _position(db_session, order, status="CLOSED", realized_pnl=Decimal("30"), closed_at=datetime(2026, 5, 23, 21, 40, tzinfo=UTC))
    _fill(db_session, order=order, position=position, side="ENTRY", price="100", filled_at=datetime(2026, 5, 23, 21, 31, tzinfo=UTC))
    _fill(db_session, order=order, position=position, side="EXIT", price="110", filled_at=datetime(2026, 5, 23, 21, 40, tzinfo=UTC), realized_pnl=Decimal("30"))
    _audit(db_session, event_type="ORDER_ENTRY_FILLED", ts=datetime(2026, 5, 23, 21, 31, tzinfo=UTC), order_id=order.id, reason="entry_filled", context={"ticker": "AAPL"})
    _audit(db_session, event_type="POSITION_CLOSED", ts=datetime(2026, 5, 23, 21, 40, tzinfo=UTC), order_id=order.id, reason="closed", context={"position_id": position.id, "ticker": "AAPL"})
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)

    row = dashboard.order_lifecycles.data[0]
    assert row.order_id == order.id
    assert row.entry_price == Decimal("100.000000")
    assert row.exit_price == Decimal("110.000000")
    assert row.realized_pnl == Decimal("30.000000")
    assert row.latest_audit_reason == "closed"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/trading/test_query_models.py -q
```

Expected: position and order lifecycle tests fail.

- [ ] **Step 3: Implement batch-loaded positions and lifecycles**

Update imports:

```python
from sqlalchemy import Integer, cast, desc, func, select

from marketpulse.db.models import PaperAuditEvent, PaperCashLedger, PaperFill, PaperOrder, PaperPosition
```

Add helpers:

```python
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
            .group_by(position_id_expr)
        ).all()
        attempts.update(
            {
                int(row.position_id): int(row.max_attempt)
                for row in historical
                if row.position_id is not None and row.max_attempt is not None
            }
        )
    for row in rows:
        if row.event_type != "PRICE_UNAVAILABLE":
            continue
        pid = _position_id(row)
        attempt = _context_int(row.context or {}, "attempt_count")
        if pid is not None and attempt is not None:
            attempts[pid] = max(attempts.get(pid, 0), attempt)
    return attempts


def _exit_status(position: PaperPosition, *, today, attempts: dict[int, int]) -> tuple[OperationalExitStatus, str]:
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
```

Replace `_load_positions_section(...)`:

```python
def _load_positions_section(
    db: Session,
    window: OperationalWindow,
    today: object,
    rows: list[PaperAuditEvent],
) -> SectionResult[list[PositionRow]]:
    recovered = _recovered_positions(db, rows)
    recovered_ids = sorted(recovered)
    query = select(PaperPosition).where(PaperPosition.status == "OPEN")
    open_positions = list(db.execute(query.order_by(PaperPosition.id)).scalars().all())
    recovered_positions = []
    if recovered_ids:
        recovered_positions = list(
            db.execute(
                select(PaperPosition)
                .where(PaperPosition.id.in_(recovered_ids))
                .where(PaperPosition.status == "CLOSED")
                .order_by(PaperPosition.id)
            ).scalars().all()
        )
    all_position_ids = {
        position.id
        for position in [*open_positions, *recovered_positions]
    }
    attempts = _latest_pu_attempts(db, position_ids=all_position_ids, rows=rows)
    seen: set[int] = set()
    out: list[PositionRow] = []
    for position in [*open_positions, *recovered_positions]:
        if position.id in seen:
            continue
        seen.add(position.id)
        status, label = _exit_status(position, today=today, attempts=attempts)
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
                operational_exit_status=status,
                exit_health_label=label,
                realized_pnl=position.realized_pnl,
            )
        )
    return section_ok(out, "No open paper positions")
```

Replace `_load_order_lifecycles_section(...)`:

```python
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
        db.execute(select(PaperOrder).where(PaperOrder.id.in_(order_ids)).order_by(PaperOrder.id)).scalars().all()
    )
    positions = list(
        db.execute(select(PaperPosition).where(PaperPosition.order_id.in_(order_ids))).scalars().all()
    )
    fills = list(
        db.execute(select(PaperFill).where(PaperFill.order_id.in_(order_ids))).scalars().all()
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
                exit_time=exit_fill.filled_at if exit_fill is not None else (position.closed_at if position else None),
                realized_pnl=(exit_fill.realized_pnl if exit_fill is not None else (position.realized_pnl if position else None)),
                latest_audit_reason=latest_reason_by_order.get(order.id),
            )
        )
    return section_ok(out, "No order lifecycle activity in current cycle")
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
pytest tests/trading/test_query_models.py -q
```

Expected: all query-model tests pass.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/query_models.py tests/trading/test_query_models.py
git commit -m "feat(phase-6f): add paper positions and lifecycle projections"
```

---

### Task 5: `/lab/paper-trading` Route, Auth, Nav, and Read-Only Boundary

**Files:**
- Modify: `marketpulse/web/routes/lab.py`
- Modify: `marketpulse/web/templates/base.html`
- Create: `marketpulse/web/templates/lab_paper_trading.html`
- Test: `tests/web/test_lab_paper_trading.py`

- [ ] **Step 1: Write failing route/template tests**

Create `tests/web/test_lab_paper_trading.py`:

```python
"""Tests for /lab/paper-trading operations dashboard."""

from __future__ import annotations

from marketpulse.auth.password import hash_password


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def test_paper_trading_requires_auth(client):
    response = client.get("/lab/paper-trading", follow_redirects=False)
    assert response.status_code == 303


def test_paper_trading_post_not_registered(client, monkeypatch):
    _login(client, monkeypatch)
    response = client.post("/lab/paper-trading")
    assert response.status_code == 405


def test_paper_trading_fresh_db_renders_empty_healthy_page(client, monkeypatch):
    _login(client, monkeypatch)
    response = client.get("/lab/paper-trading")

    assert response.status_code == 200
    assert "Paper Trading · Operations" in response.text
    assert "System Status" in response.text
    assert "Healthy" in response.text
    assert "Generated at" in response.text
    assert "No paper tick has completed yet" in response.text
    assert "No open paper positions" in response.text
    assert "No operational events in current cycle" in response.text
    assert "No order lifecycle activity in current cycle" in response.text
    assert "纸上交易" in response.text


def test_paper_trading_has_no_control_plane_controls(client, monkeypatch):
    _login(client, monkeypatch)
    response = client.get("/lab/paper-trading")

    assert "Force Close" not in response.text
    assert "Replay" not in response.text
    assert "Retry" not in response.text
    assert "Kill Switch Toggle" not in response.text
    assert 'type="submit"' not in response.text
```

- [ ] **Step 2: Run route tests and verify failure**

Run:

```bash
pytest tests/web/test_lab_paper_trading.py -q
```

Expected: 404 for GET and 405 test not yet meaningful.

- [ ] **Step 3: Add route**

Modify `marketpulse/web/routes/lab.py` imports:

```python
from marketpulse.trading import query_models as paper_query_models
```

Add route at the end of the file:

```python
@router.get("/lab/paper-trading", response_class=HTMLResponse)
def lab_paper_trading(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    dashboard = paper_query_models.load_paper_trading_dashboard(db)
    return templates.TemplateResponse(
        request,
        "lab_paper_trading.html",
        {"dashboard": dashboard},
    )
```

- [ ] **Step 4: Add nav link**

Modify `marketpulse/web/templates/base.html` nav links:

```html
<a href="/lab/paper-trading" class="{% if p.startswith('/lab/paper-trading') %}mp-nav-active{% endif %}">纸上交易</a>
```

Place it next to the existing lab/backtest links.

- [ ] **Step 5: Add minimal template**

Create `marketpulse/web/templates/lab_paper_trading.html`:

```html
{% extends "base.html" %}
{% block main_width %}max-w-[1800px]{% endblock %}
{% block nav_width %}max-w-[1800px]{% endblock %}
{% block content %}

<section class="mp-paper-ops">
  <header class="mp-paper-ops__header">
    <div>
      <p class="mp-eyebrow mp-eyebrow--primary">Lab · Inspection Plane</p>
      <h1>Paper Trading · Operations</h1>
      <p>{{ dashboard.current_operational_window.label }}</p>
    </div>
    <div class="mp-paper-ops__generated">{{ dashboard.generated_at_label }}</div>
  </header>

  <section class="mp-paper-kpis" aria-label="Health Summary">
    <article class="mp-card mp-paper-kpi">
      <div class="mp-card__body">
        <div class="mp-card__eyebrow">System Status</div>
        <div class="mp-paper-kpi__value">{{ dashboard.system_status }}</div>
      </div>
    </article>
    <article class="mp-card mp-paper-kpi">
      <div class="mp-card__body">
        <div class="mp-card__eyebrow">Cash Balance</div>
        <div class="mp-paper-kpi__value">${{ dashboard.health.cash_balance }}</div>
      </div>
    </article>
    <article class="mp-card mp-paper-kpi">
      <div class="mp-card__body">
        <div class="mp-card__eyebrow">Realized P&amp;L Today</div>
        <div class="mp-paper-kpi__value">{{ dashboard.health.realized_pnl_today }}</div>
      </div>
    </article>
    <article class="mp-card mp-paper-kpi">
      <div class="mp-card__body">
        <div class="mp-card__eyebrow">Open Positions</div>
        <div class="mp-paper-kpi__value">{{ dashboard.health.open_positions_count }}</div>
      </div>
    </article>
    <article class="mp-card mp-paper-kpi">
      <div class="mp-card__body">
        <div class="mp-card__eyebrow">Kill Switch</div>
        <div class="mp-paper-kpi__value">{{ dashboard.health.kill_switch_state }}</div>
        {% if dashboard.health.kill_switch_reason %}<div class="mp-paper-muted">{{ dashboard.health.kill_switch_reason }}</div>{% endif %}
      </div>
    </article>
  </section>

  <section class="mp-paper-primary-row">
    <article class="mp-card">
      <div class="mp-card__head"><div class="mp-card__title">Critical Events</div></div>
      <div class="mp-card__body">
        {% if dashboard.critical_events.status == "error" %}
          <div class="mp-paper-degraded">{{ dashboard.critical_events.error_title }}</div>
        {% elif dashboard.critical_events.data %}
          <ul class="mp-paper-events">
            {% for event in dashboard.critical_events.data %}
              <li><strong>{{ event.title }}</strong><span>{{ event.ticker or "" }}</span><small>{{ event.detail }}</small></li>
            {% endfor %}
          </ul>
        {% else %}
          <div class="mp-paper-empty">{{ dashboard.critical_events.empty_message }}</div>
        {% endif %}
      </div>
    </article>

    <article class="mp-card">
      <div class="mp-card__head"><div class="mp-card__title">Positions</div></div>
      <div class="mp-card__body">
        {% if dashboard.positions.status == "error" %}
          <div class="mp-paper-degraded">{{ dashboard.positions.error_title }}</div>
        {% elif dashboard.positions.data %}
          <div class="mp-paper-table-wrap">
            <table class="mp-table mp-table--paper">
              <thead><tr><th>Ticker</th><th>Strategy</th><th class="num">Qty</th><th class="num">Entry</th><th>Horizon</th><th>Status</th><th>Exit Health</th><th class="num">Realized P&amp;L</th></tr></thead>
              <tbody>
              {% for row in dashboard.positions.data %}
                <tr>
                  <td>{{ row.ticker }}</td>
                  <td>{{ row.strategy }}</td>
                  <td class="num">{{ row.quantity }}</td>
                  <td class="num">{{ row.entry_price }}</td>
                  <td>{{ row.horizon_date }}</td>
                  <td>{{ row.canonical_status }}</td>
                  <td>{{ row.exit_health_label }}</td>
                  <td class="num">{{ row.realized_pnl or "" }}</td>
                </tr>
              {% endfor %}
              </tbody>
            </table>
          </div>
        {% else %}
          <div class="mp-paper-empty">{{ dashboard.positions.empty_message }}</div>
        {% endif %}
      </div>
    </article>
  </section>

  <section class="mp-paper-drilldown">
    <div class="mp-paper-tabs" role="tablist" aria-label="Paper trading drill-down">
      <button type="button" class="mp-paper-tab is-active" data-paper-tab="orders">Orders &amp; Fills</button>
      <button type="button" class="mp-paper-tab" data-paper-tab="audit">Audit Timeline</button>
    </div>

    <article class="mp-card mp-paper-tab-panel" data-paper-panel="orders">
      <div class="mp-card__head"><div class="mp-card__title">Orders &amp; Fills</div></div>
      <div class="mp-card__body">
        {% if dashboard.order_lifecycles.status == "error" %}
          <div class="mp-paper-degraded">{{ dashboard.order_lifecycles.error_title }}</div>
        {% elif dashboard.order_lifecycles.data %}
          <div class="mp-paper-table-wrap">
            <table class="mp-table mp-table--paper">
              <thead><tr><th>Order ID</th><th>Ticker</th><th>Strategy</th><th class="num">Qty</th><th>Status</th><th>Placed</th><th>Entry</th><th>Exit</th><th class="num">P&amp;L</th><th>Latest Audit Reason</th></tr></thead>
              <tbody>
              {% for row in dashboard.order_lifecycles.data %}
                <tr><td>{{ row.order_id }}</td><td>{{ row.ticker }}</td><td>{{ row.strategy }}</td><td class="num">{{ row.quantity }}</td><td>{{ row.order_status }}</td><td>{{ row.placed_at }}</td><td>{{ row.entry_price or "" }}</td><td>{{ row.exit_price or "" }}</td><td class="num">{{ row.realized_pnl or "" }}</td><td>{{ row.latest_audit_reason or "" }}</td></tr>
              {% endfor %}
              </tbody>
            </table>
          </div>
        {% else %}
          <div class="mp-paper-empty">{{ dashboard.order_lifecycles.empty_message }}</div>
        {% endif %}
      </div>
    </article>

    <article class="mp-card mp-paper-tab-panel is-hidden" data-paper-panel="audit">
      <div class="mp-card__head"><div class="mp-card__title">Audit Timeline</div></div>
      <div class="mp-card__body">
        {% if dashboard.audit_timeline.status == "error" %}
          <div class="mp-paper-degraded">{{ dashboard.audit_timeline.error_title }}</div>
        {% elif dashboard.audit_timeline.data.rows %}
          {% if dashboard.audit_timeline.data.routine_hidden_count %}
            <button type="button" class="mp-paper-routine-toggle" data-routine-toggle>Show routine events · {{ dashboard.audit_timeline.data.routine_hidden_count }} hidden</button>
          {% endif %}
          <ul class="mp-paper-audit-list">
            {% for row in dashboard.audit_timeline.data.rows %}
              <li class="{% if row.routine %}mp-paper-routine-row is-hidden{% endif %}"><strong>{{ row.event_type }}</strong><span>{{ row.ticker or "" }}</span><small>{{ row.reason }}</small></li>
            {% endfor %}
          </ul>
        {% else %}
          <div class="mp-paper-empty">{{ dashboard.audit_timeline.empty_message }}</div>
        {% endif %}
      </div>
    </article>
  </section>
</section>

<script>
document.querySelectorAll("[data-paper-tab]").forEach((tab) => {
  tab.addEventListener("click", () => {
    const target = tab.getAttribute("data-paper-tab");
    document.querySelectorAll("[data-paper-tab]").forEach((item) => item.classList.toggle("is-active", item === tab));
    document.querySelectorAll("[data-paper-panel]").forEach((panel) => {
      panel.classList.toggle("is-hidden", panel.getAttribute("data-paper-panel") !== target);
    });
  });
});
document.querySelectorAll("[data-routine-toggle]").forEach((toggle) => {
  toggle.addEventListener("click", () => {
    document.querySelectorAll(".mp-paper-routine-row").forEach((row) => row.classList.toggle("is-hidden"));
  });
});
</script>

{% endblock %}
```

- [ ] **Step 6: Run route tests and verify pass**

Run:

```bash
pytest tests/web/test_lab_paper_trading.py -q
```

Expected: all web tests pass.

- [ ] **Step 7: Commit**

```bash
git add marketpulse/web/routes/lab.py marketpulse/web/templates/base.html marketpulse/web/templates/lab_paper_trading.html tests/web/test_lab_paper_trading.py
git commit -m "feat(phase-6f): add paper trading operations route"
```

---

### Task 6: Fail-Soft Degraded Rendering

**Files:**
- Modify: `marketpulse/trading/query_models.py`
- Modify: `marketpulse/web/templates/lab_paper_trading.html`
- Test: `tests/trading/test_query_models.py`
- Test: `tests/web/test_lab_paper_trading.py`

- [ ] **Step 1: Add failing query-model test for partial failure**

Append to `tests/trading/test_query_models.py`:

```python
def test_positions_loader_failure_degrades_only_positions(db_session, monkeypatch):
    import marketpulse.trading.query_models as qm

    def fail_positions(db, window, today, rows):
        raise RuntimeError("boom")

    monkeypatch.setattr(qm, "_load_positions_section", fail_positions)

    dashboard = qm.load_paper_trading_dashboard(db_session)

    assert dashboard.system_status == "Degraded"
    assert dashboard.positions.status == "error"
    assert dashboard.positions.error_title == "Unable to load Positions"
    assert dashboard.positions.degraded_reason == "RuntimeError"
    assert dashboard.critical_events.status == "ok"
    assert dashboard.order_lifecycles.status == "ok"
    assert dashboard.audit_timeline.status == "ok"
```

- [ ] **Step 2: Add failing route render test for degraded section**

Append to `tests/web/test_lab_paper_trading.py`:

```python
def test_paper_trading_renders_degraded_positions_section(client, monkeypatch):
    _login(client, monkeypatch)

    import marketpulse.trading.query_models as qm

    def fail_positions(db, window, today, rows):
        raise RuntimeError("boom")

    monkeypatch.setattr(qm, "_load_positions_section", fail_positions)
    response = client.get("/lab/paper-trading")

    assert response.status_code == 200
    assert "Degraded" in response.text
    assert "Unable to load Positions" in response.text
    assert "Traceback" not in response.text
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```bash
pytest tests/trading/test_query_models.py tests/web/test_lab_paper_trading.py -q
```

Expected: failure because loader exceptions propagate or monkeypatch misses the route import.

- [ ] **Step 4: Implement section failure wrapping**

In `marketpulse/trading/query_models.py`, add:

```python
def _safe_section(title: str, loader):
    try:
        return loader()
    except Exception as exc:
        return section_error(title, type(exc).__name__)
```

Update the top-level loader:

```python
projection = _build_projection_context(db, rows)
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
```

Do not wrap route/template rendering exceptions.

- [ ] **Step 5: Run tests and verify pass**

Run:

```bash
pytest tests/trading/test_query_models.py tests/web/test_lab_paper_trading.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/trading/query_models.py marketpulse/web/templates/lab_paper_trading.html tests/trading/test_query_models.py tests/web/test_lab_paper_trading.py
git commit -m "feat(phase-6f): render degraded paper dashboard sections"
```

---

### Task 7: Compact Ops Console Styling

**Files:**
- Modify: `marketpulse/web/static/css/app.css`
- Modify: `marketpulse/web/templates/lab_paper_trading.html`
- Test: `tests/web/test_lab_paper_trading.py`

- [ ] **Step 1: Add HTML structure assertions for visual hierarchy**

Append to `tests/web/test_lab_paper_trading.py`:

```python
def test_paper_trading_uses_compact_ops_console_layout(client, monkeypatch):
    _login(client, monkeypatch)
    response = client.get("/lab/paper-trading")

    assert "mp-paper-ops" in response.text
    assert "mp-paper-kpis" in response.text
    assert "mp-paper-primary-row" in response.text
    assert response.text.index("Critical Events") < response.text.index("Orders &amp; Fills")
    assert response.text.index("Positions") < response.text.index("Audit Timeline")
```

- [ ] **Step 2: Run web tests**

Run:

```bash
pytest tests/web/test_lab_paper_trading.py -q
```

Expected: pass if classes already exist from Task 5; keep this test to lock the visual hierarchy.

- [ ] **Step 3: Add CSS**

Append near the existing lab/backtest styles in `marketpulse/web/static/css/app.css`:

```css
/* ════════ Phase 6f: /lab/paper-trading operations UI ════════ */
.mp-paper-ops {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.mp-paper-ops__header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 24px;
  padding: 10px 4px 2px;
}
.mp-paper-ops__header h1 {
  margin: 4px 0 4px;
  font: 700 34px/1 var(--ns-font-headline);
  color: var(--ns-navy);
  letter-spacing: 0;
}
.mp-paper-ops__header p {
  margin: 0;
  color: var(--ns-on-surface-variant);
  font-size: 13px;
}
.mp-paper-ops__generated,
.mp-paper-muted {
  color: var(--ns-on-surface-variant);
  font-size: 12px;
}
.mp-paper-kpis {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
}
.mp-paper-kpi .mp-card__body {
  padding: 16px 18px;
}
.mp-paper-kpi__value {
  margin-top: 6px;
  font: 700 28px/1.05 var(--ns-font-headline);
  color: var(--ns-navy);
  letter-spacing: 0;
}
.mp-paper-primary-row {
  display: grid;
  grid-template-columns: minmax(320px, 0.8fr) minmax(640px, 1.2fr);
  gap: 16px;
  align-items: start;
}
.mp-paper-drilldown {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
}
.mp-paper-tabs {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.mp-paper-tab {
  border: 1px solid var(--ns-outline-variant);
  background: white;
  color: var(--ns-on-surface-variant);
  border-radius: 4px;
  padding: 8px 12px;
  font: 700 12px/1 var(--ns-font-headline);
  cursor: pointer;
}
.mp-paper-tab.is-active {
  background: var(--ns-navy);
  color: white;
  border-color: var(--ns-navy);
}
.mp-paper-tab-panel.is-hidden {
  display: none;
}
.mp-paper-table-wrap {
  overflow-x: auto;
}
.mp-table--paper {
  min-width: 920px;
  width: 100%;
  border-collapse: collapse;
}
.mp-table--paper th {
  font: 600 10px/1 var(--ns-font-headline);
  color: var(--ns-on-surface-variant);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  padding: 8px 10px;
}
.mp-table--paper td {
  padding: 10px;
  font-size: 12px;
}
.mp-paper-events,
.mp-paper-audit-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.mp-paper-events li,
.mp-paper-audit-list li {
  border: 1px solid var(--ns-outline-variant);
  background: var(--ns-surface-container-low);
  padding: 10px 12px;
  border-radius: 4px;
}
.mp-paper-events strong,
.mp-paper-audit-list strong {
  display: block;
  font: 700 12px/1.2 var(--ns-font-headline);
  color: var(--ns-navy);
}
.mp-paper-events span,
.mp-paper-audit-list span,
.mp-paper-events small,
.mp-paper-audit-list small {
  color: var(--ns-on-surface-variant);
  font-size: 12px;
}
.mp-paper-empty,
.mp-paper-degraded {
  padding: 14px 16px;
  border: 1px dashed var(--ns-outline-variant);
  border-radius: 4px;
  color: var(--ns-on-surface-variant);
  background: var(--ns-surface-container-low);
}
.mp-paper-degraded {
  border-color: #f59e0b;
  background: #fffbeb;
  color: #92400e;
}
.mp-paper-routine-toggle {
  border: 1px solid var(--ns-outline-variant);
  background: white;
  color: var(--ns-on-surface-variant);
  border-radius: 4px;
  padding: 8px 10px;
  font: 600 12px/1 var(--ns-font-headline);
  margin-bottom: 12px;
  cursor: pointer;
}
.mp-paper-routine-row.is-hidden {
  display: none;
}
@media (max-width: 1100px) {
  .mp-paper-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .mp-paper-primary-row { grid-template-columns: 1fr; }
}
```

- [ ] **Step 4: Run tests**

Run:

```bash
pytest tests/web/test_lab_paper_trading.py -q
```

Expected: all web tests pass.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/web/static/css/app.css tests/web/test_lab_paper_trading.py
git commit -m "feat(phase-6f): style paper trading ops console"
```

---

### Task 8: Architecture Guards, Full Verification, and Browser Smoke

**Files:**
- Modify: `tests/architecture/test_repository_boundary.py` only if the existing guard misses `query_models.py`
- No production code unless tests expose a defect

- [ ] **Step 1: Add or confirm read-side guard**

If the existing `test_repository_is_single_writer` already flags writes in `marketpulse/trading/query_models.py`, add this focused assertion for clarity:

```python
def test_paper_query_models_are_read_only():
    path = TRADING_ROOT / "query_models.py"
    tree = ast.parse(path.read_text())
    violations = [
        f"{path}:{lineno} {call}"
        for lineno, call in [
            *_find_session_mutation_calls(tree),
            *_find_insert_update_execute(tree),
        ]
    ]

    assert not violations, (
        "Phase 6f query_models.py is read-side only and must not mutate paper_* "
        "state. Violations:\n  " + "\n  ".join(violations)
    )
```

- [ ] **Step 2: Run focused suites**

Run:

```bash
pytest tests/trading/test_query_models.py tests/web/test_lab_paper_trading.py tests/architecture/test_repository_boundary.py -q
```

Expected: all pass.

- [ ] **Step 3: Run full pytest**

Run:

```bash
pytest
```

Expected: all pass.

- [ ] **Step 4: Run ruff**

Run:

```bash
ruff check .
```

Expected: all pass.

- [ ] **Step 5: Verify Alembic heads**

Run:

```bash
alembic heads
```

Expected: one head only. No migration is expected for 6f because it is read-only.

- [ ] **Step 6: Browser smoke**

Start the dev server if no server is already running:

```bash
uvicorn marketpulse.web.main:app --host 127.0.0.1 --port 8000
```

Use the in-app browser to open:

```text
http://127.0.0.1:8000/lab/paper-trading
```

Verify:

- Unauthenticated request redirects to `/login`.
- After login, the page displays `Paper Trading · Operations`.
- Health Summary appears first.
- Critical Events and Positions are above Orders & Fills and Audit Timeline.
- Fresh DB empty states render explicitly.
- No control-plane controls appear.

- [ ] **Step 7: Commit final guard fixes if any**

If Step 1 changed the architecture test:

```bash
git add tests/architecture/test_repository_boundary.py
git commit -m "test(phase-6f): guard paper query model read side"
```

If no files changed, skip the commit.

---

## Final Completion Checklist

- [ ] `pytest tests/trading/test_query_models.py tests/web/test_lab_paper_trading.py tests/architecture/test_repository_boundary.py -q`
- [ ] `pytest`
- [ ] `ruff check .`
- [ ] `alembic heads`
- [ ] Browser smoke on `/lab/paper-trading`
- [ ] `git status --short` contains only intentional untracked local artifacts, if any.

## Self-Review Notes

- Spec coverage: plan covers route, auth, read-only/no POST, COW, `>= started_at`, fresh DB Healthy, kill switch source, fail-soft degraded sections, empty states, positions overlay with historical `PRICE_UNAVAILABLE`, recovery collapse with close-timestamp historical `PRICE_UNAVAILABLE`, integer-cast attempt counts, shared projection context, order lifecycle aggregation, audit triage feed, client-side drill-down tabs, routine client reveal, nav, styling, no charts, and no mutation controls.
- Placeholder scan: no deferred implementation placeholders remain in task steps; explicit deferrals are product scope from the spec.
- Type consistency: DTO and helper names match across tasks: `PaperTradingDashboard`, `OperationalWindow`, `SectionResult`, `section_ok`, `section_error`, `HealthSummary`, `OperationalEvent`, `PositionRow`, `OrderLifecycleRow`, `AuditTimelineRow`, `AuditTimeline`, and `load_paper_trading_dashboard`.
