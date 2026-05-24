# Layer: pure
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal


def test_broker_read_client_protocol_only_exposes_fetch_snapshot():
    from marketpulse.broker.read_client import BrokerReadClient

    # Use __dict__ instead of typing.get_protocol_members so the test stays
    # compatible with Python 3.12 while still catching Protocol surface drift.
    own_methods = {
        key for key, value in BrokerReadClient.__dict__.items()
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


def test_classify_broker_environment_blocks_known_live_port():
    from marketpulse.broker.types import classify_broker_environment

    assert classify_broker_environment(7497) == "paper"
    assert classify_broker_environment(7496) == "live"
    assert classify_broker_environment(4002) == "unknown"
