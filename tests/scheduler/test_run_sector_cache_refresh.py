# Layer: test
"""run_sector_cache_refresh composition root: runs warmup, never raises, closes db."""
from __future__ import annotations

import marketpulse.scheduler.jobs as jobs_mod


def test_run_sector_cache_refresh_runs_and_closes(monkeypatch):
    closed = {"v": False}

    class _Gen:
        def __init__(self):
            self.db = object()

        def __iter__(self):
            return self

        def __next__(self):
            # first next() -> yield db; second next() -> raise StopIteration (teardown)
            if not closed["v"]:
                closed["v"] = True
                return self.db
            raise StopIteration

    monkeypatch.setattr(jobs_mod, "session_scope", lambda: _Gen())
    ran = {}
    monkeypatch.setattr(
        jobs_mod, "refresh_sector_cache",
        lambda db, **kw: ran.setdefault("db", db),
    )
    jobs_mod.run_sector_cache_refresh()  # must not raise
    assert "db" in ran
    assert closed["v"] is True


def test_run_sector_cache_refresh_swallows_errors(monkeypatch):
    monkeypatch.setattr(jobs_mod, "session_scope", lambda: iter([object()]))

    def _boom(db, **kw):
        raise RuntimeError("yf down")

    monkeypatch.setattr(jobs_mod, "refresh_sector_cache", _boom)
    jobs_mod.run_sector_cache_refresh()  # must NOT raise


def test_sector_cache_refresh_job_registered():
    """build_scheduler() must add the daily sector_cache_refresh cron at 20:45 UTC."""
    scheduler = jobs_mod.build_scheduler()
    job = scheduler.get_job("sector_cache_refresh")
    assert job is not None
    trigger_repr = str(job.trigger)
    assert "hour='20'" in trigger_repr or "hour=20" in trigger_repr, trigger_repr
    assert "minute='45'" in trigger_repr or "minute=45" in trigger_repr, trigger_repr
