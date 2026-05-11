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
