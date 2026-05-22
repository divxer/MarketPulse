# Layer: pure
"""6b+T3: PriceProvider Protocol + ClosePrice + StubPriceProvider."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest


def test_close_price_dataclass_is_frozen_with_4_fields():
    from marketpulse.trading.price_provider import ClosePrice
    cp = ClosePrice(
        price=Decimal("100.123456"),
        price_date=date(2026, 5, 20),
        requested_date=date(2026, 5, 22),
        source="yfinance",
    )
    assert cp.price == Decimal("100.123456")
    assert cp.price_date == date(2026, 5, 20)
    assert cp.requested_date == date(2026, 5, 22)
    assert cp.source == "yfinance"
    # frozen — mutation should raise FrozenInstanceError (subclass of
    # AttributeError on 3.11+, dataclasses.FrozenInstanceError otherwise).
    from dataclasses import FrozenInstanceError
    with pytest.raises((FrozenInstanceError, AttributeError)):
        cp.price = Decimal("999")


def test_stub_price_provider_source_and_lookback_days():
    """Lock 6b+L8: provider exposes source + lookback_days."""
    from marketpulse.trading.price_provider import StubPriceProvider
    p = StubPriceProvider()
    assert p.source == "stub"
    assert p.lookback_days == 0


def test_stub_price_provider_rejects_default_kwarg():
    """Lock 6b+L3: StubPriceProvider has NO `default` parameter."""
    from marketpulse.trading.price_provider import StubPriceProvider
    with pytest.raises(TypeError):
        StubPriceProvider(default=Decimal("0"))   # should NOT be accepted


def test_stub_price_provider_map_only_lookup():
    from marketpulse.trading.price_provider import ClosePrice, StubPriceProvider
    cp_aapl = ClosePrice(
        price=Decimal("150.50"),
        price_date=date(2026, 5, 20),
        requested_date=date(2026, 5, 20),
        source="stub",
    )
    p = StubPriceProvider(map={("AAPL", date(2026, 5, 20)): cp_aapl})
    assert p.close_on_date(ticker="AAPL", on_date=date(2026, 5, 20)) is cp_aapl
    # Miss returns None — NO default fallback
    assert p.close_on_date(ticker="AAPL", on_date=date(2026, 5, 21)) is None
    assert p.close_on_date(ticker="MSFT", on_date=date(2026, 5, 20)) is None


def test_lookback_days_module_constant_is_10():
    from marketpulse.trading.price_provider import LOOKBACK_DAYS
    assert LOOKBACK_DAYS == 10
