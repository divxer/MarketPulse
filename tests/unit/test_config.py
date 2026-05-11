import pytest
from pydantic import ValidationError

from marketpulse.config import Settings


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "y" * 32)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "z")
    s = Settings(_env_file=None)
    assert s.database_url == "sqlite:///./marketpulse.db"
    assert s.watchlist_recap_time == "16:30"
    assert s.ai_model == "claude-sonnet-4-6"
    assert s.ai_cache_ttl_hours == 24
    assert s.news_cache_ttl_days == 7
    assert s.log_level == "INFO"


def test_settings_requires_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("APP_PASSWORD_HASH", "SESSION_SECRET", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_notifier_recap_enabled_default_true(monkeypatch) -> None:
    monkeypatch.delenv("NOTIFIER_RECAP_ENABLED", raising=False)
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    s = get_settings()
    assert s.notifier_recap_enabled is True


def test_notifier_recap_enabled_can_disable(monkeypatch) -> None:
    monkeypatch.setenv("NOTIFIER_RECAP_ENABLED", "false")
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    assert get_settings().notifier_recap_enabled is False


def test_public_base_url_default_empty(monkeypatch) -> None:
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    assert get_settings().public_base_url == ""
