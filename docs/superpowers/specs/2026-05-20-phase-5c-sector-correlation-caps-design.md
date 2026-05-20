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
| 8 | **Correlation data**: 60d daily Pearson on `price_cache` returns (identical window to Phase 5b `rolling_sigma` / `rolling_alpha`) | LOCKED |
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

    Causality: window is identical to rolling_sigma / rolling_alpha (Phase 5b lock).
    """


def find_correlation_neighbors(
    candidate_ticker: str,
    open_position_tickers: list[str],
    *,
    as_of: date,
    threshold: float = 0.6,
    lookback_days: int = 60,
    price_provider: PriceProvider,
) -> tuple[list[str], dict[str, float | None]]:
    """For a candidate bid, return:
      - neighbors: list of open-position tickers whose pairwise corr >= threshold
      - diagnostics: dict[ticker -> corr value or None] for ALL pairs checked

    The diagnostics map is preserved for bid history display:
    'blocked because AAPL ρ=0.72, GOOGL ρ=0.68 already at 38% of pool'.
    """
```

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

The simplification avoids transitive closure complexity. The cost: A and C might both be opened even though they share B as a common driver. In practice the cap on (A+B) and (B+C) separately already constrains exposure within ~2x of the cluster cap.

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
        neighbors, _ = find_correlation_neighbors(
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
                blocked_by_correlation_with=tuple(neighbors),
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

### Order of checks (cheapest first)

1. **cap_full** — O(1) sum + compare against constant
2. **cash_short** — O(1) compare
3. **sector_cap_full** — O(1) dict lookup
4. **correlation_cap_full** — O(N) pairwise corr (with LRU cache, mostly O(1) on hit)

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
    blocked_by_sector: str | None = None              # only for "sector_cap_full"
    blocked_by_correlation_with: tuple[str, ...] = () # only for "correlation_cap_full"
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
- **`blocked_by_correlation_with`**: tuple of ticker symbols (from `open_positions`) whose pairwise ρ ≥ 0.6 with the candidate. Stored as tuple (immutable, dataclass-frozen-compatible)
- **`max_sector_exposure`**: peak single-day single-sector fraction. Computed daily and `max()`-reduced over the backtest window
- **`sector_breakdown`**: time-averaged. Each sector's value = mean over all days of `Σ(positions in sector) / pool_capital`. Sums to ≤ 1.0 (typically less, since positions don't fill the pool every day)
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
| Cap fields leak into Phase 4 per-strategy result via shared dataclass | Phase 4 uses `StrategyBacktestResult`, not `PortfolioBacktestResult`. Different dataclasses, no contamination |
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

The plan will list the exact test files and the toggle additions in a dedicated migration task.

---

## 11. Open Questions for Plan-Writing Phase

These should be resolved during plan-writing, not in the spec:

- Exact `_compute_sector_breakdown` daily collection: per-day snapshot dict, then mean at finalize? Or running EMA?
- Where to inject `price_provider` into the simulator signature — through `simulate_shared_pool` kwarg or via DI?
- Test fixture: a deterministic ticker→sector lookup function for unit tests (avoids real yfinance calls)
- CSS: does the existing `.mp-chip--down` style work for both new outcomes, or should `correlation_cap_full` get its own visual treatment?
- Hero template — should the new 3rd paragraph hide entirely when both caps are disabled, or show a neutral "caps disabled" line?

---

## Appendix A: Glossary

- **Sector** — a string label classifying a ticker's industry exposure. Sourced from yfinance + YAML overrides. Examples: `"Technology"`, `"Energy"`, `"leveraged_qqq"`, `"unknown"`
- **Correlation cluster** — for a candidate bid, the set of open-position tickers with pairwise ρ ≥ threshold against the candidate. Computed independently per candidate; not transitive
- **Cap** — an upper bound on cumulative exposure. Sector cap = max fraction of pool allocated to one sector. Correlation cap = max fraction allocated to one correlation cluster
- **Failsafe-open** — when input data is missing, fall back to "do not block." Mirrors Phase 5b cold-start (`vol_scale=1.0`)
- **Failsafe-degrade** — when configuration is malformed, log error and continue with safe defaults. Never crash simulator
- **Neighbor sum** — the algorithm for computing correlation cluster exposure: candidate + all open positions with pairwise corr above threshold. Avoids transitive cluster detection
