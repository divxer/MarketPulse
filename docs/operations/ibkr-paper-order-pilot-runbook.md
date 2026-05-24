# IBKR Paper Order Pilot Runbook (Phase 7b)

## Scope

This runbook is the operator-facing manual smoke guide for the Phase 7b IBKR
paper-order execution pilot. It is **paper-only**, **manual CLI only**, and has
**no automation**. It complements the Phase 7a Flex sync, which remains
read-only — Phase 7b adds the first (and only) write path against IBKR, and
only against paper (`DU*`) accounts via a local TWS or IB Gateway socket.

Nothing in 7b is scheduled, exposed to the web UI, or run from any strategy.
Every order intent is initiated by a human operator running a CLI command,
explicitly naming the account, and (for transmission) typing a confirmation
token.

---

## 1. Preconditions

Before running any 7b command, confirm all of the following:

- IBKR paper account is active and the account ID matches `DU<letters>*<digits>`
  (paper accounts always begin with `DU`).
- A local **TWS** or **IB Gateway** is running on this machine and is logged
  into the paper account.
- The TWS API socket is enabled:
  - File → Global Configuration → API → Settings → **Enable ActiveX and Socket Clients**.
  - Default port **7497** for TWS paper, **4002** for IB Gateway paper.
- **TWS "Read-Only API" checkbox in API Precautions must be DISABLED for
  this phase.** 7b needs write capability to stage and transmit orders. (If
  you are only running 7a Flex sync, re-enable Read-Only API afterwards.)
  After 7b experimentation is over, re-enable Read-Only API in TWS to
  prevent accidental write exposure.
- DB migration `0013` has been applied:
  ```
  uv run alembic upgrade head
  ```
  This creates the `broker_order_intent` and `broker_order_event` tables that
  the 7b CLI writes to.

---

## 1a. Test coverage gap — real TWS smoke is the only verification

Automated tests in `tests/broker/test_ibkr_order_client_class.py` use a
fake-app substitute injected via `app_factory`. They validate the adapter's
Event-wait/timeout logic and the observation-buffer semantics, but they DO
NOT exercise real `ibapi` callback ordering, real `EClient.run()`
reader-thread interleaving, or real TWS-side error code mapping. The manual
smoke procedures below are therefore the only end-to-end verification of the
threading code under realistic conditions. **Do not run `--transmit true`
until you have first completed a `--transmit false` (staged) smoke** that
exercises connect → nextValidId → placeOrder → status → cancel against a
real TWS instance.

---

## 2. One-Time Setup

### Environment variables (or pass via CLI flags)

| Variable                 | Default       | Purpose                                                              |
| ------------------------ | ------------- | -------------------------------------------------------------------- |
| `IBKR_ORDER_HOST`        | `127.0.0.1`   | Host where TWS/Gateway is listening.                                 |
| `IBKR_ORDER_PORT`        | `7497`        | Socket port. TWS paper=7497, Gateway paper=4002.                     |
| `IBKR_ORDER_CLIENT_ID`   | `72`          | API client ID. **Must be unique** vs. every other client on the same TWS (Flex sync, manual TWS, etc.). |

### Trusted IPs in TWS

In TWS: File → Global Configuration → API → Settings → **Trusted IPs**, add
`127.0.0.1` (and any other IP from which the marketpulse client will connect).
Without this, TWS will prompt for manual approval on every connection.

Keep the marketpulse client ID (`IBKR_ORDER_CLIENT_ID`) distinct from any
client ID used by the Phase 7a Flex sync or by your own manual TWS API
sessions — TWS rejects duplicate client IDs.

---

## 3. Safety Rules

These rules are enforced in code; this section restates them in operator
language so you know what to expect and what will be refused.

- **Paper-only.** The CLI accepts only accounts matching `DU*`. Live accounts
  are refused at the CLI gate and again at the service layer. There is no
  flag, no env var, no override that bypasses this in 7b.
- **`MP_IBKR_ALLOW_LIVE` does not apply.** That env var governs 7a Flex
  fetches against live accounts. 7b ignores it — live order routes are always
  refused, regardless of `MP_IBKR_ALLOW_LIVE`.
- **`--transmit false` is the default.** With the default, the order is
  **staged** in TWS but **not submitted** to the broker. You will see it in
  the TWS UI with a "transmit" button next to it; nothing has gone to IBKR
  yet.
- **To actually submit:** pass both `--transmit true` and
  `--confirm-transmit PAPER`. You must type the literal token `PAPER` as the
  value of `--confirm-transmit`. Anything else (including no flag) refuses
  transmission.
- **Cancel requires `--confirm-cancel`.** Without that flag, `cancel` is
  refused.
- **`--account` is always required.** No default account, no
  "last-used" account, no implicit fallback.
- **7b does not write to `paper_*` tables.** Those belong to the Phase 6
  paper-trading lifecycle. 7b writes only to `broker_order_intent` and
  `broker_order_event`. Phase 7c will handle reconciliation between the two.

---

## 4. First Smoke (Must Pass)

This is the **recommended first run**. It stages an order in TWS without
submitting it to the broker, so there is zero risk of an unintended fill.

```bash
uv run python scripts/ibkr_paper_order.py place \
  --account DUxxxxxxx \
  --symbol AAPL \
  --side BUY \
  --quantity 1 \
  --limit-price 1.00 \
  --transmit false
```

### Expected output

- An `intent_id` (integer, freshly created row in `broker_order_intent`).
- `status: completed`
- Events recorded against the intent including:
  - `next_valid_id_received`
  - `staged_to_tws`

### Inspect the intent

```bash
uv run python scripts/ibkr_paper_order.py status \
  --account DUxxxxxxx \
  --intent-id <N>
```

### Cancel the staged intent

```bash
uv run python scripts/ibkr_paper_order.py cancel \
  --account DUxxxxxxx \
  --intent-id <N> \
  --confirm-cancel
```

If this full sequence succeeds, the local 7b plumbing — DB writes, TWS
connection, callback handling, idempotency — is healthy.

---

## 5. Flex Visibility Caveat

**Staged orders (`--transmit false`) do NOT appear in Phase 7a Flex
snapshots.** Flex queries return only activity that has been *submitted to and
recorded by the broker* — open orders, executions, fills. A staged order
lives entirely in TWS' local state and never reaches IBKR's brokerage
backend, so Flex has nothing to report.

This is expected behavior. Do not file a bug because a `--transmit false`
order is "missing from Flex". If you want the order to appear in Flex, you
must transmit it (see next section).

---

## 6. Optional Transmitted Paper Smoke

**Only run this after the staged smoke (Section 4) has passed.**

```bash
uv run python scripts/ibkr_paper_order.py place \
  --account DUxxxxxxx \
  --symbol AAPL \
  --side BUY \
  --quantity 1 \
  --limit-price 1.00 \
  --transmit true \
  --confirm-transmit PAPER
```

Pick a `--limit-price` that is **far from market** (e.g. $1.00 on a $200
stock for a BUY) so the order will not fill. The purpose of this smoke is to
verify the `submitted_to_broker → cancellable` round-trip — i.e. that the
order reaches IBKR, that we observe the open-order callbacks, and that
`cancel` against a live (paper) broker order works — **not** to produce a
fill.

After placement, cancel the order:

```bash
uv run python scripts/ibkr_paper_order.py cancel \
  --account DUxxxxxxx \
  --intent-id <N> \
  --confirm-cancel
```

Transmitted paper orders will show up in 7a Flex snapshots on the next sync.

---

## 7. Error Diagnosis

| `error_type`                                            | Meaning / Action                                                                                                                                                                                  |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `OrderSafetyError`                                      | A CLI gate or service-layer safety check refused the operation (e.g. live account, missing `--confirm-transmit PAPER`, missing `--confirm-cancel`). Read the message — it names the failed gate.   |
| `OrderDuplicateError`                                   | The supplied idempotency key has already been used for an existing intent. Generate a new one — the simplest fix is to omit `--idempotency-key` so the CLI auto-generates a fresh UUID.            |
| `OrderAccountMismatchError`                             | The `--account` value is not in TWS' `managedAccounts` list. Confirm TWS is logged into the expected paper account and that you typed the `DU…` ID correctly.                                     |
| `OrderConnectionError`                                  | The CLI could not open a socket to TWS. TWS/Gateway is not running, the wrong host/port is configured, the API socket is disabled, or the client ID collides with another active client.           |
| `OrderCallbackTimeoutError` (`placeorder_called=False`) | `nextValidId` never arrived — we timed out before TWS sent the initial handshake. TWS may be frozen, mid-restart, or rejecting the client. Restart TWS and retry. No order was placed.              |
| `OrderCallbackTimeoutError` (`placeorder_called=True`)  | `placeOrder` was accepted by TWS, but no status callback arrived in time. **The order may have landed.** Open TWS UI and run `status --intent-id N`; do not blindly retry — you may double-submit. |
| `OrderBrokerCallError`                                  | TWS returned an error code (e.g. invalid contract, insufficient permissions, market closed for that product). The message includes the IBKR error code — look it up in the IBKR API error table.   |

When in doubt, inspect the event log for the intent (Section 8) — it
records every callback in order, with timestamps.

---

## 8. Inspecting Recent Intents and Events

Recent intents:

```sql
SELECT id, created_at, action, account_id, symbol, status, broker_order_id
FROM broker_order_intent
ORDER BY id DESC
LIMIT 10;
```

Events for a specific intent:

```sql
SELECT *
FROM broker_order_event
WHERE intent_id = <N>
ORDER BY observed_at;
```

These two tables are the source of truth for what 7b did and observed. They
are append-only from the operator's perspective — do not edit rows manually.

---

## 9. What Phase 7b Never Does

Out of scope for 7b — if you find yourself wanting any of these, it belongs
to a later phase or to Phase 6 paper-lifecycle work:

- No live-account execution (refused at CLI and service layers).
- No automatic strategy execution.
- No scheduler trigger — there is no cron, no APScheduler job, no background
  worker for 7b.
- No web UI button or HTMX route that places orders.
- No writes to `paper_*` tables — Phase 6 owns the paper-lifecycle state.
- No market orders. **LMT (limit) orders only.**
- No bracket, OCO, algo, or trailing-stop orders.
- No order modify/replace — cancel + re-place if you need to change a price.
- No automatic retry or reconnect on transient errors. Failures are surfaced
  to the operator; rerunning is a manual decision.

---

## 10. Phase 7c Pointer

Phase 7c will introduce broker-vs-paper **reconciliation**: comparing
Phase 7a Flex (truth from IBKR) against Phase 7b order intents and Phase 6
paper-lifecycle state to detect drift. Until 7c lands, **no reconciliation
is performed** — `broker_order_intent` rows and Phase 6 `paper_*` rows are
maintained independently, and operators should not assume they will line up
automatically.
