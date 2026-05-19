import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

# Ensure required env vars exist before importing settings.
os.environ.setdefault("APP_PASSWORD_HASH", "$2b$12$abcdefghijklmnopqrstuv")
os.environ.setdefault("SESSION_SECRET", "x" * 32)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def _clear_quote_cache() -> None:
    """The QUOTE_CACHE module-level singleton must not leak across tests."""
    from marketpulse.data.quote_cache import QUOTE_CACHE
    QUOTE_CACHE.clear()
    yield
    QUOTE_CACHE.clear()


@pytest.fixture(scope="session", autouse=True)
def _ensure_tailwind_output_exists():
    """Ensure marketpulse/web/static/app.css exists before tests run.

    Tailwind output is .gitignore'd; on a fresh checkout or in CI
    without node, the file is missing. Tests that assert on
    static_version('app.css') need *some* file there. We create a
    minimal stub if the real build hasn't produced one; a real
    Tailwind run (npm run build:css) will overwrite it.
    """
    css_path = (
        Path(__file__).resolve().parent.parent
        / "marketpulse" / "web" / "static" / "app.css"
    )
    if not css_path.exists():
        css_path.write_text(
            "/* tailwind build stub — run `npm run build:css` for the real one */\n"
        )
    yield


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture()
def db_session(db_url: str) -> Session:
    from marketpulse.db import base as db_base
    from marketpulse.db.base import Base

    db_base.init_engine(db_url)
    Base.metadata.create_all(db_base.get_engine())
    gen = db_base.session_scope()
    session = next(gen)
    try:
        yield session
    finally:
        session.close()
        db_base.reset_engine()


@pytest.fixture()
def client(db_url: str):
    from fastapi.testclient import TestClient

    from marketpulse.db import base as db_base
    from marketpulse.db.base import Base
    from marketpulse.web.main import create_app

    db_base.init_engine(db_url)
    Base.metadata.create_all(db_base.get_engine())
    app = create_app()
    with TestClient(app) as c:
        yield c
