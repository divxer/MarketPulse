# Layer: test
"""PR3b — charter_review orchestration tests."""
from __future__ import annotations

import json as _json
from datetime import UTC
from datetime import date as _date
from datetime import datetime as _dt

import pytest

from marketpulse.ops.charter_review import (
    CharterReviewError,
    _atomic_write_text,
    generate_charter_review,
)


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


def test_generate_reads_real_pr1_manifest_via_pr2_normalizer(db_session, tmp_path):
    """Regression: a healthy backup written in PR1's RAW manifest shape
    (`timestamp`, no `is_stale`/`last_backup_at`) must render as a real,
    fresh backup — not 'unavailable' and not N/A. This fails if the
    orchestration reads raw manifest keys instead of routing through PR2's
    build_backup_section, which computes staleness from `timestamp`."""
    manifest_path = tmp_path / "m.json"
    # Exactly the shape marketpulse.ops.backup.write_manifest produces.
    manifest_path.write_text(
        _json.dumps({
            "status": "ok",
            "timestamp": "2026-08-17T09:00:00+00:00",
            "source": "/data/marketpulse.db",
            "destination": "/data/backups/marketpulse-2026-08-17.db",
            "size_bytes": 1024,
            "integrity_check": "ok",
            "duration_ms": 42,
            "error": None,
        }),
        encoding="utf-8",
    )
    p = generate_charter_review(
        session=db_session,
        week_ending=_date(2026, 8, 16),
        now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),  # 30 min after backup → fresh
        recaps_dir=tmp_path / "charter",
        backup_manifest_path=manifest_path,
    )
    text = p.read_text(encoding="utf-8")
    assert "Backup manifest unavailable" not in text
    assert "Backup status: ok" in text
    assert "Last successful backup: 2026-08-17T09:00:00+00:00" in text
    assert "Stale (>25h): False" in text


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


def test_generate_success_emits_info_log(db_session, tmp_path, monkeypatch):
    """Capture the log.info call directly — caplog is unreliable in the full
    suite because earlier tests reconfigure root handlers."""
    from marketpulse.ops import charter_review as cr_mod
    calls: list[tuple[str, dict]] = []

    def _capture(event, *args, **kwargs):
        calls.append((event, kwargs.get("extra", {})))

    monkeypatch.setattr(cr_mod.log, "info", _capture)
    generate_charter_review(
        session=db_session,
        week_ending=_date(2026, 8, 16),
        now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
        recaps_dir=tmp_path / "charter",
        backup_manifest_path=tmp_path / "m.json",
    )
    matches = [c for c in calls if c[0] == "charter_review_generated"]
    assert len(matches) == 1
    assert matches[0][1]["week_ending"] == "2026-08-16"


def test_generate_atomic_write_preserves_old_md_on_failure(
    db_session, tmp_path, monkeypatch,
):
    recaps = tmp_path / "charter"
    recaps.mkdir()
    old_md = recaps / "2026-08-16.md"
    old_md.write_text("OLD CONTENT", encoding="utf-8")

    import marketpulse.ops.charter_review as cr_mod
    real_replace = cr_mod._os_replace

    def boom_for_md(src, dst):
        if str(dst).endswith(".md"):
            raise OSError("simulated .md replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(cr_mod, "_os_replace", boom_for_md)

    with pytest.raises(CharterReviewError):
        generate_charter_review(
            session=db_session,
            week_ending=_date(2026, 8, 16),
            now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
            recaps_dir=recaps,
            backup_manifest_path=tmp_path / "m.json",
        )

    assert old_md.read_text(encoding="utf-8") == "OLD CONTENT"
    orphans = sorted(recaps.glob(".*.tmp"))
    assert orphans == []


def test_generate_atomic_write_preserves_old_latest_json_on_failure(
    db_session, tmp_path, monkeypatch,
):
    recaps = tmp_path / "charter"
    recaps.mkdir()
    old_json = recaps / "latest.json"
    old_json.write_text("OLD JSON", encoding="utf-8")

    import marketpulse.ops.charter_review as cr_mod
    real_replace = cr_mod._os_replace

    def boom_for_json(src, dst):
        if str(dst).endswith("latest.json"):
            raise OSError("simulated latest.json replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(cr_mod, "_os_replace", boom_for_json)

    with pytest.raises(CharterReviewError):
        generate_charter_review(
            session=db_session,
            week_ending=_date(2026, 8, 16),
            now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
            recaps_dir=recaps,
            backup_manifest_path=tmp_path / "m.json",
        )

    assert old_json.read_text(encoding="utf-8") == "OLD JSON"
    orphans = sorted(recaps.glob(".*.tmp"))
    assert orphans == []


def test_generate_atomic_write_no_orphan_tempfiles(db_session, tmp_path):
    generate_charter_review(
        session=db_session,
        week_ending=_date(2026, 8, 16),
        now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
        recaps_dir=tmp_path / "charter",
        backup_manifest_path=tmp_path / "m.json",
    )
    orphans = sorted((tmp_path / "charter").glob(".*.tmp"))
    assert orphans == []


def test_generate_latest_json_atomic_replace(db_session, tmp_path):
    common = dict(
        session=db_session,
        recaps_dir=tmp_path / "charter",
        backup_manifest_path=tmp_path / "m.json",
    )
    generate_charter_review(
        week_ending=_date(2026, 8, 9), now=_dt(2026, 8, 10, 9, 30, tzinfo=UTC),
        **common,
    )
    generate_charter_review(
        week_ending=_date(2026, 8, 16), now=_dt(2026, 8, 17, 9, 30, tzinfo=UTC),
        **common,
    )
    latest = (tmp_path / "charter" / "latest.json")
    parsed = _json.loads(latest.read_text(encoding="utf-8"))
    assert parsed["week_ending"] == "2026-08-16"
    orphans = sorted((tmp_path / "charter").glob(".*.tmp"))
    assert orphans == []
