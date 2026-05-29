# Layer: test
"""PR3a — scheduler-level isolation test for the NAV snapshot hook.

Locks tested: L4 (runner errors visible; tick not aborted).
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from marketpulse.db.models import PaperCashLedger


def test_run_nav_snapshot_safely_logs_and_swallows(
    db_session, monkeypatch,
):
    """Unit test on the wrapper: when run_nav_snapshot raises, the wrapper
    swallows + logs a warning. This proves L4 at the boundary; we DO NOT
    here exercise the full run_paper_trading_tick (that would require
    much more fixture state). Integration of the wrapper into the tick
    is verified separately by the existing scheduler suite still passing
    in Task 13."""
    from marketpulse.scheduler import jobs as jobs_mod

    # Seed enough state that the runner would have succeeded.
    db_session.add(PaperCashLedger(
        timestamp=datetime(2026, 5, 28, 13, 0, tzinfo=UTC),
        delta=Decimal("100000"), reason="INITIAL_DEPOSIT",
        fill_id=None, balance_after=Decimal("100000"),
    ))
    db_session.commit()

    def boom(session, *, trading_date):  # noqa: ANN001
        raise RuntimeError("simulated disk full")

    monkeypatch.setattr(jobs_mod, "run_nav_snapshot", boom)

    # Capture structlog warning calls on the module-level `log` object.
    # jobs.py uses structlog (get_logger), which doesn't route through
    # pytest's caplog handler — so we monkeypatch the bound method directly.
    warnings_emitted: list[tuple[str, dict]] = []

    def _capture_warning(event, **kw):  # noqa: ANN001
        warnings_emitted.append((event, kw))

    monkeypatch.setattr(jobs_mod.log, "warning", _capture_warning)

    # The hook wrapper must not raise.
    jobs_mod._run_nav_snapshot_safely(db_session, tick_date=date(2026, 5, 28))

    # And the warning must be emitted.
    assert any(
        "nav_snapshot_failed" in event
        for event, _ in warnings_emitted
    )


def test_run_nav_snapshot_safely_commits_so_row_survives_session_close(
    db_session, db_url,
):
    """Regression: the hook MUST commit. Otherwise the snapshot is only
    flushed (not committed) and gets rolled back when the tick's session
    closes — leaving paper_nav_snapshot empty forever (observed in prod:
    8 fills / 5 ticks but 0 snapshots). Verify persistence from a SEPARATE
    connection, which only sees COMMITTED rows.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session as SASession

    db_session.add(PaperCashLedger(
        timestamp=datetime(2026, 5, 28, 13, 0, tzinfo=UTC),
        delta=Decimal("100000"), reason="INITIAL_DEPOSIT",
        fill_id=None, balance_after=Decimal("100000"),
    ))
    db_session.commit()

    from marketpulse.scheduler import jobs as jobs_mod
    jobs_mod._run_nav_snapshot_safely(db_session, tick_date=date(2026, 5, 28))

    # Read from a fresh connection — committed data only.
    engine = create_engine(db_url)
    with SASession(engine) as fresh:
        count = fresh.execute(
            text("SELECT COUNT(*) FROM paper_nav_snapshot"),
        ).scalar()
    assert count == 1, (
        "snapshot must be committed so it survives the tick session closing"
    )
