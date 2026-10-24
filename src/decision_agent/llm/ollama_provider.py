from __future__ import annotations

import json

from decision_agent.connectors.http import Http, HttpError, UrllibHttp
from decision_agent.llm.base import LLMProvider


class OllamaConnectionError(RuntimeError):
    """The Ollama server cannot be reached."""


class OllamaModelMissing(RuntimeError):
    """The server answered but does not have the requested model."""


class OllamaProvider(LLMProvider):
    """Local model through an Ollama server. Documents stay on the machine (or the given server)."""

    def __init__(
        self, base_url: str, model: str, http: Http | None = None, timeout: float = 600.0
    ) -> None:
        if not model:
            raise RuntimeError("모델 이름이 필요합니다.")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.http = http or UrllibHttp()
        self.timeout = timeout

    def complete(
        self, system: str, user: str, temperature: float = 0.0, json_schema: dict | None = None
    ) -> str:
        note = ""
        if json_schema:
            note = "\n반드시 다음 JSON Schema에 맞는 JSON만 출력하세요:\n" + json.dumps(
                json_schema, ensure_ascii=False
            )
        payload: dict = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system + note},
                {"role": "user", "content": user},
            ],
            "options": {"temperature": temperature},
        }
        if json_schema or "JSON" in system:
            payload["format"] = "json"
        try:
            response = self.http.request(
                "POST", f"{self.base_url}/api/chat", json_body=payload, timeout=self.timeout
            )
        except HttpError as exc:
            raise OllamaConnectionError(str(exc)) from exc
        if response.status == 404:
            raise OllamaModelMissing(f"Ollama에 모델 '{self.model}'이(가) 없습니다.")
        if not response.ok:
            raise OllamaConnectionError(f"Ollama가 오류를 돌려줬습니다 ({response.status}).")
        message = response.json().get("message") or {}
        return str(message.get("content", "")).strip()
