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


def test_recap_push_skipped_when_generation_failed(monkeypatch, fake_recap) -> None:
    """No point pushing an empty/error summary — skip when status != 'ok'."""
    fake_recap.generation_status = "failed"
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "y" * 32)
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
        bn.return_value = MagicMock()
        from marketpulse.scheduler.jobs import run_daily_recap
        run_daily_recap()
        assert not push.called


def test_detect_corporate_actions_records_new_splits(monkeypatch) -> None:
    """Job should call fetch_splits per held/watched ticker and persist new rows."""
    from datetime import date
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "y" * 32)
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    fake_session = MagicMock()
    fake_session.query.return_value.all.side_effect = [
        [MagicMock(ticker="TQQQ")],
        [MagicMock(ticker="NVDA")],
    ]

    def fake_session_scope():
        yield fake_session

    fake_yf = MagicMock()
    fake_yf.fetch_splits.side_effect = [
        [(date(2025, 11, 20), 2.0)],  # TQQQ
        [],                            # NVDA
    ]

    with patch("marketpulse.scheduler.jobs.session_scope", fake_session_scope), \
         patch("marketpulse.scheduler.jobs.YFinanceClient", return_value=fake_yf), \
         patch("marketpulse.scheduler.jobs.record_split") as rs, \
         patch("marketpulse.scheduler.jobs.recompute_ticker") as rc:
        from marketpulse.scheduler.jobs import run_detect_corporate_actions
        run_detect_corporate_actions()

    assert rs.call_count == 1
    args, kwargs = rs.call_args
    assert kwargs["ticker"] == "TQQQ"
    assert kwargs["ex_date"] == date(2025, 11, 20)
    assert kwargs["ratio"] == 2.0
    assert kwargs["source"] == "yfinance"
    rc.assert_called_once_with(fake_session, "TQQQ")


def test_detect_corporate_actions_idempotent(monkeypatch) -> None:
    """If a split is already recorded, SplitError is swallowed and we move on."""
    from datetime import date
    from unittest.mock import MagicMock, patch

    from marketpulse.holdings.splits import SplitError

    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "y" * 32)
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    fake_session = MagicMock()
    fake_session.query.return_value.all.side_effect = [
        [MagicMock(ticker="TQQQ")],
        [],
    ]

    def fake_session_scope():
        yield fake_session

    fake_yf = MagicMock()
    fake_yf.fetch_splits.return_value = [(date(2025, 11, 20), 2.0)]

    with patch("marketpulse.scheduler.jobs.session_scope", fake_session_scope), \
         patch("marketpulse.scheduler.jobs.YFinanceClient", return_value=fake_yf), \
         patch("marketpulse.scheduler.jobs.record_split",
               side_effect=SplitError("already recorded")), \
         patch("marketpulse.scheduler.jobs.recompute_ticker") as rc:
        from marketpulse.scheduler.jobs import run_detect_corporate_actions
        run_detect_corporate_actions()

    rc.assert_not_called()


def test_detect_corporate_actions_yfinance_failure_does_not_propagate(monkeypatch) -> None:
    """A yfinance exception on one ticker must not abort the whole job."""
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "y" * 32)
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    fake_session = MagicMock()
    fake_session.query.return_value.all.side_effect = [
        [MagicMock(ticker="TQQQ"), MagicMock(ticker="NVDA")],
        [],
    ]

    def fake_session_scope():
        yield fake_session

    fake_yf = MagicMock()
    fake_yf.fetch_splits.side_effect = [RuntimeError("yahoo timeout"), []]

    with patch("marketpulse.scheduler.jobs.session_scope", fake_session_scope), \
         patch("marketpulse.scheduler.jobs.YFinanceClient", return_value=fake_yf), \
         patch("marketpulse.scheduler.jobs.record_split"), \
         patch("marketpulse.scheduler.jobs.recompute_ticker"):
        from marketpulse.scheduler.jobs import run_detect_corporate_actions
        run_detect_corporate_actions()

    assert fake_yf.fetch_splits.call_count == 2
