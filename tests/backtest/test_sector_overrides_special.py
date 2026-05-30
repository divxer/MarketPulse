# Layer: test
"""Broad-market ETFs are pinned via overrides (no natural GICS sector).

Plain equities are intentionally NOT pinned here — they get real GICS sectors
from the yfinance warmup job. Pre-existing custom correlation buckets
(leveraged_qqq / quantum_compute) feed SectorExposureGate and must stay as-is.
"""
from __future__ import annotations

from marketpulse.backtest.sector import load_sector_overrides


def test_broad_market_etfs_pinned():
    ov = load_sector_overrides()
    assert ov.get("SPY") == "ETF"
    assert ov.get("QQQ") == "ETF"
    assert ov.get("IWM") == "ETF"


def test_existing_buckets_unchanged():
    # Guard against accidental relabeling — these values feed SectorExposureGate.
    ov = load_sector_overrides()
    assert ov.get("TQQQ") == "leveraged_qqq"
    assert ov.get("TNA") == "leveraged_small_cap"
    assert ov.get("QBTS") == "quantum_compute"
    assert ov.get("QUBT") == "quantum_compute"
