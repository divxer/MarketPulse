# Layer: invariant
"""6a-1: paper_* model classes wired into Base."""


def test_paper_models_have_expected_tablenames():
    from marketpulse.db.models import (
        PaperAuditEvent, PaperCashLedger, PaperFill, PaperOrder, PaperPosition,
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
