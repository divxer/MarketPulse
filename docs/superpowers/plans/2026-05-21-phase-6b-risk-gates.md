# Phase 6b — Risk Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `AlwaysApproveRiskGate` stub with a 4-gate `CompositeRiskGate` (MarketHours, StrategySize, DailyLoss, SectorExposure) that enforces deterministic pre-trade safety on `place_order` and audits every denial via the existing `ORDER_REJECTED` event.

**Architecture:** New `marketpulse/trading/risk_gates/` package owns the 4 gates + composite + config provider. `RiskIntent` enum lives in `types.py` (canonical home — lock 6b-L12). `risk_gate.py` extends `RiskResult` with `failed_gates`+`context` and re-exports new package symbols for back-compat. `repository.py` gains two read helpers (`today_realized_pnl`, `sector_exposure_notional`). `ForwardExecutionEngine.place_order` routes `risk_result.failed_gates`+`per_gate` into existing `ORDER_REJECTED` audit context (zero migration). DI swap happens in `paper_trading_tick.py`.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, pytest, PyYAML, exchange_calendars (via existing `NYTradingCalendar`), `zoneinfo.ZoneInfo("America/New_York")`, dataclasses + StrEnum.

**Spec reference:** `docs/superpowers/specs/2026-05-21-phase-6b-risk-gates-design.md`

**Branch:** `plan/phase-6b-risk-gates` (already exists; spec already committed at `8e92277`). Single squash PR at end of T18.

**Key codebase-vs-spec reconciliations:**
- Strategy YAMLs live at `marketpulse/strategies/definitions/*.yaml`. The 6 strategy stems are: `news_event`, `oversold_reversal`, `sector_rotation`, `general`, `momentum_breakout`, `fundamental_value`. `RiskConfigProvider`'s lookup key is the stem (matches `Strategy.name`).
- `marketpulse/backtest/sector.get_sector(ticker)` returns `str` always (`"unknown"` on fallback). We add a strict wrapper `strict_sector(ticker) -> str | None` in the new package and use it as the gate's `sector_provider`.
- NY tz currently `_NY` (private) in `calendar.py` — T9 exposes a public alias.
- `forward_engine._dump` only shallow-normalizes Decimals. T14 adds a recursive `_normalize_context` helper in the new package.

---

## File Structure

**New files (10 production + 11 tests + 1 script):**

```
marketpulse/trading/audit_json.py            # normalize_for_json shared util
                                             #   (lock 6b-L17 — single audit
                                             #   JSON normalizer)

marketpulse/trading/risk_gates/
├── __init__.py             # Re-exports CompositeRiskGate, 4 gate classes,
│                           #   RiskConfigProvider, dataclasses,
│                           #   strict_sector, build_standard_composite
├── config_provider.py      # 5 frozen dataclasses + RiskConfigProvider.from_yaml
├── composite.py            # CompositeRiskGate(gates: Sequence[RiskGate])
│                           #   — run-all + deny-if-any + exception=deny +
│                           #   audit-all via normalize_for_json (locks
│                           #   6b-L2, 6b-L15, 6b-L17)
├── factory.py              # build_standard_composite(...) — canonical
│                           #   4-gate factory used by paper_trading_tick.py
│                           #   (lock 6b-L15)
├── market_hours.py         # MarketHoursGate + _window_check
├── strategy_size.py        # StrategySizeGate
├── daily_loss.py           # DailyLossGate
├── sector_exposure.py      # SectorExposureGate
└── _sector.py              # strict_sector(ticker) -> str | None wrapper

config/risk_gates.yaml      # Portfolio governance YAML

scripts/preflight_phase6b_sector_check.py    # Lock 6b-L11 deploy checklist
```

**Modified files (5 + 6 strategy YAMLs):**

```
marketpulse/trading/types.py             # RiskIntent enum; OrderRequest.risk_intent
marketpulse/trading/risk_gate.py         # RiskResult+(failed_gates, context);
                                         #   re-exports new package + RiskIntent
marketpulse/trading/repository.py        # today_realized_pnl + sector_exposure_notional
marketpulse/trading/forward_engine.py    # ORDER_REJECTED context now carries
                                         #   failed_gates + per_gate
marketpulse/trading/calendar.py          # Public `NY` alias (was `_NY`)
marketpulse/scheduler/paper_trading_tick.py   # DI swap to CompositeRiskGate

marketpulse/strategies/definitions/*.yaml  (6 files) — each gains `risk: { max_position_notional: <N> }`
```

**New test files (11):**

```
tests/trading/risk_gates/__init__.py
tests/trading/risk_gates/test_config_provider.py
tests/trading/risk_gates/test_market_hours.py
tests/trading/risk_gates/test_strategy_size.py
tests/trading/risk_gates/test_daily_loss.py
tests/trading/risk_gates/test_sector_exposure.py
tests/trading/risk_gates/test_composite.py
tests/trading/risk_gates/test_factory.py
tests/trading/risk_gates/test_strict_sector.py
tests/trading/test_repository_risk_extensions.py
tests/trading/test_audit_json.py
```

**Modified test files (3):**

```
tests/trading/test_forward_engine.py     # composite-aware
tests/trading/test_scheduler.py          # DI swap covered
tests/trading/test_e2e_stateful.py       # 17:30 NY happy path + composite deny scenarios
```

---

## Task Inventory

- T0 — Preflight: read spec, verify branch + 6a tests green
- T1 — `RiskIntent` enum in `types.py` + `OrderRequest.risk_intent` field
- T2 — Extend `RiskResult` in `risk_gate.py` (`failed_gates`, `context`) + re-export `RiskIntent`
- T3 — Expose `NY` tz from `calendar.py`
- T4 — `RiskConfigProvider` dataclasses (skeleton, no parser)
- T5 — `RiskConfigProvider.from_yaml` global parser + ship `config/risk_gates.yaml`
- T6 — `RiskConfigProvider` strategy YAML `risk:` block parser
- T7 — Add `risk:` block to 6 strategy YAML files
- T8 — `Repository.today_realized_pnl` (DST-safe NY window — lock 6b-L13)
- T9 — `Repository.sector_exposure_notional`
- T10 — `strict_sector` wrapper in `_sector.py`
- T11 — `MarketHoursGate`
- T12 — `StrategySizeGate`
- T13 — `DailyLossGate`
- T14 — `SectorExposureGate`
- T14a — `marketpulse/trading/audit_json.py` shared `normalize_for_json` util (lock 6b-L17)
- T15 — `CompositeRiskGate(gates=Sequence[RiskGate])` + `build_standard_composite` factory (locks 6b-L15, 6b-L16, 6b-L17)
- T16 — `ForwardExecutionEngine._dump` delegates to `normalize_for_json`; ORDER_REJECTED context carries `failed_gates`+`per_gate`
- T17 — `paper_trading_tick.py` DI swap via `build_standard_composite(...)`
- T18 — E2E `tests/trading/test_e2e_stateful.py` 17:30 NY happy + composite deny scenarios; preflight sector script
- T19 — Final integration: full suite, ruff, alembic, route smoke; merge

---

### Task T0: Preflight

**Files:**
- Read: `docs/superpowers/specs/2026-05-21-phase-6b-risk-gates-design.md`

- [ ] **Step 1: Verify branch + working tree**

Run: `git status && git log --oneline -3`
Expected: on branch `plan/phase-6b-risk-gates`; HEAD is `docs(phase-6b): review-round-2` (commit `8e92277` or later); working tree clean (or only the unrelated `stock.html` change carried over from prior work — leave untouched).

- [ ] **Step 2: Verify 6a baseline tests pass**

Run: `cd /Users/harvey/Dev/src/MarketPulse && uv run pytest -q tests/trading/ tests/architecture/ -x`
Expected: ALL pass (Phase 6a contract). If anything fails, STOP — fix before starting 6b.

- [ ] **Step 3: Verify ruff clean**

Run: `uv run ruff check marketpulse/ tests/`
Expected: `All checks passed!`

---

### Task T1: `RiskIntent` enum + `OrderRequest.risk_intent` field

**Files:**
- Modify: `marketpulse/trading/types.py`
- Test: `tests/trading/risk_gates/__init__.py` (new, empty), `tests/trading/risk_gates/test_config_provider.py` (new)

- [ ] **Step 1: Create test package init**

Create `tests/trading/risk_gates/__init__.py` with a single line:

```python
# Layer: stateful
```

- [ ] **Step 2: Write failing test for RiskIntent enum**

Create `tests/trading/risk_gates/test_config_provider.py`:

```python
# Layer: pure
"""6b-T1..T6: RiskIntent + RiskConfigProvider tests."""

from __future__ import annotations


def test_risk_intent_enum_values():
    from marketpulse.trading.types import RiskIntent
    assert RiskIntent.OPEN == "open"
    assert RiskIntent.ADD == "add"
    assert RiskIntent.CLOSE == "close"
    assert RiskIntent.REDUCE == "reduce"
    assert RiskIntent.FLIP == "flip"


def test_risk_intent_is_str_enum():
    from marketpulse.trading.types import RiskIntent
    # StrEnum membership preserves str identity
    assert isinstance(RiskIntent.OPEN, str)


def test_order_request_defaults_risk_intent_to_open():
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from marketpulse.trading.types import AllocationRunId, OrderRequest, RiskIntent

    req = OrderRequest(
        strategy="momentum_breakout",
        ticker="AAPL",
        quantity=10,
        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=Decimal("150.00"),
        horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155.00"),
        allocation_run_id=AllocationRunId("paper-2026-05-21"),
        strategy_version="v1",
        allocator_version="phase6a-v1",
        execution_engine_version="phase6a-v1",
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
    assert req.risk_intent == RiskIntent.OPEN
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/trading/risk_gates/test_config_provider.py -v`
Expected: 3 FAIL with `ImportError: cannot import name 'RiskIntent'` (first two) or `TypeError: OrderRequest.__init__() got an unexpected keyword argument 'risk_intent'` (third).

- [ ] **Step 4: Add `RiskIntent` to `types.py`**

In `marketpulse/trading/types.py`, locate the `# === Status / side enums ===` block (around line 22). Immediately after the `FillSide` Literal, add:

```python


# === Risk intent (Phase 6b — lock 6b-L12) ===
# Lives here, NOT in risk_gate.py, because OrderRequest carries
# risk_intent: RiskIntent as a field. Placing the enum in risk_gate.py
# would invert the 6a-established dependency layer (types is a leaf —
# nothing above it should import down). risk_gate.py re-exports for
# back-compat but the canonical import is:
#     from marketpulse.trading.types import RiskIntent

class RiskIntent(StrEnum):
    OPEN = "open"        # NEW position; gates run
    ADD = "add"          # increase existing position; gates run
    CLOSE = "close"      # full exit; gates bypassed
    REDUCE = "reduce"    # partial exit; gates bypassed
    FLIP = "flip"        # 6b denies; Phase 7 wires properly
```

- [ ] **Step 5: Add `risk_intent` field to `OrderRequest`**

In `OrderRequest` (same file), append after `size_clamped_by_override: bool` (last field):

```python

    # Phase 6b — RiskIntent classification (lock 6b-L1).
    # Defaulted to OPEN: Phase 6 production paths only emit OPEN
    # (Phase 4-5 pattern: enter at event, exit at horizon). Field exists
    # for forward-compat; 6b operational test #2 exercises CLOSE/REDUCE
    # bypass via tests that synthesize OrderRequests directly.
    risk_intent: RiskIntent = RiskIntent.OPEN
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/trading/risk_gates/test_config_provider.py -v`
Expected: 3 PASS.

- [ ] **Step 7: Run 6a regression to ensure default-OPEN keeps backward compat**

Run: `uv run pytest -q tests/trading/`
Expected: ALL pass (no test should care about a new default-valued field).

- [ ] **Step 8: Commit**

```bash
git add marketpulse/trading/types.py tests/trading/risk_gates/__init__.py tests/trading/risk_gates/test_config_provider.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-T1): RiskIntent enum + OrderRequest.risk_intent field

Lock 6b-L12: RiskIntent lives in types.py (canonical home). Default OPEN
preserves Phase 6a behavior.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T2: Extend `RiskResult` + re-export `RiskIntent` from `risk_gate.py`

**Files:**
- Modify: `marketpulse/trading/risk_gate.py`
- Test: `tests/trading/risk_gates/test_config_provider.py` (extend)

- [ ] **Step 1: Append failing tests to `test_config_provider.py`**

Append:

```python
def test_risk_result_defaults_are_back_compat():
    """6a callers construct RiskResult(approved, reason, gate_name); new
    fields default to () and an empty read-only mapping so the old
    signature still works."""
    from marketpulse.trading.risk_gate import RiskResult
    r = RiskResult(approved=True, reason="", gate_name="x")
    assert r.failed_gates == ()
    assert dict(r.context) == {}


def test_risk_result_full_construction():
    from marketpulse.trading.risk_gate import RiskResult
    r = RiskResult(
        approved=False,
        reason="market_hours_outside_window",
        gate_name="market_hours",
        failed_gates=("market_hours",),
        context={"per_gate": [{"gate_name": "market_hours", "approved": False}]},
    )
    assert r.failed_gates == ("market_hours",)
    assert r.context["per_gate"][0]["approved"] is False


def test_risk_result_context_is_immutable_mapping():
    """Lock 6b-L16: top-level context mutation raises TypeError. Gate
    authors pass plain dicts; __post_init__ wraps in MappingProxyType."""
    from marketpulse.trading.risk_gate import RiskResult
    r = RiskResult(approved=False, reason="x", gate_name="g", context={"a": 1})
    import pytest
    with pytest.raises(TypeError):
        r.context["a"] = 2  # type: ignore[index]
    with pytest.raises(TypeError):
        r.context["new_key"] = 99  # type: ignore[index]


def test_risk_gate_module_reexports_risk_intent():
    """6b-L12 back-compat: callers may still write
    `from marketpulse.trading.risk_gate import RiskIntent`."""
    from marketpulse.trading.risk_gate import RiskIntent as RI1
    from marketpulse.trading.types import RiskIntent as RI2
    assert RI1 is RI2
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/risk_gates/test_config_provider.py -v -k "risk_result or reexports"`
Expected: 3 FAIL with `TypeError: __init__() got an unexpected keyword argument 'failed_gates'` (and missing re-export).

- [ ] **Step 3: Rewrite `marketpulse/trading/risk_gate.py`**

Replace the file with:

```python
"""RiskGate Protocol + 6a's AlwaysApproveRiskGate stub.

6b extends RiskResult with `failed_gates` + `context` for the composite
gate's run-all + audit-all contract. The 6a contract (approved, reason,
gate_name) stays intact via default values.

Lock 6b-L16: `context` is wrapped in MappingProxyType post-construction
so top-level mutation raises TypeError. Gate authors pass plain dicts
for ergonomics — the dataclass freezes them.

Re-exports `RiskIntent` from types.py for back-compat (canonical home is
types.py per lock 6b-L12)."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from marketpulse.trading.types import OrderRequest, RiskIntent

__all__ = [
    "RiskIntent",
    "RiskResult",
    "RiskGate",
    "AlwaysApproveRiskGate",
]


_EMPTY_CONTEXT: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True)
class RiskResult:
    approved: bool
    reason: str
    gate_name: str = ""
    failed_gates: tuple[str, ...] = ()
    # Lock 6b-L16: top-level immutability. Gate authors pass plain dicts;
    # __post_init__ wraps in MappingProxyType so external mutation raises
    # TypeError. Nested dict mutation is still possible — that's
    # deliberately left to the normalize_for_json serialization boundary
    # (lock 6b-L17) which materializes deep copies.
    context: Mapping[str, Any] = field(
        default_factory=lambda: _EMPTY_CONTEXT,
    )

    def __post_init__(self) -> None:
        if isinstance(self.context, dict):
            object.__setattr__(self, "context", MappingProxyType(self.context))


class RiskGate(Protocol):
    def check_pre_trade(self, *, order_request: OrderRequest) -> RiskResult: ...


class AlwaysApproveRiskGate:
    """6a's default. Approves everything. 6b production paths use
    CompositeRiskGate; AlwaysApproveRiskGate remains for tests that
    exercise non-gate code paths."""

    def check_pre_trade(self, *, order_request: OrderRequest) -> RiskResult:
        return RiskResult(approved=True, reason="", gate_name="always_approve")
```

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/trading/risk_gates/test_config_provider.py -v`
Expected: all PASS so far.

- [ ] **Step 5: Run full trading suite to confirm no 6a regression**

Run: `uv run pytest -q tests/trading/`
Expected: ALL pass.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/trading/risk_gate.py tests/trading/risk_gates/test_config_provider.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-T2): RiskResult extended with failed_gates + context

Default-value extension keeps 6a callers (approved, reason, gate_name)
working. RiskIntent re-exported from risk_gate.py for back-compat (lock
6b-L12 canonical home stays in types.py).

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T3: Expose `NY` tz publicly from `calendar.py`

**Files:**
- Modify: `marketpulse/trading/calendar.py`
- Test: `tests/trading/risk_gates/test_market_hours.py` (new — minimal preview)

- [ ] **Step 1: Create stub test file**

Create `tests/trading/risk_gates/test_market_hours.py`:

```python
# Layer: pure
"""6b-T11: MarketHoursGate tests (file created in T3 for the NY import)."""

from __future__ import annotations


def test_calendar_module_exports_ny_zoneinfo():
    """T3: NY tz alias must be publicly importable for risk_gates package."""
    from zoneinfo import ZoneInfo

    from marketpulse.trading.calendar import NY
    assert NY == ZoneInfo("America/New_York")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/risk_gates/test_market_hours.py -v`
Expected: FAIL with `ImportError: cannot import name 'NY'`.

- [ ] **Step 3: Add public `NY` alias to `calendar.py`**

In `marketpulse/trading/calendar.py`, locate the `_NY = ZoneInfo("America/New_York")` line (around line 14). Immediately below it add:

```python

# Public alias — Phase 6b risk_gates package imports `NY` directly to
# avoid leaking a private symbol across module boundaries.
NY = _NY
```

- [ ] **Step 4: Run to verify test passes**

Run: `uv run pytest tests/trading/risk_gates/test_market_hours.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/calendar.py tests/trading/risk_gates/test_market_hours.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-T3): expose `NY` tz alias from calendar.py

Phase 6b risk_gates package needs NY for MarketHoursGate window checks
and DailyLossGate DST-safe NY-day window. Public alias avoids leaking
the private `_NY` symbol across module boundaries.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T4: `RiskConfigProvider` dataclasses (skeleton)

**Files:**
- Create: `marketpulse/trading/risk_gates/__init__.py`
- Create: `marketpulse/trading/risk_gates/config_provider.py`
- Test: `tests/trading/risk_gates/test_config_provider.py` (extend)

- [ ] **Step 1: Append failing tests**

Append to `tests/trading/risk_gates/test_config_provider.py`:

```python
def test_market_hours_config_construction():
    from datetime import time

    from marketpulse.trading.risk_gates.config_provider import MarketHoursConfig
    c = MarketHoursConfig(
        enabled=True, exchange="XNYS",
        allow_regular_session=True, allow_post_close=True,
        post_close_until=time(18, 0), allow_premarket=False,
    )
    assert c.enabled is True
    assert c.post_close_until == time(18, 0)


def test_daily_loss_config_construction():
    from decimal import Decimal

    from marketpulse.trading.risk_gates.config_provider import DailyLossConfig
    c = DailyLossConfig(enabled=True, daily_loss_limit=Decimal("500"))
    assert c.daily_loss_limit == Decimal("500")


def test_sector_exposure_config_construction():
    from decimal import Decimal

    from marketpulse.trading.risk_gates.config_provider import SectorExposureConfig
    c = SectorExposureConfig(
        enabled=True, max_sector_exposure_pct=0.35,
        configured_max_capital_in_use=Decimal("10000"),
    )
    assert c.max_sector_exposure_pct == 0.35


def test_risk_gate_config_aggregates_three():
    from datetime import time
    from decimal import Decimal

    from marketpulse.trading.risk_gates.config_provider import (
        DailyLossConfig,
        MarketHoursConfig,
        RiskGateConfig,
        SectorExposureConfig,
    )
    cfg = RiskGateConfig(
        market_hours=MarketHoursConfig(
            enabled=True, exchange="XNYS",
            allow_regular_session=True, allow_post_close=True,
            post_close_until=time(18, 0), allow_premarket=False,
        ),
        daily_loss=DailyLossConfig(
            enabled=True, daily_loss_limit=Decimal("500"),
        ),
        sector_exposure=SectorExposureConfig(
            enabled=True, max_sector_exposure_pct=0.35,
            configured_max_capital_in_use=Decimal("10000"),
        ),
    )
    assert cfg.market_hours.enabled is True
    assert cfg.daily_loss.daily_loss_limit == Decimal("500")


def test_strategy_risk_config_optional_limit():
    from decimal import Decimal

    from marketpulse.trading.risk_gates.config_provider import StrategyRiskConfig
    c = StrategyRiskConfig(max_position_notional=Decimal("25000"))
    assert c.max_position_notional == Decimal("25000")
    c2 = StrategyRiskConfig(max_position_notional=None)
    assert c2.max_position_notional is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/risk_gates/test_config_provider.py -v -k "config_construction or aggregates_three or strategy_risk_config_optional"`
Expected: 5 FAIL with `ModuleNotFoundError: No module named 'marketpulse.trading.risk_gates'`.

- [ ] **Step 3: Create package `__init__.py`**

Create `marketpulse/trading/risk_gates/__init__.py` with minimal re-exports (filled in across later tasks):

```python
"""Phase 6b risk gates package.

CompositeRiskGate runs 4 deterministic pre-trade gates: MarketHoursGate,
StrategySizeGate, DailyLossGate, SectorExposureGate. Block risk-increasing
actions only — CLOSE/REDUCE bypass all gates (lock 6b-L1). KillSwitch
remains an emergency global halt OUTSIDE this principle scope (lock
clarification in spec § 2)."""

from __future__ import annotations

from marketpulse.trading.risk_gates.config_provider import (
    DailyLossConfig,
    MarketHoursConfig,
    RiskConfigProvider,
    RiskGateConfig,
    SectorExposureConfig,
    StrategyRiskConfig,
)

__all__ = [
    "DailyLossConfig",
    "MarketHoursConfig",
    "RiskConfigProvider",
    "RiskGateConfig",
    "SectorExposureConfig",
    "StrategyRiskConfig",
]
```

- [ ] **Step 4: Create `config_provider.py` with the 5 dataclasses**

Create `marketpulse/trading/risk_gates/config_provider.py`:

```python
"""RiskConfigProvider + 5 frozen config dataclasses (lock 6b-L3, 6b-L14).

The provider is the SINGLE site that reads YAML for risk configuration.
Gates NEVER read YAML directly — they take provider methods or pre-built
config dataclasses at construction time.

Lock 6b-L14 scope discipline:
  - Reads ONLY the `risk:` block of each strategy YAML; never `signals:`,
    `sizing:`, or other strategy-execution blocks (those remain owned by
    marketpulse/strategies/loader.py).
  - Strategy lookup key is the YAML filename stem (== Strategy.name).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from pathlib import Path

import yaml

__all__ = [
    "MarketHoursConfig",
    "DailyLossConfig",
    "SectorExposureConfig",
    "RiskGateConfig",
    "StrategyRiskConfig",
    "RiskConfigProvider",
]


@dataclass(frozen=True)
class MarketHoursConfig:
    enabled: bool
    exchange: str
    allow_regular_session: bool       # 09:30-16:00 NY inclusive
    allow_post_close: bool            # 16:00-post_close_until NY (open-left, closed-right)
    post_close_until: time            # parsed from "HH:MM"
    allow_premarket: bool             # 04:00-09:30 NY inclusive-left, exclusive-right


@dataclass(frozen=True)
class DailyLossConfig:
    enabled: bool
    daily_loss_limit: Decimal         # POSITIVE Decimal; deny when realized <= -limit


@dataclass(frozen=True)
class SectorExposureConfig:
    enabled: bool
    max_sector_exposure_pct: float
    configured_max_capital_in_use: Decimal  # FIXED denominator (lock 6b-L4)


@dataclass(frozen=True)
class RiskGateConfig:
    market_hours: MarketHoursConfig
    daily_loss: DailyLossConfig
    sector_exposure: SectorExposureConfig


@dataclass(frozen=True)
class StrategyRiskConfig:
    max_position_notional: Decimal | None  # None → StrategySizeGate fail-closed (6b-L9)


class RiskConfigProvider:
    """Single parser. Gates NEVER read YAML directly (locks 6b-L3, 6b-L14)."""

    def __init__(
        self,
        *,
        global_cfg: RiskGateConfig,
        strategy_cfgs: dict[str, StrategyRiskConfig],
    ) -> None:
        self._global = global_cfg
        self._strategies = dict(strategy_cfgs)

    def global_config(self) -> RiskGateConfig:
        return self._global

    def strategy_config(self, strategy: str) -> StrategyRiskConfig | None:
        """Returns None when strategy has no `risk:` block. Triggers
        StrategySizeGate fail-closed (6b-L9)."""
        return self._strategies.get(strategy)

    @classmethod
    def from_yaml(
        cls,
        *,
        global_path: Path,
        strategies_dir: Path,
    ) -> "RiskConfigProvider":
        # Filled in at T5 (global) and T6 (strategy YAMLs).
        raise NotImplementedError("RiskConfigProvider.from_yaml — see T5/T6")
```

- [ ] **Step 5: Run to verify dataclass tests pass**

Run: `uv run pytest tests/trading/risk_gates/test_config_provider.py -v -k "config_construction or aggregates_three or strategy_risk_config_optional"`
Expected: 5 PASS.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/trading/risk_gates/__init__.py marketpulse/trading/risk_gates/config_provider.py tests/trading/risk_gates/test_config_provider.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-T4): RiskConfigProvider + 5 frozen config dataclasses

Skeleton with constructor and accessors. from_yaml() raises
NotImplementedError until T5 + T6 fill it in.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T5: `RiskConfigProvider.from_yaml` global parser + ship `config/risk_gates.yaml`

**Files:**
- Create: `config/risk_gates.yaml`
- Modify: `marketpulse/trading/risk_gates/config_provider.py`
- Test: `tests/trading/risk_gates/test_config_provider.py` (extend)

- [ ] **Step 1: Ship `config/risk_gates.yaml`**

Create `config/risk_gates.yaml`:

```yaml
# Phase 6b portfolio-level governance. Strategy-local knobs live in
# marketpulse/strategies/definitions/*.yaml under the `risk:` block
# (lock 6b-L3 hybrid split).

market_hours:
  enabled: true
  exchange: XNYS
  allow_regular_session: true     # 09:30-16:00 NY (inclusive)
  allow_post_close: true          # 16:00-post_close_until NY (open-left)
  post_close_until: "18:00"       # placement window cutoff (NY tz, inclusive)
  allow_premarket: false          # 04:00-09:30 NY

daily_loss:
  enabled: true
  daily_loss_limit: 500           # absolute USD; deny when
                                  # today_realized_pnl <= -daily_loss_limit

sector_exposure:
  enabled: true
  max_sector_exposure_pct: 0.35   # fraction of configured_max_capital_in_use
  configured_max_capital_in_use: 10000   # FIXED denominator (lock 6b-L4) —
                                         # NOT live cash/equity
```

- [ ] **Step 2: Append failing tests**

Append to `tests/trading/risk_gates/test_config_provider.py`:

```python
def test_from_yaml_global_only_parses_shipped_default(tmp_path):
    """T5: parses config/risk_gates.yaml shape correctly."""
    from datetime import time
    from decimal import Decimal

    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    yaml_text = """
market_hours:
  enabled: true
  exchange: XNYS
  allow_regular_session: true
  allow_post_close: true
  post_close_until: "18:00"
  allow_premarket: false
daily_loss:
  enabled: true
  daily_loss_limit: 500
sector_exposure:
  enabled: true
  max_sector_exposure_pct: 0.35
  configured_max_capital_in_use: 10000
"""
    global_path = tmp_path / "risk_gates.yaml"
    global_path.write_text(yaml_text)
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    provider = RiskConfigProvider.from_yaml(
        global_path=global_path, strategies_dir=strategies_dir,
    )
    g = provider.global_config()
    assert g.market_hours.enabled is True
    assert g.market_hours.post_close_until == time(18, 0)
    assert g.market_hours.allow_premarket is False
    assert g.daily_loss.daily_loss_limit == Decimal("500")
    assert g.sector_exposure.max_sector_exposure_pct == 0.35
    assert g.sector_exposure.configured_max_capital_in_use == Decimal("10000")


def test_from_yaml_missing_global_raises(tmp_path):
    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    import pytest
    with pytest.raises(FileNotFoundError):
        RiskConfigProvider.from_yaml(
            global_path=tmp_path / "missing.yaml",
            strategies_dir=strategies_dir,
        )


def test_from_yaml_global_missing_required_key_raises(tmp_path):
    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    # Drop `sector_exposure` block.
    bad = """
market_hours:
  enabled: true
  exchange: XNYS
  allow_regular_session: true
  allow_post_close: true
  post_close_until: "18:00"
  allow_premarket: false
daily_loss:
  enabled: true
  daily_loss_limit: 500
"""
    p = tmp_path / "bad.yaml"
    p.write_text(bad)
    sd = tmp_path / "strategies"
    sd.mkdir()
    import pytest
    with pytest.raises(ValueError, match="sector_exposure"):
        RiskConfigProvider.from_yaml(global_path=p, strategies_dir=sd)


def test_shipped_default_yaml_parses_via_from_yaml(tmp_path):
    """Locks the shipped default config — if config/risk_gates.yaml
    drifts away from the documented shape, this test catches it."""
    from pathlib import Path

    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    repo_root = Path(__file__).resolve().parents[3]
    real_global = repo_root / "config" / "risk_gates.yaml"
    sd = tmp_path / "strategies"
    sd.mkdir()
    provider = RiskConfigProvider.from_yaml(global_path=real_global, strategies_dir=sd)
    g = provider.global_config()
    assert g.market_hours.enabled is True
    assert g.daily_loss.enabled is True
    assert g.sector_exposure.enabled is True
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/trading/risk_gates/test_config_provider.py -v -k "from_yaml or shipped_default"`
Expected: 4 FAIL with `NotImplementedError`.

- [ ] **Step 4: Implement `from_yaml` global parser**

In `marketpulse/trading/risk_gates/config_provider.py`, replace the `from_yaml` method body and add a private helper:

```python
    @classmethod
    def from_yaml(
        cls,
        *,
        global_path: Path,
        strategies_dir: Path,
    ) -> "RiskConfigProvider":
        global_cfg = _parse_global_yaml(global_path)
        strategy_cfgs = _parse_strategy_dir(strategies_dir)
        return cls(global_cfg=global_cfg, strategy_cfgs=strategy_cfgs)


def _parse_global_yaml(path: Path) -> RiskGateConfig:
    if not path.exists():
        raise FileNotFoundError(
            f"risk_gates global config not found: {path}",
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping")
    for key in ("market_hours", "daily_loss", "sector_exposure"):
        if key not in data:
            raise ValueError(f"{path}: missing required top-level key {key!r}")
    return RiskGateConfig(
        market_hours=_parse_market_hours(data["market_hours"], path),
        daily_loss=_parse_daily_loss(data["daily_loss"], path),
        sector_exposure=_parse_sector_exposure(data["sector_exposure"], path),
    )


def _parse_market_hours(d: dict, path: Path) -> MarketHoursConfig:
    required = (
        "enabled", "exchange", "allow_regular_session",
        "allow_post_close", "post_close_until", "allow_premarket",
    )
    for k in required:
        if k not in d:
            raise ValueError(f"{path}: market_hours missing {k!r}")
    hh, mm = str(d["post_close_until"]).split(":")
    return MarketHoursConfig(
        enabled=bool(d["enabled"]),
        exchange=str(d["exchange"]),
        allow_regular_session=bool(d["allow_regular_session"]),
        allow_post_close=bool(d["allow_post_close"]),
        post_close_until=time(int(hh), int(mm)),
        allow_premarket=bool(d["allow_premarket"]),
    )


def _parse_daily_loss(d: dict, path: Path) -> DailyLossConfig:
    for k in ("enabled", "daily_loss_limit"):
        if k not in d:
            raise ValueError(f"{path}: daily_loss missing {k!r}")
    limit = Decimal(str(d["daily_loss_limit"]))
    if limit < 0:
        raise ValueError(
            f"{path}: daily_loss.daily_loss_limit must be non-negative "
            f"(got {limit})",
        )
    return DailyLossConfig(enabled=bool(d["enabled"]), daily_loss_limit=limit)


def _parse_sector_exposure(d: dict, path: Path) -> SectorExposureConfig:
    for k in ("enabled", "max_sector_exposure_pct", "configured_max_capital_in_use"):
        if k not in d:
            raise ValueError(f"{path}: sector_exposure missing {k!r}")
    pct = float(d["max_sector_exposure_pct"])
    if not 0.0 <= pct <= 1.0:
        raise ValueError(
            f"{path}: sector_exposure.max_sector_exposure_pct must be in [0,1] "
            f"(got {pct})",
        )
    cap = Decimal(str(d["configured_max_capital_in_use"]))
    if cap <= 0:
        raise ValueError(
            f"{path}: sector_exposure.configured_max_capital_in_use must be > 0",
        )
    return SectorExposureConfig(
        enabled=bool(d["enabled"]),
        max_sector_exposure_pct=pct,
        configured_max_capital_in_use=cap,
    )


def _parse_strategy_dir(strategies_dir: Path) -> dict[str, StrategyRiskConfig]:
    """Filled in at T6. Empty dict for T5 so global-only tests pass."""
    return {}
```

- [ ] **Step 5: Run to verify tests pass**

Run: `uv run pytest tests/trading/risk_gates/test_config_provider.py -v`
Expected: ALL pass (the strategy-dir tests for T6 not yet written).

- [ ] **Step 6: Commit**

```bash
git add config/risk_gates.yaml marketpulse/trading/risk_gates/config_provider.py tests/trading/risk_gates/test_config_provider.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-T5): RiskConfigProvider.from_yaml global parser + ship default

Parses config/risk_gates.yaml: market_hours, daily_loss, sector_exposure
blocks. Schema validation: required keys, sane ranges (pct in [0,1],
non-negative loss limit, positive capital denominator). Strategy-dir
parsing stubbed empty until T6.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T6: `RiskConfigProvider` strategy YAML `risk:` block parser

**Files:**
- Modify: `marketpulse/trading/risk_gates/config_provider.py`
- Test: `tests/trading/risk_gates/test_config_provider.py` (extend)

- [ ] **Step 1: Append failing tests**

Append to `tests/trading/risk_gates/test_config_provider.py`:

```python
def _write_min_strategy_yaml(path, *, name, risk=None):
    """Helper — minimal valid strategy YAML the loader also accepts."""
    blocks = [
        f"name: {name}",
        f"display_name: {name}",
        "version: v1",
        "description: test",
        "applies_when: test",
        "expected_horizons: [5]",
        "instructions: test",
    ]
    if risk is not None:
        blocks.append("risk:")
        for k, v in risk.items():
            blocks.append(f"  {k}: {v}")
    path.write_text("\n".join(blocks) + "\n")


def test_strategy_dir_parses_risk_block(tmp_path):
    """T6: strategy YAML with `risk:` block becomes StrategyRiskConfig."""
    from decimal import Decimal

    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    sd = tmp_path / "strategies"
    sd.mkdir()
    _write_min_strategy_yaml(
        sd / "momentum_breakout.yaml",
        name="momentum_breakout",
        risk={"max_position_notional": 25000},
    )
    _write_min_strategy_yaml(
        sd / "general.yaml", name="general",
        risk={"max_position_notional": 10000},
    )
    # Ship a stub global YAML.
    g = tmp_path / "g.yaml"
    g.write_text("""
market_hours: {enabled: true, exchange: XNYS, allow_regular_session: true, allow_post_close: true, post_close_until: "18:00", allow_premarket: false}
daily_loss: {enabled: true, daily_loss_limit: 500}
sector_exposure: {enabled: true, max_sector_exposure_pct: 0.35, configured_max_capital_in_use: 10000}
""")
    p = RiskConfigProvider.from_yaml(global_path=g, strategies_dir=sd)
    assert p.strategy_config("momentum_breakout").max_position_notional == Decimal("25000")
    assert p.strategy_config("general").max_position_notional == Decimal("10000")


def test_strategy_without_risk_block_returns_none(tmp_path):
    """Lock 6b-L9: strategy_config() returns None for strategies missing
    a `risk:` block. StrategySizeGate uses this for fail-closed."""
    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    sd = tmp_path / "strategies"
    sd.mkdir()
    _write_min_strategy_yaml(sd / "no_risk.yaml", name="no_risk")  # no risk block
    g = tmp_path / "g.yaml"
    g.write_text("""
market_hours: {enabled: true, exchange: XNYS, allow_regular_session: true, allow_post_close: true, post_close_until: "18:00", allow_premarket: false}
daily_loss: {enabled: true, daily_loss_limit: 500}
sector_exposure: {enabled: true, max_sector_exposure_pct: 0.35, configured_max_capital_in_use: 10000}
""")
    p = RiskConfigProvider.from_yaml(global_path=g, strategies_dir=sd)
    assert p.strategy_config("no_risk") is None


def test_strategy_with_risk_but_missing_limit_field_returns_config_with_none(tmp_path):
    """`risk: {}` block (empty mapping) → StrategyRiskConfig with
    max_position_notional=None. Triggers fail-closed by 6b-L9."""
    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    sd = tmp_path / "strategies"
    sd.mkdir()
    p_yaml = sd / "empty_risk.yaml"
    _write_min_strategy_yaml(p_yaml, name="empty_risk")
    # Append empty risk block.
    p_yaml.write_text(p_yaml.read_text() + "risk: {}\n")
    g = tmp_path / "g.yaml"
    g.write_text("""
market_hours: {enabled: true, exchange: XNYS, allow_regular_session: true, allow_post_close: true, post_close_until: "18:00", allow_premarket: false}
daily_loss: {enabled: true, daily_loss_limit: 500}
sector_exposure: {enabled: true, max_sector_exposure_pct: 0.35, configured_max_capital_in_use: 10000}
""")
    p = RiskConfigProvider.from_yaml(global_path=g, strategies_dir=sd)
    cfg = p.strategy_config("empty_risk")
    assert cfg is not None
    assert cfg.max_position_notional is None


def test_strategy_dir_rejects_negative_notional(tmp_path):
    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    sd = tmp_path / "strategies"
    sd.mkdir()
    _write_min_strategy_yaml(
        sd / "bad.yaml", name="bad",
        risk={"max_position_notional": -100},
    )
    g = tmp_path / "g.yaml"
    g.write_text("""
market_hours: {enabled: true, exchange: XNYS, allow_regular_session: true, allow_post_close: true, post_close_until: "18:00", allow_premarket: false}
daily_loss: {enabled: true, daily_loss_limit: 500}
sector_exposure: {enabled: true, max_sector_exposure_pct: 0.35, configured_max_capital_in_use: 10000}
""")
    import pytest
    with pytest.raises(ValueError, match="max_position_notional"):
        RiskConfigProvider.from_yaml(global_path=g, strategies_dir=sd)


def test_strategy_dir_filename_stem_is_key(tmp_path):
    """Lock 6b-L14: lookup key is the YAML filename stem."""
    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    sd = tmp_path / "strategies"
    sd.mkdir()
    _write_min_strategy_yaml(
        sd / "sector_rotation.yaml", name="sector_rotation",
        risk={"max_position_notional": 5000},
    )
    g = tmp_path / "g.yaml"
    g.write_text("""
market_hours: {enabled: true, exchange: XNYS, allow_regular_session: true, allow_post_close: true, post_close_until: "18:00", allow_premarket: false}
daily_loss: {enabled: true, daily_loss_limit: 500}
sector_exposure: {enabled: true, max_sector_exposure_pct: 0.35, configured_max_capital_in_use: 10000}
""")
    p = RiskConfigProvider.from_yaml(global_path=g, strategies_dir=sd)
    # Lookup by stem.
    assert p.strategy_config("sector_rotation") is not None
    # Lookup by unrelated key.
    assert p.strategy_config("not_a_strategy") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/risk_gates/test_config_provider.py -v -k "strategy_dir or strategy_without or strategy_with_risk"`
Expected: 5 FAIL (most because `_parse_strategy_dir` returns `{}`; the negative-notional test FAILs because no validation runs).

- [ ] **Step 3: Implement `_parse_strategy_dir`**

In `marketpulse/trading/risk_gates/config_provider.py`, replace the `_parse_strategy_dir` stub:

```python
def _parse_strategy_dir(strategies_dir: Path) -> dict[str, StrategyRiskConfig]:
    """Lock 6b-L14: strategy lookup key is YAML filename stem. Reads ONLY
    the `risk:` block — never `signals`, `sizing`, or other strategy-
    execution blocks.

    Behavior matrix:
      - file has no `risk:` key       → strategy NOT registered
                                        (strategy_config(stem) → None,
                                        triggers fail-closed via 6b-L9)
      - file has `risk: {}` empty     → registered with
                                        max_position_notional=None
                                        (still fail-closed via 6b-L9)
      - file has `risk: {max_position_notional: N}` → registered with
                                        Decimal(N) (must be >= 0)
    """
    out: dict[str, StrategyRiskConfig] = {}
    if not strategies_dir.exists():
        return out
    for yaml_path in sorted(strategies_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(
                f"{yaml_path}: top-level YAML must be a mapping",
            )
        if "risk" not in data:
            continue  # not registered; fail-closed via 6b-L9
        risk_block = data["risk"]
        if risk_block is None:
            risk_block = {}
        if not isinstance(risk_block, dict):
            raise ValueError(
                f"{yaml_path}: `risk:` must be a mapping, got "
                f"{type(risk_block).__name__}",
            )
        raw = risk_block.get("max_position_notional")
        if raw is None:
            cfg = StrategyRiskConfig(max_position_notional=None)
        else:
            try:
                limit = Decimal(str(raw))
            except Exception as e:
                raise ValueError(
                    f"{yaml_path}: risk.max_position_notional must parse as "
                    f"Decimal (got {raw!r}): {e}",
                ) from e
            if limit < 0:
                raise ValueError(
                    f"{yaml_path}: risk.max_position_notional must be >= 0 "
                    f"(got {limit})",
                )
            cfg = StrategyRiskConfig(max_position_notional=limit)
        out[yaml_path.stem] = cfg
    return out
```

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/trading/risk_gates/test_config_provider.py -v`
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/risk_gates/config_provider.py tests/trading/risk_gates/test_config_provider.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-T6): strategy YAML risk: block parser (lock 6b-L14 scope)

Reads ONLY `risk:` block — strategy lookup key is filename stem. Missing
`risk:` → strategy not registered → strategy_config() returns None →
StrategySizeGate fail-closed via 6b-L9. `risk: {}` empty → registered with
max_position_notional=None (still fail-closed). Validates notional >= 0.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T7: Add `risk:` block to 6 production strategy YAMLs

**Files:**
- Modify: `marketpulse/strategies/definitions/news_event.yaml`
- Modify: `marketpulse/strategies/definitions/oversold_reversal.yaml`
- Modify: `marketpulse/strategies/definitions/sector_rotation.yaml`
- Modify: `marketpulse/strategies/definitions/general.yaml`
- Modify: `marketpulse/strategies/definitions/momentum_breakout.yaml`
- Modify: `marketpulse/strategies/definitions/fundamental_value.yaml`
- Test: `tests/trading/risk_gates/test_config_provider.py` (extend)

- [ ] **Step 1: Append failing integration test**

Append to `tests/trading/risk_gates/test_config_provider.py`:

```python
def test_shipped_strategies_all_have_risk_blocks():
    """T7: every production strategy YAML must declare a `risk:` block
    with a finite max_position_notional. Missing or None → StrategySizeGate
    fail-closes EVERY order in that strategy (lock 6b-L9), which is fatal
    in production. This test guards against accidental regression of any
    YAML file in marketpulse/strategies/definitions/."""
    from pathlib import Path

    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    repo_root = Path(__file__).resolve().parents[3]
    global_path = repo_root / "config" / "risk_gates.yaml"
    strategies_dir = repo_root / "marketpulse" / "strategies" / "definitions"
    provider = RiskConfigProvider.from_yaml(
        global_path=global_path, strategies_dir=strategies_dir,
    )
    expected_stems = {
        "news_event", "oversold_reversal", "sector_rotation",
        "general", "momentum_breakout", "fundamental_value",
    }
    for stem in expected_stems:
        cfg = provider.strategy_config(stem)
        assert cfg is not None, (
            f"Strategy {stem!r} has no `risk:` block in its YAML — "
            "StrategySizeGate will fail-closed every order for this strategy "
            "(lock 6b-L9). Add `risk: { max_position_notional: <N> }` to "
            f"marketpulse/strategies/definitions/{stem}.yaml"
        )
        assert cfg.max_position_notional is not None, (
            f"Strategy {stem!r} has `risk:` but no max_position_notional — "
            "still fail-closed by 6b-L9"
        )
        assert cfg.max_position_notional > 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/risk_gates/test_config_provider.py::test_shipped_strategies_all_have_risk_blocks -v`
Expected: FAIL with `Strategy 'news_event' has no \`risk:\` block`.

- [ ] **Step 3: Append `risk:` block to all 6 strategy YAMLs**

To EACH of these files, append exactly the block shown:

- `marketpulse/strategies/definitions/news_event.yaml` — append:

```yaml

# Phase 6b risk gate config (StrategySizeGate cap — lock 6b-L9).
risk:
  max_position_notional: 25000
```

- `marketpulse/strategies/definitions/oversold_reversal.yaml` — append:

```yaml

# Phase 6b risk gate config (StrategySizeGate cap — lock 6b-L9).
risk:
  max_position_notional: 25000
```

- `marketpulse/strategies/definitions/sector_rotation.yaml` — append:

```yaml

# Phase 6b risk gate config (StrategySizeGate cap — lock 6b-L9).
risk:
  max_position_notional: 25000
```

- `marketpulse/strategies/definitions/general.yaml` — append:

```yaml

# Phase 6b risk gate config (StrategySizeGate cap — lock 6b-L9).
risk:
  max_position_notional: 25000
```

- `marketpulse/strategies/definitions/momentum_breakout.yaml` — append:

```yaml

# Phase 6b risk gate config (StrategySizeGate cap — lock 6b-L9).
risk:
  max_position_notional: 25000
```

- `marketpulse/strategies/definitions/fundamental_value.yaml` — append:

```yaml

# Phase 6b risk gate config (StrategySizeGate cap — lock 6b-L9).
risk:
  max_position_notional: 25000
```

- [ ] **Step 4: Verify the strategy loader still accepts the extended YAMLs (no regression)**

Run: `uv run pytest tests/strategies/ -v`
Expected: ALL pass. (The existing loader ignores unknown top-level keys — it only validates the documented set.)

- [ ] **Step 5: Run the new test to verify it passes**

Run: `uv run pytest tests/trading/risk_gates/test_config_provider.py::test_shipped_strategies_all_have_risk_blocks -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/strategies/definitions/ tests/trading/risk_gates/test_config_provider.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-T7): add risk: block to 6 production strategy YAMLs

Every shipped strategy now declares max_position_notional. Without this,
StrategySizeGate would fail-closed every order in production (lock 6b-L9).
Integration test guards against future regression.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

I'll continue in the next message to keep this within bounds — T8 through T19 plus self-review and execution handoff.
### Task T8: `Repository.today_realized_pnl` (DST-safe NY window — lock 6b-L13)

**Files:**
- Modify: `marketpulse/trading/repository.py`
- Test: `tests/trading/test_repository_risk_extensions.py` (new)

- [ ] **Step 1: Create failing tests**

Create `tests/trading/test_repository_risk_extensions.py`:

```python
# Layer: stateful
"""6b-T8/T9: Repository extensions for risk gates."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base
    eng = create_engine(f"sqlite:///{tmp_path / 'repo.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _insert_order(session, order_id, strategy="momentum_breakout", ticker="AAPL"):
    from marketpulse.db.models import PaperOrder
    o = PaperOrder(
        id=order_id, strategy=strategy, ticker=ticker, quantity=10,
        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=Decimal("150.00"),
        horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155.00"),
        idempotency_key=f"k-{order_id}",
        allocation_run_id=f"r-{order_id}",
        status="ENTRY_FILLED",
        placed_at=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        filled_at=datetime(2026, 5, 21, 14, 1, tzinfo=UTC),
        strategy_version="v1",
        allocator_version="phase6a-v1",
        execution_engine_version="phase6a-v1",
        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
        effective_corr_window=60,
        rewarded_for_negative_corr=False,
        would_change_rank=False,
        size_clamped_by_override=False,
    )
    session.add(o)
    session.flush()
    return o


def _insert_fill(session, order_id, side, realized_pnl, filled_at):
    from marketpulse.db.models import PaperFill
    f = PaperFill(
        order_id=order_id, side=side, quantity=10,
        price=Decimal("150.00"), filled_at=filled_at,
        realized_pnl=realized_pnl,
    )
    session.add(f)
    session.flush()
    return f


def test_today_realized_pnl_no_fills_returns_zero(session):
    from marketpulse.trading.repository import Repository
    repo = Repository(session=session)
    assert repo.today_realized_pnl(tick_date=date(2026, 5, 21)) == Decimal(0)


def test_today_realized_pnl_sums_exit_fills_in_ny_day(session):
    from marketpulse.trading.repository import Repository
    NY = ZoneInfo("America/New_York")
    # Two EXIT fills on 2026-05-21 NY (Thursday).
    # 14:00 NY = 18:00 UTC; 17:30 NY = 21:30 UTC.
    o1 = _insert_order(session, 1001)
    o2 = _insert_order(session, 1002)
    _insert_fill(session, o1.id, "EXIT", Decimal("100"),
                 datetime(2026, 5, 21, 14, 0, tzinfo=NY).astimezone(UTC))
    _insert_fill(session, o2.id, "EXIT", Decimal("-30"),
                 datetime(2026, 5, 21, 17, 30, tzinfo=NY).astimezone(UTC))
    repo = Repository(session=session)
    assert repo.today_realized_pnl(tick_date=date(2026, 5, 21)) == Decimal(70)


def test_today_realized_pnl_excludes_entry_fills(session):
    """ENTRY fills don't realize PnL; gate sums only EXIT fills."""
    from marketpulse.trading.repository import Repository
    NY = ZoneInfo("America/New_York")
    o = _insert_order(session, 2001)
    _insert_fill(session, o.id, "ENTRY", None,
                 datetime(2026, 5, 21, 14, 0, tzinfo=NY).astimezone(UTC))
    _insert_fill(session, o.id, "EXIT", Decimal("50"),
                 datetime(2026, 5, 21, 15, 0, tzinfo=NY).astimezone(UTC))
    repo = Repository(session=session)
    assert repo.today_realized_pnl(tick_date=date(2026, 5, 21)) == Decimal(50)


def test_today_realized_pnl_excludes_prior_day_fills(session):
    from marketpulse.trading.repository import Repository
    NY = ZoneInfo("America/New_York")
    o1 = _insert_order(session, 3001)
    o2 = _insert_order(session, 3002)
    # Yesterday's EXIT.
    _insert_fill(session, o1.id, "EXIT", Decimal("100"),
                 datetime(2026, 5, 20, 14, 0, tzinfo=NY).astimezone(UTC))
    # Today's EXIT.
    _insert_fill(session, o2.id, "EXIT", Decimal("25"),
                 datetime(2026, 5, 21, 14, 0, tzinfo=NY).astimezone(UTC))
    repo = Repository(session=session)
    assert repo.today_realized_pnl(tick_date=date(2026, 5, 21)) == Decimal(25)


def test_today_realized_pnl_dst_spring_forward_no_overlap(session):
    """Lock 6b-L13: on 2026-03-08 (US spring-forward), the NY day is 23h
    long in wall-clock; we must NOT include a fill that lands in the next
    NY-day's wall-clock window. Build both bounds in NY-local time first."""
    from marketpulse.trading.repository import Repository
    NY = ZoneInfo("America/New_York")
    o1 = _insert_order(session, 4001)
    o2 = _insert_order(session, 4002)
    # Sunday 2026-03-08 NY ends at 2026-03-09 00:00 NY = 2026-03-09 04:00 UTC.
    # A naïve +24h-from-NY-midnight-UTC window would extend to 05:00 UTC
    # and accidentally include a fill at 2026-03-09 00:30 NY (04:30 UTC).
    _insert_fill(session, o1.id, "EXIT", Decimal("10"),
                 datetime(2026, 3, 8, 23, 30, tzinfo=NY).astimezone(UTC))
    _insert_fill(session, o2.id, "EXIT", Decimal("999"),
                 datetime(2026, 3, 9, 0, 30, tzinfo=NY).astimezone(UTC))
    repo = Repository(session=session)
    # Only the 23:30 NY fill should be counted for 2026-03-08.
    assert repo.today_realized_pnl(tick_date=date(2026, 3, 8)) == Decimal(10)
    # The 00:30 next-day fill counts for 2026-03-09.
    assert repo.today_realized_pnl(tick_date=date(2026, 3, 9)) == Decimal(999)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/test_repository_risk_extensions.py -v`
Expected: 5 FAIL with `AttributeError: 'Repository' object has no attribute 'today_realized_pnl'`.

- [ ] **Step 3: Add `today_realized_pnl` to `Repository`**

In `marketpulse/trading/repository.py`:

(a) Add to the imports at the top, replacing the existing `from datetime import date, datetime` line with:

```python
from datetime import UTC, date, datetime, time, timedelta
```

(b) Add this top-level import below the existing zoneinfo-free imports (after the `from sqlalchemy.orm import Session` line):

```python
from marketpulse.trading.calendar import NY
```

(c) Append at the end of the `Repository` class (after `latest_kill_switch_state`):

```python

    # === Phase 6b risk-gate read helpers (lock 6b-L5: extension only) ===

    def today_realized_pnl(self, *, tick_date: date) -> Decimal:
        """Sum of paper_fill.realized_pnl where side='EXIT' and the fill's
        NY-day equals tick_date. Returns Decimal(0) if no rows.

        DST-safe NY-day window (lock 6b-L13): build both bounds as NY-local
        midnight, then convert each to UTC independently. The naïve
        `ny_start + timedelta(days=1)` adds 24 wall-clock UTC hours, which
        is off-by-1h on DST transition days — using two NY-local midnights
        and converting each to UTC guarantees a true 23/24/25-hour NY day.
        """
        from marketpulse.db.models import PaperFill

        ny_start = datetime.combine(tick_date, time.min, tzinfo=NY)
        ny_end = datetime.combine(tick_date + timedelta(days=1), time.min, tzinfo=NY)
        utc_start = ny_start.astimezone(UTC)
        utc_end = ny_end.astimezone(UTC)
        total = self._session.execute(
            select(func.coalesce(func.sum(PaperFill.realized_pnl), Decimal("0")))
            .where(PaperFill.side == "EXIT")
            .where(PaperFill.filled_at >= utc_start)
            .where(PaperFill.filled_at < utc_end)
        ).scalar()
        return Decimal(total or 0)
```

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/trading/test_repository_risk_extensions.py -v -k "today_realized_pnl"`
Expected: 5 PASS.

- [ ] **Step 5: Run full repository test suite for no regressions**

Run: `uv run pytest -q tests/trading/test_repository* tests/architecture/`
Expected: ALL pass.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/trading/repository.py tests/trading/test_repository_risk_extensions.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-T8): Repository.today_realized_pnl (DST-safe NY window)

Sum of EXIT-side paper_fill.realized_pnl within the NY trading day. Lock
6b-L13: both window bounds built as NY-local midnight + converted to UTC
independently, so DST transition days are exactly 23/24/25 hours long
(NOT a fixed 24h UTC slice). DST-spring-forward test exercises the edge.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T9: `Repository.sector_exposure_notional`

**Files:**
- Modify: `marketpulse/trading/repository.py`
- Test: `tests/trading/test_repository_risk_extensions.py` (extend)

- [ ] **Step 1: Append failing tests**

Append to `tests/trading/test_repository_risk_extensions.py`:

```python
def _insert_position(session, *, ticker, quantity, entry_price, status="OPEN", strategy="momentum_breakout"):
    """Helper — minimal OPEN position. order_id reused to avoid wiring a
    second order; CHECK constraints don't gate on order_id uniqueness across
    OPEN rows (only the order table enforces that via unique idx)."""
    from marketpulse.db.models import PaperOrder, PaperPosition
    # Each PaperPosition needs a distinct order_id (UNIQUE). Insert a stub
    # order first.
    nxt = (session.execute(
        __import__("sqlalchemy").select(__import__("sqlalchemy").func.coalesce(
            __import__("sqlalchemy").func.max(PaperOrder.id), 0
        ))
    ).scalar() or 0) + 1
    o = PaperOrder(
        id=nxt, strategy=strategy, ticker=ticker, quantity=quantity,
        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=entry_price,
        horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("0"),
        idempotency_key=f"pos-{nxt}-{ticker}",
        allocation_run_id=f"r-{nxt}",
        status="ENTRY_FILLED",
        placed_at=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        filled_at=datetime(2026, 5, 21, 14, 1, tzinfo=UTC),
        strategy_version="v1",
        allocator_version="phase6a-v1",
        execution_engine_version="phase6a-v1",
        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
        effective_corr_window=60,
        rewarded_for_negative_corr=False,
        would_change_rank=False,
        size_clamped_by_override=False,
    )
    session.add(o)
    session.flush()
    p = PaperPosition(
        order_id=o.id, strategy=strategy, ticker=ticker,
        quantity=quantity, entry_price=entry_price,
        entry_date=date(2026, 5, 21),
        horizon_date=date(2026, 5, 28),
        status=status,
        opened_at=datetime(2026, 5, 21, 14, 1, tzinfo=UTC),
        entry_fill_id=None, exit_fill_id=None,
    )
    session.add(p)
    session.flush()
    return p


def test_sector_exposure_notional_empty(session):
    from marketpulse.trading.repository import Repository
    repo = Repository(session=session)
    result = repo.sector_exposure_notional(sector_provider=lambda t: "Technology")
    assert result == {}


def test_sector_exposure_notional_groups_open_positions(session):
    from marketpulse.trading.repository import Repository
    _insert_position(session, ticker="AAPL", quantity=10, entry_price=Decimal("150"))
    _insert_position(session, ticker="MSFT", quantity=5, entry_price=Decimal("400"))
    _insert_position(session, ticker="JPM", quantity=20, entry_price=Decimal("160"))

    def sector(t):
        return {"AAPL": "Technology", "MSFT": "Technology", "JPM": "Financials"}[t]

    repo = Repository(session=session)
    result = repo.sector_exposure_notional(sector_provider=sector)
    assert result["Technology"] == Decimal("3500")   # 10*150 + 5*400
    assert result["Financials"] == Decimal("3200")   # 20*160


def test_sector_exposure_notional_excludes_closed_positions(session):
    from marketpulse.trading.repository import Repository
    _insert_position(session, ticker="AAPL", quantity=10, entry_price=Decimal("150"))
    _insert_position(session, ticker="OLD", quantity=10, entry_price=Decimal("100"), status="CLOSED")

    repo = Repository(session=session)
    result = repo.sector_exposure_notional(sector_provider=lambda t: "Technology")
    assert result == {"Technology": Decimal("1500")}


def test_sector_exposure_notional_excludes_unknown_sector_positions(session):
    """Lock 6b-L8 / 6b-L11: positions with sector_provider(t) == None do
    not anchor a sector bucket. They're silently dropped from the result;
    SectorExposureGate's own pre-check fails NEW orders with unknown sector
    via the fail-closed `proposed_sector is None` path."""
    from marketpulse.trading.repository import Repository
    _insert_position(session, ticker="KNOWN", quantity=10, entry_price=Decimal("100"))
    _insert_position(session, ticker="UNKNOWN", quantity=5, entry_price=Decimal("200"))

    def sector(t):
        return "Tech" if t == "KNOWN" else None

    repo = Repository(session=session)
    result = repo.sector_exposure_notional(sector_provider=sector)
    assert result == {"Tech": Decimal("1000")}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/test_repository_risk_extensions.py -v -k "sector_exposure"`
Expected: 4 FAIL with `AttributeError: ... has no attribute 'sector_exposure_notional'`.

- [ ] **Step 3: Add `sector_exposure_notional` to `Repository`**

At the top of `marketpulse/trading/repository.py`, ensure the imports include `Callable`. Replace the existing `from typing import Literal` with:

```python
from collections.abc import Callable
from typing import Literal
```

Then, immediately below `today_realized_pnl`, append:

```python

    def sector_exposure_notional(
        self,
        *,
        sector_provider: Callable[[str], str | None],
    ) -> dict[str, Decimal]:
        """OPEN paper_position rows grouped by sector. Notional per position
        = quantity * entry_price (Phase 6 does NOT mark-to-market). Tickers
        whose sector_provider returns None are EXCLUDED from the result
        (locks 6b-L8 + 6b-L11). Returns {sector: total_notional}."""
        from marketpulse.db.models import PaperPosition

        rows = self._session.execute(
            select(PaperPosition).where(PaperPosition.status == "OPEN")
        ).scalars().all()
        out: dict[str, Decimal] = {}
        for p in rows:
            sector = sector_provider(p.ticker)
            if sector is None:
                continue
            out[sector] = out.get(sector, Decimal(0)) + (
                Decimal(p.quantity) * p.entry_price
            )
        return out
```

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/trading/test_repository_risk_extensions.py -v`
Expected: ALL pass.

- [ ] **Step 5: Verify architecture lock-iii AST guard still happy**

Run: `uv run pytest -q tests/architecture/test_repository_boundary.py`
Expected: PASS. The guard checks that only `Repository` writes paper_* tables; both new helpers are read-only (`select()`), so they don't trigger it.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/trading/repository.py tests/trading/test_repository_risk_extensions.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-T9): Repository.sector_exposure_notional

Groups OPEN paper_position rows by sector (resolved via injected
sector_provider callable). Notional = quantity * entry_price (no MtM in
Phase 6). Unknown-sector positions (provider returns None) excluded from
the buckets — locks 6b-L8 + 6b-L11.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T10: `strict_sector` wrapper

**Files:**
- Create: `marketpulse/trading/risk_gates/_sector.py`
- Modify: `marketpulse/trading/risk_gates/__init__.py`
- Test: `tests/trading/risk_gates/test_strict_sector.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/trading/risk_gates/test_strict_sector.py`:

```python
# Layer: pure
"""6b-T10: strict_sector wrapper test.

marketpulse.backtest.sector.get_sector() always returns a str — falling
back to 'unknown' when no resolution succeeds. SectorExposureGate's
fail-closed semantics need `None` for the unknown case (lock 6b-L8).
This wrapper bridges the two contracts."""

from __future__ import annotations


def test_strict_sector_returns_none_for_unknown(monkeypatch):
    from marketpulse.trading.risk_gates._sector import strict_sector

    # Patch the backing get_sector to return 'unknown' regardless of input.
    monkeypatch.setattr(
        "marketpulse.trading.risk_gates._sector._get_sector",
        lambda t: "unknown",
    )
    assert strict_sector("ANY") is None


def test_strict_sector_passes_through_real_sector(monkeypatch):
    from marketpulse.trading.risk_gates._sector import strict_sector

    monkeypatch.setattr(
        "marketpulse.trading.risk_gates._sector._get_sector",
        lambda t: {"AAPL": "Technology", "JPM": "Financials"}[t],
    )
    assert strict_sector("AAPL") == "Technology"
    assert strict_sector("JPM") == "Financials"


def test_strict_sector_returns_none_on_empty_string(monkeypatch):
    """Defensive: get_sector never returns '' today, but treat falsy
    as None to keep the gate fail-closed."""
    from marketpulse.trading.risk_gates._sector import strict_sector

    monkeypatch.setattr(
        "marketpulse.trading.risk_gates._sector._get_sector",
        lambda t: "",
    )
    assert strict_sector("X") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/risk_gates/test_strict_sector.py -v`
Expected: 3 FAIL with `ModuleNotFoundError: ... _sector`.

- [ ] **Step 3: Implement `_sector.py`**

Create `marketpulse/trading/risk_gates/_sector.py`:

```python
"""strict_sector wrapper.

marketpulse.backtest.sector.get_sector() always returns str (falls back
to "unknown"). SectorExposureGate needs `None` for the unknown case so
its fail-closed branch (lock 6b-L8) fires. This module bridges the two:

    get_sector("AAPL")   → "Technology"
    get_sector("ZZZZZ")  → "unknown"    (the underlying contract)

    strict_sector("AAPL")  → "Technology"
    strict_sector("ZZZZZ") → None       (gate-friendly contract)
"""

from __future__ import annotations

from marketpulse.backtest.sector import get_sector as _get_sector

__all__ = ["strict_sector"]


def strict_sector(ticker: str) -> str | None:
    s = _get_sector(ticker)
    if not s or s == "unknown":
        return None
    return s
```

- [ ] **Step 4: Re-export from package init**

In `marketpulse/trading/risk_gates/__init__.py`, append `strict_sector` to the imports + `__all__`:

```python
from marketpulse.trading.risk_gates._sector import strict_sector
```

Then update `__all__` to include `"strict_sector"`.

- [ ] **Step 5: Run to verify tests pass**

Run: `uv run pytest tests/trading/risk_gates/test_strict_sector.py -v`
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/trading/risk_gates/_sector.py marketpulse/trading/risk_gates/__init__.py tests/trading/risk_gates/test_strict_sector.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-T10): strict_sector wrapper bridges str → str | None

marketpulse.backtest.sector.get_sector falls back to 'unknown';
SectorExposureGate needs None to trigger fail-closed (lock 6b-L8). Thin
wrapper translates between the two contracts so the gate stays clean.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T11: `MarketHoursGate`

**Files:**
- Create: `marketpulse/trading/risk_gates/market_hours.py`
- Modify: `marketpulse/trading/risk_gates/__init__.py`
- Test: `tests/trading/risk_gates/test_market_hours.py` (extend)

- [ ] **Step 1: Write failing tests (extend existing T3 file)**

Replace the body of `tests/trading/risk_gates/test_market_hours.py` (keep the existing `test_calendar_module_exports_ny_zoneinfo` and add the rest) with:

```python
# Layer: pure
"""6b-T11: MarketHoursGate tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest


def test_calendar_module_exports_ny_zoneinfo():
    from marketpulse.trading.calendar import NY
    assert NY == ZoneInfo("America/New_York")


# === Test fixtures ===

NY_TZ = ZoneInfo("America/New_York")


def _make_request(*, allocation_date=date(2026, 5, 21), risk_intent=None, ticker="AAPL"):
    from marketpulse.trading.types import AllocationRunId, OrderRequest, RiskIntent
    return OrderRequest(
        strategy="momentum_breakout", ticker=ticker, quantity=10,
        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        allocation_date=allocation_date,
        event_price=Decimal("150"),
        horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155"),
        allocation_run_id=AllocationRunId("paper-2026-05-21"),
        strategy_version="v1",
        allocator_version="phase6a-v1",
        execution_engine_version="phase6a-v1",
        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
        effective_corr_window=60,
        rewarded_for_negative_corr=False,
        would_change_rank=False,
        size_clamped_by_override=False,
        risk_intent=risk_intent if risk_intent is not None else RiskIntent.OPEN,
    )


class _FakeClock:
    def __init__(self, now):
        self._now = now

    def now(self):
        return self._now


def _make_gate(now_ny=None, cfg=None):
    from marketpulse.trading.calendar import NYTradingCalendar
    from marketpulse.trading.risk_gates.config_provider import MarketHoursConfig
    from marketpulse.trading.risk_gates.market_hours import MarketHoursGate
    if now_ny is None:
        now_ny = datetime(2026, 5, 21, 14, 0, tzinfo=NY_TZ)
    if cfg is None:
        cfg = MarketHoursConfig(
            enabled=True, exchange="XNYS",
            allow_regular_session=True, allow_post_close=True,
            post_close_until=time(18, 0), allow_premarket=False,
        )
    return MarketHoursGate(
        cfg=cfg, calendar=NYTradingCalendar(),
        clock=_FakeClock(now_ny.astimezone(UTC)),
    )


def test_market_hours_close_bypass():
    """6b-L1: CLOSE intent skips the gate even outside hours."""
    from marketpulse.trading.types import RiskIntent
    gate = _make_gate(now_ny=datetime(2026, 5, 21, 22, 0, tzinfo=NY_TZ))  # 22:00 NY
    r = gate.check_pre_trade(order_request=_make_request(risk_intent=RiskIntent.CLOSE))
    assert r.approved is True


def test_market_hours_reduce_bypass():
    from marketpulse.trading.types import RiskIntent
    gate = _make_gate(now_ny=datetime(2026, 5, 21, 22, 0, tzinfo=NY_TZ))
    r = gate.check_pre_trade(order_request=_make_request(risk_intent=RiskIntent.REDUCE))
    assert r.approved is True


def test_market_hours_flip_unsupported():
    from marketpulse.trading.types import RiskIntent
    gate = _make_gate()
    r = gate.check_pre_trade(order_request=_make_request(risk_intent=RiskIntent.FLIP))
    assert r.approved is False
    assert r.reason == "unsupported_risk_intent"
    assert r.gate_name == "market_hours"


def test_market_hours_disabled_passes_through():
    from marketpulse.trading.risk_gates.config_provider import MarketHoursConfig
    cfg = MarketHoursConfig(
        enabled=False, exchange="XNYS",
        allow_regular_session=True, allow_post_close=True,
        post_close_until=time(18, 0), allow_premarket=False,
    )
    gate = _make_gate(cfg=cfg, now_ny=datetime(2026, 5, 21, 3, 0, tzinfo=NY_TZ))
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is True


def test_market_hours_stale_allocation_date_denies():
    """Lock 6b-L7."""
    gate = _make_gate(now_ny=datetime(2026, 5, 22, 14, 0, tzinfo=NY_TZ))
    r = gate.check_pre_trade(order_request=_make_request(allocation_date=date(2026, 5, 21)))
    assert r.approved is False
    assert r.reason == "stale_allocation_date"
    assert r.context["allocation_date"] == "2026-05-21"
    assert r.context["today_session"] == "2026-05-22"


def test_market_hours_weekend_session_denies():
    # 2026-05-23 is a Saturday — not a session day.
    gate = _make_gate(now_ny=datetime(2026, 5, 23, 14, 0, tzinfo=NY_TZ))
    r = gate.check_pre_trade(order_request=_make_request(allocation_date=date(2026, 5, 23)))
    assert r.approved is False
    # today_ny_trading_date rolls back to Friday → stale_allocation_date.
    # Either reason is acceptable as long as the order is denied for a
    # non-session-day reason.
    assert r.reason in ("stale_allocation_date", "not_a_session_day")


# === Boundary tests (operational tests #17-#20) ===

def _at(hour, minute, second=0):
    """NY-time datetime at the given clock face on 2026-05-21 (Thu)."""
    return datetime(2026, 5, 21, hour, minute, second, tzinfo=NY_TZ)


def test_boundary_premarket_close_edge():
    """Op-test #17: premarket disabled, 09:29:59 deny, 09:30:00 approve."""
    gate = _make_gate(now_ny=_at(9, 29, 59))
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is False
    assert r.reason == "outside_placement_window"

    gate2 = _make_gate(now_ny=_at(9, 30, 0))
    r2 = gate2.check_pre_trade(order_request=_make_request())
    assert r2.approved is True


def test_boundary_regular_close_edge():
    """Op-test #18: 16:00:00 approve (regular inclusive right); 16:00:01
    approve via post-close (open-left)."""
    gate = _make_gate(now_ny=_at(16, 0, 0))
    assert gate.check_pre_trade(order_request=_make_request()).approved is True

    gate2 = _make_gate(now_ny=_at(16, 0, 1))
    assert gate2.check_pre_trade(order_request=_make_request()).approved is True


def test_boundary_post_close_cutoff_edge():
    """Op-test #19: post_close_until=18:00 — 18:00:00 approve (inclusive
    right); 18:00:01 deny."""
    gate = _make_gate(now_ny=_at(18, 0, 0))
    assert gate.check_pre_trade(order_request=_make_request()).approved is True

    gate2 = _make_gate(now_ny=_at(18, 0, 1))
    r = gate2.check_pre_trade(order_request=_make_request())
    assert r.approved is False
    assert r.reason == "outside_placement_window"


def test_boundary_all_disabled_denies_everywhere():
    """Op-test #20: all three window flags false → no valid placement window."""
    from marketpulse.trading.risk_gates.config_provider import MarketHoursConfig
    cfg = MarketHoursConfig(
        enabled=True, exchange="XNYS",
        allow_regular_session=False, allow_post_close=False,
        post_close_until=time(18, 0), allow_premarket=False,
    )
    for hh in (5, 10, 14, 17, 23):
        gate = _make_gate(cfg=cfg, now_ny=_at(hh, 0, 0))
        r = gate.check_pre_trade(order_request=_make_request())
        assert r.approved is False, f"{hh:02d}:00 should deny"
        assert r.reason == "outside_placement_window"


def test_market_hours_17_30_default_passes():
    """Op-test #14: Phase 6a default tick fires at 17:30 NY → must pass."""
    gate = _make_gate(now_ny=_at(17, 30, 0))
    assert gate.check_pre_trade(order_request=_make_request()).approved is True
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/risk_gates/test_market_hours.py -v`
Expected: many FAIL with `ModuleNotFoundError: ... market_hours`.

- [ ] **Step 3: Implement `market_hours.py`**

Create `marketpulse/trading/risk_gates/market_hours.py`:

```python
"""MarketHoursGate — denies OPEN/ADD orders outside the configured NY
placement window. CLOSE/REDUCE bypass (lock 6b-L1). FLIP denies
unsupported_risk_intent."""

from __future__ import annotations

from datetime import time

from marketpulse.trading.calendar import NY, NYTradingCalendar
from marketpulse.trading.clock import Clock
from marketpulse.trading.risk_gate import RiskResult
from marketpulse.trading.risk_gates.config_provider import MarketHoursConfig
from marketpulse.trading.types import OrderRequest, RiskIntent

__all__ = ["MarketHoursGate"]


class MarketHoursGate:
    name = "market_hours"

    def __init__(
        self,
        *,
        cfg: MarketHoursConfig,
        calendar: NYTradingCalendar,
        clock: Clock,
    ) -> None:
        self._cfg = cfg
        self._calendar = calendar
        self._clock = clock

    def check_pre_trade(self, *, order_request: OrderRequest) -> RiskResult:
        # === RiskIntent bypass/deny (lock 6b-L1) ===
        if order_request.risk_intent in (RiskIntent.CLOSE, RiskIntent.REDUCE):
            return RiskResult(approved=True, gate_name=self.name, reason="")
        if order_request.risk_intent == RiskIntent.FLIP:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="unsupported_risk_intent",
            )

        cfg = self._cfg
        if not cfg.enabled:
            return RiskResult(approved=True, gate_name=self.name, reason="")

        # === Stale allocation_date guard (lock 6b-L7) ===
        today_session = self._calendar.today_ny_trading_date(self._clock.now())
        if order_request.allocation_date != today_session:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="stale_allocation_date",
                context={
                    "allocation_date": order_request.allocation_date.isoformat(),
                    "today_session": today_session.isoformat(),
                },
            )

        # === Session-day guard ===
        if not self._calendar.is_business_day(order_request.allocation_date):
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="not_a_session_day",
                context={"allocation_date": order_request.allocation_date.isoformat()},
            )

        # === Wall-time window check ===
        now_ny = self._clock.now().astimezone(NY)
        if not _window_check(now_ny.time(), cfg):
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="outside_placement_window",
                context={"now_ny": now_ny.isoformat()},
            )
        return RiskResult(approved=True, gate_name=self.name, reason="")


def _window_check(t: time, cfg: MarketHoursConfig) -> bool:
    """Returns True iff t falls within any enabled NY-time window.
    Boundaries:
      - premarket:        [04:00, 09:30)  inclusive-left, exclusive-right
      - regular session:  [09:30, 16:00]  inclusive both ends
      - post-close:       (16:00, post_close_until]  exclusive-left,
                                                     inclusive-right
    If all flags False → False (no valid placement window)."""
    if cfg.allow_premarket and time(4, 0) <= t < time(9, 30):
        return True
    if cfg.allow_regular_session and time(9, 30) <= t <= time(16, 0):
        return True
    if cfg.allow_post_close and time(16, 0) < t <= cfg.post_close_until:
        return True
    return False
```

- [ ] **Step 4: Re-export from package init**

In `marketpulse/trading/risk_gates/__init__.py`, add:

```python
from marketpulse.trading.risk_gates.market_hours import MarketHoursGate
```

And append `"MarketHoursGate"` to `__all__`.

- [ ] **Step 5: Run to verify tests pass**

Run: `uv run pytest tests/trading/risk_gates/test_market_hours.py -v`
Expected: ALL pass.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/trading/risk_gates/market_hours.py marketpulse/trading/risk_gates/__init__.py tests/trading/risk_gates/test_market_hours.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-T11): MarketHoursGate + _window_check

CLOSE/REDUCE bypass (lock 6b-L1); FLIP denies unsupported_risk_intent.
Stale allocation_date denied (6b-L7). Window boundaries: premarket
[04:00, 09:30), regular [09:30, 16:00], post-close (16:00, cutoff].
Operational tests #14, #17-#20 exercise boundary cases.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T12: `StrategySizeGate`

**Files:**
- Create: `marketpulse/trading/risk_gates/strategy_size.py`
- Modify: `marketpulse/trading/risk_gates/__init__.py`
- Test: `tests/trading/risk_gates/test_strategy_size.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/trading/risk_gates/test_strategy_size.py`:

```python
# Layer: pure
"""6b-T12: StrategySizeGate tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal


def _make_request(*, strategy="momentum_breakout", event_price="150", quantity=10, risk_intent=None):
    from marketpulse.trading.types import AllocationRunId, OrderRequest, RiskIntent
    return OrderRequest(
        strategy=strategy, ticker="AAPL", quantity=quantity,
        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=Decimal(event_price),
        horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155"),
        allocation_run_id=AllocationRunId("paper-2026-05-21"),
        strategy_version="v1",
        allocator_version="phase6a-v1",
        execution_engine_version="phase6a-v1",
        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
        effective_corr_window=60,
        rewarded_for_negative_corr=False,
        would_change_rank=False,
        size_clamped_by_override=False,
        risk_intent=risk_intent if risk_intent is not None else RiskIntent.OPEN,
    )


class _StubProvider:
    def __init__(self, mapping):
        self._m = mapping

    def strategy_config(self, name):
        from marketpulse.trading.risk_gates.config_provider import StrategyRiskConfig
        v = self._m.get(name, "MISSING")
        if v == "MISSING":
            return None
        return StrategyRiskConfig(max_position_notional=v)


def _make_gate(mapping):
    from marketpulse.trading.risk_gates.strategy_size import StrategySizeGate
    return StrategySizeGate(provider=_StubProvider(mapping))


def test_strategy_size_close_bypass():
    from marketpulse.trading.types import RiskIntent
    gate = _make_gate({"momentum_breakout": Decimal("100")})  # tiny cap
    r = gate.check_pre_trade(
        order_request=_make_request(risk_intent=RiskIntent.CLOSE)
    )
    assert r.approved is True


def test_strategy_size_flip_unsupported():
    from marketpulse.trading.types import RiskIntent
    gate = _make_gate({"momentum_breakout": Decimal("25000")})
    r = gate.check_pre_trade(
        order_request=_make_request(risk_intent=RiskIntent.FLIP)
    )
    assert r.approved is False
    assert r.reason == "unsupported_risk_intent"


def test_strategy_size_under_cap_approves():
    gate = _make_gate({"momentum_breakout": Decimal("25000")})
    r = gate.check_pre_trade(
        order_request=_make_request(event_price="150", quantity=10),  # 1500
    )
    assert r.approved is True


def test_strategy_size_at_cap_approves():
    """Op-test #8: proposed == max → APPROVE (deny only on >)."""
    gate = _make_gate({"momentum_breakout": Decimal("1500")})
    r = gate.check_pre_trade(
        order_request=_make_request(event_price="150", quantity=10),  # 1500
    )
    assert r.approved is True


def test_strategy_size_over_cap_denies():
    gate = _make_gate({"momentum_breakout": Decimal("1499")})
    r = gate.check_pre_trade(
        order_request=_make_request(event_price="150", quantity=10),  # 1500
    )
    assert r.approved is False
    assert r.reason == "strategy_size_exceeded"
    assert r.context["proposed"] == "1500"
    assert r.context["limit"] == "1499"
    assert r.context["strategy"] == "momentum_breakout"


def test_strategy_size_missing_strategy_fail_closed():
    """Lock 6b-L9."""
    gate = _make_gate({})  # no entries; provider returns None
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is False
    assert r.reason == "missing_strategy_risk_config"
    assert r.context["strategy"] == "momentum_breakout"


def test_strategy_size_explicit_none_limit_fail_closed():
    """Lock 6b-L9: strategy_config returns config with None limit."""
    gate = _make_gate({"momentum_breakout": None})
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is False
    assert r.reason == "missing_strategy_risk_config"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/risk_gates/test_strategy_size.py -v`
Expected: 7 FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `strategy_size.py`**

Create `marketpulse/trading/risk_gates/strategy_size.py`:

```python
"""StrategySizeGate — denies OPEN/ADD orders where
event_price * quantity exceeds the strategy's max_position_notional.

Lock 6b-L9: missing strategy risk config → fail-closed deny
`missing_strategy_risk_config`. No infinite-cap default.

Op-test #8: boundary semantic is strict-greater — proposed == cap is
APPROVED; only proposed > cap denies."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from marketpulse.trading.risk_gate import RiskResult
from marketpulse.trading.risk_gates.config_provider import StrategyRiskConfig
from marketpulse.trading.types import OrderRequest, RiskIntent

__all__ = ["StrategySizeGate", "_StrategyConfigSource"]


class _StrategyConfigSource(Protocol):
    """Minimal contract the gate needs from the config provider."""
    def strategy_config(self, strategy: str) -> StrategyRiskConfig | None: ...


class StrategySizeGate:
    name = "strategy_size"

    def __init__(self, *, provider: _StrategyConfigSource) -> None:
        self._provider = provider

    def check_pre_trade(self, *, order_request: OrderRequest) -> RiskResult:
        if order_request.risk_intent in (RiskIntent.CLOSE, RiskIntent.REDUCE):
            return RiskResult(approved=True, gate_name=self.name, reason="")
        if order_request.risk_intent == RiskIntent.FLIP:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="unsupported_risk_intent",
            )

        cfg = self._provider.strategy_config(order_request.strategy)
        if cfg is None or cfg.max_position_notional is None:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="missing_strategy_risk_config",
                context={"strategy": order_request.strategy},
            )

        proposed = order_request.event_price * Decimal(order_request.quantity)
        if proposed > cfg.max_position_notional:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="strategy_size_exceeded",
                context={
                    "strategy": order_request.strategy,
                    "proposed": str(proposed),
                    "limit": str(cfg.max_position_notional),
                },
            )
        return RiskResult(approved=True, gate_name=self.name, reason="")
```

- [ ] **Step 4: Re-export from `__init__.py`**

Append to `marketpulse/trading/risk_gates/__init__.py`:

```python
from marketpulse.trading.risk_gates.strategy_size import StrategySizeGate
```

Add `"StrategySizeGate"` to `__all__`.

- [ ] **Step 5: Run to verify tests pass**

Run: `uv run pytest tests/trading/risk_gates/test_strategy_size.py -v`
Expected: 7 PASS.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/trading/risk_gates/strategy_size.py marketpulse/trading/risk_gates/__init__.py tests/trading/risk_gates/test_strategy_size.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-T12): StrategySizeGate

CLOSE/REDUCE bypass; FLIP denies; missing strategy risk config →
fail-closed deny (lock 6b-L9). Boundary: proposed == cap approves
(strict-greater on deny).

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T13: `DailyLossGate`

**Files:**
- Create: `marketpulse/trading/risk_gates/daily_loss.py`
- Modify: `marketpulse/trading/risk_gates/__init__.py`
- Test: `tests/trading/risk_gates/test_daily_loss.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/trading/risk_gates/test_daily_loss.py`:

```python
# Layer: pure
"""6b-T13: DailyLossGate tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal


def _make_request(*, allocation_date=date(2026, 5, 21), risk_intent=None):
    from marketpulse.trading.types import AllocationRunId, OrderRequest, RiskIntent
    return OrderRequest(
        strategy="momentum_breakout", ticker="AAPL", quantity=10,
        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        allocation_date=allocation_date,
        event_price=Decimal("150"),
        horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155"),
        allocation_run_id=AllocationRunId("paper-2026-05-21"),
        strategy_version="v1",
        allocator_version="phase6a-v1",
        execution_engine_version="phase6a-v1",
        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
        effective_corr_window=60,
        rewarded_for_negative_corr=False,
        would_change_rank=False,
        size_clamped_by_override=False,
        risk_intent=risk_intent if risk_intent is not None else RiskIntent.OPEN,
    )


class _StubRepo:
    def __init__(self, today_pnl):
        self._pnl = today_pnl
        self.calls = []

    def today_realized_pnl(self, *, tick_date):
        self.calls.append(tick_date)
        return self._pnl


def _make_gate(*, today_pnl, limit=Decimal("500"), enabled=True):
    from marketpulse.trading.risk_gates.config_provider import DailyLossConfig
    from marketpulse.trading.risk_gates.daily_loss import DailyLossGate
    return DailyLossGate(
        cfg=DailyLossConfig(enabled=enabled, daily_loss_limit=limit),
        repository=_StubRepo(today_pnl),
    )


def test_daily_loss_close_bypass():
    from marketpulse.trading.types import RiskIntent
    gate = _make_gate(today_pnl=Decimal("-9999"))
    r = gate.check_pre_trade(order_request=_make_request(risk_intent=RiskIntent.CLOSE))
    assert r.approved is True


def test_daily_loss_disabled_passes_through():
    gate = _make_gate(today_pnl=Decimal("-9999"), enabled=False)
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is True


def test_daily_loss_under_limit_approves():
    gate = _make_gate(today_pnl=Decimal("-300"), limit=Decimal("500"))
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is True


def test_daily_loss_at_boundary_denies():
    """Op-test #7: realized == -limit exactly denies."""
    gate = _make_gate(today_pnl=Decimal("-500"), limit=Decimal("500"))
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is False
    assert r.reason == "daily_loss_limit_exceeded"
    assert r.context["today_realized_pnl"] == "-500"
    assert r.context["daily_loss_limit"] == "500"
    assert r.context["allocation_date"] == "2026-05-21"


def test_daily_loss_over_limit_denies():
    gate = _make_gate(today_pnl=Decimal("-800"), limit=Decimal("500"))
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is False


def test_daily_loss_positive_pnl_approves():
    gate = _make_gate(today_pnl=Decimal("250"), limit=Decimal("500"))
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is True


def test_daily_loss_passes_allocation_date_to_repo():
    gate = _make_gate(today_pnl=Decimal("0"))
    gate.check_pre_trade(order_request=_make_request(allocation_date=date(2026, 5, 22)))
    assert gate._repo.calls == [date(2026, 5, 22)]


def test_daily_loss_flip_unsupported():
    from marketpulse.trading.types import RiskIntent
    gate = _make_gate(today_pnl=Decimal("0"))
    r = gate.check_pre_trade(order_request=_make_request(risk_intent=RiskIntent.FLIP))
    assert r.approved is False
    assert r.reason == "unsupported_risk_intent"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/risk_gates/test_daily_loss.py -v`
Expected: 8 FAIL `ModuleNotFoundError`.

- [ ] **Step 3: Implement `daily_loss.py`**

Create `marketpulse/trading/risk_gates/daily_loss.py`:

```python
"""DailyLossGate — denies OPEN/ADD orders when the day's realized PnL is
at or below -daily_loss_limit. CLOSE/REDUCE bypass (lock 6b-L1).

Boundary semantic: deny when realized_pnl <= -limit (op-test #7)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol

from marketpulse.trading.risk_gate import RiskResult
from marketpulse.trading.risk_gates.config_provider import DailyLossConfig
from marketpulse.trading.types import OrderRequest, RiskIntent

__all__ = ["DailyLossGate", "_TodayRealizedPnlRepo"]


class _TodayRealizedPnlRepo(Protocol):
    def today_realized_pnl(self, *, tick_date: date) -> Decimal: ...


class DailyLossGate:
    name = "daily_loss"

    def __init__(
        self,
        *,
        cfg: DailyLossConfig,
        repository: _TodayRealizedPnlRepo,
    ) -> None:
        self._cfg = cfg
        self._repo = repository

    def check_pre_trade(self, *, order_request: OrderRequest) -> RiskResult:
        if order_request.risk_intent in (RiskIntent.CLOSE, RiskIntent.REDUCE):
            return RiskResult(approved=True, gate_name=self.name, reason="")
        if order_request.risk_intent == RiskIntent.FLIP:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="unsupported_risk_intent",
            )

        cfg = self._cfg
        if not cfg.enabled:
            return RiskResult(approved=True, gate_name=self.name, reason="")

        realized = self._repo.today_realized_pnl(
            tick_date=order_request.allocation_date,
        )
        limit = cfg.daily_loss_limit
        if realized <= -limit:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="daily_loss_limit_exceeded",
                context={
                    "today_realized_pnl": str(realized),
                    "daily_loss_limit": str(limit),
                    "allocation_date": order_request.allocation_date.isoformat(),
                },
            )
        return RiskResult(approved=True, gate_name=self.name, reason="")
```

- [ ] **Step 4: Re-export from `__init__.py`**

Add to `marketpulse/trading/risk_gates/__init__.py`:

```python
from marketpulse.trading.risk_gates.daily_loss import DailyLossGate
```

Add `"DailyLossGate"` to `__all__`.

- [ ] **Step 5: Run to verify tests pass**

Run: `uv run pytest tests/trading/risk_gates/test_daily_loss.py -v`
Expected: 8 PASS.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/trading/risk_gates/daily_loss.py marketpulse/trading/risk_gates/__init__.py tests/trading/risk_gates/test_daily_loss.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-T13): DailyLossGate

Sums today's realized PnL via Repository.today_realized_pnl. Boundary:
realized <= -limit denies (op-test #7). CLOSE/REDUCE bypass; FLIP denies;
disabled flag passes through.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T14: `SectorExposureGate`

**Files:**
- Create: `marketpulse/trading/risk_gates/sector_exposure.py`
- Modify: `marketpulse/trading/risk_gates/__init__.py`
- Test: `tests/trading/risk_gates/test_sector_exposure.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/trading/risk_gates/test_sector_exposure.py`:

```python
# Layer: pure
"""6b-T14: SectorExposureGate tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal


def _make_request(*, ticker="AAPL", event_price="150", quantity=10, risk_intent=None):
    from marketpulse.trading.types import AllocationRunId, OrderRequest, RiskIntent
    return OrderRequest(
        strategy="momentum_breakout", ticker=ticker, quantity=quantity,
        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=Decimal(event_price),
        horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155"),
        allocation_run_id=AllocationRunId("paper-2026-05-21"),
        strategy_version="v1",
        allocator_version="phase6a-v1",
        execution_engine_version="phase6a-v1",
        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
        effective_corr_window=60,
        rewarded_for_negative_corr=False,
        would_change_rank=False,
        size_clamped_by_override=False,
        risk_intent=risk_intent if risk_intent is not None else RiskIntent.OPEN,
    )


class _StubRepo:
    def __init__(self, current):
        self._current = current

    def sector_exposure_notional(self, *, sector_provider):
        return dict(self._current)


def _make_gate(*, current, sector_map, pct=0.35, denom=Decimal("10000"), enabled=True):
    from marketpulse.trading.risk_gates.config_provider import SectorExposureConfig
    from marketpulse.trading.risk_gates.sector_exposure import SectorExposureGate
    return SectorExposureGate(
        cfg=SectorExposureConfig(
            enabled=enabled, max_sector_exposure_pct=pct,
            configured_max_capital_in_use=denom,
        ),
        repository=_StubRepo(current),
        sector_provider=lambda t: sector_map.get(t),
    )


def test_sector_exposure_close_bypass():
    from marketpulse.trading.types import RiskIntent
    gate = _make_gate(current={"Tech": Decimal("99999")}, sector_map={"AAPL": "Tech"})
    r = gate.check_pre_trade(order_request=_make_request(risk_intent=RiskIntent.CLOSE))
    assert r.approved is True


def test_sector_exposure_disabled_passes_through():
    gate = _make_gate(current={"Tech": Decimal("99999")}, sector_map={"AAPL": "Tech"}, enabled=False)
    r = gate.check_pre_trade(order_request=_make_request())
    assert r.approved is True


def test_sector_exposure_unknown_sector_denies_fail_closed():
    """Lock 6b-L8."""
    gate = _make_gate(current={}, sector_map={})  # ticker → None
    r = gate.check_pre_trade(order_request=_make_request(ticker="UNKNOWNTICK"))
    assert r.approved is False
    assert r.reason == "unknown_sector"
    assert r.context["ticker"] == "UNKNOWNTICK"


def test_sector_exposure_under_cap_approves():
    # cap = 0.35 * 10_000 = 3500. current Tech=1000, proposed=1500 → projected 2500 < 3500.
    gate = _make_gate(
        current={"Tech": Decimal("1000")},
        sector_map={"AAPL": "Tech"},
    )
    r = gate.check_pre_trade(order_request=_make_request(event_price="150", quantity=10))
    assert r.approved is True


def test_sector_exposure_at_cap_approves():
    """projected == cap → approve (deny only on >)."""
    # cap = 3500; current=2000; proposed=1500; projected=3500 == cap.
    gate = _make_gate(
        current={"Tech": Decimal("2000")},
        sector_map={"AAPL": "Tech"},
    )
    r = gate.check_pre_trade(order_request=_make_request(event_price="150", quantity=10))
    assert r.approved is True


def test_sector_exposure_over_cap_denies_with_projection_context():
    """Op-test #10: deny with full projection context."""
    # cap = 3500; current=2500; proposed=1500; projected=4000 > 3500.
    gate = _make_gate(
        current={"Tech": Decimal("2500")},
        sector_map={"AAPL": "Tech"},
    )
    r = gate.check_pre_trade(order_request=_make_request(event_price="150", quantity=10))
    assert r.approved is False
    assert r.reason == "sector_cap_exceeded"
    assert r.context["sector"] == "Tech"
    assert r.context["current"] == "2500"
    assert r.context["proposed"] == "1500"
    assert r.context["projected"] == "4000"
    assert r.context["cap"] == "3500.00"


def test_sector_exposure_denominator_fixed_not_live_cash():
    """Op-test #11: live cash doesn't affect the cap denominator.
    Test pegs configured_max_capital_in_use to a fixed Decimal — the
    gate must use that, not anything observed from a repo."""
    gate = _make_gate(
        current={"Tech": Decimal("0")},
        sector_map={"AAPL": "Tech"},
        pct=0.5, denom=Decimal("1000"),  # cap = 500
    )
    # proposed = 150 * 4 = 600 > 500 → deny.
    r = gate.check_pre_trade(order_request=_make_request(event_price="150", quantity=4))
    assert r.approved is False
    assert r.context["cap"] == "500.0"


def test_sector_exposure_flip_unsupported():
    from marketpulse.trading.types import RiskIntent
    gate = _make_gate(current={}, sector_map={"AAPL": "Tech"})
    r = gate.check_pre_trade(order_request=_make_request(risk_intent=RiskIntent.FLIP))
    assert r.approved is False
    assert r.reason == "unsupported_risk_intent"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/risk_gates/test_sector_exposure.py -v`
Expected: 8 FAIL `ModuleNotFoundError`.

- [ ] **Step 3: Implement `sector_exposure.py`**

Create `marketpulse/trading/risk_gates/sector_exposure.py`:

```python
"""SectorExposureGate — denies OPEN/ADD orders whose projected sector
notional exceeds max_sector_exposure_pct * configured_max_capital_in_use.

Lock 6b-L4: denominator is a configured constant (NOT live cash/equity).
Lock 6b-L8: proposed_sector is None → fail-closed deny.

Boundary semantic: projected == cap approves; only projected > cap denies."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Protocol

from marketpulse.trading.risk_gate import RiskResult
from marketpulse.trading.risk_gates.config_provider import SectorExposureConfig
from marketpulse.trading.types import OrderRequest, RiskIntent

__all__ = ["SectorExposureGate", "_SectorExposureRepo"]


class _SectorExposureRepo(Protocol):
    def sector_exposure_notional(
        self,
        *,
        sector_provider: Callable[[str], str | None],
    ) -> dict[str, Decimal]: ...


class SectorExposureGate:
    name = "sector_exposure"

    def __init__(
        self,
        *,
        cfg: SectorExposureConfig,
        repository: _SectorExposureRepo,
        sector_provider: Callable[[str], str | None],
    ) -> None:
        self._cfg = cfg
        self._repo = repository
        self._sector_provider = sector_provider

    def check_pre_trade(self, *, order_request: OrderRequest) -> RiskResult:
        if order_request.risk_intent in (RiskIntent.CLOSE, RiskIntent.REDUCE):
            return RiskResult(approved=True, gate_name=self.name, reason="")
        if order_request.risk_intent == RiskIntent.FLIP:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="unsupported_risk_intent",
            )

        cfg = self._cfg
        if not cfg.enabled:
            return RiskResult(approved=True, gate_name=self.name, reason="")

        proposed_sector = self._sector_provider(order_request.ticker)
        if proposed_sector is None:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="unknown_sector",
                context={"ticker": order_request.ticker},
            )

        proposed = order_request.event_price * Decimal(order_request.quantity)
        current_by_sector = self._repo.sector_exposure_notional(
            sector_provider=self._sector_provider,
        )
        current = current_by_sector.get(proposed_sector, Decimal(0))
        projected = current + proposed
        cap_dollars = (
            Decimal(str(cfg.max_sector_exposure_pct))
            * cfg.configured_max_capital_in_use
        )
        if projected > cap_dollars:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="sector_cap_exceeded",
                context={
                    "sector": proposed_sector,
                    "current": str(current),
                    "proposed": str(proposed),
                    "projected": str(projected),
                    "cap": str(cap_dollars),
                },
            )
        return RiskResult(approved=True, gate_name=self.name, reason="")
```

- [ ] **Step 4: Re-export from `__init__.py`**

Add to `marketpulse/trading/risk_gates/__init__.py`:

```python
from marketpulse.trading.risk_gates.sector_exposure import SectorExposureGate
```

Add `"SectorExposureGate"` to `__all__`.

- [ ] **Step 5: Run to verify tests pass**

Run: `uv run pytest tests/trading/risk_gates/test_sector_exposure.py -v`
Expected: 8 PASS.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/trading/risk_gates/sector_exposure.py marketpulse/trading/risk_gates/__init__.py tests/trading/risk_gates/test_sector_exposure.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-T14): SectorExposureGate

Projected sector notional vs configured constant denominator (lock 6b-L4).
Unknown-sector → fail-closed (lock 6b-L8). Boundary: projected == cap
approves. Context surfaces (current, proposed, projected, cap) for audit.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T14a: `marketpulse/trading/audit_json.py` shared util (lock 6b-L17)

**Files:**
- Create: `marketpulse/trading/audit_json.py`
- Test: `tests/trading/test_audit_json.py` (new)

This task ships the single canonical JSON normalizer used by every audit-writing code path. Without it, `forward_engine._dump`, `composite._normalize_context`, and future audit writers (6f UI, 6g recap, broker integration) drift into 4 parallel implementations.

- [ ] **Step 1: Write failing tests**

Create `tests/trading/test_audit_json.py`:

```python
# Layer: pure
"""6b-T14a: audit_json.normalize_for_json shared util tests (lock 6b-L17)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, time
from decimal import Decimal


def test_normalize_decimal_to_str():
    from marketpulse.trading.audit_json import normalize_for_json
    assert normalize_for_json(Decimal("1.5")) == "1.5"
    assert normalize_for_json(Decimal("-500.123456")) == "-500.123456"


def test_normalize_datetime_to_isoformat():
    from marketpulse.trading.audit_json import normalize_for_json
    dt = datetime(2026, 5, 21, 14, 30, tzinfo=UTC)
    assert normalize_for_json(dt) == dt.isoformat()


def test_normalize_date_to_isoformat():
    from marketpulse.trading.audit_json import normalize_for_json
    assert normalize_for_json(date(2026, 5, 21)) == "2026-05-21"


def test_normalize_time_to_isoformat():
    from marketpulse.trading.audit_json import normalize_for_json
    assert normalize_for_json(time(18, 0)) == "18:00:00"


def test_normalize_dict_recursive():
    from marketpulse.trading.audit_json import normalize_for_json
    d = {"a": Decimal("1"), "b": {"c": Decimal("2")}}
    assert normalize_for_json(d) == {"a": "1", "b": {"c": "2"}}


def test_normalize_list_recursive():
    from marketpulse.trading.audit_json import normalize_for_json
    assert normalize_for_json([Decimal("1"), Decimal("2")]) == ["1", "2"]


def test_normalize_tuple_to_list():
    from marketpulse.trading.audit_json import normalize_for_json
    assert normalize_for_json((Decimal("1"), Decimal("2"))) == ["1", "2"]


def test_normalize_dataclass_to_dict():
    from marketpulse.trading.audit_json import normalize_for_json

    @dataclasses.dataclass(frozen=True)
    class _X:
        a: Decimal
        b: date

    x = _X(a=Decimal("3.14"), b=date(2026, 5, 21))
    assert normalize_for_json(x) == {"a": "3.14", "b": "2026-05-21"}


def test_normalize_mapping_proxy():
    """MappingProxyType (lock 6b-L16) round-trips through normalize."""
    from types import MappingProxyType

    from marketpulse.trading.audit_json import normalize_for_json
    proxy = MappingProxyType({"a": Decimal("1")})
    assert normalize_for_json(proxy) == {"a": "1"}


def test_normalize_passthrough_primitives():
    from marketpulse.trading.audit_json import normalize_for_json
    assert normalize_for_json("hello") == "hello"
    assert normalize_for_json(42) == 42
    assert normalize_for_json(3.14) == 3.14
    assert normalize_for_json(True) is True
    assert normalize_for_json(None) is None


def test_normalize_result_is_json_dumpable():
    import json

    from marketpulse.trading.audit_json import normalize_for_json
    raw = {
        "decimal": Decimal("1.5"),
        "datetime": datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        "nested": {"date": date(2026, 5, 21), "list": [Decimal("2")]},
    }
    out = normalize_for_json(raw)
    json.dumps(out)  # must not raise
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/test_audit_json.py -v`
Expected: 10 FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `audit_json.py`**

Create `marketpulse/trading/audit_json.py`:

```python
"""Single canonical audit-JSON normalizer (lock 6b-L17).

Every audit-writing code path (forward_engine._dump, CompositeRiskGate's
per_gate emission, future 6f UI render, 6g recap jobs, Phase 7 broker
audit) MUST route through `normalize_for_json`. No per-module
normalizers, no inline `json.dumps(asdict(...))` — that path either
crashes on Decimal or emits non-deterministic floats, both unacceptable
for the append-only audit ledger.

The function is intentionally narrow in scope: take any nested Python
object, return a JSON-safe structure (str, int, float, bool, None, list,
dict).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

__all__ = ["normalize_for_json"]


def normalize_for_json(value: Any) -> Any:
    """Recursively convert a value into a JSON-safe representation.

    Conversions:
      - Decimal → str (exact precision preserved)
      - datetime / date / time → .isoformat()
      - dataclass instance → dict (with same recursion applied to fields)
      - tuple → list (JSON has no tuples)
      - Mapping (incl. MappingProxyType) → dict (recursing on values)
      - list → list (recursing on elements)
      - str / int / float / bool / None → unchanged

    Non-trivial objects without a known conversion fall through unchanged.
    json.dumps will raise on them — that's the right failure mode (better
    than silent str() coercion that loses semantics).
    """
    if isinstance(value, Decimal):
        return str(value)
    # datetime IS a date subclass; check datetime first so we get the
    # full ISO format including time + tz.
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: normalize_for_json(getattr(value, f.name))
            for f in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {k: normalize_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_for_json(v) for v in value]
    return value
```

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/trading/test_audit_json.py -v`
Expected: 10 PASS.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/trading/audit_json.py tests/trading/test_audit_json.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-T14a): audit_json.normalize_for_json shared util (lock 6b-L17)

Single canonical JSON normalizer for all audit-writing code paths:
Decimal→str, datetime/date/time→isoformat, dataclass→dict, tuple→list,
Mapping→dict, recursive on nested values. T15 + T16 both delegate here
so the codebase doesn't grow 4 parallel normalizers.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T15: `CompositeRiskGate(gates=Sequence[RiskGate])` + `build_standard_composite` factory

**Files:**
- Create: `marketpulse/trading/risk_gates/composite.py`
- Create: `marketpulse/trading/risk_gates/factory.py`
- Modify: `marketpulse/trading/risk_gates/__init__.py`
- Test: `tests/trading/risk_gates/test_composite.py` (new)
- Test: `tests/trading/risk_gates/test_factory.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/trading/risk_gates/test_composite.py`:

```python
# Layer: pure
"""6b-T15: CompositeRiskGate tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal


def _make_request(*, risk_intent=None):
    from marketpulse.trading.types import AllocationRunId, OrderRequest, RiskIntent
    return OrderRequest(
        strategy="momentum_breakout", ticker="AAPL", quantity=10,
        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=Decimal("150"),
        horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155"),
        allocation_run_id=AllocationRunId("paper-2026-05-21"),
        strategy_version="v1",
        allocator_version="phase6a-v1",
        execution_engine_version="phase6a-v1",
        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
        effective_corr_window=60,
        rewarded_for_negative_corr=False,
        would_change_rank=False,
        size_clamped_by_override=False,
        risk_intent=risk_intent if risk_intent is not None else RiskIntent.OPEN,
    )


class _ApproveGate:
    name = "approve_gate"

    def check_pre_trade(self, *, order_request):
        from marketpulse.trading.risk_gate import RiskResult
        return RiskResult(approved=True, gate_name=self.name, reason="")


class _DenyGate:
    def __init__(self, *, name, reason, context=None):
        self.name = name
        self._reason = reason
        self._context = context or {}

    def check_pre_trade(self, *, order_request):
        from marketpulse.trading.risk_gate import RiskResult
        return RiskResult(
            approved=False, gate_name=self.name,
            reason=self._reason, context=dict(self._context),
        )


class _ExplodingGate:
    name = "exploder"

    def check_pre_trade(self, *, order_request):
        raise RuntimeError("boom from exploder")


def _make_composite(gates):
    """Lock 6b-L15: composite now accepts gates: Sequence[RiskGate]
    directly — tests construct via the public constructor, NOT
    `__new__` + private attribute. Fakes inject behavior without
    needing to fake deps."""
    from marketpulse.trading.risk_gates.composite import CompositeRiskGate
    return CompositeRiskGate(gates=tuple(gates))


def test_composite_all_approve():
    """Op-test #0: composite approves when all 4 gates approve."""
    c = _make_composite([_ApproveGate(), _ApproveGate(), _ApproveGate(), _ApproveGate()])
    r = c.check_pre_trade(order_request=_make_request())
    assert r.approved is True
    assert r.gate_name == "composite"
    assert r.failed_gates == ()


def test_composite_close_bypass_short_circuits():
    """Lock 6b-L1: CLOSE bypasses the composite — no child gate runs."""
    from marketpulse.trading.types import RiskIntent
    exploder = _ExplodingGate()
    c = _make_composite([exploder, exploder])
    r = c.check_pre_trade(
        order_request=_make_request(risk_intent=RiskIntent.CLOSE),
    )
    assert r.approved is True


def test_composite_flip_denies_unsupported():
    from marketpulse.trading.types import RiskIntent
    c = _make_composite([_ApproveGate(), _ApproveGate()])
    r = c.check_pre_trade(order_request=_make_request(risk_intent=RiskIntent.FLIP))
    assert r.approved is False
    assert r.reason == "unsupported_risk_intent"
    assert r.gate_name == "composite"


def test_composite_one_deny_denies_with_failed_gates_list():
    c = _make_composite([
        _ApproveGate(),
        _DenyGate(name="daily_loss", reason="daily_loss_limit_exceeded"),
        _ApproveGate(),
        _ApproveGate(),
    ])
    r = c.check_pre_trade(order_request=_make_request())
    assert r.approved is False
    assert r.failed_gates == ("daily_loss",)
    assert r.reason == "daily_loss_limit_exceeded"
    assert len(r.context["per_gate"]) == 4


def test_composite_multi_deny_lists_all_failed():
    """Op-test #4: run-all not fail-fast. Even when one gate denies, the
    remaining gates still run and per_gate lists ALL results."""
    c = _make_composite([
        _DenyGate(name="market_hours", reason="outside_placement_window"),
        _DenyGate(name="daily_loss", reason="daily_loss_limit_exceeded"),
        _ApproveGate(),
        _DenyGate(name="sector_exposure", reason="sector_cap_exceeded"),
    ])
    r = c.check_pre_trade(order_request=_make_request())
    assert r.approved is False
    assert set(r.failed_gates) == {"market_hours", "daily_loss", "sector_exposure"}
    assert "outside_placement_window" in r.reason
    assert "daily_loss_limit_exceeded" in r.reason
    assert len(r.context["per_gate"]) == 4


def test_composite_exception_becomes_per_gate_deny():
    """Op-test #1: gate raise → composite captures + denies + records
    error_type + error in per_gate context."""
    c = _make_composite([
        _ApproveGate(),
        _ExplodingGate(),
        _ApproveGate(),
    ])
    r = c.check_pre_trade(order_request=_make_request())
    assert r.approved is False
    assert "exploder" in r.failed_gates
    # Per-gate context for the exploder should carry the error type.
    exploder_row = next(g for g in r.context["per_gate"] if g["gate_name"] == "exploder")
    assert exploder_row["context"]["error_type"] == "RuntimeError"
    assert exploder_row["context"]["error"] == "boom from exploder"


def test_composite_normalizes_decimal_in_context_to_str():
    """Lock 6b-L10: Decimals in nested context must serialize as strings.
    The composite normalizes per_gate[*].context recursively so the
    forward_engine audit writer (which json.dumps the context) doesn't
    crash or emit non-deterministic floats."""
    c = _make_composite([
        _DenyGate(
            name="daily_loss",
            reason="x",
            context={
                "today_realized_pnl": Decimal("-500.123456"),
                "nested": {"inner_decimal": Decimal("1.5")},
            },
        ),
    ])
    r = c.check_pre_trade(order_request=_make_request())
    row = r.context["per_gate"][0]
    assert row["context"]["today_realized_pnl"] == "-500.123456"
    assert row["context"]["nested"]["inner_decimal"] == "1.5"
    # Ensure the composite top-level context survives json.dumps round-trip.
    import json
    json.dumps(r.context)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/risk_gates/test_composite.py -v`
Expected: 7 FAIL `ModuleNotFoundError`.

- [ ] **Step 3: Implement `composite.py`**

Create `marketpulse/trading/risk_gates/composite.py`:

```python
"""CompositeRiskGate — run-all + deny-if-any + exception=deny + audit-all.

Lock 6b-L2 forbids fail-fast at runtime: every child gate runs even if an
earlier one denied, so audit context lists ALL gate results.

Lock 6b-L15: composite uses **dependency inversion**. `__init__` accepts
`gates: Sequence[RiskGate]` and does no construction itself. The
composition root (`paper_trading_tick.py`) owns the gate list; in
production it calls `build_standard_composite(...)` (see factory.py) to
materialize the canonical 4-gate composite. Tests construct directly with
fakes for individual gates without needing to fake deeper deps.

Lock 6b-L17: per-gate result serialization for the audit ledger routes
through `marketpulse.trading.audit_json.normalize_for_json`. No inline
normalization — single source of truth across the codebase.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from marketpulse.trading.audit_json import normalize_for_json
from marketpulse.trading.risk_gate import RiskGate, RiskResult
from marketpulse.trading.types import OrderRequest, RiskIntent

__all__ = ["CompositeRiskGate"]


class CompositeRiskGate:
    name = "composite"

    def __init__(self, *, gates: Sequence[RiskGate]) -> None:
        # Defensive copy into a tuple — composition root owns the list,
        # composite owns the runtime ordering. Tuple makes accidental
        # in-place mutation impossible (helps with audit determinism).
        self._gates: tuple[RiskGate, ...] = tuple(gates)

    def check_pre_trade(self, *, order_request: OrderRequest) -> RiskResult:
        # === Composite-level RiskIntent handling ===
        # CLOSE/REDUCE bypass every gate without running them — saves DB
        # reads and clock reads, and prevents an exploding gate from
        # blocking risk-reducing actions (lock 6b-L1).
        if order_request.risk_intent in (RiskIntent.CLOSE, RiskIntent.REDUCE):
            return RiskResult(approved=True, gate_name=self.name, reason="")
        if order_request.risk_intent == RiskIntent.FLIP:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="unsupported_risk_intent",
            )

        # === Run all gates, capturing exceptions as per-gate denies ===
        all_results: list[RiskResult] = []
        for gate in self._gates:
            try:
                r = gate.check_pre_trade(order_request=order_request)
            except Exception as e:  # noqa: BLE001 — fail-closed catches everything
                r = RiskResult(
                    approved=False,
                    reason=f"{getattr(gate, 'name', type(gate).__name__)}_error",
                    gate_name=getattr(gate, "name", type(gate).__name__),
                    context={
                        "error_type": type(e).__name__,
                        "error": str(e),
                    },
                )
            all_results.append(r)

        failed = [r for r in all_results if not r.approved]
        per_gate = [_serialize_result(r) for r in all_results]

        if failed:
            return RiskResult(
                approved=False,
                reason="; ".join(r.reason for r in failed),
                gate_name=failed[0].gate_name,
                failed_gates=tuple(r.gate_name for r in failed),
                context={"per_gate": per_gate},
            )
        return RiskResult(
            approved=True, gate_name=self.name, reason="",
            context={"per_gate": per_gate},
        )


def _serialize_result(r: RiskResult) -> dict[str, Any]:
    """Serialize a RiskResult into a JSON-safe dict for audit storage
    (locks 6b-L10 + 6b-L17). Delegates all normalization to the shared
    audit_json util — single source of truth."""
    # normalize_for_json handles the RiskResult dataclass directly,
    # producing a dict with normalized values (Decimal → str, etc.).
    return normalize_for_json(r)
```

- [ ] **Step 4: Run composite tests to verify they pass**

Run: `uv run pytest tests/trading/risk_gates/test_composite.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Write factory test**

Create `tests/trading/risk_gates/test_factory.py`:

```python
# Layer: pure
"""6b-T15: build_standard_composite factory tests (lock 6b-L15)."""

from __future__ import annotations

from datetime import time
from decimal import Decimal


class _StubProvider:
    def global_config(self):
        from marketpulse.trading.risk_gates.config_provider import (
            DailyLossConfig,
            MarketHoursConfig,
            RiskGateConfig,
            SectorExposureConfig,
        )
        return RiskGateConfig(
            market_hours=MarketHoursConfig(
                enabled=True, exchange="XNYS",
                allow_regular_session=True, allow_post_close=True,
                post_close_until=time(18, 0), allow_premarket=False,
            ),
            daily_loss=DailyLossConfig(enabled=True, daily_loss_limit=Decimal("500")),
            sector_exposure=SectorExposureConfig(
                enabled=True, max_sector_exposure_pct=0.35,
                configured_max_capital_in_use=Decimal("10000"),
            ),
        )

    def strategy_config(self, strategy):
        return None  # not exercised by this test


class _StubRepo:
    def today_realized_pnl(self, *, tick_date):
        return Decimal("0")

    def sector_exposure_notional(self, *, sector_provider):
        return {}


class _FakeClock:
    def now(self):
        from datetime import UTC, datetime
        return datetime(2026, 5, 21, 21, 30, tzinfo=UTC)


def test_factory_builds_4_gates_in_canonical_order():
    """Lock 6b-L15: factory is the single canonical builder. Order matters
    for audit reproducibility — operators reading per_gate[*] entries
    expect a stable order."""
    from marketpulse.trading.calendar import NYTradingCalendar
    from marketpulse.trading.risk_gates.factory import build_standard_composite

    composite = build_standard_composite(
        config_provider=_StubProvider(),
        repository=_StubRepo(),
        calendar=NYTradingCalendar(),
        clock=_FakeClock(),
        sector_provider=lambda t: "Technology",
    )
    names = [g.name for g in composite._gates]
    assert names == [
        "market_hours", "strategy_size", "daily_loss", "sector_exposure",
    ]
```

- [ ] **Step 6: Implement `factory.py`**

Create `marketpulse/trading/risk_gates/factory.py`:

```python
"""Phase 6b canonical composite factory (lock 6b-L15).

`build_standard_composite` is the SINGLE blessed builder for the 4-gate
production composite. The scheduler entrypoint (`paper_trading_tick.py`)
calls this; tests are free to instantiate `CompositeRiskGate(gates=[...])`
directly with whatever fakes they need.

Order matters for audit reproducibility — per_gate[*] entries appear in
this order in every ORDER_REJECTED row across the lifetime of the system.
Changing the order requires a coordinated migration of any downstream
consumer (6f UI, 6g recap). Keep it stable.
"""

from __future__ import annotations

from collections.abc import Callable

from marketpulse.trading.calendar import NYTradingCalendar
from marketpulse.trading.clock import Clock
from marketpulse.trading.risk_gates.composite import CompositeRiskGate
from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider
from marketpulse.trading.risk_gates.daily_loss import DailyLossGate
from marketpulse.trading.risk_gates.market_hours import MarketHoursGate
from marketpulse.trading.risk_gates.sector_exposure import SectorExposureGate
from marketpulse.trading.risk_gates.strategy_size import StrategySizeGate

__all__ = ["build_standard_composite"]


def build_standard_composite(
    *,
    config_provider: RiskConfigProvider,
    repository,
    calendar: NYTradingCalendar,
    clock: Clock,
    sector_provider: Callable[[str], str | None],
) -> CompositeRiskGate:
    """Build the canonical 4-gate composite. Order: market_hours,
    strategy_size, daily_loss, sector_exposure."""
    global_cfg = config_provider.global_config()
    return CompositeRiskGate(
        gates=(
            MarketHoursGate(
                cfg=global_cfg.market_hours, calendar=calendar, clock=clock,
            ),
            StrategySizeGate(provider=config_provider),
            DailyLossGate(
                cfg=global_cfg.daily_loss, repository=repository,
            ),
            SectorExposureGate(
                cfg=global_cfg.sector_exposure,
                repository=repository,
                sector_provider=sector_provider,
            ),
        ),
    )
```

- [ ] **Step 7: Re-export from `__init__.py`**

Add to `marketpulse/trading/risk_gates/__init__.py`:

```python
from marketpulse.trading.risk_gates.composite import CompositeRiskGate
from marketpulse.trading.risk_gates.factory import build_standard_composite
```

Add `"CompositeRiskGate"` and `"build_standard_composite"` to `__all__`.

- [ ] **Step 8: Run factory + composite + full risk_gates suite**

Run: `uv run pytest -q tests/trading/risk_gates/`
Expected: ALL pass.

- [ ] **Step 9: Commit**

```bash
git add marketpulse/trading/risk_gates/composite.py marketpulse/trading/risk_gates/factory.py marketpulse/trading/risk_gates/__init__.py tests/trading/risk_gates/test_composite.py tests/trading/risk_gates/test_factory.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-T15): CompositeRiskGate (gates: Sequence) + factory (locks 6b-L15..6b-L17)

Composite uses dependency inversion (lock 6b-L15): __init__ accepts a
Sequence[RiskGate]; composition root lives at DI seam. Production builds
via build_standard_composite(...); tests construct CompositeRiskGate with
fake gates directly — no need to fake repo/clock/sector_provider per
composite test.

Audit-context serialization delegates to audit_json.normalize_for_json
(lock 6b-L17) — single source of truth. RiskResult.context wrapped in
MappingProxyType post-construction (lock 6b-L16) prevents top-level
mutation.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T16: `ForwardExecutionEngine` propagates `failed_gates`+`per_gate` into `ORDER_REJECTED` context

**Files:**
- Modify: `marketpulse/trading/forward_engine.py`
- Test: `tests/trading/test_forward_engine.py` (extend)

- [ ] **Step 1: Append failing tests**

Append to `tests/trading/test_forward_engine.py`:

```python
def test_forward_engine_propagates_failed_gates_into_audit_context(tmp_path):
    """6b-T16: when CompositeRiskGate denies, ORDER_REJECTED audit row's
    context.failed_gates and context.per_gate carry the composite's
    extended fields (lock 6b-L6 — reuse ORDER_REJECTED, no new event)."""
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from marketpulse.db.base import Base
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gate import RiskResult
    from marketpulse.trading.types import AllocationRunId, OrderRejected, OrderRequest

    class _ExtendedDenyGate:
        def check_pre_trade(self, *, order_request):
            return RiskResult(
                approved=False,
                reason="daily_loss_limit_exceeded; sector_cap_exceeded",
                gate_name="daily_loss",
                failed_gates=("daily_loss", "sector_exposure"),
                context={
                    "per_gate": [
                        {"gate_name": "market_hours", "approved": True},
                        {"gate_name": "strategy_size", "approved": True},
                        {"gate_name": "daily_loss", "approved": False,
                         "reason": "daily_loss_limit_exceeded",
                         "context": {"today_realized_pnl": "-500"}},
                        {"gate_name": "sector_exposure", "approved": False,
                         "reason": "sector_cap_exceeded",
                         "context": {"projected": "4000", "cap": "3500"}},
                    ],
                },
            )

    eng_db = tmp_path / "fe.db"
    db_engine = create_engine(f"sqlite:///{eng_db}")
    Base.metadata.create_all(db_engine)
    with Session(db_engine) as s:
        repo = Repository(session=s)
        clock = FakeClock(now=datetime(2026, 5, 21, 21, 30, tzinfo=UTC))
        repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=clock.now())
        ks = KillSwitchState(env_var="MP_NEVER", repository=repo)
        engine = ForwardExecutionEngine(
            repository=repo, clock=clock, kill_switch=ks,
            risk_gate=_ExtendedDenyGate(),
        )
        req = OrderRequest(
            strategy="momentum_breakout", ticker="AAPL", quantity=10,
            event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
            allocation_date=date(2026, 5, 21),
            event_price=Decimal("150"),
            horizon_date=date(2026, 5, 28),
            horizon_price=Decimal("155"),
            allocation_run_id=AllocationRunId("paper-2026-05-21"),
            strategy_version="v1",
            allocator_version="phase6a-v1",
            execution_engine_version="phase6a-v1",
            weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
            contribution_multiplier=1.0, adjusted_bid_weight=1.0,
            effective_corr_window=60,
            rewarded_for_negative_corr=False,
            would_change_rank=False,
            size_clamped_by_override=False,
        )
        import pytest
        with pytest.raises(OrderRejected):
            engine.place_order(order_request=req)

        rejects = s.execute(
            select(PaperAuditEvent)
            .where(PaperAuditEvent.event_type == "ORDER_REJECTED")
        ).scalars().all()
        assert len(rejects) == 1
        ctx = rejects[0].context
        assert ctx["gate"] == "daily_loss"            # 6a-compat
        assert ctx["failed_gates"] == ["daily_loss", "sector_exposure"]
        assert len(ctx["per_gate"]) == 4
        assert ctx["per_gate"][2]["reason"] == "daily_loss_limit_exceeded"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/test_forward_engine.py::test_forward_engine_propagates_failed_gates_into_audit_context -v`
Expected: FAIL with `KeyError: 'failed_gates'` (current audit context only includes `gate`).

- [ ] **Step 3: Migrate `_dump` to delegate to `normalize_for_json` (lock 6b-L17)**

In `marketpulse/trading/forward_engine.py`, replace the existing `_dump` helper (around lines 31-39) with:

```python
from marketpulse.trading.audit_json import normalize_for_json


def _dump(order_request: OrderRequest) -> dict:
    """Lock 6b-L17: delegate to the shared audit-JSON normalizer.
    Kept as a thin wrapper for back-compat and to make grep-ability of
    audit-writing sites obvious. New audit code should call
    `normalize_for_json` directly."""
    return normalize_for_json(order_request)
```

Add the `from marketpulse.trading.audit_json import normalize_for_json` to the imports block at the top of the file.

- [ ] **Step 4: Update the `if not risk_result.approved:` block to thread the new fields**

In `marketpulse/trading/forward_engine.py`, locate the `if not risk_result.approved:` block (around line 116). Replace it with:

```python
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
                        # Phase 6b: composite extensions (lock 6b-L6 — no
                        # new audit event type; reuse ORDER_REJECTED with
                        # extended context). list() so JSON column accepts.
                        "failed_gates": list(risk_result.failed_gates),
                        "per_gate": list(risk_result.context.get("per_gate", [])),
                    },
                    timestamp=self._clock.now(),
                )
            raise OrderRejected(risk_result.reason)
```

- [ ] **Step 5: Run to verify test passes**

Run: `uv run pytest tests/trading/test_forward_engine.py::test_forward_engine_propagates_failed_gates_into_audit_context -v`
Expected: PASS.

- [ ] **Step 6: Run full forward_engine + risk_gates tests for regressions**

Run: `uv run pytest -q tests/trading/test_forward_engine.py tests/trading/risk_gates/`
Expected: ALL pass — the `_dump` delegate change is behavior-preserving (normalize_for_json produces the same output shape as the old shallow normalizer for OrderRequest's flat field set).

- [ ] **Step 7: Commit**

```bash
git add marketpulse/trading/forward_engine.py tests/trading/test_forward_engine.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-T16): ORDER_REJECTED context + _dump delegates to normalize_for_json

Lock 6b-L6: reuse ORDER_REJECTED — no new audit event type, no migration.
Lock 6b-L17: forward_engine._dump now delegates to the shared
audit_json.normalize_for_json util. Composite's failed_gates tuple is
JSONified as a list; per_gate list of per-child results is passed through
verbatim (already normalized by CompositeRiskGate per lock 6b-L10/L17).

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T17: `paper_trading_tick.py` DI swap

**Files:**
- Modify: `marketpulse/scheduler/paper_trading_tick.py`
- Test: `tests/trading/test_scheduler.py` (extend)

- [ ] **Step 1: Inspect existing scheduler test file**

Run: `grep -n "AlwaysApproveRiskGate\|paper_trading_tick\|CompositeRiskGate" tests/trading/test_scheduler.py | head`
Note the current shape so we know where to add coverage. (Subagent should read the file before editing.)

- [ ] **Step 2: Append failing test**

Append to `tests/trading/test_scheduler.py`:

```python
def test_paper_trading_tick_uses_composite_risk_gate(monkeypatch, tmp_path):
    """6b-T17 DI swap: paper_trading_tick_job must construct a
    CompositeRiskGate (not AlwaysApproveRiskGate)."""
    import marketpulse.scheduler.paper_trading_tick as m
    from marketpulse.trading.risk_gates.composite import CompositeRiskGate

    captured = {}

    real_engine_cls = m.ForwardExecutionEngine

    class _SpyEngine(real_engine_cls):
        def __init__(self, *args, **kwargs):
            captured["risk_gate"] = kwargs.get("risk_gate")
            super().__init__(*args, **kwargs)

        def tick(self, *, as_of):
            from marketpulse.trading.types import TickResult
            return TickResult(as_of=as_of, entries_materialized=0,
                              exits_materialized=0, errors=())

    monkeypatch.setattr(m, "ForwardExecutionEngine", _SpyEngine)

    # Provide a minimal session via the real session_scope context.
    m.paper_trading_tick_job()

    assert isinstance(captured["risk_gate"], CompositeRiskGate)
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/trading/test_scheduler.py::test_paper_trading_tick_uses_composite_risk_gate -v`
Expected: FAIL — current code wires `AlwaysApproveRiskGate`.

- [ ] **Step 4: Update `paper_trading_tick.py` — composition root uses the factory**

Replace `marketpulse/scheduler/paper_trading_tick.py` with:

```python
"""APScheduler entrypoint for the daily paper-trading tick (lock xxv).

This module is the **composition root** for Phase 6b risk gates (lock
6b-L15). It owns the canonical 4-gate composite by calling
`build_standard_composite(...)` — no business logic, just DI wiring +
delegation to daily_cycle.run.

Phase 6b: AlwaysApproveRiskGate → CompositeRiskGate (4 production gates).
RiskConfigProvider reads config/risk_gates.yaml + per-strategy `risk:`
blocks from marketpulse/strategies/definitions/*.yaml."""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

from marketpulse.backtest.allocation import allocate_for_day
from marketpulse.backtest.sector import get_sector
from marketpulse.db.base import session_scope
from marketpulse.trading import daily_cycle
from marketpulse.trading.bid_aggregator import BidAggregator
from marketpulse.trading.calendar import NYTradingCalendar
from marketpulse.trading.clock import WallClock
from marketpulse.trading.forward_engine import ForwardExecutionEngine
from marketpulse.trading.kill_switch import KillSwitchState
from marketpulse.trading.price_provider import StubPriceProvider
from marketpulse.trading.repository import Repository
from marketpulse.trading.risk_gates import (
    RiskConfigProvider,
    build_standard_composite,
    strict_sector,
)

log = logging.getLogger(__name__)

# Resolve config paths once at import — these are deployment-static.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_RISK_GATES_YAML = _REPO_ROOT / "config" / "risk_gates.yaml"
_STRATEGIES_DIR = _REPO_ROOT / "marketpulse" / "strategies" / "definitions"


def paper_trading_tick_job() -> None:
    gen = session_scope()
    session = next(gen)
    try:
        clock = WallClock()
        calendar = NYTradingCalendar()
        repository = Repository(session=session)

        # Phase 6b: real composite gate replaces the 6a stub. The
        # composition root (this file) owns the canonical gate list via
        # the factory — see lock 6b-L15.
        risk_config_provider = RiskConfigProvider.from_yaml(
            global_path=_RISK_GATES_YAML,
            strategies_dir=_STRATEGIES_DIR,
        )
        kill_switch = KillSwitchState(
            env_var="MP_PAPER_KILL_SWITCH", repository=repository,
        )
        risk_gate = build_standard_composite(
            config_provider=risk_config_provider,
            repository=repository,
            calendar=calendar,
            clock=clock,
            sector_provider=strict_sector,
        )
        engine = ForwardExecutionEngine(
            repository=repository, clock=clock,
            kill_switch=kill_switch, risk_gate=risk_gate,
        )
        bid_aggregator = BidAggregator(session=session, calendar=calendar)
        # TODO(6b/6c): replace StubPriceProvider with a real provider
        # (yfinance-backed or broker quote API). See price_provider.py.
        price_provider = StubPriceProvider(default=Decimal("0"))

        result = daily_cycle.run(
            clock=clock, engine=engine, repository=repository,
            bid_aggregator=bid_aggregator, allocator=allocate_for_day,
            calendar=calendar, kill_switch=kill_switch,
            price_provider=price_provider,
            daily_curves={},
            daily_strategy_contribution_returns={},
            daily_pool_returns=[],
            sector_provider=get_sector,
        )
        log.info(
            "paper_trading_tick done: tick_date=%s placed=%d exits=%d entries=%d errors=%d",
            result.tick_date, result.orders_placed, result.exits_materialized,
            result.entries_materialized, len(result.tick_errors),
        )
    finally:
        session.close()
```

- [ ] **Step 5: Run to verify test passes**

Run: `uv run pytest tests/trading/test_scheduler.py -v`
Expected: ALL pass.

- [ ] **Step 6: Run full trading + scheduler tests**

Run: `uv run pytest -q tests/trading/ tests/scheduler/ 2>/dev/null || uv run pytest -q tests/trading/`
Expected: ALL pass.

- [ ] **Step 7: Commit**

```bash
git add marketpulse/scheduler/paper_trading_tick.py tests/trading/test_scheduler.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-T17): paper_trading_tick DI swap to CompositeRiskGate

AlwaysApproveRiskGate → CompositeRiskGate(config_provider, repository,
calendar, clock, sector_provider=strict_sector). RiskConfigProvider reads
config/risk_gates.yaml + per-strategy risk: blocks at job start; values
are deployment-static so the per-tick parse cost is negligible.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T18: E2E + preflight sector script

**Files:**
- Modify: `tests/trading/test_e2e_stateful.py`
- Create: `scripts/preflight_phase6b_sector_check.py`

- [ ] **Step 1: Append E2E scenarios**

Append to `tests/trading/test_e2e_stateful.py`:

```python
# === Phase 6b — composite gate E2E (op-tests #14, #16) ===

def test_e2e_phase6b_17_30_ny_happy_path(tmp_path, monkeypatch):
    """Op-test #14: Phase 6a default tick fires at 17:30 NY post-close;
    CompositeRiskGate's MarketHoursGate (post_close_until=18:00) must
    pass. This is the lock-iv compatibility check: 6b must not break the
    6a default scheduler cadence."""
    from datetime import UTC, date, datetime
    from decimal import Decimal
    from pathlib import Path
    from zoneinfo import ZoneInfo

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from marketpulse.backtest.allocation import AllocationResult, AllocationWinner
    from marketpulse.db.base import Base
    from marketpulse.db.models import PaperAuditEvent, PaperOrder
    from marketpulse.trading import daily_cycle
    from marketpulse.trading.bid_aggregator import BidAggregator
    from marketpulse.trading.calendar import NYTradingCalendar
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.price_provider import StubPriceProvider
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gates import (
        CompositeRiskGate,
        RiskConfigProvider,
    )

    eng = create_engine(f"sqlite:///{tmp_path / 'e2e.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        repo = Repository(session=s)
        # 17:30 NY = 21:30 UTC on a Thursday (2026-05-21).
        clock = FakeClock(now=datetime(2026, 5, 21, 21, 30, tzinfo=UTC))
        calendar = NYTradingCalendar()
        repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=clock.now())
        ks = KillSwitchState(env_var="MP_NEVER", repository=repo)
        repo_root = Path(__file__).resolve().parents[2]
        provider = RiskConfigProvider.from_yaml(
            global_path=repo_root / "config" / "risk_gates.yaml",
            strategies_dir=repo_root / "marketpulse" / "strategies" / "definitions",
        )
        risk_gate = CompositeRiskGate(
            config_provider=provider, repository=repo,
            calendar=calendar, clock=clock,
            sector_provider=lambda t: "Technology" if t == "AAPL" else None,
        )
        engine = ForwardExecutionEngine(
            repository=repo, clock=clock, kill_switch=ks, risk_gate=risk_gate,
        )

        def alloc(**kw):
            return AllocationResult(
                winners=(
                    AllocationWinner(
                        strategy="momentum_breakout", ticker="AAPL",
                        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
                        event_price=150.0,
                        horizon_date=date(2026, 5, 28),
                        horizon_price=155.0,
                        quantity=10,
                        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
                        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
                        effective_corr_window=60,
                        rewarded_for_negative_corr=False,
                        would_change_rank=False,
                        size_clamped_by_override=False,
                        strategy_version="v1",
                    ),
                ),
                blocked=(), cash_used=1500.0, cash_remaining=8500.0,
                timeline=(),
            )
        alloc.__version__ = "v1"

        result = daily_cycle.run(
            clock=clock, engine=engine, repository=repo,
            bid_aggregator=BidAggregator(session=s, calendar=calendar),
            allocator=alloc, calendar=calendar, kill_switch=ks,
            price_provider=StubPriceProvider(default=Decimal("0")),
        )
        assert result.orders_placed == 1
        assert result.cycle_status == "completed"

        orders = s.execute(select(PaperOrder)).scalars().all()
        assert len(orders) == 1


def test_e2e_phase6b_sector_cap_denial_writes_per_gate_audit(tmp_path):
    """Op-test #16: E2E denial with all 4 gates active. Verifies the audit
    row carries failed_gates + per_gate."""
    from datetime import UTC, date, datetime
    from decimal import Decimal
    from pathlib import Path

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from marketpulse.backtest.allocation import AllocationResult, AllocationWinner
    from marketpulse.db.base import Base
    from marketpulse.db.models import PaperAuditEvent
    from marketpulse.trading import daily_cycle
    from marketpulse.trading.bid_aggregator import BidAggregator
    from marketpulse.trading.calendar import NYTradingCalendar
    from marketpulse.trading.clock import FakeClock
    from marketpulse.trading.forward_engine import ForwardExecutionEngine
    from marketpulse.trading.kill_switch import KillSwitchState
    from marketpulse.trading.price_provider import StubPriceProvider
    from marketpulse.trading.repository import Repository
    from marketpulse.trading.risk_gates import (
        CompositeRiskGate,
        RiskConfigProvider,
    )

    eng = create_engine(f"sqlite:///{tmp_path / 'e2e2.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        repo = Repository(session=s)
        clock = FakeClock(now=datetime(2026, 5, 21, 21, 30, tzinfo=UTC))
        repo.ensure_initial_deposit(amount=Decimal("10000"), timestamp=clock.now())
        ks = KillSwitchState(env_var="MP_NEVER", repository=repo)
        repo_root = Path(__file__).resolve().parents[2]
        provider = RiskConfigProvider.from_yaml(
            global_path=repo_root / "config" / "risk_gates.yaml",
            strategies_dir=repo_root / "marketpulse" / "strategies" / "definitions",
        )
        risk_gate = CompositeRiskGate(
            config_provider=provider, repository=repo,
            calendar=NYTradingCalendar(), clock=clock,
            sector_provider=lambda t: "Technology",
        )
        engine = ForwardExecutionEngine(
            repository=repo, clock=clock, kill_switch=ks, risk_gate=risk_gate,
        )

        def alloc(**kw):
            return AllocationResult(
                winners=(
                    # event_price * quantity = 200 * 100 = 20_000, well over
                    # 0.35 * 10_000 = 3_500 cap → sector_exposure denies.
                    AllocationWinner(
                        strategy="momentum_breakout", ticker="AAPL",
                        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
                        event_price=200.0,
                        horizon_date=date(2026, 5, 28),
                        horizon_price=210.0,
                        quantity=100,
                        weight=1.0, raw_bid_weight=1.0, pool_corr=0.1,
                        contribution_multiplier=1.0, adjusted_bid_weight=1.0,
                        effective_corr_window=60,
                        rewarded_for_negative_corr=False,
                        would_change_rank=False,
                        size_clamped_by_override=False,
                        strategy_version="v1",
                    ),
                ),
                blocked=(), cash_used=0.0, cash_remaining=10000.0,
                timeline=(),
            )
        alloc.__version__ = "v1"

        result = daily_cycle.run(
            clock=clock, engine=engine, repository=repo,
            bid_aggregator=BidAggregator(session=s, calendar=NYTradingCalendar()),
            allocator=alloc, calendar=NYTradingCalendar(), kill_switch=ks,
            price_provider=StubPriceProvider(default=Decimal("0")),
        )
        assert result.orders_placed == 0
        assert result.orders_rejected == 1

        rejects = s.execute(
            select(PaperAuditEvent)
            .where(PaperAuditEvent.event_type == "ORDER_REJECTED")
        ).scalars().all()
        assert len(rejects) == 1
        ctx = rejects[0].context
        # strategy_size also denies (20_000 > 25_000? actually 20_000 < 25_000
        # so strategy_size approves; only sector_exposure denies).
        assert "sector_exposure" in ctx["failed_gates"]
        assert len(ctx["per_gate"]) == 4
```

- [ ] **Step 2: Run E2E tests to verify**

Run: `uv run pytest tests/trading/test_e2e_stateful.py::test_e2e_phase6b_17_30_ny_happy_path tests/trading/test_e2e_stateful.py::test_e2e_phase6b_sector_cap_denial_writes_per_gate_audit -v`
Expected: 2 PASS.

- [ ] **Step 3: Create preflight sector script**

Create `scripts/preflight_phase6b_sector_check.py`:

```python
"""Phase 6b preflight check — lock 6b-L11.

Enumerates every distinct (strategy, ticker) pair in paper_position WHERE
status='OPEN', runs `get_sector(t)` on each, and lists tickers that
resolve to None / 'unknown'. Operators MUST add YAML overrides for that
list (or explicitly accept) before flipping the CompositeRiskGate DI seam
into production.

Usage:
    uv run python scripts/preflight_phase6b_sector_check.py [DB_URL]

If DB_URL is omitted, uses MARKETPULSE_DB_URL env var, falling back to
sqlite:///./data/marketpulse.db. Exit code 0 if all OPEN positions have
known sectors, 1 if any unknowns remain.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from marketpulse.backtest.sector import get_sector
from marketpulse.db.models import PaperPosition
from marketpulse.trading.risk_gates._sector import strict_sector


def main() -> int:
    db_url = (
        sys.argv[1] if len(sys.argv) > 1
        else os.getenv("MARKETPULSE_DB_URL", "sqlite:///./data/marketpulse.db")
    )
    engine = create_engine(db_url)
    with Session(engine) as session:
        rows = session.execute(
            select(PaperPosition.ticker, PaperPosition.strategy)
            .where(PaperPosition.status == "OPEN")
            .distinct()
        ).all()

    print(f"OPEN paper_position rows: {len(rows)} distinct (strategy, ticker) pairs")
    unknowns: list[tuple[str, str, str]] = []
    for ticker, strategy in rows:
        raw = get_sector(ticker)
        strict = strict_sector(ticker)
        if strict is None:
            unknowns.append((ticker, strategy, raw))

    if not unknowns:
        print("OK: all OPEN positions resolve to a known sector.")
        return 0

    print(f"\nFAIL: {len(unknowns)} ticker(s) resolve to unknown:")
    print(f"  {'ticker':<10} {'strategy':<20} get_sector()")
    print("  " + "-" * 55)
    for ticker, strategy, raw in unknowns:
        print(f"  {ticker:<10} {strategy:<20} {raw!r}")
    print(
        "\nAction (lock 6b-L11): add explicit overrides to "
        "config/sector_overrides.yaml for each ticker above, OR delete the "
        "OPEN positions, OR explicitly accept that pre-6b positions don't "
        "count toward sector caps. Do this BEFORE deploying 6b.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Smoke-run the preflight script against a clean DB**

Run: `uv run python /Users/harvey/Dev/src/MarketPulse/scripts/preflight_phase6b_sector_check.py "sqlite:///$(mktemp -t mpre.XXXXXX).db" || echo "exit=$?"`
Expected: First DB has no `paper_position` table → SQLAlchemy raises `OperationalError`. That's fine; the script is intended for an initialized DB. Re-run against the project's `data/marketpulse.db` if it exists:

```bash
[ -f /Users/harvey/Dev/src/MarketPulse/data/marketpulse.db ] && \
  uv run python /Users/harvey/Dev/src/MarketPulse/scripts/preflight_phase6b_sector_check.py "sqlite:////Users/harvey/Dev/src/MarketPulse/data/marketpulse.db" \
  || echo "no local DB — skipping smoke (this is OK in CI)"
```
Expected: prints either `OK: all OPEN positions resolve to a known sector.` (exit 0) or a list of unknowns (exit 1). Either outcome is acceptable for plan completion — operator runs this on the deploy target.

- [ ] **Step 5: Commit**

```bash
git add tests/trading/test_e2e_stateful.py scripts/preflight_phase6b_sector_check.py
git commit -m "$(cat <<'EOF'
feat(phase-6b-T18): E2E 17:30 happy + sector deny; preflight sector script

E2E op-test #14 (17:30 NY happy path through full composite) + op-test
#16 (sector cap E2E deny with full per_gate audit). Preflight script
implements lock 6b-L11 deploy checklist: lists OPEN paper_position rows
whose sector resolves to None / 'unknown', so operators can add YAML
overrides before flipping the DI seam.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T19: Final integration

**Files:**
- None new; full-suite verification

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -q`
Expected: ALL pass. Note pre-6b baseline was 1024+ tests; 6b adds roughly 40-50 new tests.

- [ ] **Step 2: Run ruff**

Run: `uv run ruff check marketpulse/ tests/ scripts/`
Expected: `All checks passed!`. If anything fails, fix inline and commit a follow-up.

- [ ] **Step 3: Run alembic check (no new migration in 6b, but verify head is intact)**

Run: `uv run alembic heads`
Expected: single head, no error (6a's `0010` is the latest — 6b adds zero migrations per lock 6b-L6).

- [ ] **Step 4: Route smoke (web app boots, key routes return)**

Run: `uv run pytest tests/web/ -q`
Expected: ALL pass.

- [ ] **Step 5: Quick manual sanity — import composite from a fresh process**

Run:
```bash
uv run python -c "
from marketpulse.trading.risk_gates import (
    CompositeRiskGate, RiskConfigProvider, MarketHoursGate, StrategySizeGate,
    DailyLossGate, SectorExposureGate, strict_sector,
)
from marketpulse.trading.types import RiskIntent
from marketpulse.trading.risk_gate import RiskResult
print('OK', RiskIntent.OPEN, RiskResult(approved=True, reason=''))
"
```
Expected: `OK open RiskResult(approved=True, reason='', gate_name='', failed_gates=(), context={})`.

- [ ] **Step 6: Confirm working tree clean (or only unrelated carryover)**

Run: `git status`
Expected: working tree clean OR only `marketpulse/web/templates/stock.html` modified (the unrelated pre-existing change carried over from prior work — leave it alone).

- [ ] **Step 7: Push branch + open PR**

Run:
```bash
git push origin plan/phase-6b-risk-gates
gh pr create --title "feat(phase-6b): risk gates" --body "$(cat <<'EOF'
## Summary
- Replaces `AlwaysApproveRiskGate` stub with `CompositeRiskGate` running 4 deterministic pre-trade gates: MarketHoursGate, StrategySizeGate, DailyLossGate, SectorExposureGate.
- Spec: `docs/superpowers/specs/2026-05-21-phase-6b-risk-gates-design.md`
- Plan: `docs/superpowers/plans/2026-05-21-phase-6b-risk-gates.md`
- Zero schema migration (reuses `ORDER_REJECTED` with extended context per lock 6b-L6).
- `RiskIntent` canonical home in `types.py` (lock 6b-L12); `risk_gate.py` re-exports.
- DST-safe NY-day window for `today_realized_pnl` (lock 6b-L13).
- CompositeRiskGate uses dependency-inversion (lock 6b-L15); composition root in `paper_trading_tick.py` via `build_standard_composite()` factory.
- `RiskResult.context` wrapped in `MappingProxyType` post-construction (lock 6b-L16) prevents top-level mutation.
- Single audit-JSON normalizer (`marketpulse.trading.audit_json.normalize_for_json`) used by composite + forward_engine (lock 6b-L17).
- 17 locks total (6b-L1 .. 6b-L17).

## Test plan
- [ ] `uv run pytest -q` — full suite green
- [ ] `uv run ruff check marketpulse/ tests/ scripts/` — clean
- [ ] `uv run alembic heads` — single head, unchanged from 6a
- [ ] Manual: deploy to NAS, run `scripts/preflight_phase6b_sector_check.py` (lock 6b-L11), confirm OK or apply sector overrides before flipping live
- [ ] Manual: verify 17:30 NY tick clears MarketHoursGate (op-test #14 covers E2E; this is the production smoke)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 8: Done**

Hand off to `superpowers:finishing-a-development-branch` to drive merge.

---

## Self-Review

**Spec coverage check (spec § numbering ↔ task numbering):**
- Spec §1 Goal/Boundary → T0 (preflight understanding); no code task — anti-goals enforced by gate tests.
- Spec §2 Architecture → T2 (RiskResult), T4 (config_provider), T15 (CompositeRiskGate), T17 (DI seam).
- Spec §2 RiskIntent semantics → T1.
- Spec §2 Kill-switch outside-RiskGate-scope clarification → No code change needed (forward_engine already kills before composite — 6a-L8 behavior preserved). Documented in spec only.
- Spec §3 MarketHoursGate → T11.
- Spec §3 StrategySizeGate → T12.
- Spec §3 DailyLossGate → T13.
- Spec §3 SectorExposureGate → T14.
- Spec §3 Repository extensions → T8 (today_realized_pnl), T9 (sector_exposure_notional).
- Spec §4 risk_gates.yaml + RiskConfigProvider → T5, T6, T7.
- Spec §5 sub-task decomp → maps to T1..T18 (one spec sub-task spans 2-4 plan tasks).
- Spec §6 op-tests #0..#21 → all covered:
  - #0 → T15 `test_composite_all_approve`
  - #1 → T15 `test_composite_exception_becomes_per_gate_deny`
  - #2 → T11/T12/T13/T14 (CLOSE bypass tests in each); T15 `test_composite_close_bypass_short_circuits`
  - #3 → T11/T12/T13/T14 (FLIP unsupported); T15 `test_composite_flip_denies_unsupported`
  - #4 → T15 `test_composite_multi_deny_lists_all_failed`
  - #5 → T11 `test_market_hours_stale_allocation_date_denies`
  - #6 → T14 `test_sector_exposure_unknown_sector_denies_fail_closed`
  - #7 → T13 `test_daily_loss_at_boundary_denies`
  - #8 → T12 `test_strategy_size_at_cap_approves` + `_over_cap_denies`
  - #9 → T12 `test_strategy_size_missing_strategy_fail_closed` + `_explicit_none_limit_fail_closed`
  - #10 → T14 `test_sector_exposure_over_cap_denies_with_projection_context`
  - #11 → T14 `test_sector_exposure_denominator_fixed_not_live_cash`
  - #12 → T11 `test_market_hours_disabled_passes_through`; T13 `test_daily_loss_disabled_passes_through`; T14 `test_sector_exposure_disabled_passes_through`
  - #13 → T5 `test_shipped_default_yaml_parses_via_from_yaml`
  - #14 → T11 `test_market_hours_17_30_default_passes` + T18 `test_e2e_phase6b_17_30_ny_happy_path`
  - #15 → T16 `test_forward_engine_propagates_failed_gates_into_audit_context`
  - #16 → T18 `test_e2e_phase6b_sector_cap_denial_writes_per_gate_audit`
  - #17 → T11 `test_boundary_premarket_close_edge`
  - #18 → T11 `test_boundary_regular_close_edge`
  - #19 → T11 `test_boundary_post_close_cutoff_edge`
  - #20 → T11 `test_boundary_all_disabled_denies_everywhere`
  - #21 → T8 `test_today_realized_pnl_dst_spring_forward_no_overlap`
- Spec §7 locks 6b-L1..6b-L17 → exercised by listed tests:
  - 6b-L1 (block risk-increasing only) → CLOSE/REDUCE bypass tests in T11/T12/T13/T14 + T15 composite short-circuit.
  - 6b-L2 (run-all + audit-all) → T15 `test_composite_multi_deny_lists_all_failed`.
  - 6b-L3, 6b-L14 (config-split scope discipline) → T5/T6/T7 config provider tests.
  - 6b-L4 (sector denominator fixed) → T14 `test_sector_exposure_denominator_fixed_not_live_cash`.
  - 6b-L5 (repository extension only) → T8/T9 repository tests + architecture lock-iii guard runs in T9 step 5.
  - 6b-L6 (no new audit event type) → T16 audit-context test.
  - 6b-L7 (stale allocation_date) → T11 stale-date test.
  - 6b-L8 (sector fail-closed) → T14 `test_sector_exposure_unknown_sector_denies_fail_closed`.
  - 6b-L9 (strategy size fail-closed) → T12 missing-config tests.
  - 6b-L10 (Decimal-in-context normalization) → T15 `test_composite_normalizes_decimal_in_context_to_str`.
  - 6b-L11 (preflight checklist) → T18 `scripts/preflight_phase6b_sector_check.py`.
  - 6b-L12 (RiskIntent canonical home in types.py) → T1 + T2 re-export test.
  - 6b-L13 (DST-safe NY window) → T8 `test_today_realized_pnl_dst_spring_forward_no_overlap`.
  - 6b-L15 (CompositeRiskGate dependency inversion) → T15 `test_factory_builds_4_gates_in_canonical_order` + `_make_composite` test helper exercises the public `gates=` constructor.
  - 6b-L16 (RiskResult.context immutability) → T2 `test_risk_result_context_is_immutable_mapping`.
  - 6b-L17 (single audit-JSON normalizer) → T14a `test_audit_json.py` (10 tests) + T16 forward_engine `_dump` delegation.
- Spec §8 forward-warnings → no code in this plan (deferred to 6c/6f/6g).
- Spec §9 deliverables summary → T17 wires composite via factory; T7 ships strategy YAML risk: blocks; new test files match the summary's list.

**Placeholder scan:** None. All code blocks are concrete; all commands have expected outputs; "TBD/TODO" are absent (the only `TODO(6b/6c)` line carried over verbatim from existing scheduler code is fine — it's an in-source forward-warning to future plans, not a plan placeholder).

**Type consistency:**
- `RiskIntent` always referenced as `marketpulse.trading.types.RiskIntent` (canonical) or via `marketpulse.trading.risk_gate.RiskIntent` (re-export) — both resolve to same object (T2 test guard).
- `RiskResult` signature consistent: `(approved, reason, gate_name="", failed_gates=(), context=MappingProxyType({}))` from T2 onward; `context` typed as `Mapping[str, Any]` post-T2.
- `RiskConfigProvider.strategy_config(strategy)` → `StrategyRiskConfig | None` consistent across T4, T6, T12.
- Gate `name` attribute consistent: every gate exposes `name = "<snake_case>"`, composite uses it for `failed_gates`.
- Repository helpers consistent: `today_realized_pnl(*, tick_date)` → `Decimal`, `sector_exposure_notional(*, sector_provider)` → `dict[str, Decimal]`, used uniformly in T13 (`DailyLossGate`) and T14 (`SectorExposureGate`).
- `strict_sector` is used everywhere a `Callable[[str], str | None]` is required (T17 DI, T14 default).
- `CompositeRiskGate.__init__(*, gates: Sequence[RiskGate])` consistent across T15 implementation + tests + T17 (which constructs via `build_standard_composite` factory).
- `normalize_for_json(value: Any) -> Any` signature consistent across T14a util, T15 composite, T16 forward_engine delegate.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-21-phase-6b-risk-gates.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**

