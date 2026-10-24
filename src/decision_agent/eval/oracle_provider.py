from __future__ import annotations

import json
import re

from decision_agent.eval.dataset import EvalCase
from decision_agent.llm.base import LLMProvider
from decision_agent.llm.mock_provider import MockProvider, _field


class GoldOracleProvider(LLMProvider):
    """Test double that behaves like a perfect reader of one case.

    It answers every LLM task from the case's gold data, so it has no vocabulary limits.
    Running the real orchestration with it checks two things: the harness plumbing, and that
    the gold labels of a case are actually reachable through the resolver, the clarification
    gate, and the evidence flow. It says nothing about the quality of a real model.
    """

    def __init__(self, case: EvalCase) -> None:
        self.case = case
        self._by_text = {(d.date, d.text): d for d in case.gold_decisions}
        self._id_text = {d.id: d.text for d in case.gold_decisions}
        self._gold = case.gold_by_id()
        self._tool_policy = MockProvider()

    def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
        json_schema: dict | None = None,
    ) -> str:
        if "TASK:DECISION_EXTRACT" in system:
            return self._extract(user)
        if "TASK:RELATION_CLASSIFY" in system:
            return self._relation(user)
        if "TASK:IMPACT_SELECT" in system:
            return self._impact(user)
        if "TASK:CONSISTENCY" in system:
            return self._consistency(user)
        if "TASK:QUESTION_GENERATE" in system:
            return self._questions()
        if "TASK:QUESTION_GAIN" in system:
            return self._gain(user)
        if "TASK:TOOL_SELECT" in system:
            return self._tool_policy._tool_select(user)
        if "TASK:GUARD" in system:
            return json.dumps({"severity": "none", "rationale": "oracle"})
        return "{}"

    def _extract(self, user: str) -> str:
        decision = self._by_text.get((_field(user, "DATE"), _field(user, "TEXT")))
        if decision is None:
            return json.dumps(
                {
                    "is_decision": False,
                    "text": "",
                    "topic": "misc",
                    "scope": {"scope_kind": "GLOBAL"},
                }
            )
        return json.dumps(
            {
                "is_decision": True,
                "text": decision.text,
                "topic": decision.topic,
                "scope": decision.scope.model_dump(),
                "reason": decision.reason,
                "due_date": decision.due_date,
            },
            ensure_ascii=False,
        )

    def _relation(self, user: str) -> str:
        old, new = _field(user, "OLD"), _field(user, "NEW")
        for rel in self.case.gold_relations:
            if self._id_text[rel.old_id] == old and self._id_text[rel.new_id] == new:
                return json.dumps(
                    {
                        "relation_type": rel.relation_type.value,
                        "affected_scope": rel.affected_scope.model_dump(),
                        "evidence": rel.evidence,
                    },
                    ensure_ascii=False,
                )
        return json.dumps(
            {
                "relation_type": "NONE",
                "affected_scope": {"scope_kind": "GLOBAL"},
                "evidence": "none",
            }
        )

    def _impact(self, user: str) -> str:
        ids = re.findall(r"^ID=(\S+) ", user.partition("CANDIDATES:")[2], re.MULTILINE)
        picked = [i for i in ids if self._gold[i].affected]
        return json.dumps({"artifact_ids": picked})

    def _consistency(self, user: str) -> str:
        artifact_id = _field(user, "ARTIFACT_ID")
        gold = self._gold[artifact_id]
        evidence = user.split("EVIDENCE:", 1)[1] if "EVIDENCE:" in user else ""
        answers = [
            line
            for line in evidence.splitlines()
            if line.startswith("- ") and "HISTORY:" not in line and "RESOLVER_NOTE:" not in line
        ]
        label = "UNCERTAIN" if gold.needs_clarification and not answers else gold.label
        return json.dumps({"label": label, "evidence": "oracle", "expected_action": "oracle"})

    def _questions(self) -> str:
        keywords = [f.keywords[0] for f in self.case.user_facts] or ["확인"]
        questions = [f"{keywords[0]} 관련 내용을 알려주실 수 있나요?", "변경 사유가 있나요?"]
        return json.dumps({"questions": questions}, ensure_ascii=False)

    def _gain(self, user: str) -> str:
        question = _field(user, "QUESTION")
        useful = any(k in question for f in self.case.user_facts for k in f.keywords)
        return json.dumps({"gain": 0.5 if useful else 0.05})
