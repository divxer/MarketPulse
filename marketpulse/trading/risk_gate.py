"""RiskGate Protocol + 6a's AlwaysApproveRiskGate stub.

6a ships ONLY the Protocol + AlwaysApproveRiskGate stub. 6b adds real
implementations (sector cap, correlation cap, daily loss limit,
market-hours). AlwaysApproveRiskGate approves all requests; the kill
switch is enforced separately BEFORE the risk gate in
ForwardExecutionEngine.place_order (NOT inside this gate). See lock
6a-L3 for fail-closed exception semantics in the engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from marketpulse.trading.types import OrderRequest


@dataclass(frozen=True)
class RiskResult:
    approved: bool
    reason: str
    gate_name: str = ""


class RiskGate(Protocol):
    def check_pre_trade(self, *, order_request: OrderRequest) -> RiskResult: ...


class AlwaysApproveRiskGate:
    """6a's default. Approves everything. 6b replaces this at the DI seam
    with real composite gates."""

    def check_pre_trade(self, *, order_request: OrderRequest) -> RiskResult:
        return RiskResult(approved=True, reason="", gate_name="always_approve")
