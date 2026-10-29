from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel
from sklearn.feature_extraction.text import TfidfVectorizer

from decision_agent.config import DEFAULT_SETTINGS, effective_k
from decision_agent.llm.base import LLMProvider
from decision_agent.models import Artifact, Decision, DecisionRelation
from decision_agent.ranking import stable_order
from decision_agent.retrieval import tfidf_similarities
from decision_agent.scope import scope_overlaps
from decision_agent.storage import SQLiteStore
from decision_agent.verdicts import VerdictStore

IMPACT_SYSTEM = """TASK:IMPACT_SELECT
너는 결정 변경의 영향을 받아야 하는 산출물을 고른다.
DECISIONS는 같은 주제(topic)의 결정 이력이고, 그 아래 CANDIDATES는 후보 산출물 목록이다.

규칙:
1. 후보 목록에 있는 ID 중에서만 고른다. 목록에 없는 ID를 만들지 않는다.
2. 결정의 대상 기능, 요구사항, 일정과 같은 주제를 다루는 산출물을 고른다. 이미 결정을 반영했더라도 같은 주제이면 고른다
   (반영 여부는 다음 단계에서 판단한다).
3. 결정 대상과 상관없는 산출물은 고르지 않는다.

출력: {"artifact_ids": ["ID1", "ID2"]}
"""


class ImpactPayload(BaseModel):
    artifact_ids: list[str]


def _vectorizer() -> TfidfVectorizer:
    return TfidfVectorizer(
        analyzer=DEFAULT_SETTINGS.tfidf_analyzer,
        ngram_range=(DEFAULT_SETTINGS.tfidf_ngram_min, DEFAULT_SETTINGS.tfidf_ngram_max),
    )


def _rank_candidates(
    query: str,
    decisions: list[Decision],
    artifacts: list[Artifact],
    k: int,
) -> list[Artifact]:
    """Top-k artifacts by character TF-IDF similarity, dropping zero-similarity and scope conflicts."""
    if not artifacts or not decisions:
        return []
    k = effective_k(k, len(artifacts))
    # Same numbers as fit_transform(texts + [query]); the counting of the texts is cached.
    sims = tfidf_similarities([a.text for a in artifacts], query)
    result: list[Artifact] = []
    for i in stable_order(sims, [a.id for a in artifacts]):
        if sims[i] <= 0.0:
            break
        artifact = artifacts[i]
        if all(scope_overlaps(d.scope, artifact.scope) is False for d in decisions):
            continue
        result.append(artifact)
        if len(result) >= k:
            break
    return result


def find_candidate_artifacts(
    decision: Decision,
    artifacts: list[Artifact],
    k: int | None = None,
) -> list[Artifact]:
    """Candidate artifacts for a single decision."""
    return _rank_candidates(decision.text, [decision], artifacts, k or DEFAULT_SETTINGS.k)


def _select(
    decisions: list[Decision],
    candidates: list[Artifact],
    provider: LLMProvider,
    store: VerdictStore | None = None,
) -> list[Artifact]:
    """The model picks affected ids among the candidates.

    With a store, candidates whose selection is already known for this decision state are not
    sent again; only unseen candidates go to the model. The result is then in candidate order.
    """
    if not candidates:
        return []
    if store is None:
        return _ask(decisions, candidates, provider)
    known = store.get_selections(candidates)
    unseen = [a for a in candidates if a.id not in known]
    picked: set[str] = set()
    if unseen:
        picked = {a.id for a in _ask(decisions, unseen, provider)}
        store.put_selections(unseen, picked)
    return [a for a in candidates if known.get(a.id) or a.id in picked]


def _ask(
    decisions: list[Decision],
    candidates: list[Artifact],
    provider: LLMProvider,
) -> list[Artifact]:
    lines = [f"- {d.date} {d.text}" for d in decisions]
    user = "DECISIONS=\n" + "\n".join(lines) + "\nCANDIDATES:\n"
    user += "\n".join(f"ID={a.id} KIND={a.kind.value} TEXT={a.text}" for a in candidates)
    payload = provider.complete_json(IMPACT_SYSTEM, user, ImpactPayload, 0.0)
    allowed = {a.id: a for a in candidates}
    return [allowed[i] for i in dict.fromkeys(payload.artifact_ids) if i in allowed]


def predict_affected(
    decision: Decision,
    relation: DecisionRelation | None,
    artifacts: list[Artifact],
    provider: LLMProvider,
    k: int | None = None,
) -> list[Artifact]:
    """Select affected artifacts for one decision; the LLM can only choose candidate ids."""
    candidates = find_candidate_artifacts(decision, artifacts, k)
    return _select([decision], candidates, provider)


def candidate_artifacts_for_topic(
    store: SQLiteStore,
    topic: str,
    artifacts: list[Artifact],
    as_of_date: str,
    k: int | None = None,
    extra_ranker: Callable[[str, list[Artifact]], list[Artifact]] | None = None,
) -> tuple[list[Decision], list[Artifact]]:
    """Decisions of the topic up to the date and the TF-IDF candidate artifacts for them.

    extra_ranker may add artifacts found by another method (for example embeddings). They are
    appended after the TF-IDF candidates and must still pass the same scope check.
    """
    decisions = [d for d in store.list_decisions(topic) if d.date <= as_of_date]
    if not decisions:
        return [], []
    query = " ".join(d.text for d in decisions)
    ranked = _rank_candidates(query, decisions, artifacts, k or DEFAULT_SETTINGS.k)
    if extra_ranker is not None:
        seen = {a.id for a in ranked}
        for artifact in extra_ranker(query, artifacts):
            if artifact.id in seen:
                continue
            if all(scope_overlaps(d.scope, artifact.scope) is False for d in decisions):
                continue
            ranked.append(artifact)
            seen.add(artifact.id)
    return decisions, ranked


def predict_affected_for_topic(
    store: SQLiteStore,
    topic: str,
    artifacts: list[Artifact],
    provider: LLMProvider,
    as_of_date: str,
    k: int | None = None,
    verdict_store: VerdictStore | None = None,
) -> list[Artifact]:
    """Select artifacts affected by any decision of the topic, using the whole decision history."""
    decisions, candidates = candidate_artifacts_for_topic(store, topic, artifacts, as_of_date, k)
    if not decisions:
        return []
    return _select(decisions, candidates, provider, verdict_store)
