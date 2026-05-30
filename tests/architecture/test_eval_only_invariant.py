# Layer: test
"""Task #57 — eval-only invariant: the eval-analysis core must never import the
allocator / order-placement / watchlist-mutation layers. Scans only import lines
so a module name in a docstring/comment can't cause a false positive."""
from __future__ import annotations

from pathlib import Path

_MODULE = (
    Path(__file__).resolve().parents[2]
    / "marketpulse" / "ai" / "eval_analysis.py"
)

_FORBIDDEN = (
    "marketpulse.trading.execution_engine",
    "marketpulse.trading.forward_engine",
    "marketpulse.trading.daily_cycle",
    "marketpulse.trading.bid_aggregator",
    "marketpulse.backtest.allocation",      # allocate_for_day kernel
    "marketpulse.broker.order_service",     # order placement
    "marketpulse.web.routes.watchlist",     # watchlist mutation (add/delete)
)


def _import_lines(path: Path) -> list[str]:
    out = []
    for raw in path.read_text().splitlines():
        s = raw.strip()
        if s.startswith("import ") or s.startswith("from "):
            out.append(s)
    return out


def test_eval_analysis_has_no_forbidden_imports():
    lines = _import_lines(_MODULE)
    for forbidden in _FORBIDDEN:
        offenders = [ln for ln in lines if forbidden in ln]
        assert not offenders, (
            f"eval_analysis.py must not import {forbidden} "
            f"(eval-only invariant). Offending lines: {offenders}"
        )
