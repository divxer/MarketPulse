import httpx
import pytest
import respx

from marketpulse.data.tencent_client import TencentClient

# Real-shape sample copied from a live response to TQQQ on 2026-05-08.
_TQQQ_PAYLOAD = (
    'v_usTQQQ.OQ="200~ProShares~TQQQ.OQ~76.28~71.34~72.82~68713740~0~0~'
    '76.55~1700~0~0~0~0~0~0~0~0~76.57~200~0~0~0~0~0~0~0~0~~'
    '2026-05-08 16:00:02~4.94~6.92~76.31~72.70~USD~68713740~5155665553~~~~~~5.07";'
)
_EMPTY_PAYLOAD = 'v_usFAKE="";'


@respx.mock
def test_fetch_quote_parses_response() -> None:
    respx.get("https://qt.gtimg.cn/q=usTQQQ").mock(
        return_value=httpx.Response(200, text=_EMPTY_PAYLOAD),
    )
    respx.get("https://qt.gtimg.cn/q=usTQQQ.OQ").mock(
        return_value=httpx.Response(200, text=_TQQQ_PAYLOAD),
    )
    q = TencentClient().fetch_quote("TQQQ")
    assert q.ticker == "TQQQ"
    assert q.price == 76.28
    assert q.change_pct == 6.92
    assert q.volume == 68713740
    assert q.avg_volume_20d == 0  # not provided by Tencent


@respx.mock
def test_unknown_ticker_raises() -> None:
    for suffix in ("", ".OQ", ".N"):
        respx.get(f"https://qt.gtimg.cn/q=usFAKE{suffix}").mock(
            return_value=httpx.Response(200, text='v_usFAKE="";'),
        )
    with pytest.raises(ValueError, match="no Tencent quote"):
        TencentClient().fetch_quote("FAKE")


def test_index_rejected() -> None:
    with pytest.raises(ValueError, match="index"):
        TencentClient().fetch_quote("^VIX")


@respx.mock
def test_http_error_tries_next_suffix() -> None:
    respx.get("https://qt.gtimg.cn/q=usNVDA").mock(
        return_value=httpx.Response(500),
    )
    respx.get("https://qt.gtimg.cn/q=usNVDA.OQ").mock(
        return_value=httpx.Response(200, text=_TQQQ_PAYLOAD.replace("TQQQ", "NVDA")),
    )
    q = TencentClient().fetch_quote("NVDA")
    assert q.ticker == "NVDA"
    assert q.price == 76.28


# Kline (历史K线) tests. Response shape mirrors a live capture for AAPL.
# Row format: [date, open, close, high, low, volume, ...]  (close/high/low order)
import json as _json  # noqa: E402

import pytest as _pytest  # noqa: E402

from datetime import date as _date  # noqa: E402, I001


def _kline_envelope(symbol: str, rows: list) -> str:
    return _json.dumps({"code": 0, "data": {symbol: {"qfqday": rows}}})


@respx.mock
def test_fetch_history_parses_kline() -> None:
    today = _date.today().isoformat()
    yday = (_date.today() - __import__("datetime").timedelta(days=1)).isoformat()
    rows = [
        [yday, "100.00", "101.50", "102.00", "99.50", "1000000"],
        [today, "101.00", "103.00", "103.50", "100.50", "2000000"],
    ]
    # .OQ is the first suffix tried — return data.
    respx.get(
        "https://web.ifzq.gtimg.cn/appstock/app/Usfqkline/get",
        params={"param": "usAAPL.OQ,day,,,120,qfq"},
    ).mock(return_value=httpx.Response(
        200, text=_kline_envelope("usAAPL.OQ", rows),
    ))

    bars = TencentClient().fetch_history("AAPL", period="60d")
    assert len(bars) == 2
    assert bars[0].date.isoformat() == yday
    assert bars[0].open == 100.0
    assert bars[0].close == 101.50
    assert bars[0].high == 102.00
    assert bars[0].low == 99.50
    assert bars[0].volume == 1_000_000


@respx.mock
def test_fetch_history_filters_by_period() -> None:
    from datetime import timedelta as _td
    today = _date.today()
    rows = [
        # 100 days ago — outside 30d window
        [(today - _td(days=100)).isoformat(), "1", "2", "3", "0.5", "1"],
        # 5 days ago — inside
        [(today - _td(days=5)).isoformat(), "10", "11", "12", "9", "100"],
    ]
    respx.get("https://web.ifzq.gtimg.cn/appstock/app/Usfqkline/get").mock(
        return_value=httpx.Response(200, text=_kline_envelope("usAAPL.OQ", rows)),
    )
    bars = TencentClient().fetch_history("AAPL", period="30d")
    assert len(bars) == 1
    assert bars[0].close == 11.0


def test_fetch_history_rejects_index() -> None:
    with _pytest.raises(ValueError, match="index"):
        TencentClient().fetch_history("^VIX")


@respx.mock
def test_fetch_history_raises_when_all_empty() -> None:
    respx.get("https://web.ifzq.gtimg.cn/appstock/app/Usfqkline/get").mock(
        return_value=httpx.Response(200, text=_json.dumps({"code": 0, "data": {}})),
    )
    with _pytest.raises(ValueError, match="no Tencent kline"):
        TencentClient().fetch_history("ZZZZ")


@respx.mock
def test_fetch_history_accepts_1y_period() -> None:
    today = _date.today()
    from datetime import timedelta as _td
    rows = [
        [(today - _td(days=300)).isoformat(),
         "100.00", "101.00", "102.00", "99.00", "1000"],
        [today.isoformat(), "110.00", "111.00", "112.00", "109.00", "2000"],
    ]
    respx.get("https://web.ifzq.gtimg.cn/appstock/app/Usfqkline/get").mock(
        return_value=httpx.Response(200, text=_kline_envelope("usAAPL.OQ", rows)),
    )
    bars = TencentClient().fetch_history("AAPL", period="1y")
    # 300 days ago is inside a 1y (365 day) window → both rows kept
    assert len(bars) == 2


@respx.mock
def test_fetch_history_accepts_6m_period() -> None:
    today = _date.today()
    from datetime import timedelta as _td
    rows = [
        [(today - _td(days=200)).isoformat(),
         "100.00", "101.00", "102.00", "99.00", "1000"],
        [today.isoformat(), "110.00", "111.00", "112.00", "109.00", "2000"],
    ]
    respx.get("https://web.ifzq.gtimg.cn/appstock/app/Usfqkline/get").mock(
        return_value=httpx.Response(200, text=_kline_envelope("usAAPL.OQ", rows)),
    )
    bars = TencentClient().fetch_history("AAPL", period="6m")
    # 200 days ago is OUTSIDE 6m (180 day) window → only today kept
    assert len(bars) == 1
