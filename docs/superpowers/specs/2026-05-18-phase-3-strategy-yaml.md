# Phase 3 — Strategy YAML System

> **Status:** Spec ready for plan
> **Branch:** `spec/phase-3-strategy-yaml`
> **Depends on:** Phase 1 (eval infra) + Phase 2 (verdict + scoring) — both merged in main

## Goal

Improve the quality of `/stock/{ticker}` AI deep-analysis by replacing the single generic prompt with **strategy-driven analysis**: a cheap LLM router first picks the most appropriate strategy for the ticker's current state, then deep analysis runs with that strategy's specialist instructions.

**Primary value:** Better verdicts (Claude follows a focused playbook instead of a generic three-section template).
**Secondary value:** Phase 2 hit-rate framework gains a new dimension — per-strategy scoring lets us answer "which strategy works best on tech stocks vs cyclicals."

## Non-Goals (out of scope)

- Multi-strategy parallel analysis (run N strategies on the same ticker, present comparison) — possible Phase 4 if data motivates it
- Structured `applies_when` requirements (e.g. `min_market_cap: 1e9` machine-readable filters) — v0 uses natural-language hints only
- User-defined strategies via web UI — v0 strategies are code-managed YAML files committed in repo
- Strategy-level Brier / probabilistic scoring — that's the existing "concept D" track
- Recap (`RecapService.generate()`) does NOT get strategy routing — only `/stock` deep analysis. Recap uses its own commentary prompt and continues unchanged.

## Architecture

Two-stage flow inside `AiService.analyze(ticker)`:

```
User clicks "AI 分析" on /stock/AAPL
       ↓
AiService.analyze(ticker)
       ↓
[Stage 1: Router]
   ├── Fetch market data ONCE (shared with Stage 2):
   │     - quote (price, change_pct, volume, avg_volume_20d)
   │     - fundamentals (market_cap, sector, industry)
   │     - 60d bars (for trend / RSI / volume ratio)
   │     - SPY 60d bars (for sector relative strength)
   │     - news (last 7d)
   ├── Build router context from the shared data:
   │     - price, change_pct
   │     - market_cap (USD)
   │     - 60d trend summary (MA20/50 direction, position vs 60d high/low)
   │     - sector / industry
   │     - volume_ratio_20d (today volume / 20d avg)
   │     - rsi_14 (computed from bars)
   │     - sector_rs_20d_vs_spy (ticker 20d return − SPY 20d return)
   │     - news_count_7d
   ├── Cheap LLM call (Haiku) with router prompt
   │     "Choose ONE strategy from the list; output {strategy, reason} JSON"
   ├── Router decision cached per (ticker, today_us_eastern) — same trading day
   │     re-clicks skip the LLM call
   └── Returns strategy name (one of 6)
       ↓
[Stage 2: Deep Analysis]
   ├── Load strategies/definitions/<strategy>.yaml
   ├── REUSE the bars / quote / fundamentals / news fetched in Stage 1
   │     (do not re-fetch — pass forward via local variables)
   ├── Build prompt:
   │     system = base_system + strategy.instructions
   │     user   = data snapshot (same shape as today)
   ├── LLM call (Sonnet/Opus — same model_analyze as today)
   └── Parse verdict (same VERDICTS_JSON parser as Phase 2) + record event with
       payload.strategy = "<strategy_name>",
       payload.strategy_version = "<v_N>"
       ↓
Cache key: (ticker, strategy, strategy_version, prompt_version), 24h TTL
```

**Key invariant:** From the user's POV the UX is unchanged — one button → one analysis → cached for 24h. The router stage is server-side and invisible.

**Cost delta vs current:**
- Each `/stock` analyze call: +1 Haiku call (~$0.0005) on cache miss, +0 on cache hit
- 24h cache hit rate in production is high → router cost amortized

## File Structure

```
marketpulse/
├── strategies/                          ← NEW module
│   ├── __init__.py
│   ├── loader.py                        # YAML → Strategy dataclass, startup load + validate
│   ├── router.py                        # Router prompt build + LLM parse
│   ├── selector.py                      # Picks Strategy by name, fallback to general
│   └── definitions/
│       ├── fundamental_value.yaml
│       ├── momentum_breakout.yaml
│       ├── news_event.yaml
│       ├── sector_rotation.yaml
│       ├── oversold_reversal.yaml
│       └── general.yaml                 # fallback when router fails or no clear fit

marketpulse/ai/
├── prompts.py                           # MODIFY: keep COMMENTARY/RISK; ANALYSIS becomes per-strategy
├── service.py                           # MODIFY: analyze() becomes two-stage
└── ...

marketpulse/evaluation/
└── scoring.py                           # MODIFY: 4 functions get strategy: str | None = None param

marketpulse/web/
├── routes/
│   ├── stock.py                         # MODIFY: pass `strategy` to badge context (informational)
│   └── lab.py                           # MODIFY: accept ?strategy=... query param
└── templates/
    ├── partials/
    │   ├── stock_ai_card.html           # MODIFY: show selected strategy badge in head
    │   ├── ai_track_filter_card.html    # MODIFY: add strategy chips
    │   └── ai_track_strategy_table.html # NEW: per-strategy leaderboard partial
    └── lab_ai_track.html                # MODIFY: include new partial

tests/
├── unit/
│   ├── test_strategies_loader.py        # NEW
│   ├── test_strategies_router.py        # NEW
│   └── test_evaluation_scoring.py       # EXTEND: add strategy filter unit tests
│                                        # (~5 new tests: filter applied,
│                                        # None default preserves Phase 2 behavior,
│                                        # json_extract correctness, etc.)
├── integration/
│   └── test_stock_analyze_with_strategy.py  # NEW: full two-stage flow
└── web/
    ├── test_stock_strategy_badge.py     # NEW
    └── test_lab_strategy_filter.py      # NEW
```

No DB migration — `EvaluationEvent.payload` is already JSON; just write `strategy` as a new key.

## YAML Schema

Each strategy YAML file MUST have these top-level fields:

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | str | ✅ | Snake-case identifier. MUST match filename stem. MUST be `^[a-z][a-z0-9_]*$`. Used as `payload.strategy` value. |
| `display_name` | str | ✅ | Chinese human-readable name shown in /lab UI |
| `version` | str | ✅ | Strategy version (e.g. `v1`). Bumping invalidates that strategy's cache. Format: `^v\d+$` |
| `description` | str | ✅ | One-line summary, used in router LLM context |
| `applies_when` | str | ✅ | Natural-language hint to router: when is this strategy appropriate? |
| `expected_horizons` | list[int] | ✅ | Subset of `[1, 5, 20, 60]`. Which horizons this strategy is designed for. **Read-only UI hint** — surfaced as a small "rated for: 5d / 20d" label next to the strategy in /lab leaderboard. Does NOT mutate or filter scoring queries. |
| `instructions` | str | ✅ | The Chinese markdown prompt body passed as part of the deep-analysis system message. Must end with the verdict taxonomy explanation. |

**Loader validation:**
- All 7 fields present → else fail fast at app startup
- `name` matches filename → else fail
- `expected_horizons` is non-empty subset of `[1, 5, 20, 60]` → else fail
- `version` matches `^v\d+$` → else fail
- `name` is globally unique across the library → else fail

**No optional fields in v0.** All-or-nothing keeps the schema discoverable.

## Strategy Library — Initial Content

Brief sketches; full prompts written during implementation.

### 1. `fundamental_value`
**applies_when:** 大盘股、稳定行业(消费/医疗/公用事业)、PE 显著低于历史中位、现金流稳定、技术面无突出特征
**expected_horizons:** [20, 60]
**focus:** PE/PB/EV-EBITDA、自由现金流、股息率、行业地位、Moat

### 2. `momentum_breakout`
**applies_when:** 上升趋势中、近 5-10 日新高、量能配合、不适用震荡或深跌反弹
**expected_horizons:** [5, 20]
**focus:** 突破质量(量比、假突破识别)、MA 排列、MACD/RSI、止损位

### 3. `news_event`
**applies_when:** 近 3 日有重大新闻/公告/事件触发显著价格波动
**expected_horizons:** [1, 5]
**focus:** 事件性质(M&A/产品/监管)、市场吸收速度、过度反应或不足反应

### 4. `sector_rotation`
**applies_when:** 行业出现显著相对强弱变化、宏观利率/通胀因子变化、风格切换信号
**expected_horizons:** [20, 60]
**focus:** 行业 RS(相对 SPY)、子行业领涨/落后、风格因子(growth vs value)

### 5. `oversold_reversal`
**applies_when:** 价格连续下跌后出现技术超卖信号(RSI<30、布林下轨外)、基本面无重大恶化
**expected_horizons:** [5, 20]
**focus:** 超卖深度、反弹动能、止跌信号(锤子线/吞没)、风险:接飞刀

### 6. `general` (fallback)
**applies_when:** 路由器不确定 / 数据不足 / 不适配任何具体策略
**expected_horizons:** [5, 20]
**focus:** 基本面 + 技术面 + 风险三段式综合分析(等同于现 Phase 2 的 prompt)

## Router Design

### Router prompt template

```
你是分析策略路由器。根据下面这只股票的当前状态,
从可选策略中选 1 个最合适的来做深度分析。

【可选策略】
- fundamental_value: 大盘稳定、估值合理时的价值分析
- momentum_breakout: 趋势突破时的动量分析
- news_event: 近期重大事件驱动的分析
- sector_rotation: 行业风格切换时的相对强弱分析
- oversold_reversal: 超卖后反弹的判定分析
- general: 不符合上述场景时的通用分析(兜底)

【股票快照】
ticker: AAPL
price: $180.42 (+1.2%)
market_cap: $2.8T
sector: Technology / Consumer Electronics
60d trend: MA20 向上, 价格 > MA50, 距 60d 高 -2%
volume_ratio_20d: 1.15 (今日量 / 20 日均量)
rsi_14: 62
sector_rs_20d_vs_spy: +3.2% (该股 20d 涨幅 - SPY 20d 涨幅)
recent news count (7d): 2

输出 JSON,严格遵守 schema:
ROUTER_JSON: {"strategy": "<name>", "reason": "<一句话依据>"}
```

### Router output parsing

Symmetric to Phase 2 `_parse_analyze_output`:
- `rfind("ROUTER_JSON:")` to tolerate router quoting itself
- Validate `strategy` in the loaded library
- If JSON parse fails OR strategy not in library → fall back to `general`
- Log a warning on fallback (telemetry)

### Router model

- Default: `claude-haiku-4-6` (or whichever cheapest available)
- Config knob: `settings.ai_model_router` (env var `AI_MODEL_ROUTER`)
- Cost: ~$0.0005 per call, ~$0 with cache

### Router cache

- Key: `(ticker, today_us_eastern_iso)` — strategy decision valid for one US trading day
- Storage: in-memory dict on `AiService` instance (cleared on process restart)
- Rationale: same-day re-clicks shouldn't re-invoke router; over-day caching could go stale as price/trend shifts
- **TZ choice: US/Eastern** — matches US market trading day. Date rolls over at midnight ET (typically when market is closed).
- **Multi-worker note:** in a Gunicorn / multi-worker deployment, each worker process has its own in-memory cache. Same ticker on the same day might hit the router N times across N workers. Acceptable trade-off — router cost is ~$0.0005/call, even 10x redundancy is negligible. SQLite-backed router cache deferred to Phase 4 if cost becomes meaningful.

## Phase 2 Evaluation Integration

### `EvaluationEvent.payload` schema change

Three explicit fields — clean, indexable, no string parsing:

```python
{
  "source": "stock_analysis",
  "strategy": "momentum_breakout",      # NEW — present when source == "stock_analysis"
  "strategy_version": "v1",             # NEW — from strategy YAML's `version:` field
  "prompt_version": "analysis-v4",      # CHANGED — now base-only, no strategy suffix
  "rationale": "...",
  "model": "claude-sonnet-4-6",
}
```

For recap-sourced events (`source == "recap"`), both `strategy` and `strategy_version` are absent.

### Prompt versioning

Two independent version fields:

| Field | Bumps when | Effect |
|---|---|---|
| `prompt_version` (`analysis-v4`) | base system prompt OR verdict schema changes | Invalidates ALL strategy caches |
| `strategy_version` (per-YAML `version:`) | that strategy's `instructions` text edited | Invalidates ONLY that strategy's cache |

Examples of bumps:
- Adding a new field to VERDICTS_JSON → bump `analysis-v4 → analysis-v5`
- Tightening `momentum_breakout` prompt to require explicit volume confirmation → bump that YAML's `version: v1 → v2` only

### Cache key

The cache key uses all three fields together:

```python
cache_key = (ticker, strategy, strategy_version, prompt_version)
```

This means a v1→v2 strategy edit leaves old v1 cached rows orphaned (they expire naturally on 24h TTL). Acceptable storage cost.

### scoring.py — 4 functions extended

All 4 functions in `marketpulse/evaluation/scoring.py` gain an optional `strategy: str | None = None` parameter:

```python
def compute_hit_rate(
    db: Session,
    *,
    event_type: str = "ai_analysis",
    subtype: str | None = None,
    ticker: str | None = None,
    horizon: int = 5,
    source: str | None = None,
    strategy: str | None = None,        # NEW
    since: date | None = None,
) -> HitRateStats: ...
```

Filter implementation parallel to existing `source` filter (SQLite `json_extract` on payload). Default `None` = no filter = backward compatible.

### `/lab/ai-track` UI changes

1. **Filter card — two-level Source → Strategy:**
   - "Source" chip group at top: `全部` | `stock_analysis` | `recap`
   - "Strategy" chip group below: `全部` | one chip per strategy (6 strategies)
   - **The Strategy group is visually disabled (gray + click-blocked) when Source ≠ `stock_analysis`**, because recap events have no strategy field. A tooltip explains: "策略筛选仅适用于股票分析事件"
   - When user picks `source=recap`, strategy filter auto-resets to `全部` and locks
2. **KPI strip** — add a 5th card "Best Strategy" (highest hit_rate strategy with n >= 5, similar to existing Best Ticker)
3. **NEW partial:** `ai_track_strategy_table.html` — leaderboard of strategies by hit_rate. Each row shows: strategy name, hit_rate, n_total, plus a small "rated for: 5d / 20d" gray label (from `expected_horizons`, **read-only hint**, does NOT mutate the horizon filter). Inserted in the rail next to the ticker table.
4. **Query string preservation** — `_qs_from_filters` extended to include `strategy` param. When `source != "stock_analysis"`, the strategy param is dropped from the URL automatically.

### `/stock/{ticker}` card UI changes

Phase 2 already added `mp-ai-badge` (hit-rate good/neutral/bad/pending) in the AI card head. Phase 3 adds a strategy indicator that does NOT compete with that badge:

- **Layout:** strategy name appears as a small chip in the `mp-card__sub` line BELOW the existing card title, NOT next to the hit-rate badge. Example:
  ```
  <header class="mp-card__head">
    <span class="mp-card__title">AI 分析 [auto_awesome icon]</span>
    <a class="mp-ai-badge mp-ai-badge--good" ...>70% (7/10)</a>   ← Phase 2 badge, unchanged
  </header>
  <div class="mp-card__sub">
    <span class="mp-chip mp-chip--strategy">动量突破</span>        ← Phase 3 NEW: strategy display_name
    <span class="muted">策略 · 由 router 自动选择</span>
  </div>
  ```
- The strategy chip is purely informational (no link / no interaction in v0). Phase 4 could make it a link to `/lab/ai-track?strategy=momentum_breakout`.

## Cache

### Deep analysis cache (existing infra)

- Key extended from `(ticker, prompt_version)` to `(ticker, strategy, strategy_version, prompt_version)`
- The `AiAnalysis` SQLAlchemy model gets two new columns (`strategy`, `strategy_version`) OR these fields are stored only in `payload` JSON with cache lookup via composite WHERE clause. **Decision deferred to plan** — column-based is cleaner if migration cost acceptable; JSON-only is faster to ship.
- TTL: 24h (unchanged)
- Cache HIT does NOT record a new event (Phase 2 invariant preserved)

### Router decision cache (new)

- In-memory dict keyed by `(ticker, today_date_iso)`, value = strategy name
- TTL: until midnight UTC OR process restart, whichever first
- Misses: ~ one Haiku call per ticker per day
- No DB persistence — cheap to rebuild

## Backward Compatibility

| Concern | Handling |
|---|---|
| Existing `EvaluationEvent` rows from Phase 2 have no `strategy` field | `scoring.py` default `strategy=None` = no filter → those rows continue to count toward "all strategies" |
| Existing `analysis-v3-zh-verdict` cache rows | Will naturally expire within 24h after deploy; new traffic uses `analysis-v4` + strategy fields |
| Old cached responses with no strategy info | Re-analysis after TTL automatically uses new flow |
| `/lab/ai-track?strategy=X` URL hits before any data exists for X | Returns empty placeholder (same as ticker-with-no-data path) |
| Recap commentary verdicts (`source == "recap"`) | No strategy field — they ONLY appear when Source filter is `全部` or `recap`. When Source is `stock_analysis`, recap events are filtered out (correct semantic — they are a different population, not "no-strategy stock events"). |

## Edge Cases

| Case | Handling |
|---|---|
| Router picks invalid name | Fallback to `general`, log warning `router_invalid_strategy` |
| Router JSON unparseable | Fallback to `general`, log warning `router_json_parse_failed` |
| Router LLM call fails (network, timeout) | Fallback to `general`, log warning `router_llm_failed` (no fail-the-request) |
| All 7 YAMLs fail to load at startup | App fails to boot (fail-fast). Strategies are required infra. |
| Single YAML invalid at startup | App fails to boot. No partial library. |
| User clicks "AI 分析" twice in 10 seconds before first response | Standard request-level dedup via existing logic (not strategy-specific) |
| Same ticker, two browser tabs, router runs in both | Both write to router cache; same result; OK |
| Cache hit for `strategy=A` but router would now pick `B` | Cache hit wins for 24h. Stale strategy is acceptable for now (price action over 24h rarely justifies re-routing). Phase 4 could revisit. |
| Manual override `/stock/AAPL?strategy=momentum_breakout` (force) | OUT OF SCOPE for v0. Could add as Phase 4 feature. |

## Open Decisions (locked for v0)

These are decisions made by the brainstorming session. Implementation should NOT revisit without spec amendment.

1. **Router model: Haiku** — cheapest viable. If router accuracy is poor in practice, swap via env var `AI_MODEL_ROUTER=claude-sonnet-...`
2. **General fallback: YES** — `general.yaml` exists as 6th strategy
3. **`risk_off` → renamed to `oversold_reversal`** — micro-level reversibility easier to evaluate than macro-risk-off
4. **`applies_when` natural language only** — no structured `requirements:` field in v0
5. **Strategy YAML committed in repo** — no web-UI editing
6. **Recap is NOT routed** — only `/stock` analyze. Recap commentary stays as-is.
7. **Three-field schema (not composite version string)** — `payload.strategy`, `payload.strategy_version`, `payload.prompt_version` are three explicit indexable fields. No string parsing for filters.
8. **Router cache: in-memory, per-day, US/Eastern TZ** — no DB persistence. Multi-worker fan-out (N redundant calls) accepted.
9. **`earnings_setup` deferred to Phase 3.5** — depends on earnings-calendar data source which MarketPulse does not yet have (yfinance `earnings_dates` is flaky, no other source wired up). v0 ships **6 strategies** instead of 7. `earnings_setup.yaml` is a Phase 3.5 candidate once earnings-calendar data lands.
10. **`expected_horizons` is UI hint only** — read-only label in /lab leaderboard. Does NOT filter scoring queries or mutate filter UI state.
11. **Source → Strategy filter is two-level** — Strategy chip group disabled when Source ≠ `stock_analysis`. Recap events do not get tagged as "no-strategy stock events" because they are a different population.

## Telemetry / Observability

To know whether the strategy system is earning its keep, the implementation MUST emit these counters / logs:

| Signal | Where | Purpose |
|---|---|---|
| `router.pick.<strategy>` counter | structlog `log.info("router_picked", strategy=...)` in `AiService.analyze()` | Track distribution. If `general` > 50% of picks over a rolling week, the router isn't differentiating — flag for prompt tuning. |
| `router.fallback.<reason>` counter | `log.warning("router_fallback", reason="json_parse_failed" \| "invalid_name" \| "llm_failed")` | If non-trivial (>5%), router prompt or model is brittle. |
| `router.cache.hit` / `router.cache.miss` counter | After cache lookup | Validate cache effectiveness. Expected hit rate >70% during US trading hours. |
| `analyze.cache.hit_with_strategy` / `analyze.cache.miss_with_strategy` counter | After deep-analysis cache lookup, keyed by strategy | Strategy-level cache stats (some strategies might have skewed hit rates). |

No new dashboard required — these counters flow through existing structlog → file → docker logs. A future "ops dashboard" could surface them, but spec doesn't require it.

## Self-Review Notes

(Following the brainstorming skill's spec self-review checklist.)

**Placeholder scan:** None found. Every section has concrete values.

**Internal consistency:**
- File structure (§ File Structure) lists 6 YAMLs; strategy library section (§ Strategy Library) defines 6; router prompt (§ Router Design) lists 6. ✓
- 3-field schema (`strategy` / `strategy_version` / `prompt_version`) defined once (§ Phase 2 Integration) and referenced in cache, backward compat, edge cases, open decisions. ✓
- `EvaluationEvent.payload.strategy` field appears in: integration, scoring, /lab, edge cases. Consistently `payload.strategy`. ✓
- Router context fields (§ Architecture and § Router Design) are identical 8 fields. ✓

**Scope check:** v0 is bounded — 6 strategies (earnings_setup deferred), single-strategy-per-analyze, no UI editing, no multi-strategy parallel. Plan can be written from this. ✓

**Ambiguity check:**
- Router context data shape (§ Router Design) is specified down to field names. ✓
- Cache TTL behaviors (24h deep, daily router US/Eastern) explicit. ✓
- `expected_horizons` is read-only UI hint, not enforcement — clarified. ✓
- Three-field versioning schema unambiguous (no string parsing). ✓
- Two-level Source → Strategy filter behavior fully spelled out (§ /lab UI changes + § Edge Cases). ✓

**Two ambiguities left for plan to resolve** (intentional — call them out):
1. **`base_system` exact wording** — `system = base_system + strategy.instructions`. Plan should define `base_system` explicitly, likely a shortened version of current `_ANALYSIS_SYSTEM` with the VERDICTS_JSON taxonomy retained.
2. **AiAnalysis cache: new columns vs JSON-only lookup** — § Cache lists both options. Plan should pick one and migrate accordingly. Recommended: new SQLAlchemy columns + small Alembic migration (clean, indexable, ~10 line PR).

## Implementation Pointers

- `marketpulse/ai/service.py:AiService.analyze()` — entry point to modify
- `marketpulse/ai/prompts.py` — `ANALYSIS_PROMPT_VERSION` bumps to `analysis-v4`
- `marketpulse/evaluation/scoring.py` — 4 functions get `strategy` param (parallel to existing `source` param)
- `marketpulse/web/routes/lab.py:lab_ai_track` — accepts `strategy` query param, threads through `_qs_from_filters`
- `marketpulse/web/templates/partials/ai_track_filter_card.html` — new chip group following the existing source/verdict pattern
- Phase 2 `_parse_analyze_output` is REUSED unchanged for stage 2 verdict extraction
- Single commit transaction boundary preserved: router cache is in-memory (no DB write); deep analysis writes AiAnalysis + EvaluationEvent in one commit as today
