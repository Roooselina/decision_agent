from __future__ import annotations

import json
from pathlib import Path

import pytest
import sample_files as sf

from decision_agent.models import ArtifactKind, ProjectConfig, ScopeKind
from decision_agent.parsing.loader import load_project
from decision_agent.sources.builder import MEETING, build_inputs
from decision_agent.sources.dates import parse_date
from decision_agent.sources.readers import (
    SUPPORTED_EXTENSIONS,
    UnsupportedFormat,
    read_document,
)

DEMO_DIR = Path(__file__).resolve().parents[1] / "data" / "demo"
ALIASES = {
    "release": {"mvp": ["MVP", "이번 버전"], "production": ["Production", "정식 출시"]},
    "module": {"login": ["로그인"]},
    "user_group": {},
    "environment": {},
}
CONFIG = ProjectConfig(current_release="mvp")


def build(tmp_path: Path, names: list[str], overrides=None):
    docs = [read_document(tmp_path / n, tmp_path) for n in names]
    return build_inputs(docs, CONFIG, ALIASES, overrides)


def test_parse_date_formats():
    assert parse_date("2026-09-01 회의") == "2026-09-01"
    assert parse_date("2026.9.1") == "2026-09-01"
    assert parse_date("2026년 9월 1일") == "2026-09-01"
    assert parse_date("meeting_20260901.docx") == "2026-09-01"
    assert parse_date("9월 3일 회의") is None
    assert parse_date("9월 3일 회의", default_year=2026) == "2026-09-03"
    assert parse_date("2026-13-40") is None


def test_demo_folder_matches_legacy_loader():
    legacy_meetings, legacy_artifacts, _ = load_project(DEMO_DIR)
    names = ["meetings/meeting.md", "requirements.md", "tasks.csv", "test_plan.md", "schedule.csv"]
    docs = [read_document(DEMO_DIR / n, DEMO_DIR) for n in names]
    result = build_inputs(docs, CONFIG, json.loads((DEMO_DIR / "scope_aliases.json").read_text(encoding="utf-8")))
    assert [(m.date, m.text) for m in result.meetings] == [
        (m.date, m.text) for m in legacy_meetings
    ]
    assert len(result.artifacts) == len(legacy_artifacts)
    by_text = {a.text: a for a in result.artifacts}
    for old in legacy_artifacts:
        new = by_text[old.text]
        assert new.kind == old.kind
        assert new.scope == old.scope


def test_docx_meeting(tmp_path):
    sf.write_docx_meeting(tmp_path / "회의록.docx")
    result = build(tmp_path, ["회의록.docx"])
    assert [(m.date, m.text) for m in result.meetings] == [
        ("2026-09-01", "로그인에 이메일 인증을 적용한다."),
        ("2026-09-01", "React를 사용한다."),
        ("2026-09-10", "일정 문제로 이번 MVP에서는 이메일 인증을 제외한다."),
    ]


def test_docx_table_meeting_with_date_line(tmp_path):
    sf.write_docx_with_table(tmp_path / "meeting_notes.docx")
    result = build(tmp_path, ["meeting_notes.docx"])
    texts = {m.text: m.date for m in result.meetings}
    assert texts == {
        "결제는 토스페이먼츠를 사용한다.": "2026-09-05",
        "데이터베이스는 MySQL을 사용한다.": "2026-09-05",
    }


def test_docx_tracked_changes_text(tmp_path):
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    path = tmp_path / "meeting.docx"
    document = Document()
    document.add_heading("2026-09-02 회의", level=2)
    paragraph = document.add_paragraph(style="List Bullet")
    run = paragraph.add_run("결제는 ")
    ins = OxmlElement("w:ins")
    ins_run = OxmlElement("w:r")
    ins_text = OxmlElement("w:t")
    ins_text.text = "토스페이먼츠를 "
    ins_run.append(ins_text)
    ins.append(ins_run)
    run._r.addnext(ins)
    deleted = OxmlElement("w:del")
    del_run = OxmlElement("w:r")
    del_text = OxmlElement("w:delText")
    del_text.text = "카카오페이를 "
    del_run.append(del_text)
    deleted.append(del_run)
    ins.addnext(deleted)
    tail = OxmlElement("w:r")
    tail_text = OxmlElement("w:t")
    tail_text.text = "사용한다."
    tail.append(tail_text)
    deleted.addnext(tail)
    assert paragraph._p.xml.count(qn("w:del").split("}")[1]) >= 1
    document.save(str(path))
    result = build(tmp_path, ["meeting.docx"])
    assert [m.text for m in result.meetings] == ["결제는 토스페이먼츠를 사용한다."]


def test_xlsx_sheets_classified_by_name_and_columns(tmp_path):
    sf.write_xlsx_project(tmp_path / "project.xlsx")
    result = build(tmp_path, ["project.xlsx"])
    kinds = {a.text: a.kind for a in result.artifacts}
    assert kinds["MVP 이메일 인증 개발 진행 중"] == ArtifactKind.TASK
    assert kinds["발표"] == ArtifactKind.SCHEDULE
    task = next(a for a in result.artifacts if a.text.startswith("MVP"))
    assert task.scope.scope_kind == ScopeKind.SCOPED and task.scope.release == "mvp"
    assert task.id.endswith(":T1")
    assert task.metadata["status"] == "IN_PROGRESS"


def test_pptx_text_and_notes(tmp_path):
    sf.write_pptx(tmp_path / "requirements.pptx")
    doc = read_document(tmp_path / "requirements.pptx", tmp_path)
    texts = [b.text for b in doc.all_blocks()]
    assert "요구사항" in texts and "MVP 로그인에 이메일 인증 필수" in texts
    assert "발표자 메모 확인" in texts
    result = build(tmp_path, ["requirements.pptx"])
    assert any(a.kind == ArtifactKind.REQUIREMENT for a in result.artifacts)


def test_pdf_text(tmp_path):
    sf.write_pdf(tmp_path / "meeting.pdf", ["2026-09-06", "- Database is MySQL.", "- Use React."])
    result = build(tmp_path, ["meeting.pdf"])
    assert [(m.date, m.text) for m in result.meetings] == [
        ("2026-09-06", "Database is MySQL."),
        ("2026-09-06", "Use React."),
    ]


def test_pdf_without_text_layer_warns(tmp_path):
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with (tmp_path / "scan.pdf").open("wb") as f:
        writer.write(f)
    doc = read_document(tmp_path / "scan.pdf", tmp_path)
    assert doc.is_empty() and any("OCR" in w for w in doc.warnings)


def test_html_markdown_txt_csv(tmp_path):
    (tmp_path / "meeting.html").write_text(
        "<html><body><h2>2026-09-07 회의</h2><ul><li>CI는 Jenkins를 사용한다.</li></ul>"
        "<script>alert(1)</script></body></html>",
        encoding="utf-8",
    )
    (tmp_path / "meeting.txt").write_text(
        "2026-09-08 회의\n- 로그 보관 기간은 90일로 한다.\n", encoding="utf-8"
    )
    (tmp_path / "tasks.csv").write_bytes(
        "id,title,status,assignee\nT1,푸시 알림 서버 구현,TODO,A\n".encode("cp949")
    )
    result = build(tmp_path, ["meeting.html", "meeting.txt", "tasks.csv"])
    assert {m.text for m in result.meetings} == {
        "CI는 Jenkins를 사용한다.",
        "로그 보관 기간은 90일로 한다.",
    }
    assert [a.text for a in result.artifacts] == ["푸시 알림 서버 구현"]


def test_markdown_checkbox_tasks_and_heading_scope(tmp_path):
    (tmp_path / "tasks.md").write_text(
        "## MVP\n- [ ] 이메일 인증 개발\n- [x] 로그인 화면\n## Production\n- [ ] 이메일 인증 구현\n",
        encoding="utf-8",
    )
    result = build(tmp_path, ["tasks.md"])
    by_text = {a.text: a for a in result.artifacts}
    assert by_text["이메일 인증 개발"].metadata["status"] == "TODO"
    assert by_text["로그인 화면"].metadata["status"] == "DONE"
    assert by_text["이메일 인증 개발"].scope.release == "mvp"
    assert by_text["이메일 인증 구현"].scope.release == "production"


def test_markdown_pipe_table(tmp_path):
    (tmp_path / "schedule.md").write_text(
        "| 일정 | 날짜 |\n|---|---|\n| 정식 출시 | 2026-10-20 |\n| 발표 | 2026-09-22 |\n",
        encoding="utf-8",
    )
    result = build(tmp_path, ["schedule.md"])
    assert [(a.text, a.metadata["date"]) for a in result.artifacts] == [
        ("정식 출시", "2026-10-20"),
        ("발표", "2026-09-22"),
    ]


def test_json_and_jsonl_records(tmp_path):
    (tmp_path / "tasks.json").write_text(
        json.dumps({"tasks": [{"id": "J1", "title": "OTP 앱 인증 개발", "status": "TODO"}]}),
        encoding="utf-8",
    )
    (tmp_path / "slack_meeting.json").write_text(
        json.dumps([{"text": "CI를 GitHub Actions로 변경한다.", "ts": "1788220800.000100"}]),
        encoding="utf-8",
    )
    result = build(tmp_path, ["tasks.json", "slack_meeting.json"])
    assert [a.text for a in result.artifacts] == ["OTP 앱 인증 개발"]
    assert [m.text for m in result.meetings] == ["CI를 GitHub Actions로 변경한다."]
    assert result.meetings[0].date == "2026-09-01"


def test_ics_events_and_meeting_notes(tmp_path):
    (tmp_path / "calendar.ics").write_text(sf.ICS_TEXT, encoding="utf-8")
    result = build(tmp_path, ["calendar.ics"])
    schedule = {a.text: a.metadata["date"] for a in result.artifacts}
    assert schedule == {"정식 출시": "2026-10-20", "주간 회의": "2026-09-15"}
    assert [(m.date, m.text) for m in result.meetings] == [
        ("2026-09-15", "발표를 다음 주로 연기한다."),
        ("2026-09-15", "리허설 추가"),
    ]


def test_eml_vtt_rtf(tmp_path):
    (tmp_path / "meeting_minutes.eml").write_text(
        "From: a@example.com\nSubject: 회의록 공유\nDate: Tue, 8 Sep 2026 10:00:00 +0900\n"
        "Content-Type: text/plain; charset=utf-8\n\n- 결제는 토스페이먼츠를 사용한다.\n",
        encoding="utf-8",
    )
    (tmp_path / "meeting_2026-09-09.vtt").write_text(
        "WEBVTT\n\n1\n00:00:01.000 --> 00:00:04.000\nCI는 Jenkins로 하기로 결정했습니다.\n\n"
        "2\n00:00:05.000 --> 00:00:07.000\n점심 뭐 먹죠\n",
        encoding="utf-8",
    )
    (tmp_path / "meeting_2026-09-10.rtf").write_text(
        r"{\rtf1\ansi\ansicpg949 {\fonttbl{\f0 Arial;}}\f0 - Use React.\par - Use MySQL.\par}",
        encoding="latin-1",
    )
    result = build(tmp_path, ["meeting_minutes.eml", "meeting_2026-09-09.vtt", "meeting_2026-09-10.rtf"])
    texts = {(m.date, m.text) for m in result.meetings}
    assert ("2026-09-08", "결제는 토스페이먼츠를 사용한다.") in texts
    assert ("2026-09-09", "CI는 Jenkins로 하기로 결정했습니다.") in texts
    assert all("점심" not in t for _, t in texts)
    assert ("2026-09-10", "Use React.") in texts and ("2026-09-10", "Use MySQL.") in texts


def test_odt_and_hwpx(tmp_path):
    sf.write_odt(tmp_path / "meeting.odt")
    sf.write_hwpx(tmp_path / "meeting.hwpx")
    result = build(tmp_path, ["meeting.odt", "meeting.hwpx"])
    assert {(m.date, m.text) for m in result.meetings} == {
        ("2026-09-03", "데이터베이스는 MySQL을 사용한다."),
        ("2026-09-04", "푸시 알림 기능 개발은 취소한다."),
    }


def test_odf_entity_declaration_rejected(tmp_path):
    import zipfile

    with zipfile.ZipFile(tmp_path / "bad.odt", "w") as archive:
        archive.writestr(
            "content.xml", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>'
        )
    with pytest.raises(ValueError):
        read_document(tmp_path / "bad.odt", tmp_path)


def test_unsupported_formats_have_clear_message(tmp_path):
    (tmp_path / "old.hwp").write_bytes(b"x")
    with pytest.raises(UnsupportedFormat, match="hwpx"):
        read_document(tmp_path / "old.hwp", tmp_path)
    assert "hwp" not in SUPPORTED_EXTENSIONS


def test_override_and_unclassified(tmp_path):
    (tmp_path / "notes.txt").write_text("2026-09-08 회의\n- 로그 보관 기간은 90일로 한다.\n", encoding="utf-8")
    (tmp_path / "random.md").write_text("# 잡담\n오늘 날씨가 좋다.\n", encoding="utf-8")
    result = build(tmp_path, ["notes.txt", "random.md"])
    assert result.unclassified == ["random.md"]
    assert [m.text for m in result.meetings] == ["로그 보관 기간은 90일로 한다."]
    forced = build(tmp_path, ["notes.txt", "random.md"], {"random.md": "IGNORE"})
    assert forced.unclassified == []
    as_task = build(tmp_path, ["notes.txt"], {"notes.txt": "TASK"})
    assert as_task.meetings == [] and as_task.artifacts


def test_artifact_ids_are_unique_and_stable(tmp_path):
    (tmp_path / "tasks.md").write_text("- 같은 작업 항목\n- 같은 작업 항목\n", encoding="utf-8")
    first = build(tmp_path, ["tasks.md"])
    second = build(tmp_path, ["tasks.md"])
    ids = [a.id for a in first.artifacts]
    assert len(set(ids)) == 2
    assert ids == [a.id for a in second.artifacts]


def test_meeting_kind_constant_and_free_text_filter(tmp_path):
    (tmp_path / "meeting.txt").write_text(
        "2026-09-11\n오늘 날씨 좋네요\n로그인 방식을 OTP로 변경하기로 했다.\n", encoding="utf-8"
    )
    result = build(tmp_path, ["meeting.txt"])
    assert result.summaries[0].kinds == [MEETING]
    assert [m.text for m in result.meetings] == ["로그인 방식을 OTP로 변경하기로 했다."]


def test_supported_extension_count():
    assert len(SUPPORTED_EXTENSIONS) >= 25
