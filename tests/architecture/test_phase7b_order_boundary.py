"""Phase 7b: order write-path architecture guards.

The 7b plan (L33 / L35 / L2 / L58) requires that the IBKR order write
path is hermetically isolated:

- L33: only ``marketpulse/broker/ibkr_order_client.py`` may import ``ibapi``.
- L35: the adapter exposes only ``place_lmt_order``, ``fetch_order_status``,
  ``cancel_order`` — no modify / replace / global cancel / options exercise.
- L2:  scheduler, daily_cycle, web routes, and strategy-allocation flows
  cannot reach the order service. The write path must be unreachable
  from automation.
- L58: architecture guards prove the above by static analysis.

These guards complement (and narrow) ``test_phase7a_ibkr_readonly_boundary``.
"""
# Layer: architecture

from __future__ import annotations

import ast
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "marketpulse"
REPO_ROOT = ROOT.parent

# L33 allow-list — single permitted ``ibapi`` importer.
_IBAPI_IMPORT_ALLOWLIST: frozenset[str] = frozenset(
    {"marketpulse/broker/ibkr_order_client.py"}
)

# L2 forbidden imports — modules listed below must NOT appear in scheduler /
# daily_cycle / web routes / strategy allocation code.
_FORBIDDEN_WRITE_PATH_MODULES: frozenset[str] = frozenset(
    {
        "marketpulse.broker.order_service",
        "marketpulse.broker.ibkr_order_client",
        "scripts.ibkr_paper_order",
    }
)

# L35 / forbidden adapter method names.
_FORBIDDEN_ADAPTER_METHODS: frozenset[str] = frozenset(
    {
        "modify_order",
        "replace_order",
        "global_cancel",
        "exercise_option",
        "place_market_order",
        "place_bracket_order",
        "place_oco",
        "place_stop_order",
        "place_trailing_stop",
    }
)

# Forbidden raw ibapi method calls inside the adapter source.
_FORBIDDEN_IBAPI_CALLS: frozenset[str] = frozenset(
    {
        "placeBracketOrder",
        "exerciseOptions",
        "reqGlobalCancel",
        "modifyOrder",
    }
)


# --------------------------------------------------------------------------- helpers


def _python_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _imported_modules(path: Path) -> set[str]:
    """Return the set of fully-qualified module names imported by ``path``."""
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, OSError):
        return set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _scan_forbidden_imports(roots: list[Path]) -> list[str]:
    offenders: list[str] = []
    for root in roots:
        for path in _python_files(root):
            mods = _imported_modules(path)
            for forbidden in _FORBIDDEN_WRITE_PATH_MODULES:
                # Match exact module or any submodule (``foo`` or ``foo.bar``).
                hit = any(m == forbidden or m.startswith(forbidden + ".") for m in mods)
                if hit:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: imports {forbidden}")
    return offenders


# --------------------------------------------------------------------------- L33


def test_only_ibkr_order_client_imports_ibapi() -> None:
    """L33: only the dedicated adapter may import ``ibapi``."""
    offenders: list[str] = []
    for path in _python_files(ROOT):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in _IBAPI_IMPORT_ALLOWLIST:
            continue
        for module in _imported_modules(path):
            if module == "ibapi" or module.startswith("ibapi."):
                offenders.append(f"{rel}: imports {module}")
    assert not offenders, (
        "Only marketpulse/broker/ibkr_order_client.py may import ibapi:\n"
        + "\n".join(offenders)
    )


# --------------------------------------------------------------------------- L35


def test_adapter_exposes_only_three_methods() -> None:
    """L35: ``IbkrOrderClient`` exposes exactly the three contract methods."""
    from marketpulse.broker.ibkr_order_client import IbkrOrderClient

    public = {
        name
        for name, _ in inspect.getmembers(IbkrOrderClient, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    expected = {"place_lmt_order", "fetch_order_status", "cancel_order"}
    assert public == expected, (
        f"IbkrOrderClient public methods must be exactly {sorted(expected)}; "
        f"got {sorted(public)}"
    )


def test_forbidden_adapter_methods_absent() -> None:
    """L35: forbidden order-mutation methods must not exist on the adapter."""
    from marketpulse.broker.ibkr_order_client import IbkrOrderClient

    present_forbidden = sorted(
        name for name in _FORBIDDEN_ADAPTER_METHODS if hasattr(IbkrOrderClient, name)
    )
    assert not present_forbidden, (
        "IbkrOrderClient must not expose forbidden methods: "
        f"{present_forbidden}"
    )


def test_forbidden_ibapi_methods_absent_in_adapter() -> None:
    """L35: no calls to forbidden raw ibapi methods inside the adapter source."""
    adapter = ROOT / "broker" / "ibkr_order_client.py"
    tree = ast.parse(adapter.read_text())
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_IBAPI_CALLS:
            offenders.append(f"line {node.lineno}: .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_IBAPI_CALLS:
            offenders.append(f"line {node.lineno}: {node.id}")
    assert not offenders, (
        "Forbidden ibapi method references found in ibkr_order_client.py:\n"
        + "\n".join(offenders)
    )


# --------------------------------------------------------------------------- L2 / L58


def test_scheduler_does_not_import_order_service() -> None:
    """L2: scheduler code cannot reach the write path."""
    scheduler_root = ROOT / "scheduler"
    jobs_module = ROOT / "jobs.py"  # may or may not exist
    roots = [scheduler_root]
    if jobs_module.exists():
        # Single file root — wrap to use the same scanner.
        roots.append(jobs_module.parent)  # parent is ROOT; will be deduped by tests
    offenders = _scan_forbidden_imports([scheduler_root])
    # Additionally scan the standalone jobs.py if present without sweeping ROOT.
    if jobs_module.exists():
        mods = _imported_modules(jobs_module)
        for forbidden in _FORBIDDEN_WRITE_PATH_MODULES:
            if any(m == forbidden or m.startswith(forbidden + ".") for m in mods):
                offenders.append(
                    f"{jobs_module.relative_to(REPO_ROOT)}: imports {forbidden}"
                )
    assert not offenders, (
        "Scheduler must not import the order write path:\n" + "\n".join(offenders)
    )


def test_daily_cycle_does_not_import_order_service() -> None:
    """L2: daily_cycle cannot reach the write path."""
    daily_cycle = ROOT / "trading" / "daily_cycle.py"
    assert daily_cycle.exists(), f"expected {daily_cycle} to exist"
    mods = _imported_modules(daily_cycle)
    offenders = [
        f"{daily_cycle.relative_to(REPO_ROOT)}: imports {m}"
        for m in mods
        if any(m == f or m.startswith(f + ".") for f in _FORBIDDEN_WRITE_PATH_MODULES)
    ]
    assert not offenders, (
        "daily_cycle.py must not import the order write path:\n" + "\n".join(offenders)
    )


def test_web_routes_do_not_import_order_service() -> None:
    """L2: web routes cannot reach the write path."""
    routes_root = ROOT / "web" / "routes"
    assert routes_root.exists(), f"expected {routes_root} to exist"
    offenders = _scan_forbidden_imports([routes_root])
    assert not offenders, (
        "Web routes must not import the order write path:\n" + "\n".join(offenders)
    )


def test_strategy_allocation_does_not_import_order_service() -> None:
    """L2: strategy allocation / backtest flows cannot reach the write path."""
    backtest_root = ROOT / "backtest"
    strategies_root = ROOT / "strategies"
    roots = [r for r in (backtest_root, strategies_root) if r.exists()]
    assert roots, "expected at least one of backtest/ or strategies/ to exist"
    offenders = _scan_forbidden_imports(roots)
    assert not offenders, (
        "Strategy / allocation flows must not import the order write path:\n"
        + "\n".join(offenders)
    )
