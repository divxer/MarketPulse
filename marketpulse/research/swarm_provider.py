# Layer: research
"""Swarm verdict provider (Phase 8c-1 spec): parser + Protocol + Stub +
HttpVibeSwarmProvider. No DB, no allocator, no execution — research arm only."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol
from urllib.parse import urlparse

import httpx

from marketpulse.evaluation.constants import AIVerdict
from marketpulse.logging import get_logger

log = get_logger(__name__)

# Protection 1 (review-tightened): a VERDICT must be its OWN final-style line —
# `^ VERDICT: <label> $` anchored, multiline — so a mid-sentence quote like
# "Yesterday we said VERDICT: bearish." is NOT caught. Take the LAST such line.
# Built from AIVerdict.all() so the valid-label set has a single source of truth
# (no drift if a fourth verdict label is ever added).
_VERDICT_RE = re.compile(
    rf"(?im)^\s*VERDICT:\s*({'|'.join(sorted(AIVerdict.all()))})\s*$",
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
        # Review (Minor): auth is applied PER REQUEST (not only on the default
        # client's headers) so an INJECTED client without auth headers still
        # authenticates.
        self._auth = {"Authorization": f"Bearer {api_key}"}
        self._client = client or httpx.Client(timeout=30)
        self._backend = self._fetch_backend()

    def _fetch_backend(self) -> str:
        try:
            r = self._client.get(
                f"{self._base}/settings/llm", headers=self._auth, timeout=10)
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
        r = self._client.post(
            f"{self._base}/swarm/runs", json=body, headers=self._auth)
        r.raise_for_status()
        data = r.json()
        # swarm_size: only if the POST/preset response carries it; else None.
        size = data.get("agent_count") or data.get("swarm_size")
        return str(data["id"]), (int(size) if size else None)

    def _poll_report(self, run_id: str) -> str | None:
        deadline = self._clock.monotonic() + self._timeout
        while self._clock.monotonic() < deadline:
            r = self._client.get(
                f"{self._base}/swarm/runs/{run_id}", headers=self._auth)
            r.raise_for_status()
            data = r.json()
            status = str(data.get("status", "")).lower()
            if status in _TERMINAL:
                if status == "completed":
                    return data.get("final_report")
                return None  # failed/cancelled → abstain
            self._clock.sleep(self._poll)
        return None  # timeout → abstain
