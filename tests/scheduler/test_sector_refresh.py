# Layer: test
"""Warmup fetches+persists sectors only for tickers not already resolved."""
from __future__ import annotations

import marketpulse.backtest.sector as sec
from marketpulse.db.models import Holding, WatchlistItem
from marketpulse.scheduler.sector_refresh import refresh_sector_cache


class _FakeClient:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def get_sector(self, ticker):
        self.calls.append(ticker)
        return self.mapping.get(ticker)


def test_refresh_resolves_only_uncached(db_session, monkeypatch, tmp_path):
    # _default_cache_path() reads the env at call time — no module reload needed
    # (reloading would pollute risk_gates._sector which imports these functions).
    monkeypatch.setenv("SECTOR_CACHE_PATH", str(tmp_path / "sector_cache.json"))
    # AAPL held w/ sector -> skip; TSLA uncached + not in overrides -> fetch;
    # SPY in overrides -> skip; ZZZZ fetch returns None -> not cached.
    # (TSLA is the "uncached" probe because MSFT/AAPL/SPY are all pinned in the
    # real config/sector_overrides.yaml.)
    db_session.add(Holding(ticker="AAPL", quantity=1, avg_cost=1, sector="Technology"))
    for t in ["AAPL", "TSLA", "SPY", "ZZZZ"]:
        db_session.add(WatchlistItem(ticker=t))
    db_session.commit()

    client = _FakeClient({"TSLA": "Consumer Cyclical", "ZZZZ": None})
    summary = refresh_sector_cache(db_session, client=client)

    assert "TSLA" in client.calls
    assert "AAPL" not in client.calls   # holdings.sector
    assert "SPY" not in client.calls     # override
    assert sec.load_sector_cache().get("TSLA") == "Consumer Cyclical"
    assert "ZZZZ" not in sec.load_sector_cache()  # None not cached
    assert summary.resolved == 1

    client.calls.clear()
    refresh_sector_cache(db_session, client=client)
    assert "TSLA" not in client.calls    # idempotent (now cached)
