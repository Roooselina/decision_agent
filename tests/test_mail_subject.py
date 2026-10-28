"""Mail subjects stay out of the heading, so a decision word in the subject cannot hide the body."""

from __future__ import annotations

from decision_agent.connectors.base import MailMessage, mail_document
from decision_agent.models import ProjectConfig
from decision_agent.sources.builder import build_inputs

ALIASES = {"release": {"mvp": ["MVP"]}, "module": {}, "user_group": {}, "environment": {}}
CONFIG = ProjectConfig(current_release="mvp")


def meetings(subject: str, body: str, kind: str = "MEETING") -> list[str]:
    made = mail_document(MailMessage("m1", "2026-10-27", subject, "prof@example.ac.kr", body), {}, kind)
    assert made is not None
    name, doc = made
    doc.path = f"@gmail/{name}"
    return [m.text for m in build_inputs([doc], CONFIG, ALIASES, {}).meetings]


def test_heading_is_the_date_and_the_subject_is_one_line_before_the_body():
    _, doc = mail_document(MailMessage("m1", "2026-10-27", "최종 발표 일정 확정 부탁드립니다", "a@example.com",
                                       "최종 발표는 11월 17일로 확정합니다."), {})
    blocks = doc.sections[0].blocks
    assert (blocks[0].kind, blocks[0].text) == ("heading", "2026-10-27")
    assert (blocks[1].kind, blocks[1].text) == ("para", "제목: 최종 발표 일정 확정 부탁드립니다")


def test_subject_with_a_decision_word_keeps_the_sentence_decisions_of_the_body():
    found = meetings(
        "최종 발표 일정 확정 부탁드립니다",
        "안녕하세요.\n최종 발표는 11월 17일 오후 2시, 리허설은 11월 16일로 확정합니다.\n감사합니다.",
    )
    assert "최종 발표는 11월 17일 오후 2시, 리허설은 11월 16일로 확정합니다." in found
    assert not any(text.startswith("제목:") for text in found)
    for subject in ("Action items", "회의 결정 사항", "follow up: 배포", "합의 내용"):
        assert meetings(subject, "배포는 Render 유료 플랜으로 하자.") == ["배포는 Render 유료 플랜으로 하자."]


def test_subject_without_a_decision_word_gives_the_same_body_decisions():
    found = meetings("배포 서버 비용 문의", "그럼 배포는 Render 유료 플랜으로 하자. Lightsail은 이번 주 안에 정리할게.")
    assert found == ["그럼 배포는 Render 유료 플랜으로 하자. Lightsail은 이번 주 안에 정리할게."]
    # The subject line itself is never an item.
    assert meetings("결제 방식 확정", "점심 뭐 먹지") == []


def test_bullet_bodies_are_read_as_before():
    assert meetings("회의 결정 사항", "- 결제는 MVP에서 제외한다\n- 사진은 5장까지") == [
        "결제는 MVP에서 제외한다", "사진은 5장까지",
    ]


def test_a_mail_with_only_a_subject_still_uses_the_subject():
    assert meetings("배포 서버는 Render로 변경합니다", "") == ["배포 서버는 Render로 변경합니다"]
