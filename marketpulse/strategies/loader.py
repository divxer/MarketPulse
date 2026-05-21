"""YAML loader for strategy definitions.

Discovers all *.yaml files in `definitions_dir`, parses each, validates
all required fields are present and well-formed, returns a dict
{name: Strategy}.

Called once at app startup (via marketpulse.web.main) — invalid YAML
fails fast so the deploy never serves a half-broken strategy library.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from marketpulse.strategies.types import Strategy

_REQUIRED_FIELDS = (
    "name", "display_name", "version", "description",
    "applies_when", "expected_horizons", "instructions",
)
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_VERSION_RE = re.compile(r"^v\d+$")
_VALID_HORIZONS = {1, 5, 20, 60}

_DEFAULT_DIR = Path(__file__).parent / "definitions"

# Module-level cache for the packaged default directory only. Custom
# `definitions_dir` args (tests) bypass the cache so they always see the
# directory contents they prepared.
_PACKAGED_CACHE: dict[str, Strategy] | None = None


def clear_strategy_cache() -> None:
    """Test helper — wipe the module-level packaged-dir cache."""
    global _PACKAGED_CACHE
    _PACKAGED_CACHE = None


def load_strategies(definitions_dir: Path | None = None) -> dict[str, Strategy]:
    """Discover and load all strategy YAMLs from definitions_dir.

    Args:
        definitions_dir: directory to scan for *.yaml; defaults to packaged
            marketpulse/strategies/definitions/. Default-dir results are
            process-cached after first load; custom dirs always re-read.

    Returns:
        Dict keyed by strategy `name` field, values are Strategy instances.

    Raises:
        ValueError: invalid YAML (missing field, mismatched name, bad version,
            non-snake-case name, expected_horizons not subset of default).
    """
    global _PACKAGED_CACHE
    if definitions_dir is None and _PACKAGED_CACHE is not None:
        return _PACKAGED_CACHE

    dirpath = definitions_dir or _DEFAULT_DIR
    result: dict[str, Strategy] = {}
    for yaml_path in sorted(dirpath.glob("*.yaml")):
        data = yaml.safe_load(yaml_path.read_text())
        if not isinstance(data, dict):
            raise ValueError(f"{yaml_path}: YAML root must be a mapping")
        _validate(yaml_path.stem, data, yaml_path)
        base, mn, mx = _validate_sizing(data["name"], data.get("sizing"), yaml_path)
        strategy = Strategy(
            name=data["name"],
            display_name=data["display_name"],
            version=data["version"],
            description=data["description"],
            applies_when=data["applies_when"],
            expected_horizons=list(data["expected_horizons"]),
            instructions=data["instructions"],
            base_position_size=base,
            min_position=mn,
            max_position=mx,
        )
        result[strategy.name] = strategy

    if definitions_dir is None:
        _PACKAGED_CACHE = result
    return result


def _validate(stem: str, data: dict[str, Any], path: Path) -> None:
    """Fail-fast checks for one YAML file."""
    for field in _REQUIRED_FIELDS:
        if field not in data:
            raise ValueError(
                f"{path}: missing required field {field!r}"
            )
    name = data["name"]
    if not _NAME_RE.match(name):
        raise ValueError(
            f"{path}: name {name!r} must be snake_case "
            f"(matching {_NAME_RE.pattern})"
        )
    if name != stem:
        raise ValueError(
            f"{path}: name {name!r} does not match filename {stem!r}"
        )
    if not _VERSION_RE.match(str(data["version"])):
        raise ValueError(
            f"{path}: version {data['version']!r} format invalid "
            f"(expect {_VERSION_RE.pattern})"
        )
    horizons = data["expected_horizons"]
    if not isinstance(horizons, list) or not horizons:
        raise ValueError(
            f"{path}: expected_horizons must be a non-empty list"
        )
    if not set(horizons).issubset(_VALID_HORIZONS):
        raise ValueError(
            f"{path}: expected_horizons {horizons!r} must be subset of "
            f"{sorted(_VALID_HORIZONS)}"
        )


def _validate_sizing(
    name: str,
    sizing: dict | None,
    path: Path,
) -> tuple[float | None, float | None, float | None]:
    """Parse and validate optional `sizing:` block.

    Spec § 2 lock #5. Strict validation: each field if present must be > 0;
    when overrides are merged with global Phase 5b defaults, the resulting
    (eff_min, eff_base, eff_max) tuple must satisfy eff_min <= eff_base <= eff_max.

    Loader does not have access to runtime globals (base_position_size etc.),
    so the merged invariant check uses Phase 5b shipping defaults as the
    reference: base=1000.0, min=200.0, max=4000.0. This is acceptable
    because YAML overrides are static configuration; runtime globals are
    backtest-call-time kwargs. If the runtime call uses different globals,
    the validation is still meaningful: it catches YAML that would be
    invalid against the SHIPPED defaults, which is what humans configure.

    Returns (base, min, max) — each float or None. Raises ValueError
    (matches existing loader idiom) on invalid input. The error message
    includes strategy name and offending value(s).
    """
    if sizing is None:
        return (None, None, None)
    if not isinstance(sizing, dict):
        raise ValueError(
            f"{path}: sizing must be a mapping, got {type(sizing).__name__}"
        )
    base = sizing.get("base_position_size")
    mn = sizing.get("min_position")
    mx = sizing.get("max_position")
    for fld, val in (("base_position_size", base), ("min_position", mn), ("max_position", mx)):
        if val is None:
            continue
        if not isinstance(val, (int, float)):
            raise ValueError(
                f"{path}: sizing.{fld} must be a number, got {type(val).__name__}"
            )
        if val <= 0:
            raise ValueError(
                f"Strategy {name!r}: sizing.{fld} must be > 0 (got {val})"
            )
    # Merge with Phase 5b shipping defaults to validate the invariant
    g_base, g_min, g_max = 1_000.0, 200.0, 4_000.0
    eff_base = float(base) if base is not None else g_base
    eff_min = float(mn) if mn is not None else g_min
    eff_max = float(mx) if mx is not None else g_max
    if not (eff_min <= eff_base <= eff_max):
        raise ValueError(
            f"Strategy {name!r}: sizing invariant violated — "
            f"need min ({eff_min}) <= base ({eff_base}) <= max ({eff_max})"
        )
    return (
        float(base) if base is not None else None,
        float(mn) if mn is not None else None,
        float(mx) if mx is not None else None,
    )
