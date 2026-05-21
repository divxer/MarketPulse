# Layer: invariant
"""6a-1: PriceProvider Protocol + StubPriceProvider determinism."""

from __future__ import annotations

from datetime import date
from decimal import Decimal


def test_stub_price_provider_returns_configured_value():
    from marketpulse.trading.price_provider import StubPriceProvider

    p = StubPriceProvider(map={("AAPL", date(2026, 5, 28)): Decimal("155.00")})
    assert p.horizon_price(ticker="AAPL", horizon_date=date(2026, 5, 28)) == Decimal("155.00")


def test_stub_price_provider_default_on_missing():
    from marketpulse.trading.price_provider import StubPriceProvider

    p = StubPriceProvider(default=Decimal("100.00"))
    assert p.horizon_price(ticker="UNKNOWN", horizon_date=date(2026, 5, 28)) == Decimal("100.00")


def test_stub_price_provider_returns_none_when_no_match_no_default():
    from marketpulse.trading.price_provider import StubPriceProvider

    p = StubPriceProvider()
    assert p.horizon_price(ticker="X", horizon_date=date(2026, 5, 28)) is None
