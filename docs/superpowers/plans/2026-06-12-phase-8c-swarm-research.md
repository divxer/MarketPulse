# Phase 8c-1 — Swarm Research Arm — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

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
    assert parse_verdict("VERDICT: bearish\n") == "bearish"
    assert parse_verdict("VERDICT:neutral") == "neutral"        # no space
    assert parse_verdict("verdict: BULLISH") == "bullish"        # case-insensitive


def test_parse_verdict_takes_last_match():
    # Protection 1: a mid-report quote of a prior verdict must not win.
    report = (
        "Yesterday we said VERDICT: bearish.\n"
        "Today after analysis...\n"
        "VERDICT: bullish\n"
    )
    assert parse_verdict(report) == "bullish"


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

# Protection 1: capture every VERDICT line, take the LAST valid one.
_VERDICT_RE = re.compile(r"VERDICT:\s*([A-Za-z]+)", re.IGNORECASE)


def parse_verdict(report: str) -> str | None:
    """Last `VERDICT: <label>` whose label is a valid AIVerdict; else None.
    Unparseable/invalid → None (caller abstains; never a forced neutral)."""
    valid = AIVerdict.all()
    last: str | None = None
    for m in _VERDICT_RE.finditer(report or ""):
        label = m.group(1).lower()
        if label in valid:
            last = label
    return last


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
_GOAL_SUFFIX = (
    " End your report with a single final line exactly in the form: "
    "VERDICT: bullish|neutral|bearish"
)


class HttpVibeSwarmProvider:
    """Real adapter against the NAS Vibe-Trading service (Bearer auth).
    Async poll model. Any failure for a ticker → None (abstain); never raises
    out to the batch, never touches a MarketPulse production path."""

    def __init__(self, *, base_url: str, api_key: str, preset: str,
                 timeout_seconds: int, goal: str,
                 poll_interval: float = 5.0, clock=time) -> None:
        self._base = base_url.rstrip("/")
        self._host = urlparse(base_url).netloc or base_url   # Protection 2
        self._key = api_key
        self._preset = preset
        self._timeout = timeout_seconds
        self._goal = goal
        self._poll = poll_interval
        self._clock = clock
        self._backend = self._fetch_backend()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._key}"}

    def _fetch_backend(self) -> str:
        try:
            r = httpx.get(f"{self._base}/settings/llm",
                          headers=self._headers(), timeout=10)
            r.raise_for_status()
            data = r.json()
            return str(data.get("model") or data.get("backend") or "unknown")
        except Exception:  # noqa: BLE001 — provenance is best-effort
            return "unknown"

    def verdict_for(self, *, ticker: str, as_of: date) -> SwarmVerdict | None:
        try:
            run_id = self._start(ticker)
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
        return SwarmVerdict(verdict=verdict, run_id=run_id, provenance=prov)

    def _start(self, ticker: str) -> str:
        body = {"preset_name": self._preset,
                "user_vars": {"target": ticker, "market": "US",
                              "goal": self._goal + _GOAL_SUFFIX}}
        r = httpx.post(f"{self._base}/swarm/runs", json=body,
                       headers=self._headers(), timeout=30)
        r.raise_for_status()
        return str(r.json()["id"])

    def _poll_report(self, run_id: str) -> str | None:
        deadline = self._clock.monotonic() + self._timeout
        while self._clock.monotonic() < deadline:
            r = httpx.get(f"{self._base}/swarm/runs/{run_id}",
                          headers=self._headers(), timeout=30)
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

(Adjust `data.get("model")` to the real `/settings/llm` field once a key is available; until
then `"unknown"` is the correct recorded value. If `swarm_size` is exposed on the run detail,
add it to provenance; otherwise omit — do not hardcode 4.)

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
    res = BatchResult()
    for ticker in tickers:
        v = provider.verdict_for(ticker=ticker, as_of=as_of)
        if v is None:
            res.abstained += 1
            log.info("swarm_research_abstained", ticker=ticker)
            continue
        close = price_provider.close_on_date(ticker=ticker, on_date=as_of)
        if close is None:
            res.failed += 1
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
        # production price path; tests inject a stub
        from marketpulse.data.yfinance_client import YFinanceClient
        from marketpulse.trading.price_provider import YFinancePriceProvider
        price_provider = YFinancePriceProvider(client=YFinanceClient())

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

## PR 8c-1d — strategy YAML + CHARTER pointer + first dry run

**Files:** `marketpulse/strategies/definitions/swarm_research.yaml`; `docs/CHARTER.md`.

- [ ] **Step 1: strategy definition** (so the arm is named/visible like the others; its
  `instructions` double as the swarm `goal` text). Mirror the existing YAML shape; add an
  `engine: vibe-trading` marker and verdict-line instruction. If the YAML loader rejects
  unknown keys, add `engine` to the loader's allowed set (check
  `marketpulse/strategies/` loader first; keep it backward-compatible).

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

---

## Out of scope (reaffirmed)

No allocator/execution/North-Star coupling; no daily-tick auto-run (the
`SWARM_RESEARCH_DAILY` flag is NOT built here); no Catalyst/Narrative detectors; no new
event_type/schema/migration/statistics; no compose merge. Consensus-Breaker is a later SQL
analysis on the accumulated rows, not a system feature.
