# Layer: test
"""PR4 — portfolio_vs_spy_view pure presenter."""
from __future__ import annotations

from decimal import Decimal

from marketpulse.portfolio.portfolio_vs_spy_view import (
    _fmt_excess_label,
    _fmt_index_label,
)


def test_fmt_excess_label_positive():
    assert _fmt_excess_label(Decimal("0.032")) == "+3.2%"


def test_fmt_excess_label_negative():
    assert _fmt_excess_label(Decimal("-0.014")) == "-1.4%"


def test_fmt_excess_label_zero():
    assert _fmt_excess_label(Decimal("0")) == "+0.0%"


def test_fmt_excess_label_none():
    assert _fmt_excess_label(None) == "N/A"


def test_fmt_index_label():
    assert _fmt_index_label(Decimal("1.0413")) == "1.041"


def test_fmt_index_label_none():
    assert _fmt_index_label(None) == "N/A"
