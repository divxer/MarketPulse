"""Post-tick paper-trading notification dispatcher for Phase 6g."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from marketpulse.alerts.notifier import Notifier
from marketpulse.config import get_settings
from marketpulse.db.models import PaperAuditEvent, PaperOrder, PaperPosition
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


@dataclass(frozen=True)
class _ProjectionAuditRow:
    """Read-side audit row enriched for observability rendering only."""

    id: int
    timestamp: datetime
    event_type: str
    order_id: int | None
    strategy: str | None
    reason: str
    context: dict


def _is_enabled() -> bool:
    return get_settings().paper_notifications_enabled


def _record_query_failure(
    failures: list[NotificationFailure],
    *,
    event_type: str,
    title: str,
    exc: Exception,
) -> None:
    failures.append(
        NotificationFailure(
            event_type=event_type,
            title=title,
            error=f"query_error:{type(exc).__name__}:{exc}",
        )
    )


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


def _context_int(context: Mapping[str, object], key: str) -> int | None:
    value = context.get(key)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _enrich_order_request(context: dict) -> None:
    order_request = context.get("order_request")
    if not isinstance(order_request, Mapping):
        return
    for key in ("ticker", "strategy", "quantity"):
        value = order_request.get(key)
        if value is not None:
            context.setdefault(key, value)


def _enrich_rows(
    repository: Repository,
    rows: list[PaperAuditEvent],
    failures: list[NotificationFailure],
) -> list[_ProjectionAuditRow]:
    session = repository._session  # noqa: SLF001 - read-side projection.
    position_ids = sorted(
        {
            position_id
            for row in rows
            if (position_id := _context_int(row.context or {}, "position_id"))
            is not None
        }
    )
    positions_by_id: dict[int, PaperPosition] = {}
    if position_ids:
        positions = session.execute(
            select(PaperPosition).where(PaperPosition.id.in_(position_ids))
        ).scalars().all()
        positions_by_id = {position.id: position for position in positions}

    order_ids = {
        int(row.order_id)
        for row in rows
        if row.order_id is not None
    }
    order_ids.update(position.order_id for position in positions_by_id.values())
    orders_by_id: dict[int, PaperOrder] = {}
    if order_ids:
        orders = session.execute(
            select(PaperOrder).where(PaperOrder.id.in_(sorted(order_ids)))
        ).scalars().all()
        orders_by_id = {order.id: order for order in orders}

    enriched_rows: list[_ProjectionAuditRow] = []
    for row in rows:
        context = dict(row.context or {})
        _enrich_order_request(context)

        order = orders_by_id.get(row.order_id) if row.order_id is not None else None
        if order is not None:
            context.setdefault("ticker", order.ticker)
            context.setdefault("quantity", order.quantity)
            context.setdefault("strategy", order.strategy)
            context.setdefault("horizon_date", order.horizon_date.isoformat())

        position_id = _context_int(context, "position_id")
        position = positions_by_id.get(position_id) if position_id is not None else None
        if position is not None:
            context.setdefault("ticker", position.ticker)
            context.setdefault("quantity", position.quantity)
            context.setdefault("strategy", position.strategy)
            context.setdefault("horizon_date", position.horizon_date.isoformat())

        if row.event_type == "POSITION_CLOSED" and position_id is not None:
            try:
                prior_attempts = repository.latest_price_unavailable_attempt_counts(
                    position_ids=[position_id],
                    before=row.timestamp,
                )
            except Exception as exc:
                _record_query_failure(
                    failures,
                    event_type="POSITION_CLOSED",
                    title="latest_price_unavailable_attempt_counts",
                    exc=exc,
                )
                log.warning(
                    "paper_tick_notify_recovery_attempts_query_failed",
                    error=str(exc),
                )
            else:
                attempt_count = prior_attempts.get(position_id)
                if attempt_count is not None:
                    context.setdefault("retry_count", attempt_count)

        strategy = row.strategy
        context_strategy = context.get("strategy")
        if strategy is None and context_strategy is not None:
            strategy = str(context_strategy)

        enriched_rows.append(
            _ProjectionAuditRow(
                id=row.id,
                timestamp=row.timestamp,
                event_type=row.event_type,
                order_id=row.order_id,
                strategy=strategy,
                reason=row.reason,
                context=context,
            )
        )
    return enriched_rows


def _dedup_before_for_kscs(
    rows: list[_ProjectionAuditRow],
    fallback: datetime,
) -> datetime:
    skipped_rows = [
        row for row in rows if row.event_type == "KILL_SWITCH_CYCLE_SKIPPED"
    ]
    if not skipped_rows:
        return fallback
    return min(row.timestamp for row in skipped_rows)


def _positions_with_prior_pu(
    repository: Repository,
    rows: list[_ProjectionAuditRow],
) -> set[int]:
    out: set[int] = set()
    for row in rows:
        if row.event_type != "POSITION_CLOSED":
            continue
        position_id = _context_int(row.context or {}, "position_id")
        if position_id is None:
            continue
        out.update(
            repository.positions_with_prior_price_unavailable(
                position_ids=[position_id],
                before=row.timestamp,
            )
        )
    return out


def _prior_pu_attempts(
    repository: Repository,
    rows: list[_ProjectionAuditRow],
    *,
    before: datetime,
) -> dict[int, int]:
    position_ids = sorted(
        {
            position_id
            for row in rows
            if row.event_type == "PRICE_UNAVAILABLE"
            and (position_id := _context_int(row.context or {}, "position_id"))
            is not None
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
    *,
    before: datetime,
) -> tuple[int, list[tuple[str, int]]]:
    session = repository._session  # noqa: SLF001 - read-side projection.
    positions = session.execute(
        select(PaperPosition)
        .where(PaperPosition.status == "OPEN")
        .order_by(PaperPosition.id)
    ).scalars().all()
    position_ids = [position.id for position in positions]
    attempts_by_position = repository.latest_price_unavailable_attempt_counts(
        position_ids=position_ids,
        before=before,
    )
    active_with_pu: list[tuple[str, int]] = []
    for position in positions:
        attempts = attempts_by_position.get(position.id, 0)
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
    *,
    notifier_kind: str | None,
) -> tuple[str, str] | None:
    try:
        return render_tick_summary(summary, notifier_kind=notifier_kind)
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
    until: datetime | None = None,
) -> NotificationResult:
    """Dispatch critical paper audit pushes plus one routine tick summary."""
    failures: list[NotificationFailure] = []
    settings = get_settings()
    if not settings.paper_notifications_enabled:
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

    notify_started_at = until or clock.now()
    try:
        latest_tick_completed_at = repository.latest_tick_completed_timestamp(
            before=since,
        )
    except Exception as exc:
        latest_tick_completed_at = None
        _record_query_failure(
            failures,
            event_type="audit_query",
            title="latest_tick_completed_timestamp",
            exc=exc,
        )
        log.warning("paper_tick_notify_latest_tick_query_failed", error=str(exc))

    try:
        raw_rows = _query_window_rows(
            repository,
            since=since,
            until=notify_started_at,
            tick_date=tick_date,
            latest_tick_completed_at=latest_tick_completed_at,
        )
        rows = _enrich_rows(repository, raw_rows, failures)
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
        _record_query_failure(
            failures,
            event_type="KILL_SWITCH_CYCLE_SKIPPED",
            title="kill_switch_cycle_skipped_in_active_period",
            exc=exc,
        )
        log.warning("paper_tick_notify_kscs_query_failed", error=str(exc))

    try:
        positions_with_prior_pu = _positions_with_prior_pu(
            repository,
            rows,
        )
    except Exception as exc:
        positions_with_prior_pu = set()
        _record_query_failure(
            failures,
            event_type="POSITION_CLOSED",
            title="positions_with_prior_price_unavailable",
            exc=exc,
        )
        log.warning("paper_tick_notify_prior_pu_query_failed", error=str(exc))

    try:
        prior_attempts_by_position = _prior_pu_attempts(
            repository,
            rows,
            before=since,
        )
    except Exception as exc:
        prior_attempts_by_position = {}
        _record_query_failure(
            failures,
            event_type="PRICE_UNAVAILABLE",
            title="latest_price_unavailable_attempt_counts",
            exc=exc,
        )
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
        _record_query_failure(
            failures,
            event_type="tick_summary",
            title="cash_balance",
            exc=exc,
        )
        log.warning("paper_tick_notify_cash_query_failed", error=str(exc))

    try:
        active_count, active_with_pu = _active_positions(
            repository,
            before=notify_started_at + timedelta(microseconds=1),
        )
    except Exception as exc:
        active_count, active_with_pu = 0, []
        _record_query_failure(
            failures,
            event_type="tick_summary",
            title="active_positions",
            exc=exc,
        )
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
    rendered_summary = _safe_render_summary(
        summary,
        failures,
        notifier_kind=settings.notifier_kind,
    )
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
