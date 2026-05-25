"""Phase 7a+ Broker Truth Viewer — single read-only dashboard route.

LOCK L1: this route does NOT mutate state. No sync trigger, no actions.
LOCK L2: it reads via load_broker_dashboard which only touches the four
Phase 7a snapshot tables. NO broker_order_intent / broker_order_event /
paper_* access (enforced by tests/architecture/test_lab_broker_isolation.py).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from marketpulse.broker.query_models import load_broker_dashboard
from marketpulse.web.deps import get_db, require_auth
from marketpulse.web.main import templates

router = APIRouter()


@router.get("/lab/broker", response_class=HTMLResponse)
def lab_broker(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    """Phase 7a+ Broker Truth Viewer.

    Read-only display of the most recent IBKR Flex snapshot. No filters,
    no actions. If the latest sync attempt failed but a prior completed
    run exists, the page falls back to the last completed snapshot and
    annotates the staleness explicitly.
    """
    dashboard = load_broker_dashboard(db)
    return templates.TemplateResponse(
        request,
        "lab_broker.html",
        {"dashboard": dashboard},
    )
