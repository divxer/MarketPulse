# Layer: cli
"""CLI smoke for python -m marketpulse.cli.pricing_audit."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from tests.evaluation.test_pricing_audit import (
    _seed_nav_snapshot,
    _seed_position_chain,
)


def _point_cli_at_db(monkeypatch, db_url: str) -> None:
    """Repo-standard DB-pointing pattern (see tests/web/test_charter_route.py)."""
    monkeypatch.setenv("DATABASE_URL", db_url)
    from marketpulse.config import get_settings
    get_settings.cache_clear()


class _StubTencentClient:
    """Deterministic bars; no network."""

    def fetch_history(self, ticker: str, period: str = "60d"):
        from marketpulse.data.types import Bar

        base = {"AAPL": 100.0, "SPY": 600.0}.get(ticker.upper())
        if base is None:
            raise ValueError(f"no Tencent data for {ticker!r}")
        return [
            Bar(date=d, open=base - 1, high=base + 1, low=base - 2,
                close=base, volume=1000)
            for d in (date(2026, 6, 8), date(2026, 6, 9), date(2026, 6, 10))
        ]


def _stub_tencent(monkeypatch) -> None:
    import marketpulse.cli.pricing_audit as mod

    monkeypatch.setattr(mod, "TencentClient", _StubTencentClient)


def _seed(db_session) -> None:
    _seed_position_chain(
        db_session, ticker="AAPL", qty=5,
        opened=datetime(2026, 6, 8, 20, 0, tzinfo=UTC),  # NY 16:00 06-08
        price="100.10",
    )
    _seed_nav_snapshot(
        db_session, trading_date=date(2026, 6, 9),
        cash="500", mtm="500.5", nav="1000.5", spy_close="600.25",
    )
    db_session.commit()


def test_seeded_db_prints_json_report(db_session, db_url, monkeypatch, capsys):
    from marketpulse.cli.pricing_audit import main

    _seed(db_session)
    _point_cli_at_db(monkeypatch, db_url)
    _stub_tencent(monkeypatch)

    main(argv=[])

    out = capsys.readouterr().out
    body = json.loads(out)
    for key in (
        "thresholds",
        "fills",
        "nav",
        "adjustment_basis_analysis",
        "verdict",
        "caveats",
    ):
        assert key in body
    # thresholds echoed match the locked, non-overridable constants
    assert body["thresholds"] == {
        "fills_mean_abs_bps": 25.0,
        "fills_p95_abs_bps": 100.0,
        "fills_anomaly_bps": 200.0,
        "nav_mean_abs_drift_pct": 0.10,
        "nav_max_abs_drift_pct": 0.50,
    }
    assert body["fills"]["n"] == 1
    assert body["nav"]["days"] == 1
    assert body["verdict"]["overall"] in ("PASS", "FAIL")
    assert isinstance(body["caveats"], list) and len(body["caveats"]) == 2


def test_empty_db_exits_1_no_json(db_session, db_url, monkeypatch, capsys):
    from marketpulse.cli.pricing_audit import main

    _point_cli_at_db(monkeypatch, db_url)
    _stub_tencent(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        main(argv=[])
    assert excinfo.value.code == 1

    captured = capsys.readouterr()
    assert "nothing to audit" in captured.err
    assert captured.out == ""  # no partial JSON


def test_no_threshold_override_flags(db_session, db_url, monkeypatch, capsys):
    """Spec-locked: thresholds are NOT overridable; argparse must reject."""
    from marketpulse.cli.pricing_audit import main

    _point_cli_at_db(monkeypatch, db_url)
    _stub_tencent(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        main(argv=["--fills-mean", "999"])
    assert excinfo.value.code == 2  # argparse usage error
