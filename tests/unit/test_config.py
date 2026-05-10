import os

import pytest

from marketpulse.config import Settings


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "y" * 32)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "z")
    s = Settings()
    assert s.database_url == "sqlite:///./marketpulse.db"
    assert s.watchlist_recap_time == "16:30"
    assert s.ai_model == "claude-sonnet-4-6"
    assert s.ai_cache_ttl_hours == 24
    assert s.news_cache_ttl_days == 7
    assert s.log_level == "INFO"


def test_settings_requires_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("APP_PASSWORD_HASH", "SESSION_SECRET", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(Exception):
        Settings()
