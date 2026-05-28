# Phase 8a — ML Feature Snapshots Design

**Date:** 2026-05-28
**Status:** Approved (brainstorming phase)
**Project naming note:** Phase 8a is a NEW ROADMAP TRACK ("ML advisor for analyze()"), distinct from both the original 2026-05-10 v1 implementation phases (which ended at Phase 10) and the strategic Phase 1–7 milestones (which ended at broker reconciliation). It is Phase 1 of a separately-numbered C → B → D roadmap, where C = ML as LLM advisor (this phase), B = ML → strategy YAML compiler, D = ML-native bidder competing in allocator. No prior umbrella spec planned this work — it emerged from a 2026-05-28 brainstorm.

## 1 — Goal & Phase boundary

Add a structured technical-feature snapshot to `AiService.analyze(ticker)` so Claude receives 15 quantitative signals (RSI, MACD, MA gaps, breakout, etc.) as supplemental evidence per analysis call. Reduce hallucination by giving the verdict layer numerical anchors, without introducing ML training, model serving, calibration, or backtest infrastructure.

### Anti-goals for Phase 8a

- **No model training.** Pure deterministic feature engineering on existing `price_cache` data.
- **No prediction output.** Features only — no `P(up_5d)`, no `expected_return`, no calibrated probabilities.
- **No new data source.** `price_cache` is the only input; no new yfinance pull, no news, no fundamentals.
- **No allocator change.** The bid → ALLOC → execution path is unchanged. Phase 8a only enriches the analyze prompt.
- **No backfill of historical snapshots.** On-demand only; old `ai_analysis` rows are not retro-fitted.
- **No verdict-text mining.** Whether Claude actually references a feature in its prose is observable but not measured automatically in v1.

### Phase boundary

Phase 8a is **shippable** when:
1. `AiService.analyze(ticker)` includes an `ml_features` block in the prompt DATA section when input is sufficient.
2. `ml_feature_snapshots` rows accumulate as side-effect on each analyze call.
3. `ANALYSIS_PROMPT_VERSION` bumps to v5, invalidating stale `ai_analysis` cache rows.
4. Insufficient-history / NaN-Inf / DB-write-failure paths all degrade gracefully without crashing analyze.

Out of scope: any consumer reading `ml_feature_snapshots` other than the analyze prompt. That's Phase 8b.

## 2 — Locked decisions

1. **Scope is features-only.** No ML model is trained, served, or persisted. The output schema (§ 6.3) contains 15 deterministic numerical/boolean fields plus envelope metadata. Anything labeled "prediction" / "probability" / "confidence" is out of scope and rejected at code review.

2. **On-demand trigger at `AiService.analyze()` only.** No scheduler job, no nightly batch, no pre-warming. The first analyze call for `(ticker, today's price_last_date)` computes the snapshot; subsequent calls within the same `price_last_date` reuse it.

3. **Cache invalidation is `price_last_date`-aware, NOT TTL.** Cache key is `(ticker, price_last_date, feature_version)`. If `price_cache` has a newer max date for the ticker than the latest snapshot, recompute. If `feature_version` bumps in code, all snapshots for older versions are effectively orphaned (and ignored by lookup); they remain in the table for forensic value, not pruned.

4. **`feature_version=1` ships 15 fields, listed exhaustively in § 6.2.** Adding a feature requires bumping `feature_version` and a spec note. Removing or redefining a feature ALSO requires bumping `feature_version`. No silent schema drift.

5. **Insufficient-history is hard skip.** If `price_cache` returns fewer than 60 rows for the ticker, `status = "insufficient_history"`, no snapshot row is written, no `ml_features` block is injected into the prompt. The analyze call proceeds without ML features. This is structurally distinct from "all fields computed but some are NaN" (lock 8).

6. **`source_row_count` records actual rows fed to the calculator.** Not `lookback_days`, not the configured minimum. If `price_cache` returns 73 rows, `source_row_count=73`. This makes warm-up debugging tractable — a snapshot with `source_row_count=60` (bare minimum) vs `source_row_count=90` (preferred) can be distinguished post-hoc when feature values disagree.

7. **`features_json` is an enveloped JSON object, not raw values.** Mandatory shape:

   ```json
   {
     "feature_version": 1,
     "price_last_date": "2026-05-28",
     "status": "ok",
     "values": { "<feature_name>": <number_or_bool>, ... }
   }
   ```

   The envelope holds metadata (`feature_version`, `price_last_date`, `status`); `values` holds exactly the v1 fields. Phase 8b can extend the envelope (e.g., add `predictions: {...}`) without disrupting v1 consumers. `status` values: `"ok"` (snapshot written) or `"insufficient_history"` / `"invalid"` (when status is anything other than `"ok"`, the row is NOT written — see locks 5 and 8).

8. **NaN / Inf are rejected globally.** Any feature value that is NaN, +Inf, -Inf, or otherwise not JSON-safe causes the entire snapshot to be marked `status="invalid"`, NOT written to the DB, and NOT injected into the prompt. A WARNING log line `ml_features_invalid ticker=<t> reason=<which_field>` is emitted. The analyze call continues without ML features. Rationale: half-injected blocks induce false reasoning in the LLM; a single bad field discards the whole snapshot.

9. **Persistence is best-effort, decoupled from injection.** Compute succeeds → the in-memory feature dict is the source of truth for THIS analyze call. DB write may succeed or fail independently:
   - DB write succeeds → `snapshot_id` is recorded in `AiAnalysis.input_data_json`.
   - DB write fails → `snapshot_id = null` is recorded, plus `ml_features_source: "computed_unpersisted"`. A WARNING log is emitted. The current analyze STILL injects the in-memory features.
   - Compute fails → fall through to lock 5 (insufficient_history) or lock 8 (invalid).

   Rationale: a transient DB error must NOT degrade the analyze quality for the current call.

10. **`AiService.analyze()` prompt structure**: the `ml_features` block sits at the END of the rendered DATA section, AFTER strategy outputs / market context / news. The LLM anchors on strategy logic first; ML evidence is consulted as supplementary check, not as a leading frame. The block carries inline system guidance (§ 7.2) framing it as "supplemental heuristic evidence only — do not override strategy-specific logic, risk controls, or contradictory evidence." This positioning is locked because the alternative (features at the top) measurably biases Claude toward feature-worship in pilot prompts.

11. **`ANALYSIS_PROMPT_VERSION` bumps from `v4` → `v5`.** This invalidates all existing cached `ai_analysis` rows for the analyze flow. Rationale: a verdict generated under v4 (no ML features) and a verdict under v5 (with ML features) are different prompt artifacts; caching across the boundary would silently mix them. Recap commentary version is unaffected (recap does not use this code path).

12. **No verdict-text mining in v1.** Whether the LLM textually references "RSI" or "breakout" in its prose is NOT extracted, scored, or A/B-tested in Phase 8a. The Phase 8b → 8c roadmap defers this to when the predictions layer ships.

## 3 — Architecture & module layout

```
marketpulse/
├── ml/                                  # NEW namespace
│   ├── __init__.py                      # exports `compute_snapshot`, `lookup_or_compute`
│   ├── features.py                      # 15 pure compute functions + insufficient_history guard
│   └── snapshots.py                     # DB upsert + cache lookup + best-effort write
│
├── ai/
│   ├── service.py                       # MODIFIED: analyze() calls ml.lookup_or_compute()
│   └── prompts.py                       # MODIFIED: render_strategy_analysis_prompt() conditionally appends ml_features block
│
├── db/
│   └── models.py                        # MODIFIED: add MlFeatureSnapshot ORM model
│
└── migrations/versions/
    └── 00XX_ml_feature_snapshots.py     # NEW Alembic migration
```

**Module responsibilities:**

- `marketpulse/ml/features.py`: pure pandas/numpy. Input is an OHLCV DataFrame (ascending by date); output is `dict[str, float | int]` or raises if any value is NaN/Inf (lock 8). Has ZERO DB awareness, ZERO logging in normal paths, ZERO IO. Fully unit-testable with synthetic data.

- `marketpulse/ml/snapshots.py`: knows about the `MlFeatureSnapshot` model, `price_cache` reader, and the in-process logger. Encapsulates the lookup-then-compute-then-persist sequence. Hands back a `SnapshotResult` dataclass:

  ```python
  @dataclass(frozen=True)
  class SnapshotResult:
      values: dict[str, float | int] | None   # None when status != "ok"
      status: Literal["ok", "insufficient_history", "invalid"]
      price_last_date: date | None
      source_row_count: int                   # 0 when status != "ok"
      snapshot_id: int | None                 # None when not persisted
      source: Literal["cached", "computed_persisted", "computed_unpersisted", "skipped"]
  ```

  `source` distinguishes the four outcomes of lock 9. Callers (currently only `ai/service.py`) MUST handle all four.

- `marketpulse/ai/service.py`: in `analyze()`, before calling the LLM, invokes `ml.lookup_or_compute(session, ticker)`. The result is passed to `prompts.render_strategy_analysis_prompt(...)` as a new kwarg `ml_snapshot: SnapshotResult | None`. Result is also recorded in the `AiAnalysis` row's `input_data_json` (lock 9 telemetry).

- `marketpulse/ai/prompts.py`: `render_strategy_analysis_prompt` gains a final optional section. If `ml_snapshot.status == "ok"`, append the JSON block + system guidance text. Otherwise, append nothing. `ANALYSIS_PROMPT_VERSION` bumps to `"v5"`.

## 4 — Data flow

```
AiService.analyze(ticker)
   │
   ├─► [Phase 3 router stage] pick strategy
   │
   ├─► ml.lookup_or_compute(session, ticker)
   │       │
   │       ├─► SELECT max(date) FROM price_cache WHERE ticker=?
   │       │       └─► no rows → SnapshotResult(status="insufficient_history", ...)
   │       │
   │       ├─► SELECT * FROM ml_feature_snapshots
   │       │       WHERE ticker=? AND price_last_date=? AND feature_version=1
   │       │       └─► HIT  → SnapshotResult(status="ok", source="cached", values=..., snapshot_id=N)
   │       │       └─► MISS → continue
   │       │
   │       ├─► SELECT bars FROM price_cache WHERE ticker=? ORDER BY date
   │       │       (keep last 90 if available; minimum 60 required)
   │       │       └─► <60 rows → SnapshotResult(status="insufficient_history", ...)
   │       │
   │       ├─► features.compute_features(bars)
   │       │       ├─► any NaN/Inf → SnapshotResult(status="invalid", ...)  + log ml_features_invalid
   │       │       └─► values dict
   │       │
   │       └─► try INSERT INTO ml_feature_snapshots (...)
   │               ├─► OK  → SnapshotResult(source="computed_persisted", snapshot_id=N, ...)
   │               └─► IntegrityError / OperationalError
   │                      → SnapshotResult(source="computed_unpersisted", snapshot_id=None, ...)
   │                      + log ml_features_persist_failed
   │
   ├─► prompts.render_strategy_analysis_prompt(..., ml_snapshot=result)
   │       └─► if result.status == "ok": append ml_features block (§ 7)
   │
   ├─► AnthropicClient.complete(rendered_prompt)
   │
   └─► persist AiAnalysis row
           input_data_json includes:
              "ml_features_snapshot_id": result.snapshot_id,
              "ml_features_source": result.source,
              "ml_features_status": result.status
```

## 5 — DB schema

### 5.1 `ml_feature_snapshots`

```sql
CREATE TABLE ml_feature_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              VARCHAR(16) NOT NULL,
    price_last_date     DATE NOT NULL,
    feature_version     INTEGER NOT NULL,
    features_json       TEXT NOT NULL,        -- enveloped JSON, schema in § 6.3
    computed_at         DATETIME NOT NULL,    -- TZ-aware (UTC at write time)
    source_row_count    INTEGER NOT NULL,     -- actual rows used (lock 6)
    lookback_days       INTEGER NOT NULL,     -- configured target (e.g., 90)

    CONSTRAINT uq_ml_feature_snapshot_natural
        UNIQUE (ticker, price_last_date, feature_version)
);

CREATE INDEX ix_ml_feature_snapshots_lookup
    ON ml_feature_snapshots (ticker, price_last_date, feature_version);
```

Notes:
- `features_json` is the SQLAlchemy `JSON` column type (stored as TEXT in SQLite; behavior unchanged).
- `source_row_count` records actual rows pulled from `price_cache`; `lookback_days` records what the calling code requested.
- UNIQUE constraint enforces the cache key. INSERT conflict on a recompute is impossible by construction (we lookup first); if it happens it's a race condition and the IntegrityError surfaces as `source="computed_unpersisted"` per lock 9.

### 5.2 `AiAnalysis.input_data_json` extension

No schema change. The existing `JSON` column gains three optional keys:

```json
{
  ... (existing fields) ...,
  "ml_features_snapshot_id": 1234,            // null when not persisted
  "ml_features_source": "computed_persisted", // one of: cached / computed_persisted / computed_unpersisted / skipped
  "ml_features_status": "ok"                  // ok / insufficient_history / invalid
}
```

Backward compat: existing rows have no keys; readers (currently none — only used for observability) must treat absence as "no Phase 8a context."

### 5.3 Alembic migration

```python
# migrations/versions/00XX_ml_feature_snapshots.py
"""Add ml_feature_snapshots table for Phase 8a"""

def upgrade():
    op.create_table(
        "ml_feature_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("price_last_date", sa.Date(), nullable=False),
        sa.Column("feature_version", sa.Integer(), nullable=False),
        sa.Column("features_json", sa.JSON(), nullable=False),
        sa.Column("computed_at", TZDateTime(), nullable=False),
        sa.Column("source_row_count", sa.Integer(), nullable=False),
        sa.Column("lookback_days", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "ticker", "price_last_date", "feature_version",
            name="uq_ml_feature_snapshot_natural",
        ),
    )
    op.create_index(
        "ix_ml_feature_snapshots_lookup",
        "ml_feature_snapshots",
        ["ticker", "price_last_date", "feature_version"],
    )

def downgrade():
    op.drop_index("ix_ml_feature_snapshots_lookup", table_name="ml_feature_snapshots")
    op.drop_table("ml_feature_snapshots")
```

Migration revision number is the next available integer past the current head (will be determined at implementation time).

## 6 — Feature catalog v1

### 6.1 Configuration

- **Timeframe:** daily only.
- **Source:** `price_cache` table; no live yfinance call.
- **Lookback target:** 90 trading days preferred, 60 required (warm-up for MACD/EMA).
- **Symbol scope:** any ticker passed to `analyze()`; no allow-list, no holdings filter.

### 6.2 The 15 features

All computations are on the price_cache rows for the ticker, ascending by date. `close[i]` denotes close on row `i`; index `-1` is the most recent.

#### Trend / moving averages (5)

| Feature | Definition | Type |
|---|---|---|
| `ma5` | `mean(close[-5:])` | float |
| `ma20` | `mean(close[-20:])` | float |
| `ma60` | `mean(close[-60:])` | float |
| `ma_gap_20` | `(close[-1] - ma20) / ma20` | float |
| `ma_alignment` | `1` if `ma5 > ma20 > ma60`; `-1` if `ma5 < ma20 < ma60`; `0` otherwise | int (−1/0/+1) |

#### Momentum / returns (3)

| Feature | Definition | Type |
|---|---|---|
| `return_1d` | `close[-1] / close[-2] - 1` | float |
| `return_5d` | `close[-1] / close[-6] - 1` | float |
| `return_20d` | `close[-1] / close[-21] - 1` | float |

#### Oscillators (2)

| Feature | Definition | Type |
|---|---|---|
| `rsi_14` | Wilder's RSI(14), final value. Smoothing via `pandas.ewm(alpha=1/14, adjust=False)` on gains/losses. | float, 0–100 |
| `macd_signal_diff` | Let `MACD = EMA12(close) - EMA26(close)` and `signal = EMA9(MACD)`; then `macd_signal_diff = MACD - signal` (the histogram, final value) | float |

#### Volatility (1)

| Feature | Definition | Type |
|---|---|---|
| `atr_14_pct` | Wilder's ATR(14) / close[-1]. ATR uses True Range = `max(high-low, |high - prev_close|, |low - prev_close|)`. | float (e.g., 0.043 = 4.3%) |

#### Volume (2)

| Feature | Definition | Type |
|---|---|---|
| `volume_ratio_20` | `volume[-1] / mean(volume[-20:])` | float |
| `obv_slope_5` | `(OBV[-1] - OBV[-6]) / mean(volume[-20:])` — normalized by 20d avg volume so different tickers are comparable | float |

#### Patterns (2)

| Feature | Definition | Type |
|---|---|---|
| `breakout_20d_high` | `1` if `close[-1] > max(high[-21:-1])` (excludes today; lock against trivial self-comparison); else `0` | int (0/1) |
| `gap_open_pct` | `(open[-1] - close[-2]) / close[-2]` | float |

### 6.3 Output envelope

```json
{
  "feature_version": 1,
  "price_last_date": "2026-05-28",
  "status": "ok",
  "values": {
    "ma5": 123.45,
    "ma20": 119.20,
    "ma60": 115.10,
    "ma_gap_20": 0.031,
    "ma_alignment": 1,
    "return_1d": 0.008,
    "return_5d": 0.082,
    "return_20d": 0.142,
    "rsi_14": 62.1,
    "macd_signal_diff": 1.24,
    "atr_14_pct": 0.043,
    "volume_ratio_20": 1.48,
    "obv_slope_5": 0.77,
    "breakout_20d_high": 1,
    "gap_open_pct": 0.012
  }
}
```

The envelope is the EXACT JSON written into `ml_feature_snapshots.features_json` AND the EXACT JSON injected into the prompt (§ 7). One canonical form; no transcoding boundary where bugs hide.

## 7 — Prompt injection

### 7.1 Position in rendered prompt

Existing structure of `render_strategy_analysis_prompt` (Phase 3, unchanged in 5/6/7):

```
SYSTEM:
  <strategy-specific system text>

DATA:
  ticker: NVDA
  strategy_outputs: [...]
  market_context: {...}
  recent_news: [...]
  fundamentals: {...}
  ←── ml_features block inserted HERE (locked end-of-DATA position)
```

### 7.2 Block content

When `ml_snapshot.status == "ok"`, append the following to the DATA payload (literal markdown including the inline guidance paragraph):

```markdown
ml_features:
{
  "feature_version": 1,
  "price_last_date": "2026-05-28",
  "status": "ok",
  "values": { "ma5": 123.45, "rsi_14": 62.1, ... 13 more ... }
}

ml_features_guidance:
The ml_features block above is supplemental heuristic evidence only.
It does NOT override strategy-specific logic, risk controls, or contradictory
evidence elsewhere in this prompt. Treat it as weak-to-moderate supporting
signal: a confluence with strategy_outputs raises confidence, a contradiction
warrants explanation rather than dismissal of strategy_outputs.
```

When `ml_snapshot.status != "ok"`, append **nothing** (no header, no null, no placeholder). The prompt is structurally identical to v4 plus the prompt version bump.

### 7.3 Prompt version

`ANALYSIS_PROMPT_VERSION = "v5"` in `marketpulse/ai/prompts.py`. The version is included in the `AiAnalysis` cache key, so old v4 verdicts cannot collide with v5. Recap prompt version (`COMMENTARY_PROMPT_VERSION`) is unchanged.

## 8 — Testing strategy

Tests adopt the Phase 5e `# Layer: invariant` vs `# Layer: behavioral` tagging discipline (5e lock #13 / enforcement hook 5e-B8b). All new tests carry one of:
- `# Layer: pure` — feature math (no DB, no I/O)
- `# Layer: stateful` — full DB roundtrip via `db_session` fixture
- `# Layer: invariant` — structural property of the system

### 8.1 Feature math (pure)

For each of the 15 features:
- **Known-answer test:** synthetic 90-row OHLCV with hand-computed expected value.
- **Boundary test:** at the 60-row minimum, value still computes and matches `ta`-library-equivalent reference (we use ta as a CROSS-CHECK, NOT as a dependency).
- **Numerical-stability test:** constant prices (zero variance) — verify no division-by-zero / NaN escape (RSI typically returns 50 or 100 depending on side; ATR returns 0; MA equals price). These cases must trigger lock 8 (invalid) only if a NaN/Inf actually emerges, not preemptively.

### 8.2 Insufficient-history guard

- `# Layer: stateful`: seed `price_cache` with 59 rows for `XYZ`; call `lookup_or_compute("XYZ")`; assert `status == "insufficient_history"`, no DB row written, `source == "skipped"`.
- Compare with 60 rows: status `"ok"`, snapshot row written, all 15 fields present.

### 8.3 Cache lookup

- `# Layer: stateful`: seed cache, call `lookup_or_compute("XYZ")` twice; second call returns `source == "cached"` with identical `snapshot_id`.
- Bump `feature_version` in code (monkeypatch), recall: new snapshot row written; old row remains.
- Advance `price_cache` by one row (new latest date), recall: new snapshot, new `price_last_date`.

### 8.4 NaN / Inf rejection

- `# Layer: pure` + `# Layer: stateful`: monkeypatch one feature function to return `float("nan")`. Call `compute_snapshot`; assert raises a structured `MlFeaturesInvalidError`; assert `lookup_or_compute` catches it, returns `status="invalid"`, writes NO DB row, emits exactly one `ml_features_invalid` warning log.
- Repeat for `+Inf` and `-Inf`.
- Negative test: a legitimate negative feature value (e.g., `return_1d == -0.05`) does NOT trip the guard.

### 8.5 Persistence-failure isolation

- `# Layer: stateful`: monkeypatch the `session.commit()` to raise `OperationalError` after the feature dict is built; assert `SnapshotResult` returned has `source == "computed_unpersisted"`, `snapshot_id is None`, `values` populated. Assert one warning log.
- Assert the LLM still sees the features (verify via the prompt-rendering integration test, § 8.7).

### 8.6 Prompt injection

- `# Layer: stateful`: mock the AI client. Call `analyze("XYZ")` with sufficient price_cache. Capture the rendered prompt. Assert:
  - The literal substring `ml_features:` appears exactly once.
  - The literal substring `"feature_version": 1` appears.
  - The literal substring `ml_features_guidance:` appears.
  - The substring appears AFTER `strategy_outputs:` (positional invariant § 7.1).
- Same setup with insufficient history: assert NONE of those substrings appear.
- Same with NaN-poisoned compute: assert NONE of those substrings appear.

### 8.7 Prompt version bump

- `# Layer: invariant`: `assert ANALYSIS_PROMPT_VERSION == "v5"`.
- Test that a v4-cached `AiAnalysis` row is NOT returned for an analyze call under v5 (existing cache machinery already keys on prompt_version; we just verify Phase 8a doesn't accidentally subvert it).

### 8.8 AiAnalysis row telemetry

- `# Layer: stateful`: full E2E analyze call. Inspect the persisted `AiAnalysis.input_data_json`. Assert:
  - Cache-hit path: `ml_features_source == "cached"`, snapshot_id is a positive int.
  - Compute-and-persist path: `ml_features_source == "computed_persisted"`, snapshot_id matches the new row.
  - Compute-but-DB-fail path: `ml_features_source == "computed_unpersisted"`, snapshot_id is null.
  - Skipped path: `ml_features_source == "skipped"`, snapshot_id is null, `ml_features_status` is `"insufficient_history"`.

## 9 — Out of scope (deferred to Phase 8b or later)

- **Predictions** — `P(up_5d)`, expected return, calibrated probabilities. Phase 8b.
- **Model training pipeline** — train/val/test split, walk-forward retraining, model registry, feature importance, hyperparameter sweep. Phase 8b.
- **A/B verdict drift measurement** — does Claude's verdict change with vs without ml_features? Requires shadow-mode analyze. Phase 8b.
- **Verdict-text mining** — extract which features Claude actually referenced in prose. Phase 8b.
- **ML → strategy YAML compiler** — auto-generate `strategies/generated/ml_*.yaml`. Phase 8c.
- **ML-native bidder** — ML strategy competes in allocator alongside the 6 hand-written ones. Phase 8d.
- **Cross-sectional features** — features that compare a ticker to its sector or to SPY (e.g., `relative_strength_vs_spy`). Requires reading multiple tickers per snapshot. Phase 8b candidate.
- **Intraday timeframe** — minute-level features. Out of scope of the entire ML track for v1 (4-pillar B work).
- **News / fundamentals features** — Phase 8b candidate, requires expanding input beyond `price_cache`.

## 10 — Phase 8b forward-warnings

The 5e/Phase-6-umbrella pattern: name the architectural pressure points Phase 8a deliberately does NOT solve, so the Phase 8b spec inherits a clear set of problems.

**PP1 — `features_json` envelope evolution.** Phase 8a fixes the envelope at `{feature_version, price_last_date, status, values}`. Phase 8b will likely add `predictions`, possibly `meta` (e.g., regime label), possibly `confidence`. The envelope MUST stay backward-readable: a v1 reader looking at a v2 snapshot must not crash on unknown keys. Phase 8b's spec must define the additive-only rule.

**PP2 — Cache key suffices for features, not for predictions.** `(ticker, price_last_date, feature_version)` uniquely identifies a feature snapshot because features are pure deterministic functions of OHLCV. PREDICTIONS additionally depend on `model_version` and `model_training_data_cutoff`. Phase 8b must extend the cache key. The current `feature_version` column will need a sibling `prediction_version`.

**PP3 — `ml_features_source` distinction widens.** Phase 8a has 4 sources (cached / computed_persisted / computed_unpersisted / skipped). Phase 8b will need a 5th: `predictions_only_cached` (features hit cache but predictions need recompute because model_version bumped). This is observability hygiene, not a blocker.

**PP4 — On-demand compute may not scale.** Phase 8a's MVP touches `price_cache` once per analyze call when the cache misses. For 5 holdings × 1 analyze/day this is trivial. For a Phase 8b scenario where every watchlist ticker (~30) gets an analyze nightly, the read load matters. Mitigation deferred to 8b: either a nightly precompute job (becomes the original Q3 option B) or a price_cache index review.

**PP5 — Verdict mining and feature attribution.** Phase 8b's natural next step ("does Claude actually use these features?") requires either (a) a parser over verdict prose looking for feature names, or (b) a structured second LLM call asking Claude to itemize the evidence it weighed. Both have well-known failure modes (false positives in (a), cost in (b)). Phase 8b's spec must pick one.

## 11 — Operational test map

Compact: ~10 scenarios across 6 categories. Structured by failure class.

| # | Category | Scenario | Locks protected |
|---|---|---|---|
| 1 | **Feature math** | Known-answer test for each of the 15 features (§ 8.1) | 4 |
| 2 | **Feature math** | Constant-price series → no NaN escape | 8 |
| 3 | **History guard** | 59 rows → status="insufficient_history", no row, no prompt block | 5 |
| 4 | **History guard** | 60 rows → status="ok", row written, prompt block present | 5 |
| 5 | **Cache** | Second analyze same `price_last_date` → source="cached" | 3 |
| 6 | **Cache** | New `price_last_date` → source="computed_persisted", new row | 3 |
| 7 | **NaN guard** | NaN-poisoned compute → status="invalid", no row, no inject, 1 warning log | 8 |
| 8 | **Persist isolation** | DB commit raises → source="computed_unpersisted", values still injected | 9 |
| 9 | **Prompt injection** | Block appears at END of DATA section, after strategy_outputs | 10 |
| 10 | **Prompt injection** | Insufficient/invalid status → block completely omitted | 5, 8 |
| 11 | **Telemetry** | `AiAnalysis.input_data_json` records snapshot_id, source, status for all 4 paths | 9 |
| 12 | **Prompt version** | `ANALYSIS_PROMPT_VERSION == "v5"`; v4 cache hits do not return v5 prompts | 11 |

## 12 — No new locks added by Section 10–11

Sections 10 and 11 are forward-warning and test-mapping respectively. The 12 numbered locks in § 2 are the complete locked set for Phase 8a.
