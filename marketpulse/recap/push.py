"""Build a short Markdown summary of a DailyRecap and push it via a notifier.

The format is designed to be readable on Bark (4096-char limit), Server酱
(32 KB), and SMTP (unlimited). Empty sections are silently skipped so missing
data never produces a broken-looking message.
"""

import json

from tenacity import retry, stop_after_attempt, wait_fixed

from marketpulse.alerts.notifier import Notifier
from marketpulse.db.models import DailyRecap
from marketpulse.logging import get_logger

log = get_logger(__name__)

# Per-channel body limits. SMTP is unlimited so we use a sentinel.
_BODY_LIMITS: dict[str, int | None] = {
    "bark": 3500,        # Bark accepts 4096; leave headroom for footer
    "serverchan": 30000, # 32 KB nominal
    "smtp": None,        # no limit
}

_SIGNAL_LABELS = {
    "EMA_GOLDEN_CROSS": "EMA 金叉",
    "EMA_DEATH_CROSS": "EMA 死叉",
    "RSI_OVERBOUGHT": "RSI 超买",
    "RSI_OVERSOLD": "RSI 超卖",
    "BOLLINGER_UPPER": "突破布林上轨",
    "BOLLINGER_LOWER": "跌破布林下轨",
    "BIG_MOVE": "大幅波动",
    "VOLUME_SPIKE": "成交量异常",
    "MA20_BREAKOUT": "突破 MA20",
}


def _truncate(text: str, limit: int | None, suffix: str = "…") -> str:
    if limit is None or len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


def _market_section(market_json: str | None) -> str | None:
    if not market_json:
        return None
    d = json.loads(market_json)
    parts = []
    if "spy" in d: parts.append(f"SPY {d['spy']:+.2f}%")
    if "qqq" in d: parts.append(f"QQQ {d['qqq']:+.2f}%")
    if "dia" in d: parts.append(f"DIA {d['dia']:+.2f}%")
    if "vix" in d: parts.append(f"VIX {d['vix']:.1f}")
    return "📈 大盘\n" + "  ".join(parts) if parts else None


def _holdings_section(overview_json: str | None, totals_json: str | None) -> str | None:
    if not overview_json:
        return None
    rows = json.loads(overview_json)
    if not rows:
        return None
    head = "💼 持仓"
    if totals_json:
        t = json.loads(totals_json)
        head += f" ({t.get('pl_dollars', 0):+.0f} / {t.get('pl_pct', 0):+.2f}%)"
    body_parts = []
    for r in rows:
        ticker = r.get("ticker", "?")
        pl_pct = r.get("pl_pct")
        if pl_pct is None:
            continue
        body_parts.append(f"{ticker} {pl_pct:+.0f}%")
    if not body_parts:
        return head
    return head + "\n" + "  ".join(body_parts)


def _signals_section(perf_json: str | None) -> str | None:
    if not perf_json:
        return None
    rows = json.loads(perf_json)
    fired = []
    for r in rows:
        sigs = r.get("signals") or []
        if sigs:
            labels = ", ".join(_SIGNAL_LABELS.get(s, s) for s in sigs)
            fired.append(f"{r.get('ticker', '?')}: {labels}")
    if not fired:
        return None
    return "⚠️ 异动信号\n" + "\n".join(fired)


def _ai_section(text: str | None, max_chars: int = 200) -> str | None:
    if not text or not text.strip():
        return None
    body = text.strip()
    if len(body) > max_chars:
        body = body[: max_chars] + "…"
    return f"🤖 AI 总评\n{body}"


def build_summary(
    recap: DailyRecap,
    base_url: str | None = None,
    notifier_kind: str | None = None,
) -> tuple[str, str]:
    """Produce (title, body) for the given recap.

    `notifier_kind` is used only to size the body to the channel limit. Unknown
    or None means apply no limit (SMTP-style).
    """
    title = f"MarketPulse 复盘 · {recap.recap_date.isoformat()}"

    sections: list[str] = []
    for section in (
        _market_section(recap.market_summary_json),
        _holdings_section(recap.holdings_overview_json, recap.holdings_totals_json),
        _signals_section(recap.watchlist_performance_json),
        _ai_section(recap.ai_commentary_text),
    ):
        if section:
            sections.append(section)

    body = "\n\n".join(sections)
    if base_url:
        link = f"{base_url.rstrip('/')}/recap/{recap.recap_date.isoformat()}"
        body += f"\n\n───\n详情: {link}"

    limit = _BODY_LIMITS.get((notifier_kind or "").lower())
    body = _truncate(body, limit)
    return title, body


@retry(stop=stop_after_attempt(2), wait=wait_fixed(2), reraise=False)
def _send_with_retry(notifier: Notifier, title: str, body: str, url: str | None) -> bool:
    return notifier.send(title, body, url=url)


def push_recap_summary(
    recap: DailyRecap,
    notifier: Notifier,
    base_url: str | None = None,
    notifier_kind: str | None = None,
) -> bool:
    """Build + send the summary. Returns True on success.

    Wraps the send in tenacity (one retry after a 2s wait). Any exception that
    escapes the retry is caught and logged — recap-push failure is non-fatal.
    """
    title, body = build_summary(recap, base_url=base_url, notifier_kind=notifier_kind)
    url = (
        f"{base_url.rstrip('/')}/recap/{recap.recap_date.isoformat()}"
        if base_url else None
    )
    try:
        return bool(_send_with_retry(notifier, title, body, url))
    except Exception as exc:
        log.warning("recap_push_failed_after_retry", error=str(exc))
        return False
