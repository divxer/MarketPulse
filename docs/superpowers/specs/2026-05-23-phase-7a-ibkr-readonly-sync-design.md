# Phase 7a - IBKR Paper Account Read-Only Sync Design

**Status:** Draft locked for implementation planning  
**Author:** Codex + Harvey, 2026-05-23  
**Spec-type:** sub-project under Phase 7 broker integration  
**Depends on:** Phase 6 paper-trading MVP (6a, 6b, 6b+, 6f, 6g, 6h)  
**Scope:** IBKR paper account read-only broker-truth capture. No broker execution.

---

## 1 - Goal & Boundary

Phase 7a starts Phase 7 by proving MarketPulse can connect to an IBKR paper
account and persist a trustworthy read-only broker-state snapshot.

**Success standard:**

> MarketPulse can connect to an IBKR paper account and produce a trustworthy
> broker-state snapshot: account, cash, positions, open orders, executions, and
> connection status.

7a is broker-truth capture, not broker execution.

`broker_*` snapshots are observational records only. They are not authoritative
for MarketPulse paper state in 7a, and they do not drive allocation, risk gates,
paper ticks, or local paper lifecycle transitions.

Manual IBKR smoke failure is not a failed implementation if it records a failed
`broker_sync_run` with diagnostic error details.

### In Scope

- Read-only IBKR paper account sync foundation.
- Append-only `broker_*` snapshot schema.
- One-shot operator-triggered CLI sync.
- IBKR client adapter isolated behind a MarketPulse-owned read-only Protocol.
- Fake-client automated tests.
- Architecture guards for read-only and dependency boundaries.
- Operations runbook for IBKR Gateway/TWS setup, CLI usage, and smoke outcomes.

### Explicit Non-Goals

- No `BrokerExecutionEngine`.
- No implementation of `BrokerExecutionEngine.place_order()`.
- No order placement, order modification, order cancellation, or global cancel.
- No scheduler, daemon, watchdog, background sync, or automatic reconnect loop.
- No Web UI, route, dashboard, web-triggered sync, or broker status card.
- No `/lab/paper-trading` broker truth display.
- No paper-vs-broker reconciliation UI.
- No writes to `paper_order`, `paper_fill`, `paper_position`, or
  `paper_cash_ledger`.
- No use of broker truth to drive allocation, risk gates, or paper lifecycle.

---

## 2 - Hard Locks

| Lock | Requirement |
| --- | --- |
| 7a-L1 | IBKR integration starts as read-only. Any API method capable of placing, modifying, exercising, or cancelling orders is forbidden in 7a source and tests. |
| 7a-L2 | IBKR read results are persisted only into `broker_*` snapshot tables. 7a never writes `paper_order`, `paper_fill`, `paper_position`, or `paper_cash_ledger`. |
| 7a-L3 | `broker_*` tables are observational broker truth, not execution state. They do not drive allocation, risk gates, or local paper lifecycle transitions in 7a. |
| 7a-L4 | Each sync is append-only by `sync_run_id`. A new IBKR poll creates a new `broker_sync_run` and new snapshot rows; it does not update prior snapshots. |
| 7a-L5 | Broker account id is persisted on every snapshot row, even if 7a only supports one paper account initially. |
| 7a-L6 | IBKR read-only sync is operator-triggered only in 7a. No scheduler, no background daemon, no web-triggered sync. |
| 7a-L7 | Each CLI invocation creates exactly one `broker_sync_run` and one immutable snapshot set. |
| 7a-L8 | IBKR sync is not called from `paper_trading_tick_job` or `daily_cycle.run` in 7a. |
| 7a-L9 | `ib_insync` is the only IBKR client library used in 7a. |
| 7a-L10 | Only `marketpulse/broker/ibkr_client.py` may import `ib_insync`. All other modules depend on the `BrokerReadClient` Protocol or DTOs. |
| 7a-L11 | `BrokerReadClient` is read-only. Its public surface exposes `fetch_snapshot(...)` only; no place/modify/cancel methods exist in 7a. |
| 7a-L12 | Any use of IBKR order-mutating APIs is forbidden in 7a source and tests: `placeOrder`, `cancelOrder`, `reqGlobalCancel`, `exerciseOptions`, replace/order modify paths. |
| 7a-L13 | IBKR connection configuration is environment/settings driven only. 7a exposes no Web UI configuration surface. |
| 7a-L14 | 7a defaults to IBKR paper trading port `7497` unless explicitly overridden. |
| 7a-L15 | If `IBKR_ACCOUNT_ID` is configured, readonly sync must verify the returned account matches exactly before persisting any snapshot rows. |
| 7a-L16 | If `IBKR_ACCOUNT_ID` is unset: exactly one returned account is allowed; multiple returned accounts produce `broker_sync_run(status="failed")` and no snapshots. |
| 7a-L17 | Connection/configuration failures create `broker_sync_run(status="failed")` with structured `error_type`, `error_message`, and context. |
| 7a-L18 | 7a refuses to connect to known IBKR live trading ports by default, including `7496`, unless `MP_IBKR_ALLOW_LIVE=true` is explicitly set. |
| 7a-L19 | `BrokerSnapshot` and `broker_sync_run` persist broker environment classification: `paper`, `live`, or `unknown`. |
| 7a-L20 | 7a has no Web UI, no route, no dashboard, and no web-triggered sync. |
| 7a-L21 | 7a output surfaces are CLI stdout, `broker_*` tables, tests, and operations documentation only. |
| 7a-L22 | Broker truth is not shown inside `/lab/paper-trading` in 7a. Paper-vs-broker comparison is deferred to 7c reconciliation. |
| 7a-L23 | Automated tests never require a real IBKR connection. |
| 7a-L24 | Real IBKR connectivity is validated only by manual operator smoke, not CI. |
| 7a-L25 | A failed real IBKR smoke is still a valid diagnostic outcome if it creates `broker_sync_run(status="failed")` with structured error details. |
| 7a-L26 | CLI sync creates `broker_sync_run(status="started")` before IBKR connection/configuration/account validation, unless the database itself is unavailable. |
| 7a-L27 | Known live-port refusal happens before connecting to IBKR but after creating a run header that is marked failed. |
| 7a-L28 | Long-lived `broker_sync_run(status="started")` rows are allowed and mean the sync process was interrupted before it could mark completion or failure. |
| 7a-L29 | Broker monetary and quantity values are stored as Decimal-backed numeric columns, not floats. |
| 7a-L30 | Broker execution snapshots use an explicit execution window recorded in `broker_sync_run.context`. |
| 7a-L31 | `broker_open_order_snapshot` never creates, updates, cancels, reconciles, or otherwise drives local `paper_order` state in 7a. |
| 7a-L32 | `marketpulse/trading/*` must not import `marketpulse.broker.readonly_sync`, `marketpulse.broker.repository`, or `marketpulse.broker.ibkr_client` in 7a. |

---

## 3 - Data Model

7a introduces an append-only broker snapshot layer. The table names are
intentionally broker-generic so that later phases can support another broker or
compare IBKR paper and IBKR live accounts without rewriting the storage shape.

### `broker_sync_run`

One row per CLI invocation, including failures.

Fields:

- `id`
- `started_at`
- `completed_at`
- `broker` - `"IBKR"` in 7a
- `broker_environment` - `paper`, `live`, or `unknown`
- `account_id`
- `status` - `started`, `completed`, or `failed`
- `error_type`
- `error_message`
- `context` - JSON diagnostic payload

`broker_sync_run` is the audit header for the snapshot. Failed runs are valuable:
they preserve connection, configuration, and account-selection failures.

The CLI creates `broker_sync_run(status="started")` before connecting to IBKR,
validating the configured account, or applying live-port safety checks. The only
expected exception is database unavailability; if the application cannot open a
DB session or insert the run header, there is nowhere reliable to persist the
failed run.

Live-port refusal (`IBKR_PORT=7496` with `MP_IBKR_ALLOW_LIVE=false`) happens
before any IBKR socket connection attempt, but after the run header is created.
The final row state is `status="failed"` and no snapshot rows are written.

A process crash can leave `status="started"`. 7a does not automatically repair
or rewrite those rows. Runbooks classify a `started` row with no recent progress
as an interrupted sync.

`account_id` may be null on failed runs where account discovery never happened.
Completed runs and all snapshot rows must have a non-empty `account_id`.

Minimum `context` fields:

- `host`
- `port`
- `client_id`
- `configured_account_id`
- `selected_account_id`
- `allow_live`
- `execution_window_start`
- `execution_window_end`

For completed runs, all fields above must be present. For failed runs,
`selected_account_id`, `execution_window_start`, and `execution_window_end` are
best-effort because live-port refusal, account-selection failure, or connection
failure may happen before those values are known.

### `broker_account_snapshot`

One account-level snapshot row per completed sync.

Fields:

- `sync_run_id`
- `account_id`
- `broker_environment`
- `captured_at`
- `account_type`
- `base_currency`
- `net_liquidation` - Decimal-backed numeric
- `buying_power` - Decimal-backed numeric
- `maintenance_margin` - Decimal-backed numeric
- `excess_liquidity` - Decimal-backed numeric

### `broker_cash_snapshot`

One row per account/currency cash balance returned by IBKR.

Fields:

- `sync_run_id`
- `account_id`
- `broker_environment`
- `captured_at`
- `currency`
- `cash_balance` - Decimal-backed numeric
- `settled_cash` - Decimal-backed numeric
- `accrued_interest` - Decimal-backed numeric

### `broker_position_snapshot`

One row per account/symbol position returned by IBKR.

Fields:

- `sync_run_id`
- `account_id`
- `broker_environment`
- `captured_at`
- `symbol`
- `asset_class`
- `quantity` - Decimal-backed numeric
- `avg_cost` - Decimal-backed numeric
- `market_price` - Decimal-backed numeric
- `market_value` - Decimal-backed numeric
- `unrealized_pnl` - Decimal-backed numeric
- `realized_pnl` - Decimal-backed numeric

### `broker_open_order_snapshot`

One row per open order returned by IBKR.

Fields:

- `sync_run_id`
- `account_id`
- `broker_environment`
- `captured_at`
- `broker_order_id`
- `symbol`
- `side`
- `order_type`
- `quantity` - Decimal-backed numeric
- `limit_price` - Decimal-backed numeric, nullable for non-limit orders
- `status`

`broker_open_order_snapshot` is read-only broker truth. It must not create or
update `paper_order`, trigger cancellation, or participate in reconciliation in
7a.

### `broker_execution_snapshot`

One row per execution returned by IBKR for the execution window.

Fields:

- `sync_run_id`
- `account_id`
- `broker_environment`
- `captured_at`
- `broker_exec_id`
- `broker_order_id`
- `symbol`
- `side`
- `quantity` - Decimal-backed numeric
- `price` - Decimal-backed numeric
- `executed_at`

7a MVP uses a conservative execution window: NY trading-day midnight through the
sync capture time. The selected window is recorded on `broker_sync_run.context`
as `execution_window_start` and `execution_window_end`. Future phases may add a
configurable lookback, but 7a must make every execution snapshot comparable by
persisting the window used for that sync.

IBKR execution filtering has broker-specific timezone and availability behavior.
7a execution rows are therefore a best-effort read snapshot for the configured
window, not a complete historical execution archive. The runbook must make this
operator-visible.

### Numeric Precision

All monetary, price, quantity, market value, cost, and PnL fields use
Decimal-backed SQLAlchemy `Numeric` columns. The implementation may choose the
project's standard precision, or `Numeric(18, 6)` if no stronger local standard
exists. 7a must not persist broker numeric values as floats.

### Append-Only Semantics

Every successful sync writes a complete snapshot set for its `sync_run_id`.
Existing snapshot rows are never updated to represent newer broker state.

Running sync twice against the same fake or real account creates two
`broker_sync_run` rows and two independent snapshot sets. The first run remains
unchanged.

Every snapshot row carries `captured_at`, matching the `BrokerSnapshot` capture
time. Consumers may still join through `broker_sync_run` for run metadata, but
simple analysis does not need a join just to know when a row was captured.

---

## 4 - Broker Package Architecture

7a adds a new broker bounded context:

```text
marketpulse/broker/
  __init__.py
  types.py
  read_client.py
  ibkr_client.py
  readonly_sync.py
  repository.py
```

### `types.py`

Owns pure DTOs. These DTOs are the only values that leave the adapter boundary.
They must not contain `ib_insync` objects.

```python
@dataclass(frozen=True)
class BrokerSnapshot:
    broker: Literal["IBKR"]
    broker_environment: Literal["paper", "live", "unknown"]
    account_id: str
    captured_at: datetime
    account: BrokerAccount
    cash: tuple[BrokerCash, ...]
    positions: tuple[BrokerPosition, ...]
    open_orders: tuple[BrokerOpenOrder, ...]
    executions: tuple[BrokerExecution, ...]
```

Companion DTOs:

- `BrokerAccount`
- `BrokerCash`
- `BrokerPosition`
- `BrokerOpenOrder`
- `BrokerExecution`

### `read_client.py`

Defines the read-only port:

```python
class BrokerReadClient(Protocol):
    def fetch_snapshot(self) -> BrokerSnapshot: ...
```

No mutating methods exist in 7a.

### `ibkr_client.py`

Contains `IbkrReadClient` and is the only module allowed to import
`ib_insync`.

Responsibilities:

- connect to IBKR Gateway/TWS;
- fetch account, cash, positions, open orders, and executions;
- map IBKR objects into pure MarketPulse DTOs;
- disconnect cleanly after the one-shot sync;
- raise structured exceptions for connection/config/account-selection failures.

It must not expose raw IBKR objects to the rest of the application.

`IbkrReadClient` receives connection configuration at construction time: host,
port, client id, timeout, and account id hint. `fetch_snapshot()` remains
argument-free so the application uses one stable `BrokerReadClient` Protocol.
The sync layer owns live-port policy and final account selection validation.

### `readonly_sync.py`

Orchestrates:

```text
create broker_sync_run(status="started")
  -> environment/live-port validation
  -> BrokerReadClient.fetch_snapshot()
  -> account validation
  -> broker repository writes
  -> SyncResult for CLI output
```

Failure path:

- update the started run to failed;
- include structured error metadata;
- do not write snapshot rows.

### `repository.py`

Owns `broker_*` writes only.

It must not import or call paper-trading repository mutation helpers. It may use
the shared SQLAlchemy session and model declarations, but the write surface is
restricted to `broker_*` tables.

---

## 5 - CLI

7a exposes one operator-triggered command:

```bash
uv run python scripts/sync_ibkr_readonly.py
```

The CLI reads defaults from settings/env and accepts explicit overrides:

```bash
uv run python scripts/sync_ibkr_readonly.py \
  --host 127.0.0.1 \
  --port 7497 \
  --client-id 71 \
  --account-id DUxxxxxxx
```

Required output on success:

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

Required output on failure:

```text
sync_run_id: 124
broker: IBKR
broker_environment: unknown
host: 127.0.0.1
port: 7497
client_id: 71
status: failed
error_type: ConnectionError
error_message: ...
```

Exit codes:

- `0` for completed sync.
- non-zero for failed sync.

Failed sync still writes `broker_sync_run(status="failed")`, unless the database
itself is unavailable before a run header can be created.

---

## 6 - Settings & Connection Safety

7a extends `Settings` with IBKR read-only sync configuration:

```python
ibkr_host: str = Field("127.0.0.1", alias="IBKR_HOST")
ibkr_port: int = Field(7497, alias="IBKR_PORT")
ibkr_client_id: int = Field(71, alias="IBKR_CLIENT_ID")
ibkr_account_id: str = Field("", alias="IBKR_ACCOUNT_ID")
ibkr_connect_timeout_seconds: int = Field(
    10,
    alias="IBKR_CONNECT_TIMEOUT_SECONDS",
)
ibkr_allow_live: bool = Field(False, alias="MP_IBKR_ALLOW_LIVE")
```

### Account Selection

If `IBKR_ACCOUNT_ID` is configured:

- the sync must verify that IBKR returned that exact account id;
- mismatch creates a failed run;
- no snapshot rows are persisted.

If `IBKR_ACCOUNT_ID` is unset:

- exactly one returned account is allowed;
- multiple returned accounts create a failed run;
- no snapshot rows are persisted for ambiguous accounts.

### Environment Classification

7a classifies the configured connection as:

- `paper` for known paper port `7497`;
- `live` for known live port `7496`;
- `unknown` otherwise.

By default, 7a refuses known live ports. Connecting to `7496` requires
`MP_IBKR_ALLOW_LIVE=true`. Even when allowed, the sync remains read-only.

Live-port refusal is a persisted diagnostic outcome: it creates a failed
`broker_sync_run` with no snapshot rows and does not attempt an IBKR connection.

---

## 7 - UI Boundary

7a has no UI.

There is no:

- `/lab/broker-sync`;
- `/lab/reconciliation`;
- web sync button;
- scheduler sync;
- broker status card inside `/lab/paper-trading`;
- paper-vs-broker comparison UI.

7a output surfaces are:

- CLI stdout;
- `broker_*` tables;
- tests;
- operations documentation.

7c owns reconciliation and operator-facing broker-vs-paper comparison.

---

## 8 - Testing & Acceptance

7a acceptance is fake-client automated proof plus real IBKR manual smoke.

### Automated Tests

Automated tests never require a real IBKR connection.

Required coverage:

- fake completed sync writes a completed `broker_sync_run` and snapshot rows;
- connection failure writes a failed `broker_sync_run` and no snapshot rows;
- account mismatch fails closed and writes no snapshot rows;
- multiple accounts with no configured `IBKR_ACCOUNT_ID` fail closed;
- live-port block creates a failed `broker_sync_run` before connecting and writes
  no snapshot rows;
- stale `started` rows are representable and documented as interrupted syncs;
- running sync twice creates two independent snapshot sets;
- first-run snapshot rows are not updated by the second run;
- persisted broker money, price, quantity, value, cost, and PnL fields are
  Decimal-backed numeric values, not floats;
- execution snapshots record `execution_window_start` and
  `execution_window_end` in run context;
- `paper_order`, `paper_fill`, `paper_position`, and `paper_cash_ledger` row
  counts are unchanged before/after sync;
- architecture guards pass;
- migration upgrade/downgrade works;
- `uv run alembic heads` reports a single head.

### Architecture Guards

Required guards:

- only `marketpulse/broker/ibkr_client.py` imports `ib_insync`;
- 7a source and tests contain no mutating IBKR API usage:
  - `placeOrder`
  - `cancelOrder`
  - `reqGlobalCancel`
  - `exerciseOptions`
  - replace/order modify paths
- `paper_trading_tick_job` and `daily_cycle.run` do not import or call broker
  sync;
- `marketpulse/trading/*` does not import `marketpulse.broker.readonly_sync`,
  `marketpulse.broker.repository`, or `marketpulse.broker.ibkr_client`;
- 7a repository code does not call paper-trading repository mutation helpers.

### Manual IBKR Smoke

Manual smoke command:

```bash
uv run python scripts/sync_ibkr_readonly.py
```

Success criteria:

- stdout reports `sync_run_id`, broker, environment, account, status, and row
  counts;
- DB contains matching `broker_sync_run`;
- completed run has account/cash/position/open-order/execution rows as returned
  by IBKR;
- failure case leaves a failed run with `error_type` and `error_message`.

A failed real IBKR smoke is not an implementation failure when it records a
diagnostic failed run. That outcome means the sync foundation can observe and
explain IBKR connectivity/configuration problems.

---

## 9 - Operations Documentation

7a adds an operator-first runbook covering:

- IBKR Gateway/TWS paper account setup;
- expected paper port `7497`;
- live port refusal behavior;
- interrupted `started` run interpretation;
- required and optional environment variables;
- one-shot CLI command;
- successful stdout example;
- failed stdout example;
- how to inspect latest `broker_sync_run`;
- how to classify connection/config/account mismatch failures;
- what evidence to capture before escalating.

The runbook must not require reading source code as the primary diagnosis path.
It must also state that 7a execution snapshots are best-effort rows for the
configured execution window, not a complete historical execution archive.

---

## 10 - Migration Downgrade

7a adds new `broker_*` tables only. Downgrade may drop those tables because no
pre-existing data model depends on them.

Drop order must respect foreign-key dependencies:

1. `broker_execution_snapshot`
2. `broker_open_order_snapshot`
3. `broker_position_snapshot`
4. `broker_cash_snapshot`
5. `broker_account_snapshot`
6. `broker_sync_run`

---

## 11 - Deferred Work

Deferred to later Phase 7 work:

- `BrokerExecutionEngine`;
- order placement/modification/cancellation;
- broker idempotency keys and `client_order_id` / `permId` mapping;
- partial-fill lifecycle mapping;
- paper-vs-broker reconciliation;
- `/lab/broker-sync`;
- `/lab/reconciliation`;
- broker status cards inside `/lab/paper-trading`;
- scheduler or daemon sync;
- automatic reconnect loops;
- real-money risk gate changes;
- Postgres reassessment for broker reconciliation and streaming writes.

---

## 12 - Phase 7 Follow-On Shape

7a captures broker truth.

7b may introduce broker execution, but only after 7a proves connection,
account-selection, data mapping, and read-only safety.

7c should consume `broker_*` snapshots for reconciliation and operator-facing
comparison. It should not rely on live IBKR queries as its only source of truth.
