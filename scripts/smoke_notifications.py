"""Phase 6h notification smoke.

Default mode validates configuration only. Sending requires both --send and
--confirm-send.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

# Allow direct script execution from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marketpulse.alerts.notifier import (  # noqa: E402
    NoopNotifier,
    get_notifier_from_settings,
)
from marketpulse.config import get_settings  # noqa: E402

SMOKE_TITLE = "SMOKE TEST — Paper Trading Notifications"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--confirm-send", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.send and not args.confirm_send:
        print("ERROR: --send requires --confirm-send")
        return 2

    try:
        settings = get_settings()
        notifier = get_notifier_from_settings(settings)
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1

    kind = settings.notifier_kind or "none"
    enabled = settings.paper_notifications_enabled
    if isinstance(notifier, NoopNotifier):
        print(
            f"Notification config OK: kind={kind}, "
            f"paper_notifications_enabled={enabled}, resolves to NoopNotifier",
        )
    else:
        print(
            f"Notification config OK: kind={kind}, "
            f"paper_notifications_enabled={enabled}",
        )

    if not args.send:
        print("No send attempted. Pass --send --confirm-send to emit smoke message.")
        return 0

    body = (
        "SMOKE TEST only. This is not a paper tick, fill, risk alert, "
        f"or trading event. Sent at {datetime.now(UTC).isoformat()}."
    )
    ok = notifier.send(SMOKE_TITLE, body, None)
    if not ok:
        print("FAIL: notifier.send returned false")
        return 1
    print("OK: notification smoke sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
