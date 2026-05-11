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

import re
from datetime import UTC, datetime

import httpx

from marketpulse.data.types import Quote
from marketpulse.logging import get_logger

log = get_logger(__name__)

_PARSE_RE = re.compile(r'v_[^=]+="([^"]+)"')
_SUFFIXES = ("", ".OQ", ".N")


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
