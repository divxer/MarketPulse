import pytest

from marketpulse.data.service import DataService
from marketpulse.data.types import Bar


class _FakeYF:
    def __init__(self) -> None:
        self.last_period: str | None = None
        self.bars_to_return: list[Bar] = []

    def fetch_history(self, ticker: str, period: str = "60d") -> list[Bar]:
        self.last_period = period
        return self.bars_to_return

    def fetch_quote(self, ticker: str):
        raise NotImplementedError

    def fetch_news(self, ticker: str, limit: int = 10):
        return []

    def fetch_fundamentals(self, ticker: str):
        raise NotImplementedError

    def fetch_market_overview(self):
        raise NotImplementedError


def test_get_history_accepts_30d_60d_6m_1y(db_session) -> None:
    yf = _FakeYF()
    svc = DataService(db_session, yf)
    for period in ("30d", "60d", "6m", "1y"):
        svc.get_history("AAPL", period=period)
        assert yf.last_period == period


def test_get_history_rejects_unknown_period(db_session) -> None:
    yf = _FakeYF()
    svc = DataService(db_session, yf)
    with pytest.raises(ValueError, match="unsupported period"):
        svc.get_history("AAPL", period="invalid")
