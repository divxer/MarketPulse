"""Tests for the outcome-computation scheduler job."""
from unittest.mock import MagicMock

from marketpulse.evaluation.outcomes import ComputeOutcomeReport
from marketpulse.scheduler.jobs import build_scheduler, run_outcome_computation


def test_outcome_job_registered():
    scheduler = build_scheduler()
    job = scheduler.get_job("outcome_computation")
    assert job is not None
    # Verify it's a daily 02:00 UTC trigger. APScheduler's CronTrigger
    # representation varies across versions; just check the trigger fields
    # at minimum have hour=2.
    trigger_repr = str(job.trigger)
    assert "hour='2'" in trigger_repr or "hour=2" in trigger_repr, (
        f"expected hour=2 in trigger, got: {trigger_repr}"
    )


def test_run_outcome_computation_calls_compute(monkeypatch):
    """Verify the job invokes compute_outcomes_for_pending_events."""
    called = {}

    def fake_compute(db, data, horizons=None, max_events=500):
        called["yes"] = True
        return ComputeOutcomeReport()

    fake_db = MagicMock()

    # Patch session_scope to yield our fake db
    import marketpulse.scheduler.jobs as jobs_mod

    def fake_session_scope():
        yield fake_db

    monkeypatch.setattr(jobs_mod, "session_scope", fake_session_scope)

    # Patch _build_quote_client to avoid network
    monkeypatch.setattr(jobs_mod, "_build_quote_client", lambda: MagicMock())

    # Patch DataService so construction doesn't hit the DB
    monkeypatch.setattr(jobs_mod, "DataService", lambda *a, **kw: MagicMock())

    # Patch record_run_summary so it doesn't touch the DB
    monkeypatch.setattr(jobs_mod, "record_run_summary", lambda *a, **kw: None)

    # Patch the symbol inside the evaluation package
    monkeypatch.setattr(
        "marketpulse.evaluation.compute_outcomes_for_pending_events",
        fake_compute,
    )

    run_outcome_computation()
    assert called.get("yes"), "compute_outcomes_for_pending_events was not called"
