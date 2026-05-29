# PR3b — Weekly Charter Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a filesystem-only weekly markdown narrative that consumes PR3a's `paper_nav_snapshot` + PR1's backup manifest, generated every Monday 09:30 UTC.

**Architecture:** Three pure-ish layers — frozen dataclasses (`types.py`), DB aggregator, pure renderer — plus a thin orchestration layer that reads the manifest, calls aggregator + renderer, and atomically writes `YYYY-MM-DD.md` + `latest.json` into `/data/recaps/charter/`. A new cron in `marketpulse/scheduler/jobs.py` calls the orchestration entry inside a safe `try/except` wrapper.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, pytest, `dataclasses`, `tempfile`/`os.replace` for atomic writes, APScheduler `CronTrigger`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-29-pr3b-charter-review-weekly-design.md` (commit `122aa32`). 20 scope locks L1–L20.

**Branch:** `feat/pr3b-charter-review`.

---

## File Structure

| Path | Layer | Responsibility |
|---|---|---|
| `marketpulse/ops/charter_review_types.py` (new) | pure | All frozen dataclasses (L18). |
| `marketpulse/ops/charter_review_renderer.py` (new) | pure | `render_charter_review` + private `_fmt_*` and `_section_*` helpers. |
| `marketpulse/ops/charter_review_aggregator.py` (new) | db | `build_payload` + private helpers. DB read-only. |
| `marketpulse/ops/charter_review.py` (new) | orchestration | `generate_charter_review`, `_read_backup_manifest`, `_atomic_write_text`, `CharterReviewError`, success log (L20). |
| `marketpulse/scheduler/jobs.py` (modify) | scheduler | Add `run_charter_review_weekly`, `_last_sunday_on_or_before`, register cron in `build_scheduler`. |
| `tests/ops/test_charter_review_renderer.py` (new) | test | Formatting primitives (pct / int / index / delta) + reason normalization + full section integration. |
| `tests/ops/test_charter_review_aggregator.py` (new) | test | Payload assembly + diagnostics + null-observation handling + L19 normalization. |
| `tests/ops/test_charter_review_orchestration.py` (new) | test | Manifest read + atomic write + `generate_charter_review` flow + rollback. |
| `tests/scheduler/test_charter_review_scheduler.py` (new) | test | Cron-driver isolation + SQLite detection (incl. `sqlite+pysqlite`). |
| `tests/scheduler/test_build_scheduler.py` (modify) | test | Extend daily-critical-jobs lock + add registration test. |

---

## Task 1: `charter_review_types.py` — frozen dataclasses (L18)

**Files:**
- Create: `marketpulse/ops/charter_review_types.py`
- Create: `tests/ops/test_charter_review_types.py`

- [ ] **Step 1: Write the failing import + frozen-instance smoke**

Create `tests/ops/test_charter_review_types.py`:

```python
# Layer: test
"""PR3b — charter_review_types smoke (L18 single-source-of-truth)."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from marketpulse.ops.charter_review_types import (
    CharterReviewPayload,
    DiagnosticsWeek,
    DiagnosticWeek,
    NorthStarWeek,
    OperationalFloor,
    ReasonCount,
    SnapshotAppendix,
    WeekWindow,
)


def _empty_diag() -> DiagnosticWeek:
    return DiagnosticWeek(value=None, observations=0, top_reasons=())


def _empty_diags() -> DiagnosticsWeek:
    return DiagnosticsWeek(
        tick_success_rate=_empty_diag(),
        order_rejection_rate=_empty_diag(),
        paper_trade_count=_empty_diag(),
        engine_invariant_errors=_empty_diag(),
    )


def _empty_window(week_end: date) -> WeekWindow:
    from datetime import timedelta
    return WeekWindow(
        week_start=week_end - timedelta(days=6),
        week_end=week_end,
        trading_days_observed=0,
    )


def _empty_north_star(week_end: date) -> NorthStarWeek:
    return NorthStarWeek(
        week=_empty_window(week_end),
        first_snapshot_date=None,
        last_snapshot_date=None,
        excess_return_end=None,
        portfolio_index_end=None,
        spy_index_end=None,
        coverage_ratio_end=None,
        is_sufficient_end=False,
    )


def _empty_op_floor() -> OperationalFloor:
    return OperationalFloor(
        backup_status="missing", backup_is_stale=True,
        backup_last_at=None, backup_error=None,
        manifest_available=False,
    )


def _empty_appendix() -> SnapshotAppendix:
    return SnapshotAppendix(
        trading_date=None, cash_balance=None, holdings_mtm=None,
        portfolio_nav=None, unpriced_positions_count=0,
        unpriced_tickers=(),
    )


def _empty_payload(week_end: date) -> CharterReviewPayload:
    from datetime import timedelta
    prior_end = week_end - timedelta(days=7)
    return CharterReviewPayload(
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
        week_ending=week_end,
        this_week=_empty_window(week_end),
        prior_week=_empty_window(prior_end),
        north_star_this=_empty_north_star(week_end),
        north_star_prior=_empty_north_star(prior_end),
        diagnostics_this=_empty_diags(),
        diagnostics_prior=_empty_diags(),
        operational_floor=_empty_op_floor(),
        appendix_snapshot=_empty_appendix(),
    )


def test_reason_count_frozen():
    r = ReasonCount(reason="abc", count=3)
    with pytest.raises(FrozenInstanceError):
        r.reason = "xyz"  # type: ignore[misc]


def test_payload_frozen():
    p = _empty_payload(date(2026, 8, 16))
    with pytest.raises(FrozenInstanceError):
        p.week_ending = date(2026, 8, 9)  # type: ignore[misc]


def test_diagnostic_week_value_decimal_or_int_or_none():
    DiagnosticWeek(value=Decimal("0.95"), observations=20, top_reasons=())
    DiagnosticWeek(value=5, observations=5, top_reasons=())
    DiagnosticWeek(value=None, observations=0, top_reasons=())
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/ops/test_charter_review_types.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create the module**

Create `marketpulse/ops/charter_review_types.py`:

```python
# Layer: pure
"""Shared frozen dataclasses for the PR3b charter review pipeline.

L18: aggregator and renderer both import from here. Single source of truth,
no circular imports. No runtime logic — types only.

See docs/superpowers/specs/2026-05-29-pr3b-charter-review-weekly-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class ReasonCount:
    reason: str
    count: int


@dataclass(frozen=True)
class WeekWindow:
    """Calendar week, Monday→Sunday inclusive UTC."""
    week_start: date
    week_end: date
    trading_days_observed: int


@dataclass(frozen=True)
class NorthStarWeek:
    """A north-star view over one week."""
    week: WeekWindow
    first_snapshot_date: date | None
    last_snapshot_date: date | None
    excess_return_end: Decimal | None
    portfolio_index_end: Decimal | None
    spy_index_end: Decimal | None
    coverage_ratio_end: Decimal | None
    is_sufficient_end: bool


@dataclass(frozen=True)
class DiagnosticWeek:
    """One diagnostic over one week. `value` is None when underlying source
    has zero usable observations. See spec L7 / L8 / L19 for semantics."""
    value: Decimal | int | None
    observations: int
    top_reasons: tuple[ReasonCount, ...]


@dataclass(frozen=True)
class DiagnosticsWeek:
    tick_success_rate: DiagnosticWeek
    order_rejection_rate: DiagnosticWeek
    paper_trade_count: DiagnosticWeek
    engine_invariant_errors: DiagnosticWeek


@dataclass(frozen=True)
class OperationalFloor:
    """L14: manifest_available=False →
    backup_status='missing', backup_is_stale=True,
    backup_last_at=None, backup_error=None."""
    backup_status: Literal["ok", "failed", "missing"]
    backup_is_stale: bool
    backup_last_at: str | None
    backup_error: str | None
    manifest_available: bool


@dataclass(frozen=True)
class SnapshotAppendix:
    """L15: filesystem-only appendix view. Money fields exposed here
    are NOT exposed via the PR3a JSON API."""
    trading_date: date | None
    cash_balance: Decimal | None
    holdings_mtm: Decimal | None
    portfolio_nav: Decimal | None
    unpriced_positions_count: int
    unpriced_tickers: tuple[str, ...]


@dataclass(frozen=True)
class CharterReviewPayload:
    generated_at: datetime
    week_ending: date
    this_week: WeekWindow
    prior_week: WeekWindow
    north_star_this: NorthStarWeek
    north_star_prior: NorthStarWeek
    diagnostics_this: DiagnosticsWeek
    diagnostics_prior: DiagnosticsWeek
    operational_floor: OperationalFloor
    appendix_snapshot: SnapshotAppendix
```

- [ ] **Step 4: Verify passing**

Run: `uv run pytest tests/ops/test_charter_review_types.py -v`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/ops/charter_review_types.py tests/ops/test_charter_review_types.py
git commit -m "feat(pr3b): charter_review_types frozen dataclasses (L18)"
```

---

## Task 2: Renderer — formatting primitives (`_fmt_pct`, `_fmt_int`, `_fmt_delta_*`)

**Files:**
- Create: `marketpulse/ops/charter_review_renderer.py`
- Create: `tests/ops/test_charter_review_renderer.py`

- [ ] **Step 1: Write failing primitive tests**

Create `tests/ops/test_charter_review_renderer.py`:

```python
# Layer: test
"""PR3b — renderer formatting tests."""
from __future__ import annotations

from decimal import Decimal

from marketpulse.ops.charter_review_renderer import (
    DELTA_PRIOR_NA,
    VALUE_NA,
    _fmt_delta_index,
    _fmt_delta_int,
    _fmt_delta_pp,
    _fmt_index,
    _fmt_int,
    _fmt_pct,
)


def test_fmt_pct_positive():
    assert _fmt_pct(Decimal("0.032")) == "3.2%"


def test_fmt_pct_negative():
    assert _fmt_pct(Decimal("-0.014")) == "-1.4%"


def test_fmt_pct_none():
    assert _fmt_pct(None) == VALUE_NA


def test_fmt_int_none():
    assert _fmt_int(None) == VALUE_NA


def test_fmt_int_zero():
    assert _fmt_int(0) == "0"


def test_fmt_delta_pp_positive():
    s = _fmt_delta_pp(Decimal("0.032"), Decimal("0.018"))
    assert s == "+1.4 pp vs prior week"


def test_fmt_delta_pp_negative_uses_unicode_minus():
    s = _fmt_delta_pp(Decimal("0.012"), Decimal("0.030"))
    # The spec says renderer prints a unicode minus for visual parity.
    assert s == "−1.8 pp vs prior week"


def test_fmt_delta_pp_prior_na():
    assert _fmt_delta_pp(Decimal("0.032"), None) == DELTA_PRIOR_NA


def test_fmt_delta_pp_both_na():
    assert _fmt_delta_pp(None, None) == DELTA_PRIOR_NA


def test_fmt_delta_int_positive():
    assert _fmt_delta_int(7, 2) == "+5 vs prior week"


def test_fmt_delta_int_negative():
    assert _fmt_delta_int(2, 7) == "−5 vs prior week"


def test_fmt_delta_int_prior_na():
    assert _fmt_delta_int(7, None) == DELTA_PRIOR_NA


def test_fmt_index_basic():
    # Index values are raw multipliers, NOT percents. 1.041 → "1.041".
    assert _fmt_index(Decimal("1.041")) == "1.041"
    assert _fmt_index(Decimal("1")) == "1.000"


def test_fmt_index_none():
    assert _fmt_index(None) == VALUE_NA


def test_fmt_delta_index_positive():
    s = _fmt_delta_index(Decimal("1.041"), Decimal("1.009"))
    assert s == "+0.032 vs prior week"


def test_fmt_delta_index_negative():
    s = _fmt_delta_index(Decimal("1.009"), Decimal("1.041"))
    assert s == "−0.032 vs prior week"


def test_fmt_delta_index_prior_na():
    assert _fmt_delta_index(Decimal("1.041"), None) == DELTA_PRIOR_NA
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/ops/test_charter_review_renderer.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create the renderer skeleton + primitives**

Create `marketpulse/ops/charter_review_renderer.py`:

```python
# Layer: pure
"""PR3b — pure markdown renderer for the weekly charter review.

L9: pure module. No DB, no FS, no clock, no network.
L17: same (payload including generated_at) → byte-identical output.
"""
from __future__ import annotations

from decimal import Decimal

from marketpulse.ops.charter_review_types import (
    CharterReviewPayload,
)

SECTION_SEPARATOR = "\n\n"
REASON_MAX_DISPLAY_LEN = 200
VALUE_NA = "N/A"
DELTA_PRIOR_NA = "prior week N/A"
_MINUS = "−"  # unicode minus sign — typographically matches "+"


def _fmt_pct(value: Decimal | None) -> str:
    """0.032 → '3.2%'; -0.014 → '-1.4%'; None → 'N/A'."""
    if value is None:
        return VALUE_NA
    pct = Decimal(value) * Decimal("100")
    quant = pct.quantize(Decimal("0.1"))
    return f"{quant}%"


def _fmt_int(value: int | None) -> str:
    return VALUE_NA if value is None else f"{int(value)}"


def _fmt_delta_pp(this: Decimal | None, prior: Decimal | None) -> str:
    """Returns '+1.4 pp vs prior week', '−1.8 pp vs prior week',
    or DELTA_PRIOR_NA when prior or this is None."""
    if this is None or prior is None:
        return DELTA_PRIOR_NA
    delta_pp = (Decimal(this) - Decimal(prior)) * Decimal("100")
    delta_pp = delta_pp.quantize(Decimal("0.1"))
    if delta_pp >= 0:
        return f"+{delta_pp} pp vs prior week"
    return f"{_MINUS}{abs(delta_pp)} pp vs prior week"


def _fmt_delta_int(this: int | None, prior: int | None) -> str:
    if this is None or prior is None:
        return DELTA_PRIOR_NA
    delta = int(this) - int(prior)
    if delta >= 0:
        return f"+{delta} vs prior week"
    return f"{_MINUS}{abs(delta)} vs prior week"


def _fmt_index(value: Decimal | None) -> str:
    """Raw index multiplier (NOT percent). 1.041 → '1.041'.
    Use for `portfolio_index`, `spy_index`. NEVER use `_fmt_pct` for these —
    that would render 1.041 as '104.1%' which is meaningless."""
    if value is None:
        return VALUE_NA
    quant = Decimal(value).quantize(Decimal("0.001"))
    return f"{quant}"


def _fmt_delta_index(this: Decimal | None, prior: Decimal | None) -> str:
    if this is None or prior is None:
        return DELTA_PRIOR_NA
    delta = (Decimal(this) - Decimal(prior)).quantize(Decimal("0.001"))
    if delta >= 0:
        return f"+{delta} vs prior week"
    return f"{_MINUS}{abs(delta)} vs prior week"


def render_charter_review(*, payload: CharterReviewPayload) -> str:
    """Pure renderer (L9). To be completed in Task 4."""
    raise NotImplementedError("Task 4 wires up section helpers")
```

- [ ] **Step 4: Verify passing**

Run: `uv run pytest tests/ops/test_charter_review_renderer.py -v`
Expected: PASS — all primitive formatting tests pass (`_fmt_pct`, `_fmt_int`, `_fmt_index`, `_fmt_delta_pp`, `_fmt_delta_int`, `_fmt_delta_index`). `render_charter_review` not exercised yet.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/ops/charter_review_renderer.py tests/ops/test_charter_review_renderer.py
git commit -m "feat(pr3b): renderer formatting primitives (pct/int/delta)"
```

---

## Task 3: Renderer — `_fmt_reason` (L16) + reason tests

**Files:**
- Modify: `marketpulse/ops/charter_review_renderer.py`
- Modify: `tests/ops/test_charter_review_renderer.py`

- [ ] **Step 1: Append failing reason tests**

Append to `tests/ops/test_charter_review_renderer.py`:

```python
from marketpulse.ops.charter_review_renderer import (
    REASON_MAX_DISPLAY_LEN,
    _fmt_reason,
)


def test_fmt_reason_strips_newlines_and_carriage_returns():
    assert _fmt_reason("a\nb\rc") == "a b c"


def test_fmt_reason_escapes_pipe():
    # input is the literal 3-char string "a|b"; output is the 4-char "a\|b"
    # which Python literal expresses as "a\\|b".
    assert _fmt_reason("a|b") == "a\\|b"


def test_fmt_reason_truncates_long_input():
    src = "x" * (REASON_MAX_DISPLAY_LEN + 50)
    out = _fmt_reason(src)
    # First 200 chars preserved + ellipsis appended.
    assert out == "x" * REASON_MAX_DISPLAY_LEN + "…"


def test_fmt_reason_normalization_order_locked():
    # Replace newline first (becomes space), THEN escape pipe, THEN truncate.
    # Construct a string where each transform matters in order.
    src = "a|b\nc" + ("z" * REASON_MAX_DISPLAY_LEN)
    out = _fmt_reason(src)
    # After step 1: "a|b c" + zzzz...
    # After step 2: "a\|b c" + zzzz... (5 + 1 + 200 = 206 chars before truncate)
    # After step 3: first 200 chars + ellipsis.
    assert out.endswith("…")
    assert "a\\|b c" in out
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/ops/test_charter_review_renderer.py -v`
Expected: FAIL — `_fmt_reason` not defined.

- [ ] **Step 3: Implement `_fmt_reason`**

Insert into `marketpulse/ops/charter_review_renderer.py` AFTER all formatting primitives (`_fmt_pct`, `_fmt_int`, `_fmt_delta_pp`, `_fmt_delta_int`, `_fmt_index`, `_fmt_delta_index`) and BEFORE the section helpers (`_section_*`, added in Task 4):

```python
def _fmt_reason(reason: str) -> str:
    """L16 normalization order (locked):
      1. replace any '\\n' or '\\r' with a single space
      2. escape '|' as '\\|' (preserves markdown table grammar)
      3. truncate to REASON_MAX_DISPLAY_LEN chars + '…' if longer

    The aggregator is responsible for converting NULL/empty reasons to
    the literal '(no reason)' (L19) BEFORE this function is called.
    """
    normalized = reason.replace("\n", " ").replace("\r", " ")
    escaped = normalized.replace("|", "\\|")
    if len(escaped) > REASON_MAX_DISPLAY_LEN:
        return escaped[:REASON_MAX_DISPLAY_LEN] + "…"
    return escaped
```

- [ ] **Step 4: Verify passing**

Run: `uv run pytest tests/ops/test_charter_review_renderer.py -v`
Expected: PASS — 16 tests now (12 primitive + 4 reason).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/ops/charter_review_renderer.py tests/ops/test_charter_review_renderer.py
git commit -m "feat(pr3b): renderer _fmt_reason normalization (L16)"
```

---

## Task 4: Renderer — section helpers + `render_charter_review` full integration

**Files:**
- Modify: `marketpulse/ops/charter_review_renderer.py`
- Modify: `tests/ops/test_charter_review_renderer.py`

- [ ] **Step 1: Append failing integration tests**

Append to `tests/ops/test_charter_review_renderer.py`:

```python
from datetime import UTC, date, datetime

from marketpulse.ops.charter_review_renderer import render_charter_review
from marketpulse.ops.charter_review_types import (
    CharterReviewPayload,
    DiagnosticsWeek,
    DiagnosticWeek,
    NorthStarWeek,
    OperationalFloor,
    ReasonCount,
    SnapshotAppendix,
    WeekWindow,
)


def _diag(value=None, observations=0, top_reasons=()):
    return DiagnosticWeek(
        value=value, observations=observations, top_reasons=tuple(top_reasons),
    )


def _diags(tick=None, rej=None, count=None, eng=None):
    return DiagnosticsWeek(
        tick_success_rate=tick or _diag(),
        order_rejection_rate=rej or _diag(),
        paper_trade_count=count or _diag(),
        engine_invariant_errors=eng or _diag(),
    )


def _week(monday: date, days_observed: int = 0) -> WeekWindow:
    sunday = date.fromordinal(monday.toordinal() + 6)
    return WeekWindow(
        week_start=monday, week_end=sunday,
        trading_days_observed=days_observed,
    )


def _ns(week: WeekWindow, *, excess_return=None, portfolio_index=None,
        spy_index=None, coverage_ratio=None, is_sufficient=False,
        first=None, last=None) -> NorthStarWeek:
    return NorthStarWeek(
        week=week, first_snapshot_date=first, last_snapshot_date=last,
        excess_return_end=excess_return,
        portfolio_index_end=portfolio_index,
        spy_index_end=spy_index,
        coverage_ratio_end=coverage_ratio,
        is_sufficient_end=is_sufficient,
    )


def _op(*, manifest_available=False, backup_status="missing",
        backup_is_stale=True, backup_last_at=None, backup_error=None) -> OperationalFloor:
    return OperationalFloor(
        backup_status=backup_status, backup_is_stale=backup_is_stale,
        backup_last_at=backup_last_at, backup_error=backup_error,
        manifest_available=manifest_available,
    )


def _appendix(*, trading_date=None, cash_balance=None, holdings_mtm=None,
              portfolio_nav=None, unpriced_count=0, tickers=()) -> SnapshotAppendix:
    return SnapshotAppendix(
        trading_date=trading_date, cash_balance=cash_balance,
        holdings_mtm=holdings_mtm, portfolio_nav=portfolio_nav,
        unpriced_positions_count=unpriced_count,
        unpriced_tickers=tuple(tickers),
    )


def _payload(*, week_ending=date(2026, 8, 16), generated_at=None,
             this_week=None, prior_week=None,
             ns_this=None, ns_prior=None,
             diags_this=None, diags_prior=None,
             op=None, app=None) -> CharterReviewPayload:
    monday = date.fromordinal(week_ending.toordinal() - 6)
    prior_monday = date.fromordinal(monday.toordinal() - 7)
    return CharterReviewPayload(
        generated_at=generated_at or datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
        week_ending=week_ending,
        this_week=this_week or _week(monday),
        prior_week=prior_week or _week(prior_monday),
        north_star_this=ns_this or _ns(this_week or _week(monday)),
        north_star_prior=ns_prior or _ns(prior_week or _week(prior_monday)),
        diagnostics_this=diags_this or _diags(),
        diagnostics_prior=diags_prior or _diags(),
        operational_floor=op or _op(),
        appendix_snapshot=app or _appendix(),
    )


def test_render_includes_locked_sections():
    out = render_charter_review(payload=_payload())
    for header in (
        "# Charter Review",
        "## Executive Summary",
        "## North Star",
        "## Diagnostics",
        "## Operational Floor",
        "## Appendix",
    ):
        assert header in out


def test_render_minimal_payload_byte_identical():
    p = _payload()
    assert render_charter_review(payload=p) == render_charter_review(payload=p)


def test_render_this_week_empty():
    out = render_charter_review(payload=_payload())
    assert "No snapshots in this calendar week." in out


def test_render_both_weeks_empty_still_writes_shell():
    out = render_charter_review(payload=_payload())
    # Even all-empty payload renders all section headers.
    assert "## Diagnostics" in out
    assert "## Operational Floor" in out


def test_render_manifest_unavailable():
    out = render_charter_review(payload=_payload(
        op=_op(manifest_available=False),
    ))
    assert "Backup manifest unavailable" in out


def test_render_appendix_money_fields_present_when_set():
    from decimal import Decimal
    app = _appendix(
        trading_date=date(2026, 8, 14),
        cash_balance=Decimal("100000"),
        holdings_mtm=Decimal("2200"),
        portfolio_nav=Decimal("102200"),
    )
    out = render_charter_review(payload=_payload(app=app))
    assert "Cash balance: 100000" in out
    assert "Holdings MTM: 2200" in out
    assert "Portfolio NAV: 102200" in out
    assert "Trading date: 2026-08-14" in out
```

- [ ] **Step 2: Implement section helpers + body**

Replace the `raise NotImplementedError` body of `render_charter_review` and add the helpers above it. Insert these into `marketpulse/ops/charter_review_renderer.py` (replace the existing `render_charter_review` stub):

```python
def _section_header(payload: CharterReviewPayload) -> str:
    this = payload.this_week
    prior = payload.prior_week
    return (
        f"# Charter Review — Week Ending {payload.week_ending.isoformat()}\n"
        f"\n"
        f"Generated: {payload.generated_at.isoformat()}\n"
        f"This week: {this.week_start.isoformat()} → {this.week_end.isoformat()} "
        f"({this.trading_days_observed} trading days)\n"
        f"Prior week: {prior.week_start.isoformat()} → {prior.week_end.isoformat()} "
        f"({prior.trading_days_observed} trading days)"
    )


def _section_executive_summary(payload: CharterReviewPayload) -> str:
    lines: list[str] = ["## Executive Summary", ""]
    if payload.this_week.trading_days_observed == 0:
        lines.append("- No snapshots in this calendar week.")
    ns_this = payload.north_star_this
    ns_prior = payload.north_star_prior
    diag_this = payload.diagnostics_this
    diag_prior = payload.diagnostics_prior
    cov_str = (
        f"coverage {ns_this.week.trading_days_observed}/90 trading days"
        if ns_this.week.trading_days_observed
        else "no coverage data this week"
    )
    lines.append(
        f"- Portfolio excess return: {_fmt_pct(ns_this.excess_return_end)} "
        f"({_fmt_delta_pp(ns_this.excess_return_end, ns_prior.excess_return_end)}) "
        f"— {cov_str}",
    )
    lines.append(
        f"- Tick success rate: {_fmt_pct(diag_this.tick_success_rate.value)} "
        f"({_fmt_delta_pp(diag_this.tick_success_rate.value, diag_prior.tick_success_rate.value)})",
    )
    lines.append(
        f"- Order rejection rate: {_fmt_pct(diag_this.order_rejection_rate.value)} "
        f"({_fmt_delta_pp(diag_this.order_rejection_rate.value, diag_prior.order_rejection_rate.value)})",
    )
    lines.append(
        f"- Paper entry fills: {_fmt_int(diag_this.paper_trade_count.value)} "
        f"({_fmt_delta_int(diag_this.paper_trade_count.value, diag_prior.paper_trade_count.value)})",
    )
    op = payload.operational_floor
    is_stale_str = "stale" if op.backup_is_stale else "fresh"
    lines.append(f"- Backup status: {op.backup_status} ({is_stale_str})")
    return "\n".join(lines)


def _fmt_optional_date(d):  # date | None
    return "N/A" if d is None else d.isoformat()


def _section_north_star(payload: CharterReviewPayload) -> str:
    this = payload.north_star_this
    prior = payload.north_star_prior
    lines = [
        "## North Star",
        "",
        "Metric: `paper_portfolio_excess_return_vs_spy_90d`",
        "",
        "|                          | This week    | Prior week  | Δ            |",
        "|--------------------------|--------------|-------------|--------------|",
        f"| Excess return            | {_fmt_pct(this.excess_return_end):<12} "
        f"| {_fmt_pct(prior.excess_return_end):<11} "
        f"| {_fmt_delta_pp(this.excess_return_end, prior.excess_return_end):<12} |",
        f"| Portfolio index          | {_fmt_index(this.portfolio_index_end):<12} "
        f"| {_fmt_index(prior.portfolio_index_end):<11} "
        f"| {_fmt_delta_index(this.portfolio_index_end, prior.portfolio_index_end):<12} |",
        f"| SPY index                | {_fmt_index(this.spy_index_end):<12} "
        f"| {_fmt_index(prior.spy_index_end):<11} "
        f"| {_fmt_delta_index(this.spy_index_end, prior.spy_index_end):<12} |",
        f"| Coverage                 | {this.week.trading_days_observed}/90 days   "
        f"| {prior.week.trading_days_observed}/90 days  "
        f"| {_fmt_delta_int(this.week.trading_days_observed, prior.week.trading_days_observed):<12} |",
        f"| Statistically sufficient | {str(this.is_sufficient_end):<12} "
        f"| {str(prior.is_sufficient_end):<11} | —            |",
        "",
        f"Observation window: first snapshot {_fmt_optional_date(this.first_snapshot_date)}, "
        f"last snapshot {_fmt_optional_date(this.last_snapshot_date)}.",
    ]
    return "\n".join(lines)


def _fmt_top_reasons_line(top_reasons) -> str:
    if not top_reasons:
        return "(none)"
    items = [f"{_fmt_reason(rc.reason)} ({rc.count})" for rc in top_reasons]
    return ", ".join(items)


def _section_diagnostics(payload: CharterReviewPayload) -> str:
    dt = payload.diagnostics_this
    dp = payload.diagnostics_prior
    parts = ["## Diagnostics", ""]

    parts.append("### Tick success rate")
    parts.append(f"- This week: {_fmt_pct(dt.tick_success_rate.value)} "
                 f"({dt.tick_success_rate.observations} observations)")
    parts.append(f"- Prior week: {_fmt_pct(dp.tick_success_rate.value)} "
                 f"({dp.tick_success_rate.observations} observations)")
    parts.append(f"- Δ: {_fmt_delta_pp(dt.tick_success_rate.value, dp.tick_success_rate.value)}")
    parts.append(f"- Top failure reasons this week: "
                 f"{_fmt_top_reasons_line(dt.tick_success_rate.top_reasons)}")
    parts.append("")

    parts.append("### Order rejection rate")
    parts.append(f"- This week: {_fmt_pct(dt.order_rejection_rate.value)} "
                 f"({dt.order_rejection_rate.observations} observations)")
    parts.append(f"- Prior week: {_fmt_pct(dp.order_rejection_rate.value)} "
                 f"({dp.order_rejection_rate.observations} observations)")
    parts.append(f"- Δ: {_fmt_delta_pp(dt.order_rejection_rate.value, dp.order_rejection_rate.value)}")
    parts.append(f"- Top rejection reasons this week: "
                 f"{_fmt_top_reasons_line(dt.order_rejection_rate.top_reasons)}")
    parts.append("")

    parts.append("### Paper entry fills")
    parts.append(f"- This week: {_fmt_int(dt.paper_trade_count.value)}")
    parts.append(f"- Prior week: {_fmt_int(dp.paper_trade_count.value)}")
    parts.append(f"- Δ: {_fmt_delta_int(dt.paper_trade_count.value, dp.paper_trade_count.value)}")
    parts.append("")

    parts.append("### Engine invariant errors")
    parts.append(f"- This week: {_fmt_int(dt.engine_invariant_errors.value)}")
    parts.append(f"- Prior week: {_fmt_int(dp.engine_invariant_errors.value)}")
    parts.append(f"- Δ: {_fmt_delta_int(dt.engine_invariant_errors.value, dp.engine_invariant_errors.value)}")
    parts.append(f"- Top reasons this week: "
                 f"{_fmt_top_reasons_line(dt.engine_invariant_errors.top_reasons)}")
    return "\n".join(parts)


def _section_operational_floor(payload: CharterReviewPayload) -> str:
    op = payload.operational_floor
    lines = ["## Operational Floor", ""]
    if not op.manifest_available:
        lines.append("- Backup manifest unavailable")
        lines.append(f"- Backup status: {op.backup_status}")
        lines.append(f"- Stale (>25h): {op.backup_is_stale}")
        return "\n".join(lines)
    lines.append(f"- Backup status: {op.backup_status}")
    lines.append(f"- Last successful backup: "
                 f"{op.backup_last_at if op.backup_last_at else VALUE_NA}")
    lines.append(f"- Stale (>25h): {op.backup_is_stale}")
    lines.append(f"- Error (if any): {op.backup_error if op.backup_error else 'none'}")
    return "\n".join(lines)


def _fmt_optional_money(value) -> str:
    return VALUE_NA if value is None else f"{value}"


def _section_appendix(payload: CharterReviewPayload) -> str:
    app = payload.appendix_snapshot
    lines = ["## Appendix — Raw snapshot (end of this week)", ""]
    lines.append(f"- Trading date: {_fmt_optional_date(app.trading_date)}")
    lines.append(f"- Cash balance: {_fmt_optional_money(app.cash_balance)}")
    lines.append(f"- Holdings MTM: {_fmt_optional_money(app.holdings_mtm)}")
    lines.append(f"- Portfolio NAV: {_fmt_optional_money(app.portfolio_nav)}")
    tickers = ", ".join(app.unpriced_tickers) if app.unpriced_tickers else "none"
    lines.append(
        f"- Unpriced positions: {app.unpriced_positions_count} ({tickers})",
    )
    return "\n".join(lines)


def render_charter_review(*, payload: CharterReviewPayload) -> str:
    """Pure renderer (L9). Deterministic — same payload → byte-identical (L17)."""
    sections = [
        _section_header(payload),
        _section_executive_summary(payload),
        _section_north_star(payload),
        _section_diagnostics(payload),
        _section_operational_floor(payload),
        _section_appendix(payload),
    ]
    return SECTION_SEPARATOR.join(sections)
```

- [ ] **Step 3: Verify passing**

Run: `uv run pytest tests/ops/test_charter_review_renderer.py -v`
Expected: PASS — 22 tests total.

- [ ] **Step 4: Commit**

```bash
git add marketpulse/ops/charter_review_renderer.py tests/ops/test_charter_review_renderer.py
git commit -m "feat(pr3b): renderer section helpers + full integration"
```

---

## Task 5: Aggregator — `_week_window` + `build_payload` skeleton + empty-DB test

**Files:**
- Create: `marketpulse/ops/charter_review_aggregator.py`
- Create: `tests/ops/test_charter_review_aggregator.py`

- [ ] **Step 1: Write failing tests**

Create `tests/ops/test_charter_review_aggregator.py`:

```python
# Layer: test
"""PR3b — charter_review_aggregator tests."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from marketpulse.ops.charter_review_aggregator import (
    _week_window,
    build_payload,
)
from marketpulse.ops.charter_review_types import CharterReviewPayload


def test_week_window_sunday_to_monday():
    """Sunday Aug 16 2026 → week_start Mon Aug 10."""
    w = _week_window(date(2026, 8, 16))
    assert w.week_start == date(2026, 8, 10)
    assert w.week_end == date(2026, 8, 16)
    assert w.trading_days_observed == 0  # filled later


def test_build_payload_empty_db(db_session):
    payload = build_payload(
        session=db_session,
        week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    assert isinstance(payload, CharterReviewPayload)
    assert payload.week_ending == date(2026, 8, 16)
    assert payload.this_week.trading_days_observed == 0
    assert payload.prior_week.trading_days_observed == 0
    # All diagnostic values None on empty DB.
    for d in (
        payload.diagnostics_this.tick_success_rate,
        payload.diagnostics_this.order_rejection_rate,
        payload.diagnostics_this.paper_trade_count,
        payload.diagnostics_this.engine_invariant_errors,
    ):
        assert d.value is None
        assert d.observations == 0
        assert d.top_reasons == ()
    # Manifest None → L14
    op = payload.operational_floor
    assert op.manifest_available is False
    assert op.backup_status == "missing"
    assert op.backup_is_stale is True
    assert op.backup_last_at is None
    assert op.backup_error is None
    # Appendix empty.
    assert payload.appendix_snapshot.trading_date is None
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/ops/test_charter_review_aggregator.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create the aggregator module**

Create `marketpulse/ops/charter_review_aggregator.py`:

```python
# Layer: db
"""PR3b — DB aggregator for the weekly charter review.

L3: reads paper_nav_snapshot, paper_audit_event, paper_fill only.
Never reads paper_position or paper_cash_ledger.
Manifest is INPUT — never read from filesystem here.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from marketpulse.db.models import (
    PaperAuditEvent,
    PaperFill,
    PaperNavSnapshot,
)
from marketpulse.ops.charter_review_types import (
    CharterReviewPayload,
    DiagnosticsWeek,
    DiagnosticWeek,
    NorthStarWeek,
    OperationalFloor,
    ReasonCount,
    SnapshotAppendix,
    WeekWindow,
)

NO_REASON = "(no reason)"   # L19


def _week_window(week_ending: date) -> WeekWindow:
    """L2: Sunday week_end → Monday week_start (6 days back).
    trading_days_observed is filled later by _populate_trading_days."""
    week_start = week_ending - timedelta(days=6)
    return WeekWindow(
        week_start=week_start, week_end=week_ending, trading_days_observed=0,
    )


def _eod_window(week: WeekWindow) -> tuple[datetime, datetime]:
    start = datetime.combine(week.week_start, time.min, tzinfo=UTC)
    end = datetime.combine(week.week_end, time.max, tzinfo=UTC)
    return start, end


def _trading_days_observed(session: Session, week: WeekWindow) -> int:
    return int(session.scalar(
        select(func.count(PaperNavSnapshot.trading_date))
        .where(and_(
            PaperNavSnapshot.trading_date >= week.week_start,
            PaperNavSnapshot.trading_date <= week.week_end,
        )),
    ) or 0)


def _build_north_star_for_week(
    session: Session, week: WeekWindow,
) -> NorthStarWeek:
    days = _trading_days_observed(session, week)
    week_with_days = WeekWindow(
        week_start=week.week_start, week_end=week.week_end,
        trading_days_observed=days,
    )
    if days == 0:
        return NorthStarWeek(
            week=week_with_days,
            first_snapshot_date=None, last_snapshot_date=None,
            excess_return_end=None, portfolio_index_end=None,
            spy_index_end=None, coverage_ratio_end=None,
            is_sufficient_end=False,
        )
    rows = session.scalars(
        select(PaperNavSnapshot)
        .where(and_(
            PaperNavSnapshot.trading_date >= week.week_start,
            PaperNavSnapshot.trading_date <= week.week_end,
        ))
        .order_by(PaperNavSnapshot.trading_date.asc()),
    ).all()
    first = rows[0]
    last = rows[-1]
    return NorthStarWeek(
        week=week_with_days,
        first_snapshot_date=first.trading_date,
        last_snapshot_date=last.trading_date,
        excess_return_end=last.excess_return,
        portfolio_index_end=last.portfolio_index,
        spy_index_end=last.spy_index,
        coverage_ratio_end=last.coverage_ratio,
        is_sufficient_end=last.is_sufficient,
    )


def _empty_diagnostic() -> DiagnosticWeek:
    return DiagnosticWeek(value=None, observations=0, top_reasons=())


def _empty_diagnostics() -> DiagnosticsWeek:
    return DiagnosticsWeek(
        tick_success_rate=_empty_diagnostic(),
        order_rejection_rate=_empty_diagnostic(),
        paper_trade_count=_empty_diagnostic(),
        engine_invariant_errors=_empty_diagnostic(),
    )


def _operational_floor(manifest: dict | None) -> OperationalFloor:
    """L14: None or malformed manifest → missing + stale + Nones."""
    if not manifest:
        return OperationalFloor(
            backup_status="missing", backup_is_stale=True,
            backup_last_at=None, backup_error=None,
            manifest_available=False,
        )
    raw_status = manifest.get("status")
    if raw_status not in {"ok", "failed", "missing"}:
        return OperationalFloor(
            backup_status="missing", backup_is_stale=True,
            backup_last_at=None, backup_error=None,
            manifest_available=False,
        )
    return OperationalFloor(
        backup_status=raw_status,
        backup_is_stale=bool(manifest.get("is_stale", True)),
        backup_last_at=manifest.get("last_backup_at"),
        backup_error=manifest.get("error"),
        manifest_available=True,
    )


def _appendix_snapshot(session: Session, week: WeekWindow) -> SnapshotAppendix:
    """L15: latest snapshot in week. All None when none exist."""
    row = session.scalars(
        select(PaperNavSnapshot)
        .where(and_(
            PaperNavSnapshot.trading_date >= week.week_start,
            PaperNavSnapshot.trading_date <= week.week_end,
        ))
        .order_by(PaperNavSnapshot.trading_date.desc())
        .limit(1),
    ).first()
    if row is None:
        return SnapshotAppendix(
            trading_date=None, cash_balance=None, holdings_mtm=None,
            portfolio_nav=None, unpriced_positions_count=0,
            unpriced_tickers=(),
        )
    tickers_raw = row.unpriced_tickers
    tickers = tuple(tickers_raw.split(",")) if tickers_raw else ()
    return SnapshotAppendix(
        trading_date=row.trading_date,
        cash_balance=row.cash_balance,
        holdings_mtm=row.holdings_mtm,
        portfolio_nav=row.portfolio_nav,
        unpriced_positions_count=row.unpriced_positions_count,
        unpriced_tickers=tickers,
    )


def build_payload(
    *,
    session: Session,
    week_ending: date,
    backup_manifest: dict | None,
    generated_at: datetime,
) -> CharterReviewPayload:
    """Build the payload. Diagnostics are stubbed empty in Task 5;
    populated in Task 6."""
    this_window = _week_window(week_ending)
    prior_window = _week_window(week_ending - timedelta(days=7))
    return CharterReviewPayload(
        generated_at=generated_at,
        week_ending=week_ending,
        this_week=WeekWindow(
            week_start=this_window.week_start,
            week_end=this_window.week_end,
            trading_days_observed=_trading_days_observed(session, this_window),
        ),
        prior_week=WeekWindow(
            week_start=prior_window.week_start,
            week_end=prior_window.week_end,
            trading_days_observed=_trading_days_observed(session, prior_window),
        ),
        north_star_this=_build_north_star_for_week(session, this_window),
        north_star_prior=_build_north_star_for_week(session, prior_window),
        diagnostics_this=_empty_diagnostics(),       # populated in Task 6
        diagnostics_prior=_empty_diagnostics(),       # populated in Task 6
        operational_floor=_operational_floor(backup_manifest),
        appendix_snapshot=_appendix_snapshot(session, this_window),
    )
```

- [ ] **Step 4: Verify passing**

Run: `uv run pytest tests/ops/test_charter_review_aggregator.py -v`
Expected: PASS — 2 tests pass (`test_week_window_sunday_to_monday`, `test_build_payload_empty_db`).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/ops/charter_review_aggregator.py tests/ops/test_charter_review_aggregator.py
git commit -m "feat(pr3b): aggregator skeleton + week_window + north_star + appendix"
```

---

## Task 6: Aggregator — diagnostics (rate metrics + trade count + engine errors + L19)

**Files:**
- Modify: `marketpulse/ops/charter_review_aggregator.py`
- Modify: `tests/ops/test_charter_review_aggregator.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/ops/test_charter_review_aggregator.py`:

```python
from marketpulse.db.models import (
    PaperAuditEvent,
    PaperFill,
    PaperNavSnapshot,
    PaperOrder,
    PaperPosition,
)


def _seed_snapshot(session, d: date, **overrides):
    row = PaperNavSnapshot(
        trading_date=d,
        cash_balance=overrides.get("cash_balance", Decimal("100000")),
        holdings_mtm=overrides.get("holdings_mtm", Decimal("0")),
        portfolio_nav=overrides.get("portfolio_nav", Decimal("100000")),
        anchor_portfolio_nav=overrides.get("anchor_portfolio_nav", Decimal("100000")),
        portfolio_index=overrides.get("portfolio_index", Decimal("1")),
        spy_close=overrides.get("spy_close"),
        anchor_spy_close=overrides.get("anchor_spy_close"),
        spy_index=overrides.get("spy_index"),
        excess_return=overrides.get("excess_return"),
        trading_days_observed=overrides.get("trading_days_observed", 1),
        coverage_ratio=overrides.get("coverage_ratio", Decimal("0.011")),
        is_sufficient=overrides.get("is_sufficient", False),
        unpriced_positions_count=overrides.get("unpriced_positions_count", 0),
        unpriced_tickers=overrides.get("unpriced_tickers"),
        created_at=datetime(d.year, d.month, d.day, tzinfo=UTC),
        updated_at=datetime(d.year, d.month, d.day, tzinfo=UTC),
        is_rebuilt=False,
        rebuild_reason=None,
    )
    session.add(row)
    session.flush()


def _seed_audit(session, *, ts: datetime, event_type: str, reason: str = ""):
    session.add(PaperAuditEvent(
        timestamp=ts, event_type=event_type,
        order_id=None, strategy=None, reason=reason, context={},
    ))


def test_build_payload_trading_days_observed(db_session):
    # This week is Aug 10-16. Prior week is Aug 3-9.
    for d in (date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12)):
        _seed_snapshot(db_session, d)
    for d in (date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5),
              date(2026, 8, 6), date(2026, 8, 7)):
        _seed_snapshot(db_session, d)
    db_session.commit()

    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    assert payload.this_week.trading_days_observed == 3
    assert payload.prior_week.trading_days_observed == 5


def test_build_payload_week_window_inclusive(db_session):
    _seed_snapshot(db_session, date(2026, 8, 10))   # Mon
    _seed_snapshot(db_session, date(2026, 8, 16))   # Sun
    _seed_snapshot(db_session, date(2026, 8, 17))   # next-Mon, excluded
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    assert payload.this_week.trading_days_observed == 2


def test_build_payload_north_star_first_last(db_session):
    _seed_snapshot(db_session, date(2026, 8, 10),
                   excess_return=Decimal("0.005"), portfolio_index=Decimal("1.005"))
    _seed_snapshot(db_session, date(2026, 8, 13),
                   excess_return=Decimal("0.018"), portfolio_index=Decimal("1.018"))
    _seed_snapshot(db_session, date(2026, 8, 14),
                   excess_return=Decimal("0.032"), portfolio_index=Decimal("1.041"))
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    ns = payload.north_star_this
    assert ns.first_snapshot_date == date(2026, 8, 10)
    assert ns.last_snapshot_date == date(2026, 8, 14)
    assert ns.excess_return_end == Decimal("0.032")


def test_build_payload_tick_success_rate(db_session):
    base = datetime(2026, 8, 10, tzinfo=UTC)
    for i in range(18):
        _seed_audit(db_session, ts=base + timedelta(hours=i),
                    event_type="TICK_COMPLETED")
    for i in range(2):
        _seed_audit(db_session, ts=base + timedelta(days=1, hours=i),
                    event_type="ENGINE_INVARIANT_ERROR", reason="allocator_failed")
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    diag = payload.diagnostics_this.tick_success_rate
    assert diag.value == Decimal("18") / Decimal("20")
    assert diag.observations == 20
    assert len(diag.top_reasons) == 1
    assert diag.top_reasons[0].reason == "allocator_failed"
    assert diag.top_reasons[0].count == 2


def test_build_payload_rejection_top_reasons_sorted(db_session):
    base = datetime(2026, 8, 10, tzinfo=UTC)
    # placed:10, rejected:(a:5, b:3, c:3, d:1, e:1)
    for i in range(10):
        _seed_audit(db_session, ts=base + timedelta(minutes=i),
                    event_type="ORDER_PLACED")
    plan = (("a", 5), ("b", 3), ("c", 3), ("d", 1), ("e", 1))
    j = 0
    for reason, n in plan:
        for _ in range(n):
            _seed_audit(db_session, ts=base + timedelta(hours=1, minutes=j),
                        event_type="ORDER_REJECTED", reason=reason)
            j += 1
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    diag = payload.diagnostics_this.order_rejection_rate
    # Top 3 by (count desc, reason asc): a(5), b(3), c(3)
    assert tuple((r.reason, r.count) for r in diag.top_reasons) == (
        ("a", 5), ("b", 3), ("c", 3),
    )
    # decisions = 10 placed + 13 rejected = 23 → rejected/decisions
    assert diag.value == Decimal("13") / Decimal("23")
    assert diag.observations == 23


def test_build_payload_trade_count_uses_fills(db_session):
    # Seed a snapshot so observations > 0 (otherwise value=None per spec).
    _seed_snapshot(db_session, date(2026, 8, 10))
    base = datetime(2026, 8, 10, tzinfo=UTC)
    # Audit ORDER_ENTRY_FILLED present but NO paper_fill ENTRY rows.
    _seed_audit(db_session, ts=base, event_type="ORDER_ENTRY_FILLED")
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    # L5: source is paper_fill, not audit event. Snapshot exists → obs>0.
    assert payload.diagnostics_this.paper_trade_count.value == 0
    assert payload.diagnostics_this.paper_trade_count.observations == 1


def test_build_payload_trade_count_none_when_no_snapshots(db_session):
    """Null rule: zero observations → value=None (NOT 0)."""
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    assert payload.diagnostics_this.paper_trade_count.value is None
    assert payload.diagnostics_this.paper_trade_count.observations == 0


def test_build_payload_engine_errors_none_when_no_ticks(db_session):
    """Null rule: zero tick events → value=None (NOT 0)."""
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    assert payload.diagnostics_this.engine_invariant_errors.value is None
    assert payload.diagnostics_this.engine_invariant_errors.observations == 0


def test_build_payload_engine_errors_observations(db_session):
    base = datetime(2026, 8, 10, tzinfo=UTC)
    for i in range(15):
        _seed_audit(db_session, ts=base + timedelta(hours=i),
                    event_type="TICK_COMPLETED")
    for i in range(5):
        _seed_audit(db_session, ts=base + timedelta(days=1, hours=i),
                    event_type="ENGINE_INVARIANT_ERROR", reason="r")
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    diag = payload.diagnostics_this.engine_invariant_errors
    assert diag.value == 5             # count of ENGINE_INVARIANT_ERROR
    assert diag.observations == 20     # L6: TICK_COMPLETED + ENGINE_INVARIANT_ERROR


def test_build_payload_engine_errors_reasons_only_from_engine(db_session):
    base = datetime(2026, 8, 10, tzinfo=UTC)
    _seed_audit(db_session, ts=base,
                event_type="ORDER_REJECTED", reason="should_not_appear")
    _seed_audit(db_session, ts=base + timedelta(hours=1),
                event_type="ENGINE_INVARIANT_ERROR", reason="real_engine_reason")
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    diag = payload.diagnostics_this.engine_invariant_errors
    assert tuple(r.reason for r in diag.top_reasons) == ("real_engine_reason",)


def test_build_payload_top_reasons_empty_normalized(db_session):
    """L19: empty `reason` → '(no reason)' bucket.

    The `paper_audit_event.reason` column is `Mapped[str]` with
    `nullable=False, default=""`. NULL is impossible at the schema level,
    so the spec's "NULL or empty" reduces in practice to "empty".
    """
    base = datetime(2026, 8, 10, tzinfo=UTC)
    for i in range(3):
        _seed_audit(
            db_session, ts=base + timedelta(hours=i),
            event_type="ENGINE_INVARIANT_ERROR", reason="",
        )
    db_session.commit()
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    diag = payload.diagnostics_this.engine_invariant_errors
    assert tuple((r.reason, r.count) for r in diag.top_reasons) == (
        ("(no reason)", 3),
    )


def test_build_payload_manifest_none(db_session):
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=None,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    op = payload.operational_floor
    assert op.manifest_available is False
    assert op.backup_status == "missing"
    assert op.backup_is_stale is True
    assert op.backup_last_at is None
    assert op.backup_error is None


def test_build_payload_manifest_ok(db_session):
    manifest = {
        "status": "ok",
        "is_stale": False,
        "last_backup_at": "2026-08-17T09:00:00+00:00",
        "error": None,
    }
    payload = build_payload(
        session=db_session, week_ending=date(2026, 8, 16),
        backup_manifest=manifest,
        generated_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
    )
    op = payload.operational_floor
    assert op.manifest_available is True
    assert op.backup_status == "ok"
    assert op.backup_is_stale is False
    assert op.backup_last_at == "2026-08-17T09:00:00+00:00"
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/ops/test_charter_review_aggregator.py -v`
Expected: FAIL — diagnostics still empty.

- [ ] **Step 3: Implement diagnostics in aggregator**

In `marketpulse/ops/charter_review_aggregator.py`, REPLACE the two `_empty_diagnostics()` calls in `build_payload` and add the helper functions. Add this block AFTER `_empty_diagnostics()` and BEFORE `_operational_floor`:

```python
def _normalize_reason(raw: str | None) -> str:
    """L19: NULL or empty → '(no reason)'. Anything else passes through."""
    if raw is None or raw == "":
        return NO_REASON
    return raw


def _top_reasons(
    session: Session, *, event_types: tuple[str, ...],
    window_start: datetime, window_end: datetime, limit: int = 3,
) -> tuple[ReasonCount, ...]:
    """SELECT normalized(reason), COUNT(*) GROUP BY normalized(reason)
    ORDER BY count DESC, reason ASC LIMIT {limit}."""
    rows = session.execute(
        select(PaperAuditEvent.reason, func.count(PaperAuditEvent.id))
        .where(and_(
            PaperAuditEvent.event_type.in_(event_types),
            PaperAuditEvent.timestamp >= window_start,
            PaperAuditEvent.timestamp <= window_end,
        ))
        .group_by(PaperAuditEvent.reason),
    ).all()
    # Normalize NULL/empty into "(no reason)" then re-aggregate.
    bucket: dict[str, int] = {}
    for raw_reason, n in rows:
        key = _normalize_reason(raw_reason)
        bucket[key] = bucket.get(key, 0) + int(n)
    # Deterministic order (L8): count desc, reason asc.
    ordered = sorted(bucket.items(), key=lambda kv: (-kv[1], kv[0]))
    return tuple(ReasonCount(reason=r, count=c) for r, c in ordered[:limit])


def _count_audit(
    session: Session, *, event_type: str,
    window_start: datetime, window_end: datetime,
) -> int:
    return int(session.scalar(
        select(func.count(PaperAuditEvent.id))
        .where(and_(
            PaperAuditEvent.event_type == event_type,
            PaperAuditEvent.timestamp >= window_start,
            PaperAuditEvent.timestamp <= window_end,
        )),
    ) or 0)


def _build_tick_success_rate(
    session: Session, week: WeekWindow,
) -> DiagnosticWeek:
    start, end = _eod_window(week)
    completed = _count_audit(session, event_type="TICK_COMPLETED",
                              window_start=start, window_end=end)
    errored = _count_audit(session, event_type="ENGINE_INVARIANT_ERROR",
                            window_start=start, window_end=end)
    total = completed + errored
    if total == 0:
        return _empty_diagnostic()
    value = Decimal(completed) / Decimal(total)
    return DiagnosticWeek(
        value=value, observations=total,
        top_reasons=_top_reasons(
            session, event_types=("ENGINE_INVARIANT_ERROR",),
            window_start=start, window_end=end,
        ),
    )


def _build_order_rejection_rate(
    session: Session, week: WeekWindow,
) -> DiagnosticWeek:
    start, end = _eod_window(week)
    placed = _count_audit(session, event_type="ORDER_PLACED",
                           window_start=start, window_end=end)
    rejected = _count_audit(session, event_type="ORDER_REJECTED",
                             window_start=start, window_end=end)
    total = placed + rejected
    if total == 0:
        return _empty_diagnostic()
    value = Decimal(rejected) / Decimal(total)
    return DiagnosticWeek(
        value=value, observations=total,
        top_reasons=_top_reasons(
            session, event_types=("ORDER_REJECTED",),
            window_start=start, window_end=end,
        ),
    )


def _build_paper_trade_count(
    session: Session, week: WeekWindow,
) -> DiagnosticWeek:
    """L5: paper_fill side='ENTRY' AND position_id IS NOT NULL.
    L7: observations = trading days observed in week.
    Null rule (spec): zero observations → value=None. Otherwise the
    integer fill count, including 0 when the week had trading days
    but no entry fills."""
    obs = _trading_days_observed(session, week)
    if obs == 0:
        return _empty_diagnostic()
    start, end = _eod_window(week)
    count = int(session.scalar(
        select(func.count(PaperFill.id))
        .where(and_(
            PaperFill.side == "ENTRY",
            PaperFill.position_id.is_not(None),
            PaperFill.filled_at >= start,
            PaperFill.filled_at <= end,
        )),
    ) or 0)
    return DiagnosticWeek(
        value=count, observations=obs, top_reasons=(),
    )


def _build_engine_invariant_errors(
    session: Session, week: WeekWindow,
) -> DiagnosticWeek:
    """L6: observations = TICK_COMPLETED + ENGINE_INVARIANT_ERROR.
    Null rule: if total = 0 (no tick activity at all this week), value=None.
    If total>0 and errors=0, value=0 (truthful: ticks ran, none broke)."""
    start, end = _eod_window(week)
    completed = _count_audit(session, event_type="TICK_COMPLETED",
                              window_start=start, window_end=end)
    errored = _count_audit(session, event_type="ENGINE_INVARIANT_ERROR",
                            window_start=start, window_end=end)
    total = completed + errored
    if total == 0:
        return _empty_diagnostic()
    return DiagnosticWeek(
        value=errored, observations=total,
        top_reasons=_top_reasons(
            session, event_types=("ENGINE_INVARIANT_ERROR",),
            window_start=start, window_end=end,
        ),
    )


def _build_diagnostics(session: Session, week: WeekWindow) -> DiagnosticsWeek:
    return DiagnosticsWeek(
        tick_success_rate=_build_tick_success_rate(session, week),
        order_rejection_rate=_build_order_rejection_rate(session, week),
        paper_trade_count=_build_paper_trade_count(session, week),
        engine_invariant_errors=_build_engine_invariant_errors(session, week),
    )
```

Then in `build_payload`, replace the two `_empty_diagnostics()` calls with `_build_diagnostics(...)`:

```python
        diagnostics_this=_build_diagnostics(session, this_window),
        diagnostics_prior=_build_diagnostics(session, prior_window),
```

- [ ] **Step 4: Verify passing**

Run: `uv run pytest tests/ops/test_charter_review_aggregator.py -v`
Expected: PASS — all aggregator tests (~14 total).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/ops/charter_review_aggregator.py tests/ops/test_charter_review_aggregator.py
git commit -m "feat(pr3b): aggregator diagnostics — rate + trade count + engine errors (L5/L6/L7/L8/L19)"
```

---

## Task 7: Orchestration — atomic write + `_read_backup_manifest`

**Files:**
- Create: `marketpulse/ops/charter_review.py`
- Create: `tests/ops/test_charter_review_orchestration.py`

- [ ] **Step 1: Write failing tests for helpers**

Create `tests/ops/test_charter_review_orchestration.py`:

```python
# Layer: test
"""PR3b — charter_review orchestration tests."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from marketpulse.ops.charter_review import (
    _atomic_write_text,
    _read_backup_manifest,
)


def test_read_backup_manifest_missing_returns_none(tmp_path):
    assert _read_backup_manifest(tmp_path / "nope.json") is None


def test_read_backup_manifest_malformed_returns_none(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text("{not json", encoding="utf-8")
    assert _read_backup_manifest(p) is None


def test_read_backup_manifest_ok(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    parsed = _read_backup_manifest(p)
    assert parsed == {"status": "ok"}


def test_atomic_write_text_creates_new_file(tmp_path):
    target = tmp_path / "out.md"
    _atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"
    leftovers = sorted(tmp_path.glob(".*.tmp"))
    assert leftovers == []


def test_atomic_write_text_replaces_existing(tmp_path):
    target = tmp_path / "out.md"
    target.write_text("old", encoding="utf-8")
    _atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    leftovers = sorted(tmp_path.glob(".*.tmp"))
    assert leftovers == []


def test_atomic_write_text_preserves_old_on_replace_failure(tmp_path, monkeypatch):
    target = tmp_path / "out.md"
    target.write_text("old", encoding="utf-8")

    def boom(src, dst):
        raise OSError("simulated replace failure")

    import marketpulse.ops.charter_review as cr_mod
    monkeypatch.setattr(cr_mod, "_os_replace", boom)

    with pytest.raises(OSError):
        _atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "old"
    leftovers = sorted(tmp_path.glob(".*.tmp"))
    assert leftovers == []
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/ops/test_charter_review_orchestration.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create the orchestration module (helpers only)**

Create `marketpulse/ops/charter_review.py`:

```python
# Layer: ops
"""PR3b — orchestration entry for the weekly charter review.

Reads backup manifest, calls aggregator + renderer, atomically writes
the markdown and the latest.json companion (L10/L11). May raise
CharterReviewError; the scheduler catches at the boundary (L4).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import suppress
from datetime import date, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from marketpulse.ops.charter_review_aggregator import build_payload
from marketpulse.ops.charter_review_renderer import render_charter_review
from marketpulse.ops.charter_review_types import CharterReviewPayload

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Module-level alias so tests can monkeypatch ONLY this module's reference,
# without affecting global `os.replace` for other callers in the same test.
_os_replace = os.replace


class CharterReviewError(Exception):
    """Surface error from the charter review pipeline. Raised by
    generate_charter_review; the scheduler boundary catches and logs."""


def _read_backup_manifest(path: Path) -> dict | None:
    """Returns parsed manifest dict, or None on missing/unreadable/malformed.
    Never raises — that case becomes manifest_available=False in payload."""
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return None
        return parsed
    except (OSError, json.JSONDecodeError):
        return None


def _atomic_write_text(path: Path, payload: str) -> None:
    """L10: tempfile in same dir → fdopen → write → fsync → os.replace.
    L11: on any failure, tempfile is cleaned; pre-existing target is
    NOT touched (because os.replace is the only operation that mutates
    the target, and it is atomic by POSIX guarantees)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd: int | None = None
    tmp_path: str | None = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
        )
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            tmp_fd = None
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        _os_replace(tmp_path, str(path))
        tmp_path = None
    finally:
        if tmp_fd is not None:
            with suppress(OSError):
                os.close(tmp_fd)
        if tmp_path is not None and os.path.exists(tmp_path):
            with suppress(OSError):
                os.unlink(tmp_path)
```

- [ ] **Step 4: Verify passing**

Run: `uv run pytest tests/ops/test_charter_review_orchestration.py -v`
Expected: PASS — 6 helper tests pass.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/ops/charter_review.py tests/ops/test_charter_review_orchestration.py
git commit -m "feat(pr3b): orchestration helpers — _read_backup_manifest + _atomic_write_text (L10/L11)"
```

---

## Task 8: Orchestration — `generate_charter_review` + L12 + L20 success log

**Files:**
- Modify: `marketpulse/ops/charter_review.py`
- Modify: `tests/ops/test_charter_review_orchestration.py`

- [ ] **Step 1: Append failing entry-point tests**

Append to `tests/ops/test_charter_review_orchestration.py`:

```python
import json as _json
from datetime import date as _date, datetime as _dt
import logging as _logging
from unittest.mock import patch

from marketpulse.ops.charter_review import (
    CharterReviewError,
    generate_charter_review,
)


def test_generate_writes_markdown_and_latest_json(db_session, tmp_path):
    recaps = tmp_path / "charter"
    manifest_path = tmp_path / "manifest.json"   # missing
    out_path = generate_charter_review(
        session=db_session,
        week_ending=_date(2026, 8, 16),
        now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
        recaps_dir=recaps,
        backup_manifest_path=manifest_path,
    )
    assert out_path == recaps / "2026-08-16.md"
    assert out_path.exists()
    assert "# Charter Review" in out_path.read_text(encoding="utf-8")
    latest = recaps / "latest.json"
    assert latest.exists()
    parsed = _json.loads(latest.read_text(encoding="utf-8"))
    assert parsed["week_ending"] == "2026-08-16"
    assert parsed["path"] == str(out_path)
    assert parsed["schema_version"] == 1


def test_generate_validates_week_ending_is_sunday(db_session, tmp_path):
    with pytest.raises(CharterReviewError, match="Sunday"):
        generate_charter_review(
            session=db_session,
            week_ending=_date(2026, 8, 15),   # Saturday
            now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
            recaps_dir=tmp_path / "charter",
            backup_manifest_path=tmp_path / "m.json",
        )


def test_generate_idempotent_same_week_same_now(db_session, tmp_path):
    common = dict(
        session=db_session,
        week_ending=_date(2026, 8, 16),
        now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
        recaps_dir=tmp_path / "charter",
        backup_manifest_path=tmp_path / "m.json",
    )
    p1 = generate_charter_review(**common)
    body1 = p1.read_text(encoding="utf-8")
    p2 = generate_charter_review(**common)
    body2 = p2.read_text(encoding="utf-8")
    assert body1 == body2


def test_generate_missing_manifest_lands_file(db_session, tmp_path):
    p = generate_charter_review(
        session=db_session,
        week_ending=_date(2026, 8, 16),
        now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
        recaps_dir=tmp_path / "charter",
        backup_manifest_path=tmp_path / "absent.json",
    )
    text = p.read_text(encoding="utf-8")
    assert "Backup manifest unavailable" in text


def test_generate_malformed_manifest_lands_file(db_session, tmp_path):
    manifest_path = tmp_path / "m.json"
    manifest_path.write_text("{not json", encoding="utf-8")
    p = generate_charter_review(
        session=db_session,
        week_ending=_date(2026, 8, 16),
        now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
        recaps_dir=tmp_path / "charter",
        backup_manifest_path=manifest_path,
    )
    text = p.read_text(encoding="utf-8")
    assert "Backup manifest unavailable" in text


def test_generate_db_query_failure_raises_typed(db_session, tmp_path, monkeypatch):
    from marketpulse.ops import charter_review as cr_mod

    def boom(**kwargs):
        raise RuntimeError("simulated aggregator failure")

    monkeypatch.setattr(cr_mod, "build_payload", boom)
    with pytest.raises(CharterReviewError):
        generate_charter_review(
            session=db_session,
            week_ending=_date(2026, 8, 16),
            now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
            recaps_dir=tmp_path / "charter",
            backup_manifest_path=tmp_path / "m.json",
        )


def test_generate_success_emits_info_log(db_session, tmp_path, caplog):
    caplog.set_level(_logging.INFO, logger="marketpulse.ops.charter_review")
    generate_charter_review(
        session=db_session,
        week_ending=_date(2026, 8, 16),
        now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
        recaps_dir=tmp_path / "charter",
        backup_manifest_path=tmp_path / "m.json",
    )
    # L20: one info log named charter_review_generated.
    matches = [r for r in caplog.records
               if "charter_review_generated" in r.getMessage()]
    assert len(matches) == 1
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/ops/test_charter_review_orchestration.py -v`
Expected: FAIL — `generate_charter_review` not defined.

- [ ] **Step 3: Add `generate_charter_review` to the module**

Append to `marketpulse/ops/charter_review.py`:

```python
def generate_charter_review(
    *,
    session: Session,
    week_ending: date,
    now: datetime,
    recaps_dir: Path,
    backup_manifest_path: Path,
) -> Path:
    """Build payload → render markdown → atomic-write .md + latest.json.

    L12: validates week_ending is Sunday (weekday == 6) at entry.
    L4: may raise CharterReviewError on DB / render / FS failures.
    L20: on success emits info log charter_review_generated with extra=
         {week_ending, path, generated_at}.
    """
    if week_ending.weekday() != 6:
        raise CharterReviewError(
            f"week_ending must be Sunday (weekday=6); got weekday={week_ending.weekday()}",
        )

    manifest = _read_backup_manifest(backup_manifest_path)
    try:
        payload = build_payload(
            session=session, week_ending=week_ending,
            backup_manifest=manifest, generated_at=now,
        )
    except CharterReviewError:
        raise   # already typed; don't double-wrap
    except Exception as exc:  # noqa: BLE001
        raise CharterReviewError(
            f"aggregator failed: {type(exc).__name__}: {exc}",
        ) from exc

    try:
        markdown = render_charter_review(payload=payload)
    except CharterReviewError:
        raise   # already typed; don't double-wrap
    except Exception as exc:  # noqa: BLE001
        raise CharterReviewError(
            f"renderer failed: {type(exc).__name__}: {exc}",
        ) from exc

    md_path = recaps_dir / f"{week_ending.isoformat()}.md"
    latest_path = recaps_dir / "latest.json"
    manifest_payload = {
        "schema_version": SCHEMA_VERSION,
        "week_ending": week_ending.isoformat(),
        "path": str(md_path),
        "generated_at": now.isoformat(),
    }

    try:
        _atomic_write_text(md_path, markdown)
        _atomic_write_text(
            latest_path,
            json.dumps(manifest_payload, indent=2, sort_keys=True),
        )
    except OSError as exc:
        raise CharterReviewError(
            f"atomic write failed: {type(exc).__name__}: {exc}",
        ) from exc

    log.info(
        "charter_review_generated",
        extra={
            "week_ending": week_ending.isoformat(),
            "path": str(md_path),
            "generated_at": now.isoformat(),
        },
    )
    return md_path
```

- [ ] **Step 4: Verify passing**

Run: `uv run pytest tests/ops/test_charter_review_orchestration.py -v`
Expected: PASS — 13 total tests.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/ops/charter_review.py tests/ops/test_charter_review_orchestration.py
git commit -m "feat(pr3b): generate_charter_review entry — L12 + L20"
```

---

## Task 9: Orchestration — atomic rollback tests for `.md` AND `latest.json`

**Files:**
- Modify: `tests/ops/test_charter_review_orchestration.py`

- [ ] **Step 1: Append failing rollback tests**

Append to `tests/ops/test_charter_review_orchestration.py`:

```python
def test_generate_atomic_write_preserves_old_md_on_failure(
    db_session, tmp_path, monkeypatch,
):
    recaps = tmp_path / "charter"
    recaps.mkdir()
    old_md = recaps / "2026-08-16.md"
    old_md.write_text("OLD CONTENT", encoding="utf-8")

    import marketpulse.ops.charter_review as cr_mod
    real_replace = cr_mod._os_replace

    def boom_for_md(src, dst):
        if str(dst).endswith(".md"):
            raise OSError("simulated .md replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(cr_mod, "_os_replace", boom_for_md)

    with pytest.raises(CharterReviewError):
        generate_charter_review(
            session=db_session,
            week_ending=_date(2026, 8, 16),
            now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
            recaps_dir=recaps,
            backup_manifest_path=tmp_path / "m.json",
        )

    # Old file preserved; no orphan tempfile.
    assert old_md.read_text(encoding="utf-8") == "OLD CONTENT"
    orphans = sorted(recaps.glob(".*.tmp"))
    assert orphans == []


def test_generate_atomic_write_preserves_old_latest_json_on_failure(
    db_session, tmp_path, monkeypatch,
):
    recaps = tmp_path / "charter"
    recaps.mkdir()
    old_json = recaps / "latest.json"
    old_json.write_text("OLD JSON", encoding="utf-8")

    import marketpulse.ops.charter_review as cr_mod
    real_replace = cr_mod._os_replace

    def boom_for_json(src, dst):
        if str(dst).endswith("latest.json"):
            raise OSError("simulated latest.json replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(cr_mod, "_os_replace", boom_for_json)

    with pytest.raises(CharterReviewError):
        generate_charter_review(
            session=db_session,
            week_ending=_date(2026, 8, 16),
            now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
            recaps_dir=recaps,
            backup_manifest_path=tmp_path / "m.json",
        )

    assert old_json.read_text(encoding="utf-8") == "OLD JSON"
    orphans = sorted(recaps.glob(".*.tmp"))
    assert orphans == []


def test_generate_atomic_write_no_orphan_tempfiles(db_session, tmp_path):
    generate_charter_review(
        session=db_session,
        week_ending=_date(2026, 8, 16),
        now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
        recaps_dir=tmp_path / "charter",
        backup_manifest_path=tmp_path / "m.json",
    )
    orphans = sorted((tmp_path / "charter").glob(".*.tmp"))
    assert orphans == []


def test_generate_latest_json_atomic_replace(db_session, tmp_path):
    common = dict(
        session=db_session,
        recaps_dir=tmp_path / "charter",
        backup_manifest_path=tmp_path / "m.json",
    )
    generate_charter_review(
        week_ending=_date(2026, 8, 9), now=_dt(2026, 8, 10, 9, 30, tzinfo=UTC),
        **common,
    )
    generate_charter_review(
        week_ending=_date(2026, 8, 16), now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
        **common,
    )
    latest = (tmp_path / "charter" / "latest.json")
    parsed = _json.loads(latest.read_text(encoding="utf-8"))
    assert parsed["week_ending"] == "2026-08-16"
    orphans = sorted((tmp_path / "charter").glob(".*.tmp"))
    assert orphans == []
```

- [ ] **Step 2: Verify passing**

Run: `uv run pytest tests/ops/test_charter_review_orchestration.py -v`
Expected: PASS — 17 total orchestration tests.

- [ ] **Step 3: Commit**

```bash
git add tests/ops/test_charter_review_orchestration.py
git commit -m "test(pr3b): atomic-write rollback for both .md and latest.json (L11)"
```

---

## Task 10: Scheduler — `run_charter_review_weekly` + `_last_sunday_on_or_before`

**Files:**
- Modify: `marketpulse/scheduler/jobs.py`
- Create: `tests/scheduler/test_charter_review_scheduler.py`

- [ ] **Step 1: Write failing tests**

Create `tests/scheduler/test_charter_review_scheduler.py`:

```python
# Layer: test
"""PR3b — scheduler-level isolation tests for the weekly charter review."""
from __future__ import annotations

import logging
from datetime import date

import pytest


def test_last_sunday_on_or_before_monday():
    from marketpulse.scheduler.jobs import _last_sunday_on_or_before
    assert _last_sunday_on_or_before(date(2026, 8, 17)) == date(2026, 8, 16)


def test_last_sunday_on_or_before_sunday():
    from marketpulse.scheduler.jobs import _last_sunday_on_or_before
    assert _last_sunday_on_or_before(date(2026, 8, 16)) == date(2026, 8, 16)


def test_last_sunday_on_or_before_friday():
    from marketpulse.scheduler.jobs import _last_sunday_on_or_before
    # Friday → previous Sunday (Aug 9).
    assert _last_sunday_on_or_before(date(2026, 8, 14)) == date(2026, 8, 9)


def test_run_charter_review_weekly_failure_logged_not_raised(
    db_session, monkeypatch, caplog,
):
    from marketpulse.scheduler import jobs as jobs_mod

    def boom(**kwargs):
        raise RuntimeError("simulated review failure")

    monkeypatch.setattr(jobs_mod, "generate_charter_review", boom)
    caplog.set_level(logging.WARNING, logger="marketpulse.scheduler.jobs")
    # Should not raise.
    jobs_mod.run_charter_review_weekly()
    # And the warning must be emitted.
    assert any(
        "charter_review_failed" in rec.getMessage()
        for rec in caplog.records
    )


def test_run_charter_review_weekly_skipped_for_non_sqlite(monkeypatch, caplog):
    from marketpulse.scheduler import jobs as jobs_mod
    from marketpulse.config import get_settings

    real_settings = get_settings()

    class _StubSettings:
        database_url = "postgresql://user:pw@localhost:5432/mp"
        def __getattr__(self, name):
            return getattr(real_settings, name)

    monkeypatch.setattr(jobs_mod, "get_settings", lambda: _StubSettings())
    caplog.set_level(logging.INFO, logger="marketpulse.scheduler.jobs")
    jobs_mod.run_charter_review_weekly()
    assert any(
        "charter_review_skipped_not_sqlite" in rec.getMessage()
        for rec in caplog.records
    )


def test_run_charter_review_weekly_accepts_sqlite_pysqlite(
    db_session, monkeypatch, tmp_path,
):
    """L13 / PR2 lesson: `sqlite+pysqlite:///...` MUST be treated as sqlite,
    not skipped. We don't run the full generator — just verify the driver
    check doesn't short-circuit by asserting generate_charter_review IS called."""
    from marketpulse.scheduler import jobs as jobs_mod
    from marketpulse.config import get_settings

    real_settings = get_settings()
    db_file = tmp_path / "smoke.db"

    class _StubSettings:
        database_url = f"sqlite+pysqlite:///{db_file}"
        def __getattr__(self, name):
            return getattr(real_settings, name)

    called = {"count": 0}

    def fake_generate(**kwargs):
        called["count"] += 1
        return tmp_path / "ok.md"

    monkeypatch.setattr(jobs_mod, "get_settings", lambda: _StubSettings())
    monkeypatch.setattr(jobs_mod, "generate_charter_review", fake_generate)
    # Need a usable session — the real session_scope will open the DB at
    # the stubbed URL, which is a fresh empty sqlite file. That's fine
    # because our fake generator never touches it.
    jobs_mod.run_charter_review_weekly()
    assert called["count"] == 1
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/scheduler/test_charter_review_scheduler.py -v`
Expected: FAIL — `_last_sunday_on_or_before` / `run_charter_review_weekly` missing.

- [ ] **Step 3: Add to `marketpulse/scheduler/jobs.py`**

Add this import near the other `marketpulse.*` imports at the top:

```python
from marketpulse.ops.charter_review import generate_charter_review
```

Add the helper and the new cron handler. Best placement: immediately AFTER `run_db_backup` (~line 200 in the file) and BEFORE `run_news_purge`:

```python
def _last_sunday_on_or_before(d: date) -> date:
    """Mon=0..Sun=6. Returns d if Sunday, else d minus (weekday+1) days."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def run_charter_review_weekly() -> None:
    """Mon 09:30 UTC — generate the weekly charter review markdown.

    L4: errors from generate_charter_review are caught here and logged;
    the scheduler must not crash because of this job.
    L13: skipped with info log if database_url isn't a sqlite driver.
    """
    settings = get_settings()
    parsed = make_url(settings.database_url)
    if not parsed.drivername.startswith("sqlite") or not parsed.database:
        log.info(
            "charter_review_skipped_not_sqlite",
            database_url=settings.database_url,
        )
        return
    source_db = Path(parsed.database).resolve()
    data_dir = source_db.parent
    recaps_dir = data_dir / "recaps" / "charter"
    backup_manifest_path = data_dir / "backups" / "latest.json"
    now = datetime.now(UTC)
    week_ending = _last_sunday_on_or_before(now.date())
    try:
        gen = session_scope()
        session = next(gen)
        try:
            generate_charter_review(
                session=session,
                week_ending=week_ending,
                now=now,
                recaps_dir=recaps_dir,
                backup_manifest_path=backup_manifest_path,
            )
        finally:
            with suppress(Exception):
                session.close()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "charter_review_failed",
            extra={"week_ending": str(week_ending), "exception": str(exc)},
        )
```

(`from contextlib import suppress` is already imported in `jobs.py`; if not, add it. `make_url`, `get_settings`, `session_scope`, `Path`, `datetime`, `UTC`, `timedelta`, `date`, `log` are all already present in this file.)

- [ ] **Step 4: Verify passing**

Run: `uv run pytest tests/scheduler/test_charter_review_scheduler.py -v`
Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/scheduler/jobs.py tests/scheduler/test_charter_review_scheduler.py
git commit -m "feat(pr3b): scheduler hook run_charter_review_weekly + _last_sunday_on_or_before"
```

---

## Task 11: Scheduler — cron registration + invariant tests

**Files:**
- Modify: `marketpulse/scheduler/jobs.py` (add to `build_scheduler`)
- Modify: `tests/scheduler/test_build_scheduler.py`

- [ ] **Step 1: Add failing assertions to `test_build_scheduler.py`**

Edit `tests/scheduler/test_build_scheduler.py`. In `test_daily_critical_jobs_have_no_misfire_grace`, extend the tuple of job IDs to include `charter_review_weekly`:

Find:
```python
    for job_id in (
        "paper_trading_tick", "outcome_computation", "flex_sync",
        "sector_backfill", "db_backup",
    ):
```

Replace with:
```python
    for job_id in (
        "paper_trading_tick", "outcome_computation", "flex_sync",
        "sector_backfill", "db_backup", "charter_review_weekly",
    ):
```

Append a new test at the end of the file:

```python
def test_charter_review_weekly_job_registered():
    """PR3b: weekly markdown at 09:30 UTC every Monday."""
    sched = build_scheduler()
    job = sched.get_job("charter_review_weekly")
    assert job is not None, "charter_review_weekly cron must be registered"
    trigger_repr = str(job.trigger)
    assert "day_of_week='mon'" in trigger_repr, trigger_repr
    assert "hour='9'" in trigger_repr or "hour=9" in trigger_repr, trigger_repr
    assert "minute='30'" in trigger_repr or "minute=30" in trigger_repr, trigger_repr
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/scheduler/test_build_scheduler.py -v`
Expected: FAIL — job not registered.

- [ ] **Step 3: Register the job in `build_scheduler`**

In `marketpulse/scheduler/jobs.py`, find the `db_backup` `add_job` call (around line 585):

```python
    sched.add_job(
        run_db_backup,
        trigger=CronTrigger(hour=9, minute=0, timezone="UTC"),
        id="db_backup",
        replace_existing=True,
        misfire_grace_time=None,
        coalesce=True,
    )
```

Add this BLOCK immediately AFTER the `db_backup` block:

```python
    # PR3b: weekly charter review. Runs every Monday 09:30 UTC, AFTER the
    # 09:00 UTC db_backup so the report reads a fresh backup manifest.
    sched.add_job(
        run_charter_review_weekly,
        trigger=CronTrigger(
            day_of_week="mon", hour=9, minute=30, timezone="UTC",
        ),
        id="charter_review_weekly",
        replace_existing=True,
        misfire_grace_time=None,
        coalesce=True,
    )
```

- [ ] **Step 4: Verify passing**

Run: `uv run pytest tests/scheduler/test_build_scheduler.py -v`
Expected: PASS — including the extended daily-critical test and the new registration test.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/scheduler/jobs.py tests/scheduler/test_build_scheduler.py
git commit -m "feat(pr3b): register charter_review_weekly cron (Mon 09:30 UTC)"
```

---

## Task 12: Final integration — full suite + ruff + smoke + PR

**Files:** none (verification + PR)

- [ ] **Step 1: Run the full pytest suite**

Run: `uv run pytest -x`
Expected: PASS — no regressions in any prior suite.

- [ ] **Step 2: Run ruff**

Run: `uv run ruff check .`
Expected: clean.

- [ ] **Step 3: Local smoke (optional)**

```bash
uv run python -c "
from datetime import UTC, date, datetime
from pathlib import Path
import tempfile
from sqlalchemy.orm import Session
from marketpulse.db.base import init_engine, get_engine
from marketpulse.db.base import Base
from marketpulse.ops.charter_review import generate_charter_review

with tempfile.TemporaryDirectory() as tmp:
    db_url = f'sqlite:///{tmp}/smoke.db'
    init_engine(db_url)
    Base.metadata.create_all(get_engine())
    with Session(get_engine()) as session:
        out = generate_charter_review(
            session=session,
            week_ending=date(2026, 8, 16),
            now=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
            recaps_dir=Path(tmp) / 'charter',
            backup_manifest_path=Path(tmp) / 'absent.json',
        )
        print('wrote', out)
        print(out.read_text(encoding='utf-8')[:500])
"
```

Expected: prints the path and the first 500 chars of the report (showing the 6 section headers).

- [ ] **Step 4: Push and open PR**

```bash
git push -u origin feat/pr3b-charter-review
gh pr create --title "feat(pr3b): weekly charter review markdown — Charter top-3 #1 PR3b" \
  --body "$(cat <<'EOF'
## Summary
- Adds three pure-ish modules (`types`, `aggregator`, `renderer`) + one orchestration entry
  (`charter_review.py`) for the weekly markdown narrative.
- Cron `charter_review_weekly` runs every Monday 09:30 UTC after `db_backup`.
- Writes `/data/recaps/charter/YYYY-MM-DD.md` + `latest.json` (atomic, L10/L11).
- Filesystem only — no DB schema changes.
- Renderer is pure and deterministic (L9/L17): same payload → byte-identical.
- "What happened?" not "Why?" — counts, deltas, rankings (top reasons). No inferred causes.

Charter top-3 priority #1, PR3b. Spec: `docs/superpowers/specs/2026-05-29-pr3b-charter-review-weekly-design.md`. 20 scope locks L1–L20.

## Test Plan
- [x] `pytest tests/ops/test_charter_review_types.py` — 3 tests
- [x] `pytest tests/ops/test_charter_review_renderer.py` — 22 tests
- [x] `pytest tests/ops/test_charter_review_aggregator.py` — 14 tests
- [x] `pytest tests/ops/test_charter_review_orchestration.py` — 17 tests
- [x] `pytest tests/scheduler/test_charter_review_scheduler.py` — 6 tests
- [x] `pytest tests/scheduler/test_build_scheduler.py` — extended daily-critical lock + new registration test
- [x] `pytest -x` — full suite green, no regressions
- [x] `ruff check .` — clean
- [ ] Post-deploy smoke: `docker exec marketpulse python -c "from marketpulse.scheduler.jobs import run_charter_review_weekly; run_charter_review_weekly()"`
      then verify `/data/recaps/charter/<last-sunday>.md` exists with 6 section headers.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Spec → Plan Coverage Map

| Spec lock / requirement | Implemented in |
|---|---|
| L1 filesystem-only | Task 7 (`_atomic_write_text`); no new DB tables anywhere |
| L2 calendar week Mon→Sun | Task 5 (`_week_window`); Task 12 manual smoke checks file lands |
| L3 reads only nav_snapshot + audit_event + paper_fill | Task 5/6 (aggregator queries); test seeds `paper_position` to demonstrate non-touch via test_build_payload_does_not_touch* (added in Task 6 test suite) |
| L4 generate may raise; scheduler catches | Task 8 + Task 10 |
| L5 trade count via paper_fill ENTRY | Task 6 (`_build_paper_trade_count`); test `test_build_payload_trade_count_uses_fills` |
| L6 engine_invariant_errors source | Task 6 (`_build_engine_invariant_errors`); test `test_build_payload_engine_errors_observations` + `_reasons_only_from_engine` |
| L7 per-metric `observations` semantics | Task 6 (each builder pins) |
| L8 top_reasons deterministic | Task 6 (`_top_reasons` sort key); test `_rejection_top_reasons_sorted` |
| L9 renderer pure | Task 2-4 (no DB / FS / clock / network imports) |
| L10 atomic write tempfile + os.replace | Task 7 (`_atomic_write_text`) |
| L11 old file preserved on failure | Task 9 (`test_generate_atomic_write_preserves_old_*`) |
| L12 week_ending must be Sunday | Task 8 (validation) + test |
| L13 sqlite drivername.startswith | Task 10 (`run_charter_review_weekly` check) + test |
| L14 manifest_available=False semantics | Task 5 (`_operational_floor`) + tests `_manifest_none` / `_manifest_ok` |
| L15 appendix may include money fields | Task 5 (`_appendix_snapshot`) + renderer Task 4 + test `_appendix_money_fields_present_when_set` |
| L16 `_fmt_reason` normalization order | Task 3 implementation + 4 tests |
| L17 byte-identical determinism | Task 4 test `_minimal_payload_byte_identical` |
| L18 dataclasses in types.py | Task 1 |
| L19 NULL/empty reason → "(no reason)" | Task 6 (`_normalize_reason`) + test `_top_reasons_null_normalized` |
| L20 success info log charter_review_generated | Task 8 implementation + test `_success_emits_info_log` |
| Cron registered + invariants | Task 11 |
| Full suite + ruff + smoke + PR | Task 12 |
