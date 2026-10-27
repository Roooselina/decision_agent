from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from decision_agent.config import DEFAULT_SETTINGS
from decision_agent.llm.base import LLMProvider
from decision_agent.models import (
    Decision,
    DecisionScope,
    MeetingItem,
    ProjectConfig,
    ScopeKind,
)
from decision_agent.parallel import run_parallel
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


EXTRACTION_BATCH_SYSTEM = "TASK:DECISION_EXTRACT_BATCH\n" + EXTRACTION_SYSTEM.split("\n", 1)[1].replace(
    "출력 JSON 스키마:",
    "ITEMS의 각 항목을 서로 독립적으로 판단한다. 모든 번호(index)에 대해 결과를 하나씩 낸다.\n"
    "결정이 아닌 항목도 is_decision=false로 결과에 포함한다. 번호를 빼거나 만들지 않는다.\n\n"
    "출력 JSON 스키마 (항목 하나의 형식. 전체는 {\"items\": [...]} 로 낸다. 각 항목에 index를 추가한다):",
)


class ExtractionBatchItem(BaseModel):
    index: int
    is_decision: bool = True
    text: str = ""
    topic: str = "misc"
    scope: DecisionScope = Field(default_factory=lambda: DecisionScope(scope_kind=ScopeKind.GLOBAL))
    reason: str | None = None
    due_date: str | None = None


class ExtractionBatchPayload(BaseModel):
    items: list[ExtractionBatchItem]


_SPACES = re.compile(r"\s+")


def _normalize_text(text: str) -> str:
    return _SPACES.sub(" ", text).strip().rstrip(".。!").lower()


def _single_user(item: MeetingItem, header: str) -> str:
    return (
        f"DATE={item.date}\n"
        f"{header}"
        f"TEXT={item.text}"
    )


def _extract_batch(
    group: list[MeetingItem], header: str, provider: LLMProvider
) -> dict[int, ExtractionPayload]:
    """Extract one group of items with a single call; items the model skipped are done singly."""
    done: dict[int, ExtractionPayload] = {}
    if len(group) > 1:
        lines = [f"[{n}] DATE={item.date} | TEXT={_SPACES.sub(' ', item.text)}" for n, item in enumerate(group, 1)]
        user = f"{header}ITEMS:\n" + "\n".join(lines)
        try:
            payload = provider.complete_json(EXTRACTION_BATCH_SYSTEM, user, ExtractionBatchPayload, 0.0)
        except Exception:  # noqa: BLE001 - fall back to one call per item
            payload = None
        if payload is not None:
            for row in payload.items:
                if 1 <= row.index <= len(group) and row.index - 1 not in done:
                    done[row.index - 1] = ExtractionPayload(
                        text=row.text or group[row.index - 1].text,
                        topic=row.topic,
                        scope=row.scope,
                        reason=row.reason,
                        due_date=row.due_date,
                        is_decision=row.is_decision,
                    )
    for number, item in enumerate(group):
        if number not in done:
            done[number] = provider.complete_json(
                EXTRACTION_SYSTEM, _single_user(item, header), ExtractionPayload, 0.0
            )
    return done


def pack_documents(docs: list[list[MeetingItem]], size: int) -> list[list[MeetingItem]]:
    """Fill groups of up to `size` items, keeping the items of one document together.

    Small documents share a group, so a folder of many short meeting notes needs far fewer calls.
    A document is only split when it alone exceeds `size`. Documents are packed in their given
    order, so adding or changing one document changes its own group and, at most, the groups
    after it; the earlier groups keep their exact prompts and stay cached.
    """
    groups: list[list[MeetingItem]] = []
    current: list[MeetingItem] = []
    for items in docs:
        if len(items) > size:
            if current:
                groups.append(current)
                current = []
            for start in range(0, len(items), size):
                chunk = items[start : start + size]
                if len(chunk) == size:
                    groups.append(chunk)
                else:
                    current = chunk
            continue
        if len(current) + len(items) > size:
            groups.append(current)
            current = []
        current = current + items
    if current:
        groups.append(current)
    return groups


def extract_decisions(
    items: list[MeetingItem],
    provider: LLMProvider,
    config: ProjectConfig | None = None,
    aliases: dict[str, Any] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[Decision]:
    """Extract decisions from meeting items and normalize their scopes with the alias table.

    Identical items (same date and text, for example a decision in the minutes and again in a
    mail) are judged once. Items of the same document go to the model together in groups of
    DEFAULT_SETTINGS.extract_batch, and groups run in parallel. A decision that merely restates
    the previous decision of its topic word for word is dropped.
    """
    allowed = json.dumps(_allowed_values(aliases), ensure_ascii=False)
    current_release = config.current_release if config else None
    header = f"PROJECT_CURRENT_RELEASE={current_release or '-'}\nALLOWED_SCOPE_VALUES={allowed}\n"

    unique: dict[tuple[str, str], MeetingItem] = {}
    for item in items:
        unique.setdefault((item.date, item.text), item)
    pending = list(unique.values())
    size = max(1, DEFAULT_SETTINGS.extract_batch)
    by_doc: dict[str, list[MeetingItem]] = {}
    for item in pending:
        by_doc.setdefault(item.source_doc, []).append(item)
    groups = pack_documents(list(by_doc.values()), size)

    progress = {"done": 0}
    progress_lock = threading.Lock()

    def run(group: list[MeetingItem]) -> dict[int, ExtractionPayload]:
        result = _extract_batch(group, header, provider)
        if on_progress is not None:
            with progress_lock:
                progress["done"] += len(group)
                on_progress(progress["done"], len(pending))
        return result

    outputs = run_parallel(run, groups)
    payloads: dict[tuple[str, str], ExtractionPayload] = {}
    for group, output in zip(groups, outputs, strict=True):
        for number, item in enumerate(group):
            payloads[(item.date, item.text)] = output[number]

    candidates: list[tuple[MeetingItem, ExtractionPayload]] = []
    for item in items:
        payload = payloads[(item.date, item.text)]
        if payload.is_decision:
            candidates.append((item, payload))

    # Drop word-for-word restatements of the previous decision of the same topic.
    skip: set[int] = set()
    last_text: dict[str, str] = {}
    order = sorted(range(len(candidates)), key=lambda i: (candidates[i][1].topic, candidates[i][0].date, i))
    for i in order:
        item, payload = candidates[i]
        normalized = _normalize_text(payload.text)
        if last_text.get(payload.topic) == normalized:
            skip.add(i)
            continue
        last_text[payload.topic] = normalized

    decisions: list[Decision] = []
    counter = 1
    for i, (item, payload) in enumerate(candidates):
        if i in skip:
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
