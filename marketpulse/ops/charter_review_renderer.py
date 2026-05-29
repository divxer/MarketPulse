# Layer: pure
"""PR3b — pure markdown renderer for the weekly charter review.

L9: pure module. No DB, no FS, no clock, no network.
L17: same (payload including generated_at) → byte-identical output.
"""
from __future__ import annotations

from decimal import Decimal

from marketpulse.ops.charter_review_types import (
    CharterReviewPayload,
)

SECTION_SEPARATOR = "\n\n"
REASON_MAX_DISPLAY_LEN = 200
VALUE_NA = "N/A"
DELTA_PRIOR_NA = "prior week N/A"
_MINUS = "−"  # unicode minus sign — typographically matches "+"


def _fmt_pct(value: Decimal | None) -> str:
    """0.032 → '3.2%'; -0.014 → '-1.4%'; None → 'N/A'."""
    if value is None:
        return VALUE_NA
    pct = Decimal(value) * Decimal("100")
    quant = pct.quantize(Decimal("0.1"))
    return f"{quant}%"


def _fmt_int(value: int | None) -> str:
    return VALUE_NA if value is None else f"{int(value)}"


def _fmt_delta_pp(this: Decimal | None, prior: Decimal | None) -> str:
    """Returns '+1.4 pp vs prior week', '−1.8 pp vs prior week',
    or DELTA_PRIOR_NA when prior or this is None."""
    if this is None or prior is None:
        return DELTA_PRIOR_NA
    delta_pp = (Decimal(this) - Decimal(prior)) * Decimal("100")
    delta_pp = delta_pp.quantize(Decimal("0.1"))
    if delta_pp >= 0:
        return f"+{delta_pp} pp vs prior week"
    return f"{_MINUS}{abs(delta_pp)} pp vs prior week"


def _fmt_delta_int(this: int | None, prior: int | None) -> str:
    if this is None or prior is None:
        return DELTA_PRIOR_NA
    delta = int(this) - int(prior)
    if delta >= 0:
        return f"+{delta} vs prior week"
    return f"{_MINUS}{abs(delta)} vs prior week"


def _fmt_index(value: Decimal | None) -> str:
    """L21: raw index multiplier (NOT percent). 1.041 → '1.041'.
    Use for `portfolio_index`, `spy_index`. NEVER use `_fmt_pct` for these —
    that would render 1.041 as '104.1%' which is meaningless."""
    if value is None:
        return VALUE_NA
    quant = Decimal(value).quantize(Decimal("0.001"))
    return f"{quant}"


def _fmt_delta_index(this: Decimal | None, prior: Decimal | None) -> str:
    if this is None or prior is None:
        return DELTA_PRIOR_NA
    delta = (Decimal(this) - Decimal(prior)).quantize(Decimal("0.001"))
    if delta >= 0:
        return f"+{delta} vs prior week"
    return f"{_MINUS}{abs(delta)} vs prior week"


def _fmt_reason(reason: str) -> str:
    """L16 normalization order (locked):
      1. replace any '\\n' or '\\r' with a single space
      2. escape '|' as '\\|' (preserves markdown table grammar)
      3. truncate to REASON_MAX_DISPLAY_LEN chars + '…' if longer

    The aggregator is responsible for converting empty reasons to
    the literal '(no reason)' (L19) BEFORE this function is called.
    """
    normalized = reason.replace("\n", " ").replace("\r", " ")
    escaped = normalized.replace("|", "\\|")
    if len(escaped) > REASON_MAX_DISPLAY_LEN:
        return escaped[:REASON_MAX_DISPLAY_LEN] + "…"
    return escaped


def _section_header(payload: CharterReviewPayload) -> str:
    this = payload.this_week
    prior = payload.prior_week
    return (
        f"# Charter Review — Week Ending {payload.week_ending.isoformat()}\n"
        f"\n"
        f"Generated: {payload.generated_at.isoformat()}\n"
        f"This week: {this.week_start.isoformat()} → {this.week_end.isoformat()} "
        f"({this.trading_days_observed} trading days)\n"
        f"Prior week: {prior.week_start.isoformat()} → {prior.week_end.isoformat()} "
        f"({prior.trading_days_observed} trading days)"
    )


def _section_executive_summary(payload: CharterReviewPayload) -> str:
    lines: list[str] = ["## Executive Summary", ""]
    if payload.this_week.trading_days_observed == 0:
        lines.append("- No snapshots in this calendar week.")
    ns_this = payload.north_star_this
    ns_prior = payload.north_star_prior
    diag_this = payload.diagnostics_this
    diag_prior = payload.diagnostics_prior
    cov_str = (
        f"coverage {ns_this.week.trading_days_observed}/90 trading days"
        if ns_this.week.trading_days_observed
        else "no coverage data this week"
    )
    lines.append(
        f"- Portfolio excess return: {_fmt_pct(ns_this.excess_return_end)} "
        f"({_fmt_delta_pp(ns_this.excess_return_end, ns_prior.excess_return_end)}) "
        f"— {cov_str}",
    )
    lines.append(
        f"- Tick success rate: {_fmt_pct(diag_this.tick_success_rate.value)} "
        f"({_fmt_delta_pp(diag_this.tick_success_rate.value, diag_prior.tick_success_rate.value)})",
    )
    rej_val = diag_this.order_rejection_rate.value
    rej_prior = diag_prior.order_rejection_rate.value
    lines.append(
        f"- Order rejection rate: {_fmt_pct(rej_val)} "
        f"({_fmt_delta_pp(rej_val, rej_prior)})",
    )
    fills_val = diag_this.paper_trade_count.value
    fills_prior = diag_prior.paper_trade_count.value
    lines.append(
        f"- Paper entry fills: {_fmt_int(fills_val)} "
        f"({_fmt_delta_int(fills_val, fills_prior)})",
    )
    op = payload.operational_floor
    is_stale_str = "stale" if op.backup_is_stale else "fresh"
    lines.append(f"- Backup status: {op.backup_status} ({is_stale_str})")
    return "\n".join(lines)


def _fmt_optional_date(d):  # date | None
    return "N/A" if d is None else d.isoformat()


def _section_north_star(payload: CharterReviewPayload) -> str:
    this = payload.north_star_this
    prior = payload.north_star_prior
    cov_delta = _fmt_delta_int(
        this.week.trading_days_observed, prior.week.trading_days_observed,
    )
    lines = [
        "## North Star",
        "",
        "Metric: `paper_portfolio_excess_return_vs_spy_90d`",
        "",
        "|                          | This week    | Prior week  | Δ            |",
        "|--------------------------|--------------|-------------|--------------|",
        f"| Excess return            | {_fmt_pct(this.excess_return_end):<12} "
        f"| {_fmt_pct(prior.excess_return_end):<11} "
        f"| {_fmt_delta_pp(this.excess_return_end, prior.excess_return_end):<12} |",
        f"| Portfolio index          | {_fmt_index(this.portfolio_index_end):<12} "
        f"| {_fmt_index(prior.portfolio_index_end):<11} "
        f"| {_fmt_delta_index(this.portfolio_index_end, prior.portfolio_index_end):<12} |",
        f"| SPY index                | {_fmt_index(this.spy_index_end):<12} "
        f"| {_fmt_index(prior.spy_index_end):<11} "
        f"| {_fmt_delta_index(this.spy_index_end, prior.spy_index_end):<12} |",
        f"| Coverage                 | {this.week.trading_days_observed}/90 days   "
        f"| {prior.week.trading_days_observed}/90 days  "
        f"| {cov_delta:<12} |",
        f"| Statistically sufficient | {str(this.is_sufficient_end):<12} "
        f"| {str(prior.is_sufficient_end):<11} | —            |",
        "",
        f"Observation window: first snapshot {_fmt_optional_date(this.first_snapshot_date)}, "
        f"last snapshot {_fmt_optional_date(this.last_snapshot_date)}.",
    ]
    return "\n".join(lines)


def _fmt_top_reasons_line(top_reasons) -> str:
    if not top_reasons:
        return "(none)"
    items = [f"{_fmt_reason(rc.reason)} ({rc.count})" for rc in top_reasons]
    return ", ".join(items)


def _section_diagnostics(payload: CharterReviewPayload) -> str:
    dt = payload.diagnostics_this
    dp = payload.diagnostics_prior
    parts = ["## Diagnostics", ""]

    parts.append("### Tick success rate")
    parts.append(f"- This week: {_fmt_pct(dt.tick_success_rate.value)} "
                 f"({dt.tick_success_rate.observations} observations)")
    parts.append(f"- Prior week: {_fmt_pct(dp.tick_success_rate.value)} "
                 f"({dp.tick_success_rate.observations} observations)")
    parts.append(f"- Δ: {_fmt_delta_pp(dt.tick_success_rate.value, dp.tick_success_rate.value)}")
    parts.append(f"- Top failure reasons this week: "
                 f"{_fmt_top_reasons_line(dt.tick_success_rate.top_reasons)}")
    parts.append("")

    parts.append("### Order rejection rate")
    parts.append(f"- This week: {_fmt_pct(dt.order_rejection_rate.value)} "
                 f"({dt.order_rejection_rate.observations} observations)")
    parts.append(f"- Prior week: {_fmt_pct(dp.order_rejection_rate.value)} "
                 f"({dp.order_rejection_rate.observations} observations)")
    rej_delta = _fmt_delta_pp(
        dt.order_rejection_rate.value, dp.order_rejection_rate.value,
    )
    parts.append(f"- Δ: {rej_delta}")
    parts.append(f"- Top rejection reasons this week: "
                 f"{_fmt_top_reasons_line(dt.order_rejection_rate.top_reasons)}")
    parts.append("")

    parts.append("### Paper entry fills")
    parts.append(f"- This week: {_fmt_int(dt.paper_trade_count.value)}")
    parts.append(f"- Prior week: {_fmt_int(dp.paper_trade_count.value)}")
    trade_delta = _fmt_delta_int(
        dt.paper_trade_count.value, dp.paper_trade_count.value,
    )
    parts.append(f"- Δ: {trade_delta}")
    parts.append("")

    parts.append("### Engine invariant errors")
    parts.append(f"- This week: {_fmt_int(dt.engine_invariant_errors.value)}")
    parts.append(f"- Prior week: {_fmt_int(dp.engine_invariant_errors.value)}")
    eng_delta = _fmt_delta_int(
        dt.engine_invariant_errors.value, dp.engine_invariant_errors.value,
    )
    parts.append(f"- Δ: {eng_delta}")
    parts.append(f"- Top reasons this week: "
                 f"{_fmt_top_reasons_line(dt.engine_invariant_errors.top_reasons)}")
    return "\n".join(parts)


def _section_operational_floor(payload: CharterReviewPayload) -> str:
    op = payload.operational_floor
    lines = ["## Operational Floor", ""]
    if not op.manifest_available:
        lines.append("- Backup manifest unavailable")
        lines.append(f"- Backup status: {op.backup_status}")
        lines.append(f"- Stale (>25h): {op.backup_is_stale}")
        return "\n".join(lines)
    lines.append(f"- Backup status: {op.backup_status}")
    lines.append(f"- Last successful backup: "
                 f"{op.backup_last_at if op.backup_last_at else VALUE_NA}")
    lines.append(f"- Stale (>25h): {op.backup_is_stale}")
    lines.append(f"- Error (if any): {op.backup_error if op.backup_error else 'none'}")
    return "\n".join(lines)


def _fmt_optional_money(value) -> str:
    return VALUE_NA if value is None else f"{value}"


def _section_appendix(payload: CharterReviewPayload) -> str:
    app = payload.appendix_snapshot
    lines = ["## Appendix — Raw snapshot (end of this week)", ""]
    lines.append(f"- Trading date: {_fmt_optional_date(app.trading_date)}")
    lines.append(f"- Cash balance: {_fmt_optional_money(app.cash_balance)}")
    lines.append(f"- Holdings MTM: {_fmt_optional_money(app.holdings_mtm)}")
    lines.append(f"- Portfolio NAV: {_fmt_optional_money(app.portfolio_nav)}")
    tickers = ", ".join(app.unpriced_tickers) if app.unpriced_tickers else "none"
    lines.append(
        f"- Unpriced positions: {app.unpriced_positions_count} ({tickers})",
    )
    return "\n".join(lines)


def render_charter_review(*, payload: CharterReviewPayload) -> str:
    """Pure renderer (L9). Deterministic — same payload → byte-identical (L17)."""
    sections = [
        _section_header(payload),
        _section_executive_summary(payload),
        _section_north_star(payload),
        _section_diagnostics(payload),
        _section_operational_floor(payload),
        _section_appendix(payload),
    ]
    return SECTION_SEPARATOR.join(sections)
