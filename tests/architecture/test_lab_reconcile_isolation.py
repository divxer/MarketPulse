"""Phase 7c reconciliation - architecture guard.

The reconcile module and route must only read these models:
  PaperPosition
  BrokerSyncRun
  BrokerPositionSnapshot

Templates must use user-facing copy rather than ORM class names.
"""
# Layer: architecture
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PY_TARGETS = [
    ROOT / "marketpulse" / "reconcile" / "types.py",
    ROOT / "marketpulse" / "reconcile" / "diffing.py",
    ROOT / "marketpulse" / "reconcile" / "query_models.py",
    ROOT / "marketpulse" / "web" / "routes" / "reconcile.py",
]

TEMPLATE_TARGETS = [
    ROOT / "marketpulse" / "web" / "templates" / "lab_reconcile.html",
    ROOT / "marketpulse" / "web" / "templates" / "partials" / "reconcile_hero.html",
    ROOT / "marketpulse" / "web" / "templates" / "partials" / "reconcile_summary_cards.html",
    ROOT / "marketpulse" / "web" / "templates" / "partials" / "reconcile_diff_table.html",
]

FORBIDDEN_NAMES = (
    "BrokerOrderIntent",
    "BrokerOrderEvent",
    "BrokerOpenOrderSnapshot",
    "BrokerExecutionSnapshot",
    "BrokerAccountSnapshot",
    "BrokerCashSnapshot",
    "PaperOrder",
    "PaperFill",
    "PaperCashLedger",
    "PaperAuditEvent",
)

FORBIDDEN_SESSION_ATTRS = ("add", "add_all", "flush", "commit", "delete")


def _walk_ast(path: Path) -> tuple[list[str], list[str]]:
    tree = ast.parse(path.read_text())
    names: list[str] = []
    attrs: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            attrs.append(node.attr)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.append(alias.name)
    return names, attrs


def test_python_targets_avoid_forbidden_models():
    offenders: list[str] = []
    for path in PY_TARGETS:
        assert path.exists(), f"target missing: {path}"
        names, _ = _walk_ast(path)
        for name in names:
            if name in FORBIDDEN_NAMES:
                offenders.append(f"{path.name}: references {name}")
    assert not offenders, (
        "Phase 7c reconcile module must only read PaperPosition / "
        "BrokerSyncRun / BrokerPositionSnapshot:\n  "
        + "\n  ".join(offenders)
    )


def test_python_targets_do_not_mutate_session():
    offenders: list[str] = []
    for path in PY_TARGETS:
        _, attrs = _walk_ast(path)
        for attr in attrs:
            if attr in FORBIDDEN_SESSION_ATTRS:
                offenders.append(f"{path.name}: calls .{attr}()")
    assert not offenders, (
        "Phase 7c reconcile module must be read-only:\n  "
        + "\n  ".join(offenders)
    )


def test_templates_avoid_orm_class_names():
    offenders: list[str] = []
    for path in TEMPLATE_TARGETS:
        assert path.exists(), f"template missing: {path}"
        text = path.read_text()
        for name in FORBIDDEN_NAMES:
            if name in text:
                offenders.append(f"{path.name}: contains forbidden name {name}")
        if "PaperPosition" in text:
            offenders.append(f"{path.name}: contains PaperPosition")
    assert not offenders, (
        "Phase 7c templates must use user-facing copy only:\n  "
        + "\n  ".join(offenders)
    )


def test_query_model_references_allowed_sources():
    names, _ = _walk_ast(ROOT / "marketpulse" / "reconcile" / "query_models.py")
    assert "PaperPosition" in names
    assert "BrokerSyncRun" in names
    assert "BrokerPositionSnapshot" in names
