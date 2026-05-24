"""T7a tests: pure helpers for the IBKR ibapi-backed order adapter.

These tests exercise ONLY the side-effect-free helpers in
``marketpulse.broker.ibkr_order_client``. The ``IbkrOrderClient`` class
itself (with ``EClient``/``EWrapper`` machinery, threading, and bounded
waits) is T7b territory and is NOT imported here.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from marketpulse.broker.ibkr_order_client import (
    _build_contract,
    _build_order,
    _decimal_or_none,
    _map_order_status_event,
    _sanitize_raw,
)
from marketpulse.broker.order_types import BrokerOrderObservation

# --- _decimal_or_none ------------------------------------------------------


class TestDecimalOrNone:
    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "NaN",
            "nan",
            float("inf"),
            float("-inf"),
            float("nan"),
            "1.7976931348623157E308",  # IBKR "unset" sentinel
        ],
    )
    def test_none_like_inputs_return_none(self, value: object) -> None:
        assert _decimal_or_none(value) is None

    def test_numeric_unset_sentinel_returns_none(self) -> None:
        assert _decimal_or_none(1.7976931348623157e308) is None

    def test_string_decimal(self) -> None:
        assert _decimal_or_none("12.34") == Decimal("12.34")

    def test_float_decimal(self) -> None:
        assert _decimal_or_none(12.34) == Decimal("12.34")

    @pytest.mark.parametrize("value", [0, "0", "0.00"])
    def test_zero_inputs(self, value: object) -> None:
        assert _decimal_or_none(value) == Decimal("0")

    def test_returns_decimal_type(self) -> None:
        result = _decimal_or_none("3.14")
        assert isinstance(result, Decimal)

    def test_garbage_string_returns_none(self) -> None:
        assert _decimal_or_none("not-a-number") is None


# --- _sanitize_raw ---------------------------------------------------------


class TestSanitizeRaw:
    def test_returns_dict(self) -> None:
        out = _sanitize_raw({"a": 1})
        assert isinstance(out, dict)

    def test_deep_copy_not_same_object(self) -> None:
        src = {"a": {"b": 1}}
        out = _sanitize_raw(src)
        assert out is not src
        assert out["a"] is not src["a"]
        # Mutating output should not touch input
        out["a"]["b"] = 99
        assert src["a"]["b"] == 1

    @pytest.mark.parametrize(
        "key",
        [
            "token",
            "password",
            "secret",
            "session_id",
            "session",
            "auth",
            "cred",
            "credentials",
            "api_key",
            "apikey",
        ],
    )
    def test_redacts_sensitive_keys(self, key: str) -> None:
        out = _sanitize_raw({key: "shhhh"})
        assert out[key] == "[redacted]"

    def test_case_insensitive_redaction(self) -> None:
        out = _sanitize_raw({"Token": "abc", "PASSWORD": "p", "Api_Key": "k"})
        assert out["Token"] == "[redacted]"
        assert out["PASSWORD"] == "[redacted]"
        assert out["Api_Key"] == "[redacted]"

    def test_recursive_nested_dicts(self) -> None:
        out = _sanitize_raw(
            {
                "outer": {
                    "token": "abc",
                    "inner": {"password": "p", "ok": "keep"},
                },
                "ok": "keep",
            }
        )
        assert out["outer"]["token"] == "[redacted]"
        assert out["outer"]["inner"]["password"] == "[redacted]"
        assert out["outer"]["inner"]["ok"] == "keep"
        assert out["ok"] == "keep"

    def test_lists_of_dicts_are_sanitized(self) -> None:
        out = _sanitize_raw(
            {
                "events": [
                    {"token": "abc", "ok": "v1"},
                    {"secret": "sss", "ok": "v2"},
                ]
            }
        )
        assert out["events"][0]["token"] == "[redacted]"
        assert out["events"][0]["ok"] == "v1"
        assert out["events"][1]["secret"] == "[redacted]"
        assert out["events"][1]["ok"] == "v2"

    def test_scalar_passthrough(self) -> None:
        out = _sanitize_raw(
            {"n": 42, "f": 3.14, "b": True, "none": None, "s": "hello"}
        )
        assert out == {"n": 42, "f": 3.14, "b": True, "none": None, "s": "hello"}

    def test_non_serializable_values_are_repr(self) -> None:
        class Thing:
            def __repr__(self) -> str:
                return "<Thing!>"

        out = _sanitize_raw({"obj": Thing()})
        assert out["obj"] == "<Thing!>"

    def test_datetime_stringified(self) -> None:
        dt = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
        out = _sanitize_raw({"when": dt})
        assert isinstance(out["when"], str)
        assert "2026" in out["when"]


# --- _map_order_status_event ----------------------------------------------


class TestMapOrderStatusEvent:
    OBSERVED_AT = datetime(2026, 5, 24, 10, 0, tzinfo=UTC)

    def _call(self, **overrides: object) -> BrokerOrderObservation:
        kwargs = dict(
            observed_at=self.OBSERVED_AT,
            broker_order_id="123",
            status="Submitted",
            filled="0",
            remaining="100",
            avg_fill_price="0",
            perm_id="987",
            raw={"status": "Submitted"},
        )
        kwargs.update(overrides)
        return _map_order_status_event(**kwargs)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "status", ["Submitted", "PreSubmitted", "PendingSubmit", "ApiPending"]
    )
    def test_non_terminal_status_is_order_status_seen(self, status: str) -> None:
        obs = self._call(status=status)
        assert obs.event_type == "order_status_seen"
        assert obs.broker_status == status

    def test_filled_with_positive_qty(self) -> None:
        obs = self._call(status="Filled", filled="100", remaining="0", avg_fill_price="12.34")
        assert obs.event_type == "filled"
        assert obs.broker_status == "Filled"
        assert obs.filled_quantity == Decimal("100")
        assert obs.remaining_quantity == Decimal("0")
        assert obs.avg_fill_price == Decimal("12.34")

    def test_filled_status_with_zero_qty_is_not_filled(self) -> None:
        # Edge: status=Filled but filled=0 should not flip to filled event
        obs = self._call(status="Filled", filled="0", remaining="100")
        assert obs.event_type == "order_status_seen"

    @pytest.mark.parametrize("status", ["Cancelled", "ApiCancelled"])
    def test_cancelled(self, status: str) -> None:
        obs = self._call(status=status)
        assert obs.event_type == "cancelled"
        assert obs.broker_status == status

    def test_inactive_is_rejected(self) -> None:
        obs = self._call(status="Inactive")
        assert obs.event_type == "rejected"
        assert obs.broker_status == "Inactive"

    def test_raw_is_sanitized(self) -> None:
        obs = self._call(raw={"token": "abc", "status": "Submitted"})
        assert obs.raw["token"] == "[redacted]"
        assert obs.raw["status"] == "Submitted"

    def test_decimal_normalization_handles_unset_sentinel(self) -> None:
        obs = self._call(
            status="Submitted",
            filled="1.7976931348623157E308",
            remaining="100",
            avg_fill_price="1.7976931348623157E308",
        )
        assert obs.filled_quantity is None
        assert obs.avg_fill_price is None
        assert obs.remaining_quantity == Decimal("100")

    def test_broker_order_id_and_perm_id_preserved(self) -> None:
        obs = self._call(broker_order_id="42", perm_id="999")
        assert obs.broker_order_id == "42"
        assert obs.broker_perm_id == "999"


# --- _build_contract ------------------------------------------------------


class TestBuildContract:
    def test_stk_defaults(self) -> None:
        c = _build_contract("AAPL", "STK")
        assert c.symbol == "AAPL"
        assert c.secType == "STK"
        assert c.exchange == "SMART"
        assert c.currency == "USD"

    def test_non_stk_raises(self) -> None:
        with pytest.raises(ValueError, match="STK"):
            _build_contract("ESM6", "FUT")


# --- _build_order ---------------------------------------------------------


class TestBuildOrder:
    def test_basic_lmt(self) -> None:
        o = _build_order(
            side="BUY",
            quantity=Decimal("100"),
            order_type="LMT",
            limit_price=Decimal("12.34"),
            transmit=True,
            order_ref="MP-7B-1-deadbeef",
        )
        assert o.action == "BUY"
        assert Decimal(str(o.totalQuantity)) == Decimal("100")
        assert o.orderType == "LMT"
        assert Decimal(str(o.lmtPrice)) == Decimal("12.34")
        assert o.transmit is True
        assert o.orderRef == "MP-7B-1-deadbeef"
        assert o.tif == "DAY"

    def test_sell_side(self) -> None:
        o = _build_order(
            side="SELL",
            quantity=Decimal("50"),
            order_type="LMT",
            limit_price=Decimal("1.00"),
            transmit=False,
            order_ref="MP-7B-2-abc",
        )
        assert o.action == "SELL"
        assert o.transmit is False

    def test_non_lmt_raises(self) -> None:
        with pytest.raises(ValueError, match="LMT"):
            _build_order(
                side="BUY",
                quantity=Decimal("1"),
                order_type="MKT",
                limit_price=None,
                transmit=True,
                order_ref="x",
            )


# Sanity: math.inf still recognized via _decimal_or_none
def test_decimal_or_none_math_inf() -> None:
    assert _decimal_or_none(math.inf) is None
