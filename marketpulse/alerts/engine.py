"""Evaluate alert rules against live market data and fire notifications.

Designed to be called periodically (APScheduler job during market hours).
Each rule fires at most once per debounce window (default 60 min) to avoid
spamming the notification channel on volatile minute-by-minute readings.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy.orm import Session

from marketpulse.alerts.notifier import Notifier
from marketpulse.data.types import Quote
from marketpulse.db.models import AlertRule
from marketpulse.logging import get_logger

log = get_logger(__name__)

VALID_METRICS = ("price", "change_pct", "volume_ratio")
VALID_OPS = (">=", "<=")

# Friendly labels for notification text.
_METRIC_LABELS = {
    "price": "价格",
    "change_pct": "涨跌幅",
    "volume_ratio": "量比",
}


class _DataLike(Protocol):
    def get_quote(self, ticker: str) -> Quote: ...


def _extract_metric(quote: Quote, metric: str) -> float | None:
    if metric == "price":
        return quote.price
    if metric == "change_pct":
        return quote.change_pct
    if metric == "volume_ratio":
        if not quote.avg_volume_20d:
            return None
        return quote.volume / quote.avg_volume_20d
    return None


def _matches(value: float, op: str, threshold: float) -> bool:
    if op == ">=":
        return value >= threshold
    if op == "<=":
        return value <= threshold
    return False


def _debounced(rule: AlertRule, debounce: timedelta, now: datetime) -> bool:
    """True if this rule fired recently and should be skipped."""
    if rule.last_triggered_at is None:
        return False
    elapsed = now - rule.last_triggered_at
    return elapsed < debounce


def _format_value(metric: str, value: float) -> str:
    if metric == "price":
        return f"${value:.2f}"
    if metric == "change_pct":
        return f"{value:+.2f}%"
    return f"{value:.2f}x"


def evaluate_rules(
    session: Session,
    *,
    data: _DataLike,
    notifier: Notifier,
    debounce_minutes: int = 60,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Evaluate all enabled rules. Returns a list of {rule_id, fired, reason}
    diagnostic entries. Commits within."""
    now = now or datetime.now(UTC)
    debounce = timedelta(minutes=debounce_minutes)
    rules = session.query(AlertRule).filter(AlertRule.enabled).all()
    results: list[dict[str, Any]] = []

    for r in rules:
        entry: dict[str, Any] = {"rule_id": r.id, "ticker": r.ticker, "fired": False}
        if _debounced(r, debounce, now):
            entry["reason"] = "debounced"
            results.append(entry)
            continue
        try:
            quote = data.get_quote(r.ticker)
        except Exception as exc:
            log.warning("alert_quote_failed", ticker=r.ticker, error=str(exc))
            entry["reason"] = f"quote-failed: {exc}"
            results.append(entry)
            continue
        value = _extract_metric(quote, r.metric)
        if value is None:
            entry["reason"] = "metric-unavailable"
            results.append(entry)
            continue
        entry["value"] = value
        if not _matches(value, r.op, r.threshold):
            entry["reason"] = "not-matched"
            results.append(entry)
            continue

        # Fire
        label = _METRIC_LABELS.get(r.metric, r.metric)
        title = f"{r.ticker} {label} 触发告警"
        body = (
            f"{r.ticker} {label} {_format_value(r.metric, value)} "
            f"{r.op} {_format_value(r.metric, r.threshold)}"
        )
        if r.notes:
            body += f"\n备注:{r.notes}"
        sent = notifier.send(title, body, url=None)
        r.last_triggered_at = now
        r.last_value = value
        entry["fired"] = True
        entry["sent"] = sent
        entry["reason"] = "matched"
        log.info(
            "alert_fired",
            rule_id=r.id, ticker=r.ticker, metric=r.metric,
            value=value, threshold=r.threshold, sent=sent,
        )
        results.append(entry)

    session.commit()
    return results
