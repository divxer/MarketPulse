# Layer: test
"""L2/L5: the watchlist presenter must be cache-only / zero-network — it must
not import any quote client, yfinance, DataService, or the network get_sector."""
from __future__ import annotations

from pathlib import Path

_MODULE = (Path(__file__).resolve().parents[2]
           / "marketpulse" / "web" / "watchlist_view.py")

_FORBIDDEN = (
    "marketpulse.data.service",        # DataService (live quotes)
    "marketpulse.data.yfinance_client",
    "marketpulse.data.tencent_client",
    "marketpulse.data.hybrid_client",
    "marketpulse.holdings.sector",     # network get_sector(ticker)
    "import yfinance",
)


def _import_lines(path: Path) -> list[str]:
    return [s for ln in path.read_text().splitlines()
            if (s := ln.strip()).startswith(("import ", "from "))]


def test_watchlist_view_is_zero_network():
    lines = _import_lines(_MODULE)
    for forbidden in _FORBIDDEN:
        bad = [ln for ln in lines if forbidden in ln]
        assert not bad, f"watchlist_view must not import {forbidden}: {bad}"
