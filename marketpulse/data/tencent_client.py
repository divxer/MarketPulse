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
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import httpx

from marketpulse.data.types import Bar, Quote
from marketpulse.logging import get_logger

log = get_logger(__name__)

_PARSE_RE = re.compile(r'v_[^=]+="([^"]+)"')
_SUFFIXES = ("", ".OQ", ".N")
_PERIOD_DAYS = {"30d": 30, "60d": 60, "6m": 180, "1y": 365}


@dataclass
class CorporateActions:
    """Result of fetch_corporate_actions: separate lists for dividends and splits.

    Each entry is (ex_date, value): for dividends value is amount-per-share USD;
    for splits value is the ratio (new_shares / old_shares).
    """
    dividends: list[tuple[date, float]] = field(default_factory=list)
    splits: list[tuple[date, float]] = field(default_factory=list)


# Regexes for parsing the Chinese-language corporate-action fields from Tencent.
_DIV_RE = re.compile(r"每股分配([\d.]+)美元")
_FORWARD_SPLIT_RE = re.compile(r"每(\d+)股拆分成(\d+)股")
_REVERSE_SPLIT_RE = re.compile(r"每(\d+)股合并成(\d+)股")


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

    def fetch_corporate_actions(
        self, ticker: str, *, start: date, end: date,
    ) -> CorporateActions:
        """Parse cash dividends and splits from Tencent's Usfqkline endpoint.

        Returns a CorporateActions with separate dividend and split lists.
        Raises ValueError when no suffix variant returned a usable envelope.

        The Usfqkline payload places an optional dict at index 6 of each daily
        row when a corporate action occurred that day:
            {"FHcontent": "每股分配0.25美元", "hgcgContent": "每1股拆分成10股",
             "cqr": "2026-02-10"}
        Either field can be empty; both can be populated on the same date.

        Unparseable strings (formats we don't recognise) are logged via
        log.warning and skipped — they don't fail the whole call.
        """
        upper = ticker.strip().upper()
        if upper.startswith("^"):
            raise ValueError(f"Tencent fqkline does not cover index {ticker!r}")

        start_s = start.strftime("%Y-%m-%d")
        end_s = end.strftime("%Y-%m-%d")
        # Tencent's Usfqkline caps requested rows at ~1200 — values past that
        # return {"code": -1, "msg": "limit error"}. 1200 trading days is
        # ~4.75 years, plenty for our daily-job lookback. Note: the start/end
        # date params appear to be decorative; Tencent always returns the
        # latest N rows regardless of `start`.
        n_rows = 1200

        last_err: Exception | None = None
        # Skip the no-suffix variant — Usfqkline requires a market suffix.
        for suffix in (".OQ", ".N"):
            symbol = f"us{upper}{suffix}"
            url = (
                f"https://web.ifzq.gtimg.cn/appstock/app/Usfqkline/get"
                f"?param={symbol},day,{start_s},{end_s},{n_rows},qfq"
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

            actions = CorporateActions()
            for r in rows:
                if len(r) < 7:
                    continue  # plain OHLCV row, no action
                action_dict = r[6]
                if not isinstance(action_dict, dict):
                    continue
                cqr = action_dict.get("cqr", "")
                try:
                    ex_date = datetime.strptime(cqr, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    log.warning("tencent_corp_action_bad_date",
                                ticker=upper, cqr=cqr)
                    continue

                fh = action_dict.get("FHcontent", "") or ""
                hg = action_dict.get("hgcgContent", "") or ""

                if fh:
                    m = _DIV_RE.search(fh)
                    if m:
                        try:
                            actions.dividends.append((ex_date, float(m.group(1))))
                        except ValueError:
                            log.warning("tencent_corp_action_bad_dividend",
                                        ticker=upper, ex_date=str(ex_date),
                                        content=fh)
                    else:
                        log.warning("tencent_corp_action_unparseable_dividend",
                                    ticker=upper, ex_date=str(ex_date),
                                    content=fh)

                if hg:
                    m_f = _FORWARD_SPLIT_RE.search(hg)
                    m_r = _REVERSE_SPLIT_RE.search(hg)
                    if m_f:
                        a, b = int(m_f.group(1)), int(m_f.group(2))
                        if a > 0:
                            actions.splits.append((ex_date, b / a))
                    elif m_r:
                        a, b = int(m_r.group(1)), int(m_r.group(2))
                        if a > 0:
                            actions.splits.append((ex_date, b / a))
                    else:
                        log.warning("tencent_corp_action_unparseable_split",
                                    ticker=upper, ex_date=str(ex_date),
                                    content=hg)

            return actions  # first suffix that returns a usable envelope wins

        raise ValueError(
            f"no Tencent corporate actions for {ticker!r}"
            + (f" (last error: {last_err})" if last_err else ""),
        )
