from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from decision_agent.sources.model import Block, ParsedDocument, Section
from decision_agent.sources.readers_text import (
    BULLET_RE,
    CHECKBOX_RE,
    _clean_cell,
    rows_to_table,
)

MAX_XML_MEMBER_BYTES = 60 * 1024 * 1024
MAX_SHEET_ROWS = 5000
PDF_BULLET_RE = re.compile(r"^\s*(?:[•·▪●○■□–—*-]|\d{1,3}[.)])\s+(.+?)\s*$")


def _safe_xml(data: bytes) -> ET.Element:
    """Parse XML from untrusted files; entity declarations are refused outright."""
    if b"<!ENTITY" in data:
        raise ValueError("XML 엔티티 선언이 포함된 파일은 읽지 않습니다.")
    return ET.fromstring(data)


def _zip_member(archive: zipfile.ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > MAX_XML_MEMBER_BYTES:
        raise ValueError(f"압축 해제 크기가 너무 큰 항목입니다: {name}")
    return archive.read(name)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_docx_file(path: Path, fmt: str) -> ParsedDocument:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table as DocxTable
    from docx.text.paragraph import Paragraph

    document = Document(str(path))
    w_t, w_tab, w_br = qn("w:t"), qn("w:tab"), qn("w:br")

    def element_text(element) -> str:
        """Collect live text, including tracked insertions and excluding tracked deletions."""
        parts: list[str] = []
        for node in element.iter():
            if node.tag == w_t and node.text:
                parts.append(node.text)
            elif node.tag in (w_tab, w_br):
                parts.append(" ")
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    section = Section(name=path.stem)
    counter = 0
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            counter += 1
            text = element_text(item._p)
            if not text:
                continue
            style = (item.style.name if item.style is not None else "") or ""
            lowered = style.lower()
            p_pr = item._p.pPr
            numbered = p_pr is not None and p_pr.numPr is not None
            if lowered.startswith(("heading", "title", "제목")):
                level_match = re.search(r"(\d+)", style)
                level = int(level_match.group(1)) if level_match else 1
                section.blocks.append(
                    Block(text=text, kind="heading", level=level, location=str(counter))
                )
            elif "list" in lowered or "목록" in lowered or numbered:
                body = BULLET_RE.match(text)
                section.blocks.append(
                    Block(text=body.group(1) if body else text, kind="bullet", location=str(counter))
                )
            else:
                bullet = BULLET_RE.match(text)
                if bullet:
                    section.blocks.append(
                        Block(text=bullet.group(1), kind="bullet", location=str(counter))
                    )
                else:
                    section.blocks.append(Block(text=text, kind="para", location=str(counter)))
        elif isinstance(item, DocxTable):
            raw: list[list[object]] = []
            for row in item.rows:
                seen: list = []
                cells: list[str] = []
                for cell in row.cells:
                    if any(cell._tc is s for s in seen):
                        continue
                    seen.append(cell._tc)
                    cells.append(element_text(cell._tc))
                raw.append(cells)
            table = rows_to_table(raw)
            if table is not None:
                section.tables.append(table)
    meta: dict[str, str] = {}
    props = document.core_properties
    if props.title:
        meta["title"] = props.title
    if props.created:
        meta["created"] = props.created.date().isoformat()
    if props.modified:
        meta["modified"] = props.modified.date().isoformat()
    return ParsedDocument(path=str(path), fmt=fmt, sections=[section], meta=meta)


def read_xlsx_file(path: Path, fmt: str) -> ParsedDocument:
    from openpyxl import load_workbook

    workbook = load_workbook(str(path), read_only=True, data_only=True)
    doc = ParsedDocument(path=str(path), fmt=fmt)
    try:
        for sheet in workbook.worksheets:
            if getattr(sheet, "sheet_state", "visible") != "visible":
                continue
            raw: list[list[object]] = []
            for i, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                if i > MAX_SHEET_ROWS:
                    doc.warnings.append(f"{sheet.title}: {MAX_SHEET_ROWS}행까지만 읽었습니다.")
                    break
                raw.append(list(row))
            table = rows_to_table(raw)
            if table is not None:
                doc.sections.append(Section(name=sheet.title, tables=[table]))
    finally:
        workbook.close()
    return doc


def read_pptx_file(path: Path, fmt: str) -> ParsedDocument:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    presentation = Presentation(str(path))
    doc = ParsedDocument(path=str(path), fmt=fmt)

    def add_shape(shape, section: Section, title_shape) -> None:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            for inner in shape.shapes:
                add_shape(inner, section, title_shape)
            return
        if getattr(shape, "has_table", False) and shape.has_table:
            raw = [[cell.text.strip() for cell in row.cells] for row in shape.table.rows]
            table = rows_to_table([list(r) for r in raw])
            if table is not None:
                section.tables.append(table)
            return
        if not getattr(shape, "has_text_frame", False) or not shape.has_text_frame:
            return
        is_title = title_shape is not None and shape.shape_id == title_shape.shape_id
        for paragraph in shape.text_frame.paragraphs:
            text = "".join(run.text for run in paragraph.runs).strip()
            if not text:
                continue
            if is_title:
                section.blocks.append(Block(text=text, kind="heading", level=1))
            else:
                body = BULLET_RE.match(text)
                section.blocks.append(
                    Block(text=body.group(1) if body else text, kind="bullet", level=paragraph.level)
                )

    for number, slide in enumerate(presentation.slides, start=1):
        section = Section(name=f"slide{number}")
        title_shape = slide.shapes.title
        for shape in slide.shapes:
            add_shape(shape, section, title_shape)
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            for line in slide.notes_slide.notes_text_frame.text.splitlines():
                if line.strip():
                    section.blocks.append(Block(text=line.strip(), kind="para", location="notes"))
        for i, block in enumerate(section.blocks, start=1):
            block.location = f"s{number}:{block.location or i}"
        if section.blocks or section.tables:
            doc.sections.append(section)
    return doc


def read_pdf_file(path: Path, fmt: str) -> ParsedDocument:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    doc = ParsedDocument(path=str(path), fmt=fmt)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001 - any failure means the file cannot be read
            doc.warnings.append("암호가 걸린 PDF라 읽지 못했습니다.")
            return doc
    section = Section(name=path.stem)
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            location = f"p{page_number}:{line_number}"
            bullet = PDF_BULLET_RE.match(stripped)
            if bullet:
                body = bullet.group(1)
                box = CHECKBOX_RE.match(body)
                section.blocks.append(
                    Block(
                        text=box.group(2) if box else body,
                        kind="bullet",
                        checked=(box.group(1) in ("x", "X")) if box else None,
                        location=location,
                    )
                )
            else:
                section.blocks.append(Block(text=stripped, kind="para", location=location))
    if not section.blocks:
        doc.warnings.append("텍스트 층이 없습니다. 스캔본이면 OCR이 필요하며 현재 지원하지 않습니다.")
    else:
        doc.sections.append(section)
    return doc


def _odf_text(element: ET.Element) -> str:
    parts: list[str] = []

    def visit(node: ET.Element) -> None:
        name = _local(node.tag)
        if name == "s":
            parts.append(" " * int(node.attrib.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}c", "1")))
        elif name in {"tab", "line-break"}:
            parts.append(" ")
        if node.text and name not in {"annotation"}:
            parts.append(node.text)
        for child in node:
            if _local(child.tag) != "annotation":
                visit(child)
            if child.tail:
                parts.append(child.tail)

    visit(element)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def read_odf_file(path: Path, fmt: str) -> ParsedDocument:
    """OpenDocument text (odt), presentation (odp), and spreadsheet (ods) files."""
    with zipfile.ZipFile(path) as archive:
        root = _safe_xml(_zip_member(archive, "content.xml"))
    doc = ParsedDocument(path=str(path), fmt=fmt)
    section = Section(name=path.stem)

    def walk(node: ET.Element, in_list: bool) -> None:
        for child in node:
            name = _local(child.tag)
            if name == "h":
                text = _odf_text(child)
                if text:
                    level = int(child.attrib.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}outline-level", "1") or 1)
                    section.blocks.append(Block(text=text, kind="heading", level=level))
            elif name == "p":
                text = _odf_text(child)
                if text:
                    body = BULLET_RE.match(text)
                    if in_list or body:
                        section.blocks.append(
                            Block(text=body.group(1) if body else text, kind="bullet")
                        )
                    else:
                        section.blocks.append(Block(text=text, kind="para"))
            elif name == "table" and fmt != "ods":
                raw = []
                for row in child.iter():
                    if _local(row.tag) == "table-row":
                        raw.append([_odf_text(c) for c in row if _local(c.tag) == "table-cell"])
                table = rows_to_table([list(r) for r in raw])
                if table is not None:
                    section.tables.append(table)
            elif name == "list":
                walk(child, True)
            else:
                walk(child, in_list)

    if fmt == "ods":
        for table_node in root.iter():
            if _local(table_node.tag) != "table":
                continue
            raw = []
            for row in table_node:
                if _local(row.tag) != "table-row":
                    continue
                cells: list[str] = []
                for cell in row:
                    if _local(cell.tag) != "table-cell":
                        continue
                    repeat = int(
                        cell.attrib.get(
                            "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}number-columns-repeated",
                            "1",
                        )
                    )
                    text = _odf_text(cell)
                    cells.extend([text] * (repeat if text else min(repeat, 1)))
                raw.append(cells)
                if len(raw) > MAX_SHEET_ROWS:
                    break
            name_attr = table_node.attrib.get(
                "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}name", "sheet"
            )
            table = rows_to_table([list(r) for r in raw])
            if table is not None:
                doc.sections.append(Section(name=name_attr, tables=[table]))
        return doc
    walk(root, False)
    if section.blocks or section.tables:
        doc.sections.append(section)
    return doc


def read_hwpx_file(path: Path, fmt: str) -> ParsedDocument:
    """HWPX (Hancom, zip of XML). The older binary .hwp format is not supported."""
    doc = ParsedDocument(path=str(path), fmt=fmt)
    section = Section(name=path.stem)
    with zipfile.ZipFile(path) as archive:
        names = sorted(
            n for n in archive.namelist() if re.match(r"Contents/section\d+\.xml$", n)
        )
        for name in names:
            root = _safe_xml(_zip_member(archive, name))
            for paragraph in root.iter():
                if _local(paragraph.tag) != "p":
                    continue
                pieces = [
                    t.text for t in paragraph.iter() if _local(t.tag) == "t" and t.text
                ]
                text = re.sub(r"\s+", " ", "".join(pieces)).strip()
                if not text:
                    continue
                body = BULLET_RE.match(text)
                if body:
                    section.blocks.append(Block(text=body.group(1), kind="bullet"))
                else:
                    section.blocks.append(Block(text=text, kind="para"))
    if section.blocks:
        doc.sections.append(section)
    return doc


__all__ = [
    "_clean_cell",
    "read_docx_file",
    "read_hwpx_file",
    "read_odf_file",
    "read_pdf_file",
    "read_pptx_file",
    "read_xlsx_file",
]
