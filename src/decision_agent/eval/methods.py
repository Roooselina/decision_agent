from __future__ import annotations

import time
from dataclasses import dataclass, field

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from decision_agent.agent.agent import run_agent
from decision_agent.agent.pipeline import run_pipeline
from decision_agent.config import Settings
from decision_agent.consistency import (
    CONSISTENCY_SYSTEM,
    ConsistencyPayload,
    build_consistency_prompt,
)
from decision_agent.eval.dataset import EvalCase
from decision_agent.eval.simulator import UserSimulator
from decision_agent.extraction import extract_decisions
from decision_agent.llm.base import LLMProvider
from decision_agent.models import (
    Artifact,
    ConsistencyLabel,
    Decision,
    DecisionRelation,
    DecisionScope,
    MeetingItem,
    ProjectConfig,
    ScopeKind,
)
from decision_agent.relations import apply_relation_status, classify_and_store
from decision_agent.storage import SQLiteStore

METHOD_DESCRIPTIONS = {
    "B1_tfidf": "retrieval only: top-k TF-IDF matches to the latest decision are all flagged",
    "B2_rag": "retrieve then one judge call against the most similar decision; no scope, no history",
    "B3_long_context": "one judge call per artifact with the raw decision log; no retrieval, no scope",
    "P": "fixed pipeline, no clarification",
    "M2": "fixed pipeline with clarification",
    "M1": "agent, no clarification",
    "M1c": "agent with clarification",
    "A_scope_agnostic": "M1c on a store whose scopes were all reset to GLOBAL (ablation)",
}
ALL_METHODS = list(METHOD_DESCRIPTIONS)
ORACLE_STAGES = ("none", "decisions", "full")


class CountingProvider(LLMProvider):
    """Counts complete() calls so cost can be compared across methods."""

    def __init__(self, inner: LLMProvider) -> None:
        self.inner = inner
        self.calls = 0

    def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
        json_schema: dict | None = None,
    ) -> str:
        self.calls += 1
        return self.inner.complete(system, user, temperature, json_schema)


@dataclass
class Prepared:
    """Decisions and relations after the ingest stage, shared by all methods of one run."""

    decisions: list[Decision]
    relations: list[DecisionRelation]
    llm_calls: int = 0
    extraction_f1: float | None = None
    relation_f1: float | None = None


@dataclass
class MethodRun:
    predictions: dict[str, ConsistencyLabel]
    flagged: set[str]
    confidences: dict[str, float | None]
    evidence: dict[str, str]
    questions: int = 0
    informative_answers: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    elapsed: float = 0.0
    steps: list[str] = field(default_factory=list)


def _f1(predicted: set, gold: set) -> float:
    if not predicted and not gold:
        return 1.0
    tp = len(predicted & gold)
    precision = tp / len(predicted) if predicted else 0.0
    recall = tp / len(gold) if gold else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def prepare(case: EvalCase, provider: LLMProvider, oracle: str) -> Prepared:
    """Run the ingest stage, or substitute gold decisions and relations (oracle stages)."""
    counter = CountingProvider(provider)
    config = ProjectConfig(current_release=case.current_release)
    if oracle in ("decisions", "full"):
        decisions = list(case.gold_decisions)
        extraction_f1 = None
    else:
        meetings = [MeetingItem(**m.model_dump()) for m in case.meetings]
        decisions = extract_decisions(meetings, counter, config, case.aliases)
        got = {(d.date, d.text, d.topic, d.scope.scope_kind.value) for d in decisions}
        want = {(d.date, d.text, d.topic, d.scope.scope_kind.value) for d in case.gold_decisions}
        extraction_f1 = _f1(got, want)
    scratch = SQLiteStore()
    for d in decisions:
        scratch.save_decision(d)
    if oracle == "full":
        relations = list(case.gold_relations)
        relation_f1 = None
    else:
        relations = classify_and_store(decisions, counter, scratch)
        by_id = {d.id: d for d in decisions}
        gold_by_id = {d.id: d for d in case.gold_decisions}

        def key(rel: DecisionRelation, lookup: dict[str, Decision]):
            return (
                lookup[rel.old_id].text,
                lookup[rel.new_id].text,
                rel.relation_type.value,
                rel.affected_scope.scope_kind.value,
            )

        got_rel = {key(r, by_id) for r in relations}
        want_rel = {key(r, gold_by_id) for r in case.gold_relations}
        relation_f1 = _f1(got_rel, want_rel)
    return Prepared(decisions, relations, counter.calls, extraction_f1, relation_f1)


def _global(scope: DecisionScope) -> DecisionScope:
    return DecisionScope(scope_kind=ScopeKind.GLOBAL)


def build_store(case: EvalCase, prepared: Prepared, scope_agnostic: bool = False) -> SQLiteStore:
    """Fresh store for one method run. scope_agnostic resets every scope to GLOBAL."""
    store = SQLiteStore()
    for decision in prepared.decisions:
        d = decision.model_copy(deep=True)
        if scope_agnostic:
            d.scope = _global(d.scope)
        store.save_decision(d)
    for artifact in case.artifacts:
        a = artifact.model_copy(deep=True)
        if scope_agnostic:
            a.scope = _global(a.scope)
        store.save_artifact(a)
    for relation in prepared.relations:
        r = relation.model_copy(deep=True)
        if scope_agnostic:
            r.affected_scope = _global(r.affected_scope)
        store.save_relation(r)
        apply_relation_status(store, r)
    return store


def _topic_decisions(store: SQLiteStore, case: EvalCase) -> list[Decision]:
    return [d for d in store.list_decisions(case.topic) if d.date <= case.as_of_date]


def _vectorizer(settings: Settings) -> TfidfVectorizer:
    return TfidfVectorizer(
        analyzer=settings.tfidf_analyzer,
        ngram_range=(settings.tfidf_ngram_min, settings.tfidf_ngram_max),
    )


def _rank(query: str, documents: list[str], settings: Settings) -> list[float]:
    matrix = _vectorizer(settings).fit_transform(documents + [query])
    return [float(x) for x in cosine_similarity(matrix[-1], matrix[:-1]).ravel()]


def _top_k(case: EvalCase, query: str, settings: Settings) -> list[Artifact]:
    sims = _rank(query, [a.text for a in case.artifacts], settings)
    order = sorted(range(len(sims)), key=lambda i: -sims[i])
    return [case.artifacts[i] for i in order if sims[i] > 0.0][: settings.k]


def _judge(
    provider: LLMProvider, decision: Decision | None, text: str, artifact: Artifact, due=None
) -> ConsistencyLabel:
    scope_json = decision.scope.model_dump_json() if decision else "{}"
    user = build_consistency_prompt(
        text,
        decision.date if decision else "-",
        due or (decision.due_date if decision else None),
        scope_json,
        artifact,
        [],
    )
    return provider.complete_json(CONSISTENCY_SYSTEM, user, ConsistencyPayload, 0.0).label


def _finish(
    case: EvalCase,
    labels: dict[str, ConsistencyLabel],
    flagged: set[str],
    confidences: dict[str, float | None],
    evidence: dict[str, str],
) -> dict[str, ConsistencyLabel]:
    return {a.id: labels.get(a.id, ConsistencyLabel.CONSISTENT) for a in case.artifacts}


def run_b1(case: EvalCase, store: SQLiteStore, settings: Settings) -> MethodRun:
    decisions = _topic_decisions(store, case)
    flagged = {a.id for a in _top_k(case, decisions[-1].text, settings)} if decisions else set()
    labels = {i: ConsistencyLabel.INCONSISTENT for i in flagged}
    return MethodRun(
        predictions=_finish(case, labels, flagged, {}, {}),
        flagged=flagged,
        confidences={i: None for i in flagged},
        evidence={i: "retrieval hit" for i in flagged},
        steps=["retrieve"],
    )


def run_b2(
    case: EvalCase, store: SQLiteStore, provider: LLMProvider, settings: Settings
) -> MethodRun:
    topic_decisions = _topic_decisions(store, case)
    all_decisions = [d for d in store.list_decisions() if d.date <= case.as_of_date]
    if not topic_decisions:
        return MethodRun({a.id: ConsistencyLabel.CONSISTENT for a in case.artifacts}, set(), {}, {})
    flagged_artifacts = _top_k(case, topic_decisions[-1].text, settings)
    labels: dict[str, ConsistencyLabel] = {}
    for artifact in flagged_artifacts:
        sims = _rank(artifact.text, [d.text for d in all_decisions], settings)
        best = all_decisions[max(range(len(sims)), key=lambda i: sims[i])]
        labels[artifact.id] = _judge(provider, best, best.text, artifact)
    flagged = {a.id for a in flagged_artifacts}
    return MethodRun(
        predictions=_finish(case, labels, flagged, {}, {}),
        flagged=flagged,
        confidences={i: 1.0 for i in flagged},
        evidence={i: "single judge call" for i in flagged},
        steps=["retrieve", "judge"],
    )


def run_b3(
    case: EvalCase, store: SQLiteStore, provider: LLMProvider, settings: Settings
) -> MethodRun:
    decisions = sorted(
        (d for d in store.list_decisions() if d.date <= case.as_of_date),
        key=lambda d: (d.date, d.id),
    )
    if not decisions:
        return MethodRun({a.id: ConsistencyLabel.CONSISTENT for a in case.artifacts}, set(), {}, {})
    log = " / ".join(f"{d.date} {d.text}" for d in decisions)
    latest = decisions[-1]
    labels = {a.id: _judge(provider, latest, log, a, latest.due_date) for a in case.artifacts}
    flagged = {a.id for a in case.artifacts}
    return MethodRun(
        predictions=_finish(case, labels, flagged, {}, {}),
        flagged=flagged,
        confidences={i: 1.0 for i in flagged},
        evidence={i: "single judge call over the full log" for i in flagged},
        steps=["judge_all"],
    )


def _from_trace(case: EvalCase, trace, sim: UserSimulator | None) -> MethodRun:
    labels = {r.artifact_id: r.label for r in trace.final_results}
    return MethodRun(
        predictions={a.id: labels.get(a.id, ConsistencyLabel.CONSISTENT) for a in case.artifacts},
        flagged=set(trace.affected_artifact_ids),
        confidences={r.artifact_id: r.confidence for r in trace.final_results},
        evidence={r.artifact_id: r.evidence for r in trace.final_results},
        questions=trace.questions_asked,
        informative_answers=sim.informative_answers if sim else 0,
        tool_calls=trace.tool_calls,
        steps=[s.tool_name for s in trace.steps],
    )


def run_method(
    name: str,
    case: EvalCase,
    prepared: Prepared,
    provider: LLMProvider,
    settings: Settings,
) -> MethodRun:
    """Run one method on one case with a fresh store and a fresh simulated user."""
    if name not in METHOD_DESCRIPTIONS:
        raise ValueError(f"unknown method {name}")
    counter = CountingProvider(provider)
    store = build_store(case, prepared, scope_agnostic=name == "A_scope_agnostic")
    sim = UserSimulator(case)
    start = time.perf_counter()
    if name == "B1_tfidf":
        run = run_b1(case, store, settings)
    elif name == "B2_rag":
        run = run_b2(case, store, counter, settings)
    elif name == "B3_long_context":
        run = run_b3(case, store, counter, settings)
    elif name in ("P", "M2"):
        trace = run_pipeline(
            store,
            counter,
            case.topic,
            case.artifacts,
            case.as_of_date,
            clarify=name == "M2",
            ask=sim.ask,
            settings=settings,
        )
        run = _from_trace(case, trace, sim)
    else:
        trace = run_agent(
            store,
            counter,
            case.topic,
            case.artifacts,
            case.as_of_date,
            ask=sim.ask,
            settings=settings,
            allow_clarify=name in ("M1c", "A_scope_agnostic"),
        )
        run = _from_trace(case, trace, sim)
    run.elapsed = time.perf_counter() - start
    run.llm_calls = counter.calls
    return run
