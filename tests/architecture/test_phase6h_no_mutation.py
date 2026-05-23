"""Architecture guards for Phase 6h ops scripts."""

from __future__ import annotations

from pathlib import Path


PHASE6H_SCRIPTS = [
    Path("scripts/check_paper_trading_health.py"),
    Path("scripts/smoke_paper_trading_ops.py"),
    Path("scripts/smoke_notifications.py"),
]


def test_phase6h_scripts_do_not_use_sqlalchemy_mutation_apis():
    forbidden = (
        ".add(",
        ".merge(",
        ".delete(",
        ".execute(",
        "insert(",
        "update(",
        "delete(",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
    )
    offenders: list[str] = []
    for path in PHASE6H_SCRIPTS:
        src = path.read_text()
        for needle in forbidden:
            if needle in src:
                offenders.append(f"{path}:{needle}")

    assert offenders == []
