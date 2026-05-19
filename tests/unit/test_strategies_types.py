"""Strategy frozen dataclass for Phase 3 strategy YAML system."""
from dataclasses import FrozenInstanceError

import pytest


def test_strategy_is_frozen():
    from marketpulse.strategies.types import Strategy
    s = Strategy(
        name="momentum_breakout",
        display_name="动量突破",
        version="v1",
        description="趋势突破时的动量分析",
        applies_when="上升趋势 + 量能配合",
        expected_horizons=[5, 20],
        instructions="...策略指令...",
    )
    with pytest.raises(FrozenInstanceError):
        s.name = "other"


def test_strategy_required_fields():
    from marketpulse.strategies.types import Strategy
    with pytest.raises(TypeError):
        Strategy()  # all fields required


def test_strategy_equality_by_value():
    from marketpulse.strategies.types import Strategy
    a = Strategy(name="x", display_name="X", version="v1", description="",
                 applies_when="", expected_horizons=[5], instructions="")
    b = Strategy(name="x", display_name="X", version="v1", description="",
                 applies_when="", expected_horizons=[5], instructions="")
    assert a == b
