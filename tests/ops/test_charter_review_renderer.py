# Layer: test
"""PR3b — renderer formatting tests."""
from __future__ import annotations

from decimal import Decimal

from marketpulse.ops.charter_review_renderer import (
    DELTA_PRIOR_NA,
    VALUE_NA,
    _fmt_delta_index,
    _fmt_delta_int,
    _fmt_delta_pp,
    _fmt_index,
    _fmt_int,
    _fmt_pct,
)


def test_fmt_pct_positive():
    assert _fmt_pct(Decimal("0.032")) == "3.2%"


def test_fmt_pct_negative():
    assert _fmt_pct(Decimal("-0.014")) == "-1.4%"


def test_fmt_pct_none():
    assert _fmt_pct(None) == VALUE_NA


def test_fmt_int_none():
    assert _fmt_int(None) == VALUE_NA


def test_fmt_int_zero():
    assert _fmt_int(0) == "0"


def test_fmt_delta_pp_positive():
    s = _fmt_delta_pp(Decimal("0.032"), Decimal("0.018"))
    assert s == "+1.4 pp vs prior week"


def test_fmt_delta_pp_negative_uses_unicode_minus():
    s = _fmt_delta_pp(Decimal("0.012"), Decimal("0.030"))
    assert s == "−1.8 pp vs prior week"


def test_fmt_delta_pp_prior_na():
    assert _fmt_delta_pp(Decimal("0.032"), None) == DELTA_PRIOR_NA


def test_fmt_delta_pp_both_na():
    assert _fmt_delta_pp(None, None) == DELTA_PRIOR_NA


def test_fmt_delta_int_positive():
    assert _fmt_delta_int(7, 2) == "+5 vs prior week"


def test_fmt_delta_int_negative():
    assert _fmt_delta_int(2, 7) == "−5 vs prior week"


def test_fmt_delta_int_prior_na():
    assert _fmt_delta_int(7, None) == DELTA_PRIOR_NA


def test_fmt_index_basic():
    assert _fmt_index(Decimal("1.041")) == "1.041"
    assert _fmt_index(Decimal("1")) == "1.000"


def test_fmt_index_none():
    assert _fmt_index(None) == VALUE_NA


def test_fmt_delta_index_positive():
    s = _fmt_delta_index(Decimal("1.041"), Decimal("1.009"))
    assert s == "+0.032 vs prior week"


def test_fmt_delta_index_negative():
    s = _fmt_delta_index(Decimal("1.009"), Decimal("1.041"))
    assert s == "−0.032 vs prior week"


def test_fmt_delta_index_prior_na():
    assert _fmt_delta_index(Decimal("1.041"), None) == DELTA_PRIOR_NA
