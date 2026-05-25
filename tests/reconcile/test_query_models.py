"""Phase 7c - query model integration.

Covers account picking, ambiguity, symbol normalization, aggregation,
stale-snapshot detection, and hero severity.
"""
# Layer: stateful
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from marketpulse.db.base import Base
from marketpulse.db.models import (
    BrokerPositionSnapshot,
    BrokerSyncRun,
    PaperOrder,
    PaperPosition,
)
from marketpulse.reconcile.query_models import load_reconciliation_dashboard
from marketpulse.reconcile.types import DiffType, Severity


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _make_completed_run(
    db: Session,
    *,
    started_at: datetime,
    account_id: str = "DU123",
    reference_code: str = "REF-1",
) -> BrokerSyncRun:
    run = BrokerSyncRun(
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=10),
        broker="IBKR",
        broker_environment="paper",
        account_id=account_id,
        status="completed",
        context={"reference_code": reference_code},
    )
    db.add(run)
    db.flush()
    return run


def _make_failed_run(
    db: Session,
    *,
    started_at: datetime,
    account_id: str | None,
) -> BrokerSyncRun:
    run = BrokerSyncRun(
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=5),
        broker="IBKR",
        broker_environment="paper",
        account_id=account_id,
        status="failed",
        error_type="FlexHttpError",
        error_message="503",
        context={},
    )
    db.add(run)
    db.flush()
    return run


def _add_broker_position(
    db: Session,
    run: BrokerSyncRun,
    *,
    symbol: str,
    quantity: Decimal,
) -> None:
    db.add(
        BrokerPositionSnapshot(
            sync_run_id=run.id,
            account_id=run.account_id or "DU123",
            broker_environment=run.broker_environment,
            captured_at=run.completed_at or run.started_at,
            symbol=symbol,
            asset_class="STK",
            quantity=quantity,
        )
    )


def _add_paper_position(
    db: Session,
    *,
    ticker: str,
    quantity: int,
    closed: bool = False,
    idempotency_suffix: str = "",
) -> None:
    now = datetime.now(UTC)
    order = PaperOrder(
        idempotency_key=f"k_{ticker}_{quantity}{idempotency_suffix}"[:32],
        allocation_run_id="run-1",
        strategy="general",
        ticker=ticker,
        quantity=quantity,
        event_time=now,
        allocation_date=date(2026, 5, 25),
        horizon_date=date(2026, 6, 1),
        placed_at=now,
        filled_at=now,
        event_price=Decimal("100"),
        status="ENTRY_FILLED",
        strategy_version="v1",
        allocator_version="v1",
        execution_engine_version="v1",
        weight=1.0,
    )
    db.add(order)
    db.flush()
    db.add(
        PaperPosition(
            order_id=order.id,
            entry_fill_id=1,
            exit_fill_id=99 if closed else None,
            strategy="general",
            ticker=ticker,
            quantity=quantity,
            entry_price=Decimal("100"),
            entry_date=date(2026, 5, 25),
            horizon_date=date(2026, 6, 1),
            status="CLOSED" if closed else "OPEN",
            opened_at=now,
        )
    )
    db.flush()


def test_empty_db_yields_no_broker_data_gray():
    db = _session()
    dash = load_reconciliation_dashboard(db)
    assert dash.no_broker_data is True
    assert dash.account_ambiguous is False
    assert dash.severity == Severity.GRAY
    assert dash.rows == ()


def test_only_failed_runs_yields_no_broker_data_not_ambiguous():
    db = _session()
    base = datetime(2026, 5, 25, 12, tzinfo=UTC)
    _make_failed_run(db, started_at=base, account_id="DU-A")
    _make_failed_run(db, started_at=base + timedelta(hours=1), account_id="DU-B")
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.no_broker_data is True
    assert dash.account_ambiguous is False
    assert dash.severity == Severity.GRAY
    assert dash.recent_failed_run_descriptions


def test_multi_account_completed_runs_yields_ambiguous(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "")
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    db = _session()
    base = datetime(2026, 5, 25, 12, tzinfo=UTC)
    _make_completed_run(db, started_at=base, account_id="DU-A")
    _make_completed_run(db, started_at=base + timedelta(hours=1), account_id="DU-B")
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.account_ambiguous is True
    assert dash.severity == Severity.GRAY
    get_settings.cache_clear()


def test_settings_override_picks_account_in_multi_account_history(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-B")
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    db = _session()
    base = datetime(2026, 5, 25, 12, tzinfo=UTC)
    _make_completed_run(db, started_at=base, account_id="DU-A")
    run_b = _make_completed_run(db, started_at=base + timedelta(hours=1), account_id="DU-B")
    _add_broker_position(db, run_b, symbol="AAPL", quantity=Decimal("100"))
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.account_ambiguous is False
    assert dash.broker_account_id == "DU-B"
    assert len(dash.rows) == 1
    assert dash.rows[0].symbol == "AAPL"
    get_settings.cache_clear()


def test_all_matched_yields_green(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    db = _session()
    base = datetime.now(UTC) - timedelta(hours=1)
    run = _make_completed_run(db, started_at=base, account_id="DU-A")
    _add_broker_position(db, run, symbol="AAPL", quantity=Decimal("100"))
    _add_paper_position(db, ticker="AAPL", quantity=100)
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.severity == Severity.GREEN
    assert dash.matched_count == 1
    assert dash.rows[0].diff_type == DiffType.MATCHED
    get_settings.cache_clear()


def test_symbol_normalization_lowercase_vs_uppercase(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    db = _session()
    base = datetime.now(UTC) - timedelta(hours=1)
    run = _make_completed_run(db, started_at=base, account_id="DU-A")
    _add_broker_position(db, run, symbol="aapl", quantity=Decimal("100"))
    _add_paper_position(db, ticker="AAPL", quantity=100)
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.matched_count == 1
    assert dash.rows[0].symbol == "AAPL"
    get_settings.cache_clear()


def test_broker_side_aggregation(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    db = _session()
    base = datetime.now(UTC) - timedelta(hours=1)
    run = _make_completed_run(db, started_at=base, account_id="DU-A")
    _add_broker_position(db, run, symbol="AAPL", quantity=Decimal("50"))
    _add_broker_position(db, run, symbol="AAPL", quantity=Decimal("50"))
    _add_paper_position(db, ticker="AAPL", quantity=100)
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.matched_count == 1
    assert dash.rows[0].broker_qty == Decimal("100.000000")
    get_settings.cache_clear()


def test_paper_side_aggregation(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    db = _session()
    base = datetime.now(UTC) - timedelta(hours=1)
    run = _make_completed_run(db, started_at=base, account_id="DU-A")
    _add_broker_position(db, run, symbol="AAPL", quantity=Decimal("100"))
    _add_paper_position(db, ticker="AAPL", quantity=50, idempotency_suffix="_lot1")
    _add_paper_position(db, ticker="AAPL", quantity=50, idempotency_suffix="_lot2")
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.matched_count == 1
    assert dash.rows[0].paper_qty == Decimal("100")
    get_settings.cache_clear()


def test_closed_paper_positions_excluded(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    db = _session()
    base = datetime.now(UTC) - timedelta(hours=1)
    run = _make_completed_run(db, started_at=base, account_id="DU-A")
    _add_broker_position(db, run, symbol="MSFT", quantity=Decimal("10"))
    _add_paper_position(db, ticker="AAPL", quantity=100, closed=True)
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert {r.symbol: r.diff_type for r in dash.rows} == {"MSFT": DiffType.MISSING_IN_PAPER}
    get_settings.cache_clear()


def test_stale_broker_snapshot_deterministic(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    db = _session()
    run_started = datetime(2026, 5, 24, 12, tzinfo=UTC)
    run = _make_completed_run(db, started_at=run_started, account_id="DU-A")
    _add_broker_position(db, run, symbol="AAPL", quantity=Decimal("100"))
    _add_paper_position(db, ticker="AAPL", quantity=100)
    db.commit()

    fresh = load_reconciliation_dashboard(db, now=run.completed_at + timedelta(hours=23, minutes=59))
    assert fresh.broker_is_stale is False
    assert fresh.severity == Severity.GREEN

    stale = load_reconciliation_dashboard(db, now=run.completed_at + timedelta(hours=24, minutes=1))
    assert stale.broker_is_stale is True
    assert stale.severity == Severity.GREEN
    get_settings.cache_clear()


def test_latest_completed_run_picked_when_multiple_on_same_account(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    db = _session()
    base = datetime.now(UTC) - timedelta(hours=10)
    old_run = _make_completed_run(db, started_at=base, account_id="DU-A", reference_code="REF-OLD")
    _add_broker_position(db, old_run, symbol="AAPL", quantity=Decimal("50"))
    new_run = _make_completed_run(
        db,
        started_at=base + timedelta(hours=1),
        account_id="DU-A",
        reference_code="REF-NEW",
    )
    _add_broker_position(db, new_run, symbol="AAPL", quantity=Decimal("100"))
    _add_paper_position(db, ticker="AAPL", quantity=100)
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.broker_reference_code == "REF-NEW"
    assert dash.matched_count == 1
    get_settings.cache_clear()


def test_hero_severity_red_on_side_mismatch(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    db = _session()
    base = datetime.now(UTC) - timedelta(hours=1)
    run = _make_completed_run(db, started_at=base, account_id="DU-A")
    _add_broker_position(db, run, symbol="AAPL", quantity=Decimal("-10"))
    _add_paper_position(db, ticker="AAPL", quantity=10)
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.severity == Severity.RED
    assert dash.side_mismatch_count == 1
    get_settings.cache_clear()


def test_hero_severity_red_on_missing_in_broker_with_paper_qty(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    db = _session()
    base = datetime.now(UTC) - timedelta(hours=1)
    _make_completed_run(db, started_at=base, account_id="DU-A")
    _add_paper_position(db, ticker="AAPL", quantity=10)
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.severity == Severity.RED
    assert dash.missing_in_broker_count == 1
    get_settings.cache_clear()


def test_hero_severity_yellow_on_single_quantity_mismatch(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    db = _session()
    base = datetime.now(UTC) - timedelta(hours=1)
    run = _make_completed_run(db, started_at=base, account_id="DU-A")
    _add_broker_position(db, run, symbol="AAPL", quantity=Decimal("50"))
    _add_paper_position(db, ticker="AAPL", quantity=100)
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.severity == Severity.YELLOW
    assert dash.quantity_mismatch_count == 1
    get_settings.cache_clear()


def test_hero_severity_red_on_three_plus_mismatches(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DU-A")
    from marketpulse.config import get_settings

    get_settings.cache_clear()
    db = _session()
    base = datetime.now(UTC) - timedelta(hours=1)
    run = _make_completed_run(db, started_at=base, account_id="DU-A")
    for symbol in ("AAPL", "MSFT", "GOOG"):
        _add_broker_position(db, run, symbol=symbol, quantity=Decimal("50"))
        _add_paper_position(db, ticker=symbol, quantity=100)
    db.commit()

    dash = load_reconciliation_dashboard(db)
    assert dash.severity == Severity.RED
    assert dash.quantity_mismatch_count == 3
    get_settings.cache_clear()
