# Layer: ops
"""PR3b — orchestration entry for the weekly charter review.

Reads backup manifest, calls aggregator + renderer, atomically writes
the markdown and the latest.json companion (L10/L11). May raise
CharterReviewError; the scheduler catches at the boundary (L4).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import suppress
from datetime import date, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from marketpulse.ops.charter_review_aggregator import build_payload
from marketpulse.ops.charter_review_renderer import render_charter_review
from marketpulse.ops.charter_review_types import CharterReviewPayload

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Module-level alias so tests can monkeypatch ONLY this module's reference,
# without affecting global `os.replace` for other callers in the same test.
_os_replace = os.replace


class CharterReviewError(Exception):
    """Surface error from the charter review pipeline. Raised by
    generate_charter_review; the scheduler boundary catches and logs."""


def _read_backup_manifest(path: Path) -> dict | None:
    """Returns parsed manifest dict, or None on missing/unreadable/malformed.
    Never raises — that case becomes manifest_available=False in payload."""
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return None
        return parsed
    except (OSError, json.JSONDecodeError):
        return None


def _atomic_write_text(path: Path, payload: str) -> None:
    """L10: tempfile in same dir → fdopen → write → fsync → os.replace.
    L11: on any failure, tempfile is cleaned; pre-existing target is
    NOT touched (because os.replace is the only operation that mutates
    the target, and it is atomic by POSIX guarantees)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd: int | None = None
    tmp_path: str | None = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
        )
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            tmp_fd = None
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        _os_replace(tmp_path, str(path))
        tmp_path = None
    finally:
        if tmp_fd is not None:
            with suppress(OSError):
                os.close(tmp_fd)
        if tmp_path is not None and os.path.exists(tmp_path):
            with suppress(OSError):
                os.unlink(tmp_path)
