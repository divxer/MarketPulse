"""Home page — daily recap card + watchlist table with live quotes."""
from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from marketpulse.data.service import DataService
from marketpulse.db.models import DailyRecap, WatchlistItem
from marketpulse.logging import get_logger
from marketpulse.web.deps import get_data_service, get_db, require_auth
from marketpulse.web.main import templates

log = get_logger(__name__)
router = APIRouter()


@router.get("/")
def home(
    request: Request,
    db: Session = Depends(get_db),
    data: DataService = Depends(get_data_service),
    _: None = Depends(require_auth),
):
    today = date.today()
    recap = db.query(DailyRecap).filter(DailyRecap.recap_date == today).one_or_none()

    # Watchlist table: enrich each row with a live quote so price / change /
    # volume render instead of em-dash placeholders. Each ticker is fetched
    # independently; one failure doesn't take the whole table down.
    rows = db.query(WatchlistItem).order_by(WatchlistItem.sort_order).all()
    watchlist: list[dict] = []
    for item in rows:
        try:
            q = data.get_quote(item.ticker)
            watchlist.append({
                "ticker": item.ticker,
                "price": q.price,
                "change_pct": q.change_pct,
                "volume": q.volume,
                "stale": getattr(q, "stale", False),
            })
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "home_watchlist_quote_failed",
                ticker=item.ticker, error=str(exc),
            )
            watchlist.append({
                "ticker": item.ticker,
                "price": None,
                "change_pct": None,
                "volume": None,
                "stale": False,
            })

    return templates.TemplateResponse(
        request, "home.html",
        {"recap": recap, "watchlist": watchlist, "today": today},
    )
