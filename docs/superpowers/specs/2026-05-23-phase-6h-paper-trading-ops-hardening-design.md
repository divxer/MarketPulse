# Phase 6h — Paper Trading Ops Hardening Design

**Status:** Draft locked for implementation planning  
**Author:** Codex + Harvey, 2026-05-23  
**Spec-type:** sub-project under Phase 6 umbrella  
**Depends on:** Phase 6a, 6b, 6b+, 6f, 6g  
**Scope:** Paper-trading operations hardening only. No trading behavior changes.

---

## 1 — Goal & Boundary

Phase 6a-6g turned paper trading into a working multi-subsystem chain:

```
scheduler → daily_cycle → ForwardExecutionEngine → paper_* tables
          → paper_audit_event → notifications → /lab/paper-trading
```

Phase 6h closes the operational gap between "features implemented" and
"operator can trust the system in production."

**Success standard:**

> Operator can deploy, smoke-test, diagnose, and accept/reject a paper tick
> without reading source code.

### In Scope

- Deployment smoke script for the paper-trading operations surface.
- Deterministic paper-trading health check command.
- Notification smoke command.
- Price provider smoke check.
- DB/audit health checks for latest tick, scheduler gaps, kill switch,
  `PRICE_UNAVAILABLE`, open positions, and cash ledger.
- Operator-first runbook.
- Real tick acceptance checklist.
- Rollback / mitigation notes.

### Explicit Non-Goals

- No new trading logic.
- No new UI feature.
- No risk gate changes.
- No broker / IBKR readiness work.
- No automatic remediation.
- No daemon, watchdog, cron, or background monitor.
- No writes to paper trading state.
- No requirement to wait for real tick data before starting implementation.

---

## 2 — Hard Principles

### 2.1 Observational, Never Mutational

6h tools are read-only. They may query HTTP routes, inspect database rows, validate
configuration, and perform provider/notifier dry-run checks. They must not:

- create fake orders, fills, positions, cash ledger rows, or audit rows;
- flip kill switch state;
- retry or reprocess ticks;
- close or repair positions;
- mutate scheduler state;
- resend production notifications as a side effect of diagnosis.

Notification smoke is allowed only when explicitly operator-triggered and clearly
marked as a smoke message. It must not pretend a real fill/tick occurred and must
not write paper state.

### 2.2 Snapshot Checks, Not Continuous Monitoring

6h health checks are deterministic snapshots. An operator runs a command and gets
a point-in-time result with exit code and human-readable findings.

6h does not introduce a long-running process, polling loop, watchdog, or cron job.
Future automation may invoke these commands, but the commands themselves remain
single-shot.

### 2.3 Operator-First Documentation

Runbooks must start from operational symptoms, not source-code internals:

1. What the operator sees.
2. How to classify it: Healthy / Attention / Degraded / Failed.
3. What safe action to take.
4. Which evidence to capture.
5. Optional code entry points for developers.

The primary path must not require reading source code.

### 2.4 Release Gate Artifact

The acceptance checklist is a deploy-time release gate. It is required before
enabling unattended daily paper ticks after a new deployment.

The checklist can include "pending real tick acceptance" items, but it must clearly
distinguish:

- checks that can run immediately after deploy;
- checks that require the next real paper tick.

### 2.5 No Automatic Remediation

6h observes, diagnoses, and instructs the operator. It never self-heals.

Specifically, 6h does not:

- automatically retry failed ticks;
- automatically clear stuck state;
- automatically close positions;
- automatically resend notifications;
- automatically repair scheduler gaps;
- automatically toggle kill switch state.

---

## 3 — Artifacts

### 3.1 `scripts/smoke_paper_trading_ops.py`

Deployment smoke script for the UI and HTTP contract.

Checks:

- `/lab/paper-trading` requires auth when unauthenticated.
- `POST /lab/paper-trading` returns 405.
- Authenticated page render returns 200.
- Page includes key operator markers:
  - `Paper Trading · Operations`
  - `System Status`
  - `Generated at`
  - `Critical Events`
  - `Positions`
  - `Orders & Fills`
  - `Audit Timeline`
- Page does not expose control-plane actions:
  - `Force Close`
  - `Replay`
  - `Retry`
  - `Kill Switch Toggle`
  - `type="submit"`

Inputs:

- `--base-url` default: `http://127.0.0.1:8000`
- `--password` or `MARKETPULSE_SMOKE_PASSWORD`
- optional `--timeout-seconds`

Authentication flow:

1. `GET /lab/paper-trading` unauthenticated and verify redirect to `/login`.
2. `POST /login` with the supplied password.
3. Preserve the returned session cookie.
4. `GET /lab/paper-trading` with the session cookie.

The script must not bypass the app's normal login/session flow.

Exit codes:

- `0`: all checks pass.
- `1`: one or more checks fail.
- `2`: invalid CLI usage or missing required credentials.

### 3.2 `scripts/check_paper_trading_health.py`

Read-only DB health snapshot for the paper engine.

Checks:

- latest operational boundary event:
  - `TICK_COMPLETED`
  - `KILL_SWITCH_CYCLE_SKIPPED`
  - `TICK_REPROCESSED_COMPLETED`
- current operational window label / start time.
- latest tick status.
- scheduler gap events in the current operational window.
- kill switch state from audit/config-readable source.
- current cash balance from latest `paper_cash_ledger.balance_after`.
- open positions count.
- stuck `PRICE_UNAVAILABLE` positions:
  - attempt 1
  - attempt 2
  - attempt 3+
- unresolved critical/warning audit rows in the current operational window.
- section-level query failures should be reported as degraded, not hidden.

Implementation rule:

- The health command must prefer `load_paper_trading_dashboard(...)` or same-layer
  query helpers for COW, stuck `PRICE_UNAVAILABLE`, kill switch, section health,
  and status semantics. It must not reimplement those semantics inside the CLI.

Inputs:

- optional positional `DB_URL`
- default DB URL resolution follows existing scripts:
  `MARKETPULSE_DB_URL` → `sqlite:///./data/marketpulse.db`
- optional `--json`

Exit codes:

- `0`: Healthy.
- `1`: Attention or Degraded findings exist.
- `2`: invalid CLI usage or unable to inspect DB.

### 3.3 `scripts/smoke_notifications.py`

Operator-triggered notification smoke command.

Checks:

- notification settings can be loaded.
- notifier factory can construct the configured notifier.
- optional smoke send can emit a clearly labeled test message.

Rules:

- default mode is configuration-only and sends nothing.
- sending requires an explicit flag: `--send`.
- sending also requires explicit confirmation: `--confirm-send`.
- smoke message title must start with
  `SMOKE TEST — Paper Trading Notifications`.
- smoke message text must include `SMOKE TEST` and must not resemble a real
  trading event or real paper tick alert.
- command never writes paper state or audit rows.

Inputs:

- `--send`
- `--confirm-send`
- optional `--channel` if supported by the notifier implementation.

Exit codes:

- `0`: configuration valid, and send succeeded if requested.
- `1`: notifier unavailable or send failed.
- `2`: invalid CLI usage.

### 3.4 Price Provider Smoke

Price provider smoke can live inside `check_paper_trading_health.py` or a helper it
calls. It should validate that a known liquid ticker can resolve a recent close
without mutating trading state.

Default ticker: `SPY`.

Date rule:

- Query the most recent completed New York trading day close, not "today".
  Weekends, holidays, and pre-close intraday runs must not produce false
  Attention solely because today's close is not final yet.

Rules:

- Uses existing price provider abstractions.
- Does not create or close paper positions.
- Reports unavailable price as Attention, not a crash.

### 3.5 `docs/operations/paper-trading-runbook.md`

Operator-first runbook.

Sections:

- Daily 30-second check.
- After-deploy smoke.
- How to read `/lab/paper-trading`.
- Healthy / Attention / Degraded.
- `PRICE_UNAVAILABLE_1`, `PRICE_UNAVAILABLE_2`, `STUCK_3_PLUS`.
- Scheduler gap.
- Kill switch ON.
- Notification failure.
- Price provider failure.
- Rollback / mitigation.
- Evidence to capture before asking for developer help.
- Optional developer appendix with code entry points.

### 3.6 `docs/operations/paper-trading-acceptance-checklist.md`

Release gate checklist.

Sections:

- Pre-deploy readiness.
- Immediate post-deploy smoke.
- DB/audit health snapshot.
- Notification smoke.
- Price provider smoke.
- Next real tick acceptance.
- Rollback / mitigation decision.
- Sign-off block.

The checklist must explicitly mark which items can be completed immediately and
which require the next real tick.

---

## 4 — Health Semantics

6h should reuse the 6f status vocabulary without inventing a new ontology:

- **Healthy:** no degraded checks and no active attention findings.
- **Attention:** system is visible, but operator should inspect a current
  operational condition.
- **Degraded:** health tool or dashboard query surface could not load part of the
  required telemetry.
- **Failed:** script could not run the requested check at all, such as invalid
  credentials, unreachable DB, unreachable app, or invalid CLI usage.

`Failed` is a script outcome. It is not a paper-engine state.

Fresh / empty DB semantics:

- No completed paper tick yet is an explicit empty state.
- Fresh / empty DB is Healthy unless a query, configuration, app, notifier, or
  provider check fails.
- Empty does not mean Attention and does not mean Degraded.

---

## 5 — Testing Strategy

Tests must prove the tools are safe before proving they are useful.

Required coverage:

- CLI help / argument validation.
- Unauthenticated route smoke detects redirect to `/login`.
- POST route smoke detects 405.
- Authenticated route smoke detects required markers.
- Route smoke rejects control-plane markers.
- Health check returns Healthy on fresh/empty DB.
- Health check returns Attention for `PRICE_UNAVAILABLE` 3+.
- Health check returns Attention for scheduler gap.
- Health check returns Attention for kill switch ON.
- Health check reports DB inspection failure as exit code 2.
- Notification smoke default mode does not send.
- Notification smoke `--send` requires `--confirm-send` and emits a fixed
  `SMOKE TEST — Paper Trading Notifications` title.
- No 6h script inserts, updates, or deletes `paper_order`, `paper_fill`,
  `paper_position`, `paper_cash_ledger`, or `paper_audit_event`.

No-mutation tests must use two layers:

1. Static guard: grep/AST-style test rejects `insert`, `update`, `delete`,
   `session.add`, `session.merge`, and `session.delete` in 6h scripts when they
   touch paper trading tables/models.
2. Runtime guard: run each script against a test DB and assert row counts for
   `paper_order`, `paper_fill`, `paper_position`, `paper_cash_ledger`, and
   `paper_audit_event` are unchanged before vs. after.

Verification before merge:

- `uv run pytest`
- `uv run ruff check .`
- `uv run alembic heads`
- Manual deployed smoke against the target host when available.

---

## 6 — Implementation Locks

| Lock | Rule |
|---|---|
| 6h-L1 | 6h tools are observational and never mutate paper trading state. |
| 6h-L2 | 6h checks are operator-triggered deterministic snapshots, not continuous monitors. |
| 6h-L3 | 6h does not attempt automatic remediation. |
| 6h-L4 | Runbook content is operator-first: symptom → classification → safe action → evidence. |
| 6h-L5 | Acceptance checklist is a release gate before unattended daily paper ticks. |
| 6h-L6 | Notification smoke must be explicit, labeled as smoke, and must not mimic real trading events. |
| 6h-L7 | Scripts use existing canonical query/projection helpers where available instead of re-deriving trading semantics ad hoc. |
| 6h-L8 | Scripts must return stable exit codes suitable for CI/deploy tooling. |
| 6h-L9 | Fresh / empty DB is Healthy empty state unless an actual check fails. |
| 6h-L10 | Price smoke uses the most recent completed NY trading day close. |
| 6h-L11 | No-mutation safety is verified by both static guards and DB before/after row-count checks. |

---

## 7 — Out-of-Scope Future Work

- IBKR / broker read-only probe.
- BrokerExecutionEngine.
- RealtimeExecutionEngine.
- ShadowPoolOptimizer.
- Daily NAV snapshot table.
- DrawdownHaltGate.
- CorrelationCapGate.
- Control-plane actions in `/lab/paper-trading`.
- Automated remediation.
