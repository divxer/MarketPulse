"""Phase 5c-1: ticker→sector lookup with YAML overrides and JSON cache.

Spec § 4 (Sector Data Layer): yfinance is the default; config/sector_overrides.yaml
provides edge-case manual overrides; data/sector_cache.json persists successful
yfinance fetches.

Failsafe-degrade: every failure path logs and returns a safe default. Never
crashes the simulator.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Protocol

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


_LEGACY_CACHE_PATH = Path(__file__).parent.parent.parent / "data" / "sector_cache.json"


def _default_cache_path() -> Path:
    """Cache lives wherever SECTOR_CACHE_PATH points (set to the mounted /data
    volume in prod so it survives container recreation). Falls back to the
    in-repo data/ dir for local dev / tests."""
    env = os.environ.get("SECTOR_CACHE_PATH")
    return Path(env) if env else _LEGACY_CACHE_PATH

_SECTOR_CACHE: dict[str, str] = {}
_OVERRIDES_CACHE: dict[str, str] | None = None
_WARNED_TICKERS: set[str] = set()


class _YfSectorClient(Protocol):
    """Minimal interface for yfinance-style sector lookup."""

    def get_sector(self, ticker: str) -> str | None:
        ...


def _reset_caches_for_testing() -> None:
    """Test helper: clear all in-memory caches."""
    _SECTOR_CACHE.clear()
    _WARNED_TICKERS.clear()
    global _OVERRIDES_CACHE
    _OVERRIDES_CACHE = None


def save_sector_cache(cache: dict[str, str], path: Path | str | None = None) -> None:
    """Persist sector lookup dict to JSON. Creates parent dirs if missing."""
    target = Path(path) if path is not None else _default_cache_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def load_sector_cache(path: Path | str | None = None) -> dict[str, str]:
    """Load sector cache from JSON. Returns {} on missing/corrupt file."""
    target = Path(path) if path is not None else _default_cache_path()
    if not target.exists():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _logger.warning("sector_cache.json corrupt at %s: %s — rebuilding", target, exc)
        return {}
    if not isinstance(raw, dict):
        _logger.warning("sector_cache.json must be a JSON object, got %s", type(raw).__name__)
        return {}
    return {str(k): str(v) for k, v in raw.items() if v}


def get_sector(
    ticker: str,
    *,
    yf_client: _YfSectorClient | None = None,
    overrides: dict[str, str] | None = None,
) -> str:
    """Return canonical sector for ticker. Never None — falls back to 'unknown'.

    Resolution order:
      1. config/sector_overrides.yaml (passed via `overrides` or loaded lazily)
      2. In-memory process cache
      3. yfinance Ticker.info['sector'] via yf_client (fetched + cached on success)
      4. 'unknown' fallback (logged WARNING once per ticker)
    """
    global _OVERRIDES_CACHE
    if overrides is None:
        if _OVERRIDES_CACHE is None:
            _OVERRIDES_CACHE = load_sector_overrides()
        overrides = _OVERRIDES_CACHE

    if ticker in overrides:
        return overrides[ticker]

    if ticker in _SECTOR_CACHE:
        return _SECTOR_CACHE[ticker]

    if yf_client is None:
        if ticker not in _WARNED_TICKERS:
            _logger.warning("get_sector(%r) called without yf_client; returning 'unknown'", ticker)
            _WARNED_TICKERS.add(ticker)
        return "unknown"

    try:
        sector = yf_client.get_sector(ticker)
    except Exception as exc:
        if ticker not in _WARNED_TICKERS:
            _logger.warning("yfinance.get_sector(%r) raised %s; returning 'unknown'", ticker, exc)
            _WARNED_TICKERS.add(ticker)
        return "unknown"

    if not sector:
        if ticker not in _WARNED_TICKERS:
            _logger.warning(
                "yfinance.get_sector(%r) returned %r; returning 'unknown'", ticker, sector
            )
            _WARNED_TICKERS.add(ticker)
        return "unknown"

    _SECTOR_CACHE[ticker] = sector
    return sector
