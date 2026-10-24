from decision_agent.models import (
    Certainty,
    Decision,
    DecisionRelation,
    DecisionScope,
    RelationType,
    ScopeKind,
)
from decision_agent.relations import apply_relation_status
from decision_agent.resolver import resolve_effective_decision
from decision_agent.storage import SQLiteStore

GLOBAL = DecisionScope(scope_kind=ScopeKind.GLOBAL)
MVP = DecisionScope(scope_kind=ScopeKind.SCOPED, release="mvp")
PROD = DecisionScope(scope_kind=ScopeKind.SCOPED, release="production")
UNKNOWN = DecisionScope(scope_kind=ScopeKind.UNKNOWN)


def decision(decision_id, text, date, scope, topic="email_auth"):
    return Decision(
        id=decision_id, text=text, date=date, source_doc="m.md", topic=topic, scope=scope
    )


def relate(store, old, new, relation_type, scope):
    rel = DecisionRelation(
        old_id=old, new_id=new, relation_type=relation_type, affected_scope=scope, evidence="x"
    )
    store.save_relation(rel)
    apply_relation_status(store, rel)


def restricted_store():
    store = SQLiteStore()
    store.save_decision(decision("D01", "이메일 인증 적용", "2026-09-01", GLOBAL))
    store.save_decision(decision("D02", "MVP에서는 이메일 인증 제외", "2026-09-10", MVP))
    relate(store, "D01", "D02", RelationType.SCOPE_RESTRICT, MVP)
    return store


def test_scope_restrict_keeps_old_active_and_resolves_by_scope():
    store = restricted_store()
    assert store.get_decision("D01").status.value == "ACTIVE"
    assert resolve_effective_decision(store, "email_auth", MVP, "2026-12-01").decision.id == "D02"
    assert resolve_effective_decision(store, "email_auth", PROD, "2026-12-01").decision.id == "D01"


def test_global_supersede_and_cancel_update_status():
    store = SQLiteStore()
    store.save_decision(decision("D01", "React 사용", "2026-09-01", GLOBAL, "fw"))
    store.save_decision(decision("D02", "Vue 사용", "2026-09-02", GLOBAL, "fw"))
    store.save_decision(decision("D03", "결제 개발", "2026-09-01", GLOBAL, "pay"))
    store.save_decision(decision("D04", "결제 취소", "2026-09-02", GLOBAL, "pay"))
    relate(store, "D01", "D02", RelationType.SUPERSEDE, GLOBAL)
    relate(store, "D03", "D04", RelationType.CANCEL, GLOBAL)
    assert store.get_decision("D01").status.value == "SUPERSEDED"
    assert store.get_decision("D03").status.value == "CANCELLED"


def test_scoped_relations_and_unknown_do_not_change_status():
    store = SQLiteStore()
    for i, text in enumerate(["a", "b", "c", "d"], start=1):
        store.save_decision(decision(f"D0{i}", text, f"2026-09-0{i}", GLOBAL))
    relate(store, "D01", "D02", RelationType.SUPERSEDE, MVP)
    relate(store, "D01", "D03", RelationType.CANCEL, MVP)
    relate(store, "D01", "D04", RelationType.SUPERSEDE, UNKNOWN)
    assert store.get_decision("D01").status.value == "ACTIVE"


def test_postpone_keeps_active_and_sets_effective_due_date():
    store = SQLiteStore()
    store.save_decision(decision("D01", "10월 20일 출시", "2026-09-01", GLOBAL, "release"))
    later = decision("D02", "11월 10일 출시", "2026-09-02", GLOBAL, "release")
    later.due_date = "2026-11-10"
    store.save_decision(later)
    relate(store, "D01", "D02", RelationType.POSTPONE, GLOBAL)
    assert store.get_decision("D01").status.value == "ACTIVE"
    resolved = resolve_effective_decision(store, "release", GLOBAL, "2026-12-01")
    assert resolved.effective_due_date == "2026-11-10"


def test_unknown_decision_scope_is_not_global():
    store = SQLiteStore()
    store.save_decision(decision("D01", "이번 버전에서는 제외", "2026-09-10", UNKNOWN))
    resolved = resolve_effective_decision(store, "email_auth", MVP, "2026-12-01")
    assert resolved.decision is None
    assert resolved.certainty == Certainty.UNCERTAIN


def test_unknown_artifact_scope_is_uncertain_only_when_scoped_decisions_exist():
    scoped = resolve_effective_decision(restricted_store(), "email_auth", UNKNOWN, "2026-12-01")
    assert scoped.certainty == Certainty.UNCERTAIN
    assert scoped.unknown_target is True

    only_global = SQLiteStore()
    only_global.save_decision(decision("D01", "Vue 사용", "2026-09-01", GLOBAL, "fw"))
    resolved = resolve_effective_decision(only_global, "fw", UNKNOWN, "2026-12-01")
    assert resolved.certainty == Certainty.CERTAIN
    assert resolved.decision.id == "D01"


def test_same_rank_conflict_is_uncertain():
    store = SQLiteStore()
    store.save_decision(decision("D01", "A안", "2026-09-01", MVP))
    store.save_decision(decision("D02", "B안", "2026-09-01", MVP))
    resolved = resolve_effective_decision(store, "email_auth", MVP, "2026-12-01")
    assert resolved.certainty == Certainty.UNCERTAIN


def test_as_of_date_ignores_later_decisions():
    store = restricted_store()
    resolved = resolve_effective_decision(store, "email_auth", MVP, "2026-09-05")
    assert resolved.decision.id == "D01"
