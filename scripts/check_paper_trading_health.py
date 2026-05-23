"""Phase 6h paper-trading health snapshot.

Read-only. Uses 6f query models for operational status semantics.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Allow direct script execution from the repository root.
sys.path[:0] = [str(Path(__file__).resolve().parent.parent)]
from scripts._paper_ops_common import count_paper_tables, resolve_db_url, session_from_url

from marketpulse.data.yfinance_client import YFinanceClient  # noqa: E402
from marketpulse.trading.calendar import NY, NYTradingCalendar  # noqa: E402
from marketpulse.trading.price_provider import YFinancePriceProvider  # noqa: E402
from marketpulse.trading.query_models import load_paper_trading_dashboard  # noqa: E402


def _status_value(status: object) -> str:
    value = getattr(status, "value", status)
    return str(value)


def _generated_at_label(dashboard) -> str:
    label = getattr(dashboard, "generated_at_label", None)
    if label is not None:
        return str(label)
    return f"Generated at {dashboard.generated_at.astimezone(NY):%H:%M NY}"


def _latest_completed_ny_trading_day(now: datetime | None = None):
    now_utc = now or datetime.now(UTC)
    ny_now = now_utc.astimezone(NY)
    candidate = ny_now.date()
    if ny_now.hour < 16:
        candidate = candidate - timedelta(days=1)
    calendar = NYTradingCalendar()
    while not calendar.is_business_day(candidate):
        candidate = candidate - timedelta(days=1)
    return candidate


def _price_smoke(*, ticker: str) -> tuple[str, str]:
    on_date = _latest_completed_ny_trading_day()
    provider = YFinancePriceProvider(client=YFinanceClient())
    close = provider.close_on_date(ticker=ticker, on_date=on_date)
    if close is None:
        return "Attention", f"Price smoke unavailable: {ticker} close for {on_date}"
    return (
        "Healthy",
        f"Price smoke OK: {ticker} {close.price} on {close.price_date} ({close.source})",
    )


def _dashboard_to_dict(dashboard) -> dict:
    return asdict(dashboard)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_url", nargs="?", help="SQLAlchemy DB URL")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--skip-price-smoke", action="store_true")
    parser.add_argument("--price-ticker", default="SPY")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        db_url = resolve_db_url(args.db_url)
        with session_from_url(db_url) as session:
            count_paper_tables(session)
            dashboard = load_paper_trading_dashboard(session)
        price_status = "Skipped"
        price_detail = "Price smoke skipped"
        if not args.skip_price_smoke:
            price_status, price_detail = _price_smoke(ticker=args.price_ticker)
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 2

    effective_status = _status_value(dashboard.system_status)
    if price_status == "Attention" and effective_status == "Healthy":
        effective_status = "Attention"

    if args.as_json:
        payload = _dashboard_to_dict(dashboard)
        payload["effective_status"] = effective_status
        payload["price_smoke"] = {"status": price_status, "detail": price_detail}
        print(json.dumps(payload, default=str, indent=2, sort_keys=True))
    else:
        print(f"System Status: {effective_status}")
        print(dashboard.current_operational_window.label)
        print(_generated_at_label(dashboard))
        print(f"Latest Tick: {dashboard.health.latest_tick_status or 'none'}")
        print(f"Cash Balance: {dashboard.health.cash_balance}")
        print(f"Open Positions: {dashboard.health.open_positions_count}")
        print(f"Kill Switch: {dashboard.health.kill_switch_state}")
        print(price_detail)
        if dashboard.critical_events.status == "ok":
            events = dashboard.critical_events.data or []
            if events:
                print("Operational Events:")
                for event in events:
                    ticker = f" {event.ticker}" if event.ticker else ""
                    print(f"- {event.severity}: {event.event_type}{ticker} {event.detail}")
            else:
                print(dashboard.critical_events.empty_message)
        else:
            print(f"Degraded: {dashboard.critical_events.error_title}")

    return 0 if effective_status == "Healthy" else 1


if __name__ == "__main__":
    sys.exit(main())
