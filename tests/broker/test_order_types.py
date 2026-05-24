from __future__ import annotations

from decimal import Decimal

import pytest

from marketpulse.broker.order_types import (
    BrokerOrderRequest,
    build_order_ref,
    classify_order_account,
)
from marketpulse.config import Settings


def test_classify_order_account_accepts_only_du_paper():
    assert classify_order_account("DU123456") == "paper"
    assert classify_order_account("DUE411848") == "paper"
    assert classify_order_account("U123456") == "live"
    assert classify_order_account("ABC123") == "unknown"
    assert classify_order_account("") == "unknown"
    assert classify_order_account(None) == "unknown"


def test_order_ref_is_short_and_contains_intent():
    ref = build_order_ref(intent_id=123, local_idempotency_key="abcdef1234567890")
    assert ref.startswith("MP-7B-123-")
    short_key = ref.rsplit("-", 1)[-1]
    assert len(short_key) == 8
    assert len(ref) <= 32


def test_order_ref_rejects_too_long_intent_id():
    with pytest.raises(ValueError, match="orderRef exceeds"):
        build_order_ref(intent_id=12345678901234567890, local_idempotency_key="abcdef123456")


def test_broker_order_request_rejects_non_mvp_order_shape():
    with pytest.raises(ValueError, match="STK"):
        BrokerOrderRequest(
            account_id="DU123456",
            symbol="AAPL",
            asset_class="OPT",
            side="BUY",
            quantity=Decimal("1"),
            order_type="LMT",
            limit_price=Decimal("1.00"),
            transmit=False,
            local_idempotency_key="key-1",
        )
    with pytest.raises(ValueError, match="LMT"):
        BrokerOrderRequest(
            account_id="DU123456",
            symbol="AAPL",
            asset_class="STK",
            side="BUY",
            quantity=Decimal("1"),
            order_type="MKT",
            limit_price=Decimal("1.00"),
            transmit=False,
            local_idempotency_key="key-2",
        )
    with pytest.raises(ValueError, match="limit_price"):
        BrokerOrderRequest(
            account_id="DU123456",
            symbol="AAPL",
            asset_class="STK",
            side="BUY",
            quantity=Decimal("1"),
            order_type="LMT",
            limit_price=None,
            transmit=False,
            local_idempotency_key="key-3",
        )
    with pytest.raises(ValueError, match="limit_price must be positive"):
        BrokerOrderRequest(
            account_id="DU123456",
            symbol="AAPL",
            asset_class="STK",
            side="BUY",
            quantity=Decimal("1"),
            order_type="LMT",
            limit_price=Decimal("0"),
            transmit=False,
            local_idempotency_key="key-4",
        )
    with pytest.raises(ValueError, match="quantity must be positive"):
        BrokerOrderRequest(
            account_id="DU123456",
            symbol="AAPL",
            asset_class="STK",
            side="BUY",
            quantity=Decimal("0"),
            order_type="LMT",
            limit_price=Decimal("1.00"),
            transmit=False,
            local_idempotency_key="key-5",
        )


def test_broker_order_request_is_frozen():
    request = BrokerOrderRequest(
        account_id="DU123456",
        symbol="AAPL",
        asset_class="STK",
        side="BUY",
        quantity=Decimal("1"),
        order_type="LMT",
        limit_price=Decimal("1.00"),
        transmit=False,
        local_idempotency_key="ok",
    )
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        request.symbol = "MSFT"  # type: ignore[misc]


def test_settings_have_order_defaults(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "x" * 16)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    settings = Settings()
    assert settings.ibkr_order_host == "127.0.0.1"
    assert settings.ibkr_order_port == 7497
    assert settings.ibkr_order_client_id == 72
    assert settings.ibkr_order_connect_timeout_seconds == 10
    assert settings.ibkr_order_next_valid_id_timeout_seconds == 10
    assert settings.ibkr_order_observation_timeout_seconds == 15
