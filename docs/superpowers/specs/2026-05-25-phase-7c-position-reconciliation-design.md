# Phase 7c — Position Reconciliation MVP

**Status:** Approved (2026-05-25), pending plan + implementation
**Goal:** Detect whether MarketPulse paper state and broker truth materially diverge at the position level.

## Background

- **Phase 7a (Flex readonly sync)** captures IBKR Activity Flex snapshots into the `broker_*` tables. `BrokerSyncRun` / `BrokerAccountSnapshot` / `BrokerCashSnapshot` / `BrokerPositionSnapshot` are the four truth tables (`/lab/broker` already exposes them).
- **Phase 6 / 6a / 6b (paper trading)** drives the `paper_*` tables (`PaperOrder`, `PaperFill`, `PaperPosition`, `PaperCashLedger`, `PaperAuditEvent`) via a forward-execution engine and daily scheduler.
- These two worlds have never been compared against each other. Phase 7c is the **first reconciliation surface** between them.

## Scope Locks

These bound the MVP and prevent scope drift into a reconciliation platform.

| Lock | Statement |
|------|-----------|
| L1 | Reconciliation granularity is position-level only. |
| L2 | Compare latest completed broker snapshot vs current paper state only. No historical timeline reconciliation. |
| L3 | MVP reconciliation dimensions: symbol presence, net quantity, side direction. |
| L4 | No fill-level, commission, slippage, realized/unrealized P&L reconciliation. |
| L5 | Anomaly detection only — no automatic synchronization or corrective writeback. |
| L6 | Reconciliation is computed on-demand from (latest completed broker snapshot, current paper state). |
| L7 | No persistence — no `reconciliation_run` table, no Alembic migration. |
| L8 | `/lab/reconcile` is a derived inspection surface, not a historical audit archive. |
| L9 | Refresh semantics follow 6f/7a: refresh-on-load only. |
| L10 | Paper reconciliation scope is `paper_position.exit_fill_id IS NULL` only (open positions). |
| L11 | Quantity comparison uses normalized Decimal (`Decimal(paper.quantity)` vs `broker.quantity`). |
| L12 | Quantity reconciliation uses material-share threshold semantics, not Decimal exact equality. |
| L13 | `QUANTITY_MISMATCH` triggers only when `abs(paper_qty - broker_qty) >= 1`. |
| L14 | Fractional broker quantities are preserved in display even when row is `MATCHED`. |

**Explicitly NOT in MVP** (deferred to Phase 7d+):
- Pending / partially-filled order reconciliation
- Market value diff, P&L diff
- Execution quality / slippage attribution
- Simulator calibration
- Drift-over-time / historical reconciliation
- Auto-tolerance learning
- Auto-repair / corrective writeback
- Push notifications on diff

## Architecture

### Module structure
```
marketpulse/reconcile/
  __init__.py
  types.py           # DiffRow dataclass + DiffType enum + ReconciliationDashboard
  diffing.py         # Pure: reconcile_positions(paper, broker) -> list[DiffRow]
  query_models.py    # load_reconciliation_dashboard(db) -> ReconciliationDashboard
```

No new DB tables, no Alembic migration, no scheduler hook. The reconciliation logic is a pure function over current state.

### Web surface
```
marketpulse/web/routes/reconcile.py        # GET /lab/reconcile (read-only, auth-gated)
marketpulse/web/templates/lab_reconcile.html
marketpulse/web/templates/partials/
  reconcile_hero.html
  reconcile_summary_cards.html
  reconcile_diff_table.html
```

Nav link added to `base.html` next to `/lab/broker`, labeled "对账".

### Diff taxonomy (canonical enum names)

```python
class DiffType(StrEnum):
    MATCHED            = "matched"
    MISSING_IN_BROKER  = "missing_in_broker"  # paper has, broker doesn't
    MISSING_IN_PAPER   = "missing_in_paper"   # broker has, paper doesn't
    QUANTITY_MISMATCH  = "quantity_mismatch"  # both have, |diff| >= 1
    SIDE_MISMATCH      = "side_mismatch"      # paper_qty * broker_qty < 0
```

UI labels may shorten ("缺 broker" / "缺 paper") but enum names are the canonical reference everywhere in code, tests, and the architecture guard.

### `DiffRow` shape
```python
@dataclass(frozen=True)
class DiffRow:
    symbol: str
    diff_type: DiffType
    paper_qty: Decimal | None      # None when MISSING_IN_PAPER
    broker_qty: Decimal | None     # None when MISSING_IN_BROKER
    delta: Decimal | None          # paper_qty - broker_qty; None if either side None
    is_red: bool                   # see Hero severity below
```

**Symbol normalization (input contract):** all symbol strings — both `paper.ticker` and `broker.symbol` — are normalized via `symbol.upper().strip()` before entering the diff. No conId / exchange / currency-level matching in MVP.

**Aggregation (input contract):** if the same normalized symbol appears more than once on either side (multi-lot paper positions, or duplicate broker snapshot rows), the input maps must be pre-aggregated by `sum(quantity)`. The map type is `Mapping[str, Decimal]` keyed by normalized symbol; the diff function never sees lot-level rows.

### `reconcile_positions()` algorithm

```python
_SEVERITY_RANK = {
    SIDE_MISMATCH:     0,  # red, most severe
    MISSING_IN_BROKER: 1,  # red when paper qty != 0, else yellow
    QUANTITY_MISMATCH: 2,
    MISSING_IN_PAPER:  3,
    MATCHED:           4,  # least severe, last
}

def reconcile_positions(
    paper: Mapping[str, Decimal],
    broker: Mapping[str, Decimal],
) -> list[DiffRow]:
    """Pure diff. Inputs are pre-normalized symbol→qty maps.

    Caller is responsible for symbol normalization (upper().strip()) and
    same-symbol aggregation. Returns rows sorted by severity then symbol.
    """
    rows: list[DiffRow] = []
    for symbol in sorted(paper.keys() | broker.keys()):
        p = paper.get(symbol)
        b = broker.get(symbol)
        if p is None:
            rows.append(DiffRow(symbol, MISSING_IN_PAPER, None, b, None, is_red=False))
        elif b is None:
            red = p != 0   # MISSING_IN_BROKER w/ non-zero paper exposure is severe
            rows.append(DiffRow(symbol, MISSING_IN_BROKER, p, None, None, is_red=red))
        elif p * b < 0:
            rows.append(DiffRow(symbol, SIDE_MISMATCH, p, b, p - b, is_red=True))
        elif abs(p - b) >= 1:
            rows.append(DiffRow(symbol, QUANTITY_MISMATCH, p, b, p - b, is_red=False))
        else:
            rows.append(DiffRow(symbol, MATCHED, p, b, p - b, is_red=False))
    return sorted(rows, key=lambda r: (_SEVERITY_RANK[r.diff_type], r.symbol))
```

### `load_reconciliation_dashboard(db)` algorithm

Settings attribute is `settings.ibkr_account_id` (confirmed against `marketpulse/config.py` — pydantic Settings field aliased to `IBKR_ACCOUNT_ID`).

1. **Pick the broker account:**
   - If `settings.ibkr_account_id` is non-empty: use it.
   - Else: query `account_id` from `BrokerSyncRun` where `status='completed'`. If exactly one distinct account exists, use it. If **multiple distinct accounts exist among completed runs only** (failed runs ignored), return `account_ambiguous=True` and skip the diff (hero shows gray "Ambiguous broker account — set `IBKR_ACCOUNT_ID`"). If no completed runs at all, fall through to step 2's `no_broker_data` branch.
2. **Fetch broker snapshot:** latest `BrokerSyncRun` where `status='completed'` AND `account_id=<chosen>`. If none, return `no_broker_data=True` (hero shows gray "尚未捕获 broker truth").
3. **Build broker positions map:** aggregate `BrokerPositionSnapshot` rows where `sync_run_id=<chosen>` by normalized symbol:
   ```python
   broker_map: dict[str, Decimal] = {}
   for row in rows:
       key = row.symbol.upper().strip()
       broker_map[key] = broker_map.get(key, Decimal(0)) + row.quantity
   ```
4. **Build paper positions map:** aggregate `PaperPosition` rows where `exit_fill_id IS NULL` by normalized ticker:
   ```python
   paper_map: dict[str, Decimal] = {}
   for row in rows:
       key = row.ticker.upper().strip()
       paper_map[key] = paper_map.get(key, Decimal(0)) + Decimal(row.quantity)
   ```
5. **Call `reconcile_positions(paper_map, broker_map)`**.
6. **Compute hero severity** (see below).
7. **Compute stale flag:** `now_utc - broker_run.completed_at > 24h` → `is_stale=True` (warning banner only; does NOT affect diff calculation).
8. **Return** `ReconciliationDashboard` with all fields.

### Hero severity

```python
def compute_hero_severity(rows: list[DiffRow], dashboard_state) -> Severity:
    if dashboard_state.no_broker_data or dashboard_state.account_ambiguous:
        return GRAY      # cannot reconcile
    non_matched = [r for r in rows if r.diff_type != MATCHED]
    if not non_matched:
        return GREEN     # 全部对齐
    has_red = any(r.is_red for r in non_matched)
    if has_red or len(non_matched) >= 3:
        return RED       # 严重偏差
    return YELLOW        # 有偏差
```

`is_red` is true for:
- Any `SIDE_MISMATCH`
- `MISSING_IN_BROKER` where paper qty is non-zero

(Both conditions reflect risks where paper believes it holds exposure broker doesn't see.)

### Stale broker snapshot

If `latest_completed_broker_sync_run.completed_at` is older than 24h:
- Hero shows a yellow banner: `"Broker snapshot 已 X 天未更新"` with a link to `/lab/broker`.
- Diff calculation is **not** altered (we still reconcile against the stale snapshot).
- The stale state can co-exist with green/yellow/red hero severity from the diff itself.

### Architecture guard

`tests/architecture/test_lab_reconcile_isolation.py` — AST-walk of:
- `marketpulse/reconcile/query_models.py`
- `marketpulse/reconcile/diffing.py`
- `marketpulse/web/routes/reconcile.py`

Forbidden references (write semantics):
- `session.add`, `session.flush`, `session.commit` on any paper_* or broker_* model
- Direct writes to `PaperOrder`, `PaperFill`, `PaperPosition`, `PaperCashLedger`, `PaperAuditEvent`
- Direct writes to `BrokerOrderIntent`, `BrokerOrderEvent`
- Direct writes to `BrokerSyncRun`, `BrokerAccountSnapshot`, `BrokerCashSnapshot`, `BrokerPositionSnapshot`

Forbidden references (out-of-scope reads):
- `BrokerOpenOrderSnapshot`, `BrokerExecutionSnapshot` (not part of MVP diff)
- `PaperOrder`, `PaperFill`, `PaperCashLedger`, `PaperAuditEvent` (only `PaperPosition` is allowed)

Allowed reads (MVP):
- `PaperPosition` (paper open positions)
- `BrokerSyncRun` (account picking + stale check + hero metadata)
- `BrokerPositionSnapshot` (broker positions)

`BrokerAccountSnapshot` and `BrokerCashSnapshot` are NOT consumed by MVP — `/lab/broker` already shows that data. If a future revision needs to display NLV in the reconciliation hero, add `BrokerAccountSnapshot` to the allowed-reads list at that time.

Template guard (substring scan of all `reconcile_*.html` partials): same forbidden-name list.

**Template authoring rule:** templates MUST NOT mention ORM model class names (e.g. `PaperOrder`, `BrokerOrderIntent`) even in user-facing explanatory copy, since the substring scan will fire. Use user-friendly Chinese / English labels only ("纸上交易订单", "broker 订单意图"). The guard is intentionally name-blind — keep the surface clean.

## UI

### Page structure (`/lab/reconcile`)

```
Hero (mp-lab-ops__header pattern, matching /lab/broker)
  Eyebrow: Lab · Reconciliation
  H1: 对账
  Body: latest broker sync time + paper open-position count + severity chip
  Right meta: ref code, started_at

  Note: MVP does NOT display "latest paper tick time". That would require
  reading PaperAuditEvent (TICK_COMPLETED event), which is excluded from
  the allowed-reads list to keep the architecture guard tight. Paper-side
  freshness can be inferred from the open-position count + the broker
  side's stale flag.
  Conditional banners:
    - Stale broker snapshot (yellow)
    - Account ambiguous (gray)
    - No broker data (gray)

Summary cards (5 cards, mp-lab-kpis grid):
  - 已对齐 (MATCHED count)
  - 缺 broker (MISSING_IN_BROKER count)
  - 缺 paper (MISSING_IN_PAPER count)
  - 数量不一致 (QUANTITY_MISMATCH count)
  - 方向相反 (SIDE_MISMATCH count) — red text if non-zero

Main table (mp-table--broker reuse):
  Columns: Symbol | 类型 chip | Paper Qty | Broker Qty | Δ | severity
  Rows sorted: red first, then QUANTITY_MISMATCH, then MATCHED, alphabetically within bucket
  Fractional broker qty displayed in full precision (per L14)
```

### Empty / error states

| State | Hero | Body |
|-------|------|------|
| No broker_sync_run | gray "无法对账" | empty-state card pointing to runbook |
| Latest run failed, no prior completed | gray "broker truth 不可用" | show recent failed runs from `BrokerSyncRun` |
| Account ambiguous (multi-account, no settings) | gray "Ambiguous broker account" | instruct setting `IBKR_ACCOUNT_ID` |
| No paper_position open rows | green/yellow/red depending on broker side | summary cards still render |
| Broker stale (> 24h) | severity unchanged | yellow banner above summary |

## Testing strategy

### Pure-logic tests (`tests/reconcile/test_diffing.py`)
Layer: `unit`. Exhaustive coverage of `reconcile_positions()`:
- Empty paper + empty broker → []
- Empty paper + non-empty broker → all MISSING_IN_PAPER with `delta is None`
- Non-empty paper + empty broker → all MISSING_IN_BROKER with `delta is None` and `is_red` set by paper qty != 0
- MATCHED: paper=100, broker=100 → diff_type=MATCHED, delta=0
- MATCHED with fractional: paper=100, broker=100.34 → MATCHED with delta=-0.34
- QUANTITY_MISMATCH boundary cases:
  - paper=100, broker=99    → QUANTITY_MISMATCH (|diff|=1, threshold inclusive)
  - paper=100, broker=99.5  → MATCHED (|diff|=0.5 < 1)
  - paper=100, broker=99.01 → MATCHED (just under threshold; spec L14 still records delta)
- SIDE_MISMATCH: paper=10 (long), broker=-5 (short) → SIDE_MISMATCH with is_red=True
- Sort order assertions: order must follow `_SEVERITY_RANK` then symbol
  - SIDE_MISMATCH before MISSING_IN_BROKER (both red, severity ranked)
  - MISSING_IN_BROKER before QUANTITY_MISMATCH before MISSING_IN_PAPER before MATCHED
  - Within same diff_type: alphabetical by symbol

### Query model tests (`tests/reconcile/test_query_models.py`)
Layer: `stateful`. SQLite in-memory:
- Empty DB → no_broker_data=True, gray hero
- Only failed runs → no_broker_data=True (per spec: failed runs do NOT count toward account ambiguity)
- One completed broker run + no paper positions → MISSING_IN_PAPER rows
- Completed broker run + paper positions, all MATCHED → green hero, 0 mismatches
- **Account picking: failed runs ignored** → seed 2 failed runs on account A + 1 failed on account B + 0 completed: result is no_broker_data, NOT account_ambiguous
- Multi-account history (2 distinct accounts among completed runs) + no settings → account_ambiguous=True
- Multi-account history (2 distinct) + `settings.ibkr_account_id` set → picks the configured account, ambiguous=False
- Stale broker snapshot (completed_at = now - 25h) → is_stale=True
- **Symbol normalization:** broker has `"aapl"` (lowercase), paper has `"AAPL"` → MATCHED (both normalize to "AAPL")
- **Aggregation:** 2 BrokerPositionSnapshot rows for "AAPL" qty=50 each, paper has one row qty=100 → MATCHED with delta=0
- **Aggregation paper side:** 2 PaperPosition rows for "AAPL" qty=50 each (open), broker has one row qty=100 → MATCHED

### Route tests (`tests/web/test_lab_reconcile_route.py`)
Layer: `route`. TestClient with auth fixture:
- GET /lab/reconcile unauthenticated → 303 redirect to /login
- GET /lab/reconcile, empty DB → 200 with "无法对账" copy
- GET /lab/reconcile, MATCHED state → 200 with "已对齐" copy
- GET /lab/reconcile, QUANTITY_MISMATCH → 200 with mismatch summary card non-zero
- GET /lab/reconcile, SIDE_MISMATCH → 200 with red severity hero

### Architecture guard (`tests/architecture/test_lab_reconcile_isolation.py`)
Layer: `architecture`. AST-walk and template substring scan as described above. Tests that:
- query_models.py and diffing.py do not reference any forbidden write paths
- Templates do not reference forbidden out-of-scope models
- Allowed reads list is complete (positive assertion: at least one of `PaperPosition`, `BrokerSyncRun`, `BrokerPositionSnapshot` is referenced)

## Open questions

None. All clarifications resolved through 14 scope locks + 5 design refinements.

## Future work (explicitly out of scope)

- **Phase 7d — Simulator calibration:** use reconciliation findings to tune paper engine slippage / commission assumptions.
- **Phase 7e — Historical reconciliation:** persist `reconciliation_run` rows, expose drift charts.
- **Phase 7f — Fill-level matching:** reconcile `paper_fill` against `BrokerExecutionSnapshot`.
- **Phase 7g — P&L reconciliation:** market value, realized, unrealized.
- **Phase 7h — Active reconciliation:** ingest `BrokerOpenOrderSnapshot` to detect paper orders that never reached broker.
