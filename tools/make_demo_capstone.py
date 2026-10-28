"""Write the BookSwap capstone demo folders from the authored content in demo/.

Usage:  python tools/make_demo_capstone.py [--out DIR] [--variant capstone|full|both] [--no-pdf]

For each variant it writes a pack folder DIR/<name>/ holding the folder to watch
(DIR/<name>/<name>/), the answer key (json and md), a README and google_calendar_import.ics.
The answer key and the calendar import file stay outside the watched folder on purpose.
The text of every document is authored in demo/*.py; this script only writes formats.
PDF files are printed with Playwright's Chromium; --no-pdf skips them.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import importlib.util
import io
import json
import os
import re
import sys
from pathlib import Path

from decision_agent.connectors.base import MailMessage, mail_document
from decision_agent.desktop.profile import load_profile
from decision_agent.desktop.settings import AppSettings
from decision_agent.sources.builder import BuildResult, build_inputs
from decision_agent.sources.model import ParsedDocument
from decision_agent.sources.readers import is_supported, read_document

REPO = Path(__file__).resolve().parents[1]
DEMO = REPO / "demo"
# Today in the demo story. The calendar import file is built from offsets to this date, so
# moving the whole story means changing this one constant.
BASE_DATE = datetime.date(2026, 10, 28)
VARIANTS = {
    "capstone": ("demo_capstone", ["capstone_meetings", "capstone_work"]),
    "full": ("demo_capstone_full", ["capstone_meetings", "capstone_work", "capstone_full_notes", "capstone_full_work"]),
}
CONFIG = {
    "current_release": "mvp",
    "scope_aliases": {
        "release": {"mvp": ["MVP", "이번 학기", "1차 버전"], "later": ["다음 버전", "MVP 이후"]},
        "module": {
            "login": ["로그인", "SSO", "가입"],
            "listing": ["매물", "사진"],
            "search": ["검색"],
            "message": ["쪽지", "채팅", "메시지"],
            "trade": ["거래", "결제", "계좌"],
            "push": ["푸시", "알림"],
        },
        "user_group": {},
        "environment": {},
    },
    "kind_overrides": {
        "ideas/*": "MEETING",
        "status/*": "MEETING",
        "emails/*": "MEETING",
        "deploy/*": "REQUIREMENT",
        "design/*": "REQUIREMENT",
        "docs/*": "IGNORE",
    },
}


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(f"demo_{name}", DEMO / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def content(variant: str) -> tuple[dict[str, object], dict[str, str]]:
    """(relative path -> authored content, relative path -> file date) of one variant."""
    docs: dict[str, object] = {}
    dates: dict[str, str] = {}
    for name in VARIANTS[variant][1]:
        module = load_module(name)
        for path, value in {**module.DOCS, **getattr(module, "ICS", {}), **getattr(module, "EML", {})}.items():
            if path in docs:
                raise ValueError(f"{path} is authored twice")
            docs[path] = value
        dates.update(getattr(module, "DATES", {}))
    return docs, dates


def threads() -> dict:
    return json.loads((DEMO / "mail_threads.json").read_text(encoding="utf-8"))


# Writers: one per format. Each takes the authored text and the target path.

def _md_blocks(text: str) -> list[tuple[str, object]]:
    """("h1"|"h2"|"li"|"p", text) and ("table", rows) from the markdown-like authored text."""
    out: list[tuple[str, object]] = []
    table: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                table.append(cells)
            continue
        if table:
            out.append(("table", table))
            table = []
        if not stripped:
            continue
        if stripped.startswith("## "):
            out.append(("h2", stripped[3:]))
        elif stripped.startswith("# "):
            out.append(("h1", stripped[2:]))
        elif stripped.startswith("- "):
            out.append(("li", stripped[2:]))
        else:
            out.append(("p", stripped))
    if table:
        out.append(("table", table))
    return out


def write_docx(text: str, target: Path, date: datetime.date) -> None:
    import docx

    document = docx.Document()
    for kind, value in _md_blocks(text):
        if kind == "h1":
            document.add_heading(value, level=1)
        elif kind == "h2":
            document.add_heading(value, level=2)
        elif kind == "li":
            document.add_paragraph(value, style="List Bullet")
        elif kind == "p":
            document.add_paragraph(value)
        else:
            rows = value
            table = document.add_table(rows=len(rows), cols=len(rows[0]))
            table.style = "Table Grid"
            for r, row in enumerate(rows):
                for c, cell in enumerate(row):
                    table.cell(r, c).text = cell
    stamp = datetime.datetime.combine(date, datetime.time(21, 0))
    document.core_properties.created = stamp
    document.core_properties.modified = stamp
    document.core_properties.author = "BookSwap 5조"
    document.save(target)


def write_xlsx(text: str, target: Path, date: datetime.date) -> None:
    import openpyxl

    book = openpyxl.Workbook()
    book.remove(book.active)
    for block in text.split("=== ")[1:]:
        title, _, body = block.partition("\n")
        sheet = book.create_sheet(title.strip())
        for row in csv.reader(io.StringIO(body.strip())):
            sheet.append(row)
    stamp = datetime.datetime.combine(date, datetime.time(21, 0))
    book.properties.created = stamp
    book.properties.modified = stamp
    book.save(target)


def write_pptx(text: str, target: Path, date: datetime.date) -> None:
    from pptx import Presentation

    deck = Presentation()
    for chunk in text.strip().split("\n---\n"):
        lines = [line for line in chunk.strip().splitlines() if line.strip()]
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = lines[0]
        body = slide.placeholders[1].text_frame
        for index, line in enumerate(lines[1:]):
            paragraph = body.paragraphs[0] if index == 0 else body.add_paragraph()
            paragraph.text = line.strip().removeprefix("- ")
            paragraph.level = 1 if line.startswith("  ") else 0
    stamp = datetime.datetime.combine(date, datetime.time(21, 0))
    deck.core_properties.created = stamp
    deck.core_properties.modified = stamp
    deck.save(target)


def write_pdf(text: str, target: Path, browser) -> None:
    import html

    parts = []
    for kind, value in _md_blocks(text):
        if kind in ("h1", "h2"):
            parts.append(f"<{kind}>{html.escape(value)}</{kind}>")
        elif kind == "li":
            parts.append(f"<p>- {html.escape(value)}</p>")
        elif kind == "p":
            parts.append(f"<p>{html.escape(value)}</p>")
    page = browser.new_page()
    page.set_content(
        "<html><head><meta charset='utf-8'><style>body{font-family:'Malgun Gothic',sans-serif;"
        "font-size:12pt;margin:40px}h1{font-size:18pt}h2{font-size:14pt}p{margin:4px 0}</style>"
        f"</head><body>{''.join(parts)}</body></html>"
    )
    page.pdf(path=str(target), format="A4")
    page.close()


def _ics_stamp(value: str) -> str:
    return value.replace("-", "").replace(":", "") + ("00" if len(value) == 16 else "")


def _ics_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics_text(events: list[dict], name: str) -> str:
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//BookSwap demo//KO", "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{name}", "X-WR-TIMEZONE:Asia/Seoul",
        "BEGIN:VTIMEZONE", "TZID:Asia/Seoul", "BEGIN:STANDARD", "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0900", "TZOFFSETTO:+0900", "TZNAME:KST", "END:STANDARD", "END:VTIMEZONE",
    ]
    for event in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{event['uid']}@bookswap.example.com",
            "DTSTAMP:20261001T000000Z",
            f"DTSTART;TZID=Asia/Seoul:{_ics_stamp(event['start'])}",
            f"DTEND;TZID=Asia/Seoul:{_ics_stamp(event['end'])}",
            f"SUMMARY:{_ics_text(event['summary'])}",
            f"LOCATION:{_ics_text(event['location'])}",
            f"DESCRIPTION:{_ics_text(event['description'])}",
        ]
        if event.get("rrule"):
            lines.append(f"RRULE:{event['rrule']}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def eml_bytes(mail: dict) -> bytes:
    from email.message import EmailMessage
    from email.utils import format_datetime

    message = EmailMessage()
    message["From"] = mail["from"]
    message["To"] = mail["to"]
    message["Subject"] = mail["subject"]
    when = datetime.datetime.fromisoformat(mail["date"]).replace(tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
    message["Date"] = format_datetime(when)
    message["Message-ID"] = f"<{mail['id']}@bookswap.example.com>"
    message.set_content(mail["body"])
    return bytes(message)


def file_date(path: str, dates: dict[str, str]) -> datetime.date:
    if path in dates:
        return datetime.date.fromisoformat(dates[path])
    found = re.search(r"(20\d\d)-(\d\d)-(\d\d)", path)
    if found:
        return datetime.date(*map(int, found.groups()))
    return BASE_DATE - datetime.timedelta(days=1)


def write_folder(variant: str, root: Path, pdf: bool = True) -> list[str]:
    """Write the watched folder of one variant into root. Returns the relative paths written."""
    docs, dates = content(variant)
    root.mkdir(parents=True, exist_ok=True)
    (root / ".decision_agent.json").write_bytes(
        json.dumps(CONFIG, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    )
    written: list[str] = []
    browser = playwright = None
    try:
        for rel, value in sorted(docs.items()):
            suffix = Path(rel).suffix
            if suffix == ".pdf" and not pdf:
                continue
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            date = file_date(rel, dates)
            if suffix in (".md", ".txt"):
                target.write_bytes(value.encode("utf-8"))
            elif suffix == ".csv":
                target.write_bytes(value.encode("utf-8-sig"))
            elif suffix == ".docx":
                write_docx(value, target, date)
            elif suffix == ".xlsx":
                write_xlsx(value, target, date)
            elif suffix == ".pptx":
                write_pptx(value, target, date)
            elif suffix == ".ics":
                target.write_bytes(ics_text(value, "BookSwap 팀 일정").encode("utf-8"))
            elif suffix == ".eml":
                target.write_bytes(eml_bytes(value))
            elif suffix == ".pdf":
                if browser is None:
                    from playwright.sync_api import sync_playwright

                    playwright = sync_playwright().start()
                    browser = playwright.chromium.launch()
                write_pdf(value, target, browser)
            else:
                raise ValueError(f"no writer for {rel}")
            stamp = datetime.datetime.combine(date, datetime.time(21, 0)).timestamp()
            os.utime(target, (stamp, stamp))
            written.append(rel)
    finally:
        if browser is not None:
            browser.close()
            playwright.stop()
    return written


# Parsing with the app's own readers and builder, for the key and the checks.

def read_folder(root: Path) -> list[ParsedDocument]:
    docs = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if not path.is_file() or rel.startswith(".") or not is_supported(path):
            continue
        docs.append(read_document(path, root))
    return docs


def build_folder(root: Path, extra: list[ParsedDocument] | None = None) -> BuildResult:
    profile = load_profile(root, AppSettings())
    return build_inputs(read_folder(root) + (extra or []), profile.config, profile.aliases, profile.overrides)


def mail_documents(data: dict | None = None) -> dict[str, ParsedDocument]:
    """The 17 thread messages as connector documents, named as the Gmail connector names them."""
    data = data or threads()
    people = data["people"]
    out: dict[str, ParsedDocument] = {}
    for thread in data["threads"]:
        for number, message in enumerate(thread["messages"], start=1):
            sender = people[message["from"]]
            subject = thread["subject"] if number == 1 or thread["subject"].startswith("Re:") else f"Re: {thread['subject']}"
            mail = MailMessage(
                f"{thread['id']}-{number}", message["date"][:10], subject,
                f"{sender['name']} <{sender['example']}>", message["body"],
            )
            made = mail_document(mail, {})
            if made is not None:
                name, doc = made
                doc.path = f"@gmail/{name}"
                out[name] = doc
    return out


def _resolve(texts: list[tuple[str, str]], file: str | None, marker: str, exact: bool) -> str:
    found = sorted({
        text for source, text in texts
        if (file is None or source == file) and (text == marker if exact else marker in text)
    })
    if len(found) != 1:
        raise ValueError(f"marker {marker!r} in {file or 'mail'} matched {len(found)} items: {found[:3]}")
    return found[0]


def answer_key(root: Path) -> dict:
    """The answer key in the demo_big schema, with exact texts resolved through the parser."""
    key_module = load_module("capstone_key")
    mail = mail_documents()
    built = build_folder(root, list(mail.values()))
    meetings = [(m.source_doc, m.text) for m in built.meetings]
    artifacts = [(a.source_doc, a.text) for a in built.artifacts]
    decisions = []
    for d in key_module.DECISIONS:
        file = None if d.get("mail") else d["file"]
        text = _resolve(meetings if file else [(s, t) for s, t in meetings if s.startswith("@gmail/")], file, d["marker"], False)
        decisions.append({k: v for k, v in {**d, "text": text}.items() if k != "marker"})

    def latest(topic: str, with_mail: bool) -> str:
        values = [d["value"] for d in decisions if d["topic"] == topic and (with_mail or not d.get("mail"))]
        return values[-1]

    lines = []
    for line in key_module.LINES:
        text = _resolve(artifacts, line["file"], line["marker"], line.get("exact", False))
        entry = {k: v for k, v in line.items() if k not in ("marker", "exact")}
        entry["text"] = text
        entry["expected"] = "INCONSISTENT" if line["value"] != latest(line["topic"], True) else "CONSISTENT"
        entry["expected_files_only"] = "INCONSISTENT" if line["value"] != latest(line["topic"], False) else "CONSISTENT"
        lines.append(entry)
    topics = []
    for topic in dict.fromkeys(d["topic"] for d in decisions):
        chain = [d for d in decisions if d["topic"] == topic]
        topics.append({"key": topic, "values": [d["value"] for d in chain], "dates": [d["date"] for d in chain],
                       "latest": chain[-1]["value"]})
    return {
        "as_of": BASE_DATE.isoformat(),
        "topics": topics,
        "decisions": decisions,
        "artifact_lines": lines,
        "traps": key_module.TRAPS,
        "note": "The simulated perfect model in tools/bench_demo.py reads this key. It shows that the stale lines are "
                "reachable by the pipeline, not how accurate a real model is. The demo judge (MockProvider) catches only "
                "some topics; real-model results are what matter.",
    }


def key_markdown(key: dict, title: str) -> str:
    stale = sorted((line for line in key["artifact_lines"] if line.get("item")), key=lambda line: line["item"])
    out = [
        f"# {title} 정답표 (사람이 보는 용도, 점검 폴더에 넣지 마세요)", "",
        f"기준일은 {key['as_of']} 입니다. 반영 안 됨으로 나와야 하는 줄은 정확히 10개입니다.",
        "1~6번은 폴더의 문서만으로 찾을 수 있고, 7~10번은 메일 스레드(T1, T2)를 읽은 뒤에만 찾을 수 있습니다.", "",
        "## 반영 안 됨으로 나와야 하는 줄", "",
        "| 번호 | 파일 | 줄 | 최신 결정 | 메일 필요 |", "|---|---|---|---|---|",
    ]
    for line in stale:
        latest = next(t["latest"] for t in key["topics"] if t["key"] == line["topic"])
        need = "예" if line["expected_files_only"] == "CONSISTENT" else "아니오"
        out.append(f"| {line['item']} | {line['file']} | {line['text']} | {latest} | {need} |")
    out += ["", "## 주제별 결정 이력", "", "| 주제 | 이력 | 최신 값 |", "|---|---|---|"]
    for topic in key["topics"]:
        history = " -> ".join(f"{d}: {v}" for d, v in zip(topic["dates"], topic["values"], strict=True))
        out.append(f"| {topic['key']} | {history} | {topic['latest']} |")
    out += ["", "## 지적되면 안 되는 것 (함정)", ""] + [f"- {t}" for t in key["traps"]]
    out += ["", "## 참고", "",
            "- 데모 판정기(MockProvider)는 일부 주제만 잡습니다. 실제 모델의 결과가 중요합니다.",
            "- tools/bench_demo.py의 모의 모델은 이 정답표를 그대로 읽는 완벽한 판정기라서, 실제 정확도의 근거가 아닙니다.", ""]
    return "\n".join(out)


def calendar_import_events() -> list[dict]:
    """Events of the next three weeks around BASE_DATE for the user's own Google Calendar."""
    def at(days: int, clock: str) -> str:
        return f"{(BASE_DATE + datetime.timedelta(days=days)).isoformat()}T{clock}"

    friday = (4 - BASE_DATE.weekday()) % 7
    return [
        {"uid": "import-midterm-due", "summary": "중간 점검 자료 마감", "start": at(0, "23:30"), "end": at(0, "23:59"),
         "location": "팀 공유 폴더", "description": "중간 점검 슬라이드와 시연 영상을 공유 폴더에 올리기 (밤 12시 전)"},
        {"uid": "import-midterm", "summary": "중간 점검", "start": at(1, "14:00"), "end": at(1, "15:00"),
         "location": "공학관 세미나실", "description": "팀당 15분 발표와 질의응답, 시연 포함"},
        {"uid": "import-weekly", "summary": "주간 회의", "start": at(friday, "18:00"), "end": at(friday, "19:00"),
         "location": "공학관 3층 세미나실", "description": "진행 상황 공유와 다음 주 할 일 정리",
         "rrule": "FREQ=WEEKLY;BYDAY=FR;COUNT=3"},
        {"uid": "import-advisor", "summary": "교수님 면담", "start": at(7, "16:00"), "end": at(7, "16:30"),
         "location": "공학관 701호", "description": "중간 점검 피드백 반영 상황 공유"},
        {"uid": "import-rehearsal", "summary": "리허설", "start": at(12, "16:00"), "end": at(12, "18:00"),
         "location": "공학관 세미나실", "description": "최종 발표 리허설, 발표 순서와 시연 기기 점검"},
        {"uid": "import-final", "summary": "최종 발표", "start": at(13, "14:00"), "end": at(13, "15:00"),
         "location": "공학관 대강당", "description": "팀당 20분 발표와 시연"},
    ]


README = """# {name}

BookSwap(캠퍼스 중고 교재 거래 앱)을 만드는 5인 캡스톤 팀의 프로젝트 폴더를 흉내 낸 데모입니다.
이야기 속 오늘은 2026-10-28(수)이고, 파일 기록은 2026-10-27에서 끝납니다. 등장인물과 주소는 모두 가상입니다.

## 들어 있는 것
- `{name}/`: 점검할 폴더. 앱에서 이 폴더 하나만 추가하세요. 파일 {count}개.
- `{name}_answer_key.json`, `{name}_answer_key.md`: 정답표. 점검 폴더 밖에 두세요.
- `google_calendar_import.ics`: 내 구글 캘린더에 가져올 일정 (설정, 가져오기 및 내보내기).

## 메일 스레드
메일 다섯 스레드(17통)는 저장소의 `demo/mail_threads.json`에 있습니다.
`python tools/seed_mail_threads.py --me 내주소 --other 다른주소`로 계획을 먼저 보고, `--send`를 붙여 실제로 보냅니다.
앱 비밀번호가 없으면 `tools/mail_thread_kit.html`을 열어 손으로 보낼 수 있습니다.

## 캘린더를 연결할 때 주의
`google_calendar_import.ics`를 구글 캘린더에 가져오고 앱에서 구글 캘린더를 연결했다면,
설정의 제외 패턴에 `schedule/team_calendar.ics`를 추가하세요. 그렇지 않으면 같은 옛 일정(최종 발표 11/10, 리허설 11/9)이 두 번 지적됩니다.

## 기대 결과
- 문서만 읽으면 반영 안 됨 6건 (정답표 1~6번)
- 메일 T1, T2를 읽은 뒤 4건이 더해져 10건 (7~10번)
- 날짜 없는 회의 메모 하나는 날짜를 알 수 없다는 안내가 붙는 것이 정상입니다.
"""


def write_pack(variant: str, out: Path, pdf: bool = True) -> Path:
    name = VARIANTS[variant][0]
    pack = out / name
    root = pack / name
    written = write_folder(variant, root, pdf)
    key = answer_key(root)
    (pack / f"{name}_answer_key.json").write_bytes(json.dumps(key, ensure_ascii=False, indent=1).encode("utf-8"))
    (pack / f"{name}_answer_key.md").write_bytes(key_markdown(key, name).encode("utf-8"))
    (pack / "README.md").write_bytes(README.format(name=name, count=len(written)).encode("utf-8"))
    (pack / "google_calendar_import.ics").write_bytes(
        ics_text(calendar_import_events(), "BookSwap 데모 일정").encode("utf-8")
    )
    return pack


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(Path.home() / "Downloads"))
    parser.add_argument("--variant", choices=["capstone", "full", "both"], default="both")
    parser.add_argument("--no-pdf", action="store_true")
    args = parser.parse_args(argv)
    for variant in (["capstone", "full"] if args.variant == "both" else [args.variant]):
        pack = write_pack(variant, Path(args.out), pdf=not args.no_pdf)
        print(f"{variant}: {pack}")


if __name__ == "__main__":
    sys.exit(main())
