from decision_agent.llm.base import LLMProvider
from decision_agent.llm.caching import CachingProvider


class Counter(LLMProvider):
    def __init__(self):
        self.n = 0

    def complete(self, system, user, temperature=0.0, json_schema=None):
        self.n += 1
        return f"response-{self.n}"


def test_replays_identical_prompts_in_order_after_begin_run():
    inner = Counter()
    cache = CachingProvider(inner)
    first = [cache.complete("s", "u", 0.7) for _ in range(3)]
    assert first == ["response-1", "response-2", "response-3"]
    cache.begin_run()
    assert [cache.complete("s", "u", 0.7) for _ in range(3)] == first
    assert inner.n == 3
    assert cache.complete("s", "u", 0.7) == "response-4"
    assert cache.complete("s", "changed prompt", 0.7) == "response-5"
