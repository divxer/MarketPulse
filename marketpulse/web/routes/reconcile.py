"""Phase 7c - /lab/reconcile route."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from marketpulse.reconcile.query_models import load_reconciliation_dashboard
from marketpulse.web.deps import get_db, require_auth
from marketpulse.web.main import templates

router = APIRouter()


@router.get("/lab/reconcile", response_class=HTMLResponse)
def lab_reconcile(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """Render the read-only broker-vs-paper position reconciliation view."""
    dashboard = load_reconciliation_dashboard(db)
    return templates.TemplateResponse(
        request,
        "lab_reconcile.html",
        {"dashboard": dashboard},
    )
