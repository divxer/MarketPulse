# PR3b — Weekly Charter Review Narrative Design

> Charter top-3 priority #1, fourth installment. Consumer of PR3a's `paper_nav_snapshot` and PR1's backup manifest. See `docs/CHARTER.md` § "Top 3 priorities".

## Purpose

Ship a weekly markdown narrative that answers **what happened this calendar week vs prior calendar week** in operational and north-star terms. The report is a filesystem artifact, generated at end-of-Monday after the daily backup has run. Every sentence in the report must be provable from a row in a DB table or a field in the backup manifest.

## Positioning (one-line lock)

**PR3b answers "What happened?"** — not "Why did it happen?"

Allowed building blocks: counts, deltas, rankings (top reasons by frequency), trend summaries.
NOT allowed: inferred root causes, subsystem attribution, YAML expert rules, AI-generated explanations.

## Non-goals (deferred)

- Web UI for browsing past reports (separate PR if needed)
- Email / Slack / push notifications
- Daily or monthly periodicity
- AI-generated narrative
- Condition-rules YAML
- HTML rendering
- RSS feed
- Backfill / regenerate past weeks (out of scope for v1)

## Scope locks (referenced throughout)

| Lock | Statement |
|---|---|
| L1 | Output is filesystem-only. No new DB tables. No JSON API. |
| L2 | Report window is a calendar week, Monday → Sunday inclusive in UTC. Filename uses week-ending Sunday date. |
| L3 | All metrics derive from `paper_nav_snapshot`, `paper_audit_event`, `paper_fill` (read-only). PR3b never reads `paper_position` or `paper_cash_ledger` — that data is already encoded in PR3a's snapshot row. |
| L4 | `generate_charter_review` MAY raise on DB/FS/render failures. The scheduler boundary catches and logs. (Same shape as PR3a L4.) |
| L5 | `paper_trade_count` source = `paper_fill` rows with `position_id IS NOT NULL AND side='ENTRY' AND filled_at` in window. Matches PR3a L13 — schema uses ENTRY/EXIT, not BUY/SELL. |
| L6 | `engine_invariant_errors` source: `value` = ENGINE_INVARIANT_ERROR count in week; `observations` = TICK_COMPLETED + ENGINE_INVARIANT_ERROR; `top_reasons` from ENGINE_INVARIANT_ERROR rows only (never order events). |
| L7 | Per-metric `observations` semantics differ and are pinned in the producer: rate metrics = event count; `paper_trade_count` = trading days observed in week. |
| L8 | `top_reasons` is at most 3 entries, ordered (count DESC, reason ASC). Deterministic — no `set()` iteration leaks. |
| L9 | Renderer is pure: no DB, no FS, no clock, no network. Deterministic — same `payload` (including `generated_at`) → byte-identical output. |
| L10 | Atomic write for **both** `YYYY-MM-DD.md` and `latest.json`: tempfile in same directory → fsync → `os.replace`. |
| L11 | If `os.replace` fails, the existing on-disk `YYYY-MM-DD.md` is left untouched, no `.tmp` orphan remains, no partial `.md` is visible. |
| L12 | `week_ending` MUST be Sunday (weekday == 6). Otherwise raise `CharterReviewError("week_ending must be Sunday")` at entry. |
| L13 | SQLite detection uses `drivername.startswith("sqlite")` — same as PR2. Non-SQLite deployment skips with info log, no exception. |
| L14 | `manifest_available=False` deterministically yields `backup_status="missing"`, `backup_is_stale=True`, `backup_last_at=None`, `backup_error=None`. Matches PR2's missing-manifest semantics. |
| L15 | Appendix may include money fields (`cash_balance`, `holdings_mtm`, `portfolio_nav`). This is consistent with PR3a's L17 (Decimal → float for JSON API) because PR3b is a local filesystem artifact, never serialized to JSON. |
| L16 | `_fmt_reason` normalization (locked order): (a) replace `\n` and `\r` with space, (b) escape `|` as `\|`, (c) truncate to 200 chars + `…` if longer. |
| L17 | `generated_at` is part of the payload. Same `(payload including generated_at)` → byte-identical markdown. Test: `render(p) == render(p)`. |
| L18 | All dataclasses (`CharterReviewPayload`, `WeekWindow`, `NorthStarWeek`, `DiagnosticWeek`, `DiagnosticsWeek`, `OperationalFloor`, `SnapshotAppendix`, `ReasonCount`) live in `marketpulse/ops/charter_review_types.py`. Both aggregator and renderer import from this module — single source of truth, no circular-import risk. |
| L19 | Null / empty `reason` from `paper_audit_event` is normalized to the literal string `"(no reason)"` BEFORE forming `ReasonCount`. This happens in the aggregator's `_top_reasons` helper, not the renderer. Renderer-side `_fmt_reason` operates on already-normalized text. |
| L20 | On successful `generate_charter_review` return, orchestration emits ONE info log: `charter_review_generated`, with `extra={"week_ending": <iso>, "path": <abs>, "generated_at": <iso>}`. This is the hook future delivery integrations (email / Slack / web UI) can subscribe to without code changes here. |

## Architecture & Boundaries

```
                    run_charter_review_weekly  (scheduler — Mon 09:30 UTC)
                                          │
                                          ▼  catches generic Exception, logs warning
                              generate_charter_review     (orchestration — may raise)
                              ┌─────────┬──────────┐
                              │         │          │
              reads /data/backups/      │     writes /data/recaps/charter/YYYY-MM-DD.md
                  latest.json           │     writes /data/recaps/charter/latest.json
                              │         │          │ (both atomic: tempfile + os.replace)
                              ▼         ▼
                       build_payload(session, week_ending, backup_manifest, generated_at)
                                (aggregator — DB-only, manifest as input)
                                          │
                                          ▼
                              CharterReviewPayload (frozen dataclass)
                                          │
                                          ▼
                              render_charter_review(payload) → markdown str
                                       (renderer — pure)
```

Layers:
- **orchestration**: `marketpulse/ops/charter_review.py` — reads manifest, writes files, wraps errors.
- **aggregator (DB)**: `marketpulse/ops/charter_review_aggregator.py` — queries snapshot / audit / fill tables. Manifest is input, not I/O.
- **renderer (pure)**: `marketpulse/ops/charter_review_renderer.py` — string-only.
- **scheduler**: `marketpulse/scheduler/jobs.py` (modified) — cron + safe wrapper.

## Output (locked)

- `/data/recaps/charter/<YYYY-MM-DD>.md` — `YYYY-MM-DD` is the week-ending Sunday.
- `/data/recaps/charter/latest.json` — companion manifest:

```json
{
  "schema_version": 1,
  "week_ending": "2026-08-16",
  "path": "/data/recaps/charter/2026-08-16.md",
  "generated_at": "2026-08-17T09:30:00.000000+00:00"
}
```

## Data Contract — `CharterReviewPayload`

```python
@dataclass(frozen=True)
class ReasonCount:
    reason: str
    count: int

@dataclass(frozen=True)
class WeekWindow:
    """Calendar week, Monday→Sunday inclusive UTC."""
    week_start: date    # Monday
    week_end: date      # Sunday (== week_ending)
    trading_days_observed: int   # paper_nav_snapshot rows in [week_start, week_end]

@dataclass(frozen=True)
class NorthStarWeek:
    """A north-star view over one week."""
    week: WeekWindow
    first_snapshot_date: date | None
    last_snapshot_date: date | None
    excess_return_end: Decimal | None     # excess_return of last snapshot in week
    portfolio_index_end: Decimal | None
    spy_index_end: Decimal | None
    coverage_ratio_end: Decimal | None
    is_sufficient_end: bool

@dataclass(frozen=True)
class DiagnosticWeek:
    """One diagnostic over one week. `value` is None when underlying source
    has zero usable observations.

    `observations` semantics differ per metric (see L7); they are pinned by
    the producer in charter_review_aggregator.py:
      - tick_success_rate      : TICK_COMPLETED + ENGINE_INVARIANT_ERROR events
      - order_rejection_rate   : ORDER_PLACED + ORDER_REJECTED events
      - paper_trade_count      : trading days observed in week
      - engine_invariant_errors: TICK_COMPLETED + ENGINE_INVARIANT_ERROR events

    `top_reasons` semantics (see L8 for ordering):
      - tick_success_rate      : reasons from ENGINE_INVARIANT_ERROR rows
      - order_rejection_rate   : reasons from ORDER_REJECTED rows
      - paper_trade_count      : () — fills have no semantic reason
      - engine_invariant_errors: reasons from ENGINE_INVARIANT_ERROR rows
    """
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
    backup_status: Literal["ok", "failed", "missing"]
    backup_is_stale: bool
    backup_last_at: str | None
    backup_error: str | None
    manifest_available: bool
    # L14: manifest_available=False →
    #   backup_status="missing", backup_is_stale=True, backup_last_at=None, backup_error=None

@dataclass(frozen=True)
class SnapshotAppendix:
    """L15: filesystem-only appendix view. Money fields exposed here are
    NOT exposed via the PR3a JSON API."""
    trading_date: date | None
    cash_balance: Decimal | None
    holdings_mtm: Decimal | None
    portfolio_nav: Decimal | None
    unpriced_positions_count: int
    unpriced_tickers: tuple[str, ...]

@dataclass(frozen=True)
class CharterReviewPayload:
    generated_at: datetime
    week_ending: date           # Sunday; must equal this_week.week_end
    this_week: WeekWindow
    prior_week: WeekWindow
    north_star_this: NorthStarWeek
    north_star_prior: NorthStarWeek
    diagnostics_this: DiagnosticsWeek
    diagnostics_prior: DiagnosticsWeek
    operational_floor: OperationalFloor
    appendix_snapshot: SnapshotAppendix     # latest snapshot in this_week (or None fields)
```

**Delta rule:** the renderer computes deltas inline from `this`/`prior` pairs. The payload never pre-computes deltas — keeps the shape stable when one week is empty.

**Null rule:** every "value" field is `None` when the underlying source has zero observations. Renderer prints `N/A` for `None`. No zero-stuffing.

## Markdown Skeleton (locked)

```markdown
# Charter Review — Week Ending {week_ending}

Generated: {generated_at}
This week: {this.week_start} → {this.week_end} ({trading_days} trading days)
Prior week: {prior.week_start} → {prior.week_end} ({trading_days} trading days)

## Executive Summary

- Portfolio excess return: {value} ({delta_str}) — {coverage_str}
- Tick success rate: {value} ({delta_str})
- Order rejection rate: {value} ({delta_str})
- Paper entry fills: {count} ({delta_str})
- Backup status: {status} ({is_stale_str})

(If this_week.trading_days_observed == 0, prepend the bullet
 "No snapshots in this calendar week.")

## North Star

Metric: `paper_portfolio_excess_return_vs_spy_90d`

|                          | This week    | Prior week  | Δ            |
|--------------------------|--------------|-------------|--------------|
| Excess return            | {value}      | {value}     | {Δ}          |
| Portfolio index          | {value}      | {value}     | {Δ}          |
| SPY index                | {value}      | {value}     | {Δ}          |
| Coverage                 | {N}/90 days  | {N}/90 days | +{Δ} days    |
| Statistically sufficient | {bool}       | {bool}      | —            |

Observation window: first snapshot {first_date}, last snapshot {last_date}.

## Diagnostics

### Tick success rate

- This week: {value} ({obs} observations)
- Prior week: {value} ({obs} observations)
- Δ: {Δ}
- Top failure reasons this week: {reason} ({count}), …

### Order rejection rate

- This week: {value} ({obs} observations)
- Prior week: {value} ({obs} observations)
- Δ: {Δ}
- Top rejection reasons this week: {reason} ({count}), …

### Paper entry fills

- This week: {count}
- Prior week: {count}
- Δ: {Δ}

### Engine invariant errors

- This week: {count}
- Prior week: {count}
- Δ: {Δ}
- Top reasons this week: {reason} ({count}), …

## Operational Floor

- Backup status: {status}
- Last successful backup: {timestamp}
- Stale (>25h): {bool}
- Error (if any): {error_text}

## Appendix — Raw snapshot (end of this week)

- Trading date: {date}
- Cash balance: {value}
- Holdings MTM: {value}
- Portfolio NAV: {value}
- Unpriced positions: {count} ({tickers})
```

## Edge-case behavior table

| Condition | Behavior |
|---|---|
| `this_week.trading_days_observed == 0` | Markdown still generated. All "this week" cells say `N/A`. Executive summary prepends "No snapshots in this calendar week." |
| `prior_week.trading_days_observed == 0` | Δ cells say `prior week N/A`. Values still shown for this week. |
| Both weeks empty | Shell rendered + "No data yet" line. `latest.json` still written. |
| `manifest_available=False` | "Backup manifest unavailable" line; `backup_is_stale=True` reported (L14). |
| Unicode in `reason` | Pass through verbatim after `_fmt_reason` normalization. |
| `reason` contains `|` | Escaped as `\|` to preserve table grammar (L16). |
| `reason` length > 200 chars | Truncated to 200 chars + `…`. |
| `reason` contains `\n` / `\r` | Replaced with space. |

## Module Interfaces

### `marketpulse/ops/charter_review_types.py` (L18)

```python
# Layer: pure
"""Shared frozen dataclasses for the PR3b charter review pipeline.

L18: Both aggregator and renderer import from here. No circular imports.
No runtime logic — types only.
"""

@dataclass(frozen=True)
class ReasonCount: ...
@dataclass(frozen=True)
class WeekWindow: ...
@dataclass(frozen=True)
class NorthStarWeek: ...
@dataclass(frozen=True)
class DiagnosticWeek: ...
@dataclass(frozen=True)
class DiagnosticsWeek: ...
@dataclass(frozen=True)
class OperationalFloor: ...
@dataclass(frozen=True)
class SnapshotAppendix: ...
@dataclass(frozen=True)
class CharterReviewPayload: ...
```

(Full definitions are the ones in the Data Contract section above.)

### `marketpulse/ops/charter_review.py`

```python
# Layer: ops
SCHEMA_VERSION = 1


class CharterReviewError(Exception):
    """Surface error from the charter review pipeline. Raised by the
    orchestration entry; the scheduler catches at the boundary."""


def generate_charter_review(
    *,
    session: Session,
    week_ending: date,                 # Sunday (L12)
    now: datetime,                     # injected for tests
    recaps_dir: Path,                  # final output directory (e.g. /data/recaps/charter)
    backup_manifest_path: Path,
) -> Path:
    """Build payload → render markdown → atomic-write both files.
    Returns the path to the written .md.

    May raise CharterReviewError on DB / FS / render failures (L4).
    Validates week_ending.weekday() == 6 (Sunday) at entry (L12).
    On success, emits one info log `charter_review_generated` (L20)
    with week_ending / path / generated_at."""


def _read_backup_manifest(path: Path) -> dict | None:
    """Returns parsed manifest dict, or None on missing/unreadable/malformed.
    Never raises — that case becomes manifest_available=False in the payload."""


def _atomic_write_text(path: Path, payload: str) -> None:
    """tempfile.mkstemp in same dir → fdopen → write → fsync → os.replace.
    On any failure: tempfile cleaned, target file (if pre-existing) unchanged (L11)."""
```

### `marketpulse/ops/charter_review_aggregator.py`

```python
# Layer: ops
def build_payload(
    *,
    session: Session,
    week_ending: date,
    backup_manifest: dict | None,
    generated_at: datetime,
) -> CharterReviewPayload:
    """Build the payload. DB queries + dict reads only.
    Does not touch the filesystem (L3)."""


def _week_window(week_ending: date) -> WeekWindow:
    """Sunday week_end → Monday week_start (week_end - timedelta(days=6))."""


def _build_north_star_for_week(session, week: WeekWindow) -> NorthStarWeek: ...

def _build_tick_success_rate(session, week: WeekWindow) -> DiagnosticWeek: ...
def _build_order_rejection_rate(session, week: WeekWindow) -> DiagnosticWeek: ...
def _build_trade_count(session, week: WeekWindow) -> DiagnosticWeek: ...
def _build_engine_errors(session, week: WeekWindow) -> DiagnosticWeek: ...

def _top_reasons(session, *, event_type: str,
                 window_start_eod, window_end_eod, limit: int = 3,
                 ) -> tuple[ReasonCount, ...]:
    """SELECT reason, COUNT(*) GROUP BY reason ORDER BY count DESC, reason ASC
    LIMIT {limit}. Deterministic ordering (L8).

    L19: NULL or empty-string reason is normalized to the literal "(no reason)"
    BEFORE COUNT. Two NULL reasons + one "" reason all collapse into a single
    "(no reason)" bucket with count=3. Renderer never sees a blank reason."""

def _operational_floor(manifest: dict | None) -> OperationalFloor:
    """Manifest dict → OperationalFloor. L14: None or malformed →
    manifest_available=False, backup_status='missing', backup_is_stale=True,
    backup_last_at=None, backup_error=None."""

def _appendix_snapshot(session, week: WeekWindow) -> SnapshotAppendix:
    """Latest snapshot in week (L15). All fields None if no snapshot."""
```

**Week → event window:**
- `event_window_start = datetime.combine(week.week_start, time.min, tzinfo=UTC)`
- `event_window_end = datetime.combine(week.week_end, time.max, tzinfo=UTC)`

### `marketpulse/ops/charter_review_renderer.py`

```python
# Layer: pure
SECTION_SEPARATOR = "\n\n"
REASON_MAX_DISPLAY_LEN = 200
VALUE_NA = "N/A"
DELTA_PRIOR_NA = "prior week N/A"


def render_charter_review(*, payload: CharterReviewPayload) -> str:
    """Pure renderer (L9). Returns the complete markdown body."""


def _fmt_pct(value: Decimal | None, *, signed: bool = False) -> str: ...
def _fmt_int(value: int | None) -> str: ...
def _fmt_delta_pp(this: Decimal | None, prior: Decimal | None) -> str: ...
def _fmt_delta_int(this: int | None, prior: int | None) -> str: ...

def _fmt_reason(reason: str) -> str:
    """L16: replace \\n/\\r with space → escape | → truncate to 200 + …."""

def _section_executive_summary(payload) -> str: ...
def _section_north_star(payload) -> str: ...
def _section_diagnostics(payload) -> str: ...
def _section_operational_floor(payload) -> str: ...
def _section_appendix(payload) -> str: ...
```

### `marketpulse/scheduler/jobs.py` (modify)

```python
def run_charter_review_weekly() -> None:
    """Mon 09:30 UTC — generate weekly charter review (L4 safety wrapper)."""
    from marketpulse.ops.charter_review import generate_charter_review

    settings = get_settings()
    parsed = make_url(settings.database_url)
    if not parsed.drivername.startswith("sqlite") or not parsed.database:    # L13
        log.info("charter_review_skipped_not_sqlite",
                 database_url=settings.database_url)
        return
    source_db = Path(parsed.database).resolve()
    data_dir = source_db.parent
    recaps_dir = data_dir / "recaps" / "charter"
    backup_manifest_path = data_dir / "backups" / "latest.json"
    now = datetime.now(UTC)
    week_ending = _last_sunday_on_or_before(now.date())
    try:
        with session_scope() as session:
            generate_charter_review(
                session=session,
                week_ending=week_ending,
                now=now,
                recaps_dir=recaps_dir,
                backup_manifest_path=backup_manifest_path,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "charter_review_failed",
            extra={"week_ending": str(week_ending), "exception": str(exc)},
        )


def _last_sunday_on_or_before(d: date) -> date:
    """Mon=0..Sun=6. Returns d if Sunday, else d minus (weekday+1) days."""
    return d - timedelta(days=(d.weekday() + 1) % 7)
```

Registered in `build_scheduler`:

```python
scheduler.add_job(
    run_charter_review_weekly,
    CronTrigger(day_of_week="mon", hour=9, minute=30, timezone=timezone.utc),
    id="charter_review_weekly",
    misfire_grace_time=None,
    coalesce=True,
    replace_existing=True,
)
```

## Testing

### Renderer (`tests/ops/test_charter_review_renderer.py`, pure)

| Test | Asserts |
|---|---|
| `test_render_minimal_payload_byte_identical` | `render(p) == render(p)` — L17 |
| `test_render_includes_locked_sections` | Output contains all 6 section headers |
| `test_render_excess_return_formatting` | `Decimal("0.032")` → `"3.2%"`; None → `"N/A"`; `Decimal("-0.014")` → `"-1.4%"` |
| `test_render_delta_pp_signed_strings` | `_fmt_delta_pp(0.032, 0.018)` → `"+1.4 pp vs prior week"`; `(0.012, 0.030)` → `"−1.8 pp vs prior week"` |
| `test_render_delta_prior_week_na` | `_fmt_delta_pp(0.032, None)` → `"prior week N/A"` |
| `test_render_top_reasons_deterministic_order` | Two reasons same count → sorted by reason ASC |
| `test_render_reason_pipe_escaped` | Input reason = the literal string `alloc \| failed` (one pipe, single-backslash from the source, no escape) → rendered output contains `alloc \| failed` where the backslash is a real character in the markdown (the `\|` two-char sequence preserves the pipe inside markdown tables). Python literal: `assert "alloc \\| failed" in output` (L16) |
| `test_render_reason_truncated` | 250-char reason → first 200 + `…` (L16) |
| `test_render_reason_strips_newlines` | reason `"a\\nb\\rc"` → `"a b c"` (L16) |
| `test_render_this_week_empty` | trading_days_observed=0 → "No snapshots in this calendar week." line present |
| `test_render_both_weeks_empty` | All-empty payload still produces 6 section headers + week-window headers |
| `test_render_manifest_unavailable` | manifest_available=False → "Backup manifest unavailable"; `backup_is_stale=True` reported |
| `test_render_appendix_money_fields` | Appendix contains `cash_balance`, `holdings_mtm`, `portfolio_nav` from SnapshotAppendix |

### Aggregator (`tests/ops/test_charter_review_aggregator.py`, DB)

| Test | Asserts |
|---|---|
| `test_build_payload_empty_db` | No snapshots, no audit → first_snapshot_date None; all `value` fields None; `observations == 0` |
| `test_build_payload_trading_days_observed` | 3 in this-week, 5 in prior-week → counts match |
| `test_build_payload_week_window_inclusive` | Snapshot on `week_start` (Mon) AND `week_end` (Sun) both counted; next-Mon excluded |
| `test_build_payload_north_star_first_last` | 5 snapshots in week → first/last dates correct; excess_return_end from latest |
| `test_build_payload_tick_success_rate` | 18 TICK_COMPLETED + 2 ENGINE_INVARIANT_ERROR → value=18/20, obs=20, top_reasons from engine errors |
| `test_build_payload_rejection_top_reasons_sorted` | Counts (5,3,3,1,1) → returns 3 in (count desc, reason asc) order |
| `test_build_payload_trade_count_uses_fills` | Audit ORDER_ENTRY_FILLED present but absent paper_fill ENTRY → value=0 (L5) |
| `test_build_payload_engine_errors_observations` | engine_invariant_errors.observations = TICK_COMPLETED + ENGINE_INVARIANT_ERROR (L6) |
| `test_build_payload_engine_errors_reasons_only_from_engine` | Seed ORDER_REJECTED reasons in window — must NOT appear in engine_invariant_errors.top_reasons (L6) |
| `test_build_payload_top_reasons_null_normalized` | Seed 2 ENGINE_INVARIANT_ERROR rows with reason=NULL and 1 with reason="" → top_reasons returns single `ReasonCount("(no reason)", 3)` (L19) |
| `test_build_payload_manifest_none` | backup_manifest=None → manifest_available=False, backup_status="missing", backup_is_stale=True (L14) |
| `test_build_payload_manifest_ok` | status="ok" dict → manifest_available=True, fields populated |
| `test_build_payload_appendix_snapshot_latest` | 3 snapshots in week, dates D1<D2<D3 → appendix.trading_date == D3, money fields from D3 |
| `test_build_payload_does_not_touch_paper_position` | Seed `paper_position` rows; aggregator output unchanged from same payload without them (L3) |
| `test_build_payload_does_not_touch_paper_cash_ledger` | Seed `paper_cash_ledger` rows; output unchanged (L3) |

### Orchestration (`tests/ops/test_charter_review_orchestration.py`, DB + tmp_path FS)

| Test | Asserts |
|---|---|
| `test_generate_writes_markdown_and_latest_json` | File at expected path; `latest.json` parses; `path` matches |
| `test_generate_validates_week_ending_is_sunday` | `week_ending=Saturday` → raises `CharterReviewError` (L12) |
| `test_generate_missing_manifest_lands_file` | Manifest path missing → markdown still written; section reflects unavailable |
| `test_generate_malformed_manifest_lands_file` | Manifest `{not json}` → markdown still written; manifest_available=False |
| `test_generate_idempotent_same_week_same_now` | Two runs same `week_ending` + same `now` → byte-identical file content |
| `test_generate_atomic_write_no_orphan_tempfiles` | After successful run, no `.tmp` files in recaps_dir |
| `test_generate_atomic_write_preserves_old_md_on_failure` | Pre-existing `2026-08-16.md` + `os.replace` patched to raise on the .md write → old file unchanged, no `.tmp` orphan, no partial new content (L11) |
| `test_generate_atomic_write_preserves_old_latest_json_on_failure` | Pre-existing `latest.json` + `os.replace` patched to raise on the latest.json write (after .md succeeded) → old `latest.json` unchanged, no `.tmp` orphan (L11) |
| `test_generate_latest_json_atomic_replace` | Second run overwrites `latest.json`; no `.tmp` lingering |
| `test_generate_success_emits_info_log` | Successful run emits one info log `charter_review_generated` with extra={week_ending, path, generated_at} (L20) |
| `test_generate_db_query_failure_raises_typed` | Force aggregator to raise OperationalError → caller sees `CharterReviewError` wrapping the original |

### Scheduler (`tests/scheduler/test_charter_review_scheduler.py`)

| Test | Asserts |
|---|---|
| `test_run_charter_review_weekly_failure_logged_not_raised` | Monkeypatch `generate_charter_review` to raise → wrapper returns None, warning logged |
| `test_run_charter_review_weekly_skipped_for_non_sqlite` | Monkeypatch settings to postgres URL → info log, no exception, no file written |
| `test_last_sunday_on_or_before_monday` | `date(2026,8,17)` (Mon) → `date(2026,8,16)` (Sun) |
| `test_last_sunday_on_or_before_sunday` | `date(2026,8,16)` (Sun) → same day |

### Scheduler build invariants — extend `tests/scheduler/test_build_scheduler.py`

| Test | Asserts |
|---|---|
| Existing `test_daily_critical_jobs_have_no_misfire_grace` extended with `charter_review_weekly` | misfire_grace_time is None, coalesce is True |
| `test_charter_review_weekly_job_registered` | `cron[day_of_week='mon', hour='9', minute='30']` |

## Acceptance Criteria

1. `ruff check .` clean.
2. Full `pytest` suite green; no regressions in PR1/PR2/PR3a tests.
3. Cron `charter_review_weekly` registered with `misfire_grace_time=None, coalesce=True`.
4. `run_charter_review_weekly` never crashes the scheduler on failure (warning logged, function returns).
5. `generate_charter_review` raises `CharterReviewError` on DB/FS/render failure; tempfile cleanup guaranteed.
6. Same `(snapshot state, audit state, manifest, generated_at)` → byte-identical markdown.
7. `week_ending` not Sunday → typed error, no partial file written.
8. Pre-existing `<week_ending>.md` + atomic-replace failure → original file unchanged (L11).
9. Manual smoke (post-deploy):
   ```bash
   docker exec marketpulse python -c "from marketpulse.scheduler.jobs import run_charter_review_weekly; run_charter_review_weekly()"
   ```
   - Verify `/data/recaps/charter/<last-sunday>.md` exists.
   - Verify `/data/recaps/charter/latest.json` parses.
   - Verify markdown contains the 6 locked section headers.
10. Spec / code never reference HTML, email, Slack, AI client, YAML rules, condition tagging, or per-subsystem attribution.
11. Spec / code never read or write any DB table beyond `paper_nav_snapshot`, `paper_audit_event`, `paper_fill` (read-only).

## Forward Compatibility

- A future "report registry" route (`/recaps/charter/` browser) can be added by reading `latest.json` and the directory listing; no PR3b code changes needed.
- A future delivery integration (email / Slack) can subscribe to a new `charter_review_generated` log event; PR3b emits an info log with the written path on success.
- The renderer is pure and deterministic — golden-file tests can be added later without changing the contract.
- If the audit-event model evolves (PR3a L12 invariant changes), the rejection-rate metric in PR3b also needs revision. Aggregator is the single update site.
