"""Tests for Phase 6f paper-trading read-side query models."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

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
    assert (
        dashboard.current_operational_window.label
        == "No paper tick has completed yet"
    )
    assert dashboard.critical_events.status == "ok"
    assert dashboard.critical_events.data == []
    assert (
        dashboard.critical_events.empty_message
        == "No operational events in current cycle"
    )
    assert dashboard.positions.status == "ok"
    assert dashboard.positions.data == []
    assert dashboard.positions.empty_message == "No open paper positions"
    assert dashboard.order_lifecycles.status == "ok"
    assert dashboard.order_lifecycles.data == []
    assert (
        dashboard.order_lifecycles.empty_message
        == "No order lifecycle activity in current cycle"
    )


def _audit(
    db_session,
    *,
    event_type,
    ts,
    reason="",
    context=None,
    order_id=None,
    strategy=None,
):
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
    _audit(
        db_session,
        event_type="TICK_COMPLETED",
        ts=old,
        context={"tick_date": "2026-05-22", "status": "completed"},
    )
    boundary = _audit(
        db_session,
        event_type="TICK_REPROCESSED_COMPLETED",
        ts=new,
        reason="recovered_from_errors",
        context={
            "tick_date": "2026-05-23",
            "status": "completed",
            "prior_status": "completed_with_errors",
            "new_status": "completed",
        },
    )
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)

    assert dashboard.current_operational_window.started_at == new
    assert (
        dashboard.current_operational_window.source_event_type
        == "TICK_REPROCESSED_COMPLETED"
    )
    assert dashboard.system_status == "Attention"
    assert [row.audit_id for row in dashboard.audit_timeline.data.rows] == [
        boundary.id,
    ]


def test_completed_tick_without_warnings_is_healthy(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    ts = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    _audit(
        db_session,
        event_type="TICK_COMPLETED",
        ts=ts,
        context={"tick_date": "2026-05-23", "status": "completed"},
    )
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)

    assert dashboard.system_status == "Healthy"
    assert dashboard.health.latest_tick_status == "completed"


def test_completed_with_errors_tick_is_attention(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    ts = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    _audit(
        db_session,
        event_type="TICK_COMPLETED",
        ts=ts,
        context={"tick_date": "2026-05-23", "status": "completed_with_errors"},
    )
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)

    assert dashboard.system_status == "Attention"
    assert dashboard.health.latest_tick_status == "completed_with_errors"


def test_kill_switch_on_from_latest_flip_is_attention(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    ts = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    _audit(
        db_session,
        event_type="TICK_COMPLETED",
        ts=ts,
        context={"tick_date": "2026-05-23", "status": "completed"},
    )
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
        lambda db, window, today, rows: qm.section_error(
            "Unable to load Positions",
            "positions query failed",
        ),
    )

    dashboard = qm.load_paper_trading_dashboard(db_session)

    assert dashboard.system_status == "Degraded"
    assert dashboard.positions.error_title == "Unable to load Positions"
    assert dashboard.positions.degraded_reason == "positions query failed"


@pytest.mark.parametrize(
    "helper_name",
    [
        "_load_operational_window",
        "_window_rows",
        "_build_projection_context",
        "_load_health_summary",
    ],
)
def test_shared_query_failure_returns_degraded_dashboard(
    db_session,
    monkeypatch,
    helper_name,
):
    import marketpulse.trading.query_models as qm

    def fail_shared_fetch(*args, **kwargs):
        raise RuntimeError("shared fetch failed")

    monkeypatch.setattr(qm, helper_name, fail_shared_fetch)

    dashboard = qm.load_paper_trading_dashboard(db_session)

    assert dashboard.system_status == "Degraded"
    assert dashboard.current_operational_window.label == "Unable to load dashboard data"
    assert dashboard.critical_events.status == "error"
    assert dashboard.critical_events.error_title == "Unable to load Critical Events"
    assert dashboard.critical_events.degraded_reason == "RuntimeError"
    assert dashboard.positions.status == "error"
    assert dashboard.positions.error_title == "Unable to load Positions"
    assert dashboard.order_lifecycles.status == "error"
    assert dashboard.order_lifecycles.error_title == "Unable to load Orders & Fills"
    assert dashboard.audit_timeline.status == "error"
    assert dashboard.audit_timeline.error_title == "Unable to load Audit Timeline"


def test_price_unavailable_three_plus_is_attention_and_visible(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    start = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    _audit(
        db_session,
        event_type="TICK_COMPLETED",
        ts=start,
        context={"tick_date": "2026-05-23", "status": "completed"},
    )
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
    _audit(
        db_session,
        event_type="TICK_COMPLETED",
        ts=start,
        context={"tick_date": "2026-05-23", "status": "completed"},
    )
    _audit(
        db_session,
        event_type="PRICE_UNAVAILABLE",
        ts=datetime(2026, 5, 23, 21, 31, tzinfo=UTC),
        reason="no_price",
        context={"position_id": 7, "ticker": "AAPL", "attempt_count": 3},
    )
    recovered = _audit(
        db_session,
        event_type="POSITION_CLOSED",
        ts=datetime(2026, 5, 23, 21, 32, tzinfo=UTC),
        reason="closed",
        context={"position_id": 7, "ticker": "AAPL", "retry_count": 3},
    )
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)

    assert dashboard.system_status == "Healthy"
    assert [event.event_type for event in dashboard.critical_events.data] == [
        "POSITION_CLOSED",
    ]
    assert dashboard.critical_events.data[0].severity == "recovery"
    assert dashboard.critical_events.data[0].audit_id == recovered.id
    assert [row.event_type for row in dashboard.audit_timeline.data.rows] == [
        "PRICE_UNAVAILABLE",
        "POSITION_CLOSED",
    ]


def test_position_closed_recovery_uses_historical_price_unavailable(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    _audit(
        db_session,
        event_type="PRICE_UNAVAILABLE",
        ts=datetime(2026, 5, 22, 21, 31, tzinfo=UTC),
        reason="no_price",
        context={"position_id": 7, "ticker": "AAPL", "attempt_count": 3},
    )
    start = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    _audit(
        db_session,
        event_type="TICK_COMPLETED",
        ts=start,
        context={"tick_date": "2026-05-23", "status": "completed"},
    )
    recovered = _audit(
        db_session,
        event_type="POSITION_CLOSED",
        ts=datetime(2026, 5, 23, 21, 32, tzinfo=UTC),
        reason="closed",
        context={"position_id": 7, "ticker": "AAPL"},
    )
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)

    assert [event.event_type for event in dashboard.critical_events.data] == [
        "POSITION_CLOSED",
    ]
    assert dashboard.critical_events.data[0].severity == "recovery"
    assert dashboard.critical_events.data[0].audit_id == recovered.id


def test_position_closed_recovery_ticker_is_enriched_from_position(db_session):
    from decimal import Decimal

    from marketpulse.trading.query_models import load_paper_trading_dashboard

    order = _paper_order(db_session, ticker="AAPL", strategy="momentum_breakout")
    position = _position(
        db_session,
        order,
        status="CLOSED",
        closed_at=datetime(2026, 5, 23, 21, 32, tzinfo=UTC),
        realized_pnl=Decimal("0"),
    )
    _audit(
        db_session,
        event_type="PRICE_UNAVAILABLE",
        ts=datetime(2026, 5, 22, 21, 31, tzinfo=UTC),
        reason="no_price",
        context={"position_id": position.id, "attempt_count": 3},
    )
    start = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    _audit(
        db_session,
        event_type="TICK_COMPLETED",
        ts=start,
        context={"tick_date": "2026-05-23", "status": "completed"},
    )
    recovered = _audit(
        db_session,
        event_type="POSITION_CLOSED",
        ts=datetime(2026, 5, 23, 21, 32, tzinfo=UTC),
        reason="closed",
        context={"position_id": position.id},
    )
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)

    assert dashboard.critical_events.data[0].audit_id == recovered.id
    assert dashboard.critical_events.data[0].ticker == "AAPL"
    assert dashboard.critical_events.data[0].strategy == "momentum_breakout"
    timeline_row = dashboard.audit_timeline.data.rows[0]
    assert timeline_row.audit_id == recovered.id
    assert timeline_row.ticker == "AAPL"
    assert timeline_row.strategy == "momentum_breakout"


def test_audit_timeline_hides_routine_rows_but_loads_them_for_client_reveal(
    db_session,
):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    start = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    _audit(
        db_session,
        event_type="TICK_COMPLETED",
        ts=start,
        context={"tick_date": "2026-05-23", "status": "completed"},
    )
    placed = _audit(
        db_session,
        event_type="ORDER_PLACED",
        ts=datetime(2026, 5, 23, 21, 31, tzinfo=UTC),
        reason="",
        context={"ticker": "AAPL"},
    )
    rejected = _audit(
        db_session,
        event_type="ORDER_REJECTED",
        ts=datetime(2026, 5, 23, 21, 32, tzinfo=UTC),
        reason="risk_gate_failed",
        context={"failed_gates": ["daily_loss"], "ticker": "MSFT"},
    )
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)
    timeline = dashboard.audit_timeline.data

    assert timeline.routine_hidden_count == 1
    assert {row.audit_id for row in timeline.rows} == {placed.id, rejected.id}
    assert [row.routine for row in timeline.rows if row.audit_id == placed.id] == [
        True,
    ]
    assert [row.routine for row in timeline.rows if row.audit_id == rejected.id] == [
        False,
    ]


def _paper_order(
    db_session,
    *,
    ticker="AAPL",
    strategy="momentum_breakout",
    status="ENTRY_FILLED",
    placed_at=None,
    filled_at=None,
):
    from decimal import Decimal

    from marketpulse.db.models import PaperOrder

    ts = placed_at or datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    effective_filled_at = filled_at
    if status == "ENTRY_FILLED" and effective_filled_at is None:
        effective_filled_at = ts
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
        filled_at=effective_filled_at,
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


def _position(
    db_session,
    order,
    *,
    status="OPEN",
    horizon_date=date(2026, 5, 23),
    realized_pnl=None,
    closed_at=None,
):
    from decimal import Decimal

    from marketpulse.db.models import PaperPosition

    row = PaperPosition(
        order_id=order.id,
        entry_fill_id=1 if status == "CLOSED" else None,
        exit_fill_id=2 if status == "CLOSED" else None,
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
    _audit(
        db_session,
        event_type="TICK_COMPLETED",
        ts=start,
        context={"tick_date": "2026-05-23", "status": "completed"},
    )
    order = _paper_order(db_session)
    position = _position(db_session, order, horizon_date=date(2026, 5, 23))
    _audit(
        db_session,
        event_type="PRICE_UNAVAILABLE",
        ts=datetime(2026, 5, 23, 21, 31, tzinfo=UTC),
        context={"position_id": position.id, "attempt_count": 2, "ticker": "AAPL"},
    )
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
    _audit(
        db_session,
        event_type="TICK_COMPLETED",
        ts=start,
        context={"tick_date": "2026-05-23", "status": "completed"},
    )
    order = _paper_order(db_session)
    position = _position(db_session, order, horizon_date=date(2026, 5, 23))
    _audit(
        db_session,
        event_type="PRICE_UNAVAILABLE",
        ts=datetime(2026, 5, 23, 21, 31, tzinfo=UTC),
        context={"position_id": position.id, "attempt_count": 3, "ticker": "AAPL"},
    )
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)

    assert dashboard.positions.data[0].operational_exit_status == "STUCK_3_PLUS"
    assert dashboard.positions.data[0].exit_health_label == "Stuck 3+"


def test_positions_overlay_uses_historical_price_unavailable_attempts(db_session):
    from marketpulse.trading.query_models import load_paper_trading_dashboard

    order = _paper_order(db_session)
    position = _position(db_session, order, horizon_date=date(2026, 5, 23))
    _audit(
        db_session,
        event_type="PRICE_UNAVAILABLE",
        ts=datetime(2026, 5, 22, 21, 31, tzinfo=UTC),
        context={"position_id": position.id, "attempt_count": 3, "ticker": "AAPL"},
    )
    _audit(
        db_session,
        event_type="TICK_COMPLETED",
        ts=datetime(2026, 5, 23, 21, 30, tzinfo=UTC),
        context={"tick_date": "2026-05-23", "status": "completed"},
    )
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
    _audit(
        db_session,
        event_type="TICK_COMPLETED",
        ts=start,
        context={"tick_date": "2026-05-23", "status": "completed"},
    )
    order = _paper_order(
        db_session,
        filled_at=datetime(2026, 5, 23, 21, 31, tzinfo=UTC),
    )
    position = _position(
        db_session,
        order,
        status="CLOSED",
        realized_pnl=Decimal("30"),
        closed_at=datetime(2026, 5, 23, 21, 40, tzinfo=UTC),
    )
    _fill(
        db_session,
        order=order,
        position=position,
        side="ENTRY",
        price="100",
        filled_at=datetime(2026, 5, 23, 21, 31, tzinfo=UTC),
    )
    _fill(
        db_session,
        order=order,
        position=position,
        side="EXIT",
        price="110",
        filled_at=datetime(2026, 5, 23, 21, 40, tzinfo=UTC),
        realized_pnl=Decimal("30"),
    )
    _audit(
        db_session,
        event_type="ORDER_ENTRY_FILLED",
        ts=datetime(2026, 5, 23, 21, 31, tzinfo=UTC),
        order_id=order.id,
        reason="entry_filled",
        context={"ticker": "AAPL"},
    )
    _audit(
        db_session,
        event_type="POSITION_CLOSED",
        ts=datetime(2026, 5, 23, 21, 40, tzinfo=UTC),
        order_id=order.id,
        reason="closed",
        context={"position_id": position.id, "ticker": "AAPL"},
    )
    db_session.commit()

    dashboard = load_paper_trading_dashboard(db_session)

    row = dashboard.order_lifecycles.data[0]
    assert row.order_id == order.id
    assert row.entry_price == Decimal("100.000000")
    assert row.exit_price == Decimal("110.000000")
    assert row.realized_pnl == Decimal("30.000000")
    assert row.latest_audit_reason == "closed"


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


def _closed(
    db_session,
    *,
    realized_pnl,
    closed_at,
    ticker="AAPL",
    strategy="general",
    entry_price=Decimal("100"),
    quantity=3,
    entry_date=date(2026, 5, 20),
):
    """Seed one CLOSED PaperPosition (with its parent order) with controllable
    entry_price / quantity / realized_pnl / closed_at."""
    from marketpulse.db.models import PaperPosition

    order = _paper_order(db_session, ticker=ticker, strategy=strategy, placed_at=closed_at)
    pos = PaperPosition(
        order_id=order.id,
        entry_fill_id=1,
        exit_fill_id=2,
        strategy=strategy,
        ticker=ticker,
        quantity=quantity,
        entry_price=entry_price,
        entry_date=entry_date,
        horizon_date=entry_date,
        status="CLOSED",
        opened_at=closed_at,
        closed_at=closed_at,
        exit_price=Decimal("110"),
        realized_pnl=realized_pnl,
    )
    db_session.add(pos)
    db_session.flush()
    return pos


def test_closed_trades_orders_and_summary(db_session):
    from marketpulse.trading.query_models import _load_closed_trades_section

    _closed(db_session, ticker="AAA", realized_pnl=Decimal("30"),
            closed_at=datetime(2026, 6, 1, 21, 30, tzinfo=UTC), entry_date=date(2026, 5, 27))
    _closed(db_session, ticker="BBB", realized_pnl=Decimal("-15"),
            closed_at=datetime(2026, 6, 2, 21, 30, tzinfo=UTC), entry_date=date(2026, 5, 28))
    _closed(db_session, ticker="CCC", realized_pnl=Decimal("60"),
            closed_at=datetime(2026, 6, 3, 21, 30, tzinfo=UTC), entry_date=date(2026, 5, 29))
    db_session.commit()

    section = _load_closed_trades_section(db_session)
    assert section.status == "ok"
    ct = section.data
    assert [r.ticker for r in ct.rows] == ["CCC", "BBB", "AAA"]
    ccc = ct.rows[0]
    assert ccc.exit_date == date(2026, 6, 3)
    assert ccc.days_held == (date(2026, 6, 3) - date(2026, 5, 29)).days
    assert ccc.return_pct == pytest.approx(60.0 / 300.0)
    s = ct.summary
    assert s.total_count == 3
    assert s.realized_pnl_total == Decimal("75")
    assert s.win_rate == 2 / 3
    assert s.avg_return_pct == pytest.approx((0.1 - 0.05 + 0.2) / 3)
    assert ct.count_label == "Showing 3 closed trades"


def test_closed_trades_zero_cost_return_none(db_session):
    from marketpulse.trading.query_models import _load_closed_trades_section
    _closed(db_session, ticker="ZZZ", realized_pnl=Decimal("5"),
            closed_at=datetime(2026, 6, 1, 21, 30, tzinfo=UTC), entry_price=Decimal("0"))
    db_session.commit()
    ct = _load_closed_trades_section(db_session).data
    assert ct.rows[0].return_pct is None
    assert ct.summary.avg_return_pct is None
    assert ct.summary.win_rate == 1.0


def test_closed_trades_winrate_excludes_unpriced(db_session):
    from marketpulse.trading.query_models import _load_closed_trades_section
    _closed(db_session, ticker="WIN1", realized_pnl=Decimal("10"),
            closed_at=datetime(2026, 6, 1, 21, 30, tzinfo=UTC))
    _closed(db_session, ticker="WIN2", realized_pnl=Decimal("20"),
            closed_at=datetime(2026, 6, 2, 21, 30, tzinfo=UTC))
    _closed(db_session, ticker="NONE", realized_pnl=None,
            closed_at=datetime(2026, 6, 3, 21, 30, tzinfo=UTC))
    db_session.commit()
    ct = _load_closed_trades_section(db_session).data
    assert ct.summary.total_count == 3
    assert ct.summary.win_rate == 1.0
    assert ct.summary.realized_pnl_total == Decimal("30")


def test_closed_trades_empty(db_session):
    from marketpulse.trading.query_models import _load_closed_trades_section
    section = _load_closed_trades_section(db_session)
    assert section.status == "ok"
    ct = section.data
    assert ct.rows == []
    assert ct.summary.total_count == 0
    assert ct.summary.win_rate is None
    assert ct.summary.avg_return_pct is None
    assert ct.count_label == "Showing 0 closed trades"
    assert section.empty_message == "No closed trades yet"


def test_closed_trades_cap_50(db_session):
    from marketpulse.trading.query_models import _load_closed_trades_section
    for i in range(55):
        _closed(db_session, ticker=f"T{i:02d}", realized_pnl=Decimal("1"),
                closed_at=datetime(2026, 6, 1, 0, i, tzinfo=UTC))
    db_session.commit()
    ct = _load_closed_trades_section(db_session).data
    assert len(ct.rows) == 50
    assert ct.summary.total_count == 55
    assert ct.count_label == "Showing latest 50 of 55 closed trades"
