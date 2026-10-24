from __future__ import annotations

from pathlib import Path

import pytest

from decision_agent.config import DEFAULT_SETTINGS
from decision_agent.eval.dataset import load_data_dir
from decision_agent.eval.methods import (
    ALL_METHODS,
    CountingProvider,
    build_store,
    prepare,
    run_method,
)
from decision_agent.eval.simulator import UserSimulator
from decision_agent.llm.mock_provider import MockProvider
from decision_agent.models import ConsistencyLabel, ScopeKind

DATA = Path(__file__).resolve().parents[1] / "data" / "eval"


@pytest.fixture(scope="module")
def synthetic():
    return {c.case_id: c for c in load_data_dir(DATA, ["synthetic"])}


def test_every_method_returns_a_label_for_every_artifact(synthetic):
    case = synthetic["syn_l3_01"]
    prepared = prepare(case, MockProvider(), "none")
    for method in ALL_METHODS:
        run = run_method(method, case, prepared, MockProvider(), DEFAULT_SETTINGS)
        assert set(run.predictions) == {a.id for a in case.artifacts}, method
        assert run.flagged <= set(run.predictions)


def test_mock_ingest_reproduces_gold_on_demo_vocabulary(synthetic):
    prepared = prepare(synthetic["syn_l5_01"], MockProvider(), "none")
    assert prepared.extraction_f1 == 1.0
    assert prepared.relation_f1 == 1.0
    assert prepared.llm_calls > 0


def test_oracle_stages_use_gold_and_skip_ingest_calls(synthetic):
    case = synthetic["syn_l5_01"]
    full = prepare(case, MockProvider(), "full")
    assert full.llm_calls == 0
    assert [d.id for d in full.decisions] == [d.id for d in case.gold_decisions]
    assert len(full.relations) == len(case.gold_relations)
    decisions_only = prepare(case, MockProvider(), "decisions")
    assert decisions_only.relation_f1 is not None
    assert decisions_only.extraction_f1 is None


def test_scope_agnostic_store_has_no_scoped_items(synthetic):
    case = synthetic["syn_l3_01"]
    prepared = prepare(case, MockProvider(), "none")
    store = build_store(case, prepared, scope_agnostic=True)
    assert all(d.scope.scope_kind == ScopeKind.GLOBAL for d in store.list_decisions())
    assert all(a.scope.scope_kind == ScopeKind.GLOBAL for a in store.list_artifacts())
    assert all(r.affected_scope.scope_kind == ScopeKind.GLOBAL for r in store.list_relations())
    normal = build_store(case, prepared)
    assert any(a.scope.scope_kind == ScopeKind.SCOPED for a in normal.list_artifacts())


def test_scope_decoys_are_not_flagged_by_scope_aware_methods(synthetic):
    case = synthetic["syn_l3_01"]
    prepared = prepare(case, MockProvider(), "none")
    run = run_method("M1c", case, prepared, MockProvider(), DEFAULT_SETTINGS)
    gold = case.gold_by_id()
    for artifact_id, label in run.predictions.items():
        if gold[artifact_id].scope_trap:
            assert label != ConsistencyLabel.INCONSISTENT


def test_clarification_resolves_uncertainty_only_when_allowed(synthetic):
    case = synthetic["syn_l6_01"]
    prepared = prepare(case, MockProvider(), "none")
    without = run_method("M1", case, prepared, MockProvider(), DEFAULT_SETTINGS)
    with_q = run_method("M1c", case, prepared, MockProvider(), DEFAULT_SETTINGS)
    assert without.questions == 0
    assert with_q.questions == 1
    assert ConsistencyLabel.UNCERTAIN in without.predictions.values()
    gold = case.gold_by_id()
    assert all(with_q.predictions[i].value == g.label for i, g in gold.items() if g.affected)


def test_agent_reads_history_for_a_vague_decision_and_pipeline_asks(synthetic):
    case = synthetic["syn_l5_01"]
    prepared = prepare(case, MockProvider(), "none")
    agent = run_method("M1", case, prepared, MockProvider(), DEFAULT_SETTINGS)
    pipeline = run_method("M2", case, prepared, MockProvider(), DEFAULT_SETTINGS)
    assert "get_decision_history" in agent.steps
    assert agent.questions == 0
    assert pipeline.questions == 1


def test_unknown_method_name_is_rejected(synthetic):
    case = synthetic["syn_l1_01"]
    prepared = prepare(case, MockProvider(), "none")
    with pytest.raises(ValueError):
        run_method("nope", case, prepared, MockProvider(), DEFAULT_SETTINGS)


def test_simulator_matches_keywords_and_falls_back(synthetic):
    case = synthetic["syn_l6_01"]
    sim = UserSimulator(case)
    assert "범위" in sim.ask("이번 변경은 표시된 범위에만 적용되나요?")
    assert sim.ask("전혀 다른 질문") == case.default_answer
    assert sim.questions == 2
    assert sim.informative_answers == 1


def test_counting_provider_counts_calls():
    counter = CountingProvider(MockProvider())
    counter.complete("TASK:GUARD", "TASK=x\nEFFECTIVE_DECISION=y")
    counter.complete("TASK:GUARD", "TASK=x\nEFFECTIVE_DECISION=y")
    assert counter.calls == 2
