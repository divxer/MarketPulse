"""RiskGate Protocol + 6a's AlwaysApproveRiskGate stub.

6b extends RiskResult with `failed_gates` + `context` for the composite
gate's run-all + audit-all contract. The 6a contract (approved, reason,
gate_name) stays intact via default values.

Lock 6b-L16: `context` is wrapped in MappingProxyType post-construction
so top-level mutation (`result.context["x"] = y`) raises TypeError.
**This is a top-level guarantee only — NOT a deep freeze.** Nested
mutables (e.g., `result.context["per_gate"][0]["context"]["k"] = "v"`)
remain mutable in memory. Deep immutability is owned by lock 6b-L17:
`audit_json.normalize_for_json` materializes fresh deep copies at every
audit-write boundary, so the persisted audit ledger is the authoritative
immutable snapshot. The two locks pair deliberately: 6b-L16 = in-memory
top-level; 6b-L17 = on-disk deep.

Re-exports `RiskIntent` from types.py for back-compat (canonical home is
types.py per lock 6b-L12)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

from marketpulse.trading.types import OrderRequest, RiskIntent

__all__ = [
    "RiskIntent",
    "RiskResult",
    "RiskGate",
    "AlwaysApproveRiskGate",
]


_EMPTY_CONTEXT: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True)
class RiskResult:
    approved: bool
    reason: str
    gate_name: str = ""
    failed_gates: tuple[str, ...] = ()
    # Lock 6b-L16: top-level immutability. Gate authors pass plain dicts;
    # __post_init__ wraps in MappingProxyType so external mutation raises
    # TypeError. Nested dict mutation is still possible — that's
    # deliberately left to the normalize_for_json serialization boundary
    # (lock 6b-L17) which materializes deep copies.
    context: Mapping[str, Any] = field(
        default_factory=lambda: _EMPTY_CONTEXT,
    )

    def __post_init__(self) -> None:
        if isinstance(self.context, dict):
            object.__setattr__(self, "context", MappingProxyType(self.context))


class RiskGate(Protocol):
    def check_pre_trade(self, *, order_request: OrderRequest) -> RiskResult: ...


class AlwaysApproveRiskGate:
    """6a's default. Approves everything. 6b production paths use
    CompositeRiskGate; AlwaysApproveRiskGate remains for tests that
    exercise non-gate code paths."""

    def check_pre_trade(self, *, order_request: OrderRequest) -> RiskResult:
        return RiskResult(approved=True, reason="", gate_name="always_approve")
