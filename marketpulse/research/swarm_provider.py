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
