"""End-to-end-ish test for the chart-data lazy-load handoff.

Confirms that the second chunk's bars strictly precede the first chunk —
the property that lets the frontend prepend without overlap. Also confirms
indicator alignment with the returned bar window.
"""
from datetime import date, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.data.types import Bar


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _fake_bars(start: date, n: int) -> list[Bar]:
    return [
        Bar(date=start + timedelta(days=i),
            open=10.0, high=10.5, low=9.5,
            close=10.0 + i * 0.01, volume=1_000_000)
        for i in range(n)
    ]


def test_lazy_load_chunk_strictly_precedes_initial(client: TestClient, monkeypatch):
    """Lazy chunk: when client requests ?before=X, every bar is < X.
    This is the invariant that lets the frontend prepend without overlap."""
    _login(client, monkeypatch)
    today = date.today()
    before_date = today - timedelta(days=60)
    before_iso = before_date.isoformat()
    # Fake bars end one day before `before_date` so they respect the boundary
    # the same way a real yfinance response would (the route passes end=before-1).
    fake_lazy = _fake_bars(before_date - timedelta(days=430), 430)
    with patch(
        "marketpulse.data.yfinance_client.YFinanceClient.fetch_history_range",
        return_value=fake_lazy,
    ):
        res = client.get(f"/stock/AAPL/chart-data?before={before_iso}&count=180")
    assert res.status_code == 200
    chunk = res.json()
    assert chunk["bars"], "lazy chunk should not be empty when fake data spans the window"
    # Every bar in the chunk must be strictly before `before`.
    assert all(b["time"] < before_iso for b in chunk["bars"])


def test_lazy_load_indicators_align_with_bars(client: TestClient, monkeypatch):
    """Every indicator point's `time` must match one of the bar `time` values
    (no orphan indicator points outside the bar window).
    """
    _login(client, monkeypatch)
    today = date.today()
    fake = _fake_bars(today - timedelta(days=430), 430)
    with patch(
        "marketpulse.data.yfinance_client.YFinanceClient.fetch_history_range",
        return_value=fake,
    ):
        res = client.get(
            f"/stock/AAPL/chart-data?before={today.isoformat()}&count=180",
        )
    assert res.status_code == 200
    body = res.json()
    bar_times = {b["time"] for b in body["bars"]}
    for series_name in ("ema12", "sma50", "sma200", "bb_upper", "rsi"):
        for p in body[series_name]:
            assert p["time"] in bar_times, (
                f"{series_name} point at {p['time']} has no matching bar"
            )
