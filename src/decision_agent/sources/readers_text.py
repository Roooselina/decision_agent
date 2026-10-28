from __future__ import annotations

import csv
import datetime
import email
import io
import json
import re
from email import policy
from pathlib import Path

from decision_agent.sources.model import Block, ParsedDocument, Section, Table

BULLET_RE = re.compile(r"^\s*(?:[-*+\u2022\u00b7\u25aa\u25cf\u25cb\u25a0\u25a1\u2013]|\d{1,3}[.)])\s+(.+?)\s*$")
CHECKBOX_RE = re.compile(r"^\[( |x|X)\]\s*(.*)$")
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_EPOCH_KEYS = {"ts", "timestamp", "created", "updated", "time", "created_at", "updated_at"}


def decode_bytes(data: bytes) -> str:
    """Decode text files from Korean Windows environments: utf-8, utf-16 with BOM, cp949."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    stripped = stripped.removeprefix("|")
    stripped = stripped.removesuffix("|")
    return [cell.strip() for cell in stripped.split("|")]


def lines_to_section(text: str, name: str = "", markdown: bool = True) -> Section:
    """Convert plain or markdown text to blocks and pipe tables."""
    section = Section(name=name)
    raw_lines = text.splitlines()
    index = 0
    while index < len(raw_lines):
        line = raw_lines[index]
        number = index + 1
        starts_table = (
            markdown
            and line.lstrip().startswith("|")
            and index + 1 < len(raw_lines)
            and TABLE_SEP_RE.match(raw_lines[index + 1]) is not None
        )
        if starts_table:
            headers = _split_row(line)
            rows: list[list[str]] = []
            first = index + 3
            index += 2
            while index < len(raw_lines) and raw_lines[index].lstrip().startswith("|"):
                rows.append(_split_row(raw_lines[index]))
                index += 1
            section.tables.append(Table(headers=headers, rows=rows, first_row_location=first))
            continue
        index += 1
        if not line.strip():
            continue
        heading = HEADING_RE.match(line) if markdown else None
        if heading:
            section.blocks.append(
                Block(
                    text=heading.group(2),
                    kind="heading",
                    level=len(heading.group(1)),
                    location=str(number),
                )
            )
            continue
        bullet = BULLET_RE.match(line)
        if bullet:
            body = bullet.group(1)
            checked = None
            box = CHECKBOX_RE.match(body)
            if box:
                checked = box.group(1) in ("x", "X")
                body = box.group(2)
            if body:
                section.blocks.append(
                    Block(text=body, kind="bullet", checked=checked, location=str(number))
                )
            continue
        section.blocks.append(Block(text=line.strip(), kind="para", location=str(number)))
    return section


def read_text_file(path: Path, fmt: str) -> ParsedDocument:
    text = decode_bytes(path.read_bytes())
    section = lines_to_section(text, name=path.stem, markdown=fmt in {"md", "markdown"})
    return ParsedDocument(path=str(path), fmt=fmt, sections=[section])


def _clean_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.date().isoformat() if value.time() == datetime.time(0) else value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def rows_to_table(raw_rows: list[list[object]], first_row_number: int = 1) -> Table | None:
    """Use the first non-empty row as the header; keep later non-empty rows."""
    header_at = None
    for i, row in enumerate(raw_rows):
        if any(_clean_cell(c) for c in row):
            header_at = i
            break
    if header_at is None:
        return None
    headers: list[str] = []
    for n, cell in enumerate(raw_rows[header_at], start=1):
        label = _clean_cell(cell) or f"col{n}"
        while label in headers:
            label += "_"
        headers.append(label)
    rows = []
    for row in raw_rows[header_at + 1 :]:
        cells = [_clean_cell(c) for c in row]
        if any(cells):
            rows.append(cells[: len(headers)] + [""] * max(0, len(headers) - len(cells)))
    return Table(headers=headers, rows=rows, first_row_location=first_row_number + header_at + 1)


def read_csv_file(path: Path, fmt: str) -> ParsedDocument:
    text = decode_bytes(path.read_bytes())
    sample = text[:4096]
    delimiter = "\t" if fmt == "tsv" else ","
    if fmt == "csv":
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    raw = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    table = rows_to_table([list(r) for r in raw])
    doc = ParsedDocument(path=str(path), fmt=fmt)
    if table is not None:
        doc.sections.append(Section(name=path.stem, tables=[table]))
    return doc


def _epoch_to_date(value: object) -> str | None:
    try:
        number = float(str(value))
    except ValueError:
        return None
    if number > 1e11:
        number /= 1000.0
    if 9e8 < number < 4.2e9:
        return datetime.datetime.fromtimestamp(number, datetime.UTC).date().isoformat()
    return None


def _json_cell(key: str, value: object) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if key.lower() in _EPOCH_KEYS:
        converted = _epoch_to_date(value)
        if converted:
            return converted
    return _clean_cell(value)


def _records_to_table(records: list[dict]) -> Table:
    headers: list[str] = []
    for record in records:
        for key in record:
            if key not in headers:
                headers.append(str(key))
    rows = [[_json_cell(h, record.get(h)) for h in headers] for record in records]
    return Table(headers=headers, rows=rows, first_row_location=1)


def _collect_json(value: object, name: str, section: Section, sections: list[Section]) -> None:
    if isinstance(value, list):
        dicts = [v for v in value if isinstance(v, dict)]
        strings = [v for v in value if isinstance(v, str) and v.strip()]
        if dicts:
            sections.append(Section(name=name, tables=[_records_to_table(dicts)]))
        for i, text in enumerate(strings, start=1):
            section.blocks.append(Block(text=text.strip(), kind="bullet", location=str(i)))
    elif isinstance(value, dict):
        for key, inner in value.items():
            if isinstance(inner, (list, dict)):
                _collect_json(inner, str(key), section, sections)
            elif isinstance(inner, str) and inner.strip():
                section.blocks.append(Block(text=f"{key}: {inner.strip()}", kind="para"))


def read_json_file(path: Path, fmt: str) -> ParsedDocument:
    text = decode_bytes(path.read_bytes())
    doc = ParsedDocument(path=str(path), fmt=fmt)
    if fmt == "jsonl":
        records = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    records.append(item)
        if records:
            doc.sections.append(Section(name=path.stem, tables=[_records_to_table(records)]))
        return doc
    data = json.loads(text)
    root_section = Section(name=path.stem)
    extra: list[Section] = []
    _collect_json(data, path.stem, root_section, extra)
    if root_section.blocks:
        doc.sections.append(root_section)
    doc.sections.extend(extra)
    return doc


def read_html_file(path: Path, fmt: str) -> ParsedDocument:
    from bs4 import BeautifulSoup, Tag

    soup = BeautifulSoup(path.read_bytes(), "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "svg", "head"]):
        tag.decompose()
    title = path.stem
    section = Section(name=title)
    heading_tags = {f"h{i}": i for i in range(1, 7)}

    def own_text(element: Tag) -> str:
        parts = []
        for child in element.children:
            if isinstance(child, Tag):
                if child.name in {"ul", "ol", "table"}:
                    continue
                parts.append(child.get_text(" ", strip=True))
            else:
                parts.append(str(child).strip())
        return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()

    def walk(element: Tag) -> None:
        for child in element.children:
            if not isinstance(child, Tag):
                continue
            name = child.name
            if name in heading_tags:
                text = child.get_text(" ", strip=True)
                if text:
                    section.blocks.append(
                        Block(text=text, kind="heading", level=heading_tags[name])
                    )
            elif name == "table":
                raw = []
                for tr in child.find_all("tr"):
                    cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
                    raw.append(cells)
                table = rows_to_table([list(r) for r in raw])
                if table is not None:
                    section.tables.append(table)
            elif name == "li":
                text = own_text(child)
                if text:
                    section.blocks.append(Block(text=text, kind="bullet"))
                walk(child)
            elif name in {"p", "pre", "blockquote", "dt", "dd"}:
                text = child.get_text(" ", strip=True)
                if text:
                    for line in re.split(r"\n+", text):
                        if line.strip():
                            section.blocks.append(Block(text=line.strip(), kind="para"))
            else:
                walk(child)

    walk(soup)
    return ParsedDocument(path=str(path), fmt=fmt, sections=[section])


def _html_to_text(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text("\n")


def read_eml_file(path: Path, fmt: str) -> ParsedDocument:
    message = email.message_from_bytes(path.read_bytes(), policy=policy.default)
    subject = str(message.get("subject") or "").strip()
    date_value = ""
    raw_date = message.get("date")
    if raw_date is not None:
        try:
            date_value = email.utils.parsedate_to_datetime(str(raw_date)).date().isoformat()
        except (TypeError, ValueError):
            date_value = ""
    body_part = message.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        content = body_part.get_content()
        body = _html_to_text(content) if body_part.get_content_type() == "text/html" else content
    section = lines_to_section(body, name=subject or path.stem, markdown=False)
    # The heading carries only the date: a decision word in the subject would otherwise make
    # the whole mail a decision section and switch off sentence extraction (as for connectors).
    head: list[Block] = []
    if date_value:
        head.append(Block(text=date_value, kind="heading", level=1, location="0"))
    if subject:
        head.append(Block(text=f"제목: {subject}", kind="para", location="subject"))
    section.blocks[:0] = head
    meta = {"subject": subject}
    if date_value:
        meta["date"] = date_value
    return ParsedDocument(path=str(path), fmt=fmt, sections=[section], meta=meta)


_CUE_TIME_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?[.,]\d{1,3}\s*-->\s*\d{1,2}:\d{2}")
_CUE_NUMBER_RE = re.compile(r"^\d+$")


def read_subtitle_file(path: Path, fmt: str) -> ParsedDocument:
    """Meeting transcripts (vtt, srt): drop timing lines, keep spoken text in order."""
    section = Section(name=path.stem)
    last = ""
    for number, line in enumerate(decode_bytes(path.read_bytes()).splitlines(), start=1):
        text = line.strip()
        if (
            not text
            or text.upper().startswith(("WEBVTT", "NOTE", "STYLE"))
            or _CUE_TIME_RE.match(text)
            or _CUE_NUMBER_RE.match(text)
        ):
            continue
        text = re.sub(r"<[^>]+>", "", text)
        if text and text != last:
            section.blocks.append(Block(text=text, kind="para", location=str(number)))
            last = text
    return ParsedDocument(path=str(path), fmt=fmt, sections=[section])


_RTF_SKIP_GROUPS = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "header", "footer", "themedata",
    "datastore", "latentstyles", "generator", "listtable", "listoverridetable", "rsidtbl",
}


def rtf_to_text(data: str, codepage: str = "cp949") -> str:
    """Small RTF reader: handles paragraphs, hex escapes, unicode escapes, and skip groups."""
    out: list[str] = []
    pending = bytearray()
    stack: list[bool] = []
    skip = False
    i = 0
    n = len(data)

    def flush() -> None:
        if pending:
            out.append(bytes(pending).decode(codepage, errors="replace"))
            pending.clear()

    while i < n:
        ch = data[i]
        if ch == "{":
            stack.append(skip)
            i += 1
        elif ch == "}":
            flush()
            skip = stack.pop() if stack else False
            i += 1
        elif ch == "\\":
            i += 1
            if i >= n:
                break
            nxt = data[i]
            if nxt == "'":
                hex_value = data[i + 1 : i + 3]
                i += 3
                if not skip:
                    try:
                        pending.append(int(hex_value, 16))
                    except ValueError:
                        pass
            elif nxt in "\\{}":
                flush()
                if not skip:
                    out.append(nxt)
                i += 1
            elif nxt == "*":
                skip = True
                i += 1
            else:
                match = re.match(r"([a-zA-Z]+)(-?\d+)? ?", data[i:])
                if not match:
                    i += 1
                    continue
                word, argument = match.group(1), match.group(2)
                i += match.end()
                if word in _RTF_SKIP_GROUPS:
                    skip = True
                elif word in {"par", "line", "row"} and not skip:
                    flush()
                    out.append("\n")
                elif word in {"tab", "cell"} and not skip:
                    flush()
                    out.append(" ")
                elif word == "u" and argument and not skip:
                    flush()
                    code = int(argument)
                    out.append(chr(code if code >= 0 else code + 65536))
                    if i < n and data[i] == "?":
                        i += 1
        elif ch in "\r\n":
            i += 1
        else:
            if not skip:
                flush()
                out.append(ch)
            i += 1
    flush()
    return "".join(out)


def read_rtf_file(path: Path, fmt: str) -> ParsedDocument:
    raw = path.read_bytes().decode("latin-1")
    match = re.search(r"\\ansicpg(\d+)", raw)
    codepage = f"cp{match.group(1)}" if match else "cp949"
    try:
        text = rtf_to_text(raw, codepage)
    except LookupError:
        text = rtf_to_text(raw, "cp949")
    section = lines_to_section(text, name=path.stem, markdown=False)
    doc = ParsedDocument(path=str(path), fmt=fmt, sections=[section])
    doc.warnings.append("RTF는 단순 변환으로 읽어 서식과 표가 유실될 수 있습니다.")
    return doc


def _unfold_ics(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _ics_unescape(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _ics_date(value: str) -> str:
    digits = re.match(r"(\d{4})(\d{2})(\d{2})", value.strip())
    return f"{digits.group(1)}-{digits.group(2)}-{digits.group(3)}" if digits else ""


def _ics_clock(value: str) -> str:
    """Start time as HH:MM in local time. UTC times (ending in Z) are converted; dates give ""."""
    match = re.match(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})?(Z?)", value.strip())
    if not match:
        return ""
    year, month, day, hour, minute = (int(match.group(i)) for i in range(1, 6))
    if match.group(7):
        try:
            moment = datetime.datetime(year, month, day, hour, minute, tzinfo=datetime.UTC)
        except ValueError:
            return ""
        local = moment.astimezone()
        return f"{local.hour:02d}:{local.minute:02d}"
    return f"{hour:02d}:{minute:02d}"


MEETING_WORDS = re.compile(r"회의|미팅|meeting|minutes|sync|standup|스탠드업|리뷰|review", re.IGNORECASE)


def read_ics_file(path: Path, fmt: str) -> ParsedDocument:
    """Calendar files: events become a schedule table; meeting events with notes also become notes."""
    events: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in _unfold_ics(decode_bytes(path.read_bytes())):
        upper = line.upper()
        if upper.startswith("BEGIN:VEVENT"):
            current = {}
        elif upper.startswith("END:VEVENT"):
            if current is not None:
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            head, _, value = line.partition(":")
            key = head.split(";")[0].upper()
            if key in {"SUMMARY", "DESCRIPTION", "LOCATION", "STATUS", "DTSTART"}:
                current[key] = _ics_unescape(value) if key != "DTSTART" else value
    rows = []
    notes = Section(name="meeting_notes")
    for event in events:
        if event.get("STATUS", "").upper() == "CANCELLED":
            continue
        date = _ics_date(event.get("DTSTART", ""))
        summary = event.get("SUMMARY", "").strip()
        description = event.get("DESCRIPTION", "").strip()
        if not summary:
            continue
        rows.append(
            [summary, date, _ics_clock(event.get("DTSTART", "")), description.replace("\n", " "),
             event.get("LOCATION", "")]
        )
        if date and description and MEETING_WORDS.search(summary):
            notes.blocks.append(Block(text=f"{date} {summary}", kind="heading", level=2))
            for part in description.splitlines():
                part = part.strip()
                if part:
                    bullet = BULLET_RE.match(part)
                    notes.blocks.append(
                        Block(text=bullet.group(1) if bullet else part, kind="bullet")
                    )
    doc = ParsedDocument(path=str(path), fmt=fmt)
    if rows:
        table = Table(
            headers=["summary", "date", "time", "description", "location"],
            rows=rows,
            first_row_location=1,
        )
        doc.sections.append(Section(name="schedule", tables=[table]))
    if notes.blocks:
        doc.sections.append(notes)
    return doc
