import json

from decision_agent.consistency import check
from decision_agent.impact import (
    find_candidate_artifacts,
    predict_affected,
    predict_affected_for_topic,
)
from decision_agent.llm.base import LLMProvider
from decision_agent.llm.mock_provider import MockProvider
from decision_agent.models import ConsistencyLabel


class SpyProvider(MockProvider):
    def __init__(self):
        super().__init__()
        self.consistency_prompts = []

    def _consistency(self, user, temperature):
        self.consistency_prompts.append(user)
        return super()._consistency(user, temperature)


class InventingProvider(LLMProvider):
    """Tries to return an id that is not among the candidates."""

    def complete(self, system, user, temperature=0.0, json_schema=None):
        return json.dumps({"artifact_ids": ["TASK_01", "NOT_A_CANDIDATE"]})


def artifact(project, artifact_id):
    return next(a for a in project.artifacts if a.id == artifact_id)


def test_candidates_are_ranked_and_scope_filtered(demo):
    _, project = demo
    decision = project.store.get_decision("D03")
    candidates = find_candidate_artifacts(decision, project.artifacts, 8)
    ids = {a.id for a in candidates}
    assert "TASK_01" in ids
    # A production-scoped artifact cannot be affected by an MVP-scoped decision.
    assert "TASK_02" not in ids


def test_llm_cannot_add_ids_outside_candidates(demo):
    _, project = demo
    decision = project.store.get_decision("D03")
    out = predict_affected(decision, None, project.artifacts, InventingProvider())
    assert [a.id for a in out] == ["TASK_01"]


def test_topic_prediction_uses_whole_history(demo):
    provider, project = demo
    affected = predict_affected_for_topic(
        project.store, "frontend_framework", project.artifacts, provider, "2026-12-31"
    )
    assert {a.id for a in affected} == {"REQUIREMENT_3", "TASK_03", "TASK_04"}


def test_scope_aware_consistency(demo):
    provider, project = demo
    mvp = check(project.store, "email_auth", artifact(project, "TASK_01"), "2026-12-31", provider)
    prod = check(project.store, "email_auth", artifact(project, "TASK_02"), "2026-12-31", provider)
    assert mvp.label == ConsistencyLabel.INCONSISTENT
    assert prod.label == ConsistencyLabel.CONSISTENT
    assert mvp.effective_decision_id == "D03"
    assert prod.effective_decision_id == "D01"


def test_extra_evidence_reaches_the_prompt(demo):
    _, project = demo
    spy = SpyProvider()
    check(
        project.store,
        "presentation_schedule",
        artifact(project, "SCHED_03"),
        "2026-12-31",
        spy,
        extra_evidence=["세 일정 모두 다음 주로 변경"],
    )
    assert "세 일정 모두 다음 주로 변경" in spy.consistency_prompts[0]
    assert "DECISION_DUE=2026-09-21" in spy.consistency_prompts[0]
