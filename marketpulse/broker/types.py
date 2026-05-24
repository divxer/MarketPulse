"""Pure broker truth DTOs for read-only sync capture (Phase 7a-Flex)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

BrokerName = Literal["IBKR"]
BrokerEnvironment = Literal["paper", "live", "unknown"]
SyncStatus = Literal["started", "completed", "failed"]
Transport = Literal["flex"]


_PAPER_RE = re.compile(r"^DU[A-Z]*\d+$")
_LIVE_RE = re.compile(r"^U\d+$")


def classify_broker_environment_from_account_id(account_id: str | None) -> BrokerEnvironment:
    """Classify environment from IBKR account ID prefix (L21).

    DU<optional letters><digits>  → paper   (e.g. DU1234567, DUE411848, DUH123456)
    U<digits>                     → live
    anything else                 → unknown (treated like live by the brake; never falls through)
    """
    if not account_id:
        return "unknown"
    if _PAPER_RE.match(account_id):
        return "paper"
    if _LIVE_RE.match(account_id):
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
    open_orders: tuple[BrokerOpenOrder, ...]  # always () under Flex transport (L18)
    executions: tuple[BrokerExecution, ...]


@dataclass(frozen=True)
class SyncResult:
    """Phase 7a-Flex result. transport-discriminated shape (L20)."""

    sync_run_id: int
    broker: BrokerName
    broker_environment: BrokerEnvironment
    account_id: str | None
    status: SyncStatus
    transport: Transport
    endpoint: str
    query_id: int | None
    reference_code: str | None = None
    account_snapshots: int = 0
    cash_rows: int = 0
    positions: int = 0
    open_orders: int = 0
    executions: int = 0
    error_type: str | None = None
    error_message: str | None = None
