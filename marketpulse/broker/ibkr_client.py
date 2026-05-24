"""IBKR read-only adapter.

This is the only Phase 7a module allowed to import the IBKR Python SDK.
We use `ib_async` (https://github.com/ib-api-reloaded/ib_async), the
actively-maintained fork of the unmaintained `ib_insync` library.
API surface is identical — same module names, same classes — so the
swap is a one-line import change.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ib_async import IB, ExecutionFilter

from marketpulse.broker.types import (
    BrokerAccount,
    BrokerCash,
    BrokerEnvironment,
    BrokerExecution,
    BrokerOpenOrder,
    BrokerPosition,
    BrokerSnapshot,
)

IBKR_UNSET_DOUBLE = Decimal("1.7976931348623157E308")


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value)
    if text in {"", "nan", "NaN", "inf", "Infinity", "-inf", "-Infinity"}:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    decimal = Decimal(text)
    if not decimal.is_finite():
        return None
    if decimal.copy_abs() >= IBKR_UNSET_DOUBLE:
        return None
    return decimal


def _ibkr_execution_filter_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%d %H:%M:%S")


def _map_position(
    position: Any,
    *,
    market_price: Any = None,
    market_value: Any = None,
    unrealized_pnl: Any = None,
    realized_pnl: Any = None,
) -> BrokerPosition:
    contract = position.contract
    return BrokerPosition(
        account_id=str(position.account),
        symbol=str(getattr(contract, "symbol", "")),
        asset_class=getattr(contract, "secType", None),
        quantity=_decimal_or_none(position.position) or Decimal("0"),
        avg_cost=_decimal_or_none(position.avgCost),
        market_price=_decimal_or_none(market_price),
        market_value=_decimal_or_none(market_value),
        unrealized_pnl=_decimal_or_none(unrealized_pnl),
        realized_pnl=_decimal_or_none(realized_pnl),
    )


class IbkrReadClient:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        client_id: int,
        timeout_seconds: int,
        account_id: str = "",
        broker_environment: BrokerEnvironment = "unknown",
        execution_window_start: datetime | None = None,
        ib: IB | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.timeout_seconds = timeout_seconds
        self.account_id = account_id
        self.broker_environment = broker_environment
        self.execution_window_start = execution_window_start
        self._ib = ib or IB()

    def fetch_snapshot(self) -> BrokerSnapshot:
        captured_at = datetime.now(UTC)
        self._ib.connect(
            self.host,
            self.port,
            clientId=self.client_id,
            timeout=self.timeout_seconds,
            readonly=True,
        )
        try:
            accounts = tuple(self._ib.managedAccounts())
            account_id = self._select_account(accounts)
            account_values = self._ib.accountValues(account_id)
            account = self._map_account(account_id, account_values)
            cash = self._map_cash(account_id, account_values)
            positions = tuple(
                _map_position(p) for p in self._ib.positions() if p.account == account_id
            )
            open_orders = tuple(
                self._map_open_order(item, account_id) for item in self._ib.openTrades()
            )
            executions = self._fetch_executions(account_id)
            return BrokerSnapshot(
                broker="IBKR",
                broker_environment=self.broker_environment,
                account_id=account_id,
                captured_at=captured_at,
                account=account,
                cash=cash,
                positions=positions,
                open_orders=open_orders,
                executions=executions,
            )
        finally:
            self._ib.disconnect()

    def _select_account(self, accounts: tuple[str, ...]) -> str:
        if self.account_id:
            if self.account_id not in accounts:
                raise RuntimeError(
                    f"Configured account {self.account_id} not returned by IBKR"
                )
            return self.account_id
        if len(accounts) == 1:
            return accounts[0]
        raise RuntimeError(
            f"IBKR returned {len(accounts)} accounts; configure IBKR_ACCOUNT_ID"
        )

    def _map_account(self, account_id: str, values: list[Any]) -> BrokerAccount:
        by_tag = {(v.tag, v.currency): v.value for v in values}
        currencies = sorted({v.currency for v in values if v.currency})
        base_currency = (
            by_tag.get(("BaseCurrency", ""))
            or by_tag.get(("Currency", ""))
            or (currencies[0] if len(currencies) == 1 else None)
            or "USD"
        )
        return BrokerAccount(
            account_id=account_id,
            account_type=by_tag.get(("AccountType", "")),
            base_currency=base_currency,
            net_liquidation=_decimal_or_none(by_tag.get(("NetLiquidation", base_currency))),
            buying_power=_decimal_or_none(by_tag.get(("BuyingPower", base_currency))),
            maintenance_margin=_decimal_or_none(by_tag.get(("MaintMarginReq", base_currency))),
            excess_liquidity=_decimal_or_none(by_tag.get(("ExcessLiquidity", base_currency))),
        )

    def _map_cash(self, account_id: str, values: list[Any]) -> tuple[BrokerCash, ...]:
        by_tag = {(v.tag, v.currency): v.value for v in values}
        cash_tags = {"TotalCashBalance", "SettledCash", "AccruedCash"}
        currencies = sorted(
            {v.currency for v in values if v.currency and v.tag in cash_tags}
        )
        rows: list[BrokerCash] = []
        for currency in currencies:
            rows.append(
                BrokerCash(
                    account_id=account_id,
                    currency=currency,
                    cash_balance=_decimal_or_none(by_tag.get(("TotalCashBalance", currency))),
                    settled_cash=_decimal_or_none(by_tag.get(("SettledCash", currency))),
                    accrued_interest=_decimal_or_none(by_tag.get(("AccruedCash", currency))),
                )
            )
        return tuple(rows)

    def _map_open_order(self, trade: Any, account_id: str) -> BrokerOpenOrder:
        order = trade.order
        contract = trade.contract
        status = getattr(trade.orderStatus, "status", None)
        return BrokerOpenOrder(
            account_id=account_id,
            broker_order_id=str(order.orderId),
            symbol=getattr(contract, "symbol", None),
            side=getattr(order, "action", None),
            order_type=getattr(order, "orderType", None),
            quantity=_decimal_or_none(getattr(order, "totalQuantity", None)),
            limit_price=_decimal_or_none(getattr(order, "lmtPrice", None)),
            status=status,
        )

    def _fetch_executions(self, account_id: str) -> tuple[BrokerExecution, ...]:
        filt = ExecutionFilter(acctCode=account_id)
        if self.execution_window_start is not None:
            filt.time = _ibkr_execution_filter_time(self.execution_window_start)
        rows = []
        for fill in self._ib.reqExecutions(filt):
            execution = fill.execution
            contract = fill.contract
            broker_order_id = (
                str(execution.orderId) if execution.orderId is not None else None
            )
            rows.append(
                BrokerExecution(
                    account_id=account_id,
                    broker_exec_id=str(execution.execId),
                    broker_order_id=broker_order_id,
                    symbol=getattr(contract, "symbol", None),
                    side=getattr(execution, "side", None),
                    quantity=_decimal_or_none(getattr(execution, "shares", None)),
                    price=_decimal_or_none(getattr(execution, "price", None)),
                    executed_at=getattr(execution, "time", None),
                )
            )
        return tuple(rows)
