import json
from datetime import UTC, date, datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session

from marketpulse.data.types import Bar, MarketOverview, NewsItem, Quote
from marketpulse.db.models import DailyRecap, Holding, WatchlistItem
from marketpulse.holdings.service import compute_totals, enrich_holdings
from marketpulse.logging import get_logger
from marketpulse.recap.signals import detect_signals

log = get_logger(__name__)


def _parse_ai_output(raw: str) -> tuple[str, str | None]:
    """Split AI output into (commentary_markdown, key_events_json_str).

    Looks for the LAST occurrence of the `KEY_EVENTS_JSON:` marker (rfind,
    not index) — the structured separator is always the final occurrence;
    earlier occurrences are AI quoting/referring to the marker in commentary.
    Everything before the last marker is the commentary (Markdown).
    Everything after (parsed as JSON) is events.

    Failures (no marker, malformed JSON, JSON not a list) silently fall
    back to (entire raw output as commentary, events_json = None).
    """
    marker = "KEY_EVENTS_JSON:"
    idx = raw.rfind(marker)
    if idx == -1:
        return raw, None

    commentary = raw[:idx].rstrip()
    events_part = raw[idx + len(marker):].strip()

    try:
        events = json.loads(events_part)
        if not isinstance(events, list):
            return commentary, None
        return commentary, json.dumps(events, ensure_ascii=False)
    except json.JSONDecodeError:
        return commentary, None


class _DataLike(Protocol):
    def get_market_overview(self) -> MarketOverview: ...
    def get_quote(self, ticker: str) -> Quote: ...
    def get_history(self, ticker: str, period: str = ...) -> list[Bar]: ...
    def get_news(self, ticker: str, limit: int = ...) -> list[NewsItem]: ...


class _AiLike(Protocol):
    def daily_commentary(
        self, *,
        market_summary: dict[str, Any],
        watchlist_perf: list[dict[str, Any]],
        holdings_overview: list[dict[str, Any]] | None = None,
        holdings_totals: dict[str, float] | None = None,
    ) -> str: ...


class RecapService:
    def __init__(self, session: Session, *, data: _DataLike, ai: _AiLike) -> None:
        self.session = session
        self.data = data
        self.ai = ai

    def generate(self, target: date) -> DailyRecap:
        recap = self._upsert_pending(target)
        try:
            market = self.data.get_market_overview()
            market_summary = {
                "spy": market.spy.change_pct,
                "qqq": market.qqq.change_pct,
                "dia": market.dia.change_pct,
                "vix": market.vix.price,
                "vix_change_pct": market.vix.change_pct,
            }
            watch = self.session.query(WatchlistItem).order_by(WatchlistItem.sort_order).all()
            perf: list[dict[str, Any]] = []
            news_summary: list[dict[str, Any]] = []
            for item in watch:
                row = self._build_ticker_row(item.ticker)
                if not row.get("error"):
                    news_summary.append({"ticker": item.ticker, "items": row.pop("news_items", [])})
                perf.append(row)
            # Fetch holdings + compute live P&L so commentary can mention them
            holdings = self.session.query(Holding).order_by(Holding.sort_order).all()
            holdings_overview = enrich_holdings(holdings, self.data) if holdings else []
            holdings_totals = compute_totals(holdings_overview) if holdings_overview else None

            if not perf and not holdings_overview:
                log.info("recap_empty_watchlist_and_holdings", date=str(target))
                commentary = "自选股清单和持仓均为空,无需生成 AI 点评。"
            else:
                try:
                    commentary = self.ai.daily_commentary(
                        market_summary=market_summary,
                        watchlist_perf=perf,
                        holdings_overview=holdings_overview or None,
                        holdings_totals=holdings_totals,
                    )
                except Exception as exc:
                    log.warning("commentary_failed", error=str(exc))
                    commentary = f"AI commentary unavailable ({exc})."

            recap.market_summary_json = json.dumps(market_summary)
            recap.watchlist_performance_json = json.dumps(perf)
            recap.holdings_overview_json = (
                json.dumps(holdings_overview) if holdings_overview else None
            )
            recap.holdings_totals_json = (
                json.dumps(holdings_totals) if holdings_totals else None
            )
            recap.news_summary_json = json.dumps(news_summary)
            commentary_md, events_json = _parse_ai_output(commentary)
            recap.ai_commentary_text = commentary_md
            recap.key_events_json = events_json
            recap.generation_status = "success"
            recap.error_message = None
            recap.generated_at = datetime.now(UTC)
        except Exception as exc:
            log.error("recap_failed", date=str(target), error=str(exc))
            recap.generation_status = "failed"
            recap.error_message = str(exc)
            recap.generated_at = datetime.now(UTC)
        self.session.commit()
        return recap

    def _upsert_pending(self, target: date) -> DailyRecap:
        existing = (
            self.session.query(DailyRecap).filter(DailyRecap.recap_date == target).one_or_none()
        )
        if existing:
            existing.generation_status = "pending"
            existing.error_message = None
            existing.market_summary_json = None
            existing.watchlist_performance_json = None
            existing.holdings_overview_json = None
            existing.holdings_totals_json = None
            existing.news_summary_json = None
            existing.ai_commentary_text = None
            existing.key_events_json = None
            existing.generated_at = None
            self.session.commit()
            return existing
        rec = DailyRecap(recap_date=target, generation_status="pending")
        self.session.add(rec)
        self.session.commit()
        return rec

    def _build_ticker_row(self, ticker: str) -> dict[str, Any]:
        try:
            quote = self.data.get_quote(ticker)
            bars = self.data.get_history(ticker, period="60d")
            news = self.data.get_news(ticker, limit=5)
            signals = detect_signals(quote, bars)
            return {
                "ticker": ticker,
                "price": quote.price,
                "change_pct": round(quote.change_pct, 2),
                "volume": quote.volume,
                "avg_volume_20d": quote.avg_volume_20d,
                "stale": quote.stale,
                "signals": signals,
                "error": None,
                "news_items": [
                    {"headline": n.headline, "url": n.url, "source": n.source,
                     "published_at": n.published_at.isoformat()}
                    for n in news
                ],
            }
        except Exception as exc:
            log.warning("ticker_row_failed", ticker=ticker, error=str(exc))
            return {"ticker": ticker, "error": str(exc), "signals": []}
