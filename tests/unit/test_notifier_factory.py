# Layer: pure
"""6g-T1: get_notifier_from_settings wrapper (lock 6g-L13)."""

from __future__ import annotations


def _settings(monkeypatch, **overrides):
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)

    from marketpulse.config import Settings

    return Settings()


def test_get_notifier_from_settings_returns_noop_when_kind_none(monkeypatch):
    from marketpulse.alerts.notifier import NoopNotifier, get_notifier_from_settings

    settings = _settings(monkeypatch, NOTIFIER_KIND="none")
    notifier = get_notifier_from_settings(settings)

    assert isinstance(notifier, NoopNotifier)


def test_get_notifier_from_settings_returns_bark(monkeypatch):
    from marketpulse.alerts.notifier import BarkNotifier, get_notifier_from_settings

    settings = _settings(
        monkeypatch,
        NOTIFIER_KIND="bark",
        NOTIFIER_BARK_URL="https://api.day.app/devicekey",
    )
    notifier = get_notifier_from_settings(settings)

    assert isinstance(notifier, BarkNotifier)


def test_get_notifier_from_settings_is_thin_wrapper_around_build_notifier(
    monkeypatch,
):
    """Lock 6g-L13: documented 6g name behaves like build_notifier."""
    from marketpulse.alerts.notifier import build_notifier, get_notifier_from_settings

    settings = _settings(monkeypatch, NOTIFIER_KIND="none")
    wrapped = get_notifier_from_settings(settings)
    direct = build_notifier(settings)

    assert type(wrapped) is type(direct)
