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
