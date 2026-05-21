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
| **6a-0** | Extract `allocate_for_day(...)` pure-function kernel from Phase 5 `simulate_shared_pool`. Refactor `portfolio_simulator.py` to call it once per historical day. **Extraction boundary (6a-L1):** ONLY the BID → SIZE → DEDUP → ALLOC kernel is lifted out. CLOSE, MTM, RECORD, equity-curve update, contribution decomposition, rolling-stats finalization all STAY in `portfolio_simulator.py`. The 5d/5e contribution machinery is not touched. | Phase 5 full regression: behavioral + public-field equality on existing fixtures (warm-pool included). Pre-extraction vs post-extraction `PortfolioBacktestResult.bid_history`, KPIs, sector breakdown all equal field-by-field, excluding any intentionally versioned/provenance fields. |
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
                                                  BidCandidate, AllocationContext (6a-L9 —
                                                  explicit allocation_date, target_vol,
                                                  sector_caps, correlation_caps,
                                                  contribution_enabled, pool_corr_mode,
                                                  phase5e_warm_pool_overlap_days),
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
| `TICK_COMPLETED` | NULL | `daily_cycle.run` end (first complete-or-with-errors run for a `tick_date`) | **`tick_date` (required)**, **`status`** (`completed` \| `completed_with_errors`), `allocation_run_id`, `bids_collected`, `orders_placed`, `orders_rejected`, `duplicates_skipped`, `entries_materialized`, `exits_materialized`, `cash_balance_end` |
| `TICK_REPROCESSED_COMPLETED` | NULL | `daily_cycle.run` when prior `TICK_COMPLETED` for the same `tick_date` had `status=completed_with_errors` and the new run has `status=completed` (recovery after data fix) | `tick_date`, `prior_status`, `new_status`, `prior_tick_completed_id`, plus all the regular `TICK_COMPLETED` context keys |
| `KILL_SWITCH_CYCLE_SKIPPED` | NULL | `daily_cycle.run` cycle-level short-circuit when kill switch is active | `tick_date`, `mode: "kill_switch_active"`, `tick_result` (still records any exits processed) |
| `SCHEDULER_GAP_DETECTED` | NULL | `daily_cycle.run` gap branch | `last_processed_tick_date`, `resume_date`, `missed_business_days`, `mode` |
| `ENGINE_INVARIANT_ERROR` | NULL | `tick` invariant violation | `phase`, `position_id` or `order_id`, `error`, `as_of` |

`AuditEventType` is a string enum in `marketpulse/trading/types.py`. 6b / 6f / 6g extend the migration's CHECK constraint as they add types.

**Note on SQLite CHECK extension:** SQLite cannot `ALTER` a column-level `CHECK` constraint; extending the audit event-type enumeration in 6b / 6g requires a table-rebuild migration pattern (`CREATE TABLE paper_audit_event_new`, `INSERT … SELECT *`, drop+rename). Acceptable at 6b/6g scale (table is append-only and bounded). This is a forward-warning, not a 6a-1 issue.

**Indexes:**

```
INDEX (timestamp)
INDEX (event_type, timestamp)                      -- last_processed_tick_date query
INDEX (order_id)                                   -- per-order provenance
INDEX (strategy, timestamp)
```

**CHECK:**

```sql
CHECK (event_type IN ('ORDER_PLACED', 'ORDER_PLACED_DUPLICATE', 'ORDER_REJECTED',
                      'ORDER_CANCELLED', 'ORDER_ENTRY_FILLED', 'POSITION_CLOSED',
                      'KILL_SWITCH_FLIPPED', 'KILL_SWITCH_CYCLE_SKIPPED',
                      'TICK_COMPLETED', 'TICK_REPROCESSED_COMPLETED',
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
class PlaceOrderResult:
    """Returned by place_order(). The (created, duplicate) flags eliminate
    the TOCTOU race that a separate idempotency pre-check would introduce
    in callers (6a-L2)."""
    order_id: OrderId
    created: bool          # True if a new paper_order row was inserted
    duplicate: bool        # True if the call hit an existing idempotency_key


@dataclass(frozen=True)
class TickError:
    """Structured invariant-error record (6a-L4)."""
    phase: Literal["entry_materialization", "exit_materialization"]
    order_id: int | None
    position_id: int | None
    error: str             # short reason; full context goes into the audit row


@dataclass(frozen=True)
class TickResult:
    """Returned by ExecutionEngine.tick() so callers know what actually
    happened. Net-OPEN counting from outside is unreliable for same-tick
    open+close edge cases."""
    as_of: date
    entries_materialized: int      # PLACED → ENTRY_FILLED count
    exits_materialized: int        # OPEN → CLOSED count
    errors: tuple[TickError, ...]  # structured (6a-L4)


class ExecutionEngine(Protocol):
    """Command-only Protocol. Reads of canonical state happen via
    DB query helpers in repository.py (execution-path) or future
    query_models.py (UI/observability — deferred to 6f/6g).

    Phase 6 ships:   ForwardExecutionEngine
    Phase 7 will add: BrokerExecutionEngine
    Stretch (6d):     RealtimeExecutionEngine

    All three implement the SAME Protocol. Downstream callers do not
    know which one is running."""

    def place_order(self, *, order_request: OrderRequest) -> PlaceOrderResult: ...

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
def place_order(self, *, order_request: OrderRequest) -> PlaceOrderResult:
    # Step 1: deterministic idempotency key (pure; no DB)
    key = compute_idempotency_key(order_request)

    # Step 2: idempotency check
    existing = self._repo.find_paper_order_by_idempotency_key(key)
    if existing is not None:
        # ORDER_PLACED_DUPLICATE deduped per (idempotency_key, tick_date)
        # via repository.write_duplicate_audit_once (6a-L5).
        self._repo.write_duplicate_audit_once(
            idempotency_key=key,
            order_id=existing.id,
            strategy=order_request.strategy,
            tick_date=order_request.allocation_date,
            context={"allocation_run_id": order_request.allocation_run_id},
        )
        return PlaceOrderResult(
            order_id=OrderId(existing.id),
            created=False,
            duplicate=True,
        )

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
    # Exceptions from the gate are fail-closed (lock iv + 6a-L3): an
    # unexpected error becomes ORDER_REJECTED, not a swallowed exception.
    try:
        risk_result = self._risk_gate.check_pre_trade(order_request=order_request)
    except Exception as e:
        self._repo.write_audit_event(
            event_type=AuditEventType.ORDER_REJECTED,
            order_id=None,
            strategy=order_request.strategy,
            reason="risk_gate_error",
            context={"order_request": _dump(order_request),
                     "error_type": type(e).__name__,
                     "error": str(e)},
        )
        raise OrderRejected("risk_gate_error") from e

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

    return PlaceOrderResult(
        order_id=OrderId(order.id),
        created=True,
        duplicate=False,
    )
```

**Invariants:**

| # | Invariant |
|---|---|
| A | Step order is **idempotency → kill switch → risk gate → atomic INSERT**. Tests grep the function body to confirm. |
| B | `OrderRejected` is raised ONLY after the `ORDER_REJECTED` audit row commits. If audit write fails, the DB error surfaces. |
| C | Rejections create NO `paper_order` row. |
| D | Idempotency hits write `ORDER_PLACED_DUPLICATE` **at most once per `(idempotency_key, tick_date)` pair** via `repository.write_duplicate_audit_once` (6a-L5). |
| E | Accepted INSERT + `ORDER_PLACED` audit are in a single transaction. |
| F | `RiskGate` exceptions are fail-closed: `ORDER_REJECTED` audit row written, then `OrderRejected("risk_gate_error")` raised (6a-L3). |
| G | Return value is `PlaceOrderResult(order_id, created, duplicate)` — no caller needs to pre-check idempotency (6a-L2). |

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
    errors: list[TickError] = []

    # Phase A: entries for PLACED orders whose allocation_date <= as_of
    pending_entries = self._repo.find_orders_for_entry(as_of=as_of)
    for order in pending_entries:
        try:
            self._materialize_entry(order, fill_date=as_of)
            entries += 1
        except InvariantError as e:
            err = TickError(
                phase="entry_materialization",
                order_id=order.id,
                position_id=None,
                error=str(e),
            )
            errors.append(err)
            # ALWAYS write ENGINE_INVARIANT_ERROR audit (6a-L4)
            self._repo.write_audit_event(
                event_type=AuditEventType.ENGINE_INVARIANT_ERROR,
                order_id=order.id,
                strategy=order.strategy,
                reason="invariant_error",
                context={
                    "phase": err.phase,
                    "order_id": err.order_id,
                    "error": err.error,
                    "as_of": as_of.isoformat(),
                },
            )

    # Phase B: exits for OPEN positions whose horizon_date <= as_of
    pending_exits = self._repo.find_positions_for_exit(as_of=as_of)
    for position in pending_exits:
        try:
            self._materialize_exit(position, exit_date=as_of)
            exits += 1
        except InvariantError as e:
            err = TickError(
                phase="exit_materialization",
                order_id=position.order_id,
                position_id=position.id,
                error=str(e),
            )
            errors.append(err)
            self._repo.write_audit_event(
                event_type=AuditEventType.ENGINE_INVARIANT_ERROR,
                order_id=position.order_id,
                strategy=position.strategy,
                reason="invariant_error",
                context={
                    "phase": err.phase,
                    "position_id": err.position_id,
                    "order_id": err.order_id,
                    "error": err.error,
                    "as_of": as_of.isoformat(),
                },
            )

    return TickResult(
        as_of=as_of,
        entries_materialized=entries,
        exits_materialized=exits,
        errors=tuple(errors),
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
    orders_placed: int             # PlaceOrderResult.created count
    orders_rejected: int
    duplicates_skipped: int        # PlaceOrderResult.duplicate count
    entries_materialized: int      # from TickResult
    exits_materialized: int        # from TickResult
    tick_errors: tuple[TickError, ...]   # structured (6a-L4)
    cycle_status: Literal["completed", "completed_with_errors", "kill_switch_skipped"]
    cash_balance_end: Decimal


def run(
    *,
    clock: Clock,
    engine: ExecutionEngine,
    repository: Repository,
    bid_aggregator: BidAggregator,
    allocator: AllocateForDay,     # alias for the allocate_for_day function
    calendar: TradingCalendar,
    kill_switch: KillSwitchState,
) -> DailyCycleResult:
    """One business-day forward step. Idempotent across reruns of the same
    tick_date (deterministic allocation_run_id, idempotent TICK_COMPLETED).

    Phase 6a contract: exactly one authoritative allocation run per NY
    trading day. Manual rerun reuses the same allocation_run_id and is
    treated as replay, not a new run (6a-L7 — clarifying lock).
    Future multi-run-per-day support would extend allocation_run_id to
    include a run-mode discriminator."""

    tick_date = calendar.today_ny_trading_date(clock.now())
    allocation_run_id = AllocationRunId(f"paper-{tick_date.isoformat()}")

    # ---- Phase 1: gap detection (deduped per gap window) ----
    last_processed = repository.last_processed_tick_date()
    if last_processed is not None and last_processed < tick_date:
        missed = calendar.business_days_between(last_processed, tick_date) - 1
        if missed > 0:
            repository.write_gap_audit_once(
                last_tick=last_processed,
                resume_date=tick_date,
                missed_business_days=missed,
            )

    # ---- Phase 1.5: kill-switch cycle-level short-circuit (6a-L8) ----
    # When the kill switch is active, the cycle does NOT collect bids,
    # does NOT allocate, does NOT place new orders. It STILL calls
    # engine.tick(as_of=tick_date) so existing OPEN positions can close
    # at horizon (otherwise positions would be trapped open forever).
    # The engine internally short-circuits place_order on its own
    # kill-switch check too — defense in depth.
    if kill_switch.is_active():
        tick_result = engine.tick(as_of=tick_date)
        repository.write_audit_event(
            event_type=AuditEventType.KILL_SWITCH_CYCLE_SKIPPED,
            order_id=None,
            strategy=None,
            reason="kill_switch_active",
            context={
                "tick_date": tick_date.isoformat(),
                "mode": "kill_switch_active",
                "tick_entries_materialized": tick_result.entries_materialized,
                "tick_exits_materialized": tick_result.exits_materialized,
                "tick_errors": [
                    {"phase": e.phase, "order_id": e.order_id,
                     "position_id": e.position_id, "error": e.error}
                    for e in tick_result.errors
                ],
            },
        )
        return DailyCycleResult(
            tick_date=tick_date,
            allocation_run_id=allocation_run_id,
            bids_collected=0,
            orders_placed=0,
            orders_rejected=0,
            duplicates_skipped=0,
            entries_materialized=tick_result.entries_materialized,
            exits_materialized=tick_result.exits_materialized,
            tick_errors=tick_result.errors,
            cycle_status="kill_switch_skipped",
            cash_balance_end=repository.cash_balance(),
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
    # No pre-check race (6a-L2): the engine's PlaceOrderResult tells us
    # whether the call created a new row or hit an existing one.
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
        try:
            result = engine.place_order(order_request=request)
            if result.created:
                placed += 1
            elif result.duplicate:
                duplicates += 1
        except OrderRejected:
            rejected += 1

    # ---- Phase 5: tick (entries then exits, returns counts + structured errors) ----
    tick_result = engine.tick(as_of=tick_date)

    # ---- Phase 6: TICK_COMPLETED audit (idempotent per tick_date) ----
    cycle_status: Literal["completed", "completed_with_errors"] = (
        "completed_with_errors" if tick_result.errors else "completed"
    )
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
        cycle_status=cycle_status,
        cash_balance_end=repository.cash_balance(),
    )

    # repository.write_tick_completed_once handles both first-run and
    # recovery-after-fix semantics (6a-L8):
    #   - If no prior TICK_COMPLETED for tick_date: append TICK_COMPLETED
    #     with this run's status.
    #   - If prior TICK_COMPLETED.status == "completed_with_errors" AND
    #     this run is "completed": append TICK_REPROCESSED_COMPLETED
    #     (the original TICK_COMPLETED row is NOT modified — audit table
    #     is append-only per lock xiii).
    #   - Otherwise (prior TICK_COMPLETED status matches this run, or
    #     prior was "completed"): no-op.
    repository.write_tick_completed_once(
        tick_date=tick_date,
        context={
            "tick_date": tick_date.isoformat(),         # canonical query key (§ 4.5)
            "status": cycle_status,                     # 6a-L5 — completed | completed_with_errors
            "allocation_run_id": allocation_run_id,
            "bids_collected": result.bids_collected,
            "orders_placed": result.orders_placed,
            "orders_rejected": result.orders_rejected,
            "duplicates_skipped": result.duplicates_skipped,
            "entries_materialized": result.entries_materialized,
            "exits_materialized": result.exits_materialized,
            "tick_errors": [
                {"phase": e.phase, "order_id": e.order_id,
                 "position_id": e.position_id, "error": e.error}
                for e in result.tick_errors
            ],
            "cash_balance_end": str(result.cash_balance_end),
        },
    )

    return result
```

**`write_tick_completed_once` decision table** (repository internal logic — 6a-L8):

| Prior `TICK_COMPLETED` for `tick_date`? | Prior `status` | New `status` | Action |
|---|---|---|---|
| no | — | `completed` or `completed_with_errors` | INSERT `TICK_COMPLETED` |
| yes | `completed` | any | no-op (already terminal) |
| yes | `completed_with_errors` | `completed_with_errors` | no-op (same state) |
| yes | `completed_with_errors` | `completed` | INSERT `TICK_REPROCESSED_COMPLETED` (records the recovery; original `TICK_COMPLETED` row remains, append-only) |

Recovery audit trail: a future query for "did this tick ever fully succeed?" reads the latest event for `tick_date` ordered by `id DESC` and accepts both `TICK_COMPLETED(status=completed)` and `TICK_REPROCESSED_COMPLETED` as success states.

### 7.2 Determinism + idempotency contract

| What | How |
|---|---|
| `allocation_run_id` per day | Deterministic: `f"paper-{tick_date.isoformat()}"`. Same-day rerun threads the SAME id into every OrderRequest. Phase 6a permits exactly one authoritative allocation run per NY trading day (6a-L7). |
| Idempotency key per order | Deterministic from `(strategy, ticker, event_time, allocation_run_id)`. Same-day rerun produces the same keys → `PlaceOrderResult.duplicate=True`. |
| `ORDER_PLACED_DUPLICATE` row | `repository.write_duplicate_audit_once(idempotency_key, tick_date, ...)` is a no-op if an audit with the same `(idempotency_key, tick_date)` already exists (6a-L5). |
| `TICK_COMPLETED` row | `repository.write_tick_completed_once(tick_date, ...)` is a no-op if a `TICK_COMPLETED` audit with the same `context.tick_date` already exists. `context.status` distinguishes `completed` vs `completed_with_errors` (6a-L5). |
| `SCHEDULER_GAP_DETECTED` row | `repository.write_gap_audit_once(...)` is a no-op if an audit with the same `(last_processed_tick_date, resume_date)` already exists. |
| `tick()` row materialization | Filters by `status = 'PLACED' / 'OPEN'`; once flipped, subsequent ticks find zero rows. |

A second `daily_cycle.run()` call on the same NY trading day produces:

- 0 new `paper_order` rows (all idempotency keys hit existing rows)
- 0 new `ORDER_PLACED_DUPLICATE` audits (deduped per (idempotency_key, tick_date) — 6a-L5)
- 0 new `paper_fill` rows (orders already ENTRY_FILLED → not in PLACED query)
- 0 new `paper_position` rows
- 0 new `paper_cash_ledger` rows
- 0 new `TICK_COMPLETED` rows (dedup)
- 0 new `SCHEDULER_GAP_DETECTED` rows (dedup)
- `DailyCycleResult.duplicates_skipped == N` reflects winners that hit existing keys

### 7.3 Forward-only downtime recovery (lock xxxiii)

- BidAggregator considers **only events with `event_date == tick_date`** (today).
- Events from prior dates are forever skipped from a paper-trading perspective. They remain in `evaluation_event` for Phase 5 backtest use.
- When gap is detected, `SCHEDULER_GAP_DETECTED` audit is written with `{last_processed_tick_date, resume_date, missed_business_days, mode: "forward_only_skip"}`.
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
    """Idempotent. INSERT the INITIAL_DEPOSIT row only if paper_cash_ledger is empty.

    Uses the same transaction discipline as every other paper_* write (no
    naked session.commit() — repository's only commit surface is
    self.transaction())."""
    with self.transaction():
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
        # self.transaction() context manager commits on exit. No naked commit.
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
            kill_switch=kill_switch,
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
                                   # Assert frozen-ness only — do NOT assert
                                   # hashability across the board (future fields
                                   # may include list/dict context payloads).
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
| 2 | Replay / idempotency | `place_order(req)` twice with same key → `PlaceOrderResult(duplicate=True)` second call; exactly 1 `ORDER_PLACED_DUPLICATE` audit per (idempotency_key, tick_date) across N replays; 0 new `paper_order` | xvii, xxx, 6a-L2, 6a-L5 |
| 3 | Replay / idempotency | `tick(as_of=D)` twice → second call processes 0 rows; `TickResult(entries=0, exits=0, errors=())` | xxiv |
| 4 | Replay / idempotency | `daily_cycle.run()` twice on same tick_date → 0 new state rows; 1 `TICK_COMPLETED` (dedup); `DailyCycleResult.duplicates_skipped == N` on second call | xvii, xxiv, 6a-L5 |
| 5 | Transactionality | Mock DB error mid-accepted `place_order` → rollback leaves NO `paper_order` AND NO audit row | xxvii |
| 6 | Transactionality | Mock DB error during `ORDER_REJECTED` audit → caller sees DB error, NOT `OrderRejected`; no audit row | ix, xxvii |
| 7 | Single-writer | `grep -rn 'session\.add\(\|session\.execute(insert\|session\.execute(update' marketpulse/` returns matches ONLY inside `marketpulse/trading/repository.py` | iii, viii |
| 8 | Lifecycle correctness | E2E: place_order → PLACED → tick(entry_date) → ENTRY_FILLED + position OPEN → tick(horizon) → CLOSED + EXIT fill recorded | xi, xix |
| 9 | Lifecycle correctness | `grep -rnE '"FILLED"\|\bORDER_FILLED\b' marketpulse/` returns ZERO matches (word boundary on ORDER_FILLED so we don't false-match ORDER_ENTRY_FILLED; the only legal status string is `ENTRY_FILLED` / event type `ORDER_ENTRY_FILLED`) | xix |
| 10 | Ledger correctness | `Σ paper_cash_ledger.delta == (SELECT balance_after FROM paper_cash_ledger ORDER BY id DESC LIMIT 1)` after every fixture | xvi, xxi |
| 11 | Ledger correctness | Property: 100 random cash movements with same timestamp → `balance_after` monotonic by `id`, not timestamp | xxi |
| 12 | Stateful flow | Full E2E with `FakeClock`: seed evaluation_event → `daily_cycle.run(D0)` → `daily_cycle.run(D5)` → `paper_cash_ledger` shows entry + exit deltas, `realized_pnl` matches Phase 5 math for the same inputs | end-to-end (xxiii, xi, x, xvi) |
| 13 | Fail-closed risk | `RiskGate.check_pre_trade` raises arbitrary exception → `place_order` does NOT swallow it; `ORDER_REJECTED` audit records exception type + message; NO `paper_order` row | iv, ix |
| 14 | Audit completeness | After N E2E operations, `count(paper_audit_event) >= count(state_transitions)`. No silent state changes. | v, ix |
| 15 | CQRS boundary | `ExecutionEngine` Protocol has EXACTLY 3 methods. Test asserts `set(dir(ExecutionEngine)) - {magic}` equals expected. | viii |
| 16 | Forward-only recovery | Test fixture: simulate 3-day downtime. Restart → `daily_cycle.run` writes 1 `SCHEDULER_GAP_DETECTED` audit; subsequent rerun on same day writes 0 additional gap audit rows | xxxiii |
| 17 | Determinism | `daily_cycle.run` produces same `allocation_run_id` across reruns of same tick_date (value: `f"paper-{tick_date}"`) | xxx, 6a-L7 |
| 19 | Status transitions | `repository.update_paper_order_status(PLACED→ENTRY_FILLED)` succeeds; `(ENTRY_FILLED→PLACED)`, `(CANCELLED→ENTRY_FILLED)`, `(ENTRY_FILLED→CANCELLED)` all raise `InvariantError`. Same enforcement for `paper_position`. | 6a-L6 |
| 20a | Risk-gate fail-closed (audit OK) | `RiskGate.check_pre_trade` raises `RuntimeError("boom")` AND audit insert succeeds → exactly one `ORDER_REJECTED` audit (reason=`risk_gate_error`, context includes `error_type="RuntimeError"`) → caller catches `OrderRejected("risk_gate_error")` → 0 paper_order rows. | iv, ix, 6a-L3 |
| 20b | Risk-gate fail-closed (audit fails) | `RiskGate.check_pre_trade` raises `RuntimeError("boom")` AND `write_audit_event` raises `OperationalError` → caller sees the DB error (NOT `OrderRejected`); no `paper_order` row; no `paper_audit_event` row. A rejection without audit is not a valid completed rejection (lock ix). | iv, ix, 6a-L3 |
| 21 | TICK_COMPLETED status | Tick with 1 entry success + 1 exit `InvariantError` → `TICK_COMPLETED.context.status == "completed_with_errors"`; `last_processed_tick_date()` returns this `tick_date`; 1 `ENGINE_INVARIANT_ERROR` audit row exists. | 6a-L4, 6a-L5 |
| 22 | PlaceOrderResult contract | `place_order(req)` first call returns `PlaceOrderResult(created=True, duplicate=False)`; second call returns `PlaceOrderResult(created=False, duplicate=True)` with same `order_id`. No caller pre-checks. | 6a-L2 |
| 23 | Extraction boundary | `marketpulse/backtest/allocation.py` does NOT import/reference: equity-curve, MTM, CLOSE step, contribution decomposition, rolling-stats finalization. Grep test asserts the absence list. | 6a-L1 |
| 24 | Kill switch cycle-level | Active kill switch + `daily_cycle.run()` with N seeded events → 0 `paper_order` rows, 0 `ORDER_REJECTED` rows, exactly 1 `KILL_SWITCH_CYCLE_SKIPPED` audit, AND `engine.tick(as_of=tick_date)` still ran (verifiable by seeding an OPEN position with `horizon_date <= tick_date` and confirming it closes). | iv, 6a-L8 |
| 25 | Kill switch defense-in-depth | Call `engine.place_order(...)` directly with kill switch active (bypassing `daily_cycle`) → `OrderRejected("kill_switch_active")` raised after exactly one `ORDER_REJECTED` audit. The engine-level check is the second layer of the defense (6a-L8). | iv, 6a-L8 |
| 26 | Recovery audit | Seed a `tick_date` with `TICK_COMPLETED.status=completed_with_errors`. Fix the underlying data. Rerun `daily_cycle.run()` for the same `tick_date` → original row remains; new `TICK_REPROCESSED_COMPLETED` row appended with `prior_status=completed_with_errors` and `new_status=completed`. | 6a-L5, 6a-L8 |
| 27 | Replay across deploys | Seed paper_order on D0 with `allocator_version=v1`. Bump `allocator_version=v2`. Rerun `daily_cycle.run()` on D0 → no new orders (`PlaceOrderResult.duplicate=True` on every winner); the existing `paper_order` row's `allocator_version` is UNCHANGED (still `v1`); 0 `ORDER_PLACED_DUPLICATE` net-new rows (deduped per `(idempotency_key, tick_date)`). | 6a-L7 |
| 28 | AllocationContext purity | Test fixture: pass an `AllocationContext` with `allocation_date=D0`, plus a partial `existing_positions` snapshot. Call `allocate_for_day(...)` twice with bit-identical inputs → results equal. Then mock `datetime.now()` to a different value during the second call → results STILL equal (no hidden `today` dependency — 6a-L9). | 6a-L1, 6a-L9 |
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

## 10.1 Pinned dependency

`pyproject.toml` adds `exchange_calendars` with a **pinned version** (e.g., `exchange_calendars >=4.5,<5.0`). Trading-day tests must remain stable across the library's holiday-data updates. Phase 6a-1 picks the exact version at implementation time; subsequent libraries upgrade only via spec amendment.

---

## 10.2 Status-transition table (enforced in `repository.py`)

`update_paper_order_status` validates the requested transition against this table; any other transition raises `InvariantError("illegal status transition")` and the audit row is NOT written (6a-L6):

| From | To | Allowed | Writer |
|---|---|---|---|
| `PLACED` | `ENTRY_FILLED` | ✅ | `_materialize_entry` |
| `PLACED` | `CANCELLED` | ✅ | `cancel_order` |
| `ENTRY_FILLED` | (any) | ❌ | terminal |
| `CANCELLED` | (any) | ❌ | terminal |

Likewise for `paper_position`:

| From | To | Allowed | Writer |
|---|---|---|---|
| `OPEN` | `CLOSED` | ✅ | `_materialize_exit` |
| `CLOSED` | (any) | ❌ | terminal |

These app-level invariants are tested explicitly in `test_repository.py` with mutation attempts that must raise.

---

## 10.3 BidAggregator strategy-tag handling

`evaluation_event` rows without a `strategy` value are SKIPPED by `BidAggregator.collect_for_date`. No `paper_audit_event` row is written in 6a — this is a pure read-side decision. 6b may add a `BID_SKIPPED_NO_STRATEGY` audit type if observability needs it. Tests in `test_bid_aggregator.py` cover both the "all events have strategy" happy path and the "mixed strategy / NULL" skip path.

---

## 10.4 JSON context access — wrapper-only

External code MUST NOT issue raw SQL like `json_extract(context, '$.tick_date')`. The repository exposes typed helpers:

```python
repository.last_processed_tick_date() -> date | None
repository.find_tick_completed_for(tick_date) -> PaperAuditEvent | None
repository.find_gap_audit_for(last_tick, resume_date) -> PaperAuditEvent | None
repository.find_duplicate_audit_for(idempotency_key, tick_date) -> PaperAuditEvent | None
```

This insulates callers from SQLite's JSON1 syntax (or any future migration to Postgres JSONB). All `json_extract(...)` SQL appears only inside `repository.py`.

---

## 11 — Section Locks

### 11.1 6a-local locks (introduced by this spec, scope = 6a only)

These are NOT umbrella-level architectural locks (umbrella stays at 32); they are 6a's internal commitments that 6b/6f/6g must honor when extending the foundation.

| # | Lock |
|---|---|
| **6a-L1** | `allocate_for_day(...)` extracts ONLY the BID → SIZE → DEDUP → ALLOC kernel. CLOSE, MTM, RECORD, equity-curve update, contribution decomposition, and rolling-stats finalization remain in `marketpulse/backtest/portfolio_simulator.py`. The 6a-0 contract is a *narrow* extraction. |
| **6a-L2** | `ExecutionEngine.place_order` returns `PlaceOrderResult(order_id, created, duplicate)`. Callers (including `daily_cycle.run`) MUST NOT pre-check idempotency via a separate `find_by_key` call — that introduces a TOCTOU race. The result flags are the single source of truth for "what happened." |
| **6a-L3** | `RiskGate` exceptions are fail-closed. Any exception raised by `risk_gate.check_pre_trade` is converted by `ForwardExecutionEngine.place_order` into an `ORDER_REJECTED` audit row (reason=`"risk_gate_error"`, context includes `error_type` + `error`) followed by `OrderRejected("risk_gate_error")`. No exception is swallowed; no `paper_order` row is created. |
| **6a-L4** | Tick errors are structured `TickError(phase, order_id, position_id, error)` objects. Every `TickError` corresponds to exactly one `ENGINE_INVARIANT_ERROR` audit row written by `ForwardExecutionEngine.tick`. `TickResult.errors` is `tuple[TickError, ...]`, not `list[str]`. |
| **6a-L5** | `TICK_COMPLETED.context.status` is `"completed"` or `"completed_with_errors"`. `repository.last_processed_tick_date()` returns the latest `tick_date` regardless of `status` — a tick with errors is still "processed" and gap detection respects it. UI / observability (6g) is responsible for surfacing `tick_errors_total > 0`. `ORDER_PLACED_DUPLICATE` is written at most once per `(idempotency_key, tick_date)` via `repository.write_duplicate_audit_once`. |
| **6a-L6** | `paper_order.status` and `paper_position.status` transitions are validated by `repository.update_*_status(...)` against the allowed-transition table (§ 10.2). Illegal transitions raise `InvariantError`; the new row is NOT written. |
| **6a-L7** | Phase 6a permits exactly ONE authoritative allocation run per NY trading day. `allocation_run_id = f"paper-{tick_date.isoformat()}"`. A same-day rerun reuses the same id and is treated as **replay**, not a new run. **Same-day rerun AFTER a code deploy (new `allocator_version` / `execution_engine_version`) is still replay**, not recomputation — `idempotency_key` does not include version fields. The version columns on `paper_order` explain the *original* allocation; they do not authorize an in-place replacement. This is intentional: re-allocating mid-day with a freshly-deployed allocator would silently shift portfolio state in ways the original placement audit cannot explain. Future multi-run-per-day support (open + close cycles) would extend `allocation_run_id` with a run-mode discriminator; 6a does not anticipate it. |
| **6a-L8** | Kill switch is enforced at TWO layers (defense in depth): (a) `daily_cycle.run` checks `kill_switch.is_active()` at cycle start; if active, the cycle SKIPS bid collection / allocation / new order placement, but STILL calls `engine.tick(as_of=tick_date)` so existing OPEN positions can close at their horizon. A single `KILL_SWITCH_CYCLE_SKIPPED` audit row records the skip. (b) `ForwardExecutionEngine.place_order` also checks the kill switch and writes per-order `ORDER_REJECTED("kill_switch_active")` for any call that bypasses the cycle gate. Recovery semantics: `TICK_COMPLETED` is append-only per `(tick_date)`; if a tick first writes `completed_with_errors` and a later same-day rerun completes cleanly, `repository.write_tick_completed_once(...)` appends `TICK_REPROCESSED_COMPLETED` (does NOT modify the original row — table is append-only per lock xiii). |
| **6a-L9** | `AllocationContext` (the dataclass passed into `allocate_for_day`) carries every input the allocator needs as an explicit named field — `allocation_date`, `target_vol`, `sector_caps`, `correlation_caps`, `contribution_enabled`, `pool_corr_mode`, `phase5e_warm_pool_overlap_days`, etc. The allocator MUST NOT read any state outside `AllocationContext`, `SizingContext`, `bids`, `existing_positions`, or `cash_available`. No hidden `today` dependency, no environment lookup, no DB read. This is what makes `allocate_for_day` truly pure (6a-L1 companion). |

### 11.2 Umbrella lock manifestations

This spec introduces no new umbrella-level locks (umbrella stays at 32 architectural locks). It documents how each umbrella lock manifests in code:

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
