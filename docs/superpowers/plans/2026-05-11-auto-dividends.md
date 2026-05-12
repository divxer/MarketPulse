# Auto-Detect Dividends + Tencent Primary Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-detect cash dividends from Tencent's `Usfqkline` endpoint and switch the existing `detect_corporate_actions` daily job to use Tencent as primary source for **both** splits and dividends, with yfinance as fallback. Compute `Dividend.total_amount` correctly using a new `quantity_as_of` helper derived from the Trade+StockSplit timeline.

**Architecture:** Extract `_walk_events` from `recompute_ticker` so both `recompute_ticker` and the new `quantity_as_of(session, ticker, as_of)` can share the chronological event-walk logic. Add `TencentClient.fetch_corporate_actions` (parses `FHcontent` / `hgcgContent` from the same K-line payload `fetch_history` already uses) and `YFinanceClient.fetch_dividends`. Rewrite the daily scheduler job to try Tencent first, fall back to yfinance, record both event types in one pass. Add `source` column + `UniqueConstraint` + `CheckConstraint` to the `Dividend` model via Alembic 0008.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 (Mapped/mapped_column), Alembic (batch_alter_table for SQLite), APScheduler, httpx for Tencent, yfinance for fallback, pytest with mocks. Spec at `docs/superpowers/specs/2026-05-11-auto-dividends-design.md`.

---

## File Structure

**New files:**

| File | Responsibility |
|---|---|
| `marketpulse/holdings/quantity_history.py` | `quantity_as_of(session, ticker, as_of)` — historical-snapshot helper |
| `alembic/versions/0008_dividends_source_and_unique.py` | Migration adding `source`, dedup, UNIQUE + CHECK |
| `tests/unit/test_quantity_history.py` | Behavior tests for `quantity_as_of` |
| `tests/unit/test_tencent_corporate_actions.py` | Parser + endpoint tests (httpx mocked) |
| `tests/unit/test_yfinance_dividends.py` | Mirror of `test_yfinance_splits.py` |

**Modified files:**

| File | Change |
|---|---|
| `marketpulse/db/models.py` | `Dividend` adds `source`, `UniqueConstraint`, `CheckConstraint` |
| `marketpulse/holdings/dividends.py` | `record_dividend` gains `source` param + `IntegrityError` → `DividendError("already recorded")`; new `delete_dividend` |
| `marketpulse/holdings/trades.py` | Extract `_walk_events` shared helper; `recompute_ticker` becomes a thin wrapper |
| `marketpulse/data/tencent_client.py` | New `fetch_corporate_actions` method + `CorporateActions` dataclass |
| `marketpulse/data/yfinance_client.py` | New `fetch_dividends` method |
| `marketpulse/scheduler/jobs.py` | Rewrite `run_detect_corporate_actions` with Tencent primary + yfinance fallback + dividend recording |
| `tests/unit/test_dividends.py` | Extend for `source`, duplicate handling, `delete_dividend` |
| `tests/unit/test_scheduler_jobs.py` | Rewrite split-only tests for the new Tencent+yfinance combined flow |
| `tests/integration/test_trades.py` | Regression test: `recompute_ticker` unchanged after `_walk_events` extraction |

---

## Task 1: Add `source` + UNIQUE + CHECK to `Dividend` model

**Files:**
- Modify: `marketpulse/db/models.py`
- Test: `tests/unit/test_models.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_models.py`:

```python
def test_dividend_source_field_default(db_session) -> None:
    """Dividend.source defaults to 'manual' when not specified."""
    from datetime import date
    from marketpulse.db.models import Dividend

    d = Dividend(ticker="TQQQ", ex_date=date(2025, 9, 24),
                 amount_per_share=0.10, total_amount=2.00)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    assert d.source == "manual"


def test_dividend_unique_constraint(db_session) -> None:
    """(ticker, ex_date) must be unique to support idempotent auto-record."""
    from datetime import date
    from sqlalchemy.exc import IntegrityError
    from marketpulse.db.models import Dividend

    db_session.add(Dividend(ticker="TQQQ", ex_date=date(2025, 9, 24),
                            amount_per_share=0.10, total_amount=2.00))
    db_session.commit()
    db_session.add(Dividend(ticker="TQQQ", ex_date=date(2025, 9, 24),
                            amount_per_share=0.12, total_amount=2.40))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_dividend_check_constraint_rejects_negative_amounts(db_session) -> None:
    """DB-level CHECK rejects negative amount_per_share or total_amount."""
    from datetime import date
    from sqlalchemy.exc import IntegrityError
    from marketpulse.db.models import Dividend

    # Negative per-share
    db_session.add(Dividend(ticker="X", ex_date=date(2025, 1, 1),
                            amount_per_share=-0.10, total_amount=1.0))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Negative total
    db_session.add(Dividend(ticker="X", ex_date=date(2025, 1, 1),
                            amount_per_share=0.10, total_amount=-1.0))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/unit/test_models.py::test_dividend_source_field_default tests/unit/test_models.py::test_dividend_unique_constraint tests/unit/test_models.py::test_dividend_check_constraint_rejects_negative_amounts -v`

Expected: FAIL — `source` attribute doesn't exist, constraints missing.

- [ ] **Step 3: Update the model**

In `marketpulse/db/models.py`, find the `Dividend` class (around line 93). Replace its `__table_args__` and add a `source` column. The full updated class:

```python
class Dividend(Base):
    """Cash dividend received on a held position. Separate from Trade because
    dividends don't change share count or cost basis — they're income only.
    """
    __tablename__ = "dividends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Per-share payout; total = per_share * shares held at record date.
    # Both are stored explicitly so we can round-trip the original 腾讯自选股 entry.
    amount_per_share: Mapped[float] = mapped_column(Float, nullable=False)
    total_amount: Mapped[float] = mapped_column(Float, nullable=False)
    # Source of this dividend: "manual" | "tencent" | "yfinance" | "import".
    # Lets reconciliation prefer one over another and helps debug data origin.
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_dividends_ticker_ex_date", "ticker", "ex_date"),
        UniqueConstraint("ticker", "ex_date", name="uq_dividends_ticker_date"),
        CheckConstraint(
            "amount_per_share >= 0 AND total_amount >= 0",
            name="ck_dividends_amounts_non_negative",
        ),
    )
```

`CheckConstraint` and `UniqueConstraint` are already in the file's import block from the prior splits work — verify they are, no import changes needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_models.py -v`

Expected: PASS (all model tests including the 3 new ones).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/db/models.py tests/unit/test_models.py
git commit -m "feat(dividends): add source field + unique + check constraints"
```

---

## Task 2: Alembic migration 0008

**Files:**
- Create: `alembic/versions/0008_dividends_source_and_unique.py`

- [ ] **Step 1: Write the migration**

Create `alembic/versions/0008_dividends_source_and_unique.py`:

```python
"""add dividends source + unique constraint + check

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-11
"""
import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add source column with server_default so existing rows get "manual".
    op.add_column(
        "dividends",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
    )

    # 2. Defensive dedup — older versions allowed duplicate (ticker, ex_date).
    # Keep the row with the smallest id (oldest by insert order).
    op.execute("""
        DELETE FROM dividends WHERE id NOT IN (
            SELECT MIN(id) FROM dividends GROUP BY ticker, ex_date
        )
    """)

    # 3. Add UNIQUE + CHECK. SQLite requires batch mode for ALTER TABLE ADD CONSTRAINT.
    with op.batch_alter_table("dividends") as batch:
        batch.create_unique_constraint(
            "uq_dividends_ticker_date", ["ticker", "ex_date"],
        )
        batch.create_check_constraint(
            "ck_dividends_amounts_non_negative",
            "amount_per_share >= 0 AND total_amount >= 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("dividends") as batch:
        batch.drop_constraint("ck_dividends_amounts_non_negative", type_="check")
        batch.drop_constraint("uq_dividends_ticker_date", type_="unique")
    op.drop_column("dividends", "source")
```

- [ ] **Step 2: Round-trip the migration on a scratch DB**

```bash
source .venv/bin/activate
SCRATCH="$(mktemp -d)/scratch.db"
DATABASE_URL="sqlite:///$SCRATCH" alembic upgrade head
DATABASE_URL="sqlite:///$SCRATCH" alembic downgrade 0007
DATABASE_URL="sqlite:///$SCRATCH" alembic upgrade head
```

Expected: every command exits 0. After the final `upgrade head`, querying the schema with `sqlite3 "$SCRATCH" ".schema dividends"` should show the `source` column with default `'manual'`, the `uq_dividends_ticker_date` unique constraint, and the `ck_dividends_amounts_non_negative` check constraint.

- [ ] **Step 3: Verify dedup logic with a populated scratch DB**

```bash
SCRATCH2="$(mktemp -d)/scratch2.db"
DATABASE_URL="sqlite:///$SCRATCH2" alembic upgrade 0007

# Seed with duplicate dividends (allowed under 0007).
python -c "
from marketpulse.db import base as db_base
from marketpulse.db.models import Dividend
from datetime import date
db_base.init_engine('sqlite:///$SCRATCH2')
gen = db_base.session_scope()
s = next(gen)
s.add_all([
    Dividend(ticker='TQQQ', ex_date=date(2024, 3, 20), amount_per_share=0.22, total_amount=6.02),
    Dividend(ticker='TQQQ', ex_date=date(2024, 3, 20), amount_per_share=0.22, total_amount=6.02),
    Dividend(ticker='TQQQ', ex_date=date(2024, 6, 26), amount_per_share=0.28, total_amount=24.88),
])
s.commit()
print('seeded 3 rows (2 are duplicates)')
"

DATABASE_URL="sqlite:///$SCRATCH2" alembic upgrade head

python -c "
from marketpulse.db import base as db_base
from marketpulse.db.models import Dividend
db_base.reset_engine()
db_base.init_engine('sqlite:///$SCRATCH2')
gen = db_base.session_scope()
s = next(gen)
rows = s.query(Dividend).all()
assert len(rows) == 2, f'expected 2 rows after dedup, got {len(rows)}'
assert all(d.source == 'manual' for d in rows)
print('✓ dedup correct; 2 rows remain, both source=manual')
"
```

Expected: `✓ dedup correct; 2 rows remain, both source=manual`.

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/0008_dividends_source_and_unique.py
git commit -m "feat(dividends): alembic migration 0008 (source, dedup, unique, check)"
```

---

## Task 3: Service-layer changes — `source` parameter, `IntegrityError` handling, `delete_dividend`

**Files:**
- Modify: `marketpulse/holdings/dividends.py`
- Test: `tests/unit/test_dividends.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_dividends.py`:

```python
def test_record_dividend_persists_source(db_session) -> None:
    """Non-default source is persisted and round-trips."""
    d = record_dividend(
        db_session, ticker="TQQQ", ex_date=date(2025, 9, 24),
        amount_per_share=0.10, total_amount=2.00, source="tencent",
    )
    assert d.source == "tencent"


def test_record_dividend_duplicate_raises(db_session) -> None:
    """(ticker, ex_date) duplicate → DividendError 'already recorded'."""
    record_dividend(
        db_session, ticker="TQQQ", ex_date=date(2025, 9, 24),
        amount_per_share=0.10, total_amount=2.00,
    )
    with pytest.raises(DividendError, match="already recorded"):
        record_dividend(
            db_session, ticker="TQQQ", ex_date=date(2025, 9, 24),
            amount_per_share=0.12, total_amount=2.40,
        )


def test_record_dividend_session_clean_after_duplicate(db_session) -> None:
    """After a duplicate raises, the session must still be usable."""
    record_dividend(
        db_session, ticker="TQQQ", ex_date=date(2025, 9, 24),
        amount_per_share=0.10, total_amount=2.00,
    )
    with pytest.raises(DividendError, match="already recorded"):
        record_dividend(
            db_session, ticker="TQQQ", ex_date=date(2025, 9, 24),
            amount_per_share=0.12, total_amount=2.40,
        )
    # Different ex_date — must succeed.
    d = record_dividend(
        db_session, ticker="TQQQ", ex_date=date(2025, 12, 24),
        amount_per_share=0.09, total_amount=3.42,
    )
    assert d.id is not None


def test_delete_dividend_returns_ticker(db_session) -> None:
    from marketpulse.holdings.dividends import delete_dividend

    d = record_dividend(
        db_session, ticker="TQQQ", ex_date=date(2025, 9, 24),
        amount_per_share=0.10, total_amount=2.00,
    )
    t = delete_dividend(db_session, d.id)
    assert t == "TQQQ"
    assert total_dividends(db_session, ticker="TQQQ") == 0


def test_delete_dividend_missing_raises(db_session) -> None:
    from marketpulse.holdings.dividends import delete_dividend

    with pytest.raises(DividendError, match="not found"):
        delete_dividend(db_session, 9999)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_dividends.py -v -k "source or duplicate or session_clean or delete"`

Expected: FAIL (`source` parameter not yet accepted; `delete_dividend` doesn't exist; `IntegrityError` not converted).

- [ ] **Step 3: Implement service changes**

Replace the contents of `marketpulse/holdings/dividends.py` with:

```python
"""Dividend tracking.

Cash dividends are not Trades — they don't change share count or cost basis.
This module keeps them in their own table and exposes simple aggregates used
by the /holdings dashboard ("累计分红" KPI, monthly dividend rollup).
"""
from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marketpulse.db.models import Dividend


class DividendError(ValueError):
    """Raised on invalid dividend input or duplicate (ticker, ex_date)."""


def record_dividend(
    session: Session,
    *,
    ticker: str,
    ex_date: date,
    amount_per_share: float,
    total_amount: float,
    source: str = "manual",
    notes: str | None = None,
) -> Dividend:
    """Persist a dividend payout. Commits within. Raises DividendError on
    invalid input or duplicate (ticker, ex_date).
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise DividendError("ticker is required")
    if amount_per_share < 0:
        raise DividendError("amount_per_share cannot be negative")
    if total_amount < 0:
        raise DividendError("total_amount cannot be negative")

    div = Dividend(
        ticker=ticker,
        ex_date=ex_date,
        amount_per_share=amount_per_share,
        total_amount=total_amount,
        source=source,
        notes=notes or None,
    )
    session.add(div)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise DividendError(
            f"dividend already recorded for {ticker} on {ex_date}",
        ) from exc
    session.refresh(div)
    return div


def delete_dividend(session: Session, dividend_id: int) -> str:
    """Delete a dividend by id. Returns the affected ticker. Raises
    DividendError if not found.
    """
    div = session.query(Dividend).filter(Dividend.id == dividend_id).one_or_none()
    if not div:
        raise DividendError(f"dividend {dividend_id} not found")
    ticker = div.ticker
    session.delete(div)
    session.commit()
    return ticker


def total_dividends(session: Session, *, ticker: str | None = None) -> float:
    """Sum of all dividends received (optionally filtered by ticker)."""
    q = session.query(Dividend)
    if ticker:
        q = q.filter(Dividend.ticker == ticker.upper())
    return sum(d.total_amount for d in q.all())


def per_ticker_dividends(session: Session) -> dict[str, float]:
    """Map of ticker → total dividends received for that ticker."""
    out: dict[str, float] = defaultdict(float)
    for d in session.query(Dividend).all():
        out[d.ticker] += d.total_amount
    return dict(out)


def monthly_dividends(session: Session) -> list[dict[str, Any]]:
    """Aggregate dividends by (year, month). Same shape as monthly_realized_pl
    so the UI can stack them on the same histogram.
    """
    buckets: dict[str, float] = defaultdict(float)
    for d in session.query(Dividend).all():
        key = f"{d.ex_date.year:04d}-{d.ex_date.month:02d}"
        buckets[key] += d.total_amount
    return [
        {"month": m, "amount": amt}
        for m, amt in sorted(buckets.items())
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_dividends.py -v`

Expected: PASS — all original dividend tests still pass plus the 5 new ones.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/holdings/dividends.py tests/unit/test_dividends.py
git commit -m "feat(dividends): record_dividend gains source param + duplicate handling; add delete_dividend"
```

---

## Task 4: Extract `_walk_events` from `recompute_ticker`

**Files:**
- Modify: `marketpulse/holdings/trades.py`
- Test: `tests/integration/test_trades.py` (regression — must still pass unchanged)

- [ ] **Step 1: Refactor `recompute_ticker` to use a shared helper**

In `marketpulse/holdings/trades.py`, replace the existing `recompute_ticker` function with the refactor below. The shared helper `_walk_events` is internal to the module (underscore prefix); `quantity_history.py` will import it.

```python
def _walk_events(
    session: Session, ticker: str, *, until: date | None = None,
) -> tuple[float, float, list[Trade]]:
    """Walk Trade + StockSplit events for `ticker` in chronological order.

    Returns (final_quantity, final_avg_cost, processed_trades). The trades
    list is needed by recompute_ticker to set realized_pl on sells;
    callers that only need qty can ignore it.

    If `until` is provided, stops including events whose `when` date is
    strictly after `until`. Splits are EOD-anchored, so a split with
    ex_date == until is INCLUDED (the holding state at end-of-day `until`
    reflects the split).

    Trade rows are mutated (realized_pl on sells) — the caller decides
    whether to session.commit().
    """
    ticker = ticker.strip().upper()
    trades = (
        session.query(Trade)
        .filter(Trade.ticker == ticker)
        .order_by(Trade.executed_at.asc().nulls_last(), Trade.created_at.asc())
        .all()
    )
    splits = (
        session.query(StockSplit)
        .filter(StockSplit.ticker == ticker)
        .order_by(StockSplit.ex_date.asc())
        .all()
    )

    def _trade_when(t: Trade) -> datetime:
        """Trades without an executed_at sort last — matches old SQL NULLS LAST."""
        return t.executed_at if t.executed_at else _NULL_EXECUTED_AT_SENTINEL

    events: list[tuple[datetime, int, str, Trade | StockSplit]] = []
    for t in trades:
        events.append((_trade_when(t), 0, "trade", t))
    for s in splits:
        events.append((datetime.combine(s.ex_date, _EOD), 1, "split", s))
    events.sort(key=lambda x: (x[0], x[1]))

    # If `until` is set, stop at the last event whose date is <= until.
    if until is not None:
        cutoff = datetime.combine(until, _EOD)
        events = [e for e in events if e[0] <= cutoff]

    qty = 0.0
    avg_cost = 0.0
    processed: list[Trade] = []
    for _when, _order, kind, evt in events:
        if kind == "trade":
            t = evt
            if t.action == "buy":
                new_qty = qty + t.quantity
                total_cost = qty * avg_cost + t.quantity * t.price + t.fees
                avg_cost = total_cost / new_qty if new_qty else 0
                qty = new_qty
                t.realized_pl = None
            else:  # sell
                t.realized_pl = (t.price - avg_cost) * t.quantity - t.fees
                qty -= t.quantity
                # avg_cost unchanged on partial sell
            processed.append(t)
        else:  # split
            s = evt
            qty = qty * s.ratio
            avg_cost = avg_cost / s.ratio

    return qty, avg_cost, processed


def recompute_ticker(session: Session, ticker: str) -> None:
    """Rebuild Holding row + realized_pl values from the full Trade + StockSplit
    history for ticker.

    Thin wrapper over `_walk_events` — the heavy lifting (chronological
    merge, EOD anchoring, realized_pl recompute) lives there so
    `quantity_as_of` can share the walk without re-implementing it.
    """
    ticker = ticker.strip().upper()
    qty, avg_cost, _ = _walk_events(session, ticker)

    holding = session.query(Holding).filter(Holding.ticker == ticker).one_or_none()
    if qty <= _EPSILON:
        if holding:
            session.delete(holding)
    elif holding:
        holding.quantity = qty
        holding.avg_cost = avg_cost
    else:
        session.add(Holding(ticker=ticker, quantity=qty, avg_cost=avg_cost))

    session.commit()
```

Update the imports at the top of `marketpulse/holdings/trades.py` — `date` is now needed for the `until` parameter:

```python
from datetime import UTC, date, datetime, time
```

- [ ] **Step 2: Run the existing integration tests as a regression check**

Run: `python -m pytest tests/integration/test_trades.py -v`

Expected: PASS — all 21 trade integration tests (including the 7 splits-aware ones from PR #1) must still pass. If any fails, the refactor introduced a behavior change. Compare the old `recompute_ticker` against `_walk_events` + new wrapper and re-align.

- [ ] **Step 3: Commit**

```bash
git add marketpulse/holdings/trades.py
git commit -m "refactor(trades): extract _walk_events helper from recompute_ticker"
```

---

## Task 5: `quantity_as_of` helper

**Files:**
- Create: `marketpulse/holdings/quantity_history.py`
- Create: `tests/unit/test_quantity_history.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_quantity_history.py`:

```python
from datetime import UTC, date, datetime

import pytest

from marketpulse.holdings.quantity_history import quantity_as_of
from marketpulse.holdings.splits import record_split
from marketpulse.holdings.trades import record_trade


def test_qty_zero_when_never_held(db_session) -> None:
    assert quantity_as_of(db_session, "NEVER", date(2025, 1, 1)) == 0


def test_qty_after_single_buy(db_session) -> None:
    record_trade(db_session, ticker="X", action="buy", quantity=20, price=30,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    assert quantity_as_of(db_session, "X", date(2024, 1, 15)) == 20
    assert quantity_as_of(db_session, "X", date(2024, 1, 14)) == 0  # day before buy


def test_qty_after_buy_sell_sequence(db_session) -> None:
    record_trade(db_session, ticker="X", action="buy", quantity=20, price=30,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    record_trade(db_session, ticker="X", action="sell", quantity=8, price=40,
                 executed_at=datetime(2024, 6, 1, tzinfo=UTC))
    assert quantity_as_of(db_session, "X", date(2024, 1, 15)) == 20
    assert quantity_as_of(db_session, "X", date(2024, 5, 31)) == 20
    assert quantity_as_of(db_session, "X", date(2024, 6, 1)) == 12
    assert quantity_as_of(db_session, "X", date(2025, 1, 1)) == 12


def test_qty_after_split_doubles(db_session) -> None:
    """1:2 forward split doubles the snapshot starting on ex_date."""
    record_trade(db_session, ticker="X", action="buy", quantity=20, price=30,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    record_split(db_session, ticker="X", ex_date=date(2025, 6, 1), ratio=2.0)
    # Split is EOD-anchored; on ex_date the qty reflects the split.
    assert quantity_as_of(db_session, "X", date(2025, 5, 31)) == 20
    assert quantity_as_of(db_session, "X", date(2025, 6, 1)) == 40
    assert quantity_as_of(db_session, "X", date(2026, 1, 1)) == 40


def test_qty_full_sale_then_zero(db_session) -> None:
    """Selling 100% leaves qty == 0 after the sell date."""
    record_trade(db_session, ticker="X", action="buy", quantity=10, price=30,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    record_trade(db_session, ticker="X", action="sell", quantity=10, price=50,
                 executed_at=datetime(2024, 12, 1, tzinfo=UTC))
    assert quantity_as_of(db_session, "X", date(2024, 6, 1)) == 10
    assert quantity_as_of(db_session, "X", date(2024, 12, 1)) == 0
    assert quantity_as_of(db_session, "X", date(2025, 12, 1)) == 0


def test_qty_split_then_partial_sell(db_session) -> None:
    """Split first, then sell — snapshot reflects post-split qty minus sold."""
    record_trade(db_session, ticker="X", action="buy", quantity=20, price=60,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    record_split(db_session, ticker="X", ex_date=date(2024, 6, 1), ratio=2.0)
    record_trade(db_session, ticker="X", action="sell", quantity=15, price=35,
                 executed_at=datetime(2024, 9, 1, tzinfo=UTC))
    # Post-split: 40 shares. After selling 15: 25.
    assert quantity_as_of(db_session, "X", date(2024, 1, 15)) == 20
    assert quantity_as_of(db_session, "X", date(2024, 6, 1)) == 40
    assert quantity_as_of(db_session, "X", date(2024, 8, 31)) == 40
    assert quantity_as_of(db_session, "X", date(2024, 9, 1)) == 25


def test_qty_same_day_buy_counted(db_session) -> None:
    """A buy at 09:30 on as_of date IS included in the snapshot
    (same-day buy already settled by end-of-day)."""
    record_trade(db_session, ticker="X", action="buy", quantity=20, price=30,
                 executed_at=datetime(2024, 1, 15, 9, 30, tzinfo=UTC))
    assert quantity_as_of(db_session, "X", date(2024, 1, 15)) == 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_quantity_history.py -v`

Expected: FAIL — `marketpulse.holdings.quantity_history` doesn't exist.

- [ ] **Step 3: Implement the helper**

Create `marketpulse/holdings/quantity_history.py`:

```python
"""Historical-snapshot helper for holdings.

`quantity_as_of(session, ticker, as_of)` walks the merged Trade + StockSplit
timeline up to end-of-`as_of` and returns the share count at that point. Used
by the dividend auto-detection job to compute `total_amount` correctly
without needing to call recompute_ticker (which mutates state) just to read
a historical qty.

Shares the `_walk_events` helper with `marketpulse.holdings.trades` so the
chronological ordering and split-anchor logic stays in one place.
"""
from datetime import date

from sqlalchemy.orm import Session

from marketpulse.holdings.trades import _walk_events


def quantity_as_of(session: Session, ticker: str, as_of: date) -> float:
    """Return share quantity held at end-of-day `as_of`, derived from all
    Trade and StockSplit events for `ticker` whose chronological time is
    <= end-of-`as_of`.

    Returns 0.0 if the ticker was never held or fully sold before as_of.
    Read-only — no DB writes, no recompute side-effect.
    """
    qty, _avg_cost, _processed = _walk_events(session, ticker, until=as_of)
    return qty
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_quantity_history.py -v`

Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/holdings/quantity_history.py tests/unit/test_quantity_history.py
git commit -m "feat(holdings): quantity_as_of helper using shared _walk_events"
```

---

## Task 6: `YFinanceClient.fetch_dividends`

**Files:**
- Modify: `marketpulse/data/yfinance_client.py`
- Create: `tests/unit/test_yfinance_dividends.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_yfinance_dividends.py`:

```python
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd


def test_fetch_dividends_returns_list_of_date_amount_tuples() -> None:
    from marketpulse.data.yfinance_client import YFinanceClient

    fake_series = pd.Series(
        data=[0.22, 0.28, 0.10],
        index=pd.to_datetime([
            "2024-03-20 00:00:00-05:00",
            "2024-06-26 00:00:00-05:00",
            "2025-09-24 00:00:00-04:00",
        ]),
    )

    fake_ticker = MagicMock()
    fake_ticker.dividends = fake_series

    with patch("marketpulse.data.yfinance_client.yf.Ticker", return_value=fake_ticker):
        out = YFinanceClient().fetch_dividends("TQQQ")

    assert out == [
        (date(2024, 3, 20), 0.22),
        (date(2024, 6, 26), 0.28),
        (date(2025, 9, 24), 0.10),
    ]


def test_fetch_dividends_empty_returns_empty_list() -> None:
    from marketpulse.data.yfinance_client import YFinanceClient

    fake_ticker = MagicMock()
    fake_ticker.dividends = pd.Series(dtype=float)

    with patch("marketpulse.data.yfinance_client.yf.Ticker", return_value=fake_ticker):
        assert YFinanceClient().fetch_dividends("NODIV") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_yfinance_dividends.py -v`

Expected: FAIL — `YFinanceClient` has no `fetch_dividends`.

- [ ] **Step 3: Implement `fetch_dividends`**

In `marketpulse/data/yfinance_client.py`, add this method to the `YFinanceClient` class immediately after `fetch_splits`:

```python
    @_retry
    def fetch_dividends(self, ticker: str) -> list[tuple[date, float]]:
        """Return historical cash dividends for a ticker as (ex_date, amount_per_share).

        Returns an empty list if yfinance has no dividend history. Network and
        rate-limit errors propagate through `_retry`. Mirrors `fetch_splits` so
        the scheduler can swap sources cleanly when Tencent is unavailable.
        """
        s = yf.Ticker(ticker).dividends
        if s is None or s.empty:
            return []
        out: list[tuple[date, float]] = []
        for ts, amount in s.items():
            try:
                d = ts.date()
            except AttributeError:
                d = datetime.fromisoformat(str(ts)).date()
            out.append((d, float(amount)))
        return out
```

(No new imports needed — `date` and `datetime` are already imported by the file's existing splits method.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_yfinance_dividends.py -v`

Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/data/yfinance_client.py tests/unit/test_yfinance_dividends.py
git commit -m "feat(yfinance): fetch_dividends method (fallback source)"
```

---

## Task 7: `TencentClient.fetch_corporate_actions`

**Files:**
- Modify: `marketpulse/data/tencent_client.py`
- Create: `tests/unit/test_tencent_corporate_actions.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_tencent_corporate_actions.py`:

```python
import json
from datetime import date
from unittest.mock import MagicMock, patch


def _make_envelope(rows_by_symbol: dict[str, list[list]]) -> str:
    """Build a Tencent fqkline-style JSON envelope for one symbol."""
    sym, rows = next(iter(rows_by_symbol.items()))
    return json.dumps({
        "code": 0,
        "msg": "",
        "data": {sym: {"qfqday": rows}},
    })


def test_parse_dividend_row() -> None:
    """A row with FHcontent populated yields a dividend entry."""
    from marketpulse.data.tencent_client import TencentClient

    body = _make_envelope({
        "usAAPL.OQ": [
            ["2026-02-10", "228", "229", "230", "227", "30000000",
             {"FHcontent": "每股分配0.25美元", "hgcgContent": "", "cqr": "2026-02-10"}],
        ],
    })
    fake_resp = MagicMock(text=body)
    fake_resp.raise_for_status.return_value = None

    with patch("marketpulse.data.tencent_client.httpx.get", return_value=fake_resp):
        actions = TencentClient().fetch_corporate_actions(
            "AAPL", start=date(2026, 1, 1), end=date(2026, 3, 1),
        )

    assert actions.dividends == [(date(2026, 2, 10), 0.25)]
    assert actions.splits == []


def test_parse_forward_split_row() -> None:
    """hgcgContent '每1股拆分成10股' → ratio 10.0."""
    from marketpulse.data.tencent_client import TencentClient

    body = _make_envelope({
        "usNVDA.OQ": [
            ["2024-06-10", "120", "121", "123", "117", "300000000",
             {"FHcontent": "", "hgcgContent": "每1股拆分成10股", "cqr": "2024-06-10"}],
        ],
    })
    fake_resp = MagicMock(text=body)
    fake_resp.raise_for_status.return_value = None

    with patch("marketpulse.data.tencent_client.httpx.get", return_value=fake_resp):
        actions = TencentClient().fetch_corporate_actions(
            "NVDA", start=date(2024, 6, 1), end=date(2024, 6, 30),
        )

    assert actions.splits == [(date(2024, 6, 10), 10.0)]
    assert actions.dividends == []


def test_parse_reverse_split_row() -> None:
    """hgcgContent '每5股合并成1股' → ratio 0.2 (1/5)."""
    from marketpulse.data.tencent_client import TencentClient

    body = _make_envelope({
        "usFOO.OQ": [
            ["2025-01-15", "10", "11", "12", "10", "100000",
             {"FHcontent": "", "hgcgContent": "每5股合并成1股", "cqr": "2025-01-15"}],
        ],
    })
    fake_resp = MagicMock(text=body)
    fake_resp.raise_for_status.return_value = None

    with patch("marketpulse.data.tencent_client.httpx.get", return_value=fake_resp):
        actions = TencentClient().fetch_corporate_actions(
            "FOO", start=date(2025, 1, 1), end=date(2025, 1, 30),
        )

    assert actions.splits == [(date(2025, 1, 15), 0.2)]


def test_parse_same_day_split_and_dividend() -> None:
    """A row with both FHcontent and hgcgContent yields TWO entries."""
    from marketpulse.data.tencent_client import TencentClient

    body = _make_envelope({
        "usBOTH.OQ": [
            ["2025-05-01", "100", "101", "102", "99", "1000000",
             {"FHcontent": "每股分配0.50美元", "hgcgContent": "每1股拆分成2股",
              "cqr": "2025-05-01"}],
        ],
    })
    fake_resp = MagicMock(text=body)
    fake_resp.raise_for_status.return_value = None

    with patch("marketpulse.data.tencent_client.httpx.get", return_value=fake_resp):
        actions = TencentClient().fetch_corporate_actions(
            "BOTH", start=date(2025, 1, 1), end=date(2025, 12, 31),
        )

    assert actions.dividends == [(date(2025, 5, 1), 0.50)]
    assert actions.splits == [(date(2025, 5, 1), 2.0)]


def test_unparseable_strings_are_skipped() -> None:
    """Rows with unrecognised FHcontent/hgcgContent format are logged + skipped."""
    from marketpulse.data.tencent_client import TencentClient

    body = _make_envelope({
        "usX.OQ": [
            ["2025-01-01", "1", "1", "1", "1", "0",
             {"FHcontent": "特别分红 unknown", "hgcgContent": "weird",
              "cqr": "2025-01-01"}],
            ["2025-02-01", "1", "1", "1", "1", "0",
             {"FHcontent": "每股分配0.10美元", "hgcgContent": "", "cqr": "2025-02-01"}],
        ],
    })
    fake_resp = MagicMock(text=body)
    fake_resp.raise_for_status.return_value = None

    with patch("marketpulse.data.tencent_client.httpx.get", return_value=fake_resp):
        actions = TencentClient().fetch_corporate_actions(
            "X", start=date(2025, 1, 1), end=date(2025, 12, 31),
        )

    # Unparseable row is skipped; second row parses fine.
    assert actions.dividends == [(date(2025, 2, 1), 0.10)]
    assert actions.splits == []


def test_empty_response_returns_empty_lists() -> None:
    """A bad-route envelope (code != 0) raises ValueError after trying suffixes."""
    import pytest as _pytest
    from marketpulse.data.tencent_client import TencentClient

    fake_resp = MagicMock(text='{"code": 11, "data": "", "msg": "no controller"}')
    fake_resp.raise_for_status.return_value = None

    with patch("marketpulse.data.tencent_client.httpx.get", return_value=fake_resp):
        with _pytest.raises(ValueError, match="no Tencent corporate actions"):
            TencentClient().fetch_corporate_actions(
                "UNKNOWN", start=date(2025, 1, 1), end=date(2025, 12, 31),
            )


def test_response_with_only_ohlcv_rows_returns_empty() -> None:
    """Rows without the dict at index 6 are plain OHLCV — no actions found."""
    from marketpulse.data.tencent_client import TencentClient

    body = _make_envelope({
        "usPLAIN.OQ": [
            ["2025-01-02", "100", "101", "102", "99", "1000000"],
            ["2025-01-03", "101", "102", "103", "100", "900000"],
        ],
    })
    fake_resp = MagicMock(text=body)
    fake_resp.raise_for_status.return_value = None

    with patch("marketpulse.data.tencent_client.httpx.get", return_value=fake_resp):
        actions = TencentClient().fetch_corporate_actions(
            "PLAIN", start=date(2025, 1, 1), end=date(2025, 1, 31),
        )

    assert actions.dividends == []
    assert actions.splits == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_tencent_corporate_actions.py -v`

Expected: FAIL — `fetch_corporate_actions` doesn't exist.

- [ ] **Step 3: Implement `fetch_corporate_actions` and `CorporateActions`**

In `marketpulse/data/tencent_client.py`:

a) At the top of the file, after the existing imports, add:

```python
from dataclasses import dataclass, field
```

b) Define the result dataclass near the top of the module (after the constants `_PARSE_RE`, `_SUFFIXES`, `_PERIOD_DAYS`):

```python
@dataclass
class CorporateActions:
    """Result of fetch_corporate_actions: separate lists for dividends and splits.

    Each entry is (ex_date, value): for dividends value is amount-per-share USD;
    for splits value is the ratio (new_shares / old_shares).
    """
    dividends: list[tuple[date, float]] = field(default_factory=list)
    splits: list[tuple[date, float]] = field(default_factory=list)


# Regexes for parsing the Chinese-language corporate-action fields from Tencent.
_DIV_RE = re.compile(r"每股分配([\d.]+)美元")
_FORWARD_SPLIT_RE = re.compile(r"每(\d+)股拆分成(\d+)股")
_REVERSE_SPLIT_RE = re.compile(r"每(\d+)股合并成(\d+)股")
```

c) Add the method to the `TencentClient` class (place it after `fetch_history`):

```python
    def fetch_corporate_actions(
        self, ticker: str, *, start: date, end: date,
    ) -> CorporateActions:
        """Parse cash dividends and splits from Tencent's Usfqkline endpoint.

        Returns a CorporateActions with separate dividend and split lists.
        Raises ValueError when no suffix variant returned a usable envelope.

        The Usfqkline payload places an optional dict at index 6 of each daily
        row when a corporate action occurred that day:
            {"FHcontent": "每股分配0.25美元", "hgcgContent": "每1股拆分成10股",
             "cqr": "2026-02-10"}
        Either field can be empty; both can be populated on the same date.

        Unparseable strings (formats we don't recognise) are logged via
        log.warning and skipped — they don't fail the whole call.
        """
        upper = ticker.strip().upper()
        if upper.startswith("^"):
            raise ValueError(f"Tencent fqkline does not cover index {ticker!r}")

        start_s = start.strftime("%Y-%m-%d")
        end_s = end.strftime("%Y-%m-%d")
        # 1825 = ~5 years headroom in trading days; covers the lookback the
        # scheduler asks for.
        n_rows = 1825

        last_err: Exception | None = None
        # Skip the no-suffix variant — Usfqkline requires a market suffix.
        for suffix in (".OQ", ".N"):
            symbol = f"us{upper}{suffix}"
            url = (
                f"https://web.ifzq.gtimg.cn/appstock/app/Usfqkline/get"
                f"?param={symbol},day,{start_s},{end_s},{n_rows},qfq"
            )
            try:
                resp = httpx.get(url, timeout=10)
                resp.raise_for_status()
            except Exception as exc:
                last_err = exc
                continue

            try:
                envelope = json.loads(resp.text)
            except json.JSONDecodeError as exc:
                last_err = exc
                continue

            if envelope.get("code") != 0:
                continue
            data = envelope.get("data") or {}
            sym_block = data.get(symbol) or {}
            rows = sym_block.get("qfqday") or sym_block.get("day") or []

            actions = CorporateActions()
            for r in rows:
                if len(r) < 7:
                    continue  # plain OHLCV row, no action
                action_dict = r[6]
                if not isinstance(action_dict, dict):
                    continue
                cqr = action_dict.get("cqr", "")
                try:
                    ex_date = datetime.strptime(cqr, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    log.warning("tencent_corp_action_bad_date",
                                ticker=upper, cqr=cqr)
                    continue

                fh = action_dict.get("FHcontent", "") or ""
                hg = action_dict.get("hgcgContent", "") or ""

                if fh:
                    m = _DIV_RE.search(fh)
                    if m:
                        try:
                            actions.dividends.append((ex_date, float(m.group(1))))
                        except ValueError:
                            log.warning("tencent_corp_action_bad_dividend",
                                        ticker=upper, ex_date=str(ex_date),
                                        content=fh)
                    else:
                        log.warning("tencent_corp_action_unparseable_dividend",
                                    ticker=upper, ex_date=str(ex_date),
                                    content=fh)

                if hg:
                    m_f = _FORWARD_SPLIT_RE.search(hg)
                    m_r = _REVERSE_SPLIT_RE.search(hg)
                    if m_f:
                        a, b = int(m_f.group(1)), int(m_f.group(2))
                        if a > 0:
                            actions.splits.append((ex_date, b / a))
                    elif m_r:
                        a, b = int(m_r.group(1)), int(m_r.group(2))
                        if a > 0:
                            actions.splits.append((ex_date, b / a))
                    else:
                        log.warning("tencent_corp_action_unparseable_split",
                                    ticker=upper, ex_date=str(ex_date),
                                    content=hg)

            return actions  # first suffix that returns a usable envelope wins

        raise ValueError(
            f"no Tencent corporate actions for {ticker!r}"
            + (f" (last error: {last_err})" if last_err else ""),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_tencent_corporate_actions.py -v`

Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/data/tencent_client.py tests/unit/test_tencent_corporate_actions.py
git commit -m "feat(tencent): fetch_corporate_actions parses dividends + splits"
```

---

## Task 8: Rewrite `run_detect_corporate_actions` (Tencent primary, yfinance fallback, splits + dividends)

**Files:**
- Modify: `marketpulse/scheduler/jobs.py`
- Modify: `tests/unit/test_scheduler_jobs.py`

- [ ] **Step 1: Update the existing scheduler tests + write new ones**

The existing `test_detect_corporate_actions_*` tests in `tests/unit/test_scheduler_jobs.py` were written for the splits-only yfinance flow. They need to be rewritten for the new Tencent-primary flow. Replace the three existing `test_detect_corporate_actions_*` tests with the following set:

```python
def test_detect_corporate_actions_records_tencent_splits_and_dividends(monkeypatch) -> None:
    """Tencent ok → both splits and dividends recorded; recompute_ticker
    called once per ticker that got a new split."""
    from datetime import date
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "y" * 32)
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    fake_session = MagicMock()
    # Holdings first, then watchlist
    fake_session.query.return_value.all.side_effect = [
        [MagicMock(ticker="TQQQ")],  # holdings
        [MagicMock(ticker="NVDA")],  # watchlist
    ]

    def fake_session_scope():
        yield fake_session

    from marketpulse.data.tencent_client import CorporateActions
    fake_tencent = MagicMock()
    fake_tencent.fetch_corporate_actions.side_effect = [
        CorporateActions(
            dividends=[(date(2025, 9, 24), 0.10)],
            splits=[(date(2025, 11, 20), 2.0)],
        ),
        CorporateActions(splits=[(date(2024, 6, 10), 10.0)], dividends=[]),
    ]

    with patch("marketpulse.scheduler.jobs.session_scope", fake_session_scope), \
         patch("marketpulse.scheduler.jobs.TencentClient", return_value=fake_tencent), \
         patch("marketpulse.scheduler.jobs.YFinanceClient") as YfMock, \
         patch("marketpulse.scheduler.jobs.record_split") as rs, \
         patch("marketpulse.scheduler.jobs.record_dividend") as rd, \
         patch("marketpulse.scheduler.jobs.quantity_as_of",
               return_value=20.0) as qa, \
         patch("marketpulse.scheduler.jobs.recompute_ticker") as rc:
        from marketpulse.scheduler.jobs import run_detect_corporate_actions
        run_detect_corporate_actions()

    # 2 splits recorded (one per ticker)
    assert rs.call_count == 2
    # source kwarg is "tencent" on both calls
    for call in rs.call_args_list:
        assert call.kwargs["source"] == "tencent"

    # 1 dividend recorded (TQQQ only; NVDA had none)
    assert rd.call_count == 1
    dkw = rd.call_args.kwargs
    assert dkw["ticker"] == "TQQQ"
    assert dkw["ex_date"] == date(2025, 9, 24)
    assert dkw["amount_per_share"] == 0.10
    assert dkw["total_amount"] == 2.0  # 20 * 0.10
    assert dkw["source"] == "tencent"

    # recompute_ticker called once per ticker with new splits
    assert rc.call_count == 2

    # yfinance fallback NOT invoked
    YfMock.return_value.fetch_splits.assert_not_called()
    YfMock.return_value.fetch_dividends.assert_not_called()


def test_detect_corporate_actions_tencent_fails_yfinance_fallback(monkeypatch) -> None:
    """Tencent raises → yfinance fetch_splits + fetch_dividends are tried."""
    from datetime import date
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "y" * 32)
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    fake_session = MagicMock()
    fake_session.query.return_value.all.side_effect = [
        [MagicMock(ticker="TQQQ")],
        [],
    ]

    def fake_session_scope():
        yield fake_session

    fake_tencent = MagicMock()
    fake_tencent.fetch_corporate_actions.side_effect = RuntimeError("Tencent down")
    fake_yf = MagicMock()
    fake_yf.fetch_splits.return_value = [(date(2025, 11, 20), 2.0)]
    fake_yf.fetch_dividends.return_value = [(date(2025, 9, 24), 0.10)]

    with patch("marketpulse.scheduler.jobs.session_scope", fake_session_scope), \
         patch("marketpulse.scheduler.jobs.TencentClient", return_value=fake_tencent), \
         patch("marketpulse.scheduler.jobs.YFinanceClient", return_value=fake_yf), \
         patch("marketpulse.scheduler.jobs.record_split") as rs, \
         patch("marketpulse.scheduler.jobs.record_dividend") as rd, \
         patch("marketpulse.scheduler.jobs.quantity_as_of", return_value=20.0), \
         patch("marketpulse.scheduler.jobs.recompute_ticker"):
        from marketpulse.scheduler.jobs import run_detect_corporate_actions
        run_detect_corporate_actions()

    # Tencent was attempted
    fake_tencent.fetch_corporate_actions.assert_called_once()
    # Fallback engaged
    fake_yf.fetch_splits.assert_called_once_with("TQQQ")
    fake_yf.fetch_dividends.assert_called_once_with("TQQQ")
    # Records carry source="yfinance"
    assert rs.call_args.kwargs["source"] == "yfinance"
    assert rd.call_args.kwargs["source"] == "yfinance"


def test_detect_corporate_actions_both_sources_fail(monkeypatch) -> None:
    """Both Tencent and yfinance raise → job continues, no records."""
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "y" * 32)
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    fake_session = MagicMock()
    fake_session.query.return_value.all.side_effect = [
        [MagicMock(ticker="TQQQ"), MagicMock(ticker="NVDA")],
        [],
    ]

    def fake_session_scope():
        yield fake_session

    fake_tencent = MagicMock()
    fake_tencent.fetch_corporate_actions.side_effect = RuntimeError("Tencent down")
    fake_yf = MagicMock()
    fake_yf.fetch_splits.side_effect = RuntimeError("yahoo timeout")

    with patch("marketpulse.scheduler.jobs.session_scope", fake_session_scope), \
         patch("marketpulse.scheduler.jobs.TencentClient", return_value=fake_tencent), \
         patch("marketpulse.scheduler.jobs.YFinanceClient", return_value=fake_yf), \
         patch("marketpulse.scheduler.jobs.record_split") as rs, \
         patch("marketpulse.scheduler.jobs.record_dividend") as rd, \
         patch("marketpulse.scheduler.jobs.recompute_ticker"):
        from marketpulse.scheduler.jobs import run_detect_corporate_actions
        run_detect_corporate_actions()  # must not raise

    # Both tickers attempted, nothing recorded
    assert fake_tencent.fetch_corporate_actions.call_count == 2
    rs.assert_not_called()
    rd.assert_not_called()


def test_detect_corporate_actions_skips_dividend_when_qty_zero(monkeypatch) -> None:
    """Dividend ex_date with qty=0 (never held / sold before) → no record."""
    from datetime import date
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "y" * 32)
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    fake_session = MagicMock()
    fake_session.query.return_value.all.side_effect = [
        [],
        [MagicMock(ticker="WATCHED")],
    ]

    def fake_session_scope():
        yield fake_session

    from marketpulse.data.tencent_client import CorporateActions
    fake_tencent = MagicMock()
    fake_tencent.fetch_corporate_actions.return_value = CorporateActions(
        dividends=[(date(2025, 9, 24), 0.10)],
        splits=[],
    )

    with patch("marketpulse.scheduler.jobs.session_scope", fake_session_scope), \
         patch("marketpulse.scheduler.jobs.TencentClient", return_value=fake_tencent), \
         patch("marketpulse.scheduler.jobs.YFinanceClient"), \
         patch("marketpulse.scheduler.jobs.record_split"), \
         patch("marketpulse.scheduler.jobs.record_dividend") as rd, \
         patch("marketpulse.scheduler.jobs.quantity_as_of", return_value=0.0), \
         patch("marketpulse.scheduler.jobs.recompute_ticker"):
        from marketpulse.scheduler.jobs import run_detect_corporate_actions
        run_detect_corporate_actions()

    # Dividend was returned by Tencent but qty=0 → skipped.
    rd.assert_not_called()


def test_detect_corporate_actions_idempotent(monkeypatch) -> None:
    """If a split/dividend already exists, SplitError/DividendError swallowed."""
    from datetime import date
    from unittest.mock import MagicMock, patch

    from marketpulse.holdings.dividends import DividendError
    from marketpulse.holdings.splits import SplitError

    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "y" * 32)
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    fake_session = MagicMock()
    fake_session.query.return_value.all.side_effect = [
        [MagicMock(ticker="TQQQ")],
        [],
    ]

    def fake_session_scope():
        yield fake_session

    from marketpulse.data.tencent_client import CorporateActions
    fake_tencent = MagicMock()
    fake_tencent.fetch_corporate_actions.return_value = CorporateActions(
        dividends=[(date(2025, 9, 24), 0.10)],
        splits=[(date(2025, 11, 20), 2.0)],
    )

    with patch("marketpulse.scheduler.jobs.session_scope", fake_session_scope), \
         patch("marketpulse.scheduler.jobs.TencentClient", return_value=fake_tencent), \
         patch("marketpulse.scheduler.jobs.YFinanceClient"), \
         patch("marketpulse.scheduler.jobs.record_split",
               side_effect=SplitError("already recorded")), \
         patch("marketpulse.scheduler.jobs.record_dividend",
               side_effect=DividendError("already recorded")), \
         patch("marketpulse.scheduler.jobs.quantity_as_of", return_value=20.0), \
         patch("marketpulse.scheduler.jobs.recompute_ticker") as rc:
        from marketpulse.scheduler.jobs import run_detect_corporate_actions
        run_detect_corporate_actions()  # must not raise

    # No new splits → recompute NOT called.
    rc.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_scheduler_jobs.py -v -k "detect_corporate_actions"`

Expected: FAIL — `TencentClient.fetch_corporate_actions` isn't wired into the job yet; `quantity_as_of` not imported; `record_dividend` not called from the job; `source="tencent"` not set on records.

- [ ] **Step 3: Rewrite the job**

In `marketpulse/scheduler/jobs.py`:

a) Update the imports near the top of the file (after the existing imports), adding `TencentClient`, `timedelta`, `record_dividend`, `DividendError`, and `quantity_as_of`:

```python
from datetime import date, time, timedelta
# ... existing imports stay ...
from marketpulse.data.tencent_client import TencentClient
from marketpulse.data.yfinance_client import YFinanceClient
from marketpulse.db.models import Holding, WatchlistItem
from marketpulse.holdings.dividends import DividendError, record_dividend
from marketpulse.holdings.quantity_history import quantity_as_of
from marketpulse.holdings.splits import SplitError, record_split
from marketpulse.holdings.trades import recompute_ticker
```

(Some of these may already be present from PR #1; verify by reading the existing import block and add only what's missing.)

b) Replace the entire `run_detect_corporate_actions` function with:

```python
def run_detect_corporate_actions() -> None:
    """Daily 17:00 ET: pull dividends + splits from Tencent for every
    held/watched ticker; fall back to yfinance on Tencent failure.

    Idempotent — duplicate (ticker, ex_date) at the service layer is swallowed.
    Dividends are only recorded when shares were held on ex_date (per
    quantity_as_of). Splits are always recorded so future buys recompute
    correctly. recompute_ticker is only called when at least one new split
    actually landed.
    """
    log.info("detect_corporate_actions_start")
    tencent = TencentClient()
    yf_client = YFinanceClient()
    today = date.today()
    since = today - timedelta(days=1825)  # ~5 years lookback

    gen = session_scope()
    db = next(gen)
    try:
        held = [h.ticker for h in db.query(Holding).all()]
        watched = [w.ticker for w in db.query(WatchlistItem).all()]
        seen: set[str] = set()
        tickers: list[str] = []
        for t in held + watched:
            if t not in seen:
                seen.add(t)
                tickers.append(t)

        for t in tickers:
            actions, src = _fetch_corp_actions(t, tencent, yf_client, since, today)
            if actions is None:
                continue  # both sources logged + failed

            recompute_needed = False

            # Splits: record for all tickers (watchlist-only included).
            for ex_date, ratio in actions.splits:
                try:
                    record_split(
                        db, ticker=t, ex_date=ex_date, ratio=ratio, source=src,
                    )
                    log.info("split_recorded", ticker=t,
                             ex_date=str(ex_date), ratio=ratio, source=src)
                    recompute_needed = True
                except SplitError:
                    pass  # already recorded

            # Dividends: only record when shares held on ex_date.
            for ex_date, per_share in actions.dividends:
                qty = quantity_as_of(db, t, ex_date)
                if qty <= 0:
                    continue
                try:
                    record_dividend(
                        db, ticker=t, ex_date=ex_date,
                        amount_per_share=per_share,
                        total_amount=qty * per_share,
                        source=src,
                    )
                    log.info("dividend_recorded", ticker=t,
                             ex_date=str(ex_date), per_share=per_share,
                             qty=qty, source=src)
                except DividendError:
                    pass  # already recorded

            if recompute_needed:
                recompute_ticker(db, t)
    finally:
        db.close()
    log.info("detect_corporate_actions_done")


def _fetch_corp_actions(ticker, tencent, yf_client, since, today):
    """Try Tencent first; fall back to yfinance on any exception.
    Returns (CorporateActions, source_label) or (None, "none") on total failure.
    Never raises.
    """
    from marketpulse.data.tencent_client import CorporateActions
    try:
        actions = tencent.fetch_corporate_actions(ticker, start=since, end=today)
        return actions, "tencent"
    except Exception as exc:  # noqa: BLE001 — best-effort across data sources
        log.warning("tencent_corp_actions_failed",
                    ticker=ticker, error=str(exc))
    try:
        splits = yf_client.fetch_splits(ticker)
        dividends = yf_client.fetch_dividends(ticker)
        return CorporateActions(dividends=dividends, splits=splits), "yfinance"
    except Exception as exc:  # noqa: BLE001
        log.warning("corp_actions_all_sources_failed",
                    ticker=ticker, error=str(exc))
        return None, "none"
```

- [ ] **Step 4: Run scheduler tests**

Run: `python -m pytest tests/unit/test_scheduler_jobs.py -v`

Expected: PASS — 5 new `detect_corporate_actions` tests + the pre-existing recap-related tests (recap tests should be unaffected; verify there's no regression).

- [ ] **Step 5: Run the full integration check**

Run: `python -m pytest -q`

Expected: All pass. If `tests/integration/test_trades.py` (the 21 splits-aware tests) fails after the `_walk_events` refactor from Task 4, debug there before continuing.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/scheduler/jobs.py tests/unit/test_scheduler_jobs.py
git commit -m "feat(scheduler): detect_corporate_actions uses Tencent primary + dividends"
```

---

## Task 9: Full-suite regression + ruff

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

Run: `source .venv/bin/activate && python -m pytest -q`

Expected: All tests pass (count will be ≥ 245 + new tests from this PR; rough estimate ~270).

- [ ] **Step 2: Ruff check**

Run: `ruff check marketpulse tests`

Expected: `All checks passed!`

If ruff finds issues, run `ruff check marketpulse tests --fix` and inspect the diff before re-running pytest to confirm no behavior change.

- [ ] **Step 3: Manual Tencent endpoint sanity check**

Run a one-shot script against a known dividend-paying ticker to confirm the live endpoint still returns the expected shape:

```bash
python -c "
from datetime import date
from marketpulse.data.tencent_client import TencentClient
out = TencentClient().fetch_corporate_actions(
    'AAPL', start=date(2025, 1, 1), end=date(2026, 5, 1),
)
print(f'AAPL dividends: {len(out.dividends)}, splits: {len(out.splits)}')
for d, amt in out.dividends[:5]:
    print(f'  div {d}: \${amt}/share')
"
```

Expected: prints 4-5 dividends from 2025 (AAPL pays quarterly).

If this fails with a network error, that's OK — the test suite mocked the network. The live check is a one-off confidence step, not a CI gate.

- [ ] **Step 4: Migration sanity check on live DB**

If you have a local copy of the prod DB:

```bash
DATABASE_URL="sqlite:///./data/marketpulse.db.bak-pre-0008" alembic upgrade head
# Then inspect dividends.source — all existing rows should be "manual".
```

- [ ] **Step 5: No commit needed** — Task 9 is verification only.

---

## After-deployment runbook

(Mirrors the spec's Deployment Notes — single source of truth lives there.)

1. **Back up the prod DB:** `cp data/marketpulse.db data/marketpulse.db.bak-pre-0008`
2. **Apply migration:** `alembic upgrade head` (now at 0008)
3. **Wipe manual dividend rows:** `sqlite3 data/marketpulse.db "DELETE FROM dividends WHERE source = 'manual';"`
4. **Trigger the scheduler manually:** `python -c "from marketpulse.scheduler.jobs import run_detect_corporate_actions; run_detect_corporate_actions()"`
5. **Verify:** open `/trades`, filter to `仅分红`, confirm 14+ TQQQ rows reappear with `source="tencent"` populated. Spot-check `amount_per_share` against the original screenshots — these MUST match. `total_amount` may differ if the original import used a coarser share-count snapshot — that's expected and the new values are authoritative.
