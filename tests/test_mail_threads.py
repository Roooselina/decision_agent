"""Threads, sent mail, local labels and the background mail refresh (no model calls)."""

from __future__ import annotations

import base64
import datetime
import json
import time
from pathlib import Path

import desktop_helpers as dh
import pytest
from fastapi.testclient import TestClient
from test_connectors import FakeHttp, gmail_labels
from test_desktop_features import make_app
from test_mail_sources import logged_in
from test_mail_subject import ALIASES, CONFIG

from decision_agent.connectors import google as google_module
from decision_agent.connectors.base import MailMessage, split_message
from decision_agent.desktop.maildb import MailDB, thread_key_of
from decision_agent.desktop.mailpoller import wants_toast
from decision_agent.desktop.settings import ConnectorSettings, MailSource
from decision_agent.desktop.webapp import create_web_app
from decision_agent.sources.builder import build_inputs
from decision_agent.sources.model import ParsedDocument

API = "https://gmail.googleapis.com/gmail/v1/users/me"
ME = "me.demo@gmail.com"
OTHER = "other.demo@gmail.com"
TOKEN = "t" * 32
THREADS = json.loads((Path(__file__).resolve().parents[1] / "demo" / "mail_threads.json").read_text(encoding="utf-8"))


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


class ThreadGmail:
    """A Gmail account with threads and a history log, enough for the poller."""

    def __init__(self, http: FakeHttp) -> None:
        self.http = http
        self.messages: dict[str, dict] = {}
        self.history: list[tuple[int, dict]] = []
        self.history_id = 100
        gmail_labels(http, "팀A")
        http.add("GET", f"{API}/profile", lambda p, b, f: {"emailAddress": ME, "historyId": str(self.history_id)})
        http.add("GET", f"{API}/messages", self._list)
        self.restore_history()

    def _list(self, params, body, form):
        wanted = params.get("labelIds")
        ids = [i for i, m in self.messages.items() if wanted is None or wanted in m["labelIds"]]
        return {"messages": [{"id": i, "threadId": self.messages[i]["threadId"]} for i in sorted(ids, reverse=True)]}

    def _history(self, params, body, form):
        start = int(params["startHistoryId"])
        entries = [{"id": str(h), "messagesAdded": [{"message": {
            "id": m["id"], "threadId": m["threadId"], "labelIds": m["labelIds"]}}]} for h, m in self.history if h > start]
        return {"history": entries, "historyId": str(self.history_id)}

    def restore_history(self) -> None:
        self.http.add("GET", f"{API}/history", self._history)

    def expire_history(self) -> None:
        self.http.add("GET", f"{API}/history", lambda p, b, f: {"error": {"code": 404}}, status=404)

    def add(self, msg_id: str, thread_id: str, *, sent: bool, subject: str, body: str, sender_name: str,
            reply_to: str = "", refs: tuple[str, ...] = (), minutes: int = 0) -> dict:
        stamp = datetime.datetime(2026, 10, 27, 9, 0) + datetime.timedelta(minutes=minutes)
        address = ME if sent else OTHER
        headers = [
            {"name": "Subject", "value": subject},
            {"name": "From", "value": f"{sender_name} <{address}>"},
            {"name": "To", "value": OTHER if sent else ME},
            {"name": "Message-ID", "value": f"<{msg_id}@example.com>"},
        ]
        if reply_to:
            headers += [{"name": "In-Reply-To", "value": reply_to}, {"name": "References", "value": " ".join(refs)}]
        message = {
            "id": msg_id, "threadId": thread_id, "labelIds": ["SENT"] if sent else ["INBOX", "L1"],
            "internalDate": str(int(stamp.timestamp() * 1000)),
            "payload": {"mimeType": "text/plain", "body": {"data": b64(body)}, "headers": headers},
        }
        self.messages[msg_id] = message
        self.http.add("GET", f"{API}/messages/{msg_id}", lambda p, b, f, m=message: m)
        self.http.add("GET", f"{API}/threads/{thread_id}", lambda p, b, f, t=thread_id: {
            "id": t, "messages": [m for m in self.messages.values() if m["threadId"] == t]})
        self.history_id += 1
        self.history.append((self.history_id, message))
        return message

    def add_thread(self, thread: dict, upto: int | None = None, start: int = 0) -> None:
        """Messages of a demo thread; mine are SENT only (no source label), the others carry 팀A."""
        ids: list[str] = []
        for number, data in enumerate(thread["messages"][: upto], start=1):
            msg_id = f"{thread['id'].lower()}m{number}"
            if number <= start:
                ids.append(f"<{msg_id}@example.com>")
                continue
            sent = data["from"] == "me"
            subject = thread["subject"] if number == 1 or thread["subject"].startswith("Re:") else f"Re: {thread['subject']}"
            name = THREADS["people"][data["from"]]["name"]
            self.add(msg_id, thread["id"], sent=sent, subject=subject, body=data["body"], sender_name=name,
                     reply_to=ids[-1] if ids else "", refs=tuple(ids), minutes=number * 10)
            ids.append(f"<{msg_id}@example.com>")

    def calls(self, path: str) -> int:
        return sum(1 for c in self.http.calls if c["url"] == f"{API}{path}")


def thread_of(tid: str) -> dict:
    return next(t for t in THREADS["threads"] if t["id"] == tid)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(google_module, "SLEEP", lambda s: None)


@pytest.fixture
def mailbox(tmp_path):
    http = FakeHttp()
    gmail = ThreadGmail(http)
    root = dh.make_watch_dir(tmp_path)
    controller = make_app(root, http=http, secrets=logged_in())
    controller.settings.connectors["gmail"] = ConnectorSettings(enabled=True, options={"client_id": "cid"})
    controller.settings.mail_sources = [MailSource(id="team", name="팀A 메일", connector="gmail", labels=["팀A"])]
    controller.mail_analysis_delay = 0.05
    jobs: list = []
    original = controller.enqueue

    def record(job):
        jobs.append(job)
        original(job)

    controller.enqueue = record
    controller.start()
    assert controller.wait_idle(60)
    jobs.clear()
    yield controller, gmail, jobs
    controller.stop()


def source(controller) -> MailSource:
    return controller.settings.mail_sources[0]


def documents(controller) -> list[ParsedDocument]:
    runtime = next(iter(controller.runtimes.values()))
    return [ParsedDocument.model_validate_json(r["payload"]) for r in runtime.db.list_files()
            if r["path"].startswith("@gmail.team/")]


def mail_texts(controller) -> list[str]:
    return [b.text for doc in documents(controller) for b in doc.sections[0].blocks]


def wait_for(predicate, seconds: float = 3.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_my_sent_reply_joins_the_thread_and_its_decision_is_analysed(mailbox):
    controller, gmail, jobs = mailbox
    gmail.add_thread(thread_of("T2"))
    gmail.add_thread(thread_of("T3"))
    poll = controller.mail_poller.check(source(controller))
    assert poll.ok and poll.new == 7
    threads = {t["subject"]: t for t in controller.thread_list({})["items"]}
    t2 = controller.thread_read(threads["배포 서버 비용 문의"]["id"])
    assert [m["mine"] for m in t2["messages"]] == [False, True, False, True]
    assert t2["participants"] == ["민재", "나"] and t2["count"] == 4
    assert "그럼 배포는 Render 유료 플랜으로 하자." in t2["messages"][3]["body"]
    assert t2["messages"][0]["signature"] == "민재"
    # The analysis gets my reply as its own document, attributed to me, and finds the decision.
    texts = mail_texts(controller)
    assert "보낸 사람: 이은정 (내가 보낸 메일)" in texts
    meetings = [m.text for m in build_inputs(documents(controller), CONFIG, ALIASES, {}).meetings]
    assert any("배포는 Render 유료 플랜으로 하자" in t for t in meetings)
    assert not any(t.startswith(("보낸 사람", "제목:")) for t in meetings)
    # The quoted Toss sentence of T3 is kept for display but never analysed.
    t3 = controller.thread_read(threads["결제 방식 확인"]["id"])
    assert "토스페이먼츠" in t3["messages"][1]["quoted"] and "토스페이먼츠" not in t3["messages"][1]["body"]
    assert not any("토스페이먼츠로 앱 안에서" in t for t in meetings)
    # New documents of a source with analysis on: one analysis after the quiet period.
    assert wait_for(lambda: [j.kind for j in jobs] == ["reanalyze"])


def test_without_include_sent_only_the_labelled_mails_are_read(mailbox):
    controller, gmail, _ = mailbox
    controller.settings.mail_sources = [source(controller).model_copy(update={"include_sent": False})]
    gmail.add_thread(thread_of("T2"))
    assert controller.mail_poller.check(source(controller)).new == 2
    assert gmail.calls("/threads/T2") == 0
    assert not any("내가 보낸 메일" in t for t in mail_texts(controller))


def test_history_makes_later_checks_cheap_and_picks_up_new_mail_and_my_reply(mailbox):
    controller, gmail, jobs = mailbox
    gmail.add_thread(thread_of("T2"), upto=2)
    assert controller.mail_poller.check(source(controller)).new == 2
    lists = gmail.calls("/messages")
    # Nothing new: only the history call, no listing and no body.
    assert controller.mail_poller.check(source(controller)).new == 0
    assert gmail.calls("/messages") == lists and gmail.calls("/history") == 1
    # Two mails arrive between polls, one of them my reply without the label.
    gmail.add_thread(thread_of("T2"), upto=4, start=2)
    poll = controller.mail_poller.check(source(controller))
    assert poll.new == 2 and gmail.calls("/messages") == lists + 1
    rows = controller.thread_list({})["items"]
    assert rows[0]["count"] == 4 and rows[0]["unread"] == 1
    assert "마지막 확인" in controller.mail_state()["status"]["line"]
    assert wait_for(lambda: len(jobs) >= 1)
    time.sleep(0.2)
    assert [j.kind for j in jobs] == ["reanalyze"]


def test_an_expired_history_id_lists_once_more(mailbox):
    controller, gmail, _ = mailbox
    gmail.add_thread(thread_of("T4"), upto=1)
    controller.mail_poller.check(source(controller))
    gmail.expire_history()
    gmail.add_thread(thread_of("T4"), upto=3, start=1)
    lists = gmail.calls("/messages")
    poll = controller.mail_poller.check(source(controller))
    assert poll.ok and poll.new == 2 and gmail.calls("/messages") == lists + 1
    gmail.restore_history()
    assert controller.mail_poller.check(source(controller)).new == 0


def test_no_analysis_when_analysis_is_off_but_the_index_changes(mailbox):
    controller, gmail, jobs = mailbox
    controller.settings.mail_sources = [source(controller).model_copy(update={"analysis": "off"})]
    gmail.add_thread(thread_of("T5"))
    assert controller.mail_poller.check(source(controller)).new == 3
    assert controller.thread_list({})["items"][0]["analysis"] == "점검 안 함"
    assert documents(controller) == []
    time.sleep(0.2)
    assert jobs == []
    assert not wants_toast(source(controller)) and wants_toast(source(controller).model_copy(update={"notify": "on"}))


def test_failures_back_off_honour_retry_after_and_a_revoked_login_stops(mailbox):
    controller, gmail, _ = mailbox
    poller = controller.mail_poller
    now = [1000.0]
    poller.clock = lambda: now[0]
    gmail.add_thread(thread_of("T4"), upto=1)
    poller.check(source(controller))
    gmail.http.add("GET", f"{API}/history", lambda p, b, f: {}, status=500)
    poll = poller.check(source(controller))
    assert not poll.ok and poll.failures == 1 and poll.next_due == now[0] + 120.0
    poll = poller.check(source(controller))
    assert poll.failures == 2 and poll.next_due == now[0] + 240.0
    assert "다시 시도 중" in poller.status()["line"]
    from decision_agent.connectors.http import HttpResponse

    def limited(method, url, **kwargs):
        return HttpResponse(429, b"{}", {"Retry-After": "600"})

    real = gmail.http.request
    gmail.http.request = limited
    poll = poller.check(source(controller))
    assert poll.next_due == now[0] + 600.0
    gmail.http.request = real
    gmail.http.add("GET", f"{API}/history", lambda p, b, f: {}, status=401)
    poll = poller.check(source(controller))
    assert poll.stopped and "다시 로그인" in poll.message
    assert poller.run_due() == 0
    assert poller.status()["line"] == "확인 멈춤 · 다시 로그인이 필요합니다"
    gmail.restore_history()
    poller.trigger()
    assert poller.run_due() == 1 and poller.polls["team"].ok


def test_the_poller_runs_in_the_background_and_stops_with_the_app(tmp_path):
    http = FakeHttp()
    gmail = ThreadGmail(http)
    gmail.add_thread(thread_of("T1"))
    root = dh.make_watch_dir(tmp_path)
    controller = make_app(root, http=http, secrets=logged_in())
    controller.settings.connectors["gmail"] = ConnectorSettings(enabled=True, options={"client_id": "cid"})
    controller.settings.mail_sources = [MailSource(id="team", name="팀A 메일", connector="gmail", labels=["팀A"])]
    controller.start()
    try:
        assert controller.check_mail_now()
        assert controller.mail_poller.running
        assert wait_for(lambda: controller.maildb.count() == 4, 10)
    finally:
        controller.stop()
    assert not controller.mail_poller.running


def test_threads_without_a_provider_id_group_by_references_then_subject():
    db = MailDB()
    first = MailMessage("a", "2026-10-26", "일정 문의", "prof@x.kr", "본문", header_id="<a@x>", to="me@x.kr")
    reply = MailMessage("b", "2026-10-26", "RE: 일정 문의", "me@x.kr", "답장", header_id="<b@x>",
                        in_reply_to="<a@x>", references=["<a@x>"], to="prof@x.kr")
    third = MailMessage("c", "2026-10-27", "회신: Re: 일정 문의", "prof@x.kr", "다시", header_id="<c@x>",
                        in_reply_to="<b@x>", references=["<a@x>", "<b@x>"], to="me@x.kr")
    loose1 = MailMessage("d", "2026-10-20", "회의실", "kim@x.kr", "예약", to="me@x.kr")
    loose2 = MailMessage("e", "2026-10-21", "Re: 회의실", "me@x.kr", "확인", to="kim@x.kr")
    assert len({thread_key_of(m) for m in (first, reply, third)}) == 1
    assert thread_key_of(loose1) == thread_key_of(loose2)
    assert db.upsert("imap", [first, reply, third, loose1, loose2], accounts=["ME@x.kr"]) == 5
    rows = db.threads()
    assert [(r["subject"], r["count"], r["has_sent"]) for r in rows] == [("일정 문의", 3, 1), ("회의실", 2, 1)]
    assert json.loads(rows[0]["participants"]) == ["prof@x.kr", "나"]
    # The same mail stored by two sources is shown once.
    db.upsert("other", [first])
    assert db.threads()[0]["count"] == 3


def test_unread_state_is_local_and_follows_new_arrivals():
    db = MailDB()
    assert db.upsert("s", [MailMessage("a", "2026-10-26", "공지", "x@y.kr", "본문")]) == 1
    assert db.threads()[0]["unread"] == 0
    db.upsert("s", [MailMessage("b", "2026-10-27", "새 공지", "x@y.kr", "본문")], unread=True)
    tid = next(r["tid"] for r in db.threads() if r["subject"] == "새 공지")
    assert db.thread(tid)["unread"] == 1
    seq = db.change_seq()
    assert db.mark_read(tid) and db.thread(tid)["unread"] == 0 and db.change_seq() > seq
    # A mail stored again keeps its local read state.
    db.upsert("s", [MailMessage("b", "2026-10-27", "새 공지", "x@y.kr", "본문")], unread=True)
    assert db.thread(tid)["unread"] == 0


def test_local_labels_attach_to_threads_and_sources_and_count_in_the_sidebar(mailbox):
    controller, gmail, _ = mailbox
    gmail.add_thread(thread_of("T4"))
    gmail.add_thread(thread_of("T5"))
    controller.mail_poller.check(source(controller))
    design = controller.label_create("디자인", "purple")
    later = controller.label_create("나중에", "gray")
    with pytest.raises(Exception, match="같은 이름"):
        controller.label_create("디자인", "blue")
    items = {t["subject"]: t for t in controller.thread_list({})["items"]}
    controller.thread_labels_set(items["디자인 시안 A/B 의견"]["id"], [design["id"], "unknown"])
    listing = controller.thread_list({"label": f"local:{design['id']}"})
    assert [t["subject"] for t in listing["items"]] == ["디자인 시안 A/B 의견"]
    controller.source_labels_set("team", [later["id"]])
    listing = controller.thread_list({})
    assert {g["name"]: g["count"] for g in listing["groups"]["local"]} == {"나중에": 2, "디자인": 1}
    assert listing["boxes"]["sent"]["count"] == 2 and listing["boxes"]["all"]["count"] == 2
    chips = {c["text"] for c in listing["items"][0]["chips"]}
    assert {"팀A", "결정 근거", "나중에"} <= chips
    controller.label_delete(design["id"])
    assert controller.thread_list({"label": f"local:{design['id']}"})["items"] == []


def test_split_message_keeps_quotes_and_signature_apart():
    body = thread_of("T3")["messages"][1]["body"]
    own, quoted, signature = split_message(body)
    assert "계좌이체" in own and "토스" not in own
    assert quoted.startswith("2026년 10월 26일") and "> 결제는 토스페이먼츠로" in quoted
    assert signature == "은정"


def test_thread_endpoints_need_the_token_and_mark_read(mailbox):
    controller, gmail, _ = mailbox
    gmail.add_thread(thread_of("T1"))
    controller.mail_poller.check(source(controller))
    http = TestClient(create_web_app(controller, TOKEN, 8765), base_url="http://127.0.0.1:8765")
    headers = {"X-DA-Token": TOKEN}
    assert http.get("/api/mail/threads").status_code == 403
    listing = http.get("/api/mail/threads", headers=headers).json()
    assert listing["total"] == 1 and listing["state"]["seq"] >= 1
    tid = listing["items"][0]["id"]
    thread = http.get(f"/api/mail/threads/{tid}", headers=headers).json()
    assert len(thread["messages"]) == 4 and thread["subject"] == "최종 발표 일정 확정 부탁드립니다"
    assert http.post(f"/api/mail/threads/{tid}/read", headers=headers, json={"read": True}).status_code == 200
    label = http.post("/api/mail/labels", headers=headers, json={"name": "발표", "color": "red"}).json()
    assert http.put(f"/api/mail/threads/{tid}/labels", headers=headers, json={"ids": [label["id"]]}).status_code == 200
    assert http.get("/api/mail/threads", headers=headers, params={"box": "bad"}).status_code == 400
    state = http.get("/api/mail/state", headers=headers).json()
    assert set(state) == {"seq", "status", "events"}
    assert http.get("/api/mail/threads/nothing", headers=headers).status_code == 404
