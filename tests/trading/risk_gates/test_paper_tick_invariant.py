# Layer: pure
"""Startup invariant: paper_tick wall-clock ∈ MarketHoursGate placement
window. Prevents the silent-no-orders bug where the cron fires outside
the gate window and every order gets rejected.
"""
from __future__ import annotations

from datetime import time

import pytest

from marketpulse.trading.risk_gates import (
    MarketHoursConfig,
    validate_paper_tick_in_placement_window,
)


def _cfg(
    *,
    enabled: bool = True,
    regular: bool = True,
    post_close: bool = True,
    post_close_until: time = time(18, 0),
    premarket: bool = False,
) -> MarketHoursConfig:
    return MarketHoursConfig(
        enabled=enabled,
        exchange="XNYS",
        allow_regular_session=regular,
        allow_post_close=post_close,
        post_close_until=post_close_until,
        allow_premarket=premarket,
    )


# === Happy path ===

def test_default_production_config_passes():
    # 17:30 NY ∈ post-close window (16:00, 18:00]
    validate_paper_tick_in_placement_window(
        tick_hour=17, tick_minute=30, cfg=_cfg(),
    )


def test_regular_session_tick_passes():
    # 14:00 NY ∈ regular session
    validate_paper_tick_in_placement_window(
        tick_hour=14, tick_minute=0, cfg=_cfg(),
    )


def test_post_close_boundary_inclusive():
    # 18:00 NY == post_close_until → inclusive-right → OK
    validate_paper_tick_in_placement_window(
        tick_hour=18, tick_minute=0, cfg=_cfg(),
    )


def test_open_boundary_inclusive():
    # 09:30 NY == regular session open → inclusive-left → OK
    validate_paper_tick_in_placement_window(
        tick_hour=9, tick_minute=30, cfg=_cfg(),
    )


# === Rejection path ===

def test_after_post_close_cutoff_rejected():
    # 18:30 NY > 18:00 cutoff
    with pytest.raises(ValueError, match="outside_placement_window"):
        validate_paper_tick_in_placement_window(
            tick_hour=18, tick_minute=30, cfg=_cfg(),
        )


def test_just_after_close_with_post_close_disabled_rejected():
    # 16:30 NY but post_close disabled → no window covers it
    with pytest.raises(ValueError, match="outside_placement_window"):
        validate_paper_tick_in_placement_window(
            tick_hour=16, tick_minute=30,
            cfg=_cfg(post_close=False),
        )


def test_early_morning_rejected_when_premarket_disabled():
    # 06:00 NY but premarket disabled
    with pytest.raises(ValueError, match="outside_placement_window"):
        validate_paper_tick_in_placement_window(
            tick_hour=6, tick_minute=0, cfg=_cfg(premarket=False),
        )


def test_early_morning_passes_when_premarket_enabled():
    validate_paper_tick_in_placement_window(
        tick_hour=6, tick_minute=0, cfg=_cfg(premarket=True),
    )


# === Operator opt-out ===

def test_disabled_gate_skips_invariant():
    # If the gate is disabled, the cron need not align. The invariant
    # is a guard against silent misconfiguration, not a hard policy.
    validate_paper_tick_in_placement_window(
        tick_hour=22, tick_minute=30,
        cfg=_cfg(enabled=False),
    )


# === Timezone confusion guard ===

def test_utc_2230_is_outside_when_cutoff_is_1800_ny():
    """If an operator misreads tz docs and sets MP_PAPER_TICK_HOUR=22
    intending "22:30 UTC == 17:30 NY EDT", the scheduler interprets 22
    as NY local (per cron timezone=America/New_York). 22:30 NY is
    well past the 18:00 post_close cutoff → invariant fails fast.
    """
    with pytest.raises(ValueError, match="outside_placement_window"):
        validate_paper_tick_in_placement_window(
            tick_hour=22, tick_minute=30, cfg=_cfg(),
        )


def test_error_message_includes_remediation():
    with pytest.raises(ValueError) as exc_info:
        validate_paper_tick_in_placement_window(
            tick_hour=22, tick_minute=30, cfg=_cfg(),
        )
    msg = str(exc_info.value)
    assert "MP_PAPER_TICK_HOUR" in msg
    assert "post_close_until" in msg
    assert "22:30" in msg
