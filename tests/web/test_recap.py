from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from marketpulse.auth.password import hash_password
from marketpulse.db.base import session_scope
from marketpulse.db.models import DailyRecap


def _login(client, monkeypatch):
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})


def _seed_recap(d: date, status: str = "success") -> None:
    gen = session_scope()
    db = next(gen)
    try:
        db.add(DailyRecap(
            recap_date=d, generation_status=status,
            ai_commentary_text="ok",
            generated_at=datetime.now(UTC),
        ))
        db.commit()
    finally:
        db.close()


def test_recap_detail(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)
    _seed_recap(date(2026, 5, 8))
    res = client.get("/recap/2026-05-08")
    assert res.status_code == 200
    assert "2026-05-08" in res.text


def test_recap_list(client: TestClient, monkeypatch) -> None:
    _login(client, monkeypatch)
    _seed_recap(date(2026, 5, 7))
    _seed_recap(date(2026, 5, 8))
    res = client.get("/recaps")
    assert res.status_code == 200
    assert "2026-05-07" in res.text and "2026-05-08" in res.text
