"""Phase 7c - pure diff logic.

Exhaustively covers all 5 DiffType outcomes plus the spec's quantity
boundary cases (|diff| >= 1 inclusive) and the canonical sort order.
"""
# Layer: unit
from __future__ import annotations

from decimal import Decimal

from marketpulse.reconcile.diffing import reconcile_positions
from marketpulse.reconcile.types import DiffType


def test_empty_inputs_returns_empty_list():
    assert reconcile_positions({}, {}) == []


def test_only_broker_yields_missing_in_paper_with_delta_none():
    rows = reconcile_positions({}, {"AAPL": Decimal("100")})
    assert len(rows) == 1
    r = rows[0]
    assert r.symbol == "AAPL"
    assert r.diff_type == DiffType.MISSING_IN_PAPER
    assert r.paper_qty is None
    assert r.broker_qty == Decimal("100")
    assert r.delta is None
    assert r.is_red is False


def test_only_paper_nonzero_yields_red_missing_in_broker():
    rows = reconcile_positions({"AAPL": Decimal("100")}, {})
    assert len(rows) == 1
    r = rows[0]
    assert r.diff_type == DiffType.MISSING_IN_BROKER
    assert r.paper_qty == Decimal("100")
    assert r.broker_qty is None
    assert r.delta is None
    assert r.is_red is True


def test_only_paper_zero_qty_is_not_red():
    rows = reconcile_positions({"AAPL": Decimal("0")}, {})
    assert rows[0].is_red is False


def test_matched_exact():
    rows = reconcile_positions({"AAPL": Decimal("100")}, {"AAPL": Decimal("100")})
    r = rows[0]
    assert r.diff_type == DiffType.MATCHED
    assert r.delta == Decimal("0")


def test_matched_with_fractional_remnant():
    rows = reconcile_positions({"AAPL": Decimal("100")}, {"AAPL": Decimal("100.34")})
    r = rows[0]
    assert r.diff_type == DiffType.MATCHED
    assert r.delta == Decimal("-0.34")


def test_quantity_mismatch_boundary_inclusive_at_one_share():
    rows = reconcile_positions({"AAPL": Decimal("100")}, {"AAPL": Decimal("99")})
    assert rows[0].diff_type == DiffType.QUANTITY_MISMATCH


def test_quantity_mismatch_just_under_threshold_is_matched():
    rows = reconcile_positions({"AAPL": Decimal("100")}, {"AAPL": Decimal("99.01")})
    assert rows[0].diff_type == DiffType.MATCHED
    assert rows[0].delta == Decimal("0.99")


def test_side_mismatch_long_paper_short_broker():
    rows = reconcile_positions({"AAPL": Decimal("10")}, {"AAPL": Decimal("-5")})
    r = rows[0]
    assert r.diff_type == DiffType.SIDE_MISMATCH
    assert r.is_red is True
    assert r.delta == Decimal("15")


def test_side_mismatch_short_paper_long_broker():
    rows = reconcile_positions({"AAPL": Decimal("-10")}, {"AAPL": Decimal("5")})
    assert rows[0].diff_type == DiffType.SIDE_MISMATCH


def test_zero_on_either_side_is_not_side_mismatch():
    rows = reconcile_positions({"AAPL": Decimal("0")}, {"AAPL": Decimal("5")})
    assert rows[0].diff_type == DiffType.QUANTITY_MISMATCH


def test_sort_order_severity_then_alphabetical():
    rows = reconcile_positions(
        paper={
            "ZZZZ": Decimal("100"),
            "AAAA": Decimal("50"),
            "MMMM": Decimal("10"),
            "BBBB": Decimal("100"),
        },
        broker={
            "ZZZZ": Decimal("100"),
            "MMMM": Decimal("-1"),
            "BBBB": Decimal("50"),
            "CCCC": Decimal("99"),
        },
    )
    assert [r.symbol for r in rows] == ["MMMM", "AAAA", "BBBB", "CCCC", "ZZZZ"]


def test_sort_within_same_severity_is_alphabetical():
    rows = reconcile_positions(
        paper={"ZZZZ": Decimal("10"), "AAAA": Decimal("5")},
        broker={"ZZZZ": Decimal("10"), "AAAA": Decimal("5")},
    )
    assert [r.symbol for r in rows] == ["AAAA", "ZZZZ"]
