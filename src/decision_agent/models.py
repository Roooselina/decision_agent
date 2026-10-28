from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ScopeKind(str, Enum):
    GLOBAL = "GLOBAL"
    SCOPED = "SCOPED"
    UNKNOWN = "UNKNOWN"


class DecisionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"


class RelationType(str, Enum):
    SUPERSEDE = "SUPERSEDE"
    CANCEL = "CANCEL"
    POSTPONE = "POSTPONE"
    SCOPE_RESTRICT = "SCOPE_RESTRICT"
    NONE = "NONE"


class ArtifactKind(str, Enum):
    REQUIREMENT = "REQUIREMENT"
    TASK = "TASK"
    TEST = "TEST"
    SCHEDULE = "SCHEDULE"


class Certainty(str, Enum):
    CERTAIN = "CERTAIN"
    UNCERTAIN = "UNCERTAIN"


class ConsistencyLabel(str, Enum):
    CONSISTENT = "CONSISTENT"
    INCONSISTENT = "INCONSISTENT"
    UNCERTAIN = "UNCERTAIN"


class DecisionScope(BaseModel):
    scope_kind: ScopeKind
    release: str | None = None
    module: str | None = None
    user_group: str | None = None
    environment: str | None = None
    unmapped_terms: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    id: str
    text: str
    date: str
    source_doc: str
    topic: str
    scope: DecisionScope
    status: DecisionStatus = DecisionStatus.ACTIVE
    reason: str | None = None
    due_date: str | None = None


class DecisionRelation(BaseModel):
    old_id: str
    new_id: str
    relation_type: RelationType
    affected_scope: DecisionScope
    evidence: str


class Artifact(BaseModel):
    id: str
    kind: ArtifactKind
    text: str
    source_doc: str
    location: str
    scope: DecisionScope
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResolvedDecision(BaseModel):
    decision: Decision | None
    applied_relation: DecisionRelation | None = None
    effective_due_date: str | None = None
    certainty: Certainty = Certainty.CERTAIN
    reasons: list[str] = Field(default_factory=list)
    evidence: str = ""
    # True when the artifact scope is UNKNOWN and scoped decisions exist for the topic.
    unknown_target: bool = False


class ConsistencyResult(BaseModel):
    artifact_id: str
    label: ConsistencyLabel
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str
    expected_action: str
    effective_decision_id: str | None = None


class Question(BaseModel):
    id: str
    text: str
    target_artifact_ids: list[str]
    expected_gain: float = 0.0


class AgentStep(BaseModel):
    step_number: int
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str
    observation_summary: str


class AgentTrace(BaseModel):
    steps: list[AgentStep] = Field(default_factory=list)
    questions_asked: int = 0
    tool_calls: int = 0
    elapsed_seconds: float = 0.0
    final_results: list[ConsistencyResult] = Field(default_factory=list)
    affected_artifact_ids: list[str] = Field(default_factory=list)
    rechecked_artifact_ids: list[str] = Field(default_factory=list)
    local_answers: int = 0
    reused_lines: int = 0
    judged_lines: int = 0


class GuardWarning(BaseModel):
    severity: str
    message: str
    topic: str | None = None
    decision_id: str | None = None


class ProjectConfig(BaseModel):
    current_release: str | None = None
    scope_aliases_file: str = "scope_aliases.json"


class MeetingItem(BaseModel):
    date: str
    text: str
    source_doc: str
    location: str
