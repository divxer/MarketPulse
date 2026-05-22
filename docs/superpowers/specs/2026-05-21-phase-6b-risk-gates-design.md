# Phase 6b — Risk Gates: Design

**Status:** Brainstorm complete · ready for implementation plan
**Author:** brainstorm 2026-05-21
**Spec-type:** sub-project (second concrete spec under the Phase 6 umbrella)
**Umbrella:** `docs/superpowers/specs/2026-05-21-phase-6-umbrella-design.md`
**6a Spec:** `docs/superpowers/specs/2026-05-21-phase-6a-paper-trading-foundation-design.md`
**Scope:** Replace `AlwaysApproveRiskGate` stub with real pre-trade gates. Hard deterministic execution safety only.

---

## 1 — Goal & Boundary

### Goal

Replace `marketpulse/trading/risk_gate.py`'s `AlwaysApproveRiskGate` stub at the `ForwardExecutionEngine` DI seam with a `CompositeRiskGate` running four deterministic, explainable, audit-traced pre-trade gates. After 6b ships, paper trading has real production-grade execution safety — a daily-loss hard stop, market-hours guard, per-strategy size cap, and sector-concentration cap.

### Core principle (locked)

**"Block risk-increasing actions only. Never block risk-reducing actions."**

This applies to all 4 gates: `OPEN` and `ADD` intents are checked; `CLOSE` and `REDUCE` intents bypass every gate. The system never lets a hard stop trap risk by blocking flatten / forced-exit operations. `FLIP` is denied as `unsupported_risk_intent` in 6b; Phase 7 (broker integration) will wire FLIP semantics with proper net-delta handling.

### 6b is deterministic execution safety, NOT portfolio intelligence

6b is intentionally narrow: O(1) or simple aggregation, no statistics, no rolling windows, no covariance matrices, no portfolio optimization, no live admin tuning, no hot-reload. If a gate proposal requires any of those, it belongs in 6c (or Phase 7), not here.

### What 6b explicitly does NOT include

- **No DrawdownHaltGate** — needs canonical equity timeline + MtM engine. Land after a daily NAV snapshot job exists.
- **No CorrelationCapGate** — needs covariance machinery + rolling history. That's allocator intelligence, not execution safety.
- **No new audit event type** — reuse `ORDER_REJECTED` with extended `context`. Overrides the umbrella's speculative "extends CHECK constraint to include RISK_GATE_BLOCKED" language; zero migration in 6b.
- **No DB-backed risk config** — YAML files only.
- **No env-var-first config** — env vars don't scale to per-strategy parameters.
- **No mid-flight `tick()` gate enforcement** — gates run on `place_order` only. `_materialize_entry` and `_materialize_exit` are engine-internal and bypass gates entirely (otherwise `daily_loss > limit` would trap unrelated horizon exits).

### Anti-goals

- ❌ Block any risk-reducing order (CLOSE / REDUCE).
- ❌ Use live cash/equity in sector-cap denominator (introduces intraday drift).
- ❌ Bucket unknown sectors to "UNKNOWN" and allow (fail-closed instead).
- ❌ Default missing strategy risk config to infinite size cap.
- ❌ Allow stale `allocation_date` to pass `MarketHoursGate`.

---

## 2 — Architecture

### Module layout

```
marketpulse/trading/risk_gates/                (NEW package)
├── __init__.py                                re-exports CompositeRiskGate,
│                                              RiskConfigProvider, the 4 gate
│                                              classes
├── composite.py                               CompositeRiskGate
├── market_hours.py                            MarketHoursGate
├── strategy_size.py                           StrategySizeGate
├── daily_loss.py                              DailyLossGate
├── sector_exposure.py                         SectorExposureGate
└── config_provider.py                         RiskConfigProvider + dataclasses
                                               (RiskGateConfig,
                                                MarketHoursConfig,
                                                DailyLossConfig,
                                                SectorExposureConfig,
                                                StrategyRiskConfig)

marketpulse/trading/risk_gate.py               (MODIFIED — backward compat)
                                               Extends RiskResult with
                                               failed_gates + context.
                                               Re-exports symbols from
                                               risk_gates/ package and
                                               re-exports RiskIntent from
                                               types.py (back-compat alias).

marketpulse/trading/types.py                   (MODIFIED)
                                               Adds RiskIntent enum (canonical
                                               home — keeps OrderRequest's type
                                               dep upward, never sideways).
                                               OrderRequest gains
                                               risk_intent: RiskIntent = OPEN

marketpulse/trading/repository.py              (MODIFIED — extension only)
                                               Adds today_realized_pnl(),
                                               sector_exposure_notional()

marketpulse/scheduler/paper_trading_tick.py    (MODIFIED — DI swap)
                                               AlwaysApproveRiskGate
                                               → CompositeRiskGate

marketpulse/strategies/loader.py               (UNCHANGED in 6b)
                                               The Strategy dataclass does NOT
                                               grow a `risk:` field. Instead,
                                               RiskConfigProvider reads the
                                               risk: block directly from each
                                               strategy YAML during from_yaml.
                                               This keeps strategy execution
                                               concerns separate from risk
                                               governance concerns (lock 6b-L3).

config/risk_gates.yaml                         (NEW) portfolio governance
config/strategies/*.yaml                       (MODIFIED) add risk: block
```

### `RiskIntent` semantics

```python
# marketpulse/trading/types.py  (canonical home — see lock 6b-L12)
from enum import StrEnum

class RiskIntent(StrEnum):
    OPEN = "open"        # NEW position; gates run
    ADD = "add"          # increase existing position; gates run
    CLOSE = "close"      # full exit; gates bypassed
    REDUCE = "reduce"    # partial exit; gates bypassed
    FLIP = "flip"        # 6b denies; Phase 7 wires properly
```

`RiskIntent` lives in `types.py` (NOT in `risk_gate.py`) because `OrderRequest` (defined in `types.py`) carries `risk_intent: RiskIntent` as a field. If `RiskIntent` lived in `risk_gate.py`, `types.py` would have to import from `risk_gate.py`, inverting the dependency layer that 6a established (types is a leaf module — nothing above it should import down into it). `risk_gate.py` re-exports `RiskIntent` for back-compat callers but the canonical home is `types.py` (lock 6b-L12).

Phase 6 paper trading currently emits only `OPEN` (Phase 4-5 pattern: enter at event, exit at horizon). Field exists for forward-compat; only OPEN exercised by 6b production paths.

### `RiskResult` (extended, 6a-compat)

```python
@dataclass(frozen=True)
class RiskResult:
    approved: bool
    reason: str
    gate_name: str = ""                          # 6a-compat
    failed_gates: tuple[str, ...] = ()           # NEW in 6b
    context: dict[str, Any] = field(default_factory=dict)  # NEW in 6b
```

6a callers (`ForwardExecutionEngine.place_order`) read `approved`/`reason`/`gate_name` unchanged. 6b adds richer fields without breaking the contract.

### Data flow

```
ForwardExecutionEngine.place_order(order_request)
    │
    ├── idempotency check               (6a, lock xxx)
    ├── kill switch check                (6a, lock 6a-L8)
    │
    ├── if order_request.risk_intent in (CLOSE, REDUCE):
    │       skip CompositeRiskGate entirely → allow
    │
    ├── if order_request.risk_intent == FLIP:
    │       composite returns deny("unsupported_risk_intent")
    │       → ORDER_REJECTED audit; raise OrderRejected
    │
    ├── CompositeRiskGate.check_pre_trade(order_request)
    │       │
    │       all_results = []
    │       for gate in [market_hours, strategy_size, daily_loss, sector_exposure]:
    │           try:
    │               r = gate.check_pre_trade(order_request)
    │           except Exception as e:
    │               r = RiskResult(approved=False,
    │                              reason=f"{gate.name}_error",
    │                              gate_name=gate.name,
    │                              context={"error_type": ..., "error": ...})
    │           all_results.append(r)
    │       failed = [r for r in all_results if not r.approved]
    │       if failed:
    │           return RiskResult(
    │               approved=False,
    │               reason="; ".join(r.reason for r in failed),
    │               gate_name=failed[0].gate_name,
    │               failed_gates=tuple(r.gate_name for r in failed),
    │               context={"per_gate": [asdict(r) for r in all_results]},
    │           )
    │       return RiskResult(approved=True, gate_name="composite", reason="")
    │
    └── on deny:
        ORDER_REJECTED audit (6a path) with:
            reason            = risk_result.reason
            context.gate       = risk_result.gate_name      (6a-compat)
            context.failed_gates = risk_result.failed_gates  (6b new)
            context.per_gate     = risk_result.context["per_gate"]  (6b new)
        raise OrderRejected(risk_result.reason)
```

### Audit event type — reuse `ORDER_REJECTED` (lock 6b-L6)

The umbrella spec speculated about adding a `RISK_GATE_BLOCKED` event type; 6b overrides that decision. Reusing `ORDER_REJECTED` with extended context keeps zero migration overhead and matches the 6a pattern already established for kill-switch and risk denials.

**Decimal-in-context normalization (required):** every gate's `RiskResult.context` may contain `Decimal` values (e.g., `daily_loss_limit`, `sector_cap`, `projected_notional`). Before persisting to `paper_audit_event.context` (JSON column), the audit writer MUST normalize all `Decimal` values to strings via the existing 6a pattern (`_dump` helper in `forward_engine.py`). Direct `json.dumps(asdict(result))` will either crash or emit non-deterministic floats — both unacceptable for an append-only audit ledger. The implementer applies `_dump`-equivalent recursion to nested `context.per_gate[*].context` dicts.

Operators can query risk-gate denials with:

```sql
SELECT * FROM paper_audit_event
WHERE event_type = 'ORDER_REJECTED'
  AND json_extract(context, '$.failed_gates') IS NOT NULL
```

### DI seam

```python
# marketpulse/scheduler/paper_trading_tick.py (modified)
from marketpulse.trading.risk_gates import (
    CompositeRiskGate, RiskConfigProvider,
)

config_provider = RiskConfigProvider.from_yaml(
    global_path=Path("config/risk_gates.yaml"),
    strategies_dir=Path("config/strategies/"),
)
risk_gate = CompositeRiskGate(
    config_provider=config_provider,
    repository=repository,
    calendar=calendar,
    clock=clock,
    sector_provider=get_sector,
)
engine = ForwardExecutionEngine(
    repository=repository, clock=clock,
    kill_switch=kill_switch, risk_gate=risk_gate,
)
```

**`CompositeRiskGate.__init__` construction model:** the composite builds its 4 child gates *internally* from the injected dependencies (config_provider, repository, calendar, clock, sector_provider). Callers do not pass pre-built gates — that's an over-flexibility trap; the gate identities + order are locked by 6b-L2. Tests substitute behavior by injecting fakes for the underlying dependencies (FakeClock, in-memory Repository, stub sector_provider) rather than overriding child gates.

**Kill-switch is OUTSIDE the RiskGate principle scope (clarification):** the core principle "Never block risk-reducing actions" applies *within* the `CompositeRiskGate` layer only. `KillSwitch` is a separate emergency global halt that lives in `marketpulse/trading/kill_switch.py`, NOT inside the `risk_gates/` package, and intentionally falls outside this principle. `ForwardExecutionEngine.place_order` checks the kill switch BEFORE invoking `CompositeRiskGate` (6a-L8 defense-in-depth) — so an active kill switch denies ALL orders, including `CLOSE`/`REDUCE` intents that the RiskGate layer would otherwise bypass. This is intentional catastrophic safety: operators flip the kill switch when they want a complete halt; reducing positions during a halt requires lifting the kill switch first. If a use case for "kill switch active but allow forced flatten" emerges, it lands in Phase 7 broker work, not 6b.

Tests continue using `AlwaysApproveRiskGate` where the composite isn't the unit-under-test (existing 6a test fixtures unchanged).

---

## 3 — Gate Semantics

### MarketHoursGate

```python
def check_pre_trade(self, *, order_request):
    if order_request.risk_intent in (CLOSE, REDUCE):
        return RiskResult(approved=True, gate_name="market_hours", reason="")
    if order_request.risk_intent == FLIP:
        return RiskResult(approved=False, gate_name="market_hours",
                          reason="unsupported_risk_intent")
    cfg = self._cfg
    if not cfg.enabled:
        return RiskResult(approved=True, gate_name="market_hours", reason="")
    # Stale-allocation-date guard (6b-L7).
    today_session = self._calendar.today_ny_trading_date(self._clock.now())
    if order_request.allocation_date != today_session:
        return RiskResult(approved=False, gate_name="market_hours",
                          reason="stale_allocation_date",
                          context={
                              "allocation_date": order_request.allocation_date.isoformat(),
                              "today_session": today_session.isoformat(),
                          })
    if not self._calendar.is_business_day(order_request.allocation_date):
        return RiskResult(approved=False, gate_name="market_hours",
                          reason="not_a_session_day",
                          context={"allocation_date": order_request.allocation_date.isoformat()})
    now_ny = self._clock.now().astimezone(NY)
    if not self._window_check(now_ny.time(), cfg):
        return RiskResult(approved=False, gate_name="market_hours",
                          reason="outside_placement_window",
                          context={"now_ny": now_ny.isoformat()})
    return RiskResult(approved=True, gate_name="market_hours", reason="")
```

`_window_check` algorithm (explicit, ordered to avoid ambiguity at window boundaries):

```python
def _window_check(t: time, cfg: MarketHoursConfig) -> bool:
    """Returns True iff t falls within any enabled NY-time window.
    Boundaries: regular session is INCLUSIVE on both ends [09:30, 16:00];
    post-close is EXCLUSIVE on left, INCLUSIVE on right (16:00, until];
    premarket is INCLUSIVE [04:00, 09:30). If all flags False → returns
    False (no valid placement window)."""
    if cfg.allow_premarket and time(4, 0) <= t < time(9, 30):
        return True
    if cfg.allow_regular_session and time(9, 30) <= t <= time(16, 0):
        return True
    if cfg.allow_post_close and time(16, 0) < t <= cfg.post_close_until:
        return True
    return False
```

### StrategySizeGate

```python
def check_pre_trade(self, *, order_request):
    if order_request.risk_intent in (CLOSE, REDUCE):
        return RiskResult(approved=True, gate_name="strategy_size", reason="")
    if order_request.risk_intent == FLIP:
        return RiskResult(approved=False, gate_name="strategy_size",
                          reason="unsupported_risk_intent")
    # Missing-strategy-risk-config: fail-closed (6b-L9).
    cfg = self._provider.strategy_config(order_request.strategy)
    if cfg is None or cfg.max_position_notional is None:
        return RiskResult(approved=False, gate_name="strategy_size",
                          reason="missing_strategy_risk_config",
                          context={"strategy": order_request.strategy})
    proposed = order_request.event_price * Decimal(order_request.quantity)
    if proposed > cfg.max_position_notional:
        return RiskResult(approved=False, gate_name="strategy_size",
                          reason="strategy_size_exceeded",
                          context={
                              "strategy": order_request.strategy,
                              "proposed": str(proposed),
                              "limit": str(cfg.max_position_notional),
                          })
    return RiskResult(approved=True, gate_name="strategy_size", reason="")
```

### DailyLossGate

```python
def check_pre_trade(self, *, order_request):
    if order_request.risk_intent in (CLOSE, REDUCE):
        return RiskResult(approved=True, gate_name="daily_loss", reason="")
    if order_request.risk_intent == FLIP:
        return RiskResult(approved=False, gate_name="daily_loss",
                          reason="unsupported_risk_intent")
    cfg = self._cfg
    if not cfg.enabled:
        return RiskResult(approved=True, gate_name="daily_loss", reason="")
    realized = self._repo.today_realized_pnl(
        tick_date=order_request.allocation_date,
    )
    limit = cfg.daily_loss_limit  # positive Decimal
    if realized <= -limit:        # boundary inclusive on deny side
        return RiskResult(approved=False, gate_name="daily_loss",
                          reason="daily_loss_limit_exceeded",
                          context={
                              "today_realized_pnl": str(realized),
                              "daily_loss_limit": str(limit),
                              "allocation_date": order_request.allocation_date.isoformat(),
                          })
    return RiskResult(approved=True, gate_name="daily_loss", reason="")
```

### SectorExposureGate

```python
def check_pre_trade(self, *, order_request):
    if order_request.risk_intent in (CLOSE, REDUCE):
        return RiskResult(approved=True, gate_name="sector_exposure", reason="")
    if order_request.risk_intent == FLIP:
        return RiskResult(approved=False, gate_name="sector_exposure",
                          reason="unsupported_risk_intent")
    cfg = self._cfg
    if not cfg.enabled:
        return RiskResult(approved=True, gate_name="sector_exposure", reason="")
    # Unknown-sector fail-closed (6b-L8).
    proposed_sector = self._sector_provider(order_request.ticker)
    if proposed_sector is None:
        return RiskResult(approved=False, gate_name="sector_exposure",
                          reason="unknown_sector",
                          context={"ticker": order_request.ticker})
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
        return RiskResult(approved=False, gate_name="sector_exposure",
                          reason="sector_cap_exceeded",
                          context={
                              "sector": proposed_sector,
                              "current": str(current),
                              "proposed": str(proposed),
                              "projected": str(projected),
                              "cap": str(cap_dollars),
                          })
    return RiskResult(approved=True, gate_name="sector_exposure", reason="")
```

### Repository extensions

```python
# marketpulse/trading/repository.py (appended)

def today_realized_pnl(self, *, tick_date: date) -> Decimal:
    """Sum of paper_fill.realized_pnl where side='EXIT' and the fill's
    NY-day equals tick_date. Returns Decimal(0) if no rows.

    DST-safe NY-day window (lock 6b-L13): build both bounds as NY-local
    midnight and convert each to UTC independently. The naïve approach
    `ny_start + timedelta(days=1)` adds 24 wall-clock hours in UTC, which
    is off by 1 hour on the DST transition days (spring/fall). Building
    both bounds in NY-local time first guarantees a true 23/24/25-hour
    NY-day window."""
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

def sector_exposure_notional(
    self, *, sector_provider: Callable[[str], str | None],
) -> dict[str, Decimal]:
    """OPEN paper_position rows grouped by sector. Notional per position
    = quantity * entry_price (Phase 6 doesn't MtM). Tickers whose sector
    is None are EXCLUDED from the result. Returns {sector: total}."""
    rows = self._session.execute(
        select(PaperPosition).where(PaperPosition.status == "OPEN")
    ).scalars().all()
    out: dict[str, Decimal] = {}
    for p in rows:
        sector = sector_provider(p.ticker)
        if sector is None:
            continue  # unknown-sector OPEN positions excluded from buckets;
                      # SectorExposureGate independently denies new orders
                      # with unknown sector via fail-closed check
        out[sector] = out.get(sector, Decimal(0)) + (
            Decimal(p.quantity) * p.entry_price
        )
    return out
```

---

## 4 — Configuration

### `config/risk_gates.yaml` (new)

```yaml
# Phase 6b portfolio-level governance. Strategy-local knobs live in
# config/strategies/*.yaml under the `risk:` block.

market_hours:
  enabled: true
  exchange: XNYS
  allow_regular_session: true     # 09:30-16:00 NY
  allow_post_close: true          # 16:00-post_close_until NY
  post_close_until: "18:00"       # placement window cutoff (NY tz)
  allow_premarket: false          # 04:00-09:30 NY

daily_loss:
  enabled: true
  daily_loss_limit: 500           # absolute USD; deny when
                                  # today_realized_pnl <= -daily_loss_limit

sector_exposure:
  enabled: true
  max_sector_exposure_pct: 0.35   # of configured_max_capital_in_use
  configured_max_capital_in_use: 10000   # FIXED denominator (lock 6b-L4) —
                                         # NOT live cash/equity
```

### `config/strategies/<strategy>.yaml` (extended)

```yaml
# Existing strategy fields … plus:
risk:
  max_position_notional: 25000    # USD; deny if event_price * quantity > this
```

### `RiskConfigProvider`

```python
# marketpulse/trading/risk_gates/config_provider.py

@dataclass(frozen=True)
class MarketHoursConfig:
    enabled: bool
    exchange: str
    allow_regular_session: bool
    allow_post_close: bool
    post_close_until: time          # parsed from "HH:MM"
    allow_premarket: bool

@dataclass(frozen=True)
class DailyLossConfig:
    enabled: bool
    daily_loss_limit: Decimal

@dataclass(frozen=True)
class SectorExposureConfig:
    enabled: bool
    max_sector_exposure_pct: float
    configured_max_capital_in_use: Decimal

@dataclass(frozen=True)
class RiskGateConfig:
    market_hours: MarketHoursConfig
    daily_loss: DailyLossConfig
    sector_exposure: SectorExposureConfig

@dataclass(frozen=True)
class StrategyRiskConfig:
    max_position_notional: Decimal | None  # None → StrategySizeGate fail-closed

class RiskConfigProvider:
    """Single parser. Gates never read YAML directly (lock 6b-L3)."""

    def __init__(self, *, global_cfg: RiskGateConfig,
                 strategy_cfgs: dict[str, StrategyRiskConfig]) -> None: ...

    @classmethod
    def from_yaml(cls, *, global_path: Path, strategies_dir: Path,
    ) -> "RiskConfigProvider": ...

    def global_config(self) -> RiskGateConfig: ...

    def strategy_config(self, strategy: str) -> StrategyRiskConfig | None:
        """Returns None when strategy has no `risk:` block. Triggers
        StrategySizeGate fail-closed (6b-L9)."""
```

---

## 5 — Sub-task Decomposition

| Sub-task | Scope | Tests at sub-task boundary |
|---|---|---|
| **6b-0** | `RiskConfigProvider` + 5 frozen dataclasses + YAML schema validation. Ships default `config/risk_gates.yaml`. Extends strategy YAML loader to parse `risk:` block. Adds `RiskIntent` enum + `OrderRequest.risk_intent: RiskIntent = OPEN`. | Config-parse tests, schema validation, OrderRequest default OPEN, strategy YAML round-trip, missing `risk:` block → `strategy_config()` returns None |
| **6b-1** | Extends `repository.py`: `today_realized_pnl(tick_date)` + `sector_exposure_notional(sector_provider)`. | Per-helper tests with seeded `paper_fill` + `paper_position` fixtures. Edge cases: no fills today, multiple sectors, tickers with `sector_provider(t) → None` excluded, prior-day fills excluded |
| **6b-2** | Implements the 4 gates: `MarketHoursGate`, `StrategySizeGate`, `DailyLossGate`, `SectorExposureGate`. Each gate ≈ one file, one method. Applies `RiskIntent` bypass for CLOSE/REDUCE; FLIP denies. | Per-gate unit tests: enabled/disabled, CLOSE/REDUCE bypass, FLIP deny, exact-boundary cases (today_realized_pnl == -limit, proposed_notional == max), config-parse via provider |
| **6b-3** | `CompositeRiskGate` (run-all + deny-if-any + exception=deny + audit-all). Extended `RiskResult`. Audit context routes `failed_gates` + `per_gate` into existing `ORDER_REJECTED` event. | Composite tests: all-approve, one-deny, multi-deny, exception-from-one-gate, audit context shape |
| **6b-4** | DI seam: `paper_trading_tick.py` swaps `AlwaysApproveRiskGate()` → `CompositeRiskGate(...)`. Updates `test_scheduler.py` and `test_e2e_stateful.py` for composite. Full smoke + ruff + alembic + route smoke. **Includes explicit 17:30 NY post-close E2E** confirming Phase 6a's default tick passes MarketHoursGate (lock-iv compat check). | E2E: happy-path order through 4-gate composite; deny case → `ORDER_REJECTED` with `failed_gates`; 17:30 NY happy path; stale-allocation-date deny; unknown-sector deny |

5 sub-tasks. Branch `plan/phase-6b-risk-gates` off main. Single PR at end of 6b-4.

---

## 6 — Operational Test Map

| # | Category | Scenario | Lock |
|---|---|---|---|
| 0 | Composite happy path | all 4 gates approve → `CompositeRiskGate` returns `RiskResult(approved=True, gate_name="composite", failed_gates=())`; `ORDER_PLACED` audit written; `paper_order` row created. Unit-level guard before E2E `#14`. | 6b-L2 |
| 1 | Fail-closed exception | gate raises arbitrary `RuntimeError` → `CompositeRiskGate` denies with `<gate>_error`; `ORDER_REJECTED` audit written; no `paper_order` row | iv, ix, 6a-L3 |
| 2 | RiskIntent CLOSE/REDUCE bypass | `order_request.risk_intent == CLOSE` → all 4 gates return approve immediately (no DB reads, no clock reads) | 6b-L1 |
| 3 | RiskIntent FLIP | `order_request.risk_intent == FLIP` → composite returns `unsupported_risk_intent` deny | 6b-L1 |
| 4 | Run-all not fail-fast | 3 gates deny → audit `context.per_gate` lists ALL 4 results | 6b-L2 |
| 5 | Stale allocation_date | `allocation_date < today` → `MarketHoursGate` denies `stale_allocation_date` | 6b-L7 |
| 6 | Unknown sector | `sector_provider(ticker)` returns `None` → `SectorExposureGate` denies `unknown_sector` | 6b-L8 |
| 7 | DailyLoss boundary | `today_realized_pnl == -daily_loss_limit` exactly → `DailyLossGate` denies | (Section 1 fix #2) |
| 8 | StrategySize boundary | `proposed == max_position_notional` exactly → `StrategySizeGate` approves (deny only on `>`) | strict-greater semantic |
| 9 | Missing strategy risk config | `RiskConfigProvider.strategy_config(s)` returns None or `max_position_notional` None → `StrategySizeGate` denies `missing_strategy_risk_config` | 6b-L9 |
| 10 | Sector cap projected | proposed pushes sector over cap → deny with `context.{current, proposed, projected, cap}` | sector projection contract |
| 11 | Sector cap denominator | live cash changes during day → sector cap result unchanged (denominator fixed in YAML) | 6b-L4 |
| 12 | Disabled gate | `daily_loss.enabled = false` → `DailyLossGate` auto-approves regardless of PnL | per-gate enable flag |
| 13 | Config YAML round-trip | YAML → `RiskGateConfig` → re-serialize → equal | RiskConfigProvider correctness |
| 14 | 17:30 NY happy path | Phase 6a default tick fires at 17:30 NY → `MarketHoursGate` passes (post_close_until=18:00) | (Section 4 fix) |
| 15 | Audit reuse | `ORDER_REJECTED` event_type, `context.failed_gates` populated, `context.per_gate` lists all 4 results | 6b-L6 |
| 16 | Integration smoke | E2E with all 4 gates active; happy-path order; denied (sector cap exceeded); fresh-session assertion that audit was committed before raise | end-to-end |
| 17 | MarketHours boundary — premarket close edge | premarket disabled, 09:29:59 NY → deny `outside_placement_window`; 09:30:00 NY (regular session inclusive left) → approve | `_window_check` algorithm |
| 18 | MarketHours boundary — regular session close edge | 16:00:00 NY (regular session inclusive right) → approve; 16:00:01 NY (post-close inclusive left-open) → approve when `allow_post_close=true` | `_window_check` algorithm |
| 19 | MarketHours boundary — post-close cutoff edge | `post_close_until=18:00`, 18:00:00 NY → approve (inclusive right); 18:00:01 NY → deny `outside_placement_window` | `_window_check` algorithm |
| 20 | MarketHours boundary — all-disabled | all 3 flags false → every wall-time denies `outside_placement_window` | `_window_check` returns False |
| 21 | DailyLoss DST window | `tick_date` straddles a US DST transition (spring forward / fall back) — `today_realized_pnl` window covers exactly the right NY-day fills, not a fixed 24h UTC slice | 6b-L13 |

---

## 7 — Locks (6b-local)

| # | Lock |
|---|---|
| **6b-L1** | **Block risk-increasing actions only.** `CLOSE` / `REDUCE` bypass all gates. `OPEN` / `ADD` run gates. `FLIP` denied as `unsupported_risk_intent`. Phase 7 wires FLIP properly. |
| **6b-L2** | **`CompositeRiskGate` is run-all, deny-if-any, exception=deny, audit-all.** All gates execute; per-gate exceptions become per-gate denies; audit context lists every gate's result. Fail-fast is forbidden. |
| **6b-L3** | **Config split:** strategy YAML for strategy-local knobs (`max_position_notional`); `config/risk_gates.yaml` for portfolio governance. Gates read via `RiskConfigProvider`, NEVER directly. |
| **6b-L4** | **Sector cap denominator is configured constant**, NOT live cash/equity. Prevents intraday drift. |
| **6b-L5** | **Repository extension only** — new read helpers (`today_realized_pnl`, `sector_exposure_notional`) live in `repository.py`. NO `query_models.py` yet (defer to 6f). |
| **6b-L6** | **No new audit event type.** Reuse `ORDER_REJECTED` with `context.failed_gates: list[str]` + `context.per_gate: list[dict]`. Zero schema migration in 6b. Overrides the umbrella's speculative `RISK_GATE_BLOCKED` language. |
| **6b-L7** | **`MarketHoursGate` denies stale `allocation_date`.** `allocation_date != calendar.today_ny_trading_date(clock.now())` → deny `stale_allocation_date`. 6a-L7 same-day replay still works via `idempotency_key`. |
| **6b-L8** | **`SectorExposureGate` fail-closed on `proposed_sector is None`.** Never bucket to UNKNOWN-and-allow. `sector_exposure_notional()` also excludes unknown-sector OPEN positions from the buckets (they don't anchor a sector). |
| **6b-L9** | **`StrategySizeGate` fail-closed on missing strategy risk config.** When `RiskConfigProvider.strategy_config(s)` is None OR `max_position_notional` is None, deny `missing_strategy_risk_config`. No infinite-cap default. |
| **6b-L10** | **Decimal values in audit context MUST be string-normalized** before persistence (matches 6a's `_dump` pattern). Applies to nested `context.per_gate[*].context` as well. Prevents non-deterministic float serialization and Postgres-migration drift. |
| **6b-L11** | **Pre-existing OPEN positions with unknown sector are excluded from sector-cap accounting.** Operators MUST run sector mapping backfill (populate `config/sector_overrides.yaml`) before 6b production deploy, OR accept that pre-6b OPENs don't count toward the cap. 6b-L8 fail-closed prevents NEW unknown-sector positions from being placed; this lock covers the deployment transition. The implementation plan ships a **preflight deploy checklist** that enumerates every distinct ticker in `paper_position WHERE status='OPEN'`, runs `get_sector(t)` on each, and lists tickers with `None` result. Operators must add YAML overrides for that list (or explicitly accept) before flipping the DI seam. |
| **6b-L12** | **`RiskIntent` lives in `marketpulse/trading/types.py`** (NOT in `risk_gate.py`). `OrderRequest` carries `risk_intent: RiskIntent` as a field; placing the enum in `risk_gate.py` would invert the 6a-established dependency hierarchy (types is a leaf). `risk_gate.py` re-exports for back-compat callers but `from marketpulse.trading.types import RiskIntent` is the canonical import. |
| **6b-L13** | **DST-safe NY-day window** for `today_realized_pnl`. Both bounds are constructed as NY-local midnight (one for `tick_date`, one for `tick_date + 1`) and converted to UTC independently. The naïve `ny_start + timedelta(days=1)` adds 24 wall-clock UTC hours and is off-by-one-hour on DST transition days. Operational test #21 enforces. |
| **6b-L14** | **`RiskConfigProvider` scope discipline.** The provider parses ONLY the `risk:` block from each strategy YAML — never `signals:`, `sizing:`, or other strategy-execution blocks (those remain owned by `marketpulse/strategies/loader.py`). The strategy lookup key is the **YAML filename stem** (e.g., `momentum.yaml` → `"momentum"`), matching the Phase 3-T2 / Phase 3-T3 loader's identifier convention. Mismatch with the Phase 3 loader's naming = silent fail-closed for every order in the affected strategy — verify naming alignment in the implementation plan's 6b-0 acceptance tests. |

---

## 8 — Forward-warnings (6c / 6f / 6g)

### To 6c

- **DrawdownHaltGate** lands here, AFTER a daily NAV snapshot job exists. Requires canonical equity timeline from `paper_cash_ledger` + open-position MtM. Phase 6b's lock 6b-L1 (block risk-increasing only) applies — drawdown halt must allow exits.
- **CorrelationCapGate** lands here, AFTER Phase 5c's covariance machinery is wired into a Phase 6 read context. May actually belong in the allocator (`allocate_for_day` already has `compute_adjusted_bid_weight`), not the execution-safety layer.

### To 6f (UI)

- `/lab/paper-trading` surfaces per-gate denials per day. Query:
  ```sql
  -- SQLite syntax shown for illustration only. 6f MUST go through a
  -- typed wrapper in repository.py (or future query_models.py) that
  -- abstracts json_extract() — Phase 7 Postgres migration uses
  -- context::jsonb->'failed_gates' instead. No raw SQL in 6f route
  -- handlers (per 6a-2 round-6 R6-5 lock — wrapper-only JSON access).
  SELECT json_extract(context, '$.failed_gates') AS gates,
         json_extract(context, '$.per_gate') AS detail,
         strategy, reason, timestamp
  FROM paper_audit_event
  WHERE event_type = 'ORDER_REJECTED'
    AND json_extract(context, '$.failed_gates') IS NOT NULL
  ORDER BY timestamp DESC
  ```
- Gate-block heatmap (per gate, per day) is a natural 6f addition.

### To 6g (observability)

- Push notification on `ORDER_REJECTED` where `failed_gates` includes `daily_loss` → operator alert.
- Recap job tallies per-gate-block counts per day and surfaces them.
- Counter metrics: `gate_blocks_total{gate=daily_loss}`, etc.

---

## 9 — Deliverables Summary

Single PR `feat(phase-6b): risk gates`. Branches from main → `plan/phase-6b-risk-gates` → merge to main at end of 6b-4.

**New files (8):**
- `marketpulse/trading/risk_gates/__init__.py`
- `marketpulse/trading/risk_gates/composite.py`
- `marketpulse/trading/risk_gates/market_hours.py`
- `marketpulse/trading/risk_gates/strategy_size.py`
- `marketpulse/trading/risk_gates/daily_loss.py`
- `marketpulse/trading/risk_gates/sector_exposure.py`
- `marketpulse/trading/risk_gates/config_provider.py`
- `config/risk_gates.yaml`

**Modified files (5):**
- `marketpulse/trading/risk_gate.py` — extends `RiskResult` (adds `failed_gates`, `context`), re-exports `CompositeRiskGate` + child gates from the new package, and re-exports `RiskIntent` from `types.py` for back-compat callers
- `marketpulse/trading/types.py` — adds `RiskIntent` enum (canonical home — see lock 6b-L12); `OrderRequest.risk_intent: RiskIntent = OPEN`
- `marketpulse/trading/repository.py` — appends 2 read helpers
- `marketpulse/scheduler/paper_trading_tick.py` — wires `CompositeRiskGate`
- `config/strategies/*.yaml` — adds `risk:` block per strategy. Phase 3-T2 created 6 strategy files (`momentum.yaml`, `meanrev.yaml`, etc.); each needs a `risk: { max_position_notional: <N> }` block. Any strategy YAML shipped WITHOUT a `risk:` block will trigger `StrategySizeGate` fail-closed deny `missing_strategy_risk_config` (6b-L9) for all orders in that strategy — verify all 6 are updated before 6b deploy.

**New tests (7):**
- `tests/trading/risk_gates/__init__.py`
- `tests/trading/risk_gates/test_config_provider.py`
- `tests/trading/risk_gates/test_market_hours.py`
- `tests/trading/risk_gates/test_strategy_size.py`
- `tests/trading/risk_gates/test_daily_loss.py`
- `tests/trading/risk_gates/test_sector_exposure.py`
- `tests/trading/risk_gates/test_composite.py`

**Modified tests (3):**
- `tests/trading/test_forward_engine.py` — composite-aware
- `tests/trading/test_scheduler.py` — DI swap covered
- `tests/trading/test_e2e_stateful.py` — adds 17:30 NY happy path + composite deny scenarios

**Phase 6a regression contract:** all existing 1024+ tests continue to pass. 6a's `AlwaysApproveRiskGate` test fixtures stay usable (still exported by `risk_gate.py`). The DI swap is isolated to the production scheduler entrypoint; tests choose which gate to inject.

---

**End of 6b spec.**
