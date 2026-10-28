from __future__ import annotations

import fnmatch
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from decision_agent.models import (
    Artifact,
    ArtifactKind,
    DecisionScope,
    MeetingItem,
    ProjectConfig,
    ScopeKind,
)
from decision_agent.parsing.loader import infer_scope_from_text
from decision_agent.scope import normalize_scope
from decision_agent.sources.dates import parse_date
from decision_agent.sources.model import Block, ParsedDocument, Section, Table

MEETING = "MEETING"
IGNORE = "IGNORE"
KIND_NAMES = {MEETING, IGNORE} | {k.value for k in ArtifactKind}

_HINTS: list[tuple[str, re.Pattern[str]]] = [
    (
        MEETING,
        re.compile(
            r"회의|미팅|회의록|meeting|minutes|\bmom\b|transcript|녹취|스크럼|scrum|standup|회고|retro",
            re.IGNORECASE,
        ),
    ),
    (
        ArtifactKind.REQUIREMENT.value,
        re.compile(
            r"요구\s*사항|requirement|\breqs?\b|\bsrs\b|명세|\bspec\b|specification|유스케이스|user stor",
            re.IGNORECASE,
        ),
    ),
    (
        ArtifactKind.TEST.value,
        re.compile(r"\btests?\b|테스트|\bqa\b|시험|검증|\btc\b", re.IGNORECASE),
    ),
    (
        ArtifactKind.SCHEDULE.value,
        re.compile(
            r"schedule|일정|timeline|calendar|milestone|마일스톤|로드맵|roadmap|gantt|간트|캘린더",
            re.IGNORECASE,
        ),
    ),
    (
        ArtifactKind.TASK.value,
        re.compile(
            r"\btasks?\b|todo|할 ?일|작업|백로그|backlog|이슈|\bissues?\b|ticket|티켓|jira|kanban|칸반|\bwbs\b",
            re.IGNORECASE,
        ),
    ),
]

_DECISION_HEAD = re.compile(
    r"결정|합의|확정|decision|agreed|action ?items?|액션|조치|실행 ?항목|follow ?up|todo", re.IGNORECASE
)
_SKIP_HEAD = re.compile(r"참석|참가|불참|출석|attendee|participants", re.IGNORECASE)
_DECISION_KEYWORDS = re.compile(
    r"결정|하기로|한다|합니다|하자|변경|교체|취소|제외|연기|중단|폐기|유지|도입|채택|확정|"
    r"decid|agree|will |cancel|postpone|switch|replace|drop|adopt|approved",
    re.IGNORECASE,
)
_DATE_LABEL_WORDS = re.compile(
    r"일시|날짜|회의일|개최|회의록|회의|미팅|주간|정기|킥오프|date|when|meeting|minutes|\d차|팀",
    re.IGNORECASE,
)

_COLUMN_SYNONYMS: dict[str, tuple[str, ...]] = {
    "id": ("id", "번호", "no", "no.", "key", "ticket", "티켓", "이슈번호", "요구사항id", "req id"),
    "title": (
        "title", "제목", "작업명", "작업", "task", "할 일", "할일", "summary", "name", "이름",
        "요구사항", "requirement", "항목", "테스트", "test case", "테스트 케이스", "케이스",
        "시나리오", "내용", "description", "설명", "text", "message", "일정", "event", "이벤트",
        "activity", "결정 사항", "결정사항", "결정",
    ),
    "status": ("status", "상태", "진행", "진행상태", "state"),
    "assignee": ("assignee", "owner", "담당", "담당자", "작성자", "책임자", "worker"),
    "date": (
        "date", "날짜", "일자", "due", "due date", "기한", "마감", "마감일", "시작", "시작일",
        "start", "end", "종료일", "예정일", "deadline", "일시", "ts", "timestamp", "created",
    ),
    "release": ("release", "릴리스", "릴리즈", "버전", "version", "phase", "단계"),
    "module": ("module", "모듈", "component", "컴포넌트"),
    "user_group": ("user_group", "user group", "사용자 그룹", "사용자", "대상 사용자", "role", "권한"),
    "environment": ("environment", "env", "환경"),
}
_MEETING_TEXT_COLUMNS = ("결정 사항", "결정사항", "결정", "decision", "decisions", "내용", "안건", "논의", "item", "text")
_MEETING_DATE_COLUMNS = ("date", "날짜", "일자", "회의일", "일시", "ts", "timestamp")


@dataclass
class DocSummary:
    path: str
    fmt: str
    kinds: list[str] = field(default_factory=list)
    meeting_items: int = 0
    artifacts: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class BuildResult:
    meetings: list[MeetingItem] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    summaries: list[DocSummary] = field(default_factory=list)
    unclassified: list[str] = field(default_factory=list)


def _normalize_hint_text(text: str) -> str:
    return re.sub(r"[_\-./\\]+", " ", text)


def _first_hint(text: str, tabular: bool) -> str | None:
    cleaned = _normalize_hint_text(text)
    matches = [kind for kind, pattern in _HINTS if pattern.search(cleaned)]
    if not matches:
        return None
    if MEETING in matches and ArtifactKind.SCHEDULE.value in matches and tabular:
        return ArtifactKind.SCHEDULE.value
    return matches[0]


def _column_map(headers: list[str]) -> dict[str, int]:
    """Map semantic roles to column indexes using exact synonym matches first, then substrings."""
    lowered = [h.strip().lower() for h in headers]
    mapping: dict[str, int] = {}
    for role, names in _COLUMN_SYNONYMS.items():
        for name in names:
            if name in lowered:
                mapping[role] = lowered.index(name)
                break
    for role, names in _COLUMN_SYNONYMS.items():
        if role in mapping or role in {"id", "title"}:
            continue
        for i, header in enumerate(lowered):
            if i not in mapping.values() and any(len(n) >= 2 and n in header for n in names):
                mapping[role] = i
                break
    return mapping


def _pick_title_column(table: Table, mapping: dict[str, int]) -> int | None:
    if "title" in mapping:
        return mapping["title"]
    used = set(mapping.values())
    best_index, best_len = None, 0.0
    for i in range(len(table.headers)):
        if i in used:
            continue
        values = [row[i] for row in table.rows if i < len(row) and row[i]]
        if not values:
            continue
        average = sum(len(v) for v in values) / len(values)
        if average > best_len:
            best_index, best_len = i, average
    return best_index


def _slug(path: str, section_name: str = "") -> str:
    base = re.sub(r"\.[A-Za-z0-9]+$", "", path)
    base = re.sub(r"[^0-9A-Za-z가-힣]+", "-", base).strip("-")[:40] or "doc"
    if section_name:
        extra = re.sub(r"[^0-9A-Za-z가-힣]+", "-", section_name).strip("-")[:20]
        if extra:
            base += f"#{extra}"
    return base


def _hash_key(kind: str, text: str) -> str:
    return hashlib.sha1(f"{kind}|{text}".encode()).hexdigest()[:6]


class _IdAllocator:
    def __init__(self) -> None:
        self.used: set[str] = set()

    def take(self, base: str) -> str:
        candidate = base
        n = 2
        while candidate in self.used:
            candidate = f"{base}~{n}"
            n += 1
        self.used.add(candidate)
        return candidate


def _is_date_header(block: Block, default_year: int | None) -> str | None:
    found = parse_date(block.text, default_year)
    if found is None or block.kind == "bullet":
        return None
    if block.kind == "heading":
        return found
    remainder = re.sub(
        r"(?<!\d)\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2}|(?<!\d)\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일"
        r"|(?<!\d)\d{1,2}\s*월\s*\d{1,2}\s*일|(?<!\d)(?:19|20)\d{6}(?!\d)",
        " ",
        block.text,
    )
    remainder = _DATE_LABEL_WORDS.sub(" ", remainder)
    remainder = re.sub(r"[\s:：\-–·()\[\]/,.]+", "", remainder)
    return found if len(remainder) <= 4 else None


def _meeting_items_from_blocks(
    doc: ParsedDocument,
    section: Section,
    fallback_date: str | None,
    default_year: int | None,
    carry_date: str | None,
    warnings: list[str],
) -> tuple[list[MeetingItem], str | None]:
    items: list[MeetingItem] = []
    current = carry_date
    has_bullets = any(b.kind == "bullet" for b in section.blocks)
    has_decision_heading = any(
        b.kind == "heading" and _DECISION_HEAD.search(b.text) for b in section.blocks
    )
    free_text = not has_bullets and not has_decision_heading
    in_decision = False
    skipping = False
    undated = 0
    used_fallback = 0
    for index, block in enumerate(section.blocks, start=1):
        header_date = _is_date_header(block, default_year)
        if header_date:
            current = header_date
            continue
        if block.kind == "heading":
            in_decision = bool(_DECISION_HEAD.search(block.text))
            skipping = bool(_SKIP_HEAD.search(block.text))
            continue
        if skipping or block.location in ("subject", "sender"):
            # The subject and sender lines of a mail document are context, not decisions.
            continue
        text = block.text.strip()
        if block.kind == "bullet":
            accept = True
        elif block.kind == "para":
            if in_decision:
                accept = True
            elif free_text:
                accept = bool(_DECISION_KEYWORDS.search(text)) and 6 <= len(text) <= 300
            else:
                accept = False
        else:
            accept = False
        if not accept or len(text) < 2:
            continue
        date = current or fallback_date
        if date is None:
            undated += 1
            continue
        if current is None:
            used_fallback += 1
        items.append(
            MeetingItem(
                date=date,
                text=text[:400],
                source_doc=doc.path,
                location=block.location or str(index),
            )
        )
    if undated:
        warnings.append(f"날짜를 알 수 없어 건너뛴 회의 항목 {undated}건")
    if used_fallback:
        warnings.append(
            f"본문에서 날짜를 찾지 못한 회의 항목 {used_fallback}건에 파일 날짜({fallback_date})를 사용했습니다."
        )
    return items, current


def _meeting_items_from_table(
    doc: ParsedDocument,
    table: Table,
    fallback_date: str | None,
    default_year: int | None,
    warnings: list[str],
    heading_date: bool = False,
) -> list[MeetingItem]:
    """heading_date: the fallback is the date of a dated heading above the table, not a guess."""
    lowered = [h.strip().lower() for h in table.headers]
    text_index = next((lowered.index(n) for n in _MEETING_TEXT_COLUMNS if n in lowered), None)
    if text_index is None:
        text_index = _pick_title_column(table, _column_map(table.headers))
    date_index = next((lowered.index(n) for n in _MEETING_DATE_COLUMNS if n in lowered), None)
    if text_index is None:
        return []
    items: list[MeetingItem] = []
    undated = 0
    used_fallback = 0
    for offset, row in enumerate(table.rows):
        text = row[text_index].strip() if text_index < len(row) else ""
        if not text:
            continue
        date = None
        if date_index is not None and date_index < len(row):
            date = parse_date(row[date_index], default_year)
        if date is None:
            date = fallback_date
            used_fallback += 1
        if date is None:
            undated += 1
            used_fallback -= 1
            continue
        items.append(
            MeetingItem(
                date=date,
                text=text[:400],
                source_doc=doc.path,
                location=str(table.first_row_location + offset),
            )
        )
    if undated:
        warnings.append(f"날짜를 알 수 없어 건너뛴 회의 표 행 {undated}건")
    if used_fallback and not heading_date:
        warnings.append(
            f"날짜 칸이 없거나 비어 있는 회의 표 행 {used_fallback}건에 문서 날짜({fallback_date})를 사용했습니다."
        )
    return items


def _heading_scope(
    heading: str | None, config: ProjectConfig, aliases: dict[str, Any]
) -> DecisionScope | None:
    if not heading:
        return None
    scope = infer_scope_from_text(heading, config, aliases)
    return None if scope.scope_kind == ScopeKind.UNKNOWN else scope


def _artifacts_from_blocks(
    kind: str,
    doc: ParsedDocument,
    section: Section,
    config: ProjectConfig,
    aliases: dict[str, Any],
    default_year: int | None,
    ids: _IdAllocator,
    slug: str,
) -> list[Artifact]:
    artifact_kind = ArtifactKind(kind)
    has_bullets = any(b.kind == "bullet" for b in section.blocks)
    heading: str | None = None
    result: list[Artifact] = []
    for index, block in enumerate(section.blocks, start=1):
        if block.kind == "heading" or block.location == "subject":
            # A mail subject is not an item, but its words still give the scope.
            heading = block.text
            continue
        if block.location == "sender":
            continue
        if has_bullets and block.kind != "bullet":
            continue
        text = block.text.strip()
        if len(text) < (4 if block.kind == "bullet" else 6) or len(text) > 400:
            continue
        scope = infer_scope_from_text(text, config, aliases)
        if scope.scope_kind == ScopeKind.UNKNOWN:
            inherited = _heading_scope(heading, config, aliases)
            if inherited is not None:
                scope = inherited
        metadata: dict[str, Any] = {}
        if block.checked is not None:
            metadata["status"] = "DONE" if block.checked else "TODO"
        if artifact_kind == ArtifactKind.SCHEDULE:
            metadata["date"] = parse_date(text, default_year) or ""
        result.append(
            Artifact(
                id=ids.take(f"{slug}:{_hash_key(kind, text)}"),
                kind=artifact_kind,
                text=text,
                source_doc=doc.path,
                location=block.location or str(index),
                scope=scope,
                metadata=metadata,
            )
        )
    return result


def _artifacts_from_table(
    kind: str,
    doc: ParsedDocument,
    table: Table,
    config: ProjectConfig,
    aliases: dict[str, Any],
    default_year: int | None,
    ids: _IdAllocator,
    slug: str,
    warnings: list[str],
) -> list[Artifact]:
    artifact_kind = ArtifactKind(kind)
    mapping = _column_map(table.headers)
    title_index = _pick_title_column(table, mapping)
    if title_index is None:
        return []
    result: list[Artifact] = []
    bad_dates = 0

    def cell(row: list[str], role: str) -> str:
        index = mapping.get(role)
        return row[index].strip() if index is not None and index < len(row) else ""

    for offset, row in enumerate(table.rows):
        text = row[title_index].strip() if title_index < len(row) else ""
        if not text:
            continue
        raw = {d: (cell(row, d) or None) for d in ("release", "module", "user_group", "environment")}
        if any(raw.values()):
            scope = normalize_scope(raw, config, aliases)
        else:
            scope = infer_scope_from_text(text, config, aliases)
        row_id = cell(row, "id")
        base = f"{slug}:{row_id}" if row_id else f"{slug}:{_hash_key(kind, text)}"
        metadata: dict[str, Any] = {}
        if cell(row, "status"):
            metadata["status"] = cell(row, "status")
        if cell(row, "assignee"):
            metadata["assignee"] = cell(row, "assignee")
        if artifact_kind == ArtifactKind.SCHEDULE or cell(row, "date"):
            raw_date = cell(row, "date")
            parsed = parse_date(raw_date, default_year) if raw_date else None
            if raw_date and parsed is None:
                bad_dates += 1
            metadata["date"] = parsed or raw_date
        result.append(
            Artifact(
                id=ids.take(base),
                kind=artifact_kind,
                text=text[:400],
                source_doc=doc.path,
                location=str(table.first_row_location + offset),
                scope=scope,
                metadata=metadata,
            )
        )
    if bad_dates:
        warnings.append(f"날짜 형식을 해석하지 못한 행 {bad_dates}건")
    return result


def _infer_table_kind(table: Table) -> str | None:
    mapping = _column_map(table.headers)
    if "status" in mapping or "assignee" in mapping:
        return ArtifactKind.TASK.value
    if "date" in mapping:
        return ArtifactKind.SCHEDULE.value
    return None


def _infer_text_kind(section: Section, default_year: int | None) -> str | None:
    has_date_header = any(_is_date_header(b, default_year) for b in section.blocks)
    has_bullet = any(b.kind == "bullet" for b in section.blocks)
    if has_date_header and has_bullet:
        return MEETING
    return None


def _override_for(path: str, overrides: dict[str, str]) -> str | None:
    lowered = path.lower()
    for pattern, kind in overrides.items():
        if fnmatch.fnmatch(lowered, pattern.lower()) or fnmatch.fnmatch(
            lowered.rsplit("/", 1)[-1], pattern.lower()
        ):
            kind = kind.upper()
            if kind in KIND_NAMES:
                return kind
    return None


def classify_section(
    doc: ParsedDocument,
    section: Section,
    table: Table | None,
    overrides: dict[str, str],
    default_year: int | None,
) -> str | None:
    """Decide what a section or table is: MEETING, an ArtifactKind value, IGNORE, or None."""
    forced = _override_for(doc.path, overrides)
    if forced:
        return forced
    declared = doc.meta.get("kind", "").upper()
    if declared in KIND_NAMES:
        return declared
    tabular = table is not None
    for text in (section.name if len(doc.sections) > 1 else "", doc.path):
        if text:
            found = _first_hint(text, tabular)
            if found:
                return found
    if table is not None:
        return _infer_table_kind(table)
    return _infer_text_kind(section, default_year)


def build_inputs(
    docs: list[ParsedDocument],
    config: ProjectConfig,
    aliases: dict[str, Any],
    overrides: dict[str, str] | None = None,
) -> BuildResult:
    """Turn parsed documents into meeting items and artifacts for the existing pipeline."""
    overrides = overrides or {}
    result = BuildResult()
    ids = _IdAllocator()
    for doc in sorted(docs, key=lambda d: d.path):
        summary = DocSummary(path=doc.path, fmt=doc.fmt, warnings=list(doc.warnings))
        fallback = (
            parse_date(doc.path)
            or doc.meta.get("date")
            or doc.meta.get("mtime_date")
            or doc.meta.get("modified")
            or doc.meta.get("created")
        )
        default_year = int(fallback[:4]) if fallback else None
        carry: str | None = None
        multi = len([s for s in doc.sections if s.blocks or s.tables]) > 1
        for section in doc.sections:
            units: list[tuple[Section, Table | None]] = []
            if section.blocks:
                units.append((section, None))
            units.extend((section, table) for table in section.tables)
            for sec, table in units:
                kind = classify_section(doc, sec, table, overrides, default_year)
                if kind is None or kind == IGNORE:
                    if kind is None:
                        label = f"{doc.path}" + (f" [{sec.name}]" if multi and sec.name else "")
                        result.unclassified.append(label)
                    continue
                if kind not in summary.kinds:
                    summary.kinds.append(kind)
                local_warnings: list[str] = []
                slug = _slug(doc.path, sec.name if multi else "")
                if kind == MEETING:
                    if table is not None:
                        items = _meeting_items_from_table(
                            doc, table, carry or fallback, default_year, local_warnings,
                            heading_date=carry is not None,
                        )
                    else:
                        items, carry = _meeting_items_from_blocks(
                            doc, sec, fallback, default_year, carry, local_warnings
                        )
                    result.meetings.extend(items)
                    summary.meeting_items += len(items)
                else:
                    if table is not None:
                        built = _artifacts_from_table(
                            kind, doc, table, config, aliases, default_year, ids, slug,
                            local_warnings,
                        )
                    else:
                        built = _artifacts_from_blocks(
                            kind, doc, sec, config, aliases, default_year, ids, slug
                        )
                    result.artifacts.extend(built)
                    summary.artifacts += len(built)
                summary.warnings.extend(local_warnings)
        result.summaries.append(summary)
    result.meetings.sort(key=lambda m: (m.date, m.source_doc, m.location))
    return result
