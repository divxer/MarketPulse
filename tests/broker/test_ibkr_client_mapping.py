# Layer: stateful
"""Mapping + adapter tests for the ibapi-based IBKR read-only client.

These tests never open a real IBKR socket. They drive the adapter by
substituting a fake ``_IbReader`` whose ``connect`` / ``run`` /
``reqXxx`` methods immediately fire the corresponding EWrapper
callbacks the adapter is waiting on.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from marketpulse.broker.ibkr_client import (
    IbkrReadClient,
    _AccountValueRow,
    _decimal_or_none,
    _ibkr_execution_filter_time,
    _map_position,
    _parse_ibkr_time,
)


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


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


def test_decimal_conversion_avoids_float_repr_artifacts():
    assert _decimal_or_none(0.1) == Decimal("0.1")
    assert _decimal_or_none(None) is None
    assert _decimal_or_none("nan") is None
    assert _decimal_or_none(Decimal("NaN")) is None
    assert _decimal_or_none(float("inf")) is None
    assert _decimal_or_none("1.7976931348623157E308") is None


def test_position_mapping_returns_pure_dto():
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
    value = _ibkr_execution_filter_time(datetime(2026, 5, 23, 7, 0, tzinfo=UTC))
    assert value == "20260523 07:00:00"


# ---------------------------------------------------------------------------
# Fake _IbReader test seam
# ---------------------------------------------------------------------------


@dataclass
class _FakeOrder:
    orderId: int = 0
    action: str | None = None
    orderType: str | None = None
    totalQuantity: float | None = None
    lmtPrice: float | None = None


@dataclass
class _FakeOrderState:
    status: str | None = None


@dataclass
class _FakeExecution:
    execId: str
    orderId: int | None
    side: str | None
    shares: float | None
    price: float | None
    time: datetime | None


@dataclass
class FakeIbReader:
    """Minimal stand-in for the real ``_IbReader``.

    The adapter calls ``connect`` then starts a reader thread that
    invokes ``run``. We use ``run`` to immediately fire ``nextValidId``
    and ``managedAccounts`` callbacks, which unblocks the main thread.
    Subsequent ``reqXxx`` calls fire their matching end-events
    synchronously.
    """

    managed_accounts: list[str] = field(default_factory=lambda: ["DU123"])
    account_values_to_return: list[tuple[str, str, str]] = field(default_factory=list)
    positions_to_return: list[FakePosition] = field(default_factory=list)
    open_orders_to_return: list[tuple[int, FakeContract, _FakeOrder, _FakeOrderState]] = field(
        default_factory=list
    )
    executions_to_return: list[tuple[FakeContract, _FakeExecution]] = field(default_factory=list)

    # Adapter-facing state (mirrors _IbReader public surface)
    ready_event: threading.Event = field(default_factory=threading.Event)
    managed_accounts_event: threading.Event = field(default_factory=threading.Event)
    account_download_end_event: threading.Event = field(default_factory=threading.Event)
    position_end_event: threading.Event = field(default_factory=threading.Event)
    open_order_end_event: threading.Event = field(default_factory=threading.Event)
    exec_details_end_events: dict[int, threading.Event] = field(default_factory=dict)

    managed_accounts_text: str = ""
    account_values: list = field(default_factory=list)
    positions_buffer: list = field(default_factory=list)
    open_orders_buffer: list = field(default_factory=list)
    executions_buffer: dict = field(default_factory=dict)
    fatal_error: Exception | None = None

    # Bookkeeping
    connected_args: tuple = ()
    disconnected: bool = False
    cancel_positions_called: bool = False
    unsubscribed_account: str | None = None
    forbidden_called: list[str] = field(default_factory=list)

    # --- Methods the adapter calls on the client ---

    def connect(self, host: str, port: int, client_id: int) -> None:
        self.connected_args = (host, port, client_id)

    def run(self) -> None:
        # Pretend the reader thread observed nextValidId + managedAccounts.
        self.managed_accounts_text = ",".join(self.managed_accounts)
        self.ready_event.set()
        self.managed_accounts_event.set()

    def disconnect(self) -> None:
        self.disconnected = True

    def reqAccountUpdates(self, subscribe: bool, account: str) -> None:  # noqa: N802
        if subscribe:
            for tag, value, currency in self.account_values_to_return:
                self.account_values.append(
                    _AccountValueRow(tag=tag, value=value, currency=currency, account=account)
                )
            self.account_download_end_event.set()
        else:
            self.unsubscribed_account = account

    def reqPositions(self) -> None:  # noqa: N802
        from marketpulse.broker.ibkr_client import _PositionRow

        for p in self.positions_to_return:
            self.positions_buffer.append(
                _PositionRow(
                    account=p.account,
                    contract=p.contract,
                    position=p.position,
                    avg_cost=p.avgCost,
                )
            )
        self.position_end_event.set()

    def cancelPositions(self) -> None:  # noqa: N802
        self.cancel_positions_called = True

    def reqAllOpenOrders(self) -> None:  # noqa: N802
        from marketpulse.broker.ibkr_client import _OpenOrderRow

        for order_id, contract, order, state in self.open_orders_to_return:
            self.open_orders_buffer.append(
                _OpenOrderRow(
                    order_id=order_id, contract=contract, order=order, order_state=state
                )
            )
        self.open_order_end_event.set()

    def reqExecutions(self, req_id: int, filt) -> None:  # noqa: N802
        from marketpulse.broker.ibkr_client import _ExecutionRow

        bucket = self.executions_buffer.setdefault(req_id, [])
        for contract, execution in self.executions_to_return:
            bucket.append(_ExecutionRow(contract=contract, execution=execution))
        evt = self.exec_details_end_events.setdefault(req_id, threading.Event())
        evt.set()

    # Honeypots: the adapter must never call any of these.
    def placeOrder(self, *a, **k):  # noqa: N802
        self.forbidden_called.append("placeOrder")

    def cancelOrder(self, *a, **k):  # noqa: N802
        self.forbidden_called.append("cancelOrder")

    def reqGlobalCancel(self, *a, **k):  # noqa: N802
        self.forbidden_called.append("reqGlobalCancel")


def _make_client(reader: FakeIbReader, **overrides) -> IbkrReadClient:
    kwargs: dict = dict(
        host="127.0.0.1",
        port=7497,
        client_id=71,
        timeout_seconds=5,
        account_id="",
        broker_environment="paper",
        client_factory=lambda: reader,
    )
    kwargs.update(overrides)
    return IbkrReadClient(**kwargs)


# ---------------------------------------------------------------------------
# Adapter behaviour tests
# ---------------------------------------------------------------------------


def test_account_selection_requires_config_when_multiple_accounts_returned():
    reader = FakeIbReader(managed_accounts=["DU1", "DU2"])
    client = _make_client(reader)

    try:
        client._select_account(("DU1", "DU2"))
    except RuntimeError as exc:
        assert "configure IBKR_ACCOUNT_ID" in str(exc)
    else:
        raise AssertionError("multiple returned accounts should fail closed")


def test_fetch_snapshot_returns_mapped_dto_and_never_calls_mutating_apis():
    reader = FakeIbReader(
        managed_accounts=["DU123"],
        account_values_to_return=[
            ("AccountType", "INDIVIDUAL", ""),
            ("BaseCurrency", "EUR", ""),
            ("NetLiquidation", "100000", "EUR"),
            ("BuyingPower", "50000", "EUR"),
            ("TotalCashBalance", "25000", "EUR"),
            ("SettledCash", "25000", "EUR"),
        ],
    )
    client = _make_client(reader)

    snapshot = client.fetch_snapshot()

    assert snapshot.broker == "IBKR"
    assert snapshot.broker_environment == "paper"
    assert snapshot.account_id == "DU123"
    assert snapshot.account.account_type == "INDIVIDUAL"
    assert snapshot.account.base_currency == "EUR"
    assert snapshot.account.net_liquidation == Decimal("100000")
    assert snapshot.account.buying_power == Decimal("50000")

    assert len(snapshot.cash) == 1
    assert snapshot.cash[0].currency == "EUR"
    assert snapshot.cash[0].cash_balance == Decimal("25000")
    assert snapshot.cash[0].settled_cash == Decimal("25000")

    assert reader.connected_args == ("127.0.0.1", 7497, 71)
    assert reader.disconnected is True
    assert reader.unsubscribed_account == "DU123"
    assert reader.cancel_positions_called is True
    assert reader.forbidden_called == []


def test_fetch_snapshot_disconnects_after_multiple_account_ambiguity():
    reader = FakeIbReader(managed_accounts=["DU1", "DU2"])
    client = _make_client(reader)

    try:
        client.fetch_snapshot()
    except RuntimeError as exc:
        assert "configure IBKR_ACCOUNT_ID" in str(exc)
    else:
        raise AssertionError("multiple returned accounts should fail closed")

    assert reader.disconnected is True


def test_fetch_snapshot_filters_positions_to_selected_account():
    reader = FakeIbReader(
        managed_accounts=["DU123"],
        account_values_to_return=[("BaseCurrency", "USD", "")],
        positions_to_return=[
            FakePosition("DU123", FakeContract("AAPL", "STK"), 3.0, 180.25),
            FakePosition("DU999", FakeContract("MSFT", "STK"), 1.0, 400.0),
        ],
    )
    client = _make_client(reader)

    snapshot = client.fetch_snapshot()

    assert len(snapshot.positions) == 1
    assert snapshot.positions[0].symbol == "AAPL"
    assert snapshot.positions[0].account_id == "DU123"


def test_fetch_snapshot_maps_open_orders_and_executions():
    contract = FakeContract("AAPL", "STK")
    order = _FakeOrder(
        orderId=42,
        action="BUY",
        orderType="LMT",
        totalQuantity=10.0,
        lmtPrice=150.0,
    )
    state = _FakeOrderState(status="Submitted")
    execution = _FakeExecution(
        execId="exec-1",
        orderId=42,
        side="BOT",
        shares=10.0,
        price=149.5,
        time=datetime(2026, 5, 23, 14, 30, tzinfo=UTC),
    )

    reader = FakeIbReader(
        managed_accounts=["DU123"],
        account_values_to_return=[("BaseCurrency", "USD", "")],
        open_orders_to_return=[(42, contract, order, state)],
        executions_to_return=[(contract, execution)],
    )
    client = _make_client(
        reader, execution_window_start=datetime(2026, 5, 23, 4, 0, tzinfo=UTC)
    )

    snapshot = client.fetch_snapshot()

    assert len(snapshot.open_orders) == 1
    o = snapshot.open_orders[0]
    assert o.broker_order_id == "42"
    assert o.symbol == "AAPL"
    assert o.side == "BUY"
    assert o.order_type == "LMT"
    assert o.quantity == Decimal("10.0")
    assert o.limit_price == Decimal("150.0")
    assert o.status == "Submitted"

    assert len(snapshot.executions) == 1
    e = snapshot.executions[0]
    assert e.broker_exec_id == "exec-1"
    assert e.broker_order_id == "42"
    assert e.symbol == "AAPL"
    assert e.side == "BOT"
    assert e.quantity == Decimal("10.0")
    assert e.price == Decimal("149.5")


def test_fetch_snapshot_raises_timeout_when_end_event_never_fires():
    from marketpulse.broker.ibkr_client import IbkrTimeoutError

    class StalledReader(FakeIbReader):
        def reqAccountUpdates(self, subscribe: bool, account: str) -> None:  # noqa: N802
            # Subscribe is acknowledged but we deliberately never set
            # account_download_end_event, simulating TWS going silent
            # after the request. The adapter should time out.
            if not subscribe:
                self.unsubscribed_account = account

    reader = StalledReader(
        managed_accounts=["DU123"],
        account_values_to_return=[("BaseCurrency", "USD", "")],
    )
    client = _make_client(reader, timeout_seconds=1)

    try:
        client.fetch_snapshot()
    except IbkrTimeoutError as exc:
        assert "accountDownloadEnd" in str(exc)
    else:
        raise AssertionError("missing end-event should raise IbkrTimeoutError")

    assert reader.disconnected is True


def test_fatal_error_callback_aborts_fetch_snapshot():
    from marketpulse.broker.ibkr_client import IbkrApiError

    class ErroringReader(FakeIbReader):
        def run(self) -> None:
            # Simulate IBKR raising a fatal error code (>= 1100) before
            # the connection is fully ready.
            self.fatal_error = IbkrApiError("IBKR error 1100: connection lost")
            self.ready_event.set()
            self.managed_accounts_event.set()

    reader = ErroringReader()
    client = _make_client(reader)

    try:
        client.fetch_snapshot()
    except IbkrApiError as exc:
        assert "1100" in str(exc)
    else:
        raise AssertionError("fatal error should propagate")

    assert reader.disconnected is True


# ---------------------------------------------------------------------------
# Phase 7a follow-up: ibapi Execution.time string parsing + fatal error 326
# ---------------------------------------------------------------------------


def test_parse_ibkr_time_handles_known_formats():
    # None / empty / malformed → None (snapshot must remain persistable)
    assert _parse_ibkr_time(None) is None
    assert _parse_ibkr_time("") is None
    assert _parse_ibkr_time("   ") is None
    assert _parse_ibkr_time("not a date") is None
    assert _parse_ibkr_time("20260523") is None
    assert _parse_ibkr_time("99999999 99:99:99") is None

    # No-TZ form → assumed UTC
    parsed = _parse_ibkr_time("20260523 21:30:00")
    assert isinstance(parsed, datetime)
    assert parsed.tzinfo is not None
    assert parsed.astimezone(UTC) == datetime(2026, 5, 23, 21, 30, tzinfo=UTC)

    # With TZ suffix → converted to UTC
    parsed_tz = _parse_ibkr_time("20260523 21:30:00 US/Eastern")
    assert isinstance(parsed_tz, datetime)
    assert parsed_tz.tzinfo is not None
    # US/Eastern in May = UTC-4 (EDT) → 21:30 EDT == 01:30 UTC next day
    assert parsed_tz.astimezone(UTC) == datetime(2026, 5, 24, 1, 30, tzinfo=UTC)

    # Unknown TZ name → falls through to UTC assumption (does not raise)
    parsed_bad_tz = _parse_ibkr_time("20260523 21:30:00 Bogus/Zone")
    assert isinstance(parsed_bad_tz, datetime)
    assert parsed_bad_tz.tzinfo is not None


def test_executed_at_parses_ibkr_string_time_to_utc_datetime():
    """End-to-end: real ibapi Execution shape uses a *string* time field.

    The previous FakeIbReader-driven tests passed a real datetime in
    place of execution.time, which masked the bug. This test mimics the
    raw ibapi.Execution shape (plain attribute object with string time).
    """

    class _RawExecution:
        # Duck-typed, NOT an _ExecutionRow / _FakeExecution dataclass.
        def __init__(self, time_str: str):
            self.execId = "exec-raw-1"
            self.orderId = 99
            self.side = "BOT"
            self.shares = 5.0
            self.price = 200.0
            self.time = time_str  # string, exactly like ibapi delivers

    class _RawExecution2:
        def __init__(self, time_str: str):
            self.execId = "exec-raw-2"
            self.orderId = 100
            self.side = "BOT"
            self.shares = 1.0
            self.price = 201.0
            self.time = time_str

    contract = FakeContract("AAPL", "STK")
    raw_no_tz = _RawExecution("20260523 21:30:00")
    raw_with_tz = _RawExecution2("20260523 21:30:00 US/Eastern")

    reader = FakeIbReader(
        managed_accounts=["DU123"],
        account_values_to_return=[("BaseCurrency", "USD", "")],
        executions_to_return=[(contract, raw_no_tz), (contract, raw_with_tz)],
    )
    client = _make_client(reader)

    snapshot = client.fetch_snapshot()

    assert len(snapshot.executions) == 2
    for e in snapshot.executions:
        assert isinstance(e.executed_at, datetime), (
            f"executed_at must be parsed to datetime (got {type(e.executed_at)})"
        )
        assert e.executed_at.tzinfo is not None, "executed_at must be timezone-aware"


def test_error_code_326_aborts_fetch_snapshot():
    """IBKR error 326 (client id already in use) is fatal sub-1100."""
    from marketpulse.broker.ibkr_client import IbkrApiError, _IbReader

    reader = _IbReader()
    # Simulate the error callback firing on the reader thread.
    reader.error(reqId=-1, errorCode=326, errorString="Client id is already in use")

    assert reader.fatal_error is not None
    assert isinstance(reader.fatal_error, IbkrApiError)
    assert "326" in str(reader.fatal_error)
    # All blocking events should have been released so waiters unblock.
    assert reader.ready_event.is_set()
    assert reader.managed_accounts_event.is_set()
    assert reader.account_download_end_event.is_set()
    assert reader.position_end_event.is_set()
    assert reader.open_order_end_event.is_set()
