# PR4 — `/lab/portfolio-vs-spy` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only `/lab/portfolio-vs-spy` web page that visualizes PR3a's `paper_nav_snapshot` series as a dual-index chart (portfolio vs SPY), an excess-return spread chart, and a confidence-badged hero number.

**Architecture:** Pure presenter (`portfolio_vs_spy_view.py`) maps `list[NavSnapshot]` → frozen view-model with precomputed SVG polyline strings; a thin route (`routes/portfolio.py`) does `get_all_snapshots()` → presenter → render; a dumb template renders labels + inline SVG. No NAV recompute, no migration, no network.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, FastAPI, Jinja2, server-rendered inline SVG `<polyline>` (no JS chart lib), pytest.

**Spec:** `docs/superpowers/specs/2026-05-29-pr4-portfolio-vs-spy-design.md` (commit `6909c54`). Scope locks L1–L15, edge cases E1–E10.

**Branch:** `feat/pr4-portfolio-vs-spy` (already created).

---

## File Structure

| File | Responsibility | Layer |
|---|---|---|
| `marketpulse/portfolio/portfolio_vs_spy_view.py` (create) | Pure presenter: dataclasses, label formatters, chart_run filter, downsample, SVG scaling, `build_portfolio_vs_spy_view` | `pure` |
| `marketpulse/portfolio/snapshot_repo.py` (modify) | Add `get_all_snapshots` read-only helper | `db` |
| `marketpulse/web/routes/portfolio.py` (create) | Thin route `GET /lab/portfolio-vs-spy` | `web` |
| `marketpulse/web/main.py` (modify) | Register `portfolio.router` | `web` |
| `marketpulse/web/templates/lab_portfolio_vs_spy.html` (create) | Page shell | template |
| `marketpulse/web/templates/partials/pvs_hero.html` (create) | Hero + badge | template |
| `marketpulse/web/templates/partials/pvs_banner.html` (create) | Insufficiency banner | template |
| `marketpulse/web/templates/partials/pvs_kpi_strip.html` (create) | KPI strip | template |
| `marketpulse/web/templates/partials/pvs_index_chart.html` (create) | Dual-index SVG | template |
| `marketpulse/web/templates/partials/pvs_excess_chart.html` (create) | Excess SVG + 0-line | template |
| `marketpulse/web/templates/base.html` (modify) | Nav entry | template |
| `tests/portfolio/test_portfolio_vs_spy_view.py` (create) | Presenter unit tests E1–E10 + math | test |
| `tests/portfolio/test_snapshot_repo_get_all.py` (create) | `get_all_snapshots` test | test |
| `tests/web/test_portfolio_route.py` (create) | Route tests | test |

---

## Task 1: `get_all_snapshots` repo helper

**Files:**
- Modify: `marketpulse/portfolio/snapshot_repo.py`
- Test: `tests/portfolio/test_snapshot_repo_get_all.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# Layer: test
"""PR4 — get_all_snapshots read-only helper."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from marketpulse.db.base import Base
from marketpulse.portfolio.north_star import NavSnapshot
from marketpulse.portfolio.snapshot_repo import get_all_snapshots, insert_snapshot


def _snap(d: date) -> NavSnapshot:
    return NavSnapshot(
        trading_date=d, cash_balance=Decimal("100000"), holdings_mtm=Decimal("0"),
        portfolio_nav=Decimal("100000"), anchor_portfolio_nav=Decimal("100000"),
        portfolio_index=Decimal("1.0"), spy_close=Decimal("500"),
        anchor_spy_close=Decimal("500"), spy_index=Decimal("1.0"),
        excess_return=Decimal("0.0"), trading_days_observed=1,
        coverage_ratio=Decimal("0.01"), is_sufficient=False,
        unpriced_positions_count=0, unpriced_tickers=(),
    )


def test_get_all_snapshots_empty_returns_empty_list():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        assert get_all_snapshots(s) == []


def test_get_all_snapshots_ascending_by_date():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        insert_snapshot(s, _snap(date(2026, 8, 14)))
        insert_snapshot(s, _snap(date(2026, 8, 12)))
        insert_snapshot(s, _snap(date(2026, 8, 13)))
        s.commit()
        rows = get_all_snapshots(s)
        assert [r.trading_date for r in rows] == [
            date(2026, 8, 12), date(2026, 8, 13), date(2026, 8, 14),
        ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/portfolio/test_snapshot_repo_get_all.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_all_snapshots'`.

- [ ] **Step 3: Add the helper**

Add to `marketpulse/portfolio/snapshot_repo.py` (after `get_earliest_snapshot`):

```python
def get_all_snapshots(session: Session) -> list[NavSnapshot]:
    """All snapshots, ascending by trading_date.

    Read-only UI helper for /lab/portfolio-vs-spy (L12). NOT used by snapshot
    computation or anchor-recovery paths.
    """
    rows = session.scalars(
        select(PaperNavSnapshot)
        .order_by(PaperNavSnapshot.trading_date.asc()),
    ).all()
    return [_row_to_dc(r) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/portfolio/test_snapshot_repo_get_all.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/portfolio/snapshot_repo.py tests/portfolio/test_snapshot_repo_get_all.py
git commit -m "feat(pr4): get_all_snapshots read-only repo helper (L12)"
```

---

## Task 2: View dataclasses + label formatters

**Files:**
- Create: `marketpulse/portfolio/portfolio_vs_spy_view.py`
- Test: `tests/portfolio/test_portfolio_vs_spy_view.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# Layer: test
"""PR4 — portfolio_vs_spy_view pure presenter."""
from __future__ import annotations

from decimal import Decimal

from marketpulse.portfolio.portfolio_vs_spy_view import (
    _fmt_excess_label,
    _fmt_index_label,
)


def test_fmt_excess_label_positive():
    assert _fmt_excess_label(Decimal("0.032")) == "+3.2%"


def test_fmt_excess_label_negative():
    assert _fmt_excess_label(Decimal("-0.014")) == "-1.4%"


def test_fmt_excess_label_zero():
    assert _fmt_excess_label(Decimal("0")) == "+0.0%"


def test_fmt_excess_label_none():
    assert _fmt_excess_label(None) == "N/A"


def test_fmt_index_label():
    assert _fmt_index_label(Decimal("1.0413")) == "1.041"


def test_fmt_index_label_none():
    assert _fmt_index_label(None) == "N/A"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/portfolio/test_portfolio_vs_spy_view.py -v`
Expected: FAIL — `ModuleNotFoundError: marketpulse.portfolio.portfolio_vs_spy_view`.

- [ ] **Step 3: Create the module with dataclasses + formatters**

```python
# Layer: pure
"""PR4 — pure presenter for /lab/portfolio-vs-spy.

Maps a list[NavSnapshot] (PR3a source of truth) into a frozen view-model with
precomputed SVG polyline strings. No DB, no FastAPI, no Jinja, no auth, no clock
(L1). All Decimal->string formatting lives here (L13).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from marketpulse.portfolio.north_star import NORTH_STAR_WINDOW, NavSnapshot

VIEWBOX_W = 800
VIEWBOX_H = 280
MAX_CHART_POINTS = 180
VALUE_NA = "N/A"


@dataclass(frozen=True)
class ChartData:
    portfolio_points: str        # SVG polyline "x,y x,y …"
    spy_points: str
    excess_points: str
    zero_y: float                # y-coord of the excess 0-reference line
    index_lo: Decimal            # shared index y-axis bound (label)
    index_hi: Decimal
    excess_lo: Decimal
    excess_hi: Decimal
    viewbox_w: int
    viewbox_h: int


@dataclass(frozen=True)
class PortfolioVsSpyView:
    has_data: bool
    chartable: bool
    hero_excess_return: Decimal | None
    hero_excess_return_label: str
    badge: Literal["PRELIMINARY", "SUFFICIENT"]
    show_insufficiency_banner: bool
    portfolio_index_latest: Decimal | None
    portfolio_index_label: str
    spy_index_latest: Decimal | None
    spy_index_label: str
    coverage_observed: int
    coverage_required: int
    coverage_label: str
    is_sufficient: bool
    first_date: date | None
    last_date: date | None
    chart_start_date: date | None
    dropped_prefix_count: int
    excluded_nonprefix_count: int
    chart: ChartData | None


def _fmt_excess_label(value: Decimal | None) -> str:
    """0.032 -> '+3.2%'; -0.014 -> '-1.4%'; 0 -> '+0.0%'; None -> 'N/A'."""
    if value is None:
        return VALUE_NA
    pct = (Decimal(value) * Decimal("100")).quantize(Decimal("0.1"))
    return f"+{pct}%" if pct >= 0 else f"{pct}%"


def _fmt_index_label(value: Decimal | None) -> str:
    """1.0413 -> '1.041'; None -> 'N/A'."""
    if value is None:
        return VALUE_NA
    return f"{Decimal(value).quantize(Decimal('0.001'))}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/portfolio/test_portfolio_vs_spy_view.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/portfolio/portfolio_vs_spy_view.py tests/portfolio/test_portfolio_vs_spy_view.py
git commit -m "feat(pr4): view-model dataclasses + label formatters (L13)"
```

---

## Task 3: `_compute_chart_run` — contiguous suffix (L2, L15)

**Files:**
- Modify: `marketpulse/portfolio/portfolio_vs_spy_view.py`
- Test: `tests/portfolio/test_portfolio_vs_spy_view.py`

- [ ] **Step 1: Write the failing test**

Add to the test file (append; add these imports at the top of the file alongside the existing import):

```python
from datetime import date

from marketpulse.portfolio.north_star import NavSnapshot
from marketpulse.portfolio.portfolio_vs_spy_view import _compute_chart_run


def _snap(d, *, port="1.0", spy="1.0", excess="0.0", days=10, sufficient=False):
    """Factory. Pass None (not a string) for port/spy/excess to omit a field."""
    def _dec(x):
        return None if x is None else Decimal(x)
    return NavSnapshot(
        trading_date=d, cash_balance=Decimal("100000"), holdings_mtm=Decimal("0"),
        portfolio_nav=Decimal("100000"), anchor_portfolio_nav=Decimal("100000"),
        portfolio_index=_dec(port), spy_close=Decimal("500"),
        anchor_spy_close=Decimal("500"), spy_index=_dec(spy),
        excess_return=_dec(excess), trading_days_observed=days,
        coverage_ratio=Decimal("0.1"), is_sufficient=sufficient,
        unpriced_positions_count=0, unpriced_tickers=(),
    )


def test_chart_run_all_complete():
    series = [_snap(date(2026, 8, 10 + i)) for i in range(3)]
    run, dropped, excluded = _compute_chart_run(series)
    assert len(run) == 3
    assert dropped == 0
    assert excluded == 0


def test_chart_run_drops_leading_portfolio_only_prefix():
    # First 2 rows have no SPY (pre-anchor), rest complete.
    series = [
        _snap(date(2026, 8, 10), spy=None, excess=None),
        _snap(date(2026, 8, 11), spy=None, excess=None),
        _snap(date(2026, 8, 12)),
        _snap(date(2026, 8, 13)),
    ]
    run, dropped, excluded = _compute_chart_run(series)
    assert [s.trading_date for s in run] == [date(2026, 8, 12), date(2026, 8, 13)]
    assert dropped == 2
    assert excluded == 0


def test_chart_run_truncates_at_midseries_gap():
    # Complete, complete, GAP, complete -> run stops at the gap, excluded counts the tail.
    series = [
        _snap(date(2026, 8, 12)),
        _snap(date(2026, 8, 13)),
        _snap(date(2026, 8, 14), spy=None, excess=None),
        _snap(date(2026, 8, 15)),
    ]
    run, dropped, excluded = _compute_chart_run(series)
    assert [s.trading_date for s in run] == [date(2026, 8, 12), date(2026, 8, 13)]
    assert dropped == 0
    assert excluded == 2  # the gap row + the later complete row after it


def test_chart_run_all_incomplete():
    series = [_snap(date(2026, 8, 10 + i), spy=None, excess=None) for i in range(3)]
    run, dropped, excluded = _compute_chart_run(series)
    assert run == []
    assert dropped == 3
    assert excluded == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/portfolio/test_portfolio_vs_spy_view.py::test_chart_run_all_complete -v`
Expected: FAIL — `ImportError: cannot import name '_compute_chart_run'`.

- [ ] **Step 3: Implement `_compute_chart_run`**

Add to `marketpulse/portfolio/portfolio_vs_spy_view.py`:

```python
def _is_complete(s: NavSnapshot) -> bool:
    return (
        s.portfolio_index is not None
        and s.spy_index is not None
        and s.excess_return is not None
    )


def _compute_chart_run(
    series: list[NavSnapshot],
) -> tuple[list[NavSnapshot], int, int]:
    """Return (chart_run, dropped_prefix_count, excluded_nonprefix_count).

    chart_run is the CONTIGUOUS run of all-three-non-null snapshots starting at
    the first complete row (L2, L15). It STOPS at the first later incomplete row
    (a mid/tail gap) rather than connecting across it.
      - dropped_prefix_count = index of first complete row (true leading prefix).
        If no complete row exists, every row is prefix -> len(series).
      - excluded_nonprefix_count = rows after `start` that were dropped because a
        gap appeared (should be 0 under the PR3a lazy-anchor invariant).
    """
    start = next((i for i, s in enumerate(series) if _is_complete(s)), None)
    if start is None:
        return [], len(series), 0
    tail = series[start:]
    run: list[NavSnapshot] = []
    for s in tail:
        if _is_complete(s):
            run.append(s)
        else:
            break
    return run, start, len(tail) - len(run)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/portfolio/test_portfolio_vs_spy_view.py -v`
Expected: PASS (all chart_run tests + Task 2 formatters).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/portfolio/portfolio_vs_spy_view.py tests/portfolio/test_portfolio_vs_spy_view.py
git commit -m "feat(pr4): _compute_chart_run contiguous suffix + prefix/gap accounting (L2,L15)"
```

---

## Task 4: `_downsample` — ≤180 pts, first+last preserved, deterministic (L5)

**Files:**
- Modify: `marketpulse/portfolio/portfolio_vs_spy_view.py`
- Test: `tests/portfolio/test_portfolio_vs_spy_view.py`

- [ ] **Step 1: Write the failing test**

```python
from marketpulse.portfolio.portfolio_vs_spy_view import MAX_CHART_POINTS, _downsample


def test_downsample_noop_when_small():
    rows = [_snap(date(2026, 1, 1)) for _ in range(10)]
    out = _downsample(rows)
    assert len(out) == 10
    assert out == rows


def test_downsample_caps_and_preserves_first_last():
    rows = [_snap(date(2026, 1, 1), port=str(1.0 + i / 1000)) for i in range(500)]
    out = _downsample(rows)
    assert len(out) <= MAX_CHART_POINTS
    assert out[0] is rows[0]
    assert out[-1] is rows[-1]


def test_downsample_deterministic():
    rows = [_snap(date(2026, 1, 1), port=str(1.0 + i / 1000)) for i in range(500)]
    assert _downsample(rows) == _downsample(rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/portfolio/test_portfolio_vs_spy_view.py::test_downsample_caps_and_preserves_first_last -v`
Expected: FAIL — `ImportError: cannot import name '_downsample'`.

- [ ] **Step 3: Implement `_downsample`**

```python
def _downsample(
    rows: list[NavSnapshot], max_points: int = MAX_CHART_POINTS,
) -> list[NavSnapshot]:
    """Deterministic stride-sample to <= max_points, ALWAYS preserving the first
    and last rows (L5). No-op when len(rows) <= max_points."""
    n = len(rows)
    if n <= max_points:
        return list(rows)
    # Evenly spaced indices across [0, n-1]; i=0 -> 0, i=max-1 -> n-1.
    idxs = sorted({
        round(i * (n - 1) / (max_points - 1)) for i in range(max_points)
    })
    return [rows[i] for i in idxs]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/portfolio/test_portfolio_vs_spy_view.py -k downsample -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/portfolio/portfolio_vs_spy_view.py tests/portfolio/test_portfolio_vs_spy_view.py
git commit -m "feat(pr4): _downsample deterministic, first+last preserved (L5)"
```

---

## Task 5: `_build_chart_data` — SVG scaling (L3, L4, L5)

**Files:**
- Modify: `marketpulse/portfolio/portfolio_vs_spy_view.py`
- Test: `tests/portfolio/test_portfolio_vs_spy_view.py`

- [ ] **Step 1: Write the failing test**

```python
from marketpulse.portfolio.portfolio_vs_spy_view import (
    VIEWBOX_H,
    VIEWBOX_W,
    _build_chart_data,
)


def _pts(points_str):
    """Parse 'x,y x,y' -> [(x, y), ...] floats."""
    return [tuple(float(c) for c in pair.split(",")) for pair in points_str.split()]


def test_chart_data_shared_index_scale():
    # portfolio rises to 1.06, spy to 1.02. Shared lo/hi => same scale both lines.
    run = [
        _snap(date(2026, 8, 10), port="1.00", spy="1.00", excess="0.00"),
        _snap(date(2026, 8, 11), port="1.06", spy="1.02", excess="0.04"),
    ]
    cd = _build_chart_data(run)
    assert cd.index_lo == Decimal("1.00")
    assert cd.index_hi == Decimal("1.06")
    # Shared scale: the max value (portfolio 1.06) sits at the top (y≈0),
    # the min value (either at 1.00) sits at the bottom (y≈H).
    port = _pts(cd.portfolio_points)
    spy = _pts(cd.spy_points)
    assert port[1][1] == 0.0          # portfolio 1.06 == hi -> top
    assert port[0][1] == VIEWBOX_H    # portfolio 1.00 == lo -> bottom
    # SPY 1.02 is between -> y strictly inside (0, H), proving SHARED scale
    # (if spy were autoscaled to its own 1.00..1.02, 1.02 would be at y=0).
    assert 0.0 < spy[1][1] < VIEWBOX_H


def test_chart_data_x_spans_full_width():
    run = [
        _snap(date(2026, 8, 10), port="1.00", spy="1.00", excess="0.00"),
        _snap(date(2026, 8, 11), port="1.01", spy="1.00", excess="0.01"),
        _snap(date(2026, 8, 12), port="1.02", spy="1.00", excess="0.02"),
    ]
    cd = _build_chart_data(run)
    xs = [x for x, _ in _pts(cd.portfolio_points)]
    assert xs[0] == 0.0
    assert xs[-1] == float(VIEWBOX_W)


def test_chart_data_excess_range_contains_zero_positive_only():
    # All excess > 0: range must still include 0 so the 0-line is on-canvas.
    run = [
        _snap(date(2026, 8, 10), port="1.02", spy="1.00", excess="0.02"),
        _snap(date(2026, 8, 11), port="1.05", spy="1.00", excess="0.05"),
    ]
    cd = _build_chart_data(run)
    assert cd.excess_lo == Decimal("0")
    assert cd.excess_hi == Decimal("0.05")
    # zero is the floor -> 0-line at the bottom (y == H).
    assert cd.zero_y == VIEWBOX_H


def test_chart_data_flat_index_guard_no_div_zero():
    run = [
        _snap(date(2026, 8, 10), port="1.00", spy="1.00", excess="0.00"),
        _snap(date(2026, 8, 11), port="1.00", spy="1.00", excess="0.00"),
    ]
    cd = _build_chart_data(run)  # hi == lo; must not raise
    port = _pts(cd.portfolio_points)
    assert all(y == VIEWBOX_H / 2 for _, y in port)   # mid-line guard


def test_chart_data_all_excess_zero_guard():
    run = [
        _snap(date(2026, 8, 10), port="1.00", spy="1.00", excess="0.00"),
        _snap(date(2026, 8, 11), port="1.02", spy="1.02", excess="0.00"),
    ]
    cd = _build_chart_data(run)  # excess all 0; must not raise
    assert cd.zero_y == VIEWBOX_H / 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/portfolio/test_portfolio_vs_spy_view.py::test_chart_data_shared_index_scale -v`
Expected: FAIL — `ImportError: cannot import name '_build_chart_data'`.

- [ ] **Step 3: Implement `_build_chart_data`**

```python
def _scale_y(value: Decimal, lo: Decimal, hi: Decimal, height: int) -> float:
    """Map value in [lo, hi] to SVG y (grows downward -> invert). Guards lo==hi."""
    if hi == lo:
        return height / 2
    frac = (Decimal(value) - lo) / (hi - lo)
    return height - float(frac) * height


def _build_chart_data(chart_run: list[NavSnapshot]) -> ChartData:
    """Compose SVG polyline strings. Caller guarantees len(chart_run) >= 2 (L2).

    L3: portfolio and SPY share one [lo, hi] index scale.
    L4: the excess scale always contains 0 so the 0-reference line is on-canvas.
    L5: x is computed AFTER downsampling, against the plotted count.
    """
    plotted = _downsample(chart_run)
    n = len(plotted)
    port_vals = [s.portfolio_index for s in plotted]
    spy_vals = [s.spy_index for s in plotted]
    exc_vals = [s.excess_return for s in plotted]

    # Shared index scale (L3).
    lo = min(min(port_vals), min(spy_vals))
    hi = max(max(port_vals), max(spy_vals))

    # Excess scale that always contains 0 (L4).
    elo = min(Decimal("0"), min(exc_vals))
    ehi = max(Decimal("0"), max(exc_vals))
    if ehi == elo:  # all excess == 0 -> degenerate; widen symmetrically.
        elo, ehi = Decimal("-0.0001"), Decimal("0.0001")

    def x_at(i: int) -> float:
        return i / (n - 1) * VIEWBOX_W

    def points(vals: list[Decimal], lo_: Decimal, hi_: Decimal) -> str:
        return " ".join(
            f"{x_at(i):.1f},{_scale_y(v, lo_, hi_, VIEWBOX_H):.1f}"
            for i, v in enumerate(vals)
        )

    zero_y = _scale_y(Decimal("0"), elo, ehi, VIEWBOX_H)

    return ChartData(
        portfolio_points=points(port_vals, lo, hi),
        spy_points=points(spy_vals, lo, hi),
        excess_points=points(exc_vals, elo, ehi),
        zero_y=zero_y,
        index_lo=lo, index_hi=hi,
        excess_lo=elo, excess_hi=ehi,
        viewbox_w=VIEWBOX_W, viewbox_h=VIEWBOX_H,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/portfolio/test_portfolio_vs_spy_view.py -k chart_data -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/portfolio/portfolio_vs_spy_view.py tests/portfolio/test_portfolio_vs_spy_view.py
git commit -m "feat(pr4): _build_chart_data SVG scaling — shared index scale + 0-inclusion (L3,L4,L5)"
```

---

## Task 6: `build_portfolio_vs_spy_view` orchestration + edge cases (E1–E10, L6,L7,L11,L14)

**Files:**
- Modify: `marketpulse/portfolio/portfolio_vs_spy_view.py`
- Test: `tests/portfolio/test_portfolio_vs_spy_view.py`

- [ ] **Step 1: Write the failing test**

```python
from marketpulse.portfolio.portfolio_vs_spy_view import build_portfolio_vs_spy_view


def test_view_e1_empty_series():
    v = build_portfolio_vs_spy_view([])
    assert v.has_data is False
    assert v.chartable is False
    assert v.chart is None
    assert v.show_insufficiency_banner is False
    assert v.hero_excess_return_label == "N/A"
    assert v.coverage_label == "0 / 90"


def test_view_e2_all_spy_none_not_chartable():
    series = [_snap(date(2026, 8, 10 + i), spy=None, excess=None) for i in range(5)]
    v = build_portfolio_vs_spy_view(series)
    assert v.has_data is True
    assert v.chartable is False
    assert v.chart is None
    assert v.chart_start_date is None
    assert v.dropped_prefix_count == 5
    assert v.spy_index_label == "N/A"          # latest has no SPY
    assert v.portfolio_index_label != "N/A"    # portfolio present


def test_view_e3_single_chart_point_not_chartable():
    series = [
        _snap(date(2026, 8, 10), spy=None, excess=None),
        _snap(date(2026, 8, 11)),  # only ONE complete row
    ]
    v = build_portfolio_vs_spy_view(series)
    assert v.chartable is False
    assert v.chart is None
    assert v.hero_excess_return is not None   # hero/KPIs still render


def test_view_e6_latest_missing_spy_na_but_banner_from_sufficiency():
    series = [
        _snap(date(2026, 8, 10), sufficient=True),
        _snap(date(2026, 8, 11), spy=None, excess=None, sufficient=True),
    ]
    v = build_portfolio_vs_spy_view(series)
    assert v.hero_excess_return is None
    assert v.hero_excess_return_label == "N/A"
    assert v.badge == "SUFFICIENT"             # from latest.is_sufficient
    assert v.show_insufficiency_banner is False


def test_view_banner_when_insufficient_but_hero_value_shown():
    series = [
        _snap(date(2026, 8, 10), excess="0.03", sufficient=False),
        _snap(date(2026, 8, 11), excess="0.032", sufficient=False),
    ]
    v = build_portfolio_vs_spy_view(series)
    assert v.show_insufficiency_banner is True
    assert v.badge == "PRELIMINARY"
    assert v.hero_excess_return_label == "+3.2%"   # L7: value never hidden


def test_view_e10_chart_present_points_nonempty():
    series = [
        _snap(date(2026, 8, 10), port="1.00", spy="1.00", excess="0.00"),
        _snap(date(2026, 8, 11), port="1.02", spy="1.00", excess="0.02"),
    ]
    v = build_portfolio_vs_spy_view(series)
    assert v.chartable is True
    assert v.chart is not None
    assert v.chart.portfolio_points != ""
    assert v.chart.spy_points != ""
    assert v.chart.excess_points != ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/portfolio/test_portfolio_vs_spy_view.py::test_view_e1_empty_series -v`
Expected: FAIL — `ImportError: cannot import name 'build_portfolio_vs_spy_view'`.

- [ ] **Step 3: Implement `build_portfolio_vs_spy_view`**

```python
def build_portfolio_vs_spy_view(series: list[NavSnapshot]) -> PortfolioVsSpyView:
    """Map the snapshot series into the view-model. Pure (L1)."""
    if not series:
        return PortfolioVsSpyView(
            has_data=False, chartable=False,
            hero_excess_return=None, hero_excess_return_label=VALUE_NA,
            badge="PRELIMINARY", show_insufficiency_banner=False,
            portfolio_index_latest=None, portfolio_index_label=VALUE_NA,
            spy_index_latest=None, spy_index_label=VALUE_NA,
            coverage_observed=0, coverage_required=NORTH_STAR_WINDOW,
            coverage_label=f"0 / {NORTH_STAR_WINDOW}", is_sufficient=False,
            first_date=None, last_date=None, chart_start_date=None,
            dropped_prefix_count=0, excluded_nonprefix_count=0, chart=None,
        )

    latest = series[-1]
    chart_run, dropped, excluded = _compute_chart_run(series)
    chartable = len(chart_run) >= 2
    chart = _build_chart_data(chart_run) if chartable else None

    return PortfolioVsSpyView(
        has_data=True,
        chartable=chartable,
        hero_excess_return=latest.excess_return,
        hero_excess_return_label=_fmt_excess_label(latest.excess_return),
        badge="SUFFICIENT" if latest.is_sufficient else "PRELIMINARY",
        show_insufficiency_banner=not latest.is_sufficient,
        portfolio_index_latest=latest.portfolio_index,
        portfolio_index_label=_fmt_index_label(latest.portfolio_index),
        spy_index_latest=latest.spy_index,
        spy_index_label=_fmt_index_label(latest.spy_index),
        coverage_observed=latest.trading_days_observed,
        coverage_required=NORTH_STAR_WINDOW,
        coverage_label=f"{latest.trading_days_observed} / {NORTH_STAR_WINDOW}",
        is_sufficient=latest.is_sufficient,
        first_date=series[0].trading_date,
        last_date=latest.trading_date,
        chart_start_date=chart_run[0].trading_date if chart_run else None,
        dropped_prefix_count=dropped,
        excluded_nonprefix_count=excluded,
        chart=chart,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/portfolio/test_portfolio_vs_spy_view.py -v`
Expected: PASS (all presenter tests).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/portfolio/portfolio_vs_spy_view.py tests/portfolio/test_portfolio_vs_spy_view.py
git commit -m "feat(pr4): build_portfolio_vs_spy_view orchestration + edge cases (E1-E10,L6,L7,L11)"
```

---

## Task 7: Route `GET /lab/portfolio-vs-spy` + registration

**Files:**
- Create: `marketpulse/web/routes/portfolio.py`
- Modify: `marketpulse/web/main.py`
- Test: `tests/web/test_portfolio_route.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# Layer: test
"""PR4 — /lab/portfolio-vs-spy route tests."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password


def _login(client: TestClient, monkeypatch) -> None:
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _seed(session, d: date, *, excess="0.03", sufficient=False):
    from marketpulse.portfolio.north_star import NavSnapshot
    from marketpulse.portfolio.snapshot_repo import insert_snapshot
    insert_snapshot(session, NavSnapshot(
        trading_date=d, cash_balance=Decimal("100000"), holdings_mtm=Decimal("0"),
        portfolio_nav=Decimal("103000"), anchor_portfolio_nav=Decimal("100000"),
        portfolio_index=Decimal("1.03"), spy_close=Decimal("500"),
        anchor_spy_close=Decimal("500"), spy_index=Decimal("1.00"),
        excess_return=Decimal(excess), trading_days_observed=42,
        coverage_ratio=Decimal("0.46"), is_sufficient=sufficient,
        unpriced_positions_count=0, unpriced_tickers=(),
    ))


def test_route_requires_auth(client: TestClient):
    r = client.get("/lab/portfolio-vs-spy", headers={"Accept": "application/json"})
    assert r.status_code == 401


def test_route_empty_db_renders_empty_state(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/portfolio-vs-spy")
    assert r.status_code == 200
    assert "No snapshots yet" in r.text


def test_route_insufficient_shows_banner_and_hero(client, monkeypatch, db_url):
    _login(client, monkeypatch)
    monkeypatch.setenv("DATABASE_URL", db_url)
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    engine = create_engine(db_url)
    with Session(engine) as s:
        _seed(s, date(2026, 8, 13))
        _seed(s, date(2026, 8, 14), excess="0.032")
        s.commit()

    r = client.get("/lab/portfolio-vs-spy")
    assert r.status_code == 200
    assert "PRELIMINARY" in r.text          # badge
    assert "+3.2%" in r.text                # hero value still shown (L7)
    assert "<polyline" in r.text            # chart rendered (L14)
    assert "Portfolio" in r.text
    assert "SPY" in r.text
    assert "Excess Return" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_portfolio_route.py -v`
Expected: FAIL — 404 (route not registered) on the authed tests.

- [ ] **Step 3: Create the route**

`marketpulse/web/routes/portfolio.py`:

```python
# Layer: web
"""PR4 — GET /lab/portfolio-vs-spy north-star visualization.

Thin composition root: read snapshots -> pure presenter -> render. No chart
math here (L1 lives in portfolio_vs_spy_view).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from marketpulse.portfolio.portfolio_vs_spy_view import build_portfolio_vs_spy_view
from marketpulse.portfolio.snapshot_repo import get_all_snapshots
from marketpulse.web.deps import get_db, require_auth
from marketpulse.web.main import templates

router = APIRouter()


@router.get("/lab/portfolio-vs-spy", response_class=HTMLResponse)
def lab_portfolio_vs_spy(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    view = build_portfolio_vs_spy_view(get_all_snapshots(db))
    return templates.TemplateResponse(
        "lab_portfolio_vs_spy.html", {"request": request, "view": view},
    )
```

Register in `marketpulse/web/main.py` — add the import alongside the other route imports and the `include_router` call after `charter.router` (line ~168):

```python
    app.include_router(charter.router)
    app.include_router(portfolio.router)
```

Ensure `portfolio` is imported with the other `from marketpulse.web.routes import (...)` (match the existing import style in main.py — add `portfolio` to that import list).

> NOTE: the template doesn't exist yet (Task 8), so the two authed tests will
> still fail at render. That's expected — they go green in Task 8. Verify only
> the auth test now:

- [ ] **Step 4: Run the auth test (the others wait for Task 8)**

Run: `uv run pytest tests/web/test_portfolio_route.py::test_route_requires_auth -v`
Expected: PASS. (The render tests fail until Task 8 — that's fine.)

- [ ] **Step 5: Commit**

```bash
git add marketpulse/web/routes/portfolio.py marketpulse/web/main.py tests/web/test_portfolio_route.py
git commit -m "feat(pr4): /lab/portfolio-vs-spy route + registration (auth test green)"
```

---

## Task 8: Templates — shell + 5 partials (dumb renderer, L11, L14)

**Files:**
- Create: `marketpulse/web/templates/lab_portfolio_vs_spy.html`
- Create: `marketpulse/web/templates/partials/pvs_hero.html`
- Create: `marketpulse/web/templates/partials/pvs_banner.html`
- Create: `marketpulse/web/templates/partials/pvs_kpi_strip.html`
- Create: `marketpulse/web/templates/partials/pvs_index_chart.html`
- Create: `marketpulse/web/templates/partials/pvs_excess_chart.html`
- Test: `tests/web/test_portfolio_route.py` (already written in Task 7)

- [ ] **Step 1: Confirm the failing render tests**

Run: `uv run pytest tests/web/test_portfolio_route.py -v`
Expected: `test_route_empty_db_renders_empty_state` and `test_route_insufficient_shows_banner_and_hero` FAIL (template not found / missing strings).

- [ ] **Step 2: Create the shell `lab_portfolio_vs_spy.html`**

```html
{% extends "base.html" %}
{% block content %}
<h1 class="mp-page-title">Portfolio vs SPY</h1>

{% if not view.has_data %}
  <section class="mp-card"><div class="mp-card__body">
    <p class="muted" style="text-align:center; padding:32px;">
      No snapshots yet — the daily NAV snapshot job hasn't recorded data.
    </p>
  </div></section>
{% else %}
  {% if view.show_insufficiency_banner %}
    {% include "partials/pvs_banner.html" %}
  {% endif %}
  {% include "partials/pvs_hero.html" %}
  {% include "partials/pvs_kpi_strip.html" %}

  {% if view.chartable %}
    {% if view.dropped_prefix_count > 0 %}
      <p class="muted" style="font-size:12px;">
        Chart starts from first snapshot with SPY benchmark data.
      </p>
    {% endif %}
    {% if view.excluded_nonprefix_count > 0 %}
      <p class="muted" style="font-size:12px;">
        Chart truncated at a gap in benchmark data.
      </p>
    {% endif %}
    {% include "partials/pvs_index_chart.html" %}
    {% include "partials/pvs_excess_chart.html" %}
  {% else %}
    <section class="mp-card"><div class="mp-card__body">
      <p class="muted" style="text-align:center; padding:24px;">
        Snapshots exist but benchmark/chart not ready (need ≥2 days with SPY data).
      </p>
    </div></section>
  {% endif %}
{% endif %}
{% endblock %}
```

- [ ] **Step 3: Create `pvs_banner.html`**

```html
<div class="mp-backtest-warning">
  <span class="material-symbols-outlined">warning</span>
  <span><strong>Preliminary Data</strong> — Coverage: {{ view.coverage_label }}
  trading days. The excess-return metric is observable but has not yet met the
  Charter sufficiency threshold (90 trading days). Interpret trends with caution.
  </span>
</div>
```

- [ ] **Step 4: Create `pvs_hero.html`**

```html
<section class="mp-card">
  <div class="mp-card__body" style="display:flex; align-items:baseline; gap:12px;">
    <span class="mp-card__sub">Portfolio Excess Return vs SPY</span>
    <span style="font-size:32px; font-weight:700;">{{ view.hero_excess_return_label }}</span>
    <span class="mp-chip {% if view.is_sufficient %}mp-chip--ok{% else %}mp-chip--warn{% endif %}">
      {{ view.badge }}
    </span>
  </div>
</section>
```

- [ ] **Step 5: Create `pvs_kpi_strip.html`**

```html
<section class="mp-card"><div class="mp-card__body">
  <table class="mp-kpi-table">
    <tr><td>Excess Return</td><td>{{ view.hero_excess_return_label }}</td></tr>
    <tr><td>Portfolio Index</td><td>{{ view.portfolio_index_label }}</td></tr>
    <tr><td>SPY Index</td><td>{{ view.spy_index_label }}</td></tr>
    <tr><td>Coverage</td><td>{{ view.coverage_label }}</td></tr>
    <tr><td>Sufficient</td>
        <td class="{% if not view.is_sufficient %}mp-text-warn{% endif %}">
          {{ "Yes" if view.is_sufficient else "No" }}</td></tr>
  </table>
</div></section>
```

- [ ] **Step 6: Create `pvs_index_chart.html`**

```html
<section class="mp-card">
  <div class="mp-card__head"><span class="mp-card__title">
    <span class="material-symbols-outlined">show_chart</span>Portfolio Index vs SPY Index
  </span></div>
  <div class="mp-card__body">
    <svg viewBox="0 0 {{ view.chart.viewbox_w }} {{ view.chart.viewbox_h }}" width="100%" height="280">
      <polyline points="{{ view.chart.portfolio_points }}" fill="none"
                stroke="#2563eb" stroke-width="2"><title>Portfolio</title></polyline>
      <polyline points="{{ view.chart.spy_points }}" fill="none"
                stroke="#475569" stroke-width="1.5" stroke-dasharray="4 4"><title>SPY</title></polyline>
    </svg>
    <div class="mp-chart-legend">
      <span class="mp-chart-legend__item">
        <span class="mp-chart-legend__swatch" style="background:#2563eb;"></span>Portfolio</span>
      <span class="mp-chart-legend__item">
        <span class="mp-chart-legend__swatch" style="background:#475569;border:1px dashed #475569;"></span>SPY</span>
    </div>
  </div>
</section>
```

- [ ] **Step 7: Create `pvs_excess_chart.html`**

```html
<section class="mp-card">
  <div class="mp-card__head"><span class="mp-card__title">
    <span class="material-symbols-outlined">trending_up</span>Excess Return
  </span></div>
  <div class="mp-card__body">
    <svg viewBox="0 0 {{ view.chart.viewbox_w }} {{ view.chart.viewbox_h }}" width="100%" height="180">
      <line x1="0" y1="{{ '%.1f'|format(view.chart.zero_y) }}"
            x2="{{ view.chart.viewbox_w }}" y2="{{ '%.1f'|format(view.chart.zero_y) }}"
            stroke="var(--ns-outline-variant)" stroke-dasharray="2 4" />
      <polyline points="{{ view.chart.excess_points }}" fill="none"
                stroke="#16a34a" stroke-width="2"><title>Excess Return</title></polyline>
    </svg>
  </div>
</section>
```

- [ ] **Step 8: Run the route render tests**

Run: `uv run pytest tests/web/test_portfolio_route.py -v`
Expected: PASS (3 passed) — empty-state, banner+hero+polyline all present.

- [ ] **Step 9: Commit**

```bash
git add marketpulse/web/templates/lab_portfolio_vs_spy.html marketpulse/web/templates/partials/pvs_*.html tests/web/test_portfolio_route.py
git commit -m "feat(pr4): templates — shell + 5 pvs_ partials (dumb renderer, L11,L14)"
```

---

## Task 9: Nav entry + nav regression check (L10)

**Files:**
- Modify: `marketpulse/web/templates/base.html`

- [ ] **Step 1: Add the nav entry**

In `marketpulse/web/templates/base.html`, insert the north-star entry as the FIRST lab-group link (immediately before the `/lab/ai-track` line):

```html
      <a href="/lab/portfolio-vs-spy" class="{% if p.startswith('/lab/portfolio-vs-spy') %}mp-nav-active{% endif %}">北极星</a>
      <a href="/lab/ai-track" class="{% if p.startswith('/lab/ai-track') %}mp-nav-active{% endif %}">实验室</a>
```

- [ ] **Step 2: Verify nav renders and existing links intact (automated)**

Add to `tests/web/test_portfolio_route.py`:

```python
def test_nav_contains_all_lab_links(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/portfolio-vs-spy")
    assert r.status_code == 200
    for href in (
        "/lab/portfolio-vs-spy", "/lab/ai-track", "/lab/backtest",
        "/lab/paper-trading", "/lab/broker", "/lab/reconcile",
    ):
        assert f'href="{href}"' in r.text
```

Run: `uv run pytest tests/web/test_portfolio_route.py::test_nav_contains_all_lab_links -v`
Expected: PASS — proves the new entry is present AND no existing lab link was dropped.

- [ ] **Step 3: Manual screenshot check (one-time, human verification)**

Start the app locally (or on the deployed instance), open `/lab/portfolio-vs-spy`, and visually confirm:
- the「北极星」entry appears in the nav and is highlighted (`mp-nav-active`),
- clicking the other lab links (实验室 / 回测 / 纸上交易 / 券商 / 对账) still navigates correctly and each highlights its own entry.

Record the result in the PR description (screenshot or a one-line confirmation).

- [ ] **Step 4: Commit**

```bash
git add marketpulse/web/templates/base.html tests/web/test_portfolio_route.py
git commit -m "feat(pr4): nav entry 北极星 leads lab group + nav-integrity test (L10)"
```

---

## Task 10: Final integration — full suite + ruff + smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS (all prior tests + the new PR4 tests; ~1850+ passed). The `# Layer:`-tag pytest hook must accept all new files (each has its tag).

- [ ] **Step 2: Run ruff**

Run: `uv run ruff check .`
Expected: `All checks passed!` (fix any E501/E402/F401 inline, then re-run).

- [ ] **Step 3: Import smoke**

Run: `uv run python -c "import marketpulse.web.main; from marketpulse.portfolio.portfolio_vs_spy_view import build_portfolio_vs_spy_view; print('ok')"`
Expected: `ok` (no circular-import or registration error).

- [ ] **Step 4: Commit any cleanup**

```bash
git add -A
git commit -m "chore(pr4): final integration — full suite green, ruff clean"
```

- [ ] **Step 5: Push + open PR**

```bash
git push -u origin feat/pr4-portfolio-vs-spy
gh pr create --title "PR4: /lab/portfolio-vs-spy north-star visualization" --body "$(cat <<'EOF'
## Summary
- Read-only `/lab/portfolio-vs-spy` page visualizing PR3a's paper_nav_snapshot series
- Dual-index chart (portfolio vs SPY, shared scale) + excess-spread chart (0-reference) + confidence-badged hero
- Pure presenter / thin route / dumb template; no NAV recompute, no migration, no network
- Charter priority #2 (observability before optimization)

## Test plan
- [ ] Presenter unit tests E1–E10 + scaling math
- [ ] Route tests: auth, empty-state, banner+hero+polyline
- [ ] Nav integrity test + manual screenshot of lab nav
- [ ] Full suite green, ruff clean
EOF
)"
```

---

## Self-Review

**Spec coverage:**
- L1 pure presenter → Tasks 2–6 (module has no DB/web imports). ✓
- L2 chart_run + chartable≥2 → Task 3 + Task 6. ✓
- L3 shared index lo/hi → Task 5 (`test_chart_data_shared_index_scale`). ✓
- L4 excess contains 0 → Task 5 (`test_chart_data_excess_range_contains_zero_positive_only`). ✓
- L5 downsample + x-recompute → Task 4 + Task 5 (`test_chart_data_x_spans_full_width`). ✓
- L6 latest-missing-SPY → Task 6 (`test_view_e6_*`). ✓
- L7 banner + hero never hidden → Task 6 (`test_view_banner_when_insufficient_but_hero_value_shown`). ✓
- L8 no range toggle → nothing added (route has no range param). ✓
- L9 read-only, no migration/network → Tasks 1/7 (DB-only read). ✓
- L10 nav leads lab group → Task 9. ✓
- L11 two empty states → Task 8 shell (`has_data` vs `chartable`). ✓
- L12 get_all_snapshots read-only → Task 1 docstring. ✓
- L13 labels in presenter → Task 2; template never formats Decimal (Task 8 uses `*_label`). ✓
- L14 chart None ⇒ no polyline; present ⇒ non-empty → Task 6 (`test_view_e10_*`) + Task 8 shell guards `{% if view.chartable %}`. ✓
- L15 contiguous suffix + prefix/gap accounting → Task 3. ✓
- E1–E10 → Task 6 covers E1/E2/E3/E6/E10; E4/E5 in Task 5; E7/E8 in Task 3. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases" — every step has complete code or an exact command. ✓

**Type consistency:** `build_portfolio_vs_spy_view`, `_compute_chart_run` (3-tuple), `_build_chart_data` → `ChartData`, `_downsample`, `_fmt_excess_label`/`_fmt_index_label`, `get_all_snapshots`, `PortfolioVsSpyView`/`ChartData` field names — all consistent across tasks and match the spec contract. Constants `VIEWBOX_W/H`, `MAX_CHART_POINTS`, `NORTH_STAR_WINDOW` used consistently. ✓
