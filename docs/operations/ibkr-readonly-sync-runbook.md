# IBKR Read-Only Sync Runbook

Phase 7a captures IBKR broker truth into append-only `broker_*` snapshot tables.
It does not place, modify, cancel, reconcile, or drive paper trading state.

## Preconditions

- IBKR TWS or IB Gateway is running.
- Paper trading API access is enabled.
- Socket API port is reachable from the MarketPulse runtime.
- Default Gateway paper port is `4002` (TWS paper is `7497`).
- Known live ports `4001` (Gateway) / `7496` (TWS) are blocked unless `MP_IBKR_ALLOW_LIVE=true`.

## Recommended deployment: IB Gateway in Docker (production)

The production stack on the NAS runs **IB Gateway** (not TWS) as a
sidecar container alongside `marketpulse`. Gateway is the headless,
lightweight flavor designed for 7×24 server-side automation. The
compose files (`docker-compose.cn.yml`, `docker-compose.prod.yml`)
define both services on a shared docker network; `marketpulse` talks
to `ib-gateway:4002` via docker DNS — no host port bind needed for
the API socket.

### 1. Pre-flight: get an IBKR paper account

- Sign up at <https://www.interactivebrokers.com> → "Open Account" → paper trading.
- Paper accounts are **free** and require no deposit.
- Your paper account ID starts with `DU` (e.g. `DU1234567`).
- Paper has a **separate password** from your live account; IBKR forces you to set it on first paper login.

### 2. Populate `.env` on the NAS

The Portainer stack reads variables from a `.env` file (or its
"Environment variables" panel). The minimum required for `ib-gateway`
to boot:

```env
IBKR_USERNAME=<paper account username>
IBKR_PASSWORD=<paper account password>
IBKR_ACCOUNT_ID=DUxxxxxxx
IBKR_TRADING_MODE=paper
IBKR_READ_ONLY_API=yes
IB_GATEWAY_VNC_BIND=192.168.50.29:5900
VNC_SERVER_PASSWORD=<6–8 char VNC password — NOT your IBKR password>
```

Full env reference is in `.env.example`. Never commit `.env` to git.

### 3. Deploy the updated stack via Portainer

1. Portainer → Stacks → marketpulse → Update.
2. Confirm the env panel has all the new IBKR_* and VNC_* keys.
3. Pull and recreate. First boot of `ib-gateway` takes ~30–60s (login + cold cache).
4. `docker ps` should show both `marketpulse` and `ib-gateway` as `Up` and `healthy`.

### 4. First-boot GUI verification (one-time, via VNC)

IBC auto-logs in and toggles most settings, but verify Read-Only API
and Trusted IPs the first time. From your Mac:

```bash
open vnc://192.168.50.29:5900
```

Enter the `VNC_SERVER_PASSWORD` from `.env`. Then in the IB Gateway window:

1. **Configure → Settings → API → Settings**
   - ✅ Enable ActiveX and Socket Clients
   - Socket port = `4002` (paper)
   - Master API client ID: leave blank
2. **Configure → Settings → API → Precautions**
   - ✅ **Read-Only API** (defense in depth on top of our adapter's static guard)
3. **Configure → Settings → API → Trusted IPs**
   - Leave empty — only the docker network can reach the socket
4. Click **OK**. Config persists across restarts because `/home/ibgateway` is a volume.

Disconnect VNC. You normally never need it again.

### 5. Verify end-to-end

```bash
sudo docker exec marketpulse uv run python scripts/sync_ibkr_readonly.py
```

Expected on success: `status: completed` + row counts. Then check the snapshot tables:

```bash
sudo docker exec marketpulse uv run python -c "
import sqlite3
con = sqlite3.connect('/data/marketpulse.db')
for tbl in ('broker_sync_run', 'broker_account_snapshot',
            'broker_position_snapshot', 'broker_cash_snapshot'):
    n = con.execute(f'SELECT COUNT(*) FROM {tbl}').fetchone()[0]
    print(f'{tbl}: {n}')"
```

Each non-zero count confirms the pipeline is working.

### Read-Only API enforcement (TWS-side, required)

The MarketPulse Phase 7a adapter uses Interactive Brokers' official
`ibapi` Python SDK. Unlike the older community `ib_insync` library,
`ibapi` has **no client-side `readonly=True` flag** on `connect()`.
Read-only enforcement therefore lives in TWS / IB Gateway itself:

1. Open **TWS → File → Global Configuration → API → Precautions**.
2. Tick **"Read-Only API"**.
3. Click **OK / Apply**.

With that checkbox enabled, TWS refuses any order-placement, cancel,
or modify request that arrives on the API socket — even if a buggy or
malicious client were to send one. The MarketPulse adapter also never
calls any mutating `ibapi` method (architecture guard test
`test_no_ibkr_mutating_api_names_in_production_or_scripts` enforces
this in CI), giving us defense in depth: TWS-side hard stop **plus**
codebase-side absence.

Operators must verify the checkbox is set each time TWS is reinstalled
or its config is reset, as it does not persist across fresh installs.

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
