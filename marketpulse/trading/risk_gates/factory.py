"""Phase 6b canonical composite factory (lock 6b-L15).

`build_standard_composite` is the SINGLE blessed builder for the 4-gate
production composite. The scheduler entrypoint (`paper_trading_tick.py`)
calls this; tests are free to instantiate `CompositeRiskGate(gates=[...])`
directly with whatever fakes they need.

Order matters for audit reproducibility — per_gate[*] entries appear in
this order in every ORDER_REJECTED row across the lifetime of the system.
Changing the order requires a coordinated migration of any downstream
consumer (6f UI, 6g recap). Keep it stable.
"""

from __future__ import annotations

from collections.abc import Callable

from marketpulse.trading.calendar import NYTradingCalendar
from marketpulse.trading.clock import Clock
from marketpulse.trading.risk_gates.composite import CompositeRiskGate
from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider
from marketpulse.trading.risk_gates.daily_loss import DailyLossGate
from marketpulse.trading.risk_gates.market_hours import MarketHoursGate
from marketpulse.trading.risk_gates.sector_exposure import SectorExposureGate
from marketpulse.trading.risk_gates.strategy_size import StrategySizeGate

__all__ = ["build_standard_composite"]


def build_standard_composite(
    *,
    config_provider: RiskConfigProvider,
    repository,
    calendar: NYTradingCalendar,
    clock: Clock,
    sector_provider: Callable[[str], str | None],
) -> CompositeRiskGate:
    """Build the canonical 4-gate composite. Order: market_hours,
    strategy_size, daily_loss, sector_exposure."""
    global_cfg = config_provider.global_config()
    return CompositeRiskGate(
        gates=(
            MarketHoursGate(
                cfg=global_cfg.market_hours, calendar=calendar, clock=clock,
            ),
            StrategySizeGate(provider=config_provider),
            DailyLossGate(
                cfg=global_cfg.daily_loss, repository=repository,
            ),
            SectorExposureGate(
                cfg=global_cfg.sector_exposure,
                repository=repository,
                sector_provider=sector_provider,
            ),
        ),
    )
