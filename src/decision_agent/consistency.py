from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from decision_agent.llm.base import LLMProvider
from decision_agent.models import Artifact, Certainty, ConsistencyLabel, ConsistencyResult, Decision
from decision_agent.resolver import resolve_effective_decision
from decision_agent.storage import SQLiteStore

CONSISTENCY_SYSTEM = """TASK:CONSISTENCY
너는 프로젝트 산출물이 회의 결정을 제대로 반영했는지 점검하는 검토자다.
입력에는 해당 산출물의 범위(scope)에서 현재 유효한 결정(DECISION), 산출물(ARTIFACT), 추가 근거(EVIDENCE)가 있다.

판단 규칙:
1. 산출물을 최신 결정 문장과 단순 비교하지 말고, 입력된 DECISION이 이 산출물의 범위에 적용되는 결정이라는 전제로 판단한다.
2. 산출물이 결정과 모순되는 작업, 일정, 요구사항, 테스트를 그대로 유지하고 있으면 INCONSISTENT.
3. 산출물이 결정을 이미 반영했거나 결정과 무관하게 유지되어야 하는 내용이면 CONSISTENT.
4. 결정문이 무엇을 가리키는지 특정하지 못하거나(예: "기존 방식으로 돌아간다"), 산출물이 결정의 영향 범위에 포함되는지 근거만으로 알 수 없으면 UNCERTAIN. 추측하지 않는다.
5. EVIDENCE에 사용자 답변이나 결정 이력이 있으면 그것을 근거로 사용한다.
6. 일정 결정은 DECISION_DUE와 ARTIFACT_DATE를 비교한다. 결정이 적용되는 일정인데 ARTIFACT_DATE가 DECISION_DUE보다 이르면 반영되지 않은 것이다.

출력은 JSON 한 개만 낸다.
{"label": "CONSISTENT|INCONSISTENT|UNCERTAIN", "evidence": "판단 근거 한 문장", "expected_action": "사람이 할 조치 한 문장"}
expected_action은 제안일 뿐이며 파일을 직접 수정하지 않는다.
"""


class ConsistencyPayload(BaseModel):
    label: ConsistencyLabel
    evidence: str
    expected_action: str


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def build_consistency_prompt(
    decision_text: str,
    decision_date: str,
    decision_due: str | None,
    decision_scope_json: str,
    artifact: Artifact,
    evidence: list[str],
) -> str:
    """Render the structured user message for TASK:CONSISTENCY."""
    lines = [
        f"DECISION={decision_text}",
        f"DECISION_DATE={decision_date}",
        f"DECISION_DUE={decision_due or '-'}",
        f"DECISION_SCOPE={decision_scope_json}",
        f"ARTIFACT_ID={artifact.id}",
        f"ARTIFACT_KIND={artifact.kind.value}",
        f"ARTIFACT={artifact.text}",
        f"ARTIFACT_DATE={artifact.metadata.get('date') or '-'}",
        f"ARTIFACT_STATUS={artifact.metadata.get('status') or '-'}",
        f"ARTIFACT_SCOPE={artifact.scope.model_dump_json()}",
        "EVIDENCE:",
    ]
    lines.extend(f"- {item}" for item in evidence)
    return "\n".join(lines)


CONSISTENCY_BATCH_SYSTEM = """TASK:CONSISTENCY_BATCH
""" + CONSISTENCY_SYSTEM.split("\n", 1)[1].replace(
    "출력은 JSON 한 개만 낸다.\n"
    '{"label": "CONSISTENT|INCONSISTENT|UNCERTAIN", "evidence": "판단 근거 한 문장", "expected_action": "사람이 할 조치 한 문장"}\n',
    "같은 DECISION에 대해 ARTIFACTS의 각 산출물을 서로 독립적으로 판단한다. 한 산출물의 판단이 다른 산출물에 영향을 주면 안 된다.\n"
    "모든 번호(index)에 대해 결과를 하나씩 낸다. 번호를 빼거나 만들지 않는다.\n"
    "출력은 JSON 한 개만 낸다.\n"
    '{"results": [{"index": 1, "label": "CONSISTENT|INCONSISTENT|UNCERTAIN", "evidence": "판단 근거 한 문장", "expected_action": "사람이 할 조치 한 문장"}]}\n',
)


class ConsistencyBatchItem(BaseModel):
    index: int
    label: ConsistencyLabel
    evidence: str
    expected_action: str


class ConsistencyBatchPayload(BaseModel):
    results: list[ConsistencyBatchItem]


@dataclass
class PreparedCheck:
    """Everything needed to judge one artifact, read from the store in the calling thread."""

    artifact: Artifact
    early: ConsistencyResult | None = None
    decision: Decision | None = None
    due: str | None = None
    evidence: list[str] = field(default_factory=list)
    user: str = ""

    def group_key(self) -> tuple:
        """Artifacts with the same key are judged against the same decision and evidence."""
        decision_id = self.decision.id if self.decision else None
        return (decision_id, self.due, tuple(self.evidence))

    def signature(self) -> tuple:
        """Two artifacts with the same signature get the same judgement, so one call is enough."""
        a = self.artifact
        return (
            self.group_key(),
            a.kind.value,
            a.text,
            str(a.metadata.get("date") or ""),
            str(a.metadata.get("status") or ""),
            a.scope.model_dump_json(),
        )


def prepare_check(
    store: SQLiteStore,
    topic: str,
    artifact: Artifact,
    as_of_date: str,
    extra_evidence: list[str] | None = None,
) -> PreparedCheck:
    """Resolve the effective decision and build the prompt without calling the model."""
    resolved = resolve_effective_decision(store, topic, artifact.scope, as_of_date)
    evidence = _dedupe(list(extra_evidence or []) + store.list_evidence(topic, artifact.id))
    decision_id = resolved.decision.id if resolved.decision else None
    if resolved.decision is None:
        return PreparedCheck(
            artifact,
            early=ConsistencyResult(
                artifact_id=artifact.id,
                label=ConsistencyLabel.UNCERTAIN,
                confidence=0.0,
                evidence=resolved.evidence or "유효한 결정을 확정할 수 없습니다.",
                expected_action="관련 결정을 확인하세요.",
                effective_decision_id=None,
            ),
        )
    user_answers = [e for e in evidence if not e.startswith("HISTORY:")]
    # An uncertain resolution blocks the judgement until the user has answered a question
    # about it. After that the judge sees the answer together with the resolver's note.
    blocked = resolved.certainty == Certainty.UNCERTAIN and not user_answers
    if blocked:
        return PreparedCheck(
            artifact,
            early=ConsistencyResult(
                artifact_id=artifact.id,
                label=ConsistencyLabel.UNCERTAIN,
                confidence=0.0,
                evidence=resolved.evidence or "유효한 결정을 확정할 수 없습니다.",
                expected_action="적용 범위를 확인하세요.",
                effective_decision_id=decision_id,
            ),
        )
    decision = resolved.decision
    if resolved.certainty == Certainty.UNCERTAIN and resolved.evidence:
        evidence = [*evidence, f"RESOLVER_NOTE: {resolved.evidence}"]
    due = resolved.effective_due_date or decision.due_date
    user = build_consistency_prompt(
        decision.text, decision.date, due, decision.scope.model_dump_json(), artifact, evidence
    )
    return PreparedCheck(artifact, decision=decision, due=due, evidence=evidence, user=user)


def _result(prepared: PreparedCheck, label, evidence: str, action: str) -> ConsistencyResult:
    assert prepared.decision is not None
    return ConsistencyResult(
        artifact_id=prepared.artifact.id,
        label=label,
        confidence=1.0,
        evidence=evidence,
        expected_action=action,
        effective_decision_id=prepared.decision.id,
    )


def judge_prepared(
    prepared: PreparedCheck, provider: LLMProvider, temperature: float = 0.0
) -> ConsistencyResult:
    """One model call for one prepared artifact."""
    if prepared.early is not None:
        return prepared.early
    payload = provider.complete_json(CONSISTENCY_SYSTEM, prepared.user, ConsistencyPayload, temperature)
    return _result(prepared, payload.label, payload.evidence, payload.expected_action)


def build_batch_prompt(items: list[PreparedCheck]) -> str:
    """One DECISION block followed by numbered artifacts. All items share the same group key."""
    first = items[0]
    assert first.decision is not None
    decision = first.decision
    lines = [
        f"DECISION={decision.text}",
        f"DECISION_DATE={decision.date}",
        f"DECISION_DUE={first.due or '-'}",
        f"DECISION_SCOPE={decision.scope.model_dump_json()}",
        "EVIDENCE:",
        *[f"- {item}" for item in first.evidence],
        "ARTIFACTS:",
    ]
    for number, prepared in enumerate(items, start=1):
        a = prepared.artifact
        lines.append(
            f"[{number}] ID={a.id} | KIND={a.kind.value} | ARTIFACT={a.text} | ARTIFACT_DATE={a.metadata.get('date') or '-'}"
            f" | ARTIFACT_STATUS={a.metadata.get('status') or '-'} | ARTIFACT_SCOPE={a.scope.model_dump_json()}"
        )
    return "\n".join(lines)


def judge_batch(
    items: list[PreparedCheck], provider: LLMProvider, temperature: float = 0.0
) -> dict[str, ConsistencyResult]:
    """One model call for several artifacts that share a decision.

    Returns results only for artifacts the model answered; the caller judges the rest one by one.
    """
    payload = provider.complete_json(
        CONSISTENCY_BATCH_SYSTEM, build_batch_prompt(items), ConsistencyBatchPayload, temperature
    )
    results: dict[str, ConsistencyResult] = {}
    for row in payload.results:
        if not 1 <= row.index <= len(items):
            continue
        prepared = items[row.index - 1]
        if prepared.artifact.id in results:
            continue
        results[prepared.artifact.id] = _result(prepared, row.label, row.evidence, row.expected_action)
    return results


def check(
    store: SQLiteStore,
    topic: str,
    artifact: Artifact,
    as_of_date: str,
    provider: LLMProvider,
    temperature: float = 0.0,
    extra_evidence: list[str] | None = None,
) -> ConsistencyResult:
    """Judge one artifact against the decision that is effective for its own scope."""
    prepared = prepare_check(store, topic, artifact, as_of_date, extra_evidence)
    return judge_prepared(prepared, provider, temperature)
