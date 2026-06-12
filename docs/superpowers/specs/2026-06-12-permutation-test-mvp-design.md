# Permutation Test MVP — Design

**Date:** 2026-06-12
**Status:** Approved (design locked)
**Charter link:** strategy-trust chain (research-trustworthiness evidence chain, item 1).
First statistical-validation deliverable. Deliberately an MVP, not a statistics framework:
answer the single most important open question first — **does the system have edge?**

## Problem

MarketPulse has hit-rate metrics (110 resolved h5 outcomes, per-strategy leaderboards) but no
statistical control. With this little data, an observed hit rate of 58% — or a leaderboard
winner at 68% — may be pure chance. Two distinct doubts, currently unanswerable:

1. **System edge:** are the AI verdicts, as a whole, better than random pairing with outcomes?
2. **Champion illusion:** the "best strategy" is a max over ~6 strategies of ~18 samples each —
   under randomness *something* always wins (Harvey-Liu-Zhu selection effect). Is the observed
   winner better than a *randomly selected champion*?

## Goal

One CLI run prints two p-values (A: system, C: best-strategy, selection-corrected) plus an
interpretation quadrant. Pure function + CLI, **zero persistence, zero schema, zero routes,
zero network, zero new dependencies** (stdlib `random` suffices).

## Scope (locked)

**In:** `marketpulse/evaluation/permutation.py` (pure computation) +
`python -m marketpulse.cli.permutation_test` (JSON to stdout) + CHARTER recalibration note.
**Out (deferred, recorded in CHARTER):** Bootstrap CI (P2), Shadow 2a (P3), Walk-Forward (P4 —
last because current samples are too thin to split), block permutation, result persistence,
/lab UI, h1 horizon by default (parameterized but not the default run).

## The test (A+C, one shuffle engine)

### Data extraction (one query)

All `(subtype, excess_return, strategy)` triples where:
- `event_type = 'ai_analysis'`, `horizon_trading_days = 5` (CLI flag `--horizon`, default 5)
- **A-sample eligibility (locked):** `subtype IS NOT NULL AND excess_return IS NOT NULL`;
  `strategy` MAY be null (pre-Phase-3 events count toward system edge).
- `strategy` extracted from `payload.$.strategy` (same json_extract path as
  `scoring.compute_hit_rate`).

### Hit definition (locked — no reimplementation)

```
hit = scoring._is_hit(subtype, excess_return)
```

The CLI/module MUST import and reuse `marketpulse.evaluation.scoring._is_hit`. Reimplementing
bullish/bearish/neutral threshold logic anywhere else is forbidden — one scoring authority,
or grading forks. (If exposing `_is_hit` publicly is preferred, rename/alias in scoring.py;
do not copy the logic.)

### Null hypothesis / shuffle

H0: verdict (subtype) is independent of outcome. Fix the `excess_return` vector (and each
row's `strategy` assignment); permute the `subtype` labels across rows. N = 10,000 permutations
(CLI flag `--permutations`), deterministic `--seed` default 42 (reproducible runs).

Each permutation computes BOTH statistics from the same shuffled labels:
- **A — overall hit rate** over the full A-sample.
- **C — max per-strategy hit rate over the eligible strategy set** (below).

### C's eligible strategy set (locked)

Determined ONCE from real (unshuffled) data: `strategy IS NOT NULL AND n >= 5`
(n = that strategy's row count in the A-sample; reuses the codebase's n<5 reporting gate).
Both the observed best and every permutation's max are taken over THIS FIXED SET — the set is
never re-derived per permutation, so the selection universe cannot drift between observed and
null. (Implementation note: since only subtype labels are shuffled, per-strategy n is invariant
anyway — the explicit freeze is defensive clarity and survives future variants that might
shuffle strategy assignment.) Rows with null strategy are excluded from C entirely; the count
of such rows is reported.

### p-values (locked)

One-sided, add-one estimator: `p = (count(null_stat >= observed_stat) + 1) / (N + 1)`.

### Interpretation quadrant (2×2, locked)

| | best p < α | best p ≥ α |
|---|---|---|
| **system p < α** | `both_significant` | `system_only` |
| **system p ≥ α** | `best_only` ⚠️ | `neither` |

α = 0.05 (CLI flag `--alpha`). `best_only` is flagged **suspicious** with the fixed message:
`"possible selection artifact; system-level edge not established"`. The quadrant is a label in
the output — the numbers stand on their own.

### Output (JSON to stdout)

```json
{
  "horizon": 5,
  "n_permutations": 10000,
  "seed": 42,
  "alpha": 0.05,
  "sample_size": 110,
  "rows_excluded_null_strategy_from_C": 24,
  "overall": {
    "observed_hit_rate": 0.582,
    "null_mean": 0.501,
    "p_value": 0.031
  },
  "best_strategy": {
    "strategy": "momentum_breakout",
    "observed_hit_rate": 0.684,
    "n": 19,
    "null_max_mean": 0.612,
    "p_value": 0.118,
    "eligible_strategies": ["..."]
  },
  "per_strategy": [
    {"strategy": "...", "n": 19, "observed_hit_rate": 0.684, "eligible_for_best": true},
    {"strategy": "...", "n": 4, "observed_hit_rate": 0.75, "eligible_for_best": false}
  ],
  "interpretation": "system_only",
  "interpretation_note": null,
  "caveats": [
    "permutation assumes exchangeability; same-ticker repeat analyses and overlapping h5 windows are correlated, so p-values are likely optimistic",
    "n=110 total; per-strategy cells are small — treat C as a screen, not a verdict"
  ]
}
```

`interpretation_note` carries the suspicious message iff interpretation is `best_only`.
Degenerate inputs: A-sample empty → exit with a clear message, no JSON; eligible strategy set
empty → `best_strategy: null`, interpretation computed from A only (`system_only`/`neither`).
**Implementation contract (review fix): when the eligible set is empty the shuffle engine
SKIPS the C statistic entirely — `max()` over an empty strategy set is never evaluated, in
the observed pass or any permutation.** C is conditionally computed, not computed-then-dropped.

## Architecture

- `marketpulse/evaluation/permutation.py` — frozen dataclass `PermutationResult` + pure
  function `run_permutation_test(rows, *, n_permutations, seed, alpha) -> PermutationResult`
  (rows = list of `(subtype, excess_return, strategy|None)`; NO db access in the pure core) +
  thin `load_rows(db, *, horizon)` query helper. Pure core is what gets exhaustively tested.
- `marketpulse/cli/permutation_test.py` — repo CLI convention (`# Layer: cli`, manual
  `session_scope` generator driving), argparse flags `--horizon --permutations --seed --alpha`,
  prints `json.dumps(..., indent=2)`.
- `docs/CHARTER.md` — strategy-trust chain entry updated: priority recalibration
  **P1 Permutation > P2 Bootstrap CI > P3 Shadow 2a > P4 Walk-Forward** (walk-forward last:
  samples too thin to split); Evidence-Engine-vs-Research-Engine identity note; Research
  Sandbox + Promotion Gate (research layer may never touch North Star / production ledger)
  recorded as a future roadmap candidate.

## Error handling

Pure core raises `ValueError` on empty input / invalid params; CLI catches and prints a
human-readable line to stderr with exit 1. No partial JSON ever printed.

## Testing (`# Layer:` tags; `uv run pytest`)

1. **Exact small-N:** 3-4 rows where all permutations are enumerable by hand → p-value matches
   the exact enumeration within add-one convention.
2. **Perfect predictor:** all verdicts hit. NOTE (review fix): permutations CAN tie a perfect
   assignment when the shuffled label multiset happens to reproduce one, so the assertion is
   NOT `p == 1/(N+1)`. Correct contract: `p = (ties + 1) / (N + 1)` where ties = permutations
   matching the observed perfect hit rate; the fixture is constructed so ties are
   combinatorially rare (mixed verdict labels over distinct outcomes), and the test asserts
   `p < alpha`, not an exact value.
3. **Null calibration:** random verdicts vs random outcomes → p roughly uniform (sample a few
   seeds; assert p not concentrated below 0.05 — sanity, not strict distribution test).
4. **Hit-definition delegation:** monkeypatch `scoring._is_hit` → permutation module's results
   change accordingly (proves reuse, not reimplementation).
5. **Eligible-set freeze:** strategy with n=4 excluded from C in both observed and null, but
   PRESENT in `per_strategy` with `"eligible_for_best": false` (review fix: visibility without
   eligibility must be explicit — a reader seeing its 75% must not mistake it for a champion-
   test participant).
6. **Null-strategy rows:** count toward A, excluded from C, reported in
   `rows_excluded_null_strategy_from_C`.
7. **Determinism:** same seed → identical JSON; different seed → different null stats.
8. **Quadrant labels:** four synthetic cases produce the four labels; `best_only` carries the
   suspicious note.
9. **CLI smoke:** seeded tmp DB → valid JSON, exit 0; empty DB → stderr message, exit 1.

## Files touched

- `marketpulse/evaluation/permutation.py` — create
- `marketpulse/evaluation/scoring.py` — only if `_is_hit` needs a public alias (no logic change)
- `marketpulse/cli/permutation_test.py` — create
- `docs/CHARTER.md` — strategy-trust recalibration
- `tests/evaluation/test_permutation.py`, `tests/cli/test_permutation_cli.py` — create

No schema, no migration, no routes, no new dependencies, no network.
