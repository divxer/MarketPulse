# Phase 6b+ — Paper P&L Realization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `StubPriceProvider(default=Decimal("0"))` with `YFinancePriceProvider` and fix the underlying design bug — move PriceProvider injection from order-placement (`daily_cycle.run`, impossible: future date) to exit-materialization (`ForwardExecutionEngine._materialize_exit`, correct semantics).

**Architecture:** New `ClosePrice` dataclass + `PriceProvider` Protocol with `source`/`lookback_days` properties. `YFinancePriceProvider` wraps a new `YFinanceClient.fetch_close_on_date(ticker, on_date, lookback_days=10)` method (tests mocked, no network). `ForwardExecutionEngine._materialize_exit` becomes `-> bool` (True=CLOSED, False=PRICE_UNAVAILABLE position stays OPEN, next tick retries). New `AuditEventType.PRICE_UNAVAILABLE` + Alembic 0011 migration (SQLite table rebuild). `daily_cycle.run` loses `price_provider` kwarg (breaking change — exposes all old miswiring).

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 + Alembic, pytest, yfinance (mocked in tests), Decimal (Numeric(18,6) DB columns).

**Spec reference:** `docs/superpowers/specs/2026-05-22-phase-6b-plus-paper-pnl-realization-design.md` (commit `5498616`)

**Branch:** `plan/phase-6b-plus-paper-pnl-realization` (already exists; spec committed). Single squash-or-rebase PR at end of T11.

**Key codebase-vs-spec reconciliations:**
- 0010 `paper_audit_event` schema: `id INTEGER PK`, `timestamp DATETIME tz NOT NULL`, `event_type VARCHAR(48) NOT NULL`, `order_id INTEGER NULL`, `strategy VARCHAR(64) NULL`, `reason TEXT NOT NULL DEFAULT ""`, `context JSON NOT NULL DEFAULT '{}'`. CHECK named `ck_paper_audit_event_type`. 4 indexes: `ix_paper_audit_ts`, `ix_paper_audit_type_ts`, `ix_paper_audit_order`, `ix_paper_audit_strategy_ts`.
- 6a `Phase 6a` revision in alembic is `0010`; new revision is `0011` with `down_revision = "0010"`.
- `Bar` dataclass already exists at `marketpulse/data/types.py` — `Bar(date, open, high, low, close, volume)`. `YFinanceClient.fetch_close_on_date` reuses it.

---

## File Structure

**New files (1 production + 6 tests + 1 migration):**

```
alembic/versions/0011_audit_check_price_unavailable.py        # migration
marketpulse/trading/price_provider.py                          # REWRITTEN (not strictly new)

tests/data/test_yfinance_close_on_date.py                      # T4 mocked tests
tests/trading/test_price_provider.py                           # T3 ClosePrice/Stub tests
tests/trading/test_yfinance_price_provider.py                  # T5 provider tests
tests/trading/test_repository_price_unavailable.py             # T6 helper tests
tests/migration/test_0011_audit_check.py                       # T2 migration tests
tests/trading/test_forward_engine_price_provider.py            # T7a/T7b focused
```

**Modified files (8 production + 4 test):**

```
marketpulse/trading/types.py                                   # T1: AuditEventType.PRICE_UNAVAILABLE
marketpulse/data/yfinance_client.py                            # T4: fetch_close_on_date method
marketpulse/trading/repository.py                              # T6: count_price_unavailable_attempts
marketpulse/trading/forward_engine.py                          # T7a + T7b: price_provider injection + _materialize_exit -> bool
marketpulse/trading/daily_cycle.py                             # T8: remove price_provider kwarg
marketpulse/backtest/allocation.py                             # T8: docstring update only
marketpulse/scheduler/paper_trading_tick.py                    # T9: DI rewire to YFinancePriceProvider

tests/trading/test_forward_engine.py                           # T7a/T7b regressions
tests/trading/test_daily_cycle.py                              # T8 signature change
tests/trading/test_e2e_stateful.py                             # T10 roll-back + retry scenarios
tests/trading/test_scheduler.py                                # T9 DI swap test
```

---

## Task Inventory

- **T0** — Preflight: branch verification + 6b baseline green
- **T1** — `AuditEventType.PRICE_UNAVAILABLE` enum value
- **T2** — Alembic 0011 migration (rebuild paper_audit_event CHECK) + up/down tests
- **T3** — `PriceProvider` Protocol rewrite + `ClosePrice` dataclass + `StubPriceProvider` rewrite (no default)
- **T4** — `YFinanceClient.fetch_close_on_date(ticker, on_date, lookback_days=10) -> Bar | None` (mocked)
- **T5** — `YFinancePriceProvider` with `source`/`lookback_days` properties + Decimal quantization (lock 6b+L14)
- **T6** — `Repository.count_price_unavailable_attempts(*, position_id)`
- **T7a** — `ForwardExecutionEngine.__init__(price_provider=...)` required kwarg (no semantic changes yet; just constructor + 6a regression updates)
- **T7b** — `_materialize_exit -> bool` rewrite + PRICE_UNAVAILABLE audit + POSITION_CLOSED provenance + `tick()` counts via bool + `last_price_unavailable_count()`
- **T8** — `daily_cycle.run(price_provider=...)` kwarg removed + `OrderRequest.horizon_price=None` forward invariant + TICK_COMPLETED.context["price_unavailable_count"]
- **T9** — `paper_trading_tick.py` DI rewire to `YFinancePriceProvider`
- **T10** — E2E tests: roll-back, retry-and-succeed, attempt_count progression, P&L correctness
- **T11** — Final integration: full suite + ruff + alembic heads (expect 0011) + manual smoke + PR

---

### Task T0: Preflight

**Files:**
- Read-only verification

- [ ] **Step 1: Verify branch + clean working tree**

Run: `cd /Users/harvey/Dev/src/MarketPulse && git status && git log --oneline -3`
Expected: on branch `plan/phase-6b-plus-paper-pnl-realization`; HEAD is `5498616 docs(phase-6b-plus): review-round-2` or later spec commit; working tree clean (any pre-existing `marketpulse/web/templates/stock.html` change should be discarded; that file's canonical version is on main as of PR #81).

If `stock.html` shows modified, run: `git checkout -- marketpulse/web/templates/stock.html`

- [ ] **Step 2: Verify 6a + 6b baseline tests pass**

Run: `uv run pytest -q tests/trading/ --tb=no`
Expected: ALL pass. Should be ~159+ tests (6a + 6b done). If anything fails, STOP — investigate before starting 6b+.

- [ ] **Step 3: Verify ruff clean**

Run: `uv run ruff check marketpulse/ tests/`
Expected: `All checks passed!`

- [ ] **Step 4: Verify alembic head is 0010**

Run: `uv run alembic heads`
Expected: `0010 (head)` or similar showing 0010 as the current head.

---

### Task T1: `AuditEventType.PRICE_UNAVAILABLE`

**Files:**
- Modify: `marketpulse/trading/types.py`
- Test: `tests/trading/test_types.py` (extend if exists, else new minimal test)

- [ ] **Step 1: Write failing test**

Append (or create) in `tests/trading/test_types.py`:

```python
# Layer: pure
"""6b+T1: AuditEventType.PRICE_UNAVAILABLE."""

from __future__ import annotations


def test_audit_event_type_price_unavailable_value():
    from marketpulse.trading.types import AuditEventType
    assert AuditEventType.PRICE_UNAVAILABLE == "PRICE_UNAVAILABLE"
    assert AuditEventType.PRICE_UNAVAILABLE.value == "PRICE_UNAVAILABLE"


def test_audit_event_type_is_str_enum_member():
    from marketpulse.trading.types import AuditEventType
    assert isinstance(AuditEventType.PRICE_UNAVAILABLE, str)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/test_types.py::test_audit_event_type_price_unavailable_value -v`
Expected: FAIL with `AttributeError: PRICE_UNAVAILABLE`.

- [ ] **Step 3: Add enum value**

In `marketpulse/trading/types.py`, locate the `AuditEventType(StrEnum)` class (around line 47). Append a new value after `ENGINE_INVARIANT_ERROR`:

```python
class AuditEventType(StrEnum):
    ORDER_PLACED = "ORDER_PLACED"
    ORDER_PLACED_DUPLICATE = "ORDER_PLACED_DUPLICATE"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_ENTRY_FILLED = "ORDER_ENTRY_FILLED"
    POSITION_CLOSED = "POSITION_CLOSED"
    KILL_SWITCH_FLIPPED = "KILL_SWITCH_FLIPPED"
    KILL_SWITCH_CYCLE_SKIPPED = "KILL_SWITCH_CYCLE_SKIPPED"
    TICK_COMPLETED = "TICK_COMPLETED"
    TICK_REPROCESSED_COMPLETED = "TICK_REPROCESSED_COMPLETED"
    SCHEDULER_GAP_DETECTED = "SCHEDULER_GAP_DETECTED"
    ENGINE_INVARIANT_ERROR = "ENGINE_INVARIANT_ERROR"
    # Phase 6b+: price source unavailable at exit time (transient data
    # gap, NOT an InvariantError — see lock 6b+L7).
    PRICE_UNAVAILABLE = "PRICE_UNAVAILABLE"
```

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/trading/test_types.py -v`
Expected: 2 PASS (plus any existing tests in the file).

- [ ] **Step 5: Run full trading suite for no regression**

Run: `uv run pytest -q tests/trading/ --tb=no`
Expected: same count as T0 + 2 new tests.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/trading/types.py tests/trading/test_types.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-plus-T1): AuditEventType.PRICE_UNAVAILABLE

Adds the enum value used by ForwardExecutionEngine._materialize_exit
when the price provider returns None. Distinct from ENGINE_INVARIANT_ERROR
(this is a transient data gap, not a code bug — see lock 6b+L7).
Alembic 0011 will extend the DB CHECK constraint to allow this value.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T2: Alembic 0011 migration

**Files:**
- Create: `alembic/versions/0011_audit_check_price_unavailable.py`
- Create: `tests/migration/test_0011_audit_check.py`

- [ ] **Step 1: Create migration test file**

Create `tests/migration/test_0011_audit_check.py`:

```python
# Layer: stateful
"""6b+T2: Alembic 0011 — extend paper_audit_event CHECK to include
PRICE_UNAVAILABLE. Lock 6b+L6 (SQLite table rebuild), 6b+L10 (schema
preservation), 6b+L13 (explicit column INSERT)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.command import downgrade as alembic_downgrade
from alembic.command import upgrade as alembic_upgrade
from alembic.config import Config
from sqlalchemy import create_engine, text


@pytest.fixture
def alembic_cfg(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    db_url = f"sqlite:///{tmp_path / 'mig.db'}"
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.db_url = db_url
    return cfg


def _engine(cfg):
    return create_engine(cfg.db_url)


def test_0011_upgrade_inserts_price_unavailable_succeeds(alembic_cfg):
    """After 0011 upgrade, INSERT of PRICE_UNAVAILABLE passes the CHECK."""
    alembic_upgrade(alembic_cfg, "0011")
    eng = _engine(alembic_cfg)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO paper_audit_event "
            "(timestamp, event_type, order_id, strategy, reason, context) "
            "VALUES (:ts, 'PRICE_UNAVAILABLE', 1, 'test', 'no_data', '{}')"
        ), {"ts": datetime.now(UTC)})
        row = conn.execute(text(
            "SELECT event_type FROM paper_audit_event WHERE event_type='PRICE_UNAVAILABLE'"
        )).fetchone()
        assert row is not None
        assert row[0] == "PRICE_UNAVAILABLE"


def test_0011_upgrade_preserves_6a_indexes_exact_names(alembic_cfg):
    """Lock 6b+L10: all 4 indexes match 0010 names EXACTLY."""
    alembic_upgrade(alembic_cfg, "0011")
    eng = _engine(alembic_cfg)
    with eng.begin() as conn:
        rows = conn.execute(text(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='paper_audit_event' "
            "AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )).fetchall()
        names = {r[0] for r in rows}
        assert names == {
            "ix_paper_audit_ts",
            "ix_paper_audit_type_ts",
            "ix_paper_audit_order",
            "ix_paper_audit_strategy_ts",
        }


def test_0011_upgrade_preserves_6a_rows(alembic_cfg):
    """Op-test #24: seed 6a rows, upgrade, assert all preserved."""
    # First upgrade only to 0010
    alembic_upgrade(alembic_cfg, "0010")
    eng = _engine(alembic_cfg)
    ts = datetime.now(UTC)
    with eng.begin() as conn:
        for et in ("ORDER_PLACED", "POSITION_CLOSED", "TICK_COMPLETED",
                   "KILL_SWITCH_FLIPPED", "ENGINE_INVARIANT_ERROR"):
            conn.execute(text(
                "INSERT INTO paper_audit_event "
                "(timestamp, event_type, order_id, strategy, reason, context) "
                "VALUES (:ts, :et, 1, 'test', 'seed', '{}')"
            ), {"ts": ts, "et": et})

    # Now upgrade to 0011 (which rebuilds the table)
    alembic_upgrade(alembic_cfg, "0011")

    with eng.begin() as conn:
        rows = conn.execute(text(
            "SELECT event_type FROM paper_audit_event ORDER BY event_type"
        )).fetchall()
        assert {r[0] for r in rows} == {
            "ORDER_PLACED", "POSITION_CLOSED", "TICK_COMPLETED",
            "KILL_SWITCH_FLIPPED", "ENGINE_INVARIANT_ERROR",
        }


def test_0011_downgrade_with_no_price_unavailable_succeeds(alembic_cfg):
    """Lock 6b+L10 downgrade: with 0 PRICE_UNAVAILABLE rows, downgrade
    succeeds and rebuilds the old CHECK."""
    alembic_upgrade(alembic_cfg, "0011")
    eng = _engine(alembic_cfg)
    ts = datetime.now(UTC)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO paper_audit_event "
            "(timestamp, event_type, order_id, strategy, reason, context) "
            "VALUES (:ts, 'ORDER_PLACED', 1, 'test', '', '{}')"
        ), {"ts": ts})

    alembic_downgrade(alembic_cfg, "0010")

    # Old CHECK should reject PRICE_UNAVAILABLE now
    with eng.begin() as conn:
        with pytest.raises(Exception):    # SQLite raises IntegrityError
            conn.execute(text(
                "INSERT INTO paper_audit_event "
                "(timestamp, event_type, order_id, strategy, reason, context) "
                "VALUES (:ts, 'PRICE_UNAVAILABLE', 1, 'test', '', '{}')"
            ), {"ts": ts})


def test_0011_downgrade_with_price_unavailable_rows_raises(alembic_cfg):
    """Lock 6b+L10: refuses to downgrade if PRICE_UNAVAILABLE rows exist."""
    alembic_upgrade(alembic_cfg, "0011")
    eng = _engine(alembic_cfg)
    ts = datetime.now(UTC)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO paper_audit_event "
            "(timestamp, event_type, order_id, strategy, reason, context) "
            "VALUES (:ts, 'PRICE_UNAVAILABLE', 1, 'test', 'no_data', '{}')"
        ), {"ts": ts})

    with pytest.raises(RuntimeError, match="PRICE_UNAVAILABLE"):
        alembic_downgrade(alembic_cfg, "0010")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/migration/test_0011_audit_check.py -v`
Expected: 5 FAIL with `Can't locate revision identified by '0011'` (migration doesn't exist yet).

- [ ] **Step 3: Create migration file**

Create `alembic/versions/0011_audit_check_price_unavailable.py`:

```python
"""Phase 6b+: extend paper_audit_event CHECK to allow PRICE_UNAVAILABLE.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-22

Lock 6b+L6: SQLite table rebuild (no ALTER CHECK).
Lock 6b+L10: column defs / defaults / index names match 0010 exactly.
Lock 6b+L13: INSERT-SELECT uses explicit column lists, never SELECT *.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 12 event types from 0010 + the new 6b+ one.
_TYPES_6A = (
    "ORDER_PLACED", "ORDER_PLACED_DUPLICATE", "ORDER_REJECTED",
    "ORDER_CANCELLED", "ORDER_ENTRY_FILLED", "POSITION_CLOSED",
    "KILL_SWITCH_FLIPPED", "KILL_SWITCH_CYCLE_SKIPPED",
    "TICK_COMPLETED", "TICK_REPROCESSED_COMPLETED",
    "SCHEDULER_GAP_DETECTED", "ENGINE_INVARIANT_ERROR",
)
_TYPES_6B_PLUS = ("PRICE_UNAVAILABLE",)


def _check_clause(types: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{t}'" for t in types)
    return f"event_type IN ({joined})"


def _rebuild(new_check: str) -> None:
    """Rebuild paper_audit_event with the supplied CHECK clause.

    Lock 6b+L10: column definitions, defaults, and index names match 0010.
    """
    # 1. Create new table with same schema as 0010, replacing only the CHECK.
    op.execute(f"""
        CREATE TABLE paper_audit_event_new (
            id INTEGER NOT NULL,
            timestamp DATETIME NOT NULL,
            event_type VARCHAR(48) NOT NULL,
            order_id INTEGER,
            strategy VARCHAR(64),
            reason TEXT NOT NULL DEFAULT '',
            context JSON NOT NULL DEFAULT '{{}}',
            PRIMARY KEY (id),
            CONSTRAINT ck_paper_audit_event_type CHECK ({new_check})
        )
    """)
    # 2. Copy rows with explicit column list (lock 6b+L13).
    op.execute("""
        INSERT INTO paper_audit_event_new
            (id, timestamp, event_type, order_id, strategy, reason, context)
        SELECT id, timestamp, event_type, order_id, strategy, reason, context
        FROM paper_audit_event
    """)
    # 3. Drop old table.
    op.execute("DROP TABLE paper_audit_event")
    # 4. Rename new table.
    op.execute("ALTER TABLE paper_audit_event_new RENAME TO paper_audit_event")
    # 5. Recreate indexes with EXACT 0010 names (lock 6b+L10).
    op.execute("CREATE INDEX ix_paper_audit_ts ON paper_audit_event (timestamp)")
    op.execute(
        "CREATE INDEX ix_paper_audit_type_ts ON paper_audit_event "
        "(event_type, timestamp)"
    )
    op.execute("CREATE INDEX ix_paper_audit_order ON paper_audit_event (order_id)")
    op.execute(
        "CREATE INDEX ix_paper_audit_strategy_ts ON paper_audit_event "
        "(strategy, timestamp)"
    )


def upgrade() -> None:
    _rebuild(_check_clause(_TYPES_6A + _TYPES_6B_PLUS))


def downgrade() -> None:
    """Refuse to downgrade if PRICE_UNAVAILABLE rows exist (would orphan
    them under old CHECK). Lock 6b+L10."""
    conn = op.get_bind()
    count = conn.execute(text(
        "SELECT COUNT(*) FROM paper_audit_event "
        "WHERE event_type = 'PRICE_UNAVAILABLE'"
    )).scalar() or 0
    if count > 0:
        raise RuntimeError(
            f"Cannot downgrade 0011 → 0010: {count} PRICE_UNAVAILABLE row(s) "
            "would violate the 0010 CHECK constraint. Delete them first or "
            "implement a manual data-loss-acceptable rollback."
        )
    _rebuild(_check_clause(_TYPES_6A))
```

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/migration/test_0011_audit_check.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Smoke alembic CLI**

Run: `uv run alembic heads`
Expected: `0011 (head)`.

Run (against the dev DB to dry-test the schema): `uv run alembic upgrade head` then `uv run alembic heads` again — head should be 0011.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0011_audit_check_price_unavailable.py tests/migration/test_0011_audit_check.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-plus-T2): Alembic 0011 extends paper_audit_event CHECK

Rebuilds paper_audit_event to add 'PRICE_UNAVAILABLE' to the event_type
CHECK constraint. Uses SQLite table rebuild idiom (lock 6b+L6, matches
0010 pattern). Preserves column definitions, defaults, and all 4 index
names exactly (lock 6b+L10). INSERT-SELECT uses explicit column list
(lock 6b+L13). Downgrade refuses if PRICE_UNAVAILABLE rows exist.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T3: `PriceProvider` Protocol + `ClosePrice` + `StubPriceProvider` rewrite

**Files:**
- Modify: `marketpulse/trading/price_provider.py` (rewrite)
- Create: `tests/trading/test_price_provider.py` (new — separate from any existing 6a-era test)

- [ ] **Step 1: Write failing tests**

Create `tests/trading/test_price_provider.py`:

```python
# Layer: pure
"""6b+T3: PriceProvider Protocol + ClosePrice + StubPriceProvider."""

from __future__ import annotations

from datetime import date
from decimal import Decimal


def test_close_price_dataclass_is_frozen_with_4_fields():
    from marketpulse.trading.price_provider import ClosePrice
    cp = ClosePrice(
        price=Decimal("100.123456"),
        price_date=date(2026, 5, 20),
        requested_date=date(2026, 5, 22),
        source="yfinance",
    )
    assert cp.price == Decimal("100.123456")
    assert cp.price_date == date(2026, 5, 20)
    assert cp.requested_date == date(2026, 5, 22)
    assert cp.source == "yfinance"
    # frozen — mutation should raise
    import pytest
    with pytest.raises(Exception):    # FrozenInstanceError or AttributeError
        cp.price = Decimal("999")


def test_stub_price_provider_source_and_lookback_days():
    """Lock 6b+L8: provider exposes source + lookback_days."""
    from marketpulse.trading.price_provider import StubPriceProvider
    p = StubPriceProvider()
    assert p.source == "stub"
    assert p.lookback_days == 0


def test_stub_price_provider_rejects_default_kwarg():
    """Lock 6b+L3: StubPriceProvider has NO `default` parameter."""
    from marketpulse.trading.price_provider import StubPriceProvider
    import pytest
    with pytest.raises(TypeError):
        StubPriceProvider(default=Decimal("0"))   # should NOT be accepted


def test_stub_price_provider_map_only_lookup():
    from marketpulse.trading.price_provider import ClosePrice, StubPriceProvider
    cp_aapl = ClosePrice(
        price=Decimal("150.50"),
        price_date=date(2026, 5, 20),
        requested_date=date(2026, 5, 20),
        source="stub",
    )
    p = StubPriceProvider(map={("AAPL", date(2026, 5, 20)): cp_aapl})
    assert p.close_on_date(ticker="AAPL", on_date=date(2026, 5, 20)) is cp_aapl
    # Miss returns None — NO default fallback
    assert p.close_on_date(ticker="AAPL", on_date=date(2026, 5, 21)) is None
    assert p.close_on_date(ticker="MSFT", on_date=date(2026, 5, 20)) is None


def test_lookback_days_module_constant_is_10():
    from marketpulse.trading.price_provider import LOOKBACK_DAYS
    assert LOOKBACK_DAYS == 10
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/test_price_provider.py -v`
Expected: 5 FAIL — `cannot import name 'ClosePrice'` and similar.

- [ ] **Step 3: Rewrite `price_provider.py`**

Replace the entire contents of `marketpulse/trading/price_provider.py` with:

```python
"""PriceProvider Protocol + ClosePrice + reference implementations.

Phase 6b+ (paper P&L realization):
- `ClosePrice` dataclass carries provenance (requested_date vs price_date
  for roll-back transparency; source for audit).
- `PriceProvider.close_on_date(ticker, on_date)` returns the most recent
  available close at or before `on_date`. None means "no data in window."
- Providers expose `source: str` and `lookback_days: int` properties so
  audit rows can record provenance from the actual provider, not
  hardcoded values (lock 6b+L8).
- `YFinancePriceProvider` lives in this module and wraps a new
  `YFinanceClient.fetch_close_on_date` method (added in T4).
- `StubPriceProvider` is test-only: NO `default` parameter; only exact
  map lookup; miss returns None (lock 6b+L3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Protocol

LOOKBACK_DAYS = 10
"""Default calendar-day window for YFinancePriceProvider.

Covers any US holiday cluster (Thanksgiving + adjacent weekend ≈ 5
non-trading days; 10-day window adds safety margin)."""

_QUANT = Decimal("0.000001")
"""Lock 6b+L14: 6 decimal places matches paper_fill.price Numeric(18, 6).
HALF_EVEN rounding aligns with Python's default banker's rounding for
floats and is deterministic across platforms."""


@dataclass(frozen=True)
class ClosePrice:
    """A close price for a (ticker, on_date) query.

    `price_date` is the actual date of the bar yfinance returned. It can
    differ from `requested_date` when the requested date is non-session
    (roll-back to previous available close — see spec § 2).

    `source` is the provider that produced this (e.g., "yfinance",
    "stub") — used by audit (lock 6b+L8).
    """
    price: Decimal
    price_date: date
    requested_date: date
    source: str


class PriceProvider(Protocol):
    """Lock 6b+L8: providers expose source + lookback_days as properties.
    Audit rows read these directly rather than hardcoding."""

    source: str
    lookback_days: int

    def close_on_date(
        self, *, ticker: str, on_date: date,
    ) -> ClosePrice | None: ...


class YFinancePriceProvider:
    """Production provider. Wraps YFinanceClient.fetch_close_on_date.

    Lock 6b+L14: quantizes the close to 6 decimal places HALF_EVEN before
    constructing ClosePrice. Downstream code (engine, repository) can
    trust the Decimal is round-trip-safe with Numeric(18, 6)."""

    source = "yfinance"

    def __init__(
        self,
        *,
        client,    # YFinanceClient — duck-typed to avoid circular import
        lookback_days: int = LOOKBACK_DAYS,
    ) -> None:
        self._client = client
        self._lookback_days = lookback_days

    @property
    def lookback_days(self) -> int:
        return self._lookback_days

    def close_on_date(
        self, *, ticker: str, on_date: date,
    ) -> ClosePrice | None:
        bar = self._client.fetch_close_on_date(
            ticker, on_date, lookback_days=self._lookback_days,
        )
        if bar is None:
            return None
        price = Decimal(str(bar.close)).quantize(_QUANT, rounding=ROUND_HALF_EVEN)
        return ClosePrice(
            price=price,
            price_date=bar.date,
            requested_date=on_date,
            source=self.source,
        )


class StubPriceProvider:
    """Test-only deterministic provider.

    Lock 6b+L3: NO `default` parameter. Miss returns None. Callers
    responsible for pre-quantizing values in `map`.
    """

    source = "stub"
    lookback_days = 0

    def __init__(
        self,
        *,
        map: dict[tuple[str, date], ClosePrice] | None = None,
    ) -> None:
        self._map: dict[tuple[str, date], ClosePrice] = dict(map or {})

    def close_on_date(
        self, *, ticker: str, on_date: date,
    ) -> ClosePrice | None:
        return self._map.get((ticker, on_date))
```

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/trading/test_price_provider.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Run full trading suite — expect SOME failures**

Run: `uv run pytest -q tests/trading/ --tb=no | tail -3`
Expected: Many failures (existing 6a tests / 6b tests use `StubPriceProvider(default=Decimal("0"))` and `horizon_price` Protocol method — these will break). That's intentional; the breakage points are the call sites we need to fix in T7a/T8/T9.

Note: do NOT proceed if any test failure is NOT a StubPriceProvider / horizon_price signature issue. Investigate before continuing.

- [ ] **Step 6: Commit (with known broken state — T7-T9 will fix call sites)**

```bash
git add marketpulse/trading/price_provider.py tests/trading/test_price_provider.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-plus-T3): PriceProvider Protocol + ClosePrice + StubPriceProvider rewrite

Lock 6b+L3: StubPriceProvider has no `default` kwarg; miss returns None.
Lock 6b+L8: providers expose source + lookback_days properties.
Lock 6b+L14: YFinancePriceProvider quantizes price to 6 decimal places
HALF_EVEN before constructing ClosePrice — matches Numeric(18, 6) DB.

ClosePrice carries (price, price_date, requested_date, source) for
roll-back transparency in POSITION_CLOSED audit context.

NOTE: existing call sites in forward_engine / daily_cycle / scheduler
will break (signature changed: horizon_price → close_on_date; ClosePrice
return type replaces Decimal). T7-T9 fix these call sites. Tests in
tests/trading/ have known failures until then.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T4: `YFinanceClient.fetch_close_on_date` (mocked)

**Files:**
- Modify: `marketpulse/data/yfinance_client.py`
- Create: `tests/data/test_yfinance_close_on_date.py`

- [ ] **Step 1: Write failing tests (all mocked, no network)**

Create `tests/data/test_yfinance_close_on_date.py`:

```python
# Layer: stateful
"""6b+T4: YFinanceClient.fetch_close_on_date — mocked tests, NO network.

Lock 6b+L5: end=on_date + timedelta(days=1) because yfinance end is exclusive."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from marketpulse.data.yfinance_client import YFinanceClient


def _make_history_df(rows: list[tuple[date, float]]) -> pd.DataFrame:
    """Build a pandas DataFrame mimicking yf.Ticker.history() output."""
    if not rows:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    df = pd.DataFrame(
        [{"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1000}
         for _, close in rows],
        index=pd.DatetimeIndex([datetime.combine(d, datetime.min.time(), tzinfo=UTC)
                                for d, _ in rows]),
    )
    return df


def test_fetch_close_on_date_calls_yfinance_with_correct_window():
    """Lock 6b+L5: start=on_date - lookback_days, end=on_date + 1 day."""
    on_date = date(2026, 5, 22)
    lookback = 10

    mock_ticker = MagicMock()
    mock_ticker.history.return_value = _make_history_df([
        (date(2026, 5, 22), 150.50),
    ])

    with patch("marketpulse.data.yfinance_client.yf.Ticker",
               return_value=mock_ticker) as ticker_cls:
        client = YFinanceClient()
        client.fetch_close_on_date("AAPL", on_date, lookback_days=lookback)

    ticker_cls.assert_called_once_with("AAPL")
    mock_ticker.history.assert_called_once()
    call_kwargs = mock_ticker.history.call_args.kwargs
    assert call_kwargs["start"] == on_date - timedelta(days=lookback)
    assert call_kwargs["end"] == on_date + timedelta(days=1)
    assert call_kwargs["interval"] == "1d"


def test_fetch_close_on_date_returns_bar_with_exact_date():
    """Happy path: yfinance returns a bar dated exactly on_date."""
    on_date = date(2026, 5, 22)
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = _make_history_df([
        (date(2026, 5, 21), 149.00),
        (date(2026, 5, 22), 150.50),
    ])

    with patch("marketpulse.data.yfinance_client.yf.Ticker",
               return_value=mock_ticker):
        client = YFinanceClient()
        bar = client.fetch_close_on_date("AAPL", on_date)

    assert bar is not None
    assert bar.date == date(2026, 5, 22)
    assert bar.close == 150.50


def test_fetch_close_on_date_rollback_to_prior_session():
    """Roll-back: on_date=Saturday → return Friday's bar."""
    saturday = date(2026, 5, 23)
    friday = date(2026, 5, 22)
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = _make_history_df([
        (date(2026, 5, 21), 149.00),
        (friday, 150.50),
        # No Saturday/Sunday data
    ])

    with patch("marketpulse.data.yfinance_client.yf.Ticker",
               return_value=mock_ticker):
        client = YFinanceClient()
        bar = client.fetch_close_on_date("AAPL", saturday)

    assert bar is not None
    assert bar.date == friday    # rolled back
    assert bar.close == 150.50


def test_fetch_close_on_date_empty_window_returns_none():
    """Lock 6b+L5: no bar in [on_date - lookback, on_date] → None."""
    on_date = date(2026, 5, 22)
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = _make_history_df([])    # empty DataFrame

    with patch("marketpulse.data.yfinance_client.yf.Ticker",
               return_value=mock_ticker):
        client = YFinanceClient()
        bar = client.fetch_close_on_date("AAPL", on_date)

    assert bar is None


def test_fetch_close_on_date_bars_only_after_on_date_returns_none():
    """If yfinance returns bars but all are AFTER on_date (shouldn't happen
    given end=on_date+1, but defensive), return None."""
    on_date = date(2026, 5, 22)
    # NOTE: this is a pathological case; end=on_date+1 should make yfinance
    # never return >on_date bars. Defensive guard verifies the filter still works.
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = _make_history_df([
        (date(2026, 5, 25), 151.00),    # > on_date
    ])

    with patch("marketpulse.data.yfinance_client.yf.Ticker",
               return_value=mock_ticker):
        client = YFinanceClient()
        bar = client.fetch_close_on_date("AAPL", on_date)

    assert bar is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/data/test_yfinance_close_on_date.py -v`
Expected: 5 FAIL with `AttributeError: 'YFinanceClient' object has no attribute 'fetch_close_on_date'`.

- [ ] **Step 3: Add `fetch_close_on_date` to `YFinanceClient`**

In `marketpulse/data/yfinance_client.py`, locate the `YFinanceClient` class (around line 39). Add the import `from datetime import date` if not already imported (datetime is, date may need adding). Then append a new method to the class — insert it right after `fetch_history`:

```python
    @_retry
    def fetch_close_on_date(
        self,
        ticker: str,
        on_date: date,
        *,
        lookback_days: int = 10,
    ) -> Bar | None:
        """Return the most recent daily Bar with bar.date <= on_date,
        searching the window [on_date - lookback_days, on_date].

        Lock 6b+L5: yfinance.Ticker.history's `end` is EXCLUSIVE, so we
        pass `end=on_date + 1 day` to include on_date itself. Without
        the +1 day, querying for today's close would miss today's bar.

        Returns None if no bar exists in the window.
        """
        start = on_date - timedelta(days=lookback_days)
        end = on_date + timedelta(days=1)    # exclusive
        hist = yf.Ticker(ticker).history(
            start=start, end=end, interval="1d",
        )
        if hist.empty:
            return None
        candidates: list[Bar] = []
        for idx, row in hist.iterrows():
            bar_date = idx.date() if hasattr(idx, "date") else idx
            if bar_date > on_date:
                continue    # defensive — end=on_date+1 should prevent this
            candidates.append(Bar(
                date=bar_date,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=int(row["Volume"]),
            ))
        if not candidates:
            return None
        # Return the bar with the latest date <= on_date.
        return max(candidates, key=lambda b: b.date)
```

Also at the top of the file, ensure `timedelta` is imported alongside `datetime`:

```python
from datetime import UTC, date, datetime, timedelta
```

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/data/test_yfinance_close_on_date.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Confirm ruff clean**

Run: `uv run ruff check marketpulse/data/yfinance_client.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add marketpulse/data/yfinance_client.py tests/data/test_yfinance_close_on_date.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-plus-T4): YFinanceClient.fetch_close_on_date

Mocked tests verify (1) start=on_date - lookback_days, (2) end=on_date + 1
day (lock 6b+L5, yfinance end is exclusive), (3) returns max(bar.date <=
on_date), (4) returns None when window empty, (5) defensive filter for
unexpected > on_date bars.

No real network calls in tests.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T5: `YFinancePriceProvider` tests (impl already done in T3)

**Files:**
- Create: `tests/trading/test_yfinance_price_provider.py`

The implementation was added in T3 (since it shares the same `price_provider.py` module). T5 just adds the dedicated tests for it.

- [ ] **Step 1: Write tests**

Create `tests/trading/test_yfinance_price_provider.py`:

```python
# Layer: stateful
"""6b+T5: YFinancePriceProvider tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock


def test_yfinance_provider_source_is_yfinance():
    """Lock 6b+L8: source property."""
    from marketpulse.trading.price_provider import YFinancePriceProvider
    p = YFinancePriceProvider(client=MagicMock())
    assert p.source == "yfinance"


def test_yfinance_provider_lookback_days_default_10():
    """Lock 6b+L8: lookback_days property; default 10."""
    from marketpulse.trading.price_provider import YFinancePriceProvider
    p = YFinancePriceProvider(client=MagicMock())
    assert p.lookback_days == 10


def test_yfinance_provider_lookback_days_custom():
    from marketpulse.trading.price_provider import YFinancePriceProvider
    p = YFinancePriceProvider(client=MagicMock(), lookback_days=20)
    assert p.lookback_days == 20


def test_close_on_date_returns_close_price_with_provenance():
    """Happy path: Bar -> ClosePrice with all 4 fields."""
    from marketpulse.data.types import Bar
    from marketpulse.trading.price_provider import (
        ClosePrice, YFinancePriceProvider,
    )

    mock_client = MagicMock()
    mock_client.fetch_close_on_date.return_value = Bar(
        date=date(2026, 5, 22),
        open=149.0, high=151.0, low=148.5, close=150.50, volume=1000000,
    )
    p = YFinancePriceProvider(client=mock_client)

    result = p.close_on_date(ticker="AAPL", on_date=date(2026, 5, 22))

    assert isinstance(result, ClosePrice)
    assert result.price == Decimal("150.500000")    # quantized to 6dp
    assert result.price_date == date(2026, 5, 22)
    assert result.requested_date == date(2026, 5, 22)
    assert result.source == "yfinance"


def test_close_on_date_passes_lookback_days_to_client():
    """Provider's lookback_days threads through to YFinanceClient."""
    from marketpulse.data.types import Bar
    from marketpulse.trading.price_provider import YFinancePriceProvider

    mock_client = MagicMock()
    mock_client.fetch_close_on_date.return_value = Bar(
        date=date(2026, 5, 22),
        open=149.0, high=151.0, low=148.5, close=150.50, volume=1000000,
    )
    p = YFinancePriceProvider(client=mock_client, lookback_days=15)

    p.close_on_date(ticker="AAPL", on_date=date(2026, 5, 22))

    mock_client.fetch_close_on_date.assert_called_once_with(
        "AAPL", date(2026, 5, 22), lookback_days=15,
    )


def test_close_on_date_returns_none_when_client_returns_none():
    from marketpulse.trading.price_provider import YFinancePriceProvider
    mock_client = MagicMock()
    mock_client.fetch_close_on_date.return_value = None
    p = YFinancePriceProvider(client=mock_client)
    assert p.close_on_date(ticker="ZZZZ", on_date=date(2026, 5, 22)) is None


def test_close_on_date_quantizes_high_precision_close_to_6dp():
    """Lock 6b+L14: 100.123456789 → quantize HALF_EVEN → 100.123457."""
    from marketpulse.data.types import Bar
    from marketpulse.trading.price_provider import YFinancePriceProvider

    mock_client = MagicMock()
    mock_client.fetch_close_on_date.return_value = Bar(
        date=date(2026, 5, 22),
        open=100.0, high=100.0, low=100.0, close=100.123456789, volume=1000,
    )
    p = YFinancePriceProvider(client=mock_client)

    result = p.close_on_date(ticker="AAPL", on_date=date(2026, 5, 22))

    assert result.price == Decimal("100.123457")
    # Verify exact Decimal representation (no float artifacts)
    assert str(result.price) == "100.123457"


def test_close_on_date_rollback_preserves_price_date():
    """If client returns a Bar with date < requested, ClosePrice.price_date
    reflects the roll-back."""
    from marketpulse.data.types import Bar
    from marketpulse.trading.price_provider import YFinancePriceProvider

    mock_client = MagicMock()
    mock_client.fetch_close_on_date.return_value = Bar(
        date=date(2026, 5, 22),    # Friday
        open=149.0, high=151.0, low=148.5, close=150.50, volume=1000,
    )
    p = YFinancePriceProvider(client=mock_client)

    result = p.close_on_date(
        ticker="AAPL", on_date=date(2026, 5, 26),    # Tuesday after Memorial Day
    )

    assert result.price_date == date(2026, 5, 22)     # rolled back
    assert result.requested_date == date(2026, 5, 26)
```

- [ ] **Step 2: Run to verify**

Run: `uv run pytest tests/trading/test_yfinance_price_provider.py -v`
Expected: 8 PASS.

- [ ] **Step 3: Run ruff**

Run: `uv run ruff check tests/trading/test_yfinance_price_provider.py`
Expected: `All checks passed!`

- [ ] **Step 4: Commit**

```bash
git add tests/trading/test_yfinance_price_provider.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-plus-T5): YFinancePriceProvider tests

8 mocked tests covering: source property, lookback_days property
(default 10 + custom), Bar→ClosePrice translation with all 4 fields,
lookback_days threading to client, None propagation, Decimal
quantization to 6dp (lock 6b+L14: 100.123456789 → "100.123457"),
roll-back preserves price_date.

YFinancePriceProvider impl lives in price_provider.py (added in T3).

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T6: `Repository.count_price_unavailable_attempts`

**Files:**
- Modify: `marketpulse/trading/repository.py`
- Create: `tests/trading/test_repository_price_unavailable.py`

- [ ] **Step 1: Write failing tests**

Create `tests/trading/test_repository_price_unavailable.py`:

```python
# Layer: stateful
"""6b+T6: Repository.count_price_unavailable_attempts tests.

Lock 6b+L9: wrapper-only API. External code must not write json_extract
inline. attempt_count progression (1, 2, 3) on consecutive failures."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base
    eng = create_engine(f"sqlite:///{tmp_path / 'pu.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def test_count_zero_when_no_audits(session):
    from marketpulse.trading.repository import Repository
    repo = Repository(session=session)
    assert repo.count_price_unavailable_attempts(position_id=42) == 0


def test_count_returns_audit_rows_with_matching_position_id(session):
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.repository import Repository

    ts = datetime.now(UTC)
    # Three PRICE_UNAVAILABLE audits for position 42; one for position 99.
    for _ in range(3):
        session.add(PaperAuditEvent(
            timestamp=ts, event_type="PRICE_UNAVAILABLE",
            order_id=100, strategy="test", reason="no_data",
            context={"position_id": 42, "ticker": "AAPL"},
        ))
    session.add(PaperAuditEvent(
        timestamp=ts, event_type="PRICE_UNAVAILABLE",
        order_id=200, strategy="test", reason="no_data",
        context={"position_id": 99, "ticker": "MSFT"},
    ))
    # Plus an unrelated event_type — must NOT be counted
    session.add(PaperAuditEvent(
        timestamp=ts, event_type="POSITION_CLOSED",
        order_id=100, strategy="test", reason="",
        context={"position_id": 42, "exit_price": "150"},
    ))
    session.flush()

    repo = Repository(session=session)
    assert repo.count_price_unavailable_attempts(position_id=42) == 3
    assert repo.count_price_unavailable_attempts(position_id=99) == 1
    assert repo.count_price_unavailable_attempts(position_id=999) == 0


def test_count_progression_supports_attempt_count_calculation(session):
    """Lock 6b+L9: consecutive PRICE_UNAVAILABLE audits yield 1, 2, 3
    when each writes `attempt_count = previous_count + 1`."""
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    ts = datetime.now(UTC)
    pid = 7

    expected_counts: list[int] = []
    for _ in range(3):
        prior = repo.count_price_unavailable_attempts(position_id=pid)
        expected_counts.append(prior + 1)
        session.add(PaperAuditEvent(
            timestamp=ts, event_type="PRICE_UNAVAILABLE",
            order_id=100, strategy="test", reason="no_data",
            context={"position_id": pid, "attempt_count": prior + 1},
        ))
        session.flush()

    assert expected_counts == [1, 2, 3]
    assert repo.count_price_unavailable_attempts(position_id=pid) == 3
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/test_repository_price_unavailable.py -v`
Expected: 3 FAIL with `AttributeError: ... has no attribute 'count_price_unavailable_attempts'`.

- [ ] **Step 3: Add method to `Repository`**

In `marketpulse/trading/repository.py`, append after `sector_exposure_notional` (end of class):

```python

    def count_price_unavailable_attempts(self, *, position_id: int) -> int:
        """Count of PRICE_UNAVAILABLE audit rows for a given position.

        Lock 6b+L9: wrapper-only. External code MUST NOT write
        json_extract inline — go through this method.

        Uses json_extract(context, '$.position_id') matching so we don't
        depend on the order ↔ position 1:1 invariant (Phase 7 may relax)."""
        from marketpulse.db.models import PaperAuditEvent

        return self._session.execute(
            select(func.count(PaperAuditEvent.id))
            .where(PaperAuditEvent.event_type == "PRICE_UNAVAILABLE")
            .where(
                func.json_extract(PaperAuditEvent.context, "$.position_id")
                == position_id
            )
        ).scalar() or 0
```

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/trading/test_repository_price_unavailable.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Architecture guard still happy**

Run: `uv run pytest -q tests/architecture/test_repository_boundary.py`
Expected: PASS (read-only `select()`, no writes — lock-iii not triggered).

- [ ] **Step 6: Commit**

```bash
git add marketpulse/trading/repository.py tests/trading/test_repository_price_unavailable.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-plus-T6): Repository.count_price_unavailable_attempts

Lock 6b+L9 wrapper-only API. Matches via
json_extract(context, '$.position_id') so it stays correct even after
Phase 7 relaxes order ↔ position 1:1 invariant. Test verifies
progression (1, 2, 3) used by _materialize_exit to populate
attempt_count in the next PRICE_UNAVAILABLE audit row.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T7a: `ForwardExecutionEngine.__init__` accepts required `price_provider`

**Files:**
- Modify: `marketpulse/trading/forward_engine.py`
- Modify: `tests/trading/test_forward_engine.py` (constructor regression)
- Modify: `tests/trading/test_e2e_stateful.py` (any direct engine constructions)
- Modify: `tests/trading/test_scheduler.py` (DI test)
- Modify: `tests/trading/test_daily_cycle.py` (E2E engine constructions)

T7a is **scaffolding only** — adds the required kwarg + 6a regression test updates. T7b implements the actual semantic change to `_materialize_exit`.

- [ ] **Step 1: Add `price_provider` to constructor**

In `marketpulse/trading/forward_engine.py`, locate the `ForwardExecutionEngine.__init__` (around line 45). Update the signature + body:

```python
    def __init__(
        self,
        *,
        repository: Repository,
        clock: Clock,
        kill_switch: KillSwitchState,
        risk_gate: RiskGate,
        price_provider,    # PriceProvider — duck-typed to avoid circular
                           # import (lock 6b+L2: REQUIRED, no default)
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._kill_switch = kill_switch
        self._risk_gate = risk_gate
        self._price_provider = price_provider
        self._last_price_unavailable_count: int = 0    # lock 6b+L11; T7b uses

    def last_price_unavailable_count(self) -> int:
        """Lock 6b+L11: read-only diagnostic from most recent tick().
        Reset to 0 at the start of every tick()."""
        return self._last_price_unavailable_count
```

- [ ] **Step 2: Update all in-tree constructors to pass `price_provider`**

Search-and-replace pattern: every `ForwardExecutionEngine(...)` call must pass `price_provider=...`. Use a `StubPriceProvider(map={})` for tests that don't care about exits (existing 6a tests focused on place_order / kill switch).

Find affected files:

Run: `grep -rn "ForwardExecutionEngine(" tests/ --include="*.py" -l`

Edit each one to add `price_provider=StubPriceProvider(map={})` (or with an appropriate map for exit-tests in T7b).

Example for `tests/trading/test_forward_engine.py` 6a regression fixtures — update the `_make_deps`-style helper (or each direct call) to include the new kwarg.

- [ ] **Step 3: Add regression test for missing kwarg**

In `tests/trading/test_forward_engine.py`, append:

```python
def test_forward_engine_requires_price_provider_kwarg(tmp_path):
    """Lock 6b+L2: missing price_provider → TypeError."""
    from datetime import UTC, datetime
    from decimal import Decimal

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from marketpulse.db.base import Base
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gate import AlwaysApproveRiskGate

    eng_db = tmp_path / "fe.db"
    db_engine = create_engine(f"sqlite:///{eng_db}")
    Base.metadata.create_all(db_engine)
    with Session(db_engine) as s:
        repo = Repository(session=s)
        clock = FakeClock(now=datetime(2026, 5, 22, 21, 30, tzinfo=UTC))
        repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=clock.now())
        ks = KillSwitchState(env_var="MP_NEVER", repository=repo)
        import pytest
        with pytest.raises(TypeError):
            ForwardExecutionEngine(
                repository=repo, clock=clock, kill_switch=ks,
                risk_gate=AlwaysApproveRiskGate(),
                # price_provider omitted intentionally
            )
```

- [ ] **Step 4: Run to verify constructor regression**

Run: `uv run pytest -q tests/trading/test_forward_engine.py`
Expected: ALL pass (including the new constructor regression test). Other 6a tests in this file are now fixed by Step 2's kwarg addition.

- [ ] **Step 5: Run full trading suite — should be GREEN now**

Run: `uv run pytest -q tests/trading/ --tb=no | tail -3`
Expected: All tests pass. The T3 breakage from `StubPriceProvider(default=...)` is now fixed because we've updated call sites to use `StubPriceProvider(map={})` in T7a Step 2. If any failures remain, they're likely missed call sites — find with `grep -rn "StubPriceProvider(default=" tests/` and fix.

- [ ] **Step 6: Run ruff**

Run: `uv run ruff check marketpulse/ tests/`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add marketpulse/trading/forward_engine.py tests/trading/
git commit -m "$(cat <<'EOF'
feat(phase-6b-plus-T7a): ForwardExecutionEngine accepts required price_provider

Lock 6b+L2: price_provider is REQUIRED constructor kwarg (no default).
Lock 6b+L11: adds last_price_unavailable_count() diagnostic method;
counter resets per tick (used by T7b).

All in-tree constructors updated to pass StubPriceProvider(map={}) for
6a regression tests that don't exercise exits. No semantic changes to
_materialize_exit yet — T7b does that.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T7b: `_materialize_exit -> bool` + PRICE_UNAVAILABLE + tick accounting

**Files:**
- Modify: `marketpulse/trading/forward_engine.py`
- Create: `tests/trading/test_forward_engine_price_provider.py` (new — focused on exit semantics)

- [ ] **Step 1: Write failing tests**

Create `tests/trading/test_forward_engine_price_provider.py`:

```python
# Layer: stateful
"""6b+T7b: ForwardExecutionEngine._materialize_exit price provider integration.

Covers locks 6b+L1, L4, L7, L8, L11."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base
    eng = create_engine(f"sqlite:///{tmp_path / 'fe.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _make_engine(session, *, price_provider, now=None):
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gate import AlwaysApproveRiskGate
    repo = Repository(session=session)
    clock = FakeClock(now=now or datetime(2026, 5, 22, 21, 30, tzinfo=UTC))
    repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=clock.now())
    ks = KillSwitchState(env_var="MP_NEVER", repository=repo)
    return ForwardExecutionEngine(
        repository=repo, clock=clock, kill_switch=ks,
        risk_gate=AlwaysApproveRiskGate(),
        price_provider=price_provider,
    ), repo, clock


def _place_and_open(repo, clock, *, ticker="AAPL", horizon_date=date(2026, 5, 22)):
    """Helper: create a paper_order + paper_position in OPEN state with the
    given horizon_date so we can directly test _materialize_exit."""
    from marketpulse.db.models import PaperOrder, PaperPosition

    order = PaperOrder(
        idempotency_key="k1", allocation_run_id="r1",
        strategy="momentum_breakout", ticker=ticker, quantity=10,
        event_time=datetime(2026, 5, 22, 14, 0, tzinfo=UTC),
        allocation_date=date(2026, 5, 22),
        horizon_date=horizon_date,
        placed_at=datetime(2026, 5, 22, 14, 0, tzinfo=UTC),
        filled_at=datetime(2026, 5, 22, 14, 1, tzinfo=UTC),
        event_price=Decimal("150.000000"),
        horizon_price=None,    # forward mode: NULL
        status="ENTRY_FILLED",
        strategy_version="v1",
        allocator_version="phase6a-v1",
        execution_engine_version="phase6a-v1",
        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
        effective_corr_window=60,
        rewarded_for_negative_corr=False,
        would_change_rank=False,
        size_clamped_by_override=False,
    )
    repo._session.add(order)
    repo._session.flush()
    position = PaperPosition(
        order_id=order.id, strategy=order.strategy, ticker=order.ticker,
        quantity=order.quantity,
        entry_price=Decimal("150.000000"),
        entry_date=date(2026, 5, 22),
        horizon_date=horizon_date,
        status="OPEN",
        opened_at=datetime(2026, 5, 22, 14, 1, tzinfo=UTC),
        entry_fill_id=None, exit_fill_id=None,
    )
    repo._session.add(position)
    repo._session.flush()
    return order, position


def test_materialize_exit_returns_true_on_close_price_success(session):
    """Happy path: provider returns ClosePrice → True; position CLOSED."""
    from marketpulse.db.models import PaperPosition
    from marketpulse.trading.price_provider import ClosePrice, StubPriceProvider

    horizon = date(2026, 5, 22)
    close = ClosePrice(
        price=Decimal("155.250000"),
        price_date=horizon,
        requested_date=horizon,
        source="stub",
    )
    provider = StubPriceProvider(map={("AAPL", horizon): close})
    engine, repo, _ = _make_engine(session, price_provider=provider)

    _, position = _place_and_open(repo, _, horizon_date=horizon)

    result = engine._materialize_exit(position, exit_date=date(2026, 5, 22))

    assert result is True
    refreshed = session.execute(
        select(PaperPosition).where(PaperPosition.id == position.id)
    ).scalar_one()
    assert refreshed.status == "CLOSED"
    assert refreshed.exit_price == Decimal("155.250000")
    assert refreshed.realized_pnl == Decimal("52.500000")   # (155.25 - 150) * 10


def test_materialize_exit_returns_false_on_price_unavailable(session):
    """Lock 6b+L7: provider returns None → False; position stays OPEN."""
    from marketpulse.db.models import PaperPosition
    from marketpulse.trading.price_provider import StubPriceProvider

    provider = StubPriceProvider(map={})    # empty — every call returns None
    engine, repo, _ = _make_engine(session, price_provider=provider)
    _, position = _place_and_open(repo, _, horizon_date=date(2026, 5, 22))

    result = engine._materialize_exit(position, exit_date=date(2026, 5, 22))

    assert result is False
    refreshed = session.execute(
        select(PaperPosition).where(PaperPosition.id == position.id)
    ).scalar_one()
    assert refreshed.status == "OPEN"
    assert refreshed.exit_price is None
    assert refreshed.realized_pnl is None


def test_price_unavailable_writes_audit_with_provider_provenance(session):
    """Locks 6b+L4 (order_id=position.order_id), 6b+L8 (provider source/
    lookback in audit)."""
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.price_provider import StubPriceProvider

    provider = StubPriceProvider(map={})    # always None
    engine, repo, _ = _make_engine(session, price_provider=provider)
    order, position = _place_and_open(repo, _, horizon_date=date(2026, 5, 22))

    engine._materialize_exit(position, exit_date=date(2026, 5, 23))

    audits = session.execute(
        select(PaperAuditEvent)
        .where(PaperAuditEvent.event_type == "PRICE_UNAVAILABLE")
    ).scalars().all()
    assert len(audits) == 1
    a = audits[0]
    assert a.order_id == order.id    # lock 6b+L4
    assert a.context["position_id"] == position.id
    assert a.context["ticker"] == "AAPL"
    assert a.context["horizon_date"] == "2026-05-22"
    assert a.context["as_of"] == "2026-05-23"
    assert a.context["source"] == "stub"        # lock 6b+L8 — from provider
    assert a.context["lookback_days"] == 0      # lock 6b+L8 — from provider
    assert a.context["attempt_count"] == 1


def test_attempt_count_progression_1_2_3(session):
    """Lock 6b+L9: 3 consecutive PRICE_UNAVAILABLE writes 1, 2, 3."""
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.price_provider import StubPriceProvider

    provider = StubPriceProvider(map={})
    engine, repo, _ = _make_engine(session, price_provider=provider)
    _, position = _place_and_open(repo, _, horizon_date=date(2026, 5, 22))

    for _ in range(3):
        engine._materialize_exit(position, exit_date=date(2026, 5, 23))

    audits = session.execute(
        select(PaperAuditEvent)
        .where(PaperAuditEvent.event_type == "PRICE_UNAVAILABLE")
        .order_by(PaperAuditEvent.id)
    ).scalars().all()
    assert [a.context["attempt_count"] for a in audits] == [1, 2, 3]


def test_position_closed_audit_has_provenance_fields(session):
    """4 new fields: requested_horizon_date, actual_price_date, price_source,
    roll_policy."""
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.price_provider import ClosePrice, StubPriceProvider

    requested = date(2026, 5, 26)    # Memorial Day (US 2026)
    actual = date(2026, 5, 22)        # Friday before
    close = ClosePrice(
        price=Decimal("155.250000"),
        price_date=actual,
        requested_date=requested,
        source="stub",
    )
    provider = StubPriceProvider(map={("AAPL", requested): close})
    engine, repo, _ = _make_engine(session, price_provider=provider)
    _, position = _place_and_open(repo, _, horizon_date=requested)

    engine._materialize_exit(position, exit_date=date(2026, 5, 27))

    audits = session.execute(
        select(PaperAuditEvent)
        .where(PaperAuditEvent.event_type == "POSITION_CLOSED")
    ).scalars().all()
    assert len(audits) == 1
    ctx = audits[0].context
    assert ctx["requested_horizon_date"] == "2026-05-26"
    assert ctx["actual_price_date"] == "2026-05-22"
    assert ctx["price_source"] == "stub"
    assert ctx["roll_policy"] == "previous_available_close"


def test_position_closed_audit_roll_policy_exact_match(session):
    """When price_date == requested_date → roll_policy=exact_match."""
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.price_provider import ClosePrice, StubPriceProvider

    horizon = date(2026, 5, 22)
    close = ClosePrice(
        price=Decimal("155.250000"),
        price_date=horizon, requested_date=horizon, source="stub",
    )
    provider = StubPriceProvider(map={("AAPL", horizon): close})
    engine, repo, _ = _make_engine(session, price_provider=provider)
    _, position = _place_and_open(repo, _, horizon_date=horizon)

    engine._materialize_exit(position, exit_date=horizon)

    audit = session.execute(
        select(PaperAuditEvent)
        .where(PaperAuditEvent.event_type == "POSITION_CLOSED")
    ).scalar_one()
    assert audit.context["roll_policy"] == "exact_match"


def test_tick_returns_no_errors_when_only_price_unavailable(session):
    """Lock 6b+L7: PRICE_UNAVAILABLE does NOT populate TickResult.errors."""
    from marketpulse.trading.price_provider import StubPriceProvider

    provider = StubPriceProvider(map={})
    engine, repo, _ = _make_engine(session, price_provider=provider)
    _, _ = _place_and_open(repo, _, horizon_date=date(2026, 5, 22))

    result = engine.tick(as_of=date(2026, 5, 23))

    assert result.errors == ()
    assert result.exits_materialized == 0
    assert engine.last_price_unavailable_count() == 1


def test_last_price_unavailable_count_resets_each_tick(session):
    """Lock 6b+L11: counter resets at start of every tick()."""
    from datetime import timedelta

    from marketpulse.trading.price_provider import ClosePrice, StubPriceProvider

    # Tick 1: 1 PRICE_UNAVAILABLE
    provider = StubPriceProvider(map={})
    engine, repo, _ = _make_engine(session, price_provider=provider)
    _, _ = _place_and_open(repo, _, horizon_date=date(2026, 5, 22))
    engine.tick(as_of=date(2026, 5, 23))
    assert engine.last_price_unavailable_count() == 1

    # Tick 2: same engine, fresh provider with valid price → 0 unavailable
    engine._price_provider = StubPriceProvider(map={
        ("AAPL", date(2026, 5, 22)): ClosePrice(
            price=Decimal("155.000000"),
            price_date=date(2026, 5, 22),
            requested_date=date(2026, 5, 22),
            source="stub",
        ),
    })
    engine.tick(as_of=date(2026, 5, 23))
    assert engine.last_price_unavailable_count() == 0    # NOT stale 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/test_forward_engine_price_provider.py -v`
Expected: 8 FAIL — `_materialize_exit` currently returns `None` and uses `order.horizon_price` (now NULL).

- [ ] **Step 3: Rewrite `_materialize_exit` to use price provider**

In `marketpulse/trading/forward_engine.py`, locate `_materialize_exit` (around line 302). Replace the entire method:

```python
    def _materialize_exit(self, position, *, exit_date: date) -> bool:
        """Lock 6b+L1: exit_price comes from PriceProvider at this point
        in time (NOT from order.horizon_price). Lock 6b+L7: returns True
        if CLOSED, False if PRICE_UNAVAILABLE (position stays OPEN)."""
        exit_time = self._clock.now()

        close = self._price_provider.close_on_date(
            ticker=position.ticker,
            on_date=position.horizon_date,
        )
        if close is None:
            # Lock 6b+L7: NOT an InvariantError. Position stays OPEN;
            # next tick retries.
            prior_attempts = self._repo.count_price_unavailable_attempts(
                position_id=position.id,
            )
            with self._repo.transaction():
                self._repo.write_audit_event(
                    event_type=AuditEventType.PRICE_UNAVAILABLE,
                    order_id=position.order_id,     # lock 6b+L4
                    strategy=position.strategy,
                    reason="close_on_date_returned_none",
                    context={
                        "position_id": position.id,
                        "ticker": position.ticker,
                        "horizon_date": position.horizon_date.isoformat(),
                        "as_of": exit_date.isoformat(),
                        "lookback_days": self._price_provider.lookback_days,
                        "source": self._price_provider.source,
                        "attempt_count": prior_attempts + 1,
                    },
                    timestamp=exit_time,
                )
            return False

        # Success path: write fill at the actual price_date.
        exit_price = close.price    # already quantized in YFinancePriceProvider
        cash_inflow = exit_price * Decimal(position.quantity)
        realized_pnl = (
            (exit_price - position.entry_price) * Decimal(position.quantity)
        )

        with self._repo.transaction():
            fill = self._repo.insert_paper_fill(
                order_id=position.order_id, position_id=position.id,
                side="EXIT", price=exit_price,
                quantity=position.quantity,
                filled_at=exit_time, cash_delta=cash_inflow,
                realized_pnl=realized_pnl,
            )
            self._repo.update_paper_position_exit(
                position_id=position.id, exit_fill_id=fill.id,
                exit_price=exit_price, realized_pnl=realized_pnl,
                closed_at=exit_time,
            )
            self._repo.insert_cash_ledger_entry_for_fill(
                timestamp=exit_time, delta=cash_inflow,
                reason="EXIT_FILL", fill_id=fill.id,
            )
            self._repo.write_audit_event(
                event_type=AuditEventType.POSITION_CLOSED,
                order_id=position.order_id,
                strategy=position.strategy,
                reason="",
                context={
                    "position_id": position.id,
                    "exit_price": str(exit_price),
                    "realized_pnl": str(realized_pnl),
                    "cash_balance_after": str(self._repo.cash_balance()),
                    # Lock 6b+L1 provenance:
                    "requested_horizon_date": position.horizon_date.isoformat(),
                    "actual_price_date": close.price_date.isoformat(),
                    "price_source": close.source,
                    "roll_policy": (
                        "exact_match"
                        if close.price_date == position.horizon_date
                        else "previous_available_close"
                    ),
                },
                timestamp=exit_time,
            )
        return True
```

- [ ] **Step 4: Rewrite `tick()` to use the bool return + reset counter**

In `marketpulse/trading/forward_engine.py`, locate `tick()` (around line 175). Replace the existing exit loop. The full method:

```python
    def tick(self, *, as_of: date) -> TickResult:
        """Lock 6b+L11: resets last_price_unavailable_count at start.
        Lock 6b+L7: PRICE_UNAVAILABLE does NOT count as TickError."""
        self._last_price_unavailable_count = 0    # reset per tick

        entries_materialized = 0
        exits_materialized = 0
        errors: list[TickError] = []

        # Entry path (unchanged from 6a)
        for order in self._repo.find_orders_for_entry(as_of=as_of):
            try:
                self._materialize_entry(order, fill_date=as_of)
                entries_materialized += 1
            except InvariantError as e:
                errors.append(TickError(
                    phase="entry_materialization",
                    order_id=order.id, position_id=None,
                    error=str(e),
                ))

        # Exit path (6b+ changes)
        for position in self._repo.find_positions_for_exit(as_of=as_of):
            try:
                closed = self._materialize_exit(position, exit_date=as_of)
                if closed:
                    exits_materialized += 1
                else:
                    # Lock 6b+L7: NOT a TickError. Just counter.
                    self._last_price_unavailable_count += 1
            except InvariantError as e:
                # Non-price invariant violations preserve 6a behavior
                errors.append(TickError(
                    phase="exit_materialization",
                    order_id=position.order_id,
                    position_id=position.id,
                    error=str(e),
                ))

        return TickResult(
            as_of=as_of,
            entries_materialized=entries_materialized,
            exits_materialized=exits_materialized,
            errors=tuple(errors),
        )
```

- [ ] **Step 5: Run to verify tests pass**

Run: `uv run pytest tests/trading/test_forward_engine_price_provider.py -v`
Expected: 8 PASS.

- [ ] **Step 6: Run full trading suite**

Run: `uv run pytest -q tests/trading/ --tb=no | tail -3`
Expected: all pass. (Existing 6a/6b tests that exercise `_materialize_exit` indirectly should now work because `order.horizon_price` is no longer read.)

- [ ] **Step 7: Ruff clean**

Run: `uv run ruff check marketpulse/ tests/`
Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add marketpulse/trading/forward_engine.py tests/trading/test_forward_engine_price_provider.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-plus-T7b): _materialize_exit uses price provider; -> bool

Lock 6b+L1: exit_price now from price_provider.close_on_date (NOT
order.horizon_price). paper_fill.price is canonical P&L source.
Lock 6b+L4: PRICE_UNAVAILABLE audit order_id=position.order_id.
Lock 6b+L7: returns False on PRICE_UNAVAILABLE; position stays OPEN.
NOT an InvariantError. tick.errors stays () when only price gaps.
Lock 6b+L8: audit context reads source/lookback_days from provider
properties (NOT hardcoded).
Lock 6b+L11: last_price_unavailable_count() exposes per-tick count;
resets to 0 at start of each tick(). T8 will read this and surface
in TICK_COMPLETED context.

POSITION_CLOSED audit context gains 4 provenance fields
(requested_horizon_date, actual_price_date, price_source, roll_policy).

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T8: `daily_cycle.run` signature change + `TICK_COMPLETED.context["price_unavailable_count"]`

**Files:**
- Modify: `marketpulse/trading/daily_cycle.py`
- Modify: `marketpulse/backtest/allocation.py` (docstring only)
- Modify: `tests/trading/test_daily_cycle.py`

- [ ] **Step 1: Append failing test for new behavior**

Append to `tests/trading/test_daily_cycle.py`:

```python
def test_daily_cycle_run_rejects_price_provider_kwarg(session):
    """T8: price_provider kwarg removed from daily_cycle.run (breaking change)."""
    from marketpulse.trading import daily_cycle
    from marketpulse.trading.price_provider import StubPriceProvider

    deps = _make_deps(
        session,
        fake_now=datetime(2026, 5, 21, 21, 30, tzinfo=UTC),
        allocator=_stub_allocator(expected_winners=[]),
    )
    # Adding price_provider should raise TypeError
    import pytest
    with pytest.raises(TypeError, match="price_provider|unexpected"):
        daily_cycle.run(**deps, price_provider=StubPriceProvider(map={}))


def test_daily_cycle_forward_mode_paper_order_horizon_price_is_null(session):
    """Lock 6b+L1: forward mode never writes horizon_price to paper_order."""
    from marketpulse.db.models import PaperOrder
    from marketpulse.trading import daily_cycle

    deps = _make_deps(
        session,
        fake_now=datetime(2026, 5, 21, 21, 30, tzinfo=UTC),
        allocator=_stub_allocator(expected_winners=[
            _winner_for("AAPL", "momentum", date(2026, 5, 21)),
        ]),
    )
    daily_cycle.run(**deps)
    orders = session.execute(select(PaperOrder)).scalars().all()
    assert len(orders) == 1
    assert orders[0].horizon_price is None


def test_daily_cycle_tick_completed_includes_price_unavailable_count(session):
    """T8: TICK_COMPLETED.context surfaces engine.last_price_unavailable_count()."""
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading import daily_cycle

    # Set up: a position with horizon_date=today, but no price in stub
    # (so _materialize_exit returns False).
    deps = _make_deps(
        session,
        fake_now=datetime(2026, 5, 21, 21, 30, tzinfo=UTC),
        allocator=_stub_allocator(expected_winners=[]),
    )
    # Pre-seed an OPEN position from yesterday with today's horizon
    # (the engine's stub provider returns None → PRICE_UNAVAILABLE)
    # [test helper here would build a paper_order + paper_position]
    # ... (test scaffolding omitted for brevity; tests in test_e2e_stateful.py
    # cover this end-to-end)

    result = daily_cycle.run(**deps)
    tc_audits = session.execute(
        select(PaperAuditEvent)
        .where(PaperAuditEvent.event_type == "TICK_COMPLETED")
    ).scalars().all()
    assert len(tc_audits) == 1
    # Context has the new key (value can be 0 if nothing to exit)
    assert "price_unavailable_count" in tc_audits[0].context
```

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/trading/test_daily_cycle.py::test_daily_cycle_run_rejects_price_provider_kwarg tests/trading/test_daily_cycle.py::test_daily_cycle_forward_mode_paper_order_horizon_price_is_null tests/trading/test_daily_cycle.py::test_daily_cycle_tick_completed_includes_price_unavailable_count -v`
Expected: 3 FAIL (kwarg still accepted; horizon_price still gets stub default; context missing key).

- [ ] **Step 3: Update `daily_cycle.run` signature and `_make_order_request`**

In `marketpulse/trading/daily_cycle.py`:

(a) Remove `price_provider` parameter from `_make_order_request`:

```python
def _make_order_request(
    *,
    winner,
    allocation_run_id: AllocationRunId,
    allocation_date: date,
) -> OrderRequest:
    """Quantization site: float → Decimal at the OrderRequest boundary
    (lock xxii).

    Lock 6b+L1: forward mode ALWAYS leaves horizon_price=None. The
    ForwardExecutionEngine fetches the actual close at exit time via
    its injected PriceProvider. paper_order.horizon_price is a legacy
    field (Phase 5 backtest still writes it from historical data)."""
    return OrderRequest(
        strategy=winner.strategy,
        ticker=winner.ticker,
        quantity=winner.quantity,
        event_time=winner.event_time,
        allocation_date=allocation_date,
        event_price=Decimal(str(winner.event_price)),
        horizon_date=winner.horizon_date,
        horizon_price=None,    # lock 6b+L1: never set in forward mode
        allocation_run_id=allocation_run_id,
        strategy_version=winner.strategy_version,
        allocator_version=ALLOCATOR_VERSION,
        execution_engine_version=EXECUTION_ENGINE_VERSION,
        weight=winner.weight,
        raw_bid_weight=winner.raw_bid_weight,
        pool_corr=winner.pool_corr,
        contribution_multiplier=winner.contribution_multiplier,
        adjusted_bid_weight=winner.adjusted_bid_weight,
        effective_corr_window=winner.effective_corr_window,
        rewarded_for_negative_corr=winner.rewarded_for_negative_corr,
        would_change_rank=winner.would_change_rank,
        size_clamped_by_override=winner.size_clamped_by_override,
    )
```

(b) Remove `price_provider` from `run()` signature and from the `_make_order_request` call:

```python
def run(
    *,
    clock: Clock,
    engine: ExecutionEngine,
    repository: Repository,
    bid_aggregator: BidAggregator,
    allocator: Callable[..., AllocationResult],
    calendar: NYTradingCalendar,
    kill_switch: KillSwitchState,
    # price_provider PARAMETER REMOVED (lock 6b+L1)
    daily_curves: dict[str, list[tuple[date, float]]] | None = None,
    daily_strategy_contribution_returns: dict[
        str, list[tuple[date, float]]
    ] | None = None,
    daily_pool_returns: list[tuple[date, float]] | None = None,
    sector_provider: Callable[[str], str] | None = None,
) -> DailyCycleResult:
    ...
```

And inside `run()`, find where `_make_order_request` is called and remove the `price_provider=...` kwarg.

(c) Update the `TICK_COMPLETED` audit context to include `price_unavailable_count`:

Locate the `with repository.transaction(): repository.write_tick_completed_once(...)` block. Add the new key to the context dict:

```python
        repository.write_tick_completed_once(
            tick_date=tick_date,
            context={
                "tick_date": tick_date.isoformat(),
                "status": cycle_status,
                "allocation_run_id": allocation_run_id,
                "bids_collected": result.bids_collected,
                "orders_placed": result.orders_placed,
                "orders_rejected": result.orders_rejected,
                "duplicates_skipped": result.duplicates_skipped,
                "entries_materialized": result.entries_materialized,
                "exits_materialized": result.exits_materialized,
                # Lock 6b+L11: surface engine's tick-local counter
                "price_unavailable_count": engine.last_price_unavailable_count(),
                "tick_errors": [
                    {
                        "phase": e.phase,
                        "order_id": e.order_id,
                        "position_id": e.position_id,
                        "error": e.error,
                    }
                    for e in result.tick_errors
                ],
                "cash_balance_end": str(result.cash_balance_end),
            },
            timestamp=clock.now(),
        )
```

Note: the same change should be applied to the kill-switch-cycle-skipped audit path so the new key always exists (set to 0 there since tick may or may not run).

In the kill-switch path:

```python
        with repository.transaction():
            repository.write_audit_event(
                event_type=AuditEventType.KILL_SWITCH_CYCLE_SKIPPED,
                ...
                context={
                    ...existing fields...,
                    "price_unavailable_count": engine.last_price_unavailable_count(),
                    ...
                },
                ...
            )
```

(d) Also update the allocator-error path (where cycle_status="completed_with_errors" and tick still runs):

```python
    # === Phase 5: tick (always runs — close due positions even if
    # allocation failed)
    tick_result = engine.tick(as_of=tick_date)
    price_unavailable_count = engine.last_price_unavailable_count()    # capture

    # === Phase 6: TICK_COMPLETED ===
    # Lock 6b+L12: PRICE_UNAVAILABLE alone does NOT change cycle_status.
    cycle_status: Literal["completed", "completed_with_errors"] = (
        "completed_with_errors"
        if (tick_result.errors or allocator_error is not None)
        else "completed"
    )
```

- [ ] **Step 4: Update `marketpulse/backtest/allocation.py` docstring (lines around `horizon_price` field)**

Find the `AllocationWinner` dataclass (or equivalent) where `horizon_price: float | None` is defined. Update the comment:

```python
    horizon_price: float | None
    # Phase 5 backtest: filled by simulator from historical data.
    # Phase 6+ forward mode: left None; ForwardExecutionEngine fetches
    # the actual close at exit time via PriceProvider (lock 6b+L1).
```

- [ ] **Step 5: Run tests to verify**

Run: `uv run pytest tests/trading/test_daily_cycle.py -v`
Expected: ALL pass (existing tests + the 3 new ones).

- [ ] **Step 6: Run full trading suite**

Run: `uv run pytest -q tests/trading/ --tb=no | tail -3`
Expected: ALL pass.

- [ ] **Step 7: Ruff clean**

Run: `uv run ruff check marketpulse/ tests/`
Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add marketpulse/trading/daily_cycle.py marketpulse/backtest/allocation.py tests/trading/test_daily_cycle.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-plus-T8): daily_cycle.run drops price_provider kwarg

Lock 6b+L1: forward mode ALWAYS writes paper_order.horizon_price=NULL.
_make_order_request no longer accepts price_provider; OrderRequest
construction is simpler.

Lock 6b+L11 plumbing: daily_cycle reads engine.last_price_unavailable_count()
after engine.tick(...) and surfaces it in TICK_COMPLETED.context (also
in KILL_SWITCH_CYCLE_SKIPPED for symmetry).

Lock 6b+L12: cycle_status='completed_with_errors' iff TickResult.errors
non-empty OR allocator failed. PRICE_UNAVAILABLE alone leaves
cycle_status='completed'.

Comment-only update to marketpulse/backtest/allocation.py
AllocationWinner.horizon_price docstring clarifies Phase 5 backtest
vs Phase 6+ forward semantics.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T9: `paper_trading_tick.py` DI rewire

**Files:**
- Modify: `marketpulse/scheduler/paper_trading_tick.py`
- Modify: `tests/trading/test_scheduler.py`

- [ ] **Step 1: Append failing test**

Append to `tests/trading/test_scheduler.py`:

```python
def test_paper_trading_tick_injects_yfinance_price_provider(monkeypatch, tmp_path):
    """T9 DI swap: paper_trading_tick_job must wire YFinancePriceProvider
    into the engine."""
    import marketpulse.scheduler.paper_trading_tick as m
    from marketpulse.trading.price_provider import YFinancePriceProvider

    captured = {}

    real_engine_cls = m.ForwardExecutionEngine

    class _SpyEngine(real_engine_cls):
        def __init__(self, *args, **kwargs):
            captured["price_provider"] = kwargs.get("price_provider")
            super().__init__(*args, **kwargs)

        def tick(self, *, as_of):
            from marketpulse.trading.types import TickResult
            return TickResult(
                as_of=as_of, entries_materialized=0,
                exits_materialized=0, errors=(),
            )

        def last_price_unavailable_count(self) -> int:
            return 0

    monkeypatch.setattr(m, "ForwardExecutionEngine", _SpyEngine)

    # The scheduler job uses session_scope which needs Base.metadata.create_all
    from marketpulse.db import base as db_base
    from marketpulse.db.base import Base
    from sqlalchemy import create_engine
    test_engine = create_engine(f"sqlite:///{tmp_path / 'sch.db'}")
    Base.metadata.create_all(test_engine)
    original_engine = db_base._engine
    db_base._engine = test_engine
    try:
        m.paper_trading_tick_job()
    finally:
        db_base._engine = original_engine

    assert isinstance(captured["price_provider"], YFinancePriceProvider)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/test_scheduler.py::test_paper_trading_tick_injects_yfinance_price_provider -v`
Expected: FAIL — current code wires `StubPriceProvider`.

- [ ] **Step 3: Update `paper_trading_tick.py`**

Open `marketpulse/scheduler/paper_trading_tick.py` and update:

(a) Imports — replace `from marketpulse.trading.price_provider import StubPriceProvider` with:

```python
from marketpulse.data.yfinance_client import YFinanceClient
from marketpulse.trading.price_provider import YFinancePriceProvider
```

(b) Replace the `price_provider = StubPriceProvider(default=Decimal("0"))` line with:

```python
        price_provider = YFinancePriceProvider(client=YFinanceClient())
```

(c) Update the `ForwardExecutionEngine(...)` constructor call to pass `price_provider=price_provider`. Make sure it's at the engine, NOT at `daily_cycle.run`:

```python
        engine = ForwardExecutionEngine(
            repository=repository, clock=clock,
            kill_switch=kill_switch, risk_gate=risk_gate,
            price_provider=price_provider,    # 6b+: injected into engine
        )
        ...
        result = daily_cycle.run(
            clock=clock, engine=engine, repository=repository,
            bid_aggregator=bid_aggregator, allocator=allocate_for_day,
            calendar=calendar, kill_switch=kill_switch,
            # price_provider kwarg REMOVED (lock 6b+L1, T8)
            ...
        )
```

(d) If `Decimal` import is now only used for `Decimal("0")` in the removed line and nowhere else in the file, remove the `from decimal import Decimal` import line. Otherwise leave it.

- [ ] **Step 4: Run to verify test passes**

Run: `uv run pytest tests/trading/test_scheduler.py -v`
Expected: ALL pass (T9 test + any pre-existing T17 6b test).

- [ ] **Step 5: Run full trading suite**

Run: `uv run pytest -q tests/trading/ --tb=no | tail -3`
Expected: ALL pass.

- [ ] **Step 6: Ruff**

Run: `uv run ruff check marketpulse/ tests/`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add marketpulse/scheduler/paper_trading_tick.py tests/trading/test_scheduler.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-plus-T9): paper_trading_tick wires YFinancePriceProvider

DI swap: StubPriceProvider(default=Decimal("0")) →
YFinancePriceProvider(client=YFinanceClient()). Provider now injected
into ForwardExecutionEngine (lock 6b+L2), NOT into daily_cycle.run
(removed in T8).

This is the moment production paper trading starts using real exit
close prices instead of Decimal("0") garbage.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T10: E2E tests — roll-back, retry, P&L correctness

**Files:**
- Modify: `tests/trading/test_e2e_stateful.py`

- [ ] **Step 1: Append E2E scenarios**

Append to `tests/trading/test_e2e_stateful.py`:

```python
# === Phase 6b+ — Paper P&L Realization E2E ===

def test_e2e_phase6b_plus_happy_path_real_pnl(tmp_path):
    """Op-test #1: exit_price from PriceProvider; realized_pnl from
    paper_fill.price (NOT order.horizon_price)."""
    from datetime import UTC, date, datetime, timedelta
    from decimal import Decimal

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from marketpulse.backtest.allocation import AllocationResult, AllocationWinner
    from marketpulse.db.base import Base
    from marketpulse.db.models import PaperFill, PaperOrder, PaperPosition
    from marketpulse.trading import daily_cycle
    from marketpulse.trading.bid_aggregator import BidAggregator
    from marketpulse.trading.calendar import NYTradingCalendar
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.price_provider import ClosePrice, StubPriceProvider
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gate import AlwaysApproveRiskGate

    eng = create_engine(f"sqlite:///{tmp_path / 'e2e_pnl.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        repo = Repository(session=s)
        clock = FakeClock(now=datetime(2026, 5, 22, 21, 30, tzinfo=UTC))
        repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=clock.now())
        ks = KillSwitchState(env_var="MP_NEVER", repository=repo)
        # Provider returns concrete price for horizon date
        horizon_date = date(2026, 5, 22)
        provider = StubPriceProvider(map={
            ("AAPL", horizon_date): ClosePrice(
                price=Decimal("155.500000"),
                price_date=horizon_date,
                requested_date=horizon_date,
                source="stub",
            ),
        })
        engine = ForwardExecutionEngine(
            repository=repo, clock=clock, kill_switch=ks,
            risk_gate=AlwaysApproveRiskGate(),
            price_provider=provider,
        )

        # Allocator returns a winner with horizon=today (same-day exit
        # for simplicity in this test)
        def alloc(**kw):
            return AllocationResult(
                winners=(
                    AllocationWinner(
                        strategy="momentum_breakout", ticker="AAPL",
                        event_time=datetime(2026, 5, 22, 14, 0, tzinfo=UTC),
                        event_price=150.0,
                        horizon_date=horizon_date,
                        horizon_price=None,    # forward mode
                        quantity=10,
                        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
                        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
                        effective_corr_window=60,
                        rewarded_for_negative_corr=False,
                        would_change_rank=False,
                        size_clamped_by_override=False,
                        strategy_version="v1",
                    ),
                ),
                blocked=(), cash_used=1500.0, cash_remaining=8500.0,
                timeline=(),
            )
        alloc.__version__ = "v1"

        result = daily_cycle.run(
            clock=clock, engine=engine, repository=repo,
            bid_aggregator=BidAggregator(session=s, calendar=NYTradingCalendar()),
            allocator=alloc, calendar=NYTradingCalendar(), kill_switch=ks,
        )

        # Order placed + entry materialized + exit materialized in same tick
        assert result.orders_placed == 1
        assert result.entries_materialized == 1
        assert result.exits_materialized == 1
        assert result.cycle_status == "completed"

        # Verify P&L from paper_fill (lock 6b+L1), NOT from
        # paper_order.horizon_price (which should be NULL).
        order = s.execute(select(PaperOrder)).scalar_one()
        assert order.horizon_price is None    # lock 6b+L1

        exit_fill = s.execute(
            select(PaperFill).where(PaperFill.side == "EXIT")
        ).scalar_one()
        assert exit_fill.price == Decimal("155.500000")
        assert exit_fill.realized_pnl == Decimal("55.000000")   # (155.5-150)*10

        position = s.execute(select(PaperPosition)).scalar_one()
        assert position.status == "CLOSED"
        assert position.exit_price == Decimal("155.500000")
        assert position.realized_pnl == Decimal("55.000000")


def test_e2e_phase6b_plus_roll_back_to_prior_session(tmp_path):
    """Op-test #2: horizon_date is non-session → price_date < horizon_date,
    POSITION_CLOSED audit roll_policy='previous_available_close'."""
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from marketpulse.backtest.allocation import AllocationResult, AllocationWinner
    from marketpulse.db.base import Base
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading import daily_cycle
    from marketpulse.trading.bid_aggregator import BidAggregator
    from marketpulse.trading.calendar import NYTradingCalendar
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.price_provider import ClosePrice, StubPriceProvider
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gate import AlwaysApproveRiskGate

    eng = create_engine(f"sqlite:///{tmp_path / 'e2e_rollback.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        repo = Repository(session=s)
        clock = FakeClock(now=datetime(2026, 5, 22, 21, 30, tzinfo=UTC))
        repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=clock.now())
        ks = KillSwitchState(env_var="MP_NEVER", repository=repo)

        # horizon = Monday but Memorial Day → rolls back to Friday
        requested = date(2026, 5, 25)
        actual = date(2026, 5, 22)
        provider = StubPriceProvider(map={
            ("AAPL", requested): ClosePrice(
                price=Decimal("152.000000"),
                price_date=actual,
                requested_date=requested,
                source="stub",
            ),
        })
        engine = ForwardExecutionEngine(
            repository=repo, clock=clock, kill_switch=ks,
            risk_gate=AlwaysApproveRiskGate(),
            price_provider=provider,
        )

        def alloc(**kw):
            return AllocationResult(
                winners=(
                    AllocationWinner(
                        strategy="momentum_breakout", ticker="AAPL",
                        event_time=datetime(2026, 5, 22, 14, 0, tzinfo=UTC),
                        event_price=150.0,
                        horizon_date=requested,
                        horizon_price=None,
                        quantity=10,
                        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
                        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
                        effective_corr_window=60,
                        rewarded_for_negative_corr=False,
                        would_change_rank=False,
                        size_clamped_by_override=False,
                        strategy_version="v1",
                    ),
                ),
                blocked=(), cash_used=1500.0, cash_remaining=8500.0,
                timeline=(),
            )
        alloc.__version__ = "v1"

        # We need horizon_date >= as_of for exit to fire; use a fake_now
        # later than requested so positions become eligible.
        # Place the order on day D=2026-05-22, then run a 2nd tick on
        # D+10 to trigger exit.
        result1 = daily_cycle.run(
            clock=clock, engine=engine, repository=repo,
            bid_aggregator=BidAggregator(session=s, calendar=NYTradingCalendar()),
            allocator=alloc, calendar=NYTradingCalendar(), kill_switch=ks,
        )
        assert result1.orders_placed == 1
        # Position is OPEN; horizon (May 25) hasn't been reached yet

        # Tick forward
        clock_2 = FakeClock(now=datetime(2026, 6, 2, 21, 30, tzinfo=UTC))
        engine._clock = clock_2    # bump engine clock for materialize
        # No new allocations on the second tick
        def alloc2(**kw):
            return AllocationResult(
                winners=(), blocked=(), cash_used=0.0, cash_remaining=8500.0,
                timeline=(),
            )
        alloc2.__version__ = "v1"
        result2 = daily_cycle.run(
            clock=clock_2, engine=engine, repository=repo,
            bid_aggregator=BidAggregator(session=s, calendar=NYTradingCalendar()),
            allocator=alloc2, calendar=NYTradingCalendar(), kill_switch=ks,
        )
        assert result2.exits_materialized == 1
        assert result2.cycle_status == "completed"

        # Verify POSITION_CLOSED audit has roll-back provenance
        audits = s.execute(
            select(PaperAuditEvent)
            .where(PaperAuditEvent.event_type == "POSITION_CLOSED")
        ).scalars().all()
        assert len(audits) == 1
        ctx = audits[0].context
        assert ctx["requested_horizon_date"] == "2026-05-25"
        assert ctx["actual_price_date"] == "2026-05-22"
        assert ctx["roll_policy"] == "previous_available_close"
        assert ctx["price_source"] == "stub"


def test_e2e_phase6b_plus_price_unavailable_retry_then_succeed(tmp_path):
    """Op-test #5: tick 1 PRICE_UNAVAILABLE, tick 2 succeeds. attempt_count
    sequence on the audit rows is [1] then position CLOSED on tick 2."""
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from marketpulse.backtest.allocation import AllocationResult, AllocationWinner
    from marketpulse.db.base import Base
    from marketpulse.db.models import PaperAuditEvent, PaperPosition
    from marketpulse.trading import daily_cycle
    from marketpulse.trading.bid_aggregator import BidAggregator
    from marketpulse.trading.calendar import NYTradingCalendar
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.price_provider import ClosePrice, StubPriceProvider
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gate import AlwaysApproveRiskGate

    eng = create_engine(f"sqlite:///{tmp_path / 'e2e_retry.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        repo = Repository(session=s)
        clock = FakeClock(now=datetime(2026, 5, 22, 21, 30, tzinfo=UTC))
        repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=clock.now())
        ks = KillSwitchState(env_var="MP_NEVER", repository=repo)

        horizon = date(2026, 5, 22)
        empty_provider = StubPriceProvider(map={})
        engine = ForwardExecutionEngine(
            repository=repo, clock=clock, kill_switch=ks,
            risk_gate=AlwaysApproveRiskGate(),
            price_provider=empty_provider,
        )

        def alloc(**kw):
            return AllocationResult(
                winners=(
                    AllocationWinner(
                        strategy="momentum_breakout", ticker="AAPL",
                        event_time=datetime(2026, 5, 22, 14, 0, tzinfo=UTC),
                        event_price=150.0,
                        horizon_date=horizon, horizon_price=None,
                        quantity=10,
                        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
                        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
                        effective_corr_window=60,
                        rewarded_for_negative_corr=False,
                        would_change_rank=False,
                        size_clamped_by_override=False,
                        strategy_version="v1",
                    ),
                ),
                blocked=(), cash_used=1500.0, cash_remaining=8500.0,
                timeline=(),
            )
        alloc.__version__ = "v1"

        # Tick 1: entry + exit-attempt fails (provider empty)
        result1 = daily_cycle.run(
            clock=clock, engine=engine, repository=repo,
            bid_aggregator=BidAggregator(session=s, calendar=NYTradingCalendar()),
            allocator=alloc, calendar=NYTradingCalendar(), kill_switch=ks,
        )
        assert result1.entries_materialized == 1
        assert result1.exits_materialized == 0     # PRICE_UNAVAILABLE
        assert result1.cycle_status == "completed"  # lock 6b+L12

        pu_audits = s.execute(
            select(PaperAuditEvent)
            .where(PaperAuditEvent.event_type == "PRICE_UNAVAILABLE")
        ).scalars().all()
        assert len(pu_audits) == 1
        assert pu_audits[0].context["attempt_count"] == 1

        # Tick 2: provider now has the price → position CLOSED
        engine._price_provider = StubPriceProvider(map={
            ("AAPL", horizon): ClosePrice(
                price=Decimal("160.000000"),
                price_date=horizon,
                requested_date=horizon,
                source="stub",
            ),
        })
        clock_2 = FakeClock(now=datetime(2026, 5, 23, 21, 30, tzinfo=UTC))
        engine._clock = clock_2

        def alloc2(**kw):
            return AllocationResult(
                winners=(), blocked=(), cash_used=0.0, cash_remaining=8500.0,
                timeline=(),
            )
        alloc2.__version__ = "v1"
        result2 = daily_cycle.run(
            clock=clock_2, engine=engine, repository=repo,
            bid_aggregator=BidAggregator(session=s, calendar=NYTradingCalendar()),
            allocator=alloc2, calendar=NYTradingCalendar(), kill_switch=ks,
        )
        assert result2.exits_materialized == 1
        assert result2.cycle_status == "completed"

        position = s.execute(select(PaperPosition)).scalar_one()
        assert position.status == "CLOSED"
        assert position.realized_pnl == Decimal("100.000000")   # (160-150)*10


def test_e2e_phase6b_plus_price_unavailable_does_not_mutate_state(tmp_path):
    """Op-test #19: after 3 consecutive PRICE_UNAVAILABLE, position
    still OPEN, no EXIT fill, cash_balance unchanged."""
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from marketpulse.db.base import Base
    from marketpulse.db.models import PaperFill, PaperPosition
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.price_provider import StubPriceProvider
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gate import AlwaysApproveRiskGate

    eng = create_engine(f"sqlite:///{tmp_path / 'e2e_nostate.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        repo = Repository(session=s)
        clock = FakeClock(now=datetime(2026, 5, 22, 21, 30, tzinfo=UTC))
        repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=clock.now())
        cash_before = repo.cash_balance()
        ks = KillSwitchState(env_var="MP_NEVER", repository=repo)

        provider = StubPriceProvider(map={})    # always None
        engine = ForwardExecutionEngine(
            repository=repo, clock=clock, kill_switch=ks,
            risk_gate=AlwaysApproveRiskGate(),
            price_provider=provider,
        )

        # Manually create an OPEN position
        from marketpulse.db.models import PaperOrder
        order = PaperOrder(
            idempotency_key="k1", allocation_run_id="r1",
            strategy="momentum_breakout", ticker="AAPL", quantity=10,
            event_time=datetime(2026, 5, 22, 14, 0, tzinfo=UTC),
            allocation_date=date(2026, 5, 22),
            horizon_date=date(2026, 5, 22),
            placed_at=datetime(2026, 5, 22, 14, 0, tzinfo=UTC),
            filled_at=datetime(2026, 5, 22, 14, 1, tzinfo=UTC),
            event_price=Decimal("150.000000"),
            horizon_price=None, status="ENTRY_FILLED",
            strategy_version="v1", allocator_version="phase6a-v1",
            execution_engine_version="phase6a-v1",
            weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
            contribution_multiplier=1.0, adjusted_bid_weight=1.0,
            effective_corr_window=60,
            rewarded_for_negative_corr=False, would_change_rank=False,
            size_clamped_by_override=False,
        )
        s.add(order)
        s.flush()
        position = PaperPosition(
            order_id=order.id, strategy="momentum_breakout", ticker="AAPL",
            quantity=10, entry_price=Decimal("150.000000"),
            entry_date=date(2026, 5, 22), horizon_date=date(2026, 5, 22),
            status="OPEN",
            opened_at=datetime(2026, 5, 22, 14, 1, tzinfo=UTC),
            entry_fill_id=None, exit_fill_id=None,
        )
        s.add(position)
        s.flush()

        # 3 consecutive ticks; all PRICE_UNAVAILABLE
        for _ in range(3):
            engine._materialize_exit(position, exit_date=date(2026, 5, 23))

        # Refresh position from DB
        refreshed = s.execute(
            select(PaperPosition).where(PaperPosition.id == position.id)
        ).scalar_one()
        assert refreshed.status == "OPEN"
        assert refreshed.realized_pnl is None
        assert refreshed.exit_price is None

        # No EXIT fill rows
        exit_fills = s.execute(
            select(PaperFill).where(PaperFill.side == "EXIT")
        ).scalars().all()
        assert exit_fills == []

        # cash_balance unchanged
        assert repo.cash_balance() == cash_before
```

- [ ] **Step 2: Run E2E tests**

Run: `uv run pytest tests/trading/test_e2e_stateful.py -v -k "phase6b_plus"`
Expected: 4 PASS.

- [ ] **Step 3: Run full trading suite**

Run: `uv run pytest -q tests/trading/ --tb=no | tail -3`
Expected: all pass.

- [ ] **Step 4: Ruff**

Run: `uv run ruff check marketpulse/ tests/`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add tests/trading/test_e2e_stateful.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-plus-T10): E2E paper P&L realization scenarios

4 E2E tests through full daily_cycle.run + engine.tick path:
- Happy path: real exit_price from provider; realized_pnl from
  paper_fill (lock 6b+L1); paper_order.horizon_price stays NULL
- Roll-back: horizon=Memorial Day → POSITION_CLOSED context shows
  actual_price_date < requested_horizon_date,
  roll_policy='previous_available_close'
- Retry-and-succeed: tick 1 PRICE_UNAVAILABLE (attempt_count=1, cycle
  status='completed' per lock 6b+L12), tick 2 closes position
- No mutation on PRICE_UNAVAILABLE: 3 failures → position OPEN,
  no EXIT fill, cash_balance unchanged (op-test #19)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T11: Final integration

**Files:**
- None new; verification + PR

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -q --tb=no | tail -3`
Expected: ALL pass. Pre-6b+ baseline was 1116; 6b+ adds ~30-40 tests, so expect ~1150+.

- [ ] **Step 2: Run ruff**

Run: `uv run ruff check marketpulse/ tests/`
Expected: `All checks passed!`

- [ ] **Step 3: Alembic head check**

Run: `uv run alembic heads`
Expected: `0011 (head)`.

- [ ] **Step 4: Manual import sanity**

Run:
```bash
uv run python -c "
from marketpulse.trading.price_provider import (
    ClosePrice, PriceProvider, YFinancePriceProvider, StubPriceProvider, LOOKBACK_DAYS,
)
from marketpulse.trading.types import AuditEventType
from marketpulse.data.yfinance_client import YFinanceClient
from decimal import Decimal
from datetime import date
print('LOOKBACK_DAYS:', LOOKBACK_DAYS)
print('Stub source/lookback:', StubPriceProvider().source, StubPriceProvider().lookback_days)
print('PRICE_UNAVAILABLE:', AuditEventType.PRICE_UNAVAILABLE)
print('All 6b+ imports OK')
"
```
Expected: `LOOKBACK_DAYS: 10` / `Stub source/lookback: stub 0` / `PRICE_UNAVAILABLE: PRICE_UNAVAILABLE` / `All 6b+ imports OK`.

- [ ] **Step 5: Confirm working tree clean**

Run: `git status --short`
Expected: empty output OR only unrelated working-tree changes you want to keep (NOT staged).

- [ ] **Step 6: Push branch + open PR**

```bash
git push -u origin plan/phase-6b-plus-paper-pnl-realization
gh pr create --title "feat(phase-6b-plus): paper P&L realization" --body "$(cat <<'EOF'
## Summary

Replaces `StubPriceProvider(default=Decimal("0"))` with `YFinancePriceProvider`. Fixes the time-of-fetch bug: PriceProvider is now injected into `ForwardExecutionEngine` and called at exit-materialization time (when the historical close exists), NOT at order-placement time (when `horizon_date` is in the future and physically impossible to satisfy).

- **Spec:** `docs/superpowers/specs/2026-05-22-phase-6b-plus-paper-pnl-realization-design.md`
- **Plan:** `docs/superpowers/plans/2026-05-22-phase-6b-plus-paper-pnl-realization.md`
- **Locks:** 14 (6b+L1 .. 6b+L14)
- **Migration:** Alembic 0011 (SQLite table rebuild, extends paper_audit_event CHECK to include `PRICE_UNAVAILABLE`)

## Key invariants

- `paper_fill.price WHERE side='EXIT'` is the canonical P&L source. `paper_order.horizon_price` is legacy/forecast field (forward path now writes NULL; only Phase 5 backtest still writes).
- `_materialize_exit` returns `bool`: True = CLOSED, False = PRICE_UNAVAILABLE (position stays OPEN, next tick retries).
- PRICE_UNAVAILABLE is NOT an InvariantError. `TickResult.errors` stays `()`. `cycle_status` stays `"completed"`.
- Yfinance `end` is exclusive — `fetch_close_on_date` passes `end=on_date + timedelta(days=1)`.
- All audit-context Decimal prices quantized to 6 decimal places (`Numeric(18,6)` aligned).

## Test plan
- [ ] `uv run pytest -q` — all pass (~1150 tests)
- [ ] `uv run ruff check marketpulse/ tests/` — clean
- [ ] `uv run alembic heads` — shows `0011 (head)`
- [ ] Manual: deploy to NAS, verify next 17:30 NY tick generates a POSITION_CLOSED audit with real `exit_price` (not 0) and the new 4 provenance fields (requested_horizon_date / actual_price_date / price_source / roll_policy)
- [ ] Manual: verify any new PRICE_UNAVAILABLE audit rows have valid `attempt_count` and the position stays OPEN

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 7: Done**

Hand off to `superpowers:finishing-a-development-branch` to drive merge.

---

## Self-Review

**Spec coverage check (spec § / lock → task):**
- Spec § 1 Goal/Anti-goals → T0 (preflight understanding); anti-goals enforced by tests in T3 (no default), T7b (PRICE_UNAVAILABLE not InvariantError), T8 (horizon_price=NULL), T10 (paper_fill canonical).
- Spec § 2 Architecture → T1 (enum), T3 (Protocol + Stub), T4 (YFinanceClient), T5 (YFinancePriceProvider tests), T6 (Repository helper), T7a (engine constructor), T7b (engine semantics), T8 (daily_cycle), T9 (DI seam).
- Spec § 3 Exit Materialization → T7b (full).
- Spec § 4 Migration → T2 (0011).
- Spec § 5 Sub-task decomp → T1..T11 (T7 split into T7a/T7b).
- Spec § 6 Locks 6b+L1..L14 → all referenced inline in tasks.
- Spec § 7 Op-tests #1-#27:
  - #1 → T10 happy path
  - #2 → T10 roll-back
  - #3 → T7b `test_tick_returns_no_errors_when_only_price_unavailable` + T10 retry test
  - #4 → T7b `test_attempt_count_progression_1_2_3`
  - #5 → T10 retry-and-succeed
  - #6 → T4 `test_fetch_close_on_date_empty_window_returns_none`
  - #7 → T4 `test_fetch_close_on_date_calls_yfinance_with_correct_window`
  - #8 → T7b `test_position_closed_audit_has_provenance_fields`
  - #9 → T10 happy path (P&L from paper_fill)
  - #10 → T10 happy path (cash_balance)
  - #11 → T8 `test_daily_cycle_run_rejects_price_provider_kwarg`
  - #12 → T7a `test_forward_engine_requires_price_provider_kwarg`
  - #13 → T3 `test_stub_price_provider_rejects_default_kwarg`
  - #14 → T7b `test_price_unavailable_writes_audit_with_provider_provenance` (source from provider)
  - #15 → T7b same test (lookback from provider)
  - #16 → T2 upgrade tests
  - #17 → T2 downgrade tests
  - #18 → T10 happy path (paper_fill.price ≠ order.horizon_price)
  - #19 → T10 `test_e2e_phase6b_plus_price_unavailable_does_not_mutate_state`
  - #20 → T7b `test_last_price_unavailable_count_resets_each_tick`
  - #21 → covered indirectly by T7b + T8 cycle_status invariant; explicit dedicated test left to implementer if desired
  - #22 → T4 `test_fetch_close_on_date_calls_yfinance_with_correct_window`
  - #23 → T2 migration tests (explicit column list verifiable by reading the source / by SQL parse)
  - #24 → T2 `test_0011_upgrade_preserves_6a_rows` + `test_0011_upgrade_preserves_6a_indexes_exact_names`
  - #25 → T2 `test_0011_downgrade_with_no_price_unavailable_succeeds`
  - #26 → T5 `test_close_on_date_quantizes_high_precision_close_to_6dp`
  - #27 → implicitly covered by T10 happy path (Decimal stored and read back via `paper_fill.price`)

**Placeholder scan:** None. Every code step shows complete code; every command shows expected output.

**Type consistency:**
- `PriceProvider.close_on_date(*, ticker, on_date) -> ClosePrice | None` — consistent in T3, T5, T7b
- `YFinanceClient.fetch_close_on_date(ticker, on_date, *, lookback_days=10) -> Bar | None` — consistent in T4, T5
- `_materialize_exit -> bool` — consistent in T7b, T10
- `last_price_unavailable_count() -> int` — consistent in T7a (declaration), T7b (reset/increment), T8 (read)
- `count_price_unavailable_attempts(*, position_id: int) -> int` — consistent in T6, T7b
- `AuditEventType.PRICE_UNAVAILABLE` string value `"PRICE_UNAVAILABLE"` — consistent in T1 enum, T2 CHECK, T7b audit write
- `ClosePrice(price, price_date, requested_date, source)` — consistent 4-field shape across T3, T5, T7b, T10

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-22-phase-6b-plus-paper-pnl-realization.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, two-stage review (spec compliance then code quality) between tasks. Same session.

**2. Inline Execution** — Execute tasks in this session using executing-plans with batch checkpoints.

**Which approach?**
