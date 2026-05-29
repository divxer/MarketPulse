# Layer: test
"""PR3b — scheduler-level isolation tests for the weekly charter review."""
from __future__ import annotations

from datetime import date


def test_last_sunday_on_or_before_monday():
    from marketpulse.scheduler.jobs import _last_sunday_on_or_before
    assert _last_sunday_on_or_before(date(2026, 8, 17)) == date(2026, 8, 16)


def test_last_sunday_on_or_before_sunday():
    from marketpulse.scheduler.jobs import _last_sunday_on_or_before
    assert _last_sunday_on_or_before(date(2026, 8, 16)) == date(2026, 8, 16)


def test_last_sunday_on_or_before_friday():
    from marketpulse.scheduler.jobs import _last_sunday_on_or_before
    assert _last_sunday_on_or_before(date(2026, 8, 14)) == date(2026, 8, 9)


def test_run_charter_review_weekly_failure_logged_not_raised(
    db_session, monkeypatch,
):
    from marketpulse.scheduler import jobs as jobs_mod

    def boom(**kwargs):
        raise RuntimeError("simulated review failure")

    monkeypatch.setattr(jobs_mod, "generate_charter_review", boom)

    # jobs.py uses structlog (get_logger), which doesn't route through
    # pytest's caplog handler — monkeypatch the bound method directly.
    warnings_emitted: list[tuple[str, dict]] = []

    def _capture_warning(event, **kw):  # noqa: ANN001
        warnings_emitted.append((event, kw))

    monkeypatch.setattr(jobs_mod.log, "warning", _capture_warning)

    # Should not raise.
    jobs_mod.run_charter_review_weekly()
    # And the warning must be emitted.
    assert any(
        "charter_review_failed" in event
        for event, _ in warnings_emitted
    )


def test_run_charter_review_weekly_skipped_for_non_sqlite(monkeypatch):
    from marketpulse.config import get_settings
    from marketpulse.scheduler import jobs as jobs_mod

    real_settings = get_settings()

    class _StubSettings:
        database_url = "postgresql://user:pw@localhost:5432/mp"
        def __getattr__(self, name):
            return getattr(real_settings, name)

    monkeypatch.setattr(jobs_mod, "get_settings", lambda: _StubSettings())

    # Capture structlog info calls on the module-level log object.
    info_emitted: list[tuple[str, dict]] = []

    def _capture_info(event, **kw):  # noqa: ANN001
        info_emitted.append((event, kw))

    monkeypatch.setattr(jobs_mod.log, "info", _capture_info)

    jobs_mod.run_charter_review_weekly()
    assert any(
        "charter_review_skipped_not_sqlite" in event
        for event, _ in info_emitted
    )


def test_run_charter_review_weekly_accepts_sqlite_pysqlite(
    db_session, monkeypatch, tmp_path,
):
    """L13 / PR2 lesson: `sqlite+pysqlite:///...` MUST be treated as sqlite,
    not skipped. We don't run the full generator — just verify the driver
    check doesn't short-circuit by asserting generate_charter_review IS called."""
    from marketpulse.config import get_settings
    from marketpulse.scheduler import jobs as jobs_mod

    real_settings = get_settings()
    db_file = tmp_path / "smoke.db"

    class _StubSettings:
        database_url = f"sqlite+pysqlite:///{db_file}"
        def __getattr__(self, name):
            return getattr(real_settings, name)

    called = {"count": 0}

    def fake_generate(**kwargs):
        called["count"] += 1
        return tmp_path / "ok.md"

    monkeypatch.setattr(jobs_mod, "get_settings", lambda: _StubSettings())
    monkeypatch.setattr(jobs_mod, "generate_charter_review", fake_generate)
    jobs_mod.run_charter_review_weekly()
    assert called["count"] == 1
