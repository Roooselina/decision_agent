from __future__ import annotations

import datetime
import hashlib
import html
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from decision_agent.connectors.http import Http, HttpResponse
from decision_agent.sources.model import Block, ParsedDocument, Section, Table


class ConnectorError(RuntimeError):
    """A failure the user can understand and act on. The message is shown in the dashboard."""


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


@dataclass(frozen=True)
class OptionSpec:
    name: str
    label: str
    kind: str = "text"  # text, number, list, bool
    default: Any = ""
    help: str = ""
    required: bool = False


@dataclass(frozen=True)
class SecretSpec:
    name: str
    label: str
    help: str = ""
    required: bool = True


@dataclass
class ConnectorContext:
    http: Http
    secrets: SecretStore
    oauth: Any = None  # OAuthManager, set by the manager when the connector needs it


@dataclass
class FetchOutcome:
    docs: dict[str, ParsedDocument] = field(default_factory=dict)
    summary: str = ""


class Connector:
    """Base class. Subclasses set the class attributes and implement fetch()."""

    id = ""
    label = ""
    description = ""
    guide: tuple[str, ...] = ()
    options: tuple[OptionSpec, ...] = ()
    secrets: tuple[SecretSpec, ...] = ()
    uses_oauth = False
    reads = ""  # one plain sentence on exactly what is read

    def __init__(self, options: dict[str, Any], ctx: ConnectorContext) -> None:
        self.opts = {spec.name: options.get(spec.name, spec.default) for spec in self.options}
        self.ctx = ctx

    def problems(self) -> list[str]:
        """Missing settings, as messages. An empty list means fetch() may run."""
        issues = []
        for spec in self.options:
            value = self.opts.get(spec.name)
            if spec.required and (value is None or value == "" or value == []):
                issues.append(f"'{spec.label}' 값을 입력하세요.")
        for secret in self.secrets:
            if secret.required and not self.ctx.secrets.get(self.secret_key(secret.name)):
                issues.append(f"'{secret.label}'을(를) 저장하세요.")
        if self.uses_oauth and not self.ctx.secrets.get(self.secret_key("oauth")):
            issues.append("'계정 연결'을 눌러 로그인하고 읽기 권한을 허용하세요.")
        return issues

    def secret_key(self, name: str) -> str:
        return f"connector.{self.id}.{name}"

    def secret(self, name: str) -> str:
        value = self.ctx.secrets.get(self.secret_key(name))
        if not value:
            raise ConnectorError(f"'{name}' 값이 저장되어 있지 않습니다.")
        return value

    def text_option(self, name: str) -> str:
        return str(self.opts.get(name) or "").strip()

    def int_option(self, name: str, default: int, low: int, high: int) -> int:
        try:
            value = int(self.opts.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))

    def list_option(self, name: str) -> list[str]:
        value = self.opts.get(name) or []
        if isinstance(value, str):
            value = re.split(r"[\n,]", value)
        return [str(v).strip() for v in value if str(v).strip()]

    def oauth_app(self) -> Any:
        """The OAuth client description. Only connectors with uses_oauth implement this."""
        raise ConnectorError("이 연결은 로그인 창이 필요 없습니다.")

    def fetch(self, today: datetime.date) -> FetchOutcome:
        raise NotImplementedError


def check_response(response: HttpResponse, service: str) -> HttpResponse:
    """Raise a readable ConnectorError for a failed response."""
    if response.ok:
        return response
    if response.status in {401, 403}:
        raise ConnectorError(f"{service}: 인증에 실패했거나 읽기 권한이 없습니다 ({response.status}).")
    if response.status == 404:
        raise ConnectorError(f"{service}: 대상을 찾을 수 없습니다. 이름이나 주소를 확인하세요.")
    if response.status == 429:
        raise ConnectorError(f"{service}: 요청이 너무 많습니다. 잠시 뒤 다시 시도합니다.")
    if response.status in {301, 302, 303, 307, 308}:
        raise ConnectorError(f"{service}: 주소가 다른 곳으로 이동했습니다. 주소를 확인하세요.")
    raise ConnectorError(f"{service}: 서비스 오류 ({response.status}).")


# text helpers shared by the connectors

_TAG_RE = re.compile(r"<[^>]+>")
_BREAK_RE = re.compile(r"<\s*(br|/p|/div|/li|/tr|/h[1-6])\s*/?\s*>", re.IGNORECASE)
_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_BULLET_RE = re.compile(r"^\s*(?:[-*•·]|\d+[.)])\s+(.*)$")


def strip_html(value: str) -> str:
    value = _STYLE_RE.sub(" ", value)
    value = _BREAK_RE.sub("\n", value)
    value = _TAG_RE.sub(" ", value)
    return html.unescape(value)


def clean_message_text(value: str, limit: int = 6000) -> list[str]:
    """Plain lines of a message body without quoted replies, signatures, and link noise."""
    lines: list[str] = []
    for raw in value.replace("\r", "").split("\n"):
        line = raw.strip()
        if line == "--":
            break
        if not line or line.startswith(">"):
            continue
        if line.lower().startswith(("sent from my", "보낸 사람:", "from:")):
            continue
        if re.match(r"^(on .+ wrote:|.+님이 작성:|-----\s*original message)", line, re.IGNORECASE):
            break
        line = re.sub(r"https?://\S+", "", line).strip()
        if len(line) >= 2:
            lines.append(line)
    out, total = [], 0
    for line in lines:
        total += len(line)
        if total > limit:
            break
        out.append(line)
    return out


def message_document(
    subject: str, date: str, sender: str, lines: list[str], kind: str = "MEETING"
) -> ParsedDocument:
    """One message as a meeting-style document: a dated heading, then its lines."""
    section = Section(name="message")
    section.blocks.append(Block(text=f"{date} {subject}".strip(), kind="heading", level=2))
    for index, line in enumerate(lines, start=1):
        match = _BULLET_RE.match(line)
        if match:
            section.blocks.append(Block(text=match.group(1), kind="bullet", location=str(index)))
        else:
            section.blocks.append(Block(text=line, kind="para", location=str(index)))
    doc = ParsedDocument(path="", fmt="connector", sections=[section])
    doc.meta["kind"] = kind
    doc.meta["date"] = date
    if sender:
        doc.meta["from"] = sender[:120]
    return doc


def safe_name(text: str, limit: int = 50) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", text).strip() or "untitled"
    return cleaned[:limit].strip()


def unique_name(prefix: str, key: str) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:6]
    return f"{safe_name(prefix)} [{digest}]"


@dataclass
class CalendarEvent:
    summary: str
    date: str
    description: str = ""
    location: str = ""
    cancelled: bool = False
    time: str = ""


def clock_of(raw: str) -> str:
    """HH:MM from an ISO date-time such as 2026-10-26T13:00:00+09:00, or "" for all-day dates."""
    match = re.match(r"\d{4}-\d{2}-\d{2}T(\d{2}):(\d{2})", raw or "")
    return f"{match.group(1)}:{match.group(2)}" if match else ""


MEETING_WORDS = re.compile(r"회의|미팅|회고|스크럼|킥오프|meeting|sync|standup|retro|review", re.IGNORECASE)


def calendar_documents(events: list[CalendarEvent]) -> dict[str, ParsedDocument]:
    """Events become a schedule table; meetings that carry notes also become meeting notes."""
    rows = []
    notes = Section(name="meeting_notes")
    for event in events:
        if event.cancelled or not event.summary.strip() or not event.date:
            continue
        description = event.description.strip()
        rows.append(
            [
                event.summary.strip(), event.date, event.time,
                description.replace("\n", " ")[:300], event.location,
            ]
        )
        if description and MEETING_WORDS.search(event.summary):
            notes.blocks.append(Block(text=f"{event.date} {event.summary.strip()}", kind="heading", level=2))
            for part in description.splitlines():
                part = part.strip()
                if not part:
                    continue
                match = _BULLET_RE.match(part)
                notes.blocks.append(Block(text=match.group(1) if match else part, kind="bullet"))
    docs: dict[str, ParsedDocument] = {}
    if rows:
        table = Table(
            headers=["summary", "date", "time", "description", "location"],
            rows=rows,
            first_row_location=1,
        )
        doc = ParsedDocument(path="", fmt="connector", sections=[Section(name="schedule", tables=[table])])
        doc.meta["kind"] = "SCHEDULE"
        docs["schedule"] = doc
    if notes.blocks:
        doc = ParsedDocument(path="", fmt="connector", sections=[notes])
        doc.meta["kind"] = "MEETING"
        docs["meeting notes"] = doc
    return docs
