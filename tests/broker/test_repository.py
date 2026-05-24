# Layer: stateful
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from marketpulse.broker.types import (
    BrokerAccount,
    BrokerCash,
    BrokerExecution,
    BrokerOpenOrder,
    BrokerPosition,
    BrokerSnapshot,
)
from marketpulse.db.base import Base
from marketpulse.db.models import (
    BrokerAccountSnapshot,
    BrokerCashSnapshot,
    BrokerExecutionSnapshot,
    BrokerOpenOrderSnapshot,
    BrokerPositionSnapshot,
    BrokerSyncRun,
    PaperCashLedger,
    PaperFill,
    PaperOrder,
    PaperPosition,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _snapshot(account_id: str = "DU123") -> BrokerSnapshot:
    captured_at = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    return BrokerSnapshot(
        broker="IBKR",
        broker_environment="paper",
        account_id=account_id,
        captured_at=captured_at,
        account=BrokerAccount(
            account_id=account_id,
            account_type="INDIVIDUAL",
            base_currency="USD",
            net_liquidation=Decimal("100000.00"),
            buying_power=Decimal("50000.00"),
            maintenance_margin=Decimal("1000.00"),
            excess_liquidity=Decimal("49000.00"),
        ),
        cash=(
            BrokerCash(account_id, "USD", Decimal("1000"), Decimal("900"), Decimal("0")),
        ),
        positions=(
            BrokerPosition(
                account_id,
                "AAPL",
                "STK",
                Decimal("3"),
                Decimal("180.00"),
                Decimal("190.00"),
                Decimal("570.00"),
                Decimal("30.00"),
                Decimal("0.00"),
            ),
        ),
        open_orders=(
            BrokerOpenOrder(
                account_id,
                "1001",
                "MSFT",
                "BUY",
                "LMT",
                Decimal("2"),
                Decimal("300.00"),
                "Submitted",
            ),
        ),
        executions=(
            BrokerExecution(
                account_id,
                "E123",
                "1000",
                "NVDA",
                "BOT",
                Decimal("1"),
                Decimal("900.00"),
                captured_at,
            ),
        ),
    )


def _count(session: Session, model) -> int:
    return int(session.execute(select(func.count(model.id))).scalar() or 0)


def test_repository_writes_started_completed_and_snapshots():
    from marketpulse.broker.repository import (
        create_started_run,
        mark_run_completed,
        persist_snapshot_rows,
    )

    session = _session()
    started_at = datetime(2026, 5, 23, 21, 0, tzinfo=UTC)
    run = create_started_run(
        session,
        started_at=started_at,
        broker="IBKR",
        broker_environment="paper",
        account_id=None,
        context={"host": "127.0.0.1"},
    )
    snapshot = _snapshot()

    counts = persist_snapshot_rows(session, sync_run_id=run.id, snapshot=snapshot)
    mark_run_completed(
        session,
        sync_run_id=run.id,
        completed_at=snapshot.captured_at,
        account_id="DU123",
    )
    session.commit()

    saved = session.get(BrokerSyncRun, run.id)
    assert saved is not None
    assert saved.status == "completed"
    assert saved.account_id == "DU123"
    assert counts == {
        "account_snapshots": 1,
        "cash_rows": 1,
        "positions": 1,
        "open_orders": 1,
        "executions": 1,
    }
    assert _count(session, BrokerAccountSnapshot) == 1
    assert _count(session, BrokerCashSnapshot) == 1
    assert _count(session, BrokerPositionSnapshot) == 1
    assert _count(session, BrokerOpenOrderSnapshot) == 1
    assert _count(session, BrokerExecutionSnapshot) == 1


def test_repository_marks_failed_without_snapshot_rows():
    from marketpulse.broker.repository import create_started_run, mark_run_failed

    session = _session()
    started_at = datetime(2026, 5, 23, 21, 0, tzinfo=UTC)
    run = create_started_run(
        session,
        started_at=started_at,
        broker="IBKR",
        broker_environment="unknown",
        account_id=None,
        context={"host": "127.0.0.1"},
    )
    mark_run_failed(
        session,
        sync_run_id=run.id,
        completed_at=started_at,
        error_type="ConnectionError",
        error_message="cannot connect",
        context_patch={"port": 7497},
    )
    session.commit()

    saved = session.get(BrokerSyncRun, run.id)
    assert saved is not None
    assert saved.status == "failed"
    assert saved.error_type == "ConnectionError"
    assert saved.context["port"] == 7497
    assert _count(session, BrokerAccountSnapshot) == 0


def test_repository_append_only_and_does_not_touch_paper_tables():
    from marketpulse.broker.repository import (
        create_started_run,
        mark_run_completed,
        persist_snapshot_rows,
    )

    session = _session()
    before = {
        "paper_order": _count(session, PaperOrder),
        "paper_fill": _count(session, PaperFill),
        "paper_position": _count(session, PaperPosition),
        "paper_cash_ledger": _count(session, PaperCashLedger),
    }
    for minute in (0, 5):
        captured_at = datetime(2026, 5, 23, 21, minute, tzinfo=UTC)
        snapshot = _snapshot()
        run = create_started_run(
            session,
            started_at=captured_at,
            broker="IBKR",
            broker_environment="paper",
            account_id=None,
            context={},
        )
        persist_snapshot_rows(session, sync_run_id=run.id, snapshot=snapshot)
        mark_run_completed(
            session,
            sync_run_id=run.id,
            completed_at=captured_at,
            account_id="DU123",
        )
    session.commit()

    assert _count(session, BrokerSyncRun) == 2
    assert _count(session, BrokerPositionSnapshot) == 2
    after = {
        "paper_order": _count(session, PaperOrder),
        "paper_fill": _count(session, PaperFill),
        "paper_position": _count(session, PaperPosition),
        "paper_cash_ledger": _count(session, PaperCashLedger),
    }
    assert after == before


def test_repository_rejects_child_rows_for_different_account():
    from marketpulse.broker.repository import create_started_run, persist_snapshot_rows

    session = _session()
    started_at = datetime(2026, 5, 23, 21, 0, tzinfo=UTC)
    run = create_started_run(
        session,
        started_at=started_at,
        broker="IBKR",
        broker_environment="paper",
        account_id=None,
        context={},
    )
    snapshot = _snapshot("DU123")
    bad_snapshot = BrokerSnapshot(
        broker=snapshot.broker,
        broker_environment=snapshot.broker_environment,
        account_id=snapshot.account_id,
        captured_at=snapshot.captured_at,
        account=snapshot.account,
        cash=(BrokerCash("DU999", "USD", Decimal("1"), None, None),),
        positions=snapshot.positions,
        open_orders=snapshot.open_orders,
        executions=snapshot.executions,
    )

    try:
        persist_snapshot_rows(session, sync_run_id=run.id, snapshot=bad_snapshot)
    except ValueError as exc:
        assert "snapshot child account mismatch" in str(exc)
    else:
        raise AssertionError("mixed-account snapshot rows should be rejected")
