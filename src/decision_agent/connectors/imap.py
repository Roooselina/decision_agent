from __future__ import annotations

import datetime
import email
import email.policy
import email.utils
import imaplib

from decision_agent.connectors.base import (
    NO_SUBJECT,
    Connector,
    ConnectorError,
    FetchOutcome,
    MailMessage,
    OptionSpec,
    SecretSpec,
    mail_document,
    mail_summary,
    new_mail_stats,
    preview_summary,
    strip_html,
)

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
        OptionSpec("folder", "읽을 폴더 이름", help="예: 회의록 또는 INBOX", required=True),
        OptionSpec("days_back", "과거 일수", "number", 14),
        OptionSpec("max_messages", "최대 메일 수", "number", 200),
    )
    secrets = (SecretSpec("password", "비밀번호 또는 앱 비밀번호"),)

    def _session(self, today: datetime.date, work) -> object:
        """Open the folder read-only, list the message numbers since days_back, run work()."""
        host = self.text_option("host")
        port = self.int_option("port", 993, 1, 65535)
        days = self.int_option("days_back", 14, 1, 365)
        limit = self.int_option("max_messages", 200, 1, 1000)
        since = (today - datetime.timedelta(days=days)).strftime("%d-%b-%Y")
        try:
            conn = IMAP_FACTORY(host, port)
            try:
                conn.login(self.text_option("username"), self.secret("password"))
                status, _ = conn.select(f'"{self.text_option("folder")}"', readonly=True)
                if status != "OK":
                    raise ConnectorError(f"폴더 '{self.text_option('folder')}'을(를) 열지 못했습니다.")
                status, data = conn.search(None, "SINCE", since)
                if status != "OK":
                    raise ConnectorError("메일 검색에 실패했습니다.")
                ids = data[0].split()[-limit:] if data and data[0] else []
                return work(conn, ids)
            finally:
                try:
                    conn.logout()
                except (imaplib.IMAP4.error, OSError):
                    pass
        except imaplib.IMAP4.error as exc:
            raise ConnectorError("메일 서버가 로그인 또는 요청을 거절했습니다. 계정과 비밀번호를 확인하세요.") from exc
        except (OSError, TimeoutError) as exc:
            raise ConnectorError(f"메일 서버에 연결하지 못했습니다: {exc}") from exc

    def _scope(self) -> str:
        return f"폴더: {self.text_option('folder')}, 최근 {self.int_option('days_back', 14, 1, 365)}일"

    @staticmethod
    def _parse(raw: bytes, today: datetime.date, with_body: bool) -> MailMessage:
        message = email.message_from_bytes(raw, policy=email.policy.default)
        try:
            stamp = email.utils.parsedate_to_datetime(message["Date"]) if message["Date"] else None
        except (TypeError, ValueError):
            stamp = None
        date = (stamp.date() if stamp else today).isoformat()
        subject = str(message["Subject"] or NO_SUBJECT)
        return MailMessage(
            msg_id=str(message["Message-ID"] or f"{subject}{date}"),
            date=date,
            subject=subject,
            sender=str(message["From"] or ""),
            body=message_text(message) if with_body else "",
        )

    def fetch(self, today: datetime.date) -> FetchOutcome:
        stats = new_mail_stats()
        docs = {}

        def work(conn, ids):
            stats["matched"] = len(ids)
            for message_id in ids:
                status, parts = conn.fetch(message_id, "(BODY.PEEK[])")
                if status != "OK" or not parts or not isinstance(parts[0], tuple):
                    stats["skipped_empty"] += 1
                    continue
                made = mail_document(self._parse(parts[0][1], today, True), stats)
                if made is not None:
                    docs[made[0]] = made[1]

        self._session(today, work)
        return FetchOutcome(docs, mail_summary(stats, self._scope()), stats)

    def preview(self, today: datetime.date, limit: int = 10) -> dict:
        stats = new_mail_stats()
        rows: list[dict] = []

        def work(conn, ids):
            stats["matched"] = len(ids)
            for message_id in list(reversed(ids))[:limit]:
                status, parts = conn.fetch(message_id, "(BODY.PEEK[HEADER])")
                if status != "OK" or not parts or not isinstance(parts[0], tuple):
                    continue
                mail = self._parse(parts[0][1], today, False)
                rows.append({"date": mail.date, "subject": mail.subject, "sender": mail.sender})

        self._session(today, work)
        return {"rows": rows, "stats": stats, "summary": preview_summary(stats, self._scope())}

    def account_info(self) -> dict:
        return {"account": self.text_option("username")}
