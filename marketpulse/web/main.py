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

    from marketpulse.web.routes import (  # noqa: WPS433
        auth,
        health,
        holdings,
        home,
        recap,
        stock,
        trades,
        watchlist,
    )
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(home.router)
    app.include_router(watchlist.router)
    app.include_router(holdings.router)
    app.include_router(trades.router)
    app.include_router(stock.router)
    app.include_router(recap.router)

    from fastapi import HTTPException

    @app.exception_handler(HTTPException)
    async def _redirect_unauth(request, exc):  # noqa: ANN001
        from fastapi.responses import JSONResponse, RedirectResponse
        if exc.status_code == 401:
            if request.url.path.startswith("/login") or request.url.path.startswith("/health"):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
            accept = request.headers.get("accept", "")
            if "application/json" in accept and "text/html" not in accept:
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
            return RedirectResponse(url="/login", status_code=303)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    from marketpulse.scheduler.jobs import build_scheduler
    scheduler = build_scheduler()

    @app.on_event("startup")
    def _start_scheduler() -> None:
        if not scheduler.running:
            scheduler.start()

    @app.on_event("shutdown")
    def _stop_scheduler() -> None:
        if scheduler.running:
            scheduler.shutdown(wait=False)

    return app


app = create_app()
