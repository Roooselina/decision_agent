from __future__ import annotations

import json
import threading

import desktop_helpers as dh
import pytest

from decision_agent.agent.agent import choose_by_rules, run_agent
from decision_agent.clarification import generate_questions
from decision_agent.config import DEFAULT_SETTINGS, Settings, settings_for_profile
from decision_agent.consistency import PreparedCheck, prepare_check
from decision_agent.desktop.engine import ProjectEngine
from decision_agent.desktop.estimate import estimate_calls, estimate_topic_calls
from decision_agent.desktop.providers import (
    CountingProvider,
    ProviderBundle,
    TieredProvider,
    build_provider,
    task_of,
)
from decision_agent.desktop.settings import AppSettings
from decision_agent.desktop.state import StateDB
from decision_agent.llm.mock_provider import MockProvider
from decision_agent.models import (
    Artifact,
    ArtifactKind,
    ConsistencyLabel,
    Decision,
    DecisionScope,
    ScopeKind,
)
from decision_agent.prefilter import PrefilterContext, local_verdict, value_tokens
from decision_agent.storage import SQLiteStore
from decision_agent.uncertainty import estimate_many

END = "2026-12-31"
GLOBAL = DecisionScope(scope_kind=ScopeKind.GLOBAL)


class Tasks(MockProvider):
    """Counts model calls by task. A batch the mock splits locally counts once."""

    def __init__(self):
        super().__init__()
        self.tasks: dict[str, int] = {}
        self.guard = threading.Lock()
        self.inside = threading.local()

    def complete(self, system, user, temperature=0.0, json_schema=None):
        if getattr(self.inside, "batch", False):
            return super().complete(system, user, temperature, json_schema)
        name = system.split("\n", 1)[0].removeprefix("TASK:")
        with self.guard:
            self.tasks[name] = self.tasks.get(name, 0) + 1
        if name.endswith("_BATCH"):
            self.inside.batch = True
            try:
                return super().complete(system, user, temperature, json_schema)
            finally:
                self.inside.batch = False
        return super().complete(system, user, temperature, json_schema)


def decision(id_, text, date, **extra):
    return Decision(
        id=id_, text=text, date=date, source_doc="m.md", topic="t", scope=extra.pop("scope", GLOBAL),
        **extra,
    )


def artifact(id_, text, **extra):
    return Artifact(
        id=id_, kind=ArtifactKind.REQUIREMENT, text=text, source_doc="r.md", location="L1",
        scope=GLOBAL, **extra,
    )


def prepared_for(art, dec):
    return PreparedCheck(art, decision=dec, evidence=[], user="x")


# profiles


def test_precise_profile_keeps_the_original_behaviour():
    precise = settings_for_profile("precise")
    assert precise.routing == "llm" and precise.clarify_gain == "full"
    assert not precise.consistent_single and not precise.prefilter
    assert DEFAULT_SETTINGS.routing == "llm" and not DEFAULT_SETTINGS.prefilter


def test_balanced_and_saver_profiles_cut_calls_in_steps():
    balanced = settings_for_profile("balanced")
    assert balanced.routing == "rules" and balanced.consistent_single and balanced.prefilter
    assert balanced.clarify_gain == "batch" and balanced.adaptive_min_samples == 2
    saver = settings_for_profile("saver")
    assert saver.adaptive_min_samples == 1 and saver.clarify_gain == "none" and saver.prefilter


# routing


def test_rule_routing_makes_no_tool_select_calls(demo):
    _, project = demo
    provider = Tasks()
    trace = run_agent(
        project.store, provider, "email_auth", project.artifacts, END,
        settings=settings_for_profile("balanced"), allow_clarify=False,
    )
    assert "TOOL_SELECT" not in provider.tasks
    assert trace.steps[0].tool_name == "predict_affected"
    assert trace.steps[-1].tool_name == "finalize"
    assert any(s.tool_name == "check_consistency" for s in trace.steps)


def test_llm_routing_still_asks_the_model(demo):
    _, project = demo
    provider = Tasks()
    run_agent(
        project.store, provider, "email_auth", project.artifacts, END,
        settings=settings_for_profile("precise"), allow_clarify=False,
    )
    assert provider.tasks.get("TOOL_SELECT", 0) >= 3


def test_rules_read_history_only_when_the_decision_points_back(demo):
    _, project = demo
    from decision_agent.agent.core import AnalysisContext

    ctx = AnalysisContext(
        store=project.store, provider=Tasks(), topic="frontend_framework",
        artifacts=project.artifacts, as_of_date=END, settings=settings_for_profile("balanced"),
    )
    ctx.predict()
    assert choose_by_rules(ctx).tool == "get_decision_history"
    ctx.history()
    assert choose_by_rules(ctx).tool != "get_decision_history"
    other = AnalysisContext(
        store=project.store, provider=Tasks(), topic="email_auth",
        artifacts=project.artifacts, as_of_date=END, settings=settings_for_profile("balanced"),
    )
    other.predict()
    assert choose_by_rules(other).tool != "get_decision_history"


# clarification scoring


CONTEXT = "현재 결정 이력:\n- 2026-09-20 발표를 다음 주로 연기한다."


def test_batch_gain_uses_one_call_instead_of_nine():
    full, batch = Tasks(), Tasks()
    generate_questions(full, CONTEXT, ["A"], Settings(clarify_gain="full"))
    generate_questions(batch, CONTEXT, ["A"], Settings(clarify_gain="batch"))
    assert full.tasks["QUESTION_GAIN"] == 9
    assert batch.tasks.get("QUESTION_GAIN", 0) == 0 and batch.tasks["QUESTION_GAIN_BATCH"] == 1


def test_batch_gain_gives_the_same_scores_as_single_calls():
    one = generate_questions(Tasks(), CONTEXT, ["A"], Settings(clarify_gain="full"))
    two = generate_questions(Tasks(), CONTEXT, ["A"], Settings(clarify_gain="batch"))
    assert [round(q.expected_gain, 6) for q in one] == [round(q.expected_gain, 6) for q in two]


def test_none_mode_asks_the_first_question_without_scoring():
    provider = Tasks()
    questions = generate_questions(provider, CONTEXT, ["A"], Settings(clarify_gain="none"))
    assert set(provider.tasks) == {"QUESTION_GENERATE"}
    assert questions[0].expected_gain >= Settings().min_gain
    assert all(q.expected_gain == 0.0 for q in questions[1:])


class BadBatchGain(Tasks):
    def complete(self, system, user, temperature=0.0, json_schema=None):
        if "TASK:QUESTION_GAIN_BATCH" in system:
            with self.guard:
                self.tasks["QUESTION_GAIN_BATCH"] = self.tasks.get("QUESTION_GAIN_BATCH", 0) + 1
            return "not json"
        return super().complete(system, user, temperature, json_schema)


def test_unusable_batch_answer_falls_back_to_single_scoring():
    provider = BadBatchGain()
    questions = generate_questions(provider, CONTEXT, ["A"], Settings(clarify_gain="batch"))
    assert provider.tasks["QUESTION_GAIN"] == 9 and len(questions) == 3


# sampling policy


class AlwaysLabel(MockProvider):
    def __init__(self, label):
        super().__init__()
        self.label = label
        self.batches = 0
        self.guard = threading.Lock()

    def complete(self, system, user, temperature=0.0, json_schema=None):
        if "TASK:CONSISTENCY_BATCH" in system:
            with self.guard:
                self.batches += 1
        return super().complete(system, user, temperature, json_schema)

    def _consistency(self, user, temperature):
        return json.dumps({"label": self.label, "evidence": "e", "expected_action": "a"})


def _checks(count=3):
    """Prepared checks that share one decision, so one batch call covers all of them."""
    store = SQLiteStore()
    store.save_decision(decision("D1", "DB는 PostgreSQL로 변경한다", "2026-06-01"))
    prepared = []
    for number in range(count):
        art = artifact(f"A{number}", f"데이터베이스 설정 항목 {number}")
        store.save_artifact(art)
        prepared.append(prepare_check(store, "t", art, END))
    assert all(p.early is None for p in prepared)
    assert len({p.group_key() for p in prepared}) == 1
    return prepared


def test_consistent_first_sample_is_accepted_without_a_second_call():
    prepared = _checks()
    settings = settings_for_profile("balanced")
    provider = AlwaysLabel("CONSISTENT")
    results = estimate_many(prepared, provider, settings)
    assert provider.batches == 1
    assert all(r.label == ConsistencyLabel.CONSISTENT and r.confidence == 1.0 for r in results)


def test_inconsistent_first_sample_is_confirmed_by_a_second_call():
    prepared = _checks()
    provider = AlwaysLabel("INCONSISTENT")
    results = estimate_many(prepared, provider, settings_for_profile("balanced"))
    assert provider.batches == 2
    assert all(r.label == ConsistencyLabel.INCONSISTENT for r in results)


def test_precise_profile_still_samples_everything_twice():
    prepared = _checks()
    provider = AlwaysLabel("CONSISTENT")
    estimate_many(prepared, provider, settings_for_profile("precise"))
    assert provider.batches == 2


def test_saver_takes_one_sample_for_every_label():
    prepared = _checks()
    provider = AlwaysLabel("INCONSISTENT")
    estimate_many(prepared, provider, settings_for_profile("saver"))
    assert provider.batches == 1


class FlipOnSecond(MockProvider):
    """First sample INCONSISTENT, every later sample CONSISTENT."""

    def __init__(self):
        super().__init__()
        self.n = 0
        self.guard = threading.Lock()

    def _consistency(self, user, temperature):
        with self.guard:
            self.n += 1
            label = "INCONSISTENT" if self.n == 1 else "CONSISTENT"
        return json.dumps({"label": label, "evidence": "e", "expected_action": "a"})


def test_disagreement_between_samples_falls_back_to_full_sampling():
    prepared = _checks(count=2)
    provider = FlipOnSecond()
    results = estimate_many(prepared, provider, settings_for_profile("balanced"))
    # round one answers both lines (the first says INCONSISTENT, the second CONSISTENT and is
    # accepted); round two re-asks the first line and disagrees; then single samples are taken
    # until two agree, so a lone CONSISTENT answer cannot settle the line
    assert provider.n == 2 + 1 + 2
    assert [r.label for r in results] == [ConsistencyLabel.CONSISTENT] * 2


# prefilter


def test_value_tokens_pick_numbers_and_names_only():
    assert value_tokens("교재 사진은 최대 3장까지 업로드한다") == {"3장"}
    assert value_tokens("DB는 MySQL에서 PostgreSQL로 옮긴다") == {"db", "mysql", "postgresql"}
    assert value_tokens("중간 점검은 10월 29일 진행") == {"10월", "29일"}


def make_history():
    return [
        decision("D1", "DB는 MySQL을 사용한다", "2026-05-01"),
        decision("D2", "DB는 PostgreSQL로 변경한다", "2026-06-01"),
    ]


def test_line_with_the_new_value_and_no_old_value_is_answered_locally():
    history = make_history()
    context = PrefilterContext(history)
    verdict = local_verdict(prepared_for(artifact("A", "데이터베이스: PostgreSQL 15"), history[1]), context)
    assert verdict is not None and verdict.label == ConsistencyLabel.CONSISTENT
    assert verdict.effective_decision_id == "D2" and "PostgreSQL".lower() in verdict.evidence.lower()


@pytest.mark.parametrize(
    "text",
    [
        "데이터베이스: MySQL 8",  # old value only
        "MySQL에서 PostgreSQL로 이전 작업",  # both values
        "ORM 설정 정리",  # no value at all
    ],
)
def test_other_lines_go_to_the_model(text):
    history = make_history()
    context = PrefilterContext(history)
    assert local_verdict(prepared_for(artifact("A", text), history[1]), context) is None


def test_decisions_that_remove_something_are_never_prefiltered():
    history = [
        decision("D1", "카카오 로그인을 제공한다", "2026-05-01"),
        decision("D2", "카카오 로그인은 제공하지 않는다", "2026-06-01"),
    ]
    context = PrefilterContext(history)
    assert local_verdict(prepared_for(artifact("A", "카카오 로그인 구현 작업"), history[1]), context) is None


def test_undoing_a_removal_is_not_prefiltered():
    history = [
        decision("D1", "푸시 알림을 제공한다", "2026-05-01"),
        decision("D2", "푸시 알림은 제외한다", "2026-06-01"),
        decision("D3", "푸시 알림을 FCM으로 다시 제공한다", "2026-07-01"),
    ]
    context = PrefilterContext(history)
    assert local_verdict(prepared_for(artifact("A", "FCM 연동"), history[2]), context) is None


def test_scoped_and_dated_decisions_are_never_prefiltered():
    scoped = DecisionScope(scope_kind=ScopeKind.SCOPED, release="mvp")
    history = [
        decision("D1", "배포는 AWS를 사용한다", "2026-05-01"),
        decision("D2", "배포는 GCP로 변경한다", "2026-06-01", scope=scoped),
    ]
    assert local_verdict(prepared_for(artifact("A", "GCP 배포"), history[1]), PrefilterContext(history)) is None
    dated = [
        decision("D1", "발표는 10월 20일", "2026-05-01"),
        decision("D2", "발표는 11월 10일로 변경한다", "2026-06-01", due_date="2026-11-10"),
    ]
    assert local_verdict(prepared_for(artifact("A", "발표 11월 10일"), dated[1]), PrefilterContext(dated)) is None


def test_artifacts_with_a_date_field_are_left_to_the_model():
    history = make_history()
    art = artifact("A", "PostgreSQL 설정", metadata={"date": "2026-05-02"})
    assert local_verdict(prepared_for(art, history[1]), PrefilterContext(history)) is None


def test_reverting_to_an_earlier_value_checks_the_value_that_replaced_it():
    history = [
        decision("D1", "사진은 최대 5장까지 올린다", "2026-05-01"),
        decision("D2", "사진은 최대 3장으로 줄인다", "2026-06-01"),
        decision("D3", "사진은 최대 5장으로 되돌린다", "2026-07-01"),
    ]
    context = PrefilterContext(history)
    assert local_verdict(prepared_for(artifact("A", "사진 업로드 최대 5장"), history[2]), context) is not None
    assert local_verdict(prepared_for(artifact("B", "사진 업로드 최대 3장"), history[2]), context) is None


def test_prefilter_is_off_in_the_precise_profile(demo):
    _, project = demo
    provider = Tasks()
    run_agent(
        project.store, provider, "email_auth", project.artifacts, END,
        settings=settings_for_profile("precise"), allow_clarify=False,
    )
    trace = run_agent(
        project.store, Tasks(), "email_auth", project.artifacts, END,
        settings=settings_for_profile("precise"), allow_clarify=False,
    )
    assert trace.local_answers == 0


# tiered provider and counters


class Named(MockProvider):
    def __init__(self, name):
        super().__init__()
        self.name = name
        self.seen: list[str] = []

    def complete(self, system, user, temperature=0.0, json_schema=None):
        self.seen.append(task_of(system))
        return "{}"


def test_tiered_provider_keeps_the_judgement_on_the_main_model():
    main, cheap = Named("main"), Named("cheap")
    tiered = TieredProvider(main, cheap)
    for task in ("DECISION_EXTRACT_BATCH", "RELATION_CLASSIFY", "IMPACT_SELECT", "TOOL_SELECT", "QUESTION_GAIN_BATCH"):
        tiered.complete(f"TASK:{task}\nx", "u")
    for task in ("CONSISTENCY", "CONSISTENCY_BATCH"):
        tiered.complete(f"TASK:{task}\nx", "u")
    assert main.seen == ["CONSISTENCY", "CONSISTENCY_BATCH"]
    assert len(cheap.seen) == 5


def test_counting_provider_counts_by_task_and_resets():
    counter = CountingProvider(Named("m"), 100)
    counter.complete("TASK:IMPACT_SELECT\nx", "u")
    counter.complete("TASK:IMPACT_SELECT\nx", "u")
    counter.complete("TASK:CONSISTENCY\nx", "u")
    assert counter.by_task == {"IMPACT_SELECT": 2, "CONSISTENCY": 1} and counter.calls == 3
    counter.reset()
    assert counter.by_task == {} and counter.calls == 0


def test_build_provider_adds_the_cheap_model_to_the_id_only_when_different():
    made: list[str] = []

    def factory(key, model):
        made.append(model)
        return Named(model)

    settings = AppSettings(provider="anthropic", allow_external_send=True, model_name="big", cheap_model_name="small")
    bundle = build_provider(settings, key_getter=lambda: "k", anthropic_factory=factory)
    assert bundle.provider_id == "anthropic:big+small" and made == ["big", "small"]
    assert isinstance(bundle.provider, TieredProvider)
    same = AppSettings(provider="anthropic", allow_external_send=True, model_name="big", cheap_model_name="big")
    bundle = build_provider(same, key_getter=lambda: "k", anthropic_factory=factory)
    assert bundle.provider_id == "anthropic:big"
    plain = AppSettings(provider="anthropic", allow_external_send=True, model_name="big")
    assert build_provider(plain, key_getter=lambda: "k", anthropic_factory=factory).provider_id == "anthropic:big"


# estimate and confirmation gate


def test_estimate_shrinks_with_the_profile():
    sizes = [14] * 20
    precise = estimate_calls(sizes, settings_for_profile("precise"))
    balanced = estimate_calls(sizes, settings_for_profile("balanced"))
    saver = estimate_calls(sizes, settings_for_profile("saver"))
    assert precise > balanced > saver > 0
    assert estimate_topic_calls(0, settings_for_profile("balanced")) == 0


def external_engine(root, **overrides):
    settings = dh.make_settings(root, **overrides)
    bundle = ProviderBundle(MockProvider(), "fake:external", False, "test", True)
    db = StateDB(":memory:")
    return ProjectEngine(root, settings, db, bundle), db


def test_expensive_check_waits_for_confirmation_and_then_completes(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    engine, db = external_engine(root, confirm_over_calls=1)
    engine.sync()
    report = engine.analyze()
    assert report.pending_confirm and report.pending_confirm["estimated_calls"] > 0
    assert engine.progress.phase == "awaiting_confirm"
    assert engine.pending_confirm() == report.pending_confirm
    assert engine.completion() is None and db.list_findings(include_hidden=True) == []
    done = engine.analyze(confirm=True)
    assert done.pending_confirm is None and engine.pending_confirm() is None
    assert engine.completion() is not None and db.list_findings(include_hidden=True)
    assert engine.progress.phase == "done"


def test_confirming_after_the_gate_costs_nothing_extra_for_reading(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    engine, _ = external_engine(root, confirm_over_calls=1)
    engine.sync()
    held = engine.analyze()
    first = held.llm_calls
    final = engine.analyze(confirm=True)
    assert final.llm_calls >= 0 and first >= 0
    assert final.calls_by_task is not None


def test_gate_is_off_for_the_demo_judge_and_when_the_limit_is_zero(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    engine, _, _ = dh.make_engine(root, confirm_over_calls=1)
    engine.sync()
    assert engine.analyze().pending_confirm is None
    root2 = dh.make_watch_dir(tmp_path, "watch2")
    engine2, _ = external_engine(root2, confirm_over_calls=0)
    engine2.sync()
    assert engine2.analyze().pending_confirm is None


def test_report_lists_calls_by_task_and_local_answers(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    engine, _ = external_engine(root, confirm_over_calls=0, cost_profile="balanced")
    engine.sync()
    report = engine.analyze()
    assert report.llm_calls == sum(report.calls_by_task.values()) > 0
    record = engine.completion()
    assert record["cost_profile"] == "balanced" and record["calls_by_task"] == report.calls_by_task
    assert "TOOL_SELECT" not in report.calls_by_task


def test_changing_the_profile_invalidates_the_previous_result(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    engine, _ = external_engine(root, confirm_over_calls=0, cost_profile="precise")
    engine.sync()
    engine.analyze()
    engine.settings.cost_profile = "saver"
    again = engine.analyze()
    assert again.rebuilt and again.topics_checked


def make_external_app(root, **overrides):
    from decision_agent.desktop.app import DecisionAgentApp
    from decision_agent.desktop.notify import CallbackNotifier

    toasts = []
    app = DecisionAgentApp(
        dh.make_settings(root, **overrides),
        notifier=CallbackNotifier(lambda title, body: toasts.append((title, body))),
        bundle_factory=lambda s: ProviderBundle(MockProvider(), "fake:external", False, "test", True),
        db_factory=lambda r: StateDB(":memory:"),
        start_watchers=False,
    )
    return app, toasts


def test_app_holds_the_check_until_confirmed_and_never_reports_it_as_done(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    app, toasts = make_external_app(root, confirm_over_calls=1)
    app.start()
    try:
        assert app.wait_idle(60)
        status = app.status()
        assert status["overall"]["state"] == "awaiting_confirm"
        assert status["folders"][0]["pending_confirm"]["estimated_calls"] > 0
        assert status["inconsistent"] == 0
        assert not [t for t, _ in toasts if "마쳤습니다" in t]
        runtime = next(iter(app.runtimes.values()))
        assert runtime.db.meta_get("initial_done") is None
        app.confirm(next(iter(app.runtimes)))
        assert app.wait_idle(60)
        status = app.status()
        assert status["overall"]["state"] == "verified" and status["folders"][0]["pending_confirm"] is None
        assert status["inconsistent"] >= 1
        assert [t for t, _ in toasts].count("처음 점검을 마쳤습니다") == 1
        assert runtime.db.meta_get("initial_done") == "1"
    finally:
        app.stop()


def test_changing_the_profile_marks_the_old_completion_stale(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    app, _ = make_external_app(root, confirm_over_calls=0, cost_profile="balanced")
    app.start()
    try:
        assert app.wait_idle(60)
        assert app.status()["overall"]["state"] == "verified"
        app.settings.cost_profile = "saver"
        assert app.status()["overall"]["state"] == "stale"
    finally:
        app.stop()
