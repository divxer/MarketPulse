from contextlib import asynccontextmanager
from pathlib import Path

import markdown as _markdown
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from marketpulse.config import get_settings
from marketpulse.logging import configure_logging
from marketpulse.web.static_versioning import configure as _configure_static_versioning
from marketpulse.web.static_versioning import static_version

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

_configure_static_versioning(STATIC_DIR)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["static_version"] = static_version


def _render_markdown(text: str | None) -> Markup:
    """Convert AI markdown output to safe HTML. Used by templates as |markdown."""
    if not text:
        return Markup("")
    # `nl2br` keeps single newlines as <br>; without it the AI's intra-paragraph
    # newlines would visually merge text together.
    html = _markdown.markdown(text, extensions=["nl2br", "tables"])
    return Markup(html)


templates.env.filters["markdown"] = _render_markdown


def _sparkpoints(values: list[float] | None, width: int, height: int) -> str:
    """Convert a values list to SVG polyline points attribute.

    Linearly normalizes values to fit [0, width] × [0, height], inverting Y
    (higher value → smaller y, closer to top). Returns empty string for
    inputs < 2 points (no line to draw).

    Used by stock.html watchlist sparkline rendering.
    """
    if not values or len(values) < 2:
        return ""
    lo = min(values)
    hi = max(values)
    n = len(values)
    if hi == lo:
        # Flat line — horizontal at midline so it's visible
        mid = height / 2
        return " ".join(
            f"{i * width / (n - 1):.1f},{mid:.1f}" for i in range(n)
        )
    span = hi - lo
    pts = []
    for i, v in enumerate(values):
        x = i * width / (n - 1)
        y = height - (v - lo) / span * height
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


templates.env.filters["sparkpoints"] = _sparkpoints


def _relative_zh(ts) -> str:
    """Render a datetime as a Chinese relative-time phrase ("3 分钟前").

    Buckets: <1min "刚刚生成"; <60min "N 分钟前"; <24h "N 小时前";
    older "N 天前". `None` → "刚刚生成" (preserves pre-cache-timestamp UX).
    """
    if ts is None:
        return "刚刚生成"
    from datetime import UTC, datetime
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - ts
    secs = int(delta.total_seconds())
    if secs < 60:
        return "刚刚生成"
    if secs < 3600:
        return f"{secs // 60} 分钟前"
    if secs < 86400:
        return f"{secs // 3600} 小时前"
    return f"{secs // 86400} 天前"


templates.env.filters["relative_zh"] = _relative_zh


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    # Honor user-configured TTL for the in-memory quote cache.
    from marketpulse.data.quote_cache import QUOTE_CACHE
    QUOTE_CACHE.configure(settings.quote_cache_ttl_seconds)

    from marketpulse.scheduler.jobs import build_scheduler
    scheduler = build_scheduler()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ARG001
        # Phase 6a paper-trading initialization (idempotent — Repository
        # guards against double-seeding via PaperCashLedger row count).
        from decimal import Decimal

        from marketpulse.db.base import session_scope
        from marketpulse.trading.clock import WallClock
        from marketpulse.trading.repository import Repository

        gen = session_scope()
        db = next(gen)
        try:
            Repository(session=db).ensure_initial_deposit(
                amount=Decimal(settings.paper_initial_deposit),
                timestamp=WallClock().now(),
            )
        finally:
            db.close()

        if not scheduler.running:
            scheduler.start()
        try:
            yield
        finally:
            if scheduler.running:
                scheduler.shutdown(wait=False)

    app = FastAPI(title="MarketPulse", lifespan=lifespan)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    from marketpulse.web.routes import (  # noqa: WPS433
        alerts,
        auth,
        backtest,
        broker,
        charter,
        health,
        holdings,
        home,
        lab,
        recap,
        reconcile,
        splits,
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
    app.include_router(splits.router)
    app.include_router(alerts.router)
    app.include_router(stock.router)
    app.include_router(recap.router)
    app.include_router(lab.router)
    app.include_router(backtest.router)
    app.include_router(broker.router)
    app.include_router(reconcile.router)
    app.include_router(charter.router)

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

    return app


app = create_app()
