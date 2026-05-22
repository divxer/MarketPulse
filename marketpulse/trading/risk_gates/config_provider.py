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

import yaml

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
        global_cfg = _parse_global_yaml(global_path)
        strategy_cfgs = _parse_strategy_dir(strategies_dir)
        return cls(global_cfg=global_cfg, strategy_cfgs=strategy_cfgs)


def _parse_global_yaml(path: Path) -> RiskGateConfig:
    if not path.exists():
        raise FileNotFoundError(
            f"risk_gates global config not found: {path}",
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping")
    for key in ("market_hours", "daily_loss", "sector_exposure"):
        if key not in data:
            raise ValueError(f"{path}: missing required top-level key {key!r}")
    return RiskGateConfig(
        market_hours=_parse_market_hours(data["market_hours"], path),
        daily_loss=_parse_daily_loss(data["daily_loss"], path),
        sector_exposure=_parse_sector_exposure(data["sector_exposure"], path),
    )


def _parse_market_hours(d: dict, path: Path) -> MarketHoursConfig:
    required = (
        "enabled", "exchange", "allow_regular_session",
        "allow_post_close", "post_close_until", "allow_premarket",
    )
    for k in required:
        if k not in d:
            raise ValueError(f"{path}: market_hours missing {k!r}")
    hh, mm = str(d["post_close_until"]).split(":")
    return MarketHoursConfig(
        enabled=bool(d["enabled"]),
        exchange=str(d["exchange"]),
        allow_regular_session=bool(d["allow_regular_session"]),
        allow_post_close=bool(d["allow_post_close"]),
        post_close_until=time(int(hh), int(mm)),
        allow_premarket=bool(d["allow_premarket"]),
    )


def _parse_daily_loss(d: dict, path: Path) -> DailyLossConfig:
    for k in ("enabled", "daily_loss_limit"):
        if k not in d:
            raise ValueError(f"{path}: daily_loss missing {k!r}")
    limit = Decimal(str(d["daily_loss_limit"]))
    if limit < 0:
        raise ValueError(
            f"{path}: daily_loss.daily_loss_limit must be non-negative "
            f"(got {limit})",
        )
    return DailyLossConfig(enabled=bool(d["enabled"]), daily_loss_limit=limit)


def _parse_sector_exposure(d: dict, path: Path) -> SectorExposureConfig:
    for k in ("enabled", "max_sector_exposure_pct", "configured_max_capital_in_use"):
        if k not in d:
            raise ValueError(f"{path}: sector_exposure missing {k!r}")
    pct = float(d["max_sector_exposure_pct"])
    if not 0.0 <= pct <= 1.0:
        raise ValueError(
            f"{path}: sector_exposure.max_sector_exposure_pct must be in [0,1] "
            f"(got {pct})",
        )
    cap = Decimal(str(d["configured_max_capital_in_use"]))
    if cap <= 0:
        raise ValueError(
            f"{path}: sector_exposure.configured_max_capital_in_use must be > 0",
        )
    return SectorExposureConfig(
        enabled=bool(d["enabled"]),
        max_sector_exposure_pct=pct,
        configured_max_capital_in_use=cap,
    )


def _parse_strategy_dir(strategies_dir: Path) -> dict[str, StrategyRiskConfig]:
    """Lock 6b-L14: strategy lookup key is YAML filename stem. Reads ONLY
    the `risk:` block — never `signals`, `sizing`, or other strategy-
    execution blocks.

    Behavior matrix:
      - file has no `risk:` key       → strategy NOT registered
                                        (strategy_config(stem) → None,
                                        triggers fail-closed via 6b-L9)
      - file has `risk: {}` empty     → registered with
                                        max_position_notional=None
                                        (still fail-closed via 6b-L9)
      - file has `risk: {max_position_notional: N}` → registered with
                                        Decimal(N) (must be >= 0)
    """
    out: dict[str, StrategyRiskConfig] = {}
    if not strategies_dir.exists():
        return out
    for yaml_path in sorted(strategies_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(
                f"{yaml_path}: top-level YAML must be a mapping",
            )
        if "risk" not in data:
            continue  # not registered; fail-closed via 6b-L9
        risk_block = data["risk"]
        if risk_block is None:
            risk_block = {}
        if not isinstance(risk_block, dict):
            raise ValueError(
                f"{yaml_path}: `risk:` must be a mapping, got "
                f"{type(risk_block).__name__}",
            )
        raw = risk_block.get("max_position_notional")
        if raw is None:
            cfg = StrategyRiskConfig(max_position_notional=None)
        else:
            try:
                limit = Decimal(str(raw))
            except Exception as e:
                raise ValueError(
                    f"{yaml_path}: risk.max_position_notional must parse as "
                    f"Decimal (got {raw!r}): {e}",
                ) from e
            if limit < 0:
                raise ValueError(
                    f"{yaml_path}: risk.max_position_notional must be >= 0 "
                    f"(got {limit})",
                )
            cfg = StrategyRiskConfig(max_position_notional=limit)
        out[yaml_path.stem] = cfg
    return out
