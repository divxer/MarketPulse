"""StrategySizeGate — denies OPEN/ADD orders where
event_price * quantity exceeds the strategy's max_position_notional.

Lock 6b-L9: missing strategy risk config → fail-closed deny
`missing_strategy_risk_config`. No infinite-cap default.

Op-test #8: boundary semantic is strict-greater — proposed == cap is
APPROVED; only proposed > cap denies."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from marketpulse.trading.risk_gate import RiskResult
from marketpulse.trading.risk_gates.config_provider import StrategyRiskConfig
from marketpulse.trading.types import OrderRequest, RiskIntent

__all__ = ["StrategySizeGate", "_StrategyConfigSource"]


class _StrategyConfigSource(Protocol):
    """Minimal contract the gate needs from the config provider."""
    def strategy_config(self, strategy: str) -> StrategyRiskConfig | None: ...


class StrategySizeGate:
    name = "strategy_size"

    def __init__(self, *, provider: _StrategyConfigSource) -> None:
        self._provider = provider

    def check_pre_trade(self, *, order_request: OrderRequest) -> RiskResult:
        if order_request.risk_intent in (RiskIntent.CLOSE, RiskIntent.REDUCE):
            return RiskResult(approved=True, gate_name=self.name, reason="")
        if order_request.risk_intent == RiskIntent.FLIP:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="unsupported_risk_intent",
            )

        cfg = self._provider.strategy_config(order_request.strategy)
        if cfg is None or cfg.max_position_notional is None:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="missing_strategy_risk_config",
                context={"strategy": order_request.strategy},
            )

        proposed = order_request.event_price * Decimal(order_request.quantity)
        if proposed > cfg.max_position_notional:
            return RiskResult(
                approved=False, gate_name=self.name,
                reason="strategy_size_exceeded",
                context={
                    "strategy": order_request.strategy,
                    "proposed": str(proposed),
                    "limit": str(cfg.max_position_notional),
                },
            )
        return RiskResult(approved=True, gate_name=self.name, reason="")
