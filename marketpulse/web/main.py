from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from marketpulse.config import get_settings
from marketpulse.logging import configure_logging

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="MarketPulse")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    from marketpulse.web.routes import auth, health  # noqa: WPS433
    app.include_router(health.router)
    app.include_router(auth.router)

    @app.exception_handler(Exception)
    async def _redirect_unauth(request, exc):  # noqa: ANN001
        from fastapi import HTTPException
        from fastapi.responses import JSONResponse, RedirectResponse
        if isinstance(exc, HTTPException) and exc.status_code == 401:
            if request.url.path.startswith("/login") or request.url.path.startswith("/health"):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
            accept = request.headers.get("accept", "")
            if "text/html" in accept or accept == "":
                return RedirectResponse(url="/login", status_code=303)
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        raise exc

    return app


app = create_app()
