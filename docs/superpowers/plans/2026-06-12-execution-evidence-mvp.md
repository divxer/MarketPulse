# Execution Evidence MVP (Independent Pricing Audit) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One read-only CLI prints a pre-registered PASS/FAIL pricing audit: paper fills and
the daily NAV series re-priced against Tencent (independent vendor), answering "is the North
Star built on a price illusion?"

**Architecture:** Clone of the permutation-MVP shape — pure core (no DB/network) + thin DB
loaders + CLI that performs the Tencent fetches. Zero schema/persistence/UI/deps.

**Tech stack:** Python 3.12. Tests `uv run pytest`, lint `uv run ruff check`, `# Layer:` tags.

**Spec:** `docs/superpowers/specs/2026-06-12-execution-evidence-mvp-design.md` (locked).
**Branch:** `feat/execution-evidence-mvp` (created; spec committed on it).

Verified facts (do not rediscover):
- `PaperFill` (models.py:381): order_id, position_id, side, `price` Numeric(18,6), quantity,
  `filled_at` TZDateTime. **NO ticker column** — ticker comes via join to
  `PaperPosition.ticker` on position_id.
- Fill prices are close-on-date with lookback fallback
  (`marketpulse/trading/price_provider.py:close_on_date`) — same-day-close is the
  apples-to-apples primary comparison; audit mirrors the `<= date` last-available convention.
- `PaperNavSnapshot` (models.py:489): trading_date, cash_balance, holdings_mtm,
  portfolio_nav, spy_close, … . **NAV = cash_balance + holdings_mtm; SPY is NOT inside NAV**
  (it feeds index/excess). Drift gates apply to NAV only; SPY appears as per-day comparison
  columns and in adjustment_basis_analysis.
- Positions as-of a date: reuse the as-of query shape from
  `marketpulse/portfolio/snapshot_runner.py:_read_open_positions` (READ it; reuse, don't
  reinvent). Its items expose ticker + quantity via the `OpenPosition` type
  (`marketpulse/portfolio/north_star.py`) — verify exact attribute names there.
- `TencentClient.fetch_history(ticker, period="60d")` → `list[Bar]`
  (`marketpulse/data/tencent_client.py:110`; Bar = marketpulse.data.types.Bar with
  open/high/low/close). qfq-adjusted. 60d covers the paper era (starts 2026-05-29).
- NY trading date for a UTC `filled_at`: `filled_at.astimezone(NY).date()` with
  `from marketpulse.trading.calendar import NY`.
- CLI convention: mirror `marketpulse/cli/permutation_test.py` (argparse, `# Layer: cli`,
  manual `gen = session_scope(); db = next(gen)` driving). Network at CLI tier is allowed
  (finalize CLI precedent).
- `db_session` fixture: tests/conftest.py:66. tests/evaluation/ and tests/cli/ exist.
- Spec-locked: thresholds have NO CLI override flags; `vs_next_available_open` NEVER enters
  the verdict; anomaly > 200 bps ⇒ fills FAIL; one-leg-empty ⇒ SKIPPED; both-empty ⇒ error.

---

### Task 1: Pure core — `run_pricing_audit`

**Files:**
- Create: `marketpulse/evaluation/pricing_audit.py`
- Test: `tests/evaluation/test_pricing_audit.py`

- [ ] **Step 1: Write the failing tests**

```python
# Layer: unit
"""Independent pricing audit core (spec 2026-06-12). Pure — no DB/network."""
from __future__ import annotations

from datetime import date

import pytest

from marketpulse.evaluation.pricing_audit import (
    THRESHOLDS,
    AuditBar,
    FillInput,
    NavDayInput,
    PositionInput,
    run_pricing_audit,
)


def _bars(ticker_days: dict[str, dict[date, tuple[float, float]]]):
    """{ticker: {date: (open, close)}} -> {ticker: [AuditBar...]} sorted by date."""
    out = {}
    for t, days in ticker_days.items():
        out[t] = [
            AuditBar(date=d, open=o, close=c) for d, (o, c) in sorted(days.items())
        ]
    return out


D1, D2, D3 = date(2026, 6, 8), date(2026, 6, 9), date(2026, 6, 10)


def test_fill_bps_exact_and_pass():
    # paper 100.10 vs tencent close 100.00 -> +10 bps exactly.
    fills = [FillInput(fill_id=1, ticker="AAA", side="BUY", quantity=5,
                       price=100.10, trading_date=D1)]
    bars = _bars({"AAA": {D1: (99.0, 100.0), D2: (101.0, 102.0)}})
    r = run_pricing_audit(fills, [], bars)
    f = r.fills
    assert f.n == 1
    assert abs(f.vs_same_day_close.mean_abs_bps - 10.0) < 1e-9
    # next available open AFTER D1 is D2's open 101.0 -> (100.10-101)/101*1e4
    assert abs(f.vs_next_available_open.mean_abs_bps - abs((100.10 - 101.0) / 101.0 * 1e4)) < 1e-6
    assert f.anomalies == ()
    assert r.verdict.fills == "PASS"


def test_fill_anomaly_over_200bps_fails_leg():
    fills = [FillInput(fill_id=1, ticker="AAA", side="BUY", quantity=5,
                       price=103.0, trading_date=D1)]  # +300 bps vs 100.0
    bars = _bars({"AAA": {D1: (99.0, 100.0)}})
    r = run_pricing_audit(fills, [], bars)
    assert len(r.fills.anomalies) == 1
    assert r.verdict.fills == "FAIL"


def test_next_open_never_enters_verdict():
    # same-day-close error tiny; next-open error absurd -> still PASS.
    fills = [FillInput(fill_id=1, ticker="AAA", side="BUY", quantity=5,
                       price=100.001, trading_date=D1)]
    bars = _bars({"AAA": {D1: (99.0, 100.0), D2: (500.0, 500.0)}})
    r = run_pricing_audit(fills, [], bars)
    assert r.fills.vs_next_available_open.mean_abs_bps > 1000
    assert r.verdict.fills == "PASS"


def test_fill_lookback_uses_last_available_close():
    # No bar on D2 (fill date) -> previous close D1 used, mirroring the engine.
    fills = [FillInput(fill_id=1, ticker="AAA", side="BUY", quantity=5,
                       price=100.0, trading_date=D2)]
    bars = _bars({"AAA": {D1: (99.0, 100.0)}})
    r = run_pricing_audit(fills, [], bars)
    assert abs(r.fills.vs_same_day_close.mean_abs_bps - 0.0) < 1e-9


def test_nav_drift_exact_and_gates():
    # cash 1000 + 10 sh AAA @ tencent 101 = 2010 vs recorded 2000 -> +0.5% drift
    nav_days = [NavDayInput(
        trading_date=D1, cash_balance=1000.0, holdings_mtm=1000.0,
        portfolio_nav=2000.0, spy_close=500.0,
        positions=(PositionInput(ticker="AAA", quantity=10),),
    )]
    bars = _bars({"AAA": {D1: (100.0, 101.0)}, "SPY": {D1: (499.0, 500.0)}})
    r = run_pricing_audit([], nav_days, bars)
    n = r.nav
    assert abs(n.per_day[0].drift_pct - 0.5) < 1e-9
    assert abs(n.max_abs_drift_pct - 0.5) < 1e-9
    assert n.max_drift_date == D1
    # 0.5% == max threshold boundary: gate is <=, so PASS at exactly 0.50
    assert r.verdict.nav == ("FAIL" if THRESHOLDS.nav_mean_abs_drift_pct < 0.5 else r.verdict.nav)
    # mean gate: single day mean 0.5 > 0.10 -> nav FAIL
    assert r.verdict.nav == "FAIL"


def test_nav_signed_and_weighted_means():
    # Two days: +0.08% on small holdings day, -0.08% on big holdings day.
    nav_days = [
        NavDayInput(trading_date=D1, cash_balance=900.0, holdings_mtm=100.0,
                    portfolio_nav=1000.0, spy_close=500.0,
                    positions=(PositionInput("AAA", 1),)),
        NavDayInput(trading_date=D2, cash_balance=100.0, holdings_mtm=900.0,
                    portfolio_nav=1000.0, spy_close=500.0,
                    positions=(PositionInput("BBB", 9),)),
    ]
    bars = _bars({
        "AAA": {D1: (0, 100.8)},   # 1 sh: mtm 100.8 vs 100 -> nav 1000.8 -> +0.08%
        "BBB": {D2: (0, 99.911)},  # 9 sh: 899.2 vs 900 -> nav 999.2 -> -0.08%
        "SPY": {D1: (0, 500.0), D2: (0, 500.0)},
    })
    r = run_pricing_audit([], nav_days, bars)
    n = r.nav
    assert n.mean_abs_drift_pct == pytest.approx(0.08, abs=0.005)
    assert abs(n.mean_signed_drift_pct) < 0.01          # signs cancel
    # weighted by holdings_mtm: day2 dominates -> weighted ~0.08 still (both 0.08 abs)
    assert n.weighted_mean_abs_drift_pct == pytest.approx(0.08, abs=0.005)
    assert r.verdict.nav == "PASS"


def test_unpriceable_ticker_visible_not_silent():
    nav_days = [NavDayInput(trading_date=D1, cash_balance=0.0, holdings_mtm=1000.0,
                            portfolio_nav=1000.0, spy_close=500.0,
                            positions=(PositionInput("ZZZ", 10),))]
    bars = _bars({"SPY": {D1: (0, 500.0)}})  # ZZZ missing entirely
    r = run_pricing_audit([], nav_days, bars)
    assert "ZZZ" in r.nav.unpriceable_tickers
    # recorded value kept -> zero drift
    assert r.nav.per_day[0].drift_pct == pytest.approx(0.0)


def test_adjustment_basis_same_sign_ratio():
    # AAA tencent close consistently 1% above recorded-side closes used in fills.
    fills = [FillInput(fill_id=i, ticker="AAA", side="BUY", quantity=1,
                       price=99.0, trading_date=d)
             for i, d in enumerate((D1, D2, D3), start=1)]
    bars = _bars({"AAA": {D1: (0, 100.0), D2: (0, 100.0), D3: (0, 100.0)}})
    r = run_pricing_audit(fills, [], bars)
    row = next(a for a in r.adjustment_basis_analysis if a.ticker == "AAA")
    assert row.same_sign_ratio == 1.0
    assert row.mean_signed_bps == pytest.approx(-100.0, rel=0.01)


def test_one_leg_empty_skipped_and_both_empty_raises():
    bars = _bars({"AAA": {D1: (0, 100.0)}})
    r = run_pricing_audit(
        [FillInput(1, "AAA", "BUY", 1, 100.0, D1)], [], bars,
    )
    assert r.verdict.nav == "SKIPPED"
    assert r.verdict.overall == r.verdict.fills  # gates on remaining leg only
    with pytest.raises(ValueError):
        run_pricing_audit([], [], bars)
```

(`FillInput` positional order in the last test must match the dataclass definition — keep
`(fill_id, ticker, side, quantity, price, trading_date)`.)

- [ ] **Step 2: Run → FAIL** (ModuleNotFoundError).

- [ ] **Step 3: Implement `marketpulse/evaluation/pricing_audit.py`**

```python
"""Independent pricing audit — execution-trust chain first evidence (spec 2026-06-12).

NOT broker shadow: compares the yfinance/price_cache pricing chain against an
independent market-data vendor (Tencent qfq). Two legs from one bar set:
fills (coarse sanity check) and the daily NAV series (the main course).
Pure computation — no DB, no network here.

Pre-registered thresholds are module constants; there is deliberately NO way
to override them at runtime (a FAIL is a FAIL and becomes a finding).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

FILLS_CAVEAT = (
    "fills audit compares paper fills to same-day close, not an execution-time "
    "quote; it is a coarse sanity check, not a fill-quality verdict"
)
ADJUSTMENT_CAVEAT = (
    "tencent bars are qfq (forward-adjusted); yfinance is auto-adjusted - a "
    "consistent same-sign offset suggests adjustment-basis divergence, not bad data"
)


@dataclass(frozen=True)
class Thresholds:
    fills_mean_abs_bps: float = 25.0
    fills_p95_abs_bps: float = 100.0
    fills_anomaly_bps: float = 200.0   # 2 x p95 threshold (spec review fix)
    nav_mean_abs_drift_pct: float = 0.10
    nav_max_abs_drift_pct: float = 0.50


THRESHOLDS = Thresholds()  # pre-registered; no CLI override


@dataclass(frozen=True)
class AuditBar:
    date: date
    open: float
    close: float


@dataclass(frozen=True)
class FillInput:
    fill_id: int
    ticker: str
    side: str
    quantity: int
    price: float
    trading_date: date


@dataclass(frozen=True)
class PositionInput:
    ticker: str
    quantity: float


@dataclass(frozen=True)
class NavDayInput:
    trading_date: date
    cash_balance: float
    holdings_mtm: float
    portfolio_nav: float
    spy_close: float | None
    positions: tuple[PositionInput, ...]


# --- result dataclasses (all frozen) ---

@dataclass(frozen=True)
class BpsStats:
    mean_abs_bps: float
    p95_abs_bps: float


@dataclass(frozen=True)
class FillRow:
    fill_id: int
    ticker: str
    trading_date: date
    side: str
    paper_price: float
    tencent_close: float | None
    bps_vs_close: float | None
    tencent_next_open: float | None
    bps_vs_next_open: float | None


@dataclass(frozen=True)
class FillsAudit:
    n: int
    n_unpriced: int
    vs_same_day_close: BpsStats | None
    vs_next_available_open: BpsStats | None
    anomalies: tuple[FillRow, ...]
    per_fill: tuple[FillRow, ...]


@dataclass(frozen=True)
class NavDayRow:
    trading_date: date
    nav_recorded: float
    nav_tencent: float
    drift_pct: float
    spy_close_recorded: float | None
    spy_close_tencent: float | None


@dataclass(frozen=True)
class NavAudit:
    days: int
    mean_abs_drift_pct: float
    max_abs_drift_pct: float
    max_drift_date: date
    weighted_mean_abs_drift_pct: float
    mean_signed_drift_pct: float
    per_day: tuple[NavDayRow, ...]
    unpriceable_tickers: tuple[str, ...]


@dataclass(frozen=True)
class AdjustmentBasisRow:
    ticker: str
    n_dates: int
    mean_signed_bps: float
    same_sign_ratio: float


@dataclass(frozen=True)
class Verdict:
    fills: str   # PASS | FAIL | SKIPPED
    nav: str
    overall: str


@dataclass(frozen=True)
class PricingAuditResult:
    thresholds: Thresholds
    fills: FillsAudit | None
    nav: NavAudit | None
    adjustment_basis_analysis: tuple[AdjustmentBasisRow, ...]
    verdict: Verdict
    caveats: tuple[str, ...] = (FILLS_CAVEAT, ADJUSTMENT_CAVEAT)


def _close_on_or_before(bars: list[AuditBar], d: date) -> AuditBar | None:
    """Last available bar <= d - mirrors the engine's lookback convention."""
    best = None
    for b in bars:
        if b.date <= d and (best is None or b.date > best.date):
            best = b
    return best


def _open_after(bars: list[AuditBar], d: date) -> AuditBar | None:
    best = None
    for b in bars:
        if b.date > d and (best is None or b.date < best.date):
            best = b
    return best


def _p95(values: list[float]) -> float:
    """Nearest-rank p95 over sorted absolute values (no numpy dependency)."""
    s = sorted(values)
    if not s:
        return 0.0
    k = max(0, min(len(s) - 1, int(round(0.95 * len(s) + 0.5)) - 1))
    return s[k]


def run_pricing_audit(
    fills: list[FillInput],
    nav_days: list[NavDayInput],
    bars_by_ticker: dict[str, list[AuditBar]],
    *,
    thresholds: Thresholds = THRESHOLDS,
) -> PricingAuditResult:
    if not fills and not nav_days:
        raise ValueError("nothing to audit: no fills and no NAV days")

    # collect signed bps per ticker for adjustment-basis analysis
    signed_by_ticker: dict[str, list[float]] = {}

    # --- fills leg ---
    fills_audit = None
    fills_verdict = "SKIPPED"
    if fills:
        rows: list[FillRow] = []
        close_bps: list[float] = []
        open_bps: list[float] = []
        anomalies: list[FillRow] = []
        n_unpriced = 0
        for f in sorted(fills, key=lambda x: (x.trading_date, x.fill_id)):
            t_bars = bars_by_ticker.get(f.ticker, [])
            cb = _close_on_or_before(t_bars, f.trading_date)
            ob = _open_after(t_bars, f.trading_date)
            bps_c = (
                (f.price - cb.close) / cb.close * 1e4 if cb and cb.close else None
            )
            bps_o = (
                (f.price - ob.open) / ob.open * 1e4 if ob and ob.open else None
            )
            row = FillRow(
                fill_id=f.fill_id, ticker=f.ticker, trading_date=f.trading_date,
                side=f.side, paper_price=f.price,
                tencent_close=cb.close if cb else None, bps_vs_close=bps_c,
                tencent_next_open=ob.open if ob else None, bps_vs_next_open=bps_o,
            )
            rows.append(row)
            if bps_c is None:
                n_unpriced += 1
            else:
                close_bps.append(abs(bps_c))
                signed_by_ticker.setdefault(f.ticker, []).append(bps_c)
                if abs(bps_c) > thresholds.fills_anomaly_bps:
                    anomalies.append(row)
            if bps_o is not None:
                open_bps.append(abs(bps_o))
        vs_close = (
            BpsStats(sum(close_bps) / len(close_bps), _p95(close_bps))
            if close_bps else None
        )
        vs_open = (
            BpsStats(sum(open_bps) / len(open_bps), _p95(open_bps))
            if open_bps else None
        )
        fills_audit = FillsAudit(
            n=len(rows), n_unpriced=n_unpriced,
            vs_same_day_close=vs_close, vs_next_available_open=vs_open,
            anomalies=tuple(anomalies), per_fill=tuple(rows),
        )
        # Verdict: ONLY vs_same_day_close + anomalies. next_open never gates.
        if vs_close is None:
            fills_verdict = "SKIPPED"
        elif (
            anomalies
            or vs_close.mean_abs_bps > thresholds.fills_mean_abs_bps
            or vs_close.p95_abs_bps > thresholds.fills_p95_abs_bps
        ):
            fills_verdict = "FAIL"
        else:
            fills_verdict = "PASS"

    # --- NAV leg ---
    nav_audit = None
    nav_verdict = "SKIPPED"
    if nav_days:
        day_rows: list[NavDayRow] = []
        unpriceable: set[str] = set()
        for nd in sorted(nav_days, key=lambda x: x.trading_date):
            mtm = 0.0
            for pos in nd.positions:
                cb = _close_on_or_before(bars_by_ticker.get(pos.ticker, []), nd.trading_date)
                if cb is None:
                    unpriceable.add(pos.ticker)
                    # recorded value kept: approximate this position's recorded
                    # share by recomputing total from recorded holdings minus
                    # nothing - simplest honest rule: add its recorded-implied
                    # value via holdings_mtm proportional fallback is NOT
                    # possible per-position; spec rule = keep recorded value,
                    # implemented as: drift contribution zero by adding the
                    # recorded per-position value. We only know totals, so:
                    mtm = None  # sentinel: see below
                    break
                mtm += pos.quantity * cb.close
            if mtm is None:
                # at least one unpriceable position -> keep recorded NAV whole
                nav_tencent = nd.cash_balance + nd.holdings_mtm
            else:
                nav_tencent = nd.cash_balance + mtm
            drift = (
                (nav_tencent - nd.portfolio_nav) / nd.portfolio_nav * 100.0
                if nd.portfolio_nav else 0.0
            )
            spy_cb = _close_on_or_before(bars_by_ticker.get("SPY", []), nd.trading_date)
            if spy_cb is not None and nd.spy_close:
                signed_by_ticker.setdefault("SPY", []).append(
                    (nd.spy_close - spy_cb.close) / spy_cb.close * 1e4,
                )
            day_rows.append(NavDayRow(
                trading_date=nd.trading_date,
                nav_recorded=nd.portfolio_nav, nav_tencent=nav_tencent,
                drift_pct=drift,
                spy_close_recorded=nd.spy_close,
                spy_close_tencent=spy_cb.close if spy_cb else None,
            ))
        abs_drifts = [abs(r.drift_pct) for r in day_rows]
        weights = [max(0.0, nd.holdings_mtm) for nd in sorted(nav_days, key=lambda x: x.trading_date)]
        wsum = sum(weights)
        weighted = (
            sum(a * w for a, w in zip(abs_drifts, weights, strict=True)) / wsum
            if wsum > 0 else 0.0
        )
        max_i = max(range(len(day_rows)), key=lambda i: abs_drifts[i])
        nav_audit = NavAudit(
            days=len(day_rows),
            mean_abs_drift_pct=sum(abs_drifts) / len(abs_drifts),
            max_abs_drift_pct=abs_drifts[max_i],
            max_drift_date=day_rows[max_i].trading_date,
            weighted_mean_abs_drift_pct=weighted,
            mean_signed_drift_pct=sum(r.drift_pct for r in day_rows) / len(day_rows),
            per_day=tuple(day_rows),
            unpriceable_tickers=tuple(sorted(unpriceable)),
        )
        nav_verdict = (
            "FAIL"
            if (nav_audit.mean_abs_drift_pct > thresholds.nav_mean_abs_drift_pct
                or nav_audit.max_abs_drift_pct > thresholds.nav_max_abs_drift_pct)
            else "PASS"
        )

    # --- adjustment-basis analysis (first-class) ---
    adj = []
    for ticker, vals in sorted(signed_by_ticker.items()):
        pos = sum(1 for v in vals if v > 0)
        neg = sum(1 for v in vals if v < 0)
        dominant = max(pos, neg)
        adj.append(AdjustmentBasisRow(
            ticker=ticker, n_dates=len(vals),
            mean_signed_bps=sum(vals) / len(vals),
            same_sign_ratio=(dominant / len(vals)) if vals else 0.0,
        ))

    legs = [v for v in (fills_verdict, nav_verdict) if v != "SKIPPED"]
    overall = "FAIL" if "FAIL" in legs else ("PASS" if legs else "SKIPPED")
    return PricingAuditResult(
        thresholds=thresholds,
        fills=fills_audit,
        nav=nav_audit,
        adjustment_basis_analysis=tuple(adj),
        verdict=Verdict(fills=fills_verdict, nav=nav_verdict, overall=overall),
    )
```

IMPLEMENTATION NOTE on the unpriceable-position branch: the `mtm = None; break` sentinel
implements the spec rule "that position keeps its recorded value" at DAY granularity (we only
have day-level recorded totals, not per-position recorded values). A day containing any
unpriceable position contributes zero drift and its tickers are listed. The test
`test_unpriceable_ticker_visible_not_silent` pins this. If ruff/typing complains about the
sentinel, restructure to a boolean flag — behavior, not style, is what's locked.

- [ ] **Step 4: Run → 9 tests pass. `uv run ruff check` clean.**

- [ ] **Step 5: Commit** — `feat(evaluation): pricing audit core — fills + NAV dual leg (EE-T1)`

---

### Task 2: DB loaders

**Files:**
- Modify: `marketpulse/evaluation/pricing_audit.py` (append)
- Test: append to `tests/evaluation/test_pricing_audit.py`

- [ ] **Step 1: Failing tests** — seed with `db_session`: a PaperOrder + PaperPosition +
  PaperFill chain (copy the seeding approach from tests/trading/test_query_models.py's
  helpers — it builds exactly this chain), plus a PaperNavSnapshot row and matching
  PaperCashLedger/positions (copy from tests/portfolio/test_snapshot_runner.py). Assert:
  - `load_fills(db)` returns FillInput rows with ticker JOINED from PaperPosition, NY trading
    date derived from filled_at, float price.
  - `load_nav_days(db)` returns one NavDayInput per snapshot with positions-as-of that date
    and recorded cash/holdings/nav/spy_close as floats.

- [ ] **Step 2: FAIL.** **Step 3: Implement** (append; local imports per repo style):

```python
def load_fills(db) -> list[FillInput]:
    """All paper fills with ticker joined via position; NY trading date."""
    from sqlalchemy import select

    from marketpulse.db.models import PaperFill, PaperPosition
    from marketpulse.trading.calendar import NY

    rows = db.execute(
        select(PaperFill, PaperPosition.ticker)
        .join(PaperPosition, PaperPosition.id == PaperFill.position_id)
        .order_by(PaperFill.id),
    ).all()
    out = []
    for fill, ticker in rows:
        filled_at = fill.filled_at
        if filled_at.tzinfo is None:
            from datetime import UTC
            filled_at = filled_at.replace(tzinfo=UTC)
        out.append(FillInput(
            fill_id=fill.id, ticker=ticker, side=fill.side,
            quantity=fill.quantity, price=float(fill.price),
            trading_date=filled_at.astimezone(NY).date(),
        ))
    return out


def load_nav_days(db) -> list[NavDayInput]:
    """One NavDayInput per recorded snapshot, positions as-of that date."""
    from sqlalchemy import select

    from marketpulse.db.models import PaperNavSnapshot
    from marketpulse.portfolio.snapshot_runner import _read_open_positions

    snaps = db.scalars(
        select(PaperNavSnapshot).order_by(PaperNavSnapshot.trading_date),
    ).all()
    out = []
    for s in snaps:
        positions = tuple(
            PositionInput(ticker=p.ticker, quantity=float(p.quantity))
            for p in _read_open_positions(db, s.trading_date)
        )
        out.append(NavDayInput(
            trading_date=s.trading_date,
            cash_balance=float(s.cash_balance),
            holdings_mtm=float(s.holdings_mtm),
            portfolio_nav=float(s.portfolio_nav),
            spy_close=float(s.spy_close) if s.spy_close is not None else None,
            positions=positions,
        ))
    return out
```

Check `_read_open_positions`'s actual signature/return before relying on attribute names —
adapt the comprehension if OpenPosition exposes different names. Importing a private helper
from snapshot_runner is acceptable here (read path reuse, same pattern as tests do); do NOT
copy the query.

- [ ] **Step 4: PASS + full suite + ruff.** **Step 5: Commit** —
  `feat(evaluation): pricing audit DB loaders (EE-T2)`

---

### Task 3: CLI with Tencent fetch

**Files:**
- Create: `marketpulse/cli/pricing_audit.py`
- Test: `tests/cli/test_pricing_audit_cli.py`

- [ ] **Step 1: Failing tests** — mock TencentClient (monkeypatch
  `marketpulse.cli.pricing_audit.TencentClient` with a stub returning Bars built from
  marketpulse.data.types.Bar). Three tests:
  1. seeded db (reuse Task 2 seeding) + stub bars → `main(argv=[])` prints valid JSON with
     keys thresholds/fills/nav/adjustment_basis_analysis/verdict/caveats; thresholds echoed
     match the locked constants; DATABASE_URL monkeypatch + get_settings.cache_clear() idiom.
  2. empty db → SystemExit(1), stderr mentions "nothing to audit".
  3. argparse exposes NO threshold flags: `main(argv=["--fills-mean", "999"])` exits with
     argparse error (SystemExit code 2).

- [ ] **Step 2: FAIL.** **Step 3: Implement**:

```python
# Layer: cli
"""Independent pricing audit: python -m marketpulse.cli.pricing_audit

NOT broker shadow. Compares paper fills + the NAV series against Tencent
(independent vendor). Pre-registered thresholds; deliberately NO flags to
override them (a FAIL is a FAIL).
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import sys

from marketpulse.data.tencent_client import TencentClient
from marketpulse.db.base import session_scope
from marketpulse.evaluation.pricing_audit import (
    AuditBar,
    load_fills,
    load_nav_days,
    run_pricing_audit,
)


def _json_default(o):
    import datetime
    if isinstance(o, (datetime.date, datetime.datetime)):
        return o.isoformat()
    raise TypeError(type(o).__name__)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="60d", help="tencent history window")
    args = ap.parse_args(argv)  # NO threshold flags by design

    gen = session_scope()
    db = next(gen)
    try:
        fills = load_fills(db)
        nav_days = load_nav_days(db)
        if not fills and not nav_days:
            print("nothing to audit: no fills and no NAV days", file=sys.stderr)
            raise SystemExit(1)
        tickers = (
            {f.ticker for f in fills}
            | {p.ticker for nd in nav_days for p in nd.positions}
            | {"SPY"}
        )
        client = TencentClient()
        bars_by_ticker = {}
        for t in sorted(tickers):
            try:
                bars = client.fetch_history(t, period=args.period)
            except Exception as exc:  # noqa: BLE001 - per-ticker isolation
                print(f"tencent fetch failed for {t}: {exc}", file=sys.stderr)
                bars = []
            bars_by_ticker[t] = [
                AuditBar(date=b.date, open=float(b.open), close=float(b.close))
                for b in bars
            ]
        result = run_pricing_audit(fills, nav_days, bars_by_ticker)
        print(json.dumps(dataclasses.asdict(result), indent=2, default=_json_default))
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


if __name__ == "__main__":
    main()
```

(Verify TencentClient.fetch_history's `period` parameter accepts "60d"; if its signature
differs, adapt the call — the spec requirement is "bars covering the paper era".)

- [ ] **Step 4: PASS + FULL suite + ruff.** **Step 5: Commit** —
  `feat(cli): pricing_audit CLI — independent pricing audit JSON (EE-T3)`

---

### Task 4: CHARTER pointer + final integration

- [ ] **Step 1:** In `docs/CHARTER.md`, execution-trust chain item (item 3 of the evidence
  chain), append ONE pointer line:

```markdown
**2a deliverable (this PR):** `python -m marketpulse.cli.pricing_audit` — the Independent
Pricing Audit (spec 2026-06-12); pre-registered PASS/FAIL; run results live in run output,
not in this fact layer, unless later promoted.
```

- [ ] **Step 2:** `uv run pytest -q` full suite green; `uv run ruff check` clean.
- [ ] **Step 3: Commit** — `docs(charter): execution-trust 2a deliverable pointer (EE-T4)`

---

## Post-merge run (operator step)

```bash
docker exec marketpulse /app/.venv/bin/python -m marketpulse.cli.pricing_audit
```

First Execution Trustworthiness evidence: PASS/FAIL on the pre-registered gates. Bring the
JSON back for interpretation — especially `adjustment_basis_analysis` and the NAV drift legs.
