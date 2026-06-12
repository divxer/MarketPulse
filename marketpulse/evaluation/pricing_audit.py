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
        weights = [
            max(0.0, nd.holdings_mtm)
            for nd in sorted(nav_days, key=lambda x: x.trading_date)
        ]
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
        nonzero = pos + neg
        adj.append(AdjustmentBasisRow(
            ticker=ticker, n_dates=len(vals),
            mean_signed_bps=sum(vals) / len(vals),
            # Spec-locked: fraction of NON-ZERO observations sharing the
            # majority sign; zeros excluded from both sides; 0.0 if none.
            same_sign_ratio=(max(pos, neg) / nonzero) if nonzero else 0.0,
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


# --- DB loaders (thin; local imports per repo style) ---


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
