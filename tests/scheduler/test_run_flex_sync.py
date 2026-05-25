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
