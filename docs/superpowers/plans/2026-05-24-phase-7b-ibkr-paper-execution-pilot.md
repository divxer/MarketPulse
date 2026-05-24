# Phase 7b IBKR Paper Execution Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a manual, paper-account-only IBKR order execution pilot that can place, observe, and cancel a controlled STK LMT order with local provenance and without touching `paper_*` state.

**Architecture:** Add a dedicated broker order command layer beside the existing 7a Flex read-only layer. Persist operator commands into `broker_order_intent` and broker/safety observations into append-only `broker_order_event`; keep `ibapi` isolated inside `marketpulse/broker/ibkr_order_client.py`; expose only one CLI with `place`, `status`, and `cancel` subcommands. The service uses MarketPulse DTOs/Protocols and fake clients in automated tests; real TWS/Gateway is manual smoke only.

**Tech Stack:** Python 3.12, SQLAlchemy 2, Alembic, pytest, argparse, official IBKR `ibapi`, existing `marketpulse.broker` package.

---

## File Structure

Create:
- `alembic/versions/0013_phase7b_broker_order_pilot.py` — creates/drops `broker_order_intent` and `broker_order_event` with CHECK/UNIQUE constraints.
- `marketpulse/broker/order_types.py` — immutable DTOs/enums/errors for 7b order pilot.
- `marketpulse/broker/order_client.py` — `BrokerOrderClient` Protocol.
- `marketpulse/broker/order_repository.py` — persistence helpers for intents/events and status transitions.
- `marketpulse/broker/order_service.py` — safety validation and orchestration for place/status/cancel.
- `marketpulse/broker/ibkr_order_client.py` — only module importing `ibapi`; adapter normalizes callbacks into DTOs.
- `scripts/ibkr_paper_order.py` — single manual CLI with `place`, `status`, `cancel`.
- `docs/operations/ibkr-paper-order-pilot-runbook.md` — manual smoke/runbook.
- `tests/migration/test_0013_broker_order_pilot.py`
- `tests/broker/test_order_types.py`
- `tests/broker/test_order_repository.py`
- `tests/broker/test_order_service_place.py`
- `tests/broker/test_order_service_status_cancel.py`
- `tests/broker/test_ibkr_order_client.py`
- `tests/broker/test_paper_order_cli.py`
- `tests/architecture/test_phase7b_order_boundary.py`

Modify:
- `pyproject.toml` — add `ibapi` dependency.
- `marketpulse/config.py` — add 7b order connection/timeout settings.
- `marketpulse/db/models.py` — add ORM models for `BrokerOrderIntent` and `BrokerOrderEvent`.
- `marketpulse/broker/__init__.py` — keep empty/no side effects; do not import `ibapi`.
- `docs/superpowers/specs/2026-05-24-phase-7b-ibkr-paper-execution-pilot-design.md` — already committed; do not rewrite unless implementation reveals a contradiction.

Do not modify:
- `paper_order`, `paper_fill`, `paper_position`, `paper_cash_ledger` semantics.
- scheduler, `marketpulse/trading/daily_cycle.py`, web routes, strategy allocation flows.
- 7a Flex read-only sync behavior.

---

## Task 1: Dependency, Settings, DTOs, and Protocol

**Files:**
- Modify: `pyproject.toml`
- Modify: `marketpulse/config.py`
- Create: `marketpulse/broker/order_types.py`
- Create: `marketpulse/broker/order_client.py`
- Test: `tests/broker/test_order_types.py`

- [ ] **Step 1: Write failing DTO/settings tests**

Create `tests/broker/test_order_types.py`:

```python
from __future__ import annotations

from decimal import Decimal

import pytest

from marketpulse.broker.order_types import (
    BrokerOrderRequest,
    build_order_ref,
    classify_order_account,
)
from marketpulse.config import Settings


def test_classify_order_account_accepts_only_du_paper():
    assert classify_order_account("DU123456") == "paper"
    assert classify_order_account("DUE411848") == "paper"
    assert classify_order_account("U123456") == "live"
    assert classify_order_account("ABC123") == "unknown"
    assert classify_order_account("") == "unknown"


def test_order_ref_is_short_and_contains_intent():
    ref = build_order_ref(intent_id=123, local_idempotency_key="abcdef1234567890")
    assert ref == "MP-7B-123-abcdef12"
    assert len(ref) <= 32


def test_order_ref_rejects_too_long_intent_id():
    with pytest.raises(ValueError, match="orderRef exceeds"):
        build_order_ref(intent_id=12345678901234567890, local_idempotency_key="abcdef123456")


def test_order_ref_rejects_empty_sanitized_key():
    with pytest.raises(ValueError, match="alphanumeric"):
        build_order_ref(intent_id=123, local_idempotency_key="---___")


def test_broker_order_request_rejects_non_mvp_order_shape():
    with pytest.raises(ValueError, match="STK"):
        BrokerOrderRequest(
            account_id="DU123456",
            symbol="AAPL",
            asset_class="OPT",
            side="BUY",
            quantity=Decimal("1"),
            order_type="LMT",
            limit_price=Decimal("1.00"),
            transmit=False,
            local_idempotency_key="key-1",
        )
    with pytest.raises(ValueError, match="LMT"):
        BrokerOrderRequest(
            account_id="DU123456",
            symbol="AAPL",
            asset_class="STK",
            side="BUY",
            quantity=Decimal("1"),
            order_type="MKT",
            limit_price=Decimal("1.00"),
            transmit=False,
            local_idempotency_key="key-2",
        )
    with pytest.raises(ValueError, match="limit_price"):
        BrokerOrderRequest(
            account_id="DU123456",
            symbol="AAPL",
            asset_class="STK",
            side="BUY",
            quantity=Decimal("1"),
            order_type="LMT",
            limit_price=None,
            transmit=False,
            local_idempotency_key="key-3",
        )
    with pytest.raises(ValueError, match="limit_price must be positive"):
        BrokerOrderRequest(
            account_id="DU123456",
            symbol="AAPL",
            asset_class="STK",
            side="BUY",
            quantity=Decimal("1"),
            order_type="LMT",
            limit_price=Decimal("0"),
            transmit=False,
            local_idempotency_key="key-4",
        )


def test_settings_have_order_defaults(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "x" * 16)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    settings = Settings()
    assert settings.ibkr_order_host == "127.0.0.1"
    assert settings.ibkr_order_port == 7497
    assert settings.ibkr_order_client_id == 72
    assert settings.ibkr_order_connect_timeout_seconds == 10
    assert settings.ibkr_order_next_valid_id_timeout_seconds == 10
    assert settings.ibkr_order_observation_timeout_seconds == 15
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/broker/test_order_types.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'marketpulse.broker.order_types'` or missing settings attributes.

- [ ] **Step 3: Add dependency and settings**

Modify `pyproject.toml` dependencies:

```toml
    "ibapi>=9.81",
```

Modify `marketpulse/config.py` after the 7a Flex settings:

```python
    # Phase 7b IBKR paper order pilot via TWS / IB Gateway.
    ibkr_order_host: str = Field("127.0.0.1", alias="IBKR_ORDER_HOST")
    ibkr_order_port: int = Field(7497, alias="IBKR_ORDER_PORT", ge=0)
    ibkr_order_client_id: int = Field(72, alias="IBKR_ORDER_CLIENT_ID", ge=0)
    ibkr_order_connect_timeout_seconds: int = Field(
        10,
        alias="IBKR_ORDER_CONNECT_TIMEOUT_SECONDS",
        ge=1,
    )
    ibkr_order_next_valid_id_timeout_seconds: int = Field(
        10,
        alias="IBKR_ORDER_NEXT_VALID_ID_TIMEOUT_SECONDS",
        ge=1,
    )
    ibkr_order_observation_timeout_seconds: int = Field(
        15,
        alias="IBKR_ORDER_OBSERVATION_TIMEOUT_SECONDS",
        ge=1,
    )
```

- [ ] **Step 4: Implement DTOs and Protocol**

Create `marketpulse/broker/order_types.py`:

```python
"""DTOs for Phase 7b IBKR paper order pilot."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

BrokerOrderAction = Literal["place", "cancel", "status_check"]
BrokerOrderIntentStatus = Literal["created", "sent", "completed", "rejected", "failed"]
BrokerOrderEventSource = Literal["adapter_callback", "service_safety", "cli_validation", "timeout"]
BrokerOrderEventType = Literal[
    "safety_rejected",
    "connection_failed",
    "account_mismatch",
    "next_valid_id_received",
    "staged_to_tws",
    "submitted_to_broker",
    "open_order_seen",
    "order_status_seen",
    "broker_cancel_requested",
    "staged_cancelled",
    "cancelled",
    "filled",
    "rejected",
    "error",
]
OrderAccountEnvironment = Literal["paper", "live", "unknown"]

_PAPER_RE = re.compile(r"^DU[A-Z]*\d+$")
_LIVE_RE = re.compile(r"^U\d+$")


class BrokerOrderSafetyError(RuntimeError):
    """Order pilot refused before broker mutation for safety/config reasons."""


class BrokerOrderConnectionError(RuntimeError):
    """TWS/Gateway connection or session validation failed."""


class BrokerOrderTimeoutError(RuntimeError):
    """Expected broker callback did not arrive before the bounded timeout."""


def classify_order_account(account_id: str | None) -> OrderAccountEnvironment:
    if not account_id:
        return "unknown"
    if _PAPER_RE.match(account_id):
        return "paper"
    if _LIVE_RE.match(account_id):
        return "live"
    return "unknown"


def build_order_ref(*, intent_id: int, local_idempotency_key: str) -> str:
    short_key = re.sub(r"[^A-Za-z0-9]", "", local_idempotency_key)[:8]
    if not short_key:
        raise ValueError("local_idempotency_key must contain at least one alphanumeric character")
    ref = f"MP-7B-{intent_id}-{short_key}"
    if len(ref) > 32:
        raise ValueError(f"orderRef exceeds 32 characters: {ref}")
    return ref


@dataclass(frozen=True)
class BrokerOrderRequest:
    account_id: str
    symbol: str
    asset_class: str
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    order_type: str
    limit_price: Decimal | None
    transmit: bool
    local_idempotency_key: str

    def __post_init__(self) -> None:
        if self.asset_class != "STK":
            raise ValueError("7b MVP supports asset_class=STK only")
        if self.order_type != "LMT":
            raise ValueError("7b MVP supports order_type=LMT only")
        if self.limit_price is None:
            raise ValueError("limit_price is required for 7b LMT orders")
        if self.limit_price <= 0:
            raise ValueError("limit_price must be positive")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")


@dataclass(frozen=True)
class BrokerOrderObservation:
    event_type: BrokerOrderEventType
    event_source: BrokerOrderEventSource
    observed_at: datetime
    broker_order_id: str | None = None
    broker_perm_id: str | None = None
    broker_status: str | None = None
    filled_quantity: Decimal | None = None
    remaining_quantity: Decimal | None = None
    avg_fill_price: Decimal | None = None
    message: str | None = None
    raw: dict | None = None


@dataclass(frozen=True)
class PlaceOrderResult:
    broker_order_id: str
    order_ref: str
    observations: tuple[BrokerOrderObservation, ...]


@dataclass(frozen=True)
class OrderStatusResult:
    observations: tuple[BrokerOrderObservation, ...]


@dataclass(frozen=True)
class CancelOrderResult:
    observations: tuple[BrokerOrderObservation, ...]
```

`event_source="cli_validation"` is reserved for future validation paths that
create an intent before rejecting CLI input. In 7b MVP, argparse/dataclass
validation can exit before intent creation, so most safety events use
`service_safety`.

Create `marketpulse/broker/order_client.py`:

```python
"""Read/write client Protocol for Phase 7b manual paper order pilot."""

from __future__ import annotations

from typing import Protocol

from marketpulse.broker.order_types import (
    BrokerOrderRequest,
    CancelOrderResult,
    OrderStatusResult,
    PlaceOrderResult,
)


class BrokerOrderClient(Protocol):
    def place_lmt_order(
        self,
        *,
        request: BrokerOrderRequest,
        intent_id: int,
        order_ref: str,
    ) -> PlaceOrderResult: ...

    def fetch_order_status(
        self,
        *,
        account_id: str,
        broker_order_id: str,
    ) -> OrderStatusResult: ...

    def cancel_order(
        self,
        *,
        account_id: str,
        broker_order_id: str,
        staged: bool,
    ) -> CancelOrderResult: ...
```

- [ ] **Step 5: Run tests and lockfile update**

Run:

```bash
uv lock
uv run pytest tests/broker/test_order_types.py -q
```

Expected: all tests in `test_order_types.py` pass and `uv.lock` updates for `ibapi`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock marketpulse/config.py marketpulse/broker/order_types.py marketpulse/broker/order_client.py tests/broker/test_order_types.py
git commit -m "feat(7b): add broker order DTOs and settings"
```

---

## Task 2: Migration and ORM Models

**Files:**
- Create: `alembic/versions/0013_phase7b_broker_order_pilot.py`
- Modify: `marketpulse/db/models.py`
- Test: `tests/migration/test_0013_broker_order_pilot.py`

- [ ] **Step 1: Write migration tests**

Create `tests/migration/test_0013_broker_order_pilot.py`:

```python
# Layer: stateful
from __future__ import annotations

import subprocess

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text

BROKER_ORDER_TABLES = {"broker_order_intent", "broker_order_event"}


def _upgrade(db_url: str, monkeypatch: pytest.MonkeyPatch) -> sa.Engine:
    monkeypatch.setenv("DATABASE_URL", db_url)
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)
    return create_engine(db_url)


def test_0013_upgrade_creates_broker_order_tables(tmp_path, monkeypatch):
    engine = _upgrade(f"sqlite:///{tmp_path / 'broker_order.db'}", monkeypatch)

    tables = set(inspect(engine).get_table_names())
    assert tables >= BROKER_ORDER_TABLES

    intent_cols = {col["name"] for col in inspect(engine).get_columns("broker_order_intent")}
    event_cols = {col["name"] for col in inspect(engine).get_columns("broker_order_event")}
    assert {
        "id",
        "created_at",
        "operator_source",
        "action",
        "broker",
        "broker_environment",
        "account_id",
        "symbol",
        "asset_class",
        "side",
        "quantity",
        "order_type",
        "limit_price",
        "transmit",
        "local_idempotency_key",
        "parent_intent_id",
        "broker_order_id",
        "broker_perm_id",
        "status",
        "context",
    } <= intent_cols
    assert {
        "id",
        "intent_id",
        "observed_at",
        "event_type",
        "event_source",
        "broker_order_id",
        "broker_perm_id",
        "broker_status",
        "filled_quantity",
        "remaining_quantity",
        "avg_fill_price",
        "message",
        "raw",
    } <= event_cols


def test_0013_constraints_reject_invalid_status_event_type_and_duplicate_key(tmp_path, monkeypatch):
    engine = _upgrade(f"sqlite:///{tmp_path / 'constraints.db'}", monkeypatch)

    insert_intent = """
        INSERT INTO broker_order_intent
        (id, created_at, operator_source, action, broker, broker_environment, account_id,
         symbol, asset_class, side, quantity, order_type, limit_price, transmit,
         local_idempotency_key, parent_intent_id, broker_order_id, broker_perm_id,
         status, context)
        VALUES
        (:id, '2026-05-24 00:00:00', 'cli', 'place', 'IBKR', 'paper', 'DU123',
         'AAPL', 'STK', 'BUY', 1, 'LMT', 1.0, 0,
         :key, NULL, NULL, NULL, 'created', '{}')
    """
    with engine.begin() as conn:
        conn.execute(text(insert_intent), {"id": 1, "key": "same-key"})

    with engine.connect() as conn:
        trans = conn.begin()
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO broker_order_intent
                    (created_at, operator_source, action, broker, broker_environment, account_id,
                     symbol, asset_class, side, quantity, order_type, limit_price, transmit,
                     local_idempotency_key, parent_intent_id, broker_order_id, broker_perm_id,
                     status, context)
                    VALUES
                    ('2026-05-24 00:00:01', 'cli', 'place', 'IBKR', 'paper', 'DU123',
                     'AAPL', 'STK', 'BUY', 1, 'LMT', 1.0, 0,
                     'same-key', NULL, NULL, NULL, 'created', '{}')
                    """
                )
            )
        trans.rollback()

    with engine.connect() as conn:
        trans = conn.begin()
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(text("UPDATE broker_order_intent SET status='broker_submitted' WHERE id=1"))
        trans.rollback()

    with engine.connect() as conn:
        trans = conn.begin()
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO broker_order_event
                    (intent_id, observed_at, event_type, event_source, raw)
                    VALUES (1, '2026-05-24 00:00:02', 'Submitted', 'adapter_callback', '{}')
                    """
                )
            )
        trans.rollback()

    with engine.connect() as conn:
        trans = conn.begin()
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO broker_order_event
                    (intent_id, observed_at, event_type, event_source, raw)
                    VALUES (1, '2026-05-24 00:00:03', 'error', 'operator_guess', '{}')
                    """
                )
            )
        trans.rollback()


def test_0013_downgrade_drops_broker_order_tables_only(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'downgrade.db'}"
    _upgrade(db_url, monkeypatch)
    subprocess.run(["uv", "run", "alembic", "downgrade", "0012"], check=True)

    engine = create_engine(db_url)
    tables = set(inspect(engine).get_table_names())
    assert BROKER_ORDER_TABLES.isdisjoint(tables)
    assert "broker_sync_run" in tables
    assert "paper_order" in tables
```

- [ ] **Step 2: Run migration tests to verify failure**

Run:

```bash
uv run pytest tests/migration/test_0013_broker_order_pilot.py -q
```

Expected: fail because migration `0013` and ORM models do not exist.

- [ ] **Step 3: Add migration**

Create `alembic/versions/0013_phase7b_broker_order_pilot.py`:

```python
"""Phase 7b broker order pilot tables.

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-24
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INTENT_STATUS = "'created', 'sent', 'completed', 'rejected', 'failed'"
EVENT_TYPES = (
    "'safety_rejected', 'connection_failed', 'account_mismatch', "
    "'next_valid_id_received', 'staged_to_tws', 'submitted_to_broker', "
    "'open_order_seen', 'order_status_seen', 'broker_cancel_requested', "
    "'staged_cancelled', 'cancelled', 'filled', 'rejected', 'error'"
)
EVENT_SOURCES = "'adapter_callback', 'service_safety', 'cli_validation', 'timeout'"


def upgrade() -> None:
    op.create_table(
        "broker_order_intent",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("operator_source", sa.String(16), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("broker", sa.String(16), nullable=False),
        sa.Column("broker_environment", sa.String(16), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("asset_class", sa.String(16), nullable=True),
        sa.Column("side", sa.String(8), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("order_type", sa.String(16), nullable=True),
        sa.Column("limit_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("transmit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("local_idempotency_key", sa.String(64), nullable=False),
        sa.Column("parent_intent_id", sa.Integer(), nullable=True),
        sa.Column("broker_order_id", sa.String(64), nullable=True),
        sa.Column("broker_perm_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["parent_intent_id"], ["broker_order_intent.id"]),
        sa.CheckConstraint("operator_source IN ('cli')", name="ck_broker_order_intent_source"),
        sa.CheckConstraint("action IN ('place', 'cancel', 'status_check')", name="ck_broker_order_intent_action"),
        sa.CheckConstraint("broker IN ('IBKR')", name="ck_broker_order_intent_broker"),
        sa.CheckConstraint("broker_environment IN ('paper')", name="ck_broker_order_intent_env"),
        sa.CheckConstraint("asset_class IS NULL OR asset_class IN ('STK')", name="ck_broker_order_intent_asset_class"),
        sa.CheckConstraint("side IS NULL OR side IN ('BUY', 'SELL')", name="ck_broker_order_intent_side"),
        sa.CheckConstraint("order_type IS NULL OR order_type IN ('LMT')", name="ck_broker_order_intent_order_type"),
        sa.CheckConstraint("quantity IS NULL OR quantity > 0", name="ck_broker_order_intent_quantity"),
        sa.CheckConstraint("limit_price IS NULL OR limit_price > 0", name="ck_broker_order_intent_limit_price"),
        sa.CheckConstraint(f"status IN ({INTENT_STATUS})", name="ck_broker_order_intent_status"),
        sa.UniqueConstraint(
            "account_id",
            "action",
            "local_idempotency_key",
            name="uq_broker_order_intent_account_action_key",
        ),
    )
    op.create_index("ix_broker_order_intent_created", "broker_order_intent", ["created_at"])
    op.create_index(
        "ix_broker_order_intent_parent",
        "broker_order_intent",
        ["parent_intent_id"],
    )
    op.create_index(
        "ix_broker_order_intent_broker_order",
        "broker_order_intent",
        ["account_id", "broker_order_id"],
    )

    op.create_table(
        "broker_order_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("intent_id", sa.Integer(), sa.ForeignKey("broker_order_intent.id"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("event_source", sa.String(32), nullable=False),
        sa.Column("broker_order_id", sa.String(64), nullable=True),
        sa.Column("broker_perm_id", sa.String(64), nullable=True),
        sa.Column("broker_status", sa.String(64), nullable=True),
        sa.Column("filled_quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("remaining_quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("avg_fill_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=False, server_default="{}"),
        sa.CheckConstraint(f"event_type IN ({EVENT_TYPES})", name="ck_broker_order_event_type"),
        sa.CheckConstraint(f"event_source IN ({EVENT_SOURCES})", name="ck_broker_order_event_source"),
    )
    op.create_index("ix_broker_order_event_intent", "broker_order_event", ["intent_id"])
    op.create_index("ix_broker_order_event_observed", "broker_order_event", ["observed_at"])


def downgrade() -> None:
    op.drop_index("ix_broker_order_event_observed", table_name="broker_order_event")
    op.drop_index("ix_broker_order_event_intent", table_name="broker_order_event")
    op.drop_table("broker_order_event")

    op.drop_index("ix_broker_order_intent_broker_order", table_name="broker_order_intent")
    op.drop_index("ix_broker_order_intent_parent", table_name="broker_order_intent")
    op.drop_index("ix_broker_order_intent_created", table_name="broker_order_intent")
    op.drop_table("broker_order_intent")
```

- [ ] **Step 4: Add ORM models**

Append to `marketpulse/db/models.py` after `BrokerExecutionSnapshot`:

```python
class BrokerOrderIntent(Base):
    __tablename__ = "broker_order_intent"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    operator_source: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    broker: Mapped[str] = mapped_column(String(16), nullable=False)
    broker_environment: Mapped[str] = mapped_column(String(16), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    asset_class: Mapped[str | None] = mapped_column(String(16), nullable=True)
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    quantity: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    order_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    limit_price: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    transmit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    local_idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_intent_id: Mapped[int | None] = mapped_column(
        ForeignKey("broker_order_intent.id"),
        nullable=True,
    )
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    broker_perm_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_broker_order_intent_created", "created_at"),
        Index("ix_broker_order_intent_parent", "parent_intent_id"),
        Index("ix_broker_order_intent_broker_order", "account_id", "broker_order_id"),
        UniqueConstraint(
            "account_id",
            "action",
            "local_idempotency_key",
            name="uq_broker_order_intent_account_action_key",
        ),
        CheckConstraint("operator_source IN ('cli')", name="ck_broker_order_intent_source"),
        CheckConstraint(
            "action IN ('place', 'cancel', 'status_check')",
            name="ck_broker_order_intent_action",
        ),
        CheckConstraint("broker IN ('IBKR')", name="ck_broker_order_intent_broker"),
        CheckConstraint("broker_environment IN ('paper')", name="ck_broker_order_intent_env"),
        CheckConstraint(
            "asset_class IS NULL OR asset_class IN ('STK')",
            name="ck_broker_order_intent_asset_class",
        ),
        CheckConstraint("side IS NULL OR side IN ('BUY', 'SELL')", name="ck_broker_order_intent_side"),
        CheckConstraint(
            "order_type IS NULL OR order_type IN ('LMT')",
            name="ck_broker_order_intent_order_type",
        ),
        CheckConstraint("quantity IS NULL OR quantity > 0", name="ck_broker_order_intent_quantity"),
        CheckConstraint(
            "limit_price IS NULL OR limit_price > 0",
            name="ck_broker_order_intent_limit_price",
        ),
        CheckConstraint(
            "status IN ('created', 'sent', 'completed', 'rejected', 'failed')",
            name="ck_broker_order_intent_status",
        ),
    )


class BrokerOrderEvent(Base):
    __tablename__ = "broker_order_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    intent_id: Mapped[int] = mapped_column(ForeignKey("broker_order_intent.id"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_source: Mapped[str] = mapped_column(String(32), nullable=False)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    broker_perm_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    broker_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    filled_quantity: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    remaining_quantity: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    avg_fill_price: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_broker_order_event_intent", "intent_id"),
        Index("ix_broker_order_event_observed", "observed_at"),
        CheckConstraint(
            "event_type IN ("
            "'safety_rejected', 'connection_failed', 'account_mismatch', "
            "'next_valid_id_received', 'staged_to_tws', 'submitted_to_broker', "
            "'open_order_seen', 'order_status_seen', 'broker_cancel_requested', "
            "'staged_cancelled', 'cancelled', 'filled', 'rejected', 'error'"
            ")",
            name="ck_broker_order_event_type",
        ),
        CheckConstraint(
            "event_source IN ('adapter_callback', 'service_safety', 'cli_validation', 'timeout')",
            name="ck_broker_order_event_source",
        ),
    )
```

- [ ] **Step 5: Run migration tests**

Run:

```bash
uv run pytest tests/migration/test_0013_broker_order_pilot.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0013_phase7b_broker_order_pilot.py marketpulse/db/models.py tests/migration/test_0013_broker_order_pilot.py
git commit -m "feat(7b): add broker order provenance tables"
```

---

## Task 3: Order Repository

**Files:**
- Create: `marketpulse/broker/order_repository.py`
- Test: `tests/broker/test_order_repository.py`

- [ ] **Step 1: Write failing repository tests**

Create `tests/broker/test_order_repository.py`:

```python
# Layer: stateful
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marketpulse.broker.order_types import BrokerOrderObservation, BrokerOrderRequest
from marketpulse.db.base import Base
from marketpulse.db.models import (
    BrokerOrderEvent,
    BrokerOrderIntent,
    PaperCashLedger,
    PaperFill,
    PaperOrder,
    PaperPosition,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _count(session: Session, model) -> int:
    return int(session.execute(select(func.count(model.id))).scalar() or 0)


def _request(key: str = "key-1") -> BrokerOrderRequest:
    return BrokerOrderRequest(
        account_id="DU123456",
        symbol="AAPL",
        asset_class="STK",
        side="BUY",
        quantity=Decimal("1"),
        order_type="LMT",
        limit_price=Decimal("1.00"),
        transmit=False,
        local_idempotency_key=key,
    )


def test_create_place_intent_before_event_and_update_status():
    from marketpulse.broker.order_repository import (
        append_event,
        create_place_intent,
        mark_intent_sent,
    )

    session = _session()
    created_at = datetime(2026, 5, 24, 12, tzinfo=UTC)
    intent = create_place_intent(
        session,
        request=_request(),
        created_at=created_at,
        context={"cli": "place"},
    )
    assert intent.id is not None
    assert intent.status == "created"

    mark_intent_sent(session, intent_id=intent.id, broker_order_id="1001", broker_perm_id=None)
    append_event(
        session,
        intent_id=intent.id,
        observation=BrokerOrderObservation(
            event_type="staged_to_tws",
            event_source="adapter_callback",
            observed_at=created_at,
            broker_order_id="1001",
            broker_status="PreSubmitted",
            raw={"status": "PreSubmitted"},
        ),
    )
    session.commit()

    saved = session.get(BrokerOrderIntent, intent.id)
    assert saved is not None
    assert saved.status == "sent"
    assert saved.broker_order_id == "1001"
    assert _count(session, BrokerOrderEvent) == 1


def test_duplicate_place_idempotency_key_rejected_by_db():
    from marketpulse.broker.order_repository import create_place_intent

    session = _session()
    now = datetime(2026, 5, 24, 12, tzinfo=UTC)
    create_place_intent(session, request=_request("same"), created_at=now, context={})
    with pytest.raises(IntegrityError):
        create_place_intent(session, request=_request("same"), created_at=now, context={})
    session.rollback()


def test_child_intents_reference_parent_and_do_not_touch_paper_tables():
    from marketpulse.broker.order_repository import (
        create_cancel_intent,
        create_place_intent,
        create_status_intent,
        mark_intent_sent,
    )

    session = _session()
    now = datetime(2026, 5, 24, 12, tzinfo=UTC)
    before = {
        "paper_order": _count(session, PaperOrder),
        "paper_fill": _count(session, PaperFill),
        "paper_position": _count(session, PaperPosition),
        "paper_cash_ledger": _count(session, PaperCashLedger),
    }

    parent = create_place_intent(session, request=_request("parent"), created_at=now, context={})
    mark_intent_sent(session, intent_id=parent.id, broker_order_id="1001", broker_perm_id=None)
    status = create_status_intent(
        session,
        parent_intent=parent,
        created_at=now,
        local_idempotency_key="status-key",
        context={},
    )
    cancel = create_cancel_intent(
        session,
        parent_intent=parent,
        created_at=now,
        local_idempotency_key="cancel-key",
        context={},
    )
    session.commit()

    assert status.parent_intent_id == parent.id
    assert cancel.parent_intent_id == parent.id
    after = {
        "paper_order": _count(session, PaperOrder),
        "paper_fill": _count(session, PaperFill),
        "paper_position": _count(session, PaperPosition),
        "paper_cash_ledger": _count(session, PaperCashLedger),
    }
    assert after == before
```

- [ ] **Step 2: Run repository tests to verify failure**

Run:

```bash
uv run pytest tests/broker/test_order_repository.py -q
```

Expected: fail because `order_repository.py` does not exist.

- [ ] **Step 3: Implement repository helpers**

Create `marketpulse/broker/order_repository.py`:

```python
"""Persistence helpers for Phase 7b broker order provenance."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from marketpulse.broker.order_types import BrokerOrderObservation, BrokerOrderRequest
from marketpulse.db.models import BrokerOrderEvent, BrokerOrderIntent


def _new_child_key(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def create_place_intent(
    session: Session,
    *,
    request: BrokerOrderRequest,
    created_at: datetime,
    context: dict,
) -> BrokerOrderIntent:
    intent = BrokerOrderIntent(
        created_at=created_at,
        operator_source="cli",
        action="place",
        broker="IBKR",
        broker_environment="paper",
        account_id=request.account_id,
        symbol=request.symbol,
        asset_class=request.asset_class,
        side=request.side,
        quantity=request.quantity,
        order_type=request.order_type,
        limit_price=request.limit_price,
        transmit=request.transmit,
        local_idempotency_key=request.local_idempotency_key,
        parent_intent_id=None,
        broker_order_id=None,
        broker_perm_id=None,
        status="created",
        context=context,
    )
    session.add(intent)
    session.flush()
    return intent


def create_status_intent(
    session: Session,
    *,
    parent_intent: BrokerOrderIntent,
    created_at: datetime,
    local_idempotency_key: str | None = None,
    context: dict,
) -> BrokerOrderIntent:
    intent = _create_child_intent(
        session,
        parent_intent=parent_intent,
        created_at=created_at,
        action="status_check",
        local_idempotency_key=local_idempotency_key or _new_child_key("status"),
        context=context,
    )
    return intent


def create_cancel_intent(
    session: Session,
    *,
    parent_intent: BrokerOrderIntent,
    created_at: datetime,
    local_idempotency_key: str | None = None,
    context: dict,
) -> BrokerOrderIntent:
    intent = _create_child_intent(
        session,
        parent_intent=parent_intent,
        created_at=created_at,
        action="cancel",
        local_idempotency_key=local_idempotency_key or _new_child_key("cancel"),
        context=context,
    )
    return intent


def _create_child_intent(
    session: Session,
    *,
    parent_intent: BrokerOrderIntent,
    created_at: datetime,
    action: str,
    local_idempotency_key: str,
    context: dict,
) -> BrokerOrderIntent:
    intent = BrokerOrderIntent(
        created_at=created_at,
        operator_source="cli",
        action=action,
        broker="IBKR",
        broker_environment="paper",
        account_id=parent_intent.account_id,
        symbol=parent_intent.symbol,
        asset_class=parent_intent.asset_class,
        side=parent_intent.side,
        quantity=parent_intent.quantity,
        order_type=parent_intent.order_type,
        limit_price=parent_intent.limit_price,
        transmit=parent_intent.transmit,
        local_idempotency_key=local_idempotency_key,
        parent_intent_id=parent_intent.id,
        broker_order_id=parent_intent.broker_order_id,
        broker_perm_id=parent_intent.broker_perm_id,
        status="created",
        context=context,
    )
    session.add(intent)
    session.flush()
    return intent


def get_intent(session: Session, intent_id: int) -> BrokerOrderIntent | None:
    return session.get(BrokerOrderIntent, intent_id)


def mark_intent_sent(
    session: Session,
    *,
    intent_id: int,
    broker_order_id: str,
    broker_perm_id: str | None,
) -> None:
    intent = session.get(BrokerOrderIntent, intent_id)
    if intent is None:
        raise ValueError(f"broker_order_intent not found: {intent_id}")
    intent.status = "sent"
    intent.broker_order_id = broker_order_id
    intent.broker_perm_id = broker_perm_id
    session.flush()


def mark_intent_terminal(session: Session, *, intent_id: int, status: str) -> None:
    if status not in {"completed", "rejected", "failed"}:
        raise ValueError(f"not a terminal intent status: {status}")
    intent = session.get(BrokerOrderIntent, intent_id)
    if intent is None:
        raise ValueError(f"broker_order_intent not found: {intent_id}")
    intent.status = status
    session.flush()


def append_event(
    session: Session,
    *,
    intent_id: int,
    observation: BrokerOrderObservation,
) -> BrokerOrderEvent:
    event = BrokerOrderEvent(
        intent_id=intent_id,
        observed_at=observation.observed_at,
        event_type=observation.event_type,
        event_source=observation.event_source,
        broker_order_id=observation.broker_order_id,
        broker_perm_id=observation.broker_perm_id,
        broker_status=observation.broker_status,
        filled_quantity=observation.filled_quantity,
        remaining_quantity=observation.remaining_quantity,
        avg_fill_price=observation.avg_fill_price,
        message=observation.message,
        raw=observation.raw or {},
    )
    session.add(event)
    session.flush()
    return event
```

- [ ] **Step 4: Run repository tests**

Run:

```bash
uv run pytest tests/broker/test_order_repository.py -q
```

Expected: all repository tests pass.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/broker/order_repository.py tests/broker/test_order_repository.py
git commit -m "feat(7b): add broker order repository"
```

---

## Task 4: Order Service Place Flow

**Files:**
- Create: `marketpulse/broker/order_service.py`
- Test: `tests/broker/test_order_service_place.py`

- [ ] **Step 1: Write failing place-service tests**

Create `tests/broker/test_order_service_place.py`:

```python
# Layer: stateful
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from marketpulse.broker.order_types import (
    BrokerOrderObservation,
    BrokerOrderRequest,
    BrokerOrderSafetyError,
    PlaceOrderResult,
)
from marketpulse.db.base import Base
from marketpulse.db.models import BrokerOrderEvent, BrokerOrderIntent, PaperOrder


class FakeOrderClient:
    def __init__(self) -> None:
        self.place_calls = 0

    def place_lmt_order(self, *, request: BrokerOrderRequest, intent_id: int, order_ref: str):
        self.place_calls += 1
        now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
        return PlaceOrderResult(
            broker_order_id="1001",
            order_ref=order_ref,
            observations=(
                BrokerOrderObservation(
                    event_type="next_valid_id_received",
                    event_source="adapter_callback",
                    observed_at=now,
                    broker_order_id="1001",
                    raw={"order_ref": order_ref},
                ),
                BrokerOrderObservation(
                    event_type="staged_to_tws",
                    event_source="adapter_callback",
                    observed_at=now,
                    broker_order_id="1001",
                    broker_status="PreSubmitted",
                    raw={"transmit": False},
                ),
            ),
        )


class FakeTimeoutOrderClient:
    def place_lmt_order(self, *, request: BrokerOrderRequest, intent_id: int, order_ref: str):
        now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
        return PlaceOrderResult(
            broker_order_id="1002",
            order_ref=order_ref,
            observations=(
                BrokerOrderObservation(
                    event_type="error",
                    event_source="timeout",
                    observed_at=now,
                    broker_order_id="1002",
                    message="callback_timeout",
                    raw={"reason": "callback_timeout"},
                ),
            ),
        )


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _count(session: Session, model) -> int:
    return int(session.execute(select(func.count(model.id))).scalar() or 0)


def _request(account_id: str = "DU123456", key: str = "key-1", transmit: bool = False):
    return BrokerOrderRequest(
        account_id=account_id,
        symbol="AAPL",
        asset_class="STK",
        side="BUY",
        quantity=Decimal("1"),
        order_type="LMT",
        limit_price=Decimal("1.00"),
        transmit=transmit,
        local_idempotency_key=key,
    )


def test_place_creates_intent_before_broker_call_and_records_staged_event():
    from marketpulse.broker.order_service import place_paper_lmt_order

    session = _session()
    client = FakeOrderClient()
    result = place_paper_lmt_order(
        session,
        client=client,
        request=_request(),
        now=datetime(2026, 5, 24, 12, tzinfo=UTC),
        context={"source": "test"},
    )
    session.commit()

    assert client.place_calls == 1
    assert result.status == "completed"
    intent = session.get(BrokerOrderIntent, result.intent_id)
    assert intent is not None
    assert intent.broker_order_id == "1001"
    assert intent.status == "completed"
    assert _count(session, BrokerOrderEvent) == 2
    event_types = [row.event_type for row in session.scalars(select(BrokerOrderEvent)).all()]
    assert event_types == ["next_valid_id_received", "staged_to_tws"]


def test_place_refuses_live_account_before_broker_call_and_records_safety_event():
    from marketpulse.broker.order_service import place_paper_lmt_order

    session = _session()
    client = FakeOrderClient()
    result = place_paper_lmt_order(
        session,
        client=client,
        request=_request(account_id="U123456"),
        now=datetime(2026, 5, 24, 12, tzinfo=UTC),
        context={},
    )
    session.commit()

    assert client.place_calls == 0
    assert result.status == "failed"
    intent = session.get(BrokerOrderIntent, result.intent_id)
    assert intent is not None
    assert intent.status == "failed"
    event = session.scalars(select(BrokerOrderEvent)).one()
    assert event.event_type == "safety_rejected"
    assert event.event_source == "service_safety"


def test_transmit_true_requires_confirmation_before_broker_call():
    from marketpulse.broker.order_service import place_paper_lmt_order

    session = _session()
    client = FakeOrderClient()
    result = place_paper_lmt_order(
        session,
        client=client,
        request=_request(key="tx", transmit=True),
        now=datetime(2026, 5, 24, 12, tzinfo=UTC),
        context={},
        confirm_transmit=None,
    )

    assert client.place_calls == 0
    assert result.status == "failed"
    assert session.scalars(select(BrokerOrderEvent)).one().event_type == "safety_rejected"


def test_place_does_not_touch_paper_order_table():
    from marketpulse.broker.order_service import place_paper_lmt_order

    session = _session()
    before = _count(session, PaperOrder)
    place_paper_lmt_order(
        session,
        client=FakeOrderClient(),
        request=_request(key="paper-isolation"),
        now=datetime(2026, 5, 24, 12, tzinfo=UTC),
        context={},
    )
    assert _count(session, PaperOrder) == before


def test_place_callback_timeout_returns_sent_and_leaves_intent_sent():
    from marketpulse.broker.order_service import place_paper_lmt_order

    session = _session()
    result = place_paper_lmt_order(
        session,
        client=FakeTimeoutOrderClient(),
        request=_request(key="timeout"),
        now=datetime(2026, 5, 24, 12, tzinfo=UTC),
        context={},
    )
    session.commit()

    assert result.status == "sent"
    intent = session.get(BrokerOrderIntent, result.intent_id)
    assert intent is not None
    assert intent.status == "sent"
    event = session.scalars(select(BrokerOrderEvent)).one()
    assert event.event_type == "error"
    assert event.event_source == "timeout"
    assert event.raw["reason"] == "callback_timeout"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/broker/test_order_service_place.py -q
```

Expected: fail because `order_service.py` does not exist.

- [ ] **Step 3: Implement place service**

Create `marketpulse/broker/order_service.py` with place flow:

```python
"""Phase 7b manual IBKR paper order pilot orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marketpulse.broker.order_client import BrokerOrderClient
from marketpulse.broker.order_repository import (
    append_event,
    create_place_intent,
    mark_intent_sent,
    mark_intent_terminal,
)
from marketpulse.broker.order_types import (
    BrokerOrderObservation,
    BrokerOrderRequest,
    build_order_ref,
    classify_order_account,
)
from marketpulse.db.models import BrokerOrderIntent


@dataclass(frozen=True)
class OrderCommandResult:
    intent_id: int
    status: Literal["sent", "completed", "rejected", "failed"]
    broker_order_id: str | None = None
    error_message: str | None = None


def _utc(now: datetime | None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(UTC)


def _failure_event(now: datetime, event_type: str, message: str, source: str = "service_safety"):
    return BrokerOrderObservation(
        event_type=event_type,  # type: ignore[arg-type]
        event_source=source,  # type: ignore[arg-type]
        observed_at=now,
        message=message,
        raw={"message": message},
    )


def _append_all(session: Session, *, intent_id: int, observations) -> None:
    for observation in observations:
        append_event(session, intent_id=intent_id, observation=observation)


def _is_transmit_confirmed(request: BrokerOrderRequest, confirm_transmit: str | None) -> bool:
    return not request.transmit or confirm_transmit == "PAPER"


def place_paper_lmt_order(
    session: Session,
    *,
    client: BrokerOrderClient,
    request: BrokerOrderRequest,
    now: datetime | None = None,
    context: dict,
    confirm_transmit: str | None = None,
) -> OrderCommandResult:
    created_at = _utc(now)
    try:
        intent = create_place_intent(
            session,
            request=request,
            created_at=created_at,
            context=context,
        )
    except IntegrityError:
        session.rollback()
        raise

    def fail(message: str, event_type: str = "safety_rejected") -> OrderCommandResult:
        append_event(
            session,
            intent_id=intent.id,
            observation=_failure_event(created_at, event_type, message),
        )
        mark_intent_terminal(session, intent_id=intent.id, status="failed")
        return OrderCommandResult(intent_id=intent.id, status="failed", error_message=message)

    if classify_order_account(request.account_id) != "paper":
        return fail(f"Refusing non-paper account for 7b order pilot: {request.account_id}")
    if not _is_transmit_confirmed(request, confirm_transmit):
        return fail("transmit=true requires --confirm-transmit PAPER")

    order_ref = build_order_ref(intent_id=intent.id, local_idempotency_key=request.local_idempotency_key)
    try:
        result = client.place_lmt_order(request=request, intent_id=intent.id, order_ref=order_ref)
    except Exception as exc:  # noqa: BLE001
        append_event(
            session,
            intent_id=intent.id,
            observation=_failure_event(created_at, "error", str(exc)),
        )
        mark_intent_terminal(session, intent_id=intent.id, status="failed")
        return OrderCommandResult(intent_id=intent.id, status="failed", error_message=str(exc))

    mark_intent_sent(
        session,
        intent_id=intent.id,
        broker_order_id=result.broker_order_id,
        broker_perm_id=None,
    )
    _append_all(session, intent_id=intent.id, observations=result.observations)
    if any(obs.event_type == "rejected" for obs in result.observations):
        mark_intent_terminal(session, intent_id=intent.id, status="rejected")
        status: Literal["sent", "completed", "rejected", "failed"] = "rejected"
    elif any(obs.event_type == "error" and obs.raw and obs.raw.get("reason") == "callback_timeout" for obs in result.observations):
        status = "sent"
    else:
        mark_intent_terminal(session, intent_id=intent.id, status="completed")
        status = "completed"
    return OrderCommandResult(intent_id=intent.id, status=status, broker_order_id=result.broker_order_id)
```

- [ ] **Step 4: Run place-service tests**

Run:

```bash
uv run pytest tests/broker/test_order_service_place.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/broker/order_service.py tests/broker/test_order_service_place.py
git commit -m "feat(7b): orchestrate paper limit order placement"
```

---

## Task 5: Status and Cancel Service Flows

**Files:**
- Modify: `marketpulse/broker/order_service.py`
- Test: `tests/broker/test_order_service_status_cancel.py`

- [ ] **Step 1: Write failing status/cancel tests**

Create `tests/broker/test_order_service_status_cancel.py`:

```python
# Layer: stateful
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from marketpulse.broker.order_types import (
    BrokerOrderObservation,
    BrokerOrderRequest,
    CancelOrderResult,
    OrderStatusResult,
    PlaceOrderResult,
)
from marketpulse.db.base import Base
from marketpulse.db.models import BrokerOrderEvent, BrokerOrderIntent


class FakeOrderClient:
    def __init__(self, *, staged_cancel: bool = False) -> None:
        self.status_calls = 0
        self.cancel_calls = 0
        self.staged_cancel = staged_cancel

    def place_lmt_order(self, *, request, intent_id, order_ref):
        now = datetime(2026, 5, 24, 12, tzinfo=UTC)
        return PlaceOrderResult(
            broker_order_id="1001",
            order_ref=order_ref,
            observations=(
                BrokerOrderObservation("staged_to_tws", "adapter_callback", now, "1001"),
            ),
        )

    def fetch_order_status(self, *, account_id: str, broker_order_id: str):
        self.status_calls += 1
        now = datetime(2026, 5, 24, 12, 1, tzinfo=UTC)
        return OrderStatusResult(
            observations=(
                BrokerOrderObservation(
                    event_type="order_status_seen",
                    event_source="adapter_callback",
                    observed_at=now,
                    broker_order_id=broker_order_id,
                    broker_status="PreSubmitted",
                    raw={"visibility": "current_session"},
                ),
            ),
        )

    def cancel_order(self, *, account_id: str, broker_order_id: str, staged: bool):
        self.cancel_calls += 1
        now = datetime(2026, 5, 24, 12, 2, tzinfo=UTC)
        event_type = "staged_cancelled" if staged or self.staged_cancel else "cancelled"
        return CancelOrderResult(
            observations=(
                BrokerOrderObservation(
                    event_type=event_type,
                    event_source="adapter_callback",
                    observed_at=now,
                    broker_order_id=broker_order_id,
                    raw={"staged": staged},
                ),
            ),
        )


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _request(key: str = "key-1") -> BrokerOrderRequest:
    return BrokerOrderRequest(
        account_id="DU123456",
        symbol="AAPL",
        asset_class="STK",
        side="BUY",
        quantity=Decimal("1"),
        order_type="LMT",
        limit_price=Decimal("1.00"),
        transmit=False,
        local_idempotency_key=key,
    )


def _placed_intent(session: Session, client: FakeOrderClient) -> int:
    from marketpulse.broker.order_service import place_paper_lmt_order

    result = place_paper_lmt_order(
        session,
        client=client,
        request=_request(),
        now=datetime(2026, 5, 24, 12, tzinfo=UTC),
        context={},
    )
    return result.intent_id


def test_status_creates_child_intent_and_records_current_session_observation():
    from marketpulse.broker.order_service import check_paper_order_status

    session = _session()
    client = FakeOrderClient()
    parent_id = _placed_intent(session, client)

    result = check_paper_order_status(
        session,
        client=client,
        account_id="DU123456",
        parent_intent_id=parent_id,
        now=datetime(2026, 5, 24, 12, 1, tzinfo=UTC),
        context={},
    )
    session.commit()

    assert result.status == "completed"
    child = session.get(BrokerOrderIntent, result.intent_id)
    assert child is not None
    assert child.action == "status_check"
    assert child.parent_intent_id == parent_id
    events = session.scalars(select(BrokerOrderEvent).where(BrokerOrderEvent.intent_id == child.id)).all()
    assert [event.event_type for event in events] == ["order_status_seen"]


def test_cancel_creates_child_intent_and_records_staged_cancelled():
    from marketpulse.broker.order_service import cancel_paper_order

    session = _session()
    client = FakeOrderClient(staged_cancel=True)
    parent_id = _placed_intent(session, client)

    result = cancel_paper_order(
        session,
        client=client,
        account_id="DU123456",
        parent_intent_id=parent_id,
        confirm_cancel=True,
        now=datetime(2026, 5, 24, 12, 2, tzinfo=UTC),
        context={},
    )
    session.commit()

    assert result.status == "completed"
    child = session.get(BrokerOrderIntent, result.intent_id)
    assert child is not None
    assert child.action == "cancel"
    assert child.parent_intent_id == parent_id
    events = session.scalars(select(BrokerOrderEvent).where(BrokerOrderEvent.intent_id == child.id)).all()
    assert [event.event_type for event in events] == ["staged_cancelled"]


def test_status_and_cancel_fail_closed_without_broker_order_id():
    from marketpulse.broker.order_repository import create_place_intent
    from marketpulse.broker.order_service import cancel_paper_order, check_paper_order_status

    session = _session()
    parent = create_place_intent(
        session,
        request=_request("no-broker-id"),
        created_at=datetime(2026, 5, 24, 12, tzinfo=UTC),
        context={},
    )
    client = FakeOrderClient()

    status_result = check_paper_order_status(
        session,
        client=client,
        account_id="DU123456",
        parent_intent_id=parent.id,
        now=datetime(2026, 5, 24, 12, 1, tzinfo=UTC),
        context={},
    )
    cancel_result = cancel_paper_order(
        session,
        client=client,
        account_id="DU123456",
        parent_intent_id=parent.id,
        confirm_cancel=True,
        now=datetime(2026, 5, 24, 12, 2, tzinfo=UTC),
        context={},
    )

    assert status_result.status == "failed"
    assert cancel_result.status == "failed"
    assert client.status_calls == 0
    assert client.cancel_calls == 0


def test_status_account_mismatch_creates_failed_child_intent_with_event():
    from marketpulse.broker.order_service import check_paper_order_status

    session = _session()
    client = FakeOrderClient()
    parent_id = _placed_intent(session, client)

    result = check_paper_order_status(
        session,
        client=client,
        account_id="DU999999",
        parent_intent_id=parent_id,
        now=datetime(2026, 5, 24, 12, 3, tzinfo=UTC),
        context={},
    )
    session.commit()

    assert result.status == "failed"
    assert client.status_calls == 0
    child = session.get(BrokerOrderIntent, result.intent_id)
    assert child is not None
    assert child.parent_intent_id == parent_id
    event = session.scalars(select(BrokerOrderEvent).where(BrokerOrderEvent.intent_id == child.id)).one()
    assert event.event_type == "account_mismatch"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/broker/test_order_service_status_cancel.py -q
```

Expected: fail because status/cancel service functions do not exist.

- [ ] **Step 3: Implement status/cancel service functions**

Append to `marketpulse/broker/order_service.py`:

```python
from marketpulse.broker.order_repository import (
    create_cancel_intent,
    create_status_intent,
    get_intent,
)


def _fail_child(
    session: Session,
    *,
    intent: BrokerOrderIntent,
    now: datetime,
    message: str,
    event_type: str = "safety_rejected",
) -> OrderCommandResult:
    append_event(
        session,
        intent_id=intent.id,
        observation=_failure_event(now, event_type, message),
    )
    mark_intent_terminal(session, intent_id=intent.id, status="failed")
    return OrderCommandResult(intent_id=intent.id, status="failed", error_message=message)


def _load_parent_for_child(
    session: Session,
    *,
    parent_intent_id: int,
) -> BrokerOrderIntent:
    parent = get_intent(session, parent_intent_id)
    if parent is None:
        raise ValueError(f"broker_order_intent not found: {parent_intent_id}")
    if parent.action != "place":
        raise ValueError("status/cancel target must be a place intent")
    return parent


def _is_staged_parent(parent: BrokerOrderIntent) -> bool:
    return not bool(parent.transmit)


def check_paper_order_status(
    session: Session,
    *,
    client: BrokerOrderClient,
    account_id: str,
    parent_intent_id: int,
    now: datetime | None = None,
    context: dict,
) -> OrderCommandResult:
    created_at = _utc(now)
    parent = _load_parent_for_child(session, parent_intent_id=parent_intent_id)
    child = create_status_intent(session, parent_intent=parent, created_at=created_at, context=context)
    if parent.account_id != account_id:
        return _fail_child(
            session,
            intent=child,
            now=created_at,
            message="status account must match parent place intent",
            event_type="account_mismatch",
        )
    if classify_order_account(account_id) != "paper":
        return _fail_child(session, intent=child, now=created_at, message="status requires DU* paper account")
    if not parent.broker_order_id:
        return _fail_child(session, intent=child, now=created_at, message="parent place intent has no broker_order_id")
    try:
        result = client.fetch_order_status(account_id=account_id, broker_order_id=parent.broker_order_id)
    except Exception as exc:  # noqa: BLE001
        append_event(session, intent_id=child.id, observation=_failure_event(created_at, "error", str(exc)))
        mark_intent_terminal(session, intent_id=child.id, status="failed")
        return OrderCommandResult(intent_id=child.id, status="failed", error_message=str(exc))
    _append_all(session, intent_id=child.id, observations=result.observations)
    mark_intent_terminal(session, intent_id=child.id, status="completed")
    return OrderCommandResult(intent_id=child.id, status="completed", broker_order_id=parent.broker_order_id)


def cancel_paper_order(
    session: Session,
    *,
    client: BrokerOrderClient,
    account_id: str,
    parent_intent_id: int,
    confirm_cancel: bool,
    now: datetime | None = None,
    context: dict,
) -> OrderCommandResult:
    created_at = _utc(now)
    parent = _load_parent_for_child(session, parent_intent_id=parent_intent_id)
    child = create_cancel_intent(session, parent_intent=parent, created_at=created_at, context=context)
    if parent.account_id != account_id:
        return _fail_child(
            session,
            intent=child,
            now=created_at,
            message="cancel account must match parent place intent",
            event_type="account_mismatch",
        )
    if classify_order_account(account_id) != "paper":
        return _fail_child(session, intent=child, now=created_at, message="cancel requires DU* paper account")
    if not confirm_cancel:
        return _fail_child(session, intent=child, now=created_at, message="cancel requires --confirm-cancel")
    if not parent.broker_order_id:
        return _fail_child(session, intent=child, now=created_at, message="parent place intent has no broker_order_id")
    try:
        result = client.cancel_order(
            account_id=account_id,
            broker_order_id=parent.broker_order_id,
            staged=_is_staged_parent(parent),
        )
    except Exception as exc:  # noqa: BLE001
        append_event(session, intent_id=child.id, observation=_failure_event(created_at, "error", str(exc)))
        mark_intent_terminal(session, intent_id=child.id, status="failed")
        return OrderCommandResult(intent_id=child.id, status="failed", error_message=str(exc))
    _append_all(session, intent_id=child.id, observations=result.observations)
    mark_intent_terminal(session, intent_id=child.id, status="completed")
    return OrderCommandResult(intent_id=child.id, status="completed", broker_order_id=parent.broker_order_id)
```

- [ ] **Step 4: Run status/cancel tests**

Run:

```bash
uv run pytest tests/broker/test_order_service_status_cancel.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Run order service suite**

Run:

```bash
uv run pytest tests/broker/test_order_service_place.py tests/broker/test_order_service_status_cancel.py -q
```

Expected: all service tests pass.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/broker/order_service.py tests/broker/test_order_service_status_cancel.py
git commit -m "feat(7b): add order status and cancel flows"
```

---

## Task 6: CLI

**Files:**
- Create: `scripts/ibkr_paper_order.py`
- Test: `tests/broker/test_paper_order_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/broker/test_paper_order_cli.py`:

```python
from __future__ import annotations

from types import SimpleNamespace

import pytest

import scripts.ibkr_paper_order as cli


def test_place_parser_defaults_transmit_false():
    args = cli.build_parser().parse_args(
        [
            "place",
            "--account",
            "DU123456",
            "--symbol",
            "AAPL",
            "--side",
            "BUY",
            "--quantity",
            "1",
            "--limit-price",
            "1.00",
        ]
    )
    assert args.command == "place"
    assert args.transmit is False


def test_place_parser_requires_confirm_for_transmit_true(monkeypatch):
    called = {}

    def fake_run(args):
        called["confirm"] = args.confirm_transmit
        return SimpleNamespace(status="failed", intent_id=1, broker_order_id=None, error_message="no")

    monkeypatch.setattr(cli, "_run_place", fake_run)
    exit_code = cli.main(
        [
            "place",
            "--account",
            "DU123456",
            "--symbol",
            "AAPL",
            "--side",
            "BUY",
            "--quantity",
            "1",
            "--limit-price",
            "1.00",
            "--transmit",
            "true",
        ]
    )
    assert exit_code == 1
    assert called["confirm"] is None


def test_status_and_cancel_require_intent_id():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["status", "--account", "DU123456"])
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["cancel", "--account", "DU123456", "--confirm-cancel"])


def test_cancel_requires_confirm_cancel():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["cancel", "--account", "DU123456", "--intent-id", "1"])
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/broker/test_paper_order_cli.py -q
```

Expected: fail because `scripts/ibkr_paper_order.py` does not exist.

- [ ] **Step 3: Implement CLI**

Create `scripts/ibkr_paper_order.py`:

```python
"""Manual IBKR paper order pilot CLI (Phase 7b)."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path[:0] = [str(Path(__file__).resolve().parent.parent)]

from marketpulse.broker.order_service import (  # noqa: E402
    cancel_paper_order,
    check_paper_order_status,
    place_paper_lmt_order,
)
from marketpulse.broker.order_types import BrokerOrderRequest  # noqa: E402
from marketpulse.config import get_settings  # noqa: E402


def _bool_arg(value: str) -> bool:
    lowered = value.lower()
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--client-id", type=int)
    parser.add_argument("--db-url")
    sub = parser.add_subparsers(dest="command", required=True)

    place = sub.add_parser("place")
    place.add_argument("--account", required=True)
    place.add_argument("--symbol", required=True)
    place.add_argument("--side", required=True, choices=["BUY", "SELL"])
    place.add_argument("--quantity", required=True, type=Decimal)
    place.add_argument("--limit-price", required=True, type=Decimal)
    place.add_argument("--transmit", type=_bool_arg, default=False)
    place.add_argument("--confirm-transmit")
    place.add_argument("--idempotency-key")

    status = sub.add_parser("status")
    status.add_argument("--account", required=True)
    status.add_argument("--intent-id", required=True, type=int)

    cancel = sub.add_parser("cancel")
    cancel.add_argument("--account", required=True)
    cancel.add_argument("--intent-id", required=True, type=int)
    cancel.add_argument("--confirm-cancel", action="store_true", required=True)
    return parser


def _client(args: argparse.Namespace):
    from marketpulse.broker.ibkr_order_client import IbkrOrderClient

    settings = get_settings()
    return IbkrOrderClient(
        host=args.host or settings.ibkr_order_host,
        port=args.port if args.port is not None else settings.ibkr_order_port,
        client_id=args.client_id if args.client_id is not None else settings.ibkr_order_client_id,
        connect_timeout_seconds=settings.ibkr_order_connect_timeout_seconds,
        next_valid_id_timeout_seconds=settings.ibkr_order_next_valid_id_timeout_seconds,
        observation_timeout_seconds=settings.ibkr_order_observation_timeout_seconds,
    )


def _session(args: argparse.Namespace) -> Session:
    settings = get_settings()
    return Session(create_engine(args.db_url or settings.database_url))


def _run_place(args: argparse.Namespace):
    request = BrokerOrderRequest(
        account_id=args.account,
        symbol=args.symbol.upper(),
        asset_class="STK",
        side=args.side,
        quantity=args.quantity,
        order_type="LMT",
        limit_price=args.limit_price,
        transmit=args.transmit,
        local_idempotency_key=args.idempotency_key or f"place-{uuid4().hex[:12]}",
    )
    with _client(args) as client, _session(args) as session:
        result = place_paper_lmt_order(
            session,
            client=client,
            request=request,
            now=datetime.now(UTC),
            context={"command": "place", "transmit": args.transmit},
            confirm_transmit=args.confirm_transmit,
        )
        session.commit()
        return result


def _run_status(args: argparse.Namespace):
    with _client(args) as client, _session(args) as session:
        result = check_paper_order_status(
            session,
            client=client,
            account_id=args.account,
            parent_intent_id=args.intent_id,
            now=datetime.now(UTC),
            context={"command": "status"},
        )
        session.commit()
        return result


def _run_cancel(args: argparse.Namespace):
    with _client(args) as client, _session(args) as session:
        result = cancel_paper_order(
            session,
            client=client,
            account_id=args.account,
            parent_intent_id=args.intent_id,
            confirm_cancel=args.confirm_cancel,
            now=datetime.now(UTC),
            context={"command": "cancel"},
        )
        session.commit()
        return result


def _print_result(result) -> None:
    print(f"intent_id: {result.intent_id}")
    print(f"status: {result.status}")
    if result.broker_order_id:
        print(f"broker_order_id: {result.broker_order_id}")
    if result.error_message:
        print(f"error_message: {result.error_message}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "place":
        result = _run_place(args)
    elif args.command == "status":
        result = _run_status(args)
    elif args.command == "cancel":
        result = _run_cancel(args)
    else:
        raise SystemExit(f"unknown command: {args.command}")
    _print_result(result)
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run CLI tests**

Run:

```bash
uv run pytest tests/broker/test_paper_order_cli.py -q
```

Expected: all CLI tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/ibkr_paper_order.py tests/broker/test_paper_order_cli.py
git commit -m "feat(7b): add manual IBKR paper order CLI"
```

---

## Task 7: IBKR `ibapi` Adapter

**Files:**
- Create: `marketpulse/broker/ibkr_order_client.py`
- Test: `tests/broker/test_ibkr_order_client.py`

- [ ] **Step 1: Write adapter tests with fake app hooks**

Create `tests/broker/test_ibkr_order_client.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from marketpulse.broker.ibkr_order_client import (
    _decimal_or_none,
    _map_order_status_event,
    _sanitize_raw,
)


def test_decimal_or_none_handles_ibkr_unset_values():
    assert _decimal_or_none(None) is None
    assert _decimal_or_none("1.7976931348623157E308") is None
    assert _decimal_or_none(float("inf")) is None
    assert _decimal_or_none("NaN") is None
    assert _decimal_or_none("12.34") == Decimal("12.34")


def test_sanitize_raw_removes_secret_like_keys_and_stringifies_values():
    raw = _sanitize_raw(
        {
            "status": "Submitted",
            "token": "secret",
            "password": "secret",
            "nested": {"session": "secret", "ok": "yes"},
        }
    )
    assert raw["status"] == "Submitted"
    assert raw["token"] == "[redacted]"
    assert raw["password"] == "[redacted]"
    assert raw["nested"]["session"] == "[redacted]"
    assert raw["nested"]["ok"] == "yes"


def test_map_order_status_preserves_broker_status_separately():
    observed_at = datetime(2026, 5, 24, 12, tzinfo=UTC)
    obs = _map_order_status_event(
        observed_at=observed_at,
        broker_order_id="1001",
        status="Submitted",
        filled=Decimal("0"),
        remaining=Decimal("1"),
        avg_fill_price=None,
        perm_id="555",
        raw={"status": "Submitted"},
    )
    assert obs.event_type == "order_status_seen"
    assert obs.event_source == "adapter_callback"
    assert obs.broker_status == "Submitted"
    assert obs.raw == {"status": "Submitted"}


def test_map_filled_status_to_observational_filled_event():
    observed_at = datetime(2026, 5, 24, 12, tzinfo=UTC)
    obs = _map_order_status_event(
        observed_at=observed_at,
        broker_order_id="1001",
        status="Filled",
        filled=Decimal("1"),
        remaining=Decimal("0"),
        avg_fill_price=Decimal("1.00"),
        perm_id="555",
        raw={"status": "Filled"},
    )
    assert obs.event_type == "filled"
    assert obs.broker_status == "Filled"
```

- [ ] **Step 2: Run adapter tests to verify failure**

Run:

```bash
uv run pytest tests/broker/test_ibkr_order_client.py -q
```

Expected: fail because `ibkr_order_client.py` does not exist.

- [ ] **Step 3: Implement adapter helpers and client skeleton**

Create `marketpulse/broker/ibkr_order_client.py`:

```python
"""IBKR TWS/Gateway order adapter for Phase 7b.

Only this module may import ibapi. Callback state is normalized into immutable
MarketPulse DTOs before crossing the adapter boundary.
"""

from __future__ import annotations

import math
import queue
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.wrapper import EWrapper

from marketpulse.broker.order_types import (
    BrokerOrderConnectionError,
    BrokerOrderObservation,
    BrokerOrderRequest,
    BrokerOrderTimeoutError,
    CancelOrderResult,
    OrderStatusResult,
    PlaceOrderResult,
)

UNSET_DOUBLE = Decimal("1.7976931348623157E308")
SECRET_KEYS = {"token", "password", "session", "secret", "authorization", "cookie"}


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not decimal.is_finite():
        return None
    if decimal.copy_abs() >= UNSET_DOUBLE:
        return None
    return decimal


def _sanitize_raw(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            if str(key).lower() in SECRET_KEYS:
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = _sanitize_raw(item)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_sanitize_raw(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _map_order_status_event(
    *,
    observed_at: datetime,
    broker_order_id: str,
    status: str,
    filled: Decimal | None,
    remaining: Decimal | None,
    avg_fill_price: Decimal | None,
    perm_id: str | None,
    raw: dict,
) -> BrokerOrderObservation:
    event_type = "filled" if status == "Filled" else "order_status_seen"
    if status in {"Cancelled", "ApiCancelled"}:
        event_type = "cancelled"
    if status in {"Inactive"}:
        event_type = "rejected"
    return BrokerOrderObservation(
        event_type=event_type,  # type: ignore[arg-type]
        event_source="adapter_callback",
        observed_at=observed_at,
        broker_order_id=broker_order_id,
        broker_perm_id=perm_id,
        broker_status=status,
        filled_quantity=filled,
        remaining_quantity=remaining,
        avg_fill_price=avg_fill_price,
        raw=_sanitize_raw(raw),
    )


class _IbkrOrderApp(EWrapper, EClient):
    def __init__(self) -> None:
        EClient.__init__(self, self)
        self.next_valid_id: int | None = None
        self.managed_accounts: set[str] = set()
        self.observations: queue.Queue[BrokerOrderObservation] = queue.Queue()

    def nextValidId(self, orderId: int) -> None:  # noqa: N802
        self.next_valid_id = orderId
        self.observations.put(
            BrokerOrderObservation(
                event_type="next_valid_id_received",
                event_source="adapter_callback",
                observed_at=datetime.now(UTC),
                broker_order_id=str(orderId),
                raw={"orderId": orderId},
            )
        )

    def managedAccounts(self, accountsList: str) -> None:  # noqa: N802
        self.managed_accounts = {account.strip() for account in accountsList.split(",") if account.strip()}

    def orderStatus(  # noqa: N802
        self,
        orderId,
        status,
        filled,
        remaining,
        avgFillPrice,
        permId,
        parentId,
        lastFillPrice,
        clientId,
        whyHeld,
        mktCapPrice,
    ) -> None:
        self.observations.put(
            _map_order_status_event(
                observed_at=datetime.now(UTC),
                broker_order_id=str(orderId),
                status=str(status),
                filled=_decimal_or_none(filled),
                remaining=_decimal_or_none(remaining),
                avg_fill_price=_decimal_or_none(avgFillPrice),
                perm_id=str(permId) if permId else None,
                raw={
                    "orderId": orderId,
                    "status": status,
                    "filled": filled,
                    "remaining": remaining,
                    "avgFillPrice": avgFillPrice,
                    "permId": permId,
                    "parentId": parentId,
                    "lastFillPrice": lastFillPrice,
                    "clientId": clientId,
                    "whyHeld": whyHeld,
                    "mktCapPrice": mktCapPrice,
                },
            )
        )


class IbkrOrderClient:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        client_id: int,
        connect_timeout_seconds: int,
        next_valid_id_timeout_seconds: int,
        observation_timeout_seconds: int,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.connect_timeout_seconds = connect_timeout_seconds
        self.next_valid_id_timeout_seconds = next_valid_id_timeout_seconds
        self.observation_timeout_seconds = observation_timeout_seconds
        self._app = _IbkrOrderApp()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "IbkrOrderClient":
        self._app.connect(self.host, self.port, self.client_id)
        self._thread = threading.Thread(target=self._app.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + self.connect_timeout_seconds
        while time.monotonic() < deadline:
            if self._app.isConnected():
                return self
            time.sleep(0.05)
        raise BrokerOrderConnectionError(f"failed to connect to TWS/Gateway at {self.host}:{self.port}")

    def __exit__(self, exc_type, exc, tb) -> None:
        self._app.disconnect()

    def _wait_next_valid_id(self) -> int:
        deadline = time.monotonic() + self.next_valid_id_timeout_seconds
        while time.monotonic() < deadline:
            if self._app.next_valid_id is not None:
                return self._app.next_valid_id
            time.sleep(0.05)
        raise BrokerOrderTimeoutError("nextValidId timeout")

    def _wait_managed_accounts(self) -> set[str]:
        deadline = time.monotonic() + self.connect_timeout_seconds
        while time.monotonic() < deadline:
            if self._app.managed_accounts:
                return self._app.managed_accounts
            time.sleep(0.05)
        raise BrokerOrderConnectionError("managedAccounts timeout")

    def _validate_account(self, account_id: str) -> None:
        managed_accounts = self._wait_managed_accounts()
        if account_id not in managed_accounts:
            raise BrokerOrderConnectionError(f"connected accounts do not include {account_id}")

    def _drain_observations(self) -> tuple[BrokerOrderObservation, ...]:
        observations: list[BrokerOrderObservation] = []
        deadline = time.monotonic() + self.observation_timeout_seconds
        while time.monotonic() < deadline:
            try:
                observations.append(self._app.observations.get(timeout=0.1))
                break
            except queue.Empty:
                continue
        while True:
            try:
                observations.append(self._app.observations.get_nowait())
            except queue.Empty:
                break
        return tuple(observations)

    def place_lmt_order(self, *, request: BrokerOrderRequest, intent_id: int, order_ref: str) -> PlaceOrderResult:
        self._validate_account(request.account_id)
        order_id = self._wait_next_valid_id()
        contract = Contract()
        contract.symbol = request.symbol
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"

        order = Order()
        order.action = request.side
        order.orderType = "LMT"
        order.totalQuantity = float(request.quantity)
        order.lmtPrice = float(request.limit_price)
        order.account = request.account_id
        order.transmit = bool(request.transmit)
        order.orderRef = order_ref

        self._app.placeOrder(order_id, contract, order)
        observations = self._drain_observations()
        if not any(obs.event_type in {"staged_to_tws", "submitted_to_broker", "order_status_seen", "filled", "rejected"} for obs in observations):
            observations = observations + (
                BrokerOrderObservation(
                    event_type="error",
                    event_source="timeout",
                    observed_at=datetime.now(UTC),
                    broker_order_id=str(order_id),
                    message="callback_timeout",
                    raw={"reason": "callback_timeout"},
                ),
            )
        if request.transmit:
            observations = observations + (
                BrokerOrderObservation(
                    event_type="submitted_to_broker",
                    event_source="adapter_callback",
                    observed_at=datetime.now(UTC),
                    broker_order_id=str(order_id),
                    raw={"transmit": True},
                ),
            )
        else:
            observations = observations + (
                BrokerOrderObservation(
                    event_type="staged_to_tws",
                    event_source="adapter_callback",
                    observed_at=datetime.now(UTC),
                    broker_order_id=str(order_id),
                    raw={"transmit": False},
                ),
            )
        return PlaceOrderResult(
            broker_order_id=str(order_id),
            order_ref=order_ref,
            observations=observations,
        )

    def fetch_order_status(self, *, account_id: str, broker_order_id: str) -> OrderStatusResult:
        self._validate_account(account_id)
        observations = self._drain_observations()
        if not observations:
            observations = (
                BrokerOrderObservation(
                    event_type="error",
                    event_source="timeout",
                    observed_at=datetime.now(UTC),
                    broker_order_id=broker_order_id,
                    message="current session did not return order status",
                    raw={"reason": "status_not_visible_in_current_session"},
                ),
            )
        return OrderStatusResult(observations=observations)

    def cancel_order(self, *, account_id: str, broker_order_id: str, staged: bool) -> CancelOrderResult:
        self._validate_account(account_id)
        self._app.cancelOrder(int(broker_order_id), "")
        observations = self._drain_observations()
        if not observations:
            observations = (
                BrokerOrderObservation(
                    event_type="staged_cancelled" if staged else "broker_cancel_requested",
                    event_source="adapter_callback",
                    observed_at=datetime.now(UTC),
                    broker_order_id=broker_order_id,
                    raw={"staged": staged},
                ),
            )
        return CancelOrderResult(observations=observations)
```

- [ ] **Step 4: Run adapter tests**

Run:

```bash
uv run pytest tests/broker/test_ibkr_order_client.py -q
```

Expected: all adapter helper tests pass. If `ibapi` import fails, re-run `uv lock` and confirm `ibapi` is in `uv.lock`.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/broker/ibkr_order_client.py tests/broker/test_ibkr_order_client.py
git commit -m "feat(7b): add isolated ibapi order adapter"
```

---

## Task 8: Architecture Guards

**Files:**
- Create: `tests/architecture/test_phase7b_order_boundary.py`
- Modify: `tests/architecture/test_phase7a_ibkr_readonly_boundary.py`

- [ ] **Step 1: Write architecture guard**

Create `tests/architecture/test_phase7b_order_boundary.py`:

```python
# Layer: architecture
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PRODUCTION_ROOTS = [REPO / "marketpulse", REPO / "scripts"]
IBAPI_ALLOW = {REPO / "marketpulse" / "broker" / "ibkr_order_client.py"}
FORBIDDEN_IMPORTERS = (
    "marketpulse.scheduler",
    "marketpulse.trading.daily_cycle",
    "marketpulse.web",
    "marketpulse.app",
)
FORBIDDEN_MUTATING_TOKENS = {
    "reqGlobalCancel",
    "exerciseOptions",
    "replaceOrder",
    "modifyOrder",
}


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in PRODUCTION_ROOTS:
        if root.exists():
            files.extend(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    return files


def test_only_ibkr_order_client_imports_ibapi():
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            imported = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "ibapi" or alias.name.startswith("ibapi."):
                        imported = alias.name
            elif isinstance(node, ast.ImportFrom) and (
                node.module == "ibapi" or (node.module or "").startswith("ibapi.")
            ):
                imported = node.module
            if imported and path not in IBAPI_ALLOW:
                offenders.append(f"{path.relative_to(REPO)} imports {imported}")
    assert not offenders, "Only marketpulse/broker/ibkr_order_client.py may import ibapi:\n" + "\n".join(offenders)


def test_scheduler_daily_cycle_web_and_strategy_do_not_import_order_service():
    offenders: list[str] = []
    for path in (REPO / "marketpulse").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        module_name = ".".join(path.relative_to(REPO).with_suffix("").parts)
        if not module_name.startswith(FORBIDDEN_IMPORTERS):
            continue
        text = path.read_text()
        if "marketpulse.broker.order_service" in text or "ibkr_paper_order" in text:
            offenders.append(str(path.relative_to(REPO)))
    assert not offenders, "7b write path must not be reachable from automated/web flows:\n" + "\n".join(offenders)


def test_forbidden_ibkr_write_apis_absent():
    offenders: list[str] = []
    for path in _python_files():
        text = path.read_text()
        for token in FORBIDDEN_MUTATING_TOKENS:
            if token in text:
                offenders.append(f"{path.relative_to(REPO)} contains {token}")
    assert not offenders, "7b forbids modify/global-cancel/options APIs:\n" + "\n".join(offenders)
```

- [ ] **Step 2: Update 7a guard to allow 7b adapter only**

Modify `tests/architecture/test_phase7a_ibkr_readonly_boundary.py` so the first test becomes a read-only boundary guard rather than a global deny:

```python
def test_no_readonly_sync_module_imports_ibapi():
    """7a-Flex remains ibapi-free; 7b may use ibapi only in ibkr_order_client."""
    offenders: list[str] = []
    readonly_files = [
        ROOT / "broker" / "flex_client.py",
        ROOT / "broker" / "read_client.py",
        ROOT / "broker" / "readonly_sync.py",
        ROOT / "broker" / "repository.py",
    ]
    for path in readonly_files:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "ibapi" or alias.name.startswith("ibapi."):
                        offenders.append(f"{path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and (
                node.module == "ibapi" or (node.module or "").startswith("ibapi.")
            ):
                offenders.append(f"{path}: from {node.module} import ...")
    assert not offenders, "7a-Flex read-only sync modules must not import ibapi:\n" + "\n".join(offenders)
```

- [ ] **Step 3: Run architecture tests**

Run:

```bash
uv run pytest tests/architecture/test_phase7a_ibkr_readonly_boundary.py tests/architecture/test_phase7b_order_boundary.py -q
```

Expected: all architecture tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/architecture/test_phase7a_ibkr_readonly_boundary.py tests/architecture/test_phase7b_order_boundary.py
git commit -m "test(7b): guard broker order boundaries"
```

---

## Task 9: Runbook and Manual Smoke Notes

**Files:**
- Create: `docs/operations/ibkr-paper-order-pilot-runbook.md`
- Test: `tests/broker/test_paper_order_cli.py`

- [ ] **Step 1: Add runbook assertions to CLI test**

Append to `tests/broker/test_paper_order_cli.py`:

```python
from pathlib import Path


def test_runbook_documents_transmit_false_and_flex_visibility():
    text = Path("docs/operations/ibkr-paper-order-pilot-runbook.md").read_text()
    assert "transmit=false" in text
    assert "may not appear in 7a Flex snapshots" in text
    assert "--confirm-transmit PAPER" in text
    assert "does not write paper_order" in text
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
uv run pytest tests/broker/test_paper_order_cli.py::test_runbook_documents_transmit_false_and_flex_visibility -q
```

Expected: fail because the runbook does not exist.

- [ ] **Step 3: Write runbook**

Create `docs/operations/ibkr-paper-order-pilot-runbook.md`:

```markdown
# IBKR Paper Order Pilot Runbook

Phase 7b is a manual paper-account execution pilot. It is not production broker
execution, not a scheduler path, and not an OMS.

## Safety Rules

- Use only an IBKR paper account whose id starts with `DU`.
- 7b ignores `MP_IBKR_ALLOW_LIVE`; live execution is out of scope.
- The CLI does not write `paper_order`, `paper_fill`, `paper_position`, or `paper_cash_ledger`.
- The first smoke uses `transmit=false`.
- Transmitted paper orders require `--transmit true --confirm-transmit PAPER`.

## First Smoke

Start TWS or IB Gateway locally and log into the paper account. Then run:

```bash
uv run python scripts/ibkr_paper_order.py place \
  --account DUxxxx \
  --symbol AAPL \
  --side BUY \
  --quantity 1 \
  --limit-price 1.00 \
  --transmit false
```

Expected result:

- `broker_order_intent` row with `action=place`
- `broker_order_event` rows including `staged_to_tws` or diagnostic `error`
- no rows added to paper trading lifecycle tables

Then run:

```bash
uv run python scripts/ibkr_paper_order.py status --account DUxxxx --intent-id 123
uv run python scripts/ibkr_paper_order.py cancel --account DUxxxx --intent-id 123 --confirm-cancel
```

## Flex Visibility

A `transmit=false` staged order may not appear in 7a Flex snapshots because it was
not submitted/executed at IBKR. That is expected. 7a Flex remains the broker-truth
capture path for submitted/executed account activity; 7b provenance tables record
TWS-local staged smoke artifacts.

## Optional Transmitted Paper Smoke

Only after staged smoke succeeds:

```bash
uv run python scripts/ibkr_paper_order.py place \
  --account DUxxxx \
  --symbol AAPL \
  --side BUY \
  --quantity 1 \
  --limit-price 1.00 \
  --transmit true \
  --confirm-transmit PAPER
```

Do not add retry/replay logic, market orders, modify/replace, bracket/OCO/algo
orders, web buttons, scheduler hooks, or strategy wiring in Phase 7b.
```

- [ ] **Step 4: Run runbook test**

Run:

```bash
uv run pytest tests/broker/test_paper_order_cli.py::test_runbook_documents_transmit_false_and_flex_visibility -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add docs/operations/ibkr-paper-order-pilot-runbook.md tests/broker/test_paper_order_cli.py
git commit -m "docs(7b): add IBKR paper order pilot runbook"
```

---

## Task 10: Full Verification and Cleanups

**Files:**
- All 7b files

- [ ] **Step 1: Run focused 7b tests**

Run:

```bash
uv run pytest \
  tests/migration/test_0013_broker_order_pilot.py \
  tests/broker/test_order_types.py \
  tests/broker/test_order_repository.py \
  tests/broker/test_order_service_place.py \
  tests/broker/test_order_service_status_cancel.py \
  tests/broker/test_ibkr_order_client.py \
  tests/broker/test_paper_order_cli.py \
  tests/architecture/test_phase7b_order_boundary.py \
  -q
```

Expected: all focused 7b tests pass.

- [ ] **Step 2: Run full pytest**

Run:

```bash
uv run pytest
```

Expected: full suite passes. If existing unrelated tests fail, capture exact failures before changing anything.

- [ ] **Step 3: Run ruff**

Run:

```bash
uv run ruff check .
```

Expected: no lint failures.

- [ ] **Step 4: Verify Alembic single head**

Run:

```bash
uv run alembic heads
```

Expected: exactly one head, `0013`.

- [ ] **Step 5: Verify migration upgrade/downgrade path**

Run:

```bash
tmp_db="$(mktemp -t marketpulse-7b-XXXXXX.db)"
DATABASE_URL="sqlite:///$tmp_db" uv run alembic upgrade head
DATABASE_URL="sqlite:///$tmp_db" uv run alembic downgrade 0012
DATABASE_URL="sqlite:///$tmp_db" uv run alembic upgrade head
rm -f "$tmp_db"
```

Expected: all commands exit 0.

- [ ] **Step 6: Manual smoke command examples only**

Do not run against real IBKR in automated verification. Record these commands in the final handoff for the operator:

```bash
uv run python scripts/ibkr_paper_order.py place \
  --account DUxxxx \
  --symbol AAPL \
  --side BUY \
  --quantity 1 \
  --limit-price 1.00 \
  --transmit false

uv run python scripts/ibkr_paper_order.py status --account DUxxxx --intent-id 123

uv run python scripts/ibkr_paper_order.py cancel \
  --account DUxxxx \
  --intent-id 123 \
  --confirm-cancel
```

- [ ] **Step 7: Final commit**

If verification fixes changed files, commit them:

```bash
git status --short
git add pyproject.toml uv.lock marketpulse/config.py marketpulse/db/models.py marketpulse/broker/order_types.py marketpulse/broker/order_client.py marketpulse/broker/order_repository.py marketpulse/broker/order_service.py marketpulse/broker/ibkr_order_client.py scripts/ibkr_paper_order.py docs/operations/ibkr-paper-order-pilot-runbook.md tests/migration/test_0013_broker_order_pilot.py tests/broker/test_order_types.py tests/broker/test_order_repository.py tests/broker/test_order_service_place.py tests/broker/test_order_service_status_cancel.py tests/broker/test_ibkr_order_client.py tests/broker/test_paper_order_cli.py tests/architecture/test_phase7a_ibkr_readonly_boundary.py tests/architecture/test_phase7b_order_boundary.py
git commit -m "test(7b): verify IBKR paper execution pilot"
```

If no files changed, do not create an empty commit.
