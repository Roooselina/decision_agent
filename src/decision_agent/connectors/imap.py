from __future__ import annotations

import datetime
import email
import email.policy
import email.utils
import imaplib

from decision_agent.connectors.base import (
    NO_SCOPE,
    NO_SUBJECT,
    Connector,
    ConnectorError,
    FetchOutcome,
    MailMessage,
    OptionSpec,
    SecretSpec,
    mail_summary,
    new_mail_stats,
    preview_summary,
    strip_html,
)
from decision_agent.connectors.mailfilter import MailFilter, MailQuery, collect_mail

# Replaced in tests. Always a TLS connection.
IMAP_FACTORY = imaplib.IMAP4_SSL


def message_text(message: email.message.EmailMessage) -> str:
    body = message.get_body(preferencelist=("plain", "html"))
    if body is None:
        return ""
    try:
        content = body.get_content()
    except (LookupError, ValueError):
        return ""
    return strip_html(content) if body.get_content_type() == "text/html" else content


class ImapMail(Connector):
    id = "imap_mail"
    label = "메일 (IMAP)"
    description = "IMAP을 지원하는 모든 메일(네이버, 다음, 회사 메일 등)의 지정한 폴더를 읽습니다."
    reads = (
        "지정한 폴더를 읽기 전용으로 열어 최근 메일의 제목, 날짜, 본문 텍스트만 가져옵니다. "
        "읽음 표시를 바꾸지 않고, 메일을 보내거나 지우지 않습니다. 앱 비밀번호 사용을 권장합니다."
    )
    guide = (
        "메일 서비스 설정에서 IMAP 사용을 켭니다.",
        "가능하면 일반 비밀번호 대신 '앱 비밀번호'를 만들어 사용합니다 (2단계 인증 사용 시 필요).",
        "서버 주소, 계정, 폴더 이름을 입력하고 앱 비밀번호를 저장합니다.",
    )
    options = (
        OptionSpec("host", "IMAP 서버", help="예: imap.naver.com, imap.gmail.com", required=True),
        OptionSpec("port", "포트", "number", 993),
        OptionSpec("username", "계정", required=True),
        OptionSpec("folder", "읽을 폴더 이름", help="이전 버전 설정입니다. 읽을 범위는 메일 소스에서 정합니다."),
        OptionSpec("days_back", "과거 일수", "number", 14),
        OptionSpec("max_messages", "최대 메일 수", "number", 200),
    )
    secrets = (SecretSpec("password", "비밀번호 또는 앱 비밀번호"),)

    def options_query(self) -> MailQuery:
        """The single-folder setup of older versions, as a mail query."""
        folder = self.text_option("folder")
        if not folder:
            raise ConnectorError(NO_SCOPE)
        return MailQuery(
            source_id="", name=folder, labels=[folder] if folder else [], folder_word="폴더",
            days_back=self.int_option("days_back", 14, 1, 365),
            max_messages=self.int_option("max_messages", 200, 1, 1000), prefilter=False,
        )

    @staticmethod
    def _criteria(query: MailQuery, since: str) -> list[str]:
        """SINCE plus FROM and SUBJECT when one plain ASCII term can be sent to the server.

        Several terms, or non-ASCII ones, are checked locally by MailFilter instead.
        """
        criteria = ["SINCE", since]
        for key, terms in (("FROM", query.senders), ("SUBJECT", query.subject_keywords)):
            if len(terms) == 1 and terms[0].isascii() and terms[0].strip() and '"' not in terms[0]:
                criteria += [key, f'"{terms[0].strip().lstrip("@")}"']
        return criteria

    def _session(self, today: datetime.date, query: MailQuery, work) -> None:
        """Open each folder read-only, search it, and run work(conn, folder, numbers)."""
        host = self.text_option("host")
        port = self.int_option("port", 993, 1, 65535)
        since = (today - datetime.timedelta(days=query.days_back)).strftime("%d-%b-%Y")
        folders = query.labels or ["INBOX"]
        try:
            conn = IMAP_FACTORY(host, port)
            try:
                conn.login(self.text_option("username"), self.secret("password"))
                for folder in folders:
                    status, _ = conn.select(f'"{folder}"', readonly=True)
                    if status != "OK":
                        raise ConnectorError(f"폴더 '{folder}'을(를) 열지 못했습니다.")
                    status, data = conn.search(None, *self._criteria(query, since))
                    if status != "OK":
                        raise ConnectorError("메일 검색에 실패했습니다.")
                    numbers = data[0].split()[-query.max_messages:] if data and data[0] else []
                    work(conn, folder, numbers)
            finally:
                try:
                    conn.logout()
                except (imaplib.IMAP4.error, OSError):
                    pass
        except imaplib.IMAP4.error as exc:
            raise ConnectorError("메일 서버가 로그인 또는 요청을 거절했습니다. 계정과 비밀번호를 확인하세요.") from exc
        except (OSError, TimeoutError) as exc:
            raise ConnectorError(f"메일 서버에 연결하지 못했습니다: {exc}") from exc

    @staticmethod
    def _parse(raw: bytes, today: datetime.date, with_body: bool) -> MailMessage:
        message = email.message_from_bytes(raw, policy=email.policy.default)
        try:
            stamp = email.utils.parsedate_to_datetime(message["Date"]) if message["Date"] else None
        except (TypeError, ValueError):
            stamp = None
        date = (stamp.date() if stamp else today).isoformat()
        subject = str(message["Subject"] or NO_SUBJECT)
        name, _ = email.utils.parseaddr(str(message["From"] or ""))
        return MailMessage(
            msg_id=str(message["Message-ID"] or f"{subject}{date}"),
            date=date,
            subject=subject,
            sender=str(message["From"] or ""),
            sender_name=name,
            body=message_text(message) if with_body else "",
        )

    @staticmethod
    def _raw(conn, number: bytes, spec: str) -> bytes | None:
        status, parts = conn.fetch(number, spec)
        if status != "OK" or not parts or not isinstance(parts[0], tuple):
            return None
        return parts[0][1]

    def fetch_source(
        self, query: MailQuery, today: datetime.date, known: set[str] | None = None
    ) -> FetchOutcome:
        """Fetch one source. Headers come first; bodies only for mails not stored yet.

        One IMAP connection cannot be shared between threads, so bodies are read in order.
        """
        known = known or set()
        stats = new_mail_stats()
        messages: list[MailMessage] = []
        seen: list[str] = []

        def work(conn, folder, numbers):
            for number in numbers:
                if len(seen) >= query.max_messages:
                    return
                header = self._raw(conn, number, "(BODY.PEEK[HEADER])")
                if header is None:
                    continue
                head = self._parse(header, today, False)
                if head.msg_id in seen:
                    continue
                seen.append(head.msg_id)
                if head.msg_id in known:
                    stats["known"] += 1
                    continue
                raw = self._raw(conn, number, "(BODY.PEEK[])")
                if raw is None:
                    stats["skipped_empty"] += 1
                    continue
                messages.append(self._parse(raw, today, True))

        self._session(today, query, work)
        stats["matched"] = len(seen)
        docs = collect_mail(messages, query, stats)
        return FetchOutcome(docs, mail_summary(stats, query.scope_text()), stats, seen_ids=seen)

    def fetch(self, today: datetime.date) -> FetchOutcome:
        return self.fetch_source(self.options_query(), today)

    def preview_source(self, query: MailQuery, today: datetime.date, limit: int = 10) -> dict:
        stats = new_mail_stats()
        rows: list[dict] = []

        def work(conn, folder, numbers):
            stats["matched"] += len(numbers)
            for number in list(reversed(numbers)):
                if len(rows) >= limit:
                    return
                header = self._raw(conn, number, "(BODY.PEEK[HEADER])")
                if header is None:
                    continue
                mail = self._parse(header, today, False)
                if MailFilter.of(query).matches(mail):
                    rows.append({"date": mail.date, "subject": mail.subject, "sender": mail.sender})

        self._session(today, query, work)
        return {"rows": rows, "stats": stats, "summary": preview_summary(stats, query.scope_text())}

    def preview(self, today: datetime.date, limit: int = 10) -> dict:
        return self.preview_source(self.options_query(), today, limit)

    def account_info(self) -> dict:
        return {"account": self.text_option("username")}
