# Research Sandbox Experiment 1 — Swarm Research Arm (Shadow Strategy) — Design

> Formerly drafted as **Phase 8c-1**; renamed before merge because this arm is **independent
> of the Phase 8a/8b ML work** — a parallel research-sandbox experiment, not a step in the
> main ML feature/prediction dependency chain. The code strategy label stays `swarm_research`
> (unchanged). "Phase number = main-line dependency chain; research sandbox = parallel arm."

**Date:** 2026-06-12
**Status:** **Technical validation complete ✅ (2026-06-15)** — waiting on (1) preset
persistence in the Vibe repo and (2) the MarketPulse default-flip PR. No core logic changes
pending; the design is proven end-to-end.
**Charter link:** strategy-trust chain — a NEW verdict source put through the EXISTING
permutation pipeline. Pure research arm: **must not touch the allocator, execution, the
North Star, or introduce any new statistics.**

## Technical validation (2026-06-15, live NAS, single AAPL run)

The full chain was proven end-to-end:

```
MarketPulse → HttpVibeSwarmProvider → Vibe Swarm → VERDICT contract
→ parse_verdict → EvaluationEvent (payload.strategy="swarm_research") → permutation pipeline
```

Confirmed: provider architecture (Stub/HTTP); `trust_env=False` defeats the container's
ambient `HTTP_PROXY` (the real cause of the "intermittent 502s"); long-running task completes
(~16.5 min); the dedicated preset reliably emits a machine-parsable verdict; `parse_verdict`
hit a real report (`VERDICT: neutral`, event 263); `research_only=True` keeps it out of the
allocator; provenance recorded in full.

**The decisive finding** was not the network: the stock `investment_committee` preset declares
only `target`/`market` and templates **no `{goal}`**, so the appended VERDICT suffix was
**architecturally dropped** (`format_map(_FallbackDict(...))` silently discards unused vars).
Without this end-to-end test the arm would have collected **100% abstained** for a month while
we blamed the parser, timeout, or model. Fix: a dedicated `swarm_research_investment_committee`
preset that owns the VERDICT contract at the PM `system_prompt` level and actually consumes `{goal}`.

### Swarm throughput & cost (first real measurement)

| Metric | Observed (AAPL, investment_committee 4-agent DAG) |
|---|---|
| Wall-clock | ≈ 16.5 min |
| LLM round-trips | ≈ 48 (bull 13–16, bear 11–16, risk 5, PM 13) |
| Backend | OpenRouter · `deepseek/deepseek-v4-pro` |
| Est. cost / ticker | ~$0.2–1 (token usage not exposed by the run API; estimate from iterations + report sizes) |
| Default timeout | **1500s** (300s/900s were too short — a run runs right up to ~16 min) |

Planning implication: 30 outcomes ≈ 1–2 weeks at a small daily basket; a daily universe (50–100
names) becomes a real throughput/cost question. These are the first measured numbers, not guesses.

## Problem / the one question

The single-LLM advisor layer was measured to have **no statistical edge** (permutation A,
p=0.859). Vibe-Trading is a multi-agent ("swarm") research system now running as a separate
docker-compose service on the NAS (`http://192.168.50.29:8899`). The only question 8c-1 asks:

> **Do swarm verdicts have a measurable edge, on the same yardstick as the existing
> deterministic-prompt strategies?**

This is a hypothesis test, not an architecture change. One hypothesis at a time — Catalyst /
Narrative detectors (8c-2+) and the Consensus-Breaker analysis (free SQL on the resulting
data) are explicitly out of MVP scope.

## Scope (locked)

**In:** a `swarm_research` strategy arm whose `bullish/neutral/bearish` verdicts come from
Vibe-Trading over HTTP, recorded as ordinary `ai_analysis` evaluation events and graded by the
existing `_is_hit` + permutation pipeline. Manual/low-frequency batch CLI; deployment stays
SEPARATE (two composes, HTTP between them).

**Out:** allocator/execution integration; automatic daily full-universe runs (a
`SWARM_RESEARCH_DAILY` flag is reserved, default off, NOT built here); Catalyst/Narrative
detectors; any new event_type, schema, or statistical method; merging the composes.

## Architecture

### Event model — reuse, zero schema change (key decision)

A swarm verdict is recorded as an **existing `ai_analysis` event** (`subtype` =
bullish/neutral/bearish), with the arm identity in **`payload.strategy = "swarm_research"`**.
Consequence: `evaluation.permutation.load_rows` and the per-strategy diagnostic already group
by `payload.$.strategy`, so the swarm arm appears **automatically** as a peer of
momentum/news/general — head-to-head comparable, no new statistics, no migration.

`abstain`/unparseable is NOT a valid subtype, so an unparseable swarm result simply **records
no event** (never a forced `neutral` — the run-#1 neutral-overuse lesson). The run is logged
as `skipped`/`failed` with a reason; it produces no evaluation row.

### Verdict source (decision A, locked)

Vibe returns a free-text `final_report`, not a structured verdict. The verdict is the SWARM's
own conclusion — MarketPulse's own LLM is deliberately NOT inserted into the path (that would
test a different thing). Extraction failure → abstain (no event), never a default.

**Revised 2026-06-15 (validated):** the original plan appended a `VERDICT:` instruction to
`user_vars.goal`. That is **architecturally ineffective** — the stock `investment_committee`
preset declares only `target`/`market` and templates no `{goal}`, so Vibe's
`format_map(_FallbackDict(...))` silently discards it and no agent ever sees the instruction →
the PM ends in trade prose → 100% abstain. The VERDICT contract therefore lives in a **dedicated
preset, `swarm_research_investment_committee`** (clone of `investment_committee`): its
`portfolio_manager.system_prompt` mandates a standalone final line `VERDICT: bullish|neutral|bearish`
(BUY/ADD/LONG→bullish, SELL/SHORT/REDUCE→bearish, HOLD/WAIT→neutral), the `task-decision`
template repeats it, and a declared `goal` variable is consumed by every task. MarketPulse now
sends `goal` as plain context only (`_GOAL_SUFFIX` removed); the preset owns the contract.
`parse_verdict` extracts the last `VERDICT:` line; failure → abstain.

### Provider Protocol (replaceable; tests never hit the network)

```python
class SwarmVerdictProvider(Protocol):
    def verdict_for(self, *, ticker: str, as_of: date) -> SwarmVerdict | None: ...
# SwarmVerdict: frozen — verdict: str (bullish/neutral/bearish), run_id, provenance: dict
```

- `StubSwarmVerdictProvider` — tests inject canned verdicts.
- `HttpVibeSwarmProvider` — real adapter against `:8899`.

Per-ticker isolation: a Vibe failure/timeout for one ticker yields `None` (abstain) and is
logged; it never raises out of the batch, never touches any MarketPulse production path.

### HTTP contract (verified 2026-06-12 against the running service)

Bearer-auth on all swarm endpoints. Async poll model:

1. `POST /swarm/runs` body `{"preset_name": "investment_committee",
   "user_vars": {"target": "<TICKER>", "market": "US", "goal": "<analysis ask + 'End your
   report with a line: VERDICT: bullish|neutral|bearish'>"}}` → `{"id", "status"}`.
2. Poll `GET /swarm/runs/{id}` until `status` is terminal (completed / failed / cancelled) or
   `SWARM_RESEARCH_TIMEOUT_SECONDS` elapses → read `final_report`.
3. Regex-extract the last `VERDICT:` line; map to subtype; else abstain.

Backend identity for provenance comes from `GET /settings/llm` (also Bearer); on failure
record `backend: "unknown"`.

### Data flow

```
run_swarm_research CLI (manual/cron; OFF by default)
  → for each ticker (≤ MAX_TICKERS_PER_RUN):
      HttpVibeSwarmProvider.verdict_for(ticker, as_of)
        POST /swarm/runs → poll GET → parse VERDICT
      if verdict: record_event(event_type="ai_analysis", subtype=verdict,
                               ticker, event_time=<as_of EOD UTC>,
                               event_price=<as_of close from price_cache/DataService>,
                               payload={"strategy":"swarm_research","source":"swarm",
                                        "provenance":{...}})
      else: log skipped/failed(reason)
  → existing eval-outcome job resolves h1/h5 as usual
  → existing permutation CLI measures the swarm_research arm
```

`event_price` must be a positive close on `as_of` — reuse the existing price path (price_cache
/ DataService), the same source other analysis events use.

### Config (env)

```
SWARM_RESEARCH_ENABLED=false                         # default MUST be false
SWARM_RESEARCH_BASE_URL=http://192.168.50.29:8899
SWARM_RESEARCH_API_KEY=                              # Bearer token; NEVER logged or persisted
SWARM_RESEARCH_PRESET=swarm_research_investment_committee   # dedicated VERDICT-enforcing preset (owns the final-line contract; consumes {goal})
SWARM_RESEARCH_TIMEOUT_SECONDS=1500                  # 25 min; measured single-ticker 4-agent run ~16.5 min, 900s timed out just short
SWARM_RESEARCH_MAX_TICKERS_PER_RUN=5
```

The CLI refuses to run unless `SWARM_RESEARCH_ENABLED=true` AND an API key is set (fail loud,
no silent no-op that looks like success).

### Provenance (recorded in event payload; read-only, never feeds any decision)

```json
{"engine":"vibe-trading","provider":"http","base_url":"192.168.50.29:8899",
 "backend":"<from /settings/llm or 'unknown'>","preset":"investment_committee",
 "swarm_size":4,"run_id":"<vibe run id>","adapter_version":"8c-1"}
```

Records WHICH swarm/backend produced the verdict so a future edge can be attributed to a Vibe
version rather than confounded across upgrades.

## Pre-registered success / failure (permutation A, existing pipeline)

Once the `swarm_research` arm accumulates **≥30 resolved h5 outcomes**, run the existing
`permutation_test` CLI. **`p_system < 0.05` for the swarm arm ⇒ edge worth pursuing
(8c-2 detectors become justified); `p ≥ 0.05` ⇒ no edge, archive** — same verdict discipline,
locked before the data arrives. The Consensus-Breaker view (`swarm verdict ≠ traditional
consensus` on the resulting rows) is a free SQL analysis for the eventual report, not a system
feature.

## Error handling

- Vibe unreachable / non-terminal past timeout / unparseable report → that ticker abstains
  (no event), reason logged; batch continues. Total failure → CLI exits non-zero with a clear
  message; no partial-success illusion.
- API key absent while enabled → CLI refuses at startup.

## Testing (`# Layer:` tags; `uv run pytest`)

1. VERDICT extraction: bullish/neutral/bearish parsed from a report's last `VERDICT:` line;
   no line / garbage → abstain (None).
2. Provider stub injection: CLI records the stubbed verdict as an `ai_analysis` event with
   `payload.strategy=="swarm_research"` and full provenance.
3. Per-ticker isolation: one ticker's provider raises → that ticker abstains, others recorded,
   batch returns non-zero failure count without aborting.
4. Abstain records NO event (never a forced neutral).
5. `record_event` integration: subtype validates against bullish/neutral/bearish; event_price
   positive; event reachable by `permutation.load_rows` as a `swarm_research` strategy row.
6. Config gate: ENABLED=false or missing key → CLI refuses (no events written).
7. HTTP adapter against a mocked httpx (POST→poll→final_report); timeout path → abstain.
8. Secret hygiene: API key never appears in logs or persisted payloads (assert provenance has
   no key field).

## Files touched

- `marketpulse/research/swarm_provider.py` — Protocol + SwarmVerdict + Stub + HttpVibe adapter
- `marketpulse/research/verdict_parse.py` — VERDICT-line extraction (or inline in provider)
- `marketpulse/cli/run_swarm_research.py` — batch CLI
- `marketpulse/config.py` — 6 SWARM_RESEARCH_* settings
- ~~`marketpulse/strategies/definitions/swarm_research.yaml`~~ — **DROPPED (corrected during
  8c-1d implementation).** The `definitions/` dir is loaded by `RiskConfigProvider.from_yaml(
  strategies_dir=...)` and fed to the paper-trading risk gate / allocator — placing the arm
  there would COUPLE this shadow arm into the execution path, violating the locked
  "isolated from allocator/execution" invariant. The arm identity lives solely in the CLI's
  `payload.strategy="swarm_research"`; the swarm `goal` is the CLI `--goal` arg, not a YAML.
- `docs/CHARTER.md` — strategy-trust chain: 8c-1 pointer (shadow arm, permutation-gated,
  isolated; results stay in run output)
- tests per section above

No new event_type, no migration, no new dependencies (httpx already used), no allocator/
execution/UI changes, no compose merge.
