# Layer: test
"""Sector cache path honors SECTOR_CACHE_PATH env (so it can live on /data)."""
from __future__ import annotations

import importlib


def test_cache_path_follows_env(tmp_path, monkeypatch):
    target = tmp_path / "sub" / "sector_cache.json"
    monkeypatch.setenv("SECTOR_CACHE_PATH", str(target))
    import marketpulse.backtest.sector as sec
    importlib.reload(sec)
    try:
        sec.save_sector_cache({"AAPL": "Technology"})
        assert target.exists()
        assert sec.load_sector_cache() == {"AAPL": "Technology"}
    finally:
        monkeypatch.delenv("SECTOR_CACHE_PATH", raising=False)
        importlib.reload(sec)


def test_cache_path_defaults_to_app_data(monkeypatch):
    monkeypatch.delenv("SECTOR_CACHE_PATH", raising=False)
    import marketpulse.backtest.sector as sec
    importlib.reload(sec)
    assert str(sec._default_cache_path()).endswith("/data/sector_cache.json")
