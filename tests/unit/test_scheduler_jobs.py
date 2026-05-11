from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

import pytest

from marketpulse.alerts.notifier import NoopNotifier
from marketpulse.db.models import DailyRecap


@pytest.fixture
def fake_recap():
    return DailyRecap(
        recap_date=date.today(),
        market_summary_json='{"spy": 0.5, "qqq": 0.3, "dia": 0.1, "vix": 15}',
        ai_commentary_text="今日小幅上涨。",
        generation_status="ok",
        generated_at=datetime.now(UTC),
    )


def test_recap_push_called_when_enabled(monkeypatch, fake_recap) -> None:
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "yyyyyyyyyyyyyyyy")
    monkeypatch.setenv("NOTIFIER_KIND", "bark")
    monkeypatch.setenv("NOTIFIER_BARK_URL", "https://api.day.app/abc")
    monkeypatch.setenv("NOTIFIER_RECAP_ENABLED", "true")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    with patch("marketpulse.scheduler.jobs.RecapService") as RS, \
         patch("marketpulse.scheduler.jobs.push_recap_summary") as push, \
         patch("marketpulse.scheduler.jobs.build_notifier") as bn, \
         patch("marketpulse.scheduler.jobs._build_quote_client"), \
         patch("marketpulse.scheduler.jobs.session_scope"):
        RS.return_value.generate.return_value = fake_recap
        bn.return_value = MagicMock()  # not a NoopNotifier
        from marketpulse.scheduler.jobs import run_daily_recap
        run_daily_recap()
        assert push.called


def test_recap_push_skipped_when_disabled(monkeypatch, fake_recap) -> None:
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "yyyyyyyyyyyyyyyy")
    monkeypatch.setenv("NOTIFIER_KIND", "bark")
    monkeypatch.setenv("NOTIFIER_RECAP_ENABLED", "false")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    with patch("marketpulse.scheduler.jobs.RecapService") as RS, \
         patch("marketpulse.scheduler.jobs.push_recap_summary") as push, \
         patch("marketpulse.scheduler.jobs._build_quote_client"), \
         patch("marketpulse.scheduler.jobs.session_scope"):
        RS.return_value.generate.return_value = fake_recap
        from marketpulse.scheduler.jobs import run_daily_recap
        run_daily_recap()
        assert not push.called


def test_recap_push_skipped_when_notifier_is_noop(monkeypatch, fake_recap) -> None:
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "yyyyyyyyyyyyyyyy")
    monkeypatch.setenv("NOTIFIER_KIND", "none")
    monkeypatch.setenv("NOTIFIER_RECAP_ENABLED", "true")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    with patch("marketpulse.scheduler.jobs.RecapService") as RS, \
         patch("marketpulse.scheduler.jobs.push_recap_summary") as push, \
         patch("marketpulse.scheduler.jobs.build_notifier") as bn, \
         patch("marketpulse.scheduler.jobs._build_quote_client"), \
         patch("marketpulse.scheduler.jobs.session_scope"):
        RS.return_value.generate.return_value = fake_recap
        bn.return_value = NoopNotifier()
        from marketpulse.scheduler.jobs import run_daily_recap
        run_daily_recap()
        assert not push.called


def test_recap_push_failure_does_not_propagate(monkeypatch, fake_recap) -> None:
    """If push raises (even past the internal retry), the recap job still finishes."""
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "yyyyyyyyyyyyyyyy")
    monkeypatch.setenv("NOTIFIER_KIND", "bark")
    monkeypatch.setenv("NOTIFIER_BARK_URL", "https://api.day.app/abc")
    monkeypatch.setenv("NOTIFIER_RECAP_ENABLED", "true")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    with patch("marketpulse.scheduler.jobs.RecapService") as RS, \
         patch("marketpulse.scheduler.jobs.push_recap_summary",
               side_effect=RuntimeError("boom")), \
         patch("marketpulse.scheduler.jobs.build_notifier") as bn, \
         patch("marketpulse.scheduler.jobs._build_quote_client"), \
         patch("marketpulse.scheduler.jobs.session_scope"):
        RS.return_value.generate.return_value = fake_recap
        bn.return_value = MagicMock()
        from marketpulse.scheduler.jobs import run_daily_recap
        run_daily_recap()  # must not raise
