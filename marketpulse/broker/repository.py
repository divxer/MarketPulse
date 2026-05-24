"""Append-only broker snapshot writes for Phase 7a."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from marketpulse.broker.types import BrokerEnvironment, BrokerName, BrokerSnapshot
from marketpulse.db.models import (
    BrokerAccountSnapshot,
    BrokerCashSnapshot,
    BrokerExecutionSnapshot,
    BrokerOpenOrderSnapshot,
    BrokerPositionSnapshot,
    BrokerSyncRun,
)


def create_started_run(
    session: Session,
    *,
    started_at: datetime,
    broker: BrokerName,
    broker_environment: BrokerEnvironment,
    account_id: str | None,
    context: dict,
) -> BrokerSyncRun:
    run = BrokerSyncRun(
        started_at=started_at,
        completed_at=None,
        broker=broker,
        broker_environment=broker_environment,
        account_id=account_id,
        status="started",
        error_type=None,
        error_message=None,
        context=context,
    )
    session.add(run)
    session.flush()
    return run


def mark_run_completed(
    session: Session,
    *,
    sync_run_id: int,
    completed_at: datetime,
    account_id: str,
    context_patch: dict | None = None,
) -> None:
    run = session.get(BrokerSyncRun, sync_run_id)
    if run is None:
        raise ValueError(f"broker_sync_run not found: {sync_run_id}")
    run.completed_at = completed_at
    run.account_id = account_id
    run.status = "completed"
    if context_patch:
        run.context = {**(run.context or {}), **context_patch}
    session.flush()


def mark_run_failed(
    session: Session,
    *,
    sync_run_id: int,
    completed_at: datetime,
    error_type: str,
    error_message: str,
    context_patch: dict | None = None,
) -> None:
    run = session.get(BrokerSyncRun, sync_run_id)
    if run is None:
        raise ValueError(f"broker_sync_run not found: {sync_run_id}")
    run.completed_at = completed_at
    run.status = "failed"
    run.error_type = error_type
    run.error_message = error_message
    if context_patch:
        run.context = {**(run.context or {}), **context_patch}
    session.flush()


SnapshotCounts = dict[
    Literal["account_snapshots", "cash_rows", "positions", "open_orders", "executions"],
    int,
]


def _assert_child_account(snapshot_account_id: str, child_account_id: str) -> None:
    if child_account_id != snapshot_account_id:
        raise ValueError(
            "snapshot child account mismatch: "
            f"{child_account_id} != {snapshot_account_id}"
        )


def persist_snapshot_rows(
    session: Session,
    *,
    sync_run_id: int,
    snapshot: BrokerSnapshot,
) -> SnapshotCounts:
    account = snapshot.account
    _assert_child_account(snapshot.account_id, account.account_id)
    for child in (*snapshot.cash, *snapshot.positions, *snapshot.open_orders, *snapshot.executions):
        _assert_child_account(snapshot.account_id, child.account_id)
    common = {
        "sync_run_id": sync_run_id,
        "account_id": snapshot.account_id,
        "broker_environment": snapshot.broker_environment,
        "captured_at": snapshot.captured_at,
    }
    session.add(
        BrokerAccountSnapshot(
            **common,
            account_type=account.account_type,
            base_currency=account.base_currency,
            net_liquidation=account.net_liquidation,
            buying_power=account.buying_power,
            maintenance_margin=account.maintenance_margin,
            excess_liquidity=account.excess_liquidity,
        )
    )
    for cash in snapshot.cash:
        session.add(
            BrokerCashSnapshot(
                **common,
                currency=cash.currency,
                cash_balance=cash.cash_balance,
                settled_cash=cash.settled_cash,
                accrued_interest=cash.accrued_interest,
            )
        )
    for position in snapshot.positions:
        session.add(
            BrokerPositionSnapshot(
                **common,
                symbol=position.symbol,
                asset_class=position.asset_class,
                quantity=position.quantity,
                avg_cost=position.avg_cost,
                market_price=position.market_price,
                market_value=position.market_value,
                unrealized_pnl=position.unrealized_pnl,
                realized_pnl=position.realized_pnl,
            )
        )
    for order in snapshot.open_orders:
        session.add(
            BrokerOpenOrderSnapshot(
                **common,
                broker_order_id=order.broker_order_id,
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                quantity=order.quantity,
                limit_price=order.limit_price,
                status=order.status,
            )
        )
    for execution in snapshot.executions:
        session.add(
            BrokerExecutionSnapshot(
                **common,
                broker_exec_id=execution.broker_exec_id,
                broker_order_id=execution.broker_order_id,
                symbol=execution.symbol,
                side=execution.side,
                quantity=execution.quantity,
                price=execution.price,
                executed_at=execution.executed_at,
            )
        )
    session.flush()
    return {
        "account_snapshots": 1,
        "cash_rows": len(snapshot.cash),
        "positions": len(snapshot.positions),
        "open_orders": len(snapshot.open_orders),
        "executions": len(snapshot.executions),
    }
