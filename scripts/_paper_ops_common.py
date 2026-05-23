"""Shared helpers for Phase 6h paper-trading ops scripts.

Read-only utilities only. This module must not mutate paper trading state.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

# Allow direct script execution via commands like
# `uv run python scripts/check_paper_trading_health.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marketpulse.db.models import (  # noqa: E402
    PaperAuditEvent,
    PaperCashLedger,
    PaperFill,
    PaperOrder,
    PaperPosition,
)

DEFAULT_DB_URL = "sqlite:///./data/marketpulse.db"
PAPER_TABLE_MODELS = {
    "paper_order": PaperOrder,
    "paper_fill": PaperFill,
    "paper_position": PaperPosition,
    "paper_cash_ledger": PaperCashLedger,
    "paper_audit_event": PaperAuditEvent,
}


def resolve_db_url(db_url: str | None) -> str:
    return db_url or os.getenv("MARKETPULSE_DB_URL", DEFAULT_DB_URL)


@contextmanager
def session_from_url(db_url: str) -> Iterator[Session]:
    engine = create_engine(db_url)
    with Session(engine) as session:
        yield session


def count_paper_tables(session: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, model in PAPER_TABLE_MODELS.items():
        counts[name] = int(session.execute(select(func.count(model.id))).scalar() or 0)
    return counts
