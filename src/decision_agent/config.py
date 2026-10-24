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

    @classmethod
    def from_env(cls) -> Settings:
        return cls(model_name=os.getenv("DECISION_AGENT_MODEL", ""))


DEFAULT_SETTINGS = Settings.from_env()
