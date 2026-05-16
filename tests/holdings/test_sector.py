"""yfinance sector lookup + bounded backfill."""
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from marketpulse.db.models import Holding


def _holding(session, ticker, *, sector=None) -> Holding:
    h = Holding(ticker=ticker, quantity=1.0, avg_cost=1.0, sort_order=0,
                sector=sector)
    session.add(h)
    session.commit()
    return h


def test_get_sector_returns_yfinance_sector():
    from marketpulse.holdings.sector import _cache, get_sector
    _cache.clear()
    fake_ticker = MagicMock()
    fake_ticker.info = {"sector": "Technology"}
    with patch("yfinance.Ticker", return_value=fake_ticker):
        assert get_sector("AAPL") == "Technology"


def test_get_sector_returns_none_on_failure():
    from marketpulse.holdings.sector import _cache, get_sector
    _cache.clear()
    with patch("yfinance.Ticker", side_effect=RuntimeError("network")):
        assert get_sector("AAPL") is None


def test_get_sector_returns_none_when_field_missing():
    from marketpulse.holdings.sector import _cache, get_sector
    _cache.clear()
    fake_ticker = MagicMock()
    fake_ticker.info = {}  # no sector key
    with patch("yfinance.Ticker", return_value=fake_ticker):
        assert get_sector("AAPL") is None


def test_get_sector_caches_within_ttl():
    """Two calls within TTL → only one yfinance fetch."""
    from marketpulse.holdings.sector import _cache, get_sector
    _cache.clear()
    fake_ticker = MagicMock()
    fake_ticker.info = {"sector": "Technology"}
    with patch("yfinance.Ticker", return_value=fake_ticker) as m:
        get_sector("AAPL")
        get_sector("AAPL")
        assert m.call_count == 1


def test_get_sector_cache_expires_after_ttl():
    """After TTL elapses, next call re-fetches."""
    from marketpulse.holdings import sector as sector_mod
    sector_mod._cache.clear()
    # Insert stale cache entry (25h ago).
    sector_mod._cache["AAPL"] = ("Technology", datetime.now(UTC) - timedelta(hours=25))
    fake_ticker = MagicMock()
    fake_ticker.info = {"sector": "Tech-Refreshed"}
    with patch("yfinance.Ticker", return_value=fake_ticker):
        assert sector_mod.get_sector("AAPL") == "Tech-Refreshed"


def test_backfill_only_fills_null(db_session):
    from marketpulse.holdings.sector import backfill_holding_sectors
    _holding(db_session, "AAPL", sector=None)
    _holding(db_session, "NVDA", sector="Existing")
    with patch("marketpulse.holdings.sector.get_sector", return_value="Technology"):
        n = backfill_holding_sectors(db_session)
    assert n == 1
    db_session.expire_all()
    aapl = db_session.query(Holding).filter_by(ticker="AAPL").one()
    nvda = db_session.query(Holding).filter_by(ticker="NVDA").one()
    assert aapl.sector == "Technology"
    assert nvda.sector == "Existing"


def test_backfill_bounded_by_max_per_call(db_session):
    """5 NULL holdings, max_per_call=3 → only 3 filled in one call."""
    from marketpulse.holdings.sector import backfill_holding_sectors
    for t in ("A", "B", "C", "D", "E"):
        _holding(db_session, t, sector=None)
    with patch("marketpulse.holdings.sector.get_sector", return_value="Tech"):
        n = backfill_holding_sectors(db_session, max_per_call=3)
    assert n == 3


def test_backfill_idempotent(db_session):
    """Calling twice after all rows filled returns 0 the second time."""
    from marketpulse.holdings.sector import backfill_holding_sectors
    _holding(db_session, "AAPL", sector=None)
    with patch("marketpulse.holdings.sector.get_sector", return_value="Tech"):
        n1 = backfill_holding_sectors(db_session)
        n2 = backfill_holding_sectors(db_session)
    assert n1 == 1
    assert n2 == 0
