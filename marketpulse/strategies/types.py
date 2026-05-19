"""Strategy dataclass — frozen, value-equal, loaded from YAML."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Strategy:
    name: str
    display_name: str
    version: str
    description: str
    applies_when: str
    expected_horizons: list[int]
    instructions: str
