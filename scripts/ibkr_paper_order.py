"""Run one manual IBKR paper order command (Phase 7b).

Subcommands: ``place``, ``status``, ``cancel``. Paper account only — the
service layer (``marketpulse.broker.order_service``) re-validates the
``account_id`` against the ``^DU[A-Z]*\\d+$`` paper regex (L26/L30) before any
broker call, and refuses non-paper accounts with a ``safety_rejected`` event.

Spec locks honored by this CLI (L17–L25):

* L19 — one script, three subcommands.
* L20 — ``place`` defaults ``--transmit false``; ``--transmit true`` requires
  ``--confirm-transmit PAPER`` (token must match exactly).
* L21 — ``cancel`` requires ``--intent-id`` and ``--confirm-cancel``.
* L22 — ``status`` requires ``--intent-id``.
* L23 — ``place`` requires an explicit ``--limit-price``. ``order_type`` is
  always ``LMT``; market orders are not representable.
* L25 — every subcommand requires ``--account``.

The real ``IbkrOrderClient`` is wired by T7b. Until then ``_build_client``
raises a clear ``SystemExit`` if invoked directly; tests monkeypatch it to
inject a fake matching the ``BrokerOrderClient`` Protocol.

Exit codes:

* 0 — intent reached ``completed``.
* 1 — intent reached a non-completed terminal state (``sent``/``failed``/
  ``rejected``); provenance is still persisted.
* 2 — ``OrderSafetyError`` raised before the broker was called.
* 3 — ``OrderDuplicateError`` (idempotency collision).
* 4 — any other ``OrderError`` subclass.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path[:0] = [str(Path(__file__).resolve().parent.parent)]

from marketpulse.broker import order_service  # noqa: E402
from marketpulse.broker.order_client import BrokerOrderClient  # noqa: E402
from marketpulse.broker.order_types import (  # noqa: E402
    BrokerOrderRequest,
    OrderDuplicateError,
    OrderError,
    OrderSafetyError,
)
from marketpulse.config import get_settings  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", help="override DATABASE_URL")
    subs = parser.add_subparsers(dest="command", required=True)

    p = subs.add_parser("place", help="Place a paper STK LMT order")
    p.add_argument("--account", required=True)
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", choices=["BUY", "SELL"], required=True)
    p.add_argument("--quantity", type=Decimal, required=True)
    p.add_argument(
        "--limit-price",
        type=Decimal,
        required=True,
        help="Required. Market orders are rejected (L23).",
    )
    p.add_argument("--transmit", choices=["true", "false"], default="false")
    p.add_argument(
        "--confirm-transmit",
        help="Required when --transmit true. Must equal 'PAPER'.",
    )
    p.add_argument(
        "--idempotency-key",
        help="Optional; generated if absent.",
    )

    s = subs.add_parser("status", help="Observe order status for a place intent")
    s.add_argument("--account", required=True)
    s.add_argument("--intent-id", type=int, required=True)

    c = subs.add_parser("cancel", help="Cancel a previously placed order")
    c.add_argument("--account", required=True)
    c.add_argument("--intent-id", type=int, required=True)
    c.add_argument(
        "--confirm-cancel",
        action="store_true",
        help="Required to actually cancel.",
    )

    return parser


def _build_client(account_id: str | None = None) -> BrokerOrderClient:
    """Build the real broker client.

    ``account_id`` is required for the real wiring (the adapter validates that
    TWS' ``managedAccounts`` includes this account before any place/cancel
    call). The default-``None`` arg is preserved for backwards compatibility
    with tests that monkeypatch ``_build_client`` without a binding;
    invoking the real wiring without an account raises ``SystemExit``.
    """

    if not account_id:
        raise SystemExit(
            "IbkrOrderClient requires --account; pass account_id "
            "explicitly when constructing _build_client outside tests."
        )
    # Lazy import to keep the ``ibapi`` dependency out of test import paths
    # that monkeypatch this function before any real call.
    from marketpulse.broker.ibkr_order_client import IbkrOrderClient

    settings = get_settings()
    return IbkrOrderClient(
        host=settings.ibkr_order_host,
        port=settings.ibkr_order_port,
        client_id=settings.ibkr_order_client_id,
        account_id=account_id,
        connect_timeout_seconds=settings.ibkr_order_connect_timeout_seconds,
        next_valid_id_timeout_seconds=(
            settings.ibkr_order_next_valid_id_timeout_seconds
        ),
        observation_timeout_seconds=(
            settings.ibkr_order_observation_timeout_seconds
        ),
    )


def _gen_idempotency_key() -> str:
    ts = datetime.now(UTC).timestamp()
    return f"place-{ts}-{secrets.token_hex(4)}"


def _print_connect_banner() -> None:
    """Emit a one-line "connecting to TWS" banner on stderr.

    Goes to stderr so stdout stays parseable (the result-printer writes
    key:value lines on stdout).
    """

    settings = get_settings()
    print(
        f"connecting to TWS at {settings.ibkr_order_host}:{settings.ibkr_order_port} "
        f"(client_id={settings.ibkr_order_client_id})",
        file=sys.stderr,
    )


def _do_place(args: argparse.Namespace, session: Session) -> int:
    if args.transmit == "true" and args.confirm_transmit != "PAPER":
        raise SystemExit(
            "--confirm-transmit PAPER required when --transmit true (L20)"
        )

    key = args.idempotency_key or _gen_idempotency_key()
    request = BrokerOrderRequest(
        account_id=args.account,
        symbol=args.symbol,
        side=args.side,
        quantity=args.quantity,
        order_type="LMT",
        limit_price=args.limit_price,
        asset_class="STK",
        local_idempotency_key=key,
        transmit=(args.transmit == "true"),
    )
    _print_connect_banner()
    client = _build_client(args.account)
    result = order_service.place_order(
        session,
        client=client,
        request=request,
        confirm_transmit=(args.confirm_transmit == "PAPER"),
    )
    session.commit()
    _print_intent_result(result, label="place")
    return 0 if result.status == "completed" else 1


def _do_status(args: argparse.Namespace, session: Session) -> int:
    _print_connect_banner()
    client = _build_client(args.account)
    result = order_service.fetch_status(
        session, client=client, intent_id=args.intent_id
    )
    session.commit()
    _print_intent_result(result, label="status")
    return 0 if result.status == "completed" else 1


def _do_cancel(args: argparse.Namespace, session: Session) -> int:
    if not args.confirm_cancel:
        raise SystemExit("--confirm-cancel required (L21)")
    _print_connect_banner()
    client = _build_client(args.account)
    result = order_service.cancel_order(
        session,
        client=client,
        intent_id=args.intent_id,
        confirm_cancel=True,
    )
    session.commit()
    _print_intent_result(result, label="cancel")
    return 0 if result.status == "completed" else 1


def _print_intent_result(result, *, label: str) -> None:
    intent = result.intent
    print(f"command: {label}")
    print("transport: ibapi")
    print(f"intent_id: {intent.id}")
    print(f"intent_status: {result.status}")
    print(f"account: {intent.account_id}")
    print(f"action: {intent.action}")
    if intent.broker_order_id:
        print(f"broker_order_id: {intent.broker_order_id}")
    if intent.broker_perm_id:
        print(f"broker_perm_id: {intent.broker_perm_id}")
    if getattr(intent, "local_idempotency_key", None):
        print(f"local_idempotency_key: {intent.local_idempotency_key}")
    print(f"events: {len(result.events)}")
    for ev in result.events:
        extras = []
        if ev.broker_status:
            extras.append(f"broker_status={ev.broker_status}")
        if ev.filled_quantity is not None:
            extras.append(f"filled={ev.filled_quantity}")
        if ev.message:
            extras.append(f"msg={ev.message!r}")
        extra_str = " " + " ".join(extras) if extras else ""
        print(f"  - {ev.event_type} ({ev.event_source}){extra_str}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    db_url = args.db_url or settings.database_url
    engine = create_engine(db_url)
    try:
        with Session(engine) as session:
            try:
                if args.command == "place":
                    return _do_place(args, session)
                if args.command == "status":
                    return _do_status(args, session)
                if args.command == "cancel":
                    return _do_cancel(args, session)
                raise SystemExit(f"unknown command: {args.command}")
            except OrderDuplicateError as exc:
                print(f"error: OrderDuplicateError: {exc}", file=sys.stderr)
                return 3
            except OrderSafetyError as exc:
                print(f"error: OrderSafetyError: {exc}", file=sys.stderr)
                return 2
            except OrderError as exc:
                print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 4
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
