"""YFinanceClient.fetch_fundamentals — resilience against yfinance errors.

Regression test for 2026-05-26 incident: Yahoo Finance metadata response
omitted ``currentTradingPeriod`` key, causing yfinance 1.3.0's lazy
``Ticker.info`` accessor to raise ``KeyError('currentTradingPeriod')`` from
``scrapers/quote.py:134``. The error bypassed our @_retry filter (which
only catches network errors) and crashed the entire daily recap.

Contract: any exception from ``yf.Ticker(ticker).info`` must yield an
empty Fundamentals (all fields None) so the recap can continue.
"""
# Layer: unit
from __future__ import annotations

from unittest.mock import MagicMock, patch

from marketpulse.data.yfinance_client import YFinanceClient


def test_fetch_fundamentals_handles_key_error_from_info():
    """Reproduce the production failure: info access raises KeyError."""
    client = YFinanceClient()
    fake_ticker = MagicMock()
    type(fake_ticker).info = property(
        lambda self: (_ for _ in ()).throw(KeyError("currentTradingPeriod"))
    )
    with patch("marketpulse.data.yfinance_client.yf.Ticker", return_value=fake_ticker):
        result = client.fetch_fundamentals("AAPL")
    # All fields None — downstream code already None-checks each one
    assert result.ticker == "AAPL"
    assert result.market_cap is None
    assert result.pe_ratio is None
    assert result.eps is None
    assert result.sector is None
    assert result.industry is None


def test_fetch_fundamentals_handles_arbitrary_exception():
    """Any exception type — not just KeyError — must be swallowed."""
    client = YFinanceClient()
    fake_ticker = MagicMock()
    type(fake_ticker).info = property(
        lambda self: (_ for _ in ()).throw(RuntimeError("yahoo down"))
    )
    with patch("marketpulse.data.yfinance_client.yf.Ticker", return_value=fake_ticker):
        result = client.fetch_fundamentals("AAPL")
    assert result.market_cap is None


def test_fetch_fundamentals_passes_through_when_info_works():
    """Sanity: when info returns normally, fields are extracted."""
    client = YFinanceClient()
    fake_ticker = MagicMock()
    fake_ticker.info = {
        "marketCap": 1_000_000_000,
        "trailingPE": 22.5,
        "trailingEps": 5.1,
        "sector": "Technology",
        "industry": "Software",
    }
    with patch("marketpulse.data.yfinance_client.yf.Ticker", return_value=fake_ticker):
        result = client.fetch_fundamentals("AAPL")
    assert result.market_cap == 1_000_000_000
    assert result.pe_ratio == 22.5
    assert result.sector == "Technology"
