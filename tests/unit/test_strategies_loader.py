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
