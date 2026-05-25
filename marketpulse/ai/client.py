from typing import Protocol

import anthropic
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from marketpulse.config import get_settings


class AiClient(Protocol):
    def complete(self, *, system: str, user: str, model: str | None = None) -> str: ...


# Retry only on transient API/network errors. Validation errors (bad input) shouldn't retry.
_AI_RETRY_EXCEPTIONS = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)


class AnthropicClient:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self._client = anthropic.Anthropic(api_key=api_key or settings.anthropic_api_key)
        self._model = model or settings.ai_model

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=8),
        retry=retry_if_exception_type(_AI_RETRY_EXCEPTIONS),
    )
    def complete(self, *, system: str, user: str, model: str | None = None) -> str:
        # Only attach a system block when there's actual content. Anthropic API
        # rejects empty text blocks under cache_control with
        #   "system.0: cache_control cannot be set for empty text blocks".
        # The router stage (service._route_strategy) calls complete(system="")
        # because the router prompt is fully embedded in the user message;
        # without this guard every router call 400'd and silently fell back
        # to the "general" strategy, defeating Phase 3 entirely.
        kwargs: dict = {
            "model": model or self._model,
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": user}],
        }
        if system:
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                },
            ]
        msg = self._client.messages.create(**kwargs)
        parts: list[str] = []
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
        return "".join(parts)
