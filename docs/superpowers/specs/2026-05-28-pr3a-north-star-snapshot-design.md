# PR3a — North-Star NAV Snapshot Design

> Charter top-3 priority #1, third installment (semantic layer). See `docs/CHARTER.md` § "Top 3 priorities" and PR1/PR2 specs.

## Purpose

Build the **source-of-truth** for the charter's north-star metric `paper_portfolio_excess_return_vs_spy_90d` and its three operational diagnostics. This PR ships:

1. A new immutable daily-snapshot table — `paper_nav_snapshot`.
2. Compute logic that turns paper-trading state into one row per trading day.
3. A scheduler hook that produces the row at end of `paper_trading_tick`.
4. Filled-in `north_star` and `diagnostics` sections in `/lab/charter-metrics` (replacing PR2's `"not_implemented"` placeholders).

The point is not visualization. The point is to make the charter's "is the system winning?" question answerable, auditable, and reproducible.

## Non-goals (deferred)

- **PR3b**: weekly `charter_review` markdown report into `/recaps/charter/YYYY-MM-DD.md`. PR3b is a *consumer* of PR3a's snapshot table; brainstormed separately.
- **Charter top-3 #2**: `/lab/portfolio-vs-spy` chart UI.
- Admin rebuild CLI / management tooling for `force_replace_snapshot`.
- SPY backfill or any change to `price_cache` semantics — PR3a only reads what's there.
- Alerts on `is_sufficient`, `is_stale`, drawdown breaches.
- Sparkline data API, CSV export, JSON streaming.
- Intraday recompute or live MTM polling.

## Scope locks (referenced throughout)

| Lock | Statement |
|---|---|
| L1 | Snapshots are immutable in normal flow. Insert-only via `insert_snapshot()`. The admin path is `force_replace_snapshot(reason)`. |
| L2 | One row per `trading_date` (primary key). |
| L3 | Snapshot generation piggybacks on `paper_trading_tick` (no standalone cron, avoids race against fill settlement). |
| L4 | Snapshot runner MUST NOT swallow persistence errors. The scheduler boundary catches and logs; failures remain visible. |
| L5 | No external network in the compute/persist path. SPY comes from `price_cache`. |
| L6 | Missing prices degrade *data quality*, never silently zero NAV. |
| L7 | Historical EOD position reconstruction uses time predicates (`opened_at <= EOD AND (closed_at IS NULL OR closed_at > EOD)`), not the live `status='OPEN'` column. |
| L8 | `compute_nav_snapshot()` is pure; `snapshot_runner` and `snapshot_repo` are not pure but do not depend on the network. |
| L9 | `charter_metrics.py` extensions are DB-backed contract builders, *not* pure. The label is honest. |
| L10 | `build_charter_metrics(session=None)` is a unit-test / CLI affordance only. Production route always passes the session. |
| L11 | Diagnostics window = the last 30 snapshot `trading_date` values (or all of them if fewer than 30 exist). Calendar dates are not used for windowing. |
| L12 | `ORDER_PLACED` and `ORDER_REJECTED` are mutually exclusive terminal decisions in MarketPulse's audit model. Rejection denominator = `PLACED + REJECTED`. If the audit model ever evolves to non-exclusive events, this metric must be revisited. |
| L13 | `paper_trade_count_30d` primary source = `paper_fill` rows with `position_id IS NOT NULL AND side='BUY' AND filled_at` in window. Audit-event count remains a diagnostic cross-check only. **`side='BUY'` assumes the current long-only paper engine.** If short positions are introduced, entry-fill detection must switch to position/opening-fill semantics rather than `side='BUY'` — this metric needs a spec revision at that point. |
| L14 | `quantity` is `Decimal` in the typed boundary (dataclass), even though the current paper engine writes integers. The portfolio layer must not pre-commit to integer-only quantities. |
| L15 | `unpriced_tickers` is dedup'd and sorted: `tuple(sorted({...}))`. `unpriced_positions_count` still counts lots. |
| L16 | **SPY benchmark anchor is established lazily by the first snapshot with a non-null `spy_close`.** All snapshots before that point have `anchor_spy_close=null`, `spy_index=null`, `excess_return=null`; their portfolio side (`portfolio_index`) is still authoritative. Once established, every later snapshot reads the anchor from `get_spy_anchor()` (earliest snapshot with a non-null `anchor_spy_close`). The asymmetry (portfolio anchors at row 1; SPY anchors at first SPY-available row) is intentional and documented; it means the SPY benchmark series may start later than the NAV series. |
| L17 | **JSON serialization rule:** the `/lab/charter-metrics` builder MUST convert all `Decimal` values to `float` for ratios / returns / index fields (`portfolio_index`, `spy_index`, `excess_return`, `value`, `coverage_ratio`). Money fields (`cash_balance`, `holdings_mtm`, `portfolio_nav`, `anchor_portfolio_nav`, `anchor_spy_close`, `spy_close`) are NOT exposed via JSON in PR3a — they remain `Decimal` in the DB and dataclass only. This avoids FastAPI's default Decimal-handling ambiguity. |
| L18 | **Empty cash ledger → runner raises.** If `paper_cash_ledger` has no row with `created_at <= EOD(trading_date)`, `run_nav_snapshot` raises `NoCashLedgerForDate` (not silently 0 — that would produce a misleading NAV). The scheduler's generic `except Exception` (L4) catches and logs; the tick is not aborted. |
| L19 | **Price source contract.** `price_lookup` reads `price_cache.close` directly as written by the existing price ingestion path. PR3a does **not** transform prices — no adjusted/unadjusted conversion, no split correction, no FX. Whatever `price_cache` stores is what NAV uses. If the project later changes `price_cache` semantics, the snapshot table inherits the new semantics from that day forward (earlier rows stay frozen per L1). |
| L20 | **`unpriced_tickers` text encoding.** DB column is comma-separated TEXT. Parsing: `None → ()`, `"" → ()`, `"QUBT,TQQQ" → ("QUBT", "TQQQ")`. Writing: `() → NULL`, non-empty tuple → `",".join(sorted(set(t)))`. Tickers MUST NOT contain commas; the existing ingestion path already enforces ticker grammar. |

## Architecture & Boundaries

```
                                  paper_trading_tick (existing)
                                          │
                                          ▼  (after fill settle)
                       ┌────────────── run_nav_snapshot(session, trading_date)
                       │                  │
                       │  cash_ledger     ▼  read forward state
                       │  paper_position  │
   marketpulse/        │  price_cache     │  build OpenPosition list
   portfolio/          │                  │  price_lookup callable
                       │                  ▼  spy_close from cache (None ok)
                       │           compute_nav_snapshot(...)  ◄── pure
                       │                  │
                       │                  ▼  NavSnapshot
                       └─── insert_snapshot(session, snapshot)
                                          │
                                          ▼
                                  paper_nav_snapshot table
                                          ▲
                                          │  read (never recompute)
        /lab/charter-metrics ───► build_north_star_section
                                  build_diagnostics_section
```

Layers:
- **pure**: `marketpulse/portfolio/north_star.py` — dataclasses, `compute_nav_snapshot`.
- **db**: `marketpulse/portfolio/snapshot_repo.py` — SQLAlchemy CRUD; rejects normal-flow updates.
- **orchestration**: `marketpulse/portfolio/snapshot_runner.py` — reads forward state, computes, persists.
- **web/contract**: `marketpulse/ops/charter_metrics.py` extended with two DB-backed builders.
- **infra**: Alembic `0012_paper_nav_snapshot`.

## Data Contract (extended `/lab/charter-metrics` v1)

PR2 reserved `north_star.status = "not_implemented"` and `diagnostics.status = "not_implemented"`. PR3a *replaces* these placeholders. Schema version stays at **1** (adding fields is non-breaking per PR2's lock policy).

```json
{
  "schema_version": 1,
  "timestamp": "2026-08-15T14:00:00+00:00",
  "operational_floor": { "backup": { ... unchanged ... } },
  "north_star": {
    "metric": "paper_portfolio_excess_return_vs_spy_90d",
    "as_of_trading_date": "2026-08-14",
    "value": 0.032,
    "portfolio_index": 1.041,
    "spy_index": 1.009,
    "trading_days_observed": 12,
    "trading_days_required": 90,
    "coverage_ratio": 0.133,
    "is_sufficient": false,
    "window_start": "2026-07-30",
    "window_end": "2026-08-14",
    "data_quality": {
      "unpriced_positions_count": 0,
      "unpriced_tickers": [],
      "is_complete": true
    },
    "error": null
  },
  "diagnostics": {
    "tick_success_rate_30d": {
      "value": 0.92,
      "observations": 14,
      "required_observations": 30,
      "coverage_ratio": 0.467,
      "is_sufficient": false
    },
    "order_rejection_rate_30d": {
      "value": 0.05,
      "observations": 21,
      "required_observations": 30,
      "coverage_ratio": 0.700,
      "is_sufficient": false
    },
    "paper_trade_count_30d": {
      "value": 21,
      "observations": 18,
      "required_observations": 30,
      "coverage_ratio": 0.600,
      "is_sufficient": false
    }
  }
}
```

### Field semantics

- `north_star.as_of_trading_date` = `trading_date` of the latest `paper_nav_snapshot` row.
- `value` = `excess_return` of that row.
- `portfolio_index`, `spy_index` = same row's index fields.
- `window_start` / `window_end` = earliest / latest snapshot date currently inside the metric window (NOT theoretical 90d calendar).
- `trading_days_observed` = snapshot row's stored value (running window count).
- `trading_days_required` = constant 90.
- `coverage_ratio` = `min(trading_days_observed / 90, 1)`.
- `is_sufficient` = `trading_days_observed >= 90`.
- `data_quality` = the row's `unpriced_positions_count` and `unpriced_tickers` propagated for visibility. Sorted, deduped.
- `error` = `null` in normal flow.

### Empty-snapshot fallback

```json
"north_star": {
  "metric": "paper_portfolio_excess_return_vs_spy_90d",
  "as_of_trading_date": null,
  "value": null,
  "portfolio_index": null,
  "spy_index": null,
  "trading_days_observed": 0,
  "trading_days_required": 90,
  "coverage_ratio": 0,
  "is_sufficient": false,
  "window_start": null,
  "window_end": null,
  "data_quality": {
    "unpriced_positions_count": 0,
    "unpriced_tickers": [],
    "is_complete": true
  },
  "error": "no_snapshots_yet"
}
```

### `session=None` fallback (test/CLI affordance)

Same shape as empty-snapshot fallback for both `north_star` and each diagnostic, with `error="db_session_unavailable"` set on `north_star`.

### Diagnostic-side empty fallback (audit table empty for a metric)

```json
"tick_success_rate_30d": {
  "value": null,
  "observations": 0,
  "required_observations": 30,
  "coverage_ratio": 0,
  "is_sufficient": false
}
```

### Diagnostic source-of-truth

| Metric | Numerator | Denominator | "observations" | "is_sufficient" basis |
|---|---|---|---|---|
| `tick_success_rate_30d` | `TICK_COMPLETED` count | `TICK_COMPLETED + ENGINE_INVARIANT_ERROR` | numerator + denominator events | `observations >= 30` |
| `order_rejection_rate_30d` | `ORDER_REJECTED` count | `ORDER_PLACED + ORDER_REJECTED` (mutually exclusive, see L12) | numerator + denominator events | `observations >= 30` |
| `paper_trade_count_30d` | `paper_fill` rows: `position_id IS NOT NULL AND side='BUY' AND filled_at` ∈ window (see L13) | n/a (count metric) | distinct snapshot trading_dates in window | `observations >= 30` |

`coverage_ratio = min(observations / required_observations, 1)` — computed server-side, never deferred to consumers.

## DB Schema — Alembic `0012_paper_nav_snapshot`

```sql
CREATE TABLE paper_nav_snapshot (
    trading_date           DATE PRIMARY KEY,
    cash_balance           NUMERIC NOT NULL,
    holdings_mtm           NUMERIC NOT NULL,
    portfolio_nav          NUMERIC NOT NULL,                           -- cash + holdings_mtm
    anchor_portfolio_nav   NUMERIC NOT NULL,                           -- locked at first snapshot
    portfolio_index        NUMERIC NOT NULL,                           -- portfolio_nav / anchor_portfolio_nav
    spy_close              NUMERIC,                                    -- nullable: SPY data may lag
    anchor_spy_close       NUMERIC,                                    -- nullable mirrors spy_close availability
    spy_index              NUMERIC,                                    -- spy_close / anchor_spy_close
    excess_return          NUMERIC,                                    -- portfolio_index - spy_index
    trading_days_observed  INTEGER NOT NULL,
    coverage_ratio         NUMERIC NOT NULL,
    is_sufficient          BOOLEAN NOT NULL,
    unpriced_positions_count INTEGER NOT NULL DEFAULT 0,
    unpriced_tickers       TEXT,                                       -- comma-separated, NULL when count=0
    created_at             TIMESTAMP NOT NULL,
    updated_at             TIMESTAMP NOT NULL,
    is_rebuilt             BOOLEAN NOT NULL DEFAULT 0,
    rebuild_reason         TEXT
);
```

- Forward migration creates the table.
- Downgrade drops the table.
- Defaults `unpriced_positions_count=0` and `is_rebuilt=0` survive future hand-inserts.

## Module Layout & Interfaces

### `marketpulse/portfolio/__init__.py` — empty module init.

### `marketpulse/portfolio/north_star.py` (pure)

```python
# Layer: pure
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable, Iterable

NORTH_STAR_WINDOW = 90

@dataclass(frozen=True)
class OpenPosition:
    ticker: str
    quantity: Decimal     # L14

@dataclass(frozen=True)
class NavSnapshot:
    trading_date: date
    cash_balance: Decimal
    holdings_mtm: Decimal
    portfolio_nav: Decimal
    anchor_portfolio_nav: Decimal
    portfolio_index: Decimal
    spy_close: Decimal | None
    anchor_spy_close: Decimal | None
    spy_index: Decimal | None
    excess_return: Decimal | None
    trading_days_observed: int
    coverage_ratio: Decimal
    is_sufficient: bool
    unpriced_positions_count: int
    unpriced_tickers: tuple[str, ...]   # dedup'd + sorted, L15

def compute_nav_snapshot(
    *,
    trading_date: date,
    cash_balance: Decimal,
    open_positions: Iterable[OpenPosition],
    price_lookup: Callable[[str], Decimal | None],
    spy_close: Decimal | None,
    anchor_portfolio_nav: Decimal,
    anchor_spy_close: Decimal | None,
    trading_days_observed: int,
    window_size: int = NORTH_STAR_WINDOW,
) -> NavSnapshot:
    """Build one snapshot. Missing price_lookup(ticker) → position is OMITTED
    from holdings_mtm (NOT zeroed) and the ticker recorded in unpriced_tickers."""
```

### `marketpulse/portfolio/snapshot_repo.py` (db)

```python
# Layer: db
class SnapshotAlreadyExists(Exception): ...

def insert_snapshot(session: Session, snapshot: NavSnapshot) -> None: ...
def force_replace_snapshot(
    session: Session, snapshot: NavSnapshot, *, reason: str,
) -> None: ...
def get_snapshot(session: Session, trading_date: date) -> NavSnapshot | None: ...
def get_latest_snapshot(session: Session) -> NavSnapshot | None: ...
def get_snapshot_series(
    session: Session, *, window_start: date, window_end: date,
) -> list[NavSnapshot]:
    """Inclusive range, ordered by trading_date ascending."""
def get_recent_snapshot_dates(
    session: Session, *, limit: int,
) -> list[date]:
    """Most-recent N trading_dates, returned in ASCENDING order."""
def get_spy_anchor(session: Session) -> Decimal | None:
    """Earliest non-null anchor_spy_close in the snapshot table.
    Used by snapshot_runner to enforce L16 (lazy SPY anchor)."""
def count_snapshots_in_window(
    session: Session, *, window_end: date, window_size: int,
) -> int:
    """Count of most-recent snapshot rows with trading_date <= window_end,
    capped at window_size. Trading-day semantics (L11) — NEVER calendar:
    if 200 snapshots exist and window_size=90, returns 90, regardless of
    calendar gap from earliest snapshot to window_end."""
```

### `marketpulse/portfolio/snapshot_runner.py` (orchestration)

```python
# Layer: orchestration
def run_nav_snapshot(
    session: Session, *, trading_date: date, settings: Settings,
) -> NavSnapshot:
    """Read forward state, compute, persist. Called at EOD inside
    paper_trading_tick. Idempotent on PK conflict: existing row is returned
    + a warning is logged. ALL OTHER persistence errors propagate (see L4)."""
```

Flow:
1. `cash_balance`: latest `paper_cash_ledger` row with `created_at <= EOD(trading_date)`.
2. `open_positions`: historical-safe SQL (L7).
3. `price_lookup`: `price_cache` rows for `trading_date`, returning `Decimal | None`.
4. `spy_close`: `price_cache` row for `('SPY', trading_date)`. None if missing (L5).
5. Anchors:
   - **Portfolio**: earliest snapshot's `portfolio_nav`, or self-anchor on first run.
   - **SPY (L16)**: earliest snapshot with a non-null `anchor_spy_close` via `snapshot_repo.get_spy_anchor()`. If no prior SPY anchor exists AND current `spy_close` is non-null, current `spy_close` becomes the anchor. If `spy_close` is None AND no prior anchor exists, `anchor_spy_close=None`, `spy_index=None`, `excess_return=None`. Once a snapshot writes a non-null `anchor_spy_close`, it is referenced by all future snapshots — earlier null-anchor rows stay frozen (L1 immutability).
6. `trading_days_observed = count_snapshots_in_window(window_end, 90) + 1`.
7. `compute_nav_snapshot(...)`.
8. `insert_snapshot(session, snapshot)` → on `SnapshotAlreadyExists` return existing row + warn (idempotent on PK conflict only).

### Scheduler integration

In `marketpulse/scheduler/jobs.py` `run_paper_trading_tick`, after fills + reconciliation settle and the tick's audit `TICK_COMPLETED` lands:

```python
# Charter top-3 #1 PR3a — EOD NAV snapshot. Piggybacks on tick fill
# settlement to avoid race conditions. PK conflicts (re-run same date)
# are handled INSIDE the runner — scheduler does NOT need to catch
# SnapshotAlreadyExists. Only non-PK persistence errors surface here.
try:
    run_nav_snapshot(session, trading_date=tick_date, settings=settings)
except Exception as exc:
    # L4: non-PK persistence errors are visible here; tick is never aborted.
    log.warning("nav_snapshot_failed", error=str(exc), tick_date=str(tick_date))
```

### `marketpulse/ops/charter_metrics.py` (extended, DB-backed)

```python
# These helpers are DB-backed; they are NOT pure (L9).
def build_north_star_section(
    session: Session | None, *, now: datetime,
) -> dict[str, Any]:
    """Latest-snapshot read. Empty table → no_snapshots_yet fallback.
    session=None → db_session_unavailable fallback (L10)."""

def build_diagnostics_section(
    session: Session | None, *, now: datetime,
) -> dict[str, Any]:
    """Window from last 30 snapshot trading_dates (L11). Audit-event queries
    bounded by that window. Empty audit → null value + observations=0 for
    each metric (locked shape)."""
```

`build_charter_metrics` signature:

```python
def build_charter_metrics(
    *, manifest_path: Path, now: datetime,
    backup_unavailable_reason: str | None = None,
    session: Session | None = None,                          # L10
) -> dict[str, Any]:
```

### `marketpulse/web/routes/charter.py` (modified)

```python
@router.get("/lab/charter-metrics")
def lab_charter_metrics(
    _: None = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ...
    return build_charter_metrics(
        manifest_path=manifest_path,
        now=datetime.now(UTC),
        session=db,
    )
```

## Testing

### Unit — `tests/portfolio/test_north_star.py`

| Test | Asserts |
|---|---|
| `test_compute_nav_basic_priced` | both positions priced; index + excess_return math correct; data quality empty |
| `test_compute_nav_unpriced_omitted` | unpriced position OMITTED from MTM (not zeroed); `unpriced_positions_count=1`; ticker recorded |
| `test_compute_nav_all_unpriced` | all unpriced → holdings_mtm=0, NAV=cash; data quality reflects all-degraded |
| `test_compute_nav_unpriced_tickers_dedup_sorted` | 3 lots same ticker → count=3, tickers tuple has 1 unique element (L15) |
| `test_compute_nav_spy_missing` | spy_close=None → spy_index=None, excess_return=None; portfolio side fully populated |
| `test_compute_nav_first_snapshot_self_anchor` | anchor == portfolio_nav → portfolio_index = 1.0 |
| `test_coverage_ratio_clamped` | observed=120 → coverage_ratio=1.0 |
| `test_is_sufficient_threshold` | 89→false; 90→true |
| `test_nav_snapshot_is_frozen` | mutation raises `FrozenInstanceError` |

### Repo — `tests/portfolio/test_snapshot_repo.py`

| Test | Asserts |
|---|---|
| `test_insert_snapshot_succeeds` | round-trip via `get_snapshot` |
| `test_insert_snapshot_pk_conflict_raises` | second insert same date → `SnapshotAlreadyExists`; original row unchanged |
| `test_force_replace_snapshot` | `is_rebuilt=True`, `rebuild_reason=<text>`, `updated_at != created_at` |
| `test_get_latest_snapshot_empty` | returns None |
| `test_get_snapshot_series_range_ascending` | 5 inserted; range returned inclusive, ordered ascending by trading_date |
| `test_get_recent_snapshot_dates_ascending` | 40 inserted; `limit=30` returns 30 dates in ascending order (consumer-friendly) |
| `test_count_snapshots_in_window_caps_at_size` | 200 snapshots inserted; `window_size=90` returns 90 (trading-day cap, NOT calendar `[D-89, D]`) |
| `test_count_snapshots_in_window_below_cap` | 12 snapshots inserted; `window_size=90` returns 12 (fewer than cap) |

### Runner — `tests/portfolio/test_snapshot_runner.py`

| Test | Asserts |
|---|---|
| `test_run_nav_snapshot_first_run_self_anchors` | fresh DB; 1 priced position; row created with `anchor_portfolio_nav == portfolio_nav` |
| `test_run_nav_snapshot_historical_open_positions` | position opened day-2 closed day-4; rebuild of day-3 includes it, rebuild of day-5 excludes it — proves L7 |
| `test_run_nav_snapshot_idempotent_pk_conflict` | second call same date returns existing row + warns; does NOT raise |
| `test_run_nav_snapshot_repo_error_propagates` | force repo to raise a non-PK error; runner re-raises (L4) — does NOT swallow |
| `test_run_nav_snapshot_no_spy_in_cache` | price_cache lacks SPY → snapshot persists with spy_close/spy_index/excess_return all None |
| `test_run_nav_snapshot_partial_pricing` | 3 positions, 1 unpriced → unpriced_count=1, MTM reflects only priced |
| `test_run_nav_snapshot_no_network` | monkeypatch any yfinance shim to raise; snapshot still succeeds — proves L5 |
| `test_run_nav_snapshot_spy_anchor_late_establishment` | snapshot day-1 with SPY missing → row has `anchor_spy_close=null, spy_index=null`. Day-2 with SPY present → row's `anchor_spy_close` = its own `spy_close`. Day-3 with SPY present → reads anchor from day-2; day-1 row stays frozen (L16) |
| `test_run_nav_snapshot_empty_cash_ledger_raises` | `paper_cash_ledger` has no row by EOD → runner raises `NoCashLedgerForDate` (L18); no row inserted |

### Scheduler isolation — `tests/scheduler/test_paper_trading_tick.py`

| Test | Asserts |
|---|---|
| `test_scheduler_nav_snapshot_failure_does_not_abort_tick` | inject `run_nav_snapshot` to raise; `run_paper_trading_tick` completes with warning logged; tick audit `TICK_COMPLETED` already emitted before snapshot call |

### charter_metrics extension — `tests/ops/test_charter_metrics_north_star.py`

| Test | Asserts |
|---|---|
| `test_north_star_empty_table` | no snapshots → `error="no_snapshots_yet"`, all numeric fields null, coverage=0 |
| `test_north_star_partial_window` | 12 snapshots → value=latest excess_return, coverage≈0.133, is_sufficient=False, data_quality carried through |
| `test_north_star_sufficient_window` | 90 snapshots → is_sufficient=True |
| `test_north_star_session_none` | session=None → `error="db_session_unavailable"`, locked empty shape |
| `test_diagnostics_empty_audit` | snapshots present, audit empty → each metric value=null, observations=0 |
| `test_diagnostics_window_from_snapshot_series` | 40 snapshots; window = most-recent 30 trading_dates; events outside excluded (L11) |
| `test_diagnostics_tick_success_rate` | seed 28 `TICK_COMPLETED` + 2 `ENGINE_INVARIANT_ERROR` → value=28/30=0.933 |
| `test_diagnostics_rejection_rate_mutually_exclusive` | seed 18 `ORDER_PLACED` + 12 `ORDER_REJECTED` → value=12/30=0.4 (proves L12 denominator) |
| `test_diagnostics_paper_trade_count_via_fills` | seed `paper_fill` entry rows + audit `ORDER_ENTRY_FILLED` → value reflects fill rows count, not audit count (L13) |
| `test_diagnostics_coverage_ratio_server_side` | every diagnostic carries `coverage_ratio`; consumer never needs to compute |

### Route — extend `tests/web/test_charter_route.py`

| Test | Asserts |
|---|---|
| `test_endpoint_north_star_empty` | fresh DB → 200; `north_star.error="no_snapshots_yet"` |
| `test_endpoint_north_star_with_snapshot` | seed 1 snapshot row → 200; numeric values present |
| `test_endpoint_diagnostics_populated` | seed audit + snapshots → 200; diagnostics carry values + coverage_ratio |
| `test_endpoint_no_network_call` | monkeypatch any yfinance shim to raise; endpoint returns 200 — read-path is DB-only |
| `test_endpoint_decimals_serialized_as_floats` | response JSON: `north_star.value`, `portfolio_index`, `spy_index`, `coverage_ratio` and each diagnostic's `value` / `coverage_ratio` are JSON numbers (not strings); `type(parsed_value) is float` (L17) |
| `test_endpoint_data_quality_is_complete` | snapshot row with `unpriced_positions_count=0` → `data_quality.is_complete=True`; row with count=1 → `is_complete=False` |
| `test_endpoint_coverage_ratio_roundtrip` | seed snapshot with `coverage_ratio=Decimal("0.4")` → JSON `north_star.coverage_ratio == 0.4` (float); assert `float(latest_snapshot_row.coverage_ratio) == response_body[...]` |

### Migration — `tests/migrations/test_0012_paper_nav_snapshot.py`

| Test | Asserts |
|---|---|
| `test_alembic_upgrade_creates_table` | forward apply creates `paper_nav_snapshot` with the locked column set |
| `test_alembic_downgrade_drops_table` | downgrade removes the table |
| `test_column_defaults_safe_for_hand_insert` | INSERT with only required columns: `is_rebuilt` defaults to 0, `unpriced_positions_count` defaults to 0 |

## Acceptance Criteria

1. `ruff check .` clean.
2. Full pytest suite green; no regressions in PR1/PR2 tests.
3. Alembic `0012_paper_nav_snapshot` applies cleanly forward and downgrades cleanly.
4. `run_paper_trading_tick` continues to succeed when `run_nav_snapshot` raises a non-PK persistence error (warning logged, tick not aborted).
5. `run_nav_snapshot` does NOT swallow non-PK persistence errors (L4 visible).
6. Manual smoke post-deploy: hit `/lab/charter-metrics` after the first post-deploy tick. `north_star.error` transitions from `"no_snapshots_yet"` to `null`; first snapshot has `coverage_ratio = Decimal('1') / Decimal('90')` (i.e. ≈ 0.011111...), `is_sufficient=false`. No fixed three-decimal string comparison.
7. Spec / code never reference `yfinance` from the snapshot runner or charter_metrics extension.
8. Spec / code never reference HTML / HTMX / template / markdown writer for this PR.

## Out of scope (deferred)

- PR3b weekly markdown report and its cron.
- `/lab/portfolio-vs-spy` chart UI (Charter top-3 #2).
- Admin rebuild CLI.
- SPY backfill / `price_cache` writer changes.
- Alerts, sparklines, CSV export, intraday recompute.

## Forward compatibility notes

- PR3b weekly report consumes `paper_nav_snapshot` directly. It must not re-derive metric semantics; it reads the row and renders.
- Charter top-3 #2 chart UI also reads the snapshot series. Same rule: render, never recompute.
- If audit-event model changes (L12 invalidation), the rejection-rate metric needs a spec revision — the snapshot table is unaffected.
- If `paper_engine` ever supports fractional shares, `quantity: Decimal` is already correct in the typed boundary (L14). The SQL column will need migration at that point.
