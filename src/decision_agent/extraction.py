from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from decision_agent.llm.base import LLMProvider
from decision_agent.models import (
    Decision,
    DecisionScope,
    MeetingItem,
    ProjectConfig,
    ScopeKind,
)
from decision_agent.scope import DIMENSIONS, normalize_scope

EXTRACTION_SYSTEM = """TASK:DECISION_EXTRACT
너는 회의록 한 항목에서 확정된 의사결정을 추출한다.

규칙:
1. 단순 논의, 의견, 질문, 잡담은 결정이 아니다. 이 경우 is_decision을 false로 둔다.
2. text는 결정 문장을 원문 그대로 쓴다.
3. topic은 결정 대상을 나타내는 영어 snake_case 식별자다. 같은 대상에 대한 결정은 같은 topic을 쓴다
   (예: 이메일 인증 관련은 email_auth, 프론트엔드 프레임워크 관련은 frontend_framework).
4. scope는 문장에 명시된 적용 범위만 쓴다.
   - 범위 제한 표현이 없고 전체에 적용되는 것이 분명하면 scope_kind=GLOBAL.
   - release, module, user_group, environment 중 명시된 것이 있으면 scope_kind=SCOPED이고 해당 칸을 채운다.
     값은 ALLOWED_SCOPE_VALUES에 있는 정규 값을 우선 사용한다.
   - 범위를 나타내는 표현이 있지만 어떤 범위인지 알 수 없으면 scope_kind=UNKNOWN이고 그 표현을 unmapped_terms에 넣는다.
   - 모르면 GLOBAL로 추측하지 않는다.
5. "이번 버전", "이번 MVP"처럼 상대적 표현은 PROJECT_CURRENT_RELEASE 값으로 해석한다.
6. reason은 문장에 명시된 사유만 쓴다. 없으면 null.
7. due_date는 일정 결정에서 새 날짜를 YYYY-MM-DD로 알 수 있을 때만 쓴다. "다음 주"는 회의일 기준 다음 주 월요일로 계산한다.

출력 JSON 스키마:
{"is_decision": true, "text": "", "topic": "", "scope": {"scope_kind": "GLOBAL|SCOPED|UNKNOWN", "release": null, "module": null, "user_group": null, "environment": null, "unmapped_terms": []}, "reason": null, "due_date": null}
"""


class ExtractionPayload(BaseModel):
    text: str
    topic: str
    scope: DecisionScope
    reason: str | None = None
    due_date: str | None = None
    is_decision: bool = True


def _allowed_values(aliases: dict[str, Any] | None) -> dict[str, list[str]]:
    if not aliases:
        return {}
    return {dim: sorted(values.keys()) for dim, values in aliases.items() if values}


def _normalize_payload_scope(
    scope: DecisionScope,
    config: ProjectConfig | None,
    aliases: dict[str, Any] | None,
) -> DecisionScope:
    """Map LLM scope values onto canonical values; unknown terms make the scope UNKNOWN."""
    if aliases is None or scope.scope_kind == ScopeKind.UNKNOWN:
        return scope
    raw = {dim: getattr(scope, dim) for dim in DIMENSIONS}
    normalized = normalize_scope(raw, config or ProjectConfig(), aliases)
    normalized.unmapped_terms = list(
        dict.fromkeys(normalized.unmapped_terms + scope.unmapped_terms)
    )
    if scope.scope_kind == ScopeKind.SCOPED and normalized.scope_kind == ScopeKind.GLOBAL:
        return DecisionScope(scope_kind=ScopeKind.UNKNOWN, unmapped_terms=normalized.unmapped_terms)
    if normalized.unmapped_terms:
        normalized.scope_kind = ScopeKind.UNKNOWN
    return normalized


def extract_decisions(
    items: list[MeetingItem],
    provider: LLMProvider,
    config: ProjectConfig | None = None,
    aliases: dict[str, Any] | None = None,
) -> list[Decision]:
    """Extract decisions from meeting items and normalize their scopes with the alias table."""
    decisions: list[Decision] = []
    counter = 1
    allowed = json.dumps(_allowed_values(aliases), ensure_ascii=False)
    current_release = config.current_release if config else None
    for item in items:
        user = (
            f"DATE={item.date}\n"
            f"PROJECT_CURRENT_RELEASE={current_release or '-'}\n"
            f"ALLOWED_SCOPE_VALUES={allowed}\n"
            f"TEXT={item.text}"
        )
        payload = provider.complete_json(EXTRACTION_SYSTEM, user, ExtractionPayload, 0.0)
        if not payload.is_decision:
            continue
        decisions.append(
            Decision(
                id=f"D{counter:02d}",
                text=payload.text,
                date=item.date,
                source_doc=item.source_doc,
                topic=payload.topic,
                scope=_normalize_payload_scope(payload.scope, config, aliases),
                reason=payload.reason,
                due_date=payload.due_date,
            )
        )
        counter += 1
    return sorted(decisions, key=lambda d: (d.topic, d.date, d.id))
