# Layer: test
"""PR3b — charter_review orchestration tests."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from marketpulse.ops.charter_review import (
    _atomic_write_text,
    _read_backup_manifest,
)


def test_read_backup_manifest_missing_returns_none(tmp_path):
    assert _read_backup_manifest(tmp_path / "nope.json") is None


def test_read_backup_manifest_malformed_returns_none(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text("{not json", encoding="utf-8")
    assert _read_backup_manifest(p) is None


def test_read_backup_manifest_ok(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    parsed = _read_backup_manifest(p)
    assert parsed == {"status": "ok"}


def test_atomic_write_text_creates_new_file(tmp_path):
    target = tmp_path / "out.md"
    _atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"
    leftovers = sorted(tmp_path.glob(".*.tmp"))
    assert leftovers == []


def test_atomic_write_text_replaces_existing(tmp_path):
    target = tmp_path / "out.md"
    target.write_text("old", encoding="utf-8")
    _atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    leftovers = sorted(tmp_path.glob(".*.tmp"))
    assert leftovers == []


def test_atomic_write_text_preserves_old_on_replace_failure(tmp_path, monkeypatch):
    target = tmp_path / "out.md"
    target.write_text("old", encoding="utf-8")

    def boom(src, dst):
        raise OSError("simulated replace failure")

    import marketpulse.ops.charter_review as cr_mod
    monkeypatch.setattr(cr_mod, "_os_replace", boom)

    with pytest.raises(OSError):
        _atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "old"
    leftovers = sorted(tmp_path.glob(".*.tmp"))
    assert leftovers == []


import json as _json
from datetime import date as _date
from datetime import datetime as _dt
import logging as _logging

from marketpulse.ops.charter_review import (
    CharterReviewError,
    generate_charter_review,
)


def test_generate_writes_markdown_and_latest_json(db_session, tmp_path):
    recaps = tmp_path / "charter"
    manifest_path = tmp_path / "manifest.json"   # missing
    out_path = generate_charter_review(
        session=db_session,
        week_ending=_date(2026, 8, 16),
        now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
        recaps_dir=recaps,
        backup_manifest_path=manifest_path,
    )
    assert out_path == recaps / "2026-08-16.md"
    assert out_path.exists()
    assert "# Charter Review" in out_path.read_text(encoding="utf-8")
    latest = recaps / "latest.json"
    assert latest.exists()
    parsed = _json.loads(latest.read_text(encoding="utf-8"))
    assert parsed["week_ending"] == "2026-08-16"
    assert parsed["path"] == str(out_path)
    assert parsed["schema_version"] == 1


def test_generate_validates_week_ending_is_sunday(db_session, tmp_path):
    with pytest.raises(CharterReviewError, match="Sunday"):
        generate_charter_review(
            session=db_session,
            week_ending=_date(2026, 8, 15),   # Saturday
            now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
            recaps_dir=tmp_path / "charter",
            backup_manifest_path=tmp_path / "m.json",
        )


def test_generate_idempotent_same_week_same_now(db_session, tmp_path):
    common = dict(
        session=db_session,
        week_ending=_date(2026, 8, 16),
        now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
        recaps_dir=tmp_path / "charter",
        backup_manifest_path=tmp_path / "m.json",
    )
    p1 = generate_charter_review(**common)
    body1 = p1.read_text(encoding="utf-8")
    p2 = generate_charter_review(**common)
    body2 = p2.read_text(encoding="utf-8")
    assert body1 == body2


def test_generate_missing_manifest_lands_file(db_session, tmp_path):
    p = generate_charter_review(
        session=db_session,
        week_ending=_date(2026, 8, 16),
        now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
        recaps_dir=tmp_path / "charter",
        backup_manifest_path=tmp_path / "absent.json",
    )
    text = p.read_text(encoding="utf-8")
    assert "Backup manifest unavailable" in text


def test_generate_malformed_manifest_lands_file(db_session, tmp_path):
    manifest_path = tmp_path / "m.json"
    manifest_path.write_text("{not json", encoding="utf-8")
    p = generate_charter_review(
        session=db_session,
        week_ending=_date(2026, 8, 16),
        now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
        recaps_dir=tmp_path / "charter",
        backup_manifest_path=manifest_path,
    )
    text = p.read_text(encoding="utf-8")
    assert "Backup manifest unavailable" in text


def test_generate_db_query_failure_raises_typed(db_session, tmp_path, monkeypatch):
    from marketpulse.ops import charter_review as cr_mod

    def boom(**kwargs):
        raise RuntimeError("simulated aggregator failure")

    monkeypatch.setattr(cr_mod, "build_payload", boom)
    with pytest.raises(CharterReviewError):
        generate_charter_review(
            session=db_session,
            week_ending=_date(2026, 8, 16),
            now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
            recaps_dir=tmp_path / "charter",
            backup_manifest_path=tmp_path / "m.json",
        )


def test_generate_success_emits_info_log(db_session, tmp_path, caplog):
    caplog.set_level(_logging.INFO, logger="marketpulse.ops.charter_review")
    generate_charter_review(
        session=db_session,
        week_ending=_date(2026, 8, 16),
        now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
        recaps_dir=tmp_path / "charter",
        backup_manifest_path=tmp_path / "m.json",
    )
    matches = [r for r in caplog.records
               if "charter_review_generated" in r.getMessage()]
    assert len(matches) == 1
