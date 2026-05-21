# Phase 6 — Live Trading: Umbrella Architecture Design

**Status:** Brainstorm complete · ready for sub-project specs (6a first)
**Author:** brainstorm 2026-05-21
**Spec-type:** umbrella architecture (meta-spec; sub-project specs 6a-6g detail implementation)
**Scope:** Paper-only trading. Real money is Phase 7.

---

## 1 — Goal & Phase boundary

**Goal:** Promote MarketPulse from a research/backtest engine into a forward-running paper-trading execution system. AI analyses + Phase 5 allocation produce paper orders against a `ForwardExecutionEngine`. Orders, fills, positions, cash, P&L, risk state, and audit events are tracked in DB. The UI surfaces current paper-trading state, and kill switch + risk gates make the system safe to run unattended in shadow mode.

**Phase boundary:**

- Phase 6 is **paper-only**. Real money is Phase 7.
- The `ExecutionEngine` interface ships in Phase 6 with one canonical implementation: `ForwardExecutionEngine`.
- Phase 7's `BrokerExecutionEngine` must plug into the same interface. It should be an **adapter swap, not an architectural rewrite**.
- All Phase 4-5 backtest infrastructure remains. Phase 6 adds forward-running paper trading **alongside** the existing research/backtest UI.

### Anti-goals for Phase 6

- ❌ No real-broker API integration. Deferred to Phase 7.
- ❌ No real-money order placement.
- ❌ No mandatory streaming/real-time quote dependency. `RealtimeExecutionEngine` may be a stretch/future implementation, but `ForwardExecutionEngine` is the canonical Phase 6 path.
- ❌ No optimizer-controlled execution. `ShadowPoolOptimizer` may run as diagnostic-only; `RuleCascadeAllocator` remains authoritative.

### Critical scope clarification: Phase 6 is NOT a market simulator

`ForwardExecutionEngine` validates:

- state transitions
- allocation plumbing
- risk gating
- persistence
- lifecycle correctness
- restart / idempotency behavior

It does **NOT** attempt to model:

- realistic slippage
- queue priority / exchange microstructure
- partial fills
- spread dynamics
- broker outages or rate limits

`RealtimeExecutionEngine` (6d) begins validating some microstructure properties. Phase 7 `BrokerExecutionEngine` becomes the real source of execution-truth. **Phase 6 paper P&L is allocation-truth, not execution-truth.** Future readers comparing Phase 6 paper numbers to live broker results must keep this distinction in mind.

---

## 2 — Sub-project decomposition

Six sub-projects in Phase 6 (paper-only). Phase 7 holds `7a BrokerExecutionEngine`.

### 6a Broker abstraction + ForwardExecutionEngine [MVP, foundation]

- `ExecutionEngine` Protocol (place_order / cancel_order / tick — see § 3.2)
- `ForwardExecutionEngine` impl (runs on newly arriving AI/strategy events; prices/fills computed via Phase 4-5 outcome math)
- New DB tables: `paper_order`, `paper_fill`, `paper_position`, `paper_cash_ledger`
- AI-signal → strategy → allocation → order wiring
- Kill switch (env var + DB flag; respected by `ExecutionEngine.place_order`)

**Locked invariants:**

- **(i)** Canonical state lives in DB-backed `paper_order` / `paper_fill` / `paper_position` / `paper_cash_ledger` records. All downstream systems (UI, observability, risk, optimizer) CONSUME these records; they do NOT reconstruct truth independently.
- **(ii)** `ForwardExecutionEngine` is the authoritative owner of execution-time progression. When an order becomes eligible, when a fill occurs, when P&L updates, when positions close — all driven by `ForwardExecutionEngine`'s clock. Recap jobs, risk jobs, UI refresh, optimizer telemetry consume this clock; they do not advance it.
- **(iii)** `ExecutionEngine` is the **ONLY** writer of:
  - order status
  - fills
  - position quantity
  - cash balance

  All other components (scheduler, UI, recap jobs, risk jobs) OBSERVE this state; they do not mutate it. Single-writer execution model. Becomes critical before Phase 7.

### 6b Risk gates [MVP]

- Pre-trade checks (per-strategy size, daily loss limit, max drawdown halt)
- Market-hours / holiday calendar (when can orders fire)
- Sector + correlation caps applied in real-time (vs. Phase 5c batch)
- Kill-switch propagation (blocks all order placement)

**Locked invariant:**

- **(iv)** Risk gates are **FAIL-CLOSED**. Any unknown / errored / unavailable risk state DENIES order placement. There is no "None → allow" default path.

### 6f Paper-trading UI [MVP]

- `/lab/paper-trading` route (or extends existing `/lab/backtest`)
- Live positions table, running P&L, cash balance, order history, fill audit, risk-state indicator, kill switch button
- Reads canonical state from 6a tables (lock i)

### 6g Observability + alerting [MVP]

- Push notifications on fills, errors, kill-switch flips
- Per-order provenance log (placed/cancelled/filled, with strategy + allocation context)
- Daily P&L summary (reuses Phase 2 recap infrastructure)

**Locked invariant:**

- **(v)** Audit events are **APPEND-ONLY** provenance records. They are not mutable reconstructed state. Once written, an audit row is never updated or deleted.

### 6e ShadowPoolOptimizer (diagnostic-only) [STRETCH]

- Runs alongside `RuleCascadeAllocator` each day
- Computes what a constrained solver WOULD have allocated for the **SAME bid set and SAME constraint set** as the production allocator consumed (pure allocator comparison, not input drift)
- New metric: `optimizer_drift` (extends 5e `rank_drift_from_signal`)
- **NEVER authoritative** — telemetry only (S1 anti-goal)

### 6d RealtimeExecutionEngine [STRETCH, optional]

- Implements the `ExecutionEngine` interface using streaming quotes
- Validates basic slippage + partial-fill semantics in paper mode
- Depends on 6a (`ExecutionEngine` interface), NOT on 6e
- MAY become required before/during Phase 7 (broker/data-feed dependent)

### Dependency graph

```
            ┌──────────────────────┐
            │  6a (foundation)     │
            └──────┬───────────────┘
                   │
       ┌───────────┼──────────┬───────────┬──────────┐
       ▼           ▼          ▼           ▼          ▼
      6b          6f         6g          6e         6d
    (risk)      (UI)       (obs)      (shadow)   (realtime)
    [MVP]      [MVP]      [MVP]     [STRETCH]   [STRETCH]
```

### MVP boundary + suggested execution order

Phase 6 is **shippable** after `6a + 6b + 6f + 6g`. 6e and 6d are quality enhancements; absence does not prevent "system can paper-trade safely." Notably 6g is in the MVP — paper trading without observability is un-debuggable.

Suggested order:

1. **6a** (largest single sub-project; establishes interface + state schema + invariants)
2. **6b + 6g in parallel** (risk layers on 6a state; observability reads 6a state — both independent of each other)
3. **6f** (UI consumes 6a state + 6b risk-state + 6g audit log)
4. *(MVP shippable here — Phase 6 paper-trading exists)*
5. **6e** (stretch — shadow optimizer adds drift metric)
6. **6d** (stretch — realtime execution validates slippage)

### 6a is itself large — sub-decomposition guidance

Per umbrella review: 6a carries ~65-75% of Phase 6's total complexity (Protocol, persistence, lifecycle, idempotency, transactionality, restart safety, clock, scheduler, state progression). The 6a spec should consider sub-decomposing into at least:

| Sub-task | Scope |
|---|---|
| **6a-1** | DB schema (5 tables) + Alembic migration + `ExecutionEngine` Protocol |
| **6a-2** | `ForwardExecutionEngine` implementation (place_order, cancel_order, tick) |
| **6a-3** | Scheduler wrapper + idempotency + transactionality + restart safety |
| **6a-4** | Stateful test suite (clock-injected, `# Layer: stateful` tag) |

This is umbrella-level guidance only; 6a's own spec will lock the exact sub-task boundaries. The point is: 6a is not "one task" — it's a sub-phase with its own internal decomposition.

---

## 3 — Data flow & ExecutionEngine interface

### 3.1 End-to-end data flow

```
┌─────────────────────────────────────────────────────────────────────┐
│ EXISTING (Phase 1-5e, unchanged in Phase 6):                        │
│   watchlist scan → event detection → AI analysis (strategy-routed)  │
│       → evaluation_event row inserted                               │
│       → (eventually) evaluation_outcome row inserted at horizon     │
└────────────────────────────┬────────────────────────────────────────┘
                             │  new event with strategy tag
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│ NEW (Phase 6 paper-trading pipeline):                               │
│                                                                     │
│  BidAggregator (collects today's bids)                              │
│      │                                                              │
│      ▼                                                              │
│  RuleCascadeAllocator (authoritative; Phase 5a-5e logic)            │
│      │ surviving bids w/ position sizes                             │
│      │                                                              │
│      ├──→ ShadowPoolOptimizer (6e) (diagnostic only)                │
│      │     → optimizer_drift metric                                 │
│      │                                                              │
│      ▼                                                              │
│  RiskGates (6b, fail-closed) → approved                             │
│      │  (boundary object: OrderRequest)                             │
│      ▼                                                              │
│  ExecutionEngine.place_order(order_request)                         │
│      │                                                              │
│      ├──→ rejected synchronously? → OrderRejected raised            │
│      │    + audit event (lock ix) → NO paper_order row              │
│      │                                                              │
│      └──→ accepted → paper_order row PLACED                         │
│           │                                                         │
│           │  tick(as_of=date) advances clock (ForwardExecutionEngine)│
│           ▼                                                         │
│      Entry fill → paper_fill (ENTRY) + paper_position OPEN          │
│           │                                                         │
│           │  tick(as_of=horizon_date)                               │
│           ▼                                                         │
│      Exit fill → paper_fill (EXIT) + paper_position CLOSED          │
│           │                                                         │
│           ▼                                                         │
│      paper_cash_ledger updated, P&L realized                        │
│                                                                     │
│  ObservabilityLog (6g, append-only)                                 │
│  Paper-trading UI (6f, reads canonical state)                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 `ExecutionEngine` interface contract

The load-bearing contract. Phase 6 ships `ForwardExecutionEngine`; Phase 7 adds `BrokerExecutionEngine`; stretch `RealtimeExecutionEngine` slots in alongside. All three implement the same Protocol; downstream code never knows which one is running.

```python
# marketpulse/trading/execution_engine.py  (NEW Phase 6a)

@dataclass(frozen=True)
class OrderRequest:
    """Boundary object: RiskGates produce, ExecutionEngine consumes.
    Phase 7's BrokerExecutionEngine consumes the same shape (lock vi)."""
    strategy: str
    ticker: str
    quantity: int  # signed: positive=long, negative=short (future)
    event_time: datetime
    event_price: float
    horizon_date: date
    horizon_price: float | None  # forward-known for Phase 6;
                                  # None reserved for Phase 7 broker
    # ... plus Phase 5 allocation context (weight, contribution metadata, etc.)


class ExecutionEngine(Protocol):
    """The ONE writer of paper_order/paper_fill/paper_position/paper_cash_ledger
    state. Locks i, ii, iii (§ 2) + viii (§ 3).

    Command-only Protocol: place_order / cancel_order / tick.
    All reads go through DB query models against canonical tables."""

    def place_order(self, *, order_request: OrderRequest) -> OrderId:
        """Submit. Returns OrderId. Synchronous rejection raises
        OrderRejected (no paper_order row created; audit event written
        per lock ix)."""

    def cancel_order(self, *, order_id: OrderId) -> None:
        """Cancel a still-PLACED order. No-op if already ENTRY_FILLED/CANCELLED."""

    def tick(self, *, as_of: date) -> None:
        """Advance the execution clock to `as_of`.
        - ForwardExecutionEngine: materializes fills (entry) for orders
          with entry_date <= as_of; processes closures (exit) for
          positions with horizon_date <= as_of.
        - RealtimeExecutionEngine (future): no-op or wall-clock-driven.
        - BrokerExecutionEngine (Phase 7): no-op; broker's event stream
          / fill callbacks drive state."""
```

**Read access:** UI (6f), risk gates (6b), observability (6g), shadow optimizer (6e) read canonical state by querying `paper_order` / `paper_fill` / `paper_position` / `paper_cash_ledger` tables directly. The `ExecutionEngine` interface is **command-only**; reads go through the DB. This keeps the interface lean AND enforces lock iii (single-writer) at the type level: no method on `ExecutionEngine` can be misused to mutate state from a read site.

### 3.3 Two separate state machines

**Order lifecycle:**

```
                  ┌──────────────┐
                  │   PLACED     │ ← place_order() returns OrderId
                  └──┬───────┬───┘
                     │       │
   tick() processes  │       │  cancel_order()
   entry fill        │       │
                     ▼       ▼
              ┌──────────────┐  ┌────────────┐
              │ ENTRY_FILLED │  │ CANCELLED  │
              │  (entry      │  │ (no fill,  │
              │   recorded)  │  │  no cash   │
              └──────────────┘  │  movement) │
                                └────────────┘

REJECTED is synchronous-only:
  place_order() raises OrderRejected
  → NO paper_order row
  → REQUIRED audit event (lock ix)
```

**Position lifecycle (independent — lock xi):**

```
                   ┌──────────┐
                   │  OPEN    │ ← created when paper_order → ENTRY_FILLED
                   └──────┬───┘
                          │
                          │  tick(as_of >= horizon_date)
                          │  processes the exit fill
                          ▼
                    ┌──────────┐
                    │  CLOSED  │
                    └──────────┘
```

**Coupling:** when a `paper_order` transitions PLACED → ENTRY_FILLED, the entry `paper_fill` row is written AND a `paper_position` OPEN row is created. When `tick()` later processes the horizon, the exit `paper_fill` row is written AND the `paper_position` flips OPEN → CLOSED. Order and position lifecycles are **separate state machines on linked rows** — never conflated.

### 3.4 Phase 5 ↔ Phase 6 boundary

|  | Phase 5 (Backtest) | Phase 6 (Paper Trading) |
|---|---|---|
| Time scope | Historical (event_time in past) | Forward (event_time = now) |
| Input source | `evaluation_outcome` table (matured) | `evaluation_event` stream (pre-horizon) |
| Output | `PortfolioBacktestResult` in-memory | `paper_order` / `paper_fill` / `paper_position` rows |
| UI | `/lab/backtest` (historical) | `/lab/paper-trading` (live, runs forward) |
| Allocator | `RuleCascadeAllocator` (in-memory) | `RuleCascadeAllocator` (same code; canonical) |
| Optimizer | n/a (no 6e in Phase 5) | `ShadowPoolOptimizer` diagnostic |

**Coexistence principle:** the math is shared (`simulate_shared_pool` → `compute_position_sizes` → `compute_bid_weights` etc. unchanged), only the time semantics and persistence layer differ. Phase 5 reads matured data and produces transient results; Phase 6 reads pre-mature signals and produces persistent state.

### 3.5 Section 3 locks

- **(vi)** `ExecutionEngine` is a Protocol (structural typing). Phase 6 ships `ForwardExecutionEngine`; Phase 7 adds `BrokerExecutionEngine`; `RealtimeExecutionEngine` is a stretch sibling. All three implement the same Protocol.
- **(vii)** In `ForwardExecutionEngine`, `tick(as_of)` is the sole clock-advancement mechanism. Other implementations may use different drivers (wall-clock, broker callback).
- **(viii)** All execution-state mutation MUST pass through the `ExecutionEngine` boundary. Other components may READ wall-clock time (for display, eligibility checks, market-hours logic), but they cannot advance `paper_order` / `paper_fill` / `paper_position` / `paper_cash_ledger` state.
- **(ix)** Rejected order attempts create NO `paper_order` row, but MUST write an append-only audit event (via 6g) with full rejection reason (failing risk gate, kill-switch state, etc.).
- **(x)** `RuleCascadeAllocator` is shared between Phase 5 backtest and Phase 6 paper trading — no duplicate allocator implementation. The math is one source; the time semantics + persistence layer differ.
- **(xi)** Order lifecycle (PLACED → ENTRY_FILLED / CANCELLED) and position lifecycle (OPEN → CLOSED) are SEPARATE state machines. Entry fill creates/updates `paper_position`; horizon exit closes it. `paper_order.ENTRY_FILLED` means entry recorded, NOT both entry and exit.
- **(xii)** `OrderRequest.horizon_price: float | None`. `ForwardExecutionEngine` REJECTS `OrderRequest` with `horizon_price is None` (cannot compute fill without it). `BrokerExecutionEngine` IGNORES `horizon_price` (broker reports real fill). `RealtimeExecutionEngine` semantics are deferred to the 6d spec when that sub-project is brainstormed.

---

## 4 — State model / DB schema

Five new tables in Phase 6. This section sketches columns + relationships; exact DDL + indexes are 6a's job in its own spec.

### 4.1 `paper_order`

Single row per submitted order. Lifecycle: PLACED → ENTRY_FILLED | CANCELLED.

```
paper_order
├── id                       PK
├── idempotency_key          UNIQUE
│                            (computed: strategy + ticker + event_time +
│                             allocation_run_id)
├── allocation_run_id        UUID
│                            (groups orders from one allocation batch)
├── strategy                 FK to strategy YAML
├── ticker
├── quantity                 signed int (positive only in Phase 6;
│                                        shorts deferred)
├── event_time               timestamp WITH TIME ZONE (UTC — lock xxix)
├── event_price              Decimal(18, 6)   ← was float (lock xxxi)
├── horizon_date             date
├── horizon_price            Decimal(18, 6) | None
│                            (forward-known for Phase 6;
│                             None reserved for Phase 7 broker)
├── status                   Literal["PLACED", "ENTRY_FILLED", "CANCELLED"]
├── placed_at                timestamp WITH TIME ZONE (UTC)
├── filled_at                timestamp WITH TIME ZONE | None
├── cancelled_at             timestamp WITH TIME ZONE | None
├── cancel_reason            str | None
│
├── -- Versioning for replay determinism (lock xxviii) --
├── strategy_version         str (the version field from strategy YAML)
├── allocator_version        str (semver or git-sha of RuleCascadeAllocator)
├── execution_engine_version str (semver of ForwardExecutionEngine)
│
├── -- Phase 5 allocation provenance (lock x: shared with backtest) --
├── weight                   float (internal math; Decimal cast on read if needed)
├── raw_bid_weight           float | None
├── pool_corr                float | None
├── contribution_multiplier  float
├── adjusted_bid_weight      float | None
├── effective_corr_window    int
├── rewarded_for_negative_corr  bool
├── would_change_rank        bool
└── size_clamped_by_override bool
```

**Indexes:** `(status, horizon_date)` for fast `tick()` scans; `(strategy, placed_at)` for per-strategy queries; `idempotency_key` UNIQUE.

### 4.2 `paper_fill`

Two rows per ENTRY_FILLED order: one entry, one exit. Append-only.

```
paper_fill
├── id                  PK
├── order_id            FK → paper_order.id
├── position_id         FK → paper_position.id  (NOT NULL)
├── side                Literal["ENTRY", "EXIT"]
├── price               Decimal(18, 6)    ← was float (lock xxxi)
├── quantity            signed int
├── filled_at           timestamp WITH TIME ZONE (UTC — lock xxix)
├── cash_delta          Decimal(18, 6)    ← was float
└── realized_pnl        Decimal(18, 6) | None (None for ENTRY; set for EXIT)
```

**Insertion-order discipline** (preserves append-only on `paper_fill`):

- **ENTRY flow:** (1) create `paper_position` OPEN with `entry_fill_id` NULL → (2) INSERT `paper_fill` ENTRY with known `position_id` → (3) UPDATE `paper_position.entry_fill_id`. paper_fill is never updated; paper_position is mutable (lifecycle).
- **EXIT flow:** (1) INSERT `paper_fill` EXIT with known `position_id` → (2) UPDATE `paper_position.exit_fill_id` + status=CLOSED.

**Constraint:** ≤1 ENTRY row + ≤1 EXIT row per `order_id`. EXIT cannot exist before ENTRY (app-level invariant).

### 4.3 `paper_position`

Single row per OPEN position; flipped to CLOSED on horizon. Linked to paper_order via the entry fill.

```
paper_position
├── id                  PK
├── order_id            FK → paper_order.id (UNIQUE — lock xiv)
├── entry_fill_id       FK → paper_fill.id
├── exit_fill_id        FK → paper_fill.id | None
├── strategy            denormalized
├── ticker              denormalized
├── quantity            signed int
├── entry_price         Decimal(18, 6)    ← was float (lock xxxi)
├── entry_date          date
├── horizon_date        date
├── status              Literal["OPEN", "CLOSED"]
├── opened_at           timestamp WITH TIME ZONE (UTC — lock xxix)
├── closed_at           timestamp WITH TIME ZONE | None
├── exit_price          Decimal(18, 6) | None
└── realized_pnl        Decimal(18, 6) | None
```

**Lifecycle invariant from FK pattern:**

- OPEN: `entry_fill_id` SET, `exit_fill_id` NULL
- CLOSED: BOTH set

### 4.4 `paper_cash_ledger`

Append-only ledger of every cash movement. Source of truth for cash balance.

```
paper_cash_ledger
├── id                  PK (monotonic; defines ordering — lock xxi)
├── timestamp           timestamp WITH TIME ZONE (UTC — lock xxix)
├── delta               Decimal(18, 6) signed   ← was float (lock xxxi)
├── reason              Literal["ENTRY_FILL", "EXIT_FILL",
│                                "INITIAL_DEPOSIT", "MANUAL_ADJUSTMENT"]
├── fill_id             FK → paper_fill.id | None
│                       (NULL for INITIAL_DEPOSIT, MANUAL_ADJUSTMENT)
└── balance_after       Decimal(18, 6) (denormalized running total)
```

**Read semantic (lock xxi):** `current_cash_balance = SELECT balance_after FROM paper_cash_ledger ORDER BY id DESC LIMIT 1`. Same-timestamp ambiguity is resolved by monotonic `id`.

**Initial seed:** Phase 6a inserts one `INITIAL_DEPOSIT` row with `delta=10_000` (matching the existing `initial_capital=10_000` default).

### 4.5 `paper_audit_event` (6g)

Universal provenance log. Every state transition AND every rejection writes here.

```
paper_audit_event
├── id                  PK
├── timestamp           when the event occurred
├── event_type          Literal["ORDER_PLACED", "ORDER_FILLED",
│                                "ORDER_CANCELLED", "ORDER_REJECTED",
│                                "POSITION_CLOSED", "KILL_SWITCH_FLIPPED",
│                                "RISK_GATE_BLOCKED", ...]
├── order_id            FK → paper_order.id | None (None for REJECTED)
├── strategy            str | None
├── reason              text (rejection reason for *_REJECTED / *_BLOCKED;
│                            empty for success events)
└── context             JSON (full OrderRequest dump for REJECTED;
                             placement metadata for PLACED; etc.)

(NO updated_at or modified_by — append-only per lock v)
```

**Lock ix coverage:** every rejected order attempt MUST write an `ORDER_REJECTED` row here with full reason in `reason` + full `OrderRequest` in `context`. This is queryable for analytics ("how often does the daily loss limit gate fire?").

### 4.6 Float vs Decimal — persistence-layer discipline

**Phase 6a DB persistence uses `Decimal(18, 6)`** for all price / cash / P&L columns. Internal math may continue to use `float` for Phase 4-5 backtest compatibility (rolling Sharpe, alpha-conviction sizing, contribution multiplier, etc.); the persistence layer quantizes float → Decimal at INSERT, and reconstitutes Decimal → float at READ for backtest math reuse.

This avoids the silent reconciliation drift the user identified: broker reports `10000.23`, our recomputed float says `10000.22999997`, drift accumulates over months, Phase 7 reconciliation becomes a tolerance-hack nightmare. Promoting Decimal to Phase 6a means Phase 7 inherits a clean ledger, not a migration target.

**The split:**

| Layer | Type | Why |
|---|---|---|
| Backtest math (`compute_position_sizes`, etc.) | `float` | Phase 4-5 compatibility; no rewrite |
| `OrderRequest` / `Order` in-memory objects | `Decimal` | Boundary with persistence |
| All DB columns for price/cash/P&L | `Decimal(18, 6)` | Source of truth |
| `paper_audit_event.context` JSON | string-serialized Decimal | Roundtrip-stable |

**Quantization point:** the `OrderRequest` constructor (or the place that builds it from allocator output) is where `float → Decimal` happens. The simulator math runs in float; the boundary into ExecutionEngine is the conversion site. Phase 7's broker engine then deals with Decimal natively (which most broker APIs accept).

### 4.7 Schema impact summary

| Table | Rows per trading day (typical) | Phase 5 ↔ Phase 6 overlap |
|---|---|---|
| `paper_order` | ~5-30 | none — Phase 6 adds |
| `paper_fill` | ~10-60 (2× orders) | none |
| `paper_position` | ~5-30 OPEN at any time | none |
| `paper_cash_ledger` | ~10-60 (one per fill + seed) | none |
| `paper_audit_event` | ~20-100 | none |

**No existing tables modified.** Phase 5's `evaluation_event`, `evaluation_outcome`, `ai_analyses`, `daily_recaps`, etc. stay exactly as they are. Phase 6 is purely additive on the DB side.

**Alembic migration count for 6a:** 1 (creates all 5 tables in one revision).

### 4.8 Section 4 locks

- **(xiii)** `paper_fill`, `paper_audit_event`, `paper_cash_ledger` are append-only. No UPDATE / DELETE.
- **(xiv)** `paper_position.order_id` is UNIQUE — one position per order in Phase 6. Phase 7 may lift this with a new spec.
- **(xv)** Phase 6 introduces NO modifications to existing Phase 1-5 tables. All Phase 6 state in the 5 new tables.
- **(xvi)** Cash source-of-truth: `paper_cash_ledger` append-only ledger. No mutable "current balance" row.
- **(xvii)** `paper_order.idempotency_key` is UNIQUE, computed deterministically from `strategy + ticker + event_time + allocation_run_id`. Re-running an allocation batch with the same inputs MUST NOT produce duplicate orders.
- **(xviii)** Every order carries an `allocation_run_id` (UUID). All orders from one allocation batch share the same ID. Enables provenance grouping for debugging, comparison with `ShadowPoolOptimizer` (6e), and decision reproduction. Also appears on `paper_audit_event.context`.
- **(xix)** `paper_order.status` is `PLACED | ENTRY_FILLED | CANCELLED` (NOT "FILLED"). The rename makes lock xi (order ≠ position lifecycle) explicit at the column level. Phase 7 broker integration carries the same vocabulary forward.
- **(xx)** `paper_order` rows are created ONLY for won allocation outcomes. Non-won outcomes (dedup_loser, cap_full, cash_short, size_too_small, etc.) appear in `paper_audit_event` and in the existing `BidRecord` telemetry from Phase 5, NOT in `paper_order`.
- **(xxi)** `paper_cash_ledger.balance_after` is the source of truth for cash balance. Read order: `ORDER BY id DESC LIMIT 1`. The monotonic `id` resolves same-timestamp ambiguity.
- **(xxii)** **DB persistence layer uses `Decimal(18, 6)` for all price / cash / P&L columns from Phase 6a Day 1.** Internal math (`compute_position_sizes`, rolling Sharpe, etc.) keeps `float` for Phase 4-5 compatibility; the persistence boundary (typically `OrderRequest` construction) quantizes float → Decimal. Phase 7 inherits a clean ledger, not a migration target. (Promoted from Phase 7 to Phase 6a per umbrella review 2026-05-21.)

---

## 5 — Testing & operational discipline

Phase 6 introduces a new failure mode that Phase 5 didn't have: **the system carries state across days**. A backtest run is hermetic; a paper-trading run survives restarts, mid-day crashes, scheduler hiccups. Section 5 locks the disciplines that make this safe.

### 5.1 Clock injection

`ForwardExecutionEngine.__init__(*, clock: Clock)` — explicit `Clock` dependency, injected. Production uses `WallClock`; tests use `FakeClock`.

```python
class Clock(Protocol):
    def now(self) -> datetime: ...
    def today(self) -> date: ...


class WallClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
    def today(self) -> date:
        return self.now().date()  # NOT date.today() — preserves lock xxiii


class FakeClock:  # tests only
    def __init__(self, *, now: datetime):
        self._now = now
    def now(self) -> datetime:
        return self._now
    def today(self) -> date:
        return self._now.date()
    def advance(self, *, days: int) -> None:
        self._now += timedelta(days=days)
```

### 5.2 Restart safety

The system MUST survive a crash mid-day without corruption:

- **Mid-`place_order()` crash**: idempotency_key (lock xvii) makes the retry a no-op. The DB transaction wrapping `paper_order` INSERT + `paper_audit_event` INSERT is atomic.
- **Mid-`tick()` crash**: `tick(as_of=D)` is idempotent — running it twice for the same date produces the same end state. The query "orders to fill" is `WHERE status = PLACED AND event_date <= D`; orders already filled are not re-touched.
- **Process restart**: state is fully in DB (lock i). On boot, the scheduler simply calls `engine.tick(as_of=clock.today())`. Because tick is idempotent and DB-backed, no separate resume pointer is required.

### 5.3 Scheduler responsibilities (Phase 6a)

A scheduler component drives `tick()` exactly once per business day (and on demand for testing). It is **NOT** the ExecutionEngine — it is a thin caller:

```python
# marketpulse/jobs/paper_trading_tick.py  (Phase 6a)
def run_paper_trading_tick(*, clock: Clock, engine: ExecutionEngine):
    today = clock.today()
    engine.tick(as_of=today)
    # That's it. ExecutionEngine handles all state mutation internally.
```

### 5.4 Testing patterns

Phase 5e's `# Layer: invariant` / `# Layer: behavioral` tags carry forward verbatim. Phase 6 adds a third tag for operational tests:

**`# Layer: stateful`** — tests that exercise multi-step paper trading flows (place → tick → fill → tick → close). Use `FakeClock` to compress the 5-day horizon into milliseconds. The pytest enforcement hook (5e lock #22) is extended to accept `stateful` as a third valid value.

Three test categories in Phase 6:

- **invariant**: structural properties (Σ paper_cash_ledger.delta = balance_after of latest row, paper_fill always references valid position, etc.)
- **behavioral**: dynamics-dependent properties (a 60-day backtest with sector_caps fires the cap on day X)
- **stateful**: multi-step flows (place_order → tick to entry_date → ENTRY_FILLED + OPEN position → tick to horizon → CLOSED position + cash delta correct)

### 5.5 Section 5 locks

- **(xxiii)** All Phase 6 production code (6a, 6b, 6e, 6f) reads time via an injected `Clock` dependency. No `date.today()` or `datetime.now()` calls in production code paths. `WallClock` in production; `FakeClock` in tests.
- **(xxiv)** `ExecutionEngine.tick(as_of=D)` is **idempotent**. Running it twice for the same `D` produces the same end state. This guarantees restart safety + replay safety.
- **(xxv)** The scheduler that drives `tick()` is a thin single-purpose wrapper. It does NOT read order state, mutate state, or contain business logic. The scheduler advances the clock; the ExecutionEngine handles everything else.
- **(xxvi)** Phase 5e's test taxonomy is extended with a third category: `# Layer: stateful` for multi-step paper-trading flow tests. The pytest enforcement hook (5e lock #22) accepts the third value.
- **(xxvii)** `place_order` is transactional. Accepted attempts commit `paper_order` + `ORDER_PLACED` audit together. Rejected attempts commit `ORDER_REJECTED` audit without `paper_order`. No partial accepted/rejected state.

### 5.6 Cross-cutting locks added per umbrella review (2026-05-21)

These four locks address concrete future-bug surfaces identified during architectural review:

- **(xxviii)** Every `paper_order` row carries `strategy_version` (from YAML), `allocator_version` (semver/git-sha of `RuleCascadeAllocator`), and `execution_engine_version` (semver of `ForwardExecutionEngine`). Without these, replay across deploys produces different output for the same input — and there's no way to explain why March's allocation differs from a re-run today. The versions appear on every row so replay determinism is auditable per-order.
- **(xxix)** **All timestamps stored in UTC.** All market-hours / holiday logic evaluated in `America/New_York` (or the strategy's declared exchange tz). DST never appears as a bug because timezone-naive datetimes never enter the DB and never enter business-day arithmetic.
- **(xxx)** **Idempotency check executes BEFORE risk gates** in `place_order(order_request)`:
    1. Compute `idempotency_key` from `OrderRequest`
    2. IF an existing `paper_order` row matches → return its `OrderId` (no-op; no audit row, or `ORDER_PLACED_DUPLICATE` audit)
    3. ELSE → run risk gates → if accepted, INSERT paper_order + ORDER_PLACED audit transactionally (lock xxvii)

  Rationale: once an order is accepted, retries (network timeout, scheduler restart) MUST NOT be re-evaluated by risk gates whose state has since changed. The accepted-then-retried-then-now-rejected scenario produces non-deterministic replays. Idempotency wins.
- **(xxxi)** DB persistence uses `Decimal(18, 6)` from Phase 6a Day 1 (see § 4.6 + updated lock xxii). Internal backtest math keeps float; the persistence boundary (typically `OrderRequest` construction) is the float → Decimal quantization site.
- **(xxxii)** `marketpulse/trading/calendar.py` declares the single canonical source of trading-calendar truth. Phase 6a picks ONE library (`exchange_calendars`, `pandas_market_calendars`, or a hand-rolled NYSE holiday list) and locks the choice in this module. Risk gates (6b), scheduler (6a), market-hours UI indicators (6f) all import from here. No second source.

---

## 6 — Phase 7 forward-warnings

Parallel to Phase 5e § 10. Names the architectural pressure points that Phase 6 deliberately does NOT solve, so Phase 7's broker-integration spec inherits a clear set of problems with known shapes.

### 6.1 The five Phase 7 pressure points

**PP1 — `horizon_price` becomes `None` mid-flow.**
In `ForwardExecutionEngine`, `horizon_price` is known at `place_order()` time (Phase 4-5 outcome math). In `BrokerExecutionEngine`, the broker reports the actual fill price post-hoc via callback. `OrderRequest.horizon_price` is typed `float | None` (lock xii); ForwardExecutionEngine rejects None, BrokerExecutionEngine accepts it. **But:** `paper_order.horizon_price` is currently `float | None` and the Phase 6 query pattern assumes "if status=ENTRY_FILLED, horizon_price is the truth." Phase 7 must split this: `expected_horizon_price` (set at placement, signal-driven) vs. `realized_horizon_price` (set by broker callback, may differ by slippage). The two columns let drift be measured.

**PP2 — Broker-reported vs. recomputed price reconciliation.**
Phase 6 already persists prices/cash/P&L as `Decimal(18, 6)` (lock xxii / xxxi), so the ledger itself does NOT accumulate float-rounding drift. The remaining Phase 7 problem is *semantic*: the broker's reported fill price will differ from `ForwardExecutionEngine`'s outcome-math fill price by real-market slippage, commissions, FX, and venue-specific rounding. Phase 7 must split `paper_order.horizon_price` into `expected_horizon_price` (signal-driven, set at placement) vs. `realized_horizon_price` (broker callback, may differ). See PP1 — the two pressure points share the same architectural fix. The work Phase 6 deferred from this point is the boundary split, not the type migration.

**PP3 — `tick(as_of)` semantics for broker-driven mode.**
`ForwardExecutionEngine`: `tick(as_of=D)` materializes all fills for D. `RealtimeExecutionEngine`: `tick()` may be no-op (wall clock drives) or wall-clock advance. `BrokerExecutionEngine`: `tick()` is fundamentally no-op (broker's WebSocket callbacks drive state). But the scheduler still runs! Phase 7 must define: does the scheduler keep calling `tick()` on a daily cron (broker engine no-ops it), or does the scheduler itself change shape per engine? Lock xxv says scheduler is single-purpose; Phase 7 might break that.

**PP4 — `paper_position.order_id` UNIQUE → many-to-many.**
Lock xiv: one position per order in Phase 6. Brokers do not honor this. Partial fills, position aggregation across multiple orders, manual adjustments — all break the 1:1. Phase 7 must lift this constraint and introduce a `position_fill_links` association table (or similar). Existing 6f UI queries that assume 1:1 must be revisited.

**PP5 — Idempotency semantics under broker retries.**
Lock xvii: `idempotency_key` UNIQUE on `paper_order`. The key is computed deterministically from `(strategy, ticker, event_time, allocation_run_id)`. But brokers issue their own order IDs; a network timeout might result in the broker accepting the order even though our `place_order()` raised. Phase 7 must handle: (a) idempotent broker submission (most modern broker APIs support this with a client_order_id) AND (b) reconciliation between our `paper_order.idempotency_key` and the broker's `client_order_id`. The relationship is 1:1 but the broker is the source of truth post-acceptance.

### 6.2 Three architectural drifts acknowledged but deferred

**Drift A — `RuleCascadeAllocator` runs once per day in Phase 6 (batch). In real-time (6d stretch or Phase 7), it must run on every new event.**
Lock x shares the allocator code between Phase 5 backtest and Phase 6 paper trading. Phase 6 batches bids overnight (allocation_run_id groups one day's bids). Real-time mode would need streaming arbitration: how do new bids interact with already-placed orders? Either (a) re-allocate the full open-bid set on every event (computationally expensive but consistent), (b) lock placed orders and only allocate the residual capacity for new bids (faster but produces order-dependent outcomes), or (c) introduce a `pool_optimizer.py` that explicitly handles streaming. This is 5e § 10 Pressure Point #5 reified.

**Drift B — `paper_audit_event.context: JSON` is unbounded.**
Lock xviii puts the full `OrderRequest` in audit context for REJECTED events. Over months/years this column grows. Phase 7 should introduce retention or compaction policy. Not Phase 6 work.

**Drift C — `ShadowPoolOptimizer` (6e) needs a constrained-optimization library.**
If 6e ships in Phase 6 (stretch), it pulls in `scipy.optimize` or `cvxpy` as a dependency. Phase 6 currently has no optimization library. Decision is deferred to 6e's own spec, but flagged here so Phase 7 doesn't inherit an unexpected dependency lock-in.

**Drift D — State snapshot discipline.**
Phase 6 reconstructs P&L / position state on demand by querying `paper_fill` + `paper_cash_ledger` + `paper_position`. This is fine at Phase 6's scale (~30 orders/day, months of history). At Phase 7 / 6d real-time scale, on-demand reconstruction becomes expensive and snapshot tables (e.g., `daily_position_snapshot`, `daily_pnl_snapshot`) become necessary for UI and reporting performance. The snapshot tables would be derived (not source-of-truth — locks i / xvi still bind the ledger as truth) and recomputable from the append-only ledger. Phase 6 deliberately does NOT introduce snapshots, because they add cache-invalidation complexity without measurable benefit at current scale. Phase 7's reconciliation against a live broker feed is the natural trigger for adding them.

### 6.3 Phase 7 entry checklist

When Phase 7 brainstorming starts, the spec MUST address:

| # | Question | Anchored by |
|---|---|---|
| 1 | Which broker? (IBKR / Alpaca / Tiger / Futu / other) | New decision |
| 2 | Real-time data feed strategy (use broker's quotes vs. independent provider) | PP3 + 6d stretch outcome |
| 3 | Broker-reported vs. expected fill reconciliation (slippage tolerance, drift alarms) | PP2 + lock xxii |
| 4 | `expected_horizon_price` vs `realized_horizon_price` split | PP1 |
| 5 | `paper_position` 1:1 → N:M migration | PP4 + lock xiv |
| 6 | Idempotency key reconciliation with broker `client_order_id` | PP5 + lock xvii |
| 7 | Risk gate categories for real money (vs. paper's looser config) | New spec |
| 8 | Operational concerns (regulatory audit log retention, kill switch SLA, etc.) | New territory |

### 6.4 What Phase 6 explicitly DOES solve (success criteria)

Mirror-image of forward-warnings. By the end of Phase 6 MVP (6a + 6b + 6f + 6g):

- ✅ A new AI analysis → strategy router → RuleCascadeAllocator → ExecutionEngine → paper_order produces a real DB row with full Phase 5 telemetry threaded through
- ✅ `tick(today)` advances time deterministically; restart-safe via idempotency + DB canonical state
- ✅ Risk gates (6b) prevent order placement when daily loss limit, kill switch, or market-hours fail
- ✅ UI (6f) shows running paper-trading state: positions, P&L, cash, recent orders/fills, kill-switch toggle
- ✅ Observability (6g) emits push notifications on fills + alerts; append-only audit log captures every state transition
- ✅ Test discipline (5e taxonomy + Phase 6 stateful tag) prevents tautological tests
- ✅ Lock #16 contract (5e) extended: downstream consumers (Phase 7 broker engine) can read `paper_order` / `paper_fill` / `paper_position` / `paper_cash_ledger` fields unconditionally — no `hasattr` defensive code needed

### 6.5 No new locks in Section 6

Section 6 is documentary forward-warning. No new architectural locks; lock count stays at **32**.

---

## 7 — (intentionally omitted)

Section 7 (sub-project timeline / ordering rationale) was considered and intentionally omitted. Section 2's dependency graph + MVP boundary already encode sequencing. A timeline section risks becoming stale project-management prose instead of architecture.

---

## 8 — Operational test map

Compact: ~14 scenarios across 10 categories. Structured by **failure class** (not implementation task), because Phase 6's likely failure mode is replay/restart/idempotency corruption — not the Phase 5e-style tautological analytics.

| # | Category | Scenario | Locks protected |
|---|---|---|---|
| 1 | **Clock determinism** | `grep -rn "date\.today()\|datetime\.now()" marketpulse/trading/` returns ZERO matches outside `WallClock` definition | xxiii |
| 2 | **Replay / idempotency** | `place_order(req)` called twice with same `idempotency_key` → first call creates `paper_order` + audit; second call returns the existing OrderId, creates no new row, writes no new audit (or writes ORDER_PLACED_DUPLICATE noting the no-op) | xvii |
| 3 | **Replay / idempotency** | `tick(as_of=D)` called twice → second call is no-op: no new fills, no new ledger entries, no new audit rows beyond what the first call produced | xxiv |
| 4 | **Transactionality** | `place_order()` succeeds: query asserts `paper_order` row AND `ORDER_PLACED` audit row both committed within the same transaction (inject DB-error between INSERTs in a mock; verify rollback leaves NEITHER row) | xxvii |
| 5 | **Transactionality** | `place_order()` rejected (risk gate fail): query asserts `ORDER_REJECTED` audit row present, `paper_order` row absent — partial-state combinations forbidden | xxvii + ix |
| 6 | **Single-writer** | `grep -rn "paper_order\|paper_fill\|paper_position\|paper_cash_ledger" marketpulse/` returns INSERTs/UPDATEs only inside the `marketpulse/trading/execution_engine/` module. UI, risk gates, observability, optimizer use SELECT only. | iii + viii |
| 7 | **Lifecycle correctness** | E2E: `place_order` → status=PLACED → `tick(entry_date)` → status=ENTRY_FILLED + paper_position OPEN → `tick(horizon_date)` → paper_position CLOSED + exit fill recorded. Assert intermediate states explicitly, not just end state. | xi + xix |
| 8 | **Lifecycle correctness** | `grep -rn '"FILLED"' marketpulse/` returns ZERO matches (the renamed `ENTRY_FILLED` value must not coexist with the old `FILLED` literal anywhere) | xix |
| 9 | **Ledger correctness** | `SUM(delta) == (SELECT balance_after FROM paper_cash_ledger ORDER BY id DESC LIMIT 1)` holds after every fill, in every test fixture | xvi + xxi |
| 10 | **Ledger correctness** | Property test: insert N random cash movements with same timestamp; verify `balance_after` is monotonic by `id` (not timestamp) | xxi |
| 11 | **Stateful flow integrity** | Full E2E with `FakeClock`: seed evaluation_event → run allocation → place_order → tick(entry) → tick(horizon) → assert paper_cash_ledger reflects expected entry+exit deltas and `realized_pnl` matches Phase 5 backtest math for the same inputs | end-to-end (xxiii + xi + x + xvi) |
| 12 | **Fail-closed risk** | `RiskGate.check_pre_trade()` raises arbitrary exception → `place_order()` does NOT swallow it; the order is rejected, `ORDER_REJECTED` audit row records the exception type + message, NO `paper_order` row created | iv + ix |
| 13 | **Audit completeness** | Property: after N E2E operations, `count(paper_audit_event)` ≥ `count(state transitions)` (every transition has at least one audit row; some have multiple). No silent state changes. | v + ix |
| 14 | **CQRS boundary** | The `ExecutionEngine` Protocol has EXACTLY 3 methods: `place_order`, `cancel_order`, `tick`. Lint/test asserts `set(dir(ExecutionEngine)) - {magic methods}` matches expected set — adding a read method without spec amendment is rejected. | viii |

### 8.1 Test category tagging (parallel to 5e taxonomy)

Each scenario above is tagged with the appropriate `# Layer:` value:

- **invariant**: scenarios 1, 4, 5, 6, 8, 9, 10, 12, 13, 14 (10 scenarios)
- **behavioral**: none in this map — the warm-pool-style behavioral tests will live in 6a's spec, not the umbrella
- **stateful**: scenarios 2, 3, 7, 11 (4 scenarios — multi-step flows)

### 8.2 What S8 deliberately does NOT cover

- Per-sub-project test detail (6a/6b/6f/6g/6e/6d specs own their own test scenarios)
- Performance/load testing (Phase 6 scale is "one user, ~30 orders/day" — no perf concerns)
- Migration testing (no schema changes to existing tables; new tables are clean)
- UI snapshot/visual regression (6f's job)

S8 covers ONLY the cross-cutting operational integrity surface — the "must never break" suite that survives any sub-project iteration.

### 8.3 No new locks in Section 8

Section 8 is the test map for existing locks. No new architectural commitments; lock count stays at **32**.

---

## Appendix A — Consolidated lock list (32 locked decisions)

| # | Lock | Section |
|---|---|---|
| i | Canonical state in 6a DB tables; downstream observes only | § 2 (6a) |
| ii | `ForwardExecutionEngine` owns the execution clock | § 2 (6a) |
| iii | Single-writer: ExecutionEngine is ONLY mutator of order/fill/position/cash | § 2 (6a) |
| iv | Risk gates are fail-closed | § 2 (6b) |
| v | Audit events are append-only | § 2 (6g) |
| vi | `ExecutionEngine` is a Protocol; 3 implementations planned | § 3 (6a) |
| vii | `tick(as_of)` is sole clock-advancement in ForwardExecutionEngine | § 3 (6a) |
| viii | All state mutation passes through ExecutionEngine; reads may use wall-clock | § 3 (6a) |
| ix | Rejected orders write audit event with full reason; no paper_order row | § 3 (6a + 6g) |
| x | `RuleCascadeAllocator` shared between Phase 5 backtest and Phase 6 paper trading | § 3 (6a) |
| xi | Order lifecycle ≠ position lifecycle; separate state machines | § 3 (6a) |
| xii | `OrderRequest.horizon_price: float | None`; ForwardExec rejects None, BrokerExec ignores | § 3 (6a) |
| xiii | `paper_fill` + `paper_audit_event` + `paper_cash_ledger` are append-only | § 4 (6a + 6g) |
| xiv | `paper_position.order_id` is UNIQUE in Phase 6 | § 4 (6a) |
| xv | Phase 6 introduces NO modifications to Phase 1-5 tables | § 4 (6a) |
| xvi | Cash source-of-truth is `paper_cash_ledger`; no mutable balance row | § 4 (6a) |
| xvii | `paper_order.idempotency_key` is UNIQUE; computed deterministically | § 4 (6a) |
| xviii | Every order carries `allocation_run_id` (UUID); shared per batch | § 4 (6a + 6g) |
| xix | `paper_order.status` is PLACED/ENTRY_FILLED/CANCELLED (NOT "FILLED") | § 4 (6a) |
| xx | `paper_order` rows created ONLY for won allocation outcomes | § 4 (6a) |
| xxi | `paper_cash_ledger.balance_after` ordered by monotonic `id`; latest is truth | § 4 (6a) |
| xxii | DB persistence uses `Decimal(18, 6)` from Phase 6a Day 1; internal math stays float | § 4 (6a) |
| xxiii | All Phase 6 production code reads time via injected `Clock`; no `date.today()` | § 5 (6a + 6b + 6e + 6f) |
| xxiv | `tick(as_of=D)` is idempotent | § 5 (6a) |
| xxv | Scheduler is thin single-purpose wrapper around `engine.tick()` | § 5 (6a) |
| xxvi | Test taxonomy extended with `# Layer: stateful` category | § 5 (6a) |
| xxvii | `place_order` is transactional; accepted and rejected are atomic | § 5 (6a) |
| xxviii | Every `paper_order` carries `strategy_version` + `allocator_version` + `execution_engine_version` for replay determinism | § 5 (6a) |
| xxix | All timestamps stored in UTC; market-hours logic evaluated in `America/New_York` | § 5 (6a + 6b) |
| xxx | Idempotency check runs BEFORE risk gates in `place_order` | § 5 (6a) |
| xxxi | DB persistence uses `Decimal(18, 6)` from Phase 6a Day 1 (companion to lock xxii) | § 5 (6a) |
| xxxii | `marketpulse/trading/calendar.py` is the single canonical trading-calendar source | § 5 (6a + 6b + 6f) |

---

## Appendix B — System-evolution status

**Phase 6 is the first phase where MarketPulse stops being purely deterministic.**

Phase 1-5 are reproducible end-to-end given the same inputs:
- Phase 1-2: deterministic AI analysis (LLM is the only stochastic element; otherwise functional)
- Phase 3: deterministic strategy routing
- Phase 4: deterministic single-strategy backtest (historical data is fixed)
- Phase 5a-5e: deterministic shared-pool backtest (same fixture → same result)

Phase 6 introduces stateful temporal progression: today's `tick()` produces fills that change tomorrow's risk-gate state. The system is no longer hermetic — it carries history forward. This is why locks xvii (idempotency), xxiv (idempotent tick), xxvii (transactional place_order) are load-bearing for the umbrella architecture.

Phase 7 introduces nondeterminism from broker callbacks. Phase 6's deterministic clock + DB-canonical-state foundation is what makes Phase 7's adapter-swap viable: the only thing changing is the input source (broker WebSocket vs. tick()-driven outcome math); everything else stays.

This is the architectural contract Phase 6 hands to Phase 7.

---

**End of umbrella spec.**
