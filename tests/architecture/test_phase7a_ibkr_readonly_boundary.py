# Layer: invariant
"""Architecture guards for Phase 7a IBKR read-only sync."""

from __future__ import annotations

import ast
from pathlib import Path

PROD = Path("marketpulse")
BROKER = PROD / "broker"
IBKR_CLIENT = BROKER / "ibkr_client.py"
TRADING = PROD / "trading"
SCHEDULER = PROD / "scheduler"

MUTATING_IBKR_APIS = (
    "placeOrder",
    "cancelOrder",
    "reqGlobalCancel",
    "exerciseOptions",
    "modifyOrder",
    "replaceOrder",
)


def _python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def test_only_ibkr_client_imports_ib_insync():
    offenders: list[str] = []
    for path in [*_python_files(PROD), *_python_files(Path("tests"))]:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Import)
                and any(alias.name == "ib_insync" for alias in node.names)
                and path != IBKR_CLIENT
            ):
                offenders.append(str(path))
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "ib_insync"
                and path != IBKR_CLIENT
            ):
                offenders.append(str(path))
    assert sorted(set(offenders)) == []


def test_no_ibkr_mutating_api_names_in_production_or_scripts():
    offenders: list[str] = []
    for path in [*_python_files(PROD), Path("scripts/sync_ibkr_readonly.py")]:
        text = path.read_text()
        for needle in MUTATING_IBKR_APIS:
            if needle in text:
                offenders.append(f"{path}:{needle}")
    assert offenders == []


def test_trading_and_scheduler_do_not_import_broker_sync_modules():
    forbidden = (
        "marketpulse.broker.readonly_sync",
        "marketpulse.broker.repository",
        "marketpulse.broker.ibkr_client",
    )
    offenders: list[str] = []
    for root in (TRADING, SCHEDULER):
        for path in _python_files(root):
            text = path.read_text()
            for needle in forbidden:
                if needle in text:
                    offenders.append(f"{path}:{needle}")
    assert offenders == []


def test_phase7a_never_writes_paper_tables_by_name():
    offenders: list[str] = []
    for path in _python_files(BROKER) + [Path("scripts/sync_ibkr_readonly.py")]:
        text = path.read_text()
        for needle in ("PaperOrder", "PaperFill", "PaperPosition", "PaperCashLedger"):
            if needle in text:
                offenders.append(f"{path}:{needle}")
    assert offenders == []
