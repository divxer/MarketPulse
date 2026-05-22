# Layer: pure
"""6b-T14a: audit_json.normalize_for_json shared util tests (lock 6b-L17)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, time
from decimal import Decimal


def test_normalize_decimal_to_str():
    from marketpulse.trading.audit_json import normalize_for_json
    assert normalize_for_json(Decimal("1.5")) == "1.5"
    assert normalize_for_json(Decimal("-500.123456")) == "-500.123456"


def test_normalize_datetime_to_isoformat():
    from marketpulse.trading.audit_json import normalize_for_json
    dt = datetime(2026, 5, 21, 14, 30, tzinfo=UTC)
    assert normalize_for_json(dt) == dt.isoformat()


def test_normalize_date_to_isoformat():
    from marketpulse.trading.audit_json import normalize_for_json
    assert normalize_for_json(date(2026, 5, 21)) == "2026-05-21"


def test_normalize_time_to_isoformat():
    from marketpulse.trading.audit_json import normalize_for_json
    assert normalize_for_json(time(18, 0)) == "18:00:00"


def test_normalize_dict_recursive():
    from marketpulse.trading.audit_json import normalize_for_json
    d = {"a": Decimal("1"), "b": {"c": Decimal("2")}}
    assert normalize_for_json(d) == {"a": "1", "b": {"c": "2"}}


def test_normalize_list_recursive():
    from marketpulse.trading.audit_json import normalize_for_json
    assert normalize_for_json([Decimal("1"), Decimal("2")]) == ["1", "2"]


def test_normalize_tuple_to_list():
    from marketpulse.trading.audit_json import normalize_for_json
    assert normalize_for_json((Decimal("1"), Decimal("2"))) == ["1", "2"]


def test_normalize_dataclass_to_dict():
    from marketpulse.trading.audit_json import normalize_for_json

    @dataclasses.dataclass(frozen=True)
    class _X:
        a: Decimal
        b: date

    x = _X(a=Decimal("3.14"), b=date(2026, 5, 21))
    assert normalize_for_json(x) == {"a": "3.14", "b": "2026-05-21"}


def test_normalize_mapping_proxy():
    """MappingProxyType (lock 6b-L16) round-trips through normalize."""
    from types import MappingProxyType

    from marketpulse.trading.audit_json import normalize_for_json
    proxy = MappingProxyType({"a": Decimal("1")})
    assert normalize_for_json(proxy) == {"a": "1"}


def test_normalize_passthrough_primitives():
    from marketpulse.trading.audit_json import normalize_for_json
    assert normalize_for_json("hello") == "hello"
    assert normalize_for_json(42) == 42
    assert normalize_for_json(3.14) == 3.14
    assert normalize_for_json(True) is True
    assert normalize_for_json(None) is None


def test_normalize_result_is_json_dumpable():
    import json

    from marketpulse.trading.audit_json import normalize_for_json
    raw = {
        "decimal": Decimal("1.5"),
        "datetime": datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        "nested": {"date": date(2026, 5, 21), "list": [Decimal("2")]},
    }
    out = normalize_for_json(raw)
    json.dumps(out)  # must not raise
