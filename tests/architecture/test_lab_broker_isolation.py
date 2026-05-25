"""Phase 7a+ Broker Truth Viewer — LOCK L2 architecture guard.

The viewer (query model + route + templates) MUST only read the four
Phase 7a snapshot tables:
  - broker_sync_run
  - broker_account_snapshot
  - broker_cash_snapshot
  - broker_position_snapshot

It MUST NOT touch:
  - broker_order_intent / broker_order_event (Phase 7b write provenance)
  - any paper_* table (Phase 6 paper lifecycle)
"""
# Layer: architecture

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PY_TARGETS = [
    ROOT / "marketpulse" / "broker" / "query_models.py",
    ROOT / "marketpulse" / "web" / "routes" / "broker.py",
]

TEMPLATE_TARGETS = [
    ROOT / "marketpulse" / "web" / "templates" / "lab_broker.html",
    ROOT / "marketpulse" / "web" / "templates" / "partials" / "broker_hero.html",
    ROOT
    / "marketpulse"
    / "web"
    / "templates"
    / "partials"
    / "broker_kpi_strip.html",
    ROOT
    / "marketpulse"
    / "web"
    / "templates"
    / "partials"
    / "broker_positions_table.html",
    ROOT
    / "marketpulse"
    / "web"
    / "templates"
    / "partials"
    / "broker_cash_table.html",
    ROOT
    / "marketpulse"
    / "web"
    / "templates"
    / "partials"
    / "broker_recent_runs_table.html",
]

FORBIDDEN_NAMES = (
    # Phase 7b write-provenance models / tables
    "BrokerOrderIntent",
    "BrokerOrderEvent",
    "broker_order_intent",
    "broker_order_event",
    # Phase 7a non-truth-viewer tables (out of scope for L2)
    "BrokerOpenOrderSnapshot",
    "BrokerExecutionSnapshot",
    "broker_open_order_snapshot",
    "broker_execution_snapshot",
)

PAPER_PREFIXES = ("Paper", "paper_")


def _python_files_walk_strings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.append(alias.name)
            if node.module:
                names.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
    return names


def test_python_targets_avoid_phase7b_and_paper_names():
    offenders: list[str] = []
    for path in PY_TARGETS:
        assert path.exists(), f"target missing: {path}"
        names = _python_files_walk_strings(path)
        for name in names:
            if name in FORBIDDEN_NAMES:
                offenders.append(f"{path}: references {name}")
            elif any(name.startswith(p) for p in PAPER_PREFIXES):
                offenders.append(f"{path}: references paper_* name {name}")
    assert not offenders, (
        "Phase 7a+ viewer must only read the 4 truth tables:\n  "
        + "\n  ".join(offenders)
    )


def test_templates_avoid_phase7b_and_paper_names():
    offenders: list[str] = []
    for path in TEMPLATE_TARGETS:
        assert path.exists(), f"target missing: {path}"
        text = path.read_text()
        for name in FORBIDDEN_NAMES:
            if name in text:
                offenders.append(f"{path}: contains forbidden name {name}")
        # paper_* substring scan (case-sensitive — avoids matching "Paper
        # Trading" in nav labels, etc.). We scan the body for paper_* DB
        # symbols specifically.
        if "paper_" in text:
            offenders.append(f"{path}: contains paper_* token")
    assert not offenders, (
        "Phase 7a+ templates must not reference Phase 7b / paper_* models:\n  "
        + "\n  ".join(offenders)
    )
