"""Phase 7c reconciliation - type contracts."""
# Layer: unit
from __future__ import annotations

from decimal import Decimal

from marketpulse.reconcile.types import (
    _SEVERITY_RANK,
    DiffRow,
    DiffType,
    Severity,
)


def test_diff_type_enum_values_stable():
    assert DiffType.MATCHED.value == "matched"
    assert DiffType.MISSING_IN_BROKER.value == "missing_in_broker"
    assert DiffType.MISSING_IN_PAPER.value == "missing_in_paper"
    assert DiffType.QUANTITY_MISMATCH.value == "quantity_mismatch"
    assert DiffType.SIDE_MISMATCH.value == "side_mismatch"


def test_severity_rank_order():
    # Lower rank = sorted earlier = more severe at top.
    assert _SEVERITY_RANK[DiffType.SIDE_MISMATCH] < _SEVERITY_RANK[DiffType.MISSING_IN_BROKER]
    assert _SEVERITY_RANK[DiffType.MISSING_IN_BROKER] < _SEVERITY_RANK[DiffType.QUANTITY_MISMATCH]
    assert _SEVERITY_RANK[DiffType.QUANTITY_MISMATCH] < _SEVERITY_RANK[DiffType.MISSING_IN_PAPER]
    assert _SEVERITY_RANK[DiffType.MISSING_IN_PAPER] < _SEVERITY_RANK[DiffType.MATCHED]


def test_diff_row_frozen():
    import dataclasses

    import pytest

    row = DiffRow(
        symbol="AAPL",
        diff_type=DiffType.MATCHED,
        paper_qty=Decimal("100"),
        broker_qty=Decimal("100"),
        delta=Decimal("0"),
        is_red=False,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        row.symbol = "MSFT"  # type: ignore[misc]


def test_severity_enum_values():
    assert Severity.GREEN.value == "green"
    assert Severity.YELLOW.value == "yellow"
    assert Severity.RED.value == "red"
    assert Severity.GRAY.value == "gray"
