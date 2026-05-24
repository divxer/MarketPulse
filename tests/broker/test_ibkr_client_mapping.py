# Layer: stateful
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal


@dataclass
class FakeContract:
    symbol: str = "AAPL"
    secType: str = "STK"


@dataclass
class FakePosition:
    account: str
    contract: FakeContract
    position: float
    avgCost: float


@dataclass
class FakeAccountValue:
    tag: str
    currency: str
    value: str


class FakeIB:
    def __init__(self) -> None:
        self.connected_kwargs = {}
        self.disconnected = False

    def connect(self, host: str, port: int, *, clientId: int, timeout: int, readonly: bool) -> None:
        self.connected_kwargs = {
            "host": host,
            "port": port,
            "clientId": clientId,
            "timeout": timeout,
            "readonly": readonly,
        }

    def disconnect(self) -> None:
        self.disconnected = True

    def managedAccounts(self) -> list[str]:
        return ["DU123"]

    def accountValues(self, account: str):
        assert account == "DU123"
        return [
            FakeAccountValue("AccountType", "", "INDIVIDUAL"),
            FakeAccountValue("BaseCurrency", "", "EUR"),
            FakeAccountValue("NetLiquidation", "EUR", "100000"),
            FakeAccountValue("BuyingPower", "EUR", "50000"),
        ]

    def positions(self) -> list[FakePosition]:
        return []

    def openTrades(self) -> list:
        return []

    def reqExecutions(self, filt) -> list:
        return []


class MultiAccountFakeIB(FakeIB):
    def managedAccounts(self) -> list[str]:
        return ["DU1", "DU2"]


def test_decimal_conversion_avoids_float_repr_artifacts():
    from marketpulse.broker.ibkr_client import _decimal_or_none

    assert _decimal_or_none(0.1) == Decimal("0.1")
    assert _decimal_or_none(None) is None
    assert _decimal_or_none("nan") is None
    assert _decimal_or_none(Decimal("NaN")) is None
    assert _decimal_or_none(float("inf")) is None
    assert _decimal_or_none("1.7976931348623157E308") is None


def test_position_mapping_returns_pure_dto():
    from marketpulse.broker.ibkr_client import _map_position

    mapped = _map_position(
        FakePosition("DU123", FakeContract("AAPL", "STK"), 3.0, 180.25),
        market_price=190.0,
        market_value=570.0,
        unrealized_pnl=30.0,
        realized_pnl=0.0,
    )

    assert mapped.account_id == "DU123"
    assert mapped.symbol == "AAPL"
    assert mapped.asset_class == "STK"
    assert mapped.quantity == Decimal("3.0")
    assert mapped.avg_cost == Decimal("180.25")
    assert mapped.market_price == Decimal("190.0")
    assert mapped.market_value == Decimal("570.0")
    assert mapped.unrealized_pnl == Decimal("30.0")
    assert mapped.realized_pnl == Decimal("0.0")


def test_execution_window_formatter_uses_utc_ibkr_format():
    from marketpulse.broker.ibkr_client import _ibkr_execution_filter_time

    value = _ibkr_execution_filter_time(datetime(2026, 5, 23, 7, 0, tzinfo=UTC))

    assert value == "20260523 07:00:00"


def test_account_selection_requires_config_when_multiple_accounts_returned():
    from marketpulse.broker.ibkr_client import IbkrReadClient

    client = IbkrReadClient(
        host="127.0.0.1",
        port=7497,
        client_id=71,
        timeout_seconds=10,
        account_id="",
        broker_environment="paper",
        ib=object(),
    )

    try:
        client._select_account(("DU1", "DU2"))
    except RuntimeError as exc:
        assert "configure IBKR_ACCOUNT_ID" in str(exc)
    else:
        raise AssertionError("multiple returned accounts should fail closed")


def test_fetch_snapshot_uses_readonly_connection_without_mutating_api_surface():
    from marketpulse.broker.ibkr_client import IbkrReadClient

    fake_ib = FakeIB()
    assert not hasattr(fake_ib, "placeOrder")
    assert not hasattr(fake_ib, "cancelOrder")

    client = IbkrReadClient(
        host="127.0.0.1",
        port=7497,
        client_id=71,
        timeout_seconds=10,
        account_id="",
        broker_environment="paper",
        ib=fake_ib,
    )

    snapshot = client.fetch_snapshot()

    assert snapshot.account_id == "DU123"
    assert snapshot.account.base_currency == "EUR"
    assert fake_ib.connected_kwargs["readonly"] is True
    assert fake_ib.disconnected is True


def test_fetch_snapshot_disconnects_after_multiple_account_ambiguity():
    from marketpulse.broker.ibkr_client import IbkrReadClient

    fake_ib = MultiAccountFakeIB()
    client = IbkrReadClient(
        host="127.0.0.1",
        port=7497,
        client_id=71,
        timeout_seconds=10,
        account_id="",
        broker_environment="paper",
        ib=fake_ib,
    )

    try:
        client.fetch_snapshot()
    except RuntimeError as exc:
        assert "configure IBKR_ACCOUNT_ID" in str(exc)
    else:
        raise AssertionError("multiple returned accounts should fail closed")

    assert fake_ib.disconnected is True
