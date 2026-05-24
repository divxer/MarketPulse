# Layer: pure
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from marketpulse.broker.types import (
    SyncResult,
    classify_broker_environment_from_account_id,
)


def test_broker_read_client_protocol_only_exposes_fetch_snapshot():
    from marketpulse.broker.read_client import BrokerReadClient

    # Use __dict__ instead of typing.get_protocol_members so the test stays
    # compatible with Python 3.12 while still catching Protocol surface drift.
    own_methods = {
        key
        for key, value in BrokerReadClient.__dict__.items()
        if callable(value) and not key.startswith("_")
    }
    assert own_methods == {"fetch_snapshot"}


def test_broker_snapshot_is_pure_dataclass():
    from marketpulse.broker.types import (
        BrokerAccount,
        BrokerCash,
        BrokerSnapshot,
    )

    captured_at = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    snapshot = BrokerSnapshot(
        broker="IBKR",
        broker_environment="paper",
        account_id="DU123",
        captured_at=captured_at,
        account=BrokerAccount(
            account_id="DU123",
            account_type="INDIVIDUAL",
            base_currency="USD",
            net_liquidation=Decimal("100000.00"),
            buying_power=Decimal("50000.00"),
            maintenance_margin=None,
            excess_liquidity=None,
        ),
        cash=(
            BrokerCash(
                account_id="DU123",
                currency="USD",
                cash_balance=Decimal("1000.00"),
                settled_cash=Decimal("900.00"),
                accrued_interest=Decimal("0.00"),
            ),
        ),
        positions=(),
        open_orders=(),
        executions=(),
    )

    assert snapshot.broker == "IBKR"
    assert snapshot.cash[0].cash_balance == Decimal("1000.00")


class TestClassifier:
    @pytest.mark.parametrize(
        "aid,expected",
        [
            ("DU1234567", "paper"),
            ("DU99999999", "paper"),
            ("U1234567", "live"),
            ("U1", "live"),
            ("", "unknown"),
            (None, "unknown"),
            ("FOO123", "unknown"),
            ("DUabc", "unknown"),
            ("DU", "unknown"),
            ("UA1234", "unknown"),
        ],
    )
    def test_classifier(self, aid, expected):
        assert classify_broker_environment_from_account_id(aid) == expected


def test_sync_result_has_transport_shape():
    sr = SyncResult(
        sync_run_id=1,
        broker="IBKR",
        broker_environment="paper",
        account_id="DU1",
        status="completed",
        transport="flex",
        endpoint="https://gdcdyn.interactivebrokers.com/Universal/servlet",
        query_id=123,
        reference_code="ref",
    )
    assert sr.transport == "flex"
    assert sr.query_id == 123
    assert not hasattr(sr, "host")  # L20: removed
    assert not hasattr(sr, "port")
    assert not hasattr(sr, "client_id")
