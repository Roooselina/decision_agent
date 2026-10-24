from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from decision_agent.sources import readers_office as office
from decision_agent.sources import readers_text as text
from decision_agent.sources.model import ParsedDocument

Reader = Callable[[Path, str], ParsedDocument]

READERS: dict[str, Reader] = {
    "md": text.read_text_file,
    "markdown": text.read_text_file,
    "txt": text.read_text_file,
    "text": text.read_text_file,
    "log": text.read_text_file,
    "rst": text.read_text_file,
    "csv": text.read_csv_file,
    "tsv": text.read_csv_file,
    "json": text.read_json_file,
    "jsonl": text.read_json_file,
    "html": text.read_html_file,
    "htm": text.read_html_file,
    "eml": text.read_eml_file,
    "vtt": text.read_subtitle_file,
    "srt": text.read_subtitle_file,
    "rtf": text.read_rtf_file,
    "ics": text.read_ics_file,
    "docx": office.read_docx_file,
    "docm": office.read_docx_file,
    "xlsx": office.read_xlsx_file,
    "xlsm": office.read_xlsx_file,
    "pptx": office.read_pptx_file,
    "pptm": office.read_pptx_file,
    "pdf": office.read_pdf_file,
    "odt": office.read_odf_file,
    "odp": office.read_odf_file,
    "ods": office.read_odf_file,
    "hwpx": office.read_hwpx_file,
}

# Formats we recognise but cannot read, with the reason shown to the user.
UNSUPPORTED_HINTS: dict[str, str] = {
    "hwp": "구형 한글(hwp)은 지원하지 않습니다. 한글에서 hwpx 또는 docx로 저장해 주세요.",
    "doc": "구형 Word(doc)는 지원하지 않습니다. docx로 저장해 주세요.",
    "xls": "구형 Excel(xls)은 지원하지 않습니다. xlsx 또는 csv로 저장해 주세요.",
    "ppt": "구형 PowerPoint(ppt)는 지원하지 않습니다. pptx로 저장해 주세요.",
    "msg": "Outlook msg는 지원하지 않습니다. eml로 저장해 주세요.",
}

SUPPORTED_EXTENSIONS = frozenset(READERS)


class UnsupportedFormat(Exception):
    pass


def extension_of(path: str | Path) -> str:
    return Path(path).suffix.lower().lstrip(".")


def is_supported(path: str | Path) -> bool:
    return extension_of(path) in SUPPORTED_EXTENSIONS


def read_document(path: Path, relative_to: Path | None = None) -> ParsedDocument:
    """Read one file into a ParsedDocument. Files are only ever opened for reading."""
    fmt = extension_of(path)
    reader = READERS.get(fmt)
    if reader is None:
        raise UnsupportedFormat(UNSUPPORTED_HINTS.get(fmt, f"지원하지 않는 형식입니다: .{fmt}"))
    doc = reader(path, fmt)
    if relative_to is not None:
        try:
            doc.path = path.relative_to(relative_to).as_posix()
        except ValueError:
            doc.path = path.name
    else:
        doc.path = path.name
    return doc
