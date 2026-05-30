# Layer: test
"""Task #57 — run_eval_analysis_job composition root."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import marketpulse.scheduler.jobs as jobs_mod
from marketpulse.scheduler.jobs import run_eval_analysis_job


@pytest.fixture()
def wired(db_session, monkeypatch):
    """Wire the job to a real db_session; stub network-touching constructors."""
    def fake_session_scope():
        yield db_session
    monkeypatch.setattr(jobs_mod, "session_scope", fake_session_scope)
    monkeypatch.setattr(jobs_mod, "_build_quote_client", lambda: MagicMock())
    monkeypatch.setattr(jobs_mod, "DataService", lambda *a, **kw: MagicMock())
    # AnthropicClient() is evaluated even when AiService is mocked (it's the
    # ai_client= arg) — stub it so its real constructor can't touch the network
    # or require an API key.
    monkeypatch.setattr(jobs_mod, "AnthropicClient", lambda *a, **kw: MagicMock())
    return db_session


def _settings(monkeypatch, **over):
    from marketpulse.config import Settings
    base = dict(ai_eval_enabled=True, ai_eval_max_calls_per_day=60)
    base.update(over)
    monkeypatch.setattr(jobs_mod, "get_settings", lambda: Settings(**base))


def test_disabled_records_disabled_summary_no_analyze(wired, monkeypatch):
    _settings(monkeypatch, ai_eval_enabled=False)
    ai_factory = MagicMock(return_value=MagicMock())   # the AiService class itself
    monkeypatch.setattr(jobs_mod, "AiService", ai_factory)

    run_eval_analysis_job()

    from marketpulse.scheduler.eval_state import get_eval_last_run_summary
    got = get_eval_last_run_summary(wired)
    assert got["status"] == "disabled"
    ai_factory.assert_not_called()                     # AiService never even constructed
    ai_factory.return_value.analyze.assert_not_called()


def test_happy_path_records_ok(wired, monkeypatch):
    _settings(monkeypatch, ai_eval_enabled=True)
    monkeypatch.setattr(jobs_mod, "AiService", lambda *a, **kw: MagicMock())
    monkeypatch.setattr(jobs_mod, "build_eval_universe", lambda s: [])

    run_eval_analysis_job()

    from marketpulse.scheduler.eval_state import get_eval_last_run_summary
    got = get_eval_last_run_summary(wired)
    assert got["status"] == "ok"
    assert got["universe_size"] == 0


def test_job_boundary_failure_records_failed_no_raise(wired, monkeypatch):
    _settings(monkeypatch, ai_eval_enabled=True)
    monkeypatch.setattr(jobs_mod, "AiService", lambda *a, **kw: MagicMock())

    def boom(_session):
        raise RuntimeError("universe build failed")
    monkeypatch.setattr(jobs_mod, "build_eval_universe", boom)

    run_eval_analysis_job()                         # must NOT raise

    from marketpulse.scheduler.eval_state import get_eval_last_run_summary
    got = get_eval_last_run_summary(wired)
    assert got["status"] == "failed"
    assert "error" in got


def test_session_open_failure_logs_only_no_summary(monkeypatch):
    _settings(monkeypatch, ai_eval_enabled=True)

    def fake_session_scope():
        raise RuntimeError("db down")
        yield  # pragma: no cover
    monkeypatch.setattr(jobs_mod, "session_scope", fake_session_scope)

    # Must not raise; physically cannot persist a summary (no session).
    run_eval_analysis_job()
