"""Pure notification renderers for Phase 6g paper-trading observability."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from marketpulse.observability.audit_projection import CriticalEvent, TickSummary
from marketpulse.recap.push import _BODY_LIMITS, _truncate

_NY = ZoneInfo("America/New_York")
_PU_CAP = 4


def _money_signed(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.01"))
    sign = "-" if quantized < 0 else "+"
    return f"{sign}${abs(quantized):.2f}"


def _money_plain(value: Decimal) -> str:
    return f"${value.quantize(Decimal('0.01')):,.2f}"


def _price(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):.2f}"


def _hhmm_ny(timestamp: datetime) -> str:
    return timestamp.astimezone(_NY).strftime("%H:%M NY")


def _render_kill_switch_flipped(event: CriticalEvent) -> tuple[str, str]:
    to_state = bool(event.context.get("to_state"))
    title = "🛑 Kill Switch FLIPPED" if to_state else "✅ Kill Switch CLEARED"
    reason = event.reason or event.context.get("reason", "")
    body = f"Reason: {reason}\nTime:   {_hhmm_ny(event.timestamp)}"
    return title, body


def _render_kill_switch_cycle_skipped(event: CriticalEvent) -> tuple[str, str]:
    tick_date = event.context.get("tick_date", "?")
    reason = event.context.get("reason", event.reason or "")
    body = f"Date:   {tick_date}\nReason: {reason}"
    return "🛑 Kill Switch — Cycle Skipped", body


def _render_engine_invariant_error(event: CriticalEvent) -> tuple[str, str]:
    lines = [
        f"Phase:  {event.context.get('phase', '?')}",
        f"Error:  {event.context.get('error', event.reason or '?')}",
    ]
    position_id = event.context.get("position_id")
    order_id = event.context.get("order_id")
    if position_id is not None:
        lines.append(f"Position: {position_id}")
    if order_id is not None:
        lines.append(f"Order:    {order_id}")
    return "🛑 Engine Invariant Error", "\n".join(lines)


def _render_scheduler_gap(event: CriticalEvent) -> tuple[str, str]:
    last_tick = event.context.get(
        "last_tick_date",
        event.context.get("last_processed_tick_date", "?"),
    )
    gap_days = event.context.get(
        "gap_days",
        event.context.get("missed_business_days", "?"),
    )
    body = f"Last tick: {last_tick}\nMissing:   {gap_days} trading day(s)"
    return "🛑 Scheduler Gap Detected", body


def _render_tick_reprocessed(event: CriticalEvent) -> tuple[str, str]:
    tick_date = event.context.get("tick_date", "?")
    return "⚠️ Tick Reprocessed", f"Date: {tick_date}\nOriginal run superseded"


def _order_request_field(
    context: Mapping[str, object],
    key: str,
    default: object = "?",
) -> object:
    order_request = context.get("order_request")
    if isinstance(order_request, Mapping):
        return order_request.get(key, default)
    return default


def _daily_loss_value(context: Mapping[str, object]) -> object:
    if "loss_today" in context:
        return context["loss_today"]

    per_gate = context.get("per_gate")
    if not isinstance(per_gate, (list, tuple)):
        return "0"
    for gate_result in per_gate:
        if not isinstance(gate_result, Mapping):
            continue
        if gate_result.get("gate_name") != "daily_loss":
            continue
        gate_context = gate_result.get("context")
        if isinstance(gate_context, Mapping):
            return gate_context.get("today_realized_pnl", "0")
    return "0"


def _render_daily_loss_reject(event: CriticalEvent) -> tuple[str, str]:
    ticker = event.context.get("ticker", _order_request_field(event.context, "ticker"))
    strategy = (
        event.strategy
        or event.context.get("strategy")
        or _order_request_field(event.context, "strategy")
    )
    quantity = event.context.get(
        "quantity",
        _order_request_field(event.context, "quantity"),
    )
    loss_raw = _daily_loss_value(event.context)
    try:
        loss = _money_signed(Decimal(str(loss_raw)))
    except Exception:
        loss = str(loss_raw)
    gates = ", ".join(event.context.get("failed_gates", []) or ["daily_loss"])
    body = (
        f"Order: {ticker} {strategy} × {quantity}\n"
        f"Loss today: {loss}\n"
        f"Failed gates: {gates}"
    )
    return "🛑 Daily Loss Limit Tripped", body


def _render_price_unavailable_stuck(event: CriticalEvent) -> tuple[str, str]:
    ticker = event.context.get("ticker", "?")
    strategy = event.strategy or event.context.get("strategy", "?")
    horizon = event.context.get("horizon_date", "?")
    attempts = event.context.get("attempt_count", "?")
    source = event.context.get("source", "?")
    body = (
        f"Strategy: {strategy}\n"
        f"Horizon:  {horizon}\n"
        f"{attempts} retries failed\n"
        f"Source:   {source}"
    )
    return f"⚠️ Position Stuck — {ticker}", body


def _render_position_recovered(event: CriticalEvent) -> tuple[str, str]:
    ticker = event.context.get("ticker", "?")
    retries = event.context.get("retry_count", event.context.get("attempt_count", "?"))
    try:
        exit_price = _price(Decimal(str(event.context.get("exit_price", "0"))))
    except Exception:
        exit_price = str(event.context.get("exit_price", "?"))
    try:
        pnl = _money_signed(Decimal(str(event.context.get("realized_pnl", "0"))))
    except Exception:
        pnl = str(event.context.get("realized_pnl", "?"))
    body = (
        f"Closed after {retries} retries\n"
        f"Exit @ {exit_price}\n"
        f"Realized P&L: {pnl}"
    )
    return f"✅ Position Recovered — {ticker}", body


def render_critical_event(event: CriticalEvent) -> tuple[str, str]:
    """Render one selected critical event to a notifier title/body pair."""
    if event.event_type == "KILL_SWITCH_FLIPPED":
        return _render_kill_switch_flipped(event)
    if event.event_type == "KILL_SWITCH_CYCLE_SKIPPED":
        return _render_kill_switch_cycle_skipped(event)
    if event.event_type == "ENGINE_INVARIANT_ERROR":
        return _render_engine_invariant_error(event)
    if event.event_type == "SCHEDULER_GAP_DETECTED":
        return _render_scheduler_gap(event)
    if event.event_type == "TICK_REPROCESSED_COMPLETED":
        return _render_tick_reprocessed(event)
    if event.event_type == "ORDER_REJECTED":
        return _render_daily_loss_reject(event)
    if event.event_type == "PRICE_UNAVAILABLE":
        return _render_price_unavailable_stuck(event)
    if event.event_type == "POSITION_CLOSED":
        return _render_position_recovered(event)
    raise ValueError(f"unsupported critical event type: {event.event_type!r}")


def _format_pu_attempt(attempt: int) -> str:
    if attempt >= _PU_CAP:
        return "4+"
    return f"{attempt}/3"


def render_tick_summary(
    summary: TickSummary,
    *,
    notifier_kind: str | None = None,
) -> tuple[str, str]:
    """Render the routine per-tick heartbeat summary."""
    title = f"📊 Paper Tick {summary.tick_date.isoformat()}"
    lines: list[str] = []

    lines.append(
        f"订单：{summary.orders_placed} placed, {summary.orders_rejected} rejected"
    )
    for detail in summary.orders_placed_detail:
        lines.append(f"  {detail.ticker} × {detail.quantity} ({detail.strategy})")
    for ticker, gate in summary.orders_rejected_breakdown:
        lines.append(f"  ❌ {ticker} ({gate})")
    extras = []
    if summary.orders_cancelled:
        extras.append(f"{summary.orders_cancelled} cancelled")
    if summary.duplicates_skipped:
        extras.append(f"{summary.duplicates_skipped} duplicates")
    if extras:
        lines.append("  (" + ", ".join(extras) + ")")
    lines.append("")

    entry_count = len(summary.entries_filled)
    exit_count = len(summary.positions_closed)
    exit_word = "exit" if exit_count == 1 else "exits"
    lines.append(f"成交：{entry_count} entries, {exit_count} {exit_word}")
    if summary.entries_filled:
        entries = [
            f"{ticker} @ {_price(fill_price)}"
            for ticker, fill_price in summary.entries_filled
        ]
        lines.append("  ENTRY: " + ", ".join(entries))
    for ticker, exit_price, pnl in summary.positions_closed:
        lines.append(f"  EXIT:  {ticker} @ {_price(exit_price)}, P&L {_money_signed(pnl)}")
    lines.append("")

    lines.append(f"今日 P&L：{_money_signed(summary.total_realized_pnl)} (realized)")
    lines.append(f"现金：{_money_plain(summary.cash_balance_end)}")
    active_line = f"活跃持仓：{summary.active_positions_count}"
    if summary.active_positions_with_pu:
        pu_parts = [
            f"{ticker} attempt {_format_pu_attempt(attempt)}"
            for ticker, attempt in summary.active_positions_with_pu
        ]
        active_line += (
            f" ({len(summary.active_positions_with_pu)} with PRICE_UNAVAILABLE "
            + ", ".join(pu_parts)
            + ")"
        )
    lines.append(active_line)
    lines.append("")
    lines.append(f"Status: {summary.cycle_status}")
    body = "\n".join(lines)
    limit = _BODY_LIMITS.get((notifier_kind or "").lower())
    return title, _truncate(body, limit)
