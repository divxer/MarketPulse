"""Phase 6h deployment smoke for /lab/paper-trading."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

# Allow direct script execution from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REQUIRED_MARKERS = (
    "Paper Trading · Operations",
    "System Status",
    "Generated at",
    "Critical Events",
    "Positions",
    "Orders & Fills",
    "Audit Timeline",
)
FORBIDDEN_MARKERS = (
    "Force Close",
    "Replay",
    "Retry",
    "Kill Switch Toggle",
    'type="submit"',
)


def _client(timeout: float) -> httpx.Client:
    return httpx.Client(follow_redirects=False, timeout=timeout)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--password")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    return parser


def _fail(message: str) -> int:
    print(f"FAIL: {message}")
    return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    password = args.password or os.getenv("MARKETPULSE_SMOKE_PASSWORD")
    if not password:
        print("ERROR: password required via --password or MARKETPULSE_SMOKE_PASSWORD")
        return 2

    base_url = args.base_url.rstrip("/")
    try:
        with _client(args.timeout_seconds) as client:
            unauth = client.get(f"{base_url}/lab/paper-trading")
            if (
                unauth.status_code not in {302, 303}
                or "/login" not in unauth.headers.get("location", "")
            ):
                return _fail(
                    "unauthenticated /lab/paper-trading did not redirect to /login",
                )

            post_route = client.post(f"{base_url}/lab/paper-trading")
            if post_route.status_code != 405:
                return _fail("POST /lab/paper-trading did not return 405")

            login = client.post(f"{base_url}/login", data={"password": password})
            if login.status_code not in {302, 303}:
                return _fail("login failed")

            page = client.get(f"{base_url}/lab/paper-trading")
            if page.status_code != 200:
                return _fail(
                    f"authenticated /lab/paper-trading returned {page.status_code}",
                )

            missing = [marker for marker in REQUIRED_MARKERS if marker not in page.text]
            if missing:
                return _fail(f"missing marker(s): {', '.join(missing)}")

            forbidden = [marker for marker in FORBIDDEN_MARKERS if marker in page.text]
            if forbidden:
                return _fail(f"control-plane marker(s) present: {', '.join(forbidden)}")
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 2

    print("OK: /lab/paper-trading smoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
