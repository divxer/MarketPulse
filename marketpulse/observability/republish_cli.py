"""Operator-triggered replay CLI for Phase 6g notifications."""

from __future__ import annotations

import argparse
import sys
from contextlib import suppress
from datetime import UTC, date, datetime

from marketpulse.alerts.notifier import get_notifier_from_settings
from marketpulse.config import get_settings
from marketpulse.db import base as db_base
from marketpulse.observability.paper_tick_notifier import (
    NotificationResult,
    notify_paper_tick_events,
)
from marketpulse.trading.clock import WallClock
from marketpulse.trading.repository import Repository

_BODY_PREVIEW_LIMIT = 200


def _format_body_preview(body: str | None) -> str:
    if body is None:
        return "(no body)"
    if len(body) <= _BODY_PREVIEW_LIMIT:
        return body
    return body[:_BODY_PREVIEW_LIMIT] + "..."


def _print_result(result: NotificationResult) -> None:
    if result.critical_sent:
        for push in result.critical_sent:
            print(f"pushed: {push.event_type} (audit_id={push.audit_id}) :: {push.title}")
    else:
        print("pushed: (none)")

    if result.summary_sent and result.summary_title:
        print(f"summary_title: {result.summary_title}")
        print(f"summary_body_preview: {_format_body_preview(result.summary_body)}")
    else:
        print("summary: (not emitted)")

    if result.failures:
        for failure in result.failures:
            print(f"failure: {failure.event_type} :: {failure.title} :: {failure.error}")
    else:
        print("failures: (none)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="republish_cli",
        description=(
            "Republish Phase 6g notifications for a paper-trading tick "
            "(operator-triggered replay only)."
        ),
    )
    parser.add_argument(
        "--date",
        required=True,
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Target tick date (ISO format: YYYY-MM-DD)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.paper_notifications_enabled:
        print(
            "ERROR: MP_PAPER_NOTIFICATIONS_ENABLED=false. "
            "Enable the feature before republishing notifications.",
            file=sys.stderr,
        )
        return 1

    tick_date: date = args.date
    since = datetime.combine(tick_date, datetime.min.time(), tzinfo=UTC)
    notifier = get_notifier_from_settings(settings)
    clock = WallClock()

    gen = db_base.session_scope()
    session = next(gen)
    try:
        result = notify_paper_tick_events(
            since=since,
            tick_date=tick_date,
            repository=Repository(session=session),
            notifier=notifier,
            clock=clock,
        )
    finally:
        with suppress(StopIteration):
            next(gen)

    _print_result(result)
    return 0 if not result.failures else 1


if __name__ == "__main__":
    sys.exit(main())
