"""AnthropicClient — empty-system-block guard.

Regression test for the router stage. AiService._route_strategy calls
client.complete(system="", user=...) since the entire router prompt is in
the user message. Anthropic API rejects empty text blocks under
cache_control with HTTP 400 "system.0: cache_control cannot be set for
empty text blocks", which previously caused every router call to silently
fall back to the 'general' strategy in production.
"""
# Layer: unit

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from marketpulse.ai.client import AnthropicClient


def _stub_response() -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok")],
    )


def test_complete_omits_system_when_empty(monkeypatch):
    client = AnthropicClient(api_key="sk-test")
    mock_create = MagicMock(return_value=_stub_response())
    monkeypatch.setattr(client._client.messages, "create", mock_create)

    client.complete(system="", user="hi", model="claude-haiku-4-5")

    kwargs = mock_create.call_args.kwargs
    assert "system" not in kwargs, (
        "system kwarg must be omitted when text is empty — otherwise "
        "Anthropic rejects the cache_control on the empty text block"
    )
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


def test_complete_attaches_system_with_cache_control(monkeypatch):
    client = AnthropicClient(api_key="sk-test")
    mock_create = MagicMock(return_value=_stub_response())
    monkeypatch.setattr(client._client.messages, "create", mock_create)

    client.complete(system="you are an analyst", user="ticker?", model="m")

    kwargs = mock_create.call_args.kwargs
    assert kwargs["system"] == [
        {
            "type": "text",
            "text": "you are an analyst",
            "cache_control": {"type": "ephemeral"},
        }
    ]
