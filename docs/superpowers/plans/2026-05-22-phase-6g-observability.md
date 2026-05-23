# Phase 6g Observability + Alerting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `paper_audit_event` stream into operator push notifications via post-tick hook. 6g is a strict consumer; no new trading state.

**Architecture:** Audit-driven consumer projection. `notify_paper_tick_events(since, tick_date, repository, notifier, clock)` runs after `daily_cycle.run()` completes. Critical events emit standalone pushes (per §3.1); routine activity is summarized once per tick. PRICE_UNAVAILABLE stuck/recovery + KILL_SWITCH dedup all derive from audit history (no new state).

**Tech Stack:** Python 3.12+ • SQLAlchemy 2 • existing alerts/notifier.py Protocol • httpx (already used) • argparse (stdlib).

**Spec reference:** `docs/superpowers/specs/2026-05-22-phase-6g-observability-design.md` (**21 numbered locks 6g-L1..6g-L21 + 3 L4 sublocks (L4a/L4b/L4c) = 23 lock entries total**).

**Branch:** `plan/phase-6g-observability` (already checked out). Single squash-or-rebase PR at end of T9.

**Baseline assumptions (verified at T0):**
- `alembic heads` → `0011 (head)` (6b+ landed; 6g adds NO migration).
- `paper_audit_event` CHECK accepts all 13 event types including `PRICE_UNAVAILABLE`.
- `Settings` already has `notifier_kind` / `notifier_bark_url` / `notifier_serverchan_key` / `notifier_smtp_*` / `notifier_recap_enabled`.
- `Repository` already has `count_price_unavailable_attempts(*, position_id)`.
- `AuditEventType` StrEnum already contains 13 values (no enum edits in 6g).
- `paper_trading_tick_job() -> None` (no args). T7 adds `*, notifier=None` kwarg.

---

## Task Inventory

- **T0** — Preflight: branch + baseline (pytest, ruff, alembic head 0011).
- **T1** — Settings flag `MP_PAPER_NOTIFICATIONS_ENABLED` + `get_notifier_from_settings` wrapper (6g-L13, 6g-L15).
- **T2** — Repository 4 new read-only helpers: `positions_with_prior_price_unavailable`, `kill_switch_cycle_skipped_in_active_period`, `latest_tick_completed_timestamp`, `latest_price_unavailable_attempt_counts` (6g-L5, 6g-L17, 6g-L20, 6g-L4c global).
- **T3** — Pure projection dataclasses (`NotificationFailure`, `CriticalEvent`, `PlacedOrderDetail`, `TickSummary`) + `select_critical_events` (6g-L3, 6g-L4a, 6g-L4b, 6g-L4c, 6g-L5, 6g-L9).
- **T4** — Pure `summarize_tick` with fallback semantics (6g-L21).
- **T5** — `templates.py` renderers: critical + summary, money formatting, empty-section skipping, `4+` cap (6g-L10, 6g-L11, 6g-L12).
- **T6** — `paper_tick_notifier.py` entrypoint with windows, dedup, per-event isolation, disabled-path (6g-L1, 6g-L7, 6g-L13, 6g-L14, 6g-L15, 6g-L19, 6g-L20).
- **T7** — Scheduler hook: `paper_trading_tick_job(*, notifier=None)` + best-effort post-tick `notify_paper_tick_events` (6g-L1).
- **T8** — `republish_cli.py` with `--date YYYY-MM-DD`, disabled-config guard, NotificationResult stdout (6g-L8, 6g-L18).
- **T9** — Final integration: full suite + ruff + alembic head still 0011 + smoke + PR.

---

## File Structure

**New files (5 production + 9 tests):**

```
marketpulse/observability/__init__.py
marketpulse/observability/audit_projection.py        # T3 + T4
marketpulse/observability/templates.py               # T5
marketpulse/observability/paper_tick_notifier.py     # T6
marketpulse/observability/republish_cli.py           # T8

tests/observability/__init__.py
tests/observability/test_audit_projection_critical.py  # T3
tests/observability/test_audit_projection_summary.py   # T4
tests/observability/test_templates.py                  # T5
tests/observability/test_paper_tick_notifier.py        # T6
tests/observability/test_republish_cli.py              # T8
tests/trading/test_paper_tick_notifies_after_run.py    # T7
tests/trading/test_repository_observability_helpers.py # T2
tests/unit/test_settings_paper_notifications.py        # T1
tests/unit/test_notifier_factory.py                    # T1
```

**Modified files (3 production):**

```
marketpulse/config.py                              # T1
marketpulse/alerts/notifier.py                     # T1
marketpulse/trading/repository.py                  # T2
marketpulse/scheduler/paper_trading_tick.py        # T7
```

---

### Task T0: Preflight

**Files:** read-only verification.

**Locks-Referenced:** baseline only — no lock implementation.

- [ ] **Step 1: Verify branch + clean working tree**

Run: `git status && git log --oneline -5`
Expected: on branch `plan/phase-6g-observability`; working tree clean. HEAD is the 6g spec commit or the 6b+ tail.

- [ ] **Step 2: Baseline pytest green**

Run: `uv run pytest -q --tb=no | tail -3`
Expected: ALL pass. Total ~1163 tests (post-6b+T18 from recent commits `7e942ab` / `205015e` / `bea39ba`). If anything red, STOP and investigate before 6g.

- [ ] **Step 3: Ruff clean**

Run: `uv run ruff check marketpulse/ tests/`
Expected: `All checks passed!`

- [ ] **Step 4: Alembic head**

Run: `uv run alembic heads`
Expected: `0011 (head)`. 6g adds NO migration; head stays 0011 at the end of T9.

- [ ] **Step 5: Confirm spec presence**

Run: `ls -la docs/superpowers/specs/2026-05-22-phase-6g-observability-design.md`
Expected: file exists and is the 23-lock finalized version. (Plan references the locks by ID; the implementer never needs to open the spec while executing.)

- [ ] **Step 6: Smoke import existing pieces**

Run:
```bash
uv run python -c "
from marketpulse.alerts.notifier import build_notifier, Notifier, NoopNotifier, BarkNotifier, ServerChanNotifier, SmtpNotifier
from marketpulse.config import Settings, get_settings
from marketpulse.trading.repository import Repository
from marketpulse.trading.types import AuditEventType
from marketpulse.scheduler.paper_trading_tick import paper_trading_tick_job
assert hasattr(Repository, 'count_price_unavailable_attempts')
assert AuditEventType.PRICE_UNAVAILABLE == 'PRICE_UNAVAILABLE'
print('Baseline imports OK')
"
```
Expected: `Baseline imports OK`.

No commit on T0 — preflight only.

---

### Task T1: Settings flag + notifier factory wrapper

**Locks-Referenced:** 6g-L13 (observability → alerts dependency direction), 6g-L15 (independent enable flag).

**Files:**
- Modify: `marketpulse/config.py`
- Modify: `marketpulse/alerts/notifier.py`
- Create: `tests/unit/test_settings_paper_notifications.py`
- Create: `tests/unit/test_notifier_factory.py`

- [ ] **Step 1: Write failing settings test**

Create `tests/unit/test_settings_paper_notifications.py`:

```python
# Layer: pure
"""6g-T1: MP_PAPER_NOTIFICATIONS_ENABLED settings flag (lock 6g-L15)."""

from __future__ import annotations

import os
from unittest.mock import patch


def _fresh_settings():
    """Build a fresh Settings instance without the cached get_settings()."""
    from marketpulse.config import Settings
    return Settings()


def test_paper_notifications_enabled_defaults_to_true(monkeypatch):
    """Lock 6g-L15: default true so a fresh deployment immediately
    notifies. Operator must opt OUT via env, not opt IN."""
    monkeypatch.delenv("MP_PAPER_NOTIFICATIONS_ENABLED", raising=False)
    # Required env vars for Settings construction
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    s = _fresh_settings()
    assert s.paper_notifications_enabled is True


def test_paper_notifications_enabled_reads_env_false(monkeypatch):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "false")
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    s = _fresh_settings()
    assert s.paper_notifications_enabled is False


def test_paper_notifications_enabled_reads_env_true(monkeypatch):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    s = _fresh_settings()
    assert s.paper_notifications_enabled is True


def test_paper_notifications_independent_of_recap_enabled(monkeypatch):
    """Lock 6g-L15: flag is INDEPENDENT of NOTIFIER_RECAP_ENABLED.
    Phase 2 recap and 6g paper trading are independent tracks."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "false")
    monkeypatch.setenv("NOTIFIER_RECAP_ENABLED", "true")
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    s = _fresh_settings()
    assert s.paper_notifications_enabled is False
    assert s.notifier_recap_enabled is True
```

- [ ] **Step 2: Write failing notifier factory test**

Create `tests/unit/test_notifier_factory.py`:

```python
# Layer: pure
"""6g-T1: get_notifier_from_settings wrapper (lock 6g-L13)."""

from __future__ import annotations


def _settings(monkeypatch, **overrides):
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    for k, v in overrides.items():
        monkeypatch.setenv(k, v)
    from marketpulse.config import Settings
    return Settings()


def test_get_notifier_from_settings_returns_noop_when_kind_none(monkeypatch):
    from marketpulse.alerts.notifier import (
        NoopNotifier, get_notifier_from_settings,
    )
    s = _settings(monkeypatch, NOTIFIER_KIND="none")
    n = get_notifier_from_settings(s)
    assert isinstance(n, NoopNotifier)


def test_get_notifier_from_settings_returns_bark(monkeypatch):
    from marketpulse.alerts.notifier import (
        BarkNotifier, get_notifier_from_settings,
    )
    s = _settings(
        monkeypatch,
        NOTIFIER_KIND="bark",
        NOTIFIER_BARK_URL="https://api.day.app/devicekey",
    )
    n = get_notifier_from_settings(s)
    assert isinstance(n, BarkNotifier)


def test_get_notifier_from_settings_is_thin_wrapper_around_build_notifier(monkeypatch):
    """Lock 6g-L13: get_notifier_from_settings exists so callers can use
    the 6g-documented name; behaviour must equal build_notifier(settings)."""
    from marketpulse.alerts.notifier import (
        build_notifier, get_notifier_from_settings,
    )
    s = _settings(monkeypatch, NOTIFIER_KIND="none")
    a = get_notifier_from_settings(s)
    b = build_notifier(s)
    assert type(a) is type(b)
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/unit/test_settings_paper_notifications.py tests/unit/test_notifier_factory.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'paper_notifications_enabled'` and `ImportError: cannot import name 'get_notifier_from_settings'`.

- [ ] **Step 4: Add settings field**

Edit `marketpulse/config.py`. After the `paper_kill_switch` line (currently at the end of `Settings`), append:

```python
    # Phase 6g: master enable for paper-trading post-tick notifications.
    # Independent of NOTIFIER_RECAP_ENABLED (Phase 2). Lock 6g-L15.
    paper_notifications_enabled: bool = Field(
        True, alias="MP_PAPER_NOTIFICATIONS_ENABLED",
    )
```

The line goes between `paper_kill_switch` and the module-level `@lru_cache` block.

- [ ] **Step 5: Add factory wrapper**

Edit `marketpulse/alerts/notifier.py`. After the `build_notifier(settings: Settings) -> Notifier:` function (end of file), append:

```python


def get_notifier_from_settings(settings: Settings) -> Notifier:
    """Phase 6g (lock 6g-L13): thin wrapper around build_notifier so 6g
    code reads as `get_notifier_from_settings(settings)` per the spec's
    boundary-doc naming. observability/ → alerts/ dependency direction.

    Behaviour is exactly `build_notifier(settings)`; the indirection exists
    to make the 6g call sites self-documenting and to provide a single
    place to evolve paper-trading-specific defaults if they ever diverge
    from the recap-side defaults (none today)."""
    return build_notifier(settings)
```

- [ ] **Step 6: Run to verify tests pass**

Run: `uv run pytest tests/unit/test_settings_paper_notifications.py tests/unit/test_notifier_factory.py -v`
Expected: 7 PASS.

- [ ] **Step 7: Smoke ruff**

Run: `uv run ruff check marketpulse/config.py marketpulse/alerts/notifier.py tests/unit/test_settings_paper_notifications.py tests/unit/test_notifier_factory.py`
Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add marketpulse/config.py marketpulse/alerts/notifier.py tests/unit/test_settings_paper_notifications.py tests/unit/test_notifier_factory.py
git commit -m "$(cat <<'EOF'
feat(phase-6g-T1): MP_PAPER_NOTIFICATIONS_ENABLED + factory wrapper

Adds the 6g master enable flag (lock 6g-L15) — defaults to True so a
fresh deployment immediately notifies; operator opts out via env.
Independent of NOTIFIER_RECAP_ENABLED.

Adds get_notifier_from_settings(settings) — a thin re-export of
build_notifier so observability/ code reads with the 6g-documented
naming (lock 6g-L13 boundary doc).

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T2: Repository read-only helpers (3 new methods)

**Locks-Referenced:** 6g-L5 (kill-switch skipped dedup), 6g-L17 (batch recovery helper), 6g-L20 (latest tick completed timestamp).

**Files:**
- Modify: `marketpulse/trading/repository.py`
- Create: `tests/trading/test_repository_observability_helpers.py`

All 3 helpers are read-only `select()` — must pass the architecture guard at `tests/architecture/test_repository_boundary.py`.

**Production-write boundary (lock iii reminder):** the T2 *tests* directly INSERT `PaperAuditEvent` rows via `session.add(...)` to seed query fixtures. This is **test-only**. Production code MUST go through `Repository.write_audit_event` for ALL audit writes — the engine and risk-gate paths already do this, and 6g introduces no new audit write sites. If a future change starts writing audit rows from outside the repository wrapper, the lock-iii boundary test should catch it.

- [ ] **Step 1: Write failing helper tests**

Create `tests/trading/test_repository_observability_helpers.py`:

```python
# Layer: stateful
"""6g-T2: Repository observability helpers (locks 6g-L5, 6g-L17, 6g-L20)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base
    eng = create_engine(f"sqlite:///{tmp_path / 'obs.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _audit(session, *, event_type, timestamp, context=None, strategy=None,
           order_id=None, reason=""):
    """Direct INSERT — these tests verify Repository READ helpers; the
    boundary lock (iii) covers WRITE sites elsewhere. PaperAuditEvent
    has no foreign-key constraints on order_id."""
    from marketpulse.db.models import PaperAuditEvent
    row = PaperAuditEvent(
        timestamp=timestamp, event_type=event_type, order_id=order_id,
        strategy=strategy, reason=reason, context=context or {},
    )
    session.add(row)
    session.flush()
    return row


# === positions_with_prior_price_unavailable (lock 6g-L17) ===

def test_positions_with_prior_pu_empty_position_ids_returns_empty(session):
    from marketpulse.trading.repository import Repository
    repo = Repository(session=session)
    out = repo.positions_with_prior_price_unavailable(
        position_ids=[], before=datetime(2026, 5, 22, tzinfo=UTC),
    )
    assert out == set()


def test_positions_with_prior_pu_matches_by_context_position_id(session):
    """Lock 6g-L17: batch helper. position_id 1 has prior PU; 2 has none."""
    from marketpulse.trading.repository import Repository
    t = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    _audit(session, event_type="PRICE_UNAVAILABLE",
           timestamp=t - timedelta(days=2),
           context={"position_id": 1, "attempt_count": 1})
    repo = Repository(session=session)
    out = repo.positions_with_prior_price_unavailable(
        position_ids=[1, 2], before=t,
    )
    assert out == {1}


def test_positions_with_prior_pu_excludes_concurrent_timestamps(session):
    """Lock 6g-L4b: "prior in history" means strictly before, not equal."""
    from marketpulse.trading.repository import Repository
    t = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    _audit(session, event_type="PRICE_UNAVAILABLE", timestamp=t,
           context={"position_id": 1, "attempt_count": 1})
    repo = Repository(session=session)
    out = repo.positions_with_prior_price_unavailable(
        position_ids=[1], before=t,
    )
    assert out == set()


def test_positions_with_prior_pu_multi_position(session):
    from marketpulse.trading.repository import Repository
    t = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    earlier = t - timedelta(days=3)
    _audit(session, event_type="PRICE_UNAVAILABLE", timestamp=earlier,
           context={"position_id": 1, "attempt_count": 1})
    _audit(session, event_type="PRICE_UNAVAILABLE", timestamp=earlier,
           context={"position_id": 3, "attempt_count": 2})
    repo = Repository(session=session)
    out = repo.positions_with_prior_price_unavailable(
        position_ids=[1, 2, 3, 4], before=t,
    )
    assert out == {1, 3}


# === kill_switch_cycle_skipped_in_active_period (lock 6g-L5) ===

def test_kscs_in_period_returns_false_when_no_flipped(session):
    """Lock 6g-L5 boundary: orphan SKIPPED (no FLIPPED in history) → False."""
    from marketpulse.trading.repository import Repository
    t = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    _audit(session, event_type="KILL_SWITCH_CYCLE_SKIPPED",
           timestamp=t - timedelta(hours=1),
           context={"tick_date": "2026-05-22"})
    repo = Repository(session=session)
    assert repo.kill_switch_cycle_skipped_in_active_period(before=t) is False


def test_kscs_in_period_returns_false_when_no_skip_since_flip(session):
    from marketpulse.trading.repository import Repository
    t = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    _audit(session, event_type="KILL_SWITCH_FLIPPED",
           timestamp=t - timedelta(hours=2),
           context={"to_state": True, "reason": "drawdown"})
    repo = Repository(session=session)
    assert repo.kill_switch_cycle_skipped_in_active_period(before=t) is False


def test_kscs_in_period_returns_true_when_skip_exists_since_flip(session):
    from marketpulse.trading.repository import Repository
    t = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    _audit(session, event_type="KILL_SWITCH_FLIPPED",
           timestamp=t - timedelta(hours=3),
           context={"to_state": True})
    _audit(session, event_type="KILL_SWITCH_CYCLE_SKIPPED",
           timestamp=t - timedelta(hours=1),
           context={"tick_date": "2026-05-22"})
    repo = Repository(session=session)
    assert repo.kill_switch_cycle_skipped_in_active_period(before=t) is True


def test_kscs_in_period_resets_after_clear_and_re_flip(session):
    """A flip → clear → flip(true) cycle: skip-in-old-period should not
    appear in the new period's count."""
    from marketpulse.trading.repository import Repository
    t = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    _audit(session, event_type="KILL_SWITCH_FLIPPED",
           timestamp=t - timedelta(days=5),
           context={"to_state": True})
    _audit(session, event_type="KILL_SWITCH_CYCLE_SKIPPED",
           timestamp=t - timedelta(days=4),
           context={"tick_date": "2026-05-17"})
    _audit(session, event_type="KILL_SWITCH_FLIPPED",
           timestamp=t - timedelta(days=3),
           context={"to_state": False})
    _audit(session, event_type="KILL_SWITCH_FLIPPED",
           timestamp=t - timedelta(hours=2),
           context={"to_state": True})
    # No skip yet in the NEW active period → False
    repo = Repository(session=session)
    assert repo.kill_switch_cycle_skipped_in_active_period(before=t) is False


def test_kscs_in_period_respects_before_cutoff(session):
    from marketpulse.trading.repository import Repository
    t = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    _audit(session, event_type="KILL_SWITCH_FLIPPED",
           timestamp=t - timedelta(hours=3),
           context={"to_state": True})
    # SKIP is AT `before` — must be excluded (strictly before)
    _audit(session, event_type="KILL_SWITCH_CYCLE_SKIPPED",
           timestamp=t, context={"tick_date": "2026-05-22"})
    repo = Repository(session=session)
    assert repo.kill_switch_cycle_skipped_in_active_period(before=t) is False


# === latest_tick_completed_timestamp (lock 6g-L20) ===

def test_latest_tick_completed_returns_none_when_empty(session):
    from marketpulse.trading.repository import Repository
    repo = Repository(session=session)
    out = repo.latest_tick_completed_timestamp(
        before=datetime(2026, 5, 22, tzinfo=UTC),
    )
    assert out is None


def test_latest_tick_completed_returns_most_recent(session):
    from marketpulse.trading.repository import Repository
    t = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    older = t - timedelta(days=2)
    newer = t - timedelta(days=1)
    _audit(session, event_type="TICK_COMPLETED", timestamp=older,
           context={"tick_date": "2026-05-20"})
    _audit(session, event_type="TICK_COMPLETED", timestamp=newer,
           context={"tick_date": "2026-05-21"})
    repo = Repository(session=session)
    out = repo.latest_tick_completed_timestamp(before=t)
    assert out == newer


def test_latest_tick_completed_respects_before_cutoff(session):
    from marketpulse.trading.repository import Repository
    t = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    _audit(session, event_type="TICK_COMPLETED", timestamp=t,
           context={"tick_date": "2026-05-22"})
    older = t - timedelta(days=1)
    _audit(session, event_type="TICK_COMPLETED", timestamp=older,
           context={"tick_date": "2026-05-21"})
    repo = Repository(session=session)
    # before=t — the row AT t is excluded (strictly before)
    out = repo.latest_tick_completed_timestamp(before=t)
    assert out == older
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/test_repository_observability_helpers.py -v`
Expected: All FAIL with `AttributeError: 'Repository' object has no attribute 'positions_with_prior_price_unavailable'` and friends.

- [ ] **Step 3: Implement the 3 helpers**

In `marketpulse/trading/repository.py`, after the existing `count_price_unavailable_attempts` method (currently the last method in the file, around line 576-593), append:

```python

    # === Phase 6g observability helpers (read-only) ===

    def positions_with_prior_price_unavailable(
        self, *, position_ids: list[int], before: datetime,
    ) -> set[int]:
        """Lock 6g-L17: batch helper. Returns subset of `position_ids` that
        have ≥ 1 PRICE_UNAVAILABLE audit row with timestamp strictly
        before `before` (lock 6g-L4b "prior in history, not concurrent").

        Empty-history contract: empty `position_ids` returns `set()` without
        querying. No matching rows returns `set()`. Uses
        json_extract(context, '$.position_id') for parity with
        count_price_unavailable_attempts (6b+T6 / lock 6b+L9 wrapper-only)."""
        from marketpulse.db.models import PaperAuditEvent

        if not position_ids:
            return set()
        rows = self._session.execute(
            select(
                func.json_extract(PaperAuditEvent.context, "$.position_id"),
            )
            .where(PaperAuditEvent.event_type == "PRICE_UNAVAILABLE")
            .where(PaperAuditEvent.timestamp < before)
            .where(
                func.json_extract(PaperAuditEvent.context, "$.position_id")
                .in_(position_ids)
            )
            .distinct()
        ).all()
        return {int(r[0]) for r in rows if r[0] is not None}

    def kill_switch_cycle_skipped_in_active_period(
        self, *, before: datetime,
    ) -> bool:
        """Lock 6g-L5: True iff (a) the most-recent KILL_SWITCH_FLIPPED
        row before `before` has `to_state=true` (i.e. we ARE currently
        in an active kill-switch period) AND (b) there's at least one
        KILL_SWITCH_CYCLE_SKIPPED row strictly between that flip's
        timestamp and `before`.

        Edge case the round-3 reviewer caught: looking only at the
        latest *active=true* flip is unsafe. Sequence
            flip(true) → skip → flip(false) → skip(orphan bug)
        has a latest flip(active=true) before the orphan skip — old
        impl would incorrectly suppress the orphan because a prior
        skip exists in that window. Fix: inspect the latest flip
        REGARDLESS of state; if it cleared (to_state=false), we are
        NOT in an active period, so don't dedup — emit the push.

        Boundary: if no KILL_SWITCH_FLIPPED row exists at all (orphan
        skip without any prior flip), returns False — let the
        entrypoint emit the push because the skip itself is anomalous
        and operator-worthy. Pure audit projection; no extra state."""
        from marketpulse.db.models import PaperAuditEvent

        latest_flip = self._session.execute(
            select(
                PaperAuditEvent.timestamp,
                func.json_extract(PaperAuditEvent.context, "$.to_state"),
            )
            .where(PaperAuditEvent.event_type == "KILL_SWITCH_FLIPPED")
            .where(PaperAuditEvent.timestamp < before)
            .order_by(desc(PaperAuditEvent.timestamp))
            .limit(1)
        ).first()
        if latest_flip is None:
            # No flip in history at all → orphan skip → emit (don't dedup).
            return False
        flip_ts, flip_to_state = latest_flip
        if flip_to_state != 1:
            # Latest flip cleared the switch → not in an active period.
            # Any skip after that is itself anomalous → emit (don't dedup).
            return False
        # We're inside an active period started at flip_ts. Dedup iff a
        # prior KILL_SWITCH_CYCLE_SKIPPED already exists in this period.
        skip_exists = self._session.execute(
            select(func.count(PaperAuditEvent.id))
            .where(PaperAuditEvent.event_type == "KILL_SWITCH_CYCLE_SKIPPED")
            .where(PaperAuditEvent.timestamp > flip_ts)
            .where(PaperAuditEvent.timestamp < before)
        ).scalar() or 0
        return skip_exists > 0

    def latest_price_unavailable_attempt_counts(
        self, *, position_ids: list[int], before: datetime,
    ) -> dict[int, int]:
        """Lock 6g-L4c **global** monotonic invariant support: returns
        a mapping `{position_id: max(attempt_count)}` over PRICE_UNAVAILABLE
        audit rows with `timestamp < before` for the given position_ids.

        The 6g translator (T6 entrypoint) uses this to seed the L4c
        check WITHOUT the "same-batch local ordering" limitation —
        comparing the new tick's attempt_count values against
        per-position max from ALL prior history makes a regression
        like `tick1: attempt=5 → tick2: attempt=2` actually detectable.

        Missing position_ids in the result (no prior PRICE_UNAVAILABLE
        rows for them) are simply absent from the dict; the caller
        treats absence as `prior_max = 0`.

        Read-only `select()` — passes the architecture boundary guard."""
        from marketpulse.db.models import PaperAuditEvent

        if not position_ids:
            return {}
        rows = self._session.execute(
            select(
                func.json_extract(
                    PaperAuditEvent.context, "$.position_id",
                ).label("pid"),
                func.max(
                    func.json_extract(
                        PaperAuditEvent.context, "$.attempt_count",
                    ),
                ).label("max_attempt"),
            )
            .where(PaperAuditEvent.event_type == "PRICE_UNAVAILABLE")
            .where(PaperAuditEvent.timestamp < before)
            .where(
                func.json_extract(
                    PaperAuditEvent.context, "$.position_id",
                ).in_(position_ids)
            )
            .group_by("pid")
        ).all()
        # Filter Nones (defensive: rows without position_id in context)
        # and coerce to int (SQLite returns json_extract as varying types).
        return {
            int(r.pid): int(r.max_attempt)
            for r in rows
            if r.pid is not None and r.max_attempt is not None
        }

    def latest_tick_completed_timestamp(
        self, *, before: datetime,
    ) -> datetime | None:
        """Lock 6g-L20: most recent TICK_COMPLETED audit timestamp strictly
        before `before`, or None if no such row exists (first-ever tick).

        Used to construct the between-tick window for KILL_SWITCH_FLIPPED
        rows that may have been written externally (manual CLI, etc.)."""
        from marketpulse.db.models import PaperAuditEvent

        return self._session.execute(
            select(PaperAuditEvent.timestamp)
            .where(PaperAuditEvent.event_type == "TICK_COMPLETED")
            .where(PaperAuditEvent.timestamp < before)
            .order_by(desc(PaperAuditEvent.timestamp))
            .limit(1)
        ).scalar()
```

Note: `desc` and `func` are already imported at the top of `repository.py` (line 18: `from sqlalchemy import desc, func, select`). No new imports needed.

The SQLite JSON `active=true` check uses `json_extract(...) == 1` because SQLite stores JSON booleans as `1`/`0` integers when extracted. If the audit writer stores the literal `True` (Python bool) into JSON, SQLAlchemy serializes that to `true` in the JSON column; SQLite's `json_extract` returns `1` for `true` and `0` for `false`. Verify against the actual KILL_SWITCH_FLIPPED writer (look for `kill_switch.py` if uncertain) — if it stores e.g. a string `"true"`, change the comparison accordingly. As of 6a/6b the writer uses Python `True`.

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/trading/test_repository_observability_helpers.py -v`
Expected: 11 PASS.

- [ ] **Step 5: Architecture guard still passes**

Run: `uv run pytest tests/architecture/test_repository_boundary.py -v`
Expected: PASS (the new helpers use only `select()` — no `add`/`commit`/`merge`/`delete`/`execute(insert/update)`).

- [ ] **Step 6: Ruff**

Run: `uv run ruff check marketpulse/trading/repository.py tests/trading/test_repository_observability_helpers.py`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add marketpulse/trading/repository.py tests/trading/test_repository_observability_helpers.py
git commit -m "$(cat <<'EOF'
feat(phase-6g-T2): Repository observability helpers (3 read-only)

Adds 3 read-only audit-projection helpers used by 6g notify dispatcher:
- positions_with_prior_price_unavailable (lock 6g-L17 batch recovery)
- kill_switch_cycle_skipped_in_active_period (lock 6g-L5 dedup)
- latest_tick_completed_timestamp (lock 6g-L20 between-tick window)

All three use select() only — single-writer architecture guard (lock iii)
still passes.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T3: Pure projection dataclasses + `select_critical_events`

**Locks-Referenced:** 6g-L3 (daily_loss filter), 6g-L4a (PU attempt==3), 6g-L4b (recovery prior-history), 6g-L4c (monotonic invariant), 6g-L5 (KSCS dedup decision), 6g-L9 (TICK_REPROCESSED_COMPLETED critical).

**Files:**
- Create: `marketpulse/observability/__init__.py`
- Create: `marketpulse/observability/audit_projection.py` (dataclasses + select_critical_events; summarize_tick added in T4)
- Create: `tests/observability/__init__.py`
- Create: `tests/observability/test_audit_projection_critical.py`

- [ ] **Step 1: Write failing tests**

Create `tests/observability/__init__.py` as an empty file.

Create `tests/observability/test_audit_projection_critical.py`:

```python
# Layer: pure
"""6g-T3: select_critical_events — pure projection tests.

Covers locks 6g-L3 (daily_loss), 6g-L4a (PU attempt==3 only),
6g-L4b (recovery prior history), 6g-L5 (KSCS dedup decision),
6g-L9 (TICK_REPROCESSED critical)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime


@dataclass
class _Row:
    """Minimal stand-in for PaperAuditEvent — projection is pure and only
    reads the documented attributes. Plain dataclass; equality not needed."""
    id: int
    timestamp: datetime
    event_type: str
    order_id: int | None = None
    strategy: str | None = None
    reason: str = ""
    context: dict | None = None

    def __post_init__(self):
        if self.context is None:
            self.context = {}


def _ts(hour: int = 18) -> datetime:
    return datetime(2026, 5, 22, hour, 0, tzinfo=UTC)


# === Daily loss (lock 6g-L3) ===

def test_order_rejected_daily_loss_is_critical():
    from marketpulse.observability.audit_projection import select_critical_events
    row = _Row(
        id=1, timestamp=_ts(), event_type="ORDER_REJECTED",
        order_id=10, strategy="momentum", reason="rejected",
        context={"ticker": "AAPL", "quantity": 10,
                 "failed_gates": ["daily_loss"], "loss_today": "-150.00"},
    )
    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
        threshold=3,
    )
    assert len(out) == 1
    assert out[0].event_type == "ORDER_REJECTED"
    assert out[0].audit_id == 1
    assert out[0].context["failed_gates"] == ["daily_loss"]


def test_order_rejected_other_gate_is_not_critical():
    from marketpulse.observability.audit_projection import select_critical_events
    row = _Row(
        id=2, timestamp=_ts(), event_type="ORDER_REJECTED",
        order_id=11, strategy="momentum", reason="rejected",
        context={"ticker": "GOOG", "failed_gates": ["sector_exposure"]},
    )
    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )
    assert out == []


def test_order_rejected_daily_loss_among_multiple_gates_is_critical():
    """Lock 6g-L3: ANY failed_gate containing 'daily_loss' → critical."""
    from marketpulse.observability.audit_projection import select_critical_events
    row = _Row(
        id=3, timestamp=_ts(), event_type="ORDER_REJECTED",
        order_id=12, strategy="defensive",
        context={"failed_gates": ["sector_exposure", "daily_loss"]},
    )
    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )
    assert len(out) == 1


def test_order_rejected_missing_failed_gates_is_not_critical():
    """Defensive: malformed audit (no failed_gates key) → summary only."""
    from marketpulse.observability.audit_projection import select_critical_events
    row = _Row(
        id=4, timestamp=_ts(), event_type="ORDER_REJECTED",
        context={"ticker": "TSLA"},
    )
    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )
    assert out == []


# === PRICE_UNAVAILABLE (lock 6g-L4a) ===

def test_price_unavailable_attempt_3_is_critical():
    from marketpulse.observability.audit_projection import select_critical_events
    row = _Row(
        id=5, timestamp=_ts(), event_type="PRICE_UNAVAILABLE",
        strategy="momentum",
        context={"ticker": "AAPL", "position_id": 42,
                 "attempt_count": 3, "horizon_date": "2026-05-22",
                 "source": "yfinance"},
    )
    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
        threshold=3,
    )
    assert len(out) == 1
    assert out[0].event_type == "PRICE_UNAVAILABLE"


def test_price_unavailable_attempt_1_or_2_is_not_critical():
    from marketpulse.observability.audit_projection import select_critical_events
    for attempt in (1, 2):
        row = _Row(
            id=10 + attempt, timestamp=_ts(), event_type="PRICE_UNAVAILABLE",
            context={"ticker": "AAPL", "position_id": 42,
                     "attempt_count": attempt},
        )
        out = select_critical_events(
            new_audit_rows=[row],
            kill_switch_cycle_skipped_in_period=False,
            positions_with_prior_pu=set(),
            threshold=3,
        )
        assert out == [], f"attempt {attempt} unexpectedly critical"


def test_price_unavailable_attempt_4_plus_is_suppressed():
    """Lock 6g-L4a: ≥ 4 suppressed — no repeated pushes for the same
    stuck position."""
    from marketpulse.observability.audit_projection import select_critical_events
    for attempt in (4, 5, 10):
        row = _Row(
            id=100 + attempt, timestamp=_ts(),
            event_type="PRICE_UNAVAILABLE",
            context={"ticker": "AAPL", "position_id": 42,
                     "attempt_count": attempt},
        )
        out = select_critical_events(
            new_audit_rows=[row],
            kill_switch_cycle_skipped_in_period=False,
            positions_with_prior_pu=set(),
            threshold=3,
        )
        assert out == [], f"attempt {attempt} should be suppressed"


# === Recovery (lock 6g-L4b) ===

def test_position_closed_with_prior_pu_is_recovery_critical():
    from marketpulse.observability.audit_projection import select_critical_events
    row = _Row(
        id=20, timestamp=_ts(), event_type="POSITION_CLOSED",
        strategy="momentum",
        context={"ticker": "AAPL", "position_id": 42,
                 "exit_price": "152.10", "realized_pnl": "21.00",
                 "retry_count": 5},
    )
    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu={42},
    )
    assert len(out) == 1
    assert out[0].event_type == "POSITION_CLOSED"


def test_position_closed_without_prior_pu_is_summary_only():
    from marketpulse.observability.audit_projection import select_critical_events
    row = _Row(
        id=21, timestamp=_ts(), event_type="POSITION_CLOSED",
        context={"ticker": "AAPL", "position_id": 99,
                 "exit_price": "155.00", "realized_pnl": "50.00"},
    )
    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )
    assert out == []


# === Kill switch (lock 6g-L5) ===

def test_kill_switch_flipped_active_true_is_critical():
    from marketpulse.observability.audit_projection import select_critical_events
    row = _Row(
        id=30, timestamp=_ts(), event_type="KILL_SWITCH_FLIPPED",
        reason="max_drawdown_exceeded",
        context={"to_state": True, "reason": "max_drawdown_exceeded"},
    )
    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )
    assert len(out) == 1


def test_kill_switch_flipped_active_false_is_critical():
    """Cleared also fires (different title rendered later)."""
    from marketpulse.observability.audit_projection import select_critical_events
    row = _Row(
        id=31, timestamp=_ts(), event_type="KILL_SWITCH_FLIPPED",
        context={"to_state": False, "reason": "manual_reset"},
    )
    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )
    assert len(out) == 1


def test_kill_switch_cycle_skipped_first_is_critical():
    from marketpulse.observability.audit_projection import select_critical_events
    row = _Row(
        id=32, timestamp=_ts(),
        event_type="KILL_SWITCH_CYCLE_SKIPPED",
        context={"tick_date": "2026-05-23", "reason": "kill_switch_active"},
    )
    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )
    assert len(out) == 1


def test_kill_switch_cycle_skipped_dedups_when_prior_exists():
    """Lock 6g-L5: subsequent skip in same active period is suppressed."""
    from marketpulse.observability.audit_projection import select_critical_events
    row = _Row(
        id=33, timestamp=_ts(),
        event_type="KILL_SWITCH_CYCLE_SKIPPED",
        context={"tick_date": "2026-05-24"},
    )
    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=True,
        positions_with_prior_pu=set(),
    )
    assert out == []


# === Engine invariant / scheduler gap / reprocessed (lock 6g-L9) ===

def test_engine_invariant_error_always_critical():
    from marketpulse.observability.audit_projection import select_critical_events
    row = _Row(
        id=40, timestamp=_ts(), event_type="ENGINE_INVARIANT_ERROR",
        context={"phase": "exit_materialization", "error": "decimal-mismatch"},
    )
    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )
    assert len(out) == 1


def test_scheduler_gap_always_critical():
    from marketpulse.observability.audit_projection import select_critical_events
    row = _Row(
        id=41, timestamp=_ts(), event_type="SCHEDULER_GAP_DETECTED",
        context={"last_tick_date": "2026-05-15", "gap_days": 4},
    )
    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )
    assert len(out) == 1


def test_tick_reprocessed_completed_always_critical():
    """Lock 6g-L9."""
    from marketpulse.observability.audit_projection import select_critical_events
    row = _Row(
        id=42, timestamp=_ts(), event_type="TICK_REPROCESSED_COMPLETED",
        context={"tick_date": "2026-05-22", "status": "completed"},
    )
    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )
    assert len(out) == 1


# === Routine events do NOT emit critical ===

def test_routine_events_never_critical():
    from marketpulse.observability.audit_projection import select_critical_events
    for et in (
        "ORDER_PLACED", "ORDER_ENTRY_FILLED", "ORDER_PLACED_DUPLICATE",
        "ORDER_CANCELLED", "TICK_COMPLETED",
    ):
        row = _Row(id=50, timestamp=_ts(), event_type=et, context={})
        out = select_critical_events(
            new_audit_rows=[row],
            kill_switch_cycle_skipped_in_period=False,
            positions_with_prior_pu=set(),
        )
        assert out == [], f"{et} should not be critical"


def test_critical_event_carries_canonical_fields():
    """CriticalEvent flatten: timestamp / strategy / reason / audit_id /
    context (raw context preserved for templates)."""
    from marketpulse.observability.audit_projection import (
        CriticalEvent, select_critical_events,
    )
    row = _Row(
        id=60, timestamp=_ts(20), event_type="PRICE_UNAVAILABLE",
        strategy="momentum", reason="no_close",
        context={"ticker": "AAPL", "position_id": 42, "attempt_count": 3,
                 "horizon_date": "2026-05-22", "source": "yfinance"},
    )
    out = select_critical_events(
        new_audit_rows=[row],
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )
    assert len(out) == 1
    ev = out[0]
    assert isinstance(ev, CriticalEvent)
    assert ev.audit_id == 60
    assert ev.timestamp == _ts(20)
    assert ev.strategy == "momentum"
    assert ev.reason == "no_close"
    assert ev.context["ticker"] == "AAPL"


def test_select_critical_events_preserves_audit_order():
    """Multiple critical rows in input → output in same id order
    (deterministic for republish CLI output)."""
    from marketpulse.observability.audit_projection import select_critical_events
    rows = [
        _Row(id=1, timestamp=_ts(), event_type="KILL_SWITCH_FLIPPED",
             context={"to_state": True}),
        _Row(id=2, timestamp=_ts(), event_type="PRICE_UNAVAILABLE",
             context={"ticker": "AAPL", "position_id": 1,
                      "attempt_count": 3}),
        _Row(id=3, timestamp=_ts(), event_type="ENGINE_INVARIANT_ERROR",
             context={"phase": "entry", "error": "bad"}),
    ]
    out = select_critical_events(
        new_audit_rows=rows,
        kill_switch_cycle_skipped_in_period=False,
        positions_with_prior_pu=set(),
    )
    assert [e.audit_id for e in out] == [1, 2, 3]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/observability/test_audit_projection_critical.py -v`
Expected: ModuleNotFoundError on `marketpulse.observability.audit_projection`.

- [ ] **Step 3: Create observability package + audit_projection.py**

Create `marketpulse/observability/__init__.py` as an empty file with the package docstring:

```python
"""Phase 6g — Observability + Alerting (paper-trading operator pushes).

Pure consumer layer of marketpulse.trading.repository.paper_audit_event.
This package writes nothing; it reads audit rows and dispatches push
notifications via marketpulse.alerts.notifier (lock 6g-L13)."""
```

Create `marketpulse/observability/audit_projection.py`:

```python
"""Phase 6g pure projection layer.

Translates lists of PaperAuditEvent rows into:
- CriticalEvent[] (one per standalone push, per § 3.1 of the spec)
- TickSummary (the routine summary; built in T4 — summarize_tick)

No DB, no notifier, no clock. Caller supplies dedup facts as primitives
(kill_switch_cycle_skipped_in_period: bool, positions_with_prior_pu:
set[int]) so this module stays unit-testable with synthetic row lists."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class NotificationFailure:
    """Structured failure record so republish CLI / tests can diagnose
    which event failed without grepping log lines (spec § 6.2)."""
    event_type: str        # e.g. "ORDER_REJECTED", "tick_summary", "config"
    title: str             # the rendered title that was about to be sent
    error: str             # short error category:
                           #   "send_returned_false",
                           #   "send_raised:<ExceptionClass>",
                           #   "template_error:<...>",
                           #   "disabled_by_config",
                           #   "missing_tick_completed_row"


@dataclass(frozen=True)
class CriticalEvent:
    """One critical audit row scheduled for standalone push.

    Carries the projection's view of the audit row — templates.py is
    pure rendering and should NOT need to reach back into raw context
    keys for canonical fields (timestamp / strategy / reason).

    Field-level contracts:
      reason: `None` when the audit row's `reason` column was unset.
        Distinct from `""` (intentionally blank). Renderers branching
        on truthiness see both as "no reason", but downstream telemetry
        / CLI can match `is None` to flag "missing reason on an event
        type that should have one".
      context: a read-only `Mapping[str, object]` view (built via
        `MappingProxyType` in `_freeze_context`). Without the proxy,
        `@dataclass(frozen=True)` only freezes the slot bindings — a
        renderer doing `ctx["x"] = ...` would silently corrupt the
        projection between renders.
    """
    event_type: str             # AuditEventType value (str)
    audit_id: int               # for logging / republish CLI output
    timestamp: datetime         # row's timestamp (UTC)
    strategy: str | None        # PaperAuditEvent.strategy column (may be None)
    reason: str | None          # None = unset; "" = explicit blank
    context: "Mapping[str, object]"   # immutable view — see _freeze_context


def _freeze_context(raw) -> "Mapping[str, object]":
    """Helper: convert raw audit row context dict (or None) → immutable
    Mapping for CriticalEvent. `None` becomes an empty proxy. Callers
    in `select_critical_events` and projection tests use this so that
    renderer-side mutations are impossible (item 2 from round 4
    review)."""
    from types import MappingProxyType
    return MappingProxyType(dict(raw or {}))


@dataclass(frozen=True)
class PlacedOrderDetail:
    """One row for the 'orders placed' section of the summary."""
    ticker: str
    strategy: str
    quantity: int


@dataclass(frozen=True)
class TickSummary:
    """Aggregate of routine activity for the 📊 summary push.

    Lists preserved (not just counts) so templates can render
    "AAPL × 10 (momentum)" detail lines without a second pass over rows.
    """
    tick_date: date
    cycle_status: str
    orders_placed: int
    orders_placed_detail: list[PlacedOrderDetail]
    orders_rejected: int
    orders_rejected_breakdown: list[tuple[str, str]]   # (ticker, gate_name)
    orders_cancelled: int
    duplicates_skipped: int
    entries_filled: list[tuple[str, Decimal]]          # (ticker, fill_price)
    positions_closed: list[tuple[str, Decimal, Decimal]]  # (ticker, exit_price, realized_pnl)
    total_realized_pnl: Decimal
    cash_balance_end: Decimal
    active_positions_count: int
    active_positions_with_pu: list[tuple[str, int]]    # (ticker, attempt_count_capped)


# === select_critical_events ===

# Critical-event decision rules (item 5 of round-4 review: move from
# scattered elif-chain to a small dispatch table for future
# extensibility — adding 6h / 6i / 7a event types only requires adding
# a row here).
#
# Each rule is a callable `(context: Mapping, dedup_facts) -> bool`.
# `dedup_facts` is a small bag passed by select_critical_events with
# the lock 6g-L4b / L5 dedup state. Rules that always fire ignore
# both args.
_ALWAYS = lambda ctx, facts: True  # noqa: E731 — short rule
_ALWAYS_CRITICAL = frozenset({
    "ENGINE_INVARIANT_ERROR",
    "SCHEDULER_GAP_DETECTED",
    "TICK_REPROCESSED_COMPLETED",
    "KILL_SWITCH_FLIPPED",
})


def _is_kscs_first_in_period(ctx, facts) -> bool:
    return not facts.kill_switch_cycle_skipped_in_period


def _is_position_recovered(ctx, facts) -> bool:
    pos_id = ctx.get("position_id")
    return pos_id is not None and pos_id in facts.positions_with_prior_pu


# Conditional-rule table. Iteration order is irrelevant (no event type
# can match two rules — guarded by the `et in _ALWAYS_CRITICAL` short
# circuit + mutually-exclusive event_type keys). Future phases that
# introduce new event types add one row here.
_CONDITIONAL_RULES: dict[str, "callable"] = {
    "KILL_SWITCH_CYCLE_SKIPPED": _is_kscs_first_in_period,
    "ORDER_REJECTED": lambda ctx, facts: _is_daily_loss_reject(ctx),
    "PRICE_UNAVAILABLE": lambda ctx, facts: _is_pu_third_attempt(
        ctx, facts.threshold,
    ),
    "POSITION_CLOSED": _is_position_recovered,
}


@dataclass(frozen=True)
class _DedupFacts:
    """Bag-of-facts threaded into _CONDITIONAL_RULES lambdas. Kept as a
    tiny internal record so we don't pass 5+ positional args around."""
    kill_switch_cycle_skipped_in_period: bool
    positions_with_prior_pu: set[int]
    threshold: int


def _is_daily_loss_reject(context: dict) -> bool:
    """Lock 6g-L3: ORDER_REJECTED is critical iff failed_gates contains
    'daily_loss'. Defensive against malformed context."""
    gates = context.get("failed_gates")
    if not isinstance(gates, (list, tuple)):
        return False
    return "daily_loss" in gates


def _is_pu_third_attempt(context: dict, threshold: int) -> bool:
    """Lock 6g-L4a: PRICE_UNAVAILABLE critical iff attempt_count ==
    threshold exactly. ≥ threshold+1 suppressed; < threshold suppressed."""
    return context.get("attempt_count") == threshold


def _check_pu_monotonic(
    new_audit_rows,
    failures: "list[NotificationFailure]",
    *,
    prior_attempts_by_position: dict[int, int],
) -> None:
    """Lock 6g-L4c **global** runtime check: PRICE_UNAVAILABLE.context[
    "attempt_count"] must be per-position monotonic non-decreasing
    across ALL audit history (enforced by 6b+T6/T7 `prior_attempts + 1`
    semantics + append-only audit). If a future audit-writer bug breaks
    the invariant (e.g., resets to 1 mid-stream, goes backwards within
    a tick, OR regresses ACROSS ticks like tick1=5 → tick2=2), we
    record a NotificationFailure so the operator sees the schema
    regression — but we DO NOT raise, because 6g is a strict consumer.

    `prior_attempts_by_position` carries the cross-tick history: it
    maps `{position_id → max(attempt_count) from audit rows BEFORE the
    current window}`. The caller (T6 entrypoint) supplies this via
    `repository.latest_price_unavailable_attempt_counts(...)`. Positions
    with no prior history are absent from the dict and treated as
    `prior_max = 0`. This closes the same-batch-only gap the round-4
    reviewer caught: tick1=5 → tick2=2 IS now detectable.

    Pure projection: walks new_audit_rows in source order, threads
    per-position max forward from the prior global state, appends a
    failure for any decrease. Per item 3 below, total failures
    appended by this check are capped at MAX_INVARIANT_FAILURES (10)
    so a wholesale audit-schema bug can't fan out into hundreds of
    NotificationFailures."""
    MAX_INVARIANT_FAILURES = 10
    appended = 0
    seen_max: dict[int, int] = dict(prior_attempts_by_position)
    for row in new_audit_rows:
        if row.event_type != "PRICE_UNAVAILABLE":
            continue
        ctx = row.context or {}
        pos_id = ctx.get("position_id")
        attempt = ctx.get("attempt_count")
        if not isinstance(pos_id, int) or not isinstance(attempt, int):
            continue
        prior = seen_max.get(pos_id, 0)
        if attempt < prior and appended < MAX_INVARIANT_FAILURES:
            failures.append(NotificationFailure(
                event_type="PRICE_UNAVAILABLE",
                title=f"position_id={pos_id}",
                error=(
                    f"monotonic_invariant_violation:attempt_count "
                    f"{prior}->{attempt} (lock 6g-L4c)"
                ),
            ))
            appended += 1
        seen_max[pos_id] = max(prior, attempt)
    if appended >= MAX_INVARIANT_FAILURES:
        failures.append(NotificationFailure(
            event_type="PRICE_UNAVAILABLE",
            title="invariant_failures_capped",
            error=(
                f"more than {MAX_INVARIANT_FAILURES} monotonic violations "
                f"in this tick — further entries suppressed (lock 6g-L4c)"
            ),
        ))


def select_critical_events(
    *,
    new_audit_rows,
    kill_switch_cycle_skipped_in_period: bool,
    positions_with_prior_pu: set[int],
    threshold: int = 3,
    failures: "list[NotificationFailure] | None" = None,
    prior_attempts_by_position: "dict[int, int] | None" = None,
) -> list[CriticalEvent]:
    """Stateless decision: which rows in `new_audit_rows` warrant a
    standalone push? See spec § 3.1 for the per-event rules.

    Args:
      new_audit_rows: iterable of audit rows (PaperAuditEvent or duck-typed
        equivalent — only `.id`, `.timestamp`, `.event_type`, `.strategy`,
        `.reason`, `.context` are read).
      kill_switch_cycle_skipped_in_period: lock 6g-L5 dedup fact. True
        means a prior KILL_SWITCH_CYCLE_SKIPPED already pushed in the
        current active period; suppress further skips.
      positions_with_prior_pu: lock 6g-L4b recovery dedup set. POSITION_CLOSED
        rows for these position_ids emit recovery push.
      threshold: lock 6g-L4a attempt_count gate (default 3).
      failures: optional list to append `NotificationFailure` records to
        when an invariant violation is detected.
      prior_attempts_by_position: cross-tick per-position max(attempt_count)
        from history BEFORE this batch — typically supplied by
        `repository.latest_price_unavailable_attempt_counts(...)`. Used
        by the lock-6g-L4c monotonic invariant check to detect
        regressions that span ticks (tick1=5 → tick2=2). Defaults to
        `{}` when omitted; the check then degrades to within-batch
        ordering only (acceptable for pure-function unit tests where
        the caller doesn't want to wire a Repository).

    Returns CriticalEvent[] in the same order as `new_audit_rows`. Pure;
    no DB / notifier / clock.
    """
    if failures is not None:
        _check_pu_monotonic(
            new_audit_rows, failures,
            prior_attempts_by_position=prior_attempts_by_position or {},
        )

    facts = _DedupFacts(
        kill_switch_cycle_skipped_in_period=kill_switch_cycle_skipped_in_period,
        positions_with_prior_pu=positions_with_prior_pu,
        threshold=threshold,
    )
    out: list[CriticalEvent] = []
    for row in new_audit_rows:
        ctx = row.context or {}
        et = row.event_type
        keep = False

        if et in _ALWAYS_CRITICAL:
            keep = True
        else:
            rule = _CONDITIONAL_RULES.get(et)
            if rule is not None:
                keep = rule(ctx, facts)

        if keep:
            out.append(CriticalEvent(
                event_type=et,
                audit_id=row.id,
                timestamp=row.timestamp,
                strategy=row.strategy,
                # reason: pass through None vs "" distinction (item 4
                # of round-4 review). Renderers treat both as "no
                # reason"; CLI / telemetry can match `is None`.
                reason=row.reason if row.reason else (
                    None if row.reason is None else ""
                ),
                # context: freeze into an immutable Mapping so the
                # downstream renderer cannot mutate it (item 2 of
                # round-4 review).
                context=_freeze_context(ctx),
            ))
    return out
```

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/observability/test_audit_projection_critical.py -v`
Expected: ~17 PASS.

- [ ] **Step 5: Ruff**

Run: `uv run ruff check marketpulse/observability/ tests/observability/`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add marketpulse/observability/__init__.py marketpulse/observability/audit_projection.py tests/observability/__init__.py tests/observability/test_audit_projection_critical.py
git commit -m "$(cat <<'EOF'
feat(phase-6g-T3): pure projection dataclasses + select_critical_events

Adds the audit-row → CriticalEvent[] pure decision function. Implements:
- 6g-L3 daily_loss filter on ORDER_REJECTED
- 6g-L4a attempt_count == threshold (default 3) on PRICE_UNAVAILABLE
- 6g-L4b POSITION_CLOSED recovery via positions_with_prior_pu set
- 6g-L5 KILL_SWITCH_CYCLE_SKIPPED dedup via boolean fact
- 6g-L9 TICK_REPROCESSED_COMPLETED critical
- Always-critical: ENGINE_INVARIANT_ERROR, SCHEDULER_GAP_DETECTED,
  KILL_SWITCH_FLIPPED

NotificationFailure / CriticalEvent / PlacedOrderDetail / TickSummary
dataclasses live here for re-use by T4 summarize_tick + T6 entrypoint.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T4: `summarize_tick` pure function

**Locks-Referenced:** 6g-L21 (cycle_status priority chain + canonical-tables sourcing).

**Files:**
- Modify: `marketpulse/observability/audit_projection.py` (append `summarize_tick`)
- Create: `tests/observability/test_audit_projection_summary.py`

- [ ] **Step 1: Write failing tests**

Create `tests/observability/test_audit_projection_summary.py`:

```python
# Layer: pure
"""6g-T4: summarize_tick — pure summary builder (lock 6g-L21)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal


@dataclass
class _Row:
    id: int
    timestamp: datetime
    event_type: str
    order_id: int | None = None
    strategy: str | None = None
    reason: str = ""
    context: dict | None = None

    def __post_init__(self):
        if self.context is None:
            self.context = {}


def _ts(hour: int = 18) -> datetime:
    return datetime(2026, 5, 22, hour, 0, tzinfo=UTC)


# === cycle_status priority chain (lock 6g-L21) ===

def test_summarize_reads_status_from_tick_completed_row():
    from marketpulse.observability.audit_projection import summarize_tick
    rows = [
        _Row(id=1, timestamp=_ts(), event_type="TICK_COMPLETED",
             context={"tick_date": "2026-05-22", "status": "completed"}),
    ]
    summary, failure = summarize_tick(
        new_audit_rows=rows,
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("10000.00"),
        active_positions_with_pu_attempts=[],
        active_positions_count=0,
    )
    assert summary.cycle_status == "completed"
    assert failure is None


def test_summarize_falls_back_to_kill_switch_cycle_skipped_status():
    """Lock 6g-L21: TICK_COMPLETED missing → use KILL_SWITCH_CYCLE_SKIPPED."""
    from marketpulse.observability.audit_projection import summarize_tick
    rows = [
        _Row(id=1, timestamp=_ts(),
             event_type="KILL_SWITCH_CYCLE_SKIPPED",
             context={"tick_date": "2026-05-22", "status": "skipped"}),
    ]
    summary, failure = summarize_tick(
        new_audit_rows=rows,
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("10000.00"),
        active_positions_with_pu_attempts=[],
        active_positions_count=0,
    )
    assert summary.cycle_status == "skipped"
    assert failure is None


def test_summarize_returns_unknown_status_and_failure_when_no_tick_row():
    """Lock 6g-L21: heartbeat still emits with status='unknown' + failure."""
    from marketpulse.observability.audit_projection import (
        NotificationFailure, summarize_tick,
    )
    summary, failure = summarize_tick(
        new_audit_rows=[],
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("10000.00"),
        active_positions_with_pu_attempts=[],
        active_positions_count=0,
    )
    assert summary.cycle_status == "unknown"
    assert isinstance(failure, NotificationFailure)
    assert failure.event_type == "tick_summary"
    assert failure.error == "missing_tick_completed_row"


def test_summarize_ignores_status_rows_for_different_tick_date():
    """tick_date filter: a TICK_COMPLETED for a DIFFERENT date in the
    window must NOT seed cycle_status for the current tick."""
    from marketpulse.observability.audit_projection import summarize_tick
    rows = [
        _Row(id=1, timestamp=_ts(),
             event_type="TICK_COMPLETED",
             context={"tick_date": "2026-05-20", "status": "completed"}),
    ]
    summary, failure = summarize_tick(
        new_audit_rows=rows,
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("10000.00"),
        active_positions_with_pu_attempts=[],
        active_positions_count=0,
    )
    assert summary.cycle_status == "unknown"
    assert failure is not None


# === Aggregation: orders, fills, exits ===

def test_summarize_aggregates_orders_placed_detail():
    from marketpulse.observability.audit_projection import (
        PlacedOrderDetail, summarize_tick,
    )
    rows = [
        _Row(id=1, timestamp=_ts(), event_type="ORDER_PLACED",
             strategy="momentum",
             context={"ticker": "AAPL", "quantity": 10}),
        _Row(id=2, timestamp=_ts(), event_type="ORDER_PLACED",
             strategy="defensive",
             context={"ticker": "NVDA", "quantity": 5}),
        _Row(id=3, timestamp=_ts(), event_type="TICK_COMPLETED",
             context={"tick_date": "2026-05-22", "status": "completed"}),
    ]
    summary, _ = summarize_tick(
        new_audit_rows=rows,
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("8500.00"),
        active_positions_with_pu_attempts=[],
        active_positions_count=2,
    )
    assert summary.orders_placed == 2
    assert summary.orders_placed_detail == [
        PlacedOrderDetail(ticker="AAPL", strategy="momentum", quantity=10),
        PlacedOrderDetail(ticker="NVDA", strategy="defensive", quantity=5),
    ]


def test_summarize_aggregates_rejects_breakdown():
    from marketpulse.observability.audit_projection import summarize_tick
    rows = [
        _Row(id=1, timestamp=_ts(), event_type="ORDER_REJECTED",
             context={"ticker": "GOOG", "failed_gates": ["sector_exposure"]}),
        _Row(id=2, timestamp=_ts(), event_type="ORDER_REJECTED",
             context={"ticker": "TSLA", "failed_gates": ["daily_loss"]}),
        _Row(id=3, timestamp=_ts(), event_type="TICK_COMPLETED",
             context={"tick_date": "2026-05-22", "status": "completed"}),
    ]
    summary, _ = summarize_tick(
        new_audit_rows=rows,
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("10000.00"),
        active_positions_with_pu_attempts=[],
        active_positions_count=0,
    )
    assert summary.orders_rejected == 2
    # First failed_gate per row used as the "gate_name"
    assert summary.orders_rejected_breakdown == [
        ("GOOG", "sector_exposure"),
        ("TSLA", "daily_loss"),
    ]


def test_summarize_collects_entry_fills_and_exits_with_pnl():
    from marketpulse.observability.audit_projection import summarize_tick
    rows = [
        _Row(id=1, timestamp=_ts(), event_type="ORDER_ENTRY_FILLED",
             context={"ticker": "AAPL", "fill_price": "155.50"}),
        _Row(id=2, timestamp=_ts(), event_type="POSITION_CLOSED",
             context={"ticker": "TSLA", "exit_price": "248.30",
                      "realized_pnl": "32.50"}),
        _Row(id=3, timestamp=_ts(), event_type="TICK_COMPLETED",
             context={"tick_date": "2026-05-22", "status": "completed"}),
    ]
    summary, _ = summarize_tick(
        new_audit_rows=rows,
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("10032.50"),
        active_positions_with_pu_attempts=[],
        active_positions_count=1,
    )
    assert summary.entries_filled == [("AAPL", Decimal("155.50"))]
    assert summary.positions_closed == [
        ("TSLA", Decimal("248.30"), Decimal("32.50")),
    ]
    assert summary.total_realized_pnl == Decimal("32.50")


def test_summarize_counts_cancelled_and_duplicates():
    from marketpulse.observability.audit_projection import summarize_tick
    rows = [
        _Row(id=1, timestamp=_ts(), event_type="ORDER_CANCELLED",
             context={"ticker": "AAPL"}),
        _Row(id=2, timestamp=_ts(), event_type="ORDER_PLACED_DUPLICATE",
             context={"idempotency_key": "k1"}),
        _Row(id=3, timestamp=_ts(), event_type="ORDER_PLACED_DUPLICATE",
             context={"idempotency_key": "k2"}),
        _Row(id=4, timestamp=_ts(), event_type="TICK_COMPLETED",
             context={"tick_date": "2026-05-22", "status": "completed"}),
    ]
    summary, _ = summarize_tick(
        new_audit_rows=rows,
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("10000.00"),
        active_positions_with_pu_attempts=[],
        active_positions_count=0,
    )
    assert summary.orders_cancelled == 1
    assert summary.duplicates_skipped == 2


def test_summarize_threads_active_positions_with_pu_attempts():
    """Field-sourcing rule 2 (lock 6g-L21): active positions come from
    canonical tables via the caller, not audit rows."""
    from marketpulse.observability.audit_projection import summarize_tick
    rows = [
        _Row(id=1, timestamp=_ts(), event_type="TICK_COMPLETED",
             context={"tick_date": "2026-05-22", "status": "completed"}),
    ]
    summary, _ = summarize_tick(
        new_audit_rows=rows,
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("9800.00"),
        active_positions_with_pu_attempts=[("AAPL", 3), ("MSFT", 4)],
        active_positions_count=4,
    )
    assert summary.active_positions_count == 4
    assert summary.active_positions_with_pu == [("AAPL", 3), ("MSFT", 4)]


def test_summarize_heartbeat_zero_activity():
    """Spec § 9.1: zero-activity day still produces a complete summary."""
    from marketpulse.observability.audit_projection import summarize_tick
    rows = [
        _Row(id=1, timestamp=_ts(), event_type="TICK_COMPLETED",
             context={"tick_date": "2026-05-22", "status": "completed"}),
    ]
    summary, failure = summarize_tick(
        new_audit_rows=rows,
        tick_date=date(2026, 5, 22),
        cash_balance_end=Decimal("10000.00"),
        active_positions_with_pu_attempts=[],
        active_positions_count=0,
    )
    assert summary.tick_date == date(2026, 5, 22)
    assert summary.cycle_status == "completed"
    assert summary.orders_placed == 0
    assert summary.orders_rejected == 0
    assert summary.orders_cancelled == 0
    assert summary.duplicates_skipped == 0
    assert summary.entries_filled == []
    assert summary.positions_closed == []
    assert summary.total_realized_pnl == Decimal("0")
    assert summary.cash_balance_end == Decimal("10000.00")
    assert summary.active_positions_count == 0
    assert failure is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/observability/test_audit_projection_summary.py -v`
Expected: ImportError on `summarize_tick`.

- [ ] **Step 3: Implement `summarize_tick`**

Append to `marketpulse/observability/audit_projection.py`:

```python


# === summarize_tick (lock 6g-L21) ===

MAX_NUMERIC_FAILURES_PER_TICK = 10
"""Item 3 of round-4 review: cap _safe_decimal NotificationFailure
fan-out so a wholesale audit-schema bug (100 malformed rows) can't
flood CLI / logs / summary / telemetry with hundreds of failure
records. After this many entries, further malformed values are still
quantized to the default but the FAILURE is suppressed; a single
sentinel `malformed_numeric_capped` entry is appended at the end so
the operator can see the cap kicked in."""


def _safe_decimal(
    value, default: str = "0", *,
    field_name: str | None = None,
    failures: "list[NotificationFailure] | None" = None,
) -> Decimal:
    """Convert audit-context numeric (str / Decimal / int / float) to
    Decimal defensively. Returns Decimal(default) on any conversion
    failure rather than raising — heartbeat summary must never crash on
    a malformed context.

    If `field_name` AND `failures` are both supplied, a non-None value
    that fails conversion appends a NotificationFailure(event_type=
    "tick_summary", error=f"malformed_numeric:{field_name}") so the
    operator can tell the heartbeat went through with a stub default
    instead of real data. None values are treated as "missing" and do
    NOT generate a failure (the audit row simply didn't carry the key).

    Cap behavior (item 3 of round-4 review): once `failures` already
    contains MAX_NUMERIC_FAILURES_PER_TICK entries whose error starts
    with `malformed_numeric:`, further entries are suppressed AND a
    single `malformed_numeric_capped` sentinel is added (only once)
    so the cap is itself observable. The quantize still falls back to
    `default`, so summary computation is unaffected.
    """
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception as exc:
        if field_name is not None and failures is not None:
            existing = sum(
                1 for f in failures
                if f.error.startswith("malformed_numeric:")
                and f.error != "malformed_numeric_capped"
            )
            if existing < MAX_NUMERIC_FAILURES_PER_TICK:
                failures.append(NotificationFailure(
                    event_type="tick_summary",
                    title="",
                    error=f"malformed_numeric:{field_name}:{type(exc).__name__}",
                ))
            elif not any(
                f.error == "malformed_numeric_capped" for f in failures
            ):
                failures.append(NotificationFailure(
                    event_type="tick_summary",
                    title="",
                    error="malformed_numeric_capped",
                ))
        return Decimal(default)


def _first_failed_gate(context: dict) -> str:
    """Returns the first entry in context['failed_gates'] or 'unknown'."""
    gates = context.get("failed_gates")
    if isinstance(gates, (list, tuple)) and gates:
        return str(gates[0])
    return "unknown"


def _resolve_cycle_status(
    rows, tick_date: date,
) -> tuple[str, tuple["NotificationFailure", ...]]:
    """Lock 6g-L21: priority chain TICK_COMPLETED → KILL_SWITCH_CYCLE_SKIPPED
    → ('unknown', failure).

    Returns `tuple[str, tuple[NotificationFailure, ...]]` — empty tuple
    when the status was resolved from a TICK_COMPLETED or
    KILL_SWITCH_CYCLE_SKIPPED row; single-failure tuple in the fallback
    "unknown" branch. The uniform tuple shape means the caller in
    `summarize_tick` just extends its own `failures` list with this
    return value, no `if failure is not None` branching."""
    iso = tick_date.isoformat()
    for row in rows:
        if row.event_type == "TICK_COMPLETED" and (
            (row.context or {}).get("tick_date") == iso
        ):
            return str((row.context or {}).get("status", "completed")), ()
    for row in rows:
        if row.event_type == "KILL_SWITCH_CYCLE_SKIPPED" and (
            (row.context or {}).get("tick_date") == iso
        ):
            return str((row.context or {}).get("status", "skipped")), ()
    return "unknown", (NotificationFailure(
        event_type="tick_summary",
        title="",
        error="missing_tick_completed_row",
    ),)


def summarize_tick(
    *,
    new_audit_rows,
    tick_date: date,
    cash_balance_end: Decimal,
    active_positions_with_pu_attempts: list[tuple[str, int]],
    active_positions_count: int,
) -> tuple[TickSummary, tuple["NotificationFailure", ...]]:
    """Pure aggregation for the routine summary push (lock 6g-L21).

    Field-sourcing rules:
      1. `cycle_status` reads from the matching TICK_COMPLETED row's
         context["status"]; falls back to KILL_SWITCH_CYCLE_SKIPPED
         (status defaults to "skipped"); finally to "unknown" + a
         NotificationFailure(event_type="tick_summary",
         error="missing_tick_completed_row").
      2. `cash_balance_end` + `active_positions_count` +
         `active_positions_with_pu_attempts` are passed in from canonical
         tables (paper_cash_ledger / paper_position) — NOT extracted
         from audit context. Audit only owns event history; canonical
         state has its own source.

    Returns (TickSummary, optional NotificationFailure). The failure is
    returned (not raised) so the caller appends it to
    NotificationResult.failures while still emitting the summary push
    (heartbeat discipline)."""
    cycle_status, cycle_failures = _resolve_cycle_status(
        new_audit_rows, tick_date,
    )

    # Unified failure list (lock 6g-L21 heartbeat discipline): summary
    # always emits; cycle-status and numeric-coercion failures both
    # accumulate here for the caller to extend its NotificationResult
    # with.
    failures: list[NotificationFailure] = list(cycle_failures)

    orders_placed_detail: list[PlacedOrderDetail] = []
    orders_rejected_breakdown: list[tuple[str, str]] = []
    entries_filled: list[tuple[str, Decimal]] = []
    positions_closed: list[tuple[str, Decimal, Decimal]] = []
    orders_cancelled = 0
    duplicates_skipped = 0
    total_realized = Decimal("0")

    for row in new_audit_rows:
        ctx = row.context or {}
        et = row.event_type
        if et == "ORDER_PLACED":
            orders_placed_detail.append(PlacedOrderDetail(
                ticker=str(ctx.get("ticker", "?")),
                strategy=str(row.strategy or ctx.get("strategy", "?")),
                quantity=int(ctx.get("quantity", 0) or 0),
            ))
        elif et == "ORDER_REJECTED":
            orders_rejected_breakdown.append((
                str(ctx.get("ticker", "?")),
                _first_failed_gate(ctx),
            ))
        elif et == "ORDER_CANCELLED":
            orders_cancelled += 1
        elif et == "ORDER_PLACED_DUPLICATE":
            duplicates_skipped += 1
        elif et == "ORDER_ENTRY_FILLED":
            entries_filled.append((
                str(ctx.get("ticker", "?")),
                _safe_decimal(ctx.get("fill_price"),
                              field_name="fill_price",
                              failures=failures),
            ))
        elif et == "POSITION_CLOSED":
            pnl = _safe_decimal(ctx.get("realized_pnl"),
                                field_name="realized_pnl",
                                failures=failures)
            positions_closed.append((
                str(ctx.get("ticker", "?")),
                _safe_decimal(ctx.get("exit_price"),
                              field_name="exit_price",
                              failures=failures),
                pnl,
            ))
            total_realized += pnl

    summary = TickSummary(
        tick_date=tick_date,
        cycle_status=cycle_status,
        orders_placed=len(orders_placed_detail),
        orders_placed_detail=orders_placed_detail,
        orders_rejected=len(orders_rejected_breakdown),
        orders_rejected_breakdown=orders_rejected_breakdown,
        orders_cancelled=orders_cancelled,
        duplicates_skipped=duplicates_skipped,
        entries_filled=entries_filled,
        positions_closed=positions_closed,
        total_realized_pnl=total_realized,
        cash_balance_end=cash_balance_end,
        active_positions_count=active_positions_count,
        active_positions_with_pu=list(active_positions_with_pu_attempts),
    )
    # The unified `failures` list already contains:
    #   - any cycle_status fallback failure (from _resolve_cycle_status)
    #   - all malformed_numeric failures (from _safe_decimal call sites)
    # Caller (paper_tick_notifier.py) extends NotificationResult.failures
    # with this tuple — the heartbeat summary always ships.
    return summary, tuple(failures)
```

> **Unified failure model (lock 6g-L21 + reviewer round 3):** every
> piece of the summarization pipeline (`_resolve_cycle_status` and
> every `_safe_decimal` call) appends into a single
> `failures: list[NotificationFailure]`. The function returns
> `tuple[TickSummary, tuple[NotificationFailure, ...]]` with a possibly-
> empty tuple. No `None` sentinel anywhere — the caller never branches
> on `is not None`, it just extends its own list with the returned
> tuple. CLI / notifier / tests / telemetry all see the same shape.

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/observability/test_audit_projection_summary.py -v`
Expected: 10 PASS.

- [ ] **Step 5: Run both projection test files together**

Run: `uv run pytest tests/observability/ -v`
Expected: 27 PASS total (T3 + T4).

- [ ] **Step 6: Ruff**

Run: `uv run ruff check marketpulse/observability/ tests/observability/`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add marketpulse/observability/audit_projection.py tests/observability/test_audit_projection_summary.py
git commit -m "$(cat <<'EOF'
feat(phase-6g-T4): summarize_tick pure builder + cycle_status fallback

Lock 6g-L21 priority chain implemented:
  TICK_COMPLETED.context.status → KILL_SWITCH_CYCLE_SKIPPED.context.status
  → ('unknown', NotificationFailure(error='missing_tick_completed_row'))

Heartbeat discipline: the summary is built (and a push will be emitted
by T6) even when the tick row is missing — the failure is returned so
the entrypoint records it in NotificationResult.failures.

Canonical-table fields (cash_balance_end, active_positions_count,
active_positions_with_pu_attempts) are threaded in from the caller
rather than read from audit context.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T5: `templates.py` — pure renderers

**Locks-Referenced:** 6g-L10 (emoji prefix taxonomy), 6g-L11 (url=None in MVP), 6g-L12 (compact + section-skipping summary, money with sign + 2 decimals, `4+` PU cap).

**Files:**
- Create: `marketpulse/observability/templates.py`
- Create: `tests/observability/test_templates.py`

- [ ] **Step 1: Write failing tests**

Create `tests/observability/test_templates.py`:

```python
# Layer: pure
"""6g-T5: templates.py — title/body renderers (locks 6g-L10, L11, L12)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest


def _ev(event_type, **kwargs):
    from marketpulse.observability.audit_projection import CriticalEvent
    defaults = dict(
        audit_id=1,
        timestamp=datetime(2026, 5, 22, 21, 30, tzinfo=UTC),
        strategy=None,
        reason="",
        context={},
    )
    defaults.update(kwargs)
    return CriticalEvent(event_type=event_type, **defaults)


# === Critical: kill switch ===

def test_render_kill_switch_flipped_active_true():
    from marketpulse.observability.templates import render_critical_event
    ev = _ev("KILL_SWITCH_FLIPPED",
             context={"to_state": True, "reason": "max_drawdown_exceeded"})
    title, body = render_critical_event(ev)
    assert title == "🛑 Kill Switch FLIPPED"
    assert "max_drawdown_exceeded" in body
    assert "Time:" in body


def test_render_kill_switch_flipped_active_false():
    from marketpulse.observability.templates import render_critical_event
    ev = _ev("KILL_SWITCH_FLIPPED",
             context={"to_state": False, "reason": "manual_reset"})
    title, body = render_critical_event(ev)
    assert title == "✅ Kill Switch CLEARED"
    assert "manual_reset" in body


def test_render_kill_switch_cycle_skipped():
    from marketpulse.observability.templates import render_critical_event
    ev = _ev("KILL_SWITCH_CYCLE_SKIPPED",
             context={"tick_date": "2026-05-23",
                      "reason": "kill_switch_active"})
    title, body = render_critical_event(ev)
    assert title == "🛑 Kill Switch — Cycle Skipped"
    assert "2026-05-23" in body
    assert "kill_switch_active" in body


# === Critical: engine / scheduler ===

def test_render_engine_invariant_error():
    from marketpulse.observability.templates import render_critical_event
    ev = _ev("ENGINE_INVARIANT_ERROR",
             context={"phase": "exit_materialization",
                      "error": "decimal-mismatch",
                      "position_id": 42})
    title, body = render_critical_event(ev)
    assert title == "🛑 Engine Invariant Error"
    assert "exit_materialization" in body
    assert "decimal-mismatch" in body
    assert "42" in body


def test_render_scheduler_gap_detected():
    from marketpulse.observability.templates import render_critical_event
    ev = _ev("SCHEDULER_GAP_DETECTED",
             context={"last_tick_date": "2026-05-15", "gap_days": 4})
    title, body = render_critical_event(ev)
    assert title == "🛑 Scheduler Gap Detected"
    assert "2026-05-15" in body
    assert "4" in body


def test_render_tick_reprocessed_completed():
    from marketpulse.observability.templates import render_critical_event
    ev = _ev("TICK_REPROCESSED_COMPLETED",
             context={"tick_date": "2026-05-22"})
    title, body = render_critical_event(ev)
    assert title == "⚠️ Tick Reprocessed"
    assert "2026-05-22" in body


# === Critical: daily loss ===

def test_render_daily_loss_reject():
    from marketpulse.observability.templates import render_critical_event
    ev = _ev("ORDER_REJECTED",
             strategy="momentum",
             context={"ticker": "AAPL", "strategy": "momentum",
                      "quantity": 10,
                      "failed_gates": ["daily_loss"],
                      "loss_today": "-150.00"})
    title, body = render_critical_event(ev)
    assert title == "🛑 Daily Loss Limit Tripped"
    assert "AAPL" in body
    assert "momentum" in body
    assert "10" in body
    assert "daily_loss" in body
    # Loss formatted with sign + 2 decimals
    assert "-$150.00" in body or "-150.00" in body


# === Critical: PRICE_UNAVAILABLE stuck ===

def test_render_price_unavailable_stuck():
    from marketpulse.observability.templates import render_critical_event
    ev = _ev("PRICE_UNAVAILABLE",
             strategy="momentum",
             context={"ticker": "AAPL", "position_id": 42,
                      "attempt_count": 3,
                      "horizon_date": "2026-05-22",
                      "source": "yfinance"})
    title, body = render_critical_event(ev)
    assert title == "⚠️ Position Stuck — AAPL"
    assert "momentum" in body
    assert "2026-05-22" in body
    assert "3 retries failed" in body or "3 retries" in body
    assert "yfinance" in body


# === Critical: recovery ===

def test_render_position_recovered():
    from marketpulse.observability.templates import render_critical_event
    ev = _ev("POSITION_CLOSED",
             context={"ticker": "AAPL", "position_id": 42,
                      "exit_price": "152.10", "realized_pnl": "21.00",
                      "retry_count": 5})
    title, body = render_critical_event(ev)
    assert title == "✅ Position Recovered — AAPL"
    assert "5 retries" in body
    assert "152.10" in body
    # P&L sign + 2 decimals
    assert "+$21.00" in body


def test_render_position_recovered_negative_pnl_sign():
    from marketpulse.observability.templates import render_critical_event
    ev = _ev("POSITION_CLOSED",
             context={"ticker": "AAPL", "position_id": 42,
                      "exit_price": "140.10", "realized_pnl": "-12.34",
                      "retry_count": 3})
    _, body = render_critical_event(ev)
    assert "-$12.34" in body


# === Summary template ===

def _summary(**overrides):
    from marketpulse.observability.audit_projection import (
        PlacedOrderDetail, TickSummary,
    )
    defaults = dict(
        tick_date=date(2026, 5, 22),
        cycle_status="completed",
        orders_placed=0,
        orders_placed_detail=[],
        orders_rejected=0,
        orders_rejected_breakdown=[],
        orders_cancelled=0,
        duplicates_skipped=0,
        entries_filled=[],
        positions_closed=[],
        total_realized_pnl=Decimal("0"),
        cash_balance_end=Decimal("10000.00"),
        active_positions_count=0,
        active_positions_with_pu=[],
    )
    defaults.update(overrides)
    return TickSummary(**defaults)


def test_render_summary_zero_activity_heartbeat():
    """Spec § 9.1: even zero-activity day → header + status + cash."""
    from marketpulse.observability.templates import render_tick_summary
    s = _summary()
    title, body = render_tick_summary(s)
    assert title == "📊 Paper Tick 2026-05-22"
    assert "0 placed, 0 rejected" in body
    assert "0 entries, 0 exits" in body
    assert "+$0.00" in body
    assert "$10,000.00" in body or "$10000.00" in body
    assert "活跃持仓：0" in body
    assert "Status: completed" in body


def test_render_summary_full_activity():
    """Spec § 9.2: activity day with rejects + entries + exit."""
    from marketpulse.observability.audit_projection import PlacedOrderDetail
    from marketpulse.observability.templates import render_tick_summary
    s = _summary(
        orders_placed=3,
        orders_placed_detail=[
            PlacedOrderDetail(ticker="AAPL", strategy="momentum", quantity=10),
            PlacedOrderDetail(ticker="NVDA", strategy="defensive", quantity=5),
            PlacedOrderDetail(ticker="MSFT", strategy="momentum", quantity=8),
        ],
        orders_rejected=1,
        orders_rejected_breakdown=[("GOOG", "sector_exposure")],
        entries_filled=[("AAPL", Decimal("155.50")),
                        ("NVDA", Decimal("432.10"))],
        positions_closed=[("TSLA", Decimal("248.30"), Decimal("32.50"))],
        total_realized_pnl=Decimal("32.50"),
        cash_balance_end=Decimal("9847.50"),
        active_positions_count=4,
    )
    _, body = render_tick_summary(s)
    assert "3 placed, 1 rejected" in body
    assert "AAPL × 10 (momentum)" in body
    assert "NVDA × 5 (defensive)" in body
    assert "MSFT × 8 (momentum)" in body
    assert "❌ GOOG (sector_exposure)" in body
    assert "2 entries, 1 exit" in body
    assert "AAPL @ 155.50" in body
    assert "NVDA @ 432.10" in body
    assert "TSLA @ 248.30" in body
    assert "+$32.50" in body
    assert "9,847.50" in body or "9847.50" in body
    assert "活跃持仓：4" in body


def test_render_summary_omits_empty_orders_section():
    """Lock 6g-L12: empty sections omitted. But header + status + cash
    always present. With orders=0, the placed-detail list and rejects
    list must NOT appear; aggregate '0 placed, 0 rejected' line still
    appears as a one-liner (spec § 9.1)."""
    from marketpulse.observability.audit_projection import PlacedOrderDetail
    from marketpulse.observability.templates import render_tick_summary
    s = _summary(
        orders_placed=0, orders_rejected=0,
        entries_filled=[("AAPL", Decimal("100.00"))],
        positions_closed=[],
    )
    _, body = render_tick_summary(s)
    assert "0 placed, 0 rejected" in body
    # Entry section present, exit detail not present
    assert "ENTRY: AAPL @ 100.00" in body
    # No "EXIT: " line when no exits
    assert "EXIT:" not in body


def test_render_summary_pu_attempt_cap_4_plus():
    """Lock 6g-L12 `4+` notation: attempts ≥ 4 render as '4+' (cap), not
    the raw integer."""
    from marketpulse.observability.templates import render_tick_summary
    s = _summary(
        active_positions_count=2,
        active_positions_with_pu=[("AAPL", 4), ("MSFT", 7)],
    )
    _, body = render_tick_summary(s)
    # Both attempt counts should render with the 4+ cap
    assert "AAPL" in body
    assert "MSFT" in body
    assert "4+" in body
    # Should NOT contain the raw "7" attempt number
    assert "attempt 7" not in body


def test_render_summary_pu_attempt_under_threshold():
    """attempt_count < 4 renders as raw number with /3 denominator."""
    from marketpulse.observability.templates import render_tick_summary
    s = _summary(
        active_positions_count=1,
        active_positions_with_pu=[("AAPL", 2)],
    )
    _, body = render_tick_summary(s)
    assert "AAPL" in body
    # e.g. "(1 with PRICE_UNAVAILABLE attempt 2/3)"
    assert "2/3" in body


def test_render_summary_money_sign_and_two_decimals():
    """Lock 6g-L12: money rendered with sign + 2 decimals."""
    from marketpulse.observability.templates import render_tick_summary
    s = _summary(total_realized_pnl=Decimal("-12.50"),
                 cash_balance_end=Decimal("9987.50"))
    _, body = render_tick_summary(s)
    assert "-$12.50" in body


def test_render_summary_status_skipped():
    from marketpulse.observability.templates import render_tick_summary
    s = _summary(cycle_status="skipped")
    _, body = render_tick_summary(s)
    assert "Status: skipped" in body
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/observability/test_templates.py -v`
Expected: ModuleNotFoundError on `marketpulse.observability.templates`.

- [ ] **Step 3: Implement templates.py**

Create `marketpulse/observability/templates.py`:

```python
"""Phase 6g pure renderers — CriticalEvent / TickSummary → (title, body).

No I/O, no notifier, no DB. Renderers consume the dataclasses produced
by audit_projection.py (lock 6g-L10 emoji taxonomy + 6g-L12 formatting).

Money formatting convention (lock 6g-L12):
- Realized P&L, daily loss: rendered as `+$N.NN` / `-$N.NN` with explicit
  sign and 2 decimal places (banker's quantize at the boundary).
- Cash balance: rendered with thousands separator and 2 decimals.
- Prices: 2 decimals, no sign (always positive in MVP).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from marketpulse.observability.audit_projection import (
    CriticalEvent,
    TickSummary,
)

_NY = ZoneInfo("America/New_York")
_PU_CAP = 4
"""Display cap for PRICE_UNAVAILABLE attempt counter (lock 6g-L12 `4+`)."""


def _money_signed(value: Decimal) -> str:
    """Render a Decimal as `+$N.NN` / `-$N.NN`."""
    q = value.quantize(Decimal("0.01"))
    sign = "-" if q < 0 else "+"
    return f"{sign}${abs(q):.2f}"


def _money_plain(value: Decimal) -> str:
    """Cash balance: `$9,847.50`."""
    q = value.quantize(Decimal("0.01"))
    return f"${q:,.2f}"


def _price(value: Decimal) -> str:
    """Price: 2 decimals, no sign."""
    return f"{value.quantize(Decimal('0.01')):.2f}"


def _hhmm_ny(ts: datetime) -> str:
    """Render an audit timestamp as HH:MM NY."""
    return ts.astimezone(_NY).strftime("%H:%M NY")


# === Critical event templates (spec § 4.2) ===

def _render_kill_switch_flipped(ev: CriticalEvent) -> tuple[str, str]:
    """Render KILL_SWITCH_FLIPPED. The audit row's context schema (per
    marketpulse/trading/kill_switch.py L40-L60) is:
        {"from_state": bool, "to_state": bool, "actor": str}
    `reason` lives on the audit row's reason column (not context).
    `to_state=True` means the switch is now ACTIVE (flipped to stop).
    `to_state=False` means it just CLEARED."""
    to_state = bool(ev.context.get("to_state"))
    # reason: ev.reason is `None` when the audit row's reason column was
    # unset, `""` when explicitly blank, otherwise a real string. For
    # rendering, all three collapse to "no reason" or the actual text.
    reason = ev.reason or ""
    if to_state:
        title = "🛑 Kill Switch FLIPPED"
    else:
        title = "✅ Kill Switch CLEARED"
    body_lines = [f"Reason: {reason}", f"Time:   {_hhmm_ny(ev.timestamp)}"]
    return title, "\n".join(body_lines)


def _render_kill_switch_cycle_skipped(ev: CriticalEvent) -> tuple[str, str]:
    tick_date = ev.context.get("tick_date", "?")
    reason = ev.context.get("reason", ev.reason or "")
    body = f"Date:   {tick_date}\nReason: {reason}"
    return "🛑 Kill Switch — Cycle Skipped", body


def _render_engine_invariant_error(ev: CriticalEvent) -> tuple[str, str]:
    phase = ev.context.get("phase", "?")
    error = ev.context.get("error", ev.reason or "?")
    lines = [f"Phase:  {phase}", f"Error:  {error}"]
    pid = ev.context.get("position_id")
    oid = ev.context.get("order_id")
    if pid is not None:
        lines.append(f"Position: {pid}")
    if oid is not None:
        lines.append(f"Order:    {oid}")
    return "🛑 Engine Invariant Error", "\n".join(lines)


def _render_scheduler_gap(ev: CriticalEvent) -> tuple[str, str]:
    last = ev.context.get("last_tick_date", "?")
    gap = ev.context.get("gap_days", "?")
    body = f"Last tick: {last}\nMissing:   {gap} trading day(s)"
    return "🛑 Scheduler Gap Detected", body


def _render_tick_reprocessed(ev: CriticalEvent) -> tuple[str, str]:
    tick_date = ev.context.get("tick_date", "?")
    body = f"Date: {tick_date}\nOriginal run superseded"
    return "⚠️ Tick Reprocessed", body


def _render_daily_loss_reject(ev: CriticalEvent) -> tuple[str, str]:
    ctx = ev.context
    ticker = ctx.get("ticker", "?")
    strategy = ev.strategy or ctx.get("strategy", "?")
    quantity = ctx.get("quantity", "?")
    loss_raw = ctx.get("loss_today", "0")
    try:
        loss_str = _money_signed(Decimal(str(loss_raw)))
    except Exception:
        loss_str = str(loss_raw)
    gates = ", ".join(ctx.get("failed_gates", []) or ["daily_loss"])
    body = (
        f"Order: {ticker} {strategy} × {quantity}\n"
        f"Loss today: {loss_str}\n"
        f"Failed gates: {gates}"
    )
    return "🛑 Daily Loss Limit Tripped", body


def _render_price_unavailable_stuck(ev: CriticalEvent) -> tuple[str, str]:
    ctx = ev.context
    ticker = ctx.get("ticker", "?")
    strategy = ev.strategy or ctx.get("strategy", "?")
    horizon = ctx.get("horizon_date", "?")
    attempts = ctx.get("attempt_count", "?")
    source = ctx.get("source", "?")
    body = (
        f"Strategy: {strategy}\n"
        f"Horizon:  {horizon}\n"
        f"{attempts} retries failed\n"
        f"Source:   {source}"
    )
    return f"⚠️ Position Stuck — {ticker}", body


def _render_position_recovered(ev: CriticalEvent) -> tuple[str, str]:
    ctx = ev.context
    ticker = ctx.get("ticker", "?")
    retries = ctx.get("retry_count", ctx.get("attempt_count", "?"))
    try:
        exit_price_str = _price(Decimal(str(ctx.get("exit_price", "0"))))
    except Exception:
        exit_price_str = str(ctx.get("exit_price", "?"))
    try:
        pnl_str = _money_signed(Decimal(str(ctx.get("realized_pnl", "0"))))
    except Exception:
        pnl_str = str(ctx.get("realized_pnl", "?"))
    body = (
        f"Closed after {retries} retries\n"
        f"Exit @ {exit_price_str}\n"
        f"Realized P&L: {pnl_str}"
    )
    return f"✅ Position Recovered — {ticker}", body


def render_critical_event(ev: CriticalEvent) -> tuple[str, str]:
    """Dispatch on `ev.event_type` → (title, body) per spec § 4.2.

    Pure function: raises only if the caller passes an unknown event_type.
    Callers should not reach this branch because select_critical_events
    only emits the documented set."""
    et = ev.event_type
    if et == "KILL_SWITCH_FLIPPED":
        return _render_kill_switch_flipped(ev)
    if et == "KILL_SWITCH_CYCLE_SKIPPED":
        return _render_kill_switch_cycle_skipped(ev)
    if et == "ENGINE_INVARIANT_ERROR":
        return _render_engine_invariant_error(ev)
    if et == "SCHEDULER_GAP_DETECTED":
        return _render_scheduler_gap(ev)
    if et == "TICK_REPROCESSED_COMPLETED":
        return _render_tick_reprocessed(ev)
    if et == "ORDER_REJECTED":
        return _render_daily_loss_reject(ev)
    if et == "PRICE_UNAVAILABLE":
        return _render_price_unavailable_stuck(ev)
    if et == "POSITION_CLOSED":
        return _render_position_recovered(ev)
    raise ValueError(f"render_critical_event: unsupported event_type {et!r}")


# === Tick summary template (spec § 4.3) ===

def _format_pu_attempt(attempt: int) -> str:
    """Lock 6g-L12: cap at `4+` to suppress noise once we're past the
    standalone-push threshold."""
    if attempt >= _PU_CAP:
        return "4+"
    return f"{attempt}/3"


def render_tick_summary(summary: TickSummary) -> tuple[str, str]:
    """Render the routine summary push. Sections with no content are
    omitted from the detail blocks (lock 6g-L12), but the header /
    aggregate counters / status footer are ALWAYS present (heartbeat
    discipline, spec § 9.1)."""
    title = f"📊 Paper Tick {summary.tick_date.isoformat()}"
    lines: list[str] = []

    # 订单 — counts line always present; detail block only if non-empty.
    lines.append(
        f"订单：{summary.orders_placed} placed, "
        f"{summary.orders_rejected} rejected"
    )
    for d in summary.orders_placed_detail:
        lines.append(f"  {d.ticker} × {d.quantity} ({d.strategy})")
    for ticker, gate in summary.orders_rejected_breakdown:
        lines.append(f"  ❌ {ticker} ({gate})")
    if summary.orders_cancelled or summary.duplicates_skipped:
        extras = []
        if summary.orders_cancelled:
            extras.append(f"{summary.orders_cancelled} cancelled")
        if summary.duplicates_skipped:
            extras.append(f"{summary.duplicates_skipped} duplicates")
        lines.append("  (" + ", ".join(extras) + ")")
    lines.append("")

    # 成交
    entries_count = len(summary.entries_filled)
    exits_count = len(summary.positions_closed)
    lines.append(
        f"成交：{entries_count} entries, {exits_count} "
        f"exit{'s' if exits_count != 1 else ''}"
    )
    if summary.entries_filled:
        entry_parts = [f"{t} @ {_price(p)}" for t, p in summary.entries_filled]
        lines.append("  ENTRY: " + ", ".join(entry_parts))
    if summary.positions_closed:
        for t, p, pnl in summary.positions_closed:
            lines.append(
                f"  EXIT:  {t} @ {_price(p)}, P&L {_money_signed(pnl)}"
            )
    lines.append("")

    # P&L + cash + active positions
    lines.append(f"今日 P&L：{_money_signed(summary.total_realized_pnl)} (realized)")
    lines.append(f"现金：{_money_plain(summary.cash_balance_end)}")

    active_line = f"活跃持仓：{summary.active_positions_count}"
    if summary.active_positions_with_pu:
        pu_parts = [
            f"{t} attempt {_format_pu_attempt(a)}"
            for t, a in summary.active_positions_with_pu
        ]
        active_line += (
            f" ({len(summary.active_positions_with_pu)} with PRICE_UNAVAILABLE "
            + ", ".join(pu_parts) + ")"
        )
    lines.append(active_line)
    lines.append("")
    lines.append(f"Status: {summary.cycle_status}")

    return title, "\n".join(lines)
```

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/observability/test_templates.py -v`
Expected: ~18 PASS.

- [ ] **Step 5: Run all observability tests**

Run: `uv run pytest tests/observability/ -v --tb=short`
Expected: 45+ PASS total.

- [ ] **Step 6: Ruff**

Run: `uv run ruff check marketpulse/observability/templates.py tests/observability/test_templates.py`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add marketpulse/observability/templates.py tests/observability/test_templates.py
git commit -m "$(cat <<'EOF'
feat(phase-6g-T5): pure templates for critical pushes + tick summary

Implements lock 6g-L10 emoji prefix taxonomy (🛑/⚠️/✅/📊), lock 6g-L11
url=None convention, and lock 6g-L12 formatting:
- Money rendered as `+$N.NN` / `-$N.NN` (sign + 2 decimals)
- Cash balance with thousands separator
- PRICE_UNAVAILABLE attempt counter caps at `4+` in summary
- Empty detail sections omitted; header / status / cash always present
  (heartbeat discipline)

render_critical_event dispatches on event_type for all 8 critical
templates from spec § 4.2; render_tick_summary builds the section-
skipping 📊 push from spec § 4.3.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T6: `paper_tick_notifier.py` — entrypoint

**Locks-Referenced:** 6g-L1 (post-tick only), 6g-L7 (per-tick window only), 6g-L13 (observability → alerts), 6g-L14 (best-effort), 6g-L15 (disabled-path), 6g-L19 (conditional tick_date filter), 6g-L20 (extended kill-switch window).

**Files:**
- Create: `marketpulse/observability/paper_tick_notifier.py`
- Create: `tests/observability/test_paper_tick_notifier.py`

This is the integration site — it queries the audit table, calls the pure projection layer (T3/T4), dispatches via templates (T5) + Notifier, and returns a `NotificationResult` for testability.

- [ ] **Step 1: Write failing tests (10 scenarios)**

Create `tests/observability/test_paper_tick_notifier.py`:

```python
# Layer: stateful
"""6g-T6: notify_paper_tick_events — entrypoint integration tests.

10 scenarios exercise locks 6g-L1, L7, L13, L14, L15, L19, L20.
Uses real SQLite + CapturingNotifier (no mocks)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path):
    from marketpulse.db.base import Base
    eng = create_engine(f"sqlite:///{tmp_path / 'notify.db'}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


@dataclass
class CapturingNotifier:
    sent: list[tuple[str, str, str | None]] = field(default_factory=list)
    return_value: bool = True

    def send(self, title: str, body: str, url: str | None = None) -> bool:
        self.sent.append((title, body, url))
        return self.return_value


class FailingNotifier:
    def send(self, title, body, url=None) -> bool:
        return False


class RaisingNotifier:
    def send(self, title, body, url=None) -> bool:
        raise RuntimeError("transport down")


def _seed_audit(session, *, event_type, timestamp, context=None,
                strategy=None, order_id=None, reason=""):
    from marketpulse.db.models import PaperAuditEvent
    row = PaperAuditEvent(
        timestamp=timestamp, event_type=event_type, order_id=order_id,
        strategy=strategy, reason=reason, context=context or {},
    )
    session.add(row)
    session.flush()
    return row


def _clock(now: datetime):
    from marketpulse.trading.clock import FakeClock
    return FakeClock(now=now)


# === 1. Disabled-path (lock 6g-L15) ===

def test_disabled_path_emits_no_notifications(session, monkeypatch):
    """Lock 6g-L15: MP_PAPER_NOTIFICATIONS_ENABLED=false → no sends.
    Seed audit rows that WOULD trigger critical pushes; assert silence."""
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "false")

    from marketpulse.observability.paper_tick_notifier import (
        NotificationResult, notify_paper_tick_events,
    )
    from marketpulse.trading.repository import Repository

    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(session, event_type="KILL_SWITCH_FLIPPED",
                timestamp=since + timedelta(minutes=10),
                context={"to_state": True, "reason": "drawdown"})
    _seed_audit(session, event_type="ORDER_PLACED",
                timestamp=since + timedelta(minutes=15),
                strategy="momentum",
                context={"ticker": "AAPL", "quantity": 10})

    notifier = CapturingNotifier()
    result = notify_paper_tick_events(
        since=since,
        tick_date=date(2026, 5, 22),
        repository=Repository(session=session),
        notifier=notifier,
        clock=_clock(since + timedelta(minutes=30)),
    )
    assert isinstance(result, NotificationResult)
    assert result.critical_sent == ()
    assert result.summary_sent is False
    assert notifier.sent == []
    assert len(result.failures) == 1
    assert result.failures[0].event_type == "config"
    assert result.failures[0].error == "disabled_by_config"


# === 2. Heartbeat with zero audit rows ===

def test_heartbeat_emits_summary_when_no_audit_rows(session, monkeypatch):
    """Spec § 9.1: zero-activity day still emits a summary push."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    from marketpulse.observability.paper_tick_notifier import (
        notify_paper_tick_events,
    )
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    repo.ensure_initial_deposit(
        amount=Decimal("10000"),
        timestamp=datetime(2026, 5, 22, 18, 0, tzinfo=UTC),
    )
    session.commit()

    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    notifier = CapturingNotifier()
    result = notify_paper_tick_events(
        since=since,
        tick_date=date(2026, 5, 22),
        repository=repo,
        notifier=notifier,
        clock=_clock(since + timedelta(minutes=5)),
    )
    assert result.critical_sent == ()
    assert result.summary_sent is True
    assert len(notifier.sent) == 1
    title, body, url = notifier.sent[0]
    assert title.startswith("📊 Paper Tick")
    assert url is None  # lock 6g-L11


# === 3. Happy path with routine events only ===

def test_happy_path_routine_events_summary_only(session, monkeypatch):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    from marketpulse.observability.paper_tick_notifier import (
        notify_paper_tick_events,
    )
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    repo.ensure_initial_deposit(
        amount=Decimal("10000"),
        timestamp=datetime(2026, 5, 22, 18, 0, tzinfo=UTC),
    )
    session.commit()

    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(session, event_type="ORDER_PLACED",
                timestamp=since + timedelta(minutes=5),
                strategy="momentum",
                context={"ticker": "AAPL", "quantity": 10})
    _seed_audit(session, event_type="ORDER_ENTRY_FILLED",
                timestamp=since + timedelta(minutes=6),
                strategy="momentum",
                context={"ticker": "AAPL", "fill_price": "150.50"})
    _seed_audit(session, event_type="TICK_COMPLETED",
                timestamp=since + timedelta(minutes=10),
                context={"tick_date": "2026-05-22", "status": "completed"})
    session.commit()

    notifier = CapturingNotifier()
    result = notify_paper_tick_events(
        since=since, tick_date=date(2026, 5, 22),
        repository=repo, notifier=notifier,
        clock=_clock(since + timedelta(minutes=15)),
    )
    assert result.critical_sent == ()
    assert result.summary_sent is True
    assert len(notifier.sent) == 1
    _, body, _ = notifier.sent[0]
    assert "AAPL × 10 (momentum)" in body
    assert "AAPL @ 150.50" in body
    assert "Status: completed" in body


# === 4. PRICE_UNAVAILABLE attempt 3 → critical + summary ===

def test_price_unavailable_attempt_3_critical_plus_summary(session, monkeypatch):
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    from marketpulse.observability.paper_tick_notifier import (
        notify_paper_tick_events,
    )
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    repo.ensure_initial_deposit(
        amount=Decimal("10000"),
        timestamp=datetime(2026, 5, 22, 18, 0, tzinfo=UTC),
    )
    session.commit()
    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(session, event_type="PRICE_UNAVAILABLE",
                timestamp=since + timedelta(minutes=3),
                strategy="momentum",
                context={"ticker": "AAPL", "position_id": 42,
                         "attempt_count": 3, "horizon_date": "2026-05-22",
                         "source": "yfinance"})
    _seed_audit(session, event_type="TICK_COMPLETED",
                timestamp=since + timedelta(minutes=5),
                context={"tick_date": "2026-05-22", "status": "completed"})
    session.commit()

    notifier = CapturingNotifier()
    result = notify_paper_tick_events(
        since=since, tick_date=date(2026, 5, 22),
        repository=repo, notifier=notifier,
        clock=_clock(since + timedelta(minutes=10)),
    )
    assert len(result.critical_sent) == 1
    assert result.critical_sent[0].event_type == "PRICE_UNAVAILABLE"
    assert result.summary_sent is True
    assert len(notifier.sent) == 2
    # Critical first, then summary
    assert notifier.sent[0][0] == "⚠️ Position Stuck — AAPL"
    assert notifier.sent[1][0].startswith("📊 Paper Tick")


# === 5. PRICE_UNAVAILABLE attempt 4 → summary only ===

def test_price_unavailable_attempt_4_suppressed(session, monkeypatch):
    """Lock 6g-L4a: ≥ 4 suppressed; only the summary push appears."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    from marketpulse.observability.paper_tick_notifier import (
        notify_paper_tick_events,
    )
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    repo.ensure_initial_deposit(
        amount=Decimal("10000"),
        timestamp=datetime(2026, 5, 22, 18, 0, tzinfo=UTC),
    )
    session.commit()
    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(session, event_type="PRICE_UNAVAILABLE",
                timestamp=since + timedelta(minutes=3),
                context={"ticker": "AAPL", "position_id": 42,
                         "attempt_count": 4})
    _seed_audit(session, event_type="TICK_COMPLETED",
                timestamp=since + timedelta(minutes=5),
                context={"tick_date": "2026-05-22", "status": "completed"})
    session.commit()

    notifier = CapturingNotifier()
    result = notify_paper_tick_events(
        since=since, tick_date=date(2026, 5, 22),
        repository=repo, notifier=notifier,
        clock=_clock(since + timedelta(minutes=10)),
    )
    assert result.critical_sent == ()
    assert result.summary_sent is True
    assert len(notifier.sent) == 1
    assert notifier.sent[0][0].startswith("📊 Paper Tick")


# === 6. POSITION_CLOSED with prior PU → recovery critical ===

def test_position_closed_with_prior_pu_recovery_critical(session, monkeypatch):
    """Lock 6g-L4b: prior PRICE_UNAVAILABLE row outside the current
    window → POSITION_CLOSED is critical (recovery)."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    from marketpulse.observability.paper_tick_notifier import (
        notify_paper_tick_events,
    )
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    repo.ensure_initial_deposit(
        amount=Decimal("10000"),
        timestamp=datetime(2026, 5, 18, 18, 0, tzinfo=UTC),
    )
    session.commit()
    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    # Prior PRICE_UNAVAILABLE — well before `since`, so NOT in current window
    _seed_audit(session, event_type="PRICE_UNAVAILABLE",
                timestamp=since - timedelta(days=2),
                context={"ticker": "AAPL", "position_id": 42,
                         "attempt_count": 1})
    # Recovery close in the current window
    _seed_audit(session, event_type="POSITION_CLOSED",
                timestamp=since + timedelta(minutes=3),
                strategy="momentum",
                context={"ticker": "AAPL", "position_id": 42,
                         "exit_price": "152.10",
                         "realized_pnl": "21.00",
                         "retry_count": 5})
    _seed_audit(session, event_type="TICK_COMPLETED",
                timestamp=since + timedelta(minutes=5),
                context={"tick_date": "2026-05-22", "status": "completed"})
    session.commit()

    notifier = CapturingNotifier()
    result = notify_paper_tick_events(
        since=since, tick_date=date(2026, 5, 22),
        repository=repo, notifier=notifier,
        clock=_clock(since + timedelta(minutes=10)),
    )
    assert len(result.critical_sent) == 1
    assert result.critical_sent[0].event_type == "POSITION_CLOSED"
    assert result.summary_sent is True
    assert notifier.sent[0][0] == "✅ Position Recovered — AAPL"


# === 7. KILL_SWITCH_FLIPPED between ticks (extended window — lock 6g-L20) ===

def test_kill_switch_flipped_between_ticks_picked_up(session, monkeypatch):
    """Lock 6g-L20: an external KILL_SWITCH_FLIPPED row written between
    the last TICK_COMPLETED and this tick's `since` is still notified
    via the extended window."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    from marketpulse.observability.paper_tick_notifier import (
        notify_paper_tick_events,
    )
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    repo.ensure_initial_deposit(
        amount=Decimal("10000"),
        timestamp=datetime(2026, 5, 21, 18, 0, tzinfo=UTC),
    )
    session.commit()
    # Prior tick completed
    prev_tick = datetime(2026, 5, 21, 21, 30, tzinfo=UTC)
    _seed_audit(session, event_type="TICK_COMPLETED",
                timestamp=prev_tick,
                context={"tick_date": "2026-05-21", "status": "completed"})
    # External flip written BETWEEN ticks (e.g. manual CLI noon next day)
    flip_ts = datetime(2026, 5, 22, 16, 0, tzinfo=UTC)
    _seed_audit(session, event_type="KILL_SWITCH_FLIPPED",
                timestamp=flip_ts,
                context={"to_state": True, "reason": "manual"})
    # New tick starts later
    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(session, event_type="TICK_COMPLETED",
                timestamp=since + timedelta(minutes=5),
                context={"tick_date": "2026-05-22", "status": "completed"})
    session.commit()

    notifier = CapturingNotifier()
    result = notify_paper_tick_events(
        since=since, tick_date=date(2026, 5, 22),
        repository=repo, notifier=notifier,
        clock=_clock(since + timedelta(minutes=10)),
    )
    pushed_event_types = [p.event_type for p in result.critical_sent]
    assert "KILL_SWITCH_FLIPPED" in pushed_event_types
    titles = [s[0] for s in notifier.sent]
    assert "🛑 Kill Switch FLIPPED" in titles


# === 8. KILL_SWITCH_CYCLE_SKIPPED dedup (lock 6g-L5) ===

def test_kill_switch_cycle_skipped_dedup(session, monkeypatch):
    """First skip fires; subsequent skip in same active period does not."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    from marketpulse.observability.paper_tick_notifier import (
        notify_paper_tick_events,
    )
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    repo.ensure_initial_deposit(
        amount=Decimal("10000"),
        timestamp=datetime(2026, 5, 21, 18, 0, tzinfo=UTC),
    )
    session.commit()

    # The flip happened on day D-1 (before tick window).
    flip_ts = datetime(2026, 5, 21, 14, 0, tzinfo=UTC)
    _seed_audit(session, event_type="KILL_SWITCH_FLIPPED",
                timestamp=flip_ts,
                context={"to_state": True, "reason": "drawdown"})

    # === Tick D (first skip): the SKIP is INSIDE the window
    since_d = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(session, event_type="KILL_SWITCH_CYCLE_SKIPPED",
                timestamp=since_d + timedelta(minutes=2),
                context={"tick_date": "2026-05-22", "status": "skipped",
                         "reason": "kill_switch_active"})
    session.commit()

    notifier_d = CapturingNotifier()
    result_d = notify_paper_tick_events(
        since=since_d, tick_date=date(2026, 5, 22),
        repository=repo, notifier=notifier_d,
        clock=_clock(since_d + timedelta(minutes=5)),
    )
    assert "KILL_SWITCH_CYCLE_SKIPPED" in result_d.critical_sent

    # === Tick D+1 (subsequent skip): dedup kicks in
    since_d1 = datetime(2026, 5, 23, 21, 0, tzinfo=UTC)
    _seed_audit(session, event_type="KILL_SWITCH_CYCLE_SKIPPED",
                timestamp=since_d1 + timedelta(minutes=2),
                context={"tick_date": "2026-05-23", "status": "skipped",
                         "reason": "kill_switch_active"})
    session.commit()
    notifier_d1 = CapturingNotifier()
    result_d1 = notify_paper_tick_events(
        since=since_d1, tick_date=date(2026, 5, 23),
        repository=repo, notifier=notifier_d1,
        clock=_clock(since_d1 + timedelta(minutes=5)),
    )
    # Critical SKIP suppressed; only summary
    assert "KILL_SWITCH_CYCLE_SKIPPED" not in result_d1.critical_sent
    assert result_d1.summary_sent is True


# === 9. ENGINE_INVARIANT_ERROR (lock 6g-L19 regression) ===

def test_engine_invariant_error_admitted_without_tick_date(session, monkeypatch):
    """Lock 6g-L19: ENGINE_INVARIANT_ERROR's context has no tick_date key
    (phase/order_id/position_id/error/as_of). Must still be admitted via
    time-window-only filter."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    from marketpulse.observability.paper_tick_notifier import (
        notify_paper_tick_events,
    )
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    repo.ensure_initial_deposit(
        amount=Decimal("10000"),
        timestamp=datetime(2026, 5, 22, 18, 0, tzinfo=UTC),
    )
    session.commit()

    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(session, event_type="ENGINE_INVARIANT_ERROR",
                timestamp=since + timedelta(minutes=3),
                context={"phase": "exit_materialization",
                         "order_id": 7, "position_id": 12,
                         "error": "decimal-mismatch",
                         "as_of": "2026-05-22"})
    _seed_audit(session, event_type="TICK_COMPLETED",
                timestamp=since + timedelta(minutes=5),
                context={"tick_date": "2026-05-22", "status": "completed"})
    session.commit()

    notifier = CapturingNotifier()
    result = notify_paper_tick_events(
        since=since, tick_date=date(2026, 5, 22),
        repository=repo, notifier=notifier,
        clock=_clock(since + timedelta(minutes=10)),
    )
    pushed_event_types = [p.event_type for p in result.critical_sent]
    assert "ENGINE_INVARIANT_ERROR" in pushed_event_types
    titles = [s[0] for s in notifier.sent]
    assert "🛑 Engine Invariant Error" in titles


# === 10. Best-effort per-event isolation (lock 6g-L14) ===

def test_notifier_returning_false_is_recorded_and_proceeds(session, monkeypatch):
    """Lock 6g-L14: send() False → NotificationFailure recorded; later
    events still attempted."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    from marketpulse.observability.paper_tick_notifier import (
        notify_paper_tick_events,
    )
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    repo.ensure_initial_deposit(
        amount=Decimal("10000"),
        timestamp=datetime(2026, 5, 22, 18, 0, tzinfo=UTC),
    )
    session.commit()

    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(session, event_type="KILL_SWITCH_FLIPPED",
                timestamp=since + timedelta(minutes=1),
                context={"to_state": True, "reason": "drawdown"})
    _seed_audit(session, event_type="TICK_COMPLETED",
                timestamp=since + timedelta(minutes=5),
                context={"tick_date": "2026-05-22", "status": "completed"})
    session.commit()

    notifier = FailingNotifier()
    result = notify_paper_tick_events(
        since=since, tick_date=date(2026, 5, 22),
        repository=repo, notifier=notifier,
        clock=_clock(since + timedelta(minutes=10)),
    )
    # `critical_sent` records only SUCCESSFUL dispatches (gated by
    # `if sent_ok` in the entrypoint). A FailingNotifier returns False
    # for every call, so critical_sent stays empty AND every attempt
    # produces a NotificationFailure(error="send_returned_false").
    pushed_event_types = [p.event_type for p in result.critical_sent]
    assert "KILL_SWITCH_FLIPPED" not in pushed_event_types, (
        "send returned False → must NOT appear in critical_sent"
    )
    assert len(result.failures) >= 1
    errors = {f.error for f in result.failures}
    assert "send_returned_false" in errors


def test_notifier_raising_does_not_propagate(session, monkeypatch):
    """Lock 6g-L14: notifier.send raising → caught + recorded; no
    propagation to caller."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    from marketpulse.observability.paper_tick_notifier import (
        notify_paper_tick_events,
    )
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    repo.ensure_initial_deposit(
        amount=Decimal("10000"),
        timestamp=datetime(2026, 5, 22, 18, 0, tzinfo=UTC),
    )
    session.commit()

    since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(session, event_type="TICK_COMPLETED",
                timestamp=since + timedelta(minutes=5),
                context={"tick_date": "2026-05-22", "status": "completed"})
    session.commit()

    # Should NOT raise even though every send() raises
    result = notify_paper_tick_events(
        since=since, tick_date=date(2026, 5, 22),
        repository=repo, notifier=RaisingNotifier(),
        clock=_clock(since + timedelta(minutes=10)),
    )
    assert any(f.error.startswith("send_raised:") for f in result.failures)


# === 11. Tick-window isolation (lock 6g-L7) + boundary cases ===

def test_audit_row_at_exact_since_is_included(session, monkeypatch):
    """Boundary: a row whose timestamp == since (the inclusive lower
    bound) IS included. Test pins the closed-on-both-ends semantic of
    the query window."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    get_settings.cache_clear()
    since = datetime(2026, 5, 22, 21, 30, tzinfo=UTC)
    # Seed PRICE_UNAVAILABLE attempt 3 EXACTLY at `since`
    _audit(session, event_type="PRICE_UNAVAILABLE", timestamp=since,
           context={"position_id": 1, "ticker": "AAPL",
                    "attempt_count": 3, "horizon_date": "2026-05-22",
                    "as_of": "2026-05-22", "source": "yfinance",
                    "lookback_days": 10})
    session.flush()
    repo = Repository(session=session)
    notifier = CapturingNotifier()
    result = notify_paper_tick_events(
        since=since, tick_date=date(2026, 5, 22),
        repository=repo, notifier=notifier,
        clock=_clock(since + timedelta(seconds=10)),
    )
    pushed_event_types = [p.event_type for p in result.critical_sent]
    assert "PRICE_UNAVAILABLE" in pushed_event_types, (
        "row at timestamp == since should be included (closed lower bound)"
    )


def test_audit_row_at_exact_notify_started_at_is_included(session, monkeypatch):
    """Boundary: a row whose timestamp == notify_started_at (the upper
    bound) IS included. notify_started_at is captured INSIDE the
    entrypoint via clock.now(), so a row written at exactly that
    instant must land in the window."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    get_settings.cache_clear()
    since = datetime(2026, 5, 22, 21, 30, tzinfo=UTC)
    notify_at = since + timedelta(seconds=10)
    _audit(session, event_type="PRICE_UNAVAILABLE", timestamp=notify_at,
           context={"position_id": 1, "ticker": "AAPL",
                    "attempt_count": 3, "horizon_date": "2026-05-22",
                    "as_of": "2026-05-22", "source": "yfinance",
                    "lookback_days": 10})
    session.flush()
    repo = Repository(session=session)
    notifier = CapturingNotifier()
    result = notify_paper_tick_events(
        since=since, tick_date=date(2026, 5, 22),
        repository=repo, notifier=notifier,
        clock=_clock(notify_at),    # clock.now() returns notify_at exactly
    )
    pushed_event_types = [p.event_type for p in result.critical_sent]
    assert "PRICE_UNAVAILABLE" in pushed_event_types, (
        "row at timestamp == notify_started_at must be included "
        "(closed upper bound)"
    )


def test_audit_row_one_microsecond_before_since_is_excluded(session, monkeypatch):
    """Boundary: a row written one microsecond before `since` is NOT
    in the window — guards against an off-by-one that would double-push
    on tick reprocess."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    get_settings.cache_clear()
    since = datetime(2026, 5, 22, 21, 30, tzinfo=UTC)
    _audit(session, event_type="PRICE_UNAVAILABLE",
           timestamp=since - timedelta(microseconds=1),
           context={"position_id": 1, "ticker": "AAPL",
                    "attempt_count": 3, "horizon_date": "2026-05-22",
                    "as_of": "2026-05-22", "source": "yfinance",
                    "lookback_days": 10})
    session.flush()
    repo = Repository(session=session)
    notifier = CapturingNotifier()
    result = notify_paper_tick_events(
        since=since, tick_date=date(2026, 5, 22),
        repository=repo, notifier=notifier,
        clock=_clock(since + timedelta(seconds=10)),
    )
    assert result.critical_sent == ()


def test_prior_run_audit_rows_filtered_out(session, monkeypatch):
    """Lock 6g-L7: rows with timestamp < since are NOT picked up.
    Re-running the same tick produces no duplicate critical pushes."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    from marketpulse.observability.paper_tick_notifier import (
        notify_paper_tick_events,
    )
    from marketpulse.trading.repository import Repository

    repo = Repository(session=session)
    repo.ensure_initial_deposit(
        amount=Decimal("10000"),
        timestamp=datetime(2026, 5, 22, 16, 0, tzinfo=UTC),
    )
    session.commit()

    # Prior-run rows
    prior_since = datetime(2026, 5, 22, 20, 0, tzinfo=UTC)
    _seed_audit(session, event_type="ENGINE_INVARIANT_ERROR",
                timestamp=prior_since + timedelta(minutes=2),
                context={"phase": "p", "error": "e", "as_of": "2026-05-22"})
    _seed_audit(session, event_type="TICK_COMPLETED",
                timestamp=prior_since + timedelta(minutes=5),
                context={"tick_date": "2026-05-22", "status": "completed_with_errors"})

    # Re-run: new `since` is AFTER the prior rows
    new_since = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
    _seed_audit(session, event_type="TICK_REPROCESSED_COMPLETED",
                timestamp=new_since + timedelta(minutes=3),
                context={"tick_date": "2026-05-22", "status": "completed"})
    session.commit()

    notifier = CapturingNotifier()
    result = notify_paper_tick_events(
        since=new_since, tick_date=date(2026, 5, 22),
        repository=repo, notifier=notifier,
        clock=_clock(new_since + timedelta(minutes=10)),
    )
    # Only the reprocessed-completed should be critical; the prior
    # invariant error is filtered out by `since`.
    assert "TICK_REPROCESSED_COMPLETED" in result.critical_sent
    assert "ENGINE_INVARIANT_ERROR" not in result.critical_sent
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/observability/test_paper_tick_notifier.py -v`
Expected: ModuleNotFoundError on `marketpulse.observability.paper_tick_notifier`.

- [ ] **Step 3: Implement `paper_tick_notifier.py`**

Create `marketpulse/observability/paper_tick_notifier.py`:

```python
"""Phase 6g audit-driven post-tick notification dispatcher (entrypoint).

Glues together repository reads (audit rows + dedup facts + canonical
state) + pure projection (audit_projection.py) + pure renderers
(templates.py) + transport (alerts.notifier.Notifier).

Boundary: this is the ONLY 6g module that touches the repository or
the Notifier. audit_projection.py and templates.py stay pure."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

from marketpulse.alerts.notifier import Notifier
from marketpulse.config import get_settings
from marketpulse.db.models import PaperAuditEvent, PaperPosition
from marketpulse.logging import get_logger
from marketpulse.observability.audit_projection import (
    CriticalEvent,
    NotificationFailure,
    TickSummary,
    select_critical_events,
    summarize_tick,
)
from marketpulse.observability.templates import (
    render_critical_event,
    render_tick_summary,
)
from marketpulse.trading.clock import Clock
from marketpulse.trading.repository import Repository

log = get_logger(__name__)

# Event types whose audit row context carries a "tick_date" key — the
# conditional filter (lock 6g-L19) applies to these only.
_REQUIRES_TICK_DATE = frozenset({
    "TICK_COMPLETED",
    "TICK_REPROCESSED_COMPLETED",
    "KILL_SWITCH_CYCLE_SKIPPED",
})

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class CriticalPush:
    """One critical push successfully dispatched. Carries enough info
    for republish_cli stdout + per-event test assertions WITHOUT
    requiring the test to re-query the audit table.

    Note: the per-event audit_id disambiguates multiple same-event-type
    rows in one tick (e.g., three ENGINE_INVARIANT_ERROR rows produce
    three CriticalPush entries with distinct audit_id values — the
    `critical_sent` tuple of bare event_type strings would not).
    """
    event_type: str
    audit_id: int
    title: str    # the rendered title that was sent


@dataclass(frozen=True)
class NotificationResult:
    """Returned for testability + republish_cli output (spec § 5.2).

    `critical_sent` is a tuple of `CriticalPush` records (NOT bare
    event_type strings) so the operator can distinguish multiple
    same-event-type pushes within one tick — see CriticalPush docstring.
    """
    critical_sent: tuple[CriticalPush, ...]
    summary_sent: bool
    failures: tuple[NotificationFailure, ...]
    summary_title: str | None = None
    summary_body: str | None = None


def _is_enabled() -> bool:
    """Lock 6g-L15: master switch. Reads via `get_settings()` so we
    share the project's single canonical settings source — then calls
    `get_settings.cache_clear()` is the canonical way to refresh after
    a monkeypatch in tests. (Earlier draft used `Settings()` directly
    to avoid cache; that worked but bypassed the project convention.
    Tests now use `monkeypatch.setenv(...)` followed by
    `get_settings.cache_clear()`.)"""
    from marketpulse.config import get_settings
    return get_settings().paper_notifications_enabled


def _query_window_rows(
    repository: Repository, *, since: datetime, until: datetime,
    tick_date: date, latest_tick_completed_at: datetime | None,
) -> list[PaperAuditEvent]:
    """Builds the audit-row list this tick is responsible for.

    Implements lock 6g-L19 (conditional tick_date filter) and lock
    6g-L20 (extended kill-switch window).

    Returns rows ordered by id ASC so downstream loops are deterministic
    and the republish CLI stdout is stable."""
    session = repository._session  # noqa: SLF001 — observability reads only
    ks_lower = latest_tick_completed_at or _EPOCH

    # Engine-written rows in the narrow window.
    narrow = session.execute(
        select(PaperAuditEvent)
        .where(PaperAuditEvent.timestamp >= since)
        .where(PaperAuditEvent.timestamp <= until)
        .order_by(PaperAuditEvent.id)
    ).scalars().all()

    # KILL_SWITCH_FLIPPED rows in the extended between-tick window.
    extended_flips = session.execute(
        select(PaperAuditEvent)
        .where(PaperAuditEvent.event_type == "KILL_SWITCH_FLIPPED")
        .where(PaperAuditEvent.timestamp >= ks_lower)
        .where(PaperAuditEvent.timestamp < since)
        .order_by(PaperAuditEvent.id)
    ).scalars().all()

    # Merge — extended flips precede narrow rows by timestamp (between-tick
    # window ends strictly before `since`); ordering preserved by audit id.
    merged: list[PaperAuditEvent] = list(extended_flips) + list(narrow)

    # Lock 6g-L19: conditional tick_date filter.
    iso = tick_date.isoformat()
    filtered: list[PaperAuditEvent] = []
    for row in merged:
        if row.event_type in _REQUIRES_TICK_DATE:
            if (row.context or {}).get("tick_date") != iso:
                continue
        filtered.append(row)
    return filtered


def _active_positions(repository: Repository, *, threshold: int) -> tuple[
    int, list[tuple[str, int]],
]:
    """Read canonical paper_position table (lock 6g-L21 rule 2).

    Returns:
      active_positions_count: total OPEN positions.
      active_with_pu: list of (ticker, attempt_count_capped) for the
        subset that has ≥ 1 PRICE_UNAVAILABLE audit row in history.
        Attempts are RAW from count_price_unavailable_attempts; the
        template caps display at `4+` (lock 6g-L12)."""
    session = repository._session  # noqa: SLF001
    positions = session.execute(
        select(PaperPosition).where(PaperPosition.status == "OPEN")
    ).scalars().all()
    active_count = len(positions)
    with_pu: list[tuple[str, int]] = []
    for p in positions:
        try:
            n = repository.count_price_unavailable_attempts(position_id=p.id)
        except Exception as exc:  # noqa: BLE001
            log.warning("paper_tick_notify_pu_count_failed",
                        position_id=p.id, error=str(exc))
            n = 0
        if n > 0:
            with_pu.append((p.ticker, int(n)))
    return active_count, with_pu


def _safe_send(
    notifier: Notifier, *, title: str, body: str, url: str | None = None,
    event_type: str, failures: list[NotificationFailure],
) -> bool:
    """Lock 6g-L14: best-effort send. Returns True on success, False on
    any failure path. Appends a NotificationFailure on every non-success
    path so the operator can diagnose without log grepping."""
    try:
        ok = notifier.send(title, body, url)
    except Exception as exc:  # noqa: BLE001
        failures.append(NotificationFailure(
            event_type=event_type, title=title,
            error=f"send_raised:{type(exc).__name__}",
        ))
        log.warning("paper_tick_notify_send_raised",
                    event_type=event_type, error=str(exc))
        return False
    if not ok:
        failures.append(NotificationFailure(
            event_type=event_type, title=title,
            error="send_returned_false",
        ))
        return False
    return True


def _safe_render_critical(
    ev: CriticalEvent, failures: list[NotificationFailure],
) -> tuple[str, str] | None:
    """Lock 6g-L14: per-event template isolation. Returns None on render
    failure; appends a NotificationFailure."""
    try:
        return render_critical_event(ev)
    except Exception as exc:  # noqa: BLE001
        failures.append(NotificationFailure(
            event_type=ev.event_type, title="",
            error=f"template_error:{type(exc).__name__}:{exc}",
        ))
        log.warning("paper_tick_notify_template_failed",
                    event_type=ev.event_type, audit_id=ev.audit_id,
                    error=str(exc))
        return None


def _safe_render_summary(
    summary: TickSummary, failures: list[NotificationFailure],
) -> tuple[str, str] | None:
    try:
        return render_tick_summary(summary)
    except Exception as exc:  # noqa: BLE001
        failures.append(NotificationFailure(
            event_type="tick_summary", title="",
            error=f"template_error:{type(exc).__name__}:{exc}",
        ))
        log.warning("paper_tick_notify_summary_template_failed",
                    error=str(exc))
        return None


def notify_paper_tick_events(
    *,
    since: datetime,
    tick_date: date,
    repository: Repository,
    notifier: Notifier,
    clock: Clock,
    price_unavailable_threshold: int = 3,
) -> NotificationResult:
    """Audit-driven operator notification dispatcher (spec § 6.2).

    Window:
      - Engine-written events: [since, clock.now()]
      - KILL_SWITCH_FLIPPED (lock 6g-L20): [latest_tick_completed_at, since)
        unioned with the narrow window so externally-written flips are
        picked up next tick.

    See module docstring for layering."""
    failures: list[NotificationFailure] = []

    # Lock 6g-L15 disabled path — return early with a single config failure
    # so callers can detect the disabled state without ambiguity.
    if not _is_enabled():
        failures.append(NotificationFailure(
            event_type="config", title="", error="disabled_by_config",
        ))
        return NotificationResult(
            critical_sent=(), summary_sent=False,
            failures=tuple(failures),
        )

    notify_started_at = clock.now()

    # Lock 6g-L20 extended kill-switch window lower bound.
    try:
        latest_tick_completed_at = repository.latest_tick_completed_timestamp(
            before=since,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("paper_tick_notify_latest_tick_query_failed",
                    error=str(exc))
        latest_tick_completed_at = None

    # Query the audit window (lock 6g-L19 conditional filter applied).
    try:
        rows = _query_window_rows(
            repository,
            since=since, until=notify_started_at, tick_date=tick_date,
            latest_tick_completed_at=latest_tick_completed_at,
        )
    except Exception as exc:  # noqa: BLE001
        failures.append(NotificationFailure(
            event_type="audit_query", title="",
            error=f"query_error:{type(exc).__name__}:{exc}",
        ))
        log.warning("paper_tick_notify_query_failed", error=str(exc))
        rows = []

    # Stateless dedup facts.
    try:
        kscs_in_period = (
            repository.kill_switch_cycle_skipped_in_active_period(
                before=notify_started_at,
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("paper_tick_notify_kscs_query_failed", error=str(exc))
        kscs_in_period = False

    # POSITION_CLOSED rows in the window → batch-lookup which have prior PU.
    position_ids_in_window = [
        int((r.context or {}).get("position_id"))
        for r in rows
        if r.event_type == "POSITION_CLOSED"
        and (r.context or {}).get("position_id") is not None
    ]
    if position_ids_in_window:
        # `before` is the LATEST POSITION_CLOSED ts so we cover all rows
        # in one batch; per-row strictness is enforced by the
        # `< before` comparison inside the helper.
        try:
            positions_with_prior_pu = (
                repository.positions_with_prior_price_unavailable(
                    position_ids=position_ids_in_window,
                    before=notify_started_at,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("paper_tick_notify_pwpp_query_failed",
                        error=str(exc))
            positions_with_prior_pu = set()
    else:
        positions_with_prior_pu = set()

    # Lock 6g-L4c GLOBAL monotonic invariant: pull per-position prior
    # max(attempt_count) from history BEFORE this window so the
    # projection's check can detect cross-tick regressions, not just
    # within-batch ordering.
    pu_position_ids_in_window = sorted({
        int((r.context or {}).get("position_id"))
        for r in rows
        if r.event_type == "PRICE_UNAVAILABLE"
        and isinstance((r.context or {}).get("position_id"), int)
    })
    if pu_position_ids_in_window:
        try:
            prior_attempts_by_position = (
                repository.latest_price_unavailable_attempt_counts(
                    position_ids=pu_position_ids_in_window,
                    before=since,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("paper_tick_notify_lpac_query_failed",
                        error=str(exc))
            prior_attempts_by_position = {}
    else:
        prior_attempts_by_position = {}

    # Pure projection. The `failures` list + prior_attempts dict are
    # threaded in so the L4c monotonic-invariant runtime check inside
    # select_critical_events can detect cross-tick regressions without
    # raising.
    try:
        criticals: list[CriticalEvent] = select_critical_events(
            new_audit_rows=rows,
            kill_switch_cycle_skipped_in_period=kscs_in_period,
            positions_with_prior_pu=positions_with_prior_pu,
            threshold=price_unavailable_threshold,
            failures=failures,
            prior_attempts_by_position=prior_attempts_by_position,
        )
    except Exception as exc:  # noqa: BLE001
        failures.append(NotificationFailure(
            event_type="projection", title="",
            error=f"projection_error:{type(exc).__name__}:{exc}",
        ))
        log.warning("paper_tick_notify_projection_failed", error=str(exc))
        criticals = []

    # Dispatch critical pushes (one per event; lock 6g-L14 isolation).
    # Each successful dispatch produces a CriticalPush record so the
    # operator can disambiguate multiple same-event-type rows in one tick.
    critical_sent: list[CriticalPush] = []
    for ev in criticals:
        rendered = _safe_render_critical(ev, failures)
        if rendered is None:
            continue
        title, body = rendered
        # Lock 6g-L11: url=None in MVP.
        sent_ok = _safe_send(
            notifier, title=title, body=body, url=None,
            event_type=ev.event_type, failures=failures,
        )
        if sent_ok:
            critical_sent.append(CriticalPush(
                event_type=ev.event_type, audit_id=ev.audit_id, title=title,
            ))

    # Build + dispatch summary.
    try:
        cash_balance_end = repository.cash_balance()
    except Exception as exc:  # noqa: BLE001
        log.warning("paper_tick_notify_cash_query_failed", error=str(exc))
        cash_balance_end = Decimal("0")

    active_count, active_with_pu = _active_positions(
        repository, threshold=price_unavailable_threshold,
    )

    try:
        summary, summary_failures = summarize_tick(
            new_audit_rows=rows,
            tick_date=tick_date,
            cash_balance_end=cash_balance_end,
            active_positions_with_pu_attempts=active_with_pu,
            active_positions_count=active_count,
        )
    except Exception as exc:  # noqa: BLE001
        failures.append(NotificationFailure(
            event_type="tick_summary", title="",
            error=f"projection_error:{type(exc).__name__}:{exc}",
        ))
        log.warning("paper_tick_notify_summary_projection_failed",
                    error=str(exc))
        return NotificationResult(
            critical_sent=tuple(critical_sent),
            summary_sent=False,
            failures=tuple(failures),
        )
    # summary_failures is a tuple of 0..N NotificationFailure (lock 6g-L21
    # malformed_numeric annotations + missing_tick_completed_row).
    failures.extend(summary_failures)

    rendered_summary = _safe_render_summary(summary, failures)
    summary_sent = False
    summary_title: str | None = None
    summary_body: str | None = None
    if rendered_summary is not None:
        summary_title, summary_body = rendered_summary
        summary_sent = _safe_send(
            notifier,
            title=summary_title, body=summary_body, url=None,
            event_type="tick_summary", failures=failures,
        )

    return NotificationResult(
        critical_sent=tuple(critical_sent),
        summary_sent=summary_sent,
        failures=tuple(failures),
        summary_title=summary_title,
        summary_body=summary_body,
    )
```

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/observability/test_paper_tick_notifier.py -v`
Expected: ~11 PASS.

If a Settings instantiation test fails because `get_settings()` is cached, note the implementation reads via `Settings()` directly inside `_is_enabled()` — no `@lru_cache` interference. Add `monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")` defensively in any test that needs the enabled path.

- [ ] **Step 5: Run full observability + boundary tests**

Run: `uv run pytest tests/observability/ tests/architecture/test_repository_boundary.py -v --tb=short`
Expected: ~56 PASS.

- [ ] **Step 6: Ruff**

Run: `uv run ruff check marketpulse/observability/ tests/observability/`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add marketpulse/observability/paper_tick_notifier.py tests/observability/test_paper_tick_notifier.py
git commit -m "$(cat <<'EOF'
feat(phase-6g-T6): notify_paper_tick_events entrypoint

Audit-driven dispatcher that:
- Queries paper_audit_event in [since, clock.now()] for engine events
  and [latest_tick_completed_at, since) for KILL_SWITCH_FLIPPED only
  (lock 6g-L20 extended window)
- Applies conditional tick_date filter (lock 6g-L19) — required for
  TICK_COMPLETED / TICK_REPROCESSED_COMPLETED / KILL_SWITCH_CYCLE_SKIPPED
  only; ENGINE_INVARIANT_ERROR admitted on time window alone
- Pulls dedup facts (kill_switch_cycle_skipped_in_active_period,
  positions_with_prior_price_unavailable) from Repository
- Reads canonical state (cash_balance, OPEN positions) from Repository,
  NOT from audit context (lock 6g-L21 rule 2)
- Dispatches via render_critical_event + render_tick_summary
- Per-event try/except isolation — notifier.send False → failure
  recorded; send raising → caught + recorded; template/projection
  exceptions caught at the per-event boundary (lock 6g-L14)
- Disabled path (lock 6g-L15): MP_PAPER_NOTIFICATIONS_ENABLED=false
  returns immediately with a single NotificationFailure
  (event_type="config", error="disabled_by_config"), no notifier
  calls issued

Tests: 11 scenarios covering disabled-path, heartbeat, happy path,
PU attempt 3 / 4, recovery, between-tick flip, KSCS dedup, invariant
error context shape, best-effort, and tick-window isolation.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T7: Scheduler hook — `paper_trading_tick_job(*, notifier=None)` + post-tick notify

**Files:**
- Modify: `marketpulse/scheduler/paper_trading_tick.py`
- Create: `tests/trading/test_paper_tick_notifies_after_run.py`

**Locks-Referenced:** 6g-L1 (post-tick hook), 6g-L2 (hybrid dispatch reaches scheduler edge), 6g-L6 (heartbeat summary per tick), 6g-L14 (best-effort, never crash scheduler).

- [ ] **Step 1: Write the failing E2E test**

Create `tests/trading/test_paper_tick_notifies_after_run.py`:

```python
# Layer: stateful
"""6g-T7 E2E: paper_trading_tick_job invokes notify_paper_tick_events
after daily_cycle.run completes. CapturingNotifier asserts the chain."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from marketpulse.db.base import Base
from marketpulse.db.models import PaperAuditEvent


class CapturingNotifier:
    """Test double matching the Notifier Protocol."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str | None]] = []

    def send(self, title: str, body: str, url: str | None = None) -> bool:
        self.sent.append((title, body, url))
        return True


@pytest.fixture
def patched_session_scope(tmp_path, monkeypatch):
    """Patch session_scope to point at a tmp_path DB so the job runs in
    isolation. Yields (eng, capturing_notifier, monkeypatch) so the test
    can pre-seed audit rows + assert post-run notifier state."""
    db_path = tmp_path / "pt.db"
    eng = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(eng)

    # Patch the session_scope generator used by paper_trading_tick_job
    from marketpulse.scheduler import paper_trading_tick as ptt_mod

    def _scope():
        with Session(eng) as s:
            yield s

    monkeypatch.setattr(ptt_mod, "session_scope", _scope)
    yield eng, monkeypatch


def test_paper_trading_tick_job_calls_notifier_with_heartbeat(
    patched_session_scope, monkeypatch,
):
    """Zero audit activity → still emits 1 heartbeat summary push
    (lock 6g-L6)."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    eng, _ = patched_session_scope

    notifier = CapturingNotifier()
    from marketpulse.scheduler.paper_trading_tick import paper_trading_tick_job

    # Should not raise even with empty allocator / risk-gate / etc.
    paper_trading_tick_job(notifier=notifier)

    # Heartbeat: exactly 1 push (📊 summary), even with no activity
    assert len(notifier.sent) >= 1
    titles = [t for (t, _, _) in notifier.sent]
    assert any(t.startswith("📊 Paper Tick") for t in titles), (
        f"expected heartbeat summary push, got titles={titles}"
    )


def test_paper_trading_tick_job_default_notifier_is_settings_driven(
    patched_session_scope, monkeypatch,
):
    """Without passing notifier=..., the job uses get_notifier_from_settings.
    Default settings have NOTIFIER_KIND=none → NoopNotifier → no error."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("NOTIFIER_KIND", "none")
    eng, _ = patched_session_scope

    from marketpulse.scheduler.paper_trading_tick import paper_trading_tick_job

    # Should not raise; Noop notifier swallows everything
    paper_trading_tick_job()


def test_paper_trading_tick_job_signature_compatible_with_apscheduler():
    """APScheduler invokes job callables with no positional args. We
    keep `notifier` keyword-only with a default so the scheduler keeps
    working unchanged. Verify by inspection that:
      - `paper_trading_tick_job` is callable with zero arguments
      - `notifier` is keyword-only with a default of None
    """
    import inspect

    from marketpulse.scheduler.paper_trading_tick import paper_trading_tick_job

    sig = inspect.signature(paper_trading_tick_job)
    # All parameters must be keyword-only with defaults — APScheduler
    # passes no args.
    for name, param in sig.parameters.items():
        assert param.kind in (
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.VAR_KEYWORD,
        ), f"param {name!r} is not keyword-only — would break APScheduler call"
        assert param.default is not inspect.Parameter.empty, (
            f"param {name!r} has no default — would break APScheduler call"
        )

    # `notifier` specifically must exist and default to None
    assert "notifier" in sig.parameters
    assert sig.parameters["notifier"].default is None


def test_paper_trading_tick_job_notify_failure_does_not_propagate(
    patched_session_scope, monkeypatch,
):
    """Lock 6g-L14 best-effort: if notify_paper_tick_events somehow raises
    (unexpected escape past its own try/except), the scheduler job still
    completes successfully."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    eng, _ = patched_session_scope

    # Monkey-patch notify_paper_tick_events to raise — even though the
    # function itself shouldn't, this verifies the outer try/except in
    # the scheduler job.
    from marketpulse.scheduler import paper_trading_tick as ptt_mod

    def _broken(**kwargs):
        raise RuntimeError("simulated notify catastrophe")

    monkeypatch.setattr(ptt_mod, "notify_paper_tick_events", _broken)

    from marketpulse.scheduler.paper_trading_tick import paper_trading_tick_job

    # Job must NOT raise — outer try/except absorbs the catastrophic failure
    paper_trading_tick_job(notifier=CapturingNotifier())
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/trading/test_paper_tick_notifies_after_run.py -v`
Expected: 3 FAIL — `paper_trading_tick_job` doesn't accept `notifier` kwarg yet.

- [ ] **Step 3: Modify `paper_trading_tick_job`**

In `marketpulse/scheduler/paper_trading_tick.py`, locate `def paper_trading_tick_job() -> None:` and update:

```python
def paper_trading_tick_job(
    *,
    notifier: "Notifier | None" = None,
) -> None:
    """Daily 17:30 NY paper-trading tick.

    6g-T7: accepts `notifier` kwarg (test seam, lock 6g-L16). Production
    scheduler calls with no args → defaults to `get_notifier_from_settings`.
    After `daily_cycle.run` returns, dispatches `notify_paper_tick_events`
    best-effort (lock 6g-L14) — translator/notifier failures are logged
    but never crash the job.
    """
    from marketpulse.alerts.notifier import get_notifier_from_settings
    from marketpulse.config import get_settings
    from marketpulse.observability.paper_tick_notifier import (
        notify_paper_tick_events,
    )

    settings = get_settings()
    if notifier is None:
        notifier = get_notifier_from_settings(settings)

    gen = session_scope()
    session = next(gen)
    try:
        clock = WallClock()
        calendar = NYTradingCalendar()
        # ... existing setup (unchanged) ...
        # KEEP all existing wiring up to and including:
        #   tick_started_at = clock.now()                  # NEW LINE (lock 6g-L1 window floor)
        #   result = daily_cycle.run(...)
        # ... existing handling of result ...

        # NEW — 6g hook, best-effort, never raises (lock 6g-L14 belt-
        # and-braces with the inner try/except in notify_paper_tick_events).
        try:
            notify_paper_tick_events(
                since=tick_started_at,
                tick_date=result.tick_date,
                repository=repository,
                notifier=notifier,
                clock=clock,
            )
        except Exception as exc:  # pragma: no cover — belt-and-braces
            log.warning("paper_tick_notify_failed", error=str(exc))
    finally:
        try:
            next(gen)
        except StopIteration:
            pass
```

**Implementation note:** the EXACT existing code in `paper_trading_tick_job` (everything between `try:` after `next(gen)` and the `finally:` block) stays UNCHANGED except:

1. Add `tick_started_at = clock.now()` immediately BEFORE `result = daily_cycle.run(...)`.
2. Add the new 6g try/except block immediately AFTER `daily_cycle.run` returns and any existing post-run handling.

Open the file, read the existing body carefully, and inject the two additions in the correct positions. Do NOT rewrite the entire function — that risks breaking the existing 6a/6b/6b+ scheduler wiring.

- [ ] **Step 4: Run E2E test to verify passing**

Run: `uv run pytest tests/trading/test_paper_tick_notifies_after_run.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Run pre-existing scheduler tests to confirm no regression**

Run: `uv run pytest tests/trading/test_scheduler.py -v`
Expected: ALL pre-existing tests pass (the `notifier` kwarg has a default, so old callers still work).

- [ ] **Step 6: Run full trading suite**

Run: `uv run pytest -q tests/trading/ tests/observability/ tests/architecture/ --tb=no | tail -3`
Expected: ALL pass.

- [ ] **Step 7: Ruff**

Run: `uv run ruff check marketpulse/scheduler/ tests/trading/test_paper_tick_notifies_after_run.py`
Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add marketpulse/scheduler/paper_trading_tick.py tests/trading/test_paper_tick_notifies_after_run.py
git commit -m "$(cat <<'EOF'
feat(phase-6g-T7): scheduler hook — best-effort post-tick notifier dispatch

paper_trading_tick_job(*, notifier=None) accepts an injected Notifier
for tests; production defaults to get_notifier_from_settings(get_settings()).

Records tick_started_at=clock.now() immediately before daily_cycle.run,
then after it returns calls notify_paper_tick_events(...) wrapped in
a best-effort try/except (lock 6g-L14 belt-and-braces with the inner
exception isolation in notify_paper_tick_events). Translator/notifier
failures are logged via log.warning("paper_tick_notify_failed") but
never propagate to APScheduler.

Implements lock 6g-L1 (notifications only after daily_cycle.run
completes) + lock 6g-L6 (one routine summary per tick — heartbeat
emitted even on zero-activity days).

E2E test (3 scenarios): heartbeat with zero activity, default
settings-driven notifier path, catastrophic notify failure absorbed.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T8: `republish_cli.py` — disaster recovery CLI

**Files:**
- Create: `marketpulse/observability/republish_cli.py`
- Create: `tests/observability/test_republish_cli.py`

**Locks-Referenced:** 6g-L8 (operator-triggered replay only), 6g-L18 (refuses to run when disabled).

- [ ] **Step 1: Write the failing test**

Create `tests/observability/test_republish_cli.py`:

```python
# Layer: stateful
"""6g-T8: republish_cli — operator-triggered replay (lock 6g-L8 / L18).

Tests use in-process `main(argv)` to avoid subprocess overhead.
"""

from __future__ import annotations

import io
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from marketpulse.db.base import Base
from marketpulse.db.models import PaperAuditEvent


class CapturingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str | None]] = []

    def send(self, title, body, url=None) -> bool:
        self.sent.append((title, body, url))
        return True


@pytest.fixture
def patched_db(tmp_path, monkeypatch):
    db_path = tmp_path / "rp.db"
    eng = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(eng)

    # CLI uses session_scope from marketpulse.db.base
    from marketpulse.db import base as base_mod

    def _scope():
        with Session(eng) as s:
            yield s

    monkeypatch.setattr(base_mod, "session_scope", _scope)
    yield eng, monkeypatch


def test_republish_cli_refuses_when_disabled(patched_db, monkeypatch, capsys):
    """Lock 6g-L18: MP_PAPER_NOTIFICATIONS_ENABLED=false → exit 1, no sends."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "false")

    from marketpulse.observability.republish_cli import main

    exit_code = main(["--date", "2026-05-22"])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "MP_PAPER_NOTIFICATIONS_ENABLED" in captured.err or \
           "disabled" in captured.err.lower()


def test_republish_cli_runs_with_provided_notifier(patched_db, monkeypatch, capsys):
    """Happy path: seed a fill audit row for 2026-05-22, run CLI →
    summary push captured + stdout contains it."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    eng, _ = patched_db

    # Seed an ORDER_ENTRY_FILLED on the target date
    ts = datetime(2026, 5, 22, 21, 30, tzinfo=UTC)
    with Session(eng) as s:
        s.add(PaperAuditEvent(
            timestamp=ts, event_type="ORDER_ENTRY_FILLED",
            order_id=1, strategy="momentum", reason="",
            context={
                "ticker": "AAPL",
                "fill_price": "155.500000",
                "cash_balance_after": "9844.50",
            },
        ))
        s.commit()

    notifier = CapturingNotifier()
    from marketpulse.observability import republish_cli as rcli_mod

    # Patch the CLI's notifier factory to inject our capturing instance
    monkeypatch.setattr(
        rcli_mod, "get_notifier_from_settings", lambda settings: notifier,
    )

    exit_code = rcli_mod.main(["--date", "2026-05-22"])

    assert exit_code == 0
    # Notifier received at least the summary push
    titles = [t for (t, _, _) in notifier.sent]
    assert any(t.startswith("📊 Paper Tick") for t in titles), \
        f"expected summary push, got titles={titles}"

    captured = capsys.readouterr()
    # Stdout summary preview per §5.2: title + 200-char body preview
    assert "📊 Paper Tick" in captured.out


def test_republish_cli_rejects_invalid_date(patched_db, monkeypatch, capsys):
    """Argparse type=date.fromisoformat → invalid date exits with nonzero."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")

    from marketpulse.observability.republish_cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(["--date", "not-a-date"])
    assert excinfo.value.code != 0


def test_republish_cli_failure_exit_code(patched_db, monkeypatch, capsys):
    """If notifier.send returns False for any push, exit code is 1."""
    monkeypatch.setenv("MP_PAPER_NOTIFICATIONS_ENABLED", "true")
    eng, _ = patched_db

    ts = datetime(2026, 5, 22, 21, 30, tzinfo=UTC)
    with Session(eng) as s:
        s.add(PaperAuditEvent(
            timestamp=ts, event_type="ORDER_PLACED",
            order_id=1, strategy="momentum", reason="",
            context={"ticker": "AAPL", "quantity": 10},
        ))
        s.commit()

    class FailingNotifier:
        def __init__(self) -> None:
            self.sent: list = []

        def send(self, title, body, url=None) -> bool:
            self.sent.append((title, body, url))
            return False  # transport failure

    from marketpulse.observability import republish_cli as rcli_mod
    monkeypatch.setattr(
        rcli_mod, "get_notifier_from_settings", lambda settings: FailingNotifier(),
    )

    exit_code = rcli_mod.main(["--date", "2026-05-22"])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "failure" in captured.out.lower() or \
           "send_returned_false" in captured.out
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/observability/test_republish_cli.py -v`
Expected: 4 FAIL — `republish_cli` module doesn't exist yet.

- [ ] **Step 3: Implement `republish_cli.py`**

Create `marketpulse/observability/republish_cli.py`:

```python
"""6g-T8: operator-triggered replay CLI (lock 6g-L8 / L18).

    uv run python -m marketpulse.observability.republish_cli --date YYYY-MM-DD

Sets `since = start_of_day_utc(--date)` and runs `notify_paper_tick_events`
against that window. Refuses to execute when MP_PAPER_NOTIFICATIONS_ENABLED
is false (exit 1) to avoid the failure mode "operator thinks they republished
but the disabled flag swallowed all sends".

Stdout format per spec §5.2 in this exact order:
  1. `critical_sent`: one `pushed: <EVENT_TYPE>` line per event_type
  2. `summary_title` + 200-char-truncated `summary_body` preview
  3. `failures`: one `failure: <event_type> :: <title> :: <error>` line per
     NotificationFailure

Exit code: 0 iff failures == (); 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime

from marketpulse.alerts.notifier import get_notifier_from_settings
from marketpulse.config import get_settings
from marketpulse.db.base import session_scope
from marketpulse.observability.paper_tick_notifier import (
    NotificationResult,
    notify_paper_tick_events,
)
from marketpulse.trading.clock import WallClock
from marketpulse.trading.repository import Repository

_BODY_PREVIEW_LIMIT = 200


def _format_body_preview(body: str | None) -> str:
    if body is None:
        return "(no body)"
    if len(body) <= _BODY_PREVIEW_LIMIT:
        return body
    return body[:_BODY_PREVIEW_LIMIT] + "…"


def _print_result(result: NotificationResult) -> None:
    """Format NotificationResult to stdout per spec §5.2."""
    # 1. critical_sent — each entry is a CriticalPush record
    if result.critical_sent:
        for push in result.critical_sent:
            print(f"pushed: {push.event_type} (audit_id={push.audit_id}) :: {push.title}")
    else:
        print("pushed: (none)")

    # 2. summary_title + body preview
    if result.summary_sent and result.summary_title:
        print(f"summary_title: {result.summary_title}")
        print(f"summary_body_preview: {_format_body_preview(result.summary_body)}")
    elif not result.summary_sent:
        print("summary: (not emitted)")

    # 3. failures
    if result.failures:
        for f in result.failures:
            print(f"failure: {f.event_type} :: {f.title} :: {f.error}")
    else:
        print("failures: (none)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="republish_cli",
        description="Republish 6g notifications for a historical paper-trading tick "
                    "(operator-triggered disaster recovery only — lock 6g-L8).",
    )
    parser.add_argument(
        "--date",
        required=True,
        type=date.fromisoformat,
        help="Target tick date (ISO format: YYYY-MM-DD)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.paper_notifications_enabled:
        # Lock 6g-L18: refuse silently-pretending-to-work
        print(
            "ERROR: MP_PAPER_NOTIFICATIONS_ENABLED=false. "
            "Cannot republish notifications when the feature is disabled. "
            "Enable the flag and re-run.",
            file=sys.stderr,
        )
        return 1

    tick_date: date = args.date
    since = datetime.combine(tick_date, datetime.min.time(), tzinfo=UTC)

    notifier = get_notifier_from_settings(settings)
    clock = WallClock()

    gen = session_scope()
    session = next(gen)
    try:
        repository = Repository(session=session)
        result = notify_paper_tick_events(
            since=since,
            tick_date=tick_date,
            repository=repository,
            notifier=notifier,
            clock=clock,
        )
    finally:
        try:
            next(gen)
        except StopIteration:
            pass

    _print_result(result)

    # Exit code: 0 if no failures, 1 otherwise
    return 0 if not result.failures else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/observability/test_republish_cli.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Smoke the CLI**

Run: `uv run python -m marketpulse.observability.republish_cli --help`
Expected: argparse help output mentioning `--date YYYY-MM-DD` and lock 6g-L8.

- [ ] **Step 6: Ruff**

Run: `uv run ruff check marketpulse/observability/republish_cli.py tests/observability/test_republish_cli.py`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add marketpulse/observability/republish_cli.py tests/observability/test_republish_cli.py
git commit -m "$(cat <<'EOF'
feat(phase-6g-T8): republish_cli — operator-triggered replay

argparse CLI invoked as:
    uv run python -m marketpulse.observability.republish_cli --date YYYY-MM-DD

Sets since=start_of_day_utc(date) and dispatches notify_paper_tick_events
on the canonical window. Refuses to run when
MP_PAPER_NOTIFICATIONS_ENABLED=false (lock 6g-L18) and exits 1 with
stderr message — prevents the failure mode "thought I republished but
disabled flag swallowed everything".

Stdout format per spec §5.2:
  pushed: <EVENT_TYPE>  (or "pushed: (none)")
  summary_title: <title>
  summary_body_preview: <body truncated to 200 chars + ellipsis>
  failure: <event_type> :: <title> :: <error>  (per failure)

Exit code: 0 iff result.failures == (), else 1.

Implements lock 6g-L8 (operator-triggered only — never automatic replay).
Tests: 4 scenarios (disabled refusal, happy path, invalid date arg,
failure exit code).

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task T9: Final integration + PR

**Files:** none new; verification + PR.

**Locks-Referenced:** all 21 numbered locks + 3 L4 sublocks = 23 entries (final coverage check). Test strategy itself satisfies lock 6g-L16 (4-layer test plan: pure projection, templates, notifier entrypoint with seeded DB, scheduler E2E).

- [ ] **Step 1: Full test suite green**

Run: `uv run pytest -q --tb=no | tail -3`
Expected: ALL pass. Total tests: baseline (~1163) + 6g additions:
- T1: ~6 settings/factory tests
- T2: ~10 repository helper tests
- T3: ~15 critical-event projection tests
- T4: ~8 summary projection tests
- T5: ~20 template tests
- T6: ~11 notifier entrypoint tests
- T7: ~3 scheduler E2E tests
- T8: ~4 CLI tests

Expected total: ~1240 passed, 0 failed.

If anything red, identify which task introduced the regression and fix before T9 PR.

- [ ] **Step 2: Ruff clean**

Run: `uv run ruff check marketpulse/ tests/`
Expected: `All checks passed!`

- [ ] **Step 3: Alembic head still 0011**

Run: `uv run alembic heads`
Expected: `0011 (head)`. 6g adds NO migration — verify head did not move.

- [ ] **Step 4: Smoke imports**

Run:
```bash
uv run python -c "
from marketpulse.observability.paper_tick_notifier import (
    notify_paper_tick_events, NotificationResult,
)
from marketpulse.observability.audit_projection import (
    CriticalEvent, TickSummary, PlacedOrderDetail, NotificationFailure,
    select_critical_events, summarize_tick,
)
from marketpulse.observability.templates import (
    render_critical_event, render_tick_summary,
)
from marketpulse.observability.republish_cli import main as republish_main
from marketpulse.alerts.notifier import get_notifier_from_settings
from marketpulse.config import Settings
s = Settings(MP_PAPER_NOTIFICATIONS_ENABLED='true')
assert s.paper_notifications_enabled is True
print('All 6g imports OK')
"
```
Expected: `All 6g imports OK`.

- [ ] **Step 5: CLI help smoke**

Run: `uv run python -m marketpulse.observability.republish_cli --help`
Expected: argparse help; mentions `--date YYYY-MM-DD`.

- [ ] **Step 6: Forward-invariant grep (port from 6b+T8 pattern)**

Run (`grep --quiet` returns 0 on match, 1 on no-match; we want no-match so we use `! grep -q`):

```bash
set -e
status=0
if grep -rq "write_audit_event" marketpulse/observability/; then
    echo "FAIL: observability writes audit (6g-L1 violation)"
    grep -rn "write_audit_event" marketpulse/observability/
    status=1
fi
if grep -rqE "session\.add|session\.commit" marketpulse/observability/; then
    echo "FAIL: observability mutates DB (6g-L1 violation)"
    grep -rnE "session\.add|session\.commit" marketpulse/observability/
    status=1
fi
if grep -rq "from marketpulse.observability" marketpulse/alerts/; then
    echo "FAIL: alerts depends on observability (6g-L13 reverse-direction violation)"
    grep -rn "from marketpulse.observability" marketpulse/alerts/
    status=1
fi
if [ "$status" -eq 0 ]; then
    echo "Forward-invariant grep: PASS"
fi
exit "$status"
```

Expected: `Forward-invariant grep: PASS` and exit code 0.

Why this shape (not `grep ... && echo FAIL`): bare `grep && echo` returns nonzero from `grep` on no-match, and under `set -e` (or strict CI shells) that exits the script before the failure-check completes — masking the absence of violations as a failure. The `if grep -q` form inverts cleanly: match → enter the failure branch; no-match → fall through.

This enforces:
- Lock 6g-L1: `observability/` is consumer; never writes audit rows.
- Lock 6g-L13: dependency direction `observability → alerts`, never reverse.

- [ ] **Step 7: Lock coverage verification**

Run:
```bash
PLAN=docs/superpowers/plans/2026-05-22-phase-6g-observability.md
for lock in L1 L2 L3 L4a L4b L4c L5 L6 L7 L8 L9 L10 L11 L12 L13 L14 L15 L16 L17 L18 L19 L20 L21; do
    count=$(grep -c "6g-$lock\b" "$PLAN")
    if [ "$count" -eq 0 ]; then echo "MISSING: 6g-$lock"; fi
done
```
Expected: no MISSING output (all 21 numbered locks + 3 L4 sublocks = 23 lock entries referenced at least once in plan tasks).

- [ ] **Step 8: Working tree clean**

Run: `git status --short`
Expected: empty output. All T1..T8 commits in place; no untracked or staged residue.

- [ ] **Step 9: Push branch + create PR**

```bash
git push -u origin plan/phase-6g-observability
gh pr create --title "feat(phase-6g): observability + alerting (operator notifications)" --body "$(cat <<'EOF'
## Summary

Wires the existing `paper_audit_event` stream into operator push notifications via a strict consumer projection. 6g is a **post-tick hook** — audit-write code paths in `repository.py` are unchanged (lock 6g-L1). No new trading state, no new tables, no watermark.

- **Spec:** `docs/superpowers/specs/2026-05-22-phase-6g-observability-design.md`
- **Plan:** `docs/superpowers/plans/2026-05-22-phase-6g-observability.md`
- **Locks:** 23 (6g-L1 .. 6g-L21 incl. L4a/L4b/L4c)
- **Migration:** None (alembic head stays 0011)

## Key invariants

- `notify_paper_tick_events(since=tick_started_at, tick_date, repository, notifier, clock)` runs **after** `daily_cycle.run()` completes (lock 6g-L1). Best-effort: failures recorded in `NotificationResult.failures`, never propagated (lock 6g-L14).
- **Hybrid taxonomy** (lock 6g-L2): critical audit events emit standalone pushes (🛑 / ⚠️ / ✅ prefixes); routine activity is summarized in one `📊 Paper Tick YYYY-MM-DD` per tick (heartbeat — emitted even on zero-activity days when enabled).
- **PRICE_UNAVAILABLE attempt 3** standalone push exactly once at threshold (lock 6g-L4a); recovery push when a `POSITION_CLOSED` row has prior PU history (lock 6g-L4b). Both pure audit projections — no new state.
- **KILL_SWITCH_CYCLE_SKIPPED dedup** per active period (lock 6g-L5). KILL_SWITCH_FLIPPED uses asymmetric extended window `[latest_tick_completed_at, notify_started_at]` so externally-triggered between-tick flips are picked up (lock 6g-L20).
- **Conditional tick_date filter** (lock 6g-L19): TICK_COMPLETED / TICK_REPROCESSED_COMPLETED / KILL_SWITCH_CYCLE_SKIPPED MUST match `context["tick_date"]`; all other events admitted on time window alone.
- **No coalescing** within a single tick (§3.1): three ENGINE_INVARIANT_ERROR rows → three pushes.
- **Disabled path** (lock 6g-L15): `MP_PAPER_NOTIFICATIONS_ENABLED=false` → no sends, `critical_sent=()`, `summary_sent=False`, single `NotificationFailure(error="disabled_by_config")`.
- **Disaster recovery** via explicit CLI (lock 6g-L8): `python -m marketpulse.observability.republish_cli --date YYYY-MM-DD`. Refuses to run when feature flag is false (lock 6g-L18).

## Architecture

```
marketpulse/observability/
  audit_projection.py        # pure: CriticalEvent / TickSummary / select_critical_events / summarize_tick
  templates.py               # pure: render_critical_event / render_tick_summary
  paper_tick_notifier.py     # entrypoint with window math + dedup + dispatch
  republish_cli.py           # argparse CLI for operator-triggered replay

marketpulse/alerts/notifier.py       # +get_notifier_from_settings (thin wrapper)
marketpulse/config.py                # +MP_PAPER_NOTIFICATIONS_ENABLED
marketpulse/trading/repository.py    # +3 read-only audit-projection helpers
marketpulse/scheduler/paper_trading_tick.py  # +notifier kwarg + post-tick hook
```

## Test plan

- [x] `uv run pytest` — all pass (~1240 tests; +77 from 6g layers)
- [x] `uv run ruff check marketpulse/ tests/` — clean
- [x] `uv run alembic heads` — `0011 (head)` (no migration added)
- [x] Forward-invariant grep: `observability/` does not write audit; `alerts/` does not import from `observability/` (lock 6g-L13)
- [x] 4-layer tests (lock 6g-L16): pure projection / templates / notifier entrypoint with seeded DB / scheduler E2E
- [ ] Post-merge: deploy to NAS, verify next 17:30 NY tick produces exactly one `📊 Paper Tick` push containing real fills/rejects/PnL (or a zero-activity heartbeat if no orders)
- [ ] Post-merge: trigger a deliberate `daily_loss` reject; confirm operator gets `🛑 Daily Loss Limit Tripped` standalone push
- [ ] Post-merge: trigger a between-tick kill-switch flip via CLI; confirm next-tick standalone push lands

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 10: Done**

Hand off to `superpowers:finishing-a-development-branch` to drive merge.

---

## Self-Review (run before T0)

### Spec coverage

| Lock | Covered by task |
|---|---|
| 6g-L1 | T6 (audit-write code path untouched), T7 (post-tick hook contract) |
| 6g-L2 | T3 + T4 (hybrid: critical standalone via select_critical_events; routine summary via summarize_tick), T7 (dispatch reaches both arms) |
| 6g-L3 | T3 (ORDER_REJECTED standalone iff daily_loss in failed_gates) |
| 6g-L4a | T3 (PRICE_UNAVAILABLE attempt_count==3 exactly) |
| 6g-L4b | T3 (recovery push uses positions_with_prior_price_unavailable from T2) |
| 6g-L4c | T3 (invariant comment cites attempt_count monotonicity assumption from 6b+T6/T7) |
| 6g-L5 | T2 (kill_switch_cycle_skipped_in_active_period helper), T3 (dedup gate in select_critical_events) |
| 6g-L6 | T6 (heartbeat: 1 summary per tick when enabled), T7 (E2E asserts heartbeat) |
| 6g-L7 | T6 (window=[since, notify_started_at]; reprocess doesn't replay since `tick_started_at` advances) |
| 6g-L8 | T8 (republish_cli) |
| 6g-L9 | T3 (TICK_REPROCESSED_COMPLETED critical) |
| 6g-L10 | T5 (emoji prefix taxonomy in templates) |
| 6g-L11 | T5 (url=None — no deep links in MVP) |
| 6g-L12 | T5 (compact + section-skipping + truncation + sign+2dp) |
| 6g-L13 | T6 entrypoint imports from `alerts.Notifier`; T9 forward-invariant grep enforces no reverse-direction imports |
| 6g-L14 | T6 (best-effort: NotificationFailure records, never raises), T7 (outer try/except belt-and-braces) |
| 6g-L15 | T1 (settings flag), T6 (disabled-path early return), T8 (CLI refuses when disabled) |
| 6g-L16 | This plan IS the 4-layer test strategy: T3+T4 pure projection, T5 templates, T6 notifier entrypoint with seeded DB, T7 scheduler E2E |
| 6g-L17 | T2 (positions_with_prior_price_unavailable batch helper), T6 (calls per-tick) |
| 6g-L18 | T8 (refuses execution with stderr + exit 1) |
| 6g-L19 | T6 (conditional tick_date filter logic) |
| 6g-L20 | T2 (latest_tick_completed_timestamp helper), T6 (asymmetric extended window for KILL_SWITCH_FLIPPED) |
| 6g-L21 | T4 (summarize_tick TICK_COMPLETED → KSCS → "unknown" fallback; cash/positions from canonical tables) |

All **21 numbered locks + 3 L4 sublocks = 23 entries** accounted for.

### Placeholder scan

After writing this plan, run:

```bash
grep -nE "TODO|TBD|XXX|FIXME|implement later|similar to" docs/superpowers/plans/2026-05-22-phase-6g-observability.md
```

Expected: empty.

### Type consistency

- `NotificationFailure` declared once in T3, imported by T6/T8.
- `select_critical_events(*, new_audit_rows, kill_switch_cycle_skipped_in_period, positions_with_prior_pu, threshold=3, failures=None)` — T3 declares, T6 imports + calls with these exact kwarg names. The optional `failures` kwarg lets T6 receive lock-6g-L4c monotonic-invariant violation records.
- `summarize_tick(*, new_audit_rows, tick_date, cash_balance_end, active_positions_with_pu_attempts, active_positions_count) -> tuple[TickSummary, tuple[NotificationFailure, ...]]` — T4 declares, T6 calls. (The failure tuple is empty when both `_resolve_cycle_status` and all `_safe_decimal` coercions succeed; non-empty when missing TICK_COMPLETED or malformed numeric fields surface.)
- `render_critical_event(event: CriticalEvent) -> tuple[str, str]` and `render_tick_summary(summary: TickSummary) -> tuple[str, str]` — T5 declares, T6 imports.
- `notify_paper_tick_events(*, since, tick_date, repository, notifier, clock, price_unavailable_threshold=3) -> NotificationResult` — T6 declares, T7 (scheduler hook) + T8 (CLI) call.
- `get_notifier_from_settings(settings) -> Notifier` — T1 declares, T7 + T8 call.

All function signatures cross-checked. No drift.
