# Phase 7a-Flex — IBKR Read-Only Broker Sync via Flex Web Service

**Date:** 2026-05-24
**Status:** Design approved, ready to plan
**Supersedes:** the IB Gateway sidecar deployment path established in PR #93/#94 (the
ibapi-based `IbkrReadClient` and the `gnzsnz/ib-gateway` Docker service). Phase 7a's
DB schema, repository, DTOs, sync_run state machine, and CLI name are unchanged.

## Goal

Replace the IB Gateway sidecar transport with IBKR's official Flex Web Service for
read-only broker truth capture, so that Phase 7a can run on a NAS in mainland China
without a Java Gateway container, IBC dialog choreography, daily forced-logout
recovery, or VNC first-boot setup.

Phase 7a-Flex is still read-only and still writes the same `broker_*` snapshot tables.
Trading and quote streaming remain out of scope; both will be addressed in Phase 7b/7c
with whatever Gateway-based transport is most appropriate then.

## Why now

The IB Gateway sidecar path (gnzsnz/ib-gateway + IBC) failed to reach a usable
state during deployment on the user's Synology NAS. The IBC controller hung on an
unrecognized `** no title **` dialog that appears mid-login on this account, and
no combination of `EXISTING_SESSION_DETECTED_ACTION`, full IBKR-side session
expiration, clean Portal logout, or fresh container restarts cleared it. The
underlying problem is account- or IP-specific behavior in IBKR's login flow that
IBC's dialog dispatcher does not handle; debugging it further requires manual VNC
intervention every time the dialog appears, which is incompatible with unattended
operation.

Flex Web Service is the IBKR-official mechanism for unattended batch broker-truth
capture. It is HTTPS-only, uses a permanent token, never requires 2FA on the
request side, has no login session to maintain, and the configuration is a single
Query ID created once in the IBKR Portal. It is the correct tool for the Phase 7a
job; the Gateway transport was chosen originally because Phase 7+ will need a
write path, but write-path tool selection should happen at Phase 7b time, not now.

## Architecture

```
DELETED                                ADDED
─────────────────────────              ─────────────────────────
ibapi dependency                       — (none; httpx already in tree)
marketpulse/broker/ibkr_client.py      marketpulse/broker/flex_client.py
gnzsnz/ib-gateway sidecar              — (no sidecar service)
IBKR_HOST/PORT/CLIENT_ID env vars      IBKR_FLEX_TOKEN / IBKR_FLEX_QUERY_ID env vars
IB_GATEWAY_*, TWS_*, VNC_* env vars    — (gone)
IBKR_PASSWORD / IBKR_USERNAME env      — (gone; Flex token replaces account auth)
IBKR_READ_ONLY_API env                 — (gone; Flex is structurally read-only)

UNCHANGED
─────────────────────────
BrokerReadClient Protocol (one method: fetch_snapshot)
marketpulse/broker/repository.py
marketpulse/broker/readonly_sync.py orchestration shape
broker_* DB tables + 0012 Alembic migration
DTOs (BrokerAccount, BrokerCash, BrokerPosition, BrokerOpenOrder, BrokerExecution, BrokerSnapshot)
scripts/sync_ibkr_readonly.py CLI name + return-code semantics
Architecture guards (re-aimed at Flex mutating endpoints)
```

The `BrokerReadClient` Protocol exposes exactly `fetch_snapshot() -> BrokerSnapshot`.
The Flex implementation satisfies it; everything above the Protocol is reused.

### Sync flow (one CLI invocation = one Flex report)

```
sync_ibkr_readonly.py
    ↓
readonly_sync.run_sync(client, repo, settings)
    ↓
repository.create_started_run(...)          → broker_sync_run row, status=started
    ↓
client.fetch_snapshot()                      → FlexClient
    │
    ├─ POST FlexStatementService.SendRequest
    │    → ReferenceCode
    │
    ├─ poll GetStatement every 5s (max 60s)
    │    │
    │    ├─ "Statement generation in progress" → keep polling
    │    ├─ ErrorCode XML response             → FlexStatementError
    │    └─ Activity XML                       → parse
    │
    └─ XML → BrokerSnapshot
    ↓
repository.persist_snapshot_rows(...)       → broker_account_snapshot / cash / position / execution rows
    ↓
repository.mark_run_completed(...)          → broker_sync_run status=completed
```

Failure at any step: `mark_run_failed(error_type, error_message, context)`. No
snapshot rows are written on failure. The Flex `reference_code` is recorded in
`broker_sync_run.context` whenever `SendRequest` succeeds, so operators can manually
re-fetch the same generation via `GetStatement` for forensics.

### Open orders are not produced

Flex Activity reports do not include working/open orders — only filled executions
historically. Phase 7a's `broker_open_orders_snapshot` table will therefore receive
zero rows under the Flex transport. This is acceptable for the read-only snapshot
use case (open-order state is volatile and only useful for live reconciliation,
which is a Phase 7b concern). The empty list is documented; the table is kept so
that the schema is forward-compatible with the eventual Phase 7b/7c write-side
transport.

### Transport context in SyncResult

`SyncResult` is refactored to drop Gateway-specific transport fields
(`host`, `port`, `client_id`) and adopt a transport-discriminated shape:

```python
@dataclass(frozen=True)
class SyncResult:
    sync_run_id: int
    broker: BrokerName
    broker_environment: BrokerEnvironment
    account_id: str | None
    status: SyncStatus
    transport: Literal["flex"]              # was: host: str
    endpoint: str                            # was: port: int — full URL like "https://gdcdyn.../FlexStatementService"
    query_id: str | None                     # was: client_id: int — Flex Activity Flex Query ID
    reference_code: str | None = None       # NEW: set when SendRequest succeeds, preserved on subsequent failure
    account_snapshots: int = 0
    cash_rows: int = 0
    positions: int = 0
    open_orders: int = 0                    # always 0 for Flex (L18); annotated in CLI output
    executions: int = 0
    error_type: str | None = None
    error_message: str | None = None
```

Rationale: repurposing `host`/`port`/`client_id` to mean "Flex endpoint"/"443"/
"query_id" was semantically misleading and would have leaked into the CLI summary
output, the runbook, and any future consumer. Since the Gateway transport is
removed in the same PR, there is no backward-compatibility cost to refactoring
now — and there is a permanent cost (CLI/runbook ambiguity) to leaving the
field names misaligned.

The only consumers of these fields today are `scripts/sync_ibkr_readonly.py`'s
CLI printer and a small number of broker tests. Both update in this PR.

### Environment classification

`classify_broker_environment(port: int)` is Gateway-specific (7497 → paper, 7496 →
live). Flex reports tag accounts directly with an `accountType` field of `INDIVIDUAL`,
`DEMO`, `JOINT`, etc., and the account ID prefix is the truth (DU → paper, U → live).
A new helper `classify_broker_environment_from_account_id(account_id: str)` will live
beside the port-based one (port-based is removed at the same time, since no
production caller will use it after 7a-Flex). Behavior:

| Account ID pattern | Environment |
|---|---|
| `DU` followed by digits | `paper` |
| `U` followed by digits (no other letters) | `live` |
| anything else | `unknown` |

`unknown` is treated identically to `live` by the safety brake — the brake
fires unless `mp_ibkr_allow_live` is True and the environment is exactly
`paper`. This is intentional: an ambiguous account is a refusal trigger, not
a fall-through to "best guess".

### Live-account safety brake

The Gateway transport had `MP_IBKR_ALLOW_LIVE` as a structural safety brake (the
adapter refused to connect to live ports unless this was explicitly set). Flex has
no port distinction, so the brake is re-implemented at the sync orchestration
level: after fetching the snapshot, if `broker_environment != "paper"` (i.e.
`live` or `unknown`) and `settings.mp_ibkr_allow_live` is False, the sync fails
closed with `LiveAccountRefusedError`, writes no snapshot rows, and marks the run
failed. This preserves the "you must consciously enable live capture" property
without depending on port choice, and treats ambiguous classification as a
refusal trigger rather than a soft warning.

## Settings (Pydantic Settings additions)

```
ibkr_flex_token: SecretStr                         # required to enable Flex sync
ibkr_flex_query_id: int | None = None              # required to enable Flex sync
ibkr_flex_poll_interval_seconds: int = 5
ibkr_flex_max_wait_seconds: int = 60
ibkr_flex_base_url: str = "https://gdcdyn.interactivebrokers.com/Universal/servlet"
ibkr_account_id: str | None = None                 # unchanged from 7a
mp_ibkr_allow_live: bool = False                   # unchanged from 7a, now structural brake
```

Removed (all in the same change): `ibkr_host`, `ibkr_port`, `ibkr_client_id`,
`ibkr_connect_timeout_seconds`, plus all the IB Gateway sidecar env vars
(`IBKR_USERNAME`, `IBKR_PASSWORD`, `IBKR_TRADING_MODE`, `IBKR_READ_ONLY_API`,
`IB_GATEWAY_VNC_BIND`, `VNC_SERVER_PASSWORD`, `EXISTING_SESSION_DETECTED_ACTION`,
`IB_GATEWAY_IMAGE`).

`ibkr_flex_base_url` is configurable so an operator can override during testing
(e.g. against a recorded fixture proxy) without code changes, but defaults to the
canonical IBKR endpoint.

## Error taxonomy

```
FlexHttpError              transport-layer failure: DNS, TLS handshake, connection
                           refused, read/connect timeout, 5xx status code. Raised
                           by both SendRequest and GetStatement before any XML is
                           even seen.
FlexAuthError              token rejected, or query owned by another user
                           (IBKR returns this as an XML ErrorCode, not 401)
FlexSendRequestError       SendRequest returned a valid HTTP 200 but the XML
                           body contained an error code (token/query mismatch
                           that is not auth, malformed query, etc.)
FlexReportTimeoutError     poll exhausted ibkr_flex_max_wait_seconds
                           (IBKR kept returning "generation in progress")
FlexStatementError         GetStatement returned valid HTTP 200 but XML body had
                           an error code after the report should have been ready
                           (e.g. report expired, internal IBKR error)
FlexParseError             XML structurally invalid OR required Account section
                           missing OR account_id missing from Account section.
                           Optional sections (Cash, Positions, Trades) being
                           absent does NOT raise this.
FlexAccountMismatchError   returned report's accountId != settings.ibkr_account_id
                           (only checked when settings.ibkr_account_id is set)
LiveAccountRefusedError    report's broker_environment is not "paper" but
                           settings.mp_ibkr_allow_live is False
```

Every error type is a subclass of `FlexError(Exception)`. The `error_type` column
in `broker_sync_run` records the class name. `error_message` records a one-line
human summary suitable for the runbook diagnosis table. `broker_sync_run.context`
stores the `reference_code` (when known), the HTTP status code (for
`FlexHttpError`), and the IBKR ErrorCode (for `FlexSendRequestError` /
`FlexStatementError`) so the operator can correlate against IBKR's docs.

## Lock points

These are explicit design constraints frozen during brainstorming. Plan tasks
that violate any of them must escalate, not silently relax.

| ID | Lock |
|---|---|
| L1 | 7a MVP uses exactly one Activity Flex Query ID. |
| L2 | One CLI invocation fetches one Flex report and maps its sections into broker_* snapshot rows. |
| L3 | No multi-query orchestration in MVP. |
| L4 | Trade Confirmation Flex Query is deferred unless Activity Flex executions are insufficient. |
| L5 | Canonical CLI remains `scripts/sync_ibkr_readonly.py`. |
| L6 | CLI implementation is Flex-backed in 7a-Flex. The CLI name describes broker-truth sync semantics, not transport. |
| L7 | No parallel production CLI for ibapi/Gateway in 7a-Flex. Old ibapi adapter path is removed. |
| L8 | Flex report generation uses bounded polling, never infinite polling. |
| L9 | Default polling interval = 5 seconds; default max wait = 60 seconds. |
| L10 | Poll timeout is a failed sync_run with `error_type="FlexReportTimeoutError"`; no snapshot rows are written. |
| L11 | SendRequest success but GetStatement failure still records `reference_code` in `broker_sync_run.context`. |
| L12 | Phase 7a-Flex branches from clean main after PR #73 is merged or explicitly deferred. |
| L13 | 7a-Flex does not share a branch with Phase 5c sector/correlation caps. |
| L14 | If #73 cannot merge tonight, park it unchanged and branch 7a-Flex from current main, not from the 5c branch. |
| L15 | `ibapi` is removed from the dependency graph in this PR (no dead code path retained). |
| L16 | The Gateway sidecar service is removed from both `docker-compose.cn.yml` and `docker-compose.prod.yml` in this PR. |
| L17 | DB schema is unchanged. No new Alembic migration. Flex-specific metadata lives in `broker_sync_run.context` JSON. |
| L18 | `broker_open_orders_snapshot` receives zero rows under Flex transport. CLI output annotates this explicitly: `open orders: 0 (not available via Flex Activity)`. |
| L19 | HTTP transport is `httpx` (sync client) with explicit `httpx.Timeout(connect=5, read=30, write=10, pool=5)`. No bare `requests`, no implicit infinite timeout. |
| L20 | `SyncResult` is refactored to `transport: Literal["flex"]` / `endpoint: str` / `query_id: str | None`. Gateway-shaped `host`/`port`/`client_id` fields are removed in this PR. |
| L21 | XML parser: Account section is required (missing → `FlexParseError`); Cash, Positions, Trades sections are individually optional (missing → empty tuple, recorded as a `context` warning string). |
| L22 | CLI prints `reference_code` on every run (success or failure) when `SendRequest` succeeded, so operators can manually re-fetch via `GetStatement` for forensics. |
| L23 | Sidecar cleanup is comprehensive: removes the `ib-gateway` service block, plus any `volumes:` declarations exclusive to it, plus all gateway-related env on the `marketpulse` service, plus any `depends_on` / `networks` aliases pointing at it, plus all gateway/VNC mentions in `DEPLOY.md` and the runbook. |

## Files affected

| File | Action |
|---|---|
| `marketpulse/broker/ibkr_client.py` | **delete** (entire ibapi adapter) |
| `marketpulse/broker/flex_client.py` | **create** (HTTP client + XML parser + adapter) |
| `marketpulse/broker/readonly_sync.py` | **modify** (drop `IbkrSyncConfig` host/port/client_id; add `FlexSyncConfig`; live-account brake at orchestration level; environment classification by account ID) |
| `marketpulse/broker/types.py` | **modify** (drop `classify_broker_environment(port)`; add `classify_broker_environment_from_account_id`; document `SyncResult.host/port/client_id` convention for Flex; do not change field shape) |
| `marketpulse/broker/repository.py` | **unchanged** |
| `marketpulse/broker/__init__.py` | **modify** (re-exports updated) |
| `marketpulse/settings.py` | **modify** (add Flex settings, remove Gateway settings) |
| `scripts/sync_ibkr_readonly.py` | **modify** (rewire DI: build `FlexClient` instead of `IbkrReadClient`; CLI flags `--max-wait-seconds`, `--poll-interval-seconds`, `--query-id`, `--account-id` for override; help text updated to Flex semantics; same exit codes) |
| `.env.example` | **modify** (replace entire Phase 7a section with Flex env documentation) |
| `docker-compose.cn.yml` | **modify** (L23 sidecar cleanup: remove `ib-gateway` service block; remove its `volumes:` declarations if any; remove all `IBKR_HOST/PORT/CLIENT_ID/USERNAME/PASSWORD/TRADING_MODE/READ_ONLY_API/CONNECT_TIMEOUT_SECONDS/MP_IBKR_ALLOW_LIVE` from `marketpulse.environment` except `MP_IBKR_ALLOW_LIVE` which moves with new docstring; remove any `depends_on: [ib-gateway]`; remove `ib-gateway` from the `marketpulse.NO_PROXY` default list; add `IBKR_FLEX_TOKEN`, `IBKR_FLEX_QUERY_ID`, `IBKR_ACCOUNT_ID`, `IBKR_FLEX_POLL_INTERVAL_SECONDS`, `IBKR_FLEX_MAX_WAIT_SECONDS`) |
| `docker-compose.prod.yml` | **modify** (same as cn.yml, plus check for and remove the `IB_GATEWAY_VNC_BIND` port binding if present) |
| `DEPLOY.md` | **modify if it references ib-gateway / TWS / VNC / paper credentials** (audit and prune any gateway-era mentions; replace with Flex setup pointer to the runbook) |
| `pyproject.toml` | **modify** (drop `ibapi` dependency; lockfile regenerated) |
| `tests/broker/test_ibkr_client_mapping.py` | **delete** (520 lines of ibapi adapter tests) |
| `tests/broker/test_flex_client.py` | **create** (table-driven XML→DTO mapping; HTTP error paths; polling timeout; auth error; account mismatch; live-account refusal; reference-code preserved on poll failure) |
| `tests/broker/test_readonly_sync.py` | **modify** (re-aim at `FlexSyncConfig`; preserve all existing sync_run state-machine assertions) |
| `tests/broker/test_repository.py` | **unchanged** (repository is unchanged) |
| `tests/broker/test_sync_cli.py` | **modify** (replace ibapi-side flags with Flex flags; same exit-code contract) |
| `tests/broker/test_types_and_contract.py` | **modify** (`classify_broker_environment` swap from port-based to account-id-based; SyncResult shape unchanged) |
| `tests/architecture/test_phase7a_ibkr_readonly_boundary.py` | **modify** (the "only `ibkr_client.py` may import `ibapi`" guard becomes "no production module imports `ibapi`" — by removal of `ibapi` from the dep graph altogether. The test file is renamed in spirit (boundary moves from "single-module-allow-list" to "deny-all"). Keep the file as `test_phase7a_ibkr_readonly_boundary.py` so historical context is preserved; expand its docstring to explain Phase 7a-Flex changed the boundary from allow-list to deny-list.) |
| `docs/operations/ibkr-readonly-sync-runbook.md` | **full rewrite** (Flex setup walkthrough: create Activity Flex Query in Portal, fetch token, fill env, run CLI, verify rows; error-code → diagnosis table per error taxonomy; remove all Gateway/VNC content) |
| `docs/superpowers/specs/2026-05-23-phase-7a-ibkr-readonly-sync-design.md` | **add header note** pointing forward to this spec (do not delete; historical record) |
| `docs/superpowers/specs/2026-05-24-phase-7a-flex-readonly-sync-design.md` | **this file** |

## Test strategy

The existing 7a test suite already exercises the read-only sync state machine,
DTO mapping, repository writes, and architecture guards. Phase 7a-Flex re-aims
these and adds Flex-specific transport tests; it does not weaken anything that
exists today.

| Layer | New tests |
|---|---|
| `flex_client` unit (parser) | XML→DTO mapping table-driven across fixture matrix: full report; missing Cash (→ empty cash); missing Positions (→ empty positions); missing Trades (→ empty executions); missing Account (→ `FlexParseError`); Account present but no account_id (→ `FlexParseError`); multi-currency cash; multi-account report (→ `FlexAccountMismatchError` if filtered) |
| `flex_client` HTTP transport | SendRequest happy path; SendRequest XML error (`FlexSendRequestError`); SendRequest 5xx (`FlexHttpError`); SendRequest connect timeout (`FlexHttpError`); SendRequest TLS error (`FlexHttpError`); GetStatement "generation in progress" then ready; GetStatement XML error (`FlexStatementError`); GetStatement 5xx (`FlexHttpError`); GetStatement auth XML error (`FlexAuthError`) |
| `flex_client` polling | timeout triggers `FlexReportTimeoutError` with reference_code preserved; first-poll-already-ready short-circuits without sleeping; `ibkr_flex_poll_interval_seconds=0` boundary (still safe, no busy loop because each poll has a network round-trip) |
| `readonly_sync` (Flex) | full happy path; account mismatch raises and fails the run; live-account brake fires for `live` env; live-account brake also fires for `unknown` env (L21 conservative classification); SendRequest success + GetStatement failure preserves reference_code in `broker_sync_run.context`; environment classification by account_id |
| `architecture_guards` | `ibapi` not importable from any production module (since dep is removed, this becomes a static-analysis grep on `import ibapi`); no `gnzsnz/ib-gateway` references in compose files; no `IBKR_HOST` / `IBKR_PORT` / `IBKR_CLIENT_ID` / `TWS_*` env names referenced anywhere except docs that explain the removal |

XML fixtures live under `tests/broker/fixtures/flex/`. The matrix:

| Fixture | Purpose |
|---|---|
| `full_paper.xml` | DU account, 3 positions, 2 cash currencies (USD + HKD), 2 executions |
| `full_live.xml` | U account variant for live-brake test |
| `account_only.xml` | Account section present, no Cash/Positions/Trades — tests empty-tuple semantics |
| `missing_account.xml` | No `<AccountInformation>` — raises `FlexParseError` |
| `missing_account_id.xml` | `<AccountInformation>` present but `accountId` empty — raises `FlexParseError` |
| `multi_currency.xml` | DU account, 4 cash currencies including JPY + CAD for currency-code coverage |
| `multi_account.xml` | Two accountIds in one report — verifies mismatch logic when filtering |
| `err_auth.xml` | IBKR ErrorCode for invalid token — raises `FlexAuthError` |
| `err_generation_in_progress.xml` | Used in polling tests to drive the "keep polling" branch |
| `err_report_expired.xml` | GetStatement after long delay — raises `FlexStatementError` |
| `malformed.xml` | Not valid XML — raises `FlexParseError` |

No live IBKR call is required for unit tests.

A manual end-to-end smoke (operator runs the CLI against their actual IBKR
account) is documented in the runbook and is the deployment acceptance test.

## Out of scope (explicitly deferred)

* Multi-query orchestration (L3, L4).
* Trade Confirmation Flex Query (L4).
* Reconciliation between `broker_*` and `paper_*` tables.
* Open-orders capture (L18).
* Real-time / streaming broker truth.
* Any write-side broker operations (placeOrder/cancel/modify) — Phase 7b.
* `SyncResult` dataclass refactor to a transport-agnostic shape — Phase 7c+.
* Web UI for browsing `broker_*` rows.
* Notification on sync failure.
* Scheduling/cron of the CLI (operator runs it manually or wires their own cron;
  no in-app scheduler).

## Risks and open questions

* **Account-ID-based environment classification correctness.** The `DU` prefix
  is documented by IBKR for paper accounts and is used as a heuristic across the
  ecosystem (ibapi tests, ib_insync, etc.). The risk is a future IBKR change of
  the prefix scheme. Mitigation: classification is in one helper, easy to
  expand; the `LiveAccountRefusedError` brake is the structural defense, not the
  classifier itself.
* ~~`SyncResult.host/port/client_id` repurposing as Flex coordinates.~~
  Resolved during spec review: L20 refactors `SyncResult` to a Flex-native
  shape in this PR.
* **Flex Query content drift.** The Activity Flex Query is configured in the
  IBKR Portal by the operator, not in code. The parser is layered per L21:
  Account Information is **required** (missing → `FlexParseError`, because
  without it we cannot apply the live-account brake or check account_id
  mismatch); Cash, Positions, and Trades are each **optional** (missing →
  empty tuple, with a context warning so the operator notices). NAV is not
  used by Phase 7a (we use Account net_liquidation instead). The runbook
  spells out the minimum tick-box set for our use case.
* **Token rotation.** Flex tokens do not expire on a fixed schedule but the
  user may rotate them. Mitigation: clear `FlexAuthError` message tells the
  operator to regenerate token in Portal and update env.
* **Network reachability from China.** Tested earlier in this session against
  cdc1.ibllc.com (IBKR backend); reachable. Flex Web Service uses the same
  IBKR domain set and is expected to be similarly reachable. Confirmed end-to-end
  during deployment acceptance.

## Execution handoff

Plan to be written next at
`docs/superpowers/plans/2026-05-24-phase-7a-flex-readonly-sync.md`, then executed
via `superpowers:subagent-driven-development`.
