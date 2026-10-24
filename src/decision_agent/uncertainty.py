from __future__ import annotations

from collections import Counter

from decision_agent.config import DEFAULT_SETTINGS, Settings
from decision_agent.consistency import check
from decision_agent.llm.base import LLMProvider
from decision_agent.models import Artifact, ConsistencyLabel, ConsistencyResult
from decision_agent.storage import SQLiteStore


def evidence_is_insufficient(effective_decision_id: str | None) -> bool:
    """Evidence is insufficient when no effective decision could be determined."""
    return effective_decision_id is None


def estimate_uncertainty(
    store: SQLiteStore,
    topic: str,
    artifact: Artifact,
    as_of_date: str,
    provider: LLMProvider,
    settings: Settings = DEFAULT_SETTINGS,
    extra_evidence: list[str] | None = None,
) -> ConsistencyResult:
    """Sample the judgement N times and mark UNCERTAIN when agreement or evidence is weak.

    The confidence field is a heuristic: the share of samples that agree with the majority
    label. It is not a calibrated probability. When the final label is UNCERTAIN because the
    effective decision or the scope could not be determined, confidence is 0.0.
    """
    samples = [
        check(
            store,
            topic,
            artifact,
            as_of_date,
            provider,
            settings.sample_temperature,
            extra_evidence,
        )
        for _ in range(settings.n)
    ]
    labels = [s.label for s in samples]
    majority, count = Counter(labels).most_common(1)[0]
    agreement = count / len(samples)
    result = samples[labels.index(majority)]
    insufficient = evidence_is_insufficient(result.effective_decision_id)
    low_agreement = agreement < settings.tau_conf
    if low_agreement or insufficient or majority == ConsistencyLabel.UNCERTAIN:
        result.label = ConsistencyLabel.UNCERTAIN
        if insufficient:
            result.evidence = (result.evidence + " 검색 근거가 부족합니다.").strip()
        if low_agreement:
            result.evidence = (result.evidence + " 반복 판단 결과가 일치하지 않습니다.").strip()
    result.confidence = (
        agreement if (result.label != ConsistencyLabel.UNCERTAIN or low_agreement) else 0.0
    )
    return result


def record_calibration_pair(
    result: ConsistencyResult, truth: ConsistencyLabel
) -> tuple[float, bool]:
    """Return (confidence, correct) for later reliability analysis."""
    return result.confidence, result.label == truth
