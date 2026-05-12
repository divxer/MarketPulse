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


def test_detect_corporate_actions_records_tencent_splits_and_dividends(monkeypatch) -> None:
    """Tencent ok → both splits and dividends recorded; recompute_ticker
    called once per ticker that got a new split."""
    from datetime import date
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "y" * 32)
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    fake_session = MagicMock()
    # Holdings first, then watchlist
    fake_session.query.return_value.all.side_effect = [
        [MagicMock(ticker="TQQQ")],  # holdings
        [MagicMock(ticker="NVDA")],  # watchlist
    ]

    def fake_session_scope():
        yield fake_session

    from marketpulse.data.tencent_client import CorporateActions
    fake_tencent = MagicMock()
    fake_tencent.fetch_corporate_actions.side_effect = [
        CorporateActions(
            dividends=[(date(2025, 9, 24), 0.10)],
            splits=[(date(2025, 11, 20), 2.0)],
        ),
        CorporateActions(splits=[(date(2024, 6, 10), 10.0)], dividends=[]),
    ]

    with patch("marketpulse.scheduler.jobs.session_scope", fake_session_scope), \
         patch("marketpulse.scheduler.jobs.TencentClient", return_value=fake_tencent), \
         patch("marketpulse.scheduler.jobs.YFinanceClient") as YfMock, \
         patch("marketpulse.scheduler.jobs.record_split") as rs, \
         patch("marketpulse.scheduler.jobs.record_dividend") as rd, \
         patch("marketpulse.scheduler.jobs.quantity_as_of",
               return_value=20.0) as qa, \
         patch("marketpulse.scheduler.jobs.recompute_ticker") as rc:
        from marketpulse.scheduler.jobs import run_detect_corporate_actions
        run_detect_corporate_actions()

    # 2 splits recorded (one per ticker)
    assert rs.call_count == 2
    # source kwarg is "tencent" on both calls
    for call in rs.call_args_list:
        assert call.kwargs["source"] == "tencent"

    # 1 dividend recorded (TQQQ only; NVDA had none)
    assert rd.call_count == 1
    dkw = rd.call_args.kwargs
    assert dkw["ticker"] == "TQQQ"
    assert dkw["ex_date"] == date(2025, 9, 24)
    assert dkw["amount_per_share"] == 0.10
    assert dkw["total_amount"] == 2.0  # 20 * 0.10
    assert dkw["source"] == "tencent"

    # quantity_as_of must be invoked with ex_date - 1 day (T-1 holder-of-record),
    # not the ex_date itself.
    assert qa.call_args.args[1] == "TQQQ"
    assert qa.call_args.args[2] == date(2025, 9, 23)

    # recompute_ticker called once per ticker with new splits
    assert rc.call_count == 2

    # yfinance fallback NOT invoked
    YfMock.return_value.fetch_splits.assert_not_called()
    YfMock.return_value.fetch_dividends.assert_not_called()


def test_detect_corporate_actions_tencent_fails_yfinance_fallback(monkeypatch) -> None:
    """Tencent raises → yfinance fetch_splits + fetch_dividends are tried."""
    from datetime import date
    from unittest.mock import MagicMock, patch

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

    fake_tencent = MagicMock()
    fake_tencent.fetch_corporate_actions.side_effect = RuntimeError("Tencent down")
    fake_yf = MagicMock()
    fake_yf.fetch_splits.return_value = [(date(2025, 11, 20), 2.0)]
    fake_yf.fetch_dividends.return_value = [(date(2025, 9, 24), 0.10)]

    with patch("marketpulse.scheduler.jobs.session_scope", fake_session_scope), \
         patch("marketpulse.scheduler.jobs.TencentClient", return_value=fake_tencent), \
         patch("marketpulse.scheduler.jobs.YFinanceClient", return_value=fake_yf), \
         patch("marketpulse.scheduler.jobs.record_split") as rs, \
         patch("marketpulse.scheduler.jobs.record_dividend") as rd, \
         patch("marketpulse.scheduler.jobs.quantity_as_of", return_value=20.0), \
         patch("marketpulse.scheduler.jobs.recompute_ticker"):
        from marketpulse.scheduler.jobs import run_detect_corporate_actions
        run_detect_corporate_actions()

    # Tencent was attempted
    fake_tencent.fetch_corporate_actions.assert_called_once()
    # Fallback engaged
    fake_yf.fetch_splits.assert_called_once_with("TQQQ")
    fake_yf.fetch_dividends.assert_called_once_with("TQQQ")
    # Records carry source="yfinance"
    assert rs.call_args.kwargs["source"] == "yfinance"
    assert rd.call_args.kwargs["source"] == "yfinance"


def test_detect_corporate_actions_both_sources_fail(monkeypatch) -> None:
    """Both Tencent and yfinance raise → job continues, no records."""
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

    fake_tencent = MagicMock()
    fake_tencent.fetch_corporate_actions.side_effect = RuntimeError("Tencent down")
    fake_yf = MagicMock()
    fake_yf.fetch_splits.side_effect = RuntimeError("yahoo timeout")

    with patch("marketpulse.scheduler.jobs.session_scope", fake_session_scope), \
         patch("marketpulse.scheduler.jobs.TencentClient", return_value=fake_tencent), \
         patch("marketpulse.scheduler.jobs.YFinanceClient", return_value=fake_yf), \
         patch("marketpulse.scheduler.jobs.record_split") as rs, \
         patch("marketpulse.scheduler.jobs.record_dividend") as rd, \
         patch("marketpulse.scheduler.jobs.recompute_ticker"):
        from marketpulse.scheduler.jobs import run_detect_corporate_actions
        run_detect_corporate_actions()  # must not raise

    # Both tickers attempted, nothing recorded
    assert fake_tencent.fetch_corporate_actions.call_count == 2
    rs.assert_not_called()
    rd.assert_not_called()


def test_detect_corporate_actions_skips_dividend_when_qty_zero(monkeypatch) -> None:
    """Dividend ex_date with qty=0 (never held / sold before) → no record."""
    from datetime import date
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "y" * 32)
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    fake_session = MagicMock()
    fake_session.query.return_value.all.side_effect = [
        [],
        [MagicMock(ticker="WATCHED")],
    ]

    def fake_session_scope():
        yield fake_session

    from marketpulse.data.tencent_client import CorporateActions
    fake_tencent = MagicMock()
    fake_tencent.fetch_corporate_actions.return_value = CorporateActions(
        dividends=[(date(2025, 9, 24), 0.10)],
        splits=[],
    )

    with patch("marketpulse.scheduler.jobs.session_scope", fake_session_scope), \
         patch("marketpulse.scheduler.jobs.TencentClient", return_value=fake_tencent), \
         patch("marketpulse.scheduler.jobs.YFinanceClient"), \
         patch("marketpulse.scheduler.jobs.record_split"), \
         patch("marketpulse.scheduler.jobs.record_dividend") as rd, \
         patch("marketpulse.scheduler.jobs.quantity_as_of", return_value=0.0), \
         patch("marketpulse.scheduler.jobs.recompute_ticker"):
        from marketpulse.scheduler.jobs import run_detect_corporate_actions
        run_detect_corporate_actions()

    # Dividend was returned by Tencent but qty=0 → skipped.
    rd.assert_not_called()


def test_detect_corporate_actions_idempotent(monkeypatch) -> None:
    """If a split/dividend already exists, SplitError/DividendError swallowed."""
    from datetime import date
    from unittest.mock import MagicMock, patch

    from marketpulse.holdings.dividends import DividendError
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

    from marketpulse.data.tencent_client import CorporateActions
    fake_tencent = MagicMock()
    fake_tencent.fetch_corporate_actions.return_value = CorporateActions(
        dividends=[(date(2025, 9, 24), 0.10)],
        splits=[(date(2025, 11, 20), 2.0)],
    )

    with patch("marketpulse.scheduler.jobs.session_scope", fake_session_scope), \
         patch("marketpulse.scheduler.jobs.TencentClient", return_value=fake_tencent), \
         patch("marketpulse.scheduler.jobs.YFinanceClient"), \
         patch("marketpulse.scheduler.jobs.record_split",
               side_effect=SplitError("already recorded")), \
         patch("marketpulse.scheduler.jobs.record_dividend",
               side_effect=DividendError("already recorded")), \
         patch("marketpulse.scheduler.jobs.quantity_as_of", return_value=20.0), \
         patch("marketpulse.scheduler.jobs.recompute_ticker") as rc:
        from marketpulse.scheduler.jobs import run_detect_corporate_actions
        run_detect_corporate_actions()  # must not raise

    # No new splits → recompute NOT called.
    rc.assert_not_called()
