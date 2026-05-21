# Layer: invariant
"""6a-2 invariants enforced by grep against the source tree."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def test_no_date_today_or_datetime_now_in_trading_or_scheduler():
    """Lock xxiii: all production code reads time via Clock. Exception:
    inside WallClock.now/today (which IS the unique production wrapper)."""
    paths = [
        Path("marketpulse/trading"),
        Path("marketpulse/scheduler/paper_trading_tick.py"),
    ]
    pattern = re.compile(r"\b(date\.today\(\)|datetime\.now\()")
    for root in paths:
        if not root.exists():
            continue
        targets = [root] if root.is_file() else list(root.rglob("*.py"))
        for f in targets:
            text = f.read_text()
            # The Clock module IS the production wrapper (lock xxiii).
            # Skip it wholesale — its docstring legitimately names the
            # forbidden calls.
            if f.name == "clock.py":
                continue
            assert not pattern.search(text), (
                f"Lock xxiii violation: date.today()/datetime.now() in {f}"
            )


def test_only_repository_writes_paper_tables():
    """Lock iii (grep companion to AST guard): repository.py is the only
    writer of paper_* tables under marketpulse/trading/. AST-based
    check lives in tests/architecture/test_repository_boundary.py; this
    grep is the lightweight fallback."""
    out = subprocess.run(
        [
            "git", "grep", "-nE",
            r"session\.add|session\.execute\((insert|update)",
            "marketpulse/trading/",
        ],
        capture_output=True, text=True,
    ).stdout
    bad = [
        line for line in out.splitlines()
        if "marketpulse/trading/" in line
        and "trading/repository.py" not in line
        and "trading/__init__.py" not in line
    ]
    assert not bad, (
        "Lock iii violation: session.add/insert/update outside "
        "repository.py:\n" + "\n".join(bad)
    )


def test_no_legacy_filled_status_string():
    """Lock xix: legal status string is ENTRY_FILLED, not FILLED."""
    out = subprocess.run(
        [
            "git", "grep", "-nE",
            r'"FILLED"|\bORDER_FILLED\b',
            "marketpulse/trading/",
        ],
        capture_output=True, text=True,
    ).stdout
    bad = [
        line for line in out.splitlines()
        if "ORDER_ENTRY_FILLED" not in line  # word-boundary safety
    ]
    assert not bad, "Lock xix violation:\n" + "\n".join(bad)
