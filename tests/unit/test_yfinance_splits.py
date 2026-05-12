from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd


def test_fetch_splits_returns_list_of_date_ratio_tuples() -> None:
    from marketpulse.data.yfinance_client import YFinanceClient

    fake_series = pd.Series(
        data=[2.0, 3.0],
        index=pd.to_datetime(["2022-01-13 00:00:00-05:00", "2025-11-20 00:00:00-05:00"]),
    )

    fake_ticker = MagicMock()
    fake_ticker.splits = fake_series

    with patch("marketpulse.data.yfinance_client.yf.Ticker", return_value=fake_ticker):
        out = YFinanceClient().fetch_splits("TQQQ")

    assert out == [(date(2022, 1, 13), 2.0), (date(2025, 11, 20), 3.0)]


def test_fetch_splits_empty_returns_empty_list() -> None:
    from marketpulse.data.yfinance_client import YFinanceClient

    fake_ticker = MagicMock()
    fake_ticker.splits = pd.Series(dtype=float)

    with patch("marketpulse.data.yfinance_client.yf.Ticker", return_value=fake_ticker):
        assert YFinanceClient().fetch_splits("NOSPLIT") == []
