# Phase 7b — IBKR Paper Broker Execution Pilot

**Date:** 2026-05-24
**Status:** Design approved, ready to plan
**Depends on:** Phase 7a-Flex read-only broker truth capture

## Goal

Phase 7b proves that MarketPulse can safely touch IBKR's order-mutating API in a
paper account, with full local provenance and without connecting strategy output
to broker execution.

This phase is a controlled manual pilot, not a production broker OMS. Success is
defined by safety-boundary enforcement and auditability: an operator can submit,
observe, and cancel a single controlled IBKR paper order while MarketPulse records
what was attempted and what the broker reported.

## Position in Phase 7

Phase 7 stays split into three distinct layers:

```text
7a = Flex read-only broker truth capture
7b = TWS/Gateway API manual paper execution pilot
7c = broker-vs-paper reconciliation and operator comparison
```

7b does not roll back Phase 7a to the old IB Gateway sidecar read-sync design.
The old sidecar path tried to use IB Gateway as the read-only broker snapshot
transport. Phase 7a now uses Flex Web Service for that job because it is a better
fit for NAS-friendly unattended read capture.

7b reintroduces TWS/IB Gateway only for the capability Flex does not provide:
manual order write-path smoke testing. Flex remains the authoritative 7a
broker-truth snapshot path.

## Hard Locks

### Scope and safety

- **7b-L1:** 7b is paper-account only. Live account execution is structurally refused.
- **7b-L2:** 7b does not connect strategy allocation output to broker execution. No scheduler auto-ordering.
- **7b-L3:** 7b has no web UI button. Trigger is manual CLI only.
- **7b-L4:** 7a Flex read-only sync remains unchanged and remains the broker-truth capture path.
- **7b-L5:** 7b supports only minimal order operations: place paper order, query status, cancel paper order. No modify/replace, no options exercise, no bracket/OCO/algo orders.
- **7b-L6:** First supported order type is low-risk and explicit: stock, quantity, side, `order_type=LMT`, limit price required. No market orders in MVP.
- **7b-L7:** 7b write transport is TWS / IB Gateway official API, used only for controlled manual paper execution smoke.
- **7b-L8:** 7b may require operator-run local TWS/Gateway. NAS unattended Gateway automation is explicitly deferred.
- **7b-L9:** 7b does not revive the old 7a Gateway read-sync path. 7a Flex remains the authoritative broker-truth snapshot path.
- **7b-L10:** 7b supports only paper-account manual place/status/cancel for STK LMT orders. No market orders, no auto strategy execution, no scheduler, no UI.
- **7b-L11:** First smoke order defaults to `transmit=false` or equivalent staged behavior when available. Any transmitted paper order requires explicit CLI flag confirmation.
- **7b-L60:** No automated retry/reconnect/order replay logic exists in 7b MVP. Broker write attempts are single-shot operator actions.
- **7b-L61:** 7b acceptance is defined by provenance correctness and safety-boundary enforcement, not by successful market execution/fill.

### Local persistence boundary

- **7b-L12:** 7b persists broker execution pilot provenance into dedicated `broker_order_intent` and `broker_order_event` tables.
- **7b-L13:** 7b never writes `paper_order`, `paper_fill`, `paper_position`, or `paper_cash_ledger`, and never advances MarketPulse paper lifecycle state.
- **7b-L14:** `broker_order_event` rows are observational/provenance records, not strategy execution state.
- **7b-L15:** Cancel/status operations are limited to locally known 7b `broker_order_intent` rows. Arbitrary broker-order lookup/cancel is deferred.
- **7b-L16:** `broker_order_intent` is append-only except status transitions on the same intent. `broker_order_event` is strictly append-only.
- **7b-L17:** 7b CLI must create `broker_order_intent` before calling the broker write API, so failed broker calls still leave local provenance.
- **7b-L18:** `broker_order_intent.status` tracks local command lifecycle only. IBKR order lifecycle is represented by append-only `broker_order_event` rows.

### CLI shape

- **7b-L19:** 7b exposes one manual CLI, `scripts/ibkr_paper_order.py`, with `place`, `status`, and `cancel` subcommands.
- **7b-L20:** `place` defaults to `transmit=false`. Any transmitted order requires `--transmit true --confirm-transmit PAPER`.
- **7b-L21:** `cancel` requires `--intent-id` and `--confirm-cancel`. Arbitrary broker order cancellation is deferred.
- **7b-L22:** `status` requires `--intent-id`. Arbitrary broker order lookup is deferred.
- **7b-L23:** `place` requires an explicit limit price. Market orders are rejected at CLI validation.
- **7b-L24:** `place` supports only STK LMT orders in 7b MVP. No options, futures, forex, crypto, bracket, OCO, algo, or trailing orders.

### Account safety

- **7b-L25:** 7b requires explicit `--account` for every place/status/cancel command. No automatic account selection.
- **7b-L26:** 7b refuses live or unknown account classification before calling any order-mutating API.
- **7b-L27:** 7b does not honor `MP_IBKR_ALLOW_LIVE` for order placement. Live execution is out of scope regardless of configuration.
- **7b-L28:** 7b validates that the connected TWS/Gateway `managedAccounts()` includes the requested paper account before place/cancel/status.
- **7b-L29:** Connection failures, account mismatch, safety validation failures, and broker API failures leave local `broker_order_intent` / `broker_order_event` evidence whenever an intent can be created.
- **7b-L30:** Account environment classification is based on explicit account id, not port. Only `DU*` is accepted in 7b MVP.
- **7b-L31:** The broker adapter must validate account safety before constructing or submitting an `Order` object.

### Adapter boundary

- **7b-L32:** 7b uses the official IBKR TWS API Python package (`ibapi`) for order-mutating operations.
- **7b-L33:** Only `marketpulse/broker/ibkr_order_client.py` may import `ibapi`.
- **7b-L34:** All orchestration code depends on MarketPulse-owned DTOs and Protocols, not raw IBKR objects.
- **7b-L35:** The adapter public surface exposes only `place_lmt_order`, `fetch_order_status`, and `cancel_order`. No modify/replace/global cancel/options exercise APIs exist in 7b.
- **7b-L36:** `ibapi` callback objects (`EWrapper`/`EClient` callbacks) must be normalized into immutable MarketPulse DTO/event records before leaving `ibkr_order_client.py`.
- **7b-L37:** No raw `ibapi` `orderId`/`orderStatus`/`openOrder` callback state is shared directly with orchestration or persistence layers.

### Idempotency and broker identity

- **7b-L38:** Each `place` intent has a `local_idempotency_key`. Duplicate keys for the same account/action are rejected before broker API calls.
- **7b-L39:** IBKR `orderRef` must include local `broker_order_intent.id` or `local_idempotency_key` for provenance. Format: `MP-7B-{intent_id}-{short_key}`.
- **7b-L40:** `broker_order_id` is assigned only from IBKR/TWS `nextValidId` or broker callback data. It is never guessed locally.
- **7b-L41:** `transmit=false` orders are recorded as staged / TWS-local events, not market-submitted events.
- **7b-L42:** `status` and `cancel` resolve broker identity only from locally persisted place intent data.
- **7b-L43:** `broker_order_id` must be persisted on the original place intent once known.
- **7b-L44:** If `place` fails before `broker_order_id` is known, the intent remains failed and is not eligible for status/cancel.
- **7b-L45:** `cancel` creates its own cancel intent that references the original place intent via `parent_intent_id`.
- **7b-L46:** `status` creates its own `status_check` intent that references the original place intent via `parent_intent_id`.

### Event taxonomy

- **7b-L47:** MVP event taxonomy is fixed and finite. Raw IBKR status strings are stored in `broker_status`, not promoted to new `event_type` values.
- **7b-L48:** Safety/config failures emit explicit events when an intent exists: `safety_rejected`, `account_mismatch`, `connection_failed`.
- **7b-L49:** `transmit=false` successful place emits `staged_to_tws`. `transmit=true` successful place emits `submitted_to_broker` plus any observed status events.
- **7b-L50:** `filled` is observational only. It never writes `paper_fill` and never updates `paper_position`.
- **7b-L51:** Unknown/unmapped IBKR callbacks are stored in sanitized `raw` and surfaced as `order_status_seen` or `error`, not as new schema states.
- **7b-L52:** Every terminal failure path must produce at least one `broker_order_event` when intent creation succeeded.
- **7b-L53:** `broker_order_event.raw` must be sanitized JSON only; no raw `ibapi` objects, no credentials, no session tokens.
- **7b-L54:** `event_type` is controlled by MarketPulse mapping; `broker_status` preserves IBKR-native status text separately.

### Acceptance

- **7b-L55:** Automated tests use fake clients only. No real IBKR connection is required or allowed in CI.
- **7b-L56:** Manual IBKR smoke is required for acceptance, but diagnostic failure evidence is acceptable when TWS/Gateway is unavailable.
- **7b-L57:** 7b verification includes explicit paper-table isolation proof before/after execution attempts.
- **7b-L58:** Architecture guards prove the write path is unreachable from scheduler, `daily_cycle`, web routes, and strategy allocation flows.
- **7b-L59:** First manual acceptance smoke uses `transmit=false`. Transmitted paper-order smoke is optional and separately confirmed.
- **7b-L62:** `status` only promises to observe order state visible in the current TWS/Gateway session. Historical broker truth remains a 7a Flex responsibility.
- **7b-L63:** Cancelling a `transmit=false` staged order is represented separately from broker-side cancellation: `staged_cancelled`, `broker_cancel_requested`, and `cancelled` are distinct event meanings.
- **7b-L64:** `broker_order_intent.status` is a finite DB-constrained enum. Terminal statuses are `completed`, `rejected`, and `failed`.
- **7b-L65:** `broker_order_event.event_type` is protected by a DB CHECK constraint matching the fixed MVP taxonomy.
- **7b-L66:** `local_idempotency_key` uniqueness is enforced at the DB layer for `(account_id, action, local_idempotency_key)`, in addition to service-level validation.
- **7b-L67:** IBKR `orderRef` must stay short enough for broker compatibility. MVP format is `MP-7B-{intent_id}-{short_key}`, where `short_key` is 8-12 characters and the full value is at most 32 characters.
- **7b-L68:** Adapter waits are bounded. `nextValidId` timeout defaults to 10 seconds; place/status/cancel broker observation timeout defaults to 15 seconds.
- **7b-L69:** After `placeOrder` is called, 7b waits for at least one interpretable outcome: `staged_to_tws`, `submitted_to_broker`, `rejected`, `error`, or timeout. If `placeOrder` was called but no callback arrives before timeout, the local intent remains `sent`, command result status is `sent`, and an `error` event with `callback_timeout` is appended. If `placeOrder` was never called, the local intent is `failed`.
- **7b-L70:** MVP idempotency semantics are strict for `place`. `status` and `cancel` child intents should use generated idempotency keys; operator-supplied keys for those actions are not encouraged in MVP.
- **7b-L71:** `broker_order_event` records an event source: `adapter_callback`, `service_safety`, `cli_validation`, or `timeout`.
- **7b-L72:** A `transmit=false` staged order path must not emit `filled`. Filled observations are valid only for transmitted broker-side orders.
- **7b-L73:** Manual smoke documentation must state that `transmit=false` staged orders may not appear in 7a Flex snapshots because they were not submitted/executed at IBKR.

## Data Model

### `broker_order_intent`

One row represents an operator command issued through the 7b CLI.

| Column | Meaning |
|---|---|
| `id` | Local primary key |
| `created_at` | Server-side UTC creation time |
| `operator_source` | MVP value: `cli` |
| `action` | `place`, `cancel`, `status_check` |
| `broker` | MVP value: `IBKR` |
| `broker_environment` | MVP value: `paper`; live/unknown refused |
| `account_id` | Explicit requested paper account |
| `symbol` | Stock ticker for place; copied from parent for status/cancel |
| `asset_class` | MVP value: `STK` |
| `side` | `BUY` or `SELL` for place; copied from parent where useful |
| `quantity` | Decimal quantity |
| `order_type` | MVP value: `LMT` |
| `limit_price` | Decimal limit price for place |
| `transmit` | Defaults false |
| `local_idempotency_key` | Operator or system-generated key for duplicate prevention |
| `parent_intent_id` | Original place intent for cancel/status_check |
| `broker_order_id` | IBKR/TWS order id once known |
| `broker_perm_id` | IBKR permanent id once observed |
| `status` | Local lifecycle: `created`, `sent`, `completed`, `rejected`, `failed` |
| `context` | Sanitized CLI args, confirmations, connection settings without secrets |

`status` is intentionally local. It does not mean "IBKR order status". The DB
constraint allows only `created`, `sent`, `completed`, `rejected`, and `failed`.
The terminal values are `completed`, `rejected`, and `failed`; `sent` is a
non-terminal ambiguous state meaning the write API was called but final broker
interpretation was not proven during the bounded observation window.

The DB also enforces duplicate protection for local command provenance:

```text
UNIQUE(account_id, action, local_idempotency_key)
```

This is a safety guard against concurrent CLI invocations bypassing service-level
idempotency checks. The MVP uses strict idempotency semantics for `place`. For
`status_check` and `cancel`, the CLI should generate a fresh key per child intent;
operator-provided child keys are not part of the MVP UX.

### `broker_order_event`

One row represents a safety, connection, or broker observation related to an
intent. Rows are append-only.

| Column | Meaning |
|---|---|
| `id` | Local primary key |
| `intent_id` | FK to `broker_order_intent` |
| `observed_at` | UTC observation time |
| `event_type` | One of the finite MVP values below |
| `event_source` | `adapter_callback`, `service_safety`, `cli_validation`, or `timeout` |
| `broker_order_id` | IBKR/TWS order id if known |
| `broker_perm_id` | IBKR permanent id if known |
| `broker_status` | IBKR-native status string, e.g. `Submitted`, `PreSubmitted`, `Filled` |
| `filled_quantity` | Decimal filled quantity if observed |
| `remaining_quantity` | Decimal remaining quantity if observed |
| `avg_fill_price` | Decimal average fill price if observed |
| `message` | Human-readable sanitized summary |
| `raw` | Sanitized JSON payload |

MVP `event_type` enum:

```text
safety_rejected
connection_failed
account_mismatch
next_valid_id_received
staged_to_tws
submitted_to_broker
open_order_seen
order_status_seen
broker_cancel_requested
staged_cancelled
cancelled
filled
rejected
error
```

The migration must enforce this taxonomy with a DB CHECK constraint. IBKR-native
strings such as `Submitted`, `PreSubmitted`, `Filled`, `ApiCancelled`, or
`Inactive` belong in `broker_status`, never in `event_type`.

`event_source` is also finite and DB-constrained. It lets an operator distinguish
events created by service-level safety checks from events normalized from broker
callbacks or timeout handling.

## CLI

One script owns the manual write path:

```bash
uv run python scripts/ibkr_paper_order.py place \
  --account DUxxxx \
  --symbol AAPL \
  --side BUY \
  --quantity 1 \
  --limit-price 1.00 \
  --transmit false

uv run python scripts/ibkr_paper_order.py status \
  --account DUxxxx \
  --intent-id 123

uv run python scripts/ibkr_paper_order.py cancel \
  --account DUxxxx \
  --intent-id 123 \
  --confirm-cancel
```

Transmitted paper order smoke is intentionally harder to invoke:

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

The first acceptance smoke should use `transmit=false`, `quantity=1`, and a limit
price far away from market, so the pilot verifies local intent creation, safety
validation, TWS/Gateway write acceptance, event persistence, status observation,
and cancellation without optimizing for fill.

## Architecture

```text
scripts/ibkr_paper_order.py
    ↓
marketpulse.broker.order_service
    ↓
marketpulse.broker.order_repository
    ↓
broker_order_intent / broker_order_event

order_service
    ↓ Protocol
marketpulse.broker.order_client.BrokerOrderClient
    ↓ implementation
marketpulse.broker.ibkr_order_client.IbkrOrderClient
    ↓ only module importing ibapi
IBKR TWS / IB Gateway socket API
```

Proposed modules:

```text
marketpulse/broker/order_types.py
marketpulse/broker/order_client.py
marketpulse/broker/ibkr_order_client.py
marketpulse/broker/order_repository.py
marketpulse/broker/order_service.py
scripts/ibkr_paper_order.py
```

The adapter is callback-driven internally, but callback state does not escape the
adapter. It emits immutable MarketPulse DTOs that the service persists as events.

## Command Flows

### Place

```text
place CLI
  → create broker_order_intent(action=place, status=created, idempotency_key=...)
  → validate CLI safety: STK/LMT, limit_price, paper account pattern, confirmations
  → connect to TWS/Gateway
  → validate managedAccounts includes requested account
  → validate account is DU* paper
  → wait nextValidId
  → append next_valid_id_received event
  → build orderRef = MP-7B-{intent_id}-{short_key}
  → build contract/order
  → placeOrder(order_id, contract, order)
  → persist broker_order_id on original intent
  → append staged_to_tws or submitted_to_broker plus observed status events
  → update local intent status
```

### Status

```text
status CLI
  → load original place intent by intent_id
  → require broker_order_id exists
  → create broker_order_intent(action=status_check, parent_intent_id=place_id)
  → connect and validate same requested paper account
  → fetch broker status for known order identity visible in the current TWS/Gateway session
  → append order_status_seen / filled / rejected / error
  → update child intent status
```

7b status is not a historical order archive. If a broker order has disappeared
from the current TWS/Gateway session, the status command may return an `error` or
empty observation event. Historical executions and broker truth remain the job of
7a Flex snapshots.

### Cancel

```text
cancel CLI
  → load original place intent by intent_id
  → require broker_order_id exists
  → require --confirm-cancel
  → create broker_order_intent(action=cancel, parent_intent_id=place_id)
  → connect and validate same requested paper account
  → cancel only the known broker_order_id
  → append broker_cancel_requested and cancelled/order_status_seen/error
  → update child intent status
```

If the original order was `transmit=false`, it may exist only as a TWS-local
staged order. In that case cancellation may be a local TWS delete/cancel
operation rather than a broker-side cancel request, and the event should be
`staged_cancelled` instead of `broker_cancel_requested`/`cancelled` when the
adapter can distinguish it.

## Error Handling

7b is fail-closed. If an intent can be created, failure is recorded locally.
Examples:

| Failure | Intent | Event | Broker write API called? |
|---|---|---|---|
| CLI validation rejects market order | failed place intent | `safety_rejected` | No |
| Account is not `DU*` | failed intent | `safety_rejected` | No |
| TWS/Gateway connection fails | failed intent | `connection_failed` | No order mutation |
| Connected accounts do not include requested account | failed intent | `account_mismatch` | No |
| `nextValidId` never arrives | failed intent | `error` | No |
| `placeOrder` raises/returns error | failed or rejected intent | `error` or `rejected` | Attempted |
| `placeOrder` called but no callback before timeout | sent intent and sent result | `error` with `callback_timeout` | Attempted |
| Status/cancel target lacks `broker_order_id` | failed child intent | `safety_rejected` | No |

No automatic retry, reconnect loop, replay, modification, force close, or
self-healing exists in 7b.

## Settings

7b uses environment/settings plus CLI override for TWS/Gateway connection details.
No web configuration surface exists.

Expected settings shape:

```python
ibkr_order_host: str = "127.0.0.1"
ibkr_order_port: int = 7497
ibkr_order_client_id: int = 72
ibkr_order_connect_timeout_seconds: int = 10
ibkr_order_next_valid_id_timeout_seconds: int = 10
ibkr_order_observation_timeout_seconds: int = 15
```

`MP_IBKR_ALLOW_LIVE` is not honored by 7b order placement. It remains relevant to
read-only 7a behavior only. 7b always refuses live/unknown execution.

## Testing and Acceptance

Automated tests use fake clients only:

- migration creates/drops `broker_order_intent` and `broker_order_event`
- migration enforces intent status CHECK, event type CHECK, and idempotency unique constraint
- fake order client `place` writes intent before broker call
- fake client success writes `broker_order_id`, `orderRef`, and event rows
- `transmit=false` writes `staged_to_tws`
- staged cancel can write `staged_cancelled` separately from broker-side cancel events
- staged `transmit=false` paths never emit `filled`
- `transmit=true` requires `--confirm-transmit PAPER`
- live/unknown account rejected before broker client place/cancel
- duplicate idempotency key rejected before broker call
- status/cancel require a local known place intent with `broker_order_id`
- status documents current-session visibility limits and does not claim historical lookup
- status/cancel create child intents with `parent_intent_id`
- connection/account/broker failures leave events when intent creation succeeded
- `nextValidId` timeout and callback timeout paths produce deterministic local intent/event outcomes
- paper table row counts unchanged before/after pilot commands
- architecture guard: only `ibkr_order_client.py` imports `ibapi`
- architecture guard: scheduler, `daily_cycle`, web routes, and strategy allocation cannot import the order service
- architecture guard: forbidden APIs absent outside the approved adapter surface: modify/replace/global cancel/options exercise

Manual acceptance smoke:

1. Operator starts local TWS or IB Gateway connected to the IBKR paper account.
2. Run `place` with `transmit=false`, `quantity=1`, and a limit price far from market.
3. Confirm local intent/event rows exist.
4. Run `status --intent-id`.
5. Run `cancel --intent-id --confirm-cancel`.
6. Optionally run a second `place` with `--transmit true --confirm-transmit PAPER` after staged smoke passes.

If TWS/Gateway is unavailable, a failed manual smoke is still diagnostic success
when MarketPulse records a failed intent plus structured event details.

The runbook must warn that `transmit=false` staged orders may not appear in 7a
Flex snapshots. That is expected: Flex remains the read-only broker-truth capture
path for submitted/executed account activity, while staged TWS-local orders are a
7b write-path smoke artifact.

## Explicit Deferrals

Deferred from 7b:

- live account execution
- strategy/scheduler/daily-cycle broker execution
- BrokerExecutionEngine production wiring
- web UI, web-triggered sync/order buttons, or `/lab` broker controls
- market orders
- options, futures, forex, crypto
- bracket, OCO, trailing, algo, or conditional orders
- modify/replace order flows
- arbitrary broker-order lookup or cancellation
- automatic retry, reconnect, replay, or remediation
- NAS unattended Gateway automation
- reconciliation UI or paper-vs-broker comparison
- writing any Phase 6 paper lifecycle tables
