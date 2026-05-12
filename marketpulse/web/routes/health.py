from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from marketpulse.db.base import get_engine
from marketpulse.scheduler.state import get_last_run_summary
from marketpulse.web.deps import get_db, require_auth

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    engine = get_engine()
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    return {"status": "ok"}


@router.get("/health/scheduler")
def scheduler_health(
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
) -> JSONResponse:
    """Return the most recent detect_corporate_actions run summary.

    Useful for diagnosing why a ticker didn't get its expected splits/dividends:
    per-ticker `source` shows which fetcher succeeded (tencent / yfinance / none),
    `splits_added` / `dividends_added` show what was newly persisted this run.
    """
    summary = get_last_run_summary(db)
    if summary is None:
        return JSONResponse({"status": "never_ran", "last_run": None})
    return JSONResponse(summary)
