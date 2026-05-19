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


def load_strategies(definitions_dir: Path | None = None) -> dict[str, Strategy]:
    """Discover and load all strategy YAMLs from definitions_dir.

    Args:
        definitions_dir: directory to scan for *.yaml; defaults to packaged
            marketpulse/strategies/definitions/

    Returns:
        Dict keyed by strategy `name` field, values are Strategy instances.

    Raises:
        ValueError: invalid YAML (missing field, mismatched name, bad version,
            non-snake-case name, expected_horizons not subset of default).
    """
    dirpath = definitions_dir or _DEFAULT_DIR
    result: dict[str, Strategy] = {}
    for yaml_path in sorted(dirpath.glob("*.yaml")):
        data = yaml.safe_load(yaml_path.read_text())
        if not isinstance(data, dict):
            raise ValueError(f"{yaml_path}: YAML root must be a mapping")
        _validate(yaml_path.stem, data, yaml_path)
        strategy = Strategy(
            name=data["name"],
            display_name=data["display_name"],
            version=data["version"],
            description=data["description"],
            applies_when=data["applies_when"],
            expected_horizons=list(data["expected_horizons"]),
            instructions=data["instructions"],
        )
        result[strategy.name] = strategy
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
