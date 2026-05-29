# Layer: test
"""PR3a — compute_nav_snapshot tests.

Spec: docs/superpowers/specs/2026-05-28-pr3a-north-star-snapshot-design.md
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import pytest

from marketpulse.portfolio.north_star import (
    NORTH_STAR_WINDOW,
    OpenPosition,
    compute_nav_snapshot,
)


def _prices(mapping: dict[str, Decimal]):
    def lookup(ticker: str) -> Decimal | None:
        return mapping.get(ticker)
    return lookup


def test_compute_nav_basic_priced():
    snap = compute_nav_snapshot(
        trading_date=date(2026, 5, 28),
        cash_balance=Decimal("10000"),
        open_positions=[
            OpenPosition(ticker="AAPL", quantity=Decimal("10")),
            OpenPosition(ticker="GOOGL", quantity=Decimal("2")),
        ],
        price_lookup=_prices({"AAPL": Decimal("200"), "GOOGL": Decimal("100")}),
        spy_close=Decimal("500"),
        anchor_portfolio_nav=Decimal("12000"),  # 10000 + 10*200 + 2*100 = 12200
        anchor_spy_close=Decimal("475"),
        trading_days_observed=10,
    )
    assert snap.cash_balance == Decimal("10000")
    assert snap.holdings_mtm == Decimal("2200")
    assert snap.portfolio_nav == Decimal("12200")
    # portfolio_index = 12200 / 12000
    assert snap.portfolio_index == Decimal("12200") / Decimal("12000")
    # spy_index = 500 / 475
    assert snap.spy_index == Decimal("500") / Decimal("475")
    # excess_return = portfolio_index - spy_index
    assert snap.excess_return == snap.portfolio_index - snap.spy_index
    assert snap.unpriced_positions_count == 0
    assert snap.unpriced_tickers == ()


def test_compute_nav_first_snapshot_self_anchor():
    snap = compute_nav_snapshot(
        trading_date=date(2026, 5, 28),
        cash_balance=Decimal("100000"),
        open_positions=[],
        price_lookup=_prices({}),
        spy_close=Decimal("500"),
        anchor_portfolio_nav=Decimal("100000"),  # self-anchored
        anchor_spy_close=Decimal("500"),
        trading_days_observed=1,
    )
    assert snap.portfolio_nav == Decimal("100000")
    assert snap.portfolio_index == Decimal("1")
    assert snap.spy_index == Decimal("1")
    assert snap.excess_return == Decimal("0")


def test_nav_snapshot_is_frozen():
    snap = compute_nav_snapshot(
        trading_date=date(2026, 5, 28),
        cash_balance=Decimal("1000"),
        open_positions=[],
        price_lookup=_prices({}),
        spy_close=None,
        anchor_portfolio_nav=Decimal("1000"),
        anchor_spy_close=None,
        trading_days_observed=1,
    )
    with pytest.raises(FrozenInstanceError):
        snap.cash_balance = Decimal("9999")  # type: ignore[misc]


def test_north_star_window_constant():
    assert NORTH_STAR_WINDOW == 90


def test_compute_nav_unpriced_omitted():
    """L6: unpriced position is OMITTED, not zeroed."""
    snap = compute_nav_snapshot(
        trading_date=date(2026, 5, 28),
        cash_balance=Decimal("10000"),
        open_positions=[
            OpenPosition(ticker="AAPL", quantity=Decimal("10")),
            OpenPosition(ticker="XYZ", quantity=Decimal("5")),  # no price
        ],
        price_lookup=_prices({"AAPL": Decimal("200")}),
        spy_close=Decimal("500"),
        anchor_portfolio_nav=Decimal("12000"),
        anchor_spy_close=Decimal("500"),
        trading_days_observed=1,
    )
    # MTM reflects only the priced position.
    assert snap.holdings_mtm == Decimal("2000")
    assert snap.portfolio_nav == Decimal("12000")
    assert snap.unpriced_positions_count == 1
    assert snap.unpriced_tickers == ("XYZ",)


def test_compute_nav_all_unpriced():
    snap = compute_nav_snapshot(
        trading_date=date(2026, 5, 28),
        cash_balance=Decimal("10000"),
        open_positions=[
            OpenPosition(ticker="A", quantity=Decimal("1")),
            OpenPosition(ticker="B", quantity=Decimal("1")),
        ],
        price_lookup=_prices({}),
        spy_close=Decimal("500"),
        anchor_portfolio_nav=Decimal("10000"),
        anchor_spy_close=Decimal("500"),
        trading_days_observed=1,
    )
    assert snap.holdings_mtm == Decimal("0")
    assert snap.portfolio_nav == Decimal("10000")
    assert snap.unpriced_positions_count == 2
    assert snap.unpriced_tickers == ("A", "B")


def test_compute_nav_unpriced_tickers_dedup_sorted():
    """L15: 3 lots of same ticker → count=3, tuple has 1 unique element."""
    snap = compute_nav_snapshot(
        trading_date=date(2026, 5, 28),
        cash_balance=Decimal("10000"),
        open_positions=[
            OpenPosition(ticker="ZZZ", quantity=Decimal("1")),
            OpenPosition(ticker="ZZZ", quantity=Decimal("2")),
            OpenPosition(ticker="ZZZ", quantity=Decimal("3")),
            OpenPosition(ticker="AAA", quantity=Decimal("1")),
        ],
        price_lookup=_prices({}),
        spy_close=None,
        anchor_portfolio_nav=Decimal("10000"),
        anchor_spy_close=None,
        trading_days_observed=1,
    )
    assert snap.unpriced_positions_count == 4
    assert snap.unpriced_tickers == ("AAA", "ZZZ")


def test_compute_nav_spy_missing():
    snap = compute_nav_snapshot(
        trading_date=date(2026, 5, 28),
        cash_balance=Decimal("10000"),
        open_positions=[],
        price_lookup=_prices({}),
        spy_close=None,
        anchor_portfolio_nav=Decimal("10000"),
        anchor_spy_close=None,
        trading_days_observed=1,
    )
    assert snap.portfolio_nav == Decimal("10000")
    assert snap.portfolio_index == Decimal("1")
    assert snap.spy_index is None
    assert snap.excess_return is None


def test_coverage_ratio_clamped():
    snap = compute_nav_snapshot(
        trading_date=date(2026, 5, 28),
        cash_balance=Decimal("1"),
        open_positions=[],
        price_lookup=_prices({}),
        spy_close=None,
        anchor_portfolio_nav=Decimal("1"),
        anchor_spy_close=None,
        trading_days_observed=120,  # > window
    )
    assert snap.coverage_ratio == Decimal("1")


def test_is_sufficient_threshold():
    common = dict(
        trading_date=date(2026, 5, 28),
        cash_balance=Decimal("1"),
        open_positions=[],
        price_lookup=_prices({}),
        spy_close=None,
        anchor_portfolio_nav=Decimal("1"),
        anchor_spy_close=None,
    )
    assert compute_nav_snapshot(**common, trading_days_observed=89).is_sufficient is False
    assert compute_nav_snapshot(**common, trading_days_observed=90).is_sufficient is True
