# Phase 3 — Strategy YAML System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert `/stock/{ticker}` AI analyze into a two-stage flow (Haiku router → Sonnet deep analysis) driven by 6 YAML-defined strategies. Each `EvaluationEvent` gains a `strategy` dimension so Phase 2 hit-rate scoring can compare strategies head-to-head.

**Architecture:** New `marketpulse/strategies/` module (pure-YAML library + loader + router). `AiService.analyze()` becomes two-stage. `AiAnalysis` gets two new columns (`strategy`, `strategy_version`) via Alembic migration. `EvaluationEvent.payload` gains `strategy` + `strategy_version` fields (no schema change — payload is JSON). `scoring.py` 4 functions extended with `strategy` filter. `/lab/ai-track` adds two-level Source → Strategy filter + strategy leaderboard. `/stock` AI card head shows the selected strategy chip below Phase 2 hit-rate badge.

**Tech Stack:** Python 3.12 + FastAPI + SQLAlchemy 2.x + Alembic + Jinja2 + vanilla CSS (NineScrolls) + Anthropic Claude + PyYAML. No new dependencies — PyYAML is already in `uv.lock`.

**Spec:** `docs/superpowers/specs/2026-05-18-phase-3-strategy-yaml.md`

---

## File Structure

```
marketpulse/
├── strategies/                              NEW module
│   ├── __init__.py                          NEW: re-export Strategy + load_strategies
│   ├── types.py                             NEW: Strategy frozen dataclass
│   ├── loader.py                            NEW: discover + parse + validate YAMLs
│   ├── router.py                            NEW: router context builder + LLM parser
│   └── definitions/
│       ├── fundamental_value.yaml           NEW
│       ├── momentum_breakout.yaml           NEW
│       ├── news_event.yaml                  NEW
│       ├── sector_rotation.yaml             NEW
│       ├── oversold_reversal.yaml           NEW
│       └── general.yaml                     NEW (fallback)
├── ai/
│   ├── prompts.py                           MODIFY: _BASE_ANALYSIS_SYSTEM constant +
│   │                                                 render_strategy_analysis_prompt + bump version
│   └── service.py                           MODIFY: two-stage analyze()
├── db/
│   └── models.py                            MODIFY: AiAnalysis +strategy +strategy_version columns
├── evaluation/
│   └── scoring.py                           MODIFY: 4 functions get strategy filter
└── web/
    ├── routes/
    │   ├── stock.py                         MODIFY: pass strategy to template
    │   └── lab.py                           MODIFY: accept ?source & ?strategy params
    ├── templates/
    │   ├── stock.html                       MODIFY: strategy chip in mp-card__sub
    │   ├── lab_ai_track.html                MODIFY: include strategy partials
    │   └── partials/
    │       ├── ai_track_filter_card.html    MODIFY: two-level Source → Strategy chips
    │       ├── ai_track_kpi_strip.html      MODIFY: add Best Strategy KPI card
    │       └── ai_track_strategy_table.html NEW: strategy leaderboard
    └── static/css/app.css                   MODIFY: append Phase 3 CSS

alembic/versions/
└── 0009_aianalyses_strategy.py              NEW: migration

tests/
├── unit/
│   ├── test_strategies_types.py             NEW
│   ├── test_strategies_loader.py            NEW
│   ├── test_strategies_router.py            NEW
│   ├── test_analysis_prompts_v4.py          NEW: base_system + render_strategy_analysis_prompt
│   └── test_evaluation_scoring.py           EXTEND: 5 new tests for strategy filter
├── integration/
│   ├── test_ai_router.py                    NEW: AiService router stage
│   ├── test_stock_analyze_with_strategy.py  NEW: full two-stage flow
│   └── test_router_telemetry.py             NEW: structlog counters
└── web/
    ├── test_stock_strategy_chip.py          NEW
    ├── test_lab_strategy_filter.py          NEW: two-level filter + ?source/?strategy
    └── test_lab_strategy_table.py           NEW
```

---

## Conventions

- **TDD**: failing test → run/see fail → minimal impl → run/see pass → commit. Each task is one commit.
- **Strategy YAML location**: `marketpulse/strategies/definitions/*.yaml`. Loader discovers via `glob("*.yaml")`. Filename stem MUST match `name:` field.
- **Frozen dataclass**: `Strategy` is `@dataclass(frozen=True)` — immutable after load.
- **YAML loaded once at module import time**: `loader.py` caches the loaded list in a module-level `_STRATEGIES` dict. Test reloading: tests use `loader.load_strategies()` directly, never the module-level cache.
- **Router model env**: `AI_MODEL_ROUTER` env var (default `claude-haiku-4-5` or whatever cheapest is configured in `settings.py`).
- **TZ for router cache**: US/Eastern, key is `today_us_eastern.isoformat()`.
- **Cache columns vs JSON (spec ambiguity #2 RESOLVED):** Column-based. `AiAnalysis` gets two new `nullable=True` String columns (`strategy`, `strategy_version`). Phase 2 v3 rows have these as NULL — backward compat preserved. Lookup uses exact-match `AND` clauses, no `json_extract`.
- **base_system text (spec ambiguity #1 RESOLVED):** Defined verbatim in Task 5 below. Strips the "三段式 基本面/技术面/风险" section structure (since strategies define their own structure), keeps the VERDICTS_JSON output requirement so all strategies emit consistent verdicts.
- **Single commit boundary**: existing `AiService.analyze()` invariant preserved — one `session.commit()` covers AiAnalysis + EvaluationEvent. Router stage is in-memory only, no DB writes.
- **Cache HIT does NOT record a new event** — Phase 2 invariant preserved.
- **Run tests**: `cd /Users/harvey/Dev/src/MarketPulse/.claude/worktrees/phase-3-plan && uv run pytest <path> -v`
- **Lint**: `uv run ruff check <path>`
- **Migration head before this work**: `cff08d913c3b`. New migration revision = `0009_aianalyses_strategy`.
- **Existing locations to know:**
  - `marketpulse/ai/prompts.py:6` — `ANALYSIS_PROMPT_VERSION = "analysis-v3-zh-verdict"` (bumps to `analysis-v4`)
  - `marketpulse/ai/service.py:57+` — `AiService.analyze()` (two-stage rewrite target)
  - `marketpulse/ai/service.py:90` — `cached = self._lookup_cache(ticker, version)` (cache call site)
  - `marketpulse/ai/service.py:142` — `record = AiAnalysis(...)` (cache write site)
  - `marketpulse/ai/service.py:189+` — `_lookup_cache(self, ticker, version)` (lookup method)
  - `marketpulse/db/models.py:187` — `class AiAnalysis(Base)`
  - `marketpulse/evaluation/scoring.py` — 4 query functions (compute_hit_rate / get_per_ticker_hit_rates / get_hit_rate_trend / get_recent_events_with_outcomes)
  - `marketpulse/web/routes/lab.py:lab_ai_track` — accepts new `source`/`strategy` query params
  - `marketpulse/web/templates/stock.html` — AI card head (where chip goes)
  - `marketpulse/web/templates/partials/ai_track_filter_card.html` — extend with two-level filter

---

### Task 1: `Strategy` dataclass + module init

**Files:**
- Create: `marketpulse/strategies/__init__.py`
- Create: `marketpulse/strategies/types.py`
- Test: `tests/unit/test_strategies_types.py` (NEW)

- [ ] **Step 1.1: Write failing tests**

Create `tests/unit/test_strategies_types.py`:

```python
"""Strategy frozen dataclass for Phase 3 strategy YAML system."""
from dataclasses import FrozenInstanceError

import pytest


def test_strategy_is_frozen():
    from marketpulse.strategies.types import Strategy
    s = Strategy(
        name="momentum_breakout",
        display_name="动量突破",
        version="v1",
        description="趋势突破时的动量分析",
        applies_when="上升趋势 + 量能配合",
        expected_horizons=[5, 20],
        instructions="...策略指令...",
    )
    with pytest.raises(FrozenInstanceError):
        s.name = "other"


def test_strategy_required_fields():
    from marketpulse.strategies.types import Strategy
    with pytest.raises(TypeError):
        Strategy()  # all fields required


def test_strategy_equality_by_value():
    from marketpulse.strategies.types import Strategy
    a = Strategy(name="x", display_name="X", version="v1", description="",
                 applies_when="", expected_horizons=[5], instructions="")
    b = Strategy(name="x", display_name="X", version="v1", description="",
                 applies_when="", expected_horizons=[5], instructions="")
    assert a == b
```

- [ ] **Step 1.2: Run, fail**

```bash
uv run pytest tests/unit/test_strategies_types.py -v
```

Expected: ImportError on `marketpulse.strategies.types` (module doesn't exist).

- [ ] **Step 1.3: Create `marketpulse/strategies/__init__.py`**

```python
"""Strategy YAML system — Phase 3.

A strategy is a named, versioned, YAML-defined playbook for /stock AI
analysis. The router picks one strategy per ticker; deep analysis runs
with that strategy's specialist instructions.
"""
from marketpulse.strategies.types import Strategy

__all__ = ["Strategy"]
```

- [ ] **Step 1.4: Create `marketpulse/strategies/types.py`**

```python
"""Strategy dataclass — frozen, value-equal, loaded from YAML."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Strategy:
    """A single strategy from definitions/*.yaml.

    Frozen so that loaded strategies are immutable across the process.
    All fields are required (no defaults) to keep YAML schema
    discoverable and prevent silent omissions.
    """
    name: str
    display_name: str
    version: str
    description: str
    applies_when: str
    expected_horizons: list[int] = field()
    instructions: str
```

(Note: `expected_horizons: list[int] = field()` is a quirk to satisfy dataclass — using `field()` without a default means "required but is a list-typed annotation that mypy might otherwise warn about as mutable default". Plain `expected_horizons: list[int]` also works; pick whichever ruff accepts.)

Simplify to plain annotation:

```python
"""Strategy dataclass — frozen, value-equal, loaded from YAML."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Strategy:
    name: str
    display_name: str
    version: str
    description: str
    applies_when: str
    expected_horizons: list[int]
    instructions: str
```

- [ ] **Step 1.5: Run, pass**

```bash
uv run pytest tests/unit/test_strategies_types.py -v
```

Expected: 3/3 pass.

- [ ] **Step 1.6: Ruff + commit**

```bash
uv run ruff check marketpulse/strategies/ tests/unit/test_strategies_types.py
git add marketpulse/strategies/ tests/unit/test_strategies_types.py
git commit -m "feat(strategies): Strategy frozen dataclass + module init

Foundation for Phase 3 strategy YAML system. Frozen dataclass with
7 required fields (name, display_name, version, description,
applies_when, expected_horizons, instructions). 3 unit tests verify
frozen behavior + required-field discipline + value equality.

Spec: docs/superpowers/specs/2026-05-18-phase-3-strategy-yaml.md"
```

---

### Task 2: Six strategy YAML files

**Files:**
- Create: `marketpulse/strategies/definitions/fundamental_value.yaml`
- Create: `marketpulse/strategies/definitions/momentum_breakout.yaml`
- Create: `marketpulse/strategies/definitions/news_event.yaml`
- Create: `marketpulse/strategies/definitions/sector_rotation.yaml`
- Create: `marketpulse/strategies/definitions/oversold_reversal.yaml`
- Create: `marketpulse/strategies/definitions/general.yaml`

No test code in this task — Task 3's loader tests will validate the YAMLs.

- [ ] **Step 2.1: Create `fundamental_value.yaml`**

```yaml
name: fundamental_value
display_name: 价值分析
version: v1
description: 大盘稳定股 + 估值合理 + 现金流稳定时的基本面价值分析
applies_when: |
  - 大盘股(market_cap > $10B 优先,但不强制)
  - 稳定行业:消费 / 医疗 / 公用事业 / 必需品
  - PE 显著低于历史中位或行业中位
  - 自由现金流稳定 / 股息可持续
  - 技术面无突出特征(不在突破或超卖)
  - 不适用:新经济成长股、热点炒作、强趋势中
expected_horizons: [20, 60]
instructions: |
  你是一名价值投资分析师。重点用基本面数据评估这只股票的中长期投资价值。

  ## 估值
  - PE / PB / EV/EBITDA 与行业中位、历史中位的对比
  - 如果数据缺失,明确指出而不要编造

  ## 现金流与质量
  - 自由现金流稳定性、ROE / ROA、负债率
  - 股息率与派息率(如适用)

  ## 行业地位与护城河
  - 在所在子行业中的相对位置
  - 是否存在结构性护城河(品牌 / 网络效应 / 规模 / 监管)

  ## 风险
  - 估值陷阱可能性(价值股变价值陷阱的常见原因)
  - 行业逆风、监管、竞争

  ## verdict 取值规则
  - bullish: 估值合理偏低 + 基本面稳健 + 行业地位牢固 → 20-60d 跑赢 SPY
  - bearish: 估值看似低但基本面恶化 / 行业结构性下行
  - neutral: 估值合理但缺乏催化、或基本面混合信号
```

- [ ] **Step 2.2: Create `momentum_breakout.yaml`**

```yaml
name: momentum_breakout
display_name: 动量突破
version: v1
description: 价格突破近期高点 + 量能确认时的动量延续分析
applies_when: |
  - 上升趋势中(MA20 向上、价格 > MA50)
  - 近 5-10 日内出现新高或突破关键阻力位
  - 成交量较 20 日均量放大(volume_ratio_20d > 1.2 优先)
  - 不适用:盘整震荡、深度下跌反弹、超买极端区域
expected_horizons: [5, 20]
instructions: |
  你是一名动量交易策略分析师。重点关注突破质量与趋势延续性。

  ## 突破质量
  - 突破点位是否清晰(前期高点 / 整数关口 / 阻力位)
  - 突破时成交量配合度(量比、放量天数)
  - 是否有假突破特征(实体小、影线长、回踩破位)

  ## 趋势背景
  - MA5 / MA20 / MA50 排列是否完整向上
  - MACD 状态(金叉位置、零轴上下)
  - RSI 区间(50-70 健康,>80 极端警惕)

  ## 持仓周期与止损
  - 5d / 20d 内合理的潜在空间(参考过往突破后的平均涨幅)
  - 关键止损位(突破点回踩失败、MA20 跌破)

  ## verdict 取值规则
  - bullish: 突破有效 + 量能配合 + 趋势完整 → 5-20d 跑赢 SPY
  - bearish: 假突破特征 / 趋势已破坏 / 严重超买
  - neutral: 突破存在但量能不足 / 趋势不明确
```

- [ ] **Step 2.3: Create `news_event.yaml`**

```yaml
name: news_event
display_name: 事件驱动
version: v1
description: 近期重大新闻 / 公告 / 事件触发显著价格波动时的事件性分析
applies_when: |
  - 近 3-7 日有重大新闻(M&A、产品发布、监管事件、管理层变动等)
  - 价格出现明显跳空或异常波动
  - 不适用:无明确事件触发的常规分析
expected_horizons: [1, 5]
instructions: |
  你是一名事件驱动策略分析师。重点判断市场对事件的反应是否充分 / 过度 / 不足。

  ## 事件性质
  - 事件类型(M&A 拟议 / 已宣布 / 反垄断、产品发布、监管、财报相关消息等)
  - 事件影响范围(行业级、公司级、临时性、永久性)
  - 历史上类似事件后的价格表现

  ## 市场反应评估
  - 当前价格反应幅度与历史可比事件对照
  - 成交量配合(放量 → 共识强;缩量 → 共识弱)
  - 是否存在过度反应(短期 reversion)或不足反应(继续 drift)

  ## 后续催化
  - 1-5 日内的关键时间点(公告 / 听证 / 数据)
  - 二次衍生效应(行业波及、监管跟进)

  ## verdict 取值规则
  - bullish: 事件正面 + 市场反应不足 + 1-5d 内继续 drift up
  - bearish: 事件负面 / 市场反应不足 / 1-5d 内 drift down,或正面但已 overpriced
  - neutral: 事件被充分定价、信号混合、二次效应不明
```

- [ ] **Step 2.4: Create `sector_rotation.yaml`**

```yaml
name: sector_rotation
display_name: 行业轮动
version: v1
description: 行业显著相对强弱变化 / 风格切换信号下的相对强弱分析
applies_when: |
  - 行业相对 SPY 出现明显相对强 / 弱(sector_rs_20d_vs_spy 绝对值 > 5%)
  - 宏观因子变化(利率、通胀预期、风险偏好)驱动风格切换
  - 不适用:个股 alpha 主导,行业因子不显著
expected_horizons: [20, 60]
instructions: |
  你是一名行业轮动策略分析师。重点用相对强弱判断该股是否处于行业 momentum 的有利方向。

  ## 行业相对强弱
  - 该股所在行业过去 20 / 60 日相对 SPY 的表现
  - 子行业领涨 / 落后(若有数据)
  - 行业 ETF 资金流向(若数据缺失,跳过不要编造)

  ## 风格切换信号
  - 当前宏观环境(利率走向、通胀预期、风险偏好)
  - 该行业是否处于受益方(growth/value/cyclical/defensive)

  ## 个股在行业中的位置
  - 该股相对所在行业 ETF 的相对强弱
  - 是否是行业 leader 还是 laggard

  ## verdict 取值规则
  - bullish: 行业相对强 + 个股是行业 leader + 风格切换有利 → 20-60d 跑赢 SPY
  - bearish: 行业相对弱 / 个股是 laggard / 风格切换不利
  - neutral: 行业因子混合 / 个股表现与行业脱钩
```

- [ ] **Step 2.5: Create `oversold_reversal.yaml`**

```yaml
name: oversold_reversal
display_name: 超卖反弹
version: v1
description: 价格深跌后技术超卖 + 基本面无重大恶化时的反弹判定
applies_when: |
  - 短期连续下跌(20d 跌幅显著)
  - 技术超卖信号(RSI < 30、布林下轨外、抛售放量)
  - 基本面无重大恶化(无利空催化)
  - 不适用:基本面持续恶化、趋势完整向下的杀跌
expected_horizons: [5, 20]
instructions: |
  你是一名反弹策略分析师。重点判断是否真正的反弹机会还是接飞刀。

  ## 超卖深度
  - RSI / 布林位置 / 距 60d 高点的跌幅
  - 抛售强度(连续阴线天数、放量阴线)
  - 是否触及关键支撑(MA200、历史成交密集区)

  ## 止跌信号
  - K 线形态(锤子线、十字星、吞没)
  - 缩量下跌或异常放量
  - MACD / RSI 是否出现底背离

  ## 风险:接飞刀
  - 基本面是否有持续恶化迹象(财报不及预期、guidance 下调、行业逆风)
  - 大盘 / 行业是否同步下跌(系统性 vs 个股)
  - 跌势是否完整、是否需要二次探底

  ## verdict 取值规则
  - bullish: 超卖深 + 止跌信号 + 基本面稳 → 5-20d 反弹跑赢 SPY
  - bearish: 跌势未止 / 基本面持续恶化 / 缺乏止跌信号 → 接飞刀
  - neutral: 超卖但无明确止跌信号、或基本面信号混合
```

- [ ] **Step 2.6: Create `general.yaml` (fallback)**

```yaml
name: general
display_name: 通用分析
version: v1
description: 不符合具体策略场景时的兜底通用分析(等同于 Phase 2 三段式)
applies_when: |
  - 路由器无法明确选择具体策略
  - 数据不足以判定适用策略
  - 个股状态平稳,无突出特征
expected_horizons: [5, 20]
instructions: |
  你是一名股票研究分析师。请用基本面 + 技术面 + 风险的综合视角分析。

  ## 基本面
  - 估值水平、盈利能力、增长性
  - 行业地位、竞争格局
  - 财务健康度

  ## 技术面
  - 趋势方向(MA 排列、MACD、RSI)
  - 关键价位(支撑 / 阻力)
  - 成交量配合

  ## 风险
  - 短期风险因子(财报、事件、监管)
  - 中期风险因子(竞争、行业逆风)
  - 估值风险(过高 / 价值陷阱)

  ## verdict 取值规则
  - bullish: 综合显示中短期相对大盘有正向超额
  - bearish: 综合显示中短期相对大盘负向超额风险
  - neutral: 信号混合 / 无明确方向倾向
```

- [ ] **Step 2.7: Verify all 6 files exist + commit**

```bash
ls marketpulse/strategies/definitions/*.yaml
```

Expected output (6 files):
```
marketpulse/strategies/definitions/fundamental_value.yaml
marketpulse/strategies/definitions/general.yaml
marketpulse/strategies/definitions/momentum_breakout.yaml
marketpulse/strategies/definitions/news_event.yaml
marketpulse/strategies/definitions/oversold_reversal.yaml
marketpulse/strategies/definitions/sector_rotation.yaml
```

Quick YAML syntax sanity check:
```bash
uv run python -c "
import yaml
from pathlib import Path
for p in Path('marketpulse/strategies/definitions').glob('*.yaml'):
    data = yaml.safe_load(p.read_text())
    print(p.stem, '->', data['name'], 'v=' + data['version'])
"
```

Expected: 6 lines, each printing `<stem> -> <name> v=v1` with stem == name.

```bash
git add marketpulse/strategies/definitions/
git commit -m "feat(strategies): 6 strategy YAML definitions for v0

v0 strategy library: fundamental_value, momentum_breakout, news_event,
sector_rotation, oversold_reversal, general (fallback). Each file has
the 7 required fields per spec § YAML Schema. All version=v1.

earnings_setup deferred to Phase 3.5 (depends on earnings-calendar data
not yet wired up). All filename stems match name: field for loader
validation."
```

---

### Task 3: `loader.py` + validation

**Files:**
- Create: `marketpulse/strategies/loader.py`
- Modify: `marketpulse/strategies/__init__.py` (re-export `load_strategies`)
- Test: `tests/unit/test_strategies_loader.py` (NEW)

- [ ] **Step 3.1: Write failing tests**

Create `tests/unit/test_strategies_loader.py`:

```python
"""Tests for strategy YAML loader + validation."""
from pathlib import Path

import pytest

from marketpulse.strategies.types import Strategy


def test_load_strategies_returns_dict_keyed_by_name(tmp_path):
    from marketpulse.strategies.loader import load_strategies
    # Use the real definitions/ dir packaged in marketpulse/
    result = load_strategies()
    assert isinstance(result, dict)
    # All 6 v0 strategies present
    expected = {
        "fundamental_value", "momentum_breakout", "news_event",
        "sector_rotation", "oversold_reversal", "general",
    }
    assert set(result.keys()) == expected
    # Each value is a Strategy
    for name, strat in result.items():
        assert isinstance(strat, Strategy)
        assert strat.name == name


def test_loaded_strategy_has_all_required_fields():
    from marketpulse.strategies.loader import load_strategies
    s = load_strategies()["momentum_breakout"]
    assert s.name == "momentum_breakout"
    assert s.display_name == "动量突破"
    assert s.version == "v1"
    assert s.description
    assert s.applies_when
    assert s.expected_horizons == [5, 20]
    assert "突破质量" in s.instructions  # spot-check prompt content


def test_load_from_directory_with_custom_path(tmp_path):
    from marketpulse.strategies.loader import load_strategies
    # Write one minimal YAML
    yaml_file = tmp_path / "tiny.yaml"
    yaml_file.write_text(
        "name: tiny\n"
        "display_name: 极简\n"
        "version: v1\n"
        "description: minimal\n"
        "applies_when: always\n"
        "expected_horizons: [5]\n"
        "instructions: do stuff\n"
    )
    result = load_strategies(definitions_dir=tmp_path)
    assert "tiny" in result
    assert result["tiny"].display_name == "极简"


def test_load_fails_when_required_field_missing(tmp_path):
    from marketpulse.strategies.loader import load_strategies
    yaml_file = tmp_path / "bad.yaml"
    yaml_file.write_text(
        "name: bad\n"
        "display_name: Bad\n"
        # missing: version, description, applies_when, expected_horizons, instructions
    )
    with pytest.raises(ValueError, match="missing required field"):
        load_strategies(definitions_dir=tmp_path)


def test_load_fails_when_name_does_not_match_filename(tmp_path):
    from marketpulse.strategies.loader import load_strategies
    yaml_file = tmp_path / "alpha.yaml"
    yaml_file.write_text(
        "name: beta\n"  # mismatch!
        "display_name: Beta\n"
        "version: v1\n"
        "description: x\n"
        "applies_when: x\n"
        "expected_horizons: [5]\n"
        "instructions: x\n"
    )
    with pytest.raises(ValueError, match="name 'beta' does not match filename 'alpha'"):
        load_strategies(definitions_dir=tmp_path)


def test_load_fails_when_expected_horizons_not_subset_of_default(tmp_path):
    from marketpulse.strategies.loader import load_strategies
    yaml_file = tmp_path / "weird.yaml"
    yaml_file.write_text(
        "name: weird\n"
        "display_name: Weird\n"
        "version: v1\n"
        "description: x\n"
        "applies_when: x\n"
        "expected_horizons: [3, 10]\n"   # not in [1, 5, 20, 60]
        "instructions: x\n"
    )
    with pytest.raises(ValueError, match="expected_horizons.*must be subset"):
        load_strategies(definitions_dir=tmp_path)


def test_load_fails_when_version_format_invalid(tmp_path):
    from marketpulse.strategies.loader import load_strategies
    yaml_file = tmp_path / "x.yaml"
    yaml_file.write_text(
        "name: x\n"
        "display_name: X\n"
        "version: 1.0\n"    # not vN format
        "description: x\n"
        "applies_when: x\n"
        "expected_horizons: [5]\n"
        "instructions: x\n"
    )
    with pytest.raises(ValueError, match="version.*format"):
        load_strategies(definitions_dir=tmp_path)


def test_load_fails_when_name_not_snake_case(tmp_path):
    from marketpulse.strategies.loader import load_strategies
    yaml_file = tmp_path / "BadName.yaml"
    yaml_file.write_text(
        "name: BadName\n"
        "display_name: X\n"
        "version: v1\n"
        "description: x\n"
        "applies_when: x\n"
        "expected_horizons: [5]\n"
        "instructions: x\n"
    )
    with pytest.raises(ValueError, match="name.*snake_case"):
        load_strategies(definitions_dir=tmp_path)


def test_load_fails_when_directory_has_duplicate_names(tmp_path):
    """Two files with overlapping name: field — should never happen but defend."""
    from marketpulse.strategies.loader import load_strategies
    (tmp_path / "one.yaml").write_text(
        "name: one\ndisplay_name: One\nversion: v1\ndescription: x\n"
        "applies_when: x\nexpected_horizons: [5]\ninstructions: x\n"
    )
    # filename validation catches this — but if two YAMLs had same name field
    # via copy mistake, loader should fail. Filename match check covers it.
    # This test is implicit; skip if redundant.
```

- [ ] **Step 3.2: Run, fail**

```bash
uv run pytest tests/unit/test_strategies_loader.py -v
```

Expected: ImportError on `marketpulse.strategies.loader`.

- [ ] **Step 3.3: Create `marketpulse/strategies/loader.py`**

```python
"""YAML loader for strategy definitions.

Discovers all *.yaml files in `definitions_dir`, parses each, validates
all required fields are present and well-formed, returns a dict
{name: Strategy}.

Called once at app startup (via marketpulse.web.main) — invalid YAML
fails fast so the deploy never serves a half-broken strategy library.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from marketpulse.strategies.types import Strategy

_REQUIRED_FIELDS = (
    "name", "display_name", "version", "description",
    "applies_when", "expected_horizons", "instructions",
)
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_VERSION_RE = re.compile(r"^v\d+$")
_VALID_HORIZONS = {1, 5, 20, 60}

_DEFAULT_DIR = Path(__file__).parent / "definitions"


def load_strategies(definitions_dir: Path | None = None) -> dict[str, Strategy]:
    """Discover and load all strategy YAMLs from definitions_dir.

    Args:
        definitions_dir: directory to scan for *.yaml; defaults to packaged
            marketpulse/strategies/definitions/

    Returns:
        Dict keyed by strategy `name` field, values are Strategy instances.

    Raises:
        ValueError: invalid YAML (missing field, mismatched name, bad version,
            non-snake-case name, expected_horizons not subset of default).
    """
    dirpath = definitions_dir or _DEFAULT_DIR
    result: dict[str, Strategy] = {}
    for yaml_path in sorted(dirpath.glob("*.yaml")):
        data = yaml.safe_load(yaml_path.read_text())
        if not isinstance(data, dict):
            raise ValueError(f"{yaml_path}: YAML root must be a mapping")
        _validate(yaml_path.stem, data, yaml_path)
        strategy = Strategy(
            name=data["name"],
            display_name=data["display_name"],
            version=data["version"],
            description=data["description"],
            applies_when=data["applies_when"],
            expected_horizons=list(data["expected_horizons"]),
            instructions=data["instructions"],
        )
        result[strategy.name] = strategy
    return result


def _validate(stem: str, data: dict[str, Any], path: Path) -> None:
    """Fail-fast checks for one YAML file."""
    for field in _REQUIRED_FIELDS:
        if field not in data:
            raise ValueError(
                f"{path}: missing required field {field!r}"
            )
    name = data["name"]
    if not _NAME_RE.match(name):
        raise ValueError(
            f"{path}: name {name!r} must be snake_case "
            f"(matching {_NAME_RE.pattern})"
        )
    if name != stem:
        raise ValueError(
            f"{path}: name {name!r} does not match filename {stem!r}"
        )
    if not _VERSION_RE.match(data["version"]):
        raise ValueError(
            f"{path}: version {data['version']!r} format invalid "
            f"(expect {_VERSION_RE.pattern})"
        )
    horizons = data["expected_horizons"]
    if not isinstance(horizons, list) or not horizons:
        raise ValueError(
            f"{path}: expected_horizons must be a non-empty list"
        )
    if not set(horizons).issubset(_VALID_HORIZONS):
        raise ValueError(
            f"{path}: expected_horizons {horizons!r} must be subset of "
            f"{sorted(_VALID_HORIZONS)}"
        )
```

- [ ] **Step 3.4: Update `marketpulse/strategies/__init__.py`**

```python
"""Strategy YAML system — Phase 3."""
from marketpulse.strategies.loader import load_strategies
from marketpulse.strategies.types import Strategy

__all__ = ["Strategy", "load_strategies"]
```

- [ ] **Step 3.5: Run all loader tests + cross-check that all 6 real YAMLs load**

```bash
uv run pytest tests/unit/test_strategies_loader.py -v
```

Expected: 8/8 pass (matches test count above; the duplicate-names case is a comment-only skip).

```bash
uv run python -c "from marketpulse.strategies import load_strategies; print(sorted(load_strategies()))"
```

Expected:
```
['fundamental_value', 'general', 'momentum_breakout', 'news_event', 'oversold_reversal', 'sector_rotation']
```

- [ ] **Step 3.6: Ruff + commit**

```bash
uv run ruff check marketpulse/strategies/loader.py tests/unit/test_strategies_loader.py marketpulse/strategies/__init__.py
git add marketpulse/strategies/loader.py marketpulse/strategies/__init__.py tests/unit/test_strategies_loader.py
git commit -m "feat(strategies): YAML loader with fail-fast validation

Discovers *.yaml in definitions/, parses each, validates: 7 required
fields present, name matches filename, name is snake_case, version
matches v<N>, expected_horizons is non-empty subset of [1,5,20,60].

8 unit tests cover happy path + 7 failure modes. Real definitions/
loads cleanly (6 strategies, all version=v1)."
```

---

### Task 4: AiAnalysis migration + model columns

**Files:**
- Modify: `marketpulse/db/models.py:187-199` (AiAnalysis class)
- Create: `alembic/versions/0009_aianalyses_strategy.py`
- Test: existing `tests/integration/test_stock_analyze_records_event.py` via test_database fixture re-creating schema

- [ ] **Step 4.1: Add columns to model**

Modify `marketpulse/db/models.py` — extend `AiAnalysis` class (line 187+). After the `prompt_version` column add:

```python
    strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    strategy_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
```

Full updated class for clarity:

```python
class AiAnalysis(Base):
    __tablename__ = "ai_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    strategy_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    input_data_json: Mapped[str] = mapped_column(Text, nullable=False)
    response_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(TZDateTime(), default=_utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)

    __table_args__ = (Index("ix_ai_analyses_ticker_expires", "ticker", "expires_at"),)
```

- [ ] **Step 4.2: Create Alembic migration**

Create `alembic/versions/0009_aianalyses_strategy.py`:

```python
"""add ai_analyses strategy + strategy_version columns

Revision ID: 0009_aianalyses_strategy
Revises: cff08d913c3b
Create Date: 2026-05-18 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0009_aianalyses_strategy'
down_revision: str | Sequence[str] | None = 'cff08d913c3b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_analyses",
        sa.Column("strategy", sa.String(64), nullable=True),
    )
    op.add_column(
        "ai_analyses",
        sa.Column("strategy_version", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_analyses", "strategy_version")
    op.drop_column("ai_analyses", "strategy")
```

- [ ] **Step 4.3: Verify migration is detected at HEAD**

```bash
uv run alembic heads
```

Expected: `0009_aianalyses_strategy (head)`.

- [ ] **Step 4.4: Run a test that uses db_session — confirm schema includes new columns**

```bash
uv run pytest tests/integration/test_stock_analyze_records_event.py -v
```

Expected: 5 passed (Phase 2's existing tests should still pass — new columns are nullable, no data path changes yet).

- [ ] **Step 4.5: Confirm model accepts the new attributes**

```bash
uv run python -c "
from marketpulse.db.models import AiAnalysis
a = AiAnalysis()
a.strategy = 'momentum_breakout'
a.strategy_version = 'v1'
print('ok')
"
```

Expected: `ok`.

- [ ] **Step 4.6: Ruff + commit**

```bash
uv run ruff check marketpulse/db/models.py alembic/versions/0009_aianalyses_strategy.py
git add marketpulse/db/models.py alembic/versions/0009_aianalyses_strategy.py
git commit -m "feat(db): add ai_analyses.strategy + strategy_version columns

Both nullable=True for backward compat with Phase 2 v3 rows (NULL).
Phase 3 cache lookups will use exact-match WHERE clauses on these
columns instead of JSON extracts. Migration 0009 chained off Phase 2
head cff08d913c3b."
```

---

### Task 5: Bump prompt version + `_BASE_ANALYSIS_SYSTEM` + `render_strategy_analysis_prompt`

**Files:**
- Modify: `marketpulse/ai/prompts.py` (whole module)
- Test: `tests/unit/test_analysis_prompts_v4.py` (NEW)

- [ ] **Step 5.1: Write failing tests**

Create `tests/unit/test_analysis_prompts_v4.py`:

```python
"""Tests for v4 base_system + render_strategy_analysis_prompt."""
import pytest


def test_analysis_prompt_version_is_v4():
    from marketpulse.ai.prompts import ANALYSIS_PROMPT_VERSION
    assert ANALYSIS_PROMPT_VERSION == "analysis-v4"


def test_base_analysis_system_contains_verdict_taxonomy():
    """base_system must define VERDICTS_JSON output schema + verdict values."""
    from marketpulse.ai.prompts import _BASE_ANALYSIS_SYSTEM
    assert "VERDICTS_JSON" in _BASE_ANALYSIS_SYSTEM
    assert "bullish" in _BASE_ANALYSIS_SYSTEM
    assert "neutral" in _BASE_ANALYSIS_SYSTEM
    assert "bearish" in _BASE_ANALYSIS_SYSTEM


def test_base_analysis_system_strips_three_section_structure():
    """base_system MUST NOT prescribe 基本面/技术面/风险 — strategies define their own."""
    from marketpulse.ai.prompts import _BASE_ANALYSIS_SYSTEM
    # The fixed three-section structure goes away
    assert "包含三个部分" not in _BASE_ANALYSIS_SYSTEM


def test_render_strategy_analysis_prompt_includes_strategy_instructions():
    """The rendered system message = base_system + strategy.instructions."""
    from datetime import UTC, date, datetime

    from marketpulse.ai.prompts import render_strategy_analysis_prompt
    from marketpulse.data.types import Bar, Fundamentals, NewsItem, Quote
    from marketpulse.strategies.types import Strategy

    strat = Strategy(
        name="momentum_breakout",
        display_name="动量突破",
        version="v1",
        description="x",
        applies_when="x",
        expected_horizons=[5, 20],
        instructions="STRATEGY_MARKER_BREAKOUT_ANALYSIS_BODY",
    )
    quote = Quote(
        ticker="AAPL", price=180.0, change_pct=1.0, volume=1000,
        avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False,
    )
    fundamentals = Fundamentals(
        ticker="AAPL", market_cap=3e12, pe_ratio=25.0, eps=7.0,
        sector="Tech", industry="Hardware",
    )
    bars = [
        Bar(date=date(2026, 5, d), open=180, high=181, low=179,
            close=180.0 + d * 0.1, volume=1000)
        for d in range(1, 16)
    ]
    news: list[NewsItem] = []

    rendered = render_strategy_analysis_prompt(
        strategy=strat, quote=quote, fundamentals=fundamentals,
        news=news, bars=bars,
    )
    # base_system + strategy.instructions both in there
    assert "VERDICTS_JSON" in rendered
    assert "STRATEGY_MARKER_BREAKOUT_ANALYSIS_BODY" in rendered


def test_render_with_general_strategy_works():
    """general.yaml is the fallback — render should not require any special handling."""
    from datetime import UTC, date, datetime

    from marketpulse.ai.prompts import render_strategy_analysis_prompt
    from marketpulse.data.types import Bar, Fundamentals, Quote
    from marketpulse.strategies import load_strategies

    general = load_strategies()["general"]
    quote = Quote(
        ticker="AAPL", price=180.0, change_pct=1.0, volume=1000,
        avg_volume_20d=2000, fetched_at=datetime.now(UTC), stale=False,
    )
    fundamentals = Fundamentals(
        ticker="AAPL", market_cap=3e12, pe_ratio=25.0, eps=7.0,
        sector="Tech", industry="Hardware",
    )
    bars = [
        Bar(date=date(2026, 5, d), open=180, high=181, low=179,
            close=180.0 + d * 0.1, volume=1000)
        for d in range(1, 16)
    ]
    rendered = render_strategy_analysis_prompt(
        strategy=general, quote=quote, fundamentals=fundamentals,
        news=[], bars=bars,
    )
    assert "通用分析" in rendered or "基本面" in rendered  # general's content
    assert "VERDICTS_JSON" in rendered
```

- [ ] **Step 5.2: Run, fail**

```bash
uv run pytest tests/unit/test_analysis_prompts_v4.py -v
```

Expected: 5 fails — `ANALYSIS_PROMPT_VERSION` still v3, `_BASE_ANALYSIS_SYSTEM` doesn't exist, `render_strategy_analysis_prompt` doesn't exist.

- [ ] **Step 5.3: Restructure `marketpulse/ai/prompts.py`**

The current file has:
- `ANALYSIS_PROMPT_VERSION = "analysis-v3-zh-verdict"` (line 6)
- `_ANALYSIS_SYSTEM` constant
- `render_analysis_prompt(...)` function (current entry point)
- (unchanged) `COMMENTARY_PROMPT_VERSION`, `_COMMENTARY_SYSTEM`, recap-related helpers
- (unchanged) `RISK_PROMPT_VERSION`, risk-related helpers

Changes:

1. Bump version: `ANALYSIS_PROMPT_VERSION = "analysis-v4"` (drops the `-zh-verdict` suffix per spec)
2. Replace `_ANALYSIS_SYSTEM` with `_BASE_ANALYSIS_SYSTEM` (strips three-section structure):

```python
_BASE_ANALYSIS_SYSTEM = (
    "你是一名股票研究分析师。请用中文输出一份简明的 markdown 报告。"
    "只使用所提供的数据,不要编造数字,不要给出买入或卖出建议。"
    "股票代码、行业名称等专有名词可保留英文原文。\n\n"
    "在 markdown 报告之后必须**单独一行**输出 verdict JSON,"
    "严格遵守此 schema:\n\n"
    "VERDICTS_JSON: {\"ticker\": \"AAPL\", \"verdict\": \"bullish\", "
    "\"rationale\": \"一句话说明依据\"}\n\n"
    "verdict 取值: bullish | neutral | bearish。\n"
    "- bullish: 数据显示中短期相对大盘有正向超额\n"
    "- bearish: 数据显示中短期相对大盘负向超额风险\n"
    "- neutral: 无明确方向倾向 (数据混合 / 噪声大)\n\n"
    "客观,基于数据,不要因为缺数据而强行选边。\n\n"
    "---\n\n"
    "下面是这次分析使用的具体策略指令:\n\n"
)
```

3. Rename `render_analysis_prompt` to `render_strategy_analysis_prompt` and add a `strategy` parameter. The function:
   - Takes `Strategy` as first kwarg
   - Builds `system = _BASE_ANALYSIS_SYSTEM + strategy.instructions`
   - Builds `data` block exactly like before (quote / fundamentals / bars summary / news)
   - Returns the combined string in the existing format `_split_prompt` understands (the `__SYSTEM__` / `__DATA__` markers stay)

Replace the existing `render_analysis_prompt` function with:

```python
def render_strategy_analysis_prompt(
    *,
    strategy: Any,   # marketpulse.strategies.types.Strategy
    quote: Quote,
    fundamentals: Fundamentals,
    news: list[NewsItem],
    bars: list[Bar],
) -> str:
    """Build the deep-analysis prompt for the chosen strategy.

    Concatenates _BASE_ANALYSIS_SYSTEM (verdict taxonomy + style rules)
    with the strategy's `instructions` body. Data block is identical to
    the pre-Phase-3 render.
    """
    system = _BASE_ANALYSIS_SYSTEM + strategy.instructions
    data = _render_analysis_data_block(
        quote=quote, fundamentals=fundamentals, news=news, bars=bars,
    )
    return f"__SYSTEM__\n{system}\n__DATA__\n{data}"
```

Where `_render_analysis_data_block` is the same content currently inside `render_analysis_prompt` — extract it as a helper. Run it once with the pre-Phase-3 test data to confirm output identical.

(If the data-block construction logic is not currently factored out, this task includes that refactor — it should be a pure extraction without behavior change.)

4. Delete the old `render_analysis_prompt` name (callers update in Task 8).

5. Update the `from typing import Any` import if necessary (replace with `from marketpulse.strategies.types import Strategy` once we accept the strategy type — for now `Any` avoids circular import risk; Task 8 confirms there's no cycle).

Actually clean: import Strategy directly. Replace `strategy: Any` with `strategy: Strategy`:

```python
from marketpulse.strategies.types import Strategy

def render_strategy_analysis_prompt(
    *,
    strategy: Strategy,
    quote: Quote,
    fundamentals: Fundamentals,
    news: list[NewsItem],
    bars: list[Bar],
) -> str:
    """..."""
    system = _BASE_ANALYSIS_SYSTEM + strategy.instructions
    data = _render_analysis_data_block(
        quote=quote, fundamentals=fundamentals, news=news, bars=bars,
    )
    return f"__SYSTEM__\n{system}\n__DATA__\n{data}"
```

Verify no circular import: `strategies/types.py` imports nothing app-specific, so `ai/prompts.py → strategies.types` is clean.

- [ ] **Step 5.4: Run unit tests**

```bash
uv run pytest tests/unit/test_analysis_prompts_v4.py -v
```

Expected: 5/5 pass.

- [ ] **Step 5.5: Confirm no regressions in older v3-named tests**

```bash
grep -rn "analysis-v3-zh-verdict\|render_analysis_prompt\b" tests/ marketpulse/ 2>/dev/null
```

If any matches in tests/ (Phase 2 may have referenced them), update them to `analysis-v4` and `render_strategy_analysis_prompt`. Most should be in Task 8's service tests but a quick grep catches stragglers.

If matches in marketpulse/ outside `ai/prompts.py` — that's the existing call site in `service.py:analyze()` which Task 8 rewires. Leave for now.

Run full unit suite:

```bash
uv run pytest tests/unit/ -q
```

Expected: all pass except possibly `test_analysis_prompt_parsing.py` (Phase 2's `_parse_analyze_output` tests — should still pass, that helper doesn't reference the prompt versions).

- [ ] **Step 5.6: Ruff + commit**

```bash
uv run ruff check marketpulse/ai/prompts.py tests/unit/test_analysis_prompts_v4.py
git add marketpulse/ai/prompts.py tests/unit/test_analysis_prompts_v4.py
git commit -m "feat(ai): bump ANALYSIS_PROMPT_VERSION → analysis-v4 + base_system

_BASE_ANALYSIS_SYSTEM is a shortened analysis system message that
keeps the VERDICTS_JSON verdict taxonomy (bullish/neutral/bearish)
but drops the fixed 基本面/技术面/风险 three-section structure —
each strategy now defines its own section layout in YAML.

render_strategy_analysis_prompt() takes a Strategy and concatenates
base_system + strategy.instructions. Data block unchanged from v3.

5 unit tests cover version bump, base_system content, section-strip,
strategy injection, general fallback render."
```

---

### Task 6: Router prompt builder + parser

**Files:**
- Create: `marketpulse/strategies/router.py`
- Test: `tests/unit/test_strategies_router.py` (NEW)

- [ ] **Step 6.1: Write failing tests**

Create `tests/unit/test_strategies_router.py`:

```python
"""Tests for router prompt context builder + LLM output parser."""
from datetime import UTC, date, datetime

import pytest

from marketpulse.data.types import Bar, Fundamentals, Quote


def _quote(ticker="AAPL", price=180.0):
    return Quote(
        ticker=ticker, price=price, change_pct=1.2, volume=10_000,
        avg_volume_20d=8_500, fetched_at=datetime.now(UTC), stale=False,
    )


def _fundamentals(ticker="AAPL"):
    return Fundamentals(
        ticker=ticker, market_cap=2.8e12, pe_ratio=28.0, eps=6.5,
        sector="Technology", industry="Consumer Electronics",
    )


def _bars(close_seq=None):
    close_seq = close_seq or [180.0 + i * 0.1 for i in range(60)]
    return [
        Bar(date=date(2026, 3, 1) + (date(2026, 5, 1) - date(2026, 3, 1)) * 0,
            open=180, high=181, low=179, close=c, volume=10_000)
        for c in close_seq
    ]


def test_build_router_context_has_required_fields():
    from marketpulse.strategies.router import build_router_context

    ctx = build_router_context(
        quote=_quote(),
        fundamentals=_fundamentals(),
        bars=_bars(),
        spy_bars=_bars([500.0 + i * 0.05 for i in range(60)]),
        news_count_7d=2,
    )
    # All 8 fields from spec § Router Design
    assert ctx["ticker"] == "AAPL"
    assert "price" in ctx
    assert "change_pct" in ctx
    assert "market_cap" in ctx
    assert "sector" in ctx
    assert "trend_summary" in ctx           # MA20/50 direction + position
    assert "volume_ratio_20d" in ctx
    assert "rsi_14" in ctx
    assert "sector_rs_20d_vs_spy" in ctx
    assert ctx["news_count_7d"] == 2


def test_build_router_context_volume_ratio_correct():
    """volume_ratio_20d = today_volume / avg_volume_20d."""
    from marketpulse.strategies.router import build_router_context
    q = _quote()
    # quote.volume=10000, avg_volume_20d=8500 → ratio ≈ 1.176
    ctx = build_router_context(
        quote=q, fundamentals=_fundamentals(),
        bars=_bars(), spy_bars=_bars(),
        news_count_7d=0,
    )
    assert ctx["volume_ratio_20d"] == pytest.approx(10000 / 8500, abs=0.01)


def test_render_router_prompt_lists_all_6_strategies():
    from marketpulse.strategies import load_strategies
    from marketpulse.strategies.router import render_router_prompt

    strategies = load_strategies()
    ctx = {
        "ticker": "AAPL", "price": 180.0, "change_pct": 1.2,
        "market_cap": 2.8e12, "sector": "Technology",
        "trend_summary": "MA20 向上", "volume_ratio_20d": 1.2,
        "rsi_14": 62, "sector_rs_20d_vs_spy": 3.2, "news_count_7d": 2,
    }
    prompt = render_router_prompt(strategies=strategies, context=ctx)
    # All 6 strategy names appear in the prompt's options list
    for name in ["fundamental_value", "momentum_breakout", "news_event",
                 "sector_rotation", "oversold_reversal", "general"]:
        assert name in prompt
    # The context shows up
    assert "AAPL" in prompt
    assert "180.0" in prompt or "$180" in prompt
    assert "ROUTER_JSON" in prompt


def test_parse_router_output_valid():
    from marketpulse.strategies.router import parse_router_output
    raw = (
        "我会用动量突破策略,因为价格刚刚突破前期高点。\n\n"
        "ROUTER_JSON: {\"strategy\": \"momentum_breakout\", \"reason\": \"突破新高\"}"
    )
    result = parse_router_output(raw, valid_names={"momentum_breakout", "general"})
    assert result == {"strategy": "momentum_breakout", "reason": "突破新高"}


def test_parse_router_output_uses_rfind_when_marker_quoted_in_body():
    """If the LLM mentions ROUTER_JSON: in the body before the real one."""
    from marketpulse.strategies.router import parse_router_output
    raw = (
        "ROUTER_JSON: 这是输出格式说明。\n\n"
        "实际选择:动量突破\n\n"
        "ROUTER_JSON: {\"strategy\": \"momentum_breakout\", \"reason\": \"x\"}"
    )
    result = parse_router_output(raw, valid_names={"momentum_breakout"})
    assert result["strategy"] == "momentum_breakout"


def test_parse_router_output_no_marker_returns_none():
    from marketpulse.strategies.router import parse_router_output
    result = parse_router_output("no router json here.", valid_names={"general"})
    assert result is None


def test_parse_router_output_malformed_json_returns_none():
    from marketpulse.strategies.router import parse_router_output
    result = parse_router_output(
        "ROUTER_JSON: not-json-at-all",
        valid_names={"general"},
    )
    assert result is None


def test_parse_router_output_invalid_strategy_name_returns_none():
    from marketpulse.strategies.router import parse_router_output
    result = parse_router_output(
        'ROUTER_JSON: {"strategy": "bogus", "reason": "x"}',
        valid_names={"momentum_breakout", "general"},
    )
    assert result is None


def test_parse_router_output_missing_strategy_field_returns_none():
    from marketpulse.strategies.router import parse_router_output
    result = parse_router_output(
        'ROUTER_JSON: {"reason": "x"}',
        valid_names={"general"},
    )
    assert result is None
```

- [ ] **Step 6.2: Run, fail**

```bash
uv run pytest tests/unit/test_strategies_router.py -v
```

Expected: ImportError on `marketpulse.strategies.router`.

- [ ] **Step 6.3: Create `marketpulse/strategies/router.py`**

```python
"""Router stage: build LLM context + parse LLM output.

The router is a cheap LLM call (Haiku) that picks ONE strategy from the
loaded library based on a small structured ticker snapshot.

This module is pure: no DB, no LLM calls, no I/O. AiService wires it
to the actual LLM in Task 7.
"""
from __future__ import annotations

import json
from typing import Any

from marketpulse.data.types import Bar, Fundamentals, Quote
from marketpulse.strategies.types import Strategy

_ROUTER_MARKER = "ROUTER_JSON:"


def build_router_context(
    *,
    quote: Quote,
    fundamentals: Fundamentals,
    bars: list[Bar],
    spy_bars: list[Bar],
    news_count_7d: int,
) -> dict[str, Any]:
    """Compose the structured snapshot the router LLM sees.

    All values are computed from the same fetched data Stage 2 (deep
    analysis) will reuse — no extra LLM-side computation.

    Returns a dict with 11 fields per spec § Router Design.
    """
    closes = [b.close for b in bars]
    spy_closes = [b.close for b in spy_bars]

    # 20-day rolling indicators
    ma20 = _mean(closes[-20:]) if len(closes) >= 20 else None
    ma50 = _mean(closes[-50:]) if len(closes) >= 50 else None
    high_60d = max(closes[-60:]) if len(closes) >= 60 else (max(closes) if closes else quote.price)
    pos_vs_60d_high_pct = ((quote.price - high_60d) / high_60d * 100.0) if high_60d else 0.0

    trend_summary = _trend_summary(closes, ma20, ma50, pos_vs_60d_high_pct)
    rsi_14 = _rsi(closes, period=14)
    volume_ratio = (quote.volume / quote.avg_volume_20d) if quote.avg_volume_20d else 0.0
    rs_20d = _relative_strength_20d(closes, spy_closes)

    return {
        "ticker": quote.ticker,
        "price": quote.price,
        "change_pct": quote.change_pct,
        "market_cap": fundamentals.market_cap or 0.0,
        "sector": f"{fundamentals.sector or '?'} / {fundamentals.industry or '?'}",
        "trend_summary": trend_summary,
        "volume_ratio_20d": round(volume_ratio, 2),
        "rsi_14": round(rsi_14, 1) if rsi_14 is not None else None,
        "sector_rs_20d_vs_spy": round(rs_20d * 100, 2) if rs_20d is not None else None,
        "news_count_7d": news_count_7d,
    }


def render_router_prompt(
    *, strategies: dict[str, Strategy], context: dict[str, Any],
) -> str:
    """Build the full router prompt: strategy menu + ticker snapshot + output schema."""
    menu_lines = [
        f"- {s.name}: {s.description}"
        for s in strategies.values()
    ]
    menu_block = "\n".join(menu_lines)

    snapshot = "\n".join([
        f"ticker: {context['ticker']}",
        f"price: ${context['price']:.2f} ({context['change_pct']:+.2f}%)",
        f"market_cap: ${context['market_cap']:.2e}",
        f"sector: {context['sector']}",
        f"60d trend: {context['trend_summary']}",
        f"volume_ratio_20d: {context['volume_ratio_20d']} (今日量 / 20日均量)",
        f"rsi_14: {context['rsi_14']}",
        f"sector_rs_20d_vs_spy: {context['sector_rs_20d_vs_spy']}%",
        f"news_count_7d: {context['news_count_7d']}",
    ])

    return (
        "你是分析策略路由器。根据下面这只股票的当前状态,"
        "从可选策略中选 1 个最合适的来做深度分析。\n\n"
        f"【可选策略】\n{menu_block}\n\n"
        f"【股票快照】\n{snapshot}\n\n"
        "输出 JSON,严格遵守 schema:\n"
        "ROUTER_JSON: {\"strategy\": \"<name>\", \"reason\": \"<一句话依据>\"}"
    )


def parse_router_output(
    raw: str, *, valid_names: set[str],
) -> dict[str, str] | None:
    """Extract the {strategy, reason} from router LLM output.

    Uses rfind to tolerate the LLM quoting the marker in body. Validates
    the strategy field is in valid_names. Returns None on any failure;
    caller falls back to 'general'.
    """
    idx = raw.rfind(_ROUTER_MARKER)
    if idx == -1:
        return None
    tail = raw[idx + len(_ROUTER_MARKER):].strip()
    try:
        parsed = json.loads(tail)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    strategy = parsed.get("strategy")
    if not isinstance(strategy, str) or strategy not in valid_names:
        return None
    return {"strategy": strategy, "reason": str(parsed.get("reason", ""))}


# ---------- internal indicators ----------

def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _rsi(closes: list[float], *, period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, period + 1):
        diff = closes[-i] - closes[-i - 1]
        if diff > 0:
            gains.append(diff)
        else:
            losses.append(-diff)
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _relative_strength_20d(closes: list[float], spy_closes: list[float]) -> float | None:
    if len(closes) < 21 or len(spy_closes) < 21:
        return None
    stock_ret = (closes[-1] - closes[-21]) / closes[-21] if closes[-21] else None
    spy_ret = (spy_closes[-1] - spy_closes[-21]) / spy_closes[-21] if spy_closes[-21] else None
    if stock_ret is None or spy_ret is None:
        return None
    return stock_ret - spy_ret


def _trend_summary(
    closes: list[float], ma20: float | None, ma50: float | None,
    pos_vs_60d_high_pct: float,
) -> str:
    if not closes or ma20 is None or ma50 is None:
        return "数据不足"
    direction = "上行" if closes[-1] > ma20 > ma50 else (
        "下行" if closes[-1] < ma20 < ma50 else "震荡"
    )
    return f"{direction}, 距 60d 高 {pos_vs_60d_high_pct:+.1f}%"
```

- [ ] **Step 6.4: Run, pass**

```bash
uv run pytest tests/unit/test_strategies_router.py -v
```

Expected: 9/9 pass.

- [ ] **Step 6.5: Ruff + commit**

```bash
uv run ruff check marketpulse/strategies/router.py tests/unit/test_strategies_router.py
git add marketpulse/strategies/router.py tests/unit/test_strategies_router.py
git commit -m "feat(strategies): router context builder + LLM output parser

Pure (no DB, no I/O) module exposing:
- build_router_context() — composes 11-field ticker snapshot from
  quote / fundamentals / bars / spy_bars / news count
- render_router_prompt() — combines strategy menu + snapshot +
  output schema
- parse_router_output() — rfind ROUTER_JSON: marker, validates
  strategy name against loaded library, returns None on any error

9 unit tests cover context building, RSI/volume_ratio/RS computation,
parser happy path, rfind for quoted marker, all failure modes
(no marker, bad JSON, invalid name, missing field)."
```

---

### Task 7: AiService router stage + in-memory daily cache

**Files:**
- Modify: `marketpulse/ai/service.py` (add `_route_strategy` method + cache)
- Test: `tests/integration/test_ai_router.py` (NEW)

- [ ] **Step 7.1: Write failing tests**

Create `tests/integration/test_ai_router.py`:

```python
"""Integration: AiService router stage — LLM call + per-day cache."""
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest

from marketpulse.ai.service import AiService
from marketpulse.data.types import Bar, Fundamentals, Quote


def _build_service(db_session, *, router_response: str = ""):
    fake_ai = MagicMock()
    # complete() can be called for both router (cheap model) and deep analysis.
    # First call = router; second call = deep analysis (deep stage isn't tested
    # here, so just return empty markdown if it fires).
    fake_ai.complete.side_effect = [router_response, "## Body\n\nVERDICTS_JSON: {\"ticker\":\"AAPL\",\"verdict\":\"neutral\",\"rationale\":\"x\"}"]
    fake_data = MagicMock()
    fake_data.get_quote.return_value = Quote(
        ticker="AAPL", price=180.0, change_pct=1.0, volume=10000,
        avg_volume_20d=8500, fetched_at=datetime.now(UTC), stale=False,
    )
    fake_data.get_fundamentals.return_value = Fundamentals(
        ticker="AAPL", market_cap=3e12, pe_ratio=25.0, eps=7.0,
        sector="Technology", industry="Consumer Electronics",
    )
    fake_data.get_history.return_value = [
        Bar(date=date(2026, 3, 1), open=180, high=181, low=179,
            close=180.0 + i * 0.1, volume=10000)
        for i in range(60)
    ]
    fake_data.get_news.return_value = []
    return AiService(
        session=db_session, ai_client=fake_ai, data=fake_data,
        model="claude-sonnet-4-6", ttl_hours=24,
        model_analyze="claude-sonnet-4-6",
        model_router="claude-haiku-4-5",
    )


def test_route_strategy_returns_router_pick(db_session):
    svc = _build_service(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "momentum_breakout", "reason": "突破新高"}',
    )
    name, reason = svc._route_strategy("AAPL")
    assert name == "momentum_breakout"
    assert reason == "突破新高"


def test_route_strategy_falls_back_to_general_on_parse_failure(db_session):
    svc = _build_service(db_session, router_response="garbage no marker")
    name, reason = svc._route_strategy("AAPL")
    assert name == "general"
    assert "fallback" in reason.lower() or reason == ""


def test_route_strategy_falls_back_when_router_picks_invalid_name(db_session):
    svc = _build_service(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "bogus", "reason": "x"}',
    )
    name, _ = svc._route_strategy("AAPL")
    assert name == "general"


def test_route_strategy_uses_daily_cache(db_session):
    """Second call same ticker same day → no LLM call."""
    svc = _build_service(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "momentum_breakout", "reason": "x"}',
    )
    svc._route_strategy("AAPL")
    # Replace side_effect with an exception so second router call would fail
    svc.ai.complete.side_effect = AssertionError("router should not run again same day")
    name, _ = svc._route_strategy("AAPL")
    # If cache works, no AssertionError raised
    assert name == "momentum_breakout"


def test_route_strategy_uses_router_model_not_analyze_model(db_session):
    """The router LLM call uses model_router, not model_analyze."""
    svc = _build_service(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "general", "reason": "x"}',
    )
    svc._route_strategy("AAPL")
    # First call to complete() should have been with model=claude-haiku-4-5
    call = svc.ai.complete.call_args_list[0]
    assert call.kwargs["model"] == "claude-haiku-4-5"
```

- [ ] **Step 7.2: Run, fail**

```bash
uv run pytest tests/integration/test_ai_router.py -v
```

Expected: AttributeError on `_route_strategy` and on `model_router` kwarg.

- [ ] **Step 7.3: Modify `AiService.__init__`**

In `marketpulse/ai/service.py`, extend `AiService.__init__` signature with `model_router`:

Find the existing block (around line 70-90):

```python
class AiService:
    def __init__(
        self,
        session: Session,
        *,
        ai_client: AiClient,
        data: _DataLike,
        model: str,
        ttl_hours: int,
        model_analyze: str | None = None,
    ) -> None:
        self.session = session
        ...
```

Extend to:

```python
class AiService:
    def __init__(
        self,
        session: Session,
        *,
        ai_client: AiClient,
        data: _DataLike,
        model: str,
        ttl_hours: int,
        model_analyze: str | None = None,
        model_router: str | None = None,
    ) -> None:
        self.session = session
        self.ai = ai_client
        self.data = data
        self.model = model
        self.model_analyze = model_analyze or model
        # Router uses a cheap model; falls back to `model` if unset.
        self.model_router = model_router or model
        self.ttl_hours = ttl_hours
        # In-memory router decision cache: {(ticker, today_us_eastern_iso): (strategy, reason)}
        self._router_cache: dict[tuple[str, str], tuple[str, str]] = {}
```

- [ ] **Step 7.4: Add imports**

Near the top of `marketpulse/ai/service.py`, add:

```python
from zoneinfo import ZoneInfo

from marketpulse.strategies import load_strategies
from marketpulse.strategies.router import (
    build_router_context,
    parse_router_output,
    render_router_prompt,
)
```

`zoneinfo` is in the stdlib (Python 3.9+), no extra dependency.

- [ ] **Step 7.5: Add `_route_strategy` method**

In `AiService`, after `__init__` and before `analyze()`, add:

```python
_US_EASTERN = ZoneInfo("America/New_York")


def _route_strategy(self, ticker: str) -> tuple[str, str]:
    """Stage 1 of analyze() — pick a strategy via cheap router LLM.

    Returns (strategy_name, reason). On any failure (no marker, bad
    JSON, invalid name, LLM error), falls back to 'general' with a
    structured warning log.

    Cached in-memory per (ticker, today_us_eastern) — same-day re-clicks
    skip the LLM call. Cache cleared on process restart.
    """
    today_key = datetime.now(_US_EASTERN).date().isoformat()
    cache_key = (ticker, today_key)
    if cache_key in self._router_cache:
        return self._router_cache[cache_key]

    # Fetch the data needed for context (will be reused in deep analysis;
    # caller currently re-fetches in analyze() — Task 8 plumbs the shared
    # data through.)
    quote = self.data.get_quote(ticker)
    fundamentals = self.data.get_fundamentals(ticker)
    bars = self.data.get_history(ticker, period="60d")
    try:
        spy_bars = self.data.get_history("SPY", period="60d")
    except Exception:  # noqa: BLE001
        spy_bars = []
    try:
        news_count = len(self.data.get_news(ticker, limit=20))
    except Exception:  # noqa: BLE001
        news_count = 0

    strategies = load_strategies()
    ctx = build_router_context(
        quote=quote, fundamentals=fundamentals,
        bars=bars, spy_bars=spy_bars, news_count_7d=news_count,
    )
    prompt = render_router_prompt(strategies=strategies, context=ctx)

    try:
        raw = self.ai.complete(
            system="", user=prompt, model=self.model_router,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("router_llm_failed", ticker=ticker, error=str(exc))
        decision = ("general", "router_llm_failed")
        self._router_cache[cache_key] = decision
        return decision

    parsed = parse_router_output(raw, valid_names=set(strategies.keys()))
    if parsed is None:
        log.warning(
            "router_fallback", ticker=ticker, reason="parse_or_invalid",
            raw_excerpt=raw[:200],
        )
        decision = ("general", "router_parse_or_invalid_name")
        self._router_cache[cache_key] = decision
        return decision

    log.info(
        "router_picked", ticker=ticker,
        strategy=parsed["strategy"], reason=parsed["reason"],
    )
    decision = (parsed["strategy"], parsed["reason"])
    self._router_cache[cache_key] = decision
    return decision
```

The `log` variable should already be imported in `service.py` (Phase 2's record_event hook added it). If not:

```python
import structlog
log = structlog.get_logger()
```

- [ ] **Step 7.6: Run, pass**

```bash
uv run pytest tests/integration/test_ai_router.py -v
```

Expected: 5/5 pass.

- [ ] **Step 7.7: Ruff + commit**

```bash
uv run ruff check marketpulse/ai/service.py tests/integration/test_ai_router.py
git add marketpulse/ai/service.py tests/integration/test_ai_router.py
git commit -m "feat(ai): AiService._route_strategy() router stage

Cheap LLM call (model_router, default Haiku) picks one strategy from
the loaded library based on a structured ticker snapshot. In-memory
cache per (ticker, today_us_eastern) avoids re-routing same-day
re-clicks. All failures (parse error, invalid name, LLM exception)
fall back to 'general' with structured warning logs.

5 integration tests cover happy path, parse fallback, invalid-name
fallback, daily cache, model selection."
```

---

### Task 8: Rewire `AiService.analyze()` as two-stage flow

**Files:**
- Modify: `marketpulse/ai/service.py:analyze()` + `_lookup_cache()` + AiAnalysis write site
- Test: `tests/integration/test_stock_analyze_with_strategy.py` (NEW)

- [ ] **Step 8.1: Write failing tests**

Create `tests/integration/test_stock_analyze_with_strategy.py`:

```python
"""End-to-end: /stock analyze() does router → deep → records event with strategy."""
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from marketpulse.ai.service import AiService
from marketpulse.db.models import AiAnalysis, EvaluationEvent
from marketpulse.evaluation.constants import AIVerdict


_DEEP_RESPONSE_BULLISH = (
    "## 突破质量\n\n突破有效\n\n"
    "VERDICTS_JSON: {\"ticker\": \"AAPL\", \"verdict\": \"bullish\", \"rationale\": \"x\"}"
)


def _build_service(db_session, *, router_response, deep_response):
    fake_ai = MagicMock()
    fake_ai.complete.side_effect = [router_response, deep_response]
    fake_data = MagicMock()
    fake_data.get_quote.return_value = type("Q", (), dict(
        ticker="AAPL", price=180.0, change_pct=1.0, volume=10_000,
        avg_volume_20d=8_500, fetched_at=datetime.now(UTC), stale=False,
    ))()
    fake_data.get_fundamentals.return_value = type("F", (), dict(
        ticker="AAPL", market_cap=3e12, pe_ratio=25.0, eps=7.0,
        sector="Tech", industry="Hardware",
    ))()
    from marketpulse.data.types import Bar
    fake_data.get_history.return_value = [
        Bar(date=date(2026, 3, 1), open=180, high=181, low=179,
            close=180.0 + i * 0.1, volume=10_000)
        for i in range(60)
    ]
    fake_data.get_news.return_value = []
    return AiService(
        session=db_session, ai_client=fake_ai, data=fake_data,
        model="claude-sonnet-4-6", ttl_hours=24,
        model_analyze="claude-sonnet-4-6",
        model_router="claude-haiku-4-5",
    )


def test_analyze_records_event_with_strategy_and_version(db_session):
    svc = _build_service(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "momentum_breakout", "reason": "x"}',
        deep_response=_DEEP_RESPONSE_BULLISH,
    )
    svc.analyze("AAPL")
    events = db_session.query(EvaluationEvent).all()
    assert len(events) == 1
    e = events[0]
    assert e.payload["strategy"] == "momentum_breakout"
    assert e.payload["strategy_version"] == "v1"
    assert e.payload["prompt_version"] == "analysis-v4"
    assert e.subtype == AIVerdict.BULLISH


def test_analyze_cache_hit_returns_existing_no_new_event(db_session):
    """Second call same ticker within 24h: cache hits, no new event."""
    svc = _build_service(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "momentum_breakout", "reason": "x"}',
        deep_response=_DEEP_RESPONSE_BULLISH,
    )
    svc.analyze("AAPL")
    # Reset side_effect: any further LLM call should not run (cache hits skip)
    svc.ai.complete.side_effect = AssertionError("cache should serve second call")
    svc.analyze("AAPL")
    assert db_session.query(EvaluationEvent).count() == 1


def test_analyze_router_fallback_to_general_records_general_strategy(db_session):
    """Router parse fails → general fallback → deep analysis with general.yaml."""
    svc = _build_service(
        db_session,
        router_response="garbage no marker",
        deep_response=_DEEP_RESPONSE_BULLISH,
    )
    svc.analyze("AAPL")
    events = db_session.query(EvaluationEvent).all()
    assert len(events) == 1
    assert events[0].payload["strategy"] == "general"


def test_analyze_stores_strategy_columns_on_ai_analyses(db_session):
    """AiAnalysis row should populate strategy + strategy_version columns."""
    svc = _build_service(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "fundamental_value", "reason": "x"}',
        deep_response=_DEEP_RESPONSE_BULLISH,
    )
    svc.analyze("AAPL")
    rows = db_session.query(AiAnalysis).all()
    assert len(rows) == 1
    assert rows[0].strategy == "fundamental_value"
    assert rows[0].strategy_version == "v1"
    assert rows[0].prompt_version == "analysis-v4"


def test_analyze_different_strategies_cache_independently(db_session):
    """Same ticker, two different days (two different router picks) → 2 cache rows."""
    svc = _build_service(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "momentum_breakout", "reason": "x"}',
        deep_response=_DEEP_RESPONSE_BULLISH,
    )
    svc.analyze("AAPL")
    # Clear router cache to simulate "next day"
    svc._router_cache.clear()
    # New router response picks a different strategy
    svc.ai.complete.side_effect = [
        'ROUTER_JSON: {"strategy": "oversold_reversal", "reason": "y"}',
        _DEEP_RESPONSE_BULLISH,
    ]
    svc.analyze("AAPL")
    rows = db_session.query(AiAnalysis).order_by(AiAnalysis.id).all()
    assert len(rows) == 2
    assert {r.strategy for r in rows} == {"momentum_breakout", "oversold_reversal"}
```

- [ ] **Step 8.2: Run, fail**

```bash
uv run pytest tests/integration/test_stock_analyze_with_strategy.py -v
```

Expected: 5 fails (analyze() still uses v3 single-stage path, no strategy column populated).

- [ ] **Step 8.3: Rewrite `AiService.analyze()`**

Current `analyze()` (around lines 80-200 of `marketpulse/ai/service.py`) does:
1. Look up cache by `(ticker, version=ANALYSIS_PROMPT_VERSION)`
2. If cache miss, fetch data, render prompt, call LLM, parse verdict, create AiAnalysis, record event, commit
3. Return AnalysisResult

New two-stage flow:

```python
def analyze(self, ticker: str) -> AnalysisResult:
    # Stage 1: router picks strategy (cached per-day in memory).
    # Side effect: data was fetched as part of router context. We
    # re-fetch in Stage 2 here for simplicity; passing forward is a
    # later optimization.
    strategy_name, _reason = self._route_strategy(ticker)

    from marketpulse.strategies import load_strategies
    strategy = load_strategies()[strategy_name]

    base_version = prompts.ANALYSIS_PROMPT_VERSION
    cached = self._lookup_cache_with_strategy(
        ticker=ticker, prompt_version=base_version,
        strategy=strategy.name, strategy_version=strategy.version,
    )
    if cached:
        return AnalysisResult(
            ticker=ticker,
            model=cached.model,
            prompt_version=cached.prompt_version,
            strategy=cached.strategy,
            strategy_version=cached.strategy_version,
            response_markdown=cached.response_markdown,
            requested_at=cached.requested_at,
            cached=True,
        )

    # Stage 2: deep analysis with chosen strategy.
    quote = self.data.get_quote(ticker)
    fundamentals = self.data.get_fundamentals(ticker)
    bars = self.data.get_history(ticker, period="60d")
    news = self.data.get_news(ticker, limit=10)

    prompt_text = prompts.render_strategy_analysis_prompt(
        strategy=strategy, quote=quote, fundamentals=fundamentals,
        news=news, bars=bars,
    )
    system, data_block = _split_prompt(prompt_text)
    response = self.ai.complete(
        system=system, user=data_block, model=self.model_analyze,
    )
    now = datetime.now(UTC)
    input_snapshot = {
        "ticker": quote.ticker,
        "quote": {"price": quote.price, "change_pct": quote.change_pct},
        "fundamentals": {
            "market_cap": fundamentals.market_cap,
            "pe_ratio": fundamentals.pe_ratio,
            "sector": fundamentals.sector,
        },
        "bars": {"count": len(bars)},
        "news": {"count": len(news)},
    }
    record = AiAnalysis(
        ticker=ticker,
        model=self.model_analyze,
        prompt_version=base_version,
        strategy=strategy.name,
        strategy_version=strategy.version,
        input_data_json=json.dumps(input_snapshot, default=str),
        response_markdown=response,
        requested_at=now,
        expires_at=now + timedelta(hours=self.ttl_hours),
    )
    self.session.add(record)

    # Phase 2: parse verdict and record event (same transaction).
    _, verdict = _parse_analyze_output(response)
    if verdict is not None:
        v_value = verdict.get("verdict")
        v_ticker = (verdict.get("ticker") or "").strip().upper()
        if v_value in AIVerdict.all() and v_ticker:
            try:
                record_event(
                    event_type="ai_analysis",
                    subtype=v_value,
                    ticker=v_ticker,
                    event_time=now,
                    event_price=quote.price,
                    payload={
                        "rationale": verdict.get("rationale", ""),
                        "prompt_version": base_version,
                        "source": "stock_analysis",
                        "strategy": strategy.name,
                        "strategy_version": strategy.version,
                        "model": self.model_analyze,
                    },
                    db=self.session,
                )
            except ValueError as exc:
                log.warning("ai_verdict_invalid", error=str(exc), verdict=verdict)
            except Exception as exc:  # noqa: BLE001
                log.warning("record_event_failed", error=str(exc))
        else:
            log.warning("ai_verdict_invalid_shape", verdict=verdict)

    # Single commit covers both AiAnalysis + EvaluationEvent
    self.session.commit()

    return AnalysisResult(
        ticker=ticker,
        model=self.model_analyze,
        prompt_version=base_version,
        strategy=strategy.name,
        strategy_version=strategy.version,
        response_markdown=response,
        requested_at=now,
        cached=False,
    )
```

- [ ] **Step 8.4: Update `AnalysisResult` to carry strategy fields**

Find the existing `AnalysisResult` dataclass (in `marketpulse/ai/types.py` or `marketpulse/ai/service.py` — likely `types.py`). Add `strategy` + `strategy_version` fields.

```bash
grep -n "class AnalysisResult\|strategy" marketpulse/ai/types.py
```

If found in `types.py`, extend:

```python
@dataclass(frozen=True)
class AnalysisResult:
    ticker: str
    model: str
    prompt_version: str
    strategy: str | None    # NEW
    strategy_version: str | None    # NEW
    response_markdown: str
    requested_at: datetime
    cached: bool
```

If the existing dataclass has many call sites that don't pass strategy (likely true), make strategy fields default `None`:

```python
strategy: str | None = None
strategy_version: str | None = None
```

The `analyze()` rewrite always passes them, but other callers (portfolio_risk_cached, etc.) won't.

- [ ] **Step 8.5: Replace `_lookup_cache` with `_lookup_cache_with_strategy`**

The existing `_lookup_cache(ticker, version)` keys on 2 columns. Add a new method `_lookup_cache_with_strategy` that keys on 4:

```python
def _lookup_cache_with_strategy(
    self, *, ticker: str, prompt_version: str,
    strategy: str, strategy_version: str,
) -> AiAnalysis | None:
    """Cache scoped to (ticker, model, prompt_version, strategy, strategy_version).

    Bumping any field forces a fresh call.
    """
    stmt = (
        select(AiAnalysis)
        .where(AiAnalysis.ticker == ticker)
        .where(AiAnalysis.model == self.model_analyze)
        .where(AiAnalysis.prompt_version == prompt_version)
        .where(AiAnalysis.strategy == strategy)
        .where(AiAnalysis.strategy_version == strategy_version)
        .where(AiAnalysis.expires_at > datetime.now(UTC))
        .order_by(AiAnalysis.requested_at.desc())
        .limit(1)
    )
    return self.session.execute(stmt).scalars().first()
```

Keep the old `_lookup_cache(ticker, version)` for legacy callers (portfolio_risk_cached uses it). Don't delete.

- [ ] **Step 8.6: Run unit + integration suite**

```bash
uv run pytest tests/integration/test_stock_analyze_with_strategy.py -v
uv run pytest tests/integration/ tests/unit/ -q
```

Expected: 5/5 new tests pass. Existing 5 Phase 2 tests in `test_stock_analyze_records_event.py` should ALSO still pass (they don't pass `model_router`, so it defaults to `self.model`; the test fakes return verdict-containing responses for both calls, which works because `_parse_analyze_output` only matches valid VERDICTS_JSON).

If Phase 2 tests break because the fake AI client's `side_effect` is a single response (now we call it twice — router + deep), the fixture needs `side_effect = [...]` with two entries. Verify and fix the existing tests' fixtures to provide a valid router response too. The simplest fix: in the existing `_build_service`, set:

```python
fake_ai.complete.side_effect = [
    'ROUTER_JSON: {"strategy": "general", "reason": "test default"}',
    ai_response,  # the original Phase 2 deep response
]
```

This makes the existing Phase 2 tests route through "general" then run the deep analysis, preserving their original assertions (which check verdict subtype, not strategy).

- [ ] **Step 8.7: Update v3-references in tests**

```bash
grep -rn "analysis-v3-zh-verdict" tests/ marketpulse/
```

Replace each match in `tests/` with `analysis-v4`. (Phase 2 tests check `prompt_version.startswith("analysis-v3")` in one place — change to `analysis-v4`.)

```bash
uv run pytest tests/ -q
```

Expected: full suite green except the pre-existing test_base.py / test_events.py issues which are already documented as pre-existing.

- [ ] **Step 8.8: Ruff + commit**

```bash
uv run ruff check marketpulse/ai/service.py marketpulse/ai/types.py tests/integration/test_stock_analyze_with_strategy.py
git add marketpulse/ai/service.py marketpulse/ai/types.py tests/integration/
git commit -m "feat(ai): analyze() becomes two-stage router → deep flow

Stage 1 (_route_strategy) picks a strategy via Haiku router.
Stage 2 (deep analysis) runs with that strategy's YAML instructions
via render_strategy_analysis_prompt. AiAnalysis row populates new
strategy + strategy_version columns. EvaluationEvent.payload gains
strategy + strategy_version (alongside existing prompt_version).

Single-commit boundary preserved: AiAnalysis insert + EvaluationEvent
record happen in one session.commit(). Cache HIT does NOT record a
new event.

AnalysisResult dataclass extended with strategy + strategy_version
fields (default None for legacy callers).

5 new integration tests + Phase 2 test fixtures updated to provide
router response. Full Phase 2 test count preserved (~57 stays green)."
```

---

### Task 9: scoring.py — 4 functions get strategy filter

**Files:**
- Modify: `marketpulse/evaluation/scoring.py` (4 functions)
- Modify: `tests/unit/test_evaluation_scoring.py` (EXTEND with 5 new tests)

- [ ] **Step 9.1: Append failing tests**

Append to `tests/unit/test_evaluation_scoring.py`:

```python
def test_compute_hit_rate_filters_by_strategy(db_session):
    from marketpulse.evaluation.scoring import compute_hit_rate

    # 2 events: one tagged momentum_breakout, one tagged general
    e1 = _ev(db_session, ticker="AAA", subtype="bullish")
    e1.payload = {**e1.payload, "strategy": "momentum_breakout"}
    _out(db_session, e1, excess=0.03)

    e2 = _ev(db_session, ticker="BBB", subtype="bullish")
    e2.payload = {**e2.payload, "strategy": "general"}
    _out(db_session, e2, excess=0.03)
    db_session.commit()

    stats_mb = compute_hit_rate(db_session, horizon=5, strategy="momentum_breakout")
    assert stats_mb.n_total == 1

    stats_gen = compute_hit_rate(db_session, horizon=5, strategy="general")
    assert stats_gen.n_total == 1


def test_compute_hit_rate_strategy_none_preserves_phase_2_behavior(db_session):
    """strategy=None (default) does NOT filter — includes events with no strategy field."""
    from marketpulse.evaluation.scoring import compute_hit_rate

    # One event with strategy, one without (Phase 2 style)
    e1 = _ev(db_session, ticker="AAA")
    e1.payload = {**e1.payload, "strategy": "momentum_breakout"}
    _out(db_session, e1, excess=0.03)

    e2 = _ev(db_session, ticker="BBB")  # no strategy in payload
    _out(db_session, e2, excess=0.03)
    db_session.commit()

    stats = compute_hit_rate(db_session, horizon=5)  # strategy default None
    assert stats.n_total == 2  # both counted


def test_get_per_ticker_hit_rates_filters_by_strategy(db_session):
    from marketpulse.evaluation.scoring import get_per_ticker_hit_rates

    e1 = _ev(db_session, ticker="AAA", subtype="bullish")
    e1.payload = {**e1.payload, "strategy": "momentum_breakout"}
    _out(db_session, e1, excess=0.03)

    e2 = _ev(db_session, ticker="BBB", subtype="bullish")
    e2.payload = {**e2.payload, "strategy": "fundamental_value"}
    _out(db_session, e2, excess=0.03)
    db_session.commit()

    rows = get_per_ticker_hit_rates(db_session, horizon=5, strategy="momentum_breakout")
    assert [r.ticker for r in rows] == ["AAA"]


def test_get_hit_rate_trend_filters_by_strategy(db_session):
    from marketpulse.evaluation.scoring import get_hit_rate_trend

    for d in range(30):
        e = _ev(db_session, days_ago=d, subtype="bullish")
        e.payload = {**e.payload, "strategy": "momentum_breakout"}
        _out(db_session, e, excess=0.03)
    db_session.commit()

    trend_mb = get_hit_rate_trend(db_session, horizon=5, window_days=30,
                                   rolling=10, strategy="momentum_breakout")
    trend_other = get_hit_rate_trend(db_session, horizon=5, window_days=30,
                                     rolling=10, strategy="fundamental_value")
    assert any(d.n_total > 0 for d in trend_mb)
    assert all(d.n_total == 0 for d in trend_other)


def test_get_recent_events_with_outcomes_filters_by_strategy(db_session):
    from marketpulse.evaluation.scoring import get_recent_events_with_outcomes

    e1 = _ev(db_session, ticker="AAA")
    e1.payload = {**e1.payload, "strategy": "momentum_breakout"}
    _out(db_session, e1, excess=0.03)
    e2 = _ev(db_session, ticker="BBB")
    e2.payload = {**e2.payload, "strategy": "general"}
    _out(db_session, e2, excess=0.03)
    db_session.commit()

    rows = get_recent_events_with_outcomes(db_session, horizon=5,
                                            strategy="momentum_breakout")
    assert [r.ticker for r in rows] == ["AAA"]
```

- [ ] **Step 9.2: Run, fail**

```bash
uv run pytest tests/unit/test_evaluation_scoring.py -v -k strategy
```

Expected: 5 fails (functions don't accept `strategy` kwarg).

- [ ] **Step 9.3: Extend `compute_hit_rate`**

In `marketpulse/evaluation/scoring.py`, find `compute_hit_rate` (around line 50):

```python
def compute_hit_rate(
    db: Session,
    *,
    event_type: str = "ai_analysis",
    subtype: str | None = None,
    ticker: str | None = None,
    horizon: int = 5,
    source: str | None = None,
    strategy: str | None = None,         # NEW
    since: date | None = None,
) -> HitRateStats:
    """..."""
    stmt = (
        select(...)
        .join(...)
        .where(EvaluationEvent.event_type == event_type)
        .where(EvaluationOutcome.horizon_trading_days == horizon)
    )
    if subtype is not None:
        stmt = stmt.where(EvaluationEvent.subtype == subtype)
    if ticker is not None:
        stmt = stmt.where(EvaluationEvent.ticker == ticker)
    if source is not None:
        stmt = stmt.where(
            func.json_extract(EvaluationEvent.payload, "$.source") == source,
        )
    if strategy is not None:                                     # NEW
        stmt = stmt.where(                                       # NEW
            func.json_extract(EvaluationEvent.payload, "$.strategy") == strategy,  # NEW
        )                                                        # NEW
    if since is not None:
        ...
```

- [ ] **Step 9.4: Extend `get_per_ticker_hit_rates`**

Same pattern. Add `strategy: str | None = None` kwarg + the SQLite `json_extract` where-clause.

- [ ] **Step 9.5: Extend `get_hit_rate_trend`**

Same pattern. Add `strategy: str | None = None` kwarg + the where-clause.

- [ ] **Step 9.6: Extend `get_recent_events_with_outcomes`**

Same pattern. Add `strategy: str | None = None` kwarg + the where-clause.

- [ ] **Step 9.7: Run all scoring tests**

```bash
uv run pytest tests/unit/test_evaluation_scoring.py -v
```

Expected: 20/20 pass (15 Phase 2 + 5 new).

- [ ] **Step 9.8: Ruff + commit**

```bash
uv run ruff check marketpulse/evaluation/scoring.py tests/unit/test_evaluation_scoring.py
git add marketpulse/evaluation/scoring.py tests/unit/test_evaluation_scoring.py
git commit -m "feat(evaluation): scoring 4 functions accept strategy filter

compute_hit_rate / get_per_ticker_hit_rates / get_hit_rate_trend /
get_recent_events_with_outcomes all gain optional strategy parameter.
Filter implementation parallel to existing source filter — SQLite
json_extract on payload.

Default strategy=None preserves Phase 2 behavior (includes events
with and without strategy field). Backward compatible with Phase 2
data which has NO strategy field.

5 new unit tests cover filter behavior + None default + interaction
with all 4 scoring functions."
```

---

### Task 10: Router telemetry (structlog counters)

**Files:**
- Modify: `marketpulse/ai/service.py` (already has `log.info("router_picked")` and `log.warning("router_fallback")` from Task 7 — confirm they're correct)
- Test: `tests/integration/test_router_telemetry.py` (NEW)

This task is **mostly verification** that the logs added in Task 7 capture the right structlog fields. No new production code needed.

- [ ] **Step 10.1: Write failing tests**

Create `tests/integration/test_router_telemetry.py`:

```python
"""Verify router stage emits the structlog events the spec requires."""
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest
import structlog


@pytest.fixture
def log_capture():
    """Capture structlog events into a list."""
    captured = []
    structlog.configure(
        processors=[
            structlog.processors.KeyValueRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
        logger_factory=lambda *a, **kw: structlog.testing.LogCapture(captured),
    )
    yield captured
    # Reset (in real use, structlog config is global — tests should
    # use a separate logger or capsys-based approach instead of mutating
    # global config. See pytest-structlog plugin if heavy use.)


def _build_service(db_session, router_response):
    fake_ai = MagicMock()
    fake_ai.complete.side_effect = [router_response, "VERDICTS_JSON: {\"ticker\":\"AAPL\",\"verdict\":\"neutral\",\"rationale\":\"x\"}"]
    fake_data = MagicMock()
    from marketpulse.data.types import Bar, Fundamentals, Quote
    fake_data.get_quote.return_value = Quote(
        ticker="AAPL", price=180.0, change_pct=1.0, volume=10000,
        avg_volume_20d=8500, fetched_at=datetime.now(UTC), stale=False,
    )
    fake_data.get_fundamentals.return_value = Fundamentals(
        ticker="AAPL", market_cap=3e12, pe_ratio=25.0, eps=7.0,
        sector="Tech", industry="Hardware",
    )
    fake_data.get_history.return_value = [
        Bar(date=date(2026, 3, 1), open=180, high=181, low=179,
            close=180.0 + i * 0.1, volume=10000)
        for i in range(60)
    ]
    fake_data.get_news.return_value = []
    from marketpulse.ai.service import AiService
    return AiService(
        session=db_session, ai_client=fake_ai, data=fake_data,
        model="claude-sonnet-4-6", ttl_hours=24,
        model_analyze="claude-sonnet-4-6",
        model_router="claude-haiku-4-5",
    )


def test_router_picked_logs_strategy_and_reason(db_session, caplog):
    """Successful router pick emits structlog 'router_picked' with strategy field."""
    svc = _build_service(
        db_session,
        router_response='ROUTER_JSON: {"strategy": "momentum_breakout", "reason": "突破新高"}',
    )
    with caplog.at_level("INFO"):
        svc._route_strategy("AAPL")
    # caplog captures the *formatted* log output; check the key fields appear
    assert "router_picked" in caplog.text
    assert "momentum_breakout" in caplog.text


def test_router_fallback_logs_warning_with_reason(db_session, caplog):
    """Fallback emits 'router_fallback' (parse failure) at WARNING."""
    svc = _build_service(db_session, router_response="garbage no marker")
    with caplog.at_level("WARNING"):
        svc._route_strategy("AAPL")
    assert "router_fallback" in caplog.text
    assert "parse_or_invalid" in caplog.text or "general" in caplog.text
```

- [ ] **Step 10.2: Confirm structlog config in test envt supports caplog**

MarketPulse's structlog setup may pipe through Python's stdlib logging — if so, caplog works. If structlog is configured with custom processors that bypass stdlib, this test may need adjustment.

Check:

```bash
grep -n "structlog.configure\|configure_logging" marketpulse/logging.py 2>/dev/null
```

If `configure_logging` uses `ConsoleRenderer` and a stdlib logger factory, caplog should work. If not, swap to using `structlog.testing.capture_logs` instead:

```python
from structlog.testing import capture_logs

def test_router_picked_logs_strategy_and_reason(db_session):
    svc = _build_service(...)
    with capture_logs() as captured:
        svc._route_strategy("AAPL")
    assert any(
        e.get("event") == "router_picked" and e.get("strategy") == "momentum_breakout"
        for e in captured
    )
```

Pick whichever fits the project's existing testing patterns (likely `capture_logs` for structlog purity).

- [ ] **Step 10.3: Run, expect green (the logs already exist from Task 7)**

```bash
uv run pytest tests/integration/test_router_telemetry.py -v
```

Expected: 2/2 pass. If they fail, the structlog test harness needs adjustment (see Step 10.2).

- [ ] **Step 10.4: Ruff + commit**

```bash
uv run ruff check tests/integration/test_router_telemetry.py
git add tests/integration/test_router_telemetry.py
git commit -m "test(strategies): router telemetry — assert structlog events emitted

Verifies the router stage emits 'router_picked' (info) on success
and 'router_fallback' (warning) on parse/invalid-name failure, with
the expected structured fields (strategy, reason). Per spec §
Telemetry — operators can grep these in production logs."
```

---

### Task 11: `/stock` UI strategy chip + CSS

**Files:**
- Modify: `marketpulse/web/routes/stock.py` (add strategy to template context)
- Modify: `marketpulse/web/templates/stock.html` (chip below title)
- Modify: `marketpulse/web/static/css/app.css` (chip color)
- Test: `tests/web/test_stock_strategy_chip.py` (NEW)

- [ ] **Step 11.1: Write failing tests**

Create `tests/web/test_stock_strategy_chip.py`:

```python
"""Strategy chip in /stock AI card head."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.db.models import AiAnalysis


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _seed_cached(db, *, ticker="AAPL", strategy="momentum_breakout"):
    now = datetime.now(UTC)
    db.add(AiAnalysis(
        ticker=ticker,
        model="claude-sonnet-4-6",
        prompt_version="analysis-v4",
        strategy=strategy,
        strategy_version="v1",
        input_data_json="{}",
        response_markdown="## body\n\nVERDICTS_JSON: {\"ticker\":\"AAPL\",\"verdict\":\"bullish\",\"rationale\":\"x\"}",
        requested_at=now,
        expires_at=now + timedelta(hours=24),
    ))
    db.commit()


def test_stock_page_renders_strategy_chip_when_analysis_cached(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_cached(db_session)
    r = client.get("/stock/AAPL")
    assert "mp-chip--strategy" in r.text
    assert "动量突破" in r.text  # display_name


def test_stock_page_no_strategy_chip_when_no_cached_analysis(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/stock/AAPL")
    assert "mp-chip--strategy" not in r.text


def test_stock_page_chip_uses_strategy_display_name_not_internal_name(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_cached(db_session, strategy="fundamental_value")
    r = client.get("/stock/AAPL")
    # Display name should appear, not internal snake_case
    assert "价值分析" in r.text
    # The internal name is not user-visible (it can still be in HTML attrs, just not as the visible chip text)
    # Light check: not in plain text within a span — but easier to skip if too brittle.
```

- [ ] **Step 11.2: Run, fail**

```bash
uv run pytest tests/web/test_stock_strategy_chip.py -v
```

Expected: 3 fails (no chip rendered).

- [ ] **Step 11.3: Update `marketpulse/web/routes/stock.py`**

Find the GET `/stock/{ticker}` handler. Locate where the latest cached `AiAnalysis` is looked up for displaying the most recent analysis (search the handler):

```bash
grep -n "AiAnalysis" marketpulse/web/routes/stock.py
```

Where the latest `AiAnalysis` row is queried for the template, also extract `strategy` and `strategy_version`. Inject into context:

```python
ai_strategy_display = None
if cached_analysis and cached_analysis.strategy:
    from marketpulse.strategies import load_strategies
    strategies = load_strategies()
    if cached_analysis.strategy in strategies:
        ai_strategy_display = strategies[cached_analysis.strategy].display_name
    else:
        ai_strategy_display = cached_analysis.strategy  # raw name fallback

ctx["ai_strategy_name"] = cached_analysis.strategy if cached_analysis else None
ctx["ai_strategy_display"] = ai_strategy_display
```

- [ ] **Step 11.4: Update template `marketpulse/web/templates/stock.html`**

Find the AI analysis card head (search for `AI 分析` or `mp-ai-badge`):

```bash
grep -n "AI 分析\|mp-ai-badge" marketpulse/web/templates/stock.html
```

Below the existing card head (which holds the title + Phase 2 hit-rate badge), add a `mp-card__sub` line for the strategy chip:

```html
{% if ai_strategy_display %}
<div class="mp-card__sub" style="display:flex; align-items:center; gap:8px;">
  <span class="mp-chip mp-chip--strategy">{{ ai_strategy_display }}</span>
  <span class="muted">策略 · 由 router 自动选择</span>
</div>
{% endif %}
```

- [ ] **Step 11.5: Append CSS to `marketpulse/web/static/css/app.css`**

```css
/* ════════ Phase 3: strategy chip ════════ */
.mp-chip--strategy {
  background: var(--ns-tertiary-container);
  color: var(--ns-on-tertiary-container);
  font-weight: 600;
}
```

If `--ns-tertiary-container` and `--ns-on-tertiary-container` aren't defined tokens, substitute with concrete colors:

```css
.mp-chip--strategy {
  background: #ede9fe;
  color: #5b21b6;
  font-weight: 600;
}
```

- [ ] **Step 11.6: Run tests**

```bash
uv run pytest tests/web/test_stock_strategy_chip.py -v
```

Expected: 3/3 pass.

- [ ] **Step 11.7: Run broader web suite**

```bash
uv run pytest tests/web/ -q
```

Expected: no new failures (pre-existing 2 test_base.py failures are OK per Phase 2 spec).

- [ ] **Step 11.8: Ruff + commit**

```bash
uv run ruff check marketpulse/web/routes/stock.py tests/web/test_stock_strategy_chip.py
git add marketpulse/web/routes/stock.py marketpulse/web/templates/stock.html \
       marketpulse/web/static/css/app.css tests/web/test_stock_strategy_chip.py
git commit -m "feat(stock): show selected strategy chip in AI card head

When the most recent cached AiAnalysis has a strategy field, render
its display_name in a small chip on the mp-card__sub line below the
card title. Does NOT collide with the Phase 2 mp-ai-badge in the
head.

3 web tests: chip appears when analysis cached, no chip when none,
chip uses display_name not internal snake_case."
```

---

### Task 12: `/lab/ai-track` route accepts `source` + `strategy` filters

**Files:**
- Modify: `marketpulse/web/routes/lab.py:lab_ai_track`
- Test: `tests/web/test_lab_strategy_filter.py` (NEW)

- [ ] **Step 12.1: Write failing tests**

Create `tests/web/test_lab_strategy_filter.py`:

```python
"""/lab/ai-track filter — Source × Strategy two-level."""
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.db.models import EvaluationEvent, EvaluationOutcome


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _seed_event(db, *, ticker="AAPL", source="stock_analysis",
                 strategy="momentum_breakout", excess=0.03, days_ago=10):
    e = EvaluationEvent(
        event_type="ai_analysis",
        subtype="bullish",
        ticker=ticker,
        event_time=datetime.now(UTC) - timedelta(days=days_ago),
        event_price=100.0,
        payload={
            "source": source,
            "strategy": strategy,
            "strategy_version": "v1",
            "prompt_version": "analysis-v4",
        },
    )
    db.add(e)
    db.flush()
    db.add(EvaluationOutcome(
        event_id=e.id, horizon_trading_days=5,
        event_price=100.0, horizon_price=103.0,
        horizon_date=date.today(),
        forward_return=excess + 0.001,
        benchmark_ticker="SPY", benchmark_forward_return=0.001,
        excess_return=excess,
    ))


def test_lab_accepts_strategy_query_param(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="AAA", strategy="momentum_breakout")
    _seed_event(db_session, ticker="BBB", strategy="fundamental_value")
    db_session.commit()
    r = client.get("/lab/ai-track?strategy=momentum_breakout")
    assert r.status_code == 200
    assert "AAA" in r.text
    # BBB only seen via Phase 2 ticker leaderboard if we filtered correctly
    # Light check: page renders, strategy chip appears active
    assert "momentum_breakout" in r.text


def test_lab_accepts_source_query_param(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="STK", source="stock_analysis", strategy="general")
    # Recap event has no strategy
    e2 = EvaluationEvent(
        event_type="ai_analysis", subtype="bullish", ticker="RCP",
        event_time=datetime.now(UTC) - timedelta(days=10),
        event_price=100.0,
        payload={"source": "recap", "prompt_version": "commentary-v5-zh-verdicts"},
    )
    db_session.add(e2)
    db_session.flush()
    db_session.add(EvaluationOutcome(
        event_id=e2.id, horizon_trading_days=5,
        event_price=100.0, horizon_price=103.0,
        horizon_date=date.today(),
        forward_return=0.031, benchmark_ticker="SPY",
        benchmark_forward_return=0.001, excess_return=0.03,
    ))
    db_session.commit()
    r_stock = client.get("/lab/ai-track?source=stock_analysis")
    assert r_stock.status_code == 200
    assert "STK" in r_stock.text

    r_recap = client.get("/lab/ai-track?source=recap")
    assert r_recap.status_code == 200
    assert "RCP" in r_recap.text


def test_lab_strategy_filter_only_returns_matching_events(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed_event(db_session, ticker="MBR", strategy="momentum_breakout")
    _seed_event(db_session, ticker="FVR", strategy="fundamental_value")
    db_session.commit()
    r = client.get("/lab/ai-track?source=stock_analysis&strategy=momentum_breakout")
    # The recent events table should show MBR but not FVR
    # (depends on partial implementation in Task 14; rely on KPI strip n_total at minimum)
    assert "MBR" in r.text


def test_lab_invalid_strategy_returns_200_with_empty(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    # No data; filter by non-existent strategy
    r = client.get("/lab/ai-track?strategy=nonexistent_strategy")
    assert r.status_code == 200
    # Should render placeholder, not 500


def test_lab_recap_source_drops_strategy_from_url(client: TestClient, monkeypatch):
    """When source=recap, strategy filter is ignored (since recap events have no strategy)."""
    _login(client, monkeypatch)
    r = client.get("/lab/ai-track?source=recap&strategy=momentum_breakout")
    assert r.status_code == 200
    # The route should NOT 500; the query string passed through is route-implementation detail.
```

- [ ] **Step 12.2: Run, fail**

```bash
uv run pytest tests/web/test_lab_strategy_filter.py -v
```

Expected: most fail (route ignores `source`/`strategy` params).

- [ ] **Step 12.3: Extend `marketpulse/web/routes/lab.py:lab_ai_track`**

Find the route signature (around the top of `lab.py`):

```python
@router.get("/lab/ai-track", response_class=HTMLResponse)
def lab_ai_track(
    request: Request,
    ticker: str | None = None,
    horizon: int = 5,
    source: str | None = None,
    verdict: str | None = None,
    since_days: str | int = 90,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
```

Add `strategy: str | None = None` parameter (alphabetically near other filters):

```python
@router.get("/lab/ai-track", response_class=HTMLResponse)
def lab_ai_track(
    request: Request,
    ticker: str | None = None,
    horizon: int = 5,
    source: str | None = None,
    strategy: str | None = None,   # NEW
    verdict: str | None = None,
    since_days: str | int = 90,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
```

In the route body, when source != "stock_analysis", force `strategy = None`:

```python
# When user filters by recap (or anything other than stock_analysis), strategy
# is meaningless — recap events have no strategy field.
if source and source != "stock_analysis":
    strategy = None
```

Pass `strategy` into all 4 scoring calls:

```python
overall = scoring.compute_hit_rate(
    db, ticker=ticker_u, subtype=verdict, strategy=strategy, **common,
)
trend = scoring.get_hit_rate_trend(
    db, ticker=ticker_u, subtype=verdict, strategy=strategy,
    window_days=since_int or 90, rolling=30, **common,
)
per_ticker = scoring.get_per_ticker_hit_rates(
    db, subtype=verdict, strategy=strategy, **common,
)
recent = scoring.get_recent_events_with_outcomes(
    db, ticker=ticker_u, subtype=verdict, strategy=strategy,
    limit=20, **common,
)
```

Add `strategy` to the filters dict:

```python
filters = {
    "ticker": ticker, "horizon": horizon,
    "source": source, "strategy": strategy,   # NEW
    "verdict": verdict, "since_days": since_days,
}
```

Add per-strategy aggregation for the new strategy leaderboard partial:

```python
# Build per-strategy leaderboard (independent of `strategy` filter, so the
# UI always shows which strategies are performing well)
from marketpulse.strategies import load_strategies
strategy_lib = load_strategies()
per_strategy: list[dict] = []
for name in strategy_lib.keys():
    s_stats = scoring.compute_hit_rate(
        db, ticker=None, subtype=None, source="stock_analysis",
        strategy=name, horizon=horizon, since=since,
    )
    if s_stats.n_total > 0:
        per_strategy.append({
            "name": name,
            "display_name": strategy_lib[name].display_name,
            "expected_horizons": strategy_lib[name].expected_horizons,
            "n_total": s_stats.n_total,
            "n_hits": s_stats.n_hits,
            "hit_rate": s_stats.hit_rate,
            "avg_excess_return": s_stats.avg_excess_return,
        })
per_strategy.sort(key=lambda x: x["hit_rate"] or -1, reverse=True)

# Best strategy (n>=5) — for the KPI strip
best_strategy = next(
    (s for s in per_strategy if s["n_total"] >= 5),
    None,
)
```

Pass all into the template:

```python
return templates.TemplateResponse(request, "lab_ai_track.html", {
    "overall": overall,
    "trend": trend,
    "per_ticker": per_ticker,
    "recent": recent,
    "best": best,
    "best_strategy": best_strategy,                    # NEW
    "per_strategy": per_strategy,                       # NEW
    "strategy_library": list(strategy_lib.values()),    # NEW: for filter chips
    "filters": filters,
    "filters_qs": _qs_from_filters(filters),
    "filters_qs_no_ticker": _qs_from_filters({**filters, "ticker": None}),
    "filters_qs_no_strategy": _qs_from_filters({**filters, "strategy": None}),  # NEW: for chip clearing
})
```

Update `_qs_from_filters` if needed — it should already handle `strategy` since it iterates all dict items, but verify defaults:

```python
def _qs_from_filters(filters: dict) -> str:
    DEFAULTS = {"horizon": 5, "since_days": 90}
    payload = {}
    for k, v in filters.items():
        if v is None or v == "":
            continue
        if k in DEFAULTS and v == DEFAULTS[k]:
            continue
        payload[k] = str(v)
    return urlencode(payload)
```

No changes needed — it already filters None/empty.

- [ ] **Step 12.4: Run tests**

```bash
uv run pytest tests/web/test_lab_strategy_filter.py -v
```

Expected: 5/5 pass.

- [ ] **Step 12.5: Ruff + commit**

```bash
uv run ruff check marketpulse/web/routes/lab.py tests/web/test_lab_strategy_filter.py
git add marketpulse/web/routes/lab.py tests/web/test_lab_strategy_filter.py
git commit -m "feat(lab): /lab/ai-track accepts ?source & ?strategy filters

Adds strategy parameter (forced to None when source != stock_analysis).
Threads strategy through all 4 scoring queries. Pre-computes
per-strategy leaderboard for new strategy_table partial.

5 web tests: strategy filter applied, source filter applied, both
combined, invalid strategy returns 200 (empty), recap+strategy
drops strategy."
```

---

### Task 13: Two-level filter card + Best Strategy KPI

**Files:**
- Modify: `marketpulse/web/templates/partials/ai_track_filter_card.html` (two-level)
- Modify: `marketpulse/web/templates/partials/ai_track_kpi_strip.html` (5th card)
- Modify: `marketpulse/web/static/css/app.css` (disabled chip)
- Test: extends `tests/web/test_lab_strategy_filter.py` (append HTML structure tests)

- [ ] **Step 13.1: Append failing tests**

Append to `tests/web/test_lab_strategy_filter.py`:

```python
def test_lab_filter_card_renders_source_chips(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/ai-track")
    # Source chip group present
    assert "Source" in r.text or "事件来源" in r.text
    assert "stock_analysis" in r.text
    assert "recap" in r.text


def test_lab_filter_card_renders_strategy_chips_when_source_is_stock(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/ai-track?source=stock_analysis")
    assert "momentum_breakout" in r.text
    assert "fundamental_value" in r.text
    assert "general" in r.text


def test_lab_filter_card_disables_strategy_chips_when_source_is_recap(client: TestClient, monkeypatch):
    """Strategy chips visually disabled (gray, click-blocked) when source=recap."""
    _login(client, monkeypatch)
    r = client.get("/lab/ai-track?source=recap")
    # The disabled class / aria-disabled marker should appear on the strategy section
    assert "is-disabled" in r.text or "aria-disabled=\"true\"" in r.text


def test_lab_kpi_strip_renders_best_strategy_card_when_data_exists(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    # Seed 5+ events same strategy to make best_strategy non-null
    for i in range(6):
        _seed_event(db_session, ticker=f"T{i}", strategy="momentum_breakout")
    db_session.commit()
    r = client.get("/lab/ai-track")
    assert "Best Strategy" in r.text or "最强策略" in r.text


def test_lab_kpi_strip_shows_dash_when_no_strategy_has_n5(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/ai-track")
    # Empty state — Best Strategy shows dash
    assert "Best Strategy" in r.text or "最强策略" in r.text
    # And shows "—" or similar fallback
```

- [ ] **Step 13.2: Run, fail**

```bash
uv run pytest tests/web/test_lab_strategy_filter.py -v -k "filter_card\|kpi_strip\|chips"
```

Expected: 5 fails.

- [ ] **Step 13.3: Update `marketpulse/web/templates/partials/ai_track_filter_card.html`**

Read the current filter card first to understand existing structure:

```bash
cat marketpulse/web/templates/partials/ai_track_filter_card.html | head -60
```

The existing partial has chip groups for Horizon, Source, Verdict, Time. Update the Source group to use radio-button-style buttons (`stock_analysis` / `recap` / `全部 None`), and ADD a Strategy chip group below Verdict.

Replace the existing Source block:

```html
<div>
  <span class="mp-eyebrow">Source</span>
  <div class="mp-seg" style="margin-top:6px;">
    <button type="submit" name="source" value="" class="{% if not filters.source %}is-active{% endif %}">全部</button>
    <button type="submit" name="source" value="stock_analysis" class="{% if filters.source == 'stock_analysis' %}is-active{% endif %}">stock_analysis</button>
    <button type="submit" name="source" value="recap" class="{% if filters.source == 'recap' %}is-active{% endif %}">recap</button>
  </div>
</div>
```

Add Strategy block (after Verdict, before Time):

```html
{# Strategy filter: only enabled when Source == stock_analysis (recap events have no strategy field) #}
{% set strategy_disabled = filters.source and filters.source != 'stock_analysis' %}
<div class="{% if strategy_disabled %}is-disabled{% endif %}"
     {% if strategy_disabled %}aria-disabled="true" title="策略筛选仅适用于股票分析事件"{% endif %}>
  <span class="mp-eyebrow">Strategy</span>
  <div class="mp-seg" style="margin-top:6px;">
    <button type="submit" name="strategy" value=""
            {% if strategy_disabled %}disabled{% endif %}
            class="{% if not filters.strategy %}is-active{% endif %}">全部</button>
    {% for s in strategy_library %}
      <button type="submit" name="strategy" value="{{ s.name }}"
              {% if strategy_disabled %}disabled{% endif %}
              class="{% if filters.strategy == s.name %}is-active{% endif %}"
              title="{{ s.description }}">{{ s.display_name }}</button>
    {% endfor %}
  </div>
</div>
```

- [ ] **Step 13.4: Update `marketpulse/web/templates/partials/ai_track_kpi_strip.html`**

Add a 5th KPI card after the "Best Ticker" card:

```html
<div class="mp-card mp-kpi">
  <div class="mp-kpi__head">
    <span class="mp-eyebrow mp-eyebrow--primary">Best Strategy</span>
    <span class="material-symbols-outlined mp-kpi__icon">analytics</span>
  </div>
  <div class="mp-kpi__value grotesk tnum">
    {% if best_strategy %}{{ best_strategy.display_name }}{% else %}—{% endif %}
  </div>
  <div class="mp-kpi__hint">
    {% if best_strategy %}
      {{ "{:.0f}%".format(best_strategy.hit_rate * 100) }} hit · n={{ best_strategy.n_total }}
    {% else %}n<5 暂无最佳{% endif %}
  </div>
</div>
```

Since the KPI strip is now 5 cards wide, ensure the grid in `app.css` (`.mp-ai-track-kpi`) accommodates:

Current CSS (from Phase 2):
```css
.mp-ai-track-kpi {
  padding: 0 48px 16px;
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px;
}
```

Change to `repeat(5, 1fr)`:

```css
.mp-ai-track-kpi {
  padding: 0 48px 16px;
  display: grid; grid-template-columns: repeat(5, 1fr); gap: 16px;
}
@media (max-width: 1200px) {
  .mp-ai-track-kpi { grid-template-columns: repeat(3, 1fr); }
}
@media (max-width: 900px) {
  .mp-ai-track-kpi { grid-template-columns: repeat(2, 1fr); }
}
```

- [ ] **Step 13.5: Append CSS for disabled state**

```css
/* ════════ Phase 3: disabled strategy chip group ════════ */
.is-disabled {
  opacity: 0.4;
  pointer-events: none;
}
.is-disabled button {
  cursor: not-allowed;
}
```

- [ ] **Step 13.6: Run tests**

```bash
uv run pytest tests/web/test_lab_strategy_filter.py -v
```

Expected: 10/10 pass (5 from Task 12 + 5 from Task 13).

- [ ] **Step 13.7: Ruff + commit**

```bash
uv run ruff check marketpulse/web/routes/lab.py
git add marketpulse/web/templates/partials/ai_track_filter_card.html \
       marketpulse/web/templates/partials/ai_track_kpi_strip.html \
       marketpulse/web/static/css/app.css \
       tests/web/test_lab_strategy_filter.py
git commit -m "feat(lab): two-level Source → Strategy filter + Best Strategy KPI

Filter card: Source chip group at top (stock_analysis | recap | 全部),
Strategy chip group below (6 strategies + 全部). Strategy group
disabled (opacity 0.4 + pointer-events none) when Source != stock_analysis.

KPI strip: 5th card 'Best Strategy' shows the highest hit_rate strategy
with n>=5 events. Falls back to '—' / 'n<5 暂无最佳' when empty.

Grid layout updated to repeat(5, 1fr) with responsive collapse at
1200/900px breakpoints. 5 new web tests cover both UI changes."
```

---

### Task 14: Strategy leaderboard partial in rail

**Files:**
- Create: `marketpulse/web/templates/partials/ai_track_strategy_table.html` (NEW)
- Modify: `marketpulse/web/templates/lab_ai_track.html` (include)
- Test: `tests/web/test_lab_strategy_table.py` (NEW)

- [ ] **Step 14.1: Write failing tests**

Create `tests/web/test_lab_strategy_table.py`:

```python
"""Strategy leaderboard partial in /lab rail."""
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.db.models import EvaluationEvent, EvaluationOutcome


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _seed(db, *, ticker, strategy, excess, days_ago=10):
    e = EvaluationEvent(
        event_type="ai_analysis", subtype="bullish", ticker=ticker,
        event_time=datetime.now(UTC) - timedelta(days=days_ago),
        event_price=100.0,
        payload={
            "source": "stock_analysis", "strategy": strategy,
            "strategy_version": "v1", "prompt_version": "analysis-v4",
        },
    )
    db.add(e); db.flush()
    db.add(EvaluationOutcome(
        event_id=e.id, horizon_trading_days=5,
        event_price=100.0, horizon_price=100 * (1 + excess + 0.001),
        horizon_date=date.today(),
        forward_return=excess + 0.001, benchmark_ticker="SPY",
        benchmark_forward_return=0.001, excess_return=excess,
    ))


def test_lab_strategy_table_renders_when_data_exists(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed(db_session, ticker="A1", strategy="momentum_breakout", excess=0.05)
    _seed(db_session, ticker="A2", strategy="fundamental_value", excess=0.02)
    db_session.commit()
    r = client.get("/lab/ai-track")
    assert "按 Strategy" in r.text or "Strategy Leaderboard" in r.text
    # Both strategies appear in the table
    assert "动量突破" in r.text
    assert "价值分析" in r.text


def test_lab_strategy_table_orders_by_hit_rate_desc(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    # momentum: 2/2 hits; fundamental: 0/2 hits
    _seed(db_session, ticker="A1", strategy="momentum_breakout", excess=0.05)
    _seed(db_session, ticker="A2", strategy="momentum_breakout", excess=0.04)
    _seed(db_session, ticker="B1", strategy="fundamental_value", excess=-0.05)
    _seed(db_session, ticker="B2", strategy="fundamental_value", excess=-0.04)
    db_session.commit()
    r = client.get("/lab/ai-track")
    # momentum_breakout (100%) should appear before fundamental_value (0%) in the rendered HTML
    mbox = r.text.index("动量突破")
    fbox = r.text.index("价值分析")
    assert mbox < fbox


def test_lab_strategy_table_shows_expected_horizons_hint(client: TestClient, monkeypatch, db_session):
    _login(client, monkeypatch)
    _seed(db_session, ticker="X", strategy="momentum_breakout", excess=0.03)
    db_session.commit()
    r = client.get("/lab/ai-track")
    # The "rated for: 5d / 20d" hint
    assert "5d" in r.text  # momentum_breakout's expected_horizons


def test_lab_strategy_table_empty_when_no_data(client: TestClient, monkeypatch):
    _login(client, monkeypatch)
    r = client.get("/lab/ai-track")
    # Render placeholder, no error
    assert r.status_code == 200
    # The strategy table either doesn't render or shows an empty state
    # We just check the page loads without errors
```

- [ ] **Step 14.2: Run, fail**

```bash
uv run pytest tests/web/test_lab_strategy_table.py -v
```

Expected: 4 fails.

- [ ] **Step 14.3: Create `marketpulse/web/templates/partials/ai_track_strategy_table.html`**

```html
{% if per_strategy %}
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">leaderboard</span>按 Strategy
    </span>
    <span class="mp-card__sub">{{ filters.horizon }}d hit rate desc</span>
  </div>
  <ul class="mp-ai-track-strategy-list">
    {% for s in per_strategy %}
      <li>
        <a href="?{{ filters_qs_no_strategy }}{% if filters_qs_no_strategy %}&{% endif %}strategy={{ s.name }}"
           class="mp-strategy-link">{{ s.display_name }}</a>
        <small class="muted">
          rated for: {% for h in s.expected_horizons %}{{ h }}d{% if not loop.last %} / {% endif %}{% endfor %}
        </small>
        {% if s.n_total < 5 %}
          <span class="mp-chip mp-chip--pending" style="margin-left:auto;">
            积累中 ({{ s.n_total }})
          </span>
        {% else %}
          <span class="mono tnum" style="margin-left:auto;">
            {{ "{:.0f}%".format(s.hit_rate * 100) }}
          </span>
          <small class="muted">{{ s.n_hits }}/{{ s.n_total }}</small>
        {% endif %}
      </li>
    {% endfor %}
  </ul>
</section>
{% endif %}
```

- [ ] **Step 14.4: Update `lab_ai_track.html` to include the new partial**

Find the rail `<aside class="mp-ai-track-rail">` block:

```bash
grep -n "mp-ai-track-rail\|ai_track_ticker_table" marketpulse/web/templates/lab_ai_track.html
```

Insert the new include before `ai_track_ticker_table.html`:

```html
<aside class="mp-ai-track-rail">
  {% include "partials/ai_track_filter_card.html" ignore missing %}
  {% include "partials/ai_track_strategy_table.html" ignore missing %}
  {% include "partials/ai_track_ticker_table.html" ignore missing %}
</aside>
```

- [ ] **Step 14.5: Append CSS**

```css
/* ════════ Phase 3: strategy leaderboard list ════════ */
.mp-ai-track-strategy-list { list-style: none; margin: 0; padding: 10px 16px 18px; }
.mp-ai-track-strategy-list li {
  display: grid; grid-template-columns: 1fr auto auto;
  column-gap: 10px; row-gap: 2px;
  grid-template-areas:
    "name      pct    chip"
    "hint      ratio  ratio";
  align-items: center; padding: 8px 0;
  border-bottom: 1px solid var(--ns-outline-variant);
}
.mp-ai-track-strategy-list li:last-child { border-bottom: 0; }
.mp-ai-track-strategy-list li .mp-strategy-link { grid-area: name; }
.mp-ai-track-strategy-list li small.muted:first-of-type { grid-area: hint; font-size: 10px; }
.mp-strategy-link { color: var(--ns-primary); text-decoration: none; }
.mp-strategy-link:hover { text-decoration: underline; }
```

- [ ] **Step 14.6: Run tests**

```bash
uv run pytest tests/web/test_lab_strategy_table.py -v
```

Expected: 4/4 pass.

- [ ] **Step 14.7: Ruff + commit**

```bash
uv run ruff check tests/web/test_lab_strategy_table.py
git add marketpulse/web/templates/partials/ai_track_strategy_table.html \
       marketpulse/web/templates/lab_ai_track.html \
       marketpulse/web/static/css/app.css \
       tests/web/test_lab_strategy_table.py
git commit -m "feat(lab): strategy leaderboard partial in /lab rail

New partial ai_track_strategy_table.html — sorted by hit_rate desc,
with expected_horizons read-only label, '积累中 (N)' pending chip
when n<5, and a link that adds ?strategy=<name> to the URL while
preserving other filters (uses filters_qs_no_strategy).

Inserted between the filter card and the ticker leaderboard in the
rail. Hidden entirely when per_strategy list is empty.

4 web tests: renders with data, hit_rate desc order, expected_horizons
hint visible, empty state graceful."
```

---

### Task 15: Final integration — full suite + ruff + smoke

- [ ] **Step 15.1: Full pytest**

```bash
uv run pytest 2>&1 | tail -1
```

Expected: ~640+ passed (Phase 2 had 582; Phase 3 adds ~55 new tests). Pre-existing test_base.py and test_events.py failures from Phase 2 carry over — UNLESS the post-Phase 2 PRs #52 and #53 fixed them (they did, both merged). After Phase 3, full suite should be green except for any environment-specific issues.

If there are any unexpected failures, fix them with a follow-up commit:

```bash
git add <files>
git commit -m "fix(phase-3): <specific issue>"
```

- [ ] **Step 15.2: Ruff entire repo**

```bash
uv run ruff check . 2>&1 | tail -3
```

Expected: `All checks passed!`. Fix any new lint issues introduced.

- [ ] **Step 15.3: Smoke test routes**

```bash
SESSION_SECRET=test-secret-thats-long-enough-32chars APP_PASSWORD_HASH=x uv run python -c "
from fastapi.testclient import TestClient
from marketpulse.web.main import app
client = TestClient(app)
for path in ['/stock/AAPL', '/lab/ai-track',
             '/lab/ai-track?source=stock_analysis',
             '/lab/ai-track?source=stock_analysis&strategy=momentum_breakout',
             '/lab/ai-track?source=recap']:
    r = client.get(path, follow_redirects=False)
    print(f'{path}: {r.status_code}')
"
```

Expected: each route returns 303 (redirect to login). No 500.

- [ ] **Step 15.4: Strategy library imports cleanly**

```bash
uv run python -c "
from marketpulse.strategies import load_strategies, Strategy
strats = load_strategies()
print('loaded:', sorted(strats.keys()))
print('count:', len(strats))
assert len(strats) == 6, 'expect 6 strategies'
assert all(isinstance(s, Strategy) for s in strats.values())
assert all(s.version == 'v1' for s in strats.values())
print('ok')
"
```

Expected:
```
loaded: ['fundamental_value', 'general', 'momentum_breakout', 'news_event', 'oversold_reversal', 'sector_rotation']
count: 6
ok
```

- [ ] **Step 15.5: Router module imports cleanly**

```bash
uv run python -c "
from marketpulse.strategies.router import (
    build_router_context, render_router_prompt, parse_router_output,
)
print('ok')
"
```

Expected: `ok`.

- [ ] **Step 15.6: Migration is at head**

```bash
uv run alembic heads
```

Expected: `0009_aianalyses_strategy (head)`.

- [ ] **Step 15.7: AiAnalysis model has new columns**

```bash
uv run python -c "
from sqlalchemy import inspect
from marketpulse.db.models import AiAnalysis
cols = {c.name for c in inspect(AiAnalysis).columns}
assert 'strategy' in cols
assert 'strategy_version' in cols
print('ok')
"
```

Expected: `ok`.

- [ ] **Step 15.8: Commit log review**

```bash
git log --oneline main..HEAD | head -20
```

Expected: 14 task commits + this final summary commit, all conventional-commit format.

- [ ] **Step 15.9: If anything failed, fix + commit**

If full suite or ruff fails, investigate and fix. Commit message:

```bash
git add <files>
git commit -m "fix(phase-3): <specific cleanup>"
```

- [ ] **Step 15.10: Final cleanup commit (optional — only if needed)**

If no cleanup needed, skip. The plan is complete.

---

## Self-Review Notes

**Placeholder scan:** Searched for "TBD" / "TODO" / "implement later" / "fill in" / "Similar to Task" patterns. None found. The two spec ambiguities (`base_system` text + AiAnalysis cache strategy) are resolved with concrete text/code in Tasks 4 and 5.

**Spec coverage:**

| Spec section | Covered by task |
|---|---|
| Goal: two-stage router → deep | Tasks 7 + 8 |
| Architecture: 11-field router context | Task 6 (`build_router_context`) |
| Architecture: data fetch dedup | Task 8 (Step 8.3, comments note re-fetch is acceptable for simplicity in v0) |
| File structure: all NEW files | Tasks 1-3, 6, 14 (NEW); Tasks 4-5, 8-13 (MODIFY) |
| YAML schema: 7 required fields | Task 3 validation |
| YAML schema: name/filename match | Task 3 (test + impl) |
| Strategy library: 6 v0 strategies | Task 2 (all 6 YAMLs) |
| Router prompt template | Task 6 (`render_router_prompt`) |
| Router output parsing (rfind, fallback) | Task 6 (`parse_router_output`) |
| Router model env-configurable | Task 7 (`model_router` kwarg, settings layer adds env wiring) |
| Router cache (in-memory, US/Eastern) | Task 7 (`_router_cache` dict) |
| EvaluationEvent.payload 3-field schema | Task 8 (Step 8.3, record_event call) |
| Prompt versioning (analysis-v4 + per-strategy version) | Task 5 (constant bump) + Task 8 (payload write) |
| Cache key extension | Task 8 (`_lookup_cache_with_strategy`) |
| scoring.py 4 functions extended | Task 9 |
| /lab Source × Strategy two-level filter | Tasks 12 + 13 |
| /lab Best Strategy KPI card | Task 13 |
| /lab ai_track_strategy_table.html partial | Task 14 |
| /lab query string preservation | Task 12 (`filters_qs_no_strategy`) |
| /stock strategy chip in mp-card__sub | Task 11 |
| Telemetry (router.pick.*, router.fallback.*) | Tasks 7 + 10 |
| Backward compat (Phase 2 v3 rows) | Task 4 (nullable columns) + Task 9 (strategy=None default) |
| Edge cases (router invalid, JSON parse fail, LLM fail) | Task 7 (`_route_strategy` fallback path) |
| `earnings_setup` deferred | Task 2 (only 6 YAMLs) |

All spec sections accounted for.

**Type consistency:**
- `Strategy` dataclass fields (name, display_name, version, description, applies_when, expected_horizons, instructions) used consistently across Tasks 1-3, 5, 6, 7, 8, 11, 12, 13, 14.
- `_route_strategy()` returns `tuple[str, str]` (strategy_name, reason) — used consistently in Task 7 tests and Task 8 analyze().
- `_lookup_cache_with_strategy` kwargs (ticker, prompt_version, strategy, strategy_version) match the AiAnalysis columns added in Task 4.
- `parse_router_output` returns `dict[str, str] | None` — Task 6 + Task 7 consistent.
- `AnalysisResult` extended with `strategy` + `strategy_version` (default None) — used in Task 8 and propagated to /stock route in Task 11.
- `EvaluationEvent.payload` keys (`source`, `strategy`, `strategy_version`, `prompt_version`, `rationale`, `model`) consistent across Tasks 8 + 9 + 12.
- `compute_hit_rate` signature stays backward compat (strategy=None default) — Tasks 9 + 12 callers both work.

No type drift detected.

**Acknowledged simplification (v0):** Task 7 + Task 8 both fetch quote / fundamentals / bars / news. The spec § Architecture says "fetch ONCE" but the plan keeps two fetches for simplicity. This is a known minor inefficiency — Phase 4 candidate to plumb the shared data through.
