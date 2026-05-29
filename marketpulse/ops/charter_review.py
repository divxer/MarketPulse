# Layer: ops
"""PR3b — orchestration entry for the weekly charter review.

Normalizes the backup manifest via PR2's shared builder, calls aggregator
+ renderer, atomically writes the markdown and the latest.json companion
(L10/L11). May raise CharterReviewError; the scheduler catches at the
boundary (L4).
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

from marketpulse.ops.charter_metrics import build_backup_section
from marketpulse.ops.charter_review_aggregator import build_payload
from marketpulse.ops.charter_review_renderer import render_charter_review

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Module-level alias so tests can monkeypatch ONLY this module's reference,
# without affecting global `os.replace` for other callers in the same test.
_os_replace = os.replace


class CharterReviewError(Exception):
    """Surface error from the charter review pipeline. Raised by
    generate_charter_review; the scheduler boundary catches and logs."""


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


def generate_charter_review(
    *,
    session: Session,
    week_ending: date,
    now: datetime,
    recaps_dir: Path,
    backup_manifest_path: Path,
) -> Path:
    """Build payload → render markdown → atomic-write .md + latest.json.

    L12: validates week_ending is Sunday (weekday == 6) at entry.
    L4: may raise CharterReviewError on DB / render / FS failures.
    L20: on success emits info log charter_review_generated with extra=
         {week_ending, path, generated_at}.
    """
    if week_ending.weekday() != 6:
        raise CharterReviewError(
            f"week_ending must be Sunday (weekday=6); got weekday={week_ending.weekday()}",
        )

    # L14: normalize via PR2's shared builder so the weekly report and the
    # /lab/charter-metrics endpoint agree on backup status + staleness.
    backup_section = build_backup_section(
        manifest_path=backup_manifest_path, now=now,
    )
    try:
        payload = build_payload(
            session=session, week_ending=week_ending,
            backup_section=backup_section, generated_at=now,
        )
    except CharterReviewError:
        raise   # already typed; don't double-wrap
    except Exception as exc:  # noqa: BLE001
        raise CharterReviewError(
            f"aggregator failed: {type(exc).__name__}: {exc}",
        ) from exc

    try:
        markdown = render_charter_review(payload=payload)
    except CharterReviewError:
        raise   # already typed; don't double-wrap
    except Exception as exc:  # noqa: BLE001
        raise CharterReviewError(
            f"renderer failed: {type(exc).__name__}: {exc}",
        ) from exc

    md_path = recaps_dir / f"{week_ending.isoformat()}.md"
    latest_path = recaps_dir / "latest.json"
    manifest_payload = {
        "schema_version": SCHEMA_VERSION,
        "week_ending": week_ending.isoformat(),
        "path": str(md_path),
        "generated_at": now.isoformat(),
    }

    try:
        _atomic_write_text(md_path, markdown)
        _atomic_write_text(
            latest_path,
            json.dumps(manifest_payload, indent=2, sort_keys=True),
        )
    except OSError as exc:
        raise CharterReviewError(
            f"atomic write failed: {type(exc).__name__}: {exc}",
        ) from exc

    log.info(
        "charter_review_generated",
        extra={
            "week_ending": week_ending.isoformat(),
            "path": str(md_path),
            "generated_at": now.isoformat(),
        },
    )
    return md_path
