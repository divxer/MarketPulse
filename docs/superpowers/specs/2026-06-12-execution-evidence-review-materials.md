# Execution Evidence MVP — Run #1 Review Materials

**Date:** 2026-06-12
**Status:** Review material, same tier as `2026-06-12-phase-8a-design-review-materials.md`.
**Nothing in this document is a locked fact** except the run verdict itself (recorded at the
fact level in `docs/CHARTER.md`). Everything else here is best-current-explanation or known
defect, explicitly replaceable by better evidence.

## Locked Fact

**Execution Evidence MVP run #1 (2026-06-12, prod, pre-registered gates): overall = FAIL**
(fills FAIL: mean 31.2 bps > 25, one anomaly −596.1 bps > 200; NAV FAIL: mean |drift| 0.298% >
0.10%, max 2.243% on 2026-06-03 > 0.50%). Findings identified; follow-up required. Per the
pre-registration protocol the FAIL stands as recorded — the material below investigates it,
it does not reinterpret it.

## Strong Finding — Historical Price Revision Detected

The single fills-leg anomaly has a complete evidence chain:

```
paper fill #5      AMSC ENTRY 2026-05-28 @ 48.27   (frozen at fill time from yfinance)
yfinance today     AMSC 2026-05-28 close = 51.33   (price_cache, refetched final 2026-06-12)
Tencent            AMSC 2026-05-28 close = 51.33   (independent vendor)
```

Both vendors NOW agree. The fill price matches NEITHER. This is not vendor disagreement —
it is **vendor revision over time**: yfinance's AMSC 2026-05-28 close changed from 48.27 to
51.33 — a ~6% upward revision — at some point after the fill froze the then-current value. The paper ledger holds
an entry price no vendor currently endorses; that position's P&L rests on it.

This is the first production hit of the previously-theorized provenance gap
(`correction_version` / `source_revision` — Data Freshness & Provenance Layer discussion).
Removing this one fill, the fills leg would PASS comfortably (mean 11.8 bps).

**Reframe (the run's most important output):** the audit was designed to measure
*single-vendor floor risk* (Tencent vs yfinance). It instead surfaced **historical
reproducibility risk** (yfinance-then vs yfinance-now) — a deeper problem in the same family
as `is_final`. The governance chain now reads: Freshness → Finality (implemented) →
Provenance (emerging) → **Revision Tracking (first observed in production today)**.

## Blocking Defect — Tencent Coverage Bug (engineering, not research)

`TencentClient.fetch_history` returns **1 bar** (latest day only) for BAC, CVX, QBTS but 43
bars for AMSC — a market-suffix resolution defect in the client, not missing listings.
Consequence: 7 of 10 NAV audit days were **unverifiable** (positions in those tickers had no
historical Tencent bars → day kept recorded value → drift 0.000 by construction, NOT verified
clean). **These days were effectively excluded from verification coverage — a 0.000 drift on
them is absence of measurement, not evidence of agreement.** Effective verified days: 3. Fix tracked as a standalone task (chip
"Fix TencentClient 1-bar history for some tickers"); the audit should be re-run after the fix.

## Working Hypothesis — NAV drift may be dominated by the AMSC revision cluster

The three verifiable drift days (06-02 +0.41%, 06-03 −2.24%, 06-04 +0.33%) bracket AMSC's
high-volatility window, and recorded NAV snapshots froze MTM values from fetch-time yfinance
bars that may since have been revised (same mechanism as the Strong Finding). Time-correlation
only; not yet decomposed per-ticker per-day. Status: **hypothesis, not established** —
alternative explanations (genuine vendor divergence on those dates, other tickers'
contributions) are not excluded. **Confidence: low-to-moderate** — the basis is time
correlation, not attribution decomposition. Decomposition belongs to the post-fix re-run.

Supporting context: adjustment_basis_analysis shows no systematic qfq-vs-auto-adjust pattern —
all non-AMSC tickers have small (≤20 bps) signed offsets with scattered signs. The originally
feared adjustment-basis hazard did NOT materialize in this window.

## Future Design Implications (backlog candidates, low priority — recorded, not scheduled)

- **Price Revision Audit / Source Revision Tracking** (suggested priority: tail of the queue,
  ~P7): periodically re-fetch historical bars and diff against `price_cache` to detect
  upstream revisions; today's AMSC case is the first real specimen. Unlike the Tencent bug
  (fixed once, gone), revision risk is systemic — today AMSC, tomorrow possibly SPY/QBTS/TQQQ.
- Provenance fields on the price layer (`source_revision` / `correction_version`) when the
  Data Freshness & Provenance Layer next evolves — design issue, not a current work item.

## Open Question — Ledger Immutability vs Historical Revisions

Today exposed a structural tension that the revision-tracking idea alone does not resolve:
**the paper ledger is immutable by design, but vendor prices revise over time.**

> How should historical price revisions interact with the paper ledger's immutability
> principle? If a fill was executed using information available at time T, should later
> vendor revisions retroactively change the evaluation baseline, or merely be recorded as
> provenance events?

Concretely, for fill #5: the 48.27 entry was the best information available at execution
time — arguably the "honest" fill under point-in-time semantics; 51.33 is the best current
estimate of what that close actually was. The north-star, hit-rate outcomes, and any future
broker reconciliation may each want a different answer. **This question does not need an
answer today** — it is recorded so the eventual Price Provenance Layer design starts here
instead of rediscovering the tension.

## Run #2 and Run #3 (2026-06-12, same day — appended)

**Run #2 (after the Tencent coverage fix, PR #157):** coverage healed (0 unpriced fills, 0
unpriceable tickers, 10/10 verifiable days). Verdict overall FAIL again — but the drift
pattern formed a natural experiment: the 8 pre-finality snapshots (2026-05-29 … 06-09, frozen
before the is_final governance) drifted −0.5% … −3.6% with signed mean −0.88%, while the 2
snapshots rebuilt on healed bars (06-10, 06-11) agreed with Tencent at exactly 0.000%. The
run-#1 Working Hypothesis was thereby UPGRADED and generalized: not an AMSC cluster but a
**pre-finality contamination boundary** — the frozen series held provisional/revised bar
values that neither vendor now endorses.

**Remediation (approved on the run-#2 evidence):** the FULL 10-day series was rebuilt in ONE
transaction (delete-all → ascending rebuild so 05-29 self-anchors as inception → flag →
single commit; rollback-safe; `rebuild_reason='post-finality-remediation'`). Scope note: the
user approved 8 days; anchor coherence required touching all 10 — `run_nav_snapshot` anchors
to the earliest eligible snapshot, so rebuilding 05-29 with any old/rebuilt later row present
would inherit a wrong anchor. The 06-10/11 NAV values did not change; their anchor/excess
columns re-derived from the new inception. Pre-rebuild values archived:
`docs/operations/north-star-baseline-v0.json`. Headline change: latest north-star excess
moved from −2.45% (contaminated anchor) to **−1.71%** (healed chain).

**Run #3 (post-remediation): NAV leg PASS — 0.0000% drift on all 10 days.** The project's
original question is answered for the current canonical chain: **no single-vendor price
illusion**. Fills leg remains FAIL solely on fill #5 (frozen revised value, −596 bps) — by
design under the fill-immutable principle; the audit will keep flagging it until the Open
Question below is answered by the eventual Price Provenance Layer. Excluding that single
fill, fills mean is 11.8 bps (within gates).

## What this does NOT change

- Roadmap order unchanged: Shadow 2a complete (this MVP); Shadow 2b stays on its lifecycle
  gate; 8a design review stays next-after the north-star 30-day mark.
- Charter fact layer records only: run #1 overall FAIL, findings identified, follow-up
  required. The Tencent bug, the AMSC-drift hypothesis, and the revision-tracking idea stay
  at this tier.
