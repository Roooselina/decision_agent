from __future__ import annotations

import types

from decision_agent.llm.anthropic_provider import AnthropicProvider


class FakeMessages:
    def __init__(self, reject):
        self.reject = reject
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.reject == "type" and "temperature" in kwargs:
            raise TypeError("got an unexpected keyword argument 'temperature'")
        if self.reject == "api" and "temperature" in kwargs:
            raise RuntimeError("temperature is not supported for this model")
        return types.SimpleNamespace(content=[types.SimpleNamespace(text=" ok ")])


def make(reject):
    provider = AnthropicProvider(api_key="k" * 12, model="m")
    provider.client = types.SimpleNamespace(messages=FakeMessages(reject))
    return provider


def test_retries_without_temperature_and_remembers():
    for reject in ("type", "api"):
        provider = make(reject)
        assert provider.complete("s", "u", 0.0) == "ok"
        assert provider.complete("s", "u", 0.0) == "ok"
        calls = provider.client.messages.calls
        assert "temperature" in calls[0] and all("temperature" not in c for c in calls[1:])
        assert len(calls) == 3


def test_sends_temperature_when_accepted():
    provider = make(None)
    provider.complete("s", "u", 0.3)
    assert provider.client.messages.calls[0]["temperature"] == 0.3
