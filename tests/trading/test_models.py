# Layer: invariant
"""6a-1: paper_* model classes wired into Base."""


def test_paper_models_have_expected_tablenames():
    from marketpulse.db.models import (
        PaperAuditEvent,
        PaperCashLedger,
        PaperFill,
        PaperOrder,
        PaperPosition,
    )
    assert PaperOrder.__tablename__ == "paper_order"
    assert PaperFill.__tablename__ == "paper_fill"
    assert PaperPosition.__tablename__ == "paper_position"
    assert PaperCashLedger.__tablename__ == "paper_cash_ledger"
    assert PaperAuditEvent.__tablename__ == "paper_audit_event"


def test_paper_order_has_allocation_date_column():
    """6a-L5 / lock xxxiii companion: paper_order distinguishes
    event_time (AI saw it), allocation_date (allocator decision day),
    placed_at (DB write time)."""
    from sqlalchemy import inspect

    from marketpulse.db.models import PaperOrder

    cols = {c.name for c in inspect(PaperOrder).columns}
    assert "event_time" in cols
    assert "allocation_date" in cols
    assert "placed_at" in cols


def test_paper_order_has_versioning_columns():
    """Lock xxviii: replay determinism."""
    from sqlalchemy import inspect

    from marketpulse.db.models import PaperOrder

    cols = {c.name for c in inspect(PaperOrder).columns}
    assert {"strategy_version", "allocator_version", "execution_engine_version"} <= cols


def test_migration_creates_and_drops_paper_tables(tmp_path, monkeypatch):
    """0010 migration creates all 5 paper_* tables on upgrade; drops them on downgrade."""
    import subprocess

    db_file = tmp_path / "smoke.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")

    # Force settings cache invalidation if needed
    from marketpulse.config import get_settings
    get_settings.cache_clear()

    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)

    # All 5 tables exist
    from sqlalchemy import create_engine, inspect
    eng = create_engine(f"sqlite:///{db_file}")
    insp = inspect(eng)
    tables = set(insp.get_table_names())
    assert {"paper_order", "paper_fill", "paper_position",
            "paper_cash_ledger", "paper_audit_event"} <= tables

    # Downgrade by one revision and confirm removal.
    subprocess.run(["uv", "run", "alembic", "downgrade", "-1"], check=True)
    insp = inspect(eng)
    tables = set(insp.get_table_names())
    assert "paper_order" not in tables
