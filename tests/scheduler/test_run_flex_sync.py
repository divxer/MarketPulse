# Layer: unit
"""Tests for the Phase 7a-Flex daily broker truth sync scheduler job."""

from unittest.mock import MagicMock

import pytest

from marketpulse.broker.types import SyncResult
from marketpulse.scheduler.jobs import build_scheduler, run_flex_sync


def _set_settings(monkeypatch, *, token: str, query_id: str) -> None:
    """Reset cached settings with the given Flex env values."""
    monkeypatch.setenv("IBKR_FLEX_TOKEN", token)
    monkeypatch.setenv("IBKR_FLEX_QUERY_ID", query_id)
    # Required base env so Settings() can construct.
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    from marketpulse.config import get_settings
    get_settings.cache_clear()


def test_flex_sync_job_registered():
    """build_scheduler() must add the daily flex_sync cron."""
    scheduler = build_scheduler()
    job = scheduler.get_job("flex_sync")
    assert job is not None
    trigger_repr = str(job.trigger)
    # Default 23:30 NY.
    assert "hour='23'" in trigger_repr or "hour=23" in trigger_repr, trigger_repr
    assert "minute='30'" in trigger_repr or "minute=30" in trigger_repr, trigger_repr
    assert "mon-fri" in trigger_repr or "0-4" in trigger_repr or "1-5" in trigger_repr, (
        trigger_repr
    )


def test_skips_when_token_missing(monkeypatch):
    """Empty IBKR_FLEX_TOKEN → log + return; no FlexClient instantiation."""
    _set_settings(monkeypatch, token="", query_id="12345")
    import marketpulse.scheduler.jobs as jobs_mod
    monkeypatch.setattr(
        jobs_mod, "session_scope",
        lambda: (_ for _ in ()).throw(AssertionError("session_scope must not be called")),
    )
    # If the job tried to construct FlexClient it would explode on no token.
    run_flex_sync()  # must not raise


def test_skips_when_query_id_zero(monkeypatch):
    """Zero IBKR_FLEX_QUERY_ID → log + return; no FlexClient instantiation."""
    _set_settings(monkeypatch, token="tok", query_id="0")
    import marketpulse.scheduler.jobs as jobs_mod
    monkeypatch.setattr(
        jobs_mod, "session_scope",
        lambda: (_ for _ in ()).throw(AssertionError("session_scope must not be called")),
    )
    run_flex_sync()  # must not raise


def test_invokes_full_pipeline_when_configured(monkeypatch):
    """With both env vars set, job builds FlexClient + calls run_readonly_sync + commits."""
    _set_settings(monkeypatch, token="tok-abc", query_id="98765")

    import marketpulse.broker.flex_client as flex_mod
    import marketpulse.broker.readonly_sync as sync_mod
    import marketpulse.scheduler.jobs as jobs_mod

    fake_db = MagicMock()
    monkeypatch.setattr(jobs_mod, "session_scope", lambda: iter([fake_db]))
    monkeypatch.setattr(jobs_mod, "record_run_summary", lambda *a, **kw: None)

    # FlexClient must act as a context manager.
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    flex_ctor = MagicMock(return_value=fake_client)
    monkeypatch.setattr(flex_mod, "FlexClient", flex_ctor)

    result = SyncResult(
        sync_run_id=42,
        broker="ibkr",
        broker_environment="paper",
        account_id="DU1234567",
        status="completed",
        transport="flex",
        endpoint="https://example/Universal/servlet/FlexStatementService.SendRequest",
        query_id=98765,
        account_snapshots=1,
        cash_rows=2,
        positions=3,
        executions=4,
    )
    sync_spy = MagicMock(return_value=result)
    monkeypatch.setattr(sync_mod, "run_readonly_sync", sync_spy)

    run_flex_sync()

    flex_ctor.assert_called_once()
    kwargs = flex_ctor.call_args.kwargs
    assert kwargs["token"] == "tok-abc"
    assert kwargs["query_id"] == 98765

    sync_spy.assert_called_once()
    fake_db.commit.assert_called_once()
    fake_db.close.assert_called_once()


def test_db_closed_even_when_sync_raises(monkeypatch):
    """If run_readonly_sync raises, the session is still closed (finally block)."""
    _set_settings(monkeypatch, token="tok", query_id="1")

    import marketpulse.broker.flex_client as flex_mod
    import marketpulse.broker.readonly_sync as sync_mod
    import marketpulse.scheduler.jobs as jobs_mod

    fake_db = MagicMock()
    monkeypatch.setattr(jobs_mod, "session_scope", lambda: iter([fake_db]))

    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(flex_mod, "FlexClient", MagicMock(return_value=fake_client))
    monkeypatch.setattr(
        sync_mod, "run_readonly_sync",
        MagicMock(side_effect=RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError):
        run_flex_sync()
    fake_db.close.assert_called_once()


# === Holiday / business-day skip ===


def _patch_today_ny(monkeypatch, ny_date):
    """Force NYTradingCalendar.is_business_day(today_ny) by patching the
    NY 'today' datetime that run_flex_sync reads.
    """
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    import marketpulse.scheduler.jobs as jobs_mod
    fake_now_ny_aware = datetime.combine(
        ny_date, datetime.min.time().replace(hour=23, minute=30),
        tzinfo=ZoneInfo("America/New_York"),
    )
    fake_now_utc = fake_now_ny_aware.astimezone(UTC)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now_utc if tz is None else fake_now_utc.astimezone(tz)
    monkeypatch.setattr(jobs_mod, "datetime", _DT)


def test_skips_on_us_market_holiday(monkeypatch):
    """Memorial Day 2026-05-25 → no SendRequest, no session_scope call."""
    from datetime import date
    _set_settings(monkeypatch, token="tok", query_id="1")
    import marketpulse.scheduler.jobs as jobs_mod
    monkeypatch.setattr(
        jobs_mod, "session_scope",
        lambda: (_ for _ in ()).throw(AssertionError("must not open DB")),
    )
    _patch_today_ny(monkeypatch, date(2026, 5, 25))  # Monday, Memorial Day
    run_flex_sync()  # must not raise


def test_skips_on_weekend(monkeypatch):
    """Saturday 2026-05-23 → skip."""
    from datetime import date
    _set_settings(monkeypatch, token="tok", query_id="1")
    import marketpulse.scheduler.jobs as jobs_mod
    monkeypatch.setattr(
        jobs_mod, "session_scope",
        lambda: (_ for _ in ()).throw(AssertionError("must not open DB")),
    )
    _patch_today_ny(monkeypatch, date(2026, 5, 23))
    run_flex_sync()


def test_proceeds_on_normal_trading_day(monkeypatch):
    """Friday 2026-05-22 → proceeds past the skip into the pipeline."""
    from datetime import date
    _set_settings(monkeypatch, token="tok", query_id="1")
    import marketpulse.broker.flex_client as flex_mod
    import marketpulse.broker.readonly_sync as sync_mod
    import marketpulse.scheduler.jobs as jobs_mod

    fake_db = MagicMock()
    monkeypatch.setattr(jobs_mod, "session_scope", lambda: iter([fake_db]))
    monkeypatch.setattr(jobs_mod, "record_run_summary", lambda *a, **kw: None)

    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(flex_mod, "FlexClient", MagicMock(return_value=fake_client))
    result = SyncResult(
        sync_run_id=1, broker="ibkr", broker_environment="paper",
        account_id="DU1", status="completed", transport="flex",
        endpoint="x", query_id=1,
    )
    sync_spy = MagicMock(return_value=result)
    monkeypatch.setattr(sync_mod, "run_readonly_sync", sync_spy)

    _patch_today_ny(monkeypatch, date(2026, 5, 22))  # Friday
    run_flex_sync()
    sync_spy.assert_called_once()


# === Retry job ===


def test_retry_job_registered():
    """build_scheduler() must add flex_sync_retry 30min after main."""
    scheduler = build_scheduler()
    job = scheduler.get_job("flex_sync_retry")
    assert job is not None
    trigger_repr = str(job.trigger)
    # Default 23:30 main → retry at 00:00 next day
    assert "hour='0'" in trigger_repr or "hour=0" in trigger_repr, trigger_repr
    assert "minute='0'" in trigger_repr or "minute=0" in trigger_repr, trigger_repr


def _retry_with_last_run(
    monkeypatch, *, status: str | None, error_type: str | None,
    main_should_fire: bool,
):
    """Helper: stub the BrokerSyncRun query result and assert whether
    run_flex_sync is invoked."""
    from datetime import date
    _set_settings(monkeypatch, token="tok", query_id="1")
    import marketpulse.scheduler.jobs as jobs_mod

    fake_db = MagicMock()
    chain = fake_db.query.return_value.filter.return_value.order_by.return_value
    if status is None:
        chain.first.return_value = None
    else:
        chain.first.return_value = MagicMock(
            id=42, status=status, error_type=error_type,
        )
    monkeypatch.setattr(jobs_mod, "session_scope", lambda: iter([fake_db]))
    _patch_today_ny(monkeypatch, date(2026, 5, 22))  # Friday trading day

    main_spy = MagicMock()
    monkeypatch.setattr(jobs_mod, "run_flex_sync", main_spy)
    from marketpulse.scheduler.jobs import run_flex_sync_retry
    run_flex_sync_retry()
    assert main_spy.called == main_should_fire


def test_retry_fires_on_flex_send_request_error_1001(monkeypatch):
    """The 2026-05-26 03:30 UTC observed failure mode → retry fires."""
    _retry_with_last_run(
        monkeypatch, status="failed", error_type="FlexSendRequestError",
        main_should_fire=True,
    )


def test_retry_fires_on_connect_timeout(monkeypatch):
    """The 2026-05-24 ConnectTimeout failure mode → retry fires."""
    _retry_with_last_run(
        monkeypatch, status="failed", error_type="FlexHttpError",
        main_should_fire=True,
    )


def test_retry_skipped_when_last_run_succeeded(monkeypatch):
    """Don't double-sync after a successful run."""
    _retry_with_last_run(
        monkeypatch, status="completed", error_type=None,
        main_should_fire=False,
    )


def test_retry_skipped_on_auth_error(monkeypatch):
    """Token expired / bad query — retry would just fail the same way."""
    _retry_with_last_run(
        monkeypatch, status="failed", error_type="FlexAuthError",
        main_should_fire=False,
    )


def test_retry_skipped_on_parse_error(monkeypatch):
    """Schema/parse errors are persistent — don't retry."""
    _retry_with_last_run(
        monkeypatch, status="failed", error_type="FlexParseError",
        main_should_fire=False,
    )


def test_retry_skipped_when_no_run_today(monkeypatch):
    """Empty broker_sync_run for today (e.g. main was skipped) → no retry."""
    _retry_with_last_run(
        monkeypatch, status=None, error_type=None,
        main_should_fire=False,
    )


def test_retry_skipped_on_holiday(monkeypatch):
    """Memorial Day — main was skipped, retry should also no-op without
    touching the DB or run_flex_sync."""
    from datetime import date
    _set_settings(monkeypatch, token="tok", query_id="1")
    import marketpulse.scheduler.jobs as jobs_mod
    monkeypatch.setattr(
        jobs_mod, "session_scope",
        lambda: (_ for _ in ()).throw(AssertionError("must not open DB on holiday")),
    )
    main_spy = MagicMock()
    monkeypatch.setattr(jobs_mod, "run_flex_sync", main_spy)
    _patch_today_ny(monkeypatch, date(2026, 5, 25))  # Memorial Day
    from marketpulse.scheduler.jobs import run_flex_sync_retry
    run_flex_sync_retry()
    assert not main_spy.called
