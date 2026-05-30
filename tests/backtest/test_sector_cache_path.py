# Layer: test
"""Sector cache path honors SECTOR_CACHE_PATH env (so it can live on /data).

`_default_cache_path()` reads os.environ at call time, so no module reload is
needed — and reloading the module would pollute other modules that hold
references to its functions (e.g. risk_gates._sector)."""
from __future__ import annotations

import marketpulse.backtest.sector as sec


def test_cache_path_follows_env(tmp_path, monkeypatch):
    target = tmp_path / "sub" / "sector_cache.json"
    monkeypatch.setenv("SECTOR_CACHE_PATH", str(target))
    sec.save_sector_cache({"AAPL": "Technology"})
    assert target.exists()  # written to env path, parent auto-created
    assert sec.load_sector_cache() == {"AAPL": "Technology"}


def test_cache_path_defaults_to_app_data(monkeypatch):
    monkeypatch.delenv("SECTOR_CACHE_PATH", raising=False)
    assert str(sec._default_cache_path()).endswith("/data/sector_cache.json")
