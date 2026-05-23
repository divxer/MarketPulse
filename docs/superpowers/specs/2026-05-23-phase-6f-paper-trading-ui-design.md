# Phase 6f — Paper Trading Operations UI Design

**Date:** 2026-05-23
**Status:** Spec — ready for implementation plan
**Scope:** Phase 6 MVP sub-project (6f)
**Route:** `/lab/paper-trading`
**Depends on:** 6a paper-trading canonical state, 6b risk gates, 6b+ paper P&L realization, 6g observability taxonomy
**Independent of:** Phase 7 broker execution

## 1 — Goal and Scope

Phase 6f is the `/lab/paper-trading` read-only operations dashboard for paper
trading. It completes Phase 6 visual awareness: 6g is the interrupt channel;
6f is the state inspection surface.

6f is an inspection plane, not a control plane. It lets an operator inspect
current paper-trading health, but it does not mutate trading state or trigger
operational actions.

**Success criterion:**

> Without querying the database or reading raw logs, an operator can determine
> whether the paper engine is healthy and locate abnormal positions/orders
> within 2 minutes.

**Current Operational Window (COW):** the current cycle-scoped operational
attention window. Default data scope is current paper state + COW, not a
historical archive.

MVP hard boundaries:

- Read-only.
- Single-page operational dashboard, not workflow UI.
- Refresh-on-load only.
- No websocket or polling.
- No mutation endpoint.
- No kill-switch toggle.
- No replay buttons.
- No force close, retry, or repair actions.
- No charts, performance visualization, or strategy analytics.
- No historical audit archive.
- No full-text search, arbitrary filter builder, or export.

Operational status takes precedence over performance visualization. Charts and
strategy analytics are intentionally excluded from the 6f MVP.

## 2 — Information Architecture

The page lives at `/lab/paper-trading`. It is a Lab/diagnostic page, not a
top-level production trading page. The navigation label is `纸上交易`; the page
H1 is `Paper Trading · Operations`.

Access follows the existing admin/lab route authorization policy. 6f adds no
new role model.

6f MVP is desktop-first. Mobile optimization is deferred.

Primary operational signals must fit within a single desktop viewport whenever
data volume allows. The visual direction is Compact Ops Console with selective
Holdings-style KPI typography and spacing.

Primary hierarchy:

1. Health Summary
2. Critical Events + Positions
3. Secondary drill-down tabs:
   - Orders & Fills
   - Audit Timeline

Layout locks:

- Critical Events and Positions form the primary operational inspection row.
- Critical Events panel is always visible above the fold on desktop.
- Positions and exit health have higher visual priority than Audit Timeline.
- KPI strip uses concise numeric operational indicators only.
- No donut, sparkline, chart hero, or exposure visualization in MVP.
- Audit Timeline is secondary drill-down content and must not dominate the
  initial viewport.
- Secondary tabs are ephemeral UI state only. No URL/query-param
  synchronization in 6f MVP.

Refresh semantics:

- Data is fetched once on server-side page load.
- No polling and no in-app refresh action.
- `Generated at HH:MM NY` reflects server-side render/query time in NY trading
  timezone semantics, not browser local time.
- Operator refreshes the browser to reload.

## 3 — Data Model and Query Boundary

6f owns a dedicated read-side query model in
`marketpulse/trading/query_models.py`.

Route behavior:

- Route calls one top-level loader: `load_paper_trading_dashboard(...)`.
- Route does not assemble DB rows directly.
- Template renders shaped data only.
- Frontend/template does not infer lifecycle, stuck status, recovery,
  empty/degraded state, or system health.

DTO shape:

```python
@dataclass(frozen=True)
class SectionResult[T]:
    status: Literal["ok", "error"]
    data: T | None
    empty_message: str | None = None
    error_title: str | None = None
    degraded_reason: str | None = None
```

Section result invariants:

- If `status == "error"`, `data MUST be None`.
- If `status == "ok"`, `data MUST be non-None`, even if empty.

Helper constructors enforce the invariants:

```python
section_ok(data, empty_message=None)
section_error(error_title, degraded_reason)
```

```python
@dataclass(frozen=True)
class OperationalWindow:
    started_at: datetime | None
    source_event_type: str | None
    label: str
```

Fresh DB behavior:

- `started_at=None`
- `source_event_type=None`
- `label="No paper tick has completed yet"`

```python
@dataclass(frozen=True)
class AuditTimeline:
    rows: list[AuditTimelineRow]
    routine_hidden_count: int
    show_routine: bool = False
```

```python
@dataclass(frozen=True)
class PaperTradingDashboard:
    generated_at: datetime
    current_operational_window: OperationalWindow
    system_status: SystemStatus
    health: HealthSummary
    critical_events: SectionResult[list[OperationalEvent]]
    positions: SectionResult[list[PositionRow]]
    order_lifecycles: SectionResult[list[OrderLifecycleRow]]
    audit_timeline: SectionResult[AuditTimeline]
```

Boundary locks:

- `SystemStatus` is computed only by `load_paper_trading_dashboard(...)`, not by
  route/template.
- 6f may reuse 6g operational taxonomy/projection rules.
- 6f does not reuse notification DTOs/templates as UI models.
- 6f does not write to paper tables.
- Query-model loaders batch-load `paper_order`, `paper_position`,
  `paper_fill`, and `paper_audit_event`. No per-row DB query loops.
- Query-model/data-fetch failures become section-level degraded states.
- Template/render exceptions are not swallowed.

## 4 — Operational Semantics

### Current Operational Window

COW is cycle-scoped, not time-scoped. It represents current operational
attention state, not historical audit browsing.

Window start is the latest of:

- `TICK_COMPLETED.timestamp`
- `KILL_SWITCH_CYCLE_SKIPPED.timestamp`
- `TICK_REPROCESSED_COMPLETED.timestamp`

If none exists:

- `started_at=None`
- empty/fresh state
- label: `No paper tick has completed yet`

COW boundary events define the inspection window. Their event type may still
contribute to `SystemStatus` independently. Example:
`TICK_REPROCESSED_COMPLETED` can start the COW and still make the dashboard
`Attention`.

COW queries use `timestamp >= started_at`, not `timestamp > started_at`, so
the boundary event remains inside the current inspection window.

### Critical Events Panel

Critical Events is the current operational attention surface. It is not a raw
audit event feed.

It shows only:

- unresolved warnings
- severe operational events
- useful recent recoveries

Routine successes are excluded. Resolved warnings are collapsed/suppressed by
the query model, not the frontend.

Example: `PRICE_UNAVAILABLE` followed by recovery `POSITION_CLOSED` collapses
into recovery/resolved state, not two scary cards.

### System Status

Status enum:

- `Healthy`
- `Attention`
- `Degraded`

Priority:

```text
Degraded > Attention > Healthy
```

Rules:

- `Degraded` if any `SectionResult.status == "error"`.
- Otherwise `Attention` if:
  - COW has unresolved critical/warning.
  - Kill switch is ON.
  - Latest tick status is `completed_with_errors`.
  - Latest tick status is `kill_switch_skipped`.
  - Latest tick status is `reprocessed_completed`.
- Otherwise `Healthy`.

`Degraded` means dashboard data quality is impaired. It does not necessarily
mean the trading engine is broken. `Attention` means the engine/audit state
needs operator review.

### Positions Exit Overlay

Positions status has two layers:

- `canonical_status`: from `paper_position.status`
- `operational_exit_status`: read-only query-model overlay

Overlay enum:

- `CLOSED`
- `ON_SCHEDULE`
- `EXIT_PENDING`
- `PRICE_UNAVAILABLE_1`
- `PRICE_UNAVAILABLE_2`
- `STUCK_3_PLUS`

Rules:

- `paper_position.status == CLOSED` -> `CLOSED`
- `OPEN` and `horizon_date > current NY trading date` -> `ON_SCHEDULE`
- `OPEN` and `horizon_date <= current NY trading date` and latest
  `PRICE_UNAVAILABLE` attempt count is 0 -> `EXIT_PENDING`
- latest `PRICE_UNAVAILABLE` attempt count = 1 -> `PRICE_UNAVAILABLE_1`
- latest `PRICE_UNAVAILABLE` attempt count = 2 -> `PRICE_UNAVAILABLE_2`
- latest `PRICE_UNAVAILABLE` attempt count >= 3 -> `STUCK_3_PLUS`

Overlay never writes back to `paper_position` and never changes execution
semantics.

### Kill Switch State

Health Summary displays kill switch state as operational state only.

Source priority:

- repository/helper-exposed environment override, if available
- latest `KILL_SWITCH_FLIPPED` audit event
- `unknown` when no reliable source is available

If an environment override forces the kill switch on, the UI displays
`Kill switch: ON (env override)`.

## 5 — Tabs and Rows

### Positions Tab

Positions is the default primary tab. It shows execution-state, not market
valuation.

Scope:

- Current `OPEN` positions.
- Plus `CLOSED` positions from COW when operationally useful, for example a
  recovered close after prior `PRICE_UNAVAILABLE`.

MVP fields:

- Ticker
- Strategy
- Qty
- Entry Price
- Entry Date
- Horizon Date
- Status (`OPEN` / `CLOSED`)
- Exit Health
- Realized P&L if closed

Explicitly deferred:

- Current/last known price.
- Unrealized P&L.
- MtM valuation.
- Quote freshness.
- Exposure charts.

Empty state:

- `No open paper positions`
- If COW contains operationally useful closed/recovered rows, show those rows
  rather than empty.

### Orders & Fills Tab

Orders & Fills is order-centric lifecycle view.

Locks:

- One row = one `paper_order`.
- Fills, position, and latest audit are joined into a read-only query model.
- Frontend does not manually correlate order/fill/position rows.

MVP fields:

- Order ID
- Ticker
- Strategy
- Qty
- Order Status
- Placed At
- Entry Price / Entry Time
- Exit Price / Exit Time
- Realized P&L
- Latest Audit Reason

Latest Audit Reason:

- latest `paper_audit_event` for this `order_id` within COW, if any
- do not pull stale historical reasons from outside COW

Empty state:

- `No order lifecycle activity in current cycle`

Deferred row expansion:

- Audit timeline per order.
- Entry/exit fill detail.
- Allocation provenance.
- Risk-gate reject detail.

### Audit Timeline Tab

Audit Timeline is an operator triage feed, not a raw log browser.

Scope:

- COW only.

Default visible events:

- `ENGINE_INVARIANT_ERROR`
- `SCHEDULER_GAP_DETECTED`
- `TICK_REPROCESSED_COMPLETED`
- `KILL_SWITCH_FLIPPED`
- `KILL_SWITCH_CYCLE_SKIPPED`
- `PRICE_UNAVAILABLE`
- `ORDER_REJECTED` where daily-loss or failed gates are present
- `POSITION_CLOSED` only when recovery from prior `PRICE_UNAVAILABLE`

Routine hidden by default:

- `ORDER_PLACED`
- `ORDER_PLACED_DUPLICATE`
- `ORDER_ENTRY_FILLED`
- normal `POSITION_CLOSED`
- `TICK_COMPLETED`

`Show routine events`:

- additive, not a mode switch
- critical/warning events always remain visible
- display `N routine events hidden`
- client-side reveal of already-loaded routine rows, not a second server query

Deferred:

- Full-text search.
- Regex.
- Arbitrary filters.
- Export.
- Saved views.
- Historical audit archive.

## 6 — Error, Empty, and Testing Strategy

### Error Semantics

`/lab/paper-trading` is fail-soft at the section/query-model layer. A failed
query model degrades only its section/card, not the whole page.

Any section error makes `SystemStatus = Degraded`.

Route-level rendering/template exceptions are not swallowed. Only query-model
or data-fetch failures are converted into degraded UI states.

### Empty Semantics

Empty operational state is explicit and healthy.

Fresh DB with no tick yet is `Healthy`, unless a query failure occurs.

Empty rendering comes from query-model semantics, not frontend truthiness
checks.

Canonical messages:

- `No paper tick has completed yet`
- `No open paper positions`
- `No operational events in current cycle`
- `No order lifecycle activity in current cycle`

### Required Tests

Pure query model:

- COW boundary.
- `SectionResult` invariant helpers.
- `SystemStatus` priority.
- position overlay enum.
- order lifecycle joins.
- audit timeline routine hidden count.
- recovery collapse.

Partial failure:

- positions query fails.
- other sections still render.
- `SystemStatus = Degraded`.
- Positions card shows `Unable to load Positions`.

Route/template:

- `/lab/paper-trading` requires auth.
- normal render.
- fresh DB empty render.
- degraded section render.
- no traceback.
- key labels present.

Read-only/no-controls boundary:

- `POST /lab/paper-trading` returns 405 or route not registered.
- rendered HTML does not contain:
  - `Force Close`
  - `Replay`
  - `Retry`
  - `Kill Switch Toggle`
- no mutation submit controls for kill switch/replay/force-close.

## 7 — Explicit Deferrals

The following are intentionally out of scope for the 6f MVP:

- Kill switch toggle or any control-plane action.
- Manual replay button.
- Force close, retry, repair, or manual intervention workflows.
- Live polling, websocket, or in-app refresh button.
- Historical paper archive.
- Full audit explorer.
- Search DSL, regex, saved filters, export.
- Charts, equity curves, exposure donuts, sparklines, or strategy analytics.
- Cash ledger browser or accounting-grade reconciliation tooling.
- Mobile optimization.
- Broker integration or real-money workflow.

These deferrals are product locks, not missing features. They preserve 6f as
an operator confidence surface rather than an OMS, SIEM, or Grafana clone.
