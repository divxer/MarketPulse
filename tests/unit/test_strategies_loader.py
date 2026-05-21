"""Tests for strategy YAML loader + validation."""
import pytest

from marketpulse.strategies.types import Strategy


def test_load_strategies_returns_dict_keyed_by_name(tmp_path):
    from marketpulse.strategies.loader import load_strategies
    # Use the real definitions/ dir packaged in marketpulse/
    result = load_strategies()
    assert isinstance(result, dict)
    # All 6 v0 strategies present
    expected = {
        "fundamental_value", "momentum_breakout", "news_event",
        "sector_rotation", "oversold_reversal", "general",
    }
    assert set(result.keys()) == expected
    # Each value is a Strategy
    for name, strat in result.items():
        assert isinstance(strat, Strategy)
        assert strat.name == name


def test_loaded_strategy_has_all_required_fields():
    from marketpulse.strategies.loader import load_strategies
    s = load_strategies()["momentum_breakout"]
    assert s.name == "momentum_breakout"
    assert s.display_name == "动量突破"
    assert s.version == "v1"
    assert s.description
    assert s.applies_when
    assert s.expected_horizons == [5, 20]
    assert "突破质量" in s.instructions  # spot-check prompt content


def test_load_from_directory_with_custom_path(tmp_path):
    from marketpulse.strategies.loader import load_strategies
    # Write one minimal YAML
    yaml_file = tmp_path / "tiny.yaml"
    yaml_file.write_text(
        "name: tiny\n"
        "display_name: 极简\n"
        "version: v1\n"
        "description: minimal\n"
        "applies_when: always\n"
        "expected_horizons: [5]\n"
        "instructions: do stuff\n"
    )
    result = load_strategies(definitions_dir=tmp_path)
    assert "tiny" in result
    assert result["tiny"].display_name == "极简"


def test_load_fails_when_required_field_missing(tmp_path):
    from marketpulse.strategies.loader import load_strategies
    yaml_file = tmp_path / "bad.yaml"
    yaml_file.write_text(
        "name: bad\n"
        "display_name: Bad\n"
        # missing: version, description, applies_when, expected_horizons, instructions
    )
    with pytest.raises(ValueError, match="missing required field"):
        load_strategies(definitions_dir=tmp_path)


def test_load_fails_when_name_does_not_match_filename(tmp_path):
    from marketpulse.strategies.loader import load_strategies
    yaml_file = tmp_path / "alpha.yaml"
    yaml_file.write_text(
        "name: beta\n"  # mismatch!
        "display_name: Beta\n"
        "version: v1\n"
        "description: x\n"
        "applies_when: x\n"
        "expected_horizons: [5]\n"
        "instructions: x\n"
    )
    with pytest.raises(ValueError, match="name 'beta' does not match filename 'alpha'"):
        load_strategies(definitions_dir=tmp_path)


def test_load_fails_when_expected_horizons_not_subset_of_default(tmp_path):
    from marketpulse.strategies.loader import load_strategies
    yaml_file = tmp_path / "weird.yaml"
    yaml_file.write_text(
        "name: weird\n"
        "display_name: Weird\n"
        "version: v1\n"
        "description: x\n"
        "applies_when: x\n"
        "expected_horizons: [3, 10]\n"   # not in [1, 5, 20, 60]
        "instructions: x\n"
    )
    with pytest.raises(ValueError, match="expected_horizons.*must be subset"):
        load_strategies(definitions_dir=tmp_path)


def test_load_fails_when_version_format_invalid(tmp_path):
    from marketpulse.strategies.loader import load_strategies
    yaml_file = tmp_path / "x.yaml"
    yaml_file.write_text(
        "name: x\n"
        "display_name: X\n"
        "version: 1.0\n"    # not vN format
        "description: x\n"
        "applies_when: x\n"
        "expected_horizons: [5]\n"
        "instructions: x\n"
    )
    with pytest.raises(ValueError, match="version.*format"):
        load_strategies(definitions_dir=tmp_path)


def test_load_fails_when_name_not_snake_case(tmp_path):
    from marketpulse.strategies.loader import load_strategies
    yaml_file = tmp_path / "BadName.yaml"
    yaml_file.write_text(
        "name: BadName\n"
        "display_name: X\n"
        "version: v1\n"
        "description: x\n"
        "applies_when: x\n"
        "expected_horizons: [5]\n"
        "instructions: x\n"
    )
    with pytest.raises(ValueError, match="name.*snake_case"):
        load_strategies(definitions_dir=tmp_path)


def test_phase5e_strategy_dataclass_has_phase5e_sizing_fields_defaulted() -> None:
    """# Layer: invariant
    Strategy dataclass gains 3 optional sizing override fields (Phase 5e § 3.3).
    All defaulted to None so existing YAML files load without modification
    (backward-compat lock #4).
    """
    from marketpulse.strategies.types import Strategy
    s = Strategy(
        name="x", display_name="X", version="v1",
        description="test", applies_when="always",
        expected_horizons=[5], instructions="do x",
    )
    assert s.base_position_size is None
    assert s.min_position is None
    assert s.max_position is None


def test_phase5e_sizing_block_absent_yields_none_fields(tmp_path) -> None:
    """# Layer: invariant
    No sizing: block → all 3 Strategy fields are None. Backward-compat
    with existing YAMLs.
    """
    from marketpulse.strategies.loader import load_strategies
    yaml_text = """
name: test_strategy
display_name: Test
version: v1
description: test
applies_when: always
expected_horizons: [5]
instructions: do x
"""
    (tmp_path / "test_strategy.yaml").write_text(yaml_text)
    strategies = load_strategies(tmp_path)
    s = strategies["test_strategy"]
    assert s.base_position_size is None
    assert s.min_position is None
    assert s.max_position is None


def test_phase5e_sizing_block_partial_only_base(tmp_path) -> None:
    """# Layer: invariant
    Partial sizing: block (only base_position_size) → other 2 fields None.
    """
    from marketpulse.strategies.loader import load_strategies
    yaml_text = """
name: test_strategy
display_name: Test
version: v1
description: test
applies_when: always
expected_horizons: [5]
instructions: do x
sizing:
  base_position_size: 750
"""
    (tmp_path / "test_strategy.yaml").write_text(yaml_text)
    strategies = load_strategies(tmp_path)
    s = strategies["test_strategy"]
    assert s.base_position_size == 750.0
    assert s.min_position is None
    assert s.max_position is None


def test_phase5e_sizing_block_full_valid(tmp_path) -> None:
    """# Layer: invariant
    Full sizing: block with all 3 fields, satisfying min <= base <= max.
    """
    from marketpulse.strategies.loader import load_strategies
    yaml_text = """
name: test_strategy
display_name: Test
version: v1
description: test
applies_when: always
expected_horizons: [5]
instructions: do x
sizing:
  base_position_size: 500
  min_position: 200
  max_position: 2000
"""
    (tmp_path / "test_strategy.yaml").write_text(yaml_text)
    strategies = load_strategies(tmp_path)
    s = strategies["test_strategy"]
    assert s.base_position_size == 500.0
    assert s.min_position == 200.0
    assert s.max_position == 2000.0


def test_phase5e_sizing_invalid_min_greater_than_max_raises(tmp_path) -> None:
    """# Layer: invariant
    min > max (after merging with globals) → ValueError with both values
    in the message. Spec § 5 error-message contract.
    """
    import pytest

    from marketpulse.strategies.loader import load_strategies
    yaml_text = """
name: test_strategy
display_name: Test
version: v1
description: test
applies_when: always
expected_horizons: [5]
instructions: do x
sizing:
  min_position: 5000
  max_position: 1000
"""
    (tmp_path / "test_strategy.yaml").write_text(yaml_text)
    with pytest.raises(ValueError) as exc_info:
        load_strategies(tmp_path)
    # Precondition: error mentions both values
    msg = str(exc_info.value)
    assert "5000" in msg and "1000" in msg
    assert "min" in msg.lower() and "max" in msg.lower()
    assert "test_strategy" in msg


def test_phase5e_sizing_invalid_base_greater_than_max_raises(tmp_path) -> None:
    """# Layer: invariant
    base > max (after merging with globals) → ValueError.
    """
    import pytest

    from marketpulse.strategies.loader import load_strategies
    yaml_text = """
name: test_strategy
display_name: Test
version: v1
description: test
applies_when: always
expected_horizons: [5]
instructions: do x
sizing:
  base_position_size: 6000
  max_position: 3000
"""
    (tmp_path / "test_strategy.yaml").write_text(yaml_text)
    with pytest.raises(ValueError) as exc_info:
        load_strategies(tmp_path)
    msg = str(exc_info.value)
    assert "6000" in msg and "3000" in msg
    assert "test_strategy" in msg


def test_phase5e_sizing_invalid_negative_min_raises(tmp_path) -> None:
    """# Layer: invariant
    Negative sizing.min_position → ValueError with field name + value.
    """
    import pytest

    from marketpulse.strategies.loader import load_strategies
    yaml_text = """
name: test_strategy
display_name: Test
version: v1
description: test
applies_when: always
expected_horizons: [5]
instructions: do x
sizing:
  min_position: -100
"""
    (tmp_path / "test_strategy.yaml").write_text(yaml_text)
    with pytest.raises(ValueError) as exc_info:
        load_strategies(tmp_path)
    msg = str(exc_info.value)
    assert "min_position" in msg
    assert "-100" in msg
    assert "test_strategy" in msg
