# Sector Cache Persistence + Warmup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/watchlist` sector classification durable so tickers stop collapsing into "Uncategorized" after container recreates — by persisting the sector cache to the mounted `/data` volume, warming it from yfinance for the watchlist∪holdings universe, and pinning the few non-GICS tickers (ETF/leveraged/quantum) via overrides.

**Architecture (locked, L14):** Sector classification priority is `holdings.sector` > `config/sector_overrides.yaml` > **persistent** `sector_cache.json` > `Uncategorized`. The `/watchlist` presenter stays cache-only / zero-network (unchanged). A new **background job** does the network (yfinance) and writes the persistent cache. `sector_cache.json` must live on `/data` and survive container recreation.

**Tech Stack:** Python 3.12, pydantic-settings, APScheduler `CronTrigger`, yfinance (reuse existing client), pytest, structlog.

**Cross-cutting caution:** `config/sector_overrides.yaml` is **shared** — it also feeds the allocator's `SectorExposureGate`. Therefore P3 only **adds** entries (never relabels the existing `TQQQ: leveraged`), choosing values consistent with the file's stated intent (ETFs/leveraged/thematic).

---

## Task 1: Make the sector cache path env-configurable (P1)

**Files:**
- Modify: `marketpulse/backtest/sector.py` (around line 68 `_DEFAULT_CACHE_PATH`, and `save_sector_cache`/`load_sector_cache` at ~90/97)
- Test: `tests/backtest/test_sector_cache_path.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# Layer: test
"""Sector cache path honors SECTOR_CACHE_PATH env (so it can live on /data)."""
from __future__ import annotations

import importlib


def test_cache_path_follows_env(tmp_path, monkeypatch):
    target = tmp_path / "sub" / "sector_cache.json"
    monkeypatch.setenv("SECTOR_CACHE_PATH", str(target))
    import marketpulse.backtest.sector as sec
    importlib.reload(sec)  # re-evaluate module-level default with env set
    try:
        sec.save_sector_cache({"AAPL": "Technology"})
        assert target.exists()  # written to the env path, parent auto-created
        assert sec.load_sector_cache() == {"AAPL": "Technology"}
    finally:
        monkeypatch.delenv("SECTOR_CACHE_PATH", raising=False)
        importlib.reload(sec)


def test_cache_path_defaults_to_app_data(monkeypatch):
    monkeypatch.delenv("SECTOR_CACHE_PATH", raising=False)
    import marketpulse.backtest.sector as sec
    importlib.reload(sec)
    assert str(sec._default_cache_path()).endswith("/data/sector_cache.json")
```

- [ ] **Step 2: Run → FAIL**

Run: `uv run pytest tests/backtest/test_sector_cache_path.py -v`
Expected: FAIL (`_default_cache_path` undefined; env ignored).

- [ ] **Step 3: Implement** in `marketpulse/backtest/sector.py`

Add `import os` at top. Replace the module-level `_DEFAULT_CACHE_PATH` constant with a function (keeps decoupling from config.py — sector.py is a pure util):

```python
_LEGACY_CACHE_PATH = Path(__file__).parent.parent.parent / "data" / "sector_cache.json"


def _default_cache_path() -> Path:
    """Cache lives wherever SECTOR_CACHE_PATH points (set to the mounted /data
    volume in prod so it survives container recreation). Falls back to the
    in-repo data/ dir for local dev / tests."""
    env = os.environ.get("SECTOR_CACHE_PATH")
    return Path(env) if env else _LEGACY_CACHE_PATH
```

Update `save_sector_cache` to use it AND create the parent dir:

```python
def save_sector_cache(cache: dict[str, str], path: Path | str | None = None) -> None:
    target = Path(path) if path is not None else _default_cache_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
```
(Preserve the existing write format if it differs — read the current body first and keep its json.dump style; only swap the path source + add the mkdir.)

Update `load_sector_cache` default: `target = Path(path) if path is not None else _default_cache_path()`.

Grep for any other reference to `_DEFAULT_CACHE_PATH` in the module and the `get_sector(...)` function (line ~113) and repoint them to `_default_cache_path()`.

- [ ] **Step 4: Run → PASS**

Run: `uv run pytest tests/backtest/test_sector_cache_path.py -v`

- [ ] **Step 5: Regression** (cache is used by risk gate + presenter)

Run: `uv run pytest tests/backtest/ tests/web/test_watchlist_view.py -q`
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add marketpulse/backtest/sector.py tests/backtest/test_sector_cache_path.py
git commit -m "feat(sector): make sector_cache.json path env-configurable (SECTOR_CACHE_PATH)"
```

---

## Task 2: Wire SECTOR_CACHE_PATH to the mounted volume in compose (P1)

**Files:**
- Modify: `docker-compose.cn.yml`, `docker-compose.prod.yml` (the `marketpulse` service `environment:` block)

- [ ] **Step 1: Add the env var to both compose files**

In each file's `marketpulse` service `environment:` mapping, add:
```yaml
      SECTOR_CACHE_PATH: ${SECTOR_CACHE_PATH:-/data/sector_cache.json}
```
`/data` is the existing mounted volume (same place `marketpulse.db` lives), so the cache now survives recreates.

- [ ] **Step 2: Sanity-check** the compose files parse

Run: `cd /Users/harvey/Dev/src/MarketPulse && docker compose -f docker-compose.prod.yml config >/dev/null 2>&1 || echo "(docker not available locally — skip; YAML lint instead)"`
If docker isn't available: visually verify indentation matches the surrounding `environment:` keys.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.cn.yml docker-compose.prod.yml
git commit -m "chore(deploy): persist sector_cache.json to /data via SECTOR_CACHE_PATH"
```

---

## Task 3: Pin non-GICS watchlist tickers via overrides (P3)

**Files:**
- Modify: `config/sector_overrides.yaml`
- Test: `tests/backtest/test_sector_overrides_special.py` (create)

**Note:** ADD-only. Do NOT change the existing `TQQQ: leveraged` line (shared with `SectorExposureGate`). Values chosen to match the file's documented intent (ETFs / leveraged / thematic with unreliable yfinance sectors).

- [ ] **Step 1: Write the failing test**

```python
# Layer: test
"""Special (ETF/leveraged/quantum) watchlist tickers are pinned via overrides."""
from __future__ import annotations

from marketpulse.backtest.sector import load_sector_overrides


def test_special_tickers_have_overrides():
    ov = load_sector_overrides()
    assert ov.get("SPY") == "ETF"
    assert ov.get("QQQ") == "ETF"
    assert ov.get("IWM") == "ETF"
    assert ov.get("TNA") == "leveraged"     # 3x small-cap bull, like TQQQ
    assert ov.get("TQQQ") == "leveraged"    # pre-existing, unchanged
    assert ov.get("QBTS") == "Quantum"
    assert ov.get("QUBT") == "Quantum"
```

- [ ] **Step 2: Run → FAIL**

Run: `uv run pytest tests/backtest/test_sector_overrides_special.py -v`

- [ ] **Step 3: Implement** — under the `overrides:` key in `config/sector_overrides.yaml`, add (keep `TQQQ: leveraged` as-is):

```yaml
  # Broad-market / sector ETFs (no natural single GICS sector)
  SPY: ETF
  QQQ: ETF
  IWM: ETF
  # Leveraged ETFs (3x)
  TNA: leveraged
  # Quantum-computing thematic (yfinance sector unreliable / inconsistent)
  QBTS: Quantum
  QUBT: Quantum
```

- [ ] **Step 4: Run → PASS**

Run: `uv run pytest tests/backtest/test_sector_overrides_special.py -v`

- [ ] **Step 5: Guard — confirm risk-gate override loading still valid**

Run: `uv run pytest tests/ -q -k "sector"`
Expected: pass (no test asserts the absence of these keys).

- [ ] **Step 6: Commit**

```bash
git add config/sector_overrides.yaml tests/backtest/test_sector_overrides_special.py
git commit -m "feat(sector): pin ETF/leveraged/quantum watchlist tickers via overrides"
```

---

## Task 4: Sector-cache warmup function (P2)

**Files:**
- Create: `marketpulse/scheduler/sector_refresh.py`
- Test: `tests/scheduler/test_sector_refresh.py` (create)

Reuse the existing yfinance lookup. `marketpulse/trading/risk_gates/_sector.py::_LazyYfSectorClient` implements `get_sector(ticker) -> str | None`; use it as the default client (injectable for tests).

- [ ] **Step 1: Write the failing test**

```python
# Layer: test
"""Warmup fetches+persists sectors only for tickers not already resolved."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from marketpulse.db.models import Holding, WatchlistItem
from marketpulse.scheduler.sector_refresh import refresh_sector_cache


class _FakeClient:
    def __init__(self, mapping): self.mapping = mapping; self.calls = []
    def get_sector(self, ticker):
        self.calls.append(ticker)
        return self.mapping.get(ticker)


def test_refresh_resolves_only_uncached(db_session, monkeypatch, tmp_path):
    monkeypatch.setenv("SECTOR_CACHE_PATH", str(tmp_path / "sector_cache.json"))
    import importlib, marketpulse.backtest.sector as sec
    importlib.reload(sec)
    # AAPL is a holding w/ sector -> must be skipped; MSFT uncached -> fetched;
    # SPY in overrides -> skipped; ZZZZ fetch returns None -> skipped/persist-miss
    db_session.add(Holding(ticker="AAPL", quantity=1, avg_cost=1, sector="Technology"))
    for t in ["AAPL", "MSFT", "SPY", "ZZZZ"]:
        db_session.add(WatchlistItem(ticker=t))
    db_session.commit()

    client = _FakeClient({"MSFT": "Technology", "ZZZZ": None})
    summary = refresh_sector_cache(db_session, client=client)

    assert "MSFT" in client.calls          # uncached equity -> fetched
    assert "AAPL" not in client.calls       # held (holdings.sector) -> skipped
    assert "SPY" not in client.calls        # override -> skipped
    assert sec.load_sector_cache().get("MSFT") == "Technology"
    assert "ZZZZ" not in sec.load_sector_cache()  # None result not cached
    assert summary.resolved == 1

    # idempotent: second run does not re-fetch MSFT
    client.calls.clear()
    refresh_sector_cache(db_session, client=client)
    assert "MSFT" not in client.calls
    importlib.reload(sec)  # cleanup module state
```

Verify the real `Holding` / `WatchlistItem` constructor kwargs against `marketpulse/db/models.py` before finalizing (match non-nullable columns).

- [ ] **Step 2: Run → FAIL**

Run: `uv run pytest tests/scheduler/test_sector_refresh.py -v`

- [ ] **Step 3: Implement** `marketpulse/scheduler/sector_refresh.py`

```python
# Layer: scheduler
"""Warm the persistent sector cache for the watchlist∪holdings universe.

Network (yfinance) lives here, OUTSIDE the cache-only /watchlist presenter.
Resolution priority mirrors L14: holdings.sector > overrides > cache. Only
tickers unresolved by all three get a yfinance lookup; successes are written
to the persistent sector_cache.json (SECTOR_CACHE_PATH)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketpulse.backtest.sector import (
    load_sector_cache,
    load_sector_overrides,
    save_sector_cache,
)
from marketpulse.db.models import Holding, WatchlistItem
from marketpulse.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class SectorRefreshSummary:
    universe: int
    already: int
    resolved: int
    failed: int


def refresh_sector_cache(db: Session, *, client=None) -> SectorRefreshSummary:
    if client is None:
        from marketpulse.trading.risk_gates._sector import _LazyYfSectorClient
        client = _LazyYfSectorClient()

    watch = {t for (t,) in db.execute(select(WatchlistItem.ticker)).all()}
    holds = {
        t: s for (t, s) in db.execute(select(Holding.ticker, Holding.sector)).all()
    }
    universe = sorted(watch | set(holds))

    overrides = load_sector_overrides()
    cache = dict(load_sector_cache())

    already = resolved = failed = 0
    for t in universe:
        if holds.get(t) or t in overrides or t in cache:
            already += 1
            continue
        sector = None
        try:
            sector = client.get_sector(t)
        except Exception as exc:  # never crash the job
            log.warning("sector_refresh_fetch_failed", ticker=t, error=str(exc))
        if sector:
            cache[t] = sector
            resolved += 1
        else:
            failed += 1

    if resolved:
        save_sector_cache(cache)
    log.info("sector_refresh_done", universe=len(universe), already=already,
             resolved=resolved, failed=failed)
    return SectorRefreshSummary(len(universe), already, resolved, failed)
```

Confirm `marketpulse.logging.get_logger` is the correct import (match what `scheduler/jobs.py` uses; adjust if it's `structlog.get_logger`).

- [ ] **Step 4: Run → PASS**

Run: `uv run pytest tests/scheduler/test_sector_refresh.py -v`

- [ ] **Step 5: Lint + commit**

Run: `uv run ruff check marketpulse/scheduler/sector_refresh.py tests/scheduler/test_sector_refresh.py`
```bash
git add marketpulse/scheduler/sector_refresh.py tests/scheduler/test_sector_refresh.py
git commit -m "feat(sector): warmup that fetches+persists sectors for watchlist universe"
```

---

## Task 5: Schedule the warmup job + manual CLI (P2)

**Files:**
- Modify: `marketpulse/scheduler/jobs.py` (add `run_sector_cache_refresh()` composition root + register in `build_scheduler()`)
- Create: `marketpulse/cli/refresh_sectors.py` (manual one-off entrypoint)
- Test: `tests/scheduler/test_run_sector_cache_refresh.py` (create)

- [ ] **Step 1: Write the failing test** (composition root closes the session, never raises)

```python
# Layer: test
from unittest.mock import MagicMock

import marketpulse.scheduler.jobs as jobs_mod


def test_run_sector_cache_refresh_closes_db(monkeypatch):
    fake_db = MagicMock()
    monkeypatch.setattr(jobs_mod, "session_scope", lambda: iter([fake_db]))
    called = {}
    monkeypatch.setattr(jobs_mod, "refresh_sector_cache",
                        lambda db, **kw: called.setdefault("ran", True))
    jobs_mod.run_sector_cache_refresh()
    assert called.get("ran") is True
    fake_db.close.assert_called()  # or generator teardown, match the file's pattern
```

Match the exact session/teardown idiom used by `run_eval_analysis_job` (gen/db None-init + `contextlib.suppress` teardown).

- [ ] **Step 2: Run → FAIL**

Run: `uv run pytest tests/scheduler/test_run_sector_cache_refresh.py -v`

- [ ] **Step 3: Implement** in `marketpulse/scheduler/jobs.py`

Import: `from marketpulse.scheduler.sector_refresh import refresh_sector_cache`.
Add a composition-root job mirroring `run_eval_analysis_job`'s session/teardown shape:

```python
def run_sector_cache_refresh() -> None:
    """Daily: warm the persistent sector cache from yfinance for the
    watchlist∪holdings universe. Best-effort; never raises."""
    gen = db = None
    try:
        gen = session_scope()
        db = next(gen)
        refresh_sector_cache(db)
    except Exception as exc:
        log.warning("sector_cache_refresh_failed", error=str(exc))
    finally:
        if gen is not None:
            with contextlib.suppress(StopIteration):
                next(gen)
```

Register in `build_scheduler()` (run a bit before the 21:00 UTC eval job so fresh sectors are available; daily, all week):

```python
    sched.add_job(
        run_sector_cache_refresh,
        trigger=CronTrigger(hour=20, minute=45),  # UTC, daily
        id="sector_cache_refresh",
        misfire_grace_time=None,
        coalesce=True,
    )
```

- [ ] **Step 4: Implement the manual CLI** `marketpulse/cli/refresh_sectors.py`

```python
# Layer: cli
"""Manual one-off: python -m marketpulse.cli.refresh_sectors"""
from __future__ import annotations

from marketpulse.db.base import session_scope
from marketpulse.scheduler.sector_refresh import refresh_sector_cache


def main() -> None:
    gen = session_scope()
    db = next(gen)
    try:
        s = refresh_sector_cache(db)
        print(f"universe={s.universe} already={s.already} resolved={s.resolved} failed={s.failed}")
    finally:
        from contextlib import suppress
        with suppress(StopIteration):
            next(gen)


if __name__ == "__main__":
    main()
```
(Match the real `session_scope` import path used elsewhere — confirm it's `marketpulse.db.base`.)

- [ ] **Step 5: Run → PASS + lint**

Run: `uv run pytest tests/scheduler/test_run_sector_cache_refresh.py -v`
Run: `uv run ruff check marketpulse/scheduler/jobs.py marketpulse/cli/refresh_sectors.py`

- [ ] **Step 6: Commit**

```bash
git add marketpulse/scheduler/jobs.py marketpulse/cli/refresh_sectors.py tests/scheduler/test_run_sector_cache_refresh.py
git commit -m "feat(sector): daily warmup job + manual CLI (python -m marketpulse.cli.refresh_sectors)"
```

---

## Task 6: Final integration

- [ ] **Step 1: Full suite**

Run: `uv run pytest -q`
Expected: all pass except the pre-existing env-dependent `test_charter_route::test_endpoint_returns_200_with_no_backup_dir` (unrelated).

- [ ] **Step 2: Lint repo-wide**

Run: `uv run ruff check .` → clean.

- [ ] **Step 3: Smoke the warmup locally (optional, needs network)**

Run: `uv run python -m marketpulse.cli.refresh_sectors`
Expected: prints a summary; `sector_cache.json` written at the resolved path.

---

## Post-deploy (ops, not code)

After merge + deploy, run the warmup once on prod so the cache fills immediately (otherwise it fills at the next 20:45 UTC tick):
```
docker exec marketpulse python -m marketpulse.cli.refresh_sectors
```
Then `/watchlist` shows real sectors instead of 16× Uncategorized, and the cache persists on `/data` across future recreates.
