# Phase 5c — Cross-Strategy Sector & Correlation Caps Design

**Status:** Locked
**Author:** harvey
**Date:** 2026-05-20
**Phase position:** After Phase 5b (Dynamic Position Sizing); before Phase 5d (Contribution-Adjusted Sharpe)

---

## 1. Goal

Phase 5b shipped two observation-only telemetry fields (`max_strategy_exposure`, `hhi_concentration`) under the explicit deferral "Phase 5d will read them to enforce caps." Phase 5c keeps those Phase 5b telemetry fields **observation-only** (Phase 5d still owns them) and instead introduces **two new constraint dimensions** that are **enforced from day 1**:

1. **Sector cap (5c-1)** — single sector ≤ 40% of pool capital
2. **Correlation cap (5c-2)** — neighbor-sum cluster (pairwise ρ ≥ 0.6) ≤ 40% of pool capital

Both are gated by independent toggle flags (`sector_caps_enabled`, `correlation_caps_enabled`) following the Phase 5b `sizing_enabled` pattern. The combined effect: the shared pool stops over-concentrating in correlated assets like "AAPL + GOOGL + TQQQ all big-tech" even when each individual bid has a high Sharpe.

### Why this and not 5b-3 (per-strategy YAML sizing override)?

5b-3 makes per-strategy sizing models tuneable in YAML (Kelly, fractional, risk-parity). It requires multi-strategy data to evaluate — currently NAS prod has all 12 analyses classified as `general` strategy (Phase 3 router bias), so 5b-3 would ship into a world where the YAML override is dead code. Phase 5c sector + correlation caps are independent of that data state — they constrain at the bid-allocation layer and produce visible diagnostics even with a single strategy.

5b-3 is folded into the Phase 5c thinking pool but explicitly deferred until router diversity improves.

---

## 2. Locked Decisions

| # | Decision | Status |
|---|----------|--------|
| 1 | **Scope**: 5c-1 (sector cap) + 5c-2 (correlation cap) merged into one spec, one plan, one PR (Phase 5b decomposition pattern) | LOCKED |
| 2 | **Sector data source**: yfinance `Ticker.info['sector']` + `config/sector_overrides.yaml` for ETFs and edge cases | LOCKED |
| 3 | **Enforcement cadence**: enforce from day 1, with `sector_caps_enabled` / `correlation_caps_enabled` toggle flags (defaults True) | LOCKED |
| 4 | **Sector cap**: 40% × `pool_capital` ($4k on the default $10k pool, equal to Phase 5b's `max_position`) | LOCKED |
| 5 | **Correlation cap**: ρ ≥ 0.6 Pearson threshold, 40% × `pool_capital`, "neighbor sum" algorithm (no transitive clustering) | LOCKED |
| 6 | **New BidRecord outcomes**: `sector_cap_full`, `correlation_cap_full` (parallel to existing `cap_full`) | LOCKED |
| 7 | **Mode scope**: shared-pool mode only. Per-strategy mode is unaffected | LOCKED |
| 8 | **Correlation data**: 60d daily Pearson, **window duration identical** to Phase 5b `rolling_sigma`/`rolling_alpha` (60 days), but **data source is `price_cache`** (raw OHLC close prices per ticker), not the per-strategy equity curves Phase 5b uses for σ/α. Different denominators — see §5 for the rationale | LOCKED |
| 9 | **No new DB tables, no Alembic migration**. Sector cache lives in `data/sector_cache.json`; correlation results are LRU-cached in-memory per backtest run | LOCKED |

### Derived locks (not user-decided but spec-locked)

- **Daily loop ORDER**: `CLOSE → BID → WEIGHT → SIZE → DEDUP → ALLOC → MTM → RECORD` — unchanged from Phase 5b. New cap checks slot **inside** the ALLOC step, **after** `cap_full` and `cash_short`, **before** position opening
- **BidRecord new diagnostic fields**: `blocked_by_sector: str | None = None` (populated only for `sector_cap_full`) and `blocked_by_correlation_with: tuple[str, ...] = ()` (populated only for `correlation_cap_full`)
- **`unknown` sector is a real sector**: tickers whose yfinance fetch fails get sector `"unknown"`, which itself counts against the 40% cap. Failsafe-degrade-not-crash
- **Failsafe-open for correlation cold-start**: when price_cache lacks enough data for a ticker (< 30 overlapping days), `find_correlation_neighbors` returns empty → cap does not trigger. Mirrors Phase 5b's `vol_scale=1.0` cold-start

### Out of scope (explicit deferrals)

- **Per-sector custom caps** — uniform 40% only. YAML config (`config/sector_caps.yaml`) deferred
- **Per-strategy sector exemption** — e.g., `sector_rotation` strategy wanting 80% cap. Folded into the 5b-3 deferral
- **Cluster algorithm upgrade** (agglomerative / DBSCAN) — neighbor-sum is sufficient; upgrade requires prod data showing misclassification
- **Dynamic cap adjustment** (drawdown-aware, regime-aware) — Phase 7 territory
- **Sector hierarchy / sub-sectors** — flat strings only
- **Persisted `ticker_metadata` DB table** — JSON cache file avoids migration cost
- **`?sector_caps=off` URL A/B toggle** — defer until A/B comparison is needed

---

## 3. Architecture

```
marketpulse/backtest/
├── sector.py                              NEW: ticker→sector lookup + cache + YAML override loader
├── correlation.py                         NEW: pairwise corr matrix + find_correlation_neighbors()
├── portfolio_simulator.py                 MODIFY: ALLOCATE step adds 2 cap checks
├── types.py                               MODIFY: BidRecord outcome literal, 2 new fields,
│                                                  StrategyContribution +2 n_skipped counters,
│                                                  PortfolioBacktestResult +6 sector/corr fields
├── simulator.py                           MODIFY: run_shared_pool_backtest threads new knobs
└── sharpe.py                              UNCHANGED — Phase 5b's signal computation untouched

config/
└── sector_overrides.yaml                  NEW: ticker→sector overrides for ETFs and edge cases

data/
└── sector_cache.json                      NEW (runtime artifact, gitignored): yfinance fetch cache

marketpulse/web/
├── routes/backtest.py                     MODIFY: compute sector_breakdown + max_sector,
│                                                  pass via template context
└── templates/partials/
    ├── backtest_hero.html                 MODIFY: + 3rd paragraph for sector/corr cap policy
    ├── backtest_bid_history.html          MODIFY: render 2 new outcome chips with diagnostic
    ├── backtest_strategy_table_shared.html MODIFY: n_skipped sums 5 buckets; tooltip breakdown
    └── backtest_sector_breakdown.html     NEW: sector breakdown card placed below strategy table

tests/
├── unit/
│   ├── test_backtest_sector.py            NEW: ~10 tests (override + fallback + cache)
│   ├── test_backtest_correlation.py       NEW: ~10 tests (pearson + neighbor finding + cold-start)
│   └── test_backtest_portfolio_simulator.py MODIFY: + ~10 cap enforcement tests
├── integration/
│   └── test_backtest_shared_pool.py       MODIFY: + 3 cap orchestrator tests
└── web/
    └── test_lab_backtest_modes.py         MODIFY: + 2 UI assertions
```

### Isolation principles

- `sector.py` is a **pure function module**. Its public surface is `get_sector(ticker) -> str` and `load_sector_overrides() -> dict[str, str]`. yfinance fetch and JSON cache are internal implementation details. ALLOCATE never imports yfinance directly
- `correlation.py` is a **pure function module**. Its public surface is `compute_pairwise_correlation(a, b, as_of, ...)` and `find_correlation_neighbors(candidate, open_tickers, as_of, ...)`. Caller passes a `price_provider` callable; no DB queries inside the module
- `portfolio_simulator.py` ALLOCATE step **does not** know about yfinance, the JSON cache, or the correlation matrix internals. It calls the two module APIs

### Key non-goals

- **No new DB tables** (sector_cache.json is a runtime artifact, like price_cache mirrors)
- **No cluster algorithm library** (neighbor-sum is O(N²) at the open-positions level; N is ≤ 10 in practice)
- **No change to `sharpe.py`** — Phase 5b signal stack frozen

---

## 4. Sector Data Layer

### Public API (`marketpulse/backtest/sector.py`)

```python
def get_sector(ticker: str, *, yf_client=None) -> str:
    """Return canonical sector for ticker. Never None — falls back to 'unknown'.

    Resolution order (first match wins):
      1. config/sector_overrides.yaml — manual override (highest priority)
      2. In-memory process cache (populated from previous yfinance lookup)
      3. yfinance Ticker.info['sector'] (lazy fetch, cached on success)
      4. 'unknown' fallback (logged once per session per ticker)
    """


def load_sector_overrides(path: Path | str | None = None) -> dict[str, str]:
    """Load and validate config/sector_overrides.yaml. Cached for process lifetime.

    Validation:
      - Each value must be a non-empty str
      - Empty/missing file returns {} silently (no overrides)
      - YAML parse error logs ERROR and returns {}
      - Returns empty dict on validation failure, never raises
    """


def save_sector_cache(cache: dict[str, str], path: Path | None = None) -> None:
    """Persist in-memory sector lookup to data/sector_cache.json. Called by yfinance fetch."""


def load_sector_cache(path: Path | None = None) -> dict[str, str]:
    """Load data/sector_cache.json into in-memory cache on simulator startup."""
```

### `config/sector_overrides.yaml`

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
  TQQQ: leveraged_qqq        # 3x QQQ; yfinance says "Financial Services"
  TNA:  leveraged_small_cap  # 3x Russell 2000
  QBTS: quantum_compute      # D-Wave Quantum
  QUBT: quantum_compute      # Quantum Computing Inc
```

### Cache behavior

- **In-memory** (`_SECTOR_CACHE: dict[str, str]`): per-process, populated on first lookup, never invalidated mid-run
- **On-disk** (`data/sector_cache.json`): persisted on successful yfinance fetch, loaded at simulator startup
- **Expiry**: cache file is overwritten in-place; for re-fetch, delete the file. The Phase 5b cadence (sector rebalances rarely occur intra-month) makes 30-day staleness acceptable. No automated TTL in v0
- **`.gitignore` adds `data/sector_cache.json`** — runtime artifact, do not commit

### Failure modes

| Failure | Behavior |
|---|---|
| yfinance unreachable | Log WARNING once per ticker per session, return `"unknown"`, do not retry until process restart |
| yfinance returns `None` for `sector` | Same as above — sector becomes `"unknown"` |
| sector_overrides.yaml parse error | Log ERROR, return `{}` (no overrides), do not crash |
| sector_overrides.yaml missing | Return `{}` silently |
| sector_cache.json corrupt | Log WARNING, return `{}`, continue (cache rebuilds via yfinance) |

---

## 5. Correlation Layer

### Public API (`marketpulse/backtest/correlation.py`)

```python
from typing import Protocol

class PriceProvider(Protocol):
    """Read-only price interface consumed by correlation calculations.

    Implementations: the simulator's normal yfinance-backed price_cache wrapper
    in production; a deterministic in-memory dict-backed fake in tests.
    """
    def get_daily_closes(
        self,
        ticker: str,
        start: date,
        end: date,
    ) -> list[tuple[date, float]]:
        """Return (date, close) tuples for ticker, dates in [start, end). Sorted ascending."""


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
      - Either ticker missing from price_cache for the window
      - Overlapping days < min_overlap (cold-start protection)
      - Computed corr is NaN (zero variance in either series)
      - ticker_a == ticker_b (self-pair short-circuit; see contract below)

    Contract:
      - Causality: window is [as_of - lookback_days, as_of); outcomes after
        as_of are excluded. Same window duration as Phase 5b rolling_sigma.
      - Data source: PriceProvider.get_daily_closes (price_cache mirror),
        NOT per-strategy equity curves.
      - Self-pair: ticker_a == ticker_b returns None (NOT 1.0). The caller
        does not want a position to be its own "neighbor".
    """


def find_correlation_neighbors(
    candidate_ticker: str,
    open_position_tickers: list[str],
    *,
    as_of: date,
    threshold: float = 0.6,
    lookback_days: int = 60,
    price_provider: PriceProvider,
) -> tuple[list[str], tuple[tuple[str, float], ...]]:
    """For a candidate bid, return:
      - neighbors: list of open-position tickers whose pairwise corr >= threshold
      - diagnostics: tuple of (ticker, corr_value) pairs for ALL pairs checked
        where corr is not None. Sorted by corr descending. Hashable, frozen-safe
        for embedding in BidRecord.

    Self-pair exclusion: if candidate_ticker appears in open_position_tickers
    (e.g., two strategies bidding the same ticker on adjacent days, or a stale
    position from yesterday), it is filtered out of the input list before
    pairing. Self-correlation is never a meaningful signal.

    The diagnostics tuple is preserved by the simulator and embedded in the
    BidRecord.blocked_by_correlation_with field for tooltip rendering:
    'blocked because AAPL ρ=0.72, GOOGL ρ=0.68 already at 38% of pool'.
    """
```

### Why `price_cache` not equity curves for correlation data

Phase 5b's `rolling_sigma`/`rolling_alpha` consume each strategy's per-strategy equity curve (the Phase 4 isolated-pool curve). That curve embeds both strategy timing and position sizing, which is fine for measuring **strategy-level** σ/α.

Correlation, by contrast, is a **ticker-level** property. We want "how correlated are AAPL and GOOGL price moves?" — not "how correlated are AAPL-bid trades and GOOGL-bid trades after strategy filtering?" The raw close-to-close return series from `price_cache` is the right denominator. Two completely separate strategies that happen to trade highly correlated tickers should both feel the correlation cap; this only happens if we measure at ticker level.

### Algorithm (neighbor sum)

For each candidate bid `b` during ALLOCATE:

1. `neighbors = find_correlation_neighbors(b.ticker, [p.ticker for p in open_positions], ...)` returns tickers with pairwise ρ ≥ 0.6
2. `cluster = {b} ∪ {p for p in open_positions if p.ticker in neighbors}`
3. `cluster_exposure = b.requested_size + sum(p.position_size for p in cluster - {b})`
4. If `cluster_exposure > correlation_cap_pct × pool_capital` → reject as `correlation_cap_full`

This is **not** transitive. If A↔B has ρ=0.7 and B↔C has ρ=0.7 but A↔C has ρ=0.4, then:
- Candidate A finds neighbors {B} (not C)
- Candidate C finds neighbors {B} (not A)
- Each cluster is computed independently per candidate

The simplification avoids transitive closure complexity. The cost: A and C might both be opened even though they share B as a common driver.

**Worked example of the 2× bound.** Cap = $4000, B already open at $3000:

- Candidate A: cluster={A,B}; A_size + 3000 must ≤ 4000 → A_size ≤ 1000
- Candidate C (later same day): cluster={C,B}; C_size + 3000 must ≤ 4000 → C_size ≤ 1000

After both fire: total in {A,B,C} = A_size + 3000 + C_size ≤ 1000 + 3000 + 1000 = 5000 (peak), or 5/4 = **1.25× the cluster cap**, not 2×. The reviewer's earlier "~2× upper bound" was conservative; in practice it's lower because each pairwise check binds the size of the new entrant tightly. Worst case is when the shared driver (B) is small relative to A+C, which yields the 2× ceiling.

### Cold-start (failsafe-open)

When `price_cache` doesn't have enough overlap for a pair:
- `compute_pairwise_correlation` returns None
- `find_correlation_neighbors` treats None as "not a neighbor"
- Empty neighbors → cluster = {candidate} only → cluster_exposure = candidate.size only
- Cap not triggered → bid opens

This means the first ~30 trading days of any new ticker effectively bypass correlation cap. Documented in hero text or runbook.

### Caching

- **Per-backtest LRU**: `compute_pairwise_correlation` is wrapped in `functools.lru_cache(maxsize=256)` for a single `simulate_shared_pool` call
- **Discarded between runs**: each new backtest starts with empty cache
- **No persistence**: correlation drifts faster than sector, recomputation is cheap

### Performance budget

- Watchlist: 7 tickers × 60d returns = 420 floats
- Pearson corr: ~0.1ms per pair on numpy
- ALLOCATE step per day (worst case): 7 candidates × 6 open positions = 42 pairs
- LRU cache hit rate on a 100-day backtest: typically > 95% after warmup
- Total ALLOCATE overhead: < 1 second per 100-day backtest

---

## 6. Daily Loop — ALLOCATE Step Internal Flow

The Phase 5b 8-step loop is unchanged. Inside ALLOCATE:

### Interaction with Phase 5b SIZE COMPUTE step

Phase 5b's SIZE COMPUTE step (positioned between WEIGHT and DEDUP) filters out strategies whose `compute_position_sizes` returns None — those bids exit with outcome `size_too_small` and **never reach ALLOCATE**. Consequently:

- `size_too_small` bids are **not** subject to sector cap or correlation cap
- The cap pre-warm loop (`sector_by_ticker` and `sector_exposure` dicts) skips strategies that did not survive SIZE COMPUTE
- `position_sizes[b.strategy]` is guaranteed to be a real float (not None) for every bid in `sorted_winners` — no KeyError risk
- In the bid history table, a strategy can appear with at most one of `{size_too_small, sector_cap_full, correlation_cap_full, cap_full, cash_short, won, dedup_loser}` per (date, ticker) — the outcomes are mutually exclusive per bid

```python
# ─── ALLOCATE (capital + sector + correlation constrained) ───
# Pre-warm: get sectors for all candidate + open-position tickers
all_tickers = {b.ticker for b in sorted_winners} | {p.ticker for p in open_positions}
sector_by_ticker = {t: get_sector(t) for t in all_tickers}

sector_cap_dollars = sector_cap_pct * pool_capital  # 0.4 * 10000 = 4000
correlation_cap_dollars = correlation_cap_pct * pool_capital  # 0.4 * 10000 = 4000

# Running per-sector exposure
sector_exposure: dict[str, float] = {}
for p in open_positions:
    s = sector_by_ticker[p.ticker]
    sector_exposure[s] = sector_exposure.get(s, 0.0) + p.position_size

for b in sorted_winners:
    requested_size = position_sizes[b.strategy]
    candidate_sector = sector_by_ticker[b.ticker]

    # ── Existing Phase 5a/5b checks (unchanged) ──
    if capital_in_use + requested_size > max_capital_in_use:
        all_bid_records.append(BidRecord(..., outcome="cap_full", ...))
        continue
    if cash < requested_size:
        all_bid_records.append(BidRecord(..., outcome="cash_short", ...))
        continue

    # ── NEW: Phase 5c-1 sector cap ──
    if sector_caps_enabled:
        if sector_exposure.get(candidate_sector, 0.0) + requested_size > sector_cap_dollars:
            all_bid_records.append(BidRecord(
                date=d, strategy=b.strategy, ticker=b.ticker,
                weight=weights[b.strategy],
                outcome="sector_cap_full",
                winner=None,
                position_size=requested_size,
                blocked_by_sector=candidate_sector,
            ))
            n_sector_cap_skipped_by_strategy[b.strategy] = (
                n_sector_cap_skipped_by_strategy.get(b.strategy, 0) + 1
            )
            continue

    # ── NEW: Phase 5c-2 correlation cap ──
    if correlation_caps_enabled:
        open_tickers = [p.ticker for p in open_positions]
        neighbors, corr_diagnostics = find_correlation_neighbors(
            b.ticker, open_tickers,
            as_of=d, threshold=correlation_threshold,
            price_provider=price_provider,
        )
        cluster_exposure = requested_size + sum(
            p.position_size for p in open_positions if p.ticker in neighbors
        )
        if cluster_exposure > correlation_cap_dollars:
            all_bid_records.append(BidRecord(
                date=d, strategy=b.strategy, ticker=b.ticker,
                weight=weights[b.strategy],
                outcome="correlation_cap_full",
                winner=None,
                position_size=requested_size,
                blocked_by_correlation_with=corr_diagnostics,
            ))
            n_correlation_cap_skipped_by_strategy[b.strategy] = (
                n_correlation_cap_skipped_by_strategy.get(b.strategy, 0) + 1
            )
            continue

    # ── Open position (unchanged from Phase 5b) ──
    open_positions.append(_OpenPosition(
        strategy=b.strategy, ticker=b.ticker,
        entry_date=d, entry_price=b.event_price,
        horizon_date=b.horizon_date, horizon_price=b.horizon_price,
        position_size=requested_size,
    ))
    cash -= requested_size
    sector_exposure[candidate_sector] = sector_exposure.get(candidate_sector, 0.0) + requested_size
    n_trades_by_strategy[b.strategy] = n_trades_by_strategy.get(b.strategy, 0) + 1
    all_bid_records.append(BidRecord(
        ..., outcome="won", position_size=requested_size, ...
    ))
```

### Order of checks (cheapest first — deliberate trade-off)

1. **cap_full** — O(1) sum + compare against constant
2. **cash_short** — O(1) compare
3. **sector_cap_full** — O(1) dict lookup
4. **correlation_cap_full** — O(N) pairwise corr (with LRU cache, mostly O(1) on hit)

This is a **deliberate priority order** that trades diagnostic clarity for runtime cost. Consider: a $4000 candidate, pool already at $7000 with $6000 of that in Technology sector. cap_full fires first (`$7k + $4k > $10k`). The bid history shows `cap_full`, even though the deeper signal is "Tech is at 60% of pool, well past the 40% cap."

**Why this is acceptable for v0**:
- `cap_full` and `cash_short` are nearly free; running them first costs ~zero
- When the pool is below the global cap, `cap_full` cannot fire, so sector/correlation diagnoses get full visibility
- A bid that's `cap_full` AND would also trigger `sector_cap_full` is a "double squeeze" — both signals would be useful but the pool itself is the primary constraint
- An advanced UI can render a tooltip like "blocked by cap_full; would also have hit sector cap (Tech at 60%)" by re-checking the sector dict at bid_history render time. Deferred to plan if needed

**Future option**: reorder to `sector → correlation → cap → cash` for richer diagnostics, paying ~10× the per-bid cost. Not committed to v0; flagged in the open questions.

### Sector exposure tracker invariants

- `sector_exposure` is **rebuilt fresh** each ALLOCATE step from current `open_positions`. Cheap (≤ 10 positions)
- Open positions update `sector_exposure` **immediately** within the loop — next candidate sees updated value
- Rejected candidates do **not** update `sector_exposure`

### CLOSE step

When positions are closed (horizon reached), they are removed from `open_positions`. The next day's ALLOCATE rebuilds `sector_exposure` from the new `open_positions`, so closed positions automatically free up sector capacity. No separate maintenance needed.

---

## 7. Type Extensions

### `BidRecord`

```python
@dataclass(frozen=True)
class BidRecord:
    date: date
    strategy: str
    ticker: str
    weight: float
    outcome: Literal[
        "won", "dedup_loser", "cap_full", "cash_short",
        "size_too_small",                # Phase 5b
        "sector_cap_full",               # NEW Phase 5c-1
        "correlation_cap_full",          # NEW Phase 5c-2
    ]
    winner: str | None
    position_size: float
    # NEW Phase 5c diagnostic fields (default empty; populated only for matching outcome)
    blocked_by_sector: str | None = None                                 # only for "sector_cap_full"
    blocked_by_correlation_with: tuple[tuple[str, float], ...] = ()      # only for "correlation_cap_full"
                                                                          # each pair = (ticker, corr_value)
                                                                          # sorted by corr desc
```

### `StrategyContribution`

```python
@dataclass(frozen=True)
class StrategyContribution:
    strategy: str
    display_name: str
    n_trades: int
    n_dedup_skipped: int
    n_capacity_skipped: int            # Phase 5a's cap_full
    n_cash_short_skipped: int
    n_size_too_small_skipped: int      # Phase 5b
    n_sector_cap_skipped: int          # NEW Phase 5c-1
    n_correlation_cap_skipped: int     # NEW Phase 5c-2
    contribution_pnl: float
    avg_exposure: float
    avg_bid_weight: float
    avg_position_size: float           # Phase 5b
    n_bids: int
    n_floor_hits: int
```

### `PortfolioBacktestResult`

```python
@dataclass(frozen=True)
class PortfolioBacktestResult:
    # Identity + aggregate counts + utilization (Phase 5a, unchanged)
    horizon: int
    n_trades: int
    n_dedup_total: int
    avg_capital_utilization: float

    # Phase 5b telemetry (observation-only, still owned by Phase 5d)
    max_strategy_exposure: float
    hhi_concentration: float

    # NEW Phase 5c-1: sector telemetry
    max_sector_exposure: float                  # peak Σ(positions in any one sector) / pool
    sector_breakdown: dict[str, float]          # sector → time-avg fraction of pool

    # NEW Phase 5c-2: correlation telemetry
    max_cluster_exposure: float                 # peak observed correlated-cluster size / pool
    n_correlation_cap_events: int               # total bids rejected by correlation cap

    # Performance metrics (Phase 5a/5b, unchanged)
    cumulative_return: float
    annual_return: float
    sharpe: float | None
    sortino: float | None
    max_drawdown: float
    calmar: float | None
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float

    # Series + benchmarks (Phase 5a/5b, unchanged)
    daily_equity_curve: list[tuple[date, float]]
    excess_vs_spy: float

    # Breakdown + diagnostics (Phase 5a/5b, unchanged)
    per_strategy_stats: dict[str, "StrategyContribution"]
    bid_history: list["BidRecord"]

    # Defaulted provenance (Phase 5a/5b + NEW Phase 5c)
    display_name: str = "Shared Pool"
    mtm_model: str = "linear_interpolation_v0"
    bid_policy: str = "rolling_sharpe_60d_v0"
    sizing_policy: str = "fixed_v0"                            # Phase 5b
    sector_cap_policy: str = "uniform_40pct_v0"                # NEW Phase 5c
    correlation_cap_policy: str = "neighbor_sum_rho06_40pct_v0" # NEW Phase 5c
    sector_caps_enabled: bool = True                            # NEW Phase 5c
    correlation_caps_enabled: bool = True                       # NEW Phase 5c
```

### Field semantics

- **`blocked_by_sector`**: the sector string that was already at-or-near cap when this bid was rejected. Format matches `get_sector()` output (e.g., `"Technology"`, `"leveraged_qqq"`, `"unknown"`)
- **`blocked_by_correlation_with`**: tuple of `(ticker, corr_value)` pairs from `open_positions` whose pairwise ρ ≥ 0.6 with the candidate. Sorted by corr descending. Stored as tuple-of-tuples (immutable, frozen-dataclass-safe, hashable). Example: `(("AAPL", 0.72), ("GOOGL", 0.68))`
- **`max_sector_exposure`**: peak single-day single-sector fraction. Computed daily as `max over sectors of (Σ positions in sector / pool_capital)` and `max()`-reduced over the backtest window
- **`sector_breakdown`**: time-averaged fractional exposure per sector. **Concrete semantics**:
  - Computed during finalization (not maintained as running state)
  - Iterate every calendar day in the backtest window (including days with empty pool)
  - For each day, for each open position at end-of-day, contribute `position_size / pool_capital` to that position's sector
  - Average over **the count of calendar days in the window** (denominator is fixed, NOT "days with positions"). This deliberately includes empty-pool days, so the average reflects deployment intensity, not just allocation mix
  - Result: `dict[sector_str, float]` where each value is in `[0.0, 1.0]`. The sum of values is `≤ avg_capital_utilization ≤ 1.0` (proven by linearity of expectation over the same calendar days)
  - Empty backtest (no positions ever opened) → `{}` (empty dict)
- **`max_cluster_exposure`**: similar to `max_sector_exposure` but computed at the candidate-cluster level. Tracked only when correlation caps are enabled
- **`n_correlation_cap_events`**: simple counter, used for hero dashboard or sanity check

### Schema stability when caps disabled

- `sector_caps_enabled=False` → `max_sector_exposure=0.0`, `sector_breakdown={}`. **Fields still exist** in the dataclass
- `correlation_caps_enabled=False` → `max_cluster_exposure=0.0`, `n_correlation_cap_events=0`
- UI guards (`{% if shared_result.sector_breakdown %}`) skip empty cards

---

## 8. UI Surfacing

### Hero — 3rd paragraph (shared-pool mode)

```html
{% if mode == 'shared-pool' %}
  <!-- Phase 5a paragraph: 60-day rolling Sharpe weighting -->
  <p class="mp-hero__desc">...</p>

  <!-- Phase 5b paragraph: sizing policy -->
  {% if sizing_policy == 'vol_target_conviction_v0' %}
    <p class="mp-hero__desc">...</p>
  {% endif %}

  <!-- NEW Phase 5c paragraph: cap policy -->
  {% if shared_result.sector_caps_enabled or shared_result.correlation_caps_enabled %}
    <p class="mp-hero__desc">
      多策略集中度治理:
      {% if shared_result.sector_caps_enabled %}单一 sector ≤ 40% 池容量{% endif %}
      {% if shared_result.sector_caps_enabled and shared_result.correlation_caps_enabled %} · {% endif %}
      {% if shared_result.correlation_caps_enabled %}correlation cluster (ρ≥0.6) ≤ 40%{% endif %}。
      <strong>sector_cap_policy={{ shared_result.sector_cap_policy }}</strong> ·
      <strong>correlation_cap_policy={{ shared_result.correlation_cap_policy }}</strong>。
    </p>
  {% endif %}
{% endif %}
```

### Bid history — 2 new outcome chips

Append to the existing `<td>结果</td>` conditional chain:

```html
{% elif b.outcome == 'sector_cap_full' %}
  <span class="mp-chip mp-chip--down" title="sector '{{ b.blocked_by_sector }}' 已满 (≥40%)">
    sector full · {{ b.blocked_by_sector }}
  </span>
{% elif b.outcome == 'correlation_cap_full' %}
  <span class="mp-chip mp-chip--down"
        title="与已有仓位高度相关 (ρ≥0.6): {{ b.blocked_by_correlation_with|join(', ') }}">
    corr full · {{ b.blocked_by_correlation_with|length }} neighbors
  </span>
```

Row tinting: both new outcomes use existing `.is-skipped` class (red tint). No new CSS.

### Strategy table — `n_skipped` becomes 5-bucket sum

```html
<td class="num mono tnum"
    title="cap_full: {{ c.n_capacity_skipped }} · cash_short: {{ c.n_cash_short_skipped }} ·
           size_too_small: {{ c.n_size_too_small_skipped }} ·
           sector_cap: {{ c.n_sector_cap_skipped }} ·
           correlation_cap: {{ c.n_correlation_cap_skipped }}">
  {{ c.n_capacity_skipped + c.n_cash_short_skipped + c.n_size_too_small_skipped
     + c.n_sector_cap_skipped + c.n_correlation_cap_skipped }}
</td>
```

### New partial: `backtest_sector_breakdown.html`

Placed below the strategy table:

```html
{% if shared_result and shared_result.sector_breakdown %}
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">pie_chart</span>Sector 暴露分布
    </span>
    <span class="mp-card__sub">时间加权 · cap = 40%</span>
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
            {% if loop.first %}
              <strong>{{ "{:.1%}".format(shared_result.max_sector_exposure) }}</strong>
            {% else %}—{% endif %}
          </td>
          <td>
            {% if avg_frac > 0.35 %}<span class="mp-chip mp-chip--down">near cap</span>
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

The sort ensures the top row (largest avg) shows the peak — matching `max_sector_exposure`.

### KPI strip — unchanged

The 5 existing KPIs (Pool Sharpe / Cum Ret / MaxDD / vs SPY / N dedup) are not touched. Sector info lives in the new card to avoid cluttering the strip.

---

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| yfinance `sector` field unstable or missing | Always fall back to `"unknown"` with one-time WARNING log. `unknown` counts as a real sector (also 40% cap), preventing silent failure-to-cap |
| Correlation cold-start (first ~30d) | `find_correlation_neighbors` returns `[]` when overlap < `min_overlap=30`. Cap effectively dormant during warmup. Documented in hero text or release notes |
| `sector_overrides.yaml` malformed or typo'd | Schema validation at load: each value must be non-empty str. Failure logs ERROR and returns `{}` (no overrides). Never crashes simulator |
| Sector cap too tight blocks too many bids | Telemetry `max_sector_exposure` + `n_sector_cap_skipped` per strategy expose this. Strategy table tooltip shows the breakdown. User can lower threshold or disable via toggle |
| Correlation matrix degenerate (zero variance in one ticker) | `compute_pairwise_correlation` returns None. `find_correlation_neighbors` treats as not-a-neighbor. Equivalent to failsafe-open |
| Phase 5b `position_size` semantics change | Unchanged. `sector_cap_full` and `correlation_cap_full` use the REQUESTED size (same as `cap_full`, `cash_short`) — preserves diagnostic value |
| PR review pattern: vacuous `if X: assert Y` tests | Spec requires for every cap-related test: **assert precondition first** (e.g., `assert sector_exposure_before > 3000`), **then assert outcome** (`assert b.outcome == "sector_cap_full"`). No `if X: assert Y` style allowed |
| LRU cache poisoning between tests | `compute_pairwise_correlation` LRU is at module level. Test fixtures clear it via `compute_pairwise_correlation.cache_clear()` in setup |

---

## 10. Consistency with Prior Phases

Phase 5c integrates with prior phases without breaking any locked behavior:

| Prior phase lock | Phase 5c behavior |
|---|---|
| Phase 4 per-strategy mode runs each strategy in $10k isolated pool | **Unaffected**. `sector_breakdown={}`, `sector_caps_enabled` ignored in per-strategy code path |
| Phase 5a `bid_policy="rolling_sharpe_60d_v0"` for shared pool | **Unchanged**. Cap checks happen AFTER bid weights are computed and ALLOCATE has sorted by weight |
| Phase 5a `position_size=$1000` fixed when `sizing_enabled=False` | **Compatible**. Sector cap = 40% of $10k = $4k, so a $1000 fixed bid fits up to 4 same-sector positions before cap fires |
| Phase 5b `position_size` field on BidRecord for all 5 outcomes | **Extended**. The 2 new outcomes (`sector_cap_full`, `correlation_cap_full`) follow the Phase 5b semantic: REQUESTED size, not 0.0 |
| Phase 5b `sizing_enabled=False` → `sizing_policy="fixed_v0"` | **Independent**. `sector_caps_enabled` and `correlation_caps_enabled` are orthogonal toggle flags. All 4 combinations are valid (sizing × sector × correlation) |
| Phase 5b daily loop ORDER lock | **Preserved**. New cap checks are INSIDE ALLOCATE step, not new steps |

### Backward-compat regression categories

Existing tests fall into two groups based on whether default-on caps interfere with the invariant being tested.

**Group A — pass without modification:**

- `test_size_formula_not_double_rewarding_low_vol` (Phase 5b review regression) — sizing math untouched; caps fire AFTER sizing, no interference
- `test_shared_pool_sizing_provenance_field_set` (Phase 5b) — provenance string check, independent of cap logic
- `test_shared_pool_empty_bids_returns_fixed_v0_when_disabled` (Phase 5b) — empty path, no positions to cap

**Group B — need toggle flags set to False to isolate the invariant:**

Some Phase 5a/5b invariant tests use synthetic curves that may trigger sector or correlation cap when defaults are on. Following the Phase 5b pattern (Task 6 reviewer flagged the analogous `sizing_enabled=False` requirement), these tests must pass `sector_caps_enabled=False, correlation_caps_enabled=False`:

- `test_shared_pool_close_frees_cap_before_alloc` (Phase 5a) — tests pool-level cap mechanics; same-sector positions could trigger sector cap first and mask the invariant
- `test_shared_pool_greedy_alloc_respects_max_cap` (Phase 5a) — tests greedy allocation order; sector cap could reorder which bids land
- `test_shared_pool_high_size_strategy_blocks_more_small_bids` (Phase 5b) — tests `cap_full` ordering; without isolating the cap dimension, sector cap might fire first

**Group C — Phase 5b telemetry tests requiring audit:**

These tests assert specific values for `max_strategy_exposure`, `hhi_concentration`, `avg_position_size`, or `n_size_too_small_skipped`. Default-on sector/correlation caps could pre-empt bids that those tests expect to land, changing the asserted values:

- `test_shared_pool_max_strategy_exposure_computed` — single-strategy single-sector setup; should be safe but audit for cluster cap
- `test_shared_pool_hhi_concentration_computed` — two-strategy two-ticker setup; if both tickers share a sector under yfinance default, sector cap could fire. Audit needed
- `test_shared_pool_avg_position_size_in_contribution` — 3 bids same strategy same ticker (via stub fixture); if AAPL is "Technology" and all 3 bids open, $3k Tech is fine. But if bids increase to 5+, sector cap could fire. Audit
- `test_shared_pool_n_size_too_small_in_contribution` — designed to fail at SIZE COMPUTE, so caps never fire. Safe by construction

The plan will list the exact test files and the toggle additions in a dedicated migration task. The audit pattern: run each test with default-on caps; if it fails, either (a) add the toggle flags to the test, or (b) verify the new behavior is correct and update the expected values.

---

## 11. Open Questions for Plan-Writing Phase

These have specific implementation flexibility but do not change semantics. The plan resolves each with a concrete choice:

- **`price_provider` injection point**: through `simulate_shared_pool(price_provider=...)` kwarg with a default of the existing `data_service.price_cache` wrapper, OR via attribute on the simulator class. Plan picks one
- **Test fixture for sector lookup**: a deterministic `FakeSectorProvider` in `tests/conftest.py` that monkey-patches `get_sector` to avoid real yfinance calls. Plan specifies fixture name and scope
- **Test fixture for correlation LRU clearing**: `compute_pairwise_correlation.cache_clear()` should run at module-scope teardown OR in a `pytest.fixture(autouse=True)` in the correlation test file. Plan picks one
- **CSS for `correlation_cap_full` chip**: reuse `.mp-chip--down` (red tint) OR add a new `.mp-chip--corr` (e.g., purple/desaturated). Plan picks based on visual hierarchy preference
- **Hero template when both caps disabled**: hide the 3rd paragraph entirely, OR render a neutral line "caps disabled — running unconstrained shared-pool simulation". Plan picks based on cumulative UI density

### Resolved (no longer open)

- `_compute_sector_breakdown` semantics — resolved in §7 (per-day snapshot, denominator = total calendar days, sum ≤ `avg_capital_utilization`)
- Self-pair handling in correlation — resolved in §5 (`compute_pairwise_correlation` returns None when `a == b`; `find_correlation_neighbors` filters self before pairing)
- Diagnostic-dict plumbing — resolved in §5 / §7 (`tuple[tuple[str, float], ...]` embedded in `BidRecord.blocked_by_correlation_with`)
- `size_too_small` × cap interaction — resolved in §6 (size_too_small bids exit before ALLOCATE, never subject to caps)
- Cap-check order — resolved in §6 (cheapest-first, deliberate trade-off documented)

---

## 12. Required Test Scenarios

Each enumerated scenario MUST land as at least one test in the plan. Test names are illustrative; plan can rename. Pattern requirement: **assert preconditions explicitly, then assert outcome.** No `if X: assert Y` style.

### `tests/unit/test_backtest_sector.py` (sector.py — ~10 tests)

1. `test_get_sector_returns_yfinance_sector_for_normal_equity` — AAPL via stubbed yfinance returns `"Technology"`. Assert exact string match
2. `test_get_sector_override_wins_over_yfinance` — load YAML with `TQQQ: leveraged_qqq`; yfinance returns `"Financial Services"`. Assert override wins
3. `test_get_sector_returns_unknown_when_yfinance_fails` — yfinance stub raises; `get_sector` returns `"unknown"`. Assert string match + log captured WARNING
4. `test_get_sector_returns_unknown_when_yfinance_returns_none_sector` — yfinance returns `{"sector": None}`. Assert `"unknown"`
5. `test_get_sector_caches_within_process` — call `get_sector("AAPL")` twice with a stub that increments a call counter. Assert counter == 1
6. `test_load_sector_overrides_handles_missing_file` — file absent. Assert returns `{}`, no exception
7. `test_load_sector_overrides_rejects_non_string_values` — YAML `TQQQ: 42`. Assert returns `{}` and logs ERROR (validation rejection)
8. `test_load_sector_overrides_strips_empty_strings` — YAML `TQQQ: ""`. Assert that entry filtered out (still returns `{}` for that key, falls through to yfinance)
9. `test_sector_cache_json_round_trips_via_save_and_load` — save then load. Assert dict equality
10. `test_sector_cache_json_handles_corrupt_file` — write invalid JSON; load. Assert returns `{}`, no exception, logs WARNING

### `tests/unit/test_backtest_correlation.py` (correlation.py — ~10 tests)

1. `test_pairwise_correlation_perfectly_correlated_series_returns_1` — two identical price series, `as_of` at end. Assert corr > 0.999
2. `test_pairwise_correlation_inverse_series_returns_negative_1` — one series is the reverse of the other. Assert corr < -0.999
3. `test_pairwise_correlation_returns_none_below_min_overlap` — 10 days of data, min_overlap=30. Assert returns None
4. `test_pairwise_correlation_returns_none_for_self_pair` — `a == b == "AAPL"`. Assert returns None (NOT 1.0). Critical: self-pair contract
5. `test_pairwise_correlation_returns_none_for_zero_variance_series` — one ticker has flat prices. Assert returns None
6. `test_pairwise_correlation_excludes_dates_at_or_after_as_of` — synthetic data extends past `as_of`; assert returned corr uses only pre-as_of data
7. `test_find_correlation_neighbors_returns_only_above_threshold` — 3 tickers with ρ=0.7, 0.5, 0.4 to candidate. Assert only first is in neighbors list
8. `test_find_correlation_neighbors_filters_self_from_input` — pass `open_position_tickers=["AAPL", "GOOGL"]` with `candidate="AAPL"`. Assert AAPL filtered out of pairing
9. `test_find_correlation_neighbors_diagnostics_sorted_desc` — pairs with ρ=0.5, 0.8, 0.6 (none above threshold for simplicity). Assert diagnostics tuple sorted: `(("X", 0.8), ("Y", 0.6), ("Z", 0.5))`
10. `test_find_correlation_neighbors_cold_start_returns_empty` — `price_provider` has 10 days of data, min_overlap=30. Assert returns `([], ())` — fail-safe-open

### `tests/unit/test_backtest_portfolio_simulator.py` (ALLOCATE cap enforcement — ~10 new tests)

Each test pre-loads 30-60 days of price data into a fake `PriceProvider`, then asserts a specific outcome:

1. `test_sector_cap_fires_at_boundary` — pool $10k, $3000 in Tech, $1000 Tech candidate. **Precondition assert**: sector_exposure["Technology"] == 3000. **Outcome assert**: candidate outcome == "won" AND new exposure == 4000 (exactly at boundary, NOT rejected)
2. `test_sector_cap_fires_when_crossed` — pool $10k, $3500 in Tech, $1000 Tech candidate. Precondition: 3500. Outcome: "sector_cap_full" AND blocked_by_sector == "Technology"
3. `test_sector_cap_does_not_fire_below_threshold` — pool $10k, $2000 Tech, $1500 Tech candidate. Outcome: "won". Sector exposure now 3500
4. `test_sector_cap_unknown_sector_obeys_same_cap` — ticker with no yfinance entry → sector "unknown"; 5 unknown bids of $1000 each, 4 should land, 5th blocked
5. `test_correlation_cap_fires_when_cluster_exceeds` — AAPL+GOOGL ρ=0.7; pool already has $3000 AAPL, $1500 GOOGL candidate. Cluster = AAPL+GOOGL = 4500 > 4000. Outcome: "correlation_cap_full" AND blocked_by_correlation_with == `(("AAPL", ~0.7),)`
6. `test_correlation_cap_does_not_fire_below_threshold` — AAPL+TNA ρ=0.4 (below 0.6); pool has $3000 AAPL, $1500 TNA candidate. Outcome: "won" (no neighbor)
7. `test_correlation_cap_cold_start_bypassed` — TNA has only 10 days of data, min_overlap=30. Pool has $3000 AAPL. TNA candidate. Outcome: "won" (correlation returns None → no neighbor → no cap)
8. `test_caps_disabled_via_toggle_bypassed` — set `sector_caps_enabled=False, correlation_caps_enabled=False`. Pool $3500 Tech, $1000 Tech candidate. Outcome: "won" (caps inactive)
9. `test_size_too_small_bids_not_subject_to_caps` — strategy with raw size = $50 < min_position. Strategy → size_too_small at SIZE COMPUTE. Pool $4000 Tech already, candidate is Tech ticker. Outcome: "size_too_small" (not "sector_cap_full"). Precondition: candidate exits SIZE COMPUTE before ALLOCATE
10. `test_cap_full_fires_before_sector_cap_when_both_apply` — pool at $7000, $4000 Tech, $4000 Tech candidate. Both `cap_full` ($7+$4>$10) and `sector_cap_full` ($4+$4>$4) would apply. Outcome: "cap_full" (order-of-checks lock)

### `tests/integration/test_backtest_shared_pool.py` (orchestrator — ~3 new tests)

1. `test_run_shared_pool_default_caps_enabled` — run via `run_shared_pool_backtest()` with default args; assert `result.sector_caps_enabled == True` and `result.sector_cap_policy == "uniform_40pct_v0"`
2. `test_run_shared_pool_caps_disabled_via_kwargs` — pass `sector_caps_enabled=False, correlation_caps_enabled=False`; assert result fields reflect
3. `test_run_shared_pool_sector_breakdown_populated_when_caps_active` — multi-strategy multi-ticker fixture; assert `sector_breakdown` is non-empty dict and sums match `avg_capital_utilization` within tolerance

### `tests/web/test_lab_backtest_modes.py` (UI assertions — ~2 new tests)

1. `test_lab_backtest_shared_mode_renders_sector_breakdown_card` — hits `/lab/backtest?mode=shared-pool`; assert `"Sector 暴露分布"` text appears in response
2. `test_lab_backtest_shared_mode_renders_cap_policy_in_hero` — hits same; assert `"sector_cap_policy=uniform_40pct_v0"` appears in response

### `tests/unit/test_backtest_types_phase5a.py` (type extensions — ~3 new tests)

1. `test_bid_record_sector_cap_full_outcome_literal` — `BidRecord(..., outcome="sector_cap_full", blocked_by_sector="Tech")`. Assert outcome string + blocked_by_sector value
2. `test_bid_record_correlation_cap_full_with_diagnostics` — `BidRecord(..., outcome="correlation_cap_full", blocked_by_correlation_with=(("AAPL", 0.72),))`. Assert outcome + diagnostic tuple structure
3. `test_portfolio_result_default_sector_cap_policy` — construct `PortfolioBacktestResult()` with all required args; assert `sector_cap_policy == "uniform_40pct_v0"` and `correlation_cap_policy == "neighbor_sum_rho06_40pct_v0"`

---

## Appendix A: Glossary

- **Sector** — a string label classifying a ticker's industry exposure. Sourced from yfinance + YAML overrides. Examples: `"Technology"`, `"Energy"`, `"leveraged_qqq"`, `"unknown"`
- **Correlation cluster** — for a candidate bid, the set of open-position tickers with pairwise ρ ≥ threshold against the candidate. Computed independently per candidate; not transitive
- **Cap** — an upper bound on cumulative exposure. Sector cap = max fraction of pool allocated to one sector. Correlation cap = max fraction allocated to one correlation cluster
- **Failsafe-open** — when input data is missing, fall back to "do not block." Mirrors Phase 5b cold-start (`vol_scale=1.0`)
- **Failsafe-degrade** — when configuration is malformed, log error and continue with safe defaults. Never crash simulator
- **Neighbor sum** — the algorithm for computing correlation cluster exposure: candidate + all open positions with pairwise corr above threshold. Avoids transitive cluster detection
