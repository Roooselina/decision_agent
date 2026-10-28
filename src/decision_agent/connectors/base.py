from __future__ import annotations

import datetime
import hashlib
import html
import json
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
    # Counts only (never subjects, bodies or addresses). Mail connectors fill MAIL_STATS keys.
    stats: dict[str, int] = field(default_factory=dict)
    # Ids of every mail the server listed for a mail source, including ones already stored
    # whose bodies were not requested again. None for other connectors.
    seen_ids: list[str] | None = None
    # The mails whose bodies were read in this fetch (for the local mail index).
    messages: list[MailMessage] = field(default_factory=list)


# matched: listed by the server; read: body text used; subject_only: no readable body, the
# subject line was kept instead; skipped_empty: neither body nor subject; skipped_filtered:
# left out by a sender or subject filter; skipped_prefilter: no decision wording; known:
# already stored, so the body was not requested again.
MAIL_STATS = (
    "matched", "read", "subject_only", "skipped_empty", "skipped_filtered", "skipped_prefilter",
    "known", "deferred", "stored_only",
)
NO_SUBJECT = "(제목 없음)"
NO_SCOPE = "읽을 범위가 없습니다. 설정에서 메일 소스를 추가하세요."


def new_mail_stats() -> dict[str, int]:
    return dict.fromkeys(MAIL_STATS, 0)


def mail_summary(stats: dict[str, int], scope: str) -> str:
    """One status sentence that tells apart 'nothing matched' and 'matched but unreadable'."""
    matched = stats.get("matched", 0)
    if not matched:
        return f"조건에 맞는 메일이 없습니다 ({scope})"
    parts = [f"{matched}건 찾음", f"{stats.get('read', 0)}건 읽음"]
    if stats.get("known"):
        parts.append(f"이미 가져온 메일 {stats['known']}건")
    if stats.get("subject_only"):
        parts.append(f"제목만 사용 {stats['subject_only']}건")
    if stats.get("skipped_empty"):
        parts.append(f"{stats['skipped_empty']}건은 본문이 비어 있거나 인용만 있어 건너뜀")
    if stats.get("skipped_filtered"):
        parts.append(f"조건에 맞지 않아 제외 {stats['skipped_filtered']}건")
    if stats.get("skipped_prefilter"):
        parts.append(f"결정 표현 없음 {stats['skipped_prefilter']}건 건너뜀")
    if stats.get("stored_only"):
        parts.append(f"분석하지 않고 저장만 {stats['stored_only']}건")
    if stats.get("deferred"):
        parts.append(f"다음에 가져올 메일 {stats['deferred']}건")
    return ", ".join(parts)


def preview_summary(stats: dict[str, int], scope: str) -> str:
    """A preview reads headers only, so it can say how many mails match but not how many read."""
    if not stats.get("matched"):
        return f"조건에 맞는 메일이 없습니다 ({scope})"
    return f"조건에 맞는 메일 {stats['matched']}건 ({scope})"


@dataclass
class MailMessage:
    """One fetched mail, provider independent. Never logged."""

    msg_id: str
    date: str
    subject: str
    sender: str
    body: str = ""
    thread_id: str = ""
    labels: list[str] = field(default_factory=list)
    sender_name: str = ""


def mail_document(
    message: MailMessage, stats: dict[str, int], kind: str = "MEETING"
) -> tuple[str, ParsedDocument] | None:
    """(document name, document) for one mail, or None when nothing readable is left.

    A mail whose body is empty, an image, an attachment or only quoted text keeps one line made
    of its subject, because announcements often carry the date and place only in the subject.
    """
    lines = clean_message_text(message.body)
    subject = message.subject.strip()
    if lines:
        stats["read"] = stats.get("read", 0) + 1
    elif subject and subject != NO_SUBJECT:
        lines = [subject]
        stats["subject_only"] = stats.get("subject_only", 0) + 1
    else:
        stats["skipped_empty"] = stats.get("skipped_empty", 0) + 1
        return None
    doc = message_document(subject or NO_SUBJECT, message.date, message.sender, lines, kind)
    doc.meta["msg_id"] = message.msg_id
    return unique_name(f"{message.date} {subject}", message.msg_id), doc


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

    def preview(self, today: datetime.date, limit: int = 10) -> dict:
        """Up to `limit` matching mails as headers only. Never stores anything or calls a model."""
        raise ConnectorError("이 연결은 미리보기를 지원하지 않습니다.")

    def account_info(self) -> dict:
        """What the user needs to see which account is read (UI only, never logged)."""
        return {}


# Short Korean hints for the error reasons Google puts in its JSON error body.
GOOGLE_HINTS = {
    "accessnotconfigured": "Google Cloud에서 이 API를 사용 설정했는지 확인하세요",
    "service_disabled": "Google Cloud에서 이 API를 사용 설정했는지 확인하세요",
    "insufficientpermissions": "권한이 부족합니다. 계정을 다시 연결하세요",
    "access_token_scope_insufficient": "권한이 부족합니다. 계정을 다시 연결하세요",
    "invalid_grant": "로그인이 만료되었습니다. 다시 연결하세요; 테스트 모드의 Google 앱은 7일 뒤 만료됩니다",
    "autherror": "로그인이 만료되었습니다. 다시 연결하세요; 테스트 모드의 Google 앱은 7일 뒤 만료됩니다",
}


def error_reason(response: HttpResponse) -> tuple[str, str]:
    """(reason, message) from a Google-style JSON error body, or empty strings."""
    try:
        body = json.loads(response.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "", ""
    if not isinstance(body, dict):
        return "", ""
    error = body.get("error")
    if isinstance(error, str):
        return error, str(body.get("error_description") or "")[:160]
    if not isinstance(error, dict):
        return "", ""
    reason = ""
    errors = error.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        reason = str(errors[0].get("reason") or "")
    for detail in error.get("details") or []:
        if not reason and isinstance(detail, dict) and detail.get("reason"):
            reason = str(detail["reason"])
    return reason or str(error.get("status") or ""), str(error.get("message") or "")[:160]


def check_response(response: HttpResponse, service: str) -> HttpResponse:
    """Raise a readable ConnectorError for a failed response.

    Only the service's own reason and message are shown, never request headers or tokens.
    """
    if response.ok:
        return response
    if response.status in {401, 403}:
        reason, message = error_reason(response)
        hint = GOOGLE_HINTS.get(reason.lower(), "")
        text = f"{service}: 인증에 실패했거나 읽기 권한이 없습니다 ({response.status})."
        if hint:
            text += f" {hint}."
        if message:
            text += f" ({message})"
        raise ConnectorError(text)
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
