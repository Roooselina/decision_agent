from __future__ import annotations

import base64
import datetime
import email.message
import http.server
import json
import threading
import urllib.parse
from typing import ClassVar

import pytest

from decision_agent.connectors import imap as imap_module
from decision_agent.connectors.base import (
    ConnectorContext,
    ConnectorError,
    clean_message_text,
)
from decision_agent.connectors.google import Gmail, GoogleCalendar
from decision_agent.connectors.http import HttpError, HttpResponse, UrllibHttp
from decision_agent.connectors.imap import ImapMail
from decision_agent.connectors.jira import JiraConnector
from decision_agent.connectors.microsoft import OutlookCalendar, OutlookMail
from decision_agent.connectors.notion import NotionConnector
from decision_agent.connectors.oauth import OAuthApp, OAuthManager
from decision_agent.connectors.registry import CONNECTORS, describe
from decision_agent.connectors.slack import SlackConnector, clean_slack
from decision_agent.models import ProjectConfig
from decision_agent.sources.builder import build_inputs

TODAY = datetime.date(2026, 10, 22)
ALIASES = {"release": {"mvp": ["MVP"]}, "module": {}, "user_group": {}, "environment": {}}
CONFIG = ProjectConfig(current_release="mvp")


class MemorySecrets:
    def __init__(self, **values):
        self.values = dict(values)

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


class FakeHttp:
    """Maps (method, url prefix) to a function that returns a JSON-able body."""

    def __init__(self):
        self.routes = []
        self.calls = []

    def add(self, method, prefix, handler, status=200):
        self.routes.append((method, prefix, handler, status))

    def request(self, method, url, *, headers=None, params=None, form=None, json_body=None, timeout=30.0):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "params": params or {}, "form": form, "json": json_body})
        for route_method, prefix, handler, status in reversed(self.routes):
            if route_method == method and url.startswith(prefix):
                body = handler(params or {}, json_body, form)
                return HttpResponse(status, json.dumps(body).encode("utf-8"))
        return HttpResponse(404, b"{}")


class StubOAuth:
    def __init__(self):
        self.asked = []

    def access_token(self, connector_id, app):
        self.asked.append((connector_id, app))
        return "ACCESS"


def context(http, **secrets):
    return ConnectorContext(http=http, secrets=MemorySecrets(**secrets), oauth=StubOAuth())


def gmail_labels(http, *names):
    """Register the Gmail label list with user labels L1, L2, ... named as given."""
    labels = [{"id": "INBOX", "name": "INBOX", "type": "system"}]
    labels += [{"id": f"L{i}", "name": name, "type": "user"} for i, name in enumerate(names, start=1)]
    http.add("GET", "https://gmail.googleapis.com/gmail/v1/users/me/labels", lambda p, b, f: {"labels": labels})


def build(docs):
    named = []
    for name, doc in docs.items():
        doc.path = f"@x/{name}"
        named.append(doc)
    return build_inputs(named, CONFIG, ALIASES, {})


def test_every_connector_describes_itself_and_states_what_it_reads():
    assert len(CONNECTORS) == 8
    for cls in CONNECTORS.values():
        info = describe(cls)
        assert info["label"] and info["reads"] and info["guide"]
        names = [o["name"] for o in info["options"]]
        assert len(names) == len(set(names))


def test_problems_lists_missing_settings_before_any_request():
    http = FakeHttp()
    connector = JiraConnector({}, context(http))
    messages = connector.problems()
    assert any("Jira 주소" in m for m in messages) and any("API 토큰" in m for m in messages)
    assert http.calls == []
    oauth_connector = GoogleCalendar({"client_id": "id"}, context(http))
    assert any("계정 연결" in m for m in oauth_connector.problems())


def test_google_calendar_events_become_schedule_and_meeting_notes():
    http = FakeHttp()
    pages = {
        None: {
            "items": [
                {"summary": "주간 회의", "start": {"dateTime": "2026-10-25T10:00:00+09:00"},
                 "description": "<p>- 발표를 다음 주로 연기한다</p><p>- 이메일 인증은 MVP에서 제외한다</p>"},
                {"summary": "취소된 일정", "start": {"date": "2026-10-26"}, "status": "cancelled"},
            ],
            "nextPageToken": "p2",
        },
        "p2": {"items": [{"summary": "발표", "start": {"date": "2026-10-28"}, "location": "A101"}]},
    }
    http.add("GET", "https://www.googleapis.com/calendar/v3/calendars/primary/events",
             lambda params, body, form: pages[params.get("pageToken")])
    connector = GoogleCalendar({"client_id": "cid"}, context(http))
    outcome = connector.fetch(TODAY)
    assert set(outcome.docs) == {"schedule", "meeting notes"}
    assert all(c["headers"]["Authorization"] == "Bearer ACCESS" for c in http.calls)
    assert http.calls[0]["params"]["timeMin"].startswith("2026-10-08")
    built = build(outcome.docs)
    assert {m.text for m in built.meetings} >= {"발표를 다음 주로 연기한다", "이메일 인증은 MVP에서 제외한다"}
    assert {m.date for m in built.meetings} == {"2026-10-25"}
    assert {a.text for a in built.artifacts} >= {"발표", "주간 회의"}
    assert all("취소" not in a.text for a in built.artifacts)


def test_google_calendar_error_messages_are_readable():
    http = FakeHttp()
    http.add("GET", "https://www.googleapis.com/calendar", lambda p, b, f: {}, status=403)
    with pytest.raises(ConnectorError, match="권한"):
        GoogleCalendar({"client_id": "cid"}, context(http)).fetch(TODAY)


def test_gmail_reads_only_the_chosen_label_and_decodes_the_body():
    http = FakeHttp()

    def b64(text):
        return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")

    http.add("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages",
             lambda p, b, f: {"messages": [{"id": "m1"}]})
    http.add("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages/m1", lambda p, b, f: {
        "internalDate": str(int(datetime.datetime(2026, 10, 20, 12).timestamp() * 1000)),
        "payload": {
            "headers": [{"name": "Subject", "value": "주간 회의록"}, {"name": "From", "value": "a@b.c"}],
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": b64("결정: 발표는 취소한다.\n> 이전 메일 내용\n-- \n서명")}},
                {"mimeType": "text/html", "body": {"data": b64("<p>ignored</p>")}},
            ],
        },
    })
    gmail_labels(http, "회의록")
    outcome = Gmail({"client_id": "cid", "label": "회의록"}, context(http)).fetch(TODAY)
    listing = next(c for c in http.calls if c["url"].endswith("/messages"))
    assert listing["params"]["labelIds"] == "L1" and listing["params"]["q"] == "newer_than:14d"
    (doc,) = outcome.docs.values()
    texts = [b.text for b in doc.sections[0].blocks]
    assert texts[:2] == ["2026-10-20", "제목: 주간 회의록"] and "결정: 발표는 취소한다." in texts
    assert not any("이전 메일" in t or "서명" in t for t in texts)
    built = build(outcome.docs)
    assert [m.text for m in built.meetings] == ["결정: 발표는 취소한다."]


def test_gmail_without_a_label_or_source_reads_nothing():
    # The read scope comes from mail sources; the old single label is optional, and without it
    # the connector refuses to list mail instead of reading the whole mailbox.
    http = FakeHttp()
    gmail = Gmail({"client_id": "c"}, context(http))
    assert not any("라벨" in p for p in gmail.problems())
    with pytest.raises(ConnectorError, match="메일 소스"):
        gmail.fetch(TODAY)
    assert http.calls == []


def test_outlook_calendar_follows_next_link_only_on_graph():
    http = FakeHttp()
    first = {"value": [{"subject": "리허설", "start": {"dateTime": "2026-10-27T09:00:00.0000000"},
                        "body": {"contentType": "text", "content": ""}, "location": {"displayName": "B1"}}],
             "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/calendarView?page=2"}
    second = {"value": [{"subject": "발표", "start": {"dateTime": "2026-10-28T09:00:00.0000000"},
                         "body": {"contentType": "html", "content": "<p>x</p>"}}]}
    http.add("GET", "https://graph.microsoft.com/v1.0/me/calendarView", lambda p, b, f: first)
    http.add("GET", "https://graph.microsoft.com/v1.0/me/calendarView?page=2", lambda p, b, f: second)
    outcome = OutlookCalendar({"client_id": "cid"}, context(http)).fetch(TODAY)
    built = build(outcome.docs)
    assert {a.text for a in built.artifacts} == {"리허설", "발표"}
    evil = FakeHttp()
    evil.add("GET", "https://graph.microsoft.com/v1.0/me/calendarView", lambda p, b, f: {
        "value": [], "@odata.nextLink": "https://evil.example/steal"})
    OutlookCalendar({"client_id": "cid"}, context(evil)).fetch(TODAY)
    assert all("evil.example" not in c["url"] for c in evil.calls)


def test_outlook_mail_resolves_folder_by_name_and_filters_dates():
    http = FakeHttp()
    http.add("GET", "https://graph.microsoft.com/v1.0/me/mailFolders", lambda p, b, f: {
        "value": [{"id": "F1", "displayName": "회의록"}]})
    http.add("GET", "https://graph.microsoft.com/v1.0/me/mailFolders/F1/messages", lambda p, b, f: {"value": [{
        "id": "x1", "subject": "결정 사항", "receivedDateTime": "2026-10-21T01:00:00Z",
        "from": {"emailAddress": {"address": "lead@example.com"}},
        "body": {"contentType": "text", "content": "- MVP에서 이메일 인증을 제외한다\n- 발표는 연기한다"}}]})
    outcome = OutlookMail({"client_id": "cid", "folder": "회의록"}, context(http)).fetch(TODAY)
    messages_call = next(c for c in http.calls if c["url"].endswith("/messages"))
    assert messages_call["params"]["$filter"] == "receivedDateTime ge 2026-10-08T00:00:00Z"
    built = build(outcome.docs)
    assert len(built.meetings) == 2 and built.meetings[0].date == "2026-10-21"


def test_outlook_mail_unknown_folder_is_a_clear_error():
    http = FakeHttp()
    http.add("GET", "https://graph.microsoft.com/v1.0/me/mailFolders", lambda p, b, f: {"value": []})
    with pytest.raises(ConnectorError, match="찾지 못했습니다"):
        OutlookMail({"client_id": "c", "folder": "없는폴더"}, context(http)).fetch(TODAY)


class FakeImap:
    log: ClassVar[list] = []

    def __init__(self, host, port):
        FakeImap.log.append(("connect", host, port))

    def login(self, user, password):
        FakeImap.log.append(("login", user, password))

    def select(self, folder, readonly=False):
        FakeImap.log.append(("select", folder, readonly))
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        FakeImap.log.append(("search", criteria))
        return "OK", [b"1 2"]

    def fetch(self, message_id, spec):
        FakeImap.log.append(("fetch", message_id, spec))
        msg = email.message.EmailMessage()
        msg["Subject"] = f"회의록 {message_id.decode()}"
        msg["Date"] = "Tue, 20 Oct 2026 10:00:00 +0900"
        msg["Message-ID"] = f"<{message_id.decode()}@x>"
        msg.set_content("- 로그인 화면은 React로 유지한다\n> quoted")
        return "OK", [(b"1 (RFC822 {10}", msg.as_bytes()), b")"]

    def logout(self):
        FakeImap.log.append(("logout",))


def test_imap_opens_folder_read_only_and_peeks(monkeypatch):
    FakeImap.log = []
    monkeypatch.setattr(imap_module, "IMAP_FACTORY", FakeImap)
    options = {"host": "imap.example.com", "username": "me", "folder": "회의록"}
    connector = ImapMail(options, context(FakeHttp(), **{"connector.imap_mail.password": "pw"}))
    outcome = connector.fetch(TODAY)
    assert len(outcome.docs) == 2
    assert ("select", '"회의록"', True) in FakeImap.log
    assert all("BODY.PEEK" in entry[2] for entry in FakeImap.log if entry[0] == "fetch")
    assert ("search", ("SINCE", "08-Oct-2026")) in FakeImap.log
    assert FakeImap.log[-1] == ("logout",)
    built = build(outcome.docs)
    assert {m.text for m in built.meetings} == {"로그인 화면은 React로 유지한다"}


def test_imap_login_failure_is_readable(monkeypatch):
    class Refusing(FakeImap):
        def login(self, user, password):
            raise imap_module.imaplib.IMAP4.error("bad")

    monkeypatch.setattr(imap_module, "IMAP_FACTORY", Refusing)
    connector = ImapMail({"host": "h", "username": "u", "folder": "f"},
                         context(FakeHttp(), **{"connector.imap_mail.password": "pw"}))
    with pytest.raises(ConnectorError, match="로그인"):
        connector.fetch(TODAY)


def test_notion_pages_and_databases():
    http = FakeHttp()
    http.add("POST", "https://api.notion.com/v1/search", lambda p, b, f: {"results": [
        {"object": "page", "id": "p1", "last_edited_time": "2026-10-20T01:00:00.000Z",
         "properties": {"Name": {"type": "title", "title": [{"plain_text": "10월 20일 회의록"}]}}},
        {"object": "database", "id": "d1", "title": [{"plain_text": "작업 보드"}],
         "last_edited_time": "2026-10-21T01:00:00.000Z"},
        {"object": "page", "id": "old", "last_edited_time": "2020-01-01T00:00:00.000Z",
         "properties": {"Name": {"type": "title", "title": [{"plain_text": "오래된 페이지"}]}}},
    ]})
    http.add("GET", "https://api.notion.com/v1/blocks/p1/children", lambda p, b, f: {"results": [
        {"id": "b1", "type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "결정 사항"}]}},
        {"id": "b2", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "발표를 취소한다"}]}},
    ], "has_more": False})
    http.add("POST", "https://api.notion.com/v1/databases/d1/query", lambda p, b, f: {"results": [
        {"properties": {"이름": {"type": "title", "title": [{"plain_text": "MVP 이메일 인증 개발"}]},
                        "상태": {"type": "status", "status": {"name": "진행 중"}},
                        "담당": {"type": "people", "people": [{"name": "철수"}]}}}]})
    outcome = NotionConnector({}, context(http, **{"connector.notion.token": "secret_x"})).fetch(TODAY)
    assert len(outcome.docs) == 2 and not any("오래된" in n for n in outcome.docs)
    assert http.calls[0]["headers"]["Notion-Version"] == "2022-06-28"
    built = build(outcome.docs)
    assert [m.text for m in built.meetings] == ["발표를 취소한다"]
    task = next(a for a in built.artifacts if "MVP 이메일 인증 개발" in a.text)
    assert task.kind.value == "TASK" and task.metadata.get("assignee") == "철수"


def test_jira_search_jql_with_token_pagination():
    http = FakeHttp()
    pages = {None: {"issues": [{"key": "APP-1", "fields": {"summary": "MVP 이메일 인증 개발", "status": {"name": "In Progress"},
                                                          "assignee": {"displayName": "Kim"}, "duedate": "2026-11-01",
                                                          "fixVersions": [{"name": "mvp"}]}}],
                    "nextPageToken": "t2"},
             "t2": {"issues": [{"key": "APP-2", "fields": {"summary": "배포 자동화", "status": {"name": "To Do"}}}], "isLast": True}}
    http.add("GET", "https://team.atlassian.net/rest/api/3/search/jql",
             lambda p, b, f: pages[p.get("nextPageToken")])
    connector = JiraConnector({"base_url": "https://team.atlassian.net/", "email": "me@x.com", "jql": "project = APP"},
                              context(http, **{"connector.jira.api_token": "tok"}))
    outcome = connector.fetch(TODAY)
    auth = http.calls[0]["headers"]["Authorization"]
    assert base64.b64decode(auth.split()[1]).decode() == "me@x.com:tok"
    assert "fields" in http.calls[0]["params"]
    built = build(outcome.docs)
    ids = {a.id.split(":")[-1] for a in built.artifacts}
    assert {"APP-1", "APP-2"} <= ids and all(a.kind.value == "TASK" for a in built.artifacts)


def test_jira_rejects_plain_http_and_bad_jql():
    secrets = {"connector.jira.api_token": "tok"}
    bad = JiraConnector({"base_url": "http://team.example.com", "email": "a", "jql": "x"}, context(FakeHttp(), **secrets))
    with pytest.raises(ConnectorError, match="https"):
        bad.fetch(TODAY)
    http = FakeHttp()
    http.add("GET", "https://t.atlassian.net/rest/api/3/search/jql", lambda p, b, f: {}, status=400)
    broken = JiraConnector({"base_url": "https://t.atlassian.net", "email": "a", "jql": "???"}, context(http, **secrets))
    with pytest.raises(ConnectorError, match="JQL"):
        broken.fetch(TODAY)


def test_slack_groups_messages_by_day_and_skips_system_messages():
    http = FakeHttp()
    stamp = datetime.datetime(2026, 10, 21, 15, 0).timestamp()
    http.add("GET", "https://slack.com/api/conversations.history", lambda p, b, f: {"ok": True, "messages": [
        {"ts": str(stamp), "text": "발표는 다음 주로 연기합니다 <@U1> <https://x.com|링크>"},
        {"ts": str(stamp + 5), "subtype": "channel_join", "text": "joined"},
    ]})
    connector = SlackConnector({"channels": ["C123"]}, context(http, **{"connector.slack.bot_token": "xoxb-1"}))
    outcome = connector.fetch(TODAY)
    assert http.calls[0]["params"]["channel"] == "C123"
    built = build(outcome.docs)
    assert [m.text for m in built.meetings] == ["발표는 다음 주로 연기합니다 @user 링크"]
    assert built.meetings[0].date == "2026-10-21"


def test_slack_errors_are_explained():
    http = FakeHttp()
    http.add("GET", "https://slack.com/api/conversations.history", lambda p, b, f: {"ok": False, "error": "not_in_channel"})
    connector = SlackConnector({"channels": ["C1"]}, context(http, **{"connector.slack.bot_token": "x"}))
    with pytest.raises(ConnectorError, match="초대"):
        connector.fetch(TODAY)
    assert SlackConnector({}, context(FakeHttp())).problems()


def test_text_cleaning_helpers():
    assert clean_slack("hi <#C1|general> &amp; <@U2|kim>") == "hi #general & @kim"
    lines = clean_message_text("결정합니다.\n> quoted\nOn Mon, X wrote:\n이전 내용")
    assert lines == ["결정합니다."]


# OAuth


def oauth_app():
    return OAuthApp(auth_url="https://auth.example/authorize", token_url="https://auth.example/token",
                    scopes=("read",), client_id="cid", client_secret="sec")


def test_oauth_pkce_state_is_single_use_and_tokens_go_to_the_secret_store():
    http = FakeHttp()
    http.add("POST", "https://auth.example/token", lambda p, b, f: {
        "access_token": "A1", "refresh_token": "R1", "expires_in": 3600})
    secrets = MemorySecrets()
    now = [1000.0]
    manager = OAuthManager(http, secrets, 8765, clock=lambda: now[0])
    url = manager.begin("google_calendar", oauth_app())
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert query["code_challenge_method"] == ["S256"] and query["redirect_uri"] == ["http://127.0.0.1:8765/oauth/callback"]
    state = query["state"][0]
    assert manager.complete(state, "CODE") == "google_calendar"
    form = http.calls[0]["form"]
    assert form["code"] == "CODE" and form["client_secret"] == "sec" and len(form["code_verifier"]) >= 43
    stored = json.loads(secrets.get("connector.google_calendar.oauth"))
    assert stored["refresh_token"] == "R1"
    with pytest.raises(ConnectorError):
        manager.complete(state, "CODE")
    with pytest.raises(ConnectorError):
        manager.complete("forged", "CODE")


def test_oauth_state_expires_and_access_token_refreshes():
    http = FakeHttp()
    replies = iter([
        {"access_token": "A1", "refresh_token": "R1", "expires_in": 100},
        {"access_token": "A2", "expires_in": 3600},
    ])
    http.add("POST", "https://auth.example/token", lambda p, b, f: next(replies))
    now = [0.0]
    manager = OAuthManager(http, MemorySecrets(), 8765, clock=lambda: now[0])
    stale = urllib.parse.parse_qs(urllib.parse.urlsplit(manager.begin("c", oauth_app())).query)["state"][0]
    now[0] = 5000.0
    with pytest.raises(ConnectorError):
        manager.complete(stale, "CODE")
    now[0] = 6000.0
    fresh = urllib.parse.parse_qs(urllib.parse.urlsplit(manager.begin("c", oauth_app())).query)["state"][0]
    manager.complete(fresh, "CODE")
    assert manager.access_token("c", oauth_app()) == "A1"
    now[0] += 200
    assert manager.access_token("c", oauth_app()) == "A2"
    assert http.calls[-1]["form"]["grant_type"] == "refresh_token"
    assert json.loads(manager.secrets.get("connector.c.oauth"))["refresh_token"] == "R1"
    manager.disconnect("c")
    with pytest.raises(ConnectorError):
        manager.access_token("c", oauth_app())


def test_oauth_rejected_login_and_missing_client_id():
    http = FakeHttp()
    http.add("POST", "https://auth.example/token", lambda p, b, f: {"error": "invalid_grant", "error_description": "bad code"}, status=400)
    manager = OAuthManager(http, MemorySecrets(), 8765)
    state = urllib.parse.parse_qs(urllib.parse.urlsplit(manager.begin("c", oauth_app())).query)["state"][0]
    with pytest.raises(ConnectorError, match="bad code"):
        manager.complete(state, "x")
    with pytest.raises(ConnectorError, match="클라이언트 ID"):
        manager.begin("c", OAuthApp("a", "t", ("s",), ""))


# HTTP client safety


class _Handler(http.server.BaseHTTPRequestHandler):
    seen: ClassVar[list] = []

    def do_GET(self):
        _Handler.seen.append(self.headers.get("Authorization"))
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args):
        pass


def test_http_client_is_https_only_and_never_follows_redirects():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = UrllibHttp()
        base = f"http://127.0.0.1:{server.server_port}"
        assert client.request("GET", base + "/x", headers={"Authorization": "Bearer T"}).json() == {"ok": True}
        redirected = client.request("GET", base + "/redirect", headers={"Authorization": "Bearer T"})
        assert redirected.status == 302 and not redirected.ok
        with pytest.raises(HttpError, match="https"):
            client.request("GET", "http://example.com/")
        with pytest.raises(HttpError, match="https"):
            client.request("GET", "ftp://example.com/")
    finally:
        server.shutdown()
        server.server_close()


def test_gmail_follows_next_page_tokens_up_to_the_limit():
    http = FakeHttp()
    pages = {
        None: {"messages": [{"id": f"a{i}"} for i in range(3)], "nextPageToken": "p2"},
        "p2": {"messages": [{"id": f"b{i}"} for i in range(3)], "nextPageToken": "p3"},
        "p3": {"messages": [{"id": "c0"}]},
    }
    http.add("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages",
             lambda p, b, f: pages[p.get("pageToken")])

    def one(p, b, f):
        return {"internalDate": "1760000000000", "payload": {"headers": [{"name": "Subject", "value": "s"}],
                "mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode("결정: 발표는 취소한다.".encode()).decode()}}}

    http.add("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages/", one)
    gmail_labels(http, "x")
    outcome = Gmail({"client_id": "cid", "label": "x", "max_messages": 5}, context(http)).fetch(TODAY)
    listing_calls = [c for c in http.calls if c["url"].endswith("/messages")]
    assert len(listing_calls) == 2
    assert listing_calls[0]["params"]["maxResults"] == 5 and listing_calls[1]["params"]["pageToken"] == "p2"
    assert len(outcome.docs) == 5


def test_outlook_mail_follows_next_links_and_refuses_other_hosts():
    http = FakeHttp()
    http.add("GET", "https://graph.microsoft.com/v1.0/me/mailFolders", lambda p, b, f: {
        "value": [{"id": "F1", "displayName": "회의록"}]})

    def item(n):
        return {"id": f"x{n}", "subject": f"결정 {n}", "receivedDateTime": "2026-10-21T01:00:00Z",
                "from": {"emailAddress": {"address": "a@b.c"}},
                "body": {"contentType": "text", "content": f"- 결정 항목 {n}을 확정한다"}}

    http.add("GET", "https://graph.microsoft.com/v1.0/me/mailFolders/F1/messages", lambda p, b, f: {
        "value": [item(1), item(2)], "@odata.nextLink": "https://graph.microsoft.com/v1.0/next?page=2"})
    http.add("GET", "https://graph.microsoft.com/v1.0/next", lambda p, b, f: {"value": [item(3)]})
    outcome = OutlookMail({"client_id": "cid", "folder": "회의록", "max_messages": 3}, context(http)).fetch(TODAY)
    assert len(outcome.docs) == 3

    evil = FakeHttp()
    evil.add("GET", "https://graph.microsoft.com/v1.0/me/mailFolders", lambda p, b, f: {
        "value": [{"id": "F1", "displayName": "회의록"}]})
    evil.add("GET", "https://graph.microsoft.com/v1.0/me/mailFolders/F1/messages", lambda p, b, f: {
        "value": [item(1)], "@odata.nextLink": "https://evil.example.com/steal"})
    with pytest.raises(ConnectorError):
        OutlookMail({"client_id": "cid", "folder": "회의록", "max_messages": 50}, context(evil)).fetch(TODAY)
    assert not any("evil.example.com" in c["url"] for c in evil.calls)
