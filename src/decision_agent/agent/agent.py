from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from decision_agent.agent.core import AnalysisContext
from decision_agent.agent.interfaces import CallbackUserInterface, UserInterface
from decision_agent.agent.tools import TOOL_SPECS, execute_tool
from decision_agent.config import DEFAULT_SETTINGS, Settings
from decision_agent.llm.base import LLMProvider
from decision_agent.models import AgentStep, AgentTrace, Artifact
from decision_agent.storage import SQLiteStore

AGENT_SYSTEM = (
    """TASK:TOOL_SELECT
너는 프로젝트 결정이 바뀐 뒤 그 변경이 관련 산출물(요구사항, Task, 테스트, 일정)에 반영되었는지 점검하는 에이전트다.
매 단계에서 현재 상태(STATE)를 읽고 다음에 쓸 도구 하나를 고른다. 고정된 순서를 따르지 않는다.

매 단계 스스로 판단할 것:
- 영향 대상은 이미 찾았는가? (AFFECTED_KNOWN)
- 최신 결정문(LATEST_DECISION)이 무엇을 가리키는지 알 수 있는가? "기존 방식으로 돌아간다"처럼 이전 결정을 참조하면 먼저 get_decision_history로 이력을 읽는다.
- 범위가 지정된 관계가 있는가? (SCOPED_RELATION) 있으면 산출물마다 유효한 결정이 다르므로 resolve_scope로 확인한다.
- 판단하지 않은 산출물이 남았는가? (UNJUDGED) 있으면 check_consistency로 판단한다.
- 판단이 불확실한 산출물이 남았는가? (UNCERTAIN) 그리고 질문 한도가 남았고 질문이 막히지 않았다면 ask_user로 질문한다.
- 근거가 충분하고 더 알아낼 것이 없으면 불필요한 도구를 부르지 말고 바로 finalize한다.

도구 목록:
"""
    + "\n".join(f"- {name}: {desc}" for name, desc in TOOL_SPECS.items())
    + """

출력은 JSON 한 개만 낸다.
{"tool": "도구 이름", "arguments": {}, "reason": "왜 지금 이 도구가 필요한지 한 문장"}
"""
)


class ToolChoice(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


def _state_text(ctx: AnalysisContext, step: int, max_steps: int) -> str:
    decisions = ctx.topic_decisions()
    latest = decisions[-1] if decisions else None
    judged = ", ".join(f"{i}:{r.label.value}" for i, r in ctx.results.items())
    lines = [
        f"TOPIC={ctx.topic}",
        f"AS_OF={ctx.as_of_date}",
        f"LATEST_DECISION={latest.text if latest else '(없음)'}",
        f"LATEST_DECISION_DATE={latest.date if latest else '-'}",
        f"DECISION_COUNT={len(decisions)}",
        f"SCOPED_RELATION={str(ctx.has_scoped_relation()).lower()}",
        f"AFFECTED_KNOWN={str(ctx.affected is not None).lower()}",
        f"AFFECTED={','.join(ctx.affected_ids()) or '-'}",
        f"UNJUDGED={','.join(ctx.unjudged_ids()) or '-'}",
        f"JUDGED={judged or '-'}",
        f"UNCERTAIN={','.join(ctx.uncertain_ids()) or '-'}",
        f"HISTORY_SEEN={str(ctx.history_seen).lower()}",
        f"SCOPE_CHECKED={str(ctx.scope_checked).lower()}",
        f"CLARIFY_ENABLED={str(ctx.allow_clarify).lower()}",
        f"QUESTIONS_ASKED={ctx.questions_asked}",
        f"MAX_QUESTIONS={ctx.settings.max_questions}",
        f"QUESTION_BLOCKED={str(ctx.question_blocked).lower()}",
        f"STEP={step}/{max_steps}",
    ]
    return "STATE:\n" + "\n".join(lines)


def run_agent(
    store: SQLiteStore,
    provider: LLMProvider,
    topic: str,
    artifacts: list[Artifact],
    as_of_date: str = "9999-12-31",
    ui: UserInterface | None = None,
    ask=None,
    settings: Settings = DEFAULT_SETTINGS,
    allow_clarify: bool = True,
) -> AgentTrace:
    """Run the tool-selection loop. The LLM sees real state and picks each next tool."""
    if ui is None and ask is not None:
        ui = CallbackUserInterface(ask)
    ctx = AnalysisContext(
        store=store,
        provider=provider,
        topic=topic,
        artifacts=artifacts,
        as_of_date=as_of_date,
        settings=settings,
        allow_clarify=allow_clarify,
    )
    trace = AgentTrace()
    start = time.perf_counter()
    finished = False
    for step in range(1, settings.max_steps + 1):
        state = _state_text(ctx, step, settings.max_steps)
        choice = provider.complete_json(AGENT_SYSTEM, state, ToolChoice, 0.0)
        if choice.tool == "finalize":
            trace.steps.append(
                AgentStep(
                    step_number=step,
                    tool_name="finalize",
                    arguments=choice.arguments,
                    reason=choice.reason,
                    observation_summary="결과 확정",
                )
            )
            finished = True
            break
        if choice.tool not in TOOL_SPECS:
            observation = f"알 수 없는 도구 '{choice.tool}'입니다."
        else:
            observation = execute_tool(ctx, choice.tool, choice.arguments, ui)
        trace.steps.append(
            AgentStep(
                step_number=step,
                tool_name=choice.tool,
                arguments=choice.arguments,
                reason=choice.reason,
                observation_summary=observation,
            )
        )
    if not finished:
        trace.steps.append(
            AgentStep(
                step_number=settings.max_steps + 1,
                tool_name="finalize",
                arguments={},
                reason="max_steps 초과",
                observation_summary="현재 결과로 강제 종료",
            )
        )
    trace.questions_asked = ctx.questions_asked
    trace.affected_artifact_ids = ctx.affected_ids()
    trace.rechecked_artifact_ids = list(ctx.rechecked_ids)
    trace.final_results = ctx.final_results()
    trace.tool_calls = len(trace.steps)
    trace.elapsed_seconds = time.perf_counter() - start
    return trace
