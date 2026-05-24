"""One-shot read-only broker sync orchestration (Phase 7a-Flex)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from marketpulse.broker.flex_client import (
    FlexReportTimeoutError,
    LiveAccountRefusedError,
)
from marketpulse.broker.read_client import BrokerReadClient
from marketpulse.broker.repository import (
    create_started_run,
    mark_run_completed,
    mark_run_failed,
    persist_snapshot_rows,
)
from marketpulse.broker.types import (
    BrokerEnvironment,
    SyncResult,
    classify_broker_environment_from_account_id,
)
from marketpulse.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class FlexSyncConfig:
    token: str
    query_id: int
    base_url: str
    account_id: str | None
    poll_interval_seconds: int
    max_wait_seconds: int
    allow_live: bool


class AccountMismatchError(RuntimeError):
    """Snapshot's account_id disagrees with configured account_id."""


def _base_context(config: FlexSyncConfig, *, selected_account_id: str | None) -> dict:
    return {
        "transport": "flex",
        "endpoint": config.base_url,
        "query_id": config.query_id,
        "configured_account_id": config.account_id,
        "selected_account_id": selected_account_id,
        "allow_live": config.allow_live,
        "poll_interval_seconds": config.poll_interval_seconds,
        "max_wait_seconds": config.max_wait_seconds,
    }


def run_readonly_sync(
    session: Session,
    *,
    client: BrokerReadClient,
    config: FlexSyncConfig,
    now: datetime | None = None,
) -> SyncResult:
    started_at = (now or datetime.now(UTC)).astimezone(UTC)
    initial_environment: BrokerEnvironment = (
        classify_broker_environment_from_account_id(config.account_id)
        if config.account_id
        else "unknown"
    )
    run = create_started_run(
        session,
        started_at=started_at,
        broker="IBKR",
        broker_environment=initial_environment,
        account_id=config.account_id,
        context=_base_context(config, selected_account_id=None),
    )

    reference_code: str | None = None
    try:
        snapshot = client.fetch_snapshot()
        reference_code = getattr(client, "reference_code", None)

        if config.account_id and snapshot.account_id != config.account_id:
            raise AccountMismatchError(
                f"Configured account {config.account_id} != returned {snapshot.account_id}"
            )

        # Live-account brake (L21): unknown is treated like live.
        if snapshot.broker_environment != "paper" and not config.allow_live:
            raise LiveAccountRefusedError(
                f"Refusing to capture {snapshot.broker_environment} account "
                f"{snapshot.account_id}; set MP_IBKR_ALLOW_LIVE=true to override"
            )

        counts = persist_snapshot_rows(session, sync_run_id=run.id, snapshot=snapshot)
        context_patch = _base_context(config, selected_account_id=snapshot.account_id)
        if reference_code:
            context_patch["reference_code"] = reference_code
        mark_run_completed(
            session,
            sync_run_id=run.id,
            completed_at=snapshot.captured_at,
            account_id=snapshot.account_id,
            context_patch=context_patch,
        )
        session.flush()
        return SyncResult(
            sync_run_id=run.id,
            broker="IBKR",
            broker_environment=snapshot.broker_environment,
            account_id=snapshot.account_id,
            status="completed",
            transport="flex",
            endpoint=config.base_url,
            query_id=config.query_id,
            reference_code=reference_code,
            **counts,
        )
    except Exception as exc:
        # Preserve reference_code even on failure (L11/L22).
        if reference_code is None:
            reference_code = getattr(client, "reference_code", None)
        if isinstance(exc, FlexReportTimeoutError) and exc.reference_code:
            reference_code = exc.reference_code

        context_patch = _base_context(config, selected_account_id=None)
        if reference_code:
            context_patch["reference_code"] = reference_code

        try:
            mark_run_failed(
                session,
                sync_run_id=run.id,
                completed_at=datetime.now(UTC),
                error_type=type(exc).__name__,
                error_message=str(exc),
                context_patch=context_patch,
            )
            session.flush()
        except Exception as commit_exc:  # noqa: BLE001
            log.warning(
                "broker_sync_mark_run_failed_failed",
                original_error_type=type(exc).__name__,
                original_error=str(exc),
                commit_error_type=type(commit_exc).__name__,
                commit_error=str(commit_exc),
            )
        return SyncResult(
            sync_run_id=run.id,
            broker="IBKR",
            broker_environment=initial_environment,
            account_id=None,
            status="failed",
            transport="flex",
            endpoint=config.base_url,
            query_id=config.query_id,
            reference_code=reference_code,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
