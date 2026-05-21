# Phase 6a — Paper Trading Foundation: Design

**Status:** Brainstorm complete · ready for implementation plan
**Author:** brainstorm 2026-05-21
**Spec-type:** sub-project (first concrete spec under Phase 6 umbrella)
**Umbrella:** `docs/superpowers/specs/2026-05-21-phase-6-umbrella-design.md`
**Scope:** Paper-only trading foundation — `ExecutionEngine` Protocol, `ForwardExecutionEngine` implementation, single-writer persistence, daily orchestration, scheduler entrypoint, stateful test suite.

---

## 1 — Goal & Boundary

### Goal

Ship the foundation that turns MarketPulse from a research/backtest engine into a forward-running paper-trading system. After 6a:

- The `ExecutionEngine` Protocol exists; `ForwardExecutionEngine` is its canonical Phase 6 implementation.
- Five new DB tables (`paper_order`, `paper_fill`, `paper_position`, `paper_cash_ledger`, `paper_audit_event`) hold canonical paper-trading state.
- A daily scheduler job collects today's AI events, runs the Phase 5-shared allocator, places paper orders, materializes entries and exits, and writes a `TICK_COMPLETED` audit row.
- The system is restart-safe, idempotent per tick, and survives multi-day downtime by skipping missed days (forward-only recovery, lock xxxiii).
- A `# Layer: stateful` test category proves multi-day lifecycle correctness.

### What 6a explicitly does NOT include

- **No real risk gates.** `AlwaysApproveRiskGate` stub ships with 6a (gate-shape contract); real sector/correlation/daily-loss/market-hours gates ship with **6b**.
- **No UI.** Paper trading state is observable via DB queries and tests only; `/lab/paper-trading` ships with **6f**.
- **No push notifications, recap fanout, or observability UI.** Audit rows are written; consumption is **6g**.
- **No shadow optimizer.** `ShadowPoolOptimizer` is stretch **6e**.
- **No realtime engine.** `RealtimeExecutionEngine` is stretch **6d**.
- **No Postgres migration.** SQLite stays; Phase 7 evaluates Postgres (see § 12 forward-warnings).

### Anti-goals

- ❌ No event-driven (real-time) execution — daily batch only.
- ❌ No retroactive replay of missed days (lock xxxiii).
- ❌ No "current cash balance" mutable column — `paper_cash_ledger.balance_after ORDER BY id DESC LIMIT 1` is the source of truth.
- ❌ No bypass of `repository.py` — it is the **only** module allowed to INSERT/UPDATE `paper_*` tables.

---

## 2 — Sub-task Decomposition

Five sub-tasks. Each ships with its own tests at the sub-task boundary. All sub-tasks merge sequentially onto branch `plan/phase-6a-paper-trading-foundation`; a single PR brings the completed foundation to main.

| Sub-task | Scope | Tests at sub-task boundary |
|---|---|---|
| **6a-0** | Extract `allocate_for_day(...)` pure-function kernel from Phase 5 `simulate_shared_pool`. Refactor `portfolio_simulator.py` to call it once per historical day. | Phase 5 full regression: behavioral + public-field equality on existing fixtures (warm-pool included). Pre-extraction vs post-extraction `PortfolioBacktestResult.bid_history`, KPIs, sector breakdown all equal field-by-field, excluding any intentionally versioned/provenance fields. |
| **6a-1** | DB schema (5 tables) + Alembic migration `0010_phase6_paper_trading.py`. Scaffolding: `types.py`, `execution_engine.py` (Protocol), `clock.py`, `calendar.py`, `risk_gate.py`, `idempotency.py`. Empty `__init__.py` for `repository.py`, `forward_engine.py`, `kill_switch.py`, `bid_aggregator.py`, `daily_cycle.py` so import order works. | Schema applies cleanly (`alembic upgrade head` + `downgrade -1`). All imports resolve. Enum / event-type values match expected. `compute_idempotency_key(...)` is deterministic. `NYTradingCalendar` returns expected business-day arithmetic for known fixtures. |
| **6a-2** | `ForwardExecutionEngine` (full `place_order` / `cancel_order` / `tick`). `repository.py` (single-writer surface: all `paper_*` mutations + execution-path reads). `kill_switch.py` env+DB read/write. | Unit + stateful lifecycle tests. Per-order entry materialization is a single transaction; per-position exit materialization is a single transaction. `place_order` idempotency hits, kill-switch rejects, risk-gate rejects, accepted path. Atomic INSERT (order + audit). `tick` returns `TickResult` with correct counts. |
| **6a-3** | `BidAggregator` (read-only NY-day window). `daily_cycle.py` orchestration (gap detect → collect → allocate → place_order × N → tick → TICK_COMPLETED). `marketpulse/scheduler/paper_trading_tick.py` thin entrypoint. APScheduler wiring in `marketpulse/scheduler/jobs.py`. `ensure_initial_deposit()` startup hook in `marketpulse/main.py`. Config in `marketpulse/config.py`. | Scheduler / gap-detection / idempotency-restart tests. Deterministic `allocation_run_id` per tick_date confirmed. `TICK_COMPLETED` written once per tick_date. `SCHEDULER_GAP_DETECTED` deduped per (last_tick, resume_date). Kill-switch short-circuits the entire cycle. |
| **6a-4** | Full E2E stateful suite using `FakeClock`. `# Layer: stateful` enforcement via the Phase 5e pytest hook (extended to accept the third value). Smoke run of full test suite + ruff + route smoke. | Multi-day forward flow: D0 place → D0 tick entry → D1-D4 no-op ticks → D5 horizon → exit + cash correct. Same-day rerun produces zero new state. Multi-day downtime + restart produces `SCHEDULER_GAP_DETECTED`. Property: `Σ paper_cash_ledger.delta == latest balance_after`. |

### Test discipline

Each sub-task ships **with its own tests**. 6a-4 is the cross-cutting integration suite, not the only testing layer.

### Branch / PR strategy

Sub-tasks are checkpoints on `plan/phase-6a-paper-trading-foundation`. A single PR merges the completed foundation to main. Main never sees half-built schema or stub gates.

---

## 3 — Module Layout

```
marketpulse/trading/                              (NEW PACKAGE)
├── __init__.py                                   empty; no side effects
├── types.py                                      OrderRequest, OrderId, TickResult,
│                                                 OrderStatus, PositionStatus, FillSide,
│                                                 AuditEventType enum, AllocationRunId,
│                                                 OrderRejected, InvariantError
├── execution_engine.py                           ExecutionEngine Protocol (3 methods)
├── forward_engine.py                             ForwardExecutionEngine (Phase 6 impl)
├── repository.py                                 SINGLE-WRITER SURFACE for paper_*
│                                                 (writes + execution-path reads)
├── clock.py                                      Clock Protocol, WallClock, FakeClock
├── calendar.py                                   NYTradingCalendar (exchange_calendars)
├── kill_switch.py                                KillSwitchState (env + DB flag)
├── idempotency.py                                compute_idempotency_key()
├── risk_gate.py                                  RiskGate Protocol + AlwaysApproveRiskGate
├── bid_aggregator.py                             BidAggregator (read-only NY-day window)
└── daily_cycle.py                                run() orchestrator

marketpulse/scheduler/paper_trading_tick.py       (NEW) thin scheduler entrypoint

marketpulse/backtest/allocation.py                (NEW, owned by 6a-0)
                                                  allocate_for_day() pure kernel
                                                  BidCandidate, AllocationContext,
                                                  SizingContext, AllocationResult,
                                                  PositionSnapshot dataclasses

marketpulse/db/models.py                          (MODIFIED) +5 model classes

marketpulse/backtest/portfolio_simulator.py       (MODIFIED) calls allocate_for_day()
                                                  per-day instead of inline allocation

marketpulse/scheduler/jobs.py                     (MODIFIED) registers paper_trading_tick_job

marketpulse/main.py                               (MODIFIED) calls ensure_initial_deposit()

marketpulse/config.py                             (MODIFIED) adds paper_tick_hour,
                                                  paper_tick_minute, paper_initial_deposit,
                                                  paper_kill_switch settings

pyproject.toml                                    (MODIFIED) adds exchange_calendars

alembic/versions/0010_phase6_paper_trading.py     (NEW migration)
```

### Module dependency rules (import-linter enforced)

```
types.py        ───►  (no internal deps)
clock.py        ───►  (no internal deps)
calendar.py     ───►  (no internal deps)
idempotency.py  ───►  types
risk_gate.py    ───►  types
execution_engine.py ─►  types
repository.py   ───►  types, db.models                  (NEVER kill_switch, forward_engine,
                                                          daily_cycle, bid_aggregator)
kill_switch.py  ───►  types, repository                 (writes KILL_SWITCH_FLIPPED audit
                                                          via repository)
forward_engine.py ──►  types, execution_engine,
                       repository, clock, idempotency,
                       kill_switch, risk_gate
bid_aggregator.py ──►  types, calendar, db.models
daily_cycle.py  ───►  types, execution_engine,
                       repository, bid_aggregator,
                       calendar, clock,
                       backtest.allocation
scheduler/paper_trading_tick.py ─►  trading.* (all)
```

### Why this layout (recap of locked principles)

- **`execution_engine.py` is the contract**; `forward_engine.py` is one implementation. Phase 7's `BrokerExecutionEngine` and 6d's `RealtimeExecutionEngine` plug into the same Protocol without touching `forward_engine.py`.
- **`repository.py` is the single mutator surface for `paper_*` tables.** Import-linter rule: no module outside `repository.py` may use SQLAlchemy `session.add()` / `session.execute(insert)` / `session.execute(update)` on `paper_*` models.
- **`daily_cycle.py` owns the orchestration sequence; `paper_trading_tick.py` is thin** (lock xxv).
- **`bid_aggregator.py` is read-only and dumb** — no DEDUP / sizing / capping; that's `allocate_for_day`'s job.

---

## 4 — Database Schema

5 tables. Single Alembic migration `0010_phase6_paper_trading.py`. SQLite + `Numeric(18, 6)` + `TZDateTime` (existing TypeDecorator). Schema is contract; migration creates empty tables only — `INITIAL_DEPOSIT` is seeded by `ensure_initial_deposit()` at app startup (§ 7.4).

### 4.1 `paper_order`

```python
class PaperOrder(Base):
    __tablename__ = "paper_order"

    id: Mapped[int]                                # PK, autoincrement
    idempotency_key: Mapped[str]                   # UNIQUE
    allocation_run_id: Mapped[str]                 # deterministic: "paper-{tick_date}"
    strategy: Mapped[str]                          # YAML strategy name
    ticker: Mapped[str]
    quantity: Mapped[int]                          # signed (positive only in Phase 6)

    # Time semantics
    event_time: Mapped[datetime]                   # TZDateTime, UTC (lock xxix)
    allocation_date: Mapped[date]                  # NY trading day of allocator call
    horizon_date: Mapped[date]
    placed_at: Mapped[datetime]                    # TZDateTime, UTC
    filled_at: Mapped[datetime | None]
    cancelled_at: Mapped[datetime | None]
    cancel_reason: Mapped[str | None]

    # Prices — Decimal at persistence boundary (lock xxii)
    event_price: Mapped[Decimal]                   # Numeric(18, 6)
    horizon_price: Mapped[Decimal | None]          # forward-known; None reserved for Phase 7

    # Lifecycle
    status: Mapped[str]                            # PLACED | ENTRY_FILLED | CANCELLED

    # Versioning for replay determinism (lock xxviii)
    strategy_version: Mapped[str]
    allocator_version: Mapped[str]
    execution_engine_version: Mapped[str]

    # Phase 5 allocation provenance (lock x: shared with backtest)
    weight: Mapped[float]
    raw_bid_weight: Mapped[float | None]
    pool_corr: Mapped[float | None]
    contribution_multiplier: Mapped[float]
    adjusted_bid_weight: Mapped[float | None]
    effective_corr_window: Mapped[int]
    rewarded_for_negative_corr: Mapped[bool]
    would_change_rank: Mapped[bool]
    size_clamped_by_override: Mapped[bool]
```

**Indexes:**

```
UNIQUE (idempotency_key)                            -- lock xvii
INDEX  (status, horizon_date)                       -- tick exit scan
INDEX  (status, allocation_date)                    -- tick entry scan
INDEX  (allocation_date, strategy)                  -- per-day per-strategy queries
INDEX  (strategy, placed_at)                        -- per-strategy timeline
INDEX  (allocation_run_id)                          -- batch grouping
```

**CHECK constraints:**

```sql
CHECK (status IN ('PLACED', 'ENTRY_FILLED', 'CANCELLED'))
CHECK (status != 'PLACED'        OR (filled_at IS NULL AND cancelled_at IS NULL))
CHECK (status != 'ENTRY_FILLED'  OR filled_at IS NOT NULL)
CHECK (status != 'CANCELLED'     OR cancelled_at IS NOT NULL)
CHECK (quantity > 0)
```

### 4.2 `paper_fill`

```python
class PaperFill(Base):
    __tablename__ = "paper_fill"

    id: Mapped[int]                                # PK
    order_id: Mapped[int]                          # FK → paper_order.id
    position_id: Mapped[int]                       # FK → paper_position.id (NOT NULL)
    side: Mapped[str]                              # ENTRY | EXIT
    price: Mapped[Decimal]                         # Numeric(18, 6)
    quantity: Mapped[int]
    filled_at: Mapped[datetime]                    # TZDateTime, UTC
    cash_delta: Mapped[Decimal]                    # Numeric(18, 6), signed
    realized_pnl: Mapped[Decimal | None]           # NULL for ENTRY; set for EXIT
```

**Append-only contract** (lock xiii). `repository.py` exposes only `insert_paper_fill(...)`. No update or delete.

**Indexes:**

```
INDEX (order_id)
INDEX (position_id, side)
UNIQUE (order_id, side)                             -- ≤1 ENTRY + ≤1 EXIT per order
```

**CHECK:**

```sql
CHECK (side IN ('ENTRY', 'EXIT'))
CHECK (side != 'ENTRY' OR realized_pnl IS NULL)
CHECK (side != 'EXIT'  OR realized_pnl IS NOT NULL)
CHECK (quantity > 0)
```

### 4.3 `paper_position`

```python
class PaperPosition(Base):
    __tablename__ = "paper_position"

    id: Mapped[int]                                # PK
    order_id: Mapped[int]                          # FK → paper_order.id, UNIQUE (lock xiv)
    entry_fill_id: Mapped[int | None]              # plain nullable int (see § 4.7)
    exit_fill_id: Mapped[int | None]               # plain nullable int (see § 4.7)
    strategy: Mapped[str]                          # denormalized
    ticker: Mapped[str]                            # denormalized
    quantity: Mapped[int]
    entry_price: Mapped[Decimal]                   # Numeric(18, 6)
    entry_date: Mapped[date]
    horizon_date: Mapped[date]
    status: Mapped[str]                            # OPEN | CLOSED
    opened_at: Mapped[datetime]                    # TZDateTime, UTC
    closed_at: Mapped[datetime | None]
    exit_price: Mapped[Decimal | None]
    realized_pnl: Mapped[Decimal | None]
```

**Indexes:**

```
UNIQUE (order_id)                                   -- lock xiv
INDEX  (status, horizon_date)                       -- tick exit scan
INDEX  (strategy, ticker)                           -- exposure queries
INDEX  (entry_fill_id)
INDEX  (exit_fill_id)
```

**CHECK:**

```sql
CHECK (status IN ('OPEN', 'CLOSED'))
CHECK (status != 'OPEN'   OR exit_fill_id IS NULL)
CHECK (status != 'CLOSED' OR (entry_fill_id IS NOT NULL AND exit_fill_id IS NOT NULL))
```

The post-transaction invariant *"committed OPEN row has `entry_fill_id IS NOT NULL`"* is an **app-level rule** verified by `test_repository.py`. SQLite cannot express it as a CHECK because it must permit a transient NULL during the ENTRY-flow transaction.

### 4.4 `paper_cash_ledger`

```python
class PaperCashLedger(Base):
    __tablename__ = "paper_cash_ledger"

    id: Mapped[int]                                # PK, monotonic — ordering source (lock xxi)
    timestamp: Mapped[datetime]                    # TZDateTime, UTC
    delta: Mapped[Decimal]                         # Numeric(18, 6), signed
    reason: Mapped[str]                            # ENTRY_FILL | EXIT_FILL |
                                                   #  INITIAL_DEPOSIT | MANUAL_ADJUSTMENT
    fill_id: Mapped[int | None]                    # FK → paper_fill.id (NULL for deposits)
    balance_after: Mapped[Decimal]                 # Numeric(18, 6), running total
```

**Append-only** (lock xiii). Balance is computed by `repository.insert_cash_ledger_entry_for_fill(...)` inside the transaction (§ 6.3.2).

**Indexes:**

```
INDEX (timestamp)
INDEX (fill_id)
```

**CHECK:**

```sql
CHECK (reason IN ('ENTRY_FILL', 'EXIT_FILL', 'INITIAL_DEPOSIT', 'MANUAL_ADJUSTMENT'))
CHECK (reason NOT IN ('INITIAL_DEPOSIT', 'MANUAL_ADJUSTMENT') OR fill_id IS NULL)
CHECK (reason NOT IN ('ENTRY_FILL', 'EXIT_FILL') OR fill_id IS NOT NULL)
```

### 4.5 `paper_audit_event`

Execution-owned append-only provenance ledger (locks v + xiii). The audit table is part of `paper_*` (single-writer boundary), not part of 6g observability.

```python
class PaperAuditEvent(Base):
    __tablename__ = "paper_audit_event"

    id: Mapped[int]                                # PK
    timestamp: Mapped[datetime]                    # TZDateTime, UTC (write time)
    event_type: Mapped[str]                        # see AuditEventType enum below
    order_id: Mapped[int | None]
    strategy: Mapped[str | None]
    reason: Mapped[str]                            # human-readable; empty for success events
    context: Mapped[dict]                          # JSON
```

**6a audit event types (10):**

| event_type | order_id | Writer | Context required keys |
|---|---|---|---|
| `ORDER_PLACED` | set | `place_order` success | `idempotency_key`, `allocation_run_id` |
| `ORDER_PLACED_DUPLICATE` | set | `place_order` idempotency hit | `idempotency_key` |
| `ORDER_REJECTED` | NULL | `place_order` reject (kill / risk) | `order_request` dump, `reason` |
| `ORDER_CANCELLED` | set | `cancel_order` | `prior_status` |
| `ORDER_ENTRY_FILLED` | set | `tick` entry materialization | `position_id`, `fill_price`, `cash_balance_after` |
| `POSITION_CLOSED` | set | `tick` exit materialization | `position_id`, `exit_price`, `realized_pnl`, `cash_balance_after` |
| `KILL_SWITCH_FLIPPED` | NULL | `KillSwitchState.flip` | `from_state`, `to_state`, `actor`, `reason` |
| `TICK_COMPLETED` | NULL | `daily_cycle.run` end | **`tick_date` (required)**, `allocation_run_id`, `bids_collected`, `orders_placed`, `orders_rejected`, `duplicates_skipped`, `entries_materialized`, `exits_materialized`, `cash_balance_end` |
| `SCHEDULER_GAP_DETECTED` | NULL | `daily_cycle.run` gap branch | `last_successful_tick_date`, `resume_date`, `missed_business_days`, `mode` |
| `ENGINE_INVARIANT_ERROR` | NULL | `tick` invariant violation | `phase`, `position_id` or `order_id`, `error`, `as_of` |

`AuditEventType` is a string enum in `marketpulse/trading/types.py`. 6b / 6f / 6g extend the migration's CHECK constraint as they add types.

**Indexes:**

```
INDEX (timestamp)
INDEX (event_type, timestamp)                      -- last_successful_tick_date query
INDEX (order_id)                                   -- per-order provenance
INDEX (strategy, timestamp)
```

**CHECK:**

```sql
CHECK (event_type IN ('ORDER_PLACED', 'ORDER_PLACED_DUPLICATE', 'ORDER_REJECTED',
                      'ORDER_CANCELLED', 'ORDER_ENTRY_FILLED', 'POSITION_CLOSED',
                      'KILL_SWITCH_FLIPPED', 'TICK_COMPLETED',
                      'SCHEDULER_GAP_DETECTED', 'ENGINE_INVARIANT_ERROR'))
```

### 4.6 Insertion-order discipline (links § 4.2, § 4.3)

**ENTRY flow** — preserves append-only `paper_fill`:

```
1. INSERT paper_position (status=OPEN, entry_fill_id=NULL, exit_fill_id=NULL)
2. INSERT paper_fill (side=ENTRY, position_id=<known>)
3. UPDATE paper_position.entry_fill_id = <fill_id>
4. INSERT paper_cash_ledger (reason=ENTRY_FILL, fill_id=<fill_id>, delta=-cash_outflow)
5. UPDATE paper_order.status = ENTRY_FILLED, filled_at = now
6. INSERT paper_audit_event (event_type=ORDER_ENTRY_FILLED)
```

All 6 operations are in a single transaction (`_materialize_entry`, § 6.4.1).

**EXIT flow:**

```
1. INSERT paper_fill (side=EXIT, position_id=<known>, realized_pnl=<computed>)
2. UPDATE paper_position (status=CLOSED, exit_fill_id=<fill_id>, exit_price, realized_pnl, closed_at)
3. INSERT paper_cash_ledger (reason=EXIT_FILL, fill_id=<fill_id>, delta=+cash_inflow)
4. INSERT paper_audit_event (event_type=POSITION_CLOSED)
```

Single transaction (`_materialize_exit`, § 6.4.2).

### 4.7 SQLite-specific notes

- **FK enforcement** is on (`PRAGMA foreign_keys = ON` in `marketpulse/db/base.py`).
- **`paper_position.entry_fill_id / exit_fill_id` are plain nullable INTEGER columns with indexes, NOT FK constraints**, to avoid the circular-FK problem during the ENTRY-flow transaction. Phase 7 / Postgres migration tightens these into deferred FKs (Drift D in umbrella § 6.2).
- **Decimal arithmetic happens in Python.** SQLite stores `Numeric(18, 6)` as TEXT or REAL; SQLAlchemy round-trips values correctly via `Numeric`-typed columns. The persistence boundary is `OrderRequest` construction — float-from-allocator quantizes to `Decimal` there.
- **`TZDateTime`** TypeDecorator (existing) stores ISO-8601 with offset as TEXT. All Phase 6 timestamps use it. No timezone-naive datetimes ever enter `paper_*`.
- **CHECK constraint coverage** is best-effort. SQLite enforces row-level CHECKs but no cross-row or cross-transaction constraints. App-invariant tests in `test_repository.py` cover the rest.

### 4.8 Migration sequence (`0010_phase6_paper_trading.py`)

```
1. CREATE TABLE paper_audit_event
2. CREATE TABLE paper_order
3. CREATE TABLE paper_position
4. CREATE TABLE paper_fill
5. CREATE TABLE paper_cash_ledger
6. CREATE all indexes
7. (No data seeded — ensure_initial_deposit() handles INITIAL_DEPOSIT at app startup)
```

Downgrade: `DROP TABLE` in reverse order. Phase 1-5 tables untouched (lock xv).

---

## 5 — `ExecutionEngine` Protocol

```python
# marketpulse/trading/execution_engine.py

@dataclass(frozen=True)
class TickResult:
    """Returned by ExecutionEngine.tick() so callers know what happened.
    Required because net-OPEN counting from outside is unreliable for
    same-tick open+close edge cases (round-3 review)."""
    as_of: date
    entries_materialized: int      # PLACED → ENTRY_FILLED count
    exits_materialized: int        # OPEN → CLOSED count
    errors: list[str]              # InvariantError messages (e.g., horizon_price IS NULL)


class ExecutionEngine(Protocol):
    """Command-only Protocol. Reads of canonical state happen via
    DB query helpers in repository.py (execution-path) or future
    query_models.py (UI/observability — deferred to 6f/6g).

    Phase 6 ships:   ForwardExecutionEngine
    Phase 7 will add: BrokerExecutionEngine
    Stretch (6d):     RealtimeExecutionEngine

    All three implement the SAME Protocol. Downstream callers do not
    know which one is running."""

    def place_order(self, *, order_request: OrderRequest) -> OrderId: ...

    def cancel_order(self, *, order_id: OrderId) -> None: ...

    def tick(self, *, as_of: date) -> TickResult: ...
```

The Protocol surface is intentionally tiny. All operational behavior lives in the implementation (`ForwardExecutionEngine`) plus the repository.

### 5.1 `OrderRequest` boundary object

```python
# marketpulse/trading/types.py

@dataclass(frozen=True)
class OrderRequest:
    """RiskGates produce, ExecutionEngine consumes. Phase 7's
    BrokerExecutionEngine consumes the same shape.

    OrderRequest construction is the float → Decimal quantization
    boundary (lock xxii). Allocator output may be float; everything
    past this point sees Decimal."""

    strategy: str
    ticker: str
    quantity: int                      # signed: positive=long (positive only in Phase 6)
    event_time: datetime               # UTC, tz-aware (lock xxix)
    allocation_date: date              # NY trading day
    event_price: Decimal               # Numeric(18, 6) at persistence
    horizon_date: date
    horizon_price: Decimal | None      # forward-known in Phase 6; None reserved for Phase 7
    allocation_run_id: AllocationRunId

    # Versioning (lock xxviii)
    strategy_version: str
    allocator_version: str
    execution_engine_version: str

    # Phase 5 allocation provenance (lock x)
    weight: float
    raw_bid_weight: float | None
    pool_corr: float | None
    contribution_multiplier: float
    adjusted_bid_weight: float | None
    effective_corr_window: int
    rewarded_for_negative_corr: bool
    would_change_rank: bool
    size_clamped_by_override: bool

    @classmethod
    def from_winner(
        cls,
        *,
        winner: AllocationWinner,
        allocation_run_id: AllocationRunId,
        allocation_date: date,
        strategy_version: str,
        allocator_version: str,
        execution_engine_version: str,
    ) -> "OrderRequest":
        """Builds OrderRequest from allocate_for_day winner. THIS IS THE
        QUANTIZATION SITE: float prices from the allocator are converted
        to Decimal here, never elsewhere."""
        ...
```

---

## 6 — `ForwardExecutionEngine` Behavior

### 6.1 Construction (DI shape)

```python
# marketpulse/trading/forward_engine.py

class ForwardExecutionEngine:
    """The ONLY Phase 6 ExecutionEngine implementation."""

    def __init__(
        self,
        *,
        repository: Repository,
        clock: Clock,
        kill_switch: KillSwitchState,
        risk_gate: RiskGate,
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._kill_switch = kill_switch
        self._risk_gate = risk_gate
```

No business state in instance fields — all state is in the repository (lock i).

### 6.2 `place_order` — canonical flow

Per locks ix (rejection audits), xxvii (transactional), xxx (idempotency before risk gates).

```python
def place_order(self, *, order_request: OrderRequest) -> OrderId:
    # Step 1: deterministic idempotency key (pure; no DB)
    key = compute_idempotency_key(order_request)

    # Step 2: idempotency check
    existing = self._repo.find_paper_order_by_idempotency_key(key)
    if existing is not None:
        # ALWAYS write ORDER_PLACED_DUPLICATE — never silent.
        self._repo.write_audit_event(
            event_type=AuditEventType.ORDER_PLACED_DUPLICATE,
            order_id=existing.id,
            strategy=order_request.strategy,
            reason="idempotent_replay",
            context={"idempotency_key": key,
                     "allocation_run_id": order_request.allocation_run_id},
        )
        return OrderId(existing.id)

    # Step 3: kill-switch (checked separately, BEFORE risk gate)
    if self._kill_switch.is_active():
        # Audit MUST commit before raising OrderRejected (lock ix).
        # If audit write fails, the DB error surfaces; the order is
        # NOT considered a valid completed rejection.
        self._repo.write_audit_event(
            event_type=AuditEventType.ORDER_REJECTED,
            order_id=None,
            strategy=order_request.strategy,
            reason="kill_switch_active",
            context={"order_request": _dump(order_request)},
        )
        raise OrderRejected("kill_switch_active")

    # Step 4: risk gate (6a uses AlwaysApproveRiskGate; 6b replaces it)
    risk_result = self._risk_gate.check_pre_trade(order_request=order_request)
    if not risk_result.approved:
        self._repo.write_audit_event(
            event_type=AuditEventType.ORDER_REJECTED,
            order_id=None,
            strategy=order_request.strategy,
            reason=risk_result.reason,
            context={"order_request": _dump(order_request),
                     "gate": risk_result.gate_name},
        )
        raise OrderRejected(risk_result.reason)

    # Step 5: accepted — atomic INSERT order + audit (lock xxvii)
    with self._repo.transaction():
        order = self._repo.insert_paper_order(
            order_request=order_request,
            idempotency_key=key,
            placed_at=self._clock.now(),
        )
        self._repo.write_audit_event(
            event_type=AuditEventType.ORDER_PLACED,
            order_id=order.id,
            strategy=order_request.strategy,
            reason="",
            context={"idempotency_key": key,
                     "allocation_run_id": order_request.allocation_run_id},
        )

    return OrderId(order.id)
```

**Invariants:**

| # | Invariant |
|---|---|
| A | Step order is **idempotency → kill switch → risk gate → atomic INSERT**. Tests grep the function body to confirm. |
| B | `OrderRejected` is raised ONLY after the `ORDER_REJECTED` audit row commits. If audit write fails, the DB error surfaces. |
| C | Rejections create NO `paper_order` row. |
| D | Idempotency hits ALWAYS write `ORDER_PLACED_DUPLICATE`. |
| E | Accepted INSERT + `ORDER_PLACED` audit are in a single transaction. |

### 6.3 `cancel_order`

```python
def cancel_order(self, *, order_id: OrderId) -> None:
    order = self._repo.find_paper_order_by_id(int(order_id))
    if order is None:
        raise ValueError(f"unknown order_id={order_id}")

    if order.status in ("ENTRY_FILLED", "CANCELLED"):
        # Idempotent no-op. No state change. No audit row.
        return

    # status == "PLACED" — flip to CANCELLED
    with self._repo.transaction():
        self._repo.update_paper_order_status(
            order_id=order.id,
            new_status="CANCELLED",
            cancelled_at=self._clock.now(),
            cancel_reason="manual_cancel",
        )
        self._repo.write_audit_event(
            event_type=AuditEventType.ORDER_CANCELLED,
            order_id=order.id,
            strategy=order.strategy,
            reason="manual_cancel",
            context={"prior_status": "PLACED"},
        )
```

Phase 6a does not ship a "cleanup stale PLACED" job. `cancel_order` is a primitive used by future 6f UI / explicit operator actions.

### 6.4 `tick(as_of)` semantics

`tick` is **idempotent** (lock xxiv) but **NOT globally atomic**. Each row materialization is its own transaction.

```python
def tick(self, *, as_of: date) -> TickResult:
    entries = 0
    exits = 0
    errors: list[str] = []

    # Phase A: entries for PLACED orders whose allocation_date <= as_of
    pending_entries = self._repo.find_orders_for_entry(as_of=as_of)
    for order in pending_entries:
        try:
            self._materialize_entry(order, fill_date=as_of)
            entries += 1
        except InvariantError as e:
            self._record_invariant_error(phase="entry_materialization",
                                          order_id=order.id, error=str(e),
                                          as_of=as_of)
            errors.append(f"entry/{order.id}: {e}")

    # Phase B: exits for OPEN positions whose horizon_date <= as_of
    pending_exits = self._repo.find_positions_for_exit(as_of=as_of)
    for position in pending_exits:
        try:
            self._materialize_exit(position, exit_date=as_of)
            exits += 1
        except InvariantError as e:
            self._record_invariant_error(phase="exit_materialization",
                                          position_id=position.id, error=str(e),
                                          as_of=as_of)
            errors.append(f"exit/{position.id}: {e}")

    return TickResult(
        as_of=as_of,
        entries_materialized=entries,
        exits_materialized=exits,
        errors=errors,
    )
```

**Idempotency proof:** the queries filter by `status = 'PLACED'` / `status = 'OPEN'`. First call flips statuses; second call's queries return zero rows. As long as status is updated inside the same transaction as fill+ledger+audit, partial-state-on-crash is impossible.

#### 6.4.1 `_materialize_entry`

```python
def _materialize_entry(self, order: PaperOrder, *, fill_date: date) -> None:
    fill_time = self._clock.now()
    fill_price = order.event_price                   # Phase 6: allocator's event_price
    cash_outflow = fill_price * Decimal(order.quantity)

    with self._repo.transaction():
        # 1. INSERT position OPEN (entry_fill_id=NULL transiently)
        position = self._repo.insert_paper_position(
            order_id=order.id,
            strategy=order.strategy,
            ticker=order.ticker,
            quantity=order.quantity,
            entry_price=fill_price,
            entry_date=fill_date,
            horizon_date=order.horizon_date,
            opened_at=fill_time,
        )

        # 2. INSERT fill ENTRY (position_id known)
        fill = self._repo.insert_paper_fill(
            order_id=order.id,
            position_id=position.id,
            side="ENTRY",
            price=fill_price,
            quantity=order.quantity,
            filled_at=fill_time,
            cash_delta=-cash_outflow,
            realized_pnl=None,
        )

        # 3. UPDATE position.entry_fill_id (resolves the cycle)
        self._repo.update_paper_position_entry_fill(
            position_id=position.id,
            entry_fill_id=fill.id,
        )

        # 4. INSERT cash ledger (repository computes balance_after)
        self._repo.insert_cash_ledger_entry_for_fill(
            timestamp=fill_time,
            delta=-cash_outflow,
            reason="ENTRY_FILL",
            fill_id=fill.id,
        )

        # 5. UPDATE order status
        self._repo.update_paper_order_status(
            order_id=order.id,
            new_status="ENTRY_FILLED",
            filled_at=fill_time,
        )

        # 6. Audit
        self._repo.write_audit_event(
            event_type=AuditEventType.ORDER_ENTRY_FILLED,
            order_id=order.id,
            strategy=order.strategy,
            reason="",
            context={
                "position_id": position.id,
                "fill_price": str(fill_price),
                "cash_balance_after": str(self._repo.cash_balance()),
            },
        )
```

`insert_cash_ledger_entry_for_fill` is the lock — repository reads the latest `balance_after` inside the same transaction and computes the new running total, so balance reads never escape the transaction (§ 6.3 round-3 lock).

#### 6.4.2 `_materialize_exit`

```python
def _materialize_exit(self, position: PaperPosition, *, exit_date: date) -> None:
    exit_time = self._clock.now()

    order = self._repo.find_paper_order_by_id(position.order_id)
    if order.horizon_price is None:
        raise InvariantError(
            f"order {order.id} has no horizon_price; "
            "ForwardExecutionEngine cannot exit without it (lock xii)"
        )

    exit_price = order.horizon_price
    cash_inflow = exit_price * Decimal(position.quantity)
    realized_pnl = (exit_price - position.entry_price) * Decimal(position.quantity)

    with self._repo.transaction():
        # 1. INSERT fill EXIT
        fill = self._repo.insert_paper_fill(
            order_id=position.order_id,
            position_id=position.id,
            side="EXIT",
            price=exit_price,
            quantity=position.quantity,
            filled_at=exit_time,
            cash_delta=cash_inflow,
            realized_pnl=realized_pnl,
        )

        # 2. UPDATE position → CLOSED
        self._repo.update_paper_position_exit(
            position_id=position.id,
            exit_fill_id=fill.id,
            exit_price=exit_price,
            realized_pnl=realized_pnl,
            closed_at=exit_time,
        )

        # 3. INSERT cash ledger
        self._repo.insert_cash_ledger_entry_for_fill(
            timestamp=exit_time,
            delta=cash_inflow,
            reason="EXIT_FILL",
            fill_id=fill.id,
        )

        # 4. Audit
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
            },
        )
```

### 6.5 Error handling matrix

| Failure mode | Behavior |
|---|---|
| DB error mid-`place_order` accepted transaction | Rollback: no paper_order, no audit. Idempotency key still free. Retry will succeed. |
| DB error during `ORDER_REJECTED` audit write | DB error surfaces to caller. NO `OrderRejected` raised. Lock ix says a rejection without audit is not a valid rejection. |
| DB error mid-`_materialize_entry` | Rollback: no position, no fill, no ledger, no audit, order remains PLACED. Next `tick(as_of)` retries. |
| DB error mid-`_materialize_exit` | Rollback: position remains OPEN. Next `tick` retries. |
| Process killed mid-tick | On boot, scheduler fires next at cron time. `daily_cycle.run(today)` → `tick(today)` re-queries PLACED + OPEN rows and resumes. |
| `horizon_price IS NULL` at exit time | `InvariantError` raised inside the exit loop. Position remains OPEN. `ENGINE_INVARIANT_ERROR` audit row written. Other positions in the same tick continue processing (lock: tick is per-row, not globally atomic). |

---

## 7 — Daily Cycle Orchestration

### 7.1 `daily_cycle.run()`

```python
# marketpulse/trading/daily_cycle.py

@dataclass(frozen=True)
class DailyCycleResult:
    tick_date: date
    allocation_run_id: AllocationRunId
    bids_collected: int
    orders_placed: int
    orders_rejected: int
    duplicates_skipped: int
    entries_materialized: int      # from TickResult
    exits_materialized: int        # from TickResult
    tick_errors: list[str]         # from TickResult.errors
    cash_balance_end: Decimal


def run(
    *,
    clock: Clock,
    engine: ExecutionEngine,
    repository: Repository,
    bid_aggregator: BidAggregator,
    allocator: AllocateForDay,     # alias for the allocate_for_day function
    calendar: TradingCalendar,
) -> DailyCycleResult:
    """One business-day forward step. Idempotent across reruns of the same
    tick_date (deterministic allocation_run_id, idempotent TICK_COMPLETED)."""

    tick_date = calendar.today_ny_trading_date(clock.now())
    allocation_run_id = AllocationRunId(f"paper-{tick_date.isoformat()}")

    # ---- Phase 1: gap detection (deduped per gap window) ----
    last_tick = repository.last_successful_tick_date()
    if last_tick is not None and last_tick < tick_date:
        missed = calendar.business_days_between(last_tick, tick_date) - 1
        if missed > 0:
            repository.write_gap_audit_once(
                last_tick=last_tick,
                resume_date=tick_date,
                missed_business_days=missed,
            )

    # ---- Phase 2: collect today's raw bids (no DEDUP — allocator's job) ----
    bids = bid_aggregator.collect_for_date(tick_date)

    # ---- Phase 3: allocate (pure function; no DB, no Clock) ----
    allocation_result = allocator(
        bids=bids,
        existing_positions=repository.open_positions_snapshot(),
        cash_available=repository.cash_balance(),
        allocation_context=AllocationContext.from_repository(
            repository=repository,
            calendar=calendar,
            tick_date=tick_date,
        ),
        sizing_context=SizingContext.default(),
    )

    # ---- Phase 4: place_order per winner ----
    placed = rejected = duplicates = 0
    for winner in allocation_result.winners:
        request = OrderRequest.from_winner(
            winner=winner,
            allocation_run_id=allocation_run_id,
            allocation_date=tick_date,
            strategy_version=winner.strategy_version,
            allocator_version=allocator.__version__,
            execution_engine_version=ForwardExecutionEngine.VERSION,
        )

        existed_before = repository.find_paper_order_by_idempotency_key(
            compute_idempotency_key(request)
        ) is not None

        try:
            engine.place_order(order_request=request)
            if existed_before:
                duplicates += 1
            else:
                placed += 1
        except OrderRejected:
            rejected += 1

    # ---- Phase 5: tick (entries then exits, returns counts) ----
    tick_result = engine.tick(as_of=tick_date)

    # ---- Phase 6: TICK_COMPLETED audit (idempotent per tick_date) ----
    result = DailyCycleResult(
        tick_date=tick_date,
        allocation_run_id=allocation_run_id,
        bids_collected=len(bids),
        orders_placed=placed,
        orders_rejected=rejected,
        duplicates_skipped=duplicates,
        entries_materialized=tick_result.entries_materialized,
        exits_materialized=tick_result.exits_materialized,
        tick_errors=tick_result.errors,
        cash_balance_end=repository.cash_balance(),
    )

    repository.write_tick_completed_once(
        tick_date=tick_date,
        context={
            "tick_date": tick_date.isoformat(),         # canonical query key (§ 4.5)
            "allocation_run_id": allocation_run_id,
            "bids_collected": result.bids_collected,
            "orders_placed": result.orders_placed,
            "orders_rejected": result.orders_rejected,
            "duplicates_skipped": result.duplicates_skipped,
            "entries_materialized": result.entries_materialized,
            "exits_materialized": result.exits_materialized,
            "tick_errors": result.tick_errors,
            "cash_balance_end": str(result.cash_balance_end),
        },
    )

    return result
```

### 7.2 Determinism + idempotency contract

| What | How |
|---|---|
| `allocation_run_id` per day | Deterministic: `f"paper-{tick_date.isoformat()}"`. Same-day rerun threads the SAME id into every OrderRequest. |
| Idempotency key per order | Deterministic from `(strategy, ticker, event_time, allocation_run_id)`. Same-day rerun produces the same keys → all `place_order` calls hit `ORDER_PLACED_DUPLICATE`. |
| `TICK_COMPLETED` row | `repository.write_tick_completed_once(tick_date, ...)` is a no-op if a `TICK_COMPLETED` audit with the same `context.tick_date` already exists. |
| `SCHEDULER_GAP_DETECTED` row | `repository.write_gap_audit_once(...)` is a no-op if an audit with the same `(last_successful_tick_date, resume_date)` already exists. |
| `tick()` row materialization | Filters by `status = 'PLACED' / 'OPEN'`; once flipped, subsequent ticks find zero rows. |

A second `daily_cycle.run()` call on the same NY trading day produces:

- 0 new `paper_order` rows (all idempotency keys hit existing rows)
- N `ORDER_PLACED_DUPLICATE` audits (one per attempted re-allocation)
- 0 new `paper_fill` rows (orders already ENTRY_FILLED → not in PLACED query)
- 0 new `paper_position` rows
- 0 new `paper_cash_ledger` rows
- 0 new `TICK_COMPLETED` rows (dedup)
- 0 new `SCHEDULER_GAP_DETECTED` rows (dedup)

### 7.3 Forward-only downtime recovery (lock xxxiii)

- BidAggregator considers **only events with `event_date == tick_date`** (today).
- Events from prior dates are forever skipped from a paper-trading perspective. They remain in `evaluation_event` for Phase 5 backtest use.
- When gap is detected, `SCHEDULER_GAP_DETECTED` audit is written with `{last_successful_tick_date, resume_date, missed_business_days, mode: "forward_only_skip"}`.
- The system does NOT replay missed days. Phase 6 is a forward-running shadow, not a historical reconstruction engine.

### 7.4 Initial deposit (called at app startup, not migration)

```python
# marketpulse/trading/repository.py

def ensure_initial_deposit(
    self,
    *,
    amount: Decimal,
    timestamp: datetime,           # caller passes clock.now() — no datetime.now in repo
) -> None:
    """Idempotent. INSERT the INITIAL_DEPOSIT row only if paper_cash_ledger is empty."""
    count = self._session.execute(
        select(func.count(PaperCashLedger.id))
    ).scalar()
    if count == 0:
        self._session.add(PaperCashLedger(
            timestamp=timestamp,
            delta=amount,
            reason="INITIAL_DEPOSIT",
            fill_id=None,
            balance_after=amount,
        ))
        self._session.commit()
```

Called from `marketpulse/main.py` at app startup:

```python
# In startup hook, after init_engine():
with session_factory() as session:
    Repository(session).ensure_initial_deposit(
        amount=Decimal(settings.paper_initial_deposit),
        timestamp=WallClock().now(),
    )
```

---

## 8 — Scheduler Entrypoint

### 8.1 Thin entrypoint (lock xxv)

```python
# marketpulse/scheduler/paper_trading_tick.py

def paper_trading_tick_job() -> None:
    """APScheduler entrypoint. Thin: resolves DI, calls daily_cycle.run.

    Lock xxv: this function contains NO business logic. All such logic
    lives in marketpulse/trading/daily_cycle.py."""
    settings = get_settings()
    session_factory = get_session_factory()

    with session_factory() as session:
        clock = WallClock()
        calendar = NYTradingCalendar()
        repository = Repository(session)
        risk_gate = AlwaysApproveRiskGate()                # 6b replaces
        kill_switch = KillSwitchState(
            env_var="MP_PAPER_KILL_SWITCH",
            repository=repository,
        )
        engine = ForwardExecutionEngine(
            repository=repository,
            clock=clock,
            kill_switch=kill_switch,
            risk_gate=risk_gate,
        )
        bid_aggregator = BidAggregator(session=session, calendar=calendar)

        result = daily_cycle.run(
            clock=clock,
            engine=engine,
            repository=repository,
            bid_aggregator=bid_aggregator,
            allocator=allocate_for_day,
            calendar=calendar,
        )

        logger.info(
            "paper_trading_tick done: tick_date=%s placed=%d closed=%d "
            "entries=%d errors=%d",
            result.tick_date, result.orders_placed,
            result.exits_materialized, result.entries_materialized,
            len(result.tick_errors),
        )
```

### 8.2 APScheduler registration

```python
# In marketpulse/scheduler/jobs.py (modified):

scheduler.add_job(
    paper_trading_tick_job,
    trigger=CronTrigger(
        hour=settings.paper_tick_hour,           # default 17 (NY)
        minute=settings.paper_tick_minute,       # default 30
        timezone=ZoneInfo("America/New_York"),
    ),
    id="paper_trading_tick",
    misfire_grace_time=3600,                     # 1 hour grace
    coalesce=True,                               # coalesce multiple missed firings
    max_instances=1,                             # never two concurrent (lock iii)
)
```

### 8.3 Default fire time: 17:30 America/New_York

```
16:00 NY  US equity market close
16:00–17:30 NY  watchlist scan + AI analyses finish (existing pipeline)
17:30 NY  paper trading tick fires
          - All today's evaluation_event rows are in DB
          - BidAggregator collects them
          - allocate_for_day produces winners
          - place_order × N → tick(today) materializes entries
17:31 NY onward  state is final; UI / push can read it
```

Configurable via `MP_PAPER_TICK_HOUR` + `MP_PAPER_TICK_MINUTE`.

### 8.4 Boot behavior

The scheduler does NOT auto-fire on boot. Cron fires at the next scheduled NY time. Missed-day handling is via `daily_cycle.run`'s gap detection at the next regular fire.

An opt-in boot-catchup mode (`MP_PAPER_BOOT_CATCHUP=1`) may be added in a follow-up if needed. Phase 6a does NOT ship it.

---

## 9 — Test Plan

### 9.1 Test layout

```
tests/trading/
├── test_types.py                    OrderRequest frozen + hash, enum values, TickResult repr
├── test_clock.py                    FakeClock advance, WallClock UTC always
├── test_calendar.py                 business-day arithmetic, DST guard, NY → UTC window
├── test_idempotency.py              deterministic key, collision behavior, replay safety
├── test_kill_switch.py              env + DB precedence, KILL_SWITCH_FLIPPED on flip
├── test_risk_gate.py                AlwaysApproveRiskGate approves all; Protocol shape
├── test_repository.py               single-writer assertions, append-only paper_fill /
│                                    ledger / audit, OPEN with entry_fill_id NULL is
│                                    transient, transactional rollback on injected error
├── test_forward_engine.py           place_order full flow (5 sub-scenarios), cancel_order
│                                    idempotent no-op + flip, tick(as_of) entry+exit
│                                    materialization, tick is per-row transactional,
│                                    ENGINE_INVARIANT_ERROR audit on horizon_price NULL
├── test_bid_aggregator.py           today-only window, NY → UTC conversion, no DEDUP
├── test_daily_cycle.py              gap detection idempotent, allocation_run_id
│                                    deterministic, TICK_COMPLETED once per tick_date,
│                                    duplicates counted correctly
├── test_scheduler.py                paper_trading_tick_job DI resolution, thin wrapper
│                                    contains no SQL or business logic
└── test_e2e_stateful.py             6a-4: full multi-day FakeClock flow

tests/backtest/
└── test_allocation_extraction.py    6a-0: behavioral + public-field equality
                                     pre/post extraction on Phase 5 fixtures
                                     (warm-pool + others)
```

### 9.2 Layer tags

```
test_types.py                     # Layer: invariant
test_clock.py                     # Layer: invariant
test_calendar.py                  # Layer: invariant
test_idempotency.py               # Layer: invariant
test_kill_switch.py               # Layer: behavioral
test_risk_gate.py                 # Layer: invariant
test_repository.py                # Layer: invariant (single-writer + append-only)
                                  # Layer: stateful (transactional rollback flows)
test_forward_engine.py            # Layer: behavioral (place_order branches)
                                  # Layer: stateful (tick lifecycle)
test_bid_aggregator.py            # Layer: behavioral
test_daily_cycle.py               # Layer: stateful (orchestration)
test_scheduler.py                 # Layer: invariant (no-logic guard)
test_e2e_stateful.py              # Layer: stateful
test_allocation_extraction.py     # Layer: behavioral (Phase 5 cross-check)
```

### 9.3 Operational test map (parallels umbrella § 8)

| # | Category | Scenario | Locks protected |
|---|---|---|---|
| 1 | Clock determinism | `grep -rn 'date\.today()\|datetime\.now(' marketpulse/trading/ marketpulse/scheduler/paper_trading_tick.py` returns ZERO matches outside `WallClock` definition | xxiii |
| 2 | Replay / idempotency | `place_order(req)` twice with same key → existing OrderId returned + 1 `ORDER_PLACED_DUPLICATE` audit + 0 new `paper_order` | xvii, xxx |
| 3 | Replay / idempotency | `tick(as_of=D)` twice → second call processes 0 rows; `TickResult(entries=0, exits=0)` | xxiv |
| 4 | Replay / idempotency | `daily_cycle.run()` twice on same tick_date → 0 new state rows; 1 `TICK_COMPLETED` (dedup) | xvii, xxiv |
| 5 | Transactionality | Mock DB error mid-accepted `place_order` → rollback leaves NO `paper_order` AND NO audit row | xxvii |
| 6 | Transactionality | Mock DB error during `ORDER_REJECTED` audit → caller sees DB error, NOT `OrderRejected`; no audit row | ix, xxvii |
| 7 | Single-writer | `grep -rn 'session\.add\(\|session\.execute(insert\|session\.execute(update' marketpulse/` returns matches ONLY inside `marketpulse/trading/repository.py` | iii, viii |
| 8 | Lifecycle correctness | E2E: place_order → PLACED → tick(entry_date) → ENTRY_FILLED + position OPEN → tick(horizon) → CLOSED + EXIT fill recorded | xi, xix |
| 9 | Lifecycle correctness | `grep -rn '"FILLED"\|ORDER_FILLED' marketpulse/` returns ZERO matches (must be `ENTRY_FILLED` / `ORDER_ENTRY_FILLED`) | xix |
| 10 | Ledger correctness | `Σ paper_cash_ledger.delta == (SELECT balance_after FROM paper_cash_ledger ORDER BY id DESC LIMIT 1)` after every fixture | xvi, xxi |
| 11 | Ledger correctness | Property: 100 random cash movements with same timestamp → `balance_after` monotonic by `id`, not timestamp | xxi |
| 12 | Stateful flow | Full E2E with `FakeClock`: seed evaluation_event → `daily_cycle.run(D0)` → `daily_cycle.run(D5)` → `paper_cash_ledger` shows entry + exit deltas, `realized_pnl` matches Phase 5 math for the same inputs | end-to-end (xxiii, xi, x, xvi) |
| 13 | Fail-closed risk | `RiskGate.check_pre_trade` raises arbitrary exception → `place_order` does NOT swallow it; `ORDER_REJECTED` audit records exception type + message; NO `paper_order` row | iv, ix |
| 14 | Audit completeness | After N E2E operations, `count(paper_audit_event) >= count(state_transitions)`. No silent state changes. | v, ix |
| 15 | CQRS boundary | `ExecutionEngine` Protocol has EXACTLY 3 methods. Test asserts `set(dir(ExecutionEngine)) - {magic}` equals expected. | viii |
| 16 | Forward-only recovery | Test fixture: simulate 3-day downtime. Restart → `daily_cycle.run` writes 1 `SCHEDULER_GAP_DETECTED` audit; subsequent rerun on same day writes 0 additional gap audit rows | xxxiii |
| 17 | Determinism | `daily_cycle.run` produces same `allocation_run_id` across reruns of same tick_date (value: `f"paper-{tick_date}"`) | xxx |
| 18 | Tick result accuracy | `TickResult.entries_materialized` counts actual `PLACED → ENTRY_FILLED` transitions, not net OPEN count diff | (new round-3 lock) |

### 9.4 6a-4 E2E test (canonical)

```python
# tests/trading/test_e2e_stateful.py

# Layer: stateful
def test_full_lifecycle_place_to_close(test_session, fake_clock, ny_calendar):
    """D0: place → tick(entry) → ENTRY_FILLED + OPEN
       D1-D4: idle ticks
       D5 (horizon): tick(exit) → CLOSED + cash delta correct"""
    D0 = date(2026, 5, 21)
    D5 = date(2026, 5, 28)

    # Seed: one evaluation_event on D0 with horizon_date=D5
    seed_evaluation_event(test_session, event_date=D0, ticker="AAPL",
                          horizon_days=5, strategy="...")

    # D0 tick
    fake_clock.set_ny(D0, hour=17, minute=30)
    r0 = daily_cycle.run(clock=fake_clock, engine=engine, ...)
    assert r0.orders_placed == 1
    assert r0.entries_materialized == 1
    assert paper_positions_with_status(test_session, "OPEN") == 1

    # D1-D4 idle
    for day in [date(2026, 5, 22), date(2026, 5, 25),
                date(2026, 5, 26), date(2026, 5, 27)]:
        fake_clock.set_ny(day, hour=17, minute=30)
        ri = daily_cycle.run(...)
        assert ri.orders_placed == 0
        assert ri.exits_materialized == 0

    # D5 horizon
    fake_clock.set_ny(D5, hour=17, minute=30)
    r5 = daily_cycle.run(...)
    assert r5.exits_materialized == 1
    assert paper_positions_with_status(test_session, "CLOSED") == 1

    # Cash invariant
    final_balance = current_cash_balance(test_session)
    initial = Decimal("10000")
    expected = initial + (horizon_price - entry_price) * quantity
    assert final_balance == expected
```

---

## 10 — Configuration

New settings in `marketpulse/config.py`:

```python
paper_tick_hour: int        = Field(17, alias="MP_PAPER_TICK_HOUR")
paper_tick_minute: int      = Field(30, alias="MP_PAPER_TICK_MINUTE")
paper_initial_deposit: str  = Field("10000", alias="MP_PAPER_INITIAL_DEPOSIT")
paper_kill_switch: bool     = Field(False, alias="MP_PAPER_KILL_SWITCH")
```

The kill switch DB row is the canonical source; env var is a *force-on* override (env=True → kill switch active regardless of DB; env=False → DB value used).

---

## 11 — Section Locks

This spec introduces no new locks beyond those already in the umbrella (umbrella has 32 architectural locks). It documents how each lock manifests in code:

| Umbrella lock | 6a code-level manifestation |
|---|---|
| i — canonical state in DB | `paper_*` tables + `repository.py` as sole writer |
| ii — engine owns clock | `ForwardExecutionEngine.tick(as_of)` is the only mutation driver |
| iii — single writer | `repository.py` import-linter rule |
| iv — fail-closed risk | `AlwaysApproveRiskGate` Protocol shape (real gates are 6b) |
| v — audit append-only | `repository.py` exposes only `write_audit_event`; no update / delete |
| vi — Protocol structural | `ExecutionEngine` Protocol in `execution_engine.py` |
| vii — tick clock advancement | `ForwardExecutionEngine.tick` is the sole driver |
| viii — all mutation via engine | `repository.py` only callable from `forward_engine.py`, `kill_switch.py`, `daily_cycle.py` |
| ix — rejection writes audit | `place_order` raises `OrderRejected` ONLY after audit commit |
| x — shared allocator | `marketpulse/backtest/allocation.py` called by both backtest + 6a-3 |
| xi — order ≠ position lifecycle | separate `_materialize_entry` / `_materialize_exit` |
| xii — horizon_price typing | `Decimal | None`; ForwardEngine raises `InvariantError` on None at exit |
| xiii — append-only tables | repository API exposes only `insert_*` for fill / ledger / audit |
| xiv — position UNIQUE on order | DB constraint |
| xv — no Phase 1-5 schema changes | migration adds tables only |
| xvi — cash from ledger | `repository.cash_balance()` does `ORDER BY id DESC LIMIT 1` |
| xvii — idempotency UNIQUE | DB UNIQUE constraint + `compute_idempotency_key` |
| xviii — allocation_run_id | deterministic `f"paper-{tick_date}"` |
| xix — status vocabulary | `PLACED | ENTRY_FILLED | CANCELLED` in CHECK constraint; grep test asserts no "FILLED" |
| xx — orders only for winners | `daily_cycle` only calls `place_order` on `allocation_result.winners` |
| xxi — ledger balance via id | `repository.cash_balance` SQL |
| xxii — Decimal at persistence | `Numeric(18, 6)` columns; `OrderRequest.from_winner` is the quantization site |
| xxiii — Clock injection | `ForwardExecutionEngine.__init__(*, clock)`; grep test for `date.today` |
| xxiv — tick idempotent | filter by status; second call returns `TickResult(0, 0)` |
| xxv — scheduler thin | `paper_trading_tick_job` <= 30 lines; grep test asserts no SQL |
| xxvi — `# Layer: stateful` | pytest hook extended to accept third value |
| xxvii — `place_order` transactional | accepted INSERT + audit in single `with transaction()` |
| xxviii — versioning fields | `paper_order.{strategy_version, allocator_version, execution_engine_version}` populated |
| xxix — UTC + NY tz | `TZDateTime` columns; `NYTradingCalendar` does day arithmetic |
| xxx — idempotency before risk | `place_order` body order: key → existing? → kill → risk → INSERT |
| xxxi — merged into xxii | (reserved) |
| xxxii — canonical calendar | `marketpulse/trading/calendar.py` |
| xxxiii — forward-only recovery | `daily_cycle.run` skips missed days; emits `SCHEDULER_GAP_DETECTED` |

---

## 12 — Forward-warnings to 6b / 6f / 6g / Phase 7

### To 6b (risk gates)

- `RiskGate` Protocol is locked in 6a. 6b adds real implementations: sector cap, correlation cap, daily loss limit, market-hours, drawdown halt. 6b composes them into a single `CompositeRiskGate(...)` that replaces `AlwaysApproveRiskGate` at the DI seam.
- Kill switch stays in 6a's `kill_switch.py`. 6b does NOT touch it.
- 6b extends the `paper_audit_event` CHECK constraint via its own migration to include `RISK_GATE_BLOCKED` (or similar) event type.

### To 6f (UI)

- `/lab/paper-trading` reads `paper_*` tables via a new `query_models.py` (deferred from 6a). The read-path module imports `db.models` but never `repository.py`.
- UI may call `engine.cancel_order(order_id)` for the manual-cancel use case.
- Kill switch toggle exposed via UI button → calls `kill_switch.flip(reason="manual_ui", actor=current_user)`.

### To 6g (observability)

- Push notifications consume `paper_audit_event` rows. The subscription / fanout layer is 6g's design.
- Recap integration: existing `marketpulse/recap/` extends to include paper-trading P&L summary (reading from `paper_cash_ledger` + `paper_position`).
- 6g extends audit event types: `RECAP_GENERATED`, `PUSH_DELIVERED`, alerts derived from gap-detection metrics.
- Counter metrics: `missed_business_days_total`, `scheduler_gap_events_total`, `tick_errors_total`.

### To Phase 7 (real broker)

The 6a foundation hands Phase 7 a clean substrate. Phase 7's `BrokerExecutionEngine`:

- Implements the same `ExecutionEngine` Protocol; downstream code (UI, observability, risk gates, allocator) requires zero changes.
- Reuses `paper_audit_event` schema; may add broker-side event types (`BROKER_FILL_REPORTED`, `BROKER_REJECT`, etc.).
- Reuses `repository.py` writes; may extend with broker-id mapping helpers.
- Splits `paper_order.horizon_price` into `expected_horizon_price` + `realized_horizon_price` (umbrella PP1).
- Re-evaluates Postgres (umbrella Q1 forward lock): if real broker reconciliation, multi-user access, streaming writes, or DB-side aggregation arrive, Postgres becomes a Phase 7 prerequisite.

---

## 13 — Deliverables Summary

Single PR `feat(phase-6a): paper trading foundation`. Branches from main → `plan/phase-6a-paper-trading-foundation` → merge to main at end of 6a-4.

**New files (14):**
- `marketpulse/trading/__init__.py`
- `marketpulse/trading/types.py`
- `marketpulse/trading/execution_engine.py`
- `marketpulse/trading/forward_engine.py`
- `marketpulse/trading/repository.py`
- `marketpulse/trading/clock.py`
- `marketpulse/trading/calendar.py`
- `marketpulse/trading/kill_switch.py`
- `marketpulse/trading/idempotency.py`
- `marketpulse/trading/risk_gate.py`
- `marketpulse/trading/bid_aggregator.py`
- `marketpulse/trading/daily_cycle.py`
- `marketpulse/scheduler/paper_trading_tick.py`
- `marketpulse/backtest/allocation.py`

**Modified files (6):**
- `marketpulse/db/models.py` — append 5 model classes
- `marketpulse/backtest/portfolio_simulator.py` — calls `allocate_for_day(...)` per day
- `marketpulse/scheduler/jobs.py` — register `paper_trading_tick_job`
- `marketpulse/main.py` — call `ensure_initial_deposit()` at startup
- `marketpulse/config.py` — add 4 new settings
- `pyproject.toml` — add `exchange_calendars` dependency
- `tests/conftest.py` — extend `# Layer:` enforcement to accept `stateful`

**New migration:**
- `alembic/versions/0010_phase6_paper_trading.py`

**New tests (13):**
- `tests/trading/test_types.py`
- `tests/trading/test_clock.py`
- `tests/trading/test_calendar.py`
- `tests/trading/test_idempotency.py`
- `tests/trading/test_kill_switch.py`
- `tests/trading/test_risk_gate.py`
- `tests/trading/test_repository.py`
- `tests/trading/test_forward_engine.py`
- `tests/trading/test_bid_aggregator.py`
- `tests/trading/test_daily_cycle.py`
- `tests/trading/test_scheduler.py`
- `tests/trading/test_e2e_stateful.py`
- `tests/backtest/test_allocation_extraction.py`

**Phase 5 regression contract (6a-0):** the full existing test suite continues to pass; `PortfolioBacktestResult` public fields + `bid_history` records equal pre/post extraction across all Phase 5 fixtures (warm-pool included). Versioned/provenance fields may differ if they intentionally encode the extraction.

---

**End of 6a spec.**
