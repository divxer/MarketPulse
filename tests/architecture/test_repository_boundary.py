# Layer: invariant
"""Permanent guard: ONLY repository.py is allowed to write paper_*
tables. Production code outside marketpulse/trading/repository.py
must not call session.add(), session.execute(insert/update), or
session.commit() on paper_* models.

This is the load-bearing single-writer architecture lock (umbrella
lock iii). Any new module that needs to mutate paper_* state MUST
go through repository.py."""

from __future__ import annotations

import ast
from pathlib import Path

PROD_ROOT = Path("marketpulse")
TRADING_ROOT = PROD_ROOT / "trading"
REPOSITORY_PATH = TRADING_ROOT / "repository.py"

# Paths excluded from the boundary check — they own their own tables,
# NOT paper_*. (db/ holds Base + TZDateTime + engine setup; not a writer
# of paper_*.)
#
# This guard targets ONLY marketpulse/trading/ (the layer that owns
# paper_*). Phase 1-5 modules outside the trading layer write to OTHER
# tables (ai_analysis, evaluation_event, holdings_*, etc.) and predate
# this lock; the single-writer rule (iii) is scoped to paper_* state.
EXEMPT = {
    REPOSITORY_PATH,
    TRADING_ROOT / "__init__.py",
    PROD_ROOT / "db" / "base.py",
    PROD_ROOT / "db" / "models.py",  # declarative-base definitions, not writes
}

# Sentinel call names that indicate state mutation against any table.
FORBIDDEN_CALLS = {
    "add",      # session.add(...)
    "commit",   # session.commit()
    "merge",    # session.merge(...)
    "delete",   # session.delete(...)
}


def _find_session_mutation_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """Return (lineno, call_name) for each session.<forbidden>(...) call."""
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match attribute calls like session.add(...) / self._session.add(...)
        if isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_CALLS:
            hits.append((node.lineno, f".{func.attr}(...)"))
    return hits


def _find_insert_update_execute(tree: ast.AST) -> list[tuple[int, str]]:
    """Return hits for session.execute(insert(...)) / .execute(update(...))."""
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "execute"):
            continue
        for arg in node.args:
            if (
                isinstance(arg, ast.Call)
                and isinstance(arg.func, ast.Name)
                and arg.func.id in ("insert", "update", "delete")
            ):
                hits.append((node.lineno, f"execute({arg.func.id}(...))"))
    return hits


def test_repository_is_single_writer():
    violations: list[str] = []
    # Scope: only the trading layer. Non-paper writers in marketpulse/
    # outside marketpulse/trading/ are out of scope for lock iii.
    for path in TRADING_ROOT.rglob("*.py"):
        if path in EXEMPT:
            continue
        # Skip test directories (defensive — marketpulse/ shouldn't contain
        # tests but guard anyway).
        if "tests" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue

        for lineno, call in _find_session_mutation_calls(tree):
            violations.append(f"{path}:{lineno}  {call}")
        for lineno, call in _find_insert_update_execute(tree):
            violations.append(f"{path}:{lineno}  {call}")

    assert not violations, (
        "Single-writer architecture violated (lock iii). Mutations of "
        "paper_* state must go through marketpulse/trading/repository.py. "
        "Violations:\n  " + "\n  ".join(sorted(violations))
    )
