# Layer: pure
"""Shared frozen dataclasses for the PR3b charter review pipeline.

L18: aggregator and renderer both import from here. Single source of truth,
no circular imports. No runtime logic — types only.

See docs/superpowers/specs/2026-05-29-pr3b-charter-review-weekly-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class ReasonCount:
    reason: str
    count: int


@dataclass(frozen=True)
class WeekWindow:
    """Calendar week, Monday→Sunday inclusive UTC."""
    week_start: date
    week_end: date
    trading_days_observed: int


@dataclass(frozen=True)
class NorthStarWeek:
    """A north-star view over one week."""
    week: WeekWindow
    first_snapshot_date: date | None
    last_snapshot_date: date | None
    excess_return_end: Decimal | None
    portfolio_index_end: Decimal | None
    spy_index_end: Decimal | None
    coverage_ratio_end: Decimal | None
    is_sufficient_end: bool


@dataclass(frozen=True)
class DiagnosticWeek:
    """One diagnostic over one week. `value` is None when underlying source
    has zero usable observations. See spec L7 / L8 / L19 for semantics."""
    value: Decimal | int | None
    observations: int
    top_reasons: tuple[ReasonCount, ...]


@dataclass(frozen=True)
class DiagnosticsWeek:
    tick_success_rate: DiagnosticWeek
    order_rejection_rate: DiagnosticWeek
    paper_trade_count: DiagnosticWeek
    engine_invariant_errors: DiagnosticWeek


@dataclass(frozen=True)
class OperationalFloor:
    """L14: manifest_available=False →
    backup_status='missing', backup_is_stale=True,
    backup_last_at=None, backup_error=None."""
    backup_status: Literal["ok", "failed", "missing"]
    backup_is_stale: bool
    backup_last_at: str | None
    backup_error: str | None
    manifest_available: bool


@dataclass(frozen=True)
class SnapshotAppendix:
    """L15: filesystem-only appendix view. Money fields exposed here
    are NOT exposed via the PR3a JSON API."""
    trading_date: date | None
    cash_balance: Decimal | None
    holdings_mtm: Decimal | None
    portfolio_nav: Decimal | None
    unpriced_positions_count: int
    unpriced_tickers: tuple[str, ...]


@dataclass(frozen=True)
class CharterReviewPayload:
    generated_at: datetime
    week_ending: date
    this_week: WeekWindow
    prior_week: WeekWindow
    north_star_this: NorthStarWeek
    north_star_prior: NorthStarWeek
    diagnostics_this: DiagnosticsWeek
    diagnostics_prior: DiagnosticsWeek
    operational_floor: OperationalFloor
    appendix_snapshot: SnapshotAppendix
