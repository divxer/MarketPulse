# Task #57 — Nightly Eval-Analysis Job Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A nightly, post-close, eval-only scheduler job that AI-analyzes Watchlist ∪ current open paper holdings to grow `/lab/ai-track` evaluation data from ~13 outcomes to hundreds.

**Architecture:** A testable orchestration core (`marketpulse/ai/eval_analysis.py`) loops a prebuilt universe calling `AiService.analyze()` under a fresh-LLM-call cap; a thin composition-root job (`run_eval_analysis_job` in `scheduler/jobs.py`) builds the universe + AiService, runs the core, and persists a `status`-tagged coverage summary to `app_settings` (read by `/health/scheduler`). Registered as a daily-critical cron at 21:00 UTC Mon–Fri. Eval-only is double-enforced: the only side-effecting call is `analyze()`, and a CI architecture-guard test forbids allocator/order/watchlist-mutation imports.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, APScheduler `CronTrigger`, pydantic `BaseSettings`, structlog (`get_logger`), pytest.

**Spec:** `docs/superpowers/specs/2026-05-29-task-57-eval-analysis-job-design.md` (commit `177b08b`).

**Branch:** create `feat/task-57-eval-analysis-job` off `main` before Task 1.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `marketpulse/config.py` (modify) | 4 new `Settings` fields: `ai_eval_enabled`, `ai_eval_max_calls_per_day`, `ai_eval_hour`, `ai_eval_minute` |
| `marketpulse/ai/eval_analysis.py` (create) | `EvalAnalysisSummary` dataclass, `build_eval_universe`, `run_eval_analysis` core loop |
| `marketpulse/scheduler/eval_state.py` (create) | `record_eval_run_summary` / `get_eval_last_run_summary` (AppSetting JSON, mirrors `scheduler/state.py`) |
| `marketpulse/scheduler/jobs.py` (modify) | `run_eval_analysis_job` composition root + register cron in `build_scheduler()` |
| `tests/ai/test_eval_analysis.py` (create) | core loop + universe-builder unit tests |
| `tests/scheduler/test_eval_state.py` (create) | summary persistence round-trip |
| `tests/scheduler/test_eval_analysis_job.py` (create) | job: disabled / happy / failed / session-open-fail |
| `tests/scheduler/test_build_scheduler.py` (modify) | registration assertion |
| `tests/architecture/test_eval_only_invariant.py` (create) | forbidden-import guard |

---

## Task 1: Config fields

**Files:**
- Modify: `marketpulse/config.py` (the `Settings` class, near the other `ai_*` fields ~line 17-25)
- Test: `tests/test_config_eval.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_eval.py`:

```python
# Layer: test
"""Task #57 — AI eval-analysis Settings fields."""
from __future__ import annotations

from marketpulse.config import Settings


def test_eval_defaults_are_safe():
    s = Settings()
    assert s.ai_eval_enabled is False          # disabled by default — explicit opt-in
    assert s.ai_eval_max_calls_per_day == 60
    assert s.ai_eval_hour == 21                 # UTC
    assert s.ai_eval_minute == 0


def test_eval_fields_read_env(monkeypatch):
    monkeypatch.setenv("AI_EVAL_ENABLED", "true")
    monkeypatch.setenv("AI_EVAL_MAX_CALLS_PER_DAY", "25")
    monkeypatch.setenv("AI_EVAL_HOUR", "22")
    monkeypatch.setenv("AI_EVAL_MINUTE", "15")
    s = Settings()
    assert s.ai_eval_enabled is True
    assert s.ai_eval_max_calls_per_day == 25
    assert s.ai_eval_hour == 22
    assert s.ai_eval_minute == 15
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_eval.py -v`
Expected: FAIL with `AttributeError`/validation error — fields not defined.

- [ ] **Step 3: Implement the fields**

In `marketpulse/config.py`, inside the `Settings` class, immediately after the existing `ai_cache_ttl_hours` field (~line 25), add:

```python
    # Task #57 — nightly eval-analysis job. Disabled by default: it makes new
    # LLM calls, so enable explicitly on deploy (env flip + restart). The cap
    # counts FRESH LLM analyses per run (cache hits / errors do not consume it).
    ai_eval_enabled: bool = Field(False, alias="AI_EVAL_ENABLED")
    ai_eval_max_calls_per_day: int = Field(
        60, alias="AI_EVAL_MAX_CALLS_PER_DAY", ge=0,
    )
    ai_eval_hour: int = Field(21, alias="AI_EVAL_HOUR", ge=0, le=23)    # UTC
    ai_eval_minute: int = Field(0, alias="AI_EVAL_MINUTE", ge=0, le=59)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config_eval.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/config.py tests/test_config_eval.py
git commit -m "feat(task-57): AI eval-analysis Settings fields (disabled default)"
```

---

## Task 2: `EvalAnalysisSummary` dataclass

**Files:**
- Create: `marketpulse/ai/eval_analysis.py`
- Test: `tests/ai/test_eval_analysis.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/ai/test_eval_analysis.py`:

```python
# Layer: test
"""Task #57 — eval-analysis core: summary dataclass, universe, run loop."""
from __future__ import annotations

from datetime import date

from marketpulse.ai.eval_analysis import EvalAnalysisSummary


def test_summary_processed_and_invariant():
    s = EvalAnalysisSummary(
        run_date=date(2026, 5, 29), universe_size=7,
        analyzed_fresh=2, cache_hits=1, skipped_cap=3, errors=1, cap_hit=True,
    )
    assert s.processed == 4                      # fresh + cache_hits + errors
    assert s.processed + s.skipped_cap == s.universe_size


def test_summary_as_dict_ok():
    s = EvalAnalysisSummary(
        run_date=date(2026, 5, 29), universe_size=3,
        analyzed_fresh=3, cache_hits=0, skipped_cap=0, errors=0, cap_hit=False,
    )
    d = s.as_dict(status="ok")
    assert d == {
        "status": "ok", "run_date": date(2026, 5, 29), "universe_size": 3,
        "analyzed_fresh": 3, "cache_hits": 0, "skipped_cap": 0, "errors": 0,
        "cap_hit": False, "processed": 3,
    }
    assert "error" not in d


def test_summary_as_dict_failed_includes_error():
    s = EvalAnalysisSummary(date(2026, 5, 29), 0, 0, 0, 0, 0, False)
    d = s.as_dict(status="failed", error="boom")
    assert d["status"] == "failed"
    assert d["error"] == "boom"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ai/test_eval_analysis.py -v`
Expected: FAIL with `ModuleNotFoundError: marketpulse.ai.eval_analysis`.

- [ ] **Step 3: Implement the dataclass**

Create `marketpulse/ai/eval_analysis.py`:

```python
# Layer: orchestration
"""Task #57 — nightly eval-analysis core (testable service/orchestration layer).

Eval-ONLY: the only side-effecting call here is `AiService.analyze()`, which
writes AiAnalysis + EvaluationEvent and commits internally. This module must
never import the allocator / order-placement / watchlist-mutation layers — a CI
architecture guard (tests/architecture/test_eval_only_invariant.py) enforces it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from marketpulse.db.models import WatchlistItem
from marketpulse.logging import get_logger
from marketpulse.trading.repository import PaperPositionRepository

log = get_logger(__name__)


@dataclass(frozen=True)
class EvalAnalysisSummary:
    run_date: date
    universe_size: int
    analyzed_fresh: int
    cache_hits: int
    skipped_cap: int
    errors: int
    cap_hit: bool

    @property
    def processed(self) -> int:
        return self.analyzed_fresh + self.cache_hits + self.errors

    def as_dict(self, *, status: str, error: str | None = None) -> dict:
        """status ∈ {"ok", "disabled", "failed"}. `ts` is added by the
        persistence layer (record_eval_run_summary), keeping this clock-free."""
        d = {
            "status": status,
            "run_date": self.run_date,
            "universe_size": self.universe_size,
            "analyzed_fresh": self.analyzed_fresh,
            "cache_hits": self.cache_hits,
            "skipped_cap": self.skipped_cap,
            "errors": self.errors,
            "cap_hit": self.cap_hit,
            "processed": self.processed,
        }
        if error is not None:
            d["error"] = error
        return d
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ai/test_eval_analysis.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/ai/eval_analysis.py tests/ai/test_eval_analysis.py
git commit -m "feat(task-57): EvalAnalysisSummary dataclass + as_dict"
```

---

## Task 3: `build_eval_universe`

**Files:**
- Modify: `marketpulse/ai/eval_analysis.py`
- Test: `tests/ai/test_eval_analysis.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/ai/test_eval_analysis.py`:

```python
from decimal import Decimal
from datetime import UTC, datetime

from marketpulse.ai.eval_analysis import build_eval_universe
from marketpulse.db.models import PaperPosition, WatchlistItem


def _add_watch(session, ticker, order=0):
    session.add(WatchlistItem(ticker=ticker, sort_order=order))


def _add_open_position(session, ticker):
    session.add(PaperPosition(
        ticker=ticker, quantity=Decimal("1"), entry_price=Decimal("10"),
        status="OPEN", opened_at=datetime(2026, 5, 1, tzinfo=UTC),
        allocation_date=date(2026, 5, 1), horizon_date=date(2026, 6, 1),
    ))


def _add_closed_position(session, ticker):
    session.add(PaperPosition(
        ticker=ticker, quantity=Decimal("1"), entry_price=Decimal("10"),
        status="CLOSED", opened_at=datetime(2026, 5, 1, tzinfo=UTC),
        closed_at=datetime(2026, 5, 10, tzinfo=UTC),
        allocation_date=date(2026, 5, 1), horizon_date=date(2026, 6, 1),
    ))


def test_universe_union_dedup_sorted(db_session):
    _add_watch(db_session, "AAPL")
    _add_watch(db_session, "MSFT")
    _add_open_position(db_session, "AAPL")     # overlaps watchlist
    _add_open_position(db_session, "QUBT")     # holding not on watchlist
    db_session.commit()
    assert build_eval_universe(db_session) == ["AAPL", "MSFT", "QUBT"]


def test_universe_normalizes_case_and_whitespace(db_session):
    _add_watch(db_session, " aapl ")
    _add_open_position(db_session, "qubt")
    db_session.commit()
    assert build_eval_universe(db_session) == ["AAPL", "QUBT"]


def test_universe_excludes_closed_positions(db_session):
    _add_closed_position(db_session, "TSLA")
    db_session.commit()
    assert build_eval_universe(db_session) == []


def test_universe_empty(db_session):
    assert build_eval_universe(db_session) == []
```

NOTE: If `PaperPosition`'s required columns differ from the kwargs above, open
`marketpulse/db/models.py` (`class PaperPosition`) and supply the actual NOT-NULL
columns. The test only depends on `ticker` + `status`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ai/test_eval_analysis.py -k universe -v`
Expected: FAIL with `ImportError: cannot import name 'build_eval_universe'`.

- [ ] **Step 3: Implement `build_eval_universe`**

Append to `marketpulse/ai/eval_analysis.py` (after the dataclass):

```python
def build_eval_universe(session) -> list[str]:
    """Watchlist ∪ current open paper holdings, normalized + deduped + sorted ASC.

    Reads only: WatchlistItem (model) and the canonical open-positions helper
    (`PaperPositionRepository.open_positions_snapshot`, status == "OPEN"). No
    mutation. Sorted ASC so the cap-skip set is deterministic.
    """
    watch_rows = session.query(WatchlistItem.ticker).all()
    holdings = PaperPositionRepository(session=session).open_positions_snapshot()
    raw = [r[0] for r in watch_rows] + [p.ticker for p in holdings]
    normalized = {t.strip().upper() for t in raw if t and t.strip()}
    return sorted(normalized)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ai/test_eval_analysis.py -k universe -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/ai/eval_analysis.py tests/ai/test_eval_analysis.py
git commit -m "feat(task-57): build_eval_universe (watchlist ∪ open holdings)"
```

---

## Task 4: `run_eval_analysis` core loop

**Files:**
- Modify: `marketpulse/ai/eval_analysis.py`
- Test: `tests/ai/test_eval_analysis.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/ai/test_eval_analysis.py`:

```python
from unittest.mock import MagicMock

from marketpulse.ai.eval_analysis import run_eval_analysis
from marketpulse.ai.types import AnalysisResult


class FakeAi:
    """analyze() returns AnalysisResult; `behaviors` maps ticker -> 'fresh' |
    'cached' | 'raise'. Records the call order."""
    def __init__(self, behaviors):
        self.behaviors = behaviors
        self.calls = []

    def analyze(self, ticker):
        self.calls.append(ticker)
        b = self.behaviors.get(ticker, "fresh")
        if b == "raise":
            raise RuntimeError(f"boom {ticker}")
        return AnalysisResult(
            ticker=ticker, model="m", prompt_version="v",
            response_markdown="x",
            requested_at=datetime(2026, 5, 29, tzinfo=UTC),
            cached=(b == "cached"),
        )


RUN_DATE = date(2026, 5, 29)


def test_all_fresh_under_cap():
    ai = FakeAi({})
    s = run_eval_analysis(MagicMock(), ai=ai, universe=["A", "B", "C"],
                          max_calls=60, run_date=RUN_DATE)
    assert (s.analyzed_fresh, s.cache_hits, s.skipped_cap, s.cap_hit) == (3, 0, 0, False)
    assert s.processed + s.skipped_cap == s.universe_size


def test_same_day_rerun_all_cached_no_cap():
    ai = FakeAi({"A": "cached", "B": "cached"})
    s = run_eval_analysis(MagicMock(), ai=ai, universe=["A", "B"],
                          max_calls=1, run_date=RUN_DATE)
    # cache hits do NOT consume cap, so a cap of 1 still processes both
    assert (s.analyzed_fresh, s.cache_hits, s.skipped_cap, s.cap_hit) == (0, 2, 0, False)


def test_cap_hit_midloop_counts_fresh_only():
    ai = FakeAi({})
    s = run_eval_analysis(MagicMock(), ai=ai, universe=["A", "B", "C", "D"],
                          max_calls=2, run_date=RUN_DATE)
    assert (s.analyzed_fresh, s.skipped_cap, s.cap_hit) == (2, 2, True)
    assert ai.calls == ["A", "B"]                 # stopped after 2 fresh


def test_cache_hits_do_not_consume_cap():
    # A cached, B fresh, C cached, D fresh, E fresh; cap=2 fresh
    ai = FakeAi({"A": "cached", "C": "cached"})
    s = run_eval_analysis(MagicMock(), ai=ai, universe=["A", "B", "C", "D", "E"],
                          max_calls=2, run_date=RUN_DATE)
    assert s.analyzed_fresh == 2 and s.cache_hits == 2
    assert s.cap_hit is True and s.skipped_cap == 1    # E skipped
    assert s.processed + s.skipped_cap == 5


def test_max_calls_zero_nonempty_universe():
    ai = FakeAi({})
    s = run_eval_analysis(MagicMock(), ai=ai, universe=["A", "B"],
                          max_calls=0, run_date=RUN_DATE)
    assert (s.analyzed_fresh, s.skipped_cap, s.cap_hit) == (0, 2, True)
    assert ai.calls == []


def test_max_calls_zero_empty_universe_no_cap_hit():
    ai = FakeAi({})
    s = run_eval_analysis(MagicMock(), ai=ai, universe=[],
                          max_calls=0, run_date=RUN_DATE)
    assert (s.analyzed_fresh, s.skipped_cap, s.cap_hit) == (0, 0, False)


def test_per_ticker_error_rolls_back_and_continues():
    session = MagicMock()
    ai = FakeAi({"B": "raise"})
    s = run_eval_analysis(session, ai=ai, universe=["A", "B", "C"],
                          max_calls=60, run_date=RUN_DATE)
    assert (s.analyzed_fresh, s.errors, s.cap_hit) == (2, 1, False)
    assert ai.calls == ["A", "B", "C"]            # error didn't abort
    session.rollback.assert_called_once()          # cleaned partial state
    assert s.processed + s.skipped_cap == 3


def test_mixed_path_invariant():
    # fresh=2 (A,D), cache_hit=1 (B), error=1 (C) over 7, cap=2 → E,F,G skipped
    ai = FakeAi({"B": "cached", "C": "raise"})
    s = run_eval_analysis(MagicMock(), ai=ai,
                          universe=["A", "B", "C", "D", "E", "F", "G"],
                          max_calls=2, run_date=RUN_DATE)
    assert s.analyzed_fresh == 2 and s.cache_hits == 1 and s.errors == 1
    assert s.processed == 4 and s.skipped_cap == 3
    assert s.processed + s.skipped_cap == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ai/test_eval_analysis.py -k "fresh or cap or error or mixed or rerun or max_calls" -v`
Expected: FAIL with `ImportError: cannot import name 'run_eval_analysis'`.

- [ ] **Step 3: Implement `run_eval_analysis`**

Append to `marketpulse/ai/eval_analysis.py`:

```python
def run_eval_analysis(session, *, ai, universe, max_calls, run_date) -> EvalAnalysisSummary:
    """Loop the prebuilt universe calling ai.analyze() under a FRESH-call cap.

    Cap semantics: cache hits and errors do NOT consume the budget; only fresh
    (cached=False) analyses do. Reaching the cap stops the loop, sets cap_hit and
    skipped_cap. max_calls <= 0 stops before the first call (the `fresh >=
    max_calls` guard fires at 0 >= 0) — but only when the universe is non-empty,
    so an empty universe never sets cap_hit.

    Per-ticker errors are isolated: analyze() commits internally, so a raise can
    leave partial uncommitted state — rollback before the next ticker. Never
    raises; always returns a summary.
    """
    fresh = cache_hits = errors = 0
    cap_hit = False
    processed = 0
    skipped = 0
    for ticker in universe:
        if fresh >= max_calls:                    # cap counts FRESH calls only
            cap_hit = True
            skipped = len(universe) - processed
            log.warning(
                "ai_eval_daily_cap_hit", fresh=fresh,
                universe_size=len(universe), skipped=skipped,
                run_date=str(run_date),
            )
            break
        try:
            result = ai.analyze(ticker)           # commits AiAnalysis + EvaluationEvent
            if result.cached:
                cache_hits += 1
            else:
                fresh += 1
        except Exception as exc:                  # per-ticker isolation
            session.rollback()                    # clean partial state before next
            errors += 1
            log.warning("ai_eval_ticker_failed", ticker=ticker, error=str(exc))
        processed += 1
    return EvalAnalysisSummary(
        run_date=run_date, universe_size=len(universe),
        analyzed_fresh=fresh, cache_hits=cache_hits, skipped_cap=skipped,
        errors=errors, cap_hit=cap_hit,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ai/test_eval_analysis.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/ai/eval_analysis.py tests/ai/test_eval_analysis.py
git commit -m "feat(task-57): run_eval_analysis core loop (fresh-call cap, per-ticker rollback)"
```

---

## Task 5: Summary persistence (`eval_state.py`)

**Files:**
- Create: `marketpulse/scheduler/eval_state.py`
- Test: `tests/scheduler/test_eval_state.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/scheduler/test_eval_state.py`:

```python
# Layer: test
"""Task #57 — eval-analysis last-run summary persistence."""
from __future__ import annotations

from datetime import date

from marketpulse.scheduler.eval_state import (
    get_eval_last_run_summary,
    record_eval_run_summary,
)


def _payload(status="ok"):
    return {
        "status": status, "run_date": date(2026, 5, 29), "universe_size": 3,
        "analyzed_fresh": 3, "cache_hits": 0, "skipped_cap": 0, "errors": 0,
        "cap_hit": False, "processed": 3,
    }


def test_get_returns_none_when_never_run(db_session):
    assert get_eval_last_run_summary(db_session) is None


def test_record_then_get_roundtrip_adds_ts(db_session):
    record_eval_run_summary(db_session, _payload())
    got = get_eval_last_run_summary(db_session)
    assert got["status"] == "ok"
    assert got["run_date"] == "2026-05-29"        # date coerced via str() in JSON
    assert got["processed"] == 3
    assert "ts" in got                             # added by the recorder


def test_record_overwrites_previous(db_session):
    record_eval_run_summary(db_session, _payload(status="ok"))
    record_eval_run_summary(db_session, _payload(status="disabled"))
    got = get_eval_last_run_summary(db_session)
    assert got["status"] == "disabled"             # single row, overwritten
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/scheduler/test_eval_state.py -v`
Expected: FAIL with `ModuleNotFoundError: marketpulse.scheduler.eval_state`.

- [ ] **Step 3: Implement `eval_state.py`**

Create `marketpulse/scheduler/eval_state.py`:

```python
# Layer: db
"""Task #57 — eval-analysis last-run summary persistence.

Mirrors scheduler/state.py: one JSON blob in the app_settings key-value table
(no migration). Read by /health/scheduler. The recorder stamps `ts` (UTC) so the
core summary stays clock-free and unit-testable.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from marketpulse.db.models import AppSetting

_LAST_RUN_KEY = "scheduler.ai_eval_analysis.last_run"


def record_eval_run_summary(session: Session, summary: dict[str, Any]) -> None:
    """Persist the latest run summary (overwrites prior). Stamps `ts` (UTC).
    Commits within. Dates/datetimes coerced via str() in JSON."""
    payload_dict = {**summary, "ts": datetime.now(UTC).isoformat()}
    payload = json.dumps(payload_dict, default=str)
    row = (
        session.query(AppSetting)
        .filter(AppSetting.key == _LAST_RUN_KEY)
        .one_or_none()
    )
    if row:
        row.value = payload
    else:
        session.add(AppSetting(key=_LAST_RUN_KEY, value=payload))
    session.commit()


def get_eval_last_run_summary(session: Session) -> dict[str, Any] | None:
    """Return the most recent run summary, or None if never run."""
    row = (
        session.query(AppSetting)
        .filter(AppSetting.key == _LAST_RUN_KEY)
        .one_or_none()
    )
    if not row:
        return None
    return json.loads(row.value)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/scheduler/test_eval_state.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/scheduler/eval_state.py tests/scheduler/test_eval_state.py
git commit -m "feat(task-57): eval-analysis last-run summary persistence"
```

---

## Task 6: `run_eval_analysis_job` composition root

**Files:**
- Modify: `marketpulse/scheduler/jobs.py` (add the job function; imports near top)
- Test: `tests/scheduler/test_eval_analysis_job.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/scheduler/test_eval_analysis_job.py`:

```python
# Layer: test
"""Task #57 — run_eval_analysis_job composition root."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import marketpulse.scheduler.jobs as jobs_mod
from marketpulse.scheduler.jobs import run_eval_analysis_job


@pytest.fixture()
def wired(db_session, monkeypatch):
    """Wire the job to a real db_session; stub network-touching constructors."""
    def fake_session_scope():
        yield db_session
    monkeypatch.setattr(jobs_mod, "session_scope", fake_session_scope)
    monkeypatch.setattr(jobs_mod, "_build_quote_client", lambda: MagicMock())
    monkeypatch.setattr(jobs_mod, "DataService", lambda *a, **kw: MagicMock())
    # AnthropicClient() is evaluated even when AiService is mocked (it's the
    # ai_client= arg) — stub it so its real constructor can't touch the network
    # or require an API key.
    monkeypatch.setattr(jobs_mod, "AnthropicClient", lambda *a, **kw: MagicMock())
    return db_session


def _settings(monkeypatch, **over):
    from marketpulse.config import Settings
    base = dict(ai_eval_enabled=True, ai_eval_max_calls_per_day=60)
    base.update(over)
    monkeypatch.setattr(jobs_mod, "get_settings", lambda: Settings(**base))


def test_disabled_records_disabled_summary_no_analyze(wired, monkeypatch):
    _settings(monkeypatch, ai_eval_enabled=False)
    fake_ai = MagicMock()
    monkeypatch.setattr(jobs_mod, "AiService", lambda *a, **kw: fake_ai)

    run_eval_analysis_job()

    from marketpulse.scheduler.eval_state import get_eval_last_run_summary
    got = get_eval_last_run_summary(wired)
    assert got["status"] == "disabled"
    fake_ai.analyze.assert_not_called()


def test_happy_path_records_ok(wired, monkeypatch):
    _settings(monkeypatch, ai_eval_enabled=True)
    monkeypatch.setattr(jobs_mod, "AiService", lambda *a, **kw: MagicMock())
    monkeypatch.setattr(jobs_mod, "build_eval_universe", lambda s: [])

    run_eval_analysis_job()

    from marketpulse.scheduler.eval_state import get_eval_last_run_summary
    got = get_eval_last_run_summary(wired)
    assert got["status"] == "ok"
    assert got["universe_size"] == 0


def test_job_boundary_failure_records_failed_no_raise(wired, monkeypatch):
    _settings(monkeypatch, ai_eval_enabled=True)
    monkeypatch.setattr(jobs_mod, "AiService", lambda *a, **kw: MagicMock())

    def boom(_session):
        raise RuntimeError("universe build failed")
    monkeypatch.setattr(jobs_mod, "build_eval_universe", boom)

    run_eval_analysis_job()                         # must NOT raise

    from marketpulse.scheduler.eval_state import get_eval_last_run_summary
    got = get_eval_last_run_summary(wired)
    assert got["status"] == "failed"
    assert "error" in got


def test_session_open_failure_logs_only_no_summary(monkeypatch):
    _settings(monkeypatch, ai_eval_enabled=True)

    def fake_session_scope():
        raise RuntimeError("db down")
        yield  # pragma: no cover
    monkeypatch.setattr(jobs_mod, "session_scope", fake_session_scope)

    # Must not raise; physically cannot persist a summary (no session).
    run_eval_analysis_job()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/scheduler/test_eval_analysis_job.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_eval_analysis_job'`.

- [ ] **Step 3: Implement the job**

In `marketpulse/scheduler/jobs.py`, add these imports near the existing
`from marketpulse.ai.service import AiService` (line 9) block:

```python
from marketpulse.ai.eval_analysis import (
    EvalAnalysisSummary,
    build_eval_universe,
    run_eval_analysis,
)
from marketpulse.scheduler.eval_state import record_eval_run_summary
```

Then add the job function (place it after `run_outcome_computation`, ~line 450):

```python
def run_eval_analysis_job() -> None:
    """Task #57 — nightly eval-only analysis of watchlist ∪ open holdings.

    Gated by AI_EVAL_ENABLED (disabled → records a 'disabled' summary). Never
    raises: job-boundary failures record a 'failed' summary (if the session is
    usable) and log; the scheduler must never crash.
    """
    settings = get_settings()
    run_date = datetime.now(UTC).date()             # UTC — the cron is UTC
    gen = None
    db = None
    summary = None
    status = "ok"
    try:
        gen = session_scope()                       # generator helper (NOT a CM)
        db = next(gen)
        if not settings.ai_eval_enabled:
            status = "disabled"
            summary = EvalAnalysisSummary(run_date, 0, 0, 0, 0, 0, False)
            log.info("ai_eval_disabled")
            return                                  # finally persists disabled summary
        data = DataService(
            db, _build_quote_client(),
            news_ttl_days=settings.news_cache_ttl_days,
        )
        ai = AiService(
            db, ai_client=AnthropicClient(), data=data,
            model=settings.ai_model, ttl_hours=settings.ai_cache_ttl_hours,
            model_analyze=settings.ai_model_analyze or None,
            model_router=settings.ai_model_router or None,
        )
        universe = build_eval_universe(db)
        summary = run_eval_analysis(
            db, ai=ai, universe=universe,
            max_calls=settings.ai_eval_max_calls_per_day, run_date=run_date,
        )
        log.info("ai_eval_done", **summary.as_dict(status="ok"))
    except Exception as exc:                         # job-boundary failure — never re-raised
        status = "failed"
        summary = None                               # don't let finally persist a stale ok
        log.warning("ai_eval_job_failed", error=str(exc))
        if db is not None:                           # can only persist if the session opened
            try:
                record_eval_run_summary(
                    db,
                    EvalAnalysisSummary(run_date, 0, 0, 0, 0, 0, False)
                    .as_dict(status="failed", error=str(exc)),
                )
            except Exception:
                pass
        # db is None (session never opened) → log-only, no persisted summary.
    finally:
        if summary is not None and status in ("ok", "disabled"):
            try:
                record_eval_run_summary(db, summary.as_dict(status=status))
            except Exception as exc:
                log.warning("ai_eval_summary_persist_failed", error=str(exc))
        if gen is not None:
            gen.close()                              # runs the generator's finally → db.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/scheduler/test_eval_analysis_job.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/scheduler/jobs.py tests/scheduler/test_eval_analysis_job.py
git commit -m "feat(task-57): run_eval_analysis_job composition root (gate + status summary)"
```

---

## Task 7: Register the cron in `build_scheduler()`

**Files:**
- Modify: `marketpulse/scheduler/jobs.py` (`build_scheduler`, near the other `add_job` calls ~line 685)
- Test: `tests/scheduler/test_build_scheduler.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/scheduler/test_build_scheduler.py`:

```python
def test_eval_analysis_job_registered():
    """Task #57: eval-analysis cron at 21:00 UTC Mon-Fri."""
    sched = build_scheduler()
    job = sched.get_job("ai_eval_analysis")
    assert job is not None, "ai_eval_analysis cron must be registered"
    trigger_repr = str(job.trigger)
    assert "hour='21'" in trigger_repr or "hour=21" in trigger_repr, trigger_repr
    assert "minute='0'" in trigger_repr or "minute=0" in trigger_repr, trigger_repr
    assert "day_of_week='mon-fri'" in trigger_repr, trigger_repr


def test_eval_analysis_is_daily_critical():
    """Missed runs lose unrecoverable eval data (analyze uses live quotes), so
    it must catch up on next boot like the other daily-critical jobs."""
    sched = build_scheduler()
    job = sched.get_job("ai_eval_analysis")
    assert job.misfire_grace_time is None, job.misfire_grace_time
    assert job.coalesce is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/scheduler/test_build_scheduler.py -k eval -v`
Expected: FAIL — `get_job("ai_eval_analysis")` returns `None`.

- [ ] **Step 3: Register the job**

In `marketpulse/scheduler/jobs.py`, inside `build_scheduler()`, after the
`outcome_computation` `add_job` block (~line 685-690), add:

```python
    # Task #57 — nightly eval-analysis at 21:00 UTC Mon-Fri (post-close, ~17:00
    # ET EDT). Daily-critical: a missed run loses that day's eval verdicts (they
    # can't be backfilled — analyze() uses live quotes), so no misfire grace +
    # coalesce, matching outcome_computation / paper_trading_tick.
    sched.add_job(
        run_eval_analysis_job,
        trigger=CronTrigger(
            hour=settings.ai_eval_hour, minute=settings.ai_eval_minute,
            day_of_week="mon-fri", timezone="UTC",
        ),
        id="ai_eval_analysis",
        replace_existing=True,
        misfire_grace_time=None,
        coalesce=True,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/scheduler/test_build_scheduler.py -k eval -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/scheduler/jobs.py tests/scheduler/test_build_scheduler.py
git commit -m "feat(task-57): register ai_eval_analysis cron (21:00 UTC mon-fri, daily-critical)"
```

---

## Task 8: Eval-only architecture guard

**Files:**
- Create: `tests/architecture/test_eval_only_invariant.py`

- [ ] **Step 1: Write the failing test**

Create `tests/architecture/test_eval_only_invariant.py`:

```python
# Layer: test
"""Task #57 — eval-only invariant: the eval-analysis core must never import the
allocator / order-placement / watchlist-mutation layers. Scans only import lines
so a module name in a docstring/comment can't cause a false positive."""
from __future__ import annotations

from pathlib import Path

_MODULE = (
    Path(__file__).resolve().parents[2]
    / "marketpulse" / "ai" / "eval_analysis.py"
)

_FORBIDDEN = (
    "marketpulse.trading.execution_engine",
    "marketpulse.trading.forward_engine",
    "marketpulse.trading.daily_cycle",
    "marketpulse.trading.bid_aggregator",
    "marketpulse.backtest.allocation",      # allocate_for_day kernel
    "marketpulse.broker.order_service",     # order placement
    "marketpulse.web.routes.watchlist",     # watchlist mutation (add/delete)
)


def _import_lines(path: Path) -> list[str]:
    out = []
    for raw in path.read_text().splitlines():
        s = raw.strip()
        if s.startswith("import ") or s.startswith("from "):
            out.append(s)
    return out


def test_eval_analysis_has_no_forbidden_imports():
    lines = _import_lines(_MODULE)
    for forbidden in _FORBIDDEN:
        offenders = [ln for ln in lines if forbidden in ln]
        assert not offenders, (
            f"eval_analysis.py must not import {forbidden} "
            f"(eval-only invariant). Offending lines: {offenders}"
        )
```

- [ ] **Step 2: Run test to verify it passes immediately (guard already satisfied)**

Run: `pytest tests/architecture/test_eval_only_invariant.py -v`
Expected: PASS — the current `eval_analysis.py` imports only `WatchlistItem`,
`get_logger`, `PaperPositionRepository` (all allowed). (This is a guard test; it
passes on correct code and only fails if a future edit adds a forbidden import.)

- [ ] **Step 3: Sanity-check the guard actually catches violations (temporary)**

Temporarily add `from marketpulse.broker.order_service import *  # noqa` to the
top of `marketpulse/ai/eval_analysis.py`, then run:

Run: `pytest tests/architecture/test_eval_only_invariant.py -v`
Expected: FAIL — confirms the guard works. **Then remove the temporary line** and
re-run to confirm PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/architecture/test_eval_only_invariant.py
git commit -m "test(task-57): eval-only architecture guard (no allocator/order/watchlist imports)"
```

---

## Task 9: Final integration

**Files:**
- No new code; verification + cleanup only.

- [ ] **Step 1: Run the full suite**

Run: `pytest -q`
Expected: all green (existing + ~25 new tests). If any pre-existing test now fails
because of the new imports/job, investigate before proceeding.

- [ ] **Step 2: Lint**

Run: `ruff check marketpulse/ tests/`
Expected: no errors. Fix any in the new files (unused imports, line length).

- [ ] **Step 3: Import + scheduler smoke**

Run:
```bash
python -c "from marketpulse.scheduler.jobs import build_scheduler; s=build_scheduler(); print(s.get_job('ai_eval_analysis').trigger)"
```
Expected: prints a cron trigger containing `hour='21'`, `minute='0'`,
`day_of_week='mon-fri'`, timezone UTC.

- [ ] **Step 4: Confirm eval-only guard + job tests pass together**

Run: `pytest tests/ai/test_eval_analysis.py tests/scheduler/test_eval_analysis_job.py tests/scheduler/test_eval_state.py tests/architecture/test_eval_only_invariant.py tests/scheduler/test_build_scheduler.py tests/test_config_eval.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit (if any lint fixups were made)**

```bash
git add -A
git commit -m "chore(task-57): final integration — full suite + ruff clean" || echo "nothing to commit"
```

---

## Rollout (post-merge, operator)

1. Merged with `AI_EVAL_ENABLED=false` → no behavior change in prod.
2. On the NAS: set `AI_EVAL_ENABLED=true` (optionally `AI_EVAL_MAX_CALLS_PER_DAY`)
   and restart the container.
3. Watch `/health/scheduler` for the first `status="ok"` summary; confirm
   `analyzed_fresh ≈ universe_size`, `errors=0`.
4. After ~5 sessions, confirm `/lab/ai-track` h1/h5 outcome counts grow materially.

---

## Notes for the implementer

- **Logging:** all new modules use `log = get_logger(__name__)` (structlog,
  kwargs-style: `log.info("event", key=value)`). Do NOT use stdlib
  `logging.getLogger(...).info(..., extra={...})`.
- **`session_scope` is a plain generator**, not a context manager — use
  `gen = session_scope(); db = next(gen)` … `gen.close()`. Never `with`.
- **`AiService.analyze()` commits internally** — that's why the core's per-ticker
  `except` calls `session.rollback()` and the job needs no trailing commit for the
  analyses (only the summary write commits, inside `record_eval_run_summary`).
- **`PaperPositionRepository(session=...)`** — `session` is keyword-only.
- If `PaperPosition` test construction (Task 3) needs more NOT-NULL columns than
  shown, read `class PaperPosition` in `marketpulse/db/models.py` and add them;
  the universe logic only depends on `ticker` + `status`.
```
