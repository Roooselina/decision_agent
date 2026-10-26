from __future__ import annotations

from collections import Counter

from decision_agent.config import DEFAULT_SETTINGS, Settings
from decision_agent.consistency import PreparedCheck, judge_batch, judge_prepared, prepare_check
from decision_agent.llm.base import LLMProvider
from decision_agent.models import Artifact, ConsistencyLabel, ConsistencyResult
from decision_agent.parallel import run_parallel
from decision_agent.storage import SQLiteStore


def evidence_is_insufficient(effective_decision_id: str | None) -> bool:
    """Evidence is insufficient when no effective decision could be determined."""
    return effective_decision_id is None


def summarize_samples(samples: list[ConsistencyResult], settings: Settings) -> ConsistencyResult:
    """Majority label of the samples; UNCERTAIN when agreement or evidence is weak."""
    labels = [s.label for s in samples]
    majority, count = Counter(labels).most_common(1)[0]
    agreement = count / len(samples)
    result = samples[labels.index(majority)].model_copy()
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


def _first_samples(settings: Settings) -> int:
    """How many samples to take before deciding whether more are needed."""
    if settings.adaptive_min_samples <= 0:
        return settings.n
    return max(1, min(settings.adaptive_min_samples, settings.n))


def sample_prepared(
    prepared: PreparedCheck, provider: LLMProvider, settings: Settings
) -> ConsistencyResult:
    """Sample one prepared artifact. With adaptive sampling, stop once the first samples agree."""
    if prepared.early is not None:
        return summarize_samples([prepared.early] * settings.n, settings)
    first = _first_samples(settings)
    samples: list[ConsistencyResult] = []
    for number in range(settings.n):
        samples.append(judge_prepared(prepared, provider, settings.sample_temperature))
        if number + 1 == first and len({s.label for s in samples}) == 1:
            break
    return summarize_samples(samples, settings)


def estimate_uncertainty(
    store: SQLiteStore,
    topic: str,
    artifact: Artifact,
    as_of_date: str,
    provider: LLMProvider,
    settings: Settings = DEFAULT_SETTINGS,
    extra_evidence: list[str] | None = None,
) -> ConsistencyResult:
    """Sample the judgement and mark UNCERTAIN when agreement or evidence is weak.

    The confidence field is a heuristic: the share of samples that agree with the majority
    label. It is not a calibrated probability. When the final label is UNCERTAIN because the
    effective decision or the scope could not be determined, confidence is 0.0. With adaptive
    sampling, the first samples that all agree end the sampling (agreement is then 1.0).
    """
    prepared = prepare_check(store, topic, artifact, as_of_date, extra_evidence)
    return sample_prepared(prepared, provider, settings)


def estimate_many(
    prepared: list[PreparedCheck], provider: LLMProvider, settings: Settings = DEFAULT_SETTINGS
) -> list[ConsistencyResult]:
    """Judge many artifacts: identical ones once, similar ones together, all calls in parallel.

    Results come back in input order. Artifacts that need no model call (no effective decision)
    are answered locally. When several artifacts share a decision they go to the model in groups
    of settings.judge_batch; artifacts whose samples disagree, or that the model skipped, are
    judged again one at a time with the full sample count.
    """
    results: dict[int, ConsistencyResult] = {}
    groups: dict[tuple, list[int]] = {}
    for index, item in enumerate(prepared):
        if item.early is not None:
            results[index] = sample_prepared(item, provider, settings)
        else:
            groups.setdefault(item.signature(), []).append(index)
    representatives = [indexes[0] for indexes in groups.values()]
    outcome: dict[int, ConsistencyResult] = {}

    size = max(1, settings.judge_batch)
    redo: list[int] = []
    if size > 1 and len(representatives) > 1:
        by_decision: dict[tuple, list[int]] = {}
        for index in representatives:
            by_decision.setdefault(prepared[index].group_key(), []).append(index)
        chunks = [
            indexes[start : start + size]
            for indexes in by_decision.values()
            for start in range(0, len(indexes), size)
        ]
        first = _first_samples(settings)
        tasks = [(chunk, number) for chunk in chunks for number in range(first)]

        def run(task: tuple[list[int], int]) -> dict[str, ConsistencyResult]:
            chunk, _ = task
            try:
                return judge_batch([prepared[i] for i in chunk], provider, settings.sample_temperature)
            except Exception as exc:
                if exc.__class__.__name__ == "BudgetExceeded":
                    raise
                return {}

        answers = run_parallel(run, tasks)
        samples: dict[int, list[ConsistencyResult]] = {i: [] for i in representatives}
        for (chunk, _), answer in zip(tasks, answers, strict=True):
            for index in chunk:
                got = answer.get(prepared[index].artifact.id)
                if got is not None:
                    samples[index].append(got)
        for index in representatives:
            taken = samples[index]
            agreed = len(taken) == first and len({s.label for s in taken}) == 1
            if len(taken) == first and (agreed or settings.adaptive_min_samples <= 0):
                outcome[index] = summarize_samples(taken, settings)
            else:
                redo.append(index)
    else:
        redo = list(representatives)

    if redo:
        redone = run_parallel(lambda i: sample_prepared(prepared[i], provider, settings), redo)
        outcome.update(zip(redo, redone, strict=True))

    for indexes in groups.values():
        shared = outcome[indexes[0]]
        for index in indexes:
            results[index] = shared.model_copy(update={"artifact_id": prepared[index].artifact.id})
    return [results[i] for i in range(len(prepared))]


def record_calibration_pair(
    result: ConsistencyResult, truth: ConsistencyLabel
) -> tuple[float, bool]:
    """Return (confidence, correct) for later reliability analysis."""
    return result.confidence, result.label == truth
