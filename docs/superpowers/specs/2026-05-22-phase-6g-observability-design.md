# Phase 6g — Observability + Alerting (Paper-Trading Operator Notifications)

**Date:** 2026-05-22
**Status:** Spec — ready for implementation plan
**Scope:** Phase 6 MVP sub-project (6g)
**Depends on:** 6a (audit event ledger), 6b (risk gates), 6b+ (paper P&L realization, PRICE_UNAVAILABLE event)
**Independent of:** 6f (paper-trading UI) — 6g produces push notifications; 6f produces in-app dashboards. They consume the same `paper_audit_event` table but do not depend on each other.

## 1 — Goal & Anti-goals

### Goal

Translate the existing `paper_audit_event` stream into operator push notifications so the daily paper-trading tick is observable WITHOUT opening the database, SSHing into the NAS, or reading log files.

**Operator success criterion:**

> 不打开数据库、不 SSH、不看日志，也能知道 paper engine 今天是否正常工作。

Concretely (all signals land on the post-tick notification pass — see § 5.1 / lock 6g-L7 for cadence; there is no real-time fan-out):
- A fill happens → the next post-tick summary surfaces it.
- A reject happens → the next post-tick summary names the gate; if it's a daily-loss reject, an additional critical push fires.
- Kill switch flips → the **next** tick's notification pass emits a standalone critical push (operator may see a delay of up to one tick interval; for daily paper trading this is ≤ 24h).
- PRICE_UNAVAILABLE attempt_count reaches 3 → standalone critical push at the same tick the third row is written.
- Tick ends → operator gets a one-glance summary (always, even on zero-activity days — daily heartbeat).

### Anti-goals

- **No new trading state.** 6g is a strict consumer of `paper_audit_event`. It writes no new tables, owns no projections-on-disk, runs no watermarks.
- **No notifier-queue / dispatcher subsystem.** The existing `marketpulse/alerts/notifier.py` Protocol (Bark / ServerChan / SMTP) is the transport layer; 6g calls it directly.
- **No coupling to engine write path.** Audit-write sites in `repository.py` MUST stay notification-unaware (lock 6g-L1).
- **No automatic replay on tick reprocess.** Re-running a historical tick does NOT silently re-push past notifications to the operator (lock 6g-L7). Disaster recovery is an explicit CLI action (lock 6g-L8).
- **No LLM narrative in MVP.** The Phase 2 AI-recap subsystem stays independent. 6g produces templated structured pushes — not stories.

## 2 — Architecture

### 2.1 Data flow

```
                  ┌──────────────────────────────────────┐
                  │      paper_trading_tick_job          │
                  │                                      │
                  │  tick_started_at = clock.now()       │
                  │  result = daily_cycle.run(...)       │
                  │  ───────────────────────────────     │
                  │  ┌──────────────────────────────┐    │
                  │  │ notify_paper_tick_events(... │ ←──┼── since=tick_started_at
                  │  │   since=tick_started_at,     │    │   tick_date=result.tick_date
                  │  │   tick_date=...,             │    │
                  │  │   repository=...,            │    │
                  │  │   notifier=...,              │    │
                  │  │ )                            │    │
                  │  └────────────┬─────────────────┘    │
                  └───────────────│──────────────────────┘
                                  │ read-only DB
                                  ↓
              ┌───────────────────────────────────────────┐
              │   paper_audit_event (6a-owned, 6g reads)  │
              └───────────────────────┬───────────────────┘
                                      │
                                      ↓ query rows since=tick_started_at
              ┌───────────────────────────────────────────┐
              │  audit_projection.py (pure functions)     │
              │  ┌─────────────────────────────────────┐  │
              │  │ select_critical_events(rows, ...)   │  │
              │  │ summarize_tick(rows, ...)           │  │
              │  └─────────────────────────────────────┘  │
              └───────────────────────┬───────────────────┘
                                      │
                                      ↓ CriticalEvent[] + TickSummary
              ┌───────────────────────────────────────────┐
              │  templates.py (pure renderers)            │
              └───────────────────────┬───────────────────┘
                                      │
                                      ↓ (title, body)
              ┌───────────────────────────────────────────┐
              │  alerts.notifier.Notifier.send(...)       │
              │  Bark / ServerChan / SMTP                 │
              └───────────────────────────────────────────┘
```

### 2.2 Section 2 locks

- **(6g-L1)** Notifications are emitted **only after** `daily_cycle.run()` completes. Audit writes are never notification-aware. The audit-write code path in `repository.write_audit_event` is unchanged.
- **(6g-L13)** `observability/` is the audit-consumer layer; `alerts/` remains transport-only. Dependency direction is `observability → alerts`, never reverse.
- **(6g-L19)** Audit row filter applies `context["tick_date"] == tick_date` **conditionally**: events that carry the key in their context (TICK_COMPLETED, TICK_REPROCESSED_COMPLETED, KILL_SWITCH_CYCLE_SKIPPED) MUST match; events that don't (ORDER_PLACED, ORDER_ENTRY_FILLED, POSITION_CLOSED, PRICE_UNAVAILABLE, KILL_SWITCH_FLIPPED, ORDER_REJECTED, ORDER_CANCELLED, ORDER_PLACED_DUPLICATE, SCHEDULER_GAP_DETECTED, **ENGINE_INVARIANT_ERROR**) are admitted on the time window alone. ENGINE_INVARIANT_ERROR was reclassified out of the tick_date-required group because 6a writes its context with `phase / order_id / position_id / error / as_of` — `tick_date` is not guaranteed.

## 3 — Notification Taxonomy

6g uses **hybrid notification** (lock 6g-L2): critical audit events emit standalone pushes; routine activity is summarized once per tick. No per-event spam for normal fills/orders.

### 3.1 Critical (standalone) events

Each row triggers exactly one `notifier.send(title, body, url=None)` call. **No coalescing across rows of the same event type** in MVP — three `ENGINE_INVARIANT_ERROR` rows in one tick produce three pushes. (If noise becomes a problem, defer to a future enhancement; explicit-per-row is operator-correct because each row may have different context.)

| Audit event | Standalone? | Title | Notes |
|---|---|---|---|
| `KILL_SWITCH_FLIPPED` (active=true) | ✅ always | `🛑 Kill Switch FLIPPED` | |
| `KILL_SWITCH_FLIPPED` (active=false) | ✅ always | `✅ Kill Switch CLEARED` | |
| `KILL_SWITCH_CYCLE_SKIPPED` | ✅ once per active period (lock 6g-L5) | `🛑 Kill Switch — Cycle Skipped` | First skip after each KILL_SWITCH_FLIPPED(true); suppressed for subsequent skips in same period |
| `ENGINE_INVARIANT_ERROR` | ✅ always | `🛑 Engine Invariant Error` | |
| `SCHEDULER_GAP_DETECTED` | ✅ always | `🛑 Scheduler Gap Detected` | |
| `TICK_REPROCESSED_COMPLETED` | ✅ always (lock 6g-L9) | `⚠️ Tick Reprocessed` | Operational integrity signal |
| `ORDER_REJECTED` | ✅ iff `context["failed_gates"]` contains `daily_loss` (lock 6g-L3) | `🛑 Daily Loss Limit Tripped` | Other rejects → summary only |
| `PRICE_UNAVAILABLE` | ✅ iff `context["attempt_count"] == 3` exactly (lock 6g-L4) | `⚠️ Position Stuck — <TICKER>` | attempt_count 1/2 → summary only; ≥4 → silent |
| `POSITION_CLOSED` | ✅ iff position has prior PRICE_UNAVAILABLE history (lock 6g-L4 recovery) | `✅ Position Recovered — <TICKER>` | Normal close → summary only |

### 3.2 Routine (summary) events

Aggregated into one `📊 Paper Tick YYYY-MM-DD` push per tick. **The summary push is emitted every tick, even when zero routine activity occurred** — it functions as a daily heartbeat (§ 9.1). Empty sections are omitted (lock 6g-L12), but at minimum the header + status + cash balance is always present.

- `ORDER_PLACED` — ticker, strategy, quantity
- `ORDER_PLACED_DUPLICATE` — count only (rare; usually scheduler retry)
- `ORDER_REJECTED` (default, non-daily_loss) — ticker + `gate_name`, e.g. `❌ GOOG (sector_exposure)`
- `ORDER_CANCELLED` — ticker
- `ORDER_ENTRY_FILLED` — ticker, fill price
- `POSITION_CLOSED` (normal) — ticker, exit price, realized P&L
- `PRICE_UNAVAILABLE` (attempt_count == 1 or 2) — included in residual count footer

### 3.3 Section 3 locks

- **(6g-L2)** 6g uses hybrid notification: critical audit events emit standalone push; routine activity is summarized once per tick. No per-event spam for normal fills/orders.
- **(6g-L3)** `ORDER_REJECTED` enters summary by default; standalone push iff `context["failed_gates"]` includes `daily_loss`.
- **(6g-L4a)** `PRICE_UNAVAILABLE` standalone push iff `context["attempt_count"] == 3` exactly. Subsequent attempts (≥ 4) suppressed.
- **(6g-L4b)** Recovery push iff this tick's `POSITION_CLOSED` row has a `position_id` with ≥ 1 `PRICE_UNAVAILABLE` audit row whose `timestamp < POSITION_CLOSED.timestamp` (i.e., prior in history, not concurrent).
- **(6g-L4c)** Invariant assumption: `context["attempt_count"]` is **per-position monotonic non-decreasing**. Phase 6b+ T6/T7 enforce this — each new `PRICE_UNAVAILABLE` row sets `attempt_count = repository.count_price_unavailable_attempts(position_id) + 1`, and audit rows are append-only (lock v). If a future change introduces "retry counter reset" semantics, 6g-L4a must be revisited.
- **(6g-L5)** `KILL_SWITCH_CYCLE_SKIPPED` standalone push iff no prior `KILL_SWITCH_CYCLE_SKIPPED` row exists since the most recent `KILL_SWITCH_FLIPPED(active=true)`. Pure audit projection — no state.
  - **Boundary contract:** If no `KILL_SWITCH_FLIPPED(active=true)` row exists in history (orphan SKIPPED — shouldn't happen but defensive), the helper returns `False` (no prior skip in nonexistent period → emit the push). This is operator-correct: a `SKIPPED` without a flip is itself anomalous and worth notifying.
- **(6g-L6)** Paper trading MVP has exactly one routine daily notification: the post-tick summary. No separate daily digest cron. Phase 2 AI recap is independent and continues operating in its own track.
- **(6g-L9)** `TICK_REPROCESSED_COMPLETED` emits critical standalone push with prefix `⚠️ Tick Reprocessed — YYYY-MM-DD`.

## 4 — Format Conventions

### 4.1 Title emoji prefix taxonomy (lock 6g-L10)

| Prefix | Semantics |
|---|---|
| 🛑 | Critical — kill switch / engine invariant / risk-critical reject |
| ⚠️ | Warning — data gap / reprocess / position stuck |
| ✅ | Recovery — previously anomalous state cleared |
| 📊 | Routine — daily tick summary (exactly one per tick) |

### 4.2 Critical standalone templates

```
Title: 🛑 Kill Switch FLIPPED
Body:  Reason: <context.reason>
       Time:   <timestamp HH:MM NY>

Title: ✅ Kill Switch CLEARED
Body:  Reason: <context.reason>

Title: 🛑 Kill Switch — Cycle Skipped
Body:  Date:   <tick_date>
       Reason: <context.reason>

Title: 🛑 Engine Invariant Error
Body:  Phase:  <context.phase>
       Error:  <context.error>
       <position_id / order_id if present>

Title: 🛑 Scheduler Gap Detected
Body:  Last tick: <context.last_tick_date>
       Missing:   <context.gap_days> trading day(s)

Title: ⚠️ Tick Reprocessed
Body:  Date: <tick_date>
       Original run superseded

Title: 🛑 Daily Loss Limit Tripped
Body:  Order: <ticker> <strategy> × <quantity>
       Loss today: <-$N.NN>
       Failed gates: daily_loss[, ...]

Title: ⚠️ Position Stuck — <TICKER>
Body:  Strategy: <strategy>
       Horizon:  <horizon_date>
       3 retries failed
       Source:   <source>

Title: ✅ Position Recovered — <TICKER>
Body:  Closed after <N> retries
       Exit @ <price>
       Realized P&L: <±$N.NN>
```

### 4.3 Routine summary template

```
Title: 📊 Paper Tick YYYY-MM-DD

Body:
订单：<P> placed, <R> rejected
  <TICKER> × <qty> (<strategy>)
  …
  ❌ <TICKER> (<gate_name>)
  …

成交：<E> entries, <X> exits
  ENTRY: <TICKER> @ <price>, …
  EXIT:  <TICKER> @ <price>, P&L <±$N.NN>

今日 P&L：<±$N.NN> (realized)
现金：$N.NN
活跃持仓：<N> (<K> with PRICE_UNAVAILABLE attempt <a>/3)

Status: <cycle_status>
```

### 4.4 Section 4 locks

- **(6g-L10)** Notification format uses mixed Chinese labels + English identifiers. Critical standalone pushes use fixed emoji prefixes: 🛑 critical, ⚠️ warning, ✅ recovery, 📊 routine summary.
- **(6g-L11)** 6g MVP sends notifications with `url=None`. Deep links to `/lab/paper-trading/...` are deferred to 6f integration.
- **(6g-L12)** Routine tick summary is compact and section-skipping. Empty sections are omitted; body is truncated using existing `recap/push.py` truncation behavior; money/price values are rendered with sign and 2 decimals.

## 5 — Idempotency / Replay

### 5.1 Default path

```
window = (tick_started_at, now]   ∧   tick_date == as_of
```

`since = tick_started_at` excludes audit rows from prior runs of the same date. Re-running `paper_trading_tick_job` for an already-processed `tick_date` produces a new (later) `tick_started_at`; prior-run audit rows have `timestamp < since` and are silently filtered out. Operator sees no duplicates.

Edge case (acknowledged): if the FIRST run wrote audit rows but `notify_paper_tick_events` failed/crashed before push, the operator missed those notifications. The default path does NOT recover this — disaster recovery is opt-in via CLI (§5.2).

### 5.2 Disaster recovery — `republish_cli`

```
uv run python -m marketpulse.observability.republish_cli --date 2026-05-22
```

Forces `since = start_of_day(2026-05-22 UTC)` and runs the same `notify_paper_tick_events` function. Operator-triggered only.

CLI behavior:
- Refuses to run when `MP_PAPER_NOTIFICATIONS_ENABLED=false`; exit code 1 (lock 6g-L18).
- Prints `NotificationResult` to stdout in this exact order:
  1. `critical_pushed`: one line per event_type (e.g. `pushed: PRICE_UNAVAILABLE`)
  2. `summary_title` + a 200-char-truncated `summary_body` preview
  3. `failures`: one line per `NotificationFailure` with event_type, title, error
- Exit code 0 iff `failures == ()`; 1 otherwise.
- No `--dry-run` flag in MVP (YAGNI). The stdout preview is sufficient for operator inspection before deciding whether to actually re-run.

### 5.3 Section 5 locks

- **(6g-L7)** `notify_paper_tick_events(since=tick_started_at, ...)` only emits notifications for audit rows written during the current tick execution window. Reprocessing a historical tick does NOT replay prior notifications automatically.
  - **Acknowledged delay:** Kill-switch flips and clears written *between* ticks (e.g., manual operator action at noon) are picked up by the *next* tick's notification window via the extended kill-switch window (lock 6g-L20), not in real-time. This is acceptable for paper trading where ticks are daily; if real-time flip notification becomes required, lock 6g-L1 needs revisiting.
- **(6g-L20)** KILL_SWITCH_FLIPPED rows are scanned in the **extended between-tick window** `[latest_tick_completed_at, notify_started_at]` (where `latest_tick_completed_at = repository.latest_tick_completed_timestamp(before=since)`, falling back to epoch if no prior TICK_COMPLETED exists). This honors lock 6g-L7's "next-tick pickup" promise for externally-triggered flips. All other event types use the narrower `[since=tick_started_at, notify_started_at]` window. KILL_SWITCH_FLIPPED is the only event type with an asymmetric window because it's the only one that can be written externally between ticks.
- **(6g-L8)** Replay / recovery notifications are operator-triggered only, via explicit CLI (`republish_tick_notifications --date YYYY-MM-DD`). No automatic replay fan-out.
- **(6g-L18)** `republish_cli` refuses to run when `MP_PAPER_NOTIFICATIONS_ENABLED=false`; exit code 1.

## 6 — Module Structure & Function Signatures

### 6.1 Directory layout

```
marketpulse/observability/
  __init__.py
  paper_tick_notifier.py        # Main entrypoint + dispatch logic.
  audit_projection.py           # Pure functions: critical-event selection + summary building.
  templates.py                  # Title/body renderers.
  republish_cli.py              # 6g-L8 CLI entrypoint.

marketpulse/alerts/notifier.py   # Existing; gains get_notifier_from_settings(settings).

marketpulse/trading/repository.py # Existing; gains:
  - positions_with_prior_price_unavailable(position_ids, before) -> set[int]   (lock 6g-L17)
  - kill_switch_cycle_skipped_in_active_period(before) -> bool                 (lock 6g-L5)
  - latest_tick_completed_timestamp(before) -> datetime | None                 (lock 6g-L20)

marketpulse/scheduler/paper_trading_tick.py   # Existing; adds best-effort notify hook.
```

### 6.2 Entrypoint signature

```python
# marketpulse/observability/paper_tick_notifier.py

from dataclasses import dataclass, field
from datetime import date, datetime

from marketpulse.alerts.notifier import Notifier
from marketpulse.trading.repository import Repository


@dataclass(frozen=True)
class NotificationFailure:
    """Structured failure record so the operator / republish CLI can
    diagnose which event failed without grepping log lines."""
    event_type: str        # e.g. "ORDER_REJECTED", "tick_summary"
    title: str             # the rendered title that was about to be sent
    error: str             # short error category: "send_returned_false",
                           # "send_raised:<ExceptionClass>", "template_error:<...>"


@dataclass(frozen=True)
class NotificationResult:
    """Returned for testability and observability of the notifier itself."""
    critical_pushed: tuple[str, ...]   # event_type strings actually pushed
    summary_pushed: bool
    failures: tuple[NotificationFailure, ...]
    summary_title: str | None = None    # captured for test assertion
    summary_body: str | None = None     # captured for test assertion


def notify_paper_tick_events(
    *,
    since: datetime,
    tick_date: date,
    repository: Repository,
    notifier: Notifier,
    clock: Clock,                          # injected — defines window upper bound
    price_unavailable_threshold: int = 3,
) -> NotificationResult:
    """Audit-driven operator notification dispatcher.

    **Query window:** `[since, notify_started_at]` for engine-written
    events (everything except the kill-switch family), where
    `notify_started_at = clock.now()` is captured at the top of this
    call. The window upper bound is bounded explicitly to keep the
    function deterministic under test (FakeClock) and to prevent races
    with audit rows written by a concurrent process.

    **Between-tick window (kill-switch family only — lock 6g-L20):**
    `KILL_SWITCH_FLIPPED` rows can be written by external triggers
    (manual CLI, drawdown gate inside the engine of a *prior* tick that
    cleared) at any time, including between ticks. To honor lock 6g-L7's
    "kill-switch flips/clears land on the next tick" promise, the entrypoint
    additionally scans KILL_SWITCH_FLIPPED rows in the extended window
    `[latest_tick_completed_at, notify_started_at]`, where
    `latest_tick_completed_at = repository.latest_tick_completed_timestamp(
        before=since)`. If no prior TICK_COMPLETED exists (first-ever
    tick), the kill-switch query uses `[epoch, notify_started_at]`.

    **Audit row filter:** rows are first filtered by
    `since <= timestamp <= notify_started_at`. The `tick_date` parameter
    is applied **conditionally**:
      - Rows whose `context` carries a `"tick_date"` key (e.g.,
        TICK_COMPLETED, TICK_REPROCESSED_COMPLETED, KILL_SWITCH_CYCLE_SKIPPED,
        ENGINE_INVARIANT_ERROR) MUST match `context["tick_date"] ==
        tick_date.isoformat()` — guards against picking up rows from a
        prior tick that bled into the time window.
      - Rows whose context does NOT carry `tick_date` (e.g., ORDER_PLACED,
        ORDER_ENTRY_FILLED, POSITION_CLOSED, PRICE_UNAVAILABLE,
        KILL_SWITCH_FLIPPED) are accepted on the time window alone.
    This is per **lock 6g-L19** (below).

    **Stateless dedup queries:** the entrypoint additionally fetches:
      - `kill_switch_cycle_skipped_in_active_period(before=notify_started_at)`
        for the 6g-L5 boundary.
      - `positions_with_prior_price_unavailable(position_ids=[…],
        before=POSITION_CLOSED.timestamp)` for the 6g-L4b recovery
        detection (called per POSITION_CLOSED row).

    **Dispatches via injected Notifier:**
    - 1 standalone send per critical event matched (per § 3).
    - 1 summary send (📊 Paper Tick ...) per tick when
      `MP_PAPER_NOTIFICATIONS_ENABLED=true`. The summary is a daily
      heartbeat — "system ran today, nothing happened" is itself
      operator-actionable info (matches § 9.1 example).

    **Best-effort (lock 6g-L14):**
      - `notifier.send` returning False → append `NotificationFailure(
        event_type=..., title=..., error="send_returned_false")` and
        continue.
      - Translator-internal exceptions (template render bug, repository
        query crash, projection bug) → caught at the per-event boundary,
        recorded as `NotificationFailure(error="template_error:<...>")`,
        and continue. NEVER propagate to scheduler or engine.

    **Disabled path (lock 6g-L15):** if
    `MP_PAPER_NOTIFICATIONS_ENABLED=false`, returns immediately with
    `critical_pushed=()`, `summary_pushed=False`, no Notifier calls
    issued, and a single `NotificationFailure(event_type="config",
    title="", error="disabled_by_config")` so callers can detect
    the disabled state in tests.
    """
```

### 6.3 Pure projection layer

```python
# marketpulse/observability/audit_projection.py

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class CriticalEvent:
    """One critical audit event scheduled for standalone push.

    Carries the projection's view of the audit row — `templates.py` is
    pure rendering and should NOT need to reach back into raw context
    keys for canonical fields (timestamp / strategy / reason).
    """
    event_type: str        # AuditEventType value
    audit_id: int          # for logging / republish CLI output
    timestamp: datetime    # row's timestamp (UTC) — used in body "Time:" line
    strategy: str | None   # PaperAuditEvent.strategy column (may be None)
    reason: str            # PaperAuditEvent.reason column (may be "")
    context: dict          # raw audit row context (passed to templates
                           # for event-type-specific fields like ticker,
                           # gate_name, attempt_count, etc.)


@dataclass(frozen=True)
class PlacedOrderDetail:
    ticker: str
    strategy: str
    quantity: int


@dataclass(frozen=True)
class TickSummary:
    """Aggregate of routine activity for the 📊 summary push.

    Lists preserved (not just counts) so the template can render
    "AAPL × 10 (momentum)" style detail lines without a second pass
    over audit rows.
    """
    tick_date: date
    cycle_status: str
    orders_placed: int
    orders_placed_detail: list[PlacedOrderDetail]
    orders_rejected: int
    orders_rejected_breakdown: list[tuple[str, str]]   # (ticker, gate_name)
    orders_cancelled: int
    duplicates_skipped: int
    entries_filled: list[tuple[str, Decimal]]          # (ticker, fill_price)
    positions_closed: list[tuple[str, Decimal, Decimal]]  # (ticker, exit_price, realized_pnl)
    total_realized_pnl: Decimal
    cash_balance_end: Decimal
    active_positions_count: int
    active_positions_with_pu: list[tuple[str, int]]    # (ticker, attempt_count_capped)


def select_critical_events(
    *,
    new_audit_rows: list,
    kill_switch_cycle_skipped_in_period: bool,
    positions_with_prior_pu: set[int],
    threshold: int = 3,
) -> list[CriticalEvent]:
    """Stateless decision: which rows in `new_audit_rows` warrant a
    standalone push? See § 3.1 for the per-event rules.

    `kill_switch_cycle_skipped_in_period` is the lock 6g-L5 dedup fact —
    True means a prior KILL_SWITCH_CYCLE_SKIPPED already pushed in the
    current active period; suppress further skips.

    `positions_with_prior_pu` is the lock 6g-L4 recovery dedup set —
    POSITION_CLOSED rows for these position_ids emit recovery push.
    """


def summarize_tick(
    *,
    new_audit_rows: list,
    tick_date: date,
    cash_balance_end: Decimal,
    active_positions_with_pu_attempts: list[tuple[str, int]],
    active_positions_count: int,
) -> tuple[TickSummary, NotificationFailure | None]:
    """Builds the routine summary. Two field-sourcing rules (lock 6g-L21):
    
    1. `cycle_status` is read from the audit row in priority order:
       a. The row's context["status"] field on the TICK_COMPLETED row
          whose context["tick_date"] == tick_date.isoformat(); else
       b. The row's context["status"] on a KILL_SWITCH_CYCLE_SKIPPED row
          with matching tick_date (status will be "skipped"); else
       c. `cycle_status="unknown"` and a `NotificationFailure(
            event_type="tick_summary", error="missing_tick_completed_row")`
          is returned alongside — the summary push still emits (heartbeat
          discipline), but with the unknown status and the failure
          recorded in NotificationResult.failures so the operator can
          tell something is off.
    2. `cash_balance_end` and `active_positions_count` /
       `active_positions_with_pu_attempts` are pulled from the **DB
       directly** by the entrypoint (`repository.cash_balance()` etc.)
       and threaded through to this function — NOT read from audit row
       context. Reasoning: cash + open positions are canonical state in
       paper_position / paper_cash_ledger, not audit context. Audit only
       owns event history. This avoids the "audit row missing → no
       summary" failure mode for state that is recoverable from canonical
       tables.

    Returns the TickSummary plus an optional NotificationFailure for the
    caller to append to NotificationResult.failures."""
```

**Section 6.3 lock:**

- **(6g-L21)** `summarize_tick` field-sourcing: `cycle_status` reads from TICK_COMPLETED row (priority) → KILL_SWITCH_CYCLE_SKIPPED row → falls back to `"unknown"` with a `NotificationFailure(event_type="tick_summary", error="missing_tick_completed_row")`. `cash_balance_end` and active-position fields are pulled from canonical tables (`paper_cash_ledger`, `paper_position`), NEVER from audit row context — heartbeat summary must not crash on missing audit metadata.
    """Aggregate routine events for the summary push. Pure: no DB, no
    notifier. Caller is responsible for fetching cash_balance and active
    position state."""
```

### 6.4 Repository helpers (additions)

```python
# marketpulse/trading/repository.py (additions)

def positions_with_prior_price_unavailable(
    self, *, position_ids: list[int], before: datetime,
) -> set[int]:
    """Lock 6g-L17: batch helper. Returns subset of position_ids that
    have ≥ 1 PRICE_UNAVAILABLE audit row with timestamp < `before`
    (i.e., prior in history, not concurrent with the POSITION_CLOSED
    row being evaluated). Used by 6g translator to detect "recovered"
    POSITION_CLOSED events (lock 6g-L4b).

    Empty-history contract: `position_ids=[]` returns `set()` without
    querying. `position_ids` non-empty but no matching audit rows
    returns `set()`.

    Uses json_extract(context, '$.position_id') matching for consistency
    with Repository.count_price_unavailable_attempts (T6)."""

def kill_switch_cycle_skipped_in_active_period(
    self, *, before: datetime,
) -> bool:
    """Lock 6g-L5: True iff a KILL_SWITCH_CYCLE_SKIPPED audit row exists
    with timestamp > most_recent_KILL_SWITCH_FLIPPED(active=true).timestamp
    AND timestamp < `before`. Pure audit projection — no extra state.

    Empty-history contract: if NO KILL_SWITCH_FLIPPED(active=true) row
    exists in history (orphan SKIPPED state), returns `False` (per 6g-L5
    boundary clause — emit the push)."""

def latest_tick_completed_timestamp(
    self, *, before: datetime,
) -> datetime | None:
    """Lock 6g-L20: returns the most recent TICK_COMPLETED audit row's
    `timestamp` strictly before `before`, or None if no such row exists
    (first-ever tick). Used by 6g to construct the between-tick window
    for KILL_SWITCH_FLIPPED rows, which can be written externally
    (manual CLI, etc.) outside any tick execution.

    Read-only `select()` — boundary guard PASS."""
```

Both helpers are read-only `select()` queries. Lock-iii repository-boundary architecture guard tests pass.

### 6.5 Scheduler hook (single new call site)

```python
# marketpulse/scheduler/paper_trading_tick.py (modification)

def paper_trading_tick_job(
    *,
    notifier: Notifier | None = None,    # NEW — test seam
) -> None:
    # ... existing setup ...
    settings = get_settings()
    if notifier is None:
        notifier = get_notifier_from_settings(settings)  # production default

    tick_started_at = clock.now()                 # NEW
    result = daily_cycle.run(...)

    # NEW — 6g hook, best-effort, never raises.
    # The try/except here is belt-and-braces with 6g-L14's internal
    # capture; the inner contract is authoritative, this layer exists so
    # any *unexpected* escape (e.g., an unhandled exception type) still
    # cannot crash the scheduler job.
    try:
        notify_paper_tick_events(
            since=tick_started_at,
            tick_date=result.tick_date,
            repository=repository,
            notifier=notifier,
            clock=clock,                  # required (lock 6g-L19 window upper bound)
        )
    except Exception as e:
        log.warning("paper_tick_notify_failed", error=str(e))
```

**Test injection seam (lock 6g-L16):** `notifier` is a keyword-only parameter so L3/E2E tests can pass `CapturingNotifier()` directly without monkey-patching `get_notifier_from_settings`. Production scheduler calls `paper_trading_tick_job()` with no args → defaults to settings-driven notifier.

### 6.6 Section 6 locks

- **(6g-L14)** `notify_paper_tick_events(...)` is best-effort. Notifier failures and translator errors are captured/logged and returned in `NotificationResult`; they never propagate to scheduler or engine. Per-send timeout is enforced by the transport layer (`BarkNotifier`/`ServerChanNotifier` already use `httpx.post(..., timeout=10)`); translator does not retry on timeout failures — the failure is logged and the next event proceeds.
- **(6g-L15)** Paper notifications have an independent enable flag `MP_PAPER_NOTIFICATIONS_ENABLED`; disabled means no sends, but projection functions remain testable.
- **(6g-L17)** Recovery detection uses repository batch helper `positions_with_prior_price_unavailable(position_ids)`, not per-position ad hoc SQL.

## 7 — Configuration

New env vars:

| Var | Default | Semantics |
|---|---|---|
| `MP_PAPER_NOTIFICATIONS_ENABLED` | `true` | Master switch for 6g. Disabled = no sends, projection still testable. Independent of `NOTIFIER_RECAP_ENABLED` (Phase 2). |

Reused env vars (no changes):

| Var | Reused from | Notes |
|---|---|---|
| `NOTIFIER_KIND` | Phase 2 | bark / serverchan / smtp / none |
| `NOTIFIER_BARK_URL` | Phase 2 | iOS push |
| `NOTIFIER_SERVERCHAN_KEY` | Phase 2 | WeChat push |
| `NOTIFIER_SMTP_*` | Phase 2 | email fallback |

## 8 — Test Strategy

| Layer | File | Tests | Speed |
|---|---|---|---|
| L1 projection | `tests/observability/test_audit_projection.py` | Build synthetic `PaperAuditEvent` lists → assert `CriticalEvent[]` / `TickSummary` return values. Cover each lock (6g-L3 daily_loss filter, 6g-L4 attempt-3 threshold + recovery detection, 6g-L5 kill-switch dedup decision). | ms |
| L2 templates | `tests/observability/test_templates.py` | `CriticalEvent`/`TickSummary` → string. Assert emoji prefix, label, money formatting (sign + 2 decimals), empty-section skipping. | ms |
| L3 notifier entrypoint | `tests/observability/test_paper_tick_notifier.py` | Real SQLite DB seeded with audit rows + `CapturingNotifier`. Assert `NotificationResult` matches expected + `.sent` list. Cover `MP_PAPER_NOTIFICATIONS_ENABLED=false` early-return + translator exception isolation (lock 6g-L14). | tens of ms |
| E2E scheduler | `tests/trading/test_paper_tick_notifies_after_run.py` | Full `paper_trading_tick_job` with `CapturingNotifier`. One happy-path test verifying audit → notification chain end-to-end. | ~100ms |
| Repository | `tests/trading/test_repository_observability_helpers.py` | Unit tests for `positions_with_prior_price_unavailable` + `kill_switch_cycle_skipped_in_active_period`. | tens of ms |
| Architecture guard | `tests/architecture/test_repository_boundary.py` | Existing test; new helpers must pass read-only-select check. | ms |

**Capturing notifier:**

```python
class CapturingNotifier:
    def __init__(self):
        self.sent: list[tuple[str, str, str | None]] = []
    def send(self, title, body, url=None) -> bool:
        self.sent.append((title, body, url))
        return True
```

### 8.1 Section 8 locks

- **(6g-L16)** Tests are layered: pure projection, templates, notifier entrypoint with seeded DB, and one E2E scheduler hook test. Projection and template tests stay separate.

## 9 — Operational Expectations

Three scenarios spell out what the operator sees on Bark/ServerChan/SMTP.

### 9.1 Normal day, no activity

> 📊 Paper Tick 2026-05-22
>
> 订单：0 placed, 0 rejected
>
> 成交：0 entries, 0 exits
>
> 今日 P&L：+$0.00
> 现金：$10,000.00
> 活跃持仓：0
>
> Status: completed

**1 push.**

### 9.2 Normal day with activity

> 📊 Paper Tick 2026-05-22
>
> 订单：3 placed, 1 rejected
>   AAPL × 10 (momentum)
>   NVDA × 5 (defensive)
>   MSFT × 8 (momentum)
>   ❌ GOOG (sector_exposure)
>
> 成交：2 entries, 1 exit
>   ENTRY: AAPL @ 155.50, NVDA @ 432.10
>   EXIT:  TSLA @ 248.30, P&L +$32.50
>
> 今日 P&L：+$32.50 (realized)
> 现金：$9,847.50
> 活跃持仓：4
>
> Status: completed

**1 push.**

### 9.3 PRICE_UNAVAILABLE escalation day

Day D (first PU): summary footer shows `活跃持仓：4 (1 with PRICE_UNAVAILABLE attempt 1/3)`. No standalone push.

Day D+1 (second PU): summary footer shows `(1 with PRICE_UNAVAILABLE attempt 2/3)`. No standalone push.

Day D+2 (third PU): summary footer + **1 standalone push**:

> ⚠️ Position Stuck — AAPL
>
> Strategy: momentum
> Horizon:  2026-05-22
> 3 retries failed
> Source:   yfinance

Day D+3: summary footer shows `(1 with PRICE_UNAVAILABLE attempt 4+)`. No new standalone push (lock 6g-L4a suppresses ≥ 4). The `N+` notation caps display at the threshold; the actual `attempt_count` value is still in the audit row.

Day D+N (provider recovers, exit succeeds): **1 standalone push**:

> ✅ Position Recovered — AAPL
>
> Closed after 5 retries
> Exit @ 152.10
> Realized P&L: +$21.00

### 9.4 Kill switch day

When `KILL_SWITCH_FLIPPED(active=true)` is written by an external trigger (manual CLI, drawdown gate, etc.), the next post-tick notification pass emits a standalone push (lock 6g-L7 acknowledged delay — may be up to one tick interval after the actual flip):

> 🛑 Kill Switch FLIPPED
>
> Reason: max_drawdown_exceeded
> Time:   17:30 NY

Subsequent ticks while kill switch is active:

Tick 1 (first skip):

> 🛑 Kill Switch — Cycle Skipped
>
> Date:   2026-05-23
> Reason: kill_switch_active

Tick 2..N (subsequent skips during same active period): no standalone push (lock 6g-L5). Summary push still emits with `Status: skipped`.

When `KILL_SWITCH_FLIPPED(active=false)` finally writes:

> ✅ Kill Switch CLEARED
>
> By: manual_reset

## 10 — Implementation Phases (high level)

Detailed task breakdown belongs in the plan doc. High-level phases:

1. **Repository helpers** — add `positions_with_prior_price_unavailable` + `kill_switch_cycle_skipped_in_active_period`. Architecture guard verifies read-only.
2. **Pure projection** — `audit_projection.py` + `templates.py` with full L1 + L2 test coverage.
3. **Notifier entrypoint** — `paper_tick_notifier.py` with L3 tests (real DB + capturing notifier).
4. **Config + factory** — add `MP_PAPER_NOTIFICATIONS_ENABLED` to settings; `get_notifier_from_settings(settings)` helper in `alerts/notifier.py`.
5. **Scheduler hook** — modify `paper_trading_tick_job`; add E2E test.
6. **Republish CLI** — `republish_cli.py` with disabled-config guard.
7. **Final integration** — full suite + ruff + alembic head + smoke + PR.

## 11 — Locks Summary

| Lock | Section | Statement |
|---|---|---|
| 6g-L1 | § 2.2 | Notifications emitted only after `daily_cycle.run()` completes. Audit writes are never notification-aware. |
| 6g-L2 | § 3.3 | Hybrid notification: critical events standalone; routine activity summarized once per tick. |
| 6g-L3 | § 3.3 | `ORDER_REJECTED` enters summary by default; standalone push iff `failed_gates` includes `daily_loss`. |
| 6g-L4a | § 3.3 | `PRICE_UNAVAILABLE` standalone iff `attempt_count == 3` exactly; ≥ 4 suppressed. |
| 6g-L4b | § 3.3 | Recovery push iff `POSITION_CLOSED` row has position with prior PU history (timestamp strictly before POSITION_CLOSED). |
| 6g-L4c | § 3.3 | Invariant: `attempt_count` is per-position monotonic non-decreasing (enforced by 6b+ T6/T7 + append-only audit). |
| 6g-L5 | § 3.3 | `KILL_SWITCH_CYCLE_SKIPPED` standalone push iff no prior skip exists since most recent `KILL_SWITCH_FLIPPED(active=true)`. If no FLIPPED row exists, returns False (emit). |
| 6g-L6 | § 3.3 / § 1 | Paper trading MVP has exactly one routine daily notification: the post-tick summary (emitted every tick when `MP_PAPER_NOTIFICATIONS_ENABLED=true`; no-op when disabled). No separate daily digest cron. |
| 6g-L7 | § 5.3 | `notify_paper_tick_events(since=tick_started_at, ...)` emits only for current-tick audit window. Reprocessing does NOT replay. Kill-switch flips/clears written between ticks land on the NEXT tick's notification. |
| 6g-L8 | § 5.3 | Replay is operator-triggered only via explicit CLI. No automatic replay fan-out. |
| 6g-L9 | § 3.3 | `TICK_REPROCESSED_COMPLETED` emits critical standalone push with `⚠️ Tick Reprocessed — YYYY-MM-DD`. |
| 6g-L10 | § 4.4 | Mixed Chinese labels + English identifiers. Fixed emoji prefixes: 🛑 critical, ⚠️ warning, ✅ recovery, 📊 routine summary. |
| 6g-L11 | § 4.4 | MVP sends `url=None`. Deep links deferred to 6f integration. |
| 6g-L12 | § 4.4 | Routine summary compact + section-skipping. Empty sections omitted; truncation reuses `push.py`; money/prices rendered with sign + 2 decimals. |
| 6g-L13 | § 2.2 | `observability/` is consumer; `alerts/` is transport-only. Dependency direction: `observability → alerts`. |
| 6g-L14 | § 6.6 | `notify_paper_tick_events` is best-effort. Failures/exceptions captured in `NotificationResult`; never propagate. Per-send timeout enforced by transport layer (existing httpx timeout=10s); translator does not retry on timeout. |
| 6g-L15 | § 6.6 | Independent enable flag `MP_PAPER_NOTIFICATIONS_ENABLED`. Disabled = no sends; projection still testable. |
| 6g-L16 | § 8.1 | Tests layered: pure projection, templates, notifier entrypoint with seeded DB, one E2E scheduler test. Projection and template tests stay separate. |
| 6g-L17 | § 6.6 | Recovery detection uses repository batch helper `positions_with_prior_price_unavailable(position_ids)`, not per-position SQL. |
| 6g-L18 | § 5.3 | `republish_cli` refuses to run when `MP_PAPER_NOTIFICATIONS_ENABLED=false`; exit code 1. |
| 6g-L19 | § 2.2 | Audit row filter applies `context["tick_date"]` match **conditionally**: required for TICK_COMPLETED / TICK_REPROCESSED_COMPLETED / KILL_SWITCH_CYCLE_SKIPPED; admitted on time window alone for all others (incl. ENGINE_INVARIANT_ERROR). |
| 6g-L20 | § 5.3 | KILL_SWITCH_FLIPPED uses the extended between-tick window `[latest_tick_completed_at, notify_started_at]` so externally-triggered flips are picked up on the next tick. All other event types use `[since=tick_started_at, notify_started_at]`. |
| 6g-L21 | § 6.3 | `summarize_tick` reads `cycle_status` from TICK_COMPLETED (priority) → KILL_SWITCH_CYCLE_SKIPPED → "unknown" + failure record. Cash balance and active positions are pulled from canonical tables, NOT audit context. |
