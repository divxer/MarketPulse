# Phase 7a IBKR Read-Only Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an operator-triggered one-shot IBKR paper account read-only sync that captures broker truth into append-only `broker_*` snapshot tables without touching paper trading state.

**Architecture:** Add a new `marketpulse.broker` bounded context with pure DTOs, a read-only `BrokerReadClient` Protocol, an `ib_insync` adapter isolated to `ibkr_client.py`, a repository that writes only `broker_*` rows, and a CLI that creates exactly one `broker_sync_run` per invocation. Automated tests use fake clients only; real IBKR is manual smoke.

**Tech Stack:** Python 3.12, SQLAlchemy 2, Alembic, Pydantic Settings, `ib-insync`, pytest, ruff.

---

## Source Spec

Implement from:

- `docs/superpowers/specs/2026-05-23-phase-7a-ibkr-readonly-sync-design.md`

Keep these locks load-bearing:

- 7a is broker-truth capture, not broker execution.
- 7a writes only `broker_*`.
- 7a never writes `paper_order`, `paper_fill`, `paper_position`, or `paper_cash_ledger`.
- 7a has no UI, scheduler, daemon, or web-triggered sync.
- Automated tests never require real IBKR.

---

## File Structure

Create:

- `marketpulse/broker/__init__.py`  
  Package marker. No runtime behavior.
- `marketpulse/broker/types.py`  
  Pure dataclass DTOs and sync result/error types. No SQLAlchemy, no `ib_insync`.
- `marketpulse/broker/read_client.py`  
  `BrokerReadClient` Protocol with only `fetch_snapshot()`.
- `marketpulse/broker/ibkr_client.py`  
  Only file allowed to import `ib_insync`. Maps IBKR objects into DTOs.
- `marketpulse/broker/repository.py`  
  `broker_*` write helpers only. No paper repository imports.
- `marketpulse/broker/readonly_sync.py`  
  Orchestrates run creation, live-port guard, client fetch, account validation, persistence, and `SyncResult`.
- `scripts/sync_ibkr_readonly.py`  
  Operator CLI.
- `docs/operations/ibkr-readonly-sync-runbook.md`  
  Operator-first manual smoke/runbook.
- `tests/broker/test_types_and_contract.py`
- `tests/broker/test_readonly_sync.py`
- `tests/broker/test_repository.py`
- `tests/broker/test_ibkr_client_mapping.py`
- `tests/architecture/test_phase7a_ibkr_readonly_boundary.py`
- `tests/migration/test_0012_broker_snapshots.py`
- `alembic/versions/0012_phase7a_broker_snapshots.py`

Modify:

- `pyproject.toml`  
  Add `ib-insync`.
- `marketpulse/config.py`  
  Add IBKR settings.
- `marketpulse/db/models.py`  
  Add broker snapshot models.
- `tests/trading/test_models.py`  
  Add model tablename/column invariant test.

Do not modify:

- `marketpulse/trading/daily_cycle.py`
- `marketpulse/scheduler/paper_trading_tick.py`
- `marketpulse/web/routes/*`
- `marketpulse/web/templates/*`

---

## Task 1: Dependency and Settings

**Files:**

- Modify: `pyproject.toml`
- Modify: `marketpulse/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write failing settings tests**

Append to `tests/unit/test_config.py`:

```python
def test_ibkr_settings_defaults_to_paper_readonly(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "x" * 16)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    from marketpulse.config import Settings

    s = Settings(_env_file=None)

    assert s.ibkr_host == "127.0.0.1"
    assert s.ibkr_port == 7497
    assert s.ibkr_client_id == 71
    assert s.ibkr_account_id == ""
    assert s.ibkr_connect_timeout_seconds == 10
    assert s.ibkr_allow_live is False


def test_ibkr_settings_accept_env_overrides(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "x" * 16)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("IBKR_HOST", "ib-gateway")
    monkeypatch.setenv("IBKR_PORT", "4002")
    monkeypatch.setenv("IBKR_CLIENT_ID", "72")
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU1234567")
    monkeypatch.setenv("IBKR_CONNECT_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("MP_IBKR_ALLOW_LIVE", "true")
    from marketpulse.config import Settings

    s = Settings(_env_file=None)

    assert s.ibkr_host == "ib-gateway"
    assert s.ibkr_port == 4002
    assert s.ibkr_client_id == 72
    assert s.ibkr_account_id == "DU1234567"
    assert s.ibkr_connect_timeout_seconds == 3
    assert s.ibkr_allow_live is True
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
uv run pytest tests/unit/test_config.py::test_ibkr_settings_defaults_to_paper_readonly tests/unit/test_config.py::test_ibkr_settings_accept_env_overrides -q
```

Expected: fail because `Settings` has no `ibkr_*` fields.

- [ ] **Step 3: Add dependency**

In `pyproject.toml`, add to `[project].dependencies`:

```toml
    "ib-insync>=0.9.86",
```

Run:

```bash
uv sync
```

Expected: dependency lock/environment updates cleanly. If `uv sync` updates a lockfile, include it in the eventual commit.

- [ ] **Step 4: Add settings fields**

In `marketpulse/config.py`, add after the paper notification settings:

```python
    # Phase 7a IBKR read-only sync settings. Defaults target IBKR paper TWS/Gateway.
    ibkr_host: str = Field("127.0.0.1", alias="IBKR_HOST")
    ibkr_port: int = Field(7497, alias="IBKR_PORT", ge=1, le=65535)
    ibkr_client_id: int = Field(71, alias="IBKR_CLIENT_ID", ge=0)
    ibkr_account_id: str = Field("", alias="IBKR_ACCOUNT_ID")
    ibkr_connect_timeout_seconds: int = Field(
        10,
        alias="IBKR_CONNECT_TIMEOUT_SECONDS",
        ge=1,
    )
    ibkr_allow_live: bool = Field(False, alias="MP_IBKR_ALLOW_LIVE")
```

- [ ] **Step 5: Run tests and confirm pass**

Run:

```bash
uv run pytest tests/unit/test_config.py::test_ibkr_settings_defaults_to_paper_readonly tests/unit/test_config.py::test_ibkr_settings_accept_env_overrides -q
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock marketpulse/config.py tests/unit/test_config.py
git commit -m "feat: add ibkr readonly sync settings"
```

If there is no `uv.lock` change, omit `uv.lock`.

---

## Task 2: Broker Snapshot Models and Migration

**Files:**

- Modify: `marketpulse/db/models.py`
- Create: `alembic/versions/0012_phase7a_broker_snapshots.py`
- Modify: `tests/trading/test_models.py`
- Test: `tests/migration/test_0012_broker_snapshots.py`

- [ ] **Step 1: Write model invariant tests**

Append to `tests/trading/test_models.py`:

```python
def test_broker_snapshot_models_have_expected_tablenames():
    from marketpulse.db.models import (
        BrokerAccountSnapshot,
        BrokerCashSnapshot,
        BrokerExecutionSnapshot,
        BrokerOpenOrderSnapshot,
        BrokerPositionSnapshot,
        BrokerSyncRun,
    )

    assert BrokerSyncRun.__tablename__ == "broker_sync_run"
    assert BrokerAccountSnapshot.__tablename__ == "broker_account_snapshot"
    assert BrokerCashSnapshot.__tablename__ == "broker_cash_snapshot"
    assert BrokerPositionSnapshot.__tablename__ == "broker_position_snapshot"
    assert BrokerOpenOrderSnapshot.__tablename__ == "broker_open_order_snapshot"
    assert BrokerExecutionSnapshot.__tablename__ == "broker_execution_snapshot"


def test_broker_snapshot_models_use_decimal_numeric_columns():
    from sqlalchemy import Numeric, inspect

    from marketpulse.db.models import (
        BrokerAccountSnapshot,
        BrokerCashSnapshot,
        BrokerExecutionSnapshot,
        BrokerOpenOrderSnapshot,
        BrokerPositionSnapshot,
    )

    numeric_columns = {
        BrokerAccountSnapshot: {
            "net_liquidation",
            "buying_power",
            "maintenance_margin",
            "excess_liquidity",
        },
        BrokerCashSnapshot: {"cash_balance", "settled_cash", "accrued_interest"},
        BrokerPositionSnapshot: {
            "quantity",
            "avg_cost",
            "market_price",
            "market_value",
            "unrealized_pnl",
            "realized_pnl",
        },
        BrokerOpenOrderSnapshot: {"quantity", "limit_price"},
        BrokerExecutionSnapshot: {"quantity", "price"},
    }

    for model, expected_columns in numeric_columns.items():
        columns = {col.name: col.type for col in inspect(model).columns}
        for name in expected_columns:
            assert isinstance(columns[name], Numeric)
            assert columns[name].precision == 18
            assert columns[name].scale == 6


def test_broker_snapshot_rows_have_account_and_capture_columns():
    from sqlalchemy import inspect

    from marketpulse.db.models import (
        BrokerAccountSnapshot,
        BrokerCashSnapshot,
        BrokerExecutionSnapshot,
        BrokerOpenOrderSnapshot,
        BrokerPositionSnapshot,
    )

    for model in (
        BrokerAccountSnapshot,
        BrokerCashSnapshot,
        BrokerPositionSnapshot,
        BrokerOpenOrderSnapshot,
        BrokerExecutionSnapshot,
    ):
        columns = {col.name for col in inspect(model).columns}
        assert {"sync_run_id", "account_id", "broker_environment", "captured_at"} <= columns
```

- [ ] **Step 2: Write migration tests**

Create `tests/migration/test_0012_broker_snapshots.py`:

```python
from __future__ import annotations

import subprocess

from sqlalchemy import create_engine, inspect


def test_0012_upgrade_creates_broker_tables(tmp_path, monkeypatch):
    db_file = tmp_path / "broker.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")

    from marketpulse.config import get_settings

    get_settings.cache_clear()

    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)

    engine = create_engine(f"sqlite:///{db_file}")
    tables = set(inspect(engine).get_table_names())

    assert {
        "broker_sync_run",
        "broker_account_snapshot",
        "broker_cash_snapshot",
        "broker_position_snapshot",
        "broker_open_order_snapshot",
        "broker_execution_snapshot",
    } <= tables


def test_0012_downgrade_drops_broker_tables(tmp_path, monkeypatch):
    db_file = tmp_path / "broker.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")

    from marketpulse.config import get_settings

    get_settings.cache_clear()

    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)
    subprocess.run(["uv", "run", "alembic", "downgrade", "0011"], check=True)

    engine = create_engine(f"sqlite:///{db_file}")
    tables = set(inspect(engine).get_table_names())

    assert "broker_sync_run" not in tables
    assert "broker_account_snapshot" not in tables
    assert "broker_cash_snapshot" not in tables
    assert "broker_position_snapshot" not in tables
    assert "broker_open_order_snapshot" not in tables
    assert "broker_execution_snapshot" not in tables
    assert "paper_order" in tables
```

- [ ] **Step 3: Run tests and confirm failure**

Run:

```bash
uv run pytest tests/trading/test_models.py::test_broker_snapshot_models_have_expected_tablenames tests/trading/test_models.py::test_broker_snapshot_models_use_decimal_numeric_columns tests/trading/test_models.py::test_broker_snapshot_rows_have_account_and_capture_columns tests/migration/test_0012_broker_snapshots.py -q
```

Expected: fail because model classes and migration do not exist.

- [ ] **Step 4: Add model classes**

Append to `marketpulse/db/models.py` after `PaperAuditEvent`:

```python

# === Phase 7a broker snapshot models ===
# Broker truth capture only. These rows never drive paper_* state.


class BrokerSyncRun(Base):
    __tablename__ = "broker_sync_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    broker: Mapped[str] = mapped_column(String(16), nullable=False)
    broker_environment: Mapped[str] = mapped_column(String(16), nullable=False)
    account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_broker_sync_run_started", "started_at"),
        Index("ix_broker_sync_run_status_started", "status", "started_at"),
        CheckConstraint("broker IN ('IBKR')", name="ck_broker_sync_run_broker"),
        CheckConstraint(
            "broker_environment IN ('paper', 'live', 'unknown')",
            name="ck_broker_sync_run_environment",
        ),
        CheckConstraint(
            "status IN ('started', 'completed', 'failed')",
            name="ck_broker_sync_run_status",
        ),
    )


class BrokerAccountSnapshot(Base):
    __tablename__ = "broker_account_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_run_id: Mapped[int] = mapped_column(ForeignKey("broker_sync_run.id"), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_environment: Mapped[str] = mapped_column(String(16), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    account_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    net_liquidation: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    buying_power: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    maintenance_margin: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    excess_liquidity: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)

    __table_args__ = (Index("ix_broker_account_sync", "sync_run_id"),)


class BrokerCashSnapshot(Base):
    __tablename__ = "broker_cash_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_run_id: Mapped[int] = mapped_column(ForeignKey("broker_sync_run.id"), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_environment: Mapped[str] = mapped_column(String(16), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    cash_balance: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    settled_cash: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    accrued_interest: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)

    __table_args__ = (
        Index("ix_broker_cash_sync", "sync_run_id"),
        Index("ix_broker_cash_account_currency", "account_id", "currency"),
    )


class BrokerPositionSnapshot(Base):
    __tablename__ = "broker_position_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_run_id: Mapped[int] = mapped_column(ForeignKey("broker_sync_run.id"), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_environment: Mapped[str] = mapped_column(String(16), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quantity: Mapped[_Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    avg_cost: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    market_price: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    market_value: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    unrealized_pnl: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    realized_pnl: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)

    __table_args__ = (
        Index("ix_broker_position_sync", "sync_run_id"),
        Index("ix_broker_position_account_symbol", "account_id", "symbol"),
    )


class BrokerOpenOrderSnapshot(Base):
    __tablename__ = "broker_open_order_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_run_id: Mapped[int] = mapped_column(ForeignKey("broker_sync_run.id"), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_environment: Mapped[str] = mapped_column(String(16), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    broker_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    order_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quantity: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    limit_price: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_broker_open_order_sync", "sync_run_id"),
        Index("ix_broker_open_order_account_order", "account_id", "broker_order_id"),
    )


class BrokerExecutionSnapshot(Base):
    __tablename__ = "broker_execution_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_run_id: Mapped[int] = mapped_column(ForeignKey("broker_sync_run.id"), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_environment: Mapped[str] = mapped_column(String(16), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    broker_exec_id: Mapped[str] = mapped_column(String(128), nullable=False)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    quantity: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    price: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    __table_args__ = (
        Index("ix_broker_execution_sync", "sync_run_id"),
        Index("ix_broker_execution_account_exec", "account_id", "broker_exec_id"),
    )
```

- [ ] **Step 5: Add migration**

Create `alembic/versions/0012_phase7a_broker_snapshots.py`:

```python
"""phase7a broker snapshot tables

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-23
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NUMERIC = sa.Numeric(18, 6)


def upgrade() -> None:
    op.create_table(
        "broker_sync_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("broker", sa.String(16), nullable=False),
        sa.Column("broker_environment", sa.String(16), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_type", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("context", sa.JSON(), nullable=False, server_default="{}"),
        sa.CheckConstraint("broker IN ('IBKR')", name="ck_broker_sync_run_broker"),
        sa.CheckConstraint(
            "broker_environment IN ('paper', 'live', 'unknown')",
            name="ck_broker_sync_run_environment",
        ),
        sa.CheckConstraint(
            "status IN ('started', 'completed', 'failed')",
            name="ck_broker_sync_run_status",
        ),
    )
    op.create_index("ix_broker_sync_run_started", "broker_sync_run", ["started_at"])
    op.create_index(
        "ix_broker_sync_run_status_started",
        "broker_sync_run",
        ["status", "started_at"],
    )

    op.create_table(
        "broker_account_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sync_run_id", sa.Integer(), sa.ForeignKey("broker_sync_run.id"), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("broker_environment", sa.String(16), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account_type", sa.String(64), nullable=True),
        sa.Column("base_currency", sa.String(8), nullable=True),
        sa.Column("net_liquidation", NUMERIC, nullable=True),
        sa.Column("buying_power", NUMERIC, nullable=True),
        sa.Column("maintenance_margin", NUMERIC, nullable=True),
        sa.Column("excess_liquidity", NUMERIC, nullable=True),
    )
    op.create_index("ix_broker_account_sync", "broker_account_snapshot", ["sync_run_id"])

    op.create_table(
        "broker_cash_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sync_run_id", sa.Integer(), sa.ForeignKey("broker_sync_run.id"), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("broker_environment", sa.String(16), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("cash_balance", NUMERIC, nullable=True),
        sa.Column("settled_cash", NUMERIC, nullable=True),
        sa.Column("accrued_interest", NUMERIC, nullable=True),
    )
    op.create_index("ix_broker_cash_sync", "broker_cash_snapshot", ["sync_run_id"])
    op.create_index(
        "ix_broker_cash_account_currency",
        "broker_cash_snapshot",
        ["account_id", "currency"],
    )

    op.create_table(
        "broker_position_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sync_run_id", sa.Integer(), sa.ForeignKey("broker_sync_run.id"), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("broker_environment", sa.String(16), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("asset_class", sa.String(32), nullable=True),
        sa.Column("quantity", NUMERIC, nullable=False),
        sa.Column("avg_cost", NUMERIC, nullable=True),
        sa.Column("market_price", NUMERIC, nullable=True),
        sa.Column("market_value", NUMERIC, nullable=True),
        sa.Column("unrealized_pnl", NUMERIC, nullable=True),
        sa.Column("realized_pnl", NUMERIC, nullable=True),
    )
    op.create_index("ix_broker_position_sync", "broker_position_snapshot", ["sync_run_id"])
    op.create_index(
        "ix_broker_position_account_symbol",
        "broker_position_snapshot",
        ["account_id", "symbol"],
    )

    op.create_table(
        "broker_open_order_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sync_run_id", sa.Integer(), sa.ForeignKey("broker_sync_run.id"), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("broker_environment", sa.String(16), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("broker_order_id", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("side", sa.String(8), nullable=True),
        sa.Column("order_type", sa.String(32), nullable=True),
        sa.Column("quantity", NUMERIC, nullable=True),
        sa.Column("limit_price", NUMERIC, nullable=True),
        sa.Column("status", sa.String(64), nullable=True),
    )
    op.create_index("ix_broker_open_order_sync", "broker_open_order_snapshot", ["sync_run_id"])
    op.create_index(
        "ix_broker_open_order_account_order",
        "broker_open_order_snapshot",
        ["account_id", "broker_order_id"],
    )

    op.create_table(
        "broker_execution_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sync_run_id", sa.Integer(), sa.ForeignKey("broker_sync_run.id"), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("broker_environment", sa.String(16), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("broker_exec_id", sa.String(128), nullable=False),
        sa.Column("broker_order_id", sa.String(64), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("side", sa.String(8), nullable=True),
        sa.Column("quantity", NUMERIC, nullable=True),
        sa.Column("price", NUMERIC, nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_broker_execution_sync", "broker_execution_snapshot", ["sync_run_id"])
    op.create_index(
        "ix_broker_execution_account_exec",
        "broker_execution_snapshot",
        ["account_id", "broker_exec_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_broker_execution_account_exec", table_name="broker_execution_snapshot")
    op.drop_index("ix_broker_execution_sync", table_name="broker_execution_snapshot")
    op.drop_table("broker_execution_snapshot")
    op.drop_index("ix_broker_open_order_account_order", table_name="broker_open_order_snapshot")
    op.drop_index("ix_broker_open_order_sync", table_name="broker_open_order_snapshot")
    op.drop_table("broker_open_order_snapshot")
    op.drop_index("ix_broker_position_account_symbol", table_name="broker_position_snapshot")
    op.drop_index("ix_broker_position_sync", table_name="broker_position_snapshot")
    op.drop_table("broker_position_snapshot")
    op.drop_index("ix_broker_cash_account_currency", table_name="broker_cash_snapshot")
    op.drop_index("ix_broker_cash_sync", table_name="broker_cash_snapshot")
    op.drop_table("broker_cash_snapshot")
    op.drop_index("ix_broker_account_sync", table_name="broker_account_snapshot")
    op.drop_table("broker_account_snapshot")
    op.drop_index("ix_broker_sync_run_status_started", table_name="broker_sync_run")
    op.drop_index("ix_broker_sync_run_started", table_name="broker_sync_run")
    op.drop_table("broker_sync_run")
```

- [ ] **Step 6: Run tests and confirm pass**

Run:

```bash
uv run pytest tests/trading/test_models.py::test_broker_snapshot_models_have_expected_tablenames tests/trading/test_models.py::test_broker_snapshot_models_use_decimal_numeric_columns tests/trading/test_models.py::test_broker_snapshot_rows_have_account_and_capture_columns tests/migration/test_0012_broker_snapshots.py -q
uv run alembic heads
```

Expected:

- pytest passes.
- Alembic prints `0012 (head)`.

- [ ] **Step 7: Commit**

```bash
git add marketpulse/db/models.py alembic/versions/0012_phase7a_broker_snapshots.py tests/trading/test_models.py tests/migration/test_0012_broker_snapshots.py
git commit -m "feat: add broker snapshot tables"
```

---

## Task 3: Broker DTOs, Protocol, and Safety Helpers

**Files:**

- Create: `marketpulse/broker/__init__.py`
- Create: `marketpulse/broker/types.py`
- Create: `marketpulse/broker/read_client.py`
- Test: `tests/broker/test_types_and_contract.py`

- [ ] **Step 1: Write failing tests**

Create `tests/broker/test_types_and_contract.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import get_protocol_members


def test_broker_read_client_protocol_only_exposes_fetch_snapshot():
    from marketpulse.broker.read_client import BrokerReadClient

    assert get_protocol_members(BrokerReadClient) == frozenset({"fetch_snapshot"})


def test_broker_snapshot_is_pure_dataclass():
    from marketpulse.broker.types import (
        BrokerAccount,
        BrokerCash,
        BrokerSnapshot,
    )

    captured_at = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    snapshot = BrokerSnapshot(
        broker="IBKR",
        broker_environment="paper",
        account_id="DU123",
        captured_at=captured_at,
        account=BrokerAccount(
            account_id="DU123",
            account_type="INDIVIDUAL",
            base_currency="USD",
            net_liquidation=Decimal("100000.00"),
            buying_power=Decimal("50000.00"),
            maintenance_margin=None,
            excess_liquidity=None,
        ),
        cash=(
            BrokerCash(
                account_id="DU123",
                currency="USD",
                cash_balance=Decimal("1000.00"),
                settled_cash=Decimal("900.00"),
                accrued_interest=Decimal("0.00"),
            ),
        ),
        positions=(),
        open_orders=(),
        executions=(),
    )

    assert snapshot.broker == "IBKR"
    assert snapshot.cash[0].cash_balance == Decimal("1000.00")


def test_classify_broker_environment_blocks_known_live_port():
    from marketpulse.broker.types import classify_broker_environment

    assert classify_broker_environment(7497) == "paper"
    assert classify_broker_environment(7496) == "live"
    assert classify_broker_environment(4002) == "unknown"
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
uv run pytest tests/broker/test_types_and_contract.py -q
```

Expected: fail because `marketpulse.broker` modules do not exist.

- [ ] **Step 3: Add package marker**

Create `marketpulse/broker/__init__.py`:

```python
"""Read-only broker truth capture for Phase 7+."""
```

- [ ] **Step 4: Add DTOs**

Create `marketpulse/broker/types.py`:

```python
"""Pure broker DTOs for read-only broker truth capture."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

BrokerName = Literal["IBKR"]
BrokerEnvironment = Literal["paper", "live", "unknown"]
SyncStatus = Literal["started", "completed", "failed"]


def classify_broker_environment(port: int) -> BrokerEnvironment:
    if port == 7497:
        return "paper"
    if port == 7496:
        return "live"
    return "unknown"


@dataclass(frozen=True)
class BrokerAccount:
    account_id: str
    account_type: str | None
    base_currency: str | None
    net_liquidation: Decimal | None
    buying_power: Decimal | None
    maintenance_margin: Decimal | None
    excess_liquidity: Decimal | None


@dataclass(frozen=True)
class BrokerCash:
    account_id: str
    currency: str
    cash_balance: Decimal | None
    settled_cash: Decimal | None
    accrued_interest: Decimal | None


@dataclass(frozen=True)
class BrokerPosition:
    account_id: str
    symbol: str
    asset_class: str | None
    quantity: Decimal
    avg_cost: Decimal | None
    market_price: Decimal | None
    market_value: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl: Decimal | None


@dataclass(frozen=True)
class BrokerOpenOrder:
    account_id: str
    broker_order_id: str
    symbol: str | None
    side: str | None
    order_type: str | None
    quantity: Decimal | None
    limit_price: Decimal | None
    status: str | None


@dataclass(frozen=True)
class BrokerExecution:
    account_id: str
    broker_exec_id: str
    broker_order_id: str | None
    symbol: str | None
    side: str | None
    quantity: Decimal | None
    price: Decimal | None
    executed_at: datetime | None


@dataclass(frozen=True)
class BrokerSnapshot:
    broker: BrokerName
    broker_environment: BrokerEnvironment
    account_id: str
    captured_at: datetime
    account: BrokerAccount
    cash: tuple[BrokerCash, ...]
    positions: tuple[BrokerPosition, ...]
    open_orders: tuple[BrokerOpenOrder, ...]
    executions: tuple[BrokerExecution, ...]


@dataclass(frozen=True)
class SyncResult:
    sync_run_id: int
    broker: BrokerName
    broker_environment: BrokerEnvironment
    account_id: str | None
    status: SyncStatus
    host: str
    port: int
    client_id: int
    account_snapshots: int = 0
    cash_rows: int = 0
    positions: int = 0
    open_orders: int = 0
    executions: int = 0
    error_type: str | None = None
    error_message: str | None = None
```

- [ ] **Step 5: Add Protocol**

Create `marketpulse/broker/read_client.py`:

```python
"""Read-only broker client Protocol."""

from __future__ import annotations

from typing import Protocol

from marketpulse.broker.types import BrokerSnapshot


class BrokerReadClient(Protocol):
    def fetch_snapshot(self) -> BrokerSnapshot: ...
```

- [ ] **Step 6: Run tests and confirm pass**

Run:

```bash
uv run pytest tests/broker/test_types_and_contract.py -q
```

Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add marketpulse/broker/__init__.py marketpulse/broker/types.py marketpulse/broker/read_client.py tests/broker/test_types_and_contract.py
git commit -m "feat: add broker readonly dto contract"
```

---

## Task 4: Broker Repository

**Files:**

- Create: `marketpulse/broker/repository.py`
- Test: `tests/broker/test_repository.py`

- [ ] **Step 1: Write failing repository tests**

Create `tests/broker/test_repository.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from marketpulse.broker.types import (
    BrokerAccount,
    BrokerCash,
    BrokerExecution,
    BrokerOpenOrder,
    BrokerPosition,
    BrokerSnapshot,
)
from marketpulse.db.base import Base
from marketpulse.db.models import (
    BrokerAccountSnapshot,
    BrokerCashSnapshot,
    BrokerExecutionSnapshot,
    BrokerOpenOrderSnapshot,
    BrokerPositionSnapshot,
    BrokerSyncRun,
    PaperCashLedger,
    PaperFill,
    PaperOrder,
    PaperPosition,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _snapshot(account_id: str = "DU123") -> BrokerSnapshot:
    captured_at = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    return BrokerSnapshot(
        broker="IBKR",
        broker_environment="paper",
        account_id=account_id,
        captured_at=captured_at,
        account=BrokerAccount(
            account_id=account_id,
            account_type="INDIVIDUAL",
            base_currency="USD",
            net_liquidation=Decimal("100000.00"),
            buying_power=Decimal("50000.00"),
            maintenance_margin=Decimal("1000.00"),
            excess_liquidity=Decimal("49000.00"),
        ),
        cash=(
            BrokerCash(account_id, "USD", Decimal("1000"), Decimal("900"), Decimal("0")),
        ),
        positions=(
            BrokerPosition(
                account_id,
                "AAPL",
                "STK",
                Decimal("3"),
                Decimal("180.00"),
                Decimal("190.00"),
                Decimal("570.00"),
                Decimal("30.00"),
                Decimal("0.00"),
            ),
        ),
        open_orders=(
            BrokerOpenOrder(
                account_id,
                "1001",
                "MSFT",
                "BUY",
                "LMT",
                Decimal("2"),
                Decimal("300.00"),
                "Submitted",
            ),
        ),
        executions=(
            BrokerExecution(
                account_id,
                "E123",
                "1000",
                "NVDA",
                "BOT",
                Decimal("1"),
                Decimal("900.00"),
                captured_at,
            ),
        ),
    )


def _count(session: Session, model) -> int:
    return int(session.execute(select(func.count(model.id))).scalar() or 0)


def test_repository_writes_started_completed_and_snapshots():
    from marketpulse.broker.repository import (
        create_started_run,
        mark_run_completed,
        persist_snapshot_rows,
    )

    session = _session()
    started_at = datetime(2026, 5, 23, 21, 0, tzinfo=UTC)
    run = create_started_run(
        session,
        started_at=started_at,
        broker="IBKR",
        broker_environment="paper",
        account_id=None,
        context={"host": "127.0.0.1"},
    )
    snapshot = _snapshot()

    counts = persist_snapshot_rows(session, sync_run_id=run.id, snapshot=snapshot)
    mark_run_completed(session, sync_run_id=run.id, completed_at=snapshot.captured_at, account_id="DU123")
    session.commit()

    saved = session.get(BrokerSyncRun, run.id)
    assert saved is not None
    assert saved.status == "completed"
    assert saved.account_id == "DU123"
    assert counts == {
        "account_snapshots": 1,
        "cash_rows": 1,
        "positions": 1,
        "open_orders": 1,
        "executions": 1,
    }
    assert _count(session, BrokerAccountSnapshot) == 1
    assert _count(session, BrokerCashSnapshot) == 1
    assert _count(session, BrokerPositionSnapshot) == 1
    assert _count(session, BrokerOpenOrderSnapshot) == 1
    assert _count(session, BrokerExecutionSnapshot) == 1


def test_repository_marks_failed_without_snapshot_rows():
    from marketpulse.broker.repository import create_started_run, mark_run_failed

    session = _session()
    started_at = datetime(2026, 5, 23, 21, 0, tzinfo=UTC)
    run = create_started_run(
        session,
        started_at=started_at,
        broker="IBKR",
        broker_environment="unknown",
        account_id=None,
        context={"host": "127.0.0.1"},
    )
    mark_run_failed(
        session,
        sync_run_id=run.id,
        completed_at=started_at,
        error_type="ConnectionError",
        error_message="cannot connect",
        context_patch={"port": 7497},
    )
    session.commit()

    saved = session.get(BrokerSyncRun, run.id)
    assert saved is not None
    assert saved.status == "failed"
    assert saved.error_type == "ConnectionError"
    assert saved.context["port"] == 7497
    assert _count(session, BrokerAccountSnapshot) == 0


def test_repository_append_only_and_does_not_touch_paper_tables():
    from marketpulse.broker.repository import (
        create_started_run,
        mark_run_completed,
        persist_snapshot_rows,
    )

    session = _session()
    before = {
        "paper_order": _count(session, PaperOrder),
        "paper_fill": _count(session, PaperFill),
        "paper_position": _count(session, PaperPosition),
        "paper_cash_ledger": _count(session, PaperCashLedger),
    }
    for minute in (0, 5):
        captured_at = datetime(2026, 5, 23, 21, minute, tzinfo=UTC)
        snapshot = _snapshot()
        run = create_started_run(
            session,
            started_at=captured_at,
            broker="IBKR",
            broker_environment="paper",
            account_id=None,
            context={},
        )
        persist_snapshot_rows(session, sync_run_id=run.id, snapshot=snapshot)
        mark_run_completed(session, sync_run_id=run.id, completed_at=captured_at, account_id="DU123")
    session.commit()

    assert _count(session, BrokerSyncRun) == 2
    assert _count(session, BrokerPositionSnapshot) == 2
    after = {
        "paper_order": _count(session, PaperOrder),
        "paper_fill": _count(session, PaperFill),
        "paper_position": _count(session, PaperPosition),
        "paper_cash_ledger": _count(session, PaperCashLedger),
    }
    assert after == before


def test_repository_rejects_child_rows_for_different_account():
    from marketpulse.broker.repository import create_started_run, persist_snapshot_rows

    session = _session()
    started_at = datetime(2026, 5, 23, 21, 0, tzinfo=UTC)
    run = create_started_run(
        session,
        started_at=started_at,
        broker="IBKR",
        broker_environment="paper",
        account_id=None,
        context={},
    )
    snapshot = _snapshot("DU123")
    bad_snapshot = BrokerSnapshot(
        broker=snapshot.broker,
        broker_environment=snapshot.broker_environment,
        account_id=snapshot.account_id,
        captured_at=snapshot.captured_at,
        account=snapshot.account,
        cash=(BrokerCash("DU999", "USD", Decimal("1"), None, None),),
        positions=snapshot.positions,
        open_orders=snapshot.open_orders,
        executions=snapshot.executions,
    )

    try:
        persist_snapshot_rows(session, sync_run_id=run.id, snapshot=bad_snapshot)
    except ValueError as exc:
        assert "snapshot child account mismatch" in str(exc)
    else:
        raise AssertionError("mixed-account snapshot rows should be rejected")
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
uv run pytest tests/broker/test_repository.py -q
```

Expected: fail because `marketpulse.broker.repository` does not exist.

- [ ] **Step 3: Implement repository**

Create `marketpulse/broker/repository.py`:

```python
"""Append-only broker snapshot writes for Phase 7a."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from marketpulse.broker.types import BrokerEnvironment, BrokerName, BrokerSnapshot
from marketpulse.db.models import (
    BrokerAccountSnapshot,
    BrokerCashSnapshot,
    BrokerExecutionSnapshot,
    BrokerOpenOrderSnapshot,
    BrokerPositionSnapshot,
    BrokerSyncRun,
)


def create_started_run(
    session: Session,
    *,
    started_at: datetime,
    broker: BrokerName,
    broker_environment: BrokerEnvironment,
    account_id: str | None,
    context: dict,
) -> BrokerSyncRun:
    run = BrokerSyncRun(
        started_at=started_at,
        completed_at=None,
        broker=broker,
        broker_environment=broker_environment,
        account_id=account_id,
        status="started",
        error_type=None,
        error_message=None,
        context=context,
    )
    session.add(run)
    session.flush()
    return run


def mark_run_completed(
    session: Session,
    *,
    sync_run_id: int,
    completed_at: datetime,
    account_id: str,
    context_patch: dict | None = None,
) -> None:
    run = session.get(BrokerSyncRun, sync_run_id)
    if run is None:
        raise ValueError(f"broker_sync_run not found: {sync_run_id}")
    run.completed_at = completed_at
    run.account_id = account_id
    run.status = "completed"
    if context_patch:
        run.context = {**(run.context or {}), **context_patch}
    session.flush()


def mark_run_failed(
    session: Session,
    *,
    sync_run_id: int,
    completed_at: datetime,
    error_type: str,
    error_message: str,
    context_patch: dict | None = None,
) -> None:
    run = session.get(BrokerSyncRun, sync_run_id)
    if run is None:
        raise ValueError(f"broker_sync_run not found: {sync_run_id}")
    run.completed_at = completed_at
    run.status = "failed"
    run.error_type = error_type
    run.error_message = error_message
    if context_patch:
        run.context = {**(run.context or {}), **context_patch}
    session.flush()


SnapshotCounts = dict[Literal["account_snapshots", "cash_rows", "positions", "open_orders", "executions"], int]


def _assert_child_account(snapshot_account_id: str, child_account_id: str) -> None:
    if child_account_id != snapshot_account_id:
        raise ValueError(
            "snapshot child account mismatch: "
            f"{child_account_id} != {snapshot_account_id}"
        )


def persist_snapshot_rows(
    session: Session,
    *,
    sync_run_id: int,
    snapshot: BrokerSnapshot,
) -> SnapshotCounts:
    account = snapshot.account
    _assert_child_account(snapshot.account_id, account.account_id)
    for child in (*snapshot.cash, *snapshot.positions, *snapshot.open_orders, *snapshot.executions):
        _assert_child_account(snapshot.account_id, child.account_id)
    common = {
        "sync_run_id": sync_run_id,
        "account_id": snapshot.account_id,
        "broker_environment": snapshot.broker_environment,
        "captured_at": snapshot.captured_at,
    }
    session.add(
        BrokerAccountSnapshot(
            **common,
            account_type=account.account_type,
            base_currency=account.base_currency,
            net_liquidation=account.net_liquidation,
            buying_power=account.buying_power,
            maintenance_margin=account.maintenance_margin,
            excess_liquidity=account.excess_liquidity,
        )
    )
    for cash in snapshot.cash:
        session.add(BrokerCashSnapshot(**common, currency=cash.currency,
                                       cash_balance=cash.cash_balance,
                                       settled_cash=cash.settled_cash,
                                       accrued_interest=cash.accrued_interest))
    for position in snapshot.positions:
        session.add(BrokerPositionSnapshot(**common, symbol=position.symbol,
                                           asset_class=position.asset_class,
                                           quantity=position.quantity,
                                           avg_cost=position.avg_cost,
                                           market_price=position.market_price,
                                           market_value=position.market_value,
                                           unrealized_pnl=position.unrealized_pnl,
                                           realized_pnl=position.realized_pnl))
    for order in snapshot.open_orders:
        session.add(BrokerOpenOrderSnapshot(**common, broker_order_id=order.broker_order_id,
                                            symbol=order.symbol, side=order.side,
                                            order_type=order.order_type,
                                            quantity=order.quantity,
                                            limit_price=order.limit_price,
                                            status=order.status))
    for execution in snapshot.executions:
        session.add(BrokerExecutionSnapshot(**common, broker_exec_id=execution.broker_exec_id,
                                            broker_order_id=execution.broker_order_id,
                                            symbol=execution.symbol, side=execution.side,
                                            quantity=execution.quantity, price=execution.price,
                                            executed_at=execution.executed_at))
    session.flush()
    return {
        "account_snapshots": 1,
        "cash_rows": len(snapshot.cash),
        "positions": len(snapshot.positions),
        "open_orders": len(snapshot.open_orders),
        "executions": len(snapshot.executions),
    }
```

- [ ] **Step 4: Run tests and lint**

Run:

```bash
uv run pytest tests/broker/test_repository.py -q
uv run ruff check marketpulse/broker/repository.py tests/broker/test_repository.py
```

Expected: tests pass; ruff passes.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/broker/repository.py tests/broker/test_repository.py
git commit -m "feat: persist broker readonly snapshots"
```

---

## Task 5: Readonly Sync Orchestration

**Files:**

- Create: `marketpulse/broker/readonly_sync.py`
- Test: `tests/broker/test_readonly_sync.py`

- [ ] **Step 1: Write failing orchestration tests**

Create `tests/broker/test_readonly_sync.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from marketpulse.broker.types import BrokerAccount, BrokerSnapshot
from marketpulse.db.base import Base
from marketpulse.db.models import BrokerAccountSnapshot, BrokerSyncRun


class FakeClient:
    def __init__(self, snapshot: BrokerSnapshot | None = None, error: Exception | None = None):
        self.snapshot = snapshot
        self.error = error
        self.called = False

    def fetch_snapshot(self) -> BrokerSnapshot:
        self.called = True
        if self.error:
            raise self.error
        assert self.snapshot is not None
        return self.snapshot


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _snapshot(account_id: str = "DU123") -> BrokerSnapshot:
    captured_at = datetime(2026, 5, 23, 21, 30, tzinfo=UTC)
    return BrokerSnapshot(
        broker="IBKR",
        broker_environment="paper",
        account_id=account_id,
        captured_at=captured_at,
        account=BrokerAccount(
            account_id=account_id,
            account_type="INDIVIDUAL",
            base_currency="USD",
            net_liquidation=Decimal("100000"),
            buying_power=Decimal("50000"),
            maintenance_margin=None,
            excess_liquidity=None,
        ),
        cash=(),
        positions=(),
        open_orders=(),
        executions=(),
    )


def _count(session: Session, model) -> int:
    return int(session.execute(select(func.count(model.id))).scalar() or 0)


def test_sync_completed_writes_snapshot_and_completed_run():
    from marketpulse.broker.readonly_sync import IbkrSyncConfig, run_readonly_sync

    session = _session()
    config = IbkrSyncConfig(host="127.0.0.1", port=7497, client_id=71, account_id="DU123",
                            timeout_seconds=10, allow_live=False)
    result = run_readonly_sync(session, client=FakeClient(_snapshot()), config=config,
                               now=datetime(2026, 5, 23, 21, 0, tzinfo=UTC))
    session.commit()

    assert result.status == "completed"
    assert result.account_id == "DU123"
    assert _count(session, BrokerSyncRun) == 1
    assert _count(session, BrokerAccountSnapshot) == 1
    run = session.get(BrokerSyncRun, result.sync_run_id)
    assert run is not None
    assert run.context["host"] == "127.0.0.1"
    assert run.context["execution_window_start"] is not None
    assert run.context["selected_account_id"] == "DU123"


def test_connection_failure_writes_failed_run_without_snapshots():
    from marketpulse.broker.readonly_sync import IbkrSyncConfig, run_readonly_sync

    session = _session()
    config = IbkrSyncConfig(host="127.0.0.1", port=7497, client_id=71, account_id="",
                            timeout_seconds=10, allow_live=False)
    result = run_readonly_sync(session, client=FakeClient(error=ConnectionError("down")),
                               config=config, now=datetime(2026, 5, 23, 21, 0, tzinfo=UTC))
    session.commit()

    assert result.status == "failed"
    assert result.error_type == "ConnectionError"
    assert _count(session, BrokerSyncRun) == 1
    assert _count(session, BrokerAccountSnapshot) == 0
    run = session.get(BrokerSyncRun, result.sync_run_id)
    assert run is not None
    assert run.completed_at == datetime(2026, 5, 23, 21, 0, tzinfo=UTC)


def test_multiple_account_ambiguity_from_client_writes_failed_run_without_snapshots():
    from marketpulse.broker.readonly_sync import IbkrSyncConfig, run_readonly_sync

    session = _session()
    config = IbkrSyncConfig(host="127.0.0.1", port=7497, client_id=71, account_id="",
                            timeout_seconds=10, allow_live=False)
    result = run_readonly_sync(
        session,
        client=FakeClient(error=RuntimeError("IBKR returned 2 accounts; configure IBKR_ACCOUNT_ID")),
        config=config,
        now=datetime(2026, 5, 23, 21, 0, tzinfo=UTC),
    )
    session.commit()

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"
    assert "configure IBKR_ACCOUNT_ID" in (result.error_message or "")
    assert _count(session, BrokerSyncRun) == 1
    assert _count(session, BrokerAccountSnapshot) == 0


def test_account_mismatch_fails_closed_without_snapshots():
    from marketpulse.broker.readonly_sync import IbkrSyncConfig, run_readonly_sync

    session = _session()
    config = IbkrSyncConfig(host="127.0.0.1", port=7497, client_id=71, account_id="DU999",
                            timeout_seconds=10, allow_live=False)
    result = run_readonly_sync(session, client=FakeClient(_snapshot("DU123")), config=config,
                               now=datetime(2026, 5, 23, 21, 0, tzinfo=UTC))
    session.commit()

    assert result.status == "failed"
    assert result.error_type == "AccountMismatchError"
    assert _count(session, BrokerAccountSnapshot) == 0


def test_live_port_block_fails_before_fetching_snapshot():
    from marketpulse.broker.readonly_sync import IbkrSyncConfig, run_readonly_sync

    session = _session()
    client = FakeClient(_snapshot())
    config = IbkrSyncConfig(host="127.0.0.1", port=7496, client_id=71, account_id="",
                            timeout_seconds=10, allow_live=False)
    result = run_readonly_sync(session, client=client, config=config,
                               now=datetime(2026, 5, 23, 21, 0, tzinfo=UTC))
    session.commit()

    assert result.status == "failed"
    assert result.error_type == "LivePortBlockedError"
    assert client.called is False
    assert _count(session, BrokerSyncRun) == 1
    assert _count(session, BrokerAccountSnapshot) == 0
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
uv run pytest tests/broker/test_readonly_sync.py -q
```

Expected: fail because `readonly_sync.py` does not exist.

- [ ] **Step 3: Implement readonly sync**

Create `marketpulse/broker/readonly_sync.py`:

```python
"""One-shot read-only broker sync orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from marketpulse.broker.read_client import BrokerReadClient
from marketpulse.broker.repository import (
    create_started_run,
    mark_run_completed,
    mark_run_failed,
    persist_snapshot_rows,
)
from marketpulse.broker.types import BrokerEnvironment, SyncResult, classify_broker_environment

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class IbkrSyncConfig:
    host: str
    port: int
    client_id: int
    account_id: str
    timeout_seconds: int
    allow_live: bool


class LivePortBlockedError(RuntimeError):
    pass


class AccountMismatchError(RuntimeError):
    pass


def _execution_window(now: datetime) -> tuple[datetime, datetime]:
    now_utc = now.astimezone(UTC)
    ny_date = now_utc.astimezone(NY).date()
    start_ny = datetime.combine(ny_date, time.min, tzinfo=NY)
    return start_ny.astimezone(UTC), now_utc


def _base_context(config: IbkrSyncConfig, *, selected_account_id: str | None,
                  window_start: datetime | None, window_end: datetime | None) -> dict:
    return {
        "host": config.host,
        "port": config.port,
        "client_id": config.client_id,
        "configured_account_id": config.account_id or None,
        "selected_account_id": selected_account_id,
        "allow_live": config.allow_live,
        "execution_window_start": window_start.isoformat() if window_start else None,
        "execution_window_end": window_end.isoformat() if window_end else None,
    }


def run_readonly_sync(
    session: Session,
    *,
    client: BrokerReadClient,
    config: IbkrSyncConfig,
    now: datetime | None = None,
) -> SyncResult:
    started_at = (now or datetime.now(UTC)).astimezone(UTC)
    environment: BrokerEnvironment = classify_broker_environment(config.port)
    window_start, window_end = _execution_window(started_at)
    run = create_started_run(
        session,
        started_at=started_at,
        broker="IBKR",
        broker_environment=environment,
        account_id=config.account_id or None,
        context=_base_context(config, selected_account_id=None,
                              window_start=window_start, window_end=window_end),
    )

    try:
        if environment == "live" and not config.allow_live:
            raise LivePortBlockedError("Refusing to connect to known IBKR live port 7496")

        snapshot = client.fetch_snapshot()
        if config.account_id and snapshot.account_id != config.account_id:
            raise AccountMismatchError(
                f"Configured account {config.account_id} does not match returned account {snapshot.account_id}"
            )

        counts = persist_snapshot_rows(session, sync_run_id=run.id, snapshot=snapshot)
        mark_run_completed(
            session,
            sync_run_id=run.id,
            completed_at=snapshot.captured_at,
            account_id=snapshot.account_id,
            context_patch=_base_context(
                config,
                selected_account_id=snapshot.account_id,
                window_start=window_start,
                window_end=window_end,
            ),
        )
        session.flush()
        return SyncResult(
            sync_run_id=run.id,
            broker="IBKR",
            broker_environment=environment,
            account_id=snapshot.account_id,
            status="completed",
            host=config.host,
            port=config.port,
            client_id=config.client_id,
            **counts,
        )
    except Exception as exc:
        mark_run_failed(
            session,
            sync_run_id=run.id,
            completed_at=started_at,
            error_type=type(exc).__name__,
            error_message=str(exc),
            context_patch=_base_context(config, selected_account_id=None,
                                        window_start=window_start, window_end=window_end),
        )
        session.flush()
        return SyncResult(
            sync_run_id=run.id,
            broker="IBKR",
            broker_environment=environment,
            account_id=None,
            status="failed",
            host=config.host,
            port=config.port,
            client_id=config.client_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
```

- [ ] **Step 4: Run tests and confirm pass**

Run:

```bash
uv run pytest tests/broker/test_readonly_sync.py -q
uv run ruff check marketpulse/broker/readonly_sync.py tests/broker/test_readonly_sync.py
```

Expected: tests pass; ruff passes.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/broker/readonly_sync.py tests/broker/test_readonly_sync.py
git commit -m "feat: orchestrate ibkr readonly sync"
```

---

## Task 6: IBKR Adapter Mapping

**Files:**

- Create: `marketpulse/broker/ibkr_client.py`
- Test: `tests/broker/test_ibkr_client_mapping.py`

- [ ] **Step 1: Write mapping tests using fake IBKR-like objects**

Create `tests/broker/test_ibkr_client_mapping.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal


@dataclass
class FakeContract:
    symbol: str = "AAPL"
    secType: str = "STK"


@dataclass
class FakePosition:
    account: str
    contract: FakeContract
    position: float
    avgCost: float


@dataclass
class FakeAccountValue:
    tag: str
    currency: str
    value: str


class FakeIB:
    def __init__(self) -> None:
        self.connected_kwargs = {}
        self.disconnected = False

    def connect(self, host: str, port: int, *, clientId: int, timeout: int, readonly: bool) -> None:
        self.connected_kwargs = {
            "host": host,
            "port": port,
            "clientId": clientId,
            "timeout": timeout,
            "readonly": readonly,
        }

    def disconnect(self) -> None:
        self.disconnected = True

    def managedAccounts(self) -> list[str]:
        return ["DU123"]

    def accountValues(self, account: str):
        assert account == "DU123"
        return [
            FakeAccountValue("AccountType", "", "INDIVIDUAL"),
            FakeAccountValue("BaseCurrency", "", "EUR"),
            FakeAccountValue("NetLiquidation", "EUR", "100000"),
            FakeAccountValue("BuyingPower", "EUR", "50000"),
        ]

    def positions(self) -> list[FakePosition]:
        return []

    def openTrades(self) -> list:
        return []

    def reqExecutions(self, filt) -> list:
        return []


class MultiAccountFakeIB(FakeIB):
    def managedAccounts(self) -> list[str]:
        return ["DU1", "DU2"]


def test_decimal_conversion_avoids_float_repr_artifacts():
    from marketpulse.broker.ibkr_client import _decimal_or_none

    assert _decimal_or_none(0.1) == Decimal("0.1")
    assert _decimal_or_none(None) is None
    assert _decimal_or_none("nan") is None
    assert _decimal_or_none(Decimal("NaN")) is None
    assert _decimal_or_none(float("inf")) is None
    assert _decimal_or_none("1.7976931348623157E308") is None


def test_position_mapping_returns_pure_dto():
    from marketpulse.broker.ibkr_client import _map_position

    mapped = _map_position(
        FakePosition("DU123", FakeContract("AAPL", "STK"), 3.0, 180.25),
        market_price=190.0,
        market_value=570.0,
        unrealized_pnl=30.0,
        realized_pnl=0.0,
    )

    assert mapped.account_id == "DU123"
    assert mapped.symbol == "AAPL"
    assert mapped.asset_class == "STK"
    assert mapped.quantity == Decimal("3.0")
    assert mapped.avg_cost == Decimal("180.25")
    assert mapped.market_price == Decimal("190.0")
    assert mapped.market_value == Decimal("570.0")
    assert mapped.unrealized_pnl == Decimal("30.0")
    assert mapped.realized_pnl == Decimal("0.0")


def test_execution_window_formatter_uses_utc_ibkr_format():
    from marketpulse.broker.ibkr_client import _ibkr_execution_filter_time

    value = _ibkr_execution_filter_time(datetime(2026, 5, 23, 7, 0, tzinfo=UTC))

    assert value == "20260523 07:00:00"


def test_account_selection_requires_config_when_multiple_accounts_returned():
    from marketpulse.broker.ibkr_client import IbkrReadClient

    client = IbkrReadClient(
        host="127.0.0.1",
        port=7497,
        client_id=71,
        timeout_seconds=10,
        account_id="",
        broker_environment="paper",
        ib=object(),
    )

    try:
        client._select_account(("DU1", "DU2"))
    except RuntimeError as exc:
        assert "configure IBKR_ACCOUNT_ID" in str(exc)
    else:
        raise AssertionError("multiple returned accounts should fail closed")


def test_fetch_snapshot_uses_readonly_connection_without_mutating_api_surface():
    from marketpulse.broker.ibkr_client import IbkrReadClient

    fake_ib = FakeIB()
    assert not hasattr(fake_ib, "placeOrder")
    assert not hasattr(fake_ib, "cancelOrder")

    client = IbkrReadClient(
        host="127.0.0.1",
        port=7497,
        client_id=71,
        timeout_seconds=10,
        account_id="",
        broker_environment="paper",
        ib=fake_ib,
    )

    snapshot = client.fetch_snapshot()

    assert snapshot.account_id == "DU123"
    assert snapshot.account.base_currency == "EUR"
    assert fake_ib.connected_kwargs["readonly"] is True
    assert fake_ib.disconnected is True


def test_fetch_snapshot_disconnects_after_multiple_account_ambiguity():
    from marketpulse.broker.ibkr_client import IbkrReadClient

    fake_ib = MultiAccountFakeIB()
    client = IbkrReadClient(
        host="127.0.0.1",
        port=7497,
        client_id=71,
        timeout_seconds=10,
        account_id="",
        broker_environment="paper",
        ib=fake_ib,
    )

    try:
        client.fetch_snapshot()
    except RuntimeError as exc:
        assert "configure IBKR_ACCOUNT_ID" in str(exc)
    else:
        raise AssertionError("multiple returned accounts should fail closed")

    assert fake_ib.disconnected is True
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
uv run pytest tests/broker/test_ibkr_client_mapping.py -q
```

Expected: fail because `ibkr_client.py` does not exist.

- [ ] **Step 3: Implement adapter skeleton and helpers**

Create `marketpulse/broker/ibkr_client.py`:

```python
"""IBKR read-only adapter.

This is the only Phase 7a module allowed to import ib_insync.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ib_insync import ExecutionFilter, IB

from marketpulse.broker.types import (
    BrokerAccount,
    BrokerCash,
    BrokerEnvironment,
    BrokerExecution,
    BrokerOpenOrder,
    BrokerPosition,
    BrokerSnapshot,
)

IBKR_UNSET_DOUBLE = Decimal("1.7976931348623157E308")


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value)
    if text in {"", "nan", "NaN", "inf", "Infinity", "-inf", "-Infinity"}:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    decimal = Decimal(text)
    if not decimal.is_finite():
        return None
    if decimal.copy_abs() >= IBKR_UNSET_DOUBLE:
        return None
    return decimal


def _ibkr_execution_filter_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%d %H:%M:%S")


def _map_position(
    position: Any,
    *,
    market_price: Any = None,
    market_value: Any = None,
    unrealized_pnl: Any = None,
    realized_pnl: Any = None,
) -> BrokerPosition:
    contract = position.contract
    return BrokerPosition(
        account_id=str(position.account),
        symbol=str(getattr(contract, "symbol", "")),
        asset_class=getattr(contract, "secType", None),
        quantity=_decimal_or_none(position.position) or Decimal("0"),
        avg_cost=_decimal_or_none(position.avgCost),
        market_price=_decimal_or_none(market_price),
        market_value=_decimal_or_none(market_value),
        unrealized_pnl=_decimal_or_none(unrealized_pnl),
        realized_pnl=_decimal_or_none(realized_pnl),
    )


class IbkrReadClient:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        client_id: int,
        timeout_seconds: int,
        account_id: str = "",
        broker_environment: BrokerEnvironment = "unknown",
        execution_window_start: datetime | None = None,
        ib: IB | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.timeout_seconds = timeout_seconds
        self.account_id = account_id
        self.broker_environment = broker_environment
        self.execution_window_start = execution_window_start
        self._ib = ib or IB()

    def fetch_snapshot(self) -> BrokerSnapshot:
        captured_at = datetime.now(UTC)
        self._ib.connect(
            self.host,
            self.port,
            clientId=self.client_id,
            timeout=self.timeout_seconds,
            readonly=True,
        )
        try:
            accounts = tuple(self._ib.managedAccounts())
            account_id = self._select_account(accounts)
            account_values = self._ib.accountValues(account_id)
            account = self._map_account(account_id, account_values)
            cash = self._map_cash(account_id, account_values)
            positions = tuple(_map_position(p) for p in self._ib.positions() if p.account == account_id)
            open_orders = tuple(self._map_open_order(item, account_id) for item in self._ib.openTrades())
            executions = self._fetch_executions(account_id)
            return BrokerSnapshot(
                broker="IBKR",
                broker_environment=self.broker_environment,
                account_id=account_id,
                captured_at=captured_at,
                account=account,
                cash=cash,
                positions=positions,
                open_orders=open_orders,
                executions=executions,
            )
        finally:
            self._ib.disconnect()

    def _select_account(self, accounts: tuple[str, ...]) -> str:
        if self.account_id:
            if self.account_id not in accounts:
                raise RuntimeError(f"Configured account {self.account_id} not returned by IBKR")
            return self.account_id
        if len(accounts) == 1:
            return accounts[0]
        raise RuntimeError(f"IBKR returned {len(accounts)} accounts; configure IBKR_ACCOUNT_ID")

    def _map_account(self, account_id: str, values: list[Any]) -> BrokerAccount:
        by_tag = {(v.tag, v.currency): v.value for v in values}
        currencies = sorted({v.currency for v in values if v.currency})
        base_currency = (
            by_tag.get(("BaseCurrency", ""))
            or by_tag.get(("Currency", ""))
            or (currencies[0] if len(currencies) == 1 else None)
            or "USD"
        )
        return BrokerAccount(
            account_id=account_id,
            account_type=by_tag.get(("AccountType", "")),
            base_currency=base_currency,
            net_liquidation=_decimal_or_none(by_tag.get(("NetLiquidation", base_currency))),
            buying_power=_decimal_or_none(by_tag.get(("BuyingPower", base_currency))),
            maintenance_margin=_decimal_or_none(by_tag.get(("MaintMarginReq", base_currency))),
            excess_liquidity=_decimal_or_none(by_tag.get(("ExcessLiquidity", base_currency))),
        )

    def _map_cash(self, account_id: str, values: list[Any]) -> tuple[BrokerCash, ...]:
        by_tag = {(v.tag, v.currency): v.value for v in values}
        cash_tags = {"TotalCashBalance", "SettledCash", "AccruedCash"}
        currencies = sorted({v.currency for v in values if v.currency and v.tag in cash_tags})
        rows: list[BrokerCash] = []
        for currency in currencies:
            rows.append(BrokerCash(
                account_id=account_id,
                currency=currency,
                cash_balance=_decimal_or_none(by_tag.get(("TotalCashBalance", currency))),
                settled_cash=_decimal_or_none(by_tag.get(("SettledCash", currency))),
                accrued_interest=_decimal_or_none(by_tag.get(("AccruedCash", currency))),
            ))
        return tuple(rows)

    def _map_open_order(self, trade: Any, account_id: str) -> BrokerOpenOrder:
        order = trade.order
        contract = trade.contract
        status = getattr(trade.orderStatus, "status", None)
        return BrokerOpenOrder(
            account_id=account_id,
            broker_order_id=str(order.orderId),
            symbol=getattr(contract, "symbol", None),
            side=getattr(order, "action", None),
            order_type=getattr(order, "orderType", None),
            quantity=_decimal_or_none(getattr(order, "totalQuantity", None)),
            limit_price=_decimal_or_none(getattr(order, "lmtPrice", None)),
            status=status,
        )

    def _fetch_executions(self, account_id: str) -> tuple[BrokerExecution, ...]:
        filt = ExecutionFilter(acctCode=account_id)
        if self.execution_window_start is not None:
            filt.time = _ibkr_execution_filter_time(self.execution_window_start)
        rows = []
        for fill in self._ib.reqExecutions(filt):
            execution = fill.execution
            contract = fill.contract
            rows.append(BrokerExecution(
                account_id=account_id,
                broker_exec_id=str(execution.execId),
                broker_order_id=str(execution.orderId) if execution.orderId is not None else None,
                symbol=getattr(contract, "symbol", None),
                side=getattr(execution, "side", None),
                quantity=_decimal_or_none(getattr(execution, "shares", None)),
                price=_decimal_or_none(getattr(execution, "price", None)),
                executed_at=getattr(execution, "time", None),
            ))
        return tuple(rows)
```

`IB.connect(..., readonly=True)` is intentional. Before implementation, verify
the installed signature:

```bash
uv run python - <<'PY'
from ib_insync import IB
import inspect
print(inspect.signature(IB.connect))
PY
```

Expected: the signature includes `readonly: bool = False`. If it does not, remove
the `readonly=True` argument and rely on architecture guards as the hard
no-mutation boundary.

- [ ] **Step 4: Run adapter tests**

Run:

```bash
uv run pytest tests/broker/test_ibkr_client_mapping.py -q
uv run ruff check marketpulse/broker/ibkr_client.py tests/broker/test_ibkr_client_mapping.py
```

Expected: tests pass; ruff passes.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/broker/ibkr_client.py tests/broker/test_ibkr_client_mapping.py
git commit -m "feat: add ibkr readonly adapter"
```

---

## Task 7: CLI

**Files:**

- Create: `scripts/sync_ibkr_readonly.py`
- Test: `tests/broker/test_sync_cli.py`

- [ ] **Step 1: Write CLI tests**

Create `tests/broker/test_sync_cli.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FakeResult:
    sync_run_id: int = 123
    broker: str = "IBKR"
    broker_environment: str = "paper"
    account_id: str | None = "DU123"
    status: str = "completed"
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 71
    account_snapshots: int = 1
    cash_rows: int = 2
    positions: int = 5
    open_orders: int = 0
    executions: int = 3
    error_type: str | None = None
    error_message: str | None = None


def test_cli_prints_completed_summary(monkeypatch, capsys):
    from scripts import sync_ibkr_readonly as cli

    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "x" * 16)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(cli, "_run", lambda args: FakeResult())

    code = cli.main([])

    assert code == 0
    out = capsys.readouterr().out
    assert "sync_run_id: 123" in out
    assert "broker: IBKR" in out
    assert "broker_environment: paper" in out
    assert "account: DU123" in out
    assert "positions: 5" in out


def test_cli_prints_failed_summary(monkeypatch, capsys):
    from scripts import sync_ibkr_readonly as cli

    result = FakeResult(status="failed", account_id=None, error_type="ConnectionError",
                        error_message="down", account_snapshots=0, cash_rows=0,
                        positions=0, open_orders=0, executions=0)
    monkeypatch.setattr(cli, "_run", lambda args: result)

    code = cli.main([])

    assert code == 1
    out = capsys.readouterr().out
    assert "status: failed" in out
    assert "error_type: ConnectionError" in out
    assert "error_message: down" in out


def test_cli_config_prefers_args_over_settings(monkeypatch):
    from scripts import sync_ibkr_readonly as cli

    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "x" * 16)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("IBKR_HOST", "env-host")
    monkeypatch.setenv("IBKR_PORT", "7497")
    monkeypatch.setenv("IBKR_CLIENT_ID", "71")
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DUENV")
    monkeypatch.setenv("IBKR_CONNECT_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("MP_IBKR_ALLOW_LIVE", "false")

    args = cli.build_parser().parse_args([
        "--host", "arg-host",
        "--port", "7496",
        "--client-id", "72",
        "--account-id", "DUARG",
        "--timeout-seconds", "3",
        "--db-url", "sqlite:///arg.db",
    ])
    config, db_url = cli._config(args)

    assert config.host == "arg-host"
    assert config.port == 7496
    assert config.client_id == 72
    assert config.account_id == "DUARG"
    assert config.timeout_seconds == 3
    assert config.allow_live is False
    assert db_url == "sqlite:///arg.db"
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
uv run pytest tests/broker/test_sync_cli.py -q
```

Expected: fail because CLI does not exist.

- [ ] **Step 3: Implement CLI**

Create `scripts/sync_ibkr_readonly.py`:

```python
"""Run one IBKR read-only broker snapshot sync."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path[:0] = [str(Path(__file__).resolve().parent.parent)]

from marketpulse.broker.ibkr_client import IbkrReadClient  # noqa: E402
from marketpulse.broker.readonly_sync import IbkrSyncConfig, _execution_window, run_readonly_sync  # noqa: E402
from marketpulse.broker.types import SyncResult, classify_broker_environment  # noqa: E402
from marketpulse.config import get_settings  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--client-id", type=int)
    parser.add_argument("--account-id")
    parser.add_argument("--timeout-seconds", type=int)
    parser.add_argument("--db-url")
    return parser


def _config(args: argparse.Namespace) -> tuple[IbkrSyncConfig, str]:
    settings = get_settings()
    host = args.host or settings.ibkr_host
    port = args.port if args.port is not None else settings.ibkr_port
    client_id = args.client_id if args.client_id is not None else settings.ibkr_client_id
    account_id = args.account_id if args.account_id is not None else settings.ibkr_account_id
    timeout_seconds = (
        args.timeout_seconds
        if args.timeout_seconds is not None
        else settings.ibkr_connect_timeout_seconds
    )
    return (
        IbkrSyncConfig(
            host=host,
            port=port,
            client_id=client_id,
            account_id=account_id,
            timeout_seconds=timeout_seconds,
            allow_live=settings.ibkr_allow_live,
        ),
        args.db_url or settings.database_url,
    )


def _run(args: argparse.Namespace) -> SyncResult:
    config, db_url = _config(args)
    environment = classify_broker_environment(config.port)
    now = datetime.now(UTC)
    window_start, _ = _execution_window(now)
    client = IbkrReadClient(
        host=config.host,
        port=config.port,
        client_id=config.client_id,
        timeout_seconds=config.timeout_seconds,
        account_id=config.account_id,
        broker_environment=environment,
        execution_window_start=window_start,
    )
    engine = create_engine(db_url)
    with Session(engine) as session:
        result = run_readonly_sync(session, client=client, config=config, now=now)
        session.commit()
        return result


def _print_result(result: SyncResult) -> None:
    print(f"sync_run_id: {result.sync_run_id}")
    print(f"broker: {result.broker}")
    print(f"broker_environment: {result.broker_environment}")
    print(f"account: {result.account_id or 'unknown'}")
    print(f"host: {result.host}")
    print(f"port: {result.port}")
    print(f"client_id: {result.client_id}")
    print(f"status: {result.status}")
    if result.status == "completed":
        print(f"account snapshots: {result.account_snapshots}")
        print(f"cash rows: {result.cash_rows}")
        print(f"positions: {result.positions}")
        print(f"open orders: {result.open_orders}")
        print(f"executions: {result.executions}")
    else:
        print(f"error_type: {result.error_type}")
        print(f"error_message: {result.error_message}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = _run(args)
    _print_result(result)
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/broker/test_sync_cli.py -q
uv run ruff check scripts/sync_ibkr_readonly.py tests/broker/test_sync_cli.py
```

Expected: tests pass; ruff passes.

- [ ] **Step 5: Commit**

```bash
git add scripts/sync_ibkr_readonly.py tests/broker/test_sync_cli.py
git commit -m "feat: add ibkr readonly sync cli"
```

---

## Task 8: Architecture Guards

**Files:**

- Create: `tests/architecture/test_phase7a_ibkr_readonly_boundary.py`

- [ ] **Step 1: Write architecture guard tests**

Create `tests/architecture/test_phase7a_ibkr_readonly_boundary.py`:

```python
"""Architecture guards for Phase 7a IBKR read-only sync."""

from __future__ import annotations

import ast
from pathlib import Path

PROD = Path("marketpulse")
BROKER = PROD / "broker"
IBKR_CLIENT = BROKER / "ibkr_client.py"
TRADING = PROD / "trading"
SCHEDULER = PROD / "scheduler"

MUTATING_IBKR_APIS = (
    "placeOrder",
    "cancelOrder",
    "reqGlobalCancel",
    "exerciseOptions",
    "modifyOrder",
    "replaceOrder",
)


def _python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def test_only_ibkr_client_imports_ib_insync():
    offenders: list[str] = []
    for path in [*_python_files(PROD), *_python_files(Path("tests"))]:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == "ib_insync" for alias in node.names) and path != IBKR_CLIENT:
                    offenders.append(str(path))
            if isinstance(node, ast.ImportFrom):
                if node.module == "ib_insync" and path != IBKR_CLIENT:
                    offenders.append(str(path))
    assert sorted(set(offenders)) == []


def test_no_ibkr_mutating_api_names_in_production_or_scripts():
    offenders: list[str] = []
    for path in [*_python_files(PROD), Path("scripts/sync_ibkr_readonly.py")]:
        text = path.read_text()
        for needle in MUTATING_IBKR_APIS:
            if needle in text:
                offenders.append(f"{path}:{needle}")
    assert offenders == []


def test_trading_and_scheduler_do_not_import_broker_sync_modules():
    forbidden = (
        "marketpulse.broker.readonly_sync",
        "marketpulse.broker.repository",
        "marketpulse.broker.ibkr_client",
    )
    offenders: list[str] = []
    for root in (TRADING, SCHEDULER):
        for path in _python_files(root):
            text = path.read_text()
            for needle in forbidden:
                if needle in text:
                    offenders.append(f"{path}:{needle}")
    assert offenders == []


def test_phase7a_never_writes_paper_tables_by_name():
    offenders: list[str] = []
    for path in _python_files(BROKER) + [Path("scripts/sync_ibkr_readonly.py")]:
        text = path.read_text()
        for needle in ("PaperOrder", "PaperFill", "PaperPosition", "PaperCashLedger"):
            if needle in text:
                offenders.append(f"{path}:{needle}")
    assert offenders == []
```

- [ ] **Step 2: Run architecture tests**

Run:

```bash
uv run pytest tests/architecture/test_phase7a_ibkr_readonly_boundary.py -q
```

Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add tests/architecture/test_phase7a_ibkr_readonly_boundary.py
git commit -m "test: guard ibkr readonly boundaries"
```

---

## Task 9: Operations Runbook

**Files:**

- Create: `docs/operations/ibkr-readonly-sync-runbook.md`

- [ ] **Step 1: Write runbook**

Create `docs/operations/ibkr-readonly-sync-runbook.md`:

```markdown
# IBKR Read-Only Sync Runbook

Phase 7a captures IBKR broker truth into append-only `broker_*` snapshot tables.
It does not place, modify, cancel, reconcile, or drive paper trading state.

## Preconditions

- IBKR TWS or IB Gateway is running.
- Paper trading API access is enabled.
- Socket API port is reachable from the MarketPulse runtime.
- Default paper port is `7497`.
- Known live port `7496` is blocked unless `MP_IBKR_ALLOW_LIVE=true`.

## Environment

```bash
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=71
IBKR_ACCOUNT_ID=DUxxxxxxx
IBKR_CONNECT_TIMEOUT_SECONDS=10
MP_IBKR_ALLOW_LIVE=false
```

`IBKR_ACCOUNT_ID` is recommended. If it is unset and IBKR returns multiple
accounts, the sync fails closed and writes no snapshot rows.

## Manual Smoke

```bash
uv run python scripts/sync_ibkr_readonly.py
```

Successful output:

```text
sync_run_id: 123
broker: IBKR
broker_environment: paper
account: DUxxxxxxx
host: 127.0.0.1
port: 7497
client_id: 71
status: completed
account snapshots: 1
cash rows: 2
positions: 5
open orders: 0
executions: 3
```

Failed output:

```text
sync_run_id: 124
broker: IBKR
broker_environment: unknown
account: unknown
host: 127.0.0.1
port: 7497
client_id: 71
status: failed
error_type: ConnectionError
error_message: ...
```

A failed real IBKR smoke is a valid diagnostic outcome if it leaves a failed
`broker_sync_run` with `error_type`, `error_message`, and context.

## Inspect Latest Run

```bash
sqlite3 data/marketpulse.db \
  "select id, started_at, completed_at, broker_environment, account_id, status, error_type, error_message from broker_sync_run order by id desc limit 5;"
```

## Interrupted Runs

`broker_sync_run(status='started')` that remains started long after the CLI
exited means the process was interrupted before it could mark completed or
failed. Do not edit the row manually. Capture logs and rerun the CLI.

## Execution Snapshot Semantics

7a executions are best-effort rows for the configured execution window
(NY trading-day midnight through sync capture time). They are not a complete
historical execution archive.

## What 7a Never Does

- No order placement.
- No order modification.
- No order cancellation.
- No scheduler or daemon.
- No web-triggered sync.
- No writes to `paper_*`.
- No paper-vs-broker reconciliation.
```

- [ ] **Step 2: Commit**

```bash
git add docs/operations/ibkr-readonly-sync-runbook.md
git commit -m "docs: add ibkr readonly sync runbook"
```

---

## Task 10: Final Verification

**Files:**

- No new files unless fixing issues found by verification.

- [ ] **Step 1: Run focused tests**

Run:

```bash
uv run pytest tests/broker tests/architecture/test_phase7a_ibkr_readonly_boundary.py tests/migration/test_0012_broker_snapshots.py tests/trading/test_models.py::test_broker_snapshot_models_have_expected_tablenames tests/trading/test_models.py::test_broker_snapshot_models_use_decimal_numeric_columns tests/trading/test_models.py::test_broker_snapshot_rows_have_account_and_capture_columns tests/unit/test_config.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run full pytest**

Run:

```bash
uv run pytest
```

Expected: full suite passes.

- [ ] **Step 3: Run ruff**

Run:

```bash
uv run ruff check .
```

Expected: no lint violations.

- [ ] **Step 4: Verify Alembic single head**

Run:

```bash
uv run alembic heads
```

Expected:

```text
0012 (head)
```

- [ ] **Step 5: Verify downgrade smoke on temp DB**

Run:

```bash
tmpdb="$(mktemp -t marketpulse-7a.XXXXXX.db)"
DATABASE_URL="sqlite:///$tmpdb" uv run alembic upgrade head
DATABASE_URL="sqlite:///$tmpdb" uv run alembic downgrade 0011
rm -f "$tmpdb"
```

Expected: all commands exit 0.

- [ ] **Step 6: Do not run real IBKR smoke in CI/dev unless operator confirms IBKR Gateway/TWS is available**

Manual command when the operator is ready:

```bash
uv run python scripts/sync_ibkr_readonly.py
```

Expected:

- completed run if IBKR paper Gateway/TWS is reachable and account selection succeeds;
- failed run with structured error details if connection/config/account selection fails.

- [ ] **Step 7: Commit verification fixes if any**

If verification required fixes, stage the exact files changed by those fixes and commit:

```bash
git status --short
git add marketpulse/broker scripts tests docs pyproject.toml uv.lock alembic/versions/0012_phase7a_broker_snapshots.py
git commit -m "fix: stabilize ibkr readonly sync verification"
```

If no fixes were needed, no commit is required.

---

## Implementation Notes

- Keep `marketpulse/broker/ibkr_client.py` as the only `ib_insync` import site.
- Keep tests fake-client based; never require a real IBKR connection in pytest.
- `broker_sync_run` may remain `started` after a crash; do not implement automatic repair in 7a.
- Use `Decimal(str(value))` when mapping IBKR numeric values; never persist broker numerics through Python float formatting.
- Do not add UI, routes, scheduler calls, background loops, or paper state writes.
