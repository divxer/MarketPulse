# Layer: db
"""Backfill-rule correctness for the price_cache is_final migration (spec §1).

The inlined migration rule must match marketpulse.data.finality semantics.
The real upgrade() is exercised by the deploy-time scratch-DB run (plan T8);
these tests pin the rule's DST behavior so it can't silently diverge.
"""
from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path


def _load_migration():
    path = next(Path("alembic/versions").glob("*price_cache_is_final*.py"))
    spec = importlib.util.spec_from_file_location("mig", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backfill_rule_intraday_edt_provisional():
    mig = _load_migration()
    assert mig._is_final(date(2026, 6, 10), datetime(2026, 6, 10, 16, 30, tzinfo=UTC)) is False


def test_backfill_rule_evening_edt_final():
    mig = _load_migration()
    assert mig._is_final(date(2026, 6, 10), datetime(2026, 6, 10, 21, 30, tzinfo=UTC)) is True


def test_backfill_rule_est_winter():
    mig = _load_migration()
    assert mig._is_final(date(2026, 1, 15), datetime(2026, 1, 15, 21, 4, tzinfo=UTC)) is False
    assert mig._is_final(date(2026, 1, 15), datetime(2026, 1, 15, 21, 6, tzinfo=UTC)) is True


def test_backfill_rule_naive_is_utc():
    mig = _load_migration()
    assert mig._is_final(date(2026, 6, 10), datetime(2026, 6, 10, 16, 30)) is False
