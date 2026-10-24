from __future__ import annotations

import json

from decision_agent.consistency import check
from decision_agent.eval.dataset import make_artifact
from decision_agent.llm.base import LLMProvider
from decision_agent.models import (
    ArtifactKind,
    ConsistencyLabel,
    Decision,
    DecisionRelation,
    DecisionScope,
    RelationType,
    ScopeKind,
)
from decision_agent.storage import SQLiteStore


class RecordingProvider(LLMProvider):
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, system, user, temperature=0.0, json_schema=None):
        self.prompts.append(user)
        return json.dumps({"label": "CONSISTENT", "evidence": "e", "expected_action": "a"})


def build_store() -> SQLiteStore:
    store = SQLiteStore()
    unknown = DecisionScope(scope_kind=ScopeKind.UNKNOWN, unmapped_terms=["일부 고객사"])
    glob = DecisionScope(scope_kind=ScopeKind.GLOBAL)
    for decision_id, date, text, scope in (
        ("D01", "2026-09-07", "로그 보관 기간은 90일로 한다.", glob),
        ("D02", "2026-09-19", "일부 고객사 환경에서는 30일로 줄인다.", unknown),
    ):
        store.save_decision(
            Decision(id=decision_id, text=text, date=date, source_doc="m", topic="t", scope=scope)
        )
    store.save_relation(
        DecisionRelation(
            old_id="D01",
            new_id="D02",
            relation_type=RelationType.SCOPE_RESTRICT,
            affected_scope=unknown,
            evidence="e",
        )
    )
    return store


def test_uncertain_decision_scope_blocks_until_the_user_has_answered():
    store = build_store()
    artifact = make_artifact("REQ_01", ArtifactKind.REQUIREMENT, "로그는 90일간 보관한다.")
    provider = RecordingProvider()
    blocked = check(store, "t", artifact, "9999-12-31", provider)
    assert blocked.label == ConsistencyLabel.UNCERTAIN
    assert provider.prompts == []

    store.add_evidence("t", "30일은 A사 온프레미스에만 적용합니다.", "REQ_01")
    answered = check(store, "t", artifact, "9999-12-31", provider)
    assert answered.label == ConsistencyLabel.CONSISTENT
    assert len(provider.prompts) == 1
    assert "A사 온프레미스" in provider.prompts[0]
    assert "RESOLVER_NOTE" in provider.prompts[0]
