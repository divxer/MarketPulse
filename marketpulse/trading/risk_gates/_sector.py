"""strict_sector wrapper.

marketpulse.backtest.sector.get_sector() always returns str (falls back
to "unknown"). SectorExposureGate needs `None` for the unknown case so
its fail-closed branch (lock 6b-L8) fires. This module bridges the two:

    get_sector("AAPL")   → "Technology"
    get_sector("ZZZZZ")  → "unknown"    (the underlying contract)

    strict_sector("AAPL")  → "Technology"
    strict_sector("ZZZZZ") → None       (gate-friendly contract)
"""

from __future__ import annotations

from marketpulse.backtest.sector import get_sector as _get_sector

__all__ = ["strict_sector"]


def strict_sector(ticker: str) -> str | None:
    s = _get_sector(ticker)
    if not s or s == "unknown":
        return None
    return s
