"""Phase 5c-1: sector.py — ticker→sector lookup + YAML override + JSON cache."""
from __future__ import annotations

from pathlib import Path


def test_load_sector_overrides_returns_dict_for_well_formed_yaml(tmp_path: Path) -> None:
    """Well-formed YAML returns dict[str, str]."""
    from marketpulse.backtest.sector import load_sector_overrides

    yaml_file = tmp_path / "sector_overrides.yaml"
    yaml_file.write_text(
        "overrides:\n"
        "  TQQQ: leveraged_qqq\n"
        "  TNA: leveraged_small_cap\n",
        encoding="utf-8",
    )
    result = load_sector_overrides(yaml_file)
    assert result == {"TQQQ": "leveraged_qqq", "TNA": "leveraged_small_cap"}


def test_load_sector_overrides_returns_empty_when_file_missing(tmp_path: Path) -> None:
    """Missing file returns {} without raising."""
    from marketpulse.backtest.sector import load_sector_overrides

    missing = tmp_path / "does_not_exist.yaml"
    result = load_sector_overrides(missing)
    assert result == {}


def test_load_sector_overrides_returns_empty_when_yaml_corrupt(tmp_path: Path) -> None:
    """Corrupt YAML returns {} (does not raise; logged ERROR)."""
    from marketpulse.backtest.sector import load_sector_overrides

    bad = tmp_path / "bad.yaml"
    bad.write_text("not: valid: yaml: content: [", encoding="utf-8")
    result = load_sector_overrides(bad)
    assert result == {}


def test_load_sector_overrides_rejects_non_string_values(tmp_path: Path) -> None:
    """Non-string override values are rejected → {} (validation failure)."""
    from marketpulse.backtest.sector import load_sector_overrides

    bad = tmp_path / "bad_values.yaml"
    bad.write_text(
        "overrides:\n"
        "  TQQQ: 42\n",  # int, not str
        encoding="utf-8",
    )
    result = load_sector_overrides(bad)
    assert result == {}


def test_load_sector_overrides_strips_empty_string_values(tmp_path: Path) -> None:
    """Empty string values are filtered out (other entries kept)."""
    from marketpulse.backtest.sector import load_sector_overrides

    f = tmp_path / "mixed.yaml"
    f.write_text(
        "overrides:\n"
        "  TQQQ: ''\n"  # empty string → filtered
        "  TNA: leveraged_small_cap\n",
        encoding="utf-8",
    )
    result = load_sector_overrides(f)
    assert result == {"TNA": "leveraged_small_cap"}
