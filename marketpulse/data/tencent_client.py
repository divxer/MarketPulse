"""US stock quote fetcher backed by Tencent's public API (qt.gtimg.cn).

Why: Yahoo Finance aggressively rate-limits anonymous IPs, which is painful
on a personal NAS that hits it every page load. Tencent's public quote
endpoint has no auth, no observed rate limit, and lives on servers fast
to reach from mainland China.

What it can do: live quote (price, change_pct, volume, timestamp).
What it CAN'T do: news, fundamentals, historical bars, indexes (^VIX).
For those, fall back to yfinance. See HybridClient.

URL format:
    https://qt.gtimg.cn/q=usTICKER[.SUFFIX]

Suffix tried in order: "", ".OQ" (Nasdaq), ".N" (NYSE). The endpoint
returns one JS-assignment line per symbol:
    v_usTICKER="status~name~code~price~yclose~open~vol~...~time~chg~chgpct~..."

We parse field positions empirically observed (May 2026):
    [3]  current price
    [6]  volume (shares)
    [32] change percent
"""

import json
import re
from datetime import UTC, date, datetime, timedelta

import httpx

from marketpulse.data.types import Bar, Quote
from marketpulse.logging import get_logger

log = get_logger(__name__)

_PARSE_RE = re.compile(r'v_[^=]+="([^"]+)"')
_SUFFIXES = ("", ".OQ", ".N")
_PERIOD_DAYS = {"30d": 30, "60d": 60, "6m": 180, "1y": 365}


class TencentClient:
    def fetch_quote(self, ticker: str) -> Quote:
        upper = ticker.strip().upper()
        if upper.startswith("^"):
            raise ValueError(f"Tencent quote API does not cover index {ticker!r}")

        last_err: Exception | None = None
        for suffix in _SUFFIXES:
            symbol = f"us{upper}{suffix}"
            try:
                resp = httpx.get(
                    f"https://qt.gtimg.cn/q={symbol}", timeout=10,
                )
                resp.raise_for_status()
            except Exception as exc:
                last_err = exc
                continue

            match = _PARSE_RE.search(resp.text)
            if not match:
                continue
            payload = match.group(1)
            # Empty / unknown symbol → very short payload, skip and try next variant.
            if len(payload) < 20:
                continue
            fields = payload.split("~")
            if len(fields) < 33:
                continue
            try:
                price = float(fields[3])
                volume = int(float(fields[6]))
                change_pct = float(fields[32])
            except (ValueError, IndexError) as exc:
                last_err = exc
                continue
            if price <= 0:
                continue
            return Quote(
                ticker=upper,
                price=price,
                change_pct=change_pct,
                volume=volume,
                avg_volume_20d=0,  # not provided by Tencent
                fetched_at=datetime.now(UTC),
            )

        raise ValueError(
            f"no Tencent quote for {ticker!r} "
            f"(last error: {last_err})" if last_err else f"no Tencent quote for {ticker!r}"
        )

    def fetch_history(self, ticker: str, period: str = "60d") -> list[Bar]:
        """Daily OHLCV bars from Tencent's front-adjusted kline endpoint.

        URL: https://web.ifzq.gtimg.cn/appstock/app/Usfqkline/get?param=usTICKER.<MKT>,day,,,N,qfq
        The symbol MUST include a market suffix (.OQ Nasdaq, .N NYSE) — without
        it Tencent returns only the earliest+latest bar (2 rows total). Response:
        data[<symbol>].day → [[date, open, close, high, low, volume, ...], ...]
        Note (open, close, high, low) order — NOT conventional OHLC. Rows are
        oldest-first.
        """
        upper = ticker.strip().upper()
        if upper.startswith("^"):
            raise ValueError(f"Tencent kline does not cover index {ticker!r}")

        days = _PERIOD_DAYS.get(period, 60)
        # Tencent returns trading days only, but we ask for headroom and trim
        # by calendar date to honor the period boundary.
        n_rows = max(days * 2, 60)
        cutoff = date.today() - timedelta(days=days)

        last_err: Exception | None = None
        # Skip the no-suffix variant — kline endpoint requires a market suffix.
        for suffix in (".OQ", ".N"):
            symbol = f"us{upper}{suffix}"
            url = (
                f"https://web.ifzq.gtimg.cn/appstock/app/Usfqkline/get"
                f"?param={symbol},day,,,{n_rows},qfq"
            )
            try:
                resp = httpx.get(url, timeout=10)
                resp.raise_for_status()
            except Exception as exc:
                last_err = exc
                continue

            try:
                envelope = json.loads(resp.text)
            except json.JSONDecodeError as exc:
                last_err = exc
                continue

            if envelope.get("code") != 0:
                continue
            data = envelope.get("data") or {}
            sym_block = data.get(symbol) or {}
            rows = sym_block.get("qfqday") or sym_block.get("day") or []
            if not rows:
                continue

            bars: list[Bar] = []
            for r in rows:
                if len(r) < 6:
                    continue
                try:
                    d = datetime.strptime(r[0], "%Y-%m-%d").date()
                except ValueError:
                    continue
                if d < cutoff:
                    continue
                try:
                    bars.append(Bar(
                        date=d,
                        open=float(r[1]),
                        close=float(r[2]),
                        high=float(r[3]),
                        low=float(r[4]),
                        volume=int(float(r[5])),
                    ))
                except (ValueError, IndexError) as exc:
                    last_err = exc
                    continue
            if bars:
                return bars

        raise ValueError(
            f"no Tencent kline for {ticker!r}"
            + (f" (last error: {last_err})" if last_err else ""),
        )
