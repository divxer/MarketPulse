"""IBKR read-only adapter.

This is the only Phase 7a module allowed to import ``ibapi``.

Why the official IBKR Python SDK:
    * Phase 7a is read-only today, but Phase 7+ will gain write surface
      (place / modify / cancel orders against real money).
    * Write operations require IBKR's official support channel and
      same-day TWS API protocol-update parity — both of which only
      ship via the official ``ibapi`` Python package.
    * Doing the SDK swap before any write surface exists keeps the
      architecture defensible and avoids a forced migration later.

Thread model:
    ``ibapi`` is callback-driven. ``EClient.connect`` returns immediately;
    the reader thread (``EClient.run``) blocks on the socket and
    dispatches ``EWrapper.*`` callbacks. The main thread issues ``reqXxx``
    calls and waits on ``threading.Event`` flags for the corresponding
    ``*End`` callbacks. Every wait has a timeout — we never hang.

Read-only enforcement:
    The official SDK has no client-side ``readonly=True`` flag on
    ``connect``. Read-only enforcement lives in TWS:
    Global Config → API → Precautions → "Read-Only API". The
    operations runbook spells this out for operators.
"""

from __future__ import annotations

import contextlib
import math
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ibapi.client import EClient
from ibapi.common import OrderId, TickerId
from ibapi.contract import Contract
from ibapi.execution import Execution, ExecutionFilter
from ibapi.order import Order
from ibapi.order_state import OrderState
from ibapi.wrapper import EWrapper

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

# IBKR error codes >= 1100 indicate connectivity / protocol problems we
# must treat as fatal. Lower codes (e.g. 2104/2106/2158 farm-status
# notices) are informational and ignored.
_FATAL_ERROR_FLOOR = 1100

# Known-fatal IBKR error codes below the 1100 floor. These signal real
# connection / authentication / protocol problems that should surface
# immediately rather than falling through to a timeout. Codes >= 1100
# (e.g. 1100 connectivity lost) are always fatal regardless.
_FATAL_SUB_1100_ERROR_CODES = frozenset({
    502,    # Couldn't connect to TWS
    504,    # Not connected
    1300,   # Socket port has been reset and is now in use
    10182,  # Failed to request live updates (no subscription)
})

# Unique reqId for reqExecutions; do not collide with future long-lived reqIds.
_EXEC_REQ_ID = 9001


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


class IbkrApiError(RuntimeError):
    """Fatal error reported via the EWrapper.error callback."""


class IbkrTimeoutError(RuntimeError):
    """A wait-for-end callback timed out."""


class _PositionRow:
    """Lightweight position row passed to ``_map_position``."""

    __slots__ = ("account", "contract", "position", "avgCost")

    def __init__(self, account: str, contract: Contract, position: float, avg_cost: float):
        self.account = account
        self.contract = contract
        self.position = position
        self.avgCost = avg_cost


class _AccountValueRow:
    __slots__ = ("tag", "value", "currency", "account")

    def __init__(self, tag: str, value: str, currency: str, account: str):
        self.tag = tag
        self.value = value
        self.currency = currency
        self.account = account


class _OpenOrderRow:
    __slots__ = ("orderId", "contract", "order", "orderState")

    def __init__(
        self,
        order_id: int,
        contract: Contract,
        order: Order,
        order_state: OrderState,
    ):
        self.orderId = order_id
        self.contract = contract
        self.order = order
        self.orderState = order_state


class _ExecutionRow:
    __slots__ = ("contract", "execution")

    def __init__(self, contract: Contract, execution: Execution):
        self.contract = contract
        self.execution = execution


class _IbReader(EWrapper, EClient):
    """EWrapper+EClient subclass that buffers callbacks for the main thread.

    The reader thread (started after ``connect``) calls back into this
    object; we just append to lists and set ``threading.Event`` flags.
    The main thread reads those buffers once the corresponding ``*End``
    event fires (or the timeout expires).
    """

    def __init__(self) -> None:
        EClient.__init__(self, wrapper=self)
        self.ready_event = threading.Event()
        self.managed_accounts_event = threading.Event()
        self.account_download_end_event = threading.Event()
        self.position_end_event = threading.Event()
        self.open_order_end_event = threading.Event()
        self.exec_details_end_events: dict[int, threading.Event] = {}

        self.managed_accounts_text: str = ""
        self.account_values: list[_AccountValueRow] = []
        self.positions_buffer: list[_PositionRow] = []
        self.open_orders_buffer: list[_OpenOrderRow] = []
        self.executions_buffer: dict[int, list[_ExecutionRow]] = {}

        self.fatal_error: IbkrApiError | None = None

    # --- EWrapper callbacks (invoked on the reader thread) ---

    def nextValidId(self, orderId: OrderId) -> None:  # noqa: N802 (ibapi name)
        self.ready_event.set()

    def managedAccounts(self, accountsList: str) -> None:  # noqa: N802, N803
        self.managed_accounts_text = accountsList or ""
        self.managed_accounts_event.set()

    def updateAccountValue(  # noqa: N802
        self, key: str, val: str, currency: str, accountName: str  # noqa: N803
    ) -> None:
        self.account_values.append(
            _AccountValueRow(tag=key, value=val, currency=currency or "", account=accountName or "")
        )

    def accountDownloadEnd(self, accountName: str) -> None:  # noqa: N802, N803
        self.account_download_end_event.set()

    def position(  # noqa: D401
        self, account: str, contract: Contract, position: float, avgCost: float  # noqa: N803
    ) -> None:
        self.positions_buffer.append(
            _PositionRow(account=account, contract=contract, position=position, avg_cost=avgCost)
        )

    def positionEnd(self) -> None:  # noqa: N802
        self.position_end_event.set()

    def openOrder(  # noqa: N802
        self,
        orderId: OrderId,  # noqa: N803
        contract: Contract,
        order: Order,
        orderState: OrderState,  # noqa: N803
    ) -> None:
        self.open_orders_buffer.append(
            _OpenOrderRow(order_id=orderId, contract=contract, order=order, order_state=orderState)
        )

    def openOrderEnd(self) -> None:  # noqa: N802
        self.open_order_end_event.set()

    def execDetails(  # noqa: N802
        self, reqId: int, contract: Contract, execution: Execution  # noqa: N803
    ) -> None:
        self.executions_buffer.setdefault(reqId, []).append(
            _ExecutionRow(contract=contract, execution=execution)
        )

    def execDetailsEnd(self, reqId: int) -> None:  # noqa: N802, N803
        evt = self.exec_details_end_events.setdefault(reqId, threading.Event())
        evt.set()

    def error(  # noqa: D401
        self,
        reqId: TickerId,  # noqa: N803
        errorCode: int,  # noqa: N803
        errorString: str,  # noqa: N803
        advancedOrderRejectJson: str = "",  # noqa: N803
    ) -> None:
        if errorCode >= _FATAL_ERROR_FLOOR or errorCode in _FATAL_SUB_1100_ERROR_CODES:
            self.fatal_error = IbkrApiError(f"IBKR error {errorCode}: {errorString}")
            # Unblock anything that's currently waiting.
            self.ready_event.set()
            self.managed_accounts_event.set()
            self.account_download_end_event.set()
            self.position_end_event.set()
            self.open_order_end_event.set()
            for evt in self.exec_details_end_events.values():
                evt.set()


def _default_client_factory() -> _IbReader:
    return _IbReader()


class IbkrReadClient:
    """Read-only IBKR snapshot adapter built on the official ``ibapi`` SDK.

    Public API is unchanged from the previous adapter implementation so
    the orchestrator and CLI need no edits.

    Read-only is enforced by TWS configuration (see runbook); this
    adapter additionally never invokes any mutating ``ibapi`` method
    (order placement, cancellation, modification, global cancel,
    or option exercise), which is also enforced by an architecture
    guard test in ``tests/architecture``.
    """

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
        client_factory: Callable[[], _IbReader] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.timeout_seconds = timeout_seconds
        self.account_id = account_id
        self.broker_environment = broker_environment
        self.execution_window_start = execution_window_start
        self._client_factory = client_factory or _default_client_factory

    # --- public ----------------------------------------------------------

    def fetch_snapshot(self) -> BrokerSnapshot:
        """Fetch a full broker snapshot.

        Single-shot per call -- the adapter performs one full connect /
        fetch / disconnect cycle. Calling ``fetch_snapshot`` multiple
        times on the same instance is supported, but each call rebuilds
        internal state.
        """
        captured_at = datetime.now(UTC)
        client = self._client_factory()
        reader_thread: threading.Thread | None = None
        try:
            client.connect(self.host, self.port, self.client_id)
            reader_thread = threading.Thread(target=client.run, daemon=True)
            reader_thread.start()

            self._wait(client, client.ready_event, "nextValidId (connection ready)")
            self._wait(client, client.managed_accounts_event, "managedAccounts")

            accounts = tuple(
                a for a in (s.strip() for s in client.managed_accounts_text.split(",")) if a
            )
            account_id = self._select_account(accounts)

            account_values = self._fetch_account_values(client, account_id)
            account = self._map_account(account_id, account_values)
            cash = self._map_cash(account_id, account_values)
            positions_all = self._fetch_positions(client)
            positions = tuple(_map_position(p) for p in positions_all if p.account == account_id)
            open_orders = self._fetch_open_orders(client, account_id)
            executions = self._fetch_executions(client, account_id)

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
            with contextlib.suppress(Exception):
                client.disconnect()
            if reader_thread is not None and reader_thread.is_alive():
                reader_thread.join(timeout=self.timeout_seconds)

    # --- request orchestration ------------------------------------------

    def _wait(self, client: _IbReader, event: threading.Event, label: str) -> None:
        if not event.wait(timeout=self.timeout_seconds):
            raise IbkrTimeoutError(
                f"Timed out after {self.timeout_seconds}s waiting for {label}"
            )
        if client.fatal_error is not None:
            raise client.fatal_error

    def _fetch_account_values(
        self, client: _IbReader, account_id: str
    ) -> list[_AccountValueRow]:
        client.account_values.clear()
        client.account_download_end_event.clear()
        client.reqAccountUpdates(True, account_id)
        try:
            self._wait(client, client.account_download_end_event, "accountDownloadEnd")
        finally:
            with contextlib.suppress(Exception):
                client.reqAccountUpdates(False, account_id)
        # Filter to this account (in case TWS streamed extra rows).
        return [v for v in client.account_values if not v.account or v.account == account_id]

    def _fetch_positions(self, client: _IbReader) -> list[_PositionRow]:
        client.positions_buffer.clear()
        client.position_end_event.clear()
        client.reqPositions()
        try:
            self._wait(client, client.position_end_event, "positionEnd")
        finally:
            with contextlib.suppress(Exception):
                client.cancelPositions()
        return list(client.positions_buffer)

    def _fetch_open_orders(
        self, client: _IbReader, account_id: str
    ) -> tuple[BrokerOpenOrder, ...]:
        client.open_orders_buffer.clear()
        client.open_order_end_event.clear()
        client.reqAllOpenOrders()
        self._wait(client, client.open_order_end_event, "openOrderEnd")
        return tuple(
            self._map_open_order(row, account_id) for row in client.open_orders_buffer
        )

    def _fetch_executions(
        self, client: _IbReader, account_id: str
    ) -> tuple[BrokerExecution, ...]:
        req_id = _EXEC_REQ_ID
        filt = ExecutionFilter()
        filt.acctCode = account_id
        if self.execution_window_start is not None:
            filt.time = _ibkr_execution_filter_time(self.execution_window_start)
        end_event = client.exec_details_end_events.setdefault(req_id, threading.Event())
        end_event.clear()
        client.executions_buffer.pop(req_id, None)
        client.reqExecutions(req_id, filt)
        self._wait(client, end_event, f"execDetailsEnd(reqId={req_id})")

        rows: list[BrokerExecution] = []
        for fill in client.executions_buffer.get(req_id, []):
            execution = fill.execution
            contract = fill.contract
            broker_order_id = (
                str(execution.orderId) if getattr(execution, "orderId", None) is not None else None
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

    # --- account / mapping ----------------------------------------------

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

    def _map_account(
        self, account_id: str, values: list[_AccountValueRow]
    ) -> BrokerAccount:
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

    def _map_cash(
        self, account_id: str, values: list[_AccountValueRow]
    ) -> tuple[BrokerCash, ...]:
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

    def _map_open_order(self, row: _OpenOrderRow, account_id: str) -> BrokerOpenOrder:
        order = row.order
        contract = row.contract
        status = getattr(row.orderState, "status", None)
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
