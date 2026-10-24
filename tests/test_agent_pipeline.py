from decision_agent.agent.agent import run_agent
from decision_agent.agent.core import AnalysisContext
from decision_agent.agent.interfaces import (
    CallbackUserInterface,
    NeedUserInput,
    ReplayUserInterface,
)
from decision_agent.agent.pipeline import run_pipeline
from decision_agent.config import Settings
from decision_agent.llm.mock_provider import MockProvider
from decision_agent.models import ConsistencyLabel, ConsistencyResult

END = "2026-12-31"
ANSWER = "세 일정 모두 다음 주로 변경"


class CountingProvider(MockProvider):
    """Counts consistency calls per artifact id and records every prompt."""

    def __init__(self):
        super().__init__()
        self.per_artifact = {}
        self.prompts = []

    def _consistency(self, user, temperature):
        self.prompts.append(user)
        for line in user.splitlines():
            if line.startswith("ARTIFACT_ID="):
                key = line.split("=", 1)[1]
                self.per_artifact[key] = self.per_artifact.get(key, 0) + 1
        return super()._consistency(user, temperature)


def tools(trace):
    return [s.tool_name for s in trace.steps]


def labels(trace):
    return {r.artifact_id: r.label for r in trace.final_results}


def test_agent_tool_paths_differ_by_situation(demo):
    provider, project = demo
    args = (project.store, provider)
    answer = lambda _: ANSWER
    paths = {
        "scope": tools(run_agent(*args, "email_auth", project.artifacts, END, ask=answer)),
        "clarify": tools(
            run_agent(*args, "presentation_schedule", project.artifacts, END, ask=answer)
        ),
        "history": tools(
            run_agent(*args, "frontend_framework", project.artifacts, END, ask=answer)
        ),
        "clear": tools(run_agent(*args, "frontend_framework", project.artifacts, "2026-09-15")),
    }
    assert paths["scope"] == ["predict_affected", "resolve_scope", "check_consistency", "finalize"]
    assert paths["clarify"] == ["predict_affected", "check_consistency", "ask_user", "finalize"]
    assert paths["history"] == [
        "predict_affected",
        "get_decision_history",
        "check_consistency",
        "finalize",
    ]
    assert paths["clear"] == ["predict_affected", "check_consistency", "finalize"]
    assert len({tuple(p) for p in paths.values()}) == 4


def test_pipeline_runs_the_same_steps_every_time(demo):
    provider, project = demo
    expected = ["affected_prediction", "scope_resolution", "consistency_detection", "finalize"]
    for topic in ("email_auth", "presentation_schedule", "frontend_framework"):
        trace = run_pipeline(project.store, provider, topic, project.artifacts, END)
        assert tools(trace) == expected


def test_clarification_answer_resolves_uncertain_items(demo):
    provider, project = demo
    trace = run_agent(
        project.store,
        provider,
        "presentation_schedule",
        project.artifacts,
        END,
        ask=lambda _: ANSWER,
    )
    assert trace.questions_asked == 1
    assert labels(trace) == {
        "SCHED_02": ConsistencyLabel.CONSISTENT,
        "SCHED_03": ConsistencyLabel.INCONSISTENT,
        "SCHED_04": ConsistencyLabel.INCONSISTENT,
    }
    assert sorted(trace.rechecked_artifact_ids) == ["SCHED_02", "SCHED_03", "SCHED_04"]


def test_without_clarification_the_items_stay_uncertain(demo):
    provider, project = demo
    pipeline = run_pipeline(
        project.store, provider, "presentation_schedule", project.artifacts, END
    )
    no_ask = run_agent(
        project.store,
        provider,
        "presentation_schedule",
        project.artifacts,
        END,
        allow_clarify=False,
    )
    for trace in (pipeline, no_ask):
        assert set(labels(trace).values()) == {ConsistencyLabel.UNCERTAIN}
        assert trace.questions_asked == 0
    with_question = run_pipeline(
        project.store,
        provider,
        "presentation_schedule",
        project.artifacts,
        END,
        clarify=True,
        ask=lambda _: ANSWER,
    )
    assert with_question.questions_asked == 1
    assert ConsistencyLabel.UNCERTAIN not in labels(with_question).values()


def test_history_lets_the_agent_resolve_what_the_pipeline_cannot(demo):
    provider, project = demo
    agent = run_agent(project.store, provider, "frontend_framework", project.artifacts, END)
    pipeline = run_pipeline(project.store, provider, "frontend_framework", project.artifacts, END)
    assert labels(agent) == {
        "REQUIREMENT_3": ConsistencyLabel.CONSISTENT,
        "TASK_03": ConsistencyLabel.CONSISTENT,
        "TASK_04": ConsistencyLabel.INCONSISTENT,
    }
    assert set(labels(pipeline).values()) == {ConsistencyLabel.UNCERTAIN}


def test_answers_do_not_leak_into_the_next_run(demo):
    provider, project = demo
    run_agent(
        project.store,
        provider,
        "presentation_schedule",
        project.artifacts,
        END,
        ask=lambda _: ANSWER,
    )
    again = run_agent(
        project.store,
        provider,
        "presentation_schedule",
        project.artifacts,
        END,
        allow_clarify=False,
    )
    assert set(labels(again).values()) == {ConsistencyLabel.UNCERTAIN}


def test_answer_text_reaches_the_judgement_prompt(demo):
    _, project = demo
    spy = CountingProvider()
    run_agent(
        project.store, spy, "presentation_schedule", project.artifacts, END, ask=lambda _: ANSWER
    )
    assert any(ANSWER in prompt for prompt in spy.prompts)


def test_only_uncertain_targets_are_rejudged_after_an_answer(demo):
    _, project = demo
    spy = CountingProvider()
    ctx = AnalysisContext(
        store=project.store,
        provider=spy,
        topic="presentation_schedule",
        artifacts=project.artifacts,
        as_of_date=END,
        settings=Settings(n=2),
    )
    ctx.affected = [a for a in project.artifacts if a.id in ("SCHED_02", "SCHED_03")]
    certain = ConsistencyResult(
        artifact_id="SCHED_02",
        label=ConsistencyLabel.CONSISTENT,
        confidence=1.0,
        evidence="e",
        expected_action="a",
    )
    uncertain = certain.model_copy(
        update={"artifact_id": "SCHED_03", "label": ConsistencyLabel.UNCERTAIN}
    )
    ctx.results = {"SCHED_02": certain, "SCHED_03": uncertain}
    ctx.clarify(CallbackUserInterface(lambda _: ANSWER))
    assert ctx.rechecked_ids == ["SCHED_03"]
    assert ctx.results["SCHED_02"] is certain
    assert "SCHED_02" not in spy.per_artifact
    assert spy.per_artifact["SCHED_03"] == 2


def test_min_gain_blocks_the_question(demo):
    provider, project = demo
    trace = run_agent(
        project.store,
        provider,
        "presentation_schedule",
        project.artifacts,
        END,
        ask=lambda _: ANSWER,
        settings=Settings(min_gain=1.0),
    )
    assert trace.questions_asked == 0
    assert tools(trace) == ["predict_affected", "check_consistency", "ask_user", "finalize"]
    assert "min_gain" in trace.steps[2].observation_summary
    assert set(labels(trace).values()) == {ConsistencyLabel.UNCERTAIN}


def test_max_questions_limit(demo):
    provider, project = demo
    asked = []

    def unhelpful(question):
        asked.append(question)
        return "모르겠습니다"

    trace = run_agent(
        project.store,
        provider,
        "presentation_schedule",
        project.artifacts,
        END,
        ask=unhelpful,
        settings=Settings(max_questions=1),
    )
    assert len(asked) == 1
    assert trace.questions_asked == 1


def test_max_steps_forces_finalize_and_reports_unjudged_as_uncertain(demo):
    provider, project = demo
    trace = run_agent(
        project.store,
        provider,
        "email_auth",
        project.artifacts,
        END,
        settings=Settings(max_steps=1),
    )
    assert tools(trace) == ["predict_affected", "finalize"]
    assert trace.steps[-1].reason == "max_steps 초과"
    assert trace.final_results
    assert {r.label for r in trace.final_results} == {ConsistencyLabel.UNCERTAIN}


def test_every_tool_call_is_recorded_with_a_reason(demo):
    provider, project = demo
    trace = run_agent(project.store, provider, "email_auth", project.artifacts, END)
    assert trace.tool_calls == len(trace.steps)
    assert all(step.reason for step in trace.steps)
    assert all(step.observation_summary for step in trace.steps)


def test_agent_prompt_contains_real_state(demo):
    _, project = demo
    seen = []

    class Recorder(MockProvider):
        def _tool_select(self, user):
            seen.append(user)
            return super()._tool_select(user)

    run_agent(project.store, Recorder(), "email_auth", project.artifacts, END)
    assert "LATEST_DECISION=일정 문제로 이번 MVP에서는 이메일 인증을 제외한다." in seen[0]
    assert "AFFECTED_KNOWN=false" in seen[0]
    assert "AFFECTED=REQUIREMENT_1" in seen[-1]


def test_replay_user_interface():
    ui = ReplayUserInterface(["첫 답변"])
    assert ui.ask("질문 1") == "첫 답변"
    try:
        ui.ask("질문 2")
    except NeedUserInput as need:
        assert need.question == "질문 2"
    else:
        raise AssertionError("NeedUserInput was not raised")
