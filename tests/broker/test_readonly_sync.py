# Layer: stateful
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from marketpulse.broker.flex_client import FlexReportTimeoutError
from marketpulse.broker.readonly_sync import (
    AccountMismatchError,  # noqa: F401  (re-exported for parity)
    FlexSyncConfig,
    run_readonly_sync,
)
from marketpulse.broker.types import BrokerAccount, BrokerSnapshot
from marketpulse.db.base import Base
from marketpulse.db.models import BrokerAccountSnapshot, BrokerSyncRun


class StubClient:
    """Test double for BrokerReadClient.

    Mirrors the FlexClient surface: exposes a ``reference_code`` attribute
    that orchestration threads through into broker_sync_run.context.
    """

    def __init__(
        self,
        snapshot: BrokerSnapshot | None = None,
        error: Exception | None = None,
        reference_code: str | None = None,
    ):
        self.snapshot = snapshot
        self.error = error
        self.reference_code = reference_code
        self.called = False

    def fetch_snapshot(self) -> BrokerSnapshot:
        self.called = True
        if self.error is not None:
            raise self.error
        assert self.snapshot is not None
        return self.snapshot


# ---- fixtures ----


@pytest.fixture
def session_factory():
    """Returns a callable producing an in-memory SQLite session.

    Usage: ``with session_factory() as session: ...``
    """

    def _factory() -> Session:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return Session(engine)

    return _factory


@pytest.fixture
def make_snapshot():
    """Factory returning a minimal BrokerSnapshot with overridable account_id.

    Environment is derived from the account_id via the Phase 7a-Flex
    classifier (DU* → paper, U* → live, else → unknown), matching what a
    real FlexClient would emit.
    """

    def _factory(account_id: str = "DU123") -> BrokerSnapshot:
        from marketpulse.broker.types import classify_broker_environment_from_account_id

        captured_at = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
        return BrokerSnapshot(
            broker="IBKR",
            broker_environment=classify_broker_environment_from_account_id(account_id),
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

    return _factory


def _count(session: Session, model) -> int:
    return int(session.execute(select(func.count(model.id))).scalar() or 0)


def _paper_config(**overrides) -> FlexSyncConfig:
    base = {
        "token": "t",
        "query_id": 123,
        "base_url": "https://gdcdyn.interactivebrokers.com/Universal/servlet",
        "account_id": "DU123",
        "poll_interval_seconds": 0,
        "max_wait_seconds": 10,
        "allow_live": False,
    }
    base.update(overrides)
    return FlexSyncConfig(**base)


# ---- baseline state-machine tests ----


class TestSyncStateMachine:
    def test_sync_completed_writes_snapshot_and_completed_run(self, session_factory, make_snapshot):
        client = StubClient(snapshot=make_snapshot("DU123"), reference_code="REF42")
        config = _paper_config(account_id="DU123")
        with session_factory() as session:
            result = run_readonly_sync(
                session,
                client=client,
                config=config,
                now=datetime(2026, 5, 23, 21, 0, tzinfo=UTC),
            )
            session.commit()

            assert result.status == "completed"
            assert result.account_id == "DU123"
            assert result.transport == "flex"
            assert result.endpoint == config.base_url
            assert result.query_id == 123
            assert result.reference_code == "REF42"
            assert _count(session, BrokerSyncRun) == 1
            assert _count(session, BrokerAccountSnapshot) == 1
            run = session.get(BrokerSyncRun, result.sync_run_id)
            assert run is not None
            assert run.context["transport"] == "flex"
            assert run.context["endpoint"] == config.base_url
            assert run.context["query_id"] == 123
            assert run.context["selected_account_id"] == "DU123"
            assert run.context["reference_code"] == "REF42"

    def test_connection_failure_writes_failed_run_without_snapshots(self, session_factory):
        client = StubClient(error=ConnectionError("down"))
        config = _paper_config(account_id=None)
        with session_factory() as session:
            result = run_readonly_sync(
                session,
                client=client,
                config=config,
                now=datetime(2026, 5, 23, 21, 0, tzinfo=UTC),
            )
            session.commit()

            assert result.status == "failed"
            assert result.error_type == "ConnectionError"
            assert _count(session, BrokerSyncRun) == 1
            assert _count(session, BrokerAccountSnapshot) == 0
            run = session.get(BrokerSyncRun, result.sync_run_id)
            assert run is not None
            started_at = datetime(2026, 5, 23, 21, 0, tzinfo=UTC)
            assert run.completed_at >= started_at
            assert run.started_at == started_at

    def test_multiple_account_ambiguity_from_client_writes_failed_run(self, session_factory):
        client = StubClient(
            error=RuntimeError("IBKR returned 2 accounts; configure IBKR_ACCOUNT_ID")
        )
        config = _paper_config(account_id=None)
        with session_factory() as session:
            result = run_readonly_sync(
                session,
                client=client,
                config=config,
                now=datetime(2026, 5, 23, 21, 0, tzinfo=UTC),
            )
            session.commit()

            assert result.status == "failed"
            assert result.error_type == "RuntimeError"
            assert "configure IBKR_ACCOUNT_ID" in (result.error_message or "")
            assert _count(session, BrokerSyncRun) == 1
            assert _count(session, BrokerAccountSnapshot) == 0


# ---- Phase 7a-Flex brake tests ----


class TestFlexBrakes:
    def test_live_account_refused(self, session_factory, make_snapshot):
        client = StubClient(snapshot=make_snapshot(account_id="U1234567"))
        config = _paper_config(account_id=None, allow_live=False)
        with session_factory() as session:
            result = run_readonly_sync(session, client=client, config=config)
            session.commit()
            assert result.status == "failed"
            assert result.error_type == "LiveAccountRefusedError"
            assert _count(session, BrokerAccountSnapshot) == 0

    def test_unknown_account_also_refused(self, session_factory, make_snapshot):
        # FOO1 doesn't match DU* or U* → classified "unknown" → also refused (L21).
        client = StubClient(snapshot=make_snapshot(account_id="FOO1"))
        config = _paper_config(account_id=None, allow_live=False)
        with session_factory() as session:
            result = run_readonly_sync(session, client=client, config=config)
            session.commit()
            assert result.status == "failed"
            assert result.error_type == "LiveAccountRefusedError"
            assert _count(session, BrokerAccountSnapshot) == 0

    def test_allow_live_permits_live_account(self, session_factory, make_snapshot):
        client = StubClient(snapshot=make_snapshot(account_id="U1234567"))
        config = _paper_config(account_id=None, allow_live=True)
        with session_factory() as session:
            result = run_readonly_sync(session, client=client, config=config)
            session.commit()
            assert result.status == "completed"
            assert result.broker_environment == "live"
            assert result.account_id == "U1234567"

    def test_account_mismatch(self, session_factory, make_snapshot):
        client = StubClient(snapshot=make_snapshot(account_id="DU1"))
        config = _paper_config(account_id="DU2")
        with session_factory() as session:
            result = run_readonly_sync(session, client=client, config=config)
            session.commit()
            assert result.status == "failed"
            assert result.error_type == "AccountMismatchError"
            assert _count(session, BrokerAccountSnapshot) == 0

    def test_reference_code_preserved_on_failure(self, session_factory):
        """When SendRequest succeeded but GetStatement timed out, the
        reference_code on the FlexReportTimeoutError must surface in both
        the SyncResult and broker_sync_run.context."""
        client = StubClient(
            error=FlexReportTimeoutError("Flex report not ready after 0s", reference_code="ABC")
        )
        config = _paper_config(account_id=None, allow_live=False)
        with session_factory() as session:
            result = run_readonly_sync(session, client=client, config=config)
            session.commit()
            assert result.status == "failed"
            assert result.error_type == "FlexReportTimeoutError"
            assert result.reference_code == "ABC"
            run = session.get(BrokerSyncRun, result.sync_run_id)
            assert run is not None
            assert run.context["reference_code"] == "ABC"
