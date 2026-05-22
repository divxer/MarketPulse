"""Post-tick paper-trading notification dispatcher for Phase 6g."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

from marketpulse.alerts.notifier import Notifier
from marketpulse.config import get_settings
from marketpulse.db.models import PaperAuditEvent, PaperPosition
from marketpulse.logging import get_logger
from marketpulse.observability.audit_projection import (
    CriticalEvent,
    NotificationFailure,
    TickSummary,
    select_critical_events,
    summarize_tick,
)
from marketpulse.observability.templates import (
    render_critical_event,
    render_tick_summary,
)
from marketpulse.trading.clock import Clock
from marketpulse.trading.repository import Repository

log = get_logger(__name__)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_REQUIRES_TICK_DATE = frozenset(
    {
        "TICK_COMPLETED",
        "TICK_REPROCESSED_COMPLETED",
        "KILL_SWITCH_CYCLE_SKIPPED",
    }
)


@dataclass(frozen=True)
class CriticalPush:
    """One critical push that was successfully handed to the notifier."""

    event_type: str
    audit_id: int
    title: str


@dataclass(frozen=True)
class NotificationResult:
    """Testable result from one post-tick notification pass."""

    critical_sent: tuple[CriticalPush, ...]
    summary_sent: bool
    failures: tuple[NotificationFailure, ...]
    summary_title: str | None = None
    summary_body: str | None = None


def _is_enabled() -> bool:
    return get_settings().paper_notifications_enabled


def _query_window_rows(
    repository: Repository,
    *,
    since: datetime,
    until: datetime,
    tick_date: date,
    latest_tick_completed_at: datetime | None,
) -> list[PaperAuditEvent]:
    session = repository._session  # noqa: SLF001 - read-side projection.
    narrow_rows = session.execute(
        select(PaperAuditEvent)
        .where(PaperAuditEvent.timestamp >= since)
        .where(PaperAuditEvent.timestamp <= until)
        .order_by(PaperAuditEvent.id)
    ).scalars().all()

    extended_lower_bound = latest_tick_completed_at or _EPOCH
    extended_flips = session.execute(
        select(PaperAuditEvent)
        .where(PaperAuditEvent.event_type == "KILL_SWITCH_FLIPPED")
        .where(PaperAuditEvent.timestamp >= extended_lower_bound)
        .where(PaperAuditEvent.timestamp < since)
        .order_by(PaperAuditEvent.id)
    ).scalars().all()

    iso_tick_date = tick_date.isoformat()
    filtered: list[PaperAuditEvent] = []
    for row in sorted([*extended_flips, *narrow_rows], key=lambda audit: audit.id):
        if (
            row.event_type in _REQUIRES_TICK_DATE
            and (row.context or {}).get("tick_date") != iso_tick_date
        ):
            continue
        filtered.append(row)
    return filtered


def _dedup_before_for_kscs(rows: list[PaperAuditEvent], fallback: datetime) -> datetime:
    skipped_rows = [
        row for row in rows if row.event_type == "KILL_SWITCH_CYCLE_SKIPPED"
    ]
    if not skipped_rows:
        return fallback
    return min(row.timestamp for row in skipped_rows)


def _prior_pu_before_for_closed_positions(
    rows: list[PaperAuditEvent],
    fallback: datetime,
) -> datetime:
    closed_rows = [row for row in rows if row.event_type == "POSITION_CLOSED"]
    if not closed_rows:
        return fallback
    return min(row.timestamp for row in closed_rows)


def _positions_with_prior_pu(
    repository: Repository,
    rows: list[PaperAuditEvent],
    *,
    before: datetime,
) -> set[int]:
    position_ids = sorted(
        {
            int((row.context or {}).get("position_id"))
            for row in rows
            if row.event_type == "POSITION_CLOSED"
            and isinstance((row.context or {}).get("position_id"), int)
        }
    )
    if not position_ids:
        return set()
    return repository.positions_with_prior_price_unavailable(
        position_ids=position_ids,
        before=before,
    )


def _prior_pu_attempts(
    repository: Repository,
    rows: list[PaperAuditEvent],
    *,
    before: datetime,
) -> dict[int, int]:
    position_ids = sorted(
        {
            int((row.context or {}).get("position_id"))
            for row in rows
            if row.event_type == "PRICE_UNAVAILABLE"
            and isinstance((row.context or {}).get("position_id"), int)
        }
    )
    if not position_ids:
        return {}
    return repository.latest_price_unavailable_attempt_counts(
        position_ids=position_ids,
        before=before,
    )


def _active_positions(
    repository: Repository,
) -> tuple[int, list[tuple[str, int]]]:
    session = repository._session  # noqa: SLF001 - read-side projection.
    positions = session.execute(
        select(PaperPosition)
        .where(PaperPosition.status == "OPEN")
        .order_by(PaperPosition.id)
    ).scalars().all()
    active_with_pu: list[tuple[str, int]] = []
    for position in positions:
        attempts = repository.count_price_unavailable_attempts(position_id=position.id)
        if attempts > 0:
            active_with_pu.append((position.ticker, int(attempts)))
    return len(positions), active_with_pu


def _safe_send(
    notifier: Notifier,
    *,
    title: str,
    body: str,
    event_type: str,
    failures: list[NotificationFailure],
) -> bool:
    try:
        ok = notifier.send(title, body, None)
    except Exception as exc:
        failures.append(
            NotificationFailure(
                event_type=event_type,
                title=title,
                error=f"send_raised:{type(exc).__name__}",
            )
        )
        log.warning("paper_tick_notify_send_raised", event_type=event_type)
        return False
    if not ok:
        failures.append(
            NotificationFailure(
                event_type=event_type,
                title=title,
                error="send_returned_false",
            )
        )
        return False
    return True


def _safe_render_critical(
    event: CriticalEvent,
    failures: list[NotificationFailure],
) -> tuple[str, str] | None:
    try:
        return render_critical_event(event)
    except Exception as exc:
        failures.append(
            NotificationFailure(
                event_type=event.event_type,
                title="",
                error=f"template_error:{type(exc).__name__}:{exc}",
            )
        )
        log.warning(
            "paper_tick_notify_critical_template_failed",
            event_type=event.event_type,
            audit_id=event.audit_id,
        )
        return None


def _safe_render_summary(
    summary: TickSummary,
    failures: list[NotificationFailure],
) -> tuple[str, str] | None:
    try:
        return render_tick_summary(summary)
    except Exception as exc:
        failures.append(
            NotificationFailure(
                event_type="tick_summary",
                title="",
                error=f"template_error:{type(exc).__name__}:{exc}",
            )
        )
        log.warning("paper_tick_notify_summary_template_failed")
        return None


def notify_paper_tick_events(
    *,
    since: datetime,
    tick_date: date,
    repository: Repository,
    notifier: Notifier,
    clock: Clock,
    price_unavailable_threshold: int = 3,
) -> NotificationResult:
    """Dispatch critical paper audit pushes plus one routine tick summary."""
    failures: list[NotificationFailure] = []
    if not _is_enabled():
        failures.append(
            NotificationFailure(
                event_type="config",
                title="",
                error="disabled_by_config",
            )
        )
        return NotificationResult(
            critical_sent=(),
            summary_sent=False,
            failures=tuple(failures),
        )

    notify_started_at = clock.now()
    try:
        latest_tick_completed_at = repository.latest_tick_completed_timestamp(
            before=since,
        )
    except Exception as exc:
        latest_tick_completed_at = None
        log.warning("paper_tick_notify_latest_tick_query_failed", error=str(exc))

    try:
        rows = _query_window_rows(
            repository,
            since=since,
            until=notify_started_at,
            tick_date=tick_date,
            latest_tick_completed_at=latest_tick_completed_at,
        )
    except Exception as exc:
        failures.append(
            NotificationFailure(
                event_type="audit_query",
                title="",
                error=f"query_error:{type(exc).__name__}:{exc}",
            )
        )
        rows = []

    try:
        kscs_in_period = repository.kill_switch_cycle_skipped_in_active_period(
            before=_dedup_before_for_kscs(rows, notify_started_at),
        )
    except Exception as exc:
        kscs_in_period = False
        log.warning("paper_tick_notify_kscs_query_failed", error=str(exc))

    try:
        positions_with_prior_pu = _positions_with_prior_pu(
            repository,
            rows,
            before=_prior_pu_before_for_closed_positions(rows, notify_started_at),
        )
    except Exception as exc:
        positions_with_prior_pu = set()
        log.warning("paper_tick_notify_prior_pu_query_failed", error=str(exc))

    try:
        prior_attempts_by_position = _prior_pu_attempts(
            repository,
            rows,
            before=since,
        )
    except Exception as exc:
        prior_attempts_by_position = {}
        log.warning("paper_tick_notify_prior_attempts_query_failed", error=str(exc))

    try:
        critical_events = select_critical_events(
            new_audit_rows=rows,
            kill_switch_cycle_skipped_in_period=kscs_in_period,
            positions_with_prior_pu=positions_with_prior_pu,
            threshold=price_unavailable_threshold,
            failures=failures,
            prior_attempts_by_position=prior_attempts_by_position,
        )
    except Exception as exc:
        failures.append(
            NotificationFailure(
                event_type="projection",
                title="",
                error=f"projection_error:{type(exc).__name__}:{exc}",
            )
        )
        critical_events = []

    critical_sent: list[CriticalPush] = []
    for event in critical_events:
        rendered = _safe_render_critical(event, failures)
        if rendered is None:
            continue
        title, body = rendered
        if _safe_send(
            notifier,
            title=title,
            body=body,
            event_type=event.event_type,
            failures=failures,
        ):
            critical_sent.append(
                CriticalPush(
                    event_type=event.event_type,
                    audit_id=event.audit_id,
                    title=title,
                )
            )

    try:
        cash_balance_end = repository.cash_balance()
    except Exception as exc:
        cash_balance_end = Decimal("0")
        log.warning("paper_tick_notify_cash_query_failed", error=str(exc))

    try:
        active_count, active_with_pu = _active_positions(repository)
    except Exception as exc:
        active_count, active_with_pu = 0, []
        log.warning("paper_tick_notify_active_positions_failed", error=str(exc))

    try:
        summary, summary_failures = summarize_tick(
            new_audit_rows=rows,
            tick_date=tick_date,
            cash_balance_end=cash_balance_end,
            active_positions_with_pu_attempts=active_with_pu,
            active_positions_count=active_count,
        )
        failures.extend(summary_failures)
    except Exception as exc:
        failures.append(
            NotificationFailure(
                event_type="tick_summary",
                title="",
                error=f"projection_error:{type(exc).__name__}:{exc}",
            )
        )
        return NotificationResult(
            critical_sent=tuple(critical_sent),
            summary_sent=False,
            failures=tuple(failures),
        )

    summary_title: str | None = None
    summary_body: str | None = None
    summary_sent = False
    rendered_summary = _safe_render_summary(summary, failures)
    if rendered_summary is not None:
        summary_title, summary_body = rendered_summary
        summary_sent = _safe_send(
            notifier,
            title=summary_title,
            body=summary_body,
            event_type="tick_summary",
            failures=failures,
        )

    return NotificationResult(
        critical_sent=tuple(critical_sent),
        summary_sent=summary_sent,
        failures=tuple(failures),
        summary_title=summary_title,
        summary_body=summary_body,
    )
