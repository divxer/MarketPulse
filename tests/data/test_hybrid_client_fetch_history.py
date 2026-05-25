# Layer: stateless
"""HybridClient.fetch_history routing — silent-truncation bug regression tests.

Bug: Tencent's kline endpoint silently returns 1 row for long-period requests
on non-CN tickers instead of raising. HybridClient previously preferred Tencent
for all periods, so `fetch_history("SPY", "1y")` returned 1 row → the
evaluation framework wrote 0 outcomes for 23 events in production.

Fix: long periods (anything not 30d/60d) skip Tencent entirely; short periods
keep the Tencent-first behavior plus a <5 row defense-in-depth fallback.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from marketpulse.data.hybrid_client import HybridClient
from marketpulse.data.types import Bar


def _bar(d: date, close: float = 100.0) -> Bar:
    return Bar(date=d, open=close, high=close, low=close, close=close, volume=1000)


def _bars(n: int) -> list[Bar]:
    start = date(2026, 1, 1)
    return [_bar(start + timedelta(days=i)) for i in range(n)]


class _FakeYF:
    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars
        self.calls: list[tuple[str, str]] = []

    def fetch_history(self, ticker: str, period: str = "60d") -> list[Bar]:
        self.calls.append((ticker, period))
        return self._bars

    # Unused protocol stubs for typing parity (HybridClient never calls these
    # on the long-period path under test).
    def fetch_quote(self, ticker: str):  # pragma: no cover - unused
        raise NotImplementedError

    def fetch_news(self, ticker: str, limit: int = 10):  # pragma: no cover
        raise NotImplementedError

    def fetch_fundamentals(self, ticker: str):  # pragma: no cover
        raise NotImplementedError

    def fetch_market_overview(self):  # pragma: no cover
        raise NotImplementedError


class _FakeTencent:
    def __init__(
        self,
        bars: list[Bar] | None = None,
        *,
        raises: Exception | None = None,
    ) -> None:
        self._bars = bars or []
        self._raises = raises
        self.calls: list[tuple[str, str]] = []

    def fetch_quote(self, ticker: str):  # pragma: no cover - unused here
        raise NotImplementedError

    def fetch_history(self, ticker: str, period: str = "60d") -> list[Bar]:
        self.calls.append((ticker, period))
        if self._raises is not None:
            raise self._raises
        return self._bars


def test_fetch_history_long_period_skips_tencent():
    """1y request must bypass Tencent entirely — the silent-truncation bug."""
    yf = _FakeYF(_bars(251))  # realistic yfinance 1y output
    tencent = _FakeTencent(_bars(1))  # would silently truncate to 1 row
    client = HybridClient(yf, tencent=tencent, prefer_tencent=True)

    result = client.fetch_history("SPY", period="1y")

    assert len(result) == 251
    assert tencent.calls == [], "Tencent must not be called for long periods"
    assert yf.calls == [("SPY", "1y")]


def test_fetch_history_short_period_uses_tencent():
    """30d with a healthy Tencent (>=5 rows) returns Tencent's data."""
    yf = _FakeYF(_bars(30))
    tencent_bars = _bars(30)
    tencent = _FakeTencent(tencent_bars)
    client = HybridClient(yf, tencent=tencent, prefer_tencent=True)

    result = client.fetch_history("SPY", period="30d")

    assert result == tencent_bars
    assert tencent.calls == [("SPY", "30d")]
    assert yf.calls == [], "yfinance must not be called when Tencent succeeds"


def test_fetch_history_short_period_falls_back_when_tencent_returns_too_few():
    """Defense-in-depth: Tencent returning <5 rows for 30d triggers fallback."""
    yf_bars = _bars(30)
    yf = _FakeYF(yf_bars)
    tencent = _FakeTencent(_bars(2))  # silent quota / region block
    client = HybridClient(yf, tencent=tencent, prefer_tencent=True)

    result = client.fetch_history("SPY", period="30d")

    assert result == yf_bars
    assert tencent.calls == [("SPY", "30d")]
    assert yf.calls == [("SPY", "30d")]


def test_fetch_history_short_period_falls_back_when_tencent_raises():
    """Existing behavior preserved: exception from Tencent → yfinance."""
    yf_bars = _bars(30)
    yf = _FakeYF(yf_bars)
    tencent = _FakeTencent(raises=RuntimeError("boom"))
    client = HybridClient(yf, tencent=tencent, prefer_tencent=True)

    result = client.fetch_history("AAPL", period="60d")

    assert result == yf_bars
    assert tencent.calls == [("AAPL", "60d")]
    assert yf.calls == [("AAPL", "60d")]


@pytest.mark.parametrize("period", ["30d", "60d", "6m", "1y"])
def test_fetch_history_no_tencent_configured_uses_yfinance(period: str):
    """tencent=None → always yfinance regardless of period."""
    yf_bars = _bars(10)
    yf = _FakeYF(yf_bars)
    client = HybridClient(yf, tencent=None, prefer_tencent=True)

    result = client.fetch_history("SPY", period=period)

    assert result == yf_bars
    assert yf.calls == [("SPY", period)]
