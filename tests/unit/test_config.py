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


def test_ibkr_settings_defaults_to_paper_readonly(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "x" * 16)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    from marketpulse.config import Settings

    s = Settings(_env_file=None)

    assert s.ibkr_account_id == ""
    assert s.ibkr_allow_live is False


def test_ibkr_settings_accept_env_overrides(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "x" * 16)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU1234567")
    monkeypatch.setenv("MP_IBKR_ALLOW_LIVE", "true")
    from marketpulse.config import Settings

    s = Settings(_env_file=None)

    assert s.ibkr_account_id == "DU1234567"
    assert s.ibkr_allow_live is True


def test_flex_settings_have_sane_defaults(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "x" * 16)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.delenv("IBKR_FLEX_TOKEN", raising=False)
    monkeypatch.delenv("IBKR_FLEX_QUERY_ID", raising=False)
    monkeypatch.delenv("IBKR_FLEX_BASE_URL", raising=False)
    monkeypatch.delenv("IBKR_FLEX_POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("IBKR_FLEX_MAX_WAIT_SECONDS", raising=False)
    from marketpulse.config import Settings

    s = Settings(_env_file=None)

    assert s.ibkr_flex_token == ""
    assert s.ibkr_flex_query_id == 0
    assert s.ibkr_flex_poll_interval_seconds == 5
    assert s.ibkr_flex_max_wait_seconds == 60
    assert "interactivebrokers.com" in s.ibkr_flex_base_url
