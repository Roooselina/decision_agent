from __future__ import annotations

import json
import threading

import pytest

from decision_agent.config import DEFAULT_SETTINGS, Settings, effective_k
from decision_agent.consistency import prepare_check
from decision_agent.desktop.providers import BudgetExceeded, CountingProvider
from decision_agent.extraction import extract_decisions
from decision_agent.llm.base import LLMProvider
from decision_agent.llm.mock_provider import MockProvider
from decision_agent.models import ConsistencyLabel, MeetingItem
from decision_agent.parallel import run_parallel
from decision_agent.uncertainty import estimate_many, estimate_uncertainty

END = "2026-12-31"


def test_run_parallel_keeps_input_order_and_runs_concurrently():
    seen = set()
    lock = threading.Lock()

    def work(n):
        with lock:
            seen.add(threading.current_thread().name)
        return n * 2

    assert run_parallel(work, list(range(40)), workers=8) == [n * 2 for n in range(40)]
    assert len(seen) > 1
    assert run_parallel(work, [1, 2, 3], workers=1) == [2, 4, 6]


def test_run_parallel_raises_the_first_error():
    def work(n):
        if n == 3:
            raise ValueError("boom")
        return n

    with pytest.raises(ValueError, match="boom"):
        run_parallel(work, list(range(10)), workers=4)


def test_effective_k_grows_only_for_large_projects():
    assert effective_k(8, 40) == 8
    assert effective_k(8, 60) == 8
    assert 8 < effective_k(8, 100) < 30
    assert effective_k(8, 5000) == 30
    assert effective_k(40, 5000) == 40


class Counting(MockProvider):
    """Counts calls by task. A batch the mock splits locally counts once, as a real model would."""

    def __init__(self):
        super().__init__()
        self.tasks: dict[str, int] = {}
        self.guard = threading.Lock()
        self.inside = threading.local()

    def complete(self, system, user, temperature=0.0, json_schema=None):
        if getattr(self.inside, "batch", False):
            return super().complete(system, user, temperature, json_schema)
        name = system.split("\n", 1)[0]
        with self.guard:
            self.tasks[name] = self.tasks.get(name, 0) + 1
        if "_BATCH" in name:
            self.inside.batch = True
            try:
                return super().complete(system, user, temperature, json_schema)
            finally:
                self.inside.batch = False
        return super().complete(system, user, temperature, json_schema)


def test_adaptive_sampling_stops_after_two_agreeing_samples(demo):
    _, project = demo
    artifact = next(a for a in project.artifacts if a.id == "TASK_02")
    provider = Counting()
    adaptive = Settings(n=5, adaptive_min_samples=2)
    estimate_uncertainty(project.store, "email_auth", artifact, END, provider, adaptive)
    assert provider.tasks["TASK:CONSISTENCY"] == 2
    provider = Counting()
    full = Settings(n=5, adaptive_min_samples=0)
    estimate_uncertainty(project.store, "email_auth", artifact, END, provider, full)
    assert provider.tasks["TASK:CONSISTENCY"] == 5


class FlipFirst(MockProvider):
    """First sample says CONSISTENT, later samples say INCONSISTENT."""

    def __init__(self):
        super().__init__()
        self.count = 0
        self.guard = threading.Lock()

    def _consistency(self, user, temperature):
        with self.guard:
            self.count += 1
            label = "CONSISTENT" if self.count == 1 else "INCONSISTENT"
        return json.dumps({"label": label, "evidence": "e", "expected_action": "a"})


def test_adaptive_sampling_keeps_going_when_early_samples_disagree(demo):
    _, project = demo
    artifact = next(a for a in project.artifacts if a.id == "TASK_02")
    provider = FlipFirst()
    result = estimate_uncertainty(
        project.store, "email_auth", artifact, END, provider, Settings(n=5, adaptive_min_samples=2)
    )
    assert provider.count == 5
    assert result.label == ConsistencyLabel.INCONSISTENT and result.confidence == 0.8


def _prepared(project, ids, topic="email_auth"):
    by_id = {a.id: a for a in project.artifacts}
    return [prepare_check(project.store, topic, by_id[i], END) for i in ids]


def test_identical_artifacts_are_judged_once_and_keep_their_own_ids(demo):
    _, project = demo
    base = next(a for a in project.artifacts if a.id == "TASK_02")
    twin = base.model_copy(update={"id": "TASK_02_TWIN"})
    prepared = [
        prepare_check(project.store, "email_auth", base, END),
        prepare_check(project.store, "email_auth", twin, END),
    ]
    provider = Counting()
    settings = Settings(n=5, adaptive_min_samples=2, judge_batch=1)
    first, second = estimate_many(prepared, provider, settings)
    assert provider.tasks["TASK:CONSISTENCY"] == 2
    assert (first.artifact_id, second.artifact_id) == ("TASK_02", "TASK_02_TWIN")
    assert first.label == second.label


def test_batch_judging_uses_one_call_per_sample_and_matches_single_results(demo):
    _, project = demo
    ids = ["TASK_01", "TASK_02", "TASK_03", "TASK_04"]
    prepared = _prepared(project, ids, "email_auth")
    single = [
        estimate_uncertainty(project.store, "email_auth", p.artifact, END, MockProvider(),
                             Settings(n=5, adaptive_min_samples=2, judge_batch=1))
        for p in prepared
    ]
    provider = Counting()
    batched = estimate_many(prepared, provider, Settings(n=5, adaptive_min_samples=2, judge_batch=8))
    groups = len({p.group_key() for p in prepared if p.early is None})
    assert provider.tasks.get("TASK:CONSISTENCY_BATCH") == 2 * groups
    assert "TASK:CONSISTENCY" not in provider.tasks
    assert [r.label for r in batched] == [r.label for r in single]
    assert [r.artifact_id for r in batched] == ids


class DropsIndex(MockProvider):
    """Batch answers skip the second artifact, so it must be judged again on its own."""

    def complete(self, system, user, temperature=0.0, json_schema=None):
        raw = super().complete(system, user, temperature, json_schema)
        if "TASK:CONSISTENCY_BATCH" in system:
            data = json.loads(raw)
            data["results"] = [r for r in data["results"] if r["index"] != 2]
            return json.dumps(data, ensure_ascii=False)
        return raw


def test_artifacts_the_model_skipped_are_judged_one_by_one(demo):
    _, project = demo
    ids = ["TASK_01", "TASK_02", "TASK_03"]
    prepared = _prepared(project, ids)
    results = estimate_many(prepared, DropsIndex(), Settings(n=5, adaptive_min_samples=2, judge_batch=8))
    assert [r.artifact_id for r in results] == ids
    assert all(r.evidence for r in results)


class BrokenBatch(MockProvider):
    def complete(self, system, user, temperature=0.0, json_schema=None):
        if "TASK:CONSISTENCY_BATCH" in system:
            return "not json"
        return super().complete(system, user, temperature, json_schema)


def test_unparseable_batch_answers_fall_back_to_single_calls(demo):
    _, project = demo
    ids = ["TASK_01", "TASK_02", "TASK_03"]
    prepared = _prepared(project, ids)
    results = estimate_many(prepared, BrokenBatch(), Settings(n=5, adaptive_min_samples=2, judge_batch=8))
    assert [r.artifact_id for r in results] == ids


def test_budget_errors_are_not_swallowed_by_batch_fallback(demo):
    _, project = demo
    prepared = _prepared(project, ["TASK_01", "TASK_02", "TASK_03"])
    provider = CountingProvider(MockProvider(), budget=1)
    with pytest.raises(BudgetExceeded):
        estimate_many(prepared, provider, Settings(n=5, adaptive_min_samples=2, judge_batch=8))


def test_counting_provider_budget_holds_under_threads():
    class Quick(LLMProvider):
        def complete(self, system, user, temperature=0.0, json_schema=None):
            return "{}"

    provider = CountingProvider(Quick(), budget=100)
    outcomes = []

    def hit(_):
        try:
            provider.complete("s", "u")
            outcomes.append(True)
        except BudgetExceeded:
            outcomes.append(False)

    run_parallel(hit, list(range(400)), workers=16)
    assert outcomes.count(True) == 100 and provider.calls == 100


def item(date, text, doc="m.md"):
    return MeetingItem(date=date, text=text, source_doc=doc, location="1")


def test_extraction_judges_identical_items_once_and_batches_by_document():
    provider = Counting()
    items = [
        item("2026-10-01", "이메일 인증을 사용한다.", "meetings/a.md"),
        item("2026-10-01", "이메일 인증을 사용한다.", "emails/a.eml"),
        item("2026-10-01", "React를 사용한다.", "meetings/a.md"),
        item("2026-10-02", "논의가 길어졌다?", "meetings/a.md"),
    ]
    decisions = extract_decisions(items, provider)
    assert len(decisions) == 2
    batch_calls = provider.tasks.get("TASK:DECISION_EXTRACT_BATCH", 0)
    single_calls = provider.tasks.get("TASK:DECISION_EXTRACT", 0)
    assert (batch_calls, single_calls) == (1, 0)


def test_extraction_batch_off_makes_one_call_per_unique_item(monkeypatch):
    monkeypatch.setattr(DEFAULT_SETTINGS, "extract_batch", 1)
    provider = Counting()
    items = [item("2026-10-01", "이메일 인증을 사용한다."), item("2026-10-01", "React를 사용한다.")]
    extract_decisions(items, provider)
    assert provider.tasks["TASK:DECISION_EXTRACT"] == 2
    assert "TASK:DECISION_EXTRACT_BATCH" not in provider.tasks


def test_word_for_word_restatement_is_dropped_but_a_reversal_is_kept():
    items = [
        item("2026-10-01", "React를 사용한다."),
        item("2026-10-08", "React를 사용한다."),
        item("2026-10-09", "Vue로 변경한다."),
        item("2026-10-15", "React를 사용한다."),
    ]
    decisions = extract_decisions(items, MockProvider())
    texts = [(d.date, d.text) for d in sorted(decisions, key=lambda d: d.date)]
    assert texts == [
        ("2026-10-01", "React를 사용한다."),
        ("2026-10-09", "Vue로 변경한다."),
        ("2026-10-15", "React를 사용한다."),
    ]


def test_mock_sampling_variance_is_reproducible_across_threads():
    def sample(provider):
        user = "SAMPLE_VARIANCE\nDECISION=x\nARTIFACT=y\nARTIFACT_DATE=-\nDECISION_DUE=-\nEVIDENCE:\n"
        return [json.loads(provider.complete("TASK:CONSISTENCY\n", user, 0.7))["label"] for _ in range(20)]

    assert sample(MockProvider(seed=3)) == sample(MockProvider(seed=3))
