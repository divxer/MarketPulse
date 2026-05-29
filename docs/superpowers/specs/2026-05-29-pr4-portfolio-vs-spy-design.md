# PR4 — `/lab/portfolio-vs-spy` north-star visualization — Design

**Date:** 2026-05-29
**Charter priority:** #2 (observability before optimization)
**Status:** Approved (brainstorming complete)
**Consumes:** PR3a `paper_nav_snapshot` (the source of truth for the north-star metric)

---

## Goal

Surface the Charter north-star metric — `paper_portfolio_excess_return_vs_spy_90d` —
as a human-readable web page: a dual-line index chart (portfolio vs SPY), an
excess-return spread chart, and a headline excess-return number with an explicit
statistical-confidence badge.

This is a **read-only visualization** over PR3a's already-computed snapshot series.
It does NOT recompute NAV, does NOT re-anchor, does NOT add a range toggle, does NOT
re-base, and has NO network dependency.

## Why this exists

The Charter's single driving question is *"Does this system, running on paper money,
beat SPY?"* PR3a computes the answer per day into `paper_nav_snapshot`; `/lab/charter-metrics`
exposes only the *latest* point as JSON. There is no way to *see* the trajectory.
PR4 makes the north-star visible so the operator can watch the two equity curves
diverge and judge whether alpha is widening or shrinking — before any optimization work.

---

## Critical semantic context (as-built, not redesigned here)

PR3a anchors `portfolio_index` / `spy_index` to the **earliest snapshot ever**
(`snapshot_runner` lines ~127–142), NOT to a rolling 90-day-back point. Therefore:

- `portfolio_index = portfolio_nav / inception_anchor_nav` — **cumulative since inception**.
- `spy_index = spy_close / inception_spy_anchor` — cumulative since inception.
- `excess_return = portfolio_index − spy_index` — cumulative-since-inception excess.
- The "90d" in the metric name is a **statistical-sufficiency gate**
  (`trading_days_observed ≥ NORTH_STAR_WINDOW (90)` → `is_sufficient`), NOT a
  rolling return window.

PR4 visualizes exactly what PR3a stores. It does not attempt to redefine the metric.
This is consistent with the Charter directive to treat as-built specs as the baseline.

---

## Architecture

Three layers, strict separation (matches PR3a/PR3b discipline):

```
marketpulse/portfolio/portfolio_vs_spy_view.py   # PURE presenter — no DB/web/Jinja/auth/clock
marketpulse/web/routes/portfolio.py              # THIN composition root
marketpulse/web/templates/lab_portfolio_vs_spy.html + partials/pvs_*.html   # DUMB renderer
```

Plus one small read-only repo helper (`get_all_snapshots`) and one nav entry.

### Layer responsibilities

| File | Does | Does NOT |
|---|---|---|
| `portfolio_vs_spy_view.py` | `NavSnapshot[]` → view-model: hero KPI, badge, banner state, KPI strip, dual-index SVG points, excess SVG points, zero-reference line | touch DB, FastAPI, Jinja, auth, clock |
| `routes/portfolio.py` | `get_all_snapshots()` → `build_portfolio_vs_spy_view()` → render | compute chart math, scale SVG, format values |
| `lab_portfolio_vs_spy.html` + `pvs_*` partials | loop / print / inline SVG polyline | compute anything |

---

## The pure view-model (data contract)

```python
@dataclass(frozen=True)
class ChartData:
    portfolio_points: str        # SVG polyline "x,y x,y …"
    spy_points: str
    excess_points: str
    zero_y: float                # y-coord of the excess 0-reference line
    index_lo: Decimal            # shared index y-axis bound (label)
    index_hi: Decimal
    excess_lo: Decimal           # excess y-axis bound (label)
    excess_hi: Decimal
    viewbox_w: int               # 800
    viewbox_h: int               # 280

@dataclass(frozen=True)
class PortfolioVsSpyView:
    has_data: bool                       # False when 0 snapshots
    chartable: bool                      # len(chart_run) >= 2
    # hero
    hero_excess_return: Decimal | None   # latest snapshot's excess_return (None → N/A)
    badge: Literal["PRELIMINARY", "SUFFICIENT"]
    show_insufficiency_banner: bool      # has_data and not is_sufficient
    # KPI strip (from latest snapshot)
    portfolio_index_latest: Decimal | None
    spy_index_latest: Decimal | None
    coverage_observed: int               # trading_days_observed
    coverage_required: int               # NORTH_STAR_WINDOW (90)
    is_sufficient: bool
    # chart provenance
    first_date: date | None              # earliest snapshot (full series)
    last_date: date | None               # latest snapshot
    chart_start_date: date | None        # first snapshot in chart_run
    dropped_prefix_count: int            # snapshots before chart_run begins
    chart: ChartData | None              # None when not chartable
```

### `chart_run` — the single plottable filter

```
chart_run = [s for s in series
             if s.portfolio_index is not None
             and s.spy_index is not None
             and s.excess_return is not None]
```

All three lines plot from `chart_run` → identical x-domain, no gapped polylines.
`chartable = len(chart_run) >= 2`. `chart_start_date = chart_run[0].trading_date`
(or None). `dropped_prefix_count = len(series) − len(chart_run)` for the leading
portfolio-only rows before SPY was anchored.

### Latest-missing-SPY rule (explicit, never computed downstream)

If the **latest** snapshot's `spy_index`/`excess_return` is `None`:
- `hero_excess_return = None` (template renders `N/A`)
- KPI strip shows `N/A` for SPY index and excess
- `badge` still follows `latest.is_sufficient`
- `show_insufficiency_banner` still driven by coverage / `is_sufficient`

---

## SVG scaling math (the risky core — unit-tested directly)

ViewBox `0 0 800 280` (`W=800, H=280`), like `backtest_equity_chart`. Coordinates
computed as floats in the presenter, formatted into `points_str`. After downsampling,
`N = len(plotted_run)`; `chartable` guarantees `N ≥ 2`.

**x-axis (shared by all three lines), recomputed against the PLOTTED N:**
```
x_i = (i / (N − 1)) * W          # N >= 2 → no div-by-zero
```
x is computed AFTER downsampling, using the post-downsample count — never the raw N
(otherwise points bunch left with empty space on the right).

**Index chart — shared y-scale across BOTH lines:**
```
lo = min(all portfolio_index ∪ all spy_index in plotted run)
hi = max(all portfolio_index ∪ all spy_index in plotted run)
y(v) = H − (v − lo)/(hi − lo) * H        # SVG y grows down → invert
if hi == lo:  y(v) = H/2                  # flat-series guard
```
Both polylines share `lo/hi` so the two curves are honestly comparable (no per-line
autoscale illusion of convergence/divergence).

**Excess chart — separate scale that always contains 0:**
```
elo = min(0, min(excess in plotted run))
ehi = max(0, max(excess in plotted run))   # 0 ∈ [elo, ehi] guaranteed
zero_y = H − (0 − elo)/(ehi − elo) * H
if ehi == elo:  elo, ehi = −ε, +ε; zero_y = H/2   # all-zero degenerate guard
```
Forcing 0 into the range keeps the 0-reference line on-canvas at the correct height
(it is a mathematical baseline, not decoration).

**Downsampling:**
```
MAX_CHART_POINTS = 180
if len(chart_run) > MAX_CHART_POINTS:
    stride-sample, ALWAYS preserving first and last, deterministically
```
At today's ~42 snapshots this is a no-op. Intermediate selection need not be exactly
equidistant but MUST be deterministic (same input → same output).

---

## Two distinct empty states (template must not conflate)

| State | Condition | Page shows |
|---|---|---|
| No data | `has_data == False` | "No snapshots yet — the daily NAV snapshot job hasn't recorded data." No chart, no banner. |
| Not chartable | `has_data == True and chartable == False` | KPI strip + hero (from latest), plus "Snapshots exist but benchmark/chart not ready (need ≥2 days with SPY data)." Plus the dropped-prefix note if applicable. |

When `dropped_prefix_count > 0` (and chartable), show a small note:
*"Chart starts from first snapshot with SPY benchmark data."*

---

## Insufficiency UX (page state, not a minor KPI)

When `show_insufficiency_banner` is true, render a prominent warning strip ABOVE the
chart (non-dismissable):

> ⚠ **Preliminary Data** — Coverage: {observed} / {required} trading days.
> The excess-return metric is observable but has not yet met the Charter sufficiency
> threshold (90 trading days). Interpret trends with caution.

The hero excess-return value is **always shown** (never hidden/blurred) — the metric
is observable before it is statistically validated; the UI exposes both the value and
its confidence. Hero carries a `[PRELIMINARY]` / `[SUFFICIENT]` badge driven solely by
`latest.is_sufficient`. The KPI strip's `Coverage` and `Sufficient` cells use the
warning color when insufficient.

---

## Route, repo, nav

**Repo** — add one read-only helper:
```python
def get_all_snapshots(session) -> list[NavSnapshot]:
    """All snapshots, ascending by trading_date.
    Read-only UI helper; NOT used by snapshot computation/anchor paths."""
```

**Route** `marketpulse/web/routes/portfolio.py`:
```python
@router.get("/lab/portfolio-vs-spy")
def lab_portfolio_vs_spy(_: None = Depends(require_auth), db: Session = Depends(get_db)):
    view = build_portfolio_vs_spy_view(get_all_snapshots(db))
    return templates.TemplateResponse("lab_portfolio_vs_spy.html", {"request": ..., "view": view})
```
DB-only read; no manifest, no sqlite gate. Zero snapshots → `has_data=False` → empty-state,
HTTP 200. Registered in `main.py` after `charter.router`.

**Nav** — add to `base.html`, leading the lab group (this is *the* north-star page):
label **「北极星 / Portfolio vs SPY」** → `/lab/portfolio-vs-spy`, with `mp-nav-active`
on the `/lab/portfolio-vs-spy` prefix.

---

## Testing

**Pure presenter unit tests** (the bulk — no DB, no web):
- E1 empty series → `has_data=False`, `chart=None`, no banner
- E2 all `spy_index=None` → `chartable=False`, `chart_start_date=None`, `dropped_prefix_count=len`, KPIs N/A
- E3 exactly 1 row in `chart_run` → `chartable=False`; hero/KPIs still render
- E4 flat index (`hi==lo`) → mid-line, no div-by-zero
- E5 all excess == 0 → zero-range guard, `zero_y` at mid
- E6 latest snapshot missing SPY → `hero_excess_return=None`, badge from `is_sufficient`
- E7 `dropped_prefix_count > 0` → chart starts at first SPY date, note flagged
- math: shared `lo/hi` across both index lines; `0 ∈ [excess_lo, excess_hi]`; `zero_y`
  placement; x recomputed against post-downsample N; downsample preserves first+last and
  is deterministic

**Route tests** (TestClient + seeded snapshots, like `test_charter_route.py`):
- 401 unauthenticated
- 200 authed
- insufficient seed → banner present AND hero value still shown
- empty DB → empty-state, no crash
- non-brittle render assertions only — presence of substrings
  `<polyline`, `Portfolio`, `SPY`, `Excess Return`, `PRELIMINARY` — NOT exact
  `points_str` (float formatting is brittle; exact coordinates are asserted in the
  presenter unit tests instead)

All new files carry a `# Layer:` tag (enforced by the existing pytest hook).

---

## Scope locks

| Lock | Rule |
|---|---|
| L1 | `portfolio_vs_spy_view.py` is pure — no DB, web, Jinja, auth, clock |
| L2 | `chart_run` = snapshots with `portfolio_index`+`spy_index`+`excess_return` all non-null; `chartable = len(chart_run) ≥ 2` |
| L3 | Index chart shares one `lo/hi` across both lines |
| L4 | Excess chart range always contains 0; `zero_y` on-canvas |
| L5 | Downsample ≤ 180 pts; x recomputed against plotted N; first+last preserved; deterministic |
| L6 | Latest-missing-SPY → hero/excess `N/A`; badge from `is_sufficient` |
| L7 | Banner = `has_data and not is_sufficient`; hero value NEVER hidden |
| L8 | All-since-inception; NO range toggle (deferred until `snapshot_count > 120`) |
| L9 | Read-only: no new table, no migration, no network; DB-only read |
| L10 | Nav entry leads the lab group |
| L11 | Two distinct empty states (no-data vs not-chartable) never conflated |
| L12 | `get_all_snapshots` is a read-only UI helper, not used by computation paths |

## Out of scope (v1)

Range selectors (30D/90D/All), per-window re-basing, drawdown chart, per-strategy
breakdown, CSV export, auto-refresh / websocket. Range selectors are explicitly
deferred until snapshot history meaningfully exceeds the largest supported range
(`snapshot_count > 120`).

---

## Forward-compatibility note

V1 intentionally exposes the complete since-inception series with no range selector.
Range selectors (30D / 90D / 180D / All) are deferred until snapshot history
meaningfully exceeds the largest supported range. Until then, with < 90 snapshots,
"All" and "90D" are identical and a selector carries no user value (YAGNI).
