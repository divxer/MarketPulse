# Layer: web
"""PR4 — GET /lab/portfolio-vs-spy north-star visualization.

Thin composition root: read snapshots -> pure presenter -> render. No chart
math here (L1 lives in portfolio_vs_spy_view).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from marketpulse.portfolio.portfolio_vs_spy_view import build_portfolio_vs_spy_view
from marketpulse.portfolio.snapshot_repo import get_all_snapshots
from marketpulse.web.deps import get_db, require_auth
from marketpulse.web.main import templates

router = APIRouter()


@router.get("/lab/portfolio-vs-spy", response_class=HTMLResponse)
def lab_portfolio_vs_spy(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    view = build_portfolio_vs_spy_view(get_all_snapshots(db))
    return templates.TemplateResponse(
        "lab_portfolio_vs_spy.html", {"request": request, "view": view},
    )
