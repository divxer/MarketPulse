# Layer: pure
"""North-star NAV compute — PR3a of Charter top-3 priority #1.

Pure module. No DB, no network. Inputs are explicit; output is a frozen
NavSnapshot. See docs/superpowers/specs/2026-05-28-pr3a-north-star-snapshot-design.md.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

NORTH_STAR_WINDOW = 90  # trading days


@dataclass(frozen=True)
class OpenPosition:
    """Long-only paper-engine position. L14: quantity is Decimal at the typed
    boundary even though the current SQL column is INTEGER."""
    ticker: str
    quantity: Decimal


@dataclass(frozen=True)
class NavSnapshot:
    trading_date: date
    cash_balance: Decimal
    holdings_mtm: Decimal
    portfolio_nav: Decimal
    anchor_portfolio_nav: Decimal
    portfolio_index: Decimal
    spy_close: Decimal | None
    anchor_spy_close: Decimal | None
    spy_index: Decimal | None
    excess_return: Decimal | None
    trading_days_observed: int
    coverage_ratio: Decimal
    is_sufficient: bool
    unpriced_positions_count: int
    unpriced_tickers: tuple[str, ...]   # L15: dedup'd + sorted


def compute_nav_snapshot(
    *,
    trading_date: date,
    cash_balance: Decimal,
    open_positions: Iterable[OpenPosition],
    price_lookup: Callable[[str], Decimal | None],
    spy_close: Decimal | None,
    anchor_portfolio_nav: Decimal,
    anchor_spy_close: Decimal | None,
    trading_days_observed: int,
    window_size: int = NORTH_STAR_WINDOW,
) -> NavSnapshot:
    """Build one NavSnapshot.

    L6: a position with no price is OMITTED from holdings_mtm (NOT zeroed);
    its ticker is appended to unpriced_tickers and the count incremented.
    L15: unpriced_tickers is dedup'd and sorted.
    L16 (lazy SPY anchor) is enforced by the caller (runner); this function
    just consumes whatever anchor_spy_close is passed.
    """
    holdings_mtm = Decimal("0")
    unpriced: list[str] = []
    unpriced_count = 0
    for pos in open_positions:
        price = price_lookup(pos.ticker)
        if price is None:
            unpriced.append(pos.ticker)
            unpriced_count += 1
            continue
        holdings_mtm += pos.quantity * price

    portfolio_nav = cash_balance + holdings_mtm
    portfolio_index = portfolio_nav / anchor_portfolio_nav

    if spy_close is not None and anchor_spy_close is not None:
        spy_index: Decimal | None = spy_close / anchor_spy_close
        excess_return: Decimal | None = portfolio_index - spy_index
    else:
        spy_index = None
        excess_return = None

    coverage_ratio = min(
        Decimal(trading_days_observed) / Decimal(window_size),
        Decimal("1"),
    )
    is_sufficient = trading_days_observed >= window_size
    unpriced_tickers = tuple(sorted(set(unpriced)))

    return NavSnapshot(
        trading_date=trading_date,
        cash_balance=cash_balance,
        holdings_mtm=holdings_mtm,
        portfolio_nav=portfolio_nav,
        anchor_portfolio_nav=anchor_portfolio_nav,
        portfolio_index=portfolio_index,
        spy_close=spy_close,
        anchor_spy_close=anchor_spy_close,
        spy_index=spy_index,
        excess_return=excess_return,
        trading_days_observed=trading_days_observed,
        coverage_ratio=coverage_ratio,
        is_sufficient=is_sufficient,
        unpriced_positions_count=unpriced_count,
        unpriced_tickers=unpriced_tickers,
    )
