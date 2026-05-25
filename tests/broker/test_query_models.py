"""Phase 7a+ Broker Truth Viewer — query model tests."""
# Layer: stateful
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from marketpulse.broker.query_models import load_broker_dashboard
from marketpulse.db.base import Base
from marketpulse.db.models import (
    BrokerAccountSnapshot,
    BrokerCashSnapshot,
    BrokerPositionSnapshot,
    BrokerSyncRun,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _make_run(
    db: Session,
    *,
    started_at: datetime,
    status: str = "completed",
    account_id: str | None = "DU123",
    error_type: str | None = None,
    error_message: str | None = None,
    reference_code: str | None = "REF-1",
    environment: str = "paper",
) -> BrokerSyncRun:
    ctx: dict = {}
    if reference_code:
        ctx["reference_code"] = reference_code
    run = BrokerSyncRun(
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=10)
        if status != "started"
        else None,
        broker="IBKR",
        broker_environment=environment,
        account_id=account_id,
        status=status,
        error_type=error_type,
        error_message=error_message,
        context=ctx,
    )
    db.add(run)
    db.flush()
    return run


def _seed_snapshot(db: Session, run: BrokerSyncRun) -> None:
    common = {
        "sync_run_id": run.id,
        "account_id": run.account_id or "DU123",
        "broker_environment": run.broker_environment,
        "captured_at": run.completed_at or run.started_at,
    }
    db.add(
        BrokerAccountSnapshot(
            **common,
            account_type="INDIVIDUAL",
            base_currency="USD",
            net_liquidation=Decimal("100000.00"),
            buying_power=Decimal("50000.00"),
            maintenance_margin=Decimal("1000.00"),
            excess_liquidity=Decimal("49000.00"),
        )
    )
    db.add(
        BrokerCashSnapshot(
            **common,
            currency="USD",
            cash_balance=Decimal("1000"),
            settled_cash=Decimal("900"),
            accrued_interest=Decimal("0"),
        )
    )
    db.add(
        BrokerPositionSnapshot(
            **common,
            symbol="AAPL",
            asset_class="STK",
            quantity=Decimal("10"),
            avg_cost=Decimal("150"),
            market_price=Decimal("180"),
            market_value=Decimal("1800"),
            unrealized_pnl=Decimal("300"),
            realized_pnl=Decimal("0"),
        )
    )
    db.flush()


def test_empty_returns_empty_dashboard():
    """# Layer: stateful — empty DB produces fully-empty BrokerDashboard."""
    db = _session()
    dash = load_broker_dashboard(db)
    assert dash.latest_run is None
    assert dash.snapshot_run is None
    assert dash.snapshot_is_stale is False
    assert dash.account is None
    assert dash.cash_rows == ()
    assert dash.position_rows == ()
    assert dash.recent_runs == ()


def test_only_failed_runs():
    """# Layer: stateful — only failed runs produce no snapshot_run."""
    db = _session()
    base = datetime(2026, 5, 20, 21, 30, tzinfo=UTC)
    _make_run(db, started_at=base, status="failed", error_type="TimeoutError")
    _make_run(
        db, started_at=base + timedelta(hours=1), status="failed",
        error_type="AuthError",
    )
    dash = load_broker_dashboard(db)
    assert dash.latest_run is not None
    assert dash.latest_run.status == "failed"
    assert dash.latest_run.error_type == "AuthError"
    assert dash.snapshot_run is None
    assert dash.snapshot_is_stale is False
    assert dash.account is None
    assert len(dash.recent_runs) == 2


def test_latest_completed_drives_snapshot():
    """# Layer: stateful — happy path: latest completed run sources snapshot."""
    db = _session()
    base = datetime(2026, 5, 22, 21, 30, tzinfo=UTC)
    run = _make_run(db, started_at=base, status="completed")
    _seed_snapshot(db, run)
    dash = load_broker_dashboard(db)
    assert dash.latest_run is not None
    assert dash.snapshot_run is not None
    assert dash.latest_run.id == dash.snapshot_run.id
    assert dash.snapshot_is_stale is False
    assert dash.account is not None
    assert dash.account.net_liquidation == Decimal("100000.00")
    assert len(dash.cash_rows) == 1
    assert dash.cash_rows[0].currency == "USD"
    assert len(dash.position_rows) == 1
    assert dash.position_rows[0].symbol == "AAPL"


def test_failed_after_completed_uses_fallback():
    """# Layer: stateful — failed-latest fallback to prior completed snapshot."""
    db = _session()
    base = datetime(2026, 5, 22, 21, 30, tzinfo=UTC)
    completed_a = _make_run(db, started_at=base, status="completed")
    _seed_snapshot(db, completed_a)
    failed_b = _make_run(
        db, started_at=base + timedelta(hours=24), status="failed",
        error_type="FlexReportTimeoutError", account_id=None,
    )
    dash = load_broker_dashboard(db)
    assert dash.latest_run.id == failed_b.id
    assert dash.latest_run.status == "failed"
    assert dash.snapshot_run is not None
    assert dash.snapshot_run.id == completed_a.id
    assert dash.snapshot_is_stale is True
    assert dash.account is not None
    assert dash.account.net_liquidation == Decimal("100000.00")


def test_recent_runs_includes_failures():
    """# Layer: stateful — recent_runs lists last N regardless of status."""
    db = _session()
    base = datetime(2026, 5, 18, 21, 30, tzinfo=UTC)
    statuses = ["completed", "failed", "completed", "failed", "completed"]
    for i, st in enumerate(statuses):
        _make_run(db, started_at=base + timedelta(hours=i), status=st)
    dash = load_broker_dashboard(db)
    assert len(dash.recent_runs) == 5
    # Ordered desc by started_at
    starts = [r.started_at for r in dash.recent_runs]
    assert starts == sorted(starts, reverse=True)
    assert {r.status for r in dash.recent_runs} == {"completed", "failed"}
