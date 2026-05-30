# Layer: test
"""Task #57 — AI eval-analysis Settings fields."""
from __future__ import annotations

from marketpulse.config import Settings


def test_eval_defaults_are_safe():
    s = Settings()
    assert s.ai_eval_enabled is False          # disabled by default — explicit opt-in
    assert s.ai_eval_max_calls_per_day == 60
    assert s.ai_eval_hour == 21                 # UTC
    assert s.ai_eval_minute == 0


def test_eval_fields_read_env(monkeypatch):
    monkeypatch.setenv("AI_EVAL_ENABLED", "true")
    monkeypatch.setenv("AI_EVAL_MAX_CALLS_PER_DAY", "25")
    monkeypatch.setenv("AI_EVAL_HOUR", "22")
    monkeypatch.setenv("AI_EVAL_MINUTE", "15")
    s = Settings()
    assert s.ai_eval_enabled is True
    assert s.ai_eval_max_calls_per_day == 25
    assert s.ai_eval_hour == 22
    assert s.ai_eval_minute == 15
