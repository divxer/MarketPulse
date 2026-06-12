# Execution Evidence MVP — Independent Pricing Audit

> **Not Broker Shadow; no order routing, no fill reconciliation, no IBKR dependency.**

**Date:** 2026-06-12
**Status:** Approved (design locked)
**Charter link:** execution-trust chain, first evidence deliverable (lifecycle stage 2a,
"passive shadow"). Distinct from future 2b (broker shadow / execution tracking error) — this
audit uses an independent **market data source**, not a broker. Positioning is deliberately
restrained: this is Execution Trustworthiness evidence, not broker-readiness.

## Problem

The entire paper system — fill prices, daily NAV, the north-star metric — is priced from a
single vendor chain (yfinance → `price_cache`). Nobody has ever checked that chain against an
independent source. The question this MVP answers:

> **Is the North Star built on a price illusion?**

Two sub-questions, two legs:
1. **Fills (coarse sanity check):** are paper fill prices consistent with an independent
   vendor's view of the same closes? *Explicitly NOT an execution-quality verdict* — fills are
   close-priced by design (`price_provider.close_on_date`), and the comparison target is a
   daily close, not an execution-time quote.
2. **NAV (the main course):** if every recorded NAV day is re-priced with independent closes,
   how far does the NAV series drift? This directly measures single-vendor floor risk under
   the north-star.

## Pre-registered verdict thresholds (LOCKED before the first run)

| Leg | Metric | PASS |
|---|---|---|
| Fills | mean \|bps error\| (vs same-day close) | ≤ 25 bps |
| Fills | p95 \|bps error\| (vs same-day close) | ≤ 100 bps |
| Fills | hard anomaly (any single fill) | > 200 bps (= 2 × p95 threshold, review fix — keeps the anomaly bar a clean multiple of the distribution gate) → listed individually; any anomaly ⇒ fills leg FAIL |
| NAV | mean \|drift\| | ≤ 0.10% |
| NAV | max \|drift\| | ≤ 0.50% |

Output carries `verdict: {fills, nav, overall}` (overall = PASS iff both legs PASS) and echoes
the thresholds — the JSON is its own pre-registration proof. Fills thresholds are looser than
close-vs-close vendor noise on purpose: the legs compare different things (see leg semantics).
**No post-hoc reinterpretation: a FAIL is a FAIL and becomes a finding to investigate.**

## Scope (locked)

**In:** pure core `marketpulse/evaluation/pricing_audit.py` + thin DB loaders + Tencent
fetcher + `python -m marketpulse.cli.pricing_audit` (JSON to stdout). One-shot read-only
diagnostic. Zero schema, zero persistence, zero UI, zero new dependencies.
**Out:** broker data of any kind, recurring jobs, result storage, 2b execution tracking error,
automatic adjustment-basis correction, /lab surface.

## Design

### Independent source (locked)

`TencentClient.fetch_history(ticker)` — Usfqkline US daily bars, **qfq (forward-adjusted)**.
One fetch per ticker in (fill tickers ∪ NAV-day position tickers ∪ {SPY}). Bars carry
open/high/low/close, enabling both fill comparisons.

### Fills leg — coarse fill-price sanity check (downgraded framing, locked)

For each `paper_fill` (n≈32): trading date = `filled_at` converted to the NY trading date.
Two comparisons per fill, BOTH reported:

- **`vs_same_day_close` (primary — enters the verdict):** paper fills ARE close-on-date prices
  (with lookback fallback), so independent same-day close (last available ≤ fill date,
  mirroring the engine's lookback convention) is the apples-to-apples comparison.
  `bps = (paper_price − tencent_close) / tencent_close × 10⁴`.
- **`vs_next_available_open` (context only — never enters the verdict):** what the fill would
  look like against the next session's open; reported to make the close-fill assumption's
  real-world gap visible.

Fixed caveat string in output: *"fills audit compares paper fills to same-day close, not an
execution-time quote; it is a coarse sanity check, not a fill-quality verdict."*

### NAV leg — the main course

For each `paper_nav_snapshot` day: take recorded `cash_balance` (unchanged) and the open
positions as of that date (same as-of logic as `snapshot_runner._read_open_positions`);
re-price holdings MTM + the SPY leg with Tencent closes (last available ≤ trading_date —
mirrors the recorded NAV's mark-to-last-close convention).
`drift_pct = (nav_tencent − nav_recorded) / nav_recorded`.

Four aggregates (all reported; PASS gates on mean and max of |drift| only):
- `mean_abs_drift_pct`
- `max_abs_drift_pct` (with its date)
- `weighted_mean_abs_drift_pct` — weighted by recorded `holdings_mtm` (drift originates in
  the priced leg; near-cash days have almost no drift capacity and would otherwise dilute or
  amplify the plain mean). Reported for interpretation; not a gate in v1.
- `mean_signed_drift_pct` (review fix — NOT a gate): a persistent same-sign drift that stays
  under the abs gates (e.g. +0.09% every day) would PASS yet indicate a systematic offset —
  usually adjustment-basis, not noise. The signed mean makes that pattern visible instead of
  letting the abs gates launder it.

Tickers missing from Tencent entirely → that position keeps its recorded value for the
re-priced NAV, and the ticker is listed in `unpriceable_tickers` (visible degradation, never
silent).

### Adjustment-basis analysis (first-class output section, review fix)

Tencent qfq vs yfinance auto-adjust differ in adjustment convention. The audit does NOT
correct for this; it makes it diagnosable. **`adjustment_basis_analysis` is a top-level output
section** (not auxiliary metadata) — the most likely future FAIL is "复权口径不同", not
"价格错", so the diagnostic for it gets first-class status. Per ticker:

```json
{"ticker": "SPY", "n_dates": 10, "mean_signed_bps": -4.3, "same_sign_ratio": 0.92}
```

Reading: high `same_sign_ratio` (offsets consistently one direction) + non-trivial
`mean_signed_bps` ⇒ adjustment-basis/corporate-action divergence suspected; ratio near 0.5 ⇒
ordinary vendor noise. Fixed caveat in output. Tickers with a dividend/split inside the
window are the first alternative explanation for any FAIL.

### Output (JSON to stdout, shape sketch)

```json
{
  "generated_for": {"fills_n": 32, "nav_days": 10, "tickers_fetched": 14},
  "thresholds": { "...echoed..." },
  "fills": {
    "n": 32,
    "vs_same_day_close": {"mean_abs_bps": 8.7, "p95_abs_bps": 21.3},
    "vs_next_available_open": {"mean_abs_bps": 63.0, "p95_abs_bps": 180.2},
    "anomalies": [{"fill_id": 7, "ticker": "QBTS", "bps": 212.4}],
    "per_fill": [ "...fill_id/ticker/date/side/paper_price/tencent_close/bps..." ]
  },
  "nav": {
    "days": 10,
    "mean_abs_drift_pct": 0.04,
    "max_abs_drift_pct": 0.21,
    "max_drift_date": "2026-06-05",
    "weighted_mean_abs_drift_pct": 0.05,
    "mean_signed_drift_pct": 0.02,
    "per_day": [ "...date/nav_recorded/nav_tencent/drift_pct/spy_close_recorded/spy_close_tencent..." ],
    "unpriceable_tickers": []
  },
  "adjustment_basis_analysis": [
    {"ticker": "SPY", "n_dates": 10, "mean_signed_bps": -1.2, "same_sign_ratio": 0.6}
  ],
  "verdict": {"fills": "PASS", "nav": "PASS", "overall": "PASS"},
  "caveats": [ "...fills-vs-close framing...", "...qfq adjustment basis..." ]
}
```

Degenerate inputs: no fills AND no NAV days → stderr message, exit 1, no JSON. One leg empty →
that leg reported as `null` with verdict `"SKIPPED"`; overall gates on the remaining leg only.

## Architecture

- `marketpulse/evaluation/pricing_audit.py` — frozen dataclasses + pure
  `run_pricing_audit(fills, nav_days, bars_by_ticker, *, thresholds) -> PricingAuditResult`
  (NO db, NO network in the core) + thin loaders `load_fills(db)` / `load_nav_days(db)`
  (positions-as-of reuse from snapshot_runner's pattern).
- `marketpulse/cli/pricing_audit.py` — repo CLI convention; performs the Tencent fetches
  (network allowed at CLI tier, same precedent as finalize CLI), assembles inputs, prints JSON.
- Thresholds: module-level frozen `Thresholds` dataclass with the locked defaults; CLI flags
  may NOT override them in v1 (pre-registration means no runtime knob to weaken the gate).

## Error handling

Per-ticker Tencent failure → ticker enters `unpriceable_tickers` / fills for it enter
`unpriced_fills` count; audit completes with visible degradation. Total fetch failure → stderr
+ exit 1. Pure core raises ValueError on malformed inputs.

## Testing (`# Layer:` tags; `uv run pytest`)

1. Pure core, synthetic bars with hand-computed bps/drift → exact metric values.
2. PASS and FAIL on both legs (threshold boundary cases; anomaly > 150 bps ⇒ fills FAIL and
   listed).
3. `vs_next_available_open` never affects the verdict (set it absurdly high → still PASS).
4. NAV lookback convention: missing bar on trading_date → previous close used (mirror test).
5. Unpriceable ticker → recorded value kept, listed, not silent; leg still computes.
6. Signed per-ticker offset: constructed systematic +N bps offset → reported with sign.
7. One-leg-empty → SKIPPED verdict semantics; both-empty → ValueError (CLI: exit 1).
8. CLI smoke with mocked TencentClient → valid JSON; thresholds echoed; no override flags.

## Files touched

- `marketpulse/evaluation/pricing_audit.py` — create
- `marketpulse/cli/pricing_audit.py` — create
- `docs/CHARTER.md` — execution-trust chain entry: link this audit as the 2a deliverable
  (one pointer line; results stay in the run output, NOT pasted into the charter fact layer
  unless later promoted)
- `tests/evaluation/test_pricing_audit.py`, `tests/cli/test_pricing_audit_cli.py` — create

No schema, no migration, no routes, no new dependencies.
