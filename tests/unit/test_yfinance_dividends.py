from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd


def test_fetch_dividends_returns_list_of_date_amount_tuples() -> None:
    from marketpulse.data.yfinance_client import YFinanceClient

    fake_series = pd.Series(
        data=[0.22, 0.28, 0.10],
        index=pd.to_datetime([
            "2024-03-20 00:00:00-05:00",
            "2024-06-26 00:00:00-05:00",
            "2025-09-24 00:00:00-04:00",
        ], utc=True),
    )

    fake_ticker = MagicMock()
    fake_ticker.dividends = fake_series

    with patch("marketpulse.data.yfinance_client.yf.Ticker", return_value=fake_ticker):
        out = YFinanceClient().fetch_dividends("TQQQ")

    assert out == [
        (date(2024, 3, 20), 0.22),
        (date(2024, 6, 26), 0.28),
        (date(2025, 9, 24), 0.10),
    ]


def test_fetch_dividends_empty_returns_empty_list() -> None:
    from marketpulse.data.yfinance_client import YFinanceClient

    fake_ticker = MagicMock()
    fake_ticker.dividends = pd.Series(dtype=float)

    with patch("marketpulse.data.yfinance_client.yf.Ticker", return_value=fake_ticker):
        assert YFinanceClient().fetch_dividends("NODIV") == []
