from __future__ import annotations

from pydantic import BaseModel
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from decision_agent.config import DEFAULT_SETTINGS
from decision_agent.llm.base import LLMProvider
from decision_agent.models import (
    Decision,
    DecisionRelation,
    DecisionScope,
    DecisionStatus,
    RelationType,
    ScopeKind,
)
from decision_agent.parallel import run_parallel
from decision_agent.storage import SQLiteStore

RELATION_SYSTEM = """TASK:RELATION_CLASSIFY
너는 같은 프로젝트의 이전 결정(OLD)과 새 결정(NEW) 사이의 관계를 분류한다. 관계는 다음 5개 중 하나다.

SUPERSEDE: 같은 대상의 기존 결정을 다른 결정으로 교체한다.
  예: "React를 사용한다" -> "Vue로 변경한다", "A안으로 진행" -> "B안으로 진행"
CANCEL: 기존 결정 자체를 취소한다.
  예: "결제 기능을 개발한다" -> "결제 기능 개발은 취소한다", "기능 배포" -> "배포 중단"
POSTPONE: 내용은 유지하고 일정만 뒤로 이동한다.
  예: "10월 20일 출시" -> "11월 10일 출시", "금요일 발표" -> "다음 주 발표"
SCOPE_RESTRICT: 기존 결정은 일반적으로 유지되지만 특정 범위(release, module, user_group, environment)에서는 적용하지 않거나 축소한다.
  예: "로그인에 이메일 인증을 적용한다" -> "이번 MVP에서는 이메일 인증을 제외한다", "전체 기능" -> "베타 사용자에게만 제공"
NONE: 서로 관련 없는 결정이다.

affected_scope는 이 관계가 효력을 갖는 범위다.
- 전체에 적용되는 교체나 취소이면 scope_kind=GLOBAL.
- 특정 범위에서만 적용되면 scope_kind=SCOPED이고 해당 칸을 채운다. SCOPE_RESTRICT의 affected_scope는 GLOBAL일 수 없다.
- 범위를 알 수 없으면 scope_kind=UNKNOWN.
"기존 방식으로 돌아간다"처럼 이전 결정을 되돌리는 결정은 SUPERSEDE로 본다.

출력 JSON: {"relation_type": "...", "affected_scope": {"scope_kind": "GLOBAL|SCOPED|UNKNOWN", "release": null, "module": null, "user_group": null, "environment": null, "unmapped_terms": []}, "evidence": "근거 한 문장"}
"""


class RelationPayload(BaseModel):
    relation_type: RelationType
    affected_scope: DecisionScope
    evidence: str


def relation_candidates(decisions: list[Decision], new: Decision, top_k: int = 3) -> list[Decision]:
    """Earlier decisions of the same topic, or the most similar ones by character TF-IDF."""
    prior = [d for d in decisions if d.date <= new.date and d.id != new.id]
    same = [d for d in prior if d.topic == new.topic]
    if same:
        return same[-top_k:]
    if not prior:
        return []
    corpus = [d.text for d in prior] + [new.text]
    vec = TfidfVectorizer(
        analyzer=DEFAULT_SETTINGS.tfidf_analyzer,
        ngram_range=(DEFAULT_SETTINGS.tfidf_ngram_min, DEFAULT_SETTINGS.tfidf_ngram_max),
    ).fit_transform(corpus)
    sims = cosine_similarity(vec[-1], vec[:-1]).ravel()
    order = sims.argsort()[::-1][:top_k]
    return [prior[i] for i in order if sims[i] >= 0.20]


def classify_relation(old: Decision, new: Decision, provider: LLMProvider) -> DecisionRelation:
    """Classify the relation; a GLOBAL scope on SCOPE_RESTRICT is invalid and becomes UNKNOWN."""
    user = f"OLD={old.text}\nOLD_DATE={old.date}\nNEW={new.text}\nNEW_DATE={new.date}"
    payload = provider.complete_json(RELATION_SYSTEM, user, RelationPayload, 0.0)
    scope = payload.affected_scope
    if (
        payload.relation_type == RelationType.SCOPE_RESTRICT
        and scope.scope_kind == ScopeKind.GLOBAL
    ):
        scope = DecisionScope(
            scope_kind=ScopeKind.UNKNOWN, unmapped_terms=["invalid_global_scope_restrict"]
        )
    return DecisionRelation(
        old_id=old.id,
        new_id=new.id,
        relation_type=payload.relation_type,
        affected_scope=scope,
        evidence=payload.evidence,
    )


def apply_relation_status(store: SQLiteStore, relation: DecisionRelation) -> None:
    """Update the global status cache. Only GLOBAL SUPERSEDE and CANCEL change it."""
    if relation.affected_scope.scope_kind != ScopeKind.GLOBAL:
        return
    if relation.relation_type == RelationType.SUPERSEDE:
        store.update_status(relation.old_id, DecisionStatus.SUPERSEDED)
    elif relation.relation_type == RelationType.CANCEL:
        store.update_status(relation.old_id, DecisionStatus.CANCELLED)
    # POSTPONE keeps ACTIVE; the resolver reflects it through effective_due_date.


def classify_and_store(
    decisions: list[Decision],
    provider: LLMProvider,
    store: SQLiteStore,
) -> list[DecisionRelation]:
    """Classify relations for every decision pair candidate and persist the non-NONE ones.

    The model calls run in parallel. The results are applied in date order afterwards, because
    the status cache depends on the order in which relations are applied.
    """
    pairs: list[tuple[Decision, Decision]] = []
    for new in sorted(decisions, key=lambda d: (d.date, d.id)):
        for old in relation_candidates(decisions, new):
            pairs.append((old, new))
    classified = run_parallel(lambda pair: classify_relation(pair[0], pair[1], provider), pairs)
    rels: list[DecisionRelation] = []
    for rel in classified:
        if rel.relation_type != RelationType.NONE:
            store.save_relation(rel)
            apply_relation_status(store, rel)
            rels.append(rel)
    return rels
