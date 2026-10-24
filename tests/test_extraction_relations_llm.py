import json

from decision_agent.extraction import extract_decisions
from decision_agent.llm.base import LLMProvider, extract_json_text
from decision_agent.models import Decision, DecisionScope, MeetingItem, ProjectConfig, ScopeKind
from decision_agent.relations import classify_relation, relation_candidates

ALIASES = {
    "release": {"mvp": ["MVP", "이번 버전"], "production": ["정식 버전"]},
    "module": {},
    "user_group": {},
    "environment": {},
}


class FixedProvider(LLMProvider):
    def __init__(self, payload):
        self.payload = payload

    def complete(self, system, user, temperature=0.0, json_schema=None):
        return "```json\n" + json.dumps(self.payload, ensure_ascii=False) + "\n```"


def item(text="x"):
    return MeetingItem(date="2026-09-10", text=text, source_doc="m.md", location="1")


def payload(scope):
    return {"is_decision": True, "text": "t", "topic": "email_auth", "scope": scope}


def scope_dict(kind, release=None):
    return {"scope_kind": kind, "release": release}


def test_extract_json_text_strips_fences_and_prose():
    assert extract_json_text('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json_text('답변입니다 {"a": 1} 끝') == '{"a": 1}'


def test_extraction_normalizes_alias_to_canonical_value():
    provider = FixedProvider(payload(scope_dict("SCOPED", "정식 버전")))
    [d] = extract_decisions([item()], provider, ProjectConfig(current_release="mvp"), ALIASES)
    assert d.scope.scope_kind == ScopeKind.SCOPED
    assert d.scope.release == "production"
    assert d.date == "2026-09-10"


def test_extraction_unmapped_scope_becomes_unknown():
    provider = FixedProvider(payload(scope_dict("SCOPED", "알파")))
    [d] = extract_decisions([item()], provider, ProjectConfig(), ALIASES)
    assert d.scope.scope_kind == ScopeKind.UNKNOWN
    assert d.scope.unmapped_terms == ["알파"]


def test_extraction_scoped_without_values_becomes_unknown_not_global():
    provider = FixedProvider(payload(scope_dict("SCOPED")))
    [d] = extract_decisions([item()], provider, ProjectConfig(), ALIASES)
    assert d.scope.scope_kind == ScopeKind.UNKNOWN


def test_non_decision_items_are_dropped():
    provider = FixedProvider({**payload(scope_dict("GLOBAL")), "is_decision": False})
    assert extract_decisions([item()], provider) == []


def test_scope_restrict_with_global_scope_is_downgraded_to_unknown():
    provider = FixedProvider(
        {
            "relation_type": "SCOPE_RESTRICT",
            "affected_scope": scope_dict("GLOBAL"),
            "evidence": "x",
        }
    )
    glob = DecisionScope(scope_kind=ScopeKind.GLOBAL)
    old = Decision(id="D1", text="a", date="2026-09-01", source_doc="m", topic="t", scope=glob)
    new = Decision(id="D2", text="b", date="2026-09-02", source_doc="m", topic="t", scope=glob)
    rel = classify_relation(old, new, provider)
    assert rel.affected_scope.scope_kind == ScopeKind.UNKNOWN


def test_relation_candidates_prefer_same_topic():
    glob = DecisionScope(scope_kind=ScopeKind.GLOBAL)
    decisions = [
        Decision(
            id=f"D{i}", text=f"결정 {i}", date=f"2026-09-0{i}", source_doc="m", topic=t, scope=glob
        )
        for i, t in enumerate(["a", "b", "a"], start=1)
    ]
    candidates = relation_candidates(decisions, decisions[2])
    assert [d.id for d in candidates] == ["D1"]
