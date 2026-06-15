# Layer: research
"""PR 8c-1b — HttpVibeSwarmProvider tests. httpx is driven via an injected
MockTransport (no module-global patching); a fake clock makes the timeout
path testable without real sleeping."""
from __future__ import annotations

import json
from datetime import date

import httpx

from marketpulse.research.swarm_provider import HttpVibeSwarmProvider, SwarmVerdict

_AS_OF = date(2026, 6, 15)
_BASE = "http://192.168.50.29:8899"
_KEY = "secret-token-xyz"


class _FakeClock:
    """Monotonic/sleep fake: advances time on every sleep() and exposes a
    monotonic() so the poll timeout fires deterministically — no real waiting."""

    def __init__(self, *, step: float = 1.0) -> None:
        self._now = 0.0
        self._step = step
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += self._step


def _provider(handler, *, clock=None, timeout_seconds=300):
    """Build a provider with an injected MockTransport client. The handler
    receives each httpx.Request and returns an httpx.Response."""
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {_KEY}"},
    )
    return HttpVibeSwarmProvider(
        base_url=_BASE, api_key=_KEY, preset="investment_committee",
        timeout_seconds=timeout_seconds, goal="Assess the stock.",
        client=client, poll_interval=5.0,
        clock=clock or _FakeClock(),
    )


def _llm_ok(request: httpx.Request) -> httpx.Response | None:
    if request.url.path == "/settings/llm":
        return httpx.Response(200, json={"model": "qwen2.5:32b"})
    return None


def test_happy_path_running_then_completed() -> None:
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if (resp := _llm_ok(request)) is not None:
            return resp
        if request.method == "POST" and request.url.path == "/swarm/runs":
            body = json.loads(request.content)
            assert body["preset_name"] == "investment_committee"
            # The preset now owns the VERDICT contract; the provider sends the
            # caller's goal verbatim (no appended VERDICT suffix).
            assert body["user_vars"]["goal"] == "Assess the stock."
            return httpx.Response(200, json={"id": "run-42", "status": "running",
                                             "agent_count": 7})
        if request.url.path == "/swarm/runs/run-42":
            polls["n"] += 1
            if polls["n"] == 1:
                return httpx.Response(200, json={"status": "running"})
            return httpx.Response(200, json={
                "status": "completed",
                "final_report": "Analysis complete.\n\nVERDICT: bullish",
            })
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    v = _provider(handler).verdict_for(ticker="NVDA", as_of=_AS_OF)
    assert isinstance(v, SwarmVerdict)
    assert v.verdict == "bullish"
    assert v.run_id == "run-42"
    p = v.provenance
    assert p["engine"] == "vibe-trading"
    assert p["provider"] == "http"
    assert p["preset"] == "investment_committee"
    assert p["run_id"] == "run-42"
    assert p["adapter_version"] == "8c-1"
    assert p["backend"] == "qwen2.5:32b"
    assert p["swarm_size"] == 7  # carried by API response


def test_provenance_is_host_only_and_carries_no_secrets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if (resp := _llm_ok(request)) is not None:
            return resp
        if request.method == "POST":
            return httpx.Response(200, json={"id": "r1", "status": "running"})
        return httpx.Response(200, json={
            "status": "completed", "final_report": "x\n\nVERDICT: bearish"})

    v = _provider(handler).verdict_for(ticker="AAPL", as_of=_AS_OF)
    assert v is not None
    p = v.provenance
    # Protection 2: host only — no scheme, no full URL, no query.
    assert p["base_url"] == "192.168.50.29:8899"
    blob = json.dumps(p)
    assert _KEY not in blob
    assert "http://" not in blob
    assert "?" not in blob
    # no field value equals the token
    assert all(val != _KEY for val in p.values())
    # swarm_size omitted when API didn't expose it
    assert "swarm_size" not in p


def test_terminal_failed_status_abstains() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if (resp := _llm_ok(request)) is not None:
            return resp
        if request.method == "POST":
            return httpx.Response(200, json={"id": "r2", "status": "running"})
        return httpx.Response(200, json={"status": "failed"})

    assert _provider(handler).verdict_for(ticker="TSLA", as_of=_AS_OF) is None


def test_poll_timeout_abstains() -> None:
    clock = _FakeClock(step=5.0)

    def handler(request: httpx.Request) -> httpx.Response:
        if (resp := _llm_ok(request)) is not None:
            return resp
        if request.method == "POST":
            return httpx.Response(200, json={"id": "r3", "status": "running"})
        return httpx.Response(200, json={"status": "running"})  # never terminal

    # tiny timeout so the injected clock crosses the deadline quickly.
    out = _provider(handler, clock=clock, timeout_seconds=10).verdict_for(
        ticker="MSFT", as_of=_AS_OF)
    assert out is None
    assert clock.sleeps  # it polled and slept against the fake clock


def test_unparseable_report_abstains() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if (resp := _llm_ok(request)) is not None:
            return resp
        if request.method == "POST":
            return httpx.Response(200, json={"id": "r4", "status": "running"})
        return httpx.Response(200, json={
            "status": "completed",
            "final_report": "We discussed VERDICT: bullish yesterday but no final line."})

    assert _provider(handler).verdict_for(ticker="AMD", as_of=_AS_OF) is None


def test_backend_from_settings_llm() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/settings/llm":
            return httpx.Response(200, json={"provider": "ollama-local"})
        if request.method == "POST":
            return httpx.Response(200, json={"id": "r5", "status": "running"})
        return httpx.Response(200, json={
            "status": "completed", "final_report": "x\n\nVERDICT: neutral"})

    v = _provider(handler).verdict_for(ticker="GOOG", as_of=_AS_OF)
    assert v is not None
    assert v.provenance["backend"] == "ollama-local"


def test_backend_unknown_on_settings_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/settings/llm":
            return httpx.Response(401, json={"detail": "unauthorized"})
        if request.method == "POST":
            return httpx.Response(200, json={"id": "r6", "status": "running"})
        return httpx.Response(200, json={
            "status": "completed", "final_report": "x\n\nVERDICT: bullish"})

    v = _provider(handler).verdict_for(ticker="META", as_of=_AS_OF)
    assert v is not None
    assert v.provenance["backend"] == "unknown"


def test_bare_injected_client_still_authenticates() -> None:
    """Minor fix: auth is applied per-request, so a client injected WITHOUT
    auth headers still sends the Bearer token on every call."""
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        if request.url.path == "/settings/llm":
            return httpx.Response(200, json={"model": "qwen"})
        if request.method == "POST":
            return httpx.Response(200, json={"id": "r1", "status": "running"})
        return httpx.Response(200, json={"status": "completed",
                                         "final_report": "VERDICT: bullish"})

    bare = httpx.Client(transport=httpx.MockTransport(handler))  # NO auth headers
    p = HttpVibeSwarmProvider(
        base_url=_BASE, api_key=_KEY, preset="investment_committee",
        timeout_seconds=300, goal="Assess.", client=bare, clock=_FakeClock(),
    )
    v = p.verdict_for(ticker="AAPL", as_of=date(2026, 6, 15))
    assert v is not None and v.verdict == "bullish"
    # every request carried the bearer token despite the bare client
    assert seen and all(h == f"Bearer {_KEY}" for h in seen)


def test_default_client_ignores_ambient_proxy_env() -> None:
    """Vibe is an internal NAS host. The default httpx.Client MUST be built with
    trust_env=False so it never inherits the marketpulse container's
    HTTP_PROXY/HTTPS_PROXY (a CN-data Clash proxy on :7892) — routing the Vibe
    call through that proxy returns a bare 502. No client is injected here, so
    the provider builds its own; that one must not trust the environment."""
    # Unreachable local port: __init__'s _fetch_backend probe fails fast
    # (connection refused, caught → backend "unknown"); no real NAS call.
    p = HttpVibeSwarmProvider(
        base_url="http://127.0.0.1:9", api_key=_KEY,
        preset="investment_committee", timeout_seconds=300, goal="Assess.",
    )
    assert p._client.trust_env is False
