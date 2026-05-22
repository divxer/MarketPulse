# Layer: stateful
"""6b+T4: YFinanceClient.fetch_close_on_date — mocked tests, NO network.

Lock 6b+L5: end=on_date + timedelta(days=1) because yfinance end is exclusive."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd

from marketpulse.data.yfinance_client import YFinanceClient


def _make_history_df(rows: list[tuple[date, float]]) -> pd.DataFrame:
    """Build a pandas DataFrame mimicking yf.Ticker.history() output."""
    if not rows:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    df = pd.DataFrame(
        [{"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1000}
         for _, close in rows],
        index=pd.DatetimeIndex([datetime.combine(d, datetime.min.time(), tzinfo=UTC)
                                for d, _ in rows]),
    )
    return df


def test_fetch_close_on_date_calls_yfinance_with_correct_window():
    """Lock 6b+L5: start=on_date - lookback_days, end=on_date + 1 day."""
    on_date = date(2026, 5, 22)
    lookback = 10

    mock_ticker = MagicMock()
    mock_ticker.history.return_value = _make_history_df([
        (date(2026, 5, 22), 150.50),
    ])

    with patch("marketpulse.data.yfinance_client.yf.Ticker",
               return_value=mock_ticker) as ticker_cls:
        client = YFinanceClient()
        client.fetch_close_on_date("AAPL", on_date, lookback_days=lookback)

    ticker_cls.assert_called_once_with("AAPL")
    mock_ticker.history.assert_called_once()
    call_kwargs = mock_ticker.history.call_args.kwargs
    assert call_kwargs["start"] == on_date - timedelta(days=lookback)
    assert call_kwargs["end"] == on_date + timedelta(days=1)
    assert call_kwargs["interval"] == "1d"


def test_fetch_close_on_date_returns_bar_with_exact_date():
    """Happy path: yfinance returns a bar dated exactly on_date."""
    on_date = date(2026, 5, 22)
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = _make_history_df([
        (date(2026, 5, 21), 149.00),
        (date(2026, 5, 22), 150.50),
    ])

    with patch("marketpulse.data.yfinance_client.yf.Ticker",
               return_value=mock_ticker):
        client = YFinanceClient()
        bar = client.fetch_close_on_date("AAPL", on_date)

    assert bar is not None
    assert bar.date == date(2026, 5, 22)
    assert bar.close == 150.50


def test_fetch_close_on_date_rollback_to_prior_session():
    """Roll-back: on_date=Saturday → return Friday's bar."""
    saturday = date(2026, 5, 23)
    friday = date(2026, 5, 22)
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = _make_history_df([
        (date(2026, 5, 21), 149.00),
        (friday, 150.50),
        # No Saturday/Sunday data
    ])

    with patch("marketpulse.data.yfinance_client.yf.Ticker",
               return_value=mock_ticker):
        client = YFinanceClient()
        bar = client.fetch_close_on_date("AAPL", saturday)

    assert bar is not None
    assert bar.date == friday    # rolled back
    assert bar.close == 150.50


def test_fetch_close_on_date_empty_window_returns_none():
    """Lock 6b+L5: no bar in [on_date - lookback, on_date] → None."""
    on_date = date(2026, 5, 22)
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = _make_history_df([])    # empty DataFrame

    with patch("marketpulse.data.yfinance_client.yf.Ticker",
               return_value=mock_ticker):
        client = YFinanceClient()
        bar = client.fetch_close_on_date("AAPL", on_date)

    assert bar is None


def test_fetch_close_on_date_bars_only_after_on_date_returns_none():
    """If yfinance returns bars but all are AFTER on_date (shouldn't happen
    given end=on_date+1, but defensive), return None."""
    on_date = date(2026, 5, 22)
    # NOTE: this is a pathological case; end=on_date+1 should make yfinance
    # never return >on_date bars. Defensive guard verifies the filter still works.
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = _make_history_df([
        (date(2026, 5, 25), 151.00),    # > on_date
    ])

    with patch("marketpulse.data.yfinance_client.yf.Ticker",
               return_value=mock_ticker):
        client = YFinanceClient()
        bar = client.fetch_close_on_date("AAPL", on_date)

    assert bar is None


def test_fetch_close_on_date_skips_nan_close(monkeypatch):
    """Op-test #28: yfinance can return NaN Close on certain edge days
    (delisted, corporate-action gaps). MUST skip NaN rows and return the
    next-most-recent valid bar, NEVER produce Decimal('nan') or raise."""
    import math
    on_date = date(2026, 5, 22)
    # Build a DataFrame where the latest bar has NaN Close
    df = pd.DataFrame(
        [
            {"Open": 149.0, "High": 150.0, "Low": 148.0, "Close": 149.50, "Volume": 1000},
            {"Open": math.nan, "High": math.nan, "Low": math.nan, "Close": math.nan, "Volume": 0},
        ],
        index=pd.DatetimeIndex([
            datetime(2026, 5, 21, tzinfo=UTC),
            datetime(2026, 5, 22, tzinfo=UTC),
        ]),
    )
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = df

    with patch("marketpulse.data.yfinance_client.yf.Ticker",
               return_value=mock_ticker):
        client = YFinanceClient()
        bar = client.fetch_close_on_date("AAPL", on_date)

    # The May 22 row has NaN Close → skipped. May 21 row is the valid candidate.
    assert bar is not None
    assert bar.date == date(2026, 5, 21)
    assert bar.close == 149.50
    assert not math.isnan(bar.close)


def test_fetch_close_on_date_all_nan_returns_none():
    """If ALL bars in the window have NaN Close, return None."""
    import math
    on_date = date(2026, 5, 22)
    df = pd.DataFrame(
        [
            {"Open": math.nan, "High": math.nan, "Low": math.nan, "Close": math.nan, "Volume": 0},
        ],
        index=pd.DatetimeIndex([datetime(2026, 5, 22, tzinfo=UTC)]),
    )
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = df

    with patch("marketpulse.data.yfinance_client.yf.Ticker",
               return_value=mock_ticker):
        client = YFinanceClient()
        bar = client.fetch_close_on_date("AAPL", on_date)

    assert bar is None
