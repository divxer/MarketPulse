# tests/backtest/test_allocation_extraction.py
# Layer: behavioral
"""6a-0 regression: simulate_shared_pool's PortfolioBacktestResult is
behaviorally + public-field equal pre/post the allocate_for_day
extraction.

Per 6a-0 contract (spec § 2): behavioral + public-field equality, NOT
byte-identical. Numeric outputs and all bid_history records compared
field-by-field; intentionally versioned/provenance strings excluded.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pytest

BASELINE_PATH = Path(__file__).parent / "fixtures" / "phase5_warm_pool_baseline.json"

# Versioned/provenance strings — excluded from the comparison.
EXCLUDED_FIELDS = {"bid_policy", "contribution_policy", "risk_policy", "sizing_policy"}

FLOAT_PRECISION = 10  # decimal places for stable cross-platform float repr


def _normalize(value):
    """Convert dataclasses / dates / Decimal / floats to JSON-friendly
    primitives so the comparison is structural, not object-identity.

    Floats are rounded to FLOAT_PRECISION decimal places to neutralize
    repr drift across Python versions and platforms (e.g.
    0.30000000000000004 vs 0.3). This matches the spec's "behavioral +
    public-field equality" contract — drift below 1e-10 is not a real
    regression."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _normalize(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items() if k not in EXCLUDED_FIELDS}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if value.__class__.__name__ == "Decimal":
        return str(value)
    if isinstance(value, float):
        return round(value, FLOAT_PRECISION)
    return value


def dump_result(result) -> dict:
    return _normalize(result)


def test_simulate_shared_pool_matches_frozen_baseline(phase5d_warm_pool):
    """Generate-or-compare regression guard.

    If RUN_6A0_BASELINE=1 is set, this test WRITES the baseline JSON
    (used once, pre-extraction). Otherwise it COMPARES the current run
    to the committed baseline. The compare path is what catches an
    extraction regression in 6a-0.4.

    Note: phase5d_warm_pool fixture (defined in tests/conftest.py)
    returns {"shared": PortfolioBacktestResult}. It already invoked
    simulate_shared_pool; we consume the cached result here."""
    result = phase5d_warm_pool["shared"]
    current = dump_result(result)

    if os.environ.get("RUN_6A0_BASELINE") == "1":
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(json.dumps(current, indent=2, sort_keys=True))
        pytest.skip(f"Baseline regenerated at {BASELINE_PATH}")

    assert BASELINE_PATH.exists(), (
        f"Baseline missing at {BASELINE_PATH}. "
        f"Regenerate via: RUN_6A0_BASELINE=1 uv run pytest "
        f"tests/backtest/test_allocation_extraction.py::"
        f"test_simulate_shared_pool_matches_frozen_baseline"
    )
    expected = json.loads(BASELINE_PATH.read_text())
    assert current == expected, (
        "6a-0 regression: simulate_shared_pool output differs from the "
        "committed baseline. The extraction (or some upstream change) "
        "altered behavior. If the change is INTENTIONAL, regenerate via "
        "RUN_6A0_BASELINE=1 and review the diff carefully."
    )


def test_extraction_marker_post():
    """Asserts the heavy lifting moved out of portfolio_simulator into
    allocate_for_day."""
    from pathlib import Path
    sim_src = Path("marketpulse/backtest/portfolio_simulator.py").read_text()
    alloc_src = Path("marketpulse/backtest/allocation.py").read_text()
    # The kernel now lives in allocation.py
    assert "compute_position_sizes" in alloc_src
    assert ("compute_adjusted_bid_weight" in alloc_src
            or "compute_bid_weights" in alloc_src)
    # portfolio_simulator calls into the kernel rather than owning it
    assert "allocate_for_day(" in sim_src
    assert "from marketpulse.backtest.allocation import" in sim_src


def test_allocation_dataclasses_exist():
    """6a-L9: AllocationContext carries every input the allocator needs
    as an explicit named field. No hidden today dependency."""
    from datetime import date

    from marketpulse.backtest.allocation import (
        AllocationContext,
        AllocationResult,
        AllocationWinner,
        BidCandidate,
        BlockedBidReason,
        PositionSnapshot,
        SizingContext,
        allocate_for_day,
    )

    ctx = AllocationContext(
        allocation_date=date(2026, 5, 21),
        target_vol=0.01,
        lookback_days=60,
        sector_caps_enabled=True,
        sector_cap_pct=0.40,
        correlation_caps_enabled=True,
        correlation_cap_pct=0.40,
        correlation_threshold=0.60,
        contribution_enabled=False,
        contribution_lambda=0.5,
        pool_corr_mode="excludes_self",
        phase5e_warm_pool_overlap_days=20,
        max_capital_in_use=10_000.0,
    )
    assert ctx.allocation_date == date(2026, 5, 21)

    sizing = SizingContext(
        base_position_size=1_000.0,
        min_position=200.0,
        max_position=4_000.0,
        sizing_enabled=True,
        per_strategy_overrides={},
    )
    assert sizing.base_position_size == 1_000.0

    assert AllocationWinner is not None
    assert AllocationResult is not None
    assert BidCandidate is not None
    assert PositionSnapshot is not None
    assert BlockedBidReason is not None
    assert callable(allocate_for_day)


def test_allocation_module_does_not_reference_phase5_only_concerns():
    """6a-L1: allocate_for_day extracts ONLY BID→SIZE→DEDUP→ALLOC.
    CLOSE, MTM, RECORD, equity-curve update, contribution decomposition,
    and rolling-stats finalization stay in portfolio_simulator.py.

    Strips Python comments and docstrings before searching so the test
    does not false-fire on documentation that mentions these tokens.
    """
    import io
    import tokenize
    from pathlib import Path

    def _strip_comments_and_docstrings(src: str) -> str:
        out: list[str] = []
        prev_type: int | None = None
        tokens = tokenize.tokenize(io.BytesIO(src.encode("utf-8")).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and prev_type in (
                tokenize.INDENT, tokenize.NEWLINE, None,
            ):
                # Likely a module/class/function docstring — skip.
                continue
            out.append(tok.string)
            prev_type = tok.type
        return " ".join(out)

    src = _strip_comments_and_docstrings(
        Path("marketpulse/backtest/allocation.py").read_text()
    )
    forbidden = [
        "daily_equity_curve",
        "mark_to_market",
        "decompose_day_contributions",
        "compute_rolling_metrics",
        "finalize_strategy_contribution",
    ]
    for token in forbidden:
        assert token not in src, (
            f"6a-L1 boundary violation: '{token}' leaked into "
            f"marketpulse/backtest/allocation.py"
        )
