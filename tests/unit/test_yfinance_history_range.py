from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd


def _hist_df(rows: list[tuple[str, float, float, float, float, int]]) -> pd.DataFrame:
    """Build a yfinance-shaped DataFrame from (date_str, o, h, l, c, vol) tuples."""
    df = pd.DataFrame(
        rows, columns=["date", "Open", "High", "Low", "Close", "Volume"],
    )
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def test_fetch_history_range_returns_bars_in_window() -> None:
    from marketpulse.data.yfinance_client import YFinanceClient

    fake_ticker = MagicMock()
    fake_ticker.history.return_value = _hist_df([
        ("2024-01-02", 100.0, 102.0, 99.5, 101.5, 1_000_000),
        ("2024-01-03", 101.5, 103.0, 101.0, 102.5, 1_100_000),
        ("2024-01-04", 102.5, 102.8, 100.5, 101.0, 900_000),
    ])

    with patch("marketpulse.data.yfinance_client.yf.Ticker", return_value=fake_ticker):
        bars = YFinanceClient().fetch_history_range(
            "AAPL", start=date(2024, 1, 2), end=date(2024, 1, 4),
        )

    # yf.Ticker.history called with the right start/end + daily interval
    fake_ticker.history.assert_called_once()
    kwargs = fake_ticker.history.call_args.kwargs
    assert kwargs["start"] == date(2024, 1, 2)
    assert kwargs["end"] == date(2024, 1, 4)
    assert kwargs["interval"] == "1d"

    assert len(bars) == 3
    assert bars[0].date == date(2024, 1, 2)
    assert bars[0].close == 101.5
    assert bars[2].date == date(2024, 1, 4)


def test_fetch_history_range_empty_returns_empty_list() -> None:
    from marketpulse.data.yfinance_client import YFinanceClient

    fake_ticker = MagicMock()
    fake_ticker.history.return_value = pd.DataFrame(
        columns=["Open", "High", "Low", "Close", "Volume"],
    )

    with patch("marketpulse.data.yfinance_client.yf.Ticker", return_value=fake_ticker):
        bars = YFinanceClient().fetch_history_range(
            "NOSUCH", start=date(2020, 1, 1), end=date(2020, 1, 31),
        )

    assert bars == []
