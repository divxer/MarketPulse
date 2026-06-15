# Research Sandbox 1.1 — Swarm Verdict-Collection Hardening — Backlog (seed)

**Date:** 2026-06-15
**Status:** **NOT STARTED** — backlog / charter-seed only. To be chartered through the normal
brainstorm → spec → plan before any implementation. Recorded now so the prioritized risks
surfaced during RS-1 are not lost.
**Relation to RS-1:** [Research Sandbox 1](2026-06-12-research-sandbox-1-swarm-research-design.md)
(COMPLETE) proved a swarm verdict can be *produced, parsed, and entered* into the evaluation
system. RS-1.1 hardens *how verdicts are collected* so the accruing sample is trustworthy.
Two different problems — keep them separate.

## Charter principle (locked intent)

> **Research outcomes must be anchored to final prices. Provisional prices are not eligible
> for event creation.**

Rationale: RS-1.1 exists to feed a pre-registered permutation test (`p < 0.05?`). Any
inconsistency in the *event definition* is a first-class statistical risk, ranked above
engineering convenience.

## Prioritized backlog

### P0 — Final Price Enforcement  ← statistical validity

**Problem (observed in RS-1, 2026-06-15):** two validation runs for AAPL on the same `as_of`
recorded different `event_price` — **291.13** and **296.33** — because
`YFinancePriceProvider.close_on_date` fetches yfinance *live* and does **not** filter
`is_final`, despite the CLI comment claiming "resolves last-final-close ≤ as_of (post-P2F)"
(`marketpulse/cli/run_swarm_research.py:125`). A provisional (intraday, still-moving) close
became the entry price for the h5 outcome.

**Rule:** swarm_research `event_price` MUST come from an `is_final=True` bar. If the resolved
close for `as_of` is not final → **abstain** (record no event), never write an event on a
provisional price.

**Why P0:** MarketPulse already paid this lesson once — provisional SPY contaminated NAV →
Execution Audit FAIL → the `is_final` / `finalize_provisional_bars` governance was built. The
NAV / snapshot path trusts only `is_final==True` bars (`portfolio/snapshot_runner.py`).
Letting the swarm path accept provisional prices re-digs the exact same hole, and it directly
determines whether the eventual ≥30-sample permutation result can be trusted.

### P1 — Async Finalizer  ← engineering robustness

POST run → persist `run_id` → exit; a separate background job polls completed runs and writes
the event (Vibe retains runs; `event_time` = `as_of` EOD, independent of run-time). Removes
the synchronous-poll timeout fragility — swarm runtime measured **16.5 / 18.4 / 27.5 min** for
the same single-ticker DAG, so a fixed timeout is inherently brittle; `2400s` is only a stopgap
(PR #163).

### P2 — Provenance Accuracy

Make Vibe's `/settings/llm` read `os.environ` first (then fall back to the `.env` file) so
`provenance.backend` is always correct. Interim mitigation already in place (2026-06-15): a
read-only bind-mount of `agent/.env` into the Vibe container makes the display + provenance
correct durably; the clean fix is the precedence change in Vibe.

### P3 — Cost Optimization

DeepSeek-direct vs OpenRouter (base per-token ≈ equal; direct context-caching is the real
lever), and model tier Pro vs Flash (~5× difference). Affects spend / throughput, **not**
statistical validity — lowest priority.

## Priority rationale

| Item | What it protects |
|---|---|
| **P0 Final Price** | **statistical validity** — whether the samples can be believed |
| P1 Async / P2 Provenance / P3 Cost | engineering experience — robustness, accuracy of labels, spend |

Validity outranks experience for a measure-first evidence engine. `$0.5 vs $0.2 per ticker`
is strictly secondary to "is the entry price the same number every time we ask?".
