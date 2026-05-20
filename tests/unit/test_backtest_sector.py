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


def test_save_and_load_sector_cache_round_trip(tmp_path: Path) -> None:
    """Save dict to JSON, load it back, assert equality."""
    from marketpulse.backtest.sector import load_sector_cache, save_sector_cache

    cache_file = tmp_path / "sector_cache.json"
    data = {"AAPL": "Technology", "XOM": "Energy"}
    save_sector_cache(data, cache_file)

    loaded = load_sector_cache(cache_file)
    assert loaded == data


def test_load_sector_cache_returns_empty_when_file_missing(tmp_path: Path) -> None:
    """Missing cache returns {} silently."""
    from marketpulse.backtest.sector import load_sector_cache

    missing = tmp_path / "no_cache.json"
    assert load_sector_cache(missing) == {}


def test_load_sector_cache_returns_empty_when_corrupt(tmp_path: Path) -> None:
    """Corrupt JSON returns {} and logs WARNING."""
    from marketpulse.backtest.sector import load_sector_cache

    bad = tmp_path / "corrupt.json"
    bad.write_text("{not valid json", encoding="utf-8")
    assert load_sector_cache(bad) == {}


def test_get_sector_override_wins_over_yfinance() -> None:
    """When sector_overrides has the ticker, yfinance fetch is skipped."""
    from marketpulse.backtest.sector import _reset_caches_for_testing, get_sector

    _reset_caches_for_testing()

    class FakeYfClient:
        def get_sector(self, _ticker: str) -> str | None:
            raise AssertionError("yfinance should not be called when override exists")

    overrides = {"TQQQ": "leveraged_qqq"}
    result = get_sector("TQQQ", yf_client=FakeYfClient(), overrides=overrides)
    assert result == "leveraged_qqq"


def test_get_sector_returns_yfinance_value_when_no_override() -> None:
    """Falls through to yfinance when no override."""
    from marketpulse.backtest.sector import _reset_caches_for_testing, get_sector

    _reset_caches_for_testing()

    class FakeYfClient:
        def get_sector(self, ticker: str) -> str | None:
            return {"AAPL": "Technology"}.get(ticker)

    result = get_sector("AAPL", yf_client=FakeYfClient(), overrides={})
    assert result == "Technology"


def test_get_sector_returns_unknown_when_yfinance_returns_none() -> None:
    """yfinance None → 'unknown' (fail-safe closed)."""
    from marketpulse.backtest.sector import _reset_caches_for_testing, get_sector

    _reset_caches_for_testing()

    class NullYfClient:
        def get_sector(self, _ticker: str) -> str | None:
            return None

    result = get_sector("UNKNOWN_TICKER", yf_client=NullYfClient(), overrides={})
    assert result == "unknown"


def test_get_sector_returns_unknown_when_yfinance_raises() -> None:
    """yfinance exception → 'unknown' (logged WARNING)."""
    from marketpulse.backtest.sector import _reset_caches_for_testing, get_sector

    _reset_caches_for_testing()

    class CrashYfClient:
        def get_sector(self, _ticker: str) -> str | None:
            raise RuntimeError("network down")

    result = get_sector("AAPL", yf_client=CrashYfClient(), overrides={})
    assert result == "unknown"


def test_get_sector_caches_within_process() -> None:
    """Repeated calls hit cache; yf_client.get_sector invoked at most once per ticker."""
    from marketpulse.backtest.sector import _reset_caches_for_testing, get_sector

    _reset_caches_for_testing()

    call_count = 0

    class CountingYfClient:
        def get_sector(self, _ticker: str) -> str | None:
            nonlocal call_count
            call_count += 1
            return "Technology"

    yf = CountingYfClient()
    get_sector("AAPL", yf_client=yf, overrides={})
    get_sector("AAPL", yf_client=yf, overrides={})
    get_sector("AAPL", yf_client=yf, overrides={})
    assert call_count == 1
