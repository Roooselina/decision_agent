from __future__ import annotations

import json
import threading
import time

import desktop_helpers as dh
import pytest

from decision_agent.config import DEFAULT_SETTINGS
from decision_agent.desktop.engine import ProjectEngine
from decision_agent.desktop.providers import BudgetExceeded, CountingProvider, ProviderBundle
from decision_agent.desktop.state import StateDB
from decision_agent.extraction import extract_decisions, pack_documents
from decision_agent.llm.base import LLMProvider
from decision_agent.llm.mock_provider import MockProvider
from decision_agent.models import (
    Decision,
    DecisionScope,
    MeetingItem,
    RelationType,
    ScopeKind,
)
from decision_agent.parallel import run_parallel
from decision_agent.relations import RELATION_BATCH_SYSTEM, classify_and_store, classify_chunk
from decision_agent.storage import SQLiteStore

GLOBAL = DecisionScope(scope_kind=ScopeKind.GLOBAL)


class Tasks(MockProvider):
    """Counts model calls by task name. A batch the mock splits locally counts once."""

    def __init__(self):
        super().__init__()
        self.tasks: dict[str, int] = {}
        self.guard = threading.Lock()
        self.inside = threading.local()

    def complete(self, system, user, temperature=0.0, json_schema=None):
        if getattr(self.inside, "batch", False):
            return super().complete(system, user, temperature, json_schema)
        name = system.split("\n", 1)[0].removeprefix("TASK:")
        with self.guard:
            self.tasks[name] = self.tasks.get(name, 0) + 1
        if name.endswith("_BATCH"):
            self.inside.batch = True
            try:
                return super().complete(system, user, temperature, json_schema)
            finally:
                self.inside.batch = False
        return super().complete(system, user, temperature, json_schema)


def item(doc, number, date="2026-09-01"):
    return MeetingItem(
        date=date, text=f"{doc} 항목 {number} 이메일 인증을 제외한다", source_doc=doc, location=f"L{number}"
    )


# extraction packing


def test_small_documents_share_a_group_and_stay_whole():
    docs = [[item(f"d{n}.md", i) for i in range(3)] for n in range(5)]
    groups = pack_documents(docs, 8)
    assert [len(g) for g in groups] == [6, 6, 3]
    for group in groups:
        sources = [i.source_doc for i in group]
        assert sources == sorted(sources)
    flat = [i for g in groups for i in g]
    assert flat == [i for d in docs for i in d]


def test_a_large_document_is_split_and_the_remainder_is_shared():
    docs = [[item("big.md", i) for i in range(11)], [item("small.md", 0)]]
    groups = pack_documents(docs, 8)
    assert [len(g) for g in groups] == [8, 4]
    assert groups[1][-1].source_doc == "small.md"


def test_changing_the_last_document_keeps_earlier_groups_identical():
    docs = [[item(f"d{n}.md", i) for i in range(4)] for n in range(6)]
    before = pack_documents(docs, 8)
    docs[-1] = [item("d5.md", i, date="2026-10-01") for i in range(4)]
    after = pack_documents(docs, 8)
    assert before[:-1] == after[:-1] and before[-1] != after[-1]


def test_extraction_uses_far_fewer_calls_for_many_small_documents():
    items = [item(f"d{n}.md", i, date=f"2026-09-{n + 1:02d}") for n in range(10) for i in range(2)]
    provider = Tasks()
    extract_decisions(items, provider)
    assert provider.tasks["DECISION_EXTRACT_BATCH"] == 3  # 20 items in groups of 8
    assert "DECISION_EXTRACT" not in provider.tasks


# relation batching


def decisions_of_one_topic(count):
    return [
        Decision(
            id=f"D{n:02d}", text=f"로그인은 방식 {n}로 변경한다", date=f"2026-09-{n + 1:02d}",
            source_doc="m.md", topic="auth", scope=GLOBAL,
        )
        for n in range(count)
    ]


def test_relations_use_one_call_per_group_and_match_single_calls():
    decisions = decisions_of_one_topic(12)
    batched, single = Tasks(), Tasks()
    store_a, store_b = SQLiteStore(), SQLiteStore()
    for d in decisions:
        store_a.save_decision(d)
        store_b.save_decision(d)
    old = DEFAULT_SETTINGS.relation_batch
    try:
        DEFAULT_SETTINGS.relation_batch = 8
        rels_batched = classify_and_store(decisions, batched, store_a)
        DEFAULT_SETTINGS.relation_batch = 1
        rels_single = classify_and_store(decisions, single, store_b)
    finally:
        DEFAULT_SETTINGS.relation_batch = old
    assert single.tasks["RELATION_CLASSIFY"] > 10 and "RELATION_CLASSIFY_BATCH" not in single.tasks
    assert batched.tasks["RELATION_CLASSIFY_BATCH"] == -(-single.tasks["RELATION_CLASSIFY"] // 8)
    assert [(r.old_id, r.new_id, r.relation_type) for r in rels_batched] == [
        (r.old_id, r.new_id, r.relation_type) for r in rels_single
    ]
    assert all(r.relation_type == RelationType.SUPERSEDE for r in rels_batched)


class BadRelationBatch(Tasks):
    def complete(self, system, user, temperature=0.0, json_schema=None):
        if "TASK:RELATION_CLASSIFY_BATCH" in system:
            with self.guard:
                self.tasks["RELATION_CLASSIFY_BATCH"] = self.tasks.get("RELATION_CLASSIFY_BATCH", 0) + 1
            return "not json"
        return super().complete(system, user, temperature, json_schema)


def test_unusable_relation_batch_falls_back_to_single_calls():
    decisions = decisions_of_one_topic(4)
    pairs = [(decisions[0], decisions[1]), (decisions[1], decisions[2]), (decisions[2], decisions[3])]
    provider = BadRelationBatch()
    rels = classify_chunk(pairs, provider)
    assert len(rels) == 3 and provider.tasks["RELATION_CLASSIFY"] == 3


class SkipsSecond(Tasks):
    def complete(self, system, user, temperature=0.0, json_schema=None):
        raw = super().complete(system, user, temperature, json_schema)
        if "TASK:RELATION_CLASSIFY_BATCH" in system:
            data = json.loads(raw)
            data["results"] = [r for r in data["results"] if r["index"] != 2]
            return json.dumps(data)
        return raw


def test_relation_the_model_skipped_is_classified_singly():
    decisions = decisions_of_one_topic(4)
    pairs = [(decisions[0], decisions[1]), (decisions[1], decisions[2]), (decisions[2], decisions[3])]
    provider = SkipsSecond()
    rels = classify_chunk(pairs, provider)
    assert [(r.old_id, r.new_id) for r in rels] == [("D00", "D01"), ("D01", "D02"), ("D02", "D03")]
    assert provider.tasks["RELATION_CLASSIFY"] == 1


def test_relation_batch_prompt_keeps_the_rules_of_the_single_prompt():
    assert RELATION_BATCH_SYSTEM.startswith("TASK:RELATION_CLASSIFY_BATCH\n")
    assert "SUPERSEDE" in RELATION_BATCH_SYSTEM and "PAIRS" in RELATION_BATCH_SYSTEM


class OutOfBudget(Tasks):
    def complete(self, system, user, temperature=0.0, json_schema=None):
        raise BudgetExceeded("limit")


def test_budget_errors_are_not_swallowed_by_the_batch_fallback():
    decisions = decisions_of_one_topic(3)
    pairs = [(decisions[0], decisions[1]), (decisions[1], decisions[2])]
    with pytest.raises(BudgetExceeded):
        classify_chunk(pairs, OutOfBudget())


# store, semaphore, topic parallelism


def test_store_survives_many_threads_writing_and_reading():
    store = SQLiteStore()
    errors = []

    def work(n):
        try:
            for i in range(30):
                store.add_evidence(f"t{n}", f"e{i}")
                store.list_evidence(f"t{n}")
                store.clear_evidence(f"t{n}") if i % 10 == 9 else None
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    run_parallel(work, list(range(8)), workers=8)
    assert errors == []


class Slow(LLMProvider):
    def __init__(self):
        self.active = 0
        self.peak = 0
        self.guard = threading.Lock()

    def complete(self, system, user, temperature=0.0, json_schema=None):
        with self.guard:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.02)
        with self.guard:
            self.active -= 1
        return "{}"


def test_call_semaphore_caps_requests_in_flight():
    slow = Slow()
    counter = CountingProvider(slow, budget=1000, max_parallel=3)
    run_parallel(lambda n: counter.complete("TASK:CONSISTENCY\nx", str(n)), list(range(30)), workers=12)
    assert slow.peak <= 3 and counter.calls == 30
    unlimited = Slow()
    free = CountingProvider(unlimited, budget=1000)
    run_parallel(lambda n: free.complete("TASK:CONSISTENCY\nx", str(n)), list(range(30)), workers=12)
    assert unlimited.peak > 3


def external_engine(root, **overrides):
    settings = dh.make_settings(root, confirm_over_calls=0, **overrides)
    bundle = ProviderBundle(MockProvider(), "fake:external", False, "test", True)
    db = StateDB(":memory:")
    return ProjectEngine(root, settings, db, bundle), db


@pytest.mark.parametrize("profile", ["precise", "balanced"])
def test_parallel_topics_give_the_same_findings_as_one_at_a_time(tmp_path, profile):
    results = {}
    for workers in (1, 4):
        root = dh.make_watch_dir(tmp_path, f"w{workers}")
        engine, db = external_engine(root, cost_profile=profile, topic_workers=workers)
        engine.sync()
        report = engine.analyze()
        assert report.error is None
        results[workers] = (
            sorted(report.topics_checked),
            {(r["topic"], r["artifact_id"]): r["label"] for r in db.list_findings(include_hidden=True)},
            sorted(q["topic"] for q in db.list_questions()),
        )
    assert results[1] == results[4] and results[1][1]


def test_budget_stop_is_reported_once_with_parallel_topics(tmp_path):
    root = dh.make_watch_dir(tmp_path, "full")
    engine, _ = external_engine(root, cost_profile="precise", topic_workers=4)
    engine.sync()
    full = engine.analyze()
    ingest = sum(
        n for task, n in full.calls_by_task.items() if task.startswith(("DECISION_EXTRACT", "RELATION"))
    )
    root2 = dh.make_watch_dir(tmp_path, "tight")
    tight, _db = external_engine(
        root2, cost_profile="precise", topic_workers=4, max_llm_calls_per_cycle=ingest + 2
    )
    tight.sync()
    report = tight.analyze()
    assert report.needs_followup and "한도" in (report.error or "")
    assert tight.completion() is None
    assert any(o.state == "deferred" for o in report.outcomes)
    assert sum(1 for o in report.outcomes if o.state == "deferred") == 1


def test_one_failing_topic_does_not_stop_the_others(tmp_path):
    class FailsForOneTopic(MockProvider):
        def complete(self, system, user, temperature=0.0, json_schema=None):
            if "TASK:IMPACT_SELECT" in system and "React" in user:
                raise RuntimeError("temporary failure")
            return super().complete(system, user, temperature, json_schema)

    root = dh.make_watch_dir(tmp_path)
    settings = dh.make_settings(root, confirm_over_calls=0, topic_workers=4)
    bundle = ProviderBundle(FailsForOneTopic(), "fake:external", False, "t", True)
    engine = ProjectEngine(root, settings, StateDB(":memory:"), bundle)
    engine.sync()
    report = engine.analyze()
    states = {o.topic: o.state for o in report.outcomes}
    assert states["frontend_framework"] == "error"
    assert states["email_auth"] == "ok"
    assert report.needs_followup and not report.fatal
