# Layer: stateful
"""T7b tests: ``IbkrOrderClient`` class against a fake ``_IbkrOrderApp``.

Kept in a separate file from ``test_ibkr_order_client.py`` (T7a pure helpers)
so the helper tests stay focused. All synchronization uses ``threading.Event``
— no ``time.sleep`` polling — and every fake-app callback is driven by the
test thread before the adapter wait returns.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from marketpulse.broker import ibkr_order_client as mod
from marketpulse.broker.ibkr_order_client import IbkrOrderClient
from marketpulse.broker.order_types import (
    BrokerOrderObservation,
    BrokerOrderRequest,
    CancelResult,
    OrderAccountMismatchError,
    OrderCallbackTimeoutError,
    OrderConnectionError,
    PlaceResult,
    StatusResult,
)

# ---------------------------------------------------------------------------
# Fake _IbkrOrderApp substitute
# ---------------------------------------------------------------------------


class _FakeApp:
    """Stand-in for ``_IbkrOrderApp`` with no real ibapi I/O.

    Tests configure ``auto_*`` flags on construction (via factory closure) to
    drive callbacks synchronously when the adapter calls ``connect`` /
    ``reqIds`` / ``placeOrder`` / ``cancelOrder``. All Events are real.
    """

    def __init__(self) -> None:
        # Match the real wrapper's surface.
        self.next_valid_id_event = threading.Event()
        self.next_valid_id: int | None = None
        self.managed_accounts_event = threading.Event()
        self.managed_accounts: tuple[str, ...] = ()
        self.observation_event = threading.Event()
        self.observations: list[BrokerOrderObservation] = []
        self.errors: list[dict] = []
        self.broker_order_id: str | None = None
        self.broker_perm_id: str | None = None

        # Test-only knobs (set by tests post-construction).
        self.connect_raises: Exception | None = None
        self.fire_managed_accounts: tuple[str, ...] | None = None
        self.fire_next_valid_id: int | None = None
        self.fire_observations_on_place: list[BrokerOrderObservation] = []
        self.fire_observations_on_cancel: list[BrokerOrderObservation] = []
        self.fire_observations_on_status: list[BrokerOrderObservation] = []

        # Call tracking.
        self.connect_calls: list[tuple] = []
        self.placeOrder_calls: list[tuple] = []
        self.cancelOrder_calls: list[int] = []
        self.reqOpenOrders_calls = 0
        self.disconnect_calls = 0

    # --- EClient surface used by adapter ---------------------------------
    def connect(self, host: str, port: int, client_id: int) -> None:
        self.connect_calls.append((host, port, client_id))
        if self.connect_raises is not None:
            raise self.connect_raises
        if self.fire_managed_accounts is not None:
            self.managed_accounts = self.fire_managed_accounts
            self.managed_accounts_event.set()

    def run(self) -> None:  # noqa: D401 — reader thread no-op
        return

    def disconnect(self) -> None:
        self.disconnect_calls += 1

    def reqIds(self, _numIds: int) -> None:  # noqa: N802
        if self.fire_next_valid_id is not None:
            self.next_valid_id = self.fire_next_valid_id
            self.next_valid_id_event.set()

    def placeOrder(self, orderId: int, contract, order) -> None:  # noqa: N802
        self.placeOrder_calls.append((orderId, contract, order))
        for obs in self.fire_observations_on_place:
            self.observations.append(obs)
        if self.fire_observations_on_place:
            self.broker_order_id = str(orderId)
            self.observation_event.set()

    def cancelOrder(self, orderId: int, *args) -> None:  # noqa: N802
        self.cancelOrder_calls.append(orderId)
        for obs in self.fire_observations_on_cancel:
            self.observations.append(obs)
        if self.fire_observations_on_cancel:
            self.observation_event.set()

    def reqOpenOrders(self) -> None:  # noqa: N802
        self.reqOpenOrders_calls += 1
        for obs in self.fire_observations_on_status:
            self.observations.append(obs)
        if self.fire_observations_on_status:
            self.observation_event.set()


def _make_request(*, transmit: bool = False) -> BrokerOrderRequest:
    return BrokerOrderRequest(
        account_id="DU123456",
        symbol="AAPL",
        asset_class="STK",
        side="BUY",
        quantity=Decimal("1"),
        order_type="LMT",
        limit_price=Decimal("10.00"),
        transmit=transmit,
        local_idempotency_key="key-abc",
    )


def _short_timeouts() -> dict:
    """Tiny timeouts so timeout tests are fast."""
    return {
        "connect_timeout_seconds": 1,
        "next_valid_id_timeout_seconds": 1,
        "observation_timeout_seconds": 1,
    }


def _make_client(app: _FakeApp, **overrides) -> IbkrOrderClient:
    kwargs = dict(
        host="127.0.0.1",
        port=7497,
        client_id=72,
        account_id="DU123456",
        app_factory=lambda: app,
    )
    kwargs.update(overrides)
    return IbkrOrderClient(**kwargs)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_construct_does_not_connect():
    app = _FakeApp()
    _make_client(app)
    assert app.connect_calls == []


# ---------------------------------------------------------------------------
# place_lmt_order — transmit=False happy path
# ---------------------------------------------------------------------------


def test_place_transmit_false_synthesizes_staged_observation():
    app = _FakeApp()
    app.fire_managed_accounts = ("DU123456",)
    app.fire_next_valid_id = 4242
    client = _make_client(app)

    result = client.place_lmt_order(
        _make_request(transmit=False), intent_id=1, order_ref="MP-7B-1-deadbeef"
    )

    assert isinstance(result, PlaceResult)
    assert result.placeorder_called is True
    assert result.broker_order_id == "4242"
    assert result.managed_accounts == ("DU123456",)
    event_types = [o.event_type for o in result.observations]
    assert "staged_to_tws" in event_types
    # placeOrder must have been invoked with transmit=False order
    assert app.placeOrder_calls
    _, _, order = app.placeOrder_calls[0]
    assert order.transmit is False
    assert app.disconnect_calls == 1


# ---------------------------------------------------------------------------
# place_lmt_order — transmit=True happy path
# ---------------------------------------------------------------------------


def test_place_transmit_true_returns_observations():
    app = _FakeApp()
    app.fire_managed_accounts = ("DU123456",)
    app.fire_next_valid_id = 99
    app.fire_observations_on_place = [
        BrokerOrderObservation(
            event_type="order_status_seen",
            broker_order_id="99",
            broker_status="Submitted",
        )
    ]
    client = _make_client(app)

    result = client.place_lmt_order(
        _make_request(transmit=True), intent_id=1, order_ref="MP-7B-1-cafebabe"
    )

    assert result.placeorder_called is True
    assert result.broker_order_id == "99"
    event_types = [o.event_type for o in result.observations]
    assert "submitted_to_broker" in event_types
    assert "order_status_seen" in event_types
    _, _, order = app.placeOrder_calls[0]
    assert order.transmit is True


# ---------------------------------------------------------------------------
# Connect failure
# ---------------------------------------------------------------------------


def test_connect_failure_raises_order_connection_error():
    app = _FakeApp()
    app.connect_raises = RuntimeError("ECONNREFUSED")
    client = _make_client(app, **_short_timeouts())

    with pytest.raises(OrderConnectionError):
        client.place_lmt_order(
            _make_request(transmit=False), intent_id=1, order_ref="MP-7B-1-x"
        )


# ---------------------------------------------------------------------------
# Account mismatch
# ---------------------------------------------------------------------------


def test_account_mismatch_raises_before_placeorder():
    app = _FakeApp()
    app.fire_managed_accounts = ("DU999999",)  # different account
    client = _make_client(app, **_short_timeouts())

    with pytest.raises(OrderAccountMismatchError):
        client.place_lmt_order(
            _make_request(transmit=False), intent_id=1, order_ref="MP-7B-1-x"
        )
    assert app.placeOrder_calls == []


def test_managed_accounts_timeout_raises_connection_error():
    app = _FakeApp()
    # Don't fire managed_accounts event
    client = _make_client(app, **_short_timeouts())

    with pytest.raises(OrderConnectionError):
        client.place_lmt_order(
            _make_request(transmit=False), intent_id=1, order_ref="MP-7B-1-x"
        )


# ---------------------------------------------------------------------------
# nextValidId timeout (before placeOrder)
# ---------------------------------------------------------------------------


def test_next_valid_id_timeout_raises_callback_timeout_pre_placeorder():
    app = _FakeApp()
    app.fire_managed_accounts = ("DU123456",)
    # Don't fire next_valid_id
    client = _make_client(app, **_short_timeouts())

    with pytest.raises(OrderCallbackTimeoutError) as excinfo:
        client.place_lmt_order(
            _make_request(transmit=False), intent_id=1, order_ref="MP-7B-1-x"
        )
    assert excinfo.value.placeorder_called is False
    assert app.placeOrder_calls == []


# ---------------------------------------------------------------------------
# placeOrder observation timeout (after placeOrder)
# ---------------------------------------------------------------------------


def test_place_observation_timeout_raises_callback_timeout_post_placeorder():
    app = _FakeApp()
    app.fire_managed_accounts = ("DU123456",)
    app.fire_next_valid_id = 1
    # transmit=True but no callbacks fired
    client = _make_client(app, **_short_timeouts())

    with pytest.raises(OrderCallbackTimeoutError) as excinfo:
        client.place_lmt_order(
            _make_request(transmit=True), intent_id=1, order_ref="MP-7B-1-x"
        )
    assert excinfo.value.placeorder_called is True
    assert app.placeOrder_calls  # placeOrder WAS called before timeout


# ---------------------------------------------------------------------------
# fetch_order_status
# ---------------------------------------------------------------------------


def test_fetch_order_status_returns_filtered_observations():
    app = _FakeApp()
    app.fire_managed_accounts = ("DU123456",)
    app.fire_observations_on_status = [
        BrokerOrderObservation(
            event_type="order_status_seen",
            broker_order_id="7777",
            broker_status="Submitted",
        ),
        BrokerOrderObservation(
            event_type="order_status_seen",
            broker_order_id="other",
            broker_status="Submitted",
        ),
    ]
    client = _make_client(app)

    result = client.fetch_order_status(broker_order_id="7777", account_id="DU123456")

    assert isinstance(result, StatusResult)
    assert result.success is True
    assert app.reqOpenOrders_calls == 1
    ids = {o.broker_order_id for o in result.observations}
    assert ids == {"7777"}


def test_fetch_order_status_empty_when_no_session_state():
    app = _FakeApp()
    app.fire_managed_accounts = ("DU123456",)
    # no status callbacks
    client = _make_client(app, **_short_timeouts())

    result = client.fetch_order_status(broker_order_id="404", account_id="DU123456")
    assert result.success is True
    assert result.observations == ()


def test_fetch_order_status_account_mismatch():
    app = _FakeApp()
    client = _make_client(app)
    with pytest.raises(OrderAccountMismatchError):
        client.fetch_order_status(broker_order_id="x", account_id="DU999999")


# ---------------------------------------------------------------------------
# cancel_order
# ---------------------------------------------------------------------------


def test_cancel_transmit_true_calls_cancelorder_and_returns_broker_observations():
    app = _FakeApp()
    app.fire_managed_accounts = ("DU123456",)
    app.fire_observations_on_cancel = [
        BrokerOrderObservation(
            event_type="cancelled",
            broker_order_id="555",
            broker_status="Cancelled",
        )
    ]
    client = _make_client(app)

    result = client.cancel_order(
        broker_order_id="555", account_id="DU123456", was_transmitted=True
    )

    assert isinstance(result, CancelResult)
    assert result.success is True
    assert app.cancelOrder_calls == [555]
    event_types = [o.event_type for o in result.observations]
    assert "broker_cancel_requested" in event_types
    assert "cancelled" in event_types


def test_cancel_staged_synthesizes_observation_without_broker_call():
    app = _FakeApp()
    app.fire_managed_accounts = ("DU123456",)
    client = _make_client(app)

    result = client.cancel_order(
        broker_order_id="888", account_id="DU123456", was_transmitted=False
    )

    assert result.success is True
    assert app.cancelOrder_calls == []  # never called for staged
    assert len(result.observations) == 1
    assert result.observations[0].event_type == "staged_cancelled"
    assert result.observations[0].broker_order_id == "888"


def test_cancel_account_mismatch():
    app = _FakeApp()
    client = _make_client(app)
    with pytest.raises(OrderAccountMismatchError):
        client.cancel_order(
            broker_order_id="x", account_id="DU999999", was_transmitted=True
        )


# ---------------------------------------------------------------------------
# Adapter surface — no modify/replace/cancel-all/options/extra place_*
# ---------------------------------------------------------------------------


def test_adapter_exposes_only_three_public_methods():
    forbidden = {
        "modify_order",
        "replace_order",
        "global_cancel",
        "exercise_option",
        "exercise_options",
    }
    public = {n for n in dir(IbkrOrderClient) if not n.startswith("_")}
    assert not (forbidden & public), f"forbidden surface present: {forbidden & public}"
    place_methods = {n for n in public if n.startswith("place")}
    assert place_methods == {"place_lmt_order"}, place_methods


# ---------------------------------------------------------------------------
# _IbkrOrderApp is private
# ---------------------------------------------------------------------------


def test_ibkr_order_app_is_module_private():
    assert hasattr(mod, "_IbkrOrderApp")
    assert not hasattr(mod, "IbkrOrderApp")


# ---------------------------------------------------------------------------
# Reader-thread join on disconnect (no leaked daemon threads)
# ---------------------------------------------------------------------------


def test_disconnect_joins_reader_thread():
    """The adapter must join the EClient reader thread on tear-down.

    ``_FakeApp.run()`` returns immediately, so the reader thread completes
    promptly; after ``place_lmt_order`` we observe ``disconnect`` was called
    AND the reader is no longer alive (proving the join executed and the
    thread was reaped, not leaked as a daemon).
    """

    app = _FakeApp()
    app.fire_managed_accounts = ("DU123456",)
    app.fire_next_valid_id = 4242
    client = _make_client(app)

    # Snapshot enumerated threads before/after to make sure we don't leak.
    before = {t.ident for t in threading.enumerate()}

    client.place_lmt_order(
        _make_request(transmit=False), intent_id=1, order_ref="MP-7B-1-x"
    )

    assert app.disconnect_calls == 1
    # The reader thread targeting our fake _FakeApp.run() (no-op) should be
    # joined and no longer alive after place_lmt_order returns.
    after = {t for t in threading.enumerate() if t.ident not in before}
    leaked_readers = [t for t in after if t.name == "ibkr-order-reader" and t.is_alive()]
    assert leaked_readers == [], f"reader threads leaked: {leaked_readers}"
