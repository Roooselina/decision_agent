from decision_agent.agent.agent import run_agent
from decision_agent.models import ConsistencyLabel, DecisionStatus

END = "2026-12-31"


def label_map(trace):
    return {r.artifact_id: r.label for r in trace.final_results}


def test_scenario1_scope_restriction(demo):
    provider, project = demo
    store = project.store
    assert store.get_decision("D01").status == DecisionStatus.ACTIVE
    assert store.get_decision("D02").status == DecisionStatus.SUPERSEDED
    trace = run_agent(store, provider, "email_auth", project.artifacts, END)
    assert label_map(trace) == {
        "REQUIREMENT_1": ConsistencyLabel.INCONSISTENT,
        "TASK_01": ConsistencyLabel.INCONSISTENT,
        "TEST_1": ConsistencyLabel.INCONSISTENT,
        "SCHED_01": ConsistencyLabel.CONSISTENT,
        "TASK_02": ConsistencyLabel.CONSISTENT,
        "REQUIREMENT_2": ConsistencyLabel.CONSISTENT,
        "TEST_2": ConsistencyLabel.CONSISTENT,
    }


def test_scenario5_clear_supersede_finishes_early(demo):
    provider, project = demo
    trace = run_agent(
        project.store, provider, "frontend_framework", project.artifacts, "2026-09-15"
    )
    assert label_map(trace) == {
        "REQUIREMENT_3": ConsistencyLabel.INCONSISTENT,
        "TASK_03": ConsistencyLabel.INCONSISTENT,
        "TASK_04": ConsistencyLabel.CONSISTENT,
    }
    assert trace.questions_asked == 0
    assert [s.tool_name for s in trace.steps] == [
        "predict_affected",
        "check_consistency",
        "finalize",
    ]


def test_relations_extracted_from_the_demo_meetings(demo):
    _, project = demo
    pairs = {(r.relation_type.value, r.affected_scope.release) for r in project.relations}
    assert ("SCOPE_RESTRICT", "mvp") in pairs
    assert ("SUPERSEDE", None) in pairs
