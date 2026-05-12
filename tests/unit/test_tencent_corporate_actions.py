import json
from datetime import date
from unittest.mock import MagicMock, patch


def _make_envelope(rows_by_symbol: dict[str, list[list]]) -> str:
    """Build a Tencent fqkline-style JSON envelope for one symbol."""
    sym, rows = next(iter(rows_by_symbol.items()))
    return json.dumps({
        "code": 0,
        "msg": "",
        "data": {sym: {"qfqday": rows}},
    })


def test_parse_dividend_row() -> None:
    """A row with FHcontent populated yields a dividend entry."""
    from marketpulse.data.tencent_client import TencentClient

    body = _make_envelope({
        "usAAPL.OQ": [
            ["2026-02-10", "228", "229", "230", "227", "30000000",
             {"FHcontent": "每股分配0.25美元", "hgcgContent": "", "cqr": "2026-02-10"}],
        ],
    })
    fake_resp = MagicMock(text=body)
    fake_resp.raise_for_status.return_value = None

    with patch("marketpulse.data.tencent_client.httpx.get", return_value=fake_resp):
        actions = TencentClient().fetch_corporate_actions(
            "AAPL", start=date(2026, 1, 1), end=date(2026, 3, 1),
        )

    assert actions.dividends == [(date(2026, 2, 10), 0.25)]
    assert actions.splits == []


def test_parse_forward_split_row() -> None:
    """hgcgContent '每1股拆分成10股' → ratio 10.0."""
    from marketpulse.data.tencent_client import TencentClient

    body = _make_envelope({
        "usNVDA.OQ": [
            ["2024-06-10", "120", "121", "123", "117", "300000000",
             {"FHcontent": "", "hgcgContent": "每1股拆分成10股", "cqr": "2024-06-10"}],
        ],
    })
    fake_resp = MagicMock(text=body)
    fake_resp.raise_for_status.return_value = None

    with patch("marketpulse.data.tencent_client.httpx.get", return_value=fake_resp):
        actions = TencentClient().fetch_corporate_actions(
            "NVDA", start=date(2024, 6, 1), end=date(2024, 6, 30),
        )

    assert actions.splits == [(date(2024, 6, 10), 10.0)]
    assert actions.dividends == []


def test_parse_reverse_split_row() -> None:
    """hgcgContent '每5股合并成1股' → ratio 0.2 (1/5)."""
    from marketpulse.data.tencent_client import TencentClient

    body = _make_envelope({
        "usFOO.OQ": [
            ["2025-01-15", "10", "11", "12", "10", "100000",
             {"FHcontent": "", "hgcgContent": "每5股合并成1股", "cqr": "2025-01-15"}],
        ],
    })
    fake_resp = MagicMock(text=body)
    fake_resp.raise_for_status.return_value = None

    with patch("marketpulse.data.tencent_client.httpx.get", return_value=fake_resp):
        actions = TencentClient().fetch_corporate_actions(
            "FOO", start=date(2025, 1, 1), end=date(2025, 1, 30),
        )

    assert actions.splits == [(date(2025, 1, 15), 0.2)]


def test_parse_same_day_split_and_dividend() -> None:
    """A row with both FHcontent and hgcgContent yields TWO entries."""
    from marketpulse.data.tencent_client import TencentClient

    body = _make_envelope({
        "usBOTH.OQ": [
            ["2025-05-01", "100", "101", "102", "99", "1000000",
             {"FHcontent": "每股分配0.50美元", "hgcgContent": "每1股拆分成2股",
              "cqr": "2025-05-01"}],
        ],
    })
    fake_resp = MagicMock(text=body)
    fake_resp.raise_for_status.return_value = None

    with patch("marketpulse.data.tencent_client.httpx.get", return_value=fake_resp):
        actions = TencentClient().fetch_corporate_actions(
            "BOTH", start=date(2025, 1, 1), end=date(2025, 12, 31),
        )

    assert actions.dividends == [(date(2025, 5, 1), 0.50)]
    assert actions.splits == [(date(2025, 5, 1), 2.0)]


def test_unparseable_strings_are_skipped() -> None:
    """Rows with unrecognised FHcontent/hgcgContent format are logged + skipped."""
    from marketpulse.data.tencent_client import TencentClient

    body = _make_envelope({
        "usX.OQ": [
            ["2025-01-01", "1", "1", "1", "1", "0",
             {"FHcontent": "特别分红 unknown", "hgcgContent": "weird",
              "cqr": "2025-01-01"}],
            ["2025-02-01", "1", "1", "1", "1", "0",
             {"FHcontent": "每股分配0.10美元", "hgcgContent": "", "cqr": "2025-02-01"}],
        ],
    })
    fake_resp = MagicMock(text=body)
    fake_resp.raise_for_status.return_value = None

    with patch("marketpulse.data.tencent_client.httpx.get", return_value=fake_resp):
        actions = TencentClient().fetch_corporate_actions(
            "X", start=date(2025, 1, 1), end=date(2025, 12, 31),
        )

    # Unparseable row is skipped; second row parses fine.
    assert actions.dividends == [(date(2025, 2, 1), 0.10)]
    assert actions.splits == []


def test_empty_response_returns_empty_lists() -> None:
    """A bad-route envelope (code != 0) raises ValueError after trying suffixes."""
    import pytest as _pytest
    from marketpulse.data.tencent_client import TencentClient

    fake_resp = MagicMock(text='{"code": 11, "data": "", "msg": "no controller"}')
    fake_resp.raise_for_status.return_value = None

    with patch("marketpulse.data.tencent_client.httpx.get", return_value=fake_resp):
        with _pytest.raises(ValueError, match="no Tencent corporate actions"):
            TencentClient().fetch_corporate_actions(
                "UNKNOWN", start=date(2025, 1, 1), end=date(2025, 12, 31),
            )


def test_response_with_only_ohlcv_rows_returns_empty() -> None:
    """Rows without the dict at index 6 are plain OHLCV — no actions found."""
    from marketpulse.data.tencent_client import TencentClient

    body = _make_envelope({
        "usPLAIN.OQ": [
            ["2025-01-02", "100", "101", "102", "99", "1000000"],
            ["2025-01-03", "101", "102", "103", "100", "900000"],
        ],
    })
    fake_resp = MagicMock(text=body)
    fake_resp.raise_for_status.return_value = None

    with patch("marketpulse.data.tencent_client.httpx.get", return_value=fake_resp):
        actions = TencentClient().fetch_corporate_actions(
            "PLAIN", start=date(2025, 1, 1), end=date(2025, 1, 31),
        )

    assert actions.dividends == []
    assert actions.splits == []
