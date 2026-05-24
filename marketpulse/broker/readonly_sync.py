"""One-shot read-only broker sync orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from marketpulse.broker.read_client import BrokerReadClient
from marketpulse.broker.repository import (
    create_started_run,
    mark_run_completed,
    mark_run_failed,
    persist_snapshot_rows,
)
from marketpulse.broker.types import BrokerEnvironment, SyncResult, classify_broker_environment
from marketpulse.logging import get_logger

log = get_logger(__name__)

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class IbkrSyncConfig:
    host: str
    port: int
    client_id: int
    account_id: str
    timeout_seconds: int
    allow_live: bool


class LivePortBlockedError(RuntimeError):
    pass


class AccountMismatchError(RuntimeError):
    pass


def _execution_window(now: datetime) -> tuple[datetime, datetime]:
    now_utc = now.astimezone(UTC)
    ny_date = now_utc.astimezone(NY).date()
    start_ny = datetime.combine(ny_date, time.min, tzinfo=NY)
    return start_ny.astimezone(UTC), now_utc


def _base_context(
    config: IbkrSyncConfig,
    *,
    selected_account_id: str | None,
    window_start: datetime | None,
    window_end: datetime | None,
) -> dict:
    return {
        "host": config.host,
        "port": config.port,
        "client_id": config.client_id,
        "configured_account_id": config.account_id or None,
        "selected_account_id": selected_account_id,
        "allow_live": config.allow_live,
        "execution_window_start": window_start.isoformat() if window_start else None,
        "execution_window_end": window_end.isoformat() if window_end else None,
    }


def run_readonly_sync(
    session: Session,
    *,
    client: BrokerReadClient,
    config: IbkrSyncConfig,
    now: datetime | None = None,
) -> SyncResult:
    started_at = (now or datetime.now(UTC)).astimezone(UTC)
    environment: BrokerEnvironment = classify_broker_environment(config.port)
    window_start, window_end = _execution_window(started_at)
    run = create_started_run(
        session,
        started_at=started_at,
        broker="IBKR",
        broker_environment=environment,
        account_id=config.account_id or None,
        context=_base_context(
            config,
            selected_account_id=None,
            window_start=window_start,
            window_end=window_end,
        ),
    )

    try:
        if environment == "live" and not config.allow_live:
            raise LivePortBlockedError("Refusing to connect to known IBKR live port 7496")

        snapshot = client.fetch_snapshot()
        if config.account_id and snapshot.account_id != config.account_id:
            raise AccountMismatchError(
                f"Configured account {config.account_id} does not match "
                f"returned account {snapshot.account_id}"
            )

        counts = persist_snapshot_rows(session, sync_run_id=run.id, snapshot=snapshot)
        mark_run_completed(
            session,
            sync_run_id=run.id,
            completed_at=snapshot.captured_at,
            account_id=snapshot.account_id,
            context_patch=_base_context(
                config,
                selected_account_id=snapshot.account_id,
                window_start=window_start,
                window_end=window_end,
            ),
        )
        session.flush()
        return SyncResult(
            sync_run_id=run.id,
            broker="IBKR",
            broker_environment=environment,
            account_id=snapshot.account_id,
            status="completed",
            host=config.host,
            port=config.port,
            client_id=config.client_id,
            **counts,
        )
    except Exception as exc:
        try:
            mark_run_failed(
                session,
                sync_run_id=run.id,
                completed_at=datetime.now(UTC),
                error_type=type(exc).__name__,
                error_message=str(exc),
                context_patch=_base_context(
                    config,
                    selected_account_id=None,
                    window_start=window_start,
                    window_end=window_end,
                ),
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
            broker_environment=environment,
            account_id=None,
            status="failed",
            host=config.host,
            port=config.port,
            client_id=config.client_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
