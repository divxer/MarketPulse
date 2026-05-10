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
    ) -> None:
        self.session = session
        self.ai = ai_client
        self.data = data
        self.model = model
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
        response = self.ai.complete(system=system, user=data)
        now = datetime.now(UTC)
        record = AiAnalysis(
            ticker=ticker,
            model=self.model,
            prompt_version=version,
            input_data_json=json.dumps({"quote": quote.price, "n_news": len(news)}),
            response_markdown=response,
            requested_at=now,
            expires_at=now + timedelta(hours=self.ttl_hours),
        )
        self.session.add(record)
        self.session.commit()
        return AnalysisResult(
            ticker=ticker,
            model=self.model,
            prompt_version=version,
            response_markdown=response,
            requested_at=now,
            cached=False,
        )

    def daily_commentary(
        self, *, market_summary: dict[str, Any], watchlist_perf: list[dict[str, Any]]
    ) -> str:
        prompt_text = prompts.render_commentary_prompt(
            market_summary=market_summary, watchlist_perf=watchlist_perf,
        )
        system, data = _split_prompt(prompt_text)
        return self.ai.complete(system=system, user=data)

    def _lookup_cache(self, ticker: str, version: str) -> AiAnalysis | None:
        stmt = (
            select(AiAnalysis)
            .where(AiAnalysis.ticker == ticker)
            .where(AiAnalysis.prompt_version == version)
            .where(AiAnalysis.expires_at > datetime.now(UTC))
            .order_by(AiAnalysis.requested_at.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()
