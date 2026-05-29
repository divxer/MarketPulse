# PR3a — North-Star NAV Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an immutable daily-snapshot table (`paper_nav_snapshot`) plus the compute pipeline, scheduler hook, and `/lab/charter-metrics` extension that turns paper-trading state into the charter's north-star series.

**Architecture:** Pure `compute_nav_snapshot` (no I/O) → db-layer `snapshot_repo` → orchestration `snapshot_runner` (called at end of `paper_trading_tick`). `/lab/charter-metrics` reads the snapshot table for `north_star` + 3 diagnostics computed from `paper_audit_event` and `paper_fill` over the last 30 snapshot trading dates. No network, no recompute on the read path.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, Alembic, FastAPI, pytest, `Decimal`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-28-pr3a-north-star-snapshot-design.md` (commit `ef54baa`). 20 scope locks L1–L20.

**Branch:** `feat/pr3a-north-star-snapshot`.

---

## File Structure

| Path | Layer | Responsibility |
|---|---|---|
| `alembic/versions/0014_paper_nav_snapshot.py` (new) | infra | Create/drop `paper_nav_snapshot` table. Down-revision `0013`. |
| `marketpulse/db/models.py` (modify) | db model | Append `PaperNavSnapshot` ORM class. |
| `marketpulse/portfolio/__init__.py` (new) | pkg init | Empty. |
| `marketpulse/portfolio/north_star.py` (new) | pure | `OpenPosition`, `NavSnapshot` dataclasses + `compute_nav_snapshot()`. |
| `marketpulse/portfolio/snapshot_repo.py` (new) | db | `insert_snapshot`, `force_replace_snapshot`, `get_*`, `count_*`, `get_spy_anchor`, `SnapshotAlreadyExists`, `NavSnapshotRow` ↔ `NavSnapshot` translation. |
| `marketpulse/portfolio/snapshot_runner.py` (new) | orchestration | `run_nav_snapshot()`, `NoCashLedgerForDate`. |
| `marketpulse/scheduler/jobs.py` (modify) | scheduler | Hook `run_nav_snapshot` at end of `run_paper_trading_tick`. |
| `marketpulse/ops/charter_metrics.py` (modify) | contract | `build_north_star_section`, `build_diagnostics_section`, extend `build_charter_metrics` with `session` param + Decimal→float conversion. |
| `marketpulse/web/routes/charter.py` (modify) | web | Inject `db: Session` and pass to builder. |
| `tests/portfolio/__init__.py` (new) | test pkg | Empty. |
| `tests/portfolio/test_north_star.py` (new) | test | 9 compute tests. |
| `tests/portfolio/test_snapshot_repo.py` (new) | test | 8 repo tests. |
| `tests/portfolio/test_snapshot_runner.py` (new) | test | 8 runner tests. |
| `tests/scheduler/test_paper_trading_tick.py` (modify if exists, else new) | test | 1 isolation test. |
| `tests/ops/test_charter_metrics_north_star.py` (new) | test | 10 extension tests. |
| `tests/migrations/__init__.py` (new if missing) | test pkg | Empty. |
| `tests/migrations/test_0014_paper_nav_snapshot.py` (new) | test | 3 migration tests. |
| `tests/web/test_charter_route.py` (modify) | test | Add 5 route tests. |

---

## Task 1: Alembic migration + ORM model + migration tests

**Pre-flight (run once before Step 1 to confirm revision number):**

```bash
ls alembic/versions/ | sort | grep -v __pycache__ | tail -3
```

Expected at plan-write time: latest revision is `0013_phase7b_broker_order_pilot.py`. **If the head has advanced past `0013`, use the next free integer instead of `0014` throughout this task** (filename, `revision = "..."`, `down_revision = "..."`, the test's `command.upgrade(cfg, "...")` calls, and any reference to `0014` in later tasks). The plan assumes `0014`; only adjust if the pre-flight shows otherwise.

**Files:**
- Create: `alembic/versions/0014_paper_nav_snapshot.py`
- Modify: `marketpulse/db/models.py` (append PaperNavSnapshot)
- Create: `tests/migrations/test_0014_paper_nav_snapshot.py`
- Create (if missing): `tests/migrations/__init__.py`

- [ ] **Step 1: Write failing migration test**

Create `tests/migrations/__init__.py` empty if it doesn't exist.

Create `tests/migrations/test_0014_paper_nav_snapshot.py`:

```python
# Layer: test
"""PR3a — paper_nav_snapshot migration tests."""
from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config


def _alembic_config(db_url: str) -> Config:
    ini = Path(__file__).resolve().parent.parent.parent / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _table_columns(engine, table: str) -> dict[str, str]:
    insp = sa.inspect(engine)
    return {c["name"]: str(c["type"]) for c in insp.get_columns(table)}


def test_alembic_upgrade_creates_table(tmp_path):
    db_url = f"sqlite:///{tmp_path/'mig.db'}"
    cfg = _alembic_config(db_url)
    command.upgrade(cfg, "0014")

    engine = sa.create_engine(db_url)
    insp = sa.inspect(engine)
    assert "paper_nav_snapshot" in insp.get_table_names()

    cols = _table_columns(engine, "paper_nav_snapshot")
    expected = {
        "trading_date", "cash_balance", "holdings_mtm", "portfolio_nav",
        "anchor_portfolio_nav", "portfolio_index", "spy_close",
        "anchor_spy_close", "spy_index", "excess_return",
        "trading_days_observed", "coverage_ratio", "is_sufficient",
        "unpriced_positions_count", "unpriced_tickers",
        "created_at", "updated_at", "is_rebuilt", "rebuild_reason",
    }
    assert set(cols.keys()) == expected


def test_alembic_downgrade_drops_table(tmp_path):
    db_url = f"sqlite:///{tmp_path/'mig.db'}"
    cfg = _alembic_config(db_url)
    command.upgrade(cfg, "0014")
    command.downgrade(cfg, "0013")

    engine = sa.create_engine(db_url)
    insp = sa.inspect(engine)
    assert "paper_nav_snapshot" not in insp.get_table_names()


def test_column_defaults_safe_for_hand_insert(tmp_path):
    """is_rebuilt defaults to 0, unpriced_positions_count defaults to 0."""
    db_url = f"sqlite:///{tmp_path/'mig.db'}"
    cfg = _alembic_config(db_url)
    command.upgrade(cfg, "0014")
    engine = sa.create_engine(db_url)
    with engine.connect() as conn:
        conn.execute(sa.text("""
            INSERT INTO paper_nav_snapshot (
                trading_date, cash_balance, holdings_mtm, portfolio_nav,
                anchor_portfolio_nav, portfolio_index,
                trading_days_observed, coverage_ratio, is_sufficient,
                created_at, updated_at
            ) VALUES (
                '2026-05-28', 100000, 0, 100000, 100000, 1,
                1, 0.011, 0,
                '2026-05-28T00:00:00+00:00', '2026-05-28T00:00:00+00:00'
            )
        """))
        conn.commit()
        row = conn.execute(sa.text(
            "SELECT is_rebuilt, unpriced_positions_count, unpriced_tickers "
            "FROM paper_nav_snapshot WHERE trading_date='2026-05-28'"
        )).first()
    assert row.is_rebuilt == 0
    assert row.unpriced_positions_count == 0
    assert row.unpriced_tickers is None
```

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/migrations/test_0014_paper_nav_snapshot.py -v`
Expected: FAIL (`Can't locate revision 0014`).

- [ ] **Step 3: Create the migration**

Create `alembic/versions/0014_paper_nav_snapshot.py`:

```python
"""Phase Charter PR3a — paper_nav_snapshot immutable EOD NAV table.

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-28
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_nav_snapshot",
        sa.Column("trading_date", sa.Date, primary_key=True),
        sa.Column("cash_balance", sa.Numeric(18, 6), nullable=False),
        sa.Column("holdings_mtm", sa.Numeric(18, 6), nullable=False),
        sa.Column("portfolio_nav", sa.Numeric(18, 6), nullable=False),
        sa.Column("anchor_portfolio_nav", sa.Numeric(18, 6), nullable=False),
        sa.Column("portfolio_index", sa.Numeric(18, 10), nullable=False),
        sa.Column("spy_close", sa.Numeric(18, 6), nullable=True),
        sa.Column("anchor_spy_close", sa.Numeric(18, 6), nullable=True),
        sa.Column("spy_index", sa.Numeric(18, 10), nullable=True),
        sa.Column("excess_return", sa.Numeric(18, 10), nullable=True),
        sa.Column("trading_days_observed", sa.Integer, nullable=False),
        sa.Column("coverage_ratio", sa.Numeric(18, 10), nullable=False),
        sa.Column("is_sufficient", sa.Boolean, nullable=False),
        sa.Column(
            "unpriced_positions_count", sa.Integer,
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column("unpriced_tickers", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "is_rebuilt", sa.Boolean,
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column("rebuild_reason", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("paper_nav_snapshot")
```

- [ ] **Step 4: Add the ORM model**

Append to `marketpulse/db/models.py` (after `PaperAuditEvent`, before `BrokerSyncRun`):

```python
class PaperNavSnapshot(Base):
    """PR3a — immutable EOD NAV snapshot.

    Lock L1: normal flow is INSERT only; admin path sets is_rebuilt + reason.
    Lock L2: one row per trading_date (PK).
    See docs/superpowers/specs/2026-05-28-pr3a-north-star-snapshot-design.md.
    """
    __tablename__ = "paper_nav_snapshot"

    trading_date: Mapped[date] = mapped_column(Date, primary_key=True)
    cash_balance: Mapped[_Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    holdings_mtm: Mapped[_Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    portfolio_nav: Mapped[_Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    anchor_portfolio_nav: Mapped[_Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    portfolio_index: Mapped[_Decimal] = mapped_column(Numeric(18, 10), nullable=False)
    spy_close: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    anchor_spy_close: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    spy_index: Mapped[_Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    excess_return: Mapped[_Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    trading_days_observed: Mapped[int] = mapped_column(Integer, nullable=False)
    coverage_ratio: Mapped[_Decimal] = mapped_column(Numeric(18, 10), nullable=False)
    is_sufficient: Mapped[bool] = mapped_column(Boolean, nullable=False)
    unpriced_positions_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    unpriced_tickers: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    is_rebuilt: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0"),
    )
    rebuild_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Verify that all needed imports (`Boolean`, `Date`, `Integer`, `Numeric`, `Text`, `text`, `TZDateTime`, `datetime`, `date`, `_Decimal`) are already imported at the top of `models.py`. If `Boolean` is missing from the SQLAlchemy import line, add it. The other types are already used by existing models.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/migrations/test_0014_paper_nav_snapshot.py -v`
Expected: PASS — 3 tests.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0014_paper_nav_snapshot.py marketpulse/db/models.py tests/migrations/test_0014_paper_nav_snapshot.py tests/migrations/__init__.py
git commit -m "feat(pr3a): alembic 0014 + PaperNavSnapshot ORM (Charter top-3 #1 PR3a)"
```

---

## Task 2: `north_star.py` dataclasses + `compute_nav_snapshot` (priced path)

**Files:**
- Create: `marketpulse/portfolio/__init__.py`
- Create: `marketpulse/portfolio/north_star.py`
- Create: `tests/portfolio/__init__.py`
- Create: `tests/portfolio/test_north_star.py`

- [ ] **Step 1: Write failing tests for the basic priced path + self-anchor + frozen dataclass**

Create `tests/portfolio/__init__.py` empty.

Create `tests/portfolio/test_north_star.py`:

```python
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
    NavSnapshot,
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/portfolio/test_north_star.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create the module**

Create `marketpulse/portfolio/__init__.py` empty.

Create `marketpulse/portfolio/north_star.py`:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/portfolio/test_north_star.py -v`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/portfolio/__init__.py marketpulse/portfolio/north_star.py tests/portfolio/__init__.py tests/portfolio/test_north_star.py
git commit -m "feat(pr3a): NavSnapshot + compute_nav_snapshot priced path (PR3a)"
```

---

## Task 3: `compute_nav_snapshot` — unpriced / SPY missing / coverage / sufficient

**Files:**
- Modify: `tests/portfolio/test_north_star.py` (append-only)

- [ ] **Step 1: Append 5 more tests**

Append to `tests/portfolio/test_north_star.py`:

```python
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
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/portfolio/test_north_star.py -v`
Expected: PASS — 10 tests total (4 from Task 2 + 6 new).

- [ ] **Step 3: Commit**

```bash
git add tests/portfolio/test_north_star.py
git commit -m "test(pr3a): unpriced + spy missing + coverage + sufficient"
```

---

## Task 4: `snapshot_repo.py` — `insert_snapshot` + `SnapshotAlreadyExists` + `force_replace_snapshot`

**Files:**
- Create: `marketpulse/portfolio/snapshot_repo.py`
- Create: `tests/portfolio/test_snapshot_repo.py`

- [ ] **Step 1: Write failing tests**

Create `tests/portfolio/test_snapshot_repo.py`:

```python
# Layer: test
"""PR3a — snapshot_repo tests."""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from marketpulse.portfolio.north_star import NavSnapshot
from marketpulse.portfolio.snapshot_repo import (
    SnapshotAlreadyExists,
    force_replace_snapshot,
    get_latest_snapshot,
    get_snapshot,
    insert_snapshot,
)


def _make_snapshot(d: date, *, nav: str = "100000") -> NavSnapshot:
    return NavSnapshot(
        trading_date=d,
        cash_balance=Decimal(nav),
        holdings_mtm=Decimal("0"),
        portfolio_nav=Decimal(nav),
        anchor_portfolio_nav=Decimal(nav),
        portfolio_index=Decimal("1"),
        spy_close=None,
        anchor_spy_close=None,
        spy_index=None,
        excess_return=None,
        trading_days_observed=1,
        coverage_ratio=Decimal("0.0111111111"),
        is_sufficient=False,
        unpriced_positions_count=0,
        unpriced_tickers=(),
    )


def test_insert_snapshot_succeeds(db_session):
    snap = _make_snapshot(date(2026, 5, 28))
    insert_snapshot(db_session, snap)
    db_session.commit()

    fetched = get_snapshot(db_session, date(2026, 5, 28))
    assert fetched is not None
    assert fetched.trading_date == date(2026, 5, 28)
    assert fetched.portfolio_nav == Decimal("100000")


def test_insert_snapshot_pk_conflict_raises(db_session):
    snap = _make_snapshot(date(2026, 5, 28), nav="100000")
    insert_snapshot(db_session, snap)
    db_session.commit()

    second = _make_snapshot(date(2026, 5, 28), nav="999999")
    with pytest.raises(SnapshotAlreadyExists):
        insert_snapshot(db_session, second)
    db_session.rollback()

    # original row preserved
    fetched = get_snapshot(db_session, date(2026, 5, 28))
    assert fetched.portfolio_nav == Decimal("100000")


def test_force_replace_snapshot(db_session):
    snap = _make_snapshot(date(2026, 5, 28), nav="100000")
    insert_snapshot(db_session, snap)
    db_session.commit()

    replacement = _make_snapshot(date(2026, 5, 28), nav="200000")
    force_replace_snapshot(db_session, replacement, reason="corp action backfill")
    db_session.commit()

    fetched = get_snapshot(db_session, date(2026, 5, 28))
    assert fetched.portfolio_nav == Decimal("200000")
    # Repo returns NavSnapshot dataclass which doesn't carry is_rebuilt;
    # verify via raw column read.
    from marketpulse.db.models import PaperNavSnapshot
    row = db_session.query(PaperNavSnapshot).filter_by(
        trading_date=date(2026, 5, 28),
    ).one()
    assert row.is_rebuilt is True
    assert row.rebuild_reason == "corp action backfill"
    assert row.updated_at != row.created_at


def test_get_latest_snapshot_empty(db_session):
    assert get_latest_snapshot(db_session) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/portfolio/test_snapshot_repo.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create the module**

Create `marketpulse/portfolio/snapshot_repo.py`:

```python
# Layer: db
"""SQLAlchemy repository for paper_nav_snapshot.

L1: normal flow is INSERT only (insert_snapshot). The admin path is
force_replace_snapshot(reason). L20: unpriced_tickers is stored as
comma-separated TEXT; None/"" parse to empty tuple.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marketpulse.db.models import PaperNavSnapshot
from marketpulse.portfolio.north_star import NavSnapshot


class SnapshotAlreadyExists(Exception):
    """Raised by insert_snapshot on PK conflict. Use force_replace_snapshot
    for the admin/rebuild path."""


def _encode_tickers(tickers: tuple[str, ...]) -> str | None:
    """L20: empty tuple → None; otherwise comma-join sorted unique tickers."""
    if not tickers:
        return None
    return ",".join(sorted(set(tickers)))


def _decode_tickers(raw: str | None) -> tuple[str, ...]:
    """L20: None/"" → empty tuple."""
    if not raw:
        return ()
    return tuple(raw.split(","))


def _row_to_dc(row: PaperNavSnapshot) -> NavSnapshot:
    return NavSnapshot(
        trading_date=row.trading_date,
        cash_balance=row.cash_balance,
        holdings_mtm=row.holdings_mtm,
        portfolio_nav=row.portfolio_nav,
        anchor_portfolio_nav=row.anchor_portfolio_nav,
        portfolio_index=row.portfolio_index,
        spy_close=row.spy_close,
        anchor_spy_close=row.anchor_spy_close,
        spy_index=row.spy_index,
        excess_return=row.excess_return,
        trading_days_observed=row.trading_days_observed,
        coverage_ratio=row.coverage_ratio,
        is_sufficient=row.is_sufficient,
        unpriced_positions_count=row.unpriced_positions_count,
        unpriced_tickers=_decode_tickers(row.unpriced_tickers),
    )


def _dc_to_kwargs(snap: NavSnapshot, *, now: datetime) -> dict:
    return dict(
        trading_date=snap.trading_date,
        cash_balance=snap.cash_balance,
        holdings_mtm=snap.holdings_mtm,
        portfolio_nav=snap.portfolio_nav,
        anchor_portfolio_nav=snap.anchor_portfolio_nav,
        portfolio_index=snap.portfolio_index,
        spy_close=snap.spy_close,
        anchor_spy_close=snap.anchor_spy_close,
        spy_index=snap.spy_index,
        excess_return=snap.excess_return,
        trading_days_observed=snap.trading_days_observed,
        coverage_ratio=snap.coverage_ratio,
        is_sufficient=snap.is_sufficient,
        unpriced_positions_count=snap.unpriced_positions_count,
        unpriced_tickers=_encode_tickers(snap.unpriced_tickers),
        created_at=now,
        updated_at=now,
        is_rebuilt=False,
        rebuild_reason=None,
    )


def insert_snapshot(session: Session, snapshot: NavSnapshot) -> None:
    """Insert exactly once. Raises SnapshotAlreadyExists on PK conflict.

    IMPORTANT: this function does NOT call session.rollback(). Repository
    functions must not control transaction state — that's the caller's
    responsibility. The runner pre-checks existence before computing, so
    in normal flow the race-to-flush path is unreachable; if it does fire
    (concurrent writer), the caller decides whether to rollback or retry.
    """
    existing = session.get(PaperNavSnapshot, snapshot.trading_date)
    if existing is not None:
        raise SnapshotAlreadyExists(
            f"snapshot already exists for {snapshot.trading_date}"
        )
    row = PaperNavSnapshot(**_dc_to_kwargs(snapshot, now=datetime.now(UTC)))
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        # Caller decides rollback policy.
        raise SnapshotAlreadyExists(
            f"snapshot already exists for {snapshot.trading_date}"
        ) from exc


def force_replace_snapshot(
    session: Session, snapshot: NavSnapshot, *, reason: str,
) -> None:
    """Admin/rebuild path. Sets is_rebuilt=True and rebuild_reason.
    Preserves created_at; sets updated_at = now."""
    now = datetime.now(UTC)
    row = session.get(PaperNavSnapshot, snapshot.trading_date)
    if row is None:
        # No prior row — straight insert with rebuild flags set.
        kwargs = _dc_to_kwargs(snapshot, now=now)
        kwargs["is_rebuilt"] = True
        kwargs["rebuild_reason"] = reason
        session.add(PaperNavSnapshot(**kwargs))
        session.flush()
        return

    # Mutate the existing row in place; preserve created_at.
    new_kwargs = _dc_to_kwargs(snapshot, now=now)
    for key in (
        "cash_balance", "holdings_mtm", "portfolio_nav",
        "anchor_portfolio_nav", "portfolio_index",
        "spy_close", "anchor_spy_close", "spy_index", "excess_return",
        "trading_days_observed", "coverage_ratio", "is_sufficient",
        "unpriced_positions_count", "unpriced_tickers",
    ):
        setattr(row, key, new_kwargs[key])
    row.updated_at = now
    row.is_rebuilt = True
    row.rebuild_reason = reason
    session.flush()


def get_snapshot(session: Session, trading_date: date) -> NavSnapshot | None:
    row = session.get(PaperNavSnapshot, trading_date)
    return _row_to_dc(row) if row is not None else None


def get_latest_snapshot(session: Session) -> NavSnapshot | None:
    row = session.scalars(
        select(PaperNavSnapshot)
        .order_by(PaperNavSnapshot.trading_date.desc())
        .limit(1),
    ).first()
    return _row_to_dc(row) if row is not None else None
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/portfolio/test_snapshot_repo.py -v`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/portfolio/snapshot_repo.py tests/portfolio/test_snapshot_repo.py
git commit -m "feat(pr3a): snapshot_repo insert + force_replace + read basics"
```

---

## Task 5: `snapshot_repo.py` — series / recent dates / count / spy anchor

**Files:**
- Modify: `marketpulse/portfolio/snapshot_repo.py` (append helpers)
- Modify: `tests/portfolio/test_snapshot_repo.py` (append tests)

- [ ] **Step 1: Append failing tests**

Append to `tests/portfolio/test_snapshot_repo.py`:

```python
from marketpulse.portfolio.snapshot_repo import (
    count_snapshots_in_window,
    get_recent_snapshot_dates,
    get_snapshot_series,
    get_spy_anchor,
)


def test_get_snapshot_series_range_ascending(db_session):
    for i in range(5):
        insert_snapshot(db_session, _make_snapshot(date(2026, 5, 24 + i)))
    db_session.commit()
    series = get_snapshot_series(
        db_session,
        window_start=date(2026, 5, 25),
        window_end=date(2026, 5, 27),
    )
    assert [s.trading_date for s in series] == [
        date(2026, 5, 25), date(2026, 5, 26), date(2026, 5, 27),
    ]


def test_get_recent_snapshot_dates_ascending(db_session):
    for i in range(40):
        insert_snapshot(db_session, _make_snapshot(date(2026, 4, 1 + i)))
    db_session.commit()
    dates = get_recent_snapshot_dates(db_session, limit=30)
    assert len(dates) == 30
    assert dates == sorted(dates)
    # Should be the most-recent 30 (last 30 calendar dates inserted).
    expected_first = date(2026, 4, 1 + 40 - 30)
    assert dates[0] == expected_first


def test_count_snapshots_in_window_caps_at_size(db_session):
    """200 snapshots → window_size=90 returns 90 (trading-day cap, L11)."""
    from datetime import timedelta
    base = date(2026, 1, 1)
    for i in range(200):
        insert_snapshot(db_session, _make_snapshot(base + timedelta(days=i)))
    db_session.commit()
    count = count_snapshots_in_window(
        db_session, window_end=base + timedelta(days=199), window_size=90,
    )
    assert count == 90


def test_count_snapshots_in_window_below_cap(db_session):
    """12 snapshots → window_size=90 returns 12."""
    from datetime import timedelta
    base = date(2026, 1, 1)
    for i in range(12):
        insert_snapshot(db_session, _make_snapshot(base + timedelta(days=i)))
    db_session.commit()
    count = count_snapshots_in_window(
        db_session, window_end=base + timedelta(days=11), window_size=90,
    )
    assert count == 12


def test_get_spy_anchor_none_when_no_anchors(db_session):
    # Insert one snapshot with NULL anchor_spy_close.
    insert_snapshot(db_session, _make_snapshot(date(2026, 5, 28)))
    db_session.commit()
    assert get_spy_anchor(db_session) is None


def test_get_spy_anchor_returns_earliest_non_null(db_session):
    """L16: earliest snapshot with non-null anchor_spy_close."""
    from marketpulse.db.models import PaperNavSnapshot

    # Day 1: no SPY (null anchor)
    insert_snapshot(db_session, _make_snapshot(date(2026, 5, 26)))
    # Day 2: SPY available — set anchor_spy_close explicitly
    snap_d2 = _make_snapshot(date(2026, 5, 27))
    insert_snapshot(db_session, snap_d2)
    row = db_session.get(PaperNavSnapshot, date(2026, 5, 27))
    row.anchor_spy_close = Decimal("500")
    row.spy_close = Decimal("500")
    # Day 3: also SPY available
    snap_d3 = _make_snapshot(date(2026, 5, 28))
    insert_snapshot(db_session, snap_d3)
    row3 = db_session.get(PaperNavSnapshot, date(2026, 5, 28))
    row3.anchor_spy_close = Decimal("500")
    row3.spy_close = Decimal("505")
    db_session.commit()

    anchor = get_spy_anchor(db_session)
    assert anchor == Decimal("500")
```

- [ ] **Step 2: Append the helper implementations**

Append to `marketpulse/portfolio/snapshot_repo.py`:

```python
def get_snapshot_series(
    session: Session, *, window_start: date, window_end: date,
) -> list[NavSnapshot]:
    """Inclusive range, ordered by trading_date ascending."""
    rows = session.scalars(
        select(PaperNavSnapshot)
        .where(PaperNavSnapshot.trading_date >= window_start)
        .where(PaperNavSnapshot.trading_date <= window_end)
        .order_by(PaperNavSnapshot.trading_date.asc()),
    ).all()
    return [_row_to_dc(r) for r in rows]


def get_recent_snapshot_dates(
    session: Session, *, limit: int,
) -> list[date]:
    """Most-recent N trading_dates, returned in ASCENDING order."""
    desc_dates = list(session.scalars(
        select(PaperNavSnapshot.trading_date)
        .order_by(PaperNavSnapshot.trading_date.desc())
        .limit(limit),
    ).all())
    return sorted(desc_dates)


def count_snapshots_in_window(
    session: Session, *, window_end: date, window_size: int,
) -> int:
    """L11: count of most-recent snapshot rows with trading_date <= window_end,
    capped at window_size. Trading-day semantics — NEVER calendar."""
    total = session.scalar(
        select(PaperNavSnapshot)
        .where(PaperNavSnapshot.trading_date <= window_end)
        .with_only_columns(
            __import__("sqlalchemy").func.count(PaperNavSnapshot.trading_date),
        ),
    )
    if total is None:
        return 0
    return min(int(total), window_size)


def get_spy_anchor(session: Session) -> Decimal | None:
    """L16: earliest non-null anchor_spy_close in the snapshot table."""
    return session.scalar(
        select(PaperNavSnapshot.anchor_spy_close)
        .where(PaperNavSnapshot.anchor_spy_close.is_not(None))
        .order_by(PaperNavSnapshot.trading_date.asc())
        .limit(1),
    )


def get_earliest_snapshot(session: Session) -> NavSnapshot | None:
    """Used by snapshot_runner to recover anchor_portfolio_nav on every
    subsequent snapshot after the first."""
    row = session.scalars(
        select(PaperNavSnapshot)
        .order_by(PaperNavSnapshot.trading_date.asc())
        .limit(1),
    ).first()
    return _row_to_dc(row) if row is not None else None
```

Note on `count_snapshots_in_window`: replace the inline `__import__` with a proper top-of-file `from sqlalchemy import func` import — add `func` to the existing `from sqlalchemy import select` line so the line reads `from sqlalchemy import func, select`. Then change the function body to:

```python
def count_snapshots_in_window(
    session: Session, *, window_end: date, window_size: int,
) -> int:
    total = session.scalar(
        select(func.count(PaperNavSnapshot.trading_date))
        .where(PaperNavSnapshot.trading_date <= window_end),
    )
    return min(int(total or 0), window_size)
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/portfolio/test_snapshot_repo.py -v`
Expected: PASS — 10 tests total (4 + 6 new).

- [ ] **Step 4: Commit**

```bash
git add marketpulse/portfolio/snapshot_repo.py tests/portfolio/test_snapshot_repo.py
git commit -m "feat(pr3a): snapshot_repo series/recent/count/spy_anchor helpers"
```

---

## Task 6: `snapshot_runner.py` — cash + positions + `NoCashLedgerForDate`

**Files:**
- Create: `marketpulse/portfolio/snapshot_runner.py`
- Create: `tests/portfolio/test_snapshot_runner.py`

- [ ] **Step 1: Write failing tests for cash-ledger + open-position read**

Create `tests/portfolio/test_snapshot_runner.py`:

```python
# Layer: test
"""PR3a — snapshot_runner tests."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from marketpulse.db.models import (
    PaperCashLedger,
    PaperFill,
    PaperOrder,
    PaperPosition,
    PriceCacheEntry,
)
from marketpulse.portfolio.snapshot_repo import get_snapshot
from marketpulse.portfolio.snapshot_runner import (
    NoCashLedgerForDate,
    run_nav_snapshot,
)


def _seed_cash(session, balance: str, ts: datetime, reason: str = "INITIAL_DEPOSIT"):
    row = PaperCashLedger(
        timestamp=ts,
        delta=Decimal(balance),
        reason=reason,
        fill_id=None,
        balance_after=Decimal(balance),
    )
    session.add(row)
    session.flush()


def _seed_price(session, ticker: str, d: date, close: float):
    session.add(PriceCacheEntry(
        ticker=ticker, date=d, open=close, high=close, low=close,
        close=close, volume=1, fetched_at=datetime(d.year, d.month, d.day, tzinfo=UTC),
    ))


def _seed_position(session, *, ticker: str, qty: int, opened: datetime, closed: datetime | None = None):
    # Minimal PaperOrder to satisfy FK; details are irrelevant here.
    order = PaperOrder(
        idempotency_key=f"{ticker}-{opened.isoformat()}",
        strategy="general",
        ticker=ticker,
        quantity=qty,
        event_time=opened,
        allocation_date=opened.date(),
        horizon_date=opened.date() + timedelta(days=7),
        placed_at=opened,
        filled_at=opened,
        cancelled_at=None,
        cancel_reason=None,
        event_price=Decimal("100"),
        horizon_price=None,
        status="ENTRY_FILLED" if closed is None else "EXIT_FILLED",
        strategy_version="v1",
        allocator_version="v1",
        execution_engine_version="v1",
        weight=Decimal("1"),
    )
    session.add(order)
    session.flush()
    pos = PaperPosition(
        order_id=order.id,
        entry_fill_id=None, exit_fill_id=None,
        strategy="general", ticker=ticker, quantity=qty,
        entry_price=Decimal("100"), entry_date=opened.date(),
        horizon_date=opened.date() + timedelta(days=7),
        status="OPEN" if closed is None else "CLOSED",
        opened_at=opened, closed_at=closed,
        exit_price=None if closed is None else Decimal("105"),
        realized_pnl=None if closed is None else Decimal("5"),
    )
    session.add(pos)
    session.flush()
    return pos


def test_run_nav_snapshot_empty_cash_ledger_raises(db_session):
    """L18: no cash ledger row → NoCashLedgerForDate raised."""
    with pytest.raises(NoCashLedgerForDate):
        run_nav_snapshot(db_session, trading_date=date(2026, 5, 28))


def test_run_nav_snapshot_first_run_self_anchors(db_session):
    """Fresh DB, 1 priced position; row created with self-anchor on portfolio."""
    _seed_cash(db_session, "100000", datetime(2026, 5, 28, 13, 0, tzinfo=UTC))
    _seed_position(db_session, ticker="AAPL", qty=10,
                   opened=datetime(2026, 5, 28, 14, 0, tzinfo=UTC))
    _seed_price(db_session, "AAPL", date(2026, 5, 28), 200.0)
    db_session.commit()

    snap = run_nav_snapshot(db_session, trading_date=date(2026, 5, 28))
    db_session.commit()

    assert snap.cash_balance == Decimal("100000")
    assert snap.holdings_mtm == Decimal("2000")
    assert snap.portfolio_nav == Decimal("102000")
    assert snap.anchor_portfolio_nav == Decimal("102000")  # self-anchored
    assert snap.portfolio_index == Decimal("1")
    assert snap.spy_close is None  # no SPY in price_cache
    assert snap.spy_index is None
    assert snap.excess_return is None

    # Persisted
    persisted = get_snapshot(db_session, date(2026, 5, 28))
    assert persisted is not None
    assert persisted.portfolio_nav == Decimal("102000")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/portfolio/test_snapshot_runner.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create the module (partial — cash + positions + assemble basic snapshot)**

Create `marketpulse/portfolio/snapshot_runner.py`:

```python
# Layer: orchestration
"""snapshot_runner — reads forward state, computes NavSnapshot, persists.

Called at the end of paper_trading_tick. L4: persistence errors (non-PK)
propagate; the scheduler catches them and logs. L18: empty cash ledger
raises NoCashLedgerForDate.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketpulse.db.models import (
    PaperCashLedger,
    PaperNavSnapshot,
    PaperPosition,
    PriceCacheEntry,
)
from marketpulse.portfolio.north_star import (
    NORTH_STAR_WINDOW,
    NavSnapshot,
    OpenPosition,
    compute_nav_snapshot,
)
from marketpulse.portfolio.snapshot_repo import (
    SnapshotAlreadyExists,
    count_snapshots_in_window,
    get_earliest_snapshot,
    get_snapshot,
    get_spy_anchor,
    insert_snapshot,
)

log = logging.getLogger(__name__)

_SPY_TICKER = "SPY"


class NoCashLedgerForDate(Exception):
    """L18: paper_cash_ledger has no row with timestamp <= EOD(trading_date)."""


def _eod_utc(trading_date: date) -> datetime:
    """End-of-day in UTC. Paper engine timestamps are TZ-aware UTC."""
    return datetime.combine(trading_date, time.max, tzinfo=UTC)


def _read_cash_balance(session: Session, trading_date: date) -> Decimal:
    eod = _eod_utc(trading_date)
    row = session.scalars(
        select(PaperCashLedger)
        .where(PaperCashLedger.timestamp <= eod)
        .order_by(PaperCashLedger.timestamp.desc())
        .limit(1),
    ).first()
    if row is None:
        raise NoCashLedgerForDate(
            f"no paper_cash_ledger row at or before EOD {trading_date}"
        )
    return row.balance_after


def _read_open_positions(
    session: Session, trading_date: date,
) -> list[OpenPosition]:
    """L7: historical-safe — time predicates only, never status='OPEN'."""
    eod = _eod_utc(trading_date)
    rows = session.scalars(
        select(PaperPosition).where(
            PaperPosition.opened_at <= eod,
            (PaperPosition.closed_at.is_(None))
            | (PaperPosition.closed_at > eod),
        ),
    ).all()
    return [
        OpenPosition(ticker=r.ticker, quantity=Decimal(r.quantity))
        for r in rows
    ]


def _read_price_lookup(session: Session, trading_date: date):
    """L5/L19: price_cache.close as-is; Float → Decimal at the boundary."""
    rows = session.scalars(
        select(PriceCacheEntry).where(PriceCacheEntry.date == trading_date),
    ).all()
    table = {r.ticker: Decimal(str(r.close)) for r in rows}

    def lookup(ticker: str) -> Decimal | None:
        return table.get(ticker)

    return lookup, table.get(_SPY_TICKER)


def run_nav_snapshot(
    session: Session, *, trading_date: date,
) -> NavSnapshot:
    """Read forward state, compute, persist. Returns the snapshot.

    Idempotent re-run: if a snapshot for `trading_date` already exists,
    log + return it WITHOUT recomputing (avoids wasted work AND prevents
    trading_days_observed drift from re-counting a finalized day).

    All non-PK persistence errors propagate (L4). The PK race path
    (concurrent writer) rolls back the half-formed add() and returns
    the row that actually won.
    """
    # Idempotency check FIRST — before any read/compute work.
    existing = get_snapshot(session, trading_date)
    if existing is not None:
        log.warning(
            "nav_snapshot_idempotent_rerun",
            extra={"tick_date": str(trading_date)},
        )
        return existing

    cash_balance = _read_cash_balance(session, trading_date)
    open_positions = _read_open_positions(session, trading_date)
    price_lookup, spy_close = _read_price_lookup(session, trading_date)

    # Portfolio anchor — earliest snapshot's, or self-anchor on first run.
    # L6: self-anchor preview uses the SAME omit-unpriced rule as the pure
    # compute function — never `(price or 0)`. Otherwise the anchor would
    # silently include phantom zero-price MTM and corrupt portfolio_index.
    earliest = get_earliest_snapshot(session)
    if earliest is None:
        portfolio_nav_preview = cash_balance
        for pos in open_positions:
            price = price_lookup(pos.ticker)
            if price is not None:
                portfolio_nav_preview += pos.quantity * price
        anchor_portfolio_nav = portfolio_nav_preview
    else:
        anchor_portfolio_nav = earliest.anchor_portfolio_nav

    # L16: SPY lazy anchor — earliest non-null anchor_spy_close in DB; if
    # none and current SPY is available, current becomes the anchor.
    anchor_spy_close = get_spy_anchor(session)
    if anchor_spy_close is None and spy_close is not None:
        anchor_spy_close = spy_close

    trading_days_observed = count_snapshots_in_window(
        session, window_end=trading_date, window_size=NORTH_STAR_WINDOW,
    ) + 1

    snapshot = compute_nav_snapshot(
        trading_date=trading_date,
        cash_balance=cash_balance,
        open_positions=open_positions,
        price_lookup=price_lookup,
        spy_close=spy_close,
        anchor_portfolio_nav=anchor_portfolio_nav,
        anchor_spy_close=anchor_spy_close,
        trading_days_observed=trading_days_observed,
    )

    try:
        insert_snapshot(session, snapshot)
    except SnapshotAlreadyExists:
        # True race: a concurrent writer landed the row between our
        # get_snapshot() at the top and our flush. Rollback the half-formed
        # add() so the caller's transaction stays clean, then return the
        # row that actually won.
        session.rollback()
        log.warning(
            "nav_snapshot_pk_conflict_race",
            extra={"tick_date": str(trading_date)},
        )
        winning = get_snapshot(session, trading_date)
        assert winning is not None  # PK conflict implies row exists
        return winning

    return snapshot
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/portfolio/test_snapshot_runner.py -v`
Expected: PASS — 2 tests (the empty-ledger test and the first-run self-anchor).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/portfolio/snapshot_runner.py tests/portfolio/test_snapshot_runner.py
git commit -m "feat(pr3a): snapshot_runner cash + positions + first-run self-anchor"
```

---

## Task 7: Historical-safe positions + idempotency + SPY lazy anchor tests

This task adds tests that exercise the Task 6 runner's existing behavior
(historical position reconstruction, idempotent re-run, SPY lazy anchor).
No production-code changes are needed if Task 6 was implemented correctly —
the runner already uses `get_earliest_snapshot` (added in Task 5) and the
idempotency check at the top.

**Files:**
- Modify: `tests/portfolio/test_snapshot_runner.py` — add 3 tests

- [ ] **Step 1: Append failing tests**

Append to `tests/portfolio/test_snapshot_runner.py`:

```python
def test_run_nav_snapshot_historical_open_positions(db_session):
    """L7: time-predicate reconstruction. Position opened day-2 closed day-4;
    rebuild for day-3 includes it, rebuild for day-5 excludes it."""
    _seed_cash(db_session, "100000", datetime(2026, 5, 26, 13, 0, tzinfo=UTC))
    _seed_position(
        db_session, ticker="AAPL", qty=10,
        opened=datetime(2026, 5, 27, 14, 0, tzinfo=UTC),
        closed=datetime(2026, 5, 29, 14, 0, tzinfo=UTC),
    )
    _seed_price(db_session, "AAPL", date(2026, 5, 28), 200.0)
    _seed_price(db_session, "AAPL", date(2026, 5, 30), 200.0)
    db_session.commit()

    snap_d3 = run_nav_snapshot(db_session, trading_date=date(2026, 5, 28))
    db_session.commit()
    assert snap_d3.holdings_mtm == Decimal("2000")  # position still open

    snap_d5 = run_nav_snapshot(db_session, trading_date=date(2026, 5, 30))
    db_session.commit()
    assert snap_d5.holdings_mtm == Decimal("0")  # position already closed


def test_run_nav_snapshot_idempotent_pk_conflict(db_session):
    _seed_cash(db_session, "100000", datetime(2026, 5, 28, 13, 0, tzinfo=UTC))
    db_session.commit()

    snap1 = run_nav_snapshot(db_session, trading_date=date(2026, 5, 28))
    db_session.commit()
    snap2 = run_nav_snapshot(db_session, trading_date=date(2026, 5, 28))
    assert snap2.trading_date == snap1.trading_date
    assert snap2.portfolio_nav == snap1.portfolio_nav


def test_run_nav_snapshot_spy_anchor_late_establishment(db_session):
    """L16: SPY anchor establishes on first SPY-available snapshot."""
    _seed_cash(db_session, "100000", datetime(2026, 5, 26, 13, 0, tzinfo=UTC))
    db_session.commit()

    # Day 1: no SPY in cache → no SPY anchor
    snap_d1 = run_nav_snapshot(db_session, trading_date=date(2026, 5, 26))
    db_session.commit()
    assert snap_d1.anchor_spy_close is None
    assert snap_d1.spy_index is None
    assert snap_d1.excess_return is None

    # Day 2: SPY shows up
    _seed_price(db_session, "SPY", date(2026, 5, 27), 500.0)
    db_session.commit()
    snap_d2 = run_nav_snapshot(db_session, trading_date=date(2026, 5, 27))
    db_session.commit()
    assert snap_d2.anchor_spy_close == Decimal("500")
    assert snap_d2.spy_close == Decimal("500")
    # spy_index = 500/500 = 1
    assert snap_d2.spy_index == Decimal("1")

    # Day 3: SPY moves; anchor stays at 500
    _seed_price(db_session, "SPY", date(2026, 5, 28), 510.0)
    db_session.commit()
    snap_d3 = run_nav_snapshot(db_session, trading_date=date(2026, 5, 28))
    db_session.commit()
    assert snap_d3.anchor_spy_close == Decimal("500")
    assert snap_d3.spy_index == Decimal("510") / Decimal("500")

    # Day 1 row remains frozen with null benchmark side.
    persisted_d1 = get_snapshot(db_session, date(2026, 5, 26))
    assert persisted_d1.anchor_spy_close is None
    assert persisted_d1.spy_index is None
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/portfolio/test_snapshot_runner.py -v`
Expected: PASS — 5 tests total (2 from Task 6 + 3 new).

If any of the 3 new tests fail, the Task 6 runner has an issue with one
of: time-predicate position read (L7), idempotent existence-check, or SPY
lazy anchor (L16). Trace back to those sites in `snapshot_runner.py`.

- [ ] **Step 3: Commit**

```bash
git add tests/portfolio/test_snapshot_runner.py
git commit -m "test(pr3a): historical positions + idempotency + SPY lazy anchor"
```

---

## Task 8: `snapshot_runner.py` — partial pricing + no-network + repo-error propagation

**Files:**
- Modify: `tests/portfolio/test_snapshot_runner.py` — append 3 tests

- [ ] **Step 1: Append the remaining runner tests**

Append:

```python
def test_run_nav_snapshot_partial_pricing(db_session):
    """3 positions, 1 unpriced → unpriced_count=1, MTM reflects only priced."""
    _seed_cash(db_session, "100000", datetime(2026, 5, 28, 13, 0, tzinfo=UTC))
    for ticker in ("AAPL", "GOOGL", "XYZ"):
        _seed_position(
            db_session, ticker=ticker, qty=5,
            opened=datetime(2026, 5, 28, 14, 0, tzinfo=UTC),
        )
    _seed_price(db_session, "AAPL", date(2026, 5, 28), 100.0)
    _seed_price(db_session, "GOOGL", date(2026, 5, 28), 200.0)
    # XYZ intentionally absent.
    db_session.commit()

    snap = run_nav_snapshot(db_session, trading_date=date(2026, 5, 28))
    assert snap.holdings_mtm == Decimal("1500")  # 5*100 + 5*200
    assert snap.unpriced_positions_count == 1
    assert snap.unpriced_tickers == ("XYZ",)


def test_run_nav_snapshot_no_network(db_session, monkeypatch):
    """L5: snapshot runner does NOT touch yfinance. Even if any yfinance
    import is monkeypatched to raise, the snapshot still succeeds."""
    import marketpulse.data.yfinance_client as yf_mod

    def boom(*a, **kw):  # noqa: ANN001
        raise RuntimeError("yfinance must not be called from snapshot path")

    monkeypatch.setattr(yf_mod.YFinanceClient, "__init__", boom, raising=False)

    _seed_cash(db_session, "100000", datetime(2026, 5, 28, 13, 0, tzinfo=UTC))
    db_session.commit()
    snap = run_nav_snapshot(db_session, trading_date=date(2026, 5, 28))
    assert snap.portfolio_nav == Decimal("100000")


def test_run_nav_snapshot_repo_error_propagates(db_session, monkeypatch):
    """L4: non-PK persistence errors are NOT swallowed by the runner."""
    _seed_cash(db_session, "100000", datetime(2026, 5, 28, 13, 0, tzinfo=UTC))
    db_session.commit()

    def boom(session, snapshot):  # noqa: ANN001
        raise RuntimeError("disk full")

    monkeypatch.setattr(
        "marketpulse.portfolio.snapshot_runner.insert_snapshot", boom,
    )
    with pytest.raises(RuntimeError, match="disk full"):
        run_nav_snapshot(db_session, trading_date=date(2026, 5, 28))
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/portfolio/test_snapshot_runner.py -v`
Expected: PASS — 8 tests total.

- [ ] **Step 3: Commit**

```bash
git add tests/portfolio/test_snapshot_runner.py
git commit -m "test(pr3a): partial pricing + no-network + repo error propagation"
```

---

## Task 9: Scheduler hook + tick-isolation test

**Files:**
- Modify: `marketpulse/scheduler/jobs.py` — hook into `run_paper_trading_tick`
- Modify: (or create) a scheduler test verifying tick isolation

- [ ] **Step 1: Add a failing scheduler-isolation test**

First, locate the existing scheduler test file for paper_trading_tick (likely `tests/scheduler/test_paper_trading_tick.py`). If it doesn't exist, create it. Search:

Run: `ls tests/scheduler/ 2>&1`

Then create `tests/scheduler/test_paper_trading_tick_nav_snapshot.py` (new file to keep the existing test file untouched):

```python
# Layer: test
"""PR3a — scheduler-level isolation test for the NAV snapshot hook.

Locks tested: L4 (runner errors visible; tick not aborted).
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal

from marketpulse.db.models import PaperCashLedger


def test_run_nav_snapshot_safely_logs_and_swallows(
    db_session, monkeypatch, caplog,
):
    """Unit test on the wrapper: when run_nav_snapshot raises, the wrapper
    swallows + logs a warning. This proves L4 at the boundary; we DO NOT
    here exercise the full run_paper_trading_tick (that would require
    much more fixture state). Integration of the wrapper into the tick
    is verified separately by the existing scheduler suite still passing
    in Task 13."""
    from marketpulse.scheduler import jobs as jobs_mod

    # Seed enough state that the runner would have succeeded.
    db_session.add(PaperCashLedger(
        timestamp=datetime(2026, 5, 28, 13, 0, tzinfo=UTC),
        delta=Decimal("100000"), reason="INITIAL_DEPOSIT",
        fill_id=None, balance_after=Decimal("100000"),
    ))
    db_session.commit()

    def boom(session, *, trading_date):  # noqa: ANN001
        raise RuntimeError("simulated disk full")

    monkeypatch.setattr(jobs_mod, "run_nav_snapshot", boom)

    caplog.set_level(logging.WARNING)
    # The hook wrapper must not raise. The exact wrapper name is
    # `_run_nav_snapshot_safely` — see jobs.py.
    jobs_mod._run_nav_snapshot_safely(db_session, tick_date=date(2026, 5, 28))

    # And the warning must be emitted.
    assert any(
        "nav_snapshot_failed" in rec.getMessage()
        for rec in caplog.records
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/scheduler/test_paper_trading_tick_nav_snapshot.py -v`
Expected: FAIL with `AttributeError` (no `_run_nav_snapshot_safely`).

- [ ] **Step 3: Add the hook wrapper + call site in `marketpulse/scheduler/jobs.py`**

Open `marketpulse/scheduler/jobs.py`. Add this import alongside the other `marketpulse.*` imports at the top:

```python
from marketpulse.portfolio.snapshot_runner import run_nav_snapshot
```

(The wrapper deliberately takes `run_nav_snapshot` as the imported symbol so the monkeypatch in the test reaches the call site.)

Then add this private helper near other private helpers in the file (top-level, not inside a class):

```python
def _run_nav_snapshot_safely(session, *, tick_date) -> None:
    """PR3a — EOD NAV snapshot. Piggybacks on tick fill settlement.

    L4: only non-PK persistence errors are caught here; PK conflicts are
    handled INSIDE run_nav_snapshot (idempotent re-run). The tick is
    never aborted by snapshot failure.
    """
    try:
        run_nav_snapshot(session, trading_date=tick_date)
    except Exception as exc:  # noqa: BLE001
        # `exception` (not `error`) to avoid collision with stdlib LogRecord
        # fields and most structlog/JSON formatters.
        log.warning(
            "nav_snapshot_failed",
            extra={"tick_date": str(tick_date), "exception": str(exc)},
        )
```

Find the existing `run_paper_trading_tick` function. After the line that emits the `TICK_COMPLETED` audit (the final SUCCESS path, *not* an early return), add a call:

```python
    _run_nav_snapshot_safely(session, tick_date=tick_date)
```

Use the `session` and `tick_date` variables that are already in scope at that point. If `tick_date` is named differently in your tick function (e.g. `today`), use that local name when calling.

- [ ] **Step 4: Run the new test**

Run: `uv run pytest tests/scheduler/test_paper_trading_tick_nav_snapshot.py -v`
Expected: PASS.

- [ ] **Step 5: Run the existing scheduler tests to verify no regression**

Run: `uv run pytest tests/scheduler/ -v`
Expected: PASS — pre-existing scheduler suite still green.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/scheduler/jobs.py tests/scheduler/test_paper_trading_tick_nav_snapshot.py
git commit -m "feat(pr3a): scheduler hook — _run_nav_snapshot_safely after tick"
```

---

## Task 10: `charter_metrics.py` — `build_north_star_section` + Decimal→float (L17)

**Files:**
- Modify: `marketpulse/ops/charter_metrics.py`
- Create: `tests/ops/test_charter_metrics_north_star.py`

- [ ] **Step 1: Write failing tests for the north_star builder**

Create `tests/ops/test_charter_metrics_north_star.py`:

```python
# Layer: test
"""PR3a — charter_metrics north_star + diagnostics extension tests."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from marketpulse.db.models import PaperNavSnapshot
from marketpulse.ops.charter_metrics import build_charter_metrics
from marketpulse.portfolio.north_star import NavSnapshot
from marketpulse.portfolio.snapshot_repo import insert_snapshot


def _snap(d: date, *, value: str = "0.032", observed: int = 12) -> NavSnapshot:
    return NavSnapshot(
        trading_date=d,
        cash_balance=Decimal("100000"),
        holdings_mtm=Decimal("0"),
        portfolio_nav=Decimal("100000"),
        anchor_portfolio_nav=Decimal("100000"),
        portfolio_index=Decimal("1.041"),
        spy_close=Decimal("500"),
        anchor_spy_close=Decimal("475"),
        spy_index=Decimal("1.009"),
        excess_return=Decimal(value),
        trading_days_observed=observed,
        coverage_ratio=Decimal(observed) / Decimal("90"),
        is_sufficient=observed >= 90,
        unpriced_positions_count=0,
        unpriced_tickers=(),
    )


def test_north_star_empty_table(db_session, tmp_path):
    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    ns = result["north_star"]
    assert ns["error"] == "no_snapshots_yet"
    assert ns["value"] is None
    assert ns["coverage_ratio"] == 0
    assert ns["is_sufficient"] is False
    assert ns["data_quality"]["is_complete"] is True


def test_north_star_partial_window(db_session, tmp_path):
    for i in range(12):
        insert_snapshot(db_session, _snap(date(2026, 7, 30) + timedelta(days=i),
                                           observed=i + 1))
    db_session.commit()

    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    ns = result["north_star"]
    assert ns["error"] is None
    assert ns["value"] == 0.032
    assert isinstance(ns["value"], float)  # L17
    assert ns["portfolio_index"] == 1.041
    assert ns["spy_index"] == 1.009
    assert ns["is_sufficient"] is False
    assert ns["trading_days_observed"] == 12
    assert ns["window_start"] == "2026-07-30"
    assert ns["window_end"] == "2026-08-10"
    assert ns["data_quality"]["is_complete"] is True
    assert ns["data_quality"]["unpriced_positions_count"] == 0


def test_north_star_sufficient_window(db_session, tmp_path):
    insert_snapshot(db_session, _snap(date(2026, 8, 14), observed=90))
    db_session.commit()
    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    assert result["north_star"]["is_sufficient"] is True
    assert result["north_star"]["coverage_ratio"] == 1.0


def test_north_star_session_none(tmp_path):
    """L10: session=None → db_session_unavailable fallback."""
    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=None,
    )
    ns = result["north_star"]
    assert ns["error"] == "db_session_unavailable"
    assert ns["value"] is None


def test_north_star_data_quality_is_complete_false(db_session, tmp_path):
    """Snapshot with unpriced positions → is_complete=False."""
    snap = _snap(date(2026, 8, 14), observed=12)
    insert_snapshot(db_session, NavSnapshot(
        **{**snap.__dict__, "unpriced_positions_count": 1,
           "unpriced_tickers": ("XYZ",)},
    ))
    db_session.commit()
    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    dq = result["north_star"]["data_quality"]
    assert dq["is_complete"] is False
    assert dq["unpriced_positions_count"] == 1
    assert dq["unpriced_tickers"] == ["XYZ"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/ops/test_charter_metrics_north_star.py -v`
Expected: FAIL — `build_charter_metrics` doesn't accept `session` kwarg yet, or `north_star` is still `{"status": "not_implemented"}`.

- [ ] **Step 3: Extend `marketpulse/ops/charter_metrics.py`**

At the top of `marketpulse/ops/charter_metrics.py`, add imports:

```python
from datetime import date as _date
from sqlalchemy.orm import Session

from marketpulse.portfolio.snapshot_repo import (
    get_latest_snapshot,
    get_recent_snapshot_dates,
    get_snapshot,
)
```

Add the constant:

```python
NORTH_STAR_METRIC = "paper_portfolio_excess_return_vs_spy_90d"
NORTH_STAR_REQUIRED = 90
DIAGNOSTICS_REQUIRED = 30
```

Add the empty fallback helpers (above `build_charter_metrics`):

```python
def _empty_north_star(*, error: str) -> dict[str, Any]:
    return {
        "metric": NORTH_STAR_METRIC,
        "as_of_trading_date": None,
        "value": None,
        "portfolio_index": None,
        "spy_index": None,
        "trading_days_observed": 0,
        "trading_days_required": NORTH_STAR_REQUIRED,
        "coverage_ratio": 0,
        "is_sufficient": False,
        "window_start": None,
        "window_end": None,
        "data_quality": {
            "unpriced_positions_count": 0,
            "unpriced_tickers": [],
            "is_complete": True,
        },
        "error": error,
    }


def _to_float(value) -> float | None:  # Decimal | None → float | None  # noqa: ANN001
    return None if value is None else float(value)


def build_north_star_section(
    session: Session | None, *, now,
) -> dict[str, Any]:
    """L17: ratios/returns/index → float; money fields are NOT exposed.
    Empty snapshot table → no_snapshots_yet fallback. session=None →
    db_session_unavailable fallback (L10)."""
    if session is None:
        return _empty_north_star(error="db_session_unavailable")

    latest = get_latest_snapshot(session)
    if latest is None:
        return _empty_north_star(error="no_snapshots_yet")

    recent_dates = get_recent_snapshot_dates(session, limit=NORTH_STAR_REQUIRED)
    window_start = recent_dates[0] if recent_dates else None

    return {
        "metric": NORTH_STAR_METRIC,
        "as_of_trading_date": latest.trading_date.isoformat(),
        "value": _to_float(latest.excess_return),
        "portfolio_index": _to_float(latest.portfolio_index),
        "spy_index": _to_float(latest.spy_index),
        "trading_days_observed": latest.trading_days_observed,
        "trading_days_required": NORTH_STAR_REQUIRED,
        "coverage_ratio": _to_float(latest.coverage_ratio),
        "is_sufficient": latest.is_sufficient,
        "window_start": window_start.isoformat() if window_start else None,
        "window_end": latest.trading_date.isoformat(),
        "data_quality": {
            "unpriced_positions_count": latest.unpriced_positions_count,
            "unpriced_tickers": list(latest.unpriced_tickers),
            "is_complete": latest.unpriced_positions_count == 0,
        },
        "error": None,
    }
```

Now update `build_charter_metrics` to accept `session` and call the builder. Find the existing function signature and add `session` kwarg:

```python
def build_charter_metrics(
    *,
    manifest_path: Path,
    now: datetime,
    backup_unavailable_reason: str | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
```

In the function body, replace the `"north_star": {"status": "not_implemented"}` line with:

```python
        "north_star": build_north_star_section(session, now=now),
```

Leave `"diagnostics": {"status": "not_implemented"}` for Task 11.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ops/test_charter_metrics_north_star.py -v`
Expected: PASS — 5 north_star tests.

Also run the pre-existing charter_metrics tests to verify no regression:

Run: `uv run pytest tests/ops/test_charter_metrics.py -v`
Expected: PASS — pre-existing 18 tests still green.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/ops/charter_metrics.py tests/ops/test_charter_metrics_north_star.py
git commit -m "feat(pr3a): build_north_star_section + Decimal→float (L17)"
```

---

## Task 11: `charter_metrics.py` — `build_diagnostics_section` + window from snapshot series (L11)

**Files:**
- Modify: `marketpulse/ops/charter_metrics.py`
- Modify: `tests/ops/test_charter_metrics_north_star.py`

- [ ] **Step 1: Append failing diagnostics tests**

Append to `tests/ops/test_charter_metrics_north_star.py`:

```python
from marketpulse.db.models import PaperAuditEvent, PaperFill, PaperOrder


def _seed_audit(session, *, ts: datetime, event_type: str):
    session.add(PaperAuditEvent(
        timestamp=ts, event_type=event_type,
        order_id=None, strategy=None, reason="", context={},
    ))


def _seed_entry_fill(session, *, ts: datetime, position_id: int = 1):
    # The fill needs a position_id; we also need a paper_order parent for FK.
    order = PaperOrder(
        idempotency_key=f"x-{ts.isoformat()}",
        strategy="general", ticker="AAPL", quantity=1,
        event_time=ts, allocation_date=ts.date(),
        horizon_date=ts.date() + timedelta(days=7),
        placed_at=ts, filled_at=ts, cancelled_at=None,
        cancel_reason=None, event_price=Decimal("100"),
        horizon_price=None, status="ENTRY_FILLED",
        strategy_version="v1", allocator_version="v1",
        execution_engine_version="v1", weight=Decimal("1"),
    )
    session.add(order)
    session.flush()
    session.add(PaperFill(
        order_id=order.id, position_id=position_id, side="ENTRY",
        price=Decimal("100"), quantity=1, filled_at=ts,
        cash_delta=Decimal("-100"), realized_pnl=None,
    ))


def test_diagnostics_empty_audit(db_session, tmp_path):
    insert_snapshot(db_session, _snap(date(2026, 8, 14)))
    db_session.commit()
    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    diag = result["diagnostics"]
    for key in (
        "tick_success_rate_30d",
        "order_rejection_rate_30d",
        "paper_trade_count_30d",
    ):
        assert diag[key]["value"] is None
        assert diag[key]["observations"] == 0
        assert diag[key]["coverage_ratio"] == 0
        assert diag[key]["is_sufficient"] is False


def test_diagnostics_tick_success_rate(db_session, tmp_path):
    # 30 snapshots define the window.
    for i in range(30):
        insert_snapshot(db_session, _snap(
            date(2026, 7, 14) + timedelta(days=i), observed=i + 1,
        ))
    # 28 TICK_COMPLETED + 2 ENGINE_INVARIANT_ERROR inside the window.
    base = datetime(2026, 7, 14, tzinfo=UTC)
    for i in range(28):
        _seed_audit(db_session, ts=base + timedelta(days=i),
                    event_type="TICK_COMPLETED")
    for i in range(2):
        _seed_audit(db_session, ts=base + timedelta(days=28 + i),
                    event_type="ENGINE_INVARIANT_ERROR")
    db_session.commit()

    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    diag = result["diagnostics"]["tick_success_rate_30d"]
    assert diag["value"] == 28 / 30
    assert diag["observations"] == 30
    assert diag["required_observations"] == 30
    assert diag["coverage_ratio"] == 1.0
    assert diag["is_sufficient"] is True


def test_diagnostics_rejection_rate_mutually_exclusive(db_session, tmp_path):
    """L12: denominator = PLACED + REJECTED."""
    for i in range(30):
        insert_snapshot(db_session, _snap(
            date(2026, 7, 14) + timedelta(days=i),
        ))
    base = datetime(2026, 7, 14, tzinfo=UTC)
    for i in range(18):
        _seed_audit(db_session, ts=base + timedelta(days=i),
                    event_type="ORDER_PLACED")
    for i in range(12):
        _seed_audit(db_session, ts=base + timedelta(days=18 + i),
                    event_type="ORDER_REJECTED")
    db_session.commit()

    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    diag = result["diagnostics"]["order_rejection_rate_30d"]
    assert diag["value"] == 12 / 30
    assert diag["observations"] == 30


def test_diagnostics_paper_trade_count_via_fills(db_session, tmp_path):
    """L13: source = paper_fill rows, not audit ORDER_ENTRY_FILLED."""
    for i in range(30):
        insert_snapshot(db_session, _snap(
            date(2026, 7, 14) + timedelta(days=i),
        ))
    base = datetime(2026, 7, 14, tzinfo=UTC)
    # Need a paper_position to satisfy FK on paper_fill.
    from marketpulse.db.models import PaperPosition
    pos = PaperPosition(
        order_id=999, entry_fill_id=None, exit_fill_id=None,
        strategy="general", ticker="AAPL", quantity=1,
        entry_price=Decimal("100"), entry_date=date(2026, 7, 14),
        horizon_date=date(2026, 7, 21), status="OPEN",
        opened_at=base, closed_at=None, exit_price=None, realized_pnl=None,
    )
    # NB: order_id=999 is a dangling reference for test isolation; the FK is
    # plain INTEGER in SQLite. If FK enforcement bites, the order is seeded
    # by _seed_entry_fill below.
    for i in range(5):
        _seed_entry_fill(db_session, ts=base + timedelta(days=i), position_id=999)
    db_session.commit()

    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    diag = result["diagnostics"]["paper_trade_count_30d"]
    assert diag["value"] == 5
    assert diag["observations"] == 30  # 30 snapshot trading dates covered


def test_diagnostics_window_from_snapshot_series(db_session, tmp_path):
    """L11: window = last 30 snapshot trading_dates. Events outside excluded."""
    base_day = date(2026, 6, 1)
    for i in range(40):
        insert_snapshot(db_session, _snap(base_day + timedelta(days=i)))
    # Audit event OUTSIDE the 30-most-recent snapshot window (day 0..9).
    _seed_audit(db_session,
                ts=datetime.combine(base_day, datetime.min.time(), tzinfo=UTC),
                event_type="TICK_COMPLETED")
    # And one INSIDE the window (day 39).
    _seed_audit(db_session,
                ts=datetime.combine(
                    base_day + timedelta(days=39),
                    datetime.min.time(), tzinfo=UTC,
                ),
                event_type="TICK_COMPLETED")
    db_session.commit()

    result = build_charter_metrics(
        manifest_path=tmp_path / "missing.json",
        now=datetime(2026, 8, 15, tzinfo=UTC),
        session=db_session,
    )
    diag = result["diagnostics"]["tick_success_rate_30d"]
    # Only the in-window event should be counted.
    assert diag["value"] == 1.0
```

- [ ] **Step 2: Add `build_diagnostics_section` and wire it into `build_charter_metrics`**

Append helpers to `marketpulse/ops/charter_metrics.py`:

```python
def _empty_diagnostic() -> dict[str, Any]:
    return {
        "value": None,
        "observations": 0,
        "required_observations": DIAGNOSTICS_REQUIRED,
        "coverage_ratio": 0,
        "is_sufficient": False,
    }


def _empty_diagnostics() -> dict[str, Any]:
    return {
        "tick_success_rate_30d": _empty_diagnostic(),
        "order_rejection_rate_30d": _empty_diagnostic(),
        "paper_trade_count_30d": _empty_diagnostic(),
    }


def build_diagnostics_section(
    session: Session | None, *, now,
) -> dict[str, Any]:
    """L11: window = last 30 snapshot trading_dates (or all if fewer).
    L17: ratios → float."""
    if session is None:
        return _empty_diagnostics()

    recent = get_recent_snapshot_dates(session, limit=DIAGNOSTICS_REQUIRED)
    if not recent:
        return _empty_diagnostics()

    from datetime import datetime as _dt, time as _time
    window_start_eod = _dt.combine(recent[0], _time.min, tzinfo=UTC)
    window_end_eod = _dt.combine(recent[-1], _time.max, tzinfo=UTC)
    snapshot_count = len(recent)
    coverage_ratio = min(snapshot_count / DIAGNOSTICS_REQUIRED, 1.0)
    is_sufficient = snapshot_count >= DIAGNOSTICS_REQUIRED

    # 1. tick_success_rate_30d
    from marketpulse.db.models import (
        PaperAuditEvent as _Audit,
        PaperFill as _Fill,
    )
    from sqlalchemy import and_, func as _func, select as _select

    tick_completed = session.scalar(
        _select(_func.count(_Audit.id)).where(
            and_(
                _Audit.event_type == "TICK_COMPLETED",
                _Audit.timestamp >= window_start_eod,
                _Audit.timestamp <= window_end_eod,
            ),
        ),
    ) or 0
    engine_error = session.scalar(
        _select(_func.count(_Audit.id)).where(
            and_(
                _Audit.event_type == "ENGINE_INVARIANT_ERROR",
                _Audit.timestamp >= window_start_eod,
                _Audit.timestamp <= window_end_eod,
            ),
        ),
    ) or 0
    tick_total = tick_completed + engine_error
    tick_dict = _empty_diagnostic()
    if tick_total > 0:
        tick_dict["value"] = tick_completed / tick_total
        tick_dict["observations"] = tick_total
        tick_dict["coverage_ratio"] = min(tick_total / DIAGNOSTICS_REQUIRED, 1.0)
        tick_dict["is_sufficient"] = tick_total >= DIAGNOSTICS_REQUIRED

    # 2. order_rejection_rate_30d (L12: PLACED + REJECTED mutually exclusive)
    placed = session.scalar(
        _select(_func.count(_Audit.id)).where(
            and_(
                _Audit.event_type == "ORDER_PLACED",
                _Audit.timestamp >= window_start_eod,
                _Audit.timestamp <= window_end_eod,
            ),
        ),
    ) or 0
    rejected = session.scalar(
        _select(_func.count(_Audit.id)).where(
            and_(
                _Audit.event_type == "ORDER_REJECTED",
                _Audit.timestamp >= window_start_eod,
                _Audit.timestamp <= window_end_eod,
            ),
        ),
    ) or 0
    decisions = placed + rejected
    rej_dict = _empty_diagnostic()
    if decisions > 0:
        rej_dict["value"] = rejected / decisions
        rej_dict["observations"] = decisions
        rej_dict["coverage_ratio"] = min(decisions / DIAGNOSTICS_REQUIRED, 1.0)
        rej_dict["is_sufficient"] = decisions >= DIAGNOSTICS_REQUIRED

    # 3. paper_trade_count_30d (L13: paper_fill ENTRY rows)
    trade_count = session.scalar(
        _select(_func.count(_Fill.id)).where(
            and_(
                _Fill.side == "ENTRY",
                _Fill.position_id.is_not(None),
                _Fill.filled_at >= window_start_eod,
                _Fill.filled_at <= window_end_eod,
            ),
        ),
    ) or 0
    trade_dict = {
        "value": int(trade_count),
        "observations": snapshot_count,
        "required_observations": DIAGNOSTICS_REQUIRED,
        "coverage_ratio": coverage_ratio,
        "is_sufficient": is_sufficient,
    }

    return {
        "tick_success_rate_30d": tick_dict,
        "order_rejection_rate_30d": rej_dict,
        "paper_trade_count_30d": trade_dict,
    }
```

In `build_charter_metrics`, replace `"diagnostics": {"status": "not_implemented"}` with:

```python
        "diagnostics": build_diagnostics_section(session, now=now),
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/ops/test_charter_metrics_north_star.py -v`
Expected: PASS — 10 tests total (5 from Task 10 + 5 new).

- [ ] **Step 4: Commit**

```bash
git add marketpulse/ops/charter_metrics.py tests/ops/test_charter_metrics_north_star.py
git commit -m "feat(pr3a): build_diagnostics_section + snapshot-trading-date window (L11)"
```

---

## Task 12: Wire route + 5 route tests

**Files:**
- Modify: `marketpulse/web/routes/charter.py`
- Modify: `tests/web/test_charter_route.py`

- [ ] **Step 1: Append 5 failing route tests**

Append to `tests/web/test_charter_route.py`:

```python
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal


def _seed_snapshot(session, d: date, *, value: str = "0.025"):
    from marketpulse.portfolio.north_star import NavSnapshot
    from marketpulse.portfolio.snapshot_repo import insert_snapshot
    insert_snapshot(session, NavSnapshot(
        trading_date=d,
        cash_balance=Decimal("100000"),
        holdings_mtm=Decimal("0"),
        portfolio_nav=Decimal("100000"),
        anchor_portfolio_nav=Decimal("100000"),
        portfolio_index=Decimal("1.025"),
        spy_close=Decimal("500"),
        anchor_spy_close=Decimal("500"),
        spy_index=Decimal("1.000"),
        excess_return=Decimal(value),
        trading_days_observed=12,
        coverage_ratio=Decimal("0.133"),
        is_sufficient=False,
        unpriced_positions_count=0,
        unpriced_tickers=(),
    ))


def test_endpoint_north_star_empty(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/charter-metrics")
    assert r.status_code == 200
    ns = r.json()["north_star"]
    assert ns["error"] == "no_snapshots_yet"
    assert ns["value"] is None


def test_endpoint_north_star_with_snapshot(client, monkeypatch, db_url):
    """Snapshot is seeded via a fresh session against the test DB URL, then
    the endpoint reads it through the FastAPI-managed session."""
    _login(client, monkeypatch)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    engine = create_engine(db_url)
    with Session(engine) as s:
        _seed_snapshot(s, date(2026, 8, 14))
        s.commit()

    r = client.get("/lab/charter-metrics")
    assert r.status_code == 200
    ns = r.json()["north_star"]
    assert ns["error"] is None
    assert ns["value"] == 0.025


def test_endpoint_diagnostics_populated(client, monkeypatch, db_url):
    _login(client, monkeypatch)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from marketpulse.db.models import PaperAuditEvent

    engine = create_engine(db_url)
    with Session(engine) as s:
        # Seed 30 snapshots over 30 days for window establishment.
        for i in range(30):
            _seed_snapshot(s, date(2026, 7, 15) + timedelta(days=i))
        base = datetime(2026, 7, 15, tzinfo=UTC)
        for i in range(15):
            s.add(PaperAuditEvent(
                timestamp=base + timedelta(days=i),
                event_type="TICK_COMPLETED",
                order_id=None, strategy=None, reason="", context={},
            ))
        s.commit()

    r = client.get("/lab/charter-metrics")
    assert r.status_code == 200
    diag = r.json()["diagnostics"]["tick_success_rate_30d"]
    assert diag["value"] == 1.0
    assert diag["observations"] == 15
    assert "coverage_ratio" in diag


def test_endpoint_decimals_serialized_as_floats(client, monkeypatch, db_url):
    """L17: response numeric fields are JSON numbers (float), not Decimal strings."""
    _login(client, monkeypatch)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    engine = create_engine(db_url)
    with Session(engine) as s:
        _seed_snapshot(s, date(2026, 8, 14), value="0.04")
        s.commit()

    r = client.get("/lab/charter-metrics")
    assert r.status_code == 200
    ns = r.json()["north_star"]
    assert type(ns["value"]) is float
    assert type(ns["portfolio_index"]) is float
    assert type(ns["spy_index"]) is float
    assert type(ns["coverage_ratio"]) is float


def test_endpoint_no_network_call(client, monkeypatch, db_url):
    """Read path is DB-only — yfinance must never be touched."""
    _login(client, monkeypatch)
    import marketpulse.data.yfinance_client as yf_mod

    def boom(*a, **kw):  # noqa: ANN001
        raise RuntimeError("yfinance must not be called from endpoint")

    monkeypatch.setattr(yf_mod.YFinanceClient, "__init__", boom, raising=False)

    r = client.get("/lab/charter-metrics")
    assert r.status_code == 200
```

- [ ] **Step 2: Run tests to see current state**

Run: `uv run pytest tests/web/test_charter_route.py -v`
Expected: most new tests FAIL because the route doesn't inject the DB session into `build_charter_metrics` yet.

- [ ] **Step 3: Modify the route to pass `session`**

Open `marketpulse/web/routes/charter.py`. Update the route to inject `db: Session`:

```python
# Layer: web
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session

from marketpulse.config import get_settings
from marketpulse.ops.charter_metrics import build_charter_metrics
from marketpulse.web.deps import get_db, require_auth

router = APIRouter()

_NON_SQLITE_REASON = (
    "sqlite database_url required for backup manifest discovery"
)


@router.get("/lab/charter-metrics")
def lab_charter_metrics(
    _: None = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    settings = get_settings()
    parsed = make_url(settings.database_url)
    now = datetime.now(UTC)

    if not parsed.drivername.startswith("sqlite") or not parsed.database:
        return build_charter_metrics(
            manifest_path=Path("/dev/null"),
            now=now,
            backup_unavailable_reason=_NON_SQLITE_REASON,
            session=db,
        )

    manifest_path = (
        Path(parsed.database).resolve().parent / "backups" / "latest.json"
    )
    return build_charter_metrics(
        manifest_path=manifest_path,
        now=now,
        session=db,
    )
```

- [ ] **Step 4: Run tests again**

Run: `uv run pytest tests/web/test_charter_route.py -v`
Expected: PASS — all route tests (5 pre-existing + 5 new = 10 total).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/web/routes/charter.py tests/web/test_charter_route.py
git commit -m "feat(pr3a): /lab/charter-metrics injects db session + 5 new route tests"
```

---

## Task 13: Final integration — full suite + ruff + smoke + PR

**Files:** none (verification + PR)

- [ ] **Step 1: Run the full pytest suite**

Run: `uv run pytest -x`
Expected: PASS — no regressions.

- [ ] **Step 2: Run ruff**

Run: `uv run ruff check .`
Expected: clean. Fix any issues in the new files only.

- [ ] **Step 3: Local smoke (optional)**

```bash
uv run uvicorn marketpulse.web.main:create_app --factory --port 8000
```

In another shell:

```bash
curl -s -c /tmp/mp-cookies.txt -X POST http://localhost:8000/login -d "password=dev"
curl -sf -b /tmp/mp-cookies.txt http://localhost:8000/lab/charter-metrics | jq '.north_star'
```

Expected: `north_star.error="no_snapshots_yet"` on a fresh dev DB. After running a tick (or hand-seeding a snapshot), the error should clear and `value` should be a float.

- [ ] **Step 4: Push and open PR**

```bash
git push -u origin feat/pr3a-north-star-snapshot
gh pr create --title "feat(pr3a): north-star NAV snapshot — Charter top-3 #1 PR3a" \
  --body "$(cat <<'EOF'
## Summary
- Adds `paper_nav_snapshot` immutable EOD table (Alembic 0014) + `PaperNavSnapshot` ORM.
- New `marketpulse/portfolio/` package: pure `compute_nav_snapshot`, db `snapshot_repo`, orchestration `snapshot_runner`.
- Scheduler hook `_run_nav_snapshot_safely` after `paper_trading_tick`. Snapshot failure is logged; tick is never aborted (L4).
- `/lab/charter-metrics` north_star + diagnostics filled from the snapshot table — JSON schema v1 unchanged, additive only.
- SPY benchmark anchored lazily (L16). Missing prices degrade `data_quality`, not NAV (L6).

Charter top-3 priority #1, PR3a. Spec: `docs/superpowers/specs/2026-05-28-pr3a-north-star-snapshot-design.md` (ef54baa).

## Test Plan
- [x] `pytest tests/migrations/test_0014_paper_nav_snapshot.py` — 3 tests
- [x] `pytest tests/portfolio/test_north_star.py` — 10 tests
- [x] `pytest tests/portfolio/test_snapshot_repo.py` — 10 tests
- [x] `pytest tests/portfolio/test_snapshot_runner.py` — 8 tests
- [x] `pytest tests/scheduler/test_paper_trading_tick_nav_snapshot.py` — 1 test (tick isolation)
- [x] `pytest tests/ops/test_charter_metrics_north_star.py` — 10 tests
- [x] `pytest tests/web/test_charter_route.py` — 10 tests (5 pre-existing + 5 new)
- [x] `pytest -x` — full suite green
- [x] `ruff check .` — clean
- [ ] Post-deploy smoke: `/lab/charter-metrics` → `north_star.error="no_snapshots_yet"` until first tick fires, then `value` becomes a float; `data_quality.is_complete` reflects priced/unpriced state.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Spec → Plan Coverage Map

| Spec lock / requirement | Implemented in |
|---|---|
| L1 immutable normal flow + admin force_replace | Task 4 |
| L2 one row per trading_date PK | Task 1 |
| L3 piggyback on paper_trading_tick | Task 9 |
| L4 runner errors propagate; scheduler catches | Task 8 + Task 9 |
| L5 no network in compute path | Task 8 (`test_run_nav_snapshot_no_network`) |
| L6 missing price → omit (not zero) | Task 3 (`test_compute_nav_unpriced_omitted`) |
| L7 historical EOD time predicates | Task 7 (`test_run_nav_snapshot_historical_open_positions`) |
| L8 pure compute / db-orchestration split | Tasks 2, 4, 6 |
| L9 charter_metrics extensions labeled DB-backed | Task 10 docstring |
| L10 session=None fallback | Task 10 (`test_north_star_session_none`) |
| L11 diagnostics window from snapshot dates | Task 11 (`test_diagnostics_window_from_snapshot_series`) |
| L12 rejection denominator mutually exclusive | Task 11 (`test_diagnostics_rejection_rate_mutually_exclusive`) |
| L13 trade count via paper_fill ENTRY rows | Task 11 (`test_diagnostics_paper_trade_count_via_fills`) |
| L14 quantity Decimal at typed boundary | Task 2 |
| L15 unpriced_tickers dedup + sorted | Task 3 (`test_compute_nav_unpriced_tickers_dedup_sorted`) |
| L16 SPY lazy anchor | Task 7 (`test_run_nav_snapshot_spy_anchor_late_establishment`) |
| L17 Decimal → float JSON | Task 10 (`test_endpoint_decimals_serialized_as_floats`) + 12 |
| L18 empty cash ledger → NoCashLedgerForDate | Task 6 (`test_run_nav_snapshot_empty_cash_ledger_raises`) |
| L19 price source contract (price_cache.close as-is) | Task 6 (`_read_price_lookup`) |
| L20 unpriced_tickers TEXT encode/decode | Task 4 (`_encode_tickers` / `_decode_tickers`) |
| data_quality.is_complete | Task 10 (`test_north_star_data_quality_is_complete_false`) |
| Schema v1 stays | Task 10 (additive contract change) |
| Full suite + ruff + smoke | Task 13 |
