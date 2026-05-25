"""Phase 7c reconciliation DTOs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class DiffType(StrEnum):
    MATCHED = "matched"
    MISSING_IN_BROKER = "missing_in_broker"
    MISSING_IN_PAPER = "missing_in_paper"
    QUANTITY_MISMATCH = "quantity_mismatch"
    SIDE_MISMATCH = "side_mismatch"


class Severity(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    GRAY = "gray"


_SEVERITY_RANK: dict[DiffType, int] = {
    DiffType.SIDE_MISMATCH: 0,
    DiffType.MISSING_IN_BROKER: 1,
    DiffType.QUANTITY_MISMATCH: 2,
    DiffType.MISSING_IN_PAPER: 3,
    DiffType.MATCHED: 4,
}


@dataclass(frozen=True)
class DiffRow:
    symbol: str
    diff_type: DiffType
    paper_qty: Decimal | None
    broker_qty: Decimal | None
    delta: Decimal | None
    is_red: bool


@dataclass(frozen=True)
class ReconciliationDashboard:
    """Single bundle returned by load_reconciliation_dashboard()."""

    rows: tuple[DiffRow, ...]
    severity: Severity
    broker_account_id: str | None
    broker_completed_at: datetime | None
    broker_reference_code: str | None
    broker_is_stale: bool
    no_broker_data: bool
    account_ambiguous: bool
    paper_open_position_count: int
    recent_failed_run_descriptions: tuple[str, ...]
    matched_count: int
    missing_in_broker_count: int
    missing_in_paper_count: int
    quantity_mismatch_count: int
    side_mismatch_count: int
