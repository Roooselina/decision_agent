from __future__ import annotations

import json
import os

from decision_agent.config import DEFAULT_SETTINGS
from decision_agent.llm.base import LLMProvider


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        from anthropic import Anthropic

        api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY가 필요합니다.")
        # The SDK retries rate limits and overloads with backoff; many parallel calls make that matter.
        self.client = Anthropic(api_key=api_key, max_retries=6, timeout=90.0)
        self.send_temperature = True
        self.model = model or os.getenv("DECISION_AGENT_MODEL") or DEFAULT_SETTINGS.model_name
        if not self.model:
            raise RuntimeError("DECISION_AGENT_MODEL이 필요합니다.")

    def complete(
        self, system: str, user: str, temperature: float = 0.0, json_schema: dict | None = None
    ) -> str:
        schema_note = ""
        if json_schema:
            schema_note = "\n반드시 다음 JSON Schema에 맞는 JSON만 출력하세요:\n" + json.dumps(
                json_schema, ensure_ascii=False
            )
        params = {
            "model": self.model,
            "max_tokens": 1800,
            "system": system + schema_note,
            "messages": [{"role": "user", "content": user}],
        }
        msg = None
        if self.send_temperature:
            try:
                msg = self.client.messages.create(temperature=temperature, **params)
            except TypeError:
                self.send_temperature = False
            except Exception as exc:
                if "temperature" not in str(exc).lower():
                    raise
                self.send_temperature = False
        if msg is None:
            msg = self.client.messages.create(**params)
        parts = [getattr(p, "text", "") for p in msg.content]
        return "".join(parts).strip()
