"""CompositeRiskGate — run-all + deny-if-any + exception=deny + audit-all.

Lock 6b-L2 forbids fail-fast at runtime: every child gate runs even if an
earlier one denied, so audit context lists ALL gate results.

Lock 6b-L15: composite uses **dependency inversion**. `__init__` accepts
`gates: Sequence[RiskGate]` and does no construction itself. The
composition root (`paper_trading_tick.py`) owns the gate list; in
production it calls `build_standard_composite(...)` (see factory.py) to
materialize the canonical 4-gate composite. Tests construct directly with
fakes for individual gates without needing to fake deeper deps.

Lock 6b-L17: per-gate result serialization for the audit ledger routes
through `marketpulse.trading.audit_json.normalize_for_json`. No inline
normalization — single source of truth across the codebase.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from marketpulse.trading.audit_json import normalize_for_json
from marketpulse.trading.risk_gate import RiskGate, RiskResult
from marketpulse.trading.types import OrderRequest, RiskIntent

__all__ = ["CompositeRiskGate"]


class CompositeRiskGate:
    name = "composite"

    def __init__(self, *, gates: Sequence[RiskGate]) -> None:
        # Defensive copy into a tuple — composition root owns the list,
        # composite owns the runtime ordering. Tuple makes accidental
        # in-place mutation impossible (helps with audit determinism).
        self._gates: tuple[RiskGate, ...] = tuple(gates)

    def check_pre_trade(self, *, order_request: OrderRequest) -> RiskResult:
        # === Composite-level RiskIntent handling ===
        # CLOSE/REDUCE bypass every gate without running them — saves DB
        # reads and clock reads, and prevents an exploding gate from
        # blocking risk-reducing actions (lock 6b-L1).
        if order_request.risk_intent in (RiskIntent.CLOSE, RiskIntent.REDUCE):
            return RiskResult(approved=True, gate_name=self.name, reason="")
        if order_request.risk_intent == RiskIntent.FLIP:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="unsupported_risk_intent",
            )

        # === Run all gates, capturing exceptions as per-gate denies ===
        all_results: list[RiskResult] = []
        for gate in self._gates:
            try:
                r = gate.check_pre_trade(order_request=order_request)
            except Exception as e:  # noqa: BLE001 — fail-closed catches everything
                r = RiskResult(
                    approved=False,
                    reason=f"{getattr(gate, 'name', type(gate).__name__)}_error",
                    gate_name=getattr(gate, "name", type(gate).__name__),
                    context={
                        "error_type": type(e).__name__,
                        "error": str(e),
                    },
                )
            all_results.append(r)

        failed = [r for r in all_results if not r.approved]
        per_gate = [_serialize_result(r) for r in all_results]

        if failed:
            return RiskResult(
                approved=False,
                reason="; ".join(r.reason for r in failed),
                gate_name=failed[0].gate_name,
                failed_gates=tuple(r.gate_name for r in failed),
                context={"per_gate": per_gate},
            )
        return RiskResult(
            approved=True, gate_name=self.name, reason="",
            context={"per_gate": per_gate},
        )


def _serialize_result(r: RiskResult) -> dict[str, Any]:
    """Serialize a RiskResult into a JSON-safe dict for audit storage
    (locks 6b-L10 + 6b-L17). Delegates all normalization to the shared
    audit_json util — single source of truth."""
    return normalize_for_json(r)
