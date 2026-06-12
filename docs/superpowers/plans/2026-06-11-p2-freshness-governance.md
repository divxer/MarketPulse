# P2 Freshness Governance (PR1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the NAV/north-star pipeline structurally unable to consume intraday
(provisional) price bars; heal and rebuild the two contaminated snapshots.

**Architecture:** Add `is_final`/`finalized_at` to `price_cache` (Alembic + Python backfill);
compute finality at write time in `PriceCache.upsert`; a FinalizeJob refreshes provisional bars
as the structural step BEFORE the NAV snapshot inside the paper-trading tick; the NAV price
lookup filters `is_final == true` in both subquery and join and emits fallback diagnostics;
a one-off CLI rebuilds 2026-06-10/06-11 with `rebuild_reason='provisional_price_cache_fix'`.

**Tech stack:** Python 3.12, SQLAlchemy 2.x, Alembic (SQLite batch mode), pytest.
Tests: `uv run pytest`. Lint: `uv run ruff check`. Every test file starts with a `# Layer:` tag.

**Spec:** `docs/superpowers/specs/2026-06-11-p2-freshness-governance-design.md` (locked).
**Branch:** `fix/p2-freshness-is-final` (already created; spec committed on it).

Key existing facts (verified against source — do not rediscover):
- `PriceCacheEntry` is at `marketpulse/db/models.py:206` (PK `ticker, date`; has `fetched_at`).
- `PriceCache.upsert` is at `marketpulse/data/cache.py:15` (sqlite `on_conflict_do_update`).
- `_read_price_lookup` is at `marketpulse/portfolio/snapshot_runner.py:83`; its only caller is
  `run_nav_snapshot` in the same file. Its docstring premise ("yfinance only publishes
  COMPLETED daily bars, so the current day's close is not in the cache") is FALSE — fix it.
- The NAV mount point is `marketpulse/scheduler/paper_trading_tick.py:118`
  (`_run_nav_snapshot_safely(session, tick_date=result.tick_date)`).
- `YFinanceClient.fetch_history_range(ticker, *, start, end)` — **`end` is EXCLUSIVE**
  (`marketpulse/data/yfinance_client.py:133`). To include today, pass `end=today + 1 day`.
- `NYTradingCalendar` (`marketpulse/trading/calendar.py`) has `is_business_day`,
  `today_ny_trading_date(now_utc)`; NO previous-business-day helper and NO half-day knowledge.
- Alembic head is `83cf7ac9e055`. Migrations use `op.batch_alter_table` (SQLite).
- `PaperNavSnapshot` (`marketpulse/db/models.py:489`) has `is_rebuilt` + `rebuild_reason`;
  Lock L1 says "normal flow is INSERT only; admin path sets is_rebuilt + reason" — the rebuild
  CLI IS that admin path.
- `run_nav_snapshot` is idempotent: it RETURNS the existing row without recomputing — the
  rebuild CLI must DELETE the row first.
- CLI convention: `marketpulse/cli/refresh_sectors.py` (`# Layer: cli`, `main()`,
  `python -m marketpulse.cli.<name>`). The spec's `marketpulse/jobs/` path is superseded by
  this repo convention (Task 8 updates the spec's file list).
- Log style: `marketpulse/data/*` and `scheduler/jobs.py` use
  `from marketpulse.logging import get_logger` with structured kwargs;
  `snapshot_runner.py` uses stdlib `logging` with `extra={}`;
  `paper_trading_tick.py` uses %-style args. Match each file's existing style.
- SQLite stores `fetched_at` as naive-UTC text (e.g. `2026-06-11 14:24:12.296942`) and `date`
  as `YYYY-MM-DD` text. Treat naive timestamps as UTC.

---

### Task 1: Finality rule helper

**Files:**
- Create: `marketpulse/data/finality.py`
- Test: `tests/data/test_finality.py`

- [ ] **Step 1: Write the failing tests**

```python
# Layer: unit
"""Finality rule — spec §2: final iff fetched >= 16:05 America/New_York on bar date."""
from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from marketpulse.data.finality import FINAL_CUTOFF_NY, is_bar_final

NY = ZoneInfo("America/New_York")


def test_intraday_fetch_is_provisional_edt():
    # 2026-06-10 is EDT (UTC-4): 12:30 ET == 16:30 UTC — before 16:05 ET.
    assert is_bar_final(date(2026, 6, 10), datetime(2026, 6, 10, 16, 30, tzinfo=UTC)) is False


def test_after_close_fetch_is_final_edt():
    # 17:30 ET == 21:30 UTC on an EDT date.
    assert is_bar_final(date(2026, 6, 10), datetime(2026, 6, 10, 21, 30, tzinfo=UTC)) is True


def test_exactly_at_cutoff_is_final():
    cutoff_utc = datetime(2026, 6, 10, 16, 5, tzinfo=NY).astimezone(UTC)
    assert is_bar_final(date(2026, 6, 10), cutoff_utc) is True


def test_est_winter_date_cutoff():
    # 2026-01-15 is EST (UTC-5): 16:05 ET == 21:05 UTC.
    assert is_bar_final(date(2026, 1, 15), datetime(2026, 1, 15, 21, 4, tzinfo=UTC)) is False
    assert is_bar_final(date(2026, 1, 15), datetime(2026, 1, 15, 21, 6, tzinfo=UTC)) is True


def test_naive_datetime_treated_as_utc():
    # SQLite round-trips naive timestamps; rule must treat them as UTC.
    assert is_bar_final(date(2026, 6, 10), datetime(2026, 6, 10, 16, 30)) is False
    assert is_bar_final(date(2026, 6, 10), datetime(2026, 6, 10, 21, 30)) is True


def test_past_date_always_final():
    # Any fetch NOW of yesterday's bar is final — downgrade is impossible.
    assert is_bar_final(date(2026, 6, 9), datetime(2026, 6, 10, 14, 0, tzinfo=UTC)) is True


def test_cutoff_constant():
    assert FINAL_CUTOFF_NY.hour == 16 and FINAL_CUTOFF_NY.minute == 5
```

- [ ] **Step 2: Run, verify FAIL** — `uv run pytest tests/data/test_finality.py -q`
  (ModuleNotFoundError).

- [ ] **Step 3: Implement**

```python
# Layer: data
"""Bar finality — final vs provisional price bars (P2 freshness spec §2).

A bar for D is FINAL iff it was fetched at/after 16:05 America/New_York on D.
Half-days are conservative (provisional until 16:05 despite a 13:00 close) —
never the unsafe direction. Downgrade is impossible: a past date's cutoff is
always before "now".
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
FINAL_CUTOFF_NY = time(16, 5)


def is_bar_final(bar_date: date, fetched_at: datetime) -> bool:
    """True iff `fetched_at` is at/after 16:05 ET on `bar_date`. Naive input is UTC."""
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    cutoff = datetime.combine(bar_date, FINAL_CUTOFF_NY, tzinfo=NY)
    return fetched_at >= cutoff
```

- [ ] **Step 4: Run, verify PASS** — same command. Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/data/finality.py tests/data/test_finality.py
git commit -m "feat(data): bar finality rule — 16:05 ET cutoff (P2-T1)"
```

---

### Task 2: Schema columns + Alembic migration with Python backfill

**Files:**
- Modify: `marketpulse/db/models.py:206-217` (`PriceCacheEntry`)
- Create: `alembic/versions/<generated>_price_cache_is_final.py`
- Test: `tests/db/test_price_cache_is_final_migration.py`

- [ ] **Step 1: Add model columns** (after `fetched_at`):

```python
    # P2 freshness spec §1: finality governance. is_final means "fetched at/
    # after the 16:05 ET close cutoff for this bar's date"; finalized_at is
    # the fetched_at of the fetch that produced the final bar — NEVER a job
    # processing time (job runtime belongs in logs, not price rows).
    is_final: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false(),
    )
    finalized_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
```

Match the file's actual import names (`Boolean`, `sa` or direct `false()` — follow how other
boolean server defaults are declared in this models.py, e.g. `PaperNavSnapshot.is_rebuilt`).

- [ ] **Step 2: Generate migration skeleton**

Run: `uv run alembic revision -m "price_cache is_final"` → note the generated revision id;
`down_revision` must be `'83cf7ac9e055'`.

- [ ] **Step 3: Write migration (schema + inlined-rule Python backfill)**

```python
"""price_cache is_final

Revision ID: <generated>
Revises: 83cf7ac9e055
"""
from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from alembic import op

revision: str = '<generated>'
down_revision: str | Sequence[str] | None = '83cf7ac9e055'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Finality rule INLINED (not imported from app code) so the migration stays
# frozen even if marketpulse.data.finality evolves. Spec §2: final iff
# fetched_at >= 16:05 America/New_York on the bar's own date.
_NY = ZoneInfo("America/New_York")
_CUTOFF = time(16, 5)


def _is_final(bar_date: date, fetched_at: datetime) -> bool:
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    return fetched_at >= datetime.combine(bar_date, _CUTOFF, tzinfo=_NY)


def upgrade() -> None:
    with op.batch_alter_table("price_cache") as batch:
        batch.add_column(sa.Column(
            "is_final", sa.Boolean(), nullable=False, server_default=sa.false(),
        ))
        batch.add_column(sa.Column("finalized_at", sa.DateTime(), nullable=True))

    # Python backfill — the cutoff is an NY wall-clock rule; UTC offsets shift
    # with DST, so this cannot be a single SQL expression.
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT ticker, date, fetched_at FROM price_cache",
    )).fetchall()
    final_keys = []
    for ticker, bar_date_s, fetched_at_s in rows:
        bar_date = date.fromisoformat(str(bar_date_s))
        fetched_at = datetime.fromisoformat(str(fetched_at_s))
        if _is_final(bar_date, fetched_at):
            final_keys.append({"t": ticker, "d": str(bar_date_s), "f": str(fetched_at_s)})
    if final_keys:
        bind.execute(
            sa.text(
                "UPDATE price_cache SET is_final = 1, finalized_at = :f "
                "WHERE ticker = :t AND date = :d",
            ),
            final_keys,
        )


def downgrade() -> None:
    with op.batch_alter_table("price_cache") as batch:
        batch.drop_column("finalized_at")
        batch.drop_column("is_final")
```

- [ ] **Step 4: Write the migration test**

```python
# Layer: db
"""Backfill correctness for the price_cache is_final migration (spec §1).

Runs the rule against a real engine: seed rows via raw SQL into a pre-migration
shape is overkill here — instead assert post-migration invariants on freshly
upserted rows in Tasks 3's tests, and verify the BACKFILL RULE itself by
importing the migration module and exercising _is_final directly.
"""
from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path


def _load_migration():
    path = next(Path("alembic/versions").glob("*price_cache_is_final.py"))
    spec = importlib.util.spec_from_file_location("mig", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backfill_rule_intraday_edt_provisional():
    mig = _load_migration()
    assert mig._is_final(date(2026, 6, 10), datetime(2026, 6, 10, 16, 30, tzinfo=UTC)) is False


def test_backfill_rule_evening_edt_final():
    mig = _load_migration()
    assert mig._is_final(date(2026, 6, 10), datetime(2026, 6, 10, 21, 30, tzinfo=UTC)) is True


def test_backfill_rule_est_winter():
    mig = _load_migration()
    assert mig._is_final(date(2026, 1, 15), datetime(2026, 1, 15, 21, 4, tzinfo=UTC)) is False
    assert mig._is_final(date(2026, 1, 15), datetime(2026, 1, 15, 21, 6, tzinfo=UTC)) is True


def test_backfill_rule_naive_is_utc():
    mig = _load_migration()
    assert mig._is_final(date(2026, 6, 10), datetime(2026, 6, 10, 16, 30)) is False
```

- [ ] **Step 5: Run** — `uv run pytest tests/db/test_price_cache_is_final_migration.py -q`
  → 4 passed. Also run `uv run alembic upgrade head` against a scratch DB if the test suite's
  fixtures don't already exercise migrations (check `tests/conftest.py` — if fixtures
  `create_all` from models instead of running Alembic, the model columns cover the test DBs and
  the migration covers prod; both paths are now consistent).

- [ ] **Step 6: Run the FULL suite** — `uv run pytest -q`. The new NOT NULL column has a
  server default, so existing fixtures must stay green. Fix any fixture that constructs
  `PriceCacheEntry` explicitly (grep `PriceCacheEntry(` in tests) by leaving the new columns
  defaulted.

- [ ] **Step 7: Commit**

```bash
git add marketpulse/db/models.py alembic/versions/*price_cache_is_final.py tests/db/test_price_cache_is_final_migration.py
git commit -m "feat(db): price_cache is_final/finalized_at + DST-correct Python backfill (P2-T2)"
```

---

### Task 3: Write-time finality in `PriceCache.upsert`

**Files:**
- Modify: `marketpulse/data/cache.py:15-45`
- Test: `tests/data/test_price_cache_finality.py`

- [ ] **Step 1: Write the failing tests**

```python
# Layer: data
"""Write-time finality in PriceCache.upsert (spec §3)."""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select

from marketpulse.data.cache import PriceCache
from marketpulse.data.types import Bar
from marketpulse.db.models import PriceCacheEntry


def _bar(d: date, close: float = 100.0) -> Bar:
    return Bar(date=d, open=close, high=close, low=close, close=close, volume=1)


def _row(db_session, ticker: str, d: date) -> PriceCacheEntry:
    return db_session.execute(
        select(PriceCacheEntry).where(
            PriceCacheEntry.ticker == ticker, PriceCacheEntry.date == d,
        ),
    ).scalar_one()


def test_intraday_bar_written_provisional(db_session, monkeypatch):
    cache = PriceCache(db_session)
    intraday = datetime(2026, 6, 10, 16, 30, tzinfo=UTC)  # 12:30 ET
    monkeypatch.setattr("marketpulse.data.cache._now_utc", lambda: intraday)
    cache.upsert("SPY", [_bar(date(2026, 6, 10))])
    row = _row(db_session, "SPY", date(2026, 6, 10))
    assert row.is_final is False
    assert row.finalized_at is None


def test_after_close_bar_written_final_with_finalized_at_eq_fetched_at(db_session, monkeypatch):
    cache = PriceCache(db_session)
    evening = datetime(2026, 6, 10, 21, 30, tzinfo=UTC)  # 17:30 ET
    monkeypatch.setattr("marketpulse.data.cache._now_utc", lambda: evening)
    cache.upsert("SPY", [_bar(date(2026, 6, 10))])
    row = _row(db_session, "SPY", date(2026, 6, 10))
    assert row.is_final is True
    # finalized_at == fetched_at: "when obtained from source", never job time.
    assert row.finalized_at == row.fetched_at


def test_provisional_row_flips_final_on_post_close_refetch(db_session, monkeypatch):
    cache = PriceCache(db_session)
    monkeypatch.setattr(
        "marketpulse.data.cache._now_utc",
        lambda: datetime(2026, 6, 10, 16, 30, tzinfo=UTC),
    )
    cache.upsert("SPY", [_bar(date(2026, 6, 10), close=730.72)])   # midday
    monkeypatch.setattr(
        "marketpulse.data.cache._now_utc",
        lambda: datetime(2026, 6, 10, 21, 30, tzinfo=UTC),
    )
    cache.upsert("SPY", [_bar(date(2026, 6, 10), close=725.10)])   # true close
    row = _row(db_session, "SPY", date(2026, 6, 10))
    assert row.is_final is True
    assert row.close == 725.10  # OHLCV overwritten atomically with the flip


def test_past_date_bar_always_final(db_session, monkeypatch):
    cache = PriceCache(db_session)
    monkeypatch.setattr(
        "marketpulse.data.cache._now_utc",
        lambda: datetime(2026, 6, 11, 14, 0, tzinfo=UTC),  # intraday TODAY
    )
    cache.upsert("AAPL", [_bar(date(2026, 6, 10))])        # YESTERDAY's bar
    assert _row(db_session, "AAPL", date(2026, 6, 10)).is_final is True
```

Use the existing `db_session` fixture (grep `tests/` for how other `# Layer: data` tests
obtain a session against a tmp SQLite DB; reuse, do not invent).

- [ ] **Step 2: Run, verify FAIL** — `_now_utc` doesn't exist yet.

- [ ] **Step 3: Implement** — in `marketpulse/data/cache.py`:

```python
from marketpulse.data.finality import is_bar_final


def _now_utc() -> datetime:
    """Module-level for test monkeypatching."""
    return datetime.now(UTC)
```

and rewrite `upsert`'s row construction + conflict update:

```python
    def upsert(self, ticker: str, bars: list[Bar]) -> None:
        if not bars:
            return
        now = _now_utc()
        rows = []
        for b in bars:
            final = is_bar_final(b.date, now)
            rows.append(
                {
                    "ticker": ticker,
                    "date": b.date,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                    "fetched_at": now,
                    # Spec §3: finality computed at write time; finalized_at is
                    # this fetch's timestamp ("when obtained"), never job time.
                    "is_final": final,
                    "finalized_at": now if final else None,
                }
            )
        stmt = sqlite_insert(PriceCacheEntry).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["ticker", "date"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "fetched_at": stmt.excluded.fetched_at,
                "is_final": stmt.excluded.is_final,
                "finalized_at": stmt.excluded.finalized_at,
            },
        )
        self.session.execute(stmt)
        self.session.commit()
```

- [ ] **Step 4: Run, verify PASS** — `uv run pytest tests/data/test_price_cache_finality.py -q`
  → 4 passed. Then full suite (`uv run pytest -q`).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/data/cache.py tests/data/test_price_cache_finality.py
git commit -m "feat(data): write-time finality in PriceCache.upsert (P2-T3)"
```

---

### Task 4: FinalizeJob

**Files:**
- Create: `marketpulse/data/finalize.py`
- Test: `tests/data/test_finalize.py`

- [ ] **Step 1: Write the failing tests**

```python
# Layer: data
"""FinalizeJob — post-close refresh of provisional bars (spec §4)."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from marketpulse.data.cache import PriceCache
from marketpulse.data.finalize import finalize_provisional_bars
from marketpulse.data.types import Bar
from marketpulse.db.models import PriceCacheEntry

TODAY = date(2026, 6, 11)  # Thursday, NY business day


def _bar(d: date, close: float = 100.0) -> Bar:
    return Bar(date=d, open=close, high=close, low=close, close=close, volume=1)


def _seed_provisional(db_session, ticker: str, d: date, close: float, monkeypatch):
    monkeypatch.setattr(
        "marketpulse.data.cache._now_utc",
        lambda: datetime(d.year, d.month, d.day, 16, 30, tzinfo=UTC),  # 12:30 ET
    )
    PriceCache(db_session).upsert(ticker, [_bar(d, close)])


class _StubClient:
    """Records calls; returns one settled bar per requested date."""

    def __init__(self, fail_tickers: set[str] | None = None) -> None:
        self.calls: list[tuple[str, date, date]] = []
        self.fail_tickers = fail_tickers or set()

    def fetch_history_range(self, ticker: str, *, start: date, end: date) -> list[Bar]:
        self.calls.append((ticker, start, end))
        if ticker in self.fail_tickers:
            raise RuntimeError("boom")
        out, d = [], start
        while d < end:  # end exclusive, matching the real client
            if d.weekday() < 5:
                out.append(_bar(d, close=200.0))
            d += timedelta(days=1)
        return out


def _is_final(db_session, ticker: str, d: date) -> bool:
    return db_session.execute(
        select(PriceCacheEntry.is_final).where(
            PriceCacheEntry.ticker == ticker, PriceCacheEntry.date == d,
        ),
    ).scalar_one()


def test_provisional_rows_flip_final(db_session, monkeypatch):
    _seed_provisional(db_session, "AAPL", TODAY, 291.19, monkeypatch)
    monkeypatch.undo()  # restore real _now_utc: finalize's upserts run "now" (post-close in CI? no —)
    # Deterministic: pin "now" AFTER today's close so refreshed bars finalize.
    monkeypatch.setattr(
        "marketpulse.data.cache._now_utc",
        lambda: datetime(2026, 6, 11, 21, 30, tzinfo=UTC),
    )
    client = _StubClient()
    result = finalize_provisional_bars(
        db_session, client=client, today_ny=TODAY,
    )
    assert _is_final(db_session, "AAPL", TODAY) is True
    assert result.bars_finalized >= 1
    assert result.failures == 0


def test_spy_always_attempted_even_with_no_provisional_rows(db_session, monkeypatch):
    monkeypatch.setattr(
        "marketpulse.data.cache._now_utc",
        lambda: datetime(2026, 6, 11, 21, 30, tzinfo=UTC),
    )
    client = _StubClient()
    finalize_provisional_bars(db_session, client=client, today_ny=TODAY)
    assert any(t == "SPY" for t, _, _ in client.calls)


def test_spy_older_than_window_reached(db_session, monkeypatch):
    # SPY provisional row 10 trading days back — OLDER than the 5-day window.
    old = date(2026, 5, 28)
    _seed_provisional(db_session, "SPY", old, 730.72, monkeypatch)
    monkeypatch.setattr(
        "marketpulse.data.cache._now_utc",
        lambda: datetime(2026, 6, 11, 21, 30, tzinfo=UTC),
    )
    client = _StubClient()
    finalize_provisional_bars(db_session, client=client, today_ny=TODAY)
    (ticker, start, end) = next(c for c in client.calls if c[0] == "SPY")
    assert start <= old          # reaches back to the old contamination
    assert end == TODAY + timedelta(days=1)  # end exclusive includes today
    assert _is_final(db_session, "SPY", old) is True


def test_ticker_failure_is_warning_and_isolated(db_session, monkeypatch, caplog):
    _seed_provisional(db_session, "AAPL", TODAY, 291.19, monkeypatch)
    _seed_provisional(db_session, "MSFT", TODAY, 389.54, monkeypatch)
    monkeypatch.setattr(
        "marketpulse.data.cache._now_utc",
        lambda: datetime(2026, 6, 11, 21, 30, tzinfo=UTC),
    )
    client = _StubClient(fail_tickers={"AAPL"})
    result = finalize_provisional_bars(db_session, client=client, today_ny=TODAY)
    assert result.failures == 1
    assert _is_final(db_session, "AAPL", TODAY) is False   # stays provisional
    assert _is_final(db_session, "MSFT", TODAY) is True    # others unaffected


def test_spy_failure_logs_error(db_session, monkeypatch, caplog):
    import logging
    _seed_provisional(db_session, "SPY", TODAY, 730.72, monkeypatch)
    monkeypatch.setattr(
        "marketpulse.data.cache._now_utc",
        lambda: datetime(2026, 6, 11, 21, 30, tzinfo=UTC),
    )
    client = _StubClient(fail_tickers={"SPY"})
    with caplog.at_level(logging.ERROR):
        result = finalize_provisional_bars(db_session, client=client, today_ny=TODAY)
    assert result.failures == 1
    assert any("finalize_spy_failed" in r.message for r in caplog.records)
```

(Adjust the `caplog` message assertion to the repo's structlog/stdlib bridge — match how
existing tests assert on `get_logger` output; if structlog doesn't propagate to caplog, assert
via the documented repo pattern instead.)

- [ ] **Step 2: Run, verify FAIL** — module missing.

- [ ] **Step 3: Implement `marketpulse/data/finalize.py`**

```python
# Layer: data
"""FinalizeJob — post-close refresh of provisional price bars (P2 spec §4).

Mounted as the structural step BEFORE the NAV snapshot in the paper-trading
tick (ordering is structural, not clock-based). Also runnable standalone via
python -m marketpulse.cli.finalize_prices.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketpulse.data.cache import PriceCache
from marketpulse.db.models import PriceCacheEntry
from marketpulse.logging import get_logger
from marketpulse.trading.calendar import NYTradingCalendar

log = get_logger(__name__)

_SPY = "SPY"


@dataclass(frozen=True)
class FinalizeResult:
    tickers_attempted: int
    bars_finalized: int
    failures: int


def _sessions_back(cal: NYTradingCalendar, d: date, n: int) -> date:
    """Walk back n NY trading sessions from d (exclusive of d itself)."""
    out = d
    for _ in range(n):
        out = out - timedelta(days=1)
        while not cal.is_business_day(out):
            out = out - timedelta(days=1)
    return out


def _count_provisional(session: Session) -> int:
    return session.scalar(
        select(func.count())
        .select_from(PriceCacheEntry)
        .where(PriceCacheEntry.is_final == False),  # noqa: E712 — SQLA expression
    ) or 0


def finalize_provisional_bars(
    session: Session,
    *,
    client=None,
    lookback_trading_days: int = 5,
    today_ny: date | None = None,
) -> FinalizeResult:
    """Refresh provisional bars so post-close data flips final (spec §4).

    Ticker selection: tickers with provisional rows dated within the last
    `lookback_trading_days` NY sessions, always unioned with SPY (the
    north-star benchmark leg gets an attempt every run).

    Per-ticker fetch start (spec review fix — explicit branch, readable):
        start = earliest provisional date FOR THAT TICKER (any age)
        if the forced ticker has no provisional rows: start = cutoff
    This closes the SPY older-than-window hole: the forced union plus the
    any-age earliest date reaches old contamination (e.g. 2026-06-10).
    """
    from marketpulse.data.yfinance_client import YFinanceClient  # lazy: tests inject

    cal = NYTradingCalendar()
    today = today_ny or cal.today_ny_trading_date(datetime.now(UTC))
    cutoff = _sessions_back(cal, today, lookback_trading_days)
    if client is None:
        client = YFinanceClient()

    in_window = session.scalars(
        select(PriceCacheEntry.ticker)
        .where(PriceCacheEntry.is_final == False)  # noqa: E712
        .where(PriceCacheEntry.date >= cutoff)
        .distinct(),
    ).all()
    tickers = sorted(set(in_window) | {_SPY})

    before = _count_provisional(session)
    cache = PriceCache(session)
    failures = 0
    for ticker in tickers:
        earliest = session.scalar(
            select(func.min(PriceCacheEntry.date))
            .where(PriceCacheEntry.ticker == ticker)
            .where(PriceCacheEntry.is_final == False),  # noqa: E712
        )
        if earliest is None:
            start = cutoff           # forced ticker (SPY) with nothing provisional
        else:
            start = min(earliest, cutoff)
        try:
            # fetch_history_range: end is EXCLUSIVE — +1 day includes today.
            bars = client.fetch_history_range(
                ticker, start=start, end=today + timedelta(days=1),
            )
            cache.upsert(ticker, bars)
        except Exception as exc:  # noqa: BLE001 — per-ticker isolation (spec §4.3)
            failures += 1
            if ticker == _SPY:
                # North-star benchmark leg: degradation must be LOUD.
                log.error("finalize_spy_failed", ticker=ticker, error=str(exc))
            else:
                log.warning("finalize_ticker_failed", ticker=ticker, error=str(exc))

    after = _count_provisional(session)
    result = FinalizeResult(
        tickers_attempted=len(tickers),
        bars_finalized=max(0, before - after),
        failures=failures,
    )
    log.info(
        "finalize_provisional_bars_done",
        tickers_attempted=result.tickers_attempted,
        bars_finalized=result.bars_finalized,
        failures=result.failures,
    )
    return result
```

- [ ] **Step 4: Run, verify PASS** — `uv run pytest tests/data/test_finalize.py -q`.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/data/finalize.py tests/data/test_finalize.py
git commit -m "feat(data): FinalizeJob — post-close provisional bar refresh (P2-T4)"
```

---

### Task 5: NAV final-only lookup + fallback diagnostics

**Files:**
- Modify: `marketpulse/portfolio/snapshot_runner.py:83-121` (`_read_price_lookup`) and its
  unpacking in `run_nav_snapshot` (~line 153)
- Test: `tests/portfolio/test_snapshot_runner_finality.py` (or extend the existing
  snapshot_runner test module — match where `_read_price_lookup` is currently tested)

- [ ] **Step 1: Write the failing tests** — seed `PriceCacheEntry` rows directly:

```python
# Layer: orchestration
"""NAV final-only price lookup + provisional-fallback diagnostics (spec §5)."""
from __future__ import annotations

from datetime import UTC, date, datetime

from marketpulse.db.models import PriceCacheEntry
from marketpulse.portfolio.snapshot_runner import _read_price_lookup


def _seed(db_session, ticker, d, close, *, is_final):
    db_session.add(PriceCacheEntry(
        ticker=ticker, date=d, open=close, high=close, low=close,
        close=close, volume=1,
        fetched_at=datetime(2026, 6, 11, 12, 0, tzinfo=UTC),
        is_final=is_final,
        finalized_at=datetime(2026, 6, 11, 12, 0, tzinfo=UTC) if is_final else None,
    ))
    db_session.commit()


def test_wrong_shape_regression_provisional_max_falls_back_to_final(db_session):
    """MANDATORY (spec §5/§review): 06-11 provisional + 06-10 final,
    trading_date=06-11 → MUST return the 06-10 close, not None. Catches the
    join-only-filter bug where the subquery picks the provisional max(date)."""
    _seed(db_session, "SPY", date(2026, 6, 10), 725.10, is_final=True)
    _seed(db_session, "SPY", date(2026, 6, 11), 730.72, is_final=False)
    lookup, spy_close, fallback = _read_price_lookup(db_session, date(2026, 6, 11))
    assert spy_close is not None
    assert float(spy_close) == 725.10
    assert "SPY" in fallback


def test_all_final_uses_latest(db_session):
    _seed(db_session, "SPY", date(2026, 6, 10), 725.10, is_final=True)
    _seed(db_session, "SPY", date(2026, 6, 11), 728.00, is_final=True)
    lookup, spy_close, fallback = _read_price_lookup(db_session, date(2026, 6, 11))
    assert float(spy_close) == 728.00
    assert fallback == []


def test_all_provisional_ticker_is_unpriced(db_session):
    _seed(db_session, "QBTS", date(2026, 6, 11), 23.60, is_final=False)
    lookup, _, fallback = _read_price_lookup(db_session, date(2026, 6, 11))
    assert lookup("QBTS") is None
    assert "QBTS" in fallback
```

- [ ] **Step 2: Run, verify FAIL** — current signature returns 2-tuple / provisional values leak.

- [ ] **Step 3: Implement** — rewrite `_read_price_lookup`:

```python
def _read_price_lookup(session: Session, trading_date: date):
    """Mark-to-last-available-FINAL-close per ticker ON OR BEFORE trading_date.

    P2 freshness spec §5: only `is_final = true` bars are eligible — the
    2026-06-11 audit proved intraday (provisional) bars DO land in
    price_cache (the old premise that yfinance only publishes completed
    daily bars was false; SPY 06-10 was pinned at a midday price and
    contaminated two north-star days). The finality filter applies to BOTH
    the max(date) subquery AND the join: join-only filtering would let the
    subquery select a provisional max(date) and resolve a ticker to None
    even when a final yesterday-close exists.

    A ticker whose latest raw bar is provisional values at its previous
    final close — the charter-tolerated `<= trading_date` / ~1-day-lag
    convention. The `<=` bound (never `>`) prevents lookahead bias.
    Float → Decimal at the boundary.

    Returns (lookup, spy_close, provisional_fallback_tickers) — the third
    element lists tickers whose raw max(date) bar was excluded as
    provisional (observability: how often NAV runs on day-old closes).
    """
    def _max_dates(*, final_only: bool) -> dict[str, date]:
        stmt = (
            select(
                PriceCacheEntry.ticker,
                func.max(PriceCacheEntry.date).label("max_date"),
            )
            .where(PriceCacheEntry.date <= trading_date)
            .group_by(PriceCacheEntry.ticker)
        )
        if final_only:
            stmt = stmt.where(PriceCacheEntry.is_final == True)  # noqa: E712
        return dict(session.execute(stmt).all())

    final_max = _max_dates(final_only=True)
    raw_max = _max_dates(final_only=False)

    latest = (
        select(
            PriceCacheEntry.ticker,
            func.max(PriceCacheEntry.date).label("max_date"),
        )
        .where(PriceCacheEntry.date <= trading_date)
        .where(PriceCacheEntry.is_final == True)  # noqa: E712 — subquery filter
        .group_by(PriceCacheEntry.ticker)
        .subquery()
    )
    rows = session.scalars(
        select(PriceCacheEntry).join(
            latest,
            and_(
                PriceCacheEntry.ticker == latest.c.ticker,
                PriceCacheEntry.date == latest.c.max_date,
                PriceCacheEntry.is_final == True,  # noqa: E712 — join filter too
            ),
        ),
    ).all()
    table = {r.ticker: Decimal(str(r.close)) for r in rows}

    provisional_fallback_tickers = sorted(
        t for t, d in raw_max.items()
        if t not in final_max or final_max[t] < d
    )

    def lookup(ticker: str) -> Decimal | None:
        return table.get(ticker)

    return lookup, table.get(_SPY_TICKER), provisional_fallback_tickers
```

and in `run_nav_snapshot`, update the unpacking + add the structured log (stdlib style with
`extra=`, matching this file), restricted to consumed tickers:

```python
    price_lookup, spy_close, fallback_all = _read_price_lookup(session, trading_date)
    consumed = {pos.ticker for pos in open_positions} | {_SPY_TICKER}
    fallback = sorted(set(fallback_all) & consumed)
    if fallback:
        log.info(
            "nav_provisional_fallback",
            extra={
                "tick_date": str(trading_date),
                "provisional_fallback_count": len(fallback),
                "provisional_fallback_tickers": fallback,
            },
        )
```

- [ ] **Step 4: Run new tests + the full existing snapshot/NAV suites** —
  `uv run pytest tests/portfolio -q` then `uv run pytest -q`. Existing NAV tests seed
  price rows — any that now fail because their seeds default `is_final=False` must seed
  `is_final=True` (they model settled closes; update the fixtures, not the filter).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/portfolio/snapshot_runner.py tests/portfolio/test_snapshot_runner_finality.py
git commit -m "feat(portfolio): NAV consumes final bars only + fallback diagnostics (P2-T5)"
```

---

### Task 6: Tick mount (structural step 0) + finalize CLI

**Files:**
- Modify: `marketpulse/scheduler/paper_trading_tick.py` (immediately before line 118's
  `_run_nav_snapshot_safely`)
- Create: `marketpulse/cli/finalize_prices.py`
- Test: `tests/scheduler/test_tick_finalize_mount.py`

- [ ] **Step 1: Write the failing test** — assert ordering: finalize runs before the NAV
  snapshot within the tick. Pattern: monkeypatch both
  `marketpulse.scheduler.paper_trading_tick.finalize_provisional_bars` and
  `marketpulse.scheduler.paper_trading_tick._run_nav_snapshot_safely` with recorders appending
  to a shared list; invoke the tick entry function the existing tick tests use (reuse their
  fixtures/stubs wholesale); assert `calls == ["finalize", "nav"]`.

```python
# Layer: scheduler
"""Finalize is the structural step BEFORE the NAV snapshot (spec §4 mount)."""
# Reuse the existing paper_trading_tick test module's fixtures for engine/
# repository stubs — copy its minimal invocation, then:

def test_finalize_runs_before_nav_snapshot(monkeypatch, <existing tick fixtures>):
    calls = []
    monkeypatch.setattr(
        "marketpulse.scheduler.paper_trading_tick.finalize_provisional_bars",
        lambda session: calls.append("finalize"),
    )
    monkeypatch.setattr(
        "marketpulse.scheduler.paper_trading_tick._run_nav_snapshot_safely",
        lambda session, *, tick_date: calls.append("nav"),
    )
    <invoke tick as existing tests do>
    assert calls == ["finalize", "nav"]


def test_finalize_failure_does_not_abort_tick(monkeypatch, <existing tick fixtures>):
    def boom(session):
        raise RuntimeError("yfinance down")
    monkeypatch.setattr(
        "marketpulse.scheduler.paper_trading_tick.finalize_provisional_bars", boom,
    )
    seen = []
    monkeypatch.setattr(
        "marketpulse.scheduler.paper_trading_tick._run_nav_snapshot_safely",
        lambda session, *, tick_date: seen.append(tick_date),
    )
    <invoke tick>
    assert seen  # NAV still ran
```

(The `<...>` markers are instructions to mirror the existing tick test module's setup — read
`tests/scheduler/` for the current tick tests and reuse their fixtures verbatim. They are not
placeholders for new design.)

- [ ] **Step 2: Run, verify FAIL** — attribute `finalize_provisional_bars` not on the module.

- [ ] **Step 3: Implement** — in `paper_trading_tick.py`, import at top:

```python
from marketpulse.data.finalize import finalize_provisional_bars
```

and immediately before `_run_nav_snapshot_safely(session, tick_date=result.tick_date)`:

```python
        # P2 freshness spec §4 — finalize provisional bars BEFORE the NAV
        # snapshot reads price_cache. Structural ordering (step 0 of NAV),
        # not clock-based: a parallel cron ordered only by wall clock is the
        # same failure shape that produced the SPY 06-10 contamination.
        # Never aborts the tick; SPY-failure severity handled inside the job.
        try:
            finalize_provisional_bars(session)
        except Exception as exc:  # noqa: BLE001 — belt-and-braces
            log.warning("finalize_provisional_bars_failed: %s", exc)
```

- [ ] **Step 4: Create the CLI** — `marketpulse/cli/finalize_prices.py`, mirroring
  `refresh_sectors.py`:

```python
# Layer: cli
"""Manual finalize pass: python -m marketpulse.cli.finalize_prices"""
from __future__ import annotations

import contextlib

from marketpulse.data.finalize import finalize_provisional_bars
from marketpulse.db.base import session_scope


def main() -> None:
    gen = session_scope()
    db = next(gen)
    try:
        r = finalize_provisional_bars(db)
        db.commit()
        print(
            f"finalize: attempted={r.tickers_attempted} "
            f"finalized={r.bars_finalized} failures={r.failures}"
        )
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run** — new tests pass; full tick suite passes (existing tick tests must not
  hit the network: if any now reach the real `finalize_provisional_bars`, monkeypatch it to a
  no-op in their shared fixture — one fixture change, not per-test).

- [ ] **Step 6: Commit**

```bash
git add marketpulse/scheduler/paper_trading_tick.py marketpulse/cli/finalize_prices.py tests/scheduler/test_tick_finalize_mount.py
git commit -m "feat(scheduler): finalize pass as structural step 0 of NAV tick + CLI (P2-T6)"
```

---

### Task 7: Rebuild CLI for the contaminated snapshots

**Files:**
- Create: `marketpulse/cli/rebuild_nav_snapshots.py`
- Test: `tests/cli/test_rebuild_nav_snapshots.py`

- [ ] **Step 1: Write the failing tests**

```python
# Layer: cli
"""One-off rebuild of provisional-contaminated NAV snapshots (spec §6)."""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select

from marketpulse.cli.rebuild_nav_snapshots import rebuild
from marketpulse.db.models import PaperNavSnapshot, PriceCacheEntry


def test_rebuild_order_and_flags(db_session, monkeypatch, <nav seed fixtures>):
    # Seed: contaminated snapshots for 06-10/06-11 (midday SPY 730.72) +
    # healed FINAL price rows (SPY 06-10 = 725.10) + cash ledger rows the
    # snapshot runner needs (reuse the existing snapshot_runner test seeds).
    calls = []
    monkeypatch.setattr(
        "marketpulse.cli.rebuild_nav_snapshots.finalize_provisional_bars",
        lambda session: calls.append("finalize"),
    )
    rebuild(db_session, dates=(date(2026, 6, 10), date(2026, 6, 11)))
    assert calls == ["finalize"]  # finalize ran exactly once, FIRST
    rows = db_session.scalars(
        select(PaperNavSnapshot).order_by(PaperNavSnapshot.trading_date),
    ).all()
    by_date = {r.trading_date: r for r in rows}
    for d in (date(2026, 6, 10), date(2026, 6, 11)):
        assert by_date[d].is_rebuilt is True
        assert by_date[d].rebuild_reason == "provisional_price_cache_fix"
    assert float(by_date[date(2026, 6, 10)].spy_close) == 725.10  # healed value


def test_rebuild_skips_missing_date_gracefully(db_session, monkeypatch):
    monkeypatch.setattr(
        "marketpulse.cli.rebuild_nav_snapshots.finalize_provisional_bars",
        lambda session: None,
    )
    # No snapshot exists for this date and no ledger → rebuild() must report,
    # not crash (NoCashLedgerForDate is caught per-date).
    rebuild(db_session, dates=(date(2026, 6, 9),))
```

(`<nav seed fixtures>`: reuse the existing `run_nav_snapshot` test module's seeding helpers —
cash ledger + open positions + price rows; read `tests/portfolio/` first. This is a reuse
instruction, not new design.)

- [ ] **Step 2: Run, verify FAIL.**

- [ ] **Step 3: Implement**

```python
# Layer: cli
"""Rebuild provisional-contaminated NAV snapshots (P2 spec §6).

python -m marketpulse.cli.rebuild_nav_snapshots
Ordering is FIXED (06-11's north-star depends on prior state):
  1. FinalizeJob heals the bars (SPY 2026-06-10 midday price → true close).
  2. Rebuild 2026-06-10.
  3. Rebuild 2026-06-11.
PaperNavSnapshot Lock L1 names this admin path: is_rebuilt + rebuild_reason.
"""
from __future__ import annotations

import contextlib
from datetime import date

from sqlalchemy import update
from sqlalchemy.orm import Session

from marketpulse.data.finalize import finalize_provisional_bars
from marketpulse.db.base import session_scope
from marketpulse.db.models import PaperNavSnapshot
from marketpulse.logging import get_logger
from marketpulse.portfolio.snapshot_runner import NoCashLedgerForDate, run_nav_snapshot

log = get_logger(__name__)

REBUILD_REASON = "provisional_price_cache_fix"
CONTAMINATED_DATES = (date(2026, 6, 10), date(2026, 6, 11))


def rebuild(session: Session, *, dates: tuple[date, ...] = CONTAMINATED_DATES) -> None:
    # 1. Heal the data first — rebuild order is fixed, not discretionary.
    finalize_provisional_bars(session)
    session.commit()

    # 2./3. Delete + recompute in ascending date order (run_nav_snapshot is
    # idempotent and would otherwise return the stale row without recompute).
    for d in sorted(dates):
        deleted = session.query(PaperNavSnapshot).filter(
            PaperNavSnapshot.trading_date == d,
        ).delete()
        session.commit()
        try:
            run_nav_snapshot(session, trading_date=d)
            session.commit()
        except NoCashLedgerForDate:
            log.warning("rebuild_skipped_no_ledger", trading_date=str(d))
            continue
        session.execute(
            update(PaperNavSnapshot)
            .where(PaperNavSnapshot.trading_date == d)
            .values(is_rebuilt=True, rebuild_reason=REBUILD_REASON),
        )
        session.commit()
        log.info("nav_snapshot_rebuilt", trading_date=str(d), had_existing=bool(deleted))


def main() -> None:
    gen = session_scope()
    db = next(gen)
    try:
        rebuild(db)
        print(f"rebuilt {[str(d) for d in CONTAMINATED_DATES]} reason={REBUILD_REASON}")
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run, verify PASS**, then full suite.

- [ ] **Step 5: Commit**

```bash
git add marketpulse/cli/rebuild_nav_snapshots.py tests/cli/test_rebuild_nav_snapshots.py
git commit -m "feat(cli): rebuild contaminated NAV snapshots — finalize→06-10→06-11 (P2-T7)"
```

---

### Task 8: CHARTER PR2 note + spec path fix + final integration

**Files:**
- Modify: `docs/CHARTER.md` (data-trust chain entry)
- Modify: `docs/superpowers/specs/2026-06-11-p2-freshness-governance-design.md` (file list)

- [ ] **Step 1: CHARTER** — in the research-trustworthiness evidence chain, data-trust item,
  append after the "promoted from idea to required work" sentence:

```markdown
**Future hardening (PR2):** evaluation/backtest read paths consume only-final bars
(deferred per the 2026-06-11 review — the NAV leg was the proven production defect; the
research-path filter is defense-in-depth once the FinalizeJob exists).
```

- [ ] **Step 2: Spec file-list fix** — in the spec's "Files touched", replace
  `marketpulse/jobs/finalize_prices.py` with `marketpulse/cli/finalize_prices.py` and the
  rebuild entry with `marketpulse/cli/rebuild_nav_snapshots.py` (repo CLI convention).

- [ ] **Step 3: Final verification**

Run: `uv run pytest -q` → ALL pass (expect ~1950+). Run `uv run ruff check` → clean.

- [ ] **Step 4: Commit**

```bash
git add docs/CHARTER.md docs/superpowers/specs/2026-06-11-p2-freshness-governance-design.md
git commit -m "docs: CHARTER PR2 future-hardening note + spec CLI path fix (P2-T8)"
```

---

## Deploy sequence (after PR merge — operator steps, not plan tasks)

1. Merge PR → image builds → manual `docker compose ... up -d --pull always --no-deps marketpulse`.
2. Container start runs Alembic upgrade (verify migration applied: 24 rows `is_final=0`).
3. Run `docker exec marketpulse /app/.venv/bin/python -m marketpulse.cli.rebuild_nav_snapshots`
   (finalizes bars, rebuilds 06-10 → 06-11 in order).
4. Verify: `paper_nav_snapshot` 06-10/06-11 rows show `is_rebuilt=1`,
   `rebuild_reason='provisional_price_cache_fix'`, and `spy_close != 730.719970703125`;
   `price_cache` has 0 rows where `is_final=0 AND date < today`.
