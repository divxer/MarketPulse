# Layer: pure
"""6b-T15: build_standard_composite factory tests (lock 6b-L15)."""

from __future__ import annotations

from datetime import time
from decimal import Decimal


class _StubProvider:
    def global_config(self):
        from marketpulse.trading.risk_gates.config_provider import (
            DailyLossConfig,
            MarketHoursConfig,
            RiskGateConfig,
            SectorExposureConfig,
        )
        return RiskGateConfig(
            market_hours=MarketHoursConfig(
                enabled=True, exchange="XNYS",
                allow_regular_session=True, allow_post_close=True,
                post_close_until=time(18, 0), allow_premarket=False,
            ),
            daily_loss=DailyLossConfig(enabled=True, daily_loss_limit=Decimal("500")),
            sector_exposure=SectorExposureConfig(
                enabled=True, max_sector_exposure_pct=0.35,
                configured_max_capital_in_use=Decimal("10000"),
            ),
        )

    def strategy_config(self, strategy):
        return None  # not exercised by this test


class _StubRepo:
    def today_realized_pnl(self, *, tick_date):
        return Decimal("0")

    def sector_exposure_notional(self, *, sector_provider):
        return {}


class _FakeClock:
    def now(self):
        from datetime import UTC, datetime
        return datetime(2026, 5, 21, 21, 30, tzinfo=UTC)


def test_factory_builds_4_gates_in_canonical_order():
    """Lock 6b-L15: factory is the single canonical builder. Order matters
    for audit reproducibility — operators reading per_gate[*] entries
    expect a stable order."""
    from marketpulse.trading.calendar import NYTradingCalendar
    from marketpulse.trading.risk_gates.factory import build_standard_composite

    composite = build_standard_composite(
        config_provider=_StubProvider(),
        repository=_StubRepo(),
        calendar=NYTradingCalendar(),
        clock=_FakeClock(),
        sector_provider=lambda t: "Technology",
    )
    names = [g.name for g in composite._gates]
    assert names == [
        "market_hours", "strategy_size", "daily_loss", "sector_exposure",
    ]
