# Layer: test
"""PR3a — charter_metrics north_star + diagnostics extension tests."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from marketpulse.db.models import PaperNavSnapshot
from marketpulse.ops.charter_metrics import build_charter_metrics
from marketpulse.portfolio.north_star import NavSnapshot
from marketpulse.portfolio.snapshot_repo import insert_snapshot


def _snap(d: date, *, value: str = "0.032", observed: int = 12) -> NavSnapshot:
    return NavSnapshot(
        trading_date=d,
        cash_balance=Decimal("100000"),
        holdings_mtm=Decimal("0"),
        portfolio_nav=Decimal("100000"),
        anchor_portfolio_nav=Decimal("100000"),
        portfolio_index=Decimal("1.041"),
        spy_close=Decimal("500"),
        anchor_spy_close=Decimal("475"),
        spy_index=Decimal("1.009"),
        excess_return=Decimal(value),
        trading_days_observed=observed,
        coverage_ratio=Decimal(observed) / Decimal("90"),
        is_sufficient=observed >= 90,
        unpriced_positions_count=0,
        unpriced_tickers=(),
    )


def test_north_star_empty_table(db_session, tmp_path):
    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    ns = result["north_star"]
    assert ns["error"] == "no_snapshots_yet"
    assert ns["value"] is None
    assert ns["coverage_ratio"] == 0
    assert ns["is_sufficient"] is False
    assert ns["data_quality"]["is_complete"] is True


def test_north_star_partial_window(db_session, tmp_path):
    for i in range(12):
        insert_snapshot(db_session, _snap(date(2026, 7, 30) + timedelta(days=i),
                                           observed=i + 1))
    db_session.commit()

    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    ns = result["north_star"]
    assert ns["error"] is None
    assert ns["value"] == 0.032
    assert isinstance(ns["value"], float)  # L17
    assert ns["portfolio_index"] == 1.041
    assert ns["spy_index"] == 1.009
    assert ns["is_sufficient"] is False
    assert ns["trading_days_observed"] == 12
    assert ns["window_start"] == "2026-07-30"
    assert ns["window_end"] == "2026-08-10"
    assert ns["data_quality"]["is_complete"] is True
    assert ns["data_quality"]["unpriced_positions_count"] == 0


def test_north_star_sufficient_window(db_session, tmp_path):
    insert_snapshot(db_session, _snap(date(2026, 8, 14), observed=90))
    db_session.commit()
    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    assert result["north_star"]["is_sufficient"] is True
    assert result["north_star"]["coverage_ratio"] == 1.0


def test_north_star_session_none(tmp_path):
    """L10: session=None → db_session_unavailable fallback."""
    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=None,
    )
    ns = result["north_star"]
    assert ns["error"] == "db_session_unavailable"
    assert ns["value"] is None


def test_north_star_data_quality_is_complete_false(db_session, tmp_path):
    """Snapshot with unpriced positions → is_complete=False."""
    snap = _snap(date(2026, 8, 14), observed=12)
    insert_snapshot(db_session, NavSnapshot(
        **{**snap.__dict__, "unpriced_positions_count": 1,
           "unpriced_tickers": ("XYZ",)},
    ))
    db_session.commit()
    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    dq = result["north_star"]["data_quality"]
    assert dq["is_complete"] is False
    assert dq["unpriced_positions_count"] == 1
    assert dq["unpriced_tickers"] == ["XYZ"]


from marketpulse.db.models import PaperAuditEvent, PaperFill, PaperOrder


def _seed_audit(session, *, ts: datetime, event_type: str):
    session.add(PaperAuditEvent(
        timestamp=ts, event_type=event_type,
        order_id=None, strategy=None, reason="", context={},
    ))


def _seed_entry_fill(session, *, ts: datetime, position_id: int = 1):
    # The fill needs a paper_order parent for FK.
    order = PaperOrder(
        idempotency_key=f"x-{ts.isoformat()}",
        strategy="general", ticker="AAPL", quantity=1,
        event_time=ts, allocation_date=ts.date(),
        horizon_date=ts.date() + timedelta(days=7),
        placed_at=ts, filled_at=ts, cancelled_at=None,
        cancel_reason=None, event_price=Decimal("100"),
        horizon_price=None, status="ENTRY_FILLED",
        strategy_version="v1", allocator_version="v1",
        execution_engine_version="v1", weight=Decimal("1"),
    )
    # PaperOrder may also require allocation_run_id (NOT NULL); if so, set it.
    if hasattr(order, "allocation_run_id"):
        order.allocation_run_id = f"run-{ts.isoformat()}"
    session.add(order)
    session.flush()
    session.add(PaperFill(
        order_id=order.id, position_id=position_id, side="ENTRY",
        price=Decimal("100"), quantity=1, filled_at=ts,
        cash_delta=Decimal("-100"), realized_pnl=None,
    ))


def test_diagnostics_empty_audit(db_session, tmp_path):
    insert_snapshot(db_session, _snap(date(2026, 8, 14)))
    db_session.commit()
    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    diag = result["diagnostics"]
    for key in (
        "tick_success_rate_30d",
        "order_rejection_rate_30d",
        "paper_trade_count_30d",
    ):
        assert diag[key]["value"] is None or diag[key]["value"] == 0, (
            f"{key} expected null or 0 value, got {diag[key]}"
        )
        assert diag[key]["observations"] == 0 or diag[key]["observations"] == 1
        # Note: paper_trade_count_30d observations may equal snapshot count
        # (1) per L13, since it's a count metric with observations = window days
    # tick_success_rate and rejection rate strictly: null value, 0 observations
    assert result["diagnostics"]["tick_success_rate_30d"]["value"] is None
    assert result["diagnostics"]["tick_success_rate_30d"]["observations"] == 0
    assert result["diagnostics"]["order_rejection_rate_30d"]["value"] is None
    assert result["diagnostics"]["order_rejection_rate_30d"]["observations"] == 0
    # paper_trade_count: value=0 (count), observations = snapshot count
    assert result["diagnostics"]["paper_trade_count_30d"]["value"] == 0


def test_diagnostics_tick_success_rate(db_session, tmp_path):
    # 30 snapshots define the window.
    for i in range(30):
        insert_snapshot(db_session, _snap(
            date(2026, 7, 14) + timedelta(days=i), observed=i + 1,
        ))
    # 28 TICK_COMPLETED + 2 ENGINE_INVARIANT_ERROR inside the window.
    base = datetime(2026, 7, 14, tzinfo=UTC)
    for i in range(28):
        _seed_audit(db_session, ts=base + timedelta(days=i),
                    event_type="TICK_COMPLETED")
    for i in range(2):
        _seed_audit(db_session, ts=base + timedelta(days=28 + i),
                    event_type="ENGINE_INVARIANT_ERROR")
    db_session.commit()

    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    diag = result["diagnostics"]["tick_success_rate_30d"]
    assert diag["value"] == 28 / 30
    assert diag["observations"] == 30
    assert diag["required_observations"] == 30
    assert diag["coverage_ratio"] == 1.0
    assert diag["is_sufficient"] is True


def test_diagnostics_rejection_rate_mutually_exclusive(db_session, tmp_path):
    """L12: denominator = PLACED + REJECTED."""
    for i in range(30):
        insert_snapshot(db_session, _snap(
            date(2026, 7, 14) + timedelta(days=i),
        ))
    base = datetime(2026, 7, 14, tzinfo=UTC)
    for i in range(18):
        _seed_audit(db_session, ts=base + timedelta(days=i),
                    event_type="ORDER_PLACED")
    for i in range(12):
        _seed_audit(db_session, ts=base + timedelta(days=18 + i),
                    event_type="ORDER_REJECTED")
    db_session.commit()

    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    diag = result["diagnostics"]["order_rejection_rate_30d"]
    assert diag["value"] == 12 / 30
    assert diag["observations"] == 30


def test_diagnostics_paper_trade_count_via_fills(db_session, tmp_path):
    """L13: source = paper_fill ENTRY rows."""
    for i in range(30):
        insert_snapshot(db_session, _snap(
            date(2026, 7, 14) + timedelta(days=i),
        ))
    base = datetime(2026, 7, 14, tzinfo=UTC)
    # We need a paper_position to satisfy FK on paper_fill. The test
    # uses position_id=1 for all fills; create one position to back it.
    # We'll seed a real position via the existing fixture pattern:
    from marketpulse.db.models import PaperPosition
    order_root = PaperOrder(
        idempotency_key="root", strategy="general", ticker="AAPL", quantity=1,
        event_time=base, allocation_date=base.date(),
        horizon_date=base.date() + timedelta(days=7),
        placed_at=base, filled_at=base, cancelled_at=None,
        cancel_reason=None, event_price=Decimal("100"),
        horizon_price=None, status="ENTRY_FILLED",
        strategy_version="v1", allocator_version="v1",
        execution_engine_version="v1", weight=Decimal("1"),
    )
    if hasattr(order_root, "allocation_run_id"):
        order_root.allocation_run_id = "root-run"
    db_session.add(order_root)
    db_session.flush()
    pos = PaperPosition(
        order_id=order_root.id, entry_fill_id=None, exit_fill_id=None,
        strategy="general", ticker="AAPL", quantity=1,
        entry_price=Decimal("100"), entry_date=base.date(),
        horizon_date=base.date() + timedelta(days=7), status="OPEN",
        opened_at=base, closed_at=None, exit_price=None, realized_pnl=None,
    )
    db_session.add(pos)
    db_session.flush()
    for i in range(5):
        _seed_entry_fill(
            db_session, ts=base + timedelta(days=i), position_id=pos.id,
        )
    db_session.commit()

    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    diag = result["diagnostics"]["paper_trade_count_30d"]
    assert diag["value"] == 5
    assert diag["observations"] == 30  # 30 snapshot trading dates covered


def test_diagnostics_window_from_snapshot_series(db_session, tmp_path):
    """L11: window = last 30 snapshot trading_dates. Events outside excluded."""
    base_day = date(2026, 6, 1)
    for i in range(40):
        insert_snapshot(db_session, _snap(base_day + timedelta(days=i)))
    # Audit event OUTSIDE the 30-most-recent snapshot window (day 0).
    _seed_audit(db_session,
                ts=datetime.combine(base_day, datetime.min.time(), tzinfo=UTC),
                event_type="TICK_COMPLETED")
    # And one INSIDE the window (day 39).
    _seed_audit(db_session,
                ts=datetime.combine(
                    base_day + timedelta(days=39),
                    datetime.min.time(), tzinfo=UTC,
                ),
                event_type="TICK_COMPLETED")
    db_session.commit()

    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    diag = result["diagnostics"]["tick_success_rate_30d"]
    # Only the in-window event should be counted.
    assert diag["value"] == 1.0
    assert diag["observations"] == 1
