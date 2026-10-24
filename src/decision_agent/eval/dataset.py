from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from decision_agent.models import (
    Artifact,
    ArtifactKind,
    Decision,
    DecisionRelation,
    DecisionScope,
    MeetingItem,
    RelationType,
    ScopeKind,
)

CaseSource = Literal["synthetic", "assistant_written", "human_written", "real"]
GoldLabel = Literal["CONSISTENT", "INCONSISTENT"]

LEVEL_NAMES = {
    1: "single decision, propagation check",
    2: "global supersede or cancel",
    3: "scope restriction (scoped exclusion with decoy artifacts)",
    4: "postponement of schedule artifacts",
    5: "vague reference that needs decision history",
    6: "UNKNOWN scope that needs clarification",
    7: "combined: scope restriction, chained decisions, clarification",
}


class GoldArtifact(BaseModel):
    """Gold judgement for one artifact.

    affected: the artifact is on the decision topic and should be inspected.
    label: the true state after any needed clarification is answered.
    needs_clarification: the label cannot be derived from the documents alone.
    scope_trap: a decoy that a scope-agnostic system would wrongly flag.
    """

    artifact_id: str
    label: GoldLabel
    affected: bool = True
    needs_clarification: bool = False
    scope_trap: bool = False
    note: str = ""


class UserFact(BaseModel):
    """A scripted answer the simulated user gives when a question contains a keyword."""

    keywords: list[str]
    answer: str


class EvalCase(BaseModel):
    case_id: str
    level: int = Field(ge=1, le=7)
    source: CaseSource
    author: str
    description: str
    topic: str
    as_of_date: str = "9999-12-31"
    current_release: str | None = None
    aliases: dict[str, Any] = Field(default_factory=dict)
    meetings: list[MeetingItem]
    artifacts: list[Artifact]
    gold_decisions: list[Decision]
    gold_relations: list[DecisionRelation] = Field(default_factory=list)
    gold: list[GoldArtifact]
    user_facts: list[UserFact] = Field(default_factory=list)
    default_answer: str = "잘 모르겠습니다."
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_consistency(self) -> EvalCase:
        artifact_ids = [a.id for a in self.artifacts]
        if len(set(artifact_ids)) != len(artifact_ids):
            raise ValueError(f"{self.case_id}: duplicate artifact ids")
        gold_ids = [g.artifact_id for g in self.gold]
        if sorted(gold_ids) != sorted(artifact_ids):
            raise ValueError(f"{self.case_id}: gold must cover every artifact exactly once")
        decision_ids = {d.id for d in self.gold_decisions}
        for rel in self.gold_relations:
            if rel.old_id not in decision_ids or rel.new_id not in decision_ids:
                raise ValueError(f"{self.case_id}: relation refers to an unknown decision")
            if (
                rel.relation_type == RelationType.SCOPE_RESTRICT
                and rel.affected_scope.scope_kind == ScopeKind.GLOBAL
            ):
                raise ValueError(f"{self.case_id}: SCOPE_RESTRICT cannot have GLOBAL scope")
        if not any(d.topic == self.topic for d in self.gold_decisions):
            raise ValueError(f"{self.case_id}: no gold decision for topic {self.topic}")
        for g in self.gold:
            if g.needs_clarification and not self.user_facts:
                raise ValueError(f"{self.case_id}: needs_clarification without user_facts")
        return self

    def gold_by_id(self) -> dict[str, GoldArtifact]:
        return {g.artifact_id: g for g in self.gold}

    def needs_history(self) -> bool:
        return "vague_reference" in self.tags

    def needs_scope(self) -> bool:
        return any(r.affected_scope.scope_kind == ScopeKind.SCOPED for r in self.gold_relations)

    def needs_clarify(self) -> bool:
        return any(g.needs_clarification for g in self.gold)


def global_scope() -> DecisionScope:
    return DecisionScope(scope_kind=ScopeKind.GLOBAL)


def scoped(**dims: str) -> DecisionScope:
    return DecisionScope(scope_kind=ScopeKind.SCOPED, **dims)


def unknown_scope(*terms: str) -> DecisionScope:
    return DecisionScope(scope_kind=ScopeKind.UNKNOWN, unmapped_terms=list(terms))


_ARTIFACT_DOC = {
    ArtifactKind.REQUIREMENT: "requirements.md",
    ArtifactKind.TASK: "tasks.csv",
    ArtifactKind.TEST: "test_plan.md",
    ArtifactKind.SCHEDULE: "schedule.csv",
}


def make_artifact(
    artifact_id: str,
    kind: ArtifactKind,
    text: str,
    scope: DecisionScope | None = None,
    **metadata: Any,
) -> Artifact:
    return Artifact(
        id=artifact_id,
        kind=kind,
        text=text,
        source_doc=_ARTIFACT_DOC[kind],
        location=artifact_id,
        scope=scope or global_scope(),
        metadata=metadata,
    )


def load_cases(paths: list[Path]) -> list[EvalCase]:
    """Load cases from JSON files (a list of cases per file) and validate them."""
    cases: list[EvalCase] = []
    for path in paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        cases.extend(EvalCase.model_validate(item) for item in raw)
    ids = [c.case_id for c in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case ids across files")
    return cases


def load_data_dir(data_dir: Path, sources: list[str] | None = None) -> list[EvalCase]:
    """Load all cases under data_dir/<source>/*.json for the requested sources."""
    wanted = sources or ["synthetic", "assistant_written", "human_written", "real"]
    paths: list[Path] = []
    for source in wanted:
        paths.extend(sorted((data_dir / source).glob("*.json")))
    return load_cases(paths)


def save_cases(cases: list[EvalCase], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [c.model_dump(mode="json") for c in cases]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
