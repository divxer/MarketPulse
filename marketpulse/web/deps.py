from collections.abc import Iterator

from fastapi import Cookie, HTTPException, Request, status
from sqlalchemy.orm import Session

from marketpulse.auth.session import SESSION_COOKIE, SessionManager
from marketpulse.config import get_settings
from marketpulse.db.base import session_scope


def get_db() -> Iterator[Session]:
    yield from session_scope()


def get_session_manager() -> SessionManager:
    return SessionManager(secret=get_settings().session_secret)


def require_auth(
    request: Request,
    mp_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> None:
    mgr = get_session_manager()
    if not mp_session or not mgr.verify(mp_session):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="auth required")
