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


def test_watchlist_batch_add_partial_success(client, db_url, monkeypatch):
    _login(client, monkeypatch)
    _seed(db_url)  # MSFT already present
    res = client.post("/watchlist", data={"tickers": "msft, GOOGL\nNVDA\n@@bad"})
    assert res.status_code == 200
    body = res.text
    assert "mp-wl-grid" in body          # full grid fragment
    assert "GOOGL" in body and "NVDA" in body
    assert "added 2" in body.lower() or "added&nbsp;2" in body.lower()
    assert "already" in body.lower()     # MSFT existed
    assert "invalid" in body.lower()     # @@bad


def test_watchlist_batch_add_empty_is_noop(client, db_url, monkeypatch):
    _login(client, monkeypatch)
    _seed(db_url)  # MSFT only
    res = client.post("/watchlist", data={"tickers": "  \n , \n "})
    assert res.status_code == 200        # no 500
    assert "added 0" in res.text.lower()
    from marketpulse.db import base as db_base
    gen = db_base.session_scope()
    s = next(gen)
    assert s.query(WatchlistItem).count() == 1
    gen.close()


def test_watchlist_delete_returns_grid(client, db_url, monkeypatch):
    _login(client, monkeypatch)
    _seed(db_url)
    from marketpulse.db import base as db_base
    gen = db_base.session_scope()
    s = next(gen)
    item_id = s.query(WatchlistItem).filter_by(ticker="MSFT").one().id
    gen.close()
    res = client.delete(f"/watchlist/{item_id}")
    assert res.status_code == 200
    assert "mp-wl-grid" in res.text       # full grid fragment
    assert "MSFT" not in res.text


def test_watchlistitem_has_no_notes():
    from marketpulse.db.models import WatchlistItem
    assert not hasattr(WatchlistItem, "notes")


def test_watchlist_legacy_single_ticker_returns_204(client, db_url, monkeypatch):
    # /stock 加自选 button posts the legacy `ticker` field. It must still add the
    # ticker, but return 204 (htmx no-swap) so the grid is NOT injected into /stock.
    _login(client, monkeypatch)
    res = client.post("/watchlist", data={"ticker": "AAPL"})
    assert res.status_code == 204
    from marketpulse.db import base as db_base
    gen = db_base.session_scope()
    s = next(gen)
    assert s.query(WatchlistItem).filter_by(ticker="AAPL").count() == 1
    gen.close()
