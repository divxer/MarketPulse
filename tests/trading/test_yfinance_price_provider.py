# Layer: stateful
"""6b+T5: YFinancePriceProvider tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock


def test_yfinance_provider_source_is_yfinance():
    """Lock 6b+L8: source property."""
    from marketpulse.trading.price_provider import YFinancePriceProvider
    p = YFinancePriceProvider(client=MagicMock())
    assert p.source == "yfinance"


def test_yfinance_provider_lookback_days_default_10():
    """Lock 6b+L8: lookback_days property; default 10."""
    from marketpulse.trading.price_provider import YFinancePriceProvider
    p = YFinancePriceProvider(client=MagicMock())
    assert p.lookback_days == 10


def test_yfinance_provider_lookback_days_custom():
    from marketpulse.trading.price_provider import YFinancePriceProvider
    p = YFinancePriceProvider(client=MagicMock(), lookback_days=20)
    assert p.lookback_days == 20


def test_close_on_date_returns_close_price_with_provenance():
    """Happy path: Bar -> ClosePrice with all 4 fields."""
    from marketpulse.data.types import Bar
    from marketpulse.trading.price_provider import (
        ClosePrice,
        YFinancePriceProvider,
    )

    mock_client = MagicMock()
    mock_client.fetch_close_on_date.return_value = Bar(
        date=date(2026, 5, 22),
        open=149.0, high=151.0, low=148.5, close=150.50, volume=1000000,
    )
    p = YFinancePriceProvider(client=mock_client)

    result = p.close_on_date(ticker="AAPL", on_date=date(2026, 5, 22))

    assert isinstance(result, ClosePrice)
    assert result.price == Decimal("150.500000")    # quantized to 6dp
    assert result.price_date == date(2026, 5, 22)
    assert result.requested_date == date(2026, 5, 22)
    assert result.source == "yfinance"


def test_close_on_date_passes_lookback_days_to_client():
    """Provider's lookback_days threads through to YFinanceClient."""
    from marketpulse.data.types import Bar
    from marketpulse.trading.price_provider import YFinancePriceProvider

    mock_client = MagicMock()
    mock_client.fetch_close_on_date.return_value = Bar(
        date=date(2026, 5, 22),
        open=149.0, high=151.0, low=148.5, close=150.50, volume=1000000,
    )
    p = YFinancePriceProvider(client=mock_client, lookback_days=15)

    p.close_on_date(ticker="AAPL", on_date=date(2026, 5, 22))

    mock_client.fetch_close_on_date.assert_called_once_with(
        "AAPL", date(2026, 5, 22), lookback_days=15,
    )


def test_close_on_date_returns_none_when_client_returns_none():
    from marketpulse.trading.price_provider import YFinancePriceProvider
    mock_client = MagicMock()
    mock_client.fetch_close_on_date.return_value = None
    p = YFinancePriceProvider(client=mock_client)
    assert p.close_on_date(ticker="ZZZZ", on_date=date(2026, 5, 22)) is None


def test_close_on_date_quantizes_high_precision_close_to_6dp():
    """Lock 6b+L14: 100.123456789 → quantize HALF_EVEN → 100.123457."""
    from marketpulse.data.types import Bar
    from marketpulse.trading.price_provider import YFinancePriceProvider

    mock_client = MagicMock()
    mock_client.fetch_close_on_date.return_value = Bar(
        date=date(2026, 5, 22),
        open=100.0, high=100.0, low=100.0, close=100.123456789, volume=1000,
    )
    p = YFinancePriceProvider(client=mock_client)

    result = p.close_on_date(ticker="AAPL", on_date=date(2026, 5, 22))

    assert result.price == Decimal("100.123457")
    # Verify exact Decimal representation (no float artifacts)
    assert str(result.price) == "100.123457"


def test_close_on_date_rollback_preserves_price_date():
    """If client returns a Bar with date < requested, ClosePrice.price_date
    reflects the roll-back."""
    from marketpulse.data.types import Bar
    from marketpulse.trading.price_provider import YFinancePriceProvider

    mock_client = MagicMock()
    mock_client.fetch_close_on_date.return_value = Bar(
        date=date(2026, 5, 22),    # Friday
        open=149.0, high=151.0, low=148.5, close=150.50, volume=1000,
    )
    p = YFinancePriceProvider(client=mock_client)

    result = p.close_on_date(
        ticker="AAPL", on_date=date(2026, 5, 26),    # Tuesday after Memorial Day
    )

    assert result.price_date == date(2026, 5, 22)     # rolled back
    assert result.requested_date == date(2026, 5, 26)
