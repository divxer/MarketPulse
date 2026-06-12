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
        # Deterministic argmax: highest rate, ties broken by SMALLEST name
        # (review fix — max(key=(rate, s)) would pick the alphabetically
        # LARGEST name on ties; ascending-name tie-break is the convention).
        best_strategy = sorted(
            eligible,
            key=lambda s: (-_hit_rate(subtypes, excesses, idx_by_strategy[s]), s),
        )[0]
        best_n = len(idx_by_strategy[best_strategy])
        best_observed = _hit_rate(subtypes, excesses, idx_by_strategy[best_strategy])

    rng = random.Random(seed)
    ge_overall = 0
    null_overall_sum = 0.0
    ge_best = 0
    null_max_sum = 0.0
    for _ in range(n_permutations):
        # Review-locked: copy from the ORIGINAL labels each round, then
        # shuffle — chained in-place shuffles are statistically uniform too,
        # but per-round copies are easier to read and audit.
        labels = list(subtypes)
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
    # excess_return may round-trip as Decimal; float() keeps _is_hit semantics.
    return [(s, float(e), st) for s, e, st in db.execute(stmt).all()]
