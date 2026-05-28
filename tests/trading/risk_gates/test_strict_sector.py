# Layer: pure
"""6b-T10 + 2026-05-27 outage regression: strict_sector wrapper test.

Two layers covered:
  - Original 6b-T10: bridge contract from `unknown` → None (gate fail-closed)
  - 2026-05-27 fix: yfinance-backed fallback for equity tickers not in
    sector_overrides.yaml + persisted cache surviving container restarts
    (paper_trading_tick rejected 4/5 daily orders with `unknown_sector`
    until the wrapper was given a default yf_client).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import marketpulse.backtest.sector as backtest_sector

# === Original wrapper contract (6b-T10) ===


def test_strict_sector_returns_none_for_unknown(monkeypatch):
    from marketpulse.trading.risk_gates._sector import strict_sector

    monkeypatch.setattr(
        "marketpulse.trading.risk_gates._sector._get_sector",
        lambda t, **kw: "unknown",
    )
    assert strict_sector("ANY") is None


def test_strict_sector_passes_through_real_sector(monkeypatch):
    from marketpulse.trading.risk_gates._sector import strict_sector

    monkeypatch.setattr(
        "marketpulse.trading.risk_gates._sector._get_sector",
        lambda t, **kw: {"AAPL": "Technology", "JPM": "Financials"}[t],
    )
    assert strict_sector("AAPL") == "Technology"
    assert strict_sector("JPM") == "Financials"


def test_strict_sector_returns_none_on_empty_string(monkeypatch):
    """Defensive: get_sector never returns '' today, but treat falsy
    as None to keep the gate fail-closed."""
    from marketpulse.trading.risk_gates._sector import strict_sector

    monkeypatch.setattr(
        "marketpulse.trading.risk_gates._sector._get_sector",
        lambda t, **kw: "",
    )
    assert strict_sector("X") is None


# === 2026-05-27 outage regression: yfinance fallback + persisted cache ===


@pytest.fixture(autouse=True)
def _isolate_caches(tmp_path, monkeypatch):
    """Each test gets fresh in-memory caches + a tmp persisted-cache path."""
    backtest_sector._reset_caches_for_testing()
    import marketpulse.trading.risk_gates._sector as ss_mod
    monkeypatch.setattr(ss_mod, "_PERSISTED_LOADED", False)
    monkeypatch.setattr(ss_mod, "_CACHE_PATH", tmp_path / "sector_cache.json")
    yield
    backtest_sector._reset_caches_for_testing()


def test_yaml_override_wins_without_yfinance(monkeypatch):
    """Tickers in sector_overrides.yaml resolve without touching yfinance."""
    from marketpulse.trading.risk_gates._sector import strict_sector
    # TQQQ is in the shipped overrides yaml → no yfinance call.
    with patch("yfinance.Ticker") as yf_mock:
        result = strict_sector("TQQQ")
    yf_mock.assert_not_called()
    assert result == "leveraged_qqq"


def test_user_holdings_tickers_resolved_via_yaml(monkeypatch):
    """Regression guard for 2026-05-27 outage: AMSC/AAPL/AMAT/GOOGL must
    resolve without yfinance (pinned in YAML)."""
    from marketpulse.trading.risk_gates._sector import strict_sector
    with patch("yfinance.Ticker") as yf_mock:
        sectors = {
            t: strict_sector(t)
            for t in ("AMSC", "AAPL", "AMAT", "GOOGL")
        }
    yf_mock.assert_not_called()
    assert sectors == {
        "AMSC": "Industrials",
        "AAPL": "Technology",
        "AMAT": "Technology",
        "GOOGL": "Communication Services",
    }


def test_equity_falls_back_to_yfinance(monkeypatch):
    """Equity ticker not in YAML → lazy yfinance lookup returns its sector."""
    from marketpulse.trading.risk_gates._sector import strict_sector

    class _FakeTicker:
        info = {"sector": "Technology"}

    with patch("yfinance.Ticker", return_value=_FakeTicker()) as yf_mock:
        result = strict_sector("ZZZZ_NOT_IN_YAML")
    yf_mock.assert_called_once_with("ZZZZ_NOT_IN_YAML")
    assert result == "Technology"


def test_yfinance_failure_returns_none(monkeypatch):
    """yfinance throwing → strict_sector returns None (gate fail-closed)."""
    from marketpulse.trading.risk_gates._sector import strict_sector
    with patch("yfinance.Ticker", side_effect=RuntimeError("rate limited")):
        result = strict_sector("UNKNOWN_TICKER_XYZ")
    assert result is None


def test_yfinance_returns_no_sector_yields_none(monkeypatch):
    """yfinance info missing 'sector' field → None."""
    from marketpulse.trading.risk_gates._sector import strict_sector

    class _NoSectorTicker:
        info = {"symbol": "ZZZ"}

    with patch("yfinance.Ticker", return_value=_NoSectorTicker()):
        result = strict_sector("ZZZ_NO_SECTOR_FIELD")
    assert result is None


def test_successful_lookup_persists_to_disk(monkeypatch):
    """First call writes data/sector_cache.json so restarts skip yfinance."""
    import marketpulse.trading.risk_gates._sector as ss_mod
    cache_path: Path = ss_mod._CACHE_PATH

    class _FakeTicker:
        info = {"sector": "Industrials"}

    with patch("yfinance.Ticker", return_value=_FakeTicker()):
        ss_mod.strict_sector("FOO_NEW_TICKER")
    assert cache_path.exists()
    data = json.loads(cache_path.read_text())
    assert data["FOO_NEW_TICKER"] == "Industrials"


def test_cache_hit_avoids_second_yfinance_call(monkeypatch):
    """Process-level cache prevents redundant yfinance calls."""
    import marketpulse.trading.risk_gates._sector as ss_mod

    class _FakeTicker:
        info = {"sector": "Healthcare"}

    with patch("yfinance.Ticker", return_value=_FakeTicker()) as yf_mock:
        a = ss_mod.strict_sector("BAR_NEW_TICKER")
        b = ss_mod.strict_sector("BAR_NEW_TICKER")
    assert a == b == "Healthcare"
    yf_mock.assert_called_once()


def test_persisted_cache_loaded_on_first_call(monkeypatch):
    """A pre-existing sector_cache.json populates the in-memory cache,
    so the first strict_sector call after restart skips yfinance.
    """
    import marketpulse.trading.risk_gates._sector as ss_mod
    cache_path: Path = ss_mod._CACHE_PATH
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"BAZ_FROM_DISK": "Energy"}))

    with patch("yfinance.Ticker") as yf_mock:
        result = ss_mod.strict_sector("BAZ_FROM_DISK")
    yf_mock.assert_not_called()
    assert result == "Energy"
