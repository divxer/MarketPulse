# Layer: pure
"""6g-T1: MP_PAPER_NOTIFICATIONS_ENABLED settings flag (lock 6g-L15)."""

from __future__ import annotations


def _fresh_settings():
    """Build a fresh Settings instance without the cached get_settings()."""
    from marketpulse.config import Settings

    return Settings()


def test_paper_notifications_enabled_defaults_to_true(monkeypatch):
    """Lock 6g-L15: default true so a fresh deployment immediately notifies.

    Operator must opt OUT via env, not opt IN.
    """
    monkeypatch.delenv("MP_PAPER_NOTIFICATIONS_ENABLED", raising=False)
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    settings = _fresh_settings()

    assert settings.paper_notifications_enabled is True


def test_paper_notifications_enabled_reads_env_false(monkeypatch):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "false")
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    settings = _fresh_settings()

    assert settings.paper_notifications_enabled is False


def test_paper_notifications_enabled_reads_env_true(monkeypatch):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    settings = _fresh_settings()

    assert settings.paper_notifications_enabled is True


def test_paper_notifications_independent_of_recap_enabled(monkeypatch):
    """Lock 6g-L15: flag is independent of NOTIFIER_RECAP_ENABLED."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "false")
    monkeypatch.setenv("NOTIFIER_RECAP_ENABLED", "true")
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    settings = _fresh_settings()

    assert settings.paper_notifications_enabled is False
    assert settings.notifier_recap_enabled is True


def test_ibkr_allow_live_parses_truthy_strings(monkeypatch):
    """Lock pydantic-settings boolean parsing for MP_IBKR_ALLOW_LIVE.

    The live-port guard depends on Settings reliably parsing common
    string env values into booleans. Lock "1", "true", "false".
    """
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    for value in ("1", "true"):
        monkeypatch.setenv("MP_IBKR_ALLOW_LIVE", value)
        assert _fresh_settings().ibkr_allow_live is True, value

    monkeypatch.setenv("MP_IBKR_ALLOW_LIVE", "false")
    assert _fresh_settings().ibkr_allow_live is False
