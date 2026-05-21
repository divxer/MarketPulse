# Layer: invariant
# Assert frozen-ness only — do NOT strict-require hashability across the
# board (future fields may include list/dict context payloads).
"""6a-1: types.py vocabulary smoke."""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest


def test_order_request_is_frozen():
    from marketpulse.trading.types import AllocationRunId, OrderRequest

    req = OrderRequest(
        strategy="test_strat",
        ticker="AAPL",
        quantity=10,
        event_time=datetime(2026, 5, 21, 14, 30, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=Decimal("150.00"),
        horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155.00"),
        allocation_run_id=AllocationRunId("paper-2026-05-21"),
        strategy_version="v0",
        allocator_version="v0",
        execution_engine_version="v0",
        weight=1.0,
        raw_bid_weight=1.0,
        pool_corr=0.1,
        contribution_multiplier=1.0,
        adjusted_bid_weight=1.0,
        effective_corr_window=60,
        rewarded_for_negative_corr=False,
        would_change_rank=False,
        size_clamped_by_override=False,
    )

    # Frozen dataclass: mutation must raise.
    with pytest.raises(dataclasses.FrozenInstanceError):
        req.quantity = 99  # type: ignore[misc]


def test_tick_result_and_tick_error_shapes():
    from marketpulse.trading.types import TickError, TickResult

    err = TickError(
        phase="entry_materialization",
        order_id=42,
        position_id=None,
        error="something bad",
    )
    res = TickResult(
        as_of=date(2026, 5, 21),
        entries_materialized=3,
        exits_materialized=1,
        errors=(err,),
    )
    assert res.entries_materialized == 3
    assert res.errors[0].phase == "entry_materialization"
    # errors must be a tuple (immutable) — 6a-L4
    assert isinstance(res.errors, tuple)


def test_place_order_result_carries_created_and_duplicate_flags():
    """6a-L2: PlaceOrderResult eliminates the TOCTOU race that a separate
    pre-check would introduce."""
    from marketpulse.trading.types import OrderId, PlaceOrderResult

    r = PlaceOrderResult(order_id=OrderId(1), created=True, duplicate=False)
    assert r.created is True
    assert r.duplicate is False

    r2 = PlaceOrderResult(order_id=OrderId(1), created=False, duplicate=True)
    assert r2.duplicate is True


def test_audit_event_type_enum_has_12_values():
    """6a audit event types: 12 total."""
    from marketpulse.trading.types import AuditEventType

    expected = {
        "ORDER_PLACED",
        "ORDER_PLACED_DUPLICATE",
        "ORDER_REJECTED",
        "ORDER_CANCELLED",
        "ORDER_ENTRY_FILLED",
        "POSITION_CLOSED",
        "KILL_SWITCH_FLIPPED",
        "KILL_SWITCH_CYCLE_SKIPPED",
        "TICK_COMPLETED",
        "TICK_REPROCESSED_COMPLETED",
        "SCHEDULER_GAP_DETECTED",
        "ENGINE_INVARIANT_ERROR",
    }
    actual = {e.value for e in AuditEventType}
    assert actual == expected, f"Missing: {expected - actual}; Extra: {actual - expected}"


# Layer: stateful

def test_layer_stateful_tag_accepted_by_hook():
    """If pytest collected this test, the hook accepts 'stateful'.

    # Layer: stateful
    """
    assert True


def test_all_trading_modules_importable():
    """6a-1 scaffolding: all marketpulse.trading.* modules exist (some
    are stubs filled in by later sub-tasks). Import smoke only."""
    import marketpulse.trading.bid_aggregator  # noqa: F401
    import marketpulse.trading.daily_cycle  # noqa: F401
    import marketpulse.trading.forward_engine  # noqa: F401
    import marketpulse.trading.kill_switch  # noqa: F401
    import marketpulse.trading.repository  # noqa: F401
