from __future__ import annotations

from dataclasses import dataclass, field

from decision_agent.eval.dataset import CaseSource, EvalCase, GoldArtifact, UserFact, global_scope
from decision_agent.models import (
    Artifact,
    Decision,
    DecisionRelation,
    DecisionScope,
    MeetingItem,
    RelationType,
)

DEFAULT_ALIASES = {
    "release": {
        "mvp": ["MVP", "이번 버전", "1차", "시연 버전", "데모 버전"],
        "production": ["Production", "정식 출시", "정식 버전", "본 출시", "프로덕션"],
    },
    "module": {"login": ["로그인"]},
    "user_group": {"admin": ["관리자"], "beta": ["베타 테스터"]},
    "environment": {},
}


@dataclass
class DecisionSpec:
    date: str
    text: str
    scope: DecisionScope = field(default_factory=global_scope)
    due_date: str | None = None
    reason: str | None = None


def relation(
    old: str,
    new: str,
    kind: RelationType,
    scope: DecisionScope | None = None,
    evidence: str = "gold",
) -> DecisionRelation:
    return DecisionRelation(
        old_id=old,
        new_id=new,
        relation_type=kind,
        affected_scope=scope or global_scope(),
        evidence=evidence,
    )


def build_case(
    *,
    case_id: str,
    level: int,
    source: CaseSource,
    author: str,
    description: str,
    topic: str,
    decisions: list[DecisionSpec],
    relations: list[DecisionRelation],
    artifacts: list[Artifact],
    gold: list[GoldArtifact],
    user_facts: list[UserFact] | None = None,
    tags: list[str] | None = None,
    current_release: str | None = "mvp",
    as_of_date: str = "9999-12-31",
) -> EvalCase:
    """Assemble a case. Decision ids are D01.. in meeting order, matching the extractor."""
    ordered = sorted(decisions, key=lambda s: s.date)
    meetings = [
        MeetingItem(date=s.date, text=s.text, source_doc="meeting.md", location=f"item {i}")
        for i, s in enumerate(ordered, start=1)
    ]
    gold_decisions = [
        Decision(
            id=f"D{i:02d}",
            text=s.text,
            date=s.date,
            source_doc="meeting.md",
            topic=topic,
            scope=s.scope,
            reason=s.reason,
            due_date=s.due_date,
        )
        for i, s in enumerate(ordered, start=1)
    ]
    return EvalCase(
        case_id=case_id,
        level=level,
        source=source,
        author=author,
        description=description,
        topic=topic,
        as_of_date=as_of_date,
        current_release=current_release,
        aliases=DEFAULT_ALIASES,
        meetings=meetings,
        artifacts=artifacts,
        gold_decisions=gold_decisions,
        gold_relations=relations,
        gold=gold,
        user_facts=user_facts or [],
        tags=tags or [],
    )
