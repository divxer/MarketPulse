from fastapi import APIRouter

from marketpulse.db.base import get_engine

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    engine = get_engine()
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    return {"status": "ok"}
