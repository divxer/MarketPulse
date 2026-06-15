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
