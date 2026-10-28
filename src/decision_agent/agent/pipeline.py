from __future__ import annotations

import time

from decision_agent.agent.core import AnalysisContext
from decision_agent.agent.interfaces import CallbackUserInterface, UserInterface
from decision_agent.config import DEFAULT_SETTINGS, Settings
from decision_agent.llm.base import LLMProvider
from decision_agent.models import AgentStep, AgentTrace, Artifact
from decision_agent.storage import SQLiteStore
from decision_agent.verdicts import VerdictStore


def run_pipeline(
    store: SQLiteStore,
    provider: LLMProvider,
    topic: str,
    artifacts: list[Artifact],
    as_of_date: str,
    clarify: bool = False,
    ui: UserInterface | None = None,
    ask=None,
    settings: Settings = DEFAULT_SETTINGS,
    verdict_store: VerdictStore | None = None,
) -> AgentTrace:
    """Fixed-order control: predict, resolve scopes, judge, optionally clarify, finalize.

    It uses exactly the same reasoning operations as the Agent. It never reads the decision
    history on its own and never chooses what to do based on the state.
    """
    if ui is None and ask is not None:
        ui = CallbackUserInterface(ask)
    ctx = AnalysisContext(
        store=store,
        provider=provider,
        topic=topic,
        artifacts=artifacts,
        as_of_date=as_of_date,
        settings=settings,
        allow_clarify=clarify,
        verdict_store=verdict_store,
    )
    trace = AgentTrace()
    start = time.perf_counter()

    def record(name: str, observation: str) -> None:
        trace.steps.append(
            AgentStep(
                step_number=len(trace.steps) + 1,
                tool_name=name,
                arguments={},
                reason="고정 파이프라인 단계",
                observation_summary=observation,
            )
        )

    record("affected_prediction", ctx.predict())
    record("scope_resolution", ctx.resolve_scopes())
    record("consistency_detection", ctx.judge(ctx.affected_ids()))
    if clarify and ui is not None:
        while (
            ctx.uncertain_ids()
            and ctx.questions_asked < settings.max_questions
            and not ctx.question_blocked
        ):
            record("clarification", ctx.clarify(ui))
    record("finalize", "결과 확정")
    trace.questions_asked = ctx.questions_asked
    trace.affected_artifact_ids = ctx.affected_ids()
    trace.rechecked_artifact_ids = list(ctx.rechecked_ids)
    trace.final_results = ctx.final_results()
    trace.local_answers = ctx.local_answers
    trace.reused_lines = ctx.reused_lines
    trace.judged_lines = ctx.judged_lines
    trace.tool_calls = len(trace.steps)
    trace.elapsed_seconds = time.perf_counter() - start
    return trace
