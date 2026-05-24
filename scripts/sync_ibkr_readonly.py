"""Run one IBKR Flex read-only broker snapshot sync.

Phase 7a-Flex: pulls broker truth via IBKR's Flex Web Service. Configure
IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID in your environment (see
docs/operations/ibkr-readonly-sync-runbook.md for the IBKR Portal setup).
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path[:0] = [str(Path(__file__).resolve().parent.parent)]

from marketpulse.broker.flex_client import FlexClient  # noqa: E402
from marketpulse.broker.readonly_sync import (  # noqa: E402
    FlexSyncConfig,
    run_readonly_sync,
)
from marketpulse.broker.types import SyncResult  # noqa: E402
from marketpulse.config import get_settings  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", help="override IBKR_FLEX_TOKEN")
    parser.add_argument("--query-id", type=int, help="override IBKR_FLEX_QUERY_ID")
    parser.add_argument("--base-url", help="override IBKR_FLEX_BASE_URL")
    parser.add_argument("--account-id", help="override IBKR_ACCOUNT_ID")
    parser.add_argument("--poll-interval-seconds", type=int)
    parser.add_argument("--max-wait-seconds", type=int)
    parser.add_argument("--db-url")
    return parser


def _config(args: argparse.Namespace) -> tuple[FlexSyncConfig, str]:
    settings = get_settings()
    token = args.token or settings.ibkr_flex_token
    query_id = args.query_id if args.query_id is not None else settings.ibkr_flex_query_id
    if not token:
        raise SystemExit("IBKR_FLEX_TOKEN is not set (or --token not given)")
    if not query_id:
        raise SystemExit("IBKR_FLEX_QUERY_ID is not set (or --query-id not given)")
    return (
        FlexSyncConfig(
            token=token,
            query_id=query_id,
            base_url=args.base_url or settings.ibkr_flex_base_url,
            account_id=args.account_id or settings.ibkr_account_id or None,
            poll_interval_seconds=(
                args.poll_interval_seconds
                if args.poll_interval_seconds is not None
                else settings.ibkr_flex_poll_interval_seconds
            ),
            max_wait_seconds=(
                args.max_wait_seconds
                if args.max_wait_seconds is not None
                else settings.ibkr_flex_max_wait_seconds
            ),
            allow_live=settings.ibkr_allow_live,
        ),
        args.db_url or settings.database_url,
    )


def _run(args: argparse.Namespace) -> SyncResult:
    config, db_url = _config(args)
    engine = create_engine(db_url)
    with FlexClient(
        token=config.token,
        query_id=config.query_id,
        account_id=config.account_id,
        base_url=config.base_url,
        poll_interval_seconds=config.poll_interval_seconds,
        max_wait_seconds=config.max_wait_seconds,
    ) as client, Session(engine) as session:
        result = run_readonly_sync(session, client=client, config=config, now=datetime.now(UTC))
        session.commit()
        return result


def _print_result(result: SyncResult) -> None:
    print(f"sync_run_id: {result.sync_run_id}")
    print(f"broker: {result.broker}")
    print(f"broker_environment: {result.broker_environment}")
    print(f"account: {result.account_id or 'unknown'}")
    print(f"transport: {result.transport}")
    print(f"endpoint: {result.endpoint}")
    print(f"query_id: {result.query_id}")
    if result.reference_code:
        print(f"reference_code: {result.reference_code}")
    print(f"status: {result.status}")
    if result.status == "completed":
        print(f"account snapshots: {result.account_snapshots}")
        print(f"cash rows: {result.cash_rows}")
        print(f"positions: {result.positions}")
        print(f"open orders: {result.open_orders} (not available via Flex Activity)")
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
