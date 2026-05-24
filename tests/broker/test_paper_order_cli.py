# Layer: stateful
"""Tests for Phase 7b ``scripts.ibkr_paper_order`` CLI (T6).

Covers the place / status / cancel subcommands, the safety gates (L20/L21/L23/L25),
and the printable result format. The CLI never reaches real ibapi: a fake client
matching ``BrokerOrderClient`` is injected via ``_build_client``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import pytest

from marketpulse.broker.order_types import (
    BrokerOrderObservation,
    CancelResult,
    PlaceResult,
    StatusResult,
)

# ---------------------------------------------------------------------------
# Fake client
# ---------------------------------------------------------------------------


@dataclass
class _Calls:
    place: list[tuple] = field(default_factory=list)
    status: list[tuple] = field(default_factory=list)
    cancel: list[tuple] = field(default_factory=list)


class FakeOrderClient:
    """Programmable fake matching ``BrokerOrderClient`` Protocol."""

    def __init__(
        self,
        *,
        place_result: PlaceResult | Exception | None = None,
        status_result: StatusResult | Exception | None = None,
        cancel_result: CancelResult | Exception | None = None,
    ) -> None:
        self._place = place_result
        self._status = status_result
        self._cancel = cancel_result
        self.calls = _Calls()

    def place_lmt_order(self, request, *, intent_id, order_ref):
        self.calls.place.append((request, intent_id, order_ref))
        if isinstance(self._place, Exception):
            raise self._place
        assert self._place is not None
        return self._place

    def fetch_order_status(self, *, broker_order_id, account_id):
        self.calls.status.append((broker_order_id, account_id))
        if isinstance(self._status, Exception):
            raise self._status
        assert self._status is not None
        return self._status

    def cancel_order(self, *, broker_order_id, account_id, was_transmitted):
        self.calls.cancel.append((broker_order_id, account_id, was_transmitted))
        if isinstance(self._cancel, Exception):
            raise self._cancel
        assert self._cancel is not None
        return self._cancel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_required_env(monkeypatch) -> None:
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "x" * 16)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


@pytest.fixture()
def cli(monkeypatch, tmp_path):
    """Import the CLI module and rig per-test plumbing.

    Returns a (module, db_url) tuple. The DB URL points at a tmp sqlite file
    with the broker order schema pre-created.
    """

    _set_required_env(monkeypatch)
    db_path = tmp_path / "paper_orders.db"
    db_url = f"sqlite:///{db_path}"

    # Create schema in the tmp DB.
    from sqlalchemy import create_engine

    from marketpulse.db.base import Base

    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    engine.dispose()

    from scripts import ibkr_paper_order as mod

    return mod, db_url


def _install_client(monkeypatch, cli_mod, client: FakeOrderClient) -> None:
    monkeypatch.setattr(cli_mod, "_build_client", lambda: client)


def _place_argv(db_url, **overrides):
    base = {
        "account": "DU123456",
        "symbol": "AAPL",
        "side": "BUY",
        "quantity": "1",
        "limit-price": "10.00",
        "transmit": "false",
    }
    base.update(overrides)
    argv = ["--db-url", db_url, "place"]
    for k, v in base.items():
        if v is None:
            continue
        argv.extend([f"--{k}", str(v)])
    return argv


def _place_result_ok() -> PlaceResult:
    return PlaceResult(
        placeorder_called=True,
        broker_order_id="1001",
        broker_perm_id=None,
        managed_accounts=("DU123456",),
        observations=(
            BrokerOrderObservation(
                event_type="next_valid_id_received", raw={"next_valid_id": 1001}
            ),
            BrokerOrderObservation(
                event_type="staged_to_tws",
                broker_order_id="1001",
                broker_status="PreSubmitted",
                raw={"transmit": False},
            ),
        ),
    )


def _place_result_transmit_true() -> PlaceResult:
    return PlaceResult(
        placeorder_called=True,
        broker_order_id="1002",
        broker_perm_id="P-77",
        managed_accounts=("DU123456",),
        observations=(
            BrokerOrderObservation(event_type="next_valid_id_received", raw={"id": 1002}),
            BrokerOrderObservation(
                event_type="submitted_to_broker",
                broker_order_id="1002",
                broker_status="Submitted",
                raw={"transmit": True},
            ),
        ),
    )


# ---------------------------------------------------------------------------
# place — happy paths
# ---------------------------------------------------------------------------


def test_place_transmit_false_happy(cli, monkeypatch, capsys):
    mod, db_url = cli
    client = FakeOrderClient(place_result=_place_result_ok())
    _install_client(monkeypatch, mod, client)

    code = mod.main(_place_argv(db_url))

    assert code == 0
    assert len(client.calls.place) == 1
    request, intent_id, order_ref = client.calls.place[0]
    assert request.account_id == "DU123456"
    assert request.side == "BUY"
    assert request.transmit is False
    assert order_ref.startswith("MP-7B-")

    out = capsys.readouterr().out
    assert "command: place" in out
    assert "intent_status: completed" in out
    assert f"intent_id: {intent_id}" in out
    assert "account: DU123456" in out
    assert "events: 2" in out


def test_place_transmit_true_with_confirm_token_happy(cli, monkeypatch, capsys):
    mod, db_url = cli
    client = FakeOrderClient(place_result=_place_result_transmit_true())
    _install_client(monkeypatch, mod, client)

    argv = _place_argv(
        db_url, transmit="true", **{"confirm-transmit": "PAPER"}
    )
    code = mod.main(argv)

    assert code == 0
    assert client.calls.place[0][0].transmit is True
    out = capsys.readouterr().out
    assert "intent_status: completed" in out
    assert "broker_perm_id: P-77" in out


def test_place_side_sell_happy(cli, monkeypatch):
    mod, db_url = cli
    client = FakeOrderClient(place_result=_place_result_ok())
    _install_client(monkeypatch, mod, client)

    code = mod.main(_place_argv(db_url, side="SELL"))
    assert code == 0
    assert client.calls.place[0][0].side == "SELL"


# ---------------------------------------------------------------------------
# place — safety gates
# ---------------------------------------------------------------------------


def test_place_transmit_true_without_confirm_token_rejects(cli, monkeypatch, capsys):
    mod, db_url = cli
    client = FakeOrderClient(place_result=_place_result_ok())
    _install_client(monkeypatch, mod, client)

    argv = _place_argv(db_url, transmit="true")  # no --confirm-transmit
    with pytest.raises(SystemExit) as excinfo:
        mod.main(argv)
    assert excinfo.value.code != 0
    assert "confirm-transmit" in str(excinfo.value).lower() or "PAPER" in str(excinfo.value)
    # Broker must not have been called.
    assert client.calls.place == []


def test_place_transmit_true_with_wrong_confirm_token_rejects(cli, monkeypatch):
    mod, db_url = cli
    client = FakeOrderClient(place_result=_place_result_ok())
    _install_client(monkeypatch, mod, client)

    argv = _place_argv(
        db_url, transmit="true", **{"confirm-transmit": "WRONG"}
    )
    with pytest.raises(SystemExit) as excinfo:
        mod.main(argv)
    assert excinfo.value.code != 0
    assert client.calls.place == []


def test_place_missing_limit_price_argparse_error(cli):
    mod, db_url = cli
    with pytest.raises(SystemExit) as excinfo:
        mod.main(_place_argv(db_url, **{"limit-price": None}))
    assert excinfo.value.code != 0


def test_place_missing_account_argparse_error(cli):
    mod, db_url = cli
    with pytest.raises(SystemExit) as excinfo:
        mod.main(_place_argv(db_url, account=None))
    assert excinfo.value.code != 0


def test_place_missing_symbol_argparse_error(cli):
    mod, db_url = cli
    with pytest.raises(SystemExit) as excinfo:
        mod.main(_place_argv(db_url, symbol=None))
    assert excinfo.value.code != 0


def test_place_missing_side_argparse_error(cli):
    mod, db_url = cli
    with pytest.raises(SystemExit) as excinfo:
        mod.main(_place_argv(db_url, side=None))
    assert excinfo.value.code != 0


def test_place_missing_quantity_argparse_error(cli):
    mod, db_url = cli
    with pytest.raises(SystemExit) as excinfo:
        mod.main(_place_argv(db_url, quantity=None))
    assert excinfo.value.code != 0


def test_place_invalid_side_rejected(cli):
    mod, db_url = cli
    with pytest.raises(SystemExit) as excinfo:
        mod.main(_place_argv(db_url, side="HOLD"))
    assert excinfo.value.code != 0


def test_place_non_paper_account_rejected(cli, monkeypatch, capsys):
    """Live-style account: service emits safety_rejected, CLI exits non-zero."""

    mod, db_url = cli
    # Place result is irrelevant — service refuses before broker call.
    client = FakeOrderClient(place_result=_place_result_ok())
    _install_client(monkeypatch, mod, client)

    code = mod.main(_place_argv(db_url, account="U1234567"))
    assert code != 0
    assert client.calls.place == []
    out = capsys.readouterr().out
    assert "intent_status: rejected" in out


def test_place_duplicate_idempotency_key_clean_error(cli, monkeypatch, capsys):
    mod, db_url = cli
    client = FakeOrderClient(place_result=_place_result_ok())
    _install_client(monkeypatch, mod, client)

    argv = _place_argv(db_url, **{"idempotency-key": "dup-key-1"})
    code1 = mod.main(argv)
    assert code1 == 0

    code2 = mod.main(argv)
    assert code2 == 3  # OrderDuplicateError
    err = capsys.readouterr().err
    assert "OrderDuplicateError" in err


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def _status_result_ok() -> StatusResult:
    return StatusResult(
        success=True,
        managed_accounts=("DU123456",),
        observations=(
            BrokerOrderObservation(
                event_type="order_status_seen",
                broker_order_id="1001",
                broker_status="Submitted",
                filled_quantity=Decimal("0"),
                raw={"status": "Submitted"},
            ),
        ),
    )


def test_status_happy(cli, monkeypatch, capsys):
    mod, db_url = cli
    place_client = FakeOrderClient(place_result=_place_result_ok())
    _install_client(monkeypatch, mod, place_client)
    place_code = mod.main(_place_argv(db_url))
    assert place_code == 0
    place_intent_id = place_client.calls.place[0][1]

    status_client = FakeOrderClient(status_result=_status_result_ok())
    _install_client(monkeypatch, mod, status_client)

    code = mod.main([
        "--db-url", db_url, "status",
        "--account", "DU123456",
        "--intent-id", str(place_intent_id),
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "command: status" in out
    assert "intent_status: completed" in out


def test_status_missing_intent_id_argparse_error(cli):
    mod, db_url = cli
    with pytest.raises(SystemExit) as excinfo:
        mod.main(["--db-url", db_url, "status", "--account", "DU123456"])
    assert excinfo.value.code != 0


def test_status_missing_account_argparse_error(cli):
    mod, db_url = cli
    with pytest.raises(SystemExit) as excinfo:
        mod.main(["--db-url", db_url, "status", "--intent-id", "1"])
    assert excinfo.value.code != 0


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


def _cancel_result_ok() -> CancelResult:
    return CancelResult(
        success=True,
        managed_accounts=("DU123456",),
        observations=(
            BrokerOrderObservation(
                event_type="staged_cancelled",
                broker_order_id="1001",
                raw={"staged": True},
            ),
        ),
    )


def test_cancel_happy(cli, monkeypatch, capsys):
    mod, db_url = cli
    place_client = FakeOrderClient(place_result=_place_result_ok())
    _install_client(monkeypatch, mod, place_client)
    assert mod.main(_place_argv(db_url)) == 0
    place_intent_id = place_client.calls.place[0][1]

    cancel_client = FakeOrderClient(cancel_result=_cancel_result_ok())
    _install_client(monkeypatch, mod, cancel_client)

    code = mod.main([
        "--db-url", db_url, "cancel",
        "--account", "DU123456",
        "--intent-id", str(place_intent_id),
        "--confirm-cancel",
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "command: cancel" in out
    assert "intent_status: completed" in out


def test_cancel_without_confirm_flag_rejects(cli, monkeypatch, capsys):
    mod, db_url = cli
    place_client = FakeOrderClient(place_result=_place_result_ok())
    _install_client(monkeypatch, mod, place_client)
    assert mod.main(_place_argv(db_url)) == 0
    place_intent_id = place_client.calls.place[0][1]

    cancel_client = FakeOrderClient(cancel_result=_cancel_result_ok())
    _install_client(monkeypatch, mod, cancel_client)

    with pytest.raises(SystemExit) as excinfo:
        mod.main([
            "--db-url", db_url, "cancel",
            "--account", "DU123456",
            "--intent-id", str(place_intent_id),
        ])
    assert excinfo.value.code != 0
    assert "confirm-cancel" in str(excinfo.value).lower()
    assert cancel_client.calls.cancel == []


def test_cancel_missing_intent_id_argparse_error(cli):
    mod, db_url = cli
    with pytest.raises(SystemExit) as excinfo:
        mod.main([
            "--db-url", db_url, "cancel",
            "--account", "DU123456", "--confirm-cancel",
        ])
    assert excinfo.value.code != 0


# ---------------------------------------------------------------------------
# Misc / shared flags
# ---------------------------------------------------------------------------


def test_build_client_raises_without_monkeypatch(cli):
    """_build_client is a DI stub; calling it must raise clearly."""

    mod, _ = cli
    with pytest.raises(SystemExit) as excinfo:
        mod._build_client()
    assert "IbkrOrderClient" in str(excinfo.value)


def test_help_lists_three_subcommands(cli, capsys):
    mod, _ = cli
    with pytest.raises(SystemExit):
        mod.build_parser().parse_args(["--help"])
    out = capsys.readouterr().out
    assert "place" in out
    assert "status" in out
    assert "cancel" in out
