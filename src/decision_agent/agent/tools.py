from __future__ import annotations

from typing import Any

from decision_agent.agent.core import AnalysisContext
from decision_agent.agent.interfaces import UserInterface

TOOL_SPECS: dict[str, str] = {
    "predict_affected": "이 topic의 결정이 바뀌면 영향을 받아야 할 산출물 후보를 찾는다. 인자 없음.",
    "get_decision_history": "이 topic의 결정 이력과 관계를 읽는다. 현재 결정문이 모호하거나 이전 결정을 참조할 때 쓴다. 인자 없음.",
    "resolve_scope": "산출물별로 어느 결정이 유효한지 범위(scope) 기준으로 확인한다. 인자: artifact_ids(목록 또는 'all').",
    "check_consistency": "산출물이 유효한 결정을 반영했는지 판단한다. 인자: artifact_ids(목록, 'unjudged', 'uncertain').",
    "search_additional_evidence": "지금까지 모은 사용자 답변 근거를 조회한다. 인자 없음.",
    "ask_user": "불확실한 항목을 해결할 확인 질문을 사용자에게 한 번 한다. 답변이 오면 불확실했던 항목만 다시 판단한다. 인자 없음.",
    "finalize": "현재 결과로 분석을 끝낸다. 인자 없음.",
}


def _ids(ctx: AnalysisContext, value: Any, default: str) -> list[str]:
    keyword = value if isinstance(value, str) else None
    if isinstance(value, list):
        known = set(ctx.affected_ids())
        return [str(i) for i in value if str(i) in known]
    keyword = keyword or default
    if keyword == "uncertain":
        return ctx.uncertain_ids()
    if keyword == "unjudged":
        return ctx.unjudged_ids()
    return ctx.affected_ids()


def execute_tool(
    ctx: AnalysisContext,
    name: str,
    arguments: dict[str, Any],
    ui: UserInterface | None,
) -> str:
    """Run one tool against the shared context and return a short observation string."""
    if name == "predict_affected":
        return ctx.predict()
    if name == "get_decision_history":
        return ctx.history()
    if name == "resolve_scope":
        return ctx.resolve_scopes(_ids(ctx, arguments.get("artifact_ids"), "all"))
    if name == "check_consistency":
        if ctx.affected is None:
            return "먼저 predict_affected로 영향 대상을 찾아야 합니다."
        ids = _ids(ctx, arguments.get("artifact_ids"), "unjudged")
        if not ids:
            return "판단할 대상이 없습니다."
        return ctx.judge(ids)
    if name == "search_additional_evidence":
        evidence = ctx.store.list_evidence(ctx.topic)
        return f"사용자 근거 {len(evidence)}건: " + " / ".join(evidence)
    if name == "ask_user":
        return ctx.clarify(ui)
    raise KeyError(name)
