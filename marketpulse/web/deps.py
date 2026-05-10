from collections.abc import Iterator

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from marketpulse.ai.client import AnthropicClient
from marketpulse.ai.service import AiService
from marketpulse.auth.session import SESSION_COOKIE, SessionManager
from marketpulse.config import get_settings
from marketpulse.data.service import DataService
from marketpulse.data.yfinance_client import YFinanceClient
from marketpulse.db.base import session_scope
from marketpulse.recap.service import RecapService


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


def get_data_service(db: Session = Depends(get_db)) -> DataService:
    s = get_settings()
    return DataService(db, YFinanceClient(), news_ttl_days=s.news_cache_ttl_days)


def get_ai_service(
    db: Session = Depends(get_db),
    data: DataService = Depends(get_data_service),
) -> AiService:
    s = get_settings()
    return AiService(
        db, ai_client=AnthropicClient(), data=data,
        model=s.ai_model, ttl_hours=s.ai_cache_ttl_hours,
    )


def get_recap_service(
    db: Session = Depends(get_db),
    data: DataService = Depends(get_data_service),
    ai: AiService = Depends(get_ai_service),
) -> RecapService:
    return RecapService(db, data=data, ai=ai)
