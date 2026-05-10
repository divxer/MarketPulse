import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

# Ensure required env vars exist before importing settings.
os.environ.setdefault("APP_PASSWORD_HASH", "$2b$12$abcdefghijklmnopqrstuv")
os.environ.setdefault("SESSION_SECRET", "x" * 32)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


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
