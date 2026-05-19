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
   ├── Build router context:
   │     - quote (price, change_pct)
   │     - 60d trend summary (MA20/50 direction, recent high/low position)
   │     - sector / industry
   │     - news-count (last 7d)
   │     - days_to_earnings (if known, else null)
   ├── Cheap LLM call (Haiku) with router prompt
   │     "Choose ONE strategy from the list; output {strategy, reason} JSON"
   ├── Router decision cached per (ticker, today_date) — same day re-clicks skip the LLM call
   └── Returns strategy name (one of 7)
       ↓
[Stage 2: Deep Analysis]
   ├── Load strategies/definitions/<strategy>.yaml
   ├── Build prompt:
   │     system = base_system + strategy.instructions
   │     user   = data snapshot (same shape as today)
   ├── LLM call (Sonnet/Opus — same model_analyze as today)
   └── Parse verdict (same VERDICTS_JSON parser as Phase 2) + record event with
       payload.strategy = "<strategy_name>"
       ↓
Cache key: (ticker, strategy, prompt_version), 24h TTL
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
│       ├── earnings_setup.yaml
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
│   └── test_strategies_router.py        # NEW
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
| `expected_horizons` | list[int] | ✅ | Subset of `[1, 5, 20, 60]`. Which horizons this strategy is designed for. Used by /lab to default the horizon filter when viewing this strategy. |
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

### 3. `earnings_setup`
**applies_when:** 距财报 < 30 天 OR 财报后 < 5 天
**expected_horizons:** [1, 5]
**focus:** EPS/Revenue 预期、历史 surprise pattern、IV crush 风险、guidance 重要性

### 4. `news_event`
**applies_when:** 近 3 日有重大新闻/公告/事件触发显著价格波动
**expected_horizons:** [1, 5]
**focus:** 事件性质(M&A/产品/监管)、市场吸收速度、过度反应或不足反应

### 5. `sector_rotation`
**applies_when:** 行业出现显著相对强弱变化、宏观利率/通胀因子变化、风格切换信号
**expected_horizons:** [20, 60]
**focus:** 行业 RS(相对 SPY)、子行业领涨/落后、风格因子(growth vs value)

### 6. `oversold_reversal`
**applies_when:** 价格连续下跌后出现技术超卖信号(RSI<30、布林下轨外)、基本面无重大恶化
**expected_horizons:** [5, 20]
**focus:** 超卖深度、反弹动能、止跌信号(锤子线/吞没)、风险:接飞刀

### 7. `general` (fallback)
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
- earnings_setup: 距财报 30 天内的定位分析
- news_event: 近期重大事件驱动的分析
- sector_rotation: 行业风格切换时的相对强弱分析
- oversold_reversal: 超卖后反弹的判定分析
- general: 不符合上述场景时的通用分析(兜底)

【股票快照】
ticker: AAPL
price: $180.42 (+1.2%)
trend: 60 日 MA20 向上, 价格 > MA50
sector: Technology / Consumer Electronics
near-term high/low: 距 60d 高 -2%
recent news count (7d): 2
days to next earnings: 35

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

- Key: `(ticker, today_date)` — strategy decision valid for one trading day
- Storage: in-memory dict on `AiService` instance (cleared on process restart)
- Rationale: same-day re-clicks shouldn't re-invoke router; over-day caching could go stale as price/trend shifts

## Phase 2 Evaluation Integration

### `EvaluationEvent.payload` schema change

```python
{
  "source": "stock_analysis",
  "strategy": "momentum_breakout",    # NEW — present when source == "stock_analysis"
  "rationale": "...",
  "prompt_version": "analysis-v4.momentum_breakout-v1",  # composite
  "model": "claude-sonnet-4-6",
}
```

For recap-sourced events (`source == "recap"`), `strategy` is absent (recap doesn't get routing).

### Composite prompt version

`analysis-v<global>.<strategy_name>-v<strategy_version>`

Examples:
- `analysis-v4.momentum_breakout-v1`
- `analysis-v4.general-v1`

This means:
- Bumping the v4 → v5 (e.g. changing the base system prompt or verdict schema) invalidates ALL strategy caches
- Bumping a single strategy's `version` (e.g. `momentum_breakout-v1 → v2`) invalidates ONLY that strategy's cache

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

1. **Filter card** — add a new chip group "Strategy" with 7 buttons + "全部" (None)
2. **KPI strip** — add a 5th card "Best Strategy" (highest hit_rate strategy with n >= 5, similar to existing Best Ticker)
3. **NEW partial:** `ai_track_strategy_table.html` — leaderboard of strategies by hit_rate, similar to existing `ai_track_ticker_table.html`. Inserted in the rail next to the ticker table.
4. **Query string preservation** — `_qs_from_filters` extended to include `strategy` param

## Cache

### Deep analysis cache (existing infra)

- Key: `(ticker, prompt_version)` becomes effectively `(ticker, strategy, base_version)` since `prompt_version` is composite
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
| Existing `analysis-v3-zh-verdict` cache rows | Will naturally expire within 24h after deploy; new traffic uses v4 composite version |
| Old cached responses with no strategy info | Re-analysis after TTL automatically uses new flow |
| `/lab/ai-track?strategy=X` URL hits before any data exists for X | Returns empty placeholder (same as ticker-with-no-data path) |
| Recap commentary verdicts (`source == "recap"`) | No strategy field — they show under "全部" in filter, or "(无策略)" if user explicitly filters by strategy-is-null |

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
2. **General fallback: YES** — `general.yaml` exists as 7th strategy
3. **`risk_off` → renamed to `oversold_reversal`** — micro-level reversibility easier to evaluate than macro-risk-off
4. **`applies_when` natural language only** — no structured `requirements:` field in v0
5. **Strategy YAML committed in repo** — no web-UI editing
6. **Recap is NOT routed** — only `/stock` analyze. Recap commentary stays as-is.
7. **Composite version format** — `analysis-v<G>.<strategy>-v<S>`
8. **Router cache: in-memory, per-day** — no DB persistence

## Self-Review Notes

(Following the brainstorming skill's spec self-review checklist.)

**Placeholder scan:** None found. Every section has concrete values.

**Internal consistency:**
- File structure (§ File Structure) lists 7 YAMLs; strategy library section (§ Strategy Library) defines 7; router prompt (§ Router Design) lists 7. ✓
- Composite version format defined once (§ Phase 2 Integration) and referenced in cache and backward compat. ✓
- `EvaluationEvent.payload.strategy` field appears in: integration, scoring, /lab, edge cases. Consistently `payload.strategy`. ✓

**Scope check:** v0 is bounded — 7 strategies, single-strategy-per-analyze, no UI editing, no multi-strategy parallel. Plan can be written from this. ✓

**Ambiguity check:**
- "Router context" data shape (§ Router Design) is specified down to field names. ✓
- Cache TTL behaviors (24h deep, midnight router) explicit. ✓
- `expected_horizons` purpose (UI default, not enforcement) clarified. ✓
- Composite version format unambiguous (regex provided implicitly via examples).

**One open ambiguity** (call it out for plan to resolve):
- The exact wording for the **base_system** portion of the deep-analysis prompt (the part NOT in strategy `instructions`). The spec says `system = base_system + strategy.instructions`, but doesn't fully spell out `base_system`. Plan should define it explicitly — likely a shortened version of current `_ANALYSIS_SYSTEM` with the verdict taxonomy retained.

## Implementation Pointers

- `marketpulse/ai/service.py:AiService.analyze()` — entry point to modify
- `marketpulse/ai/prompts.py` — `ANALYSIS_PROMPT_VERSION` bumps to `analysis-v4`
- `marketpulse/evaluation/scoring.py` — 4 functions get `strategy` param (parallel to existing `source` param)
- `marketpulse/web/routes/lab.py:lab_ai_track` — accepts `strategy` query param, threads through `_qs_from_filters`
- `marketpulse/web/templates/partials/ai_track_filter_card.html` — new chip group following the existing source/verdict pattern
- Phase 2 `_parse_analyze_output` is REUSED unchanged for stage 2 verdict extraction
- Single commit transaction boundary preserved: router cache is in-memory (no DB write); deep analysis writes AiAnalysis + EvaluationEvent in one commit as today
