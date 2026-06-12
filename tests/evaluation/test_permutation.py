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
    # Mixed excess signs so the overall null statistic actually varies under
    # shuffling (with a single shared excess value it is permutation-invariant
    # and the cross-seed inequality below could never hold).
    rows = [("bullish", 0.05, "a")] * 5 + [("bearish", -0.05, "b")] * 5
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
