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


# === Integration regression: composite gate composition ===


def test_composite_gate_resolves_yaml_and_yfinance(monkeypatch, tmp_path):
    """End-to-end shape check: build the production composite, feed it
    a YAML-pinned ticker and a yfinance-resolvable ticker, and verify
    the sector_exposure gate sees real sectors (not 'unknown').

    Guards against regressions where sector_provider wiring drifts at
    factory or paper_trading_tick.py composition root — the 2026-05-27
    outage class.
    """
    from datetime import UTC, datetime
    from datetime import date as date_cls
    from datetime import time as time_cls
    from decimal import Decimal

    from marketpulse.trading import risk_gates as rg
    from marketpulse.trading.calendar import NYTradingCalendar
    from marketpulse.trading.clock import WallClock
    from marketpulse.trading.types import (
        AllocationRunId,
        OrderRequest,
        RiskIntent,
    )

    class _FakeRepo:
        def projected_sector_exposure(self, *, ticker, sector, projected_notional):
            return Decimal("0"), Decimal("0")

        def realized_pl_today(self, *, today_ny):
            return Decimal("0")

    class _FakeCfgProvider:
        def global_config(self):
            return rg.RiskGateConfig(
                market_hours=rg.MarketHoursConfig(
                    enabled=False, exchange="XNYS",
                    allow_regular_session=True, allow_post_close=True,
                    post_close_until=time_cls(18, 0),
                    allow_premarket=False,
                ),
                daily_loss=rg.DailyLossConfig(
                    enabled=False, daily_loss_limit=Decimal("0"),
                ),
                sector_exposure=rg.SectorExposureConfig(
                    enabled=True, max_sector_exposure_pct=0.99,
                    configured_max_capital_in_use=Decimal("10000"),
                ),
            )

        def strategy_config(self, _strategy):
            return rg.StrategyRiskConfig(
                max_position_notional=Decimal("100000"),
            )

    class _YfTechTicker:
        info = {"sector": "Technology"}

    composite = rg.build_standard_composite(
        config_provider=_FakeCfgProvider(),
        repository=_FakeRepo(),
        calendar=NYTradingCalendar(),
        clock=WallClock(),
        sector_provider=rg.strict_sector,  # production wiring
    )

    def _order(ticker: str) -> OrderRequest:
        return OrderRequest(
            strategy="general", ticker=ticker, quantity=1,
            event_time=datetime.now(UTC),
            allocation_date=date_cls.today(),
            event_price=Decimal("100"),
            horizon_date=date_cls.today(),
            horizon_price=Decimal("105"),
            allocation_run_id=AllocationRunId("paper-test"),
            strategy_version="v1",
            allocator_version="phase6a-v1",
            execution_engine_version="phase6a-v1",
            weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
            contribution_multiplier=1.0, adjusted_bid_weight=1.0,
            effective_corr_window=60,
            rewarded_for_negative_corr=False,
            would_change_rank=False,
            size_clamped_by_override=False,
            risk_intent=RiskIntent.OPEN,
        )

    # AMSC is pinned in YAML — no yfinance call.
    with patch("yfinance.Ticker") as yf_mock:
        amsc_result = composite.check_pre_trade(order_request=_order("AMSC"))
    yf_mock.assert_not_called()
    amsc_sector_gate = next(
        g for g in amsc_result.context["per_gate"]
        if g["gate_name"] == "sector_exposure"
    )
    assert amsc_sector_gate["reason"] != "unknown_sector", amsc_result

    # ZZQQ_FAKE not in YAML → falls through to yfinance fallback.
    with patch("yfinance.Ticker", return_value=_YfTechTicker()) as yf_mock:
        fake_result = composite.check_pre_trade(order_request=_order("ZZQQ_FAKE"))
    yf_mock.assert_called_once_with("ZZQQ_FAKE")
    fake_sector_gate = next(
        g for g in fake_result.context["per_gate"]
        if g["gate_name"] == "sector_exposure"
    )
    assert fake_sector_gate["reason"] != "unknown_sector", fake_result


def test_safe_sector_returns_unknown_string_for_unresolved(monkeypatch):
    """Allocator-facing wrapper returns "unknown" (str), not None."""
    from marketpulse.trading.risk_gates import safe_sector
    with patch("yfinance.Ticker", side_effect=RuntimeError("rate limited")):
        result = safe_sector("UNRESOLVABLE_XYZ")
    assert result == "unknown"


def test_safe_sector_returns_yaml_match(monkeypatch):
    """YAML hit returns the pinned sector — no yfinance touch."""
    from marketpulse.trading.risk_gates import safe_sector
    with patch("yfinance.Ticker") as yf_mock:
        assert safe_sector("TQQQ") == "leveraged_qqq"
    yf_mock.assert_not_called()


def test_persisted_cache_load_failure_retries(monkeypatch):
    """A transient load failure must not disable persisted-cache reads forever."""
    import marketpulse.trading.risk_gates._sector as ss_mod

    calls = 0

    def flaky_load(_path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary read failure")
        return {"RETRY_FROM_DISK": "Industrials"}

    monkeypatch.setattr(ss_mod, "load_sector_cache", flaky_load)

    with patch("yfinance.Ticker", side_effect=RuntimeError("network unavailable")):
        assert ss_mod.strict_sector("RETRY_FROM_DISK") is None
    with patch("yfinance.Ticker") as yf_mock:
        assert ss_mod.strict_sector("RETRY_FROM_DISK") == "Industrials"

    yf_mock.assert_not_called()
    assert calls == 2


def test_safe_sector_returns_unknown_for_unresolved(monkeypatch):
    """Allocator-facing provider keeps a string contract while sharing lookup code."""
    from marketpulse.trading.risk_gates._sector import safe_sector

    monkeypatch.setattr(
        "marketpulse.trading.risk_gates._sector._get_sector",
        lambda t, **kw: "unknown",
    )

    assert safe_sector("NOPE") == "unknown"
