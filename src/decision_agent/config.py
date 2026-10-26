from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class Settings:
    k: int = 8
    n: int = 5
    tau_conf: float = 0.8
    max_questions: int = 2
    min_gain: float = 0.1
    max_steps: int = 20
    sample_temperature: float = 0.7
    tfidf_analyzer: str = "char_wb"
    tfidf_ngram_min: int = 2
    tfidf_ngram_max: int = 4
    model_name: str = ""
    # Speed settings. concurrency is the number of LLM calls in flight at once; batch sizes group
    # several independent items into one call (1 turns batching off); adaptive_min_samples stops
    # sampling early when that many samples already agree (0 samples all n every time).
    concurrency: int = 8
    extract_batch: int = 8
    judge_batch: int = 8
    adaptive_min_samples: int = 2
    k_max: int = 30

    @classmethod
    def from_env(cls) -> Settings:
        def number(name: str, default: int) -> int:
            try:
                return max(0, int(os.getenv(name, "")))
            except ValueError:
                return default

        return cls(
            model_name=os.getenv("DECISION_AGENT_MODEL", ""),
            concurrency=max(1, number("DECISION_AGENT_CONCURRENCY", 8)),
            extract_batch=max(1, number("DECISION_AGENT_EXTRACT_BATCH", 8)),
            judge_batch=max(1, number("DECISION_AGENT_JUDGE_BATCH", 8)),
            adaptive_min_samples=number("DECISION_AGENT_ADAPTIVE_MIN", 2),
        )


def effective_k(k: int, artifact_count: int, k_max: int | None = None) -> int:
    """Candidate count per topic. It grows with project size so large folders are not truncated.

    Up to 60 artifacts the configured k is used unchanged. Beyond that k grows by one per 30
    artifacts, up to k_max.
    """
    limit = k_max if k_max is not None else DEFAULT_SETTINGS.k_max
    if artifact_count <= 60:
        return k
    return min(max(k, limit), k + -(-(artifact_count - 60) // 30))


DEFAULT_SETTINGS = Settings.from_env()
