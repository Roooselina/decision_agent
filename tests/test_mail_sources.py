"""Mail sources: separate sources per connector, routing, retention, incremental fetch."""

from __future__ import annotations

import base64
import datetime
import json
import logging

import desktop_helpers as dh
import pytest
from test_connectors import FakeHttp, MemorySecrets
from test_desktop_features import make_app

from decision_agent.connectors import google as google_module
from decision_agent.connectors.base import clean_message_text, message_document
from decision_agent.connectors.http import HttpResponse
from decision_agent.connectors.mailfilter import (
    DECISION_MARKERS,
    MailFilter,
    MailQuery,
    gmail_search,
    looks_like_decision,
    sender_matches,
)
from decision_agent.desktop.engine import ProjectEngine
from decision_agent.desktop.providers import build_provider
from decision_agent.desktop.settings import (
    NARROW_SOURCE,
    AppSettings,
    ConnectorSettings,
    MailSource,
    migrate_mail_sources,
)
from decision_agent.desktop.state import StateDB
from decision_agent.models import ProjectConfig
from decision_agent.sources.builder import build_inputs

API = "https://gmail.googleapis.com/gmail/v1/users/me"
TODAY = datetime.date.today()


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


class FakeGmail:
    """A tiny Gmail server: labels, listing by label id, and full messages by id."""

    def __init__(self, http: FakeHttp, labels: list[str]) -> None:
        self.http = http
        self.labels = {name: f"L{i}" for i, name in enumerate(labels, start=1)}
        self.messages: dict[str, dict] = {}
        listing = [{"id": label_id, "name": name, "type": "user"} for name, label_id in self.labels.items()]
        http.add("GET", f"{API}/labels", lambda p, b, f: {"labels": listing})
        http.add("GET", f"{API}/profile", lambda p, b, f: {"emailAddress": "me@example.com"})
        http.add("GET", f"{API}/messages", self._list)

    def _list(self, params, body, form):
        wanted = params.get("labelIds")
        ids = [i for i, m in self.messages.items() if wanted is None or wanted in m["labels"]]
        return {"messages": [{"id": i} for i in sorted(ids, reverse=True)]}

    def add(self, msg_id: str, label: str, subject: str, body: str, sender: str = "lead@team.kr", days_ago: int = 1):
        stamp = datetime.datetime.combine(TODAY - datetime.timedelta(days=days_ago), datetime.time(12))
        message = {
            "id": msg_id, "labels": [self.labels[label]], "internalDate": str(int(stamp.timestamp() * 1000)),
            "payload": {
                "mimeType": "text/plain", "body": {"data": b64(body)},
                "headers": [{"name": "Subject", "value": subject}, {"name": "From", "value": sender}],
            },
        }
        self.messages[msg_id] = message
        self.http.add("GET", f"{API}/messages/{msg_id}", lambda p, b, f, m=message: m)

    def remove(self, msg_id: str) -> None:
        self.messages.pop(msg_id)

    def body_calls(self) -> int:
        return sum(1 for c in self.http.calls if c["params"].get("format") == "full")

    def list_calls(self) -> list[dict]:
        return [c for c in self.http.calls if c["url"].endswith("/messages")]


def logged_in() -> MemorySecrets:
    token = {"refresh_token": "R", "access_token": "TOKEN-SECRET", "expires_at": 4e9,
             "scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}
    return MemorySecrets(**{"connector.gmail.oauth": json.dumps(token)})


def source(**values) -> dict:
    data = {"name": "팀A 회의", "connector": "gmail", "labels": ["팀A"], "purpose": "decision"}
    data.update(values)
    return data


@pytest.fixture
def mail_app(tmp_path):
    http = FakeHttp()
    gmail = FakeGmail(http, ["팀A", "팀B", "할일"])
    root = dh.make_watch_dir(tmp_path)
    settings_file = tmp_path / "settings.json"
    controller = make_app(root, http=http, secrets=logged_in())
    controller.settings_file = settings_file
    controller.settings.connectors["gmail"] = ConnectorSettings(enabled=True, options={"client_id": "cid"})
    controller.start()
    assert controller.wait_idle(60)
    yield controller, gmail, root
    controller.stop()


def mail_paths(controller, root=None) -> list[str]:
    runtime = controller.runtimes[str(root.resolve())] if root else next(iter(controller.runtimes.values()))
    return sorted(r["path"] for r in runtime.db.list_files() if r["path"].startswith("@gmail."))


def run(controller, source_id: str) -> None:
    assert controller.run_mail_source(source_id)
    assert controller.wait_idle(60)


# validation


def test_validation_rejects_an_unrestricted_source_and_accepts_narrow_ones():
    with pytest.raises(ValueError, match="범위를 좁혀"):
        MailSource(id="a", name="전체", connector="gmail")
    with pytest.raises(ValueError, match="범위를 좁혀"):
        MailSource(id="a", name="받은편지함", connector="imap_mail", labels=["INBOX"])
    with pytest.raises(ValueError, match="범위를 좁혀"):
        MailSource(id="a", name="받은편지함", connector="outlook_mail", labels=["Inbox"])
    for narrow in ({"labels": ["팀A"]}, {"senders": ["@school.ac.kr"]}, {"gmail_query": "has:attachment"}):
        assert MailSource(id="a", name="좁힘", connector="gmail", **narrow).enabled
    assert MailSource(id="a", name="x", connector="imap_mail", labels=["INBOX"], subject_keywords=["회의"])
    with pytest.raises(ValueError):
        MailSource(id="a", name="x", connector="gmail", labels=["a"], unknown=1)
    with pytest.raises(ValueError):
        MailSource(id="a", name="x", connector="gmail", labels=["a"] * 31)
    with pytest.raises(ValueError):
        MailSource(id="a", name="x", connector="gmail", labels=["a" * 101])
    with pytest.raises(ValueError):
        MailSource(id="a/b", name="x", connector="gmail", labels=["a"])
    assert NARROW_SOURCE.startswith("범위를 좁혀 주세요")


def test_the_server_validates_the_form_and_needs_the_token(mail_app):
    from fastapi.testclient import TestClient

    from decision_agent.desktop.webapp import create_web_app

    controller, _, _ = mail_app
    client = TestClient(create_web_app(controller, "t" * 32, 8765), base_url="http://127.0.0.1:8765")
    headers = {"X-DA-Token": "t" * 32}
    assert client.post("/api/mail-sources", json=source()).status_code == 403
    bad = client.post("/api/mail-sources", headers=headers, json=source(labels=[]))
    assert bad.status_code == 400 and "범위를 좁혀" in bad.json()["detail"]
    assert client.post("/api/mail-sources", headers=headers, json=source(evil=1)).status_code == 400
    made = client.post("/api/mail-sources", headers=headers, json=source()).json()
    rows = client.get("/api/connectors", headers=headers).json()
    gmail_row = next(r for r in rows if r["id"] == "gmail")
    assert [s["id"] for s in gmail_row["sources"]] == [made["id"]]
    assert gmail_row["sources"][0]["purpose_label"] == "결정 근거"
    assert client.delete(f"/api/mail-sources/{made['id']}").status_code == 403
    assert client.delete(f"/api/mail-sources/{made['id']}", headers=headers).json() == {"ok": True}


# sources, purposes and routing


def test_two_sources_stay_separate_and_removing_one_removes_only_its_documents(mail_app):
    controller, gmail, _ = mail_app
    gmail.add("m01", "팀A", "A 회의", "- 로그인은 이메일 인증으로 바꾸기로 한다")
    gmail.add("m02", "팀B", "B 회의", "- 결제는 카드만 지원하기로 결정했다")
    a = controller.save_mail_source(source(name="팀A", labels=["팀A"], team="A"))
    b = controller.save_mail_source(source(name="팀B", labels=["팀B"], team="B"))
    run(controller, a["id"])
    run(controller, b["id"])
    paths = mail_paths(controller)
    assert {p.split("/")[0] for p in paths} == {f"@gmail.{a['id']}", f"@gmail.{b['id']}"}
    controller.delete_mail_source(a["id"])
    assert controller.wait_idle(60)
    assert {p.split("/")[0] for p in mail_paths(controller)} == {f"@gmail.{b['id']}"}


def test_purpose_maps_to_the_document_kind_and_a_task_mail_is_an_artifact():
    query = MailQuery(source_id="s", name="할일", purpose="task", labels=["할일"], prefilter=False)
    assert query.kind == "TASK"
    doc = message_document("요청", "2026-10-20", "a@b.c", ["- 로그인 화면을 React로 개발한다"], query.kind)
    doc.path = "@gmail.s/요청"
    built = build_inputs([doc], ProjectConfig(), {"release": {}, "module": {}, "user_group": {}, "environment": {}}, {})
    assert built.meetings == [] and [a.kind.value for a in built.artifacts] == ["TASK"]
    for purpose, kind in (("decision", "MEETING"), ("requirement", "REQUIREMENT"), ("schedule", "SCHEDULE")):
        assert MailQuery(source_id="s", name="n", purpose=purpose).kind == kind


def test_task_source_documents_carry_kind_team_and_source(mail_app):
    controller, gmail, _ = mail_app
    gmail.add("m01", "할일", "요청", "- 로그인 화면을 React로 개발한다")
    made = controller.save_mail_source(source(name="요청 메일", labels=["할일"], purpose="task", team="개발"))
    run(controller, made["id"])
    runtime = next(iter(controller.runtimes.values()))
    (row,) = [r for r in runtime.db.list_files() if r["path"].startswith("@gmail.")]
    meta = json.loads(row["payload"])["meta"]
    assert meta["kind"] == "TASK" and meta["team"] == "개발" and meta["source_name"] == "요청 메일"
    assert meta["msg_id"] == "m01" and meta["date"]


def test_sources_with_different_targets_run_against_different_runtimes(tmp_path):
    http = FakeHttp()
    gmail = FakeGmail(http, ["팀A", "팀B"])
    first = dh.make_watch_dir(tmp_path, "one")
    second = dh.make_watch_dir(tmp_path, "two")
    controller = make_app(first, http=http, secrets=logged_in(), allowed_folders=[str(first), str(second)])
    controller.settings.connectors["gmail"] = ConnectorSettings(enabled=True, options={"client_id": "cid"})
    controller.start()
    try:
        assert controller.wait_idle(60)
        gmail.add("m01", "팀A", "A", "- 발표를 연기하기로 한다")
        gmail.add("m02", "팀B", "B", "- 발표를 취소하기로 한다")
        controller.save_mail_source(source(name="A", labels=["팀A"], target=str(first)))
        controller.save_mail_source(source(name="B", labels=["팀B"], target=str(second)))
        assert controller.run_connectors("gmail")
        assert controller.wait_idle(60)
        assert [p.rsplit("[", 1)[-1] for p in mail_paths(controller, first)] == [f"{_digest('m01')}]"]
        assert [p.rsplit("[", 1)[-1] for p in mail_paths(controller, second)] == [f"{_digest('m02')}]"]
    finally:
        controller.stop()


def _digest(msg_id: str) -> str:
    import hashlib

    return hashlib.sha1(msg_id.encode()).hexdigest()[:6]


# retention and incremental fetch


def test_merge_external_keeps_old_mail_and_drops_mail_that_left_the_window(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    settings = dh.make_settings(root)
    engine = ProjectEngine(root, settings, StateDB(":memory:"), build_provider(settings))

    def doc(msg_id: str, date: str):
        made = message_document(msg_id, date, "", ["- 결정을 바꾼다"])
        made.meta["msg_id"] = msg_id
        return made

    today = datetime.date(2026, 10, 28)
    engine.merge_external("gmail.s", {"old": doc("old", "2026-06-01"), "gone": doc("gone", "2026-10-20"),
                                      "ancient": doc("ancient", "2026-01-01"), "kept": doc("kept", "2026-10-21")},
                          None, keep_days=365, today=today)
    # A fetch of the last 14 days lists only "kept" (body not refetched) and one new mail.
    changed = engine.merge_external("gmail.s", {"new": doc("new", "2026-10-27")}, "2026-10-14",
                                    keep_days=180, seen={"kept", "new"}, today=today)
    assert changed
    names = sorted(engine.external_meta("gmail.s"))
    # "gone" was inside the window but no longer listed; "ancient" is older than keep_days;
    # "old" is outside the window and within keep_days, so it stays.
    assert names == ["@gmail.s/kept", "@gmail.s/new", "@gmail.s/old"]


def test_second_fetch_without_new_mail_lists_once_per_label_and_reads_no_body(mail_app):
    controller, gmail, _ = mail_app
    gmail.add("m01", "팀A", "A", "- 로그인은 이메일 인증으로 바꾸기로 한다")
    gmail.add("m02", "팀B", "B", "안녕하세요 다음 주에 뵙겠습니다")
    made = controller.save_mail_source(source(name="둘", labels=["팀A", "팀B"]))
    run(controller, made["id"])
    assert gmail.body_calls() == 2
    status = next(r for r in controller.connector_overview() if r["id"] == "gmail")["sources"][0]["status"]
    assert status["stats"]["read"] == 1 and status["stats"]["skipped_prefilter"] == 1
    assert "결정 표현 없음 1건 건너뜀" in status["summary"]
    gmail.http.calls.clear()
    run(controller, made["id"])
    assert gmail.body_calls() == 0 and len(gmail.list_calls()) == 2
    assert len(mail_paths(controller)) == 1
    # A mail removed from the label inside the window leaves the analysis.
    gmail.remove("m01")
    run(controller, made["id"])
    assert mail_paths(controller) == []


def test_rate_limit_backs_off_then_succeeds(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(google_module, "SLEEP", waits.append)

    class Busy(FakeHttp):
        refusals = 2

        def request(self, method, url, **kwargs):
            if url.endswith("/messages") and self.refusals:
                self.refusals -= 1
                self.calls.append({"method": method, "url": url, "params": kwargs.get("params") or {}})
                return HttpResponse(429, b"{}", {"Retry-After": "3"} if self.refusals else {})
            return super().request(method, url, **kwargs)

    http = Busy()
    gmail = FakeGmail(http, ["팀A"])
    gmail.add("m01", "팀A", "A", "- 발표를 연기하기로 한다")
    from test_connectors import context

    connector = google_module.Gmail({"client_id": "cid"}, context(http))
    outcome = connector.fetch_source(MailQuery(source_id="s", name="A", labels=["팀A"]), TODAY)
    assert waits == [3.0, 2.0] and outcome.stats["read"] == 1


def test_parallel_body_fetch_respects_the_parallel_setting(mail_app):
    controller, _, _ = mail_app
    from decision_agent.desktop.mailsources import query_for

    controller.settings.max_parallel_calls = 2
    assert query_for(MailSource(id="a", name="x", connector="gmail", labels=["a"]), controller.settings).workers == 2
    controller.settings.max_parallel_calls = 16
    assert query_for(MailSource(id="a", name="x", connector="gmail", labels=["a"]), controller.settings).workers == 4


# migration


def test_migration_from_the_old_single_label_runs_once(tmp_path):
    http = FakeHttp()
    gmail = FakeGmail(http, ["회의록"])
    gmail.add("m01", "회의록", "회의", "- 발표를 연기하기로 한다")
    root = dh.make_watch_dir(tmp_path)
    settings = dh.make_settings(root)
    settings.connectors["gmail"] = ConnectorSettings(enabled=True, options={"client_id": "cid", "label": "회의록", "days_back": 14})
    db = StateDB(tmp_path / "state.db")
    db.upsert_file("@gmail/old copy", 0, 1, "h", "connector", "ok", "", "{}")
    db.close()
    for _ in range(2):
        controller = make_app(root, http=http, secrets=logged_in())
        controller.settings = settings
        controller.db_factory = lambda r: StateDB(tmp_path / "state.db")
        controller.start()
        assert controller.wait_idle(60)
        assert controller.run_connectors("gmail")
        assert controller.wait_idle(60)
        settings = controller.settings
        assert [(s.name, s.labels, s.days_back) for s in settings.mail_sources] == [("기본", ["회의록"], 14)]
        assert "label" not in settings.connectors["gmail"].options
        runtime = next(iter(controller.runtimes.values()))
        paths = [r["path"] for r in runtime.db.list_files() if r["path"].startswith("@gmail")]
        assert len(paths) == 1 and paths[0].startswith(f"@gmail.{settings.mail_sources[0].id}/")
        controller.stop()
    assert migrate_mail_sources(settings) is False


# privacy


def test_no_secret_subject_body_or_address_in_settings_or_logs(mail_app, caplog):
    controller, gmail, _ = mail_app
    gmail.add("m01", "팀A", "비밀 제목", "- 비밀 본문을 바꾸기로 한다", sender="boss@secret.kr")
    with caplog.at_level(logging.DEBUG):
        made = controller.save_mail_source(source(senders=["boss@secret.kr"]))
        run(controller, made["id"])
        controller.connector_overview()
    saved = controller.settings_file.read_text(encoding="utf-8")
    activity = json.dumps(controller.activity.recent(200), ensure_ascii=False)
    for text in ("TOKEN-SECRET", "비밀 제목", "비밀 본문"):
        assert text not in saved and text not in caplog.text
    # The activity feed names checked decisions (as for every document), but mail lines in it
    # carry only counts and the source name: never a subject.
    assert "비밀 제목" not in activity and "TOKEN-SECRET" not in activity
    # The sender filter itself is a setting the user typed, but it never reaches logs.
    assert "boss@secret.kr" not in caplog.text and "boss@secret.kr" not in activity


# local filter and the decision triage


def test_sender_patterns_and_gmail_search():
    assert sender_matches("김 <kim@school.ac.kr>", ["@school.ac.kr"])
    assert sender_matches("kim@lab.school.ac.kr", ["school.ac.kr"])
    assert not sender_matches("kim@evilschool.ac.kr", ["school.ac.kr"])
    assert sender_matches("Kim@X.com", ["kim@x.com"]) and not sender_matches("lee@x.com", ["kim@x.com"])
    rules = MailFilter(senders=["@x.com"], exclude_subject_keywords=["광고"])
    from decision_agent.connectors.base import MailMessage

    assert rules.matches(MailMessage("1", "d", "회의 결과", "a@x.com"))
    assert not rules.matches(MailMessage("1", "d", "[광고] 회의", "a@x.com"))
    assert not rules.matches(MailMessage("1", "d", "회의", "a@y.com"))
    query = MailQuery(source_id="s", name="n", senders=["a@x.com", "@y.com"], subject_keywords=["주간 회의"],
                      exclude_senders=["spam@x.com"], gmail_query="has:attachment", days_back=30)
    assert gmail_search(query) == (
        'newer_than:30d from:(a@x.com OR y.com) subject:("주간 회의") -from:(spam@x.com) (has:attachment)'
    )


# At least 30 short Korean mails with the expected triage outcome (True: send to extraction).
PREFILTER_FIXTURE = [
    ("로그인은 이메일 인증으로 하기로 했습니다.", True),
    ("발표 일정을 다음 주로 연기합니다.", True),
    ("이번 MVP에서 결제 기능은 제외합니다.", True),
    ("회의 결과 React로 확정되었습니다.", True),
    ("데이터베이스를 MySQL에서 PostgreSQL로 변경합니다.", True),
    ("내일 리허설은 취소합니다.", True),
    ("검색 기능에 필터를 추가하기로 합의했습니다.", True),
    ("배포 주기를 2주로 정했습니다.", True),
    ("포트 번호를 8080에서 9090으로 바꾸겠습니다.", True),
    ("지난번 결정은 되돌리고 원래 방식으로 갑니다.", True),
    ("API 버전: v1 -> v2", True),
    ("마감일 10/27 => 10/31", True),
    ("최종 결정 사항 공유드립니다.", True),
    ("- 로그 보관 기간 30일\n- 백업은 매일", True),
    ("1. 화면 시안 검토\n2. 일정 조정", True),
    ("* 다음 회의는 목요일", True),
    ("디자인 시안 확정본 첨부합니다.", True),
    ("회의록 정리했습니다\n- 결제는 카드만 지원", True),
    ("관리자 페이지 출시를 연기하기로 했습니다.", True),
    ("안녕하세요. 잘 지내시죠?", False),
    ("오늘 점심 같이 드실 분 계신가요?", False),
    ("자료 잘 받았습니다. 감사합니다.", False),
    ("내일 뵙겠습니다.", False),
    ("첨부 파일 확인 부탁드립니다.", False),
    ("회의실 예약 안내드립니다.", False),
    ("이번 주 세미나 참석 여부 알려 주세요.", False),
    ("서버 점검 공지입니다. 잠시 접속이 안 될 수 있습니다.", False),
    ("생일 축하드립니다!", False),
    ("오늘 회의에 늦어서 죄송합니다.", False),
    ("논문 초안 검토 부탁드립니다.", False),
    ("주말 잘 보내세요.", False),
    ("질문이 있어서 연락드립니다.", False),
]


def test_prefilter_fixture_keeps_decision_mail_and_skips_chatter():
    assert len(PREFILTER_FIXTURE) >= 30 and "결정" in DECISION_MARKERS
    wrong = [text for text, keep in PREFILTER_FIXTURE if looks_like_decision(clean_message_text(text)) != keep]
    assert wrong == []


def test_prefilter_applies_only_to_decision_sources_and_can_be_turned_off(mail_app):
    controller, gmail, _ = mail_app
    gmail.add("m01", "할일", "요청", "로그인 화면 개발 부탁드립니다")
    made = controller.save_mail_source(source(name="할일", labels=["할일"], purpose="task"))
    run(controller, made["id"])
    assert len(mail_paths(controller)) == 1
    controller.settings.mail_prefilter = False
    decision = controller.save_mail_source(source(name="결정", labels=["할일"]))
    run(controller, decision["id"])
    assert len(mail_paths(controller)) == 2
    assert AppSettings().mail_prefilter is True
