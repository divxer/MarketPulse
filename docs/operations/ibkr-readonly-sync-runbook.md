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
