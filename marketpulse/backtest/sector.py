"""Phase 5c-1: ticker→sector lookup with YAML overrides and JSON cache.

Spec § 4 (Sector Data Layer): yfinance is the default; config/sector_overrides.yaml
provides edge-case manual overrides; data/sector_cache.json persists successful
yfinance fetches.

Failsafe-degrade: every failure path logs and returns a safe default. Never
crashes the simulator.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

_logger = logging.getLogger(__name__)

_DEFAULT_OVERRIDES_PATH = Path(__file__).parent.parent.parent / "config" / "sector_overrides.yaml"


def load_sector_overrides(path: Path | str | None = None) -> dict[str, str]:
    """Load and validate config/sector_overrides.yaml. Returns ticker→sector dict.

    Validation:
      - Each value must be a non-empty str (int/float/bool/None all rejected)
      - Empty string values are filtered (key not included in result)
      - Missing file returns {} silently
      - YAML parse error logs ERROR and returns {}
      - Returns empty dict on validation failure, never raises
    """
    target = Path(path) if path is not None else _DEFAULT_OVERRIDES_PATH
    if not target.exists():
        return {}

    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        _logger.error("sector_overrides.yaml parse error at %s: %s", target, exc)
        return {}

    if not isinstance(raw, dict):
        _logger.error(
            "sector_overrides.yaml top-level must be a mapping, got %s",
            type(raw).__name__,
        )
        return {}

    overrides = raw.get("overrides", {})
    if not isinstance(overrides, dict):
        _logger.error("sector_overrides.yaml 'overrides' key must be a mapping")
        return {}

    result: dict[str, str] = {}
    for ticker, sector in overrides.items():
        if not isinstance(sector, str) or not sector:
            _logger.warning(
                "sector_overrides.yaml: skipping ticker=%r non-string-or-empty value %r",
                ticker, sector,
            )
            continue
        result[ticker] = sector
    return result
