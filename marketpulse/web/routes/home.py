from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from marketpulse.db.models import DailyRecap, WatchlistItem
from marketpulse.web.deps import get_db, require_auth
from marketpulse.web.main import templates

router = APIRouter()


@router.get("/")
def home(request: Request, db: Session = Depends(get_db), _: None = Depends(require_auth)):
    today = date.today()
    recap = db.query(DailyRecap).filter(DailyRecap.recap_date == today).one_or_none()
    items = db.query(WatchlistItem).order_by(WatchlistItem.sort_order).all()
    return templates.TemplateResponse(
        request, "home.html",
        {"recap": recap, "watchlist": items, "today": today},
    )
