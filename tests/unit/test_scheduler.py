from datetime import time

from marketpulse.scheduler.jobs import parse_recap_time


def test_parse_recap_time_valid() -> None:
    assert parse_recap_time("16:30") == time(16, 30)


def test_parse_recap_time_invalid_falls_back() -> None:
    assert parse_recap_time("nope") == time(16, 30)
