# Phase 8a Design Review — Background Materials

**Date:** 2026-06-12
**Status:** Background material for the future 8a design review. **Nothing in this document is
a locked fact** — charter-level facts live in `docs/CHARTER.md` (the 2026-06-12 permutation A
result and the pre-registered acceptance criterion). This document holds diagnostics and
working hypotheses at the evidence tier BELOW locked findings: best current explanations,
explicitly replaceable by better ones.

## Diagnostic Findings (2026-06-12)

Method: per-strategy permutation diagnostic against the global-shuffle null (fix outcomes,
permute all verdict labels, score each strategy's index subset), seed=42, N=10,000, two-sided
p reported. Run read-only against prod (110 h5 / 190 h1 resolved outcomes). **Multiple-
comparison context: 5 strategies × 2 horizons × 2 sides = 20 simultaneous p-values — single
small p-values below are screens, not verdicts.** Exchangeability caveat applies throughout
(same-ticker repeats, overlapping windows).

### Finding 1 — news_event verdicts are 83% neutral
h5 sample: n=35, label mix **neutral 29 / bullish 3 / bearish 3**. The strategy is rarely
making a directional call at all.

### Finding 2 — news_event h5 hit rate is significantly BELOW random
Observed 5.7% vs global-shuffle null 24.2%, **p_worse = 0.0022** — survives even a crude
Bonferroni ×20 (≈0.044). The only robust statistical signal in the entire dataset. At h1 the
effect disappears (32.0% vs 34.7% null) — consistent with ±1% being holdable for 1 day but
not 5 on news-driven names.

### Supporting context (h5)
| strategy | n | label mix | observed | null | p_better | p_worse |
|---|---|---|---|---|---|---|
| news_event | 35 | 29 neu / 3 bull / 3 bear | 5.7% | 24.2% | 1.00 | **0.0022** |
| momentum_breakout | 8 | 5 bull / 3 neu | 50.0% | 26.7% | 0.128 | 0.97 |
| general | 48 | balanced | 33.3% | 29.3% | 0.30 | 0.80 |
| sector_rotation | 4 | 2 bear / 2 bull | 0% | 42.2% | 1.00 | 0.10 |
| (none) | 15 | balanced | 33.3% | 30.3% | 0.50 | 0.72 |

Bucketing: **no detectable information** — general, (none). **Possibly informative,
sample-starved** — momentum_breakout only (h5 p=0.128 at n=8; absent at h1). **Robust
negative information** — news_event at h5.

## Working Hypothesis (status: HYPOTHESIS — not established fact)

> **Neutral overuse may be systematically harmful for high-volatility news universes.**
> Chain: news → LLM → uncertain → defaults to "neutral" → neutral hit requires
> |excess| ≤ 1% over 5 trading days → news-driven names almost never stay inside that band
> → systematic miss.

**Alternative explanations that produce the same observable** (any could displace the leading
hypothesis; none are excluded by current data):
- news-universe selection bias (which tickers get news_event events at all);
- ±1% hit-definition mismatch (threshold calibrated for the wrong volatility regime);
- news_event events correlate with a high-volatility regime independent of verdict quality.

## Key observation for the 8a review

**The visible pathology is decision calibration, not feature insufficiency.** The failure
shape is "LLM uncertain → neutral", not "LLM lacks inputs to decide". These imply radically
different remedies with radically different costs:

- Feature path (8a as specced): ML features, snapshot store, v5 prompt, A/B — heavy.
- Calibration path (cheap experiment, sketch): redesign the verdict contract —
  e.g. `direction + confidence` instead of three classes; `confidence < threshold` →
  **no verdict recorded** (abstention) instead of "neutral"; forbid neutral-as-default in the
  prompt. Could be a prompt-version bump plus parser change.

The 8a design review should evaluate the calibration path FIRST (or as a v5-arm variant inside
the same pre-registered A/B), since it may capture most of the available improvement at a
fraction of 8a's cost. The pre-registered acceptance criterion (CHARTER: permutation A,
`p_system < 0.05` on the new arm's own h5 sample) applies to whichever path runs.

## What this does NOT change

- Priority order stands: **Shadow 2a precedes the 8a design review** (this diagnostic narrowed
  8a's problem definition but produced no must-code-now project; Shadow 2a produces the first
  execution-tracking-error data, advancing a chain with no deliverable yet).
- The charter-level conclusion stands unmodified: no statistical evidence of system edge
  (p_system=0.859 at h5).
