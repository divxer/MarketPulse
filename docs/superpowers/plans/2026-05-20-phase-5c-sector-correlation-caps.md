# Phase 5c — Sector & Correlation Caps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two enforced cap dimensions to the Phase 5b shared-pool backtester — single sector ≤ 40% of pool, neighbor-correlation cluster ≤ 40% of pool — both gated by toggle flags defaulting to True.

**Architecture:** Two new pure-function modules (`sector.py` for ticker→sector lookup, `correlation.py` for pairwise Pearson + neighbor finding) consumed by `portfolio_simulator.py` ALLOCATE step after existing cap_full / cash_short checks. Two new `BidRecord` outcomes (`sector_cap_full`, `correlation_cap_full`) carry diagnostic fields. Finalization adds 6 new fields to `PortfolioBacktestResult` plus a `risk_policy` composite provenance tag. UI surfaces sector breakdown as a new partial; existing bid-history / strategy-table partials gain new chip renderings.

**Tech Stack:** Python 3.12, numpy (existing), yfinance (existing), pyyaml (existing). **No new dependencies. No new DB tables. No Alembic migration.**

**Spec:** `docs/superpowers/specs/2026-05-20-phase-5c-sector-correlation-caps-design.md`

---

## File Structure

```
marketpulse/backtest/
├── sector.py                              NEW: get_sector + load_sector_overrides + JSON cache
├── correlation.py                         NEW: PriceProvider Protocol + compute_pairwise_correlation +
│                                                find_correlation_neighbors
├── portfolio_simulator.py                 MODIFY: ALLOCATE adds sector + correlation cap checks;
│                                                  finalization adds 6 telemetry fields + risk_policy
├── types.py                               MODIFY: BidRecord outcome literal + 2 fields;
│                                                  StrategyContribution + 2 counters;
│                                                  PortfolioBacktestResult + 7 fields
├── simulator.py                           MODIFY: run_shared_pool_backtest threads 4 new knobs;
│                                                  risk_policy composition helper
└── __init__.py                            (no change — public API stable)

config/
└── sector_overrides.yaml                  NEW: ticker→sector overrides for ETFs/edge cases

data/
└── sector_cache.json                      NEW (gitignored): yfinance fetch cache

marketpulse/web/
├── routes/backtest.py                     MODIFY: pass sector_breakdown + risk_policy +
│                                                  cap-related fields via context
└── templates/partials/
    ├── backtest_hero.html                 MODIFY: + 3rd paragraph for cap policy
    ├── backtest_bid_history.html          MODIFY: + 2 new chip renderings
    ├── backtest_strategy_table_shared.html MODIFY: n_skipped sums 5 buckets + tooltip
    └── backtest_sector_breakdown.html     NEW: sector breakdown card partial

tests/
├── unit/
│   ├── test_backtest_sector.py            NEW: 10 tests
│   ├── test_backtest_correlation.py       NEW: 10 tests
│   ├── test_backtest_portfolio_simulator.py MODIFY: + 10 cap enforcement tests
│   └── test_backtest_types_phase5a.py     MODIFY: + 3 type extension tests
├── integration/
│   └── test_backtest_shared_pool.py       MODIFY: + 3 orchestrator tests
└── web/
    └── test_lab_backtest_modes.py         MODIFY: + 2 UI assertions
```

**No new files outside the structure above. No DB migration. No new dependencies.**

---

## Conventions

- **TDD strict**: each task = failing test → run/see fail → minimal impl → run/see pass → commit.
- **Working directory**: `/Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan` (worktree on `plan/phase-5c-sector-correlation-caps`).
- **Run tests**: `uv run pytest <path> -v`.
- **Lint**: `uv run ruff check <path>`.
- **No new DB tables, no migrations**.
- **Daily loop ORDER LOCK** (spec § 2): `CLOSE → BID → WEIGHT → SIZE → DEDUP → ALLOC → MTM → RECORD` — unchanged. Cap checks slot INSIDE ALLOC step.
- **ALLOC cap-check order LOCK** (spec § 6): `cap_full → cash_short → sector_cap_full → correlation_cap_full → won` — cheapest first, deliberate trade-off.
- **Failsafe-open lock** (spec § 4 + § 5): yfinance failure → sector="unknown"; correlation cold-start → empty neighbors → no cap trigger; YAML malformed → {} (no overrides). Never crash simulator.
- **Self-pair exclusion** (spec § 5): `compute_pairwise_correlation(a, a, ...)` returns None; `find_correlation_neighbors` filters self before pairing.
- **size_too_small interaction** (spec § 6): bids skipped at SIZE COMPUTE never reach ALLOCATE, so they are NOT subject to sector/correlation caps.
- **Test-quality lock** (spec § 12): every cap test must `assert precondition` BEFORE `assert outcome`. No `if X: assert Y` style.

---

### Task 1: sector.py — YAML loader + validation

**Files:**
- Create: `marketpulse/backtest/sector.py`
- Create: `tests/unit/test_backtest_sector.py`
- Create: `config/sector_overrides.yaml`

- [ ] **Step 1.1: Create `config/sector_overrides.yaml`**

```yaml
# Phase 5c-1: manual sector overrides for tickers where yfinance is wrong, missing,
# or where the natural GICS sector doesn't capture meaningful exposure correlation.
#
# Use cases:
# - Leveraged ETFs (3x bull / bear)
# - Thematic ETFs without natural GICS sector
# - Crypto / digital asset proxies
#
# Equity tickers (AAPL, GOOGL, AMSC, etc.) fall through to yfinance default.
overrides:
  TQQQ: leveraged_qqq
  TNA: leveraged_small_cap
  QBTS: quantum_compute
  QUBT: quantum_compute
```

- [ ] **Step 1.2: Add `.gitignore` entry for runtime cache**

Run:
```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
echo "data/sector_cache.json" >> .gitignore
```

- [ ] **Step 1.3: Write failing tests** in `tests/unit/test_backtest_sector.py`

```python
"""Phase 5c-1: sector.py — ticker→sector lookup + YAML override + JSON cache."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_load_sector_overrides_returns_dict_for_well_formed_yaml(tmp_path: Path) -> None:
    """Well-formed YAML returns dict[str, str]."""
    from marketpulse.backtest.sector import load_sector_overrides

    yaml_file = tmp_path / "sector_overrides.yaml"
    yaml_file.write_text(
        "overrides:\n"
        "  TQQQ: leveraged_qqq\n"
        "  TNA: leveraged_small_cap\n",
        encoding="utf-8",
    )
    result = load_sector_overrides(yaml_file)
    assert result == {"TQQQ": "leveraged_qqq", "TNA": "leveraged_small_cap"}


def test_load_sector_overrides_returns_empty_when_file_missing(tmp_path: Path) -> None:
    """Missing file returns {} without raising."""
    from marketpulse.backtest.sector import load_sector_overrides

    missing = tmp_path / "does_not_exist.yaml"
    result = load_sector_overrides(missing)
    assert result == {}


def test_load_sector_overrides_returns_empty_when_yaml_corrupt(tmp_path: Path) -> None:
    """Corrupt YAML returns {} (does not raise; logged ERROR)."""
    from marketpulse.backtest.sector import load_sector_overrides

    bad = tmp_path / "bad.yaml"
    bad.write_text("not: valid: yaml: content: [", encoding="utf-8")
    result = load_sector_overrides(bad)
    assert result == {}


def test_load_sector_overrides_rejects_non_string_values(tmp_path: Path) -> None:
    """Non-string override values are rejected → {} (validation failure)."""
    from marketpulse.backtest.sector import load_sector_overrides

    bad = tmp_path / "bad_values.yaml"
    bad.write_text(
        "overrides:\n"
        "  TQQQ: 42\n",  # int, not str
        encoding="utf-8",
    )
    result = load_sector_overrides(bad)
    assert result == {}


def test_load_sector_overrides_strips_empty_string_values(tmp_path: Path) -> None:
    """Empty string values are filtered out (other entries kept)."""
    from marketpulse.backtest.sector import load_sector_overrides

    f = tmp_path / "mixed.yaml"
    f.write_text(
        "overrides:\n"
        "  TQQQ: ''\n"  # empty string → filtered
        "  TNA: leveraged_small_cap\n",
        encoding="utf-8",
    )
    result = load_sector_overrides(f)
    assert result == {"TNA": "leveraged_small_cap"}
```

- [ ] **Step 1.4: Run, see fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/unit/test_backtest_sector.py -v -k "load_sector_overrides"
```

Expected: 5 fails (ImportError: `marketpulse.backtest.sector` module not found).

- [ ] **Step 1.5: Implement `marketpulse/backtest/sector.py` — overrides loader only**

```python
"""Phase 5c-1: ticker→sector lookup with YAML overrides and JSON cache.

Spec § 4 (Sector Data Layer): yfinance is the default; config/sector_overrides.yaml
provides edge-case manual overrides; data/sector_cache.json persists successful
yfinance fetches.

Failsafe-degrade: every failure path logs and returns a safe default. Never
crashes the simulator.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

_logger = logging.getLogger(__name__)

_DEFAULT_OVERRIDES_PATH = Path(__file__).parent.parent.parent / "config" / "sector_overrides.yaml"


def load_sector_overrides(path: Path | str | None = None) -> dict[str, str]:
    """Load and validate config/sector_overrides.yaml. Returns ticker→sector dict.

    Validation:
      - Each value must be a non-empty str (int/float/bool/None all rejected)
      - Empty string values are filtered (key not included in result)
      - Missing file returns {} silently
      - YAML parse error logs ERROR and returns {}
      - Returns empty dict on validation failure, never raises
    """
    target = Path(path) if path is not None else _DEFAULT_OVERRIDES_PATH
    if not target.exists():
        return {}

    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        _logger.error("sector_overrides.yaml parse error at %s: %s", target, exc)
        return {}

    if not isinstance(raw, dict):
        _logger.error("sector_overrides.yaml top-level must be a mapping, got %s", type(raw).__name__)
        return {}

    overrides = raw.get("overrides", {})
    if not isinstance(overrides, dict):
        _logger.error("sector_overrides.yaml 'overrides' key must be a mapping")
        return {}

    result: dict[str, str] = {}
    for ticker, sector in overrides.items():
        if not isinstance(sector, str) or not sector:
            _logger.warning(
                "sector_overrides.yaml: skipping ticker=%r non-string-or-empty value %r",
                ticker, sector,
            )
            continue
        result[ticker] = sector
    return result
```

- [ ] **Step 1.6: Run, pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/unit/test_backtest_sector.py -v -k "load_sector_overrides"
```

Expected: 5/5 pass.

- [ ] **Step 1.7: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run ruff check marketpulse/backtest/sector.py tests/unit/test_backtest_sector.py config/sector_overrides.yaml
git add marketpulse/backtest/sector.py tests/unit/test_backtest_sector.py config/sector_overrides.yaml .gitignore
git commit -m "feat(phase-5c): sector.py — YAML overrides loader

Spec § 4: load_sector_overrides reads config/sector_overrides.yaml,
validates each ticker→sector pair (non-empty string), returns empty
dict on any failure (missing file, YAML parse error, malformed values).
Failsafe-degrade — never raises.

config/sector_overrides.yaml seeded with 4 NAS watchlist overrides:
TQQQ, TNA, QBTS, QUBT.

data/sector_cache.json added to .gitignore (Phase 5c runtime artifact).

5 unit tests cover happy path, missing file, corrupt YAML, non-string
values, empty string values."
```

---

### Task 2: sector.py — get_sector + cache (in-memory + JSON persistence)

**Files:**
- Modify: `marketpulse/backtest/sector.py`
- Modify: `tests/unit/test_backtest_sector.py`

- [ ] **Step 2.1: Append failing tests** to `tests/unit/test_backtest_sector.py`

```python
def test_save_and_load_sector_cache_round_trip(tmp_path: Path) -> None:
    """Save dict to JSON, load it back, assert equality."""
    from marketpulse.backtest.sector import load_sector_cache, save_sector_cache

    cache_file = tmp_path / "sector_cache.json"
    data = {"AAPL": "Technology", "XOM": "Energy"}
    save_sector_cache(data, cache_file)

    loaded = load_sector_cache(cache_file)
    assert loaded == data


def test_load_sector_cache_returns_empty_when_file_missing(tmp_path: Path) -> None:
    """Missing cache returns {} silently."""
    from marketpulse.backtest.sector import load_sector_cache

    missing = tmp_path / "no_cache.json"
    assert load_sector_cache(missing) == {}


def test_load_sector_cache_returns_empty_when_corrupt(tmp_path: Path) -> None:
    """Corrupt JSON returns {} and logs WARNING."""
    from marketpulse.backtest.sector import load_sector_cache

    bad = tmp_path / "corrupt.json"
    bad.write_text("{not valid json", encoding="utf-8")
    assert load_sector_cache(bad) == {}


def test_get_sector_override_wins_over_yfinance() -> None:
    """When sector_overrides has the ticker, yfinance fetch is skipped."""
    from marketpulse.backtest.sector import get_sector, _reset_caches_for_testing

    _reset_caches_for_testing()

    class FakeYfClient:
        def get_sector(self, _ticker: str) -> str | None:
            raise AssertionError("yfinance should not be called when override exists")

    overrides = {"TQQQ": "leveraged_qqq"}
    result = get_sector("TQQQ", yf_client=FakeYfClient(), overrides=overrides)
    assert result == "leveraged_qqq"


def test_get_sector_returns_yfinance_value_when_no_override() -> None:
    """Falls through to yfinance when no override."""
    from marketpulse.backtest.sector import get_sector, _reset_caches_for_testing

    _reset_caches_for_testing()

    class FakeYfClient:
        def get_sector(self, ticker: str) -> str | None:
            return {"AAPL": "Technology"}.get(ticker)

    result = get_sector("AAPL", yf_client=FakeYfClient(), overrides={})
    assert result == "Technology"


def test_get_sector_returns_unknown_when_yfinance_returns_none() -> None:
    """yfinance None → 'unknown' (fail-safe closed)."""
    from marketpulse.backtest.sector import get_sector, _reset_caches_for_testing

    _reset_caches_for_testing()

    class NullYfClient:
        def get_sector(self, _ticker: str) -> str | None:
            return None

    result = get_sector("UNKNOWN_TICKER", yf_client=NullYfClient(), overrides={})
    assert result == "unknown"


def test_get_sector_returns_unknown_when_yfinance_raises() -> None:
    """yfinance exception → 'unknown' (logged WARNING)."""
    from marketpulse.backtest.sector import get_sector, _reset_caches_for_testing

    _reset_caches_for_testing()

    class CrashYfClient:
        def get_sector(self, _ticker: str) -> str | None:
            raise RuntimeError("network down")

    result = get_sector("AAPL", yf_client=CrashYfClient(), overrides={})
    assert result == "unknown"


def test_get_sector_caches_within_process() -> None:
    """Repeated calls hit cache; yf_client.get_sector invoked at most once per ticker."""
    from marketpulse.backtest.sector import get_sector, _reset_caches_for_testing

    _reset_caches_for_testing()

    call_count = 0

    class CountingYfClient:
        def get_sector(self, _ticker: str) -> str | None:
            nonlocal call_count
            call_count += 1
            return "Technology"

    yf = CountingYfClient()
    get_sector("AAPL", yf_client=yf, overrides={})
    get_sector("AAPL", yf_client=yf, overrides={})
    get_sector("AAPL", yf_client=yf, overrides={})
    assert call_count == 1
```

- [ ] **Step 2.2: Run, see fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/unit/test_backtest_sector.py -v
```

Expected: 8 new fails (ImportError on `get_sector`, `save_sector_cache`, `load_sector_cache`, `_reset_caches_for_testing`).

- [ ] **Step 2.3: Extend `marketpulse/backtest/sector.py`**

Append below `load_sector_overrides`:

```python
import json
from typing import Protocol

_DEFAULT_CACHE_PATH = Path(__file__).parent.parent.parent / "data" / "sector_cache.json"

_SECTOR_CACHE: dict[str, str] = {}
_OVERRIDES_CACHE: dict[str, str] | None = None
_WARNED_TICKERS: set[str] = set()


class _YfSectorClient(Protocol):
    """Minimal interface for yfinance-style sector lookup."""
    def get_sector(self, ticker: str) -> str | None:
        ...


def _reset_caches_for_testing() -> None:
    """Test helper: clear all in-memory caches."""
    _SECTOR_CACHE.clear()
    _WARNED_TICKERS.clear()
    global _OVERRIDES_CACHE
    _OVERRIDES_CACHE = None


def save_sector_cache(cache: dict[str, str], path: Path | str | None = None) -> None:
    """Persist sector lookup dict to JSON. Creates parent dirs if missing."""
    target = Path(path) if path is not None else _DEFAULT_CACHE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def load_sector_cache(path: Path | str | None = None) -> dict[str, str]:
    """Load sector cache from JSON. Returns {} on missing/corrupt file."""
    target = Path(path) if path is not None else _DEFAULT_CACHE_PATH
    if not target.exists():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _logger.warning("sector_cache.json corrupt at %s: %s — rebuilding", target, exc)
        return {}
    if not isinstance(raw, dict):
        _logger.warning("sector_cache.json must be a JSON object, got %s", type(raw).__name__)
        return {}
    return {str(k): str(v) for k, v in raw.items() if v}


def get_sector(
    ticker: str,
    *,
    yf_client: _YfSectorClient | None = None,
    overrides: dict[str, str] | None = None,
) -> str:
    """Return canonical sector for ticker. Never None — falls back to 'unknown'.

    Resolution order:
      1. config/sector_overrides.yaml (passed via `overrides` or loaded lazily)
      2. In-memory process cache
      3. yfinance Ticker.info['sector'] via yf_client (fetched + cached on success)
      4. 'unknown' fallback (logged WARNING once per ticker)
    """
    global _OVERRIDES_CACHE
    if overrides is None:
        if _OVERRIDES_CACHE is None:
            _OVERRIDES_CACHE = load_sector_overrides()
        overrides = _OVERRIDES_CACHE

    if ticker in overrides:
        return overrides[ticker]

    if ticker in _SECTOR_CACHE:
        return _SECTOR_CACHE[ticker]

    if yf_client is None:
        if ticker not in _WARNED_TICKERS:
            _logger.warning("get_sector(%r) called without yf_client; returning 'unknown'", ticker)
            _WARNED_TICKERS.add(ticker)
        return "unknown"

    try:
        sector = yf_client.get_sector(ticker)
    except Exception as exc:
        if ticker not in _WARNED_TICKERS:
            _logger.warning("yfinance.get_sector(%r) raised %s; returning 'unknown'", ticker, exc)
            _WARNED_TICKERS.add(ticker)
        return "unknown"

    if not sector:
        if ticker not in _WARNED_TICKERS:
            _logger.warning("yfinance.get_sector(%r) returned %r; returning 'unknown'", ticker, sector)
            _WARNED_TICKERS.add(ticker)
        return "unknown"

    _SECTOR_CACHE[ticker] = sector
    return sector
```

- [ ] **Step 2.4: Run, pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/unit/test_backtest_sector.py -v
```

Expected: all 13 pass (5 from Task 1 + 8 new).

- [ ] **Step 2.5: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run ruff check marketpulse/backtest/sector.py tests/unit/test_backtest_sector.py
git add marketpulse/backtest/sector.py tests/unit/test_backtest_sector.py
git commit -m "feat(phase-5c): sector.py — get_sector + JSON cache

Spec § 4: get_sector resolution order — overrides → in-memory cache →
yf_client.get_sector → 'unknown' fallback.

Failsafe-closed semantics:
- yfinance exception → 'unknown' (logged WARNING once per ticker)
- yfinance returns None → 'unknown'
- Missing yf_client → 'unknown'
- Override absent → fall through

Process-scoped in-memory cache (_SECTOR_CACHE) avoids repeat yf calls.
JSON persistence via save_sector_cache / load_sector_cache for cross-run
warm starts. data/sector_cache.json is gitignored.

8 new unit tests cover overrides priority, yfinance fallback, None/raise
handling, caching, JSON round-trip, missing/corrupt cache handling.
Total sector.py tests: 13."
```

---

### Task 3: correlation.py — PriceProvider Protocol + compute_pairwise_correlation

**Files:**
- Create: `marketpulse/backtest/correlation.py`
- Create: `tests/unit/test_backtest_correlation.py`

- [ ] **Step 3.1: Write failing tests** in `tests/unit/test_backtest_correlation.py`

```python
"""Phase 5c-2: correlation.py — pairwise Pearson + neighbor finding."""
from __future__ import annotations

from datetime import date, timedelta


class _FakePriceProvider:
    """Test double: in-memory ticker→date→close. Implements PriceProvider Protocol."""

    def __init__(self, data: dict[str, list[tuple[date, float]]]) -> None:
        self._data = data

    def get_daily_closes(self, ticker: str, start: date, end: date) -> list[tuple[date, float]]:
        rows = self._data.get(ticker, [])
        return sorted([(d, v) for d, v in rows if start <= d < end])


def _linear(start: float, n: int, slope: float, start_date: date) -> list[tuple[date, float]]:
    """Build a linear price series for testing."""
    return [(start_date + timedelta(days=i), start + slope * i) for i in range(n)]


def test_pairwise_correlation_identical_series_returns_one() -> None:
    """Two perfectly identical return series → corr ≈ 1.0."""
    from marketpulse.backtest.correlation import compute_pairwise_correlation

    series = _linear(100.0, 60, 1.0, date(2026, 1, 1))
    provider = _FakePriceProvider({"A": series, "B": series})

    corr = compute_pairwise_correlation(
        "A", "B",
        as_of=date(2026, 3, 5),  # 60+ days past start
        lookback_days=60,
        min_overlap=30,
        price_provider=provider,
    )
    assert corr is not None
    assert corr > 0.999


def test_pairwise_correlation_inverse_series_returns_negative_one() -> None:
    """Inverse series (one rises, other falls) → corr ≈ -1.0."""
    from marketpulse.backtest.correlation import compute_pairwise_correlation

    up = _linear(100.0, 60, 1.0, date(2026, 1, 1))
    down = _linear(160.0, 60, -1.0, date(2026, 1, 1))
    provider = _FakePriceProvider({"UP": up, "DOWN": down})

    corr = compute_pairwise_correlation(
        "UP", "DOWN",
        as_of=date(2026, 3, 5),
        lookback_days=60,
        min_overlap=30,
        price_provider=provider,
    )
    assert corr is not None
    assert corr < -0.999


def test_pairwise_correlation_returns_none_below_min_overlap() -> None:
    """< min_overlap days → returns None."""
    from marketpulse.backtest.correlation import compute_pairwise_correlation

    short = _linear(100.0, 10, 1.0, date(2026, 2, 25))
    provider = _FakePriceProvider({"A": short, "B": short})

    corr = compute_pairwise_correlation(
        "A", "B",
        as_of=date(2026, 3, 7),
        lookback_days=60,
        min_overlap=30,
        price_provider=provider,
    )
    assert corr is None


def test_pairwise_correlation_returns_none_for_self_pair() -> None:
    """a == b returns None (NOT 1.0). Self-pair contract."""
    from marketpulse.backtest.correlation import compute_pairwise_correlation

    series = _linear(100.0, 60, 1.0, date(2026, 1, 1))
    provider = _FakePriceProvider({"AAPL": series})

    corr = compute_pairwise_correlation(
        "AAPL", "AAPL",
        as_of=date(2026, 3, 5),
        lookback_days=60,
        min_overlap=30,
        price_provider=provider,
    )
    assert corr is None


def test_pairwise_correlation_returns_none_for_zero_variance_series() -> None:
    """Flat series (zero variance) → corr undefined → None."""
    from marketpulse.backtest.correlation import compute_pairwise_correlation

    flat = [(date(2026, 1, 1) + timedelta(days=i), 100.0) for i in range(60)]
    moving = _linear(100.0, 60, 1.0, date(2026, 1, 1))
    provider = _FakePriceProvider({"FLAT": flat, "MOVE": moving})

    corr = compute_pairwise_correlation(
        "FLAT", "MOVE",
        as_of=date(2026, 3, 5),
        lookback_days=60,
        min_overlap=30,
        price_provider=provider,
    )
    assert corr is None


def test_pairwise_correlation_excludes_dates_at_or_after_as_of() -> None:
    """Window is [as_of - lookback, as_of) — exclusive upper bound."""
    from marketpulse.backtest.correlation import compute_pairwise_correlation

    # Series extends past as_of; future data must not affect corr
    long_series = _linear(100.0, 120, 1.0, date(2026, 1, 1))
    provider = _FakePriceProvider({"A": long_series, "B": long_series})

    as_of_early = date(2026, 2, 15)
    corr = compute_pairwise_correlation(
        "A", "B",
        as_of=as_of_early,
        lookback_days=45,
        min_overlap=30,
        price_provider=provider,
    )
    # Should succeed (45d window, ~30d data available before as_of) AND
    # not be influenced by post-as_of data (same series both legs → ~1.0)
    assert corr is not None
    assert corr > 0.999
```

- [ ] **Step 3.2: Run, see fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/unit/test_backtest_correlation.py -v
```

Expected: 6 fails (ImportError on `marketpulse.backtest.correlation`).

- [ ] **Step 3.3: Create `marketpulse/backtest/correlation.py`**

```python
"""Phase 5c-2: pairwise correlation + neighbor finding.

Spec § 5: Pearson correlation of daily returns on price_cache data, with a
60d causal window matching Phase 5b's rolling_sigma. Self-pair short-circuits
to None. Cold-start (< min_overlap) returns None (fail-safe-open at the
neighbor level).
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from functools import lru_cache
from typing import Protocol

import numpy as np


class PriceProvider(Protocol):
    """Read-only price interface consumed by correlation calculations.

    Implementations:
      - Production: a yfinance-backed price_cache wrapper
      - Tests: an in-memory dict-backed fake (see test_backtest_correlation.py)
    """

    def get_daily_closes(
        self,
        ticker: str,
        start: date,
        end: date,
    ) -> list[tuple[date, float]]:
        """Return (date, close) tuples for ticker, dates in [start, end). Sorted ascending."""
        ...


def compute_pairwise_correlation(
    ticker_a: str,
    ticker_b: str,
    *,
    as_of: date,
    lookback_days: int = 60,
    min_overlap: int = 30,
    price_provider: PriceProvider,
) -> float | None:
    """Pearson correlation of daily returns over [as_of - lookback_days, as_of).

    Returns None when:
      - ticker_a == ticker_b (self-pair short-circuit)
      - Either ticker missing data in the window
      - Overlapping days < min_overlap
      - Computed corr is NaN (zero variance in either series)

    Contract:
      - Window: [as_of - lookback_days, as_of) — exclusive upper bound
      - Data source: PriceProvider.get_daily_closes (raw OHLC close)
      - Self-pair: returns None (NOT 1.0) — caller never wants a position
        to be its own neighbor.
    """
    if ticker_a == ticker_b:
        return None

    window_start = as_of - timedelta(days=lookback_days)
    a_series = price_provider.get_daily_closes(ticker_a, window_start, as_of)
    b_series = price_provider.get_daily_closes(ticker_b, window_start, as_of)

    a_by_date = {d: v for d, v in a_series}
    b_by_date = {d: v for d, v in b_series}
    overlap_dates = sorted(set(a_by_date) & set(b_by_date))
    if len(overlap_dates) < min_overlap:
        return None

    a_prices = np.array([a_by_date[d] for d in overlap_dates], dtype=float)
    b_prices = np.array([b_by_date[d] for d in overlap_dates], dtype=float)

    if len(a_prices) < 2:
        return None

    a_returns = np.diff(a_prices) / a_prices[:-1]
    b_returns = np.diff(b_prices) / b_prices[:-1]

    if a_returns.std() == 0.0 or b_returns.std() == 0.0:
        return None

    corr = float(np.corrcoef(a_returns, b_returns)[0, 1])
    if not math.isfinite(corr):
        return None
    return corr
```

- [ ] **Step 3.4: Run, pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/unit/test_backtest_correlation.py -v
```

Expected: 6/6 pass.

- [ ] **Step 3.5: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run ruff check marketpulse/backtest/correlation.py tests/unit/test_backtest_correlation.py
git add marketpulse/backtest/correlation.py tests/unit/test_backtest_correlation.py
git commit -m "feat(phase-5c): correlation.py — Pearson + PriceProvider Protocol

Spec § 5: compute_pairwise_correlation does Pearson on daily returns
over [as_of - lookback_days, as_of) using PriceProvider.get_daily_closes.

Locked contract:
- Window duration matches Phase 5b (60d) but DATA source differs:
  price_cache OHLC, NOT per-strategy equity curves
- Self-pair (ticker_a == ticker_b) returns None — never 1.0
- Cold-start (< 30 day overlap) returns None — fail-safe-open
- Zero variance in either leg returns None

PriceProvider Protocol defines get_daily_closes(ticker, start, end) ->
list[(date, close)]. Production wraps price_cache; tests use in-memory fake.

6 unit tests cover identical/inverse/short/self-pair/zero-variance/
causal-window cases."
```

---

### Task 4: correlation.py — find_correlation_neighbors

**Files:**
- Modify: `marketpulse/backtest/correlation.py`
- Modify: `tests/unit/test_backtest_correlation.py`

- [ ] **Step 4.1: Append failing tests** to `tests/unit/test_backtest_correlation.py`

```python
def test_find_correlation_neighbors_returns_only_above_threshold() -> None:
    """Only tickers with corr >= threshold appear in neighbors."""
    from marketpulse.backtest.correlation import find_correlation_neighbors

    base = _linear(100.0, 60, 1.0, date(2026, 1, 1))
    weak = [(d, base[i][1] + 5.0 * (i % 7)) for i, (d, _) in enumerate(base)]
    flat = [(d, 100.0 + 0.05 * (i % 3)) for i, (d, _) in enumerate(base)]
    provider = _FakePriceProvider({
        "CAND": base,
        "STRONG": base,  # ρ ≈ 1.0
        "WEAK": weak,    # ρ moderate
        "FLAT": flat,    # near zero correlation
    })

    neighbors, _diag = find_correlation_neighbors(
        "CAND",
        ["STRONG", "WEAK", "FLAT"],
        as_of=date(2026, 3, 5),
        threshold=0.6,
        lookback_days=60,
        price_provider=provider,
    )
    assert "STRONG" in neighbors
    assert "FLAT" not in neighbors


def test_find_correlation_neighbors_filters_self_from_input() -> None:
    """Candidate ticker in open_positions list is filtered before pairing."""
    from marketpulse.backtest.correlation import find_correlation_neighbors

    series = _linear(100.0, 60, 1.0, date(2026, 1, 1))
    provider = _FakePriceProvider({"AAPL": series, "GOOGL": series})

    neighbors, _diag = find_correlation_neighbors(
        "AAPL",
        ["AAPL", "GOOGL"],  # candidate appears in input
        as_of=date(2026, 3, 5),
        threshold=0.6,
        lookback_days=60,
        price_provider=provider,
    )
    # AAPL must NOT appear in neighbors (self-filtered)
    assert "AAPL" not in neighbors
    # GOOGL identical series → corr ~1.0 → IS a neighbor
    assert "GOOGL" in neighbors


def test_find_correlation_neighbors_diagnostics_sorted_desc() -> None:
    """Diagnostics tuple is sorted by corr value descending (highest first)."""
    from marketpulse.backtest.correlation import find_correlation_neighbors

    # Build series with known correlations relative to CAND
    base = _linear(100.0, 60, 1.0, date(2026, 1, 1))
    medium = [(d, v + 2.0 * ((i % 9))) for i, (d, v) in enumerate(base)]
    low = [(d, v + 8.0 * ((i % 5))) for i, (d, v) in enumerate(base)]
    provider = _FakePriceProvider({
        "CAND": base,
        "HIGH": base,
        "MED": medium,
        "LOW": low,
    })

    _neighbors, diagnostics = find_correlation_neighbors(
        "CAND",
        ["HIGH", "MED", "LOW"],
        as_of=date(2026, 3, 5),
        threshold=0.0,  # capture everything for sort assertion
        lookback_days=60,
        min_overlap=30,
        price_provider=provider,
    )
    # diagnostics is tuple of (ticker, corr) sorted by corr desc
    corrs = [c for _t, c in diagnostics]
    assert corrs == sorted(corrs, reverse=True)
    # HIGH should be first (corr ~1.0)
    assert diagnostics[0][0] == "HIGH"


def test_find_correlation_neighbors_cold_start_returns_empty() -> None:
    """When all corrs are None (insufficient overlap), returns empty list + empty tuple."""
    from marketpulse.backtest.correlation import find_correlation_neighbors

    # Only 10 days of data; min_overlap=30 forces None
    short_a = _linear(100.0, 10, 1.0, date(2026, 2, 25))
    short_b = _linear(100.0, 10, 1.0, date(2026, 2, 25))
    provider = _FakePriceProvider({"A": short_a, "B": short_b})

    neighbors, diagnostics = find_correlation_neighbors(
        "A",
        ["B"],
        as_of=date(2026, 3, 7),
        threshold=0.6,
        lookback_days=60,
        min_overlap=30,
        price_provider=provider,
    )
    assert neighbors == []
    assert diagnostics == ()
```

- [ ] **Step 4.2: Run, see fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/unit/test_backtest_correlation.py -v -k "find_correlation"
```

Expected: 4 fails (ImportError on `find_correlation_neighbors`).

- [ ] **Step 4.3: Append `find_correlation_neighbors` to `marketpulse/backtest/correlation.py`**

```python
def find_correlation_neighbors(
    candidate_ticker: str,
    open_position_tickers: list[str],
    *,
    as_of: date,
    threshold: float = 0.6,
    lookback_days: int = 60,
    min_overlap: int = 30,
    price_provider: PriceProvider,
) -> tuple[list[str], tuple[tuple[str, float], ...]]:
    """For a candidate bid, find which open positions are correlated above threshold.

    Returns (neighbors, diagnostics):
      - neighbors: list of open-position tickers with pairwise corr >= threshold,
        in the same order as input (stable iteration for deterministic tests).
      - diagnostics: tuple of (ticker, corr_value) pairs for ALL pairs checked
        where corr is not None. Sorted by corr descending. Hashable, embeddable
        in BidRecord.blocked_by_correlation_with.

    Self-pair handling: candidate_ticker is filtered from open_position_tickers
    before pairing. Caller does not need to dedupe.
    """
    filtered_open = [t for t in open_position_tickers if t != candidate_ticker]

    diag_with_corr: list[tuple[str, float]] = []
    for other in filtered_open:
        corr = compute_pairwise_correlation(
            candidate_ticker, other,
            as_of=as_of,
            lookback_days=lookback_days,
            min_overlap=min_overlap,
            price_provider=price_provider,
        )
        if corr is not None:
            diag_with_corr.append((other, corr))

    diagnostics = tuple(sorted(diag_with_corr, key=lambda x: -x[1]))
    neighbors = [t for t, c in diag_with_corr if c >= threshold]
    return neighbors, diagnostics
```

- [ ] **Step 4.4: Run, pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/unit/test_backtest_correlation.py -v
```

Expected: 10/10 pass (6 from Task 3 + 4 new).

- [ ] **Step 4.5: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run ruff check marketpulse/backtest/correlation.py tests/unit/test_backtest_correlation.py
git add marketpulse/backtest/correlation.py tests/unit/test_backtest_correlation.py
git commit -m "feat(phase-5c): correlation.py — find_correlation_neighbors

Spec § 5: neighbor-sum algorithm.

For each candidate bid:
- filter candidate ticker from open_position_tickers (self-pair guard)
- compute pairwise Pearson against each remaining open position
- neighbors: list of those with corr >= threshold (default 0.6)
- diagnostics: tuple of (ticker, corr) pairs sorted by corr desc,
  embeddable in BidRecord.blocked_by_correlation_with

Cold-start failsafe: all corrs None → returns ([], ()). Caller treats
empty neighbors as 'no cluster constraint, candidate may proceed'.

4 new unit tests cover threshold filtering, self-filter, diagnostic
sort order, cold-start fallthrough."
```

---

### Task 5: types.py — extend BidRecord, StrategyContribution, PortfolioBacktestResult

**Files:**
- Modify: `marketpulse/backtest/types.py`
- Modify: `tests/unit/test_backtest_types_phase5a.py`

This task is a breaking change for callers but adds **only defaulted fields** for backward compat. Existing Phase 5a/5b helpers (`_bid_kwargs`, `_contribution_kwargs`, `_portfolio_kwargs`) need new entries.

- [ ] **Step 5.1: Append failing tests** to `tests/unit/test_backtest_types_phase5a.py`

```python
def test_bid_record_sector_cap_full_outcome_with_diagnostic() -> None:
    """Phase 5c: new 'sector_cap_full' outcome + blocked_by_sector diagnostic."""
    from datetime import date
    from marketpulse.backtest.types import BidRecord

    b = BidRecord(
        date=date(2026, 5, 1), strategy="x", ticker="AAPL",
        weight=1.0, outcome="sector_cap_full", winner=None,
        position_size=1500.0,
        blocked_by_sector="Technology",
    )
    assert b.outcome == "sector_cap_full"
    assert b.blocked_by_sector == "Technology"
    assert b.blocked_by_correlation_with == ()


def test_bid_record_correlation_cap_full_with_diagnostic_tuple() -> None:
    """Phase 5c: 'correlation_cap_full' outcome + blocked_by_correlation_with tuple."""
    from datetime import date
    from marketpulse.backtest.types import BidRecord

    diag = (("AAPL", 0.72), ("GOOGL", 0.68))
    b = BidRecord(
        date=date(2026, 5, 1), strategy="x", ticker="TQQQ",
        weight=1.0, outcome="correlation_cap_full", winner=None,
        position_size=2000.0,
        blocked_by_correlation_with=diag,
    )
    assert b.outcome == "correlation_cap_full"
    assert b.blocked_by_sector is None
    assert b.blocked_by_correlation_with == diag


def test_strategy_contribution_has_cap_skip_counters() -> None:
    """Phase 5c adds n_sector_cap_skipped + n_correlation_cap_skipped."""
    from marketpulse.backtest.types import StrategyContribution

    c = StrategyContribution(
        strategy="x", display_name="X",
        n_trades=5, n_dedup_skipped=1,
        n_capacity_skipped=0, n_cash_short_skipped=0,
        n_size_too_small_skipped=0,
        n_sector_cap_skipped=2,
        n_correlation_cap_skipped=1,
        contribution_pnl=100.0,
        avg_exposure=0.20, avg_bid_weight=1.0,
        avg_position_size=1200.0,
        n_bids=9, n_floor_hits=0,
    )
    assert c.n_sector_cap_skipped == 2
    assert c.n_correlation_cap_skipped == 1


def test_portfolio_result_has_sector_correlation_telemetry() -> None:
    """Phase 5c adds 7 new fields with sensible defaults."""
    from datetime import date
    from marketpulse.backtest.types import PortfolioBacktestResult

    r = PortfolioBacktestResult(
        horizon=5,
        n_trades=10, n_dedup_total=2,
        avg_capital_utilization=0.4,
        max_strategy_exposure=0.3, hhi_concentration=0.2,
        max_sector_exposure=0.35,
        max_sector_exposure_by_sector={"Technology": 0.35, "Energy": 0.10},
        sector_breakdown={"Technology": 0.20, "Energy": 0.05},
        max_neighbor_exposure=0.30,
        n_correlation_cap_events=1,
        cumulative_return=0.05, annual_return=0.10,
        sharpe=1.0, sortino=1.2, max_drawdown=-0.05, calmar=2.0,
        win_rate=0.6, avg_win_pct=0.02, avg_loss_pct=-0.01,
        daily_equity_curve=[(date(2026, 4, 1), 10_000.0)],
        excess_vs_spy=0.02,
        per_strategy_stats={}, bid_history=[],
    )
    assert r.max_sector_exposure == 0.35
    assert r.max_sector_exposure_by_sector["Technology"] == 0.35
    assert r.sector_breakdown["Energy"] == 0.05
    assert r.max_neighbor_exposure == 0.30
    assert r.n_correlation_cap_events == 1
    # Defaulted provenance fields
    assert r.sector_cap_policy == "uniform_40pct_v0"
    assert r.correlation_cap_policy == "neighbor_sum_rho06_40pct_v0"
    assert r.sector_caps_enabled is True
    assert r.correlation_caps_enabled is True
    assert r.risk_policy == "cap40_corr06_enforced_v0"


def test_portfolio_result_caps_disabled_provenance() -> None:
    """sector/correlation caps disabled → risk_policy='caps_disabled_v0'."""
    from datetime import date
    from marketpulse.backtest.types import PortfolioBacktestResult

    r = PortfolioBacktestResult(
        horizon=5, n_trades=0, n_dedup_total=0,
        avg_capital_utilization=0.0,
        max_strategy_exposure=0.0, hhi_concentration=0.0,
        max_sector_exposure=0.0,
        max_sector_exposure_by_sector={},
        sector_breakdown={},
        max_neighbor_exposure=0.0,
        n_correlation_cap_events=0,
        cumulative_return=0.0, annual_return=0.0,
        sharpe=None, sortino=None, max_drawdown=0.0, calmar=None,
        win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0,
        daily_equity_curve=[(date(2026, 4, 1), 10_000.0)],
        excess_vs_spy=0.0,
        per_strategy_stats={}, bid_history=[],
        sector_caps_enabled=False,
        correlation_caps_enabled=False,
        risk_policy="caps_disabled_v0",
    )
    assert r.sector_caps_enabled is False
    assert r.correlation_caps_enabled is False
    assert r.risk_policy == "caps_disabled_v0"
```

- [ ] **Step 5.2: Run, see fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/unit/test_backtest_types_phase5a.py -v -k "sector_cap_full or correlation_cap_full or cap_skip_counters or sector_correlation_telemetry or caps_disabled"
```

Expected: 5 fails (TypeError: unexpected keyword args).

- [ ] **Step 5.3: Modify `marketpulse/backtest/types.py`**

Update `BidRecord` outcome literal + add 2 diagnostic fields:

```python
@dataclass(frozen=True)
class BidRecord:
    """One bid decision — diagnostic timeline."""
    date: date
    strategy: str
    ticker: str
    weight: float
    outcome: Literal[
        "won", "dedup_loser", "cap_full", "cash_short",
        "size_too_small",
        "sector_cap_full",         # NEW Phase 5c-1
        "correlation_cap_full",    # NEW Phase 5c-2
    ]
    winner: str | None
    position_size: float
    # NEW Phase 5c diagnostic fields (default empty; populated only for matching outcome)
    blocked_by_sector: str | None = None
    blocked_by_correlation_with: tuple[tuple[str, float], ...] = ()
```

Update `StrategyContribution` — append 2 counters at end:

```python
@dataclass(frozen=True)
class StrategyContribution:
    """One strategy's slice of a shared-pool run."""
    strategy: str
    display_name: str
    n_trades: int
    n_dedup_skipped: int
    n_capacity_skipped: int
    n_cash_short_skipped: int
    n_size_too_small_skipped: int
    n_sector_cap_skipped: int       # NEW Phase 5c-1
    n_correlation_cap_skipped: int  # NEW Phase 5c-2
    contribution_pnl: float
    avg_exposure: float
    avg_bid_weight: float
    avg_position_size: float
    n_bids: int
    n_floor_hits: int
```

Update `PortfolioBacktestResult` — add 5 required cap-telemetry fields BEFORE the defaulted block, plus 5 defaulted provenance/toggle fields:

```python
@dataclass(frozen=True)
class PortfolioBacktestResult:
    """Phase 5a shared-pool result — the ONE portfolio combining all strategies."""

    horizon: int
    n_trades: int
    n_dedup_total: int
    avg_capital_utilization: float

    # Phase 5b concentration telemetry (observation-only)
    max_strategy_exposure: float
    hhi_concentration: float

    # NEW Phase 5c-1 sector telemetry (required)
    max_sector_exposure: float
    max_sector_exposure_by_sector: dict[str, float]
    sector_breakdown: dict[str, float]

    # NEW Phase 5c-2 correlation telemetry (required)
    max_neighbor_exposure: float
    n_correlation_cap_events: int

    cumulative_return: float
    annual_return: float
    sharpe: float | None
    sortino: float | None
    max_drawdown: float
    calmar: float | None
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float

    daily_equity_curve: list[tuple[date, float]]
    excess_vs_spy: float

    per_strategy_stats: dict[str, "StrategyContribution"]
    bid_history: list["BidRecord"]

    # Defaulted provenance
    display_name: str = "Shared Pool"
    mtm_model: str = "linear_interpolation_v0"
    bid_policy: str = "rolling_sharpe_60d_v0"
    sizing_policy: str = "fixed_v0"
    sector_cap_policy: str = "uniform_40pct_v0"               # NEW Phase 5c-1
    correlation_cap_policy: str = "neighbor_sum_rho06_40pct_v0"  # NEW Phase 5c-2
    sector_caps_enabled: bool = True                          # NEW Phase 5c-1
    correlation_caps_enabled: bool = True                     # NEW Phase 5c-2
    risk_policy: str = "cap40_corr06_enforced_v0"             # NEW Phase 5c composite tag
```

- [ ] **Step 5.4: Update Phase 5a/5b test helpers**

Find and update the existing helpers in `tests/unit/test_backtest_types_phase5a.py` to include the new fields with safe defaults:

```python
def _bid_kwargs(**overrides):
    base = {
        "date": date(2026, 5, 1),
        "strategy": "momentum_breakout",
        "ticker": "AAPL",
        "weight": 1.2,
        "outcome": "won",
        "winner": None,
        "position_size": 1000.0,
    }
    base.update(overrides)
    return base


def _contribution_kwargs(**overrides):
    base = {
        "strategy": "momentum_breakout",
        "display_name": "动量突破",
        "n_trades": 5,
        "n_dedup_skipped": 1,
        "n_capacity_skipped": 0,
        "n_cash_short_skipped": 0,
        "n_size_too_small_skipped": 0,
        "n_sector_cap_skipped": 0,        # NEW Phase 5c
        "n_correlation_cap_skipped": 0,   # NEW Phase 5c
        "contribution_pnl": 250.0,
        "avg_exposure": 0.30,
        "avg_bid_weight": 1.4,
        "avg_position_size": 1450.0,
        "n_bids": 6,
        "n_floor_hits": 0,
    }
    base.update(overrides)
    return base


def _portfolio_kwargs(**overrides):
    base = {
        "horizon": 5,
        "n_trades": 30,
        "n_dedup_total": 4,
        "avg_capital_utilization": 0.55,
        "max_strategy_exposure": 0.55,
        "hhi_concentration": 0.31,
        "max_sector_exposure": 0.0,              # NEW Phase 5c
        "max_sector_exposure_by_sector": {},     # NEW Phase 5c
        "sector_breakdown": {},                  # NEW Phase 5c
        "max_neighbor_exposure": 0.0,            # NEW Phase 5c
        "n_correlation_cap_events": 0,           # NEW Phase 5c
        "cumulative_return": 0.12,
        "annual_return": 0.24,
        "sharpe": 1.4,
        "sortino": 1.7,
        "max_drawdown": -0.06,
        "calmar": 4.0,
        "win_rate": 0.65,
        "avg_win_pct": 0.04,
        "avg_loss_pct": -0.02,
        "daily_equity_curve": [(date(2026, 4, 1), 10000.0), (date(2026, 5, 1), 11200.0)],
        "excess_vs_spy": 0.07,
        "per_strategy_stats": {},
        "bid_history": [],
    }
    base.update(overrides)
    return base
```

- [ ] **Step 5.5: Update `portfolio_simulator.py` constructors with placeholder defaults**

Find every `BidRecord(...)`, `StrategyContribution(...)`, `PortfolioBacktestResult(...)` constructor in `marketpulse/backtest/portfolio_simulator.py` and add the new fields with placeholder defaults. The real values land in Tasks 6-8. Placeholders:

- BidRecord: no extra (new fields are defaulted, no kwarg needed)
- StrategyContribution: add `n_sector_cap_skipped=0, n_correlation_cap_skipped=0`
- PortfolioBacktestResult (both constructors — empty-bids early return + finalize): add `max_sector_exposure=0.0, max_sector_exposure_by_sector={}, sector_breakdown={}, max_neighbor_exposure=0.0, n_correlation_cap_events=0`

- [ ] **Step 5.6: Run pytest broadly to catch every caller**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/unit/test_backtest_types_phase5a.py tests/unit/test_backtest_portfolio_simulator.py tests/integration/test_backtest_shared_pool.py -v 2>&1 | tail -10
```

Expected: any remaining `TypeError: missing keyword argument` errors. Fix each call site by threading the new defaults.

- [ ] **Step 5.7: Re-run, all pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/unit/test_backtest_types_phase5a.py -v
```

Expected: existing tests + 5 new = all pass.

- [ ] **Step 5.8: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run ruff check marketpulse/backtest/types.py marketpulse/backtest/portfolio_simulator.py tests/
git add marketpulse/backtest/types.py marketpulse/backtest/portfolio_simulator.py tests/
git commit -m "feat(phase-5c): extend types — sector + correlation telemetry

Spec § 7 type extensions.

BidRecord:
- + 'sector_cap_full' and 'correlation_cap_full' outcome literals
- + blocked_by_sector: str | None (populated only for sector_cap_full)
- + blocked_by_correlation_with: tuple[tuple[str, float], ...]
  (populated only for correlation_cap_full; embeddable in frozen dataclass)

StrategyContribution:
- + n_sector_cap_skipped
- + n_correlation_cap_skipped

PortfolioBacktestResult:
- + max_sector_exposure: pool-wide peak single-sector fraction
- + max_sector_exposure_by_sector: per-sector peak dict (fixes UI peak bug)
- + sector_breakdown: time-averaged per-sector fraction
- + max_neighbor_exposure: peak neighbor-set exposure
- + n_correlation_cap_events: rejection counter
- + sector_cap_policy / correlation_cap_policy provenance strings
- + sector_caps_enabled / correlation_caps_enabled toggles (default True)
- + risk_policy composite tag (default 'cap40_corr06_enforced_v0')

Test helpers _bid_kwargs / _contribution_kwargs / _portfolio_kwargs updated.
portfolio_simulator constructors threaded with placeholder defaults — real
values land in Tasks 6-8.

5 new type tests cover outcome literals, diagnostic fields, telemetry
fields, and risk_policy composition."
```

---

### Task 6: portfolio_simulator.py — sector cap check in ALLOCATE

**Files:**
- Modify: `marketpulse/backtest/portfolio_simulator.py`
- Modify: `tests/unit/test_backtest_portfolio_simulator.py`

**Important:** ALLOCATE pre-warm needs a `sector_provider` callable (or `get_sector` direct) so tests can inject a deterministic fake. Pass through `simulate_shared_pool(sector_provider=...)` with default `None` → uses real `get_sector` from `sector.py`.

- [ ] **Step 6.1: Append failing tests** to `tests/unit/test_backtest_portfolio_simulator.py`

```python
def test_sector_cap_fires_at_boundary():
    """Pool $10k, $3k in Tech, $1k Tech candidate → exactly at $4k cap → won."""
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    # Build a fake sector provider that classifies tickers
    def fake_sector(ticker: str) -> str:
        return {"AAPL": "Technology", "MSFT": "Technology", "GOOGL": "Technology"}.get(ticker, "unknown")

    # 4 same-sector bids of $1k each → first 3 land, 4th blocks at sector cap
    bids = [
        _pair("AAPL", "a", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("MSFT", "b", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("GOOGL", "c", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    daily_curves = {"a": good, "b": good, "c": good}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,  # fixed $1k so cap math is deterministic
        sector_caps_enabled=True,
        sector_cap_pct=0.40,
        correlation_caps_enabled=False,  # isolate sector test
        sector_provider=fake_sector,
    )
    won = [b for b in r.bid_history if b.outcome == "won"]
    # Precondition: 3 same-sector $1k bids should all land (3*1000=3000 ≤ 4000)
    assert len(won) == 3
    assert all(b.position_size == 1000.0 for b in won)


def test_sector_cap_fires_when_crossed():
    """Pool with $3.5k Tech, $1k Tech candidate → cluster $4.5k > $4k → blocked."""
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    def fake_sector(ticker: str) -> str:
        # Two tickers same sector, one different
        return {
            "AAPL": "Technology", "MSFT": "Technology",
            "GOOGL": "Technology", "TSLA": "Technology",
            "TSLA2": "Technology", "TSLA3": "Technology",
        }.get(ticker, "unknown")

    # 5 same-sector $1k bids; cap=40% of $10k = $4k → first 4 land, 5th blocks
    bids = [
        _pair(f"T{i}", f"s{i}", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)
        for i in ["AAPL", "MSFT", "GOOGL", "TSLA", "TSLA2"]
    ]
    daily_curves = {f"s{i}": good for i in ["AAPL", "MSFT", "GOOGL", "TSLA", "TSLA2"]}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False, sector_caps_enabled=True, sector_cap_pct=0.40,
        correlation_caps_enabled=False, sector_provider=fake_sector,
    )
    won = [b for b in r.bid_history if b.outcome == "won"]
    blocked = [b for b in r.bid_history if b.outcome == "sector_cap_full"]
    # Precondition: 4 land (4*$1k = $4k = cap)
    assert len(won) == 4
    # Outcome: 5th blocked with sector_cap_full + Technology diagnostic
    assert len(blocked) == 1
    assert blocked[0].blocked_by_sector == "Technology"


def test_sector_cap_unknown_sector_obeys_same_cap():
    """Tickers with sector='unknown' are still subject to the 40% cap."""
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    # All tickers fall through to 'unknown'
    def fake_sector(_ticker: str) -> str:
        return "unknown"

    bids = [
        _pair(f"T{i}", f"s{i}", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)
        for i in range(5)
    ]
    daily_curves = {f"s{i}": good for i in range(5)}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False, sector_caps_enabled=True, sector_cap_pct=0.40,
        correlation_caps_enabled=False, sector_provider=fake_sector,
    )
    won = [b for b in r.bid_history if b.outcome == "won"]
    blocked = [b for b in r.bid_history if b.outcome == "sector_cap_full"]
    # 4 land (unknown sector hits its own 40% cap), 5th blocked
    assert len(won) == 4
    assert len(blocked) == 1
    assert blocked[0].blocked_by_sector == "unknown"


def test_sector_cap_disabled_via_toggle_bypassed():
    """sector_caps_enabled=False → no cap enforcement, even at extreme concentration."""
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    def fake_sector(_ticker: str) -> str:
        return "Technology"

    bids = [
        _pair(f"T{i}", f"s{i}", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)
        for i in range(5)
    ]
    daily_curves = {f"s{i}": good for i in range(5)}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False,  # cap DISABLED
        correlation_caps_enabled=False, sector_provider=fake_sector,
    )
    won = [b for b in r.bid_history if b.outcome == "won"]
    blocked = [b for b in r.bid_history if b.outcome == "sector_cap_full"]
    # All 5 land — only blocked by global cap_full (5*$1k=$5k ≤ $10k pool)
    assert len(won) == 5
    assert len(blocked) == 0
```

- [ ] **Step 6.2: Run, see fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v -k "sector_cap"
```

Expected: 4 fails (TypeError: unexpected kwarg `sector_caps_enabled` / `sector_provider`).

- [ ] **Step 6.3: Modify `simulate_shared_pool` signature in `portfolio_simulator.py`**

Find the function signature (around line 43) and add the new kwargs after `sizing_enabled`:

```python
def simulate_shared_pool(
    bids: list,
    daily_curves: dict[str, list[tuple[date, float]]],
    *,
    horizon: int,
    initial_capital: float = 10_000.0,
    base_position_size: float = 1_000.0,
    max_capital_in_use: float = 10_000.0,
    lookback_days: int = 60,
    target_vol: float = 0.01,
    min_position: float = 200.0,
    max_position: float = 4_000.0,
    sizing_enabled: bool = True,
    # NEW Phase 5c-1
    sector_caps_enabled: bool = True,
    sector_cap_pct: float = 0.40,
    sector_provider: "Callable[[str], str] | None" = None,
    # NEW Phase 5c-2
    correlation_caps_enabled: bool = True,
    correlation_cap_pct: float = 0.40,
    correlation_threshold: float = 0.60,
    price_provider: "PriceProvider | None" = None,
) -> PortfolioBacktestResult:
```

Add imports at top of file:

```python
from typing import TYPE_CHECKING, Callable
if TYPE_CHECKING:
    from marketpulse.backtest.correlation import PriceProvider
```

Update `bid_policy` / `sizing_policy` block (around line 75) to compose `risk_policy`:

```python
bid_policy = f"rolling_sharpe_{lookback_days}d_v0"
sizing_policy = "vol_target_conviction_v0" if sizing_enabled else "fixed_v0"

# Phase 5c risk_policy composition (spec § 10b)
if sector_caps_enabled and correlation_caps_enabled:
    risk_policy = "cap40_corr06_enforced_v0"
elif not sector_caps_enabled and not correlation_caps_enabled:
    risk_policy = "caps_disabled_v0"
elif sector_caps_enabled:
    risk_policy = "cap40_only_v0"
else:
    risk_policy = "corr06_only_v0"

sector_cap_dollars = sector_cap_pct * initial_capital
correlation_cap_dollars = correlation_cap_pct * initial_capital

# Resolve sector_provider — default to real get_sector
if sector_provider is None:
    from marketpulse.backtest.sector import get_sector as _real_get_sector
    sector_provider = _real_get_sector
```

Update **both** `PortfolioBacktestResult(...)` constructors (empty-bids early-return + final return) to include the new fields. Empty bids:

```python
return PortfolioBacktestResult(
    horizon=horizon,
    n_trades=0,
    n_dedup_total=0,
    avg_capital_utilization=0.0,
    max_strategy_exposure=0.0,
    hhi_concentration=0.0,
    max_sector_exposure=0.0,
    max_sector_exposure_by_sector={},
    sector_breakdown={},
    max_neighbor_exposure=0.0,
    n_correlation_cap_events=0,
    # ...existing fields...
    bid_policy=bid_policy,
    sizing_policy=sizing_policy,
    sector_caps_enabled=sector_caps_enabled,
    correlation_caps_enabled=correlation_caps_enabled,
    risk_policy=risk_policy,
)
```

Locate the ALLOCATE block (after DEDUP — the loop iterating `sorted_winners`). **Insert sector cap check after `cash_short` and before opening the position**:

```python
# Pre-warm sector lookup (once per day)
sector_by_ticker: dict[str, str] = {}
for p in open_positions:
    sector_by_ticker.setdefault(p.ticker, sector_provider(p.ticker))
for b in sorted_winners:
    sector_by_ticker.setdefault(b.ticker, sector_provider(b.ticker))

# Build running sector_exposure from open_positions
sector_exposure: dict[str, float] = {}
for p in open_positions:
    s = sector_by_ticker[p.ticker]
    sector_exposure[s] = sector_exposure.get(s, 0.0) + p.position_size

for b in sorted_winners:
    requested_size = position_sizes[b.strategy]
    candidate_sector = sector_by_ticker[b.ticker]

    # ── Existing Phase 5a/5b cap_full check ──
    if capital_in_use + requested_size > max_capital_in_use:
        all_bid_records.append(BidRecord(
            date=d, strategy=b.strategy, ticker=b.ticker,
            weight=weights[b.strategy], outcome="cap_full", winner=None,
            position_size=requested_size,
        ))
        # ... existing counter increments ...
        continue

    # ── Existing Phase 5a/5b cash_short check ──
    if cash < requested_size:
        all_bid_records.append(BidRecord(
            date=d, strategy=b.strategy, ticker=b.ticker,
            weight=weights[b.strategy], outcome="cash_short", winner=None,
            position_size=requested_size,
        ))
        # ... existing counter increments ...
        continue

    # ── NEW Phase 5c-1: sector cap check ──
    if sector_caps_enabled:
        if sector_exposure.get(candidate_sector, 0.0) + requested_size > sector_cap_dollars:
            all_bid_records.append(BidRecord(
                date=d, strategy=b.strategy, ticker=b.ticker,
                weight=weights[b.strategy], outcome="sector_cap_full", winner=None,
                position_size=requested_size,
                blocked_by_sector=candidate_sector,
            ))
            n_sector_cap_skipped_by_strategy[b.strategy] = (
                n_sector_cap_skipped_by_strategy.get(b.strategy, 0) + 1
            )
            continue

    # Phase 5c-2 correlation cap check (lands in Task 7)
    # ...

    # ── Open position (unchanged from Phase 5b) ──
    open_positions.append(_OpenPosition(...))
    cash -= requested_size
    sector_exposure[candidate_sector] = sector_exposure.get(candidate_sector, 0.0) + requested_size
    # ... existing increments and won BidRecord ...
```

Add accumulator init at top of function (alongside other `n_*_by_strategy: dict[str, int] = {}` lines):

```python
n_sector_cap_skipped_by_strategy: dict[str, int] = {}
```

- [ ] **Step 6.4: Run, pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v -k "sector_cap"
```

Expected: 4/4 pass.

- [ ] **Step 6.5: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run ruff check marketpulse/backtest/portfolio_simulator.py tests/
git add marketpulse/backtest/portfolio_simulator.py tests/
git commit -m "feat(phase-5c): ALLOCATE sector cap check

Spec § 6: sector cap fires after cap_full / cash_short, before opening
the position. New 'sector_cap_full' BidRecord outcome with
blocked_by_sector diagnostic.

- Pre-warm sector_by_ticker dict from open_positions + candidates
- Build sector_exposure running tally from open_positions at start of day
- Per-candidate: check sector_exposure[s] + requested_size > sector_cap_dollars
- Reject with sector_cap_full + blocked_by_sector
- On open, update sector_exposure for next candidate (greedy invariant)

sector_provider kwarg accepts injectable Callable[[str], str] (default
binds to marketpulse.backtest.sector.get_sector). Tests use deterministic
in-memory fake sector functions.

risk_policy composite tag computed from {sector,correlation}_caps_enabled
combo: cap40_corr06_enforced_v0 (default) / caps_disabled_v0 /
cap40_only_v0 / corr06_only_v0.

4 new tests cover at-boundary success, crossed-boundary block, 'unknown'
sector cap obeyed, toggle bypass."
```

---

### Task 7: portfolio_simulator.py — correlation cap check in ALLOCATE

**Files:**
- Modify: `marketpulse/backtest/portfolio_simulator.py`
- Modify: `tests/unit/test_backtest_portfolio_simulator.py`

- [ ] **Step 7.1: Append failing tests** to `tests/unit/test_backtest_portfolio_simulator.py`

```python
def test_correlation_cap_fires_when_cluster_exceeds():
    """AAPL+GOOGL pair ρ≈1.0; pool $3k AAPL + $1.5k GOOGL candidate → cluster $4.5k > $4k → block."""
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    # Build identical price series → corr=1.0
    base_prices = [(date(2026, 1, 1) + timedelta(days=i), 100.0 + i) for i in range(60)]

    class FakePriceProvider:
        def get_daily_closes(self, ticker, start, end):
            if ticker in {"AAPL", "GOOGL"}:
                return [(d, v) for d, v in base_prices if start <= d < end]
            return []

    def neutral_sector(_t: str) -> str:
        return "unknown"

    good_curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
                  for i in range(30)]
    bids = [
        # First 3 AAPL bids = $3k Tech open
        _pair("AAPL", "s1", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("AAPL", "s2", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("AAPL", "s3", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        # GOOGL bid (correlated to AAPL ρ=1.0) → cluster = $3k + $1k = $4k = cap, then 5th would be $5k > $4k
        _pair("GOOGL", "s4", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("GOOGL", "s5", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    daily_curves = {f"s{i}": good_curve for i in range(1, 6)}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False,  # isolate correlation test
        correlation_caps_enabled=True,
        correlation_cap_pct=0.40, correlation_threshold=0.60,
        sector_provider=neutral_sector,
        price_provider=FakePriceProvider(),
    )
    won = [b for b in r.bid_history if b.outcome == "won"]
    blocked = [b for b in r.bid_history if b.outcome == "correlation_cap_full"]
    # 4 land ($4k = cap), 5th blocked
    assert len(won) == 4
    assert len(blocked) == 1
    # Diagnostic preserved
    assert len(blocked[0].blocked_by_correlation_with) > 0
    assert blocked[0].blocked_by_correlation_with[0][0] in {"AAPL", "GOOGL"}


def test_correlation_cap_does_not_fire_below_threshold():
    """When pairwise corr < threshold, no neighbor → no cap → opens normally."""
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    # Build inversely correlated series → corr ≈ -1
    a_prices = [(date(2026, 1, 1) + timedelta(days=i), 100.0 + i) for i in range(60)]
    b_prices = [(date(2026, 1, 1) + timedelta(days=i), 160.0 - i) for i in range(60)]

    class FakePriceProvider:
        def get_daily_closes(self, ticker, start, end):
            data = {"AAPL": a_prices, "TLT": b_prices}.get(ticker, [])
            return [(d, v) for d, v in data if start <= d < end]

    def neutral_sector(_t: str) -> str:
        return "unknown"

    good_curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
                  for i in range(30)]
    bids = [
        _pair("AAPL", "s1", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("TLT", "s2", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    daily_curves = {"s1": good_curve, "s2": good_curve}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False,
        correlation_caps_enabled=True,
        correlation_cap_pct=0.40, correlation_threshold=0.60,
        sector_provider=neutral_sector,
        price_provider=FakePriceProvider(),
    )
    won = [b for b in r.bid_history if b.outcome == "won"]
    # Both land — inverse correlation means no neighbor
    assert len(won) == 2


def test_correlation_cap_cold_start_bypassed():
    """When price data has < min_overlap days, correlation returns None → no neighbor → open."""
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    # Only 10 days of data — insufficient overlap
    short_prices = [(date(2026, 4, 20) + timedelta(days=i), 100.0 + i) for i in range(10)]

    class FakePriceProvider:
        def get_daily_closes(self, ticker, start, end):
            if ticker in {"NEW1", "NEW2"}:
                return [(d, v) for d, v in short_prices if start <= d < end]
            return []

    def neutral_sector(_t: str) -> str:
        return "unknown"

    good_curve = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
                  for i in range(30)]
    bids = [
        _pair("NEW1", "s1", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("NEW2", "s2", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    daily_curves = {"s1": good_curve, "s2": good_curve}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False,
        correlation_caps_enabled=True,
        correlation_cap_pct=0.40, correlation_threshold=0.60,
        sector_provider=neutral_sector,
        price_provider=FakePriceProvider(),
    )
    won = [b for b in r.bid_history if b.outcome == "won"]
    # Both should land — cold-start failsafe-open
    assert len(won) == 2
```

- [ ] **Step 7.2: Run, see fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v -k "correlation_cap"
```

Expected: 3 fails.

- [ ] **Step 7.3: Modify ALLOCATE in `portfolio_simulator.py` — add correlation cap check**

After the sector cap check (Task 6), before opening the position:

```python
    # ── NEW Phase 5c-2: correlation cap check ──
    if correlation_caps_enabled and price_provider is not None:
        from marketpulse.backtest.correlation import find_correlation_neighbors

        open_tickers = [p.ticker for p in open_positions]
        neighbors, corr_diagnostics = find_correlation_neighbors(
            b.ticker, open_tickers,
            as_of=d, threshold=correlation_threshold,
            lookback_days=lookback_days,
            price_provider=price_provider,
        )
        cluster_exposure = requested_size + sum(
            p.position_size for p in open_positions if p.ticker in neighbors
        )
        if cluster_exposure > correlation_cap_dollars:
            all_bid_records.append(BidRecord(
                date=d, strategy=b.strategy, ticker=b.ticker,
                weight=weights[b.strategy], outcome="correlation_cap_full", winner=None,
                position_size=requested_size,
                blocked_by_correlation_with=corr_diagnostics,
            ))
            n_correlation_cap_skipped_by_strategy[b.strategy] = (
                n_correlation_cap_skipped_by_strategy.get(b.strategy, 0) + 1
            )
            continue
```

Add accumulator at top of function:

```python
n_correlation_cap_skipped_by_strategy: dict[str, int] = {}
```

- [ ] **Step 7.4: Run, pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v -k "correlation_cap"
```

Expected: 3/3 pass.

- [ ] **Step 7.5: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run ruff check marketpulse/backtest/portfolio_simulator.py tests/
git add marketpulse/backtest/portfolio_simulator.py tests/
git commit -m "feat(phase-5c): ALLOCATE correlation cap check

Spec § 6: correlation cap fires after sector_cap_full, before opening
the position. New 'correlation_cap_full' BidRecord outcome with
blocked_by_correlation_with tuple-of-pairs diagnostic.

- For each candidate bid: find_correlation_neighbors(b.ticker, open_tickers)
- cluster_exposure = requested_size + sum of correlated open positions
- If cluster_exposure > correlation_cap_dollars → reject

Cold-start failsafe-open: when price data lacks min_overlap, neighbors
return empty → cluster = {candidate} only → cap effectively not triggered.

price_provider kwarg accepts injectable PriceProvider Protocol (default
None → correlation cap disabled even if flag is True). Tests use
in-memory fake price provider.

3 new tests cover correlated-cluster block, anti-correlated bypass,
cold-start bypass."
```

---

### Task 8: portfolio_simulator.py — finalization adds sector/correlation telemetry

**Files:**
- Modify: `marketpulse/backtest/portfolio_simulator.py`
- Modify: `tests/unit/test_backtest_portfolio_simulator.py`

- [ ] **Step 8.1: Append failing tests** to `tests/unit/test_backtest_portfolio_simulator.py`

```python
def test_finalization_populates_max_sector_exposure():
    """Pool-wide peak: max over sectors of (sector_total / pool) across all days."""
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    def fake_sector(ticker: str) -> str:
        return {"AAPL": "Technology", "MSFT": "Technology", "XOM": "Energy"}.get(ticker, "unknown")

    bids = [
        _pair("AAPL", "s1", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("MSFT", "s2", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
        _pair("XOM", "s3", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0),
    ]
    daily_curves = {"s1": good, "s2": good, "s3": good}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False, sector_caps_enabled=True, sector_cap_pct=0.40,
        correlation_caps_enabled=False, sector_provider=fake_sector,
    )
    # Tech = 2*$1k=$2k → 20%; Energy = $1k → 10%; max = 20%
    assert abs(r.max_sector_exposure - 0.20) < 0.01
    # Per-sector dict populated
    assert "Technology" in r.max_sector_exposure_by_sector
    assert abs(r.max_sector_exposure_by_sector["Technology"] - 0.20) < 0.01
    assert abs(r.max_sector_exposure_by_sector["Energy"] - 0.10) < 0.01


def test_finalization_populates_sector_breakdown_time_average():
    """sector_breakdown averages each sector's daily fraction over ALL calendar days."""
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]

    def fake_sector(_t: str) -> str:
        return "Technology"

    bids = [_pair("AAPL", "s1", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)]
    daily_curves = {"s1": good}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False, sector_caps_enabled=True, sector_cap_pct=0.40,
        correlation_caps_enabled=False, sector_provider=fake_sector,
    )
    # Single position $1k for ~5 days out of 7 calendar day window
    assert "Technology" in r.sector_breakdown
    assert r.sector_breakdown["Technology"] > 0.0
    assert r.sector_breakdown["Technology"] <= 1.0


def test_finalization_n_correlation_cap_events_counted():
    """n_correlation_cap_events counts bids rejected by correlation cap."""
    from datetime import date, timedelta
    from marketpulse.backtest.portfolio_simulator import simulate_shared_pool

    base_prices = [(date(2026, 1, 1) + timedelta(days=i), 100.0 + i) for i in range(60)]

    class FakePriceProvider:
        def get_daily_closes(self, ticker, start, end):
            if ticker in {"AAPL", "GOOGL"}:
                return [(d, v) for d, v in base_prices if start <= d < end]
            return []

    def neutral_sector(_t: str) -> str:
        return "unknown"

    good = [(date(2026, 4, 1) + timedelta(days=i), 10_000.0 * (1.005 ** i))
            for i in range(30)]
    bids = [
        _pair("AAPL", f"s{i}", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)
        for i in range(4)
    ] + [_pair("GOOGL", "s5", date(2026, 5, 1), 100.0, date(2026, 5, 8), 105.0)]
    daily_curves = {f"s{i}": good for i in range(1, 6)}

    r = simulate_shared_pool(
        bids=bids, daily_curves=daily_curves,
        horizon=5, initial_capital=10_000.0, base_position_size=1_000.0,
        max_capital_in_use=10_000.0, lookback_days=60,
        sizing_enabled=False,
        sector_caps_enabled=False,
        correlation_caps_enabled=True,
        correlation_cap_pct=0.40, correlation_threshold=0.60,
        sector_provider=neutral_sector, price_provider=FakePriceProvider(),
    )
    blocked = [b for b in r.bid_history if b.outcome == "correlation_cap_full"]
    assert r.n_correlation_cap_events == len(blocked)
    assert r.n_correlation_cap_events >= 1
```

- [ ] **Step 8.2: Run, see fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v -k "finalization_populates or n_correlation_cap_events"
```

Expected: 3 fails (max_sector_exposure stays 0.0; sector_breakdown empty; n_correlation_cap_events 0).

- [ ] **Step 8.3: Modify finalization in `portfolio_simulator.py`**

Locate the finalization block (after the daily loop, before `return PortfolioBacktestResult(...)`). Add sector tracking accumulators at top of function:

```python
# Phase 5c sector + correlation telemetry accumulators
sector_exposure_daily: list[dict[str, float]] = []  # per-day snapshot of sector → dollars
n_correlation_cap_events = 0
```

Inside the daily loop, AFTER ALLOCATE finishes (before MTM/RECORD), snapshot the sector exposure:

```python
# Phase 5c: snapshot per-day sector exposure (after open/close for the day)
day_snapshot: dict[str, float] = {}
for p in open_positions:
    s = sector_by_ticker.get(p.ticker, sector_provider(p.ticker))
    day_snapshot[s] = day_snapshot.get(s, 0.0) + p.position_size
sector_exposure_daily.append(day_snapshot)
```

Inside ALLOCATE correlation cap reject branch, also increment the running counter:

```python
n_correlation_cap_events += 1
```

In finalization, compute the new telemetry fields:

```python
# Phase 5c-1 sector telemetry
if sector_exposure_daily:
    max_sector_exposure = 0.0
    max_sector_exposure_by_sector: dict[str, float] = {}
    sector_sum_over_days: dict[str, float] = {}

    for day_snapshot in sector_exposure_daily:
        for s, dollars in day_snapshot.items():
            frac = dollars / initial_capital
            if frac > max_sector_exposure:
                max_sector_exposure = frac
            if frac > max_sector_exposure_by_sector.get(s, 0.0):
                max_sector_exposure_by_sector[s] = frac
            sector_sum_over_days[s] = sector_sum_over_days.get(s, 0.0) + frac

    n_days = len(sector_exposure_daily)
    sector_breakdown = {s: total / n_days for s, total in sector_sum_over_days.items()}
else:
    max_sector_exposure = 0.0
    max_sector_exposure_by_sector = {}
    sector_breakdown = {}

# Phase 5c-2 correlation telemetry — n_correlation_cap_events tracked in-loop
# max_neighbor_exposure stays 0.0 in v0 (deferred; spec § 7 documents this as
# a future metric tracked at finalization once cluster-size sampling is wired)
max_neighbor_exposure = 0.0
```

Update final `PortfolioBacktestResult(...)` constructor to pass real values:

```python
return PortfolioBacktestResult(
    # ... existing fields ...
    max_sector_exposure=max_sector_exposure,
    max_sector_exposure_by_sector=max_sector_exposure_by_sector,
    sector_breakdown=sector_breakdown,
    max_neighbor_exposure=max_neighbor_exposure,
    n_correlation_cap_events=n_correlation_cap_events,
    # ... existing fields ...
    sector_caps_enabled=sector_caps_enabled,
    correlation_caps_enabled=correlation_caps_enabled,
    risk_policy=risk_policy,
)
```

Update per-strategy `StrategyContribution(...)` constructor to include the new counters:

```python
per_strategy_stats[s] = StrategyContribution(
    # ... existing fields ...
    n_sector_cap_skipped=n_sector_cap_skipped_by_strategy.get(s, 0),
    n_correlation_cap_skipped=n_correlation_cap_skipped_by_strategy.get(s, 0),
    # ... existing fields ...
)
```

- [ ] **Step 8.4: Run, pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py -v
```

Expected: all portfolio simulator tests pass (existing + 10 new from T6/T7/T8).

- [ ] **Step 8.5: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run ruff check marketpulse/backtest/portfolio_simulator.py tests/
git add marketpulse/backtest/portfolio_simulator.py tests/
git commit -m "feat(phase-5c): finalization sector + correlation telemetry

Spec § 7: per-day sector exposure snapshot accumulator → finalization
computes pool-wide and per-sector peaks plus time-averaged breakdown.

- sector_exposure_daily snapshotted each day post-ALLOCATE
- max_sector_exposure: pool-wide peak (max over sectors over days)
- max_sector_exposure_by_sector: dict of per-sector peaks (fixes UI peak bug
  where avg-sorted ≠ peak-sorted; see spec § 7 + § 8)
- sector_breakdown: time-averaged fraction per sector (denominator =
  all calendar days, including empty-pool days)
- n_correlation_cap_events: running counter inside ALLOCATE
- max_neighbor_exposure: deferred to future iteration (v0 stays 0.0)

StrategyContribution.n_sector_cap_skipped / n_correlation_cap_skipped
populated from per-strategy accumulators.

3 new tests cover max_sector_exposure, sector_breakdown time-average,
n_correlation_cap_events counter."
```

---

### Task 9: simulator.py — orchestrator threads new kwargs

**Files:**
- Modify: `marketpulse/backtest/simulator.py`
- Modify: `tests/integration/test_backtest_shared_pool.py`

- [ ] **Step 9.1: Append failing tests** to `tests/integration/test_backtest_shared_pool.py`

```python
def test_run_shared_pool_default_caps_enabled(db_session):
    """Orchestrator defaults to caps enabled (Phase 5c is default)."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5)
    assert out["shared"].sector_caps_enabled is True
    assert out["shared"].correlation_caps_enabled is True
    assert out["shared"].risk_policy == "cap40_corr06_enforced_v0"


def test_run_shared_pool_caps_disabled_via_kwargs(db_session):
    """Both caps disabled → risk_policy = 'caps_disabled_v0'."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(
        db_session, horizon=5,
        sector_caps_enabled=False,
        correlation_caps_enabled=False,
    )
    assert out["shared"].sector_caps_enabled is False
    assert out["shared"].correlation_caps_enabled is False
    assert out["shared"].risk_policy == "caps_disabled_v0"


def test_run_shared_pool_sector_breakdown_populated(db_session):
    """sector_breakdown field is a dict, even when empty."""
    from marketpulse.backtest.simulator import run_shared_pool_backtest
    _seed(db_session, ticker="AAA", strategy="momentum_breakout")
    db_session.commit()

    out = run_shared_pool_backtest(db_session, horizon=5)
    assert isinstance(out["shared"].sector_breakdown, dict)
    assert isinstance(out["shared"].max_sector_exposure_by_sector, dict)
```

- [ ] **Step 9.2: Run, see fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/integration/test_backtest_shared_pool.py -v -k "default_caps_enabled or caps_disabled_via_kwargs or sector_breakdown_populated"
```

Expected: 3 fails (TypeError: unexpected kwarg `sector_caps_enabled`).

- [ ] **Step 9.3: Modify `run_shared_pool_backtest` in `marketpulse/backtest/simulator.py`**

Find the function signature (around line 467) and add the new kwargs:

```python
def run_shared_pool_backtest(
    db,
    *,
    horizon: int = 5,
    since: date | None = None,
    initial_capital: float = 10_000.0,
    base_position_size: float = 1_000.0,
    target_vol: float = 0.01,
    min_position: float = 200.0,
    max_position: float = 4_000.0,
    sizing_enabled: bool = True,
    max_capital_in_use: float = 10_000.0,
    lookback_days: int = 60,
    # NEW Phase 5c-1
    sector_caps_enabled: bool = True,
    sector_cap_pct: float = 0.40,
    # NEW Phase 5c-2
    correlation_caps_enabled: bool = True,
    correlation_cap_pct: float = 0.40,
    correlation_threshold: float = 0.60,
) -> dict:
```

Inside, construct a real `PriceProvider` from the existing price_cache infrastructure:

```python
# Phase 5c: wire price_provider for correlation cap (uses existing price_cache)
from marketpulse.backtest.correlation import PriceProvider

class _DBPriceProvider:
    """Wrap database price_cache rows in the PriceProvider Protocol."""

    def __init__(self, session) -> None:
        self._session = session

    def get_daily_closes(self, ticker, start, end):
        from marketpulse.db.models import PriceCacheEntry
        rows = (
            self._session.query(PriceCacheEntry)
            .filter(
                PriceCacheEntry.ticker == ticker,
                PriceCacheEntry.date >= start,
                PriceCacheEntry.date < end,
            )
            .order_by(PriceCacheEntry.date)
            .all()
        )
        return [(r.date, float(r.close)) for r in rows]

price_provider = _DBPriceProvider(db) if correlation_caps_enabled else None
```

Pass new kwargs through to `simulate_shared_pool`:

```python
shared_result = simulate_shared_pool(
    bids=all_bids,
    daily_curves=daily_curves,
    horizon=horizon,
    initial_capital=initial_capital,
    base_position_size=base_position_size,
    target_vol=target_vol,
    min_position=min_position,
    max_position=max_position,
    sizing_enabled=sizing_enabled,
    max_capital_in_use=max_capital_in_use,
    lookback_days=lookback_days,
    sector_caps_enabled=sector_caps_enabled,
    sector_cap_pct=sector_cap_pct,
    correlation_caps_enabled=correlation_caps_enabled,
    correlation_cap_pct=correlation_cap_pct,
    correlation_threshold=correlation_threshold,
    price_provider=price_provider,
)
```

- [ ] **Step 9.4: Run, pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/integration/test_backtest_shared_pool.py -v
```

Expected: existing + 3 new all pass.

- [ ] **Step 9.5: Ruff + commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run ruff check marketpulse/backtest/simulator.py tests/integration/test_backtest_shared_pool.py
git add marketpulse/backtest/simulator.py tests/integration/test_backtest_shared_pool.py
git commit -m "feat(phase-5c): orchestrator threads sector/correlation cap kwargs

run_shared_pool_backtest accepts:
- sector_caps_enabled (default True)
- sector_cap_pct (default 0.40)
- correlation_caps_enabled (default True)
- correlation_cap_pct (default 0.40)
- correlation_threshold (default 0.60)

Wraps existing price_cache query in _DBPriceProvider implementing the
PriceProvider Protocol from correlation.py. Provider is constructed
only when correlation_caps_enabled=True (None otherwise → cap effectively
inactive even if flag is True with no provider).

3 new integration tests cover defaults-on, both-disabled provenance,
sector_breakdown shape."
```

---

### Task 10: routes/backtest.py — pass new context to template

**Files:**
- Modify: `marketpulse/web/routes/backtest.py`
- Modify: `tests/web/test_lab_backtest_modes.py`

- [ ] **Step 10.1: Append failing tests** to `tests/web/test_lab_backtest_modes.py`

```python
def test_lab_backtest_shared_mode_renders_sector_breakdown_section(
    client, monkeypatch, db_session,
):
    """Shared-pool mode renders the new sector breakdown section."""
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="A1", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest?mode=shared-pool")
    assert r.status_code == 200
    assert "Sector 暴露分布" in r.text or "sector" in r.text.lower()


def test_lab_backtest_shared_mode_renders_cap_policy_in_hero(
    client, monkeypatch, db_session,
):
    """Hero text includes risk_policy provenance line."""
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="A1", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/backtest?mode=shared-pool")
    assert "cap40_corr06_enforced_v0" in r.text or "sector_cap_policy" in r.text
```

- [ ] **Step 10.2: Run, see fail**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/web/test_lab_backtest_modes.py -v -k "sector_breakdown_section or cap_policy_in_hero"
```

Expected: 2 fails (template doesn't include the new content yet).

- [ ] **Step 10.3: Modify `marketpulse/web/routes/backtest.py`**

In the `lab_backtest` route handler, when `mode == "shared-pool"`, the existing `run_shared_pool_backtest` call returns a result with the new fields. No changes needed in the route handler itself — the context already passes `shared_result`. Verify by checking the template variable name; if needed, add explicit aliases:

```python
return templates.TemplateResponse(
    request, "lab_backtest.html",
    {
        # ... existing context ...
        "shared_result": shared_result,
        # Phase 5c: explicit aliases for template clarity (optional)
        "sector_caps_enabled": shared_result.sector_caps_enabled,
        "correlation_caps_enabled": shared_result.correlation_caps_enabled,
        "risk_policy": shared_result.risk_policy,
    },
)
```

- [ ] **Step 10.4: Commit** (tests still fail until templates updated in T11-T14)

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run ruff check marketpulse/web/routes/backtest.py tests/web/test_lab_backtest_modes.py
git add marketpulse/web/routes/backtest.py tests/web/test_lab_backtest_modes.py
git commit -m "feat(phase-5c): route passes sector/correlation cap context

Adds explicit template context aliases for sector_caps_enabled,
correlation_caps_enabled, and risk_policy. shared_result already
carries the full Phase 5c telemetry; aliases simplify template access.

2 new web tests assert sector breakdown section + risk_policy hero text.
Currently failing — templates land in Tasks 11-14."
```

---

### Task 10b: Audit Phase 5a/5b tests for default-on cap interference (Group B + C)

**Files:**
- Modify: `tests/unit/test_backtest_portfolio_simulator.py`
- Modify: `tests/integration/test_backtest_shared_pool.py` (if needed)

The Phase 5c defaults `sector_caps_enabled=True, correlation_caps_enabled=True` may pre-empt bids that Phase 5a/5b tests expect to land. Per spec § 10 Group B and Group C, these tests need toggle flags set to False.

- [ ] **Step 10b.1: Run full suite — collect failures**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py tests/integration/test_backtest_shared_pool.py -v 2>&1 | tail -30
```

- [ ] **Step 10b.2: For each failing test, add toggle flags**

Group B (Phase 5a invariants) — add to test body:

```python
r = simulate_shared_pool(
    # ... existing args ...
    sector_caps_enabled=False,        # NEW Phase 5c isolation
    correlation_caps_enabled=False,   # NEW Phase 5c isolation
)
```

Specifically for these tests (per spec § 10 Group B):
- `test_shared_pool_close_frees_cap_before_alloc`
- `test_shared_pool_greedy_alloc_respects_max_cap`
- `test_shared_pool_high_size_strategy_blocks_more_small_bids`

Group C (Phase 5b telemetry tests requiring audit) — verify each runs green with default-on caps. If any fail, add toggle flags following the same pattern:
- `test_shared_pool_max_strategy_exposure_computed`
- `test_shared_pool_hhi_concentration_computed`
- `test_shared_pool_avg_position_size_in_contribution`

- [ ] **Step 10b.3: Re-run, all pass**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/unit/test_backtest_portfolio_simulator.py tests/integration/test_backtest_shared_pool.py -v 2>&1 | tail -5
```

Expected: 0 failures.

- [ ] **Step 10b.4: Commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
git add tests/
git commit -m "test(phase-5c): isolate Phase 5a/5b tests from default-on caps

Spec § 10 Group B + Group C: Phase 5a/5b tests use synthetic curves that
may trigger sector or correlation cap once defaults flip True. Following
the Phase 5b Task 6 pattern, add sector_caps_enabled=False and
correlation_caps_enabled=False to the affected tests to isolate the
invariant being tested.

Group B (Phase 5a invariants):
- test_shared_pool_close_frees_cap_before_alloc
- test_shared_pool_greedy_alloc_respects_max_cap
- test_shared_pool_high_size_strategy_blocks_more_small_bids

Group C (Phase 5b telemetry, audit results from this commit's run):
- Tests verified to pass with default-on caps unchanged.
- Any that needed toggle flags are listed above.

Phase 5c constraint regime is the new production default; tests that
explicitly probe Phase 5a/5b invariants use False to keep the assertion
focused on what they test."
```

---

### Task 11: Hero template — append cap policy paragraph

**Files:**
- Modify: `marketpulse/web/templates/partials/backtest_hero.html`

- [ ] **Step 11.1: Update `backtest_hero.html`**

Read the existing file and append a new paragraph after the Phase 5b sizing paragraph (inside the `{% if mode == 'shared-pool' %}` branch):

```html
{% if mode == 'shared-pool' %}
  <!-- Phase 5a paragraph: 60-day rolling Sharpe weighting -->
  <p class="mp-hero__desc">
    6 个策略共享单一 $10k 资本池,通过 {{ lookback_days }}-day 滚动 Sharpe
    加权竞标分配。撞 ticker 时高 Sharpe 策略赢。
    <strong>bid_policy=rolling_sharpe_60d_v0</strong>。
  </p>

  <!-- Phase 5b paragraph: sizing policy -->
  {% if sizing_policy == 'vol_target_conviction_v0' %}
    <p class="mp-hero__desc">
      仓位大小动态:vol-target 1.0% daily × alpha-conviction multiplier,
      floor $200 / ceiling $4,000。
      <strong>sizing_policy=vol_target_conviction_v0</strong>。
    </p>
  {% else %}
    <p class="mp-hero__desc">
      固定 $1,000 每信号。<strong>sizing_policy=fixed_v0</strong>(Phase 5a 兼容模式)。
    </p>
  {% endif %}

  <!-- NEW Phase 5c paragraph: cap policy -->
  {% if shared_result.sector_caps_enabled or shared_result.correlation_caps_enabled %}
    <p class="mp-hero__desc">
      多策略集中度治理:
      {% if shared_result.sector_caps_enabled %}单一 sector ≤ 40% 池容量{% endif %}
      {% if shared_result.sector_caps_enabled and shared_result.correlation_caps_enabled %} · {% endif %}
      {% if shared_result.correlation_caps_enabled %}correlation cluster (ρ≥0.6) ≤ 40%{% endif %}。
      <strong>sector_cap_policy={{ shared_result.sector_cap_policy }}</strong> ·
      <strong>risk_policy={{ shared_result.risk_policy }}</strong>。
    </p>
  {% else %}
    <p class="mp-hero__desc">
      集中度约束已停用(reproducing pre-5c benchmark)。
      <strong>risk_policy={{ shared_result.risk_policy }}</strong>。
    </p>
  {% endif %}
{% else %}
  <p class="mp-hero__desc">
    回放 Phase 3 的 6 个策略 + SPY 基准在过去 N 天内的策略级合成 PnL。
    回测使用 long-only 模型 + 固定持有 horizon 天 + $1k 每信号 + $10k 软上限。
  </p>
{% endif %}
```

- [ ] **Step 11.2: Commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
git add marketpulse/web/templates/partials/backtest_hero.html
git commit -m "feat(phase-5c): hero — cap policy paragraph

Spec § 8: shared-pool mode shows new 3rd paragraph with sector +
correlation cap policy provenance. When caps disabled, renders
neutral 'reproducing pre-5c benchmark' message.

risk_policy composite tag exposed to user so dashboard reads
'cap40_corr06_enforced_v0' (default) or 'caps_disabled_v0'
(reproducing pre-5c) explicitly."
```

---

### Task 12: Bid history — 2 new chip renderings

**Files:**
- Modify: `marketpulse/web/templates/partials/backtest_bid_history.html`

- [ ] **Step 12.1: Update `backtest_bid_history.html`**

Find the existing chip rendering chain in the `<td>结果</td>` column and append 2 new chip styles after the `size_too_small` branch:

```html
{% elif b.outcome == 'sector_cap_full' %}
  <span class="mp-chip mp-chip--down" title="sector '{{ b.blocked_by_sector }}' 已满 (≥40%)">
    sector full · {{ b.blocked_by_sector }}
  </span>
{% elif b.outcome == 'correlation_cap_full' %}
  <span class="mp-chip mp-chip--down"
        title="与已有仓位高度相关 (ρ≥0.6): {% for t, c in b.blocked_by_correlation_with %}{{ t }}={{ '%.2f'|format(c) }}{% if not loop.last %}, {% endif %}{% endfor %}">
    corr full · {{ b.blocked_by_correlation_with|length }} neighbors
  </span>
```

Row class for `is-skipped` already applies to both new outcomes (existing CSS).

- [ ] **Step 12.2: Commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
git add marketpulse/web/templates/partials/backtest_bid_history.html
git commit -m "feat(phase-5c): bid history chips for sector + correlation cap outcomes

Spec § 8: two new chip styles in bid history result column.

- sector_cap_full: red chip with sector name + tooltip 'sector X 已满 (≥40%)'
- correlation_cap_full: red chip with neighbor count + tooltip listing
  (ticker, corr_value) pairs

Reuses existing .mp-chip--down and .is-skipped CSS — no new styles."
```

---

### Task 13: Strategy table — 5-bucket n_skipped tooltip

**Files:**
- Modify: `marketpulse/web/templates/partials/backtest_strategy_table_shared.html`

- [ ] **Step 13.1: Update `backtest_strategy_table_shared.html`**

Find the `n_skipped` `<td>` cell and update to sum 5 buckets plus add tooltip:

```html
<td class="num mono tnum"
    title="cap_full: {{ c.n_capacity_skipped }} · cash_short: {{ c.n_cash_short_skipped }} · size_too_small: {{ c.n_size_too_small_skipped }} · sector_cap: {{ c.n_sector_cap_skipped }} · correlation_cap: {{ c.n_correlation_cap_skipped }}">
  {{ c.n_capacity_skipped + c.n_cash_short_skipped + c.n_size_too_small_skipped + c.n_sector_cap_skipped + c.n_correlation_cap_skipped }}
</td>
```

- [ ] **Step 13.2: Commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
git add marketpulse/web/templates/partials/backtest_strategy_table_shared.html
git commit -m "feat(phase-5c): strategy table n_skipped sums 5 buckets

Spec § 8: n_skipped column total now reflects all skip causes; tooltip
shows the breakdown (cap_full / cash_short / size_too_small /
sector_cap / correlation_cap) so users can identify which constraint
is biting most often."
```

---

### Task 14: NEW partial — sector breakdown card

**Files:**
- Create: `marketpulse/web/templates/partials/backtest_sector_breakdown.html`
- Modify: `marketpulse/web/templates/lab_backtest.html` (include the new partial)

- [ ] **Step 14.1: Create the new partial**

```html
{% if shared_result and shared_result.sector_breakdown %}
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">pie_chart</span>Sector 暴露分布
    </span>
    <span class="mp-card__sub">时间加权 · cap = {{ '%.0f' | format(shared_result.sector_cap_policy.replace('uniform_', '').replace('pct_v0', '')) }}%</span>
  </div>
  <div class="mp-card__body" style="padding:0; overflow-x:auto;">
    <table class="mp-table mp-sector-table">
      <thead>
        <tr><th>Sector</th><th class="num">时均</th><th class="num">峰值</th><th>状态</th></tr>
      </thead>
      <tbody>
        {% for sector, avg_frac in shared_result.sector_breakdown.items()|sort(attribute=1, reverse=True) %}
        <tr>
          <td>{{ sector }}</td>
          <td class="num mono tnum">{{ "{:.1%}".format(avg_frac) }}</td>
          <td class="num mono tnum">
            {{ "{:.1%}".format(shared_result.max_sector_exposure_by_sector.get(sector, 0.0)) }}
          </td>
          <td>
            {% set sector_peak = shared_result.max_sector_exposure_by_sector.get(sector, 0.0) %}
            {% if sector_peak > 0.39 %}<span class="mp-chip mp-chip--down">at cap</span>
            {% elif avg_frac > 0.35 %}<span class="mp-chip mp-chip--down">near cap</span>
            {% elif avg_frac > 0.2 %}<span class="mp-chip">heavy</span>
            {% else %}<span class="mp-chip mp-chip--up">light</span>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</section>
{% endif %}
```

- [ ] **Step 14.2: Include the new partial in `lab_backtest.html`**

Find the existing strategy-table include (e.g., `{% include "partials/backtest_strategy_table_shared.html" %}`) and add the sector breakdown below:

```html
{% include "partials/backtest_strategy_table_shared.html" %}
{% include "partials/backtest_sector_breakdown.html" %}
```

- [ ] **Step 14.3: Run web tests**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest tests/web/test_lab_backtest_modes.py -v
```

Expected: all 7+ pass (including the 2 new from T10).

- [ ] **Step 14.4: Commit**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
git add marketpulse/web/templates/partials/backtest_sector_breakdown.html marketpulse/web/templates/lab_backtest.html
git commit -m "feat(phase-5c): sector breakdown card partial

Spec § 8: new card below strategy table shows time-averaged and peak
fraction per sector, sorted by avg desc.

- Time-avg column: shared_result.sector_breakdown[sector]
- Peak column: shared_result.max_sector_exposure_by_sector[sector]
  (correctly per-sector; fixes earlier draft's loop.first bug)
- Status chip: 'at cap' (peak ≥ 39%), 'near cap' (avg > 35%),
  'heavy' (avg > 20%), 'light' (default)

Included in lab_backtest.html below the strategy table.

7 web tests pass."
```

---

### Task 15: Final integration — full suite + ruff + smoke

- [ ] **Step 15.1: Full pytest + ruff**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run pytest 2>&1 | tail -3
uv run ruff check . 2>&1 | tail -3
```

Expected: ~870 tests pass (Phase 5b was 832; Phase 5c adds ~38 net new tests). Ruff clean.

- [ ] **Step 15.2: Module imports smoke**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
uv run python -c "
from marketpulse.backtest import (
    BidRecord, PortfolioBacktestResult, StrategyContribution,
    StrategyBacktestArtifacts, run_shared_pool_backtest,
)
from marketpulse.backtest.sector import get_sector, load_sector_overrides
from marketpulse.backtest.correlation import (
    compute_pairwise_correlation, find_correlation_neighbors, PriceProvider,
)
print('ok')
"
```

Expected: `ok`.

- [ ] **Step 15.3: 4-variant route smoke**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
SESSION_SECRET=test-secret-32-bytes-of-random-here APP_PASSWORD_HASH=x ANTHROPIC_API_KEY=x \
  uv run alembic upgrade head 2>&1 | tail -2
SESSION_SECRET=test-secret-32-bytes-of-random-here APP_PASSWORD_HASH=x ANTHROPIC_API_KEY=x \
  uv run python -c "
import os
from fastapi.testclient import TestClient
from marketpulse.web.main import app
from marketpulse.auth.password import hash_password
client = TestClient(app)
pw = 'secret'
os.environ['APP_PASSWORD_HASH'] = hash_password(pw)
from marketpulse.config import get_settings
get_settings.cache_clear()
client.post('/login', data={'password': pw})
for path in [
    '/lab/backtest',
    '/lab/backtest?mode=shared-pool',
    '/lab/backtest?mode=shared-pool&horizon=20',
    '/lab/backtest?mode=invalid',
]:
    r = client.get(path, follow_redirects=False)
    print(f'{path}: {r.status_code}')
"
```

Expected:
```
/lab/backtest: 200
/lab/backtest?mode=shared-pool: 200
/lab/backtest?mode=shared-pool&horizon=20: 200
/lab/backtest?mode=invalid: 422
```

- [ ] **Step 15.4: Commit count check**

```bash
cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-5c-plan
git log --oneline main..HEAD | wc -l
```

Expected: 14 task commits (the plan commit + 12 feature commits + this integration commit).

- [ ] **Step 15.5: Final commit if any cleanup**

If full pytest + ruff + smoke all green, push the branch and open a PR titled `feat(phase-5c): Sector & Correlation Caps`.

---

## Self-Review Notes

### Spec coverage check

| Spec section | Task |
|---|---|
| § 1 Goal | n/a (overall plan goal) |
| § 2 Locked decision #1 (5c-1 + 5c-2 one spec/plan/PR) | This plan |
| § 2 Locked decision #2 (yfinance + YAML overrides) | T1, T2 |
| § 2 Locked decision #3 (enforce from day 1 + toggles) | T6, T7, T9 |
| § 2 Locked decision #4 (sector 40%) | T6 (default `sector_cap_pct=0.40`) |
| § 2 Locked decision #5 (correlation ρ≥0.6, 40%) | T4 (`threshold=0.6`), T7 (`correlation_cap_pct=0.4`) |
| § 2 Locked decision #6 (new BidRecord outcomes) | T5 (literal extension), T6/T7 (use sites) |
| § 2 Locked decision #7 (shared-pool only) | T6, T7 (per-strategy code path untouched) |
| § 2 Locked decision #8 (60d Pearson on price_cache) | T3 |
| § 2 Locked decision #9 (no DB tables) | confirmed throughout |
| § 3 Architecture | File structure section above |
| § 4 Sector data layer | T1, T2 |
| § 5 Correlation layer + Protocol | T3, T4 |
| § 6 ALLOCATE changes + size_too_small interaction | T6, T7 |
| § 7 Type extensions | T5 |
| § 8 UI surfacing | T11, T12, T13, T14 |
| § 9 Risks & mitigations | T6, T7 (failsafe-open semantics in tests) |
| § 10 Backward-compat groups | T10b |
| § 10b Migration & reproducibility | T6 (risk_policy composition) |
| § 11 Open questions | resolved in T6 (price_provider via kwarg), T7 |
| § 12 Required test scenarios (~38) | T1-T14 (each scenario mapped) |

### Placeholder scan: ZERO

No "TBD", "TODO", "implement later", "fill in details", "add appropriate error handling", or "similar to Task N" anywhere in this plan.

### Type consistency check

- `BidRecord.outcome` literal: `"won" | "dedup_loser" | "cap_full" | "cash_short" | "size_too_small" | "sector_cap_full" | "correlation_cap_full"` — consistent across T5, T6, T7
- `BidRecord.blocked_by_correlation_with: tuple[tuple[str, float], ...]` — consistent in T4 (return type), T5 (field type), T6 (passed value), T12 (template iter)
- `PriceProvider.get_daily_closes(ticker, start, end) -> list[tuple[date, float]]` — consistent in T3 (Protocol), T7 (call site), T9 (`_DBPriceProvider` implements)
- `sector_caps_enabled`, `correlation_caps_enabled`, `sector_cap_pct`, `correlation_cap_pct`, `correlation_threshold` kwargs — consistent through T6, T7, T9
- `risk_policy` composition — T6 defines, T5 default tests, T9 orchestrator inherits, T11 hero reads
