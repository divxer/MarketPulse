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

# Community forks are banned outright; only the official ``ibapi`` SDK
# is permitted, and only inside the read-only adapter module.
BANNED_IBKR_PACKAGES = ("ib_insync", "ib_async")
ALLOWED_OFFICIAL_PACKAGE_ROOT = "ibapi"


def _python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _module_root(name: str | None) -> str | None:
    if name is None:
        return None
    return name.split(".", 1)[0]


def test_no_module_imports_banned_ibkr_community_packages():
    """``ib_insync`` (unmaintained) and ``ib_async`` (community fork) are banned everywhere."""
    offenders: list[str] = []
    for path in [*_python_files(PROD), *_python_files(Path("tests"))]:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _module_root(alias.name) in BANNED_IBKR_PACKAGES:
                        offenders.append(f"{path}:{alias.name}")
            if (
                isinstance(node, ast.ImportFrom)
                and _module_root(node.module) in BANNED_IBKR_PACKAGES
            ):
                offenders.append(f"{path}:{node.module}")
    assert sorted(set(offenders)) == []


def test_only_ibkr_client_imports_official_ibapi():
    """Only the read-only adapter module is allowed to import ``ibapi``."""
    offenders: list[str] = []
    for path in [*_python_files(PROD), *_python_files(Path("tests"))]:
        if path == IBKR_CLIENT:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _module_root(alias.name) == ALLOWED_OFFICIAL_PACKAGE_ROOT:
                        offenders.append(f"{path}:{alias.name}")
            if (
                isinstance(node, ast.ImportFrom)
                and _module_root(node.module) == ALLOWED_OFFICIAL_PACKAGE_ROOT
            ):
                offenders.append(f"{path}:{node.module}")
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
