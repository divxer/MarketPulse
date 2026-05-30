# Layer: test
"""Task #57 — eval-analysis last-run summary persistence."""
from __future__ import annotations

from datetime import date

from marketpulse.scheduler.eval_state import (
    get_eval_last_run_summary,
    record_eval_run_summary,
)


def _payload(status="ok"):
    return {
        "status": status, "run_date": date(2026, 5, 29), "universe_size": 3,
        "analyzed_fresh": 3, "cache_hits": 0, "skipped_cap": 0, "errors": 0,
        "cap_hit": False, "processed": 3,
    }


def test_get_returns_none_when_never_run(db_session):
    assert get_eval_last_run_summary(db_session) is None


def test_record_then_get_roundtrip_adds_ts(db_session):
    record_eval_run_summary(db_session, _payload())
    got = get_eval_last_run_summary(db_session)
    assert got["status"] == "ok"
    assert got["run_date"] == "2026-05-29"        # date coerced via str() in JSON
    assert got["processed"] == 3
    assert "ts" in got                             # added by the recorder


def test_record_overwrites_previous(db_session):
    record_eval_run_summary(db_session, _payload(status="ok"))
    record_eval_run_summary(db_session, _payload(status="disabled"))
    got = get_eval_last_run_summary(db_session)
    assert got["status"] == "disabled"             # single row, overwritten
