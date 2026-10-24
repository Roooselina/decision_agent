from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def extract_json_text(raw: str) -> str:
    """Strip code fences and surrounding prose so json.loads can parse model output."""
    text = _FENCE_RE.sub("", raw.strip()).strip()
    if text.startswith(("{", "[")):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


class LLMProvider(ABC):
    """All LLM calls go through this interface so tests can run without network access."""

    @abstractmethod
    def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
        json_schema: dict | None = None,
    ) -> str:
        raise NotImplementedError

    def complete_json(
        self,
        system: str,
        user: str,
        schema_model: type[T],
        temperature: float = 0.0,
    ) -> T:
        """Call complete() and validate the JSON result; retry up to two more times."""
        last_error: Exception | None = None
        for _ in range(3):
            try:
                raw = self.complete(system, user, temperature, schema_model.model_json_schema())
                return schema_model.model_validate(json.loads(extract_json_text(raw)))
            except Exception as exc:  # noqa: BLE001 - retry on any parse or validation failure
                last_error = exc
        assert last_error is not None
        raise last_error
