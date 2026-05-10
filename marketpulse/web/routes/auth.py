from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from marketpulse.auth.password import verify_password
from marketpulse.auth.session import SESSION_COOKIE
from marketpulse.config import get_settings
from marketpulse.web.deps import get_session_manager
from marketpulse.web.main import templates

router = APIRouter()


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login")
def login(password: str = Form(...)):
    settings = get_settings()
    if not verify_password(password, settings.app_password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad password")
    token = get_session_manager().issue()
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response
