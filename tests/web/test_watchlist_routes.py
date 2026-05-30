# Layer: test
"""Watchlist route — GET renders the AI-Universe grid."""
from __future__ import annotations

from datetime import UTC, date, datetime

from marketpulse.db.models import EvaluationEvent, PriceCacheEntry, WatchlistItem


def _login(client, monkeypatch):
    from marketpulse.auth.password import hash_password
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password("secret"))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": "secret"})


def _seed(db_url):
    from marketpulse.db import base as db_base
    gen = db_base.session_scope()
    s = next(gen)
    s.add(WatchlistItem(ticker="MSFT"))
    s.add(PriceCacheEntry(ticker="MSFT", date=date(2026, 5, 29), open=1, high=1,
                          low=1, close=450.0, volume=1))
    s.add(EvaluationEvent(event_type="ai_analysis", subtype="bullish",
          ticker="MSFT", event_time=datetime(2026, 5, 29, tzinfo=UTC),
          event_price=1.0, payload={}))
    s.commit()
    gen.close()


def test_watchlist_get_renders_grid(client, db_url, monkeypatch):
    _login(client, monkeypatch)
    _seed(db_url)
    res = client.get("/watchlist")
    assert res.status_code == 200
    body = res.text
    assert "mp-wl-grid" in body
    assert "MSFT" in body
    assert "Bullish" in body
    assert "Universe Only" in body
    assert "tickers" in body  # coverage summary
    assert "备注" not in body  # notes column gone
