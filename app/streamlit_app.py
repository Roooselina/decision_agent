from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd
import streamlit as st

from decision_agent.agent.agent import run_agent
from decision_agent.agent.interfaces import NeedUserInput, ReplayUserInterface
from decision_agent.agent.pipeline import run_pipeline
from decision_agent.guard import check_new_task
from decision_agent.llm.anthropic_provider import AnthropicProvider
from decision_agent.llm.caching import CachingProvider
from decision_agent.llm.mock_provider import MockProvider
from decision_agent.models import ConsistencyLabel, DecisionScope, ScopeKind
from decision_agent.parsing.loader import infer_scope_from_text
from decision_agent.scope import normalize_scope
from decision_agent.score import consistency_score
from decision_agent.timeline import build
from decision_agent.workflow import ingest_project

DEFAULT_DIR = str(Path(__file__).resolve().parents[1] / "data" / "demo")
MODES = ["Pipeline", "Pipeline+질문", "Agent", "Agent (질문 없음)"]
LABEL_COLORS = {
    ConsistencyLabel.CONSISTENT.value: "background-color: #d7f0dc",
    ConsistencyLabel.INCONSISTENT.value: "background-color: #f6d3d3",
    ConsistencyLabel.UNCERTAIN.value: "background-color: #fbeccb",
}


def make_provider():
    """Use Anthropic when both environment variables are set; otherwise run the offline demo."""
    if os.getenv("ANTHROPIC_API_KEY") and os.getenv("DECISION_AGENT_MODEL"):
        return AnthropicProvider(), False
    return MockProvider(), True


@st.cache_resource(show_spinner="프로젝트를 분석하는 중입니다.")
def load_cached(project_dir: str):
    inner, demo = make_provider()
    provider = CachingProvider(inner)
    return provider, demo, ingest_project(project_dir, provider)


def scope_label(scope: DecisionScope) -> str:
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
    if scope.scope_kind == ScopeKind.UNKNOWN and scope.unmapped_terms:
        return "UNKNOWN (" + ", ".join(scope.unmapped_terms) + ")"
    return scope.scope_kind.value


def show_table(rows: list[dict], label_column: str | None = None) -> None:
    frame = pd.DataFrame(rows)
    if label_column and label_column in frame.columns:
        styler = frame.style
        mapper = getattr(styler, "map", None) or styler.applymap
        st.dataframe(mapper(lambda v: LABEL_COLORS.get(v, ""), subset=[label_column]))
    else:
        st.dataframe(frame)


def execute(mode: str, topic: str, as_of: str, answers: list[str], provider, proj):
    ui = ReplayUserInterface(answers)
    provider.begin_run()
    if mode.startswith("Agent"):
        return run_agent(
            proj.store,
            provider,
            topic,
            proj.artifacts,
            as_of,
            ui=ui,
            allow_clarify=(mode == "Agent"),
        )
    return run_pipeline(
        proj.store,
        provider,
        topic,
        proj.artifacts,
        as_of,
        clarify=(mode == "Pipeline+질문"),
        ui=ui,
    )


def render_trace(trace, proj) -> None:
    by_id = {a.id: a for a in proj.artifacts}
    st.metric("일관성 점수", consistency_score(trace.final_results))
    rows = []
    for result in trace.final_results:
        artifact = by_id[result.artifact_id]
        rows.append(
            {
                "산출물": result.artifact_id,
                "종류": artifact.kind.value,
                "내용": artifact.text,
                "scope": scope_label(artifact.scope),
                "유효 결정": result.effective_decision_id or "-",
                "판정": result.label.value,
                "일치도": round(result.confidence, 2),
                "근거": result.evidence,
                "조치": result.expected_action,
            }
        )
    show_table(rows, "판정")
    st.caption(
        f"질문 {trace.questions_asked}회, 도구 호출 {trace.tool_calls}회, "
        f"질문 후 재판단 {len(trace.rechecked_artifact_ids)}건, 소요 {trace.elapsed_seconds:.2f}초. "
        "일치도는 보정된 확률이 아니라 반복 판단의 일치 비율입니다."
    )
    with st.expander("도구 호출 trace"):
        show_table(
            [
                {
                    "단계": s.step_number,
                    "도구": s.tool_name,
                    "선택 이유": s.reason,
                    "관찰": s.observation_summary,
                }
                for s in trace.steps
            ]
        )


st.set_page_config(page_title="의사결정 변경 전파 불일치 탐지", layout="wide")
st.title("의사결정 변경 전파 불일치 탐지 AI Agent")

project_dir = st.sidebar.text_input("프로젝트 디렉터리", DEFAULT_DIR)
if st.sidebar.button("다시 불러오기"):
    load_cached.clear()
    st.session_state.pop("run", None)
try:
    provider, demo_mode, proj = load_cached(project_dir)
except (ValueError, OSError) as exc:
    st.error(f"프로젝트를 불러오지 못했습니다: {exc}")
    st.stop()
if demo_mode:
    st.info(
        "데모 모드: API 키가 없어 규칙 기반 MockProvider로 실행 중입니다. 결과는 성능 근거가 아닙니다."
    )

store = proj.store
topics = sorted({d.topic for d in store.list_decisions()})
latest_date = max((d.date for d in store.list_decisions()), default="2026-01-01")

tab1, tab2, tab3, tab4 = st.tabs(
    ["프로젝트 불러오기", "변경 점검", "새 Task 추가", "결정 타임라인"]
)

with tab1:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("회의 항목", len(proj.meetings))
    c2.metric("산출물", len(proj.artifacts))
    c3.metric("결정", len(proj.decisions))
    c4.metric("결정 관계", len(proj.relations))
    st.subheader("추출된 결정")
    show_table(
        [
            {
                "id": d.id,
                "날짜": d.date,
                "topic": d.topic,
                "상태": d.status.value,
                "scope": scope_label(d.scope),
                "결정": d.text,
            }
            for d in store.list_decisions()
        ]
    )
    st.subheader("결정 관계")
    show_table(
        [
            {
                "이전": r.old_id,
                "이후": r.new_id,
                "관계": r.relation_type.value,
                "적용 범위": scope_label(r.affected_scope),
                "근거": r.evidence,
            }
            for r in proj.relations
        ]
    )
    st.subheader("산출물과 scope 정규화 결과")
    unknown = [a for a in proj.artifacts if a.scope.scope_kind == ScopeKind.UNKNOWN]
    unmapped = [a for a in proj.artifacts if a.scope.unmapped_terms]
    if unmapped:
        st.warning(f"별칭 사전에 없어 정규화하지 못한 scope 표현이 있는 산출물: {len(unmapped)}건")
    if unknown:
        st.caption(
            f"scope를 알 수 없는 산출물 {len(unknown)}건. 범위가 지정된 결정이 있는 topic에서는 "
            "UNKNOWN을 전체 적용으로 가정하지 않고 불확실로 처리합니다."
        )
    show_table(
        [
            {"id": a.id, "종류": a.kind.value, "내용": a.text, "scope": scope_label(a.scope)}
            for a in proj.artifacts
        ]
    )

with tab2:
    left, mid, right = st.columns(3)
    mode = left.selectbox("실행 방식", MODES)
    topic = mid.selectbox("topic", topics)
    as_of_date = right.date_input("기준일", value=datetime.date.fromisoformat(latest_date))
    if st.button("분석 실행"):
        st.session_state["run"] = {
            "mode": mode,
            "topic": topic,
            "as_of": as_of_date.isoformat(),
            "answers": [],
        }
    run = st.session_state.get("run")
    if run:
        st.caption(f"실행 설정: {run['mode']} / {run['topic']} / 기준일 {run['as_of']}")
        trace = None
        try:
            trace = execute(run["mode"], run["topic"], run["as_of"], run["answers"], provider, proj)
        except NeedUserInput as need:
            st.warning("판단에 필요한 정보가 부족합니다. 확인 질문에 답해 주세요.")
            st.markdown(f"**{need.question}**")
            with st.form("clarify_form", clear_on_submit=True):
                answer = st.text_input("답변", key=f"answer_{len(run['answers'])}")
                submitted = st.form_submit_button("답변 제출")
            if submitted and answer.strip():
                run["answers"].append(answer.strip())
                st.rerun()
        if trace is not None:
            render_trace(trace, proj)

with tab3:
    st.write("새 Task가 제외되었거나 취소된 결정과 충돌하는지 추가하기 전에 점검합니다.")
    task_text = st.text_input("Task 텍스트", "MVP 이메일 인증 UI 개발")
    release_options = ["(자동)"] + sorted(proj.aliases.get("release", {}).keys())
    c1, c2, c3, c4 = st.columns(4)
    release = c1.selectbox("release", release_options)
    module = c2.text_input("module")
    user_group = c3.text_input("user_group")
    environment = c4.text_input("environment")
    topic_choice = st.selectbox("대상 topic", ["(자동)"] + topics)
    if st.button("사전 점검"):
        raw = {
            "release": None if release == "(자동)" else release,
            "module": module or None,
            "user_group": user_group or None,
            "environment": environment or None,
        }
        if any(raw.values()):
            task_scope = normalize_scope(raw, proj.config, proj.aliases)
        else:
            task_scope = infer_scope_from_text(task_text, proj.config, proj.aliases)
        provider.begin_run()
        warning = check_new_task(
            task_text,
            task_scope,
            store,
            provider,
            "9999-12-31",
            topic=None if topic_choice == "(자동)" else topic_choice,
        )
        st.session_state["pending_task"] = {
            "text": task_text,
            "scope": scope_label(task_scope),
            "severity": warning.severity if warning else "none",
            "message": warning.message if warning else "충돌하는 결정이 발견되지 않았습니다.",
        }
    pending = st.session_state.get("pending_task")
    if pending:
        st.caption(f"점검에 사용한 Task scope: {pending['scope']}")
        if pending["severity"] == "warning":
            st.warning(pending["message"])
        elif pending["severity"] == "info":
            st.info(pending["message"])
        else:
            st.success(pending["message"])
        if st.button("그래도 Task 추가"):
            st.session_state.setdefault("added_tasks", []).append(
                {"Task": pending["text"], "scope": pending["scope"]}
            )
            st.session_state.pop("pending_task", None)
            st.rerun()
    if st.session_state.get("added_tasks"):
        st.subheader("추가된 Task (세션 내 임시 목록)")
        show_table(st.session_state["added_tasks"])

with tab4:
    timeline_topic = st.selectbox("타임라인 topic", topics, key="timeline_topic")
    text, rows = build(store, timeline_topic)
    for row in rows:
        row["scope"] = scope_label(DecisionScope.model_validate(row["scope"]))
    st.text(text)
    show_table(rows)
