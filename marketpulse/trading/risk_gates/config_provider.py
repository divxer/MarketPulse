"""RiskConfigProvider + 5 frozen config dataclasses (lock 6b-L3, 6b-L14).

The provider is the SINGLE site that reads YAML for risk configuration.
Gates NEVER read YAML directly — they take provider methods or pre-built
config dataclasses at construction time.

Lock 6b-L14 scope discipline:
  - Reads ONLY the `risk:` block of each strategy YAML; never `signals:`,
    `sizing:`, or other strategy-execution blocks (those remain owned by
    marketpulse/strategies/loader.py).
  - Strategy lookup key is the YAML filename stem (== Strategy.name).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from pathlib import Path

__all__ = [
    "MarketHoursConfig",
    "DailyLossConfig",
    "SectorExposureConfig",
    "RiskGateConfig",
    "StrategyRiskConfig",
    "RiskConfigProvider",
]


@dataclass(frozen=True)
class MarketHoursConfig:
    enabled: bool
    exchange: str
    allow_regular_session: bool       # 09:30-16:00 NY inclusive
    allow_post_close: bool            # 16:00-post_close_until NY (open-left, closed-right)
    post_close_until: time            # parsed from "HH:MM"
    allow_premarket: bool             # 04:00-09:30 NY inclusive-left, exclusive-right


@dataclass(frozen=True)
class DailyLossConfig:
    enabled: bool
    daily_loss_limit: Decimal         # POSITIVE Decimal; deny when realized <= -limit


@dataclass(frozen=True)
class SectorExposureConfig:
    enabled: bool
    max_sector_exposure_pct: float
    configured_max_capital_in_use: Decimal  # FIXED denominator (lock 6b-L4)


@dataclass(frozen=True)
class RiskGateConfig:
    market_hours: MarketHoursConfig
    daily_loss: DailyLossConfig
    sector_exposure: SectorExposureConfig


@dataclass(frozen=True)
class StrategyRiskConfig:
    max_position_notional: Decimal | None  # None → StrategySizeGate fail-closed (6b-L9)


class RiskConfigProvider:
    """Single parser. Gates NEVER read YAML directly (locks 6b-L3, 6b-L14)."""

    def __init__(
        self,
        *,
        global_cfg: RiskGateConfig,
        strategy_cfgs: dict[str, StrategyRiskConfig],
    ) -> None:
        self._global = global_cfg
        self._strategies = dict(strategy_cfgs)

    def global_config(self) -> RiskGateConfig:
        return self._global

    def strategy_config(self, strategy: str) -> StrategyRiskConfig | None:
        """Returns None when strategy has no `risk:` block. Triggers
        StrategySizeGate fail-closed (6b-L9)."""
        return self._strategies.get(strategy)

    @classmethod
    def from_yaml(
        cls,
        *,
        global_path: Path,
        strategies_dir: Path,
    ) -> RiskConfigProvider:
        # Filled in at T5 (global) and T6 (strategy YAMLs).
        raise NotImplementedError("RiskConfigProvider.from_yaml — see T5/T6")
