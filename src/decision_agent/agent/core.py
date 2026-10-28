from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from decision_agent.agent.interfaces import UserInterface
from decision_agent.clarification import generate_questions, select_question
from decision_agent.config import DEFAULT_SETTINGS, Settings
from decision_agent.consistency import PreparedCheck, prepare_check
from decision_agent.impact import predict_affected_for_topic
from decision_agent.llm.base import LLMProvider
from decision_agent.models import (
    Artifact,
    ConsistencyLabel,
    ConsistencyResult,
    ScopeKind,
)
from decision_agent.prefilter import PrefilterContext, local_verdict
from decision_agent.resolver import resolve_effective_decision
from decision_agent.storage import SQLiteStore
from decision_agent.uncertainty import estimate_many, estimate_uncertainty
from decision_agent.verdicts import VerdictStore


def describe_scope(artifact: Artifact) -> str:
    scope = artifact.scope
    if scope.scope_kind == ScopeKind.SCOPED:
        parts = [
            f"{k}={v}"
            for k, v in (
                ("release", scope.release),
                ("module", scope.module),
                ("user_group", scope.user_group),
                ("environment", scope.environment),
            )
            if v
        ]
        return ", ".join(parts)
    return scope.scope_kind.value


@dataclass
class AnalysisContext:
    """Shared reasoning operations used by both the Agent and the fixed Pipeline.

    The two orchestrators differ only in which operations they run, in which order, and when
    they stop. Every judgement goes through these methods.
    """

    store: SQLiteStore
    provider: LLMProvider
    topic: str
    artifacts: list[Artifact]
    as_of_date: str
    settings: Settings = field(default_factory=lambda: DEFAULT_SETTINGS)
    allow_clarify: bool = True
    affected: list[Artifact] | None = None
    results: dict[str, ConsistencyResult] = field(default_factory=dict)
    shared_evidence: list[str] = field(default_factory=list)
    history_seen: bool = False
    scope_checked: bool = False
    questions_asked: int = 0
    question_blocked: bool = False
    rechecked_ids: list[str] = field(default_factory=list)
    local_answers: int = 0
    # Optional reuse of earlier verdicts (None judges every line, as the research runs do).
    verdict_store: VerdictStore | None = None
    reused_lines: int = 0
    judged_lines: int = 0

    def __post_init__(self) -> None:
        self.store.clear_evidence(self.topic)

    def _by_id(self) -> dict[str, Artifact]:
        return {a.id: a for a in (self.affected or [])}

    def affected_ids(self) -> list[str]:
        return [a.id for a in (self.affected or [])]

    def unjudged_ids(self) -> list[str]:
        return [i for i in self.affected_ids() if i not in self.results]

    def uncertain_ids(self) -> list[str]:
        return [
            i
            for i in self.affected_ids()
            if i in self.results and self.results[i].label == ConsistencyLabel.UNCERTAIN
        ]

    def topic_decisions(self):
        return [d for d in self.store.list_decisions(self.topic) if d.date <= self.as_of_date]

    def has_scoped_relation(self) -> bool:
        return any(
            r.affected_scope.scope_kind == ScopeKind.SCOPED
            for r in self.store.list_relations(self.topic)
        )

    def predict(self) -> str:
        """Find the artifacts this topic's decisions should have changed."""
        self.affected = predict_affected_for_topic(
            self.store,
            self.topic,
            self.artifacts,
            self.provider,
            self.as_of_date,
            self.settings.k,
            verdict_store=self.verdict_store,
        )
        ids = ", ".join(self.affected_ids()) or "없음"
        return f"영향 대상 {len(self.affected)}건: {ids}"

    def history(self) -> str:
        """Read the topic's full decision history and keep it as evidence for later judgements."""
        decisions = self.topic_decisions()
        relations = self.store.list_relations(self.topic)
        parts = [f"{d.id} {d.date} {d.text}" for d in decisions]
        self.shared_evidence.append("HISTORY: " + " | ".join(parts))
        self.history_seen = True
        rel_text = ", ".join(f"{r.old_id}->{r.new_id}:{r.relation_type.value}" for r in relations)
        return f"결정 {len(decisions)}건 조회; 관계: {rel_text or '없음'}"

    def resolve_scopes(self, ids: list[str] | None = None) -> str:
        """Resolve which decision is effective for each affected artifact's own scope."""
        by_id = self._by_id()
        targets = [by_id[i] for i in (ids or self.affected_ids()) if i in by_id]
        lines = []
        for artifact in targets:
            resolved = resolve_effective_decision(
                self.store, self.topic, artifact.scope, self.as_of_date
            )
            decision_id = resolved.decision.id if resolved.decision else "-"
            lines.append(
                f"{artifact.id}[{describe_scope(artifact)}] -> {decision_id}({resolved.certainty.value})"
            )
        self.scope_checked = True
        return "; ".join(lines) or "대상 없음"

    def judge(self, ids: list[str]) -> str:
        """Judge the given affected artifacts with sampling-based uncertainty."""
        by_id = self._by_id()
        targets = [by_id[i] for i in ids if i in by_id]
        prepared = [
            prepare_check(
                self.store, self.topic, artifact, self.as_of_date, self.shared_evidence
            )
            for artifact in targets
        ]
        if self.settings.prefilter:
            context = PrefilterContext(self.topic_decisions())
            for item in prepared:
                verdict = local_verdict(item, context)
                if verdict is not None:
                    item.early = verdict
                    self.local_answers += 1
        for artifact, result in zip(targets, self._estimate(prepared), strict=True):
            self.results[artifact.id] = result
        counts = Counter(self.results[i].label.value for i in ids if i in self.results)
        summary = ", ".join(f"{label}={n}" for label, n in sorted(counts.items()))
        return f"{len(ids)}건 판단: {summary or '없음'}"

    def _estimate(self, prepared: list[PreparedCheck]) -> list[ConsistencyResult]:
        """estimate_many, reusing stored verdicts for lines judged before when a store is set.

        Stored and missing lines are split, only the missing ones go to the model, and both are
        merged back in the original order. Lines answered locally never touch the store.
        """
        if self.verdict_store is None:
            return estimate_many(prepared, self.provider, self.settings)
        stored = self.verdict_store.get_verdicts(prepared)
        missing = [i for i, item in enumerate(prepared) if item.early is not None or stored[i] is None]
        fresh = estimate_many([prepared[i] for i in missing], self.provider, self.settings)
        merged: list[ConsistencyResult | None] = list(stored)
        new_rows: list[tuple[PreparedCheck, ConsistencyResult]] = []
        for index, result in zip(missing, fresh, strict=True):
            merged[index] = result
            if prepared[index].early is None:
                new_rows.append((prepared[index], result))
        if new_rows:
            self.verdict_store.put_verdicts(new_rows)
        self.judged_lines += len(new_rows)
        self.reused_lines += len(prepared) - len(missing)
        return [
            result.model_copy(update={"artifact_id": item.artifact.id})
            for item, result in zip(prepared, merged, strict=True)
            if result is not None
        ]

    def _clarify_context(self, targets: list[str]) -> str:
        by_id = self._by_id()
        lines = ["현재 결정 이력:"]
        lines.extend(f"- {d.date} {d.text}" for d in self.topic_decisions())
        lines.append("판단이 불확실한 산출물:")
        for artifact_id in targets:
            artifact = by_id[artifact_id]
            lines.append(f"- {artifact.id} ({artifact.kind.value}): {artifact.text}")
        return "\n".join(lines)

    def clarify(self, ui: UserInterface | None) -> str:
        """Ask the best clarification question once, then re-judge only the uncertain targets."""
        if not self.allow_clarify:
            return "질문 기능이 비활성화되어 있습니다."
        if self.questions_asked >= self.settings.max_questions:
            self.question_blocked = True
            return "질문 한도에 도달했습니다."
        targets = self.uncertain_ids()
        if not targets:
            return "불확실한 항목이 없습니다."
        if ui is None:
            self.question_blocked = True
            return "사용자 입력 수단이 없어 질문하지 않았습니다."
        questions = generate_questions(
            self.provider, self._clarify_context(targets), targets, self.settings
        )
        selected = select_question(questions, self.settings)
        if selected is None:
            self.question_blocked = True
            best = max((q.expected_gain for q in questions), default=0.0)
            return f"기대 이득 {best:.2f}가 min_gain 미만이라 질문하지 않았습니다."
        answer = ui.ask(selected.text)
        self.questions_asked += 1
        for artifact_id in targets:
            self.store.add_evidence(self.topic, answer, artifact_id)
        by_id = self._by_id()
        for artifact_id in targets:
            if self.verdict_store is not None:
                prepared = prepare_check(
                    self.store, self.topic, by_id[artifact_id], self.as_of_date,
                    self.shared_evidence,
                )
                self.results[artifact_id] = self._estimate([prepared])[0]
            else:
                self.results[artifact_id] = estimate_uncertainty(
                    self.store,
                    self.topic,
                    by_id[artifact_id],
                    self.as_of_date,
                    self.provider,
                    self.settings,
                    extra_evidence=self.shared_evidence,
                )
            self.rechecked_ids.append(artifact_id)
        remaining = self.uncertain_ids()
        if set(remaining) == set(targets):
            self.question_blocked = True
        return (
            f"질문 '{selected.text}' 답변 '{answer}'; "
            f"재판단 {len(targets)}건, 해소 {len(targets) - len(remaining)}건"
        )

    def final_results(self) -> list[ConsistencyResult]:
        """Results in affected order; artifacts never judged are reported as UNCERTAIN."""
        final: list[ConsistencyResult] = []
        for artifact_id in self.affected_ids():
            if artifact_id in self.results:
                final.append(self.results[artifact_id])
            else:
                final.append(
                    ConsistencyResult(
                        artifact_id=artifact_id,
                        label=ConsistencyLabel.UNCERTAIN,
                        confidence=0.0,
                        evidence="판단 전에 종료되었습니다.",
                        expected_action="다시 점검하세요.",
                    )
                )
        return final
