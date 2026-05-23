"""Pure audit projection helpers for Phase 6g notifications."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType


@dataclass(frozen=True)
class NotificationFailure:
    """Structured failure record for notification dispatch and CLI output."""

    event_type: str
    title: str
    error: str


@dataclass(frozen=True)
class CriticalEvent:
    """One critical audit row selected for a standalone push."""

    event_type: str
    audit_id: int
    timestamp: datetime
    strategy: str | None
    reason: str | None
    context: Mapping[str, object]


@dataclass(frozen=True)
class PlacedOrderDetail:
    """One placed-order detail for the routine tick summary."""

    ticker: str
    strategy: str
    quantity: int


@dataclass(frozen=True)
class TickSummary:
    """Aggregate of routine activity for the per-tick summary push."""

    tick_date: date
    cycle_status: str
    orders_placed: int
    orders_placed_detail: list[PlacedOrderDetail]
    orders_rejected: int
    orders_rejected_breakdown: list[tuple[str, str]]
    orders_cancelled: int
    duplicates_skipped: int
    entries_filled: list[tuple[str, Decimal]]
    positions_closed: list[tuple[str, Decimal, Decimal]]
    total_realized_pnl: Decimal
    cash_balance_end: Decimal
    active_positions_count: int
    active_positions_with_pu: list[tuple[str, int]]


@dataclass(frozen=True)
class _DedupFacts:
    kill_switch_cycle_skipped_in_period: bool
    positions_with_prior_pu: set[int]
    threshold: int


_ALWAYS_CRITICAL = frozenset(
    {
        "ENGINE_INVARIANT_ERROR",
        "SCHEDULER_GAP_DETECTED",
        "TICK_REPROCESSED_COMPLETED",
        "KILL_SWITCH_FLIPPED",
    },
)


def _freeze_context(raw: Mapping[str, object] | None) -> Mapping[str, object]:
    return MappingProxyType(dict(raw or {}))


def _is_daily_loss_reject(context: Mapping[str, object]) -> bool:
    gates = context.get("failed_gates")
    if not isinstance(gates, (list, tuple)):
        return False
    return "daily_loss" in gates


def _is_pu_threshold_attempt(
    context: Mapping[str, object],
    facts: _DedupFacts,
) -> bool:
    return context.get("attempt_count") == facts.threshold


def _is_position_recovered(
    context: Mapping[str, object],
    facts: _DedupFacts,
) -> bool:
    position_id = context.get("position_id")
    return isinstance(position_id, int) and position_id in facts.positions_with_prior_pu


def _is_first_kill_switch_skip(
    context: Mapping[str, object],
    facts: _DedupFacts,
) -> bool:
    del context
    return not facts.kill_switch_cycle_skipped_in_period


_CONDITIONAL_RULES: dict[str, Callable[[Mapping[str, object], _DedupFacts], bool]] = {
    "KILL_SWITCH_CYCLE_SKIPPED": _is_first_kill_switch_skip,
    "ORDER_REJECTED": lambda context, facts: _is_daily_loss_reject(context),
    "PRICE_UNAVAILABLE": _is_pu_threshold_attempt,
    "POSITION_CLOSED": _is_position_recovered,
}


def _check_pu_monotonic(
    new_audit_rows,
    failures: list[NotificationFailure],
    *,
    prior_attempts_by_position: dict[int, int],
) -> None:
    max_failures = 10
    appended = 0
    seen_max = dict(prior_attempts_by_position)
    for row in new_audit_rows:
        if row.event_type != "PRICE_UNAVAILABLE":
            continue
        context = row.context or {}
        position_id = context.get("position_id")
        attempt = context.get("attempt_count")
        if not isinstance(position_id, int) or not isinstance(attempt, int):
            continue
        prior = seen_max.get(position_id, 0)
        if attempt < prior and appended < max_failures:
            failures.append(
                NotificationFailure(
                    event_type="PRICE_UNAVAILABLE",
                    title=f"position_id={position_id}",
                    error=(
                        "monotonic_invariant_violation:"
                        f"attempt_count {prior}->{attempt} (lock 6g-L4c)"
                    ),
                )
            )
            appended += 1
        seen_max[position_id] = max(prior, attempt)
    if appended >= max_failures:
        failures.append(
            NotificationFailure(
                event_type="PRICE_UNAVAILABLE",
                title="invariant_failures_capped",
                error=(
                    f"more than {max_failures} monotonic violations in this tick "
                    "suppressed (lock 6g-L4c)"
                ),
            )
        )


def select_critical_events(
    *,
    new_audit_rows,
    kill_switch_cycle_skipped_in_period: bool,
    positions_with_prior_pu: set[int],
    threshold: int = 3,
    failures: list[NotificationFailure] | None = None,
    prior_attempts_by_position: dict[int, int] | None = None,
) -> list[CriticalEvent]:
    """Select audit rows that warrant standalone critical notification pushes."""
    rows = list(new_audit_rows)
    if failures is not None:
        _check_pu_monotonic(
            rows,
            failures,
            prior_attempts_by_position=prior_attempts_by_position or {},
        )

    facts = _DedupFacts(
        kill_switch_cycle_skipped_in_period=kill_switch_cycle_skipped_in_period,
        positions_with_prior_pu=positions_with_prior_pu,
        threshold=threshold,
    )
    out: list[CriticalEvent] = []
    for row in rows:
        context = row.context or {}
        event_type = row.event_type
        keep = event_type in _ALWAYS_CRITICAL
        if not keep:
            rule = _CONDITIONAL_RULES.get(event_type)
            keep = rule(context, facts) if rule is not None else False

        if keep:
            out.append(
                CriticalEvent(
                    event_type=event_type,
                    audit_id=row.id,
                    timestamp=row.timestamp,
                    strategy=row.strategy,
                    reason=row.reason if row.reason else None,
                    context=_freeze_context(context),
                )
            )
    return out


MAX_NUMERIC_FAILURES_PER_TICK = 10


def _safe_decimal(
    value,
    default: str = "0",
    *,
    field_name: str | None = None,
    failures: list[NotificationFailure] | None = None,
) -> Decimal:
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception as exc:
        if field_name is not None and failures is not None:
            numeric_failures = [
                failure
                for failure in failures
                if failure.error.startswith("malformed_numeric:")
            ]
            capped = any(
                failure.error == "malformed_numeric_capped"
                for failure in failures
            )
            if len(numeric_failures) < MAX_NUMERIC_FAILURES_PER_TICK:
                failures.append(
                    NotificationFailure(
                        event_type="tick_summary",
                        title="",
                        error=f"malformed_numeric:{field_name}:{type(exc).__name__}",
                    )
                )
            elif not capped:
                failures.append(
                    NotificationFailure(
                        event_type="tick_summary",
                        title="",
                        error="malformed_numeric_capped",
                    )
                )
        return Decimal(default)


def _first_failed_gate(context: Mapping[str, object]) -> str:
    gates = context.get("failed_gates")
    if isinstance(gates, (list, tuple)) and gates:
        return str(gates[0])
    return "unknown"


def _order_request_field(
    context: Mapping[str, object],
    key: str,
    default: object = "?",
) -> object:
    order_request = context.get("order_request")
    if isinstance(order_request, Mapping):
        return order_request.get(key, default)
    return default


def _context_field(
    context: Mapping[str, object],
    key: str,
    default: object = "?",
) -> object:
    value = context.get(key)
    if value is not None:
        return value
    return _order_request_field(context, key, default)


def _resolve_cycle_status(
    rows,
    tick_date: date,
) -> tuple[str, tuple[NotificationFailure, ...]]:
    iso_date = tick_date.isoformat()
    for row in rows:
        context = row.context or {}
        if row.event_type == "TICK_COMPLETED" and context.get("tick_date") == iso_date:
            return str(context.get("status", "completed")), ()

    for row in rows:
        context = row.context or {}
        if (
            row.event_type == "KILL_SWITCH_CYCLE_SKIPPED"
            and context.get("tick_date") == iso_date
        ):
            return str(context.get("status", "skipped")), ()

    return (
        "unknown",
        (
            NotificationFailure(
                event_type="tick_summary",
                title="",
                error="missing_tick_completed_row",
            ),
        ),
    )


def summarize_tick(
    *,
    new_audit_rows,
    tick_date: date,
    cash_balance_end: Decimal,
    active_positions_with_pu_attempts: list[tuple[str, int]],
    active_positions_count: int,
) -> tuple[TickSummary, tuple[NotificationFailure, ...]]:
    """Build the routine tick summary from audit rows plus canonical state.

    Lock 6g-L21: cycle status comes from TICK_COMPLETED, then
    KILL_SWITCH_CYCLE_SKIPPED, then "unknown" with a failure record. Cash and
    active-position fields are supplied by the caller from canonical tables.
    """
    rows = list(new_audit_rows)
    cycle_status, cycle_failures = _resolve_cycle_status(rows, tick_date)
    failures: list[NotificationFailure] = list(cycle_failures)

    orders_placed_detail: list[PlacedOrderDetail] = []
    orders_rejected_breakdown: list[tuple[str, str]] = []
    entries_filled: list[tuple[str, Decimal]] = []
    positions_closed: list[tuple[str, Decimal, Decimal]] = []
    orders_cancelled = 0
    duplicates_skipped = 0
    total_realized_pnl = Decimal("0")

    for row in rows:
        context = row.context or {}
        if row.event_type == "ORDER_PLACED":
            orders_placed_detail.append(
                PlacedOrderDetail(
                    ticker=str(_context_field(context, "ticker")),
                    strategy=str(
                        row.strategy or _context_field(context, "strategy")
                    ),
                    quantity=int(_context_field(context, "quantity", 0) or 0),
                )
            )
        elif row.event_type == "ORDER_REJECTED":
            orders_rejected_breakdown.append(
                (
                    str(_context_field(context, "ticker")),
                    _first_failed_gate(context),
                )
            )
        elif row.event_type == "ORDER_CANCELLED":
            orders_cancelled += 1
        elif row.event_type == "ORDER_PLACED_DUPLICATE":
            duplicates_skipped += 1
        elif row.event_type == "ORDER_ENTRY_FILLED":
            entries_filled.append(
                (
                    str(context.get("ticker", "?")),
                    _safe_decimal(
                        context.get("fill_price"),
                        field_name="fill_price",
                        failures=failures,
                    ),
                )
            )
        elif row.event_type == "POSITION_CLOSED":
            realized_pnl = _safe_decimal(
                context.get("realized_pnl"),
                field_name="realized_pnl",
                failures=failures,
            )
            positions_closed.append(
                (
                    str(context.get("ticker", "?")),
                    _safe_decimal(
                        context.get("exit_price"),
                        field_name="exit_price",
                        failures=failures,
                    ),
                    realized_pnl,
                )
            )
            total_realized_pnl += realized_pnl

    summary = TickSummary(
        tick_date=tick_date,
        cycle_status=cycle_status,
        orders_placed=len(orders_placed_detail),
        orders_placed_detail=orders_placed_detail,
        orders_rejected=len(orders_rejected_breakdown),
        orders_rejected_breakdown=orders_rejected_breakdown,
        orders_cancelled=orders_cancelled,
        duplicates_skipped=duplicates_skipped,
        entries_filled=entries_filled,
        positions_closed=positions_closed,
        total_realized_pnl=total_realized_pnl,
        cash_balance_end=cash_balance_end,
        active_positions_count=active_positions_count,
        active_positions_with_pu=list(active_positions_with_pu_attempts),
    )
    return summary, tuple(failures)
