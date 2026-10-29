"""Sending one Gmail message, only after the user pressed 보내기 in the compose window.

The outbox is the single path to Gmail's send call. submit() is called by the compose action
of the web API and nowhere else; it checks the permission and the limits, builds the message
and waits the undo period, during which cancel() stops it. Nothing here is started by the
poller, a notification, a model answer, a timer or mail content, and nothing is retried after
the undo period. The activity log gets time, account, number of recipients, subject length and
the result; never the body or the subject.
"""

from __future__ import annotations

import base64
import datetime
import email.policy
import email.utils
import json
import threading
import uuid
from dataclasses import dataclass, field
from email.message import EmailMessage

from decision_agent.connectors.base import (
    ConnectorError,
    LoginExpired,
    RateLimited,
    normalize_subject,
)
from decision_agent.connectors.google import GMAIL_SEND_SCOPE
from decision_agent.connectors.mailfilter import address_of
from decision_agent.desktop.errors import UserError

SENT_LOG_KEY = "send_log"
FALLBACK_SOURCE = "gmail-sent"


@dataclass
class Draft:
    to: list[str]
    subject: str
    body: str
    cc: list[str] = field(default_factory=list)
    # The local thread id (mail view) this mail answers; empty for a new mail.
    reply_to: str = ""


@dataclass
class OutboxItem:
    id: str
    draft: Draft
    raw: str
    thread_id: str
    recipients: list[str]
    deliver_at: float
    state: str = "waiting"  # waiting, cancelled, sending, sent, failed
    message: str = ""
    timer: threading.Timer | None = None


def clean_addresses(values: list[str]) -> list[str]:
    """Plain addresses from what the user typed ("민재 <a@b.c>", "a@b.c, d@e.f")."""
    out: list[str] = []
    for value in values:
        for piece in str(value).replace(";", ",").split(","):
            piece = piece.strip()
            if not piece:
                continue
            address = email.utils.parseaddr(piece)[1].strip()
            if address.count("@") != 1 or "." not in address.split("@")[1] or " " in address:
                raise UserError(f"주소 형식이 올바르지 않습니다: {piece[:60]}")
            if address.casefold() not in {a.casefold() for a in out}:
                out.append(address)
    return out


def build_message(account: str, draft: Draft, reply: dict | None = None) -> EmailMessage:
    """An RFC 2822 message (UTF-8 body; RFC 2047 for a Korean subject) from the draft."""
    message = EmailMessage(policy=email.policy.SMTP)
    message["From"] = account
    message["To"] = ", ".join(draft.to)
    if draft.cc:
        message["Cc"] = ", ".join(draft.cc)
    message["Subject"] = draft.subject
    message["Date"] = email.utils.formatdate(localtime=True)
    message["Message-ID"] = email.utils.make_msgid(domain=account.split("@")[-1] or "localhost")
    if reply and reply.get("header_id"):
        message["In-Reply-To"] = reply["header_id"]
        message["References"] = " ".join([*reply.get("references", []), reply["header_id"]])
    message.set_content(draft.body, charset="utf-8")
    return message


def reply_subject(subject: str) -> str:
    return f"Re: {normalize_subject(subject)}"


class Outbox:
    """Pending and recent sends of this app run (in memory; nothing is sent after a restart)."""

    def __init__(self, app, undo_seconds: float | None = None) -> None:
        self.app = app
        self.undo_seconds = undo_seconds
        self.items: dict[str, OutboxItem] = {}
        self.lock = threading.Lock()

    # permission and limits

    def status(self) -> dict:
        settings = self.app.settings.mail_send
        granted = self.app.connectors.send_granted()
        log = self._log()
        now = datetime.datetime.now()
        return {
            "enabled": settings.enabled, "granted": granted, "available": settings.enabled and granted,
            "account": self.app.connectors.accounts.get("gmail", {}).get("account", ""),
            "max_recipients": settings.max_recipients, "per_hour": settings.per_hour, "per_day": settings.per_day,
            "max_body": settings.max_body, "undo_seconds": self._undo(),
            "used_hour": sum(1 for t in log if now - t < datetime.timedelta(hours=1)),
            "used_day": sum(1 for t in log if now - t < datetime.timedelta(days=1)),
        }

    def _undo(self) -> float:
        return self.undo_seconds if self.undo_seconds is not None else float(self.app.settings.mail_send.undo_seconds)

    def _log(self) -> list[datetime.datetime]:
        try:
            stamps = json.loads(self.app.maildb.meta_get(SENT_LOG_KEY) or "[]")
        except ValueError:
            stamps = []
        return [datetime.datetime.fromisoformat(s) for s in stamps]

    def _record_send(self) -> None:
        now = datetime.datetime.now()
        keep = [t for t in self._log() if now - t < datetime.timedelta(days=1)] + [now]
        self.app.maildb.meta_put(SENT_LOG_KEY, json.dumps([t.replace(microsecond=0).isoformat() for t in keep]))

    def check(self, draft: Draft) -> list[str]:
        """The limits a draft must pass; raises UserError with the reason, sends nothing."""
        settings = self.app.settings.mail_send
        state = self.status()
        if not settings.enabled:
            raise UserError("메일 보내기가 꺼져 있습니다. 설정에서 '메일 보내기'를 켜세요.")
        if not state["granted"]:
            raise UserError("보내기 권한이 없습니다. 설정에서 '메일 보내기'를 다시 켜고 동의 화면에서 보내기를 허용하세요.")
        recipients = clean_addresses(draft.to) + clean_addresses(draft.cc)
        if not draft.to or not recipients:
            raise UserError("받는 사람을 입력하세요.")
        if len(recipients) > settings.max_recipients:
            raise UserError(f"받는 사람은 {settings.max_recipients}명까지입니다. 지금 {len(recipients)}명입니다.")
        if not draft.subject.strip():
            raise UserError("제목을 입력하세요.")
        if "\n" in draft.subject or "\r" in draft.subject:
            raise UserError("제목은 한 줄로 입력하세요.")
        if len(draft.body) > settings.max_body:
            raise UserError(f"본문은 {settings.max_body:,}자까지입니다.")
        with self.lock:
            waiting = sum(1 for i in self.items.values() if i.state in {"waiting", "sending"})
        if state["used_hour"] + waiting >= settings.per_hour:
            raise UserError(f"한 시간에 {settings.per_hour}통까지 보낼 수 있습니다. 잠시 뒤 다시 보내세요.")
        if state["used_day"] + waiting >= settings.per_day:
            raise UserError(f"하루에 {settings.per_day}통까지 보낼 수 있습니다. 내일 다시 보내세요.")
        return recipients

    # the compose action

    def submit(self, draft: Draft) -> dict:
        """Queue one mail the user just sent from the compose window. It goes out after the
        undo period unless cancel() is called."""
        recipients = self.check(draft)
        draft.to, draft.cc = clean_addresses(draft.to), clean_addresses(draft.cc)
        account = self._account()
        reply, thread_id = self._reply_info(draft.reply_to)
        message = build_message(account, draft, reply)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        item = OutboxItem(
            id=uuid.uuid4().hex[:12], draft=draft, raw=raw, thread_id=thread_id, recipients=recipients,
            deliver_at=datetime.datetime.now().timestamp() + self._undo(),
        )
        timer = threading.Timer(self._undo(), self._deliver, args=(item.id,))
        timer.daemon = True
        item.timer = timer
        with self.lock:
            self.items[item.id] = item
        timer.start()
        return self.view(item.id)

    def cancel(self, item_id: str) -> dict:
        with self.lock:
            item = self.items.get(item_id)
            if item is None:
                raise UserError("보낼 메일을 찾지 못했습니다.")
            if item.state != "waiting":
                raise UserError("이미 보냈거나 취소한 메일입니다.")
            item.state = "cancelled"
            if item.timer is not None:
                item.timer.cancel()
        return self.view(item_id)

    def view(self, item_id: str) -> dict:
        with self.lock:
            item = self.items.get(item_id)
            if item is None:
                raise UserError("보낼 메일을 찾지 못했습니다.")
            return {"id": item.id, "state": item.state, "message": item.message,
                    "recipients": list(item.recipients), "deliver_at": item.deliver_at}

    def stop(self) -> None:
        """App exit: pending mails are cancelled, never sent later."""
        with self.lock:
            for item in self.items.values():
                if item.state == "waiting":
                    item.state = "cancelled"
                    if item.timer is not None:
                        item.timer.cancel()

    # internals

    def _account(self) -> str:
        connectors = self.app.connectors
        if "gmail" not in connectors.accounts:
            connectors.remember_account("gmail")
        account = connectors.accounts.get("gmail", {}).get("account", "")
        if not account:
            raise UserError("연결된 Gmail 계정을 확인하지 못했습니다. 설정에서 Gmail을 다시 연결하세요.")
        return account

    def _reply_info(self, tid: str) -> tuple[dict | None, str]:
        if not tid:
            return None, ""
        thread = self.app.maildb.thread(tid)
        if thread is None:
            raise UserError("답장할 대화를 찾지 못했습니다.")
        rows = self.app.maildb.thread_rows_by_key(thread["thread_key"])
        last = rows[-1]
        return ({"header_id": last["header_id"], "references": json.loads(last["refs"] or "[]")},
                last["thread_id"])

    def _deliver(self, item_id: str) -> None:
        """The one place that sends. Runs once, when the undo period ends; never retried."""
        with self.lock:
            item = self.items.get(item_id)
            if item is None or item.state != "waiting":
                return
            item.state = "sending"
        connector = self.app.connectors.instance("gmail")
        try:
            sent = connector.send_raw(item.raw, item.thread_id)
        except LoginExpired:
            self._finish(item, "failed", "메일을 보내지 못했습니다. 로그인이 만료되었습니다. 설정에서 Gmail을 다시 연결하세요.")
            return
        except RateLimited:
            self._finish(item, "failed", "메일을 보내지 못했습니다. 요청이 많습니다. 잠시 뒤 다시 보내세요.")
            return
        except ConnectorError as exc:
            self._finish(item, "failed", failure_text(str(exc)))
            return
        except Exception:  # noqa: BLE001 - any failure is shown, the draft stays in the window
            self._finish(item, "failed", "메일을 보내지 못했습니다. 잠시 뒤 다시 보내세요.")
            return
        self._record_send()
        self._finish(item, "sent", "메일을 보냈습니다.")
        self._read_back(connector, str(sent.get("threadId") or item.thread_id), str(sent.get("id") or ""))

    def _finish(self, item: OutboxItem, state: str, message: str) -> None:
        with self.lock:
            item.state = state
            item.message = message
        account = self.app.connectors.accounts.get("gmail", {}).get("account", "")
        result = "보냄" if state == "sent" else "실패"
        self.app.activity.add(
            f"메일 보내기 {result}: 계정 {account}, 받는 사람 {len(item.recipients)}명, 제목 {len(item.draft.subject)}자",
            "info" if state == "sent" else "error",
        )
        self.app._changed()

    def _read_back(self, connector, thread_id: str, msg_id: str) -> None:
        """Store the sent mail through the normal thread read, as mail read through its thread,
        so the next poll knows it and nothing is stored twice."""
        if not thread_id:
            return
        maildb = self.app.maildb
        with maildb.lock:
            rows = maildb.conn.execute(
                "SELECT DISTINCT source_id FROM mail_index WHERE thread_id=?", (thread_id,)
            ).fetchall()
        sources = [r["source_id"] for r in rows] or [FALLBACK_SOURCE]
        account = self.app.connectors.accounts.get("gmail", {}).get("account", "")
        try:
            for source_id in sources:
                messages = connector.thread_messages([thread_id], maildb.ids(source_id), self.app.connectors.today())
                maildb.upsert(source_id, messages, [account] if account else [])
        except ConnectorError:
            # The next poll picks the sent mail up through its thread.
            return


def failure_text(message: str) -> str:
    """A connector error as "what happened, what to do"."""
    if "권한" in message or "403" in message:
        return "메일을 보내지 못했습니다. 보내기 권한이 없습니다. 설정에서 '메일 보내기'를 다시 켜세요."
    if "서비스 오류" in message:
        return "메일을 보내지 못했습니다. Gmail에 일시적인 오류가 있습니다. 잠시 뒤 다시 보내세요."
    if "연결" in message or "timed out" in message.casefold() or "urlopen" in message.casefold():
        return "메일을 보내지 못했습니다. 인터넷 연결을 확인하고 다시 보내세요."
    return f"메일을 보내지 못했습니다. {message}"


def recipient_suggestions(maildb, text: str, limit: int = 8) -> list[dict]:
    """Address book entries matching typed text (name, address or team). Never from mail content."""
    needle = text.strip().casefold()
    if not needle:
        return []
    found = []
    for contact in maildb.contacts():
        if contact["pattern"].startswith("@"):
            continue
        hay = " ".join((contact["name"], contact["pattern"], contact["team"])).casefold()
        if needle in hay:
            found.append({"name": contact["name"], "address": contact["pattern"], "team": contact["team"]})
        if len(found) >= limit:
            break
    return found


def owner_address(maildb, owner: str) -> str:
    """The address book address of a finding owner (exact name match), or ""."""
    wanted = owner.strip().casefold()
    if not wanted:
        return ""
    for contact in maildb.contacts():
        if contact["name"].strip().casefold() == wanted and not contact["pattern"].startswith("@"):
            return contact["pattern"]
    return ""


def finding_draft(finding: dict, maildb) -> dict:
    """A fixed-template draft for the owner of a finding. No model is called."""
    topic = finding.get("topic_label") or finding.get("topic") or ""
    lines = [
        "안녕하세요.",
        "",
        "결정이 바뀌었는데 아래 문서에 아직 이전 내용이 남아 있습니다. 확인 후 반영 부탁드립니다.",
        "",
        "결정",
        f"- 날짜: {finding.get('decision_date') or '-'}",
        f"- 내용: {finding.get('decision_text') or '-'}",
        f"- 출처: {finding.get('decision_source') or '-'}",
        "",
        "반영이 필요한 문서",
        f"- 문서: {finding.get('source_doc') or '-'}",
        f"- 위치: {finding.get('location') or '-'}",
        f"- 현재 내용: {finding.get('text') or '-'}",
        "",
        "필요한 조치",
        f"- {finding.get('action') or '문서를 최신 결정에 맞게 고쳐 주세요.'}",
    ]
    owner = finding.get("owner") or ""
    address = owner_address(maildb, owner)
    return {
        "to": [address] if address else [], "cc": [], "subject": f"[결정 변경] {topic}: 문서 반영 요청",
        "body": "\n".join(lines), "owner": owner, "owner_known": bool(address),
    }


__all__ = ["GMAIL_SEND_SCOPE", "Draft", "Outbox", "address_of", "build_message", "clean_addresses", "finding_draft",
           "recipient_suggestions", "reply_subject"]
