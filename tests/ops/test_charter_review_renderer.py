# Layer: test
"""PR3b — renderer formatting tests."""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from marketpulse.ops.charter_review_renderer import (
    DELTA_PRIOR_NA,
    REASON_MAX_DISPLAY_LEN,
    VALUE_NA,
    _fmt_delta_index,
    _fmt_delta_int,
    _fmt_delta_pp,
    _fmt_index,
    _fmt_int,
    _fmt_pct,
    _fmt_reason,
    render_charter_review,
)
from marketpulse.ops.charter_review_types import (
    CharterReviewPayload,
    DiagnosticsWeek,
    DiagnosticWeek,
    NorthStarWeek,
    OperationalFloor,
    SnapshotAppendix,
    WeekWindow,
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


def test_fmt_reason_strips_newlines_and_carriage_returns():
    assert _fmt_reason("a\nb\rc") == "a b c"


def test_fmt_reason_escapes_pipe():
    # input is the literal 3-char string "a|b"; output is the 4-char "a\|b"
    # which Python literal expresses as "a\\|b".
    assert _fmt_reason("a|b") == "a\\|b"


def test_fmt_reason_truncates_long_input():
    src = "x" * (REASON_MAX_DISPLAY_LEN + 50)
    out = _fmt_reason(src)
    assert out == "x" * REASON_MAX_DISPLAY_LEN + "…"


def test_fmt_reason_normalization_order_locked():
    # Replace newline first (becomes space), THEN escape pipe, THEN truncate.
    src = "a|b\nc" + ("z" * REASON_MAX_DISPLAY_LEN)
    out = _fmt_reason(src)
    assert out.endswith("…")
    assert "a\\|b c" in out


def _diag(value=None, observations=0, top_reasons=()):
    return DiagnosticWeek(
        value=value, observations=observations, top_reasons=tuple(top_reasons),
    )


def _diags(tick=None, rej=None, count=None, eng=None):
    return DiagnosticsWeek(
        tick_success_rate=tick or _diag(),
        order_rejection_rate=rej or _diag(),
        paper_trade_count=count or _diag(),
        engine_invariant_errors=eng or _diag(),
    )


def _week(monday: date, days_observed: int = 0) -> WeekWindow:
    sunday = date.fromordinal(monday.toordinal() + 6)
    return WeekWindow(
        week_start=monday, week_end=sunday,
        trading_days_observed=days_observed,
    )


def _ns(week: WeekWindow, *, excess_return=None, portfolio_index=None,
        spy_index=None, coverage_ratio=None, is_sufficient=False,
        first=None, last=None) -> NorthStarWeek:
    return NorthStarWeek(
        week=week, first_snapshot_date=first, last_snapshot_date=last,
        excess_return_end=excess_return,
        portfolio_index_end=portfolio_index,
        spy_index_end=spy_index,
        coverage_ratio_end=coverage_ratio,
        is_sufficient_end=is_sufficient,
    )


def _op(*, manifest_available=False, backup_status="missing",
        backup_is_stale=True, backup_last_at=None, backup_error=None) -> OperationalFloor:
    return OperationalFloor(
        backup_status=backup_status, backup_is_stale=backup_is_stale,
        backup_last_at=backup_last_at, backup_error=backup_error,
        manifest_available=manifest_available,
    )


def _appendix(*, trading_date=None, cash_balance=None, holdings_mtm=None,
              portfolio_nav=None, unpriced_count=0, tickers=()) -> SnapshotAppendix:
    return SnapshotAppendix(
        trading_date=trading_date, cash_balance=cash_balance,
        holdings_mtm=holdings_mtm, portfolio_nav=portfolio_nav,
        unpriced_positions_count=unpriced_count,
        unpriced_tickers=tuple(tickers),
    )


def _payload(*, week_ending=date(2026, 8, 16), generated_at=None,
             this_week=None, prior_week=None,
             ns_this=None, ns_prior=None,
             diags_this=None, diags_prior=None,
             op=None, app=None) -> CharterReviewPayload:
    monday = date.fromordinal(week_ending.toordinal() - 6)
    prior_monday = date.fromordinal(monday.toordinal() - 7)
    return CharterReviewPayload(
        generated_at=generated_at or datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
        week_ending=week_ending,
        this_week=this_week or _week(monday),
        prior_week=prior_week or _week(prior_monday),
        north_star_this=ns_this or _ns(this_week or _week(monday)),
        north_star_prior=ns_prior or _ns(prior_week or _week(prior_monday)),
        diagnostics_this=diags_this or _diags(),
        diagnostics_prior=diags_prior or _diags(),
        operational_floor=op or _op(),
        appendix_snapshot=app or _appendix(),
    )


def test_render_includes_locked_sections():
    out = render_charter_review(payload=_payload())
    for header in (
        "# Charter Review",
        "## Executive Summary",
        "## North Star",
        "## Diagnostics",
        "## Operational Floor",
        "## Appendix",
    ):
        assert header in out


def test_render_minimal_payload_byte_identical():
    p = _payload()
    assert render_charter_review(payload=p) == render_charter_review(payload=p)


def test_render_this_week_empty():
    out = render_charter_review(payload=_payload())
    assert "No snapshots in this calendar week." in out


def test_render_both_weeks_empty_still_writes_shell():
    out = render_charter_review(payload=_payload())
    assert "## Diagnostics" in out
    assert "## Operational Floor" in out


def test_render_manifest_unavailable():
    out = render_charter_review(payload=_payload(
        op=_op(manifest_available=False),
    ))
    assert "Backup manifest unavailable" in out


def test_render_appendix_money_fields_present_when_set():
    from decimal import Decimal
    app = _appendix(
        trading_date=date(2026, 8, 14),
        cash_balance=Decimal("100000"),
        holdings_mtm=Decimal("2200"),
        portfolio_nav=Decimal("102200"),
    )
    out = render_charter_review(payload=_payload(app=app))
    assert "Cash balance: 100000" in out
    assert "Holdings MTM: 2200" in out
    assert "Portfolio NAV: 102200" in out
    assert "Trading date: 2026-08-14" in out
