# Phase 8c-1 — Swarm Research Arm (Shadow Strategy) — Design

**Date:** 2026-06-12
**Status:** Approved (design locked)
**Charter link:** strategy-trust chain — a NEW verdict source put through the EXISTING
permutation pipeline. Pure research arm: **must not touch the allocator, execution, the
North Star, or introduce any new statistics.**

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

Vibe's `investment_committee` preset returns a free-text `final_report`, not a structured
verdict. The adapter instructs the swarm (via `user_vars.goal`) to **end its report with a
single line `VERDICT: bullish|neutral|bearish`**, then extracts it. The verdict is the SWARM's
own conclusion — MarketPulse's own LLM is deliberately NOT inserted into the path (that would
test a different thing). Extraction failure → abstain (no event), never a default.

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
SWARM_RESEARCH_PRESET=investment_committee
SWARM_RESEARCH_TIMEOUT_SECONDS=300
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
- `marketpulse/strategies/definitions/swarm_research.yaml` — arm definition (display name,
  description, `engine: vibe-trading` marker; instructions used as the swarm `goal`)
- `docs/CHARTER.md` — strategy-trust chain: 8c-1 pointer (shadow arm, permutation-gated,
  isolated; results stay in run output)
- tests per section above

No new event_type, no migration, no new dependencies (httpx already used), no allocator/
execution/UI changes, no compose merge.
