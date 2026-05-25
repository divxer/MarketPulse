# IBKR Read-Only Sync Runbook (Phase 7a-Flex)

Phase 7a-Flex captures IBKR broker truth via the official Flex Web Service
into the append-only `broker_*` snapshot tables. No daemon, no Gateway
container, no VNC, no 2FA at request time.

## Preconditions

- IBKR account with paper trading enabled (DU<optional letters><digits>, e.g. `DU1234567` or `DUE411848`) or live account if `MP_IBKR_ALLOW_LIVE=true`.
- Activity Flex Query created in IBKR Portal (one-time setup, below).
- Flex Token issued and recorded (one-time setup, below).
- Outbound HTTPS to `gdcdyn.interactivebrokers.com` reachable from MarketPulse runtime. When deployed behind a forwarding HTTP proxy (e.g. Clash/Mihomo on a NAS), IBKR Flex hosts must be in `NO_PROXY` so requests go direct — the compose defaults (`docker-compose.cn.yml`, `docker-compose.prod.yml`) already include `.interactivebrokers.com` plus explicit `gdcdyn`/`ndcdyn` hostnames (httpx does not recognize `*.domain.com` wildcards, so both forms are listed).

## One-time setup: IBKR Portal

### 1. Create the Activity Flex Query

1. Log in to <https://www.interactivebrokers.com> → **Reports** → **Flex Queries**.
2. **Activity Flex Query** → "Create" (or pencil-edit an existing one).
3. Name it `MarketPulse_7a_ReadOnly_Snapshot`.
4. Period: `Last Business Day` (or `Today`).
5. Format: `XML`, Date format `yyyy-MM-dd`, Time format `HH:mm:ss`.
6. **Sections** — tick exactly these (others are optional, see "Section drift"):
   - **Account Information** (REQUIRED)
   - **Cash Report** → tick "All currencies"
   - **Open Positions**
   - **Trades** → tick at least "Executions"
7. Save. Note the **Query ID** (a 6-digit integer).

### 2. Issue a Flex Token

1. Reports → Flex Queries → top right gear → **Token Renewal** (or "Get Current Token").
2. Generate token. **Save it in a password manager** — IBKR does not let you re-display existing tokens.
3. Tokens do not expire on a fixed schedule but can be revoked manually.

### 3. Populate `.env` / Portainer env

```env
IBKR_FLEX_TOKEN=<64-char token>
IBKR_FLEX_QUERY_ID=<6-digit query id>
IBKR_ACCOUNT_ID=DUxxxxxxx
MP_IBKR_ALLOW_LIVE=false
```

If using Portainer, **escape `$` as `$$`** in any field — docker-compose
variable substitution will silently truncate otherwise. (Tokens are
hexadecimal so usually unaffected; this is a generic warning.)

## Security considerations

**Token-in-URL caveat:** IBKR's Flex Web Service accepts the token as a URL
query parameter (`?t=<token>`). Any HTTP proxy or access log along the
request path will record it. Avoid running the sync behind a logging HTTP
proxy, or ensure such access logs are scrubbed / not retained. Keep the
token in a password manager and rotate it via IBKR Portal if you suspect
exposure.

## Automatic schedule

The Flex sync runs automatically inside the MarketPulse scheduler at
**23:30 America/New_York, Mon-Fri** (overridable via `FLEX_SYNC_HOUR` /
`FLEX_SYNC_MINUTE`). Operators no longer need to `docker exec` daily — the
manual command below is only for ad-hoc testing, troubleshooting, or
backfill after a failure window. The scheduled job silently skips itself
when `IBKR_FLEX_TOKEN` or `IBKR_FLEX_QUERY_ID` are unset.

## Manual smoke

```bash
uv run python scripts/sync_ibkr_readonly.py
```

Successful output:

```text
sync_run_id: 1
broker: IBKR
broker_environment: paper
account: DU1234567
transport: flex
endpoint: https://gdcdyn.interactivebrokers.com/Universal/servlet
query_id: 123456
reference_code: 1234567890
status: completed
account snapshots: 1
cash rows: 2
positions: 5
open orders: 0 (not available via Flex Activity)
executions: 3
```

Failed output:

```text
sync_run_id: 2
broker: IBKR
broker_environment: paper
account: unknown
transport: flex
endpoint: https://gdcdyn.interactivebrokers.com/Universal/servlet
query_id: 123456
reference_code: 9876543210
status: failed
error_type: FlexReportTimeoutError
error_message: Flex report not ready after 60s
```

Note `reference_code` is printed on every run where `SendRequest` succeeded
— even on failure. To manually re-fetch:

```bash
curl "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement?t=<TOKEN>&q=<REFERENCE_CODE>&v=3"
```

## Error diagnosis

| error_type | Meaning | Operator action |
|---|---|---|
| `FlexHttpError` | DNS / TLS / 5xx / timeout at transport layer | Check network reachability; retry; check IBKR status page |
| `FlexAuthError` | Token rejected (code 1003/1011/1012) | Re-issue token in Portal; update env |
| `FlexSendRequestError` | SendRequest XML had non-auth error | Check Query ID; check token has access to that query |
| `FlexReportTimeoutError` | Polling exhausted `IBKR_FLEX_MAX_WAIT_SECONDS` | Raise `IBKR_FLEX_MAX_WAIT_SECONDS` or wait and re-run with reference code |
| `FlexStatementError` | GetStatement returned error after ready (e.g. expired reference) | Re-run from scratch |
| `FlexParseError` | XML malformed or Account section missing | Check Query in Portal has Account Information ticked |
| `FlexAccountMismatchError` | Report contains different account than `IBKR_ACCOUNT_ID` | Either unset `IBKR_ACCOUNT_ID` or fix it |
| `LiveAccountRefusedError` | Account is not classified `paper` | Set `MP_IBKR_ALLOW_LIVE=true` if intentional |
| `AccountMismatchError` | Configured `IBKR_ACCOUNT_ID` ≠ snapshot's account | Same as `FlexAccountMismatchError` |

## Inspecting recent runs

```bash
sqlite3 data/marketpulse.db <<'SQL'
SELECT id, started_at, completed_at, broker_environment, account_id,
       status, error_type, json_extract(context, '$.reference_code') AS ref
FROM broker_sync_run
ORDER BY id DESC
LIMIT 5;
SQL
```

## Section drift

The Flex Query is configured in IBKR Portal, not in code. If you uncheck a
section:

- **Account Information**: missing → `FlexParseError`. Fix by re-ticking.
- **Cash Report / Open Positions / Trades**: missing → 0 rows recorded with no error. Intentional.

The parser will not hard-fail on missing optional sections, so you can
narrow the Query to just Account + Positions for example without breaking
the sync.

## What 7a-Flex never does

- No order placement / modification / cancellation.
- No realtime quote streaming.
- No scheduler or daemon — operator runs the CLI manually or via cron.
- No web-triggered sync.
- No writes to `paper_*` tables.
- No paper-vs-broker reconciliation.
- No open-order capture (Flex Activity reports do not include working orders).

## Phase 7b/7c

The Gateway-based write path (order placement, real-time book) is Phase 7b
and uses a separately-chosen transport (likely ibeam + Client Portal Web API
or IBKR's TWS API via a re-introduced sidecar). Phase 7a-Flex does not
constrain that choice.
