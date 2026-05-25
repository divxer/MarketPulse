"""Operator debug CLI — list all open IBKR orders in the current TWS/Gateway session.

Bypasses the Phase 7b production order service's "locally-known intent only"
restriction (spec L15/L42/L46). Use this when the production CLI parked an
intent at `sent` status without capturing a `broker_order_id` (callback
timeout, off-hours, etc.) and you need to know whether the order actually
landed at IBKR.

This script:
  - Imports `ibapi` directly (scripts/ is outside the production architecture
    guard's ibapi deny-list; the guard only scans `marketpulse/`).
  - Connects to the same TWS/Gateway as the production CLI.
  - Calls `reqAllOpenOrders` and prints every order in the account.
  - Disconnects cleanly.

NOT a production code path. Does NOT write to broker_order_intent or any other
table. Read-only observation only.

Usage:
    uv run python scripts/ibkr_debug_open_orders.py \\
      --host ib-gateway --port 4002 --client-id 99 \\
      --account DUE411848

Defaults read from IBKR_ORDER_HOST / IBKR_ORDER_PORT / IBKR_ORDER_CLIENT_ID
env. Use a DIFFERENT client_id than 72 (the production one) so we don't get
kicked off our own running connection.
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent.parent)]

from ibapi.client import EClient  # noqa: E402
from ibapi.wrapper import EWrapper  # noqa: E402

from marketpulse.config import get_settings  # noqa: E402


class _DebugApp(EWrapper, EClient):
    """Minimal ibapi callback collector. Lives only in this script."""

    def __init__(self) -> None:
        EClient.__init__(self, self)
        self.open_orders: list[dict] = []
        self.open_orders_done = threading.Event()
        self.next_valid_id_event = threading.Event()
        self.managed_accounts_event = threading.Event()
        self.managed_accounts: tuple[str, ...] = ()

    def nextValidId(self, orderId: int) -> None:
        self.next_valid_id_event.set()

    def managedAccounts(self, accountsList: str) -> None:
        self.managed_accounts = tuple(a.strip() for a in accountsList.split(",") if a.strip())
        self.managed_accounts_event.set()

    def openOrder(self, orderId, contract, order, orderState):  # noqa: ANN001
        self.open_orders.append({
            "order_id": orderId,
            "symbol": getattr(contract, "symbol", None),
            "sec_type": getattr(contract, "secType", None),
            "exchange": getattr(contract, "exchange", None),
            "action": getattr(order, "action", None),
            "quantity": getattr(order, "totalQuantity", None),
            "order_type": getattr(order, "orderType", None),
            "limit_price": getattr(order, "lmtPrice", None),
            "tif": getattr(order, "tif", None),
            "order_ref": getattr(order, "orderRef", None),
            "transmit": getattr(order, "transmit", None),
            "status": getattr(orderState, "status", None),
            "account": getattr(order, "account", None),
            "perm_id": getattr(order, "permId", None),
        })

    def openOrderEnd(self) -> None:
        self.open_orders_done.set()

    def error(self, reqId, errorCode, errorString, *args, **kwargs):  # noqa: ANN001
        # IBKR sends informational "errors" too (2104 = market data farm OK).
        # Print everything; operator can interpret.
        print(f"[ibapi error] reqId={reqId} code={errorCode} msg={errorString}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    s = get_settings()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default=s.ibkr_order_host)
    p.add_argument("--port", type=int, default=s.ibkr_order_port)
    p.add_argument(
        "--client-id",
        type=int,
        default=99,
        help="ibapi client ID. Use a value DIFFERENT from production (72) "
             "to avoid kicking the running CLI's connection.",
    )
    p.add_argument(
        "--account",
        help="Optional account filter. If set, prints only orders for this "
             "account; otherwise prints all accounts the connection sees.",
    )
    p.add_argument(
        "--connect-timeout",
        type=int,
        default=s.ibkr_order_connect_timeout_seconds,
        help="seconds to wait for managedAccounts callback",
    )
    p.add_argument(
        "--observe-seconds",
        type=int,
        default=5,
        help="seconds to wait after reqAllOpenOrders for openOrderEnd",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    app = _DebugApp()
    print(f"connecting to TWS at {args.host}:{args.port} (client_id={args.client_id})",
          file=sys.stderr)
    try:
        app.connect(args.host, args.port, args.client_id)
    except Exception as exc:
        print(f"connection_failed: {exc}", file=sys.stderr)
        return 2

    reader = threading.Thread(target=app.run, daemon=True, name="ibkr-debug-reader")
    reader.start()

    try:
        if not app.managed_accounts_event.wait(args.connect_timeout):
            print("managedAccounts callback timeout", file=sys.stderr)
            return 3
        print(f"managed_accounts: {app.managed_accounts}", file=sys.stderr)
        if args.account and args.account not in app.managed_accounts:
            print(f"requested account {args.account!r} not in managed accounts",
                  file=sys.stderr)
            return 4

        app.reqAllOpenOrders()
        if not app.open_orders_done.wait(args.observe_seconds):
            print(f"openOrderEnd not received within {args.observe_seconds}s; "
                  f"printing what we got so far ({len(app.open_orders)} orders)",
                  file=sys.stderr)

        orders = app.open_orders
        if args.account:
            orders = [o for o in orders if o.get("account") == args.account]

        print(f"\n=== {len(orders)} open order(s) ===")
        if not orders:
            print("(none)")
        for o in orders:
            print(
                f"  order_id={o['order_id']:>6}  "
                f"acct={o['account']}  "
                f"{o['action']:>4} {o['quantity']} {o['symbol']:<6}  "
                f"{o['order_type']}@{o['limit_price']:.4f}  "
                f"tif={o['tif']}  status={o['status']}  "
                f"perm_id={o['perm_id']}  "
                f"orderRef={o['order_ref']!r}"
            )
        return 0
    finally:
        try:
            app.disconnect()
        except Exception:
            pass
        reader.join(timeout=2.0)


if __name__ == "__main__":
    raise SystemExit(main())
