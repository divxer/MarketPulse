# Layer: cli
"""CLI smoke for python -m marketpulse.cli.permutation_test."""
from __future__ import annotations

import json

import pytest


def _point_cli_at_db(monkeypatch, db_url: str) -> None:
    """Repo-standard DB-pointing pattern (see tests/web/test_charter_route.py)."""
    monkeypatch.setenv("DATABASE_URL", db_url)
    from marketpulse.config import get_settings
    get_settings.cache_clear()


def _seed_mixed(db_session) -> None:
    """Mixed-sign excesses so the overall statistic varies under shuffling
    (all-positive excesses would make the null mean constant across seeds)."""
    from tests.unit.test_evaluation_scoring import _ev, _out

    for i in range(5):
        e = _ev(db_session, ticker=f"BU{i}", subtype="bullish")
        e.payload = {**e.payload, "strategy": "s1"}
        _out(db_session, e, horizon=5, excess=0.05 + i / 100)
    for i in range(5):
        e = _ev(db_session, ticker=f"BE{i}", subtype="bearish")
        e.payload = {**e.payload, "strategy": "s2"}
        _out(db_session, e, horizon=5, excess=-0.05 - i / 100)
    # one strategy-less row: counted in A, excluded from C
    e = _ev(db_session, ticker="NOSTRAT", subtype="bullish")
    _out(db_session, e, horizon=5, excess=0.03)
    db_session.commit()


def test_seeded_db_prints_json_report(db_session, db_url, monkeypatch, capsys):
    from marketpulse.cli.permutation_test import main

    _seed_mixed(db_session)
    _point_cli_at_db(monkeypatch, db_url)

    main(argv=["--permutations", "200", "--seed", "42"])

    out = capsys.readouterr().out
    body = json.loads(out)
    assert body["horizon"] == 5
    assert body["sample_size"] == 11
    for key in (
        "overall_observed_hit_rate",
        "overall_p_value",
        "best_strategy",
        "interpretation",
        "caveats",
    ):
        assert key in body
    assert body["best_strategy"] in ("s1", "s2")
    assert isinstance(body["caveats"], list) and body["caveats"]


def test_empty_db_exits_1_no_json(db_session, db_url, monkeypatch, capsys):
    from marketpulse.cli.permutation_test import main

    _point_cli_at_db(monkeypatch, db_url)

    with pytest.raises(SystemExit) as excinfo:
        main(argv=["--permutations", "200"])
    assert excinfo.value.code == 1

    captured = capsys.readouterr()
    assert "no rows" in captured.err
    assert captured.out == ""  # no partial JSON


def test_seed_determinism_and_variation(db_session, db_url, monkeypatch, capsys):
    from marketpulse.cli.permutation_test import main

    _seed_mixed(db_session)
    _point_cli_at_db(monkeypatch, db_url)

    main(argv=["--permutations", "200", "--seed", "1"])
    out_a = capsys.readouterr().out
    main(argv=["--permutations", "200", "--seed", "1"])
    out_b = capsys.readouterr().out
    assert out_a == out_b  # same seed -> byte-identical report

    main(argv=["--permutations", "200", "--seed", "2"])
    out_c = capsys.readouterr().out
    null_mean_1 = json.loads(out_a)["overall_null_mean"]
    null_mean_2 = json.loads(out_c)["overall_null_mean"]
    assert null_mean_1 != null_mean_2
