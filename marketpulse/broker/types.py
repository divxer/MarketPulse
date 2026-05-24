"""Pure broker truth DTOs for read-only sync capture."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

BrokerName = Literal["IBKR"]
BrokerEnvironment = Literal["paper", "live", "unknown"]
SyncStatus = Literal["started", "completed", "failed"]


def classify_broker_environment(port: int) -> BrokerEnvironment:
    if port == 7497:
        return "paper"
    if port == 7496:
        return "live"
    return "unknown"


def classify_broker_environment_from_account_id(account_id: str) -> BrokerEnvironment:
    """Phase 7a-Flex classifier. See L21 in design spec.

    Full implementation in T5 — this stub is so T2 tests can import.
    """
    import re

    if re.fullmatch(r"DU\d+", account_id):
        return "paper"
    if re.fullmatch(r"U\d+", account_id):
        return "live"
    return "unknown"


@dataclass(frozen=True)
class BrokerAccount:
    account_id: str
    account_type: str | None
    base_currency: str | None
    net_liquidation: Decimal | None
    buying_power: Decimal | None
    maintenance_margin: Decimal | None
    excess_liquidity: Decimal | None


@dataclass(frozen=True)
class BrokerCash:
    account_id: str
    currency: str
    cash_balance: Decimal | None
    settled_cash: Decimal | None
    accrued_interest: Decimal | None


@dataclass(frozen=True)
class BrokerPosition:
    account_id: str
    symbol: str
    asset_class: str | None
    quantity: Decimal
    avg_cost: Decimal | None
    market_price: Decimal | None
    market_value: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl: Decimal | None


@dataclass(frozen=True)
class BrokerOpenOrder:
    account_id: str
    broker_order_id: str
    symbol: str | None
    side: str | None
    order_type: str | None
    quantity: Decimal | None
    limit_price: Decimal | None
    status: str | None


@dataclass(frozen=True)
class BrokerExecution:
    account_id: str
    broker_exec_id: str
    broker_order_id: str | None
    symbol: str | None
    side: str | None
    quantity: Decimal | None
    price: Decimal | None
    executed_at: datetime | None


@dataclass(frozen=True)
class BrokerSnapshot:
    broker: BrokerName
    broker_environment: BrokerEnvironment
    account_id: str
    captured_at: datetime
    account: BrokerAccount
    cash: tuple[BrokerCash, ...]
    positions: tuple[BrokerPosition, ...]
    open_orders: tuple[BrokerOpenOrder, ...]
    executions: tuple[BrokerExecution, ...]


@dataclass(frozen=True)
class SyncResult:
    sync_run_id: int
    broker: BrokerName
    broker_environment: BrokerEnvironment
    account_id: str | None
    status: SyncStatus
    host: str
    port: int
    client_id: int
    account_snapshots: int = 0
    cash_rows: int = 0
    positions: int = 0
    open_orders: int = 0
    executions: int = 0
    error_type: str | None = None
    error_message: str | None = None
