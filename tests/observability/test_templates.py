# Layer: pure
"""6g-T5: notification title/body renderers."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal


def _ev(event_type, **kwargs):
    from marketpulse.observability.audit_projection import CriticalEvent

    defaults = {
        "audit_id": 1,
        "timestamp": datetime(2026, 5, 22, 21, 30, tzinfo=UTC),
        "strategy": None,
        "reason": "",
        "context": {},
    }
    defaults.update(kwargs)
    return CriticalEvent(event_type=event_type, **defaults)


def _summary(**overrides):
    from marketpulse.observability.audit_projection import TickSummary

    defaults = {
        "tick_date": date(2026, 5, 22),
        "cycle_status": "completed",
        "orders_placed": 0,
        "orders_placed_detail": [],
        "orders_rejected": 0,
        "orders_rejected_breakdown": [],
        "orders_cancelled": 0,
        "duplicates_skipped": 0,
        "entries_filled": [],
        "positions_closed": [],
        "total_realized_pnl": Decimal("0"),
        "cash_balance_end": Decimal("10000.00"),
        "active_positions_count": 0,
        "active_positions_with_pu": [],
    }
    defaults.update(overrides)
    return TickSummary(**defaults)


def test_render_kill_switch_flipped_active_true():
    from marketpulse.observability.templates import render_critical_event

    event = _ev(
        "KILL_SWITCH_FLIPPED",
        reason="max_drawdown_exceeded",
        context={"to_state": True},
    )

    title, body = render_critical_event(event)

    assert title == "🛑 Kill Switch FLIPPED"
    assert "max_drawdown_exceeded" in body
    assert "Time:" in body


def test_render_kill_switch_flipped_active_false():
    from marketpulse.observability.templates import render_critical_event

    event = _ev(
        "KILL_SWITCH_FLIPPED",
        reason="manual_reset",
        context={"to_state": False},
    )

    title, body = render_critical_event(event)

    assert title == "✅ Kill Switch CLEARED"
    assert "manual_reset" in body


def test_render_kill_switch_cycle_skipped():
    from marketpulse.observability.templates import render_critical_event

    event = _ev(
        "KILL_SWITCH_CYCLE_SKIPPED",
        context={"tick_date": "2026-05-23", "reason": "kill_switch_active"},
    )

    title, body = render_critical_event(event)

    assert title == "🛑 Kill Switch — Cycle Skipped"
    assert "2026-05-23" in body
    assert "kill_switch_active" in body


def test_render_engine_invariant_error():
    from marketpulse.observability.templates import render_critical_event

    event = _ev(
        "ENGINE_INVARIANT_ERROR",
        context={
            "phase": "exit_materialization",
            "error": "decimal-mismatch",
            "position_id": 42,
        },
    )

    title, body = render_critical_event(event)

    assert title == "🛑 Engine Invariant Error"
    assert "exit_materialization" in body
    assert "decimal-mismatch" in body
    assert "42" in body


def test_render_scheduler_gap_detected():
    from marketpulse.observability.templates import render_critical_event

    event = _ev(
        "SCHEDULER_GAP_DETECTED",
        context={"last_tick_date": "2026-05-15", "gap_days": 4},
    )

    title, body = render_critical_event(event)

    assert title == "🛑 Scheduler Gap Detected"
    assert "2026-05-15" in body
    assert "4" in body


def test_render_scheduler_gap_detected_real_writer_context():
    from marketpulse.observability.templates import render_critical_event

    event = _ev(
        "SCHEDULER_GAP_DETECTED",
        context={
            "last_processed_tick_date": "2026-05-15",
            "resume_date": "2026-05-22",
            "missed_business_days": 4,
            "mode": "forward_only_skip",
        },
    )

    title, body = render_critical_event(event)

    assert title == "🛑 Scheduler Gap Detected"
    assert "2026-05-15" in body
    assert "4" in body


def test_render_tick_reprocessed_completed():
    from marketpulse.observability.templates import render_critical_event

    event = _ev("TICK_REPROCESSED_COMPLETED", context={"tick_date": "2026-05-22"})

    title, body = render_critical_event(event)

    assert title == "⚠️ Tick Reprocessed"
    assert "2026-05-22" in body


def test_render_daily_loss_reject():
    from marketpulse.observability.templates import render_critical_event

    event = _ev(
        "ORDER_REJECTED",
        strategy="momentum",
        context={
            "ticker": "AAPL",
            "strategy": "momentum",
            "quantity": 10,
            "failed_gates": ["daily_loss"],
            "loss_today": "-150.00",
        },
    )

    title, body = render_critical_event(event)

    assert title == "🛑 Daily Loss Limit Tripped"
    assert "AAPL" in body
    assert "momentum" in body
    assert "10" in body
    assert "daily_loss" in body
    assert "-$150.00" in body


def test_render_daily_loss_reject_real_writer_context():
    from marketpulse.observability.templates import render_critical_event

    event = _ev(
        "ORDER_REJECTED",
        strategy="momentum",
        context={
            "order_request": {
                "ticker": "AAPL",
                "strategy": "momentum",
                "quantity": 10,
            },
            "failed_gates": ["daily_loss"],
            "per_gate": [
                {
                    "gate_name": "daily_loss",
                    "approved": False,
                    "context": {
                        "today_realized_pnl": "-150.00",
                        "daily_loss_limit": "100.00",
                    },
                },
            ],
        },
    )

    title, body = render_critical_event(event)

    assert title == "🛑 Daily Loss Limit Tripped"
    assert "AAPL momentum × 10" in body
    assert "-$150.00" in body


def test_render_price_unavailable_stuck():
    from marketpulse.observability.templates import render_critical_event

    event = _ev(
        "PRICE_UNAVAILABLE",
        strategy="momentum",
        context={
            "ticker": "AAPL",
            "position_id": 42,
            "attempt_count": 3,
            "horizon_date": "2026-05-22",
            "source": "yfinance",
        },
    )

    title, body = render_critical_event(event)

    assert title == "⚠️ Position Stuck — AAPL"
    assert "momentum" in body
    assert "2026-05-22" in body
    assert "3 retries" in body
    assert "yfinance" in body


def test_render_position_recovered_positive_pnl():
    from marketpulse.observability.templates import render_critical_event

    event = _ev(
        "POSITION_CLOSED",
        context={
            "ticker": "AAPL",
            "position_id": 42,
            "exit_price": "152.10",
            "realized_pnl": "21.00",
            "retry_count": 5,
        },
    )

    title, body = render_critical_event(event)

    assert title == "✅ Position Recovered — AAPL"
    assert "5 retries" in body
    assert "152.10" in body
    assert "+$21.00" in body


def test_render_position_recovered_negative_pnl_sign():
    from marketpulse.observability.templates import render_critical_event

    event = _ev(
        "POSITION_CLOSED",
        context={
            "ticker": "AAPL",
            "position_id": 42,
            "exit_price": "140.10",
            "realized_pnl": "-12.34",
            "retry_count": 3,
        },
    )

    _, body = render_critical_event(event)

    assert "-$12.34" in body


def test_render_summary_zero_activity_heartbeat():
    from marketpulse.observability.templates import render_tick_summary

    title, body = render_tick_summary(_summary())

    assert title == "📊 Paper Tick 2026-05-22"
    assert "0 placed, 0 rejected" in body
    assert "0 entries, 0 exits" in body
    assert "+$0.00" in body
    assert "$10,000.00" in body
    assert "活跃持仓：0" in body
    assert "Status: completed" in body


def test_render_summary_full_activity():
    from marketpulse.observability.audit_projection import PlacedOrderDetail
    from marketpulse.observability.templates import render_tick_summary

    summary = _summary(
        orders_placed=3,
        orders_placed_detail=[
            PlacedOrderDetail(ticker="AAPL", strategy="momentum", quantity=10),
            PlacedOrderDetail(ticker="NVDA", strategy="defensive", quantity=5),
            PlacedOrderDetail(ticker="MSFT", strategy="momentum", quantity=8),
        ],
        orders_rejected=1,
        orders_rejected_breakdown=[("GOOG", "sector_exposure")],
        entries_filled=[
            ("AAPL", Decimal("155.50")),
            ("NVDA", Decimal("432.10")),
        ],
        positions_closed=[("TSLA", Decimal("248.30"), Decimal("32.50"))],
        total_realized_pnl=Decimal("32.50"),
        cash_balance_end=Decimal("9847.50"),
        active_positions_count=4,
    )

    _, body = render_tick_summary(summary)

    assert "3 placed, 1 rejected" in body
    assert "AAPL × 10 (momentum)" in body
    assert "NVDA × 5 (defensive)" in body
    assert "MSFT × 8 (momentum)" in body
    assert "❌ GOOG (sector_exposure)" in body
    assert "2 entries, 1 exit" in body
    assert "AAPL @ 155.50" in body
    assert "NVDA @ 432.10" in body
    assert "TSLA @ 248.30" in body
    assert "+$32.50" in body
    assert "$9,847.50" in body
    assert "活跃持仓：4" in body


def test_render_summary_omits_empty_orders_section_details():
    from marketpulse.observability.templates import render_tick_summary

    summary = _summary(entries_filled=[("AAPL", Decimal("100.00"))])

    _, body = render_tick_summary(summary)

    assert "0 placed, 0 rejected" in body
    assert "ENTRY: AAPL @ 100.00" in body
    assert "EXIT:" not in body


def test_render_summary_pu_attempt_cap_4_plus():
    from marketpulse.observability.templates import render_tick_summary

    summary = _summary(
        active_positions_count=2,
        active_positions_with_pu=[("AAPL", 4), ("MSFT", 7)],
    )

    _, body = render_tick_summary(summary)

    assert "AAPL" in body
    assert "MSFT" in body
    assert "4+" in body
    assert "attempt 7" not in body


def test_render_summary_pu_attempt_under_threshold():
    from marketpulse.observability.templates import render_tick_summary

    summary = _summary(
        active_positions_count=1,
        active_positions_with_pu=[("AAPL", 2)],
    )

    _, body = render_tick_summary(summary)

    assert "AAPL" in body
    assert "2/3" in body


def test_render_summary_money_sign_and_two_decimals():
    from marketpulse.observability.templates import render_tick_summary

    summary = _summary(
        total_realized_pnl=Decimal("-12.50"),
        cash_balance_end=Decimal("9987.50"),
    )

    _, body = render_tick_summary(summary)

    assert "-$12.50" in body


def test_render_summary_status_skipped():
    from marketpulse.observability.templates import render_tick_summary

    _, body = render_tick_summary(_summary(cycle_status="skipped"))

    assert "Status: skipped" in body


def test_render_summary_truncates_to_notifier_body_limit():
    from marketpulse.observability.audit_projection import PlacedOrderDetail
    from marketpulse.observability.templates import render_tick_summary

    summary = _summary(
        orders_placed=500,
        orders_placed_detail=[
            PlacedOrderDetail(
                ticker=f"TICK{i:03d}",
                strategy="very_long_strategy_name",
                quantity=i + 1,
            )
            for i in range(500)
        ],
    )

    _, body = render_tick_summary(summary, notifier_kind="bark")

    assert len(body) <= 3500
    assert body.endswith("…")
