# Layer: stateful
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FakeResult:
    sync_run_id: int = 123
    broker: str = "IBKR"
    broker_environment: str = "paper"
    account_id: str | None = "DU123"
    status: str = "completed"
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 71
    account_snapshots: int = 1
    cash_rows: int = 2
    positions: int = 5
    open_orders: int = 0
    executions: int = 3
    error_type: str | None = None
    error_message: str | None = None


def test_cli_prints_completed_summary(monkeypatch, capsys):
    from scripts import sync_ibkr_readonly as cli

    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "x" * 16)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(cli, "_run", lambda args: FakeResult())

    code = cli.main([])

    assert code == 0
    out = capsys.readouterr().out
    assert "sync_run_id: 123" in out
    assert "broker: IBKR" in out
    assert "broker_environment: paper" in out
    assert "account: DU123" in out
    assert "positions: 5" in out


def test_cli_prints_failed_summary(monkeypatch, capsys):
    from scripts import sync_ibkr_readonly as cli

    result = FakeResult(status="failed", account_id=None, error_type="ConnectionError",
                        error_message="down", account_snapshots=0, cash_rows=0,
                        positions=0, open_orders=0, executions=0)
    monkeypatch.setattr(cli, "_run", lambda args: result)

    code = cli.main([])

    assert code == 1
    out = capsys.readouterr().out
    assert "status: failed" in out
    assert "error_type: ConnectionError" in out
    assert "error_message: down" in out


def test_cli_config_prefers_args_over_settings(monkeypatch):
    from scripts import sync_ibkr_readonly as cli

    monkeypatch.setenv("APP_PASSWORD_HASH", "x")
    monkeypatch.setenv("SESSION_SECRET", "x" * 16)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("IBKR_HOST", "env-host")
    monkeypatch.setenv("IBKR_PORT", "7497")
    monkeypatch.setenv("IBKR_CLIENT_ID", "71")
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DUENV")
    monkeypatch.setenv("IBKR_CONNECT_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("MP_IBKR_ALLOW_LIVE", "false")

    args = cli.build_parser().parse_args([
        "--host", "arg-host",
        "--port", "7496",
        "--client-id", "72",
        "--account-id", "DUARG",
        "--timeout-seconds", "3",
        "--db-url", "sqlite:///arg.db",
    ])
    config, db_url = cli._config(args)

    assert config.host == "arg-host"
    assert config.port == 7496
    assert config.client_id == 72
    assert config.account_id == "DUARG"
    assert config.timeout_seconds == 3
    assert config.allow_live is False
    assert db_url == "sqlite:///arg.db"
