"""Rough model-call estimate for the judging stage, made before the calls are spent."""

from __future__ import annotations

import math

from decision_agent.config import Settings

CLARIFY_CALLS = {"full": 10, "batch": 2, "none": 1}
# Share of topics that end up with an uncertain line and therefore plan a clarification question.
CLARIFY_SHARE = 0.5
# With consistent_single, a line judged CONSISTENT takes one sample; the rest take a second one.
SECOND_SAMPLE_SHARE = 0.5
ROUTING_STEPS = 5


def estimate_topic_calls(candidate_count: int, core: Settings) -> int:
    """Typical number of calls to check one topic that has `candidate_count` candidate lines."""
    if candidate_count <= 0:
        return 0
    calls = 1.0  # impact selection
    chunks = math.ceil(candidate_count / core.judge_batch) if core.judge_batch > 1 else candidate_count
    if core.adaptive_min_samples <= 0:
        samples = float(core.n)
    elif core.consistent_single:
        samples = 1.0 + (SECOND_SAMPLE_SHARE if core.adaptive_min_samples > 1 else 0.0)
    else:
        samples = float(core.adaptive_min_samples)
    calls += chunks * samples
    calls += CLARIFY_CALLS.get(core.clarify_gain, 2) * CLARIFY_SHARE
    if core.routing == "llm":
        calls += ROUTING_STEPS
    return math.ceil(calls)


def estimate_calls(candidate_counts: list[int], core: Settings) -> int:
    return sum(estimate_topic_calls(c, core) for c in candidate_counts)
