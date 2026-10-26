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


def _accepts_first_sample(first: ConsistencyResult, settings: Settings) -> bool:
    """A CONSISTENT first sample ends the sampling when consistent_single is on."""
    return (
        settings.consistent_single
        and settings.adaptive_min_samples > 0
        and first.label == ConsistencyLabel.CONSISTENT
    )


def sample_prepared(
    prepared: PreparedCheck, provider: LLMProvider, settings: Settings, strict: bool = False
) -> ConsistencyResult:
    """Sample one prepared artifact. With adaptive sampling, stop once the first samples agree.

    strict is used after earlier samples disagreed: a CONSISTENT first sample is then not enough,
    and at least two samples must agree before the sampling stops.
    """
    if prepared.early is not None:
        return summarize_samples([prepared.early] * settings.n, settings)
    first = max(2, _first_samples(settings)) if strict else _first_samples(settings)
    samples: list[ConsistencyResult] = []
    for number in range(settings.n):
        samples.append(judge_prepared(prepared, provider, settings.sample_temperature))
        if number == 0 and not strict and _accepts_first_sample(samples[0], settings):
            break
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
    disagreed: set[int] = set()
    if size > 1 and len(representatives) > 1:
        first = _first_samples(settings)
        samples: dict[int, list[ConsistencyResult]] = {i: [] for i in representatives}

        def run_round(indexes: list[int], count: int) -> None:
            """Ask `count` independent batch samples for the given artifacts, in parallel."""
            by_decision: dict[tuple, list[int]] = {}
            for index in indexes:
                by_decision.setdefault(prepared[index].group_key(), []).append(index)
            chunks = [
                members[start : start + size]
                for members in by_decision.values()
                for start in range(0, len(members), size)
            ]
            tasks = [chunk for chunk in chunks for _ in range(count)]

            def run(chunk: list[int]) -> dict[str, ConsistencyResult]:
                try:
                    return judge_batch(
                        [prepared[i] for i in chunk], provider, settings.sample_temperature
                    )
                except Exception as exc:
                    if exc.__class__.__name__ == "BudgetExceeded":
                        raise
                    return {}

            answers = run_parallel(run, tasks)
            for chunk, answer in zip(tasks, answers, strict=True):
                for index in chunk:
                    got = answer.get(prepared[index].artifact.id)
                    if got is not None:
                        samples[index].append(got)

        if settings.adaptive_min_samples <= 0:
            # Every sample is taken in one parallel wave and summarized as is.
            run_round(representatives, first)
            for index in representatives:
                taken = samples[index]
                if len(taken) == first:
                    outcome[index] = summarize_samples(taken, settings)
                else:
                    redo.append(index)
        else:
            pending = list(representatives)
            for number in range(first):
                if not pending:
                    break
                run_round(pending, 1)
                carried: list[int] = []
                for index in pending:
                    taken = samples[index]
                    if len(taken) != number + 1:
                        redo.append(index)
                    elif len({s.label for s in taken}) > 1:
                        redo.append(index)
                        disagreed.add(index)
                    elif number == 0 and _accepts_first_sample(taken[0], settings) or number + 1 == first:
                        outcome[index] = summarize_samples(taken, settings)
                    else:
                        carried.append(index)
                pending = carried
    else:
        redo = list(representatives)

    if redo:
        redone = run_parallel(
            lambda i: sample_prepared(prepared[i], provider, settings, strict=i in disagreed), redo
        )
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
