"""Cache-busting helper for static assets.

Computes a short content hash of each static file the first time it's
requested, then caches it for the lifetime of the process. The hash is
appended to the asset URL as `?v=xxxxxxxx` so browsers re-fetch when the
file content changes (e.g., after a deploy that rebuilds app.css).

Production deploy flow:
  1. Dockerfile builds app.css via Tailwind → new content hash
  2. Container restarts → fresh process → empty cache
  3. First request to base.html triggers static_version("app.css")
  4. Hash differs from previous deploy → browsers refetch the CSS

Process-level cache (not request-level): one hash computation per file
per process, then served from memory. The cache is keyed by relative
path from STATIC_DIR; missing files fall back to a fixed "missing"
suffix so the template doesn't crash on typos.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

# Set lazily by the FastAPI app at startup so this module has no import-time
# dependency on the web layer (testable in isolation).
_static_dir: Path | None = None
_hash_cache: dict[str, str] = {}


def configure(static_dir: Path) -> None:
    """Called once at FastAPI app construction with the resolved STATIC_DIR."""
    global _static_dir
    _static_dir = static_dir
    _hash_cache.clear()


def static_version(relative_path: str) -> str:
    """Return the first 8 hex chars of the file's md5 content hash.

    Used inside Jinja templates like:
        <link rel="stylesheet" href="/static/app.css?v={{ static_version('app.css') }}">

    Returns "missing" if the file doesn't exist (template doesn't crash,
    but the cache-bust value becomes a constant — that's fine, the file
    isn't there to bust).
    """
    if relative_path in _hash_cache:
        return _hash_cache[relative_path]

    if _static_dir is None:
        # Module wasn't configured (e.g., test that imports the helper but
        # doesn't start the app). Skip hashing.
        return "unconfigured"

    file_path = _static_dir / relative_path
    if not file_path.is_file():
        _hash_cache[relative_path] = "missing"
        return "missing"

    h = hashlib.md5()  # noqa: S324 — non-cryptographic, just for cache busting
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    short = h.hexdigest()[:8]
    _hash_cache[relative_path] = short
    return short
