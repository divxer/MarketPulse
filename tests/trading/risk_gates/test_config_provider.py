# Layer: pure
"""6b-T1..T6: RiskIntent + RiskConfigProvider tests."""

from __future__ import annotations


def test_risk_intent_enum_values():
    from marketpulse.trading.types import RiskIntent
    assert RiskIntent.OPEN == "open"
    assert RiskIntent.ADD == "add"
    assert RiskIntent.CLOSE == "close"
    assert RiskIntent.REDUCE == "reduce"
    assert RiskIntent.FLIP == "flip"


def test_risk_intent_is_str_enum():
    from marketpulse.trading.types import RiskIntent
    # StrEnum membership preserves str identity
    assert isinstance(RiskIntent.OPEN, str)


def test_order_request_defaults_risk_intent_to_open():
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from marketpulse.trading.types import AllocationRunId, OrderRequest, RiskIntent

    req = OrderRequest(
        strategy="momentum_breakout",
        ticker="AAPL",
        quantity=10,
        event_time=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        allocation_date=date(2026, 5, 21),
        event_price=Decimal("150.00"),
        horizon_date=date(2026, 5, 28),
        horizon_price=Decimal("155.00"),
        allocation_run_id=AllocationRunId("paper-2026-05-21"),
        strategy_version="v1",
        allocator_version="phase6a-v1",
        execution_engine_version="phase6a-v1",
        weight=1.0,
        raw_bid_weight=1.0,
        pool_corr=0.1,
        contribution_multiplier=1.0,
        adjusted_bid_weight=1.0,
        effective_corr_window=60,
        rewarded_for_negative_corr=False,
        would_change_rank=False,
        size_clamped_by_override=False,
    )
    assert req.risk_intent == RiskIntent.OPEN


def test_risk_result_defaults_are_back_compat():
    """6a callers construct RiskResult(approved, reason, gate_name); new
    fields default to () and an empty read-only mapping so the old
    signature still works."""
    from marketpulse.trading.risk_gate import RiskResult
    r = RiskResult(approved=True, reason="", gate_name="x")
    assert r.failed_gates == ()
    assert dict(r.context) == {}


def test_risk_result_full_construction():
    from marketpulse.trading.risk_gate import RiskResult
    r = RiskResult(
        approved=False,
        reason="market_hours_outside_window",
        gate_name="market_hours",
        failed_gates=("market_hours",),
        context={"per_gate": [{"gate_name": "market_hours", "approved": False}]},
    )
    assert r.failed_gates == ("market_hours",)
    assert r.context["per_gate"][0]["approved"] is False


def test_risk_result_context_is_immutable_mapping():
    """Lock 6b-L16: top-level context mutation raises TypeError. Gate
    authors pass plain dicts; __post_init__ wraps in MappingProxyType."""
    from marketpulse.trading.risk_gate import RiskResult
    r = RiskResult(approved=False, reason="x", gate_name="g", context={"a": 1})
    import pytest
    with pytest.raises(TypeError):
        r.context["a"] = 2  # type: ignore[index]
    with pytest.raises(TypeError):
        r.context["new_key"] = 99  # type: ignore[index]


def test_risk_gate_module_reexports_risk_intent():
    """6b-L12 back-compat: callers may still write
    `from marketpulse.trading.risk_gate import RiskIntent`."""
    from marketpulse.trading.risk_gate import RiskIntent as RI1
    from marketpulse.trading.types import RiskIntent as RI2
    assert RI1 is RI2


def test_market_hours_config_construction():
    from datetime import time

    from marketpulse.trading.risk_gates.config_provider import MarketHoursConfig
    c = MarketHoursConfig(
        enabled=True, exchange="XNYS",
        allow_regular_session=True, allow_post_close=True,
        post_close_until=time(18, 0), allow_premarket=False,
    )
    assert c.enabled is True
    assert c.post_close_until == time(18, 0)


def test_daily_loss_config_construction():
    from decimal import Decimal

    from marketpulse.trading.risk_gates.config_provider import DailyLossConfig
    c = DailyLossConfig(enabled=True, daily_loss_limit=Decimal("500"))
    assert c.daily_loss_limit == Decimal("500")


def test_sector_exposure_config_construction():
    from decimal import Decimal

    from marketpulse.trading.risk_gates.config_provider import SectorExposureConfig
    c = SectorExposureConfig(
        enabled=True, max_sector_exposure_pct=0.35,
        configured_max_capital_in_use=Decimal("10000"),
    )
    assert c.max_sector_exposure_pct == 0.35


def test_risk_gate_config_aggregates_three():
    from datetime import time
    from decimal import Decimal

    from marketpulse.trading.risk_gates.config_provider import (
        DailyLossConfig,
        MarketHoursConfig,
        RiskGateConfig,
        SectorExposureConfig,
    )
    cfg = RiskGateConfig(
        market_hours=MarketHoursConfig(
            enabled=True, exchange="XNYS",
            allow_regular_session=True, allow_post_close=True,
            post_close_until=time(18, 0), allow_premarket=False,
        ),
        daily_loss=DailyLossConfig(
            enabled=True, daily_loss_limit=Decimal("500"),
        ),
        sector_exposure=SectorExposureConfig(
            enabled=True, max_sector_exposure_pct=0.35,
            configured_max_capital_in_use=Decimal("10000"),
        ),
    )
    assert cfg.market_hours.enabled is True
    assert cfg.daily_loss.daily_loss_limit == Decimal("500")


def test_strategy_risk_config_optional_limit():
    from decimal import Decimal

    from marketpulse.trading.risk_gates.config_provider import StrategyRiskConfig
    c = StrategyRiskConfig(max_position_notional=Decimal("25000"))
    assert c.max_position_notional == Decimal("25000")
    c2 = StrategyRiskConfig(max_position_notional=None)
    assert c2.max_position_notional is None


def test_from_yaml_global_only_parses_shipped_default(tmp_path):
    """T5: parses config/risk_gates.yaml shape correctly."""
    from datetime import time
    from decimal import Decimal

    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    yaml_text = """
market_hours:
  enabled: true
  exchange: XNYS
  allow_regular_session: true
  allow_post_close: true
  post_close_until: "18:00"
  allow_premarket: false
daily_loss:
  enabled: true
  daily_loss_limit: 500
sector_exposure:
  enabled: true
  max_sector_exposure_pct: 0.35
  configured_max_capital_in_use: 10000
"""
    global_path = tmp_path / "risk_gates.yaml"
    global_path.write_text(yaml_text)
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    provider = RiskConfigProvider.from_yaml(
        global_path=global_path, strategies_dir=strategies_dir,
    )
    g = provider.global_config()
    assert g.market_hours.enabled is True
    assert g.market_hours.post_close_until == time(18, 0)
    assert g.market_hours.allow_premarket is False
    assert g.daily_loss.daily_loss_limit == Decimal("500")
    assert g.sector_exposure.max_sector_exposure_pct == 0.35
    assert g.sector_exposure.configured_max_capital_in_use == Decimal("10000")


def test_from_yaml_missing_global_raises(tmp_path):
    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    import pytest
    with pytest.raises(FileNotFoundError):
        RiskConfigProvider.from_yaml(
            global_path=tmp_path / "missing.yaml",
            strategies_dir=strategies_dir,
        )


def test_from_yaml_global_missing_required_key_raises(tmp_path):
    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    # Drop `sector_exposure` block.
    bad = """
market_hours:
  enabled: true
  exchange: XNYS
  allow_regular_session: true
  allow_post_close: true
  post_close_until: "18:00"
  allow_premarket: false
daily_loss:
  enabled: true
  daily_loss_limit: 500
"""
    p = tmp_path / "bad.yaml"
    p.write_text(bad)
    sd = tmp_path / "strategies"
    sd.mkdir()
    import pytest
    with pytest.raises(ValueError, match="sector_exposure"):
        RiskConfigProvider.from_yaml(global_path=p, strategies_dir=sd)


def test_shipped_default_yaml_parses_via_from_yaml(tmp_path):
    """Locks the shipped default config — if config/risk_gates.yaml
    drifts away from the documented shape, this test catches it."""
    from pathlib import Path

    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    repo_root = Path(__file__).resolve().parents[3]
    real_global = repo_root / "config" / "risk_gates.yaml"
    sd = tmp_path / "strategies"
    sd.mkdir()
    provider = RiskConfigProvider.from_yaml(global_path=real_global, strategies_dir=sd)
    g = provider.global_config()
    assert g.market_hours.enabled is True
    assert g.daily_loss.enabled is True
    assert g.sector_exposure.enabled is True


def _write_min_strategy_yaml(path, *, name, risk=None):
    """Helper — minimal valid strategy YAML the loader also accepts."""
    blocks = [
        f"name: {name}",
        f"display_name: {name}",
        "version: v1",
        "description: test",
        "applies_when: test",
        "expected_horizons: [5]",
        "instructions: test",
    ]
    if risk is not None:
        blocks.append("risk:")
        for k, v in risk.items():
            blocks.append(f"  {k}: {v}")
    path.write_text("\n".join(blocks) + "\n")


def test_strategy_dir_parses_risk_block(tmp_path):
    """T6: strategy YAML with `risk:` block becomes StrategyRiskConfig."""
    from decimal import Decimal

    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    sd = tmp_path / "strategies"
    sd.mkdir()
    _write_min_strategy_yaml(
        sd / "momentum_breakout.yaml",
        name="momentum_breakout",
        risk={"max_position_notional": 25000},
    )
    _write_min_strategy_yaml(
        sd / "general.yaml", name="general",
        risk={"max_position_notional": 10000},
    )
    # Ship a stub global YAML.
    g = tmp_path / "g.yaml"
    g.write_text("""
market_hours:
  enabled: true
  exchange: XNYS
  allow_regular_session: true
  allow_post_close: true
  post_close_until: "18:00"
  allow_premarket: false
daily_loss:
  enabled: true
  daily_loss_limit: 500
sector_exposure:
  enabled: true
  max_sector_exposure_pct: 0.35
  configured_max_capital_in_use: 10000
""")
    p = RiskConfigProvider.from_yaml(global_path=g, strategies_dir=sd)
    assert p.strategy_config("momentum_breakout").max_position_notional == Decimal("25000")
    assert p.strategy_config("general").max_position_notional == Decimal("10000")


def test_strategy_without_risk_block_returns_none(tmp_path):
    """Lock 6b-L9: strategy_config() returns None for strategies missing
    a `risk:` block. StrategySizeGate uses this for fail-closed."""
    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    sd = tmp_path / "strategies"
    sd.mkdir()
    _write_min_strategy_yaml(sd / "no_risk.yaml", name="no_risk")  # no risk block
    g = tmp_path / "g.yaml"
    g.write_text("""
market_hours:
  enabled: true
  exchange: XNYS
  allow_regular_session: true
  allow_post_close: true
  post_close_until: "18:00"
  allow_premarket: false
daily_loss:
  enabled: true
  daily_loss_limit: 500
sector_exposure:
  enabled: true
  max_sector_exposure_pct: 0.35
  configured_max_capital_in_use: 10000
""")
    p = RiskConfigProvider.from_yaml(global_path=g, strategies_dir=sd)
    assert p.strategy_config("no_risk") is None


def test_strategy_with_risk_but_missing_limit_field_returns_config_with_none(tmp_path):
    """`risk: {}` block (empty mapping) → StrategyRiskConfig with
    max_position_notional=None. Triggers fail-closed by 6b-L9."""
    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    sd = tmp_path / "strategies"
    sd.mkdir()
    p_yaml = sd / "empty_risk.yaml"
    _write_min_strategy_yaml(p_yaml, name="empty_risk")
    # Append empty risk block.
    p_yaml.write_text(p_yaml.read_text() + "risk: {}\n")
    g = tmp_path / "g.yaml"
    g.write_text("""
market_hours:
  enabled: true
  exchange: XNYS
  allow_regular_session: true
  allow_post_close: true
  post_close_until: "18:00"
  allow_premarket: false
daily_loss:
  enabled: true
  daily_loss_limit: 500
sector_exposure:
  enabled: true
  max_sector_exposure_pct: 0.35
  configured_max_capital_in_use: 10000
""")
    p = RiskConfigProvider.from_yaml(global_path=g, strategies_dir=sd)
    cfg = p.strategy_config("empty_risk")
    assert cfg is not None
    assert cfg.max_position_notional is None


def test_strategy_dir_rejects_negative_notional(tmp_path):
    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    sd = tmp_path / "strategies"
    sd.mkdir()
    _write_min_strategy_yaml(
        sd / "bad.yaml", name="bad",
        risk={"max_position_notional": -100},
    )
    g = tmp_path / "g.yaml"
    g.write_text("""
market_hours:
  enabled: true
  exchange: XNYS
  allow_regular_session: true
  allow_post_close: true
  post_close_until: "18:00"
  allow_premarket: false
daily_loss:
  enabled: true
  daily_loss_limit: 500
sector_exposure:
  enabled: true
  max_sector_exposure_pct: 0.35
  configured_max_capital_in_use: 10000
""")
    import pytest
    with pytest.raises(ValueError, match="max_position_notional"):
        RiskConfigProvider.from_yaml(global_path=g, strategies_dir=sd)


def test_strategy_dir_filename_stem_is_key(tmp_path):
    """Lock 6b-L14: lookup key is the YAML filename stem."""
    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    sd = tmp_path / "strategies"
    sd.mkdir()
    _write_min_strategy_yaml(
        sd / "sector_rotation.yaml", name="sector_rotation",
        risk={"max_position_notional": 5000},
    )
    g = tmp_path / "g.yaml"
    g.write_text("""
market_hours:
  enabled: true
  exchange: XNYS
  allow_regular_session: true
  allow_post_close: true
  post_close_until: "18:00"
  allow_premarket: false
daily_loss:
  enabled: true
  daily_loss_limit: 500
sector_exposure:
  enabled: true
  max_sector_exposure_pct: 0.35
  configured_max_capital_in_use: 10000
""")
    p = RiskConfigProvider.from_yaml(global_path=g, strategies_dir=sd)
    # Lookup by stem.
    assert p.strategy_config("sector_rotation") is not None
    # Lookup by unrelated key.
    assert p.strategy_config("not_a_strategy") is None


def test_shipped_strategies_all_have_risk_blocks():
    """T7: every production strategy YAML must declare a `risk:` block
    with a finite max_position_notional. Missing or None → StrategySizeGate
    fail-closes EVERY order in that strategy (lock 6b-L9), which is fatal
    in production. This test guards against accidental regression of any
    YAML file in marketpulse/strategies/definitions/."""
    from pathlib import Path

    from marketpulse.trading.risk_gates.config_provider import RiskConfigProvider

    repo_root = Path(__file__).resolve().parents[3]
    global_path = repo_root / "config" / "risk_gates.yaml"
    strategies_dir = repo_root / "marketpulse" / "strategies" / "definitions"
    provider = RiskConfigProvider.from_yaml(
        global_path=global_path, strategies_dir=strategies_dir,
    )
    expected_stems = {
        "news_event", "oversold_reversal", "sector_rotation",
        "general", "momentum_breakout", "fundamental_value",
    }
    for stem in expected_stems:
        cfg = provider.strategy_config(stem)
        assert cfg is not None, (
            f"Strategy {stem!r} has no `risk:` block in its YAML — "
            "StrategySizeGate will fail-closed every order for this strategy "
            "(lock 6b-L9). Add `risk: { max_position_notional: <N> }` to "
            f"marketpulse/strategies/definitions/{stem}.yaml"
        )
        assert cfg.max_position_notional is not None, (
            f"Strategy {stem!r} has `risk:` but no max_position_notional — "
            "still fail-closed by 6b-L9"
        )
        assert cfg.max_position_notional > 0
