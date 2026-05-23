# Layer: pure
"""6g-T3: select_critical_events pure projection tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType

import pytest


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


def test_order_rejected_daily_loss_is_critical():
    from marketpulse.observability.audit_projection import select_critical_events

    row = _Row(
        id=1,
        timestamp=_ts(),
        event_type="ORDER_REJECTED",
        order_id=10,
        strategy="momentum",
        reason="rejected",
        context={
            "ticker": "AAPL",
            "quantity": 10,
            "failed_gates": ["daily_loss"],
            "loss_today": "-150.00",
        },
    )

    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
        threshold=3,
    )

    assert len(out) == 1
    assert out[0].event_type == "ORDER_REJECTED"
    assert out[0].audit_id == 1
    assert out[0].context["failed_gates"] == ["daily_loss"]


def test_order_rejected_other_gate_is_not_critical():
    from marketpulse.observability.audit_projection import select_critical_events

    row = _Row(
        id=2,
        timestamp=_ts(),
        event_type="ORDER_REJECTED",
        order_id=11,
        strategy="momentum",
        reason="rejected",
        context={"ticker": "GOOG", "failed_gates": ["sector_exposure"]},
    )

    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )

    assert out == []


def test_order_rejected_daily_loss_among_multiple_gates_is_critical():
    """Lock 6g-L3: any failed gate containing daily_loss is critical."""
    from marketpulse.observability.audit_projection import select_critical_events

    row = _Row(
        id=3,
        timestamp=_ts(),
        event_type="ORDER_REJECTED",
        order_id=12,
        strategy="defensive",
        context={"failed_gates": ["sector_exposure", "daily_loss"]},
    )

    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )

    assert len(out) == 1


def test_order_rejected_missing_failed_gates_is_not_critical():
    from marketpulse.observability.audit_projection import select_critical_events

    row = _Row(
        id=4,
        timestamp=_ts(),
        event_type="ORDER_REJECTED",
        context={"ticker": "TSLA"},
    )

    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )

    assert out == []


def test_price_unavailable_attempt_3_is_critical():
    from marketpulse.observability.audit_projection import select_critical_events

    row = _Row(
        id=5,
        timestamp=_ts(),
        event_type="PRICE_UNAVAILABLE",
        strategy="momentum",
        context={
            "ticker": "AAPL",
            "position_id": 42,
            "attempt_count": 3,
            "horizon_date": "2026-05-22",
            "source": "yfinance",
        },
    )

    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
        threshold=3,
    )

    assert len(out) == 1
    assert out[0].event_type == "PRICE_UNAVAILABLE"


def test_price_unavailable_attempt_1_or_2_is_not_critical():
    from marketpulse.observability.audit_projection import select_critical_events

    for attempt in (1, 2):
        row = _Row(
            id=10 + attempt,
            timestamp=_ts(),
            event_type="PRICE_UNAVAILABLE",
            context={"ticker": "AAPL", "position_id": 42, "attempt_count": attempt},
        )

        out = select_critical_events(
            new_audit_rows=[row],
            kill_switch_cycle_skipped_in_period=False,
            positions_with_prior_pu=set(),
            threshold=3,
        )

        assert out == [], f"attempt {attempt} unexpectedly critical"


def test_price_unavailable_attempt_4_plus_is_suppressed():
    """Lock 6g-L4a: attempts after the threshold do not repeat critical pushes."""
    from marketpulse.observability.audit_projection import select_critical_events

    for attempt in (4, 5, 10):
        row = _Row(
            id=100 + attempt,
            timestamp=_ts(),
            event_type="PRICE_UNAVAILABLE",
            context={"ticker": "AAPL", "position_id": 42, "attempt_count": attempt},
        )

        out = select_critical_events(
            new_audit_rows=[row],
            kill_switch_cycle_skipped_in_period=False,
            positions_with_prior_pu=set(),
            threshold=3,
        )

        assert out == [], f"attempt {attempt} should be suppressed"


def test_position_closed_with_prior_pu_is_recovery_critical():
    from marketpulse.observability.audit_projection import select_critical_events

    row = _Row(
        id=20,
        timestamp=_ts(),
        event_type="POSITION_CLOSED",
        strategy="momentum",
        context={
            "ticker": "AAPL",
            "position_id": 42,
            "exit_price": "152.10",
            "realized_pnl": "21.00",
            "retry_count": 5,
        },
    )

    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu={42},
    )

    assert len(out) == 1
    assert out[0].event_type == "POSITION_CLOSED"


def test_position_closed_without_prior_pu_is_summary_only():
    from marketpulse.observability.audit_projection import select_critical_events

    row = _Row(
        id=21,
        timestamp=_ts(),
        event_type="POSITION_CLOSED",
        context={
            "ticker": "AAPL",
            "position_id": 99,
            "exit_price": "155.00",
            "realized_pnl": "50.00",
        },
    )

    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )

    assert out == []


def test_kill_switch_flipped_active_true_is_critical():
    from marketpulse.observability.audit_projection import select_critical_events

    row = _Row(
        id=30,
        timestamp=_ts(),
        event_type="KILL_SWITCH_FLIPPED",
        reason="max_drawdown_exceeded",
        context={"to_state": True, "reason": "max_drawdown_exceeded"},
    )

    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )

    assert len(out) == 1


def test_kill_switch_flipped_active_false_is_critical():
    from marketpulse.observability.audit_projection import select_critical_events

    row = _Row(
        id=31,
        timestamp=_ts(),
        event_type="KILL_SWITCH_FLIPPED",
        context={"to_state": False, "reason": "manual_reset"},
    )

    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )

    assert len(out) == 1


def test_kill_switch_cycle_skipped_first_is_critical():
    from marketpulse.observability.audit_projection import select_critical_events

    row = _Row(
        id=32,
        timestamp=_ts(),
        event_type="KILL_SWITCH_CYCLE_SKIPPED",
        context={"tick_date": "2026-05-23", "reason": "kill_switch_active"},
    )

    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )

    assert len(out) == 1


def test_kill_switch_cycle_skipped_dedups_when_prior_exists():
    """Lock 6g-L5: subsequent skip in same active period is suppressed."""
    from marketpulse.observability.audit_projection import select_critical_events

    row = _Row(
        id=33,
        timestamp=_ts(),
        event_type="KILL_SWITCH_CYCLE_SKIPPED",
        context={"tick_date": "2026-05-24"},
    )

    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=True,
        positions_with_prior_pu=set(),
    )

    assert out == []


@pytest.mark.parametrize(
    "event_type",
    [
        "ENGINE_INVARIANT_ERROR",
        "SCHEDULER_GAP_DETECTED",
        "TICK_REPROCESSED_COMPLETED",
    ],
)
def test_always_critical_event_types(event_type):
    from marketpulse.observability.audit_projection import select_critical_events

    row = _Row(id=40, timestamp=_ts(), event_type=event_type, context={})

    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )

    assert len(out) == 1
    assert out[0].event_type == event_type


def test_routine_events_never_critical():
    from marketpulse.observability.audit_projection import select_critical_events

    for event_type in (
        "ORDER_PLACED",
        "ORDER_ENTRY_FILLED",
        "ORDER_PLACED_DUPLICATE",
        "ORDER_CANCELLED",
        "TICK_COMPLETED",
    ):
        row = _Row(id=50, timestamp=_ts(), event_type=event_type, context={})

        out = select_critical_events(
            new_audit_rows=[row],
            kill_switch_cycle_skipped_in_period=False,
            positions_with_prior_pu=set(),
        )

        assert out == [], f"{event_type} should not be critical"


def test_critical_event_carries_canonical_fields_and_immutable_context():
    from marketpulse.observability.audit_projection import (
        CriticalEvent,
        select_critical_events,
    )

    row = _Row(
        id=60,
        timestamp=_ts(20),
        event_type="PRICE_UNAVAILABLE",
        strategy="momentum",
        reason="no_close",
        context={
            "ticker": "AAPL",
            "position_id": 42,
            "attempt_count": 3,
            "horizon_date": "2026-05-22",
            "source": "yfinance",
        },
    )

    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )

    assert len(out) == 1
    event = out[0]
    assert isinstance(event, CriticalEvent)
    assert event.audit_id == 60
    assert event.timestamp == _ts(20)
    assert event.strategy == "momentum"
    assert event.reason == "no_close"
    assert isinstance(event.context, MappingProxyType)
    assert event.context["ticker"] == "AAPL"
    with pytest.raises(TypeError):
        event.context["ticker"] = "MSFT"


def test_select_critical_events_preserves_audit_order():
    from marketpulse.observability.audit_projection import select_critical_events

    rows = [
        _Row(
            id=1,
            timestamp=_ts(),
            event_type="KILL_SWITCH_FLIPPED",
            context={"to_state": True},
        ),
        _Row(
            id=2,
            timestamp=_ts(),
            event_type="PRICE_UNAVAILABLE",
            context={"ticker": "AAPL", "position_id": 1, "attempt_count": 3},
        ),
        _Row(
            id=3,
            timestamp=_ts(),
            event_type="ENGINE_INVARIANT_ERROR",
            context={"phase": "entry", "error": "bad"},
        ),
    ]

    out = select_critical_events(
        new_audit_rows=rows,
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )

    assert [event.audit_id for event in out] == [1, 2, 3]


def test_price_unavailable_monotonic_regression_records_failure():
    from marketpulse.observability.audit_projection import (
        NotificationFailure,
        select_critical_events,
    )

    rows = [
        _Row(
            id=80,
            timestamp=_ts(),
            event_type="PRICE_UNAVAILABLE",
            context={"ticker": "AAPL", "position_id": 42, "attempt_count": 2},
        ),
    ]
    failures: list[NotificationFailure] = []

    select_critical_events(
        new_audit_rows=rows,
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
        failures=failures,
        prior_attempts_by_position={42: 5},
    )

    assert len(failures) == 1
    assert failures[0].event_type == "PRICE_UNAVAILABLE"
    assert failures[0].error.startswith("monotonic_invariant_violation:")


def test_price_unavailable_monotonic_non_regression_has_no_failure():
    from marketpulse.observability.audit_projection import (
        NotificationFailure,
        select_critical_events,
    )

    rows = [
        _Row(
            id=81,
            timestamp=_ts(),
            event_type="PRICE_UNAVAILABLE",
            context={"ticker": "AAPL", "position_id": 42, "attempt_count": 6},
        ),
    ]
    failures: list[NotificationFailure] = []

    select_critical_events(
        new_audit_rows=rows,
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
        failures=failures,
        prior_attempts_by_position={42: 5},
    )

    assert failures == []
