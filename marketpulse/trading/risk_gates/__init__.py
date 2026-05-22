"""Phase 6b risk gates package.

CompositeRiskGate runs 4 deterministic pre-trade gates: MarketHoursGate,
StrategySizeGate, DailyLossGate, SectorExposureGate. Block risk-increasing
actions only — CLOSE/REDUCE bypass all gates (lock 6b-L1). KillSwitch
remains an emergency global halt OUTSIDE this principle scope (lock
clarification in spec § 2)."""

from __future__ import annotations

from marketpulse.trading.risk_gates.config_provider import (
    DailyLossConfig,
    MarketHoursConfig,
    RiskConfigProvider,
    RiskGateConfig,
    SectorExposureConfig,
    StrategyRiskConfig,
)

__all__ = [
    "DailyLossConfig",
    "MarketHoursConfig",
    "RiskConfigProvider",
    "RiskGateConfig",
    "SectorExposureConfig",
    "StrategyRiskConfig",
]
