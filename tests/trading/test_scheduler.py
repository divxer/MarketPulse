# Layer: invariant
"""6a-3.4: paper_trading_tick.py is THIN (lock xxv)."""

from __future__ import annotations

from pathlib import Path


def test_scheduler_entrypoint_is_thin():
    """No SQL, no business logic, no state mutation inside the scheduler
    entrypoint. It must only resolve DI and call daily_cycle.run."""
    src = Path("marketpulse/scheduler/paper_trading_tick.py").read_text()

    # Forbid SQL fragments and direct paper_* writes.
    forbidden = [
        "session.add", "session.execute(insert", "session.execute(update",
        "INSERT", "UPDATE", "DELETE",
    ]
    for f in forbidden:
        assert f not in src, (
            f"thin-wrapper violation: '{f}' in scheduler entrypoint"
        )

    # The file should be small.
    line_count = len([
        line for line in src.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ])
    assert line_count < 60, (
        f"scheduler entrypoint too thick: {line_count} non-comment lines"
    )

    # Must call daily_cycle.run.
    assert (
        "daily_cycle.run(" in src
        or "from marketpulse.trading import daily_cycle" in src
    )
