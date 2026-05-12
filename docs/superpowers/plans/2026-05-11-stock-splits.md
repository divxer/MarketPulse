# Stock Splits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current "$0 buy with `拆股` in notes" hack with a first-class `StockSplit` event type that is auto-detected from yfinance, preserves original trade data, and adjusts holdings on the fly.

**Architecture:** New `stock_splits` table with `(ticker, ex_date)` unique constraint and `ratio > 0 AND ratio != 1` CHECK constraint. `recompute_ticker` walks a merged Trade+Split timeline (splits anchored to end-of-day so same-day trades execute first). New daily scheduler job pulls splits via `YFinanceClient.fetch_splits()`. UI unifies `/trades` into a Trade+Dividend+Split timeline with a filter strip. One-off migration script converts existing hack rows.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 (Mapped/mapped_column), Alembic, APScheduler, yfinance, Jinja2 + HTMX, pytest. Spec at `docs/superpowers/specs/2026-05-11-stock-splits-design.md`.

---

## File Structure

**New files:**

| File | Responsibility |
|---|---|
| `marketpulse/holdings/splits.py` | Service layer: `record_split`, `get_splits_for_ticker`, `delete_split`, `SplitError` |
| `marketpulse/web/routes/splits.py` | POST/GET/DELETE `/splits` HTTP endpoints |
| `alembic/versions/0007_stock_splits.py` | Migration for `stock_splits` table |
| `tests/unit/test_splits.py` | Service-layer + ratio validation + uniqueness tests |
| `tests/web/test_splits.py` | API + recompute integration tests |
| `scripts/cleanup_split_hacks.py` | One-off migration of `price=0` buy rows |

**Modified files:**

| File | Change |
|---|---|
| `marketpulse/db/models.py` | Add `StockSplit` model |
| `marketpulse/holdings/trades.py` | Rewrite `recompute_ticker` to walk Trade+Split timeline |
| `marketpulse/data/yfinance_client.py` | Add `fetch_splits(ticker) -> list[(date, float)]` |
| `marketpulse/scheduler/jobs.py` | Add `run_detect_corporate_actions` job + cron schedule |
| `marketpulse/web/routes/trades.py` | Unified timeline: union Trade + Split + Dividend, type filter |
| `marketpulse/web/templates/trades.html` | Type filter strip; form dropdown swaps fields when "拆股"/"分红" selected |
| `marketpulse/web/templates/partials/trades_table.html` | Three row shapes (buy/sell, dividend, split) |
| `marketpulse/web/main.py` | Register splits router |
| `tests/integration/test_trades.py` | Extend `recompute_ticker` tests for splits |
| `tests/unit/test_scheduler_jobs.py` | Tests for `run_detect_corporate_actions` |

---

## Task 1: Add `StockSplit` model

**Files:**
- Modify: `marketpulse/db/models.py`
- Test: `tests/unit/test_models.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_models.py`:

```python
def test_stock_split_model_fields(db_session) -> None:
    from datetime import date
    from marketpulse.db.models import StockSplit

    s = StockSplit(
        ticker="TQQQ",
        ex_date=date(2025, 11, 20),
        ratio=2.0,
        source="yfinance",
        notes=None,
    )
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    assert s.id is not None
    assert s.ticker == "TQQQ"
    assert s.ratio == 2.0
    assert s.source == "yfinance"
    assert s.created_at is not None


def test_stock_split_unique_constraint(db_session) -> None:
    from datetime import date
    from sqlalchemy.exc import IntegrityError
    from marketpulse.db.models import StockSplit

    db_session.add(StockSplit(ticker="TQQQ", ex_date=date(2025, 11, 20),
                              ratio=2.0, source="yfinance"))
    db_session.commit()
    db_session.add(StockSplit(ticker="TQQQ", ex_date=date(2025, 11, 20),
                              ratio=3.0, source="manual"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
```

Make sure `pytest` is already imported at the top of `test_models.py`. If not, add `import pytest` to the existing imports.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_models.py::test_stock_split_model_fields tests/unit/test_models.py::test_stock_split_unique_constraint -v`

Expected: FAIL with `ImportError: cannot import name 'StockSplit'`.

- [ ] **Step 3: Add the model**

In `marketpulse/db/models.py`, add after the `Dividend` class (around line 109, before `AlertRule`):

```python
class StockSplit(Base):
    """Corporate-action split event. Preserves original Trade rows; the
    splits-aware recompute applies these in chronological order to derive
    current Holding state. See docs/superpowers/specs/2026-05-11-stock-splits-design.md.
    """
    __tablename__ = "stock_splits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    # new_shares / old_shares. Forward 1:2 = 2.0; reverse 5:1 = 0.2.
    # CHECK constraint at the DB level guards against bad data even if a
    # caller bypasses service-layer validation.
    ratio: Mapped[float] = mapped_column(Float, nullable=False)
    # "yfinance" | "manual" | "import" — lets reconciliation prefer one over another.
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("ticker", "ex_date", name="uq_stock_splits_ticker_date"),
        CheckConstraint("ratio > 0 AND ratio != 1", name="ck_stock_splits_ratio_valid"),
        Index("ix_stock_splits_ticker_ex_date", "ticker", "ex_date"),
    )
```

Also add `CheckConstraint` to the SQLAlchemy import list at the top of the file:

```python
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_models.py::test_stock_split_model_fields tests/unit/test_models.py::test_stock_split_unique_constraint -v`

Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/db/models.py tests/unit/test_models.py
git commit -m "feat(splits): add StockSplit model"
```

---

## Task 2: Alembic migration for `stock_splits`

**Files:**
- Create: `alembic/versions/0007_stock_splits.py`

- [ ] **Step 1: Write the migration**

Create `alembic/versions/0007_stock_splits.py`:

```python
"""add stock_splits table

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-11
"""
import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stock_splits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("ratio", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_splits")),
        sa.UniqueConstraint("ticker", "ex_date", name="uq_stock_splits_ticker_date"),
        sa.CheckConstraint("ratio > 0 AND ratio != 1", name="ck_stock_splits_ratio_valid"),
    )
    op.create_index(
        "ix_stock_splits_ticker_ex_date", "stock_splits", ["ticker", "ex_date"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_stock_splits_ticker_ex_date", table_name="stock_splits")
    op.drop_table("stock_splits")
```

- [ ] **Step 2: Test the migration round-trip**

Against a scratch SQLite file:

```bash
DB_URL="sqlite:///$(mktemp -d)/test.db" alembic upgrade head
DB_URL="$DB_URL" alembic downgrade 0006
DB_URL="$DB_URL" alembic upgrade head
```

Expected: every command exits 0 with no errors.

- [ ] **Step 3: Commit**

```bash
git add alembic/versions/0007_stock_splits.py
git commit -m "feat(splits): alembic migration 0007 for stock_splits"
```

---

## Task 3: Service layer — `record_split`, `get_splits_for_ticker`, `delete_split`

**Files:**
- Create: `marketpulse/holdings/splits.py`
- Create: `tests/unit/test_splits.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_splits.py`:

```python
from datetime import date

import pytest

from marketpulse.holdings.splits import (
    SplitError,
    delete_split,
    get_splits_for_ticker,
    record_split,
)


def test_record_split_persists(db_session) -> None:
    s = record_split(db_session, ticker="TQQQ", ex_date=date(2025, 11, 20), ratio=2.0)
    assert s.id is not None
    assert s.ticker == "TQQQ"
    assert s.ratio == 2.0
    assert s.source == "manual"


def test_record_split_normalizes_ticker(db_session) -> None:
    s = record_split(db_session, ticker="  tqqq ", ex_date=date(2025, 11, 20), ratio=2.0)
    assert s.ticker == "TQQQ"


def test_record_split_rejects_invalid_ratio(db_session) -> None:
    with pytest.raises(SplitError, match="ratio"):
        record_split(db_session, ticker="X", ex_date=date(2025, 1, 1), ratio=0)
    with pytest.raises(SplitError, match="ratio"):
        record_split(db_session, ticker="X", ex_date=date(2025, 1, 1), ratio=-1)
    with pytest.raises(SplitError, match="ratio"):
        record_split(db_session, ticker="X", ex_date=date(2025, 1, 1), ratio=1)


def test_record_split_rejects_empty_ticker(db_session) -> None:
    with pytest.raises(SplitError, match="ticker"):
        record_split(db_session, ticker="  ", ex_date=date(2025, 1, 1), ratio=2)


def test_record_split_duplicate_raises(db_session) -> None:
    record_split(db_session, ticker="TQQQ", ex_date=date(2025, 11, 20), ratio=2.0)
    with pytest.raises(SplitError, match="already recorded"):
        record_split(db_session, ticker="TQQQ", ex_date=date(2025, 11, 20), ratio=3.0)


def test_get_splits_for_ticker_returns_in_date_order(db_session) -> None:
    record_split(db_session, ticker="TQQQ", ex_date=date(2025, 11, 20), ratio=2.0)
    record_split(db_session, ticker="TQQQ", ex_date=date(2022, 1, 13), ratio=2.0)
    splits = get_splits_for_ticker(db_session, "TQQQ")
    assert [s.ex_date for s in splits] == [date(2022, 1, 13), date(2025, 11, 20)]


def test_delete_split_returns_ticker(db_session) -> None:
    s = record_split(db_session, ticker="TQQQ", ex_date=date(2025, 11, 20), ratio=2.0)
    t = delete_split(db_session, s.id)
    assert t == "TQQQ"
    assert get_splits_for_ticker(db_session, "TQQQ") == []


def test_delete_split_missing_raises(db_session) -> None:
    with pytest.raises(SplitError, match="not found"):
        delete_split(db_session, 9999)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_splits.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'marketpulse.holdings.splits'`.

- [ ] **Step 3: Implement the service**

Create `marketpulse/holdings/splits.py`:

```python
"""Stock split service layer.

Splits are corporate-action events distinct from Trades. They never modify
Trade rows — `recompute_ticker` applies them on the fly when computing the
current Holding state. See docs/superpowers/specs/2026-05-11-stock-splits-design.md.
"""
from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marketpulse.db.models import StockSplit


class SplitError(ValueError):
    """Raised on invalid split input or duplicate ex-date for a ticker."""


def record_split(
    session: Session,
    *,
    ticker: str,
    ex_date: date,
    ratio: float,
    source: str = "manual",
    notes: str | None = None,
) -> StockSplit:
    """Persist a stock-split event. Commits within. Raises SplitError on
    invalid input or duplicate (ticker, ex_date).

    Callers that want to recompute the Holding after recording should call
    `marketpulse.holdings.trades.recompute_ticker(session, ticker)` next.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise SplitError("ticker is required")
    if ratio <= 0:
        raise SplitError(f"ratio must be positive, got {ratio}")
    if ratio == 1:
        raise SplitError("ratio of 1 is a no-op; not recording")

    split = StockSplit(
        ticker=ticker,
        ex_date=ex_date,
        ratio=float(ratio),
        source=source,
        notes=notes or None,
    )
    session.add(split)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise SplitError(
            f"split already recorded for {ticker} on {ex_date}",
        ) from exc
    session.refresh(split)
    return split


def get_splits_for_ticker(session: Session, ticker: str) -> list[StockSplit]:
    """Return all splits for a ticker, ordered by ex_date ascending."""
    return (
        session.query(StockSplit)
        .filter(StockSplit.ticker == ticker.strip().upper())
        .order_by(StockSplit.ex_date.asc())
        .all()
    )


def delete_split(session: Session, split_id: int) -> str:
    """Delete a split by id. Returns the affected ticker so the caller can
    `recompute_ticker` it. Raises SplitError if not found.
    """
    split = session.query(StockSplit).filter(StockSplit.id == split_id).one_or_none()
    if not split:
        raise SplitError(f"split {split_id} not found")
    ticker = split.ticker
    session.delete(split)
    session.commit()
    return ticker
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_splits.py -v`

Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/holdings/splits.py tests/unit/test_splits.py
git commit -m "feat(splits): service layer (record/get/delete)"
```

---

## Task 4: Splits-aware `recompute_ticker`

**Files:**
- Modify: `marketpulse/holdings/trades.py`
- Test: `tests/integration/test_trades.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_trades.py`:

```python
def test_recompute_applies_forward_split(db_session) -> None:
    """1:2 forward split doubles share count and halves avg cost."""
    from datetime import UTC, date, datetime
    from marketpulse.holdings.splits import record_split
    from marketpulse.holdings.trades import recompute_ticker
    from marketpulse.db.models import Holding

    record_trade(db_session, ticker="TQQQ", action="buy", quantity=20, price=30,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    record_split(db_session, ticker="TQQQ", ex_date=date(2025, 11, 20), ratio=2.0)
    recompute_ticker(db_session, "TQQQ")

    h = db_session.query(Holding).filter_by(ticker="TQQQ").one()
    assert h.quantity == 40
    assert h.avg_cost == 15.0


def test_recompute_applies_reverse_split(db_session) -> None:
    """5:1 reverse split (ratio 0.2) cuts shares to 20%, raises avg cost 5x."""
    from datetime import UTC, date, datetime
    from marketpulse.holdings.splits import record_split
    from marketpulse.holdings.trades import recompute_ticker
    from marketpulse.db.models import Holding

    record_trade(db_session, ticker="X", action="buy", quantity=100, price=10,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    record_split(db_session, ticker="X", ex_date=date(2025, 1, 1), ratio=0.2)
    recompute_ticker(db_session, "X")

    h = db_session.query(Holding).filter_by(ticker="X").one()
    assert h.quantity == pytest.approx(20)
    assert h.avg_cost == pytest.approx(50)


def test_recompute_applies_consecutive_splits(db_session) -> None:
    """Two splits compound: 1:2 then 1:3 on 10 shares = 60 shares."""
    from datetime import UTC, date, datetime
    from marketpulse.holdings.splits import record_split
    from marketpulse.holdings.trades import recompute_ticker
    from marketpulse.db.models import Holding

    record_trade(db_session, ticker="X", action="buy", quantity=10, price=60,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    record_split(db_session, ticker="X", ex_date=date(2024, 6, 1), ratio=2.0)
    record_split(db_session, ticker="X", ex_date=date(2025, 1, 1), ratio=3.0)
    recompute_ticker(db_session, "X")

    h = db_session.query(Holding).filter_by(ticker="X").one()
    assert h.quantity == 60
    assert h.avg_cost == pytest.approx(10.0)  # 60 / (2 * 3)


def test_same_day_trade_executes_before_split(db_session) -> None:
    """A trade on the same date as the split sorts BEFORE the split (splits
    are anchored to end-of-day so morning trades still trade at pre-split prices).
    """
    from datetime import UTC, date, datetime
    from marketpulse.holdings.splits import record_split
    from marketpulse.holdings.trades import recompute_ticker
    from marketpulse.db.models import Holding

    # Buy 10 @ $60 in the morning of split day, then 1:2 split same date.
    record_trade(db_session, ticker="X", action="buy", quantity=10, price=60,
                 executed_at=datetime(2025, 11, 20, 9, 30, tzinfo=UTC))
    record_split(db_session, ticker="X", ex_date=date(2025, 11, 20), ratio=2.0)
    recompute_ticker(db_session, "X")

    h = db_session.query(Holding).filter_by(ticker="X").one()
    assert h.quantity == 20  # 10 * 2
    assert h.avg_cost == 30.0  # 60 / 2


def test_recompute_handles_sell_after_split(db_session) -> None:
    """Sells use POST-split avg_cost when computing realized P&L."""
    from datetime import UTC, date, datetime
    from marketpulse.db.models import Trade
    from marketpulse.holdings.splits import record_split
    from marketpulse.holdings.trades import recompute_ticker

    # Buy 20 @ $30. Pre-split avg = $30.
    record_trade(db_session, ticker="X", action="buy", quantity=20, price=30,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    # 1:2 split → 40 @ $15 effective.
    record_split(db_session, ticker="X", ex_date=date(2025, 6, 1), ratio=2.0)
    # Sell 10 @ $20 post-split. Expected realized P&L = (20-15)*10 = 50.
    record_trade(db_session, ticker="X", action="sell", quantity=10, price=20,
                 executed_at=datetime(2025, 7, 1, tzinfo=UTC))
    recompute_ticker(db_session, "X")

    sell = (
        db_session.query(Trade)
        .filter(Trade.ticker == "X", Trade.action == "sell")
        .one()
    )
    assert sell.realized_pl == pytest.approx(50.0)


def test_recompute_after_split_delete_restores(db_session) -> None:
    """Delete a split → recompute → state matches as if split never existed."""
    from datetime import UTC, date, datetime
    from marketpulse.db.models import Holding
    from marketpulse.holdings.splits import delete_split, record_split
    from marketpulse.holdings.trades import recompute_ticker

    record_trade(db_session, ticker="X", action="buy", quantity=20, price=30,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    s = record_split(db_session, ticker="X", ex_date=date(2025, 6, 1), ratio=2.0)
    recompute_ticker(db_session, "X")
    assert db_session.query(Holding).filter_by(ticker="X").one().quantity == 40

    delete_split(db_session, s.id)
    recompute_ticker(db_session, "X")
    h = db_session.query(Holding).filter_by(ticker="X").one()
    assert h.quantity == 20
    assert h.avg_cost == 30.0


def test_fractional_shares_after_reverse_split_precise(db_session) -> None:
    """7 shares × 0.2 (5:1 reverse) = 1.4 shares; float64 should preserve this."""
    from datetime import UTC, date, datetime
    from marketpulse.db.models import Holding
    from marketpulse.holdings.splits import record_split
    from marketpulse.holdings.trades import recompute_ticker

    record_trade(db_session, ticker="X", action="buy", quantity=7, price=10,
                 executed_at=datetime(2024, 1, 15, tzinfo=UTC))
    record_split(db_session, ticker="X", ex_date=date(2025, 1, 1), ratio=0.2)
    recompute_ticker(db_session, "X")

    h = db_session.query(Holding).filter_by(ticker="X").one()
    assert h.quantity == pytest.approx(1.4)
    assert h.avg_cost == pytest.approx(50.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/integration/test_trades.py -v -k "split"`

Expected: All 7 new tests FAIL because `recompute_ticker` doesn't yet know about splits — quantities won't be multiplied by ratios.

- [ ] **Step 3: Rewrite `recompute_ticker`**

In `marketpulse/holdings/trades.py`, replace the `recompute_ticker` function (lines 96-134) with:

```python
def recompute_ticker(session: Session, ticker: str) -> None:
    """Rebuild Holding row + realized_pl values from the full Trade + StockSplit
    history for ticker.

    Walks both timelines merged in chronological order. Splits are anchored to
    end-of-day so any same-day trade sorts BEFORE the split takes effect, which
    matches real-world execution (the split is applied at market open of the
    next session, but ex_date is a date, not a datetime).

    Trade rows are never mutated — only `realized_pl` on sells is recomputed.
    """
    from datetime import UTC, datetime, time

    from marketpulse.db.models import StockSplit

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

    # Normalize event times to datetime so heterogeneous tuple comparison
    # never raises. Splits anchor at end-of-day (kind=1) so same-day trades
    # (kind=0) sort first.
    _EOD = time(23, 59, 59, tzinfo=UTC)

    def _trade_when(t: Trade) -> datetime:
        if t.executed_at:
            return t.executed_at
        return t.created_at

    events: list[tuple[datetime, int, str, object]] = []
    for t in trades:
        events.append((_trade_when(t), 0, "trade", t))
    for s in splits:
        events.append((datetime.combine(s.ex_date, _EOD), 1, "split", s))
    events.sort(key=lambda x: (x[0], x[1]))

    qty = 0.0
    avg_cost = 0.0
    for _when, _order, kind, evt in events:
        if kind == "trade":
            t = evt  # type: ignore[assignment]
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
        else:  # split
            s = evt  # type: ignore[assignment]
            qty = qty * s.ratio
            # Inverse adjustment keeps total_cost invariant.
            if s.ratio:
                avg_cost = avg_cost / s.ratio

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/integration/test_trades.py -v`

Expected: PASS, all integration tests including the 7 new split tests, no regressions on pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/holdings/trades.py tests/integration/test_trades.py
git commit -m "feat(splits): splits-aware recompute_ticker"
```

---

## Task 5: `/splits` HTTP routes

**Files:**
- Create: `marketpulse/web/routes/splits.py`
- Modify: `marketpulse/web/main.py`
- Create: `tests/web/test_splits.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_splits.py`:

```python
from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def test_post_splits_creates_row(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.post("/splits", data={
        "ticker": "TQQQ",
        "ex_date": "2025-11-20",
        "ratio": 2.0,
        "notes": "test split",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["ticker"] == "TQQQ"
    assert body["ex_date"] == "2025-11-20"
    assert body["ratio"] == 2.0
    assert body["source"] == "manual"


def test_post_splits_rejects_bad_ratio(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    for bad in ("0", "1", "-1"):
        res = client.post("/splits", data={
            "ticker": "X", "ex_date": "2025-01-01", "ratio": bad,
        })
        assert res.status_code == 422, f"ratio={bad!r} should be rejected"


def test_post_splits_rejects_bad_date(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    res = client.post("/splits", data={
        "ticker": "X", "ex_date": "not-a-date", "ratio": 2.0,
    })
    assert res.status_code == 422


def test_post_splits_duplicate_rejected(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    client.post("/splits", data={"ticker": "X", "ex_date": "2025-01-01", "ratio": 2})
    res = client.post("/splits", data={"ticker": "X", "ex_date": "2025-01-01", "ratio": 3})
    assert res.status_code == 422
    assert "already recorded" in res.json()["detail"]


def test_get_splits_filters_by_ticker(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    client.post("/splits", data={"ticker": "TQQQ", "ex_date": "2025-11-20", "ratio": 2})
    client.post("/splits", data={"ticker": "NVDA", "ex_date": "2024-06-10", "ratio": 10})

    res = client.get("/splits")
    assert res.status_code == 200
    assert len(res.json()) == 2

    res = client.get("/splits?ticker=TQQQ")
    assert len(res.json()) == 1
    assert res.json()[0]["ticker"] == "TQQQ"


def test_delete_splits_recomputes_holding(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    # 1) Buy 20 shares @ $30
    client.post("/trades", data={
        "ticker": "X", "action": "buy", "quantity": 20, "price": 30,
        "fees": 0, "executed_at": "2024-01-15",
    })
    # 2) Record 1:2 split → holding becomes 40 @ $15
    create = client.post("/splits", data={
        "ticker": "X", "ex_date": "2025-06-01", "ratio": 2,
    })
    split_id = create.json()["id"]

    # The POST /splits handler must trigger recompute. Verify via /holdings.
    res = client.get("/holdings")
    assert "X" in res.text
    # Look for quantity 40 in the rendered table.
    assert "40" in res.text

    # 3) Delete split → recompute → 20 @ $30
    res = client.delete(f"/splits/{split_id}")
    assert res.status_code == 200
    res = client.get("/holdings")
    # 20 should appear; 40 should not (alone — it may still appear inside other numbers).
    assert ">20<" in res.text or ">20.00<" in res.text or ">20 <" in res.text


def test_post_splits_requires_auth(client: TestClient):
    res = client.post("/splits", data={"ticker": "X", "ex_date": "2025-01-01", "ratio": 2})
    # Unauthenticated requests redirect to /login (HTML) or 401 (JSON).
    assert res.status_code in (303, 401)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/web/test_splits.py -v`

Expected: FAIL with `404 Not Found` on `/splits` (route doesn't exist yet).

- [ ] **Step 3: Implement the routes**

Create `marketpulse/web/routes/splits.py`:

```python
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from marketpulse.db.models import StockSplit
from marketpulse.holdings.splits import SplitError, record_split
from marketpulse.holdings.trades import recompute_ticker
from marketpulse.logging import get_logger
from marketpulse.web.deps import get_db, require_auth

router = APIRouter()
log = get_logger(__name__)


def _serialize(s: StockSplit) -> dict:
    return {
        "id": s.id,
        "ticker": s.ticker,
        "ex_date": s.ex_date.isoformat(),
        "ratio": s.ratio,
        "source": s.source,
        "notes": s.notes,
    }


@router.post("/splits")
def splits_create(
    ticker: str = Form(...),
    ex_date: str = Form(...),
    ratio: float = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """Record a stock split, then recompute the affected ticker so the
    Holding row reflects the new share count and avg_cost immediately.
    """
    try:
        ex_dt = datetime.strptime(ex_date.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid ex_date: {exc}") from exc
    try:
        s = record_split(
            db, ticker=ticker, ex_date=ex_dt, ratio=ratio,
            source="manual", notes=notes or None,
        )
    except SplitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    recompute_ticker(db, s.ticker)
    return JSONResponse(_serialize(s))


@router.get("/splits")
def splits_list(
    ticker: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    q = db.query(StockSplit).order_by(StockSplit.ex_date.desc())
    if ticker:
        q = q.filter(StockSplit.ticker == ticker.upper())
    return JSONResponse([_serialize(s) for s in q.all()])


@router.delete("/splits/{split_id}", response_class=HTMLResponse)
def splits_delete(
    split_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    from marketpulse.holdings.splits import delete_split
    try:
        ticker = delete_split(db, split_id)
    except SplitError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    recompute_ticker(db, ticker)
    return HTMLResponse("")
```

- [ ] **Step 4: Register the router**

In `marketpulse/web/main.py`, update the imports inside `create_app()` and the `include_router` calls. Find the block:

```python
    from marketpulse.web.routes import (  # noqa: WPS433
        alerts,
        auth,
        health,
        holdings,
        home,
        recap,
        stock,
        trades,
        watchlist,
    )
```

Replace it with:

```python
    from marketpulse.web.routes import (  # noqa: WPS433
        alerts,
        auth,
        health,
        holdings,
        home,
        recap,
        splits,
        stock,
        trades,
        watchlist,
    )
```

Then in the `include_router` block, after `app.include_router(trades.router)` add:

```python
    app.include_router(splits.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/web/test_splits.py -v`

Expected: PASS, 7 tests.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/web/routes/splits.py marketpulse/web/main.py tests/web/test_splits.py
git commit -m "feat(splits): POST/GET/DELETE /splits routes with recompute"
```

---

## Task 6: `YFinanceClient.fetch_splits`

**Files:**
- Modify: `marketpulse/data/yfinance_client.py`
- Test: `tests/unit/test_yfinance_splits.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_yfinance_splits.py`:

```python
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd


def test_fetch_splits_returns_list_of_date_ratio_tuples() -> None:
    from marketpulse.data.yfinance_client import YFinanceClient

    fake_series = pd.Series(
        data=[2.0, 3.0],
        index=pd.to_datetime(["2022-01-13 00:00:00-05:00", "2025-11-20 00:00:00-05:00"]),
    )

    fake_ticker = MagicMock()
    fake_ticker.splits = fake_series

    with patch("marketpulse.data.yfinance_client.yf.Ticker", return_value=fake_ticker):
        out = YFinanceClient().fetch_splits("TQQQ")

    assert out == [(date(2022, 1, 13), 2.0), (date(2025, 11, 20), 3.0)]


def test_fetch_splits_empty_returns_empty_list() -> None:
    from marketpulse.data.yfinance_client import YFinanceClient

    fake_ticker = MagicMock()
    fake_ticker.splits = pd.Series(dtype=float)

    with patch("marketpulse.data.yfinance_client.yf.Ticker", return_value=fake_ticker):
        assert YFinanceClient().fetch_splits("NOSPLIT") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_yfinance_splits.py -v`

Expected: FAIL with `AttributeError: 'YFinanceClient' object has no attribute 'fetch_splits'`.

- [ ] **Step 3: Implement `fetch_splits`**

In `marketpulse/data/yfinance_client.py`, add this method to the `YFinanceClient` class (insert after an existing `fetch_*` method, e.g. after `fetch_quote`):

```python
    @_retry
    def fetch_splits(self, ticker: str) -> list[tuple[date, float]]:
        """Return historical splits for a ticker as (ex_date, ratio) pairs.

        ratio = new_shares / old_shares (forward 1:2 = 2.0, reverse 5:1 = 0.2).
        Returns an empty list if yfinance has no split history. Network and
        rate-limit errors propagate through `_retry` and surface to the caller.
        """
        s = yf.Ticker(ticker).splits
        if s is None or s.empty:
            return []
        out: list[tuple[date, float]] = []
        for ts, ratio in s.items():
            try:
                d = ts.date()
            except AttributeError:
                # Defensive: yfinance has historically returned naive datetimes.
                d = datetime.fromisoformat(str(ts)).date()
            out.append((d, float(ratio)))
        return out
```

Update the imports at the top of the file. The line `from datetime import UTC, datetime` needs `date` added:

```python
from datetime import UTC, date, datetime
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_yfinance_splits.py -v`

Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/data/yfinance_client.py tests/unit/test_yfinance_splits.py
git commit -m "feat(splits): YFinanceClient.fetch_splits"
```

---

## Task 7: `run_detect_corporate_actions` scheduler job

**Files:**
- Modify: `marketpulse/scheduler/jobs.py`
- Test: `tests/unit/test_scheduler_jobs.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_scheduler_jobs.py`:

```python
def test_detect_corporate_actions_records_new_splits(monkeypatch) -> None:
    """Job should call fetch_splits per held/watched ticker and persist new rows."""
    from datetime import date
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "y" * 32)
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    fake_session = MagicMock()
    # Holdings query → 1 ticker, Watchlist → 1 ticker (disjoint set unioned to 2)
    fake_session.query.return_value.all.side_effect = [
        [MagicMock(ticker="TQQQ")],
        [MagicMock(ticker="NVDA")],
    ]

    def fake_session_scope():
        yield fake_session

    fake_yf = MagicMock()
    fake_yf.fetch_splits.side_effect = [
        [(date(2025, 11, 20), 2.0)],  # TQQQ
        [],                            # NVDA
    ]

    with patch("marketpulse.scheduler.jobs.session_scope", fake_session_scope), \
         patch("marketpulse.scheduler.jobs.YFinanceClient", return_value=fake_yf), \
         patch("marketpulse.scheduler.jobs.record_split") as rs, \
         patch("marketpulse.scheduler.jobs.recompute_ticker") as rc:
        from marketpulse.scheduler.jobs import run_detect_corporate_actions
        run_detect_corporate_actions()

    # record_split should be called once (only TQQQ had splits)
    assert rs.call_count == 1
    args, kwargs = rs.call_args
    assert kwargs["ticker"] == "TQQQ"
    assert kwargs["ex_date"] == date(2025, 11, 20)
    assert kwargs["ratio"] == 2.0
    assert kwargs["source"] == "yfinance"
    # recompute_ticker called once for TQQQ (NVDA had no new splits)
    rc.assert_called_once_with(fake_session, "TQQQ")


def test_detect_corporate_actions_idempotent(monkeypatch) -> None:
    """If a split is already recorded, SplitError is swallowed and we move on."""
    from datetime import date
    from unittest.mock import MagicMock, patch
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

    fake_yf = MagicMock()
    fake_yf.fetch_splits.return_value = [(date(2025, 11, 20), 2.0)]

    with patch("marketpulse.scheduler.jobs.session_scope", fake_session_scope), \
         patch("marketpulse.scheduler.jobs.YFinanceClient", return_value=fake_yf), \
         patch("marketpulse.scheduler.jobs.record_split",
               side_effect=SplitError("already recorded")), \
         patch("marketpulse.scheduler.jobs.recompute_ticker") as rc:
        from marketpulse.scheduler.jobs import run_detect_corporate_actions
        run_detect_corporate_actions()  # must not raise

    # Already-recorded split → no recompute needed
    rc.assert_not_called()


def test_detect_corporate_actions_yfinance_failure_does_not_propagate(monkeypatch) -> None:
    """A yfinance exception on one ticker must not abort the whole job."""
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

    fake_yf = MagicMock()
    fake_yf.fetch_splits.side_effect = [RuntimeError("yahoo timeout"), []]

    with patch("marketpulse.scheduler.jobs.session_scope", fake_session_scope), \
         patch("marketpulse.scheduler.jobs.YFinanceClient", return_value=fake_yf), \
         patch("marketpulse.scheduler.jobs.record_split"), \
         patch("marketpulse.scheduler.jobs.recompute_ticker"):
        from marketpulse.scheduler.jobs import run_detect_corporate_actions
        run_detect_corporate_actions()  # must not raise

    assert fake_yf.fetch_splits.call_count == 2  # both tickers attempted
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_scheduler_jobs.py -v -k "detect_corporate"`

Expected: FAIL with `ImportError: cannot import name 'run_detect_corporate_actions'`.

- [ ] **Step 3: Add the job + import dependencies**

In `marketpulse/scheduler/jobs.py`, add these imports near the existing ones (after the YFinanceClient import on line 15):

```python
from marketpulse.db.models import Holding, WatchlistItem
from marketpulse.holdings.splits import SplitError, record_split
from marketpulse.holdings.trades import recompute_ticker
```

Then add the new job function above `build_scheduler` (around line 110):

```python
def run_detect_corporate_actions() -> None:
    """Pull split history from yfinance for every held/watched ticker.

    Idempotent — re-runs are safe because (ticker, ex_date) is unique and
    SplitError on duplicates is swallowed. New splits trigger recompute_ticker
    for that ticker only. yfinance failures log a warning and are skipped.
    """
    log.info("detect_corporate_actions_start")
    yf_client = YFinanceClient()
    gen = session_scope()
    db = next(gen)
    try:
        tickers = {h.ticker for h in db.query(Holding).all()} | \
                  {w.ticker for w in db.query(WatchlistItem).all()}
        for t in sorted(tickers):
            try:
                splits = yf_client.fetch_splits(t)
            except Exception as exc:  # noqa: BLE001
                log.warning("split_fetch_failed", ticker=t, error=str(exc))
                continue
            recompute_needed = False
            for ex_date, ratio in splits:
                try:
                    record_split(
                        db, ticker=t, ex_date=ex_date, ratio=ratio,
                        source="yfinance",
                    )
                    log.info("split_recorded", ticker=t,
                             ex_date=str(ex_date), ratio=ratio)
                    recompute_needed = True
                except SplitError:
                    # Already recorded — uniqueness constraint hit. Expected
                    # on every re-run; no log spam.
                    pass
            if recompute_needed:
                recompute_ticker(db, t)
    finally:
        db.close()
    log.info("detect_corporate_actions_done")
```

- [ ] **Step 4: Schedule the job**

Inside `build_scheduler()` in `marketpulse/scheduler/jobs.py`, after the existing `news_purge` job registration, add:

```python
    # Daily split-detection: runs once at 17:00 ET (after the daily recap)
    # so any same-day splits show up in the next morning's view.
    sched.add_job(
        run_detect_corporate_actions,
        trigger=CronTrigger(hour=17, minute=0, day_of_week="mon-fri"),
        id="detect_corporate_actions", replace_existing=True, misfire_grace_time=3600,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_scheduler_jobs.py -v`

Expected: PASS, 3 new tests + pre-existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/scheduler/jobs.py tests/unit/test_scheduler_jobs.py
git commit -m "feat(splits): daily detect_corporate_actions scheduler job"
```

---

## Task 8: Unified `/trades` timeline backend

**Files:**
- Modify: `marketpulse/web/routes/trades.py`
- Test: `tests/web/test_trades.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_trades.py`:

```python
def test_trades_timeline_shows_splits_and_dividends(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    # Trade
    client.post("/trades", data={
        "ticker": "TQQQ", "action": "buy", "quantity": 20, "price": 30,
        "fees": 0, "executed_at": "2024-01-15",
    })
    # Split
    client.post("/splits", data={
        "ticker": "TQQQ", "ex_date": "2025-11-20", "ratio": 2,
    })
    # Dividend
    client.post("/dividends", data={
        "ticker": "TQQQ", "ex_date": "2025-09-24",
        "amount_per_share": 0.10, "total_amount": 4.0,
    })

    res = client.get("/trades")
    assert res.status_code == 200
    # All three event types render
    assert "买入" in res.text
    assert "拆股" in res.text or "1 → 2" in res.text
    assert "分红" in res.text


def test_trades_timeline_filter_splits_only(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    client.post("/trades", data={
        "ticker": "X", "action": "buy", "quantity": 10, "price": 100,
        "fees": 0, "executed_at": "2024-01-15",
    })
    client.post("/splits", data={
        "ticker": "X", "ex_date": "2025-01-01", "ratio": 2,
    })

    res = client.get("/trades?event_type=split")
    assert res.status_code == 200
    assert "拆股" in res.text or "1 → 2" in res.text
    # The buy row should not appear in split-only view
    assert "买入" not in res.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/web/test_trades.py -v -k "timeline or filter"`

Expected: FAIL — template currently only renders Trade rows, no split/dividend display, no `event_type` filter.

- [ ] **Step 3: Rewrite the GET /trades handler to build a unified timeline**

In `marketpulse/web/routes/trades.py`, replace the existing `trades_page` function (lines 37-56) with:

```python
@router.get("/trades", response_class=HTMLResponse)
def trades_page(
    request: Request,
    ticker: str | None = None,
    event_type: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """Unified timeline of Trade + StockSplit + Dividend events.

    `event_type` filter values: "trade" | "split" | "dividend" | None (all).
    """
    from datetime import UTC, datetime, time

    from marketpulse.db.models import Dividend, StockSplit

    tnorm = ticker.upper() if ticker else None
    events: list[dict] = []

    if event_type in (None, "trade"):
        tq = db.query(Trade)
        if tnorm:
            tq = tq.filter(Trade.ticker == tnorm)
        for t in tq.all():
            when = t.executed_at or t.created_at
            events.append({"kind": "trade", "when": when, "obj": t})

    _EOD = time(23, 59, 59, tzinfo=UTC)
    if event_type in (None, "split"):
        sq = db.query(StockSplit)
        if tnorm:
            sq = sq.filter(StockSplit.ticker == tnorm)
        for s in sq.all():
            events.append({
                "kind": "split",
                "when": datetime.combine(s.ex_date, _EOD),
                "obj": s,
            })

    if event_type in (None, "dividend"):
        dq = db.query(Dividend)
        if tnorm:
            dq = dq.filter(Dividend.ticker == tnorm)
        for d in dq.all():
            events.append({
                "kind": "dividend",
                "when": datetime.combine(d.ex_date, _EOD),
                "obj": d,
            })

    # Newest first, capped at 200 (matches the existing limit).
    events.sort(key=lambda e: e["when"], reverse=True)
    events = events[:200]

    return templates.TemplateResponse(
        request,
        "trades.html",
        {
            "events": events,
            "filter_ticker": tnorm,
            "filter_event_type": event_type,
            "realized_pl_total": total_realized_pl(db, ticker=ticker),
        },
    )
```

Also update the POST `/trades` response to use the new shape. Find the block in `trades_add` (around lines 105-121) that builds the response and replace it with:

```python
    # Re-render the full timeline so the new row + totals refresh.
    from datetime import UTC, datetime, time
    from marketpulse.db.models import Dividend, StockSplit

    events: list[dict] = []
    for t in db.query(Trade).all():
        when = t.executed_at or t.created_at
        events.append({"kind": "trade", "when": when, "obj": t})
    _EOD = time(23, 59, 59, tzinfo=UTC)
    for s in db.query(StockSplit).all():
        events.append({"kind": "split", "when": datetime.combine(s.ex_date, _EOD), "obj": s})
    for d in db.query(Dividend).all():
        events.append({"kind": "dividend", "when": datetime.combine(d.ex_date, _EOD), "obj": d})
    events.sort(key=lambda e: e["when"], reverse=True)
    events = events[:200]

    return templates.TemplateResponse(
        request,
        "partials/trades_table.html",
        {
            "events": events,
            "filter_ticker": None,
            "filter_event_type": None,
            "realized_pl_total": total_realized_pl(db),
        },
    )
```

- [ ] **Step 4: Run tests to verify backend produces the right data shape**

The template still needs updating (Task 9), so the tests won't fully pass yet. Run a quick smoke check that the route doesn't 500:

Run: `pytest tests/web/test_trades.py::test_trades_page_empty -v`

Expected: PASS (the empty case still works).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/web/routes/trades.py tests/web/test_trades.py
git commit -m "refactor(trades): unify route to produce Trade+Split+Dividend timeline"
```

---

## Task 9: Update `/trades` template + partial to render three event shapes

**Files:**
- Modify: `marketpulse/web/templates/trades.html`
- Modify: `marketpulse/web/templates/partials/trades_table.html`

- [ ] **Step 1: Update `trades.html` with filter strip + multi-type form**

Replace the entire contents of `marketpulse/web/templates/trades.html` with:

```html
{% extends "base.html" %}
{% block content %}
<section class="bg-white rounded-md shadow-sm p-4">
  <header class="flex items-center justify-between mb-3">
    <h1 class="font-semibold">
      交易记录
      {% if filter_ticker %}— {{ filter_ticker }}{% endif %}
    </h1>
    <div class="text-sm text-slate-600 flex items-center gap-4">
      <a href="/trades/import" class="text-slate-700 hover:underline">导入 Robinhood CSV</a>
      <span>已实现盈亏</span>
      <span class="font-semibold {% if realized_pl_total >= 0 %}text-green-600{% else %}text-red-600{% endif %}">
        ${{ "%+.2f"|format(realized_pl_total) }}
      </span>
    </div>
  </header>

  <!-- Filter strip: links re-fetch with ?event_type=... -->
  <div class="flex gap-2 mb-3 text-sm">
    {% set base = "/trades" + ("?ticker=" + filter_ticker if filter_ticker else "") %}
    {% set sep = "&" if filter_ticker else "?" %}
    <a href="{{ base }}"
       class="px-2 py-0.5 rounded {% if not filter_event_type %}bg-slate-900 text-white{% else %}bg-slate-100 text-slate-700{% endif %}">全部</a>
    <a href="{{ base }}{{ sep }}event_type=trade"
       class="px-2 py-0.5 rounded {% if filter_event_type == 'trade' %}bg-slate-900 text-white{% else %}bg-slate-100 text-slate-700{% endif %}">仅买卖</a>
    <a href="{{ base }}{{ sep }}event_type=split"
       class="px-2 py-0.5 rounded {% if filter_event_type == 'split' %}bg-slate-900 text-white{% else %}bg-slate-100 text-slate-700{% endif %}">仅拆股</a>
    <a href="{{ base }}{{ sep }}event_type=dividend"
       class="px-2 py-0.5 rounded {% if filter_event_type == 'dividend' %}bg-slate-900 text-white{% else %}bg-slate-100 text-slate-700{% endif %}">仅分红</a>
  </div>

  <!-- Type-aware form. JS swaps which fields are visible based on the type select. -->
  <form id="event-form"
        hx-post="/trades" hx-target="#trades-container" hx-swap="innerHTML"
        hx-on::after-request="if(event.detail.successful) this.reset()"
        class="grid grid-cols-2 md:grid-cols-7 gap-2 mb-4 text-sm">
    <select name="event_kind" id="event-kind"
            onchange="onEventKindChange(this.value)"
            class="border rounded px-3 py-1">
      <option value="buy">买入</option>
      <option value="sell">卖出</option>
      <option value="split">拆股</option>
      <option value="dividend">分红</option>
    </select>
    <input name="ticker" placeholder="代码" required
           class="border rounded px-3 py-1 uppercase" />

    <!-- Trade-only fields -->
    <input name="quantity" type="number" step="any" min="0" placeholder="数量" required
           class="border rounded px-3 py-1 trade-field" />
    <input name="price" type="number" step="any" min="0" placeholder="价格 $" required
           class="border rounded px-3 py-1 trade-field" />
    <input name="fees" type="number" step="any" min="0" placeholder="手续费 $" value="0"
           class="border rounded px-3 py-1 trade-field" />

    <!-- Split-only fields -->
    <input name="ratio" type="number" step="any" min="0.0001" placeholder="比例 (1:2 填 2)"
           class="border rounded px-3 py-1 split-field hidden" />
    <input name="ex_date" type="date" placeholder="生效日期"
           class="border rounded px-3 py-1 split-field hidden" />

    <!-- Dividend-only fields -->
    <input name="amount_per_share" type="number" step="any" min="0" placeholder="每股金额 $"
           class="border rounded px-3 py-1 dividend-field hidden" />
    <input name="total_amount" type="number" step="any" min="0" placeholder="总金额 $"
           class="border rounded px-3 py-1 dividend-field hidden" />

    <input name="notes" placeholder="备注(可选)"
           class="border rounded px-3 py-1 col-span-2 md:col-span-1 min-w-0" />
    <button class="bg-slate-900 text-white px-3 py-1 rounded">记录</button>
  </form>

  <script>
    // Swap the form action AND which fields are required based on event kind.
    // Trade kinds POST to /trades; split → /splits; dividend → /dividends.
    function onEventKindChange(kind) {
      const form = document.getElementById('event-form');
      const tradeFields = form.querySelectorAll('.trade-field');
      const splitFields = form.querySelectorAll('.split-field');
      const dividendFields = form.querySelectorAll('.dividend-field');
      const all = [tradeFields, splitFields, dividendFields];
      all.forEach(group => group.forEach(el => {
        el.classList.add('hidden');
        el.required = false;
      }));
      let action = '/trades';
      let showGroup = tradeFields;
      if (kind === 'split') { action = '/splits'; showGroup = splitFields; }
      else if (kind === 'dividend') { action = '/dividends'; showGroup = dividendFields; }
      showGroup.forEach(el => { el.classList.remove('hidden'); el.required = true; });
      form.setAttribute('hx-post', action);
      // For trade kinds we need an `action` form field carrying buy/sell.
      let actionInput = form.querySelector('input[name="action"]');
      if (kind === 'buy' || kind === 'sell') {
        if (!actionInput) {
          actionInput = document.createElement('input');
          actionInput.type = 'hidden';
          actionInput.name = 'action';
          form.appendChild(actionInput);
        }
        actionInput.value = kind;
      } else if (actionInput) {
        actionInput.remove();
      }
    }
    // Initialize on load (default kind is "buy").
    onEventKindChange(document.getElementById('event-kind').value);
  </script>

  <div id="trades-container">
    {% include "partials/trades_table.html" %}
  </div>
</section>
{% endblock %}
```

- [ ] **Step 2: Update `partials/trades_table.html` for three row shapes**

Replace the entire contents of `marketpulse/web/templates/partials/trades_table.html` with:

```html
<table class="w-full text-sm">
  <thead class="text-left text-slate-500">
    <tr>
      <th class="px-2 py-1">时间</th>
      <th>代码</th>
      <th>类型</th>
      <th class="text-right">详情</th>
      <th class="text-right">盈亏</th>
      <th>备注</th>
      <th></th>
    </tr>
  </thead>
  <tbody>
    {% for e in events %}
      {% if e.kind == "trade" %}
        {% set t = e.obj %}
        <tr class="border-t" id="trade-row-{{ t.id }}">
          <td class="px-2 py-1 text-slate-500 text-xs">
            {{ (t.executed_at or t.created_at).strftime("%Y-%m-%d %H:%M") }}
          </td>
          <td><a href="/stock/{{ t.ticker }}" class="font-medium">{{ t.ticker }}</a></td>
          <td>
            {% if t.action == "buy" %}
              <span class="text-blue-600 text-xs">🟦 买入</span>
            {% else %}
              <span class="text-orange-600 text-xs">🟧 卖出</span>
            {% endif %}
          </td>
          <td class="text-right">
            {{ "%g"|format(t.quantity) }} 股 @ ${{ "%.2f"|format(t.price) }}
            {% if t.fees %}<span class="text-xs text-slate-500">(手续费 ${{ "%.2f"|format(t.fees) }})</span>{% endif %}
          </td>
          <td class="text-right {% if t.realized_pl is not none and t.realized_pl >= 0 %}text-green-600{% elif t.realized_pl is not none %}text-red-600{% endif %}">
            {% if t.realized_pl is not none %}${{ "%+.2f"|format(t.realized_pl) }}{% endif %}
          </td>
          <td class="text-xs text-slate-500 max-w-xs truncate">{{ t.notes or "" }}</td>
          <td class="text-right">
            <button
              hx-delete="/trades/{{ t.id }}"
              hx-target="#trade-row-{{ t.id }}"
              hx-swap="outerHTML"
              hx-confirm="删除这笔交易?会自动重算该代码的持仓和已实现盈亏。"
              class="text-red-600 text-xs hover:underline">删除</button>
          </td>
        </tr>
      {% elif e.kind == "split" %}
        {% set s = e.obj %}
        <tr class="border-t bg-purple-50" id="split-row-{{ s.id }}">
          <td class="px-2 py-1 text-slate-500 text-xs">{{ s.ex_date.strftime("%Y-%m-%d") }}</td>
          <td><a href="/stock/{{ s.ticker }}" class="font-medium">{{ s.ticker }}</a></td>
          <td><span class="text-purple-700 text-xs">🟪 拆股</span></td>
          <td class="text-right">
            1 → {{ "%g"|format(s.ratio) }} (比例 {{ "%g"|format(s.ratio) }})
            <span class="text-xs text-slate-400">[{{ s.source }}]</span>
          </td>
          <td class="text-right text-slate-400">—</td>
          <td class="text-xs text-slate-500 max-w-xs truncate">{{ s.notes or "" }}</td>
          <td class="text-right">
            <button
              hx-delete="/splits/{{ s.id }}"
              hx-target="#split-row-{{ s.id }}"
              hx-swap="outerHTML"
              hx-confirm="删除这条拆股记录?会自动重算该代码的持仓。"
              class="text-red-600 text-xs hover:underline">删除</button>
          </td>
        </tr>
      {% elif e.kind == "dividend" %}
        {% set d = e.obj %}
        <tr class="border-t bg-emerald-50" id="dividend-row-{{ d.id }}">
          <td class="px-2 py-1 text-slate-500 text-xs">{{ d.ex_date.strftime("%Y-%m-%d") }}</td>
          <td><a href="/stock/{{ d.ticker }}" class="font-medium">{{ d.ticker }}</a></td>
          <td><span class="text-emerald-700 text-xs">💰 分红</span></td>
          <td class="text-right">
            ${{ "%.4f"|format(d.amount_per_share) }}/股 总 ${{ "%.2f"|format(d.total_amount) }}
          </td>
          <td class="text-right text-slate-400">—</td>
          <td class="text-xs text-slate-500 max-w-xs truncate">{{ d.notes or "" }}</td>
          <td class="text-right">
            <button
              hx-delete="/dividends/{{ d.id }}"
              hx-target="#dividend-row-{{ d.id }}"
              hx-swap="outerHTML"
              hx-confirm="删除这条分红记录?"
              class="text-red-600 text-xs hover:underline">删除</button>
          </td>
        </tr>
      {% endif %}
    {% endfor %}
    {% if not events %}
    <tr><td colspan="7" class="px-4 py-8 text-center text-slate-500">
      暂无记录。在上方表单中添加第一条。
    </td></tr>
    {% endif %}
  </tbody>
</table>
```

- [ ] **Step 3: Run timeline tests to verify they pass**

Run: `pytest tests/web/test_trades.py -v`

Expected: PASS, including the two new timeline tests. Pre-existing tests (`test_trade_post_accepts_executed_at`, `test_robinhood_import_*`) must continue to pass — the POST endpoint still accepts the same fields.

If a pre-existing test fails because it scans for a deleted column (e.g. the standalone "数量" column header), update the assertion to match the new combined "详情" column.

- [ ] **Step 4: Manual smoke check (optional but recommended)**

Run `uvicorn marketpulse.web.main:app --reload` and visit `/trades`. Verify:
- Selecting "拆股" in the dropdown shows ratio + ex_date fields and submits to `/splits`
- Selecting "分红" shows per-share + total fields and submits to `/dividends`
- Selecting "买入"/"卖出" shows quantity/price/fees and submits to `/trades`
- The "仅拆股" filter chip shows only purple-tinted rows.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/web/templates/trades.html marketpulse/web/templates/partials/trades_table.html
git commit -m "feat(splits): unified /trades UI with type filter and event-shaped rows"
```

---

## Task 10: Migration script — convert existing `price=0` hack rows

**Files:**
- Create: `scripts/cleanup_split_hacks.py`

- [ ] **Step 1: Write the migration script**

Create `scripts/cleanup_split_hacks.py`:

```python
#!/usr/bin/env python3
"""One-off migration: convert pre-feature 'price=0 buy with 拆股 in notes'
Trade rows into proper StockSplit rows.

Idempotent — uses (ticker, ex_date) uniqueness on stock_splits; rows already
migrated are skipped.

Usage:
    DB_URL="sqlite:///./data/marketpulse.db" python scripts/cleanup_split_hacks.py

After verifying output, delete this script — it's not meant to live in the repo.
"""
from __future__ import annotations

import os
import re
import sys

from marketpulse.db import base as db_base
from marketpulse.db.models import Trade
from marketpulse.holdings.splits import SplitError, record_split
from marketpulse.holdings.trades import recompute_ticker
from marketpulse.logging import configure_logging, get_logger

log = get_logger(__name__)


def main() -> int:
    configure_logging("INFO")
    db_url = os.environ.get("DB_URL")
    if not db_url:
        print("DB_URL env var required", file=sys.stderr)
        return 1

    db_base.init_engine(db_url)
    gen = db_base.session_scope()
    session = next(gen)
    try:
        hack_rows = session.query(Trade).filter(
            Trade.price == 0,
            Trade.notes.like("%拆股%"),
        ).all()

        if not hack_rows:
            print("✓ No hack rows found — nothing to migrate.")
            return 0

        print(f"Found {len(hack_rows)} candidate trade rows to migrate.")
        unparsed: list[int] = []
        migrated = 0
        skipped = 0

        for t in hack_rows:
            # Parse ratio from notes: supported formats "1:2", "1 → 2", "1拆2", "1-2"
            m = re.search(r"(\d+)\s*[:→拆\-]\s*(\d+)", t.notes or "")
            if m:
                ratio = int(m.group(2)) / int(m.group(1))
            else:
                ratio = 2.0
                unparsed.append(t.id)
                log.warning("split_migration_fallback",
                            trade_id=t.id, notes=t.notes, defaulted_ratio=ratio)

            ex_date = (t.executed_at or t.created_at).date()
            try:
                record_split(
                    session, ticker=t.ticker, ex_date=ex_date, ratio=ratio,
                    source="import",
                    notes=f"Migrated from trade #{t.id}: {t.notes or ''}".strip(),
                )
                migrated += 1
            except SplitError as exc:
                # Already migrated in a previous run — that's fine.
                log.info("split_migration_already_exists",
                         trade_id=t.id, error=str(exc))
                skipped += 1

            session.delete(t)

        session.commit()

        for ticker in {t.ticker for t in hack_rows}:
            recompute_ticker(session, ticker)

        print(f"\n✓ Migrated {migrated} hack rows, skipped {skipped} duplicates.")
        if unparsed:
            print(f"⚠️  {len(unparsed)} rows used the default 2.0 ratio because "
                  f"the notes didn't parse. Trade IDs: {unparsed}")
            print("   Review each and POST /splits with the correct ratio if needed.")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke-test on a scratch DB**

Build a tiny throwaway test:

```bash
# Make a scratch DB
SCRATCH="$(mktemp -d)/scratch.db"
DB_URL="sqlite:///$SCRATCH" alembic upgrade head

# Insert a hack row via Python
DB_URL="sqlite:///$SCRATCH" python -c "
from marketpulse.db import base as db_base
from marketpulse.db.models import Trade
from datetime import datetime, UTC
db_base.init_engine('sqlite:///$SCRATCH')
gen = db_base.session_scope()
s = next(gen)
s.add(Trade(ticker='TQQQ', action='buy', quantity=20, price=0,
            executed_at=datetime(2025, 11, 20, tzinfo=UTC),
            notes='1:2 拆股调整'))
s.commit()
"

# Run the migration
DB_URL="sqlite:///$SCRATCH" python scripts/cleanup_split_hacks.py

# Verify
DB_URL="sqlite:///$SCRATCH" python -c "
from marketpulse.db import base as db_base
from marketpulse.db.models import Trade, StockSplit
db_base.init_engine('sqlite:///$SCRATCH')
gen = db_base.session_scope()
s = next(gen)
assert s.query(Trade).filter(Trade.price == 0).count() == 0, 'hack trade still present'
splits = s.query(StockSplit).all()
assert len(splits) == 1, f'expected 1 split, got {len(splits)}'
assert splits[0].ticker == 'TQQQ' and splits[0].ratio == 2.0
print('✓ Migration produced 1 TQQQ split @ ratio 2.0')
"
```

Expected: All three commands succeed, final message is `✓ Migration produced 1 TQQQ split @ ratio 2.0`.

- [ ] **Step 3: Commit**

```bash
git add scripts/cleanup_split_hacks.py
git commit -m "feat(splits): one-off migration script for legacy hack rows"
```

---

## Task 11: Full-suite regression + final commit

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `pytest -v`

Expected: All tests pass, no regressions.

- [ ] **Step 2: Run ruff**

Run: `ruff check marketpulse tests scripts`

Expected: clean.

If ruff complains about unused imports inside the deeply-nested `import` statements I used in routes, hoist them to the top of the file.

- [ ] **Step 3: Confirm migration applies cleanly to the production DB schema**

```bash
DB_URL="sqlite:///./data/marketpulse.db" alembic current
DB_URL="sqlite:///./data/marketpulse.db" alembic upgrade head
DB_URL="sqlite:///./data/marketpulse.db" alembic current
```

Expected: `current` shows `0007 (head)` after upgrade.

- [ ] **Step 4: Tag the feature commit**

```bash
git log --oneline -15
```

Confirm the 10 commits from Tasks 1-10 are present. No additional commit needed here.

---

## After-deployment runbook (one-time, not part of git history)

Once this branch is deployed and the scheduler is live:

1. SSH to the prod box.
2. Run `DB_URL="sqlite:///./data/marketpulse.db" python scripts/cleanup_split_hacks.py`.
3. Review any "⚠️ defaulted ratio" warnings — manually correct via POST /splits if needed.
4. `git rm scripts/cleanup_split_hacks.py` and commit (the script is single-use; keeping it in the repo invites accidental reruns).
5. Re-run the TQQQ import script (`/tmp/import_tqqq_trades.py`) **without** the synthetic split-adjustment trade — the StockSplit row will already exist (auto-detected by `detect_corporate_actions`), so the import script only needs to POST the real 58 trades + 14 dividends.
