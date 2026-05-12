"""Unit tests for marketpulse.scheduler.state."""
from datetime import UTC, datetime

from marketpulse.scheduler.state import get_last_run_summary, record_run_summary


def test_get_returns_none_when_no_run(db_session) -> None:
    assert get_last_run_summary(db_session) is None


def test_record_and_get_roundtrip(db_session) -> None:
    summary = {
        "ran_at": datetime(2026, 5, 11, 21, 0, tzinfo=UTC).isoformat(),
        "tickers": [
            {"ticker": "TQQQ", "source": "tencent",
             "splits_added": 0, "dividends_added": 14, "error": None},
            {"ticker": "AAPL", "source": "tencent",
             "splits_added": 0, "dividends_added": 0, "error": None},
        ],
        "total_splits": 0,
        "total_dividends": 14,
        "total_failures": 0,
    }
    record_run_summary(db_session, summary)

    out = get_last_run_summary(db_session)
    assert out == summary


def test_record_overwrites_previous(db_session) -> None:
    record_run_summary(db_session, {"ran_at": "2026-05-11T20:00:00+00:00",
                                    "tickers": [], "total_splits": 0,
                                    "total_dividends": 0, "total_failures": 0})
    record_run_summary(db_session, {"ran_at": "2026-05-11T21:00:00+00:00",
                                    "tickers": [], "total_splits": 1,
                                    "total_dividends": 5, "total_failures": 0})

    out = get_last_run_summary(db_session)
    assert out is not None
    assert out["ran_at"] == "2026-05-11T21:00:00+00:00"
    assert out["total_splits"] == 1
    assert out["total_dividends"] == 5
