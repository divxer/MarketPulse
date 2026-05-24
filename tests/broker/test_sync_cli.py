# Layer: stateful
from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass
class FakeResult:
    sync_run_id: int = 123
    broker: str = "IBKR"
    broker_environment: str = "paper"
    account_id: str | None = "DU123"
    status: str = "completed"
    transport: str = "flex"
    endpoint: str = "https://gdcdyn.interactivebrokers.com/Universal/servlet"
    query_id: int | None = 456
    reference_code: str | None = "REF-789"
    account_snapshots: int = 1
    cash_rows: int = 2
    positions: int = 5
    open_orders: int = 0
    executions: int = 3
    error_type: str | None = None
    error_message: str | None = None


def _set_required_env(monkeypatch) -> None:
    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "x" * 16)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


def test_cli_prints_completed_summary(monkeypatch, capsys):
    from scripts import sync_ibkr_readonly as cli

    _set_required_env(monkeypatch)
    monkeypatch.setenv("IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setenv("IBKR_FLEX_QUERY_ID", "456")
    monkeypatch.setattr(cli, "_run", lambda args: FakeResult())

    code = cli.main([])

    assert code == 0
    out = capsys.readouterr().out
    assert "sync_run_id: 123" in out
    assert "broker: IBKR" in out
    assert "broker_environment: paper" in out
    assert "account: DU123" in out
    assert "transport: flex" in out
    assert "endpoint: https://gdcdyn.interactivebrokers.com/Universal/servlet" in out
    assert "query_id: 456" in out
    assert "reference_code: REF-789" in out
    assert "positions: 5" in out
    assert "open orders: 0 (not available via Flex Activity)" in out


def test_cli_prints_failed_summary(monkeypatch, capsys):
    from scripts import sync_ibkr_readonly as cli

    _set_required_env(monkeypatch)
    monkeypatch.setenv("IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setenv("IBKR_FLEX_QUERY_ID", "456")
    result = FakeResult(
        status="failed",
        account_id=None,
        error_type="FlexReportTimeoutError",
        error_message="Flex report not ready after 60s",
        account_snapshots=0,
        cash_rows=0,
        positions=0,
        open_orders=0,
        executions=0,
    )
    monkeypatch.setattr(cli, "_run", lambda args: result)

    code = cli.main([])

    assert code == 1
    out = capsys.readouterr().out
    assert "status: failed" in out
    assert "error_type: FlexReportTimeoutError" in out
    assert "error_message: Flex report not ready after 60s" in out
    # reference_code is still printed on failure (when present)
    assert "reference_code: REF-789" in out


def test_cli_omits_reference_code_when_absent(monkeypatch, capsys):
    from scripts import sync_ibkr_readonly as cli

    _set_required_env(monkeypatch)
    monkeypatch.setenv("IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setenv("IBKR_FLEX_QUERY_ID", "456")
    monkeypatch.setattr(cli, "_run", lambda args: FakeResult(reference_code=None))

    code = cli.main([])

    assert code == 0
    out = capsys.readouterr().out
    assert "reference_code:" not in out


def test_cli_config_prefers_args_over_settings(monkeypatch):
    from scripts import sync_ibkr_readonly as cli
    from marketpulse.config import get_settings

    _set_required_env(monkeypatch)
    monkeypatch.setenv("IBKR_FLEX_TOKEN", "env-tok")
    get_settings.cache_clear()
    monkeypatch.setenv("IBKR_FLEX_QUERY_ID", "111")
    monkeypatch.setenv("IBKR_FLEX_BASE_URL", "https://env-base.example/servlet")
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DUENV")
    monkeypatch.setenv("IBKR_FLEX_POLL_INTERVAL_SECONDS", "5")
    monkeypatch.setenv("IBKR_FLEX_MAX_WAIT_SECONDS", "60")
    monkeypatch.setenv("MP_IBKR_ALLOW_LIVE", "false")

    args = cli.build_parser().parse_args([
        "--token", "arg-tok",
        "--query-id", "222",
        "--base-url", "https://arg-base.example/servlet",
        "--account-id", "DUARG",
        "--poll-interval-seconds", "3",
        "--max-wait-seconds", "30",
        "--db-url", "sqlite:///arg.db",
    ])
    config, db_url = cli._config(args)

    assert config.token == "arg-tok"
    assert config.query_id == 222
    assert config.base_url == "https://arg-base.example/servlet"
    assert config.account_id == "DUARG"
    assert config.poll_interval_seconds == 3
    assert config.max_wait_seconds == 30
    assert config.allow_live is False
    assert db_url == "sqlite:///arg.db"


def test_cli_missing_token_exits(monkeypatch):
    from scripts import sync_ibkr_readonly as cli
    from marketpulse.config import get_settings

    _set_required_env(monkeypatch)
    monkeypatch.delenv("IBKR_FLEX_TOKEN", raising=False)
    monkeypatch.setenv("IBKR_FLEX_QUERY_ID", "456")
    get_settings.cache_clear()

    args = cli.build_parser().parse_args([])
    try:
        with pytest.raises(SystemExit, match="IBKR_FLEX_TOKEN"):
            cli._config(args)
    finally:
        get_settings.cache_clear()


def test_cli_missing_query_id_exits(monkeypatch):
    from scripts import sync_ibkr_readonly as cli
    from marketpulse.config import get_settings

    _set_required_env(monkeypatch)
    monkeypatch.setenv("IBKR_FLEX_TOKEN", "tok")
    monkeypatch.delenv("IBKR_FLEX_QUERY_ID", raising=False)
    get_settings.cache_clear()

    args = cli.build_parser().parse_args([])
    try:
        with pytest.raises(SystemExit, match="IBKR_FLEX_QUERY_ID"):
            cli._config(args)
    finally:
        get_settings.cache_clear()
