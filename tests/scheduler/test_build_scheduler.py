"""Misfire-handling guards for daily critical jobs.

These jobs lose real data if a missed run is silently dropped: a deploy that
restarts the container past the cron time should still trigger exactly one
catch-up run on next boot (coalesce=True merges multiple missed instances).

See fix/scheduler-misfire-daily-jobs (observed 2026-05-25 — paper_trading_tick
lost 5/22 / 5/23 / 5/24 to weekend deploys past the 1h grace window).
"""
from marketpulse.scheduler.jobs import build_scheduler


def test_daily_critical_jobs_have_no_misfire_grace():
    sched = build_scheduler()
    for job_id in ("paper_trading_tick", "outcome_computation", "flex_sync"):
        job = sched.get_job(job_id)
        assert job is not None, f"missing job {job_id}"
        assert job.misfire_grace_time is None, (
            f"{job_id} must have misfire_grace_time=None so missed daily "
            f"runs are caught up on next boot, not silently dropped"
        )
        assert job.coalesce is True, f"{job_id} must coalesce missed runs"
