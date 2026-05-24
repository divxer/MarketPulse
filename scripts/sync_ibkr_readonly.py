"""Run one IBKR read-only broker snapshot sync."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path[:0] = [str(Path(__file__).resolve().parent.parent)]

from marketpulse.broker.ibkr_client import IbkrReadClient  # noqa: E402
from marketpulse.broker.readonly_sync import (  # noqa: E402
    IbkrSyncConfig,
    _execution_window,
    run_readonly_sync,
)
from marketpulse.broker.types import SyncResult, classify_broker_environment  # noqa: E402
from marketpulse.config import get_settings  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--client-id", type=int)
    parser.add_argument("--account-id")
    parser.add_argument("--timeout-seconds", type=int)
    parser.add_argument("--db-url")
    return parser


def _config(args: argparse.Namespace) -> tuple[IbkrSyncConfig, str]:
    settings = get_settings()
    host = args.host or settings.ibkr_host
    port = args.port if args.port is not None else settings.ibkr_port
    client_id = args.client_id if args.client_id is not None else settings.ibkr_client_id
    account_id = args.account_id if args.account_id is not None else settings.ibkr_account_id
    timeout_seconds = (
        args.timeout_seconds
        if args.timeout_seconds is not None
        else settings.ibkr_connect_timeout_seconds
    )
    return (
        IbkrSyncConfig(
            host=host,
            port=port,
            client_id=client_id,
            account_id=account_id,
            timeout_seconds=timeout_seconds,
            allow_live=settings.ibkr_allow_live,
        ),
        args.db_url or settings.database_url,
    )


def _run(args: argparse.Namespace) -> SyncResult:
    config, db_url = _config(args)
    environment = classify_broker_environment(config.port)
    now = datetime.now(UTC)
    window_start, _ = _execution_window(now)
    client = IbkrReadClient(
        host=config.host,
        port=config.port,
        client_id=config.client_id,
        timeout_seconds=config.timeout_seconds,
        account_id=config.account_id,
        broker_environment=environment,
        execution_window_start=window_start,
    )
    engine = create_engine(db_url)
    with Session(engine) as session:
        result = run_readonly_sync(session, client=client, config=config, now=now)
        session.commit()
        return result


def _print_result(result: SyncResult) -> None:
    print(f"sync_run_id: {result.sync_run_id}")
    print(f"broker: {result.broker}")
    print(f"broker_environment: {result.broker_environment}")
    print(f"account: {result.account_id or 'unknown'}")
    print(f"host: {result.host}")
    print(f"port: {result.port}")
    print(f"client_id: {result.client_id}")
    print(f"status: {result.status}")
    if result.status == "completed":
        print(f"account snapshots: {result.account_snapshots}")
        print(f"cash rows: {result.cash_rows}")
        print(f"positions: {result.positions}")
        print(f"open orders: {result.open_orders}")
        print(f"executions: {result.executions}")
    else:
        print(f"error_type: {result.error_type}")
        print(f"error_message: {result.error_message}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = _run(args)
    _print_result(result)
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
