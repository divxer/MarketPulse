# Layer: pure
"""6b-T10: strict_sector wrapper test.

marketpulse.backtest.sector.get_sector() always returns a str — falling
back to 'unknown' when no resolution succeeds. SectorExposureGate's
fail-closed semantics need `None` for the unknown case (lock 6b-L8).
This wrapper bridges the two contracts."""

from __future__ import annotations


def test_strict_sector_returns_none_for_unknown(monkeypatch):
    from marketpulse.trading.risk_gates._sector import strict_sector

    # Patch the backing get_sector to return 'unknown' regardless of input.
    monkeypatch.setattr(
        "marketpulse.trading.risk_gates._sector._get_sector",
        lambda t: "unknown",
    )
    assert strict_sector("ANY") is None


def test_strict_sector_passes_through_real_sector(monkeypatch):
    from marketpulse.trading.risk_gates._sector import strict_sector

    monkeypatch.setattr(
        "marketpulse.trading.risk_gates._sector._get_sector",
        lambda t: {"AAPL": "Technology", "JPM": "Financials"}[t],
    )
    assert strict_sector("AAPL") == "Technology"
    assert strict_sector("JPM") == "Financials"


def test_strict_sector_returns_none_on_empty_string(monkeypatch):
    """Defensive: get_sector never returns '' today, but treat falsy
    as None to keep the gate fail-closed."""
    from marketpulse.trading.risk_gates._sector import strict_sector

    monkeypatch.setattr(
        "marketpulse.trading.risk_gates._sector._get_sector",
        lambda t: "",
    )
    assert strict_sector("X") is None
