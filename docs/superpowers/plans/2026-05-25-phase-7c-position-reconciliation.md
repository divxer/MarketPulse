# Phase 7c Position Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `/lab/reconcile` — an on-demand read-only diff view that compares current open `paper_position` rows against the latest completed broker truth snapshot.

**Architecture:** Pure-function diff core (`marketpulse/reconcile/diffing.py`) over already-normalized symbol→qty maps; query model (`query_models.py`) does the DB read + normalization + aggregation; FastAPI route (`routes/reconcile.py`) renders a Jinja template. No new tables, no Alembic migration, no scheduler hook.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, FastAPI, Jinja2, pydantic Settings. Tests with pytest + SQLite in-memory.

**Spec:** `docs/superpowers/specs/2026-05-25-phase-7c-position-reconciliation-design.md` (14 locks + 12 refinements; spec is authoritative if this plan conflicts).

---

## File Structure

**Create:**
- `marketpulse/reconcile/__init__.py`
- `marketpulse/reconcile/types.py` — `DiffType` enum, `DiffRow`, `ReconciliationDashboard`, `Severity` enum, `_SEVERITY_RANK`
- `marketpulse/reconcile/diffing.py` — `reconcile_positions()` pure function
- `marketpulse/reconcile/query_models.py` — `load_reconciliation_dashboard()` + `compute_hero_severity()`
- `marketpulse/web/routes/reconcile.py` — GET /lab/reconcile
- `marketpulse/web/templates/lab_reconcile.html`
- `marketpulse/web/templates/partials/reconcile_hero.html`
- `marketpulse/web/templates/partials/reconcile_summary_cards.html`
- `marketpulse/web/templates/partials/reconcile_diff_table.html`
- `tests/reconcile/__init__.py`
- `tests/reconcile/test_diffing.py` — pure logic, Layer: unit
- `tests/reconcile/test_query_models.py` — DB integration, Layer: stateful
- `tests/web/test_lab_reconcile_route.py` — route, Layer: route
- `tests/architecture/test_lab_reconcile_isolation.py` — Layer: architecture

**Modify:**
- `marketpulse/web/main.py` — register `reconcile` router
- `marketpulse/web/templates/base.html` — add `/lab/reconcile` nav link
- `marketpulse/web/static/css/app.css` — minimal additions; reuse `mp-lab-ops` / `mp-lab-kpis` from broker viewer

---

## Task 1: Types & enums

**Files:**
- Create: `marketpulse/reconcile/__init__.py`
- Create: `marketpulse/reconcile/types.py`
- Create: `tests/reconcile/__init__.py`
- Create: `tests/reconcile/test_types.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/reconcile/test_types.py
"""Phase 7c reconciliation — type contracts."""
# Layer: unit
from __future__ import annotations

from decimal import Decimal

from marketpulse.reconcile.types import (
    DiffRow,
    DiffType,
    Severity,
    _SEVERITY_RANK,
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
    # frozen=True means attribute writes raise FrozenInstanceError
    with pytest.raises(dataclasses.FrozenInstanceError):
        row.symbol = "MSFT"  # type: ignore[misc]


def test_severity_enum_values():
    assert Severity.GREEN.value == "green"
    assert Severity.YELLOW.value == "yellow"
    assert Severity.RED.value == "red"
    assert Severity.GRAY.value == "gray"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/reconcile/test_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'marketpulse.reconcile'`

- [ ] **Step 3: Write minimal implementation**

```python
# marketpulse/reconcile/__init__.py
"""Phase 7c — broker-vs-paper position reconciliation.

Pure read-only computation: no DB writes, no migration, no scheduler hook.
Architecture guard at tests/architecture/test_lab_reconcile_isolation.py
enforces the read-only boundary.
"""
```

```python
# marketpulse/reconcile/types.py
"""Phase 7c reconciliation DTOs.

Per spec (2026-05-25):
- DiffRow.delta is None whenever either paper_qty or broker_qty is None.
- _SEVERITY_RANK drives sort order: SIDE_MISMATCH first, MATCHED last.
"""
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
    GREEN = "green"   # everything matched
    YELLOW = "yellow"  # at least one non-MATCHED, no red triggers
    RED = "red"       # SIDE_MISMATCH, MISSING_IN_BROKER w/ paper>0, or >=3 mismatches
    GRAY = "gray"     # cannot reconcile (no broker truth / ambiguous account)


# Sort rank: lower = displayed first.
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
    delta: Decimal | None  # paper - broker; None if either side missing
    is_red: bool


@dataclass(frozen=True)
class ReconciliationDashboard:
    """Single bundle returned by load_reconciliation_dashboard().

    The route + templates consume this dataclass; they MUST NOT touch the
    SQLAlchemy session directly. That keeps the architecture guard
    (tests/architecture/test_lab_reconcile_isolation.py) tight.
    """
    rows: tuple[DiffRow, ...]
    severity: Severity

    # Broker side
    broker_account_id: str | None
    broker_completed_at: datetime | None
    broker_reference_code: str | None
    broker_is_stale: bool  # broker_completed_at older than 24h

    # State flags
    no_broker_data: bool
    account_ambiguous: bool

    # Paper side metadata (per spec — paper-tick time is intentionally absent)
    paper_open_position_count: int

    # Recent failed runs (for empty-state diagnostics; only populated when
    # no_broker_data is True)
    recent_failed_run_descriptions: tuple[str, ...]

    # Summary counts (precomputed so templates don't re-walk rows)
    matched_count: int
    missing_in_broker_count: int
    missing_in_paper_count: int
    quantity_mismatch_count: int
    side_mismatch_count: int
```

Fix the test — my `dataclasses.is_frozen := True` line is broken pseudocode. Replace test body:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/reconcile/test_types.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add marketpulse/reconcile/__init__.py marketpulse/reconcile/types.py \
        tests/reconcile/__init__.py tests/reconcile/test_types.py
git commit -m "feat(7c): T1 — reconciliation DTOs and severity enums"
```

---

## Task 2: Pure diff function

**Files:**
- Create: `marketpulse/reconcile/diffing.py`
- Create: `tests/reconcile/test_diffing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/reconcile/test_diffing.py
"""Phase 7c — pure diff logic.

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
    assert r.delta is None  # spec: delta=None when either side missing
    assert r.is_red is True


def test_only_paper_zero_qty_is_not_red():
    # Defensive: open paper_position theoretically can't be 0, but guard
    # the branch anyway.
    rows = reconcile_positions({"AAPL": Decimal("0")}, {})
    assert rows[0].is_red is False


def test_matched_exact():
    rows = reconcile_positions(
        {"AAPL": Decimal("100")}, {"AAPL": Decimal("100")}
    )
    r = rows[0]
    assert r.diff_type == DiffType.MATCHED
    assert r.delta == Decimal("0")


def test_matched_with_fractional_remnant():
    # Spec L14: fractional diff < 1 share is MATCHED; delta still recorded.
    rows = reconcile_positions(
        {"AAPL": Decimal("100")}, {"AAPL": Decimal("100.34")}
    )
    r = rows[0]
    assert r.diff_type == DiffType.MATCHED
    assert r.delta == Decimal("-0.34")


def test_quantity_mismatch_boundary_inclusive_at_one_share():
    # Spec L13: abs(diff) >= 1 triggers MISMATCH (inclusive).
    rows = reconcile_positions(
        {"AAPL": Decimal("100")}, {"AAPL": Decimal("99")}
    )
    assert rows[0].diff_type == DiffType.QUANTITY_MISMATCH


def test_quantity_mismatch_just_under_threshold_is_matched():
    rows = reconcile_positions(
        {"AAPL": Decimal("100")}, {"AAPL": Decimal("99.01")}
    )
    assert rows[0].diff_type == DiffType.MATCHED
    assert rows[0].delta == Decimal("0.99")


def test_side_mismatch_long_paper_short_broker():
    rows = reconcile_positions(
        {"AAPL": Decimal("10")}, {"AAPL": Decimal("-5")}
    )
    r = rows[0]
    assert r.diff_type == DiffType.SIDE_MISMATCH
    assert r.is_red is True
    assert r.delta == Decimal("15")  # 10 - (-5)


def test_side_mismatch_short_paper_long_broker():
    rows = reconcile_positions(
        {"AAPL": Decimal("-10")}, {"AAPL": Decimal("5")}
    )
    assert rows[0].diff_type == DiffType.SIDE_MISMATCH


def test_zero_on_either_side_is_not_side_mismatch():
    # p * b < 0 only when both are non-zero with opposite signs. Zero
    # multiplied by anything is 0, not negative.
    rows = reconcile_positions(
        {"AAPL": Decimal("0")}, {"AAPL": Decimal("5")}
    )
    # 0 vs 5: |diff|=5 >= 1 → QUANTITY_MISMATCH (not SIDE_MISMATCH)
    assert rows[0].diff_type == DiffType.QUANTITY_MISMATCH


def test_sort_order_severity_then_alphabetical():
    rows = reconcile_positions(
        paper={
            "ZZZZ": Decimal("100"),  # MATCHED (broker also has 100)
            "AAAA": Decimal("50"),   # MISSING_IN_BROKER (no broker entry)
            "MMMM": Decimal("10"),   # SIDE_MISMATCH (broker has -1)
            "BBBB": Decimal("100"),  # QUANTITY_MISMATCH (broker has 50)
        },
        broker={
            "ZZZZ": Decimal("100"),
            "MMMM": Decimal("-1"),
            "BBBB": Decimal("50"),
            "CCCC": Decimal("99"),   # MISSING_IN_PAPER
        },
    )
    symbols_in_order = [r.symbol for r in rows]
    # Expected order:
    #   SIDE_MISMATCH (rank 0):    MMMM
    #   MISSING_IN_BROKER (rank 1): AAAA
    #   QUANTITY_MISMATCH (rank 2): BBBB
    #   MISSING_IN_PAPER (rank 3):  CCCC
    #   MATCHED (rank 4):           ZZZZ
    assert symbols_in_order == ["MMMM", "AAAA", "BBBB", "CCCC", "ZZZZ"]


def test_sort_within_same_severity_is_alphabetical():
    rows = reconcile_positions(
        paper={"ZZZZ": Decimal("10"), "AAAA": Decimal("5")},
        broker={"ZZZZ": Decimal("10"), "AAAA": Decimal("5")},
    )
    # Both MATCHED, alphabetical within rank.
    assert [r.symbol for r in rows] == ["AAAA", "ZZZZ"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/reconcile/test_diffing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'marketpulse.reconcile.diffing'`

- [ ] **Step 3: Write minimal implementation**

```python
# marketpulse/reconcile/diffing.py
"""Phase 7c — pure position reconciliation.

Input contract (caller responsibility):
- Symbol keys are normalized via .upper().strip().
- Duplicate symbols on either side are aggregated by sum(qty) before
  this function is called.

This function never touches a session, a config object, or the clock.
It is a deterministic Mapping→list[DiffRow] transformation.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from marketpulse.reconcile.types import DiffRow, DiffType, _SEVERITY_RANK


def reconcile_positions(
    paper: Mapping[str, Decimal],
    broker: Mapping[str, Decimal],
) -> list[DiffRow]:
    rows: list[DiffRow] = []
    for symbol in sorted(paper.keys() | broker.keys()):
        p = paper.get(symbol)
        b = broker.get(symbol)
        if p is None:
            # Only broker has it.
            assert b is not None  # union membership invariant
            rows.append(
                DiffRow(
                    symbol=symbol,
                    diff_type=DiffType.MISSING_IN_PAPER,
                    paper_qty=None,
                    broker_qty=b,
                    delta=None,
                    is_red=False,
                )
            )
        elif b is None:
            # Only paper has it. Red iff paper has actual exposure.
            rows.append(
                DiffRow(
                    symbol=symbol,
                    diff_type=DiffType.MISSING_IN_BROKER,
                    paper_qty=p,
                    broker_qty=None,
                    delta=None,
                    is_red=(p != 0),
                )
            )
        elif p * b < 0:
            # Both non-zero, opposite signs.
            rows.append(
                DiffRow(
                    symbol=symbol,
                    diff_type=DiffType.SIDE_MISMATCH,
                    paper_qty=p,
                    broker_qty=b,
                    delta=p - b,
                    is_red=True,
                )
            )
        elif abs(p - b) >= 1:
            rows.append(
                DiffRow(
                    symbol=symbol,
                    diff_type=DiffType.QUANTITY_MISMATCH,
                    paper_qty=p,
                    broker_qty=b,
                    delta=p - b,
                    is_red=False,
                )
            )
        else:
            rows.append(
                DiffRow(
                    symbol=symbol,
                    diff_type=DiffType.MATCHED,
                    paper_qty=p,
                    broker_qty=b,
                    delta=p - b,
                    is_red=False,
                )
            )
    rows.sort(key=lambda r: (_SEVERITY_RANK[r.diff_type], r.symbol))
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/reconcile/test_diffing.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add marketpulse/reconcile/diffing.py tests/reconcile/test_diffing.py
git commit -m "feat(7c): T2 — pure reconcile_positions() with severity sort"
```

---

## Task 3: Query model — account picking + aggregation + stale

**Files:**
- Create: `marketpulse/reconcile/query_models.py`
- Create: `tests/reconcile/test_query_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/reconcile/test_query_models.py
"""Phase 7c — query model integration.

Covers account picking (with and without settings override), the
multi-account ambiguity branch, symbol normalization, paper+broker
aggregation, and stale-snapshot detection.
"""
# Layer: stateful
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from marketpulse.db.base import Base
from marketpulse.db.models import (
    BrokerPositionSnapshot,
    BrokerSyncRun,
    PaperOrder,
    PaperPosition,
)
from marketpulse.reconcile.query_models import load_reconciliation_dashboard
from marketpulse.reconcile.types import DiffType, Severity


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _make_completed_run(
    db: Session,
    *,
    started_at: datetime,
    account_id: str = "DU123",
    reference_code: str = "REF-1",
) -> BrokerSyncRun:
    run = BrokerSyncRun(
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=10),
        broker="IBKR",
        broker_environment="paper",
        account_id=account_id,
        status="completed",
        context={"reference_code": reference_code},
    )
    db.add(run)
    db.flush()
    return run


def _make_failed_run(db: Session, *, started_at: datetime, account_id: str | None) -> BrokerSyncRun:
    run = BrokerSyncRun(
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=5),
        broker="IBKR",
        broker_environment="paper",
        account_id=account_id,
        status="failed",
        error_type="FlexHttpError",
        error_message="503",
        context={},
    )
    db.add(run)
    db.flush()
    return run


def _add_broker_position(
    db: Session, run: BrokerSyncRun, *, symbol: str, quantity: Decimal,
) -> None:
    db.add(BrokerPositionSnapshot(
        sync_run_id=run.id,
        account_id=run.account_id or "DU123",
        broker_environment=run.broker_environment,
        captured_at=run.completed_at or run.started_at,
        symbol=symbol,
        asset_class="STK",
        quantity=quantity,
    ))


def _add_paper_position(
    db: Session, *, ticker: str, quantity: int,
    closed: bool = False, idempotency_suffix: str = "",
) -> None:
    """Add an open (or closed) PaperPosition with the bare minimum
    PaperOrder backing it.
    """
    now = datetime.now(UTC)
    order = PaperOrder(
        idempotency_key=f"k_{ticker}_{quantity}{idempotency_suffix}",
        allocation_run_id="run-1",
        strategy="general",
        ticker=ticker,
        quantity=quantity,
        event_time=now,
        allocation_date=date(2026, 5, 25),
        horizon_date=date(2026, 6, 1),
        placed_at=now,
        event_price=Decimal("100"),
        status="FILLED",
        strategy_version="v1",
        allocator_version="v1",
        execution_engine_version="v1",
        weight=1.0,
    )
    db.add(order)
    db.flush()
    db.add(PaperPosition(
        order_id=order.id,
        entry_fill_id=1,
        exit_fill_id=99 if closed else None,
        strategy="general",
        ticker=ticker,
        quantity=quantity,
        # The following columns are non-nullable per the PaperPosition
        # ORM — confirmed against marketpulse/db/models.py before plan
        # was finalized. Fixture must supply all of them.
        entry_price=Decimal("100"),
        entry_date=date(2026, 5, 25),
        horizon_date=date(2026, 6, 1),
        status="CLOSED" if closed else "OPEN",
        opened_at=now,
    ))
    db.flush()


def test_empty_db_yields_no_broker_data_gray():
    db = _session()
    dash = load_reconciliation_dashboard(db)
    assert dash.no_broker_data is True
    assert dash.account_ambiguous is False
    assert dash.severity == Severity.GRAY
    assert dash.rows == ()


def test_only_failed_runs_yields_no_broker_data_not_ambiguous():
    # Spec: failed runs do NOT contribute to account ambiguity.
    db = _session()
    base = datetime(2026, 5, 25, 12, tzinfo=UTC)
    _make_failed_run(db, started_at=base, account_id="DU-A")
    _make_failed_run(db, started_at=base + timedelta(hours=1), account_id="DU-B")
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.no_broker_data is True
    assert dash.account_ambiguous is False
    assert dash.severity == Severity.GRAY


def test_multi_account_completed_runs_yields_ambiguous(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "")  # ensure settings empty
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    db = _session()
    base = datetime(2026, 5, 25, 12, tzinfo=UTC)
    _make_completed_run(db, started_at=base, account_id="DU-A")
    _make_completed_run(db, started_at=base + timedelta(hours=1), account_id="DU-B")
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.account_ambiguous is True
    assert dash.severity == Severity.GRAY
    get_settings.cache_clear()


def test_settings_override_picks_account_in_multi_account_history(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-B")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    db = _session()
    base = datetime(2026, 5, 25, 12, tzinfo=UTC)
    _make_completed_run(db, started_at=base, account_id="DU-A")
    run_b = _make_completed_run(db, started_at=base + timedelta(hours=1), account_id="DU-B")
    _add_broker_position(db, run_b, symbol="AAPL", quantity=Decimal("100"))
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.account_ambiguous is False
    assert dash.broker_account_id == "DU-B"
    assert len(dash.rows) == 1
    assert dash.rows[0].symbol == "AAPL"
    get_settings.cache_clear()


def test_all_matched_yields_green(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    db = _session()
    base = datetime.now(UTC) - timedelta(hours=1)
    run = _make_completed_run(db, started_at=base, account_id="DU-A")
    _add_broker_position(db, run, symbol="AAPL", quantity=Decimal("100"))
    _add_paper_position(db, ticker="AAPL", quantity=100)
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.severity == Severity.GREEN
    assert dash.matched_count == 1
    assert dash.quantity_mismatch_count == 0
    assert len(dash.rows) == 1
    assert dash.rows[0].diff_type == DiffType.MATCHED
    get_settings.cache_clear()


def test_symbol_normalization_lowercase_vs_uppercase(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    db = _session()
    base = datetime.now(UTC) - timedelta(hours=1)
    run = _make_completed_run(db, started_at=base, account_id="DU-A")
    _add_broker_position(db, run, symbol="aapl", quantity=Decimal("100"))
    _add_paper_position(db, ticker="AAPL", quantity=100)
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.matched_count == 1
    assert dash.rows[0].symbol == "AAPL"
    get_settings.cache_clear()


def test_broker_side_aggregation(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    db = _session()
    base = datetime.now(UTC) - timedelta(hours=1)
    run = _make_completed_run(db, started_at=base, account_id="DU-A")
    _add_broker_position(db, run, symbol="AAPL", quantity=Decimal("50"))
    _add_broker_position(db, run, symbol="AAPL", quantity=Decimal("50"))
    _add_paper_position(db, ticker="AAPL", quantity=100)
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.matched_count == 1
    assert dash.rows[0].broker_qty == Decimal("100")
    get_settings.cache_clear()


def test_paper_side_aggregation(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    db = _session()
    base = datetime.now(UTC) - timedelta(hours=1)
    run = _make_completed_run(db, started_at=base, account_id="DU-A")
    _add_broker_position(db, run, symbol="AAPL", quantity=Decimal("100"))
    _add_paper_position(db, ticker="AAPL", quantity=50, idempotency_suffix="_lot1")
    _add_paper_position(db, ticker="AAPL", quantity=50, idempotency_suffix="_lot2")
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.matched_count == 1
    assert dash.rows[0].paper_qty == Decimal("100")
    get_settings.cache_clear()


def test_closed_paper_positions_excluded(monkeypatch):
    """Per spec L10: only exit_fill_id IS NULL participates."""
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    db = _session()
    base = datetime.now(UTC) - timedelta(hours=1)
    run = _make_completed_run(db, started_at=base, account_id="DU-A")
    _add_broker_position(db, run, symbol="MSFT", quantity=Decimal("10"))
    _add_paper_position(db, ticker="AAPL", quantity=100, closed=True)  # closed, ignored
    db.commit()

    dash = load_reconciliation_dashboard(db)
    # paper side is empty after filtering closed → MSFT is MISSING_IN_PAPER only
    symbols = {r.symbol: r.diff_type for r in dash.rows}
    assert symbols == {"MSFT": DiffType.MISSING_IN_PAPER}
    get_settings.cache_clear()


def test_stale_broker_snapshot_deterministic(monkeypatch):
    """Use the now= injection point for deterministic stale-boundary
    testing — avoids race against the wall clock."""
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    db = _session()
    run_completed = datetime(2026, 5, 24, 12, tzinfo=UTC)
    run = _make_completed_run(db, started_at=run_completed, account_id="DU-A")
    _add_broker_position(db, run, symbol="AAPL", quantity=Decimal("100"))
    _add_paper_position(db, ticker="AAPL", quantity=100)
    db.commit()

    # 23h59m after completed_at → NOT stale (boundary just under 24h)
    fresh_now = run.completed_at + timedelta(hours=23, minutes=59)
    fresh = load_reconciliation_dashboard(db, now=fresh_now)
    assert fresh.broker_is_stale is False
    assert fresh.severity == Severity.GREEN  # diff unaffected

    # 24h01m after completed_at → stale
    stale_now = run.completed_at + timedelta(hours=24, minutes=1)
    stale = load_reconciliation_dashboard(db, now=stale_now)
    assert stale.broker_is_stale is True
    assert stale.severity == Severity.GREEN  # stale does NOT bump severity
    get_settings.cache_clear()


def test_latest_completed_run_picked_when_multiple_on_same_account(monkeypatch):
    """If the same account has N completed runs, the most recent wins."""
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    db = _session()
    base = datetime.now(UTC) - timedelta(hours=10)
    old_run = _make_completed_run(
        db, started_at=base, account_id="DU-A", reference_code="REF-OLD",
    )
    _add_broker_position(db, old_run, symbol="AAPL", quantity=Decimal("50"))  # stale
    new_run = _make_completed_run(
        db, started_at=base + timedelta(hours=1), account_id="DU-A",
        reference_code="REF-NEW",
    )
    _add_broker_position(db, new_run, symbol="AAPL", quantity=Decimal("100"))  # current
    _add_paper_position(db, ticker="AAPL", quantity=100)
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.broker_reference_code == "REF-NEW"  # picked the latest
    assert dash.matched_count == 1  # against the newer 100, not the older 50
    get_settings.cache_clear()


def test_hero_severity_red_on_side_mismatch(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    db = _session()
    base = datetime.now(UTC) - timedelta(hours=1)
    run = _make_completed_run(db, started_at=base, account_id="DU-A")
    _add_broker_position(db, run, symbol="AAPL", quantity=Decimal("-10"))
    _add_paper_position(db, ticker="AAPL", quantity=10)
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.severity == Severity.RED
    assert dash.side_mismatch_count == 1
    get_settings.cache_clear()


def test_hero_severity_red_on_missing_in_broker_with_paper_qty(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    db = _session()
    base = datetime.now(UTC) - timedelta(hours=1)
    run = _make_completed_run(db, started_at=base, account_id="DU-A")
    _add_paper_position(db, ticker="AAPL", quantity=10)
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.severity == Severity.RED  # paper exposure but broker silent
    assert dash.missing_in_broker_count == 1
    get_settings.cache_clear()


def test_hero_severity_yellow_on_single_quantity_mismatch(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    db = _session()
    base = datetime.now(UTC) - timedelta(hours=1)
    run = _make_completed_run(db, started_at=base, account_id="DU-A")
    _add_broker_position(db, run, symbol="AAPL", quantity=Decimal("50"))
    _add_paper_position(db, ticker="AAPL", quantity=100)
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.severity == Severity.YELLOW
    assert dash.quantity_mismatch_count == 1
    get_settings.cache_clear()


def test_hero_severity_red_on_three_plus_mismatches(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    db = _session()
    base = datetime.now(UTC) - timedelta(hours=1)
    run = _make_completed_run(db, started_at=base, account_id="DU-A")
    _add_broker_position(db, run, symbol="AAPL", quantity=Decimal("50"))
    _add_broker_position(db, run, symbol="MSFT", quantity=Decimal("50"))
    _add_broker_position(db, run, symbol="GOOG", quantity=Decimal("50"))
    _add_paper_position(db, ticker="AAPL", quantity=100)
    _add_paper_position(db, ticker="MSFT", quantity=100)
    _add_paper_position(db, ticker="GOOG", quantity=100)
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.severity == Severity.RED  # 3 mismatches triggers red
    assert dash.quantity_mismatch_count == 3
    get_settings.cache_clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/reconcile/test_query_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'marketpulse.reconcile.query_models'`

- [ ] **Step 3: Write minimal implementation**

```python
# marketpulse/reconcile/query_models.py
"""Phase 7c — DB-backed dashboard assembly.

Allowed reads (per spec architecture guard):
  PaperPosition
  BrokerSyncRun
  BrokerPositionSnapshot

This module MUST NOT call session.add / flush / commit on any model.
Failure to respect that boundary trips
tests/architecture/test_lab_reconcile_isolation.py.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from marketpulse.config import get_settings
from marketpulse.db.models import (
    BrokerPositionSnapshot,
    BrokerSyncRun,
    PaperPosition,
)
from marketpulse.reconcile.diffing import reconcile_positions
from marketpulse.reconcile.types import (
    DiffType,
    ReconciliationDashboard,
    Severity,
)

_STALE_THRESHOLD = timedelta(hours=24)
_RED_MISMATCH_COUNT_THRESHOLD = 3


def _empty_dashboard(
    *,
    no_broker_data: bool = False,
    account_ambiguous: bool = False,
    paper_open_count: int = 0,
    recent_failed: tuple[str, ...] = (),
) -> ReconciliationDashboard:
    return ReconciliationDashboard(
        rows=(),
        severity=Severity.GRAY,
        broker_account_id=None,
        broker_completed_at=None,
        broker_reference_code=None,
        broker_is_stale=False,
        no_broker_data=no_broker_data,
        account_ambiguous=account_ambiguous,
        paper_open_position_count=paper_open_count,
        recent_failed_run_descriptions=recent_failed,
        matched_count=0,
        missing_in_broker_count=0,
        missing_in_paper_count=0,
        quantity_mismatch_count=0,
        side_mismatch_count=0,
    )


def _pick_account(db: Session) -> tuple[str | None, bool]:
    """Returns (chosen_account_id, ambiguous_flag).

    Settings override wins. Otherwise: distinct account_ids among
    completed runs only. >1 → ambiguous. 0 → (None, False), caller
    falls through to no_broker_data.
    """
    settings = get_settings()
    configured = (settings.ibkr_account_id or "").strip()
    if configured:
        return configured, False

    distinct_accounts = db.execute(
        select(distinct(BrokerSyncRun.account_id))
        .where(BrokerSyncRun.status == "completed")
    ).scalars().all()
    accounts = [a for a in distinct_accounts if a]
    if not accounts:
        return None, False
    if len(accounts) > 1:
        return None, True
    return accounts[0], False


def _recent_failed_descriptions(db: Session, limit: int = 3) -> tuple[str, ...]:
    rows = db.execute(
        select(BrokerSyncRun)
        .where(BrokerSyncRun.status == "failed")
        .order_by(BrokerSyncRun.started_at.desc())
        .limit(limit)
    ).scalars().all()
    parts: list[str] = []
    for r in rows:
        label = r.error_type or "failed"
        if r.error_message:
            # Truncate to keep the line readable on the empty-state card.
            msg = r.error_message[:80].replace("\n", " ")
            label = f"{label} — {msg}"
        parts.append(f"#{r.id} {r.started_at.strftime('%Y-%m-%d %H:%M')} {label}")
    return tuple(parts)


def _paper_map(db: Session) -> tuple[dict[str, Decimal], int]:
    """Build aggregated paper open-position map and return the row count
    (pre-aggregation) for hero display."""
    rows = db.execute(
        select(PaperPosition).where(PaperPosition.exit_fill_id.is_(None))
    ).scalars().all()
    paper: dict[str, Decimal] = {}
    for row in rows:
        key = (row.ticker or "").upper().strip()
        if not key:  # defensive: skip rows with empty ticker
            continue
        paper[key] = paper.get(key, Decimal(0)) + Decimal(row.quantity)
    return paper, len(rows)


def _broker_map(db: Session, run_id: int) -> dict[str, Decimal]:
    rows = db.execute(
        select(BrokerPositionSnapshot)
        .where(BrokerPositionSnapshot.sync_run_id == run_id)
    ).scalars().all()
    broker: dict[str, Decimal] = {}
    for row in rows:
        key = (row.symbol or "").upper().strip()
        if not key:  # defensive: skip rows with empty symbol
            continue
        broker[key] = broker.get(key, Decimal(0)) + row.quantity
    return broker


def _compute_severity(rows: list, *, no_broker: bool, ambiguous: bool) -> Severity:
    if no_broker or ambiguous:
        return Severity.GRAY
    non_matched = [r for r in rows if r.diff_type != DiffType.MATCHED]
    if not non_matched:
        return Severity.GREEN
    has_red = any(r.is_red for r in non_matched)
    if has_red or len(non_matched) >= _RED_MISMATCH_COUNT_THRESHOLD:
        return Severity.RED
    return Severity.YELLOW


def load_reconciliation_dashboard(
    db: Session, *, now: datetime | None = None,
) -> ReconciliationDashboard:
    """Build the full dashboard payload.

    The optional ``now`` parameter is for deterministic stale-flag
    testing — pass an explicit timestamp instead of relying on
    datetime.now(UTC) so the threshold boundary is reproducible.
    """
    clock_now = now if now is not None else datetime.now(UTC)
    account, ambiguous = _pick_account(db)

    if ambiguous:
        paper_map, paper_count = _paper_map(db)
        return _empty_dashboard(
            account_ambiguous=True, paper_open_count=paper_count,
        )

    if account is None:
        # No completed runs at all.
        paper_map, paper_count = _paper_map(db)
        return _empty_dashboard(
            no_broker_data=True,
            paper_open_count=paper_count,
            recent_failed=_recent_failed_descriptions(db),
        )

    latest_run = db.execute(
        select(BrokerSyncRun)
        .where(BrokerSyncRun.status == "completed")
        .where(BrokerSyncRun.account_id == account)
        .order_by(BrokerSyncRun.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if latest_run is None:
        paper_map, paper_count = _paper_map(db)
        return _empty_dashboard(
            no_broker_data=True,
            paper_open_count=paper_count,
            recent_failed=_recent_failed_descriptions(db),
        )

    paper_map, paper_count = _paper_map(db)
    broker_map = _broker_map(db, latest_run.id)
    rows = reconcile_positions(paper_map, broker_map)

    is_stale = (
        latest_run.completed_at is not None
        and (clock_now - latest_run.completed_at) > _STALE_THRESHOLD
    )

    severity = _compute_severity(rows, no_broker=False, ambiguous=False)

    counts: dict[DiffType, int] = {dt: 0 for dt in DiffType}
    for r in rows:
        counts[r.diff_type] += 1

    ctx = latest_run.context or {}
    ref = ctx.get("reference_code") if isinstance(ctx, dict) else None

    return ReconciliationDashboard(
        rows=tuple(rows),
        severity=severity,
        broker_account_id=latest_run.account_id,
        broker_completed_at=latest_run.completed_at,
        broker_reference_code=ref if isinstance(ref, str) else None,
        broker_is_stale=is_stale,
        no_broker_data=False,
        account_ambiguous=False,
        paper_open_position_count=paper_count,
        recent_failed_run_descriptions=(),
        matched_count=counts[DiffType.MATCHED],
        missing_in_broker_count=counts[DiffType.MISSING_IN_BROKER],
        missing_in_paper_count=counts[DiffType.MISSING_IN_PAPER],
        quantity_mismatch_count=counts[DiffType.QUANTITY_MISMATCH],
        side_mismatch_count=counts[DiffType.SIDE_MISMATCH],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/reconcile/test_query_models.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add marketpulse/reconcile/query_models.py tests/reconcile/test_query_models.py
git commit -m "feat(7c): T3 — load_reconciliation_dashboard with account/aggregation/stale"
```

---

## Task 4: FastAPI route

**Files:**
- Create: `marketpulse/web/routes/reconcile.py`
- Modify: `marketpulse/web/main.py` (router registration)
- Create: `tests/web/test_lab_reconcile_route.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_lab_reconcile_route.py
"""Phase 7c — /lab/reconcile route."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from marketpulse.auth.password import hash_password
from marketpulse.db import base as db_base
from marketpulse.db.models import (
    BrokerPositionSnapshot,
    BrokerSyncRun,
    PaperOrder,
    PaperPosition,
)


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _seed_completed_with_position(account_id: str = "DU-A"):
    gen = db_base.session_scope()
    db = next(gen)
    try:
        base = datetime.now(UTC) - timedelta(hours=1)
        run = BrokerSyncRun(
            started_at=base, completed_at=base + timedelta(seconds=10),
            broker="IBKR", broker_environment="paper",
            account_id=account_id, status="completed",
            context={"reference_code": "REF-OK"},
        )
        db.add(run); db.flush()
        db.add(BrokerPositionSnapshot(
            sync_run_id=run.id, account_id=account_id,
            broker_environment="paper", captured_at=run.completed_at,
            symbol="AAPL", asset_class="STK", quantity=Decimal("100"),
        ))
        order = PaperOrder(
            idempotency_key="t4-a", allocation_run_id="r",
            strategy="general", ticker="AAPL", quantity=100,
            event_time=base, allocation_date=date(2026, 5, 25),
            horizon_date=date(2026, 6, 1), placed_at=base,
            event_price=Decimal("100"), status="FILLED",
            strategy_version="v1", allocator_version="v1",
            execution_engine_version="v1", weight=1.0,
        )
        db.add(order); db.flush()
        db.add(PaperPosition(
            order_id=order.id, entry_fill_id=1, exit_fill_id=None,
            strategy="general", ticker="AAPL", quantity=100,
            entry_price=Decimal("100"), entry_date=date(2026, 5, 25),
            horizon_date=date(2026, 6, 1), status="OPEN", opened_at=base,
        ))
        db.commit()
    finally:
        db.close()


def test_requires_auth(client):
    response = client.get("/lab/reconcile", follow_redirects=False)
    assert response.status_code == 303


def test_no_data_state(client, monkeypatch):
    _login(client, monkeypatch)
    response = client.get("/lab/reconcile")
    assert response.status_code == 200
    assert "对账" in response.text  # h1
    assert "无法对账" in response.text or "no_broker_data" in response.text.lower()


def test_matched_state_green(client, monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    _login(client, monkeypatch)
    _seed_completed_with_position()
    response = client.get("/lab/reconcile")
    assert response.status_code == 200
    assert "AAPL" in response.text
    assert "已对齐" in response.text or "matched" in response.text.lower()
    get_settings.cache_clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_lab_reconcile_route.py -v`
Expected: FAIL — route does not exist (404 instead of 303/200)

- [ ] **Step 3: Write minimal implementation**

```python
# marketpulse/web/routes/reconcile.py
"""Phase 7c — /lab/reconcile route.

Read-only. No mutation. The route is auth-gated and consumes the
ReconciliationDashboard dataclass produced by query_models.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from marketpulse.reconcile.query_models import load_reconciliation_dashboard
from marketpulse.web.deps import get_db, require_auth
from marketpulse.web.main import templates

router = APIRouter()


@router.get("/lab/reconcile", response_class=HTMLResponse)
def lab_reconcile(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    dashboard = load_reconciliation_dashboard(db)
    return templates.TemplateResponse(
        request, "lab_reconcile.html", {"dashboard": dashboard},
    )
```

Modify `marketpulse/web/main.py` — find the imports inside `create_app`'s `lifespan` and the router registrations. Add `reconcile` to both:

```python
# In create_app, where routes are imported (around line 113-120):
from marketpulse.web.routes import (
    alerts, auth, backtest, broker, health, holdings, home,
    lab, paper_trading, recap, reconcile, recaps, stock, trades,
    watchlist,
)
```

```python
# Where routers are registered (around line 135-140):
app.include_router(reconcile.router)
```

- [ ] **Step 4: Add a minimal lab_reconcile.html so the route returns 200**

```html
{# marketpulse/web/templates/lab_reconcile.html - minimal placeholder for T4 #}
{% extends "base.html" %}
{% block main_width %}max-w-[2400px]{% endblock %}
{% block nav_width %}max-w-[2400px]{% endblock %}
{% block content %}
<section class="mp-lab-ops">
  <header class="mp-lab-ops__header">
    <div>
      <p class="mp-eyebrow mp-eyebrow--primary">Lab · Reconciliation</p>
      <h1>对账</h1>
    </div>
  </header>
  {% if dashboard.no_broker_data %}
    <article class="mp-card"><div class="mp-card__body" style="padding:32px;text-align:center;">
      <p>无法对账 — 尚未捕获 broker truth。</p>
    </div></article>
  {% elif dashboard.account_ambiguous %}
    <article class="mp-card"><div class="mp-card__body" style="padding:32px;text-align:center;">
      <p>Ambiguous broker account — set <code>IBKR_ACCOUNT_ID</code>.</p>
    </div></article>
  {% else %}
    <p>{% if dashboard.severity.value == 'green' %}已对齐{% endif %}</p>
    {% for row in dashboard.rows %}
      <div>{{ row.symbol }} — {{ row.diff_type.value }}</div>
    {% endfor %}
  {% endif %}
</section>
{% endblock %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/web/test_lab_reconcile_route.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add marketpulse/web/routes/reconcile.py marketpulse/web/main.py \
        marketpulse/web/templates/lab_reconcile.html \
        tests/web/test_lab_reconcile_route.py
git commit -m "feat(7c): T4 — /lab/reconcile route returning minimal dashboard"
```

---

## Task 5: Full templates — hero + summary cards + diff table

**Files:**
- Modify: `marketpulse/web/templates/lab_reconcile.html`
- Create: `marketpulse/web/templates/partials/reconcile_hero.html`
- Create: `marketpulse/web/templates/partials/reconcile_summary_cards.html`
- Create: `marketpulse/web/templates/partials/reconcile_diff_table.html`
- Modify: `marketpulse/web/static/css/app.css` (small additions)

- [ ] **Step 1: Extend the route test to assert each partial's content**

Append to `tests/web/test_lab_reconcile_route.py`:

```python
def test_summary_cards_render_counts(client, monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    _login(client, monkeypatch)
    _seed_completed_with_position()
    response = client.get("/lab/reconcile")
    assert "已对齐" in response.text
    assert "缺 broker" in response.text
    assert "缺 paper" in response.text
    assert "数量不一致" in response.text
    assert "方向相反" in response.text
    get_settings.cache_clear()


def test_diff_table_renders_columns(client, monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    _login(client, monkeypatch)
    _seed_completed_with_position()
    response = client.get("/lab/reconcile")
    # Table headers
    assert "Symbol" in response.text
    assert "Paper" in response.text
    assert "Broker" in response.text
    # Row content
    assert "AAPL" in response.text
    get_settings.cache_clear()


def test_stale_banner_shows_when_broker_old(client, monkeypatch):
    """Seed a 25h-old completed run; banner must appear."""
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    _login(client, monkeypatch)

    gen = db_base.session_scope()
    db = next(gen)
    try:
        base = datetime.now(UTC) - timedelta(hours=25)
        run = BrokerSyncRun(
            started_at=base, completed_at=base + timedelta(seconds=10),
            broker="IBKR", broker_environment="paper",
            account_id="DU-A", status="completed", context={},
        )
        db.add(run); db.commit()
    finally:
        db.close()

    response = client.get("/lab/reconcile")
    assert "未更新" in response.text or "stale" in response.text.lower()
    get_settings.cache_clear()
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `uv run pytest tests/web/test_lab_reconcile_route.py::test_summary_cards_render_counts tests/web/test_lab_reconcile_route.py::test_diff_table_renders_columns tests/web/test_lab_reconcile_route.py::test_stale_banner_shows_when_broker_old -v`
Expected: 3 FAIL — missing copy strings

- [ ] **Step 3: Write the partials and rewrite lab_reconcile.html**

```html
{# marketpulse/web/templates/partials/reconcile_hero.html #}
{# Phase 7c — read-only hero. Per spec: no "latest paper tick time"
   (would require PaperAuditEvent read, excluded from allowed-reads). #}
{% set sev = dashboard.severity.value %}
<header class="mp-lab-ops__header">
  <div>
    <p class="mp-eyebrow mp-eyebrow--primary">Lab · Reconciliation</p>
    <h1>对账</h1>
    <p>
      Phase 7c — paper engine 当前持仓 vs broker truth 最近快照,只读 diff view。
    </p>
    {% if not dashboard.no_broker_data and not dashboard.account_ambiguous %}
      <div class="mp-broker-account-row">
        <span class="mp-card__title" style="margin-right:4px;">
          {{ dashboard.broker_account_id }}
        </span>
        {% if sev == 'green' %}
          <span class="mp-chip mp-chip--success">已对齐</span>
        {% elif sev == 'yellow' %}
          <span class="mp-chip mp-chip--warn">有偏差</span>
        {% elif sev == 'red' %}
          <span class="mp-chip mp-chip--failed">严重偏差</span>
        {% endif %}
        <span class="mp-lab-muted">paper 开仓 {{ dashboard.paper_open_position_count }}</span>
      </div>
      {% if dashboard.broker_is_stale %}
        <div class="mp-broker-stale-banner">
          Broker snapshot 已超过 24 小时未更新,详见 <a href="/lab/broker">broker 页</a>。
        </div>
      {% endif %}
    {% elif dashboard.account_ambiguous %}
      <div class="mp-broker-account-row">
        <span class="mp-chip mp-chip--warn">Ambiguous broker account</span>
        <span class="mp-lab-muted">设置 <code>IBKR_ACCOUNT_ID</code> 选定账号</span>
      </div>
    {% else %}
      <div class="mp-broker-account-row">
        <span class="mp-chip mp-chip--warn">无法对账</span>
        <span class="mp-lab-muted">尚未捕获 broker truth</span>
      </div>
    {% endif %}
  </div>
  {% if dashboard.broker_completed_at %}
    <div class="mp-lab-ops__generated">
      {% if dashboard.broker_reference_code %}Ref: <code>{{ dashboard.broker_reference_code }}</code><br>{% endif %}
      Broker {{ dashboard.broker_completed_at.strftime('%Y-%m-%d %H:%M UTC') }}
    </div>
  {% endif %}
</header>
```

```html
{# marketpulse/web/templates/partials/reconcile_summary_cards.html #}
<section class="mp-lab-kpis" aria-label="Reconciliation Summary">
  <article class="mp-card mp-lab-kpi">
    <div class="mp-card__body">
      <div class="mp-card__eyebrow">已对齐</div>
      <div class="mp-lab-kpi__value">{{ dashboard.matched_count }}</div>
    </div>
  </article>
  <article class="mp-card mp-lab-kpi">
    <div class="mp-card__body">
      <div class="mp-card__eyebrow">缺 broker</div>
      <div class="mp-lab-kpi__value">{{ dashboard.missing_in_broker_count }}</div>
    </div>
  </article>
  <article class="mp-card mp-lab-kpi">
    <div class="mp-card__body">
      <div class="mp-card__eyebrow">缺 paper</div>
      <div class="mp-lab-kpi__value">{{ dashboard.missing_in_paper_count }}</div>
    </div>
  </article>
  <article class="mp-card mp-lab-kpi">
    <div class="mp-card__body">
      <div class="mp-card__eyebrow">数量不一致</div>
      <div class="mp-lab-kpi__value">{{ dashboard.quantity_mismatch_count }}</div>
    </div>
  </article>
  <article class="mp-card mp-lab-kpi">
    <div class="mp-card__body">
      <div class="mp-card__eyebrow">方向相反</div>
      <div class="mp-lab-kpi__value">{{ dashboard.side_mismatch_count }}</div>
    </div>
  </article>
</section>
```

```html
{# marketpulse/web/templates/partials/reconcile_diff_table.html #}
<article class="mp-card">
  <div class="mp-card__head"><div class="mp-card__title">持仓 diff</div></div>
  <div class="mp-card__body">
    {% if dashboard.rows %}
      <div class="mp-broker-table-wrap">
        <table class="mp-table mp-table--broker">
          <thead><tr>
            <th>Symbol</th><th>类型</th>
            <th class="num">Paper Qty</th>
            <th class="num">Broker Qty</th>
            <th class="num">Δ</th>
          </tr></thead>
          <tbody>
            {% for r in dashboard.rows %}
              <tr>
                <td>{{ r.symbol }}</td>
                <td>
                  {% if r.diff_type.value == 'matched' %}
                    <span class="mp-chip mp-chip--success">已对齐</span>
                  {% elif r.diff_type.value == 'missing_in_broker' %}
                    <span class="mp-chip {% if r.is_red %}mp-chip--failed{% else %}mp-chip--warn{% endif %}">缺 broker</span>
                  {% elif r.diff_type.value == 'missing_in_paper' %}
                    <span class="mp-chip mp-chip--warn">缺 paper</span>
                  {% elif r.diff_type.value == 'quantity_mismatch' %}
                    <span class="mp-chip mp-chip--warn">数量不一致</span>
                  {% elif r.diff_type.value == 'side_mismatch' %}
                    <span class="mp-chip mp-chip--failed">方向相反</span>
                  {% endif %}
                </td>
                <td class="num">{% if r.paper_qty is not none %}{{ r.paper_qty }}{% else %}—{% endif %}</td>
                <td class="num">{% if r.broker_qty is not none %}{{ r.broker_qty }}{% else %}—{% endif %}</td>
                <td class="num">{% if r.delta is not none %}{{ r.delta }}{% else %}—{% endif %}</td>
              </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    {% else %}
      <div class="mp-empty-state">无 diff 行</div>
    {% endif %}
  </div>
</article>
```

Rewrite `marketpulse/web/templates/lab_reconcile.html`:

```html
{# Phase 7c — Position Reconciliation MVP, read-only inspection surface. #}
{% extends "base.html" %}
{% block main_width %}max-w-[2400px]{% endblock %}
{% block nav_width %}max-w-[2400px]{% endblock %}
{% block content %}
<section class="mp-lab-ops">
  {% include "partials/reconcile_hero.html" %}

  {% if dashboard.no_broker_data %}
    <article class="mp-card">
      <div class="mp-card__body" style="padding:32px; text-align:center;">
        <h2 style="margin:0 0 8px;">无法对账</h2>
        <p class="mp-lab-muted">尚未捕获 broker truth — 先跑 Flex sync 见 <code>docs/operations/ibkr-readonly-sync-runbook.md</code>。</p>
        {% if dashboard.recent_failed_run_descriptions %}
          <p class="mp-lab-muted" style="margin-top:12px;">最近失败的同步:</p>
          <ul style="list-style:none; padding:0; margin:0;">
            {% for d in dashboard.recent_failed_run_descriptions %}
              <li><code>{{ d }}</code></li>
            {% endfor %}
          </ul>
        {% endif %}
      </div>
    </article>
  {% elif dashboard.account_ambiguous %}
    <article class="mp-card">
      <div class="mp-card__body" style="padding:32px; text-align:center;">
        <h2 style="margin:0 0 8px;">Ambiguous broker account</h2>
        <p class="mp-lab-muted">历史 sync 包含多个账号但 <code>IBKR_ACCOUNT_ID</code> 未配置,无法选定对账目标。</p>
      </div>
    </article>
  {% else %}
    {% include "partials/reconcile_summary_cards.html" %}
    {% include "partials/reconcile_diff_table.html" %}
  {% endif %}
</section>
{% endblock %}
```

Append the minimal CSS to `marketpulse/web/static/css/app.css` at the bottom (most styles are reused from broker viewer):

```css
/* Phase 7c — Reconciliation page reuses .mp-lab-ops / .mp-lab-kpis /
   .mp-broker-table-wrap / .mp-broker-stale-banner / .mp-broker-account-row
   from the broker viewer. No additional rules needed at MVP. */
```

- [ ] **Step 4: Run all reconcile tests**

Run: `uv run pytest tests/reconcile tests/web/test_lab_reconcile_route.py -v`
Expected: all pass (17 reconcile tests + 6 route tests = 23)

- [ ] **Step 5: Commit**

```bash
git add marketpulse/web/templates/lab_reconcile.html \
        marketpulse/web/templates/partials/reconcile_hero.html \
        marketpulse/web/templates/partials/reconcile_summary_cards.html \
        marketpulse/web/templates/partials/reconcile_diff_table.html \
        marketpulse/web/static/css/app.css \
        tests/web/test_lab_reconcile_route.py
git commit -m "feat(7c): T5 — hero + summary cards + diff table partials"
```

---

## Task 6: Nav link

**Files:**
- Modify: `marketpulse/web/templates/base.html`

- [ ] **Step 1: Add the nav link**

Locate the nav block in `base.html` (around line 70-75 where other lab links live). After the `/lab/broker` link, add:

```html
<a href="/lab/reconcile" class="{% if p.startswith('/lab/reconcile') %}mp-nav-active{% endif %}">对账</a>
```

- [ ] **Step 2: Add a nav-link regression assertion**

Append to `tests/web/test_lab_reconcile_route.py`:

```python
def test_nav_link_present(client, monkeypatch):
    """Regression: 对账 nav link must render on any authed page."""
    _login(client, monkeypatch)
    response = client.get("/lab/reconcile")
    assert 'href="/lab/reconcile"' in response.text
    assert "对账" in response.text
```

- [ ] **Step 3: Verify visually with a quick route check**

Run: `uv run pytest tests/web/test_lab_reconcile_route.py -v`
Expected: 7 passed (the 6 existing + the new nav assertion)

- [ ] **Step 4: Commit**

```bash
git add marketpulse/web/templates/base.html \
        tests/web/test_lab_reconcile_route.py
git commit -m "feat(7c): T6 — nav link for /lab/reconcile + regression test"
```

---

## Task 7: Architecture guard

**Files:**
- Create: `tests/architecture/test_lab_reconcile_isolation.py`

- [ ] **Step 1: Write the guard test**

```python
# tests/architecture/test_lab_reconcile_isolation.py
"""Phase 7c reconciliation — architecture guard.

The reconcile module + route + templates must only read these models:
  PaperPosition
  BrokerSyncRun
  BrokerPositionSnapshot

They must NEVER touch:
  - Any session mutation (add/flush/commit) on any model
  - Phase 7b write provenance (BrokerOrderIntent, BrokerOrderEvent)
  - Out-of-scope broker snapshots (BrokerOpenOrderSnapshot,
    BrokerExecutionSnapshot, BrokerAccountSnapshot, BrokerCashSnapshot)
  - Out-of-scope paper models (PaperOrder, PaperFill, PaperCashLedger,
    PaperAuditEvent)

Per spec: templates also must NOT mention ORM class names even in copy
(substring scan would otherwise fire).
"""
# Layer: architecture
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PY_TARGETS = [
    ROOT / "marketpulse" / "reconcile" / "types.py",
    ROOT / "marketpulse" / "reconcile" / "diffing.py",
    ROOT / "marketpulse" / "reconcile" / "query_models.py",
    ROOT / "marketpulse" / "web" / "routes" / "reconcile.py",
]

TEMPLATE_TARGETS = [
    ROOT / "marketpulse" / "web" / "templates" / "lab_reconcile.html",
    ROOT / "marketpulse" / "web" / "templates" / "partials" / "reconcile_hero.html",
    ROOT / "marketpulse" / "web" / "templates" / "partials" / "reconcile_summary_cards.html",
    ROOT / "marketpulse" / "web" / "templates" / "partials" / "reconcile_diff_table.html",
]

FORBIDDEN_NAMES = (
    # Phase 7b write-provenance
    "BrokerOrderIntent",
    "BrokerOrderEvent",
    # Out-of-scope broker snapshots
    "BrokerOpenOrderSnapshot",
    "BrokerExecutionSnapshot",
    "BrokerAccountSnapshot",
    "BrokerCashSnapshot",
    # Out-of-scope paper models (only PaperPosition is allowed)
    "PaperOrder",
    "PaperFill",
    "PaperCashLedger",
    "PaperAuditEvent",
)

# Session mutation surface — name attribute references walk these.
FORBIDDEN_SESSION_ATTRS = ("add", "add_all", "flush", "commit", "delete")


def _walk_ast(path: Path) -> tuple[list[str], list[str]]:
    """Return (names, attrs) sets used in the module."""
    tree = ast.parse(path.read_text())
    names: list[str] = []
    attrs: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            attrs.append(node.attr)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.append(alias.name)
    return names, attrs


def test_python_targets_avoid_forbidden_models():
    offenders: list[str] = []
    for path in PY_TARGETS:
        assert path.exists(), f"target missing: {path}"
        names, _ = _walk_ast(path)
        for name in names:
            if name in FORBIDDEN_NAMES:
                offenders.append(f"{path.name}: references {name}")
    assert not offenders, (
        "Phase 7c reconcile module must only read PaperPosition / "
        "BrokerSyncRun / BrokerPositionSnapshot:\n  "
        + "\n  ".join(offenders)
    )


def test_python_targets_do_not_mutate_session():
    """No session.add/flush/commit/delete in reconcile/* or route.

    AST attribute names are receiver-agnostic. Our FORBIDDEN_SESSION_ATTRS
    list is the four SQLAlchemy mutation verbs (add/add_all/flush/commit/
    delete); none of these collide with stdlib container methods like
    list.append or dict.update that the module might legitimately call.
    """
    offenders: list[str] = []
    for path in PY_TARGETS:
        _, attrs = _walk_ast(path)
        for attr in attrs:
            if attr in FORBIDDEN_SESSION_ATTRS:
                offenders.append(f"{path.name}: calls .{attr}()")
    assert not offenders, (
        "Phase 7c reconcile module must be read-only:\n  "
        + "\n  ".join(offenders)
    )


def test_templates_avoid_orm_class_names():
    """Templates substring-scanned for forbidden ORM class names.

    Per spec template authoring rule: user copy must not mention model
    classes, since the substring scan is intentionally name-blind.
    """
    offenders: list[str] = []
    for path in TEMPLATE_TARGETS:
        assert path.exists(), f"template missing: {path}"
        text = path.read_text()
        for name in FORBIDDEN_NAMES:
            if name in text:
                offenders.append(f"{path.name}: contains forbidden name {name}")
        # Also check the only-allowed paper model isn't accidentally
        # mentioned (since templates should use user copy, not class names)
        if "PaperPosition" in text:
            offenders.append(f"{path.name}: contains PaperPosition (use 纸上交易 copy)")
    assert not offenders, (
        "Phase 7c templates must use user-facing copy only:\n  "
        + "\n  ".join(offenders)
    )
```

- [ ] **Step 2: Run the guard**

Run: `uv run pytest tests/architecture/test_lab_reconcile_isolation.py -v`
Expected: 3 passed (if any of the implementation files violated the guard, this is where it surfaces — fix forward, don't relax the guard)

- [ ] **Step 3: Commit**

```bash
git add tests/architecture/test_lab_reconcile_isolation.py
git commit -m "test(7c): T7 — architecture guard for /lab/reconcile read-only boundary"
```

---

## Task 8: Final integration

**Files:**
- No new files; verify everything ties together.

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -q`
Expected: all green (existing tests unchanged; new tests pass)

- [ ] **Step 2: Run ruff**

Run: `uv run ruff check .`
Expected: All checks passed!

- [ ] **Step 3: Manual smoke (local TestClient)**

```bash
uv run python -c "
from fastapi.testclient import TestClient
from marketpulse.web.main import create_app

client = TestClient(create_app())
# Default unauthenticated → 303 redirect
resp = client.get('/lab/reconcile', follow_redirects=False)
print(f'unauthenticated: {resp.status_code}')
assert resp.status_code == 303
print('smoke OK')
"
```

Expected output:
```
unauthenticated: 303
smoke OK
```

- [ ] **Step 4: Open the PR**

```bash
git push -u origin <branch>
gh pr create --title "feat(7c): Phase 7c Position Reconciliation MVP — /lab/reconcile" \
  --body "Implements docs/superpowers/specs/2026-05-25-phase-7c-position-reconciliation-design.md.

Position-level diff between current open paper_position and the latest completed broker truth snapshot. Read-only, on-demand, no DB persistence, no migration.

## Scope locks honored
14 spec locks + 12 design refinements (see spec).

## Files
- New: marketpulse/reconcile/{types,diffing,query_models}.py
- New: marketpulse/web/routes/reconcile.py
- New: 4 Jinja templates under marketpulse/web/templates/
- New: 4 test files (~26 new tests across unit/stateful/route/architecture layers)
- Modified: marketpulse/web/{main.py, templates/base.html, static/css/app.css}

## Test Plan
- [x] Full suite passes, ruff clean
- [x] Architecture guard enforces read-only boundary
- [ ] Deploy + manually hit /lab/reconcile against the NAS broker_sync_run history"
```

---

## Self-review checklist (run by plan author before handing off)

### Spec coverage
- [x] **L1-L5 scope locks** — T1 enum + T2 diff function structurally enforce position-only, no auto-repair
- [x] **L6-L9 on-demand / no DB / no migration** — no Alembic file in the plan, query model is pure read
- [x] **L10 paper open positions only** — T3 query_model filters `exit_fill_id IS NULL`
- [x] **L11-L14 quantity semantics** — T2 tests cover all 4 boundary cases
- [x] **5 design refinements (delta=None, severity rank, completed-only ambiguity, aggregation, normalization)** — T1+T2+T3 all assert these explicitly
- [x] **#1 paper tick time removed from hero** — T5 hero shows paper open-position count instead
- [x] **#2 recent_failed_run_descriptions** — T3 query model includes; T5 template renders
- [x] **#3 defensive p!=0 for MISSING_IN_BROKER** — T2 has explicit test
- [x] **#4 templates use copy not class names** — T7 guard enforces

### Type / signature consistency
- `reconcile_positions(paper, broker)` signature consistent across T2 / T3
- `DiffRow.delta is None | Decimal` consistent in all tests + impl
- `Severity` enum used uniformly
- `ReconciliationDashboard` field names consistent T1 ↔ T3 ↔ T5

### Placeholder scan
- No TBD / TODO / "similar to" — every code block self-contained

### Open issues
None.
