# Phase 6b+ — Paper P&L Realization: Design

**Status:** Brainstorm complete · ready for implementation plan
**Author:** brainstorm 2026-05-22
**Spec-type:** Backend infrastructure (Phase 6b post-fix / Phase 6c pre-cursor)
**Umbrella:** `docs/superpowers/specs/2026-05-21-phase-6-umbrella-design.md`
**Related:**
- 6a foundation: `docs/superpowers/specs/2026-05-21-phase-6a-paper-trading-foundation-design.md`
- 6b risk gates: `docs/superpowers/specs/2026-05-21-phase-6b-risk-gates-design.md`
- **Scope:** Replace `StubPriceProvider(default=Decimal("0"))` with `YFinancePriceProvider`. Move PriceProvider injection from order-placement time (`daily_cycle.run`, broken — future date impossible) to exit-materialization time (`ForwardExecutionEngine._materialize_exit`, correct physical semantics).

---

## 1 — Goal & Boundary

### Goal

Phase 6a shipped `StubPriceProvider(default=Decimal("0"))` for production paper trading. Every exit fills at `Decimal("0")`, so `realized_pnl = (0 - entry_price) * quantity` — catastrophically wrong. This spec replaces the stub with a real yfinance-backed provider AND fixes the underlying design bug: the PriceProvider was being called at order-**placement** time with a **future** `horizon_date`, which is physically impossible to satisfy. By the time `_materialize_exit` actually runs (at or after `horizon_date`), the historical close exists and yfinance can return it.

### Core principle (locked)

**"Fetch exit close at exit time, not at placement time."** The horizon close price is a historical fact at the moment we need it. PriceProvider injection moves from `daily_cycle.run(..., price_provider=...)` to `ForwardExecutionEngine(..., price_provider=...)`. Forward-mode `OrderRequest.horizon_price=None` is legal.

### What 6b+ explicitly does NOT include

- ❌ **No expected-vs-actual reconciliation** (deferred to Phase 6e ShadowPoolOptimizer)
- ❌ **No NAV snapshot / daily MtM** (Phase 6c)
- ❌ **No push notifications / "OPEN past horizon by N days" alerts** (Phase 6g; queryable via SQL on `paper_audit_event.context` today)
- ❌ **No schema rename** of `paper_order.horizon_price` column (keep nullable, treat as legacy/forecast field — Phase 7 schema cleanup can rename)
- ❌ **No Phase 5 backtest changes** (backtest has its own `marketpulse/backtest/correlation.PriceProvider` Protocol with different methods — completely decoupled)
- ❌ **No price cache layer** (yfinance has internal cache; per-tick API call count is ~20; YAGNI)

### Anti-goals

- ❌ Compute exit P&L from `paper_order.horizon_price` — must always use `paper_fill WHERE side='EXIT'`.
- ❌ Treat `PRICE_UNAVAILABLE` as an `InvariantError` — it's a transient data gap, not a code bug.
- ❌ Hardcode `"yfinance"` or `LOOKBACK_DAYS` in `ForwardExecutionEngine` — read from provider properties.
- ❌ Default-fall-through silent fallback (no `StubPriceProvider(default=...)`).
- ❌ Use `paper_order.horizon_price` in any forward-mode code path.

---

## 2 — Architecture

### Module layout

```
marketpulse/data/yfinance_client.py             (MODIFIED — extension only)
    + fetch_close_on_date(ticker, on_date, *, lookback_days=10) -> Bar | None
      Calls yf.Ticker(ticker).history(start=on_date - lookback,
                                       end=on_date + timedelta(days=1))
      end=on_date+1 because yfinance end is EXCLUSIVE (lock 6b+L5).
      Returns the bar with max date <= on_date, or None if window empty.
      Decorated with existing @_retry (lock 6b+L8 inherits retry behavior).

marketpulse/trading/price_provider.py           (REWRITTEN)
    @dataclass(frozen=True)
    class ClosePrice:
        price: Decimal
        price_date: date          # actual yfinance bar date (may differ from
                                  # requested_date when roll-back happened)
        requested_date: date      # = horizon_date the engine asked for
        source: str               # filled by provider (lock 6b+L8)

    class PriceProvider(Protocol):
        source: str               # property — for audit provenance
        lookback_days: int        # property — for audit provenance
        def close_on_date(self, *, ticker: str, on_date: date) -> ClosePrice | None: ...

    LOOKBACK_DAYS = 10            # module constant (covers any US holiday cluster)

    class YFinancePriceProvider:
        source = "yfinance"
        def __init__(self, *, client: YFinanceClient,
                     lookback_days: int = LOOKBACK_DAYS) -> None: ...
        @property
        def lookback_days(self) -> int: ...

    class StubPriceProvider:      # tests only
        source = "stub"
        lookback_days = 0
        def __init__(self, *,
                     map: dict[tuple[str, date], ClosePrice] | None = None,
                     ) -> None: ...
        # NO default kwarg (lock 6b+L3); miss returns None

marketpulse/trading/forward_engine.py           (MODIFIED — major)
    + __init__ gains required `price_provider: PriceProvider` kwarg
      (lock 6b+L2; no default value)
    + _materialize_exit -> bool      (lock 6b+L7)
      True = CLOSED, False = PRICE_UNAVAILABLE (position stays OPEN)
      No InvariantError on missing price (data gap, not code bug)
    + tick() uses return value for exits_materialized / pu count

marketpulse/trading/daily_cycle.py              (MODIFIED — simplify)
    - run(..., price_provider=...) kwarg REMOVED (breaking)
    - _make_order_request no longer calls price_provider
    - OrderRequest.horizon_price = None is the only forward-mode value

marketpulse/trading/types.py                    (MODIFIED — minor)
    + AuditEventType.PRICE_UNAVAILABLE = "PRICE_UNAVAILABLE"

marketpulse/trading/repository.py               (MODIFIED — extension only)
    + count_price_unavailable_attempts(*, position_id: int) -> int
      Uses json_extract(context, '$.position_id') (lock 6b+L9 wrapper-only)
    + find_paper_position_by_id(position_id: int) -> PaperPosition | None
      (NOT used by _materialize_exit per lock 6b+L7, but useful for tests)

marketpulse/scheduler/paper_trading_tick.py     (MODIFIED — DI rewire)
    - StubPriceProvider(default=Decimal("0"))
    + YFinancePriceProvider(client=YFinanceClient())
    price_provider now injected into ForwardExecutionEngine
    (NOT daily_cycle.run — that kwarg is removed)

marketpulse/backtest/allocation.py              (MODIFIED — comment only)
    AllocationWinner.horizon_price docstring updated to clarify:
    Phase 5 backtest fills from history; Phase 6+ forward leaves None.

alembic/versions/0011_audit_check_price_unavailable.py    (NEW)
    SQLite table rebuild adding 'PRICE_UNAVAILABLE' to
    paper_audit_event.event_type CHECK constraint.
    Schema columns + defaults + indexes match 0010 EXACTLY
    (lock 6b+L10). Alembic env owns transactions; no manual BEGIN/COMMIT.
    downgrade() is executable: count PRICE_UNAVAILABLE rows; >0 raise;
    ==0 rebuild back to 0010 CHECK.
```

### Data flow

**Before (broken — current 6a/6b production):**

```
Day D 17:30 NY:
  daily_cycle.run(price_provider=StubPriceProvider(default=0))
    → for winner in winners:
        horizon_price = price_provider.horizon_price(
            ticker=winner.ticker,
            horizon_date=D+5,    # ← future date; physically impossible
        )                         #    stub returns Decimal("0")
        OrderRequest(horizon_price=Decimal("0"))
        engine.place_order(req)   # paper_order.horizon_price=0 stored
    → engine.tick(as_of=D):
        _materialize_exit(position):
          exit_price = order.horizon_price   # ← still 0
          realized_pnl = (0 - entry_price) * qty   # ← garbage
```

**After (correct — 6b+):**

```
Day D 17:30 NY:
  daily_cycle.run(...)             # no price_provider kwarg
    → for winner in winners:
        OrderRequest(horizon_price=None)        # forward mode invariant
        engine.place_order(req)                 # paper_order.horizon_price=NULL
    → engine.tick(as_of=D):
        for position in find_positions_for_exit(as_of=D):
          closed = _materialize_exit(position, exit_date=D)
          if closed: exits_materialized += 1
          else:      price_unavailable_count += 1   # not an error!

      _materialize_exit(position, exit_date):
        close = price_provider.close_on_date(
            ticker=position.ticker,
            on_date=position.horizon_date,        # ← past date; yfinance has it
        )
        if close is None:
            attempts = repo.count_price_unavailable_attempts(
                position_id=position.id,
            )
            write_audit(
                PRICE_UNAVAILABLE,
                order_id=position.order_id,      # lock 6b+L4
                context={
                    "position_id": position.id,
                    "ticker": position.ticker,
                    "horizon_date": position.horizon_date.isoformat(),
                    "as_of": exit_date.isoformat(),
                    "lookback_days": price_provider.lookback_days,  # provider-driven
                    "source": price_provider.source,                # provider-driven
                    "attempt_count": attempts + 1,
                },
            )
            return False   # position stays OPEN; next tick retries

        exit_price = close.price                  # canonical P&L source
        insert_paper_fill(side='EXIT', price=exit_price, ...)   # lock 6b+L1
        realized_pnl = (exit_price - position.entry_price) * qty
        write_audit(
            POSITION_CLOSED,
            context={
                ...existing 6a fields...,
                "requested_horizon_date": position.horizon_date.isoformat(),
                "actual_price_date": close.price_date.isoformat(),
                "price_source": close.source,
                "roll_policy": "exact_match" if equal else "previous_available_close",
            },
        )
        return True
```

### DI seam

```python
# marketpulse/scheduler/paper_trading_tick.py (modified)
from marketpulse.data.yfinance_client import YFinanceClient
from marketpulse.trading.price_provider import YFinancePriceProvider

yf_client = YFinanceClient()
price_provider = YFinancePriceProvider(client=yf_client)

engine = ForwardExecutionEngine(
    repository=repository,
    clock=clock,
    kill_switch=kill_switch,
    risk_gate=risk_gate,
    price_provider=price_provider,   # NEW required kwarg (lock 6b+L2)
)
result = daily_cycle.run(
    clock=clock, engine=engine, repository=repository,
    bid_aggregator=bid_aggregator, allocator=allocate_for_day,
    calendar=calendar, kill_switch=kill_switch,
    # price_provider kwarg REMOVED
    daily_curves={},
    daily_strategy_contribution_returns={},
    daily_pool_returns=[],
    sector_provider=get_sector,
)
```

### Non-session `horizon_date` handling — roll-back semantics

`YFinanceClient.fetch_close_on_date` queries `[on_date - lookback_days, on_date + 1)` and returns the **most recent bar** within that window. By yfinance's natural behavior on non-trading days (no row exists), this rolls back to the previous trading day automatically.

`YFinancePriceProvider.close_on_date` translates `Bar` → `ClosePrice`. If `bar.date < on_date`, the audit (and 6f UI later) sees `roll_policy="previous_available_close"`.

If 10 calendar days back has no bar (unprecedented multi-day market closure, or a freshly-listed ticker), `fetch_close_on_date` returns `None` → `YFinancePriceProvider.close_on_date` returns `None` → `_materialize_exit` writes `PRICE_UNAVAILABLE` and returns `False`.

### Audit reuse — `POSITION_CLOSED` extended; `PRICE_UNAVAILABLE` is new

**`POSITION_CLOSED`** (6a + 6b+ provenance):
```json
{
  "event_type": "POSITION_CLOSED",
  "order_id": 456,
  "context": {
    "position_id": 123,
    "exit_price": "155.25",
    "realized_pnl": "52.50",
    "cash_balance_after": "10052.50",
    "requested_horizon_date": "2026-05-26",
    "actual_price_date": "2026-05-22",
    "price_source": "yfinance",
    "roll_policy": "previous_available_close"
  }
}
```

**`PRICE_UNAVAILABLE`** (NEW):
```json
{
  "event_type": "PRICE_UNAVAILABLE",
  "order_id": 456,
  "strategy": "momentum_breakout",
  "reason": "close_on_date_returned_none",
  "context": {
    "position_id": 123,
    "ticker": "AAPL",
    "horizon_date": "2026-05-26",
    "as_of": "2026-05-27",
    "lookback_days": 10,
    "source": "yfinance",
    "attempt_count": 3
  }
}
```

Operators query stuck positions with:

```sql
SELECT json_extract(context, '$.position_id') AS pid,
       json_extract(context, '$.ticker') AS ticker,
       json_extract(context, '$.attempt_count') AS attempts,
       timestamp
FROM paper_audit_event
WHERE event_type = 'PRICE_UNAVAILABLE'
  AND json_extract(context, '$.attempt_count') >= 5
ORDER BY timestamp DESC;
```

---

## 3 — Exit Materialization Semantics

### `_materialize_exit` signature change

```python
def _materialize_exit(self, position, *, exit_date: date) -> bool:
    """Lock 6b+L7: True = CLOSED; False = PRICE_UNAVAILABLE (position
    stays OPEN, next tick retries). NEVER raises InvariantError on
    missing price (transient data gap, not invariant violation)."""
```

### `tick()` accounting

```python
def tick(self, *, as_of: date) -> TickResult:
    # ... (entry materialization unchanged) ...

    exits_materialized = 0
    price_unavailable_count = 0
    errors: list[TickError] = []

    for position in self._repo.find_positions_for_exit(as_of=as_of):
        try:
            closed = self._materialize_exit(position, exit_date=as_of)
            if closed:
                exits_materialized += 1
            else:
                price_unavailable_count += 1   # NOT an error
        except InvariantError as e:
            # Other invariant violations (NOT price issues) — 6a behavior preserved
            errors.append(TickError(...))

    return TickResult(
        as_of=as_of,
        entries_materialized=entries_materialized,
        exits_materialized=exits_materialized,
        errors=tuple(errors),
        # NOTE: price_unavailable_count NOT in TickResult (public interface
        # unchanged); flows into TICK_COMPLETED.context via daily_cycle
    )
```

`price_unavailable_count` is passed back to `daily_cycle.run` via a private internal channel (in-process attribute on engine, OR engine.tick returns an extended internal tuple — implementation choice for T7b). It lands in `TICK_COMPLETED.context["price_unavailable_count"]`.

### TickResult invariant (lock 6b+L7)

**`tick_result.errors` MUST be `()` even when many positions hit `PRICE_UNAVAILABLE`.** The errors tuple is reserved for `InvariantError` events. Data-gap retries are not errors — they are normal flow.

---

## 4 — Configuration + Migration

### `LOOKBACK_DAYS = 10` (module constant)

In `marketpulse/trading/price_provider.py`:

```python
LOOKBACK_DAYS = 10  # calendar days; covers any US holiday cluster
                    # (Thanksgiving + adjacent weekend ≈ 5 non-trading days;
                    # 10-day window adds safety margin)
```

Not configurable via YAML or env. If a deployment ever needs to override:
1. Modify the constant + redeploy, OR
2. Inject a custom `YFinancePriceProvider(client, lookback_days=...)` at the DI seam.

Tests inject custom values via the kwarg path. YAGNI for runtime config.

### Alembic 0011 migration

```python
# alembic/versions/0011_audit_check_price_unavailable.py
"""Phase 6b+: extend paper_audit_event CHECK to allow PRICE_UNAVAILABLE.

Revision ID: 0011
Revises: 0010
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


# 12 6a event types + the new 6b+ one.
_ALLOWED_6A = (
    "ORDER_PLACED", "ORDER_PLACED_DUPLICATE", "ORDER_REJECTED",
    "ORDER_CANCELLED", "ORDER_ENTRY_FILLED", "POSITION_CLOSED",
    "KILL_SWITCH_FLIPPED", "KILL_SWITCH_CYCLE_SKIPPED",
    "TICK_COMPLETED", "TICK_REPROCESSED_COMPLETED",
    "SCHEDULER_GAP_DETECTED", "ENGINE_INVARIANT_ERROR",
)
_ALLOWED_6B_PLUS = ("PRICE_UNAVAILABLE",)


def _check_clause(types: tuple[str, ...]) -> str:
    return "event_type IN (" + ", ".join(f"'{t}'" for t in types) + ")"


def upgrade() -> None:
    """Rebuild paper_audit_event with extended CHECK. Lock 6b+L10:
    column definitions, defaults, and index names MUST match 0010 exactly.

    Alembic env manages the transaction — do NOT BEGIN/COMMIT manually."""
    new_check = _check_clause(_ALLOWED_6A + _ALLOWED_6B_PLUS)

    # Implementer: read 0010 first to copy EXACT column types/defaults
    # and the 4 index names (ix_paper_audit_ts, ix_paper_audit_type_ts,
    # ix_paper_audit_order, ix_paper_audit_strategy_ts).
    op.execute(f"""
        CREATE TABLE paper_audit_event_new (
            -- columns identical to 0010
            ...
            CONSTRAINT ck_paper_audit_event_type CHECK ({new_check})
        )
    """)
    op.execute("""
        INSERT INTO paper_audit_event_new SELECT * FROM paper_audit_event
    """)
    op.execute("DROP TABLE paper_audit_event")
    op.execute("ALTER TABLE paper_audit_event_new RENAME TO paper_audit_event")
    # Recreate all 4 indexes with their 0010 names (lock 6b+L10).
    op.execute("CREATE INDEX ix_paper_audit_ts ON paper_audit_event (timestamp)")
    op.execute("CREATE INDEX ix_paper_audit_type_ts ON paper_audit_event (event_type, timestamp)")
    op.execute("CREATE INDEX ix_paper_audit_order ON paper_audit_event (order_id)")
    op.execute("CREATE INDEX ix_paper_audit_strategy_ts ON paper_audit_event (strategy, timestamp)")


def downgrade() -> None:
    """Rebuild with 0010 CHECK. Lock 6b+L10: PRICE_UNAVAILABLE rows
    would violate the old CHECK — refuse downgrade if any exist."""
    conn = op.get_bind()
    count = conn.execute(
        text("SELECT COUNT(*) FROM paper_audit_event "
             "WHERE event_type = 'PRICE_UNAVAILABLE'")
    ).scalar() or 0
    if count > 0:
        raise RuntimeError(
            f"Cannot downgrade 0011 → 0010: {count} PRICE_UNAVAILABLE row(s) "
            "would violate the 0010 CHECK constraint. Delete them first or "
            "implement a manual data-loss-acceptable rollback."
        )
    old_check = _check_clause(_ALLOWED_6A)
    # Same rebuild pattern as upgrade, with old CHECK clause.
    op.execute(f"""
        CREATE TABLE paper_audit_event_new (
            ...
            CONSTRAINT ck_paper_audit_event_type CHECK ({old_check})
        )
    """)
    op.execute("INSERT INTO paper_audit_event_new SELECT * FROM paper_audit_event")
    op.execute("DROP TABLE paper_audit_event")
    op.execute("ALTER TABLE paper_audit_event_new RENAME TO paper_audit_event")
    # Recreate 4 indexes (same as upgrade).
    op.execute("CREATE INDEX ix_paper_audit_ts ON paper_audit_event (timestamp)")
    op.execute("CREATE INDEX ix_paper_audit_type_ts ON paper_audit_event (event_type, timestamp)")
    op.execute("CREATE INDEX ix_paper_audit_order ON paper_audit_event (order_id)")
    op.execute("CREATE INDEX ix_paper_audit_strategy_ts ON paper_audit_event (strategy, timestamp)")
```

### Deployment

1. **Code + migration ship together** (single image, single PR).
2. Container restart triggers `alembic upgrade head` (already in startup hook) → 0010 → 0011.
3. First post-deploy 17:30 NY tick uses `YFinancePriceProvider`.

### Rollback strategy

Hard. No clean automatic rollback once any `PRICE_UNAVAILABLE` audit row exists. Options if production fails:

1. **Hotfix DI swap** (fastest): redeploy with `StubPriceProvider(map={})` → all exits PRICE_UNAVAILABLE → all positions stuck OPEN → operator manually closes via SQL.
2. **Full rollback**: `DELETE FROM paper_audit_event WHERE event_type='PRICE_UNAVAILABLE'`, then `alembic downgrade 0010`, then redeploy 6a image. Data-loss-accepting; not recommended unless catastrophic.

The first PRICE_UNAVAILABLE row makes #2 hard, so prefer #1 if anything goes wrong.

---

## 5 — Sub-task Decomposition

12 tasks. Single PR `feat(phase-6b-plus): paper P&L realization`. Branch `plan/phase-6b-plus-paper-pnl-realization` off `main`. T7 splits into two commits (T7a constructor + DI, T7b exit materialization) for safer review.

| # | Sub-task | Files | Tests at boundary |
|---|---|---|---|
| **T1** | `AuditEventType.PRICE_UNAVAILABLE` added | `marketpulse/trading/types.py` | Enum value check |
| **T2** | Alembic 0011 migration | `alembic/versions/0011_*.py`, `tests/migration/test_0011_*.py` | Upgrade succeeds + inserts PRICE_UNAVAILABLE; downgrade refuses if rows exist; index names per lock 6b+L10 |
| **T3** | `PriceProvider` Protocol + `ClosePrice` dataclass + `StubPriceProvider` rewrite (no default, source/lookback_days properties) | `marketpulse/trading/price_provider.py`, `tests/trading/test_price_provider.py` | StubPriceProvider rejects default kwarg; map-only lookup; ClosePrice frozen |
| **T4** | `YFinanceClient.fetch_close_on_date(ticker, on_date, lookback_days=10) -> Bar \| None` | `marketpulse/data/yfinance_client.py`, `tests/data/test_yfinance_close_on_date.py` | end=on_date+1 (lock 6b+L5); roll-back returns prior session bar; window-empty returns None |
| **T5** | `YFinancePriceProvider` with `source="yfinance"` + `lookback_days` property + `close_on_date` impl | `marketpulse/trading/price_provider.py`, `tests/trading/test_yfinance_price_provider.py` | Bar → ClosePrice translation; source/lookback_days propagate |
| **T6** | `Repository.count_price_unavailable_attempts(*, position_id)` using `json_extract(context, '$.position_id')` per lock 6b+L9 | `marketpulse/trading/repository.py`, `tests/trading/test_repository_price_unavailable.py` | Returns 0 initially; 1, 2, 3 after consecutive failures (op-test #4) |
| **T7a** | `ForwardExecutionEngine.__init__(price_provider=...)` required kwarg added (lock 6b+L2). Existing tests updated to pass `StubPriceProvider(map={...})`. **No** semantic changes to `_materialize_exit` yet. | `marketpulse/trading/forward_engine.py`, `tests/trading/test_forward_engine.py` | Constructor TypeError on missing price_provider; existing 6a tests pass with new arg |
| **T7b** | `_materialize_exit -> bool` rewrite: PRICE_UNAVAILABLE path + POSITION_CLOSED provenance + tick() bool accounting + price_unavailable_count flows into TICK_COMPLETED context | `marketpulse/trading/forward_engine.py`, `marketpulse/trading/daily_cycle.py`, `tests/trading/test_forward_engine.py`, `tests/trading/test_daily_cycle.py` | Op-tests #1, #2, #3, #5; tick_result.errors == () even with PRICE_UNAVAILABLE |
| **T8** | `daily_cycle.run(price_provider=...)` kwarg removed; `_make_order_request` no longer fetches; `OrderRequest.horizon_price=None` always in forward mode; `paper_order.horizon_price IS NULL` after forward place_order (test guards lock 6b+L1) | `marketpulse/trading/daily_cycle.py`, `tests/trading/test_daily_cycle.py` | `daily_cycle.run` raises TypeError if price_provider passed; new paper_order rows have horizon_price=NULL |
| **T9** | `paper_trading_tick.py` DI rewire: `YFinancePriceProvider(client=YFinanceClient())` injected into engine; daily_cycle.run no longer receives price_provider | `marketpulse/scheduler/paper_trading_tick.py`, `tests/trading/test_scheduler.py` | Scheduler smoke test asserts `isinstance(engine._price_provider, YFinancePriceProvider)` |
| **T10** | E2E tests: roll-back (weekend horizon), PRICE_UNAVAILABLE retry-and-succeed, attempt_count progression, P&L from paper_fill not order.horizon_price | `tests/trading/test_e2e_stateful.py` | Op-tests #2, #5, #18, #19 |
| **T11** | Final integration: `uv run pytest -q` (expect ~1130 total), `uv run ruff check`, `uv run alembic heads` (expect 0011), import smoke; squash + merge | — | All 19 op-tests pass |

---

## 6 — Locks (6b+ local)

| # | Lock |
|---|---|
| **6b+L1** | **Canonical exit P&L source is `paper_fill.price WHERE side='EXIT'`.** Code MUST NOT read `paper_order.horizon_price` for P&L computation. `paper_order.horizon_price` is a legacy/forecast field (nullable, 6b+ forward path writes NULL; Phase 5 backtest still writes historical). Op-test #18 enforces. |
| **6b+L2** | **`ForwardExecutionEngine.__init__(price_provider=...)` is REQUIRED kwarg.** No default. Production must explicitly inject `YFinancePriceProvider`. Op-test #12 enforces (TypeError on missing). |
| **6b+L3** | **`StubPriceProvider` has NO `default` parameter.** Constructor signature: `StubPriceProvider(*, map=None)`. Miss returns None (triggers PRICE_UNAVAILABLE downstream). Op-test #13 enforces. |
| **6b+L4** | **`PRICE_UNAVAILABLE` audit row's `order_id` field = `position.order_id`** (reuses existing `INDEX(order_id)`). `position_id` lives in `context`. |
| **6b+L5** | **`fetch_close_on_date` uses `end=on_date + timedelta(days=1)`** because yfinance's `history(start, end)` end is **exclusive**. Op-test #7 specifically pins this off-by-one. |
| **6b+L6** | **Alembic 0011 uses SQLite table rebuild** (recreate + INSERT-SELECT + DROP + RENAME), NOT `ALTER ... CHECK` (SQLite doesn't support it). Pattern matches 0010 idiom. |
| **6b+L7** | **`_materialize_exit(...) -> bool`.** True = CLOSED, False = PRICE_UNAVAILABLE (position stays OPEN). `tick()` accounting uses the return value directly — does NOT re-query DB. False does NOT count as `TickError`; `tick_result.errors` stays `()` when only PRICE_UNAVAILABLE happens. |
| **6b+L8** | **`PriceProvider` Protocol exposes `source: str` and `lookback_days: int` properties.** `_materialize_exit` reads `self._price_provider.source` and `self._price_provider.lookback_days` when writing PRICE_UNAVAILABLE audit. NEVER hardcoded. Op-tests #14, #15 verify provenance correctness when provider swapped. |
| **6b+L9** | **`Repository.count_price_unavailable_attempts(*, position_id)` matches via `json_extract(context, '$.position_id') = ?`** (Phase 7-safe — doesn't rely on `order ↔ position 1:1`). External code NEVER writes inline `json_extract` for this. Wrapper-only. Op-test #4 enforces attempt_count progression (1, 2, 3). |
| **6b+L10** | **Alembic 0011 table rebuild MUST preserve `paper_audit_event` column definitions, defaults, and index names (`ix_paper_audit_ts` / `ix_paper_audit_type_ts` / `ix_paper_audit_order` / `ix_paper_audit_strategy_ts`) EXACTLY from 0010.** Only the CHECK constraint changes. Alembic env owns the transaction — no manual `BEGIN`/`COMMIT`. `downgrade()` is executable: count PRICE_UNAVAILABLE rows > 0 → raise; else rebuild back to 0010 CHECK. |

---

## 7 — Operational Test Map

| # | Scenario | Locks |
|---|---|---|
| 1 | Happy path exact-match: horizon = session day, yfinance has it → POSITION_CLOSED, `roll_policy="exact_match"`, `actual_price_date == requested_horizon_date` | — |
| 2 | Roll-back: horizon = Saturday or US holiday → `actual_price_date < requested_horizon_date`, `roll_policy="previous_available_close"` | — |
| 3 | Single PRICE_UNAVAILABLE: `close_on_date` returns None → audit written, position stays OPEN, `tick.exits_materialized == 0`, `tick.errors == ()` | 6b+L4, 6b+L7 |
| 4 | Attempt progression: 3 consecutive ticks all fail → 3 audit rows with `attempt_count` = 1, 2, 3 | 6b+L9 |
| 5 | Retry success: tick 1 PRICE_UNAVAILABLE, tick 2 succeeds → 1 PRICE_UNAVAILABLE + 1 POSITION_CLOSED, position CLOSED at tick 2 | 6b+L7 |
| 6 | Lookback boundary: query window `[on_date - 10 calendar days, on_date]` has no bar → None → PRICE_UNAVAILABLE | 6b+L5 |
| 7 | Yfinance end-exclusive off-by-one: query for `on_date=today`, asserts `end` arg passed to yfinance is `today + 1 day` (else last bar missed) | 6b+L5 |
| 8 | Audit provenance: POSITION_CLOSED contains 4 new fields (`requested_horizon_date`, `actual_price_date`, `price_source`, `roll_policy`) | — |
| 9 | P&L invariant: `realized_pnl == (exit_price - entry_price) * quantity` where `exit_price` = `paper_fill.price WHERE side='EXIT'` (NOT order.horizon_price) | 6b+L1 |
| 10 | Cash ledger: `cash_balance` increases by `exit_price * quantity` exactly | — |
| 11 | `daily_cycle.run` signature: passing `price_provider` raises `TypeError("unexpected keyword argument")` | — |
| 12 | `ForwardExecutionEngine.__init__`: missing `price_provider` raises `TypeError` | 6b+L2 |
| 13 | `StubPriceProvider.__init__`: passing `default=...` raises `TypeError` | 6b+L3 |
| 14 | Provider `source` provenance: inject custom provider with `source="custom"` → PRICE_UNAVAILABLE audit context has `source="custom"` | 6b+L8 |
| 15 | Provider `lookback_days` provenance: inject custom provider with `lookback_days=7` → audit has `lookback_days=7` | 6b+L8 |
| 16 | Alembic 0011 upgrade: post-upgrade, INSERT PRICE_UNAVAILABLE succeeds; pre-upgrade same INSERT fails CHECK | 6b+L6, 6b+L10 |
| 17 | Alembic 0011 downgrade: with 0 PRICE_UNAVAILABLE rows, downgrade succeeds; with ≥1 row, raises RuntimeError | 6b+L10 |
| 18 | `paper_order.horizon_price` NOT used for P&L: construct order with `paper_order.horizon_price=Decimal("0")` (legacy/test fixture) but provider returns `close.price=Decimal("155")` → `paper_fill.price == 155`, `realized_pnl = (155 - entry_price) * qty` | 6b+L1 |
| 19 | PRICE_UNAVAILABLE doesn't mutate state: after 3 consecutive PRICE_UNAVAILABLE for the same position, assert `position.status == "OPEN"`, no EXIT fill rows exist for that order, `cash_balance` unchanged from pre-tick | 6b+L7 |

---

## 8 — Forward-warnings (Phase 6c / 6e / 6f / 6g / 7)

### To 6c

- `DrawdownHaltGate` will need `paper_fill.realized_pnl` aggregations across all positions (canonical via lock 6b+L1).
- NAV snapshot job can use the same provider for daily MtM of open positions (call `close_on_date(ticker, on_date=today)` for each OPEN position).

### To 6e (ShadowOptimizer)

- If 6e wants expected-vs-actual exit price comparison: re-introduce a placement-time `expected_horizon_price` field (NOT the existing `paper_order.horizon_price` — give it a fresh name to avoid confusion). 6b+ deliberately did NOT do this (YAGNI).

### To 6f (UI)

- `/lab/paper-trading` per-position view shows `requested_horizon_date` vs `actual_price_date` from POSITION_CLOSED audit context (roll-back transparency).
- "Stuck positions" view: queries PRICE_UNAVAILABLE with `attempt_count >= 5`.

### To 6g (observability)

- Push notification on `PRICE_UNAVAILABLE.attempt_count >= 3` for the same position_id.
- Recap job tallies PRICE_UNAVAILABLE events per day; daily ops digest shows.

### To Phase 7 (broker integration)

- The PriceProvider Protocol generalizes to broker-quote APIs cleanly: `close_on_date` semantics map to "official close per the broker's record". Real-time execution uses a different (live quote) interface — out of 6b+ scope.
- Eventually rename `paper_order.horizon_price` → `expected_horizon_price` (or drop entirely) in a Phase 7 schema migration.

---

## 9 — Deliverables Summary

Single PR `feat(phase-6b-plus): paper P&L realization`. Branch `plan/phase-6b-plus-paper-pnl-realization` off `main`. Estimated ~10-12 commits (12 sub-tasks; T7 splits into T7a/T7b).

**New files (2 + 1 migration):**
- `alembic/versions/0011_audit_check_price_unavailable.py`
- `tests/trading/test_yfinance_price_provider.py`
- `tests/data/test_yfinance_close_on_date.py`
- `tests/trading/test_repository_price_unavailable.py`
- `tests/migration/test_0011_audit_check.py`

**Modified files (8):**
- `marketpulse/trading/types.py` — adds `PRICE_UNAVAILABLE` to `AuditEventType`
- `marketpulse/trading/price_provider.py` — rewritten: ClosePrice + Protocol with source/lookback_days + YFinancePriceProvider + StubPriceProvider (no default)
- `marketpulse/data/yfinance_client.py` — adds `fetch_close_on_date`
- `marketpulse/trading/repository.py` — adds `count_price_unavailable_attempts`
- `marketpulse/trading/forward_engine.py` — adds required `price_provider` kwarg; `_materialize_exit -> bool`; PRICE_UNAVAILABLE path; POSITION_CLOSED provenance
- `marketpulse/trading/daily_cycle.py` — removes `price_provider` kwarg; `_make_order_request` simplified; `OrderRequest.horizon_price = None` always
- `marketpulse/scheduler/paper_trading_tick.py` — DI rewire to YFinancePriceProvider
- `marketpulse/backtest/allocation.py` — comment-only update on `AllocationWinner.horizon_price`

**Modified test files (5):**
- `tests/trading/test_forward_engine.py` — engine constructor + exit semantics
- `tests/trading/test_daily_cycle.py` — signature change
- `tests/trading/test_e2e_stateful.py` — full PRICE_UNAVAILABLE + roll-back scenarios
- `tests/trading/test_scheduler.py` — DI swap
- `tests/trading/test_price_provider.py` (existing 6a Stub test) — updated to match new contract

**Phase 6a/6b regression contract:** all existing 1116+ tests continue to pass. The breaking changes (daily_cycle.run signature, StubPriceProvider constructor) update all in-tree call sites in the same PR.

---

**End of 6b+ spec.**
