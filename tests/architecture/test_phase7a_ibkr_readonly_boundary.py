"""Phase 7a-Flex / 7b boundary: ``ibapi`` import allow-list.

- Original Phase 7a: ``ibkr_client.py`` was the only permitted importer.
- Phase 7a-Flex: removed that adapter; boundary became a DENY-LIST.
- Phase 7b: the order pilot reintroduces a single permitted importer,
  ``marketpulse/broker/ibkr_order_client.py``, holding the TWS/Gateway
  order adapter (L33 of the 7b plan). All other production modules are
  still forbidden from importing ``ibapi``.

We keep the file name to preserve git history; the docstring documents
the boundary evolution. ``test_phase7b_order_boundary.py`` (T7b) adds a
narrower allow-list assertion that only `ibkr_order_client.py` may use
ibapi.
"""
# Layer: architecture

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "marketpulse"

# Phase 7b: the order-pilot adapter is the single permitted ``ibapi`` importer.
# Any other module must remain ibapi-free.
_IBAPI_IMPORT_ALLOWLIST: frozenset[str] = frozenset(
    {"marketpulse/broker/ibkr_order_client.py"}
)


def _python_files() -> list[Path]:
    return [p for p in ROOT.rglob("*.py") if "__pycache__" not in p.parts]


def _is_allowlisted(path: Path) -> bool:
    repo_root = ROOT.parent
    rel = path.relative_to(repo_root).as_posix()
    return rel in _IBAPI_IMPORT_ALLOWLIST


def test_no_production_module_imports_ibapi():
    offenders: list[str] = []
    for path in _python_files():
        if _is_allowlisted(path):
            continue
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
