# Permutation Test MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One CLI run prints two permutation p-values — A (system verdict edge) and C
(best-strategy, selection-corrected) — plus a 2×2 interpretation, from existing h5 data.

**Architecture:** Pure computation core (`evaluation/permutation.py`, no DB access) + thin
query helper + CLI. Zero schema/routes/network/deps; stdlib `random` only.

**Tech stack:** Python 3.12. Tests `uv run pytest`, lint `uv run ruff check`, `# Layer:` tags.

**Spec:** `docs/superpowers/specs/2026-06-12-permutation-test-mvp-design.md` (locked).
**Branch:** `feat/permutation-test-mvp` (created; spec committed on it).

Verified facts (do not rediscover):
- Hit authority: `marketpulse/evaluation/scoring.py:45` `_is_hit(subtype, excess) -> bool`;
  verdict constants `AIVerdict.BULLISH/BEARISH/NEUTRAL` in `marketpulse/evaluation/constants.py`.
- Strategy lives in `json_extract(EvaluationEvent.payload, '$.strategy')`; the query shape to
  copy is `scoring.compute_hit_rate` (scoring.py:56 — join EvaluationOutcome on event_id,
  filter event_type/horizon).
- CLI convention: `marketpulse/cli/refresh_sectors.py` (`# Layer: cli` line 1, manual
  `gen = session_scope(); db = next(gen)` driving — verified generator, do NOT use `with`).
- `db_session` fixture: tests/conftest.py:66. `tests/evaluation/` and `tests/cli/` exist.
- Statistical contract (spec-locked): add-one p-values; eligible set = strategy non-null AND
  n≥5, frozen from REAL data; C skipped entirely when eligible set empty; per_strategy carries
  `eligible_for_best`; perfect-predictor test must be tie-aware.

---

### Task 1: Pure core — `run_permutation_test`

**Files:**
- Create: `marketpulse/evaluation/permutation.py`
- Test: `tests/evaluation/test_permutation.py`

- [ ] **Step 1: Write the failing tests**

```python
# Layer: unit
"""Permutation test core — A (system edge) + C (best-of-N, selection-corrected)."""
from __future__ import annotations

import pytest

from marketpulse.evaluation.permutation import (
    MIN_STRATEGY_N,
    run_permutation_test,
)

# Row = (subtype, excess_return, strategy | None)


def test_three_row_known_null_fraction():
    """3 rows, each verdict hits ONLY its own outcome → exactly 1 of 3! label
    orderings is perfect, so the null fraction >= observed converges to 1/6.
    Seeded run must land near 1/6 (deterministic given seed; tolerance covers
    Monte-Carlo error at N=12000)."""
    rows = [
        ("bullish", 0.05, None),    # hit iff excess > 0.01
        ("bearish", -0.05, None),   # hit iff excess < -0.01
        ("neutral", 0.0, None),     # hit iff |excess| <= 0.01
    ]
    r = run_permutation_test(rows, n_permutations=12_000, seed=42)
    assert r.overall_observed_hit_rate == 1.0
    assert 0.14 < r.overall_p_value < 0.20  # ≈ 1/6 with add-one estimator


def test_perfect_predictor_ties_are_rare_p_small():
    """Tie-aware perfect-predictor contract (spec review fix): with 10 distinct
    outcomes and a 4/3/3 verdict mix, only 4!*3!*3!/10! of orderings tie the
    perfect assignment → p must be < alpha, NOT exactly 1/(N+1)."""
    rows = (
        [("bullish", 0.02 + i / 100, None) for i in range(4)]
        + [("bearish", -0.02 - i / 100, None) for i in range(3)]
        + [("neutral", -0.005 + i / 200, None) for i in range(3)]
    )
    r = run_permutation_test(rows, n_permutations=10_000, seed=42)
    assert r.overall_observed_hit_rate == 1.0
    assert r.overall_p_value < 0.05


def test_hit_definition_is_delegated(monkeypatch):
    """Proves reuse of scoring._is_hit, not a reimplementation."""
    import marketpulse.evaluation.permutation as perm
    monkeypatch.setattr(perm, "_is_hit", lambda subtype, excess: True)
    rows = [("bullish", -0.5, None), ("bearish", 0.5, None)]
    r = run_permutation_test(rows, n_permutations=100, seed=1)
    assert r.overall_observed_hit_rate == 1.0  # everything "hits" under the patch


def test_eligible_set_freeze_and_flag():
    """n=4 strategy: in per_strategy with eligible_for_best=False; never in C."""
    rows = (
        [("bullish", 0.05, "big") for _ in range(5)]
        + [("bullish", 0.05, "small") for _ in range(4)]
    )
    r = run_permutation_test(rows, n_permutations=200, seed=7)
    flags = {s.strategy: s.eligible_for_best for s in r.per_strategy}
    assert flags == {"big": True, "small": False}
    assert r.eligible_strategies == ("big",)
    assert r.best_strategy == "big"
    ns = {s.strategy: s.n for s in r.per_strategy}
    assert ns == {"big": 5, "small": 4}
    assert MIN_STRATEGY_N == 5


def test_null_strategy_rows_count_in_a_excluded_from_c():
    rows = [("bullish", 0.05, None)] * 6 + [("bullish", 0.05, "s1")] * 5
    r = run_permutation_test(rows, n_permutations=100, seed=3)
    assert r.sample_size == 11                       # A counts all
    assert r.rows_excluded_null_strategy_from_c == 6
    assert r.eligible_strategies == ("s1",)


def test_empty_eligible_set_skips_c():
    """Spec review fix: C is conditionally computed — best_strategy is None and
    interpretation derives from A only. (max over empty set must never run —
    if it did, this test would crash.)"""
    rows = [("bullish", 0.05, None), ("bearish", -0.05, "tiny")]  # tiny has n=1
    r = run_permutation_test(rows, n_permutations=100, seed=5)
    assert r.best_strategy is None
    assert r.best_p_value is None
    assert r.interpretation in ("system_only", "neither")


def test_determinism_same_seed_same_result():
    rows = [("bullish", 0.05, "a")] * 5 + [("bearish", 0.05, "b")] * 5
    r1 = run_permutation_test(rows, n_permutations=500, seed=42)
    r2 = run_permutation_test(rows, n_permutations=500, seed=42)
    assert r1 == r2
    r3 = run_permutation_test(rows, n_permutations=500, seed=43)
    assert r3.overall_null_mean != r1.overall_null_mean


def test_quadrant_labels_and_suspicious_note():
    """Four synthetic cases → four labels; best_only carries the fixed note."""
    # neither: pure noise
    noise = [("bullish", 0.05, "a"), ("bullish", -0.05, "a"),
             ("bearish", 0.05, "a"), ("bearish", -0.05, "a")] * 3
    r = run_permutation_test(noise, n_permutations=2_000, seed=11)
    assert r.interpretation == "neither"
    assert r.interpretation_note is None
    # both_significant: one strategy, strongly predictive, decent n
    strong = (
        [("bullish", 0.05 + i / 100, "a") for i in range(10)]
        + [("bearish", -0.05 - i / 100, "a") for i in range(10)]
    )
    r = run_permutation_test(strong, n_permutations=2_000, seed=11)
    assert r.interpretation == "both_significant"
    # best_only / system_only are harder to synthesize deterministically:
    # construct via direct quadrant check instead — call the (public) label
    # helper with the four (p_system, p_best) combinations.
    from marketpulse.evaluation.permutation import interpret_quadrant
    assert interpret_quadrant(0.01, 0.01, alpha=0.05)[0] == "both_significant"
    assert interpret_quadrant(0.01, 0.50, alpha=0.05)[0] == "system_only"
    label, note = interpret_quadrant(0.50, 0.01, alpha=0.05)
    assert label == "best_only"
    assert note == "possible selection artifact; system-level edge not established"
    assert interpret_quadrant(0.50, 0.50, alpha=0.05)[0] == "neither"


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        run_permutation_test([], n_permutations=10, seed=1)
    with pytest.raises(ValueError):
        run_permutation_test([("bullish", 0.05, None)], n_permutations=0, seed=1)
    with pytest.raises(ValueError):
        run_permutation_test([("bullish", 0.05, None)], n_permutations=10, seed=1, alpha=1.5)
```

- [ ] **Step 2: Run, verify FAIL** — `uv run pytest tests/evaluation/test_permutation.py -q`
  (ModuleNotFoundError).

- [ ] **Step 3: Implement `marketpulse/evaluation/permutation.py`**

```python
"""Permutation test — strategy-trust chain item 1 (spec 2026-06-12).

A: are the system's verdicts better than random verdict↔outcome pairing?
C: is the best strategy better than a randomly-selected champion (best-of-N)?
One shuffle engine, two statistics. Pure computation — no DB access here.

Statistical contracts (spec-locked):
- hit = scoring._is_hit (single scoring authority; never reimplement).
- p = (count(null >= observed) + 1) / (N + 1), one-sided.
- C's eligible set (strategy non-null, n >= MIN_STRATEGY_N) is frozen from
  REAL data; every permutation takes max over that fixed set; when the set
  is empty C is SKIPPED entirely (max over an empty set never evaluated).
- Caveat (printed, not hidden): permutation assumes exchangeability; repeat
  same-ticker analyses and overlapping h5 windows correlate, so p-values
  are likely optimistic.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from marketpulse.evaluation.scoring import _is_hit

MIN_STRATEGY_N = 5
SUSPICIOUS_NOTE = "possible selection artifact; system-level edge not established"
CAVEATS = (
    "permutation assumes exchangeability; same-ticker repeat analyses and "
    "overlapping h5 windows are correlated, so p-values are likely optimistic",
    "small sample; per-strategy cells are tiny - treat C as a screen, not a verdict",
)

Row = tuple[str, float, str | None]  # (subtype, excess_return, strategy)


@dataclass(frozen=True)
class StrategyStat:
    strategy: str
    n: int
    observed_hit_rate: float
    eligible_for_best: bool


@dataclass(frozen=True)
class PermutationResult:
    n_permutations: int
    seed: int
    alpha: float
    sample_size: int
    rows_excluded_null_strategy_from_c: int
    overall_observed_hit_rate: float
    overall_null_mean: float
    overall_p_value: float
    best_strategy: str | None
    best_n: int | None
    best_observed_hit_rate: float | None
    best_null_max_mean: float | None
    best_p_value: float | None
    eligible_strategies: tuple[str, ...]
    per_strategy: tuple[StrategyStat, ...]
    interpretation: str
    interpretation_note: str | None
    caveats: tuple[str, ...] = CAVEATS


def interpret_quadrant(
    p_system: float, p_best: float | None, *, alpha: float,
) -> tuple[str, str | None]:
    """2x2 interpretation (spec-locked labels). p_best None => A-only collapse."""
    sys_sig = p_system < alpha
    if p_best is None:
        return ("system_only" if sys_sig else "neither"), None
    best_sig = p_best < alpha
    if sys_sig and best_sig:
        return "both_significant", None
    if sys_sig:
        return "system_only", None
    if best_sig:
        return "best_only", SUSPICIOUS_NOTE
    return "neither", None


def _hit_rate(subtypes: list[str], excesses: list[float], idx: list[int]) -> float:
    hits = sum(1 for i in idx if _is_hit(subtypes[i], excesses[i]))
    return hits / len(idx)


def run_permutation_test(
    rows: list[Row],
    *,
    n_permutations: int = 10_000,
    seed: int = 42,
    alpha: float = 0.05,
) -> PermutationResult:
    if not rows:
        raise ValueError("no rows - A-sample is empty")
    if n_permutations < 1:
        raise ValueError("n_permutations must be >= 1")
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha must be in (0, 1)")

    subtypes = [r[0] for r in rows]
    excesses = [r[1] for r in rows]
    strategies = [r[2] for r in rows]
    all_idx = list(range(len(rows)))

    idx_by_strategy: dict[str, list[int]] = {}
    for i, s in enumerate(strategies):
        if s is not None:
            idx_by_strategy.setdefault(s, []).append(i)
    excluded_from_c = sum(1 for s in strategies if s is None)

    # Eligible set FROZEN from real data (spec-locked; per-strategy n is
    # invariant under subtype shuffles anyway - explicit freeze is defensive).
    eligible = tuple(sorted(
        s for s, idx in idx_by_strategy.items() if len(idx) >= MIN_STRATEGY_N
    ))

    observed_overall = _hit_rate(subtypes, excesses, all_idx)
    per_strategy = tuple(
        StrategyStat(
            strategy=s,
            n=len(idx),
            observed_hit_rate=_hit_rate(subtypes, excesses, idx),
            eligible_for_best=s in eligible,
        )
        for s, idx in sorted(idx_by_strategy.items())
    )

    best_strategy = best_n = best_observed = None
    if eligible:
        # Deterministic argmax: highest rate, ties broken by name.
        best_strategy = max(
            eligible,
            key=lambda s: (_hit_rate(subtypes, excesses, idx_by_strategy[s]), s),
        )
        best_n = len(idx_by_strategy[best_strategy])
        best_observed = _hit_rate(subtypes, excesses, idx_by_strategy[best_strategy])

    rng = random.Random(seed)
    labels = list(subtypes)
    ge_overall = 0
    null_overall_sum = 0.0
    ge_best = 0
    null_max_sum = 0.0
    for _ in range(n_permutations):
        rng.shuffle(labels)
        null_overall = _hit_rate(labels, excesses, all_idx)
        null_overall_sum += null_overall
        if null_overall >= observed_overall:
            ge_overall += 1
        if eligible:  # C skipped entirely when no eligible strategies
            null_max = max(
                _hit_rate(labels, excesses, idx_by_strategy[s]) for s in eligible
            )
            null_max_sum += null_max
            if null_max >= best_observed:
                ge_best += 1

    p_overall = (ge_overall + 1) / (n_permutations + 1)
    p_best = (ge_best + 1) / (n_permutations + 1) if eligible else None
    interpretation, note = interpret_quadrant(p_overall, p_best, alpha=alpha)

    return PermutationResult(
        n_permutations=n_permutations,
        seed=seed,
        alpha=alpha,
        sample_size=len(rows),
        rows_excluded_null_strategy_from_c=excluded_from_c,
        overall_observed_hit_rate=observed_overall,
        overall_null_mean=null_overall_sum / n_permutations,
        overall_p_value=p_overall,
        best_strategy=best_strategy,
        best_n=best_n,
        best_observed_hit_rate=best_observed,
        best_null_max_mean=(null_max_sum / n_permutations) if eligible else None,
        best_p_value=p_best,
        eligible_strategies=eligible,
        per_strategy=per_strategy,
        interpretation=interpretation,
        interpretation_note=note,
    )
```

- [ ] **Step 4: Run, verify PASS** — all 9 tests. `uv run ruff check` clean (adjust noqa only).

- [ ] **Step 5: Commit**

```bash
git add marketpulse/evaluation/permutation.py tests/evaluation/test_permutation.py
git commit -m "feat(evaluation): permutation test core — A+C dual statistics (PT-T1)"
```

---

### Task 2: Query helper `load_rows`

**Files:**
- Modify: `marketpulse/evaluation/permutation.py` (append)
- Test: append to `tests/evaluation/test_permutation.py`

- [ ] **Step 1: Failing test** (uses `db_session`; seed via the same helpers the existing
  scoring tests use — read `tests/evaluation/` first and copy their event/outcome seeding):

```python
def test_load_rows_filters_and_strategy_extraction(db_session):
    # Seed (copy the existing scoring-test seeding helpers):
    #  - h5 event+outcome WITH payload.strategy="s1"        -> included, strategy "s1"
    #  - h5 event+outcome WITHOUT strategy in payload        -> included, strategy None
    #  - h1 event+outcome                                    -> excluded (horizon)
    #  - h5 event with NULL excess_return outcome (if the
    #    schema permits) -> excluded; otherwise skip this row
    from marketpulse.evaluation.permutation import load_rows
    rows = load_rows(db_session, horizon=5)
    assert all(len(r) == 3 for r in rows)
    strategies = {r[2] for r in rows}
    assert "s1" in strategies and None in strategies
```

- [ ] **Step 2: FAIL** (load_rows missing).

- [ ] **Step 3: Implement** (append; mirrors `scoring.compute_hit_rate`'s query shape):

```python
def load_rows(db, *, horizon: int = 5) -> list[Row]:
    """A-sample per spec: ai_analysis events at `horizon`, subtype and
    excess_return non-null; strategy (payload.$.strategy) may be null."""
    from sqlalchemy import func, select

    from marketpulse.db.models import EvaluationEvent, EvaluationOutcome

    stmt = (
        select(
            EvaluationEvent.subtype,
            EvaluationOutcome.excess_return,
            func.json_extract(EvaluationEvent.payload, "$.strategy"),
        )
        .join(EvaluationOutcome, EvaluationOutcome.event_id == EvaluationEvent.id)
        .where(EvaluationEvent.event_type == "ai_analysis")
        .where(EvaluationOutcome.horizon_trading_days == horizon)
        .where(EvaluationEvent.subtype.is_not(None))
        .where(EvaluationOutcome.excess_return.is_not(None))
    )
    return [(s, float(e), st) for s, e, st in db.execute(stmt).all()]
```

(`excess_return` may round-trip as Decimal — the `float()` cast is deliberate; verify against
the actual column type and keep `_is_hit`'s float comparison semantics.)

- [ ] **Step 4: PASS + full suite + ruff.**

- [ ] **Step 5: Commit** — `feat(evaluation): permutation load_rows query helper (PT-T2)`.

---

### Task 3: CLI

**Files:**
- Create: `marketpulse/cli/permutation_test.py`
- Test: `tests/cli/test_permutation_cli.py`

- [ ] **Step 1: Failing tests**

```python
# Layer: cli
"""CLI smoke for python -m marketpulse.cli.permutation_test."""
# Test 1: seeded db (reuse Task 2's seeding) -> main() prints valid JSON with
#   keys {"overall", "best_strategy", "interpretation", "caveats"}; exit 0.
#   Invoke main(argv=[...]) directly with DATABASE_URL pointed at db_url
#   (monkeypatch.setenv + get_settings.cache_clear(), the repo's standard
#   pattern), capsys captures stdout, json.loads succeeds.
# Test 2: empty db -> SystemExit(1), stderr contains "no rows", stdout has NO JSON.
# Test 3: --seed 1 vs --seed 2 produce different overall.null_mean; same seed twice identical.
```

- [ ] **Step 2: FAIL.**

- [ ] **Step 3: Implement** (CLI convention; argparse; `to_dict` via `dataclasses.asdict`):

```python
# Layer: cli
"""Permutation test: python -m marketpulse.cli.permutation_test [--horizon 5]
[--permutations 10000] [--seed 42] [--alpha 0.05]"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import sys

from marketpulse.db.base import session_scope
from marketpulse.evaluation.permutation import load_rows, run_permutation_test


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--permutations", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--alpha", type=float, default=0.05)
    args = ap.parse_args(argv)

    gen = session_scope()
    db = next(gen)
    try:
        rows = load_rows(db, horizon=args.horizon)
        if not rows:
            print(f"no rows: no resolved h{args.horizon} outcomes yet", file=sys.stderr)
            raise SystemExit(1)
        result = run_permutation_test(
            rows,
            n_permutations=args.permutations,
            seed=args.seed,
            alpha=args.alpha,
        )
        out = {"horizon": args.horizon, **dataclasses.asdict(result)}
        print(json.dumps(out, indent=2))
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: PASS + full suite + ruff.**

- [ ] **Step 5: Commit** — `feat(cli): permutation_test CLI — JSON A+C report (PT-T3)`.

---

### Task 4: CHARTER recalibration + final integration

**Files:**
- Modify: `docs/CHARTER.md` (strategy-trust chain entry, item 1 of the evidence chain)

- [ ] **Step 1:** Append to the strategy-trust item:

```markdown
**Recalibrated 2026-06-12 (ROI order, locked):** P1 permutation test (shipped — the
`permutation_test` CLI answers "is there edge at all" with selection-corrected best-of-N) >
P2 bootstrap CI > P3 shadow 2a > P4 walk-forward (last: current samples too thin to split).
Identity note: MarketPulse is an **Evidence Engine** ("is this worth believing?"), not a
Research Engine ("what else is worth trying?") — statistical validation completes our chain,
it does not make us Vibe-Trading. **Future roadmap candidate:** a Research Sandbox with a
Promotion Gate (fast experiment layer that may NEVER touch the North Star or the production
ledger; promotion into the evidence layer is explicit).
```

- [ ] **Step 2:** `uv run pytest -q` full suite green; `uv run ruff check` clean.

- [ ] **Step 3:** Run the CLI against a copy of prod data is NOT a plan task — first real run
  happens post-merge in the container (deploy step below).

- [ ] **Step 4: Commit** — `docs(charter): strategy-trust recalibration — permutation shipped, ROI order, Evidence Engine identity (PT-T4)`.

---

## Post-merge run (operator step)

```bash
docker exec marketpulse /app/.venv/bin/python -m marketpulse.cli.permutation_test
```

First real answer to "do we have edge?" — paste the JSON into the session for interpretation.
