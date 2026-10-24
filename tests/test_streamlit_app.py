from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")


def select(at, label, value):
    for box in at.selectbox:
        if box.label == label:
            box.select(value)
            return
    raise AssertionError(f"selectbox not found: {label}")


def click(at, label):
    for button in at.button:
        if button.label == label:
            button.click()
            return
    raise AssertionError(f"button not found: {label}")


def test_app_boots_and_runs_scope_scenario():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    select(at, "실행 방식", "Agent")
    select(at, "topic", "email_auth")
    click(at, "분석 실행")
    at.run()
    assert not at.exception
    assert [m.value for m in at.metric if m.label == "일관성 점수"] == ["57"]


def test_clarification_form_collects_the_answer_and_updates_the_result():
    at = AppTest.from_file(APP, default_timeout=60).run()
    select(at, "실행 방식", "Agent")
    select(at, "topic", "presentation_schedule")
    click(at, "분석 실행")
    at.run()
    assert not at.exception
    assert [t for t in at.text_input if t.label == "답변"], "question form should be shown"
    assert not [m for m in at.metric if m.label == "일관성 점수"]
    [answer] = [t for t in at.text_input if t.label == "답변"]
    answer.set_value("세 일정 모두 다음 주로 변경")
    click(at, "답변 제출")
    at.run()
    assert not at.exception
    assert [m.value for m in at.metric if m.label == "일관성 점수"] == ["33"]


def test_new_task_warning_tab():
    at = AppTest.from_file(APP, default_timeout=60).run()
    click(at, "사전 점검")
    at.run()
    assert not at.exception
    assert any("제외되었습니다" in w.value for w in at.warning)
