from __future__ import annotations

from pydantic import BaseModel
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from decision_agent.config import DEFAULT_SETTINGS
from decision_agent.llm.base import LLMProvider
from decision_agent.models import Certainty, DecisionScope, GuardWarning, ScopeKind
from decision_agent.resolver import resolve_effective_decision
from decision_agent.storage import SQLiteStore

GUARD_SYSTEM = """TASK:GUARD
너는 새로 만들려는 Task가 이미 제외되거나 취소된 기능과 충돌하는지 점검한다.
입력에는 새 Task(TASK)와 그 범위(TASK_SCOPE), 그리고 그 범위에서 현재 유효한 결정(EFFECTIVE_DECISION)이 있다.

판단:
- EFFECTIVE_DECISION이 어떤 기능을 제외, 취소, 중단하는 결정이고 TASK가 바로 그 기능을 구현하거나 진행하려는 것이면 severity=warning.
- 관련은 있어 보이지만 같은 기능인지 확신할 수 없으면 severity=info. 경고로 단정하지 않는다.
- EFFECTIVE_DECISION이 기능을 유지하거나 적용하는 결정이면 충돌이 아니다. severity=none.
- TASK와 결정이 서로 다른 주제이면 severity=none.

출력: {"severity": "warning|info|none", "rationale": "한 문장"}
"""


class GuardPayload(BaseModel):
    severity: str
    rationale: str = ""


def _describe_scope(scope: DecisionScope) -> str:
    parts = [
        f"{dim}={value}"
        for dim, value in (
            ("release", scope.release),
            ("module", scope.module),
            ("user_group", scope.user_group),
            ("environment", scope.environment),
        )
        if value
    ]
    return ", ".join(parts) if parts else "전체"


def _related_topics(task_text: str, store: SQLiteStore) -> list[str]:
    """Topics whose decision texts share character n-grams with the task text."""
    topics = sorted({d.topic for d in store.list_decisions()})
    related: list[str] = []
    for topic in topics:
        texts = [d.text for d in store.list_decisions(topic)]
        vec = TfidfVectorizer(
            analyzer=DEFAULT_SETTINGS.tfidf_analyzer,
            ngram_range=(DEFAULT_SETTINGS.tfidf_ngram_min, DEFAULT_SETTINGS.tfidf_ngram_max),
        ).fit_transform(texts + [task_text])
        if cosine_similarity(vec[-1], vec[:-1]).max() > 0.0:
            related.append(topic)
    return related


def check_new_task(
    task_text: str,
    task_scope: DecisionScope,
    store: SQLiteStore,
    provider: LLMProvider,
    as_of_date: str,
    topic: str | None = None,
) -> GuardWarning | None:
    """Warn when a new task conflicts with a decision that excludes it in the task's scope.

    The warning is a confirmation request, not a block. Ambiguous overlap yields an info-level
    note, and an unknown task scope yields an info note asking for the scope.
    """
    topics = [topic] if topic else _related_topics(task_text, store)
    best: GuardWarning | None = None
    for t in topics:
        resolved = resolve_effective_decision(store, t, task_scope, as_of_date)
        decision = resolved.decision
        if decision is None:
            continue
        if resolved.certainty == Certainty.UNCERTAIN and task_scope.scope_kind == ScopeKind.UNKNOWN:
            best = best or GuardWarning(
                severity="info",
                message="참고: Task의 범위(release, module 등)를 알 수 없어 제외된 결정과의 충돌을 점검하지 못했습니다.",
                topic=t,
                decision_id=decision.id,
            )
            continue
        user = (
            f"TASK={task_text}\n"
            f"TASK_SCOPE={task_scope.model_dump_json()}\n"
            f"EFFECTIVE_DECISION={decision.text}\n"
            f"DECISION_DATE={decision.date}\n"
            f"DECISION_SCOPE={decision.scope.model_dump_json()}\n"
            f"DECISION_REASON={decision.reason or '-'}"
        )
        payload = provider.complete_json(GUARD_SYSTEM, user, GuardPayload, 0.0)
        reason = decision.reason or "사유 기록 없음"
        if payload.severity == "warning":
            return GuardWarning(
                severity="warning",
                message=(
                    f"이 기능은 {decision.date} 회의에서 {_describe_scope(decision.scope)} 범위 "
                    f"결정으로 제외되었습니다({decision.text}). 사유: {reason}. 그래도 추가할까요?"
                ),
                topic=t,
                decision_id=decision.id,
            )
        if payload.severity == "info" and (best is None or best.severity != "warning"):
            best = GuardWarning(
                severity="info",
                message=(
                    f"참고: {decision.date} 회의의 결정({decision.text})과 관련될 수 있습니다. "
                    "같은 기능인지 확인해 보세요."
                ),
                topic=t,
                decision_id=decision.id,
            )
    return best
