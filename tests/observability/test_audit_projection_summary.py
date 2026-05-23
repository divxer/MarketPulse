# Layer: pure
"""6g-T4: summarize_tick pure summary builder tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal


@dataclass
class _Row:
    id: int
    timestamp: datetime
    event_type: str
    order_id: int | None = None
    strategy: str | None = None
    reason: str | None = ""
    context: dict | None = None

    def __post_init__(self):
        if self.context is None:
            self.context = {}


def _ts(hour: int = 18) -> datetime:
    return datetime(2026, 5, 22, hour, 0, tzinfo=UTC)


def test_summarize_reads_status_from_tick_completed_row():
    from marketpulse.observability.audit_projection import summarize_tick

    rows = [
        _Row(
            id=1,
            timestamp=_ts(),
            event_type="TICK_COMPLETED",
            context={"tick_date": "2026-05-22", "status": "completed"},
        ),
    ]

    summary, failures = summarize_tick(
        new_audit_rows=rows,
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("10000.00"),
        active_positions_with_pu_attempts=[],
        active_positions_count=0,
    )

    assert summary.cycle_status == "completed"
    assert failures == ()


def test_summarize_falls_back_to_kill_switch_cycle_skipped_status():
    from marketpulse.observability.audit_projection import summarize_tick

    rows = [
        _Row(
            id=1,
            timestamp=_ts(),
            event_type="KILL_SWITCH_CYCLE_SKIPPED",
            context={"tick_date": "2026-05-22", "status": "skipped"},
        ),
    ]

    summary, failures = summarize_tick(
        new_audit_rows=rows,
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("10000.00"),
        active_positions_with_pu_attempts=[],
        active_positions_count=0,
    )

    assert summary.cycle_status == "skipped"
    assert failures == ()


def test_summarize_returns_unknown_status_and_failure_when_no_tick_row():
    from marketpulse.observability.audit_projection import (
        NotificationFailure,
        summarize_tick,
    )

    summary, failures = summarize_tick(
        new_audit_rows=[],
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("10000.00"),
        active_positions_with_pu_attempts=[],
        active_positions_count=0,
    )

    assert summary.cycle_status == "unknown"
    assert len(failures) == 1
    assert isinstance(failures[0], NotificationFailure)
    assert failures[0].event_type == "tick_summary"
    assert failures[0].error == "missing_tick_completed_row"


def test_summarize_ignores_status_rows_for_different_tick_date():
    from marketpulse.observability.audit_projection import summarize_tick

    rows = [
        _Row(
            id=1,
            timestamp=_ts(),
            event_type="TICK_COMPLETED",
            context={"tick_date": "2026-05-20", "status": "completed"},
        ),
    ]

    summary, failures = summarize_tick(
        new_audit_rows=rows,
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("10000.00"),
        active_positions_with_pu_attempts=[],
        active_positions_count=0,
    )

    assert summary.cycle_status == "unknown"
    assert failures[0].error == "missing_tick_completed_row"


def test_summarize_aggregates_orders_placed_detail():
    from marketpulse.observability.audit_projection import (
        PlacedOrderDetail,
        summarize_tick,
    )

    rows = [
        _Row(
            id=1,
            timestamp=_ts(),
            event_type="ORDER_PLACED",
            strategy="momentum",
            context={"ticker": "AAPL", "quantity": 10},
        ),
        _Row(
            id=2,
            timestamp=_ts(),
            event_type="ORDER_PLACED",
            strategy="defensive",
            context={"ticker": "NVDA", "quantity": 5},
        ),
        _Row(
            id=3,
            timestamp=_ts(),
            event_type="TICK_COMPLETED",
            context={"tick_date": "2026-05-22", "status": "completed"},
        ),
    ]

    summary, _ = summarize_tick(
        new_audit_rows=rows,
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("8500.00"),
        active_positions_with_pu_attempts=[],
        active_positions_count=2,
    )

    assert summary.orders_placed == 2
    assert summary.orders_placed_detail == [
        PlacedOrderDetail(ticker="AAPL", strategy="momentum", quantity=10),
        PlacedOrderDetail(ticker="NVDA", strategy="defensive", quantity=5),
    ]


def test_summarize_aggregates_rejects_breakdown():
    from marketpulse.observability.audit_projection import summarize_tick

    rows = [
        _Row(
            id=1,
            timestamp=_ts(),
            event_type="ORDER_REJECTED",
            context={"ticker": "GOOG", "failed_gates": ["sector_exposure"]},
        ),
        _Row(
            id=2,
            timestamp=_ts(),
            event_type="ORDER_REJECTED",
            context={"ticker": "TSLA", "failed_gates": ["daily_loss"]},
        ),
        _Row(
            id=3,
            timestamp=_ts(),
            event_type="TICK_COMPLETED",
            context={"tick_date": "2026-05-22", "status": "completed"},
        ),
    ]

    summary, _ = summarize_tick(
        new_audit_rows=rows,
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("10000.00"),
        active_positions_with_pu_attempts=[],
        active_positions_count=0,
    )

    assert summary.orders_rejected == 2
    assert summary.orders_rejected_breakdown == [
        ("GOOG", "sector_exposure"),
        ("TSLA", "daily_loss"),
    ]


def test_summarize_collects_entry_fills_and_exits_with_pnl():
    from marketpulse.observability.audit_projection import summarize_tick

    rows = [
        _Row(
            id=1,
            timestamp=_ts(),
            event_type="ORDER_ENTRY_FILLED",
            context={"ticker": "AAPL", "fill_price": "155.50"},
        ),
        _Row(
            id=2,
            timestamp=_ts(),
            event_type="POSITION_CLOSED",
            context={
                "ticker": "TSLA",
                "exit_price": "248.30",
                "realized_pnl": "32.50",
            },
        ),
        _Row(
            id=3,
            timestamp=_ts(),
            event_type="TICK_COMPLETED",
            context={"tick_date": "2026-05-22", "status": "completed"},
        ),
    ]

    summary, failures = summarize_tick(
        new_audit_rows=rows,
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("10032.50"),
        active_positions_with_pu_attempts=[],
        active_positions_count=1,
    )

    assert summary.entries_filled == [("AAPL", Decimal("155.50"))]
    assert summary.positions_closed == [
        ("TSLA", Decimal("248.30"), Decimal("32.50")),
    ]
    assert summary.total_realized_pnl == Decimal("32.50")
    assert failures == ()


def test_summarize_counts_cancelled_and_duplicates():
    from marketpulse.observability.audit_projection import summarize_tick

    rows = [
        _Row(id=1, timestamp=_ts(), event_type="ORDER_CANCELLED"),
        _Row(id=2, timestamp=_ts(), event_type="ORDER_PLACED_DUPLICATE"),
        _Row(id=3, timestamp=_ts(), event_type="ORDER_PLACED_DUPLICATE"),
        _Row(
            id=4,
            timestamp=_ts(),
            event_type="TICK_COMPLETED",
            context={"tick_date": "2026-05-22", "status": "completed"},
        ),
    ]

    summary, _ = summarize_tick(
        new_audit_rows=rows,
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("10000.00"),
        active_positions_with_pu_attempts=[],
        active_positions_count=0,
    )

    assert summary.orders_cancelled == 1
    assert summary.duplicates_skipped == 2


def test_summarize_threads_active_positions_with_pu_attempts():
    from marketpulse.observability.audit_projection import summarize_tick

    rows = [
        _Row(
            id=1,
            timestamp=_ts(),
            event_type="TICK_COMPLETED",
            context={"tick_date": "2026-05-22", "status": "completed"},
        ),
    ]

    summary, _ = summarize_tick(
        new_audit_rows=rows,
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("9800.00"),
        active_positions_with_pu_attempts=[("AAPL", 3), ("MSFT", 4)],
        active_positions_count=4,
    )

    assert summary.active_positions_count == 4
    assert summary.active_positions_with_pu == [("AAPL", 3), ("MSFT", 4)]


def test_summarize_heartbeat_zero_activity():
    from marketpulse.observability.audit_projection import summarize_tick

    rows = [
        _Row(
            id=1,
            timestamp=_ts(),
            event_type="TICK_COMPLETED",
            context={"tick_date": "2026-05-22", "status": "completed"},
        ),
    ]

    summary, failures = summarize_tick(
        new_audit_rows=rows,
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("10000.00"),
        active_positions_with_pu_attempts=[],
        active_positions_count=0,
    )

    assert summary.tick_date == date(2026, 5, 22)
    assert summary.cycle_status == "completed"
    assert summary.orders_placed == 0
    assert summary.orders_rejected == 0
    assert summary.orders_cancelled == 0
    assert summary.duplicates_skipped == 0
    assert summary.entries_filled == []
    assert summary.positions_closed == []
    assert summary.total_realized_pnl == Decimal("0")
    assert summary.cash_balance_end == Decimal("10000.00")
    assert summary.active_positions_count == 0
    assert failures == ()


def test_summarize_malformed_numeric_uses_default_and_records_failure():
    from marketpulse.observability.audit_projection import summarize_tick

    rows = [
        _Row(
            id=1,
            timestamp=_ts(),
            event_type="ORDER_ENTRY_FILLED",
            context={"ticker": "AAPL", "fill_price": "not-a-decimal"},
        ),
        _Row(
            id=2,
            timestamp=_ts(),
            event_type="TICK_COMPLETED",
            context={"tick_date": "2026-05-22", "status": "completed"},
        ),
    ]

    summary, failures = summarize_tick(
        new_audit_rows=rows,
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("10000.00"),
        active_positions_with_pu_attempts=[],
        active_positions_count=0,
    )

    assert summary.entries_filled == [("AAPL", Decimal("0"))]
    assert len(failures) == 1
    assert failures[0].error.startswith("malformed_numeric:fill_price")
