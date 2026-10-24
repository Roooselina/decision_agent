from __future__ import annotations

from collections import defaultdict

from decision_agent.llm.base import LLMProvider


class CachingProvider(LLMProvider):
    """Replays earlier responses in call order so a UI rerun does not repeat LLM calls.

    Responses are keyed by (system, user, temperature) and replayed by position, so repeated
    sampling calls with identical prompts still return distinct recorded samples. Calls whose
    prompt changed (for example after a user answer) go to the wrapped provider.
    """

    def __init__(self, inner: LLMProvider) -> None:
        self.inner = inner
        self._recorded: dict[tuple[str, str, float], list[str]] = defaultdict(list)
        self._cursor: dict[tuple[str, str, float], int] = defaultdict(int)
        self.live_calls = 0

    def begin_run(self) -> None:
        """Reset replay positions at the start of every analysis run."""
        self._cursor.clear()

    def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
        json_schema: dict | None = None,
    ) -> str:
        key = (system, user, temperature)
        position = self._cursor[key]
        self._cursor[key] += 1
        if position < len(self._recorded[key]):
            return self._recorded[key][position]
        self.live_calls += 1
        raw = self.inner.complete(system, user, temperature, json_schema)
        self._recorded[key].append(raw)
        return raw
