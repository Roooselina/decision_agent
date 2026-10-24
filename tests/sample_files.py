"""Helpers that create small sample documents in many formats for tests."""

from __future__ import annotations

import zipfile
from pathlib import Path


def write_docx_meeting(path: Path) -> None:
    from docx import Document

    document = Document()
    document.add_heading("2026-09-01 회의", level=2)
    document.add_paragraph("로그인에 이메일 인증을 적용한다.", style="List Bullet")
    document.add_paragraph("React를 사용한다.", style="List Bullet")
    document.add_heading("2026-09-10 회의", level=2)
    document.add_paragraph("일정 문제로 이번 MVP에서는 이메일 인증을 제외한다.", style="List Bullet")
    document.save(str(path))


def write_docx_with_table(path: Path) -> None:
    from docx import Document

    document = Document()
    document.add_heading("회의록", level=1)
    document.add_paragraph("일시: 2026-09-05")
    table = document.add_table(rows=3, cols=3)
    for cell, text in zip(table.rows[0].cells, ["안건", "결정 사항", "담당"], strict=True):
        cell.text = text
    for cell, text in zip(table.rows[1].cells, ["결제", "결제는 토스페이먼츠를 사용한다.", "A"], strict=True):
        cell.text = text
    for cell, text in zip(table.rows[2].cells, ["DB", "데이터베이스는 MySQL을 사용한다.", "B"], strict=True):
        cell.text = text
    document.save(str(path))


def write_xlsx_project(path: Path) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    tasks = workbook.active
    tasks.title = "Tasks"
    tasks.append(["id", "title", "status", "assignee", "release"])
    tasks.append(["T1", "MVP 이메일 인증 개발 진행 중", "IN_PROGRESS", "A", "mvp"])
    tasks.append(["T2", "Production 이메일 인증 구현", "TODO", "B", "production"])
    schedule = workbook.create_sheet("Schedule")
    schedule.append(["title", "date"])
    schedule.append(["발표", "2026-09-22"])
    workbook.save(str(path))


def write_pptx(path: Path) -> None:
    from pptx import Presentation

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "요구사항"
    slide.placeholders[1].text_frame.text = "MVP 로그인에 이메일 인증 필수"
    slide.notes_slide.notes_text_frame.text = "발표자 메모 확인"
    presentation.save(str(path))


def write_pdf(path: Path, lines: list[str]) -> None:
    """Write a minimal one-page PDF using the built-in Helvetica font (ASCII text only)."""
    content = "BT /F1 12 Tf 72 720 Td 14 TL\n"
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content += f"({escaped}) Tj T*\n"
    content += "ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>"),
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out.encode("latin-1")))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    xref_at = len(out.encode("latin-1"))
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n"
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    path.write_bytes(out.encode("latin-1"))


def write_odt(path: Path) -> None:
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        "<office:body><office:text>"
        '<text:h text:outline-level="2">2026-09-03 회의</text:h>'
        "<text:list><text:list-item><text:p>데이터베이스는 MySQL을 사용한다.</text:p>"
        "</text:list-item></text:list>"
        "</office:text></office:body></office:document-content>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        archive.writestr("content.xml", content)


def write_hwpx(path: Path) -> None:
    section = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        "<hp:p><hp:run><hp:t>2026-09-04 회의</hp:t></hp:run></hp:p>"
        "<hp:p><hp:run><hp:t>- 푸시 알림 기능 개발은 취소한다.</hp:t></hp:run></hp:p>"
        "</hs:sec>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("Contents/section0.xml", section)


ICS_TEXT = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
    "BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20261020\r\nSUMMARY:정식 출시\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nDTSTART:20260915T090000Z\r\nSUMMARY:주간 회의\r\n"
    "DESCRIPTION:- 발표를 다음 주로 연기한다.\\n- 리허설 추가\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nDTSTART:20260916T090000Z\r\nSUMMARY:취소된 일정\r\nSTATUS:CANCELLED\r\n"
    "END:VEVENT\r\nEND:VCALENDAR\r\n"
)
