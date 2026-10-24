from __future__ import annotations

from decision_agent.models import (
    Certainty,
    Decision,
    DecisionRelation,
    DecisionScope,
    RelationType,
    ResolvedDecision,
    ScopeKind,
)
from decision_agent.scope import scope_matches, scope_specificity
from decision_agent.storage import SQLiteStore

OVERRIDING = {RelationType.SUPERSEDE, RelationType.CANCEL, RelationType.SCOPE_RESTRICT}


def _resolve_unknown_target(
    decisions: list[Decision],
    relations: list[DecisionRelation],
    by_id: dict[str, Decision],
) -> ResolvedDecision:
    """Resolve for an artifact whose scope is UNKNOWN.

    UNKNOWN is never treated as GLOBAL. The result is only certain when every decision of the
    topic is GLOBAL, because then no scope could change the outcome.
    """
    scoped_exists = any(d.scope.scope_kind != ScopeKind.GLOBAL for d in decisions)
    overridden = {
        r.old_id
        for r in relations
        if r.relation_type in OVERRIDING
        and r.affected_scope.scope_kind == ScopeKind.GLOBAL
        and r.old_id in by_id
        and r.new_id in by_id
    }
    remaining = [d for d in decisions if d.id not in overridden] or decisions
    chosen = max(remaining, key=lambda d: (d.date, d.id))
    if scoped_exists:
        reasons = [
            "Artifact scope가 UNKNOWN이고 범위가 지정된 결정이 있어 적용 결정을 확정할 수 없습니다."
        ]
        return ResolvedDecision(
            decision=chosen,
            certainty=Certainty.UNCERTAIN,
            reasons=reasons,
            evidence="; ".join(reasons),
            unknown_target=True,
        )
    return ResolvedDecision(decision=chosen, certainty=Certainty.CERTAIN)


def resolve_effective_decision(
    store: SQLiteStore,
    topic: str,
    target_scope: DecisionScope,
    as_of_date: str,
) -> ResolvedDecision:
    """Return the decision that governs target_scope on as_of_date, with a certainty flag."""
    decisions = [d for d in store.list_decisions(topic) if d.date <= as_of_date]
    relations = store.list_relations(topic)
    by_id = {d.id: d for d in decisions}
    if not decisions:
        return ResolvedDecision(
            decision=None,
            certainty=Certainty.UNCERTAIN,
            reasons=["해당 topic의 결정이 없습니다."],
            evidence="해당 topic의 결정이 없습니다.",
        )
    if target_scope.scope_kind == ScopeKind.UNKNOWN:
        return _resolve_unknown_target(decisions, relations, by_id)

    reasons: list[str] = []
    applicable: list[Decision] = []
    unknown_may_matter = False
    for d in decisions:
        match = scope_matches(d.scope, target_scope)
        if match is True:
            applicable.append(d)
        elif match is None:
            unknown_may_matter = True
    if not applicable:
        reasons.append("대상 범위에 적용되는 결정이 없습니다.")
        return ResolvedDecision(
            decision=None,
            certainty=Certainty.UNCERTAIN,
            reasons=reasons,
            evidence="; ".join(reasons),
        )

    overridden: set[str] = set()
    applied_rel: DecisionRelation | None = None
    effective_due_date: str | None = None
    for rel in relations:
        if rel.old_id not in by_id or rel.new_id not in by_id:
            continue
        applies = scope_matches(rel.affected_scope, target_scope)
        if applies is None:
            unknown_may_matter = True
            continue
        if not applies:
            continue
        if rel.relation_type in OVERRIDING:
            overridden.add(rel.old_id)
            applied_rel = rel
        elif rel.relation_type == RelationType.POSTPONE:
            applied_rel = rel
            new = by_id[rel.new_id]
            if new.due_date:
                effective_due_date = max(effective_due_date or new.due_date, new.due_date)

    remaining = [d for d in applicable if d.id not in overridden]
    if not remaining:
        # Every applicable decision was overridden; fall back to the overriding decisions.
        remaining = [
            by_id[r.new_id]
            for r in relations
            if r.new_id in by_id
            and r.old_id in overridden
            and scope_matches(by_id[r.new_id].scope, target_scope) is True
        ]
    remaining = sorted(
        remaining,
        key=lambda d: (scope_specificity(d.scope), d.date, d.id),
        reverse=True,
    )
    chosen = remaining[0] if remaining else None
    certainty = Certainty.CERTAIN
    if unknown_may_matter:
        certainty = Certainty.UNCERTAIN
        reasons.append("UNKNOWN scope 결정이 결과에 영향을 줄 수 있습니다.")
    if len(remaining) >= 2:
        first, second = remaining[0], remaining[1]
        same_rank = scope_specificity(first.scope) == scope_specificity(second.scope)
        if same_rank and first.date == second.date and first.text != second.text:
            certainty = Certainty.UNCERTAIN
            reasons.append("동일 우선순위의 충돌하는 결정이 있습니다.")
        more_specific_but_older = (
            scope_specificity(first.scope) > scope_specificity(second.scope)
            and first.date < second.date
        )
        if more_specific_but_older:
            linked = any({r.old_id, r.new_id} == {first.id, second.id} for r in relations)
            if not linked:
                certainty = Certainty.UNCERTAIN
                reasons.append("구체적인 과거 결정과 더 일반적인 최신 결정 사이 관계가 없습니다.")
    return ResolvedDecision(
        decision=chosen,
        applied_relation=applied_rel,
        effective_due_date=effective_due_date,
        certainty=certainty,
        reasons=reasons,
        evidence="; ".join(reasons),
    )
