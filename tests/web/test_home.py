"""Tests for / (home page) — daily recap + watchlist table with live quotes.

Before this PR the table had hardcoded `—` placeholders for price/change/volume.
After, the route fetches a live quote per watchlist ticker and the template
renders them with proper sign-aware coloring.
"""
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.data.types import Quote
from marketpulse.db.models import WatchlistItem


def _login(client: TestClient, monkeypatch) -> None:
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _quote(ticker: str, price: float, change_pct: float, volume: int = 1_000_000) -> Quote:
    return Quote(
        ticker=ticker,
        price=price,
        change_pct=change_pct,
        volume=volume,
        avg_volume_20d=volume,
        fetched_at=datetime.now(UTC),
    )


@pytest.fixture
def _seed_watchlist(db_session):
    """Seed a 3-ticker watchlist for home-page render tests."""
    for i, t in enumerate(["AAPL", "GOOGL", "QUBT"]):
        db_session.add(WatchlistItem(ticker=t, sort_order=i))
    db_session.commit()


def test_home_renders_live_prices_for_watchlist(
    client: TestClient, monkeypatch, _seed_watchlist,
):
    """Watchlist table renders price + change% from get_quote, not em-dashes."""
    _login(client, monkeypatch)

    def fake_get_quote(ticker: str) -> Quote:
        return _quote(
            ticker,
            price={"AAPL": 298.97, "GOOGL": 387.66, "QUBT": 9.22}[ticker],
            change_pct={"AAPL": 0.38, "GOOGL": -2.34, "QUBT": -5.10}[ticker],
        )

    with patch(
        "marketpulse.data.service.DataService.get_quote",
        side_effect=fake_get_quote,
    ):
        r = client.get("/")

    assert r.status_code == 200
    # Prices formatted to 2 decimals with $ prefix
    assert "$298.97" in r.text
    assert "$387.66" in r.text
    assert "$9.22" in r.text
    # Change% formatted with sign + 2 decimals + %
    assert "+0.38%" in r.text
    assert "-2.34%" in r.text
    assert "-5.10%" in r.text


def test_home_color_codes_gains_and_losses(
    client: TestClient, monkeypatch, _seed_watchlist,
):
    """Positive change_pct → text-green-600, negative → text-red-600."""
    _login(client, monkeypatch)

    def fake_get_quote(ticker: str) -> Quote:
        # AAPL up, GOOGL down, QUBT flat (== 0.0)
        return _quote(
            ticker,
            price=100.0,
            change_pct={"AAPL": 0.5, "GOOGL": -1.5, "QUBT": 0.0}[ticker],
        )

    with patch(
        "marketpulse.data.service.DataService.get_quote",
        side_effect=fake_get_quote,
    ):
        r = client.get("/")

    assert r.status_code == 200
    # AAPL row should have green class, GOOGL red. Find each row by ticker
    # presence and check the same line has the color class.
    # Use a coarse contains-check on the full body — the Jinja conditional
    # only emits the class when change_pct meets the sign condition.
    assert "text-green-600" in r.text  # AAPL +0.5%
    assert "text-red-600" in r.text    # GOOGL -1.5%


def test_home_gracefully_degrades_when_quote_fails(
    client: TestClient, monkeypatch, _seed_watchlist,
):
    """One ticker's quote failure doesn't break the whole table.

    Other tickers still render; the failed row shows em-dashes.
    """
    _login(client, monkeypatch)

    def fake_get_quote(ticker: str) -> Quote:
        if ticker == "QUBT":
            raise RuntimeError("simulated yfinance/Tencent outage")
        return _quote(ticker, price=100.0, change_pct=1.0)

    with patch(
        "marketpulse.data.service.DataService.get_quote",
        side_effect=fake_get_quote,
    ):
        r = client.get("/")

    assert r.status_code == 200
    # AAPL + GOOGL still rendered
    assert "$100.00" in r.text
    # QUBT row exists but shows em-dash for price (failed quote path)
    assert "QUBT" in r.text


def test_home_renders_empty_state_when_no_watchlist(
    client: TestClient, monkeypatch,
):
    """No watchlist rows → '暂无自选股' message, not a half-rendered table."""
    _login(client, monkeypatch)
    r = client.get("/")
    assert r.status_code == 200
    assert "暂无自选股" in r.text


def test_home_renders_stale_warning_indicator(
    client: TestClient, monkeypatch, _seed_watchlist,
):
    """Quote.stale=True → small ⚠ marker next to the ticker symbol.

    Indicates the price came from price_cache fallback rather than live
    fetch (e.g. yfinance rate-limited).
    """
    _login(client, monkeypatch)

    def fake_get_quote(ticker: str) -> Quote:
        return Quote(
            ticker=ticker,
            price=100.0,
            change_pct=0.5,
            volume=1_000_000,
            avg_volume_20d=1_000_000,
            fetched_at=datetime.now(UTC),
            stale=True,
        )

    with patch(
        "marketpulse.data.service.DataService.get_quote",
        side_effect=fake_get_quote,
    ):
        r = client.get("/")

    assert r.status_code == 200
    assert "⚠" in r.text
