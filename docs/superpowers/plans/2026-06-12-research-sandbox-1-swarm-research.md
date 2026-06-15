# Research Sandbox Experiment 1 — Swarm Research Arm — Implementation Plan

> Formerly drafted as **Phase 8c-1**; renamed before merge (independent of Phase 8a/8b ML
> work). Code strategy label stays `swarm_research`. PR slice labels (8c-1a..d) are retained
> in commit messages as historical markers only.
>
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

> **Status: COMPLETE ✅ (2026-06-15).** All four PR slices landed (#159) plus follow-ups
> #160 (trust_env proxy fix), #162 (default preset `swarm_research_investment_committee` +
> drop `_GOAL_SUFFIX`), #163 (timeout 2400 stopgap). Production default path validated end to
> end (event 264, no overrides). Remaining work is data accrual (≥30 h5 → permutation gate),
> not implementation. The async-finalizer fix for swarm timeout fragility is a separate
> experiment (Research Sandbox 1.1).

**Goal:** A `swarm_research` shadow strategy whose bullish/neutral/bearish verdicts come from
the NAS-hosted Vibe-Trading swarm over HTTP, recorded as ordinary `ai_analysis` events
(`payload.strategy="swarm_research"`) and graded by the existing permutation pipeline. No
allocator/execution/North-Star coupling, no new schema, default OFF.

**Architecture:** replaceable `SwarmVerdictProvider` (Stub + HttpVibe) → CLI batch → existing
`record_event` → existing eval-outcome + permutation. Spec:
`docs/superpowers/specs/2026-06-12-phase-8c-swarm-research-design.md` (locked).
**Branch:** `docs/phase-8c-swarm-research-spec` (spec committed) — implementation PRs branch
from main per the split below.

**Tech stack:** Python 3.12, httpx (already a dep), pytest. Lint `uv run ruff check`,
`# Layer:` tags. CLI convention: mirror `marketpulse/cli/finalize_prices.py`.

Verified facts (do not rediscover):
- Verdict constants: `AIVerdict.{BULLISH,NEUTRAL,BEARISH}` = "bullish"/"neutral"/"bearish",
  `AIVerdict.all()` (marketpulse/evaluation/constants.py). `EventType.AI_ANALYSIS="ai_analysis"`.
- `record_event(*, event_type, subtype, ticker, event_time, event_price, payload, db)` —
  validates subtype ∈ AIVerdict.all(), event_time tz-aware, event_price>0, ticker non-empty
  (marketpulse/evaluation/events.py). The existing analyze() call (service.py:251) is the
  template: `payload` carries `strategy`, `source`, `model`, etc.
- Event price: reuse a `close_on_date(ticker, on_date) -> ClosePrice|None` provider
  (`marketpulse/trading/price_provider.py`); `.price` is a Decimal. Price unavailable → abstain.
- Permutation arm pickup is automatic: `evaluation.permutation.load_rows` filters
  `event_type='ai_analysis'` and reads `payload.$.strategy`; no change needed there.
- Vibe HTTP (verified live 2026-06-12, Bearer auth on all `/swarm/*`):
  `POST /swarm/runs {preset_name, user_vars}` → `{id, status}`; poll
  `GET /swarm/runs/{id}` → `{status, final_report, ...}` until terminal; `GET /settings/llm`
  → backend identity (Bearer).

**Three review protections (locked into the relevant tasks):**
1. VERDICT parse takes the **LAST** `VERDICT:` match only (mid-report quotes of prior verdicts
   must not be caught). — PR 8c-1a.
2. Provenance stores **host only** (`urlparse(base_url).netloc`), never full URL / token /
   query. — PR 8c-1b.
3. CLI prints distinct **recorded / abstained / failed** counts (no "done" that reads as
   "all produced samples"). — PR 8c-1c.

---

## PR 8c-1a — Parser + Provider Protocol + tests

**Files:** create `marketpulse/research/__init__.py`, `marketpulse/research/swarm_provider.py`;
test `tests/research/test_swarm_provider.py`.

- [ ] **Step 1: failing tests**

```python
# Layer: unit
"""Swarm verdict parsing + provider Protocol (Phase 8c-1)."""
from __future__ import annotations

from datetime import date

from marketpulse.research.swarm_provider import (
    StubSwarmVerdictProvider,
    SwarmVerdict,
    parse_verdict,
)


def test_parse_verdict_basic():
    assert parse_verdict("...thesis...\nVERDICT: bullish") == "bullish"
    assert parse_verdict("VERDICT: bearish") == "bearish"
    assert parse_verdict("verdict: BULLISH") == "bullish"        # case-insensitive
    assert parse_verdict("VERDICT:neutral") == "neutral"         # no space, own line


def test_parse_verdict_line_anchored_ignores_prose():
    # Protection 1 (tightened): a mid-sentence quote is NOT a verdict line.
    assert parse_verdict("Yesterday we said VERDICT: bearish in passing.") is None
    # final anchored line wins over an earlier anchored line
    report = "VERDICT: bearish\n...revised...\nVERDICT: bullish\n"
    assert parse_verdict(report) == "bullish"
    # prose mention + a real final line → the final line
    report2 = "We noted VERDICT: bearish earlier.\nVERDICT: neutral\n"
    assert parse_verdict(report2) == "neutral"


def test_parse_verdict_unparseable_returns_none():
    assert parse_verdict("no verdict line here") is None
    assert parse_verdict("VERDICT: maybe") is None      # not a valid label
    assert parse_verdict("") is None


def test_stub_provider_returns_canned():
    stub = StubSwarmVerdictProvider({"NVDA": SwarmVerdict(
        verdict="bullish", run_id="r1", provenance={"engine": "stub"})})
    v = stub.verdict_for(ticker="NVDA", as_of=date(2026, 6, 12))
    assert v.verdict == "bullish" and v.run_id == "r1"
    assert stub.verdict_for(ticker="ZZZ", as_of=date(2026, 6, 12)) is None
```

- [ ] **Step 2: run → FAIL.**

- [ ] **Step 3: implement `marketpulse/research/swarm_provider.py`**

```python
# Layer: research
"""Swarm verdict provider (Phase 8c-1 spec). Pure protocol + parser + stub;
the HTTP adapter lands in 8c-1b. No DB, no allocator, no execution."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from marketpulse.evaluation.constants import AIVerdict

# Protection 1 (review-tightened): a VERDICT must be its OWN final-style line —
# `^ VERDICT: <label> $` anchored, multiline — so a mid-sentence quote like
# "Yesterday we said VERDICT: bearish." is NOT caught. Take the LAST such line.
_VERDICT_RE = re.compile(
    r"(?im)^\s*VERDICT:\s*(bullish|neutral|bearish)\s*$",
)


def parse_verdict(report: str) -> str | None:
    """Last anchored `VERDICT: <label>` LINE with a valid label; else None.
    Mid-report prose mentions never match (line-anchored). Unparseable/invalid
    → None (caller abstains; never a forced neutral)."""
    matches = _VERDICT_RE.findall(report or "")
    return matches[-1].lower() if matches else None


@dataclass(frozen=True)
class SwarmVerdict:
    verdict: str                      # bullish | neutral | bearish
    run_id: str
    provenance: dict = field(default_factory=dict)


class SwarmVerdictProvider(Protocol):
    def verdict_for(self, *, ticker: str, as_of: date) -> SwarmVerdict | None: ...


class StubSwarmVerdictProvider:
    """Test double — canned verdicts keyed by ticker. Never touches network."""

    def __init__(self, canned: dict[str, SwarmVerdict]) -> None:
        self._canned = canned

    def verdict_for(self, *, ticker: str, as_of: date) -> SwarmVerdict | None:
        return self._canned.get(ticker.strip().upper())
```

- [ ] **Step 4: run → 4 pass. ruff clean.**
- [ ] **Step 5: commit** — `feat(research): swarm verdict parser + provider protocol (8c-1a)`

---

## PR 8c-1b — HTTP adapter (HttpVibeSwarmProvider) + mocked-httpx tests

**Files:** extend `marketpulse/research/swarm_provider.py`; add 6 `SWARM_RESEARCH_*` settings
to `marketpulse/config.py`; test `tests/research/test_http_vibe_provider.py`.

- [ ] **Step 1: config** — add to `Settings` (mirror existing `Field(alias=...)` style):

```python
    swarm_research_enabled: bool = Field(False, alias="SWARM_RESEARCH_ENABLED")
    swarm_research_base_url: str = Field(
        "http://192.168.50.29:8899", alias="SWARM_RESEARCH_BASE_URL")
    swarm_research_api_key: str = Field("", alias="SWARM_RESEARCH_API_KEY")
    swarm_research_preset: str = Field(
        "investment_committee", alias="SWARM_RESEARCH_PRESET")
    swarm_research_timeout_seconds: int = Field(
        300, alias="SWARM_RESEARCH_TIMEOUT_SECONDS")
    swarm_research_max_tickers_per_run: int = Field(
        5, alias="SWARM_RESEARCH_MAX_TICKERS_PER_RUN")
```

- [ ] **Step 2: failing tests** — drive httpx via a mock transport (respx is already used in
  tests/unit/test_tencent_client.py — reuse it). Cover:
  - happy path: POST returns `{id,status:running}`; first poll `running`, second `completed`
    with `final_report` ending `VERDICT: bullish` → `SwarmVerdict(verdict="bullish",
    run_id=<id>, provenance has engine/provider/preset/swarm_size/run_id/adapter_version)`.
  - **Protection 2:** provenance `base_url` == host only (e.g. `192.168.50.29:8899`), and the
    provenance dict contains NO key whose value equals the API token and no `query`/full-URL.
  - terminal `failed` status → None (abstain). poll timeout (status never terminal within a
    tiny injected timeout) → None.
  - unparseable `final_report` → None.
  - backend identity: `GET /settings/llm` mocked → provenance `backend` set; on 401/error →
    `backend == "unknown"`.

- [ ] **Step 3: implement `HttpVibeSwarmProvider`** (append):

```python
import time
from urllib.parse import urlparse

import httpx

from marketpulse.logging import get_logger

log = get_logger(__name__)

_TERMINAL = {"completed", "failed", "cancelled", "error"}
# Review fix: explicit blank line so goal + suffix never concatenate into one
# run-on sentence in the prompt.
_GOAL_SUFFIX = (
    "\n\nEnd your report with a single final line exactly in the form:\n"
    "VERDICT: bullish|neutral|bearish"
)
# Review fix #6: tolerant backend identity — try these keys in order, else unknown.
_BACKEND_KEYS = ("model", "backend", "provider", "llm_model", "model_name")


class HttpVibeSwarmProvider:
    """Real adapter against the NAS Vibe-Trading service (Bearer auth).
    Async poll model. Any failure for a ticker → None (abstain); never raises
    out to the batch, never touches a MarketPulse production path.

    Review fix: an httpx.Client is INJECTED (default constructs one) so tests
    drive a mock transport instead of patching module-global httpx.get/post."""

    def __init__(self, *, base_url: str, api_key: str, preset: str,
                 timeout_seconds: int, goal: str, client: httpx.Client | None = None,
                 poll_interval: float = 5.0, clock=time) -> None:
        self._base = base_url.rstrip("/")
        self._host = urlparse(base_url).netloc or base_url   # Protection 2
        self._key = api_key
        self._preset = preset
        self._timeout = timeout_seconds
        self._goal = goal
        self._poll = poll_interval
        self._clock = clock
        self._client = client or httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"}, timeout=30,
        )
        self._backend = self._fetch_backend()

    def _fetch_backend(self) -> str:
        try:
            r = self._client.get(f"{self._base}/settings/llm", timeout=10)
            r.raise_for_status()
            data = r.json()
            for k in _BACKEND_KEYS:
                if data.get(k):
                    return str(data[k])
            return "unknown"
        except Exception:  # noqa: BLE001 — provenance is best-effort
            return "unknown"

    def verdict_for(self, *, ticker: str, as_of: date) -> SwarmVerdict | None:
        try:
            run_id, swarm_size = self._start(ticker)
            report = self._poll_report(run_id)
        except Exception as exc:  # noqa: BLE001 — per-ticker isolation
            log.warning("swarm_run_failed", ticker=ticker, error=str(exc))
            return None
        if report is None:
            return None
        verdict = parse_verdict(report)
        if verdict is None:
            log.warning("swarm_verdict_unparseable", ticker=ticker, run_id=run_id)
            return None
        # Protection 2: host only; NO token, NO query, NO full URL.
        prov = {
            "engine": "vibe-trading", "provider": "http", "base_url": self._host,
            "backend": self._backend, "preset": self._preset,
            "run_id": run_id, "adapter_version": "8c-1",
        }
        # Review fix #1: swarm_size is OPTIONAL — recorded only if the API exposed it.
        if swarm_size is not None:
            prov["swarm_size"] = swarm_size
        return SwarmVerdict(verdict=verdict, run_id=run_id, provenance=prov)

    def _start(self, ticker: str) -> tuple[str, int | None]:
        body = {"preset_name": self._preset,
                "user_vars": {"target": ticker, "market": "US",
                              "goal": self._goal + _GOAL_SUFFIX}}
        r = self._client.post(f"{self._base}/swarm/runs", json=body)
        r.raise_for_status()
        data = r.json()
        # swarm_size: only if the POST/preset response carries it; else None.
        size = data.get("agent_count") or data.get("swarm_size")
        return str(data["id"]), (int(size) if size else None)

    def _poll_report(self, run_id: str) -> str | None:
        deadline = self._clock.monotonic() + self._timeout
        while self._clock.monotonic() < deadline:
            r = self._client.get(f"{self._base}/swarm/runs/{run_id}")
            r.raise_for_status()
            data = r.json()
            status = str(data.get("status", "")).lower()
            if status in _TERMINAL:
                if status == "completed":
                    return data.get("final_report")
                return None  # failed/cancelled → abstain
            self._clock.sleep(self._poll)
        return None  # timeout → abstain
```

Notes: `/settings/llm`'s real field shape is unknown until a key is in hand — the tolerant
`_BACKEND_KEYS` scan returns `"unknown"` rather than guessing; the dry run (8c-1d) records the
actual response shape so a follow-up can pin the exact key. `swarm_size` is never hardcoded.

- [ ] **Step 4: run → pass. ruff clean.**
- [ ] **Step 5: commit** — `feat(research): HttpVibeSwarmProvider + config (8c-1b)`

---

## PR 8c-1c — CLI + record_event integration

**Files:** create `marketpulse/cli/run_swarm_research.py`; test
`tests/cli/test_run_swarm_research.py`.

- [ ] **Step 1: failing tests** (inject `StubSwarmVerdictProvider` + a stub price provider;
  use the `db_url` + `get_settings.cache_clear()` idiom). Cover:
  - records an `ai_analysis` event with `subtype` = stub verdict, `payload.strategy ==
    "swarm_research"`, `payload.source == "swarm"`, and the provenance dict present; the row is
    retrievable by `evaluation.permutation.load_rows` as a `swarm_research` strategy row.
  - **abstain records NO event** (stub returns None for a ticker → 0 events for it).
  - **Protection 3:** the CLI result/printout reports `recorded`, `abstained`, `failed` counts
    distinctly; a mix of (one verdict, one None, one price-missing) → recorded=1 abstained=1
    failed=1.
  - price unavailable (stub price provider returns None) → that ticker is `failed`/abstained,
    no event (record_event needs event_price>0).
  - config gate: `SWARM_RESEARCH_ENABLED=false` OR empty api key → CLI exits non-zero with a
    clear message, writes nothing.
  - **secret hygiene:** the persisted payload provenance contains no field equal to the API
    key.

- [ ] **Step 2: FAIL.**

- [ ] **Step 3: implement** `marketpulse/cli/run_swarm_research.py`:

```python
# Layer: cli
"""Phase 8c-1 shadow batch: python -m marketpulse.cli.run_swarm_research
   --tickers AAPL,NVDA --as-of 2026-06-15
Records swarm_research verdicts as ai_analysis events. Default OFF: requires
SWARM_RESEARCH_ENABLED=true AND SWARM_RESEARCH_API_KEY. Never auto-runs in the
daily tick. NOT an allocator/execution path."""
from __future__ import annotations

import argparse
import contextlib
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime

from marketpulse.config import get_settings
from marketpulse.db.base import session_scope
from marketpulse.evaluation.events import record_event
from marketpulse.logging import get_logger

log = get_logger(__name__)


@dataclass
class BatchResult:
    recorded: int = 0
    abstained: int = 0
    failed: int = 0


def run_batch(db, *, tickers, as_of, provider, price_provider) -> BatchResult:
    """Three-state semantics (review fix #3):
      abstained = provider returned no verdict (None, or raised — isolated here).
      failed    = HAD a verdict but couldn't record it (no event_price, or
                  record_event rejected it).
      recorded  = event written.
    """
    res = BatchResult()
    for ticker in tickers:
        # Per-ticker isolation belt-and-braces (review): even though providers
        # are designed not to raise, a stub/future provider might.
        try:
            v = provider.verdict_for(ticker=ticker, as_of=as_of)
        except Exception as exc:  # noqa: BLE001
            res.abstained += 1
            log.warning("swarm_research_provider_error", ticker=ticker, error=str(exc))
            continue
        if v is None:
            res.abstained += 1
            log.info("swarm_research_abstained", ticker=ticker)
            continue
        close = price_provider.close_on_date(ticker=ticker, on_date=as_of)
        if close is None:
            res.failed += 1   # had a verdict, lost it to a missing price
            log.warning("swarm_research_no_price", ticker=ticker)
            continue
        record_event(
            event_type="ai_analysis", subtype=v.verdict, ticker=ticker,
            event_time=datetime.combine(as_of, datetime.min.time(), tzinfo=UTC),
            event_price=float(close.price),
            payload={"source": "swarm", "strategy": "swarm_research",
                     "provenance": v.provenance},
            db=db,
        )
        res.recorded += 1
        log.info("swarm_research_recorded", ticker=ticker, verdict=v.verdict)
    return res


def _build_provider(settings, goal: str):
    from marketpulse.research.swarm_provider import HttpVibeSwarmProvider
    return HttpVibeSwarmProvider(
        base_url=settings.swarm_research_base_url,
        api_key=settings.swarm_research_api_key,
        preset=settings.swarm_research_preset,
        timeout_seconds=settings.swarm_research_timeout_seconds,
        goal=goal,
    )


def main(argv: list[str] | None = None, *, provider=None, price_provider=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", required=True, help="comma-separated")
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--goal", default="Assess 5-trading-day outlook vs SPY.")
    args = ap.parse_args(argv)

    settings = get_settings()
    if not settings.swarm_research_enabled or not settings.swarm_research_api_key:
        print("swarm research disabled: set SWARM_RESEARCH_ENABLED=true and "
              "SWARM_RESEARCH_API_KEY", file=sys.stderr)
        raise SystemExit(1)

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    cap = settings.swarm_research_max_tickers_per_run
    if len(tickers) > cap:
        print(f"too many tickers ({len(tickers)} > cap {cap})", file=sys.stderr)
        raise SystemExit(1)
    as_of = date.fromisoformat(args.as_of) if args.as_of \
        else datetime.now(UTC).date()

    if provider is None:
        provider = _build_provider(settings, args.goal)
    if price_provider is None:
        # Review #5: reuse the SAME close_on_date provider the paper engine uses
        # (marketpulse/trading/price_provider.YFinancePriceProvider) so event_price
        # is drawn from the identical price path as other evaluation events — keeps
        # the swarm arm's prices comparable, no separate ad-hoc quote source. It
        # resolves last-final-close ≤ as_of (post-P2F), the right reference price.
        from marketpulse.data.yfinance_client import YFinanceClient
        from marketpulse.trading.price_provider import YFinancePriceProvider
        price_provider = YFinancePriceProvider(client=YFinanceClient())

    # NOTE (review #4): session_scope is a PLAIN GENERATOR in this repo
    # (marketpulse/db/base.py — bare yield + finally, NOT @contextmanager).
    # Manual `next(gen)` driving is the verified convention (finalize_prices,
    # refresh_sectors, rebuild_nav_snapshots all do this). Do NOT rewrite to
    # `with session_scope() as db:` — it would raise.
    gen = session_scope()
    db = next(gen)
    try:
        res = run_batch(db, tickers=tickers, as_of=as_of,
                        provider=provider, price_provider=price_provider)
        db.commit()
        print(f"swarm_research {as_of}: recorded={res.recorded} "
              f"abstained={res.abstained} failed={res.failed}")
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: targeted pass → FULL suite → ruff clean.**
- [ ] **Step 5: commit** — `feat(cli): run_swarm_research batch + record_event (8c-1c)`

---

## PR 8c-1d — CHARTER pointer + first dry run

**Files:** `docs/CHARTER.md` only.

- [ ] **Step 1: NO strategy YAML (corrected).** The original plan added
  `definitions/swarm_research.yaml`, but `definitions/` is loaded by
  `RiskConfigProvider.from_yaml(strategies_dir=...)` and fed to the paper-trading risk
  gate/allocator (jobs.py / paper_trading_tick.py). Placing the shadow arm there would couple
  it into the execution path — a violation of the locked isolation invariant. **Do not add a
  strategy YAML.** The arm is identified solely by `payload.strategy="swarm_research"`.

- [ ] **Step 2: CHARTER pointer** (strategy-trust chain): one line —
  `8c-1 swarm_research shadow arm (spec 2026-06-12): verdicts from NAS Vibe-Trading over HTTP,
  recorded as ai_analysis/payload.strategy=swarm_research, permutation-gated (≥30 h5 →
  p_system<0.05 or archive); isolated from allocator/execution/North-Star; default OFF. Results
  live in run output / review materials, not this fact layer.`

- [ ] **Step 3: full suite + ruff.**

- [ ] **Step 4: commit** — `docs: swarm_research strategy def + CHARTER 8c-1 pointer (8c-1d)`

- [ ] **Step 5 (operator dry run, post-merge — NOT a code task):** on the NAS, with
  `SWARM_RESEARCH_ENABLED=true` + key set, run a tiny batch
  (`--tickers AAPL,NVDA,AMD --as-of <today>`); confirm: events land with
  `payload.strategy=swarm_research` + provenance (no token), counts print recorded/abstained/
  failed, Vibe `/swarm/runs` shows the runs. This seeds the arm toward the ≥30-h5 permutation
  gate. Verdict accrual is then passive (re-run the batch on a cadence you choose).
  - **Also record the actual `/settings/llm` response shape** observed during the dry run
    (with the key) so a follow-up can pin the exact backend field name (review #6).
  - **Read-only verification (review suggestion):** after the dry run, confirm via a quick
    query — count of `ai_analysis` events with `payload.$.strategy='swarm_research'` and their
    subtype distribution — to verify accrual and that no forced-neutral skew appeared. (Same
    in-container sqlite/`/app/.venv/bin/python` one-liner pattern used for prior audits.)

---

## Out of scope (reaffirmed)

No allocator/execution/North-Star coupling; no daily-tick auto-run (the
`SWARM_RESEARCH_DAILY` flag is NOT built here); no Catalyst/Narrative detectors; no new
event_type/schema/migration/statistics; no compose merge. Consensus-Breaker is a later SQL
analysis on the accumulated rows, not a system feature.
