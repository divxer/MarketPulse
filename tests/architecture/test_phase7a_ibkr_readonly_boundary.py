"""Phase 7a-Flex boundary: no production module imports ibapi.

The original Phase 7a (gnzsnz/ib-gateway + ibapi) had an ALLOW-LIST: only
``marketpulse/broker/ibkr_client.py`` was permitted to import ``ibapi``.
Phase 7a-Flex removed that adapter entirely; the boundary is now a
DENY-LIST: ``ibapi`` must not appear in any production import.

We keep the file name to preserve git history; the docstring documents
the boundary evolution.
"""
# Layer: architecture

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "marketpulse"


def _python_files() -> list[Path]:
    return [p for p in ROOT.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_production_module_imports_ibapi():
    offenders: list[str] = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "ibapi" or alias.name.startswith("ibapi."):
                        offenders.append(f"{path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and (
                node.module == "ibapi" or (node.module or "").startswith("ibapi.")
            ):
                offenders.append(f"{path}: from {node.module} import ...")
    assert not offenders, "ibapi must not be imported in production code:\n" + "\n".join(offenders)


def test_no_gateway_references_in_compose_files():
    """Phase 7a-Flex removed the ib-gateway sidecar — compose files must
    not resurrect any Gateway service / image / env-var references."""
    import re

    repo_root = Path(__file__).resolve().parents[2]
    compose_files = [
        repo_root / "docker-compose.cn.yml",
        repo_root / "docker-compose.prod.yml",
    ]
    pattern = re.compile(
        r"gnzsnz/ib-gateway|ib-gateway:|TWS_USERID|TWS_PASSWORD|"
        r"IBKR_HOST|IBKR_PORT|IBKR_CLIENT_ID",
        re.IGNORECASE,
    )
    offenders: list[str] = []
    for path in compose_files:
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Gateway references must not appear in compose files:\n" + "\n".join(offenders)
    )
