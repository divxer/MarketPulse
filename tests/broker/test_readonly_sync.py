from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from marketpulse.broker.types import BrokerAccount, BrokerSnapshot
from marketpulse.db.base import Base
from marketpulse.db.models import BrokerAccountSnapshot, BrokerSyncRun


class FakeClient:
    def __init__(self, snapshot: BrokerSnapshot | None = None, error: Exception | None = None):
        self.snapshot = snapshot
        self.error = error
        self.called = False

    def fetch_snapshot(self) -> BrokerSnapshot:
        self.called = True
        if self.error:
            raise self.error
        assert self.snapshot is not None
        return self.snapshot


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
            net_liquidation=Decimal("100000"),
            buying_power=Decimal("50000"),
            maintenance_margin=None,
            excess_liquidity=None,
        ),
        cash=(),
        positions=(),
        open_orders=(),
        executions=(),
    )


def _count(session: Session, model) -> int:
    return int(session.execute(select(func.count(model.id))).scalar() or 0)


def test_sync_completed_writes_snapshot_and_completed_run():
    from marketpulse.broker.readonly_sync import IbkrSyncConfig, run_readonly_sync

    session = _session()
    config = IbkrSyncConfig(host="127.0.0.1", port=7497, client_id=71, account_id="DU123",
                            timeout_seconds=10, allow_live=False)
    result = run_readonly_sync(session, client=FakeClient(_snapshot()), config=config,
                               now=datetime(2026, 5, 23, 21, 0, tzinfo=UTC))
    session.commit()

    assert result.status == "completed"
    assert result.account_id == "DU123"
    assert _count(session, BrokerSyncRun) == 1
    assert _count(session, BrokerAccountSnapshot) == 1
    run = session.get(BrokerSyncRun, result.sync_run_id)
    assert run is not None
    assert run.context["host"] == "127.0.0.1"
    assert run.context["execution_window_start"] is not None
    assert run.context["selected_account_id"] == "DU123"


def test_connection_failure_writes_failed_run_without_snapshots():
    from marketpulse.broker.readonly_sync import IbkrSyncConfig, run_readonly_sync

    session = _session()
    config = IbkrSyncConfig(host="127.0.0.1", port=7497, client_id=71, account_id="",
                            timeout_seconds=10, allow_live=False)
    result = run_readonly_sync(session, client=FakeClient(error=ConnectionError("down")),
                               config=config, now=datetime(2026, 5, 23, 21, 0, tzinfo=UTC))
    session.commit()

    assert result.status == "failed"
    assert result.error_type == "ConnectionError"
    assert _count(session, BrokerSyncRun) == 1
    assert _count(session, BrokerAccountSnapshot) == 0
    run = session.get(BrokerSyncRun, result.sync_run_id)
    assert run is not None
    assert run.completed_at == datetime(2026, 5, 23, 21, 0, tzinfo=UTC)


def test_multiple_account_ambiguity_from_client_writes_failed_run_without_snapshots():
    from marketpulse.broker.readonly_sync import IbkrSyncConfig, run_readonly_sync

    session = _session()
    config = IbkrSyncConfig(host="127.0.0.1", port=7497, client_id=71, account_id="",
                            timeout_seconds=10, allow_live=False)
    result = run_readonly_sync(
        session,
        client=FakeClient(
            error=RuntimeError("IBKR returned 2 accounts; configure IBKR_ACCOUNT_ID")
        ),
        config=config,
        now=datetime(2026, 5, 23, 21, 0, tzinfo=UTC),
    )
    session.commit()

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"
    assert "configure IBKR_ACCOUNT_ID" in (result.error_message or "")
    assert _count(session, BrokerSyncRun) == 1
    assert _count(session, BrokerAccountSnapshot) == 0


def test_account_mismatch_fails_closed_without_snapshots():
    from marketpulse.broker.readonly_sync import IbkrSyncConfig, run_readonly_sync

    session = _session()
    config = IbkrSyncConfig(host="127.0.0.1", port=7497, client_id=71, account_id="DU999",
                            timeout_seconds=10, allow_live=False)
    result = run_readonly_sync(session, client=FakeClient(_snapshot("DU123")), config=config,
                               now=datetime(2026, 5, 23, 21, 0, tzinfo=UTC))
    session.commit()

    assert result.status == "failed"
    assert result.error_type == "AccountMismatchError"
    assert _count(session, BrokerAccountSnapshot) == 0


def test_live_port_block_fails_before_fetching_snapshot():
    from marketpulse.broker.readonly_sync import IbkrSyncConfig, run_readonly_sync

    session = _session()
    client = FakeClient(_snapshot())
    config = IbkrSyncConfig(host="127.0.0.1", port=7496, client_id=71, account_id="",
                            timeout_seconds=10, allow_live=False)
    result = run_readonly_sync(session, client=client, config=config,
                               now=datetime(2026, 5, 23, 21, 0, tzinfo=UTC))
    session.commit()

    assert result.status == "failed"
    assert result.error_type == "LivePortBlockedError"
    assert client.called is False
    assert _count(session, BrokerSyncRun) == 1
    assert _count(session, BrokerAccountSnapshot) == 0
