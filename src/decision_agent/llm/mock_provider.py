from __future__ import annotations

import datetime
import json
import random
import re

from decision_agent.llm.base import LLMProvider


def _field(text: str, name: str) -> str:
    """Return the value of a single-line KEY=value field."""
    match = re.search(rf"^{name}=(.*)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _next_monday(date_text: str) -> str:
    day = datetime.date.fromisoformat(date_text)
    return (day + datetime.timedelta(days=7 - day.weekday())).isoformat()


def _scope(kind: str, release: str | None = None, module: str | None = None) -> dict:
    return {
        "scope_kind": kind,
        "release": release,
        "module": module,
        "user_group": None,
        "environment": None,
        "unmapped_terms": [],
    }


class MockProvider(LLMProvider):
    """Rule-based provider for wiring tests and the offline demo.

    It reads the same structured prompts a real model would see and applies keyword rules for
    the bundled demo project. Its outputs are not evidence of model quality.
    """

    def __init__(self, seed: int = 0) -> None:
        self.random = random.Random(seed)
        self.calls = 0

    def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
        json_schema: dict | None = None,
    ) -> str:
        self.calls += 1
        if "TASK:DECISION_EXTRACT" in system:
            return self._extract(user)
        if "TASK:RELATION_CLASSIFY" in system:
            return self._relation(user)
        if "TASK:IMPACT_SELECT" in system:
            return self._impact(user)
        if "TASK:CONSISTENCY" in system:
            return self._consistency(user, temperature)
        if "TASK:QUESTION_GENERATE" in system:
            return self._questions(user)
        if "TASK:QUESTION_GAIN" in system:
            return self._gain(user)
        if "TASK:TOOL_SELECT" in system:
            return self._tool_select(user)
        if "TASK:GUARD" in system:
            return self._guard(user)
        return json.dumps({"text": user[:80]}, ensure_ascii=False)

    def _extract(self, user: str) -> str:
        date = _field(user, "DATE") or "2026-01-01"
        text = _field(user, "TEXT")
        if "?" in text or "논의" in text:
            return json.dumps(
                {"is_decision": False, "text": text, "topic": "misc", "scope": _scope("GLOBAL")}
            )
        if "이메일 인증" in text:
            topic = "email_auth"
        elif "발표" in text:
            topic = "presentation_schedule"
        elif any(k in text for k in ("React", "Vue", "기존 방식")):
            topic = "frontend_framework"
        else:
            topic = "misc"
        if "MVP" in text or "이번 버전" in text:
            scope = _scope("SCOPED", "mvp", "login" if "로그인" in text else None)
        elif "Production" in text or "정식" in text:
            scope = _scope("SCOPED", "production")
        else:
            scope = _scope("GLOBAL")
        due = _next_monday(date) if "다음 주" in text else None
        reason = "일정 부족" if "일정 문제" in text else None
        return json.dumps(
            {
                "is_decision": True,
                "text": text,
                "topic": topic,
                "scope": scope,
                "reason": reason,
                "due_date": due,
            },
            ensure_ascii=False,
        )

    def _relation(self, user: str) -> str:
        new = _field(user, "NEW")
        scope = _scope("GLOBAL")
        if "로 변경" in new or "기존 방식" in new or "돌아간다" in new:
            relation = "SUPERSEDE"
        elif "취소" in new:
            relation = "CANCEL"
        elif "연기" in new or "뒤로" in new:
            relation = "POSTPONE"
        elif "MVP" in new and ("제외" in new or "미적용" in new):
            relation = "SCOPE_RESTRICT"
            scope = _scope("SCOPED", "mvp")
        else:
            relation = "NONE"
        return json.dumps(
            {"relation_type": relation, "affected_scope": scope, "evidence": "문장 의미 기준"},
            ensure_ascii=False,
        )

    def _impact(self, user: str) -> str:
        head, _, tail = user.partition("CANDIDATES:")
        groups = [
            (("이메일 인증",), ("이메일 인증",)),
            (("발표", "리허설", "자료 제출"), ("발표", "리허설", "자료 제출")),
            (("React", "Vue", "기존 방식"), ("React", "Vue", "프론트")),
        ]
        picked: list[str] = []
        for line in tail.splitlines():
            match = re.match(r"ID=(\S+) KIND=(\S+) TEXT=(.*)", line)
            if not match:
                continue
            artifact_id, _, text = match.groups()
            for triggers, keywords in groups:
                if any(t in head for t in triggers) and any(k in text for k in keywords):
                    picked.append(artifact_id)
                    break
        return json.dumps({"artifact_ids": picked}, ensure_ascii=False)

    def _consistency(self, user: str, temperature: float) -> str:
        decision = _field(user, "DECISION")
        artifact = _field(user, "ARTIFACT")
        artifact_date = _field(user, "ARTIFACT_DATE")
        due = _field(user, "DECISION_DUE")
        evidence = user.split("EVIDENCE:", 1)[1] if "EVIDENCE:" in user else ""
        label = self._judge(decision, artifact, artifact_date, due, evidence)
        if temperature > 0 and "SAMPLE_VARIANCE" in user and self.random.random() < 0.35:
            label = "INCONSISTENT" if label == "CONSISTENT" else "CONSISTENT"
        action = {
            "INCONSISTENT": "유효한 결정에 맞게 이 항목을 수정하거나 담당자에게 확인하세요.",
            "CONSISTENT": "조치가 필요하지 않습니다.",
            "UNCERTAIN": "영향 범위를 확인한 뒤 다시 점검하세요.",
        }[label]
        return json.dumps(
            {"label": label, "evidence": f"규칙 기반 판단: {decision}", "expected_action": action},
            ensure_ascii=False,
        )

    def _judge(
        self, decision: str, artifact: str, artifact_date: str, due: str, evidence: str
    ) -> str:
        answers = [
            line for line in evidence.splitlines() if line.strip() and "HISTORY:" not in line
        ]
        history = [line for line in evidence.splitlines() if "HISTORY:" in line]
        if "기존 방식" in decision:
            target = self._revert_target(answers, history)
            if target is None:
                return "UNCERTAIN"
            other = "Vue" if target == "React" else "React"
            if other in artifact and target not in artifact:
                return "INCONSISTENT"
            return "CONSISTENT"
        if "연기" in decision:
            joined = " ".join(answers)
            if not joined.strip():
                return "UNCERTAIN"
            affected = "모두" in joined or ("발표" in joined and "발표" in artifact)
            if affected and artifact_date not in ("", "-") and due not in ("", "-"):
                return "INCONSISTENT" if artifact_date < due else "CONSISTENT"
            return "CONSISTENT"
        if "Vue" in decision:
            return "INCONSISTENT" if "React" in artifact and "Vue" not in artifact else "CONSISTENT"
        if "React" in decision:
            return "INCONSISTENT" if "Vue" in artifact and "React" not in artifact else "CONSISTENT"
        if "이메일 인증" in decision or "이메일 인증" in artifact:
            if any(k in decision for k in ("제외", "취소")):
                in_scope = "MVP" not in decision or "mvp" in artifact.lower()
                active = any(k in artifact for k in ("진행", "필수", "예정", "개발"))
                removed = "삭제" in artifact
                return "INCONSISTENT" if in_scope and active and not removed else "CONSISTENT"
            return "CONSISTENT"
        return "CONSISTENT"

    @staticmethod
    def _revert_target(answers: list[str], history: list[str]) -> str | None:
        for source in (answers, history):
            text = " ".join(source)
            found = [(text.find(t), t) for t in ("React", "Vue") if t in text]
            if found:
                return min(found)[1]
        return None

    def _questions(self, user: str) -> str:
        if "발표" in user or "리허설" in user:
            questions = [
                "발표 일정만 변경된 것인가요, 리허설과 자료 제출 일정도 함께 변경되나요?",
                "다음 주의 정확한 날짜가 정해졌나요?",
                "일정 변경 사유가 있나요?",
            ]
        elif "기존 방식" in user:
            questions = [
                "'기존 방식'은 처음에 정한 React 사용을 뜻하나요?",
                "되돌리는 시점이 언제인가요?",
                "기존 방식으로 만든 산출물이 이미 있나요?",
            ]
        else:
            questions = [
                "이번 변경은 표시된 범위에만 적용되나요?",
                "관련 산출물도 함께 바뀌어야 하나요?",
                "변경 사유가 있나요?",
            ]
        return json.dumps({"questions": questions}, ensure_ascii=False)

    def _gain(self, user: str) -> str:
        question = _field(user, "QUESTION")
        high = any(k in question for k in ("변경된 것인가요", "뜻하나요", "표시된 범위"))
        return json.dumps({"gain": 0.45 if high else 0.05}, ensure_ascii=False)

    def _tool_select(self, user: str) -> str:
        state = dict(re.findall(r"^([A-Z_]+)=(.*)$", user, re.MULTILINE))

        def flag(key: str) -> bool:
            return state.get(key, "false") == "true"

        def has(key: str) -> bool:
            return state.get(key, "-") not in ("-", "")

        latest = state.get("LATEST_DECISION", "")
        vague = any(k in latest for k in ("기존 방식", "원래대로", "돌아간다"))
        can_ask = (
            has("UNCERTAIN")
            and flag("CLARIFY_ENABLED")
            and int(state.get("QUESTIONS_ASKED", "0")) < int(state.get("MAX_QUESTIONS", "0"))
            and not flag("QUESTION_BLOCKED")
        )

        def choose(tool: str, reason: str, arguments: dict | None = None) -> str:
            return json.dumps(
                {"tool": tool, "arguments": arguments or {}, "reason": reason}, ensure_ascii=False
            )

        if not flag("AFFECTED_KNOWN"):
            return choose("predict_affected", "영향 대상을 아직 찾지 않았습니다.")
        if vague and not flag("HISTORY_SEEN"):
            return choose(
                "get_decision_history", "최신 결정문이 이전 결정을 가리켜 이력이 필요합니다."
            )
        if flag("SCOPED_RELATION") and has("AFFECTED") and not flag("SCOPE_CHECKED"):
            return choose(
                "resolve_scope",
                "범위가 지정된 관계가 있어 산출물별 유효 결정을 확인합니다.",
                {"artifact_ids": "all"},
            )
        if has("UNJUDGED"):
            return choose(
                "check_consistency",
                "아직 판단하지 않은 산출물이 있습니다.",
                {"artifact_ids": "unjudged"},
            )
        if can_ask:
            return choose("ask_user", "판단이 불확실한 항목을 해결할 정보가 부족합니다.")
        return choose("finalize", "추가로 확인할 정보가 없거나 더 얻을 수 없습니다.")

    def _guard(self, user: str) -> str:
        task = _field(user, "TASK")
        decision = _field(user, "EFFECTIVE_DECISION")
        excludes = any(k in decision for k in ("제외", "취소", "중단", "하지 않"))
        if excludes and "이메일 인증" in task:
            severity = "warning"
        elif excludes and "이메일" in task:
            severity = "info"
        else:
            severity = "none"
        return json.dumps({"severity": severity, "rationale": "규칙 기반 판단"}, ensure_ascii=False)
