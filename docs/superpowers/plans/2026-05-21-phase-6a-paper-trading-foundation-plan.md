# Phase 6a Paper Trading Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the paper-trading execution foundation — `ExecutionEngine` Protocol, `ForwardExecutionEngine` impl, single-writer persistence, daily orchestration, scheduler entrypoint, stateful test suite — so MarketPulse can ingest AI events daily and produce `paper_order` / `paper_fill` / `paper_position` / `paper_cash_ledger` rows.

**Architecture:** Five DB tables added (no Phase 1-5 schema changes). New `marketpulse/trading/` package: command-only Protocol in `execution_engine.py`, the only Phase 6 impl in `forward_engine.py`, single-writer surface in `repository.py`, thin orchestration in `daily_cycle.py`, thin scheduler entrypoint in `marketpulse/scheduler/paper_trading_tick.py`. Phase 5/Phase 6 share one pure allocation kernel (`marketpulse/backtest/allocation.py`).

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 (Mapped[] style), Alembic, SQLite (`Numeric(18, 6)` for prices/cash), `TZDateTime` TypeDecorator for UTC, APScheduler (existing), `exchange_calendars` (new dependency, pinned), pytest with `# Layer: invariant|behavioral|stateful` taxonomy.

**Spec:** `docs/superpowers/specs/2026-05-21-phase-6a-paper-trading-foundation-design.md` (commit `dc20658`).
**Umbrella:** `docs/superpowers/specs/2026-05-21-phase-6-umbrella-design.md` — 32 architectural locks the implementation must honor.

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `marketpulse/backtest/allocation.py` | 6a-0. `allocate_for_day()` pure kernel. `BidCandidate`, `AllocationContext`, `SizingContext`, `AllocationResult`, `PositionSnapshot`, `AllocationWinner` dataclasses. |
| `marketpulse/trading/__init__.py` | 6a-1. Empty; no side effects. |
| `marketpulse/trading/types.py` | 6a-1. `OrderRequest`, `OrderId`, `AllocationRunId`, `TickResult`, `TickError`, `PlaceOrderResult`, `OrderRejected`, `InvariantError`, `OrderStatus`, `PositionStatus`, `FillSide`, `AuditEventType` enum. |
| `marketpulse/trading/execution_engine.py` | 6a-1. `ExecutionEngine` Protocol (3 methods). |
| `marketpulse/trading/clock.py` | 6a-1. `Clock` Protocol, `WallClock`, `FakeClock`. |
| `marketpulse/trading/calendar.py` | 6a-1. `NYTradingCalendar` (exchange_calendars-backed). |
| `marketpulse/trading/risk_gate.py` | 6a-1. `RiskGate` Protocol + `RiskResult` + `AlwaysApproveRiskGate`. |
| `marketpulse/trading/idempotency.py` | 6a-1. `compute_idempotency_key()`. |
| `marketpulse/trading/repository.py` | 6a-2. Single-writer surface for all `paper_*` mutations + execution-path reads. |
| `marketpulse/trading/forward_engine.py` | 6a-2. `ForwardExecutionEngine` (the only Phase 6 implementation). |
| `marketpulse/trading/kill_switch.py` | 6a-2. `KillSwitchState` (env + DB flag, audit on flip). |
| `marketpulse/trading/bid_aggregator.py` | 6a-3. Read-only NY-day window over `evaluation_event`. |
| `marketpulse/trading/daily_cycle.py` | 6a-3. Orchestration: gap-detect → collect → allocate → place_order × N → tick → TICK_COMPLETED. |
| `marketpulse/scheduler/paper_trading_tick.py` | 6a-3. Thin APScheduler entrypoint (~30 lines, no business logic). |
| `alembic/versions/0010_phase6_paper_trading.py` | 6a-1. Create 5 paper_* tables + indexes. |
| `tests/backtest/test_allocation_extraction.py` | 6a-0. Pre/post extraction equality on Phase 5 fixtures. |
| `tests/trading/__init__.py` | 6a-1. Empty. |
| `tests/trading/test_types.py` | 6a-1. |
| `tests/trading/test_clock.py` | 6a-1. |
| `tests/trading/test_calendar.py` | 6a-1. |
| `tests/trading/test_idempotency.py` | 6a-1. |
| `tests/trading/test_risk_gate.py` | 6a-1. |
| `tests/trading/test_kill_switch.py` | 6a-2. |
| `tests/trading/test_repository.py` | 6a-2. |
| `tests/trading/test_forward_engine.py` | 6a-2. |
| `tests/trading/test_bid_aggregator.py` | 6a-3. |
| `tests/trading/test_daily_cycle.py` | 6a-3. |
| `tests/trading/test_scheduler.py` | 6a-3. |
| `tests/trading/test_e2e_stateful.py` | 6a-4. Full multi-day FakeClock E2E. |

### Modified files

| Path | Change |
|---|---|
| `marketpulse/backtest/portfolio_simulator.py` | 6a-0. Replace inline BID→SIZE→DEDUP→ALLOC kernel with call to `allocate_for_day(...)`. |
| `marketpulse/db/models.py` | 6a-1. Append 5 model classes: `PaperOrder`, `PaperFill`, `PaperPosition`, `PaperCashLedger`, `PaperAuditEvent`. |
| `marketpulse/scheduler/jobs.py` | 6a-3. Register `paper_trading_tick_job`. |
| `marketpulse/main.py` | 6a-3. Startup hook calls `ensure_initial_deposit()`. |
| `marketpulse/config.py` | 6a-3. Add 4 settings: `paper_tick_hour`, `paper_tick_minute`, `paper_initial_deposit`, `paper_kill_switch`. |
| `pyproject.toml` | 6a-1. Add `exchange_calendars>=4.5,<5.0`. |
| `tests/conftest.py` | 6a-1. Extend `# Layer:` enforcement hook to accept `stateful` as third valid value. |

---

## Branch Setup

- [ ] **B0: Create the 6a branch from main**

```bash
git checkout main
git pull
git checkout -b plan/phase-6a-paper-trading-foundation
```

All sub-task commits land here. A single final PR merges to main at the end of 6a-4.

---

## Sub-task 6a-0: Extract `allocate_for_day` from Phase 5

**Goal:** Lift the per-day BID → SIZE → DEDUP → ALLOC kernel out of `simulate_shared_pool` into a pure function `allocate_for_day(...)` in a new module `marketpulse/backtest/allocation.py`. Phase 5 backtest calls it once per historical day; Phase 6 (later sub-tasks) will call it once per forward paper-trading day.

**Lock 6a-L1:** ONLY BID → SIZE → DEDUP → ALLOC. CLOSE, MTM, RECORD, equity-curve update, contribution decomposition, rolling-stats finalization STAY in `portfolio_simulator.py`.

**Lock 6a-L9:** `AllocationContext` carries every input the allocator needs as an explicit named field. No hidden `today` dependency.

### Task 6a-0.1: Capture a frozen pre-extraction baseline fixture

**Goal:** Save a JSON fixture of `simulate_shared_pool(**warm_pool)` *before any extraction*. Post-extraction (Task 6a-0.4) compares against it field-by-field. This is the actual regression guard — spot-checks are not sufficient for the riskiest change in 6a.

**Files:**
- Create: `tests/backtest/test_allocation_extraction.py`
- Create: `tests/backtest/fixtures/phase5_warm_pool_baseline.json` (generated, committed)

- [ ] **Step 1: Write the baseline harness**

```python
# tests/backtest/test_allocation_extraction.py
# Layer: behavioral
"""6a-0 regression: simulate_shared_pool's PortfolioBacktestResult is
behaviorally + public-field equal pre/post the allocate_for_day
extraction.

Per 6a-0 contract (spec § 2): behavioral + public-field equality, NOT
byte-identical. Numeric outputs and all bid_history records compared
field-by-field; intentionally versioned/provenance strings excluded.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pytest

from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

BASELINE_PATH = Path(__file__).parent / "fixtures" / "phase5_warm_pool_baseline.json"

# Versioned/provenance strings — excluded from the comparison.
EXCLUDED_FIELDS = {"bid_policy", "contribution_policy", "risk_policy", "sizing_policy"}


FLOAT_PRECISION = 10  # decimal places for stable cross-platform float repr


def _normalize(value):
    """Convert dataclasses / dates / Decimal / floats to JSON-friendly
    primitives so the comparison is structural, not object-identity.

    Floats are rounded to FLOAT_PRECISION decimal places to neutralize
    repr drift across Python versions and platforms (e.g.
    0.30000000000000004 vs 0.3). This matches the spec's "behavioral +
    public-field equality" contract — drift below 1e-10 is not a real
    regression."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _normalize(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items() if k not in EXCLUDED_FIELDS}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if value.__class__.__name__ == "Decimal":
        return str(value)
    if isinstance(value, float):
        return round(value, FLOAT_PRECISION)
    return value


def dump_result(result) -> dict:
    return _normalize(result)


@pytest.fixture
def phase5_warm_pool_inputs(phase5d_warm_pool):
    """Existing 5d warm-pool fixture in tests/conftest.py — canonical
    Phase 5 happy path covering shared pool + sizing + sector caps +
    correlation caps + contribution decomposition."""
    return phase5d_warm_pool


def test_simulate_shared_pool_matches_frozen_baseline(phase5_warm_pool_inputs):
    """Generate-or-compare regression guard.

    If RUN_6A0_BASELINE=1 is set, this test WRITES the baseline JSON
    (used once, pre-extraction). Otherwise it COMPARES the current run
    to the committed baseline. The compare path is what catches an
    extraction regression in 6a-0.4."""
    result = simulate_shared_pool(**phase5_warm_pool_inputs)
    current = dump_result(result)

    if os.environ.get("RUN_6A0_BASELINE") == "1":
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(json.dumps(current, indent=2, sort_keys=True))
        pytest.skip(f"Baseline regenerated at {BASELINE_PATH}")

    assert BASELINE_PATH.exists(), (
        f"Baseline missing at {BASELINE_PATH}. "
        f"Regenerate via: RUN_6A0_BASELINE=1 uv run pytest "
        f"tests/backtest/test_allocation_extraction.py::"
        f"test_simulate_shared_pool_matches_frozen_baseline"
    )
    expected = json.loads(BASELINE_PATH.read_text())
    assert current == expected, (
        "6a-0 regression: simulate_shared_pool output differs from the "
        "committed baseline. The extraction (or some upstream change) "
        "altered behavior. If the change is INTENTIONAL, regenerate via "
        "RUN_6A0_BASELINE=1 and review the diff carefully."
    )
```

- [ ] **Step 2: Generate the baseline AGAINST UNMODIFIED Phase 5 code**

```bash
RUN_6A0_BASELINE=1 uv run pytest tests/backtest/test_allocation_extraction.py::test_simulate_shared_pool_matches_frozen_baseline -v
```

Expected: SKIPPED with "Baseline regenerated at .../phase5_warm_pool_baseline.json". The fixture file now exists on disk.

- [ ] **Step 3: Re-run WITHOUT the env var to confirm comparison passes**

```bash
uv run pytest tests/backtest/test_allocation_extraction.py::test_simulate_shared_pool_matches_frozen_baseline -v
```

Expected: PASS. Same code, same output, identity comparison succeeds.

- [ ] **Step 4: Commit baseline harness + fixture together**

```bash
git add tests/backtest/test_allocation_extraction.py tests/backtest/fixtures/phase5_warm_pool_baseline.json
git commit -m "test(6a-0): frozen Phase 5 warm-pool baseline + comparison harness"
```

The baseline is now the actual regression guard. Task 6a-0.4 (extraction) must run this test (without `RUN_6A0_BASELINE`) and see PASS — that proves the extraction preserved behavior to the field level on real Phase 5 inputs.

### Task 6a-0.2: Create `marketpulse/backtest/allocation.py` with dataclasses

**Files:**
- Create: `marketpulse/backtest/allocation.py`

- [ ] **Step 1: Write the dataclass-shape test**

```python
# Append to tests/backtest/test_allocation_extraction.py

from decimal import Decimal


def test_allocation_dataclasses_exist():
    """6a-L9: AllocationContext carries every input the allocator needs
    as an explicit named field. No hidden today dependency."""
    from marketpulse.backtest.allocation import (
        AllocationContext,
        AllocationResult,
        AllocationWinner,
        BidCandidate,
        PositionSnapshot,
        SizingContext,
        allocate_for_day,
    )

    # AllocationContext must explicitly carry allocation_date (6a-L9).
    ctx = AllocationContext(
        allocation_date=date(2026, 5, 21),
        target_vol=0.01,
        lookback_days=60,
        sector_caps_enabled=True,
        sector_cap_pct=0.40,
        correlation_caps_enabled=True,
        correlation_cap_pct=0.40,
        correlation_threshold=0.60,
        contribution_enabled=False,
        contribution_lambda=0.5,
        pool_corr_mode="excludes_self",
        phase5e_warm_pool_overlap_days=20,
        max_capital_in_use=10_000.0,
    )
    assert ctx.allocation_date == date(2026, 5, 21)
    assert ctx.target_vol == 0.01

    # SizingContext is the per-strategy override map.
    sizing = SizingContext(
        base_position_size=1_000.0,
        min_position=200.0,
        max_position=4_000.0,
        sizing_enabled=True,
        per_strategy_overrides={},
    )
    assert sizing.base_position_size == 1_000.0

    # AllocationResult and other shells exist.
    assert AllocationWinner is not None
    assert AllocationResult is not None
    assert BidCandidate is not None
    assert PositionSnapshot is not None
    assert callable(allocate_for_day)
```

- [ ] **Step 2: Run the failing test**

```bash
uv run pytest tests/backtest/test_allocation_extraction.py::test_allocation_dataclasses_exist -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'marketpulse.backtest.allocation'`.

- [ ] **Step 3: Implement the dataclasses + stub function**

```python
# marketpulse/backtest/allocation.py
"""Phase 5/Phase 6 shared per-day allocation kernel (6a-0).

This module owns the BID → SIZE → DEDUP → ALLOC kernel extracted from
Phase 5's simulate_shared_pool. It is a PURE function — no DB, no Clock,
no ExecutionEngine, no audit, no I/O. Inputs are explicit dataclasses;
outputs are explicit dataclasses.

Lock 6a-L1: CLOSE, MTM, RECORD, equity-curve update, contribution
decomposition, rolling-stats finalization remain in
marketpulse/backtest/portfolio_simulator.py. The 6a-0 contract is a
narrow extraction.

Lock 6a-L9: AllocationContext carries every input the allocator needs
as an explicit named field. No hidden today dependency, no env lookup,
no DB read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

# Version stamped onto paper_order.allocator_version for replay
# determinism (lock xxviii). Bump when the kernel's behavior changes.
ALLOCATOR_VERSION = "phase6a-v1"
__version__ = ALLOCATOR_VERSION  # back-compat alias for any introspection


@dataclass(frozen=True)
class BidCandidate:
    """A raw bid candidate from the day's event stream. Phase 5 builds
    these from historical event/outcome JOIN rows; Phase 6 BidAggregator
    builds them from today's evaluation_event rows."""
    strategy: str
    ticker: str
    event_time: datetime          # UTC-aware (lock xxix)
    event_price: float
    horizon_date: date
    horizon_price: float | None   # filled by daily_cycle via PriceProvider
                                  # if BidAggregator left it None
    strategy_version: str


@dataclass(frozen=True)
class PositionSnapshot:
    """Currently-OPEN position as seen by the allocator at decision time."""
    strategy: str
    ticker: str
    quantity: int
    entry_price: float
    sector: str | None
    open_since: date


@dataclass(frozen=True)
class SizingContext:
    """Per-strategy sizing knobs. Stable across days for a single
    simulate_shared_pool run; the orchestrator threads it through."""
    base_position_size: float
    min_position: float
    max_position: float
    sizing_enabled: bool
    per_strategy_overrides: dict[
        str, tuple[float | None, float | None, float | None]
    ]


@dataclass(frozen=True)
class AllocationContext:
    """Explicit allocation-decision inputs (6a-L9). Every field is
    required; no defaults that hide a today dependency."""
    allocation_date: date
    target_vol: float
    lookback_days: int
    sector_caps_enabled: bool
    sector_cap_pct: float
    correlation_caps_enabled: bool
    correlation_cap_pct: float
    correlation_threshold: float
    contribution_enabled: bool
    contribution_lambda: float
    pool_corr_mode: str
    phase5e_warm_pool_overlap_days: int
    max_capital_in_use: float


@dataclass(frozen=True)
class AllocationWinner:
    """A bid that survived sizing + dedup + caps. Threaded into Phase 6
    OrderRequest construction."""
    strategy: str
    ticker: str
    event_time: datetime          # UTC-aware (lock xxix)
    event_price: float
    horizon_date: date
    horizon_price: float | None
    quantity: int
    weight: float
    raw_bid_weight: float | None
    pool_corr: float | None
    contribution_multiplier: float
    adjusted_bid_weight: float | None
    effective_corr_window: int
    rewarded_for_negative_corr: bool
    would_change_rank: bool
    size_clamped_by_override: bool
    strategy_version: str


@dataclass(frozen=True)
class BlockedBidReason:
    """Reason a bid did not become a winner. Typed to prevent the
    'blocked: tuple[object, ...]' architecture-erosion risk identified in
    the round-7 review. Subclasses or extension fields belong here; the
    allocator's blocked output is fully typed for downstream consumers."""
    strategy: str
    ticker: str
    reason: str          # one of: dedup_loser | cap_full | sector_cap_full
                         # | correlation_cap_full | cash_short
                         # | size_too_small (extend as needed)
    weight: float | None
    raw_bid_weight: float | None
    pool_corr: float | None
    contribution_multiplier: float
    adjusted_bid_weight: float | None


@dataclass(frozen=True)
class AllocationResult:
    """Outcome of one per-day allocation call.

    Authority contract (round-7 review Risk 10): the allocator is the
    CANONICAL source for cash_used / cash_remaining for this batch.
    Callers (daily_cycle, simulate_shared_pool) MUST NOT recompute these
    values from the winners list — that introduces derived-state
    duplication and silent drift. Read them as-is."""
    winners: tuple[AllocationWinner, ...]
    blocked: tuple[BlockedBidReason, ...]
    cash_used: float        # canonical — do not recompute
    cash_remaining: float   # canonical — do not recompute


def allocate_for_day(
    *,
    bids: list[BidCandidate],
    existing_positions: list[PositionSnapshot],
    cash_available: float,
    allocation_context: AllocationContext,
    sizing_context: SizingContext,
) -> AllocationResult:
    """Pure-function per-day allocation kernel.

    Stub for 6a-0.2. Real BID → SIZE → DEDUP → ALLOC logic is lifted out
    of simulate_shared_pool in 6a-0.4.
    """
    raise NotImplementedError(
        "allocate_for_day will be wired in 6a-0.4 (extraction step)"
    )
```

- [ ] **Step 4: Run the dataclass-shape test**

```bash
uv run pytest tests/backtest/test_allocation_extraction.py::test_allocation_dataclasses_exist -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/backtest/allocation.py tests/backtest/test_allocation_extraction.py
git commit -m "feat(6a-0): allocation module scaffolding — dataclasses + stub"
```

### Task 6a-0.3: Identify the extraction surface in `portfolio_simulator.py`

**Files:**
- Read: `marketpulse/backtest/portfolio_simulator.py` (line 107 onwards — `simulate_shared_pool`)

- [ ] **Step 1: Locate the per-day loop body**

Open the file and find the daily-loop body inside `simulate_shared_pool`. The shape is:

```
for current_date in trading_dates:
    # === CLOSE step (stays in portfolio_simulator) ===
    # close OPEN positions whose horizon_date <= current_date
    # update cash, accumulate realized PnL
    # update daily_equity_curve

    # === BID step (extract to allocate_for_day) ===
    # gather today's raw bid candidates from outcome stream

    # === WEIGHT step (extract) ===
    # rolling Sharpe, alpha conviction, raw_bid_weight, contribution

    # === SIZE step (extract) ===
    # compute_position_sizes per strategy + overrides

    # === DEDUP step (extract) ===
    # one strategy+ticker winner per day

    # === ALLOCATE step (extract) ===
    # sector cap, correlation cap, cash check, BidRecord telemetry

    # === MTM step (stays) ===
    # mark-to-market open positions for daily_equity_curve

    # === RECORD step (stays) ===
    # append bid_history rows, per_strategy contribution updates,
    # rolling_stats updates
```

The extraction lifts **only** WEIGHT + SIZE + DEDUP + ALLOCATE (the user spec calls this BID → SIZE → DEDUP → ALLOC; BID-gathering is included as "build BidCandidate list" — the *reading* of events stays where it was, but the *filtering/weighting/sizing/allocation* moves).

- [ ] **Step 2: Write a no-op marker test that asserts the extraction has not yet happened**

```python
# Append to tests/backtest/test_allocation_extraction.py

def test_extraction_marker_pre():
    """Asserts portfolio_simulator still owns the inline weight/size/dedup/
    allocate logic prior to 6a-0.4. Removed in 6a-0.4 once the extraction
    lands."""
    from pathlib import Path

    src = Path("marketpulse/backtest/portfolio_simulator.py").read_text()
    # These tokens are the loop-internal markers Phase 5 already uses.
    # If they're gone, the extraction landed.
    assert "compute_position_sizes" in src
    assert "compute_adjusted_bid_weight" in src or "compute_bid_weights" in src
```

- [ ] **Step 3: Verify the marker passes**

```bash
uv run pytest tests/backtest/test_allocation_extraction.py::test_extraction_marker_pre -v
```

Expected: PASS (the extraction has not happened yet).

- [ ] **Step 4: Commit the planning marker**

```bash
git add tests/backtest/test_allocation_extraction.py
git commit -m "test(6a-0): pre-extraction marker test"
```

### Task 6a-0.4: Extract the kernel into `allocate_for_day`

**Files:**
- Modify: `marketpulse/backtest/portfolio_simulator.py`
- Modify: `marketpulse/backtest/allocation.py`

- [ ] **Step 1: Write the post-extraction equality test**

```python
# Append to tests/backtest/test_allocation_extraction.py

def test_post_extraction_uses_allocate_for_day(phase5_warm_pool_inputs):
    """After extraction, simulate_shared_pool must produce the SAME
    PortfolioBacktestResult as the committed baseline (lock 6a-L1
    boundary check is in test_allocation_module_does_not_reference_
    phase5_only_concerns)."""
    result = simulate_shared_pool(**phase5_warm_pool_inputs)
    current = dump_result(result)
    expected = json.loads(BASELINE_PATH.read_text())
    # This is the same assertion as
    # test_simulate_shared_pool_matches_frozen_baseline; including it
    # here makes the extraction-regression narrative clear in failure
    # messages.
    assert current == expected, (
        "6a-0 extraction regression: post-extraction simulate_shared_pool "
        "output differs from the pre-extraction baseline. Behavioral + "
        "public-field equality contract violated."
    )
```

The committed `phase5_warm_pool_baseline.json` is the cross-validation source of truth. The strategy:

1. **Baseline captured** in 6a-0.1 against unmodified Phase 5 code.
2. **Extraction lands** in 6a-0.4.
3. **Same test re-runs** — `test_simulate_shared_pool_matches_frozen_baseline` must PASS without baseline regeneration. If it fails, the extraction broke behavior; revert.
4. **Full Phase 5 suite** also runs as a secondary defense — its existing assertions catch additional drift the baseline might miss.

- [ ] **Step 2: Move WEIGHT + SIZE + DEDUP + ALLOCATE from `portfolio_simulator.py` into `allocate_for_day(...)`**

This is the largest single edit in 6a-0. The body of the daily loop in `simulate_shared_pool` is restructured so the WEIGHT/SIZE/DEDUP/ALLOCATE block becomes:

```python
# Inside the existing for-loop body in simulate_shared_pool, AFTER the
# CLOSE step and AFTER building the day's raw_bid_candidates list:

day_bids = [
    BidCandidate(
        strategy=b.strategy,
        ticker=b.ticker,
        event_time=b.event_time,
        event_price=b.event_price,
        horizon_date=b.horizon_date,
        horizon_price=b.horizon_price,
        strategy_version=getattr(b, "strategy_version", "v0"),
    )
    for b in raw_bid_candidates_today
]

allocation_ctx = AllocationContext(
    allocation_date=current_date,
    target_vol=target_vol,
    lookback_days=lookback_days,
    sector_caps_enabled=sector_caps_enabled,
    sector_cap_pct=sector_cap_pct,
    correlation_caps_enabled=correlation_caps_enabled,
    correlation_cap_pct=correlation_cap_pct,
    correlation_threshold=correlation_threshold,
    contribution_enabled=contribution_enabled,
    contribution_lambda=contribution_lambda,
    pool_corr_mode=POOL_CORR_MODE,
    phase5e_warm_pool_overlap_days=MIN_OVERLAP_DAYS,
    max_capital_in_use=max_capital_in_use,
)

sizing_ctx = SizingContext(
    base_position_size=base_position_size,
    min_position=min_position,
    max_position=max_position,
    sizing_enabled=sizing_enabled,
    per_strategy_overrides=per_strategy_overrides or {},
)

allocation = allocate_for_day(
    bids=day_bids,
    existing_positions=_snapshot_open_positions(open_positions, sector_provider),
    cash_available=cash,
    allocation_context=allocation_ctx,
    sizing_context=sizing_ctx,
)

# allocation.winners + allocation.blocked feed the existing RECORD
# step downstream.
```

Inside `allocate_for_day`, paste the lifted-out logic — the WEIGHT block (rolling Sharpe + bid_weights + contribution adjustment), SIZE block (compute_position_sizes), DEDUP block (one winner per strategy+ticker), and ALLOCATE block (sector cap + correlation cap + cash check + BidRecord telemetry).

The lifted code already exists in `portfolio_simulator.py`; the move is mechanical. Wrap return values into `AllocationResult(winners=..., blocked=..., cash_used=..., cash_remaining=...)`.

A helper is needed in `portfolio_simulator.py`:

```python
def _snapshot_open_positions(
    open_positions: list[_OpenPosition],
    sector_provider: Callable[[str], str] | None,
) -> list[PositionSnapshot]:
    return [
        PositionSnapshot(
            strategy=p.strategy,
            ticker=p.ticker,
            quantity=p.quantity,
            entry_price=p.entry_price,
            sector=sector_provider(p.ticker) if sector_provider else None,
            open_since=p.entry_date,
        )
        for p in open_positions
    ]
```

- [ ] **Step 3: Run the full Phase 5 test suite (cross-validation)**

```bash
uv run pytest tests/backtest/ tests/web/ -x --tb=short
```

Expected: PASS. Every existing Phase 5 test continues to pass (behavioral + public-field equality holds; the frozen baseline from 6a-0.1 is the primary regression guard, and the Phase 5 test assertions are the secondary). If anything fails, the extraction has a bug — revert and re-do the move smaller.

- [ ] **Step 4: Update the marker test for post-extraction state**

```python
# Replace test_extraction_marker_pre with this:
def test_extraction_marker_post():
    """Asserts the heavy lifting moved out of portfolio_simulator into
    allocate_for_day."""
    from pathlib import Path

    sim_src = Path("marketpulse/backtest/portfolio_simulator.py").read_text()
    alloc_src = Path("marketpulse/backtest/allocation.py").read_text()

    # The kernel now lives in allocation.py
    assert "compute_position_sizes" in alloc_src
    assert "compute_adjusted_bid_weight" in alloc_src or \
           "compute_bid_weights" in alloc_src

    # portfolio_simulator calls into the kernel rather than owning it
    assert "allocate_for_day(" in sim_src
    assert "from marketpulse.backtest.allocation import" in sim_src
```

- [ ] **Step 5: Run extraction marker test**

```bash
uv run pytest tests/backtest/test_allocation_extraction.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit the extraction**

```bash
git add marketpulse/backtest/portfolio_simulator.py marketpulse/backtest/allocation.py tests/backtest/test_allocation_extraction.py
git commit -m "feat(6a-0): extract allocate_for_day kernel from portfolio_simulator (6a-L1)"
```

### Task 6a-0.5: Lock 6a-L1 boundary grep test

**Files:**
- Modify: `tests/backtest/test_allocation_extraction.py`

- [ ] **Step 1: Write the boundary test**

```python
# Append to tests/backtest/test_allocation_extraction.py

def test_allocation_module_does_not_reference_phase5_only_concerns():
    """6a-L1: allocate_for_day extracts ONLY BID→SIZE→DEDUP→ALLOC.
    CLOSE, MTM, RECORD, equity-curve update, contribution decomposition,
    and rolling-stats finalization stay in portfolio_simulator.py."""
    from pathlib import Path

    src = Path("marketpulse/backtest/allocation.py").read_text()

    # These concepts must NOT appear in the kernel module.
    forbidden = [
        "daily_equity_curve",
        "mark_to_market",
        "MTM",
        "decompose_day_contributions",
        "compute_rolling_metrics",
        "finalize_strategy_contribution",
    ]
    for token in forbidden:
        assert token not in src, (
            f"6a-L1 boundary violation: '{token}' leaked into "
            f"marketpulse/backtest/allocation.py"
        )
```

- [ ] **Step 2: Run boundary test**

```bash
uv run pytest tests/backtest/test_allocation_extraction.py::test_allocation_module_does_not_reference_phase5_only_concerns -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/backtest/test_allocation_extraction.py
git commit -m "test(6a-0): lock 6a-L1 extraction-boundary grep test"
```

### Task 6a-0.6: Final 6a-0 integration check

- [ ] **Step 1: Run full test suite + ruff**

```bash
uv run pytest -x --tb=short
uv run ruff check marketpulse tests
```

Expected: ALL PASS. No regression in Phase 5.

- [ ] **Step 2: Confirm sub-task complete**

```bash
git log --oneline plan/phase-6a-paper-trading-foundation ^main | head
```

Expected: see commits from 6a-0.1 through 6a-0.5.

---

## Sub-task 6a-1: DB Schema + Protocol + Scaffolding

**Goal:** Create the 5 paper_* tables + Alembic migration; lay down the `marketpulse/trading/` package with `types.py`, `execution_engine.py` (Protocol), `clock.py`, `calendar.py`, `risk_gate.py`, `idempotency.py`. Empty `__init__.py` stubs for `repository.py`, `forward_engine.py`, `kill_switch.py`, `bid_aggregator.py`, `daily_cycle.py` so import order works. **Zero behavior** in this sub-task — contracts and schema only.

### Task 6a-1.1: Add `exchange_calendars` dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Write the import smoke test**

```python
# Create tests/trading/__init__.py (empty)
# Create tests/trading/test_calendar.py

# Layer: invariant
"""6a-1: NYTradingCalendar invariants and smoke."""
from __future__ import annotations

from datetime import date

import pytest


def test_exchange_calendars_importable():
    """exchange_calendars is the locked dependency for lock xxxii."""
    import exchange_calendars
    assert exchange_calendars is not None
```

- [ ] **Step 2: Run failing test**

```bash
uv run pytest tests/trading/test_calendar.py::test_exchange_calendars_importable -v
```

Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Add the pinned dependency**

In `pyproject.toml`, find the `dependencies = [ ... ]` block and add:

```toml
    "exchange_calendars>=4.5,<5.0",
```

Then install:

```bash
uv sync
```

- [ ] **Step 4: Verify test passes**

```bash
uv run pytest tests/trading/test_calendar.py::test_exchange_calendars_importable -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock tests/trading/__init__.py tests/trading/test_calendar.py
git commit -m "feat(6a-1): pin exchange_calendars dependency (lock xxxii)"
```

### Task 6a-1.2: `types.py` — vocabulary

**Files:**
- Create: `marketpulse/trading/__init__.py`
- Create: `marketpulse/trading/types.py`
- Create: `tests/trading/test_types.py`

- [ ] **Step 1: Write the types test**

```python
# tests/trading/test_types.py
# Layer: invariant
# Assert frozen-ness only — do NOT strict-require hashability across the
# board (future fields may include list/dict context payloads).
"""6a-1: types.py vocabulary smoke."""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, UTC
from decimal import Decimal


def test_order_request_is_frozen():
    from marketpulse.trading.types import AllocationRunId, OrderRequest

    req = OrderRequest(
        strategy="test_strat",
        ticker="AAPL",
        quantity=10,
        event_time=datetime(2026, 5, 21, 14, 30, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=Decimal("150.00"),
        horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155.00"),
        allocation_run_id=AllocationRunId("paper-2026-05-21"),
        strategy_version="v0",
        allocator_version="v0",
        execution_engine_version="v0",
        weight=1.0,
        raw_bid_weight=1.0,
        pool_corr=0.1,
        contribution_multiplier=1.0,
        adjusted_bid_weight=1.0,
        effective_corr_window=60,
        rewarded_for_negative_corr=False,
        would_change_rank=False,
        size_clamped_by_override=False,
    )

    # Frozen dataclass: mutation must raise.
    with pytest.raises(dataclasses.FrozenInstanceError):
        req.quantity = 99  # type: ignore[misc]


def test_tick_result_and_tick_error_shapes():
    from marketpulse.trading.types import TickError, TickResult

    err = TickError(
        phase="entry_materialization",
        order_id=42,
        position_id=None,
        error="something bad",
    )
    res = TickResult(
        as_of=date(2026, 5, 21),
        entries_materialized=3,
        exits_materialized=1,
        errors=(err,),
    )
    assert res.entries_materialized == 3
    assert res.errors[0].phase == "entry_materialization"
    # errors must be a tuple (immutable) — 6a-L4
    assert isinstance(res.errors, tuple)


def test_place_order_result_carries_created_and_duplicate_flags():
    """6a-L2: PlaceOrderResult eliminates the TOCTOU race that a separate
    pre-check would introduce."""
    from marketpulse.trading.types import OrderId, PlaceOrderResult

    r = PlaceOrderResult(order_id=OrderId(1), created=True, duplicate=False)
    assert r.created is True
    assert r.duplicate is False

    r2 = PlaceOrderResult(order_id=OrderId(1), created=False, duplicate=True)
    assert r2.duplicate is True


def test_audit_event_type_enum_has_12_values():
    """6a audit event types: 12 total."""
    from marketpulse.trading.types import AuditEventType

    expected = {
        "ORDER_PLACED",
        "ORDER_PLACED_DUPLICATE",
        "ORDER_REJECTED",
        "ORDER_CANCELLED",
        "ORDER_ENTRY_FILLED",
        "POSITION_CLOSED",
        "KILL_SWITCH_FLIPPED",
        "KILL_SWITCH_CYCLE_SKIPPED",
        "TICK_COMPLETED",
        "TICK_REPROCESSED_COMPLETED",
        "SCHEDULER_GAP_DETECTED",
        "ENGINE_INVARIANT_ERROR",
    }
    actual = {e.value for e in AuditEventType}
    assert actual == expected, f"Missing: {expected - actual}; Extra: {actual - expected}"


import pytest  # at end so the test_audit_event imports above are clean
```

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/trading/test_types.py -v
```

Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3: Implement types.py**

```python
# marketpulse/trading/__init__.py
# (empty — no side effects per 6a brainstorm Q2 lock)
```

```python
# marketpulse/trading/types.py
"""Phase 6a shared vocabulary. Frozen dataclasses, enums, exceptions.

Nothing here imports from other marketpulse.trading.* modules — types is
the bottom of the dependency tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal, NewType


# === ID newtypes ===

OrderId = NewType("OrderId", int)
AllocationRunId = NewType("AllocationRunId", str)


# === Status / side enums (as Literal aliases, not Enum classes — keeps
# DB CHECK constraints simple) ===

OrderStatus = Literal["PLACED", "ENTRY_FILLED", "CANCELLED"]
PositionStatus = Literal["OPEN", "CLOSED"]
FillSide = Literal["ENTRY", "EXIT"]


# === Audit event types — 12 in 6a (6b/6g extend) ===

class AuditEventType(str, Enum):
    ORDER_PLACED = "ORDER_PLACED"
    ORDER_PLACED_DUPLICATE = "ORDER_PLACED_DUPLICATE"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_ENTRY_FILLED = "ORDER_ENTRY_FILLED"
    POSITION_CLOSED = "POSITION_CLOSED"
    KILL_SWITCH_FLIPPED = "KILL_SWITCH_FLIPPED"
    KILL_SWITCH_CYCLE_SKIPPED = "KILL_SWITCH_CYCLE_SKIPPED"
    TICK_COMPLETED = "TICK_COMPLETED"
    TICK_REPROCESSED_COMPLETED = "TICK_REPROCESSED_COMPLETED"
    SCHEDULER_GAP_DETECTED = "SCHEDULER_GAP_DETECTED"
    ENGINE_INVARIANT_ERROR = "ENGINE_INVARIANT_ERROR"


# === Exceptions ===

class OrderRejected(Exception):
    """Raised by ExecutionEngine.place_order when an order is rejected
    (kill switch, risk gate, etc.). Lock ix: raised ONLY after the
    ORDER_REJECTED audit row commits."""


class InvariantError(Exception):
    """Raised when a runtime invariant is violated. Caught by
    ExecutionEngine.tick to record a TickError and continue with the
    remaining rows. Phase 6 considers it a bug; Phase 7 may relax."""


# === Boundary objects ===

@dataclass(frozen=True)
class OrderRequest:
    """RiskGates produce, ExecutionEngine consumes. The constructor is
    the float → Decimal quantization site (lock xxii)."""

    strategy: str
    ticker: str
    quantity: int                       # signed; positive only in Phase 6
    event_time: datetime                # UTC, tz-aware (lock xxix)
    allocation_date: date               # NY trading day
    event_price: Decimal                # quantized
    horizon_date: date
    horizon_price: Decimal | None
    allocation_run_id: AllocationRunId

    # Versioning for replay determinism (lock xxviii)
    strategy_version: str
    allocator_version: str
    execution_engine_version: str

    # Phase 5 allocation provenance (lock x)
    weight: float
    raw_bid_weight: float | None
    pool_corr: float | None
    contribution_multiplier: float
    adjusted_bid_weight: float | None
    effective_corr_window: int
    rewarded_for_negative_corr: bool
    would_change_rank: bool
    size_clamped_by_override: bool


@dataclass(frozen=True)
class TickError:
    """Structured invariant-error record (6a-L4)."""
    phase: Literal["entry_materialization", "exit_materialization"]
    order_id: int | None
    position_id: int | None
    error: str


@dataclass(frozen=True)
class TickResult:
    """ExecutionEngine.tick() return shape."""
    as_of: date
    entries_materialized: int
    exits_materialized: int
    errors: tuple[TickError, ...]


@dataclass(frozen=True)
class PlaceOrderResult:
    """ExecutionEngine.place_order() return shape (6a-L2). The flags
    eliminate the TOCTOU race that a caller-side pre-check would
    introduce."""
    order_id: OrderId
    created: bool
    duplicate: bool
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/trading/test_types.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/__init__.py marketpulse/trading/types.py tests/trading/test_types.py
git commit -m "feat(6a-1): trading.types vocabulary (12 audit event types)"
```

### Task 6a-1.3: `execution_engine.py` Protocol

**Files:**
- Create: `marketpulse/trading/execution_engine.py`
- Create: `tests/trading/test_execution_engine_protocol.py`

- [ ] **Step 1: Write the Protocol-shape test**

```python
# tests/trading/test_execution_engine_protocol.py
# Layer: invariant
"""6a-1: ExecutionEngine Protocol has EXACTLY 3 methods (CQRS boundary)."""

from __future__ import annotations


def test_protocol_has_exactly_three_methods():
    from marketpulse.trading.execution_engine import ExecutionEngine

    # Use __dict__ instead of dir() — typing.Protocol's dir() includes
    # inherited internals (e.g. __subclasshook__, __init_subclass__,
    # _is_protocol, _is_runtime_protocol) that can shift between Python
    # versions. __dict__ contains only methods declared on this class.
    own_methods = {
        k for k, v in ExecutionEngine.__dict__.items()
        if callable(v) and not k.startswith("_")
    }
    assert own_methods == {"place_order", "cancel_order", "tick"}, (
        f"ExecutionEngine Protocol drift; got {own_methods}"
    )
```

- [ ] **Step 2: Run failing test**

```bash
uv run pytest tests/trading/test_execution_engine_protocol.py -v
```

Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement Protocol**

```python
# marketpulse/trading/execution_engine.py
"""ExecutionEngine Protocol — Phase 6 command-only contract.

Phase 6 ships ForwardExecutionEngine.
Phase 7 will add BrokerExecutionEngine.
Stretch 6d may add RealtimeExecutionEngine.

All implementations are structural (Protocol). Downstream code never
knows which one is running (lock vi)."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from marketpulse.trading.types import (
    OrderId,
    OrderRequest,
    PlaceOrderResult,
    TickResult,
)


class ExecutionEngine(Protocol):
    """Command-only Protocol. Reads of canonical state happen via DB
    query helpers in repository.py (execution-path) or future
    query_models.py (UI/observability — deferred to 6f/6g)."""

    def place_order(self, *, order_request: OrderRequest) -> PlaceOrderResult: ...

    def cancel_order(self, *, order_id: OrderId) -> None: ...

    def tick(self, *, as_of: date) -> TickResult: ...
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/trading/test_execution_engine_protocol.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/execution_engine.py tests/trading/test_execution_engine_protocol.py
git commit -m "feat(6a-1): ExecutionEngine Protocol (3 methods)"
```

### Task 6a-1.4: `clock.py` — Clock Protocol + WallClock + FakeClock

**Files:**
- Create: `marketpulse/trading/clock.py`
- Create: `tests/trading/test_clock.py`

- [ ] **Step 1: Write Clock tests**

```python
# tests/trading/test_clock.py
# Layer: invariant
"""6a-1: Clock Protocol + WallClock + FakeClock."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta


def test_wall_clock_now_is_utc_aware():
    from marketpulse.trading.clock import WallClock

    c = WallClock()
    now = c.now()
    assert now.tzinfo is not None
    # WallClock.today() must derive from .now().date(), NOT date.today()
    assert c.today() == now.date()


def test_fake_clock_advance_days():
    from marketpulse.trading.clock import FakeClock

    start = datetime(2026, 5, 21, 17, 30, tzinfo=UTC)
    c = FakeClock(now=start)
    assert c.now() == start
    assert c.today() == date(2026, 5, 21)

    c.advance(days=1)
    assert c.now() == start + timedelta(days=1)
    assert c.today() == date(2026, 5, 22)
```

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/trading/test_clock.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement Clock module**

```python
# marketpulse/trading/clock.py
"""Clock dependency for the trading layer (lock xxiii).

Production uses WallClock; tests use FakeClock. No production code in
marketpulse.trading.* or marketpulse.scheduler.paper_trading_tick may
call date.today() or datetime.now() directly — they MUST go through an
injected Clock (lock xxiii)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...
    def today(self) -> date: ...


class WallClock:
    """Production clock. Always returns UTC-aware datetimes."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def today(self) -> date:
        # Derive from .now() — preserves the tz invariant.
        return self.now().date()


class FakeClock:
    """Test clock. Caller controls time."""

    def __init__(self, *, now: datetime) -> None:
        assert now.tzinfo is not None, "FakeClock requires tz-aware datetime"
        self._now = now

    def now(self) -> datetime:
        return self._now

    def today(self) -> date:
        return self._now.date()

    def advance(self, *, days: int = 0, seconds: int = 0) -> None:
        self._now = self._now + timedelta(days=days, seconds=seconds)

    def set(self, *, now: datetime) -> None:
        assert now.tzinfo is not None
        self._now = now
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/trading/test_clock.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/clock.py tests/trading/test_clock.py
git commit -m "feat(6a-1): Clock Protocol + WallClock + FakeClock (lock xxiii)"
```

### Task 6a-1.5: `calendar.py` — NYTradingCalendar

**Files:**
- Create: `marketpulse/trading/calendar.py`
- Modify: `tests/trading/test_calendar.py`

- [ ] **Step 1: Add calendar behavior tests**

```python
# Append to tests/trading/test_calendar.py

from datetime import UTC, datetime


def test_sessions_in_range_inclusive_count():
    """sessions_in_range(a, b) returns trading sessions in [a, b] inclusive."""
    from marketpulse.trading.calendar import NYTradingCalendar

    cal = NYTradingCalendar()
    # 2026-05-21 (Thu) → 2026-05-26 (Tue). Memorial Day 2026 is Mon 25.
    # Sessions: Thu 21, Fri 22, (Mon 25 closed), Tue 26 — 3 sessions inclusive.
    assert cal.sessions_in_range(date(2026, 5, 21), date(2026, 5, 26)) == 3
    # Single-day range covering a session.
    assert cal.sessions_in_range(date(2026, 5, 21), date(2026, 5, 21)) == 1
    # a > b → 0.
    assert cal.sessions_in_range(date(2026, 5, 26), date(2026, 5, 21)) == 0


def test_sessions_after_excludes_start_includes_end():
    """sessions_after(a, b) = sessions in (a, b] — what gap detection
    needs. Returns 0 when last_tick == today (no missed days)."""
    from marketpulse.trading.calendar import NYTradingCalendar

    cal = NYTradingCalendar()
    # Thu 21 → Fri 22 → 1 session after Thu (Fri itself).
    assert cal.sessions_after(date(2026, 5, 21), date(2026, 5, 22)) == 1
    # Same day → 0 (no gap).
    assert cal.sessions_after(date(2026, 5, 21), date(2026, 5, 21)) == 0
    # Thu 21 → Tue 26 across Memorial Day → Fri 22 + Tue 26 = 2 sessions.
    assert cal.sessions_after(date(2026, 5, 21), date(2026, 5, 26)) == 2


def test_next_business_day_semantics():
    """next_business_day(d) = the first trading session STRICTLY AFTER d.

    Test cases:
    - Thu (session) → Fri (next session)
    - Fri before Memorial-Day-Monday → Tue (skip closed Mon)
    - Sat (non-session) → following Mon if open, else next session
    """
    from marketpulse.trading.calendar import NYTradingCalendar

    cal = NYTradingCalendar()

    # Thu 2026-05-21 → Fri 2026-05-22 (both sessions)
    assert cal.next_business_day(date(2026, 5, 21)) == date(2026, 5, 22)

    # Fri before Memorial Day 2026 (Mon 25 closed): Fri 22 → Tue 26
    assert cal.next_business_day(date(2026, 5, 22)) == date(2026, 5, 26)

    # Non-session input — Sat 2026-05-23: → next session (Tue 26 since Mon closed)
    assert cal.next_business_day(date(2026, 5, 23)) == date(2026, 5, 26)


def test_ny_calendar_today_ny_trading_date_for_post_close_utc():
    from marketpulse.trading.calendar import NYTradingCalendar

    cal = NYTradingCalendar()
    # 21:30 UTC on 2026-05-21 is 17:30 NY (post-close on the same NY day).
    utc_post_close = datetime(2026, 5, 21, 21, 30, tzinfo=UTC)
    assert cal.today_ny_trading_date(utc_post_close) == date(2026, 5, 21)


def test_today_ny_trading_date_on_non_session_rolls_back():
    """If clock fires on a non-session NY day (e.g., a holiday), the
    tick_date rolls back to the previous session — preventing accidental
    work on closed days."""
    from marketpulse.trading.calendar import NYTradingCalendar

    cal = NYTradingCalendar()
    # 2026-05-25 (Mon) is Memorial Day, closed. 22:00 UTC = 18:00 NY.
    holiday_utc = datetime(2026, 5, 25, 22, 0, tzinfo=UTC)
    # Previous session = Fri 2026-05-22.
    assert cal.today_ny_trading_date(holiday_utc) == date(2026, 5, 22)
```

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/trading/test_calendar.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement NYTradingCalendar**

```python
# marketpulse/trading/calendar.py
"""Canonical trading-calendar source for Phase 6 (lock xxxii).

ONE library: exchange_calendars. ONE module: this one. Risk gates (6b),
scheduler (6a), market-hours UI (6f) all import from here. No second
source."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

_NY = ZoneInfo("America/New_York")


class NYTradingCalendar:
    """US equities (XNYS) calendar wrapper.

    Phase 6 default. Lock xxix: timestamps are UTC; market-hours / holiday
    logic is evaluated in the instrument's exchange timezone (Phase 6
    default = America/New_York for US equities).

    Vocabulary discipline (per round-6 review):
        sessions_in_range(a, b)  → trading sessions in [a, b] inclusive
        sessions_after(a, b)     → trading sessions in (a, b] exclusive-start
    Gap detection uses sessions_after — last_processed_tick_date should
    not be re-counted as 'missed'."""

    def __init__(self) -> None:
        self._cal = xcals.get_calendar("XNYS")

    def is_business_day(self, d: date) -> bool:
        return self._cal.is_session(d.isoformat())

    def sessions_in_range(self, a: date, b: date) -> int:
        """Sessions in [a, b] inclusive. If a > b, returns 0."""
        if a > b:
            return 0
        return len(self._cal.sessions_in_range(a.isoformat(), b.isoformat()))

    def sessions_after(self, a: date, b: date) -> int:
        """Sessions in (a, b] — exclusive of start, inclusive of end.
        This is the right primitive for gap detection: if last_tick == today,
        there's nothing 'missed.' If last_tick=Thu and today=Tue across
        Memorial Day, missed = sessions_after(Thu, Tue) - 1 = 1 (Fri 22).
        """
        if a >= b:
            return 0
        # Sessions in (a, b] = sessions in [a+1day, b]
        from datetime import timedelta
        start = a + timedelta(days=1)
        if start > b:
            return 0
        return len(self._cal.sessions_in_range(start.isoformat(), b.isoformat()))

    def next_business_day(self, d: date) -> date:
        """First trading session STRICTLY AFTER d. Works for both
        session and non-session inputs; uses sessions_in_range with a
        forward window rather than next_session (which has documented
        edge-case behavior for non-session inputs in exchange_calendars).
        """
        from datetime import timedelta
        # Look forward up to 10 calendar days — covers any holiday gap.
        end = d + timedelta(days=10)
        sessions = self._cal.sessions_in_range(
            (d + timedelta(days=1)).isoformat(),
            end.isoformat(),
        )
        if len(sessions) == 0:
            raise RuntimeError(
                f"no trading session within 10 days after {d}; calendar gap is unrealistic"
            )
        return sessions[0].date()

    def today_ny_trading_date(self, now_utc: datetime) -> date:
        """Convert a UTC-aware datetime to the NY trading day.

        For a tick fired at 17:30 NY (21:30 UTC) on a Thursday, returns
        that Thursday's date. For non-session NY days (weekend / holiday),
        rolls back to the previous session date — so a tick that
        accidentally fires on Memorial Day produces tick_date == previous
        Friday (avoiding spurious 'today is closed' work).

        Phase 6 default fire time is 17:30 NY (post-close)."""
        if now_utc.tzinfo is None:
            raise ValueError("today_ny_trading_date requires tz-aware datetime")
        ny_now = now_utc.astimezone(_NY)
        ny_date = ny_now.date()
        if self.is_business_day(ny_date):
            return ny_date
        # Roll back to previous session.
        prev = self._cal.previous_session(ny_date.isoformat())
        return prev.date()
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/trading/test_calendar.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/calendar.py tests/trading/test_calendar.py
git commit -m "feat(6a-1): NYTradingCalendar (lock xxxii — exchange_calendars-backed)"
```

### Task 6a-1.6: `idempotency.py`

**Files:**
- Create: `marketpulse/trading/idempotency.py`
- Create: `tests/trading/test_idempotency.py`

- [ ] **Step 1: Write idempotency-key tests**

```python
# tests/trading/test_idempotency.py
# Layer: invariant
"""6a-1: compute_idempotency_key is deterministic over the lock-xvii inputs."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal


def _sample_request(strategy="s", ticker="AAPL", run_id="paper-2026-05-21"):
    from marketpulse.trading.types import AllocationRunId, OrderRequest

    return OrderRequest(
        strategy=strategy,
        ticker=ticker,
        quantity=10,
        event_time=datetime(2026, 5, 21, 14, 30, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=Decimal("150.00"),
        horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155.00"),
        allocation_run_id=AllocationRunId(run_id),
        strategy_version="v0",
        allocator_version="v0",
        execution_engine_version="v0",
        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
        effective_corr_window=60, rewarded_for_negative_corr=False,
        would_change_rank=False, size_clamped_by_override=False,
    )


def test_idempotency_key_is_deterministic():
    from marketpulse.trading.idempotency import compute_idempotency_key

    k1 = compute_idempotency_key(_sample_request())
    k2 = compute_idempotency_key(_sample_request())
    assert k1 == k2


def test_idempotency_key_distinguishes_strategy():
    from marketpulse.trading.idempotency import compute_idempotency_key

    k1 = compute_idempotency_key(_sample_request(strategy="a"))
    k2 = compute_idempotency_key(_sample_request(strategy="b"))
    assert k1 != k2


def test_idempotency_key_distinguishes_ticker():
    from marketpulse.trading.idempotency import compute_idempotency_key

    k1 = compute_idempotency_key(_sample_request(ticker="AAPL"))
    k2 = compute_idempotency_key(_sample_request(ticker="MSFT"))
    assert k1 != k2


def test_idempotency_key_distinguishes_run_id():
    from marketpulse.trading.idempotency import compute_idempotency_key

    k1 = compute_idempotency_key(_sample_request(run_id="paper-2026-05-21"))
    k2 = compute_idempotency_key(_sample_request(run_id="paper-2026-05-22"))
    assert k1 != k2


def test_idempotency_key_independent_of_version_fields():
    """6a-L7: same-day rerun after code deploy is STILL replay. The key
    must NOT include allocator_version or execution_engine_version."""
    from marketpulse.trading.idempotency import compute_idempotency_key
    from marketpulse.trading.types import AllocationRunId, OrderRequest

    base = _sample_request()
    bumped = OrderRequest(
        **{**base.__dict__, "allocator_version": "v999",
           "execution_engine_version": "v999"}
    )
    assert compute_idempotency_key(base) == compute_idempotency_key(bumped)
```

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/trading/test_idempotency.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement compute_idempotency_key**

```python
# marketpulse/trading/idempotency.py
"""Deterministic idempotency-key computation (lock xvii, lock xxx, 6a-L7).

The key is derived from (strategy, ticker, event_time, allocation_run_id).
It does NOT depend on version fields — same-day rerun after a code deploy
is STILL replay, not recomputation (6a-L7)."""

from __future__ import annotations

import hashlib

from marketpulse.trading.types import OrderRequest


def compute_idempotency_key(order_request: OrderRequest) -> str:
    """Deterministic 16-char hex digest. Matches the DB UNIQUE column on
    paper_order.idempotency_key."""
    payload = "|".join([
        order_request.strategy,
        order_request.ticker,
        order_request.event_time.isoformat(),
        order_request.allocation_run_id,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/trading/test_idempotency.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/idempotency.py tests/trading/test_idempotency.py
git commit -m "feat(6a-1): compute_idempotency_key (lock xvii, 6a-L7)"
```

### Task 6a-1.7: `risk_gate.py` — Protocol + AlwaysApproveRiskGate

**Files:**
- Create: `marketpulse/trading/risk_gate.py`
- Create: `tests/trading/test_risk_gate.py`

- [ ] **Step 1: Write tests**

```python
# tests/trading/test_risk_gate.py
# Layer: invariant


def test_always_approve_returns_approved_true():
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from marketpulse.trading.risk_gate import AlwaysApproveRiskGate
    from marketpulse.trading.types import AllocationRunId, OrderRequest

    req = OrderRequest(
        strategy="s", ticker="AAPL", quantity=10,
        event_time=datetime(2026, 5, 21, 14, 30, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=Decimal("150"), horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155"),
        allocation_run_id=AllocationRunId("paper-2026-05-21"),
        strategy_version="v0", allocator_version="v0",
        execution_engine_version="v0",
        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
        effective_corr_window=60, rewarded_for_negative_corr=False,
        would_change_rank=False, size_clamped_by_override=False,
    )

    gate = AlwaysApproveRiskGate()
    result = gate.check_pre_trade(order_request=req)
    assert result.approved is True
    assert result.reason == ""
```

- [ ] **Step 2: Run failing test**

```bash
uv run pytest tests/trading/test_risk_gate.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# marketpulse/trading/risk_gate.py
"""RiskGate Protocol + 6a's AlwaysApproveRiskGate stub.

6a ships ONLY the Protocol + AlwaysApproveRiskGate stub. 6b adds real
implementations (sector cap, correlation cap, daily loss limit,
market-hours). AlwaysApproveRiskGate approves all requests; the kill
switch is enforced separately BEFORE the risk gate in
ForwardExecutionEngine.place_order (NOT inside this gate). See lock
6a-L3 for fail-closed exception semantics in the engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from marketpulse.trading.types import OrderRequest


@dataclass(frozen=True)
class RiskResult:
    approved: bool
    reason: str
    gate_name: str = ""


class RiskGate(Protocol):
    def check_pre_trade(self, *, order_request: OrderRequest) -> RiskResult: ...


class AlwaysApproveRiskGate:
    """6a's default. Approves everything. 6b replaces this at the DI seam
    with real composite gates."""

    def check_pre_trade(self, *, order_request: OrderRequest) -> RiskResult:
        return RiskResult(approved=True, reason="", gate_name="always_approve")
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/trading/test_risk_gate.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/risk_gate.py tests/trading/test_risk_gate.py
git commit -m "feat(6a-1): RiskGate Protocol + AlwaysApproveRiskGate stub"
```

### Task 6a-1.7b: `price_provider.py` — Protocol + StubPriceProvider

**Files:**
- Create: `marketpulse/trading/price_provider.py`
- Create: `tests/trading/test_price_provider.py`

This task closes the round-6 Blocker 5 surface area. The `PriceProvider`
Protocol is used by `daily_cycle._make_order_request` to fill
`OrderRequest.horizon_price` for forward-running mode (Phase 6 doesn't
have outcome rows yet at order placement). 6a ships `StubPriceProvider`
for tests; production wires a real price-feed-backed provider in a
later phase (out of scope for 6a).

- [ ] **Step 1: Write tests**

```python
# tests/trading/test_price_provider.py
# Layer: invariant
"""6a-1: PriceProvider Protocol + StubPriceProvider determinism."""

from __future__ import annotations

from datetime import date
from decimal import Decimal


def test_stub_price_provider_returns_configured_value():
    from marketpulse.trading.price_provider import StubPriceProvider

    p = StubPriceProvider(map={("AAPL", date(2026, 5, 28)): Decimal("155.00")})
    assert p.horizon_price(ticker="AAPL", horizon_date=date(2026, 5, 28)) == Decimal("155.00")


def test_stub_price_provider_default_on_missing():
    from marketpulse.trading.price_provider import StubPriceProvider

    p = StubPriceProvider(default=Decimal("100.00"))
    assert p.horizon_price(ticker="UNKNOWN", horizon_date=date(2026, 5, 28)) == Decimal("100.00")


def test_stub_price_provider_returns_none_when_no_match_no_default():
    from marketpulse.trading.price_provider import StubPriceProvider

    p = StubPriceProvider()
    assert p.horizon_price(ticker="X", horizon_date=date(2026, 5, 28)) is None
```

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/trading/test_price_provider.py -v
```

Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

```python
# marketpulse/trading/price_provider.py
"""PriceProvider Protocol — daily_cycle uses an injected provider to
fill OrderRequest.horizon_price BEFORE construction. With horizon_price
populated, ForwardExecutionEngine.tick(horizon) will not raise
ENGINE_INVARIANT_ERROR in the normal forward flow.

6a ships StubPriceProvider for tests. Production wires a real
provider (yfinance-backed or broker quote API) — out of scope for the
6a foundation. The seam exists in marketpulse/scheduler/
paper_trading_tick.py; replace StubPriceProvider with a real
implementation when shipping."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol


class PriceProvider(Protocol):
    def horizon_price(self, *, ticker: str, horizon_date: date) -> Decimal | None: ...


class StubPriceProvider:
    """Deterministic test provider. Configured via an exact lookup map
    and/or a default fallback. Returns None when neither matches."""

    def __init__(
        self,
        *,
        map: dict[tuple[str, date], Decimal] | None = None,
        default: Decimal | None = None,
    ) -> None:
        self._map = map or {}
        self._default = default

    def horizon_price(self, *, ticker: str, horizon_date: date) -> Decimal | None:
        return self._map.get((ticker, horizon_date), self._default)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/trading/test_price_provider.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/price_provider.py tests/trading/test_price_provider.py
git commit -m "feat(6a-1): PriceProvider Protocol + StubPriceProvider"
```

### Task 6a-1.8: 5 DB model classes in `marketpulse/db/models.py`

**Files:**
- Modify: `marketpulse/db/models.py`

- [ ] **Step 1: Write model-shape tests**

```python
# Create tests/trading/test_models.py
# Layer: invariant
"""6a-1: paper_* model classes wired into Base."""


def test_paper_models_have_expected_tablenames():
    from marketpulse.db.models import (
        PaperAuditEvent, PaperCashLedger, PaperFill, PaperOrder, PaperPosition,
    )
    assert PaperOrder.__tablename__ == "paper_order"
    assert PaperFill.__tablename__ == "paper_fill"
    assert PaperPosition.__tablename__ == "paper_position"
    assert PaperCashLedger.__tablename__ == "paper_cash_ledger"
    assert PaperAuditEvent.__tablename__ == "paper_audit_event"


def test_paper_order_has_allocation_date_column():
    """6a-L5 / lock xxxiii companion: paper_order distinguishes
    event_time (AI saw it), allocation_date (allocator decision day),
    placed_at (DB write time)."""
    from sqlalchemy import inspect

    from marketpulse.db.models import PaperOrder

    cols = {c.name for c in inspect(PaperOrder).columns}
    assert "event_time" in cols
    assert "allocation_date" in cols
    assert "placed_at" in cols


def test_paper_order_has_versioning_columns():
    """Lock xxviii: replay determinism."""
    from sqlalchemy import inspect

    from marketpulse.db.models import PaperOrder

    cols = {c.name for c in inspect(PaperOrder).columns}
    assert {"strategy_version", "allocator_version", "execution_engine_version"} <= cols
```

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/trading/test_models.py -v
```

Expected: FAIL (classes don't exist).

- [ ] **Step 3: Append 5 model classes to `marketpulse/db/models.py`**

```python
# Append to marketpulse/db/models.py — full code follows.

# === Phase 6a paper-trading models ===
# Lock xv: NO modifications to existing Phase 1-5 tables.
# Lock xxii: Decimal(18, 6) for all price/cash/P&L columns.
# Lock xxix: All timestamps UTC via TZDateTime TypeDecorator.
# Lock xiii: paper_fill, paper_audit_event, paper_cash_ledger are append-only.

from decimal import Decimal as _Decimal


class PaperOrder(Base):
    __tablename__ = "paper_order"

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    allocation_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    event_time: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    allocation_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_date: Mapped[date] = mapped_column(Date, nullable=False)
    placed_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    filled_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    event_price: Mapped[_Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    horizon_price: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False)

    strategy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    allocator_version: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_engine_version: Mapped[str] = mapped_column(String(32), nullable=False)

    # Phase 5 allocation provenance
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    raw_bid_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    pool_corr: Mapped[float | None] = mapped_column(Float, nullable=True)
    contribution_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    adjusted_bid_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    effective_corr_window: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    rewarded_for_negative_corr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    would_change_rank: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    size_clamped_by_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_paper_order_status_horizon", "status", "horizon_date"),
        Index("ix_paper_order_status_alloc_date", "status", "allocation_date"),
        Index("ix_paper_order_alloc_date_strategy", "allocation_date", "strategy"),
        Index("ix_paper_order_strategy_placed", "strategy", "placed_at"),
        Index("ix_paper_order_run_id", "allocation_run_id"),
        CheckConstraint(
            "status IN ('PLACED', 'ENTRY_FILLED', 'CANCELLED')",
            name="ck_paper_order_status",
        ),
        CheckConstraint("quantity > 0", name="ck_paper_order_qty_positive"),
        # Time-consistency CHECKs (spec § 4.1 — round-7 merge):
        CheckConstraint(
            "status != 'PLACED' OR (filled_at IS NULL AND cancelled_at IS NULL)",
            name="ck_paper_order_placed_no_terminal_ts",
        ),
        CheckConstraint(
            "status != 'ENTRY_FILLED' OR filled_at IS NOT NULL",
            name="ck_paper_order_entry_filled_has_ts",
        ),
        CheckConstraint(
            "status != 'CANCELLED' OR cancelled_at IS NOT NULL",
            name="ck_paper_order_cancelled_has_ts",
        ),
    )


class PaperFill(Base):
    __tablename__ = "paper_fill"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("paper_order.id"), nullable=False)
    # FK is safe in this direction: paper_position is created first (with
    # entry_fill_id NULL), THEN paper_fill INSERT references the known
    # position_id. The circular-FK problem only affects the reverse
    # direction (paper_position.entry_fill_id / exit_fill_id → paper_fill),
    # which is why THOSE two columns stay as plain nullable Integer.
    position_id: Mapped[int] = mapped_column(ForeignKey("paper_position.id"), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    price: Mapped[_Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    filled_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    cash_delta: Mapped[_Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    realized_pnl: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)

    __table_args__ = (
        Index("ix_paper_fill_order_id", "order_id"),
        Index("ix_paper_fill_position_side", "position_id", "side"),
        UniqueConstraint("order_id", "side", name="uq_paper_fill_order_side"),
        CheckConstraint("side IN ('ENTRY', 'EXIT')", name="ck_paper_fill_side"),
        CheckConstraint("quantity > 0", name="ck_paper_fill_qty_positive"),
    )


class PaperPosition(Base):
    __tablename__ = "paper_position"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("paper_order.id"), nullable=False, unique=True)
    # entry_fill_id / exit_fill_id: per spec § 4.7, plain nullable INTEGER on SQLite v0
    # (no FK to paper_fill to avoid the circular-FK problem during ENTRY-flow
    # transaction). Phase 7 / Postgres migration tightens to deferred FKs.
    entry_fill_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_fill_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_price: Mapped[_Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    exit_price: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    realized_pnl: Mapped[_Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)

    __table_args__ = (
        Index("ix_paper_position_status_horizon", "status", "horizon_date"),
        Index("ix_paper_position_strategy_ticker", "strategy", "ticker"),
        Index("ix_paper_position_entry_fill", "entry_fill_id"),
        Index("ix_paper_position_exit_fill", "exit_fill_id"),
        CheckConstraint("status IN ('OPEN', 'CLOSED')", name="ck_paper_position_status"),
        CheckConstraint(
            "status != 'OPEN' OR exit_fill_id IS NULL",
            name="ck_paper_position_open_no_exit",
        ),
        CheckConstraint(
            "status != 'CLOSED' OR (entry_fill_id IS NOT NULL AND exit_fill_id IS NOT NULL)",
            name="ck_paper_position_closed_both_set",
        ),
    )


class PaperCashLedger(Base):
    __tablename__ = "paper_cash_ledger"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    delta: Mapped[_Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    fill_id: Mapped[int | None] = mapped_column(ForeignKey("paper_fill.id"), nullable=True)
    balance_after: Mapped[_Decimal] = mapped_column(Numeric(18, 6), nullable=False)

    __table_args__ = (
        Index("ix_paper_cash_ts", "timestamp"),
        Index("ix_paper_cash_fill", "fill_id"),
        CheckConstraint(
            "reason IN ('ENTRY_FILL', 'EXIT_FILL', 'INITIAL_DEPOSIT', 'MANUAL_ADJUSTMENT')",
            name="ck_paper_cash_reason",
        ),
    )


class PaperAuditEvent(Base):
    __tablename__ = "paper_audit_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_paper_audit_ts", "timestamp"),
        Index("ix_paper_audit_type_ts", "event_type", "timestamp"),
        Index("ix_paper_audit_order", "order_id"),
        Index("ix_paper_audit_strategy_ts", "strategy", "timestamp"),
        CheckConstraint(
            "event_type IN ("
            "'ORDER_PLACED', 'ORDER_PLACED_DUPLICATE', 'ORDER_REJECTED', "
            "'ORDER_CANCELLED', 'ORDER_ENTRY_FILLED', 'POSITION_CLOSED', "
            "'KILL_SWITCH_FLIPPED', 'KILL_SWITCH_CYCLE_SKIPPED', "
            "'TICK_COMPLETED', 'TICK_REPROCESSED_COMPLETED', "
            "'SCHEDULER_GAP_DETECTED', 'ENGINE_INVARIANT_ERROR'"
            ")",
            name="ck_paper_audit_event_type",
        ),
    )
```

Verify the file's existing imports include everything used (`String`, `Integer`, `Float`, `Boolean`, `Numeric`, `Date`, `Text`, `JSON`, `Index`, `CheckConstraint`, `UniqueConstraint`, `ForeignKey`, `Mapped`, `mapped_column`, `TZDateTime`, `date`, `datetime`). Add missing imports to the top of the file as needed.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/trading/test_models.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/db/models.py tests/trading/test_models.py
git commit -m "feat(6a-1): 5 paper_* SQLAlchemy models (locks xxii/xxviii/xxix)"
```

### Task 6a-1.9: Alembic migration `0010_phase6_paper_trading.py`

**Files:**
- Create: `alembic/versions/0010_phase6_paper_trading.py`

- [ ] **Step 1: Write migration up/down test**

```python
# Append to tests/trading/test_models.py

def test_migration_creates_and_drops_paper_tables(tmp_path, monkeypatch):
    """0010 migration creates all 5 paper_* tables on upgrade; drops them on downgrade."""
    import subprocess

    db_file = tmp_path / "smoke.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")

    # Force settings cache invalidation if needed
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)

    # All 5 tables exist
    from sqlalchemy import create_engine, inspect
    eng = create_engine(f"sqlite:///{db_file}")
    insp = inspect(eng)
    tables = set(insp.get_table_names())
    assert {"paper_order", "paper_fill", "paper_position",
            "paper_cash_ledger", "paper_audit_event"} <= tables

    # Downgrade by one revision and confirm removal.
    subprocess.run(["uv", "run", "alembic", "downgrade", "-1"], check=True)
    insp = inspect(eng)
    tables = set(insp.get_table_names())
    assert "paper_order" not in tables
```

- [ ] **Step 2: Run failing test**

```bash
uv run pytest tests/trading/test_models.py::test_migration_creates_and_drops_paper_tables -v
```

Expected: FAIL (migration not present).

- [ ] **Step 3: Generate the migration**

```bash
uv run alembic revision --autogenerate -m "phase6_paper_trading_tables" -- rev-id 0010
```

If autogenerate doesn't produce the right shape, write the migration manually:

```python
# alembic/versions/0010_phase6_paper_trading.py
"""phase6 paper trading tables

Revision ID: 0010
Revises: cff08d913c3b
Create Date: 2026-05-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "cff08d913c3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # paper_audit_event — no FKs (root of the tree)
    op.create_table(
        "paper_audit_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("strategy", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("context", sa.JSON(), nullable=False, server_default="{}"),
        sa.CheckConstraint(
            "event_type IN ("
            "'ORDER_PLACED', 'ORDER_PLACED_DUPLICATE', 'ORDER_REJECTED', "
            "'ORDER_CANCELLED', 'ORDER_ENTRY_FILLED', 'POSITION_CLOSED', "
            "'KILL_SWITCH_FLIPPED', 'KILL_SWITCH_CYCLE_SKIPPED', "
            "'TICK_COMPLETED', 'TICK_REPROCESSED_COMPLETED', "
            "'SCHEDULER_GAP_DETECTED', 'ENGINE_INVARIANT_ERROR'"
            ")",
            name="ck_paper_audit_event_type",
        ),
    )
    op.create_index("ix_paper_audit_ts", "paper_audit_event", ["timestamp"])
    op.create_index("ix_paper_audit_type_ts", "paper_audit_event", ["event_type", "timestamp"])
    op.create_index("ix_paper_audit_order", "paper_audit_event", ["order_id"])
    op.create_index("ix_paper_audit_strategy_ts", "paper_audit_event", ["strategy", "timestamp"])

    # paper_order
    op.create_table(
        "paper_order",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("idempotency_key", sa.String(32), nullable=False, unique=True),
        sa.Column("allocation_run_id", sa.String(64), nullable=False),
        sa.Column("strategy", sa.String(64), nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("allocation_date", sa.Date(), nullable=False),
        sa.Column("horizon_date", sa.Date(), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("event_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("horizon_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("strategy_version", sa.String(32), nullable=False),
        sa.Column("allocator_version", sa.String(32), nullable=False),
        sa.Column("execution_engine_version", sa.String(32), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("raw_bid_weight", sa.Float(), nullable=True),
        sa.Column("pool_corr", sa.Float(), nullable=True),
        sa.Column("contribution_multiplier", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("adjusted_bid_weight", sa.Float(), nullable=True),
        sa.Column("effective_corr_window", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("rewarded_for_negative_corr", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("would_change_rank", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("size_clamped_by_override", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint("status IN ('PLACED', 'ENTRY_FILLED', 'CANCELLED')", name="ck_paper_order_status"),
        sa.CheckConstraint("quantity > 0", name="ck_paper_order_qty_positive"),
        # Time-consistency CHECKs (spec § 4.1):
        sa.CheckConstraint(
            "status != 'PLACED' OR (filled_at IS NULL AND cancelled_at IS NULL)",
            name="ck_paper_order_placed_no_terminal_ts",
        ),
        sa.CheckConstraint(
            "status != 'ENTRY_FILLED' OR filled_at IS NOT NULL",
            name="ck_paper_order_entry_filled_has_ts",
        ),
        sa.CheckConstraint(
            "status != 'CANCELLED' OR cancelled_at IS NOT NULL",
            name="ck_paper_order_cancelled_has_ts",
        ),
    )
    op.create_index("ix_paper_order_status_horizon", "paper_order", ["status", "horizon_date"])
    op.create_index("ix_paper_order_status_alloc_date", "paper_order", ["status", "allocation_date"])
    op.create_index("ix_paper_order_alloc_date_strategy", "paper_order", ["allocation_date", "strategy"])
    op.create_index("ix_paper_order_strategy_placed", "paper_order", ["strategy", "placed_at"])
    op.create_index("ix_paper_order_run_id", "paper_order", ["allocation_run_id"])

    # paper_position (no FK to paper_fill — see spec § 4.7)
    op.create_table(
        "paper_position",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("paper_order.id"), nullable=False, unique=True),
        sa.Column("entry_fill_id", sa.Integer(), nullable=True),
        sa.Column("exit_fill_id", sa.Integer(), nullable=True),
        sa.Column("strategy", sa.String(64), nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("horizon_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(18, 6), nullable=True),
        sa.CheckConstraint("status IN ('OPEN', 'CLOSED')", name="ck_paper_position_status"),
        sa.CheckConstraint("status != 'OPEN' OR exit_fill_id IS NULL", name="ck_paper_position_open_no_exit"),
        sa.CheckConstraint(
            "status != 'CLOSED' OR (entry_fill_id IS NOT NULL AND exit_fill_id IS NOT NULL)",
            name="ck_paper_position_closed_both_set",
        ),
    )
    op.create_index("ix_paper_position_status_horizon", "paper_position", ["status", "horizon_date"])
    op.create_index("ix_paper_position_strategy_ticker", "paper_position", ["strategy", "ticker"])
    op.create_index("ix_paper_position_entry_fill", "paper_position", ["entry_fill_id"])
    op.create_index("ix_paper_position_exit_fill", "paper_position", ["exit_fill_id"])

    # paper_fill
    op.create_table(
        "paper_fill",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("paper_order.id"), nullable=False),
        sa.Column(
            "position_id", sa.Integer(),
            sa.ForeignKey("paper_position.id"), nullable=False,
        ),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("price", sa.Numeric(18, 6), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cash_delta", sa.Numeric(18, 6), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(18, 6), nullable=True),
        sa.UniqueConstraint("order_id", "side", name="uq_paper_fill_order_side"),
        sa.CheckConstraint("side IN ('ENTRY', 'EXIT')", name="ck_paper_fill_side"),
        sa.CheckConstraint("quantity > 0", name="ck_paper_fill_qty_positive"),
    )
    op.create_index("ix_paper_fill_order_id", "paper_fill", ["order_id"])
    op.create_index("ix_paper_fill_position_side", "paper_fill", ["position_id", "side"])

    # paper_cash_ledger
    op.create_table(
        "paper_cash_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delta", sa.Numeric(18, 6), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("fill_id", sa.Integer(), sa.ForeignKey("paper_fill.id"), nullable=True),
        sa.Column("balance_after", sa.Numeric(18, 6), nullable=False),
        sa.CheckConstraint(
            "reason IN ('ENTRY_FILL', 'EXIT_FILL', 'INITIAL_DEPOSIT', 'MANUAL_ADJUSTMENT')",
            name="ck_paper_cash_reason",
        ),
    )
    op.create_index("ix_paper_cash_ts", "paper_cash_ledger", ["timestamp"])
    op.create_index("ix_paper_cash_fill", "paper_cash_ledger", ["fill_id"])


def downgrade() -> None:
    op.drop_table("paper_cash_ledger")
    op.drop_table("paper_fill")
    op.drop_table("paper_position")
    op.drop_table("paper_order")
    op.drop_table("paper_audit_event")
```

- [ ] **Step 4: Run migration tests**

```bash
uv run pytest tests/trading/test_models.py::test_migration_creates_and_drops_paper_tables -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/0010_phase6_paper_trading.py tests/trading/test_models.py
git commit -m "feat(6a-1): 0010 migration — 5 paper_* tables (lock xv preserved)"
```

### Task 6a-1.10: Extend `# Layer:` pytest hook to accept `stateful`

**Files:**
- Modify: `tests/conftest.py`

- [ ] **Step 1: Locate existing hook**

```bash
grep -n "Layer:" tests/conftest.py
```

The Phase 5e hook accepts `invariant` and `behavioral`. Find the list of valid values.

- [ ] **Step 2: Add `stateful`**

```python
# In tests/conftest.py, find the line like:
#     valid_layers = {"invariant", "behavioral"}
# Change it to:
#     valid_layers = {"invariant", "behavioral", "stateful"}
```

- [ ] **Step 3: Add a self-smoke test for the hook**

```python
# Append to tests/trading/test_types.py (or wherever convenient):
# Layer: stateful

def test_layer_stateful_tag_accepted_by_hook():
    """If pytest collected this test, the hook accepts 'stateful'."""
    assert True
```

- [ ] **Step 4: Run full suite to confirm**

```bash
uv run pytest tests/trading -v
```

Expected: PASS (including the new `stateful`-tagged test).

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/trading/test_types.py
git commit -m "test(6a-1): pytest hook accepts # Layer: stateful (lock xxvi)"
```

### Task 6a-1.11: Empty stub modules for later sub-tasks

**Files:**
- Create: `marketpulse/trading/repository.py`
- Create: `marketpulse/trading/forward_engine.py`
- Create: `marketpulse/trading/kill_switch.py`
- Create: `marketpulse/trading/bid_aggregator.py`
- Create: `marketpulse/trading/daily_cycle.py`

- [ ] **Step 1: Write each stub**

Each file gets a docstring + a `__future__` import + an obvious TODO marker referencing the sub-task that fills it in. Example:

```python
# marketpulse/trading/repository.py
"""Phase 6a single-writer surface (6a-2).

This module is intentionally empty in 6a-1. Implementation lands in 6a-2.
Lock iii / 6a-L2 / 6a-L5 / 6a-L6 / 6a-L8 all manifest here."""

from __future__ import annotations

# Implemented in 6a-2.
```

Do the same for `forward_engine.py`, `kill_switch.py`, `bid_aggregator.py`, `daily_cycle.py`.

- [ ] **Step 2: Smoke test imports resolve**

```python
# Append to tests/trading/test_types.py:

def test_all_trading_modules_importable():
    """6a-1 scaffolding: all marketpulse.trading.* modules exist (some
    are stubs filled in by later sub-tasks). Import smoke only."""
    import marketpulse.trading.bid_aggregator  # noqa: F401
    import marketpulse.trading.daily_cycle  # noqa: F401
    import marketpulse.trading.forward_engine  # noqa: F401
    import marketpulse.trading.kill_switch  # noqa: F401
    import marketpulse.trading.repository  # noqa: F401
```

- [ ] **Step 3: Run import smoke**

```bash
uv run pytest tests/trading/test_types.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add marketpulse/trading/repository.py marketpulse/trading/forward_engine.py marketpulse/trading/kill_switch.py marketpulse/trading/bid_aggregator.py marketpulse/trading/daily_cycle.py tests/trading/test_types.py
git commit -m "feat(6a-1): empty stub modules for 6a-2/6a-3 sub-tasks"
```

### Task 6a-1.12: 6a-1 integration smoke

- [ ] **Step 1: Full suite + ruff + alembic round-trip**

```bash
uv run pytest -x --tb=short
uv run ruff check marketpulse tests
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```

Expected: ALL PASS.

- [ ] **Step 2: Confirm 6a-1 sub-task complete**

```bash
git log --oneline plan/phase-6a-paper-trading-foundation ^main | head -20
```

Expected: 6a-0 + 6a-1 commits present.

---

## Sub-task 6a-2: `ForwardExecutionEngine` + Repository + Kill Switch

**Goal:** Implement the actual `ForwardExecutionEngine` (`place_order`, `cancel_order`, `tick`), the `repository.py` single-writer surface (all `paper_*` mutations + the reads execution code needs), and `kill_switch.py`.

### Task 6a-2.1: Repository — `transaction()` context manager + audit append + idempotent helpers

**Files:**
- Modify: `marketpulse/trading/repository.py`
- Create: `tests/trading/test_repository.py`

- [ ] **Step 1: Write the transaction + audit test**

```python
# tests/trading/test_repository.py
# Layer: invariant
"""6a-2: repository single-writer surface basics."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base
    db_file = tmp_path / "repo.db"
    eng = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def test_write_audit_event_appends_row(session):
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.types import AuditEventType

    repo = Repository(session=session)
    repo.write_audit_event(
        event_type=AuditEventType.KILL_SWITCH_FLIPPED,
        order_id=None,
        strategy=None,
        reason="test",
        context={"k": "v"},
        timestamp=datetime(2026, 5, 21, 17, 30, tzinfo=UTC),
    )
    rows = session.execute(select(PaperAuditEvent)).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_type == "KILL_SWITCH_FLIPPED"
    assert rows[0].context == {"k": "v"}


def test_write_duplicate_audit_once_dedupes(session):
    """6a-L5: ORDER_PLACED_DUPLICATE deduped per (idempotency_key, tick_date)."""
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    args = dict(
        idempotency_key="abc123",
        order_id=42,
        strategy="s",
        tick_date=date(2026, 5, 21),
        context={"allocation_run_id": "paper-2026-05-21"},
        timestamp=datetime(2026, 5, 21, 17, 30, tzinfo=UTC),
    )
    repo.write_duplicate_audit_once(**args)
    repo.write_duplicate_audit_once(**args)  # second call no-op
    rows = session.execute(select(PaperAuditEvent)).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_type == "ORDER_PLACED_DUPLICATE"


def test_write_gap_audit_once_dedupes(session):
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    args = dict(
        last_tick=date(2026, 5, 18),
        resume_date=date(2026, 5, 21),
        missed_business_days=2,
        timestamp=datetime(2026, 5, 21, 17, 30, tzinfo=UTC),
    )
    repo.write_gap_audit_once(**args)
    repo.write_gap_audit_once(**args)
    rows = session.execute(select(PaperAuditEvent)).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_type == "SCHEDULER_GAP_DETECTED"


def test_write_tick_completed_once_no_op_when_terminal_completed(session):
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    ctx_done = {"tick_date": "2026-05-21", "status": "completed"}
    ts = datetime(2026, 5, 21, 17, 30, tzinfo=UTC)
    repo.write_tick_completed_once(tick_date=date(2026, 5, 21), context=ctx_done, timestamp=ts)
    # Second call with same status is no-op
    repo.write_tick_completed_once(tick_date=date(2026, 5, 21), context=ctx_done, timestamp=ts)
    rows = session.execute(select(PaperAuditEvent)).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_type == "TICK_COMPLETED"


def test_write_tick_completed_once_appends_reprocessed_on_recovery(session):
    """6a-L5 / 6a-L8: completed_with_errors followed by completed appends
    TICK_REPROCESSED_COMPLETED; original row is preserved."""
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    ts = datetime(2026, 5, 21, 17, 30, tzinfo=UTC)
    repo.write_tick_completed_once(
        tick_date=date(2026, 5, 21),
        context={"tick_date": "2026-05-21", "status": "completed_with_errors"},
        timestamp=ts,
    )
    repo.write_tick_completed_once(
        tick_date=date(2026, 5, 21),
        context={"tick_date": "2026-05-21", "status": "completed"},
        timestamp=ts,
    )
    rows = session.execute(select(PaperAuditEvent).order_by(PaperAuditEvent.id)).scalars().all()
    assert len(rows) == 2
    assert rows[0].event_type == "TICK_COMPLETED"
    assert rows[0].context["status"] == "completed_with_errors"
    assert rows[1].event_type == "TICK_REPROCESSED_COMPLETED"
    assert rows[1].context["new_status"] == "completed"
```

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/trading/test_repository.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement Repository basics + audit append + dedup helpers**

```python
# marketpulse/trading/repository.py
"""Phase 6a single-writer surface (lock iii).

The ONLY module allowed to INSERT/UPDATE paper_* tables. Execution-path
reads also live here (find_by_id, find_by_key, etc.). UI/observability
reads will get their own query_models.py later (deferred to 6f/6g).

This module never imports kill_switch, forward_engine, daily_cycle, or
bid_aggregator (layered dependency rule)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from marketpulse.db.models import (
    PaperAuditEvent,
    PaperCashLedger,
    PaperFill,
    PaperOrder,
    PaperPosition,
)
from marketpulse.trading.types import AuditEventType, InvariantError


class Repository:
    def __init__(self, *, session: Session) -> None:
        self._session = session

    @contextmanager
    def transaction(self):
        """Wraps a unit of work. Commits on success, rolls back on exception."""
        try:
            yield self._session
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    # === Audit writers (lock v + xiii append-only) ===

    def write_audit_event(
        self,
        *,
        event_type: AuditEventType,
        order_id: int | None,
        strategy: str | None,
        reason: str,
        context: dict,
        timestamp: datetime,
    ) -> PaperAuditEvent:
        row = PaperAuditEvent(
            timestamp=timestamp,
            event_type=event_type.value,
            order_id=order_id,
            strategy=strategy,
            reason=reason,
            context=context,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def write_duplicate_audit_once(
        self,
        *,
        idempotency_key: str,
        order_id: int,
        strategy: str,
        tick_date: date,
        context: dict,
        timestamp: datetime,
    ) -> None:
        """6a-L5: at most one ORDER_PLACED_DUPLICATE per (idempotency_key,
        tick_date)."""
        existing = self._session.execute(
            select(PaperAuditEvent).where(
                PaperAuditEvent.event_type == AuditEventType.ORDER_PLACED_DUPLICATE.value,
                PaperAuditEvent.context["idempotency_key"].as_string() == idempotency_key,
                PaperAuditEvent.context["tick_date"].as_string() == tick_date.isoformat(),
            )
        ).scalars().first()
        if existing is not None:
            return
        ctx = {**context, "idempotency_key": idempotency_key, "tick_date": tick_date.isoformat()}
        self.write_audit_event(
            event_type=AuditEventType.ORDER_PLACED_DUPLICATE,
            order_id=order_id,
            strategy=strategy,
            reason="idempotent_replay",
            context=ctx,
            timestamp=timestamp,
        )

    def write_gap_audit_once(
        self,
        *,
        last_tick: date,
        resume_date: date,
        missed_business_days: int,
        timestamp: datetime,
    ) -> None:
        """Dedup per (last_tick, resume_date)."""
        existing = self._session.execute(
            select(PaperAuditEvent).where(
                PaperAuditEvent.event_type == AuditEventType.SCHEDULER_GAP_DETECTED.value,
                PaperAuditEvent.context["last_processed_tick_date"].as_string() == last_tick.isoformat(),
                PaperAuditEvent.context["resume_date"].as_string() == resume_date.isoformat(),
            )
        ).scalars().first()
        if existing is not None:
            return
        self.write_audit_event(
            event_type=AuditEventType.SCHEDULER_GAP_DETECTED,
            order_id=None,
            strategy=None,
            reason="forward_only_skip",
            context={
                "last_processed_tick_date": last_tick.isoformat(),
                "resume_date": resume_date.isoformat(),
                "missed_business_days": missed_business_days,
                "mode": "forward_only_skip",
            },
            timestamp=timestamp,
        )

    def write_tick_completed_once(
        self,
        *,
        tick_date: date,
        context: dict,
        timestamp: datetime,
    ) -> None:
        """6a-L5 / 6a-L8 decision table:
            no prior row → append TICK_COMPLETED
            prior=completed → no-op (terminal)
            prior=completed_with_errors + new=completed_with_errors → no-op
            prior=completed_with_errors + new=completed → append TICK_REPROCESSED_COMPLETED
        """
        new_status = context["status"]
        prior = self._session.execute(
            select(PaperAuditEvent).where(
                PaperAuditEvent.event_type == AuditEventType.TICK_COMPLETED.value,
                PaperAuditEvent.context["tick_date"].as_string() == tick_date.isoformat(),
            ).order_by(PaperAuditEvent.id)
        ).scalars().first()

        if prior is None:
            self.write_audit_event(
                event_type=AuditEventType.TICK_COMPLETED,
                order_id=None,
                strategy=None,
                reason="",
                context=context,
                timestamp=timestamp,
            )
            return

        prior_status = prior.context.get("status")
        if prior_status == "completed":
            return  # terminal
        if prior_status == "completed_with_errors" and new_status == "completed_with_errors":
            return  # same state
        if prior_status == "completed_with_errors" and new_status == "completed":
            self.write_audit_event(
                event_type=AuditEventType.TICK_REPROCESSED_COMPLETED,
                order_id=None,
                strategy=None,
                reason="recovered_from_errors",
                context={
                    "tick_date": context["tick_date"],
                    "prior_status": prior_status,
                    "new_status": new_status,
                    "prior_tick_completed_id": prior.id,
                    **context,
                },
                timestamp=timestamp,
            )
            return
        # Other combinations: no-op (defensive).

    # === last_processed_tick_date (6a-L5 + 6a-L8) ===

    def last_processed_tick_date(self) -> date | None:
        """Reads max tick_date from TICK_COMPLETED OR KILL_SWITCH_CYCLE_SKIPPED
        rows. Does NOT include TICK_REPROCESSED_COMPLETED."""
        row = self._session.execute(
            select(PaperAuditEvent).where(
                PaperAuditEvent.event_type.in_([
                    AuditEventType.TICK_COMPLETED.value,
                    AuditEventType.KILL_SWITCH_CYCLE_SKIPPED.value,
                ])
            ).order_by(desc(PaperAuditEvent.id))
        ).scalars().first()
        if row is None:
            return None
        return date.fromisoformat(row.context["tick_date"])

    def latest_tick_status(
        self, tick_date: date,
    ) -> Literal["completed", "completed_with_errors", "reprocessed_completed", "kill_switch_skipped"] | None:
        """For 6g/UI badge rendering."""
        row = self._session.execute(
            select(PaperAuditEvent).where(
                PaperAuditEvent.event_type.in_([
                    AuditEventType.TICK_COMPLETED.value,
                    AuditEventType.TICK_REPROCESSED_COMPLETED.value,
                    AuditEventType.KILL_SWITCH_CYCLE_SKIPPED.value,
                ]),
                PaperAuditEvent.context["tick_date"].as_string() == tick_date.isoformat(),
            ).order_by(desc(PaperAuditEvent.id))
        ).scalars().first()
        if row is None:
            return None
        if row.event_type == AuditEventType.TICK_REPROCESSED_COMPLETED.value:
            return "reprocessed_completed"
        if row.event_type == AuditEventType.KILL_SWITCH_CYCLE_SKIPPED.value:
            return "kill_switch_skipped"
        # TICK_COMPLETED
        s = row.context.get("status")
        return "completed" if s == "completed" else "completed_with_errors"
```

- [ ] **Step 4: Run repository tests**

```bash
uv run pytest tests/trading/test_repository.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/repository.py tests/trading/test_repository.py
git commit -m "feat(6a-2): Repository — transaction, audit append, dedup helpers"
```

### Task 6a-2.2: Repository — paper_order CRUD + status transitions (6a-L6)

**Files:**
- Modify: `marketpulse/trading/repository.py`
- Modify: `tests/trading/test_repository.py`

- [ ] **Step 1: Write transition tests**

```python
# Append to tests/trading/test_repository.py


def _sample_order_request():
    from marketpulse.trading.types import AllocationRunId, OrderRequest
    return OrderRequest(
        strategy="s", ticker="AAPL", quantity=10,
        event_time=datetime(2026, 5, 21, 14, 30, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=Decimal("150"), horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155"),
        allocation_run_id=AllocationRunId("paper-2026-05-21"),
        strategy_version="v0", allocator_version="v0",
        execution_engine_version="v0",
        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
        effective_corr_window=60, rewarded_for_negative_corr=False,
        would_change_rank=False, size_clamped_by_override=False,
    )


def test_insert_paper_order_and_find_by_key(session):
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    req = _sample_order_request()
    with repo.transaction():
        order = repo.insert_paper_order(
            order_request=req,
            idempotency_key="abc123",
            placed_at=datetime(2026, 5, 21, 17, 30, tzinfo=UTC),
        )
    assert order.id is not None
    assert order.status == "PLACED"

    found = repo.find_paper_order_by_idempotency_key("abc123")
    assert found is not None
    assert found.id == order.id


def test_update_paper_order_status_allowed_transitions(session):
    """6a-L6: PLACED → ENTRY_FILLED ok; PLACED → CANCELLED ok."""
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    with repo.transaction():
        order = repo.insert_paper_order(
            order_request=_sample_order_request(),
            idempotency_key="abc123",
            placed_at=datetime(2026, 5, 21, 17, 30, tzinfo=UTC),
        )
    with repo.transaction():
        repo.update_paper_order_status(
            order_id=order.id,
            new_status="ENTRY_FILLED",
            filled_at=datetime(2026, 5, 21, 17, 31, tzinfo=UTC),
        )
    refreshed = repo.find_paper_order_by_id(order.id)
    assert refreshed.status == "ENTRY_FILLED"


def test_update_paper_order_status_illegal_raises(session):
    """6a-L6: ENTRY_FILLED → CANCELLED is illegal (terminal)."""
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.types import InvariantError

    repo = Repository(session=session)
    with repo.transaction():
        order = repo.insert_paper_order(
            order_request=_sample_order_request(),
            idempotency_key="abc",
            placed_at=datetime(2026, 5, 21, 17, 30, tzinfo=UTC),
        )
    with repo.transaction():
        repo.update_paper_order_status(
            order_id=order.id, new_status="ENTRY_FILLED",
            filled_at=datetime(2026, 5, 21, 17, 31, tzinfo=UTC),
        )
    with pytest.raises(InvariantError):
        with repo.transaction():
            repo.update_paper_order_status(
                order_id=order.id, new_status="CANCELLED",
                cancelled_at=datetime(2026, 5, 21, 17, 32, tzinfo=UTC),
                cancel_reason="oops",
            )
```

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/trading/test_repository.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement order CRUD + transition table**

```python
# Append to marketpulse/trading/repository.py

# Allowed status transitions (6a-L6)
_ALLOWED_ORDER_TRANSITIONS = {
    ("PLACED", "ENTRY_FILLED"),
    ("PLACED", "CANCELLED"),
}
_ALLOWED_POSITION_TRANSITIONS = {
    ("OPEN", "CLOSED"),
}


class _RepositoryMethods:
    """Methods appended to Repository via monkey-patch trick — replaced by
    actual method definitions inline. See class body below."""


# Actually, just continue the Repository class definition above.
# (The plan presents these as a continuation of the existing class.)


def insert_paper_order(
    self,
    *,
    order_request,
    idempotency_key: str,
    placed_at: datetime,
) -> PaperOrder:
    row = PaperOrder(
        idempotency_key=idempotency_key,
        allocation_run_id=order_request.allocation_run_id,
        strategy=order_request.strategy,
        ticker=order_request.ticker,
        quantity=order_request.quantity,
        event_time=order_request.event_time,
        allocation_date=order_request.allocation_date,
        horizon_date=order_request.horizon_date,
        placed_at=placed_at,
        event_price=order_request.event_price,
        horizon_price=order_request.horizon_price,
        status="PLACED",
        strategy_version=order_request.strategy_version,
        allocator_version=order_request.allocator_version,
        execution_engine_version=order_request.execution_engine_version,
        weight=order_request.weight,
        raw_bid_weight=order_request.raw_bid_weight,
        pool_corr=order_request.pool_corr,
        contribution_multiplier=order_request.contribution_multiplier,
        adjusted_bid_weight=order_request.adjusted_bid_weight,
        effective_corr_window=order_request.effective_corr_window,
        rewarded_for_negative_corr=order_request.rewarded_for_negative_corr,
        would_change_rank=order_request.would_change_rank,
        size_clamped_by_override=order_request.size_clamped_by_override,
    )
    self._session.add(row)
    self._session.flush()
    return row


def find_paper_order_by_id(self, order_id: int) -> PaperOrder | None:
    return self._session.get(PaperOrder, order_id)


def find_paper_order_by_idempotency_key(self, key: str) -> PaperOrder | None:
    return self._session.execute(
        select(PaperOrder).where(PaperOrder.idempotency_key == key)
    ).scalars().first()


def update_paper_order_status(
    self,
    *,
    order_id: int,
    new_status: str,
    filled_at: datetime | None = None,
    cancelled_at: datetime | None = None,
    cancel_reason: str | None = None,
) -> PaperOrder:
    order = self.find_paper_order_by_id(order_id)
    if order is None:
        raise InvariantError(f"unknown order_id={order_id}")
    if (order.status, new_status) not in _ALLOWED_ORDER_TRANSITIONS:
        raise InvariantError(
            f"illegal status transition {order.status!r} → {new_status!r} on order {order_id}"
        )
    order.status = new_status
    if filled_at is not None:
        order.filled_at = filled_at
    if cancelled_at is not None:
        order.cancelled_at = cancelled_at
    if cancel_reason is not None:
        order.cancel_reason = cancel_reason
    self._session.flush()
    return order
```

Add these as methods inside the `Repository` class (not module-level functions). The plan above shows them flat for readability; integrate them under `class Repository:` with `self` as the first param. Same applies to all subsequent repository methods.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/trading/test_repository.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/repository.py tests/trading/test_repository.py
git commit -m "feat(6a-2): repository.insert_paper_order + status transitions (6a-L6)"
```

### Task 6a-2.3: Repository — fill / position / cash ledger writers

**Files:**
- Modify: `marketpulse/trading/repository.py`
- Modify: `tests/trading/test_repository.py`

- [ ] **Step 1: Write tests**

```python
# Append to tests/trading/test_repository.py


def test_insert_position_then_fill_then_update_entry_fill(session):
    """Tests the ENTRY-flow ordering from spec § 4.6."""
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    with repo.transaction():
        order = repo.insert_paper_order(
            order_request=_sample_order_request(),
            idempotency_key="abc",
            placed_at=datetime(2026, 5, 21, 17, 30, tzinfo=UTC),
        )
        # 1. Insert position with entry_fill_id=NULL
        position = repo.insert_paper_position(
            order_id=order.id, strategy="s", ticker="AAPL",
            quantity=10, entry_price=Decimal("150"),
            entry_date=date(2026, 5, 21), horizon_date=date(2026, 5, 28),
            opened_at=datetime(2026, 5, 21, 17, 31, tzinfo=UTC),
        )
        assert position.status == "OPEN"
        assert position.entry_fill_id is None

        # 2. Insert ENTRY fill referencing position
        fill = repo.insert_paper_fill(
            order_id=order.id, position_id=position.id,
            side="ENTRY", price=Decimal("150"), quantity=10,
            filled_at=datetime(2026, 5, 21, 17, 31, tzinfo=UTC),
            cash_delta=Decimal("-1500"), realized_pnl=None,
        )

        # 3. UPDATE position.entry_fill_id
        repo.update_paper_position_entry_fill(position_id=position.id, entry_fill_id=fill.id)

    refreshed = session.get(type(position), position.id)
    assert refreshed.entry_fill_id == fill.id


def test_cash_ledger_computes_balance_inside_transaction(session):
    """6a round-3 fix: repository computes balance_after; engine passes
    only delta."""
    from marketpulse.db.models import PaperCashLedger
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    # Seed initial deposit
    with repo.transaction():
        repo.insert_cash_ledger_entry_for_fill(
            timestamp=datetime(2026, 5, 21, 0, 0, tzinfo=UTC),
            delta=Decimal("10000"),
            reason="INITIAL_DEPOSIT",
            fill_id=None,
        )

    # Add an entry fill cash outflow
    with repo.transaction():
        repo.insert_cash_ledger_entry_for_fill(
            timestamp=datetime(2026, 5, 21, 17, 31, tzinfo=UTC),
            delta=Decimal("-1500"),
            reason="ENTRY_FILL",
            fill_id=1,  # not enforced as FK in test fixture
        )

    rows = session.execute(select(PaperCashLedger).order_by(PaperCashLedger.id)).scalars().all()
    assert rows[0].balance_after == Decimal("10000")
    assert rows[1].balance_after == Decimal("8500")
    assert repo.cash_balance() == Decimal("8500")


def test_ensure_initial_deposit_strictly_idempotent(session):
    """3 consecutive calls produce exactly 1 INITIAL_DEPOSIT row.
    Locks the at-most-once contract; on SQLite the write-lock
    serializes the count→insert sequence safely."""
    from sqlalchemy import select

    from marketpulse.db.models import PaperCashLedger
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    ts = datetime(2026, 5, 21, tzinfo=UTC)
    for _ in range(3):
        repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=ts)
    rows = session.execute(
        select(PaperCashLedger).where(PaperCashLedger.reason == "INITIAL_DEPOSIT")
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].delta == Decimal("10000")
    assert rows[0].balance_after == Decimal("10000")
```

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/trading/test_repository.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement fill/position/cash methods on Repository**

```python
# Inside class Repository (continuing the existing class):

def insert_paper_position(
    self,
    *,
    order_id: int,
    strategy: str,
    ticker: str,
    quantity: int,
    entry_price: Decimal,
    entry_date: date,
    horizon_date: date,
    opened_at: datetime,
) -> PaperPosition:
    row = PaperPosition(
        order_id=order_id,
        entry_fill_id=None,
        exit_fill_id=None,
        strategy=strategy,
        ticker=ticker,
        quantity=quantity,
        entry_price=entry_price,
        entry_date=entry_date,
        horizon_date=horizon_date,
        status="OPEN",
        opened_at=opened_at,
    )
    self._session.add(row)
    self._session.flush()
    return row


def update_paper_position_entry_fill(self, *, position_id: int, entry_fill_id: int) -> None:
    pos = self._session.get(PaperPosition, position_id)
    if pos is None:
        raise InvariantError(f"unknown position_id={position_id}")
    pos.entry_fill_id = entry_fill_id
    self._session.flush()


def update_paper_position_exit(
    self,
    *,
    position_id: int,
    exit_fill_id: int,
    exit_price: Decimal,
    realized_pnl: Decimal,
    closed_at: datetime,
) -> None:
    pos = self._session.get(PaperPosition, position_id)
    if pos is None:
        raise InvariantError(f"unknown position_id={position_id}")
    if (pos.status, "CLOSED") not in _ALLOWED_POSITION_TRANSITIONS:
        raise InvariantError(f"illegal position transition {pos.status!r} → CLOSED")
    pos.exit_fill_id = exit_fill_id
    pos.exit_price = exit_price
    pos.realized_pnl = realized_pnl
    pos.closed_at = closed_at
    pos.status = "CLOSED"
    self._session.flush()


def insert_paper_fill(
    self,
    *,
    order_id: int,
    position_id: int,
    side: str,
    price: Decimal,
    quantity: int,
    filled_at: datetime,
    cash_delta: Decimal,
    realized_pnl: Decimal | None,
) -> PaperFill:
    row = PaperFill(
        order_id=order_id,
        position_id=position_id,
        side=side,
        price=price,
        quantity=quantity,
        filled_at=filled_at,
        cash_delta=cash_delta,
        realized_pnl=realized_pnl,
    )
    self._session.add(row)
    self._session.flush()
    return row


def insert_cash_ledger_entry_for_fill(
    self,
    *,
    timestamp: datetime,
    delta: Decimal,
    reason: str,
    fill_id: int | None,
) -> PaperCashLedger:
    """Repository computes balance_after inside the transaction (round-3
    lock). Engine never juggles balance arithmetic."""
    latest = self._session.execute(
        select(PaperCashLedger).order_by(desc(PaperCashLedger.id))
    ).scalars().first()
    prior_balance = latest.balance_after if latest is not None else Decimal("0")
    new_balance = prior_balance + delta
    row = PaperCashLedger(
        timestamp=timestamp,
        delta=delta,
        reason=reason,
        fill_id=fill_id,
        balance_after=new_balance,
    )
    self._session.add(row)
    self._session.flush()
    return row


def cash_balance(self) -> Decimal:
    """Lock xxi: latest balance_after by monotonic id."""
    latest = self._session.execute(
        select(PaperCashLedger).order_by(desc(PaperCashLedger.id))
    ).scalars().first()
    return latest.balance_after if latest is not None else Decimal("0")


def ensure_initial_deposit(self, *, amount: Decimal, timestamp: datetime) -> None:
    """Idempotent. Called at app startup. Uses self.transaction() — no
    naked commit (round-3 fix)."""
    from sqlalchemy import func
    with self.transaction():
        count = self._session.execute(
            select(func.count(PaperCashLedger.id))
        ).scalar()
        if count == 0:
            self._session.add(PaperCashLedger(
                timestamp=timestamp,
                delta=amount,
                reason="INITIAL_DEPOSIT",
                fill_id=None,
                balance_after=amount,
            ))


def find_orders_for_entry(self, *, as_of: date) -> list[PaperOrder]:
    """tick() Phase A query: PLACED orders with allocation_date <= as_of."""
    return list(self._session.execute(
        select(PaperOrder)
        .where(PaperOrder.status == "PLACED")
        .where(PaperOrder.allocation_date <= as_of)
        .order_by(PaperOrder.id)
    ).scalars().all())


def find_positions_for_exit(self, *, as_of: date) -> list[PaperPosition]:
    """tick() Phase B query: OPEN positions with horizon_date <= as_of."""
    return list(self._session.execute(
        select(PaperPosition)
        .where(PaperPosition.status == "OPEN")
        .where(PaperPosition.horizon_date <= as_of)
        .order_by(PaperPosition.id)
    ).scalars().all())


def open_positions_snapshot(self) -> list[PaperPosition]:
    return list(self._session.execute(
        select(PaperPosition).where(PaperPosition.status == "OPEN")
        .order_by(PaperPosition.id)
    ).scalars().all())


def count_positions_status(self, status: str) -> int:
    from sqlalchemy import func
    return self._session.execute(
        select(func.count(PaperPosition.id))
        .where(PaperPosition.status == status)
    ).scalar() or 0
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/trading/test_repository.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/repository.py tests/trading/test_repository.py
git commit -m "feat(6a-2): repository fill/position/cash ledger writers"
```

### Task 6a-2.4: Kill switch — env + DB flag

**Files:**
- Modify: `marketpulse/trading/kill_switch.py`
- Create: `tests/trading/test_kill_switch.py`

- [ ] **Step 1: Write tests**

```python
# tests/trading/test_kill_switch.py
# Layer: behavioral

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base
    eng = create_engine(f"sqlite:///{tmp_path / 'ks.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def test_kill_switch_env_var_force_on(monkeypatch, session):
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.repository import Repository

    monkeypatch.setenv("MP_PAPER_KILL_SWITCH", "1")
    repo = Repository(session=session)
    ks = KillSwitchState(env_var="MP_PAPER_KILL_SWITCH", repository=repo)
    assert ks.is_active() is True


def test_kill_switch_db_flag(monkeypatch, session):
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.repository import Repository

    monkeypatch.delenv("MP_PAPER_KILL_SWITCH", raising=False)
    repo = Repository(session=session)
    ks = KillSwitchState(env_var="MP_PAPER_KILL_SWITCH", repository=repo)
    assert ks.is_active() is False

    ks.flip(
        new_state=True,
        reason="manual",
        actor="test",
        timestamp=datetime(2026, 5, 21, 17, 30, tzinfo=UTC),
    )
    # New repo instance to confirm DB persisted
    repo2 = Repository(session=session)
    ks2 = KillSwitchState(env_var="MP_PAPER_KILL_SWITCH", repository=repo2)
    assert ks2.is_active() is True


def test_kill_switch_flip_writes_audit(monkeypatch, session):
    from marketpulse.db.models import PaperAuditEvent
    from sqlalchemy import select

    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.repository import Repository

    monkeypatch.delenv("MP_PAPER_KILL_SWITCH", raising=False)
    repo = Repository(session=session)
    ks = KillSwitchState(env_var="MP_PAPER_KILL_SWITCH", repository=repo)
    ks.flip(
        new_state=True, reason="manual", actor="alice",
        timestamp=datetime(2026, 5, 21, 17, 30, tzinfo=UTC),
    )
    rows = session.execute(select(PaperAuditEvent)).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_type == "KILL_SWITCH_FLIPPED"
    assert rows[0].context["actor"] == "alice"
    assert rows[0].context["to_state"] is True
```

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/trading/test_kill_switch.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement KillSwitchState**

```python
# marketpulse/trading/kill_switch.py
"""Phase 6a kill switch — env-var force-on + DB persisted flag.

Precedence: env var True → always active. Otherwise read DB.
Flip writes a KILL_SWITCH_FLIPPED audit row through the repository
(single-writer surface). See lock 6a-L8 for the two-layer enforcement
contract (cycle-level skip in daily_cycle + per-order check inside
ForwardExecutionEngine.place_order)."""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import desc, select

from marketpulse.db.models import PaperAuditEvent
from marketpulse.trading.repository import Repository
from marketpulse.trading.types import AuditEventType


class KillSwitchState:
    def __init__(self, *, env_var: str, repository: Repository) -> None:
        self._env_var = env_var
        self._repo = repository

    def _env_truthy(self) -> bool:
        v = os.environ.get(self._env_var, "")
        return v.lower() in ("1", "true", "yes", "on")

    def _db_state(self) -> bool:
        """Read latest KILL_SWITCH_FLIPPED; True if the latest flip set
        to_state=True. None → False (never flipped)."""
        row = self._repo._session.execute(
            select(PaperAuditEvent).where(
                PaperAuditEvent.event_type == AuditEventType.KILL_SWITCH_FLIPPED.value
            ).order_by(desc(PaperAuditEvent.id))
        ).scalars().first()
        if row is None:
            return False
        return bool(row.context.get("to_state"))

    def is_active(self) -> bool:
        if self._env_truthy():
            return True
        return self._db_state()

    def flip(
        self,
        *,
        new_state: bool,
        reason: str,
        actor: str,
        timestamp: datetime,
    ) -> None:
        with self._repo.transaction():
            self._repo.write_audit_event(
                event_type=AuditEventType.KILL_SWITCH_FLIPPED,
                order_id=None,
                strategy=None,
                reason=reason,
                context={
                    "from_state": self._db_state(),
                    "to_state": new_state,
                    "actor": actor,
                },
                timestamp=timestamp,
            )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/trading/test_kill_switch.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/kill_switch.py tests/trading/test_kill_switch.py
git commit -m "feat(6a-2): KillSwitchState — env + DB + KILL_SWITCH_FLIPPED audit"
```

### Task 6a-2.5: ForwardExecutionEngine — place_order

**Files:**
- Modify: `marketpulse/trading/forward_engine.py`
- Create: `tests/trading/test_forward_engine.py`

- [ ] **Step 1: Write place_order tests**

```python
# tests/trading/test_forward_engine.py
# Layer: behavioral

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base
    eng = create_engine(f"sqlite:///{tmp_path / 'fe.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _engine(session, *, kill_active=False):
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gate import AlwaysApproveRiskGate

    repo = Repository(session=session)
    clock = FakeClock(now=datetime(2026, 5, 21, 17, 30, tzinfo=UTC))
    ks = KillSwitchState(env_var="MP_NEVER_SET_KS", repository=repo)
    if kill_active:
        ks.flip(new_state=True, reason="test", actor="test", timestamp=clock.now())
    engine = ForwardExecutionEngine(
        repository=repo, clock=clock,
        kill_switch=ks, risk_gate=AlwaysApproveRiskGate(),
    )
    return engine, repo, clock, ks


def _request(strategy="s", run_id="paper-2026-05-21"):
    from marketpulse.trading.types import AllocationRunId, OrderRequest
    return OrderRequest(
        strategy=strategy, ticker="AAPL", quantity=10,
        event_time=datetime(2026, 5, 21, 14, 30, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=Decimal("150"), horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155"),
        allocation_run_id=AllocationRunId(run_id),
        strategy_version="v0", allocator_version="v0",
        execution_engine_version="v0",
        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
        effective_corr_window=60, rewarded_for_negative_corr=False,
        would_change_rank=False, size_clamped_by_override=False,
    )


def test_place_order_accepted_writes_order_and_audit(session):
    """6a-L2: PlaceOrderResult(created=True, duplicate=False) on first call."""
    from marketpulse.db.models import PaperAuditEvent, PaperOrder

    engine, _, _, _ = _engine(session)
    result = engine.place_order(order_request=_request())
    assert result.created is True
    assert result.duplicate is False

    orders = session.execute(select(PaperOrder)).scalars().all()
    assert len(orders) == 1

    audits = session.execute(select(PaperAuditEvent)).scalars().all()
    assert any(a.event_type == "ORDER_PLACED" for a in audits)


def test_place_order_idempotency_hit_returns_duplicate(session):
    """6a-L2: second call with same key returns (created=False, duplicate=True)."""
    engine, _, _, _ = _engine(session)
    r1 = engine.place_order(order_request=_request())
    r2 = engine.place_order(order_request=_request())
    assert r1.order_id == r2.order_id
    assert r2.created is False
    assert r2.duplicate is True


def test_place_order_idempotency_writes_duplicate_audit_once(session):
    """6a-L5: ORDER_PLACED_DUPLICATE deduped per (key, tick_date).
    Three replays produce 1 audit row total."""
    from marketpulse.db.models import PaperAuditEvent

    engine, _, _, _ = _engine(session)
    engine.place_order(order_request=_request())
    engine.place_order(order_request=_request())
    engine.place_order(order_request=_request())

    dup_audits = session.execute(
        select(PaperAuditEvent).where(PaperAuditEvent.event_type == "ORDER_PLACED_DUPLICATE")
    ).scalars().all()
    assert len(dup_audits) == 1


def test_place_order_kill_switch_active_rejects(session):
    """Kill switch active → ORDER_REJECTED audit + OrderRejected raised."""
    from marketpulse.db.models import PaperAuditEvent, PaperOrder
    from marketpulse.trading.types import OrderRejected

    engine, _, _, _ = _engine(session, kill_active=True)
    with pytest.raises(OrderRejected, match="kill_switch_active"):
        engine.place_order(order_request=_request())

    orders = session.execute(select(PaperOrder)).scalars().all()
    assert len(orders) == 0

    rejects = session.execute(
        select(PaperAuditEvent).where(PaperAuditEvent.event_type == "ORDER_REJECTED")
    ).scalars().all()
    assert len(rejects) == 1
    assert rejects[0].reason == "kill_switch_active"


def test_place_order_risk_gate_exception_fail_closed(session):
    """6a-L3: arbitrary risk_gate exception → ORDER_REJECTED("risk_gate_error")."""
    from marketpulse.db.models import PaperAuditEvent, PaperOrder
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.types import OrderRejected

    class BoomGate:
        def check_pre_trade(self, *, order_request):
            raise RuntimeError("boom")

    repo = Repository(session=session)
    clock = FakeClock(now=datetime(2026, 5, 21, 17, 30, tzinfo=UTC))
    engine = ForwardExecutionEngine(
        repository=repo, clock=clock,
        kill_switch=KillSwitchState(env_var="MP_NEVER", repository=repo),
        risk_gate=BoomGate(),
    )

    with pytest.raises(OrderRejected, match="risk_gate_error"):
        engine.place_order(order_request=_request())

    orders = session.execute(select(PaperOrder)).scalars().all()
    assert len(orders) == 0

    rejects = session.execute(
        select(PaperAuditEvent).where(PaperAuditEvent.event_type == "ORDER_REJECTED")
    ).scalars().all()
    assert len(rejects) == 1
    assert rejects[0].reason == "risk_gate_error"
    assert rejects[0].context["error_type"] == "RuntimeError"


def test_rejection_audit_committed_before_exception(session, monkeypatch):
    """Lock ix / 6a-L3: ORDER_REJECTED audit row MUST be committed (visible
    in a fresh session) before OrderRejected is raised. If audit fails
    to commit, the engine surfaces the DB error rather than pretending
    the order was rejected."""
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gate import RiskResult
    from marketpulse.trading.types import OrderRejected

    captured_audit_ids: list[int] = []

    class _RejectGate:
        def check_pre_trade(self, *, order_request):
            return RiskResult(approved=False, reason="test_block", gate_name="g")

    repo = Repository(session=session)
    # Spy on repo.write_audit_event — capture id immediately after flush.
    real_write = repo.write_audit_event

    def spy(*args, **kwargs):
        row = real_write(*args, **kwargs)
        # At this point the row has an id (post-flush). The OUTER
        # transaction has not yet committed; the assertion below uses a
        # fresh session to confirm the commit happened before the raise.
        captured_audit_ids.append(row.id)
        return row

    monkeypatch.setattr(repo, "write_audit_event", spy)

    engine = ForwardExecutionEngine(
        repository=repo,
        clock=FakeClock(now=datetime(2026, 5, 21, 17, 30, tzinfo=UTC)),
        kill_switch=KillSwitchState(env_var="MP_NEVER", repository=repo),
        risk_gate=_RejectGate(),
    )

    with pytest.raises(OrderRejected, match="test_block"):
        engine.place_order(order_request=_request())

    # Same-session check: the audit row must be visible after the raise.
    assert len(captured_audit_ids) == 1

    # Fresh-session check: the audit row must have been committed
    # (would be invisible to a new session if still in-flight).
    from sqlalchemy import create_engine as _ce
    fresh_eng = session.bind
    with Session(fresh_eng) as fresh:
        rows = fresh.execute(
            select(PaperAuditEvent).where(PaperAuditEvent.id == captured_audit_ids[0])
        ).scalars().all()
        assert len(rows) == 1, (
            "ORDER_REJECTED audit must commit BEFORE OrderRejected is raised "
            "(lock ix). A rejection without a committed audit is not a valid "
            "completed rejection."
        )
```

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/trading/test_forward_engine.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement ForwardExecutionEngine.place_order**

```python
# marketpulse/trading/forward_engine.py
"""ForwardExecutionEngine — the ONLY Phase 6 ExecutionEngine implementation.

Per spec § 6 + locks ix, xxvii, xxx, xxiv, 6a-L2, 6a-L3, 6a-L4."""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal

from marketpulse.trading.clock import Clock
from marketpulse.trading.idempotency import compute_idempotency_key
from marketpulse.trading.kill_switch import KillSwitchState
from marketpulse.trading.repository import Repository
from marketpulse.trading.risk_gate import RiskGate
from marketpulse.trading.types import (
    AuditEventType,
    InvariantError,
    OrderId,
    OrderRejected,
    OrderRequest,
    PlaceOrderResult,
    TickError,
    TickResult,
)


VERSION = "v0"


def _dump(order_request: OrderRequest) -> dict:
    d = dataclasses.asdict(order_request)
    # Make Decimal/datetime JSON-friendly
    for k, v in list(d.items()):
        if isinstance(v, Decimal):
            d[k] = str(v)
        elif hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d


class ForwardExecutionEngine:
    VERSION = VERSION

    def __init__(
        self,
        *,
        repository: Repository,
        clock: Clock,
        kill_switch: KillSwitchState,
        risk_gate: RiskGate,
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._kill_switch = kill_switch
        self._risk_gate = risk_gate

    def place_order(self, *, order_request: OrderRequest) -> PlaceOrderResult:
        key = compute_idempotency_key(order_request)

        # Step 2: idempotency hit
        existing = self._repo.find_paper_order_by_idempotency_key(key)
        if existing is not None:
            with self._repo.transaction():
                self._repo.write_duplicate_audit_once(
                    idempotency_key=key,
                    order_id=existing.id,
                    strategy=order_request.strategy,
                    tick_date=order_request.allocation_date,
                    context={"allocation_run_id": order_request.allocation_run_id},
                    timestamp=self._clock.now(),
                )
            return PlaceOrderResult(
                order_id=OrderId(existing.id), created=False, duplicate=True,
            )

        # Step 3: kill switch
        if self._kill_switch.is_active():
            with self._repo.transaction():
                self._repo.write_audit_event(
                    event_type=AuditEventType.ORDER_REJECTED,
                    order_id=None,
                    strategy=order_request.strategy,
                    reason="kill_switch_active",
                    context={"order_request": _dump(order_request)},
                    timestamp=self._clock.now(),
                )
            raise OrderRejected("kill_switch_active")

        # Step 4: risk gate — fail-closed exception path (6a-L3)
        try:
            risk_result = self._risk_gate.check_pre_trade(order_request=order_request)
        except Exception as e:
            with self._repo.transaction():
                self._repo.write_audit_event(
                    event_type=AuditEventType.ORDER_REJECTED,
                    order_id=None,
                    strategy=order_request.strategy,
                    reason="risk_gate_error",
                    context={
                        "order_request": _dump(order_request),
                        "error_type": type(e).__name__,
                        "error": str(e),
                    },
                    timestamp=self._clock.now(),
                )
            raise OrderRejected("risk_gate_error") from e

        if not risk_result.approved:
            with self._repo.transaction():
                self._repo.write_audit_event(
                    event_type=AuditEventType.ORDER_REJECTED,
                    order_id=None,
                    strategy=order_request.strategy,
                    reason=risk_result.reason,
                    context={
                        "order_request": _dump(order_request),
                        "gate": risk_result.gate_name,
                    },
                    timestamp=self._clock.now(),
                )
            raise OrderRejected(risk_result.reason)

        # Step 5: accepted — atomic INSERT order + ORDER_PLACED audit
        with self._repo.transaction():
            order = self._repo.insert_paper_order(
                order_request=order_request,
                idempotency_key=key,
                placed_at=self._clock.now(),
            )
            self._repo.write_audit_event(
                event_type=AuditEventType.ORDER_PLACED,
                order_id=order.id,
                strategy=order_request.strategy,
                reason="",
                context={
                    "idempotency_key": key,
                    "allocation_run_id": order_request.allocation_run_id,
                },
                timestamp=self._clock.now(),
            )

        return PlaceOrderResult(order_id=OrderId(order.id), created=True, duplicate=False)

    def cancel_order(self, *, order_id: OrderId) -> None:
        """Filled in Task 6a-2.6."""
        raise NotImplementedError

    def tick(self, *, as_of: date) -> TickResult:
        """Filled in Task 6a-2.7."""
        raise NotImplementedError
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/trading/test_forward_engine.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/forward_engine.py tests/trading/test_forward_engine.py
git commit -m "feat(6a-2): ForwardExecutionEngine.place_order (locks ix/xxx/xxvii/6a-L2/L3/L5)"
```

### Task 6a-2.6: ForwardExecutionEngine — cancel_order

**Files:**
- Modify: `marketpulse/trading/forward_engine.py`
- Modify: `tests/trading/test_forward_engine.py`

- [ ] **Step 1: Write tests**

```python
# Append to tests/trading/test_forward_engine.py

def test_cancel_order_flips_placed_to_cancelled(session):
    from marketpulse.db.models import PaperAuditEvent, PaperOrder
    from sqlalchemy import select

    engine, repo, _, _ = _engine(session)
    result = engine.place_order(order_request=_request())
    engine.cancel_order(order_id=result.order_id)

    order = repo.find_paper_order_by_id(int(result.order_id))
    assert order.status == "CANCELLED"

    audits = session.execute(
        select(PaperAuditEvent).where(PaperAuditEvent.event_type == "ORDER_CANCELLED")
    ).scalars().all()
    assert len(audits) == 1


def test_cancel_order_idempotent_on_already_cancelled(session):
    engine, repo, _, _ = _engine(session)
    result = engine.place_order(order_request=_request())
    engine.cancel_order(order_id=result.order_id)
    # Second call: no-op
    engine.cancel_order(order_id=result.order_id)
    order = repo.find_paper_order_by_id(int(result.order_id))
    assert order.status == "CANCELLED"
```

- [ ] **Step 2: Implement cancel_order**

```python
# Replace the placeholder cancel_order with this body:

def cancel_order(self, *, order_id):
    order = self._repo.find_paper_order_by_id(int(order_id))
    if order is None:
        raise ValueError(f"unknown order_id={order_id}")
    if order.status in ("ENTRY_FILLED", "CANCELLED"):
        return  # idempotent no-op
    with self._repo.transaction():
        self._repo.update_paper_order_status(
            order_id=order.id,
            new_status="CANCELLED",
            cancelled_at=self._clock.now(),
            cancel_reason="manual_cancel",
        )
        self._repo.write_audit_event(
            event_type=AuditEventType.ORDER_CANCELLED,
            order_id=order.id,
            strategy=order.strategy,
            reason="manual_cancel",
            context={"prior_status": "PLACED"},
            timestamp=self._clock.now(),
        )
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/trading/test_forward_engine.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add marketpulse/trading/forward_engine.py tests/trading/test_forward_engine.py
git commit -m "feat(6a-2): ForwardExecutionEngine.cancel_order"
```

### Task 6a-2.7: ForwardExecutionEngine — tick (entry + exit materialization)

**Files:**
- Modify: `marketpulse/trading/forward_engine.py`
- Modify: `tests/trading/test_forward_engine.py`

- [ ] **Step 1: Write tick tests**

```python
# Append to tests/trading/test_forward_engine.py
# Layer: stateful

def test_tick_materializes_entry_then_exit(session):
    """E2E single-position lifecycle through tick()."""
    from datetime import timedelta
    from sqlalchemy import select
    from marketpulse.db.models import PaperCashLedger, PaperFill, PaperPosition

    engine, repo, clock, _ = _engine(session)

    # Initial deposit
    repo.ensure_initial_deposit(
        amount=Decimal("10000"), timestamp=clock.now(),
    )

    # Place order
    engine.place_order(order_request=_request())

    # tick on allocation_date → ENTRY materialization
    r1 = engine.tick(as_of=date(2026, 5, 21))
    assert r1.entries_materialized == 1
    assert r1.exits_materialized == 0
    assert r1.errors == ()

    pos = session.execute(select(PaperPosition)).scalars().first()
    assert pos.status == "OPEN"
    assert pos.entry_fill_id is not None

    fills = session.execute(select(PaperFill).order_by(PaperFill.id)).scalars().all()
    assert len(fills) == 1
    assert fills[0].side == "ENTRY"

    cash_rows = session.execute(select(PaperCashLedger).order_by(PaperCashLedger.id)).scalars().all()
    # Initial 10000, then -1500 entry
    assert cash_rows[-1].balance_after == Decimal("8500")

    # tick on horizon_date → EXIT
    r2 = engine.tick(as_of=date(2026, 5, 28))
    assert r2.exits_materialized == 1

    pos = session.execute(select(PaperPosition)).scalars().first()
    assert pos.status == "CLOSED"

    fills = session.execute(select(PaperFill).order_by(PaperFill.id)).scalars().all()
    assert len(fills) == 2
    assert fills[1].side == "EXIT"
    # PnL: (155 - 150) * 10 = 50
    assert fills[1].realized_pnl == Decimal("50")

    assert repo.cash_balance() == Decimal("10050")


def test_tick_is_idempotent(session):
    """Calling tick twice for the same date produces the same state."""
    engine, repo, _, _ = _engine(session)
    repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=datetime(2026, 5, 21, tzinfo=UTC))
    engine.place_order(order_request=_request())

    r1 = engine.tick(as_of=date(2026, 5, 21))
    r2 = engine.tick(as_of=date(2026, 5, 21))
    assert r1.entries_materialized == 1
    assert r2.entries_materialized == 0  # no rows to flip
    assert r2.exits_materialized == 0


def test_tick_invariant_error_writes_audit_and_continues(session):
    """6a-L4: horizon_price is None → ENGINE_INVARIANT_ERROR audit; other
    positions in the same tick keep processing."""
    from sqlalchemy import select
    from marketpulse.db.models import PaperAuditEvent

    engine, repo, _, _ = _engine(session)
    repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=datetime(2026, 5, 21, tzinfo=UTC))

    # Place a normal order
    engine.place_order(order_request=_request(strategy="ok"))
    engine.tick(as_of=date(2026, 5, 21))  # materialize entry

    # Manually corrupt horizon_price to None on the order
    from marketpulse.db.models import PaperOrder
    bad = session.execute(select(PaperOrder)).scalars().first()
    bad.horizon_price = None
    session.commit()

    # Now tick the horizon → should record ENGINE_INVARIANT_ERROR
    result = engine.tick(as_of=date(2026, 5, 28))
    assert len(result.errors) == 1
    assert result.errors[0].phase == "exit_materialization"

    audits = session.execute(
        select(PaperAuditEvent).where(PaperAuditEvent.event_type == "ENGINE_INVARIANT_ERROR")
    ).scalars().all()
    assert len(audits) == 1
```

- [ ] **Step 2: Implement tick**

Replace the placeholder `tick` and add `_materialize_entry` + `_materialize_exit` on `ForwardExecutionEngine`:

```python
# Inside class ForwardExecutionEngine (continue):

def tick(self, *, as_of: date) -> TickResult:
    entries = 0
    exits = 0
    errors: list[TickError] = []

    # Phase A: entries
    for order in self._repo.find_orders_for_entry(as_of=as_of):
        try:
            self._materialize_entry(order, fill_date=as_of)
            entries += 1
        except InvariantError as e:
            err = TickError(
                phase="entry_materialization",
                order_id=order.id, position_id=None, error=str(e),
            )
            errors.append(err)
            with self._repo.transaction():
                self._repo.write_audit_event(
                    event_type=AuditEventType.ENGINE_INVARIANT_ERROR,
                    order_id=order.id, strategy=order.strategy,
                    reason="invariant_error",
                    context={
                        "phase": err.phase, "order_id": err.order_id,
                        "error": err.error, "as_of": as_of.isoformat(),
                    },
                    timestamp=self._clock.now(),
                )

    # Phase B: exits
    for position in self._repo.find_positions_for_exit(as_of=as_of):
        try:
            self._materialize_exit(position, exit_date=as_of)
            exits += 1
        except InvariantError as e:
            err = TickError(
                phase="exit_materialization",
                order_id=position.order_id, position_id=position.id, error=str(e),
            )
            errors.append(err)
            with self._repo.transaction():
                self._repo.write_audit_event(
                    event_type=AuditEventType.ENGINE_INVARIANT_ERROR,
                    order_id=position.order_id, strategy=position.strategy,
                    reason="invariant_error",
                    context={
                        "phase": err.phase, "position_id": err.position_id,
                        "order_id": err.order_id, "error": err.error,
                        "as_of": as_of.isoformat(),
                    },
                    timestamp=self._clock.now(),
                )

    return TickResult(
        as_of=as_of,
        entries_materialized=entries,
        exits_materialized=exits,
        errors=tuple(errors),
    )


def _materialize_entry(self, order, *, fill_date: date) -> None:
    fill_time = self._clock.now()
    fill_price = order.event_price
    cash_outflow = fill_price * Decimal(order.quantity)

    with self._repo.transaction():
        position = self._repo.insert_paper_position(
            order_id=order.id, strategy=order.strategy, ticker=order.ticker,
            quantity=order.quantity, entry_price=fill_price,
            entry_date=fill_date, horizon_date=order.horizon_date,
            opened_at=fill_time,
        )
        fill = self._repo.insert_paper_fill(
            order_id=order.id, position_id=position.id,
            side="ENTRY", price=fill_price, quantity=order.quantity,
            filled_at=fill_time, cash_delta=-cash_outflow, realized_pnl=None,
        )
        self._repo.update_paper_position_entry_fill(
            position_id=position.id, entry_fill_id=fill.id,
        )
        self._repo.insert_cash_ledger_entry_for_fill(
            timestamp=fill_time, delta=-cash_outflow,
            reason="ENTRY_FILL", fill_id=fill.id,
        )
        self._repo.update_paper_order_status(
            order_id=order.id, new_status="ENTRY_FILLED", filled_at=fill_time,
        )
        self._repo.write_audit_event(
            event_type=AuditEventType.ORDER_ENTRY_FILLED,
            order_id=order.id, strategy=order.strategy, reason="",
            context={
                "position_id": position.id,
                "fill_price": str(fill_price),
                "cash_balance_after": str(self._repo.cash_balance()),
            },
            timestamp=fill_time,
        )


def _materialize_exit(self, position, *, exit_date: date) -> None:
    exit_time = self._clock.now()
    order = self._repo.find_paper_order_by_id(position.order_id)
    if order.horizon_price is None:
        raise InvariantError(
            f"order {order.id} has no horizon_price; "
            "ForwardExecutionEngine cannot exit without it (lock xii)"
        )
    exit_price = order.horizon_price
    cash_inflow = exit_price * Decimal(position.quantity)
    realized_pnl = (exit_price - position.entry_price) * Decimal(position.quantity)

    with self._repo.transaction():
        fill = self._repo.insert_paper_fill(
            order_id=position.order_id, position_id=position.id,
            side="EXIT", price=exit_price, quantity=position.quantity,
            filled_at=exit_time, cash_delta=cash_inflow, realized_pnl=realized_pnl,
        )
        self._repo.update_paper_position_exit(
            position_id=position.id, exit_fill_id=fill.id,
            exit_price=exit_price, realized_pnl=realized_pnl, closed_at=exit_time,
        )
        self._repo.insert_cash_ledger_entry_for_fill(
            timestamp=exit_time, delta=cash_inflow,
            reason="EXIT_FILL", fill_id=fill.id,
        )
        self._repo.write_audit_event(
            event_type=AuditEventType.POSITION_CLOSED,
            order_id=position.order_id, strategy=position.strategy, reason="",
            context={
                "position_id": position.id,
                "exit_price": str(exit_price),
                "realized_pnl": str(realized_pnl),
                "cash_balance_after": str(self._repo.cash_balance()),
            },
            timestamp=exit_time,
        )
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/trading/test_forward_engine.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add marketpulse/trading/forward_engine.py tests/trading/test_forward_engine.py
git commit -m "feat(6a-2): ForwardExecutionEngine.tick — entry+exit materialization (locks xxiv/6a-L4)"
```

### Task 6a-2.8: Grep tests for clock + single-writer + FILLED vocabulary

**Files:**
- Create: `tests/trading/test_invariant_greps.py`

- [ ] **Step 1: Write grep invariants**

```python
# tests/trading/test_invariant_greps.py
# Layer: invariant
"""6a-2 invariants enforced by grep against the source tree."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def test_no_date_today_or_datetime_now_in_trading_or_scheduler():
    """Lock xxiii: all production code reads time via Clock. Exception:
    inside WallClock.now/today (which IS the unique production wrapper)."""
    paths = [
        Path("marketpulse/trading"),
        Path("marketpulse/scheduler/paper_trading_tick.py"),
    ]
    pattern = re.compile(r"\b(date\.today\(\)|datetime\.now\()")
    for root in paths:
        if not root.exists():
            continue
        targets = [root] if root.is_file() else list(root.rglob("*.py"))
        for f in targets:
            text = f.read_text()
            if "class WallClock" in text:
                # Strip class WallClock body before grepping
                text = re.sub(r"class WallClock.*?(?=\nclass |\Z)", "", text, flags=re.DOTALL)
            assert not pattern.search(text), (
                f"Lock xxiii violation: date.today()/datetime.now() in {f}"
            )


def test_only_repository_writes_paper_tables():
    """Lock iii: repository.py is the only writer of paper_* tables."""
    out = subprocess.run(
        ["git", "grep", "-nE", r"session\.add|session\.execute\((insert|update)",
         "marketpulse/"],
        capture_output=True, text=True,
    ).stdout
    bad = [
        line for line in out.splitlines()
        if "marketpulse/" in line
        and "trading/repository.py" not in line
        and "trading/__init__.py" not in line
        and "db/" not in line  # base infra
    ]
    assert not bad, (
        "Lock iii violation: session.add/insert/update outside repository.py:\n" +
        "\n".join(bad)
    )


def test_no_legacy_filled_status_string():
    """Lock xix: legal status string is ENTRY_FILLED, not FILLED."""
    out = subprocess.run(
        ["git", "grep", "-nE", r'"FILLED"|\bORDER_FILLED\b', "marketpulse/"],
        capture_output=True, text=True,
    ).stdout
    bad = [
        line for line in out.splitlines()
        if 'ORDER_ENTRY_FILLED' not in line  # word-boundary safety
    ]
    assert not bad, f"Lock xix violation:\n" + "\n".join(bad)
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/trading/test_invariant_greps.py -v
```

Expected: PASS (or shows the violation to fix).

- [ ] **Step 3: Commit**

```bash
git add tests/trading/test_invariant_greps.py
git commit -m "test(6a-2): grep invariants for clock/single-writer/FILLED vocab"
```

### Task 6a-2.9: 6a-2 integration smoke

- [ ] **Step 1: Full suite + ruff**

```bash
uv run pytest -x --tb=short
uv run ruff check marketpulse tests
```

Expected: ALL PASS.

- [ ] **Step 2: Confirm 6a-2 sub-task complete**

```bash
git log --oneline plan/phase-6a-paper-trading-foundation ^main | head -25
```

---

## Sub-task 6a-3: BidAggregator + daily_cycle + Scheduler

**Goal:** `BidAggregator` reads NY-day window from `evaluation_event`. `daily_cycle.run()` orchestrates gap-detect → collect → allocate → place_order×N → tick → TICK_COMPLETED. Thin `paper_trading_tick.py` scheduler entrypoint. APScheduler registration in `marketpulse/scheduler/jobs.py`. `ensure_initial_deposit()` startup hook in `marketpulse/main.py`. New settings in `marketpulse/config.py`.

### Task 6a-3.1: BidAggregator — NY-day window query

**Files:**
- Modify: `marketpulse/trading/bid_aggregator.py`
- Create: `tests/trading/test_bid_aggregator.py`

- [ ] **Step 1: Write tests**

```python
# tests/trading/test_bid_aggregator.py
# Layer: behavioral

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base
    eng = create_engine(f"sqlite:///{tmp_path / 'ba.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _seed_event(session, *, ticker, event_time, strategy="momentum"):
    from marketpulse.db.models import EvaluationEvent

    e = EvaluationEvent(
        event_type="ai_analysis",
        subtype="bullish",
        ticker=ticker,
        event_time=event_time,
        strategy=strategy,
        analysis_id=None,
        verdict="bullish",
    )
    session.add(e)
    session.commit()
    return e


def test_collect_for_date_returns_today_events_only(session):
    from marketpulse.trading.bid_aggregator import BidAggregator
    from marketpulse.trading.calendar import NYTradingCalendar

    # Yesterday's event (14:00 NY = 18:00 UTC on 2026-05-20)
    _seed_event(session, ticker="AAPL", event_time=datetime(2026, 5, 20, 18, 0, tzinfo=UTC))
    # Today's event (10:00 NY = 14:00 UTC on 2026-05-21)
    _seed_event(session, ticker="MSFT", event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC))

    agg = BidAggregator(session=session, calendar=NYTradingCalendar())
    bids = agg.collect_for_date(date(2026, 5, 21))
    tickers = {b.ticker for b in bids}
    assert tickers == {"MSFT"}, f"expected today-only events; got {tickers}"


def test_collect_skips_events_with_null_strategy(session):
    from marketpulse.trading.bid_aggregator import BidAggregator
    from marketpulse.trading.calendar import NYTradingCalendar

    _seed_event(session, ticker="OK", event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
                strategy="momentum")
    _seed_event(session, ticker="NO_STRAT", event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
                strategy=None)

    agg = BidAggregator(session=session, calendar=NYTradingCalendar())
    bids = agg.collect_for_date(date(2026, 5, 21))
    tickers = {b.ticker for b in bids}
    assert tickers == {"OK"}
```

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/trading/test_bid_aggregator.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement BidAggregator (read-only, NO horizon_price lookup)**

Important design decision (round-6 review Blocker 5): **horizon_price is NOT BidAggregator's concern.** BidAggregator returns raw bid candidates with `horizon_price=None`; the daily_cycle's `_make_order_request` populates `horizon_price` via an injected `PriceProvider` BEFORE constructing the OrderRequest. This means:

- Phase 6a tests use `StubPriceProvider` (deterministic).
- Phase 6a production wires a real provider (out of scope for the foundation but the seam exists).
- `BidCandidate.horizon_price` stays as Phase 5 outcome data if present (backtest mode); otherwise the daily_cycle fills it via the provider.
- `ForwardExecutionEngine.tick(horizon)` continues to raise `ENGINE_INVARIANT_ERROR` if `horizon_price IS NULL` — but in normal Phase 6a forward flow it will NOT be NULL because daily_cycle filled it.

The `PriceProvider` Protocol lives in `marketpulse/trading/price_provider.py` (added in Task 6a-1.7b — see below).

```python
# marketpulse/trading/bid_aggregator.py
"""Phase 6a BidAggregator — read-only NY-day window over evaluation_event.

Intentionally dumb. No DEDUP / sizing / capping — that's allocate_for_day's
job. No horizon_price lookup — that's daily_cycle's job via PriceProvider.
Events with NULL strategy are SKIPPED (no audit in 6a; 6b may add
BID_SKIPPED_NO_STRATEGY if needed)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketpulse.backtest.allocation import BidCandidate
from marketpulse.db.models import EvaluationEvent
from marketpulse.trading.calendar import NYTradingCalendar

_NY = ZoneInfo("America/New_York")


class BidAggregator:
    def __init__(self, *, session: Session, calendar: NYTradingCalendar) -> None:
        self._session = session
        self._calendar = calendar

    def collect_for_date(self, tick_date: date) -> list[BidCandidate]:
        """Read evaluation_event rows whose event_time falls in
        [NY-midnight(tick_date), NY-midnight(tick_date+1)) converted
        to UTC. Forward-only (lock xxxiii): only today's events."""
        ny_start = datetime.combine(tick_date, datetime.min.time(), tzinfo=_NY)
        ny_end = ny_start + timedelta(days=1)
        utc_start = ny_start.astimezone(UTC)
        utc_end = ny_end.astimezone(UTC)

        rows = self._session.execute(
            select(EvaluationEvent)
            .where(EvaluationEvent.event_time >= utc_start)
            .where(EvaluationEvent.event_time < utc_end)
            .order_by(EvaluationEvent.event_time)
        ).scalars().all()

        bids: list[BidCandidate] = []
        for r in rows:
            if not r.strategy:
                continue  # skip NULL-strategy rows (no audit in 6a)
            # horizon_date computed deterministically; horizon_price stays
            # None here. daily_cycle fills it via PriceProvider before
            # constructing the OrderRequest.
            event_date = r.event_time.astimezone(_NY).date()
            horizon_date = event_date
            for _ in range(5):
                horizon_date = self._calendar.next_business_day(horizon_date)
            bids.append(BidCandidate(
                strategy=r.strategy,
                ticker=r.ticker,
                event_time=r.event_time,
                event_price=float(getattr(r, "event_price", None) or 0.0),
                horizon_date=horizon_date,
                horizon_price=None,  # filled by daily_cycle via PriceProvider
                strategy_version=getattr(r, "strategy_version", "v0"),
            ))
        return bids
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/trading/test_bid_aggregator.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/bid_aggregator.py tests/trading/test_bid_aggregator.py
git commit -m "feat(6a-3): BidAggregator — NY-day window, skip NULL strategy"
```

### Task 6a-3.2: daily_cycle.run — gap detect + kill switch + orchestrate

**Files:**
- Modify: `marketpulse/trading/daily_cycle.py`
- Create: `tests/trading/test_daily_cycle.py`

- [ ] **Step 1: Write orchestration tests**

```python
# tests/trading/test_daily_cycle.py
# Layer: stateful

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from marketpulse.trading.calendar import NYTradingCalendar


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base
    eng = create_engine(f"sqlite:///{tmp_path / 'dc.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _stub_allocator(*, expected_winners: list[Any]):
    """Returns a callable matching allocate_for_day's signature."""
    from marketpulse.backtest.allocation import AllocationResult

    def _alloc(*, bids, existing_positions, cash_available,
               allocation_context, sizing_context):
        return AllocationResult(
            winners=tuple(expected_winners),
            blocked=(),
            cash_used=0.0,
            cash_remaining=float(cash_available),
        )
    _alloc.__version__ = "v0"
    return _alloc


def _winner_for(ticker, strategy, allocation_date):
    from marketpulse.backtest.allocation import AllocationWinner
    return AllocationWinner(
        strategy=strategy, ticker=ticker,
        event_time=datetime(allocation_date.year, allocation_date.month, allocation_date.day, 14, 0, tzinfo=UTC),
        event_price=150.0,
        horizon_date=allocation_date + timedelta(days=7),
        horizon_price=155.0,
        quantity=10,
        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
        effective_corr_window=60,
        rewarded_for_negative_corr=False,
        would_change_rank=False,
        size_clamped_by_override=False,
        strategy_version="v0",
    )


def _make_deps(session, *, fake_now, allocator):
    from marketpulse.trading.bid_aggregator import BidAggregator
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gate import AlwaysApproveRiskGate

    clock = FakeClock(now=fake_now)
    calendar = NYTradingCalendar()
    repo = Repository(session=session)
    repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=clock.now())
    risk = AlwaysApproveRiskGate()
    ks = KillSwitchState(env_var="MP_NEVER", repository=repo)
    engine = ForwardExecutionEngine(
        repository=repo, clock=clock, kill_switch=ks, risk_gate=risk,
    )
    aggregator = BidAggregator(session=session, calendar=calendar)
    return {
        "clock": clock, "engine": engine, "repository": repo,
        "bid_aggregator": aggregator, "allocator": allocator,
        "calendar": calendar, "kill_switch": ks,
    }


def test_daily_cycle_places_orders_and_writes_tick_completed(session):
    from marketpulse.trading import daily_cycle

    deps = _make_deps(
        session,
        fake_now=datetime(2026, 5, 21, 21, 30, tzinfo=UTC),  # 17:30 NY
        allocator=_stub_allocator(expected_winners=[
            _winner_for("AAPL", "momentum", date(2026, 5, 21)),
        ]),
    )
    result = daily_cycle.run(**deps)

    assert result.tick_date == date(2026, 5, 21)
    assert result.allocation_run_id == "paper-2026-05-21"
    assert result.orders_placed == 1
    assert result.cycle_status == "completed"

    # TICK_COMPLETED written
    from marketpulse.db.models import PaperAuditEvent
    audits = session.execute(select(PaperAuditEvent)).scalars().all()
    assert any(a.event_type == "TICK_COMPLETED" for a in audits)


def test_daily_cycle_same_day_rerun_is_no_op(session):
    """6a-L7: same-day rerun → 0 new orders, 0 new TICK_COMPLETED rows."""
    from marketpulse.db.models import PaperAuditEvent, PaperOrder
    from marketpulse.trading import daily_cycle

    fake_now = datetime(2026, 5, 21, 21, 30, tzinfo=UTC)
    alloc = _stub_allocator(expected_winners=[
        _winner_for("AAPL", "momentum", date(2026, 5, 21)),
    ])
    deps = _make_deps(session, fake_now=fake_now, allocator=alloc)
    daily_cycle.run(**deps)

    # Rerun — same clock, same allocator
    deps = _make_deps(session, fake_now=fake_now, allocator=alloc)
    r2 = daily_cycle.run(**deps)
    assert r2.orders_placed == 0
    assert r2.duplicates_skipped == 1

    orders = session.execute(select(PaperOrder)).scalars().all()
    assert len(orders) == 1

    tick_completed = session.execute(
        select(PaperAuditEvent).where(PaperAuditEvent.event_type == "TICK_COMPLETED")
    ).scalars().all()
    assert len(tick_completed) == 1


def test_daily_cycle_kill_switch_cycle_level_skip(session):
    """6a-L8: kill switch active → KILL_SWITCH_CYCLE_SKIPPED audit;
    0 new paper_order rows. tick() still runs."""
    from marketpulse.db.models import PaperAuditEvent, PaperOrder
    from marketpulse.trading import daily_cycle

    fake_now = datetime(2026, 5, 21, 21, 30, tzinfo=UTC)
    deps = _make_deps(
        session, fake_now=fake_now,
        allocator=_stub_allocator(expected_winners=[
            _winner_for("AAPL", "momentum", date(2026, 5, 21)),
        ]),
    )
    deps["kill_switch"].flip(
        new_state=True, reason="test", actor="t",
        timestamp=deps["clock"].now(),
    )

    result = daily_cycle.run(**deps)
    assert result.cycle_status == "kill_switch_skipped"
    assert result.orders_placed == 0

    orders = session.execute(select(PaperOrder)).scalars().all()
    assert len(orders) == 0

    skips = session.execute(
        select(PaperAuditEvent).where(PaperAuditEvent.event_type == "KILL_SWITCH_CYCLE_SKIPPED")
    ).scalars().all()
    assert len(skips) == 1
```

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/trading/test_daily_cycle.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement daily_cycle.run**

```python
# marketpulse/trading/daily_cycle.py
"""Phase 6a daily orchestration (lock xxv: scheduler is thin; this owns
the real sequence).

Sequence:
    1. Gap detection (forward-only — lock xxxiii)
    1.5. Kill switch cycle-level short-circuit (6a-L8)
    2. Collect today's bids
    3. allocate_for_day(...)
    4. place_order × N
    5. tick(as_of=tick_date)
    6. TICK_COMPLETED (or TICK_REPROCESSED_COMPLETED) audit
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable, Literal

from marketpulse.backtest.allocation import (
    AllocationContext,
    AllocationResult,
    SizingContext,
    BidCandidate,
    PositionSnapshot,
)
from marketpulse.trading.bid_aggregator import BidAggregator
from marketpulse.trading.calendar import NYTradingCalendar
from marketpulse.trading.clock import Clock
from marketpulse.trading.execution_engine import ExecutionEngine
from marketpulse.trading.forward_engine import ForwardExecutionEngine
from marketpulse.trading.kill_switch import KillSwitchState
from marketpulse.trading.repository import Repository
from marketpulse.trading.types import (
    AllocationRunId,
    AuditEventType,
    OrderRejected,
    OrderRequest,
    TickError,
    TickResult,
)


@dataclass(frozen=True)
class DailyCycleResult:
    tick_date: date
    allocation_run_id: AllocationRunId
    bids_collected: int
    orders_placed: int
    orders_rejected: int
    duplicates_skipped: int
    entries_materialized: int
    exits_materialized: int
    tick_errors: tuple[TickError, ...]
    cycle_status: Literal["completed", "completed_with_errors", "kill_switch_skipped"]
    cash_balance_end: Decimal


def _make_order_request(*, winner, allocation_run_id, allocation_date) -> OrderRequest:
    """Quantization site: float → Decimal at the OrderRequest boundary (lock xxii)."""
    return OrderRequest(
        strategy=winner.strategy,
        ticker=winner.ticker,
        quantity=winner.quantity,
        event_time=winner.event_time,
        allocation_date=allocation_date,
        event_price=Decimal(str(winner.event_price)),
        horizon_date=winner.horizon_date,
        horizon_price=(
            Decimal(str(winner.horizon_price))
            if winner.horizon_price is not None else None
        ),
        allocation_run_id=allocation_run_id,
        strategy_version=winner.strategy_version,
        allocator_version="v0",
        execution_engine_version=ForwardExecutionEngine.VERSION,
        weight=winner.weight,
        raw_bid_weight=winner.raw_bid_weight,
        pool_corr=winner.pool_corr,
        contribution_multiplier=winner.contribution_multiplier,
        adjusted_bid_weight=winner.adjusted_bid_weight,
        effective_corr_window=winner.effective_corr_window,
        rewarded_for_negative_corr=winner.rewarded_for_negative_corr,
        would_change_rank=winner.would_change_rank,
        size_clamped_by_override=winner.size_clamped_by_override,
    )


def _position_snapshots(repo: Repository) -> list[PositionSnapshot]:
    """Translate paper_position rows into PositionSnapshot dataclasses
    for the pure allocator kernel."""
    return [
        PositionSnapshot(
            strategy=p.strategy, ticker=p.ticker, quantity=p.quantity,
            entry_price=float(p.entry_price), sector=None,
            open_since=p.entry_date,
        )
        for p in repo.open_positions_snapshot()
    ]


def run(
    *,
    clock: Clock,
    engine: ExecutionEngine,
    repository: Repository,
    bid_aggregator: BidAggregator,
    allocator: Callable[..., AllocationResult],
    calendar: NYTradingCalendar,
    kill_switch: KillSwitchState,
) -> DailyCycleResult:
    tick_date = calendar.today_ny_trading_date(clock.now())
    allocation_run_id = AllocationRunId(f"paper-{tick_date.isoformat()}")

    # === Phase 1: gap detection ===
    # sessions_after returns sessions in (last_processed, tick_date].
    # That count includes tick_date itself; subtract 1 to get strictly
    # missed sessions BETWEEN them. Round-6 fix: explicit primitive
    # avoids the inclusive/exclusive ambiguity.
    last_processed = repository.last_processed_tick_date()
    if last_processed is not None and last_processed < tick_date:
        missed = calendar.sessions_after(last_processed, tick_date) - 1
        if missed > 0:
            with repository.transaction():
                repository.write_gap_audit_once(
                    last_tick=last_processed, resume_date=tick_date,
                    missed_business_days=missed, timestamp=clock.now(),
                )

    # === Phase 1.5: kill-switch cycle-level short-circuit (6a-L8) ===
    if kill_switch.is_active():
        tick_result = engine.tick(as_of=tick_date)
        with repository.transaction():
            repository.write_audit_event(
                event_type=AuditEventType.KILL_SWITCH_CYCLE_SKIPPED,
                order_id=None, strategy=None, reason="kill_switch_active",
                context={
                    "tick_date": tick_date.isoformat(),
                    "mode": "kill_switch_active",
                    "tick_entries_materialized": tick_result.entries_materialized,
                    "tick_exits_materialized": tick_result.exits_materialized,
                    "tick_errors": [
                        {"phase": e.phase, "order_id": e.order_id,
                         "position_id": e.position_id, "error": e.error}
                        for e in tick_result.errors
                    ],
                },
                timestamp=clock.now(),
            )
        return DailyCycleResult(
            tick_date=tick_date, allocation_run_id=allocation_run_id,
            bids_collected=0, orders_placed=0, orders_rejected=0,
            duplicates_skipped=0,
            entries_materialized=tick_result.entries_materialized,
            exits_materialized=tick_result.exits_materialized,
            tick_errors=tick_result.errors,
            cycle_status="kill_switch_skipped",
            cash_balance_end=repository.cash_balance(),
        )

    # === Phase 2: collect today's raw bids ===
    bids = bid_aggregator.collect_for_date(tick_date)

    # === Phase 3: allocate (pure) ===
    allocation_ctx = AllocationContext(
        allocation_date=tick_date,
        target_vol=0.01,
        lookback_days=60,
        sector_caps_enabled=True,
        sector_cap_pct=0.40,
        correlation_caps_enabled=True,
        correlation_cap_pct=0.40,
        correlation_threshold=0.60,
        contribution_enabled=False,
        contribution_lambda=0.5,
        pool_corr_mode="excludes_self",
        phase5e_warm_pool_overlap_days=20,
        max_capital_in_use=10_000.0,
    )
    sizing_ctx = SizingContext(
        base_position_size=1_000.0, min_position=200.0,
        max_position=4_000.0, sizing_enabled=True,
        per_strategy_overrides={},
    )
    allocation = allocator(
        bids=bids,
        existing_positions=_position_snapshots(repository),
        cash_available=float(repository.cash_balance()),
        allocation_context=allocation_ctx,
        sizing_context=sizing_ctx,
    )

    # === Phase 4: place_order per winner ===
    placed = rejected = duplicates = 0
    for winner in allocation.winners:
        request = _make_order_request(
            winner=winner,
            allocation_run_id=allocation_run_id,
            allocation_date=tick_date,
        )
        try:
            result = engine.place_order(order_request=request)
            if result.created:
                placed += 1
            elif result.duplicate:
                duplicates += 1
        except OrderRejected:
            rejected += 1

    # === Phase 5: tick ===
    tick_result = engine.tick(as_of=tick_date)

    # === Phase 6: TICK_COMPLETED ===
    cycle_status: Literal["completed", "completed_with_errors"] = (
        "completed_with_errors" if tick_result.errors else "completed"
    )
    result = DailyCycleResult(
        tick_date=tick_date,
        allocation_run_id=allocation_run_id,
        bids_collected=len(bids),
        orders_placed=placed,
        orders_rejected=rejected,
        duplicates_skipped=duplicates,
        entries_materialized=tick_result.entries_materialized,
        exits_materialized=tick_result.exits_materialized,
        tick_errors=tick_result.errors,
        cycle_status=cycle_status,
        cash_balance_end=repository.cash_balance(),
    )
    with repository.transaction():
        repository.write_tick_completed_once(
            tick_date=tick_date,
            context={
                "tick_date": tick_date.isoformat(),
                "status": cycle_status,
                "allocation_run_id": allocation_run_id,
                "bids_collected": result.bids_collected,
                "orders_placed": result.orders_placed,
                "orders_rejected": result.orders_rejected,
                "duplicates_skipped": result.duplicates_skipped,
                "entries_materialized": result.entries_materialized,
                "exits_materialized": result.exits_materialized,
                "tick_errors": [
                    {"phase": e.phase, "order_id": e.order_id,
                     "position_id": e.position_id, "error": e.error}
                    for e in result.tick_errors
                ],
                "cash_balance_end": str(result.cash_balance_end),
            },
            timestamp=clock.now(),
        )
    return result
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/trading/test_daily_cycle.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/daily_cycle.py tests/trading/test_daily_cycle.py
git commit -m "feat(6a-3): daily_cycle.run — gap/kill-switch/allocate/place/tick/complete"
```

### Task 6a-3.3: Settings + main.py startup hook

**Files:**
- Modify: `marketpulse/config.py`
- Modify: `marketpulse/main.py`

- [ ] **Step 1: Add settings**

Append the following four fields to `Settings` in `marketpulse/config.py`:

```python
    paper_tick_hour: int = Field(17, alias="MP_PAPER_TICK_HOUR")
    paper_tick_minute: int = Field(30, alias="MP_PAPER_TICK_MINUTE")
    paper_initial_deposit: str = Field("10000", alias="MP_PAPER_INITIAL_DEPOSIT")
    paper_kill_switch: bool = Field(False, alias="MP_PAPER_KILL_SWITCH")
```

- [ ] **Step 2: Add the startup hook in `marketpulse/main.py`**

Locate the existing app startup section (usually a `@app.on_event("startup")` or lifespan handler). Append:

```python
# Phase 6a paper-trading initialization
from decimal import Decimal
from marketpulse.trading.clock import WallClock
from marketpulse.trading.repository import Repository
from marketpulse.db.base import get_session_factory

def _seed_paper_initial_deposit() -> None:
    settings = get_settings()
    factory = get_session_factory()
    with factory() as session:
        Repository(session=session).ensure_initial_deposit(
            amount=Decimal(settings.paper_initial_deposit),
            timestamp=WallClock().now(),
        )

# Call this from existing startup. Example:
_seed_paper_initial_deposit()
```

- [ ] **Step 3: Smoke test the startup hook**

```python
# Append to tests/trading/test_kill_switch.py (or a new test_startup.py)
# Layer: behavioral

def test_ensure_initial_deposit_idempotent(tmp_path, monkeypatch):
    from datetime import UTC, datetime
    from decimal import Decimal
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from marketpulse.db.base import Base
    from marketpulse.db.models import PaperCashLedger
    from marketpulse.trading.repository import Repository

    eng = create_engine(f"sqlite:///{tmp_path / 'seed.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        repo = Repository(session=s)
        ts = datetime(2026, 5, 21, tzinfo=UTC)
        repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=ts)
        repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=ts)
        rows = s.execute(select(PaperCashLedger)).scalars().all()
        assert len(rows) == 1
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/trading/ -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/config.py marketpulse/main.py tests/trading/
git commit -m "feat(6a-3): paper_* settings + ensure_initial_deposit startup hook"
```

### Task 6a-3.4: Thin scheduler entrypoint + APScheduler registration

**Files:**
- Create: `marketpulse/scheduler/paper_trading_tick.py`
- Modify: `marketpulse/scheduler/jobs.py`
- Create: `tests/trading/test_scheduler.py`

- [ ] **Step 1: Write the thin-wrapper assertion**

```python
# tests/trading/test_scheduler.py
# Layer: invariant
"""6a-3: paper_trading_tick.py is THIN (lock xxv)."""

from __future__ import annotations

import re
from pathlib import Path


def test_scheduler_entrypoint_is_thin():
    """No SQL, no business logic, no state mutation inside the scheduler
    entrypoint. It must only resolve DI and call daily_cycle.run."""
    src = Path("marketpulse/scheduler/paper_trading_tick.py").read_text()

    # Forbid SQL fragments and direct paper_* writes.
    forbidden = [
        "session.add", "session.execute(insert", "session.execute(update",
        "INSERT", "UPDATE", "DELETE",
    ]
    for f in forbidden:
        assert f not in src, f"thin-wrapper violation: '{f}' in scheduler entrypoint"

    # The file should be small.
    line_count = len([
        l for l in src.splitlines()
        if l.strip() and not l.strip().startswith("#")
    ])
    assert line_count < 60, f"scheduler entrypoint too thick: {line_count} non-comment lines"

    # Must call daily_cycle.run.
    assert "daily_cycle.run(" in src or "from marketpulse.trading import daily_cycle" in src
```

- [ ] **Step 2: Implement the entrypoint**

```python
# marketpulse/scheduler/paper_trading_tick.py
"""APScheduler entrypoint for the daily paper-trading tick (lock xxv).

This module contains ZERO business logic. It resolves DI and calls
daily_cycle.run."""

from __future__ import annotations

import logging

from marketpulse.config import get_settings
from marketpulse.db.base import get_session_factory
from marketpulse.trading import daily_cycle
from marketpulse.trading.bid_aggregator import BidAggregator
from marketpulse.trading.calendar import NYTradingCalendar
from marketpulse.trading.clock import WallClock
from marketpulse.trading.forward_engine import ForwardExecutionEngine
from marketpulse.trading.kill_switch import KillSwitchState
from marketpulse.trading.repository import Repository
from marketpulse.trading.risk_gate import AlwaysApproveRiskGate
from marketpulse.backtest.allocation import allocate_for_day

log = logging.getLogger(__name__)


def paper_trading_tick_job() -> None:
    settings = get_settings()
    factory = get_session_factory()

    with factory() as session:
        clock = WallClock()
        calendar = NYTradingCalendar()
        repository = Repository(session=session)
        risk_gate = AlwaysApproveRiskGate()
        kill_switch = KillSwitchState(
            env_var="MP_PAPER_KILL_SWITCH", repository=repository,
        )
        engine = ForwardExecutionEngine(
            repository=repository, clock=clock,
            kill_switch=kill_switch, risk_gate=risk_gate,
        )
        bid_aggregator = BidAggregator(session=session, calendar=calendar)

        result = daily_cycle.run(
            clock=clock, engine=engine, repository=repository,
            bid_aggregator=bid_aggregator, allocator=allocate_for_day,
            calendar=calendar, kill_switch=kill_switch,
        )
        log.info(
            "paper_trading_tick done: tick_date=%s placed=%d exits=%d entries=%d errors=%d",
            result.tick_date, result.orders_placed, result.exits_materialized,
            result.entries_materialized, len(result.tick_errors),
        )
```

- [ ] **Step 3: Register in `marketpulse/scheduler/jobs.py`**

Find the existing APScheduler setup and append:

```python
from zoneinfo import ZoneInfo
from apscheduler.triggers.cron import CronTrigger
from marketpulse.scheduler.paper_trading_tick import paper_trading_tick_job

# Phase 6a paper trading daily tick (lock xxv: thin entrypoint)
scheduler.add_job(
    paper_trading_tick_job,
    trigger=CronTrigger(
        hour=settings.paper_tick_hour,
        minute=settings.paper_tick_minute,
        timezone=ZoneInfo("America/New_York"),
    ),
    id="paper_trading_tick",
    misfire_grace_time=3600,
    coalesce=True,
    max_instances=1,
)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/trading/test_scheduler.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/scheduler/paper_trading_tick.py marketpulse/scheduler/jobs.py tests/trading/test_scheduler.py
git commit -m "feat(6a-3): paper_trading_tick scheduler entrypoint (lock xxv thin)"
```

### Task 6a-3.5: 6a-3 integration smoke

- [ ] **Step 1: Full suite + ruff**

```bash
uv run pytest -x --tb=short
uv run ruff check marketpulse tests
```

Expected: ALL PASS.

---

## Sub-task 6a-4: E2E Stateful Suite + Final Integration

**Goal:** End-to-end multi-day flow with `FakeClock`; smoke run of full suite + ruff + route smoke.

### Task 6a-4.1: Multi-day E2E

**Files:**
- Create: `tests/trading/test_e2e_stateful.py`

- [ ] **Step 1: Write the canonical multi-day E2E**

```python
# tests/trading/test_e2e_stateful.py
# Layer: stateful

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base
    eng = create_engine(f"sqlite:///{tmp_path / 'e2e.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def test_full_5day_lifecycle_place_to_close(session):
    """D0: tick → place + ENTRY_FILLED + OPEN.
       D1-D4: idle ticks; no new orders, no exits.
       D5: horizon → CLOSED + cash delta correct."""
    from marketpulse.backtest.allocation import AllocationResult, AllocationWinner
    from marketpulse.db.models import (
        EvaluationEvent, PaperCashLedger, PaperPosition,
    )
    from marketpulse.trading import daily_cycle
    from marketpulse.trading.bid_aggregator import BidAggregator
    from marketpulse.trading.calendar import NYTradingCalendar
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gate import AlwaysApproveRiskGate

    D0 = date(2026, 5, 21)
    D5 = date(2026, 5, 28)

    # Seed: one evaluation_event on D0 14:00 NY = D0 18:00 UTC
    session.add(EvaluationEvent(
        event_type="ai_analysis", subtype="bullish", ticker="AAPL",
        event_time=datetime(2026, 5, 21, 18, 0, tzinfo=UTC),
        strategy="momentum", analysis_id=None, verdict="bullish",
    ))
    session.commit()

    # Closure-captured fake_clock
    fake_clock = FakeClock(now=datetime(2026, 5, 21, 21, 30, tzinfo=UTC))

    # Stub allocator that yields a single winner with horizon_price set
    def alloc(*, bids, existing_positions, cash_available,
              allocation_context, sizing_context):
        if not bids:
            return AllocationResult(
                winners=(), blocked=(), cash_used=0.0,
                cash_remaining=float(cash_available),
            )
        b = bids[0]
        return AllocationResult(
            winners=(AllocationWinner(
                strategy=b.strategy, ticker=b.ticker,
                event_time=b.event_time,
                event_price=150.0,
                horizon_date=D5, horizon_price=155.0,
                quantity=10, weight=1.0, raw_bid_weight=1.0,
                pool_corr=0.1, contribution_multiplier=1.0,
                adjusted_bid_weight=1.0, effective_corr_window=60,
                rewarded_for_negative_corr=False, would_change_rank=False,
                size_clamped_by_override=False, strategy_version="v0",
            ),),
            blocked=(),
            cash_used=1500.0,
            cash_remaining=float(cash_available) - 1500.0,
        )
    alloc.__version__ = "v0"

    def make_deps(clock):
        repo = Repository(session=session)
        repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=clock.now())
        return {
            "clock": clock,
            "engine": ForwardExecutionEngine(
                repository=repo, clock=clock,
                kill_switch=KillSwitchState(env_var="MP_NEVER", repository=repo),
                risk_gate=AlwaysApproveRiskGate(),
            ),
            "repository": repo,
            "bid_aggregator": BidAggregator(session=session, calendar=NYTradingCalendar()),
            "allocator": alloc,
            "calendar": NYTradingCalendar(),
            "kill_switch": KillSwitchState(env_var="MP_NEVER", repository=repo),
        }

    # D0
    r0 = daily_cycle.run(**make_deps(fake_clock))
    assert r0.orders_placed == 1
    assert r0.entries_materialized == 1
    positions = session.execute(select(PaperPosition)).scalars().all()
    assert len(positions) == 1
    assert positions[0].status == "OPEN"

    # D1..D4: idle (no new events)
    for d in [date(2026, 5, 22), date(2026, 5, 25),
              date(2026, 5, 26), date(2026, 5, 27)]:
        fake_clock.set(now=datetime(d.year, d.month, d.day, 21, 30, tzinfo=UTC))
        ri = daily_cycle.run(**make_deps(fake_clock))
        assert ri.orders_placed == 0
        assert ri.exits_materialized == 0

    # D5: horizon → exit
    fake_clock.set(now=datetime(2026, 5, 28, 21, 30, tzinfo=UTC))
    r5 = daily_cycle.run(**make_deps(fake_clock))
    assert r5.exits_materialized == 1

    positions = session.execute(select(PaperPosition)).scalars().all()
    assert positions[0].status == "CLOSED"
    # PnL: (155 - 150) * 10 = 50
    assert positions[0].realized_pnl == Decimal("50")

    # Cash: 10000 - 1500 (entry) + 1550 (exit) = 10050
    cash_rows = session.execute(
        select(PaperCashLedger).order_by(PaperCashLedger.id)
    ).scalars().all()
    assert cash_rows[-1].balance_after == Decimal("10050")


def test_cash_ledger_sum_equals_latest_balance(session):
    """Invariant: Σ delta == latest balance_after. Held after every fixture."""
    from marketpulse.db.models import PaperCashLedger
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    from datetime import UTC, datetime
    repo.ensure_initial_deposit(
        amount=Decimal("10000"),
        timestamp=datetime(2026, 5, 21, tzinfo=UTC),
    )

    rows = session.execute(select(PaperCashLedger)).scalars().all()
    total = sum(r.delta for r in rows)
    assert total == rows[-1].balance_after
```

- [ ] **Step 2: Run E2E**

```bash
uv run pytest tests/trading/test_e2e_stateful.py -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/trading/test_e2e_stateful.py
git commit -m "test(6a-4): full multi-day E2E stateful suite (place→tick→close)"
```

### Task 6a-4.2: Full integration smoke + ruff + alembic + route smoke

- [ ] **Step 1: Full suite**

```bash
uv run pytest -x --tb=short
```

Expected: ALL PASS.

- [ ] **Step 2: Ruff**

```bash
uv run ruff check marketpulse tests
```

Expected: no lint errors.

- [ ] **Step 3: Alembic round trip on a fresh DB**

```bash
DATABASE_URL="sqlite:///$(mktemp).db" uv run alembic upgrade head
DATABASE_URL="sqlite:///$(mktemp).db" uv run alembic downgrade base
```

Expected: both succeed.

- [ ] **Step 4: Route smoke (existing Phase 1-5 endpoints unaffected)**

```bash
uv run python -m marketpulse.web.main --check-routes  # if available, else:
uv run pytest tests/web/ -x --tb=short
```

Expected: PASS.

### Task 6a-4.3: Final 6a PR

- [ ] **Step 1: Push the branch**

```bash
git push -u origin plan/phase-6a-paper-trading-foundation
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "feat(phase-6a): paper trading foundation" --body "$(cat <<'EOF'
## Summary
- New `marketpulse/trading/` package (11 files): `ExecutionEngine` Protocol, `ForwardExecutionEngine`, single-writer `repository.py`, `daily_cycle.py`, `bid_aggregator.py`, `clock.py`, `calendar.py`, `kill_switch.py`, `idempotency.py`, `risk_gate.py`, `types.py`.
- 5 new DB tables: `paper_order`, `paper_fill`, `paper_position`, `paper_cash_ledger`, `paper_audit_event` (Alembic migration `0010_phase6_paper_trading.py`).
- Shared per-day allocation kernel `marketpulse/backtest/allocation.py` extracted from `simulate_shared_pool` (lock 6a-L1 narrow extraction).
- Daily scheduler job at 17:30 NY runs `daily_cycle.run`.
- Stateful test suite (`tests/trading/test_e2e_stateful.py`) verifies full place→tick→close lifecycle across multi-day FakeClock.
- 12 audit event types: `ORDER_PLACED`, `ORDER_PLACED_DUPLICATE`, `ORDER_REJECTED`, `ORDER_CANCELLED`, `ORDER_ENTRY_FILLED`, `POSITION_CLOSED`, `KILL_SWITCH_FLIPPED`, `KILL_SWITCH_CYCLE_SKIPPED`, `TICK_COMPLETED`, `TICK_REPROCESSED_COMPLETED`, `SCHEDULER_GAP_DETECTED`, `ENGINE_INVARIANT_ERROR`.

## Architectural locks honored
32 umbrella locks + 9 6a-local locks (6a-L1..L9). Notable:
- Lock iii (single-writer): `repository.py` only mutator; enforced by grep test in `tests/trading/test_invariant_greps.py`.
- Lock xxiii (clock): no `date.today()` / `datetime.now()` outside `WallClock`; grep-enforced.
- Lock xxxiii (forward-only recovery): missed days skipped; `SCHEDULER_GAP_DETECTED` audit.
- 6a-L2 (PlaceOrderResult): no TOCTOU race; engine returns `(created, duplicate)`.
- 6a-L4/L5 (TickError + TICK_COMPLETED.status): structured errors; recovery audit via `TICK_REPROCESSED_COMPLETED`.
- 6a-L8 (kill switch defense in depth): cycle-level skip + engine-level rejection; `engine.tick` still runs to close OPEN positions.

## Test plan
- [x] `pytest -x` green (Phase 1-5 unaffected; 6a-0 extraction validated via behavioral + public-field equality on Phase 5 fixtures)
- [x] `ruff check marketpulse tests` clean
- [x] `alembic upgrade head` + `downgrade base` round-trip succeeds
- [x] Multi-day E2E stateful test passes
- [x] 6a operational test scenarios #1-#28 in spec § 9.3 all covered

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Confirm PR URL**

The command will print the PR URL. Return it.

---

## Spec Coverage Self-Review

This plan covers all of the 6a spec's `Deliverables Summary` (§13):

**New files (14):** all present — see File Structure section + 6a-1 through 6a-3 tasks.
**Modified files (7):** `portfolio_simulator.py` (6a-0), `db/models.py` (6a-1), `scheduler/jobs.py` (6a-3), `main.py` (6a-3), `config.py` (6a-3), `pyproject.toml` (6a-1), `tests/conftest.py` (6a-1).
**New migration:** `0010_phase6_paper_trading.py` (Task 6a-1.9).
**New tests (13):** test_types, test_clock, test_calendar, test_idempotency, test_kill_switch, test_risk_gate, test_repository, test_forward_engine, test_bid_aggregator, test_daily_cycle, test_scheduler, test_e2e_stateful, test_allocation_extraction.
**Phase 5 regression contract:** 6a-0.4 step 3 runs the full Phase 5 suite as the cross-check.

**Locks → Tasks mapping (spot check):**
- Lock iii single-writer → 6a-2.1 + grep test 6a-2.8
- Lock xxii Decimal → 6a-1.8 model columns + 6a-3.2 `_make_order_request` quantization site
- Lock xxiii Clock → 6a-1.4 + grep test 6a-2.8
- Lock xxiv tick idempotent → 6a-2.7 (`test_tick_is_idempotent`)
- Lock xxvii transactional → 6a-2.5 (`test_place_order_accepted_writes_order_and_audit`)
- Lock xxx idempotency before risk → 6a-2.5 (`test_place_order_idempotency_hit_returns_duplicate`)
- Lock xxxiii forward-only recovery → 6a-3.2 (gap detection in `daily_cycle.run`)
- 6a-L1 extraction boundary → 6a-0.5 (boundary grep test)
- 6a-L2 PlaceOrderResult → 6a-1.2 (types) + 6a-2.5 (engine returns result)
- 6a-L3 risk-gate fail-closed exception → 6a-2.5 (`test_place_order_risk_gate_exception_fail_closed`)
- 6a-L4 TickError structured → 6a-1.2 + 6a-2.7
- 6a-L5 TICK_COMPLETED.status + duplicate dedupe → 6a-2.1 + 6a-2.5
- 6a-L6 status transitions → 6a-2.2
- 6a-L7 deterministic allocation_run_id → 6a-1.6 (idempotency-key not depending on versions) + 6a-3.2 (`paper-{tick_date}`)
- 6a-L8 kill-switch two layers + recovery audit → 6a-3.2 (cycle-level) + 6a-2.5 (engine-level) + 6a-2.1 (TICK_REPROCESSED_COMPLETED)
- 6a-L9 AllocationContext explicit fields → 6a-0.2 (dataclass shape test)

**End of plan.**

---

## Round-6 Required Revisions (read BEFORE implementation)

The original plan tasks above were drafted before the round-6 review surfaced 18 issues. The blockers 1-6 are already applied inline (frozen baseline, business_days_between semantics, PriceProvider note). Blockers 7-18 need the following adjustments applied as the agent works each affected task.

### R6-1 (Blocker 7): Repository must be a single class

When implementing Task 6a-2.1 / 6a-2.2 / 6a-2.3, do NOT paste the method bodies as module-level functions. Every method belongs inside `class Repository:`. The plan presents method bodies flat for readability; the implementer integrates them under the class with `self` as the first parameter. Smoke test after Task 6a-2.1: `from marketpulse.trading.repository import Repository; Repository.transaction` should resolve, not `marketpulse.trading.repository.transaction`.

### R6-2 (Blocker 8): Audit transaction convention — fixed

**Rule:** All repository write methods, INCLUDING `write_audit_event`, `write_duplicate_audit_once`, `write_gap_audit_once`, `write_tick_completed_once`, REQUIRE the caller to wrap them in `with repo.transaction():`. Repository methods themselves perform `session.add(...) + session.flush()` but NEVER `session.commit()`. Only `transaction()` commits. (`ensure_initial_deposit` is the one exception that wraps internally because it's a startup hook.)

Update every place in the plan that calls these helpers — if there's no surrounding `with repository.transaction():`, that's a bug. The plan's daily_cycle.run code already does this; the standalone repository tests in 6a-2.1 should be updated to use `with repo.transaction()` wrappers (current plan shows direct calls — fix during implementation).

### R6-3 (Blocker 9): KillSwitchState does NOT touch `repo._session`

Add the following helper to `Repository` (insert after `latest_tick_status`):

```python
def latest_kill_switch_state(self) -> bool:
    """Latest persisted kill-switch state. False if never flipped."""
    row = self._session.execute(
        select(PaperAuditEvent).where(
            PaperAuditEvent.event_type == AuditEventType.KILL_SWITCH_FLIPPED.value
        ).order_by(desc(PaperAuditEvent.id))
    ).scalars().first()
    if row is None:
        return False
    return bool(row.context.get("to_state"))
```

Then in `KillSwitchState._db_state`, replace the inline session query with `return self._repo.latest_kill_switch_state()`. The boundary stays clean — KillSwitchState never imports SQLAlchemy or touches `_session`.

### R6-4 (Blocker 10): Lock 6a-L5 wording

The spec (commit `dc20658`) already locks `last_processed_tick_date()` to read TICK_COMPLETED OR KILL_SWITCH_CYCLE_SKIPPED. The plan matches this. No additional change needed — but the implementer should grep the spec to confirm vocabulary alignment when in doubt:

```bash
grep -nE 'last_processed_tick_date|6a-L5' docs/superpowers/specs/2026-05-21-phase-6a-paper-trading-foundation-design.md
```

### R6-5 (Blocker 11): SQLite JSON access stability

The repository's dedup helpers use `PaperAuditEvent.context["tick_date"].as_string() == ...`. This relies on SQLAlchemy's JSON path expression. If pytest fails on these comparisons during 6a-2.1, fall back to fetching candidate rows and filtering in Python:

```python
candidates = self._session.execute(
    select(PaperAuditEvent).where(
        PaperAuditEvent.event_type == AuditEventType.ORDER_PLACED_DUPLICATE.value
    )
).scalars().all()
existing = next(
    (r for r in candidates
     if r.context.get("idempotency_key") == idempotency_key
     and r.context.get("tick_date") == tick_date.isoformat()),
    None,
)
```

This is slower but bulletproof on SQLite JSON1. For 6a-1 scale (~30 audit rows/day), performance is irrelevant.

### R6-6 (Blocker 12): Alembic migration uses `sa.DateTime(timezone=True)`, models use `TZDateTime`

This is intentional and matches the existing convention (see `alembic/versions/0df4e23abe4e_evaluation_event_and_outcome_tables.py` — `event_time = sa.DateTime(timezone=True)` while the model is `Mapped[datetime] = mapped_column(TZDateTime, ...)`). `TZDateTime` is a TypeDecorator over `DateTime(timezone=True)`; Alembic sees the underlying type. The model layer adds the encode/decode behavior.

Smoke this in Task 6a-1.9 step 4: after `alembic upgrade head`, verify the column round-trips a tz-aware datetime correctly via a quick session query.

### R6-7 (Blocker 13): `paper_fill.position_id` IS a foreign key

Update the model (Task 6a-1.8) and migration (Task 6a-1.9):

```python
# In marketpulse/db/models.py PaperFill:
position_id: Mapped[int] = mapped_column(
    ForeignKey("paper_position.id"), nullable=False
)

# In alembic/versions/0010_phase6_paper_trading.py paper_fill:
sa.Column("position_id", sa.Integer(),
          sa.ForeignKey("paper_position.id"), nullable=False),
```

The circular-FK problem only exists for `paper_position.entry_fill_id` / `exit_fill_id` (which point AT paper_fill from paper_position). `paper_fill.position_id` points the other way — position is created first, fill is inserted second referencing it. This direction is safe and should have referential integrity.

### R6-8 (Blocker 14): Restore paper_order CHECK constraints

Update Task 6a-1.8 model + Task 6a-1.9 migration to include these CHECKs on `paper_order` (additions to what's currently in the plan):

```python
# Add to PaperOrder.__table_args__ (and migration):
CheckConstraint(
    "status != 'PLACED' OR (filled_at IS NULL AND cancelled_at IS NULL)",
    name="ck_paper_order_placed_no_terminal_ts",
),
CheckConstraint(
    "status != 'ENTRY_FILLED' OR filled_at IS NOT NULL",
    name="ck_paper_order_entry_filled_has_ts",
),
CheckConstraint(
    "status != 'CANCELLED' OR cancelled_at IS NOT NULL",
    name="ck_paper_order_cancelled_has_ts",
),
```

These match the spec's § 4.1 CHECK list. Adds 3 lines to both the model and the migration.

### R6-9 (Blocker 15): `ensure_initial_deposit` race + test

`ensure_initial_deposit` checks `count == 0` then inserts. On SQLite single-writer this is safe (the write-lock serializes). On Postgres later this would race; for Phase 6a SQLite the current shape is fine, but add a test that calls it 3 times in a row to lock the idempotent contract:

```python
# Append to tests/trading/test_repository.py

def test_ensure_initial_deposit_strictly_idempotent(session):
    """3 consecutive calls produce exactly 1 INITIAL_DEPOSIT row."""
    from datetime import UTC, datetime
    from decimal import Decimal
    from sqlalchemy import select
    from marketpulse.db.models import PaperCashLedger
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    ts = datetime(2026, 5, 21, tzinfo=UTC)
    for _ in range(3):
        repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=ts)
    rows = session.execute(
        select(PaperCashLedger).where(PaperCashLedger.reason == "INITIAL_DEPOSIT")
    ).scalars().all()
    assert len(rows) == 1
```

### R6-10 (Blocker 16): Grep tests scoped to production code only

Replace the `test_only_repository_writes_paper_tables` body in Task 6a-2.8 with this stricter version:

```python
def test_only_repository_writes_paper_tables():
    """Lock iii: repository.py is the only writer of paper_* tables in
    PRODUCTION code (not tests, not docs)."""
    out = subprocess.run(
        ["git", "grep", "-nE",
         r"session\.add\(|session\.execute\((insert|update)",
         "--", "marketpulse/"],
        capture_output=True, text=True,
    ).stdout
    bad = []
    for line in out.splitlines():
        # Production marketpulse paths only.
        if not line.startswith("marketpulse/"):
            continue
        # Allowed locations:
        if "marketpulse/trading/repository.py" in line:
            continue
        if "marketpulse/db/" in line:  # base infra
            continue
        # Existing Phase 1-5 modules — they manage their own tables, not paper_*.
        # Add explicit exemptions only if they're writing paper_* tables.
        bad.append(line)
    assert not bad, (
        "Lock iii violation: session.add/insert/update outside "
        "repository.py:\n" + "\n".join(bad)
    )
```

Same for `test_no_legacy_filled_status_string` — keep the word-boundary regex `\bORDER_FILLED\b`.

For long-term safety, consider adding an import-linter rule to `pyproject.toml` that forbids `marketpulse.trading.*` (except `repository`) from importing `sqlalchemy.orm.Session.add` or similar — out of scope for 6a-2 but worth noting for 6b.

### R6-11 (Blocker 17): Protocol method-set test uses `__dict__`

Replace the body of `test_protocol_has_exactly_three_methods` in Task 6a-1.3:

```python
def test_protocol_has_exactly_three_methods():
    from marketpulse.trading.execution_engine import ExecutionEngine

    # Method-name set from the Protocol's own dict (excludes inherited
    # Protocol internals like __subclasshook__, __init_subclass__, etc.)
    own_methods = {
        name for name, value in ExecutionEngine.__dict__.items()
        if not name.startswith("_") and callable(value)
    }
    assert own_methods == {"place_order", "cancel_order", "tick"}, (
        f"ExecutionEngine Protocol drift; got {own_methods}"
    )
```

### R6-12 (Blocker 18): E2E uses only NY trading-session days

The E2E in Task 6a-4.1 currently uses `[date(2026, 5, 22), date(2026, 5, 25), date(2026, 5, 26), date(2026, 5, 27)]`. **2026-05-25 is Memorial Day (US market closed).** With the calendar's rollback semantics in `today_ny_trading_date`, firing on Mon 25 would resolve to Fri 22 — re-processing yesterday's tick. Replace the idle-day list with confirmed sessions only:

```python
# In test_full_5day_lifecycle_place_to_close, replace:
#     for day in [date(2026, 5, 22), date(2026, 5, 25),
#                 date(2026, 5, 26), date(2026, 5, 27)]:
# with:
    # Skip non-session days. D0=Thu 21 (placed entry), D5=Thu 28 (horizon).
    # Intermediate sessions: Fri 22, Tue 26 (Memorial Day Mon 25 closed), Wed 27.
    for day in [date(2026, 5, 22), date(2026, 5, 26), date(2026, 5, 27)]:
```

Also adjust the test to confirm Memorial Day is NOT firing a tick (or that it correctly rolls back), via a separate test:

```python
def test_tick_on_memorial_day_resolves_to_previous_friday(session):
    """If the scheduler accidentally fires on Memorial Day Mon 25, the
    cycle's tick_date resolves to the previous Friday 22 and the run is
    a no-op replay of that day (TICK_COMPLETED already present)."""
    # ... seed Fri 22 with TICK_COMPLETED, then run with clock=Memorial Day
    # 17:30 NY → assert no new orders, no new TICK_COMPLETED, no SCHEDULER_GAP_DETECTED
    # (because last_processed_tick_date == today's resolved date).
    pass  # implementer fills the body per the pattern above
```

### R6-13 (Side fix): `next_business_day` is used in BidAggregator horizon calc

Note that the BidAggregator's horizon-date loop (`for _ in range(5): horizon_date = self._calendar.next_business_day(horizon_date)`) calls `next_business_day` 5× sequentially. After the round-6 fixes to `next_business_day`, this works correctly for any starting date (session or non-session).

### R6-14: New Task 6a-1.7b — PriceProvider abstraction

Insert this task BETWEEN existing Tasks 6a-1.7 (risk_gate) and 6a-1.8 (DB models):

**Files:**
- Create: `marketpulse/trading/price_provider.py`
- Create: `tests/trading/test_price_provider.py`

- [ ] **Step 1: Write the test**

```python
# tests/trading/test_price_provider.py
# Layer: invariant

from __future__ import annotations

from datetime import date
from decimal import Decimal


def test_stub_price_provider_returns_configured_value():
    from marketpulse.trading.price_provider import StubPriceProvider

    p = StubPriceProvider(map={("AAPL", date(2026, 5, 28)): Decimal("155.00")})
    assert p.horizon_price(ticker="AAPL", horizon_date=date(2026, 5, 28)) == Decimal("155.00")


def test_stub_price_provider_default_on_missing():
    from marketpulse.trading.price_provider import StubPriceProvider

    p = StubPriceProvider(default=Decimal("100.00"))
    assert p.horizon_price(ticker="UNKNOWN", horizon_date=date(2026, 5, 28)) == Decimal("100.00")
```

- [ ] **Step 2: Implement**

```python
# marketpulse/trading/price_provider.py
"""PriceProvider Protocol (round-6 fix Blocker 5).

The daily_cycle uses an injected PriceProvider to fill OrderRequest's
horizon_price BEFORE construction. ForwardExecutionEngine then has a
guaranteed-non-None horizon_price at exit time, so tick(horizon) does
NOT fire ENGINE_INVARIANT_ERROR in the normal forward flow.

6a ships StubPriceProvider for tests. Production wires a real provider
(YFinance, broker quote API, etc.) — out of scope for 6a foundation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol


class PriceProvider(Protocol):
    def horizon_price(self, *, ticker: str, horizon_date: date) -> Decimal | None: ...


class StubPriceProvider:
    """Deterministic test provider. Configured via an exact lookup map
    or a default fallback."""

    def __init__(
        self,
        *,
        map: dict[tuple[str, date], Decimal] | None = None,
        default: Decimal | None = None,
    ) -> None:
        self._map = map or {}
        self._default = default

    def horizon_price(self, *, ticker: str, horizon_date: date) -> Decimal | None:
        return self._map.get((ticker, horizon_date), self._default)
```

- [ ] **Step 3: Commit**

```bash
git add marketpulse/trading/price_provider.py tests/trading/test_price_provider.py
git commit -m "feat(6a-1): PriceProvider Protocol + StubPriceProvider (round-6 Blocker 5)"
```

### R6-15: daily_cycle.run threads PriceProvider into OrderRequest

When implementing Task 6a-3.2 / 6a-3.4, the `daily_cycle.run` signature gains a `price_provider: PriceProvider` parameter. `_make_order_request` calls `price_provider.horizon_price(...)` if the winner's `horizon_price` is None:

```python
# Updated _make_order_request:

def _make_order_request(*, winner, allocation_run_id, allocation_date,
                       price_provider) -> OrderRequest:
    # Use Phase 5 outcome data if present; else look up via provider.
    horizon_price = winner.horizon_price
    if horizon_price is None:
        horizon_price_dec = price_provider.horizon_price(
            ticker=winner.ticker,
            horizon_date=winner.horizon_date,
        )
    else:
        horizon_price_dec = Decimal(str(horizon_price))

    return OrderRequest(
        ...,
        horizon_price=horizon_price_dec,  # may be None — engine will reject at exit
        ...,
    )
```

Scheduler entrypoint wires a stub for now (TODO comment marks where 6b wires the real provider):

```python
# In marketpulse/scheduler/paper_trading_tick.py paper_trading_tick_job:
from marketpulse.trading.price_provider import StubPriceProvider
from decimal import Decimal as _D

# 6a foundation: stub. 6b/6c wires a real YFinance-backed provider.
price_provider = StubPriceProvider(default=_D("0"))  # marks the seam

result = daily_cycle.run(
    ...,
    price_provider=price_provider,
)
```

The E2E test in 6a-4.1 uses `StubPriceProvider(map={("AAPL", D5): Decimal("155")})` so the lifecycle test still closes positions cleanly.

### R6-16: Execution order — apply these revisions in this sequence

1. **6a-0**: Apply R6-1 inline as you implement (frozen baseline already updated).
2. **6a-1**: Apply R6-6 (Alembic+TZDateTime convention), R6-7 (paper_fill FK), R6-8 (CHECK constraints), R6-11 (Protocol __dict__ test). Insert Task 6a-1.7b (PriceProvider, R6-14).
3. **6a-2**: Apply R6-1 (class structure), R6-2 (transaction convention), R6-3 (KillSwitchState boundary), R6-5 (JSON fallback if needed), R6-9 (idempotent deposit test), R6-10 (scoped grep).
4. **6a-3**: Apply R6-15 (PriceProvider threaded through daily_cycle).
5. **6a-4**: Apply R6-12 (E2E session-only days, Memorial Day test).

After all revisions, the plan is ready for agent-driven execution.

---

## Final Self-Review (Round 6)

- ✅ 6a-0 has a real frozen baseline (R6-1)
- ✅ No "byte-for-byte" wording remains
- ✅ Calendar primitives are unambiguous (sessions_in_range vs sessions_after)
- ✅ next_business_day handles non-session inputs
- ✅ horizon_price has a clean abstraction (PriceProvider) — Phase 6a forward flow can close positions in tests
- ✅ BidAggregator UTC code clean
- ✅ Repository methods explicitly inside `class Repository:`
- ✅ Audit transaction convention single (caller wraps in `with repo.transaction():`)
- ✅ KillSwitchState honors repository boundary
- ✅ paper_fill.position_id is FK
- ✅ paper_order CHECK constraints restored
- ✅ ensure_initial_deposit has an explicit idempotent test
- ✅ Grep tests scoped to production code paths
- ✅ Protocol method-set test uses `__dict__`
- ✅ E2E uses only NY session days; Memorial Day edge-case has its own test

**Plan status: Approved with required revisions applied — ready for implementation.**

---

## Round-7 Final Tightening (applied inline above + new architectural test)

The round-7 review surfaced 10 issues. Items 1, 2, 3, 4, 5, 6, 9 are
already applied inline (PaperFill FK restored, Protocol `__dict__`, float
rounding, typed `BlockedBidReason`, `event_time: datetime`,
`ALLOCATOR_VERSION = "phase6a-v1"`, `test_rejection_audit_committed_
before_exception`). Three items remain:

### R7-A (Risk 7): Strip comments before grep boundary tests

When implementing Task 6a-0.5 (extraction-boundary grep) and Task 6a-2.8
(single-writer grep), strip Python comments BEFORE searching:

```python
import io
import tokenize

def _strip_comments(src: str) -> str:
    """Remove # comments and docstrings so grep tests don't false-fire
    on documentation that mentions forbidden tokens."""
    out: list[str] = []
    prev_type: int | None = None
    tokens = tokenize.tokenize(io.BytesIO(src.encode("utf-8")).readline)
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and prev_type in (
            tokenize.INDENT, tokenize.NEWLINE, None,
        ):
            # Likely a docstring at module/class/function start — skip.
            continue
        out.append(tok.string)
        prev_type = tok.type
    return " ".join(out)
```

Apply to the boundary grep:

```python
src = _strip_comments(Path("marketpulse/backtest/allocation.py").read_text())
for token in forbidden:
    assert token not in src, f"6a-L1 boundary violation: '{token}'..."
```

This prevents the `# daily_equity_curve stays outside allocator` comment
from tripping the test.

### R7-B (Risk 8): Holiday-replay-skip invariant test

When the scheduler fires on a non-session NY day (e.g., Memorial Day),
`today_ny_trading_date` rolls back to the previous session. Without a
deduplication guard, that previous day's tick could be re-processed
incorrectly. The deduplication is already provided by:

- `repository.last_processed_tick_date()` returns the resolved date
- `daily_cycle.run` only writes `TICK_COMPLETED` via `write_tick_completed_once`
  (no-op if a row for that tick_date already exists)
- `place_order` idempotency keys are deterministic per `(strategy, ticker,
  event_time, allocation_run_id)` where `allocation_run_id = paper-{tick_date}`

But this invariant is implicit. Add an explicit test (Task 6a-4.1):

```python
def test_tick_on_memorial_day_resolves_to_previous_friday_and_is_replay(session):
    """Scheduler accidentally firing on Memorial Day Mon 25 → cycle's
    tick_date resolves to previous Friday 22. If Friday already had a
    successful TICK_COMPLETED, the rerun must be a no-op replay:
        - 0 new paper_order rows
        - 0 new ORDER_PLACED_DUPLICATE rows (the dedup-window guarantees this)
        - 0 new TICK_COMPLETED rows (dedup)
        - 0 new SCHEDULER_GAP_DETECTED rows
    """
    from datetime import UTC, date, datetime
    from sqlalchemy import select
    from marketpulse.db.models import PaperAuditEvent, PaperOrder
    from marketpulse.trading import daily_cycle

    # 1. Set up a successful Friday tick (D0 = Fri 2026-05-22)
    # ... (use the same _make_deps pattern as test_daily_cycle.py, with
    # FakeClock set to Fri 21:30 UTC = 17:30 NY)
    # Run daily_cycle.run once → 1 TICK_COMPLETED for D0.

    # 2. Simulate Memorial Day firing: clock at Mon 2026-05-25 22:00 UTC
    #    (18:00 NY).  today_ny_trading_date should resolve to Fri 2026-05-22.
    # ... run daily_cycle.run

    # 3. Assertions:
    paper_order_count = session.scalar(select(func.count(PaperOrder.id))) or 0
    tick_completed_count = session.scalar(
        select(func.count(PaperAuditEvent.id))
        .where(PaperAuditEvent.event_type == "TICK_COMPLETED")
    ) or 0
    gap_audit_count = session.scalar(
        select(func.count(PaperAuditEvent.id))
        .where(PaperAuditEvent.event_type == "SCHEDULER_GAP_DETECTED")
    ) or 0
    # Whatever the Friday run produced is unchanged; Memorial Day run was no-op.
    # (Concrete numbers depend on the fixture; the test asserts no NEW writes
    # vs. the post-Friday-run snapshot — capture counts after step 1 and
    # compare here.)
    # ...
```

The implementer captures snapshot counts after step 1 and asserts equality
after step 2.

### R7-C (Architectural): Repository-boundary architecture test

Add a new test file early in 6a-2 (right after `test_invariant_greps.py`)
that enforces the single-writer architecture as a permanent guard:

**Files:**
- Create: `tests/architecture/__init__.py`
- Create: `tests/architecture/test_repository_boundary.py`

- [ ] **Step 1: Write the architectural boundary test**

```python
# tests/architecture/test_repository_boundary.py
# Layer: invariant
"""Permanent guard: ONLY repository.py is allowed to write paper_*
tables. Production code outside marketpulse/trading/repository.py
must not call session.add(), session.execute(insert/update), or
session.commit() on paper_* models.

This is the load-bearing single-writer architecture lock (umbrella
lock iii). Any new module that needs to mutate paper_* state MUST
go through repository.py."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROD_ROOT = Path("marketpulse")
REPOSITORY_PATH = PROD_ROOT / "trading" / "repository.py"

# Paths excluded from the boundary check — they own their own tables,
# NOT paper_*. (db/ holds Base + TZDateTime + engine setup; not a writer
# of paper_*.) Add only when a new module genuinely doesn't write paper_*.
EXEMPT = {
    REPOSITORY_PATH,
    PROD_ROOT / "db" / "base.py",
    PROD_ROOT / "db" / "models.py",  # declarative-base definitions, not writes
}

# Sentinel call names that indicate state mutation against any table.
FORBIDDEN_CALLS = {
    "add",      # session.add(...)
    "commit",   # session.commit()
    "merge",    # session.merge(...)
    "delete",   # session.delete(...)
}


def _find_session_mutation_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """Return (lineno, call_name) for each session.<forbidden>(...) call."""
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match attribute calls like session.add(...) / self._session.add(...)
        if isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_CALLS:
            hits.append((node.lineno, f".{func.attr}(...)"))
    return hits


def _find_insert_update_execute(tree: ast.AST) -> list[tuple[int, str]]:
    """Return hits for session.execute(insert(...)) / .execute(update(...))."""
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "execute"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name):
                if arg.func.id in ("insert", "update", "delete"):
                    hits.append((node.lineno, f"execute({arg.func.id}(...))"))
    return hits


def test_repository_is_single_writer():
    violations: list[str] = []
    for path in PROD_ROOT.rglob("*.py"):
        if path in EXEMPT:
            continue
        # Skip test directories (we're under tests/architecture which
        # itself is below tests/, not marketpulse/, so the rglob excludes
        # them — defensive guard only).
        if "tests" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue

        for lineno, call in _find_session_mutation_calls(tree):
            violations.append(f"{path}:{lineno}  {call}")
        for lineno, call in _find_insert_update_execute(tree):
            violations.append(f"{path}:{lineno}  {call}")

    assert not violations, (
        "Single-writer architecture violated (lock iii). Mutations of "
        "paper_* state must go through marketpulse/trading/repository.py. "
        "Violations:\n  " + "\n  ".join(sorted(violations))
    )
```

- [ ] **Step 2: Smoke**

```bash
uv run pytest tests/architecture/test_repository_boundary.py -v
```

Expected: PASS (if you've already added Phase 1-5 modules that call
session.add for non-paper tables, add them to `EXEMPT`).

- [ ] **Step 3: Commit**

```bash
git add tests/architecture/
git commit -m "test(arch): single-writer boundary guard for paper_* tables (lock iii)"
```

This test runs in CI permanently. Any future module that tries to
INSERT/UPDATE/DELETE paper_* state outside repository.py fails the
build. The check is AST-based, not grep-based — comments, strings,
and similar tokens in docstrings cannot trip it.

---

## Round-7 Final Self-Review

- ✅ `PaperFill.position_id` is FK (Blocker 1 — fixed inline in model + migration)
- ✅ Protocol method-set test uses `__dict__` (Blocker 2 — fixed inline in test code)
- ✅ Baseline `_normalize` rounds floats to 10 decimal places (Blocker 3)
- ✅ `BlockedBidReason` typed dataclass replaces `tuple[object, ...]` (Risk 4)
- ✅ `event_time: datetime` (Risk 5 — BidCandidate + AllocationWinner both fixed)
- ✅ `ALLOCATOR_VERSION = "phase6a-v1"` (Risk 6)
- ✅ Grep tests strip comments via tokenize (Risk 7 — R7-A)
- ✅ Holiday replay invariant test added (Risk 8 — R7-B)
- ✅ `test_rejection_audit_committed_before_exception` added (Risk 9 — fixed inline)
- ✅ `cash_used` / `cash_remaining` authority contract documented (Risk 10)
- ✅ `tests/architecture/test_repository_boundary.py` AST-based guard (R7-C)

**Plan status: Approved with minor revisions applied — ready for agent-driven implementation.**
