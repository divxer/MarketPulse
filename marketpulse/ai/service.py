import json
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketpulse.ai import prompts
from marketpulse.ai.client import AiClient
from marketpulse.ai.types import AnalysisResult
from marketpulse.data.types import Bar, Fundamentals, NewsItem, Quote
from marketpulse.db.models import AiAnalysis
from marketpulse.evaluation.constants import AIVerdict
from marketpulse.evaluation.events import record_event
from marketpulse.logging import get_logger

log = get_logger(__name__)

_DATA_SEPARATOR = "\n\nDATA:\n"


def _split_prompt(rendered: str) -> tuple[str, str]:
    """Split a rendered prompt into (system, user_data).

    The renderers in marketpulse.ai.prompts return a single string of the form
    `<system>\n\nDATA:\n<json>`. The system part is sent as the API system prompt
    (cacheable); the JSON data is sent as the user message body.
    """
    system, sep, data = rendered.partition(_DATA_SEPARATOR)
    if not sep:
        raise ValueError("rendered prompt missing DATA separator")
    return system, data


def _parse_analyze_output(raw: str) -> tuple[str, dict | None]:
    """Split AiService.analyze() AI output into (markdown_body, verdict_dict).

    Looks for the LAST occurrence of `VERDICTS_JSON:` marker (rfind to
    tolerate AI quoting the marker in body). Everything before is the
    markdown analysis. Everything after (parsed as a JSON object) is
    the single verdict.

    Failures (no marker, malformed JSON) silently return (raw, None).
    """
    marker = "VERDICTS_JSON:"
    idx = raw.rfind(marker)
    if idx == -1:
        return raw, None

    md = raw[:idx].rstrip()
    tail = raw[idx + len(marker):].strip()
    try:
        verdict = json.loads(tail)
        if not isinstance(verdict, dict):
            return md, None
        return md, verdict
    except json.JSONDecodeError:
        return md, None


class _DataLike(Protocol):
    def get_quote(self, ticker: str) -> Quote: ...
    def get_history(self, ticker: str, period: str = ...) -> list[Bar]: ...
    def get_news(self, ticker: str, limit: int = ...) -> list[NewsItem]: ...
    def get_fundamentals(self, ticker: str) -> Fundamentals: ...


class AiService:
    def __init__(
        self,
        session: Session,
        *,
        ai_client: AiClient,
        data: _DataLike,
        model: str,
        ttl_hours: int,
        model_analyze: str | None = None,
    ) -> None:
        self.session = session
        self.ai = ai_client
        self.data = data
        self.model = model
        # /stock deep-analysis can use a premium model (e.g. Opus). Falls back
        # to `model` when not set. Cheap features (recap, risk) always use `model`.
        self.model_analyze = model_analyze or model
        self.ttl_hours = ttl_hours

    def analyze(self, ticker: str) -> AnalysisResult:
        version = prompts.ANALYSIS_PROMPT_VERSION
        cached = self._lookup_cache(ticker, version)
        if cached:
            return AnalysisResult(
                ticker=ticker,
                model=cached.model,
                prompt_version=cached.prompt_version,
                response_markdown=cached.response_markdown,
                requested_at=cached.requested_at,
                cached=True,
            )

        quote = self.data.get_quote(ticker)
        fundamentals = self.data.get_fundamentals(ticker)
        bars = self.data.get_history(ticker, period="60d")
        news = self.data.get_news(ticker, limit=10)
        prompt_text = prompts.render_analysis_prompt(
            quote=quote, fundamentals=fundamentals, news=news, bars=bars,
        )
        system, data = _split_prompt(prompt_text)
        response = self.ai.complete(system=system, user=data, model=self.model_analyze)
        now = datetime.now(UTC)
        input_snapshot = {
            "ticker": quote.ticker,
            "quote": {
                "price": quote.price,
                "change_pct": quote.change_pct,
                "volume": quote.volume,
                "avg_volume_20d": quote.avg_volume_20d,
                "stale": quote.stale,
            },
            "fundamentals": {
                "market_cap": fundamentals.market_cap,
                "pe_ratio": fundamentals.pe_ratio,
                "eps": fundamentals.eps,
                "sector": fundamentals.sector,
                "industry": fundamentals.industry,
            },
            "bars": {
                "count": len(bars),
                "first_date": bars[0].date.isoformat() if bars else None,
                "last_date": bars[-1].date.isoformat() if bars else None,
            },
            "news": [
                {
                    "headline": n.headline,
                    "source": n.source,
                    "url": n.url,
                    "published_at": n.published_at.isoformat(),
                }
                for n in news
            ],
        }
        record = AiAnalysis(
            ticker=ticker,
            model=self.model_analyze,
            prompt_version=version,
            input_data_json=json.dumps(input_snapshot, default=str),
            response_markdown=response,
            requested_at=now,
            expires_at=now + timedelta(hours=self.ttl_hours),
        )
        self.session.add(record)

        # Phase 2: parse verdict and record event (same transaction as AiAnalysis)
        _, verdict = _parse_analyze_output(response)
        if verdict is not None:
            v_value = verdict.get("verdict")
            v_ticker = (verdict.get("ticker") or "").strip().upper()
            if v_value in AIVerdict.all() and v_ticker:
                try:
                    record_event(
                        event_type="ai_analysis",
                        subtype=v_value,
                        ticker=v_ticker,
                        event_time=now,
                        event_price=quote.price,
                        payload={
                            "rationale": verdict.get("rationale", ""),
                            "prompt_version": version,
                            "source": "stock_analysis",
                            "model": self.model_analyze,
                        },
                        db=self.session,
                    )
                except ValueError as exc:
                    log.warning("ai_verdict_invalid", error=str(exc), verdict=verdict)
                except Exception as exc:
                    log.warning("record_event_failed", error=str(exc))
            else:
                log.warning("ai_verdict_invalid_shape", verdict=verdict)

        # Single commit covering both AiAnalysis + EvaluationEvent
        self.session.commit()
        return AnalysisResult(
            ticker=ticker,
            model=self.model_analyze,
            prompt_version=version,
            response_markdown=response,
            requested_at=now,
            cached=False,
        )

    def daily_commentary(
        self, *,
        market_summary: dict[str, Any],
        watchlist_perf: list[dict[str, Any]],
        holdings_overview: list[dict[str, Any]] | None = None,
        holdings_totals: dict[str, float] | None = None,
    ) -> str:
        prompt_text = prompts.render_commentary_prompt(
            market_summary=market_summary,
            watchlist_perf=watchlist_perf,
            holdings_overview=holdings_overview,
            holdings_totals=holdings_totals,
        )
        system, data = _split_prompt(prompt_text)
        return self.ai.complete(system=system, user=data)

    def portfolio_risk(
        self, *,
        holdings: list[dict[str, Any]],
        totals: dict[str, float],
        allocation: list[dict[str, Any]],
        realized_pl: float,
        trading_stats: dict[str, Any],
    ) -> str:
        """Generate a Markdown risk analysis of the current portfolio.

        Not cached — the caller decides when to refresh (typically on-demand
        from the /holdings page, since portfolio state changes per trade).
        """
        prompt_text = prompts.render_risk_prompt(
            holdings=holdings,
            totals=totals,
            allocation=allocation,
            realized_pl=realized_pl,
            trading_stats=trading_stats,
        )
        system, data = _split_prompt(prompt_text)
        return self.ai.complete(system=system, user=data)

    def portfolio_risk_cached(
        self, *,
        holdings: list[dict[str, Any]],
        totals: dict[str, float],
        allocation: list[dict[str, Any]],
        realized_pl: float,
        trading_stats: dict[str, Any],
    ) -> str:
        """portfolio_risk() with content-fingerprint cache.

        Cache key fingerprint covers sorted (ticker, quantity, avg_cost) tuples.
        Same portfolio state → cache hit (no API call). Holdings change →
        fingerprint differs → cache miss → fresh call. TTL self.ttl_hours
        ensures daily refresh even when state is identical.
        """
        fp = self._portfolio_fingerprint(holdings)
        cache_key = f"{prompts.RISK_PROMPT_VERSION}::{fp}"
        cached = self._lookup_portfolio_risk_cache(cache_key)
        if cached is not None:
            return cached

        response = self.portfolio_risk(
            holdings=holdings,
            totals=totals,
            allocation=allocation,
            realized_pl=realized_pl,
            trading_stats=trading_stats,
        )
        self._save_portfolio_risk_cache(cache_key, holdings, totals, response)
        return response

    @staticmethod
    def _portfolio_fingerprint(holdings: list[dict[str, Any]]) -> str:
        """12-char hex SHA-256 of sorted (ticker, quantity, avg_cost) tuples.
        Intraday price changes do NOT invalidate (TTL covers daily refresh)."""
        import hashlib
        state = sorted(
            (h["ticker"], float(h["quantity"]), float(h["avg_cost"]))
            for h in holdings
        )
        payload = json.dumps(state, default=str, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]

    def _lookup_portfolio_risk_cache(self, cache_key: str) -> str | None:
        """Look up a non-expired portfolio risk cache row by prompt_version key.

        Reuses the AiAnalysis table with ticker='__portfolio__'.
        """
        stmt = (
            select(AiAnalysis)
            .where(AiAnalysis.ticker == "__portfolio__")
            .where(AiAnalysis.model == self.model)
            .where(AiAnalysis.prompt_version == cache_key)
            .where(AiAnalysis.expires_at > datetime.now(UTC))
            .order_by(AiAnalysis.requested_at.desc())
            .limit(1)
        )
        row = self.session.execute(stmt).scalar_one_or_none()
        return row.response_markdown if row else None

    def _save_portfolio_risk_cache(
        self,
        cache_key: str,
        holdings: list[dict[str, Any]],
        totals: dict[str, float],
        response: str,
    ) -> None:
        now = datetime.now(UTC)
        record = AiAnalysis(
            ticker="__portfolio__",
            model=self.model,
            prompt_version=cache_key,
            input_data_json=json.dumps({
                "holdings_count": len(holdings),
                "totals": totals,
            }, default=str),
            response_markdown=response,
            requested_at=now,
            expires_at=now + timedelta(hours=self.ttl_hours),
        )
        self.session.add(record)
        self.session.commit()

    def _lookup_cache(self, ticker: str, version: str) -> AiAnalysis | None:
        # Cache scoped to (ticker, model, prompt_version) so switching either
        # the model or the prompt template invalidates and forces a fresh call.
        stmt = (
            select(AiAnalysis)
            .where(AiAnalysis.ticker == ticker)
            .where(AiAnalysis.model == self.model_analyze)
            .where(AiAnalysis.prompt_version == version)
            .where(AiAnalysis.expires_at > datetime.now(UTC))
            .order_by(AiAnalysis.requested_at.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()
