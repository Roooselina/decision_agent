from decision_agent.guard import check_new_task
from decision_agent.models import DecisionScope, ScopeKind
from decision_agent.timeline import build

MVP = DecisionScope(scope_kind=ScopeKind.SCOPED, release="mvp")
PROD = DecisionScope(scope_kind=ScopeKind.SCOPED, release="production")
UNKNOWN = DecisionScope(scope_kind=ScopeKind.UNKNOWN)
END = "2026-12-31"


def test_guard_is_scope_aware(demo):
    provider, project = demo
    warning = check_new_task("MVP 이메일 인증 UI 개발", MVP, project.store, provider, END)
    assert warning is not None
    assert warning.severity == "warning"
    assert "2026-09-10" in warning.message
    assert "일정 문제" in warning.message
    assert check_new_task("Production 이메일 인증 설계", PROD, project.store, provider, END) is None


def test_guard_ambiguous_overlap_is_info_not_warning(demo):
    provider, project = demo
    warning = check_new_task("이메일 OTP 로그인 화면 구현", MVP, project.store, provider, END)
    assert warning is not None
    assert warning.severity == "info"


def test_guard_unrelated_task_has_no_warning(demo):
    provider, project = demo
    assert check_new_task("로고 디자인 수정", MVP, project.store, provider, END) is None


def test_guard_unknown_scope_asks_for_scope(demo):
    provider, project = demo
    warning = check_new_task("이메일 인증 UI 개발", UNKNOWN, project.store, provider, END)
    assert warning is not None
    assert warning.severity == "info"
    assert "범위" in warning.message


def test_timeline_note_marks_scoped_change(demo):
    _, project = demo
    text, rows = build(project.store, "email_auth")
    assert "해당 범위에서만 변경" in text
    assert rows[0]["relation"] is None
    assert rows[1]["relation"] == "SCOPE_RESTRICT"
